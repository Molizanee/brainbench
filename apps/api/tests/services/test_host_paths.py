import socket
from pathlib import Path
from typing import Any

import pytest

from app.services.host_paths import (
    HostPathError,
    SandboxProbeError,
    resolve_host_data_dir,
    run_sandbox_probe,
    to_host,
)


class FakeContainers:
    def __init__(self, mounts: list[dict[str, str]] | None, data_dir: Path | None = None) -> None:
        self.mounts = mounts
        self.data_dir = data_dir
        self.run_calls: list[dict[str, Any]] = []

    def get(self, container_id: str) -> Any:
        if self.mounts is None:
            raise RuntimeError("socket unavailable")
        assert container_id == socket.gethostname()
        return type("C", (), {"attrs": {"Mounts": self.mounts}})()

    def run(self, image: str, command: list[str], **kwargs: Any) -> bytes:
        self.run_calls.append({"image": image, "command": command, **kwargs})
        # Simulate the daemon: read the marker through the host path the worker asked for.
        (host_dir,) = kwargs["volumes"]
        return (Path(host_dir) / "marker").read_bytes()


class FakeClient:
    def __init__(self, containers: FakeContainers) -> None:
        self.containers = containers


def test_self_inspection_reads_data_mount_source() -> None:
    client = FakeClient(FakeContainers([{"Destination": "/data", "Source": "/home/me/bb/data"}]))
    assert resolve_host_data_dir(client, Path("/data"), None) == Path("/home/me/bb/data")


def test_override_wins() -> None:
    client = FakeClient(FakeContainers(None))
    assert resolve_host_data_dir(client, Path("/data"), Path("/srv/bb")) == Path("/srv/bb")


def test_inspection_failure_names_override() -> None:
    with pytest.raises(HostPathError, match="BRAINBENCH_DATA_HOST"):
        resolve_host_data_dir(FakeClient(FakeContainers(None)), Path("/data"), None)
    with pytest.raises(HostPathError, match="BRAINBENCH_DATA_HOST"):
        resolve_host_data_dir(FakeClient(FakeContainers([])), Path("/data"), None)


def test_to_host_translates_and_confines(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "jobs" / "j1").mkdir(parents=True)
    assert to_host(data / "jobs" / "j1", data, Path("/host/data")) == Path("/host/data/jobs/j1")
    with pytest.raises(HostPathError):
        to_host(tmp_path / "elsewhere", data, Path("/host/data"))


def test_probe_reads_marker_through_host_path(tmp_path: Path) -> None:
    containers = FakeContainers([])
    # Host path == data dir here, so the fake daemon reads the real marker.
    run_sandbox_probe(FakeClient(containers), "brainbench/manim", tmp_path, tmp_path)
    (call,) = containers.run_calls
    assert call["network_mode"] == "none"
    assert call["volumes"] == {str(tmp_path / "jobs" / ".probe"): {"bind": "/probe", "mode": "ro"}}
    assert not (tmp_path / "jobs" / ".probe" / "marker").exists()


def test_probe_fails_on_wrong_host_path(tmp_path: Path) -> None:
    wrong = tmp_path / "wrong"
    (wrong / "jobs" / ".probe").mkdir(parents=True)
    (wrong / "jobs" / ".probe" / "marker").write_text("stale")
    data = tmp_path / "data"
    with pytest.raises(SandboxProbeError, match="BRAINBENCH_DATA_HOST"):
        run_sandbox_probe(FakeClient(FakeContainers([])), "brainbench/manim", data, wrong)
