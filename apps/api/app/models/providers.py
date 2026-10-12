"""Provider API contracts. No model here has a token field: tokens never leave the server."""

from typing import Literal

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
