from typing import Any
from uuid import uuid4

import pytest

from app.jobs import registry
from app.jobs.registry import HANDLERS, JobContext, register


def make_ctx(**overrides: Any) -> JobContext:
    values: dict[str, Any] = {
        "job_id": uuid4(),
        "type": "noop",
        "payload": {},
        "checkpoint": {},
        "session": None,
    }
    values.update(overrides)
    return JobContext(**values)


def test_register_stores_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "HANDLERS", {})

    @register("example")
    async def handler(ctx: JobContext) -> None:
        return None

    assert {"example": handler} == registry.HANDLERS


def test_register_twice_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "HANDLERS", {})

    @register("example")
    async def first(ctx: JobContext) -> None:
        return None

    with pytest.raises(ValueError, match="already registered"):

        @register("example")
        async def second(ctx: JobContext) -> None:
            return None


async def test_context_forwards_checkpoint_and_progress() -> None:
    saved: list[dict[str, Any]] = []
    messages: list[str] = []

    async def save(cp: dict[str, Any]) -> None:
        saved.append(cp)

    async def progress(message: str) -> None:
        messages.append(message)

    ctx = make_ctx(save_checkpoint_fn=save, progress_fn=progress, is_cancelled=lambda: True)
    await ctx.save_checkpoint({"step": 1})
    await ctx.progress("step 1 done")

    assert ctx.checkpoint == {"step": 1}
    assert saved == [{"step": 1}]
    assert messages == ["step 1 done"]
    assert ctx.cancelled() is True


async def test_noop_handler_is_registered_and_runs() -> None:
    import app.jobs  # noqa: F401

    ctx = make_ctx()
    await HANDLERS["noop"](ctx)
    assert ctx.checkpoint == {"done": True}
    assert ctx.cancelled() is False
