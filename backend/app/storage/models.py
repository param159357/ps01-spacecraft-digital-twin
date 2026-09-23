"""ORM models — CLAUDE.md section 12.1.

Conventions: UUID primary keys for domain records, UTC timestamps, explicit run/source
IDs on everything that belongs to a run.

Isolation rule worth stating loudly: `runs.private_checkpoint` holds simulator-only
truth. It is queried by simulation code alone and must never be read by a monitoring
serializer, an API response model or an AI evidence tool.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    sessions: Mapped[list[SessionToken]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class SessionToken(Base):
    """Server-side session. Only the SHA-256 hash of the token is ever stored, and the
    plaintext token is never written to a log."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")

    __table_args__ = (Index("ix_sessions_user_expiry", "user_id", "expires_at"),)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # LIVE | SYNTHETIC | REPLAY
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    config_version: Mapped[str] = mapped_column(String(128), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    sim_epoch_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # PAUSED | RUNNING | STOPPED
    control_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # SIMULATOR-ONLY. Hidden injections and truth checkpoint. Never serialized to an
    # operational role, a monitoring response or an AI tool.
    private_checkpoint: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        CheckConstraint("source IN ('LIVE','SYNTHETIC','REPLAY')", name="ck_runs_source"),
        CheckConstraint("status IN ('PAUSED','RUNNING','STOPPED')", name="ck_runs_status"),
        Index("ix_runs_source_created", "source", "created_at"),
    )


class TelemetryFrameRow(Base):
    __tablename__ = "telemetry_frames"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    frame_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    tick_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    sim_time_s: Mapped[float] = mapped_column(Float, nullable=False)
    received_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    values: Mapped[dict] = mapped_column(JSONB, nullable=False)
    quality: Mapped[dict] = mapped_column(JSONB, nullable=False)
    context: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", "frame_seq", name="uq_frames_run_seq"),
        Index("ix_frames_run_simtime", "run_id", "sim_time_s"),
    )


class IncidentRow(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    family: Mapped[str] = mapped_column(String(64), nullable=False)
    subsystem: Mapped[str] = mapped_column(String(32), nullable=False)
    affected_entity: Mapped[str] = mapped_column(String(64), nullable=False)
    affected_groups: Mapped[dict] = mapped_column(JSONB, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(16), nullable=False)
    first_seen_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    acknowledged_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    acknowledged_at_utc: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    forecast_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    related_incident_ids: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    evidence_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint(
            "severity IN ('INFO','WARNING','CRITICAL','UNKNOWN')", name="ck_incidents_severity"
        ),
        CheckConstraint(
            "lifecycle IN ('PENDING','OPEN','RECOVERING','RESOLVED')",
            name="ck_incidents_lifecycle",
        ),
        # Supports the "one active incident per run + family + entity" dedup lookup.
        Index("ix_incidents_active", "run_id", "family", "affected_entity", "lifecycle"),
    )


class IncidentEventRow(Base):
    __tablename__ = "incident_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    incident_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    lifecycle: Mapped[str | None] = mapped_column(String(16), nullable=True)
    evidence_ids: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (Index("ix_incident_events_incident_time", "incident_id", "at_utc"),)


class CommandRow(Base):
    __tablename__ = "commands"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    actor_username: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    action_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action_arguments: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    applied_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_tick_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    observed_outcome: Mapped[str] = mapped_column(
        String(24), nullable=False, default="PENDING_OBSERVATION"
    )
    outcome_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # Makes duplicate execution impossible (section 9.3).
        UniqueConstraint("run_id", "idempotency_key", name="uq_commands_idempotency"),
        CheckConstraint(
            "status IN ('ACCEPTED','APPLIED','REJECTED','FAILED')", name="ck_commands_status"
        ),
        Index("ix_commands_run_created", "run_id", "created_at"),
    )


class ForecastRow(Base):
    __tablename__ = "forecasts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    source_frame_seq: Mapped[int] = mapped_column(Integer, nullable=False)
    source_sim_time_s: Mapped[float] = mapped_column(Float, nullable=False)
    generated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    config_version: Mapped[str] = mapped_column(String(128), nullable=False)
    horizon_s: Mapped[float] = mapped_column(Float, nullable=False)
    coverage: Mapped[str] = mapped_column(String(16), nullable=False)
    action_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action_arguments: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    assumptions: Mapped[dict] = mapped_column(JSONB, nullable=False)
    series: Mapped[dict] = mapped_column(JSONB, nullable=False)
    crossings: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (Index("ix_forecasts_run_generated", "run_id", "generated_at_utc"),)


class AnalysisRow(Base):
    __tablename__ = "analyses"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    incident_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    window_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    evidence_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    generation_mode: Mapped[str] = mapped_column(String(16), nullable=False)  # LLM|TEMPLATE|REPLAY
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    usage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("generation_mode IN ('LLM','TEMPLATE','REPLAY')", name="ck_analyses_mode"),
        # Analysis cache key: one result per incident + evidence revision.
        Index("ix_analyses_cache", "incident_id", "evidence_revision"),
        Index("ix_analyses_window", "window_key"),
    )


class HistoryBucketRow(Base):
    __tablename__ = "history_buckets"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    minute_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    aggregates: Mapped[dict] = mapped_column(JSONB, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_sample_count: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", "minute_utc", name="uq_history_run_minute"),
        Index("ix_history_minute", "minute_utc"),
    )
