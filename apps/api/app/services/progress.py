"""Topic status derivation and the progress summary (learning-progress spec).

A topic is "mastered" when its latest session is rated confident or could-teach, "in-progress"
when it has any other session, and "not-started" without sessions. Topics serve a target skill
through the current plan: the latest ``study_plans`` version, items not excluded, whose
``serves_targets`` names the target by id or by name.

``topic_status``, ``session_history`` and ``mastered_topic_ids`` are consumed by curation
scoring and the planner (section 6).
"""

import logging
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.schema import (
    PlanItem,
    Profile,
    StudyPlan,
    StudySession,
    TargetSkill,
    TopicRevision,
)
from app.models.progress import (
    ProgressRead,
    StatusCounts,
    TargetProgress,
    TargetTopic,
    TopicStatus,
    WeeklyPace,
)

log = logging.getLogger(__name__)

MASTERED_RATINGS = frozenset({"confident", "could-teach"})

type Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


def status_for(latest_rating: str | None) -> TopicStatus:
    if latest_rating is None:
        return "not-started"
    return "mastered" if latest_rating in MASTERED_RATINGS else "in-progress"


def normalize_name(name: str) -> str:
    return name.strip().casefold()


@dataclass(frozen=True)
class TopicStats:
    status: TopicStatus
    minutes: int
    sessions: int


NOT_STARTED = TopicStats(status="not-started", minutes=0, sessions=0)


async def _latest_ratings(
    session: AsyncSession, topic_ids: Iterable[uuid.UUID] | None = None
) -> dict[uuid.UUID, str]:
    rank = (
        func.row_number()
        .over(
            partition_by=StudySession.topic_id,
            order_by=(StudySession.studied_at.desc(), StudySession.created_at.desc()),
        )
        .label("rank")
    )
    ranked = select(StudySession.topic_id, StudySession.rating, rank)
    if topic_ids is not None:
        ranked = ranked.where(StudySession.topic_id.in_(list(topic_ids)))
    sub = ranked.subquery()
    rows = await session.execute(select(sub.c.topic_id, sub.c.rating).where(sub.c.rank == 1))
    return {topic_id: rating for topic_id, rating in rows}


async def topic_stats(
    session: AsyncSession, topic_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, TopicStats]:
    """Status, studied minutes and session count for each topic (every id is in the result)."""
    ids = list(dict.fromkeys(topic_ids))
    if not ids:
        return {}
    latest = await _latest_ratings(session, ids)
    totals = await session.execute(
        select(StudySession.topic_id, func.sum(StudySession.minutes), func.count())
        .where(StudySession.topic_id.in_(ids))
        .group_by(StudySession.topic_id)
    )
    stats = dict.fromkeys(ids, NOT_STARTED)
    for topic_id, minutes, count in totals:
        stats[topic_id] = TopicStats(
            status=status_for(latest.get(topic_id)), minutes=int(minutes or 0), sessions=count
        )
    return stats


async def topic_status(session: AsyncSession, topic_id: uuid.UUID) -> str:
    return status_for((await _latest_ratings(session, [topic_id])).get(topic_id))


async def session_history(
    session: AsyncSession, topic_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[StudySession]]:
    """Sessions per topic, oldest first; every requested id is in the result."""
    ids = list(dict.fromkeys(topic_ids))
    history: dict[uuid.UUID, list[StudySession]] = {topic_id: [] for topic_id in ids}
    if not ids:
        return history
    rows = await session.scalars(
        select(StudySession)
        .where(StudySession.topic_id.in_(ids))
        .order_by(StudySession.studied_at, StudySession.created_at)
    )
    for row in rows:
        history[row.topic_id].append(row)
    return history


async def mastered_topic_ids(session: AsyncSession) -> set[uuid.UUID]:
    latest = await _latest_ratings(session)
    return {topic_id for topic_id, rating in latest.items() if rating in MASTERED_RATINGS}


# --- Plan and targets ---------------------------------------------------------------------


@dataclass(frozen=True)
class ServingTopic:
    topic_id: uuid.UUID
    title: str
    estimate_minutes: int | None
    serves: tuple[str, ...]


async def current_plan(session: AsyncSession) -> StudyPlan | None:
    return await session.scalar(select(StudyPlan).order_by(StudyPlan.version.desc()).limit(1))


async def plan_topics(session: AsyncSession, plan: StudyPlan) -> list[ServingTopic]:
    """Items of ``plan`` that are not excluded, in plan order, with their revision title."""
    rows = await session.execute(
        select(PlanItem, TopicRevision.title)
        .join(TopicRevision, TopicRevision.id == PlanItem.topic_revision_id)
        .where(PlanItem.plan_id == plan.id, PlanItem.excluded.is_(False))
        .order_by(PlanItem.position)
    )
    return [
        ServingTopic(
            topic_id=item.topic_id,
            title=title,
            estimate_minutes=item.estimate_minutes,
            serves=tuple(str(s) for s in item.serves_targets),
        )
        for item, title in rows
    ]


def serves(topic: ServingTopic, target: TargetSkill) -> bool:
    keys = {str(target.id), target.name_normalized, normalize_name(target.name)}
    return any(entry in keys or normalize_name(entry) in keys for entry in topic.serves)


async def load_profile(session: AsyncSession) -> Profile | None:
    return await session.scalar(select(Profile).limit(1))


async def target_skills(session: AsyncSession, profile: Profile) -> list[TargetSkill]:
    rows = await session.scalars(
        select(TargetSkill)
        .where(TargetSkill.profile_id == profile.id)
        .order_by(TargetSkill.created_at, TargetSkill.name_normalized)
    )
    return list(rows)


# --- Summary ------------------------------------------------------------------------------


def week_bounds(now: datetime, utc_offset_minutes: int) -> tuple[datetime, datetime]:
    """Monday 00:00 to the next Monday 00:00 in the viewer's local time, returned in UTC."""
    offset = timedelta(minutes=utc_offset_minutes)
    local = now.astimezone(UTC) + offset
    monday = (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start = monday - offset
    return start, start + timedelta(days=7)


def used_pct(minutes: int, budget_minutes: int | None) -> float | None:
    if not budget_minutes:
        return None
    return round(minutes * 100 / budget_minutes, 1)


class ProgressService:
    def __init__(self, session: AsyncSession, *, now: Clock = utc_now) -> None:
        self.session = session
        self.now = now

    async def summary(self, *, utc_offset_minutes: int = 0) -> ProgressRead:
        profile = await load_profile(self.session)
        plan = await current_plan(self.session)
        targets = await target_skills(self.session, profile) if profile else []
        topics = await plan_topics(self.session, plan) if plan else []
        stats = await topic_stats(self.session, (t.topic_id for t in topics))

        start, end = week_bounds(self.now(), utc_offset_minutes)
        week_minutes = await self._minutes(start, end)
        budget = round(profile.weekly_budget_hours * 60) if profile else None

        return ProgressRead(
            plan_version=plan.version if plan else None,
            profile_version=profile.version if profile else None,
            minutes_studied=await self._minutes(),
            week=WeeklyPace(
                week_start=start,
                week_end=end,
                minutes_studied=week_minutes,
                budget_minutes=budget,
                used_pct=used_pct(week_minutes, budget),
            ),
            targets=[self._target(target, topics, stats) for target in targets],
        )

    async def _minutes(self, start: datetime | None = None, end: datetime | None = None) -> int:
        query = select(func.coalesce(func.sum(StudySession.minutes), 0))
        if start is not None and end is not None:
            query = query.where(StudySession.studied_at >= start, StudySession.studied_at < end)
        return int(await self.session.scalar(query) or 0)

    @staticmethod
    def _target(
        target: TargetSkill, topics: list[ServingTopic], stats: dict[uuid.UUID, TopicStats]
    ) -> TargetProgress:
        rows = [
            TargetTopic(
                topic_id=t.topic_id,
                title=t.title,
                status=stats[t.topic_id].status,
                minutes_studied=stats[t.topic_id].minutes,
                estimate_minutes=t.estimate_minutes,
            )
            for t in topics
            if serves(t, target)
        ]
        counts = StatusCounts(
            mastered=sum(r.status == "mastered" for r in rows),
            in_progress=sum(r.status == "in-progress" for r in rows),
            not_started=sum(r.status == "not-started" for r in rows),
        )
        return TargetProgress(
            id=target.id,
            name=target.name,
            target_level=target.target_level,
            priority=target.priority,
            achieved_at=target.achieved_at,
            counts=counts,
            minutes_studied=sum(r.minutes_studied for r in rows),
            minutes_remaining=sum(r.estimate_minutes or 0 for r in rows if r.status != "mastered"),
            topics=rows,
        )
