import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.support.curation import seed_profile

from app.db.schema import CurationRun, PlanItem, StudyPlan, StudySession, TargetSkill
from app.services.planner import Edge
from app.services.plans import RunTopic, priority_rank, write_run_plan
from app.services.topics import create_topic, current_revision, merge_topics


async def run_topic(
    session: AsyncSession, title: str, relevance: float, minutes: int, serves: list[str]
) -> RunTopic:
    topic, revision = await create_topic(session, title=title, summary="s", created_by="run")
    return RunTopic(
        topic_id=topic.id,
        revision_id=revision.id,
        title=title,
        relevance=relevance,
        serves_targets=serves,
        rationale=f"{title} rationale",
        estimate_minutes=minutes,
    )


async def items(session: AsyncSession, plan: StudyPlan) -> list[PlanItem]:
    rows = await session.scalars(
        select(PlanItem).where(PlanItem.plan_id == plan.id).order_by(PlanItem.position)
    )
    return list(rows)


def test_priority_rank_uses_the_best_active_target() -> None:
    high = TargetSkill(id=uuid.uuid4(), priority="high")
    low = TargetSkill(id=uuid.uuid4(), priority="low")

    assert priority_rank([str(low.id), str(high.id)], [high, low]) == 0
    assert priority_rank([str(low.id)], [high, low]) == 2
    assert priority_rank([], [high, low]) == 3


async def test_run_plan_orders_packs_and_lists_excluded(session: AsyncSession) -> None:
    profile = await seed_profile(session, budget_hours=5)
    target = await session.scalar(select(TargetSkill))
    assert target is not None
    serves = [str(target.id)]
    terms = await run_topic(session, "Terms", 0.9, 200, serves)
    logs = await run_topic(session, "Logs", 0.8, 450, serves)
    snapshots = await run_topic(session, "Snapshots", 0.7, 100, serves)
    css = await run_topic(session, "CSS", 0.1, 60, [])
    run = CurationRun(profile_version=profile.version, source_ids=["s1"], threshold=0.4)
    session.add(run)
    await session.flush()

    plan = await write_run_plan(
        session,
        run,
        [snapshots, css, logs, terms],
        [Edge(str(logs.topic_id), str(terms.topic_id), 0.9)],
    )
    second = await write_run_plan(session, run, [terms], [])

    assert (plan.version, second.version) == (1, 2)
    assert (plan.created_by, plan.profile_version, plan.source_ids) == ("run", 1, ["s1"])
    rows = await items(session, plan)
    by_title = {r.topic_id: r for r in rows}
    order = [r.topic_id for r in rows]
    assert order == [terms.topic_id, logs.topic_id, snapshots.topic_id, css.topic_id]
    assert by_title[terms.topic_id].week_start == 1
    oversized = by_title[logs.topic_id]
    assert (oversized.week_start, oversized.week_span, oversized.exceeds_weekly_budget) == (
        2,
        2,
        True,
    )
    assert by_title[snapshots.topic_id].week_start == 4
    assert oversized.prerequisites == [str(terms.topic_id)]
    excluded = by_title[css.topic_id]
    assert excluded.excluded and excluded.week_start is None
    assert excluded.rationale == "CSS rationale"
    assert all(r.topic_revision_id for r in rows)


async def test_run_plan_promotes_targets_already_mastered(session: AsyncSession) -> None:
    profile = await seed_profile(session)
    target = await session.scalar(select(TargetSkill))
    assert target is not None
    raft = await run_topic(session, "Raft", 0.9, 60, [str(target.id)])
    session.add(StudySession(topic_id=raft.topic_id, minutes=60, rating="could-teach"))
    run = CurationRun(profile_version=profile.version, source_ids=[], threshold=0.4)
    session.add(run)
    await session.flush()

    await write_run_plan(session, run, [raft], [])

    await session.refresh(target)
    assert target.achieved_at is not None


async def test_run_plan_follows_a_merge_made_while_deferred(session: AsyncSession) -> None:
    profile = await seed_profile(session)
    survivor = await run_topic(session, "Raft", 0.9, 60, [])
    loser = await run_topic(session, "Raft consensus", 0.8, 60, [])
    await merge_topics(session, loser.topic_id, survivor.topic_id)
    run = CurationRun(profile_version=profile.version, source_ids=[], threshold=0.0)
    session.add(run)
    await session.flush()

    plan = await write_run_plan(session, run, [loser, survivor], [])

    [item] = await items(session, plan)
    revision = await current_revision(session, survivor.topic_id)
    assert (item.topic_id, item.topic_revision_id) == (survivor.topic_id, revision.id)
