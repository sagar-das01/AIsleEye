import logging
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter

logger = logging.getLogger(__name__)

MIN_BLUR_RADIUS = 12
BLUR_RADIUS_RATIO = 0.02


@dataclass(frozen=True)
class PrivacyResult:
    image: Image.Image | None
    blurred: bool
    crops_allowed: bool
    failure: str | None = None


def blur_regions(image: Image.Image, masks: list[np.ndarray]) -> Image.Image:
    """Return a copy of the image with only the masked pixels blurred."""
    out = image.convert("RGB").copy()
    if not masks:
        return out
    shape = (out.height, out.width)
    union = np.zeros(shape, dtype=bool)
    for mask in masks:
        if mask.shape != shape:
            raise ValueError(f"mask shape {mask.shape} does not match the image {shape}")
        union |= mask.astype(bool)
    if not union.any():
        return out
    radius = max(MIN_BLUR_RADIUS, round(max(shape) * BLUR_RADIUS_RATIO))
    blurred = out.filter(ImageFilter.GaussianBlur(radius))
    return Image.composite(blurred, out, Image.fromarray(union.astype(np.uint8) * 255, mode="L"))


def apply_privacy(image: Image.Image, find_people: Callable[[Image.Image], list[np.ndarray]]) -> PrivacyResult:
    """Blur every person found. If the people pass fails, nothing is blurred and no crops may be kept."""
    try:
        masks = find_people(image)
        return PrivacyResult(image=blur_regions(image, masks), blurred=True, crops_allowed=True)
    except Exception as exc:
        logger.warning("People pass failed (%s); crops are disabled for this scan", type(exc).__name__)
        return PrivacyResult(image=None, blurred=False, crops_allowed=False, failure=type(exc).__name__)
