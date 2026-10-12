"""System prompts and user-prompt builders for the curation steps (design D6).

The builders take plain data (strings, dicts) so the job decides what goes in and the package
stays free of database types.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

TOPIC_EXTRACTION_SYSTEM = """\
You build a study curriculum for a software engineer from their own reading material.

You receive one window of consecutive chunks from a single source. Each chunk starts with a key \
in square brackets, for example [C3]. Identify the distinct, teachable topics the window covers: \
concepts, techniques, or mechanisms an engineer could study for 30 minutes to a few hours.

Rules:
- Every topic cites the keys of the chunks it was derived from, at least one, only keys you were \
given.
- Prefer a few substantial topics over many fragments; skip boilerplate, navigation, licences, \
acknowledgements, and changelogs.
- Titles are short noun phrases (no trailing punctuation). Summaries are one paragraph stating \
what the learner will understand, in the language of the material.
- A window with nothing teachable yields an empty list.
"""

TOPIC_MERGE_SYSTEM = """\
You deduplicate a study curriculum. You receive groups of candidate topics that were found to \
be near-duplicates (each group has a key such as G2). For every group, write the one topic that \
replaces its candidates: a title that covers all of them and a one-paragraph summary that keeps \
everything each candidate covers. Return exactly one merged topic per group, using its key.
"""

SCORING_SYSTEM = """\
You score study topics against a software engineer's learning profile.

You receive the profile (current skills with levels, target skills with priority and level, \
background, preferences) and a batch of topics, each with a key such as T4 and, when the engineer \
has studied it, their session history (minutes and self-rated mastery: struggled, understood, \
confident, could-teach).

For each topic return:
- relevance in [0, 1]: how much studying it now advances the target skills.
- serves_targets: the exact names of the target skills it serves; empty when it serves none.
- rationale: one or two sentences naming the target skill served and why, or why it is unrelated.
- already_held: true when a current skill at level "expert" covers it, or the history shows it \
mastered (latest rating confident or could-teach). Such topics score below 0.2 and the rationale \
says the skill is already held.
- estimate_minutes: focused study time for this engineer given their level and preferred depth.

Score every topic in the batch exactly once. Use the tools only when the provided data is not \
enough to judge a topic.
"""

PREREQUISITES_SYSTEM = """\
You order a study curriculum. You receive topics, each with a key such as T5, a title, and a \
summary. Return the direct prerequisite relations between them: topic X requires topic Y when \
X cannot be understood well without first studying Y. Give each relation a confidence in [0, 1]. \
Omit transitive relations and anything you are unsure of; never relate a topic to itself.
"""


@dataclass(frozen=True)
class WindowChunk:
    key: str
    text: str
    location: str = ""


def extraction_prompt(source_title: str, chunks: Sequence[WindowChunk]) -> str:
    lines = [f"Source: {source_title or 'untitled'}", ""]
    for chunk in chunks:
        where = f" ({chunk.location})" if chunk.location else ""
        lines.append(f"[{chunk.key}]{where}")
        lines.append(chunk.text.strip())
        lines.append("")
    lines.append("List the teachable topics in this window with their chunk keys.")
    return "\n".join(lines)


@dataclass(frozen=True)
class MergeCandidate:
    title: str
    summary: str


def merge_prompt(groups: dict[str, Sequence[MergeCandidate]]) -> str:
    lines = []
    for key, members in groups.items():
        lines.append(f"Group {key}:")
        lines.extend(f"- {m.title}: {m.summary}" for m in members)
        lines.append("")
    lines.append("Return one merged topic per group.")
    return "\n".join(lines)


def scoring_prompt(profile: dict[str, Any], topics: Sequence[dict[str, Any]]) -> str:
    return (
        "Profile:\n"
        + json.dumps(profile, indent=2, ensure_ascii=False)
        + "\n\nTopics:\n"
        + json.dumps(list(topics), indent=2, ensure_ascii=False)
        + "\n\nScore every topic."
    )


def prerequisites_prompt(topics: Sequence[dict[str, str]]) -> str:
    lines = [f"{t['key']}: {t['title']}: {t['summary']}" for t in topics]
    return "Topics:\n" + "\n".join(lines) + "\n\nReturn the direct prerequisite relations."
