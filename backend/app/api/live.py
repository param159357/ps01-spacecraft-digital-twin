"""Live run metadata, the Server-Sent Events stream and bounded telemetry history."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_role, current_session
from app.api.serialization import channels_for_role, filter_frame
from app.contracts.common import Role
from app.contracts.telemetry import StreamEventType
from app.simulation.runner import SimulationRunner
from app.storage.db import get_session
from app.storage.models import Run, TelemetryFrameRow

logger = logging.getLogger("ps01.live")

router = APIRouter(tags=["live"])

HEARTBEAT_SECONDS = 10.0
MAX_HISTORY_ROWS = 3600


def get_runner(request: Request) -> SimulationRunner:
    runner: SimulationRunner | None = getattr(request.app.state, "runner", None)
    if runner is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "SIMULATION_UNAVAILABLE", "message": "The simulation is not running."},
        )
    return runner


@router.get("/runs/current")
def current_run(
    role: Role = Depends(current_role),
    runner: SimulationRunner = Depends(get_runner),
) -> dict[str, Any]:
    """Permitted run metadata and the latest committed snapshot."""
    snapshot = runner.snapshot()
    return {
        "run": snapshot["run"],
        "frame": filter_frame(snapshot["frame"], role),
        "visible_channels": sorted(channels_for_role(role)),
    }


def _sse(event_name: str, event_seq: int, payload: dict[str, Any]) -> str:
    return f"id: {event_seq}\nevent: {event_name}\ndata: {json.dumps(payload)}\n\n"


@router.get("/stream")
async def stream(
    request: Request,
    role: Role = Depends(current_role),
    runner: SimulationRunner = Depends(get_runner),
) -> StreamingResponse:
    """Role-filtered live stream.

    The subscriber is registered and the snapshot captured under the same short lock, so
    an incident or frame cannot slip through the gap between a separate snapshot fetch
    and the subscription.
    """

    async def generator() -> AsyncIterator[str]:
        async with runner._lock:  # noqa: SLF001 - deliberate: atomic subscribe + snapshot
            subscriber = runner.bus.subscribe(role)
            snapshot = runner.snapshot()
            snapshot_seq = runner.bus.event_seq

        try:
            yield _sse(
                StreamEventType.SNAPSHOT.value,
                snapshot_seq,
                {
                    "run": snapshot["run"],
                    "frame": filter_frame(snapshot["frame"], role),
                    "visible_channels": sorted(channels_for_role(role)),
                },
            )

            # No explicit disconnect poll: StreamingResponse cancels this generator when
            # the client goes away, and the `finally` below unsubscribes. Polling
            # `request.is_disconnected()` here can block under some transports.
            while True:
                if subscriber.overflowed:
                    # Rather than growing a backlog, ask the client to reconnect and
                    # take a fresh snapshot.
                    yield _sse(
                        StreamEventType.RUN_CHANGED.value,
                        runner.bus.event_seq,
                        {"reason": "resubscribe_required"},
                    )
                    break

                try:
                    event = await asyncio.wait_for(subscriber.queue.get(), HEARTBEAT_SECONDS)
                except TimeoutError:
                    # A heartbeat confirms the backend connection. It is explicitly not
                    # a statement that fresh spacecraft data arrived.
                    yield _sse(
                        StreamEventType.HEARTBEAT.value,
                        runner.bus.event_seq,
                        {"emitted_at_utc": datetime.now(UTC).isoformat()},
                    )
                    continue

                payload = event.payload
                if event.type is StreamEventType.TELEMETRY:
                    payload = filter_frame(payload, role) or {}
                yield _sse(event.type.value, event.event_seq, payload)
        finally:
            runner.bus.unsubscribe(subscriber)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Belt and braces for proxies that buffer by default.
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/telemetry")
def telemetry_history(
    role: Role = Depends(current_role),
    runner: SimulationRunner = Depends(get_runner),
    db: Session = Depends(get_session),
    run_id: UUID | None = Query(default=None),
    since_sim_time_s: float = Query(default=0.0, ge=0.0),
    until_sim_time_s: float | None = Query(default=None, ge=0.0),
    limit: int = Query(default=600, ge=1, le=MAX_HISTORY_ROWS),
    channels: str | None = Query(default=None, description="Comma-separated channel IDs."),
) -> dict[str, Any]:
    """Bounded, role-filtered history for one run."""
    target = run_id or runner.run_id
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "NO_RUN", "message": "There is no run to query."},
        )

    run = db.get(Run, target)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "RUN_NOT_FOUND", "message": "No such run."},
        )

    allowed = channels_for_role(role)
    requested = allowed
    if channels:
        asked = {c.strip() for c in channels.split(",") if c.strip()}
        unknown = asked - allowed
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "CHANNEL_NOT_PERMITTED",
                    "message": f"Not visible to role {role.value}: {sorted(unknown)}",
                },
            )
        requested = frozenset(asked)

    query = select(TelemetryFrameRow).where(
        TelemetryFrameRow.run_id == target,
        TelemetryFrameRow.sim_time_s >= since_sim_time_s,
    )
    if until_sim_time_s is not None:
        query = query.where(TelemetryFrameRow.sim_time_s <= until_sim_time_s)
    rows = db.execute(query.order_by(TelemetryFrameRow.frame_seq).limit(limit)).scalars().all()

    return {
        "run_id": str(target),
        "source": run.source,
        "truncated": len(rows) == limit,
        "frames": [
            {
                "frame_seq": row.frame_seq,
                "sim_time_s": row.sim_time_s,
                "received_at_utc": row.received_at_utc.isoformat(),
                "values": {k: v for k, v in (row.values or {}).items() if k in requested},
                "quality": {k: v for k, v in (row.quality or {}).items() if k in requested},
            }
            for row in rows
        ],
    }


@router.get("/runs")
def list_runs(
    _=Depends(current_session),
    db: Session = Depends(get_session),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """Recent runs. Source is always stated so synthetic data is never mistaken for live."""
    rows = db.execute(select(Run).order_by(Run.created_at.desc()).limit(limit)).scalars().all()
    return {
        "runs": [
            {
                "run_id": str(row.id),
                "source": row.source,
                "status": row.status,
                "seed": row.seed,
                "created_at": row.created_at.isoformat(),
                "ended_at": row.ended_at.isoformat() if row.ended_at else None,
                "config_version": row.config_version,
                "model_version": row.model_version,
            }
            for row in rows
        ]
    }
