"""One integration step of the coupled twin.

Advances hidden truth and returns the derived quantities the observation layer needs.
Power, battery and the thermal nodes are fully coupled: heater demand is part of the
electrical load, battery self-heating comes from the current actually solved for, and
payload heat follows the power actually served rather than the power requested.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config.loader import SpacecraftConfig
from app.contracts.common import Illumination, OperatingMode

from . import environment, models, profiles
from .models import ElectricalStep, LoadRequest
from .profiles import ContactState, FlightProfileState
from .truth import SimulatorTruth


@dataclass(frozen=True)
class StepResult:
    """Derived values for one tick. Inputs to observation, never stored as truth."""

    electrical: ElectricalStep
    battery_voltage_v: float
    battery_current_a: float
    bus_voltage_v: float
    solar_voltage_v: float
    solar_current_a: float
    heater_commanded_on: bool
    heater_actual_on: bool
    heater_served_w: float
    payload_served_w: float
    tank_pressure_bar: float
    illumination: Illumination
    seconds_to_illumination_change: float
    boundary_c: float
    computer_powered: bool
    # Flight-systems and link values. PROFILE coverage, not a dynamics model.
    flight: FlightProfileState
    contact: ContactState
    link_snr_db: float | None
    packet_error_rate_percent: float | None
    packets_attempted_window: int
    packets_failed_window: int
    reset_count: int
    reset_events_s: tuple[float, ...]
    # Optional branch and its isolator (section 9.2).
    branch_energised: bool
    branch_w: float
    branch_isolate_commanded: bool
    branch_isolation_effective: bool
    payload_outage_s: float


def _heater_thermostat(config: SpacecraftConfig, battery_c: float, currently_on: bool) -> bool:
    """Battery thermostat with a deadband: on below the low threshold, off above the
    high one, and holding its previous state in between."""
    thermal = config.thermal
    if battery_c < thermal.heater_on_below_c:
        return True
    if battery_c > thermal.heater_off_above_c:
        return False
    return currently_on


def step(
    config: SpacecraftConfig, truth: SimulatorTruth, dt_s: float, seed: int = 0
) -> tuple[SimulatorTruth, StepResult]:
    """Advance `truth` by `dt_s` simulated seconds and return the new state plus derived
    values. `truth` is not mutated; a new instance is returned."""
    power = config.power
    conditions = truth.conditions
    next_sim_time_s = truth.sim_time_s + dt_s

    env = environment.evaluate(
        config.environment,
        truth.sim_time_s,
        config.thermal.boundary_sunlight_c,
        config.thermal.boundary_eclipse_c,
    )

    battery_c = truth.temperatures_c["battery"]
    panel_c = truth.temperatures_c["panel"]

    # Commanded heater state is a known command; the actual state may differ when a
    # stuck actuator is injected. Keeping them separate is what makes the mismatch
    # detectable from observations alone in a later phase.
    heater_commanded_on = (
        _heater_thermostat(config, battery_c, truth.heater_on) if conditions.heater_auto else False
    )
    heater_actual_on = True if conditions.heater_stuck_on else heater_commanded_on

    # SAFE mode applies the declared essential-load policy: optional payload off.
    payload_duty = 0.0 if conditions.mode is OperatingMode.SAFE else conditions.payload_duty_percent
    payload_request_w = power.payload_nominal_w * payload_duty / 100.0

    # The optional branch is energised when it has failed on and the isolator has not
    # actually opened. Commanding isolation is not the same as the isolator obeying:
    # an injected isolator failure leaves the branch drawing power after an APPLIED
    # command, which is what makes "accepted is not recovered" demonstrable.
    branch = power.optional_branch
    branch_isolation_effective = truth.branch_isolate_commanded and not conditions.isolator_failed
    branch_energised = conditions.branch_stuck_on and not branch_isolation_effective
    branch_w = branch.rated_w if branch_energised else 0.0

    load = LoadRequest(
        essential_w=power.essential_load_w,
        payload_w=payload_request_w,
        heater_w=power.heater_rated_w if heater_actual_on else 0.0,
        fault_load_w=conditions.extra_load_w + branch_w,
    )

    electrical = models.step_electrical(
        power=power,
        energy_wh=truth.energy_wh,
        load=load,
        illumination_factor=env.illumination_factor,
        generation_factor=conditions.generation_factor,
        dt_s=dt_s,
    )

    battery_voltage_v, battery_current_a = models.battery_terminal(
        power, truth.energy_wh, electrical.battery_power_w
    )
    bus_v = models.bus_voltage(power, electrical.requested_load_w, electrical.unmet_load_w)
    solar_v, solar_a = models.solar_array_output(
        power, electrical.solar_dispatched_w, panel_c, env.is_sunlit
    )

    payload_shed = "payload" in electrical.shed
    heater_shed = "heater" in electrical.shed
    payload_served_w = 0.0 if payload_shed else payload_request_w
    heater_served_w = 0.0 if (heater_shed or not heater_actual_on) else power.heater_rated_w

    # Essential load is never shed, so any unmet demand means essential supply itself is
    # short and the computer loses power.
    computer_powered = electrical.unmet_load_w <= 1e-9

    nodes = config.thermal.nodes
    heat_inputs = {
        # Series-resistance dissipation plus whatever the heater actually consumed.
        "battery": models.battery_ohmic_loss_w(
            battery_current_a, power.battery_series_resistance_ohm
        )
        + heater_served_w,
        "panel": float(
            getattr(nodes["panel"], "sunlit_w", 60.0)
            if env.is_sunlit
            else getattr(nodes["panel"], "eclipse_w", 0.0)
        ),
        "computer": (
            float(getattr(nodes["computer"], "nominal_w", 15.0)) if computer_powered else 0.0
        ),
        # Payload electronics heat, plus the branch's share when it is energised.
        # Every watt of heat here has a modelled electrical source.
        "payload": payload_served_w * float(getattr(nodes["payload"], "fraction", 0.35))
        + branch_w * branch.heat_fraction_to_payload,
        "tank": float(getattr(nodes["tank"], "nominal_w", 2.0)),
        "thruster": (
            float(getattr(nodes["thruster"], "valve_open_w", 6.0))
            if conditions.valve_open
            else 0.0
        ),
    }

    next_temps = models.step_thermal(
        config.thermal, truth.temperatures_c, heat_inputs, env.boundary_c, dt_s
    )
    next_gas_kg = models.step_propellant(
        config.propellant, truth.gas_mass_kg, conditions.valve_open, dt_s
    )

    # --- flight-systems profiles, link and packet accounting (section 7.5) ---
    flight = profiles.evaluate_flight(
        config.flight_profiles, next_sim_time_s, seed, conditions.mode, dt_s
    )
    contact = profiles.evaluate_contact(
        config.flight_profiles, next_sim_time_s, config.environment_cycle_s
    )
    snr_db = profiles.link_snr_db(config.flight_profiles, next_sim_time_s, seed, contact)
    attempted, failed = profiles.packet_counts(config.flight_profiles, snr_db, dt_s)

    # Keep only the configured rolling window, so the reported error rate always has a
    # stated denominator rather than an open-ended one.
    window_s = float(config.flight_profiles.packet_window_seconds)
    packet_window = [
        sample
        for sample in truth.packet_window
        if next_sim_time_s - sample[0] < window_s
    ]
    if attempted > 0:
        packet_window.append((next_sim_time_s, attempted, failed))
    window_attempted = sum(sample[1] for sample in packet_window)
    window_failed = sum(sample[2] for sample in packet_window)

    # A raised reset count records one timestamped event per increment. The counter is
    # per run: a new run restarts it without implying a physical reset.
    resets_applied = truth.resets_applied
    reset_events_s = list(truth.reset_events_s)
    if conditions.computer_resets > resets_applied:
        resets_applied += 1
        reset_events_s.append(next_sim_time_s)

    # Payload operation that was commanded but not served is lost time. It accumulates
    # irreversibly: a recovery approved later cannot give these seconds back.
    payload_outage_s = truth.payload_outage_s
    if payload_duty > 0.0 and payload_served_w <= 0.0:
        payload_outage_s += dt_s

    next_truth = SimulatorTruth(
        energy_wh=electrical.next_energy_wh,
        temperatures_c=next_temps,
        gas_mass_kg=next_gas_kg,
        heater_on=heater_actual_on,
        # The valve obeys its command in this build: no valve fault is modelled, so
        # commanded and observed agree. Thruster heat, gas consumption and the
        # electrical valve allowance all already read this one field.
        valve_open=conditions.valve_open,
        conditions=conditions,
        tick_seq=truth.tick_seq + 1,
        sim_time_s=next_sim_time_s,
        packet_window=packet_window,
        resets_applied=resets_applied,
        reset_events_s=reset_events_s,
        branch_isolate_commanded=truth.branch_isolate_commanded,
        payload_outage_s=payload_outage_s,
    )

    result = StepResult(
        electrical=electrical,
        battery_voltage_v=battery_voltage_v,
        battery_current_a=battery_current_a,
        bus_voltage_v=bus_v,
        solar_voltage_v=solar_v,
        solar_current_a=solar_a,
        heater_commanded_on=heater_commanded_on,
        heater_actual_on=heater_actual_on,
        heater_served_w=heater_served_w,
        payload_served_w=payload_served_w,
        tank_pressure_bar=models.tank_pressure_bar(
            config.propellant, next_gas_kg, next_temps["tank"]
        ),
        illumination=env.illumination,
        seconds_to_illumination_change=env.seconds_to_next_change,
        boundary_c=env.boundary_c,
        computer_powered=computer_powered,
        flight=flight,
        contact=contact,
        link_snr_db=snr_db,
        packet_error_rate_percent=profiles.packet_error_rate_percent(
            window_attempted, window_failed
        ),
        packets_attempted_window=window_attempted,
        packets_failed_window=window_failed,
        reset_count=resets_applied,
        reset_events_s=tuple(reset_events_s),
        branch_energised=branch_energised,
        branch_w=branch_w,
        branch_isolate_commanded=truth.branch_isolate_commanded,
        branch_isolation_effective=branch_isolation_effective,
        payload_outage_s=payload_outage_s,
    )
    return next_truth, result
