import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.visual_seed import (
    SCRIPT,
    FakeSandbox,
    SeededTopic,
    job_context,
    manim_failure,
    manim_success,
    manim_timeout,
    probe,
    seed_topic,
    storyboard,
)

from app.core.config import settings
from app.db.schema import Asset, UsageCounter
from app.db.schema import Storyboard as StoryboardRow
from app.jobs.errors import PermanentJobError, ProviderLimitReached
from app.services.storyboard import VisualDeps
from app.services.video import (
    MANIM_COMMAND,
    Section,
    build_cues,
    parse_probe,
    render_webvtt,
    run_video,
    timestamp,
)
from brainbench_agents.visual import ChunkRef, Storyboard
from brainbench_agents.visual.testing import ScriptedModel, Turn, call
from brainbench_providers.select import ALL_ACCOUNTS
from brainbench_providers.strands_models import (
    ProviderRetryStrategy,
    ProviderThrottledException,
)


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "brainbench_data", data)
    return data


class Models:
    """Model factory for VisualDeps: one scripted model per agent step."""

    def __init__(self, **turns: list[Turn]) -> None:
        self.models = {step: ScriptedModel(list(t)) for step, t in turns.items()}
        self.steps: list[str] = []

    async def __call__(self, session: AsyncSession, step: str) -> ScriptedModel:
        self.steps.append(step)
        return self.models[step]


def deps(models: Models, sandbox: FakeSandbox, searches: list[str] | None = None) -> VisualDeps:
    def search_factory(session: AsyncSession, topic_id: uuid.UUID) -> Any:
        async def search(query: str, k: int) -> Sequence[ChunkRef]:
            if searches is not None:
                searches.append(query)
            return [ChunkRef("x", "Raft notes", "text")]

        return search

    return VisualDeps(model=models, search=search_factory, sandbox=sandbox, retry_strategy=None)  # type: ignore[arg-type]


def video_models(seeded: SeededTopic, scripts: Sequence[str] = (SCRIPT,)) -> Models:
    return Models(
        storyboard=[[call("Storyboard", storyboard(seeded.chunk_ids))]],
        manim_script=[[call("ManimScript", {"script": scripts[0]})]],
        script_repair=[[call("ManimScript", {"script": s})] for s in scripts[1:]],
    )


def test_cues_follow_real_section_durations() -> None:
    board = Storyboard.model_validate(storyboard(["c1"], ("intro", "vote", "outro")))
    sections = [Section("intro", "a.mp4", 2.5), Section("vote", "b.mp4", 3.25)]
    cues, missing = build_cues(board, sections)
    assert [(c.scene_id, c.start, c.end) for c in cues] == [("intro", 0, 2.5), ("vote", 2.5, 5.75)]
    assert missing == ["outro"]
    vtt = render_webvtt(cues)
    assert vtt.startswith("WEBVTT\n\n1\n00:00:00.000 --> 00:00:02.500\nNarration for intro.")
    assert "2\n00:00:02.500 --> 00:00:05.750\nNarration for vote." in vtt
    assert timestamp(3723.4567) == "01:02:03.457"
    assert parse_probe("a.mp4\t1.5\nb.mp4\t\nc.mp4\tN/A\n") == {"a.mp4": 1.5}


async def test_video_job_renders_and_stores_asset_with_captions(
    session: AsyncSession, data_dir: Path
) -> None:
    seeded = await seed_topic(session)
    models = video_models(seeded)
    models.models["storyboard"].turns.insert(0, [call("search_chunks", {"query": "majority"})])
    sandbox = FakeSandbox(
        [manim_success(), probe({"lesson.mp4": 6.0, "lesson_0000_intro.mp4": 2.5})]
    )
    searches: list[str] = []
    ctx, rec = await job_context(session, "video", {"topic_id": str(seeded.topic_id)})

    await run_video(ctx, deps(models, sandbox, searches))
    await session.commit()

    assert searches == ["majority"]
    assert models.steps == ["storyboard", "manim_script"]
    render, probe_run = sandbox.runs
    assert render["image"] == "brainbench/manim"
    assert render["command"] == list(MANIM_COMMAND)
    assert [(m.target, m.read_only) for m in render["mounts"]] == [("/work", True), ("/out", False)]
    assert render["register_kill_hook"] == ctx.register_kill_hook
    assert render["limits"].timeout_s == 900
    assert probe_run["command"][4:] == [
        "videos/scene/720p30/lesson.mp4",
        "videos/scene/720p30/sections/lesson_0000_intro.mp4",
        "videos/scene/720p30/sections/lesson_0001_vote.mp4",
    ]

    asset = (await session.execute(select(Asset))).scalar_one()
    assert asset.format == "video" and asset.is_current and asset.job_id == ctx.job_id
    assert asset.script == SCRIPT and asset.duration_s == 6.0
    assert asset.provenance["storyboard_title"] == "Raft leader election"
    assert asset.provenance["missing_sections"] == []
    video = data_dir / asset.files["video"]
    captions = (data_dir / asset.files["captions"]).read_text()
    assert video.read_bytes().startswith(b"\x00\x00\x00\x18ftyp")
    assert asset.files["video"] == f"assets/{seeded.topic_id}/{asset.id}/lesson.mp4"
    assert "00:00:00.000 --> 00:00:02.500\nNarration for intro." in captions
    assert "00:00:02.500 --> 00:00:03.500\nNarration for vote." in captions
    row = await session.get(StoryboardRow, asset.storyboard_id)
    assert row is not None and [s["id"] for s in row.scenes] == ["intro", "vote"]
    assert row.scenes[0]["chunk_ids"] == [seeded.chunk_ids[0]]

    minutes = (await session.execute(select(UsageCounter))).scalar_one()
    assert minutes.counter == "render_minutes_month" and minutes.value == pytest.approx(0.5)
    assert rec.progress == [
        "writing the storyboard",
        "writing the Manim script",
        "rendering (attempt 1 of 3)",
        "writing captions",
    ]
    assert rec.checkpoints[0].keys() == {"storyboard"}
    assert not (data_dir / "jobs" / str(ctx.job_id)).exists()


async def test_repair_loop_stops_after_three_attempts(session: AsyncSession) -> None:
    seeded = await seed_topic(session)
    models = video_models(seeded, scripts=[SCRIPT, SCRIPT, SCRIPT, SCRIPT])
    sandbox = FakeSandbox([manim_failure("Err1"), manim_failure("Err2"), manim_failure("Err3")])
    ctx, rec = await job_context(session, "video", {"topic_id": str(seeded.topic_id)})

    with pytest.raises(PermanentJobError, match=r"after 3 attempts; last error: .*Err3") as err:
        await run_video(ctx, deps(models, sandbox))

    assert err.value.reason == "render-failed"
    assert len(sandbox.runs) == 3
    assert models.steps == ["storyboard", "manim_script", "script_repair", "script_repair"]
    assert len(models.models["script_repair"].turns) == 1  # the third repair never ran
    repair_prompt = models.models["script_repair"].requests[0]["last_message"]["content"][0]
    assert "Err1" in repair_prompt["text"]
    assert rec.checkpoints[-1]["attempts"][-1]["error"].endswith("Err3")
    assert (await session.execute(select(Asset))).first() is None


async def test_repaired_script_renders(session: AsyncSession) -> None:
    seeded = await seed_topic(session)
    fixed = SCRIPT.replace("run_time=0.5", "run_time=1")
    models = video_models(seeded, scripts=[SCRIPT, fixed])
    sandbox = FakeSandbox([manim_failure(), manim_success(), probe({})])
    ctx, _ = await job_context(session, "video", {"topic_id": str(seeded.topic_id)})
    await run_video(ctx, deps(models, sandbox))
    asset = (await session.execute(select(Asset))).scalar_one()
    assert asset.script == fixed
    assert asset.provenance["render_attempts"] == 2
    assert asset.provenance["failed_attempts"][0]["error"].startswith("exit 1:")


async def test_contract_violation_goes_to_repair_without_rendering(session: AsyncSession) -> None:
    seeded = await seed_topic(session)
    broken = SCRIPT.replace('self.next_section(name="vote")', "pass")
    models = video_models(seeded, scripts=[broken, SCRIPT])
    sandbox = FakeSandbox([manim_success(), probe({})])
    ctx, _ = await job_context(session, "video", {"topic_id": str(seeded.topic_id)})
    await run_video(ctx, deps(models, sandbox))
    assert len(sandbox.runs) == 2  # one render, one probe
    prompt = models.models["script_repair"].requests[0]["last_message"]["content"][0]["text"]
    assert "expected ['intro', 'vote'], found ['intro']" in prompt


async def test_render_timeout_fails_with_reason_timeout(session: AsyncSession) -> None:
    seeded = await seed_topic(session)
    sandbox = FakeSandbox([manim_timeout()])
    ctx, _ = await job_context(session, "video", {"topic_id": str(seeded.topic_id)})
    with pytest.raises(PermanentJobError) as err:
        await run_video(ctx, deps(video_models(seeded), sandbox))
    assert err.value.reason == "timeout"
    assert len(sandbox.runs) == 1


async def test_missing_sections_get_no_cue_and_are_reported(session: AsyncSession) -> None:
    seeded = await seed_topic(session)
    models = Models(
        storyboard=[[call("Storyboard", storyboard(seeded.chunk_ids, ("intro", "vote")))]],
        manim_script=[[call("ManimScript", {"script": SCRIPT})]],
    )
    sandbox = FakeSandbox([manim_success(("intro",)), probe({})])
    ctx, rec = await job_context(session, "video", {"topic_id": str(seeded.topic_id)})
    await run_video(ctx, deps(models, sandbox))
    asset = (await session.execute(select(Asset))).scalar_one()
    assert asset.provenance["missing_sections"] == ["vote"]
    assert "no video section for scenes: vote" in rec.progress
    captions = (settings.brainbench_data / asset.files["captions"]).read_text()
    assert "Narration for vote" not in captions


async def test_resume_from_checkpoint_skips_finished_steps(session: AsyncSession) -> None:
    seeded = await seed_topic(session)
    checkpoint = {
        "storyboard": storyboard(seeded.chunk_ids),
        "script": "broken",
        "attempts": [{"attempt": 1, "error": "exit 1: boom"}],
        "needs_repair": True,
        "last_log": "boom",
        "last_problems": [],
    }
    models = Models(script_repair=[[call("ManimScript", {"script": SCRIPT})]])
    sandbox = FakeSandbox([manim_success(), probe({})])
    ctx, _ = await job_context(
        session, "video", {"topic_id": str(seeded.topic_id)}, checkpoint=checkpoint
    )
    await run_video(ctx, deps(models, sandbox))
    assert models.steps == ["script_repair"]
    asset = (await session.execute(select(Asset))).scalar_one()
    assert asset.provenance["render_attempts"] == 2


async def test_provider_limit_defers_with_storyboard_checkpointed(session: AsyncSession) -> None:
    seeded = await seed_topic(session)
    reset = datetime.now(UTC) + timedelta(hours=2)

    def throttled(messages: Any) -> Any:
        raise ProviderThrottledException("usage limit", reset_at=reset, scope=ALL_ACCOUNTS)

    models = Models(
        storyboard=[[call("Storyboard", storyboard(seeded.chunk_ids))]],
        manim_script=[throttled],
    )
    ctx, rec = await job_context(session, "video", {"topic_id": str(seeded.topic_id)})
    video_deps = deps(models, FakeSandbox([]))
    video_deps.retry_strategy = ProviderRetryStrategy()
    with pytest.raises(ProviderLimitReached) as err:
        await run_video(ctx, video_deps)
    assert err.value.reset_at == reset
    assert ctx.checkpoint.keys() == {"storyboard"}
    assert rec.checkpoints[-1]["storyboard"]["title"] == "Raft leader election"


async def test_regeneration_carries_feedback_and_keeps_history(session: AsyncSession) -> None:
    seeded = await seed_topic(session)
    first_ctx, _ = await job_context(session, "video", {"topic_id": str(seeded.topic_id)})
    await run_video(
        first_ctx, deps(video_models(seeded), FakeSandbox([manim_success(), probe({})]))
    )
    await session.commit()
    old = (await session.execute(select(Asset))).scalar_one()

    models = video_models(seeded)
    payload = {
        "topic_id": str(seeded.topic_id),
        "feedback": "too fast, show the proof step by step",
        "previous_asset_id": str(old.id),
    }
    ctx, _ = await job_context(session, "video", payload)
    await run_video(ctx, deps(models, FakeSandbox([manim_success(), probe({})])))
    await session.commit()

    board_prompt = models.models["storyboard"].requests[0]["last_message"]["content"][0]["text"]
    assert "too fast" in board_prompt and "Previous storyboard" in board_prompt
    script_prompt = models.models["manim_script"].requests[0]["last_message"]["content"][0]["text"]
    assert "Previous script" in script_prompt and "class Lesson(Scene)" in script_prompt
    rows = (await session.execute(select(Asset).order_by(Asset.created_at))).scalars().all()
    await session.refresh(old)
    new = next(a for a in rows if a.id != old.id)
    assert not old.is_current and new.is_current
    assert new.previous_asset_id == old.id and new.feedback == payload["feedback"]


async def test_topic_without_chunks_fails_permanently(session: AsyncSession) -> None:
    seeded = await seed_topic(session, chunks=())
    ctx, _ = await job_context(session, "video", {"topic_id": str(seeded.topic_id)})
    with pytest.raises(PermanentJobError) as err:
        await run_video(ctx, deps(Models(), FakeSandbox([])))
    assert err.value.reason == "topic-without-chunks"
