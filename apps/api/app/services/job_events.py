"""Job events: one ``job_events`` row per status change and progress update."""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.schema import Job, JobEvent

log = logging.getLogger(__name__)

EVENT_KINDS = ("status", "progress")


async def record_event(
    session: AsyncSession,
    job: Job,
    kind: str,
    *,
    message: str | None = None,
    data: dict[str, Any] | None = None,
) -> JobEvent:
    """Insert an event for ``job`` in the caller's transaction (flushed, not committed)."""
    event = JobEvent(job_id=job.id, kind=kind, status=job.status, message=message, data=data or {})
    session.add(event)
    await session.flush()
    return event
