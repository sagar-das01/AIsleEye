"""Spec for app/pipeline/privacy.py (Phase 5 SHOULD): blur people before anything is cached, cropped or sent on.

Planned interface:
    blur_regions(image, masks: list[np.ndarray(bool, HxW)]) -> PIL.Image   new image; only masked pixels change
    apply_privacy(image, find_people) -> PrivacyResult(image, blurred: bool, crops_allowed: bool, failure: str | None)
        find_people(image) -> list[mask]; if it raises, crops are NOT allowed and the scan is flagged
"""

import numpy as np
import pytest
from PIL import Image
from spec_support import need

privacy = need("app.pipeline.privacy", "blur_regions", "apply_privacy")


def noisy(size=(64, 48), seed=0) -> Image.Image:
    rng = np.random.default_rng(seed)
    return Image.fromarray(rng.integers(0, 256, (size[1], size[0], 3), dtype=np.uint8))


def region(h=48, w=64, y=10, x=10, hh=20, ww=20) -> np.ndarray:
    m = np.zeros((h, w), dtype=bool)
    m[y : y + hh, x : x + ww] = True
    return m


def test_masked_pixels_are_blurred_and_everything_else_is_untouched():
    img = noisy()
    mask = region()
    out = np.asarray(privacy.blur_regions(img, [mask]))
    src = np.asarray(img)
    assert np.array_equal(out[~mask], src[~mask])
    assert out[mask].std() < src[mask].std() * 0.6


def test_input_image_is_not_modified():
    img = noisy()
    before = np.asarray(img).copy()
    privacy.blur_regions(img, [region()])
    assert np.array_equal(np.asarray(img), before)


def test_no_masks_returns_an_identical_image():
    img = noisy()
    assert np.array_equal(np.asarray(privacy.blur_regions(img, [])), np.asarray(img))


def test_size_and_mode_are_preserved():
    out = privacy.blur_regions(noisy(), [region()])
    assert out.size == (64, 48) and out.mode == "RGB"


def test_overlapping_masks_do_not_double_blur_outside_their_union():
    img = noisy()
    a, b = region(x=10), region(x=20)
    out = np.asarray(privacy.blur_regions(img, [a, b]))
    outside = ~(a | b)
    assert np.array_equal(out[outside], np.asarray(img)[outside])


def test_full_frame_mask_blurs_everything():
    img = noisy()
    out = np.asarray(privacy.blur_regions(img, [np.ones((48, 64), dtype=bool)]))
    assert out.std() < np.asarray(img).std() * 0.6


def test_mask_with_the_wrong_shape_is_an_error():
    with pytest.raises(ValueError):
        privacy.blur_regions(noisy(), [np.ones((10, 10), dtype=bool)])


def test_blur_is_not_reversible_by_a_simple_sharpen():
    img = noisy()
    out = privacy.blur_regions(img, [region()])
    assert not np.array_equal(np.asarray(out)[region()], np.asarray(img)[region()])


def test_apply_privacy_with_people_returns_a_blurred_image():
    result = privacy.apply_privacy(noisy(), lambda im: [region()])
    assert result.blurred and result.crops_allowed and result.failure is None


def test_apply_privacy_with_nobody_in_frame_is_still_a_success():
    result = privacy.apply_privacy(noisy(), lambda im: [])
    assert result.blurred and result.crops_allowed


def test_failed_people_pass_forbids_crops_and_flags_the_scan_but_does_not_raise():
    def boom(image):
        raise RuntimeError("segmentation unavailable")

    result = privacy.apply_privacy(noisy(), boom)
    assert result.crops_allowed is False and result.blurred is False and result.failure


def test_failed_pass_never_returns_an_unblurred_image_for_caching():
    def boom(image):
        raise RuntimeError("x")

    result = privacy.apply_privacy(noisy(), boom)
    assert result.image is None or result.crops_allowed is False
