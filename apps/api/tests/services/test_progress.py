import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.schema import (
    CurrentSkill,
    PlanItem,
    Profile,
    StudyPlan,
    StudySession,
    TargetSkill,
    Topic,
    TopicRevision,
)
from app.services.progress import (
    mastered_topic_ids,
    promote_achieved_targets,
    session_history,
    status_for,
    topic_stats,
    topic_status,
    used_pct,
    week_bounds,
)

T0 = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)  # a Monday


async def add_profile(session: AsyncSession, *, budget_hours: float = 5) -> Profile:
    profile = Profile(display_name="Ada", weekly_budget_hours=budget_hours, version=3)
    session.add(profile)
    await session.flush()
    return profile


async def add_target(
    session: AsyncSession, profile: Profile, name: str, level: str = "proficient"
) -> TargetSkill:
    target = TargetSkill(
        profile_id=profile.id,
        name=name,
        name_normalized=name.strip().casefold(),
        target_level=level,
        priority="high",
    )
    session.add(target)
    await session.flush()
    return target


async def add_plan(
    session: AsyncSession,
    version: int,
    items: list[tuple[uuid.UUID, list[str]]],
    *,
    excluded: frozenset[uuid.UUID] = frozenset(),
    estimate: int = 60,
) -> StudyPlan:
    """A plan whose items serve the given target references (ids or names), in order."""
    plan = StudyPlan(version=version, created_by="run", profile_version=1)
    session.add(plan)
    await session.flush()
    for position, (topic_id, serves) in enumerate(items):
        revision = TopicRevision(
            topic_id=topic_id,
            revision=version,
            title=f"Topic {position}",
            summary="...",
            created_by="run",
        )
        session.add(revision)
        await session.flush()
        session.add(
            PlanItem(
                plan_id=plan.id,
                topic_id=topic_id,
                topic_revision_id=revision.id,
                position=position,
                serves_targets=serves,
                excluded=topic_id in excluded,
                estimate_minutes=estimate,
            )
        )
    await session.commit()
    return plan


async def add_topic(session: AsyncSession, *ratings: str, minutes: int = 30) -> uuid.UUID:
    topic = Topic()
    session.add(topic)
    await session.flush()
    for i, rating in enumerate(ratings):
        session.add(
            StudySession(
                topic_id=topic.id,
                minutes=minutes,
                rating=rating,
                studied_at=T0 + timedelta(hours=i),
            )
        )
    await session.commit()
    return topic.id


@pytest.mark.parametrize(
    ("latest", "status"),
    [
        (None, "not-started"),
        ("struggled", "in-progress"),
        ("understood", "in-progress"),
        ("confident", "mastered"),
        ("could-teach", "mastered"),
    ],
)
def test_status_for_latest_rating(latest: str | None, status: str) -> None:
    assert status_for(latest) == status


async def test_topic_status_uses_latest_session(session: AsyncSession) -> None:
    assert await topic_status(session, await add_topic(session)) == "not-started"
    assert await topic_status(session, await add_topic(session, "confident")) == "mastered"
    assert (
        await topic_status(session, await add_topic(session, "struggled", "confident"))
        == "mastered"
    )


async def test_session_history_and_mastered_ids(session: AsyncSession) -> None:
    mastered = await add_topic(session, "understood", "could-teach")
    learning = await add_topic(session, "struggled")
    untouched = await add_topic(session)

    history = await session_history(session, [mastered, learning, untouched])
    assert [s.rating for s in history[mastered]] == ["understood", "could-teach"]
    assert [s.rating for s in history[learning]] == ["struggled"]
    assert history[untouched] == []
    assert await session_history(session, []) == {}
    assert await mastered_topic_ids(session) == {mastered}


async def test_topic_stats_totals(session: AsyncSession) -> None:
    topic = await add_topic(session, "struggled", "understood", minutes=25)
    other = uuid.uuid4()
    stats = await topic_stats(session, [topic, other])
    assert (stats[topic].status, stats[topic].minutes, stats[topic].sessions) == (
        "in-progress",
        50,
        2,
    )
    assert stats[other].status == "not-started"


@pytest.mark.parametrize(
    ("now", "offset", "start"),
    [
        # Wednesday noon UTC: the week starts Monday 00:00 UTC.
        (datetime(2026, 10, 14, 12, tzinfo=UTC), 0, datetime(2026, 10, 12, tzinfo=UTC)),
        # Monday 01:00 UTC is still Sunday evening in São Paulo (UTC-3): previous week.
        (datetime(2026, 10, 12, 1, tzinfo=UTC), -180, datetime(2026, 10, 5, 3, tzinfo=UTC)),
        # Sunday 23:00 UTC is already Monday in Tokyo (UTC+9).
        (datetime(2026, 10, 11, 23, tzinfo=UTC), 540, datetime(2026, 10, 11, 15, tzinfo=UTC)),
    ],
)
def test_week_bounds(now: datetime, offset: int, start: datetime) -> None:
    assert week_bounds(now, offset) == (start, start + timedelta(days=7))


def test_used_pct() -> None:
    assert used_pct(180, 300) == 60.0
    assert used_pct(0, 300) == 0.0
    assert used_pct(400, 300) == 133.3
    assert used_pct(10, None) is None


async def test_promotion_adds_current_skill_and_bumps_version_once(
    session: AsyncSession,
) -> None:
    profile = await add_profile(session)
    raft_target = await add_target(session, profile, "Raft")
    paxos_target = await add_target(session, profile, "Paxos", level="expert")
    a = await add_topic(session, "confident")
    b = await add_topic(session, "could-teach")
    # Served by name (any case) and by id.
    await add_plan(session, 1, [(a, [" RAFT "]), (b, [str(paxos_target.id)])])

    promoted = await promote_achieved_targets(session, now=lambda: T0)
    await session.commit()

    assert {(p.name, p.level) for p in promoted} == {("Raft", "proficient"), ("Paxos", "expert")}
    assert profile.version == 4
    assert raft_target.achieved_at is not None
    skills = (await session.scalars(select(CurrentSkill))).all()
    assert {(s.name, s.level) for s in skills} == {("Raft", "proficient"), ("Paxos", "expert")}
    # Already achieved targets are not promoted again.
    assert await promote_achieved_targets(session) == []
    assert profile.version == 4


async def test_promotion_raises_a_lower_current_skill(session: AsyncSession) -> None:
    profile = await add_profile(session)
    session.add(
        CurrentSkill(profile_id=profile.id, name="Raft", name_normalized="raft", level="novice")
    )
    target = await add_target(session, profile, "Raft", level="working")
    await add_plan(session, 1, [(await add_topic(session, "confident"), [str(target.id)])])

    assert [p.name for p in await promote_achieved_targets(session)] == ["Raft"]
    [skill] = (await session.scalars(select(CurrentSkill))).all()
    assert skill.level == "working"


async def test_no_promotion_while_a_serving_topic_is_unmastered(session: AsyncSession) -> None:
    profile = await add_profile(session)
    target = await add_target(session, profile, "Raft")
    done = await add_topic(session, "confident")
    pending = await add_topic(session, "understood")
    await add_plan(session, 1, [(done, [str(target.id)]), (pending, [str(target.id)])])

    assert await promote_achieved_targets(session) == []
    assert target.achieved_at is None
    assert profile.version == 3


async def test_promotion_uses_latest_plan_and_ignores_excluded_items(
    session: AsyncSession,
) -> None:
    profile = await add_profile(session)
    target = await add_target(session, profile, "Raft")
    done = await add_topic(session, "confident")
    dropped = await add_topic(session)
    noise = await add_topic(session)
    ref = [str(target.id)]
    await add_plan(session, 1, [(done, ref), (dropped, ref)])
    await add_plan(session, 2, [(done, ref), (noise, ref)], excluded=frozenset({noise}))

    assert [p.id for p in await promote_achieved_targets(session)] == [target.id]


async def test_target_without_serving_topics_is_never_promoted(session: AsyncSession) -> None:
    profile = await add_profile(session)
    await add_target(session, profile, "Raft")
    await add_plan(session, 1, [(await add_topic(session, "confident"), ["something else"])])

    assert await promote_achieved_targets(session) == []
    assert await promote_achieved_targets(session) == []
