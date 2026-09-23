"""Strict, evidence-cited Phase 7 investigator output contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CitedStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=20)


class InvestigationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observations: list[CitedStatement] = Field(min_length=1, max_length=20)
    hypotheses: list[CitedStatement] = Field(max_length=10)
    missing_evidence: list[str] = Field(max_length=20)
    supported_action_ids: list[str] = Field(max_length=10)
    next_checks: list[str] = Field(min_length=1, max_length=20)


class AnalysisEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trigger: Literal["INCIDENT_OPENED", "SEVERITY_ESCALATED", "FAILED_RECOVERY", "MANUAL_REQUEST"]
    investigation: InvestigationOutput
    evidence_trail: list[dict]
    fallback_reason: str | None = None


class HistoryReviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    findings: list[str] = Field(min_length=1, max_length=20)
    limitations: list[str] = Field(min_length=1, max_length=10)


def validate_citations(
    output: InvestigationOutput, *, allowed_evidence_ids: set[str], enabled_actions: set[str]
) -> None:
    cited = {
        evidence_id
        for item in (*output.observations, *output.hypotheses)
        for evidence_id in item.evidence_ids
    }
    unknown = cited - allowed_evidence_ids
    if unknown:
        raise ValueError(f"unsupported evidence references: {sorted(unknown)}")
    unknown_actions = set(output.supported_action_ids) - enabled_actions
    if unknown_actions:
        raise ValueError(f"unsupported action IDs: {sorted(unknown_actions)}")
