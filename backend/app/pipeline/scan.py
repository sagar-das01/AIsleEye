import asyncio
import hashlib
import logging
import os
import tempfile
import time
from pathlib import Path

from pydantic import ValidationError

from app.clients import bytes_to_data_url
from app.config import DATA_DIR, get_settings
from app.pipeline.hazards import detect_blocked_walkways
from app.pipeline.inspect import damage_findings, inspect_boxes
from app.pipeline.preprocess import ALLOWED_MIME_TYPES, InvalidImageError, encode_jpeg, load_image
from app.pipeline.privacy import apply_privacy
from app.pipeline.segment import PERSON_CONCEPT, segment, segment_all
from app.review import flag_needs_review
from app.schemas import DetectedObject, ImageSize, ScanResult

logger = logging.getLogger(__name__)

CACHE_DIR: Path = DATA_DIR / "cache"


__all__ = ["ALLOWED_MIME_TYPES", "CACHE_DIR", "InvalidImageError", "encode_jpeg", "load_cached", "load_image", "run_scan", "scan_id_for"]


def scan_id_for(image_bytes: bytes) -> str:
    return hashlib.sha256(image_bytes).hexdigest()[:16]


def _cache_path(scan_id: str) -> Path:
    return CACHE_DIR / f"{scan_id}.json"


def load_cached(scan_id: str) -> ScanResult | None:
    path = _cache_path(scan_id)
    if not path.is_file():
        return None
    try:
        return ScanResult.model_validate_json(path.read_text())
    except (ValidationError, OSError, UnicodeDecodeError):
        # A torn or old-schema file must not make the photo unscannable; the caller rescans and overwrites it.
        logger.warning("Ignoring unreadable cache entry %s", path.name)
        return None


def _save_cached(result: ScanResult) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        f.write(result.model_dump_json())
    os.replace(tmp, _cache_path(result.scan_id))


def _ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


def _people_finder(outcome):
    def find(_image):
        if isinstance(outcome, Exception):
            raise outcome
        return [s.mask for s in outcome]

    return find


async def run_scan(image_bytes: bytes, mime: str) -> ScanResult:
    scan_id = scan_id_for(image_bytes)
    img = load_image(image_bytes, mime)
    cached = load_cached(scan_id)
    if cached is not None:
        return cached

    settings = get_settings()
    width, height = img.size
    data_url = bytes_to_data_url(encode_jpeg(img), "image/jpeg")
    timings: dict[str, int] = {}

    t = time.perf_counter()
    # The people pass runs beside the detection passes, so detection sees the original and privacy adds no latency.
    people_pass = (
        [segment(data_url, PERSON_CONCEPT, width=width, height=height, id_prefix="person")]
        if settings.privacy_blur
        else []
    )
    segments, *people = await asyncio.gather(
        segment_all(data_url, width=width, height=height), *people_pass, return_exceptions=True
    )
    if isinstance(segments, Exception):
        raise segments
    timings["segment"] = _ms(t)
    boxes, walkways = segments["box"], segments["walkway"]
    # "yellow line" also matches orange rack beams (confidence ~0.5, lanes ~0.85): weak matches are not walkways.
    walkways = [w for w in walkways if w.confidence >= settings.walkway_min_confidence]

    privacy = "off"
    working = img
    if people:
        outcome = apply_privacy(img, _people_finder(people[0]))
        privacy = "blurred" if outcome.blurred else "failed"
        working = outcome.image if outcome.image is not None else img

    t = time.perf_counter()
    bboxes = [b.bbox for b in boxes]
    assessments = await inspect_boxes(working, bboxes)
    damage = damage_findings([b.id for b in boxes], bboxes, assessments)
    timings["inspect"] = _ms(t)

    t = time.perf_counter()
    hazards = detect_blocked_walkways(boxes, walkways)
    timings["hazards"] = _ms(t)

    threshold = settings.review_confidence
    # A damage finding on something the segmenter itself was unsure is a box is a disagreement between the two models.
    disagreements = {
        f"dmg_{b.id}": f"Segmentation was unsure this is a box ({b.confidence:.2f}); check it in person."
        for b in boxes
        if b.confidence < threshold
    }
    findings = flag_needs_review(damage + hazards, threshold=threshold, disagreements=disagreements)

    objects = [
        DetectedObject(id=s.id, label=label, bbox=s.bbox, confidence=s.confidence, mask_svg=s.svg)
        for label, segs in (("box", boxes), ("walkway", walkways))
        for s in segs
    ]
    result = ScanResult(
        scan_id=scan_id,
        image_size=ImageSize(width=width, height=height),
        objects=objects,
        findings=findings,
        timings_ms=timings,
        privacy=privacy,
        uninspected_boxes=[b.id for b, a in zip(boxes, assessments, strict=True) if a is None],
    )
    _save_cached(result)
    return result
