from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.schema import Job, JobEvent
from app.services import job_queue, jobs

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
LIMITS = {"video": 1, "figure": 1}


async def add(
    session: AsyncSession,
    type: str = "noop",
    *,
    now: datetime = NOW,
    priority: int = 0,
    run_after: datetime | None = None,
) -> Job:
    job = await jobs.enqueue(session, type, {}, now=now, priority=priority, run_after=run_after)
    await session.commit()
    return job


async def claim(session: AsyncSession, worker: str = "w1", now: datetime = NOW) -> Job | None:
    job = await job_queue.claim_next(
        session, worker, now, total_limit=2, type_limits=LIMITS, gates={}
    )
    await session.commit()
    return job


async def test_claim_marks_running_with_lease(session: AsyncSession) -> None:
    queued = await add(session)
    job = await claim(session)
    assert job is not None and job.id == queued.id
    assert (job.status, job.locked_by, job.attempt) == ("running", "w1", 1)
    assert job.heartbeat_at is not None and job.started_at is not None
    kinds = (await session.execute(select(JobEvent.status).where(JobEvent.job_id == job.id))).all()
    assert [k for (k,) in kinds] == ["queued", "running"]


async def test_claim_prefers_priority_then_creation_order(session: AsyncSession) -> None:
    first = await add(session, now=NOW - timedelta(seconds=3))
    second = await add(session, now=NOW - timedelta(seconds=2))
    urgent = await add(session, now=NOW - timedelta(seconds=1), priority=5)
    order = [(await claim(session, now=NOW)) for _ in range(2)]
    assert [j.id for j in order if j] == [urgent.id, first.id]
    assert second.status == "queued"


async def test_claim_skips_jobs_not_yet_due(session: AsyncSession) -> None:
    await add(session, run_after=NOW + timedelta(seconds=30))
    assert await claim(session) is None
    assert await claim(session, now=NOW + timedelta(seconds=30)) is not None


async def test_instance_limit_holds_third_job(session: AsyncSession) -> None:
    for _ in range(3):
        await add(session)
    assert await claim(session) is not None
    assert await claim(session, "w2") is not None
    assert await claim(session, "w3") is None


async def test_render_limit_holds_second_render_while_other_type_starts(
    session: AsyncSession,
) -> None:
    await add(session, "video", now=NOW - timedelta(seconds=3))
    second_video = await add(session, "video", now=NOW - timedelta(seconds=2))
    other = await add(session, "noop", now=NOW - timedelta(seconds=1))
    first = await claim(session)
    assert first is not None and first.type == "video"
    nxt = await claim(session, "w2")
    assert nxt is not None and nxt.id == other.id
    await session.refresh(second_video)
    assert second_video.status == "queued"


async def test_claim_gate_keeps_type_queued(session: AsyncSession) -> None:
    await add(session, "video")
    allowed = False

    async def gate(_: AsyncSession) -> bool:
        return allowed

    def claim_gated() -> Coroutine[Any, Any, Job | None]:
        return job_queue.claim_next(
            session, "w1", NOW, total_limit=2, type_limits=LIMITS, gates={"video": gate}
        )

    assert await claim_gated() is None
    allowed = True
    assert await claim_gated() is not None


async def test_lease_heartbeat_and_cancel_flag(session: AsyncSession) -> None:
    await add(session)
    job = await claim(session)
    assert job is not None
    later = NOW + timedelta(seconds=15)
    assert await job_queue.check_lease(session, job.id, "w1", later, heartbeat=True) is False
    await session.commit()
    await session.refresh(job)
    assert jobs.as_utc(job.heartbeat_at or NOW) == later
    assert await job_queue.check_lease(session, job.id, "other", later, heartbeat=False) is None


async def test_reaper_requeues_stale_running_job(session: AsyncSession) -> None:
    await add(session)
    job = await claim(session)
    assert job is not None
    fresh = await job_queue.reap_stale(session, NOW + timedelta(seconds=119))
    assert fresh == []
    reaped = await job_queue.reap_stale(session, NOW + timedelta(seconds=121))
    await session.commit()
    assert [j.id for j in reaped] == [job.id]
    assert (job.status, job.locked_by, job.attempt) == ("queued", None, 2)
    # The old owner can no longer write.
    assert await job_queue.set_progress(session, job.id, "w1", "late") is False


async def test_reaper_fails_job_lost_on_last_attempt(session: AsyncSession) -> None:
    await add(session)
    job = await claim(session)
    assert job is not None
    job.attempt = job.max_attempts
    await session.commit()
    await job_queue.reap_stale(session, NOW + timedelta(minutes=3))
    assert (job.status, job.error_reason) == ("failed", "worker-lost")


async def test_reaper_finishes_cancel_of_dead_job(session: AsyncSession) -> None:
    await add(session)
    job = await claim(session)
    assert job is not None
    await jobs.cancel(session, job.id)
    await job_queue.reap_stale(session, NOW + timedelta(minutes=3))
    assert job.status == "cancelled"


def test_render_limits_follow_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "render_concurrency", 2)
    assert job_queue.render_type_limits() == {"video": 2, "figure": 2}


def test_retry_delay_doubles_from_30_seconds() -> None:
    assert [job_queue.retry_delay(n).total_seconds() for n in (1, 2, 3)] == [30, 60, 120]


@pytest.mark.parametrize("rand", [0.0, 0.5, 0.999])
def test_deferral_with_reset_lands_within_60_seconds_after_it(rand: float) -> None:
    reset = NOW + timedelta(minutes=17)
    run_after = job_queue.deferral_run_after(NOW, reset, lambda: rand)
    assert reset <= run_after <= reset + timedelta(seconds=60)
    assert run_after == reset + timedelta(seconds=60 * rand)


@pytest.mark.parametrize("rand", [0.0, 0.999])
def test_deferral_without_reset_waits_5_to_6_minutes(rand: float) -> None:
    run_after = job_queue.deferral_run_after(NOW, None, lambda: rand)
    assert NOW + timedelta(minutes=5) <= run_after <= NOW + timedelta(minutes=6)


def test_deferral_with_past_reset_counts_from_now() -> None:
    run_after = job_queue.deferral_run_after(NOW, NOW - timedelta(hours=1), lambda: 0.5)
    assert run_after == NOW + timedelta(seconds=30)


async def test_transient_failure_requeues_with_backoff_and_next_attempt(
    session: AsyncSession,
) -> None:
    await add(session)
    job = await claim(session)
    assert job is not None
    await job_queue.fail_or_retry(session, job.id, "w1", NOW, error="timeout")
    assert (job.status, job.attempt, job.error) == ("queued", 2, "timeout")
    assert jobs.as_utc(job.run_after) == NOW + timedelta(seconds=30)
    assert job.locked_by is None


async def test_transient_failure_on_last_attempt_fails(session: AsyncSession) -> None:
    await add(session)
    job = await claim(session)
    assert job is not None
    job.attempt = 3
    await job_queue.fail_or_retry(session, job.id, "w1", NOW, error="timeout")
    assert (job.status, job.error_reason) == ("failed", "retries-exhausted")


async def test_defer_keeps_attempt_and_saves_checkpoint(session: AsyncSession) -> None:
    await add(session)
    job = await claim(session)
    assert job is not None
    later = NOW + timedelta(minutes=10)
    await job_queue.defer(session, job.id, "w1", run_after=later, checkpoint={"step": 2})
    assert (job.status, job.attempt, job.checkpoint) == ("queued", 1, {"step": 2})
    assert job.progress == "waiting for provider limit reset"
    assert jobs.as_utc(job.run_after) == later
