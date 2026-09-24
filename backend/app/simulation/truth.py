"""Simulator truth and the bounded conditions a presenter may inject.

The separation enforced here is the one invariant everything else rests on
(CLAUDE.md section 1.3):

* **Hidden truth** — `generation_factor`, `extra_load_w`, `heater_stuck_on`,
  `contact_loss` and the exact node states. Only the simulator account ever sees these.
  Detection, forecasting and the AI investigator never receive them.
* **Known commands** — `payload_duty_percent` and the commanded heater mode. These are
  legitimately visible to operators as context, because a real ground team knows what it
  commanded. That is what makes an actuator mismatch detectable later.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.config.loader import SpacecraftConfig
from app.contracts.common import OperatingMode


class InjectedConditions(BaseModel):
    """Bounded scenario controls. Every field is range-validated at the API boundary."""

    model_config = ConfigDict(frozen=True)

    # --- hidden from every operational console ---
    generation_factor: float = Field(default=1.0, ge=0.0, le=1.0)
    extra_load_w: float = Field(default=0.0, ge=0.0, le=300.0)
    heater_stuck_on: bool = False
    contact_loss: bool = False
    # The optional branch draws its rated power regardless of what was commanded.
    branch_stuck_on: bool = False
    # The isolator ignores its command. A recovery action can then be APPLIED and
    # still fail to improve anything, which operators must be able to see.
    isolator_failed: bool = False

    # Total computer resets that should have occurred in this run. Raising it makes the
    # vehicle record one timestamped reset event per increment; it never decreases within
    # a run. Hidden, because an operator learns about a reset only from telemetry.
    computer_resets: int = Field(default=0, ge=0, le=20)

    # --- known commands, visible to operators as context ---
    payload_duty_percent: float = Field(default=100.0, ge=0.0, le=100.0)
    heater_auto: bool = True
    valve_open: bool = False
    mode: OperatingMode = OperatingMode.NOMINAL

    @property
    def hidden_fields(self) -> dict[str, Any]:
        return {
            "generation_factor": self.generation_factor,
            "extra_load_w": self.extra_load_w,
            "heater_stuck_on": self.heater_stuck_on,
            "contact_loss": self.contact_loss,
            "computer_resets": self.computer_resets,
            "branch_stuck_on": self.branch_stuck_on,
            "isolator_failed": self.isolator_failed,
        }

    @property
    def known_commands(self) -> dict[str, Any]:
        return {
            "payload_duty_percent": self.payload_duty_percent,
            "heater_auto": self.heater_auto,
            "valve_open_commanded": self.valve_open,
            "mode": self.mode.value,
        }


class ConditionUpdate(BaseModel):
    """Partial update. Omitted fields keep their current value."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    generation_factor: float | None = Field(default=None, ge=0.0, le=1.0)
    extra_load_w: float | None = Field(default=None, ge=0.0, le=300.0)
    heater_stuck_on: bool | None = None
    contact_loss: bool | None = None
    computer_resets: int | None = Field(default=None, ge=0, le=20)
    branch_stuck_on: bool | None = None
    isolator_failed: bool | None = None
    payload_duty_percent: float | None = Field(default=None, ge=0.0, le=100.0)
    heater_auto: bool | None = None
    # Commanded cold-gas valve state. The channel `thruster_valve_open` is the
    # observed status and is published separately, so the two stay distinguishable.
    valve_open: bool | None = None
    mode: OperatingMode | None = None

    def apply_to(self, current: InjectedConditions) -> InjectedConditions:
        changes = {k: v for k, v in self.model_dump().items() if v is not None}
        return current.model_copy(update=changes)


@dataclass
class SimulatorTruth:
    """The authoritative physical state. Never serialized to an operational role."""

    energy_wh: float
    temperatures_c: dict[str, float]
    gas_mass_kg: float
    heater_on: bool
    valve_open: bool
    conditions: InjectedConditions = field(default_factory=InjectedConditions)
    tick_seq: int = 0
    sim_time_s: float = 0.0
    # Rolling (sim_time_s, attempted, failed) packet samples for the configured
    # window. A real counter, so the reported error rate has a stated denominator.
    packet_window: list[tuple[float, int, int]] = field(default_factory=list)
    # Cumulative resets within this run, plus the simulated time of each one.
    # A new run restarts the counter without implying a physical reset (section 7.5).
    resets_applied: int = 0
    reset_events_s: list[float] = field(default_factory=list)
    # Set by an approved ISOLATE_OPTIONAL_BRANCH command. This is what the ground
    # commanded; whether the isolator obeyed is a separate, hidden matter.
    branch_isolate_commanded: bool = False
    # Simulated seconds during which payload operation was commanded but not served.
    # Already-elapsed seconds cannot be recovered by any later action.
    payload_outage_s: float = 0.0

    @classmethod
    def initial(
        cls, config: SpacecraftConfig, conditions: InjectedConditions | None = None
    ) -> SimulatorTruth:
        power = config.power
        return cls(
            energy_wh=power.battery_capacity_wh * power.initial_soc_percent / 100.0,
            temperatures_c={name: node.initial_c for name, node in config.thermal.nodes.items()},
            gas_mass_kg=config.propellant.initial_gas_mass_kg,
            heater_on=False,
            valve_open=False,
            conditions=conditions or InjectedConditions(),
        )

    def copy(self) -> SimulatorTruth:
        """Immutable snapshot for off-loop work such as forecasting."""
        return replace(
            self,
            temperatures_c=dict(self.temperatures_c),
            packet_window=list(self.packet_window),
            reset_events_s=list(self.reset_events_s),
        )

    # --- persistence ----------------------------------------------------------

    def to_checkpoint(self) -> dict[str, Any]:
        """Serialized into `runs.private_checkpoint`, which is simulator-only storage."""
        return {
            "energy_wh": self.energy_wh,
            "temperatures_c": dict(self.temperatures_c),
            "gas_mass_kg": self.gas_mass_kg,
            "heater_on": self.heater_on,
            "valve_open": self.valve_open,
            "conditions": self.conditions.model_dump(mode="json"),
            "tick_seq": self.tick_seq,
            "sim_time_s": self.sim_time_s,
            "packet_window": [list(sample) for sample in self.packet_window],
            "resets_applied": self.resets_applied,
            "reset_events_s": list(self.reset_events_s),
            "branch_isolate_commanded": self.branch_isolate_commanded,
            "payload_outage_s": self.payload_outage_s,
        }

    @classmethod
    def from_checkpoint(cls, data: dict[str, Any], config: SpacecraftConfig) -> SimulatorTruth:
        base = cls.initial(config)
        if not data:
            return base
        return cls(
            energy_wh=float(data.get("energy_wh", base.energy_wh)),
            temperatures_c={**base.temperatures_c, **dict(data.get("temperatures_c") or {})},
            gas_mass_kg=float(data.get("gas_mass_kg", base.gas_mass_kg)),
            heater_on=bool(data.get("heater_on", False)),
            valve_open=bool(data.get("valve_open", False)),
            conditions=InjectedConditions.model_validate(data.get("conditions") or {}),
            tick_seq=int(data.get("tick_seq", 0)),
            sim_time_s=float(data.get("sim_time_s", 0.0)),
            packet_window=[
                (float(s[0]), int(s[1]), int(s[2])) for s in (data.get("packet_window") or [])
            ],
            resets_applied=int(data.get("resets_applied", 0)),
            reset_events_s=[float(v) for v in (data.get("reset_events_s") or [])],
            branch_isolate_commanded=bool(data.get("branch_isolate_commanded", False)),
            payload_outage_s=float(data.get("payload_outage_s", 0.0)),
        )

    # --- presenter view -------------------------------------------------------

    def presenter_view(self) -> dict[str, Any]:
        """Full truth, for the simulator console only."""
        return {
            "tick_seq": self.tick_seq,
            "sim_time_s": self.sim_time_s,
            "energy_wh": round(self.energy_wh, 4),
            "temperatures_c": {k: round(v, 3) for k, v in self.temperatures_c.items()},
            "gas_mass_kg": round(self.gas_mass_kg, 6),
            "heater_on": self.heater_on,
            "valve_open": self.valve_open,
            "resets_applied": self.resets_applied,
            "reset_events_s": list(self.reset_events_s),
            "branch_isolate_commanded": self.branch_isolate_commanded,
            "payload_outage_s": round(self.payload_outage_s, 1),
            "hidden_conditions": self.conditions.hidden_fields,
            "known_commands": self.conditions.known_commands,
        }
