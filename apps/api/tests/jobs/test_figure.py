import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from tests.visual_seed import FakeSandbox, result, seed_topic
from tests.visual_seed import storyboard as board

from app.core.config import settings
from app.db.schema import Asset, Base, Job
from app.db.schema import Storyboard as StoryboardRow
from app.jobs.registry import HANDLERS
from app.jobs.runner import Runner
from app.services import jobs, storyboard
from app.services.sandbox import SandboxResult
from brainbench_agents.visual import ChunkRef
from brainbench_agents.visual.testing import ScriptedModel, Turn, call, say

type Factory = async_sessionmaker[AsyncSession]

HTML = "<!doctype html><html><body><svg viewBox='0 0 4 4'></svg></body></html>"
BRIEF = {
    "applicable": True,
    "rationale": "r",
    "object": "ballot boxes",
    "true_numbers": [{"name": "servers", "value": "5"}],
    "motion": "a dial",
    "readout": "votes",
}


@pytest.fixture
async def factory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Factory]:
    data = tmp_path / "data"
    skill = data / "skills" / "anatomy"
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Anatomy")
    (skill / "scripts" / "capture.mjs").write_text("//")
    monkeypatch.setattr(settings, "brainbench_data", data)
    monkeypatch.setattr(settings, "job_heartbeat_s", 0.05)
    monkeypatch.setattr(settings, "job_cancel_poll_s", 0.05)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'f.db'}", poolclass=NullPool)

    @event.listens_for(engine.sync_engine, "connect")
    def configure(dbapi_connection: Any, _: Any) -> None:
        dbapi_connection.execute("PRAGMA journal_mode=WAL")
        dbapi_connection.execute("PRAGMA busy_timeout=5000")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def install(
    monkeypatch: pytest.MonkeyPatch,
    chunk_ids: list[str],
    session_turns: list[Turn],
    *,
    brief: dict[str, Any] = BRIEF,
    sandbox: FakeSandbox | None = None,
) -> None:
    models = {
        "storyboard": ScriptedModel([[call("Storyboard", board(chunk_ids))]]),
        "figure_brief": ScriptedModel([[call("FigureBrief", brief)]]),
        "figure": ScriptedModel(session_turns),
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
        lambda: real(
            model=model,
            search=search,
            sandbox=sandbox or FakeSandbox([]),  # type: ignore[arg-type]
            retry_strategy=None,
        ),
    )


async def run_job(factory: Factory, topic_id: uuid.UUID) -> Job:
    async with factory() as session:
        job = await jobs.enqueue(session, "figure", {"topic_id": str(topic_id)})
        await session.commit()
    runner = Runner(factory, worker_id="w", handlers={"figure": HANDLERS["figure"]}, gates={})
    started = await runner.tick()
    await asyncio.gather(*(a.task for a in started if a.task is not None))
    async with factory() as session:
        loaded = await session.get(Job, job.id)
    assert loaded is not None
    return loaded


async def test_figure_job_reaches_ready_with_asset(
    factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with factory() as session:
        seeded = await seed_topic(session)

    def capture(mounts: dict[str, Path], command: list[str]) -> SandboxResult:
        (mounts["/workspace"] / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        return result(0)

    install(
        monkeypatch,
        seeded.chunk_ids,
        [
            [call("write_file", {"path": "figure.html", "content": HTML})],
            [call("run_node", {"args": ["/skill/scripts/capture.mjs", "figure.html"]})],
            [say("done")],
        ],
        sandbox=FakeSandbox([capture]),
    )
    job = await run_job(factory, seeded.topic_id)
    assert job.status == "ready", job.error
    async with factory() as session:
        asset = (await session.execute(select(Asset))).scalar_one()
    assert asset.format == "figure" and asset.job_id == job.id


async def test_figure_job_without_figure_fails_with_transcript(
    factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with factory() as session:
        seeded = await seed_topic(session)
    install(monkeypatch, seeded.chunk_ids, [[say("Nothing to build here, sorry.")]])
    job = await run_job(factory, seeded.topic_id)
    assert job.status == "failed" and job.error_reason == "no-figure"
    assert job.attempt == 1
    assert job.error is not None
    assert "Transcript tail:" in job.error and "Nothing to build here, sorry." in job.error
    async with factory() as session:
        assert (await session.execute(select(Asset))).first() is None
        assert (await session.execute(select(StoryboardRow))).first() is None


async def test_not_applicable_figure_is_skipped_with_storyboard_kept(
    factory: Factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with factory() as session:
        seeded = await seed_topic(session)
    install(
        monkeypatch,
        seeded.chunk_ids,
        [],
        brief={"applicable": False, "rationale": "Definitions only."},
    )
    job = await run_job(factory, seeded.topic_id)
    assert job.status == "skipped"
    assert job.progress == "figure not applicable: Definitions only."
    async with factory() as session:
        row = (await session.execute(select(StoryboardRow))).scalar_one()
    assert row.figure_brief is not None and row.figure_brief["rationale"] == "Definitions only."
