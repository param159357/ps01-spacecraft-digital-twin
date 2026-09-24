"""FastAPI application entry point.

One API process owns the simulation, the incident engine and bounded background
analysis (CLAUDE.md section 2.1). Phase 1 establishes the process, configuration
validation, authentication and the route contract; the simulation loop is added in
Phase 2 through this same lifespan.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.analysis.service import AnalysisService
from app.api import analysis, auth_routes, history, live, operations, pending, simulator, system
from app.config.loader import load_configuration
from app.config.settings import get_settings
from app.operations import OperationalService
from app.simulation.runner import SimulationRunner
from app.storage.db import engine

logger = logging.getLogger("ps01")


def _configure_logging() -> None:
    """Give the application's own loggers somewhere to write.

    Uvicorn configures only its own loggers, so until this existed every
    `logger.info`/`logger.warning` in this application went nowhere — including the
    startup lines below and the warning raised when a run is found holding two active
    incidents for one deduplication key. A report nobody can read is not a report.
    Uvicorn's loggers do not propagate to the root, so its output is not duplicated.
    """
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(levelname)s:    %(name)s: %(message)s",
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _configure_logging()
    settings = get_settings()

    # Fail fast on a malformed fixture: a bad constant must never reach a dashboard.
    config = load_configuration()
    app.state.config = config
    logger.info(
        "configuration loaded: %s (model %s)",
        config.config_version,
        config.spacecraft.model_version,
    )

    # Storage is reported explicitly rather than silently assumed healthy.
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        app.state.storage_ok = True
        logger.info("database reachable")
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator, not swallowed
        app.state.storage_ok = False
        app.state.storage_error = str(exc)
        logger.error("database unreachable at startup: %s", exc)

    if settings.is_production_like() and settings.session_secret == "dev-only-insecure-secret":
        raise RuntimeError("SESSION_SECRET must be set when COOKIE_SECURE is enabled")

    # This process owns the simulation. It recovers the last live run in a PAUSED state;
    # the simulator account resumes or resets it explicitly.
    runner = SimulationRunner(config, settings)
    investigator = AnalysisService(config, settings, runner.bus)
    await investigator.startup()
    runner.operations = OperationalService(config, runner.bus, investigator)
    app.state.analysis = investigator
    app.state.runner = runner
    await runner.startup()

    yield

    await runner.shutdown()
    await investigator.shutdown()
    engine.dispose()


app = FastAPI(
    title="PS01 Spacecraft Digital Twin API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

_settings = get_settings()

# Same-origin in deployment (Caddy serves the frontend and proxies /api). CORS is
# configured for the explicit development origins only, with credentials allowed so
# the session cookie is sent.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Consistent error envelope: {"error": CODE, "message": text, ...}."""
    if isinstance(exc.detail, dict):
        body = exc.detail
    else:
        body = {"error": "REQUEST_FAILED", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)


app.include_router(system.router, prefix="/api")
app.include_router(auth_routes.router, prefix="/api")
app.include_router(live.router, prefix="/api")
app.include_router(simulator.router, prefix="/api")
app.include_router(operations.router, prefix="/api")
app.include_router(history.router, prefix="/api")
app.include_router(analysis.router, prefix="/api")
app.include_router(pending.router, prefix="/api")
