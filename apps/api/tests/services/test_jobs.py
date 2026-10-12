from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import job_queue, jobs
from app.services.jobs import JobFinished, JobNotFound


async def claim(session: AsyncSession) -> None:
    await job_queue.claim_next(
        session, "w1", jobs.utcnow(), total_limit=2, type_limits={}, gates={}
    )


async def test_enqueue_dedupes_on_active_key(session: AsyncSession) -> None:
    first = await jobs.enqueue(session, "video", {"topic": 1}, dedupe_key="video:1")
    await session.commit()
    again = await jobs.enqueue(session, "video", {"topic": 1}, dedupe_key="video:1")
    assert again.id == first.id
    other = await jobs.enqueue(session, "video", {"topic": 2}, dedupe_key="video:2")
    assert other.id != first.id


async def test_enqueue_dedupe_returns_running_job_and_releases_key_when_finished(
    session: AsyncSession,
) -> None:
    first = await jobs.enqueue(session, "video", {}, dedupe_key="video:1")
    await session.commit()
    await claim(session)
    assert (await jobs.enqueue(session, "video", {}, dedupe_key="video:1")).id == first.id
    await job_queue.finish(session, first.id, "w1", jobs.utcnow())
    fresh = await jobs.enqueue(session, "video", {}, dedupe_key="video:1")
    assert fresh.id != first.id and fresh.status == "queued"


async def test_list_and_get(session: AsyncSession) -> None:
    a = await jobs.enqueue(session, "noop", {})
    b = await jobs.enqueue(session, "video", {})
    await session.commit()
    assert {j.id for j in await jobs.list_jobs(session)} == {a.id, b.id}
    assert [j.id for j in await jobs.list_jobs(session, type="video")] == [b.id]
    assert await jobs.list_jobs(session, statuses=["running"]) == []
    assert (await jobs.get_job(session, a.id)).id == a.id
    events = await jobs.list_events(session, a.id)
    assert [(e.kind, e.status) for e in events] == [("status", "queued")]
    with pytest.raises(JobNotFound):
        await jobs.get_job(session, uuid4())


async def test_cancel_queued_job_never_starts(session: AsyncSession) -> None:
    job = await jobs.enqueue(session, "noop", {})
    await session.commit()
    await jobs.cancel(session, job.id)
    await session.commit()
    assert (job.status, job.error_reason, job.cancel_requested) == ("cancelled", "cancelled", True)
    assert job.finished_at is not None
    assert (
        await job_queue.claim_next(
            session, "w1", jobs.utcnow(), total_limit=2, type_limits={}, gates={}
        )
        is None
    )


async def test_cancel_running_job_requests_cancel(session: AsyncSession) -> None:
    job = await jobs.enqueue(session, "noop", {})
    await session.commit()
    await claim(session)
    await jobs.cancel(session, job.id)
    assert (job.status, job.cancel_requested, job.progress) == (
        "running",
        True,
        "cancel requested",
    )
    assert await job_queue.check_lease(session, job.id, "w1", jobs.utcnow(), heartbeat=False)


async def test_cancel_is_idempotent_and_rejects_finished_jobs(session: AsyncSession) -> None:
    job = await jobs.enqueue(session, "noop", {})
    await session.commit()
    await jobs.cancel(session, job.id)
    assert (await jobs.cancel(session, job.id)).status == "cancelled"

    done = await jobs.enqueue(session, "noop", {})
    await session.commit()
    await claim(session)
    await job_queue.finish(session, done.id, "w1", jobs.utcnow())
    with pytest.raises(JobFinished):
        await jobs.cancel(session, done.id)
    with pytest.raises(JobNotFound):
        await jobs.cancel(session, uuid4())
