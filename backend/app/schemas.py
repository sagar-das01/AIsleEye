from typing import Literal

from pydantic import BaseModel, Field

Severity = Literal["low", "medium", "high"]
FindingType = Literal["box_damage", "walkway_blocked"]
DamageType = Literal["tear", "crushed_corner", "dent", "water_damage", "open_flap", "none"]


class BBox(BaseModel):
    # Half-open pixel box: x2/y2 are exclusive, so width = x2 - x1.
    x1: int
    y1: int
    x2: int
    y2: int


ReviewStatus = Literal["proposed", "corrected", "added", "rejected"]


class FindingOriginal(BaseModel):
    """What the model proposed before a supervisor corrected it."""

    type: FindingType
    severity: Severity
    description: str


class Finding(BaseModel):
    id: str
    type: FindingType
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: BBox
    description: str
    sku: str | None = None
    needs_review: bool = False
    review_reason: str | None = None
    # Supervisor review: "proposed" is untouched model output; the rest are human decisions, never model output.
    review_status: ReviewStatus = "proposed"
    original: FindingOriginal | None = None
    review_note: str | None = None
    object_id: str | None = None


class DetectedObject(BaseModel):
    id: str
    label: Literal["box", "walkway"]
    bbox: BBox
    confidence: float = Field(ge=0.0, le=1.0)
    mask_svg: str = Field(description="SVG path in full-image pixel coordinates.")


class DamageAssessment(BaseModel):
    damaged: bool
    damage_type: DamageType
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class ImageSize(BaseModel):
    width: int
    height: int


class ScanResult(BaseModel):
    scan_id: str
    image_size: ImageSize
    objects: list[DetectedObject]
    findings: list[Finding]
    timings_ms: dict[str, int]
    privacy: Literal["off", "blurred", "failed"] = Field(
        default="off", description="Whether people were blurred before boxes were sent for inspection."
    )
    uninspected_boxes: list[str] = Field(default_factory=list, description="Boxes the inspection returned no assessment for.")
    replay: bool = Field(default=False, description="True when served from saved demo results instead of a live analysis.")
