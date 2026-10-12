"""The ``video`` job pipeline (design D7.2, task 7.3).

storyboard -> Manim script -> render in the ``brainbench/manim`` sandbox -> section clips probed
with ``ffprobe`` -> one WebVTT cue per storyboard scene -> asset. A failed render goes to the
``script_repair`` agent with the render log; there are at most ``settings.render_attempts``
renders (3). Each completed step is checkpointed, so a provider-limit deferral resumes where it
stopped instead of starting over.
"""

import json
import logging
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.jobs.errors import PermanentJobError
from app.jobs.registry import JobContext
from app.services import assets, budget

# Module import: app.jobs imports this module's handler while app.services.storyboard may still
# be initializing (storyboard -> app.jobs.errors -> app.jobs -> app.jobs.video -> here).
from app.services import storyboard as sb
from app.services.sandbox import Mount, SandboxLimits, SandboxResult, prepare_dir

__lazy_modules__ = {"brainbench_agents.visual"}
from brainbench_agents.visual import (
    Storyboard,
    check_script,
    generate_manim_script,
    repair_manim_script,
)

log = logging.getLogger(__name__)

# Manim derives every output name from the -o stem and the quality (task 1.4 pins these).
VIDEO_DIR = "videos/scene/720p30"
VIDEO_FILE = f"{VIDEO_DIR}/lesson.mp4"
SECTIONS_DIR = f"{VIDEO_DIR}/sections"
SECTIONS_FILE = f"{SECTIONS_DIR}/lesson.json"
MANIM_COMMAND = (
    "manim",
    "-qm",
    "--save_sections",
    "--media_dir",
    "/out",
    "-o",
    "lesson.mp4",
    "/work/scene.py",
    "Lesson",
)
PROBE_SCRIPT = (
    'for f in "$@"; do printf "%s\\t" "$f"; '
    'ffprobe -v error -show_entries format=duration -of csv=p=0 "/out/$f" || echo; done'
)
CLIP_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.mp4$")
LOG_KEEP_CHARS = 60_000


@dataclass(frozen=True)
class Section:
    name: str
    video: str
    duration_s: float


@dataclass(frozen=True)
class Cue:
    scene_id: str
    start: float
    end: float
    text: str


def read_sections(out_dir: Path) -> list[dict[str, Any]]:
    """Entries of ``sections/lesson.json`` (``name``, ``video``, ...), in render order."""
    raw = json.loads((out_dir / SECTIONS_FILE).read_text())
    if not isinstance(raw, list):
        raise ValueError("sections/lesson.json is not a list")
    sections = []
    for entry in raw:
        name, video = entry.get("name"), entry.get("video")
        if not isinstance(name, str) or not isinstance(video, str) or not CLIP_NAME.match(video):
            raise ValueError(f"unexpected section entry {entry!r}")
        sections.append(entry)
    return sections


def parse_probe(output: str) -> dict[str, float]:
    """``path<TAB>duration`` lines from the probe run; unreadable durations are skipped."""
    durations: dict[str, float] = {}
    for line in output.splitlines():
        path, _, value = line.partition("\t")
        try:
            durations[path.strip()] = float(value.strip())
        except ValueError:
            continue
    return durations


def build_cues(storyboard: Storyboard, sections: list[Section]) -> tuple[list[Cue], list[str]]:
    """One cue per storyboard scene spanning its section's real time range.

    Sections follow each other in the final video, so a section starts where the previous one
    ended. Scenes the script gave no section get no cue and are returned as missing.
    """
    ranges: dict[str, tuple[float, float]] = {}
    clock = 0.0
    for section in sections:
        start, clock = clock, clock + max(section.duration_s, 0.0)
        ranges.setdefault(section.name, (start, clock))
    cues, missing = [], []
    for scene in storyboard.scenes:
        if scene.id in ranges:
            start, end = ranges[scene.id]
            cues.append(Cue(scene.id, start, end, " ".join(scene.narration.split())))
        else:
            missing.append(scene.id)
    return cues, missing


def timestamp(seconds: float) -> str:
    millis = round(max(seconds, 0.0) * 1000)
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def render_webvtt(cues: list[Cue]) -> str:
    blocks = ["WEBVTT"]
    for index, cue in enumerate(cues, start=1):
        text = cue.text.replace("-->", "->")
        blocks.append(f"{index}\n{timestamp(cue.start)} --> {timestamp(cue.end)}\n{text}")
    return "\n\n".join(blocks) + "\n"


def failure_summary(result: SandboxResult | None, problems: list[str]) -> str:
    if problems:
        return "script contract: " + "; ".join(problems)
    assert result is not None
    lines = [line.strip() for line in result.output.splitlines() if line.strip()]
    tail = " | ".join(lines[-3:]) if lines else "no output"
    return f"exit {result.exit_code}: {tail}"[:2000]


class VideoPipeline:
    def __init__(self, ctx: JobContext, deps: sb.VisualDeps) -> None:
        self.ctx = ctx
        self.deps = deps
        self.session = ctx.session
        self.checkpoint = dict(ctx.checkpoint)
        base = assets.job_dir(ctx.job_id)
        self.work = base / "work"
        self.out = base / "out"

    async def save(self) -> None:
        await self.ctx.save_checkpoint(self.checkpoint)

    def limits(self) -> SandboxLimits:
        return SandboxLimits(
            timeout_s=settings.render_timeout_s,
            cpus=settings.render_cpus,
            memory=settings.render_memory,
            pids=settings.sandbox_pids_limit,
        )

    async def run(self) -> None:
        topic_id, feedback, previous_id = sb.parse_payload(self.ctx.payload)
        topic = await sb.topic_for_job(self.session, topic_id)
        previous = await sb.load_previous(self.session, previous_id)
        language = (await sb.load_learner(self.session)).language

        if "storyboard" not in self.checkpoint:
            await self.ctx.progress("writing the storyboard")
            storyboard = await sb.write_storyboard(
                self.deps, self.session, topic, "video", previous=previous, feedback=feedback
            )
            self.checkpoint["storyboard"] = storyboard.model_dump()
            await self.save()
        storyboard = Storyboard.model_validate(self.checkpoint["storyboard"])
        self.ctx.raise_if_cancelled()

        if "script" not in self.checkpoint:
            await self.ctx.progress("writing the Manim script")
            model = await sb.routed_model(self.deps, self.session, "manim_script")
            script = await sb.agent_step(
                generate_manim_script(
                    model,
                    storyboard,
                    language=language,
                    previous_script=previous.asset.script if previous else None,
                    feedback=feedback,
                    retry_strategy=self.deps.retry_strategy,
                )
            )
            self.checkpoint.update(script=script.script, attempts=[], needs_repair=False)
            await self.save()

        result = await self.render_with_repairs(storyboard)
        await self.store(topic.id, storyboard, result, feedback, previous_id)

    async def render_with_repairs(self, storyboard: Storyboard) -> SandboxResult:
        max_attempts = settings.render_attempts
        while True:
            self.ctx.raise_if_cancelled()
            attempts: list[dict[str, Any]] = self.checkpoint.setdefault("attempts", [])
            if self.checkpoint.get("needs_repair"):
                await self.repair(storyboard, len(attempts))
            attempt = len(attempts) + 1
            script = str(self.checkpoint["script"])
            problems = check_script(script, storyboard.scene_ids)
            result: SandboxResult | None = None
            if problems:
                error = failure_summary(None, problems)
            else:
                await self.ctx.progress(f"rendering (attempt {attempt} of {max_attempts})")
                result = await self.render(script)
                if not result.ok:
                    error = failure_summary(result, [])
                elif (self.out / VIDEO_FILE).is_file() and (self.out / SECTIONS_FILE).is_file():
                    self.checkpoint["render_attempts"] = attempt
                    return result
                else:
                    error = "render finished without lesson.mp4 or sections/lesson.json"
            log.info("video job %s: attempt %d failed: %s", self.ctx.job_id, attempt, error)
            attempts.append({"attempt": attempt, "error": error})
            render_log = "\n".join(problems) if result is None else result.output
            self.checkpoint.update(
                needs_repair=True,
                last_log=render_log[-LOG_KEEP_CHARS:],
                last_problems=problems,
            )
            await self.save()
            if attempt >= max_attempts:
                raise PermanentJobError(
                    f"render failed after {attempt} attempts; last error: {error}",
                    reason="render-failed",
                )

    async def repair(self, storyboard: Storyboard, failed_attempts: int) -> None:
        await self.ctx.progress(
            f"repairing the script (attempt {failed_attempts + 1} of {settings.render_attempts})"
        )
        model = await sb.routed_model(self.deps, self.session, "script_repair")
        repaired = await sb.agent_step(
            repair_manim_script(
                model,
                storyboard,
                str(self.checkpoint["script"]),
                str(self.checkpoint.get("last_log", "")),
                problems=list(self.checkpoint.get("last_problems", [])),
                attempt=failed_attempts,
                retry_strategy=self.deps.retry_strategy,
            )
        )
        self.checkpoint.update(script=repaired.script, needs_repair=False)
        await self.save()

    async def render(self, script: str) -> SandboxResult:
        prepare_dir(self.work, writable=False)
        shutil.rmtree(self.out, ignore_errors=True)
        prepare_dir(self.out, writable=True)
        scene = self.work / "scene.py"
        scene.write_text(script)
        scene.chmod(0o644)
        result = await self.deps.sandbox.run(
            settings.manim_image,
            MANIM_COMMAND,
            mounts=[Mount(self.work, "/work"), Mount(self.out, "/out", read_only=False)],
            limits=self.limits(),
            register_kill_hook=self.ctx.register_kill_hook,
            label=f"video:{self.ctx.job_id}",
        )
        await budget.record_render(self.session, result.duration_s)
        await self.session.commit()
        self.ctx.raise_if_cancelled()
        if result.timed_out:
            raise PermanentJobError(
                f"render exceeded the {settings.render_timeout_s:.0f} s limit and was stopped",
                reason="timeout",
            )
        if result.killed:
            raise PermanentJobError("the render sandbox was killed", reason="killed")
        return result

    async def probe(self, relative_paths: list[str]) -> dict[str, float]:
        result = await self.deps.sandbox.run(
            settings.manim_image,
            ["sh", "-c", PROBE_SCRIPT, "probe", *relative_paths],
            mounts=[Mount(self.out, "/out")],
            limits=SandboxLimits(timeout_s=120, cpus=1, memory="1g", pids=64),
            register_kill_hook=self.ctx.register_kill_hook,
            label=f"probe:{self.ctx.job_id}",
        )
        if not result.ok:
            raise RuntimeError(f"ffprobe failed: {failure_summary(result, [])}")
        return parse_probe(result.stdout)

    async def store(
        self,
        topic_id: uuid.UUID,
        storyboard: Storyboard,
        render: SandboxResult,
        feedback: str | None,
        previous_id: str | None,
    ) -> None:
        await self.ctx.progress("writing captions")
        entries = read_sections(self.out)
        clips = [f"{SECTIONS_DIR}/{entry['video']}" for entry in entries]
        durations = await self.probe([VIDEO_FILE, *clips])
        sections = [
            Section(entry["name"], entry["video"], durations.get(clip, 0.0))
            for entry, clip in zip(entries, clips, strict=True)
        ]
        cues, missing = build_cues(storyboard, sections)
        if missing:
            message = "no video section for scenes: " + ", ".join(missing)
            log.warning("video job %s: %s", self.ctx.job_id, message)
            await self.ctx.progress(message)
        asset_id = uuid.uuid4()
        target = assets.asset_dir(topic_id, asset_id)
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.out / VIDEO_FILE, target / "lesson.mp4")
        (target / "lesson.vtt").write_text(render_webvtt(cues))
        duration = durations.get(VIDEO_FILE) or sum(s.duration_s for s in sections)
        await assets.save_asset(
            self.session,
            asset_id=asset_id,
            topic_id=topic_id,
            job_id=self.ctx.job_id,
            format="video",
            storyboard=storyboard,
            files={
                "video": assets.data_relative(target / "lesson.mp4"),
                "captions": assets.data_relative(target / "lesson.vtt"),
            },
            script=str(self.checkpoint["script"]),
            provenance={
                "render_attempts": self.checkpoint.get("render_attempts", 1),
                "failed_attempts": self.checkpoint.get("attempts", []),
                "sections": [
                    {"name": s.name, "video": s.video, "duration_s": s.duration_s} for s in sections
                ],
                "missing_sections": missing,
                "render_log_tail": render.output[-4000:],
            },
            duration_s=round(duration, 3),
            previous_asset_id=previous_id,
            feedback=feedback,
        )


async def run_video(ctx: JobContext, deps: sb.VisualDeps) -> None:
    pipeline = VideoPipeline(ctx, deps)
    try:
        await pipeline.run()
    finally:
        assets.remove_job_dir(ctx.job_id)
