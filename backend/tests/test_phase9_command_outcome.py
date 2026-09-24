"""Recovery outcome must be measured against the state before the command.

The defect these cover: the acceptance baseline was stored with a sentinel applied-tick
of `0`. The "wait three ticks after application" guard then read
`frame.tick_seq - 0 >= 3`, which is true on essentially every frame, so a command that
was still queued got judged immediately — compared against the vehicle as it already
was, reported `NO_CHANGE`, and its acceptance baseline deleted. `mark_applied` then
re-seeded the baseline from the frame on which the command had *just* been applied, so a
real reduction was invisible.

It only appeared when a frame was published between acceptance and application, which is
why one comparison followed by a quick approval looked correct and a slower, repeated
comparison did not.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config.loader import load_configuration
from app.contracts.actions import ActionProposal, CommandStatus, RecoveryOutcome
from app.contracts.common import (
    Illumination,
    OperatingMode,
    QualityState,
    RunStatus,
    SourceKind,
)
from app.contracts.telemetry import ChannelQuality, FrameContext, TelemetryFrame
from app.operations import OperationalService
from app.storage.models import IncidentRow, Run

RUN_ID = uuid4()
INCIDENT_ID = uuid4()


@pytest.fixture(autouse=True)
def _run_row(session_factory):
    """Commands are persisted, so the run they belong to has to exist."""
    with session_factory() as db:
        if db.get(Run, RUN_ID) is None:
            db.add(
                Run(
                    id=RUN_ID,
                    source="LIVE",
                    seed=9,
                    config_version="test",
                    model_version="test",
                    sim_epoch_utc=datetime(2026, 9, 24, tzinfo=UTC),
                    status="STOPPED",
                    control_revision=0,
                    label="command-outcome-test",
                )
            )
            db.flush()
        if db.get(IncidentRow, INCIDENT_ID) is None:
            # Command status changes are written to the incident timeline, so the
            # incident the proposal names has to exist too.
            db.add(
                IncidentRow(
                    id=INCIDENT_ID,
                    run_id=RUN_ID,
                    rule_id="ENERGY_RESERVE",
                    rule_version="test",
                    family="POWER_ENERGY",
                    subsystem="POWER",
                    affected_entity="battery",
                    affected_groups=["POWER"],
                    severity="WARNING",
                    lifecycle="OPEN",
                    first_seen_utc=datetime(2026, 9, 24, tzinfo=UTC),
                    last_seen_utc=datetime(2026, 9, 24, tzinfo=UTC),
                    evidence={"observed_evidence": []},
                )
            )
        db.commit()


def _service() -> OperationalService:
    return OperationalService(load_configuration(), SimpleNamespace(publish=lambda *a, **k: None))


def _frame(tick: int, load_w: float | None, *, valid: bool = True) -> TelemetryFrame:
    config = load_configuration()
    now = datetime(2026, 9, 24, 12, tzinfo=UTC) + timedelta(seconds=tick)
    quality = ChannelQuality(
        state=QualityState.VALID if valid else QualityState.MISSING,
        reason=None if valid else "injected dropout",
        source_time_utc=now,
    )
    return TelemetryFrame(
        run_id=RUN_ID,
        frame_seq=tick,
        tick_seq=tick,
        sim_time_s=float(tick),
        sim_epoch_utc=datetime(2026, 9, 24, tzinfo=UTC),
        received_at_utc=now,
        values={"load_power": load_w},
        quality={"load_power": quality},
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
    )


def _proposal(duty: float) -> ActionProposal:
    now = datetime.now(UTC)
    return ActionProposal(
        id=uuid4(),
        run_id=RUN_ID,
        incident_id=INCIDENT_ID,
        action_id="REDUCE_PAYLOAD_POWER",
        action_arguments={"duty_percent": duty},
        evidence_frame_seq=1,
        control_revision=0,
        created_at_utc=now,
        expires_at_utc=now + timedelta(seconds=30),
        trade_off="test",
        outcome_paths=("test",),
    )


def _accept(service: OperationalService, duty: float, load_w: float, key: str):
    """Register a proposal and accept it against an observed load of `load_w`."""
    proposal = _proposal(duty)
    service.proposals[proposal.id] = proposal
    service.latest_estimate = SimpleNamespace(
        run_id=RUN_ID, control_revision=0, load_w=load_w, frame_seq=1
    )
    return proposal, service.accept_command(proposal.id, "master", key)


def test_only_the_approved_proposal_is_applied_after_repeated_comparisons():
    """Compare 0%, then 100%, then 50%, and approve only the 50% proposal."""
    service = _service()
    offered = []
    for duty in (0.0, 100.0, 50.0):
        proposal = _proposal(duty)
        service.proposals[proposal.id] = proposal
        offered.append(proposal)

    service.latest_estimate = SimpleNamespace(
        run_id=RUN_ID, control_revision=0, load_w=450.0, frame_seq=1
    )
    record = service.accept_command(offered[2].id, "master", "approve-50")

    assert record.action_arguments["duty_percent"] == 50.0
    assert record.proposal_id == offered[2].id
    # Exactly one command exists: comparing is read-only.
    assert len(service.commands) == 1
    # The other two proposals are untouched and never became commands.
    approved = {c.proposal_id for c in service.commands.values()}
    assert {p.id for p in offered[:2]}.isdisjoint(approved)


def test_repeated_comparisons_do_not_disturb_an_accepted_command():
    service = _service()
    _, record = _accept(service, 50.0, 450.0, "key-1")
    tracked_before = dict(service._command_start_load)  # noqa: SLF001

    # More proposals arrive, as they do when an operator tries each duty option.
    for duty in (0.0, 100.0):
        extra = _proposal(duty)
        service.proposals[extra.id] = extra

    assert service._command_start_load == tracked_before  # noqa: SLF001
    assert service.commands[record.id].observed_outcome is RecoveryOutcome.PENDING_OBSERVATION


def test_a_queued_command_is_not_judged_before_it_is_applied():
    """The defect: frames published between acceptance and application judged it early."""
    service = _service()
    _, record = _accept(service, 0.0, 450.0, "key-queued")

    # Several frames arrive while the command is still queued. tick_seq is far above the
    # three-tick settling window, which is what used to make this fire.
    for tick in (130, 131, 132, 133):
        assert service._observe_command_outcomes(_frame(tick, 450.0)) == []  # noqa: SLF001

    assert service.commands[record.id].observed_outcome is RecoveryOutcome.PENDING_OBSERVATION
    # The acceptance baseline survives, still marked unapplied.
    assert service._command_start_load[record.id] == (None, 450.0)  # noqa: SLF001


def test_baseline_comes_from_before_application_and_a_real_drop_reports_improved():
    service = _service()
    _, record = _accept(service, 0.0, 450.0, "key-improved")
    for tick in (130, 131):
        service._observe_command_outcomes(_frame(tick, 450.0))  # noqa: SLF001

    # Applied at tick 132. The frame handed to mark_applied already reflects the new duty
    # — it must not become the baseline.
    service.mark_applied(record.id, 132, 410.0)
    assert service._command_start_load[record.id] == (132, 450.0)  # noqa: SLF001

    service._observe_command_outcomes(_frame(135, 409.0))  # noqa: SLF001
    updated = service.commands[record.id]
    assert updated.status is CommandStatus.APPLIED
    assert updated.observed_outcome is RecoveryOutcome.IMPROVED
    assert "450.0 W" in (updated.outcome_detail or "")
    assert "409.0 W" in (updated.outcome_detail or "")


def test_a_genuine_non_response_still_reports_no_change():
    service = _service()
    _, record = _accept(service, 0.0, 450.0, "key-nochange")
    service.mark_applied(record.id, 132, 450.0)
    failed = service._observe_command_outcomes(_frame(135, 449.5))  # noqa: SLF001

    updated = service.commands[record.id]
    assert updated.status is CommandStatus.APPLIED
    assert updated.observed_outcome is RecoveryOutcome.NO_CHANGE
    # A failed recovery is reported back so the incident can be investigated.
    assert failed


def test_missing_telemetry_cannot_establish_an_outcome():
    """Missing data is neither improvement nor failure — the verdict simply waits."""
    service = _service()
    _, record = _accept(service, 0.0, 450.0, "key-missing")
    service.mark_applied(record.id, 132, 450.0)

    service._observe_command_outcomes(_frame(140, None, valid=False))  # noqa: SLF001
    assert service.commands[record.id].observed_outcome is RecoveryOutcome.PENDING_OBSERVATION
    # A value present but marked invalid must not count either.
    service._observe_command_outcomes(_frame(141, 120.0, valid=False))  # noqa: SLF001
    assert service.commands[record.id].observed_outcome is RecoveryOutcome.PENDING_OBSERVATION

    # Once a valid reading returns, the same baseline is still used.
    service._observe_command_outcomes(_frame(142, 405.0))  # noqa: SLF001
    assert service.commands[record.id].observed_outcome is RecoveryOutcome.IMPROVED
    assert "450.0 W" in (service.commands[record.id].outcome_detail or "")


def test_duplicate_approval_returns_one_record_and_one_baseline():
    service = _service()
    proposal, first = _accept(service, 0.0, 450.0, "same-key")
    second = service.accept_command(proposal.id, "master", "same-key")

    assert first.id == second.id
    assert len(service.commands) == 1
    assert len(service._command_start_load) == 1  # noqa: SLF001
    assert service._command_start_load[first.id] == (None, 450.0)  # noqa: SLF001


def test_an_expired_proposal_is_still_refused_after_the_fix():
    service = _service()
    stale = _proposal(0.0).model_copy(
        update={
            "created_at_utc": datetime.now(UTC) - timedelta(seconds=60),
            "expires_at_utc": datetime.now(UTC) - timedelta(seconds=30),
        }
    )
    service.proposals[stale.id] = stale
    service.latest_estimate = SimpleNamespace(
        run_id=RUN_ID, control_revision=0, load_w=450.0, frame_seq=1
    )
    with pytest.raises(TimeoutError):
        service.accept_command(stale.id, "master", "expired-key")
    assert not service.commands
