"""Provider API contracts. No model here has a token field: tokens never leave the server."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

AccountStatus = Literal["connected", "needs-reauthentication", "locked", "blocked"]
LoginKind = Literal["oauth-code", "device-code"]
LoginStatus = Literal["pending", "connected", "failed", "expired"]


class VaultRead(BaseModel):
    state: Literal["ok", "empty", "locked"]
    detail: str
    key_path: str


class VaultReset(BaseModel):
    confirm: str = Field(max_length=32, description='Must be "reset".')


class LoginStart(BaseModel):
    kind: LoginKind | None = Field(
        default=None, description="Login kind; the provider's primary login when omitted."
    )


class LoginComplete(BaseModel):
    input: str = Field(
        min_length=1,
        max_length=8192,
        description="The failed redirect URL, `code#state`, or the authorization code.",
    )


class LoginRead(BaseModel):
    login_id: str
    provider: str
    kind: LoginKind
    status: LoginStatus
    expires_at: datetime
    authorize_url: str | None
    paste_code: bool
    callback_listening: bool
    callback_port: int | None
    verification_url: str | None
    user_code: str | None
    interval_s: float | None
    credential_id: UUID | None
    error: str | None
    instructions: str | None


class ModelRead(BaseModel):
    id: str
    name: str
    context_window: int
    max_output: int
    reasoning: bool
    default: bool


class BlockRead(BaseModel):
    scope: str
    until: datetime
    reason: str | None


class WindowRead(BaseModel):
    window: str
    used_pct: float | None
    resets_at: datetime | None
    fetched_at: datetime


class UsageTodayRead(BaseModel):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class AccountRead(BaseModel):
    """One connected account: identity and expiry only, never a token."""

    id: UUID
    provider: str
    email: str | None
    account_id: str | None
    org_id: str | None
    org_name: str | None
    plan: str | None
    status: AccountStatus
    disabled_cause: str | None
    expires_at: datetime | None
    priority: int
    blocked_until: datetime | None
    blocks: list[BlockRead]
    windows: list[WindowRead]
    usage_today: UsageTodayRead
    created_at: datetime | None


class ProviderRead(BaseModel):
    id: str
    name: str
    api: str
    enabled: bool
    login_kinds: list[LoginKind]
    callback_port: int | None
    policy_notice: str | None
    models: list[ModelRead]
    accounts: list[AccountRead]


class ProvidersRead(BaseModel):
    vault: VaultRead
    callbacks_enabled: bool
    usage_since: datetime
    providers: list[ProviderRead]


class AccountUpdate(BaseModel):
    priority: int = Field(ge=-1000, le=1000)
