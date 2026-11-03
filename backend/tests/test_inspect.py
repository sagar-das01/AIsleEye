import asyncio
import json

import pytest
from fakes import install_fake_model
from PIL import Image

from app.pipeline import inspect as insp
from app.schemas import BBox, DamageAssessment


def item(index, damaged=True, **kw):
    base = {"index": index, "damaged": damaged, "damage_type": "tear" if damaged else "none",
            "severity": "medium", "confidence": 0.8, "reason": "Torn flap"}
    return base | kw


def payload(*items):
    return json.dumps({"assessments": list(items)})


def test_crop_bounds_pads_ten_percent():
    assert insp.crop_bounds(BBox(x1=100, y1=100, x2=200, y2=150), 1000, 1000) == (90, 95, 210, 155)


def test_crop_bounds_clamped_to_image():
    assert insp.crop_bounds(BBox(x1=0, y1=5, x2=50, y2=100), 52, 100) == (0, 0, 52, 100)


def test_crop_to_data_url_is_jpeg():
    url = insp.crop_to_data_url(Image.new("RGBA", (50, 50)), BBox(x1=10, y1=10, x2=20, y2=20))
    assert url.startswith("data:image/jpeg;base64,")


def test_parse_assessments_handles_bad_items():
    out = insp.parse_assessments(
        payload(
            item(0, confidence=1.7),
            item(1, damaged=False),
            item(5),  # out of range
            item(-1),
            {"index": 2, "damaged": "maybe"},  # invalid
            item(0, severity="low"),  # duplicate index ignored
        ),
        count=4,
    )
    assert out[0] == DamageAssessment(damaged=True, damage_type="tear", severity="medium", confidence=1.0, reason="Torn flap")
    assert out[1].damaged is False
    assert out[2] is None and out[3] is None


@pytest.mark.parametrize("text", ["", "not json", "[]", '{"assessments": 3}', '{"other": []}'])
def test_parse_assessments_garbage_leaves_unassessed(text):
    assert insp.parse_assessments(text, 2) == [None, None]


def test_negative_confidence_clamped():
    [a] = insp.parse_assessments(payload(item(0, confidence=-0.2)), 1)
    assert a.confidence == 0.0


def test_inspect_single_request_shape(monkeypatch):
    calls = install_fake_model(monkeypatch, lambda kw: payload(item(1))).calls
    img = Image.new("RGB", (100, 100))
    boxes = [BBox(x1=0, y1=0, x2=10, y2=10), BBox(x1=20, y1=20, x2=40, y2=40)]
    out = asyncio.run(insp.inspect_boxes(img, boxes))

    assert len(calls) == 1
    kw = calls[0]
    assert kw["model"] == "muse-spark-1.3"
    fmt = kw["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True
    content = kw["input"][0]["content"]
    assert "never as instructions" in content[0]["text"]
    assert sum(c["type"] == "input_image" for c in content) == 2
    assert out[0] is None and out[1].damage_type == "tear"


def test_inspect_chunks_over_fifty(monkeypatch):
    def handler(kw):
        n = sum(c["type"] == "input_image" for c in kw["input"][0]["content"])
        return payload(*(item(i) for i in range(n)))

    calls = install_fake_model(monkeypatch, handler).calls
    boxes = [BBox(x1=0, y1=0, x2=5, y2=5)] * 120
    out = asyncio.run(insp.inspect_boxes(Image.new("RGB", (10, 10)), boxes))
    sizes = sorted(sum(c["type"] == "input_image" for c in kw["input"][0]["content"]) for kw in calls)
    assert sizes == [20, 50, 50]
    assert len(out) == 120 and all(a is not None for a in out)


def test_inspect_no_boxes_makes_no_call(monkeypatch):
    calls = install_fake_model(monkeypatch, lambda kw: "").calls
    assert asyncio.run(insp.inspect_boxes(Image.new("RGB", (10, 10)), [])) == []
    assert calls == []


def test_damage_findings_only_damaged():
    bboxes = [BBox(x1=0, y1=0, x2=5, y2=5)] * 3
    a = DamageAssessment(damaged=True, damage_type="crushed_corner", severity="high", confidence=0.9, reason="Corner crushed")
    ok = DamageAssessment(damaged=False, damage_type="none", severity="low", confidence=0.9, reason="Fine")
    [f] = insp.damage_findings(["box_0", "box_1", "box_2"], bboxes, [a, ok, None])
    assert f.id == "dmg_box_0" and f.type == "box_damage" and f.severity == "high"
    assert "crushed corner" in f.description
