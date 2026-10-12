"""No plaintext token in Postgres rows, API responses, or logs after connected accounts.

Runs against a migrated Postgres (``DATABASE_URL``; ``just providers-integration``). Every token
the fake provider issues contains ``PLAINTEXT``, so a single grep covers access tokens, refresh
tokens, rotated tokens, and JWT-shaped ChatGPT tokens.
"""

import logging
import os
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.api.deps import require_session
from app.db.session import get_session
from app.main import create_app
from app.services.providers import ProviderRuntime, get_provider_runtime, get_registry, model_for
from brainbench_providers.testing import FakeProviders, Replay

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL (Postgres)"),
]

MARKER = "PLAINTEXT"
FIXTURES = Path(__file__).parents[4] / "packages/providers/tests/fixtures"
PROVIDER_TABLES = (
    "provider_credentials",
    "provider_blocks",
    "provider_usage_windows",
    "provider_usage",
)


@pytest.fixture
async def pg() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
    async with engine.begin() as conn:
        for table in PROVIDER_TABLES:
            await conn.execute(text(f"DELETE FROM {table}"))
        await conn.execute(
            text(
                "UPDATE settings SET vault_fingerprint = NULL, enabled_providers = NULL, "
                "provider_routing = '{}'"
            )
        )
    yield engine
    async with engine.begin() as conn:
        for table in PROVIDER_TABLES:
            await conn.execute(text(f"DELETE FROM {table}"))
        await conn.execute(text("UPDATE settings SET vault_fingerprint = NULL"))
    await engine.dispose()


@pytest.fixture
def fake() -> FakeProviders:
    return FakeProviders(
        expires_in=120,  # inside the refresh skew, so the sweep rotates every token
        usage={
            "anthropic": {"five_hour": {"utilization": 20, "resets_at": "2099-01-01T00:00:00Z"}},
            "openai-codex": {"rate_limit": {"primary_window": {"used_percent": 5}}},
        },
    )


@pytest.fixture
def runtime(pg: AsyncEngine, tmp_path: Path, fake: FakeProviders) -> ProviderRuntime:
    sessions = async_sessionmaker(pg, expire_on_commit=False)
    replay = Replay.load(FIXTURES / "anthropic/unauthorized_then_ok.json", fallback=fake.handle)
    return ProviderRuntime(
        get_registry(), sessions, tmp_path / "vault.key", http=replay.http_factory()
    )


@pytest.fixture
def api(pg: AsyncEngine, runtime: ProviderRuntime) -> Iterator[TestClient]:
    app: FastAPI = create_app(schema_guard=False, background=False)
    sessions = async_sessionmaker(pg, expire_on_commit=False)

    async def session() -> AsyncIterator[AsyncSession]:
        async with sessions() as s:
            yield s

    app.dependency_overrides[get_session] = session
    app.dependency_overrides[get_provider_runtime] = lambda: runtime
    app.dependency_overrides[require_session] = lambda: None
    with TestClient(app) as client:
        yield client


async def test_no_plaintext_token_in_rows_responses_or_logs(
    api: TestClient,
    runtime: ProviderRuntime,
    fake: FakeProviders,
    pg: AsyncEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    responses: list[str] = []

    def call(method: str, url: str, **kw: Any) -> Any:
        response = api.request(method, url, **kw)
        assert response.status_code < 400, response.text
        responses.append(response.text)
        return response.json() if response.content else {}

    for provider in ("anthropic", "openai-codex"):
        login = call("POST", f"/api/providers/{provider}/logins", json={"kind": "oauth-code"})
        redirect = fake.authorize(str(login["authorize_url"]))
        call(
            "POST", f"/api/providers/logins/{login['login_id']}/complete", json={"input": redirect}
        )
        call("GET", f"/api/providers/logins/{login['login_id']}")

    # Rotate every token, fetch usage windows, and make a model call that refreshes after a 401.
    assert len((await runtime.refresher.sweep()).refreshed) == 2
    await runtime.usage.refresh_stale()
    async with async_sessionmaker(pg)() as session:
        model = await model_for(session, "tutoring", runtime)
    events = [e async for e in model.stream([{"role": "user", "content": [{"text": "hi"}]}])]
    assert events[-1]["metadata"]["usage"]["outputTokens"] == 9
    handle = await runtime.pool.lease("openai-codex")
    await handle.rate_limited("5h", runtime.clock() + timedelta(hours=1), "429 from provider")

    overview = call("GET", "/api/providers")
    call("GET", "/api/settings/providers")
    accounts = [a for p in overview["providers"] for a in p["accounts"]]
    assert len(accounts) == 2 and accounts[0]["usage_today"]["calls"] == 1

    issued = fake.issued()
    assert (
        len(issued) >= 8
    )  # 2 logins + 2 sweep refreshes + 1 refresh after 401, access and refresh
    rows: list[str] = []
    raw = b""
    async with pg.connect() as conn:
        for table in (*PROVIDER_TABLES, "settings"):
            result = await conn.execute(text(f"SELECT row_to_json(t)::text FROM {table} t"))
            rows += [str(row[0]) for row in result.all()]
        result = await conn.execute(
            text("SELECT access_enc, refresh_enc FROM provider_credentials")
        )
        for row in result.all():
            raw += b"".join(bytes(value) for value in row if value is not None)
    dump = "\n".join(rows)
    assert "dev@example.com" in dump  # the dump really holds the accounts
    assert MARKER not in dump and MARKER.encode() not in raw
    for token in issued:
        assert token not in dump
        assert token.encode() not in raw
    body = "\n".join(responses)
    assert MARKER not in body
    assert "PLAINTEXT" not in caplog.text
    assert "credential" in caplog.text  # the logs really cover the flows
