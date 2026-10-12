"""The video job against the real ``brainbench/manim`` sandbox (scripted model, SQLite)."""

import asyncio
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.support.sqlite import file_session_factory
from tests.visual_seed import SCRIPT, job_context, seed_topic
from tests.visual_seed import storyboard as board

from app.core.config import settings
from app.db.schema import Asset, Job
from app.jobs.registry import HANDLERS
from app.jobs.runner import Runner
from app.services import jobs, storyboard
from app.services.sandbox import SandboxRunner
from app.services.storyboard import VisualDeps
from app.services.video import run_video
from brainbench_agents.visual import ChunkRef
from brainbench_agents.visual.testing import ScriptedModel, call

pytestmark = pytest.mark.integration

SLEEPING_SCRIPT = """\
import time

from manim import *


class Lesson(Scene):
    def construct(self) -> None:
        self.next_section(name="intro")
        time.sleep(120)
        self.next_section(name="vote")
"""


@pytest.fixture(autouse=True)
def data(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "brainbench_data", data_dir)
    return data_dir


def models_for(chunk_ids: list[str], script: str) -> Any:
    models = {
        "storyboard": ScriptedModel([[call("Storyboard", board(chunk_ids))]]),
        "manim_script": ScriptedModel([[call("ManimScript", {"script": script})]]),
    }

    async def model(session: AsyncSession, step: str) -> ScriptedModel:
        return models[step]

    return model


def no_search(session: AsyncSession, topic_id: uuid.UUID) -> Any:
    async def search(query: str, k: int) -> list[ChunkRef]:
        return []

    return search


async def test_fixture_script_renders_mp4_with_vtt_captions(
    session: AsyncSession, manim_image: str, data: Path
) -> None:
    seeded = await seed_topic(session)
    deps = VisualDeps(
        model=models_for(seeded.chunk_ids, SCRIPT),
        search=no_search,
        sandbox=SandboxRunner(data_dir=data, host_data_dir=data),
        retry_strategy=None,
    )
    ctx, _ = await job_context(session, "video", {"topic_id": str(seeded.topic_id)})
    await run_video(ctx, deps)
    await session.commit()

    asset = (await session.execute(select(Asset))).scalar_one()
    video = data / asset.files["video"]
    assert video.stat().st_size > 1000 and video.read_bytes()[4:8] == b"ftyp"
    assert asset.duration_s is not None and 0.8 < asset.duration_s < 3
    captions = (data / asset.files["captions"]).read_text()
    cues = re.findall(r"(\d\d:\d\d:\d\d\.\d{3}) --> (\d\d:\d\d:\d\d\.\d{3})\n(.+)", captions)
    assert captions.startswith("WEBVTT")
    assert [text for _, _, text in cues] == ["Narration for intro.", "Narration for vote."]
    assert cues[0][0] == "00:00:00.000" and cues[0][1] == cues[1][0]
    sections = asset.provenance["sections"]
    assert [s["name"] for s in sections] == ["intro", "vote"]
    assert all(0.3 < s["duration_s"] < 1.5 for s in sections)
    assert not (data / "jobs" / str(ctx.job_id)).exists()


@pytest.fixture
async def factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async for factory in file_session_factory(tmp_path / "v.db"):
        yield factory


async def test_render_over_the_time_limit_fails_with_reason_timeout(
    factory: async_sessionmaker[AsyncSession],
    manim_image: str,
    data: Path,
    docker_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "render_timeout_s", 8.0)
    async with factory() as session:
        seeded = await seed_topic(session)
    real = storyboard.VisualDeps
    monkeypatch.setattr(
        storyboard,
        "VisualDeps",
        lambda: real(
            model=models_for(seeded.chunk_ids, SLEEPING_SCRIPT),
            search=no_search,
            sandbox=SandboxRunner(data_dir=data, host_data_dir=data),
            retry_strategy=None,
        ),
    )
    async with factory() as session:
        job = await jobs.enqueue(session, "video", {"topic_id": str(seeded.topic_id)})
        await session.commit()
    runner = Runner(factory, worker_id="w", handlers={"video": HANDLERS["video"]}, gates={})
    started = await runner.tick()
    await asyncio.wait_for(asyncio.gather(*(a.task for a in started if a.task)), 120)

    async with factory() as session:
        finished = await session.get(Job, job.id)
    assert finished is not None
    assert finished.status == "failed" and finished.error_reason == "timeout"
    assert finished.attempt == 1
    label = f"video:{job.id}"
    assert (
        docker_client.containers.list(all=True, filters={"label": f"brainbench.sandbox={label}"})
        == []
    )
