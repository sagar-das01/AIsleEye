from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

from app.config import DATA_DIR
from app.sanitize import sanitize_text
from app.schemas import FindingType, Severity
from app.storage import append_jsonl

FEEDBACK_PATH: Path = DATA_DIR / "feedback.jsonl"
# "missed": a supervisor added a finding the model did not report (a recall miss, not a precision one).
ACTIONS = ("reject", "correct", "missed")
MAX_REASON_CHARS = 500
MAX_ID_CHARS = 64


def record_feedback(
    scan_id: str, finding_id: str, action: str, reason: str, corrected_type: str | None = None,
    *, corrected_severity: str | None = None,
) -> dict:
    """Append one supervisor verdict. It is evaluation data only and changes no setting, prompt or permission."""
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {list(ACTIONS)}")
    if action in ("correct", "missed") and corrected_type not in get_args(FindingType):
        raise ValueError(f"corrected_type must be one of {list(get_args(FindingType))}")
    if corrected_severity is not None and corrected_severity not in get_args(Severity):
        raise ValueError(f"corrected_severity must be one of {list(get_args(Severity))}")
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "scan_id": scan_id,
        "finding_id": sanitize_text(finding_id, MAX_ID_CHARS),
        "action": action,
        "corrected_type": corrected_type if action in ("correct", "missed") else None,
        "corrected_severity": corrected_severity if action in ("correct", "missed") else None,
        "reason": sanitize_text(reason or "", MAX_REASON_CHARS),
    }
    append_jsonl(FEEDBACK_PATH, entry)
    return entry
