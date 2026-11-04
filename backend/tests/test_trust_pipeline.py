import base64
import io
import json

import numpy as np
import pytest
from fakes import concept_of, install_fake_model, sam_line, sam_record
from fastapi.testclient import TestClient
from PIL import Image

import app.config as config
import app.feedback as feedback
from app import main, replay, retention
from app.pipeline import scan as scan_mod
from app.schemas import BBox, Finding, ImageSize, ScanResult

W, H = 100, 80
client = TestClient(main.app)
BOX = (20, 30, 49, 59)


@pytest.fixture(autouse=True)
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_mod, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(feedback, "FEEDBACK_PATH", tmp_path / "feedback.jsonl")
    monkeypatch.setattr(replay, "DEMO_DIR", tmp_path / "demo")
    monkeypatch.setattr(retention, "DEMO_DIR", tmp_path / "demo")
    monkeypatch.setenv("DEMO_MODE", "")
    config.get_settings.cache_clear()
    return tmp_path


def noisy_png() -> bytes:
    rng = np.random.default_rng(1)
    buf = io.BytesIO()
    Image.fromarray(rng.integers(0, 256, (H, W, 3), dtype=np.uint8)).save(buf, format="PNG")
    return buf.getvalue()


def handler(*, box_conf=0.95, people=None, assessment=None, person_error=None):
    assessment = assessment or {"index": 0, "damaged": True, "damage_type": "dent", "severity": "low", "confidence": 0.9, "reason": "Dent"}

    def run(kw):
        if kw["model"] == "sam-3.1":
            concept = concept_of(kw)
            if concept == "person":
                return person_error or sam_line(*([sam_record(0, *people, w=W, h=H)] if people else []))
            if concept == "cardboard box":
                return sam_line(sam_record(0, *BOX, w=W, h=H, c=box_conf))
            return sam_line()
        return json.dumps({"assessments": [assessment] if assessment != "none" else []})

    return run


def inspected_crop(fake) -> np.ndarray:
    call = next(c for c in fake.calls if c["model"] != "sam-3.1")
    url = next(p["image_url"] for p in call["input"][0]["content"] if p["type"] == "input_image")
    return np.asarray(Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))))


def scan(data: bytes) -> dict:
    res = client.post("/scan", files={"image": ("p.png", data, "image/png")})
    assert res.status_code == 200, res.text
    return res.json()


def test_a_person_in_front_of_a_box_is_blurred_in_the_crop_sent_for_inspection(monkeypatch):
    clear = install_fake_model(monkeypatch, handler())
    scan(noisy_png())
    plain = inspected_crop(clear)
    scan_mod.CACHE_DIR.joinpath(next(p.name for p in scan_mod.CACHE_DIR.iterdir())).unlink()
    blurred = install_fake_model(monkeypatch, handler(people=BOX))
    body = scan(noisy_png())
    assert body["privacy"] == "blurred"
    assert inspected_crop(blurred).std() < plain.std() * 0.6


def test_a_failed_people_pass_flags_the_scan_and_still_returns_findings(monkeypatch):
    install_fake_model(monkeypatch, handler(person_error=RuntimeError("down")))
    body = scan(noisy_png())
    assert body["privacy"] == "failed" and [f["type"] for f in body["findings"]] == ["box_damage"]


def test_privacy_blur_can_be_switched_off(monkeypatch):
    fake = install_fake_model(monkeypatch, handler())
    monkeypatch.setenv("PRIVACY_BLUR", "false")
    config.get_settings.cache_clear()
    body = scan(noisy_png())
    assert body["privacy"] == "off" and all(concept_of(c) != "person" for c in fake.calls if c["model"] == "sam-3.1")


def test_a_failed_main_pass_still_surfaces_as_an_error(monkeypatch):
    def run(kw):
        if kw["model"] == "sam-3.1" and concept_of(kw) == "person":
            return sam_line()
        return RuntimeError("boom")

    install_fake_model(monkeypatch, run)
    with pytest.raises(RuntimeError):
        client.post("/scan", files={"image": ("p.png", noisy_png(), "image/png")})


def test_low_confidence_findings_need_review(monkeypatch):
    install_fake_model(monkeypatch, handler(assessment={"index": 0, "damaged": True, "damage_type": "tear", "severity": "low", "confidence": 0.4, "reason": "Maybe"}))
    [finding] = scan(noisy_png())["findings"]
    assert finding["needs_review"] and "0.40" in finding["review_reason"]


def test_a_damage_finding_on_a_box_the_segmenter_doubts_needs_review(monkeypatch):
    install_fake_model(monkeypatch, handler(box_conf=0.45))
    [finding] = scan(noisy_png())["findings"]
    assert finding["needs_review"] and "Segmentation" in finding["review_reason"]


def test_confident_findings_are_not_flagged(monkeypatch):
    install_fake_model(monkeypatch, handler())
    [finding] = scan(noisy_png())["findings"]
    assert finding["needs_review"] is False and finding["review_reason"] is None


def test_boxes_the_inspection_returned_nothing_for_are_listed(monkeypatch):
    install_fake_model(monkeypatch, handler(assessment="none"))
    body = scan(noisy_png())
    assert body["findings"] == [] and body["uninspected_boxes"] == ["box_0"]


# ---- retention API -------------------------------------------------------------------------------------------

def seed(world, scan_id="0123456789abcdef") -> None:
    result = ScanResult(scan_id=scan_id, image_size=ImageSize(width=1, height=1), objects=[], findings=[], timings_ms={})
    scan_mod.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (scan_mod.CACHE_DIR / f"{scan_id}.json").write_text(result.model_dump_json())


def test_delete_removes_the_scan_and_a_second_delete_is_404(world):
    seed(world)
    assert client.delete("/scan/0123456789abcdef").status_code == 204
    assert client.get("/scan/0123456789abcdef").status_code == 404
    assert client.delete("/scan/0123456789abcdef").status_code == 404


def test_delete_cannot_reach_a_pinned_demo_result(world):
    (world / "demo").mkdir()
    (world / "demo" / "0123456789abcdef.json").write_text(
        ScanResult(scan_id="0123456789abcdef", image_size=ImageSize(width=1, height=1), objects=[], findings=[], timings_ms={}).model_dump_json()
    )
    assert client.delete("/scan/0123456789abcdef").status_code == 404
    assert client.get("/scan/0123456789abcdef").json()["replay"] is True


def test_startup_purge_uses_the_configured_ttl(world, monkeypatch):
    import os
    import time

    seed(world, "a" * 16)
    seed(world, "b" * 16)
    old = time.time() - 9 * 86400
    os.utime(scan_mod.CACHE_DIR / ("a" * 16 + ".json"), (old, old))
    with TestClient(main.app):
        pass
    assert [p.stem for p in scan_mod.CACHE_DIR.iterdir()] == ["b" * 16]


def test_startup_purge_leaves_the_pinned_store_alone(world):
    demo = world / "demo"
    demo.mkdir()
    pinned = demo / ("c" * 16 + ".json")
    pinned.write_text("{}")
    import os

    os.utime(pinned, (0, 0))
    removed = retention.purge_expired(demo, 7)
    assert removed == [] and pinned.exists()


# ---- feedback API --------------------------------------------------------------------------------------------

def test_feedback_is_stored_for_a_real_finding(world):
    sid = "0123456789abcdef"
    result = ScanResult(
        scan_id=sid, image_size=ImageSize(width=10, height=10), objects=[], timings_ms={},
        findings=[Finding(id="dmg_box_0", type="box_damage", severity="low", confidence=0.9, bbox=BBox(x1=0, y1=0, x2=5, y2=5), description="d")],
    )
    scan_mod.CACHE_DIR.mkdir(parents=True)
    (scan_mod.CACHE_DIR / f"{sid}.json").write_text(result.model_dump_json())
    ok = client.post(f"/scan/{sid}/feedback", json={"finding_id": "dmg_box_0", "action": "reject", "reason": "shadow"})
    assert ok.status_code == 200 and ok.json()["action"] == "reject"
    assert json.loads((world / "feedback.jsonl").read_text())["finding_id"] == "dmg_box_0"
    assert client.post(f"/scan/{sid}/feedback", json={"finding_id": "nope", "action": "reject", "reason": ""}).status_code == 422
    assert client.post(f"/scan/{sid}/feedback", json={"finding_id": "dmg_box_0", "action": "correct", "reason": ""}).status_code == 422
    assert len((world / "feedback.jsonl").read_text().splitlines()) == 1
