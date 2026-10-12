from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import require_session
from app.services.instance_settings import load_instance_settings
from app.services.providers import ProviderRuntime, get_provider_runtime, get_registry
from brainbench_providers.testing import FakeProviders


@pytest.fixture
def fake() -> FakeProviders:
    return FakeProviders()


@pytest.fixture
def runtime(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path, fake: FakeProviders
) -> ProviderRuntime:
    return ProviderRuntime(
        get_registry(), session_factory, tmp_path / "vault.key", http=fake.http_factory()
    )


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


def test_connect_by_pasting_the_redirect(api: TestClient, fake: FakeProviders) -> None:
    started = api.post("/api/providers/anthropic/logins", json={})
    assert started.status_code == 201
    login = started.json()
    assert login["status"] == "pending" and login["paste_code"] is True
    assert login["callback_listening"] is False and login["kind"] == "oauth-code"
    assert login["authorize_url"].startswith("https://claude.ai/oauth/authorize?")
    redirect = fake.authorize(login["authorize_url"])
    done = api.post(f"/api/providers/logins/{login['login_id']}/complete", json={"input": redirect})
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "connected" and done.json()["credential_id"]
    polled = api.get(f"/api/providers/logins/{login['login_id']}").json()
    assert polled["status"] == "connected"
    for token in fake.issued():
        assert token not in started.text + done.text + str(polled)


def test_state_mismatch_rejects_login(api: TestClient, fake: FakeProviders) -> None:
    login = api.post("/api/providers/anthropic/logins").json()
    redirect = fake.authorize(login["authorize_url"]).replace("state=", "state=x")
    response = api.post(
        f"/api/providers/logins/{login['login_id']}/complete", json={"input": redirect}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "state-mismatch"
    assert api.get(f"/api/providers/logins/{login['login_id']}").json()["status"] == "failed"


def test_device_login_shows_code(api: TestClient) -> None:
    response = api.post("/api/providers/openai-codex/logins", json={"kind": "device-code"})
    assert response.status_code == 201
    body = response.json()
    assert body["user_code"] == "ABCD-1234" and body["authorize_url"] is None
    assert body["verification_url"] == "https://auth.openai.com/codex/device"


def test_login_errors(api: TestClient) -> None:
    assert api.post("/api/providers/gemini/logins").status_code == 404
    assert api.get("/api/providers/logins/nope").status_code == 404
    response = api.post("/api/providers/anthropic/logins", json={"kind": "device-code"})
    assert response.status_code == 404


async def test_disabled_provider_cannot_start_login(api: TestClient, session: AsyncSession) -> None:
    row = await load_instance_settings(session)
    row.enabled_providers = ["openai-codex"]
    await session.commit()
    response = api.post("/api/providers/anthropic/logins")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "provider-disabled"


async def test_locked_vault_blocks_new_logins(
    api: TestClient, runtime: ProviderRuntime, session: AsyncSession
) -> None:
    row = await load_instance_settings(session)
    row.vault_fingerprint = "0" * 64
    await session.commit()
    response = api.post("/api/providers/anthropic/logins")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "vault-locked"
    assert str(runtime.key_path) in response.json()["detail"]["message"]
