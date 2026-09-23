"""Pure model functions — CLAUDE.md sections 7.2, 7.3 and 7.4.

Everything here is a pure function of its arguments. The live twin and the Phase 3
forecaster call the same functions; neither shares a mutable truth object with the other,
which is what lets a forecast run from an immutable copy off the event loop.

All of it describes a fictional test vehicle. The thermal boundary is a toy linear
heat-rejection term over a limited range, not convection (there is no air) and not a full
radiation model. Celsius differences only; absolute temperature is used for gas maths.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from app.config.loader import PowerConfig, PropellantConfig, ThermalConfig

# Clamp only rounding dust. A large conservation error must stay visible.
_ROUNDING_WH = 1e-9


# ------------------------------------------------------------------------- electrical


@dataclass(frozen=True)
class LoadRequest:
    """Requested bus-side demand, itemised so each contribution is counted exactly once.

    `essential_w` already includes the nominal reaction-wheel allowance, so only the
    wheel's extra demand appears in `wheel_extra_w` (section 7.2).
    """

    essential_w: float
    payload_w: float
    heater_w: float
    wheel_extra_w: float = 0.0
    valve_w: float = 0.0
    fault_load_w: float = 0.0

    @property
    def total_w(self) -> float:
        return (
            self.essential_w
            + self.payload_w
            + self.heater_w
            + self.wheel_extra_w
            + self.valve_w
            + self.fault_load_w
        )


@dataclass(frozen=True)
class ElectricalStep:
    """One integration step of the power boundary. All powers are bus-side."""

    available_solar_w: float
    requested_load_w: float
    served_load_w: float
    unmet_load_w: float
    charge_bus_w: float
    discharge_bus_w: float
    curtailed_w: float
    solar_dispatched_w: float
    next_energy_wh: float
    soc_percent: float
    battery_power_w: float  # positive discharge, negative charge (battery side)
    shed: tuple[str, ...] = ()

    @property
    def delta_energy_wh(self) -> float:
        return self.next_energy_wh


def _solve_battery(
    power: PowerConfig, energy_wh: float, battery_power_w: float
) -> tuple[float, float]:
    """Terminal voltage and current for a requested battery-side power.

    Positive `battery_power_w` is discharge. Solves ``P = (V_oc - I*R) * I`` for the
    physical (smaller-current) root and refuses an impossible solution by limiting the
    delivered power to the resistive maximum ``V_oc**2 / (4R)``.
    """
    fraction = 0.0 if power.battery_capacity_wh <= 0 else energy_wh / power.battery_capacity_wh
    fraction = min(1.0, max(0.0, fraction))
    v_oc = power.battery_v_empty + (power.battery_v_full - power.battery_v_empty) * fraction
    r = power.battery_series_resistance_ohm

    if abs(battery_power_w) < 1e-12:
        return v_oc, 0.0

    if r <= 0:
        current = battery_power_w / v_oc
        return v_oc, current

    if battery_power_w > 0:
        discriminant = v_oc * v_oc - 4.0 * r * battery_power_w
        if discriminant < 0:
            # Not physically deliverable: cap at the resistive maximum.
            battery_power_w = v_oc * v_oc / (4.0 * r)
            discriminant = 0.0
        current = (v_oc - math.sqrt(discriminant)) / (2.0 * r)
        return v_oc - current * r, current

    # Charging: terminal voltage rises above open circuit.
    charge_power = -battery_power_w
    discriminant = v_oc * v_oc + 4.0 * r * charge_power
    current = (-v_oc + math.sqrt(discriminant)) / (2.0 * r)
    return v_oc + current * r, -current


def battery_terminal(
    power: PowerConfig, energy_wh: float, battery_power_w: float
) -> tuple[float, float]:
    """Public wrapper: returns (terminal_voltage_v, current_a), positive = discharge."""
    return _solve_battery(power, energy_wh, battery_power_w)


def _shed_loads(
    power: PowerConfig, load: LoadRequest, supply_w: float
) -> tuple[LoadRequest, tuple[str, ...]]:
    """Apply the declared shedding order until demand fits the available supply.

    Essential load is never shed, and an uncontrolled fault load cannot be shed. This is
    what stops the model from powering everything with SOC pinned at zero.
    """
    shed: list[str] = []
    current = load
    for name in power.shed_order:
        if current.total_w <= supply_w + 1e-9:
            break
        if name == "payload" and current.payload_w > 0:
            current = replace(current, payload_w=0.0)
            shed.append("payload")
        elif name == "heater" and current.heater_w > 0:
            current = replace(current, heater_w=0.0)
            shed.append("heater")
    return current, tuple(shed)


def step_electrical(
    power: PowerConfig,
    energy_wh: float,
    load: LoadRequest,
    illumination_factor: float,
    generation_factor: float,
    dt_s: float,
) -> ElectricalStep:
    """Advance the electrical/battery state by ``dt_s`` seconds.

    `generation_factor` is hidden simulator truth. An observer estimates it from
    observations; it is never handed to detection or forecasting directly.
    """
    available_solar_w = max(0.0, power.solar_rated_w * illumination_factor * generation_factor)

    # First pass: how much can the battery contribute at full demand?
    requested_w = load.total_w
    deficit_w = max(0.0, requested_w - available_solar_w)
    max_discharge_w = min(
        power.discharge_limit_w,
        max(0.0, energy_wh) * power.eta_discharge * 3600.0 / dt_s,
    )
    supply_w = available_solar_w + min(deficit_w, max_discharge_w)

    served, shed = _shed_loads(power, load, supply_w)
    served_request_w = served.total_w

    surplus_w = available_solar_w - served_request_w

    charge_bus_w = min(
        max(surplus_w, 0.0),
        power.charge_limit_w,
        max(0.0, power.battery_capacity_wh - energy_wh) * 3600.0 / (power.eta_charge * dt_s),
    )
    discharge_bus_w = min(
        max(-surplus_w, 0.0),
        power.discharge_limit_w,
        max(0.0, energy_wh) * power.eta_discharge * 3600.0 / dt_s,
    )

    delta_wh = (
        power.eta_charge * charge_bus_w - discharge_bus_w / power.eta_discharge
    ) * dt_s / 3600.0
    next_energy_wh = min(power.battery_capacity_wh, max(0.0, energy_wh + delta_wh))
    if abs(next_energy_wh - energy_wh) < _ROUNDING_WH:
        next_energy_wh = energy_wh

    unmet_load_w = max(0.0, served_request_w - available_solar_w - discharge_bus_w)
    curtailed_w = max(0.0, available_solar_w - served_request_w - charge_bus_w)
    solar_dispatched_w = available_solar_w - curtailed_w

    battery_power_w = discharge_bus_w / power.eta_discharge - power.eta_charge * charge_bus_w

    return ElectricalStep(
        available_solar_w=available_solar_w,
        requested_load_w=requested_w,
        served_load_w=max(0.0, served_request_w - unmet_load_w),
        unmet_load_w=unmet_load_w,
        charge_bus_w=charge_bus_w,
        discharge_bus_w=discharge_bus_w,
        curtailed_w=curtailed_w,
        solar_dispatched_w=solar_dispatched_w,
        next_energy_wh=next_energy_wh,
        soc_percent=100.0 * next_energy_wh / power.battery_capacity_wh,
        battery_power_w=battery_power_w,
        shed=shed,
    )


def bus_voltage(power: PowerConfig, requested_w: float, unmet_w: float) -> float:
    """Toy regulated-bus behaviour.

    The bus holds its setpoint while supply is adequate, and sags in proportion to the
    unmet fraction of demand when it is not. A regulated bus near 28 V says nothing about
    battery terminal voltage; the two are deliberately separate quantities.
    """
    if unmet_w <= 1e-9 or requested_w <= 1e-9:
        return power.bus_target_v
    served_fraction = max(0.0, 1.0 - unmet_w / requested_w)
    return max(0.0, power.bus_target_v * served_fraction)


def solar_array_output(
    power: PowerConfig, dispatched_w: float, panel_temperature_c: float, illuminated: bool
) -> tuple[float, float]:
    """Toy array operating point: (voltage_v, current_a).

    An unilluminated array reports zero, not an open-circuit voltage. Curtailment at a
    full battery shows up as reduced current at a steady operating voltage, which is
    expected behaviour and must not be read as a generation fault.
    """
    if not illuminated or dispatched_w <= 0.0:
        return 0.0, 0.0
    voltage = power.solar_reference_voltage_v - power.solar_voltage_tempco_v_per_k * (
        panel_temperature_c - 25.0
    )
    voltage = max(1.0, voltage)
    return voltage, dispatched_w / voltage


# ---------------------------------------------------------------------------- thermal


def thermal_substeps(thermal: ThermalConfig, dt_s: float, safety: float = 0.1) -> int:
    """Number of internal steps so the explicit integration stays well inside stability.

    Chosen from the smallest node time constant rather than assumed, so a coarse forecast
    step cannot quietly integrate an unstable thermal model.
    """
    tau = thermal.min_time_constant_s
    if tau <= 0:
        return 1
    return max(1, math.ceil(dt_s / (safety * tau)))


def step_node(
    heat_capacity_j_per_k: float,
    conductance_w_per_k: float,
    temperature_c: float,
    boundary_c: float,
    heat_in_w: float,
    dt_s: float,
) -> float:
    """One explicit step of the lumped-node approximation (section 7.3)."""
    heat_out_w = conductance_w_per_k * (temperature_c - boundary_c)
    return temperature_c + (heat_in_w - heat_out_w) * dt_s / heat_capacity_j_per_k


def step_thermal(
    thermal: ThermalConfig,
    temperatures_c: dict[str, float],
    heat_inputs_w: dict[str, float],
    boundary_c: float,
    dt_s: float,
) -> dict[str, float]:
    """Advance every configured node, substepping for stability."""
    substeps = thermal_substeps(thermal, dt_s)
    step_dt = dt_s / substeps
    current = dict(temperatures_c)

    for _ in range(substeps):
        updated = {}
        for name, node in thermal.nodes.items():
            updated[name] = step_node(
                node.heat_capacity_j_per_k,
                node.conductance_w_per_k,
                current.get(name, node.initial_c),
                boundary_c,
                heat_inputs_w.get(name, 0.0),
                step_dt,
            )
        current = updated
    return current


def analytic_node_response(
    heat_capacity_j_per_k: float,
    conductance_w_per_k: float,
    initial_c: float,
    boundary_c: float,
    heat_in_w: float,
    elapsed_s: float,
) -> float:
    """Closed-form first-order response, used only to validate the explicit integration."""
    equilibrium = boundary_c + heat_in_w / conductance_w_per_k
    tau = heat_capacity_j_per_k / conductance_w_per_k
    return equilibrium + (initial_c - equilibrium) * math.exp(-elapsed_s / tau)


def battery_ohmic_loss_w(current_a: float, resistance_ohm: float) -> float:
    """Self-heating from series resistance. Charging and discharging both dissipate."""
    return current_a * current_a * resistance_ohm


# ------------------------------------------------------------------------- propellant


def tank_pressure_bar(prop: PropellantConfig, gas_mass_kg: float, temperature_c: float) -> float:
    """Ideal-gas store pressure in bar absolute. Converts to kelvin for the gas maths."""
    temperature_k = temperature_c + 273.15
    if temperature_k <= 0:
        raise ValueError("absolute temperature must be above zero kelvin")
    return prop.ideal_pressure_pa(gas_mass_kg, temperature_k) / 1e5


def step_propellant(
    prop: PropellantConfig, gas_mass_kg: float, valve_open: bool, dt_s: float
) -> float:
    """Consume gas while the valve is open, clamped to the mass actually available."""
    if not valve_open:
        return gas_mass_kg
    consumed = min(gas_mass_kg, prop.nominal_valve_flow_kg_per_s * dt_s)
    return max(0.0, gas_mass_kg - consumed)


def mass_from_pressure(prop: PropellantConfig, pressure_bar: float, temperature_c: float) -> float:
    """Ground-side mass estimate derived from pressure and temperature.

    This is why `propellant_mass` is DERIVED, not measured: a mass 'loss' computed this
    way is not independent confirmation that gas actually left the tank.
    """
    temperature_k = temperature_c + 273.15
    if temperature_k <= 0:
        return 0.0
    return (pressure_bar * 1e5 * prop.tank_volume_m3) / (
        prop.specific_gas_constant_j_per_kg_k * temperature_k
    )
