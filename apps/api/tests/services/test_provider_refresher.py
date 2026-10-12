import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.providers import ProviderRuntime, get_registry, start_refresher
from brainbench_providers.testing import FakeProviders
from brainbench_providers.vault import SealedTokens


@pytest.fixture
def fake() -> FakeProviders:
    return FakeProviders(expires_in=120)  # inside the 5-minute refresh skew


@pytest.fixture
def runtime(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path, fake: FakeProviders
) -> ProviderRuntime:
    return ProviderRuntime(
        get_registry(),
        session_factory,
        tmp_path / "vault.key",
        http=fake.http_factory(),
        refresh_interval_s=0.01,
    )


async def connect(runtime: ProviderRuntime, fake: FakeProviders) -> None:
    view = await runtime.start_login("anthropic", None)
    await runtime.complete_login(view.login_id, fake.authorize(view.authorize_url or ""))


async def test_refresher_runs_in_the_app_lifespan(
    runtime: ProviderRuntime, fake: FakeProviders
) -> None:
    await connect(runtime, fake)
    (before,) = await runtime.store.list_credentials()
    async with start_refresher(FastAPI(), runtime):
        for _ in range(200):
            (after,) = await runtime.store.list_credentials()
            if after.version > before.version:
                break
            await asyncio.sleep(0.01)
    assert after.version > before.version
    assert after.access_enc != before.access_enc
    assert after.expires_at is not None and before.expires_at is not None
    assert after.expires_at >= before.expires_at


async def test_sql_store_compare_and_set(runtime: ProviderRuntime, fake: FakeProviders) -> None:
    await connect(runtime, fake)
    (row,) = await runtime.store.list_credentials()
    assert row.access_enc is not None
    tokens = SealedTokens(row.access_enc, row.refresh_enc)
    first = await runtime.store.update_tokens(
        row.id, expected_version=row.version, tokens=tokens, expires_at=row.expires_at
    )
    stale = await runtime.store.update_tokens(
        row.id, expected_version=row.version, tokens=tokens, expires_at=row.expires_at
    )
    assert first is not None and first.version == row.version + 1 and stale is None
    assert not await runtime.store.disable_credential(
        row.id, expected_version=row.version, cause="needs-reauthentication"
    )
    assert await runtime.store.disable_credential(
        row.id, expected_version=first.version, cause="needs-reauthentication"
    )


async def test_revoked_grant_disables_account_in_db(
    runtime: ProviderRuntime, fake: FakeProviders
) -> None:
    await connect(runtime, fake)
    fake.revoked.update(fake.refresh_tokens)
    result = await runtime.refresher.sweep()
    (row,) = await runtime.store.list_credentials()
    assert result.disabled == [row.id]
    assert row.disabled_cause == "needs-reauthentication" and row.disabled_at is not None
