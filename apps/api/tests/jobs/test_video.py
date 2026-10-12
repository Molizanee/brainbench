import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.support.sqlite import file_session_factory
from tests.visual_seed import SCRIPT, FakeSandbox, manim_failure, manim_success, probe, seed_topic
from tests.visual_seed import storyboard as board

from app.core.config import settings
from app.db.schema import Asset, Job
from app.jobs.registry import HANDLERS
from app.jobs.runner import Runner
from app.services import jobs, storyboard
from brainbench_agents.visual import ChunkRef
from brainbench_agents.visual.testing import ScriptedModel, call

type Factory = async_sessionmaker[AsyncSession]


@pytest.fixture
async def factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Factory]:
    monkeypatch.setattr(settings, "brainbench_data", tmp_path / "data")
    monkeypatch.setattr(settings, "job_heartbeat_s", 0.05)
    monkeypatch.setattr(settings, "job_cancel_poll_s", 0.05)
    async for factory in file_session_factory(tmp_path / "v.db"):
        yield factory


def install_deps(
    monkeypatch: pytest.MonkeyPatch, sandbox: FakeSandbox, chunk_ids: list[str]
) -> None:
    models = {
        "storyboard": ScriptedModel([[call("Storyboard", board(chunk_ids))]]),
        "manim_script": ScriptedModel([[call("ManimScript", {"script": SCRIPT})]]),
        "script_repair": ScriptedModel([[call("ManimScript", {"script": SCRIPT})]] * 2),
    }

    async def model(session: AsyncSession, step: str) -> ScriptedModel:
        return models[step]

    def search(session: AsyncSession, topic_id: uuid.UUID) -> Any:
        async def run(query: str, k: int) -> list[ChunkRef]:
            return []

        return run

    real = storyboard.VisualDeps
    monkeypatch.setattr(
        storyboard,
        "VisualDeps",
        lambda: real(model=model, search=search, sandbox=sandbox, retry_strategy=None),  # type: ignore[arg-type]
    )


async def run_job(factory: Factory, payload: dict[str, Any]) -> Job:
    async with factory() as session:
        job = await jobs.enqueue(
            session, "video", payload, dedupe_key=f"video:{payload['topic_id']}"
        )
        await session.commit()
    runner = Runner(factory, worker_id="w", handlers={"video": HANDLERS["video"]}, gates={})
    started = await runner.tick()
    await asyncio.gather(*(a.task for a in started if a.task is not None))
    async with factory() as session:
        loaded = await session.get(Job, job.id)
        assert loaded is not None
        return loaded


async def test_video_handler_is_registered_and_reaches_ready(
    factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with factory() as session:
        seeded = await seed_topic(session)
    install_deps(monkeypatch, FakeSandbox([manim_success(), probe({})]), seeded.chunk_ids)
    job = await run_job(factory, {"topic_id": str(seeded.topic_id), "format": "video"})
    assert job.status == "ready", job.error
    async with factory() as session:
        asset = (await session.execute(select(Asset))).scalar_one()
    assert asset.job_id == job.id and asset.format == "video"


async def test_three_failed_renders_fail_the_job_without_retry(
    factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with factory() as session:
        seeded = await seed_topic(session)
    install_deps(
        monkeypatch,
        FakeSandbox([manim_failure(), manim_failure(), manim_failure()]),
        seeded.chunk_ids,
    )
    job = await run_job(factory, {"topic_id": str(seeded.topic_id), "format": "video"})
    assert job.status == "failed" and job.error_reason == "render-failed"
    assert job.attempt == 1
    assert job.error is not None and "after 3 attempts" in job.error
