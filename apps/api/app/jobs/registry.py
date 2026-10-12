"""Job handler registry: the seam between the worker loop and job type modules.

A handler module under ``app/jobs/`` registers itself with ``@register("<type>")`` and is
imported (eagerly, for the registration side effect) from ``app/jobs/__init__.py``; heavy
dependencies inside a handler module are lazy imports, so loading every handler stays cheap.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.jobs.errors import JobCancelled

__lazy_modules__ = {"sqlalchemy.ext.asyncio"}
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

type KillHook = Callable[[], Awaitable[None]]


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
    kill_hooks: list[KillHook] = field(default_factory=list, repr=False)

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

    def raise_if_cancelled(self) -> None:
        """Raise ``JobCancelled`` when ``cancelled()``; call it between units of work."""
        if self.cancelled():
            raise JobCancelled()

    def register_kill_hook(self, hook: KillHook) -> Callable[[], None]:
        """Register ``hook`` to run when the job is cancelled, loses its lease, or the worker stops.

        A sandbox runner registers a hook that kills its container right after starting it and
        calls the returned function to unregister once the container has exited. Hooks run at
        most once; ``run_kill_hooks`` empties the list.
        """
        self.kill_hooks.append(hook)

        def unregister() -> None:
            if hook in self.kill_hooks:
                self.kill_hooks.remove(hook)

        return unregister

    async def run_kill_hooks(self) -> None:
        """Run and clear every registered kill hook; one failing hook does not stop the others."""
        hooks, self.kill_hooks = self.kill_hooks, []
        for hook in hooks:
            try:
                await hook()
            except Exception:
                log.exception("kill hook failed for job %s", self.job_id)


type Handler = Callable[[JobContext], Awaitable[None]]

HANDLERS: dict[str, Handler] = {}

type ClaimGate = Callable[[AsyncSession], Awaitable[bool]]

CLAIM_GATES: dict[str, ClaimGate] = {}


def register(job_type: str) -> Callable[[Handler], Handler]:
    """Register ``handler`` for ``job_type``. Registering a type twice is a programming error."""

    def decorator(handler: Handler) -> Handler:
        if job_type in HANDLERS and HANDLERS[job_type] is not handler:
            raise ValueError(f"job type {job_type!r} is already registered")
        HANDLERS[job_type] = handler
        return handler

    return decorator


def register_claim_gate(job_type: str) -> Callable[[ClaimGate], ClaimGate]:
    """Register a check the worker runs before claiming a job of ``job_type``.

    The gate receives the claim transaction's session and returns False to leave every queued
    job of that type queued for now (for example while a budget is exhausted). Registering a
    second gate for a type is a programming error.
    """

    def decorator(gate: ClaimGate) -> ClaimGate:
        if job_type in CLAIM_GATES and CLAIM_GATES[job_type] is not gate:
            raise ValueError(f"claim gate for {job_type!r} is already registered")
        CLAIM_GATES[job_type] = gate
        return gate

    return decorator
