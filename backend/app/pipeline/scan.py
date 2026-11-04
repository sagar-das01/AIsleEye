import hashlib
import io
import os
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from app.clients import bytes_to_data_url
from app.config import DATA_DIR
from app.pipeline.hazards import detect_blocked_walkways
from app.pipeline.inspect import damage_findings, inspect_boxes
from app.pipeline.segment import segment_all
from app.schemas import DetectedObject, ImageSize, ScanResult

CACHE_DIR: Path = DATA_DIR / "cache"
ALLOWED_MIME_TYPES = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
MAX_PIXELS = 40_000_000
# Larger photos add model latency and mask-decoding cost without improving box detection.
MAX_SIDE = 2048
JPEG_QUALITY = 92


class InvalidImageError(ValueError):
    pass


def scan_id_for(image_bytes: bytes) -> str:
    return hashlib.sha256(image_bytes).hexdigest()[:16]


def load_image(image_bytes: bytes, mime: str) -> Image.Image:
    expected = ALLOWED_MIME_TYPES.get(mime)
    if expected is None:
        raise InvalidImageError("Unsupported image type.")
    try:
        img = Image.open(io.BytesIO(image_bytes))
        if img.format != expected:
            raise InvalidImageError("Image content does not match its declared type.")
        if img.width * img.height > MAX_PIXELS:
            raise InvalidImageError("Image is too large.")
        img.load()
        img = ImageOps.exif_transpose(img).convert("RGB")
    except InvalidImageError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError) as exc:
        raise InvalidImageError("Image could not be decoded.") from exc
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    return img


def encode_jpeg(img: Image.Image) -> bytes:
    # Re-encoding from pixels drops EXIF (GPS, device) and any other metadata.
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


def _cache_path(scan_id: str) -> Path:
    return CACHE_DIR / f"{scan_id}.json"


def load_cached(scan_id: str) -> ScanResult | None:
    path = _cache_path(scan_id)
    if not path.is_file():
        return None
    return ScanResult.model_validate_json(path.read_text())


def _save_cached(result: ScanResult) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        f.write(result.model_dump_json())
    os.replace(tmp, _cache_path(result.scan_id))


def _ms(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)


async def run_scan(image_bytes: bytes, mime: str) -> ScanResult:
    scan_id = scan_id_for(image_bytes)
    img = load_image(image_bytes, mime)
    cached = load_cached(scan_id)
    if cached is not None:
        return cached

    width, height = img.size
    data_url = bytes_to_data_url(encode_jpeg(img), "image/jpeg")
    timings: dict[str, int] = {}

    t = time.perf_counter()
    segments = await segment_all(data_url, width=width, height=height)
    timings["segment"] = _ms(t)
    boxes, walkways = segments["box"], segments["walkway"]

    t = time.perf_counter()
    bboxes = [b.bbox for b in boxes]
    assessments = await inspect_boxes(img, bboxes)
    damage = damage_findings([b.id for b in boxes], bboxes, assessments)
    timings["inspect"] = _ms(t)

    t = time.perf_counter()
    hazards = detect_blocked_walkways(boxes, walkways)
    timings["hazards"] = _ms(t)

    objects = [
        DetectedObject(id=s.id, label=label, bbox=s.bbox, confidence=s.confidence, mask_svg=s.svg)
        for label, segs in (("box", boxes), ("walkway", walkways))
        for s in segs
    ]
    result = ScanResult(
        scan_id=scan_id,
        image_size=ImageSize(width=width, height=height),
        objects=objects,
        findings=damage + hazards,
        timings_ms=timings,
    )
    _save_cached(result)
    return result
