import asyncio
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass

import numpy as np
from meta_sam_parser import (
    ImageSegmentationResult,
    SegmentationBoxRecord,
    SegmentationMaskRecord,
    decode_mask_to_raster,
    decode_mask_to_svg_path,
    image_segmentation_format,
    parse_responses_stream,
)

from app.clients import get_async_client
from app.config import get_settings
from app.schemas import BBox

logger = logging.getLogger(__name__)

# Scan label -> SAM concept. SAM accepts exactly one noun phrase per request.
CONCEPTS: dict[str, str] = {"box": "cardboard box", "walkway": "floor tape"}
SAM_METADATA = {"mask_encoding": "one_bit", "include_confidence": "true"}

_SVG_POINT = re.compile(r"([ML])(-?\d+(?:\.\d+)?) (-?\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class Segment:
    id: str
    bbox: BBox
    confidence: float
    mask: np.ndarray  # bool, shape (image height, image width)
    svg: str


async def _completed_text_events(text: str) -> AsyncIterator[dict]:
    # Minimal Responses event sequence the parser accepts for one finished output_text lane.
    yield {"type": "response.output_text.done", "item_id": "sam_output", "output_index": 0, "content_index": 0, "text": text}
    yield {"type": "response.completed"}


async def parse_sam_output(text: str) -> ImageSegmentationResult:
    return await parse_responses_stream(_completed_text_events(text), image_segmentation_format()).final_result()


def _fit_raster(raster: np.ndarray, height: int, width: int) -> np.ndarray:
    if raster.shape == (height, width):
        return raster
    rows = np.arange(height) * raster.shape[0] // height
    cols = np.arange(width) * raster.shape[1] // width
    return raster[np.ix_(rows, cols)]


def _format_number(value: float) -> str:
    return f"{round(value, 2):g}"


def _svg_to_image_coords(path: str, left: int, top: int, sx: float, sy: float) -> str:
    # Library paths use mask-local pixel centres (pixel 0 spans -0.5..0.5), hence the +0.5.
    def repl(m: re.Match) -> str:
        x = left + (float(m.group(2)) + 0.5) * sx
        y = top + (float(m.group(3)) + 0.5) * sy
        return f"{m.group(1)}{_format_number(x)} {_format_number(y)}"

    return _SVG_POINT.sub(repl, path)


def records_to_segments(
    result: ImageSegmentationResult, *, width: int, height: int, id_prefix: str
) -> list[Segment]:
    for diag in result.diagnostics:
        if diag.severity == "error":
            logger.warning("SAM output line %d skipped: %s", diag.line, diag.code)

    boxes = {r.object_id: r for r in result.records if isinstance(r, SegmentationBoxRecord)}
    segments: list[Segment] = []
    for rec in result.records:
        if not isinstance(rec, SegmentationMaskRecord):
            continue
        box = boxes.get(rec.object_id)
        left, top = int(rec.bounds.left), int(rec.bounds.top)
        right, bottom = int(rec.bounds.right), int(rec.bounds.bottom)
        box_w, box_h = right - left, bottom - top
        mh, mw = rec.mask.height, rec.mask.width

        raster = np.frombuffer(decode_mask_to_raster(rec.mask), dtype=np.uint8).reshape(mh, mw).astype(bool)
        raster = _fit_raster(raster, box_h, box_w)

        full = np.zeros((height, width), dtype=bool)
        x0, y0 = max(left, 0), max(top, 0)
        x1, y1 = min(right, width), min(bottom, height)
        if x1 <= x0 or y1 <= y0:
            continue
        full[y0:y1, x0:x1] = raster[y0 - top : y1 - top, x0 - left : x1 - left]

        conf = box.confidence if box and box.confidence is not None else rec.confidence
        svg = _svg_to_image_coords(decode_mask_to_svg_path(rec.mask), left, top, box_w / mw, box_h / mh)
        segments.append(
            Segment(
                id=f"{id_prefix}_{rec.object_id}",
                bbox=BBox(x1=x0, y1=y0, x2=x1, y2=y1),
                confidence=float(conf) if conf is not None else 0.0,
                mask=full,
                svg=svg,
            )
        )
    return segments


async def segment(image_data_url: str, concept: str, *, width: int, height: int, id_prefix: str = "obj") -> list[Segment]:
    response = await get_async_client().responses.create(
        model=get_settings().sam_model,
        input=[{
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": concept},
                {"type": "input_image", "image_url": image_data_url},
            ],
        }],
        metadata=SAM_METADATA,
    )
    result = await parse_sam_output(response.output_text or "")
    # Mask decoding is pure Python; keep it off the event loop.
    return await asyncio.to_thread(records_to_segments, result, width=width, height=height, id_prefix=id_prefix)


async def segment_all(image_data_url: str, *, width: int, height: int) -> dict[str, list[Segment]]:
    labels = list(CONCEPTS)
    results = await asyncio.gather(
        *(segment(image_data_url, CONCEPTS[label], width=width, height=height, id_prefix=label) for label in labels)
    )
    return dict(zip(labels, results, strict=True))
