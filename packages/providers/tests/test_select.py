from datetime import timedelta

import pytest

from brainbench_providers.errors import NoCredential, ProviderRateLimited
from brainbench_providers.select import select
from brainbench_providers.store import Block, UsageWindow

from .conftest import Env


async def choose(env: Env) -> str:
    now = env.clock()
    chosen = select(
        "anthropic",
        await env.store.list_credentials(),
        await env.store.active_blocks(now),
        await env.store.usage_windows(),
        now,
    )
    return str(chosen.identity["email"])


async def test_priority_breaks_ties_then_age(env: Env) -> None:
    await env.connect(email="a@x.io")
    await env.connect(email="b@x.io")
    assert await choose(env) == "a@x.io"
    c = await env.connect(email="c@x.io")
    await env.store.set_priority(c.id, -1)
    assert await choose(env) == "c@x.io"


async def test_blocked_account_is_skipped_until_the_block_expires(env: Env) -> None:
    a = await env.connect(email="a@x.io")
    await env.connect(email="b@x.io")
    await env.store.add_block(Block(a.id, "5h", env.clock() + timedelta(minutes=30)))
    assert await choose(env) == "b@x.io"
    env.clock.advance(minutes=31)
    assert await choose(env) == "a@x.io"


async def test_most_remaining_in_tightest_window_wins(env: Env) -> None:
    a = await env.connect(email="a@x.io")
    b = await env.connect(email="b@x.io")
    reset = env.clock() + timedelta(hours=3)
    await env.store.save_usage_windows(
        a.id, [UsageWindow("5h", 80.0, reset), UsageWindow("7d", 10.0, reset)], env.clock()
    )
    await env.store.save_usage_windows(
        b.id, [UsageWindow("5h", 40.0, reset), UsageWindow("7d", 60.0, reset)], env.clock()
    )
    assert await choose(env) == "b@x.io"  # 40 % left beats 20 % left
    env.clock.advance(hours=4)  # both windows reset: equal again, priority/age decides
    assert await choose(env) == "a@x.io"


async def test_exhausted_window_counts_as_block(env: Env) -> None:
    a = await env.connect(email="a@x.io")
    await env.connect(email="b@x.io")
    await env.store.save_usage_windows(
        a.id, [UsageWindow("7d", 100.0, env.clock() + timedelta(days=2))], env.clock()
    )
    assert await choose(env) == "b@x.io"


async def test_disabled_accounts_are_skipped(env: Env) -> None:
    a = await env.connect(email="a@x.io")
    await env.connect(email="b@x.io")
    await env.store.disable_credential(a.id, expected_version=a.version, cause="x")
    assert await choose(env) == "b@x.io"


async def test_all_blocked_reports_earliest_reset(env: Env) -> None:
    a = await env.connect(email="a@x.io")
    b = await env.connect(email="b@x.io")
    first = env.clock() + timedelta(minutes=20)
    await env.store.add_block(Block(a.id, "5h", env.clock() + timedelta(hours=1)))
    await env.store.add_block(Block(b.id, "5h", first))
    with pytest.raises(ProviderRateLimited) as info:
        await choose(env)
    assert info.value.reset_at == first and info.value.scope == "all-accounts"


async def test_no_account(env: Env) -> None:
    await env.connect(provider="openai-codex")
    with pytest.raises(NoCredential):
        await choose(env)


async def test_lease_refreshes_a_due_credential_and_skips_revoked(env: Env) -> None:
    a = await env.connect(email="a@x.io", expires_in=timedelta(minutes=2), priority=-1)
    await env.connect(email="b@x.io")
    env.fake.revoked.update(list(env.fake.refresh_tokens)[:1])  # a's refresh token is dead
    handle = await env.pool.lease("anthropic")
    assert handle.identity.email == "b@x.io"
    disabled = await env.store.get_credential(a.id)
    assert disabled is not None and disabled.disabled_cause == "needs-reauthentication"


async def test_rate_limit_writes_a_block(env: Env) -> None:
    await env.connect()
    handle = await env.pool.lease("anthropic")
    until = await handle.rate_limited("5h", env.clock() + timedelta(hours=1), "429")
    blocks = await env.store.active_blocks(env.clock())
    assert [(b.scope, b.until) for b in blocks] == [("5h", until)]
    default = await handle.rate_limited("requests", None)
    assert default == env.clock() + timedelta(seconds=60)
