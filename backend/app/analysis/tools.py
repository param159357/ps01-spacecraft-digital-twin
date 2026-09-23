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

from app.config.loader import VehicleConfiguration
from app.storage.models import ForecastRow, IncidentEventRow, IncidentRow, Run, TelemetryFrameRow


def get_incident_evidence(db: Session, incident_id: UUID, *, limit: int = 100) -> dict[str, Any]:
    limit = max(1, min(limit, 200))
    incident = db.get(IncidentRow, incident_id)
    if incident is None:
        raise KeyError(incident_id)
    events = (
        db.execute(
            select(IncidentEventRow)
            .where(IncidentEventRow.incident_id == incident_id)
            .order_by(IncidentEventRow.at_utc)
            .limit(limit)
        )
        .scalars()
        .all()
    )
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
    rows = (
        db.execute(
            select(TelemetryFrameRow)
            .where(
                TelemetryFrameRow.run_id == run_id,
                TelemetryFrameRow.sim_time_s >= start,
                TelemetryFrameRow.sim_time_s <= before_sim_time_s,
            )
            .order_by(TelemetryFrameRow.frame_seq.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
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
    db: Session, incident_id: UUID, *, days: int = 366, limit: int = 10
) -> dict[str, Any]:
    days = max(1, min(days, 3660))
    limit = max(1, min(limit, 10))
    incident = db.get(IncidentRow, incident_id)
    if incident is None:
        raise KeyError(incident_id)
    earliest = incident.first_seen_utc - timedelta(days=days)
    source = db.execute(select(Run.source).where(Run.id == incident.run_id)).scalar_one()
    rows = (
        db.execute(
            select(IncidentRow)
            .join(Run, Run.id == IncidentRow.run_id)
            .where(
                IncidentRow.id != incident.id,
                IncidentRow.rule_id == incident.rule_id,
                IncidentRow.affected_entity == incident.affected_entity,
                IncidentRow.first_seen_utc >= earliest,
                IncidentRow.first_seen_utc <= incident.last_seen_utc,
                Run.source == source,
            )
            .order_by(IncidentRow.first_seen_utc.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
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


class InvestigatorToolbox:
    """Execute only the four Phase 6 tools, fixed to one authorized incident."""

    NAMES = frozenset(
        {
            "get_incident_evidence",
            "get_recent_telemetry",
            "find_similar_incidents",
            "get_forecast_comparison",
        }
    )

    def __init__(
        self, db: Session, incident_id: UUID, config: VehicleConfiguration, *, max_calls: int = 4
    ) -> None:
        self.db = db
        self.incident_id = incident_id
        self.config = config
        self.max_calls = max_calls
        self.calls = 0

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if name not in self.NAMES:
            raise ValueError("unauthorized investigator tool")
        if self.calls >= self.max_calls:
            raise ValueError("investigator tool-call limit reached")
        self.calls += 1
        arguments = arguments or {}
        if arguments:
            raise ValueError("investigator tools do not accept model-selected scope")
        if name == "get_incident_evidence":
            return get_incident_evidence(self.db, self.incident_id)
        if name == "find_similar_incidents":
            return find_similar_incidents(self.db, self.incident_id, limit=10)
        if name == "get_forecast_comparison":
            return get_forecast_comparison(self.db, self.incident_id)
        incident = self.db.get(IncidentRow, self.incident_id)
        if incident is None:
            raise KeyError(self.incident_id)
        latest = self.db.execute(
            select(TelemetryFrameRow)
            .where(TelemetryFrameRow.run_id == incident.run_id)
            .order_by(TelemetryFrameRow.frame_seq.desc())
            .limit(1)
        ).scalar_one_or_none()
        before = latest.sim_time_s if latest else 0.0
        result = get_recent_telemetry(
            self.db, incident.run_id, before_sim_time_s=before, seconds=300, limit=300
        )
        frames = result["frames"]
        original_count = len(frames)
        if len(frames) > 60:
            step = (len(frames) - 1) / 59
            frames = [frames[round(index * step)] for index in range(60)]
        allowed = {channel.id for channel in self.config.telemetry.channels}
        for frame in frames:
            frame["values"] = {k: v for k, v in frame["values"].items() if k in allowed}
            frame["quality"] = {k: v for k, v in frame["quality"].items() if k in allowed}
        result["frames"] = frames
        result["downsampled"] = len(frames) < original_count
        return result


def collect_evidence_ids(value: Any) -> set[str]:
    """Collect only explicit stored evidence identifiers from tool results."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"evidence_id", "forecast_id", "incident_id"} and isinstance(child, str):
                prefix = (
                    "forecast:"
                    if key == "forecast_id"
                    else "incident:"
                    if key == "incident_id"
                    else ""
                )
                found.add(f"{prefix}{child}" if prefix else child)
            elif key == "evidence_ids" and isinstance(child, list):
                found.update(item for item in child if isinstance(item, str))
            elif key == "frame_seq" and isinstance(child, int):
                found.add(f"frame:{child}")
            collect = collect_evidence_ids(child)
            found.update(collect)
    elif isinstance(value, list):
        for child in value:
            found.update(collect_evidence_ids(child))
    return found


def get_forecast_comparison(db: Session, incident_id: UUID, *, limit: int = 10) -> dict[str, Any]:
    limit = max(1, min(limit, 20))
    incident = db.get(IncidentRow, incident_id)
    if incident is None:
        raise KeyError(incident_id)
    rows = (
        db.execute(
            select(ForecastRow)
            .where(
                ForecastRow.run_id == incident.run_id,
                ForecastRow.generated_at_utc >= incident.first_seen_utc - timedelta(minutes=5),
                ForecastRow.generated_at_utc <= incident.last_seen_utc + timedelta(minutes=5),
            )
            .order_by(ForecastRow.generated_at_utc.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
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
