import time

import numpy as np
import pytest
from spec_support import need

hazards = need("app.pipeline.hazards", "detect_blocked_walkways", "BLOCK_THRESHOLD")
segment_mod = need("app.pipeline.segment", "Segment")

from app.schemas import BBox, Finding  # noqa: E402

Segment = segment_mod.Segment
SIDE = 40


def seg(sid: str, mask: np.ndarray, conf: float = 0.9) -> "Segment":
    ys, xs = np.nonzero(mask)
    if len(xs):
        bbox = BBox(x1=int(xs.min()), y1=int(ys.min()), x2=int(xs.max()) + 1, y2=int(ys.max()) + 1)
    else:
        bbox = BBox(x1=0, y1=0, x2=0, y2=0)
    return Segment(id=sid, bbox=bbox, confidence=conf, mask=mask, svg="")


def rect(x1, y1, x2, y2, side=SIDE) -> np.ndarray:
    m = np.zeros((side, side), dtype=bool)
    m[y1:y2, x1:x2] = True
    return m


def box_with_overlap(overlap_px: int) -> tuple["Segment", "Segment"]:
    """A 10x10 box (100 px) whose bottom rows overlap a walkway by exactly `overlap_px` pixels."""
    box = rect(10, 10, 20, 20)
    walkway = np.zeros((SIDE, SIDE), dtype=bool)
    flat = np.argwhere(box)[:overlap_px]
    for y, x in flat:
        walkway[y, x] = True
    return seg("b", box), seg("w", walkway)


@pytest.mark.parametrize(
    ("overlap_px", "expected"),
    [
        (0, None),
        (1, None),
        (14, None),
        (15, None),  # exactly 15%: strictly greater is required
        (16, "low"),
        (30, "low"),  # exactly 30%: still low
        (31, "medium"),
        (50, "medium"),  # exactly 50%: still medium
        (51, "high"),
        (100, "high"),
    ],
)
def test_threshold_and_severity_edges_end_to_end(overlap_px, expected):
    box, walkway = box_with_overlap(overlap_px)
    findings = hazards.detect_blocked_walkways([box], [walkway])
    if expected is None:
        assert findings == []
    else:
        assert [f.severity for f in findings] == [expected]


def test_threshold_constants_match_the_plan():
    assert hazards.BLOCK_THRESHOLD == 0.15
    assert hazards.HIGH_RATIO == 0.5 and hazards.MEDIUM_RATIO == 0.3


def test_walkway_with_an_empty_mask_blocks_nothing():
    box = seg("b", rect(5, 5, 15, 15))
    empty = seg("w", np.zeros((SIDE, SIDE), dtype=bool))
    assert hazards.detect_blocked_walkways([box], [empty]) == []


def test_box_with_an_empty_mask_is_never_flagged():
    empty = seg("b", np.zeros((SIDE, SIDE), dtype=bool))
    full = seg("w", np.ones((SIDE, SIDE), dtype=bool))
    assert hazards.detect_blocked_walkways([empty], [full]) == []


def test_disjoint_walkway_is_ignored():
    box = seg("b", rect(0, 0, 10, 10))
    walkway = seg("w", rect(20, 20, 40, 40))
    assert hazards.detect_blocked_walkways([box], [walkway]) == []


def test_only_overlapping_boxes_are_flagged_and_order_is_preserved():
    walkway = seg("w", rect(0, 18, 40, 24))
    boxes = [seg("a", rect(0, 0, 10, 10)), seg("b", rect(0, 14, 10, 24)), seg("c", rect(20, 20, 30, 24))]
    assert [f.id for f in hazards.detect_blocked_walkways(boxes, [walkway])] == ["hz_b", "hz_c"]


def test_confidence_uses_the_strongest_touching_walkway_but_never_exceeds_the_box():
    box = seg("b", rect(0, 0, 10, 10), conf=0.95)
    near = seg("w1", rect(0, 0, 10, 10), conf=0.40)
    other_near = seg("w2", rect(0, 0, 5, 10), conf=0.70)
    far = seg("w3", rect(30, 30, 40, 40), conf=0.99)  # does not touch: must not raise confidence
    [finding] = hazards.detect_blocked_walkways([box], [near, other_near, far])
    assert finding.confidence == pytest.approx(0.70)
    weak_box = seg("b", rect(0, 0, 10, 10), conf=0.30)
    [finding] = hazards.detect_blocked_walkways([weak_box], [near, other_near])
    assert finding.confidence == pytest.approx(0.30)


def test_zero_confidence_still_produces_a_valid_finding():
    [finding] = hazards.detect_blocked_walkways(
        [seg("b", rect(0, 0, 10, 10), conf=0.0)], [seg("w", rect(0, 0, 10, 10), conf=0.0)]
    )
    assert isinstance(finding, Finding) and finding.confidence == 0.0


def test_finding_carries_the_box_geometry_and_a_percentage():
    box = seg("box_7", rect(4, 6, 14, 16))
    [finding] = hazards.detect_blocked_walkways([box], [seg("w", np.ones((SIDE, SIDE), dtype=bool))])
    assert finding.id == "hz_box_7" and finding.type == "walkway_blocked"
    assert finding.bbox == box.bbox
    assert "100%" in finding.description and "box_7" in finding.description


def test_inputs_are_not_mutated():
    box, walkway = box_with_overlap(60)
    before = (box.mask.copy(), walkway.mask.copy())
    hazards.detect_blocked_walkways([box], [walkway])
    assert np.array_equal(box.mask, before[0]) and np.array_equal(walkway.mask, before[1])


def test_many_walkways_use_their_union_once():
    box = seg("b", rect(0, 0, 10, 10))
    # Four quarter-slices of the box: each alone is 25% (flagged), together they cover 100%.
    strips = [seg(f"w{i}", rect(0, i * 3, 10, i * 3 + 3)) for i in range(4)]
    [finding] = hazards.detect_blocked_walkways([box], strips)
    assert finding.severity == "high"


def test_realistic_size_runs_quickly():
    side = 1024
    walkway = seg("w", np.zeros((side, side), dtype=bool).__or__(rect(0, 500, side, 540, side)))
    boxes = [seg(f"b{i}", rect(i * 30, 480, i * 30 + 25, 560, side)) for i in range(30)]
    start = time.perf_counter()
    findings = hazards.detect_blocked_walkways(boxes, [walkway])
    assert len(findings) == 30
    assert time.perf_counter() - start < 5
