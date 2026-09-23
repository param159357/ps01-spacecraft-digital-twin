"""Read-only, bounded evidence tools prepared for the Phase 7 investigator.

The functions return stored observations only.  They do not import the simulator and do
not select the run checkpoint column.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.storage.models import ForecastRow, IncidentEventRow, IncidentRow, TelemetryFrameRow


def get_incident_evidence(db: Session, incident_id: UUID, *, limit: int = 100) -> dict[str, Any]:
    limit = max(1, min(limit, 200))
    incident = db.get(IncidentRow, incident_id)
    if incident is None:
        raise KeyError(incident_id)
    events = db.execute(
        select(IncidentEventRow)
        .where(IncidentEventRow.incident_id == incident_id)
        .order_by(IncidentEventRow.at_utc)
        .limit(limit)
    ).scalars().all()
    return {
        "incident_id": str(incident.id),
        "run_id": str(incident.run_id),
        "rule_id": incident.rule_id,
        "affected_entity": incident.affected_entity,
        "severity": incident.severity,
        "lifecycle": incident.lifecycle,
        "first_seen_utc": incident.first_seen_utc.isoformat(),
        "last_seen_utc": incident.last_seen_utc.isoformat(),
        "observed_evidence": (incident.evidence or {}).get("observed_evidence", []),
        "events": [
            {
                "at_utc": event.at_utc.isoformat(),
                "kind": event.kind,
                "detail": event.detail,
                "severity": event.severity,
                "lifecycle": event.lifecycle,
                "evidence_ids": event.evidence_ids or [],
            }
            for event in events
        ],
        "truncated": len(events) == limit,
    }


def get_recent_telemetry(
    db: Session,
    run_id: UUID,
    *,
    before_sim_time_s: float,
    seconds: int = 120,
    limit: int = 300,
) -> dict[str, Any]:
    seconds = max(1, min(seconds, 600))
    limit = max(1, min(limit, 600))
    start = max(0.0, before_sim_time_s - seconds)
    rows = db.execute(
        select(TelemetryFrameRow)
        .where(
            TelemetryFrameRow.run_id == run_id,
            TelemetryFrameRow.sim_time_s >= start,
            TelemetryFrameRow.sim_time_s <= before_sim_time_s,
        )
        .order_by(TelemetryFrameRow.frame_seq.desc())
        .limit(limit)
    ).scalars().all()
    rows.reverse()
    return {
        "run_id": str(run_id),
        "window": {"start_sim_time_s": start, "end_sim_time_s": before_sim_time_s},
        "frames": [
            {
                "frame_seq": row.frame_seq,
                "sim_time_s": row.sim_time_s,
                "received_at_utc": row.received_at_utc.isoformat(),
                "values": row.values,
                "quality": row.quality,
                "context": row.context,
            }
            for row in rows
        ],
        "truncated": len(rows) == limit,
    }


def find_similar_incidents(
    db: Session, incident_id: UUID, *, days: int = 366, limit: int = 50
) -> dict[str, Any]:
    days = max(1, min(days, 3660))
    limit = max(1, min(limit, 100))
    incident = db.get(IncidentRow, incident_id)
    if incident is None:
        raise KeyError(incident_id)
    earliest = incident.first_seen_utc - timedelta(days=days)
    rows = db.execute(
        select(IncidentRow)
        .where(
            IncidentRow.id != incident.id,
            IncidentRow.rule_id == incident.rule_id,
            IncidentRow.affected_entity == incident.affected_entity,
            IncidentRow.first_seen_utc >= earliest,
            IncidentRow.first_seen_utc <= incident.last_seen_utc,
        )
        .order_by(IncidentRow.first_seen_utc.desc())
        .limit(limit)
    ).scalars().all()
    return {
        "incident_id": str(incident.id),
        "match": {"rule_id": incident.rule_id, "affected_entity": incident.affected_entity},
        "similar": [
            {
                "incident_id": str(row.id),
                "run_id": str(row.run_id),
                "first_seen_utc": row.first_seen_utc.isoformat(),
                "last_seen_utc": row.last_seen_utc.isoformat(),
                "severity": row.severity,
                "lifecycle": row.lifecycle,
            }
            for row in rows
        ],
        "truncated": len(rows) == limit,
    }


def get_forecast_comparison(
    db: Session, incident_id: UUID, *, limit: int = 10
) -> dict[str, Any]:
    limit = max(1, min(limit, 20))
    incident = db.get(IncidentRow, incident_id)
    if incident is None:
        raise KeyError(incident_id)
    rows = db.execute(
        select(ForecastRow)
        .where(
            ForecastRow.run_id == incident.run_id,
            ForecastRow.generated_at_utc >= incident.first_seen_utc - timedelta(minutes=5),
            ForecastRow.generated_at_utc <= incident.last_seen_utc + timedelta(minutes=5),
        )
        .order_by(ForecastRow.generated_at_utc.desc())
        .limit(limit)
    ).scalars().all()
    return {
        "incident_id": str(incident.id),
        "forecasts": [
            {
                "forecast_id": str(row.id),
                "source_frame_seq": row.source_frame_seq,
                "generated_at_utc": row.generated_at_utc.isoformat(),
                "coverage": row.coverage,
                "action_id": row.action_id,
                "action_arguments": row.action_arguments or {},
                "assumptions": row.assumptions,
                "series": row.series,
                "crossings": row.crossings,
            }
            for row in rows
        ],
        "truncated": len(rows) == limit,
    }
