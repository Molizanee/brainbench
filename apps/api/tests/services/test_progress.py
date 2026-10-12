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
from app.models.progress import StudySessionCreate
from app.services.progress import (
    ProgressService,
    mastered_topic_ids,
    promote_achieved_targets,
    session_history,
    status_for,
    topic_stats,
    topic_status,
    used_pct,
    week_bounds,
)
from app.services.sessions import SessionService

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


async def test_target_promoted_when_every_serving_topic_is_mastered(
    session: AsyncSession,
) -> None:
    """Spec: every topic serving "distributed consensus" at "proficient" is mastered."""
    profile = await add_profile(session)
    target = await add_target(session, profile, "Distributed consensus")
    other = await add_target(session, profile, "Stream processing")
    raft, paxos, kafka = [await add_topic(session) for _ in range(3)]
    await add_plan(
        session,
        1,
        [(raft, [str(target.id)]), (paxos, ["distributed consensus"]), (kafka, [str(other.id)])],
    )
    ticks = iter(T0 + timedelta(minutes=i) for i in range(1, 100))
    sessions = SessionService(session, now=lambda: next(ticks))

    first = await sessions.log(raft, StudySessionCreate(minutes=60, rating="confident"))
    assert first.promoted_targets == []
    regressed = await sessions.log(paxos, StudySessionCreate(minutes=30, rating="struggled"))
    assert regressed.promoted_targets == []
    assert target.achieved_at is None

    last = await sessions.log(paxos, StudySessionCreate(minutes=45, rating="could-teach"))
    assert [(p.name, p.level) for p in last.promoted_targets] == [
        ("Distributed consensus", "proficient")
    ]
    await session.refresh(profile)
    await session.refresh(target)
    await session.refresh(other)
    assert profile.version == 4
    assert target.achieved_at is not None
    assert other.achieved_at is None
    skills = (await session.scalars(select(CurrentSkill))).all()
    assert [(s.name, s.level) for s in skills] == [("Distributed consensus", "proficient")]

    # A later regression changes the topic status but does not revoke the achievement.
    await sessions.log(raft, StudySessionCreate(minutes=15, rating="struggled"))
    summary = await ProgressService(session, now=lambda: T0).summary()
    consensus = next(t for t in summary.targets if t.id == target.id)
    assert consensus.achieved_at is not None
    assert consensus.counts.in_progress == 1
    assert consensus.counts.mastered == 1


async def test_weekly_pace_against_budget(session: AsyncSession) -> None:
    """Spec: 180 minutes in the current week with a 300-minute budget is 60%."""
    await add_profile(session, budget_hours=5)
    topic = await add_topic(session)
    week_start = datetime(2026, 10, 12, tzinfo=UTC)
    for at, minutes in [
        (week_start - timedelta(seconds=1), 500),  # previous week
        (week_start, 60),  # first instant of the week
        (week_start + timedelta(days=2), 90),
        (week_start + timedelta(days=6, hours=23), 30),
        (week_start + timedelta(days=7), 400),  # next week
    ]:
        session.add(
            StudySession(topic_id=topic, minutes=minutes, rating="understood", studied_at=at)
        )
    await session.commit()

    now = week_start + timedelta(days=3, hours=12)
    summary = await ProgressService(session, now=lambda: now).summary()
    assert summary.week.week_start == week_start
    assert summary.week.minutes_studied == 180
    assert summary.week.budget_minutes == 300
    assert summary.week.used_pct == 60.0
    assert summary.minutes_studied == 1080


async def test_weekly_pace_follows_the_viewer_week(session: AsyncSession) -> None:
    await add_profile(session, budget_hours=2.5)
    topic = await add_topic(session)
    # Monday 01:00 UTC is Sunday 22:00 in UTC-3: it belongs to the previous local week.
    session.add(
        StudySession(
            topic_id=topic,
            minutes=75,
            rating="understood",
            studied_at=datetime(2026, 10, 12, 1, tzinfo=UTC),
        )
    )
    await session.commit()
    now = datetime(2026, 10, 12, 2, tzinfo=UTC)

    utc = await ProgressService(session, now=lambda: now).summary(utc_offset_minutes=0)
    local = await ProgressService(session, now=lambda: now).summary(utc_offset_minutes=-180)
    assert (utc.week.minutes_studied, utc.week.used_pct) == (75, 50.0)
    assert (local.week.minutes_studied, local.week.used_pct) == (75, 50.0)
    later = datetime(2026, 10, 12, 4, tzinfo=UTC)  # Monday 01:00 local: a new local week
    fresh = await ProgressService(session, now=lambda: later).summary(utc_offset_minutes=-180)
    assert fresh.week.minutes_studied == 0
    assert fresh.week.used_pct == 0.0


async def test_minutes_remaining_counts_only_unmastered_estimates(session: AsyncSession) -> None:
    profile = await add_profile(session)
    target = await add_target(session, profile, "Raft")
    done = await add_topic(session, "confident")
    started = await add_topic(session, "struggled")
    fresh = await add_topic(session)
    ref = [str(target.id)]
    await add_plan(session, 1, [(done, ref), (started, ref), (fresh, ref)], estimate=90)

    [summary] = (await ProgressService(session, now=lambda: T0).summary()).targets
    assert summary.counts.model_dump() == {"mastered": 1, "in_progress": 1, "not_started": 1}
    assert summary.minutes_studied == 60
    assert summary.minutes_remaining == 180
