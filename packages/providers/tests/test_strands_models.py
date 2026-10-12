import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from strands import Agent, tool
from strands.types.exceptions import ModelThrottledException

from brainbench_providers.store import Block
from brainbench_providers.strands_models import (
    AnthropicOAuthModel,
    CodexOAuthModel,
    ProviderRetryStrategy,
    ProviderThrottledException,
    build_model,
)
from brainbench_providers.testing import Replay

from .conftest import Env

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text())
    return data


def anthropic_model(env: Env, r: Replay, **kw: Any) -> AnthropicOAuthModel:
    return AnthropicOAuthModel(
        spec=env.registry.get("anthropic"), pool=env.pool, http=r.http_factory(), **kw
    )


class Topic(BaseModel):
    title: str
    minutes: int


class TopicList(BaseModel):
    """Topics extracted from the material."""

    topics: list[Topic]


async def test_stream_maps_wire_events_and_records_usage(env: Env) -> None:
    await env.connect()
    r = Replay.load(FIXTURES / "anthropic/stream_text.json", fallback=env.fake.handle)
    model = anthropic_model(env, r, task_class="tutoring")
    events = [
        e
        async for e in model.stream(
            [{"role": "user", "content": [{"text": "Say hello to the engineer."}]}]
        )
    ]
    assert r.problems == []
    assert events[0] == {"messageStart": {"role": "assistant"}}
    assert {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Hello"}}} in events
    assert {"messageStop": {"stopReason": "end_turn"}} in events
    assert events[-1]["metadata"]["usage"] == {
        "inputTokens": 25,
        "outputTokens": 9,
        "totalTokens": 46,
        "cacheReadInputTokens": 12,
    }
    ((record, _),) = env.store.usage
    assert (record.provider, record.model, record.task_class) == (
        "anthropic",
        "claude-opus-5-5",
        "tutoring",
    )
    assert (record.input_tokens, record.output_tokens) == (37, 9)


async def test_agent_runs_a_native_tool_loop(env: Env) -> None:
    await env.connect()
    tool_turn = fixture("anthropic/stream_tool_use.json")["exchanges"][0]
    text_turn = fixture("anthropic/stream_text.json")["exchanges"][0]
    url = text_turn["request"]["url"]
    second_request = {
        "url": url,
        "json": {
            "messages": [
                {"role": "user"},
                {"role": "assistant"},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_01fixture",
                            "content": "2 chunks about raft log",
                        }
                    ],
                },
            ]
        },
    }
    r = Replay(
        {
            "exchanges": [
                {"request": {"url": url}, "response": tool_turn["response"]},
                {"request": second_request, "response": text_turn["response"]},
            ]
        },
        fallback=env.fake.handle,
    )
    seen: list[str] = []

    @tool
    def search_chunks(query: str) -> str:
        """Search the engineer's material."""
        seen.append(query)
        return f"2 chunks about {query}"

    agent = Agent(model=anthropic_model(env, r), tools=[search_chunks], callback_handler=None)
    result = await agent.invoke_async("What is a raft log?")
    assert r.problems == [] and r.remaining == 0
    assert seen == ["raft log"]
    assert str(result).strip() == "Hello, engineer."
    tools_sent = json.loads(r.served[0].content)["tools"]
    assert [t["name"] for t in tools_sent] == ["_search_chunks"]
    assert len(env.store.usage) == 2


async def test_structured_output_validates_the_answer(env: Env) -> None:
    await env.connect()
    r = Replay.load(FIXTURES / "anthropic/structured.json", fallback=env.fake.handle)
    model = anthropic_model(env, r)
    events = [
        e
        async for e in model.structured_output(
            TopicList, [{"role": "user", "content": [{"text": "List the topics."}]}]
        )
    ]
    assert events[-1]["output"] == TopicList(topics=[Topic(title="Raft", minutes=45)])
    assert len(env.store.usage) == 1


async def test_codex_structured_output(env: Env) -> None:
    await env.connect(provider="openai-codex")
    r = Replay.load(FIXTURES / "openai-codex/structured.json", fallback=env.fake.handle)
    model = build_model(env.registry.get("openai-codex"), pool=env.pool, http=r.http_factory())
    assert isinstance(model, CodexOAuthModel)
    events = [
        e
        async for e in model.structured_output(
            TopicList, [{"role": "user", "content": [{"text": "List the topics."}]}]
        )
    ]
    assert events[-1]["output"].topics[0].title == "Raft"


async def test_rate_limit_becomes_model_throttled_exception(env: Env) -> None:
    await env.connect()
    r = Replay.load(FIXTURES / "anthropic/rate_limited.json", fallback=env.fake.handle)
    model = anthropic_model(env, r)
    with pytest.raises(ModelThrottledException) as info:
        async for _ in model.stream([{"role": "user", "content": [{"text": "hi"}]}]):
            pass
    exc = info.value
    assert isinstance(exc, ProviderThrottledException)
    assert exc.reset_at == datetime.fromtimestamp(1791900000, UTC) and exc.scope == "5h"
    assert ProviderRetryStrategy().is_retryable(exc)  # another account may serve the retry


async def test_all_accounts_blocked_is_not_retried_in_the_loop(env: Env) -> None:
    stored = await env.connect()
    reset = datetime.now(UTC) + timedelta(hours=2)
    await env.store.add_block(Block(stored.id, "5h", reset))
    env.clock.now = datetime.now(UTC)
    r = Replay({"exchanges": []}, fallback=env.fake.handle)
    with pytest.raises(ProviderThrottledException) as info:
        async for _ in anthropic_model(env, r).stream(
            [{"role": "user", "content": [{"text": "x"}]}]
        ):
            pass
    assert info.value.scope == "all-accounts" and info.value.reset_at == reset
    assert not ProviderRetryStrategy().is_retryable(info.value)
    soon = ProviderThrottledException(
        "x", reset_at=datetime.now(UTC) + timedelta(seconds=30), scope="all-accounts"
    )
    assert ProviderRetryStrategy().is_retryable(soon)


def test_config_and_unknown_model(env: Env) -> None:
    model = build_model(env.registry.get("anthropic"), pool=env.pool, http=env.fake.http_factory())
    assert model.get_config()["model_id"] == "claude-opus-5-5"
    assert model.context_window_limit == 1_000_000
    model.update_config(model_id="claude-haiku-5-5")
    assert model.model_spec.name == "Claude Haiku 5.5"
    with pytest.raises(Exception, match="no model"):
        model.update_config(model_id="gpt-6")
    assert "token" not in repr(model)
