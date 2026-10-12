from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict

JobStatus = Literal["queued", "running", "ready", "failed", "cancelled", "skipped"]
JobEventKind = Literal["status", "progress"]


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_utc)]


class JobRead(BaseModel):
    """A job as the UI shows it; also the payload of the ``jobs.updated`` event."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    status: JobStatus
    priority: int
    payload: dict[str, Any]
    dedupe_key: str | None
    attempt: int
    max_attempts: int
    run_after: UtcDatetime
    progress: str | None
    error: str | None
    error_reason: str | None
    cancel_requested: bool
    created_at: UtcDatetime
    started_at: UtcDatetime | None
    finished_at: UtcDatetime | None
    updated_at: UtcDatetime


class JobEventRead(BaseModel):
    """One ``job_events`` row; also the payload of the ``jobs.event`` event."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: UUID
    kind: JobEventKind
    status: JobStatus | None
    message: str | None
    data: dict[str, Any]
    created_at: UtcDatetime


class JobDetail(JobRead):
    events: list[JobEventRead]
