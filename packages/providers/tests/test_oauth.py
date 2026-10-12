import asyncio
import base64
import hashlib
import socket
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
import pytest

from brainbench_providers.errors import (
    LoginAlreadyFinished,
    LoginNotFound,
    OAuthError,
    StateMismatch,
)
from brainbench_providers.oauth.code import new_pkce
from brainbench_providers.oauth.manager import LoginManager
from brainbench_providers.oauth.tokens import TokenSet
from brainbench_providers.registry import ProviderSpec, load_registry
from brainbench_providers.testing import FakeProviders
from brainbench_providers.vault import Identity

REGISTRY = load_registry()
ANTHROPIC = REGISTRY.get("anthropic")
CODEX = REGISTRY.get("openai-codex")


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class Persisted:
    def __init__(self) -> None:
        self.calls: list[tuple[str, TokenSet, Identity]] = []

    async def __call__(self, spec: ProviderSpec, tokens: TokenSet, identity: Identity) -> UUID:
        self.calls.append((spec.id, tokens, identity))
        return uuid4()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def fake() -> FakeProviders:
    return FakeProviders()


@pytest.fixture
def persisted() -> Persisted:
    return Persisted()


@pytest.fixture
def clock() -> Clock:
    return Clock()


def manager(fake: FakeProviders, persisted: Persisted, clock: Clock, **kw: object) -> LoginManager:
    return LoginManager(persist=persisted, http=fake.http_factory(), clock=clock, **kw)  # type: ignore[arg-type]


def test_pkce_challenge_is_s256_of_verifier() -> None:
    pkce = new_pkce()
    digest = hashlib.sha256(pkce.verifier.encode()).digest()
    assert pkce.challenge == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert 43 <= len(pkce.verifier) <= 128
    assert pkce.verifier not in repr(pkce)


async def test_authorize_url_carries_pkce_state_and_provider_params(
    fake: FakeProviders, persisted: Persisted, clock: Clock
) -> None:
    view = await manager(fake, persisted, clock).start(ANTHROPIC)
    assert view.authorize_url is not None and view.paste_code and not view.callback_listening
    parts = urlsplit(view.authorize_url)
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://claude.ai/oauth/authorize"
    assert query["client_id"] == "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    assert query["redirect_uri"] == "http://localhost:54545/callback"
    assert query["code_challenge_method"] == "S256" and len(query["state"]) >= 32
    assert query["code"] == "true"
    assert "user:inference" in query["scope"].split(" ")
    assert view.expires_at == clock.now + timedelta(minutes=10)


async def test_paste_redirect_completes_and_stores_identity(
    fake: FakeProviders, persisted: Persisted, clock: Clock
) -> None:
    logins = manager(fake, persisted, clock)
    view = await logins.start(ANTHROPIC)
    done = await logins.complete(view.login_id, fake.authorize(view.authorize_url or ""))
    assert done.status == "connected" and done.credential_id is not None
    ((provider, tokens, identity),) = persisted.calls
    assert provider == "anthropic" and tokens.refresh_token is not None
    assert identity == Identity(
        email="dev@example.com", account_id="acct-1", org_id="org-1", org_name="Personal"
    )
    body = fake.calls("https://api.anthropic.com/v1/oauth/token")[0].content
    assert b'"state"' in body and b'"code_verifier"' in body  # json body with state, as omp


async def test_code_hash_state_paste_is_accepted(
    fake: FakeProviders, persisted: Persisted, clock: Clock
) -> None:
    logins = manager(fake, persisted, clock)
    view = await logins.start(ANTHROPIC)
    redirect = fake.authorize(view.authorize_url or "")
    query = parse_qs(urlsplit(redirect).query)
    pasted = f"{query['code'][0]}#{query['state'][0]}"
    assert (await logins.complete(view.login_id, pasted)).status == "connected"


async def test_state_mismatch_is_rejected_and_nothing_stored(
    fake: FakeProviders, persisted: Persisted, clock: Clock
) -> None:
    logins = manager(fake, persisted, clock)
    view = await logins.start(ANTHROPIC)
    redirect = fake.authorize(view.authorize_url or "").replace("state=", "state=forged")
    with pytest.raises(StateMismatch):
        await logins.complete(view.login_id, redirect)
    assert persisted.calls == []
    assert logins.get(view.login_id).status == "failed"
    assert fake.calls("https://api.anthropic.com/v1/oauth/token") == []


async def test_wrong_verifier_fails_exchange(
    fake: FakeProviders, persisted: Persisted, clock: Clock
) -> None:
    logins = manager(fake, persisted, clock)
    view = await logins.start(ANTHROPIC)
    redirect = fake.authorize(view.authorize_url or "")
    fake.challenges = {k: "other-challenge" for k in fake.challenges}
    with pytest.raises(OAuthError, match="400"):
        await logins.complete(view.login_id, redirect)
    assert persisted.calls == []


async def test_identity_falls_back_to_bootstrap(
    fake: FakeProviders, persisted: Persisted, clock: Clock
) -> None:
    fake.anthropic_identity_in_token = False
    logins = manager(fake, persisted, clock)
    view = await logins.start(ANTHROPIC)
    await logins.complete(view.login_id, fake.authorize(view.authorize_url or ""))
    assert persisted.calls[0][2].org_id == "org-1"
    bootstrap = fake.calls("https://api.anthropic.com/api/claude_cli/bootstrap")[0]
    assert bootstrap.headers["anthropic-beta"] == "oauth-2025-04-20"


async def test_codex_identity_from_jwt_claims(
    fake: FakeProviders, persisted: Persisted, clock: Clock
) -> None:
    logins = manager(fake, persisted, clock)
    view = await logins.start(CODEX, "oauth-code")
    query = parse_qs(urlsplit(view.authorize_url or "").query)
    assert query["originator"] == ["omp"] and query["codex_cli_simplified_flow"] == ["true"]
    await logins.complete(view.login_id, fake.authorize(view.authorize_url or ""))
    identity = persisted.calls[0][2]
    assert (identity.email, identity.org_id, identity.plan) == ("dev@example.com", "ws-1", "plus")


async def test_expired_login_cannot_complete(
    fake: FakeProviders, persisted: Persisted, clock: Clock
) -> None:
    logins = manager(fake, persisted, clock)
    view = await logins.start(ANTHROPIC)
    redirect = fake.authorize(view.authorize_url or "")
    clock.now += timedelta(minutes=10, seconds=1)
    assert logins.get(view.login_id).status == "expired"
    with pytest.raises(LoginAlreadyFinished):
        await logins.complete(view.login_id, redirect)
    clock.now += timedelta(minutes=11)
    with pytest.raises(LoginNotFound):
        logins.get(view.login_id)


async def http_get(url: str) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.get(url)


async def test_loopback_callback_completes_then_late_callback_is_ignored(
    fake: FakeProviders, persisted: Persisted, clock: Clock
) -> None:
    port = free_port()
    logins = manager(
        fake,
        persisted,
        clock,
        callbacks_enabled=True,
        callback_host="127.0.0.1",
        port_overrides={54545: port},
    )
    view = await logins.start(ANTHROPIC)
    assert view.callback_listening and view.callback_port == 54545
    redirect = fake.authorize(view.authorize_url or "")
    local = redirect.replace("localhost:54545", f"127.0.0.1:{port}")
    response = await http_get(local)
    assert response.status_code == 200 and "Connected" in response.text
    assert logins.get(view.login_id).status == "connected"
    assert not logins.get(view.login_id).callback_listening
    with pytest.raises(LoginAlreadyFinished):
        await logins.complete(view.login_id, redirect)
    await asyncio.sleep(0.05)
    with pytest.raises(httpx.ConnectError):
        await http_get(local)  # the one-shot listener is closed
    assert len(persisted.calls) == 1
    await logins.close()


async def test_duplicate_concurrent_callback_shows_connected_and_exchanges_once(
    fake: FakeProviders, persisted: Persisted, clock: Clock
) -> None:
    port = free_port()
    logins = manager(
        fake,
        persisted,
        clock,
        callbacks_enabled=True,
        callback_host="127.0.0.1",
        port_overrides={54545: port},
    )
    view = await logins.start(ANTHROPIC)
    redirect = fake.authorize(view.authorize_url or "")
    local = redirect.replace("localhost:54545", f"127.0.0.1:{port}")
    first, second = await asyncio.gather(http_get(local), http_get(local))
    assert [first.status_code, second.status_code] == [200, 200]
    assert "Connected" in first.text and "Connected" in second.text
    assert logins.get(view.login_id).status == "connected"
    assert len(persisted.calls) == 1
    assert len(fake.calls("https://api.anthropic.com/v1/oauth/token")) == 1
    await logins.close()


async def test_paste_wins_closes_listener_and_late_callback_is_ignored(
    fake: FakeProviders, persisted: Persisted, clock: Clock
) -> None:
    port = free_port()
    logins = manager(
        fake,
        persisted,
        clock,
        callbacks_enabled=True,
        callback_host="127.0.0.1",
        port_overrides={54545: port},
    )
    first = await logins.start(ANTHROPIC)
    second = await logins.start(ANTHROPIC)  # shares the listener on the same port
    redirect = fake.authorize(first.authorize_url or "")
    assert (await logins.complete(first.login_id, redirect)).status == "connected"
    local = redirect.replace("localhost:54545", f"127.0.0.1:{port}")
    late = await http_get(local)  # listener still serves the second login
    assert late.status_code == 200 and "Connected" in late.text  # ignored: no second exchange
    assert len(persisted.calls) == 1
    other = fake.authorize(second.authorize_url or "")
    assert (await logins.complete(second.login_id, other)).status == "connected"
    await asyncio.sleep(0.05)
    with pytest.raises(httpx.ConnectError):
        await http_get(local)
    assert len(persisted.calls) == 2
    await logins.close()


async def test_callback_listener_answers_404_elsewhere(
    fake: FakeProviders, persisted: Persisted, clock: Clock
) -> None:
    port = free_port()
    logins = manager(
        fake,
        persisted,
        clock,
        callbacks_enabled=True,
        callback_host="127.0.0.1",
        port_overrides={1455: port},
    )
    await logins.start(CODEX, "oauth-code")
    assert (await http_get(f"http://127.0.0.1:{port}/other")).status_code == 404
    await logins.close()


async def test_busy_callback_port_leaves_paste_only(
    fake: FakeProviders, persisted: Persisted, clock: Clock
) -> None:
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        logins = manager(
            fake,
            persisted,
            clock,
            callbacks_enabled=True,
            callback_host="127.0.0.1",
            port_overrides={54545: port},
        )
        view = await logins.start(ANTHROPIC)
    assert not view.callback_listening and view.paste_code


class ManualSleep:
    """Sleep that yields to the loop and advances the fake clock by the requested time."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self.clock.now += timedelta(seconds=seconds)
        await asyncio.sleep(0)


async def wait_status(logins: LoginManager, login_id: str, status: str) -> None:
    for _ in range(200):
        if logins.get(login_id).status == status:
            return
        await asyncio.sleep(0)
    raise AssertionError(logins.get(login_id))


async def test_device_code_polls_at_interval_until_approved(
    fake: FakeProviders, persisted: Persisted, clock: Clock
) -> None:
    sleep = ManualSleep(clock)
    logins = manager(fake, persisted, clock, sleep=sleep)
    view = await logins.start(CODEX, "device-code")
    assert view.user_code == "ABCD-1234"
    assert view.verification_url == "https://auth.openai.com/codex/device"
    assert view.interval_s == 4.0  # provider interval 1 s + omp's 3 s safety margin
    for _ in range(20):
        await asyncio.sleep(0)
    assert view.login_id and logins.get(view.login_id).status == "pending"
    fake.device_approved = True
    await wait_status(logins, view.login_id, "connected")
    assert set(sleep.calls) == {4.0}
    assert persisted.calls[0][2].org_id == "ws-1"
    exchange = fake.calls("https://auth.openai.com/oauth/token")[0].content.decode()
    assert "redirect_uri=https%3A%2F%2Fauth.openai.com%2Fdeviceauth%2Fcallback" in exchange


async def test_device_code_expires(fake: FakeProviders, persisted: Persisted, clock: Clock) -> None:
    logins = manager(fake, persisted, clock, sleep=ManualSleep(clock))
    view = await logins.start(CODEX, "device-code")
    await wait_status(logins, view.login_id, "expired")
    assert persisted.calls == []
    polls = fake.calls("https://auth.openai.com/api/accounts/deviceauth/token")
    assert 100 <= len(polls) <= 160  # every 4 s for 10 minutes
    with pytest.raises(OAuthError):
        await logins.complete(view.login_id, "code")
