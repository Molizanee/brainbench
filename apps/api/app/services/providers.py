"""Provider connection service (design D2, D3): the app side of ``brainbench_providers``.

The package is pure Python; this module wires it to the database (``SqlProviderStore``), the
vault key file (``vault_key``), and instance settings. One :class:`ProviderRuntime` per process
holds what must outlive a request: the registry, the store, and the cached vault cipher.
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import Conflict, DomainError, NotFound
from app.db.session import SessionFactory as AppSessionFactory
from app.services.instance_settings import load_instance_settings
from app.services.provider_store import SessionFactory, SqlProviderStore
from app.services.vault_key import (
    KEY_BYTES,
    VaultKeyState,
    ensure_vault_key,
    fingerprint,
    key_state,
    write_new_key,
)
from brainbench_providers.errors import ProviderError, VaultLocked
from brainbench_providers.registry import ProviderSpec, Registry, load_registry
from brainbench_providers.vault import VaultCipher

log = logging.getLogger(__name__)

RESET_CONFIRMATION = "reset"


@cache
def get_registry() -> Registry:
    """Shipped providers plus ``$BRAINBENCH_DATA/models.yaml`` when it exists."""
    return load_registry([settings.brainbench_data / "models.yaml"])


async def enabled_provider_ids(session: AsyncSession) -> list[str] | None:
    """``settings.enabled_providers``; None means every registry provider is enabled."""
    row = await load_instance_settings(session)
    return None if row.enabled_providers is None else list(row.enabled_providers)


async def enabled_providers(session: AsyncSession, registry: Registry) -> list[ProviderSpec]:
    return registry.enabled(await enabled_provider_ids(session))


# --- Errors -------------------------------------------------------------------------------


class VaultLockedError(Conflict):
    code = "vault-locked"


class ResetNotConfirmed(DomainError):
    code = "confirmation-required"


def domain_error(exc: ProviderError) -> DomainError:
    """Translate a provider-layer error into the HTTP-mapped domain error with the same code."""
    if exc.code in {"unknown-provider", "login-not-found"}:
        return NotFound(exc.message, code=exc.code)
    if exc.code in {"vault-locked", "provider-disabled", "login-finished"}:
        return Conflict(exc.message, code=exc.code)
    return DomainError(exc.message, code=exc.code)


@contextmanager
def provider_errors() -> Iterator[None]:
    """Routers wrap provider calls in this so package errors reach the client as domain errors."""
    try:
        yield
    except ProviderError as exc:
        raise domain_error(exc) from exc


# --- Vault --------------------------------------------------------------------------------


@dataclass(frozen=True)
class VaultStatus:
    """``state``: ``ok`` (key matches), ``empty`` (no key generated yet), or ``locked``."""

    state: str
    detail: str
    key_path: Path


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class ProviderRuntime:
    """Process-wide provider state. Tests build one over their own session factory."""

    registry: Registry
    session_factory: SessionFactory
    key_path: Path
    clock: Callable[[], datetime] = _now
    store: SqlProviderStore = field(init=False)
    _cipher: tuple[str, VaultCipher] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.store = SqlProviderStore(self.session_factory)

    async def vault_status(self) -> VaultStatus:
        async with self.session_factory() as session:
            recorded = (await load_instance_settings(session)).vault_fingerprint
            await session.commit()
        if recorded is None and not self.key_path.exists():
            return VaultStatus("empty", "created with the first connected account", self.key_path)
        state = key_state(self.key_path, recorded)
        if state.locked:
            return VaultStatus("locked", state.value, self.key_path)
        return VaultStatus("ok", state.value, self.key_path)

    async def cipher(self, *, create: bool = False) -> VaultCipher:
        """The vault cipher. ``create`` generates the key on a fresh vault (first credential).

        Raises :class:`VaultLocked` when the key is missing or does not match the fingerprint;
        a key is never regenerated once a fingerprint is recorded.
        """
        async with self.session_factory() as session:
            row = await load_instance_settings(session)
            if row.vault_fingerprint is None and create:
                state = await ensure_vault_key(session, self.key_path)
                if state.locked:
                    raise VaultLocked(str(self.key_path), state.value)
                log.info("vault key %s for the first credential", state.value)
            recorded = row.vault_fingerprint
            await session.commit()
        if recorded is None:
            raise VaultLocked(str(self.key_path), VaultKeyState.LOCKED_MISSING.value)
        state = key_state(self.key_path, recorded)
        if state.locked:
            raise VaultLocked(str(self.key_path), state.value)
        if self._cipher is not None and self._cipher[0] == recorded:
            return self._cipher[1]
        cipher = VaultCipher(self.key_path.read_bytes())
        self._cipher = (recorded, cipher)
        return cipher

    async def reset_vault(self, confirm: str) -> VaultStatus:
        """Delete every credential, then replace the key file and its recorded fingerprint."""
        if confirm != RESET_CONFIRMATION:
            raise ResetNotConfirmed(
                f'Type "{RESET_CONFIRMATION}" to confirm: every stored account is deleted.'
            )
        deleted = await self.store.delete_all_credentials()
        self.key_path.unlink(missing_ok=True)
        key = write_new_key(self.key_path)
        assert len(key) == KEY_BYTES
        async with self.session_factory() as session:
            row = await load_instance_settings(session)
            row.vault_fingerprint = fingerprint(key)
            await session.commit()
        self._cipher = None
        log.warning("vault reset: %d credentials deleted, new key generated", deleted)
        return await self.vault_status()


_runtime: ProviderRuntime | None = None


def get_provider_runtime() -> ProviderRuntime:
    """The process runtime (FastAPI dependency; tests override it)."""
    global _runtime
    if _runtime is None:
        _runtime = ProviderRuntime(
            registry=get_registry(),
            session_factory=AppSessionFactory,
            key_path=settings.vault_key_path,
        )
    return _runtime
