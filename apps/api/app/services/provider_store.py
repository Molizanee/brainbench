"""SQLAlchemy implementation of ``brainbench_providers.store.ProviderStore``.

Every method runs in its own short transaction from the session factory, so the refresher, the
OAuth loopback listener, and model calls can use it outside any request.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.schema import ProviderBlock, ProviderCredential, ProviderUsage, ProviderUsageWindow
from brainbench_providers.store import (
    Block,
    StoredWindow,
    UsageRecord,
    UsageSummary,
    UsageWindow,
)
from brainbench_providers.vault import SealedTokens, StoredCredential

SessionFactory = Callable[[], AsyncSession]


def _aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; every timestamp the provider layer writes is UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _utc(value: datetime) -> datetime:
    aware = _aware(value)
    assert aware is not None
    return aware


def _stored(row: ProviderCredential) -> StoredCredential:
    return StoredCredential(
        id=row.id,
        provider=row.provider,
        identity_key=row.identity_key,
        identity=dict(row.identity or {}),
        access_enc=row.access_enc,
        refresh_enc=row.refresh_enc,
        expires_at=_aware(row.expires_at),
        version=row.version,
        disabled_at=_aware(row.disabled_at),
        disabled_cause=row.disabled_cause,
        priority=row.priority,
        created_at=_aware(row.created_at),
    )


class SqlProviderStore:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sessions = session_factory

    async def list_credentials(self, provider: str | None = None) -> list[StoredCredential]:
        query = select(ProviderCredential).order_by(
            ProviderCredential.priority, ProviderCredential.created_at
        )
        if provider is not None:
            query = query.where(ProviderCredential.provider == provider)
        async with self._sessions() as session:
            rows = (await session.scalars(query)).all()
            return [_stored(row) for row in rows]

    async def get_credential(self, credential_id: UUID) -> StoredCredential | None:
        async with self._sessions() as session:
            row = await session.get(ProviderCredential, credential_id)
            return None if row is None else _stored(row)

    async def upsert_credential(
        self,
        *,
        provider: str,
        identity_key: str,
        identity: dict[str, Any],
        tokens: SealedTokens,
        expires_at: datetime | None,
    ) -> StoredCredential:
        for attempt in range(2):
            try:
                return await self._upsert(provider, identity_key, identity, tokens, expires_at)
            except IntegrityError:
                if attempt:
                    raise
        raise AssertionError("unreachable")

    async def _upsert(
        self,
        provider: str,
        identity_key: str,
        identity: dict[str, Any],
        tokens: SealedTokens,
        expires_at: datetime | None,
    ) -> StoredCredential:
        async with self._sessions() as session, session.begin():
            row = await session.scalar(
                select(ProviderCredential).where(
                    ProviderCredential.provider == provider,
                    ProviderCredential.identity_key == identity_key,
                )
            )
            if row is None:
                row = ProviderCredential(provider=provider, identity_key=identity_key, version=1)
                session.add(row)
            else:
                row.version += 1
                row.disabled_at = None
                row.disabled_cause = None
            row.identity = identity
            row.access_enc = tokens.access_enc
            row.refresh_enc = tokens.refresh_enc
            row.expires_at = expires_at
            await session.flush()
            await session.refresh(row)
            return _stored(row)

    async def update_tokens(
        self,
        credential_id: UUID,
        *,
        expected_version: int,
        tokens: SealedTokens,
        expires_at: datetime | None,
    ) -> StoredCredential | None:
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(ProviderCredential)
                .where(
                    ProviderCredential.id == credential_id,
                    ProviderCredential.version == expected_version,
                )
                .values(
                    access_enc=tokens.access_enc,
                    refresh_enc=tokens.refresh_enc,
                    expires_at=expires_at,
                    version=ProviderCredential.version + 1,
                    updated_at=func.now(),
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:  # type: ignore[attr-defined]
                return None
            row = await session.get(ProviderCredential, credential_id, populate_existing=True)
            assert row is not None
            return _stored(row)

    async def disable_credential(
        self, credential_id: UUID, *, expected_version: int, cause: str
    ) -> bool:
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(ProviderCredential)
                .where(
                    ProviderCredential.id == credential_id,
                    ProviderCredential.version == expected_version,
                )
                .values(disabled_at=func.now(), disabled_cause=cause, updated_at=func.now())
                .execution_options(synchronize_session=False)
            )
            return bool(result.rowcount == 1)  # type: ignore[attr-defined]

    async def delete_credential(self, credential_id: UUID) -> bool:
        async with self._sessions() as session, session.begin():
            await session.execute(
                delete(ProviderBlock).where(ProviderBlock.credential_id == credential_id)
            )
            await session.execute(
                delete(ProviderUsageWindow).where(
                    ProviderUsageWindow.credential_id == credential_id
                )
            )
            result = await session.execute(
                delete(ProviderCredential).where(ProviderCredential.id == credential_id)
            )
            return bool(result.rowcount == 1)  # type: ignore[attr-defined]

    async def delete_all_credentials(self) -> int:
        async with self._sessions() as session, session.begin():
            await session.execute(delete(ProviderBlock))
            await session.execute(delete(ProviderUsageWindow))
            result = await session.execute(delete(ProviderCredential))
            return int(result.rowcount)  # type: ignore[attr-defined]

    async def set_priority(self, credential_id: UUID, priority: int) -> bool:
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(ProviderCredential)
                .where(ProviderCredential.id == credential_id)
                .values(priority=priority)
                .execution_options(synchronize_session=False)
            )
            return bool(result.rowcount == 1)  # type: ignore[attr-defined]

    async def add_block(self, block: Block) -> None:
        async with self._sessions() as session, session.begin():
            if await session.get(ProviderCredential, block.credential_id) is None:
                return
            session.add(
                ProviderBlock(
                    credential_id=block.credential_id,
                    scope=block.scope,
                    until=block.until,
                    reason=block.reason,
                )
            )

    async def active_blocks(self, now: datetime) -> list[Block]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(select(ProviderBlock).where(ProviderBlock.until > now))
            ).all()
            return [Block(r.credential_id, r.scope, _utc(r.until), r.reason) for r in rows]

    async def save_usage_windows(
        self, credential_id: UUID, windows: list[UsageWindow], fetched_at: datetime
    ) -> None:
        async with self._sessions() as session, session.begin():
            if await session.get(ProviderCredential, credential_id) is None:
                return
            existing = {
                row.window: row
                for row in (
                    await session.scalars(
                        select(ProviderUsageWindow).where(
                            ProviderUsageWindow.credential_id == credential_id
                        )
                    )
                ).all()
            }
            for w in windows:
                row = existing.get(w.window)
                if row is None:
                    row = ProviderUsageWindow(credential_id=credential_id, window=w.window)
                    session.add(row)
                row.used_pct = w.used_pct
                row.resets_at = w.resets_at
                row.fetched_at = fetched_at

    async def usage_windows(self) -> list[StoredWindow]:
        async with self._sessions() as session:
            rows = (await session.scalars(select(ProviderUsageWindow))).all()
            return [
                StoredWindow(
                    r.credential_id, r.window, r.used_pct, _aware(r.resets_at), _utc(r.fetched_at)
                )
                for r in rows
            ]

    async def record_usage(self, record: UsageRecord, at: datetime) -> None:
        async with self._sessions() as session, session.begin():
            session.add(
                ProviderUsage(
                    credential_id=record.credential_id,
                    provider=record.provider,
                    model=record.model,
                    task_class=record.task_class,
                    input_tokens=record.input_tokens,
                    output_tokens=record.output_tokens,
                    at=at,
                )
            )

    async def usage_since(self, since: datetime) -> list[UsageSummary]:
        query = (
            select(
                ProviderUsage.credential_id,
                ProviderUsage.provider,
                func.count(),
                func.coalesce(func.sum(ProviderUsage.input_tokens), 0),
                func.coalesce(func.sum(ProviderUsage.output_tokens), 0),
            )
            .where(ProviderUsage.at >= since)
            .group_by(ProviderUsage.credential_id, ProviderUsage.provider)
        )
        async with self._sessions() as session:
            rows = (await session.execute(query)).all()
            return [UsageSummary(r[0], r[1], int(r[2]), int(r[3]), int(r[4])) for r in rows]
