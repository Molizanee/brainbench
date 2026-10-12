"""System prompt and per-turn prompt of the tutor (tutor-chat spec, design D10)."""

from collections.abc import Sequence

from brainbench_agents.tutor.context import ChunkRef, TutorContext, TutorProfile

KNOWN_LEVELS = frozenset({"proficient", "expert"})

NO_MATERIAL = (
    "Retrieval found no passage of the engineer's material relevant to this question. Unless "
    "search_chunks with another query finds one, begin your reply by saying plainly that the "
    "engineer's material does not cover this, then answer from general knowledge with no "
    "citations."
)

CITATION_CONTRACT = """\
Citation contract:
- Every claim you take from the engineer's material cites the passage inline, right after the \
claim, with the passage's label copied exactly as given, in the form [source-title, location], \
for example [Raft paper, p. 5].
- Only cite labels that were shown to you in a retrieved passage. Never invent a source, a \
title, a page, or a path.
- When no retrieved passage covers the question, say so first ("Your material does not cover \
this." in the reply language), then explain from general knowledge without any citation.
- Do not cite general knowledge."""


def _depth_rule(depth: str) -> str:
    if depth == "rigor-first":
        return (
            "Explanation depth: rigor-first. Lead with the precise definition or statement, then "
            "the mechanism, then intuition and examples."
        )
    return (
        "Explanation depth: intuition-first. Start with an intuitive picture or analogy in plain "
        "words before any formal definition, notation, or proof."
    )


def _profile_block(profile: TutorProfile | None) -> str:
    if profile is None:
        return (
            "Learner profile: none yet. Reply in English, explain intuition first, and define "
            "technical terms when you first use them."
        )
    lines = [f"Reply language: {profile.language} (BCP-47). Always answer in this language."]
    lines.append(_depth_rule(profile.depth))
    if profile.background:
        lines.append(f"Background: {profile.background}")
    if profile.current_skills:
        skills = ", ".join(f"{s.name} ({s.level})" for s in profile.current_skills)
        lines.append(f"Current skills: {skills}.")
        known = [s.name for s in profile.current_skills if s.level in KNOWN_LEVELS]
        if known:
            lines.append(
                "The engineer is proficient or expert in: "
                + ", ".join(known)
                + ". Assume these; never define or re-explain them, refer to them directly."
            )
    lines.append(
        "Define any other technical term the first time you use it, at the level of the "
        "engineer's current skills."
    )
    if profile.target_skills:
        targets = ", ".join(f"{s.name} (goal: {s.level})" for s in profile.target_skills)
        lines.append(f"Target skills being studied: {targets}.")
    return "Learner profile:\n" + "\n".join(f"- {line}" for line in lines)


def build_system_prompt(context: TutorContext) -> str:
    return "\n\n".join(
        [
            "You are BrainBench's tutor: you explain the concepts behind what a software "
            "engineer is studying, grounded in the engineer's own ingested material (papers, "
            "web pages, repositories, notes), their learner profile, and their study plan.",
            _profile_block(context.profile),
            f"Context: {context.describe()}",
            CITATION_CONTRACT,
            "Tools: search_chunks finds more passages (the current topic's material first, then "
            "every source); get_topic, get_plan_summary and get_profile return the topic, the "
            "current plan, and the full profile. Use them when the passages given with the "
            "question are not enough. Answer in Markdown, concisely.",
        ]
    )


def format_passages(refs: Sequence[ChunkRef]) -> str:
    """Passages as the model sees them: each under the exact label it must cite."""
    if not refs:
        return NO_MATERIAL
    blocks = [f"{ref.label}\n{ref.text.strip()}" for ref in refs]
    return (
        "Passages from the engineer's material (cite with the label above each one):\n\n"
        + "\n\n---\n\n".join(blocks)
    )


def build_turn_prompt(question: str, refs: Sequence[ChunkRef]) -> str:
    material = format_passages(refs)
    return f"{question.strip()}\n\n<retrieved_material>\n{material}\n</retrieved_material>"
