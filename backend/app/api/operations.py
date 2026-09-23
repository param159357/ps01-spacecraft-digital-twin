"""Phase 3 incident, forecast, comparison and recovery API."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import (
    current_role,
    current_session,
    require_csrf,
    require_master,
    require_operational,
)
from app.api.live import get_runner
from app.auth import AuthenticatedSession
from app.contracts.common import Role, may_act_on
from app.simulation.runner import SimulationRunner

router = APIRouter(tags=["operations"])


class CompareRequest(BaseModel):
    """Which implemented alternative to compare against the no-action baseline."""

    action_id: str = Field(default="REDUCE_PAYLOAD_POWER")
    duty_percent: int = Field(default=0)


class CommandRequest(BaseModel):
    proposal_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=128)


def _service(runner: SimulationRunner):
    if runner.operations is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "OPERATIONS_UNAVAILABLE", "message": "Monitoring is unavailable."},
        )
    return runner.operations


@router.get("/incidents")
def list_incidents(
    role: Role = Depends(current_role),
    runner: SimulationRunner = Depends(get_runner),
) -> dict[str, Any]:
    """Incidents within this role's assigned scope (section 3).

    The master sees every subsystem; a specialist sees its own. The incident ID is the
    same record on both screens, which is what lets a master and a specialist discuss
    one incident. `hidden_from_this_role` reports how many active incidents exist
    elsewhere — a count only, with no detail — so a specialist is never misled into
    thinking the vehicle is quiet when another subsystem is in trouble.
    """
    incidents = _service(runner).list_incidents(runner.run_id)
    visible = [i for i in incidents if may_act_on(role, i.subsystem)]
    return {
        "incidents": [i.model_dump(mode="json") for i in visible],
        "role": role.value,
        "hidden_from_this_role": len(incidents) - len(visible),
    }


@router.get("/incidents/{incident_id}")
def get_incident(
    incident_id: UUID,
    role: Role = Depends(current_role),
    runner: SimulationRunner = Depends(get_runner),
) -> dict[str, Any]:
    incident = _service(runner).incident_engine.incidents.get(incident_id)
    if incident is None:
        raise HTTPException(
            status_code=404, detail={"error": "INCIDENT_NOT_FOUND", "message": "No such incident."}
        )
    if not may_act_on(role, incident.subsystem):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "SUBSYSTEM_NOT_ASSIGNED",
                "message": "Incident is outside this role's scope.",
            },
        )
    return incident.model_dump(mode="json")


@router.post("/incidents/{incident_id}/acknowledge", dependencies=[Depends(require_csrf)])
def acknowledge(
    incident_id: UUID,
    auth: AuthenticatedSession = Depends(current_session),
    runner: SimulationRunner = Depends(get_runner),
) -> dict[str, Any]:
    service = _service(runner)
    incident = service.incident_engine.incidents.get(incident_id)
    if incident is None:
        raise HTTPException(
            status_code=404, detail={"error": "INCIDENT_NOT_FOUND", "message": "No such incident."}
        )
    if not may_act_on(auth.role, incident.subsystem):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "SUBSYSTEM_NOT_ASSIGNED",
                "message": "Incident is outside this role's scope.",
            },
        )
    return service.acknowledge(incident_id, auth.user.username).model_dump(mode="json")


@router.post("/incidents/{incident_id}/compare", dependencies=[Depends(require_csrf)])
async def compare(
    incident_id: UUID,
    body: CompareRequest,
    role: Role = Depends(current_role),
    runner: SimulationRunner = Depends(get_runner),
) -> dict[str, Any]:
    if body.action_id == "REDUCE_PAYLOAD_POWER" and body.duty_percent not in (0, 50, 100):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "INVALID_ACTION_ARGUMENT",
                "message": "duty_percent must be 0, 50, or 100.",
            },
        )
    incident = _service(runner).incident_engine.incidents.get(incident_id)
    if incident is None:
        raise HTTPException(
            status_code=404, detail={"error": "INCIDENT_NOT_FOUND", "message": "No such incident."}
        )
    if not may_act_on(role, incident.subsystem):
        raise HTTPException(
            status_code=403,
            detail={
                "error": "SUBSYSTEM_NOT_ASSIGNED",
                "message": "Incident is outside this role's scope.",
            },
        )
    try:
        return await _service(runner).compare(incident_id, body.duty_percent, body.action_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=503, detail={"error": "FORECAST_UNAVAILABLE", "message": str(exc)}
        ) from exc


@router.get("/forecasts/latest", dependencies=[Depends(require_operational)])
def latest_forecast(runner: SimulationRunner = Depends(get_runner)) -> dict[str, Any]:
    value = _service(runner).latest_forecast
    if value is None or runner.run_id is None or value.run_id != runner.run_id:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "FORECAST_UNAVAILABLE",
                "message": "No valid observed state has been forecast yet.",
            },
        )
    return value.model_dump(mode="json")


@router.post("/commands", dependencies=[Depends(require_master), Depends(require_csrf)])
async def submit_command(
    body: CommandRequest,
    auth: AuthenticatedSession = Depends(current_session),
    runner: SimulationRunner = Depends(get_runner),
) -> dict[str, Any]:
    service = _service(runner)
    try:
        record = service.accept_command(body.proposal_id, auth.user.username, body.idempotency_key)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail={"error": "PROPOSAL_NOT_FOUND", "message": "No such proposal."}
        ) from exc
    except (TimeoutError, RuntimeError) as exc:
        raise HTTPException(
            status_code=409, detail={"error": "PROPOSAL_CONFLICT", "message": str(exc)}
        ) from exc
    if record.status.value == "ACCEPTED":
        await runner.queue_command(
            record.id,
            record.action_id,
            float(record.action_arguments.get("duty_percent", 0.0)),
        )
    return record.model_dump(mode="json")


@router.get("/commands/{command_id}", dependencies=[Depends(require_operational)])
def get_command(command_id: UUID, runner: SimulationRunner = Depends(get_runner)) -> dict[str, Any]:
    record = _service(runner).commands.get(command_id)
    if record is None:
        raise HTTPException(
            status_code=404, detail={"error": "COMMAND_NOT_FOUND", "message": "No such command."}
        )
    return record.model_dump(mode="json")
