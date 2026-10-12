import asyncio
import threading
from typing import Any

from brainbench_agents.tutor import (
    TOOL_NAMES,
    ChunkRef,
    CitationLedger,
    TutorContext,
    TutorTools,
    make_tutor_agent,
    retrieve,
)
from brainbench_agents.tutor.testing import Pause, ScriptedModel, ToolCall, Turn

TOPIC_HIT = ChunkRef("c1", "s1", "Raft paper", {"page": 5}, "Majorities intersect.", 0.82)
OTHER_HIT = ChunkRef("c2", "s2", "Raft (wiki)", {"fragment": "#:~:text=Raft"}, "Raft...", 0.71)
WEAK_HIT = ChunkRef("c3", "s3", "Cooking", {}, "Unrelated.", 0.41)


class Data:
    def __init__(self, topic_hits: list[ChunkRef], all_hits: list[ChunkRef]) -> None:
        self.topic_hits = topic_hits
        self.all_hits = all_hits
        self.searches: list[tuple[str, int, str | None]] = []
        self.topics: list[str] = []

    async def search(self, query: str, k: int, topic_id: str | None) -> list[ChunkRef]:
        self.searches.append((query, k, topic_id))
        return list(self.topic_hits if topic_id else self.all_hits)

    async def topic(self, topic_id: str) -> dict[str, Any] | None:
        self.topics.append(topic_id)
        return {"id": topic_id, "title": "Leader election"}

    async def plan(self) -> dict[str, Any] | None:
        return None

    async def profile(self) -> dict[str, Any] | None:
        return {"language": "en"}

    def tools(self, k: int = 8) -> TutorTools:
        return TutorTools(self.search, self.topic, self.plan, self.profile, k=k)


TOPIC = TutorContext("topic", "topic-1", "Leader election")


async def test_retrieve_prefers_the_context_topic_then_tops_up() -> None:
    data = Data([TOPIC_HIT, WEAK_HIT], [OTHER_HIT, TOPIC_HIT, WEAK_HIT])
    refs = await retrieve(data.tools(), TOPIC, "why a majority")
    assert refs == [TOPIC_HIT, OTHER_HIT]
    assert data.searches == [("why a majority", 8, "topic-1"), ("why a majority", 8, None)]
    general = await retrieve(data.tools(), TutorContext("general"), "q")
    assert general == [OTHER_HIT, TOPIC_HIT]


async def test_retrieve_skips_global_search_when_topic_fills_k() -> None:
    data = Data([TOPIC_HIT], [OTHER_HIT])
    assert await retrieve(data.tools(k=1), TOPIC, "q") == [TOPIC_HIT]
    assert len(data.searches) == 1


async def test_agent_streams_text_and_records_retrieved_passages() -> None:
    data = Data([TOPIC_HIT], [OTHER_HIT])
    model = ScriptedModel(
        [
            [ToolCall("search_chunks", {"query": "majority quorum"}), ToolCall("get_topic", {})],
            ["Any two majorities ", "overlap [Raft paper, p. 5]."],
        ]
    )
    deltas: list[str] = []
    ledger = CitationLedger()
    agent = make_tutor_agent(
        model,
        TOPIC,
        data.tools(),
        ledger=ledger,
        messages=[
            {"role": "user", "content": [{"text": "Earlier question"}]},
            {"role": "assistant", "content": [{"text": "Earlier answer"}]},
        ],
        callback_handler=lambda **event: deltas.append(event["data"]) if "data" in event else None,
    )
    result = await agent.invoke_async("Why does Raft need a majority?")
    assert result.stop_reason == "end_turn"
    assert "".join(deltas) == "Any two majorities overlap [Raft paper, p. 5]."
    assert model.calls[0].tool_names == list(TOOL_NAMES)
    assert model.calls[0].messages[0]["content"][0]["text"] == "Earlier question"
    assert "[source-title, location]" in (model.calls[0].system_prompt or "")
    assert ledger.searches == 1 and ledger.retrieved == [TOPIC_HIT, OTHER_HIT]
    assert data.topics == ["topic-1"]
    tool_results = str(model.calls[1].messages[-1])
    assert "[Raft paper, p. 5]" in tool_results and "Leader election" in tool_results
    assert ledger.scan("".join(deltas)) == [TOPIC_HIT]


async def test_cancel_signal_stops_the_reply() -> None:
    model = ScriptedModel([Turn(["First words. ", Pause(0.3), "never sent"])])
    deltas: list[str] = []
    first = asyncio.Event()
    cancel = threading.Event()

    def on_event(**event: Any) -> None:
        if "data" in event:
            deltas.append(event["data"])
            first.set()

    agent = make_tutor_agent(
        model, TutorContext("general"), Data([], []).tools(), callback_handler=on_event
    )
    task = asyncio.create_task(agent.invoke_async("Explain", cancel_signal=cancel))
    await asyncio.wait_for(first.wait(), 2)
    cancel.set()
    result = await asyncio.wait_for(task, 2)
    assert result.stop_reason == "cancelled"
    assert deltas == ["First words. "]
