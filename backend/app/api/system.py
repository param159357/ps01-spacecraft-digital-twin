"""Health and configuration endpoints.

The configuration endpoints exist so the frontend renders units, limits and coverage
from the versioned fixture files rather than hard-coding them in UI components, and so
unimplemented rules and disabled actions are shown honestly instead of implied.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import current_role
from app.config.loader import load_configuration
from app.contracts.common import Role, visible_groups

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict[str, str]:
    """Minimal process liveness. Contains no secrets and needs no session."""
    return {"status": "ok"}


@router.get("/config/channels")
def channels(role: Role = Depends(current_role)) -> dict[str, Any]:
    """Role-filtered channel dictionary: units, provenance, limits, prediction coverage."""
    config = load_configuration()
    allowed = visible_groups(role)

    items = []
    for channel in config.telemetry.channels:
        if not allowed.intersection(g.value for g in channel.groups):
            continue
        items.append(
            {
                "id": channel.id,
                "label": channel.label,
                "unit": channel.unit,
                "groups": [g.value for g in channel.groups],
                "type": channel.type.value,
                "provenance": channel.provenance.value,
                "description": channel.description,
                "operating_min": channel.bounds.operating[0],
                "operating_max": channel.bounds.operating[1],
                "display_min": channel.bounds.display[0],
                "display_max": channel.bounds.display[1],
                "prediction": channel.prediction.value,
                "rule_refs": list(channel.rule_refs),
            }
        )

    return {
        "config_version": config.telemetry.config_version,
        "freshness": {
            "warning_after_s": config.telemetry.freshness.warning_after_s,
            "critical_after_s": config.telemetry.freshness.critical_after_s,
        },
        "channels": items,
    }


@router.get("/config/coverage")
def coverage(role: Role = Depends(current_role)) -> dict[str, Any]:
    """What actually works right now. Unimplemented rules and disabled actions are
    reported as such so no screen implies coverage the backend does not have."""
    config = load_configuration()
    return {
        "config_version": config.config_version,
        "model_version": config.spacecraft.model_version,
        "vehicle_name": config.spacecraft.vehicle_name,
        "rules": [
            {
                "id": rule.id,
                "family": rule.family,
                "subsystem": rule.subsystem,
                "severity_default": rule.severity_default,
                "implemented": rule.implemented,
                "planned_phase": rule.phase,
                "description": rule.description.strip(),
            }
            for rule in config.rules.rules
        ],
        "actions": [
            {
                "id": action.id,
                "label": action.label,
                "subsystem": action.subsystem,
                "enabled": action.enabled,
                "planned_phase": action.phase,
                "operation": action.operation,
                "prerequisites": list(action.prerequisites),
                "trade_off": action.trade_off.strip(),
            }
            for action in config.actions.actions
        ],
        "executable_by_this_role": role is Role.MASTER,
    }
