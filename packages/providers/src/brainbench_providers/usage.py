"""Usage windows per account (design D2, part 5).

Fetchers read the provider's usage endpoint (Anthropic ``/api/oauth/usage``, ChatGPT
``/wham/usage``); parsers also read the rate-limit headers both backends attach to every
response, so selection sees an exhausted account before the next request burns a 429. Fetches
are cached five minutes with ±25 % jitter, as omp does. Per-call token counts are recorded by
``CredentialHandle.record_usage``.
"""

import logging
import random
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx

from brainbench_providers.oauth.tokens import Clock, HttpFactory, default_http, utcnow
from brainbench_providers.refresher import CipherFn
from brainbench_providers.registry import ProviderSpec, Registry
from brainbench_providers.store import ProviderStore, UsageWindow
from brainbench_providers.vault import StoredCredential, open_credential

log = logging.getLogger(__name__)

USAGE_TTL = timedelta(minutes=5)
USAGE_JITTER = 0.25
WINDOW_NAMES = {18000: "5h", 604800: "7d"}


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _epoch(value: Any) -> datetime | None:
    seconds = _num(value)
    return datetime.fromtimestamp(seconds, UTC) if seconds and seconds > 0 else None


def _clamp(pct: float | None) -> float | None:
    return None if pct is None else min(max(pct, 0.0), 100.0)


def parse_anthropic_usage(payload: Any) -> list[UsageWindow]:
    """``five_hour`` / ``seven_day`` buckets: ``utilization`` in percent, ``resets_at`` ISO."""
    windows: list[UsageWindow] = []
    if not isinstance(payload, dict):
        return windows
    for key, name in (("five_hour", "5h"), ("seven_day", "7d")):
        bucket = payload.get(key)
        if not isinstance(bucket, dict):
            continue
        used, resets = _clamp(_num(bucket.get("utilization"))), _iso(bucket.get("resets_at"))
        if used is not None or resets is not None:
            windows.append(UsageWindow(name, used, resets))
    return windows


def _codex_window_name(seconds: float | None, fallback: str) -> str:
    if seconds is None:
        return fallback
    return WINDOW_NAMES.get(int(seconds), f"{round(seconds / 3600)}h")


def parse_codex_usage(payload: Any, now: datetime) -> list[UsageWindow]:
    """``rate_limit.{primary,secondary}_window``: ``used_percent``, ``limit_window_seconds``,
    ``reset_at`` (epoch seconds) or ``reset_after_seconds``; ``limit_reached`` pins 100 %."""
    rate = payload.get("rate_limit") if isinstance(payload, dict) else None
    if not isinstance(rate, dict):
        return []
    windows: list[UsageWindow] = []
    for key, fallback in (("primary_window", "primary"), ("secondary_window", "secondary")):
        window = rate.get(key)
        if not isinstance(window, dict):
            continue
        used = _clamp(_num(window.get("used_percent")))
        resets = _epoch(window.get("reset_at"))
        after = _num(window.get("reset_after_seconds"))
        if resets is None and after is not None:
            resets = now + timedelta(seconds=after)
        if rate.get("limit_reached") is True and resets is not None:
            used = 100.0
        name = _codex_window_name(_num(window.get("limit_window_seconds")), fallback)
        if used is not None or resets is not None:
            windows.append(UsageWindow(name, used, resets))
    return windows


def _header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return None


def anthropic_windows_from_headers(headers: Mapping[str, str]) -> list[UsageWindow]:
    """``anthropic-ratelimit-unified-{5h,7d}-utilization`` (fraction) and ``-reset`` (epoch)."""
    windows = []
    for name in ("5h", "7d"):
        prefix = f"anthropic-ratelimit-unified-{name}-"
        fraction = _num(_header(headers, f"{prefix}utilization"))
        resets = _epoch(_header(headers, f"{prefix}reset"))
        if fraction is not None or resets is not None:
            windows.append(
                UsageWindow(name, _clamp(fraction * 100 if fraction is not None else None), resets)
            )
    return windows


def codex_windows_from_headers(headers: Mapping[str, str]) -> list[UsageWindow]:
    """``x-codex-{primary,secondary}-{used-percent,window-minutes,reset-at}``."""
    windows = []
    for key in ("primary", "secondary"):
        used = _num(_header(headers, f"x-codex-{key}-used-percent"))
        if used is None:
            continue
        minutes = _num(_header(headers, f"x-codex-{key}-window-minutes"))
        name = _codex_window_name(minutes * 60 if minutes is not None else None, key)
        windows.append(
            UsageWindow(name, _clamp(used), _epoch(_header(headers, f"x-codex-{key}-reset-at")))
        )
    return windows


def windows_from_headers(spec: ProviderSpec, headers: Mapping[str, str]) -> list[UsageWindow]:
    if spec.api == "anthropic-messages":
        return anthropic_windows_from_headers(headers)
    return codex_windows_from_headers(headers)


class UsageMonitor:
    """Fetches each account's usage windows when its cached copy is older than ~5 minutes."""

    def __init__(
        self,
        *,
        registry: Registry,
        store: ProviderStore,
        cipher: CipherFn,
        http: HttpFactory = default_http,
        clock: Clock = utcnow,
        ttl: timedelta = USAGE_TTL,
        jitter: float = USAGE_JITTER,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._registry = registry
        self._store = store
        self._cipher = cipher
        self._http = http
        self._clock = clock
        self._ttl = ttl
        self._jitter = jitter
        self._rng = rng
        self._next_due: dict[UUID, datetime] = {}

    def _schedule(self, credential_id: UUID, fetched_at: datetime) -> None:
        factor = 1 + self._jitter * (2 * self._rng() - 1)
        self._next_due[credential_id] = fetched_at + self._ttl * factor

    async def refresh_stale(self) -> list[UUID]:
        """Fetch windows for every enabled account whose cache expired; returns those fetched."""
        now = self._clock()
        if not self._next_due:
            for window in await self._store.usage_windows():
                self._schedule(window.credential_id, window.fetched_at)
        fetched = []
        for stored in await self._store.list_credentials():
            if stored.disabled or now < self._next_due.get(stored.id, now):
                continue
            if stored.provider not in self._registry.ids:
                continue
            spec = self._registry.get(stored.provider)
            if spec.usage is None:
                continue
            self._schedule(stored.id, now)  # a failed fetch also waits a full period
            if await self.fetch(spec, stored) is not None:
                fetched.append(stored.id)
        return fetched

    async def fetch(self, spec: ProviderSpec, stored: StoredCredential) -> list[UsageWindow] | None:
        usage = spec.usage
        if usage is None:
            return None
        now = self._clock()
        if stored.expires_at is not None and stored.expires_at <= now:
            return None
        credential = open_credential(await self._cipher(), stored)
        headers = {**usage.headers, "Authorization": f"Bearer {credential.access_token}"}
        if usage.format == "codex-wham-usage" and credential.identity.account_id:
            headers["ChatGPT-Account-Id"] = credential.identity.account_id
        try:
            async with self._http() as client:
                response = await client.get(usage.endpoint, headers=headers, timeout=30.0)
        except httpx.HTTPError as exc:
            log.warning("usage fetch for %s failed: %s", stored.id, type(exc).__name__)
            return None
        if response.status_code >= 400:
            log.warning("usage fetch for %s answered %d", stored.id, response.status_code)
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        windows = (
            parse_anthropic_usage(payload)
            if usage.format == "anthropic-oauth-usage"
            else parse_codex_usage(payload, now)
        )
        await self._store.save_usage_windows(stored.id, windows, now)
        return windows
