"""Account selection and per-request credential handles (design D2, part 4).

``select(provider)`` skips disabled and blocked accounts, then prefers the account with the most
remaining usage in its tightest current window, then the engineer's priority order (lower
first), then the oldest connection. A window at 100 % that has not reset counts as a block. When
every account of the provider is blocked it raises ``ProviderRateLimited`` with the earliest
reset, which the job layer turns into a deferral.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from brainbench_providers.errors import (
    AuthenticationFailed,
    NoCredential,
    ProviderRateLimited,
    RefreshFailed,
)
from brainbench_providers.oauth.tokens import Clock, utcnow
from brainbench_providers.refresher import NEEDS_REAUTH, CipherFn, Refresher
from brainbench_providers.store import (
    Block,
    ProviderStore,
    StoredWindow,
    UsageRecord,
    UsageWindow,
)
from brainbench_providers.vault import Credential, Identity, StoredCredential, open_credential

log = logging.getLogger(__name__)

DEFAULT_BLOCK = timedelta(seconds=60)
ALL_ACCOUNTS = "all-accounts"


def blocked_until(
    credential_id: UUID,
    blocks: Sequence[Block],
    windows: Sequence[StoredWindow],
    now: datetime,
) -> datetime | None:
    """Latest time a block or an exhausted window keeps this account unusable, if any."""
    until = [b.until for b in blocks if b.credential_id == credential_id and b.until > now]
    until += [
        w.resets_at
        for w in windows
        if w.credential_id == credential_id
        and w.used_pct is not None
        and w.used_pct >= 100
        and w.resets_at is not None
        and w.resets_at > now
    ]
    return max(until) if until else None


def remaining_pct(credential_id: UUID, windows: Sequence[StoredWindow], now: datetime) -> float:
    """Remaining share in the tightest window that has not reset; 100 when nothing is known."""
    used = [
        w.used_pct
        for w in windows
        if w.credential_id == credential_id
        and w.used_pct is not None
        and (w.resets_at is None or w.resets_at > now)
    ]
    return max(0.0, 100.0 - max(used)) if used else 100.0


def rank(
    credentials: Sequence[StoredCredential],
    blocks: Sequence[Block],
    windows: Sequence[StoredWindow],
    now: datetime,
) -> list[StoredCredential]:
    usable = [
        c
        for c in credentials
        if not c.disabled and blocked_until(c.id, blocks, windows, now) is None
    ]
    return sorted(
        usable,
        key=lambda c: (
            -remaining_pct(c.id, windows, now),
            c.priority,
            c.created_at or datetime.min.replace(tzinfo=now.tzinfo),
        ),
    )


def select(
    provider: str,
    credentials: Sequence[StoredCredential],
    blocks: Sequence[Block],
    windows: Sequence[StoredWindow],
    now: datetime,
) -> StoredCredential:
    own = [c for c in credentials if c.provider == provider]
    enabled = [c for c in own if not c.disabled]
    if not enabled:
        raise NoCredential(f"No connected {provider} account")
    ranked = rank(enabled, blocks, windows, now)
    if ranked:
        return ranked[0]
    resets = [blocked_until(c.id, blocks, windows, now) for c in enabled]
    earliest = min(r for r in resets if r is not None)
    raise ProviderRateLimited(
        earliest,
        ALL_ACCOUNTS,
        f"Every {provider} account is rate limited until {earliest:%H:%M} UTC",
    )


@dataclass
class CredentialHandle:
    """One selected account for one model call. Transports use it to authenticate, to recover
    from a 401 once, and to report rate limits, usage windows, and token counts."""

    pool: AccountPool
    credential: Credential = field(repr=False)

    @property
    def id(self) -> UUID:
        return self.credential.id

    @property
    def provider(self) -> str:
        return self.credential.provider

    @property
    def identity(self) -> Identity:
        return self.credential.identity

    def access_token(self) -> str:
        return self.credential.access_token

    async def refresh_after_unauthorized(self) -> str:
        """Refresh once after a 401 on a token the vault considered valid."""
        try:
            self.credential = await self.pool.refresher.refresh(
                self.credential.id, force=True, stale_version=self.credential.version
            )
        except RefreshFailed as exc:
            raise AuthenticationFailed(exc.message) from None
        return self.credential.access_token

    async def unauthorized_again(self) -> None:
        """A second 401 after the refresh: the account needs a new sign-in."""
        await self.pool.store.disable_credential(
            self.credential.id, expected_version=self.credential.version, cause=NEEDS_REAUTH
        )
        log.warning("credential %s rejected after refresh; needs re-authentication", self.id)

    async def rate_limited(
        self, scope: str, reset_at: datetime | None, reason: str | None = None
    ) -> datetime:
        until = reset_at or self.pool.clock() + DEFAULT_BLOCK
        await self.pool.store.add_block(Block(self.credential.id, scope, until, reason))
        log.info("credential %s blocked for %s until %s", self.id, scope, until.isoformat())
        return until

    async def observe_windows(self, windows: list[UsageWindow]) -> None:
        if windows:
            await self.pool.store.save_usage_windows(self.credential.id, windows, self.pool.clock())

    async def record_usage(
        self, *, model: str, task_class: str, input_tokens: int, output_tokens: int
    ) -> None:
        await self.pool.store.record_usage(
            UsageRecord(
                self.credential.id,
                self.provider,
                model,
                task_class,
                input_tokens,
                output_tokens,
            ),
            self.pool.clock(),
        )


class AccountPool:
    def __init__(
        self,
        *,
        store: ProviderStore,
        cipher: CipherFn,
        refresher: Refresher,
        clock: Clock = utcnow,
    ) -> None:
        self.store = store
        self.cipher = cipher
        self.refresher = refresher
        self.clock = clock

    async def lease(self, provider: str) -> CredentialHandle:
        """Select an account for ``provider`` and return it with a fresh access token."""
        skipped: set[UUID] = set()
        while True:
            now = self.clock()
            credentials = [
                c for c in await self.store.list_credentials(provider) if c.id not in skipped
            ]
            chosen = select(
                provider,
                credentials,
                await self.store.active_blocks(now),
                await self.store.usage_windows(),
                now,
            )
            try:
                return CredentialHandle(self, await self._fresh(chosen))
            except (AuthenticationFailed, RefreshFailed) as exc:
                if isinstance(exc, RefreshFailed) and not exc.definitive:
                    raise
                skipped.add(chosen.id)

    async def _fresh(self, stored: StoredCredential) -> Credential:
        if not self.refresher.due(stored):
            return open_credential(await self.cipher(), stored)
        try:
            return await self.refresher.refresh(stored.id)
        except RefreshFailed as exc:
            still_valid = stored.expires_at is not None and stored.expires_at > self.clock()
            if exc.definitive or not still_valid:
                raise
            return open_credential(await self.cipher(), stored)
