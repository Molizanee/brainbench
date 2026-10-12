from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from strands import Agent
from strands.types.exceptions import ModelThrottledException

from app.jobs.errors import ProviderLimitReached
from app.models.providers import ProviderSettingsUpdate, RouteEntry
from app.services.providers import (
    InvalidRouting,
    NoProvider,
    ProviderRuntime,
    VaultLockedError,
    ensure_provider_for,
    find_throttle,
    get_registry,
    model_for,
    provider_limit_from,
    provider_settings,
    update_provider_settings,
)
from brainbench_providers import TASK_CLASSES
from brainbench_providers.registry import Registry
from brainbench_providers.strands_models import (
    AnthropicOAuthModel,
    CodexOAuthModel,
    ProviderRetryStrategy,
    ProviderThrottledException,
)
from brainbench_providers.testing import FakeProviders


@pytest.fixture
def fake() -> FakeProviders:
    return FakeProviders()


@pytest.fixture
def runtime(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path, fake: FakeProviders
) -> ProviderRuntime:
    return ProviderRuntime(
        get_registry(), session_factory, tmp_path / "vault.key", http=fake.http_factory()
    )


async def connect(runtime: ProviderRuntime, fake: FakeProviders, provider: str) -> None:
    view = await runtime.start_login(provider, "oauth-code")
    await runtime.complete_login(view.login_id, fake.authorize(view.authorize_url or ""))


async def test_no_account_rejects_with_a_link_to_the_providers_page(
    session: AsyncSession, runtime: ProviderRuntime
) -> None:
    with pytest.raises(NoProvider) as info:
        await ensure_provider_for(session, "curation", runtime)
    assert info.value.code == "no-provider" and "/providers" in info.value.message
    settings = await provider_settings(session, runtime)
    assert set(settings.effective.values()) == {None}


async def test_default_routing_sends_every_task_class_to_the_connected_provider(
    session: AsyncSession, runtime: ProviderRuntime, fake: FakeProviders
) -> None:
    await connect(runtime, fake, "anthropic")
    settings = await provider_settings(session, runtime)
    for task_class in TASK_CLASSES:
        route = settings.effective[task_class]  # type: ignore[index]
        assert route is not None
        assert (route.provider, route.model, route.source) == (
            "anthropic",
            "claude-opus-5-5",
            "default",
        )
    model = await model_for(session, "storyboarding", runtime)
    assert isinstance(model, AnthropicOAuthModel)
    assert model.get_config()["task_class"] == "storyboarding"


async def test_explicit_route_to_an_unconnected_provider_is_rejected(
    session: AsyncSession, runtime: ProviderRuntime, fake: FakeProviders
) -> None:
    await connect(runtime, fake, "anthropic")
    await update_provider_settings(
        session,
        runtime,
        ProviderSettingsUpdate(
            routing={"curation": RouteEntry(provider="openai-codex", model="gpt-6-sol")}
        ),
    )
    with pytest.raises(NoProvider, match="ChatGPT"):
        await ensure_provider_for(session, "curation", runtime)
    assert (await ensure_provider_for(session, "tutoring", runtime)).spec.id == "anthropic"
    await connect(runtime, fake, "openai-codex")
    model = await model_for(session, "curation", runtime)
    assert isinstance(model, CodexOAuthModel) and model.get_config()["model_id"] == "gpt-6-sol"


async def test_invalid_routing_is_rejected(session: AsyncSession, runtime: ProviderRuntime) -> None:
    with pytest.raises(InvalidRouting, match="no model"):
        await update_provider_settings(
            session,
            runtime,
            ProviderSettingsUpdate(
                routing={"curation": RouteEntry(provider="anthropic", model="x")}
            ),
        )
    with pytest.raises(InvalidRouting, match="disabled"):
        await update_provider_settings(
            session,
            runtime,
            ProviderSettingsUpdate(
                enabled_providers=["openai-codex"],
                routing={"curation": RouteEntry(provider="anthropic", model="claude-opus-5-5")},
            ),
        )
    with pytest.raises(InvalidRouting, match="Unknown providers"):
        await update_provider_settings(
            session, runtime, ProviderSettingsUpdate(enabled_providers=["gemini"])
        )
    with pytest.raises(InvalidRouting):
        await ensure_provider_for(session, "painting", runtime)


async def test_disabled_provider_disappears_and_its_accounts_are_kept(
    session: AsyncSession, runtime: ProviderRuntime, fake: FakeProviders
) -> None:
    await connect(runtime, fake, "anthropic")
    await connect(runtime, fake, "openai-codex")
    settings = await update_provider_settings(
        session, runtime, ProviderSettingsUpdate(enabled_providers=["openai-codex"])
    )
    assert [o.provider for o in settings.options] == ["openai-codex"]
    assert settings.effective["curation"].provider == "openai-codex"  # type: ignore[union-attr]
    assert len(await runtime.store.list_credentials("anthropic")) == 1
    await update_provider_settings(session, runtime, ProviderSettingsUpdate(enabled_providers=[]))
    with pytest.raises(NoProvider):
        await ensure_provider_for(session, "curation", runtime)
    settings = await update_provider_settings(session, runtime, ProviderSettingsUpdate())
    assert settings.enabled_providers is None
    assert settings.effective["curation"].provider == "anthropic"  # type: ignore[union-attr]


async def test_blocked_accounts_still_pass_the_enqueue_check(
    session: AsyncSession, runtime: ProviderRuntime, fake: FakeProviders
) -> None:
    await connect(runtime, fake, "anthropic")
    handle = await runtime.pool.lease("anthropic")
    await handle.rate_limited("5h", runtime.clock() + timedelta(hours=1))
    assert (await ensure_provider_for(session, "curation", runtime)).spec.id == "anthropic"


async def test_locked_vault_rejects_inference_naming_the_key(
    session: AsyncSession, runtime: ProviderRuntime, fake: FakeProviders
) -> None:
    await connect(runtime, fake, "anthropic")
    runtime.key_path.unlink()
    with pytest.raises(VaultLockedError, match=str(runtime.key_path)):
        await model_for(session, "tutoring", runtime)


def test_provider_limit_from_a_throttle() -> None:
    reset = datetime(2026, 10, 12, 3, 0, tzinfo=UTC)
    limit = provider_limit_from(ProviderThrottledException("x", reset_at=reset, scope="5h"))
    assert isinstance(limit, ProviderLimitReached) and limit.reset_at == reset
    assert str(limit).startswith("waiting for provider limit reset")
    assert provider_limit_from(ModelThrottledException("slow down")).reset_at is None


async def test_sign_out_revokes_where_supported_and_deletes(
    session_factory: async_sessionmaker[AsyncSession], tmp_path: Path, fake: FakeProviders
) -> None:
    base = get_registry()
    revocable = base.get("anthropic").model_copy(
        update={"revoke_url": "https://api.anthropic.com/v1/oauth/revoke"}
    )
    runtime = ProviderRuntime(
        Registry([revocable, base.get("openai-codex")]),
        session_factory,
        tmp_path / "vault.key",
        http=fake.http_factory(),
    )
    await connect(runtime, fake, "anthropic")
    (stored,) = await runtime.store.list_credentials()
    refresh = next(iter(fake.refresh_tokens))
    await runtime.delete_account(stored.id)
    assert refresh in fake.revoked
    assert await runtime.store.list_credentials() == []


async def test_agent_level_throttle_converts_to_a_deferral(
    session: AsyncSession, runtime: ProviderRuntime, fake: FakeProviders
) -> None:
    await connect(runtime, fake, "anthropic")
    reset = datetime.now(UTC) + timedelta(hours=3)
    handle = await runtime.pool.lease("anthropic")
    await handle.rate_limited("5h", reset)
    model = await model_for(session, "curation", runtime)
    agent = Agent(model=model, retry_strategy=ProviderRetryStrategy(), callback_handler=None)
    with pytest.raises(Exception) as info:
        await agent.invoke_async("Extract topics.")
    assert find_throttle(info.value) is not None
    limit = provider_limit_from(info.value)
    assert limit.reset_at is not None and abs((limit.reset_at - reset).total_seconds()) < 1
    assert find_throttle(ValueError("x")) is None
