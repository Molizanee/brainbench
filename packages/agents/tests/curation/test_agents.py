import json

import pytest
from strands.event_loop._retry import ModelRetryStrategy
from strands.types.exceptions import ModelThrottledException

from brainbench_agents.curation import (
    CURATION_TASK_CLASSES,
    ExtractedTopic,
    MergeCandidate,
    PrerequisiteEdge,
    Prerequisites,
    TopicExtraction,
    TopicMerge,
    TopicScore,
    TopicScoring,
    WindowChunk,
    extraction_prompt,
    make_get_profile,
    make_get_sessions,
    make_search_chunks,
    merge_prompt,
    prerequisites_agent,
    prerequisites_prompt,
    run_structured,
    scoring_agent,
    scoring_prompt,
    topic_extraction_agent,
    topic_merge_agent,
)
from brainbench_agents.curation.prompts import SCORING_SYSTEM, TOPIC_EXTRACTION_SYSTEM
from brainbench_agents.testing import ScriptedModel, Structured, Text, ToolCall


def test_task_classes_in_pipeline_order() -> None:
    assert CURATION_TASK_CLASSES == ("topic_extraction", "topic_merge", "scoring", "prerequisites")


async def test_extraction_agent_returns_the_structured_answer_with_its_system_prompt() -> None:
    answer = TopicExtraction(
        topics=[ExtractedTopic(title="Raft leader election", summary="s", chunk_keys=["C1"])]
    )
    model = ScriptedModel([Structured(answer)])
    agent = topic_extraction_agent(model)

    result = await run_structured(agent, TopicExtraction, "window")

    assert result == answer
    assert model.calls[0].system_prompt == TOPIC_EXTRACTION_SYSTEM
    assert model.calls[0].tool_names == ["TopicExtraction"]


async def test_agent_may_call_its_tools_before_answering() -> None:
    searched: list[tuple[str, int]] = []

    async def search(query: str, k: int) -> list[dict[str, object]]:
        searched.append((query, k))
        return [{"chunk_id": "c-1", "text": "Raft elects a leader."}]

    model = ScriptedModel(
        [
            ToolCall("search_chunks", {"query": "leader election", "k": 50}),
            Structured(TopicExtraction(topics=[])),
        ]
    )
    agent = topic_extraction_agent(model, search_chunks=make_search_chunks(search))

    await run_structured(agent, TopicExtraction, "window")

    assert searched == [("leader election", 20)]  # k is capped
    assert model.calls[0].tool_names == ["search_chunks", "TopicExtraction"]
    tool_result = model.calls[1].messages[-1]["content"][0]["toolResult"]
    assert json.loads(tool_result["content"][0]["text"])[0]["chunk_id"] == "c-1"


async def test_each_call_starts_from_an_empty_conversation() -> None:
    model = ScriptedModel([Structured(TopicMerge(merged=[])), Structured(TopicMerge(merged=[]))])
    agent = topic_merge_agent(model)

    await run_structured(agent, TopicMerge, "first")
    await run_structured(agent, TopicMerge, "second")

    assert model.calls[1].prompt == "second"
    assert all("first" not in str(m) for m in model.calls[1].messages)


async def test_usage_hook_reports_each_invocation_once() -> None:
    log: list[tuple[str, dict[str, int]]] = []
    model = ScriptedModel(
        [
            ToolCall("get_profile", {}),
            Structured(TopicScoring(scores=[])),
            Structured(TopicScoring(scores=[])),
        ],
        usage=(100, 20),
    )

    async def profile() -> dict[str, object]:
        return {"target_skills": []}

    agent = scoring_agent(model, get_profile=make_get_profile(profile), usage_sink=_sink(log))

    await run_structured(agent, TopicScoring, "batch 1")
    await run_structured(agent, TopicScoring, "batch 2")

    assert log == [
        ("scoring", {"input_tokens": 200, "output_tokens": 40, "total_tokens": 240}),
        ("scoring", {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}),
    ]
    assert model.calls[0].system_prompt == SCORING_SYSTEM


async def test_a_throttle_propagates_and_is_still_logged() -> None:
    log: list[tuple[str, dict[str, int]]] = []
    model = ScriptedModel([ModelThrottledException("limit")])
    agent = prerequisites_agent(
        model, usage_sink=_sink(log), retry_strategy=ModelRetryStrategy(max_attempts=1)
    )

    with pytest.raises(Exception) as caught:
        await run_structured(agent, Prerequisites, "topics")

    assert _has_throttle(caught.value)
    assert log == [("prerequisites", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})]


async def test_a_text_only_answer_is_forced_into_the_output_model() -> None:
    edges = Prerequisites(edges=[PrerequisiteEdge(topic="T2", requires="T1", confidence=0.9)])
    model = ScriptedModel([Text("T2 needs T1."), Structured(edges)])

    result = await run_structured(prerequisites_agent(model), Prerequisites, "topics")

    assert result == edges


async def test_get_sessions_passes_the_keys_through() -> None:
    asked: list[list[str]] = []

    async def load(keys: list[str]) -> dict[str, list[dict[str, object]]]:
        asked.append(keys)
        return {key: [{"minutes": 30, "rating": "understood"}] for key in keys}

    model = ScriptedModel(
        [
            ToolCall("get_sessions", {"topics": ["T1", "T2"]}),
            Structured(
                TopicScoring(
                    scores=[
                        TopicScore(
                            topic="T1",
                            relevance=0.8,
                            serves_targets=["Distributed systems"],
                            rationale="r",
                            already_held=False,
                            estimate_minutes=90,
                        )
                    ]
                )
            ),
        ]
    )
    agent = scoring_agent(model, get_sessions=make_get_sessions(load))

    result = await run_structured(agent, TopicScoring, "batch")

    assert asked == [["T1", "T2"]]
    assert result.scores[0].estimate_minutes == 90


def test_prompts_carry_keys_and_data() -> None:
    window = extraction_prompt(
        "Raft paper", [WindowChunk("C1", "Leaders send heartbeats.", "page 3")]
    )
    assert "[C1] (page 3)" in window and "Leaders send heartbeats." in window

    merge = merge_prompt({"G1": [MergeCandidate("Raft", "a"), MergeCandidate("Raft log", "b")]})
    assert "Group G1:" in merge and "- Raft log: b" in merge

    scoring = scoring_prompt({"target_skills": [{"name": "Consensus"}]}, [{"key": "T1"}])
    assert '"Consensus"' in scoring and '"T1"' in scoring

    prereq = prerequisites_prompt([{"key": "T1", "title": "Logs", "summary": "Append-only."}])
    assert "T1: Logs: Append-only." in prereq


def _sink(log: list[tuple[str, dict[str, int]]]):  # type: ignore[no-untyped-def]
    return lambda step, usage: log.append((step, usage))


def _has_throttle(exc: BaseException) -> bool:
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, ModelThrottledException):
            return True
        current = getattr(current, "original_exception", None) or current.__cause__
    return False
