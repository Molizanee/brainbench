"""Shared transport types: the neutral request, the wire events, auth retry, and SSE reading.

Messages use Converse-shaped content blocks (the shape Strands also uses), as plain dicts:
``{"role": "user" | "assistant", "content": [block, ...]}`` where a block is one of
``{"text"}``, ``{"image": {"format", "source": {"bytes"}}}``, ``{"toolUse": {"toolUseId",
"name", "input"}}``, ``{"toolResult": {"toolUseId", "status", "content": [{"text"} | {"json"}]}}``,
or ``{"reasoningContent": {"reasoningText": {"text", "signature"}}}``.
"""

import json
import logging
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

import httpx

from brainbench_providers.errors import (
    AuthenticationFailed,
    ProviderRateLimited,
    TransportError,
)
from brainbench_providers.registry import ModelSpec, ProviderSpec
from brainbench_providers.store import UsageWindow
from brainbench_providers.vault import Identity

log = logging.getLogger(__name__)

StopReason = Literal["end_turn", "tool_use", "max_tokens", "stop_sequence", "content_filtered"]
Cancelled = Callable[[], bool]


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolChoice:
    """``auto``: the model decides; ``any``: some tool must be called; ``tool``: ``name``."""

    mode: Literal["auto", "any", "tool"]
    name: str | None = None


@dataclass
class ChatRequest:
    model: ModelSpec
    messages: list[dict[str, Any]]
    system: str | None = None
    tools: list[ToolDef] = field(default_factory=list)
    tool_choice: ToolChoice | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class MessageStart:
    pass


@dataclass(frozen=True)
class BlockStart:
    index: int
    kind: Literal["text", "tool_use", "reasoning"]
    tool_use_id: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class TextDelta:
    index: int
    text: str


@dataclass(frozen=True)
class ToolInputDelta:
    index: int
    partial_json: str


@dataclass(frozen=True)
class ReasoningDelta:
    index: int
    text: str | None = None
    signature: str | None = None


@dataclass(frozen=True)
class BlockStop:
    index: int


@dataclass(frozen=True)
class MessageStop:
    stop_reason: StopReason


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


WireEvent = (
    MessageStart
    | BlockStart
    | TextDelta
    | ToolInputDelta
    | ReasoningDelta
    | BlockStop
    | MessageStop
    | Usage
)


class TokenSource(Protocol):
    """What a transport needs from the selected account (``select.CredentialHandle``)."""

    @property
    def identity(self) -> Identity: ...

    def access_token(self) -> str: ...

    async def refresh_after_unauthorized(self) -> str: ...

    async def unauthorized_again(self) -> None: ...

    async def rate_limited(
        self, scope: str, reset_at: datetime | None, reason: str | None = None
    ) -> datetime: ...

    async def observe_windows(self, windows: list[UsageWindow]) -> None: ...


class Transport(Protocol):
    spec: ProviderSpec

    def stream(
        self, source: TokenSource, request: ChatRequest, *, cancelled: Cancelled | None = None
    ) -> AsyncIterator[WireEvent]: ...

    async def structured(
        self, source: TokenSource, request: ChatRequest, *, name: str, schema: dict[str, Any]
    ) -> tuple[dict[str, Any], Usage | None]: ...


HttpFactory = Callable[[], httpx.AsyncClient]


def header(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def retry_after(headers: Mapping[str, str], now: datetime) -> datetime | None:
    value = header(headers, "retry-after")
    if value is None:
        return None
    try:
        return now + timedelta(seconds=float(value))
    except ValueError:
        return None


def epoch(value: Any) -> datetime | None:
    try:
        seconds = float(value)
    except TypeError, ValueError:
        return None
    return datetime.fromtimestamp(seconds, UTC) if seconds > 0 else None


@dataclass(frozen=True)
class RateLimit:
    scope: str
    reset_at: datetime | None
    message: str


async def open_authenticated(
    client: httpx.AsyncClient,
    source: TokenSource,
    build: Callable[[str], httpx.Request],
    *,
    rate_limit: Callable[[httpx.Response, bytes], RateLimit],
    windows: Callable[[Mapping[str, str]], list[UsageWindow]],
) -> httpx.Response:
    """Send with the account's token; on 401 refresh once and retry, on a second 401 mark the
    account ``needs-reauthentication``; on 429 block the account and raise
    ``ProviderRateLimited``. Returns the open (streaming) 2xx response."""
    token = source.access_token()
    for attempt in (1, 2):
        response = await client.send(build(token), stream=True)
        if response.status_code == 401:
            await response.aread()
            await response.aclose()
            if attempt == 1:
                log.info("provider answered 401; refreshing once")
                token = await source.refresh_after_unauthorized()
                continue
            await source.unauthorized_again()
            raise AuthenticationFailed("The provider rejected the refreshed token; sign in again")
        observed = windows(response.headers)
        if observed:
            await source.observe_windows(observed)
        if response.status_code == 429:
            body = await response.aread()
            await response.aclose()
            limit = rate_limit(response, body)
            until = await source.rate_limited(limit.scope, limit.reset_at, limit.message)
            raise ProviderRateLimited(limit.reset_at or until, limit.scope, limit.message)
        if response.status_code >= 400:
            body = await response.aread()
            await response.aclose()
            raise TransportError(
                f"Provider answered {response.status_code}: {error_message(body)}",
                status=response.status_code,
            )
        return response
    raise AssertionError("unreachable")


def error_message(body: bytes) -> str:
    """A short description from an error body (never the request, which carries the token)."""
    try:
        data = json.loads(body)
    except ValueError:
        return body[:200].decode(errors="replace")
    if isinstance(data, dict):
        error = data.get("error", data)
        if isinstance(error, dict):
            return str(error.get("message") or error.get("type") or error.get("code") or error)[
                :300
            ]
        detail = data.get("detail")
        if detail:
            return str(detail)[:300]
        return str(error)[:300]
    return str(data)[:300]


async def iter_sse(response: httpx.Response) -> AsyncIterator[tuple[str, Any]]:
    """``(event, data)`` pairs from a ``text/event-stream`` body; ``data`` is parsed JSON."""
    event, data_lines = "message", list[str]()
    async for line in response.aiter_lines():
        if line == "":
            if data_lines:
                raw = "\n".join(data_lines)
                if raw != "[DONE]":
                    try:
                        yield event, json.loads(raw)
                    except ValueError:
                        raise TransportError("Malformed event in the provider stream") from None
            event, data_lines = "message", []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].removeprefix(" "))
    if data_lines:
        raw = "\n".join(data_lines)
        if raw != "[DONE]":
            yield event, json.loads(raw)


def tool_result_text(result: dict[str, Any]) -> str:
    parts = []
    for item in result.get("content", []):
        if "text" in item:
            parts.append(str(item["text"]))
        elif "json" in item:
            parts.append(json.dumps(item["json"], ensure_ascii=False))
    return "\n".join(parts)


def first_user_text(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        if message.get("role") != "user":
            continue
        for block in message.get("content", []):
            if "text" in block:
                return str(block["text"])
        return ""
    return ""
