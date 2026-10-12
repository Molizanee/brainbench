"""The tutor agent factory (design D10).

``make_tutor_agent(model, context, tools)`` returns a Strands ``Agent`` with the tutor's system
prompt and four tools built from the injected callables. Streaming is the caller's: pass a
``callback_handler`` to receive text deltas (``data=...``) as the model produces them.
"""

import json
from collections.abc import Callable
from typing import Any

from brainbench_agents.tutor.context import ChunkRef, TutorContext, TutorTools
from brainbench_agents.tutor.ledger import CitationLedger
from brainbench_agents.tutor.prompt import build_system_prompt, format_passages

__lazy_modules__ = {
    "strands",
    "strands.event_loop._retry",
    "strands.models.model",
    "strands.types.content",
}
from strands import Agent, tool
from strands.event_loop._retry import ModelRetryStrategy
from strands.models.model import Model
from strands.types.content import Messages

TOOL_NAMES = ("search_chunks", "get_topic", "get_plan_summary", "get_profile")


async def retrieve(
    tools: TutorTools, context: TutorContext, query: str, *, scope: str = "context"
) -> list[ChunkRef]:
    """Up to ``tools.k`` passages scoring at least ``tools.min_score``: the context topic's own
    chunks first, then the best of every source; each chunk once."""
    found: dict[str, ChunkRef] = {}
    if scope == "context" and context.kind == "topic" and context.topic_id:
        for ref in await tools.search_chunks(query, tools.k, context.topic_id):
            if ref.score >= tools.min_score:
                found.setdefault(ref.chunk_id, ref)
    if len(found) < tools.k:
        for ref in await tools.search_chunks(query, tools.k, None):
            if ref.score >= tools.min_score and len(found) < tools.k:
                found.setdefault(ref.chunk_id, ref)
    return list(found.values())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def build_tools(tools: TutorTools, context: TutorContext, ledger: CitationLedger) -> list[Any]:
    """The four Strands tools over the injected callables; retrieved passages go to ``ledger``."""

    @tool(name="search_chunks")
    async def search_chunks(query: str, scope: str = "context") -> str:
        """Search the engineer's ingested material for passages about a query.

        Args:
            query: What to look for, phrased as a question or key phrase.
            scope: "context" searches the current topic's material first, then every source;
                "all" searches every source.
        """
        ledger.searches += 1
        refs = await retrieve(tools, context, query, scope="all" if scope == "all" else "context")
        ledger.add(refs)
        return format_passages(refs)

    @tool(name="get_topic")
    async def get_topic(topic_id: str = "") -> str:
        """A topic of the study plan: title, summary, status, prerequisites, generated assets.

        Args:
            topic_id: The topic id; empty for the topic the engineer is viewing.
        """
        target = topic_id or context.topic_id
        if not target:
            return "No topic is open; pass a topic id from get_plan_summary."
        topic = await tools.get_topic(target)
        return _json(topic) if topic is not None else f"No topic with id {target}."

    @tool(name="get_plan_summary")
    async def get_plan_summary() -> str:
        """The current study plan: topics in order with weeks, status, and prerequisites."""
        plan = await tools.get_plan_summary()
        return _json(plan) if plan is not None else "The engineer has no study plan yet."

    @tool(name="get_profile")
    async def get_profile() -> str:
        """The learner profile: skills with levels, target skills, time budget, preferences."""
        profile = await tools.get_profile()
        return _json(profile) if profile is not None else "The engineer has no profile yet."

    return [search_chunks, get_topic, get_plan_summary, get_profile]


def make_tutor_agent(
    model: Model,
    context: TutorContext,
    tools: TutorTools,
    *,
    ledger: CitationLedger | None = None,
    messages: Messages | None = None,
    callback_handler: Callable[..., Any] | None = None,
    retry_strategy: ModelRetryStrategy | None = None,
) -> Agent:
    """A tutor ``Agent`` for one reply.

    ``messages`` is the thread history (alternating user and assistant text). Passages the
    tools retrieve are recorded in ``ledger`` so the caller can recognize citations.
    """
    ledger = ledger if ledger is not None else CitationLedger()
    options: dict[str, Any] = {}
    if retry_strategy is not None:
        options["retry_strategy"] = retry_strategy
    return Agent(
        model=model,
        messages=messages or [],
        tools=build_tools(tools, context, ledger),
        system_prompt=build_system_prompt(context),
        callback_handler=callback_handler,
        name="tutor",
        **options,
    )
