"""What the tutor knows about a conversation: the learner profile, the page context, and the
data callables its tools run on (design D10).

Everything here is plain data. The API builds these from its database; this package never
imports from ``app``.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from brainbench_agents.tutor.citations import citation_label

type ContextKind = Literal["topic", "plan", "general"]


@dataclass(frozen=True)
class SkillLine:
    name: str
    level: str


@dataclass(frozen=True)
class TutorProfile:
    """The parts of the learner profile that shape an explanation."""

    language: str = "en"
    depth: str = "intuition-first"
    current_skills: Sequence[SkillLine] = ()
    target_skills: Sequence[SkillLine] = ()
    background: str | None = None


@dataclass(frozen=True)
class TutorContext:
    """The page the tutor is bound to: one topic, the plan, or general (profile only)."""

    kind: ContextKind
    topic_id: str | None = None
    title: str | None = None
    summary: str | None = None
    profile: TutorProfile | None = None

    def describe(self) -> str:
        if self.kind == "topic":
            text = f'The engineer is on the topic page "{self.title or "untitled topic"}"'
            text += f" (topic id {self.topic_id})."
            if self.summary:
                text += f" Topic summary: {self.summary}"
            return text
        if self.kind == "plan":
            return (
                "The engineer is on the study plan page. Questions are likely about the plan as a "
                "whole: order, prerequisites, how topics relate. get_plan_summary lists it."
            )
        return "The engineer opened the tutor outside any topic or plan page (general context)."


@dataclass(frozen=True)
class ChunkRef:
    """One retrieved passage of the engineer's material, as the tutor sees and cites it."""

    chunk_id: str
    source_id: str
    source_title: str
    location: dict[str, Any]
    text: str
    score: float
    source_type: str | None = None
    uri: str | None = None

    @property
    def label(self) -> str:
        """The exact citation the model is told to copy: ``[source-title, location]``."""
        return citation_label(self.source_title, self.location)


type SearchFn = Callable[[str, int, str | None], Awaitable[list[ChunkRef]]]
"""``(query, k, topic_id)``: top ``k`` chunks, restricted to the topic's chunks when given."""

type LookupFn = Callable[[str], Awaitable[dict[str, Any] | None]]
type SummaryFn = Callable[[], Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True)
class TutorTools:
    """The data access behind the agent's tools, injected by the caller."""

    search_chunks: SearchFn
    get_topic: LookupFn
    get_plan_summary: SummaryFn
    get_profile: SummaryFn
    k: int = 8
    min_score: float = 0.6
