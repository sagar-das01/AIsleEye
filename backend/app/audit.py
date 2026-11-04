import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import DATA_DIR
from app.storage import append_jsonl, read_jsonl

AUDIT_PATH: Path = DATA_DIR / "audit_log.jsonl"
OUTCOMES = {"executed", "rejected", "invalid", "failed"}

_SECRET_KEY = re.compile(r"key|token|secret|password|authorization", re.I)
_SECRET_VALUE = re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]{8,}|\bgh[pousr]_[A-Za-z0-9]{20,}")
_LONG_BLOB = re.compile(r"^[A-Za-z0-9+/=_-]{200,}$")
MAX_VALUE_CHARS = 2000


def redact(value: Any) -> Any:
    """Drop image data, secrets and oversized payloads so the log can never leak them."""
    if isinstance(value, dict):
        return {k: "[redacted]" if _SECRET_KEY.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, bytes):
        return "[bytes removed]"
    if isinstance(value, str):
        if "data:image" in value:
            return "[image data removed]"
        if _LONG_BLOB.match(value):
            return "[long payload removed]"
        value = _SECRET_VALUE.sub("[redacted]", value)
        return value[:MAX_VALUE_CHARS]
    return value


def record(scan_id: str, finding_id: Any, tool: str, arguments: dict, outcome: str, reason: str | None = None) -> dict:
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {sorted(OUTCOMES)}")
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "scan_id": scan_id,
        "finding_id": finding_id if isinstance(finding_id, str) else None,
        "tool": redact(str(tool)),
        "arguments": redact(arguments),
        "outcome": outcome,
    }
    if reason:
        entry["reason"] = redact(reason)
    append_jsonl(AUDIT_PATH, entry)
    return entry


def read_all() -> list[dict]:
    return read_jsonl(AUDIT_PATH)
