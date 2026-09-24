"""Forecast, action proposal and command contracts (CLAUDE.md sections 9 and 12).

Rules enforced here:
  * An action is a catalogue ID plus validated arguments. Never generated text.
  * ACCEPTED is not APPLIED, and APPLIED is not recovered. Three separate facts.
  * A forecast states its assumptions, its source frame and its coverage gaps.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ForecastCoverage(str, Enum):
    MODELLED = "MODELLED"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


class CrossingKind(str, Enum):
    SOC_WARNING = "SOC_WARNING"
    SOC_CRITICAL = "SOC_CRITICAL"
    THERMAL_WARNING = "THERMAL_WARNING"
    THERMAL_CRITICAL = "THERMAL_CRITICAL"


class LimitCrossing(BaseModel):
    """ "Projected crossing in approximately X under these assumptions" — never a
    fixed "failure at 18 minutes" claim."""

    model_config = ConfigDict(frozen=True)

    kind: CrossingKind
    channel_id: str
    limit_value: float
    unit: str
    sim_time_s: float
    seconds_from_source: float


class ForecastSeries(BaseModel):
    model_config = ConfigDict(frozen=True)

    channel_id: str
    unit: str
    times_s: tuple[float, ...]
    values: tuple[float | None, ...]


class ForecastResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    run_id: UUID
    source_frame_seq: int
    source_sim_time_s: float
    generated_at_utc: datetime
    model_version: str
    config_version: str
    horizon_s: float
    coverage: ForecastCoverage
    coverage_reasons: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    series: tuple[ForecastSeries, ...] = ()
    crossings: tuple[LimitCrossing, ...] = ()
    action_id: str | None = None  # None for the no-action baseline
    action_arguments: dict[str, Any] = Field(default_factory=dict)
    stale: bool = False  # true when the source revision has moved on


class ProposalStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"
    REJECTED = "REJECTED"


class ActionProposal(BaseModel):
    """Produced by a comparison. Carries its own validity bounds and expiry."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    run_id: UUID
    incident_id: UUID | None
    action_id: str
    action_arguments: dict[str, Any] = Field(default_factory=dict)
    evidence_frame_seq: int
    control_revision: int
    created_at_utc: datetime
    expires_at_utc: datetime
    status: ProposalStatus = ProposalStatus.AVAILABLE
    baseline_forecast_id: UUID | None = None
    action_forecast_id: UUID | None = None
    input_validity: dict[str, tuple[float, float]] = Field(default_factory=dict)
    outcome_paths: tuple[str, ...] = ()
    rejection_reasons: tuple[str, ...] = ()
    trade_off: str = ""
    # Which console asked for this comparison. A specialist may raise one as a
    # recommendation; only the master may approve it.
    requested_by: str | None = None
    requested_by_role: str | None = None


class CommandStatus(str, Enum):
    ACCEPTED = "ACCEPTED"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class RecoveryOutcome(str, Enum):
    """A command may be applied yet fail to improve health."""

    PENDING_OBSERVATION = "PENDING_OBSERVATION"
    IMPROVED = "IMPROVED"
    NO_CHANGE = "NO_CHANGE"
    WORSENED = "WORSENED"
    UNKNOWN = "UNKNOWN"


class CommandRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    run_id: UUID
    proposal_id: UUID | None
    action_id: str
    action_arguments: dict[str, Any] = Field(default_factory=dict)
    actor_username: str
    idempotency_key: str
    status: CommandStatus
    status_reason: str | None = None
    created_at_utc: datetime
    applied_at_utc: datetime | None = None
    applied_tick_seq: int | None = None
    observed_outcome: RecoveryOutcome = RecoveryOutcome.PENDING_OBSERVATION
    outcome_detail: str | None = None
