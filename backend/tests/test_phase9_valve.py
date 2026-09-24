"""The cold-gas valve is now commandable, and the existing physics responds.

Nothing here adds a model. `step_propellant`, the thruster node's configured actuator
heat and the ideal-gas pressure relation were all written and tested in earlier phases;
they were simply unreachable because no control set `valve_open`. These checks confirm
the wiring, and that commanded state is published as a known command rather than hidden.
"""

from __future__ import annotations

from dataclasses import replace

from app.config.loader import load_configuration
from app.simulation.truth import ConditionUpdate, InjectedConditions, SimulatorTruth
from app.simulation.twin import step


def _run(valve_open: bool, ticks: int = 60) -> SimulatorTruth:
    config = load_configuration()
    truth = SimulatorTruth.initial(
        config.spacecraft, InjectedConditions(valve_open=valve_open)
    )
    for _ in range(ticks):
        truth, _ = step(config.spacecraft, truth, 1.0)
    return truth


def test_an_open_valve_consumes_gas_and_a_closed_one_does_not():
    config = load_configuration()
    start = config.spacecraft.propellant.initial_gas_mass_kg

    closed = _run(False)
    opened = _run(True)

    assert closed.gas_mass_kg == start, "a closed valve must not consume gas"
    assert opened.gas_mass_kg < start
    # 60 s at the configured nominal flow, to the model's own numbers.
    expected = start - config.spacecraft.propellant.nominal_valve_flow_kg_per_s * 60
    assert abs(opened.gas_mass_kg - expected) < 1e-9


def test_pressure_follows_the_mass_it_lost():
    """Pressure is the ideal-gas relation, not an independent reading."""
    config = load_configuration()
    prop = config.spacecraft.propellant

    closed = _run(False)
    opened = _run(True)

    def pressure_bar(truth: SimulatorTruth) -> float:
        kelvin = truth.temperatures_c["tank"] + 273.15
        return prop.ideal_pressure_pa(truth.gas_mass_kg, kelvin) / 1e5

    # Less gas at a comparable temperature means less pressure.
    assert pressure_bar(opened) < pressure_bar(closed)


def test_an_open_valve_warms_the_thruster_node():
    """The configured actuator heat reaches the thruster node only when open."""
    closed = _run(False)
    opened = _run(True)
    assert opened.temperatures_c["thruster"] > closed.temperatures_c["thruster"]


def test_the_valve_is_a_known_command_and_the_observed_state_follows_it():
    config = load_configuration()
    truth = SimulatorTruth.initial(config.spacecraft, InjectedConditions())
    assert truth.conditions.known_commands["valve_open_commanded"] is False

    truth = replace(
        truth, conditions=ConditionUpdate(valve_open=True).apply_to(truth.conditions)
    )
    truth, _ = step(config.spacecraft, truth, 1.0)

    # Commanded state is operator-visible context, not hidden truth.
    assert truth.conditions.known_commands["valve_open_commanded"] is True
    assert "valve_open" not in truth.conditions.hidden_fields
    # Observed valve status follows the command: no valve fault is modelled.
    assert truth.valve_open is True


def test_gas_consumption_stops_at_an_empty_tank():
    """Clamped to the mass actually available; never negative."""
    config = load_configuration()
    truth = SimulatorTruth.initial(config.spacecraft, InjectedConditions(valve_open=True))
    truth = replace(
        truth, gas_mass_kg=config.spacecraft.propellant.nominal_valve_flow_kg_per_s * 1.5
    )
    for _ in range(5):
        truth, _ = step(config.spacecraft, truth, 1.0)
    assert truth.gas_mass_kg == 0.0
