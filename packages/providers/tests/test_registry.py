import copy
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

from brainbench_providers.errors import ProviderDisabled, RegistryError, UnknownProvider
from brainbench_providers.registry import (
    DeviceCodeLogin,
    OAuthCodeLogin,
    load_registry,
    parse_provider,
    shipped_files,
)

SOURCE_COMMIT = "b83200eb866821f02625332025b453f06ce4e12a"


def shipped(provider_id: str) -> dict[str, Any]:
    for name, text in shipped_files():
        if name == f"{provider_id}.yaml":
            data: dict[str, Any] = yaml.safe_load(text)
            return data
    raise AssertionError(provider_id)


def test_shipped_entries_load_in_order() -> None:
    registry = load_registry()
    assert registry.ids == ["anthropic", "openai-codex"]


def test_anthropic_entry_matches_omp() -> None:
    spec = load_registry().get("anthropic")
    assert spec.api == "anthropic-messages"
    assert spec.source.commit == SOURCE_COMMIT
    login = spec.login_for()
    assert isinstance(login, OAuthCodeLogin)
    assert login.resolved_client_id == "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    assert login.callback_port == 54545
    assert login.redirect_uri == "http://localhost:54545/callback"
    assert login.paste_code and login.pkce
    assert "user:inference" in login.scopes
    assert login.token_body == "json"
    assert spec.identity.org_scoped
    assert spec.default_model.id == "claude-opus-5-5"
    assert spec.login_kinds == ["oauth-code"]


def test_codex_entry_has_code_and_device_logins() -> None:
    spec = load_registry().get("openai-codex")
    assert spec.api == "openai-responses"
    assert spec.base_url == "https://chatgpt.com/backend-api"
    assert spec.login_kinds == ["oauth-code", "device-code"]
    code = spec.login_for("oauth-code")
    assert isinstance(code, OAuthCodeLogin)
    assert code.callback_port == 1455
    assert code.client_id == "app_EMoamEEZ73f0CkXaXp7hrann"
    device = spec.login_for("device-code")
    assert isinstance(device, DeviceCodeLogin)
    assert device.verification_url == "https://auth.openai.com/codex/device"
    assert spec.source.commit == SOURCE_COMMIT


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: d.pop("api"), "api"),
        (lambda d: d.update(api="grpc"), "api"),
        (lambda d: d.update(surprise=True), "surprise"),
        (lambda d: d["login"][0].update(kind="password"), "login"),
        (lambda d: d["login"][0].update(callback_port=70000), "callback_port"),
        (lambda d: d["login"][0].pop("authorize_url"), "authorize_url"),
        (lambda d: d["models"][0].update(default=False), "default"),
        (lambda d: d["models"].append(dict(d["models"][1], default=True)), "default"),
        (lambda d: d["models"].append(dict(d["models"][1])), "unique"),
        (lambda d: d.update(id="Bad Id"), "id"),
        (lambda d: d["source"].update(commit="main"), "commit"),
        (lambda d: d.update(models=[]), "models"),
    ],
)
def test_grammar_rejects_invalid_entries(mutate: Any, message: str) -> None:
    data = copy.deepcopy(shipped("anthropic"))
    mutate(data)
    with pytest.raises(RegistryError, match=message):
        parse_provider(data, origin="test.yaml")


def test_extra_file_adds_providers_and_rejects_duplicates(tmp_path: Path) -> None:
    custom = copy.deepcopy(shipped("anthropic"))
    custom["id"] = "anthropic-work"
    extra = tmp_path / "models.yaml"
    extra.write_text(yaml.safe_dump([custom]))
    assert load_registry([extra, tmp_path / "missing.yaml"]).ids == [
        "anthropic",
        "openai-codex",
        "anthropic-work",
    ]
    extra.write_text(yaml.safe_dump(shipped("anthropic")))
    with pytest.raises(RegistryError, match="Duplicate"):
        load_registry([extra])


def test_invalid_yaml_is_reported_with_origin(tmp_path: Path) -> None:
    extra = tmp_path / "models.yaml"
    extra.write_text("id: [unclosed")
    with pytest.raises(RegistryError, match=str(extra)):
        load_registry([extra])


def test_disabled_providers_are_filtered() -> None:
    registry = load_registry()
    assert [p.id for p in registry.enabled(None)] == ["anthropic", "openai-codex"]
    assert [p.id for p in registry.enabled(["openai-codex"])] == ["openai-codex"]
    assert registry.enabled([]) == []
    assert registry.is_enabled("anthropic", None)
    assert not registry.is_enabled("anthropic", ["openai-codex"])
    with pytest.raises(ProviderDisabled):
        registry.require_enabled("anthropic", ["openai-codex"])
    with pytest.raises(UnknownProvider):
        registry.get("gemini")


def test_model_lookup() -> None:
    spec = load_registry().get("anthropic")
    assert spec.model("claude-sonnet-5-5").context_window == 1_000_000
    with pytest.raises(UnknownProvider):
        spec.model("gpt-6")
