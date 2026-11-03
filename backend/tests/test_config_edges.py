import warnings

import pytest
from pydantic import ValidationError

from app import config
from app.config import BACKEND_DIR, DATA_DIR, ROOT_DIR, Settings


def clean_env(monkeypatch):
    for var in ("MODEL_API_BASE_URL", "MUSE_MODEL", "SAM_MODEL", "MUSE_AGENT_MODEL", "REQUEST_TIMEOUT_S", "CORS_ORIGINS"):
        monkeypatch.delenv(var, raising=False)


def test_paths_are_anchored_to_the_repo():
    assert (ROOT_DIR / "backend").is_dir()
    assert BACKEND_DIR == ROOT_DIR / "backend"
    assert DATA_DIR == BACKEND_DIR / "data"
    assert Settings.model_config["env_file"] == ROOT_DIR / ".env"


def test_remaining_defaults(monkeypatch):
    clean_env(monkeypatch)
    s = Settings(_env_file=None)
    assert s.muse_agent_model == "muse-wonder-1.0"
    assert s.request_timeout_s == 90.0


def test_env_file_is_read(monkeypatch, tmp_path):
    clean_env(monkeypatch)
    monkeypatch.delenv("MODEL_API_KEY")
    env = tmp_path / ".env"
    env.write_text("MODEL_API_KEY=from-file\nSAM_MODEL=sam-file\n")
    s = Settings(_env_file=env)
    assert (s.model_api_key, s.sam_model) == ("from-file", "sam-file")


def test_real_env_beats_env_file(monkeypatch, tmp_path):
    clean_env(monkeypatch)
    env = tmp_path / ".env"
    env.write_text("MODEL_API_KEY=from-file\n")
    assert Settings(_env_file=env).model_api_key == "test-key"


def test_unknown_env_file_keys_are_ignored(tmp_path):
    env = tmp_path / ".env"
    env.write_text("SOMETHING_ELSE=1\nLANGFUSE_PUBLIC_KEY=pk\n")
    Settings(_env_file=env)


def test_missing_env_file_is_not_an_error(tmp_path):
    assert Settings(_env_file=tmp_path / "absent.env").model_api_key == "test-key"


@pytest.mark.parametrize("value", ["not-json", "http://a.test", '"http://a.test"', "[1, 2]"])
def test_bad_cors_origins_are_rejected(monkeypatch, value):
    monkeypatch.setenv("CORS_ORIGINS", value)
    with pytest.raises(Exception) as exc:  # pydantic-settings raises SettingsError or ValidationError
        Settings(_env_file=None)
    assert "test-key" not in str(exc.value)


def test_empty_cors_list_is_allowed(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "[]")
    assert Settings(_env_file=None).cors_origins == []


@pytest.mark.parametrize(("raw", "expected"), [("5", 5.0), ("0.5", 0.5), ("120.25", 120.25)])
def test_timeout_parses_numbers(monkeypatch, raw, expected):
    monkeypatch.setenv("REQUEST_TIMEOUT_S", raw)
    assert Settings(_env_file=None).request_timeout_s == expected


def test_timeout_rejects_non_numbers(monkeypatch):
    monkeypatch.setenv("REQUEST_TIMEOUT_S", "soon")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_validation_error_does_not_echo_a_secret(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "")
    monkeypatch.setenv("REQUEST_TIMEOUT_S", "soon")
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)
    assert "sk-" not in str(exc.value)


def test_no_protected_namespace_warnings(monkeypatch):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        Settings(_env_file=None)


def test_env_var_names_are_case_insensitive(monkeypatch):
    monkeypatch.delenv("MUSE_MODEL", raising=False)
    monkeypatch.setenv("muse_model", "muse-lower")
    assert Settings(_env_file=None).muse_model == "muse-lower"


@pytest.mark.xfail(
    strict=True,
    reason="GAP: a whitespace-only MODEL_API_KEY passes min_length=1 and only fails later as a 401 from the API",
)
def test_whitespace_only_key_is_rejected(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "   ")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_get_settings_cache_can_be_cleared(monkeypatch):
    first = config.get_settings()
    config.get_settings.cache_clear()
    monkeypatch.setenv("MUSE_MODEL", "muse-changed")
    try:
        assert config.get_settings() is not first
        assert config.get_settings().muse_model == "muse-changed"
    finally:
        monkeypatch.undo()
        config.get_settings.cache_clear()
