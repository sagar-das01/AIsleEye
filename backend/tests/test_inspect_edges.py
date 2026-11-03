import asyncio
import json

import httpx
import openai
import pytest
from PIL import Image
from spec_support import need

inspect_mod = need("app.pipeline.inspect", "inspect_boxes", "parse_assessments", "damage_findings")
fakes = need("fakes", "install_fake_model")

from app.schemas import BBox, DamageAssessment  # noqa: E402

BOX = BBox(x1=10, y1=10, x2=40, y2=40)


def assessment_json(items) -> str:
    return json.dumps({"assessments": items})


def item(index, damaged=True, damage_type="dent", severity="low", confidence=0.8, reason="r"):
    return {"index": index, "damaged": damaged, "damage_type": damage_type, "severity": severity,
            "confidence": confidence, "reason": reason}


def image_count(kwargs) -> int:
    return sum(1 for c in kwargs["input"][0]["content"] if c["type"] == "input_image")


def echo_handler(kwargs):
    """Marks each result with the number of images in its chunk so ordering across chunks is checkable."""
    n = image_count(kwargs)
    return assessment_json([item(i, reason=f"chunk{n}-{i}") for i in range(n)])


@pytest.fixture
def image():
    return Image.new("RGB", (200, 200), (90, 90, 90))


# ---- chunking -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("count", "chunk_sizes"),
    [(1, [1]), (49, [49]), (50, [50]), (51, [50, 1]), (100, [50, 50]), (101, [50, 50, 1])],
)
def test_chunk_boundaries(monkeypatch, image, count, chunk_sizes):
    fake = fakes.install_fake_model(monkeypatch, echo_handler)
    result = asyncio.run(inspect_mod.inspect_boxes(image, [BOX] * count))
    assert sorted((image_count(c) for c in fake.calls), reverse=True) == chunk_sizes
    assert len(result) == count and all(a is not None for a in result)


def test_results_keep_the_input_order_across_chunks(monkeypatch, image):
    fakes.install_fake_model(monkeypatch, echo_handler)
    result = asyncio.run(inspect_mod.inspect_boxes(image, [BOX] * 51))
    assert [a.reason for a in result[:50]] == [f"chunk50-{i}" for i in range(50)]
    assert result[50].reason == "chunk1-0"


def test_one_unparseable_chunk_only_blanks_its_own_boxes(monkeypatch, image):
    def handler(kwargs):
        return "garbage" if image_count(kwargs) == 1 else echo_handler(kwargs)

    fakes.install_fake_model(monkeypatch, handler)
    result = asyncio.run(inspect_mod.inspect_boxes(image, [BOX] * 51))
    assert all(a is not None for a in result[:50]) and result[50] is None


def test_a_failing_chunk_surfaces_the_api_error(monkeypatch, image):
    err = openai.APIConnectionError(request=httpx.Request("POST", "https://x.test"))
    fakes.install_fake_model(monkeypatch, lambda kw: err if image_count(kw) == 1 else echo_handler(kw))
    with pytest.raises(openai.APIError):
        asyncio.run(inspect_mod.inspect_boxes(image, [BOX] * 51))


def test_none_output_text_leaves_boxes_unassessed(monkeypatch, image):
    fakes.install_fake_model(monkeypatch, lambda kw: None)
    assert asyncio.run(inspect_mod.inspect_boxes(image, [BOX, BOX])) == [None, None]


def test_request_carries_one_labelled_crop_per_box_and_the_injection_guard(monkeypatch, image):
    fake = fakes.install_fake_model(monkeypatch, echo_handler)
    asyncio.run(inspect_mod.inspect_boxes(image, [BOX, BOX, BOX]))
    kwargs = fake.calls[0]
    content = kwargs["input"][0]["content"]
    assert [c["text"] for c in content if c["type"] == "input_text"][1:] == ["Image 0:", "Image 1:", "Image 2:"]
    assert all(c["image_url"].startswith("data:image/jpeg;base64,") for c in content if c["type"] == "input_image")
    assert "never as instructions" in content[0]["text"]
    fmt = kwargs["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True
    assert kwargs["model"] == "muse-spark-1.3"
    assert "tools" not in kwargs  # inspection must never be able to call tools


# ---- parse_assessments ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["", "null", "[]", "{}", '{"assessments": null}', '{"assessments": "x"}', '{"assessments": {}}', "42", '"s"'])
def test_non_conforming_documents_leave_everything_unassessed(text):
    assert inspect_mod.parse_assessments(text, 3) == [None, None, None]


def test_zero_count_never_fails():
    assert inspect_mod.parse_assessments(assessment_json([item(0)]), 0) == []


@pytest.mark.parametrize("index", [-1, 3, 10**9])
def test_out_of_range_index_is_ignored(index):
    assert inspect_mod.parse_assessments(assessment_json([item(index)]), 3) == [None, None, None]


def test_duplicate_index_keeps_the_first_answer():
    out = inspect_mod.parse_assessments(assessment_json([item(1, reason="first"), item(1, reason="second")]), 2)
    assert out[1].reason == "first"


@pytest.mark.parametrize("confidence", [-5, 1.5, 1e9, float("inf"), float("nan")])
def test_confidence_is_always_within_zero_and_one(confidence):
    text = assessment_json([item(0, confidence=confidence)]).replace("Infinity", "1e999")
    [a] = inspect_mod.parse_assessments(text, 1)
    assert 0.0 <= a.confidence <= 1.0


@pytest.mark.parametrize(("length", "expected"), [(299, 299), (300, 300), (301, 300), (5000, 300)])
def test_reason_length_cap(length, expected):
    [a] = inspect_mod.parse_assessments(assessment_json([item(0, reason="x" * length)]), 1)
    assert len(a.reason) == expected


def test_unicode_and_markup_reasons_survive_parsing_unchanged():
    reason = "Déchirure <b>grave</b> — 破損"
    [a] = inspect_mod.parse_assessments(assessment_json([item(0, reason=reason)]), 1)
    assert a.reason == reason


@pytest.mark.parametrize(
    "bad",
    [
        {"index": 0, "damaged": True, "damage_type": "exploded", "severity": "low", "confidence": 1, "reason": "r"},
        {"index": 0, "damaged": True, "damage_type": "dent", "severity": "critical", "confidence": 1, "reason": "r"},
        {"index": 0, "damaged": "maybe", "damage_type": "dent", "severity": "low", "confidence": 1, "reason": "r"},
        {"index": 0, "damaged": True, "damage_type": "dent", "severity": "low", "reason": "r"},
        {"index": "first", "damaged": True, "damage_type": "dent", "severity": "low", "confidence": 1, "reason": "r"},
        "not an object",
        None,
    ],
)
def test_invalid_items_are_skipped_but_valid_siblings_survive(bad):
    out = inspect_mod.parse_assessments(assessment_json([bad, item(1)]), 2)
    assert out[0] is None and out[1] is not None


def test_extra_keys_in_an_item_are_tolerated():
    [a] = inspect_mod.parse_assessments(assessment_json([{**item(0), "bonus": "ignored"}]), 1)
    assert a is not None


def test_model_may_wrap_json_in_prose_but_that_is_not_accepted():
    # Strict-schema mode returns bare JSON; prose-wrapped output must not be half-parsed.
    assert inspect_mod.parse_assessments("Sure! " + assessment_json([item(0)]), 1) == [None]


# ---- damage_findings ------------------------------------------------------------------------------------------

def a(damaged=True, damage_type="dent", severity="medium", confidence=0.6, reason="Side dent"):
    return DamageAssessment(damaged=damaged, damage_type=damage_type, severity=severity, confidence=confidence, reason=reason)


def test_misaligned_inputs_are_an_error_not_silent_truncation():
    with pytest.raises(ValueError):
        inspect_mod.damage_findings(["b0", "b1"], [BOX], [a()])


def test_empty_inputs_give_no_findings():
    assert inspect_mod.damage_findings([], [], []) == []


def test_finding_fields_are_taken_from_the_assessment():
    [f] = inspect_mod.damage_findings(["box_3"], [BOX], [a(damage_type="water_damage", severity="high", confidence=0.42)])
    assert (f.id, f.type, f.severity, f.confidence, f.bbox) == ("dmg_box_3", "box_damage", "high", 0.42, BOX)
    assert "water damage" in f.description and "box_3" in f.description and "Side dent" in f.description


def test_intact_and_unassessed_boxes_produce_nothing():
    findings = inspect_mod.damage_findings(["a", "b", "c"], [BOX] * 3, [a(damaged=False, damage_type="none"), None, a()])
    assert [f.id for f in findings] == ["dmg_c"]


def test_empty_reason_does_not_leave_a_dangling_sentence():
    [f] = inspect_mod.damage_findings(["b"], [BOX], [a(reason="")])
    assert f.description == f.description.strip() and "None" not in f.description


@pytest.mark.xfail(
    strict=True,
    reason="GAP: damaged=true with damage_type='none' is self-contradictory model output; it should be "
    "dropped or flagged needs-review (Phase 5) instead of raising a 'Box b: none.' finding",
)
def test_contradictory_assessment_does_not_become_a_confident_finding():
    assert inspect_mod.damage_findings(["b"], [BOX], [a(damaged=True, damage_type="none")]) == []


# ---- crops ----------------------------------------------------------------------------------------------------

def test_crop_bounds_one_pixel_box_gets_no_padding():
    assert inspect_mod.crop_bounds(BBox(x1=5, y1=5, x2=6, y2=6), 100, 100) == (5, 5, 6, 6)


def test_crop_bounds_full_image_box_stays_in_bounds():
    assert inspect_mod.crop_bounds(BBox(x1=0, y1=0, x2=100, y2=50), 100, 50) == (0, 0, 100, 50)


def test_crop_bounds_never_exceeds_the_image_for_any_corner():
    for x1, y1 in [(0, 0), (95, 0), (0, 95), (95, 95)]:
        l, t, r, b = inspect_mod.crop_bounds(BBox(x1=x1, y1=y1, x2=x1 + 5, y2=y1 + 5), 100, 100)
        assert 0 <= l < r <= 100 and 0 <= t < b <= 100


@pytest.mark.parametrize("mode", ["L", "RGBA", "P", "CMYK"])
def test_crop_to_data_url_accepts_any_pillow_mode(mode):
    url = inspect_mod.crop_to_data_url(Image.new(mode, (50, 50)), BBox(x1=5, y1=5, x2=30, y2=30))
    assert url.startswith("data:image/jpeg;base64,")


def test_crop_matches_the_padded_bounds():
    import base64
    import io

    url = inspect_mod.crop_to_data_url(Image.new("RGB", (200, 200)), BBox(x1=50, y1=50, x2=150, y2=150))
    crop = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1])))
    assert crop.size == (120, 120)  # 100 px + 10% padding each side
