import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from brainbench_providers.oauth.tokens import TokenSet
from brainbench_providers.refresher import Refresher
from brainbench_providers.registry import Registry, load_registry
from brainbench_providers.select import AccountPool
from brainbench_providers.store import MemoryStore
from brainbench_providers.testing import FakeProviders
from brainbench_providers.vault import (
    Identity,
    StoredCredential,
    VaultCipher,
    identity_key,
    seal_tokens,
)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> None:
        self.now += timedelta(**kw)


class Env:
    """A registry, a memory store, a vault cipher, the fake provider, and a refresher."""

    def __init__(self) -> None:
        self.clock = Clock()
        self.registry: Registry = load_registry()
        self.store = MemoryStore()
        self.cipher = VaultCipher(os.urandom(32))
        self.fake = FakeProviders()
        self.refresher = Refresher(
            registry=self.registry,
            store=self.store,
            cipher=self.get_cipher,
            http=self.fake.http_factory(),
            clock=self.clock,
        )
        self.pool = AccountPool(
            store=self.store, cipher=self.get_cipher, refresher=self.refresher, clock=self.clock
        )

    async def get_cipher(self) -> VaultCipher:
        return self.cipher

    async def connect(
        self,
        provider: str = "anthropic",
        email: str = "dev@example.com",
        org: str = "org-1",
        *,
        expires_in: timedelta = timedelta(hours=8),
        priority: int = 0,
    ) -> StoredCredential:
        """Store an account whose refresh token the fake provider accepts."""
        body: dict[str, Any] = self.fake._tokens(provider)
        tokens = TokenSet(
            body["access_token"], body["refresh_token"], self.clock() + expires_in, body
        )
        key = identity_key(Identity(email=email, org_id=org), org_scoped=True)
        stored = await self.store.upsert_credential(
            provider=provider,
            identity_key=key,
            identity={"email": email, "org_id": org, "account_id": org},
            tokens=seal_tokens(
                self.cipher,
                provider=provider,
                identity_key=key,
                access_token=tokens.access_token,
                refresh_token=tokens.refresh_token,
            ),
            expires_at=tokens.expires_at,
        )
        if priority:
            await self.store.set_priority(stored.id, priority)
            stored = await self.store.get_credential(stored.id)  # type: ignore[assignment]
        self.clock.advance(seconds=1)
        return stored


@pytest.fixture
def env() -> Env:
    return Env()


AsyncFn = Callable[..., Awaitable[Any]]
