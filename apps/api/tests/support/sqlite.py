"""A file-backed SQLite engine for tests that run the job runner's concurrent sessions.

The conftest in-memory database shares one connection, so the runner's handler session,
heartbeat task and job update would share a transaction. This engine gives every session its
own connection (``NullPool``, like Postgres): WAL for concurrent readers and ``busy_timeout``
so a writer waits for another writer instead of failing.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.schema import Base


async def file_session_factory(path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yields a session factory over ``path`` with the schema created; disposes on exit."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}", poolclass=NullPool)

    @event.listens_for(engine.sync_engine, "connect")
    def configure(dbapi_connection: Any, _: Any) -> None:
        dbapi_connection.execute("PRAGMA journal_mode=WAL")
        dbapi_connection.execute("PRAGMA busy_timeout=5000")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
