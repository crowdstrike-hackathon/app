from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    agent_base_url: str = "http://localhost:11434/v1"
    agent_model: str = "llama3.2:3b"
    agent_api_key: str = "ollama"
    agent_max_steps: int = 10
    agent_timeout_seconds: float = 90.0

    tickets_path: str = "tickets.json"
    tickets_fallback_path: str = "tickets.sample.json"

    database_url: str = "sqlite+aiosqlite:///./riposte.db"

    jwt_secret: str = "dev-only-change-me-32byte-minimum-secret-key-0001"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 24 * 7

    cors_origins: str = "http://localhost:3000"

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def resolve(self, path: str) -> Path:
        p = Path(path)
        return p if p.is_absolute() else ROOT / p


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
