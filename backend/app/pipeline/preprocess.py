import io

from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_MIME_TYPES = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
MAX_PIXELS = 40_000_000
# Larger photos add model latency and mask-decoding cost without improving box detection.
MAX_SIDE = 2048
JPEG_QUALITY = 92


class InvalidImageError(ValueError):
    pass


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
    # Re-encoding from bare pixels drops EXIF (GPS, device), comments and any other metadata Pillow would carry over.
    buf = io.BytesIO()
    Image.frombytes("RGB", img.size, img.convert("RGB").tobytes()).save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()
