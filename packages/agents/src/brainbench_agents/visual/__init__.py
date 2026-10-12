"""Visual content generation agents (design D7): storyboard, figure brief, Manim script, script
repair, and the figure-building session. Never imports from ``app``."""

from brainbench_agents.visual.agents import (
    NoStructuredOutput,
    SearchFn,
    StoryboardNotGrounded,
    generate_figure_brief,
    generate_manim_script,
    generate_storyboard,
    make_figure_brief_agent,
    make_manim_script_agent,
    make_script_repair_agent,
    make_storyboard_agent,
    repair_manim_script,
)
from brainbench_agents.visual.manim import check_script, template_for
from brainbench_agents.visual.models import (
    FORMATS,
    ChunkRef,
    Depth,
    FigureBrief,
    Format,
    ManimScript,
    Storyboard,
    StoryboardScene,
    TrueNumber,
)
from brainbench_agents.visual.prompts import Learner, StoryboardRequest

__all__ = [
    "FORMATS",
    "ChunkRef",
    "Depth",
    "FigureBrief",
    "Format",
    "Learner",
    "ManimScript",
    "NoStructuredOutput",
    "SearchFn",
    "Storyboard",
    "StoryboardNotGrounded",
    "StoryboardRequest",
    "StoryboardScene",
    "TrueNumber",
    "check_script",
    "generate_figure_brief",
    "generate_manim_script",
    "generate_storyboard",
    "make_figure_brief_agent",
    "make_manim_script_agent",
    "make_script_repair_agent",
    "make_storyboard_agent",
    "repair_manim_script",
    "template_for",
]
