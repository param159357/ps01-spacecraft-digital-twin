"""Role filtering for telemetry payloads.

A specialist sees its own subsystem plus the cross-system dependencies declared in the
channel dictionary, and nothing else. Filtering happens here, at the serialization
boundary, so no route can forget to apply it.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.config.loader import load_configuration
from app.contracts.common import Role, visible_groups


@lru_cache
def channels_for_role(role: Role) -> frozenset[str]:
    telemetry = load_configuration().telemetry
    allowed = visible_groups(role)
    return frozenset(
        channel.id
        for channel in telemetry.channels
        if allowed.intersection(g.value for g in channel.groups)
    )


def filter_frame(frame: dict[str, Any] | None, role: Role) -> dict[str, Any] | None:
    """Return a copy of a serialized frame containing only this role's channels.

    Engineering detail is power-boundary information, so it goes to roles that can see
    power channels. Frame context carries no hidden truth and is passed through intact.
    """
    if frame is None:
        return None

    allowed = channels_for_role(role)
    values = {k: v for k, v in (frame.get("values") or {}).items() if k in allowed}
    quality = {k: v for k, v in (frame.get("quality") or {}).items() if k in allowed}

    filtered = dict(frame)
    filtered["values"] = values
    filtered["quality"] = quality
    if "load_power" not in allowed:
        filtered["engineering"] = {}
    return filtered
