from collections.abc import Sequence
from typing import Any

import pytest

from brainbench_agents.visual import (
    ChunkRef,
    Learner,
    StoryboardNotGrounded,
    StoryboardRequest,
    generate_figure_brief,
    generate_manim_script,
    generate_storyboard,
    repair_manim_script,
)
from brainbench_agents.visual.testing import ScriptedModel, call, result_text, say, tool_results

CHUNKS = [
    ChunkRef(
        "c1", "Raft paper", "A candidate wins an election with votes from a majority.", {"page": 3}
    ),
    ChunkRef("c2", "Raft paper", "Leaders send heartbeats to keep followers.", {"page": 4}),
]


class Search:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def __call__(self, query: str, k: int) -> Sequence[ChunkRef]:
        self.calls.append((query, k))
        return CHUNKS[:k]


def board(*scenes: tuple[str, list[str]]) -> dict[str, Any]:
    return {
        "title": "Raft leader election",
        "scenes": [
            {"id": i, "objective": "o", "narration": "n", "visual": "v", "chunk_ids": c}
            for i, c in scenes
        ],
    }


def request(**overrides: Any) -> StoryboardRequest:
    values: dict[str, Any] = {
        "topic_title": "Raft leader election",
        "topic_summary": "How Raft elects a leader.",
        "format": "video",
        "learner": Learner(language="pt-BR", depth="intuition-first", skills=["TCP"]),
        "seed_chunks": CHUNKS[:1],
    }
    values.update(overrides)
    return StoryboardRequest(**values)


async def test_storyboard_searches_chunks_and_returns_structured_output() -> None:
    search = Search()
    model = ScriptedModel(
        [
            [call("search_chunks", {"query": "election majority", "k": 50})],
            [say("Writing it."), call("Storyboard", board(("intro", ["c1"]), ("vote", ["c2"])))],
        ]
    )
    storyboard = await generate_storyboard(model, search, request(), allowed_chunk_ids={"c1", "c2"})
    assert storyboard.scene_ids == ["intro", "vote"]
    assert search.calls == [("election majority", 20)]  # k is capped
    first = model.requests[0]
    assert first["tools"] == ["search_chunks", "Storyboard"]
    system = first["system_prompt"]
    assert "pt-BR" in system
    assert "first scene states the intuition" in system and "before any formal definition" in system
    assert "Format: video" in system
    prompt = first["last_message"]["content"][0]["text"]
    assert "[c1] Raft paper" in prompt and "TCP" in prompt
    results = tool_results([model.requests[1]["last_message"]])
    assert "[c2] Raft paper" in result_text(results[0])


async def test_rigor_first_and_figure_rules_reach_the_prompt() -> None:
    model = ScriptedModel([[call("Storyboard", board(("intro", ["c1"])))]])
    await generate_storyboard(
        model,
        Search(),
        request(format="figure", learner=Learner(depth="rigor-first")),
        allowed_chunk_ids={"c1"},
    )
    system = model.requests[0]["system_prompt"]
    assert "rigor-first" in system and "Format: figure" in system


async def test_ungrounded_scenes_are_sent_back_and_foreign_citations_dropped() -> None:
    model = ScriptedModel(
        [
            [call("Storyboard", board(("intro", ["c9"]), ("vote", ["c2", "c9"])))],
            [call("Storyboard", board(("intro", ["c1", "made-up"]), ("vote", ["c2"])))],
        ]
    )
    storyboard = await generate_storyboard(
        model, Search(), request(), allowed_chunk_ids=["c1", "c2"]
    )
    correction = model.requests[1]["last_message"]["content"][0]["text"]
    assert "intro: c9" in correction and "c1, c2" in correction
    assert [s.chunk_ids for s in storyboard.scenes] == [["c1"], ["c2"]]


async def test_storyboard_that_stays_ungrounded_fails() -> None:
    model = ScriptedModel([[call("Storyboard", board(("intro", ["c9"])))]] * 3)
    with pytest.raises(StoryboardNotGrounded, match="intro"):
        await generate_storyboard(model, Search(), request(), allowed_chunk_ids={"c1"})
    assert len(model.requests) == 3


async def test_previous_storyboard_and_feedback_reach_the_prompt() -> None:
    from brainbench_agents.visual import Storyboard

    previous = Storyboard.model_validate(board(("old_scene", ["c1"])))
    model = ScriptedModel([[call("Storyboard", board(("intro", ["c1"])))]])
    await generate_storyboard(
        model,
        Search(),
        request(previous=previous, feedback="too fast"),
        allowed_chunk_ids={"c1"},
    )
    prompt = model.requests[0]["last_message"]["content"][0]["text"]
    assert "old_scene" in prompt and "too fast" in prompt


async def test_figure_brief_from_storyboard_and_chunks() -> None:
    from brainbench_agents.visual import Storyboard

    storyboard = Storyboard.model_validate(board(("intro", ["c1"])))
    model = ScriptedModel(
        [[call("FigureBrief", {"applicable": False, "rationale": "Nothing moves."})]]
    )
    brief = await generate_figure_brief(model, storyboard, CHUNKS, feedback="bigger")
    assert not brief.applicable and brief.rationale == "Nothing moves."
    prompt = model.requests[0]["last_message"]["content"][0]["text"]
    assert "[c2] Raft paper" in prompt and "bigger" in prompt
    assert "applicable = false" in model.requests[0]["system_prompt"]


async def test_manim_script_and_repair_with_render_log_tool() -> None:
    from brainbench_agents.visual import Storyboard

    storyboard = Storyboard.model_validate(board(("intro", ["c1"]), ("vote", ["c2"])))
    model = ScriptedModel([[call("ManimScript", {"script": "print('v1')"})]])
    script = await generate_manim_script(
        model, storyboard, language="en", previous_script="old", feedback="slower"
    )
    assert script.script == "print('v1')"
    system = model.requests[0]["system_prompt"]
    assert 'self.next_section(name="intro")' in system and "class Lesson(Scene)" in system
    prompt = model.requests[0]["last_message"]["content"][0]["text"]
    assert "intro, vote" in prompt and "old" in prompt and "slower" in prompt

    log = (
        "\n".join(f"line {i}" for i in range(1, 501)) + "\nNameError: name 'Circel' is not defined"
    )
    repair = ScriptedModel(
        [
            [call("read_render_log", {"start_line": 2, "max_lines": 3})],
            [call("ManimScript", {"script": "fixed"})],
        ]
    )
    fixed = await repair_manim_script(
        repair, storyboard, "broken", log, problems=["no sections"], attempt=2
    )
    assert fixed.script == "fixed"
    prompt = repair.requests[0]["last_message"]["content"][0]["text"]
    assert "attempt 2" in prompt and "NameError" in prompt and "no sections" in prompt
    assert "line 1\n" not in prompt  # only the tail is inlined
    read = result_text(tool_results([repair.requests[1]["last_message"]])[0])
    assert read.startswith("lines 2-4 of 501") and "3: line 3" in read
