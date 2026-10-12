"""Study plan contracts: versions, weekly blocks, items with rationale and citations, edits."""

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.models.curation import ModelUsed
from app.models.jobs import UtcDatetime
from app.models.progress import TopicStatus

MAX_TITLE = 120
MAX_SUMMARY = 4000


class Citation(BaseModel):
    """A chunk a topic was derived from; ``url`` links to the location when the source has one."""

    chunk_id: UUID
    source_id: UUID
    source_title: str
    source_type: str
    location: dict[str, Any]
    url: str | None
    excerpt: str


class TargetRef(BaseModel):
    id: UUID
    name: str


class TopicRef(BaseModel):
    topic_id: UUID
    title: str


class PlanItemRead(BaseModel):
    topic_id: UUID
    topic_revision_id: UUID
    revision: int
    title: str
    summary: str
    position: int
    week_start: int | None
    week_span: int | None
    exceeds_weekly_budget: bool
    prerequisites: list[TopicRef]
    manually_included: bool
    excluded: bool
    relevance: float | None
    rationale: str | None
    serves_targets: list[TargetRef]
    estimate_minutes: int | None
    status: TopicStatus
    # Set when the topic was merged after this version was written; links go to the survivor.
    merged_into: UUID | None
    citations: list[Citation]


class PlanWeek(BaseModel):
    """A weekly block: one or more topics, or one oversized topic spanning several weeks."""

    week_start: int
    week_span: int
    exceeds_weekly_budget: bool
    total_minutes: int
    topic_ids: list[UUID]


class PlanRunInfo(BaseModel):
    id: UUID
    threshold: float
    models: dict[str, ModelUsed]
    finished_at: UtcDatetime | None


class PlanRead(BaseModel):
    version: int
    created_at: UtcDatetime
    created_by: Literal["run", "edit"]
    curation_run_id: UUID | None
    profile_version: int
    source_ids: list[UUID]
    current_version: int
    is_current: bool
    stale: bool
    stale_reasons: list[str]
    weekly_budget_minutes: int | None
    run: PlanRunInfo | None
    weeks: list[PlanWeek]
    items: list[PlanItemRead]
    excluded: list[PlanItemRead]


class PlanSummary(BaseModel):
    version: int
    created_at: UtcDatetime
    created_by: Literal["run", "edit"]


class ReorderEdit(BaseModel):
    """The full new order of the plan's (included) topics."""

    op: Literal["reorder"]
    order: list[UUID] = Field(min_length=1)


class RemoveEdit(BaseModel):
    op: Literal["remove"]
    topic_id: UUID


class MergeEdit(BaseModel):
    """Merge ``topic_id`` into ``into_topic_id`` (the survivor)."""

    op: Literal["merge"]
    topic_id: UUID
    into_topic_id: UUID


class TopicEdit(BaseModel):
    """New title and/or summary; creates a topic revision."""

    op: Literal["edit"]
    topic_id: UUID
    title: str | None = Field(default=None, min_length=1, max_length=MAX_TITLE)
    summary: str | None = Field(default=None, min_length=1, max_length=MAX_SUMMARY)

    @model_validator(mode="after")
    def _something(self) -> TopicEdit:
        if self.title is None and self.summary is None:
            raise ValueError("Give a new title or summary.")
        if self.title is not None and not self.title.strip():
            raise ValueError("The title cannot be blank.")
        if self.summary is not None and not self.summary.strip():
            raise ValueError("The summary cannot be blank.")
        return self


class IncludeEdit(BaseModel):
    op: Literal["include"]
    topic_id: UUID


type PlanEdit = Annotated[
    ReorderEdit | RemoveEdit | MergeEdit | TopicEdit | IncludeEdit, Field(discriminator="op")
]
