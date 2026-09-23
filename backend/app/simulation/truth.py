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

    # --- known commands, visible to operators as context ---
    payload_duty_percent: float = Field(default=100.0, ge=0.0, le=100.0)
    heater_auto: bool = True
    mode: OperatingMode = OperatingMode.NOMINAL

    @property
    def hidden_fields(self) -> dict[str, Any]:
        return {
            "generation_factor": self.generation_factor,
            "extra_load_w": self.extra_load_w,
            "heater_stuck_on": self.heater_stuck_on,
            "contact_loss": self.contact_loss,
        }

    @property
    def known_commands(self) -> dict[str, Any]:
        return {
            "payload_duty_percent": self.payload_duty_percent,
            "heater_auto": self.heater_auto,
            "mode": self.mode.value,
        }


class ConditionUpdate(BaseModel):
    """Partial update. Omitted fields keep their current value."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    generation_factor: float | None = Field(default=None, ge=0.0, le=1.0)
    extra_load_w: float | None = Field(default=None, ge=0.0, le=300.0)
    heater_stuck_on: bool | None = None
    contact_loss: bool | None = None
    payload_duty_percent: float | None = Field(default=None, ge=0.0, le=100.0)
    heater_auto: bool | None = None
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
        return replace(self, temperatures_c=dict(self.temperatures_c))

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
            "hidden_conditions": self.conditions.hidden_fields,
            "known_commands": self.conditions.known_commands,
        }
