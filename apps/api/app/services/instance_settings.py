import secrets

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.schema import InstanceSettings


async def load_instance_settings(session: AsyncSession) -> InstanceSettings:
    """Return the singleton ``settings`` row, creating it (with a session secret) if absent.

    Changes are flushed, not committed; the caller owns the transaction.
    """
    row = await session.get(InstanceSettings, 1)
    if row is None:
        try:
            async with session.begin_nested():
                row = InstanceSettings(id=1, session_secret=secrets.token_urlsafe(48))
                session.add(row)
        except IntegrityError:
            row = await session.get(InstanceSettings, 1)
            assert row is not None
    if row.session_secret is None:
        row.session_secret = secrets.token_urlsafe(48)
        await session.flush()
    return row
