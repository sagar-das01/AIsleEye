import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_shape_is_stable():
    body = client.get("/health").json()
    assert set(body) == {"status", "models", "demo_mode"}
    assert set(body["models"]) == {"vision_agent", "segmentation"}


def test_health_is_json():
    assert client.get("/health").headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("method", ["post", "put", "delete", "patch"])
def test_health_only_allows_get(method):
    assert getattr(client, method)("/health").status_code == 405


def test_unknown_route_is_404_json():
    res = client.get("/definitely-not-here")
    assert res.status_code == 404
    assert res.json() == {"detail": "Not Found"}


def test_openapi_describes_the_service():
    spec = client.get("/openapi.json").json()
    assert spec["info"]["title"] == "AIsleEye"
    assert "get" in spec["paths"]["/health"]


def test_openapi_never_contains_secrets():
    assert "test-key" not in client.get("/openapi.json").text


def test_cors_preflight_allows_any_header_from_frontend():
    res = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-custom",
        },
    )
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_preflight_from_unknown_origin_is_refused():
    res = client.options(
        "/health",
        headers={"Origin": "http://evil.test", "Access-Control-Request-Method": "GET"},
    )
    assert res.status_code == 400
    assert "access-control-allow-origin" not in res.headers


def test_cors_does_not_allow_credentials():
    res = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert res.headers.get("access-control-allow-credentials") != "true"


def test_cors_origin_match_is_exact():
    for origin in ("http://localhost:3000.evil.test", "https://localhost:3000", "http://localhost:30000"):
        res = client.get("/health", headers={"Origin": origin})
        assert "access-control-allow-origin" not in res.headers


def test_no_traceback_leaks_on_internal_errors(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "settings", None)  # health() dereferences settings
    safe = TestClient(app, raise_server_exceptions=False)
    res = safe.get("/health")
    assert res.status_code == 500
    assert "Traceback" not in res.text and "test-key" not in res.text
