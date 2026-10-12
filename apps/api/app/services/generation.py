"""Generation requests (visual-content-generation spec, tasks 7.5 and 7.6).

One job per topic and format, deduplicated on ``dedupe_key = "<format>:<topic_id>"`` while a
job with that key is queued or running (the existing job is returned instead). New jobs need a
connected provider for every task class the format uses and room in the budgets; the daily job
counter grows by the number of jobs actually created.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError, NotFound
from app.db.schema import Asset, Job, PlanItem, Profile, StudyPlan, TopicRevision
from app.models.assets import (
    BudgetRead,
    GenerateResponse,
    GenerationJob,
    PlanGenerateResponse,
)
from app.services import budget, jobs, providers
from app.services import storyboard as sb
from app.services.topics import TopicNotFound, resolve_topic

log = logging.getLogger(__name__)

FORMATS = ("video", "figure")
FORMAT_STEPS: dict[str, tuple[str, ...]] = {
    "video": ("storyboard", "manim_script", "script_repair"),
    "figure": ("storyboard", "figure_brief", "figure"),
}


class AssetNotFound(NotFound):
    code = "asset-not-found"


class NoPlan(NotFound):
    code = "plan-not-found"


class NothingToGenerate(DomainError):
    code = "nothing-to-generate"


def dedupe_key(format: str, topic_id: uuid.UUID) -> str:
    return f"{format}:{topic_id}"


def canonical_formats(formats: Sequence[str]) -> list[str]:
    return [f for f in FORMATS if f in set(formats)]


async def active_job(session: AsyncSession, format: str, topic_id: uuid.UUID) -> Job | None:
    stmt = select(Job).where(
        Job.dedupe_key == dedupe_key(format, topic_id), Job.status.in_(jobs.ACTIVE_STATUSES)
    )
    return (await session.execute(stmt)).scalars().first()


async def ensure_providers(session: AsyncSession, formats: Sequence[str]) -> None:
    """``NoProvider`` (400) or ``VaultLockedError`` (409) unless every step can be routed."""
    steps = dict.fromkeys(step for f in formats for step in FORMAT_STEPS[f])
    for task_class in dict.fromkeys(sb.STEP_TASK_CLASSES[step] for step in steps):
        await providers.ensure_provider_for(session, task_class)


@dataclass(frozen=True)
class Requested:
    topic_id: uuid.UUID
    format: str
    job: Job | None  # None in a dry run for a job that would be created
    deduplicated: bool


@dataclass(frozen=True)
class Planned:
    topic_id: uuid.UUID
    title: str
    format: str
    existing: Job | None


async def _enqueue_new(
    session: AsyncSession,
    planned: Sequence[Planned],
    *,
    feedback: str | None = None,
    previous_asset_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> list[Requested]:
    """Create the jobs that do not exist yet, after the provider and budget checks."""
    new = [p for p in planned if p.existing is None]
    if new:
        await ensure_providers(session, canonical_formats([p.format for p in new]))
        await budget.check_can_enqueue(session, len(new), now=now)
    requested = []
    for item in planned:
        if item.existing is not None:
            requested.append(Requested(item.topic_id, item.format, item.existing, True))
            continue
        payload: dict[str, str] = {"topic_id": str(item.topic_id), "format": item.format}
        if feedback:
            payload["feedback"] = feedback
        if previous_asset_id is not None:
            payload["previous_asset_id"] = str(previous_asset_id)
        job = await jobs.enqueue(
            session, item.format, payload, dedupe_key=dedupe_key(item.format, item.topic_id)
        )
        requested.append(Requested(item.topic_id, item.format, job, False))
    created = sum(1 for r in requested if not r.deduplicated)
    if created:
        await budget.record_generation_jobs(session, created, now=now)
        log.info("%d generation job(s) queued", created)
    return requested


async def request_for_topic(
    session: AsyncSession,
    topic_id: uuid.UUID,
    formats: Sequence[str],
    *,
    feedback: str | None = None,
    previous_asset_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> list[Requested]:
    """Queue one job per requested format (flushed; the caller commits)."""
    topic = await resolve_topic(session, topic_id)
    revision = await sb.latest_revision(session, topic.id)
    if revision is None:
        raise TopicNotFound(f"Topic {topic_id} has no revision.")
    planned = [
        Planned(topic.id, revision.title, f, await active_job(session, f, topic.id))
        for f in canonical_formats(formats)
    ]
    if any(p.existing is None for p in planned) and not await sb.topic_chunk_ids(session, topic.id):
        raise sb.TopicNotGrounded(
            f"Topic {revision.title!r} references no source chunks, so its visuals cannot be "
            "grounded."
        )
    return await _enqueue_new(
        session, planned, feedback=feedback, previous_asset_id=previous_asset_id, now=now
    )


async def regenerate(
    session: AsyncSession, asset_id: uuid.UUID, feedback: str, *, now: datetime | None = None
) -> Requested:
    """A new job for the asset's topic and format carrying the feedback and the previous
    asset (its storyboard, script or build source). An active job is returned instead."""
    asset = await session.get(Asset, asset_id)
    if asset is None:
        raise AssetNotFound(f"Asset {asset_id} does not exist.")
    (requested,) = await request_for_topic(
        session,
        asset.topic_id,
        [asset.format],
        feedback=feedback.strip() or None,
        previous_asset_id=asset.id,
        now=now,
    )
    return requested


async def plan_formats(session: AsyncSession) -> list[str]:
    """The profile's preferred formats that are visual (both without a profile)."""
    profile = (await session.execute(select(Profile).limit(1))).scalar_one_or_none()
    preferred = list(profile.preferred_formats) if profile is not None else list(FORMATS)
    return canonical_formats(preferred)


async def plan_items(session: AsyncSession) -> tuple[StudyPlan, list[tuple[uuid.UUID, str]]]:
    """The latest plan and its non-excluded topics in plan order, with the plan's titles."""
    plan = (
        await session.execute(select(StudyPlan).order_by(StudyPlan.version.desc()).limit(1))
    ).scalar_one_or_none()
    if plan is None:
        raise NoPlan("There is no study plan yet; run curation first.")
    stmt = (
        select(PlanItem.topic_id, TopicRevision.title)
        .join(TopicRevision, TopicRevision.id == PlanItem.topic_revision_id)
        .where(PlanItem.plan_id == plan.id, PlanItem.excluded.is_(False))
        .order_by(PlanItem.position)
    )
    rows = [(row.topic_id, row.title) for row in await session.execute(stmt)]
    return plan, rows


@dataclass(frozen=True)
class PlanRequest:
    plan_version: int
    formats: list[str]
    items: list[Requested]
    titles: dict[uuid.UUID, str]

    @property
    def new_jobs(self) -> int:
        return sum(1 for item in self.items if not item.deduplicated)


async def request_for_plan(
    session: AsyncSession, *, dry_run: bool, now: datetime | None = None
) -> PlanRequest:
    """One job per plan topic per preferred visual format, in plan order.

    With ``dry_run`` nothing is created and the result lists what would be queued, so the web
    can confirm the job count first. A real request is all-or-nothing against the budget.
    """
    plan, rows = await plan_items(session)
    formats = await plan_formats(session)
    if not formats:
        raise NothingToGenerate("The profile prefers no visual format (video or figure).")
    planned = []
    titles: dict[uuid.UUID, str] = {}
    for topic_id, title in rows:
        topic = await resolve_topic(session, topic_id)
        if topic.id in titles:  # two plan items merged into one topic
            continue
        titles[topic.id] = title
        for format in formats:
            existing = await active_job(session, format, topic.id)
            planned.append(Planned(topic.id, title, format, existing))
    if dry_run:
        items = [
            Requested(p.topic_id, p.format, p.existing, p.existing is not None) for p in planned
        ]
    else:
        items = await _enqueue_new(session, planned, now=now)
    return PlanRequest(plan.version, formats, items, titles)


def _job_item(item: Requested, title: str | None = None) -> GenerationJob:
    return GenerationJob(
        topic_id=item.topic_id,
        title=title,
        format="video" if item.format == "video" else "figure",
        job_id=item.job.id if item.job is not None else None,
        status=item.job.status if item.job is not None else None,
        deduplicated=item.deduplicated,
    )


class GenerationService:
    """HTTP-facing wrapper: runs a request, commits, and answers with the budget left."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _budget(self) -> BudgetRead:
        return budget.to_read(await budget.budget_status(self.session))

    async def for_topic(self, topic_id: uuid.UUID, formats: Sequence[str]) -> GenerateResponse:
        requested = await request_for_topic(self.session, topic_id, formats)
        await self.session.commit()
        return GenerateResponse(jobs=[_job_item(r) for r in requested], budget=await self._budget())

    async def regenerate(self, asset_id: uuid.UUID, feedback: str) -> GenerateResponse:
        requested = await regenerate(self.session, asset_id, feedback)
        await self.session.commit()
        return GenerateResponse(jobs=[_job_item(requested)], budget=await self._budget())

    async def for_plan(self, *, dry_run: bool) -> PlanGenerateResponse:
        result = await request_for_plan(self.session, dry_run=dry_run)
        if dry_run:
            await self.session.rollback()
        else:
            await self.session.commit()
        return PlanGenerateResponse(
            plan_version=result.plan_version,
            formats=[f for f in ("video", "figure") if f in result.formats],
            dry_run=dry_run,
            job_count=result.new_jobs,
            jobs=[_job_item(i, result.titles.get(i.topic_id)) for i in result.items],
            budget=await self._budget(),
        )
