"""Pending logins and the start / poll / complete lifecycle the API drives (design D2, D3).

Pending logins live in memory with a 10-minute TTL. A code login always accepts a pasted
redirect; when callbacks are enabled it also registers its state with the loopback listener on
the provider's callback port (one listener per port, shared by concurrent logins, closed when
no pending login needs it). Whichever path completes first wins under the login's lock; the
other is closed, and a late callback for a consumed state gets an "already used" page.
A device login polls in a background task at the provider's interval until approval or expiry.
"""

import asyncio
import contextlib
import logging
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from brainbench_providers.errors import (
    LoginAlreadyFinished,
    LoginNotFound,
    OAuthError,
    ProviderError,
    StateMismatch,
)
from brainbench_providers.oauth.code import (
    Pkce,
    authorize_url,
    exchange_code,
    new_pkce,
    new_state,
    parse_authorization_input,
    verify_state,
)
from brainbench_providers.oauth.device import DeviceAuthorization, poll_once, request_user_code
from brainbench_providers.oauth.listener import CallbackServer, page
from brainbench_providers.oauth.tokens import (
    Clock,
    HttpFactory,
    TokenSet,
    default_http,
    resolve_identity,
    utcnow,
)
from brainbench_providers.registry import DeviceCodeLogin, OAuthCodeLogin, ProviderSpec
from brainbench_providers.vault import Identity

log = logging.getLogger(__name__)

LOGIN_TTL = timedelta(minutes=10)

LoginStatus = Literal["pending", "connected", "failed", "expired"]

# Stores the exchanged tokens for ``identity`` and returns the credential id.
PersistFn = Callable[[ProviderSpec, TokenSet, Identity], Awaitable[UUID]]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class LoginView:
    """What the API returns for a login. Holds no secret: the state is in the URL by design."""

    login_id: str
    provider: str
    kind: str
    status: LoginStatus
    expires_at: datetime
    authorize_url: str | None = None
    paste_code: bool = False
    callback_listening: bool = False
    callback_port: int | None = None
    verification_url: str | None = None
    user_code: str | None = None
    interval_s: float | None = None
    credential_id: UUID | None = None
    error: str | None = None
    instructions: str | None = None


@dataclass
class PendingLogin:
    id: str
    spec: ProviderSpec
    login: OAuthCodeLogin | DeviceCodeLogin
    expires_at: datetime
    status: LoginStatus = "pending"
    error: str | None = None
    credential_id: UUID | None = None
    finished_at: datetime | None = None
    state: str | None = field(default=None, repr=False)
    pkce: Pkce | None = field(default=None, repr=False)
    url: str | None = None
    callback_port: int | None = None
    device: DeviceAuthorization | None = field(default=None, repr=False)
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def view(self) -> LoginView:
        code = self.login if isinstance(self.login, OAuthCodeLogin) else None
        device = self.login if isinstance(self.login, DeviceCodeLogin) else None
        return LoginView(
            login_id=self.id,
            provider=self.spec.id,
            kind=self.login.kind,
            status=self.status,
            expires_at=self.expires_at,
            authorize_url=self.url,
            paste_code=bool(code and code.paste_code),
            callback_listening=self.callback_port is not None and self.status == "pending",
            callback_port=self.callback_port,
            verification_url=device.verification_url if device else None,
            user_code=self.device.user_code if self.device else None,
            interval_s=self.device.interval_s if self.device else None,
            credential_id=self.credential_id,
            error=self.error,
            instructions=self.login.instructions,
        )


class LoginManager:
    def __init__(
        self,
        *,
        persist: PersistFn,
        http: HttpFactory = default_http,
        callbacks_enabled: bool = False,
        callback_host: str = "0.0.0.0",  # published only on 127.0.0.1 by compose (D3)
        clock: Clock = utcnow,
        sleep: Sleep = asyncio.sleep,
        ttl: timedelta = LOGIN_TTL,
        port_overrides: dict[int, int] | None = None,
    ) -> None:
        self._persist = persist
        self._http = http
        self._callbacks = callbacks_enabled
        self._host = callback_host
        self._clock = clock
        self._sleep = sleep
        self._ttl = ttl
        self._ports = port_overrides or {}
        self._logins: dict[str, PendingLogin] = {}
        self._servers: dict[int, CallbackServer] = {}
        self._states: dict[int, dict[str, str]] = {}

    # --- public API -----------------------------------------------------------------------

    async def start(self, spec: ProviderSpec, kind: str | None = None) -> LoginView:
        self._prune()
        login = spec.login_for(kind)
        pending = PendingLogin(
            id=secrets.token_urlsafe(16),
            spec=spec,
            login=login,
            expires_at=self._clock() + self._ttl,
        )
        if isinstance(login, OAuthCodeLogin):
            pending.state, pending.pkce = new_state(), new_pkce()
            pending.url = authorize_url(login, state=pending.state, pkce=pending.pkce)
            if self._callbacks and await self._listen(login, pending):
                pending.callback_port = login.callback_port
            pending.task = asyncio.create_task(self._expire_later(pending))
        else:
            pending.device = await request_user_code(self._http, login)
            pending.task = asyncio.create_task(self._poll_device(pending, login))
        self._logins[pending.id] = pending
        log.info("login %s started for %s (%s)", pending.id, spec.id, login.kind)
        return pending.view()

    def get(self, login_id: str) -> LoginView:
        pending = self._find(login_id)
        if pending.status == "pending" and self._clock() >= pending.expires_at:
            self._expire(pending)
        return pending.view()

    async def complete(self, login_id: str, text: str) -> LoginView:
        """Paste path: the failed redirect URL, ``code#state``, or the bare code."""
        pending = self._find(login_id)
        if not isinstance(pending.login, OAuthCodeLogin):
            raise OAuthError("This login completes on the provider's device page")
        received = parse_authorization_input(text)
        await self._finish_code(pending, received.code, received.state, source="paste")
        return pending.view()

    async def close(self) -> None:
        for pending in self._logins.values():
            if pending.task is not None:
                pending.task.cancel()
        for server in list(self._servers.values()):
            await server.close()
        self._servers.clear()
        self._states.clear()

    def clear(self) -> None:
        """Forget every pending login (vault reset)."""
        for pending in list(self._logins.values()):
            if pending.status == "pending":
                self._expire(pending)
        self._logins.clear()

    # --- code logins --------------------------------------------------------------------------

    def _port(self, login: OAuthCodeLogin) -> int:
        return self._ports.get(login.callback_port, login.callback_port)

    async def _listen(self, login: OAuthCodeLogin, pending: PendingLogin) -> bool:
        port = self._port(login)
        server = self._servers.get(port)
        if server is None:
            server = CallbackServer(
                self._host,
                port,
                login.callback_path,
                lambda params: self._on_callback(port, params),
            )
            if not await server.start():
                return False
            self._servers[port] = server
        assert pending.state is not None
        self._states.setdefault(port, {})[pending.state] = pending.id
        return True

    async def _on_callback(self, port: int, params: dict[str, str]) -> tuple[int, str]:
        login_id = self._states.get(port, {}).get(params.get("state", ""))
        pending = self._logins.get(login_id) if login_id else None
        if pending is None or pending.status != "pending":
            log.info("ignored OAuth callback for an unknown or consumed state on port %d", port)
            return 400, page(
                "Sign-in link already used",
                "This sign-in was already completed, cancelled, or has expired. "
                "Return to BrainBench and start again if needed.",
            )
        if "error" in params:
            self._fail(pending, f"The provider refused the authorization: {params['error']}")
            return 400, page("Sign-in refused", pending.error or "")
        code = params.get("code")
        if not code:
            return 400, page("Missing code", "The callback carried no authorization code.")
        try:
            await self._finish_code(pending, code, params.get("state"), source="callback")
        except LoginAlreadyFinished:
            return 400, page("Sign-in link already used", "This sign-in was already completed.")
        except ProviderError as exc:
            return 400, page("Sign-in failed", exc.message)
        return 200, page("Connected", "You can close this tab and return to BrainBench.")

    async def _finish_code(
        self, pending: PendingLogin, code: str, state: str | None, *, source: str
    ) -> None:
        async with pending.lock:
            if pending.status == "pending" and self._clock() >= pending.expires_at:
                self._expire(pending)
            if pending.status != "pending":
                raise LoginAlreadyFinished(f"This sign-in is already {pending.status}")
            assert isinstance(pending.login, OAuthCodeLogin)
            assert pending.state is not None and pending.pkce is not None
            try:
                verify_state(pending.state, state)
            except StateMismatch as exc:
                self._fail(pending, exc.message)
                raise
            try:
                tokens = await exchange_code(
                    self._http,
                    pending.login,
                    code=code,
                    state=pending.state,
                    pkce=pending.pkce,
                    clock=self._clock,
                )
                await self._connect(pending, tokens)
            except ProviderError as exc:
                self._fail(pending, exc.message)
                raise
            log.info("login %s completed by %s", pending.id, source)

    # --- device logins ------------------------------------------------------------------------

    async def _poll_device(self, pending: PendingLogin, login: DeviceCodeLogin) -> None:
        assert pending.device is not None
        first = True
        try:
            while pending.status == "pending":
                interval = pending.device.interval_s
                await self._sleep(min(interval, 5.0) if first else interval)
                first = False
                if self._clock() >= pending.expires_at:
                    self._expire(pending)
                    return
                tokens = await poll_once(self._http, login, pending.device, clock=self._clock)
                if tokens is None:
                    continue
                async with pending.lock:
                    if pending.status == "pending":
                        await self._connect(pending, tokens)
                        log.info("login %s approved on the device page", pending.id)
        except ProviderError as exc:
            self._fail(pending, exc.message)
        except Exception:
            log.exception("device login %s failed", pending.id)
            self._fail(pending, "Device sign-in failed unexpectedly")

    # --- shared -------------------------------------------------------------------------------

    async def _connect(self, pending: PendingLogin, tokens: TokenSet) -> None:
        identity = await resolve_identity(self._http, pending.spec.identity, tokens)
        pending.credential_id = await self._persist(pending.spec, tokens, identity)
        self._settle(pending, "connected")

    def _fail(self, pending: PendingLogin, message: str) -> None:
        pending.error = message
        self._settle(pending, "failed")
        log.info("login %s failed: %s", pending.id, message)

    def _expire(self, pending: PendingLogin) -> None:
        if pending.status == "pending":
            pending.error = "The sign-in expired; start again."
            self._settle(pending, "expired")

    def _settle(self, pending: PendingLogin, status: LoginStatus) -> None:
        pending.status = status
        pending.finished_at = self._clock()
        if pending.task is not None and pending.task is not asyncio.current_task():
            pending.task.cancel()
        if pending.callback_port is not None and pending.state is not None:
            port = self._ports.get(pending.callback_port, pending.callback_port)
            states = self._states.get(port, {})
            states.pop(pending.state, None)
            if not states and port in self._servers:
                server = self._servers.pop(port)
                self._states.pop(port, None)
                with contextlib.suppress(RuntimeError):
                    asyncio.get_running_loop().create_task(server.close())

    async def _expire_later(self, pending: PendingLogin) -> None:
        await self._sleep(max((pending.expires_at - self._clock()).total_seconds(), 0.0))
        self._expire(pending)

    def _find(self, login_id: str) -> PendingLogin:
        self._prune()
        pending = self._logins.get(login_id)
        if pending is None:
            raise LoginNotFound("Unknown or expired sign-in")
        return pending

    def _prune(self) -> None:
        """Drop logins that ended more than one TTL ago."""
        cutoff = self._clock() - self._ttl
        for login_id, pending in list(self._logins.items()):
            if pending.status == "pending" and self._clock() >= pending.expires_at:
                self._expire(pending)
            if pending.finished_at is not None and pending.finished_at < cutoff:
                del self._logins[login_id]
