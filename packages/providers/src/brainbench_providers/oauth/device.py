"""Device-code login (``openai-deviceauth`` style, omp's ``loginOpenAICodexDevice``).

The user code is shown with the verification URL; the poll endpoint answers 403/404 while the
user has not approved, then returns an authorization code and verifier that are exchanged at the
token endpoint like a code login.
"""

from dataclasses import dataclass, field

from brainbench_providers.errors import OAuthError
from brainbench_providers.oauth.tokens import Clock, HttpFactory, TokenSet, post_token_request
from brainbench_providers.registry import DeviceCodeLogin

PENDING_STATUSES = frozenset({403, 404})


@dataclass(frozen=True)
class DeviceAuthorization:
    device_auth_id: str = field(repr=False)
    user_code: str
    interval_s: float


async def request_user_code(http: HttpFactory, login: DeviceCodeLogin) -> DeviceAuthorization:
    async with http() as client:
        response = await client.post(
            login.device_url, json={"client_id": login.resolved_client_id}, timeout=login.timeout_s
        )
    if response.status_code >= 400:
        raise OAuthError(
            f"Device authorization failed with HTTP {response.status_code}",
            status=response.status_code,
        )
    data = response.json()
    device_auth_id, user_code = data.get("device_auth_id"), data.get("user_code")
    if not isinstance(device_auth_id, str) or not isinstance(user_code, str):
        raise OAuthError("Device authorization response is missing the device id or user code")
    try:
        interval = float(data.get("interval") or login.interval_s)
    except TypeError, ValueError:
        interval = login.interval_s
    return DeviceAuthorization(device_auth_id, user_code, interval + login.poll_margin_s)


async def poll_once(
    http: HttpFactory, login: DeviceCodeLogin, device: DeviceAuthorization, *, clock: Clock
) -> TokenSet | None:
    """One poll: None while approval is pending, the token set once approved."""
    async with http() as client:
        response = await client.post(
            login.device_token_url,
            json={"device_auth_id": device.device_auth_id, "user_code": device.user_code},
            timeout=login.timeout_s,
        )
    if response.status_code in PENDING_STATUSES:
        return None
    if response.status_code >= 400:
        raise OAuthError(
            f"Device authorization polling failed with HTTP {response.status_code}",
            status=response.status_code,
        )
    data = response.json()
    code, verifier = data.get("authorization_code"), data.get("code_verifier")
    if not isinstance(code, str) or not isinstance(verifier, str):
        raise OAuthError("Device approval is missing the authorization code or verifier")
    return await post_token_request(
        http,
        login.token_url,
        {
            "grant_type": "authorization_code",
            "client_id": login.resolved_client_id,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": login.redirect_uri,
        },
        body=login.token_body,
        timeout_s=login.timeout_s,
        clock=clock,
    )
