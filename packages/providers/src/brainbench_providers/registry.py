"""Declarative provider registry (design D2, part 1).

Each provider is one YAML file under ``registry/`` (shipped) or an extra file passed to
:func:`load_registry` (for example ``$BRAINBENCH_DATA/models.yaml``, which may hold one provider
mapping or a list of them). The grammar is the Pydantic model :class:`ProviderSpec`; unknown keys
are rejected so a typo cannot silently drop a parameter.

The registry is the single source for the providers page, login dispatch, and model selection.
"""

import base64
from collections.abc import Iterable, Sequence
from importlib import resources
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from brainbench_providers.errors import ProviderDisabled, RegistryError, UnknownProvider

WireApi = Literal["anthropic-messages", "openai-responses"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceRef(_Strict):
    """Where the OAuth parameters were transcribed from."""

    repo: str
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    files: list[str] = Field(min_length=1)
    notes: str | None = None


class _LoginBase(_Strict):
    client_id: str
    client_id_encoding: Literal["plain", "base64"] = "plain"
    token_url: str
    token_body: Literal["json", "form"] = "form"
    token_params: dict[str, str] = {}
    timeout_s: float = 30.0

    @property
    def resolved_client_id(self) -> str:
        if self.client_id_encoding == "base64":
            return base64.b64decode(self.client_id).decode()
        return self.client_id


class OAuthCodeLogin(_LoginBase):
    """Authorization code with PKCE and state; paste-redirect plus optional loopback callback."""

    kind: Literal["oauth-code"]
    authorize_url: str
    scopes: list[str] = Field(min_length=1)
    pkce: bool = True
    authorize_params: dict[str, str] = {}
    callback_port: int = Field(ge=1, le=65535)
    callback_path: str = Field(pattern=r"^/")
    redirect_uri: str
    paste_code: bool = True
    instructions: str | None = None


class DeviceCodeLogin(_LoginBase):
    """Device authorization: show a user code, poll until approval, exchange the code."""

    kind: Literal["device-code"]
    # `openai-deviceauth`: the ChatGPT device endpoints (user code, then a poll that returns an
    # authorization code and verifier which are exchanged at `token_url`).
    style: Literal["openai-deviceauth"]
    device_url: str
    device_token_url: str
    verification_url: str
    redirect_uri: str
    interval_s: float = Field(default=5.0, gt=0)
    poll_margin_s: float = Field(default=0.0, ge=0)
    instructions: str | None = None


Login = Annotated[OAuthCodeLogin | DeviceCodeLogin, Field(discriminator="kind")]


class RefreshSpec(_Strict):
    token_url: str
    token_body: Literal["json", "form"] = "form"
    skew_s: int = Field(default=300, ge=0)
    headers: dict[str, str] = {}
    timeout_s: float = 30.0


class IdentitySpec(_Strict):
    """How an account's identity is read after the token exchange.

    ``anthropic-token``: ``account``/``organization`` objects in the token response, with the
    Claude CLI bootstrap endpoint (``userinfo_url``) as fallback. ``openai-jwt``: ChatGPT claims
    in the access token and id token.
    """

    org_scoped: bool
    source: Literal["anthropic-token", "openai-jwt"]
    userinfo_url: str | None = None
    userinfo_headers: dict[str, str] = {}


class UsageSpec(_Strict):
    endpoint: str
    format: Literal["anthropic-oauth-usage", "codex-wham-usage"]
    windows: list[str] = Field(min_length=1)
    headers: dict[str, str] = {}


class BillingHeaderSpec(_Strict):
    """Claude Code's ``x-anthropic-billing-header`` system block and its ``cch`` attestation."""

    version: str
    salt: str
    cch_seed: int


class TransportSpec(_Strict):
    path: str = Field(pattern=r"^/")
    headers: dict[str, str] = {}
    user_agent: str | None = None
    betas: list[str] = []
    system_preamble: str | None = None
    billing_header: BillingHeaderSpec | None = None
    tool_prefix: str = ""
    client_version: str | None = None
    originator: str | None = None
    timeout_s: float = 600.0


class ModelSpec(_Strict):
    id: str
    name: str
    context_window: int = Field(gt=0)
    max_output: int = Field(gt=0)
    reasoning: bool = False
    default: bool = False


class ProviderSpec(_Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    api: WireApi
    base_url: str
    source: SourceRef
    policy_notice: str | None = None
    login: list[Login] = Field(min_length=1)
    refresh: RefreshSpec
    identity: IdentitySpec
    usage: UsageSpec | None = None
    revoke_url: str | None = None
    transport: TransportSpec
    models: list[ModelSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> ProviderSpec:
        defaults = [m.id for m in self.models if m.default]
        if len(defaults) != 1:
            raise ValueError(f"exactly one model must be the default, found {len(defaults)}")
        ids = [m.id for m in self.models]
        if len(set(ids)) != len(ids):
            raise ValueError("model ids must be unique")
        kinds = [login.kind for login in self.login]
        if len(set(kinds)) != len(kinds):
            raise ValueError("at most one login of each kind")
        return self

    @property
    def default_model(self) -> ModelSpec:
        return next(m for m in self.models if m.default)

    def model(self, model_id: str) -> ModelSpec:
        for m in self.models:
            if m.id == model_id:
                return m
        raise UnknownProvider(f"Provider {self.id!r} has no model {model_id!r}")

    def login_for(self, kind: str | None = None) -> OAuthCodeLogin | DeviceCodeLogin:
        """The login of ``kind``, or the provider's primary (first) login when ``kind`` is None."""
        if kind is None:
            return self.login[0]
        for login in self.login:
            if login.kind == kind:
                return login
        raise UnknownProvider(f"Provider {self.id!r} has no {kind} login")

    @property
    def login_kinds(self) -> list[str]:
        return [login.kind for login in self.login]


class Registry:
    """Ordered collection of providers; order is the shipped order then extra files."""

    def __init__(self, providers: Iterable[ProviderSpec]) -> None:
        self._providers: dict[str, ProviderSpec] = {}
        for spec in providers:
            if spec.id in self._providers:
                raise RegistryError(f"Duplicate provider id {spec.id!r}")
            self._providers[spec.id] = spec

    @property
    def providers(self) -> list[ProviderSpec]:
        return list(self._providers.values())

    @property
    def ids(self) -> list[str]:
        return list(self._providers)

    def get(self, provider_id: str) -> ProviderSpec:
        try:
            return self._providers[provider_id]
        except KeyError:
            raise UnknownProvider(f"Unknown provider {provider_id!r}") from None

    def enabled(self, enabled_ids: Sequence[str] | None) -> list[ProviderSpec]:
        """Providers enabled by ``settings.enabled_providers`` (None means all of them)."""
        if enabled_ids is None:
            return self.providers
        wanted = set(enabled_ids)
        return [spec for spec in self._providers.values() if spec.id in wanted]

    def is_enabled(self, provider_id: str, enabled_ids: Sequence[str] | None) -> bool:
        self.get(provider_id)
        return enabled_ids is None or provider_id in enabled_ids

    def require_enabled(self, provider_id: str, enabled_ids: Sequence[str] | None) -> ProviderSpec:
        spec = self.get(provider_id)
        if not self.is_enabled(provider_id, enabled_ids):
            raise ProviderDisabled(f"Provider {spec.name} is disabled in settings")
        return spec


def parse_provider(data: Any, *, origin: str = "<memory>") -> ProviderSpec:
    try:
        return ProviderSpec.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        )
        raise RegistryError(f"{origin}: {problems}") from None


def _parse_file(text: str, origin: str) -> list[ProviderSpec]:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RegistryError(f"{origin}: invalid YAML ({exc})") from None
    items = data if isinstance(data, list) else [data]
    return [parse_provider(item, origin=origin) for item in items]


def shipped_files() -> list[tuple[str, str]]:
    """``(name, text)`` of every shipped registry file, sorted by name."""
    root = resources.files("brainbench_providers") / "registry"
    entries = sorted(
        (entry for entry in root.iterdir() if entry.name.endswith(".yaml")), key=lambda e: e.name
    )
    return [(entry.name, entry.read_text(encoding="utf-8")) for entry in entries]


SHIPPED_ORDER = ("anthropic", "openai-codex")


def load_registry(extra_files: Sequence[Path] = ()) -> Registry:
    """Shipped providers (Anthropic first) plus providers from ``extra_files`` that exist."""
    specs: list[ProviderSpec] = []
    for name, text in shipped_files():
        specs.extend(_parse_file(text, f"registry/{name}"))
    specs.sort(key=lambda s: SHIPPED_ORDER.index(s.id) if s.id in SHIPPED_ORDER else len(specs))
    for path in extra_files:
        if path.is_file():
            specs.extend(_parse_file(path.read_text(encoding="utf-8"), str(path)))
    return Registry(specs)
