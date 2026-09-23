"""Model reference checks — CLAUDE.md sections 7.2, 7.3, 7.4 and 13.2.

These are independent checks of the physics, not restatements of the implementation:
an analytic energy reference, the closed-form thermal step response, the ideal-gas
pressure reference, and the conservation behaviour at a full and an empty battery.
"""

from __future__ import annotations

import math

import pytest

from app.config.loader import load_configuration
from app.simulation import environment, models
from app.simulation.models import LoadRequest
from app.simulation.truth import InjectedConditions, SimulatorTruth
from app.simulation.twin import step


@pytest.fixture(scope="module")
def config():
    return load_configuration()


@pytest.fixture(scope="module")
def power(config):
    return config.spacecraft.power


# --------------------------------------------------------------------------- energy


def test_energy_reference_over_1800_seconds(power):
    """Section 7.2: a 100 W bus load for 1800 s at 0.95 discharge efficiency draws
    approximately 52.6316 Wh from the battery, with no solar power."""
    energy_wh = 700.0
    load = LoadRequest(essential_w=100.0, payload_w=0.0, heater_w=0.0)
    dt = 1.0

    for _ in range(1800):
        result = models.step_electrical(
            power=power,
            energy_wh=energy_wh,
            load=load,
            illumination_factor=0.0,
            generation_factor=1.0,
            dt_s=dt,
        )
        energy_wh = result.next_energy_wh

    assert math.isclose(700.0 - energy_wh, 52.6316, rel_tol=1e-4)


def test_step_size_does_not_change_the_energy_result(power):
    """One 1800 s step and 1800 one-second steps must agree: the electrical integration
    is exact for a constant load, so a coarse forecast step cannot drift."""
    load = LoadRequest(essential_w=100.0, payload_w=0.0, heater_w=0.0)

    single = models.step_electrical(
        power=power,
        energy_wh=700.0,
        load=load,
        illumination_factor=0.0,
        generation_factor=1.0,
        dt_s=1800.0,
    ).next_energy_wh

    stepped = 700.0
    for _ in range(1800):
        stepped = models.step_electrical(
            power=power,
            energy_wh=stepped,
            load=load,
            illumination_factor=0.0,
            generation_factor=1.0,
            dt_s=1.0,
        ).next_energy_wh

    assert math.isclose(single, stepped, rel_tol=1e-9)


def test_charging_respects_capacity_and_curtails(power):
    """A full battery curtails surplus generation. That is expected behaviour, not a
    generation fault, and energy must not exceed capacity."""
    full = power.battery_capacity_wh
    load = LoadRequest(essential_w=100.0, payload_w=0.0, heater_w=0.0)

    result = models.step_electrical(
        power=power,
        energy_wh=full,
        load=load,
        illumination_factor=1.0,
        generation_factor=1.0,
        dt_s=1.0,
    )

    assert result.next_energy_wh <= full + 1e-9
    assert result.charge_bus_w == pytest.approx(0.0, abs=1e-6)
    assert result.curtailed_w > 0
    assert result.solar_dispatched_w == pytest.approx(result.requested_load_w, rel=1e-9)
    assert result.unmet_load_w == 0.0


def test_empty_battery_sheds_optional_load_instead_of_pretending(power):
    """At an empty battery the model must shed declared optional loads and record unmet
    demand, not keep everything powered with SOC pinned at zero."""
    load = LoadRequest(essential_w=100.0, payload_w=80.0, heater_w=30.0)

    result = models.step_electrical(
        power=power,
        energy_wh=0.0,
        load=load,
        illumination_factor=0.0,
        generation_factor=1.0,
        dt_s=1.0,
    )

    assert result.next_energy_wh == 0.0
    assert "payload" in result.shed
    assert "heater" in result.shed
    # Essential demand survives shedding and is reported as unmet, not as served.
    assert result.unmet_load_w == pytest.approx(100.0, rel=1e-6)
    assert result.requested_load_w == pytest.approx(210.0)


def test_energy_never_goes_negative(power):
    energy = 0.5
    load = LoadRequest(essential_w=400.0, payload_w=0.0, heater_w=0.0)
    for _ in range(60):
        energy = models.step_electrical(
            power=power,
            energy_wh=energy,
            load=load,
            illumination_factor=0.0,
            generation_factor=1.0,
            dt_s=1.0,
        ).next_energy_wh
        assert energy >= 0.0


# -------------------------------------------------------------------------- battery


def test_battery_current_sign_convention(power):
    """Positive discharge, negative charge (section 6.1)."""
    _, discharging = models.battery_terminal(power, 500.0, 100.0)
    _, charging = models.battery_terminal(power, 500.0, -100.0)
    assert discharging > 0
    assert charging < 0


def test_terminal_voltage_sags_under_load_and_rises_when_charging(power):
    v_idle, _ = models.battery_terminal(power, 500.0, 0.0)
    v_load, _ = models.battery_terminal(power, 500.0, 200.0)
    v_charge, _ = models.battery_terminal(power, 500.0, -200.0)
    assert v_load < v_idle < v_charge


def test_impossible_battery_power_is_limited_not_returned_as_nonsense(power):
    """Beyond the resistive maximum the solution is limited rather than producing a
    complex or negative-voltage result."""
    voltage, current = models.battery_terminal(power, 1000.0, 1e6)
    assert math.isfinite(voltage) and math.isfinite(current)
    assert voltage > 0


def test_regulated_bus_is_not_battery_terminal_voltage(power):
    """A regulated bus can sit at 28 V while the battery terminal is elsewhere."""
    bus = models.bus_voltage(power, requested_w=180.0, unmet_w=0.0)
    terminal, _ = models.battery_terminal(power, 300.0, 180.0)
    assert bus == pytest.approx(power.bus_target_v)
    assert abs(bus - terminal) > 1.0


def test_bus_sags_only_when_demand_is_unmet(power):
    assert models.bus_voltage(power, 200.0, 0.0) == pytest.approx(power.bus_target_v)
    assert models.bus_voltage(power, 200.0, 100.0) < power.bus_target_v


def test_unilluminated_array_reports_zero_not_open_circuit(power):
    voltage, current = models.solar_array_output(power, 0.0, 20.0, illuminated=False)
    assert voltage == 0.0 and current == 0.0


# -------------------------------------------------------------------------- thermal


def test_explicit_integration_matches_the_analytic_step_response(config):
    """Compare the explicit integration against the closed-form first-order response."""
    node = config.spacecraft.thermal.nodes["battery"]
    boundary_c, heat_in_w, elapsed_s = 10.0, 25.0, 600.0

    temperature = node.initial_c
    steps = int(elapsed_s)
    for _ in range(steps):
        temperature = models.step_node(
            node.heat_capacity_j_per_k,
            node.conductance_w_per_k,
            temperature,
            boundary_c,
            heat_in_w,
            1.0,
        )

    analytic = models.analytic_node_response(
        node.heat_capacity_j_per_k,
        node.conductance_w_per_k,
        node.initial_c,
        boundary_c,
        heat_in_w,
        elapsed_s,
    )
    assert temperature == pytest.approx(analytic, rel=1e-3)


def test_node_reaches_the_expected_equilibrium(config):
    """Equilibrium is boundary + heat_in / conductance, by construction."""
    node = config.spacecraft.thermal.nodes["payload"]
    boundary_c, heat_in_w = 10.0, 16.0
    temperature = node.initial_c
    for _ in range(40000):
        temperature = models.step_node(
            node.heat_capacity_j_per_k,
            node.conductance_w_per_k,
            temperature,
            boundary_c,
            heat_in_w,
            1.0,
        )
    assert temperature == pytest.approx(boundary_c + heat_in_w / node.conductance_w_per_k, rel=1e-4)


def test_substep_count_protects_a_coarse_forecast_step(config):
    """A 1 s tick needs one substep; a 600 s forecast step must be split."""
    thermal = config.spacecraft.thermal
    assert models.thermal_substeps(thermal, 1.0) == 1
    assert models.thermal_substeps(thermal, 600.0) > 1


def test_temperature_does_not_rise_without_energy_input(config):
    """No heat in, above boundary: the node must cool, not drift upwards."""
    node = config.spacecraft.thermal.nodes["computer"]
    temperature = 40.0
    for _ in range(300):
        temperature = models.step_node(
            node.heat_capacity_j_per_k, node.conductance_w_per_k, temperature, 10.0, 0.0, 1.0
        )
    assert temperature < 40.0


# ----------------------------------------------------------------------- propellant


def test_pressure_reference(config):
    prop = config.spacecraft.propellant
    bar = models.tank_pressure_bar(prop, prop.initial_gas_mass_kg, 15.0)
    assert bar == pytest.approx(17.1046, rel=1e-4)


def test_mass_estimate_round_trips_through_pressure(config):
    prop = config.spacecraft.propellant
    bar = models.tank_pressure_bar(prop, 0.15, 20.0)
    assert models.mass_from_pressure(prop, bar, 20.0) == pytest.approx(0.15, rel=1e-9)


def test_consumption_is_clamped_to_available_mass(config):
    prop = config.spacecraft.propellant
    assert models.step_propellant(prop, 0.00005, valve_open=True, dt_s=10.0) == 0.0
    assert models.step_propellant(prop, 0.2, valve_open=False, dt_s=10.0) == 0.2


def test_absolute_zero_is_rejected(config):
    with pytest.raises(ValueError):
        models.tank_pressure_bar(config.spacecraft.propellant, 0.2, -273.15)


# ---------------------------------------------------------------------- environment


def test_illumination_follows_the_configured_cycle(config):
    env = config.spacecraft.environment
    sunlit = environment.evaluate(env, 0.0, 10.0, -5.0)
    eclipse = environment.evaluate(env, 61 * 60.0, 10.0, -5.0)

    assert sunlit.is_sunlit and sunlit.illumination_factor == 1.0
    assert not eclipse.is_sunlit and eclipse.illumination_factor == 0.0
    assert eclipse.boundary_c == -5.0


def test_schedule_boundaries_are_found_inside_a_horizon(config):
    env = config.spacecraft.environment
    boundaries = environment.schedule_boundaries(env, start_s=3000.0, horizon_s=1800.0)
    assert boundaries == [3600.0]


# ----------------------------------------------------------------- coupled twin step


def test_heater_demand_is_counted_in_electrical_load(config):
    """Heater power must appear in total demand exactly once (section 7.2)."""
    spacecraft = config.spacecraft
    truth = SimulatorTruth.initial(spacecraft)
    truth.temperatures_c["battery"] = 0.0  # below the thermostat threshold

    _, result = step(spacecraft, truth, 1.0)

    assert result.heater_actual_on is True
    expected = (
        spacecraft.power.essential_load_w
        + spacecraft.power.payload_nominal_w
        + spacecraft.power.heater_rated_w
    )
    assert result.electrical.requested_load_w == pytest.approx(expected)


def test_thermostat_holds_state_inside_the_deadband(config):
    spacecraft = config.spacecraft
    truth = SimulatorTruth.initial(spacecraft)
    truth.temperatures_c["battery"] = 10.0  # between on-below-8 and off-above-12

    truth.heater_on = True
    _, on_result = step(spacecraft, truth, 1.0)
    truth.heater_on = False
    _, off_result = step(spacecraft, truth, 1.0)

    assert on_result.heater_commanded_on is True
    assert off_result.heater_commanded_on is False


def test_stuck_heater_ignores_the_command(config):
    """The observed actuator state is independent of what was commanded."""
    spacecraft = config.spacecraft
    truth = SimulatorTruth.initial(spacecraft, InjectedConditions(heater_stuck_on=True))
    truth.temperatures_c["battery"] = 25.0  # thermostat would command OFF

    _, result = step(spacecraft, truth, 1.0)

    assert result.heater_commanded_on is False
    assert result.heater_actual_on is True
    assert result.heater_served_w == pytest.approx(spacecraft.power.heater_rated_w)


def test_injected_extra_load_raises_demand_and_drains_the_battery(config):
    spacecraft = config.spacecraft
    baseline = SimulatorTruth.initial(spacecraft)
    loaded = SimulatorTruth.initial(spacecraft, InjectedConditions(extra_load_w=150.0))

    # Run both into eclipse so there is no generation to mask the difference.
    baseline.sim_time_s = loaded.sim_time_s = 61 * 60.0

    _, base_result = step(spacecraft, baseline, 1.0)
    _, load_result = step(spacecraft, loaded, 1.0)

    assert load_result.electrical.requested_load_w == pytest.approx(
        base_result.electrical.requested_load_w + 150.0
    )
    assert load_result.electrical.discharge_bus_w > base_result.electrical.discharge_bus_w


def test_safe_mode_removes_optional_payload_demand(config):
    from app.contracts.common import OperatingMode

    spacecraft = config.spacecraft
    truth = SimulatorTruth.initial(spacecraft, InjectedConditions(mode=OperatingMode.SAFE))
    _, result = step(spacecraft, truth, 1.0)
    assert result.payload_served_w == 0.0


def test_sunlit_run_charges_and_eclipse_discharges(config):
    """End-to-end coherence: the same vehicle gains energy in sunlight and loses it in
    eclipse, with no injected fault."""
    spacecraft = config.spacecraft

    sunlit = SimulatorTruth.initial(spacecraft)
    start = sunlit.energy_wh
    for _ in range(120):
        sunlit, _ = step(spacecraft, sunlit, 1.0)
    assert sunlit.energy_wh > start

    dark = SimulatorTruth.initial(spacecraft)
    dark.sim_time_s = 61 * 60.0
    start = dark.energy_wh
    for _ in range(120):
        dark, _ = step(spacecraft, dark, 1.0)
    assert dark.energy_wh < start
