"""Phase 4 checks — profile channels, contact windows and role-scoped incidents.

These verify the two things Phase 4 could most easily get wrong: inventing plausible
numbers for channels that have no model, and widening what a specialist console can see.
"""

from __future__ import annotations

import pytest

from app.config.loader import PredictionCoverage, load_configuration
from app.contracts.common import OperatingMode, Role, Subsystem, may_act_on
from app.simulation import profiles
from app.simulation.truth import InjectedConditions, SimulatorTruth
from app.simulation.twin import step
from tests.conftest import login, logout


@pytest.fixture(scope="module")
def config():
    return load_configuration()


# ------------------------------------------------------------------ profile channels


def test_profiles_are_deterministic_for_a_given_seed(config):
    """A scenario must replay identically, so a profile is a pure function of time."""
    fp = config.spacecraft.flight_profiles
    first = profiles.evaluate_flight(fp, 1234.0, 42, OperatingMode.NOMINAL)
    again = profiles.evaluate_flight(fp, 1234.0, 42, OperatingMode.NOMINAL)
    other_seed = profiles.evaluate_flight(fp, 1234.0, 43, OperatingMode.NOMINAL)

    assert first == again
    assert first != other_seed


def test_profile_values_stay_inside_their_configured_operating_limits(config):
    """A profile that wandered outside its own limits would open permanent false
    incidents the moment FLIGHT_PROFILE_DEVIATION is implemented."""
    fp = config.spacecraft.flight_profiles
    telemetry = config.telemetry

    for sim_time in range(0, 5400, 37):
        state = profiles.evaluate_flight(fp, float(sim_time), 20260923, OperatingMode.NOMINAL)
        for channel_id, value in (
            ("pointing_error", state.pointing_error_deg),
            ("roll_rate", state.roll_rate_dps),
            ("pitch_rate", state.pitch_rate_dps),
            ("yaw_rate", state.yaw_rate_dps),
            ("wheel_speed", state.wheel_speed_rpm),
            ("wheel_current", state.wheel_current_a),
            ("cell_voltage_spread", state.cell_voltage_spread_v),
        ):
            low, high = telemetry.by_id(channel_id).bounds.operating
            if low is not None:
                assert value >= low, f"{channel_id}={value} below {low} at t={sim_time}"
            if high is not None:
                assert value <= high, f"{channel_id}={value} above {high} at t={sim_time}"


def test_maneuver_mode_widens_rates_by_design(config):
    """A commanded manoeuvre is a known context change, not a fault."""
    fp = config.spacecraft.flight_profiles
    nominal = profiles.evaluate_flight(fp, 800.0, 7, OperatingMode.NOMINAL)
    maneuver = profiles.evaluate_flight(fp, 800.0, 7, OperatingMode.MANEUVER)
    assert abs(maneuver.roll_rate_dps) > abs(nominal.roll_rate_dps)


def test_flight_channels_are_declared_profile_not_modelled(config):
    """Showing a reading is not a claim its future behaviour is physically modelled."""
    for channel_id in ("pointing_error", "roll_rate", "wheel_speed", "link_snr"):
        assert config.telemetry.by_id(channel_id).prediction is PredictionCoverage.PROFILE


# -------------------------------------------------------------------- contact window


def test_link_is_unavailable_outside_a_scheduled_pass(config):
    """Outside a pass there is nothing to measure. `None` means no measurement; a low
    number would mean a bad link, which is a different claim."""
    fp = config.spacecraft.flight_profiles
    cycle = config.spacecraft.environment_cycle_s

    inside = profiles.evaluate_contact(fp, 600.0, cycle)
    outside = profiles.evaluate_contact(fp, 3000.0, cycle)

    assert inside.expected is True
    assert outside.expected is False
    assert profiles.link_snr_db(fp, 600.0, 1, inside) is not None
    assert profiles.link_snr_db(fp, 3000.0, 1, outside) is None


def test_packet_error_rate_is_unavailable_with_a_zero_denominator(config):
    """Section 7.5: return unavailable when nothing was attempted, never zero percent."""
    fp = config.spacecraft.flight_profiles
    outside = profiles.evaluate_contact(fp, 3000.0, config.spacecraft.environment_cycle_s)
    snr = profiles.link_snr_db(fp, 3000.0, 1, outside)
    attempted, failed = profiles.packet_counts(fp, snr, 1.0)

    assert attempted == 0
    assert profiles.packet_error_rate_percent(attempted, failed) is None
    assert profiles.packet_error_rate_percent(120, 6) == pytest.approx(5.0)


def test_packet_window_is_bounded_by_the_configured_window(config):
    """The reported ratio must always have a stated, bounded denominator."""
    spacecraft = config.spacecraft
    truth = SimulatorTruth.initial(spacecraft)
    result = None
    for _ in range(200):
        truth, result = step(spacecraft, truth, 1.0, spacecraft.simulation.seed)

    window_s = spacecraft.flight_profiles.packet_window_seconds
    assert len(truth.packet_window) <= window_s
    expected_max = spacecraft.flight_profiles.packets_per_second * window_s
    assert result.packets_attempted_window <= expected_max


# --------------------------------------------------------------------------- resets


def test_reset_counter_records_timestamped_events(config):
    """A cumulative counter plus distinct timestamped events (section 7.5)."""
    spacecraft = config.spacecraft
    truth = SimulatorTruth.initial(spacecraft, InjectedConditions(computer_resets=2))

    result = None
    for _ in range(5):
        truth, result = step(spacecraft, truth, 1.0, spacecraft.simulation.seed)

    assert result.reset_count == 2
    assert len(result.reset_events_s) == 2
    # Distinct events, not one event counted twice.
    assert len(set(result.reset_events_s)) == 2


def test_a_new_run_restarts_the_counter_without_a_physical_reset(config):
    fresh = SimulatorTruth.initial(config.spacecraft)
    assert fresh.resets_applied == 0
    assert fresh.reset_events_s == []


# ----------------------------------------------------------------- role scoping (API)


def test_specialists_only_see_incidents_in_their_own_subsystem(client, clean_runs):
    """A power incident must not appear on the flight console's list."""
    login(client, "master")
    master_body = client.get("/api/incidents").json()
    assert master_body["role"] == "MASTER"
    assert master_body["hidden_from_this_role"] == 0
    logout(client)

    login(client, "flight")
    flight_body = client.get("/api/incidents").json()
    logout(client)

    login(client, "power")
    power_body = client.get("/api/incidents").json()

    # Whatever is active, each specialist list contains only its own subsystem.
    for body, subsystem in (
        (flight_body, "FLIGHT_SYSTEMS"),
        (power_body, "POWER"),
    ):
        for incident in body["incidents"]:
            assert incident["subsystem"] == subsystem

    total = len(master_body["incidents"])
    assert flight_body["hidden_from_this_role"] == total - len(flight_body["incidents"])


def test_role_subsystem_matrix_matches_the_specification():
    """Section 3: each specialist acts only within its assigned subsystem."""
    assert may_act_on(Role.POWER, Subsystem.POWER)
    assert not may_act_on(Role.POWER, Subsystem.FLIGHT_SYSTEMS)
    assert not may_act_on(Role.FLIGHT_SYSTEMS, Subsystem.THERMAL_PROPULSION)
    for subsystem in Subsystem:
        assert may_act_on(Role.MASTER, subsystem)


def test_every_console_route_serves_its_own_channel_scope(client, clean_runs):
    """All five roles resolve, and each sees its declared channel scope — no more."""
    expected_home = {
        "simulator": "/simulator/",
        "master": "/master/",
        "power": "/power/",
        "thermal": "/thermal/",
        "flight": "/flight/",
    }
    counts: dict[str, int] = {}
    for username, home in expected_home.items():
        body = login(client, username)
        assert body["user"]["home_route"] == home
        counts[username] = len(client.get("/api/config/channels").json()["channels"])
        logout(client)

    assert counts["master"] == 28
    for specialist in ("power", "thermal", "flight"):
        assert counts[specialist] < counts["master"], specialist
