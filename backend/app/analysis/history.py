"""Bounded historical statistics and deterministic period reviews.

This module is deliberately storage-only.  It reads observations, incidents, timeline
events, commands and forecasts; simulator checkpoints and hidden conditions are not part
of its dependency graph.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.storage.models import (
    AnalysisRow,
    CommandRow,
    HistoryBucketRow,
    IncidentEventRow,
    IncidentRow,
    Run,
)

MAX_INCIDENT_ROWS = 2_000
MAX_EVENT_ROWS = 10_000
VALID_SOURCES = frozenset({"LIVE", "SYNTHETIC", "REPLAY"})
VALID_PERIODS = frozenset({"daily", "weekly", "monthly", "custom"})


@dataclass(frozen=True)
class HistoryWindow:
    period: str
    start_utc: datetime
    end_utc: datetime
    label: str

    @property
    def expected_minutes(self) -> int:
        return int((self.end_utc - self.start_utc).total_seconds() // 60)


def resolve_window(
    period: str,
    start: date,
    *,
    end: date | None = None,
    now: datetime | None = None,
) -> HistoryWindow:
    """Validate the explicit UTC window used by every historical query."""
    if period not in VALID_PERIODS:
        raise ValueError(f"period must be one of {sorted(VALID_PERIODS)}")
    start_utc = datetime.combine(start, time.min, tzinfo=UTC)
    if period == "daily":
        end_utc = start_utc + timedelta(days=1)
        label = f"Completed UTC day {start.isoformat()}"
    elif period == "weekly":
        end_utc = start_utc + timedelta(days=7)
        label = f"Seven-day UTC window from {start.isoformat()}"
    elif period == "monthly":
        if start.day != 1:
            raise ValueError("monthly windows must start on the first day of a month")
        _, days = calendar.monthrange(start.year, start.month)
        end_utc = start_utc + timedelta(days=days)
        label = f"Completed calendar month {start:%Y-%m} UTC"
    else:
        if end is None or end <= start:
            raise ValueError("custom windows require an end date after the start date")
        end_utc = datetime.combine(end, time.min, tzinfo=UTC)
        label = f"Custom UTC window {start.isoformat()} to {end.isoformat()}"
    if end_utc > (now or datetime.now(UTC)):
        raise ValueError("historical windows must be complete")
    return HistoryWindow(period, start_utc, end_utc, label)


def _command_ids(event: IncidentEventRow) -> set[UUID]:
    result: set[UUID] = set()
    for value in event.evidence_ids or []:
        if not isinstance(value, str) or not value.startswith("command:"):
            continue
        try:
            result.add(UUID(value.split(":", 1)[1]))
        except ValueError:
            continue
    return result


def compute_history_summary(db: Session, *, source: str, window: HistoryWindow) -> dict[str, Any]:
    """Compute facts for exactly one source and one bounded window."""
    if source not in VALID_SOURCES:
        raise ValueError(f"source must be one of {sorted(VALID_SOURCES)}")

    runs = (
        db.execute(
            select(Run.id).where(
                Run.source == source,
                Run.sim_epoch_utc < window.end_utc,
                or_(Run.ended_at.is_(None), Run.ended_at >= window.start_utc),
            )
        )
        .scalars()
        .all()
    )
    run_ids = tuple(runs)

    bucket_rows: list[HistoryBucketRow] = []
    incidents: list[IncidentRow] = []
    if run_ids:
        bucket_rows = list(
            db.execute(
                select(HistoryBucketRow)
                .where(
                    HistoryBucketRow.run_id.in_(run_ids),
                    HistoryBucketRow.minute_utc >= window.start_utc,
                    HistoryBucketRow.minute_utc < window.end_utc,
                )
                .order_by(HistoryBucketRow.minute_utc)
            ).scalars()
        )
        incidents = list(
            db.execute(
                select(IncidentRow)
                .where(
                    IncidentRow.run_id.in_(run_ids),
                    IncidentRow.first_seen_utc < window.end_utc,
                    IncidentRow.last_seen_utc >= window.start_utc,
                )
                .order_by(IncidentRow.first_seen_utc)
                .limit(MAX_INCIDENT_ROWS)
            ).scalars()
        )

    incident_ids = tuple(row.id for row in incidents)
    events: list[IncidentEventRow] = []
    if incident_ids:
        events = list(
            db.execute(
                select(IncidentEventRow)
                .where(
                    IncidentEventRow.incident_id.in_(incident_ids),
                    IncidentEventRow.at_utc >= window.start_utc,
                    IncidentEventRow.at_utc < window.end_utc,
                )
                .order_by(IncidentEventRow.at_utc)
                .limit(MAX_EVENT_ROWS)
            ).scalars()
        )

    linked: dict[UUID, set[UUID]] = {incident_id: set() for incident_id in incident_ids}
    for event in events:
        linked[event.incident_id].update(_command_ids(event))
    all_command_ids = tuple({cid for values in linked.values() for cid in values})
    commands = (
        {
            row.id: row
            for row in db.execute(
                select(CommandRow).where(CommandRow.id.in_(all_command_ids))
            ).scalars()
        }
        if all_command_ids
        else {}
    )

    severity_mix = {key: 0 for key in ("INFO", "WARNING", "CRITICAL", "UNKNOWN")}
    duration_values: list[float] = []
    recurrence: dict[tuple[str, str], list[IncidentRow]] = {}
    actions = {
        "command_missing": 0,
        "rejected": 0,
        "accepted_but_ineffective": 0,
        "outcome_unknown": 0,
        "improved": 0,
    }
    for incident in incidents:
        severity_mix[incident.severity] = severity_mix.get(incident.severity, 0) + 1
        clipped_start = max(incident.first_seen_utc, window.start_utc)
        clipped_end = min(incident.last_seen_utc, window.end_utc)
        duration_values.append(max(0.0, (clipped_end - clipped_start).total_seconds()))
        recurrence.setdefault((incident.rule_id, incident.affected_entity), []).append(incident)

        associated = [commands[cid] for cid in linked[incident.id] if cid in commands]
        if not associated:
            actions["command_missing"] += 1
        elif any(command.status in {"REJECTED", "FAILED"} for command in associated):
            actions["rejected"] += 1
        elif any(command.observed_outcome == "IMPROVED" for command in associated):
            actions["improved"] += 1
        elif any(command.observed_outcome in {"NO_CHANGE", "WORSENED"} for command in associated):
            actions["accepted_but_ineffective"] += 1
        else:
            actions["outcome_unknown"] += 1

    recurrence_rows = [
        {
            "rule_id": key[0],
            "affected_entity": key[1],
            "distinct_incidents": len(rows),
            "first_seen_utc": rows[0].first_seen_utc.isoformat(),
            "last_seen_utc": rows[-1].last_seen_utc.isoformat(),
        }
        for key, rows in recurrence.items()
        if len(rows) >= 2
    ]
    recurrence_rows.sort(key=lambda item: (-item["distinct_incidents"], item["rule_id"]))

    covered_minutes = len({row.minute_utc for row in bucket_rows})
    valid_samples = sum(row.valid_sample_count for row in bucket_rows)
    samples = sum(row.sample_count for row in bucket_rows)
    expected = window.expected_minutes
    coverage_percent = 0.0 if expected == 0 else 100.0 * covered_minutes / expected
    valid_percent = 0.0 if samples == 0 else 100.0 * valid_samples / samples
    total_duration = sum(duration_values)

    return {
        "dataset": source,
        "window": {
            "period": window.period,
            "label": window.label,
            "start_utc": window.start_utc.isoformat(),
            "end_utc": window.end_utc.isoformat(),
            "complete": True,
        },
        "coverage": {
            "run_count": len(run_ids),
            "expected_minutes": expected,
            "covered_minutes": covered_minutes,
            "coverage_percent": round(min(100.0, coverage_percent), 2),
            "sample_count": samples,
            "valid_sample_count": valid_samples,
            "valid_sample_percent": round(valid_percent, 2),
        },
        # Each row in incidents is one lifecycle, irrespective of its event/frame count.
        "incidents": {
            "distinct_count": len(incidents),
            "severity_mix": severity_mix,
            "total_duration_s": round(total_duration, 1),
            "mean_duration_s": round(total_duration / len(duration_values), 1)
            if duration_values
            else 0.0,
        },
        "recurrence": recurrence_rows,
        "actions": actions,
        "limits": {
            "incident_cap": MAX_INCIDENT_ROWS,
            "event_cap": MAX_EVENT_ROWS,
            "truncated": len(incidents) == MAX_INCIDENT_ROWS or len(events) == MAX_EVENT_ROWS,
        },
    }


def render_period_review(summary: dict[str, Any]) -> dict[str, Any]:
    """Deterministic, citable report structure; no provider or generated prose."""
    recurrence = summary["recurrence"]
    leading = recurrence[0] if recurrence else None
    findings = [
        f"{summary['incidents']['distinct_count']} distinct incidents were recorded.",
        (
            f"Coverage was {summary['coverage']['coverage_percent']:.2f}% "
            f"({summary['coverage']['covered_minutes']} of "
            f"{summary['coverage']['expected_minutes']} minutes)."
        ),
    ]
    if leading:
        findings.append(
            f"{leading['rule_id']} on {leading['affected_entity']} recurred "
            f"{leading['distinct_incidents']} times."
        )
    else:
        findings.append("No repeated rule/entity pair was found in this window.")
    return {
        "template_version": "history-review-1",
        "generation_mode": "TEMPLATE",
        "title": f"{summary['dataset']} — {summary['window']['label']}",
        "dataset": summary["dataset"],
        "window": summary["window"],
        "findings": findings,
        "severity_mix": summary["incidents"]["severity_mix"],
        "recurrence": recurrence,
        "command_outcomes": summary["actions"],
        "coverage": summary["coverage"],
        "limitations": [
            "This report is a deterministic rendering of stored observations and events.",
            "It does not infer hidden fault truth or establish root cause.",
        ],
    }


def history_window_key(summary: dict[str, Any]) -> str:
    window = summary["window"]
    return (
        f"history:{summary['dataset']}:{window['period']}:"
        f"{window['start_utc']}:{window['end_utc']}:history-review-1"
    )


def get_or_create_review(db: Session, summary: dict[str, Any]) -> tuple[AnalysisRow, bool]:
    key = history_window_key(summary)
    existing = db.execute(
        select(AnalysisRow)
        .where(
            AnalysisRow.window_key == key,
            AnalysisRow.generation_mode == "TEMPLATE",
            AnalysisRow.status == "COMPLETED",
        )
        .order_by(AnalysisRow.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return existing, True
    now = datetime.now(UTC)
    row = AnalysisRow(
        window_key=key,
        evidence_revision=0,
        generation_mode="TEMPLATE",
        status="COMPLETED",
        result=render_period_review(summary),
        usage={"provider": None, "model": None, "tokens": 0},
        completed_at=now,
    )
    db.add(row)
    db.flush()
    return row, False
