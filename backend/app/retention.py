import logging
import re
import time
from pathlib import Path

from app.pipeline import scan
from app.replay import DEMO_DIR

logger = logging.getLogger(__name__)

DAY = 86400
_SCAN_FILE = re.compile(r"^[0-9a-f]{16}\.json$")


def _is_pinned_store(directory: Path) -> bool:
    return directory.resolve() == DEMO_DIR.resolve()


def purge_expired(cache_dir: Path, ttl_days: float, *, now: float | None = None) -> list[str]:
    """Delete cached scan files older than the TTL and return their ids. Never touches the pinned demo store."""
    if ttl_days <= 0:
        raise ValueError("ttl_days must be positive")
    cache_dir = Path(cache_dir)
    if not cache_dir.is_dir() or _is_pinned_store(cache_dir):
        return []
    now = time.time() if now is None else now
    removed = []
    for path in cache_dir.iterdir():
        if not _SCAN_FILE.match(path.name) or path.is_symlink() or not path.is_file():
            continue
        if now - path.lstat().st_mtime > ttl_days * DAY:
            path.unlink(missing_ok=True)
            removed.append(path.stem)
    if removed:
        logger.info("Purged %d expired cached scan(s)", len(removed))
    return removed


def delete_scan(scan_id: str) -> bool:
    """Remove one cached scan; the pinned demo store is out of reach by design."""
    cache_dir = scan.CACHE_DIR
    path = cache_dir / f"{scan_id}.json"
    if _is_pinned_store(cache_dir) or not _SCAN_FILE.match(path.name) or path.is_symlink() or not path.is_file():
        return False
    path.unlink()
    return True
