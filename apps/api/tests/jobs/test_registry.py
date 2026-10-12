from typing import Any
from uuid import uuid4

import pytest

from app.jobs import registry
from app.jobs.errors import JobCancelled
from app.jobs.registry import HANDLERS, JobContext, register, register_claim_gate


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
    import app.jobs.handlers  # noqa: F401

    ctx = make_ctx()
    await HANDLERS["noop"](ctx)
    assert ctx.checkpoint == {"done": True}
    assert ctx.cancelled() is False


async def test_kill_hooks_run_once_and_can_be_unregistered() -> None:
    calls: list[str] = []

    async def kill_a() -> None:
        calls.append("a")

    async def kill_b() -> None:
        raise RuntimeError("container already gone")

    async def kill_c() -> None:
        calls.append("c")

    ctx = make_ctx()
    ctx.register_kill_hook(kill_a)
    ctx.register_kill_hook(kill_b)
    unregister_c = ctx.register_kill_hook(kill_c)
    unregister_c()
    await ctx.run_kill_hooks()
    await ctx.run_kill_hooks()
    assert calls == ["a"]


def test_raise_if_cancelled() -> None:
    flag = False
    ctx = make_ctx(is_cancelled=lambda: flag)
    ctx.raise_if_cancelled()
    flag = True
    with pytest.raises(JobCancelled) as error:
        ctx.raise_if_cancelled()
    assert error.value.reason == "cancelled"


def test_register_claim_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "CLAIM_GATES", {})

    @register_claim_gate("video")
    async def gate(session: Any) -> bool:
        return True

    assert {"video": gate} == registry.CLAIM_GATES
    with pytest.raises(ValueError, match="already registered"):

        @register_claim_gate("video")
        async def other(session: Any) -> bool:
            return False
