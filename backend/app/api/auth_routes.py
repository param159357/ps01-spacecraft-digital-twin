"""Authentication endpoints: POST /auth/login, POST /auth/logout, GET /auth/me."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_session, require_csrf, session_user
from app.auth import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    AuthenticatedSession,
    create_session,
    hash_password,
    login_throttle,
    needs_rehash,
    origin_allowed,
    revoke_session,
    verify_password,
)
from app.config.settings import get_settings
from app.contracts.auth import LoginRequest, LoginResponse, SessionUser
from app.contracts.common import COMMAND_ROLES, SIMULATOR_ROLES, Role, home_route, visible_groups
from app.storage.db import get_session
from app.storage.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookies(response: Response, session_token: str, csrf_token: str) -> None:
    settings = get_settings()
    max_age = settings.session_ttl_hours * 3600
    # HttpOnly: the session token must be unreadable from page scripts.
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    # Readable by the frontend on purpose: it is echoed back in the CSRF header.
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def _to_session_user(user: User) -> SessionUser:
    role = Role(user.role)
    return SessionUser(
        username=user.username,
        role=role,
        home_route=home_route(role),
        visible_groups=tuple(sorted(visible_groups(role))),
        can_execute_commands=role in COMMAND_ROLES,
        can_control_simulator=role in SIMULATOR_ROLES,
    )


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_session),
) -> LoginResponse:
    # The login origin is protected too, not only authenticated mutations.
    if not origin_allowed(request.headers.get("origin"), request.headers.get("referer")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "ORIGIN_NOT_ALLOWED", "message": "Request origin is not permitted."},
        )

    client_ip = request.client.host if request.client else "unknown"
    throttle_key = f"{payload.username.lower()}|{client_ip}"
    blocked_for = login_throttle.blocked_for(throttle_key)
    if blocked_for > 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "TOO_MANY_ATTEMPTS",
                "message": f"Too many sign-in attempts. Try again in {int(blocked_for) + 1}s.",
            },
            headers={"Retry-After": str(int(blocked_for) + 1)},
        )

    user = db.execute(select(User).where(User.username == payload.username)).scalar_one_or_none()

    # Identical response for unknown user, wrong password and deactivated account.
    if user is None or not user.active or not verify_password(user.password_hash, payload.password):
        login_throttle.record_failure(throttle_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "INVALID_CREDENTIALS", "message": "Incorrect username or password."},
        )

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)

    login_throttle.record_success(throttle_key)
    issued = create_session(db, user)
    _set_session_cookies(response, issued.session_token, issued.csrf_token)
    return LoginResponse(user=_to_session_user(user), csrf_token=issued.csrf_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    auth: AuthenticatedSession = Depends(require_csrf),
    db: Session = Depends(get_session),
) -> Response:
    revoke_session(db, auth.session)
    _clear_session_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=SessionUser)
def me(user: SessionUser = Depends(session_user)) -> SessionUser:
    """The server returns the role. The browser never assigns its own."""
    return user


@router.get("/csrf")
def csrf(auth: AuthenticatedSession = Depends(current_session)) -> dict[str, str]:
    """Confirms a session is live. The CSRF value itself stays in its readable cookie."""
    return {"status": "ok", "username": auth.user.username}
