"""The single ORM module (persistence shape, design D8).

Imported eagerly: the mapper registry must be complete before relationships resolve and before
Alembic autogenerate. Columns change only through new Alembic migrations.
"""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

EMBEDDING_DIM = 384

# Value sets from the specs, shared by CHECK constraints and the API contracts.
SKILL_LEVELS = ("novice", "working", "proficient", "expert")
TARGET_LEVELS = ("working", "proficient", "expert")
PRIORITIES = ("high", "medium", "low")
DEPTHS = ("intuition-first", "rigor-first")
SOURCE_TYPES = ("url", "pdf", "git", "note")
SOURCE_STATUSES = ("queued", "extracting", "embedding", "ready", "failed")
JOB_STATUSES = ("queued", "running", "ready", "failed", "cancelled", "skipped")
ASSET_FORMATS = ("video", "figure")
REVISION_ORIGINS = ("run", "edit")
MASTERY_RATINGS = ("struggled", "understood", "confident", "could-teach")
TUTOR_CONTEXTS = ("topic", "plan", "general")
TUTOR_ROLES = ("user", "assistant")

# JSONB on Postgres, plain JSON elsewhere (SQLite unit tests).
Json = JSON().with_variant(JSONB(), "postgresql")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


def _json(default: Any) -> Mapped[Any]:
    literal = "'{}'" if isinstance(default, dict) else "'[]'"
    return mapped_column(Json, default=type(default), server_default=text(literal), nullable=False)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# --- Instance -----------------------------------------------------------------------------


class InstanceSettings(Base):
    """Singleton row (id = 1): instance password, session secret, vault state, routing, budgets."""

    __tablename__ = "settings"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False, default=1)
    password_hash: Mapped[str | None] = mapped_column(Text)
    session_secret: Mapped[str | None] = mapped_column(Text)
    vault_fingerprint: Mapped[str | None] = mapped_column(String(64))
    # None means every registry provider is enabled.
    enabled_providers: Mapped[list[str] | None] = mapped_column(Json)
    provider_routing: Mapped[dict[str, Any]] = _json({})
    render_minutes_month: Mapped[int] = mapped_column(
        Integer, default=600, server_default=text("600"), nullable=False
    )
    generation_jobs_day: Mapped[int] = mapped_column(
        Integer, default=20, server_default=text("20"), nullable=False
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


# --- Learner profile ----------------------------------------------------------------------


class Profile(Base):
    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint("singleton", name="singleton"),
        CheckConstraint("weekly_budget_hours > 0", name="budget_positive"),
        CheckConstraint(_in("depth", DEPTHS), name="depth"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    singleton: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False, unique=True
    )
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    background: Mapped[str] = mapped_column(Text, nullable=False, default="")
    weekly_budget_hours: Mapped[float] = mapped_column(Float, nullable=False)
    preferred_formats: Mapped[list[str]] = mapped_column(
        Json,
        default=lambda: ["video", "figure", "text"],
        server_default=text("""'["video", "figure", "text"]'"""),
        nullable=False,
    )
    depth: Mapped[str] = mapped_column(
        String(32), default="intuition-first", server_default="intuition-first", nullable=False
    )
    language: Mapped[str] = mapped_column(
        String(16), default="en", server_default="en", nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class CurrentSkill(Base):
    __tablename__ = "current_skills"
    __table_args__ = (
        UniqueConstraint("profile_id", "name_normalized"),
        CheckConstraint(_in("level", SKILL_LEVELS), name="level"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    name_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class TargetSkill(Base):
    __tablename__ = "target_skills"
    __table_args__ = (
        UniqueConstraint("profile_id", "name_normalized"),
        CheckConstraint(_in("target_level", TARGET_LEVELS), name="target_level"),
        CheckConstraint(_in("priority", PRIORITIES), name="priority"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    name_normalized: Mapped[str] = mapped_column(Text, nullable=False)
    target_level: Mapped[str] = mapped_column(String(16), nullable=False)
    priority: Mapped[str] = mapped_column(String(8), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    achieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


# --- Jobs ---------------------------------------------------------------------------------


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(_in("status", JOB_STATUSES), name="status"),
        Index("ix_jobs_claim", "status", "priority", "run_after", "created_at"),
        Index("ix_jobs_type_status", "type", "status"),
        # At most one queued or running job per dedupe key (e.g. "video:<topic_id>").
        Index(
            "uq_jobs_active_dedupe_key",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="queued", server_default="queued", nullable=False
    )
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    payload: Mapped[dict[str, Any]] = _json({})
    dedupe_key: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    max_attempts: Mapped[int] = mapped_column(
        Integer, default=3, server_default="3", nullable=False
    )
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    locked_by: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    progress: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    error_reason: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    usage: Mapped[dict[str, Any]] = _json({})
    checkpoint: Mapped[dict[str, Any]] = _json({})
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), Identity(), primary_key=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str | None] = mapped_column(String(16))
    message: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = _json({})
    created_at: Mapped[datetime] = _created_at()


class UsageCounter(Base):
    """Budget counters, e.g. ``render_minutes_month`` for period ``2026-10``."""

    __tablename__ = "usage_counters"
    __table_args__ = (UniqueConstraint("counter", "period"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    counter: Mapped[str] = mapped_column(String(64), nullable=False)
    period: Mapped[str] = mapped_column(String(16), nullable=False)
    value: Mapped[float] = mapped_column(Float, default=0, server_default="0", nullable=False)
    updated_at: Mapped[datetime] = _updated_at()


# --- Material ingestion -------------------------------------------------------------------


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint(_in("type", SOURCE_TYPES), name="type"),
        CheckConstraint(_in("status", SOURCE_STATUSES), name="status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    type: Mapped[str] = mapped_column(String(8), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    uri: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), default="queued", server_default="queued", nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("source_id", "position"),
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # {"page": 3} | {"path": "docs/raft.md"} | {"fragment": "#leader-election"}
    location: Mapped[dict[str, Any]] = _json({})
    sentence_continuation: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[datetime] = _created_at()


# --- Curriculum curation ------------------------------------------------------------------


class CurationRun(Base):
    __tablename__ = "curation_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_ids: Mapped[list[str]] = _json([])
    threshold: Mapped[float] = mapped_column(
        Float, default=0.4, server_default="0.4", nullable=False
    )
    models: Mapped[dict[str, Any]] = _json({})
    step_log: Mapped[list[Any]] = _json([])
    created_at: Mapped[datetime] = _created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Topic(Base):
    """Stable topic identity. Content lives in ``topic_revisions``."""

    __tablename__ = "topics"

    id: Mapped[uuid.UUID] = _uuid_pk()
    merged_into: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL")
    )
    orphaned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class TopicRevision(Base):
    __tablename__ = "topic_revisions"
    __table_args__ = (
        UniqueConstraint("topic_id", "revision"),
        CheckConstraint(_in("created_by", REVISION_ORIGINS), name="created_by"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(8), nullable=False)
    curation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("curation_runs.id", ondelete="SET NULL")
    )
    # Embedding of title + summary, used by reconcile (cosine >= 0.85).
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[datetime] = _created_at()


class TopicChunk(Base):
    __tablename__ = "topic_chunks"

    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), primary_key=True
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), primary_key=True
    )


class StudyPlan(Base):
    """Immutable plan version; edits copy forward into a new version."""

    __tablename__ = "study_plans"
    __table_args__ = (CheckConstraint(_in("created_by", REVISION_ORIGINS), name="created_by"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    curation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("curation_runs.id", ondelete="SET NULL")
    )
    created_by: Mapped[str] = mapped_column(String(8), nullable=False)
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_ids: Mapped[list[str]] = _json([])
    created_at: Mapped[datetime] = _created_at()


class PlanItem(Base):
    __tablename__ = "plan_items"
    __table_args__ = (
        UniqueConstraint("plan_id", "topic_id"),
        CheckConstraint("week_span IS NULL OR week_span >= 1", name="week_span"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("study_plans.id", ondelete="CASCADE"), nullable=False
    )
    topic_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("topics.id"), nullable=False)
    topic_revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topic_revisions.id"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    # Excluded items (below threshold) have no week.
    week_start: Mapped[int | None] = mapped_column(Integer)
    week_span: Mapped[int | None] = mapped_column(Integer)
    exceeds_weekly_budget: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    prerequisites: Mapped[list[str]] = _json([])
    manually_included: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    excluded: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    relevance: Mapped[float | None] = mapped_column(Float)
    rationale: Mapped[str | None] = mapped_column(Text)
    serves_targets: Mapped[list[str]] = _json([])
    estimate_minutes: Mapped[int | None] = mapped_column(Integer)


# --- Visual generation --------------------------------------------------------------------


class Storyboard(Base):
    __tablename__ = "storyboards"
    __table_args__ = (CheckConstraint(_in("format", ASSET_FORMATS), name="format"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    scenes: Mapped[list[Any]] = _json([])
    figure_brief: Mapped[dict[str, Any] | None] = mapped_column(Json)
    created_at: Mapped[datetime] = _created_at()


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (CheckConstraint(_in("format", ASSET_FORMATS), name="format"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    storyboard_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("storyboards.id", ondelete="SET NULL")
    )
    previous_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assets.id", ondelete="SET NULL")
    )
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    is_current: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    feedback: Mapped[str | None] = mapped_column(Text)
    script: Mapped[str | None] = mapped_column(Text)
    # Paths relative to BRAINBENCH_DATA, e.g. {"video": ..., "captions": ..., "html": ...}.
    files: Mapped[dict[str, Any]] = _json({})
    # Figure transcript tail, brief, build source, render log, model ids.
    provenance: Mapped[dict[str, Any]] = _json({})
    duration_s: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = _created_at()


# --- Learning progress --------------------------------------------------------------------


class StudySession(Base):
    __tablename__ = "study_sessions"
    __table_args__ = (
        CheckConstraint("minutes > 0", name="minutes_positive"),
        CheckConstraint(_in("rating", MASTERY_RATINGS), name="rating"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    topic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE"), nullable=False, index=True
    )
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    rating: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    studied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = _created_at()


# --- Providers ----------------------------------------------------------------------------


class ProviderCredential(Base):
    """Vault row. Token columns are AES-256-GCM ciphertext and never leave the server."""

    __tablename__ = "provider_credentials"
    __table_args__ = (UniqueConstraint("provider", "identity_key"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    identity_key: Mapped[str] = mapped_column(Text, nullable=False)
    identity: Mapped[dict[str, Any]] = _json({})
    access_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    refresh_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_cause: Mapped[str | None] = mapped_column(String(64))
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    def __repr__(self) -> str:
        return (
            f"ProviderCredential(id={self.id!s}, provider={self.provider!r}, "
            f"identity_key={self.identity_key!r}, version={self.version!r})"
        )


class ProviderBlock(Base):
    __tablename__ = "provider_blocks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    credential_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("provider_credentials.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class ProviderUsageWindow(Base):
    __tablename__ = "provider_usage_windows"
    __table_args__ = (UniqueConstraint("credential_id", "window"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    credential_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("provider_credentials.id", ondelete="CASCADE"), nullable=False
    )
    window: Mapped[str] = mapped_column(String(64), nullable=False)
    used_pct: Mapped[float | None] = mapped_column(Float)
    resets_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProviderUsage(Base):
    __tablename__ = "provider_usage"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), Identity(), primary_key=True
    )
    credential_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("provider_credentials.id", ondelete="SET NULL"), index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    task_class: Mapped[str] = mapped_column(String(32), nullable=False)
    input_tokens: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    output_tokens: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


# --- Tutor --------------------------------------------------------------------------------


class TutorThread(Base):
    __tablename__ = "tutor_threads"
    __table_args__ = (
        CheckConstraint(_in("context_type", TUTOR_CONTEXTS), name="context_type"),
        Index("ix_tutor_threads_context", "context_type", "context_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    context_type: Mapped[str] = mapped_column(String(8), nullable=False)
    # Topic id for topic contexts; null for plan and general.
    context_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("topics.id", ondelete="CASCADE")
    )
    title: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class TutorMessage(Base):
    __tablename__ = "tutor_messages"
    __table_args__ = (CheckConstraint(_in("role", TUTOR_ROLES), name="role"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tutor_threads.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    citations: Mapped[list[Any]] = _json([])
    related_topics: Mapped[list[Any]] = _json([])
    stopped: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    usage: Mapped[dict[str, Any]] = _json({})
    created_at: Mapped[datetime] = _created_at()
