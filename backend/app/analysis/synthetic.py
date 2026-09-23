"""Deterministic, bounded synthetic-month generation for Phase 6."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from time import perf_counter
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config.loader import VehicleConfiguration
from app.simulation.environment import evaluate
from app.simulation.models import LoadRequest, step_electrical, step_thermal
from app.storage.models import (
    CommandRow,
    HistoryBucketRow,
    IncidentEventRow,
    IncidentRow,
    Run,
    TelemetryFrameRow,
)

EVIDENCE_WINDOWS = 3
EVIDENCE_SECONDS_BEFORE = 120
EVIDENCE_SECONDS_AFTER = 180


@dataclass(frozen=True)
class SyntheticHistorySpec:
    month: date
    seed: int = 6026
    issue_weekday: int = 1  # Tuesday, where Monday is 0
    issue_hour_utc: int = 10
    issue_duration_minutes: int = 20

    def __post_init__(self) -> None:
        if self.month.day != 1:
            raise ValueError("month must be the first day of a calendar month")
        if not 0 <= self.issue_weekday <= 6:
            raise ValueError("issue_weekday must be 0..6")
        if not 0 <= self.issue_hour_utc <= 23:
            raise ValueError("issue_hour_utc must be 0..23")
        if not 1 <= self.issue_duration_minutes <= 120:
            raise ValueError("issue_duration_minutes must be 1..120")

    @property
    def end(self) -> date:
        if self.month.month == 12:
            return date(self.month.year + 1, 1, 1)
        return date(self.month.year, self.month.month + 1, 1)


def _cache_label(config: VehicleConfiguration, spec: SyntheticHistorySpec) -> str:
    return (
        f"P6 {spec.month:%Y-%m} seed={spec.seed} {config.spacecraft.config_version} "
        f"issue={spec.issue_weekday}@{spec.issue_hour_utc}:{spec.issue_duration_minutes}"
    )


def _issue_starts(spec: SyntheticHistorySpec) -> list[datetime]:
    day = spec.month
    result: list[datetime] = []
    while day < spec.end:
        if day.weekday() == spec.issue_weekday:
            result.append(datetime(day.year, day.month, day.day, spec.issue_hour_utc, tzinfo=UTC))
        day += timedelta(days=1)
    return result


def _active_issue(at: datetime, starts: list[datetime], duration: timedelta) -> bool:
    return any(start <= at < start + duration for start in starts)


def _quality(at: datetime) -> dict[str, Any]:
    stamp = at.isoformat()
    return {
        key: {"state": "VALID", "reason": None, "source_time_utc": stamp}
        for key in (
            "load_power",
            "battery_soc",
            "payload_temperature",
            "solar_voltage",
            "solar_current",
        )
    }


def generate_synthetic_month(
    db: Session, config: VehicleConfiguration, spec: SyntheticHistorySpec
) -> dict[str, Any]:
    """Generate one month of minute summaries plus three short 1 Hz excerpts.

    The expensive state is advanced once per minute using the shared electrical and
    stability-substepped thermal model.  No simulator object or checkpoint is used.
    """
    started = perf_counter()
    label = _cache_label(config, spec)
    existing = db.execute(
        select(Run).where(Run.source == "SYNTHETIC", Run.label == label).limit(1)
    ).scalar_one_or_none()
    expected_minutes = int((spec.end - spec.month).days * 24 * 60)
    if existing is not None:
        bucket_count = db.execute(
            select(func.count()).select_from(HistoryBucketRow).where(
                HistoryBucketRow.run_id == existing.id
            )
        ).scalar_one()
        if bucket_count != expected_minutes:
            raise RuntimeError(
                "A partial cached synthetic dataset exists; preserve it and remove it "
                "explicitly before regenerating."
            )
        return {
            "run_id": str(existing.id),
            "cached": True,
            "minutes": bucket_count,
            "incidents": len(_issue_starts(spec)),
            "evidence_frames": db.execute(
                select(func.count()).select_from(TelemetryFrameRow).where(
                    TelemetryFrameRow.run_id == existing.id
                )
            ).scalar_one(),
            "elapsed_s": round(perf_counter() - started, 3),
            "label": label,
        }

    epoch = datetime.combine(spec.month, datetime.min.time(), tzinfo=UTC)
    end = datetime.combine(spec.end, datetime.min.time(), tzinfo=UTC)
    run = Run(
        source="SYNTHETIC",
        seed=spec.seed,
        config_version=config.config_version,
        model_version=config.spacecraft.model_version,
        sim_epoch_utc=epoch,
        status="STOPPED",
        control_revision=0,
        label=label,
        ended_at=end,
        private_checkpoint=None,
    )
    db.add(run)
    db.flush()

    issue_starts = _issue_starts(spec)
    duration = timedelta(minutes=spec.issue_duration_minutes)
    power = config.spacecraft.power
    thermal = config.spacecraft.thermal
    energy_wh = power.battery_capacity_wh * power.initial_soc_percent / 100.0
    temperatures = {name: node.initial_c for name, node in thermal.nodes.items()}
    branch_w = power.optional_branch.rated_w
    minute = epoch
    minute_index = 0
    bucket_batch: list[HistoryBucketRow] = []
    snapshots: dict[datetime, dict[str, float]] = {}

    while minute < end:
        sim_time_s = (minute - epoch).total_seconds()
        env = evaluate(
            config.spacecraft.environment,
            sim_time_s,
            thermal.boundary_sunlight_c,
            thermal.boundary_eclipse_c,
        )
        issue = _active_issue(minute, issue_starts, duration)
        extra_branch_w = branch_w if issue else 0.0
        load = LoadRequest(
            essential_w=power.essential_load_w,
            payload_w=power.payload_nominal_w,
            heater_w=0.0,
            fault_load_w=extra_branch_w,
        )
        electrical = step_electrical(
            power,
            energy_wh,
            load,
            env.illumination_factor,
            1.0,
            60.0,
        )
        energy_wh = electrical.next_energy_wh
        heat_inputs = {
            "battery": 0.0,
            "panel": 60.0 if env.is_sunlit else 0.0,
            "computer": 15.0,
            "payload": (
                power.payload_nominal_w * 0.35
                + extra_branch_w * power.optional_branch.heat_fraction_to_payload
            ),
            "tank": 2.0,
            "thruster": 0.0,
        }
        temperatures = step_thermal(
            thermal, temperatures, heat_inputs, env.boundary_c, 60.0
        )
        aggregates = {
            "load_power": round(electrical.requested_load_w, 3),
            "served_load_w": round(electrical.served_load_w, 3),
            "solar_power_w": round(electrical.solar_dispatched_w, 3),
            "battery_soc": round(electrical.soc_percent, 5),
            "payload_temperature": round(temperatures["payload"], 5),
            "optional_branch_scheduled_w": 0.0,
            "optional_branch_observed_w": extra_branch_w,
            "illumination": env.illumination.value,
        }
        bucket_batch.append(
            HistoryBucketRow(
                run_id=run.id,
                minute_utc=minute,
                aggregates=aggregates,
                sample_count=60,
                valid_sample_count=60,
            )
        )
        if any(abs((minute - start).total_seconds()) <= 180 for start in issue_starts[:3]):
            snapshots[minute] = aggregates
        if len(bucket_batch) >= 1_000:
            db.add_all(bucket_batch)
            db.flush()
            bucket_batch.clear()
        minute += timedelta(minutes=1)
        minute_index += 1
    if bucket_batch:
        db.add_all(bucket_batch)
        db.flush()

    command_categories = ("MISSING", "REJECTED", "NO_CHANGE", "UNKNOWN", "IMPROVED")
    incident_rows: list[IncidentRow] = []
    event_rows: list[IncidentEventRow] = []
    command_rows: list[CommandRow] = []
    for index, start in enumerate(issue_starts):
        incident_id = uuid4()
        finish = start + duration
        evidence = {
            "observed_evidence": [
                {
                    "text": (
                        f"Observed optional branch draw was {branch_w:.1f} W while the "
                        "stored equipment schedule called for 0.0 W."
                    ),
                    "evidence_id": f"bucket:{start.isoformat()}",
                    "channel_id": "load_power",
                    "value": branch_w,
                    "unit": "W",
                    "observed_at_utc": start.isoformat(),
                }
            ],
            "suspected_causes": ["Cause not established from stored observations."],
            "missing_evidence": [],
        }
        incident_rows.append(
            IncidentRow(
                id=incident_id,
                run_id=run.id,
                rule_id="ACTUATOR_MISMATCH",
                rule_version=config.rules.config_version,
                family="COMMAND_RESPONSE",
                subsystem="POWER",
                affected_entity=power.optional_branch.id,
                affected_groups=["POWER"],
                severity="WARNING",
                lifecycle="RESOLVED",
                first_seen_utc=start,
                last_seen_utc=finish,
                evidence=evidence,
                evidence_revision=2,
            )
        )
        event_rows.extend(
            [
                IncidentEventRow(
                    incident_id=incident_id,
                    at_utc=start,
                    kind="OPENED",
                    detail="Observed branch draw disagreed with the stored schedule.",
                    severity="WARNING",
                    lifecycle="OPEN",
                    evidence_ids=[f"bucket:{start.isoformat()}"],
                ),
                IncidentEventRow(
                    incident_id=incident_id,
                    at_utc=finish,
                    kind="LIFECYCLE_CHANGED",
                    detail="Observed branch draw returned to the scheduled value.",
                    severity="WARNING",
                    lifecycle="RESOLVED",
                    evidence_ids=[f"bucket:{finish.isoformat()}"],
                ),
            ]
        )
        category = command_categories[index % len(command_categories)]
        if category == "MISSING":
            continue
        command_id = uuid4()
        status = "REJECTED" if category == "REJECTED" else "APPLIED"
        outcome = {
            "REJECTED": "UNKNOWN",
            "NO_CHANGE": "NO_CHANGE",
            "UNKNOWN": "PENDING_OBSERVATION",
            "IMPROVED": "IMPROVED",
        }[category]
        created = start + timedelta(minutes=5)
        command_rows.append(
            CommandRow(
                id=command_id,
                run_id=run.id,
                actor_username="synthetic-fixture",
                idempotency_key=f"p6-{spec.seed}-{index}",
                action_id="ISOLATE_OPTIONAL_BRANCH",
                action_arguments={"branch_id": power.optional_branch.id},
                status=status,
                status_reason="Synthetic labelled evidence" if status == "REJECTED" else None,
                created_at=created,
                applied_at_utc=created if status == "APPLIED" else None,
                applied_tick_seq=None,
                observed_outcome=outcome,
                outcome_detail=f"Synthetic labelled outcome: {outcome}.",
            )
        )
        event_rows.append(
            IncidentEventRow(
                incident_id=incident_id,
                at_utc=created,
                kind="COMMAND_OUTCOME" if status == "APPLIED" else "COMMAND_STATUS",
                detail=f"Stored command status {status}; observed outcome {outcome}.",
                evidence_ids=[f"command:{command_id}"],
            )
        )
    db.add_all(incident_rows)
    db.flush()
    db.add_all(event_rows)
    db.add_all(command_rows)

    frame_rows: list[TelemetryFrameRow] = []
    frame_seq = 0
    for start in issue_starts[:EVIDENCE_WINDOWS]:
        excerpt_start = start - timedelta(seconds=EVIDENCE_SECONDS_BEFORE)
        excerpt_end = start + timedelta(seconds=EVIDENCE_SECONDS_AFTER)
        at = excerpt_start
        while at < excerpt_end:
            nearest_minute = at.replace(second=0, microsecond=0)
            aggregates = snapshots[nearest_minute]
            active = start <= at < start + duration
            observed_branch_w = branch_w if active else 0.0
            load_w = power.essential_load_w + power.payload_nominal_w + observed_branch_w
            solar_w = float(aggregates["solar_power_w"])
            solar_v = 34.0 if solar_w > 0 else 0.0
            frame_rows.append(
                TelemetryFrameRow(
                    run_id=run.id,
                    frame_seq=frame_seq,
                    tick_seq=frame_seq,
                    sim_time_s=(at - epoch).total_seconds(),
                    received_at_utc=at,
                    values={
                        "load_power": load_w,
                        "battery_soc": aggregates["battery_soc"],
                        "payload_temperature": aggregates["payload_temperature"],
                        "solar_voltage": solar_v,
                        "solar_current": solar_w / solar_v if solar_v else 0.0,
                    },
                    quality=_quality(at),
                    context={
                        "source": "SYNTHETIC",
                        "known_commands": {},
                        "equipment_schedule": [
                            {"equipment": power.optional_branch.id, "scheduled_power_w": 0.0}
                        ],
                    },
                )
            )
            frame_seq += 1
            at += timedelta(seconds=1)
    db.add_all(frame_rows)
    db.flush()

    return {
        "run_id": str(run.id),
        "cached": False,
        "minutes": minute_index,
        "incidents": len(incident_rows),
        "evidence_frames": len(frame_rows),
        "elapsed_s": round(perf_counter() - started, 3),
        "label": label,
    }
