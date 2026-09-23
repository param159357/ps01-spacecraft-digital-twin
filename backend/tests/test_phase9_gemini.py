"""Phase 9 Gemini adapter boundaries and safe degradation."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from google.genai import types

from app.analysis.contracts import InvestigationOutput, validate_citations
from app.analysis.provider import GeminiProvider, ProviderError, _gemini_response_schema
from app.config.settings import Settings


def _response(*, calls: list[str] | None = None, output: dict | None = None):
    if calls is not None:
        parts = [
            types.Part(function_call=types.FunctionCall(name=name, args={})) for name in calls
        ]
        return types.GenerateContentResponse(
            candidates=[types.Candidate(content=types.Content(role="model", parts=parts))]
        )
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model", parts=[types.Part.from_text(text=json.dumps(output))]
                )
            )
        ]
    )


class _Models:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.responses)


class _Client:
    def __init__(self, responses):
        self.models = _Models(responses)
        self.closed = False

    def close(self):
        self.closed = True


def _valid_output(evidence_id: str = "incident:abc") -> dict:
    return {
        "observations": [
            {
                "statement": "Stored incident evidence exists.",
                "evidence_ids": [evidence_id],
            }
        ],
        "hypotheses": [],
        "missing_evidence": ["Root cause is not observed."],
        "supported_action_ids": [],
        "next_checks": ["Review the next stored observation."],
    }


def _contains_key(value, target: str) -> bool:
    if isinstance(value, dict):
        return target in value or any(_contains_key(child, target) for child in value.values())
    if isinstance(value, list):
        return any(_contains_key(child, target) for child in value)
    return False


def test_gemini_schema_conversion_removes_nested_additional_properties_from_a_copy():
    original = {
        "additionalProperties": False,
        "$defs": {
            "Nested": {
                "type": "object",
                "additionalProperties": False,
                "items": [{"additionalProperties": True, "type": "object"}],
            }
        },
    }

    converted = _gemini_response_schema(original)

    assert _contains_key(converted, "additionalProperties") is False
    assert _contains_key(original, "additionalProperties") is True


def test_gemini_converts_calls_continues_with_results_and_validates_output():
    client = _Client(
        [
            _response(calls=["get_incident_evidence", "get_forecast_comparison"]),
            _response(output=_valid_output()),
        ]
    )
    provider = GeminiProvider(
        Settings(
            LLM_MODEL="authorized-test-model",
            GEMINI_API_KEY="test-key",
            AI_MAX_PROVIDER_ATTEMPTS=2,
        ),
        client=client,
    )
    invoked = []

    result = asyncio.run(
        provider.investigate(
            {"incident_id": "abc", "supported_action_ids": []},
            lambda name, arguments: invoked.append((name, arguments))
            or {"incident_id": "abc"},
        )
    )

    assert invoked == [
        ("get_incident_evidence", {}),
        ("get_forecast_comparison", {}),
    ]
    assert result.output.observations[0].evidence_ids == ["incident:abc"]
    assert len(client.models.calls) == 2
    continuation = client.models.calls[1]["contents"]
    assert continuation[1].role == "model"
    assert continuation[2].role == "tool"
    assert {part.function_response.name for part in continuation[2].parts} == {
        "get_incident_evidence",
        "get_forecast_comparison",
    }
    assert client.models.calls[1]["config"].response_mime_type == "application/json"
    investigation_schema = client.models.calls[1]["config"].response_json_schema
    assert _contains_key(investigation_schema, "additionalProperties") is False
    assert client.closed is True


def test_gemini_history_review_uses_the_compatible_schema():
    output = {
        "title": "Stored history review",
        "findings": ["One stored recurrence was observed."],
        "limitations": ["No hidden cause was inferred."],
    }
    client = _Client([_response(output=output)])
    provider = GeminiProvider(
        Settings(
            LLM_MODEL="authorized-test-model",
            GEMINI_API_KEY="test-key",
            AI_MAX_PROVIDER_ATTEMPTS=1,
        ),
        client=client,
    )

    review, _, attempts = asyncio.run(provider.review({"dataset": "SYNTHETIC"}))

    assert review.title == output["title"]
    assert attempts == 1
    history_schema = client.models.calls[0]["config"].response_json_schema
    assert _contains_key(history_schema, "additionalProperties") is False
    assert client.closed is True


def test_gemini_invalid_evidence_reference_is_rejected():
    output = InvestigationOutput.model_validate(_valid_output("frame:future-secret"))
    with pytest.raises(ValueError, match="unsupported evidence references"):
        validate_citations(
            output,
            allowed_evidence_ids={"incident:abc", "frame:12"},
            enabled_actions=set(),
        )


def test_gemini_provider_error_is_generic_and_does_not_disclose_secret():
    secret = "never-disclose-gemini-secret"

    class BrokenModels:
        def generate_content(self, **kwargs):
            raise RuntimeError(f"transport included {secret}")

    client = SimpleNamespace(models=BrokenModels(), close=lambda: None)
    provider = GeminiProvider(
        Settings(
            LLM_MODEL="authorized-test-model",
            GEMINI_API_KEY=secret,
            AI_MAX_PROVIDER_ATTEMPTS=1,
        ),
        client=client,
    )
    with pytest.raises(ProviderError) as caught:
        asyncio.run(provider.review({"dataset": "SYNTHETIC"}))
    assert secret not in str(caught.value)


def test_gemini_timeout_and_budget_readiness_degrade_before_provider_use(monkeypatch):
    class SlowModels:
        def generate_content(self, **kwargs):
            time.sleep(0.05)
            return _response(output=_valid_output())

    client = SimpleNamespace(models=SlowModels(), close=lambda: None)
    provider = GeminiProvider(
        Settings(
            LLM_MODEL="authorized-test-model",
            GEMINI_API_KEY="test-key",
            LLM_TIMEOUT_SECONDS=0,
            AI_MAX_PROVIDER_ATTEMPTS=1,
        ),
        client=client,
    )
    with pytest.raises(ProviderError):
        asyncio.run(provider.review({"dataset": "SYNTHETIC"}))

    from app.analysis.service import AnalysisService
    from app.config.loader import load_configuration

    service = AnalysisService(
        load_configuration(),
        Settings(
            AI_ENABLED=True,
            LLM_PROVIDER="gemini",
            LLM_MODEL="authorized-test-model",
            GEMINI_API_KEY="test-key",
            LLM_DAILY_CALL_LIMIT=0,
        ),
    )
    assert service._provider_ready() is True
    monkeypatch.setattr(service, "_budget_available", lambda: False)
    assert service._budget_available() is False
