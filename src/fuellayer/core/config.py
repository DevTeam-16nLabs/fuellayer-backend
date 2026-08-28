from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://fuellayer:fuellayer@localhost:5433/fuellayer"
    clerk_secret_key: str | None = None
    clerk_publishable_key: str | None = None
    clerk_jwt_key: str | None = None
    clerk_webhook_signing_secret: str | None = None
    clerk_authorized_parties: str = "fuellayer://,http://localhost:8081"
    onboarding_preview_rate_limit: int = 10

    @property
    def clerk_authorized_parties_list(self) -> list[str]:
        return [
            value.strip() for value in self.clerk_authorized_parties.split(",") if value.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
