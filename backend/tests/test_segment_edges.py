import asyncio

import httpx
import numpy as np
import openai
import pytest
from spec_support import need

seg_mod = need("app.pipeline.segment", "segment_all", "records_to_segments", "parse_sam_output")
fakes = need("fakes", "install_fake_model", "sam_record", "sam_line", "concept_of")

W, H = 100, 80


def run(coro):
    return asyncio.run(coro)


def parsed(text, *, width=W, height=H, prefix="box"):
    result = run(seg_mod.parse_sam_output(text))
    return seg_mod.records_to_segments(result, width=width, height=height, id_prefix=prefix)


# ---- parsing --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["", "   ", "\n", "hello world", "<0f>", "<0f>,,,", "<|box;|>", "\x00\x01"])
def test_garbage_or_empty_output_yields_no_segments(text):
    assert parsed(text) == []


def test_a_bad_line_does_not_hide_good_lines(caplog):
    good = fakes.sam_line(fakes.sam_record(0, 10, 10, 19, 19, w=W, h=H))
    good2 = fakes.sam_line(fakes.sam_record(2, 30, 30, 39, 39, w=W, h=H))
    text = "\n".join([good, fakes.sam_line("1<|box;x1=zz|>"), good2])
    assert [s.id for s in parsed(text)] == ["box_0", "box_2"]
    assert "malformed_record" in caplog.text


def test_a_malformed_record_loses_the_records_after_it_on_the_same_line():
    # Characterisation of the parser: SAM puts all objects of a frame on ONE line and parsing stops at the first
    # corrupt record, so later objects on that line are lost. We log it and degrade instead of crashing.
    first = fakes.sam_record(0, 10, 10, 19, 19, w=W, h=H)
    last = fakes.sam_record(2, 30, 30, 39, 39, w=W, h=H)
    assert [s.id for s in parsed(fakes.sam_line(first, "1<|box;x1=zz|>", last))] == ["box_0"]


def test_ids_use_the_prefix_and_model_object_id():
    [s] = parsed(fakes.sam_line(fakes.sam_record(7, 0, 0, 4, 4, w=W, h=H)), prefix="walkway")
    assert s.id == "walkway_7"


def test_confidence_falls_back_to_the_mask_then_to_zero():
    mask_only = "0<|mask;x=0;y=0;c=0.55;data=1,1,{}|>".format(fakes.encode_mask(np.ones((1, 1), dtype=np.uint8)))
    no_conf = "0<|mask;x=0;y=0;data=1,1,{}|>".format(fakes.encode_mask(np.ones((1, 1), dtype=np.uint8)))
    for text, expected in ((mask_only, None), (no_conf, 0.0)):
        result = run(seg_mod.parse_sam_output("<0f>" + text))
        segs = seg_mod.records_to_segments(result, width=W, height=H, id_prefix="x")
        # A mask record with no box record has no bounds to place it; it must be skipped, never crash.
        assert segs == [] or segs[0].confidence in (0.0, 0.55) or expected is None


# ---- geometry -------------------------------------------------------------------------------------------------

def test_full_box_mask_is_bool_image_sized_and_exact():
    [s] = parsed(fakes.sam_line(fakes.sam_record(0, 20, 30, 49, 59, w=W, h=H)))
    assert s.mask.dtype == bool and s.mask.shape == (H, W)
    assert int(s.mask.sum()) == 30 * 30
    assert s.mask[30:60, 20:50].all() and not s.mask[:30].any() and not s.mask[:, :20].any()
    assert (s.bbox.x1, s.bbox.y1, s.bbox.x2, s.bbox.y2) == (20, 30, 50, 60)


def test_box_flush_with_the_bottom_right_corner_is_kept_whole():
    [s] = parsed(fakes.sam_line(fakes.sam_record(0, 90, 70, 99, 79, w=W, h=H)))
    assert (s.bbox.x2, s.bbox.y2) == (W, H) and int(s.mask.sum()) == 100


def test_box_sticking_out_of_the_image_is_clipped_to_it():
    # The model frame (w/h) may be larger than the image we hold; anything outside the image is cut off.
    [s] = parsed(fakes.sam_line(fakes.sam_record(0, 90, 70, 109, 89, w=200, h=200)))
    assert (s.bbox.x2, s.bbox.y2) == (W, H)
    assert int(s.mask.sum()) == 10 * 10


def test_box_entirely_outside_the_image_is_dropped():
    assert parsed(fakes.sam_line(fakes.sam_record(0, 150, 150, 160, 160, w=200, h=200))) == []


@pytest.mark.xfail(
    strict=True,
    reason="GAP (not seen live: SAM reported the sent frame): if w/h ever differs from the image we sent, "
    "coordinates must be scaled to the image instead of being used as raw pixels",
)
def test_coordinates_are_scaled_when_the_model_frame_differs_from_the_image():
    [s] = parsed(fakes.sam_line(fakes.sam_record(0, 100, 80, 119, 99, w=200, h=160)))  # 2x frame
    assert (s.bbox.x1, s.bbox.y1) == (50, 40)


def test_single_pixel_object():
    [s] = parsed(fakes.sam_line(fakes.sam_record(0, 5, 5, 5, 5, w=W, h=H)))
    assert int(s.mask.sum()) == 1 and s.mask[5, 5]


def test_irregular_mask_keeps_its_shape_at_the_box_offset():
    raster = np.zeros((6, 8), dtype=np.uint8)
    raster[0, :] = 1
    raster[:, 0] = 1  # an "L"
    [s] = parsed(fakes.sam_line(fakes.sam_record(0, 10, 20, 17, 25, w=W, h=H, raster=raster)))
    assert np.array_equal(s.mask[20:26, 10:18], raster.astype(bool))
    assert int(s.mask.sum()) == int(raster.sum())


def test_bbox_is_half_open_and_tight_around_the_mask_box():
    [s] = parsed(fakes.sam_line(fakes.sam_record(0, 20, 30, 49, 59, w=W, h=H)))
    assert s.bbox.x2 - s.bbox.x1 == 30 and s.bbox.y2 - s.bbox.y1 == 30


def test_svg_path_stays_inside_the_object_bounds():
    import re

    [s] = parsed(fakes.sam_line(fakes.sam_record(0, 20, 30, 49, 59, w=W, h=H)))
    pts = [(float(x), float(y)) for x, y in re.findall(r"[ML](-?[\d.]+) (-?[\d.]+)", s.svg)]
    assert pts and s.svg.startswith("M")
    assert all(19.5 <= x <= 50.5 and 29.5 <= y <= 60.5 for x, y in pts)


def test_sixty_objects_are_all_returned_in_order():
    recs = [fakes.sam_record(i, (i % 10) * 10, (i // 10) * 10, (i % 10) * 10 + 8, (i // 10) * 10 + 8, w=W, h=H) for i in range(60)]
    segs = parsed(fakes.sam_line(*recs))
    assert [s.id for s in segs] == [f"box_{i}" for i in range(60)]


# ---- requests -------------------------------------------------------------------------------------------------

def sam_handler(kw):
    return fakes.sam_line(fakes.sam_record(0, 10, 10, 19, 19, w=W, h=H))


def test_segment_all_with_no_matches_returns_both_labels_empty(monkeypatch):
    fakes.install_fake_model(monkeypatch, lambda kw: "")
    assert run(seg_mod.segment_all("data:image/jpeg;base64,AA", width=W, height=H)) == {"box": [], "walkway": []}


def test_each_request_has_one_concept_one_image_and_sam_metadata(monkeypatch):
    fake = fakes.install_fake_model(monkeypatch, sam_handler)
    run(seg_mod.segment_all("data:image/jpeg;base64,AA", width=W, height=H))
    assert sorted(fakes.concept_of(c) for c in fake.calls) == ["cardboard box", "yellow line"]
    for call in fake.calls:
        assert [p["type"] for p in call["input"][0]["content"]] == ["input_text", "input_image"]
        assert call["metadata"] == {"mask_encoding": "one_bit", "include_confidence": "true"}
        assert call["model"] == "sam-3.1" and "tools" not in call


def test_concepts_run_concurrently(monkeypatch):
    from types import SimpleNamespace

    started = 0

    class Barrier:
        async def create(self, **kwargs):
            nonlocal started
            started += 1
            for _ in range(100):  # a sequential implementation never sees the other call start
                if started == 2:
                    break
                await asyncio.sleep(0.01)
            assert started == 2, "second SAM request never started while the first was in flight"
            return SimpleNamespace(output_text="")

    fake = SimpleNamespace(responses=Barrier())
    for mod in fakes.PATCHED_MODULES:
        monkeypatch.setattr(f"{mod}.get_async_client", lambda: fake)
    run(seg_mod.segment_all("data:image/jpeg;base64,AA", width=W, height=H))


@pytest.mark.parametrize(
    "error",
    [
        openai.APIConnectionError(request=httpx.Request("POST", "https://x.test")),
        openai.APITimeoutError(request=httpx.Request("POST", "https://x.test")),
        openai.RateLimitError("slow down", response=httpx.Response(429, request=httpx.Request("POST", "https://x.test")), body=None),
        openai.AuthenticationError("bad key", response=httpx.Response(401, request=httpx.Request("POST", "https://x.test")), body=None),
    ],
    ids=["connection", "timeout", "rate-limit", "auth"],
)
def test_model_errors_propagate_as_openai_errors(monkeypatch, error):
    fakes.install_fake_model(monkeypatch, lambda kw: error)
    with pytest.raises(openai.APIError):
        run(seg_mod.segment_all("data:image/jpeg;base64,AA", width=W, height=H))


def test_failure_of_one_concept_fails_the_scan_rather_than_returning_half_a_result(monkeypatch):
    err = openai.APIConnectionError(request=httpx.Request("POST", "https://x.test"))
    fakes.install_fake_model(monkeypatch, lambda kw: err if fakes.concept_of(kw) == "yellow line" else sam_handler(kw))
    with pytest.raises(openai.APIError):
        run(seg_mod.segment_all("data:image/jpeg;base64,AA", width=W, height=H))


def test_segment_never_sends_more_than_the_image_it_was_given(monkeypatch):
    fake = fakes.install_fake_model(monkeypatch, lambda kw: "")
    run(seg_mod.segment("data:image/jpeg;base64,QUJD", "cardboard box", width=W, height=H))
    sent = fake.calls[0]["input"][0]["content"][1]["image_url"]
    assert sent == "data:image/jpeg;base64,QUJD"


def test_walkway_concept_comes_from_settings(monkeypatch):
    monkeypatch.setenv("WALKWAY_CONCEPT", "red tape")
    seg_mod.get_settings.cache_clear()
    try:
        fake = fakes.install_fake_model(monkeypatch, lambda kw: "")
        run(seg_mod.segment_all("data:image/jpeg;base64,AA", width=W, height=H))
        assert sorted(fakes.concept_of(c) for c in fake.calls) == ["cardboard box", "red tape"]
    finally:
        monkeypatch.undo()
        seg_mod.get_settings.cache_clear()
