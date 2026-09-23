"""Single-worker, bounded Phase 7 investigation queue."""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.analysis.contracts import (
    AnalysisEnvelope,
    CitedStatement,
    InvestigationOutput,
    validate_citations,
)
from app.analysis.history import history_window_key, render_period_review
from app.analysis.provider import OpenAIProvider
from app.analysis.tools import InvestigatorToolbox, collect_evidence_ids
from app.config.loader import VehicleConfiguration
from app.config.settings import Settings
from app.storage.db import session_scope
from app.storage.models import AnalysisRow, IncidentRow


@dataclass(order=True)
class AnalysisJob:
    priority: int
    sequence: int
    analysis_id: UUID = field(compare=False)
    trigger: str = field(compare=False)
    incident_id: UUID | None = field(compare=False, default=None)
    evidence_revision: int = field(compare=False, default=0)
    summary: dict[str, Any] | None = field(compare=False, default=None)


class AnalysisService:
    """Own exactly one worker; monitoring only performs a non-blocking enqueue."""

    INCIDENT_TRIGGERS = frozenset(
        {"INCIDENT_OPENED", "SEVERITY_ESCALATED", "FAILED_RECOVERY", "MANUAL_REQUEST"}
    )

    def __init__(self, config: VehicleConfiguration, settings: Settings, bus: Any = None) -> None:
        self.config = config
        self.settings = settings
        self.bus = bus
        self.queue: asyncio.PriorityQueue[AnalysisJob] = asyncio.PriorityQueue(
            maxsize=settings.ai_pending_queue_limit
        )
        self._sequence = itertools.count()
        self._pending: dict[tuple[UUID, int] | str, UUID] = {}
        self._latest_automatic: dict[UUID, datetime] = {}
        self._latest_revision: dict[UUID, int] = {}
        self._worker: asyncio.Task | None = None
        self.active_jobs = 0
        self.max_active_jobs = 0

    async def startup(self) -> None:
        self._worker = asyncio.create_task(self._run(), name="phase7-analysis-worker")

    async def shutdown(self) -> None:
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass

    def enqueue_incident(self, incident_id: UUID, trigger: str, *, manual: bool = False) -> dict:
        if trigger not in self.INCIDENT_TRIGGERS:
            raise ValueError("unsupported investigation trigger")
        now = datetime.now(UTC)
        with session_scope() as db:
            incident = db.get(IncidentRow, incident_id)
            if incident is None:
                raise KeyError(incident_id)
            key = (incident.id, incident.evidence_revision)
            existing = db.execute(
                select(AnalysisRow)
                .where(
                    AnalysisRow.incident_id == incident.id,
                    AnalysisRow.evidence_revision == incident.evidence_revision,
                    AnalysisRow.status.in_(("QUEUED", "RUNNING", "COMPLETED")),
                )
                .order_by(AnalysisRow.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if existing:
                return self._summary(existing, cached=True)
            if key in self._pending:
                row = db.get(AnalysisRow, self._pending[key])
                if row:
                    return self._summary(row, cached=True)
            if not manual:
                previous = self._latest_automatic.get(incident.id)
                cooldown = timedelta(seconds=self.settings.ai_incident_cooldown_seconds)
                if previous and now - previous < cooldown:
                    row = self._new_row(incident, "DEFERRED", trigger, "COOLDOWN")
                    db.add(row)
                    db.flush()
                    return self._summary(row, cached=False)
            row = self._new_row(incident, "QUEUED", trigger)
            db.add(row)
            db.flush()
            job = AnalysisJob(
                priority=0 if incident.severity == "CRITICAL" else 1,
                sequence=next(self._sequence),
                analysis_id=row.id,
                incident_id=incident.id,
                evidence_revision=incident.evidence_revision,
                trigger=trigger,
            )
            try:
                self.queue.put_nowait(job)
            except asyncio.QueueFull:
                row.status = "DEFERRED"
                row.result = {"fallback_reason": "QUEUE_FULL", "trigger": trigger}
            else:
                self._pending[key] = row.id
                self._latest_revision[incident.id] = incident.evidence_revision
                if not manual:
                    self._latest_automatic[incident.id] = now
            return self._summary(row, cached=False)

    def enqueue_history(self, summary: dict[str, Any]) -> dict[str, Any]:
        key = history_window_key(summary)
        with session_scope() as db:
            existing = db.execute(
                select(AnalysisRow)
                .where(
                    AnalysisRow.window_key == key,
                    AnalysisRow.status.in_(("QUEUED", "RUNNING", "COMPLETED")),
                )
                .order_by(AnalysisRow.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if existing:
                return self._summary(existing, cached=True)
            row = AnalysisRow(
                window_key=key,
                evidence_revision=0,
                generation_mode="TEMPLATE",
                status="QUEUED",
                result={"dataset": summary["dataset"], "window": summary["window"]},
                usage={"provider": None, "model": None, "attempts": 0},
            )
            db.add(row)
            db.flush()
            job = AnalysisJob(
                priority=2,
                sequence=next(self._sequence),
                analysis_id=row.id,
                trigger="HISTORY_REVIEW",
                summary=summary,
            )
            try:
                self.queue.put_nowait(job)
            except asyncio.QueueFull:
                row.status = "DEFERRED"
                row.result = {"fallback_reason": "QUEUE_FULL", "window": summary["window"]}
            else:
                self._pending[key] = row.id
            return self._summary(row, cached=False)

    @staticmethod
    def _new_row(
        incident: IncidentRow, status: str, trigger: str, reason: str | None = None
    ) -> AnalysisRow:
        return AnalysisRow(
            run_id=incident.run_id,
            incident_id=incident.id,
            evidence_revision=incident.evidence_revision,
            generation_mode="TEMPLATE",
            status=status,
            result={"trigger": trigger, "fallback_reason": reason}
            if reason
            else {"trigger": trigger},
            usage={"provider": None, "model": None, "attempts": 0},
        )

    @staticmethod
    def _summary(row: AnalysisRow, *, cached: bool) -> dict[str, Any]:
        return {
            "analysis_id": str(row.id),
            "incident_id": str(row.incident_id) if row.incident_id else None,
            "evidence_revision": row.evidence_revision,
            "generation_mode": row.generation_mode,
            "status": row.status,
            "cached": cached,
            "result": row.result,
            "usage": row.usage,
        }

    async def _run(self) -> None:
        while True:
            job = await self.queue.get()
            key: tuple[UUID, int] | str = (
                (job.incident_id, job.evidence_revision)
                if job.incident_id
                else history_window_key(job.summary or {})
            )
            self.active_jobs += 1
            self.max_active_jobs = max(self.max_active_jobs, self.active_jobs)
            try:
                await self._process(job)
            finally:
                self.active_jobs -= 1
                self._pending.pop(key, None)
                self.queue.task_done()

    async def _process(self, job: AnalysisJob) -> None:
        with session_scope() as db:
            row = db.get(AnalysisRow, job.analysis_id)
            if row is None:
                return
            if (
                job.incident_id
                and job.evidence_revision < self._latest_revision.get(job.incident_id, 0)
            ):
                row.status = "DEFERRED"
                row.result = {"trigger": job.trigger, "fallback_reason": "SUPERSEDED_REVISION"}
                return
            row.status = "RUNNING"
        if job.summary is not None:
            await self._process_history(job)
            return
        if job.incident_id is None:
            return
        mode = "TEMPLATE"
        reason: str | None = None
        usage: dict[str, Any] = {"provider": None, "model": None, "attempts": 0}
        provider: OpenAIProvider | None = None
        try:
            if not self._provider_ready():
                reason = "PROVIDER_NOT_CONFIGURED"
                output, trail = await asyncio.to_thread(self._template, job.incident_id)
            elif not await asyncio.to_thread(self._budget_available):
                reason = "DAILY_BUDGET_EXHAUSTED"
                output, trail = await asyncio.to_thread(self._template, job.incident_id)
            else:
                provider = OpenAIProvider(self.settings)
                with session_scope() as db:
                    incident = db.get(IncidentRow, job.incident_id)
                    if incident is None:
                        raise KeyError(job.incident_id)
                    incident_prompt = {
                        "incident_id": str(incident.id),
                        "rule_id": incident.rule_id,
                        "severity": incident.severity,
                        "lifecycle": incident.lifecycle,
                        "affected_entity": incident.affected_entity,
                        "supported_action_ids": list(self.config.actions.enabled_ids),
                    }

                def call_tool(name: str, arguments: dict) -> dict:
                    with session_scope() as tool_db:
                        return InvestigatorToolbox(
                            tool_db,
                            job.incident_id,
                            self.config,
                            max_calls=self.settings.ai_max_tool_calls,
                        ).call(name, arguments)

                result = await provider.investigate(incident_prompt, call_tool)
                allowed = set().union(
                    *(collect_evidence_ids(item) for item in result.evidence_trail)
                )
                allowed.add(f"incident:{job.incident_id}")
                validate_citations(
                    result.output,
                    allowed_evidence_ids=allowed,
                    enabled_actions=set(self.config.actions.enabled_ids),
                )
                mode = "LLM"
                output, trail = result.output, result.evidence_trail
                usage = {
                    "provider": self.settings.llm_provider,
                    "model": self.settings.llm_model,
                    "attempts": result.attempts,
                    "provider_usage": result.usage,
                }
        except Exception as exc:  # provider/tool/schema/citation failures all degrade safely
            reason = type(exc).__name__.upper()
            if provider is not None:
                usage = {
                    "provider": self.settings.llm_provider,
                    "model": self.settings.llm_model,
                    "attempts": getattr(provider, "attempts", 0),
                }
            output, trail = await asyncio.to_thread(self._template, job.incident_id)
        envelope = AnalysisEnvelope(
            trigger=job.trigger,
            investigation=output,
            evidence_trail=trail,
            fallback_reason=reason,
        ).model_dump(mode="json")
        with session_scope() as db:
            row = db.get(AnalysisRow, job.analysis_id)
            if row:
                row.status = "COMPLETED"
                row.generation_mode = mode
                row.result = envelope
                row.usage = usage
                row.completed_at = datetime.now(UTC)

    def _provider_ready(self) -> bool:
        return bool(
            self.settings.ai_enabled
            and self.settings.llm_provider.lower() == "openai"
            and self.settings.llm_model
            and self.settings.llm_api_key
        )

    def _budget_available(self) -> bool:
        start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        with session_scope() as db:
            rows = db.execute(
                select(AnalysisRow.usage).where(AnalysisRow.created_at >= start)
            ).scalars()
            used = sum(int((usage or {}).get("attempts", 0)) for usage in rows)
        return used + self.settings.ai_max_provider_attempts <= self.settings.llm_daily_call_limit

    def _template(self, incident_id: UUID) -> tuple[InvestigationOutput, list[dict[str, Any]]]:
        with session_scope() as db:
            toolbox = InvestigatorToolbox(db, incident_id, self.config)
            evidence = toolbox.call("get_incident_evidence")
            forecasts = toolbox.call("get_forecast_comparison")
        incident_ref = f"incident:{incident_id}"
        forecast_actions = {
            row.get("action_id")
            for row in forecasts["forecasts"]
            if row.get("action_id") in self.config.actions.enabled_ids
        }
        output = InvestigationOutput(
            observations=[
                CitedStatement(
                    statement=(
                        f"Stored observations classify {evidence['rule_id']} as "
                        f"{evidence['severity']} with lifecycle {evidence['lifecycle']}."
                    ),
                    evidence_ids=[incident_ref],
                )
            ],
            hypotheses=[],
            missing_evidence=["Root cause is not established by the available observed telemetry."],
            supported_action_ids=sorted(forecast_actions),
            next_checks=[
                "Review the latest valid observed evidence and mathematical forecast.",
                "Verify measured recovery after any separately authorized command.",
            ],
        )
        return output, [
            {"tool": "get_incident_evidence", "result": evidence},
            {"tool": "get_forecast_comparison", "result": forecasts},
        ]

    def get(self, analysis_id: UUID) -> dict[str, Any] | None:
        with session_scope() as db:
            row = db.get(AnalysisRow, analysis_id)
            return self._summary(row, cached=True) if row else None

    async def _process_history(self, job: AnalysisJob) -> None:
        assert job.summary is not None
        mode = "TEMPLATE"
        reason: str | None = None
        usage: dict[str, Any] = {"provider": None, "model": None, "attempts": 0}
        provider: OpenAIProvider | None = None
        result: dict[str, Any]
        try:
            if not self._provider_ready():
                reason = "PROVIDER_NOT_CONFIGURED"
                result = render_period_review(job.summary)
            elif not await asyncio.to_thread(self._budget_available):
                reason = "DAILY_BUDGET_EXHAUSTED"
                result = render_period_review(job.summary)
            else:
                provider = OpenAIProvider(self.settings)
                review, provider_usage, attempts = await provider.review(job.summary)
                result = review.model_dump(mode="json")
                mode = "LLM"
                usage = {
                    "provider": self.settings.llm_provider,
                    "model": self.settings.llm_model,
                    "attempts": attempts,
                    "provider_usage": provider_usage,
                }
        except Exception as exc:
            reason = type(exc).__name__.upper()
            if provider is not None:
                usage = {
                    "provider": self.settings.llm_provider,
                    "model": self.settings.llm_model,
                    "attempts": getattr(provider, "attempts", 0),
                }
            result = render_period_review(job.summary)
        if reason:
            result["fallback_reason"] = reason
        with session_scope() as db:
            row = db.get(AnalysisRow, job.analysis_id)
            if row:
                row.status = "COMPLETED"
                row.generation_mode = mode
                row.result = result
                row.usage = usage
                row.completed_at = datetime.now(UTC)
