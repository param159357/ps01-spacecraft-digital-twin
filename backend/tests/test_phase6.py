"""Phase 6 acceptance checks: bounded stored history and recurrence."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select, text

from app.analysis.history import compute_history_summary, resolve_window
from app.analysis.synthetic import SyntheticHistorySpec, generate_synthetic_month
from app.analysis.tools import (
    find_similar_incidents,
    get_forecast_comparison,
    get_incident_evidence,
    get_recent_telemetry,
)
from app.config.loader import load_configuration
from app.contracts.actions import (
    ActionProposal,
    CommandRecord,
    CommandStatus,
    RecoveryOutcome,
)
from app.contracts.common import Subsystem
from app.contracts.incidents import Acknowledgement, Incident, IncidentEvent, Lifecycle, Severity
from app.operations import OperationalService
from app.storage.models import (
    HistoryBucketRow,
    IncidentEventRow,
    IncidentRow,
    Run,
    TelemetryFrameRow,
)
from tests.conftest import csrf_headers, login

HIDDEN_FIELDS = (
    "generation_factor",
    "extra_load_w",
    "heater_stuck_on",
    "contact_loss",
    "computer_resets",
    "branch_stuck_on",
    "isolator_failed",
    "private_checkpoint",
    "hidden_conditions",
)


def test_window_semantics_are_explicit_and_completed():
    now = datetime(2026, 9, 23, tzinfo=UTC)
    daily = resolve_window("daily", date(2026, 9, 21), now=now)
    weekly = resolve_window("weekly", date(2026, 9, 1), now=now)
    monthly = resolve_window("monthly", date(2026, 8, 1), now=now)
    assert daily.expected_minutes == 1_440
    assert weekly.expected_minutes == 10_080
    assert monthly.expected_minutes == 44_640
    assert monthly.label == "Completed calendar month 2026-08 UTC"


def test_synthetic_month_statistics_tools_and_real_api(
    client, clean_runs, session_factory
):
    # June has five Tuesdays, exercising every command/outcome category once.
    spec = SyntheticHistorySpec(month=date(2026, 6, 1), seed=6026)
    with session_factory() as db:
        generated = generate_synthetic_month(db, load_configuration(), spec)
        db.commit()
        assert generated["cached"] is False
        assert generated["minutes"] == 43_200
        assert generated["incidents"] == 5
        assert generated["evidence_frames"] == 900

        summary = compute_history_summary(
            db,
            source="SYNTHETIC",
            window=resolve_window("monthly", spec.month),
        )
        assert summary["dataset"] == "SYNTHETIC"
        assert summary["coverage"]["coverage_percent"] == 100.0
        assert summary["incidents"]["distinct_count"] == 5
        assert summary["recurrence"][0]["distinct_incidents"] == 5
        assert summary["actions"] == {
            "command_missing": 1,
            "rejected": 1,
            "accepted_but_ineffective": 1,
            "outcome_unknown": 1,
            "improved": 1,
        }

        # Ten timeline rows still mean five incidents: continuing state is not counted
        # from events or minute samples.
        event_count = db.scalar(select(func.count()).select_from(IncidentEventRow))
        assert event_count == 14
        assert db.scalar(select(func.count()).select_from(HistoryBucketRow)) == 43_200
        assert db.scalar(select(func.count()).select_from(TelemetryFrameRow)) == 900

        first = db.execute(
            select(IncidentRow).order_by(IncidentRow.first_seen_utc).limit(1)
        ).scalar_one()
        evidence = get_incident_evidence(db, first.id)
        recent = get_recent_telemetry(
            db,
            first.run_id,
            before_sim_time_s=(first.first_seen_utc - datetime(2026, 6, 1, tzinfo=UTC))
            .total_seconds()
            + 120,
            seconds=240,
        )
        similar = find_similar_incidents(db, first.id)
        comparison = get_forecast_comparison(db, first.id)
        assert evidence["rule_id"] == "ACTUATOR_MISMATCH"
        assert 1 <= len(recent["frames"]) <= 300
        assert len(similar["similar"]) == 0  # tool only looks backwards from the target
        assert comparison["forecasts"] == []
        raw_tools = json.dumps([evidence, recent, similar, comparison])
        for field in HIDDEN_FIELDS:
            assert field not in raw_tools

    login(client, "master")
    path = "/api/history/summary?source=SYNTHETIC&period=monthly&start=2026-06-01"
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert response.json()["incidents"]["distinct_count"] == 5
    assert client.get(
        "/api/history/summary?source=LIVE&period=monthly&start=2026-06-01"
    ).json()["incidents"]["distinct_count"] == 0
    for field in HIDDEN_FIELDS:
        assert field not in response.text

    body = {"source": "SYNTHETIC", "period": "monthly", "start": "2026-06-01"}
    first_review = client.post("/api/history/review", json=body, headers=csrf_headers(client))
    second_review = client.post("/api/history/review", json=body, headers=csrf_headers(client))
    assert first_review.status_code == 200, first_review.text
    assert first_review.json()["generation_mode"] == "TEMPLATE"
    assert first_review.json()["cached"] is False
    assert second_review.json()["cached"] is True
    assert first_review.json()["result"] == second_review.json()["result"]
    for field in HIDDEN_FIELDS:
        assert field not in first_review.text


def test_long_incident_is_counted_once_even_with_many_events(session_factory, engine):
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE telemetry_frames, runs RESTART IDENTITY CASCADE"))
    start = datetime(2026, 8, 5, 12, tzinfo=UTC)
    with session_factory() as db:
        run = Run(
            source="SYNTHETIC",
            seed=71,
            config_version="test",
            model_version="test",
            sim_epoch_utc=datetime(2026, 8, 1, tzinfo=UTC),
            status="STOPPED",
            control_revision=0,
            label="long-incident-test",
            ended_at=datetime(2026, 9, 1, tzinfo=UTC),
            private_checkpoint=None,
        )
        db.add(run)
        db.flush()
        incident = IncidentRow(
            run_id=run.id,
            rule_id="ENERGY_RESERVE",
            rule_version="test",
            family="POWER_ENERGY",
            subsystem="POWER",
            affected_entity="battery",
            affected_groups=["POWER"],
            severity="CRITICAL",
            lifecycle="RESOLVED",
            first_seen_utc=start,
            last_seen_utc=start + timedelta(hours=8),
            evidence={"observed_evidence": []},
        )
        db.add(incident)
        db.flush()
        for offset in range(20):
            db.add(
                IncidentEventRow(
                    incident_id=incident.id,
                    at_utc=start + timedelta(minutes=offset),
                    kind="EVIDENCE_UPDATED",
                    detail="Continuing observed condition.",
                    severity="CRITICAL",
                    lifecycle="OPEN",
                    evidence_ids=[f"frame:{offset}"],
                )
            )
        db.commit()
        summary = compute_history_summary(
            db,
            source="SYNTHETIC",
            window=resolve_window("monthly", date(2026, 8, 1)),
        )
        assert summary["incidents"]["distinct_count"] == 1
        assert summary["incidents"]["total_duration_s"] == 28_800.0


def test_live_lifecycle_acknowledgement_and_command_changes_write_timeline_events(
    session_factory, engine
):
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE telemetry_frames, runs RESTART IDENTITY CASCADE"))
    now = datetime(2026, 8, 5, 12, tzinfo=UTC)
    run = Run(
        source="LIVE",
        seed=1,
        config_version="test",
        model_version="test",
        sim_epoch_utc=now,
        status="STOPPED",
        control_revision=0,
        label="event-test",
        ended_at=now + timedelta(hours=1),
    )
    with session_factory() as db:
        db.add(run)
        db.commit()

    incident = Incident(
        id=uuid4(),
        run_id=run.id,
        rule_id="ENERGY_RESERVE",
        rule_version="test",
        family="POWER_ENERGY",
        subsystem=Subsystem.POWER,
        affected_groups=("POWER",),
        affected_entity="battery",
        severity=Severity.WARNING,
        lifecycle=Lifecycle.OPEN,
        first_seen_utc=now,
        last_seen_utc=now,
        timeline=(
            IncidentEvent(
                at_utc=now,
                kind="OPENED",
                detail="Observed reserve warning.",
                severity=Severity.WARNING,
                lifecycle=Lifecycle.OPEN,
            ),
        ),
    )
    service = OperationalService(load_configuration(), bus=None)
    service._persist_incident(incident)  # noqa: SLF001 - exercise the persistence boundary
    changed = incident.model_copy(
        update={
            "severity": Severity.CRITICAL,
            "lifecycle": Lifecycle.RECOVERING,
            "last_seen_utc": now + timedelta(minutes=1),
            "acknowledgement": Acknowledgement(
                acknowledged=True,
                acknowledged_by="master",
                acknowledged_at_utc=now + timedelta(seconds=30),
            ),
        }
    )
    service._persist_incident(changed)  # noqa: SLF001

    proposal = ActionProposal(
        id=uuid4(),
        run_id=run.id,
        incident_id=incident.id,
        action_id="REDUCE_PAYLOAD_POWER",
        action_arguments={"duty_percent": 0},
        evidence_frame_seq=1,
        control_revision=0,
        created_at_utc=now,
        expires_at_utc=now + timedelta(minutes=1),
    )
    service.proposals[proposal.id] = proposal
    command = CommandRecord(
        id=uuid4(),
        run_id=run.id,
        proposal_id=proposal.id,
        action_id=proposal.action_id,
        action_arguments=proposal.action_arguments,
        actor_username="master",
        idempotency_key="phase6-event-test",
        status=CommandStatus.ACCEPTED,
        created_at_utc=now,
    )
    service._persist_command(command)  # noqa: SLF001
    service._persist_command(  # noqa: SLF001
        command.model_copy(
            update={
                "status": CommandStatus.APPLIED,
                "applied_at_utc": now + timedelta(seconds=2),
                "applied_tick_seq": 2,
                "observed_outcome": RecoveryOutcome.IMPROVED,
                "outcome_detail": "Observed load fell.",
            }
        )
    )

    with session_factory() as db:
        kinds = list(
            db.execute(
                select(IncidentEventRow.kind)
                .where(IncidentEventRow.incident_id == incident.id)
                .order_by(IncidentEventRow.at_utc)
            ).scalars()
        )
    assert kinds.count("OPENED") == 1
    assert kinds.count("SEVERITY_CHANGED") == 1
    assert kinds.count("LIFECYCLE_CHANGED") == 1
    assert kinds.count("ACKNOWLEDGED") == 1
    assert kinds.count("COMMAND_STATUS") == 2
    assert kinds.count("COMMAND_OUTCOME") == 1
