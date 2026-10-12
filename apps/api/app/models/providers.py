"""Provider API contracts. No model here has a token field: tokens never leave the server."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

AccountStatus = Literal["connected", "needs-reauthentication", "locked", "blocked", "disabled"]
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
