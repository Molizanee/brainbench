"""Structured outputs of the curation steps (design D6).

Each model is the ``structured_output_model`` of one agent call. Topics and chunks are named
by short keys the prompt assigns (``C3`` for a chunk, ``T7`` for a topic) so the model never has
to copy a UUID; the job maps the keys back and drops anything it did not offer.
"""

from pydantic import BaseModel, Field


class ExtractedTopic(BaseModel):
    """One concept the window teaches, with the chunks it was derived from."""

    title: str = Field(description="Short noun phrase naming the concept, at most 80 characters.")
    summary: str = Field(
        description="One paragraph (2 to 5 sentences) on what the learner will understand."
    )
    chunk_keys: list[str] = Field(
        description="Keys of the chunks (for example C3) that support this topic; at least one."
    )


class TopicExtraction(BaseModel):
    """Candidate topics found in one window of a source."""

    topics: list[ExtractedTopic] = Field(
        description="Teachable topics in this window; empty when the window has none."
    )


class MergedTopic(BaseModel):
    """The single topic that replaces one group of near-duplicate candidates."""

    group: str = Field(description="The group key (for example G2) being merged.")
    title: str = Field(description="Title covering every candidate in the group.")
    summary: str = Field(description="One paragraph combining what the candidates cover.")


class TopicMerge(BaseModel):
    """One merged topic per colliding group."""

    merged: list[MergedTopic]


class TopicScore(BaseModel):
    """Relevance of one topic to the learner's targets."""

    topic: str = Field(description="The topic key (for example T4).")
    relevance: float = Field(
        ge=0.0, le=1.0, description="0 = does not advance any target, 1 = essential."
    )
    serves_targets: list[str] = Field(
        description="Names of the target skills this topic serves; empty when unrelated."
    )
    rationale: str = Field(
        description="Which target skill the topic serves and why, or why it is unrelated."
    )
    already_held: bool = Field(
        description="True when the learner already holds this material (a current skill at "
        "expert level, or the session history shows mastery)."
    )
    estimate_minutes: int = Field(
        ge=5, le=6000, description="Focused study time for this learner, in minutes."
    )


class TopicScoring(BaseModel):
    scores: list[TopicScore]


class PrerequisiteEdge(BaseModel):
    """``topic`` cannot be studied well before ``requires``."""

    topic: str = Field(description="Key of the dependent topic (for example T5).")
    requires: str = Field(description="Key of the topic that must come first.")
    confidence: float = Field(ge=0.0, le=1.0, description="How sure you are that it is needed.")


class Prerequisites(BaseModel):
    edges: list[PrerequisiteEdge] = Field(
        description="Direct prerequisite relations only; omit transitive ones."
    )
