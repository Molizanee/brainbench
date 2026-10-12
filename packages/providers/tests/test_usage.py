import itertools
from datetime import UTC, datetime, timedelta

from brainbench_providers.store import UsageWindow
from brainbench_providers.usage import (
    UsageMonitor,
    anthropic_windows_from_headers,
    codex_windows_from_headers,
    parse_anthropic_usage,
    parse_codex_usage,
)

from .conftest import Env

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
ANTHROPIC_USAGE = {
    "five_hour": {"utilization": 42.0, "resets_at": "2026-10-11T15:00:00Z"},
    "seven_day": {"utilization": 130, "resets_at": "2026-10-15T00:00:00+00:00"},
    "seven_day_opus": None,
}
CODEX_USAGE = {
    "plan_type": "plus",
    "rate_limit": {
        "allowed": True,
        "limit_reached": False,
        "primary_window": {
            "used_percent": 12,
            "limit_window_seconds": 18000,
            "reset_after_seconds": 600,
        },
        "secondary_window": {
            "used_percent": 55.5,
            "limit_window_seconds": 604800,
            "reset_at": 1791900000,
        },
    },
}


def test_parse_anthropic_usage() -> None:
    assert parse_anthropic_usage(ANTHROPIC_USAGE) == [
        UsageWindow("5h", 42.0, datetime(2026, 10, 11, 15, tzinfo=UTC)),
        UsageWindow("7d", 100.0, datetime(2026, 10, 15, tzinfo=UTC)),
    ]
    assert parse_anthropic_usage({"five_hour": None}) == []
    assert parse_anthropic_usage("nope") == []


def test_parse_codex_usage() -> None:
    assert parse_codex_usage(CODEX_USAGE, NOW) == [
        UsageWindow("5h", 12.0, NOW + timedelta(minutes=10)),
        UsageWindow("7d", 55.5, datetime.fromtimestamp(1791900000, UTC)),
    ]
    reached = {"rate_limit": {**CODEX_USAGE["rate_limit"], "limit_reached": True}}  # type: ignore[dict-item]
    assert {w.used_pct for w in parse_codex_usage(reached, NOW)} == {100.0}


def test_rate_limit_headers() -> None:
    assert anthropic_windows_from_headers(
        {
            "Anthropic-Ratelimit-Unified-5h-Utilization": "0.25",
            "anthropic-ratelimit-unified-5h-reset": "1791900000",
        }
    ) == [UsageWindow("5h", 25.0, datetime.fromtimestamp(1791900000, UTC))]
    assert codex_windows_from_headers(
        {
            "x-codex-primary-used-percent": "80",
            "x-codex-primary-window-minutes": "300",
            "x-codex-primary-reset-at": "1791900000",
            "x-codex-secondary-used-percent": "5",
            "x-codex-secondary-window-minutes": "10080",
        }
    ) == [
        UsageWindow("5h", 80.0, datetime.fromtimestamp(1791900000, UTC)),
        UsageWindow("7d", 5.0, None),
    ]
    assert codex_windows_from_headers({}) == []


async def test_monitor_fetches_then_caches_five_minutes_with_jitter(env: Env) -> None:
    env.fake.usage = {"anthropic": ANTHROPIC_USAGE, "openai-codex": CODEX_USAGE}
    a = await env.connect()
    c = await env.connect(provider="openai-codex")
    jitter = itertools.cycle([1.0, 0.0])  # alternately +25 % and -25 %
    monitor = UsageMonitor(
        registry=env.registry,
        store=env.store,
        cipher=env.get_cipher,
        http=env.fake.http_factory(),
        clock=env.clock,
        rng=lambda: next(jitter),
    )
    assert set(await monitor.refresh_stale()) == {a.id, c.id}
    windows = {(w.credential_id, w.window): w.used_pct for w in await env.store.usage_windows()}
    assert windows[(a.id, "5h")] == 42.0 and windows[(c.id, "7d")] == 55.5
    codex_call = env.fake.calls("https://chatgpt.com/backend-api/wham/usage")[0]
    assert codex_call.headers["ChatGPT-Account-Id"] == "org-1"
    env.clock.advance(minutes=4)
    assert await monitor.refresh_stale() == [c.id]  # 3.75 min period for the second account
    env.clock.advance(minutes=2, seconds=30)
    assert await monitor.refresh_stale() == [a.id]  # 6.25 min period for the first


async def test_monitor_skips_disabled_and_failed_fetches_wait(env: Env) -> None:
    a = await env.connect()
    monitor = UsageMonitor(
        registry=env.registry,
        store=env.store,
        cipher=env.get_cipher,
        http=env.fake.http_factory(),
        clock=env.clock,
        rng=lambda: 0.5,
    )
    assert await monitor.refresh_stale() == []  # fake has no usage: 404, retried next period
    assert len(env.fake.calls("https://api.anthropic.com/api/oauth/usage")) == 1
    assert await monitor.refresh_stale() == []
    assert len(env.fake.calls("https://api.anthropic.com/api/oauth/usage")) == 1
    await env.store.disable_credential(a.id, expected_version=a.version, cause="x")
    env.clock.advance(minutes=10)
    await monitor.refresh_stale()
    assert len(env.fake.calls("https://api.anthropic.com/api/oauth/usage")) == 1
