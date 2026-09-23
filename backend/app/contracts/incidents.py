"""Incident contract (CLAUDE.md section 8.2).

Key distinctions this type enforces:
  * Acknowledgement is orthogonal to lifecycle — acknowledging is not recovering.
  * `UNKNOWN` severity is a coverage/diagnosis statement, not an automatic critical.
  * Suspected causes are hypotheses; they are stored apart from observed evidence.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .common import Subsystem


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"


class Lifecycle(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    RECOVERING = "RECOVERING"
    RESOLVED = "RESOLVED"


class EvidenceItem(BaseModel):
    """One observed fact with a citable identifier, e.g. "frame:123"."""

    model_config = ConfigDict(frozen=True)

    text: str
    evidence_id: str
    channel_id: str | None = None
    value: float | bool | int | None = None
    unit: str | None = None
    observed_at_utc: datetime | None = None


class Acknowledgement(BaseModel):
    model_config = ConfigDict(frozen=True)

    acknowledged: bool = False
    acknowledged_by: str | None = None
    acknowledged_at_utc: datetime | None = None


class IncidentEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    at_utc: datetime
    kind: str
    detail: str
    severity: Severity | None = None
    lifecycle: Lifecycle | None = None
    evidence_ids: tuple[str, ...] = ()


class Incident(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    run_id: UUID
    rule_id: str
    rule_version: str
    family: str
    subsystem: Subsystem
    affected_groups: tuple[str, ...]
    affected_entity: str
    severity: Severity
    lifecycle: Lifecycle
    first_seen_utc: datetime
    last_seen_utc: datetime
    source_frames: tuple[int, ...] = ()
    observed_evidence: tuple[EvidenceItem, ...] = ()
    suspected_causes: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    forecast_id: UUID | None = None
    acknowledgement: Acknowledgement = Field(default_factory=Acknowledgement)
    resolution_evidence: tuple[EvidenceItem, ...] = ()
    related_incident_ids: tuple[UUID, ...] = ()
    timeline: tuple[IncidentEvent, ...] = ()
    detail: dict[str, Any] = Field(default_factory=dict)

    @property
    def dedup_key(self) -> tuple[str, str, str]:
        """Duplicate openings are suppressed by run + rule family + affected entity."""
        return (str(self.run_id), self.family, self.affected_entity)

    @property
    def is_active(self) -> bool:
        return self.lifecycle in (Lifecycle.PENDING, Lifecycle.OPEN, Lifecycle.RECOVERING)
