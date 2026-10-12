"""Docker fixtures for the visual-generation sandbox tests.

They need a reachable Docker daemon and the sandbox images (``docker compose build
sandbox-manim sandbox-anatomy``); without either the tests skip. The data directory is a
``tmp_path`` that the worker-side code and the daemon see at the same path, so
``host_data_dir`` equals ``data_dir`` (as with ``BRAINBENCH_DATA_HOST`` outside Docker).
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.core.config import settings


@pytest.fixture(scope="session")
def docker_client() -> Any:
    try:
        import docker

        client = docker.from_env()
        client.ping()
    except Exception as exc:
        pytest.skip(f"needs a Docker daemon: {exc}")
    return client


@pytest.fixture
def require_image(docker_client: Any) -> Callable[[str], str]:
    def require(name: str) -> str:
        try:
            docker_client.images.get(name)
        except Exception:
            pytest.skip(f"needs {name}: docker compose build sandbox-manim sandbox-anatomy")
        return name

    return require


@pytest.fixture
def manim_image(require_image: Callable[[str], str]) -> str:
    return require_image(settings.manim_image)


@pytest.fixture
def anatomy_image(require_image: Callable[[str], str]) -> str:
    return require_image(settings.anatomy_image)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    # Resolve symlinks (/var -> /private/var on macOS) so host paths match what the daemon sees.
    data = (tmp_path / "data").resolve()
    data.mkdir()
    return data
