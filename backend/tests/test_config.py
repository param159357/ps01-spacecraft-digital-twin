"""Configuration fixture checks.

These verify the versioned YAML actually parses into the typed contract and that the
numbers behave, so a bad constant is caught here rather than on a dashboard.
"""

from __future__ import annotations

import math

import pytest

from app.config.loader import Group, PredictionCoverage, load_configuration


@pytest.fixture(scope="module")
def config():
    return load_configuration()


def test_channel_dictionary_matches_the_specified_baseline(config):
    """Section 6.1 defines a 28-channel baseline with unique, stable IDs."""
    channels = config.telemetry.channels
    assert len(channels) == 28

    ids = [c.id for c in channels]
    assert len(set(ids)) == 28

    # A few IDs the rest of the system depends on by name.
    for required in ("battery_soc", "load_power", "battery_temperature", "telemetry_age"):
        assert config.telemetry.by_id(required) is not None


def test_every_channel_carries_the_required_metadata(config):
    for channel in config.telemetry.channels:
        assert channel.label and channel.unit and channel.description
        assert channel.groups, f"{channel.id} has no group"
        assert channel.expected_value_method
        # Physical validity bounds must exist for continuous channels so an impossible
        # encoding can be rejected. Booleans legitimately have none.
        if channel.type.value == "float":
            lo, hi = channel.bounds.physical
            assert lo is not None and hi is not None, f"{channel.id} lacks physical bounds"


def test_groups_cover_every_console(config):
    for group in (Group.POWER, Group.THERMAL, Group.FLIGHT):
        assert config.telemetry.for_group(group), f"no channels for {group}"


def test_unmodelled_channels_are_declared_as_such(config):
    """Showing a reading is not a claim that its future behaviour is modelled."""
    profile_only = [
        c.id for c in config.telemetry.channels if c.prediction is PredictionCoverage.PROFILE
    ]
    # The flight-systems channels are explicitly profile-driven in V1 (section 1.2).
    assert "roll_rate" in profile_only
    assert "wheel_speed" in profile_only
    assert config.telemetry.by_id("battery_soc").prediction is PredictionCoverage.MODELLED


def test_freshness_thresholds_are_ordered(config):
    freshness = config.telemetry.freshness
    assert freshness.warning_after_s == 3.0
    assert freshness.critical_after_s == 10.0


def test_propellant_reference_pressure(config):
    """Independent check of the ideal-gas fixture from section 7.4."""
    prop = config.spacecraft.propellant
    pressure_pa = prop.ideal_pressure_pa(prop.initial_gas_mass_kg, prop.initial_temperature_k)
    assert math.isclose(pressure_pa / 1e5, 17.1046, rel_tol=1e-4)


def test_absolute_temperature_must_be_above_zero_kelvin(config):
    with pytest.raises(ValueError):
        config.spacecraft.propellant.ideal_pressure_pa(0.2, 0.0)


def test_battery_energy_reference(config):
    """Section 7.2: a 100 W bus load for 1800 s at 0.95 discharge efficiency draws
    about 52.6316 Wh from the battery."""
    power = config.spacecraft.power
    drawn_wh = (100.0 / power.eta_discharge) * 1800.0 / 3600.0
    assert math.isclose(drawn_wh, 52.6316, rel_tol=1e-5)


def test_thermal_nodes_have_usable_time_constants(config):
    """The integration step must be small relative to the fastest node."""
    thermal = config.spacecraft.thermal
    assert set(thermal.nodes) >= {"battery", "panel", "computer", "payload", "tank", "thruster"}
    assert thermal.min_time_constant_s > config.spacecraft.simulation.tick_seconds * 10


def test_thermal_limits_are_not_copied_between_nodes(config):
    """Section 7.3: each node gets its own limits, not the battery's."""
    battery = config.telemetry.by_id("battery_temperature").bounds.operating
    panel = config.telemetry.by_id("panel_temperature").bounds.operating
    assert battery != panel


def test_rules_and_actions_report_honest_coverage(config):
    """Coverage exposes only rules and actions that are actually implemented and tested.

    Section 8.1 names the first six rules as the core demonstration set; Phase 5 completed
    that set by adding ACTUATOR_MISMATCH. The remaining four stay false until they exist.
    """
    assert len(config.rules.rules) == 10
    assert config.rules.implemented_ids == (
        "DATA_STALE",
        "GENERATION_SHORTFALL",
        "ENERGY_RESERVE",
        "THERMAL_LIMIT",
        "ACTUATOR_MISMATCH",
        "SUPPLY_SHORTAGE",
    )
    assert config.actions.enabled_ids == (
        "REDUCE_PAYLOAD_POWER",
        "ISOLATE_OPTIONAL_BRANCH",
    )
    # Still unimplemented, and still declared as such.
    for rule_id in (
        "PRESSURE_INCONSISTENCY",
        "FLIGHT_PROFILE_DEVIATION",
        "RESET_RECURRENCE",
        "SENSOR_INCONSISTENCY",
    ):
        assert config.rules.by_id(rule_id).implemented is False

    core_demo_actions = {"REDUCE_PAYLOAD_POWER", "ISOLATE_OPTIONAL_BRANCH"}
    assert core_demo_actions <= {a.id for a in config.actions.actions}


def test_cross_references_resolve(config):
    """Channel rule_refs and thermal_node references are validated at load time."""
    known_rules = {r.id for r in config.rules.rules}
    for channel in config.telemetry.channels:
        assert set(channel.rule_refs) <= known_rules


def test_combined_config_version_is_reported(config):
    version = config.config_version
    for part in ("spacecraft-", "telemetry-", "rules-", "actions-"):
        assert part in version
