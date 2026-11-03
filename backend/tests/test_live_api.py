"""Opt-in checks against the real Meta Model API (Phase 0 exit criteria). Never run in CI.

    cd backend && RUN_LIVE=1 .venv/bin/python -m pytest -m live -s

RUN_LIVE=1 uses the real key from .env and skips every offline test; without it these tests are skipped.
Each check prints its latency so the Phase 0 baseline can be copied into the plan.
"""

import asyncio
import json
import time

import pytest

from app.clients import file_to_data_url, get_client
from app.config import get_settings
from scripts import smoke_test

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def scene(tmp_path_factory):
    path = tmp_path_factory.mktemp("live") / "scene.png"
    smoke_test.make_synthetic_scene(path)
    return file_to_data_url(path)


def timed(label, fn):
    start = time.perf_counter()
    out = fn()
    print(f"\n[latency] {label}: {time.perf_counter() - start:.1f}s")
    return out


def test_text_call():
    assert timed("text", lambda: smoke_test.check_text(get_settings().muse_model, ""))


def test_vision_call(scene):
    assert timed("vision", lambda: smoke_test.check_vision(get_settings().muse_model, scene))


def test_structured_output_matches_the_schema(scene, tmp_path, monkeypatch):
    monkeypatch.setattr(smoke_test, "OUT_DIR", tmp_path)
    assert timed("structured", lambda: smoke_test.check_structured(get_settings().muse_model, scene))


def test_tool_calling_returns_a_function_call():
    assert timed("tools", lambda: smoke_test.check_tools(get_settings().muse_model, ""))


def test_sam_returns_parseable_boxes_and_masks_for_one_concept(scene):
    from app.pipeline import segment

    response = timed(
        "sam box",
        lambda: get_client().responses.create(
            model=get_settings().sam_model,
            input=[{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "cardboard box"}, {"type": "input_image", "image_url": scene}]}],
            metadata=segment.SAM_METADATA,
        ),
    )
    result = asyncio.run(segment.parse_sam_output(response.output_text or ""))
    segments = segment.records_to_segments(result, width=960, height=640, id_prefix="box")
    assert segments, "SAM found no boxes in the synthetic scene"
    assert all(0.0 <= s.confidence <= 1.0 and s.mask.shape == (640, 960) for s in segments)


def test_sam_zero_matches_is_empty_not_an_error(scene):
    from app.pipeline import segment

    response = get_client().responses.create(
        model=get_settings().sam_model,
        input=[{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "giraffe"}, {"type": "input_image", "image_url": scene}]}],
        metadata=segment.SAM_METADATA,
    )
    result = asyncio.run(segment.parse_sam_output(response.output_text or ""))
    assert segment.records_to_segments(result, width=960, height=640, id_prefix="x") == []


def test_model_frame_matches_the_image_we_sent(scene):
    """Settles the open question in test_segment_edges: does SAM report w/h equal to the sent image size?"""
    response = get_client().responses.create(
        model=get_settings().sam_model,
        input=[{"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "cardboard box"}, {"type": "input_image", "image_url": scene}]}],
        metadata={"mask_encoding": "one_bit", "include_confidence": "true"},
    )
    frames = {(int(w), int(h)) for w, h in __import__("re").findall(r";w=(\d+);h=(\d+)", response.output_text or "")}
    assert frames <= {(960, 640)}, f"SAM reported a different frame {frames}: scale coordinates in segment.py"


def test_full_scan_on_the_synthetic_scene(scene, tmp_path, monkeypatch):
    scan = pytest.importorskip("app.pipeline.scan")
    monkeypatch.setattr(scan, "CACHE_DIR", tmp_path)
    data = (smoke_test.OUT_DIR / "synthetic_scene.png")
    smoke_test.OUT_DIR.mkdir(parents=True, exist_ok=True)
    smoke_test.make_synthetic_scene(data)
    result = timed("full scan", lambda: asyncio.run(scan.run_scan(data.read_bytes(), "image/png")))
    assert result.objects and json.loads(result.model_dump_json())["scan_id"]
