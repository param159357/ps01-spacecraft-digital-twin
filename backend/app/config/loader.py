"""Typed, schema-validated loading of the YAML vehicle configuration.

Units, limits, assumptions and model versions live in these files rather than
scattered through UI code (CLAUDE.md section 4). Loading happens once at startup and
fails fast: a malformed fixture must not reach the simulation or the dashboards.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CONFIG_DIR = Path(__file__).resolve().parent


class Group(str, Enum):
    POWER = "POWER"
    THERMAL = "THERMAL"
    FLIGHT = "FLIGHT"
    MASTER = "MASTER"


class ChannelType(str, Enum):
    FLOAT = "float"
    BOOLEAN = "boolean"
    COUNT = "count"


class Provenance(str, Enum):
    SENSOR = "SENSOR"
    DERIVED = "DERIVED"
    GROUND = "GROUND"


class PredictionCoverage(str, Enum):
    MODELLED = "MODELLED"
    PROFILE = "PROFILE"
    DERIVED = "DERIVED"
    RULE_ONLY = "RULE_ONLY"


class Bounds(BaseModel):
    """Three independent bound sets; never conflate them (section 6.2)."""

    model_config = ConfigDict(frozen=True)

    physical: tuple[float | None, float | None] = (None, None)
    operating: tuple[float | None, float | None] = (None, None)
    display: tuple[float | None, float | None] = (None, None)

    @model_validator(mode="after")
    def _ordered(self) -> Bounds:
        for name in ("physical", "operating", "display"):
            lo, hi = getattr(self, name)
            if lo is not None and hi is not None and lo > hi:
                raise ValueError(f"{name} bounds inverted: {lo} > {hi}")
        return self


class Channel(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    unit: str
    groups: tuple[Group, ...]
    type: ChannelType
    provenance: Provenance
    description: str
    bounds: Bounds
    expected_value_method: str
    prediction: PredictionCoverage
    rule_refs: tuple[str, ...] = ()
    cadence_s: float | None = None
    window_seconds: float | None = None

    @model_validator(mode="after")
    def _groups_present(self) -> Channel:
        if not self.groups:
            raise ValueError(f"channel {self.id} declares no groups")
        return self


class Freshness(BaseModel):
    model_config = ConfigDict(frozen=True)

    warning_after_s: float
    critical_after_s: float

    @model_validator(mode="after")
    def _ordered(self) -> Freshness:
        if self.warning_after_s >= self.critical_after_s:
            raise ValueError("freshness warning threshold must precede the critical threshold")
        return self


class TelemetryConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    config_version: str
    default_cadence_s: float
    channels: tuple[Channel, ...]
    freshness: Freshness

    @model_validator(mode="after")
    def _unique_ids(self) -> TelemetryConfig:
        ids = [c.id for c in self.channels]
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            raise ValueError(f"duplicate channel ids: {sorted(duplicates)}")
        return self

    def by_id(self, channel_id: str) -> Channel | None:
        return next((c for c in self.channels if c.id == channel_id), None)

    def for_group(self, group: Group) -> tuple[Channel, ...]:
        return tuple(c for c in self.channels if group in c.groups)


class PowerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    battery_capacity_wh: float = Field(gt=0)
    initial_soc_percent: float = Field(ge=0, le=100)
    bus_target_v: float = Field(gt=0)
    solar_rated_w: float = Field(ge=0)
    essential_load_w: float = Field(ge=0)
    payload_nominal_w: float = Field(ge=0)
    heater_rated_w: float = Field(ge=0)
    eta_charge: float = Field(gt=0, le=1)
    eta_discharge: float = Field(gt=0, le=1)
    charge_limit_w: float = Field(gt=0)
    discharge_limit_w: float = Field(gt=0)
    battery_v_empty: float = Field(gt=0)
    battery_v_full: float = Field(gt=0)
    battery_series_resistance_ohm: float = Field(ge=0)
    soc_warning_percent: float = Field(ge=0, le=100)
    soc_critical_percent: float = Field(ge=0, le=100)
    solar_reference_voltage_v: float = Field(gt=0)
    solar_voltage_tempco_v_per_k: float = Field(ge=0)
    shed_order: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _shed_order_known(self) -> PowerConfig:
        unknown = set(self.shed_order) - {"payload", "heater"}
        if unknown:
            raise ValueError(f"shed_order names unsheddable loads: {sorted(unknown)}")
        return self

    @model_validator(mode="after")
    def _consistent(self) -> PowerConfig:
        if self.battery_v_full <= self.battery_v_empty:
            raise ValueError("battery_v_full must exceed battery_v_empty")
        if self.soc_critical_percent >= self.soc_warning_percent:
            raise ValueError("soc_critical_percent must be below soc_warning_percent")
        return self


class ThermalNode(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    heat_capacity_j_per_k: float = Field(gt=0)
    conductance_w_per_k: float = Field(gt=0)
    initial_c: float
    heat_input: str

    @property
    def time_constant_s(self) -> float:
        """First-order thermal time constant, used to pick a stable integration step."""
        return self.heat_capacity_j_per_k / self.conductance_w_per_k


class ThermalConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    boundary_sunlight_c: float
    boundary_eclipse_c: float
    heater_on_below_c: float
    heater_off_above_c: float
    nodes: dict[str, ThermalNode]

    @model_validator(mode="after")
    def _thermostat_ordered(self) -> ThermalConfig:
        if self.heater_on_below_c >= self.heater_off_above_c:
            raise ValueError("heater_on_below_c must be below heater_off_above_c")
        if not self.nodes:
            raise ValueError("no thermal nodes configured")
        return self

    @property
    def min_time_constant_s(self) -> float:
        return min(n.time_constant_s for n in self.nodes.values())


class PropellantConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    specific_gas_constant_j_per_kg_k: float = Field(gt=0)
    initial_gas_mass_kg: float = Field(ge=0)
    tank_volume_m3: float = Field(gt=0)
    initial_temperature_k: float = Field(gt=0)  # absolute temperature must exceed 0 K
    nominal_valve_flow_kg_per_s: float = Field(ge=0)

    def ideal_pressure_pa(self, gas_mass_kg: float, temperature_k: float) -> float:
        if temperature_k <= 0:
            raise ValueError("absolute temperature must be above zero kelvin")
        return (
            gas_mass_kg * self.specific_gas_constant_j_per_kg_k * temperature_k
        ) / self.tank_volume_m3


class NoiseSigma(BaseModel):
    model_config = ConfigDict(frozen=True)

    voltage_v: float = Field(ge=0)
    current_a: float = Field(ge=0)
    power_w: float = Field(ge=0)
    temperature_c: float = Field(ge=0)
    soc_percent: float = Field(ge=0)
    pressure_bar: float = Field(ge=0)
    mass_kg: float = Field(ge=0)
    angle_deg: float = Field(ge=0)
    rate_dps: float = Field(ge=0)
    wheel_rpm: float = Field(ge=0)
    cell_spread_v: float = Field(ge=0)
    snr_db: float = Field(ge=0)


class ObservationConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    noise_sigma: NoiseSigma


class EnvironmentConfig(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    sunlight_minutes: float = Field(gt=0)
    eclipse_minutes: float = Field(ge=0)


class FlightProfilesConfig(BaseModel):
    """Fixtures for the deterministic flight-systems and link profiles (section 7.5).

    Every channel driven from here carries PROFILE prediction coverage: these values
    produce reproducible readings, not a validated attitude-control model.
    """

    model_config = ConfigDict(frozen=True)

    rate_amplitude_dps: float = Field(gt=0)
    rate_period_s: float = Field(gt=0)
    pointing_bias_deg: float = Field(ge=0)
    pointing_amplitude_deg: float = Field(ge=0)
    pointing_period_s: float = Field(gt=0)
    maneuver_scale: float = Field(ge=1.0)

    wheel_bias_rpm: float
    wheel_amplitude_rpm: float = Field(ge=0)
    wheel_period_s: float = Field(gt=0)
    wheel_idle_current_a: float = Field(ge=0)
    wheel_current_per_rpm_s: float = Field(ge=0)

    cell_spread_base_v: float = Field(ge=0)
    cell_spread_amplitude_v: float = Field(ge=0)
    cell_spread_period_s: float = Field(gt=0)

    contact_minutes_per_cycle: float = Field(ge=0)
    link_snr_nominal_db: float
    link_snr_amplitude_db: float = Field(ge=0)
    link_snr_floor_db: float
    link_period_s: float = Field(gt=0)
    packets_per_second: float = Field(ge=0)
    packet_failure_at_floor: float = Field(ge=0, le=1)
    packet_window_seconds: int = Field(gt=0)

    @model_validator(mode="after")
    def _link_consistent(self) -> FlightProfilesConfig:
        if self.link_snr_floor_db >= self.link_snr_nominal_db:
            raise ValueError("link_snr_floor_db must sit below link_snr_nominal_db")
        return self


class SimulationConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    tick_seconds: float = Field(gt=0)
    forecast_horizon_s: float = Field(gt=0)
    forecast_max_points: int = Field(gt=1)
    seed: int


class SpacecraftConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    config_version: str
    model_version: str
    vehicle_name: str
    power: PowerConfig
    thermal: ThermalConfig
    propellant: PropellantConfig
    observation: ObservationConfig
    environment: EnvironmentConfig
    flight_profiles: FlightProfilesConfig
    simulation: SimulationConfig

    @property
    def environment_cycle_s(self) -> float:
        """One configured sunlight + eclipse cycle. Not an orbital period."""
        return (self.environment.sunlight_minutes + self.environment.eclipse_minutes) * 60.0


class RuleDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    family: str
    severity_default: str
    subsystem: str
    implemented: bool
    phase: int | None = None
    description: str
    params: dict[str, Any] = Field(default_factory=dict)


class RulesConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    config_version: str
    defaults: dict[str, Any]
    rules: tuple[RuleDefinition, ...]

    @property
    def implemented_ids(self) -> tuple[str, ...]:
        return tuple(r.id for r in self.rules if r.implemented)

    def by_id(self, rule_id: str) -> RuleDefinition | None:
        return next((r for r in self.rules if r.id == rule_id), None)


class ActionDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    enabled: bool
    phase: int | None = None
    subsystem: str
    operation: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    prerequisites: tuple[str, ...] = ()
    trade_off: str


class ActionsConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    config_version: str
    defaults: dict[str, Any]
    actions: tuple[ActionDefinition, ...]

    @property
    def enabled_ids(self) -> tuple[str, ...]:
        """Only these may ever be offered to an operator as an available fix."""
        return tuple(a.id for a in self.actions if a.enabled)

    def by_id(self, action_id: str) -> ActionDefinition | None:
        return next((a for a in self.actions if a.id == action_id), None)


class VehicleConfiguration(BaseModel):
    """The complete validated fixture set, plus a combined version stamp."""

    model_config = ConfigDict(frozen=True)

    spacecraft: SpacecraftConfig
    telemetry: TelemetryConfig
    rules: RulesConfig
    actions: ActionsConfig

    @property
    def config_version(self) -> str:
        return (
            f"{self.spacecraft.config_version}+{self.telemetry.config_version}"
            f"+{self.rules.config_version}+{self.actions.config_version}"
        )

    @model_validator(mode="after")
    def _cross_references_resolve(self) -> VehicleConfiguration:
        known_rules = {r.id for r in self.rules.rules}
        for channel in self.telemetry.channels:
            unknown = set(channel.rule_refs) - known_rules
            if unknown:
                raise ValueError(
                    f"channel {channel.id} references unknown rule(s): {sorted(unknown)}"
                )
        known_nodes = set(self.spacecraft.thermal.nodes)
        for channel in self.telemetry.channels:
            method = channel.expected_value_method
            if method.startswith("thermal_node:"):
                node = method.split(":", 1)[1]
                if node not in known_nodes:
                    raise ValueError(f"channel {channel.id} references unknown node {node!r}")
        return self


def _read_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{name} did not parse to a mapping")
    return data


@lru_cache
def load_configuration() -> VehicleConfiguration:
    """Load and validate every fixture file. Raises on any inconsistency."""
    return VehicleConfiguration(
        spacecraft=SpacecraftConfig.model_validate(_read_yaml("spacecraft.yaml")),
        telemetry=TelemetryConfig.model_validate(_read_yaml("telemetry.yaml")),
        rules=RulesConfig.model_validate(_read_yaml("rules.yaml")),
        actions=ActionsConfig.model_validate(_read_yaml("actions.yaml")),
    )
