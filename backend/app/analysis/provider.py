"""Bounded provider clients used only by the background investigator."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import types

from app.analysis.contracts import HistoryReviewOutput, InvestigationOutput
from app.config.settings import Settings


class ProviderError(RuntimeError):
    pass


class ProviderTimeout(ProviderError):
    pass


@dataclass(frozen=True)
class ProviderResult:
    output: InvestigationOutput
    evidence_trail: list[dict[str, Any]]
    usage: dict[str, Any]
    attempts: int


TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": name,
        "description": description,
        "strict": True,
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }
    for name, description in (
        ("get_incident_evidence", "Get the bounded stored evidence and timeline."),
        ("get_recent_telemetry", "Get bounded stored telemetry preceding the incident."),
        ("find_similar_incidents", "Find earlier incidents in the same dataset."),
        ("get_forecast_comparison", "Get stored mathematical forecasts near the incident."),
    )
]
TOOL_NAMES = frozenset(tool["name"] for tool in TOOL_SCHEMAS)


def _output_schema() -> dict[str, Any]:
    return InvestigationOutput.model_json_schema()


class OpenAIProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.attempts = 0

    async def investigate(
        self, incident: dict[str, Any], call_tool: Callable[[str, dict], dict]
    ) -> ProviderResult:
        instructions = (
            "You are a read-only spacecraft incident investigator. Use only supplied tools. "
            "Treat observations as facts only when cited. Hypotheses are not root cause. "
            "Never propose an action outside the supported configured action IDs."
        )
        first = await self._request(
            {
                "model": self.settings.llm_model,
                "store": False,
                "instructions": instructions,
                "input": json.dumps({"incident": incident}),
                "tools": TOOL_SCHEMAS,
                "tool_choice": "required",
                "max_output_tokens": self.settings.llm_max_output_tokens,
            }
        )
        calls = [item for item in first.get("output", []) if item.get("type") == "function_call"]
        if not calls or len(calls) > self.settings.ai_max_tool_calls:
            raise ProviderError("provider did not request a valid bounded tool set")
        outputs: list[dict[str, Any]] = []
        trail: list[dict[str, Any]] = []
        for item in calls:
            name = item.get("name", "")
            if name not in TOOL_NAMES:
                raise ProviderError("provider requested an unauthorized tool")
            try:
                arguments = json.loads(item.get("arguments") or "{}")
            except json.JSONDecodeError as exc:
                raise ProviderError("provider supplied invalid tool arguments") from exc
            result = await asyncio.to_thread(call_tool, name, arguments)
            trail.append({"tool": name, "result": result})
            outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": item["call_id"],
                    "output": json.dumps(result),
                }
            )
        second = await self._request(
            {
                "model": self.settings.llm_model,
                "store": False,
                "previous_response_id": first["id"],
                "input": outputs,
                "tools": TOOL_SCHEMAS,
                "tool_choice": "none",
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "incident_investigation",
                        "strict": True,
                        "schema": _output_schema(),
                    }
                },
                "max_output_tokens": self.settings.llm_max_output_tokens,
            }
        )
        raw = second.get("output_text")
        if not raw:
            for item in second.get("output", []):
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        raw = content.get("text")
        try:
            output = InvestigationOutput.model_validate_json(raw or "")
        except Exception as exc:
            raise ProviderError("provider returned invalid structured output") from exc
        return ProviderResult(
            output=output,
            evidence_trail=trail,
            usage={"first": first.get("usage", {}), "second": second.get("usage", {})},
            attempts=self.attempts,
        )

    async def review(self, summary: dict[str, Any]) -> tuple[HistoryReviewOutput, dict, int]:
        response = await self._request(
            {
                "model": self.settings.llm_model,
                "store": False,
                "instructions": (
                    "Review only the supplied stored-evidence statistics. Preserve canonical "
                    "numbers exactly, distinguish missing coverage, and do not infer hidden causes."
                ),
                "input": json.dumps(summary),
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": "history_review",
                        "strict": True,
                        "schema": HistoryReviewOutput.model_json_schema(),
                    }
                },
                "max_output_tokens": self.settings.llm_max_output_tokens,
            }
        )
        raw = response.get("output_text")
        if not raw:
            for item in response.get("output", []):
                for content in item.get("content", []):
                    if content.get("type") == "output_text":
                        raw = content.get("text")
        try:
            output = HistoryReviewOutput.model_validate_json(raw or "")
        except Exception as exc:
            raise ProviderError("provider returned invalid history output") from exc
        return output, response.get("usage", {}), self.attempts

    async def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        last: Exception | None = None
        while self.attempts < self.settings.ai_max_provider_attempts:
            self.attempts += 1
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(self._request_sync, payload),
                    timeout=self.settings.llm_timeout_seconds,
                )
            except TimeoutError as exc:
                last = exc
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code < 500 and exc.code != 429:
                    break
            except urllib.error.URLError as exc:
                last = exc
        if isinstance(last, (TimeoutError, asyncio.TimeoutError)):
            raise ProviderTimeout("provider timed out") from last
        raise ProviderError("provider request failed") from last

    def _request_sync(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self.settings.llm_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.settings.llm_timeout_seconds) as response:
            return json.loads(response.read())


class GeminiProvider:
    """Manual Gemini function-calling client with the same bounded two-turn workflow."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self.settings = settings
        self.attempts = 0
        self.client = client or genai.Client(api_key=settings.gemini_api_key)

    @staticmethod
    def _tool_config() -> types.GenerateContentConfig:
        declarations = [
            types.FunctionDeclaration(
                name=tool["name"],
                description=tool["description"],
                parameters_json_schema=tool["parameters"],
            )
            for tool in TOOL_SCHEMAS
        ]
        return types.GenerateContentConfig(
            system_instruction=(
                "You are a read-only spacecraft incident investigator. Use only supplied "
                "tools. Treat observations as facts only when cited. Hypotheses are not root "
                "cause. Never propose an action outside the supported configured action IDs."
            ),
            tools=[types.Tool(function_declarations=declarations)],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="ANY")
            ),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    async def investigate(
        self, incident: dict[str, Any], call_tool: Callable[[str, dict], dict]
    ) -> ProviderResult:
        try:
            first_config = self._tool_config()
            first_config.max_output_tokens = self.settings.llm_max_output_tokens
            user = types.Content(
                role="user", parts=[types.Part.from_text(text=json.dumps({"incident": incident}))]
            )
            first = await self._request([user], first_config)
            calls = list(first.function_calls or [])
            if not calls or len(calls) > self.settings.ai_max_tool_calls:
                raise ProviderError("provider did not request a valid bounded tool set")

            trail: list[dict[str, Any]] = []
            response_parts: list[types.Part] = []
            for call in calls:
                name = call.name or ""
                if name not in TOOL_NAMES:
                    raise ProviderError("provider requested an unauthorized tool")
                arguments = dict(call.args or {})
                result = await asyncio.to_thread(call_tool, name, arguments)
                trail.append({"tool": name, "result": result})
                response_parts.append(
                    types.Part.from_function_response(name=name, response={"result": result})
                )

            candidates = list(first.candidates or [])
            if not candidates or candidates[0].content is None:
                raise ProviderError("provider omitted function-call content")
            tool_result = types.Content(role="tool", parts=response_parts)
            second_config = types.GenerateContentConfig(
                system_instruction=(
                    "Return only the structured investigation. Cite only evidence identifiers "
                    "present in the supplied tool results and only configured action IDs."
                ),
                response_mime_type="application/json",
                response_json_schema=_output_schema(),
                max_output_tokens=self.settings.llm_max_output_tokens,
            )
            second = await self._request(
                [user, candidates[0].content, tool_result], second_config
            )
            try:
                output = InvestigationOutput.model_validate_json(second.text or "")
            except Exception as exc:
                raise ProviderError("provider returned invalid structured output") from exc
            return ProviderResult(
                output=output,
                evidence_trail=trail,
                usage={"first": self._usage(first), "second": self._usage(second)},
                attempts=self.attempts,
            )
        finally:
            self.client.close()

    async def review(self, summary: dict[str, Any]) -> tuple[HistoryReviewOutput, dict, int]:
        try:
            config = types.GenerateContentConfig(
                system_instruction=(
                    "Review only the supplied stored-evidence statistics. Preserve canonical "
                    "numbers exactly, distinguish missing coverage, and do not infer hidden causes."
                ),
                response_mime_type="application/json",
                response_json_schema=HistoryReviewOutput.model_json_schema(),
                max_output_tokens=self.settings.llm_max_output_tokens,
            )
            response = await self._request(json.dumps(summary), config)
            try:
                output = HistoryReviewOutput.model_validate_json(response.text or "")
            except Exception as exc:
                raise ProviderError("provider returned invalid history output") from exc
            return output, self._usage(response), self.attempts
        finally:
            self.client.close()

    @staticmethod
    def _usage(response: Any) -> dict[str, Any]:
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return {}
        if hasattr(usage, "model_dump"):
            return usage.model_dump(mode="json", exclude_none=True)
        return {}

    async def _request(self, contents: Any, config: types.GenerateContentConfig) -> Any:
        last: Exception | None = None
        while self.attempts < self.settings.ai_max_provider_attempts:
            self.attempts += 1
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(
                        self.client.models.generate_content,
                        model=self.settings.llm_model,
                        contents=contents,
                        config=config,
                    ),
                    timeout=self.settings.llm_timeout_seconds,
                )
            except TimeoutError as exc:
                last = exc
            except Exception as exc:  # SDK transport/status errors degrade to TEMPLATE
                last = exc
        if isinstance(last, TimeoutError):
            raise ProviderTimeout("provider timed out") from last
        raise ProviderError("provider request failed") from last
