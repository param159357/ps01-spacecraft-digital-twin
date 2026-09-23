"""Endpoints from the section 12.2 contract whose behaviour arrives in a later phase.

They are registered now, with their real authentication, role and CSRF guards active,
so authorization is verified from Phase 1 and the route contract cannot drift. Each one
answers 503 NOT_IMPLEMENTED_YET with its phase number rather than returning invented
data. Later phases replace these handlers with real implementations.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import (
    not_implemented,
    require_csrf,
    require_master,
    require_operational,
)

router = APIRouter(tags=["pending"])

_OPERATIONAL = [Depends(require_operational)]
_MASTER_MUTATION = [Depends(require_master), Depends(require_csrf)]
_OPERATIONAL_MUTATION = [Depends(require_operational), Depends(require_csrf)]


# Phase 2 implemented /runs/current, /stream, /telemetry and the /simulator/* routes.
# They live in app/api/live.py and app/api/simulator.py.


# ------------------------------------------------------------------------- incidents


@router.get("/incidents", dependencies=_OPERATIONAL)
def list_incidents() -> None:
    raise not_implemented(3, "The incident list")


@router.get("/incidents/{incident_id}", dependencies=_OPERATIONAL)
def get_incident(incident_id: UUID) -> None:
    raise not_implemented(3, "Incident detail and evidence")


@router.post("/incidents/{incident_id}/acknowledge", dependencies=_OPERATIONAL_MUTATION)
def acknowledge_incident(incident_id: UUID) -> None:
    raise not_implemented(3, "Incident acknowledgement")


@router.post("/incidents/{incident_id}/compare", dependencies=_OPERATIONAL_MUTATION)
def compare_actions(incident_id: UUID) -> None:
    raise not_implemented(3, "Baseline and action comparison")


@router.post("/incidents/{incident_id}/investigate", dependencies=_OPERATIONAL_MUTATION)
def investigate_incident(incident_id: UUID) -> None:
    raise not_implemented(7, "Bounded AI investigation")


# ------------------------------------------------------------- forecasts and commands


@router.get("/forecasts/latest", dependencies=_OPERATIONAL)
def latest_forecast() -> None:
    raise not_implemented(3, "The latest forecast")


@router.post("/commands", dependencies=_MASTER_MUTATION)
def submit_command() -> None:
    """Master only. A specialist attempting recovery is refused here with 403."""
    raise not_implemented(3, "Recovery command execution")


@router.get("/commands/{command_id}", dependencies=_OPERATIONAL)
def get_command(command_id: UUID) -> None:
    raise not_implemented(3, "Command status and outcome")


# -------------------------------------------------------------- analysis and history


@router.get("/analyses/{analysis_id}", dependencies=_OPERATIONAL)
def get_analysis(analysis_id: UUID) -> None:
    raise not_implemented(7, "Analysis results")


@router.get("/history/summary", dependencies=_OPERATIONAL)
def history_summary() -> None:
    raise not_implemented(6, "Historical summaries")


@router.post("/history/review", dependencies=_OPERATIONAL_MUTATION)
def history_review() -> None:
    raise not_implemented(6, "Period review generation")
