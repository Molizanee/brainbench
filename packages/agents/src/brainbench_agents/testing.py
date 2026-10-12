"""A scripted Strands ``Model`` for tests that drive agents without a provider.

``ScriptedModel`` answers each model call with the next scripted reply (or with whatever a
responder function returns for that call) as real ``StreamEvent``s: text, native ``toolUse``
blocks, a structured answer delivered through Strands' structured-output tool, usage metadata,
or a raised exception (for example a provider throttle). It records every call it served.
"""

import itertools
import json
from collections.abc import AsyncGenerator, AsyncIterable, Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, TypeVar, cast

from pydantic import BaseModel

__lazy_modules__ = {
    "strands.models.model",
    "strands.types.content",
    "strands.types.streaming",
    "strands.types.tools",
}
from strands.models.model import Model
from strands.types.content import Messages
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolSpec

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class ToolCall:
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class Structured:
    """A structured answer. ``data`` is the output model instance or its dict form; ``name`` is
    the output model's class name, needed only when ``data`` is a dict and the call offers
    several tools."""

    data: BaseModel | dict[str, Any]
    name: str | None = None


type Reply = Text | ToolCall | Structured | BaseException


@dataclass
class ModelCall:
    messages: Messages
    tool_names: list[str]
    system_prompt: str | None

    @property
    def prompt(self) -> str:
        """Text of the first user message (the prompt the agent was invoked with)."""
        for message in self.messages:
            if message["role"] == "user":
                return "".join(str(block.get("text", "")) for block in message["content"])
        return ""


type Responder = Callable[[ModelCall], Reply]


@dataclass
class ScriptedModel(Model):
    replies: Iterable[Reply] | Responder
    model_id: str = "scripted"
    usage: tuple[int, int] = (100, 20)
    calls: list[ModelCall] = field(default_factory=list)
    _iterator: Iterator[Reply] | None = field(default=None, init=False, repr=False)
    _ids: Iterator[int] = field(default_factory=lambda: itertools.count(1), repr=False)

    def update_config(self, **model_config: Any) -> None:
        self.model_id = model_config.get("model_id", self.model_id)

    def get_config(self) -> dict[str, Any]:
        return {"model_id": self.model_id}

    def _next(self, call: ModelCall) -> Reply:
        self.calls.append(call)
        if callable(self.replies):
            return self.replies(call)
        if self._iterator is None:
            self._iterator = iter(self.replies)
        try:
            return next(self._iterator)
        except StopIteration:
            raise AssertionError("ScriptedModel ran out of replies") from None

    def _usage_event(self) -> StreamEvent:
        prompt, completion = self.usage
        return cast(
            StreamEvent,
            {
                "metadata": {
                    "usage": {
                        "inputTokens": prompt,
                        "outputTokens": completion,
                        "totalTokens": prompt + completion,
                    },
                    "metrics": {"latencyMs": 0},
                }
            },
        )

    def _tool_events(self, name: str, payload: dict[str, Any]) -> list[StreamEvent]:
        use_id = f"tooluse_{next(self._ids)}"
        return cast(
            list[StreamEvent],
            [
                {"messageStart": {"role": "assistant"}},
                {
                    "contentBlockStart": {
                        "contentBlockIndex": 0,
                        "start": {"toolUse": {"name": name, "toolUseId": use_id}},
                    }
                },
                {
                    "contentBlockDelta": {
                        "contentBlockIndex": 0,
                        "delta": {"toolUse": {"input": json.dumps(payload)}},
                    }
                },
                {"contentBlockStop": {"contentBlockIndex": 0}},
                {"messageStop": {"stopReason": "tool_use"}},
                self._usage_event(),
            ],
        )

    def _text_events(self, text: str) -> list[StreamEvent]:
        return cast(
            list[StreamEvent],
            [
                {"messageStart": {"role": "assistant"}},
                {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}},
                {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": text}}},
                {"contentBlockStop": {"contentBlockIndex": 0}},
                {"messageStop": {"stopReason": "end_turn"}},
                self._usage_event(),
            ],
        )

    @staticmethod
    def _structured(reply: Structured, tool_names: list[str]) -> tuple[str, dict[str, Any]]:
        if isinstance(reply.data, BaseModel):
            return reply.name or type(reply.data).__name__, reply.data.model_dump(mode="json")
        if reply.name is not None:
            return reply.name, reply.data
        if not tool_names:
            raise AssertionError("a structured reply needs a tool to answer through")
        return tool_names[-1], reply.data

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        names = [spec["name"] for spec in tool_specs or []]
        reply = self._next(ModelCall(list(messages), names, system_prompt))
        match reply:
            case BaseException():
                raise reply
            case Text(text=text):
                events = self._text_events(text)
            case ToolCall(name=name, input=payload):
                events = self._tool_events(name, payload)
            case Structured():
                events = self._tool_events(*self._structured(reply, names))
        for event in events:
            yield event

    async def structured_output(
        self,
        output_model: type[T],
        prompt: Messages,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, T | Any]]:
        reply = self._next(ModelCall(list(prompt), [output_model.__name__], system_prompt))
        if isinstance(reply, BaseException):
            raise reply
        if not isinstance(reply, Structured):
            raise AssertionError(f"structured_output needs a Structured reply, got {reply!r}")
        _, data = self._structured(reply, [output_model.__name__])
        yield {"output": output_model.model_validate(data)}
