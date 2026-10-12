import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.schema import Base, InstanceSettings, Job, JobEvent
from app.jobs.errors import JobSkipped
from app.jobs.registry import HANDLERS, Handler, JobContext
from app.jobs.runner import ActiveJob, Runner
from app.services import jobs

type Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[Factory]:
    """One connection per session, like Postgres: the runner's concurrent sessions must not
    share a transaction (the conftest in-memory database has a single shared connection)."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'jobs.db'}", poolclass=NullPool)

    @event.listens_for(engine.sync_engine, "connect")
    def configure(dbapi_connection: Any, _: Any) -> None:
        dbapi_connection.execute("PRAGMA journal_mode=WAL")
        dbapi_connection.execute("PRAGMA busy_timeout=5000")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
def fast_timers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "job_cancel_poll_s", 0.01)
    monkeypatch.setattr(settings, "job_heartbeat_s", 0.02)
    monkeypatch.setattr(settings, "job_cancel_grace_s", 0.05)
    monkeypatch.setattr(settings, "worker_idle_sleep_s", 0.01)


def make_runner(factory: Factory, tmp_path: Path, handlers: dict[str, Handler]) -> Runner:
    return Runner(factory, worker_id="w1", handlers=handlers, gates={}, jobs_dir=tmp_path)


async def enqueue(factory: Factory, type: str = "t", payload: dict[str, Any] | None = None) -> UUID:
    async with factory() as session:
        job = await jobs.enqueue(session, type, payload or {})
        await session.commit()
        return job.id


async def load(factory: Factory, job_id: UUID) -> Job:
    async with factory() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        return job


async def run_all(started: list[ActiveJob]) -> None:
    await asyncio.gather(*(a.task for a in started if a.task is not None))


async def run_one(factory: Factory, tmp_path: Path, handler: Handler) -> Job:
    job_id = await enqueue(factory)
    runner = make_runner(factory, tmp_path, {"t": handler})
    await run_all(await runner.tick())
    return await load(factory, job_id)


async def test_noop_job_runs_to_ready(session_factory: Factory, tmp_path: Path) -> None:
    job_id = await enqueue(session_factory, "noop")
    runner = make_runner(session_factory, tmp_path, dict(HANDLERS))
    await run_all(await runner.tick())
    job = await load(session_factory, job_id)
    assert (job.status, job.progress, job.checkpoint) == ("ready", "ready", {"done": True})
    assert job.locked_by is None and job.finished_at is not None
    async with session_factory() as session:
        events = (
            await session.execute(
                select(JobEvent.kind, JobEvent.message).where(JobEvent.job_id == job_id)
            )
        ).all()
    assert [tuple(e) for e in events] == [
        ("status", "queued"),
        ("status", "running"),
        ("progress", "noop running"),
        ("status", "ready"),
    ]


async def test_handler_writes_commit_with_ready_status(
    session_factory: Factory, tmp_path: Path
) -> None:
    async def handler(ctx: JobContext) -> None:
        ctx.session.add(InstanceSettings(id=1, render_minutes_month=42))

    job = await run_one(session_factory, tmp_path, handler)
    assert job.status == "ready"
    async with session_factory() as session:
        row = await session.get(InstanceSettings, 1)
        assert row is not None and row.render_minutes_month == 42


async def test_skipped_outcome(session_factory: Factory, tmp_path: Path) -> None:
    async def handler(ctx: JobContext) -> None:
        raise JobSkipped("no mechanism worth a figure")

    job = await run_one(session_factory, tmp_path, handler)
    assert (job.status, job.progress) == ("skipped", "no mechanism worth a figure")


async def test_unknown_type_fails_permanently(session_factory: Factory, tmp_path: Path) -> None:
    job_id = await enqueue(session_factory, "nobody-handles-this")
    runner = make_runner(session_factory, tmp_path, {})
    await run_all(await runner.tick())
    job = await load(session_factory, job_id)
    assert (job.status, job.error_reason) == ("failed", "unknown-type")


async def test_tick_respects_instance_concurrency(
    session_factory: Factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "job_concurrency", 2)
    release = asyncio.Event()

    async def handler(ctx: JobContext) -> None:
        await release.wait()

    ids = [await enqueue(session_factory) for _ in range(3)]
    runner = make_runner(session_factory, tmp_path, {"t": handler})
    started = await runner.tick()
    assert len(started) == 2
    assert (await load(session_factory, ids[2])).status == "queued"
    release.set()
    await run_all(started)
    await run_all(await runner.tick())
    assert {(await load(session_factory, i)).status for i in ids} == {"ready"}


async def test_heartbeat_advances_while_handler_runs(
    session_factory: Factory, tmp_path: Path
) -> None:
    job_id = await enqueue(session_factory)
    beats: list[Any] = []

    async def handler(ctx: JobContext) -> None:
        for _ in range(3):
            await asyncio.sleep(0.05)
            beats.append((await load(session_factory, job_id)).heartbeat_at)

    runner = make_runner(session_factory, tmp_path, {"t": handler})
    await run_all(await runner.tick())
    assert beats[0] < beats[-1]


async def test_run_stops_and_requeues_running_jobs(
    session_factory: Factory, tmp_path: Path
) -> None:
    job_id = await enqueue(session_factory)
    started = asyncio.Event()
    killed: list[UUID] = []

    async def handler(ctx: JobContext) -> None:
        async def kill() -> None:
            killed.append(ctx.job_id)

        ctx.register_kill_hook(kill)
        started.set()
        await asyncio.sleep(60)

    runner = make_runner(session_factory, tmp_path, {"t": handler})
    stop = asyncio.Event()
    loop_task = asyncio.create_task(runner.run(stop))
    await asyncio.wait_for(started.wait(), 5)
    stop.set()
    await asyncio.wait_for(loop_task, 5)
    job = await load(session_factory, job_id)
    assert (job.status, job.attempt, job.locked_by) == ("queued", 1, None)
    assert job.progress == "requeued: worker stopped"
    assert killed == [job_id]


def handler_raising(error: BaseException) -> Callable[[JobContext], Awaitable[None]]:
    async def handler(ctx: JobContext) -> None:
        raise error

    return handler
