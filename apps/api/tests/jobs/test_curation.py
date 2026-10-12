import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.support.curation import (
    Checkpoints,
    FakeEmbeddings,
    Script,
    install_fake_embeddings,
    install_script,
    make_ctx,
    seed_profile,
    seed_source,
)

from app.core.config import settings
from app.db.schema import CurationRun, StudySession, Topic, TopicChunk, TopicRevision
from app.jobs.errors import PermanentJobError, ProviderLimitReached
from app.jobs.registry import HANDLERS
from brainbench_agents.curation import ExtractedTopic, TopicExtraction
from brainbench_agents.testing import Structured
from brainbench_providers.select import ALL_ACCOUNTS
from brainbench_providers.strands_models import ProviderThrottledException


@pytest.fixture(autouse=True)
def fake_embeddings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeEmbeddings:
    monkeypatch.setattr(settings, "brainbench_data", tmp_path)
    return install_fake_embeddings(monkeypatch)


async def new_run(session: AsyncSession, threshold: float = 0.4) -> CurationRun:
    run = CurationRun(profile_version=0, source_ids=[], threshold=threshold)
    session.add(run)
    await session.commit()
    return run


async def topic_titles(session: AsyncSession) -> dict[uuid.UUID, str]:
    rows = await session.execute(
        select(TopicRevision.topic_id, TopicRevision.title).order_by(TopicRevision.revision)
    )
    return dict(rows.all())


def throttle() -> ProviderThrottledException:
    return ProviderThrottledException(
        "limit", reset_at=datetime.now(UTC) + timedelta(hours=2), scope=ALL_ACCOUNTS
    )


async def test_topics_cite_their_chunks_and_duplicates_merge_across_sources(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, fake_embeddings: FakeEmbeddings
) -> None:
    await seed_profile(session)
    _, paper = await seed_source(
        session, "Raft paper", ["Raft: Leader election.", "Logs: Replication."]
    )
    _, notes = await seed_source(session, "Notes", ["Raft consensus: Leaders and terms."])
    fake_embeddings.alias("Raft consensus", "Raft")
    script = Script(merge_titles={"Raft": "Raft consensus algorithm"})
    install_script(monkeypatch, script)
    run = await new_run(session)

    await HANDLERS["curation"](make_ctx(session, run.id))

    titles = await topic_titles(session)
    assert sorted(titles.values()) == ["Logs", "Raft consensus algorithm"]
    raft = next(t for t, title in titles.items() if title.startswith("Raft"))
    cited = set(
        await session.scalars(select(TopicChunk.chunk_id).where(TopicChunk.topic_id == raft))
    )
    assert cited == {paper[0].id, notes[0].id}
    for topic_id in titles:
        assert await session.scalar(
            select(func.count()).select_from(TopicChunk).where(TopicChunk.topic_id == topic_id)
        )
    assert script.steps[:3] == ["topic_extraction", "topic_extraction", "topic_merge"]
    await session.refresh(run)
    assert run.models["topic_extraction"] == {
        "provider": "scripted",
        "model": "scripted-topic_extraction",
    }
    assert any(entry["message"] == "usage" for entry in run.step_log)


async def test_extraction_drops_topics_without_valid_citations(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_profile(session)
    await seed_source(session, "Raft", ["Raft: a"])
    answer = TopicExtraction(
        topics=[
            ExtractedTopic(title="Raft", summary="Leaders.", chunk_keys=["C1", "C9"]),
            ExtractedTopic(title="Invented", summary="x", chunk_keys=["C7"]),
            ExtractedTopic(title="  ", summary="x", chunk_keys=["C1"]),
        ]
    )
    script = Script()
    model = install_script(monkeypatch, script)
    model.replies = lambda call: Structured(answer) if len(model.calls) == 1 else script(call)
    run = await new_run(session)

    await HANDLERS["curation"](make_ctx(session, run.id))

    assert list((await topic_titles(session)).values()) == ["Raft"]


async def test_deferral_resumes_after_the_last_completed_window(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "curation_window_chars", 10)  # one chunk per window
    await seed_profile(session)
    await seed_source(session, "Raft", ["Raft: a.", "Logs: b.", "Terms: c."])
    script = Script(failures=[])
    model = install_script(monkeypatch, script)
    run_id = (await new_run(session)).id
    recorder = Checkpoints()

    # The first window answers, the second hits the provider limit.
    model.replies = lambda call: throttle() if len(model.calls) == 2 else script(call)
    with pytest.raises(ProviderLimitReached) as caught:
        await HANDLERS["curation"](make_ctx(session, run_id, recorder=recorder))
    assert caught.value.reset_at is not None
    checkpoint = recorder.saved[-1]
    assert len(checkpoint["windows"]) == 1
    await session.rollback()

    # The resumed attempt starts from the checkpoint and asks only for the remaining windows.
    model.calls.clear()
    model.replies = script
    await HANDLERS["curation"](make_ctx(session, run_id, checkpoint=checkpoint))

    assert sorted((await topic_titles(session)).values()) == ["Logs", "Raft", "Terms"]
    # Windows 2 and 3, one scoring batch, prerequisites; no merge call without duplicates.
    assert script.steps[1:] == ["topic_extraction", "topic_extraction", "scoring", "prerequisites"]
    assert len(model.calls) == 4


async def test_reconciled_topics_are_committed_before_later_steps(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_profile(session)
    await seed_source(session, "Raft", ["Raft: a."])
    install_script(monkeypatch, Script())
    run = await new_run(session)
    recorder = Checkpoints()

    await HANDLERS["curation"](make_ctx(session, run.id, recorder=recorder))
    await session.rollback()  # what the worker does on a later failure

    assert await session.scalar(select(func.count()).select_from(Topic)) == 1
    assert recorder.saved[-1]["topics"][0]["title"] == "Raft"


async def test_rerun_reuses_topic_ids(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_profile(session)
    await seed_source(session, "Raft", ["Raft: a.", "Logs: b."])
    install_script(monkeypatch, Script())
    await HANDLERS["curation"](make_ctx(session, (await new_run(session)).id))
    first = await topic_titles(session)

    await seed_source(session, "More", ["Snapshots: c."])
    await HANDLERS["curation"](make_ctx(session, (await new_run(session)).id))

    second = await topic_titles(session)
    assert {k: v for k, v in second.items() if k in first} == first
    assert sorted(second.values()) == ["Logs", "Raft", "Snapshots"]
    assert await session.scalar(select(func.count()).select_from(TopicRevision)) == 3


async def test_bad_payload_and_missing_preconditions_are_permanent(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_script(monkeypatch, Script())
    ctx = make_ctx(session, uuid.uuid4())
    ctx.payload = {}
    with pytest.raises(PermanentJobError):
        await HANDLERS["curation"](ctx)
    with pytest.raises(PermanentJobError):
        await HANDLERS["curation"](make_ctx(session, uuid.uuid4()))

    run = await new_run(session)
    with pytest.raises(PermanentJobError) as caught:
        await HANDLERS["curation"](make_ctx(session, run.id))
    assert caught.value.reason == "preconditions"


async def test_scoring_sees_the_profile_and_session_history_in_batches(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "curation_scoring_batch", 2)
    await seed_profile(session, skills=[("Python", "expert")])
    await seed_source(session, "Raft", ["Raft: a.", "Logs: b.", "Python lists: c."])
    script = Script(relevance={"Logs": 0.3}, held={"Python lists"})
    model = install_script(monkeypatch, script)
    await HANDLERS["curation"](make_ctx(session, (await new_run(session)).id))
    titles = {title: topic_id for topic_id, title in (await topic_titles(session)).items()}
    session.add(StudySession(topic_id=titles["Raft"], minutes=45, rating="could-teach"))
    await session.commit()
    model.calls.clear()
    recorder = Checkpoints()

    await HANDLERS["curation"](make_ctx(session, (await new_run(session)).id, recorder=recorder))

    scoring_calls = [c for c in model.calls if "TopicScoring" in c.tool_names]
    assert len(scoring_calls) == 2  # three topics, batches of two
    first = scoring_calls[0].prompt
    assert '"name": "Python"' in first and '"level": "expert"' in first
    assert '"rating": "could-teach"' in first and '"mastered": true' in first
    assert {"get_profile", "get_sessions", "search_chunks"} <= set(scoring_calls[0].tool_names)
    scores = recorder.saved[-1]["scores"]
    assert scores[str(titles["Logs"])]["relevance"] == 0.3
    held = scores[str(titles["Python lists"])]
    assert held["relevance"] < 0.2 and held["already_held"]
    messages = [entry["message"] for entry in recorder.saved[-1]["log"]]
    assert "kept 1 of 3 topics; excluded 2 below threshold 0.4" in messages


async def test_prerequisite_cycle_is_broken_at_the_weakest_edge_and_logged(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_profile(session)
    await seed_source(session, "Raft", ["Terms: a.", "Logs: b.", "Snapshots: c.", "CSS: d."])
    script = Script(
        relevance={"CSS": 0.1},
        edges=[
            ("Logs", "Terms", 0.9),
            ("Snapshots", "Logs", 0.8),
            ("Terms", "Snapshots", 0.3),
            ("Logs", "Logs", 1.0),
        ],
    )
    model = install_script(monkeypatch, script)
    run = await new_run(session)
    recorder = Checkpoints()

    await HANDLERS["curation"](make_ctx(session, run.id, recorder=recorder))

    ids = {title: str(topic_id) for topic_id, title in (await topic_titles(session)).items()}
    edges = {(e["topic"], e["requires"]) for e in recorder.saved[-1]["edges"]}
    assert edges == {(ids["Logs"], ids["Terms"]), (ids["Snapshots"], ids["Logs"])}
    [prereq_call] = [c for c in model.calls if "Prerequisites" in c.tool_names]
    assert "CSS" not in prereq_call.prompt  # excluded topics are not ordered
    await session.refresh(run)
    [entry] = [e for e in run.step_log if e["message"].startswith("cycle broken")]
    assert entry["message"] == "cycle broken: dropped 'Terms requires Snapshots' (confidence 0.3)"
    assert entry["dropped"] == {
        "topic": ids["Terms"],
        "requires": ids["Snapshots"],
        "confidence": 0.3,
    }
    assert sorted(entry["cycle"]) == ["Logs", "Snapshots", "Terms"]
