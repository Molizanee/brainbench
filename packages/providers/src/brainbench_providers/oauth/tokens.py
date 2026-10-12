"""Token endpoint requests and identity resolution shared by the login engines and refresher."""

import base64
import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from brainbench_providers.errors import OAuthError
from brainbench_providers.registry import IdentitySpec
from brainbench_providers.vault import Identity

log = logging.getLogger(__name__)

HttpFactory = Callable[[], httpx.AsyncClient]
Clock = Callable[[], datetime]

OPENAI_AUTH_CLAIM = "https://api.openai.com/auth"
OPENAI_PROFILE_CLAIM = "https://api.openai.com/profile"


def utcnow() -> datetime:
    return datetime.now(UTC)


def default_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(30.0), follow_redirects=False)


@dataclass(frozen=True)
class TokenSet:
    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    expires_at: datetime | None
    raw: dict[str, Any] = field(repr=False)


def _error_detail(response: httpx.Response) -> tuple[str | None, str]:
    """``(error code, short description)`` from an OAuth error body, never echoing tokens."""
    try:
        body = response.json()
    except ValueError:
        return None, f"HTTP {response.status_code}"
    if not isinstance(body, dict):
        return None, f"HTTP {response.status_code}"
    error = body.get("error")
    if isinstance(error, dict):
        code = error.get("code") or error.get("type")
        message = error.get("message")
        return (str(code) if code else None), str(message or code or response.status_code)
    description = body.get("error_description") or body.get("message")
    return (str(error) if error else None), str(description or error or response.status_code)


async def post_token_request(
    http: HttpFactory,
    url: str,
    params: Mapping[str, str],
    *,
    body: str,
    headers: Mapping[str, str] | None = None,
    timeout_s: float = 30.0,
    clock: Clock = utcnow,
) -> TokenSet:
    """POST to a token endpoint (``body`` is ``json`` or ``form``) and parse the token set."""
    request_headers = {"Accept": "application/json", **(headers or {})}
    async with http() as client:
        if body == "json":
            response = await client.post(
                url, json=dict(params), headers=request_headers, timeout=timeout_s
            )
        else:
            response = await client.post(
                url, data=dict(params), headers=request_headers, timeout=timeout_s
            )
    if response.status_code >= 400:
        code, description = _error_detail(response)
        raise OAuthError(
            f"Token endpoint answered {response.status_code}: {description}",
            status=response.status_code,
            error=code,
        )
    try:
        data = response.json()
    except ValueError:
        raise OAuthError("Token endpoint returned a non-JSON body") from None
    access = data.get("access_token") if isinstance(data, dict) else None
    if not isinstance(access, str) or not access:
        raise OAuthError("Token response has no access_token")
    refresh = data.get("refresh_token")
    expires_in = data.get("expires_in")
    expires_at = (
        clock() + timedelta(seconds=float(expires_in))
        if isinstance(expires_in, int | float)
        else None
    )
    return TokenSet(
        access, refresh if isinstance(refresh, str) and refresh else None, expires_at, data
    )


def decode_jwt_claims(token: str | None) -> dict[str, Any]:
    """Payload of a JWT without verifying it (the token came straight from the issuer)."""
    if not token:
        return {}
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except ValueError:
        return {}
    return claims if isinstance(claims, dict) else {}


def _str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _nested(data: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def openai_identity(access_token: str, id_token: str | None) -> Identity:
    """ChatGPT workspace and email from the access-token and id-token claims (omp's
    ``getTokenProfile``). The workspace is the account's organization."""
    access, ident = decode_jwt_claims(access_token), decode_jwt_claims(id_token)
    account_id = _str(_nested(access, OPENAI_AUTH_CLAIM, "chatgpt_account_id")) or _str(
        _nested(ident, OPENAI_AUTH_CLAIM, "chatgpt_account_id")
    )
    email = (
        _str(_nested(access, OPENAI_PROFILE_CLAIM, "email"))
        or _str(_nested(ident, OPENAI_PROFILE_CLAIM, "email"))
        or _str(ident.get("email"))
    )
    plan = _str(_nested(access, OPENAI_AUTH_CLAIM, "chatgpt_plan_type")) or _str(
        _nested(ident, OPENAI_AUTH_CLAIM, "chatgpt_plan_type")
    )
    return Identity(
        email=email.lower() if email else None,
        account_id=account_id,
        org_id=account_id,
        org_name=plan.lower() if plan else None,
        plan=plan.lower() if plan else None,
    )


async def anthropic_identity(http: HttpFactory, spec: IdentitySpec, tokens: TokenSet) -> Identity:
    """``account``/``organization`` from the token response, falling back to the Claude CLI
    bootstrap endpoint when a field is missing (omp's ``anthropic-identity`` hook)."""
    raw = tokens.raw
    identity = Identity(
        email=_str(_nested(raw, "account", "email_address")),
        account_id=_str(_nested(raw, "account", "uuid")),
        org_id=_str(_nested(raw, "organization", "uuid")),
        org_name=_str(_nested(raw, "organization", "name")),
    )
    if (identity.email and identity.account_id and identity.org_id) or not spec.userinfo_url:
        return identity
    headers = {**spec.userinfo_headers, "Authorization": f"Bearer {tokens.access_token}"}
    try:
        async with http() as client:
            response = await client.get(spec.userinfo_url, headers=headers, timeout=30.0)
        response.raise_for_status()
        account = _nested(response.json(), "oauth_account") or {}
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("anthropic identity bootstrap failed: %s", type(exc).__name__)
        return identity
    return Identity(
        email=identity.email or _str(account.get("account_email")),
        account_id=identity.account_id or _str(account.get("account_uuid")),
        org_id=identity.org_id or _str(account.get("organization_uuid")),
        org_name=identity.org_name or _str(account.get("organization_name")),
    )


async def resolve_identity(http: HttpFactory, spec: IdentitySpec, tokens: TokenSet) -> Identity:
    if spec.source == "openai-jwt":
        id_token = tokens.raw.get("id_token")
        identity = openai_identity(
            tokens.access_token, id_token if isinstance(id_token, str) else None
        )
    else:
        identity = await anthropic_identity(http, spec, tokens)
    if not identity.email and not identity.account_id:
        raise OAuthError("Could not read the account identity from the provider's answer")
    return identity
