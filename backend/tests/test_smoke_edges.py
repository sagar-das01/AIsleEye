import json
from types import SimpleNamespace

import pytest

from scripts import smoke_test


class FakeResponses:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def fake_client(monkeypatch, response):
    responses = FakeResponses(response)
    monkeypatch.setattr(smoke_test, "get_client", lambda: SimpleNamespace(responses=responses))
    return responses


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke_test, "OUT_DIR", tmp_path)
    return tmp_path


def test_check_text_sends_model_and_returns_trimmed_output(monkeypatch):
    responses = fake_client(monkeypatch, SimpleNamespace(output_text="  OK  "))
    assert smoke_test.check_text("muse-x", "") == "OK"
    assert responses.calls[0]["model"] == "muse-x"


@pytest.mark.parametrize("text", ["", "   \n"])
def test_check_text_fails_on_empty_output(monkeypatch, text):
    fake_client(monkeypatch, SimpleNamespace(output_text=text))
    with pytest.raises(AssertionError, match="empty output"):
        smoke_test.check_text("m", "")


def test_check_text_truncates_long_output(monkeypatch):
    fake_client(monkeypatch, SimpleNamespace(output_text="x" * 500))
    assert len(smoke_test.check_text("m", "")) == 80


def test_check_vision_sends_text_and_image(monkeypatch):
    responses = fake_client(monkeypatch, SimpleNamespace(output_text="A warehouse."))
    assert smoke_test.check_vision("m", "data:image/png;base64,AAA") == "A warehouse."
    content = responses.calls[0]["input"][0]["content"]
    assert [c["type"] for c in content] == ["input_text", "input_image"]
    assert content[1]["image_url"] == "data:image/png;base64,AAA"


def test_check_vision_fails_on_empty_output(monkeypatch):
    fake_client(monkeypatch, SimpleNamespace(output_text=""))
    with pytest.raises(AssertionError):
        smoke_test.check_vision("m", "u")


def test_check_structured_requests_strict_schema(monkeypatch, out_dir):
    good = {"box_count": 1, "any_damage": False, "damage_types": ["none"], "confidence": 0.5, "reason": "ok"}
    responses = fake_client(monkeypatch, SimpleNamespace(output_text=json.dumps(good)))
    smoke_test.check_structured("m", "u")
    fmt = responses.calls[0]["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True and fmt["schema"] is smoke_test.DAMAGE_SCHEMA


@pytest.mark.parametrize("text", ["not json", "", "[]"])
def test_check_structured_fails_on_non_object_output(monkeypatch, out_dir, text):
    fake_client(monkeypatch, SimpleNamespace(output_text=text))
    with pytest.raises((json.JSONDecodeError, AttributeError, TypeError)):
        smoke_test.check_structured("m", "u")


def test_check_tools_reports_missing_call_with_model_text(monkeypatch):
    fake_client(monkeypatch, SimpleNamespace(output=[SimpleNamespace(type="message")], output_text="I cannot"))
    with pytest.raises(AssertionError, match="I cannot"):
        smoke_test.check_tools("m", "")


def test_check_tools_offers_the_inventory_tool_with_auto_choice(monkeypatch):
    call = SimpleNamespace(type="function_call", name="adjust_inventory", arguments="{}")
    responses = fake_client(monkeypatch, SimpleNamespace(output=[call], output_text=""))
    smoke_test.check_tools("m", "")
    kwargs = responses.calls[0]
    assert kwargs["tools"] == [smoke_test.ADJUST_INVENTORY_TOOL]
    assert "tool_choice" not in kwargs or kwargs["tool_choice"] == "auto"  # the API only supports auto


def test_inventory_tool_schema_is_strict_compatible():
    params = smoke_test.ADJUST_INVENTORY_TOOL["parameters"]
    assert params["additionalProperties"] is False
    assert set(params["required"]) == set(params["properties"])


def test_check_sam_sends_sam_metadata_and_saves_raw_output(monkeypatch, out_dir):
    responses = fake_client(monkeypatch, SimpleNamespace(output_text="<0f>"))
    summary = smoke_test.check_sam("sam-x", "u")
    assert all(c["metadata"] == {"mask_encoding": "one_bit", "include_confidence": "true"} for c in responses.calls)
    assert (out_dir / "sam_raw_cardboard_box.txt").exists() and (out_dir / "sam_raw_yellow_line.txt").exists()
    assert "0 boxes / 0 masks" in summary


def test_check_sam_handles_none_output_text(monkeypatch, out_dir):
    fake_client(monkeypatch, SimpleNamespace(output_text=None))
    assert "0 boxes" in smoke_test.check_sam("m", "u")


@pytest.mark.parametrize(
    "text",
    [
        "<|box;x1=1;y1=2;x2=3;y2=4;w=10;h=10|>",  # confidence is optional
        "<|box;x1=1;y1=2;x2=3;y2=4;w=10;h=10;c=1|>",
    ],
)
def test_box_regex_accepts_optional_confidence(text):
    assert len(smoke_test.BOX_RE.findall(text)) == 1


@pytest.mark.parametrize(
    "text",
    ["<|box;x1=a;y1=2;x2=3;y2=4;w=10;h=10|>", "<|box;x1=1;y1=2|>", "<|box;x1=-1;y1=2;x2=3;y2=4;w=10;h=10|>", ""],
)
def test_box_regex_rejects_malformed(text):
    assert smoke_test.BOX_RE.findall(text) == []


def test_mask_header_without_confidence():
    assert smoke_test.MASK_HEADER_RE.findall("<|mask;x=0;y=0;data=5,6,Z|>") == [("5", "6", "Z")]


def test_sam_prompts_are_one_concept_each():
    assert smoke_test.SAM_PROMPTS == ["cardboard box", "yellow line"]
    assert all("," not in p and " and " not in p for p in smoke_test.SAM_PROMPTS)


def test_synthetic_scene_has_expected_size_and_content(tmp_path):
    from PIL import Image

    path = tmp_path / "s.png"
    smoke_test.make_synthetic_scene(path)
    img = Image.open(path)
    assert img.size == (960, 640)
    assert len(img.convert("RGB").getcolors(maxcolors=1_000_000)) > 3  # floor, tape and box colours


def _install_checks(monkeypatch, results):
    names = ["check_text", "check_vision", "check_structured", "check_tools", "check_sam", "check_langchain_agent", "check_langfuse"]
    for name, result in zip(names, results, strict=True):
        def fn(model, url, _r=result):
            if isinstance(_r, BaseException):
                raise _r
            return _r
        monkeypatch.setattr(smoke_test, name, fn)


def test_main_returns_zero_when_all_checks_pass(monkeypatch, out_dir, capsys):
    monkeypatch.setattr("sys.argv", ["smoke_test"])
    _install_checks(monkeypatch, ["a", "b", "c", "d", "e", "f", "g"])
    assert smoke_test.main() == 0
    out = capsys.readouterr().out
    assert out.count("PASS") == 7 and "7/7 passed" in out
    assert (out_dir / "synthetic_scene.png").exists()


def test_main_returns_one_and_reports_every_failure(monkeypatch, out_dir, capsys):
    monkeypatch.setattr("sys.argv", ["smoke_test"])
    _install_checks(monkeypatch, ["a", RuntimeError("boom"), "c", ValueError("bad"), "e", "f", "g"])
    assert smoke_test.main() == 1
    out = capsys.readouterr().out
    assert out.count("FAIL") == 2 and "RuntimeError: boom" in out and "5/7 passed" in out


def test_main_never_prints_the_api_key(monkeypatch, out_dir, capsys):
    monkeypatch.setattr("sys.argv", ["smoke_test"])
    _install_checks(monkeypatch, ["a"] * 7)
    smoke_test.main()
    assert "test-key" not in capsys.readouterr().out


def test_main_uses_a_supplied_image(monkeypatch, out_dir, capsys, tmp_path):
    img = tmp_path / "w.png"
    smoke_test.make_synthetic_scene(img)
    monkeypatch.setattr("sys.argv", ["smoke_test", "--image", str(img)])
    seen = []
    monkeypatch.setattr(smoke_test, "check_text", lambda m, u: seen.append(u) or "ok")
    _install_checks_rest = ["check_vision", "check_structured", "check_tools", "check_sam", "check_langchain_agent", "check_langfuse"]
    for name in _install_checks_rest:
        monkeypatch.setattr(smoke_test, name, lambda m, u: "ok")
    assert smoke_test.main() == 0
    assert seen[0].startswith("data:image/png;base64,")
    assert not (out_dir / "synthetic_scene.png").exists()
