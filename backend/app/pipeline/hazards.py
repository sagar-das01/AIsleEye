import numpy as np

from app.pipeline.segment import Segment
from app.schemas import BBox, Finding, Severity

BLOCK_THRESHOLD = 0.15
HIGH_RATIO = 0.5
MEDIUM_RATIO = 0.3


def overlap_ratio(box_mask: np.ndarray, walkway_union: np.ndarray) -> float:
    area = int(box_mask.sum())
    if area == 0:
        return 0.0
    return int(np.logical_and(box_mask, walkway_union).sum()) / area


def severity_for(ratio: float) -> Severity:
    if ratio > HIGH_RATIO:
        return "high"
    if ratio > MEDIUM_RATIO:
        return "medium"
    return "low"


def _window_sum(a: np.ndarray, r: int, pad_value: int) -> np.ndarray:
    # Sum over (2r+1)^2 windows via an integral image; outside the array counts as pad_value.
    k = 2 * r + 1
    h, w = a.shape
    padded = np.pad(a.astype(np.int32), r, constant_values=pad_value)
    integral = np.zeros((padded.shape[0] + 1, padded.shape[1] + 1), dtype=np.int32)
    integral[1:, 1:] = padded.cumsum(0).cumsum(1)
    return integral[k : k + h, k : k + w] - integral[:h, k : k + w] - integral[k : k + h, :w] + integral[:h, :w]


def close_mask(mask: np.ndarray, radius: int) -> np.ndarray:
    """Binary closing: fills gaps up to about 2*radius wide without growing the mask outward."""
    if radius <= 0 or not mask.any():
        return mask
    dilated = _window_sum(mask, radius, 0) > 0
    return _window_sum(dilated, radius, 1) == (2 * radius + 1) ** 2


def _crop_bounds(bbox: BBox, radius: int, shape: tuple[int, int]) -> tuple[int, int, int, int]:
    margin = 3 * radius
    h, w = shape
    return max(0, bbox.y1 - margin), min(h, bbox.y2 + margin), max(0, bbox.x1 - margin), min(w, bbox.x2 + margin)


def detect_blocked_walkways(boxes: list[Segment], walkways: list[Segment], *, fill_occlusion: bool = True) -> list[Finding]:
    """Flag boxes whose mask covers more than BLOCK_THRESHOLD of the walkway region.

    A box standing on tape hides the tape under it, so the tape mask has a hole exactly where the box is.
    With fill_occlusion the walkway is closed over gaps up to the box's own size before measuring overlap.
    """
    if not boxes or not walkways:
        return []
    union = np.logical_or.reduce([w.mask for w in walkways])
    findings = []
    for box in boxes:
        radius = max(box.bbox.x2 - box.bbox.x1, box.bbox.y2 - box.bbox.y1) // 2 + 1 if fill_occlusion else 0
        y1, y2, x1, x2 = _crop_bounds(box.bbox, radius, box.mask.shape)
        box_crop = box.mask[y1:y2, x1:x2]
        region = close_mask(union[y1:y2, x1:x2], radius)
        ratio = overlap_ratio(box_crop, region)
        if ratio <= BLOCK_THRESHOLD:
            continue
        touching = [
            w.confidence for w in walkways if np.logical_and(box_crop, close_mask(w.mask[y1:y2, x1:x2], radius)).any()
        ]
        # Geometry is exact, so confidence is only as good as the weaker of the two detections.
        confidence = min(box.confidence, max(touching))
        findings.append(
            Finding(
                id=f"hz_{box.id}",
                type="walkway_blocked",
                severity=severity_for(ratio),
                confidence=confidence,
                bbox=box.bbox,
                description=f"Box {box.id} sits on a marked walkway ({ratio:.0%} of its area).",
            )
        )
    return findings
