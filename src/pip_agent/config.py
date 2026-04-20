"""Pip-Boy host-level configuration.

All settings are host concerns only. Tool credentials, model routing, and
permission settings are handled by Claude Code itself via `.claude/settings.json`
and env vars — Pip-Boy does not proxy them.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


WORKDIR: Path = Path.cwd()
"""Absolute path of the workspace Pip-Boy is running in.

Captured once at import. All per-agent subdirectories live under ``WORKDIR/.pip/``.
"""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ``ANTHROPIC_API_KEY`` is the direct Anthropic credential;
    # ``ANTHROPIC_AUTH_TOKEN`` is the proxy-style token Claude Code itself
    # honours. Either works for reflect's direct LLM calls — we try them in
    # order and fall back to ``os.environ`` for users who set them outside
    # ``.env``.
    anthropic_api_key: str = Field(default="")
    anthropic_auth_token: str = Field(default="")
    anthropic_base_url: str = Field(default="")

    verbose: bool = Field(default=True)

    wecom_bot_id: str = Field(default="")
    wecom_bot_secret: str = Field(default="")

    # Memory pipeline (reflect / consolidate / dream) timing knobs.
    reflect_transcript_threshold: int = Field(default=10)
    transcript_retention_days: int = Field(default=7)
    dream_hour: int = Field(default=2)
    dream_min_observations: int = Field(default=20)
    dream_inactive_minutes: int = Field(default=30)

    # Heartbeat injection timing.
    heartbeat_interval: int = Field(default=1800)
    heartbeat_active_start: int = Field(default=9)
    heartbeat_active_end: int = Field(default=22)

    def check_required(self) -> None:
        """Host-level credential check.

        Pip-Boy passes ``ANTHROPIC_API_KEY`` (or ``ANTHROPIC_AUTH_TOKEN`` under a
        proxy) to the Claude Code CLI subprocess when set. If nothing is set,
        CC falls back to its own auth (``claude login`` / system config), which
        is fine — we only surface a warning, never fail.
        """
        return None


settings = Settings()
