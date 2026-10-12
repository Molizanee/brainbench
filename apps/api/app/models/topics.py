"""Contract of the topic page (``GET /api/topics/{id}``, task 7.6)."""

from uuid import UUID

from pydantic import BaseModel

from app.models.assets import AssetRead, BudgetRead
from app.models.jobs import JobRead, UtcDatetime
from app.models.progress import TopicStatus


class TopicStatsRead(BaseModel):
    minutes_studied: int
    session_count: int


class TopicRevisionRead(BaseModel):
    revision: int
    title: str
    created_by: str
    created_at: UtcDatetime


class PlanItemRead(BaseModel):
    plan_version: int
    position: int
    week_start: int | None
    week_span: int | None
    estimate_minutes: int | None
    relevance: float | None
    rationale: str | None
    excluded: bool


class TopicRead(BaseModel):
    id: UUID
    title: str
    summary: str
    status: TopicStatus
    stats: TopicStatsRead
    revision: int
    history: list[TopicRevisionRead]  # newest first
    assets: list[AssetRead]  # newest first; the current one per format has is_current
    plan_item: PlanItemRead | None  # in the latest plan version
    jobs: list[JobRead]  # the latest video and figure job, whatever their status
    budget: BudgetRead
