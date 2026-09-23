"""Phase 6 historical statistics and deterministic review endpoints."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.analysis.history import (
    compute_history_summary,
    get_or_create_review,
    resolve_window,
)
from app.api.deps import require_csrf, require_operational
from app.storage.db import get_session

router = APIRouter(tags=["history"])


class ReviewRequest(BaseModel):
    source: str
    period: str
    start: date
    end: date | None = None


def _window(period: str, start: date, end: date | None):
    try:
        return resolve_window(period, start, end=end)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "INVALID_HISTORY_WINDOW", "message": str(exc)},
        ) from exc


@router.get("/history/summary", dependencies=[Depends(require_operational)])
def history_summary(
    source: str = Query(pattern="^(LIVE|SYNTHETIC|REPLAY)$"),
    period: str = Query(pattern="^(daily|weekly|monthly|custom)$"),
    start: date = Query(),
    end: date | None = Query(default=None),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return compute_history_summary(db, source=source, window=_window(period, start, end))


@router.post(
    "/history/review",
    dependencies=[Depends(require_operational), Depends(require_csrf)],
)
def history_review(body: ReviewRequest, db: Session = Depends(get_session)) -> dict[str, Any]:
    window = _window(body.period, body.start, body.end)
    try:
        summary = compute_history_summary(db, source=body.source, window=window)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "INVALID_HISTORY_SOURCE", "message": str(exc)},
        ) from exc
    row, cached = get_or_create_review(db, summary)
    return {
        "analysis_id": str(row.id),
        "generation_mode": row.generation_mode,
        "status": row.status,
        "cached": cached,
        "result": row.result,
    }
