"""Seed data and fakes shared by the visual-generation tests (section 7).

``seed_topic`` writes a topic with a revision, a source, chunks and their ``topic_chunks``
citations, plus a profile. ``FakeSandbox`` stands in for :class:`SandboxRunner`: each run pops
a scripted step that may write files into the mounted directories, the way Manim or Node would.
"""

import json
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.schema import Chunk, Profile, Source, Topic, TopicChunk, TopicRevision
from app.jobs.registry import JobContext
from app.services import jobs
from app.services.sandbox import Mount, SandboxResult
from app.services.video import SECTIONS_DIR, VIDEO_FILE

SCRIPT = """\
from manim import *


class Lesson(Scene):
    def construct(self) -> None:
        self.next_section(name="intro")
        self.play(Create(Circle()), run_time=0.5)
        self.next_section(name="vote")
        self.wait(0.5)
"""


@dataclass(frozen=True)
class SeededTopic:
    topic_id: uuid.UUID
    source_id: uuid.UUID
    chunk_ids: list[str]
    title: str


async def seed_topic(
    session: AsyncSession,
    *,
    title: str = "Raft leader election",
    summary: str = "How Raft elects a leader with a majority of votes.",
    chunks: Sequence[str] = (
        "A candidate becomes leader when a majority of servers vote for it.",
        "Leaders send periodic heartbeats; a follower that hears none starts an election.",
    ),
    profile: bool = True,
    language: str = "en",
    depth: str = "intuition-first",
) -> SeededTopic:
    source = Source(id=uuid.uuid4(), type="note", title="Raft notes", status="ready")
    topic = Topic(id=uuid.uuid4())
    session.add_all([source, topic])
    await session.flush()
    session.add(
        TopicRevision(topic_id=topic.id, revision=1, title=title, summary=summary, created_by="run")
    )
    chunk_ids = []
    for position, text in enumerate(chunks):
        chunk = Chunk(id=uuid.uuid4(), source_id=source.id, position=position, text=text)
        session.add(chunk)
        await session.flush()
        session.add(TopicChunk(topic_id=topic.id, chunk_id=chunk.id))
        chunk_ids.append(str(chunk.id))
    if profile:
        session.add(
            Profile(
                display_name="Eng",
                background="backend engineer",
                weekly_budget_hours=5,
                depth=depth,
                language=language,
            )
        )
    await session.commit()
    return SeededTopic(topic.id, source.id, chunk_ids, title)


def storyboard(
    chunk_ids: Sequence[str], scenes: Sequence[str] = ("intro", "vote")
) -> dict[str, Any]:
    return {
        "title": "Raft leader election",
        "scenes": [
            {
                "id": scene,
                "objective": f"understand {scene}",
                "narration": f"Narration for {scene}.",
                "visual": f"Visual for {scene}.",
                "chunk_ids": [chunk_ids[i % len(chunk_ids)]],
            }
            for i, scene in enumerate(scenes)
        ],
    }


@dataclass
class Recorder:
    progress: list[str] = field(default_factory=list)
    checkpoints: list[dict[str, Any]] = field(default_factory=list)
    hooks: list[Any] = field(default_factory=list)


async def job_context(
    session: AsyncSession,
    job_type: str,
    payload: dict[str, Any],
    *,
    checkpoint: dict[str, Any] | None = None,
    cancelled: Callable[[], bool] = lambda: False,
) -> tuple[JobContext, Recorder]:
    job = await jobs.enqueue(session, job_type, payload)
    await session.commit()
    recorder = Recorder()

    async def save(cp: dict[str, Any]) -> None:
        recorder.checkpoints.append(dict(cp))

    async def progress(message: str) -> None:
        recorder.progress.append(message)

    ctx = JobContext(
        job_id=job.id,
        type=job_type,
        payload=payload,
        checkpoint=dict(checkpoint or {}),
        session=session,
        save_checkpoint_fn=save,
        progress_fn=progress,
        is_cancelled=cancelled,
    )
    return ctx, recorder


type Step = Callable[[dict[str, Path], list[str]], SandboxResult]


def result(
    exit_code: int | None = 0,
    *,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    killed: bool = False,
    duration_s: float = 30.0,
) -> SandboxResult:
    return SandboxResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        output=stdout + stderr,
        timed_out=timed_out,
        killed=killed,
        duration_s=duration_s,
    )


@dataclass
class FakeSandbox:
    """Pops one scripted step per ``run``; records images, commands and mounts."""

    steps: list[Step]
    runs: list[dict[str, Any]] = field(default_factory=list)

    async def run(self, image: str, command: Sequence[str], **options: Any) -> SandboxResult:
        mounts: Sequence[Mount] = options["mounts"]
        self.runs.append({"image": image, "command": list(command), **options})
        if not self.steps:
            raise AssertionError(f"unexpected sandbox run: {list(command)}")
        return self.steps.pop(0)({m.target: m.path for m in mounts}, list(command))


def manim_success(sections: Sequence[str] = ("intro", "vote")) -> Step:
    def step(mounts: dict[str, Path], command: list[str]) -> SandboxResult:
        out = mounts["/out"]
        (out / SECTIONS_DIR).mkdir(parents=True, exist_ok=True)
        (out / VIDEO_FILE).write_bytes(b"\x00\x00\x00\x18ftypmp42fake-mp4")
        entries = []
        for index, name in enumerate(sections):
            video = f"lesson_{index:04d}_{name}.mp4"
            (out / SECTIONS_DIR / video).write_bytes(b"clip")
            entries.append({"name": name, "type": "default.normal", "video": video})
        (out / SECTIONS_DIR / "lesson.json").write_text(json.dumps(entries))
        return result(0, stdout="File ready at /out/videos/scene/720p30/lesson.mp4\n")

    return step


def manim_failure(message: str = "NameError: name 'Circel' is not defined") -> Step:
    def step(mounts: dict[str, Path], command: list[str]) -> SandboxResult:
        return result(1, stderr=f"Traceback (most recent call last):\n  ...\n{message}\n")

    return step


def manim_timeout() -> Step:
    return lambda mounts, command: result(None, timed_out=True, duration_s=900.0)


def probe(durations: dict[str, float]) -> Step:
    def step(mounts: dict[str, Path], command: list[str]) -> SandboxResult:
        lines = []
        for path in command[4:]:
            name = path.rsplit("/", 1)[-1]
            lines.append(f"{path}\t{durations.get(name, 1.0)}")
        return result(0, stdout="\n".join(lines) + "\n")

    return step
