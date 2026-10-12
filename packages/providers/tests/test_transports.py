"""Contract tests: each transport against recorded exchanges (tests/fixtures/<provider>/)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import xxhash

from brainbench_providers.errors import AuthenticationFailed, ProviderRateLimited
from brainbench_providers.testing import Replay
from brainbench_providers.transports.anthropic_messages import AnthropicMessagesTransport
from brainbench_providers.transports.base import (
    BlockStart,
    BlockStop,
    ChatRequest,
    MessageStart,
    MessageStop,
    TextDelta,
    ToolChoice,
    ToolDef,
    ToolInputDelta,
    Usage,
    WireEvent,
)
from brainbench_providers.transports.openai_responses import OpenAIResponsesTransport

from .conftest import Env

FIXTURES = Path(__file__).parent / "fixtures"
RESET = datetime.fromtimestamp(1791900000, UTC)
SEARCH = ToolDef(
    "search_chunks",
    "Search the engineer's material.",
    {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
)
TOPICS_SCHEMA = {"type": "object", "properties": {"topics": {"type": "array"}}}


def hello(env: Env, provider: str, **kw: Any) -> ChatRequest:
    spec = env.registry.get(provider)
    return ChatRequest(
        model=spec.default_model,
        messages=[{"role": "user", "content": [{"text": "Say hello to the engineer."}]}],
        **kw,
    )


def replay(env: Env, name: str) -> Replay:
    return Replay.load(FIXTURES / name, fallback=env.fake.handle)


def anthropic(env: Env, r: Replay) -> AnthropicMessagesTransport:
    return AnthropicMessagesTransport(
        env.registry.get("anthropic"), http=r.http_factory(), clock=env.clock
    )


def codex(env: Env, r: Replay) -> OpenAIResponsesTransport:
    return OpenAIResponsesTransport(
        env.registry.get("openai-codex"), http=r.http_factory(), clock=env.clock
    )


async def collect(stream: Any) -> list[WireEvent]:
    return [event async for event in stream]


def text_of(events: list[WireEvent]) -> str:
    return "".join(e.text for e in events if isinstance(e, TextDelta))


def verify(r: Replay) -> None:
    assert r.problems == []
    assert r.remaining == 0


# --- Anthropic Messages ------------------------------------------------------------------------


async def test_anthropic_streams_text_with_claude_code_fingerprint(env: Env) -> None:
    await env.connect()
    r = replay(env, "anthropic/stream_text.json")
    handle = await env.pool.lease("anthropic")
    events = await collect(
        anthropic(env, r).stream(handle, hello(env, "anthropic", system="Be brief."))
    )
    verify(r)
    assert isinstance(events[0], MessageStart)
    assert events[1] == BlockStart(0, "text")
    assert text_of(events) == "Hello, engineer."
    assert events[-2:] == [MessageStop("end_turn"), Usage(25, 9, 12, 0)]
    body = json.loads(r.served[0].content)
    system = [b["text"] for b in body["system"]]
    assert system[0].startswith("x-anthropic-billing-header: cc_version=2.1.280.")
    assert system[1:] == ["You are Claude Code, Anthropic's official CLI for Claude.", "Be brief."]
    raw = r.served[0].content
    cch = raw[raw.index(b"cch=") + 4 : raw.index(b"cch=") + 9]
    assert cch != b"00000"
    unpatched = raw.replace(b"cch=" + cch, b"cch=00000")
    seed = env.registry.get("anthropic").transport.billing_header.cch_seed  # type: ignore[union-attr]
    assert f"{xxhash.xxh64_intdigest(unpatched, seed=seed) & 0xFFFFF:05x}".encode() == cch
    windows = {w.window: w.used_pct for w in await env.store.usage_windows()}
    assert windows == {"5h": 31.0, "7d": 12.0}


async def test_anthropic_native_tool_use_strips_the_wire_prefix(env: Env) -> None:
    await env.connect()
    r = replay(env, "anthropic/stream_tool_use.json")
    handle = await env.pool.lease("anthropic")
    request = hello(env, "anthropic", tools=[SEARCH], tool_choice=ToolChoice("auto"))
    events = await collect(anthropic(env, r).stream(handle, request))
    verify(r)
    assert BlockStart(1, "tool_use", "toolu_01fixture", "search_chunks") in events
    arguments = "".join(e.partial_json for e in events if isinstance(e, ToolInputDelta))
    assert json.loads(arguments) == {"query": "raft log"}
    assert BlockStop(1) in events and MessageStop("tool_use") in events


async def test_anthropic_replays_tool_history_with_prefixed_names(env: Env) -> None:
    transport = AnthropicMessagesTransport(
        env.registry.get("anthropic"), http=env.fake.http_factory()
    )
    request = ChatRequest(
        model=env.registry.get("anthropic").default_model,
        messages=[
            {"role": "user", "content": [{"text": "What is raft?"}]},
            {
                "role": "assistant",
                "content": [
                    {"toolUse": {"toolUseId": "t1", "name": "search_chunks", "input": {"q": 1}}}
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "t1",
                            "status": "success",
                            "content": [{"json": {"n": 0}}],
                        }
                    }
                ],
            },
        ],
    )
    body = json.loads(transport.body(request))
    assert body["messages"][1]["content"][0] == {
        "type": "tool_use",
        "id": "t1",
        "name": "_search_chunks",
        "input": {"q": 1},
    }
    assert body["messages"][2]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "t1",
        "content": '{"n": 0}',
        "is_error": False,
    }


async def test_anthropic_structured_output_forces_one_tool(env: Env) -> None:
    await env.connect()
    r = replay(env, "anthropic/structured.json")
    handle = await env.pool.lease("anthropic")
    value, usage = await anthropic(env, r).structured(
        handle, hello(env, "anthropic"), name="TopicList", schema=TOPICS_SCHEMA
    )
    verify(r)
    assert value == {"topics": [{"title": "Raft", "minutes": 45}]}
    assert usage == Usage(80, 22, 0, 0)


async def test_anthropic_401_refreshes_once_and_retries(env: Env) -> None:
    stored = await env.connect()
    r = replay(env, "anthropic/unauthorized_then_ok.json")
    handle = await env.pool.lease("anthropic")
    events = await collect(anthropic(env, r).stream(handle, hello(env, "anthropic")))
    verify(r)
    assert text_of(events) == "Hello, engineer."
    first, second = (req.headers["authorization"] for req in r.served)
    assert first != second
    assert len(env.fake.calls("https://api.anthropic.com/v1/oauth/token")) == 1
    after = await env.store.get_credential(stored.id)
    assert after is not None and after.version == stored.version + 1 and not after.disabled


async def test_anthropic_429_blocks_account_until_reset(env: Env) -> None:
    stored = await env.connect()
    r = replay(env, "anthropic/rate_limited.json")
    handle = await env.pool.lease("anthropic")
    with pytest.raises(ProviderRateLimited) as info:
        await collect(anthropic(env, r).stream(handle, hello(env, "anthropic")))
    verify(r)
    assert (info.value.reset_at, info.value.scope) == (RESET, "5h")
    blocks = await env.store.active_blocks(env.clock())
    assert [(b.credential_id, b.scope, b.until) for b in blocks] == [(stored.id, "5h", RESET)]


# --- OpenAI Responses (ChatGPT backend) ----------------------------------------------------------


async def test_codex_streams_text_with_account_headers(env: Env) -> None:
    await env.connect(provider="openai-codex")
    r = replay(env, "openai-codex/stream_text.json")
    handle = await env.pool.lease("openai-codex")
    request = hello(env, "openai-codex", system="You are a tutor.")
    events = await collect(codex(env, r).stream(handle, request))
    verify(r)
    assert text_of(events) == "Hello, engineer."
    assert events[-2:] == [MessageStop("end_turn"), Usage(32, 6, 8, 0)]
    body = json.loads(r.served[0].content)
    assert "max_output_tokens" not in body and "temperature" not in body
    windows = {w.window: (w.used_pct, w.resets_at) for w in await env.store.usage_windows()}
    assert windows["5h"] == (12.0, RESET) and windows["7d"][0] == 40.0


async def test_codex_native_function_calls(env: Env) -> None:
    await env.connect(provider="openai-codex")
    r = replay(env, "openai-codex/stream_tool_call.json")
    handle = await env.pool.lease("openai-codex")
    request = ChatRequest(
        model=env.registry.get("openai-codex").default_model,
        messages=[
            {"role": "user", "content": [{"text": "What is a raft log?"}]},
            {
                "role": "assistant",
                "content": [
                    {
                        "toolUse": {
                            "toolUseId": "call_prev",
                            "name": "search_chunks",
                            "input": {"query": "raft"},
                        }
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "toolResult": {
                            "toolUseId": "call_prev",
                            "status": "success",
                            "content": [{"text": "No results."}],
                        }
                    }
                ],
            },
        ],
        tools=[SEARCH],
        tool_choice=ToolChoice("auto"),
    )
    events = await collect(codex(env, r).stream(handle, request))
    verify(r)
    assert BlockStart(0, "tool_use", "call_fixture", "search_chunks") in events
    arguments = "".join(e.partial_json for e in events if isinstance(e, ToolInputDelta))
    assert json.loads(arguments) == {"query": "raft log"}
    assert events[-2:] == [MessageStop("tool_use"), Usage(120, 18, 0, 0)]


async def test_codex_structured_output_uses_json_schema_format(env: Env) -> None:
    await env.connect(provider="openai-codex")
    r = replay(env, "openai-codex/structured.json")
    handle = await env.pool.lease("openai-codex")
    value, usage = await codex(env, r).structured(
        handle, hello(env, "openai-codex"), name="TopicList", schema=TOPICS_SCHEMA
    )
    verify(r)
    assert value == {"topics": [{"title": "Raft", "minutes": 45}]}
    assert json.loads(r.served[0].content)["text"]["format"]["schema"] == TOPICS_SCHEMA
    assert usage == Usage(90, 20, 0, 0)


async def test_codex_second_401_disables_account(env: Env) -> None:
    stored = await env.connect(provider="openai-codex")
    r = replay(env, "openai-codex/unauthorized_twice.json")
    handle = await env.pool.lease("openai-codex")
    with pytest.raises(AuthenticationFailed):
        await collect(codex(env, r).stream(handle, hello(env, "openai-codex")))
    verify(r)
    assert len(env.fake.calls("https://auth.openai.com/oauth/token")) == 1
    after = await env.store.get_credential(stored.id)
    assert after is not None and after.disabled_cause == "needs-reauthentication"


async def test_codex_usage_limit_blocks_until_reset(env: Env) -> None:
    stored = await env.connect(provider="openai-codex")
    r = replay(env, "openai-codex/usage_limit.json")
    handle = await env.pool.lease("openai-codex")
    with pytest.raises(ProviderRateLimited) as info:
        await collect(codex(env, r).stream(handle, hello(env, "openai-codex")))
    verify(r)
    assert (info.value.reset_at, info.value.scope) == (RESET, "usage-limit")
    blocks = await env.store.active_blocks(env.clock())
    assert [(b.credential_id, b.scope, b.until) for b in blocks] == [
        (stored.id, "usage-limit", RESET)
    ]
