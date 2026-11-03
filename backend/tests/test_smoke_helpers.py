import json
from types import SimpleNamespace

import pytest
from PIL import Image

from scripts import smoke_test

SAM_LINE = (
    "<0f>0<|box;x1=353;y1=382;x2=453;y2=427;w=537;h=561;c=0.96|>"
    "<|mask;x=0;y=0;c=0.96;data=46,101,!ab|>cd|>,"
    "1<|box;x1=260;y1=382;x2=340;y2=427;w=537;h=561;c=0.91|>"
    "<|mask;x=0;y=0;c=0.91;data=46,81,~xyz|>"
)


class FakeResponses:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def fake_client(monkeypatch, response):
    responses = FakeResponses(response)
    monkeypatch.setattr(smoke_test, "get_client", lambda: SimpleNamespace(responses=responses))
    return responses


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke_test, "OUT_DIR", tmp_path)
    return tmp_path


def test_box_regex_parses_boxes_with_confidence():
    boxes = smoke_test.BOX_RE.findall(SAM_LINE)
    assert boxes == [
        "x1=353;y1=382;x2=453;y2=427;w=537;h=561;c=0.96",
        "x1=260;y1=382;x2=340;y2=427;w=537;h=561;c=0.91",
    ]


def test_mask_header_regex_ignores_payload_contents():
    # Payload "ab|>cd" contains a token terminator; only headers must be matched.
    assert smoke_test.MASK_HEADER_RE.findall(SAM_LINE) == [("46", "101", "!"), ("46", "81", "~")]


def test_damage_schema_is_strict_compatible():
    schema = smoke_test.DAMAGE_SCHEMA
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_synthetic_scene_is_valid_image(tmp_path):
    path = tmp_path / "scene.png"
    smoke_test.make_synthetic_scene(path)
    with Image.open(path) as img:
        assert img.size == (960, 640)


def test_check_structured_validates_and_saves(monkeypatch, out_dir):
    payload = {"box_count": 4, "any_damage": True, "damage_types": ["crushed_corner"], "confidence": 0.8, "reason": "x"}
    calls = fake_client(monkeypatch, SimpleNamespace(output_text=json.dumps(payload)))

    smoke_test.check_structured("muse-spark-1.3", "data:image/png;base64,AA==")

    fmt = calls.calls[0]["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True
    assert json.loads((out_dir / "structured_output.json").read_text()) == payload


def test_check_structured_fails_on_missing_keys(monkeypatch, out_dir):
    fake_client(monkeypatch, SimpleNamespace(output_text=json.dumps({"box_count": 1})))
    with pytest.raises(AssertionError, match="missing keys"):
        smoke_test.check_structured("m", "data:")


def test_check_tools_requires_function_call(monkeypatch):
    fake_client(monkeypatch, SimpleNamespace(output=[SimpleNamespace(type="message")], output_text="no tools"))
    with pytest.raises(AssertionError, match="no function_call"):
        smoke_test.check_tools("m", "")


def test_check_tools_reports_call(monkeypatch):
    call = SimpleNamespace(type="function_call", name="adjust_inventory", arguments='{"sku":"SKU-1042"}')
    calls = fake_client(monkeypatch, SimpleNamespace(output=[call], output_text=""))
    assert smoke_test.check_tools("m", "") == 'adjust_inventory({"sku":"SKU-1042"})'
    assert calls.calls[0]["tools"] == [smoke_test.ADJUST_INVENTORY_TOOL]


def test_check_sam_sends_one_concept_per_request(monkeypatch, out_dir):
    calls = fake_client(monkeypatch, SimpleNamespace(output_text=SAM_LINE))

    summary = smoke_test.check_sam("sam-3.1", "data:image/png;base64,AA==")

    prompts = [c["input"][0]["content"][0]["text"] for c in calls.calls]
    assert prompts == smoke_test.SAM_PROMPTS
    assert all(c["metadata"] == {"mask_encoding": "one_bit", "include_confidence": "true"} for c in calls.calls)
    assert "2 boxes / 2 masks" in summary
    assert (out_dir / "sam_raw_cardboard_box.txt").read_text() == SAM_LINE


def test_check_sam_handles_zero_matches(monkeypatch, out_dir):
    fake_client(monkeypatch, SimpleNamespace(output_text=""))
    assert "0 boxes / 0 masks" in smoke_test.check_sam("sam-3.1", "")
