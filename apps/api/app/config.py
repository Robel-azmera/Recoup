from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every integration falls back to a mock when its key is empty."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: Literal["local", "test", "prod"] = "local"
    web_origin: str = "http://localhost:5173"
    public_api_url: str = "http://localhost:8080"

    firestore_project_id: str = "demo-recoup"
    firestore_emulator_host: str = ""
    firebase_auth_emulator_host: str = ""
    # "firestore" (emulator or real) or "memory" (tests / no emulator)
    store_backend: Literal["firestore", "memory"] = "firestore"

    admin_password: str = "change-me"
    session_secret: str = "dev-session-secret-change-me"
    approval_secret: str = "dev-approval-secret-change-me"

    job_shared_secret: str = "dev-job-secret"
    scheduler_audience: str = ""
    scheduler_sa_email: str = ""

    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    # Tried in order when a model's daily free-tier quota runs out (each model has its own quota)
    gemini_fallback_models: str = "gemini-3.5-flash-lite,gemini-3.1-flash-lite"
    gemini_rpm: int = 8

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_webhook_secret: str = "dev-telegram-secret"
    # auto: long-polling when running locally with a real token, webhook in prod
    telegram_mode: Literal["auto", "webhook", "polling", "off"] = "auto"

    google_calendar_id: str = ""
    google_service_account_json: str = ""

    resend_api_key: str = ""
    email_from: str = "Recoup <onboarding@resend.dev>"
    owner_email: str = ""

    @property
    def integrations(self) -> dict[str, bool]:
        """True = real integration configured, False = mock."""
        return {
            "stripe": bool(self.stripe_secret_key),
            "gemini": bool(self.gemini_api_key),
            "telegram": bool(self.telegram_bot_token and self.telegram_chat_id),
            "calendar": bool(self.google_calendar_id and self.google_service_account_json),
            "email": bool(self.resend_api_key),
            "firestore_emulator": bool(self.firestore_emulator_host),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
