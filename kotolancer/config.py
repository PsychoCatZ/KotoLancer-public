from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KOTO_", env_file=ROOT / ".env", extra="ignore")

    environment: str = "local"
    auth_mode: str = "local"
    access_token: str = ""
    database_url: str = "postgresql+psycopg://kotolancer:local-kotolancer@127.0.0.1:5433/kotolancer"
    storage_dir: Path = ROOT / "data" / "files"
    allowed_hosts: list[str] = ["127.0.0.1", "localhost"]
    max_upload_bytes: int = 20 * 1024 * 1024
    extraction_timeout: int = 20
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = ""
    openai_reasoning_effort: str = ""
    openai_total_budget_nusd: int = Field(default=0, ge=0, le=1_000_000_000_000)
    openai_daily_request_limit: int = Field(default=20, ge=1, le=1000)
    openai_max_output_tokens: int = Field(default=4000, ge=500, le=16000)
    openai_timeout_seconds: int = Field(default=120, ge=10, le=300)
    ai_max_context_chars: int = Field(default=100_000, ge=1000, le=500_000)
    ai_max_binary_bytes: int = Field(default=16 * 1024 * 1024, ge=1000, le=40 * 1024 * 1024)
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_bot_username: str = Field(default="KotoLancer_Bot", pattern=r"^[A-Za-z0-9_]{5,32}$")
    telegram_owner_id: int = Field(default=0, ge=0)
    telegram_auth_max_age: int = Field(default=3600, ge=60, le=86400)
    mini_app_url: str = ""
    github_token: SecretStr = SecretStr("")
    github_owner: str = "example-owner"

    @property
    def ai_configured(self):
        return bool(
            self.openai_api_key.get_secret_value().strip()
            and self.openai_model.strip()
            and (self.environment != "production" or self.openai_total_budget_nusd > 0)
        )

    @model_validator(mode="after")
    def validate_auth(self):
        if self.auth_mode not in {"local", "token", "telegram", "service"}:
            raise ValueError("Unknown auth mode")
        if self.auth_mode == "local" and self.environment != "local":
            raise ValueError("Local authentication is forbidden outside the local environment")
        if self.auth_mode == "token" and len(self.access_token) < 32:
            raise ValueError("Token mode requires a token with at least 32 characters")
        if self.auth_mode == "telegram" and (
            not self.telegram_owner_id or not self.telegram_bot_token.get_secret_value()
        ):
            raise ValueError("Telegram mode requires bot token and owner ID")
        if self.mini_app_url:
            from urllib.parse import urlsplit

            parsed = urlsplit(self.mini_app_url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("Mini App URL must be HTTPS without credentials")
        if not self.storage_dir.is_absolute():
            self.storage_dir = (ROOT / self.storage_dir).resolve()
        return self
