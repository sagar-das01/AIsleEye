"""Spec for app/pipeline/preprocess.py (Phase 5 MUST). Skipped until the module exists.

The plan says the logic already in scan.py must be *extracted and reused*. Interface (same names as today's scan.py):
    InvalidImageError, ALLOWED_MIME_TYPES, MAX_PIXELS, MAX_SIDE
    load_image(bytes, mime) -> PIL.Image (RGB, EXIF-oriented, <= MAX_SIDE)
    encode_jpeg(image) -> bytes (no metadata)
"""

import ast
import io
from pathlib import Path

import pytest
from PIL import Image
from spec_support import need

pre = need("app.pipeline.preprocess", "load_image", "encode_jpeg", "InvalidImageError")


def png(size=(40, 30), mode="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, 0).save(buf, format="PNG")
    return buf.getvalue()


def test_scan_module_reuses_instead_of_duplicating():
    scan_path = Path(__file__).resolve().parents[1] / "app" / "pipeline" / "scan.py"
    if not scan_path.exists():
        pytest.skip("scan.py not present")
    defined = {n.name for n in ast.walk(ast.parse(scan_path.read_text())) if isinstance(n, ast.FunctionDef)}
    assert not {"load_image", "encode_jpeg"} & defined, "scan.py must import these from preprocess"


@pytest.mark.parametrize("mime", ["image/gif", "text/plain", ""])
def test_unsupported_mime_is_rejected(mime):
    with pytest.raises(pre.InvalidImageError):
        pre.load_image(png(), mime)


@pytest.mark.parametrize("data", [b"", b"x", png()[:20], b"\x89PNG\r\n\x1a\n" + b"\x00" * 50])
def test_corrupt_bytes_raise_invalid_image_error_only(data):
    with pytest.raises(pre.InvalidImageError):
        pre.load_image(data, "image/png")


def test_declared_type_must_match_content():
    with pytest.raises(pre.InvalidImageError):
        pre.load_image(png(), "image/jpeg")


def test_oversized_pixel_count_rejected_before_decoding(monkeypatch):
    monkeypatch.setattr(pre, "MAX_PIXELS", 100)
    with pytest.raises(pre.InvalidImageError):
        pre.load_image(png((20, 20)), "image/png")


def test_downscale_keeps_aspect_ratio_and_never_upscales(monkeypatch):
    monkeypatch.setattr(pre, "MAX_SIDE", 40)
    assert pre.load_image(png((80, 20)), "image/png").size == (40, 10)
    assert pre.load_image(png((30, 10)), "image/png").size == (30, 10)


@pytest.mark.parametrize("orientation", range(1, 9))
def test_exif_orientation_is_applied_for_every_value(orientation):
    exif = Image.Exif()
    exif[0x0112] = orientation
    buf = io.BytesIO()
    Image.new("RGB", (40, 20)).save(buf, format="JPEG", exif=exif)
    img = pre.load_image(buf.getvalue(), "image/jpeg")
    assert img.size == ((20, 40) if orientation in (5, 6, 7, 8) else (40, 20))


def test_reencoded_output_has_no_metadata_at_all():
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"
    exif[0x8825] = {1: "N", 2: (52.0, 31.0, 0.0)}  # GPS block
    buf = io.BytesIO()
    Image.new("RGB", (40, 20)).save(buf, format="JPEG", exif=exif, comment=b"private note")
    out = pre.encode_jpeg(pre.load_image(buf.getvalue(), "image/jpeg"))
    assert b"SecretCam" not in out and b"private note" not in out and b"Exif" not in out
    assert Image.open(io.BytesIO(out)).format == "JPEG"


def test_same_input_gives_identical_output_for_replay_and_hashing():
    img = pre.load_image(png(), "image/png")
    assert pre.encode_jpeg(img) == pre.encode_jpeg(img)
