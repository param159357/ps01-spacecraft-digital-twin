"""Telemetry frame and stream envelope contracts (CLAUDE.md sections 6.3 and 12.3).

Rules enforced by these types:
  * `null` means unavailable. NaN and Infinity are never serialized.
  * Quality carries a reason and a source timestamp; the last valid value is held
    separately from the current quality.
  * Clocks are explicit: simulated seconds, backend receipt time and sequence
    counters are distinct fields and must never be mixed in one duration.
"""

from __future__ import annotations

import math
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .common import Illumination, OperatingMode, QualityState, RunStatus, SourceKind

ChannelValue = float | bool | int | None

# Engineering detail is not a telemetry channel, so it may also carry a short label:
# the names of loads currently shed, or the times of reset events. Those entries are
# None most of the time, which is exactly why typing this as numeric-only stayed
# latent until the first real load-shedding event stopped the whole stream.
EngineeringValue = float | bool | int | str | None


def _reject_non_finite(value: EngineeringValue) -> EngineeringValue:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("NaN and Infinity must never appear in telemetry JSON; use null")
    return value


class ChannelQuality(BaseModel):
    model_config = ConfigDict(frozen=True)

    state: QualityState
    reason: str | None = None
    source_time_utc: datetime | None = None
    last_valid_value: ChannelValue = None
    last_valid_time_utc: datetime | None = None

    @field_validator("last_valid_value")
    @classmethod
    def _finite(cls, v: ChannelValue) -> ChannelValue:
        return _reject_non_finite(v)

    @property
    def is_usable(self) -> bool:
        return self.state is QualityState.VALID


class FrameContext(BaseModel):
    """Run context. Not counted against the 28-channel target (section 6.3)."""

    model_config = ConfigDict(frozen=True)

    vehicle_config_version: str
    model_version: str
    run_id: UUID
    source: SourceKind
    run_status: RunStatus
    mode: OperatingMode
    illumination: Illumination
    contact_expected: bool
    control_revision: int = Field(ge=0)
    eclipse_next_change_s: float | None = None
    # What the ground knows it commanded. Legitimately visible to operators — it is what
    # makes a command-versus-observation mismatch detectable. Never hidden truth.
    known_commands: dict[str, Any] = Field(default_factory=dict)
    acknowledged_commands: tuple[str, ...] = ()
    equipment_schedule: tuple[dict[str, Any], ...] = ()


class TelemetryFrame(BaseModel):
    """One observed frame. Observations only — never simulator truth."""

    model_config = ConfigDict(frozen=True)

    run_id: UUID
    frame_seq: int = Field(ge=0)
    tick_seq: int = Field(ge=0)
    sim_time_s: float = Field(ge=0)
    sim_epoch_utc: datetime
    received_at_utc: datetime
    values: dict[str, ChannelValue]
    quality: dict[str, ChannelQuality]
    context: FrameContext
    # Derived engineering detail that is not part of the 28-channel dictionary — for
    # example served and unmet power beside the requested `load_power` channel.
    engineering: dict[str, EngineeringValue] = Field(default_factory=dict)

    @field_validator("values")
    @classmethod
    def _finite_values(cls, v: dict[str, ChannelValue]) -> dict[str, ChannelValue]:
        for key, value in v.items():
            try:
                _reject_non_finite(value)
            except ValueError as exc:
                raise ValueError(f"channel {key}: {exc}") from exc
        return v

    @field_validator("engineering")
    @classmethod
    def _finite_engineering(cls, v: dict[str, EngineeringValue]) -> dict[str, EngineeringValue]:
        for key, value in v.items():
            try:
                _reject_non_finite(value)
            except ValueError as exc:
                raise ValueError(f"engineering {key}: {exc}") from exc
        return v

    def value_if_valid(self, channel_id: str) -> ChannelValue:
        """Read a value only when its quality says it is usable."""
        q = self.quality.get(channel_id)
        if q is None or not q.is_usable:
            return None
        return self.values.get(channel_id)


class StreamEventType(str, Enum):
    TELEMETRY = "telemetry"
    INCIDENT = "incident"
    FORECAST = "forecast"
    ANALYSIS = "analysis"
    COMMAND = "command"
    RUN_CHANGED = "run_changed"
    HEARTBEAT = "heartbeat"
    SNAPSHOT = "snapshot"


class StreamEvent(BaseModel):
    """SSE envelope. A heartbeat confirms the backend connection, not fresh readings."""

    model_config = ConfigDict(frozen=True)

    type: StreamEventType
    event_seq: int = Field(ge=0)
    run_id: UUID | None = None
    emitted_at_utc: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
