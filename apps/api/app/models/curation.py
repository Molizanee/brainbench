"""Curation run contracts (curriculum-curation spec: runs are reproducible and auditable)."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.jobs import JobStatus, UtcDatetime


class CurationRunCreate(BaseModel):
    threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Relevance below which topics are excluded; the instance default when unset",
    )


class ModelUsed(BaseModel):
    provider: str
    model: str


class CurationRunRead(BaseModel):
    """A run with its job state, inputs, models, and step log."""

    id: UUID
    job_id: UUID | None
    status: JobStatus | None
    progress: str | None
    error: str | None
    profile_version: int
    source_ids: list[UUID]
    threshold: float
    models: dict[str, ModelUsed]
    step_log: list[dict[str, Any]]
    plan_version: int | None
    created_at: UtcDatetime
    finished_at: UtcDatetime | None
