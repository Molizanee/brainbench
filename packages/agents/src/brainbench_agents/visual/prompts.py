"""System prompts and prompt builders for the visual-generation agents (design D7)."""

import json
from collections.abc import Sequence
from dataclasses import dataclass

from brainbench_agents.visual.manim import MANIM_VERSION, template_for
from brainbench_agents.visual.models import ChunkRef, Depth, Format, Storyboard

DEPTH_RULES: dict[str, str] = {
    "intuition-first": (
        "Depth preference: intuition-first. The first scene states the intuition (what the idea "
        "is for, a concrete picture or analogy) before any formal definition, notation, or "
        "equation appears anywhere in the storyboard. Formal statements come only in later scenes."
    ),
    "rigor-first": (
        "Depth preference: rigor-first. Open with the precise definition or statement, then build "
        "the intuition and examples from it."
    ),
}

FORMAT_RULES: dict[str, str] = {
    "video": (
        "Format: video. The storyboard becomes a Manim animation with narration captions. Plan "
        "3 to 7 scenes of 10 to 40 seconds each. Each scene's visual must be drawable with Manim "
        "primitives (shapes, arrows, text, MathTex, graphs, tables) and say what animates."
    ),
    "figure": (
        "Format: figure. The storyboard becomes one interactive isometric figure: a real, "
        "well-made physical object or mechanism whose working is the concept. Plan 2 to 5 scenes "
        "that walk through what the figure shows: the object, its moving parts, the controls the "
        "reader uses, and the live readout. Prefer true numbers (sizes, counts, rates, "
        "thresholds) from the material."
    ),
}

STORYBOARD_SYSTEM = """\
You are BrainBench's storyboard writer. You turn one topic of an engineer's study plan into an \
ordered storyboard that teaches it visually.

Rules:
- Ground every scene in the source material. Use the search_chunks tool to find passages \
(it searches only the chunks this topic references). Every scene lists in chunk_ids at least \
one chunk id returned by the tool or listed in the prompt; never invent ids.
- Each scene has: a short snake_case id unique in the storyboard, the learning objective, the \
narration (spoken text, 1 to 4 sentences), and the visual (what is on screen, what moves).
- Write the narration and the title in the learner's language: {language}. Ids stay in English \
snake_case.
- {depth_rule}
- {format_rule}
- Teach the learner in front of you: build on what they already know and do not re-explain it.
- Finish by returning the Storyboard structured output."""

FIGURE_BRIEF_SYSTEM = """\
You derive the brief for an interactive isometric figure (built later with the Anatomy skill) \
from a storyboard and the source chunks it cites.

The figure explains the concept as a real physical object or mechanism whose working is the \
concept (a gauge, a lock, a plotter, a cabinet of drawers, a test rig), never as boxes, arrows \
and labels.

Decide first whether the topic has a mechanism, object, or structure to build. When it does \
not (for example a purely historical, legal, or definitional topic), return applicable = false \
with the rationale and nothing else.

When it does, return applicable = true with:
- object: the object or mechanism and why it embodies the concept;
- parts: its main parts and what each does;
- true_numbers: the real quantities the figure must show (sizes, counts, rates, thresholds), \
each with the value as the material states it and the chunk id that states it when there is one;
- motion: what moves and what drives it (the control the reader uses);
- readout: the live readout the figure shows and how it is computed from the same model;
- theme: dark unless the material suggests otherwise.
Return the FigureBrief structured output."""

MANIM_SCRIPT_SYSTEM = """\
You write Manim Community v{version} scripts that render a storyboard as a narrated lesson video.

The file contract is fixed; a script that breaks it is rejected before rendering:
- Exactly one class, Lesson(Scene). No other Scene subclasses.
- Lesson.construct(self) calls self.next_section(name="<scene-id>") at the start of every \
storyboard scene, once per scene, in storyboard order, with the scene id as a string literal. \
Its first statement is the first next_section call.
- Each section's animations last about as long as its narration takes to read aloud \
(about 2.5 words per second); use self.wait() to hold.
- Use only Manim and the Python standard library (numpy is available). No files, network, \
audio, images, or LaTeX packages beyond the defaults. Prefer Text over Tex when no maths is \
needed. Keep everything inside the 14 x 8 frame and clear the previous scene before the next.
- Write the on-screen text in the learner's language.

Template:
```python
{template}
```
Return the ManimScript structured output whose script field is the whole file."""

SCRIPT_REPAIR_SYSTEM = """\
You repair Manim Community v{version} scripts that failed to render in a sandbox (no network, \
read-only source, 15-minute limit). You get the storyboard, the failing script, and the end of \
the render log; the read_render_log tool reads any part of the full log. Find the cause, fix it \
with the smallest change that keeps every scene, and keep the file contract: exactly one \
Lesson(Scene), and self.next_section(name="<scene-id>") at the start of every storyboard scene \
in order. Return the ManimScript structured output with the whole corrected file."""


@dataclass(frozen=True)
class Learner:
    """What the prompts need from the learner profile."""

    language: str = "en"
    depth: Depth = "intuition-first"
    background: str = ""
    skills: Sequence[str] = ()


@dataclass(frozen=True)
class StoryboardRequest:
    topic_title: str
    topic_summary: str
    format: Format
    learner: Learner
    seed_chunks: Sequence[ChunkRef] = ()
    previous: Storyboard | None = None
    feedback: str | None = None


def storyboard_system(request: StoryboardRequest) -> str:
    return STORYBOARD_SYSTEM.format(
        language=request.learner.language,
        depth_rule=DEPTH_RULES[request.learner.depth],
        format_rule=FORMAT_RULES[request.format],
    )


def chunk_block(chunks: Sequence[ChunkRef], *, max_chars: int = 900) -> str:
    lines = []
    for chunk in chunks:
        text = " ".join(chunk.text.split())
        if len(text) > max_chars:
            text = text[: max_chars - 1] + "…"
        where = json.dumps(chunk.location, ensure_ascii=False) if chunk.location else ""
        lines.append(f"[{chunk.chunk_id}] {chunk.source_title} {where}\n{text}")
    return "\n\n".join(lines)


def storyboard_prompt(request: StoryboardRequest) -> str:
    learner = request.learner
    parts = [
        f"Topic: {request.topic_title}",
        f"Summary: {request.topic_summary}",
        f"Learner background: {learner.background or 'not given'}",
        f"Learner already knows: {', '.join(learner.skills) or 'nothing listed'}",
    ]
    if request.seed_chunks:
        parts.append("Chunks referenced by the topic (id, source, excerpt):")
        parts.append(chunk_block(request.seed_chunks))
    if request.previous is not None:
        parts.append(
            "Previous storyboard for this topic (the learner asked for a new version):\n"
            + request.previous.model_dump_json(indent=2)
        )
    if request.feedback:
        parts.append(f"Learner feedback on the previous version, address it: {request.feedback}")
    parts.append("Write the storyboard.")
    return "\n\n".join(parts)


def grounding_correction(bad: dict[str, list[str]], allowed: Sequence[str]) -> str:
    listing = "; ".join(f"{scene}: {', '.join(ids) or 'none'}" for scene, ids in bad.items())
    return (
        "Some scenes cite no chunk this topic references (" + listing + "). Every scene must cite "
        "at least one of these chunk ids, found with search_chunks: "
        + ", ".join(allowed[:60])
        + ". Return the corrected Storyboard."
    )


def figure_brief_prompt(
    storyboard: Storyboard, chunks: Sequence[ChunkRef], feedback: str | None = None
) -> str:
    parts = [
        "Storyboard:\n" + storyboard.model_dump_json(indent=2),
        "Cited chunks:\n" + chunk_block(chunks, max_chars=2000),
    ]
    if feedback:
        parts.append(f"Learner feedback on the previous figure, address it: {feedback}")
    parts.append("Write the figure brief.")
    return "\n\n".join(parts)


def manim_system(storyboard: Storyboard) -> str:
    return MANIM_SCRIPT_SYSTEM.format(
        version=MANIM_VERSION, template=template_for(storyboard.scene_ids).rstrip()
    )


def manim_prompt(
    storyboard: Storyboard,
    *,
    language: str,
    previous_script: str | None = None,
    feedback: str | None = None,
) -> str:
    parts = [
        f"Learner language: {language}",
        "Storyboard (scene ids in order: "
        + ", ".join(storyboard.scene_ids)
        + "):\n"
        + storyboard.model_dump_json(indent=2),
    ]
    if previous_script:
        parts.append("Previous script for this topic:\n```python\n" + previous_script + "\n```")
    if feedback:
        parts.append(f"Learner feedback on the previous video, address it: {feedback}")
    parts.append("Write the Manim script.")
    return "\n\n".join(parts)


def repair_system() -> str:
    return SCRIPT_REPAIR_SYSTEM.format(version=MANIM_VERSION)


def repair_prompt(
    storyboard: Storyboard, script: str, log_tail: str, problems: Sequence[str], attempt: int
) -> str:
    parts = [
        f"Render attempt {attempt} failed.",
        "Storyboard (scene ids in order: "
        + ", ".join(storyboard.scene_ids)
        + "):\n"
        + storyboard.model_dump_json(indent=2),
        "Failing script:\n```python\n" + script + "\n```",
    ]
    if problems:
        parts.append("Contract problems:\n- " + "\n- ".join(problems))
    parts.append("End of the render log:\n```\n" + log_tail + "\n```")
    parts.append("Return the corrected script.")
    return "\n\n".join(parts)
