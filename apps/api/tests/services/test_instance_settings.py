from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.schema import InstanceSettings
from app.services.instance_settings import load_instance_settings


async def test_singleton_created_once_with_session_secret(session: AsyncSession) -> None:
    first = await load_instance_settings(session)
    second = await load_instance_settings(session)
    await session.commit()

    assert first is second
    assert first.session_secret and len(first.session_secret) >= 48
    assert await session.scalar(select(func.count()).select_from(InstanceSettings)) == 1
