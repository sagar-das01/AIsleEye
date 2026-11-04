import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings


def test_defaults_when_only_key_is_set(monkeypatch):
    for var in ("MODEL_API_BASE_URL", "MUSE_MODEL", "SAM_MODEL", "MUSE_AGENT_MODEL", "CORS_ORIGINS"):
        monkeypatch.delenv(var, raising=False)
    s = Settings(_env_file=None)
    assert s.model_api_key == "test-key"
    assert s.model_api_base_url == "https://api.meta.ai/v1"
    assert s.muse_model == "muse-spark-1.3"
    assert s.sam_model == "sam-3.1"
    assert s.cors_origins == ["http://localhost:3001", "http://127.0.0.1:3001"]


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("MUSE_MODEL", "muse-spark-1.2")
    monkeypatch.setenv("CORS_ORIGINS", '["http://a.test","http://b.test"]')
    s = Settings(_env_file=None)
    assert s.muse_model == "muse-spark-1.2"
    assert s.cors_origins == ["http://a.test", "http://b.test"]


@pytest.mark.parametrize("value", [None, ""])
def test_missing_or_blank_key_is_rejected(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("MODEL_API_KEY")
    else:
        monkeypatch.setenv("MODEL_API_KEY", value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_get_settings_is_cached():
    assert get_settings() is get_settings()
