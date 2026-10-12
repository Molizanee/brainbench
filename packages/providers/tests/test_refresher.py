import asyncio
from datetime import timedelta

import pytest

from brainbench_providers.errors import AuthenticationFailed, RefreshFailed
from brainbench_providers.vault import SealedTokens, open_credential

from .conftest import Env

TOKEN_URL = "https://api.anthropic.com/v1/oauth/token"


async def test_sweep_refreshes_only_credentials_near_expiry(env: Env) -> None:
    near = await env.connect(email="a@x.io", expires_in=timedelta(minutes=4))
    far = await env.connect(email="b@x.io", expires_in=timedelta(hours=2))
    result = await env.refresher.sweep()
    assert result.refreshed == [near.id]
    after = await env.store.get_credential(near.id)
    assert after is not None and after.version == near.version + 1
    assert after.expires_at == env.clock() + timedelta(seconds=env.fake.expires_in)
    assert (await env.store.get_credential(far.id)) == far
    request = env.fake.calls(TOKEN_URL)[0]
    assert request.headers["anthropic-beta"] == "oauth-2025-04-20"
    assert (
        b'"grant_type": "refresh_token"' in request.content or b'"refresh_token"' in request.content
    )


async def test_refresh_is_single_flight(env: Env) -> None:
    stored = await env.connect(expires_in=timedelta(minutes=1))
    results = await asyncio.gather(*(env.refresher.refresh(stored.id) for _ in range(5)))
    assert len(env.fake.calls(TOKEN_URL)) == 1
    assert len({r.access_token for r in results}) == 1
    assert results[0].version == stored.version + 1


async def test_lost_version_race_uses_the_winners_tokens(env: Env) -> None:
    stored = await env.connect(expires_in=timedelta(minutes=1))
    winner = await env.refresher.refresh(stored.id)
    # A second process read the row before the winner's write and refreshes with the old,
    # already rotated refresh token: the provider answers invalid_grant, but the row moved on,
    # so the account must not be disabled and the winner's tokens are used.
    current_get = env.store.get_credential
    reads = 0

    async def stale_first_read(credential_id):  # type: ignore[no-untyped-def]
        nonlocal reads
        reads += 1
        return stored if reads == 1 else await current_get(credential_id)

    env.store.get_credential = stale_first_read  # type: ignore[method-assign]
    result = await env.refresher.refresh(stored.id, force=True)
    assert result.access_token == winner.access_token
    current = await current_get(stored.id)
    assert current is not None and not current.disabled


async def test_cas_rejects_stale_version(env: Env) -> None:
    stored = await env.connect()
    sealed = (await env.store.get_credential(stored.id)).access_enc  # type: ignore[union-attr]
    assert sealed is not None
    first = await env.store.update_tokens(
        stored.id,
        expected_version=stored.version,
        tokens=SealedTokens(sealed, None),
        expires_at=None,
    )
    second = await env.store.update_tokens(
        stored.id,
        expected_version=stored.version,
        tokens=SealedTokens(sealed, None),
        expires_at=None,
    )
    assert first is not None and second is None


async def test_invalid_grant_disables_the_account(env: Env) -> None:
    stored = await env.connect(expires_in=timedelta(minutes=1))
    env.fake.revoked.update(env.fake.refresh_tokens)
    result = await env.refresher.sweep()
    assert result.disabled == [stored.id]
    after = await env.store.get_credential(stored.id)
    assert after is not None and after.disabled_cause == "needs-reauthentication"
    assert after.version == stored.version  # disabled, not deleted
    with pytest.raises(AuthenticationFailed):
        await env.refresher.refresh(stored.id)


async def test_transient_failure_retries_next_sweep(env: Env) -> None:
    stored = await env.connect(expires_in=timedelta(minutes=1))
    env.fake.refresh_error = 503
    assert (await env.refresher.sweep()).retry == [stored.id]
    after = await env.store.get_credential(stored.id)
    assert after is not None and not after.disabled
    env.fake.refresh_error = None
    assert (await env.refresher.sweep()).refreshed == [stored.id]


async def test_forced_refresh_skips_when_already_newer(env: Env) -> None:
    stored = await env.connect()
    first = await env.refresher.refresh(stored.id, force=True, stale_version=stored.version)
    again = await env.refresher.refresh(stored.id, force=True, stale_version=stored.version)
    assert first.version == again.version == stored.version + 1
    assert len(env.fake.calls(TOKEN_URL)) == 1


async def test_missing_refresh_token_is_definitive(env: Env) -> None:
    stored = await env.connect(expires_in=timedelta(minutes=1))
    credential = open_credential(env.cipher, stored)
    assert credential.refresh_token is not None
    env.store.credentials[stored.id] = type(stored)(**{**stored.__dict__, "refresh_enc": None})
    with pytest.raises(RefreshFailed) as info:
        await env.refresher.refresh(stored.id)
    assert info.value.definitive
