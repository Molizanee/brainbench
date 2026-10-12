"""Anthropic Messages over a Claude subscription OAuth token.

Request shape and headers follow omp's ``buildAnthropicHeaders`` and system-block builder for
OAuth tokens (registry ``transport`` section): Claude Code's beta list and user agent, the
``x-anthropic-billing-header`` system block with its ``cch`` attestation, the Claude Code
identity block, and a prefix on custom tool names that is stripped from responses.
"""

import base64
import hashlib
import json
import logging
import platform
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx
import xxhash

from brainbench_providers.errors import ProviderRateLimited, TransportError
from brainbench_providers.oauth.tokens import Clock, utcnow
from brainbench_providers.registry import BillingHeaderSpec, ProviderSpec
from brainbench_providers.transports.base import (
    BlockStart,
    BlockStop,
    Cancelled,
    ChatRequest,
    HttpFactory,
    MessageStart,
    MessageStop,
    RateLimit,
    ReasoningDelta,
    StopReason,
    TextDelta,
    TokenSource,
    ToolChoice,
    ToolDef,
    ToolInputDelta,
    Usage,
    WireEvent,
    epoch,
    error_message,
    first_user_text,
    header,
    iter_sse,
    open_authenticated,
    retry_after,
    tool_result_text,
)
from brainbench_providers.usage import anthropic_windows_from_headers

log = logging.getLogger(__name__)

BILLING_PREFIX = "x-anthropic-billing-header:"
CCH_PLACEHOLDER = "cch=00000"
CCH_WINDOW = 150
IMAGE_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "jpg": "image/jpeg", "gif": "image/gif",
               "webp": "image/webp"}  # fmt: skip
STOP_REASONS: dict[str, StopReason] = {
    "end_turn": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "stop_sequence": "stop_sequence",
    "refusal": "content_filtered",
    "pause_turn": "end_turn",
}
CLAIM_SCOPES = {
    "five_hour": "5h",
    "seven_day": "7d",
    "seven_day_opus": "7d",
    "five_hour_opus": "5h",
}
DEFAULT_MAX_TOKENS = 32_000


def stainless_platform() -> dict[str, str]:
    machine = platform.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(
        machine, machine
    )
    system = {"Darwin": "MacOS", "Linux": "Linux", "Windows": "Windows"}.get(
        platform.system(), "Other"
    )
    return {"X-Stainless-Arch": arch, "X-Stainless-OS": system}


def billing_header(spec: BillingHeaderSpec, first_text: str) -> str:
    """Claude Code's fingerprint: sha256(salt + chars 4, 7, 20 of the first user turn + version)."""
    chars = "".join(first_text[i] if i < len(first_text) else "0" for i in (4, 7, 20))
    suffix = hashlib.sha256(f"{spec.salt}{chars}{spec.version}".encode()).hexdigest()[:3]
    return (
        f"{BILLING_PREFIX} cc_version={spec.version}.{suffix}; cc_entrypoint=cli; "
        f"{CCH_PLACEHOLDER};"
    )


def patch_cch(body: bytes, seed: int) -> bytes:
    """Replace the ``cch=00000`` placeholder in system[0] with xxh64(body, seed) low 20 bits."""
    marker = f'"system":[{{"type":"text","text":"{BILLING_PREFIX}'.encode()
    at = body.find(marker)
    if at < 0:
        return body
    start = at + len(marker)
    idx = body.find(CCH_PLACEHOLDER.encode(), start)
    if idx < 0 or idx - start > CCH_WINDOW:
        log.warning("anthropic: billing placeholder not anchored; sending unattested request")
        return body
    digest = xxhash.xxh64_intdigest(body, seed=seed) & 0xFFFFF
    patched = bytearray(body)
    patched[idx + 4 : idx + 9] = f"{digest:05x}".encode()
    return bytes(patched)


class AnthropicMessagesTransport:
    def __init__(self, spec: ProviderSpec, *, http: HttpFactory, clock: Clock = utcnow) -> None:
        self.spec = spec
        self._http = http
        self._clock = clock
        self._prefix = spec.transport.tool_prefix

    # --- request ------------------------------------------------------------------------------

    def _tool_name(self, name: str) -> str:
        return f"{self._prefix}{name}"

    def _untool_name(self, name: str) -> str:
        return name.removeprefix(self._prefix) if self._prefix else name

    def _content(self, block: dict[str, Any]) -> dict[str, Any] | None:
        if "text" in block:
            return {"type": "text", "text": block["text"]}
        if "image" in block:
            image = block["image"]
            return {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": IMAGE_TYPES.get(image["format"], "image/png"),
                    "data": base64.b64encode(image["source"]["bytes"]).decode(),
                },
            }
        if "toolUse" in block:
            use = block["toolUse"]
            return {
                "type": "tool_use",
                "id": use["toolUseId"],
                "name": self._tool_name(use["name"]),
                "input": use.get("input") or {},
            }
        if "toolResult" in block:
            result = block["toolResult"]
            return {
                "type": "tool_result",
                "tool_use_id": result["toolUseId"],
                "content": tool_result_text(result),
                "is_error": result.get("status") == "error",
            }
        if "reasoningContent" in block:
            reasoning = block["reasoningContent"].get("reasoningText", {})
            if reasoning.get("signature"):
                return {
                    "type": "thinking",
                    "thinking": reasoning.get("text", ""),
                    "signature": reasoning["signature"],
                }
        return None

    def _messages(self, request: ChatRequest) -> list[dict[str, Any]]:
        out = []
        for message in request.messages:
            content = [c for b in message.get("content", []) if (c := self._content(b)) is not None]
            if content:
                out.append({"role": message["role"], "content": content})
        return out

    def _system(self, request: ChatRequest) -> list[dict[str, Any]]:
        transport = self.spec.transport
        blocks: list[dict[str, Any]] = []
        if transport.billing_header is not None:
            text = billing_header(transport.billing_header, first_user_text(request.messages))
            blocks.append({"type": "text", "text": text})
        if transport.system_preamble:
            blocks.append(
                {
                    "type": "text",
                    "text": transport.system_preamble,
                    "cache_control": {"type": "ephemeral"},
                }
            )
        if request.system:
            blocks.append({"type": "text", "text": request.system})
        return blocks

    def body(self, request: ChatRequest) -> bytes:
        payload: dict[str, Any] = {
            "model": request.model.id,
            "messages": self._messages(request),
            "system": self._system(request),
            "max_tokens": request.max_tokens or min(request.model.max_output, DEFAULT_MAX_TOKENS),
            "stream": True,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.tools:
            payload["tools"] = [
                {
                    "name": self._tool_name(t.name),
                    "description": t.description,
                    "input_schema": t.input_schema,
                }
                for t in request.tools
            ]
        choice = request.tool_choice
        if choice is not None and request.tools:
            payload["tool_choice"] = (
                {"type": "tool", "name": self._tool_name(choice.name or "")}
                if choice.mode == "tool"
                else {"type": choice.mode}
            )
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        billing = self.spec.transport.billing_header
        return patch_cch(raw, billing.cch_seed) if billing is not None else raw

    def headers(self, token: str) -> dict[str, str]:
        transport = self.spec.transport
        return {
            **transport.headers,
            **stainless_platform(),
            "Content-Type": "application/json",
            "User-Agent": transport.user_agent or "brainbench",
            "anthropic-beta": ",".join(transport.betas),
            "Authorization": f"Bearer {token}",
        }

    # --- response -----------------------------------------------------------------------------

    def _rate_limit(self, response: httpx.Response, body: bytes) -> RateLimit:
        now = self._clock()
        claim = header(response.headers, "anthropic-ratelimit-unified-representative-claim")
        reset = epoch(header(response.headers, "anthropic-ratelimit-unified-reset")) or retry_after(
            response.headers, now
        )
        scope = CLAIM_SCOPES.get(claim or "", claim or "requests")
        return RateLimit(scope, reset, f"Anthropic rate limit: {error_message(body)}")

    async def stream(
        self, source: TokenSource, request: ChatRequest, *, cancelled: Cancelled | None = None
    ) -> AsyncIterator[WireEvent]:
        url = f"{self.spec.base_url}{self.spec.transport.path}"
        body = self.body(request)
        timeout = httpx.Timeout(self.spec.transport.timeout_s, connect=30.0)
        async with self._http() as client:
            response = await open_authenticated(
                client,
                source,
                lambda token: client.build_request(
                    "POST", url, content=body, headers=self.headers(token), timeout=timeout
                ),
                rate_limit=self._rate_limit,
                windows=anthropic_windows_from_headers,
            )
            try:
                async for event in self._events(response, source, cancelled):
                    yield event
            finally:
                await response.aclose()

    async def _events(
        self, response: httpx.Response, source: TokenSource, cancelled: Cancelled | None
    ) -> AsyncIterator[WireEvent]:
        usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        stop: StopReason = "end_turn"
        async for kind, data in iter_sse(response):
            if cancelled is not None and cancelled():
                return
            kind = data.get("type", kind) if isinstance(data, Mapping) else kind
            if kind == "message_start":
                u = data.get("message", {}).get("usage", {})
                usage["input"] = int(u.get("input_tokens") or 0)
                usage["cache_read"] = int(u.get("cache_read_input_tokens") or 0)
                usage["cache_write"] = int(u.get("cache_creation_input_tokens") or 0)
                yield MessageStart()
            elif kind == "content_block_start":
                block, index = data["content_block"], int(data["index"])
                if block["type"] == "tool_use":
                    yield BlockStart(
                        index, "tool_use", block["id"], self._untool_name(block["name"])
                    )
                elif block["type"] in {"thinking", "redacted_thinking"}:
                    yield BlockStart(index, "reasoning")
                else:
                    yield BlockStart(index, "text")
                    if block.get("text"):
                        yield TextDelta(index, block["text"])
            elif kind == "content_block_delta":
                delta, index = data["delta"], int(data["index"])
                match delta.get("type"):
                    case "text_delta":
                        yield TextDelta(index, delta["text"])
                    case "input_json_delta":
                        yield ToolInputDelta(index, delta["partial_json"])
                    case "thinking_delta":
                        yield ReasoningDelta(index, text=delta["thinking"])
                    case "signature_delta":
                        yield ReasoningDelta(index, signature=delta["signature"])
            elif kind == "content_block_stop":
                yield BlockStop(int(data["index"]))
            elif kind == "message_delta":
                reason = data.get("delta", {}).get("stop_reason")
                if reason:
                    stop = STOP_REASONS.get(reason, "end_turn")
                if "usage" in data:
                    usage["output"] = int(data["usage"].get("output_tokens") or usage["output"])
            elif kind == "message_stop":
                yield MessageStop(stop)
                yield Usage(
                    usage["input"], usage["output"], usage["cache_read"], usage["cache_write"]
                )
                return
            elif kind == "error":
                await self._stream_error(data, source)
        raise TransportError("The Anthropic stream ended before message_stop")

    async def _stream_error(self, data: Mapping[str, Any], source: TokenSource) -> None:
        error = data.get("error", {})
        if error.get("type") == "rate_limit_error":
            until = await source.rate_limited("requests", None, error.get("message"))
            raise ProviderRateLimited(until, "requests", error.get("message"))
        raise TransportError(f"Anthropic stream error: {error.get('message') or error}")

    async def structured(
        self, source: TokenSource, request: ChatRequest, *, name: str, schema: dict[str, Any]
    ) -> tuple[dict[str, Any], Usage | None]:
        """Tool-forced JSON: one tool whose input schema is the output schema."""
        forced = ChatRequest(
            model=request.model,
            messages=request.messages,
            system=request.system,
            tools=[ToolDef(name, f"Return the {name} result.", schema)],
            tool_choice=ToolChoice("tool", name),
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            session_id=request.session_id,
        )
        parts: dict[int, list[str]] = {}
        names: dict[int, str] = {}
        usage: Usage | None = None
        async for event in self.stream(source, forced):
            if isinstance(event, BlockStart) and event.kind == "tool_use":
                names[event.index] = event.name or ""
                parts[event.index] = []
            elif isinstance(event, ToolInputDelta):
                parts.setdefault(event.index, []).append(event.partial_json)
            elif isinstance(event, Usage):
                usage = event
        for index, tool in names.items():
            if tool == name:
                raw = "".join(parts[index]) or "{}"
                try:
                    return json.loads(raw), usage
                except ValueError:
                    raise TransportError("The structured answer is not valid JSON") from None
        raise TransportError("The model did not return the structured answer")
