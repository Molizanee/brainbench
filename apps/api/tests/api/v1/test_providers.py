from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import require_session
from app.services.providers import ProviderRuntime, get_provider_runtime, get_registry


@pytest.fixture
def runtime(session_factory: async_sessionmaker[AsyncSession], tmp_path: Path) -> ProviderRuntime:
    return ProviderRuntime(get_registry(), session_factory, tmp_path / "vault.key")


@pytest.fixture
def api(app: FastAPI, runtime: ProviderRuntime) -> Iterator[TestClient]:
    app.dependency_overrides[get_provider_runtime] = lambda: runtime
    app.dependency_overrides[require_session] = lambda: None
    with TestClient(app) as c:
        yield c


def test_provider_routes_require_a_session(client: TestClient) -> None:
    assert client.post("/api/providers/vault/reset", json={"confirm": "reset"}).status_code == 401


def test_vault_reset_needs_explicit_confirmation(api: TestClient, runtime: ProviderRuntime) -> None:
    response = api.post("/api/providers/vault/reset", json={"confirm": "please"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "confirmation-required"
    assert not runtime.key_path.exists()


def test_vault_reset_generates_key(api: TestClient, runtime: ProviderRuntime) -> None:
    response = api.post("/api/providers/vault/reset", json={"confirm": "reset"})
    assert response.status_code == 200
    assert response.json() == {"state": "ok", "detail": "ok", "key_path": str(runtime.key_path)}
