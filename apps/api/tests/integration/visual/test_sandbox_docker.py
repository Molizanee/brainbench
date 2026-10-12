import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.services.sandbox import Mount, SandboxLimits, SandboxRunner, prepare_dir

pytestmark = pytest.mark.integration


def dirs(data_dir: Path) -> tuple[Path, Path]:
    job = data_dir / "jobs" / str(uuid.uuid4())
    return prepare_dir(job / "work", writable=False), prepare_dir(job / "out", writable=True)


def leftover(docker_client: Any, label: str) -> list[Any]:
    containers: list[Any] = docker_client.containers.list(
        all=True, filters={"label": f"brainbench.sandbox={label}"}
    )
    return containers


async def test_isolation_read_only_work_writable_out_no_network(
    docker_client: Any, manim_image: str, data_dir: Path
) -> None:
    work, out = dirs(data_dir)
    (work / "scene.py").write_text("print('hi')\n")
    label = f"it-{uuid.uuid4().hex[:8]}"
    script = (
        "cat /work/scene.py; "
        "touch /work/x 2>/dev/null && echo WORK-WRITABLE; "
        "touch /x 2>/dev/null && echo ROOT-WRITABLE; "
        "echo written > /out/marker; "
        "echo routes=$(tail -n +2 /proc/net/route | wc -l)"
    )
    result = await SandboxRunner(data_dir=data_dir, host_data_dir=data_dir).run(
        manim_image,
        ["sh", "-c", script],
        mounts=[Mount(work, "/work"), Mount(out, "/out", read_only=False)],
        limits=SandboxLimits(timeout_s=60, cpus=1, memory="512m", pids=64),
        label=label,
    )
    assert result.ok, result.output
    assert "print('hi')" in result.stdout
    assert "WORK-WRITABLE" not in result.stdout and "ROOT-WRITABLE" not in result.stdout
    assert result.stdout.split()[-1] == "routes=0"  # --network none: no route anywhere
    assert (out / "marker").read_text() == "written\n"
    assert leftover(docker_client, label) == []


async def test_wall_clock_timeout_kills_the_sandbox(
    docker_client: Any, manim_image: str, data_dir: Path
) -> None:
    work, _ = dirs(data_dir)
    label = f"it-{uuid.uuid4().hex[:8]}"
    result = await SandboxRunner(data_dir=data_dir, host_data_dir=data_dir).run(
        manim_image,
        ["sleep", "120"],
        mounts=[Mount(work, "/work")],
        limits=SandboxLimits(timeout_s=2),
        label=label,
    )
    assert result.timed_out and result.exit_code is None
    assert result.duration_s < 30
    assert leftover(docker_client, label) == []


async def test_kill_hook_stops_a_running_sandbox(
    docker_client: Any, manim_image: str, data_dir: Path
) -> None:
    work, _ = dirs(data_dir)
    hooks: list[Any] = []
    registered = asyncio.Event()

    def register(hook: Any) -> Any:
        hooks.append(hook)
        registered.set()
        return lambda: hooks.remove(hook)

    task = asyncio.create_task(
        SandboxRunner(data_dir=data_dir, host_data_dir=data_dir).run(
            manim_image,
            ["sleep", "120"],
            mounts=[Mount(work, "/work")],
            limits=SandboxLimits(timeout_s=60),
            register_kill_hook=register,
        )
    )
    await asyncio.wait_for(registered.wait(), 30)
    await asyncio.sleep(1)
    await hooks[0]()
    result = await asyncio.wait_for(task, 30)
    assert result.killed and not result.timed_out and hooks == []
