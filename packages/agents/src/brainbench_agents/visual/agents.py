"""Strands agents for the structured visual steps (design D7): ``storyboard``, ``figure_brief``,
``manim_script`` and ``script_repair``.

Each step is one agent invocation ending in a Pydantic structured output. Data comes in through
injected callables (``search``) or plain values, never from ``app``. The caller passes the
routed model and, for jobs, ``ProviderRetryStrategy`` so throttles reach the job layer.
"""

from collections.abc import Awaitable, Callable, Collection, Sequence
from typing import Any

from brainbench_agents.visual.models import (
    ChunkRef,
    FigureBrief,
    ManimScript,
    Storyboard,
    StoryboardScene,
)
from brainbench_agents.visual.prompts import (
    FIGURE_BRIEF_SYSTEM,
    StoryboardRequest,
    chunk_block,
    figure_brief_prompt,
    grounding_correction,
    manim_prompt,
    manim_system,
    repair_prompt,
    repair_system,
    storyboard_prompt,
    storyboard_system,
)

__lazy_modules__ = {"strands", "strands.agent.agent_result", "strands.models.model"}
from strands import Agent, tool
from strands.agent.agent_result import AgentResult
from strands.models.model import Model

type SearchFn = Callable[[str, int], Awaitable[Sequence[ChunkRef]]]

SEARCH_K_MAX = 20
LOG_TAIL_LINES = 60


class NoStructuredOutput(RuntimeError):
    """The agent stopped without producing its structured output."""


class StoryboardNotGrounded(ValueError):
    """Scenes still cite no chunk the topic references after the allowed corrections."""


def _agent_kwargs(retry_strategy: Any) -> dict[str, Any]:
    return {} if retry_strategy is None else {"retry_strategy": retry_strategy}


def _structured[T](result: AgentResult, output_model: type[T]) -> T:
    output = result.structured_output
    if not isinstance(output, output_model):
        raise NoStructuredOutput(
            f"{output_model.__name__} missing (stop reason {result.stop_reason})"
        )
    return output


# --- storyboard ------------------------------------------------------------------------------


def make_storyboard_agent(
    model: Model, search: SearchFn, request: StoryboardRequest, *, retry_strategy: Any = None
) -> Agent:
    """Agent with the ``search_chunks`` tool; ``search(query, k)`` is scoped by the caller."""

    @tool(name="search_chunks")
    async def search_chunks(query: str, k: int = 8) -> str:
        """Search the source chunks this topic references. Returns chunk ids, sources and text.

        Args:
            query: What to look for, in a few words.
            k: How many chunks to return (1 to 20).
        """
        hits = await search(query, max(1, min(int(k), SEARCH_K_MAX)))
        return chunk_block(hits) or "No chunk matched; try other words."

    return Agent(
        model=model,
        tools=[search_chunks],
        system_prompt=storyboard_system(request),
        callback_handler=None,
        **_agent_kwargs(retry_strategy),
    )


def ungrounded(storyboard: Storyboard, allowed: Collection[str]) -> dict[str, list[str]]:
    """Scenes that cite no allowed chunk, with what they cite."""
    return {
        scene.id: list(scene.chunk_ids)
        for scene in storyboard.scenes
        if not any(chunk in allowed for chunk in scene.chunk_ids)
    }


def keep_allowed_citations(storyboard: Storyboard, allowed: Collection[str]) -> Storyboard:
    """Drop citations of chunks the topic does not reference (every scene keeps at least one)."""
    scenes = [
        StoryboardScene.model_validate(
            {**scene.model_dump(), "chunk_ids": [c for c in scene.chunk_ids if c in allowed]}
        )
        for scene in storyboard.scenes
    ]
    return Storyboard(title=storyboard.title, scenes=scenes)


async def generate_storyboard(
    model: Model,
    search: SearchFn,
    request: StoryboardRequest,
    *,
    allowed_chunk_ids: Collection[str],
    max_corrections: int = 2,
    retry_strategy: Any = None,
) -> Storyboard:
    """Storyboard whose every scene cites at least one of ``allowed_chunk_ids``.

    A storyboard with ungrounded scenes is sent back with the list of allowed ids up to
    ``max_corrections`` times; after that :class:`StoryboardNotGrounded` is raised.
    """
    allowed = list(dict.fromkeys(allowed_chunk_ids))
    agent = make_storyboard_agent(model, search, request, retry_strategy=retry_strategy)
    result = await agent.invoke_async(
        storyboard_prompt(request), structured_output_model=Storyboard
    )
    storyboard = _structured(result, Storyboard)
    for _ in range(max_corrections):
        bad = ungrounded(storyboard, allowed)
        if not bad:
            break
        result = await agent.invoke_async(
            grounding_correction(bad, allowed), structured_output_model=Storyboard
        )
        storyboard = _structured(result, Storyboard)
    bad = ungrounded(storyboard, allowed)
    if bad:
        raise StoryboardNotGrounded(
            "scenes without a chunk referenced by the topic: " + ", ".join(sorted(bad))
        )
    return keep_allowed_citations(storyboard, allowed)


# --- figure brief ----------------------------------------------------------------------------


def make_figure_brief_agent(model: Model, *, retry_strategy: Any = None) -> Agent:
    return Agent(
        model=model,
        tools=[],
        system_prompt=FIGURE_BRIEF_SYSTEM,
        callback_handler=None,
        **_agent_kwargs(retry_strategy),
    )


async def generate_figure_brief(
    model: Model,
    storyboard: Storyboard,
    chunks: Sequence[ChunkRef],
    *,
    feedback: str | None = None,
    retry_strategy: Any = None,
) -> FigureBrief:
    agent = make_figure_brief_agent(model, retry_strategy=retry_strategy)
    result = await agent.invoke_async(
        figure_brief_prompt(storyboard, chunks, feedback), structured_output_model=FigureBrief
    )
    return _structured(result, FigureBrief)


# --- manim script and repair -----------------------------------------------------------------


def make_manim_script_agent(
    model: Model, storyboard: Storyboard, *, retry_strategy: Any = None
) -> Agent:
    return Agent(
        model=model,
        tools=[],
        system_prompt=manim_system(storyboard),
        callback_handler=None,
        **_agent_kwargs(retry_strategy),
    )


async def generate_manim_script(
    model: Model,
    storyboard: Storyboard,
    *,
    language: str,
    previous_script: str | None = None,
    feedback: str | None = None,
    retry_strategy: Any = None,
) -> ManimScript:
    agent = make_manim_script_agent(model, storyboard, retry_strategy=retry_strategy)
    result = await agent.invoke_async(
        manim_prompt(
            storyboard, language=language, previous_script=previous_script, feedback=feedback
        ),
        structured_output_model=ManimScript,
    )
    return _structured(result, ManimScript)


def log_tail(render_log: str, lines: int = LOG_TAIL_LINES) -> str:
    return "\n".join(render_log.splitlines()[-lines:])


def make_script_repair_agent(model: Model, render_log: str, *, retry_strategy: Any = None) -> Agent:
    """Agent with the ``read_render_log`` tool over the failed render's log."""
    lines = render_log.splitlines()

    @tool(name="read_render_log")
    def read_render_log(start_line: int = 1, max_lines: int = 200) -> str:
        """Read lines of the full render log (the sandbox's stderr and stdout), numbered from 1.

        Args:
            start_line: First line to return (1-based; negative counts from the end).
            max_lines: How many lines to return (at most 400).
        """
        count = max(1, min(int(max_lines), 400))
        start = len(lines) + start_line if start_line < 0 else max(start_line - 1, 0)
        start = max(0, min(start, len(lines)))
        chunk = lines[start : start + count]
        numbered = "\n".join(f"{start + i + 1}: {line}" for i, line in enumerate(chunk))
        return f"lines {start + 1}-{start + len(chunk)} of {len(lines)}\n{numbered}"

    return Agent(
        model=model,
        tools=[read_render_log],
        system_prompt=repair_system(),
        callback_handler=None,
        **_agent_kwargs(retry_strategy),
    )


async def repair_manim_script(
    model: Model,
    storyboard: Storyboard,
    script: str,
    render_log: str,
    *,
    problems: Sequence[str] = (),
    attempt: int = 1,
    retry_strategy: Any = None,
) -> ManimScript:
    agent = make_script_repair_agent(model, render_log, retry_strategy=retry_strategy)
    result = await agent.invoke_async(
        repair_prompt(storyboard, script, log_tail(render_log), problems, attempt),
        structured_output_model=ManimScript,
    )
    return _structured(result, ManimScript)
