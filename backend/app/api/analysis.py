"""Authenticated Phase 7 investigation requests and results."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_role, require_csrf, require_operational
from app.contracts.common import Role, Subsystem, may_act_on
from app.storage.db import get_session
from app.storage.models import AnalysisRow, IncidentRow

router = APIRouter(tags=["analysis"])


def _assert_incident_access(incident: IncidentRow | None, role: Role) -> IncidentRow:
    if incident is None:
        raise HTTPException(404, {"error": "INCIDENT_NOT_FOUND", "message": "Incident not found."})
    if not may_act_on(role, Subsystem(incident.subsystem)):
        raise HTTPException(
            403,
            {
                "error": "SUBSYSTEM_NOT_ASSIGNED",
                "message": "Incident is outside this role's scope.",
            },
        )
    return incident


def _forbidden() -> HTTPException:
    return HTTPException(
        403,
        {"error": "SUBSYSTEM_NOT_ASSIGNED", "message": "Analysis is outside this role's scope."},
    )


def _assert_analysis_access(db: Session, row: AnalysisRow, role: Role) -> None:
    """Fail closed. An analysis whose scope cannot be established is not served.

    An incident investigation follows its incident's subsystem. A period review is
    scoped by the role it was computed for, which is recorded in `window_key`, because
    two roles legitimately see different facts for the same window.
    """
    if row.incident_id is not None:
        incident = db.get(IncidentRow, row.incident_id)
        if incident is None:
            raise _forbidden()
        try:
            subsystem = Subsystem(incident.subsystem)
        except ValueError as exc:
            raise _forbidden() from exc
        if not may_act_on(role, subsystem):
            raise _forbidden()
        return
    if row.window_key:
        parts = row.window_key.split(":", 3)
        if len(parts) < 3 or parts[2] != role.value:
            raise _forbidden()
        return
    raise _forbidden()


@router.post(
    "/incidents/{incident_id}/investigate",
    dependencies=[Depends(require_operational), Depends(require_csrf)],
    status_code=status.HTTP_202_ACCEPTED,
)
def investigate_incident(
    incident_id: UUID,
    request: Request,
    role: Role = Depends(current_role),
    db: Session = Depends(get_session),
):
    _assert_incident_access(db.get(IncidentRow, incident_id), role)
    return request.app.state.analysis.enqueue_incident(incident_id, "MANUAL_REQUEST", manual=True)


@router.get(
    "/incidents/{incident_id}/analysis/latest", dependencies=[Depends(require_operational)]
)
def latest_incident_analysis(
    incident_id: UUID,
    request: Request,
    role: Role = Depends(current_role),
    db: Session = Depends(get_session),
):
    _assert_incident_access(db.get(IncidentRow, incident_id), role)
    row = db.execute(
        select(AnalysisRow)
        .where(AnalysisRow.incident_id == incident_id)
        .order_by(AnalysisRow.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            404, {"error": "ANALYSIS_NOT_FOUND", "message": "No investigation is stored."}
        )
    return request.app.state.analysis.get(row.id)


@router.get("/analyses/{analysis_id}", dependencies=[Depends(require_operational)])
def get_analysis(
    analysis_id: UUID,
    request: Request,
    role: Role = Depends(current_role),
    db: Session = Depends(get_session),
):
    row = db.get(AnalysisRow, analysis_id)
    if row is None:
        raise HTTPException(404, {"error": "ANALYSIS_NOT_FOUND", "message": "Analysis not found."})
    _assert_analysis_access(db, row, role)
    return request.app.state.analysis.get(analysis_id)
