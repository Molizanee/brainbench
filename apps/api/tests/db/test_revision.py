from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.revision import SchemaMismatch, ensure_schema_at_head, head_revision

SCHEMA_REV = Path(__file__).resolve().parents[4] / "infra" / "schema.rev"


def test_schema_rev_file_matches_alembic_head() -> None:
    assert SCHEMA_REV.read_text().strip() == head_revision()


async def set_revision(engine: AsyncEngine, revision: str | None) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        if revision is not None:
            await connection.execute(
                text("INSERT INTO alembic_version VALUES (:r)"), {"r": revision}
            )


async def test_head_accepted(engine: AsyncEngine) -> None:
    await set_revision(engine, head_revision())
    assert await ensure_schema_at_head(engine, "api") == head_revision()


async def test_missing_schema_refused(engine: AsyncEngine) -> None:
    with pytest.raises(SchemaMismatch, match="no schema"):
        await ensure_schema_at_head(engine, "api")


async def test_unknown_newer_revision_refused(engine: AsyncEngine) -> None:
    await set_revision(engine, "9999")
    with pytest.raises(SchemaMismatch, match=r"schema version mismatch.*9999 is newer.*worker"):
        await ensure_schema_at_head(engine, "worker")
