from pathlib import Path
from typing import Any

import pytest

import app.worker
from app.core.config import settings
from app.jobs.registry import HANDLERS
from app.services.host_paths import HostPathError


def test_worker_import_registers_handlers() -> None:
    assert callable(app.worker.main)
    assert "noop" in HANDLERS


def test_prepare_sandboxes_resolves_and_probes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, Any]] = []

    def resolve(client: Any, data_dir: Path, override: Path | None) -> Path:
        calls.append(("resolve", data_dir))
        return Path("/host")

    def probe(client: Any, image: str, data_dir: Path, host: Path) -> None:
        calls.append(("probe", host))

    monkeypatch.setattr(settings, "brainbench_data", tmp_path)
    monkeypatch.setattr(settings, "brainbench_data_host", None)
    monkeypatch.setattr("app.worker.docker.from_env", lambda: "client")
    monkeypatch.setattr(app.worker, "resolve_host_data_dir", resolve)
    monkeypatch.setattr(app.worker, "run_sandbox_probe", probe)

    assert app.worker.prepare_sandboxes() == Path("/host")
    assert calls == [("resolve", tmp_path), ("probe", Path("/host"))]


def test_main_exits_with_message_when_host_path_unknown(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def fail() -> Path:
        raise HostPathError("set BRAINBENCH_DATA_HOST to the host path mounted at /data")

    monkeypatch.setattr(app.worker, "prepare_sandboxes", fail)
    monkeypatch.setattr(app.worker, "setup_logging", lambda: None)
    with pytest.raises(SystemExit) as exit_info:
        app.worker.main()
    assert exit_info.value.code == 1
    assert "BRAINBENCH_DATA_HOST" in caplog.text
