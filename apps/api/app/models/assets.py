"""Contracts for generated assets and generation requests (task 7.6)."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.jobs import JobStatus, UtcDatetime

type VisualFormat = Literal["video", "figure"]
type AssetFileName = Literal["lesson.mp4", "lesson.vtt", "figure.html", "preview.png"]


class ChunkCitation(BaseModel):
    chunk_id: str
    source_id: UUID | None = None
    source_title: str | None = None
    location: dict[str, Any] = Field(default_factory=dict)
    excerpt: str | None = None


class SceneRead(BaseModel):
    id: str
    objective: str
    narration: str
    visual: str
    chunk_ids: list[str]
    citations: list[ChunkCitation]


class StoryboardRead(BaseModel):
    id: UUID
    title: str
    scenes: list[SceneRead]
    figure_brief: dict[str, Any] | None
    created_at: UtcDatetime


class AssetUrls(BaseModel):
    """Same-origin URLs served by ``GET /api/assets/{id}/{name}``."""

    video: str | None = None
    captions: str | None = None
    html: str | None = None
    preview: str | None = None


class BuildSourceFile(BaseModel):
    path: str
    size: int
    content: str | None = None  # omitted for large sources


class AssetRead(BaseModel):
    id: UUID
    topic_id: UUID
    format: VisualFormat
    is_current: bool
    created_at: UtcDatetime
    job_id: UUID | None
    previous_asset_id: UUID | None
    feedback: str | None
    duration_s: float | None
    urls: AssetUrls
    storyboard: StoryboardRead | None
    script: str | None
    build_source: list[BuildSourceFile]
    transcript_tail: str | None
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Render attempts, missing sections, session stop reason and turns.",
    )


class BudgetRead(BaseModel):
    render_minutes_used: float
    render_minutes_limit: int
    render_minutes_left: float
    render_resets_at: UtcDatetime
    jobs_today: int
    jobs_limit: int
    jobs_left: int
    jobs_reset_at: UtcDatetime


class GenerateRequest(BaseModel):
    formats: list[VisualFormat] = Field(min_length=1)

    @field_validator("formats")
    @classmethod
    def _unique(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class RegenerateRequest(BaseModel):
    feedback: str = Field(min_length=1, max_length=4000)

    @field_validator("feedback")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Describe what to change.")
        return value.strip()


class GenerationJob(BaseModel):
    topic_id: UUID
    title: str | None = None
    format: VisualFormat
    job_id: UUID | None  # None in a dry run for a job that would be created
    status: JobStatus | None
    deduplicated: bool


class GenerateResponse(BaseModel):
    jobs: list[GenerationJob]
    budget: BudgetRead


class PlanGenerateResponse(BaseModel):
    plan_version: int
    formats: list[VisualFormat]
    dry_run: bool
    job_count: int = Field(description="Jobs created, or that a real request would create.")
    jobs: list[GenerationJob]
    budget: BudgetRead
