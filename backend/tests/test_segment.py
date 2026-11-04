import asyncio

import numpy as np
import pytest
from fakes import concept_of, install_fake_model, sam_line, sam_record

from app.pipeline import segment as seg

W, H = 60, 40
URL = "data:image/jpeg;base64,AAAA"


def run(coro):
    return asyncio.run(coro)


def test_zero_matches_returns_empty(monkeypatch):
    install_fake_model(monkeypatch, lambda kw: "")
    assert run(seg.segment(URL, "cardboard box", width=W, height=H)) == []


def test_none_output_text_treated_as_empty(monkeypatch):
    install_fake_model(monkeypatch, lambda kw: None)
    assert run(seg.segment(URL, "cardboard box", width=W, height=H)) == []


def test_request_shape(monkeypatch):
    calls = install_fake_model(monkeypatch, lambda kw: "").calls
    run(seg.segment(URL, "yellow line", width=W, height=H))
    kw = calls[0]
    assert kw["model"] == "sam-3.1"
    assert kw["metadata"] == {"mask_encoding": "one_bit", "include_confidence": "true"}
    content = kw["input"][0]["content"]
    assert content == [{"type": "input_text", "text": "yellow line"}, {"type": "input_image", "image_url": URL}]


def test_real_mask_is_placed_at_box_offset(monkeypatch):
    raster = np.zeros((4, 5), dtype=np.uint8)
    raster[1:3, 1:4] = 1  # 2x3 blob inside a 4x5 box
    line = sam_line(sam_record(7, 10, 20, 14, 23, w=W, h=H, raster=raster, c=0.87))
    install_fake_model(monkeypatch, lambda kw: line)

    [s] = run(seg.segment(URL, "cardboard box", width=W, height=H, id_prefix="box"))

    assert s.id == "box_7"
    assert s.bbox.model_dump() == {"x1": 10, "y1": 20, "x2": 15, "y2": 24}
    assert s.confidence == pytest.approx(0.87)
    assert s.mask.shape == (H, W) and s.mask.dtype == bool
    assert s.mask.sum() == 6
    assert s.mask[21:23, 11:14].all()
    # Every SVG vertex lies in full-image pixel-edge coords around the blob (x 11..14, y 21..23).
    nums = [float(n) for n in s.svg.replace("M", " ").replace("L", " ").replace("Z", " ").split()]
    xs, ys = nums[0::2], nums[1::2]
    assert min(xs) >= 10.5 and max(xs) <= 14.5
    assert min(ys) >= 20.5 and max(ys) <= 23.5


def test_multiple_records_and_edge_clipping(monkeypatch):
    # SAM saw a wider source than our image: the second box must be clipped to W.
    line = sam_line(
        sam_record(0, 0, 0, 9, 9, w=W + 20, h=H),
        sam_record(1, W - 5, 30, W + 4, 39, w=W + 20, h=H),
    )
    install_fake_model(monkeypatch, lambda kw: line)
    a, b = run(seg.segment(URL, "cardboard box", width=W, height=H))
    assert a.mask.sum() == 100
    assert b.bbox.x2 == W
    assert b.mask.sum() == 5 * 10


def test_mask_raster_resized_when_not_box_sized():
    async def go():
        raster = np.ones((2, 2), dtype=np.uint8)
        result = await seg.parse_sam_output(sam_line(sam_record(0, 0, 0, 3, 3, w=W, h=H, raster=raster)))
        return seg.records_to_segments(result, width=W, height=H, id_prefix="box")

    [s] = run(go())
    assert s.mask[0:4, 0:4].all() and s.mask.sum() == 16


def test_malformed_line_is_skipped(monkeypatch):
    install_fake_model(monkeypatch, lambda kw: "<0f>0<|box;x1=1|>")
    assert run(seg.segment(URL, "cardboard box", width=W, height=H)) == []


def test_segment_all_runs_each_concept(monkeypatch):
    def handler(kw):
        if concept_of(kw) == "cardboard box":
            return sam_line(sam_record(0, 0, 0, 4, 4, w=W, h=H))
        return ""

    calls = install_fake_model(monkeypatch, handler).calls
    out = run(seg.segment_all(URL, width=W, height=H))
    assert sorted(concept_of(c) for c in calls) == ["cardboard box", "yellow line"]
    assert [s.id for s in out["box"]] == ["box_0"]
    assert out["walkway"] == []
