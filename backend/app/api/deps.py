"""Request dependencies: session resolution, role authorization and CSRF enforcement.

Authorization order matters and is relied on by the Phase 1 acceptance checks:
no session -> 401, wrong role -> 403, unsafe request without a valid CSRF token or
allowed origin -> 403. Only then does a handler run.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth import (
    CSRF_HEADER,
    SESSION_COOKIE,
    AuthenticatedSession,
    csrf_token_matches,
    origin_allowed,
    resolve_session,
)
from app.contracts.auth import SessionUser
from app.contracts.common import (
    COMMAND_ROLES,
    SIMULATOR_ROLES,
    Role,
    Subsystem,
    home_route,
    may_act_on,
    visible_groups,
)
from app.storage.db import get_session


def get_db() -> Session:  # pragma: no cover - thin alias for readability
    raise RuntimeError("use Depends(get_session)")


def current_session(
    request: Request,
    db: Session = Depends(get_session),
) -> AuthenticatedSession:
    """Authenticated session or 401. Never inspects a client-supplied role."""
    token = request.cookies.get(SESSION_COOKIE)
    auth = resolve_session(db, token)
    if auth is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "NOT_AUTHENTICATED", "message": "Sign in to continue."},
        )
    return auth


def current_role(auth: AuthenticatedSession = Depends(current_session)) -> Role:
    return auth.role


def session_user(auth: AuthenticatedSession = Depends(current_session)) -> SessionUser:
    role = auth.role
    return SessionUser(
        username=auth.user.username,
        role=role,
        home_route=home_route(role),
        visible_groups=tuple(sorted(visible_groups(role))),
        can_execute_commands=role in COMMAND_ROLES,
        can_control_simulator=role in SIMULATOR_ROLES,
    )


def require_roles(*roles: Role) -> Callable[..., AuthenticatedSession]:
    """Dependency factory. 401 when unauthenticated, 403 when the role is not allowed."""
    allowed = frozenset(roles)

    def _dependency(auth: AuthenticatedSession = Depends(current_session)) -> AuthenticatedSession:
        if auth.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "ROLE_NOT_PERMITTED",
                    "message": (
                        f"Role {auth.role.value} may not use this endpoint. "
                        f"Permitted: {', '.join(sorted(r.value for r in allowed))}."
                    ),
                },
            )
        return auth

    return _dependency


require_master = require_roles(Role.MASTER)
require_simulator = require_roles(Role.SIMULATOR)
require_operational = require_roles(
    Role.MASTER, Role.POWER, Role.THERMAL_PROPULSION, Role.FLIGHT_SYSTEMS
)


def require_csrf(
    request: Request,
    auth: AuthenticatedSession = Depends(current_session),
) -> AuthenticatedSession:
    """Double-submit CSRF token plus allowed-origin validation for every mutation."""
    if not origin_allowed(request.headers.get("origin"), request.headers.get("referer")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "ORIGIN_NOT_ALLOWED", "message": "Request origin is not permitted."},
        )
    if not csrf_token_matches(auth.session, request.headers.get(CSRF_HEADER)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "CSRF_INVALID", "message": "Missing or invalid CSRF token."},
        )
    return auth


def assert_subsystem_access(role: Role, subsystem: Subsystem) -> None:
    """403 when a specialist reaches outside its assigned scope."""
    if not may_act_on(role, subsystem):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "SUBSYSTEM_NOT_ASSIGNED",
                "message": f"Role {role.value} is not assigned to {subsystem.value}.",
            },
        )


def filter_channels_for_role(role: Role, channel_groups: Iterable[tuple[str, Iterable[str]]]):
    """Yield only the channel IDs whose groups intersect the role's visible groups."""
    allowed = visible_groups(role)
    for channel_id, groups in channel_groups:
        if allowed.intersection(groups):
            yield channel_id


def not_implemented(phase: int, what: str) -> HTTPException:
    """Honest 503 for a contract that exists but has no implementation yet.

    Returned rather than faked so the UI can show unsupported coverage truthfully.
    """
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "error": "NOT_IMPLEMENTED_YET",
            "message": f"{what} is implemented in Phase {phase}.",
            "phase": phase,
        },
    )
