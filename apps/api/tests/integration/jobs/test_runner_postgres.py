"""The runner end to end on Postgres: crash recovery and limits with real row locks."""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.db.schema import Job
from app.jobs.registry import JobContext
from app.jobs.runner import Runner
from app.services import jobs

pytestmark = pytest.mark.integration

type Factory = async_sessionmaker[AsyncSession]


async def enqueue(factory: Factory, type: str) -> Job:
    async with factory() as session:
        job = await jobs.enqueue(session, type, {})
        await session.commit()
        return job


async def status(factory: Factory, job: Job) -> tuple[str, int]:
    async with factory() as session:
        row = await session.get(Job, job.id)
        assert row is not None
        return row.status, row.attempt


async def test_job_of_a_killed_worker_is_requeued_and_finished_by_another(
    pg_factory: Factory, tmp_path: Path
) -> None:
    started = asyncio.Event()

    async def hangs(ctx: JobContext) -> None:
        started.set()
        await asyncio.sleep(60)

    async def completes(ctx: JobContext) -> None:
        await ctx.save_checkpoint({"done": True})

    job = await enqueue(pg_factory, "t")
    first = Runner(pg_factory, worker_id="w1", handlers={"t": hangs}, gates={}, jobs_dir=tmp_path)
    (active,) = await first.tick()
    await asyncio.wait_for(started.wait(), 5)
    for task in (active.task, active.handler_task):  # job task first: nothing is written back
        assert task is not None
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert await status(pg_factory, job) == ("running", 1)

    later = jobs.utcnow() + timedelta(seconds=settings.job_stale_after_s + 1)

    def clock() -> datetime:
        return later

    second = Runner(
        pg_factory,
        worker_id="w2",
        handlers={"t": completes},
        gates={},
        clock=clock,
        jobs_dir=tmp_path,
    )
    (rerun,) = await second.tick()
    assert rerun.task is not None
    await rerun.task
    assert await status(pg_factory, job) == ("ready", 2)


async def test_render_limit_with_two_workers(pg_factory: Factory, tmp_path: Path) -> None:
    release = asyncio.Event()

    async def handler(ctx: JobContext) -> None:
        await release.wait()

    videos = [await enqueue(pg_factory, "video") for _ in range(2)]
    other = await enqueue(pg_factory, "noop")
    handlers = {"video": handler, "noop": handler}
    workers = [
        Runner(pg_factory, worker_id=f"w{i}", handlers=handlers, gates={}, jobs_dir=tmp_path)
        for i in range(2)
    ]
    started = [a for runs in await asyncio.gather(*(w.tick() for w in workers)) for a in runs]
    assert sorted(a.job.type for a in started) == ["noop", "video"]
    assert sum([(await status(pg_factory, v))[0] == "queued" for v in videos]) == 1
    assert (await status(pg_factory, other))[0] == "running"
    release.set()
    await asyncio.gather(*(a.task for a in started if a.task is not None))
