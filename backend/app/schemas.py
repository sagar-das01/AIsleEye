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


class Finding(BaseModel):
    id: str
    type: FindingType
    severity: Severity
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: BBox
    description: str
    sku: str | None = None


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
