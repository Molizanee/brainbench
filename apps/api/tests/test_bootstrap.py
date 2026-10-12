from pathlib import Path

import pytest

import app.bootstrap
from app.core.config import settings


@pytest.fixture
def data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(settings, "brainbench_data", tmp_path)
    return tmp_path


def test_data_dirs_created(data_dir: Path) -> None:
    app.bootstrap.ensure_data_dirs()
    assert sorted(p.name for p in data_dir.iterdir()) == ["assets", "jobs", "models", "skills"]


def test_skill_failure_is_logged_not_raised(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    pin_file = data_dir / "anatomy.pin"
    pin_file.write_text("not-a-commit\n")
    monkeypatch.setattr(settings, "anatomy_pin_file", pin_file)
    app.bootstrap.skill()
    assert "anatomy skill not updated" in caplog.text


def test_embedding_model_downloaded_once(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict[str, str]] = []
    monkeypatch.setattr(app.bootstrap, "TextEmbedding", lambda **kwargs: created.append(kwargs))
    settings.models_dir.mkdir()
    app.bootstrap.embedding_model()
    app.bootstrap.embedding_model()
    assert created == [
        {"model_name": settings.embedding_model, "cache_dir": str(settings.models_dir)}
    ]


def test_embedding_model_failure_is_logged(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def offline(**kwargs: str) -> None:
        raise ConnectionError("offline")

    monkeypatch.setattr(app.bootstrap, "TextEmbedding", offline)
    settings.models_dir.mkdir()
    app.bootstrap.embedding_model()
    assert "not downloaded" in caplog.text
    assert not list(settings.models_dir.glob(".ready-*"))
