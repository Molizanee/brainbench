"""The worker's job loop (design D4): claim, run, heartbeat, reap, and record outcomes.

One ``Runner`` per worker process. Each claimed job runs as its own task with a monitor that
heartbeats every ``job_heartbeat_s`` and polls the cancel flag every ``job_cancel_poll_s``.
The handler gets a session of its own: its writes commit together with the ``ready`` status and
are rolled back on failure, deferral, or cancellation. Progress and checkpoints are written in
separate short transactions so they survive a failed attempt.
"""

import asyncio
import logging
import random
import shutil
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.jobs.errors import JobSkipped, PermanentJobError, ProviderLimitReached
from app.jobs.registry import CLAIM_GATES, HANDLERS, ClaimGate, Handler, JobContext
from app.services import job_queue
from app.services.jobs import utcnow

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClaimedJob:
    id: UUID
    type: str
    payload: dict[str, Any]
    checkpoint: dict[str, Any]
    attempt: int


@dataclass
class ActiveJob:
    job: ClaimedJob
    ctx: JobContext | None = None
    handler_task: asyncio.Task[None] | None = None
    task: asyncio.Task[None] | None = None
    cancel_requested: bool = False
    cancel_seen_at: float | None = None
    lease_lost: bool = False
    stopping: bool = False

    def interrupted(self) -> bool:
        return self.cancel_requested or self.lease_lost or self.stopping


@dataclass
class Runner:
    session_factory: async_sessionmaker[AsyncSession]
    worker_id: str
    handlers: Mapping[str, Handler] = field(default_factory=lambda: HANDLERS)
    gates: Mapping[str, ClaimGate] = field(default_factory=lambda: CLAIM_GATES)
    clock: Callable[[], datetime] = utcnow
    rand: Callable[[], float] = random.random
    jobs_dir: Path = field(default_factory=lambda: settings.jobs_dir)
    active: dict[UUID, ActiveJob] = field(default_factory=dict)
    _last_reap: float | None = None

    async def run(self, stop: asyncio.Event) -> None:
        """Claim and run jobs until ``stop`` is set, then hand running jobs back to the queue."""
        log.info("job runner %s started (concurrency %d)", self.worker_id, settings.job_concurrency)
        try:
            while not stop.is_set():
                try:
                    await self.tick()
                except Exception:
                    log.exception("job loop iteration failed; retrying")
                await self._wait(stop)
        finally:
            await self.shutdown()

    async def tick(self) -> list[ActiveJob]:
        """Reap stale jobs when due, then claim jobs while this worker has free slots."""
        if self._last_reap is None or (
            time.monotonic() - self._last_reap >= settings.job_reaper_interval_s
        ):
            self._last_reap = time.monotonic()
            await self.reap()
        started: list[ActiveJob] = []
        while len(self.active) < settings.job_concurrency:
            claimed = await self.claim()
            if claimed is None:
                break
            started.append(self.start(claimed))
        return started

    async def reap(self) -> int:
        async with self.session_factory() as session:
            reaped = await job_queue.reap_stale(session, self.clock())
            await session.commit()
        return len(reaped)

    async def claim(self) -> ClaimedJob | None:
        async with self.session_factory() as session:
            job = await job_queue.claim_next(
                session,
                self.worker_id,
                self.clock(),
                total_limit=settings.job_concurrency,
                type_limits=job_queue.render_type_limits(),
                gates=self.gates,
            )
            if job is None:
                await session.rollback()
                return None
            claimed = ClaimedJob(
                job.id, job.type, dict(job.payload), dict(job.checkpoint), job.attempt
            )
            await session.commit()
        return claimed

    def start(self, claimed: ClaimedJob) -> ActiveJob:
        active = ActiveJob(claimed)
        self.active[claimed.id] = active
        active.task = asyncio.create_task(self.execute(active), name=f"job-{claimed.id}")
        active.task.add_done_callback(lambda _: self.active.pop(claimed.id, None))
        return active

    async def shutdown(self) -> None:
        """Kill sandboxes, stop handlers, and requeue their jobs with the attempt unchanged."""
        tasks = [a.task for a in self.active.values() if a.task is not None]
        for active in list(self.active.values()):
            active.stopping = True
            await self._interrupt(active)
            if active.handler_task is not None:
                active.handler_task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _wait(self, stop: asyncio.Event) -> None:
        stop_waiter = asyncio.ensure_future(stop.wait())
        waiters: set[asyncio.Future[Any]] = {stop_waiter}
        waiters.update(a.task for a in self.active.values() if a.task is not None)
        try:
            await asyncio.wait(
                waiters, timeout=settings.worker_idle_sleep_s, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            stop_waiter.cancel()

    # One job ---------------------------------------------------------------------------

    async def execute(self, active: ActiveJob) -> None:
        job = active.job
        handler = self.handlers.get(job.type)
        if handler is None:
            message = f"no handler registered for job type {job.type!r}"
            await self._transition(
                lambda s: job_queue.fail(
                    s, job.id, self.worker_id, self.clock(), error=message, reason="unknown-type"
                )
            )
            return
        async with self.session_factory() as session:
            ctx = JobContext(
                job_id=job.id,
                type=job.type,
                payload=job.payload,
                checkpoint=job.checkpoint,
                session=session,
                save_checkpoint_fn=lambda cp: self._save_checkpoint(job.id, cp),
                progress_fn=lambda message: self._progress(job.id, message),
                is_cancelled=active.interrupted,
            )
            active.ctx = ctx
            active.handler_task = asyncio.create_task(_call(handler, ctx), name=f"handler-{job.id}")
            monitor = asyncio.create_task(self._monitor(active), name=f"monitor-{job.id}")
            try:
                # asyncio.wait does not forward our own cancellation into the handler, so a
                # cancelled handler (cancel, lost lease, shutdown) and this task being torn down
                # stay distinguishable. Teardown records nothing; the reaper takes over.
                await asyncio.wait({active.handler_task})
            except asyncio.CancelledError:
                active.handler_task.cancel()
                raise
            finally:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)
            error: BaseException | None = (
                asyncio.CancelledError("handler cancelled")
                if active.handler_task.cancelled()
                else active.handler_task.exception()
            )

            # A handler that returns after noticing an interruption has not finished its work.
            if (error is None or isinstance(error, JobSkipped)) and not active.interrupted():
                error = await self._finish(session, active, error)
                if error is None:
                    return
            await session.rollback()
            await self._record_failure(active, ctx, error)

    async def _finish(
        self, session: AsyncSession, active: ActiveJob, skipped: JobSkipped | None
    ) -> BaseException | None:
        """Commit the handler's writes with the final status. Returns an error to record instead."""
        try:
            result = await job_queue.finish(
                session,
                active.job.id,
                self.worker_id,
                self.clock(),
                status="skipped" if skipped else "ready",
                message=skipped.message if skipped else None,
            )
            if result == "done":
                await session.commit()
                return None
        except Exception as exc:
            log.exception("job %s: recording success failed", active.job.id)
            return exc
        if result == "cancelled":
            active.cancel_requested = True
        else:
            active.lease_lost = True
        return asyncio.CancelledError(result)

    async def _record_failure(
        self, active: ActiveJob, ctx: JobContext, error: BaseException | None
    ) -> None:
        job, worker = active.job, self.worker_id
        now = self.clock()
        if active.lease_lost:
            log.warning("job %s: lease lost; leaving its state to the new owner", job.id)
        elif active.cancel_requested:
            await self._transition(lambda s: job_queue.mark_cancelled(s, job.id, worker, now))
            await asyncio.to_thread(shutil.rmtree, self.jobs_dir / str(job.id), True)
        elif active.stopping:
            message = "requeued: worker stopped"
            await self._transition(
                lambda s: job_queue.requeue(s, job.id, worker, now, message=message)
            )
        elif isinstance(error, ProviderLimitReached):
            run_after = job_queue.deferral_run_after(now, error.reset_at, self.rand)
            checkpoint = dict(ctx.checkpoint)
            await self._transition(
                lambda s: job_queue.defer(
                    s, job.id, worker, run_after=run_after, checkpoint=checkpoint
                )
            )
        elif isinstance(error, PermanentJobError):
            message, reason = str(error), error.reason
            await self._transition(
                lambda s: job_queue.fail(s, job.id, worker, now, error=message, reason=reason)
            )
        else:
            log.warning("job %s attempt %d failed", job.id, job.attempt, exc_info=error)
            message = _describe(error)
            await self._transition(
                lambda s: job_queue.fail_or_retry(s, job.id, worker, now, error=message)
            )

    async def _transition(self, apply: Callable[[AsyncSession], Awaitable[object]]) -> None:
        """Apply a lease-guarded state change in its own short transaction."""
        async with self.session_factory() as session:
            await apply(session)
            await session.commit()

    async def _progress(self, job_id: UUID, message: str) -> None:
        await self._transition(lambda s: job_queue.set_progress(s, job_id, self.worker_id, message))

    async def _save_checkpoint(self, job_id: UUID, checkpoint: dict[str, Any]) -> None:
        await self._transition(
            lambda s: job_queue.save_checkpoint(s, job_id, self.worker_id, checkpoint)
        )

    async def _monitor(self, active: ActiveJob) -> None:
        """Heartbeat, watch the cancel flag, and stop the handler on cancel or a lost lease."""
        last_beat = time.monotonic()
        while True:
            await asyncio.sleep(settings.job_cancel_poll_s)
            beat = time.monotonic() - last_beat >= settings.job_heartbeat_s
            try:
                async with self.session_factory() as session:
                    cancel = await job_queue.check_lease(
                        session, active.job.id, self.worker_id, self.clock(), heartbeat=beat
                    )
                    await session.commit()
            except Exception:
                log.warning("job %s: heartbeat failed; retrying", active.job.id, exc_info=True)
                continue
            if beat:
                last_beat = time.monotonic()
            if cancel is None:
                active.lease_lost = True
                await self._interrupt(active)
                if active.handler_task is not None:
                    active.handler_task.cancel()
                return
            if cancel and active.cancel_seen_at is None:
                log.info("job %s: cancel requested; stopping", active.job.id)
                active.cancel_requested = True
                active.cancel_seen_at = time.monotonic()
                await self._interrupt(active)
            if (
                active.cancel_seen_at is not None
                and time.monotonic() - active.cancel_seen_at >= settings.job_cancel_grace_s
                and active.handler_task is not None
            ):
                active.handler_task.cancel()

    async def _interrupt(self, active: ActiveJob) -> None:
        if active.ctx is not None:
            await active.ctx.run_kill_hooks()


async def _call(handler: Handler, ctx: JobContext) -> None:
    await handler(ctx)


def _describe(error: BaseException | None) -> str:
    if error is None:
        return "unknown error"
    text = str(error)
    return f"{type(error).__name__}: {text}" if text else type(error).__name__
