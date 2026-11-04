import asyncio
import io
import json
import logging

from PIL import Image
from pydantic import BaseModel, ValidationError

from app import observability
from app.clients import bytes_to_data_url, get_async_client, reasoning_kwargs
from app.config import BACKEND_DIR, get_settings
from app.schemas import BBox, DamageAssessment, DamageType, Finding, Severity

logger = logging.getLogger(__name__)

PADDING_RATIO = 0.10
MAX_IMAGES_PER_REQUEST = 50
CROP_JPEG_QUALITY = 90
MAX_REASON_CHARS = 300

DAMAGE_TYPES = ["tear", "crushed_corner", "dent", "water_damage", "open_flap", "none"]

INSPECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "assessments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "damaged": {"type": "boolean"},
                    "damage_type": {"type": "string", "enum": DAMAGE_TYPES},
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "damaged", "damage_type", "severity", "confidence", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["assessments"],
    "additionalProperties": False,
}

PROMPT_DIR = BACKEND_DIR / "prompts"


def prompt_version() -> str:
    return f"inspect-{get_settings().inspect_prompt}"


def load_prompt() -> str:
    return (PROMPT_DIR / f"inspect_{get_settings().inspect_prompt}.txt").read_text(encoding="utf-8").strip()


class _RawAssessment(BaseModel):
    index: int
    damaged: bool
    damage_type: DamageType
    severity: Severity
    confidence: float
    reason: str


def crop_bounds(bbox: BBox, width: int, height: int, pad: float = PADDING_RATIO) -> tuple[int, int, int, int]:
    px = round((bbox.x2 - bbox.x1) * pad)
    py = round((bbox.y2 - bbox.y1) * pad)
    return max(0, bbox.x1 - px), max(0, bbox.y1 - py), min(width, bbox.x2 + px), min(height, bbox.y2 + py)


def crop_to_data_url(image: Image.Image, bbox: BBox) -> str:
    crop = image.crop(crop_bounds(bbox, image.width, image.height))
    buf = io.BytesIO()
    crop.convert("RGB").save(buf, format="JPEG", quality=CROP_JPEG_QUALITY)
    return bytes_to_data_url(buf.getvalue(), "image/jpeg")


def parse_assessments(output_text: str, count: int) -> list[DamageAssessment | None]:
    results: list[DamageAssessment | None] = [None] * count
    try:
        items = json.loads(output_text)["assessments"]
    except (json.JSONDecodeError, KeyError, TypeError):
        logger.warning("Inspection output was not valid JSON; boxes left unassessed")
        return results
    if not isinstance(items, list):
        return results
    for item in items:
        try:
            raw = _RawAssessment.model_validate(item)
        except ValidationError:
            continue
        if not 0 <= raw.index < count or results[raw.index] is not None:
            continue
        results[raw.index] = DamageAssessment(
            damaged=raw.damaged,
            damage_type=raw.damage_type,
            severity=raw.severity,
            confidence=min(1.0, max(0.0, raw.confidence)),
            reason=raw.reason[:MAX_REASON_CHARS],
        )
    return results


async def _inspect_chunk(crop_urls: list[str]) -> list[DamageAssessment | None]:
    content: list[dict] = [{"type": "input_text", "text": load_prompt()}]
    for i, url in enumerate(crop_urls):
        content.append({"type": "input_text", "text": f"Image {i}:"})
        content.append({"type": "input_image", "image_url": url})
    with observability.span(
        "inspect_boxes", as_type="generation", model=get_settings().muse_model,
        input={"images": len(crop_urls), "image_chars": sum(map(len, crop_urls))}, prompt_version=prompt_version(),
    ) as obs:
        response = await get_async_client().responses.create(
            model=get_settings().muse_model,
            input=[{"type": "message", "role": "user", "content": content}],
            text={"format": {"type": "json_schema", "name": "BoxInspection", "schema": INSPECTION_SCHEMA, "strict": True}},
            **reasoning_kwargs(),
        )
        assessed = parse_assessments(response.output_text or "", len(crop_urls))
        observability.update(obs, output={"assessed": sum(a is not None for a in assessed), "of": len(crop_urls)})
    return assessed


async def inspect_boxes(image: Image.Image, bboxes: list[BBox]) -> list[DamageAssessment | None]:
    if not bboxes:
        return []
    urls = await asyncio.to_thread(lambda: [crop_to_data_url(image, b) for b in bboxes])
    chunks = [urls[i : i + MAX_IMAGES_PER_REQUEST] for i in range(0, len(urls), MAX_IMAGES_PER_REQUEST)]
    results = await asyncio.gather(*(_inspect_chunk(c) for c in chunks))
    return [a for chunk in results for a in chunk]


def damage_findings(box_ids: list[str], bboxes: list[BBox], assessments: list[DamageAssessment | None]) -> list[Finding]:
    findings = []
    for box_id, bbox, a in zip(box_ids, bboxes, assessments, strict=True):
        if a is None or not a.damaged:
            continue
        label = a.damage_type.replace("_", " ")
        findings.append(
            Finding(
                id=f"dmg_{box_id}",
                type="box_damage",
                severity=a.severity,
                confidence=a.confidence,
                bbox=bbox,
                description=f"Box {box_id}: {label}. {a.reason}".strip(),
            )
        )
    return findings
