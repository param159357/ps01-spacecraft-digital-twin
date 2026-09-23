"""Focused Phase 3 estimator, forecast and incident checks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from app.config.loader import load_configuration
from app.contracts.actions import ActionProposal
from app.contracts.common import Illumination, RunStatus, SourceKind
from app.contracts.telemetry import ChannelQuality
from app.forecasts.engine import forecast
from app.forecasts.estimator import estimate_observed_state
from app.monitoring.engine import IncidentEngine
from app.operations import OperationalService
from app.simulation.observation import SensorNoise, observe
from app.simulation.truth import InjectedConditions, SimulatorTruth
from app.simulation.twin import step


def _frame(extra_load_w: float = 0.0):
    config = load_configuration()
    truth = SimulatorTruth.initial(config.spacecraft, InjectedConditions(extra_load_w=extra_load_w))
    truth, result = step(config.spacecraft, truth, 1.0)
    return observe(
        config=config.spacecraft,
        telemetry=config.telemetry,
        truth=truth,
        result=result,
        noise=SensorNoise(42),
        run_id=uuid4(),
        frame_seq=1,
        sim_epoch_utc=datetime.now(UTC),
        received_at_utc=datetime.now(UTC),
        run_status=RunStatus.RUNNING,
        control_revision=0,
        source=SourceKind.LIVE,
        seconds_since_previous_frame=1.0,
    )


def test_estimator_refuses_invalid_required_observation():
    config = load_configuration()
    frame = _frame()
    quality = dict(frame.quality)
    quality["battery_soc"] = ChannelQuality(
        state="MISSING", reason="test dropout", source_time_utc=frame.received_at_utc
    )
    assert (
        estimate_observed_state(frame.model_copy(update={"quality": quality}), config.spacecraft)
        is None
    )


def test_payload_action_forecast_uses_same_observed_start_and_improves_reserve():
    config = load_configuration()
    estimate = estimate_observed_state(_frame(extra_load_w=300.0), config.spacecraft)
    assert estimate is not None
    baseline = forecast(estimate, config.spacecraft)
    action = forecast(estimate, config.spacecraft, payload_duty_percent=0)
    assert baseline.source_frame_seq == action.source_frame_seq
    assert baseline.series[0].values[0] == action.series[0].values[0]
    assert action.series[0].values[-1] > baseline.series[0].values[-1]
    thermal = next(s for s in baseline.series if s.channel_id == "battery_temperature")
    assert len(thermal.values) == len(baseline.series[0].values)


def test_continuing_supply_fault_is_one_incident_and_recovers_after_dwell():
    config = load_configuration()
    engine = IncidentEngine(config)
    frame = _frame(extra_load_w=300.0)
    values = dict(frame.values)
    values["bus_voltage"] = 25.0
    engineering = dict(frame.engineering)
    engineering["unmet_load_w"] = 20.0
    frame = frame.model_copy(update={"values": values, "engineering": engineering})
    incidents = []
    for seq in range(1, 7):
        current = frame.model_copy(
            update={
                "frame_seq": seq,
                "sim_time_s": float(seq),
                "received_at_utc": frame.received_at_utc + timedelta(seconds=seq),
            }
        )
        incidents.extend(engine.evaluate(current))
    supply = [i for i in engine.incidents.values() if i.rule_id == "SUPPLY_SHORTAGE"]
    assert len(supply) == 1
    assert supply[0].lifecycle.value == "OPEN"


def test_normal_eclipse_does_not_open_generation_shortfall():
    config = load_configuration()
    engine = IncidentEngine(config)
    frame = _frame()
    values = dict(frame.values)
    values.update({"solar_voltage": 0.0, "solar_current": 0.0, "battery_current": 8.0})
    context = frame.context.model_copy(update={"illumination": Illumination.ECLIPSE})
    for seq in range(1, 35):
        engine.evaluate(
            frame.model_copy(
                update={
                    "frame_seq": seq,
                    "sim_time_s": float(seq),
                    "values": values,
                    "context": context,
                }
            )
        )
    assert not [i for i in engine.incidents.values() if i.rule_id == "GENERATION_SHORTFALL"]


def test_idempotent_command_is_one_record_and_measured_drop_is_improved(monkeypatch):
    config = load_configuration()
    frame = _frame(extra_load_w=300.0)
    estimate = estimate_observed_state(frame, config.spacecraft)
    assert estimate is not None
    service = OperationalService(config, SimpleNamespace(publish=lambda *args: None))
    monkeypatch.setattr(service, "_persist_command", lambda command: None)
    service.latest_estimate = estimate
    now = datetime.now(UTC)
    proposal = ActionProposal(
        id=uuid4(),
        run_id=frame.run_id,
        incident_id=None,
        action_id="REDUCE_PAYLOAD_POWER",
        action_arguments={"duty_percent": 0},
        evidence_frame_seq=frame.frame_seq,
        control_revision=frame.context.control_revision,
        created_at_utc=now,
        expires_at_utc=now + timedelta(seconds=30),
    )
    service.proposals[proposal.id] = proposal
    first = service.accept_command(proposal.id, "master", "same-click")
    second = service.accept_command(proposal.id, "master", "same-click")
    assert first.id == second.id
    assert len(service.commands) == 1
    service.mark_applied(first.id, 10, 300.0)
    recovered = frame.model_copy(
        update={"tick_seq": 13, "values": {**frame.values, "load_power": 300.0}}
    )
    service._observe_command_outcomes(recovered)  # noqa: SLF001
    assert service.commands[first.id].observed_outcome.value == "IMPROVED"
