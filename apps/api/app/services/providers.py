"""Provider connection service (design D2, D3): the app side of ``brainbench_providers``.

The package is pure Python; this module wires it to the database, the vault key file, and
instance settings.
"""

import logging
from functools import cache

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.instance_settings import load_instance_settings
from brainbench_providers.registry import ProviderSpec, Registry, load_registry

log = logging.getLogger(__name__)


@cache
def get_registry() -> Registry:
    """Shipped providers plus ``$BRAINBENCH_DATA/models.yaml`` when it exists."""
    return load_registry([settings.brainbench_data / "models.yaml"])


async def enabled_provider_ids(session: AsyncSession) -> list[str] | None:
    """``settings.enabled_providers``; None means every registry provider is enabled."""
    row = await load_instance_settings(session)
    return None if row.enabled_providers is None else list(row.enabled_providers)


async def enabled_providers(session: AsyncSession, registry: Registry) -> list[ProviderSpec]:
    return registry.enabled(await enabled_provider_ids(session))
