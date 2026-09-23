"""Phase 7 acceptance checks for the bounded investigator."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text

from app.analysis.contracts import CitedStatement, InvestigationOutput, validate_citations
from app.analysis.provider import ProviderError, ProviderResult, ProviderTimeout
from app.analysis.service import AnalysisService
from app.config.loader import load_configuration
from app.config.settings import Settings
from app.storage.models import IncidentRow, Run
from tests.conftest import csrf_headers, login, logout


def _incident(session_factory, *, subsystem: str = "POWER") -> IncidentRow:
    now = datetime.now(UTC)
    with session_factory() as db:
        run = Run(
            source="LIVE",
            seed=7,
            config_version="test",
            model_version="test",
            sim_epoch_utc=now,
            status="STOPPED",
            control_revision=0,
            label="phase7",
            ended_at=now + timedelta(minutes=1),
        )
        db.add(run)
        db.flush()
        row = IncidentRow(
            run_id=run.id,
            rule_id="ENERGY_RESERVE",
            rule_version="test",
            family="POWER_ENERGY",
            subsystem=subsystem,
            affected_entity="battery",
            affected_groups=[subsystem],
            severity="WARNING",
            lifecycle="OPEN",
            first_seen_utc=now,
            last_seen_utc=now,
            evidence={"observed_evidence": []},
            evidence_revision=3,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return row


def test_structured_output_rejects_bad_evidence_and_actions():
    output = InvestigationOutput(
        observations=[CitedStatement(statement="Observed.", evidence_ids=["frame:9"])],
        hypotheses=[],
        missing_evidence=[],
        supported_action_ids=["NOT_CONFIGURED"],
        next_checks=["Check."],
    )
    with pytest.raises(ValueError, match="unsupported evidence"):
        validate_citations(
            output, allowed_evidence_ids={"frame:1"}, enabled_actions={"REDUCE_PAYLOAD_POWER"}
        )


def test_queue_dedup_cooldown_and_template_fallback(session_factory, engine):
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE telemetry_frames, runs RESTART IDENTITY CASCADE"))
    incident = _incident(session_factory)
    async def exercise() -> None:
        service = AnalysisService(
            load_configuration(),
            Settings(ai_enabled=False, ai_incident_cooldown_seconds=120),
        )
        await service.startup()
        try:
            first = service.enqueue_incident(incident.id, "INCIDENT_OPENED")
            duplicate = service.enqueue_incident(incident.id, "INCIDENT_OPENED")
            assert first["analysis_id"] == duplicate["analysis_id"]
            await asyncio.wait_for(service.queue.join(), timeout=5)
            result = service.get(UUID(first["analysis_id"]))
            assert result is not None
            assert result["generation_mode"] == "TEMPLATE"
            assert result["status"] == "COMPLETED"
            assert result["result"]["fallback_reason"] == "PROVIDER_NOT_CONFIGURED"
            assert service.max_active_jobs == 1
            cached = service.enqueue_incident(incident.id, "SEVERITY_ESCALATED")
            assert cached["analysis_id"] == first["analysis_id"]
        finally:
            await service.shutdown()

    asyncio.run(exercise())


def test_manual_api_is_role_scoped_and_returns_template(client, session_factory, engine):
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE telemetry_frames, runs RESTART IDENTITY CASCADE"))
    incident = _incident(session_factory)
    login(client, "thermal")
    denied = client.post(
        f"/api/incidents/{incident.id}/investigate", headers=csrf_headers(client)
    )
    assert denied.status_code == 403

    logout(client)
    login(client, "power")
    accepted = client.post(
        f"/api/incidents/{incident.id}/investigate", headers=csrf_headers(client)
    )
    assert accepted.status_code == 202, accepted.text
    analysis_id = accepted.json()["analysis_id"]
    for _ in range(30):
        result = client.get(f"/api/analyses/{analysis_id}")
        assert result.status_code == 200
        if result.json()["status"] == "COMPLETED":
            break
        import time

        time.sleep(0.02)
    body = result.json()
    assert body["generation_mode"] == "TEMPLATE"
    assert body["result"]["fallback_reason"] == "PROVIDER_NOT_CONFIGURED"
    forbidden = (
        "private_checkpoint",
        "hidden_conditions",
        "computer_resets",
        "branch_stuck_on",
        "isolator_failed",
    )
    assert not any(field in result.text for field in forbidden)


def test_provider_failures_bad_citations_and_budget_use_template(
    session_factory, engine, monkeypatch
):
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE telemetry_frames, runs RESTART IDENTITY CASCADE"))

    class TimeoutProvider:
        def __init__(self, settings):
            pass

        async def investigate(self, incident, call_tool):
            raise ProviderTimeout("timeout")

    class InvalidProvider:
        def __init__(self, settings):
            pass

        async def investigate(self, incident, call_tool):
            raise ProviderError("invalid structured output")

    class BadCitationProvider:
        def __init__(self, settings):
            pass

        async def investigate(self, incident, call_tool):
            return ProviderResult(
                output=InvestigationOutput(
                    observations=[
                        CitedStatement(statement="Unsupported.", evidence_ids=["frame:999999"])
                    ],
                    hypotheses=[],
                    missing_evidence=[],
                    supported_action_ids=[],
                    next_checks=["Check stored evidence."],
                ),
                evidence_trail=[
                    {
                        "tool": "get_incident_evidence",
                        "result": call_tool("get_incident_evidence", {}),
                    }
                ],
                usage={},
                attempts=2,
            )

    async def exercise(provider, expected: str, *, daily_limit: int = 30) -> None:
        incident = _incident(session_factory)
        monkeypatch.setattr("app.analysis.service.OpenAIProvider", provider)
        service = AnalysisService(
            load_configuration(),
            Settings(
                AI_ENABLED=True,
                LLM_PROVIDER="openai",
                LLM_MODEL="test-model",
                LLM_API_KEY="test-key-not-used",
                LLM_DAILY_CALL_LIMIT=daily_limit,
            ),
        )
        await service.startup()
        try:
            queued = service.enqueue_incident(incident.id, "MANUAL_REQUEST", manual=True)
            await asyncio.wait_for(service.queue.join(), timeout=5)
            completed = service.get(UUID(queued["analysis_id"]))
            assert completed is not None
            assert completed["generation_mode"] == "TEMPLATE"
            assert completed["result"]["fallback_reason"] == expected
        finally:
            await service.shutdown()

    asyncio.run(exercise(TimeoutProvider, "PROVIDERTIMEOUT"))
    asyncio.run(exercise(InvalidProvider, "PROVIDERERROR"))
    asyncio.run(exercise(BadCitationProvider, "VALUEERROR"))
    asyncio.run(exercise(InvalidProvider, "DAILY_BUDGET_EXHAUSTED", daily_limit=0))


def test_queue_bound_cooldown_and_trigger_allowlist(session_factory, engine):
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE telemetry_frames, runs RESTART IDENTITY CASCADE"))
    first = _incident(session_factory)
    second = _incident(session_factory)
    service = AnalysisService(
        load_configuration(),
        Settings(AI_PENDING_QUEUE_LIMIT=1, AI_INCIDENT_COOLDOWN_SECONDS=120),
    )
    queued = service.enqueue_incident(first.id, "INCIDENT_OPENED")
    assert queued["status"] == "QUEUED"
    full = service.enqueue_incident(second.id, "INCIDENT_OPENED")
    assert full["status"] == "DEFERRED"
    assert full["result"]["fallback_reason"] == "QUEUE_FULL"
    with session_factory() as db:
        row = db.get(IncidentRow, first.id)
        row.evidence_revision += 1
        db.commit()
    cooldown = service.enqueue_incident(first.id, "SEVERITY_ESCALATED")
    assert cooldown["status"] == "DEFERRED"
    assert cooldown["result"]["fallback_reason"] == "COOLDOWN"
    with pytest.raises(ValueError, match="unsupported investigation trigger"):
        service.enqueue_incident(first.id, "EVERY_TELEMETRY_FRAME")
