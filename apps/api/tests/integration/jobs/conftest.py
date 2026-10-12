"""Postgres fixtures for the job tests; skipped unless ``DATABASE_URL`` names Postgres.

Each test gets its own throwaway schema: ``schema_translate_map`` sends every ORM table there
(the ``vector`` type still comes from the extension in ``public``), so the suite never touches
the tables of the database it runs against.
"""

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.schema import Base

DATABASE_URL = os.environ.get("DATABASE_URL", "")


@pytest.fixture
def pg_schema() -> str:
    return f"it_{uuid4().hex[:12]}"


@pytest.fixture
async def pg_engine(pg_schema: str) -> AsyncIterator[AsyncEngine]:
    if not DATABASE_URL.startswith("postgresql"):
        pytest.skip("needs DATABASE_URL pointing at Postgres")
    admin = create_async_engine(DATABASE_URL)
    async with admin.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.execute(text(f'CREATE SCHEMA "{pg_schema}"'))
    engine = create_async_engine(
        DATABASE_URL, execution_options={"schema_translate_map": {None: pg_schema}}
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{pg_schema}" CASCADE'))
        await admin.dispose()


@pytest.fixture
def pg_factory(pg_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(pg_engine, expire_on_commit=False)
