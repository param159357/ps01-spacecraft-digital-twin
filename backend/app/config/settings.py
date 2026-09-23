"""Environment-backed application settings.

Secrets live here and nowhere near the frontend bundle. Names match .env.example.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Core ---
    database_url: str = Field(
        default="postgresql+psycopg://ps01:ps01_local_dev@localhost:5432/ps01",
        alias="DATABASE_URL",
    )
    app_origin: str = Field(default="http://localhost:3000", alias="APP_ORIGIN")
    session_secret: str = Field(default="dev-only-insecure-secret", alias="SESSION_SECRET")
    cookie_secure: bool = Field(default=False, alias="COOKIE_SECURE")
    session_ttl_hours: int = Field(default=12, alias="SESSION_TTL_HOURS")
    db_connect_timeout_seconds: int = Field(default=5, alias="DB_CONNECT_TIMEOUT_SECONDS")

    # --- Simulation cadence (used from Phase 2) ---
    sim_tick_seconds: float = Field(default=1.0, alias="SIM_TICK_SECONDS")
    forecast_interval_seconds: float = Field(default=5.0, alias="FORECAST_INTERVAL_SECONDS")

    # --- AI investigator (used from Phase 7; disabled by default) ---
    ai_enabled: bool = Field(default=False, alias="AI_ENABLED")
    llm_provider: str = Field(default="", alias="LLM_PROVIDER")
    llm_model: str = Field(default="", alias="LLM_MODEL")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    llm_timeout_seconds: int = Field(default=20, alias="LLM_TIMEOUT_SECONDS")
    llm_daily_call_limit: int = Field(default=30, alias="LLM_DAILY_CALL_LIMIT")
    llm_max_output_tokens: int = Field(default=1200, alias="LLM_MAX_OUTPUT_TOKENS")
    ai_incident_cooldown_seconds: int = Field(default=120, alias="AI_INCIDENT_COOLDOWN_SECONDS")
    ai_pending_queue_limit: int = Field(default=10, alias="AI_PENDING_QUEUE_LIMIT")
    ai_max_tool_calls: int = Field(default=4, alias="AI_MAX_TOOL_CALLS")
    ai_max_provider_attempts: int = Field(default=3, alias="AI_MAX_PROVIDER_ATTEMPTS")

    # --- Optional ML (Phase 10) ---
    anomaly_model_path: str = Field(default="", alias="ANOMALY_MODEL_PATH")

    @field_validator("app_origin")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def allowed_origins(self) -> list[str]:
        """Origins accepted for CSRF / CORS checks."""
        origins = {self.app_origin}
        if not self.cookie_secure:
            # Local development convenience only; never enabled with secure cookies.
            origins.update({"http://localhost:3000", "http://127.0.0.1:3000"})
        return sorted(o for o in origins if o)

    def is_production_like(self) -> bool:
        return self.cookie_secure


@lru_cache
def get_settings() -> Settings:
    return Settings()
