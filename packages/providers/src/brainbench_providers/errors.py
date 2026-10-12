"""Provider-layer errors. None of them ever carries a token in its message."""

from datetime import datetime


class ProviderError(Exception):
    """Base class. ``code`` is a stable machine-readable slug for the API layer."""

    code: str = "provider-error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class RegistryError(ProviderError):
    """A registry file does not follow the grammar (design D2)."""

    code = "registry-invalid"


class UnknownProvider(ProviderError):
    code = "unknown-provider"


class ProviderDisabled(ProviderError):
    code = "provider-disabled"


class VaultLocked(ProviderError):
    """The vault key is missing or does not match the recorded fingerprint."""

    code = "vault-locked"

    def __init__(self, key_path: str, state: str) -> None:
        super().__init__(
            f"The credential vault is locked ({state}): the key file {key_path} is missing or "
            "does not match the fingerprint recorded when credentials were first stored. Restore "
            "the key file from backup, or reset the vault on the providers page."
        )
        self.key_path = key_path
        self.state = state


class VaultDecryptError(ProviderError):
    code = "vault-decrypt-failed"


class LoginNotFound(ProviderError):
    code = "login-not-found"


class LoginExpired(ProviderError):
    code = "login-expired"


class LoginAlreadyFinished(ProviderError):
    code = "login-finished"


class StateMismatch(ProviderError):
    """A callback or pasted redirect carried a state that is not the pending login's."""

    code = "state-mismatch"


class OAuthError(ProviderError):
    """The provider rejected an authorization, token, or identity request."""

    code = "oauth-failed"

    def __init__(
        self, message: str, *, status: int | None = None, error: str | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.error = error


class RefreshFailed(ProviderError):
    """A token refresh failed. ``definitive`` means the grant is revoked or invalid."""

    code = "refresh-failed"

    def __init__(self, message: str, *, definitive: bool) -> None:
        super().__init__(message)
        self.definitive = definitive


class AuthenticationFailed(ProviderError):
    """The provider rejected a token that was just refreshed; the account needs a new login."""

    code = "needs-reauthentication"


class NoCredential(ProviderError):
    """No connected, enabled, unlocked account exists for the provider."""

    code = "no-provider"


class ProviderRateLimited(ProviderError):
    """The provider answered with a rate or usage limit.

    ``reset_at`` is when the limit lifts (None when the provider gave no hint); ``scope`` names
    the limited window or bucket (for example ``5h``, ``7d``, ``usage-limit``, ``all-accounts``).
    """

    code = "provider-rate-limited"

    def __init__(self, reset_at: datetime | None, scope: str, message: str | None = None) -> None:
        super().__init__(message or f"provider limit reached ({scope})")
        self.reset_at = reset_at
        self.scope = scope


class TransportError(ProviderError):
    """A non-retryable wire failure (bad request, malformed stream, server error)."""

    code = "transport-error"

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status
