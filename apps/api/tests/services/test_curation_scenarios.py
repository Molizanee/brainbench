"""Curriculum-curation spec scenarios that span runs, plans, and edits (task 6.8)."""

import random
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.support.curation import (
    Script,
    curate,
    install_fake_embeddings,
    install_script,
    seed_profile,
    seed_source,
)

from app.core.config import settings
from app.db.schema import Asset, PlanItem, StudyPlan, TopicRevision, TutorThread
from app.models.plans import MergeEdit, TopicEdit
from app.models.progress import StudySessionCreate
from app.services.planner import Edge, PlanTopic, break_cycles, check_order, order_topics
from app.services.plans import PlanService, stale_reasons
from app.services.progress import session_history
from app.services.sessions import SessionService
from app.services.topics import resolve_topic, topic_history


@pytest.fixture(autouse=True)
def fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Script:
    monkeypatch.setattr(settings, "brainbench_data", tmp_path)
    install_fake_embeddings(monkeypatch)
    script = Script()
    install_script(monkeypatch, script)
    return script


async def plan_titles(session: AsyncSession, version: int) -> dict[str, uuid.UUID]:
    plan = await PlanService(session).get(version)
    return {item.title: item.topic_id for item in [*plan.items, *plan.excluded]}


# --- Planner on synthetic graphs ----------------------------------------------------------


@pytest.mark.parametrize("seed", range(25))
def test_random_graphs_order_validly_after_cycle_breaking(seed: int) -> None:
    rng = random.Random(seed)
    keys = [f"t{i}" for i in range(rng.randint(2, 30))]
    edges = [
        Edge(rng.choice(keys), rng.choice(keys), round(rng.random(), 3))
        for _ in range(rng.randint(0, 60))
    ]
    topics = [
        PlanTopic(
            key=k,
            estimate_minutes=rng.randint(15, 600),
            relevance=rng.random(),
            priority_rank=rng.randint(0, 3),
            mastered=rng.random() < 0.2,
            title=k,
        )
        for k in keys
    ]

    kept, dropped = break_cycles(set(keys), edges)
    order = order_topics(topics, kept)

    assert sorted(order) == sorted(keys)
    assert check_order(order, kept) == []
    for drop in dropped:
        assert drop.edge.topic in drop.cycle and drop.edge.requires in drop.cycle
    assert order_topics(topics, kept) == order  # deterministic


# --- Topic identity across runs -----------------------------------------------------------


async def test_recuration_keeps_topic_id_with_sessions_assets_and_threads(
    session: AsyncSession,
) -> None:
    await seed_profile(session)
    await seed_source(session, "Raft paper", ["Raft: Leader election.", "Logs: Replication."])
    await curate(session)
    raft = (await plan_titles(session, 1))["Raft"]
    await SessionService(session).log(raft, StudySessionCreate(minutes=40, rating="understood"))
    session.add_all(
        [Asset(topic_id=raft, format="video"), TutorThread(context_type="topic", context_id=raft)]
    )
    await session.commit()

    await seed_source(session, "Blog", ["Snapshots: Log compaction."])
    await curate(session)

    titles = await plan_titles(session, 2)
    assert titles["Raft"] == raft
    assert set(titles) == {"Raft", "Logs", "Snapshots"}
    history = await session_history(session, [raft])
    assert [s.minutes for s in history[raft]] == [40]
    assert (await session.scalar(select(Asset.topic_id))) == raft
    assert (await session.scalar(select(TutorThread.context_id))) == raft
    plan = await PlanService(session).current()
    assert next(i for i in plan.items if i.topic_id == raft).status == "in-progress"


async def test_changed_summary_creates_a_revision_and_old_plans_keep_old_text(
    session: AsyncSession, fakes: Script
) -> None:
    await seed_profile(session)
    await seed_source(session, "Raft paper", ["Raft: Leader election."])
    await curate(session)
    raft = (await plan_titles(session, 1))["Raft"]

    fakes.summaries["Raft"] = "Leader election, terms, and log matching."
    await curate(session)

    revisions = await topic_history(session, raft)
    assert [r.summary for r in revisions] == [
        "Leader election.",
        "Leader election, terms, and log matching.",
    ]
    v1, v2 = await PlanService(session).get(1), await PlanService(session).get(2)
    assert (v1.items[0].summary, v1.items[0].revision) == ("Leader election.", 1)
    assert (v2.items[0].summary, v2.items[0].revision) == (
        "Leader election, terms, and log matching.",
        2,
    )
    assert v2.items[0].topic_revision_id == revisions[1].id


async def test_unchanged_content_reuses_the_revision(session: AsyncSession) -> None:
    await seed_profile(session)
    await seed_source(session, "Raft paper", ["Raft: Leader election."])
    await curate(session)
    await curate(session)

    items = list(await session.scalars(select(PlanItem).order_by(PlanItem.position)))
    assert len({i.topic_revision_id for i in items}) == 1
    assert len(list(await session.scalars(select(TopicRevision)))) == 1


async def test_engineer_title_edit_survives_recuration(session: AsyncSession) -> None:
    await seed_profile(session)
    await seed_source(session, "Raft paper", ["Raft: Leader election."])
    await curate(session)
    raft = (await plan_titles(session, 1))["Raft"]
    await PlanService(session).edit(1, TopicEdit(op="edit", topic_id=raft, title="Raft basics"))

    await curate(session)

    titles = await plan_titles(session, 3)
    assert titles == {"Raft basics": raft}
    assert (await plan_titles(session, 1)) == {"Raft": raft}


async def test_merge_forwards_history_and_links_resolve_to_the_survivor(
    session: AsyncSession,
) -> None:
    await seed_profile(session)
    await seed_source(session, "Raft paper", ["Raft: Leader election.", "Terms: Epochs."])
    await curate(session)
    titles = await plan_titles(session, 1)
    raft, terms = titles["Raft"], titles["Terms"]
    await SessionService(session).log(terms, StudySessionCreate(minutes=25, rating="confident"))

    plan = await PlanService(session).edit(
        1, MergeEdit(op="merge", topic_id=terms, into_topic_id=raft)
    )

    assert [i.title for i in plan.items] == ["Raft"]
    assert (await resolve_topic(session, terms)).id == raft
    assert [s.minutes for s in (await session_history(session, [raft]))[raft]] == [25]
    old = await PlanService(session).get(1)
    assert next(i for i in old.items if i.topic_id == terms).merged_into == raft
    # A re-run matches the merged topic's title to the survivor instead of recreating it.
    await curate(session)
    assert set((await plan_titles(session, 3)).values()) == {raft}


async def test_stale_detection_for_profile_and_source_changes(session: AsyncSession) -> None:
    profile = await seed_profile(session)
    source, _ = await seed_source(session, "Raft paper", ["Raft: Leader election."])
    await curate(session)
    plan = await session.scalar(select(StudyPlan))
    assert plan is not None
    assert await stale_reasons(session, plan) == []

    source.status = "failed"
    await session.commit()
    assert await stale_reasons(session, plan) == ["Your ready sources changed (1 removed)."]

    source.status = "ready"
    profile.version = 2
    await session.commit()
    assert await stale_reasons(session, plan) == [
        "Your profile changed (version 2; this plan used version 1)."
    ]
    edited = await PlanService(session).edit(
        1, TopicEdit(op="edit", topic_id=(await plan_titles(session, 1))["Raft"], summary="New.")
    )
    assert edited.stale  # edits copy the inputs forward; only a run refreshes them
