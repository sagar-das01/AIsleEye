from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"
DATA_DIR = BACKEND_DIR / "data"

ReasoningEffort = Literal["", "minimal", "low", "medium", "high", "xhigh", "max"]


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
    # Which backend/prompts/inspect_<name>.txt the inspection uses; the name is logged with every score.
    inspect_prompt: str = Field("v1", pattern=r"^[a-z0-9_]+$")
    # Measured in Phase 0: "minimal" answered an image prompt in ~5 s vs ~16 s with the model default.
    muse_reasoning_effort: ReasoningEffort = "minimal"
    # SAM matches short noun phrases literally; "floor tape" found nothing on the test scene, a colour did.
    walkway_concept: str = "yellow line"
    walkway_min_confidence: float = Field(0.6, ge=0.0, le=1.0)
    request_timeout_s: float = 90.0
    # Whole-scan deadline: each model call retries twice, so without it one slow scan could outlast the demo.
    scan_timeout_s: float = Field(120.0, gt=0)
    # The frontend runs on 3001 because local Langfuse uses 3000.
    cors_origins: list[str] = ["http://localhost:3001", "http://127.0.0.1:3001"]

    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_jwks_url: str = ""
    rbac_users_file: Path = DATA_DIR / "access.json"
    rbac_warehouse_id: str = Field("default", min_length=1, max_length=100)

    agent_runtime: Literal["langchain", "plain", "rules"] = "langchain"
    demo_mode: Literal["", "replay"] = ""
    privacy_blur: bool = True
    review_confidence: float = Field(0.6, ge=0.0, le=1.0)
    cache_ttl_days: int = Field(7, ge=1)
    guard_max_tool_calls: int = Field(8, ge=1)

    langfuse_enabled: bool = False
    langfuse_public_key: str = ""
    langfuse_secret_key: SecretStr = SecretStr("")
    langfuse_base_url: str = "http://localhost:3000"
    score_min_reviews: int = Field(5, ge=1)
    score_weights: dict[str, float] = {"validity": 0.4, "guard_clean": 0.4, "within_cap": 0.2}
    mlflow_tracking_uri: str = "sqlite:///data/mlflow/mlflow.db"

    @field_validator("rbac_users_file")
    @classmethod
    def _anchor_access_file(cls, value: Path) -> Path:
        return value if value.is_absolute() else ROOT_DIR / value

    @model_validator(mode="after")
    def _standard_mode_models_only(self) -> "Settings":
        # Contributor-mode models let Meta train on the prompts, which here include warehouse photos.
        for name in ("muse_model", "sam_model", "muse_agent_model"):
            if getattr(self, name).endswith("-contributor"):
                raise ValueError(f"{name.upper()} must be a Standard mode model: photos must not be used for training")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
