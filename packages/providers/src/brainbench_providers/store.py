"""Persistence protocol for the provider layer.

The package never touches a database. ``apps/api/app/services/providers.py`` implements
:class:`ProviderStore` over SQLAlchemy; tests use :class:`MemoryStore`. Every method is its own
transaction, so background tasks (refresher, loopback listener, device polling) can call them
without a request scope.
"""

import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from brainbench_providers.vault import SealedTokens, StoredCredential


@dataclass(frozen=True)
class Block:
    credential_id: UUID
    scope: str
    until: datetime
    reason: str | None = None


@dataclass(frozen=True)
class UsageWindow:
    """Share of one provider usage window consumed, in percent (0-100)."""

    window: str
    used_pct: float | None
    resets_at: datetime | None


@dataclass(frozen=True)
class StoredWindow:
    credential_id: UUID
    window: str
    used_pct: float | None
    resets_at: datetime | None
    fetched_at: datetime


@dataclass(frozen=True)
class UsageRecord:
    credential_id: UUID | None
    provider: str
    model: str
    task_class: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class UsageSummary:
    credential_id: UUID | None
    provider: str
    calls: int
    input_tokens: int
    output_tokens: int


class ProviderStore(Protocol):
    async def list_credentials(self, provider: str | None = None) -> list[StoredCredential]: ...

    async def get_credential(self, credential_id: UUID) -> StoredCredential | None: ...

    async def upsert_credential(
        self,
        *,
        provider: str,
        identity_key: str,
        identity: dict[str, Any],
        tokens: SealedTokens,
        expires_at: datetime | None,
    ) -> StoredCredential:
        """Insert, or replace the tokens of the row with the same ``(provider, identity_key)``.

        Re-connecting an account clears its disabled state and bumps its version.
        """
        ...

    async def update_tokens(
        self,
        credential_id: UUID,
        *,
        expected_version: int,
        tokens: SealedTokens,
        expires_at: datetime | None,
    ) -> StoredCredential | None:
        """Compare-and-set on ``version``: None when the row changed or is gone."""
        ...

    async def disable_credential(
        self, credential_id: UUID, *, expected_version: int, cause: str
    ) -> bool:
        """Disable only if the row is still at ``expected_version``."""
        ...

    async def delete_credential(self, credential_id: UUID) -> bool: ...

    async def delete_all_credentials(self) -> int: ...

    async def set_priority(self, credential_id: UUID, priority: int) -> bool: ...

    async def add_block(self, block: Block) -> None: ...

    async def active_blocks(self, now: datetime) -> list[Block]: ...

    async def save_usage_windows(
        self, credential_id: UUID, windows: list[UsageWindow], fetched_at: datetime
    ) -> None: ...

    async def usage_windows(self) -> list[StoredWindow]: ...

    async def record_usage(self, record: UsageRecord, at: datetime) -> None: ...

    async def usage_since(self, since: datetime) -> list[UsageSummary]: ...


class MemoryStore:
    """In-memory :class:`ProviderStore` for package tests and tools."""

    def __init__(self) -> None:
        self.credentials: dict[UUID, StoredCredential] = {}
        self.blocks: list[Block] = []
        self.windows: dict[tuple[UUID, str], StoredWindow] = {}
        self.usage: list[tuple[UsageRecord, datetime]] = []

    async def list_credentials(self, provider: str | None = None) -> list[StoredCredential]:
        rows = [c for c in self.credentials.values() if provider is None or c.provider == provider]
        return sorted(rows, key=lambda c: (c.priority, c.created_at or datetime.min))

    async def get_credential(self, credential_id: UUID) -> StoredCredential | None:
        return self.credentials.get(credential_id)

    async def upsert_credential(
        self,
        *,
        provider: str,
        identity_key: str,
        identity: dict[str, Any],
        tokens: SealedTokens,
        expires_at: datetime | None,
    ) -> StoredCredential:
        existing = next(
            (
                c
                for c in self.credentials.values()
                if c.provider == provider and c.identity_key == identity_key
            ),
            None,
        )
        if existing is None:
            row = StoredCredential(
                id=uuid.uuid4(),
                provider=provider,
                identity_key=identity_key,
                identity=identity,
                access_enc=tokens.access_enc,
                refresh_enc=tokens.refresh_enc,
                expires_at=expires_at,
                version=1,
                created_at=datetime.now().astimezone(),
            )
        else:
            row = replace(
                existing,
                identity=identity,
                access_enc=tokens.access_enc,
                refresh_enc=tokens.refresh_enc,
                expires_at=expires_at,
                version=existing.version + 1,
                disabled_at=None,
                disabled_cause=None,
            )
        self.credentials[row.id] = row
        return row

    async def update_tokens(
        self,
        credential_id: UUID,
        *,
        expected_version: int,
        tokens: SealedTokens,
        expires_at: datetime | None,
    ) -> StoredCredential | None:
        row = self.credentials.get(credential_id)
        if row is None or row.version != expected_version:
            return None
        row = replace(
            row,
            access_enc=tokens.access_enc,
            refresh_enc=tokens.refresh_enc,
            expires_at=expires_at,
            version=row.version + 1,
        )
        self.credentials[credential_id] = row
        return row

    async def disable_credential(
        self, credential_id: UUID, *, expected_version: int, cause: str
    ) -> bool:
        row = self.credentials.get(credential_id)
        if row is None or row.version != expected_version:
            return False
        self.credentials[credential_id] = replace(
            row, disabled_at=datetime.now().astimezone(), disabled_cause=cause
        )
        return True

    async def delete_credential(self, credential_id: UUID) -> bool:
        self.blocks = [b for b in self.blocks if b.credential_id != credential_id]
        return self.credentials.pop(credential_id, None) is not None

    async def delete_all_credentials(self) -> int:
        count = len(self.credentials)
        self.credentials.clear()
        self.blocks.clear()
        self.windows.clear()
        return count

    async def set_priority(self, credential_id: UUID, priority: int) -> bool:
        row = self.credentials.get(credential_id)
        if row is None:
            return False
        self.credentials[credential_id] = replace(row, priority=priority)
        return True

    async def add_block(self, block: Block) -> None:
        self.blocks.append(block)

    async def active_blocks(self, now: datetime) -> list[Block]:
        return [b for b in self.blocks if b.until > now]

    async def save_usage_windows(
        self, credential_id: UUID, windows: list[UsageWindow], fetched_at: datetime
    ) -> None:
        for w in windows:
            self.windows[(credential_id, w.window)] = StoredWindow(
                credential_id, w.window, w.used_pct, w.resets_at, fetched_at
            )

    async def usage_windows(self) -> list[StoredWindow]:
        return list(self.windows.values())

    async def record_usage(self, record: UsageRecord, at: datetime) -> None:
        self.usage.append((record, at))

    async def usage_since(self, since: datetime) -> list[UsageSummary]:
        totals: dict[tuple[UUID | None, str], list[int]] = {}
        for record, at in self.usage:
            if at < since:
                continue
            t = totals.setdefault((record.credential_id, record.provider), [0, 0, 0])
            t[0] += 1
            t[1] += record.input_tokens
            t[2] += record.output_tokens
        return [UsageSummary(cid, provider, *t) for (cid, provider), t in totals.items()]
