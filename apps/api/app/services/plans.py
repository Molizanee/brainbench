"""Study plan versions (design D6): immutable ``study_plans`` rows with their ``plan_items``.

A curation run writes a new version from its scored topics; every edit copies the current
version forward into a new one. Items reference ``(topic_id, topic_revision_id)`` so an old
version keeps rendering the text it was built with. Functions flush; the caller commits.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.schema import CurationRun, PlanItem, StudyPlan, TargetSkill
from app.services.planner import NO_PRIORITY, PRIORITY_RANK, Edge, PlanTopic, plan_topics
from app.services.profile import get_profile, list_target_skills
from app.services.progress import mastered_topic_ids, promote_achieved_targets
from app.services.topics import current_revision, resolve_topic

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ItemDraft:
    """One plan item as written; ``prerequisites`` are topic ids (strings)."""

    topic_id: uuid.UUID
    topic_revision_id: uuid.UUID
    title: str
    relevance: float | None
    rationale: str | None
    serves_targets: list[str]
    estimate_minutes: int | None
    prerequisites: list[str] = field(default_factory=list)
    excluded: bool = False
    manually_included: bool = False
    week_start: int | None = None
    week_span: int | None = None
    exceeds_weekly_budget: bool = False


def priority_rank(serves: Sequence[str], targets: Sequence[TargetSkill]) -> int:
    """Best priority among the active targets a topic serves (no target ranks last)."""
    ranks = [
        PRIORITY_RANK.get(t.priority, NO_PRIORITY)
        for t in targets
        if str(t.id) in serves and t.achieved_at is None
    ]
    return min(ranks, default=NO_PRIORITY)


async def budget_minutes(session: AsyncSession) -> int:
    profile = await get_profile(session)
    return max(1, round((profile.weekly_budget_hours if profile else 1.0) * 60))


async def schedule(
    session: AsyncSession, included: Sequence[ItemDraft], edges: Sequence[Edge]
) -> list[ItemDraft]:
    """Order and pack the included items with the planner; prerequisites set from ``edges``."""
    profile = await get_profile(session)
    targets = await list_target_skills(session, profile.id) if profile else []
    mastered = await mastered_topic_ids(session)
    by_key = {str(d.topic_id): d for d in included}
    topics = [
        PlanTopic(
            key=key,
            estimate_minutes=d.estimate_minutes or 0,
            relevance=d.relevance or 0.0,
            priority_rank=priority_rank(d.serves_targets, targets),
            mastered=d.topic_id in mastered,
            title=d.title,
        )
        for key, d in by_key.items()
    ]
    placements = plan_topics(topics, edges, await budget_minutes(session))
    return [
        pack(by_key[p.key], p.week_start, p.week_span, p.exceeds_weekly_budget, edges)
        for p in placements
    ]


def pack(
    draft: ItemDraft, week_start: int, week_span: int, exceeds: bool, edges: Sequence[Edge]
) -> ItemDraft:
    key = str(draft.topic_id)
    return replace(
        draft,
        week_start=week_start,
        week_span=week_span,
        exceeds_weekly_budget=exceeds,
        prerequisites=sorted({e.requires for e in edges if e.topic == key}),
    )


async def next_version(session: AsyncSession) -> int:
    latest = await session.scalar(select(func.max(StudyPlan.version)))
    return (latest or 0) + 1


async def write_plan(
    session: AsyncSession,
    *,
    created_by: str,
    profile_version: int,
    source_ids: Sequence[str],
    included: Sequence[ItemDraft],
    excluded: Sequence[ItemDraft],
    curation_run_id: uuid.UUID | None = None,
) -> StudyPlan:
    """Insert the next plan version: included items in order, then the excluded ones.

    Promotes targets whose serving topics are all mastered in the new plan (learning-progress).
    """
    plan = StudyPlan(
        version=await next_version(session),
        curation_run_id=curation_run_id,
        created_by=created_by,
        profile_version=profile_version,
        source_ids=list(source_ids),
    )
    session.add(plan)
    await session.flush()
    rows = [
        *included,
        *(
            replace(d, excluded=True, week_start=None, week_span=None, exceeds_weekly_budget=False)
            for d in excluded
        ),
    ]
    session.add_all(
        PlanItem(
            plan_id=plan.id,
            topic_id=d.topic_id,
            topic_revision_id=d.topic_revision_id,
            position=position,
            week_start=d.week_start,
            week_span=d.week_span,
            exceeds_weekly_budget=d.exceeds_weekly_budget,
            prerequisites=list(d.prerequisites),
            manually_included=d.manually_included,
            excluded=d.excluded,
            relevance=d.relevance,
            rationale=d.rationale,
            serves_targets=list(d.serves_targets),
            estimate_minutes=d.estimate_minutes,
        )
        for position, d in enumerate(rows)
    )
    await session.flush()
    await promote_achieved_targets(session)
    log.info("plan version %d written (%s, %d items)", plan.version, created_by, len(rows))
    return plan


@dataclass(frozen=True)
class RunTopic:
    """A run's topic with its score, as the planning step receives it."""

    topic_id: uuid.UUID
    revision_id: uuid.UUID
    title: str
    relevance: float
    serves_targets: list[str]
    rationale: str
    estimate_minutes: int


async def _current(session: AsyncSession, topic: RunTopic) -> RunTopic:
    """Follow a merge made while the run was deferred: plan the survivor's current text."""
    resolved = await resolve_topic(session, topic.topic_id)
    if resolved.id == topic.topic_id:
        return topic
    revision = await current_revision(session, resolved.id)
    return replace(topic, topic_id=resolved.id, revision_id=revision.id, title=revision.title)


async def write_run_plan(
    session: AsyncSession,
    run: CurationRun,
    topics: Sequence[RunTopic],
    edges: Sequence[Edge],
) -> StudyPlan:
    """The plan a curation run produces: topics at or above the run threshold are ordered and
    packed into weeks; the others are listed as excluded, most relevant first."""
    seen: set[uuid.UUID] = set()
    current: list[RunTopic] = []
    alias: dict[str, str] = {}
    for topic in topics:
        resolved = await _current(session, topic)
        alias[str(topic.topic_id)] = str(resolved.topic_id)
        if resolved.topic_id not in seen:
            seen.add(resolved.topic_id)
            current.append(resolved)
    edges = [
        replace(e, topic=alias.get(e.topic, e.topic), requires=alias.get(e.requires, e.requires))
        for e in edges
    ]
    drafts = [
        ItemDraft(
            topic_id=t.topic_id,
            topic_revision_id=t.revision_id,
            title=t.title,
            relevance=t.relevance,
            rationale=t.rationale,
            serves_targets=list(t.serves_targets),
            estimate_minutes=t.estimate_minutes,
        )
        for t in current
    ]
    kept = [d for d in drafts if (d.relevance or 0.0) >= run.threshold]
    dropped = sorted(
        (d for d in drafts if (d.relevance or 0.0) < run.threshold),
        key=lambda d: (-(d.relevance or 0.0), d.title.casefold()),
    )
    return await write_plan(
        session,
        created_by="run",
        profile_version=run.profile_version,
        source_ids=run.source_ids,
        included=await schedule(session, kept, edges),
        excluded=dropped,
        curation_run_id=run.id,
    )
