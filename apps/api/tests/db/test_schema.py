from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.db.schema import Base, InstanceSettings, Job, ProviderCredential

D8_TABLES = {
    "settings",
    "profiles",
    "current_skills",
    "target_skills",
    "sources",
    "chunks",
    "curation_runs",
    "topics",
    "topic_revisions",
    "topic_chunks",
    "study_plans",
    "plan_items",
    "storyboards",
    "assets",
    "study_sessions",
    "jobs",
    "job_events",
    "usage_counters",
    "provider_credentials",
    "provider_blocks",
    "provider_usage_windows",
    "provider_usage",
    "tutor_threads",
    "tutor_messages",
}


def test_metadata_covers_every_d8_table() -> None:
    assert set(Base.metadata.tables) == D8_TABLES


def test_columns_later_sections_rely_on() -> None:
    tables = Base.metadata.tables
    assert {"merged_into"} <= set(tables["topics"].c.keys())
    assert {"topic_revision_id", "week_start", "week_span", "exceeds_weekly_budget"} <= set(
        tables["plan_items"].c.keys()
    )
    assert "checkpoint" in tables["jobs"].c
    assert "sentence_continuation" in tables["chunks"].c
    assert tables["chunks"].c["embedding"].type.dim == 384  # type: ignore[attr-defined]


async def test_schema_creates_and_defaults_apply(
    engine: AsyncEngine, session: AsyncSession
) -> None:
    async with engine.connect() as connection:
        names = await connection.run_sync(lambda c: set(inspect(c).get_table_names()))
    assert names >= D8_TABLES

    session.add_all([InstanceSettings(), Job(type="noop")])
    await session.commit()
    job = (await session.execute(Job.__table__.select())).one()
    assert job.status == "queued"
    assert job.attempt == 0
    assert job.checkpoint == {}


def test_credential_repr_never_contains_tokens() -> None:
    credential = ProviderCredential(
        provider="anthropic",
        identity_key="email:a@b.c",
        access_enc=b"secret-a",
        refresh_enc=b"secret-r",
    )
    assert "secret" not in repr(credential)
