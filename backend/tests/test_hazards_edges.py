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
    findings = hazards.detect_blocked_walkways([box], [walkway], fill_occlusion=False)
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


# ---- occlusion: a box standing on tape hides the tape beneath it ---------------------------------------------

def stripe_with_hole(box_rect, side=SIDE):
    stripe = rect(0, 15, side, 25, side)
    x1, y1, x2, y2 = box_rect
    stripe[y1:y2, x1:x2] = False
    return stripe


def test_box_standing_on_the_tape_is_blocking_even_though_its_pixels_are_not_tape():
    box = seg("b", rect(15, 12, 25, 32))
    walkway = seg("w", stripe_with_hole((15, 12, 25, 32)))
    assert hazards.detect_blocked_walkways([box], [walkway], fill_occlusion=False) == []  # the raw overlap misses it
    [finding] = hazards.detect_blocked_walkways([box], [walkway])
    assert finding.id == "hz_b" and finding.severity == "medium"


def test_box_fully_inside_the_stripe_is_high_severity():
    box = seg("b", rect(15, 16, 25, 24))
    [finding] = hazards.detect_blocked_walkways([box], [seg("w", stripe_with_hole((15, 16, 25, 24)))])
    assert finding.severity == "high"


def test_box_beside_the_tape_is_not_flagged():
    box = seg("b", rect(15, 0, 25, 10))
    assert hazards.detect_blocked_walkways([box], [seg("w", rect(0, 15, SIDE, 25))]) == []


def test_gap_much_larger_than_the_box_is_not_treated_as_hidden_tape():
    tape = np.zeros((SIDE * 4, SIDE * 4), dtype=bool)
    tape[60:70, 0:40] = True
    tape[60:70, 120:160] = True  # 80 px gap
    box = seg("b", rect(75, 58, 85, 72, SIDE * 4))
    assert hazards.detect_blocked_walkways([box], [seg("w", tape)]) == []


def test_occluded_finding_takes_its_confidence_from_the_tape_detection():
    box = seg("b", rect(15, 12, 25, 28), conf=0.95)
    [finding] = hazards.detect_blocked_walkways([box], [seg("w", stripe_with_hole((15, 12, 25, 28)), conf=0.6)])
    assert finding.confidence == pytest.approx(0.6)


def test_closing_fills_small_holes_and_keeps_the_stripe_ends():
    stripe = rect(5, 15, 35, 25)
    holed = stripe.copy()
    holed[17:23, 18:24] = False
    closed = hazards.close_mask(holed, 4)
    assert np.array_equal(closed, stripe)


def test_closing_never_removes_pixels_and_does_not_grow_outwards():
    rng = np.random.default_rng(1)
    mask = rng.random((30, 30)) > 0.5
    closed = hazards.close_mask(mask, 2)
    assert (closed | mask).sum() == closed.sum()  # superset of the input
    assert not hazards.close_mask(rect(10, 10, 12, 12), 3)[:9].any()


@pytest.mark.parametrize("radius", [0, -1])
def test_closing_with_no_radius_is_a_noop(radius):
    mask = rect(5, 5, 9, 9)
    assert np.array_equal(hazards.close_mask(mask, radius), mask)


def test_occlusion_handling_stays_fast_for_big_boxes():
    side = 1500
    tape = np.zeros((side, side), dtype=bool)
    tape[700:760, :] = True
    boxes = []
    for i in range(10):
        x = i * 140
        tape[700:760, x : x + 120] = False
        boxes.append(seg(f"b{i}", rect(x, 600, x + 120, 860, side)))
    start = time.perf_counter()
    findings = hazards.detect_blocked_walkways(boxes, [seg("w", tape)])
    assert len(findings) == 10 and time.perf_counter() - start < 5
