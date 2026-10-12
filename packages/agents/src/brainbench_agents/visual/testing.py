"""A scripted Strands ``Model`` for tests and throwaway smoke wiring (no provider account).

Each call to ``stream`` replays the next scripted turn: text blocks and tool calls. A tool call
named after an output model (``Storyboard``, ``ManimScript``...) is how Strands receives
structured output, so a turn ``[call("Storyboard", {...})]`` answers a structured step.
"""

import json
from collections.abc import AsyncGenerator, AsyncIterable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

__lazy_modules__ = {"strands.models.model", "strands.types.content", "strands.types.streaming"}
from strands.models.model import Model
from strands.types.content import Messages
from strands.types.streaming import StreamEvent


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class Call:
    name: str
    input: dict[str, Any]


type Block = Text | Call
type Turn = Sequence[Block] | Callable[[Messages], Sequence[Block]]


def say(text: str) -> Text:
    return Text(text)


def call(name: str, input: dict[str, Any] | None = None) -> Call:
    return Call(name, input or {})


@dataclass
class ScriptedModel(Model):
    """Replays ``turns`` in order; a callable turn sees the conversation so far.

    ``requests`` records, per call, the tool names offered and the last message, so tests can
    assert what the agent sent. Running out of turns raises ``AssertionError``.
    """

    turns: list[Turn]
    requests: list[dict[str, Any]] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)

    def update_config(self, **model_config: Any) -> None:
        self.config.update(model_config)

    def get_config(self) -> Any:
        return self.config

    async def structured_output(
        self, output_model: Any, prompt: Messages, system_prompt: str | None = None, **kwargs: Any
    ) -> AsyncGenerator[dict[str, Any]]:
        raise AssertionError("agents use tool-based structured output; script a Call instead")
        yield {}  # pragma: no cover

    async def stream(
        self,
        messages: Messages,
        tool_specs: Any = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        self.requests.append(
            {
                "tools": [spec["name"] for spec in tool_specs or []],
                "system_prompt": system_prompt,
                "last_message": messages[-1] if messages else None,
            }
        )
        if not self.turns:
            raise AssertionError(f"scripted model ran out of turns after {len(self.requests)}")
        turn = self.turns.pop(0)
        blocks = turn(messages) if callable(turn) else turn
        yield {"messageStart": {"role": "assistant"}}
        stop = "end_turn"
        for index, block in enumerate(blocks):
            if isinstance(block, Text):
                yield {"contentBlockStart": {"contentBlockIndex": index, "start": {}}}
                yield {
                    "contentBlockDelta": {"contentBlockIndex": index, "delta": {"text": block.text}}
                }
            else:
                stop = "tool_use"
                use_id = f"call_{len(self.requests)}_{index}"
                yield {
                    "contentBlockStart": {
                        "contentBlockIndex": index,
                        "start": {"toolUse": {"name": block.name, "toolUseId": use_id}},
                    }
                }
                yield {
                    "contentBlockDelta": {
                        "contentBlockIndex": index,
                        "delta": {"toolUse": {"input": json.dumps(block.input)}},
                    }
                }
            yield {"contentBlockStop": {"contentBlockIndex": index}}
        yield {"messageStop": {"stopReason": stop}}  # type: ignore[typeddict-item]
        yield {
            "metadata": {
                "usage": {"inputTokens": 10, "outputTokens": 10, "totalTokens": 20},
                "metrics": {"latencyMs": 0},
            }
        }


def tool_results(messages: Messages) -> list[dict[str, Any]]:
    """Every ``toolResult`` block in the conversation, oldest first."""
    return [
        dict(block["toolResult"])
        for message in messages
        for block in message.get("content", [])
        if "toolResult" in block
    ]


def result_text(result: dict[str, Any]) -> str:
    return "\n".join(c["text"] for c in result.get("content", []) if "text" in c)
