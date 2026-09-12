from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: str = "INFO"
    openai_api_key: str | None = None
    openrouter_api_key: str | None = None
    receipt_model: str | None = None
    recipe_import_model: str = "gpt-4.1-mini"
    database_url: str = "postgresql+asyncpg://fuellayer:fuellayer@localhost:5433/fuellayer"
    release_sha: str = "unknown"
    clerk_secret_key: str | None = None
    clerk_publishable_key: str | None = None
    clerk_jwt_key: str | None = None
    clerk_jwt_issuer: str | None = None
    clerk_webhook_signing_secret: str | None = None
    clerk_authorized_parties: str = "fuellayer://,http://localhost:8081"
    onboarding_preview_rate_limit: int = 10
    store_search_rate_limit: int = 20
    google_places_api_key: str | None = None
    cors_origins: str = "http://localhost:8081,http://127.0.0.1:8081"

    @field_validator("database_url", mode="before")
    @classmethod
    def use_async_postgres(cls, value: str) -> str:
        # Managed hosts expose standard PostgreSQL URLs, while this app uses asyncpg.
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+asyncpg://" + value[len(prefix) :]
        return value

    @property
    def receipt_api_key(self) -> str | None:
        return self.openrouter_api_key or self.openai_api_key

    @property
    def receipt_provider(self) -> str | None:
        if self.openrouter_api_key:
            return "OpenRouter"
        return "OpenAI" if self.openai_api_key else None

    @property
    def receipt_endpoint(self) -> str:
        return (
            "https://openrouter.ai/api/v1/responses"
            if self.openrouter_api_key
            else "https://api.openai.com/v1/responses"
        )

    @property
    def receipt_model_id(self) -> str:
        model = self.receipt_model or "gpt-4.1-mini"
        if self.openrouter_api_key:
            return model if "/" in model else f"openai/{model}"
        return model.removeprefix("openai/")

    @property
    def clerk_authorized_parties_list(self) -> list[str]:
        return [
            value.strip() for value in self.clerk_authorized_parties.split(",") if value.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
