import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.schema import Base, InstanceSettings, Job, JobEvent
from app.jobs.errors import JobSkipped, PermanentJobError, ProviderLimitReached
from app.jobs.registry import HANDLERS, Handler, JobContext
from app.jobs.runner import ActiveJob, Runner
from app.services import jobs
from app.services.jobs import utcnow

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


class Clock:
    def __init__(self) -> None:
        self.now = utcnow()

    def __call__(self) -> datetime:
        return self.now

    def advance(self, until: datetime) -> None:
        self.now = jobs.as_utc(until) + timedelta(seconds=1)


async def run_attempts(
    factory: Factory, tmp_path: Path, handler: Handler, runs: int, *, rand: float = 0.5
) -> tuple[Job, list[tuple[datetime, Job]]]:
    """Run the job ``runs`` times, advancing the clock past run-after between runs.

    Returns the final job and, per run, the runner's clock at that run and the job after it.
    """
    job_id = await enqueue(factory)
    clock = Clock()
    runner = Runner(
        factory,
        worker_id="w1",
        handlers={"t": handler},
        gates={},
        clock=clock,
        rand=lambda: rand,
        jobs_dir=tmp_path,
    )
    history: list[tuple[datetime, Job]] = []
    for _ in range(runs):
        ran_at = clock.now
        await run_all(await runner.tick())
        job = await load(factory, job_id)
        history.append((ran_at, job))
        clock.advance(job.run_after)
    return history[-1][1], history


async def test_transient_failure_retries_then_fails_after_three_attempts(
    session_factory: Factory, tmp_path: Path
) -> None:
    job, history = await run_attempts(
        session_factory, tmp_path, handler_raising(TimeoutError("provider call timed out")), 3
    )
    assert [(j.status, j.attempt) for _, j in history] == [
        ("queued", 2),
        ("queued", 3),
        ("failed", 3),
    ]
    backoff = [(jobs.as_utc(j.run_after) - at).total_seconds() for at, j in history[:2]]
    assert backoff == [30, 60]
    assert (job.error_reason, job.error) == (
        "retries-exhausted",
        "TimeoutError: provider call timed out",
    )


async def test_permanent_error_is_not_retried(session_factory: Factory, tmp_path: Path) -> None:
    error = PermanentJobError("render budget exhausted", reason="budget-exhausted")
    job = await run_one(session_factory, tmp_path, handler_raising(error))
    assert (job.status, job.error_reason, job.attempt) == ("failed", "budget-exhausted", 1)


async def test_provider_limit_defers_without_spending_an_attempt_and_resumes(
    session_factory: Factory, tmp_path: Path
) -> None:
    reset = utcnow() + timedelta(minutes=20)
    seen: list[dict[str, Any]] = []

    async def handler(ctx: JobContext) -> None:
        seen.append(dict(ctx.checkpoint))
        if not ctx.checkpoint:
            await ctx.save_checkpoint({"storyboard": "done"})
            raise ProviderLimitReached(reset_at=reset)
        await ctx.save_checkpoint({**ctx.checkpoint, "script": "done"})

    job, history = await run_attempts(session_factory, tmp_path, handler, 2, rand=0.5)
    deferred = history[0][1]
    assert (deferred.status, deferred.attempt) == ("queued", 1)
    assert deferred.progress == "waiting for provider limit reset"
    assert reset <= jobs.as_utc(deferred.run_after) <= reset + timedelta(seconds=60)
    assert deferred.checkpoint == {"storyboard": "done"}
    assert seen == [{}, {"storyboard": "done"}]
    assert (job.status, job.attempt) == ("ready", 1)
    assert job.checkpoint == {"storyboard": "done", "script": "done"}


async def test_three_consecutive_deferrals_do_not_fail_the_job(
    session_factory: Factory, tmp_path: Path
) -> None:
    resets: list[datetime] = []

    async def handler(ctx: JobContext) -> None:
        resets.append(utcnow() + timedelta(minutes=5 * (len(resets) + 1)))
        raise ProviderLimitReached(reset_at=resets[-1])

    job, history = await run_attempts(session_factory, tmp_path, handler, 3, rand=0.0)
    assert [(j.status, j.attempt) for _, j in history] == [("queued", 1)] * 3
    assert jobs.as_utc(job.run_after) >= resets[-1]
    assert job.error_reason is None


async def test_provider_limit_without_reset_waits_five_to_six_minutes(
    session_factory: Factory, tmp_path: Path
) -> None:
    job_id = await enqueue(session_factory)
    clock = Clock()
    runner = Runner(
        session_factory,
        worker_id="w1",
        handlers={"t": handler_raising(ProviderLimitReached())},
        gates={},
        clock=clock,
        rand=lambda: 1.0,
        jobs_dir=tmp_path,
    )
    await run_all(await runner.tick())
    job = await load(session_factory, job_id)
    wait = jobs.as_utc(job.run_after) - clock.now
    assert timedelta(minutes=5) <= wait <= timedelta(minutes=6)


async def start_and_wait(
    factory: Factory, tmp_path: Path, handler: Handler, ready: asyncio.Event
) -> tuple[UUID, list[ActiveJob]]:
    job_id = await enqueue(factory)
    runner = make_runner(factory, tmp_path, {"t": handler})
    started = await runner.tick()
    await asyncio.wait_for(ready.wait(), 5)
    return job_id, started


async def request_cancel(factory: Factory, job_id: UUID) -> None:
    async with factory() as session:
        await jobs.cancel(session, job_id)
        await session.commit()


async def test_cancel_running_job_kills_sandbox_and_discards_outputs(
    session_factory: Factory, tmp_path: Path
) -> None:
    ready = asyncio.Event()
    killed = asyncio.Event()

    async def handler(ctx: JobContext) -> None:
        out = tmp_path / str(ctx.job_id) / "out"
        out.mkdir(parents=True)
        (out / "partial.mp4").write_bytes(b"partial")

        async def kill() -> None:
            killed.set()

        ctx.register_kill_hook(kill)
        ready.set()
        await killed.wait()  # the "sandbox" exits once killed
        ctx.raise_if_cancelled()

    job_id, started = await start_and_wait(session_factory, tmp_path, handler, ready)
    await request_cancel(session_factory, job_id)
    await asyncio.wait_for(run_all(started), 5)
    job = await load(session_factory, job_id)
    assert (job.status, job.error_reason, job.locked_by) == ("cancelled", "cancelled", None)
    assert killed.is_set()
    assert not (tmp_path / str(job_id)).exists()


async def test_cancel_stops_a_handler_that_ignores_the_flag(
    session_factory: Factory, tmp_path: Path
) -> None:
    ready = asyncio.Event()

    async def handler(ctx: JobContext) -> None:
        ready.set()
        await asyncio.sleep(60)

    job_id, started = await start_and_wait(session_factory, tmp_path, handler, ready)
    await request_cancel(session_factory, job_id)
    await asyncio.wait_for(run_all(started), 5)
    assert (await load(session_factory, job_id)).status == "cancelled"


async def test_cancel_after_handler_finished_discards_its_writes(
    session_factory: Factory, tmp_path: Path
) -> None:
    """The cancel lands between the handler's last step and the ready status: nothing is
    attached and the job ends cancelled."""

    async def handler(ctx: JobContext) -> None:
        ctx.session.add(InstanceSettings(id=1, render_minutes_month=1))
        await request_cancel(session_factory, ctx.job_id)

    job = await run_one(session_factory, tmp_path, handler)
    assert job.status == "cancelled"
    async with session_factory() as session:
        assert await session.get(InstanceSettings, 1) is None


async def crash(started: list[ActiveJob]) -> None:
    """Kill a worker the hard way: the job task dies first, so nothing is written back."""
    for active in started:
        for task in (active.task, active.handler_task):
            assert task is not None
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_worker_restart_requeues_a_running_job(
    session_factory: Factory, tmp_path: Path
) -> None:
    ready = asyncio.Event()

    async def hangs(ctx: JobContext) -> None:
        ready.set()
        await asyncio.sleep(60)

    async def completes(ctx: JobContext) -> None:
        await ctx.progress("second worker done")

    job_id, started = await start_and_wait(session_factory, tmp_path, hangs, ready)
    await crash(started)
    assert (await load(session_factory, job_id)).status == "running"

    clock = Clock()
    second = Runner(
        session_factory,
        worker_id="w2",
        handlers={"t": completes},
        gates={},
        clock=clock,
        jobs_dir=tmp_path,
    )
    clock.now += timedelta(seconds=settings.job_stale_after_s - 5)
    assert await second.reap() == 0
    clock.now += timedelta(seconds=10)
    await run_all(await second.tick())
    job = await load(session_factory, job_id)
    assert (job.status, job.attempt, job.progress) == ("ready", 2, "ready")


async def test_render_limit_holds_while_a_non_render_job_starts(
    session_factory: Factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "job_concurrency", 3)
    release = asyncio.Event()

    async def handler(ctx: JobContext) -> None:
        await release.wait()

    first_video = await enqueue(session_factory, "video")
    second_video = await enqueue(session_factory, "video")
    ingest = await enqueue(session_factory, "ingest")
    runner = make_runner(session_factory, tmp_path, {"video": handler, "ingest": handler})
    started = await runner.tick()
    assert sorted(a.job.id for a in started) == sorted([first_video, ingest])
    assert (await load(session_factory, second_video)).status == "queued"
    release.set()
    await run_all(started)
    await run_all(await runner.tick())
    assert (await load(session_factory, second_video)).status == "ready"
