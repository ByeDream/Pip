from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str = Field(default="")
    anthropic_base_url: str = Field(default="")

    keys_file_path: str = Field(default=".pip/keys.json")

    profiler_enabled: bool = Field(default=False)
    verbose: bool = Field(default=True)

    search_api_key: str = Field(default="")

    wecom_bot_id: str = Field(default="")
    wecom_bot_secret: str = Field(default="")

    # Memory pipeline settings (global, not per-agent).
    reflect_transcript_threshold: int = Field(default=10)
    transcript_retention_days: int = Field(default=7)
    dream_hour: int = Field(default=2)
    dream_min_observations: int = Field(default=20)
    dream_inactive_minutes: int = Field(default=30)

    # Heartbeat settings.
    heartbeat_interval: int = Field(default=1800)
    heartbeat_active_start: int = Field(default=9)
    heartbeat_active_end: int = Field(default=22)

    def check_required(self) -> None:
        errors: list[str] = []
        if not self.anthropic_api_key:
            keys_path = Path(self.keys_file_path)
            if not keys_path.is_absolute():
                keys_path = Path.cwd() / keys_path
            if not keys_path.is_file():
                errors.append(
                    "No Anthropic credentials found: set ANTHROPIC_API_KEY in .env "
                    f"or populate {self.keys_file_path}"
                )
        if errors:
            raise ConfigError("; ".join(errors))


settings = Settings()
