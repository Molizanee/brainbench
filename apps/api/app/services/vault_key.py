"""Vault key file lifecycle (design D11 step 3). The key is 32 random bytes in
``$BRAINBENCH_DATA/vault.key`` (mode 0600); ``settings.vault_fingerprint`` is its SHA-256.

A key is generated only while no fingerprint is recorded. Once a fingerprint exists a missing or
different key is never replaced: the vault is reported locked until the key is restored or the
vault is explicitly reset.
"""

import hashlib
import logging
import os
import secrets
from enum import StrEnum
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.instance_settings import load_instance_settings

log = logging.getLogger(__name__)

KEY_BYTES = 32


class VaultKeyState(StrEnum):
    OK = "ok"
    GENERATED = "generated"
    ADOPTED = "adopted"
    LOCKED_MISSING = "locked-missing"
    LOCKED_MISMATCH = "locked-mismatch"
    LOCKED_INVALID = "locked-invalid"

    @property
    def locked(self) -> bool:
        return self.value.startswith("locked")


def fingerprint(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()


def write_new_key(path: Path) -> bytes:
    """Create ``path`` with a fresh key, owner read/write only. Never overwrites."""
    key = secrets.token_bytes(KEY_BYTES)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, key)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.chmod(path, 0o600)
    return key


def key_state(path: Path, recorded: str | None) -> VaultKeyState:
    """State of the key file against the recorded fingerprint, without changing anything."""
    if not path.is_file():
        return VaultKeyState.LOCKED_MISSING
    key = path.read_bytes()
    if len(key) != KEY_BYTES:
        return VaultKeyState.LOCKED_INVALID
    if recorded is not None and fingerprint(key) != recorded:
        return VaultKeyState.LOCKED_MISMATCH
    return VaultKeyState.OK


def _key_for_fresh_vault(path: Path) -> tuple[VaultKeyState, bytes | None]:
    """No fingerprint recorded yet: adopt a valid existing key file or create one."""
    if not path.exists():
        return VaultKeyState.GENERATED, write_new_key(path)
    state = key_state(path, None)
    if state.locked:
        return state, None
    return VaultKeyState.ADOPTED, path.read_bytes()


async def ensure_vault_key(session: AsyncSession, path: Path) -> VaultKeyState:
    """Bootstrap step: generate (or adopt) the key on a fresh vault, otherwise only verify it."""
    row = await load_instance_settings(session)
    if row.vault_fingerprint is not None:
        return key_state(path, row.vault_fingerprint)
    state, key = _key_for_fresh_vault(path)
    if key is not None:
        row.vault_fingerprint = fingerprint(key)
        await session.flush()
    return state
