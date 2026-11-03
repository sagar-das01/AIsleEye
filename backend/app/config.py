from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"
DATA_DIR = BACKEND_DIR / "data"


class Settings(BaseSettings):
    # Field names start with "model_" to match the env vars, so free pydantic's reserved namespace.
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        extra="ignore",
        protected_namespaces=("settings_",),
    )

    model_api_key: str = Field(min_length=1)
    model_api_base_url: str = "https://api.meta.ai/v1"
    muse_model: str = "muse-spark-1.3"
    sam_model: str = "sam-3.1"
    muse_agent_model: str = "muse-wonder-1.0"
    request_timeout_s: float = 90.0
    cors_origins: list[str] = ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
