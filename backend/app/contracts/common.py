"""Shared enumerations and role/visibility rules.

These types are the single source of truth for roles, subsystems and data quality.
The browser never assigns its own role; the server derives it from the session.
"""

from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    SIMULATOR = "SIMULATOR"
    MASTER = "MASTER"
    POWER = "POWER"
    THERMAL_PROPULSION = "THERMAL_PROPULSION"
    FLIGHT_SYSTEMS = "FLIGHT_SYSTEMS"


class Subsystem(str, Enum):
    POWER = "POWER"
    THERMAL_PROPULSION = "THERMAL_PROPULSION"
    FLIGHT_SYSTEMS = "FLIGHT_SYSTEMS"
    MASTER = "MASTER"


class QualityState(str, Enum):
    """Missing data is never zero and never healthy (section 6.2)."""

    VALID = "VALID"
    MISSING = "MISSING"
    STALE = "STALE"
    INVALID = "INVALID"
    SUSPECT = "SUSPECT"


class Illumination(str, Enum):
    SUNLIGHT = "SUNLIGHT"
    ECLIPSE = "ECLIPSE"


class OperatingMode(str, Enum):
    NOMINAL = "NOMINAL"
    PAYLOAD_ACTIVE = "PAYLOAD_ACTIVE"
    SAFE = "SAFE"
    MANEUVER = "MANEUVER"


class RunStatus(str, Enum):
    PAUSED = "PAUSED"
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"


class SourceKind(str, Enum):
    """A replay or synthetic dataset must never be presented as live."""

    LIVE = "LIVE"
    SYNTHETIC = "SYNTHETIC"
    REPLAY = "REPLAY"


# Home route per role. Fixed paths, compatible with Next.js static export.
ROLE_HOME: dict[Role, str] = {
    Role.SIMULATOR: "/simulator/",
    Role.MASTER: "/master/",
    Role.POWER: "/power/",
    Role.THERMAL_PROPULSION: "/thermal/",
    Role.FLIGHT_SYSTEMS: "/flight/",
}

# Telemetry groups each role may read. The simulator sees operational telemetry too,
# but hidden simulator truth is served only by the simulator-only endpoints.
ROLE_GROUPS: dict[Role, frozenset[str]] = {
    Role.SIMULATOR: frozenset({"POWER", "THERMAL", "FLIGHT", "MASTER"}),
    Role.MASTER: frozenset({"POWER", "THERMAL", "FLIGHT", "MASTER"}),
    Role.POWER: frozenset({"POWER", "THERMAL"}),
    Role.THERMAL_PROPULSION: frozenset({"THERMAL", "POWER"}),
    Role.FLIGHT_SYSTEMS: frozenset({"FLIGHT", "POWER"}),
}

# Incidents a specialist may acknowledge / investigate.
ROLE_SUBSYSTEMS: dict[Role, frozenset[Subsystem]] = {
    Role.SIMULATOR: frozenset(),
    Role.MASTER: frozenset(Subsystem),
    Role.POWER: frozenset({Subsystem.POWER}),
    Role.THERMAL_PROPULSION: frozenset({Subsystem.THERMAL_PROPULSION}),
    Role.FLIGHT_SYSTEMS: frozenset({Subsystem.FLIGHT_SYSTEMS}),
}

# Only the master executes recovery commands (section 3).
COMMAND_ROLES: frozenset[Role] = frozenset({Role.MASTER})

# Only the simulator account touches run control and condition injection.
SIMULATOR_ROLES: frozenset[Role] = frozenset({Role.SIMULATOR})


def home_route(role: Role) -> str:
    return ROLE_HOME[role]


def visible_groups(role: Role) -> frozenset[str]:
    return ROLE_GROUPS[role]


def may_act_on(role: Role, subsystem: Subsystem) -> bool:
    return subsystem in ROLE_SUBSYSTEMS[role]
