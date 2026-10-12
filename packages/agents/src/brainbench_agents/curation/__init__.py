"""Curation agents (design D6): output models, prompts, agent factories, and tools.

Task classes ``topic_extraction``, ``topic_merge``, ``scoring`` and ``prerequisites``; the
curation job in the app orchestrates them and owns every deterministic step.
"""

from brainbench_agents.curation.agents import (
    CURATION_TASK_CLASSES,
    PREREQUISITES,
    SCORING,
    TOPIC_EXTRACTION,
    TOPIC_MERGE,
    UsageRecorder,
    UsageSink,
    prerequisites_agent,
    run_structured,
    scoring_agent,
    topic_extraction_agent,
    topic_merge_agent,
)
from brainbench_agents.curation.models import (
    ExtractedTopic,
    MergedTopic,
    PrerequisiteEdge,
    Prerequisites,
    TopicExtraction,
    TopicMerge,
    TopicScore,
    TopicScoring,
)
from brainbench_agents.curation.prompts import (
    MergeCandidate,
    WindowChunk,
    extraction_prompt,
    merge_prompt,
    prerequisites_prompt,
    scoring_prompt,
)
from brainbench_agents.curation.tools import (
    make_get_profile,
    make_get_sessions,
    make_search_chunks,
)

__all__ = [
    "CURATION_TASK_CLASSES",
    "PREREQUISITES",
    "SCORING",
    "TOPIC_EXTRACTION",
    "TOPIC_MERGE",
    "ExtractedTopic",
    "MergeCandidate",
    "MergedTopic",
    "PrerequisiteEdge",
    "Prerequisites",
    "TopicExtraction",
    "TopicMerge",
    "TopicScore",
    "TopicScoring",
    "UsageRecorder",
    "UsageSink",
    "WindowChunk",
    "extraction_prompt",
    "make_get_profile",
    "make_get_sessions",
    "make_search_chunks",
    "merge_prompt",
    "prerequisites_agent",
    "prerequisites_prompt",
    "run_structured",
    "scoring_agent",
    "scoring_prompt",
    "topic_extraction_agent",
    "topic_merge_agent",
]
