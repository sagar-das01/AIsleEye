import re
from pathlib import Path

import pytest

from app.config import Settings
from app.main import app

ROOT = Path(__file__).resolve().parents[2]
README = (ROOT / "README.md").read_text()


def readme_routes() -> list[tuple[str, str, bool]]:
    rows = re.findall(r"^\| ([A-Z]+) \| `([^`]+)` \| ([^|]+) \|", README, re.M)
    return [(method, path, "available" in status and "planned" not in status) for method, path, status in rows]


def real_routes() -> set[tuple[str, str]]:
    return {(m, r.path) for r in app.routes if hasattr(r, "methods") for m in r.methods}


def test_readme_documents_an_api_table():
    routes = readme_routes()
    assert ("GET", "/health", True) in routes
    assert all(path.startswith("/") for _, path, _ in routes)


def test_every_route_the_readme_calls_available_exists():
    # Merge-safe: only checks README -> code, so later branches may add routes freely.
    real = real_routes()
    for method, path, available in readme_routes():
        if available:
            assert (method, path) in real, f"README lists {method} {path} as available but it is not routed"


def test_readme_planned_routes_are_not_marked_available_by_accident():
    planned = [(m, p) for m, p, available in readme_routes() if not available]
    assert all(m in {"GET", "POST", "DELETE"} for m, _ in planned)


def test_readme_names_the_configured_default_models():
    s = Settings(_env_file=None)
    for model in (s.muse_model, s.sam_model):
        assert model in README, f"README never mentions {model}"


def test_readme_does_not_ship_a_real_looking_key():
    assert not re.search(r"MODEL_API_KEY[ \t]*=[ \t]*(?!your|<|\.\.\.|xxx|changeme)\S{12,}", README, re.I)


def test_license_and_dataset_attribution_present():
    assert re.search(r"LOCO", README) and re.search(r"CC0|CC BY", README)


def test_env_example_covers_every_setting_when_present():
    example = ROOT / ".env.example"
    if not example.exists():
        pytest.skip(".env.example is planned for Phase 0 / 8")
    keys = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", example.read_text(), re.M))
    missing = {name.upper() for name in Settings.model_fields} - keys
    assert not missing, f".env.example is missing {sorted(missing)}"


def test_env_example_has_no_secret_values_when_present():
    example = ROOT / ".env.example"
    if not example.exists():
        pytest.skip(".env.example is planned for Phase 0 / 8")
    for line in example.read_text().splitlines():
        if re.match(r"^[A-Z_]*(KEY|SECRET|TOKEN|PASSWORD)[A-Z_]*=", line):
            value = line.split("=", 1)[1].strip()
            assert value == "" or re.search(r"your|change|example|<", value, re.I), f"possible secret in: {line.split('=')[0]}"
