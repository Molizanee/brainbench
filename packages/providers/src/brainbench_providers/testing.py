"""In-process fakes of the shipped providers' auth endpoints, for tests without network.

``FakeProviders`` is an ``httpx.MockTransport`` handler that emulates the Anthropic and ChatGPT
token endpoints (authorization code with PKCE verification, refresh with rotation, revocation),
the Claude identity bootstrap, the ChatGPT device-code endpoints, and both usage endpoints.
Every token it issues contains ``PLAINTEXT`` so tests can grep for leaks.
"""

import base64
import hashlib
import itertools
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

ANTHROPIC_TOKEN_URL = "https://api.anthropic.com/v1/oauth/token"
ANTHROPIC_BOOTSTRAP_URL = "https://api.anthropic.com/api/claude_cli/bootstrap"
ANTHROPIC_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OPENAI_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_DEVICE_URL = "https://auth.openai.com/api/accounts/deviceauth/usercode"
OPENAI_DEVICE_TOKEN_URL = "https://auth.openai.com/api/accounts/deviceauth/token"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"


def fake_jwt(claims: dict[str, Any]) -> str:
    def part(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none'})}.{part(claims)}.PLAINTEXT-signature"


@dataclass
class FakeAccount:
    email: str = "dev@example.com"
    account_id: str = "acct-1"
    org_id: str = "org-1"
    org_name: str = "Personal"
    plan: str = "pro"


@dataclass
class FakeProviders:
    anthropic_account: FakeAccount = field(default_factory=FakeAccount)
    openai_account: FakeAccount = field(
        default_factory=lambda: FakeAccount(account_id="ws-1", org_id="ws-1", org_name="plus")
    )
    # Whether Anthropic's token response carries account/organization (else bootstrap is used).
    anthropic_identity_in_token: bool = True
    expires_in: int = 3600
    usage: dict[str, Any] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    challenges: dict[str, str] = field(default_factory=dict, repr=False)
    refresh_tokens: dict[str, str] = field(default_factory=dict, repr=False)
    revoked: set[str] = field(default_factory=set, repr=False)
    device_approved: bool = False
    refresh_error: int | None = None
    _seq: itertools.count[int] = field(default_factory=itertools.count, repr=False)

    # --- helpers for tests ------------------------------------------------------------------

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    def http_factory(self) -> Callable[[], httpx.AsyncClient]:
        return self.client

    def authorize(self, authorize_url: str) -> str:
        """Simulate the browser consent: returns the redirect URL the provider would send."""
        query = {k: v[0] for k, v in parse_qs(urlsplit(authorize_url).query).items()}
        code = f"code-{next(self._seq)}"
        self.challenges[code] = query["code_challenge"]
        return f"{query['redirect_uri']}?code={code}&state={query['state']}"

    def issued(self) -> list[str]:
        """Every access and refresh token issued so far."""
        return [*self.refresh_tokens.values(), *self.refresh_tokens.keys()]

    def calls(self, url: str) -> list[httpx.Request]:
        return [r for r in self.requests if str(r.url).split("?")[0] == url]

    # --- transport ----------------------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url).split("?")[0]
        routes: dict[str, Callable[[httpx.Request], httpx.Response]] = {
            ANTHROPIC_TOKEN_URL: self._anthropic_token,
            ANTHROPIC_BOOTSTRAP_URL: self._anthropic_bootstrap,
            ANTHROPIC_USAGE_URL: lambda r: self._usage(r, "anthropic"),
            OPENAI_TOKEN_URL: self._openai_token,
            OPENAI_DEVICE_URL: self._device_code,
            OPENAI_DEVICE_TOKEN_URL: self._device_poll,
            CODEX_USAGE_URL: lambda r: self._usage(r, "openai-codex"),
        }
        if url.endswith("/oauth/revoke"):
            token = parse_qs(request.content.decode()).get("token", [""])[0]
            self.revoked.add(token)
            return httpx.Response(200, json={})
        route = routes.get(url)
        if route is None:
            return httpx.Response(404, json={"error": "not found"})
        return route(request)

    def _tokens(self, provider: str) -> dict[str, Any]:
        n = next(self._seq)
        if provider == "openai-codex":
            a = self.openai_account
            access = fake_jwt(
                {
                    "https://api.openai.com/auth": {
                        "chatgpt_account_id": a.account_id,
                        "chatgpt_plan_type": a.org_name,
                    },
                    "https://api.openai.com/profile": {"email": a.email},
                    "n": f"PLAINTEXT-access-{n}",
                }
            )
        else:
            access = f"sk-ant-oat01-PLAINTEXT-access-{n}"
        refresh = f"PLAINTEXT-refresh-{provider}-{n}"
        self.refresh_tokens[refresh] = access
        body: dict[str, Any] = {
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": self.expires_in,
            "token_type": "Bearer",
        }
        if provider == "openai-codex":
            body["id_token"] = fake_jwt({"email": self.openai_account.email})
        elif self.anthropic_identity_in_token:
            a = self.anthropic_account
            body["account"] = {"uuid": a.account_id, "email_address": a.email}
            body["organization"] = {"uuid": a.org_id, "name": a.org_name}
        return body

    def _exchange(self, params: dict[str, str], provider: str) -> httpx.Response:
        if params.get("grant_type") == "refresh_token":
            token = params.get("refresh_token", "")
            if self.refresh_error is not None:
                return httpx.Response(self.refresh_error, json={"error": "server_error"})
            if token not in self.refresh_tokens or token in self.revoked:
                return httpx.Response(
                    400, json={"error": "invalid_grant", "error_description": "revoked"}
                )
            self.revoked.add(token)  # rotation: a refresh token works once
            return httpx.Response(200, json=self._tokens(provider))
        code, verifier = params.get("code", ""), params.get("code_verifier", "")
        if code.startswith("device-"):
            expected = verifier == f"verifier-{code}"
        else:
            challenge = self.challenges.pop(code, None)
            digest = hashlib.sha256(verifier.encode()).digest()
            expected = challenge == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        if not expected:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(200, json=self._tokens(provider))

    def _anthropic_token(self, request: httpx.Request) -> httpx.Response:
        return self._exchange(json.loads(request.content), "anthropic")

    def _openai_token(self, request: httpx.Request) -> httpx.Response:
        params = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        return self._exchange(params, "openai-codex")

    def _anthropic_bootstrap(self, request: httpx.Request) -> httpx.Response:
        a = self.anthropic_account
        return httpx.Response(
            200,
            json={
                "oauth_account": {
                    "account_uuid": a.account_id,
                    "account_email": a.email,
                    "organization_uuid": a.org_id,
                    "organization_name": a.org_name,
                }
            },
        )

    def _device_code(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "device_auth_id": "PLAINTEXT-device-id",
                "user_code": "ABCD-1234",
                "interval": "1",
            },
        )

    def _device_poll(self, request: httpx.Request) -> httpx.Response:
        if not self.device_approved:
            return httpx.Response(403, json={"error": "authorization_pending"})
        code = f"device-{next(self._seq)}"
        return httpx.Response(
            200, json={"authorization_code": code, "code_verifier": f"verifier-{code}"}
        )

    def _usage(self, request: httpx.Request, provider: str) -> httpx.Response:
        if provider not in self.usage:
            return httpx.Response(404, json={"error": "no usage"})
        return httpx.Response(200, json=self.usage[provider])


# --- Recorded-exchange replay ------------------------------------------------------------------


def _subset(expected: Any, actual: Any, path: str = "$") -> list[str]:
    """Mismatches of ``expected`` inside ``actual`` (dict keys are a subset, lists match fully)."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{path}: expected an object"]
        problems = []
        for key, value in expected.items():
            if key not in actual:
                problems.append(f"{path}.{key}: missing")
            else:
                problems += _subset(value, actual[key], f"{path}.{key}")
        return problems
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return [f"{path}: expected {len(expected)} items"]
        return [
            p
            for i, (e, a) in enumerate(zip(expected, actual, strict=True))
            for p in _subset(e, a, f"{path}[{i}]")
        ]
    return [] if expected == actual else [f"{path}: expected {expected!r}, got {actual!r}"]


@dataclass
class Replay:
    """Serves the exchanges of a fixture file in order and checks each request against it.

    A fixture is ``{"exchanges": [{"request": {"method", "url", "headers", "json"}, "response":
    {"status", "headers", "sse": [{"event", "data"}] | "json": {...}}}]}``. Expected headers
    match case-insensitively; ``Bearer {access}`` accepts any bearer token. ``json`` is a subset
    of the request body. Requests to other URLs go to ``fallback`` (for example a
    :class:`FakeProviders` serving the token endpoint during a refresh).
    """

    fixture: dict[str, Any]
    fallback: Callable[[httpx.Request], httpx.Response] | None = None
    served: list[httpx.Request] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Any, **kw: Any) -> Replay:
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f), **kw)

    @property
    def urls(self) -> set[str]:
        return {e["request"]["url"] for e in self.fixture["exchanges"]}

    @property
    def remaining(self) -> int:
        return len(self.fixture["exchanges"]) - len(self.served)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    def http_factory(self) -> Callable[[], httpx.AsyncClient]:
        return self.client

    def handle(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) not in self.urls and self.fallback is not None:
            return self.fallback(request)
        exchanges = self.fixture["exchanges"]
        if len(self.served) >= len(exchanges):
            self.problems.append(f"unexpected extra request to {request.url}")
            return httpx.Response(599, json={"error": "replay exhausted"})
        exchange = exchanges[len(self.served)]
        self.served.append(request)
        self._check(exchange["request"], request)
        return self._response(exchange["response"])

    def _check(self, expected: dict[str, Any], request: httpx.Request) -> None:
        n = len(self.served)
        if request.method != expected.get("method", "POST") or str(request.url) != expected["url"]:
            self.problems.append(f"#{n}: {request.method} {request.url} != {expected['url']}")
        for key, value in expected.get("headers", {}).items():
            actual = request.headers.get(key)
            ok = (
                actual is not None and actual.startswith("Bearer ") and len(actual) > 7
                if value == "Bearer {access}"
                else actual == value
            )
            if not ok:
                self.problems.append(f"#{n}: header {key}: expected {value!r}, got {actual!r}")
        if "json" in expected:
            try:
                body = json.loads(request.content)
            except ValueError:
                self.problems.append(f"#{n}: body is not JSON")
                return
            self.problems += [f"#{n}: {p}" for p in _subset(expected["json"], body)]

    @staticmethod
    def _response(spec: dict[str, Any]) -> httpx.Response:
        headers = spec.get("headers", {})
        if "sse" in spec:
            text = "".join(
                f"event: {e['event']}\ndata: {json.dumps(e['data'])}\n\n" for e in spec["sse"]
            )
            return httpx.Response(spec["status"], headers=headers, content=text.encode())
        return httpx.Response(spec["status"], headers=headers, json=spec.get("json"))
