from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_reports_models():
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["models"] == {"vision_agent": "muse-spark-1.3", "segmentation": "sam-3.1"}


def test_health_does_not_leak_api_key():
    assert "test-key" not in client.get("/health").text


def test_cors_allows_frontend_origin():
    res = client.options(
        "/health",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_cors_rejects_unknown_origin():
    res = client.get("/health", headers={"Origin": "http://evil.test"})
    assert "access-control-allow-origin" not in res.headers
