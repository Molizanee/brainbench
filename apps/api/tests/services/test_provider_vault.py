import os
import stat
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.schema import ProviderCredential
from app.services.instance_settings import load_instance_settings
from app.services.providers import ProviderRuntime, ResetNotConfirmed, get_registry
from app.services.vault_key import fingerprint
from brainbench_providers.errors import VaultLocked
from brainbench_providers.vault import Identity, identity_key, seal_tokens

ACCESS = "access-PLAINTEXT-token"
REFRESH = "refresh-PLAINTEXT-token"


@pytest.fixture
def runtime(session_factory: async_sessionmaker[AsyncSession], tmp_path: Path) -> ProviderRuntime:
    return ProviderRuntime(get_registry(), session_factory, tmp_path / "vault.key")


async def store_one(runtime: ProviderRuntime) -> None:
    cipher = await runtime.cipher(create=True)
    key = identity_key(Identity(email="dev@example.com", org_id="org"), org_scoped=True)
    await runtime.store.upsert_credential(
        provider="anthropic",
        identity_key=key,
        identity={"email": "dev@example.com", "org_id": "org"},
        tokens=seal_tokens(
            cipher,
            provider="anthropic",
            identity_key=key,
            access_token=ACCESS,
            refresh_token=REFRESH,
        ),
        expires_at=None,
    )


async def test_fresh_vault_is_empty_until_the_first_credential(runtime: ProviderRuntime) -> None:
    assert (await runtime.vault_status()).state == "empty"
    with pytest.raises(VaultLocked):
        await runtime.cipher()
    await store_one(runtime)
    assert stat.S_IMODE(runtime.key_path.stat().st_mode) == 0o600
    status = await runtime.vault_status()
    assert status.state == "ok"


async def test_tokens_are_encrypted_at_rest(
    runtime: ProviderRuntime, session: AsyncSession
) -> None:
    await store_one(runtime)
    row = (await session.scalars(select(ProviderCredential))).one()
    assert row.access_enc is not None and ACCESS.encode() not in row.access_enc
    assert row.refresh_enc is not None and REFRESH.encode() not in row.refresh_enc
    assert ACCESS not in repr(row) and REFRESH not in repr(row)
    assert row.identity_key == "email:dev@example.com|org:org"


async def test_missing_key_locks_vault_without_regenerating(runtime: ProviderRuntime) -> None:
    await store_one(runtime)
    runtime.key_path.unlink()
    runtime._cipher = None
    status = await runtime.vault_status()
    assert (status.state, status.detail) == ("locked", "locked-missing")
    with pytest.raises(VaultLocked, match=str(runtime.key_path)):
        await runtime.cipher(create=True)
    assert not runtime.key_path.exists()


async def test_mismatched_key_locks_vault(runtime: ProviderRuntime) -> None:
    await store_one(runtime)
    runtime.key_path.write_bytes(os.urandom(32))
    assert (await runtime.vault_status()).detail == "locked-mismatch"
    with pytest.raises(VaultLocked):
        await runtime.cipher()


async def test_reset_requires_confirmation(runtime: ProviderRuntime) -> None:
    await store_one(runtime)
    with pytest.raises(ResetNotConfirmed):
        await runtime.reset_vault("yes")
    assert len(await runtime.store.list_credentials()) == 1


async def test_reset_deletes_credentials_and_regenerates_key(
    runtime: ProviderRuntime, session: AsyncSession
) -> None:
    await store_one(runtime)
    runtime.key_path.unlink()
    status = await runtime.reset_vault("reset")
    assert status.state == "ok"
    assert await runtime.store.list_credentials() == []
    row = await load_instance_settings(session)
    await session.refresh(row)
    assert row.vault_fingerprint == fingerprint(runtime.key_path.read_bytes())
    await runtime.cipher()
