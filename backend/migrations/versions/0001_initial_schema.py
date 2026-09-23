"""Initial schema: users, sessions, runs, telemetry, incidents, commands, analyses.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    # `unique=True, index=True` on the model produces a single unique index, not a
    # separate unique constraint alongside a plain index.
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"], unique=True)
    op.create_index("ix_sessions_user_expiry", "sessions", ["user_id", "expires_at"])

    op.create_table(
        "runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("config_version", sa.String(128), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("sim_epoch_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("control_revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("label", sa.String(128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        # Simulator-only truth. Never selected by monitoring, API serializers or AI tools.
        sa.Column("private_checkpoint", JSONB(), nullable=True),
        sa.CheckConstraint("source IN ('LIVE','SYNTHETIC','REPLAY')", name="ck_runs_source"),
        sa.CheckConstraint("status IN ('PAUSED','RUNNING','STOPPED')", name="ck_runs_status"),
    )
    op.create_index("ix_runs_source_created", "runs", ["source", "created_at"])

    op.create_table(
        "telemetry_frames",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("frame_seq", sa.Integer(), nullable=False),
        sa.Column("tick_seq", sa.Integer(), nullable=False),
        sa.Column("sim_time_s", sa.Float(), nullable=False),
        sa.Column("received_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("values", JSONB(), nullable=False),
        sa.Column("quality", JSONB(), nullable=False),
        sa.Column("context", JSONB(), nullable=False),
        sa.UniqueConstraint("run_id", "frame_seq", name="uq_frames_run_seq"),
    )
    op.create_index("ix_frames_run_simtime", "telemetry_frames", ["run_id", "sim_time_s"])

    op.create_table(
        "incidents",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column("family", sa.String(64), nullable=False),
        sa.Column("subsystem", sa.String(32), nullable=False),
        sa.Column("affected_entity", sa.String(64), nullable=False),
        sa.Column("affected_groups", JSONB(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("lifecycle", sa.String(16), nullable=False),
        sa.Column("first_seen_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence", JSONB(), nullable=False),
        sa.Column("acknowledged", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("acknowledged_by", sa.String(64), nullable=True),
        sa.Column("acknowledged_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("forecast_id", UUID(as_uuid=True), nullable=True),
        sa.Column("related_incident_ids", JSONB(), nullable=True),
        sa.Column("evidence_revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint(
            "severity IN ('INFO','WARNING','CRITICAL','UNKNOWN')", name="ck_incidents_severity"
        ),
        sa.CheckConstraint(
            "lifecycle IN ('PENDING','OPEN','RECOVERING','RESOLVED')", name="ck_incidents_lifecycle"
        ),
    )
    op.create_index(
        "ix_incidents_active", "incidents", ["run_id", "family", "affected_entity", "lifecycle"]
    )

    op.create_table(
        "incident_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "incident_id",
            UUID(as_uuid=True),
            sa.ForeignKey("incidents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=True),
        sa.Column("lifecycle", sa.String(16), nullable=True),
        sa.Column("evidence_ids", JSONB(), nullable=True),
    )
    op.create_index(
        "ix_incident_events_incident_time", "incident_events", ["incident_id", "at_utc"]
    )

    op.create_table(
        "commands",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("actor_username", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("proposal_id", UUID(as_uuid=True), nullable=True),
        sa.Column("action_id", sa.String(64), nullable=False),
        sa.Column("action_arguments", JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("applied_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_tick_seq", sa.Integer(), nullable=True),
        sa.Column(
            "observed_outcome",
            sa.String(24),
            nullable=False,
            server_default=sa.text("'PENDING_OBSERVATION'"),
        ),
        sa.Column("outcome_detail", sa.Text(), nullable=True),
        # Makes duplicate execution impossible.
        sa.UniqueConstraint("run_id", "idempotency_key", name="uq_commands_idempotency"),
        sa.CheckConstraint(
            "status IN ('ACCEPTED','APPLIED','REJECTED','FAILED')", name="ck_commands_status"
        ),
    )
    op.create_index("ix_commands_run_created", "commands", ["run_id", "created_at"])

    op.create_table(
        "forecasts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("source_frame_seq", sa.Integer(), nullable=False),
        sa.Column("source_sim_time_s", sa.Float(), nullable=False),
        sa.Column("generated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_version", sa.String(64), nullable=False),
        sa.Column("config_version", sa.String(128), nullable=False),
        sa.Column("horizon_s", sa.Float(), nullable=False),
        sa.Column("coverage", sa.String(16), nullable=False),
        sa.Column("action_id", sa.String(64), nullable=True),
        sa.Column("action_arguments", JSONB(), nullable=True),
        sa.Column("assumptions", JSONB(), nullable=False),
        sa.Column("series", JSONB(), nullable=False),
        sa.Column("crossings", JSONB(), nullable=False),
    )
    op.create_index("ix_forecasts_run_generated", "forecasts", ["run_id", "generated_at_utc"])

    op.create_table(
        "analyses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("incident_id", UUID(as_uuid=True), nullable=True),
        sa.Column("window_key", sa.String(128), nullable=True),
        sa.Column("evidence_revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("generation_mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result", JSONB(), nullable=True),
        sa.Column("usage", JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("generation_mode IN ('LLM','TEMPLATE','REPLAY')", name="ck_analyses_mode"),
    )
    op.create_index("ix_analyses_cache", "analyses", ["incident_id", "evidence_revision"])
    op.create_index("ix_analyses_window", "analyses", ["window_key"])

    op.create_table(
        "history_buckets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("minute_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("aggregates", JSONB(), nullable=False),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("valid_sample_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint("run_id", "minute_utc", name="uq_history_run_minute"),
    )
    op.create_index("ix_history_minute", "history_buckets", ["minute_utc"])


def downgrade() -> None:
    op.drop_table("history_buckets")
    op.drop_table("analyses")
    op.drop_table("forecasts")
    op.drop_table("commands")
    op.drop_table("incident_events")
    op.drop_table("incidents")
    op.drop_table("telemetry_frames")
    op.drop_table("runs")
    op.drop_table("sessions")
    op.drop_table("users")
