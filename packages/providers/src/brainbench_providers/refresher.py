"""Background token refresher (design D2, part 4).

Every sweep (60 s) refreshes credentials whose access token expires within the skew (5 min).
A refresh is single-flight per credential inside the process and compare-and-set on
``version`` across processes. A definitive failure (``invalid_grant`` and friends) disables only
the version that failed, with cause ``needs-reauthentication``; network and server errors are
left for the next sweep.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import UUID

import httpx

from brainbench_providers.errors import (
    AuthenticationFailed,
    NoCredential,
    OAuthError,
    RefreshFailed,
    VaultLocked,
)
from brainbench_providers.oauth.tokens import (
    Clock,
    HttpFactory,
    default_http,
    post_token_request,
    utcnow,
)
from brainbench_providers.registry import Registry
from brainbench_providers.store import ProviderStore
from brainbench_providers.vault import (
    Credential,
    StoredCredential,
    VaultCipher,
    open_credential,
    seal_tokens,
)

log = logging.getLogger(__name__)

NEEDS_REAUTH = "needs-reauthentication"
DEFINITIVE_ERRORS = frozenset(
    {"invalid_grant", "invalid_client", "unauthorized_client", "access_denied", "invalid_token"}
)
SWEEP_INTERVAL_S = 60.0
REFRESH_SKEW = timedelta(minutes=5)

CipherFn = Callable[[], Awaitable[VaultCipher]]
SweepHook = Callable[[], Awaitable[None]]


@dataclass
class SweepResult:
    refreshed: list[UUID] = field(default_factory=list)
    disabled: list[UUID] = field(default_factory=list)
    retry: list[UUID] = field(default_factory=list)


def _definitive(exc: OAuthError) -> bool:
    if exc.error in DEFINITIVE_ERRORS:
        return True
    return exc.status in {400, 401} and exc.error is None


class Refresher:
    def __init__(
        self,
        *,
        registry: Registry,
        store: ProviderStore,
        cipher: CipherFn,
        http: HttpFactory = default_http,
        clock: Clock = utcnow,
        skew: timedelta = REFRESH_SKEW,
        interval_s: float = SWEEP_INTERVAL_S,
        hooks: list[SweepHook] | None = None,
    ) -> None:
        self._registry = registry
        self._store = store
        self._cipher = cipher
        self._http = http
        self._clock = clock
        self.skew = skew
        self.interval_s = interval_s
        self.hooks = hooks if hooks is not None else []
        self._inflight: dict[UUID, tuple[bool, asyncio.Task[Credential]]] = {}

    def due(self, stored: StoredCredential) -> bool:
        return (
            not stored.disabled
            and stored.expires_at is not None
            and stored.expires_at - self.skew <= self._clock()
        )

    async def sweep(self) -> SweepResult:
        result = SweepResult()
        for stored in await self._store.list_credentials():
            if not self.due(stored):
                continue
            try:
                await self.refresh(stored.id)
                result.refreshed.append(stored.id)
            except RefreshFailed as exc:
                (result.disabled if exc.definitive else result.retry).append(stored.id)
            except AuthenticationFailed, NoCredential:
                continue
            except VaultLocked:
                log.warning("refresher skipped: vault locked")
                return result
        for hook in self.hooks:
            try:
                await hook()
            except Exception:
                log.exception("refresher sweep hook failed")
        return result

    async def run_forever(self) -> None:
        while True:
            try:
                result = await self.sweep()
                if result.refreshed or result.disabled or result.retry:
                    log.info(
                        "refresh sweep: %d refreshed, %d disabled, %d to retry",
                        len(result.refreshed),
                        len(result.disabled),
                        len(result.retry),
                    )
            except Exception:
                log.exception("refresh sweep failed")
            await asyncio.sleep(self.interval_s)

    async def refresh(
        self, credential_id: UUID, *, force: bool = False, stale_version: int | None = None
    ) -> Credential:
        """Refresh one credential; concurrent callers share the same in-flight request.

        ``force`` refreshes even when the token is not near expiry (the transport saw a 401);
        with ``stale_version`` it is skipped when the row already moved past that version.
        """
        current = self._inflight.get(credential_id)
        if current is not None:
            shared_force, task = current
            if shared_force or not force:
                return await asyncio.shield(task)
            with contextlib.suppress(Exception):
                await asyncio.shield(task)
        task = asyncio.create_task(
            self._refresh(credential_id, force=force, stale_version=stale_version)
        )
        self._inflight[credential_id] = (force, task)
        task.add_done_callback(lambda _: self._release(credential_id, task))
        return await asyncio.shield(task)

    def _release(self, credential_id: UUID, task: asyncio.Task[Credential]) -> None:
        current = self._inflight.get(credential_id)
        if current is not None and current[1] is task:
            del self._inflight[credential_id]

    async def _refresh(
        self, credential_id: UUID, *, force: bool, stale_version: int | None
    ) -> Credential:
        stored = await self._store.get_credential(credential_id)
        if stored is None:
            raise NoCredential("The account was removed")
        if stored.disabled:
            raise AuthenticationFailed(f"Account needs a new sign-in ({stored.disabled_cause})")
        cipher = await self._cipher()
        credential = open_credential(cipher, stored)
        if not force and not self.due(stored):
            return credential
        if force and stale_version is not None and stored.version > stale_version:
            return credential
        if credential.refresh_token is None:
            await self._disable(stored, "no refresh token stored")
            raise RefreshFailed("No refresh token stored; sign in again", definitive=True)
        spec = self._registry.get(stored.provider)
        try:
            tokens = await post_token_request(
                self._http,
                spec.refresh.token_url,
                {
                    "grant_type": "refresh_token",
                    "refresh_token": credential.refresh_token,
                    "client_id": spec.login[0].resolved_client_id,
                },
                body=spec.refresh.token_body,
                headers=spec.refresh.headers,
                timeout_s=spec.refresh.timeout_s,
                clock=self._clock,
            )
        except OAuthError as exc:
            if not _definitive(exc):
                log.warning("refresh of %s failed, will retry: %s", stored.id, exc.message)
                raise RefreshFailed(exc.message, definitive=False) from None
            newer = await self._newer(stored)
            if newer is not None:
                return open_credential(cipher, newer)
            await self._disable(stored, exc.message)
            raise RefreshFailed(exc.message, definitive=True) from None
        except httpx.HTTPError as exc:
            log.warning("refresh of %s failed, will retry: %s", stored.id, type(exc).__name__)
            raise RefreshFailed("Network error during refresh", definitive=False) from None
        sealed = seal_tokens(
            cipher,
            provider=stored.provider,
            identity_key=stored.identity_key,
            access_token=tokens.access_token,
            refresh_token=tokens.refresh_token or credential.refresh_token,
        )
        updated = await self._store.update_tokens(
            stored.id, expected_version=stored.version, tokens=sealed, expires_at=tokens.expires_at
        )
        if updated is None:
            # Another process refreshed first; its tokens are the current ones.
            latest = await self._store.get_credential(stored.id)
            if latest is None:
                raise NoCredential("The account was removed")
            log.info(
                "refresh of %s lost the version race; using version %d", stored.id, latest.version
            )
            return open_credential(cipher, latest)
        log.info("refreshed %s to version %d", stored.id, updated.version)
        return open_credential(cipher, updated)

    async def _newer(self, stored: StoredCredential) -> StoredCredential | None:
        """The row if another process refreshed it after we read it (our refresh token was
        rotated away), so an ``invalid_grant`` here is not the account's fault."""
        latest = await self._store.get_credential(stored.id)
        if latest is not None and latest.version > stored.version and not latest.disabled:
            return latest
        return None

    async def _disable(self, stored: StoredCredential, reason: str) -> None:
        if await self._store.disable_credential(
            stored.id, expected_version=stored.version, cause=NEEDS_REAUTH
        ):
            log.warning("credential %s needs re-authentication: %s", stored.id, reason)
