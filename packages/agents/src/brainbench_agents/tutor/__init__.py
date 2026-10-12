"""Tutor agent (design D10): factory, tools over injected callables, citation contract."""

from brainbench_agents.tutor.agent import TOOL_NAMES, build_tools, make_tutor_agent, retrieve
from brainbench_agents.tutor.citations import citation_label, location_label
from brainbench_agents.tutor.context import (
    ChunkRef,
    ContextKind,
    SkillLine,
    TutorContext,
    TutorProfile,
    TutorTools,
)
from brainbench_agents.tutor.ledger import CitationLedger
from brainbench_agents.tutor.prompt import (
    NO_MATERIAL,
    build_system_prompt,
    build_turn_prompt,
    format_passages,
)

__all__ = [
    "NO_MATERIAL",
    "TOOL_NAMES",
    "ChunkRef",
    "CitationLedger",
    "ContextKind",
    "SkillLine",
    "TutorContext",
    "TutorProfile",
    "TutorTools",
    "build_system_prompt",
    "build_tools",
    "build_turn_prompt",
    "citation_label",
    "format_passages",
    "location_label",
    "make_tutor_agent",
    "retrieve",
]
