"""Simulator-only endpoints: run control, bounded condition injection and truth.

Every route here is guarded by `require_simulator`. Condition injection is emphatically
not the same thing as a master's recovery action: it writes hidden simulator truth, and
it is reachable only by the simulator account.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import require_csrf, require_simulator
from app.api.live import get_runner
from app.simulation.runner import RunnerError, SimulationRunner
from app.simulation.truth import ConditionUpdate

router = APIRouter(prefix="/simulator", tags=["simulator"])


class RunOperation(str, Enum):
    START = "START"
    PAUSE = "PAUSE"
    RESET = "RESET"


class RunCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: RunOperation
    # A reset may pick a seed so a scenario replays identically.
    seed: int | None = Field(default=None, ge=0, le=2**31 - 1)


@router.post("/run", dependencies=[Depends(require_simulator), Depends(require_csrf)])
async def run_control(
    command: RunCommand,
    runner: SimulationRunner = Depends(get_runner),
) -> dict[str, Any]:
    try:
        if command.operation is RunOperation.START:
            return await runner.start()
        if command.operation is RunOperation.PAUSE:
            return await runner.pause()
        return await runner.reset(seed=command.seed)
    except RunnerError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "RUN_STATE_CONFLICT", "message": str(exc)},
        ) from exc


@router.post("/conditions", dependencies=[Depends(require_simulator), Depends(require_csrf)])
async def set_conditions(
    update: ConditionUpdate,
    runner: SimulationRunner = Depends(get_runner),
) -> dict[str, Any]:
    """Apply bounded scenario settings at the next tick boundary."""
    try:
        return await runner.update_conditions(update)
    except RunnerError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "RUN_STATE_CONFLICT", "message": str(exc)},
        ) from exc


@router.get("/state", dependencies=[Depends(require_simulator)])
def simulator_state(runner: SimulationRunner = Depends(get_runner)) -> dict[str, Any]:
    """Hidden truth for the presenter. Operational roles are refused by the guard above."""
    return runner.presenter_state()
