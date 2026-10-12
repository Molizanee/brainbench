import os
import uuid
from datetime import UTC, datetime

import pytest

from brainbench_providers.errors import VaultDecryptError
from brainbench_providers.vault import (
    Credential,
    Identity,
    StoredCredential,
    VaultCipher,
    identity_key,
    open_credential,
    seal_tokens,
)

KEY = os.urandom(32)
ACCESS = "sk-ant-oat01-access-PLAINTEXT"
REFRESH = "sk-ant-ort01-refresh-PLAINTEXT"


def test_round_trip_and_ciphertext_hides_plaintext() -> None:
    cipher = VaultCipher(KEY)
    blob = cipher.encrypt(ACCESS, provider="anthropic", identity_key="email:a", column="access")
    assert ACCESS.encode() not in blob
    assert cipher.decrypt(blob, provider="anthropic", identity_key="email:a", column="access") == (
        ACCESS
    )
    again = cipher.encrypt(ACCESS, provider="anthropic", identity_key="email:a", column="access")
    assert again != blob  # fresh nonce per value


@pytest.mark.parametrize(
    "context",
    [
        {"provider": "openai-codex", "identity_key": "email:a", "column": "access"},
        {"provider": "anthropic", "identity_key": "email:b", "column": "access"},
        {"provider": "anthropic", "identity_key": "email:a", "column": "refresh"},
    ],
)
def test_ciphertext_is_bound_to_row_and_column(context: dict[str, str]) -> None:
    cipher = VaultCipher(KEY)
    blob = cipher.encrypt(ACCESS, provider="anthropic", identity_key="email:a", column="access")
    with pytest.raises(VaultDecryptError):
        cipher.decrypt(blob, **context)


def test_wrong_key_does_not_decrypt() -> None:
    blob = VaultCipher(KEY).encrypt(ACCESS, provider="p", identity_key="k", column="access")
    with pytest.raises(VaultDecryptError):
        VaultCipher(os.urandom(32)).decrypt(blob, provider="p", identity_key="k", column="access")
    with pytest.raises(VaultDecryptError):
        VaultCipher(KEY).decrypt(b"garbage", provider="p", identity_key="k", column="access")


def test_key_must_be_32_bytes() -> None:
    with pytest.raises(ValueError):
        VaultCipher(b"short")


def test_identity_key_is_email_and_org_for_org_scoped_providers() -> None:
    identity = Identity(email="Dev@Example.com", org_id="org-1", org_name="Personal")
    assert identity_key(identity, org_scoped=True) == "email:dev@example.com|org:org-1"
    assert identity_key(identity, org_scoped=False) == "email:dev@example.com"
    assert identity_key(Identity(account_id="acct"), org_scoped=True) == "account:acct"
    with pytest.raises(ValueError):
        identity_key(Identity(org_id="org-1"), org_scoped=True)


def test_reprs_never_include_tokens() -> None:
    cipher = VaultCipher(KEY)
    sealed = seal_tokens(
        cipher,
        provider="anthropic",
        identity_key="email:a",
        access_token=ACCESS,
        refresh_token=REFRESH,
    )
    stored = StoredCredential(
        id=uuid.uuid4(),
        provider="anthropic",
        identity_key="email:a",
        identity={"email": "a"},
        access_enc=sealed.access_enc,
        refresh_enc=sealed.refresh_enc,
        expires_at=datetime.now(UTC),
        version=1,
    )
    credential = open_credential(cipher, stored)
    assert isinstance(credential, Credential)
    assert credential.access_token == ACCESS and credential.refresh_token == REFRESH
    for text in (repr(credential), repr(stored), repr(sealed), repr(cipher)):
        assert ACCESS not in text and REFRESH not in text
        assert "access_enc" not in text
