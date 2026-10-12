"""BrainBench provider layer (design D2).

Registry, vault, OAuth engines, refresher, usage, account selection, wire transports, and
Strands model adapters. Pure Python; never imports from ``app``.
"""

__version__ = "0.1.0"

TASK_CLASSES: tuple[str, ...] = (
    "curation",
    "storyboarding",
    "script-generation",
    "script-repair",
    "figure-building",
    "tutoring",
    # Curation steps (section 6); each falls back to the "curation" route when not routed.
    "topic_extraction",
    "topic_merge",
    "scoring",
    "prerequisites",
)
