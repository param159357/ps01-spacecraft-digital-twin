"""Phase 8: the section 13.2 checks that no earlier phase exercised.

Everything here was written by working down section 13.2 and asking what the suite did
*not* already prove. Four gaps came out of that: rule hysteresis, rule persistence,
stale-data handling, and expired proposal rejection.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config.loader import load_configuration
from app.contracts.actions import ActionProposal
from app.contracts.common import (
    Illumination,
    OperatingMode,
    QualityState,
    RunStatus,
    SourceKind,
    Subsystem,
)
from app.contracts.incidents import Lifecycle, Severity
from app.contracts.telemetry import ChannelQuality, FrameContext, TelemetryFrame
from app.monitoring.engine import IncidentEngine
from app.operations import OperationalService

RUN_ID = uuid4()


def _frame_at(
    sim_time_s: float,
    *,
    frame_seq: int,
    values: dict[str, float],
    contact_expected: bool = True,
) -> TelemetryFrame:
    """A minimal valid frame carrying exactly the channels a rule needs.

    Built directly rather than through the twin so a single channel can be walked across
    a limit without the rest of the vehicle moving with it.
    """
    config = load_configuration()
    now = datetime(2026, 9, 23, 12, tzinfo=UTC) + timedelta(seconds=sim_time_s)
    quality = {
        channel: ChannelQuality(state=QualityState.VALID, source_time_utc=now) for channel in values
    }
    return TelemetryFrame(
        run_id=RUN_ID,
        frame_seq=frame_seq,
        tick_seq=frame_seq,
        sim_time_s=sim_time_s,
        sim_epoch_utc=datetime(2026, 9, 23, tzinfo=UTC),
        received_at_utc=now,
        values=dict(values),
        quality=quality,
        context=FrameContext(
            vehicle_config_version=config.spacecraft.config_version,
            model_version=config.spacecraft.model_version,
            run_id=RUN_ID,
            source=SourceKind.LIVE,
            run_status=RunStatus.RUNNING,
            mode=OperatingMode.NOMINAL,
            illumination=Illumination.SUNLIGHT,
            contact_expected=contact_expected,
            control_revision=0,
            known_commands={"heater_auto": True, "payload_duty_percent": 100.0},
        ),
    )


def _payload_limits() -> tuple[float, float]:
    config = load_configuration()
    lo, hi = config.telemetry.by_id("payload_temperature").bounds.operating
    assert lo is not None and hi is not None
    return float(lo), float(hi)


def _drive(engine: IncidentEngine, series: list[tuple[float, float]]) -> list[str]:
    """Feed (sim_time_s, payload_temperature) and return the lifecycle after each frame."""
    states: list[str] = []
    for index, (sim_time_s, temperature_c) in enumerate(series, start=1):
        engine.evaluate(
            _frame_at(
                sim_time_s,
                frame_seq=index,
                values={"payload_temperature": temperature_c},
            ),
            False,
        )
        thermal = [
            incident
            for incident in engine.incidents.values()
            if incident.family == "THERMAL" and incident.affected_entity == "payload_temperature"
        ]
        states.append(thermal[0].lifecycle.value if thermal else "NONE")
    return states


def test_a_transient_excursion_does_not_open_an_incident():
    """Section 8.1 step 4: configured limits are evaluated with persistence."""
    _, hi = _payload_limits()
    engine = IncidentEngine(load_configuration())
    # Over the limit for one second, then back. THERMAL_LIMIT requires 10 s.
    states = _drive(engine, [(0.0, hi - 5.0), (1.0, hi + 3.0), (2.0, hi - 5.0), (3.0, hi - 5.0)])
    assert states == ["NONE", "NONE", "NONE", "NONE"]


def test_a_persistent_excursion_does_open_an_incident():
    _, hi = _payload_limits()
    engine = IncidentEngine(load_configuration())
    states = _drive(engine, [(float(t), hi + 3.0) for t in range(0, 14)])
    assert states[0] == "NONE"  # still inside the persistence window
    assert states[-1] == "OPEN"


def test_a_thermal_incident_does_not_flap_around_its_limit():
    """Section 8.2: the recovery boundary is below the warning boundary.

    `rules.yaml` configures `hysteresis_c: 2.0` with the comment "40 C warning recovers
    below 38 C". Without it, a temperature hovering on the limit re-crosses on every
    sample and the incident oscillates OPEN/RECOVERING, which is exactly the flapping
    alarm section 8.2 asks to suppress.
    """
    _, hi = _payload_limits()
    engine = IncidentEngine(load_configuration())
    hold = [(float(t), hi + 3.0) for t in range(0, 12)]  # open it
    # Now sit just below the limit but well inside the hysteresis band.
    hover = [(float(t), hi - 0.5) for t in range(12, 18)]
    states = _drive(engine, hold + hover)
    assert states[len(hold) - 1] == "OPEN"
    assert set(states[len(hold) :]) == {"OPEN"}, (
        f"incident left OPEN inside the hysteresis band: {states[len(hold):]}"
    )


def test_a_thermal_incident_recovers_once_it_clears_the_hysteresis_band():
    _, hi = _payload_limits()
    engine = IncidentEngine(load_configuration())
    hold = [(float(t), hi + 3.0) for t in range(0, 12)]
    clear = [(float(t), hi - 5.0) for t in range(12, 18)]
    states = _drive(engine, hold + clear)
    assert states[len(hold) - 1] == "OPEN"
    assert states[len(hold)] == "RECOVERING"


def test_stale_telemetry_opens_an_incident_during_expected_contact():
    """Section 8.1: no valid frame during expected contact is a missing-data incident."""
    engine = IncidentEngine(load_configuration())
    frame = _frame_at(100.0, frame_seq=1, values={"battery_soc": 60.0})
    assert engine.evaluate_stale(frame, 1.0) is None  # inside the 3 s warning threshold
    opened = None
    for age_s in (3.5, 4.5, 5.5, 6.5):
        opened = engine.evaluate_stale(
            frame.model_copy(update={"sim_time_s": 100.0 + age_s}), age_s
        )
    assert opened is not None
    assert opened.rule_id == "DATA_STALE"
    assert opened.subsystem is Subsystem.FLIGHT_SYSTEMS
    assert opened.severity is Severity.WARNING
    assert opened.lifecycle is Lifecycle.OPEN


def test_stale_telemetry_escalates_to_critical_and_never_reads_as_healthy():
    engine = IncidentEngine(load_configuration())
    frame = _frame_at(200.0, frame_seq=2, values={"battery_soc": 60.0})
    incident = None
    for age_s in (3.5, 5.0, 8.0, 11.0, 12.0):
        result = engine.evaluate_stale(
            frame.model_copy(update={"sim_time_s": 200.0 + age_s}), age_s
        )
        incident = result or incident
    assert incident is not None
    assert incident.severity is Severity.CRITICAL
    # Missing data must not satisfy healthy dwell (section 8.2).
    assert incident.lifecycle is Lifecycle.OPEN


def test_an_expected_contact_gap_does_not_open_a_stale_incident():
    """Section 7.5: an expected contact gap is not a radio failure."""
    engine = IncidentEngine(load_configuration())
    frame = _frame_at(300.0, frame_seq=3, values={"battery_soc": 60.0}, contact_expected=False)
    for age_s in (4.0, 8.0, 20.0, 60.0):
        engine.evaluate_stale(frame.model_copy(update={"sim_time_s": 300.0 + age_s}), age_s)
    assert not [i for i in engine.incidents.values() if i.rule_id == "DATA_STALE"]


def test_an_expired_proposal_is_rejected_and_a_control_change_forces_a_new_comparison():
    """Section 9.3: a proposal carries a TTL, and relevant control changes invalidate it.

    Neither path had a test. The expiry path is the one an operator actually meets: a
    comparison left on screen while the vehicle moves on must not execute later.
    """
    config = load_configuration()
    service = OperationalService(config, SimpleNamespace(publish=lambda *a, **k: None))
    run_id = uuid4()
    proposal = ActionProposal(
        id=uuid4(),
        run_id=run_id,
        incident_id=uuid4(),
        action_id="REDUCE_PAYLOAD_POWER",
        action_arguments={"duty_percent": 0.0},
        evidence_frame_seq=1,
        control_revision=0,
        created_at_utc=datetime.now(UTC) - timedelta(seconds=60),
        expires_at_utc=datetime.now(UTC) - timedelta(seconds=30),
        trade_off="test",
        baseline_summary="test",
        action_summary="test",
        assumptions=("test",),
    )
    service.proposals[proposal.id] = proposal
    service.latest_estimate = SimpleNamespace(run_id=run_id, control_revision=0)

    with pytest.raises(TimeoutError):
        service.accept_command(proposal.id, "master", "key-expired")

    # A live proposal whose control revision has moved on is refused separately, so the
    # operator compares again rather than approving against stale prerequisites.
    fresh = proposal.model_copy(
        update={
            "id": uuid4(),
            "created_at_utc": datetime.now(UTC),
            "expires_at_utc": datetime.now(UTC) + timedelta(seconds=30),
        }
    )
    service.proposals[fresh.id] = fresh
    service.latest_estimate = SimpleNamespace(run_id=run_id, control_revision=1)
    with pytest.raises(RuntimeError):
        service.accept_command(fresh.id, "master", "key-stale-controls")
