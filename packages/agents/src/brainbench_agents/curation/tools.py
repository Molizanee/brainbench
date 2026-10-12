"""``@tool`` functions the curation agents may call.

The package never imports the app: each factory receives the async callable that does the work
(a database query in the worker) and returns a Strands tool bound to it.
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

__lazy_modules__ = {"strands", "strands.types.tools"}
from strands import tool
from strands.types.tools import AgentTool

# A chunk hit as plain data: chunk_id, source_id, source_title, location, text, score.
type ChunkHitDict = dict[str, Any]
type SearchFn = Callable[[str, int], Awaitable[list[ChunkHitDict]]]
type ProfileLoader = Callable[[], Awaitable[dict[str, Any]]]
type SessionsLoader = Callable[[list[str]], Awaitable[dict[str, list[dict[str, Any]]]]]

MAX_SEARCH_K = 20


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def make_search_chunks(search: SearchFn) -> AgentTool:
    """A ``search_chunks`` tool over the engineer's ingested material."""

    @tool(name="search_chunks")
    async def search_chunks(query: str, k: int = 5) -> str:
        """Semantic search over the engineer's ingested material.

        Args:
            query: What to look for, in plain words.
            k: How many chunks to return, 1 to 20.
        """
        hits = await search(query, max(1, min(int(k), MAX_SEARCH_K)))
        return _json(hits)

    return search_chunks


def make_get_profile(load: ProfileLoader) -> AgentTool:
    """A ``get_profile`` tool returning the learner profile (skills, targets, preferences)."""

    @tool(name="get_profile")
    async def get_profile() -> str:
        """The engineer's learning profile: current skills, target skills, budget, preferences."""
        return _json(await load())

    return get_profile


def make_get_sessions(load: SessionsLoader) -> AgentTool:
    """A ``get_sessions`` tool returning logged study sessions per topic."""

    @tool(name="get_sessions")
    async def get_sessions(topics: list[str]) -> str:
        """Study sessions the engineer logged on the given topics, oldest first.

        Args:
            topics: Topic keys (for example T4) from the prompt.
        """
        return _json(await load(list(topics)))

    return get_sessions
