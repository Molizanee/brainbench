"""Schema revision guard: API and worker refuse to run against a revision they were not built
with. Migrations run only in the ``init`` service (``alembic upgrade head``)."""

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

__lazy_modules__ = {"alembic.script"}
from alembic.script import ScriptDirectory

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class SchemaMismatch(RuntimeError):
    pass


def _scripts() -> ScriptDirectory:
    return ScriptDirectory(str(MIGRATIONS_DIR))


def head_revision() -> str:
    head = _scripts().get_current_head()
    if head is None:
        raise RuntimeError("no Alembic revisions found")
    return head


def known_revisions() -> set[str]:
    return {script.revision for script in _scripts().walk_revisions()}


async def current_revision(connection: AsyncConnection) -> str | None:
    try:
        result = await connection.execute(text("SELECT version_num FROM alembic_version"))
    except DBAPIError:
        return None
    return result.scalar_one_or_none()


async def ensure_schema_at_head(engine: AsyncEngine, component: str) -> str:
    """Return the head revision, or raise ``SchemaMismatch`` naming both revisions."""
    head = head_revision()
    async with engine.connect() as connection:
        current = await current_revision(connection)
    if current == head:
        return head
    if current is None:
        detail = "the database has no schema; the init service must run `alembic upgrade head`"
    elif current not in known_revisions():
        detail = f"schema revision {current} is newer than this {component} release ({head})"
    else:
        detail = f"schema revision {current} is behind this {component} release ({head})"
    raise SchemaMismatch(f"schema version mismatch: {detail}; refusing to start the {component}")
