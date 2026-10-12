from sqlalchemy.ext.asyncio import AsyncSession

from app.services.instance_settings import load_instance_settings
from app.services.providers import enabled_provider_ids, enabled_providers, get_registry


async def test_all_providers_enabled_by_default(session: AsyncSession) -> None:
    assert await enabled_provider_ids(session) is None
    specs = await enabled_providers(session, get_registry())
    assert [s.id for s in specs] == ["anthropic", "openai-codex"]


async def test_enabled_providers_setting_filters_registry(session: AsyncSession) -> None:
    row = await load_instance_settings(session)
    row.enabled_providers = ["openai-codex"]
    await session.commit()
    specs = await enabled_providers(session, get_registry())
    assert [s.id for s in specs] == ["openai-codex"]
