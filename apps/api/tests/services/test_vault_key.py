import stat
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.instance_settings import load_instance_settings
from app.services.vault_key import VaultKeyState, ensure_vault_key, fingerprint, write_new_key


async def test_fresh_vault_generates_owner_only_key(session: AsyncSession, tmp_path: Path) -> None:
    key_path = tmp_path / "vault.key"
    assert await ensure_vault_key(session, key_path) is VaultKeyState.GENERATED
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert len(key_path.read_bytes()) == 32
    row = await load_instance_settings(session)
    assert row.vault_fingerprint == fingerprint(key_path.read_bytes())


async def test_second_run_leaves_key_unchanged(session: AsyncSession, tmp_path: Path) -> None:
    key_path = tmp_path / "vault.key"
    await ensure_vault_key(session, key_path)
    before = key_path.read_bytes()
    assert await ensure_vault_key(session, key_path) is VaultKeyState.OK
    assert key_path.read_bytes() == before


async def test_missing_key_with_fingerprint_is_locked_not_regenerated(
    session: AsyncSession, tmp_path: Path
) -> None:
    key_path = tmp_path / "vault.key"
    await ensure_vault_key(session, key_path)
    recorded = (await load_instance_settings(session)).vault_fingerprint
    key_path.unlink()

    assert await ensure_vault_key(session, key_path) is VaultKeyState.LOCKED_MISSING
    assert not key_path.exists()
    assert (await load_instance_settings(session)).vault_fingerprint == recorded


async def test_different_key_is_locked_and_untouched(session: AsyncSession, tmp_path: Path) -> None:
    key_path = tmp_path / "vault.key"
    await ensure_vault_key(session, key_path)
    key_path.unlink()
    other = write_new_key(key_path)

    assert await ensure_vault_key(session, key_path) is VaultKeyState.LOCKED_MISMATCH
    assert key_path.read_bytes() == other


async def test_existing_key_adopted_on_fresh_vault(session: AsyncSession, tmp_path: Path) -> None:
    key_path = tmp_path / "vault.key"
    key = write_new_key(key_path)
    assert await ensure_vault_key(session, key_path) is VaultKeyState.ADOPTED
    assert (await load_instance_settings(session)).vault_fingerprint == fingerprint(key)


async def test_malformed_key_never_overwritten(session: AsyncSession, tmp_path: Path) -> None:
    key_path = tmp_path / "vault.key"
    key_path.write_bytes(b"short")
    assert await ensure_vault_key(session, key_path) is VaultKeyState.LOCKED_INVALID
    assert key_path.read_bytes() == b"short"
    assert (await load_instance_settings(session)).vault_fingerprint is None
