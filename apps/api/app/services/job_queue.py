"""Worker-side job state transitions (design D4). The API side lives in ``app.services.jobs``.

Every transition of a running job is guarded by its lease (``status = 'running'`` and
``locked_by`` = this worker), so a worker that lost its job to the reaper cannot overwrite the
newer state. Functions flush; the caller owns the transaction.
"""

import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.schema import Job
from app.services.job_events import record_event

log = logging.getLogger(__name__)

# Transaction-level advisory lock that serializes claims across worker processes, so the
# running-count check and the claim are atomic. Rows are still taken with SKIP LOCKED so a
# claim never waits on a row another transaction (a cancel) holds.
CLAIM_LOCK_KEY = 0x6A6F6273  # "jobs"

PROVIDER_WAIT_MESSAGE = "waiting for provider limit reset"

type FinishResult = Literal["done", "cancelled", "lost"]

# Same shape as ``app.jobs.registry.ClaimGate`` (not imported: importing ``app.jobs`` loads
# every handler module, and handlers import services).
type ClaimGate = Callable[[AsyncSession], Awaitable[bool]]


def render_type_limits() -> dict[str, int]:
    """Per-type caps: each render type runs at most ``render_concurrency`` at a time."""
    return {"video": settings.render_concurrency, "figure": settings.render_concurrency}


def retry_delay(attempt: int, *, base_s: float | None = None) -> timedelta:
    """Backoff after attempt ``attempt`` (1-based) failed: 30 s, 60 s, 120 s, ..."""
    base = settings.job_retry_base_s if base_s is None else base_s
    return timedelta(seconds=base * 2 ** (attempt - 1))


def deferral_run_after(
    now: datetime,
    reset_at: datetime | None,
    rand: Callable[[], float],
    *,
    wait_s: float | None = None,
    jitter_s: float | None = None,
) -> datetime:
    """When a provider-limited job may run again.

    ``reset_at + jitter(0..60 s)`` when the provider reported a reset time (a reset already in
    the past counts from now), otherwise ``now + 5 min + jitter(0..60 s)``.
    """
    wait = settings.job_provider_wait_s if wait_s is None else wait_s
    jitter = settings.job_provider_jitter_s if jitter_s is None else jitter_s
    base = now + timedelta(seconds=wait) if reset_at is None else max(reset_at, now)
    return base + timedelta(seconds=jitter * rand())


async def _lock_claims(session: AsyncSession) -> None:
    if session.get_bind().dialect.name == "postgresql":
        await session.execute(select(func.pg_advisory_xact_lock(CLAIM_LOCK_KEY)))


async def claim_next(
    session: AsyncSession,
    worker_id: str,
    now: datetime,
    *,
    total_limit: int,
    type_limits: Mapping[str, int],
    gates: Mapping[str, ClaimGate],
) -> Job | None:
    """Claim the next due job within the concurrency limits, or return None.

    Order: higher ``priority`` first, then creation order. The instance limit and the per-type
    limits count running jobs across every worker. A type whose limit is reached, or whose claim
    gate says no, is skipped so other types can still start.
    """
    await _lock_claims(session)
    running_rows = await session.execute(
        select(Job.type, func.count()).where(Job.status == "running").group_by(Job.type)
    )
    running: dict[str, int] = {job_type: count for job_type, count in running_rows}
    if sum(running.values()) >= total_limit:
        return None
    blocked = {t for t, limit in type_limits.items() if running.get(t, 0) >= limit}
    for job_type, gate in gates.items():
        if job_type not in blocked and not await gate(session):
            blocked.add(job_type)

    stmt = select(Job).where(
        Job.status == "queued", Job.run_after <= now, Job.cancel_requested.is_(False)
    )
    if blocked:
        stmt = stmt.where(Job.type.not_in(blocked))
    stmt = (
        stmt.order_by(Job.priority.desc(), Job.created_at, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = (await session.execute(stmt)).scalar_one_or_none()
    if job is None:
        return None
    job.status = "running"
    job.locked_by = worker_id
    job.locked_at = now
    job.heartbeat_at = now
    job.started_at = now
    job.finished_at = None
    job.attempt = max(job.attempt, 1)
    job.progress = "running"
    await record_event(session, job, "status", message="running", data={"attempt": job.attempt})
    log.info("job %s (%s) claimed by %s, attempt %d", job.id, job.type, worker_id, job.attempt)
    return job


async def check_lease(
    session: AsyncSession, job_id: UUID, worker_id: str, now: datetime, *, heartbeat: bool
) -> bool | None:
    """Return the job's ``cancel_requested`` flag, or None when this worker no longer owns it.

    With ``heartbeat`` it also refreshes ``heartbeat_at``.
    """
    owned = (Job.id == job_id, Job.locked_by == worker_id, Job.status == "running")
    if heartbeat:
        result = await session.execute(
            update(Job).where(*owned).values(heartbeat_at=now).returning(Job.cancel_requested)
        )
    else:
        result = await session.execute(select(Job.cancel_requested).where(*owned))
    return result.scalar_one_or_none()


async def _owned(session: AsyncSession, job_id: UUID, worker_id: str) -> Job | None:
    stmt = select(Job).where(Job.id == job_id).with_for_update()
    job = (await session.execute(stmt)).scalar_one_or_none()
    if job is None or job.status != "running" or job.locked_by != worker_id:
        return None
    return job


def _release(job: Job) -> None:
    job.locked_by = None
    job.locked_at = None
    job.heartbeat_at = None


def _finish(job: Job, status: str, now: datetime) -> None:
    _release(job)
    job.status = status
    job.finished_at = now


async def set_progress(session: AsyncSession, job_id: UUID, worker_id: str, message: str) -> bool:
    job = await _owned(session, job_id, worker_id)
    if job is None:
        return False
    job.progress = message
    await record_event(session, job, "progress", message=message)
    return True


async def save_checkpoint(
    session: AsyncSession, job_id: UUID, worker_id: str, checkpoint: dict[str, Any]
) -> bool:
    job = await _owned(session, job_id, worker_id)
    if job is None:
        return False
    job.checkpoint = checkpoint
    return True


async def finish(
    session: AsyncSession,
    job_id: UUID,
    worker_id: str,
    now: datetime,
    *,
    status: Literal["ready", "skipped"] = "ready",
    message: str | None = None,
) -> FinishResult:
    """Record success in the handler's own session, so its writes commit with the status.

    Returns ``cancelled`` when a cancel was requested meanwhile (the caller rolls back the
    handler's writes and records the cancellation) and ``lost`` when the lease is gone.
    """
    job = await _owned(session, job_id, worker_id)
    if job is None:
        return "lost"
    if job.cancel_requested:
        return "cancelled"
    _finish(job, status, now)
    job.progress = message or status
    job.error = None
    job.error_reason = None
    await record_event(session, job, "status", message=job.progress)
    log.info("job %s %s", job.id, status)
    return "done"


async def fail(
    session: AsyncSession, job_id: UUID, worker_id: str, now: datetime, *, error: str, reason: str
) -> Job | None:
    """Permanent failure: ``failed`` with ``reason``, no further attempts."""
    job = await _owned(session, job_id, worker_id)
    if job is None:
        return None
    _finish(job, "failed", now)
    job.error = error
    job.error_reason = reason
    job.progress = f"failed: {reason}"
    await record_event(session, job, "status", message=error, data={"reason": reason})
    log.warning("job %s failed permanently (%s): %s", job.id, reason, error)
    return job


async def fail_or_retry(
    session: AsyncSession, job_id: UUID, worker_id: str, now: datetime, *, error: str
) -> Job | None:
    """Transient failure: re-queue with backoff and the next attempt, or fail when exhausted."""
    job = await _owned(session, job_id, worker_id)
    if job is None:
        return None
    job.error = error
    if job.attempt >= job.max_attempts:
        _finish(job, "failed", now)
        job.error_reason = "retries-exhausted"
        job.progress = f"failed after {job.attempt} attempts"
        await record_event(
            session, job, "status", message=error, data={"reason": "retries-exhausted"}
        )
        log.warning("job %s failed after %d attempts: %s", job.id, job.attempt, error)
        return job
    delay = retry_delay(job.attempt)
    _release(job)
    job.status = "queued"
    job.run_after = now + delay
    job.attempt += 1
    job.progress = f"retrying in {int(delay.total_seconds())} s after an error"
    await record_event(
        session,
        job,
        "status",
        message=job.progress,
        data={"error": error, "attempt": job.attempt, "run_after": job.run_after.isoformat()},
    )
    log.info("job %s retry %d at %s: %s", job.id, job.attempt, job.run_after, error)
    return job


async def defer(
    session: AsyncSession,
    job_id: UUID,
    worker_id: str,
    *,
    run_after: datetime,
    checkpoint: dict[str, Any],
) -> Job | None:
    """Provider usage limit: back to ``queued`` with the checkpoint, attempt count unchanged."""
    job = await _owned(session, job_id, worker_id)
    if job is None:
        return None
    _release(job)
    job.status = "queued"
    job.run_after = run_after
    job.checkpoint = checkpoint
    job.progress = PROVIDER_WAIT_MESSAGE
    await record_event(
        session, job, "status", message=job.progress, data={"run_after": run_after.isoformat()}
    )
    log.info("job %s deferred until %s (provider limit)", job.id, run_after)
    return job


async def mark_cancelled(
    session: AsyncSession, job_id: UUID, worker_id: str, now: datetime
) -> Job | None:
    job = await _owned(session, job_id, worker_id)
    if job is None:
        return None
    _finish(job, "cancelled", now)
    job.error_reason = "cancelled"
    job.progress = "cancelled"
    await record_event(session, job, "status", message="cancelled")
    log.info("job %s cancelled", job.id)
    return job


async def requeue(
    session: AsyncSession, job_id: UUID, worker_id: str, now: datetime, *, message: str
) -> Job | None:
    """Hand a running job back to the queue untouched (the worker is stopping)."""
    job = await _owned(session, job_id, worker_id)
    if job is None:
        return None
    _release(job)
    job.status = "queued"
    job.run_after = now
    job.progress = message
    await record_event(session, job, "status", message=message)
    log.info("job %s requeued: %s", job.id, message)
    return job


async def reap_stale(
    session: AsyncSession, now: datetime, *, stale_after: timedelta | None = None
) -> list[Job]:
    """Requeue running jobs whose worker stopped heartbeating.

    A lost run counts as an attempt, so a job that keeps killing its worker ends ``failed``
    with reason ``worker-lost`` instead of looping; a job with a pending cancel ends cancelled.
    """
    cutoff = now - (stale_after or timedelta(seconds=settings.job_stale_after_s))
    stmt = (
        select(Job)
        .where(
            Job.status == "running",
            or_(
                Job.heartbeat_at < cutoff,
                Job.heartbeat_at.is_(None) & or_(Job.locked_at.is_(None), Job.locked_at < cutoff),
            ),
        )
        .with_for_update(skip_locked=True)
    )
    reaped = list((await session.execute(stmt)).scalars())
    for job in reaped:
        lost_by = job.locked_by
        if job.cancel_requested:
            _finish(job, "cancelled", now)
            job.error_reason = "cancelled"
            job.progress = "cancelled"
        elif job.attempt >= job.max_attempts:
            _finish(job, "failed", now)
            job.error = f"worker {lost_by} stopped heartbeating on the last attempt"
            job.error_reason = "worker-lost"
            job.progress = "failed: worker-lost"
        else:
            _release(job)
            job.status = "queued"
            job.run_after = now
            job.attempt += 1
            job.progress = "requeued after its worker stopped heartbeating"
        await record_event(session, job, "status", message=job.progress, data={"worker": lost_by})
        log.warning("reaped job %s from %s: now %s", job.id, lost_by, job.status)
    return reaped
