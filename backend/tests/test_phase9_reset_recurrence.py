"""RESET_RECURRENCE, derived only from observed telemetry.

Both inputs are things a ground team actually receives: `computer_reset_count` is a
channel, and the event times are published in the frame's engineering detail, which the
flight console already displays. The hidden `computer_resets` injection is never read —
these tests build frames directly and never construct a simulator condition, so a rule
that peeked at truth could not pass them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.config.loader import load_configuration
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
from app.monitoring.engine import IncidentEngine, _distinct_event_times

RUN_ID = uuid4()


def _frame(
    sim_time_s: float,
    *,
    count: int | None,
    events: str | None,
    valid: bool = True,
    frame_seq: int = 1,
) -> TelemetryFrame:
    config = load_configuration()
    now = datetime(2026, 9, 24, 12, tzinfo=UTC) + timedelta(seconds=sim_time_s)
    return TelemetryFrame(
        run_id=RUN_ID,
        frame_seq=frame_seq,
        tick_seq=frame_seq,
        sim_time_s=sim_time_s,
        sim_epoch_utc=datetime(2026, 9, 24, tzinfo=UTC),
        received_at_utc=now,
        values={"computer_reset_count": count},
        quality={
            "computer_reset_count": ChannelQuality(
                state=QualityState.VALID if valid else QualityState.MISSING,
                reason=None if valid else "injected dropout",
                source_time_utc=now,
            )
        },
        context=FrameContext(
            vehicle_config_version=config.spacecraft.config_version,
            model_version=config.spacecraft.model_version,
            run_id=RUN_ID,
            source=SourceKind.LIVE,
            run_status=RunStatus.RUNNING,
            mode=OperatingMode.NOMINAL,
            illumination=Illumination.SUNLIGHT,
            contact_expected=True,
            control_revision=0,
        ),
        engineering={"reset_events_s": events},
    )


def _avionics(engine: IncidentEngine):
    return next(
        (i for i in engine.incidents.values() if i.rule_id == "RESET_RECURRENCE"),
        None,
    )


def _params() -> tuple[float, int]:
    rule = load_configuration().rules.by_id("RESET_RECURRENCE")
    return float(rule.params["window_s"]), int(rule.params["min_events"])


def test_the_rule_is_marked_implemented_in_the_catalogue():
    rule = load_configuration().rules.by_id("RESET_RECURRENCE")
    assert rule is not None
    assert rule.implemented is True
    assert rule.subsystem == "FLIGHT_SYSTEMS"


def test_distinct_event_parsing_deduplicates_and_survives_rubbish():
    assert _distinct_event_times("10, 20, 10") == {10.0, 20.0}
    assert _distinct_event_times("") == set()
    assert _distinct_event_times(None) == set()
    assert _distinct_event_times("12, not-a-number, 30") == {12.0, 30.0}


def test_one_reset_does_not_open_an_incident():
    engine = IncidentEngine(load_configuration())
    for t in range(100, 112):
        engine.evaluate(_frame(float(t), count=1, events="100", frame_seq=t), False)
    assert _avionics(engine) is None


def test_two_distinct_resets_inside_the_window_open_one_incident():
    window_s, min_events = _params()
    assert min_events == 2
    engine = IncidentEngine(load_configuration())
    for t in range(200, 215):
        engine.evaluate(_frame(float(t), count=2, events="100, 190", frame_seq=t), False)

    incident = _avionics(engine)
    assert incident is not None
    assert incident.subsystem is Subsystem.FLIGHT_SYSTEMS
    assert incident.severity is Severity.WARNING
    assert incident.lifecycle is Lifecycle.OPEN
    # Repeated samples of one condition remain one incident.
    assert len([i for i in engine.incidents.values() if i.rule_id == "RESET_RECURRENCE"]) == 1
    assert "2 distinct computer reset events" in (incident.observed_evidence[-1].text)
    assert str(int(window_s)) in incident.observed_evidence[-1].text


def test_the_same_timestamp_reported_twice_counts_once():
    engine = IncidentEngine(load_configuration())
    for t in range(300, 315):
        engine.evaluate(_frame(float(t), count=2, events="250, 250", frame_seq=t), False)
    assert _avionics(engine) is None, "one event sampled twice is not two events"


def test_events_older_than_the_window_stop_counting():
    window_s, _ = _params()
    engine = IncidentEngine(load_configuration())
    # Both events sit outside the window as of this frame.
    late = window_s + 400.0
    for t in range(int(late), int(late) + 15):
        engine.evaluate(_frame(float(t), count=2, events="100, 190", frame_seq=t), False)
    assert _avionics(engine) is None


def test_missing_counter_telemetry_neither_opens_nor_resolves():
    """Missing data must not count as healthy and must not count as a reset."""
    engine = IncidentEngine(load_configuration())
    # Open it on valid evidence.
    for t in range(400, 415):
        engine.evaluate(_frame(float(t), count=2, events="350, 390", frame_seq=t), False)
    opened = _avionics(engine)
    assert opened is not None and opened.lifecycle is Lifecycle.OPEN

    # Now the counter drops out for longer than the healthy dwell. The verdict must not
    # drift to RESOLVED on the strength of absent evidence.
    for t in range(415, 460):
        engine.evaluate(
            _frame(float(t), count=None, events="350, 390", valid=False, frame_seq=t), False
        )
    still = _avionics(engine)
    assert still is not None
    assert still.lifecycle is Lifecycle.OPEN

    # A dropout also cannot open one from nothing.
    fresh = IncidentEngine(load_configuration())
    for t in range(500, 540):
        fresh.evaluate(
            _frame(float(t), count=None, events="450, 490", valid=False, frame_seq=t), False
        )
    assert _avionics(fresh) is None


def test_it_recovers_once_the_events_age_out_of_the_window():
    window_s, _ = _params()
    engine = IncidentEngine(load_configuration())
    for t in range(600, 615):
        engine.evaluate(_frame(float(t), count=2, events="550, 590", frame_seq=t), False)
    assert _avionics(engine).lifecycle is Lifecycle.OPEN

    # Time moves past the window with no new events; the dwell then resolves it.
    start = int(590 + window_s + 1)
    for t in range(start, start + 20):
        engine.evaluate(_frame(float(t), count=2, events="550, 590", frame_seq=t), False)
    assert _avionics(engine).lifecycle is Lifecycle.RESOLVED


def test_the_rule_reads_no_hidden_simulator_field():
    """A frame carrying no injected conditions at all is enough to drive the rule."""
    engine = IncidentEngine(load_configuration())
    frame = _frame(700.0, count=2, events="650, 690", frame_seq=700)
    # Nothing resembling the hidden injection is present on the frame.
    assert "computer_resets" not in frame.engineering
    assert "computer_resets" not in frame.context.known_commands
    for t in range(700, 715):
        engine.evaluate(frame.model_copy(update={"sim_time_s": float(t), "frame_seq": t}), False)
    assert _avionics(engine) is not None
