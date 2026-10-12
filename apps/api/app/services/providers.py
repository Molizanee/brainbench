"""Provider connection service (design D2, D3): the app side of ``brainbench_providers``.

The package is pure Python; this module wires it to the database (``SqlProviderStore``), the
vault key file (``vault_key``), and instance settings. One :class:`ProviderRuntime` per process
holds what must outlive a request: the registry, the store, and the cached vault cipher.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable, Iterable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from functools import cache
from pathlib import Path
from typing import Literal
from uuid import UUID

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import Conflict, DomainError, NotFound
from app.db.session import SessionFactory as AppSessionFactory
from app.jobs.errors import ProviderLimitReached
from app.models.providers import (
    AccountRead,
    BlockRead,
    EffectiveRoute,
    ModelRead,
    ProviderRead,
    ProviderSettingsRead,
    ProviderSettingsUpdate,
    ProvidersRead,
    RouteEntry,
    RoutingModelOption,
    RoutingProviderOption,
    UsageTodayRead,
    VaultRead,
    WindowRead,
)
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
from brainbench_providers import TASK_CLASSES
from brainbench_providers.errors import ProviderError, UnknownProvider, VaultLocked
from brainbench_providers.oauth.manager import LoginManager, LoginView
from brainbench_providers.oauth.tokens import HttpFactory, TokenSet, default_http
from brainbench_providers.refresher import SWEEP_INTERVAL_S, Refresher
from brainbench_providers.registry import ModelSpec, ProviderSpec, Registry, load_registry
from brainbench_providers.select import AccountPool, blocked_until
from brainbench_providers.usage import UsageMonitor
from brainbench_providers.vault import (
    Identity,
    StoredCredential,
    VaultCipher,
    identity_key,
    open_credential,
    seal_tokens,
)

__lazy_modules__ = {
    "brainbench_providers.strands_models",
    "strands.models.model",
    "strands.types.exceptions",
}
from strands.models.model import Model
from strands.types.exceptions import ModelThrottledException

from brainbench_providers.strands_models import build_model

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


class NoProvider(DomainError):
    """No connected, enabled account for a task class; the message links the providers page."""

    code = "no-provider"


class InvalidRouting(DomainError):
    code = "invalid-routing"


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
    http: HttpFactory = default_http
    callbacks_enabled: bool = False
    callback_host: str = "0.0.0.0"
    callback_port_overrides: dict[int, int] = field(default_factory=dict)
    refresh_interval_s: float = SWEEP_INTERVAL_S
    store: SqlProviderStore = field(init=False)
    logins: LoginManager = field(init=False)
    refresher: Refresher = field(init=False)
    pool: AccountPool = field(init=False)
    usage: UsageMonitor = field(init=False)
    _cipher: tuple[str, VaultCipher] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.store = SqlProviderStore(self.session_factory)
        self.logins = LoginManager(
            persist=self._persist_login,
            http=self.http,
            callbacks_enabled=self.callbacks_enabled,
            callback_host=self.callback_host,
            clock=self.clock,
            port_overrides=self.callback_port_overrides,
        )
        self.refresher = Refresher(
            registry=self.registry,
            store=self.store,
            cipher=self.cipher,
            http=self.http,
            clock=self.clock,
            interval_s=self.refresh_interval_s,
        )
        self.pool = AccountPool(
            store=self.store, cipher=self.cipher, refresher=self.refresher, clock=self.clock
        )
        self.usage = UsageMonitor(
            registry=self.registry,
            store=self.store,
            cipher=self.cipher,
            http=self.http,
            clock=self.clock,
        )
        self.refresher.hooks.append(self._refresh_usage)

    async def _refresh_usage(self) -> None:
        if (await self.vault_status()).state == "ok":
            await self.usage.refresh_stale()

    async def enabled_ids(self) -> list[str] | None:
        async with self.session_factory() as session:
            ids = await enabled_provider_ids(session)
            await session.commit()
        return ids

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
        self.logins.clear()
        log.warning("vault reset: %d credentials deleted, new key generated", deleted)
        return await self.vault_status()

    # --- overview (task 3.5) ------------------------------------------------------------------

    async def overview(self) -> ProvidersRead:
        """Every registry provider with its accounts, windows, blocks, and today's usage."""
        now = self.clock()
        since = datetime.combine(now.date(), time.min, tzinfo=UTC)
        vault = await self.vault_status()
        enabled = await self.enabled_ids()
        credentials = await self.store.list_credentials()
        blocks = await self.store.active_blocks(now)
        windows = await self.store.usage_windows()
        usage = {u.credential_id: u for u in await self.store.usage_since(since)}
        providers = []
        for spec in self.registry.providers:
            accounts = []
            for c in (c for c in credentials if c.provider == spec.id):
                until = blocked_until(c.id, blocks, windows, now)
                status = (
                    "locked"
                    if vault.state == "locked"
                    else "needs-reauthentication"
                    if c.disabled
                    else "blocked"
                    if until is not None
                    else "connected"
                )
                today = usage.get(c.id)
                accounts.append(
                    AccountRead(
                        id=c.id,
                        provider=c.provider,
                        email=c.identity.get("email"),
                        account_id=c.identity.get("account_id"),
                        org_id=c.identity.get("org_id"),
                        org_name=c.identity.get("org_name"),
                        plan=c.identity.get("plan"),
                        status=status,
                        disabled_cause=c.disabled_cause,
                        expires_at=c.expires_at,
                        priority=c.priority,
                        blocked_until=until,
                        blocks=[
                            BlockRead(scope=b.scope, until=b.until, reason=b.reason)
                            for b in blocks
                            if b.credential_id == c.id
                        ],
                        windows=[
                            WindowRead(
                                window=w.window,
                                used_pct=w.used_pct,
                                resets_at=w.resets_at,
                                fetched_at=w.fetched_at,
                            )
                            for w in sorted(windows, key=lambda w: w.window)
                            if w.credential_id == c.id
                        ],
                        usage_today=UsageTodayRead(
                            calls=today.calls if today else 0,
                            input_tokens=today.input_tokens if today else 0,
                            output_tokens=today.output_tokens if today else 0,
                        ),
                        created_at=c.created_at,
                    )
                )
            code = next((login for login in spec.login if login.kind == "oauth-code"), None)
            providers.append(
                ProviderRead.model_validate(
                    {
                        "id": spec.id,
                        "name": spec.name,
                        "api": spec.api,
                        "enabled": enabled is None or spec.id in enabled,
                        "login_kinds": spec.login_kinds,
                        "callback_port": getattr(code, "callback_port", None),
                        "policy_notice": spec.policy_notice,
                        "models": [ModelRead.model_validate(m.model_dump()) for m in spec.models],
                        "accounts": accounts,
                    }
                )
            )
        return ProvidersRead(
            vault=VaultRead.model_validate(
                {"state": vault.state, "detail": vault.detail, "key_path": str(vault.key_path)}
            ),
            callbacks_enabled=self.callbacks_enabled,
            usage_since=since,
            providers=providers,
        )

    async def set_priority(self, credential_id: UUID, priority: int) -> None:
        if not await self.store.set_priority(credential_id, priority):
            raise NotFound("Unknown account", code="account-not-found")

    async def delete_account(self, credential_id: UUID) -> None:
        """Revoke the refresh token where the provider supports it, then delete the row.

        Requests already holding the token finish or fail; no new request selects it.
        """
        stored = await self.store.get_credential(credential_id)
        if stored is None:
            raise NotFound("Unknown account", code="account-not-found")
        spec = self.registry.get(stored.provider) if stored.provider in self.registry.ids else None
        if spec is not None and spec.revoke_url and stored.refresh_enc is not None:
            await self._revoke(spec, stored)
        await self.store.delete_credential(credential_id)
        log.info("credential %s removed", credential_id)

    async def _revoke(self, spec: ProviderSpec, stored: StoredCredential) -> None:
        assert spec.revoke_url is not None
        try:
            credential = open_credential(await self.cipher(), stored)
            async with self.http() as client:
                response = await client.post(
                    spec.revoke_url,
                    data={
                        "token": credential.refresh_token or credential.access_token,
                        "token_type_hint": "refresh_token",
                        "client_id": spec.login[0].resolved_client_id,
                    },
                    timeout=15.0,
                )
            log.info("revocation for %s answered %d", stored.id, response.status_code)
        except (ProviderError, httpx.HTTPError) as exc:
            log.warning("revocation for %s failed: %s", stored.id, type(exc).__name__)

    # --- logins (task 3.3) --------------------------------------------------------------------

    async def start_login(self, provider_id: str, kind: str | None) -> LoginView:
        spec = self.registry.require_enabled(provider_id, await self.enabled_ids())
        status = await self.vault_status()
        if status.state == "locked":
            raise VaultLocked(str(status.key_path), status.detail)
        return await self.logins.start(spec, kind)

    def get_login(self, login_id: str) -> LoginView:
        return self.logins.get(login_id)

    async def complete_login(self, login_id: str, text: str) -> LoginView:
        return await self.logins.complete(login_id, text)

    async def _persist_login(
        self, spec: ProviderSpec, tokens: TokenSet, identity: Identity
    ) -> UUID:
        """Upsert the account (one row per provider and ``email|org`` identity key)."""
        cipher = await self.cipher(create=True)
        key = identity_key(identity, org_scoped=spec.identity.org_scoped)
        stored = await self.store.upsert_credential(
            provider=spec.id,
            identity_key=key,
            identity=identity.as_dict(),
            tokens=seal_tokens(
                cipher,
                provider=spec.id,
                identity_key=key,
                access_token=tokens.access_token,
                refresh_token=tokens.refresh_token,
            ),
            expires_at=tokens.expires_at,
        )
        log.info("credential %s stored for %s (version %d)", stored.id, spec.id, stored.version)
        return stored.id


_runtime: ProviderRuntime | None = None


def get_provider_runtime() -> ProviderRuntime:
    """The process runtime (FastAPI dependency; tests override it)."""
    global _runtime
    if _runtime is None:
        _runtime = ProviderRuntime(
            registry=get_registry(),
            session_factory=AppSessionFactory,
            key_path=settings.vault_key_path,
            callbacks_enabled=settings.brainbench_oauth_callbacks,
            callback_host=settings.brainbench_oauth_callback_host,
        )
    return _runtime


@asynccontextmanager
async def start_refresher(
    app: FastAPI, runtime: ProviderRuntime | None = None
) -> AsyncIterator[ProviderRuntime]:
    """Run the provider background work for the API process's lifetime.

    The refresher sweeps every 60 s (token refresh, then usage windows); on exit it stops and
    closes pending logins and their callback listeners. ``app/main.py``'s lifespan wraps its
    ``yield`` in ``async with start_refresher(app):``. A runtime set through
    ``app.dependency_overrides`` (tests) is used when ``runtime`` is not given.
    """
    if runtime is None:
        runtime = app.dependency_overrides.get(get_provider_runtime, get_provider_runtime)()
    task = asyncio.create_task(runtime.refresher.run_forever(), name="provider-refresher")
    log.info("provider refresher started (every %.0f s)", runtime.refresher.interval_s)
    try:
        yield runtime
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await runtime.logins.close()
        log.info("provider refresher stopped")


# --- Routing and inference entry points (task 3.7) ---------------------------------------------


@dataclass(frozen=True)
class Route:
    task_class: str
    spec: ProviderSpec
    model: ModelSpec
    source: Literal["explicit", "default"]


def _routing(row_routing: dict[str, object] | None) -> dict[str, RouteEntry]:
    entries = {}
    for task_class, value in (row_routing or {}).items():
        try:
            entries[task_class] = RouteEntry.model_validate(value)
        except ValueError:
            log.warning("ignoring malformed routing entry for %s", task_class)
    return entries


async def _routing_state(
    session: AsyncSession, runtime: ProviderRuntime
) -> tuple[list[str] | None, dict[str, RouteEntry], set[str]]:
    row = await load_instance_settings(session)
    enabled = None if row.enabled_providers is None else list(row.enabled_providers)
    credentials = await runtime.store.list_credentials()
    connected = {c.provider for c in credentials if not c.disabled}
    return enabled, _routing(row.provider_routing), connected


def _resolve(
    runtime: ProviderRuntime,
    task_class: str,
    enabled: list[str] | None,
    routing: dict[str, RouteEntry],
    connected: set[str],
) -> Route | None:
    """Explicit route when it names an enabled registry provider and model, else the first
    enabled provider with a connected account, with its default model."""
    entry = routing.get(task_class)
    if entry is not None and entry.provider in runtime.registry.ids:
        spec = runtime.registry.get(entry.provider)
        if runtime.registry.is_enabled(spec.id, enabled):
            try:
                return Route(task_class, spec, spec.model(entry.model), "explicit")
            except UnknownProvider:
                log.warning("routing for %s names an unknown model %s", task_class, entry.model)
    for spec in runtime.registry.enabled(enabled):
        if spec.id in connected:
            return Route(task_class, spec, spec.default_model, "default")
    return None


async def resolve_route(
    session: AsyncSession, task_class: str, runtime: ProviderRuntime | None = None
) -> Route:
    """The provider and model a task class runs on; ``NoProvider`` when none can serve it."""
    runtime = runtime or get_provider_runtime()
    if task_class not in TASK_CLASSES:
        raise InvalidRouting(f"Unknown task class {task_class!r}", code="invalid-task-class")
    enabled, routing, connected = await _routing_state(session, runtime)
    route = _resolve(runtime, task_class, enabled, routing, connected)
    if route is None:
        raise NoProvider(
            f"No connected provider can run {task_class}. Connect your ChatGPT or Claude "
            "subscription on the providers page (/providers)."
        )
    if route.spec.id not in connected:
        raise NoProvider(
            f"{task_class} is routed to {route.spec.name}, which has no connected account. "
            "Connect one on the providers page (/providers) or change the routing."
        )
    status = await runtime.vault_status()
    if status.state == "locked":
        raise VaultLockedError(VaultLocked(str(status.key_path), status.detail).message)
    return route


async def ensure_provider_for(
    session: AsyncSession, task_class: str, runtime: ProviderRuntime | None = None
) -> Route:
    """Check before enqueueing a job or sending a tutor message: raises ``NoProvider``
    (``code="no-provider"``, 400) when the task class has no connected, enabled account, and
    ``VaultLockedError`` (409) when the vault is locked. Blocked accounts pass: jobs defer."""
    return await resolve_route(session, task_class, runtime)


async def model_for(
    session: AsyncSession, task_class: str, runtime: ProviderRuntime | None = None
) -> Model:
    """The Strands model for a task class: routing applied, an account selected per call."""
    runtime = runtime or get_provider_runtime()
    route = await resolve_route(session, task_class, runtime)
    return build_model(
        route.spec,
        pool=runtime.pool,
        http=runtime.http,
        model=route.model,
        task_class=task_class,
    )


def find_throttle(exc: BaseException) -> ModelThrottledException | None:
    """The provider throttle behind ``exc``, if any. A Strands ``Agent`` re-raises model errors
    wrapped in ``EventLoopException`` (``original_exception``), so the chain is walked."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, ModelThrottledException):
            return current
        seen.add(id(current))
        current = getattr(current, "original_exception", None) or current.__cause__
    return None


def provider_limit_from(exc: BaseException) -> ProviderLimitReached:
    """Job-layer conversion: a provider throttle (bare, or wrapped by the Strands agent loop)
    becomes a deferral until its reset; ``reset_at`` is None when the provider gave none."""
    throttle = find_throttle(exc)
    reset_at = getattr(throttle, "reset_at", None)
    scope = getattr(throttle, "scope", None)
    message = "waiting for provider limit reset"
    if scope:
        message = f"{message} ({scope})"
    return ProviderLimitReached(reset_at, message)


async def provider_settings(
    session: AsyncSession, runtime: ProviderRuntime
) -> ProviderSettingsRead:
    enabled, routing, connected = await _routing_state(session, runtime)
    effective: dict[str, EffectiveRoute | None] = {}
    for task_class in TASK_CLASSES:
        route = _resolve(runtime, task_class, enabled, routing, connected)
        effective[task_class] = (
            None
            if route is None
            else EffectiveRoute(
                provider=route.spec.id,
                model=route.model.id,
                source=route.source,
                connected=route.spec.id in connected,
            )
        )
    options = [
        RoutingProviderOption(
            provider=spec.id,
            name=spec.name,
            connected=spec.id in connected,
            models=[
                RoutingModelOption(id=m.id, name=m.name, default=m.default) for m in spec.models
            ],
        )
        for spec in runtime.registry.enabled(enabled)
    ]
    return ProviderSettingsRead.model_validate(
        {
            "task_classes": list(TASK_CLASSES),
            "enabled_providers": enabled,
            "routing": {
                k: v
                for k, v in routing.items()
                if k in TASK_CLASSES and runtime.registry.is_enabled(v.provider, enabled)
                if v.provider in runtime.registry.ids
            },
            "effective": effective,
            "options": options,
        }
    )


def _validated_enabled(runtime: ProviderRuntime, ids: list[str] | None) -> list[str] | None:
    if ids is None:
        return None
    unknown = [p for p in ids if p not in runtime.registry.ids]
    if unknown:
        raise InvalidRouting(f"Unknown providers: {', '.join(unknown)}")
    return list(dict.fromkeys(ids))


def _validate_routing(
    runtime: ProviderRuntime, routing: Iterable[tuple[str, RouteEntry]], enabled: list[str] | None
) -> None:
    for task_class, entry in routing:
        if entry.provider not in runtime.registry.ids:
            raise InvalidRouting(f"{task_class}: unknown provider {entry.provider!r}")
        spec = runtime.registry.get(entry.provider)
        if not runtime.registry.is_enabled(spec.id, enabled):
            raise InvalidRouting(f"{task_class}: {spec.name} is disabled")
        if entry.model not in {m.id for m in spec.models}:
            raise InvalidRouting(f"{task_class}: {spec.name} has no model {entry.model!r}")


async def update_provider_settings(
    session: AsyncSession, runtime: ProviderRuntime, body: ProviderSettingsUpdate
) -> ProviderSettingsRead:
    """PUT: replace the enabled providers and the routing (validated against the registry)."""
    enabled = _validated_enabled(runtime, body.enabled_providers)
    _validate_routing(runtime, body.routing.items(), enabled)
    row = await load_instance_settings(session)
    row.enabled_providers = enabled
    row.provider_routing = {k: v.model_dump() for k, v in body.routing.items()}
    await session.commit()
    log.info("provider settings replaced (enabled=%s)", enabled)
    return await provider_settings(session, runtime)


async def patch_provider_settings(
    session: AsyncSession, runtime: ProviderRuntime, body: ProviderSettingsUpdate
) -> ProviderSettingsRead:
    """PATCH: change only the fields present in the body. Routes that point at a provider
    being disabled stay stored and are ignored until it is re-enabled."""
    row = await load_instance_settings(session)
    if "enabled_providers" in body.model_fields_set:
        row.enabled_providers = _validated_enabled(runtime, body.enabled_providers)
    if "routing" in body.model_fields_set:
        enabled = None if row.enabled_providers is None else list(row.enabled_providers)
        _validate_routing(runtime, body.routing.items(), enabled)
        row.provider_routing = {k: v.model_dump() for k, v in body.routing.items()}
    await session.commit()
    log.info("provider settings updated (%s)", ", ".join(sorted(body.model_fields_set)))
    return await provider_settings(session, runtime)
