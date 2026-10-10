from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    sim_mode: str = "mock"

    anthropic_api_key: str = ""
    blue_model_name: str = "claude-opus-4-8"

    red_model_base_url: str = "http://localhost:8001/v1"
    red_model_api_key: str = ""
    red_model_name: str = "red-team-local"

    database_url: str = "sqlite+aiosqlite:///./sentinel.db"

    jwt_secret: str = "dev-only-change-me-32byte-minimum-secret-key-0001"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 24 * 7

    cors_origins: str = "http://localhost:3000"

    @property
    def is_live(self) -> bool:
        return self.sim_mode.lower() == "live"

    @property
    def blue_ready(self) -> bool:
        return bool(self.anthropic_api_key) and not self.anthropic_api_key.endswith("REPLACE_ME")

    @property
    def red_ready(self) -> bool:
        return bool(self.red_model_api_key) and not self.red_model_api_key.endswith("REPLACE_ME")

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
