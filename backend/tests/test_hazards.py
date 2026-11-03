import numpy as np
import pytest

from app.pipeline.hazards import BLOCK_THRESHOLD, detect_blocked_walkways, overlap_ratio, severity_for
from app.pipeline.segment import Segment
from app.schemas import BBox

H, W = 100, 100


def make_segment(id_: str, x1: int, y1: int, x2: int, y2: int, conf: float = 0.9) -> Segment:
    mask = np.zeros((H, W), dtype=bool)
    mask[y1:y2, x1:x2] = True
    return Segment(id=id_, bbox=BBox(x1=x1, y1=y1, x2=x2, y2=y2), confidence=conf, mask=mask, svg="")


WALKWAY = make_segment("walkway_0", 0, 50, 100, 70, conf=0.8)


def box_with_overlap(rows_on_walkway: int, conf: float = 0.95) -> Segment:
    # 10x10 box whose bottom `rows_on_walkway` rows sit on the walkway band starting at y=50.
    top = 50 - (10 - rows_on_walkway)
    return make_segment("box_0", 10, top, 20, top + 10, conf=conf)


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [(0.0, "low"), (0.3, "low"), (0.31, "medium"), (0.5, "medium"), (0.51, "high"), (1.0, "high")],
)
def test_severity_bands(ratio, expected):
    assert severity_for(ratio) == expected


def test_overlap_ratio_empty_box_is_zero():
    assert overlap_ratio(np.zeros((H, W), bool), WALKWAY.mask) == 0.0


@pytest.mark.parametrize(
    ("rows", "finding", "severity"),
    [(0, False, None), (1, False, None), (2, True, "low"), (4, True, "medium"), (6, True, "high"), (10, True, "high")],
)
def test_threshold_and_severity(rows, finding, severity):
    box = box_with_overlap(rows)
    assert overlap_ratio(box.mask, WALKWAY.mask) == pytest.approx(rows / 10)
    out = detect_blocked_walkways([box], [WALKWAY])
    assert bool(out) is finding
    if finding:
        [f] = out
        assert f.type == "walkway_blocked"
        assert f.severity == severity
        assert f.id == "hz_box_0"
        assert f.bbox == box.bbox


def test_threshold_is_strict():
    assert BLOCK_THRESHOLD == 0.15
    box = make_segment("box_0", 0, 0, 20, 20)  # 400 px
    walk = make_segment("walkway_0", 0, 17, 20, 20)  # 60 px overlap = exactly 0.15
    assert detect_blocked_walkways([box], [walk]) == []


def test_confidence_is_min_of_box_and_touching_walkway():
    far = make_segment("walkway_1", 0, 0, 5, 5, conf=0.99)
    [f] = detect_blocked_walkways([box_with_overlap(5, conf=0.95)], [WALKWAY, far])
    assert f.confidence == pytest.approx(0.8)
    [f] = detect_blocked_walkways([box_with_overlap(5, conf=0.6)], [WALKWAY, far])
    assert f.confidence == pytest.approx(0.6)


def test_overlap_uses_union_of_walkways():
    a = make_segment("walkway_0", 0, 0, 15, 100)
    b = make_segment("walkway_1", 15, 0, 100, 100)
    box = make_segment("box_0", 10, 10, 20, 20)
    [f] = detect_blocked_walkways([box], [a, b])
    assert f.severity == "high"


def test_no_walkways_or_boxes():
    assert detect_blocked_walkways([], [WALKWAY]) == []
    assert detect_blocked_walkways([box_with_overlap(10)], []) == []


def test_deterministic():
    boxes = [box_with_overlap(4), make_segment("box_1", 60, 55, 70, 65)]
    assert detect_blocked_walkways(boxes, [WALKWAY]) == detect_blocked_walkways(boxes, [WALKWAY])
