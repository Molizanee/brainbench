import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.support.curation import FakeEmbeddings, seed_profile, seed_source

from app.db.schema import CurationRun, Job, TopicChunk, TopicRevision
from app.services import curation as curation_service
from app.services import providers
from app.services.curation import (
    Candidate,
    ReadySourcesRequired,
    TargetsRequired,
    check_preconditions,
    cluster_candidates,
    effective_task_class,
    ensure_curation_providers,
    reconcile,
    source_windows,
    start_run,
)
from app.services.instance_settings import load_instance_settings
from app.services.providers import ProviderRuntime
from app.services.topics import create_topic, current_revision, merge_topics
from brainbench_providers.testing import FakeProviders


def _vectors(fake: FakeEmbeddings, candidates: list[Candidate]) -> list[list[float]]:
    return fake([c.embedding_text for c in candidates])


async def test_preconditions_need_a_target_and_a_ready_source(session: AsyncSession) -> None:
    with pytest.raises(TargetsRequired):
        await check_preconditions(session)
    await seed_profile(session, targets=[])
    with pytest.raises(TargetsRequired) as no_target:
        await check_preconditions(session)
    assert "target skill" in no_target.value.message
    await session.rollback()


async def test_preconditions_need_ready_material(session: AsyncSession) -> None:
    await seed_profile(session)
    await seed_source(session, "Draft", ["A: a"], status="embedding")
    with pytest.raises(ReadySourcesRequired) as caught:
        await check_preconditions(session)
    assert caught.value.code == "ready-sources-required"

    source, _ = await seed_source(session, "Raft", ["A: a"])
    inputs = await check_preconditions(session)
    assert inputs.source_ids == [source.id] and inputs.profile_version == 1


async def test_start_run_queues_one_job_and_dedupes(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    checked: list[str] = []

    async def ensure(session: AsyncSession, task_class: str) -> None:
        checked.append(task_class)

    monkeypatch.setattr(providers, "ensure_provider_for", ensure)
    await seed_profile(session)
    source, _ = await seed_source(session, "Raft", ["A: a"])

    run, job = await start_run(session, threshold=0.5)
    await session.commit()
    again, same_job = await start_run(session)
    await session.commit()

    assert checked[:4] == ["topic_extraction", "topic_merge", "scoring", "prerequisites"]
    assert job.type == "curation" and job.dedupe_key == "curation"
    assert job.payload == {"run_id": str(run.id)}
    assert (run.threshold, run.source_ids, run.profile_version) == (0.5, [str(source.id)], 1)
    assert again.id == run.id and same_job.id == job.id
    assert await session.scalar(select(func.count()).select_from(CurationRun)) == 1
    assert await session.scalar(select(func.count()).select_from(Job)) == 1


async def test_steps_fall_back_to_the_curation_route(session: AsyncSession) -> None:
    row = await load_instance_settings(session)
    assert await effective_task_class(session, "scoring") == "scoring"
    row.provider_routing = {"curation": {"provider": "anthropic", "model": "m"}}
    assert await effective_task_class(session, "scoring") == "curation"
    row.provider_routing = {
        "curation": {"provider": "anthropic", "model": "m"},
        "scoring": {"provider": "openai-codex", "model": "x"},
    }
    assert await effective_task_class(session, "scoring") == "scoring"
    assert await effective_task_class(session, "prerequisites") == "curation"


@pytest.fixture
def runtime(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ProviderRuntime, FakeProviders]:
    fake = FakeProviders()
    runtime = ProviderRuntime(
        providers.get_registry(), session_factory, tmp_path / "vault.key", http=fake.http_factory()
    )
    monkeypatch.setattr(providers, "get_provider_runtime", lambda: runtime)
    return runtime, fake


async def test_missing_provider_rejects_the_run(
    session: AsyncSession, runtime: tuple[ProviderRuntime, FakeProviders]
) -> None:
    with pytest.raises(providers.NoProvider) as caught:
        await ensure_curation_providers(session)
    assert "topic_extraction" in caught.value.message


async def test_routed_model_records_usage_under_the_step(
    session: AsyncSession, runtime: tuple[ProviderRuntime, FakeProviders]
) -> None:
    provider_runtime, fake = runtime
    view = await provider_runtime.start_login("anthropic", "oauth-code")
    await provider_runtime.complete_login(view.login_id, fake.authorize(view.authorize_url or ""))
    row = await load_instance_settings(session)
    row.provider_routing = {"curation": {"provider": "anthropic", "model": "claude-opus-5-5"}}

    await ensure_curation_providers(session)
    routed = await curation_service.routed_model(session, "scoring")

    assert (routed.provider, routed.model_id) == ("anthropic", "claude-opus-5-5")
    assert routed.model.get_config()["task_class"] == "scoring"


async def test_windows_split_by_size_and_restart_keys(session: AsyncSession) -> None:
    source, chunks = await seed_source(session, "Raft", ["a" * 40, "b" * 40, "c" * 90, "d" * 10])

    windows = await source_windows(session, source.id, window_chars=100)

    assert [[c.chunk_id for c in w.chunks] for w in windows] == [
        [chunks[0].id, chunks[1].id],
        [chunks[2].id, chunks[3].id],
    ]
    assert [c.key for c in windows[1].chunks] == ["C1", "C2"]
    assert windows[1].id == f"{source.id}:1"
    assert await source_windows(session, uuid.uuid4(), window_chars=100) == []


def test_cluster_by_title_or_embedding() -> None:
    fake = FakeEmbeddings()
    fake.alias("Raft consensus", "Raft")
    candidates = [
        Candidate("Raft", "x", ["1"]),
        Candidate("Paxos", "y", ["2"]),
        Candidate("Raft consensus", "z", ["3"]),
        Candidate("PAXOS!", "w", ["4"]),
        Candidate("Gossip", "v", ["5"]),
    ]

    groups = cluster_candidates(candidates, _vectors(fake, candidates), similarity=0.85)

    assert groups == [[0, 2], [1, 3], [4]]


async def test_reconcile_reuses_ids_and_revises_only_on_change(session: AsyncSession) -> None:
    fake = FakeEmbeddings()
    _, chunks = await seed_source(session, "Raft", ["Raft: a", "Paxos: b", "Raft log: c"])
    run_id = uuid.uuid4()
    session.add(CurationRun(id=run_id, profile_version=1))
    first = [
        Candidate("Raft", "Leader election.", [str(chunks[0].id)]),
        Candidate("Paxos", "Single decree.", [str(chunks[1].id)]),
    ]
    created = await reconcile(session, run_id, first, _vectors(fake, first), similarity=0.85)
    assert [t.created for t in created] == [True, True]

    fake.alias("Raft log replication", "Raft")  # same concept, new title: embedding match
    second = [
        Candidate("raft", "Leader election.", [str(chunks[2].id)]),  # title match, same text
        Candidate("Paxos", "Multi-Paxos too.", [str(chunks[1].id)]),  # summary changed
        Candidate("Raft log replication", "Logs.", [str(chunks[0].id)]),  # folds into Raft
        Candidate("Gossip", "Epidemic broadcast.", [str(chunks[2].id)]),
    ]
    again = await reconcile(session, run_id, second, _vectors(fake, second), similarity=0.85)

    raft, paxos, gossip = again
    assert raft.topic_id == created[0].topic_id and not raft.revised
    assert raft.title == "Raft"  # "raft" normalizes equal, so no new revision
    assert paxos.topic_id == created[1].topic_id and paxos.revised
    assert (await current_revision(session, paxos.topic_id)).summary == "Multi-Paxos too."
    assert gossip.created
    revisions = await session.scalar(select(func.count()).select_from(TopicRevision))
    assert revisions == 4
    raft_chunks = set(
        await session.scalars(
            select(TopicChunk.chunk_id).where(TopicChunk.topic_id == raft.topic_id)
        )
    )
    assert raft_chunks == {chunks[0].id, chunks[2].id}


async def test_reconcile_matches_a_merged_topic_to_its_survivor(session: AsyncSession) -> None:
    fake = FakeEmbeddings()
    survivor, _ = await create_topic(session, title="Raft", summary="s", created_by="run")
    loser, _ = await create_topic(session, title="Raft consensus", summary="s", created_by="run")
    await merge_topics(session, loser.id, survivor.id)
    candidates = [Candidate("Raft consensus", "s", [])]

    [topic] = await reconcile(
        session, uuid.uuid4(), candidates, _vectors(fake, candidates), similarity=0.85
    )

    assert topic.topic_id == survivor.id and not topic.created
