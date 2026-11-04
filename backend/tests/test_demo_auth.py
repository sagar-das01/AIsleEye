import os

import pytest
from fastapi.testclient import TestClient

import app.agent.tools as tools
import app.config as config
from app.main import app
from scripts import predemo

pytestmark = pytest.mark.rbac  # these exercise the real authentication dependency, not the test-only admin


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "DATA_DIR", tmp_path)
    (tmp_path / "inventory.json").write_text('{"SKU-1": {"available": 3, "quarantine": 0}}')
    monkeypatch.delenv("DEMO_AUTH_BYPASS", raising=False)
    config.get_settings.cache_clear()


def bypass(monkeypatch, on=True):
    monkeypatch.setenv("DEMO_AUTH_BYPASS", "true" if on else "false")
    config.get_settings.cache_clear()


def local() -> TestClient:
    return TestClient(app, client=("127.0.0.1", 50000))


def test_the_bypass_is_off_by_default_even_for_localhost():
    assert config.Settings(_env_file=None, model_api_key="k").demo_auth_bypass is False
    assert local().get("/inventory").status_code == 401
    assert local().get("/health").json()["auth_bypass"] is False


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_a_direct_local_request_without_a_token_acts_as_the_demo_admin(monkeypatch, host):
    bypass(monkeypatch)
    client = TestClient(app, client=(host, 50000))
    assert client.get("/inventory").json() == {"SKU-1": {"available": 3, "quarantine": 0}}
    assert client.get("/me").json()["subject"] == "demo-admin"
    assert client.get("/health").json()["auth_bypass"] is True


def test_another_machine_still_has_to_authenticate(monkeypatch):
    bypass(monkeypatch)
    remote = TestClient(app, client=("203.0.113.9", 50000))
    assert remote.get("/inventory").status_code == 401
    assert remote.get("/me").status_code == 401


@pytest.mark.parametrize("header", ["X-Forwarded-For", "Forwarded", "X-Real-IP"])
def test_a_proxied_request_is_never_treated_as_local(monkeypatch, header):
    bypass(monkeypatch)
    assert local().get("/inventory", headers={header: "198.51.100.7"}).status_code == 401


def test_a_presented_token_always_goes_through_real_verification(monkeypatch):
    bypass(monkeypatch)
    res = local().get("/inventory", headers={"Authorization": "Bearer not-a-real-token"})
    assert res.status_code in (401, 503)  # 503 here only because no identity provider is configured in the test


def test_turning_it_off_again_closes_the_door(monkeypatch):
    bypass(monkeypatch)
    assert local().get("/inventory").status_code == 200
    bypass(monkeypatch, on=False)
    assert local().get("/inventory").status_code == 401


def test_the_pre_demo_check_passes_with_real_authentication(monkeypatch):
    assert predemo.check_replay_offline()[0] == predemo.OK
    assert "DEMO_AUTH_BYPASS" not in os.environ  # the check restores the environment it borrowed


def test_the_demo_launcher_binds_to_loopback_and_sets_the_switch_itself():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "scripts" / "demo.sh").read_text()
    assert "--host 127.0.0.1" in text and "export DEMO_AUTH_BYPASS=true" in text
    assert "DEMO_AUTH_BYPASS" not in (Path(__file__).resolve().parents[2] / ".env.example").read_text().replace("DEMO_AUTH_BYPASS=false", "")
