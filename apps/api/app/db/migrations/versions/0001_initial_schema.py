"""initial schema: every table of design D8

Revision ID: 0001
Revises:
Create Date: 2026-10-11 22:32:09.785178
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("payload", postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column("attempt", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column(
            "run_after", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("locked_by", sa.Text(), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "cancel_requested", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("progress", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_reason", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("usage", postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("checkpoint", postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'ready', 'failed', 'cancelled', 'skipped')",
            name=op.f("ck_jobs_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    op.create_index(
        "ix_jobs_claim", "jobs", ["status", "priority", "run_after", "created_at"], unique=False
    )
    op.create_index("ix_jobs_type_status", "jobs", ["type", "status"], unique=False)
    op.create_index(
        "uq_jobs_active_dedupe_key",
        "jobs",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_table(
        "profiles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("singleton", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("background", sa.Text(), nullable=False),
        sa.Column("weekly_budget_hours", sa.Float(), nullable=False),
        sa.Column(
            "preferred_formats",
            postgresql.JSONB(),
            server_default=sa.text('\'["video", "figure", "text"]\''),
            nullable=False,
        ),
        sa.Column("depth", sa.String(length=32), server_default="intuition-first", nullable=False),
        sa.Column("language", sa.String(length=16), server_default="en", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "depth IN ('intuition-first', 'rigor-first')", name=op.f("ck_profiles_depth")
        ),
        sa.CheckConstraint("singleton", name=op.f("ck_profiles_singleton")),
        sa.CheckConstraint("weekly_budget_hours > 0", name=op.f("ck_profiles_budget_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profiles")),
        sa.UniqueConstraint("singleton", name=op.f("uq_profiles_singleton")),
    )
    op.create_table(
        "provider_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("identity_key", sa.Text(), nullable=False),
        sa.Column("identity", postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("access_enc", sa.LargeBinary(), nullable=True),
        sa.Column("refresh_enc", sa.LargeBinary(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_cause", sa.String(length=64), nullable=True),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_credentials")),
        sa.UniqueConstraint(
            "provider", "identity_key", name=op.f("uq_provider_credentials_provider_identity_key")
        ),
    )
    op.create_table(
        "settings",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("session_secret", sa.Text(), nullable=True),
        sa.Column("vault_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("enabled_providers", postgresql.JSONB(), nullable=True),
        sa.Column(
            "provider_routing", postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False
        ),
        sa.Column(
            "render_minutes_month", sa.Integer(), server_default=sa.text("600"), nullable=False
        ),
        sa.Column(
            "generation_jobs_day", sa.Integer(), server_default=sa.text("20"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_settings_singleton")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settings")),
    )
    op.create_table(
        "topics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merged_into", sa.Uuid(), nullable=True),
        sa.Column("orphaned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["merged_into"],
            ["topics.id"],
            name=op.f("fk_topics_merged_into_topics"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_topics")),
    )
    op.create_table(
        "usage_counters",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("counter", sa.String(length=64), nullable=False),
        sa.Column("period", sa.String(length=16), nullable=False),
        sa.Column("value", sa.Float(), server_default="0", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage_counters")),
        sa.UniqueConstraint("counter", "period", name=op.f("uq_usage_counters_counter_period")),
    )
    op.create_table(
        "curation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("source_ids", postgresql.JSONB(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("threshold", sa.Float(), server_default="0.4", nullable=False),
        sa.Column("models", postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("step_log", postgresql.JSONB(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_curation_runs_job_id_jobs"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_curation_runs")),
    )
    op.create_table(
        "current_skills",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_normalized", sa.Text(), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "level IN ('novice', 'working', 'proficient', 'expert')",
            name=op.f("ck_current_skills_level"),
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profiles.id"],
            name=op.f("fk_current_skills_profile_id_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_current_skills")),
        sa.UniqueConstraint(
            "profile_id",
            "name_normalized",
            name=op.f("uq_current_skills_profile_id_name_normalized"),
        ),
    )
    op.create_table(
        "job_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("data", postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_job_events_job_id_jobs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_events")),
    )
    op.create_index(op.f("ix_job_events_job_id"), "job_events", ["job_id"], unique=False)
    op.create_table(
        "provider_blocks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("credential_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["provider_credentials.id"],
            name=op.f("fk_provider_blocks_credential_id_provider_credentials"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_blocks")),
    )
    op.create_index(
        op.f("ix_provider_blocks_credential_id"), "provider_blocks", ["credential_id"], unique=False
    )
    op.create_table(
        "provider_usage",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("credential_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("task_class", sa.String(length=32), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["provider_credentials.id"],
            name=op.f("fk_provider_usage_credential_id_provider_credentials"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_usage")),
    )
    op.create_index(op.f("ix_provider_usage_at"), "provider_usage", ["at"], unique=False)
    op.create_index(
        op.f("ix_provider_usage_credential_id"), "provider_usage", ["credential_id"], unique=False
    )
    op.create_table(
        "provider_usage_windows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("credential_id", sa.Uuid(), nullable=False),
        sa.Column("window", sa.String(length=64), nullable=False),
        sa.Column("used_pct", sa.Float(), nullable=True),
        sa.Column("resets_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["provider_credentials.id"],
            name=op.f("fk_provider_usage_windows_credential_id_provider_credentials"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_usage_windows")),
        sa.UniqueConstraint(
            "credential_id", "window", name=op.f("uq_provider_usage_windows_credential_id_window")
        ),
    )
    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=8), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("uri", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'extracting', 'embedding', 'ready', 'failed')",
            name=op.f("ck_sources_status"),
        ),
        sa.CheckConstraint("type IN ('url', 'pdf', 'git', 'note')", name=op.f("ck_sources_type")),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_sources_job_id_jobs"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sources")),
    )
    op.create_table(
        "storyboards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("format", sa.String(length=8), nullable=False),
        sa.Column("scenes", postgresql.JSONB(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("figure_brief", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("format IN ('video', 'figure')", name=op.f("ck_storyboards_format")),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_storyboards_job_id_jobs"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["topics.id"],
            name=op.f("fk_storyboards_topic_id_topics"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_storyboards")),
    )
    op.create_table(
        "study_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("rating", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "studied_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "rating IN ('struggled', 'understood', 'confident', 'could-teach')",
            name=op.f("ck_study_sessions_rating"),
        ),
        sa.CheckConstraint("minutes > 0", name=op.f("ck_study_sessions_minutes_positive")),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["topics.id"],
            name=op.f("fk_study_sessions_topic_id_topics"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_study_sessions")),
    )
    op.create_index(
        op.f("ix_study_sessions_topic_id"), "study_sessions", ["topic_id"], unique=False
    )
    op.create_table(
        "target_skills",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_normalized", sa.Text(), nullable=False),
        sa.Column("target_level", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.String(length=8), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("achieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "priority IN ('high', 'medium', 'low')", name=op.f("ck_target_skills_priority")
        ),
        sa.CheckConstraint(
            "target_level IN ('working', 'proficient', 'expert')",
            name=op.f("ck_target_skills_target_level"),
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["profiles.id"],
            name=op.f("fk_target_skills_profile_id_profiles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_target_skills")),
        sa.UniqueConstraint(
            "profile_id",
            "name_normalized",
            name=op.f("uq_target_skills_profile_id_name_normalized"),
        ),
    )
    op.create_table(
        "tutor_threads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("context_type", sa.String(length=8), nullable=False),
        sa.Column("context_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "context_type IN ('topic', 'plan', 'general')",
            name=op.f("ck_tutor_threads_context_type"),
        ),
        sa.ForeignKeyConstraint(
            ["context_id"],
            ["topics.id"],
            name=op.f("fk_tutor_threads_context_id_topics"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tutor_threads")),
    )
    op.create_index(
        "ix_tutor_threads_context", "tutor_threads", ["context_type", "context_id"], unique=False
    )
    op.create_table(
        "assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("storyboard_id", sa.Uuid(), nullable=True),
        sa.Column("previous_asset_id", sa.Uuid(), nullable=True),
        sa.Column("format", sa.String(length=8), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("script", sa.Text(), nullable=True),
        sa.Column("files", postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("provenance", postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("duration_s", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("format IN ('video', 'figure')", name=op.f("ck_assets_format")),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name=op.f("fk_assets_job_id_jobs"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["previous_asset_id"],
            ["assets.id"],
            name=op.f("fk_assets_previous_asset_id_assets"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["storyboard_id"],
            ["storyboards.id"],
            name=op.f("fk_assets_storyboard_id_storyboards"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"], ["topics.id"], name=op.f("fk_assets_topic_id_topics"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assets")),
    )
    op.create_index(op.f("ix_assets_topic_id"), "assets", ["topic_id"], unique=False)
    op.create_table(
        "chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("location", postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column(
            "sentence_continuation", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name=op.f("fk_chunks_source_id_sources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunks")),
        sa.UniqueConstraint("source_id", "position", name=op.f("uq_chunks_source_id_position")),
    )
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_table(
        "study_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("curation_run_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.String(length=8), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("source_ids", postgresql.JSONB(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("created_by IN ('run', 'edit')", name=op.f("ck_study_plans_created_by")),
        sa.ForeignKeyConstraint(
            ["curation_run_id"],
            ["curation_runs.id"],
            name=op.f("fk_study_plans_curation_run_id_curation_runs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_study_plans")),
        sa.UniqueConstraint("version", name=op.f("uq_study_plans_version")),
    )
    op.create_table(
        "topic_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(length=8), nullable=False),
        sa.Column("curation_run_id", sa.Uuid(), nullable=True),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "created_by IN ('run', 'edit')", name=op.f("ck_topic_revisions_created_by")
        ),
        sa.ForeignKeyConstraint(
            ["curation_run_id"],
            ["curation_runs.id"],
            name=op.f("fk_topic_revisions_curation_run_id_curation_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["topics.id"],
            name=op.f("fk_topic_revisions_topic_id_topics"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_topic_revisions")),
        sa.UniqueConstraint(
            "topic_id", "revision", name=op.f("uq_topic_revisions_topic_id_revision")
        ),
    )
    op.create_table(
        "tutor_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", postgresql.JSONB(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column(
            "related_topics", postgresql.JSONB(), server_default=sa.text("'[]'"), nullable=False
        ),
        sa.Column("stopped", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("usage", postgresql.JSONB(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('user', 'assistant')", name=op.f("ck_tutor_messages_role")),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["tutor_threads.id"],
            name=op.f("fk_tutor_messages_thread_id_tutor_threads"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tutor_messages")),
    )
    op.create_index(
        op.f("ix_tutor_messages_thread_id"), "tutor_messages", ["thread_id"], unique=False
    )
    op.create_table(
        "plan_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("topic_revision_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("week_start", sa.Integer(), nullable=True),
        sa.Column("week_span", sa.Integer(), nullable=True),
        sa.Column(
            "exceeds_weekly_budget", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "prerequisites", postgresql.JSONB(), server_default=sa.text("'[]'"), nullable=False
        ),
        sa.Column(
            "manually_included", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("excluded", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("relevance", sa.Float(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column(
            "serves_targets", postgresql.JSONB(), server_default=sa.text("'[]'"), nullable=False
        ),
        sa.Column("estimate_minutes", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "week_span IS NULL OR week_span >= 1", name=op.f("ck_plan_items_week_span")
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["study_plans.id"],
            name=op.f("fk_plan_items_plan_id_study_plans"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"], ["topics.id"], name=op.f("fk_plan_items_topic_id_topics")
        ),
        sa.ForeignKeyConstraint(
            ["topic_revision_id"],
            ["topic_revisions.id"],
            name=op.f("fk_plan_items_topic_revision_id_topic_revisions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plan_items")),
        sa.UniqueConstraint("plan_id", "topic_id", name=op.f("uq_plan_items_plan_id_topic_id")),
    )
    op.create_table(
        "topic_chunks",
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["chunks.id"],
            name=op.f("fk_topic_chunks_chunk_id_chunks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["topics.id"],
            name=op.f("fk_topic_chunks_topic_id_topics"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("topic_id", "chunk_id", name=op.f("pk_topic_chunks")),
    )


def downgrade() -> None:
    op.drop_table("topic_chunks")
    op.drop_table("plan_items")
    op.drop_index(op.f("ix_tutor_messages_thread_id"), table_name="tutor_messages")
    op.drop_table("tutor_messages")
    op.drop_table("topic_revisions")
    op.drop_table("study_plans")
    op.drop_index(
        "ix_chunks_embedding_hnsw",
        table_name="chunks",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_table("chunks")
    op.drop_index(op.f("ix_assets_topic_id"), table_name="assets")
    op.drop_table("assets")
    op.drop_index("ix_tutor_threads_context", table_name="tutor_threads")
    op.drop_table("tutor_threads")
    op.drop_table("target_skills")
    op.drop_index(op.f("ix_study_sessions_topic_id"), table_name="study_sessions")
    op.drop_table("study_sessions")
    op.drop_table("storyboards")
    op.drop_table("sources")
    op.drop_table("provider_usage_windows")
    op.drop_index(op.f("ix_provider_usage_credential_id"), table_name="provider_usage")
    op.drop_index(op.f("ix_provider_usage_at"), table_name="provider_usage")
    op.drop_table("provider_usage")
    op.drop_index(op.f("ix_provider_blocks_credential_id"), table_name="provider_blocks")
    op.drop_table("provider_blocks")
    op.drop_index(op.f("ix_job_events_job_id"), table_name="job_events")
    op.drop_table("job_events")
    op.drop_table("current_skills")
    op.drop_table("curation_runs")
    op.drop_table("usage_counters")
    op.drop_table("topics")
    op.drop_table("settings")
    op.drop_table("provider_credentials")
    op.drop_table("profiles")
    op.drop_index(
        "uq_jobs_active_dedupe_key",
        table_name="jobs",
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.drop_index("ix_jobs_type_status", table_name="jobs")
    op.drop_index("ix_jobs_claim", table_name="jobs")
    op.drop_table("jobs")
    op.execute("DROP EXTENSION IF EXISTS vector")
