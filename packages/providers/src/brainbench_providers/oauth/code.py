"""Authorization code with PKCE and state (RFC 7636), paste-redirect parsing, code exchange."""

import base64
import hashlib
import secrets
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode, urlsplit

from brainbench_providers.errors import OAuthError, StateMismatch
from brainbench_providers.oauth.tokens import Clock, HttpFactory, TokenSet, post_token_request
from brainbench_providers.registry import OAuthCodeLogin


@dataclass(frozen=True)
class Pkce:
    verifier: str = field(repr=False)
    challenge: str


def new_pkce() -> Pkce:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    return Pkce(verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode())


def new_state() -> str:
    return secrets.token_urlsafe(32)


def authorize_url(login: OAuthCodeLogin, *, state: str, pkce: Pkce) -> str:
    """Standard authorize parameters followed by the provider's extras, as omp builds them."""
    params = {
        "client_id": login.resolved_client_id,
        "response_type": "code",
        "redirect_uri": login.redirect_uri,
        "scope": " ".join(login.scopes),
    }
    if login.pkce:
        params |= {"code_challenge": pkce.challenge, "code_challenge_method": "S256"}
    params["state"] = state
    params |= login.authorize_params
    return f"{login.authorize_url}?{urlencode(params)}"


@dataclass(frozen=True)
class AuthorizationInput:
    code: str = field(repr=False)
    state: str | None


def _first(params: dict[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    return values[0] if values else None


def parse_authorization_input(text: str) -> AuthorizationInput:
    """Accept the failed redirect URL, a query string, ``code#state``, or a bare code."""
    value = text.strip()
    if not value:
        raise OAuthError("Paste the redirect URL or the authorization code")
    if "://" in value or value.startswith(("?", "/")) or "code=" in value:
        parts = urlsplit(value)
        query = parse_qs(parts.query if "?" in value else value.lstrip("?"))
        fragment = parse_qs(parts.fragment)
        error = _first(query, "error")
        if error:
            raise OAuthError(f"The provider refused the authorization: {error}")
        code = _first(query, "code") or _first(fragment, "code")
        if not code:
            raise OAuthError("The pasted URL has no authorization code")
        return AuthorizationInput(code, _first(query, "state") or _first(fragment, "state"))
    if "#" in value:
        code, _, state = value.partition("#")
        return AuthorizationInput(code, state or None)
    return AuthorizationInput(value, None)


def verify_state(expected: str, received: str | None) -> None:
    """A carried state must equal the pending login's; a bare code carries none (PKCE still
    binds it to this login's verifier)."""
    if received is not None and not secrets.compare_digest(expected, received):
        raise StateMismatch("The redirect's state does not match this sign-in; start again")


async def exchange_code(
    http: HttpFactory,
    login: OAuthCodeLogin,
    *,
    code: str,
    state: str,
    pkce: Pkce,
    redirect_uri: str | None = None,
    clock: Clock,
) -> TokenSet:
    params = {
        "grant_type": "authorization_code",
        "client_id": login.resolved_client_id,
        "code": code,
        "redirect_uri": redirect_uri or login.redirect_uri,
    }
    if login.pkce:
        params["code_verifier"] = pkce.verifier
    params |= {k: v.replace("{state}", state) for k, v in login.token_params.items()}
    return await post_token_request(
        http, login.token_url, params, body=login.token_body, timeout_s=login.timeout_s, clock=clock
    )
