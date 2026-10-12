"""Job service: the only way other sections create, read, and cancel jobs.

Module functions flush and leave the commit to the caller, so a section can create its own rows
and enqueue the job that processes them in one transaction. ``JobService`` wraps them for the
HTTP layer and commits.
"""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound
from app.db.schema import Job, JobEvent
from app.services.job_events import record_event

log = logging.getLogger(__name__)

ACTIVE_STATUSES = ("queued", "running")
TERMINAL_STATUSES = ("ready", "failed", "cancelled", "skipped")


class JobNotFound(NotFound):
    code = "job-not-found"


class JobFinished(Conflict):
    code = "job-finished"


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes; every timestamp in this schema is UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _active_by_key(session: AsyncSession, dedupe_key: str) -> Job | None:
    stmt = select(Job).where(Job.dedupe_key == dedupe_key, Job.status.in_(ACTIVE_STATUSES))
    return (await session.execute(stmt)).scalar_one_or_none()


async def enqueue(
    session: AsyncSession,
    type: str,
    payload: dict[str, Any],
    *,
    dedupe_key: str | None = None,
    priority: int = 0,
    run_after: datetime | None = None,
    now: datetime | None = None,
) -> Job:
    """Queue a job, or return the queued or running job that already holds ``dedupe_key``.

    Higher ``priority`` runs first; equal priorities run in creation order. The caller commits;
    the worker sees the job only after that commit.
    """
    if dedupe_key is not None and (existing := await _active_by_key(session, dedupe_key)):
        return existing
    now = now or utcnow()
    job = Job(
        type=type,
        payload=payload,
        dedupe_key=dedupe_key,
        priority=priority,
        status="queued",
        run_after=run_after or now,
        created_at=now,
    )
    try:
        async with session.begin_nested():
            session.add(job)
    except IntegrityError:
        # A concurrent enqueue took the key between the lookup and the insert.
        if dedupe_key is not None and (existing := await _active_by_key(session, dedupe_key)):
            return existing
        raise
    await record_event(session, job, "status", message="queued")
    log.info("job %s (%s) queued", job.id, type)
    return job


async def get_job(session: AsyncSession, job_id: UUID) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise JobNotFound(f"Job {job_id} does not exist.")
    return job


async def list_jobs(
    session: AsyncSession,
    *,
    statuses: Sequence[str] | None = None,
    type: str | None = None,
    limit: int = 50,
) -> list[Job]:
    """Newest first."""
    stmt = select(Job).order_by(Job.created_at.desc(), Job.id).limit(limit)
    if statuses:
        stmt = stmt.where(Job.status.in_(statuses))
    if type is not None:
        stmt = stmt.where(Job.type == type)
    return list((await session.execute(stmt)).scalars())


async def list_events(session: AsyncSession, job_id: UUID, *, limit: int = 100) -> list[JobEvent]:
    """The job's most recent events, oldest first."""
    stmt = (
        select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id.desc()).limit(limit)
    )
    return list(reversed((await session.execute(stmt)).scalars().all()))


async def cancel(session: AsyncSession, job_id: UUID, *, now: datetime | None = None) -> Job:
    """Cancel a queued or running job (flushed, not committed).

    A queued job becomes ``cancelled`` at once and is never started. A running job gets
    ``cancel_requested``; the worker kills its sandbox, discards its outputs, and records
    ``cancelled``. Cancelling a cancelled job is a no-op; any other finished job is a conflict.
    """
    stmt = select(Job).where(Job.id == job_id).with_for_update()
    job = (await session.execute(stmt)).scalar_one_or_none()
    if job is None:
        raise JobNotFound(f"Job {job_id} does not exist.")
    if job.status == "cancelled" or (job.status == "running" and job.cancel_requested):
        return job
    if job.status in TERMINAL_STATUSES:
        raise JobFinished(f"Job {job_id} already finished with status {job.status}.")
    previous = job.status
    job.cancel_requested = True
    if previous == "queued":
        job.status = "cancelled"
        job.error_reason = "cancelled"
        job.progress = "cancelled"
        job.finished_at = now or utcnow()
        await record_event(session, job, "status", message="cancelled")
    else:
        job.progress = "cancel requested"
        await record_event(session, job, "progress", message="cancel requested")
    log.info("job %s cancel requested (was %s)", job.id, previous)
    return job


class JobService:
    """HTTP-facing wrapper: reads and the cancel action, committing its own changes."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_jobs(
        self, *, statuses: Sequence[str] | None = None, type: str | None = None, limit: int = 50
    ) -> list[Job]:
        return await list_jobs(self.session, statuses=statuses, type=type, limit=limit)

    async def get_job(self, job_id: UUID) -> tuple[Job, list[JobEvent]]:
        job = await get_job(self.session, job_id)
        return job, await list_events(self.session, job_id)

    async def cancel(self, job_id: UUID) -> Job:
        job = await cancel(self.session, job_id)
        await self.session.commit()
        await self.session.refresh(job)  # updated_at is server-generated
        return job
