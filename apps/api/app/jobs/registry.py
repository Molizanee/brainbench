"""Job handler registry: the seam between the worker loop and job type modules.

A handler module under ``app/jobs/`` registers itself with ``@register("<type>")`` and is
imported (eagerly, for the registration side effect) from ``app/jobs/__init__.py``; heavy
dependencies inside a handler module are lazy imports, so loading every handler stays cheap.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

lazy from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class JobContext:
    """Everything a handler receives. The worker builds it per claimed job.

    ``save_checkpoint``, ``progress`` and ``is_cancelled`` are supplied by the worker loop;
    the defaults below only update the in-memory copy so handlers can be unit-tested without
    a worker.
    """

    job_id: UUID
    type: str
    payload: dict[str, Any]
    checkpoint: dict[str, Any]
    session: AsyncSession
    save_checkpoint_fn: Callable[[dict[str, Any]], Awaitable[None]] | None = field(
        default=None, repr=False
    )
    progress_fn: Callable[[str], Awaitable[None]] | None = field(default=None, repr=False)
    is_cancelled: Callable[[], bool] = field(default=lambda: False, repr=False)

    async def save_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        """Persist completed step outputs so a deferred or retried job resumes from them."""
        self.checkpoint = dict(checkpoint)
        if self.save_checkpoint_fn is not None:
            await self.save_checkpoint_fn(self.checkpoint)

    async def progress(self, message: str) -> None:
        """Set the job's human-readable progress message (emits a job event)."""
        if self.progress_fn is not None:
            await self.progress_fn(message)

    def cancelled(self) -> bool:
        """Cooperative cancellation flag; long steps check it between units of work."""
        return self.is_cancelled()


type Handler = Callable[[JobContext], Awaitable[None]]

HANDLERS: dict[str, Handler] = {}


def register(job_type: str) -> Callable[[Handler], Handler]:
    """Register ``handler`` for ``job_type``. Registering a type twice is a programming error."""

    def decorator(handler: Handler) -> Handler:
        if job_type in HANDLERS and HANDLERS[job_type] is not handler:
            raise ValueError(f"job type {job_type!r} is already registered")
        HANDLERS[job_type] = handler
        return handler

    return decorator
