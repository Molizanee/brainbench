from brainbench_agents.tutor import (
    NO_MATERIAL,
    ChunkRef,
    SkillLine,
    TutorContext,
    TutorProfile,
    build_system_prompt,
    build_turn_prompt,
)

PROFILE = TutorProfile(
    language="pt-BR",
    depth="intuition-first",
    current_skills=(SkillLine("hash functions", "expert"), SkillLine("networking", "novice")),
    target_skills=(SkillLine("distributed systems", "proficient"),),
)


def test_system_prompt_carries_profile_context_and_citation_contract() -> None:
    context = TutorContext("topic", "t-1", "Consistent hashing", "Ring of nodes.", PROFILE)
    prompt = build_system_prompt(context)
    assert "Reply language: pt-BR" in prompt
    assert "intuitive picture or analogy" in prompt and "before any formal definition" in prompt
    assert "proficient or expert in: hash functions" in prompt
    assert "never define or re-explain them" in prompt
    assert "networking (novice)" in prompt and "distributed systems (goal: proficient)" in prompt
    assert '"Consistent hashing" (topic id t-1)' in prompt and "Ring of nodes." in prompt
    assert "[source-title, location]" in prompt
    assert "say so first" in prompt


def test_rigor_first_and_missing_profile() -> None:
    rigor = TutorProfile(depth="rigor-first")
    assert "Lead with the precise definition" in build_system_prompt(
        TutorContext("plan", profile=rigor)
    )
    general = build_system_prompt(TutorContext("general"))
    assert "Learner profile: none yet" in general and "general context" in general


def test_turn_prompt_labels_passages_or_states_the_gap() -> None:
    hit = ChunkRef("c1", "s1", "Raft paper", {"page": 5}, "A candidate needs a majority.", 0.8)
    grounded = build_turn_prompt(" Why a majority? ", [hit])
    assert grounded.startswith("Why a majority?")
    assert "[Raft paper, p. 5]\nA candidate needs a majority." in grounded
    assert NO_MATERIAL in build_turn_prompt("What is a CRDT?", [])
