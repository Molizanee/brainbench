"""Strands ``Model`` adapters over the provider transports (design D2).

``AnthropicOAuthModel`` and ``CodexOAuthModel`` select an account per model call (so a blocked
account is skipped mid-agent), translate wire events into Strands ``StreamEvent``s with native
``toolUse`` blocks and ``metadata`` usage, record ``provider_usage`` for every call, and use the
transport's structured mode for ``structured_output()``. A provider rate limit surfaces as
:class:`ProviderThrottledException`, a ``ModelThrottledException`` carrying the reset time.

This is the only module that touches the Strands ``Model`` interface.
"""

import logging
from collections.abc import AsyncGenerator, AsyncIterable
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict, TypeVar, cast

from pydantic import BaseModel, ValidationError
from strands.event_loop._retry import ModelRetryStrategy
from strands.models.model import Model
from strands.tools.structured_output.structured_output_utils import convert_pydantic_to_tool_spec
from strands.types.content import Messages, SystemContentBlock
from strands.types.exceptions import ModelThrottledException
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolChoice, ToolSpec

from brainbench_providers.errors import ProviderRateLimited, TransportError
from brainbench_providers.registry import ModelSpec, ProviderSpec
from brainbench_providers.select import ALL_ACCOUNTS, AccountPool, CredentialHandle
from brainbench_providers.transports import base as wire
from brainbench_providers.transports.anthropic_messages import AnthropicMessagesTransport
from brainbench_providers.transports.base import HttpFactory, Transport
from brainbench_providers.transports.openai_responses import OpenAIResponsesTransport

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# A throttle whose reset is further away than this is not retried inside the agent loop: the
# job layer defers the job (ProviderLimitReached) and the tutor reports the reset time.
MAX_IN_LOOP_WAIT = timedelta(seconds=60)


class ProviderThrottledException(ModelThrottledException):
    """``ModelThrottledException`` with the provider's reset time and limited scope."""

    def __init__(self, message: str, *, reset_at: datetime | None, scope: str) -> None:
        super().__init__(message)
        self.reset_at = reset_at
        self.scope = scope


class ProviderRetryStrategy(ModelRetryStrategy):
    """Strands retry strategy for subscription limits.

    Retries a throttle when another account may serve the next call (the throttled account is
    already blocked, so selection moves on) or when the reset is under a minute away. When every
    account of the provider is blocked until later it re-raises at once, so jobs defer until the
    reset instead of sleeping through Strands' backoff.
    """

    def is_retryable(self, exception: Exception) -> bool:
        if isinstance(exception, ProviderThrottledException):
            if exception.scope != ALL_ACCOUNTS:
                return True
            reset = exception.reset_at
            return reset is not None and reset - datetime.now(UTC) <= MAX_IN_LOOP_WAIT
        return super().is_retryable(exception)


class OAuthModelConfig(TypedDict, total=False):
    model_id: str
    max_tokens: int | None
    temperature: float | None
    task_class: str
    context_window_limit: int | None


def _system_text(system_prompt: str | None, content: list[SystemContentBlock] | None) -> str | None:
    if content:
        text = "\n\n".join(block["text"] for block in content if "text" in block)
        return text or system_prompt
    return system_prompt


def _tool_choice(choice: ToolChoice | None) -> wire.ToolChoice | None:
    if choice is None:
        return None
    if "any" in choice:
        return wire.ToolChoice("any")
    if "tool" in choice:
        return wire.ToolChoice("tool", cast(dict[str, Any], choice)["tool"]["name"])
    return wire.ToolChoice("auto")


def _tool(spec: ToolSpec) -> wire.ToolDef:
    schema = spec["inputSchema"]
    return wire.ToolDef(
        name=spec["name"],
        description=spec.get("description", ""),
        input_schema=cast(dict[str, Any], schema.get("json", schema)),
    )


def _event(event: wire.WireEvent) -> StreamEvent | None:
    match event:
        case wire.MessageStart():
            return {"messageStart": {"role": "assistant"}}
        case wire.BlockStart(index=index, kind="tool_use", tool_use_id=use_id, name=name):
            return {
                "contentBlockStart": {
                    "contentBlockIndex": index,
                    "start": {"toolUse": {"name": name or "", "toolUseId": use_id or ""}},
                }
            }
        case wire.BlockStart(index=index):
            return {"contentBlockStart": {"contentBlockIndex": index, "start": {}}}
        case wire.TextDelta(index=index, text=text):
            return {"contentBlockDelta": {"contentBlockIndex": index, "delta": {"text": text}}}
        case wire.ToolInputDelta(index=index, partial_json=partial):
            return {
                "contentBlockDelta": {
                    "contentBlockIndex": index,
                    "delta": {"toolUse": {"input": partial}},
                }
            }
        case wire.ReasoningDelta(index=index, text=text, signature=signature):
            reasoning: dict[str, Any] = {}
            if text is not None:
                reasoning["text"] = text
            if signature is not None:
                reasoning["signature"] = signature
            return cast(
                StreamEvent,
                {
                    "contentBlockDelta": {
                        "contentBlockIndex": index,
                        "delta": {"reasoningContent": reasoning},
                    }
                },
            )
        case wire.BlockStop(index=index):
            return {"contentBlockStop": {"contentBlockIndex": index}}
        case wire.MessageStop(stop_reason=reason):
            return {"messageStop": {"stopReason": reason}}
        case wire.Usage():
            return cast(
                StreamEvent, {"metadata": {"usage": _usage(event), "metrics": {"latencyMs": 0}}}
            )
    return None


def _usage(usage: wire.Usage) -> dict[str, int]:
    out = {
        "inputTokens": usage.input_tokens,
        "outputTokens": usage.output_tokens,
        "totalTokens": usage.input_tokens
        + usage.output_tokens
        + usage.cache_read_tokens
        + usage.cache_write_tokens,
    }
    if usage.cache_read_tokens:
        out["cacheReadInputTokens"] = usage.cache_read_tokens
    if usage.cache_write_tokens:
        out["cacheWriteInputTokens"] = usage.cache_write_tokens
    return out


def throttled(exc: ProviderRateLimited) -> ProviderThrottledException:
    return ProviderThrottledException(exc.message, reset_at=exc.reset_at, scope=exc.scope)


class _OAuthModel(Model):
    """Shared adapter: one account lease and one transport call per ``stream()``."""

    def __init__(
        self,
        *,
        spec: ProviderSpec,
        pool: AccountPool,
        transport: Transport,
        model: ModelSpec | None = None,
        task_class: str = "unspecified",
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> None:
        self.spec = spec
        self.pool = pool
        self.transport = transport
        chosen = model or spec.default_model
        self.config: OAuthModelConfig = {
            "model_id": chosen.id,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "task_class": task_class,
            "context_window_limit": chosen.context_window,
        }

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(provider={self.spec.id!r}, model={self.config['model_id']!r})"
        )

    def update_config(self, **model_config: Any) -> None:
        if "model_id" in model_config:
            self.spec.model(model_config["model_id"])
        self.config.update(cast(OAuthModelConfig, model_config))

    def get_config(self) -> OAuthModelConfig:
        return self.config

    @property
    def model_spec(self) -> ModelSpec:
        return self.spec.model(self.config["model_id"])

    def _request(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None,
        system: str | None,
        tool_choice: ToolChoice | None,
    ) -> wire.ChatRequest:
        return wire.ChatRequest(
            model=self.model_spec,
            messages=cast(list[dict[str, Any]], messages),
            system=system,
            tools=[_tool(t) for t in tool_specs or []],
            tool_choice=_tool_choice(tool_choice),
            max_tokens=self.config.get("max_tokens"),
            temperature=self.config.get("temperature"),
        )

    async def _lease(self) -> CredentialHandle:
        try:
            return await self.pool.lease(self.spec.id)
        except ProviderRateLimited as exc:
            raise throttled(exc) from exc

    async def _record(self, handle: CredentialHandle, usage: wire.Usage) -> None:
        try:
            await handle.record_usage(
                model=self.config["model_id"],
                task_class=self.config.get("task_class", "unspecified"),
                input_tokens=usage.input_tokens
                + usage.cache_read_tokens
                + usage.cache_write_tokens,
                output_tokens=usage.output_tokens,
            )
        except Exception:
            log.exception("recording provider usage failed")

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        *,
        tool_choice: ToolChoice | None = None,
        system_prompt_content: list[SystemContentBlock] | None = None,
        cancel_signal: Any = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        request = self._request(
            messages, tool_specs, _system_text(system_prompt, system_prompt_content), tool_choice
        )
        handle = await self._lease()
        cancelled = cancel_signal.is_set if cancel_signal is not None else None
        try:
            async for event in self.transport.stream(handle, request, cancelled=cancelled):
                if isinstance(event, wire.Usage):
                    await self._record(handle, event)
                mapped = _event(event)
                if mapped is not None:
                    yield mapped
        except ProviderRateLimited as exc:
            raise throttled(exc) from exc

    async def structured_output(
        self,
        output_model: type[T],
        prompt: Messages,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, T | Any]]:
        tool_spec = convert_pydantic_to_tool_spec(output_model)
        request = self._request(prompt, None, system_prompt, None)
        handle = await self._lease()
        try:
            value, usage = await self.transport.structured(
                handle,
                request,
                name=tool_spec["name"],
                schema=cast(dict[str, Any], tool_spec["inputSchema"]["json"]),
            )
        except ProviderRateLimited as exc:
            raise throttled(exc) from exc
        if usage is not None:
            await self._record(handle, usage)
        try:
            yield {"output": output_model.model_validate(value)}
        except ValidationError as exc:
            raise TransportError(
                f"Structured answer does not match {tool_spec['name']}: {exc}"
            ) from exc


class AnthropicOAuthModel(_OAuthModel):
    def __init__(
        self, *, spec: ProviderSpec, pool: AccountPool, http: HttpFactory, **kwargs: Any
    ) -> None:
        super().__init__(
            spec=spec,
            pool=pool,
            transport=AnthropicMessagesTransport(spec, http=http, clock=pool.clock),
            **kwargs,
        )


class CodexOAuthModel(_OAuthModel):
    def __init__(
        self, *, spec: ProviderSpec, pool: AccountPool, http: HttpFactory, **kwargs: Any
    ) -> None:
        super().__init__(
            spec=spec,
            pool=pool,
            transport=OpenAIResponsesTransport(spec, http=http, clock=pool.clock),
            **kwargs,
        )


MODEL_CLASSES: dict[str, type[AnthropicOAuthModel] | type[CodexOAuthModel]] = {
    "anthropic-messages": AnthropicOAuthModel,
    "openai-responses": CodexOAuthModel,
}


def build_model(
    spec: ProviderSpec,
    *,
    pool: AccountPool,
    http: HttpFactory,
    model: ModelSpec | None = None,
    task_class: str = "unspecified",
) -> _OAuthModel:
    """The Strands model for a provider's wire API."""
    cls = MODEL_CLASSES[spec.api]
    return cls(spec=spec, pool=pool, http=http, model=model, task_class=task_class)
