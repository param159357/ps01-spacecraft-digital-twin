"""Authentication request/response contracts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .common import Role


class LoginRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SessionUser(BaseModel):
    """What the browser is told about itself. The server remains the authority."""

    model_config = ConfigDict(frozen=True)

    username: str
    role: Role
    home_route: str
    visible_groups: tuple[str, ...]
    can_execute_commands: bool
    can_control_simulator: bool


class LoginResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    user: SessionUser
    csrf_token: str
