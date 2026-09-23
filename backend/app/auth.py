"""Password hashing, server-side sessions, CSRF tokens and login throttling.

Security posture for this prototype (CLAUDE.md section 3): hashed passwords, server-side
sessions, HttpOnly cookies, secure cookies when deployed over HTTPS, double-submit CSRF
with an origin check, and basic login throttling. Enterprise identity is out of scope.

The plaintext session token exists only in the response cookie and the request header.
Only its SHA-256 hash is stored or compared, and it is never logged.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.contracts.common import Role
from app.storage.models import SessionToken, User

SESSION_COOKIE = "ps01_session"
CSRF_COOKIE = "ps01_csrf"
CSRF_HEADER = "X-CSRF-Token"

_hasher = PasswordHasher()


# --------------------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return True


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


# ------------------------------------------------------------------------------ tokens


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------- sessions


@dataclass(frozen=True)
class IssuedSession:
    session_token: str
    csrf_token: str
    expires_at: datetime


def create_session(db: Session, user: User) -> IssuedSession:
    settings = get_settings()
    session_token = _new_token()
    csrf_token = _new_token()
    expires_at = _utcnow() + timedelta(hours=settings.session_ttl_hours)

    db.add(
        SessionToken(
            token_hash=_sha256(session_token),
            csrf_hash=_sha256(csrf_token),
            user_id=user.id,
            expires_at=expires_at,
        )
    )
    db.flush()
    return IssuedSession(session_token, csrf_token, expires_at)


@dataclass(frozen=True)
class AuthenticatedSession:
    user: User
    session: SessionToken

    @property
    def role(self) -> Role:
        return Role(self.user.role)


def resolve_session(db: Session, session_token: str | None) -> AuthenticatedSession | None:
    """Return the live session, or None when absent, expired, revoked or deactivated."""
    if not session_token:
        return None

    row = db.execute(
        select(SessionToken).where(SessionToken.token_hash == _sha256(session_token))
    ).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None

    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= _utcnow():
        return None

    user = db.get(User, row.user_id)
    if user is None or not user.active:
        return None

    return AuthenticatedSession(user=user, session=row)


def revoke_session(db: Session, session_row: SessionToken) -> None:
    session_row.revoked_at = _utcnow()
    db.flush()


def revoke_all_for_user(db: Session, user_id) -> int:
    rows = db.execute(
        select(SessionToken).where(
            SessionToken.user_id == user_id, SessionToken.revoked_at.is_(None)
        )
    ).scalars()
    count = 0
    now = _utcnow()
    for row in rows:
        row.revoked_at = now
        count += 1
    db.flush()
    return count


# -------------------------------------------------------------------------------- CSRF


def csrf_token_matches(session_row: SessionToken, presented: str | None) -> bool:
    if not presented:
        return False
    return hmac.compare_digest(session_row.csrf_hash, _sha256(presented))


def origin_allowed(origin: str | None, referer: str | None) -> bool:
    """Allowed-origin validation for authenticated mutations and for login.

    A same-origin browser request may omit Origin, so Referer is accepted as a
    fallback prefix check. A request carrying neither header is rejected.
    """
    allowed = get_settings().allowed_origins
    if origin:
        return origin in allowed
    if referer:
        return any(referer.startswith(f"{a}/") or referer == a for a in allowed)
    return False


# -------------------------------------------------------------------- login throttling


@dataclass
class _Attempts:
    count: int = 0
    blocked_until: float = 0.0


@dataclass
class LoginThrottle:
    """Basic in-process throttle. Sufficient for a single-process prototype; it resets
    on restart and does not coordinate across instances, which is acceptable here."""

    max_attempts: int = 8
    window_seconds: float = 300.0
    block_seconds: float = 120.0
    _state: dict[str, _Attempts] = field(default_factory=dict)
    _window_start: float = field(default_factory=time.monotonic)

    def _maybe_roll_window(self) -> None:
        now = time.monotonic()
        if now - self._window_start > self.window_seconds:
            self._window_start = now
            self._state = {k: v for k, v in self._state.items() if v.blocked_until > now}

    def blocked_for(self, key: str) -> float:
        self._maybe_roll_window()
        entry = self._state.get(key)
        if entry is None:
            return 0.0
        remaining = entry.blocked_until - time.monotonic()
        return max(0.0, remaining)

    def record_failure(self, key: str) -> None:
        self._maybe_roll_window()
        entry = self._state.setdefault(key, _Attempts())
        entry.count += 1
        if entry.count >= self.max_attempts:
            entry.blocked_until = time.monotonic() + self.block_seconds
            entry.count = 0

    def record_success(self, key: str) -> None:
        self._state.pop(key, None)


login_throttle = LoginThrottle()
