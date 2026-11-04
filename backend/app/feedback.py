from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

from app.config import DATA_DIR
from app.sanitize import sanitize_text
from app.schemas import FindingType
from app.storage import append_jsonl

FEEDBACK_PATH: Path = DATA_DIR / "feedback.jsonl"
ACTIONS = ("reject", "correct")
MAX_REASON_CHARS = 500
MAX_ID_CHARS = 64


def record_feedback(scan_id: str, finding_id: str, action: str, reason: str, corrected_type: str | None = None) -> dict:
    """Append one supervisor verdict. It is evaluation data only and changes no setting, prompt or permission."""
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {list(ACTIONS)}")
    if action == "correct" and corrected_type not in get_args(FindingType):
        raise ValueError(f"corrected_type must be one of {list(get_args(FindingType))}")
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "scan_id": scan_id,
        "finding_id": sanitize_text(finding_id, MAX_ID_CHARS),
        "action": action,
        "corrected_type": corrected_type if action == "correct" else None,
        "reason": sanitize_text(reason or "", MAX_REASON_CHARS),
    }
    append_jsonl(FEEDBACK_PATH, entry)
    return entry
