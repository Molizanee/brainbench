"""The ``figure`` job pipeline (design D7.3, task 7.4).

storyboard -> ``figure_brief`` (``applicable == false`` ends the job as ``skipped`` before any
session starts) -> an Anatomy agent session in a fresh per-job workspace, bounded by a turn
budget and a wall-clock limit, whose ``run_node`` tool runs in the ``brainbench/anatomy``
sandbox -> the standalone HTML, PNG preview and build source collected by content -> asset with
the storyboard, brief and transcript as provenance. No figure means ``failed`` with the
transcript tail, and nothing is stored.
"""

import asyncio
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.jobs.errors import JobSkipped, PermanentJobError
from app.jobs.registry import JobContext
from app.services import assets, budget
from app.services import storyboard as sb
from app.services.sandbox import Mount, SandboxLimits, prepare_dir

__lazy_modules__ = {"brainbench_agents.visual"}
from brainbench_agents.visual import (
    FigureBrief,
    FigureOutputs,
    FigureSession,
    FigureWorkspace,
    NodeRun,
    Storyboard,
    collect_outputs,
    figure_prompt,
    generate_figure_brief,
    make_figure_agent,
    run_figure_session,
)

log = logging.getLogger(__name__)

CHROME_WRAPPER = "/tmp/.bb/chromium"
# Chromium in a container needs --no-sandbox, which the skill's scripts do not pass; the entry
# point writes a wrapper into the /tmp tmpfs and points CHROME_PATH at it, then runs node.
NODE_ENTRYPOINT = (
    "sh",
    "-c",
    "mkdir -p /tmp/.bb && printf '#!/bin/sh\\nexec /usr/bin/chromium --no-sandbox "
    '--disable-dev-shm-usage "$@"\\n\' > /tmp/.bb/chromium && chmod 755 /tmp/.bb/chromium '
    '&& exec node "$@"',
    "node",
)
SOURCE_PROVENANCE_BYTES = 200 * 1024
TRANSCRIPT_TAIL_CHARS = 6000


def skill_dir() -> Path:
    return settings.skills_dir / "anatomy"


class SandboxNode:
    """The ``NodeRunner`` given to the figure workspace: one sandbox per ``run_node`` call,
    serialized, with the sandbox time summed for the render budget."""

    def __init__(self, ctx: JobContext, deps: sb.VisualDeps, work: Path) -> None:
        self.ctx = ctx
        self.deps = deps
        self.work = work
        self.seconds = 0.0
        self.lock = asyncio.Lock()

    async def __call__(self, command: list[str]) -> NodeRun:
        async with self.lock:
            result = await self.deps.sandbox.run(
                settings.anatomy_image,
                command,
                mounts=[
                    Mount(self.work, "/workspace", read_only=False),
                    Mount(skill_dir(), "/skill"),
                ],
                limits=SandboxLimits(
                    timeout_s=settings.figure_node_timeout_s,
                    cpus=settings.figure_cpus,
                    memory=settings.figure_memory,
                    pids=settings.sandbox_pids_limit,
                ),
                environment={"CHROME_PATH": CHROME_WRAPPER, "HOME": "/tmp"},
                entrypoint=NODE_ENTRYPOINT,
                workdir="/workspace",
                register_kill_hook=self.ctx.register_kill_hook,
                label=f"figure:{self.ctx.job_id}",
            )
        self.seconds += result.duration_s
        return NodeRun(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=result.timed_out,
            duration_s=result.duration_s,
        )


def failure_reason(session: FigureSession) -> tuple[str, str]:
    if session.timed_out:
        return "timeout", f"reached the {settings.figure_wall_clock_s / 60:.0f}-minute limit"
    if session.stop_reason == "limit_turns":
        return "turn-budget", f"used its {settings.figure_max_turns}-turn budget"
    return "no-figure", f"ended ({session.stop_reason})"


class FigurePipeline:
    def __init__(self, ctx: JobContext, deps: sb.VisualDeps) -> None:
        self.ctx = ctx
        self.deps = deps
        self.session = ctx.session
        self.checkpoint = dict(ctx.checkpoint)
        self.work = assets.job_dir(ctx.job_id) / "work"

    async def save(self) -> None:
        await self.ctx.save_checkpoint(self.checkpoint)

    async def run(self) -> None:
        topic_id, feedback, previous_id = sb.parse_payload(self.ctx.payload)
        topic = await sb.topic_for_job(self.session, topic_id)
        previous = await sb.load_previous(self.session, previous_id)

        if "storyboard" not in self.checkpoint:
            await self.ctx.progress("writing the storyboard")
            storyboard = await sb.write_storyboard(
                self.deps, self.session, topic, "figure", previous=previous, feedback=feedback
            )
            self.checkpoint["storyboard"] = storyboard.model_dump()
            await self.save()
        storyboard = Storyboard.model_validate(self.checkpoint["storyboard"])
        self.ctx.raise_if_cancelled()

        if "brief" not in self.checkpoint:
            await self.ctx.progress("deriving the figure brief")
            cited = [c for s in storyboard.scenes for c in s.chunk_ids]
            chunks = await sb.chunks_by_id(self.session, cited)
            model = await sb.routed_model(self.deps, self.session, "figure_brief")
            brief = await sb.agent_step(
                generate_figure_brief(
                    model,
                    storyboard,
                    chunks,
                    feedback=feedback,
                    retry_strategy=self.deps.retry_strategy,
                )
            )
            self.checkpoint["brief"] = brief.model_dump()
            await self.save()
        brief = FigureBrief.model_validate(self.checkpoint["brief"])

        if not brief.applicable:
            assets.save_storyboard(
                self.session,
                topic_id=topic.id,
                job_id=self.ctx.job_id,
                format="figure",
                storyboard=storyboard,
                figure_brief=brief.model_dump(),
            )
            log.info("figure job %s skipped: %s", self.ctx.job_id, brief.rationale)
            raise JobSkipped(f"figure not applicable: {brief.rationale}")

        if not (skill_dir() / "SKILL.md").is_file():
            raise PermanentJobError(
                f"the Anatomy skill is not installed at {skill_dir()}; restart the stack so "
                "init installs it",
                reason="skill-missing",
            )
        language = (await sb.load_learner(self.session)).language
        session, outputs = await self.build(topic.title, storyboard, brief, language, previous)
        await self.store(topic.id, storyboard, brief, session, outputs, feedback, previous_id)

    def seed_previous(self, previous: sb.PreviousVersion | None) -> list[str]:
        """Copy the previous version's build source into the fresh workspace."""
        if previous is None:
            return []
        copied = []
        for relative in previous.asset.files.get("source", []):
            try:
                source = assets.data_path(relative)
            except ValueError:
                continue
            name = relative.split("/source/", 1)[-1]
            if not source.is_file() or not name or ".." in Path(name).parts:
                continue
            target = self.work / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o666)
            copied.append(name)
        return copied

    async def build(
        self,
        title: str,
        storyboard: Storyboard,
        brief: FigureBrief,
        language: str,
        previous: sb.PreviousVersion | None,
    ) -> tuple[FigureSession, FigureOutputs]:
        shutil.rmtree(self.work, ignore_errors=True)
        prepare_dir(self.work, writable=True)
        previous_files = self.seed_previous(previous)
        node = SandboxNode(self.ctx, self.deps, self.work)
        workspace = FigureWorkspace(self.work, skill_dir(), node)
        model = await sb.routed_model(self.deps, self.session, "figure")
        agent = make_figure_agent(
            model,
            workspace,
            brief=brief,
            storyboard=storyboard,
            retry_strategy=self.deps.retry_strategy,
        )
        await self.ctx.progress("building the figure")
        try:
            session = await sb.agent_step(
                run_figure_session(
                    agent,
                    figure_prompt(
                        title,
                        language=language,
                        feedback=self.ctx.payload.get("feedback") or None,
                        previous_files=previous_files,
                    ),
                    max_turns=settings.figure_max_turns,
                    timeout_s=settings.figure_wall_clock_s,
                    cancelled=self.ctx.cancelled,
                )
            )
            self.ctx.raise_if_cancelled()
            outputs = collect_outputs(workspace)
            if outputs.html is not None and outputs.png is None:
                await self.ctx.progress("capturing the preview")
                await workspace.run_node(
                    [
                        "/skill/scripts/capture.mjs",
                        workspace.relative(outputs.html),
                        "--out",
                        "preview.png",
                    ]
                )
                outputs = collect_outputs(workspace)
        finally:
            await budget.record_render(self.session, node.seconds)
            await self.session.commit()
        if outputs.html is None or outputs.png is None:
            reason, how = failure_reason(session)
            missing = "a standalone HTML figure" if outputs.html is None else "a PNG preview"
            raise PermanentJobError(
                f"the figure session {how} after {session.turns} turns without {missing}. "
                f"Transcript tail:\n{session.transcript_tail(TRANSCRIPT_TAIL_CHARS)}",
                reason=reason,
            )
        return session, outputs

    async def store(
        self,
        topic_id: uuid.UUID,
        storyboard: Storyboard,
        brief: FigureBrief,
        session: FigureSession,
        outputs: FigureOutputs,
        feedback: str | None,
        previous_id: str | None,
    ) -> None:
        assert outputs.html is not None and outputs.png is not None
        asset_id = uuid.uuid4()
        target = assets.asset_dir(topic_id, asset_id)
        (target / "source").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(outputs.html, target / "figure.html")
        shutil.copyfile(outputs.png, target / "preview.png")
        source_files: list[str] = []
        build_source: list[dict[str, Any]] = []
        budget_left = SOURCE_PROVENANCE_BYTES
        for path in outputs.sources:
            name = path.relative_to(self.work).as_posix()
            copy = target / "source" / name
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, copy)
            source_files.append(assets.data_relative(copy))
            text = path.read_text(errors="replace")
            entry: dict[str, Any] = {"path": name, "size": path.stat().st_size}
            if len(text) <= budget_left:
                entry["content"] = text
                budget_left -= len(text)
            build_source.append(entry)
        await assets.save_asset(
            self.session,
            asset_id=asset_id,
            topic_id=topic_id,
            job_id=self.ctx.job_id,
            format="figure",
            storyboard=storyboard,
            figure_brief=brief.model_dump(),
            files={
                "html": assets.data_relative(target / "figure.html"),
                "png": assets.data_relative(target / "preview.png"),
                "source": source_files,
            },
            provenance={
                "brief": brief.model_dump(),
                "build_source": build_source,
                "html_source": outputs.html.relative_to(self.work).as_posix(),
                "transcript_tail": session.transcript_tail(TRANSCRIPT_TAIL_CHARS),
                "stop_reason": session.stop_reason,
                "turns": session.turns,
                "timed_out": session.timed_out,
            },
            previous_asset_id=previous_id,
            feedback=feedback,
        )


async def run_figure(ctx: JobContext, deps: sb.VisualDeps) -> None:
    try:
        await FigurePipeline(ctx, deps).run()
    finally:
        assets.remove_job_dir(ctx.job_id)
