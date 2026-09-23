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


@router.post("/incidents/{incident_id}/investigate", dependencies=_OPERATIONAL_MUTATION)
def investigate_incident(incident_id: UUID) -> None:
    raise not_implemented(7, "Bounded AI investigation")


# ------------------------------------------------------------- forecasts and commands


# -------------------------------------------------------------- analysis


@router.get("/analyses/{analysis_id}", dependencies=_OPERATIONAL)
def get_analysis(analysis_id: UUID) -> None:
    raise not_implemented(7, "Analysis results")
