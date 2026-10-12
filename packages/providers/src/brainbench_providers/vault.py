"""Credential vault (design D2, part 2).

Token columns are AES-256-GCM ciphertext under the instance key (32 raw bytes in
``$BRAINBENCH_DATA/vault.key``). Each ciphertext is bound by its associated data to the
provider, the identity key, and the column, so a value copied to another row or column fails to
decrypt. Plaintext tokens exist only inside :class:`Credential`, whose ``repr`` omits them.
"""

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from brainbench_providers.errors import VaultDecryptError

KEY_BYTES = 32
NONCE_BYTES = 12
FORMAT_V1 = b"\x01"


class VaultCipher:
    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise ValueError("vault key must be 32 bytes")
        self._aead = AESGCM(key)

    def __repr__(self) -> str:
        return "VaultCipher(<key hidden>)"

    @staticmethod
    def _aad(provider: str, identity_key: str, column: str) -> bytes:
        return f"brainbench-vault|{provider}|{identity_key}|{column}".encode()

    def encrypt(self, plaintext: str, *, provider: str, identity_key: str, column: str) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        sealed = self._aead.encrypt(
            nonce, plaintext.encode(), self._aad(provider, identity_key, column)
        )
        return FORMAT_V1 + nonce + sealed

    def decrypt(self, blob: bytes, *, provider: str, identity_key: str, column: str) -> str:
        if not blob.startswith(FORMAT_V1) or len(blob) < 1 + NONCE_BYTES + 16:
            raise VaultDecryptError("Stored credential has an unknown format")
        nonce, sealed = blob[1 : 1 + NONCE_BYTES], blob[1 + NONCE_BYTES :]
        try:
            plain = self._aead.decrypt(nonce, sealed, self._aad(provider, identity_key, column))
        except InvalidTag:
            raise VaultDecryptError(
                "Stored credential does not decrypt with the vault key"
            ) from None
        return plain.decode()


@dataclass(frozen=True)
class Identity:
    """Who an account is; ``org_id`` is the workspace the token draws limits from."""

    email: str | None = None
    account_id: str | None = None
    org_id: str | None = None
    org_name: str | None = None
    plan: str | None = None

    def as_dict(self) -> dict[str, str]:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Identity:
        return cls(
            **{k: data.get(k) for k in ("email", "account_id", "org_id", "org_name", "plan")}
        )


def identity_key(identity: Identity, *, org_scoped: bool) -> str:
    """omp's account key: ``email:<e>|org:<id>`` for org-scoped providers, else ``email:<e>``.

    Each organization or workspace of the same email is its own account. Without an email the
    account id stands in for it.
    """
    principal = (
        f"email:{identity.email.strip().lower()}"
        if identity.email
        else f"account:{identity.account_id}"
        if identity.account_id
        else None
    )
    if principal is None:
        raise ValueError("an identity needs an email or an account id")
    if org_scoped and identity.org_id:
        return f"{principal}|org:{identity.org_id}"
    return principal


@dataclass(frozen=True)
class StoredCredential:
    """A vault row as persisted: token columns are ciphertext."""

    id: UUID
    provider: str
    identity_key: str
    identity: dict[str, Any]
    access_enc: bytes | None = field(repr=False)
    refresh_enc: bytes | None = field(repr=False)
    expires_at: datetime | None
    version: int
    disabled_at: datetime | None = None
    disabled_cause: str | None = None
    priority: int = 0
    created_at: datetime | None = None

    @property
    def disabled(self) -> bool:
        return self.disabled_at is not None


@dataclass(frozen=True)
class Credential:
    """A decrypted credential, held in memory only for the duration of a request or refresh."""

    id: UUID
    provider: str
    identity_key: str
    identity: Identity
    access_token: str = field(repr=False)
    refresh_token: str | None = field(repr=False)
    expires_at: datetime | None
    version: int


def open_credential(cipher: VaultCipher, stored: StoredCredential) -> Credential:
    if stored.access_enc is None:
        raise VaultDecryptError("Stored credential has no access token")
    ctx = {"provider": stored.provider, "identity_key": stored.identity_key}
    return Credential(
        id=stored.id,
        provider=stored.provider,
        identity_key=stored.identity_key,
        identity=Identity.from_dict(stored.identity),
        access_token=cipher.decrypt(stored.access_enc, column="access", **ctx),
        refresh_token=(
            cipher.decrypt(stored.refresh_enc, column="refresh", **ctx)
            if stored.refresh_enc is not None
            else None
        ),
        expires_at=stored.expires_at,
        version=stored.version,
    )


@dataclass(frozen=True)
class SealedTokens:
    access_enc: bytes = field(repr=False)
    refresh_enc: bytes | None = field(repr=False)


def seal_tokens(
    cipher: VaultCipher,
    *,
    provider: str,
    identity_key: str,
    access_token: str,
    refresh_token: str | None,
) -> SealedTokens:
    ctx = {"provider": provider, "identity_key": identity_key}
    return SealedTokens(
        access_enc=cipher.encrypt(access_token, column="access", **ctx),
        refresh_enc=(
            cipher.encrypt(refresh_token, column="refresh", **ctx)
            if refresh_token is not None
            else None
        ),
    )
