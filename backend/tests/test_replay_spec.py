"""Spec for DEMO_MODE=replay and the pinned demo store (Phase 4): the demo must run with the network off.

Planned interface:
    settings.demo_mode: "" | "replay"
    app.replay.load_pinned(scan_id) -> ScanResult | None          reads DEMO_DIR first
    POST /scan in replay mode: pinned result first, then the regular cache; never calls a model
    ScanResult.replay: bool                                       lets the UI show the "Showing replay results" banner
"""

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from spec_support import need

replay = need("app.replay", "load_pinned", "DEMO_DIR")
main = need("app.main", "app")
scan_mod = need("app.pipeline.scan", "CACHE_DIR", "scan_id_for")

from app.schemas import ImageSize, ScanResult  # noqa: E402

client = TestClient(main.app)


def photo() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 200, 10)).save(buf, format="PNG")
    return buf.getvalue()


def pin(directory, data: bytes) -> ScanResult:
    directory.mkdir(parents=True, exist_ok=True)
    result = ScanResult(scan_id=scan_mod.scan_id_for(data), image_size=ImageSize(width=40, height=30), objects=[], findings=[], timings_ms={})
    (directory / f"{result.scan_id}.json").write_text(result.model_dump_json())
    return result


@pytest.fixture
def offline(tmp_path, monkeypatch):
    """Replay mode with a network that explodes if touched."""
    import app.config as config

    monkeypatch.setenv("DEMO_MODE", "replay")
    config.get_settings.cache_clear()
    monkeypatch.setattr(main, "settings", config.get_settings(), raising=False)
    monkeypatch.setattr(scan_mod, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(replay, "DEMO_DIR", tmp_path / "demo")

    class NoNetwork:
        def __getattr__(self, name):
            raise AssertionError("network used in replay mode")

    for mod in ("app.pipeline.segment", "app.pipeline.inspect"):
        monkeypatch.setattr(f"{mod}.get_async_client", lambda: NoNetwork())
    yield tmp_path
    monkeypatch.undo()
    config.get_settings.cache_clear()


def test_pinned_result_is_served_without_any_model_call(offline):
    data = photo()
    pinned = pin(offline / "demo", data)
    res = client.post("/scan", files={"image": ("p.png", data, "image/png")})
    assert res.status_code == 200 and res.json()["scan_id"] == pinned.scan_id


def test_replay_results_are_marked_so_the_ui_can_show_the_banner(offline):
    data = photo()
    pin(offline / "demo", data)
    assert client.post("/scan", files={"image": ("p.png", data, "image/png")}).json().get("replay") is True


def test_regular_cache_is_the_second_choice(offline):
    data = photo()
    pin(offline / "cache", data)
    assert client.post("/scan", files={"image": ("p.png", data, "image/png")}).status_code == 200


def test_pinned_store_wins_over_the_regular_cache(offline):
    data = photo()
    pinned = pin(offline / "demo", data)
    stale = json.loads(pinned.model_dump_json())
    stale["timings_ms"] = {"segment": 999999}
    (offline / "cache").mkdir()
    (offline / "cache" / f"{pinned.scan_id}.json").write_text(json.dumps(stale))
    assert client.post("/scan", files={"image": ("p.png", data, "image/png")}).json()["timings_ms"] == {}


def test_unknown_image_in_replay_mode_fails_visibly_instead_of_pretending(offline):
    res = client.post("/scan", files={"image": ("p.png", photo(), "image/png")})
    assert res.status_code in (404, 502, 503)


def test_replay_still_validates_uploads(offline):
    assert client.post("/scan", files={"image": ("p.gif", b"GIF89a", "image/gif")}).status_code == 415


def test_get_scan_reads_the_pinned_store_in_replay_mode(offline):
    pinned = pin(offline / "demo", photo())
    assert client.get(f"/scan/{pinned.scan_id}").status_code == 200


def test_load_pinned_ignores_corrupt_files(offline):
    (offline / "demo").mkdir()
    (offline / "demo" / ("a" * 16 + ".json")).write_text("{broken")
    assert replay.load_pinned("a" * 16) is None


def test_load_pinned_rejects_path_tricks(offline):
    for bad in ("../cache/x", "..", "a/b", "", "A" * 16):
        assert replay.load_pinned(bad) is None


def test_committed_demo_results_validate_against_the_schema():
    from pathlib import Path

    demo = Path(replay.DEMO_DIR)
    files = sorted(demo.glob("*.json")) if demo.exists() else []
    if not files:
        pytest.skip("no pinned demo results yet (Phase 4)")
    for f in files:
        ScanResult.model_validate_json(f.read_text())
        assert f.stem == ScanResult.model_validate_json(f.read_text()).scan_id
