import json
import re
from pathlib import Path

from pydantic import ValidationError

from app.config import DATA_DIR, ROOT_DIR, get_settings
from app.pipeline.scan import load_cached, scan_id_for
from app.schemas import ScanResult

# Pinned demo results are outside the cache, so neither the TTL purge nor DELETE /scan ever touches them.
DEMO_DIR: Path = DATA_DIR / "demo"
DEMO_IMAGES_DIR: Path = ROOT_DIR / "demo_images"
_SCAN_ID = re.compile(r"^[0-9a-f]{16}$")
_DEMO_NAME = re.compile(r"^[a-z0-9_-]{1,40}$")


def load_pinned(scan_id: str) -> ScanResult | None:
    if not _SCAN_ID.fullmatch(scan_id):
        return None
    path = DEMO_DIR / f"{scan_id}.json"
    try:
        return ScanResult.model_validate_json(path.read_text())
    except (OSError, ValidationError, UnicodeDecodeError):
        return None


def find_scan(scan_id: str) -> ScanResult | None:
    """The saved result for a scan id; anything that came from the pinned store is flagged as a replay."""
    if get_settings().demo_mode == "replay":
        found = load_pinned(scan_id) or load_cached(scan_id)
        return found.model_copy(update={"replay": True}) if found else None
    cached = load_cached(scan_id)
    if cached is not None:
        return cached
    pinned = load_pinned(scan_id)
    return pinned.model_copy(update={"replay": True}) if pinned else None


def load_manifest() -> list[dict]:
    try:
        entries = json.loads((DEMO_IMAGES_DIR / "manifest.json").read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return [e for e in entries if isinstance(e, dict) and _DEMO_NAME.fullmatch(str(e.get("id", "")))]


def demo_image_path(name: str) -> Path | None:
    if not _DEMO_NAME.fullmatch(name):
        return None
    path = DEMO_IMAGES_DIR / f"{name}.jpg"
    return path if path.is_file() else None


def demo_entries() -> list[dict]:
    """Manifest entries with the scan id of the committed image, so the UI can tell which have saved results."""
    out = []
    for entry in load_manifest():
        path = demo_image_path(entry["id"])
        if path is None:
            continue
        scan_id = scan_id_for(path.read_bytes())
        out.append({**entry, "scan_id": scan_id, "image_url": f"/demo/{entry['id']}.jpg", "pinned": load_pinned(scan_id) is not None})
    return out
