import asyncio
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.services.sandbox import (
    Mount,
    MountNotAllowed,
    SandboxError,
    SandboxLimits,
    SandboxRunner,
    mount_relative,
    prepare_dir,
)


class FakeContainer:
    def __init__(self, *, exit_code: int = 0, hang: bool = False) -> None:
        self.id = "c0ffee" * 4
        self.exit_code = exit_code
        self.done = threading.Event()
        if not hang:
            self.done.set()
        self.killed = False
        self.removed = False

    def wait(self) -> dict[str, int]:
        self.done.wait(10)
        return {"StatusCode": 137 if self.killed else self.exit_code}

    def kill(self) -> None:
        self.killed = True
        self.done.set()

    def logs(self, *, stdout: bool, stderr: bool) -> bytes:
        return (b"out\n" if stdout else b"") + (b"err\n" if stderr else b"")

    def remove(self, force: bool = False) -> None:
        self.removed = True


class FakeContainers:
    def __init__(self, container: FakeContainer | None) -> None:
        self.container = container
        self.calls: list[dict[str, Any]] = []

    def run(self, image: str, command: list[str], **options: Any) -> FakeContainer:
        self.calls.append({"image": image, "command": command, **options})
        if self.container is None:
            raise RuntimeError("No such image: brainbench/nope")
        return self.container


class FakeClient:
    def __init__(self, container: FakeContainer | None) -> None:
        self.containers = FakeContainers(container)


@pytest.fixture
def data(tmp_path: Path) -> Path:
    return tmp_path / "data"


def job_dirs(data: Path) -> tuple[Path, Path]:
    job = data / "jobs" / str(uuid.uuid4())
    return prepare_dir(job / "work", writable=False), prepare_dir(job / "out", writable=True)


def runner(data: Path, container: FakeContainer | None) -> tuple[SandboxRunner, FakeClient]:
    client = FakeClient(container)
    return SandboxRunner(client=client, data_dir=data, host_data_dir=Path("/host/data")), client


def test_only_job_work_out_and_the_skill_may_be_mounted(data: Path) -> None:
    work, _ = job_dirs(data)
    assert mount_relative(work, data).endswith("/work")
    assert mount_relative(data / "skills" / "anatomy", data) == "skills/anatomy"
    for forbidden in (
        data,
        data / "vault.key",
        data / "assets",
        data / "models",
        work.parent,
        work / "sub",
        data / "jobs" / "not-a-uuid" / "work",
        data / "jobs" / str(uuid.uuid4()) / "work" / ".." / ".." / ".." / "assets",
    ):
        with pytest.raises(MountNotAllowed):
            mount_relative(forbidden, data)
    with pytest.raises(MountNotAllowed, match="not under"):
        mount_relative(Path("/etc"), data)


async def test_run_uses_host_paths_and_sandbox_flags(data: Path) -> None:
    work, out = job_dirs(data)
    sandbox, client = runner(data, FakeContainer(exit_code=0))
    result = await sandbox.run(
        "brainbench/manim",
        ["manim", "--version"],
        mounts=[Mount(work, "/work"), Mount(out, "/out", read_only=False)],
        limits=SandboxLimits(timeout_s=5, cpus=2, memory="4g", pids=256),
        environment={"A": "1"},
        label="video",
    )
    assert result.ok and result.exit_code == 0
    assert result.stdout == "out\n" and result.stderr == "err\n" and result.output == "out\nerr\n"
    (call,) = client.containers.calls
    relative = work.relative_to(data).parent
    assert call["volumes"] == {
        f"/host/data/{relative}/work": {"bind": "/work", "mode": "ro"},
        f"/host/data/{relative}/out": {"bind": "/out", "mode": "rw"},
    }
    assert call["network_mode"] == "none"
    assert call["read_only"] is True
    assert call["nano_cpus"] == 2_000_000_000
    assert call["mem_limit"] == "4g" and call["pids_limit"] == 256
    assert call["cap_drop"] == ["ALL"]
    assert "/tmp" in call["tmpfs"]
    assert client.containers.container is not None and client.containers.container.removed
    assert oct(out.stat().st_mode & 0o777) == "0o777"


async def test_timeout_kills_and_removes_the_container(data: Path) -> None:
    work, _ = job_dirs(data)
    container = FakeContainer(hang=True)
    sandbox, _ = runner(data, container)
    result = await sandbox.run(
        "img", ["sleep", "60"], mounts=[Mount(work, "/work")], limits=SandboxLimits(timeout_s=0.2)
    )
    assert result.timed_out and not result.ok and result.exit_code is None
    assert container.killed and container.removed


async def test_kill_hook_kills_the_running_container(data: Path) -> None:
    work, _ = job_dirs(data)
    container = FakeContainer(hang=True)
    sandbox, _ = runner(data, container)
    hooks: list[Any] = []
    registered = asyncio.Event()

    def register(hook: Any) -> Any:
        hooks.append(hook)
        registered.set()
        return lambda: hooks.remove(hook)

    task = asyncio.create_task(
        sandbox.run(
            "img",
            ["sleep", "60"],
            mounts=[Mount(work, "/work")],
            limits=SandboxLimits(timeout_s=30),
            register_kill_hook=register,
        )
    )
    await asyncio.wait_for(registered.wait(), 5)
    await hooks[0]()
    result = await task
    assert result.killed and not result.timed_out and not result.ok
    assert hooks == []  # unregistered once the container exited
    assert container.removed


async def test_forbidden_mount_never_starts_a_container(data: Path) -> None:
    sandbox, client = runner(data, FakeContainer())
    (data / "assets").mkdir(parents=True)
    with pytest.raises(MountNotAllowed):
        await sandbox.run(
            "img", ["true"], mounts=[Mount(data / "assets", "/x")], limits=SandboxLimits(1)
        )
    assert client.containers.calls == []


async def test_start_failure_is_a_sandbox_error(data: Path) -> None:
    work, _ = job_dirs(data)
    sandbox, _ = runner(data, None)
    with pytest.raises(SandboxError, match="No such image"):
        await sandbox.run("img", ["true"], mounts=[Mount(work, "/w")], limits=SandboxLimits(1))
