"""OpenAI Responses against the ChatGPT backend (``/backend-api/codex/responses``).

Headers follow omp's Codex request builder (registry ``transport`` section): the ChatGPT account
id of the selected workspace, ``OpenAI-Beta: responses=experimental``, ``originator`` and
``version``. The backend requires ``stream: true`` and ``store: false`` and rejects
caller-supplied output caps and sampling parameters, so neither is sent.
"""

import base64
import json
import logging
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx

from brainbench_providers.errors import ProviderRateLimited, TransportError
from brainbench_providers.oauth.tokens import Clock, utcnow
from brainbench_providers.registry import ProviderSpec
from brainbench_providers.transports.base import (
    BlockStart,
    BlockStop,
    Cancelled,
    ChatRequest,
    HttpFactory,
    MessageStart,
    MessageStop,
    RateLimit,
    StopReason,
    TextDelta,
    TokenSource,
    ToolInputDelta,
    Usage,
    WireEvent,
    epoch,
    iter_sse,
    open_authenticated,
    retry_after,
    tool_result_text,
)
from brainbench_providers.usage import codex_windows_from_headers

log = logging.getLogger(__name__)

LIMIT_CODES = ("usage_limit_reached", "usage_not_included", "rate_limit_exceeded")


@dataclass
class _Item:
    index: int
    kind: str
    streamed: bool = False
    arguments: list[str] = field(default_factory=list)


class OpenAIResponsesTransport:
    def __init__(self, spec: ProviderSpec, *, http: HttpFactory, clock: Clock = utcnow) -> None:
        self.spec = spec
        self._http = http
        self._clock = clock

    # --- request ------------------------------------------------------------------------------

    @staticmethod
    def _input(request: ChatRequest) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for message in request.messages:
            role = message["role"]
            parts: list[dict[str, Any]] = []

            def flush(parts: list[dict[str, Any]] = parts, role: str = role) -> None:
                if parts:
                    items.append({"type": "message", "role": role, "content": list(parts)})
                    parts.clear()

            for block in message.get("content", []):
                if "text" in block:
                    kind = "output_text" if role == "assistant" else "input_text"
                    parts.append({"type": kind, "text": block["text"]})
                elif "image" in block and role == "user":
                    image = block["image"]
                    data = base64.b64encode(image["source"]["bytes"]).decode()
                    parts.append(
                        {
                            "type": "input_image",
                            "image_url": f"data:image/{image['format']};base64,{data}",
                        }
                    )
                elif "toolUse" in block:
                    flush()
                    use = block["toolUse"]
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": use["toolUseId"],
                            "name": use["name"],
                            "arguments": json.dumps(use.get("input") or {}, ensure_ascii=False),
                        }
                    )
                elif "toolResult" in block:
                    flush()
                    result = block["toolResult"]
                    items.append(
                        {
                            "type": "function_call_output",
                            "call_id": result["toolUseId"],
                            "output": tool_result_text(result),
                        }
                    )
            flush()
        return items

    def body(
        self, request: ChatRequest, *, text_format: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model.id,
            "instructions": request.system or "",
            "input": self._input(request),
            "store": False,
            "stream": True,
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                    "strict": False,
                }
                for t in request.tools
            ]
            choice = request.tool_choice
            payload["tool_choice"] = (
                "auto"
                if choice is None or choice.mode == "auto"
                else "required"
                if choice.mode == "any"
                else {"type": "function", "name": choice.name}
            )
            payload["parallel_tool_calls"] = True
        if text_format is not None:
            payload["text"] = {"format": text_format}
        if request.session_id:
            payload["prompt_cache_key"] = request.session_id
        return payload

    def headers(self, token: str, source: TokenSource, request: ChatRequest) -> dict[str, str]:
        transport = self.spec.transport
        headers = {
            **transport.headers,
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": transport.user_agent or "brainbench",
        }
        if source.identity.account_id:
            headers["chatgpt-account-id"] = source.identity.account_id
        if transport.originator:
            headers["originator"] = transport.originator
        if transport.client_version:
            headers["version"] = transport.client_version
        if request.session_id:
            headers["session_id"] = request.session_id
            headers["conversation_id"] = request.session_id
        return headers

    # --- response -----------------------------------------------------------------------------

    def _limit_from(self, error: Mapping[str, Any], headers: Mapping[str, str]) -> RateLimit:
        now = self._clock()
        code = str(error.get("code") or error.get("type") or "")
        reset = epoch(error.get("resets_at"))
        if reset is None and error.get("resets_in_seconds") is not None:
            try:
                reset = now + timedelta(seconds=float(error["resets_in_seconds"]))
            except TypeError, ValueError:
                reset = None
        if reset is None:
            exhausted = [
                w.resets_at
                for w in codex_windows_from_headers(headers)
                if w.used_pct is not None and w.used_pct >= 100 and w.resets_at is not None
            ]
            reset = max(exhausted) if exhausted else retry_after(headers, now)
        scope = "usage-limit" if code.startswith("usage_") else "rate-limit"
        message = str(error.get("message") or f"ChatGPT {scope.replace('-', ' ')} reached")
        return RateLimit(scope, reset, message)

    def _rate_limit(self, response: httpx.Response, body: bytes) -> RateLimit:
        try:
            data = json.loads(body)
        except ValueError:
            data = {}
        error = data.get("error", {}) if isinstance(data, dict) else {}
        return self._limit_from(error if isinstance(error, dict) else {}, response.headers)

    async def stream(
        self, source: TokenSource, request: ChatRequest, *, cancelled: Cancelled | None = None
    ) -> AsyncIterator[WireEvent]:
        async for event in self._stream(source, request, None, cancelled):
            yield event

    async def _stream(
        self,
        source: TokenSource,
        request: ChatRequest,
        text_format: dict[str, Any] | None,
        cancelled: Cancelled | None,
    ) -> AsyncIterator[WireEvent]:
        url = f"{self.spec.base_url}{self.spec.transport.path}"
        body = json.dumps(self.body(request, text_format=text_format), ensure_ascii=False).encode()
        timeout = httpx.Timeout(self.spec.transport.timeout_s, connect=30.0)
        async with self._http() as client:
            response = await open_authenticated(
                client,
                source,
                lambda token: client.build_request(
                    "POST",
                    url,
                    content=body,
                    headers=self.headers(token, source, request),
                    timeout=timeout,
                ),
                rate_limit=self._rate_limit,
                windows=codex_windows_from_headers,
            )
            try:
                async for event in self._events(response, source, cancelled):
                    yield event
            finally:
                await response.aclose()

    async def _events(
        self, response: httpx.Response, source: TokenSource, cancelled: Cancelled | None
    ) -> AsyncIterator[WireEvent]:
        items: dict[str, _Item] = {}
        by_output: dict[int, _Item] = {}
        used_tools = False
        yield MessageStart()
        async for kind, data in iter_sse(response):
            if cancelled is not None and cancelled():
                return
            kind = data.get("type", kind) if isinstance(data, Mapping) else kind
            if kind == "response.output_item.added":
                raw, index = data["item"], int(data.get("output_index", len(by_output)))
                added = _Item(index, raw.get("type", ""))
                by_output[index] = added
                if raw.get("id"):
                    items[raw["id"]] = added
                if added.kind == "function_call":
                    used_tools = True
                    yield BlockStart(index, "tool_use", raw.get("call_id"), raw.get("name"))
                elif added.kind == "message":
                    yield BlockStart(index, "text")
            elif kind == "response.output_text.delta":
                target = self._item(data, items, by_output)
                if target is not None and data.get("delta"):
                    target.streamed = True
                    yield TextDelta(target.index, data["delta"])
            elif kind == "response.function_call_arguments.delta":
                target = self._item(data, items, by_output)
                if target is not None and data.get("delta"):
                    target.streamed = True
                    yield ToolInputDelta(target.index, data["delta"])
            elif kind == "response.output_item.done":
                raw = data["item"]
                done = by_output.get(int(data.get("output_index", -1))) or items.get(
                    raw.get("id", "")
                )
                if done is None or done.kind not in {"function_call", "message"}:
                    continue
                if not done.streamed:
                    for event in self._unstreamed(done, raw):
                        yield event
                yield BlockStop(done.index)
            elif kind in {"response.completed", "response.incomplete"}:
                result = data.get("response", {})
                reason = (result.get("incomplete_details") or {}).get("reason")
                stop: StopReason = (
                    "max_tokens"
                    if reason == "max_output_tokens"
                    else "content_filtered"
                    if reason == "content_filter"
                    else "tool_use"
                    if used_tools
                    else "end_turn"
                )
                usage = result.get("usage") or {}
                cached = (usage.get("input_tokens_details") or {}).get("cached_tokens") or 0
                yield MessageStop(stop)
                yield Usage(
                    int(usage.get("input_tokens") or 0) - int(cached),
                    int(usage.get("output_tokens") or 0),
                    cache_read_tokens=int(cached),
                )
                return
            elif kind in {"response.failed", "error"}:
                error = data.get("response", {}).get("error") if kind == "response.failed" else data
                await self._stream_error(error or {}, response.headers, source)
        raise TransportError("The ChatGPT stream ended before response.completed")

    @staticmethod
    def _item(
        data: Mapping[str, Any], items: dict[str, _Item], by_output: dict[int, _Item]
    ) -> _Item | None:
        return items.get(data.get("item_id", "")) or by_output.get(
            int(data.get("output_index", -1))
        )

    @staticmethod
    def _unstreamed(item: _Item, raw: Mapping[str, Any]) -> list[WireEvent]:
        if item.kind == "function_call":
            return [ToolInputDelta(item.index, raw.get("arguments") or "{}")]
        text = "".join(
            part.get("text", "")
            for part in raw.get("content", [])
            if part.get("type") == "output_text"
        )
        return [TextDelta(item.index, text)] if text else []

    async def _stream_error(
        self, error: Mapping[str, Any], headers: Mapping[str, str], source: TokenSource
    ) -> None:
        code = str(error.get("code") or error.get("type") or "")
        if code.startswith(LIMIT_CODES):
            limit = self._limit_from(error, headers)
            until = await source.rate_limited(limit.scope, limit.reset_at, limit.message)
            raise ProviderRateLimited(limit.reset_at or until, limit.scope, limit.message)
        raise TransportError(f"ChatGPT stream error: {error.get('message') or code or 'unknown'}")

    async def structured(
        self, source: TokenSource, request: ChatRequest, *, name: str, schema: dict[str, Any]
    ) -> tuple[dict[str, Any], Usage | None]:
        """``text.format`` json_schema; the answer is the message text, parsed as JSON."""
        text_format = {"type": "json_schema", "name": name, "schema": schema, "strict": False}
        plain = ChatRequest(
            model=request.model,
            messages=request.messages,
            system=request.system,
            max_tokens=request.max_tokens,
            session_id=request.session_id,
        )
        chunks: list[str] = []
        usage: Usage | None = None
        async for event in self._stream(source, plain, text_format, None):
            if isinstance(event, TextDelta):
                chunks.append(event.text)
            elif isinstance(event, Usage):
                usage = event
        try:
            value = json.loads("".join(chunks))
        except ValueError:
            raise TransportError("The structured answer is not valid JSON") from None
        if not isinstance(value, dict):
            raise TransportError("The structured answer is not a JSON object")
        return value, usage
