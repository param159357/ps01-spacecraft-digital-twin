"""Phase 5 checks — the stuck optional branch, isolation, and failed recovery.

The point of this phase is that a command can be accepted, applied, and still not help.
These tests exist to stop that distinction quietly collapsing into "approved means fixed".
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.config.loader import load_configuration
from app.contracts.common import (
    Illumination,
    OperatingMode,
    QualityState,
    RunStatus,
    SourceKind,
)
from app.contracts.telemetry import ChannelQuality, FrameContext, TelemetryFrame
from app.forecasts.engine import forecast
from app.forecasts.estimator import ObservedStateEstimate
from app.monitoring.engine import IncidentEngine
from app.simulation.truth import InjectedConditions, SimulatorTruth
from app.simulation.twin import step


@pytest.fixture(scope="module")
def config():
    return load_configuration()


def _run(config, truth, seconds: int):
    result = None
    for _ in range(seconds):
        truth, result = step(config.spacecraft, truth, 1.0, config.spacecraft.simulation.seed)
    return truth, result


# ------------------------------------------------------------- the optional branch


def test_stuck_branch_raises_demand_and_heats_the_payload_node(config):
    """D04: the branch must produce both power and thermal evidence, and every watt of
    heat must have a modelled electrical source."""
    spacecraft = config.spacecraft
    branch = spacecraft.power.optional_branch

    quiet, quiet_result = _run(config, SimulatorTruth.initial(spacecraft), 120)
    stuck, stuck_result = _run(
        config, SimulatorTruth.initial(spacecraft, InjectedConditions(branch_stuck_on=True)), 120
    )

    assert stuck_result.branch_energised is True
    assert stuck_result.branch_w == pytest.approx(branch.rated_w)
    # Power evidence: demand rises by exactly the branch's rated power.
    assert stuck_result.electrical.requested_load_w == pytest.approx(
        quiet_result.electrical.requested_load_w + branch.rated_w
    )
    # Thermal evidence: the payload node runs hotter because of that same power.
    assert stuck.temperatures_c["payload"] > quiet.temperatures_c["payload"]


def test_isolation_removes_the_branch_load_and_its_heat(config):
    """Isolation must remove demand *and* the heat it was producing."""
    spacecraft = config.spacecraft
    truth = SimulatorTruth.initial(spacecraft, InjectedConditions(branch_stuck_on=True))
    truth, hot = _run(config, truth, 300)
    peak_payload_c = truth.temperatures_c["payload"]

    truth.branch_isolate_commanded = True
    truth, isolated = _run(config, truth, 300)

    assert isolated.branch_isolation_effective is True
    assert isolated.branch_energised is False
    assert isolated.branch_w == 0.0
    assert isolated.electrical.requested_load_w < hot.electrical.requested_load_w
    # The temperature trend recovers rather than merely stopping.
    assert truth.temperatures_c["payload"] < peak_payload_c


def test_isolation_never_touches_essential_supply(config):
    """Section 9.2: essential power cannot be isolated."""
    spacecraft = config.spacecraft
    truth = SimulatorTruth.initial(spacecraft, InjectedConditions(branch_stuck_on=True))
    truth.branch_isolate_commanded = True
    _, result = _run(config, truth, 10)

    assert result.electrical.requested_load_w >= spacecraft.power.essential_load_w
    assert result.computer_powered is True


def test_a_failed_isolator_applies_the_command_and_changes_nothing(config):
    """The core of this phase: APPLIED is not recovered.

    The isolator is commanded and does not respond, so demand stays exactly where it was.
    Nothing here may quietly pretend the branch was isolated.
    """
    spacecraft = config.spacecraft
    conditions = InjectedConditions(branch_stuck_on=True, isolator_failed=True)
    truth = SimulatorTruth.initial(spacecraft, conditions)
    truth, before = _run(config, truth, 60)

    truth.branch_isolate_commanded = True
    truth, after = _run(config, truth, 60)

    assert after.branch_isolate_commanded is True   # the ground did command it
    assert after.branch_isolation_effective is False  # the isolator did not obey
    assert after.branch_energised is True
    assert after.branch_w == pytest.approx(spacecraft.power.optional_branch.rated_w)
    assert after.electrical.requested_load_w == pytest.approx(
        before.electrical.requested_load_w
    )


def test_software_isolation_does_not_repair_the_branch(config):
    """Isolating removes the load; the branch is still failed underneath.

    Releasing the isolator must bring the fault straight back, because nothing repaired it.
    """
    spacecraft = config.spacecraft
    truth = SimulatorTruth.initial(spacecraft, InjectedConditions(branch_stuck_on=True))
    truth.branch_isolate_commanded = True
    truth, isolated = _run(config, truth, 30)
    assert isolated.branch_energised is False

    truth.branch_isolate_commanded = False
    _, released = _run(config, truth, 30)
    assert released.branch_energised is True


# ------------------------------------------------------- irreversible consequence


def test_payload_outage_accumulates_and_cannot_be_undone(config):
    """D03: a later recovery cannot give back operation that was already missed."""
    spacecraft = config.spacecraft
    # An empty battery in eclipse forces the declared load shedding to drop the payload.
    truth = SimulatorTruth.initial(spacecraft, InjectedConditions(payload_duty_percent=100.0))
    truth.energy_wh = 0.0
    truth.sim_time_s = 61 * 60.0

    truth, result = _run(config, truth, 30)
    lost = truth.payload_outage_s
    assert lost > 0
    assert result.payload_served_w == 0.0

    # Full recovery: battery restored, nothing shed any more.
    truth.energy_wh = spacecraft.power.battery_capacity_wh * 0.8
    truth.sim_time_s = 0.0
    truth, healthy = _run(config, truth, 30)

    assert healthy.payload_served_w > 0
    # The already-lost seconds are still on the record and have not decreased.
    assert truth.payload_outage_s >= lost


def test_low_battery_alone_does_not_claim_permanent_damage(config):
    """D03 must not assert irreversible battery damage from low charge.

    The battery recharges normally afterwards; no damage state exists in the model.
    """
    spacecraft = config.spacecraft
    truth = SimulatorTruth.initial(spacecraft)
    truth.energy_wh = 0.0
    truth, _ = _run(config, truth, 60)
    assert truth.energy_wh >= 0.0

    # Back in sunlight it charges again, exactly as an undamaged battery would.
    truth.sim_time_s = 0.0
    truth.conditions = InjectedConditions(payload_duty_percent=0.0)
    before = truth.energy_wh
    truth, _ = _run(config, truth, 120)
    assert truth.energy_wh > before


# --------------------------------------------------------------- actuator mismatch


def _frame_with(config, heater_w: float, commanded_on: bool) -> TelemetryFrame:
    now = datetime.now(UTC)
    quality = ChannelQuality(state=QualityState.VALID, source_time_utc=now)
    return TelemetryFrame(
        run_id=uuid4(),
        frame_seq=10,
        tick_seq=10,
        sim_time_s=100.0,
        sim_epoch_utc=now,
        received_at_utc=now,
        values={"heater_power": heater_w},
        quality={"heater_power": quality},
        context=FrameContext(
            vehicle_config_version=config.config_version,
            model_version=config.spacecraft.model_version,
            run_id=uuid4(),
            source=SourceKind.LIVE,
            run_status=RunStatus.RUNNING,
            mode=OperatingMode.NOMINAL,
            illumination=Illumination.SUNLIGHT,
            contact_expected=True,
            control_revision=1,
            known_commands={"heater_commanded_on": commanded_on},
        ),
    )


def test_actuator_mismatch_needs_only_observations_and_known_commands(config):
    """The rule compares a known command with a measured channel — no simulator truth."""
    engine = IncidentEngine(config)
    frame = _frame_with(config, heater_w=30.0, commanded_on=False)

    # Persistence: the rule must not fire on a single sample.
    engine.evaluate(frame)
    later = frame.model_copy(update={"sim_time_s": 120.0, "frame_seq": 30})
    engine.evaluate(later)

    mismatches = [
        i for i in engine.incidents.values() if i.rule_id == "ACTUATOR_MISMATCH" and i.is_active
    ]
    assert mismatches, "a heater drawing power while commanded off must be reported"
    assert "commanded OFF" in mismatches[0].observed_evidence[-1].text


def test_actuator_mismatch_stays_quiet_when_command_and_observation_agree(config):
    engine = IncidentEngine(config)
    for sim_time, seq in ((100.0, 10), (120.0, 30)):
        frame = _frame_with(config, heater_w=0.0, commanded_on=False)
        engine.evaluate(frame.model_copy(update={"sim_time_s": sim_time, "frame_seq": seq}))

    assert not [
        i for i in engine.incidents.values() if i.rule_id == "ACTUATOR_MISMATCH" and i.is_active
    ]


# ----------------------------------------------------------------- action catalogue


def test_isolation_forecast_projects_the_branch_load_away(config):
    """The alternative must start from the same observed state as the baseline."""
    spacecraft = config.spacecraft
    estimate = ObservedStateEstimate(
        run_id=uuid4(),
        frame_seq=50,
        sim_time_s=61 * 60.0,  # eclipse, so the difference is not masked by generation
        control_revision=1,
        energy_wh=300.0,
        load_w=spacecraft.power.essential_load_w + spacecraft.power.optional_branch.rated_w,
        solar_w=0.0,
        generation_factor=1.0,
        battery_current_a=10.0,
        battery_temperature_c=20.0,
        payload_temperature_c=25.0,
        heater_power_w=0.0,
        payload_duty_percent=0.0,
        illumination=Illumination.ECLIPSE,
    )

    baseline = forecast(estimate, spacecraft)
    isolated = forecast(estimate, spacecraft, isolate_branch=True)

    assert baseline.source_frame_seq == isolated.source_frame_seq
    assert baseline.series[0].values[0] == isolated.series[0].values[0]
    # Less demand means more reserve left at the horizon.
    assert isolated.series[0].values[-1] > baseline.series[0].values[-1]
    assert isolated.action_id == "ISOLATE_OPTIONAL_BRANCH"
    assert any("isolator responds" in a for a in isolated.assumptions)


def test_only_enabled_actions_may_be_offered(config):
    """A disabled action must never appear as an available fix (section 9.2)."""
    assert config.actions.enabled_ids == ("REDUCE_PAYLOAD_POWER", "ISOLATE_OPTIONAL_BRANCH")
    for action in config.actions.actions:
        if action.id not in config.actions.enabled_ids:
            assert action.enabled is False


# ------------------------------------------------- regression: shedding must publish


def test_load_shedding_still_produces_a_valid_frame(config):
    """A frame must still be built when loads are actually being shed.

    This is a regression guard for a real defect: `engineering` was typed as numeric
    only, while `shed_loads` and `reset_events_s` carry short labels. Both are None
    until something is actually shed or a reset actually happens, so frame construction
    threw on the very first load-shedding tick and telemetry stopped reaching every
    console while the simulation kept advancing. Nothing below may regress to a
    numeric-only engineering payload.
    """
    from app.simulation import observation

    spacecraft = config.spacecraft
    truth = SimulatorTruth.initial(
        spacecraft, InjectedConditions(payload_duty_percent=100.0, computer_resets=1)
    )
    truth.energy_wh = 0.0
    truth.sim_time_s = 61 * 60.0  # eclipse: no generation to cover demand

    noise = observation.SensorNoise(1)
    frame = None
    for _ in range(5):
        truth, result = step(spacecraft, truth, 1.0, spacecraft.simulation.seed)
        frame = observation.observe(
            config=spacecraft,
            telemetry=config.telemetry,
            truth=truth,
            result=result,
            noise=noise,
            run_id=uuid4(),
            frame_seq=1,
            sim_epoch_utc=datetime.now(UTC),
            received_at_utc=datetime.now(UTC),
            run_status=RunStatus.RUNNING,
            control_revision=1,
            source=SourceKind.LIVE,
            seconds_since_previous_frame=1.0,
        )

    assert frame is not None
    # Loads really were shed, and a reset really was recorded, so both label fields are
    # populated rather than sitting at None where the type error could not show.
    assert isinstance(frame.engineering["shed_loads"], str)
    assert frame.engineering["shed_loads"]
    assert isinstance(frame.engineering["reset_events_s"], str)
    # And the frame still serializes, which is what the stream and storage both need.
    assert frame.model_dump(mode="json")["engineering"]["shed_loads"]
