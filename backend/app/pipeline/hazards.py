import numpy as np

from app.pipeline.segment import Segment
from app.schemas import Finding, Severity

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


def detect_blocked_walkways(boxes: list[Segment], walkways: list[Segment]) -> list[Finding]:
    if not boxes or not walkways:
        return []
    union = np.logical_or.reduce([w.mask for w in walkways])
    findings = []
    for box in boxes:
        ratio = overlap_ratio(box.mask, union)
        if ratio <= BLOCK_THRESHOLD:
            continue
        touching = [w.confidence for w in walkways if np.logical_and(box.mask, w.mask).any()]
        # Geometry is exact, so confidence is only as good as the weaker of the two detections.
        confidence = min(box.confidence, max(touching))
        findings.append(
            Finding(
                id=f"hz_{box.id}",
                type="walkway_blocked",
                severity=severity_for(ratio),
                confidence=confidence,
                bbox=box.bbox,
                description=f"Box {box.id} covers a marked walkway ({ratio:.0%} of its area overlaps floor tape).",
            )
        )
    return findings
