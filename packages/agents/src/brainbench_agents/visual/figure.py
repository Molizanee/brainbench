"""The figure-building agent session (design D7.3, task 7.4).

``make_figure_agent(model, workspace)`` runs the vendored Anatomy ``SKILL.md`` as its system
prompt with BrainBench-owned tools that only reach the job workspace: ``read_file``,
``write_file``, ``list_files``, ``read_reference`` and ``run_node``. ``run_node`` goes through an
injected :data:`NodeRunner` (the app runs ``node`` in the ``brainbench/anatomy`` sandbox); PNGs
a call writes are relayed to the model as images so it can look at its own work. A rejected
path or command is raised inside the tool, which Strands turns into an error result the agent
sees; the session goes on.
"""

import asyncio
import json
import logging
import re
import threading
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from brainbench_agents.visual.models import FigureBrief, Storyboard
from brainbench_agents.visual.prompts import figure_system

__lazy_modules__ = {
    "strands",
    "strands.hooks",
    "strands.models.model",
    "strands.tools.executors",
    "strands.types.content",
}
from strands import Agent, tool
from strands.hooks import HookProvider, HookRegistry, MessageAddedEvent
from strands.models.model import Model
from strands.tools.executors import SequentialToolExecutor
from strands.types.content import ContentBlock, Message, Messages

log = logging.getLogger(__name__)

WORKSPACE_MOUNT = "/workspace"
SKILL_MOUNT = "/skill"
SCRIPT_SUFFIXES = (".mjs", ".js", ".cjs")
SKILL_SCRIPT_DIRS = ("scripts/", "kit/")
READ_LIMIT_BYTES = 256 * 1024
WRITE_LIMIT_BYTES = 4 * 1024 * 1024
LIST_LIMIT = 400
MAX_ARGS = 64
IMAGE_LIMIT_BYTES = 1_500_000
IMAGES_PER_CALL = 2
OUTPUT_TAIL_CHARS = 6000
SKIP_DIRS = {"node_modules", ".git"}
SOURCE_SUFFIXES = {".mjs", ".js", ".cjs", ".ts", ".css", ".json"}
SOURCE_LIMIT_BYTES = 512 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
EXTERNAL_REF = re.compile(
    r"""(?:src|href)\s*=\s*["']?\s*(?:https?:)?//|@import\s+(?:url\()?["']?(?:https?:)?//|"""
    r"""url\(\s*["']?(?:https?:)?//""",
    re.IGNORECASE,
)


class WorkspaceError(ValueError):
    """A tool call that would leave the workspace or run something other than Node."""


@dataclass(frozen=True)
class NodeRun:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    duration_s: float = 0.0


type NodeRunner = Callable[[list[str]], Awaitable[NodeRun]]


def _tail(text: str, limit: int = OUTPUT_TAIL_CHARS) -> str:
    return text if len(text) <= limit else "[...]\n" + text[-limit:]


class FigureWorkspace:
    """The per-job workspace (``root``, mounted read-write at ``/workspace`` in the sandbox) and
    the vendored skill (``skill_dir``, read-only at ``/skill``)."""

    def __init__(self, root: Path, skill_dir: Path, run_node: NodeRunner) -> None:
        self.root = root.resolve()
        self.skill_dir = skill_dir.resolve()
        self._run_node = run_node

    # --- paths --------------------------------------------------------------------------------

    def resolve(self, path: str) -> Path:
        """Absolute path of a workspace file; anything that leaves the workspace is refused."""
        text = str(path).strip()
        if text == WORKSPACE_MOUNT:
            text = "."
        elif text.startswith(WORKSPACE_MOUNT + "/"):
            text = text[len(WORKSPACE_MOUNT) + 1 :]
        if not text or "\x00" in text or text.startswith(("/", "~")):
            raise WorkspaceError(
                f"{path!r} is outside the workspace; use a path relative to /workspace"
            )
        candidate = (self.root / text).resolve()
        if not candidate.is_relative_to(self.root):
            raise WorkspaceError(f"{path!r} is outside the workspace")
        return candidate

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def files(self) -> list[Path]:
        """Every regular file in the workspace (no symlinks, no node_modules), sorted."""
        found = []
        for path in sorted(self.root.rglob("*")):
            parts = path.relative_to(self.root).parts
            if any(part in SKIP_DIRS for part in parts):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            found.append(path)
        return found

    # --- tools --------------------------------------------------------------------------------

    def list_files(self) -> str:
        entries = [f"{self.relative(p)} ({p.stat().st_size} bytes)" for p in self.files()]
        if not entries:
            return "The workspace is empty."
        more = len(entries) - LIST_LIMIT
        listing = "\n".join(entries[:LIST_LIMIT])
        return listing + (f"\n... and {more} more" if more > 0 else "")

    def read_file(self, path: str) -> str:
        target = self.resolve(path)
        if not target.is_file():
            raise WorkspaceError(f"{path!r} does not exist in the workspace")
        data = target.read_bytes()
        if b"\x00" in data[:8192]:
            raise WorkspaceError(f"{path!r} is a binary file ({len(data)} bytes)")
        text = data[:READ_LIMIT_BYTES].decode(errors="replace")
        if len(data) > READ_LIMIT_BYTES:
            text += f"\n[... truncated at {READ_LIMIT_BYTES} of {len(data)} bytes]"
        return text

    def write_file(self, path: str, content: str) -> str:
        target = self.resolve(path)
        data = content.encode()
        if len(data) > WRITE_LIMIT_BYTES:
            raise WorkspaceError(f"content is {len(data)} bytes; the limit is {WRITE_LIMIT_BYTES}")
        if target == self.root or target.is_dir():
            raise WorkspaceError(f"{path!r} is a directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o666)
        return f"wrote {self.relative(target)} ({len(data)} bytes)"

    def read_reference(self, name: str) -> str:
        stem = name.strip().removesuffix(".md")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", stem):
            raise WorkspaceError(f"unknown reference {name!r}")
        path = self.skill_dir / "references" / f"{stem}.md"
        if not path.is_file():
            available = sorted(p.stem for p in (self.skill_dir / "references").glob("*.md"))
            raise WorkspaceError(
                f"unknown reference {name!r}; available: {', '.join(available) or 'none'}"
            )
        return path.read_text()

    def node_command(self, args: Sequence[str]) -> list[str]:
        """Container argv for ``node <args>``; refuses node flags and paths outside the
        workspace and the skill."""
        if not args or len(args) > MAX_ARGS or not all(isinstance(a, str) for a in args):
            raise WorkspaceError("args must be 1 to 64 strings: a script, then its arguments")
        script, *rest = args
        if script.startswith("-"):
            raise WorkspaceError(
                "only scripts may be run; node flags such as -e, --eval or --require are refused"
            )
        command = [self._script_path(script)]
        for arg in rest:
            self._check_arg(arg)
            command.append(arg)
        return command

    def _script_path(self, script: str) -> str:
        if not script.endswith(SCRIPT_SUFFIXES):
            raise WorkspaceError(f"{script!r} is not a .mjs, .js or .cjs script")
        if script.startswith(SKILL_MOUNT + "/"):
            relative = script[len(SKILL_MOUNT) + 1 :]
            target = (self.skill_dir / relative).resolve()
            if (
                not relative.startswith(SKILL_SCRIPT_DIRS)
                or not target.is_relative_to(self.skill_dir)
                or not target.is_file()
            ):
                raise WorkspaceError(f"{script!r} is not one of the skill's scripts")
            return f"{SKILL_MOUNT}/{target.relative_to(self.skill_dir).as_posix()}"
        target = self.resolve(script)
        if not target.is_file():
            raise WorkspaceError(f"{script!r} does not exist in the workspace")
        return f"{WORKSPACE_MOUNT}/{self.relative(target)}"

    def _check_arg(self, arg: str) -> None:
        if "\x00" in arg:
            raise WorkspaceError("arguments may not contain NUL bytes")
        value = arg.removeprefix("file://")
        if ".." in Path(value).parts:
            raise WorkspaceError(f"{arg!r}: '..' is not allowed in paths")
        if value.startswith("/") and not (
            value == WORKSPACE_MOUNT or value.startswith((WORKSPACE_MOUNT + "/", SKILL_MOUNT + "/"))
        ):
            raise WorkspaceError(f"{arg!r} is outside /workspace and /skill")

    def _png_state(self) -> dict[Path, tuple[int, int]]:
        state = {}
        for path in self.files():
            if path.suffix.lower() == ".png":
                stat = path.stat()
                state[path] = (stat.st_mtime_ns, stat.st_size)
        return state

    async def run_node(self, args: Sequence[str]) -> tuple[str, list[Path]]:
        """Run ``node`` in the sandbox; returns the report text and the PNGs it wrote."""
        command = self.node_command(args)
        before = self._png_state()
        run = await self._run_node(command)
        after = self._png_state()
        written = sorted(
            (p for p, state in after.items() if before.get(p) != state),
            key=lambda p: after[p][0],
            reverse=True,
        )
        status = (
            "timed out and was stopped" if run.timed_out else f"exited with code {run.exit_code}"
        )
        lines = [f"node {' '.join(command)} {status} after {run.duration_s:.1f} s"]
        if run.stdout.strip():
            lines.append("stdout:\n" + _tail(run.stdout))
        if run.stderr.strip():
            lines.append("stderr:\n" + _tail(run.stderr))
        if written:
            lines.append("PNG files written: " + ", ".join(self.relative(p) for p in written))
        return "\n".join(lines), written


def _image_block(path: Path) -> dict[str, Any] | None:
    data = path.read_bytes()
    if not data.startswith(PNG_MAGIC) or len(data) > IMAGE_LIMIT_BYTES:
        return None
    return {"image": {"format": "png", "source": {"bytes": data}}}


class ImageRelay(HookProvider):
    """Moves images out of tool results into the user message that carries them.

    The provider transports send tool results as text only, while user messages may carry
    images. Older images are replaced by a note so the conversation stays small.
    """

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(MessageAddedEvent, self.on_message)

    def on_message(self, event: MessageAddedEvent) -> None:
        message: Message = event.message
        images: list[dict[str, Any]] = []
        for block in message.get("content", []):
            result = block.get("toolResult")
            if not result:
                continue
            kept = []
            for item in result.get("content", []):
                if "image" in item:
                    images.append(dict(item))
                else:
                    kept.append(item)
            result["content"] = kept
        if not images:
            return
        for older in event.agent.messages:
            if older is message:
                continue
            content = older.get("content", [])
            for index, block in enumerate(content):
                if "image" in block:
                    content[index] = {"text": "[earlier image omitted]"}
        extra = [{"text": "Images written by the last run_node call:"}, *images]
        message["content"] = [*message.get("content", []), *cast(list[ContentBlock], extra)]


def make_figure_agent(
    model: Model,
    workspace: FigureWorkspace,
    *,
    brief: FigureBrief,
    storyboard: Storyboard,
    retry_strategy: Any = None,
) -> Agent:
    """The Anatomy session: ``SKILL.md`` plus the brief as system prompt, workspace tools."""

    @tool(name="list_files")
    def list_files() -> str:
        """List the files in your workspace (/workspace) with their sizes."""
        return workspace.list_files()

    @tool(name="read_file")
    def read_file(path: str) -> str:
        """Read a text file from your workspace.

        Args:
            path: Path relative to /workspace.
        """
        return workspace.read_file(path)

    @tool(name="write_file")
    def write_file(path: str, content: str) -> str:
        """Create or replace a file in your workspace (parent directories are created).

        Args:
            path: Path relative to /workspace.
            content: The complete file content.
        """
        return workspace.write_file(path, content)

    @tool(name="read_reference")
    def read_reference(name: str) -> str:
        """Read one of the skill's reference documents (references/<name>.md).

        Args:
            name: motion, verify, 3d, webgl, craft, kit, react or walkthrough.
        """
        return workspace.read_reference(name)

    @tool(name="run_node")
    async def run_node(args: list[str]) -> dict[str, Any]:
        """Run `node <args>` in the sandbox (no network, cwd /workspace). The first argument is
        a script: a workspace file such as build.mjs, or a skill script such as
        /skill/scripts/capture.mjs. PNG files the run writes are shown to you.

        Args:
            args: The script path followed by its arguments.
        """
        text, written = await workspace.run_node(args)
        content: list[dict[str, Any]] = [{"text": text}]
        for path in written[:IMAGES_PER_CALL]:
            block = _image_block(path)
            if block is not None:
                content.append(block)
        return {"status": "success", "content": content}

    skill_md = (workspace.skill_dir / "SKILL.md").read_text()
    return Agent(
        model=model,
        tools=[list_files, read_file, write_file, read_reference, run_node],
        system_prompt=figure_system(skill_md, brief, storyboard),
        callback_handler=None,
        hooks=[ImageRelay()],
        tool_executor=SequentialToolExecutor(),
        **({} if retry_strategy is None else {"retry_strategy": retry_strategy}),
    )


# --- session ----------------------------------------------------------------------------------


@dataclass
class FigureSession:
    stop_reason: str
    turns: int
    timed_out: bool
    cancelled: bool
    messages: Messages = field(default_factory=list)

    def transcript(self) -> list[str]:
        return transcript_lines(self.messages)

    def transcript_tail(self, max_chars: int = 6000) -> str:
        return tail_lines(self.transcript(), max_chars)


async def run_figure_session(
    agent: Agent,
    prompt: str,
    *,
    max_turns: int,
    timeout_s: float,
    cancelled: Callable[[], bool] = lambda: False,
    poll_s: float = 1.0,
) -> FigureSession:
    """Run the session to its end, its turn budget, its wall-clock limit, or cancellation.

    Limits stop the loop at the next turn boundary (Strands ``limits`` and ``cancel_signal``),
    so the transcript is complete. Provider errors propagate to the caller.
    """
    stop = threading.Event()
    timed_out = False
    deadline = time.monotonic() + timeout_s

    async def watchdog() -> None:
        nonlocal timed_out
        while not stop.is_set():
            if cancelled():
                stop.set()
                return
            if time.monotonic() >= deadline:
                timed_out = True
                stop.set()
                return
            await asyncio.sleep(poll_s)

    guard = asyncio.create_task(watchdog())
    try:
        result = await agent.invoke_async(prompt, limits={"turns": max_turns}, cancel_signal=stop)
    finally:
        guard.cancel()
    return FigureSession(
        stop_reason=str(result.stop_reason),
        turns=result.metrics.cycle_count,
        timed_out=timed_out,
        cancelled=cancelled(),
        messages=agent.messages,
    )


def _short(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def transcript_lines(messages: Iterable[Message]) -> list[str]:
    """One line per text, tool call, tool result or image in the conversation."""
    lines = []
    for message in messages:
        role = message.get("role", "?")
        for block in message.get("content", []):
            if "text" in block:
                lines.append(f"{role}: {_short(block['text'], 1200)}")
            elif "toolUse" in block:
                use = block["toolUse"]
                lines.append(f"{role} -> {use['name']}({_short(use.get('input', {}), 400)})")
            elif "toolResult" in block:
                result = block["toolResult"]
                text = "\n".join(c["text"] for c in result.get("content", []) if "text" in c)
                lines.append(f"tool <- [{result.get('status', '?')}] {_short(text, 800)}")
            elif "image" in block:
                lines.append(f"{role}: [image]")
    return lines


def tail_lines(lines: Sequence[str], max_chars: int) -> str:
    kept: list[str] = []
    size = 0
    for line in reversed(lines):
        if size + len(line) + 1 > max_chars and kept:
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join(reversed(kept))


# --- collection -------------------------------------------------------------------------------


@dataclass(frozen=True)
class FigureOutputs:
    html: Path | None
    png: Path | None
    sources: list[Path]


def is_standalone_html(text: str) -> bool:
    lowered = text.lower()
    has_figure = "<svg" in lowered or "<canvas" in lowered
    return has_figure and "<html" in lowered and not EXTERNAL_REF.search(text)


def collect_outputs(workspace: FigureWorkspace) -> FigureOutputs:
    """The figure by content: a standalone HTML page (``figure.html`` preferred, else the largest
    one, light variants last), a PNG preview (``preview.png`` preferred, else the newest PNG),
    and the build source files."""
    html_candidates = []
    pngs = []
    sources = []
    for path in workspace.files():
        suffix = path.suffix.lower()
        if suffix in {".html", ".htm"}:
            text = path.read_text(errors="replace")
            if is_standalone_html(text):
                html_candidates.append(path)
        elif suffix == ".png":
            with path.open("rb") as handle:
                if handle.read(8) == PNG_MAGIC:
                    pngs.append(path)
        elif suffix in SOURCE_SUFFIXES and path.stat().st_size <= SOURCE_LIMIT_BYTES:
            sources.append(path)

    def html_rank(path: Path) -> tuple[bool, bool, int]:
        return (path.name != "figure.html", "light" in path.stem, -path.stat().st_size)

    def png_rank(path: Path) -> tuple[bool, int]:
        return (path.name != "preview.png", -path.stat().st_mtime_ns)

    html = min(html_candidates, key=html_rank) if html_candidates else None
    png = min(pngs, key=png_rank) if pngs else None
    return FigureOutputs(html, png, sources)
