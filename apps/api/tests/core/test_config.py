from pathlib import Path

import pytest

from app.core.config import Settings


def test_env_names_map_to_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAINBENCH_DATA", "/srv/bb")
    monkeypatch.setenv("BRAINBENCH_PORT", "3100")
    monkeypatch.setenv("BRAINBENCH_OAUTH_CALLBACKS", "1")
    s = Settings(_env_file=None)
    assert s.brainbench_data == Path("/srv/bb")
    assert s.vault_key_path == Path("/srv/bb/vault.key")
    assert s.brainbench_oauth_callbacks is True
    assert s.public_url == "http://localhost:3100"


def test_public_url_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRAINBENCH_PUBLIC_URL", "http://bb.lan:3000")
    assert Settings(_env_file=None).public_url == "http://bb.lan:3000"


def test_empty_env_values_mean_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # compose passes `${BRAINBENCH_DATA_HOST:-}` through as an empty string.
    monkeypatch.setenv("BRAINBENCH_DATA_HOST", "")
    monkeypatch.setenv("BRAINBENCH_PUBLIC_URL", "")
    s = Settings(_env_file=None)
    assert s.brainbench_data_host is None
    assert s.brainbench_public_url is None
