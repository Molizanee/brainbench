"""A scripted Strands ``Model`` for tests: no provider, no network.

Each model call consumes one :class:`Turn` and streams it as the provider adapters do
(``messageStart``, content blocks with text deltas or ``toolUse``, ``messageStop``, ``metadata``
usage). A turn can pause between deltas (to stop it mid-reply) or raise (to simulate a provider
throttle). Like the real transports, it stops streaming as soon as the ``cancel_signal`` is set.
"""

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel

__lazy_modules__ = {"strands.models.model", "strands.types.content", "strands.types.streaming"}
from strands.models.model import Model
from strands.types.content import Messages
from strands.types.streaming import StreamEvent

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class ToolCall:
    name: str
    input: dict[str, Any] = field(default_factory=dict)
    tool_use_id: str = ""


@dataclass(frozen=True)
class Pause:
    seconds: float


@dataclass(frozen=True)
class Raise:
    error: BaseException


type Part = str | ToolCall | Pause | Raise


@dataclass
class Turn:
    """One model response. ``str`` parts are text deltas; consecutive ones share a block."""

    parts: list[Part]
    input_tokens: int = 40
    output_tokens: int = 20


@dataclass
class Call:
    """What the model was asked: the conversation, system prompt, and tool names."""

    messages: Messages
    system_prompt: str | None
    tool_names: list[str]


class ScriptedModel(Model):
    def __init__(self, turns: list[Turn | list[Part]]) -> None:
        self.turns = [t if isinstance(t, Turn) else Turn(t) for t in turns]
        self.calls: list[Call] = []
        self.config: dict[str, Any] = {"model_id": "scripted"}

    def update_config(self, **model_config: Any) -> None:
        self.config.update(model_config)

    def get_config(self) -> dict[str, Any]:
        return self.config

    async def structured_output(
        self,
        output_model: type[T],
        prompt: Messages,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, T | Any]]:
        raise NotImplementedError("the tutor never asks for structured output")
        yield {}  # pragma: no cover

    async def stream(
        self,
        messages: Messages,
        tool_specs: Any = None,
        system_prompt: str | None = None,
        *,
        cancel_signal: Any = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        self.calls.append(
            Call(
                messages=json.loads(json.dumps(messages, default=str)),
                system_prompt=system_prompt,
                tool_names=[spec["name"] for spec in tool_specs or []],
            )
        )
        if not self.turns:
            raise AssertionError("ScriptedModel ran out of turns")
        turn = self.turns.pop(0)

        def cancelled() -> bool:
            return cancel_signal is not None and cancel_signal.is_set()

        yield {"messageStart": {"role": "assistant"}}
        index = -1
        open_text = False
        used_tools = False
        for part in turn.parts:
            if cancelled():
                return
            match part:
                case Pause(seconds=seconds):
                    await asyncio.sleep(seconds)
                case Raise(error=error):
                    raise error
                case ToolCall(name=name, input=args, tool_use_id=use_id):
                    if open_text:
                        yield {"contentBlockStop": {"contentBlockIndex": index}}
                        open_text = False
                    index += 1
                    used_tools = True
                    yield {
                        "contentBlockStart": {
                            "contentBlockIndex": index,
                            "start": {
                                "toolUse": {"name": name, "toolUseId": use_id or f"call-{index}"}
                            },
                        }
                    }
                    yield {
                        "contentBlockDelta": {
                            "contentBlockIndex": index,
                            "delta": {"toolUse": {"input": json.dumps(args)}},
                        }
                    }
                    yield {"contentBlockStop": {"contentBlockIndex": index}}
                case str() as text:
                    if not open_text:
                        index += 1
                        open_text = True
                        yield {"contentBlockStart": {"contentBlockIndex": index, "start": {}}}
                    yield {
                        "contentBlockDelta": {"contentBlockIndex": index, "delta": {"text": text}}
                    }
                    await asyncio.sleep(0)
        if cancelled():
            return
        if open_text:
            yield {"contentBlockStop": {"contentBlockIndex": index}}
        yield {"messageStop": {"stopReason": "tool_use" if used_tools else "end_turn"}}
        yield {
            "metadata": {
                "usage": {
                    "inputTokens": turn.input_tokens,
                    "outputTokens": turn.output_tokens,
                    "totalTokens": turn.input_tokens + turn.output_tokens,
                },
                "metrics": {"latencyMs": 0},
            }
        }
