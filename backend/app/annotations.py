"""Supervisor review of model findings, stored apart from the inference output (cache and pinned demo store stay untouched).

A scan's reviewed view is the model's findings with these decisions applied: corrected fields replace the model's values
(the originals are kept alongside), supervisor-added findings cover boxes the model missed, and rejected findings are
marked. Approval dispatches the reviewed view, so a correction is what the agent and the tickets see. Corrections are
human annotations for this dispatch and for evaluation; they are never ground truth or model training data.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.sanitize import sanitize_text
from app.schemas import Finding, FindingOriginal, ScanResult
from app.storage import read_json, update_json

DESCRIPTION_CHARS, REASON_CHARS = 500, 300


class ReviewError(Exception):
    status_code = 422

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class Locked(ReviewError):
    status_code = 409


class NotFound(ReviewError):
    status_code = 404


def _path(scan_id: str) -> Path:
    from app.agent import tools  # late: tests point tools.DATA_DIR at a temporary directory

    return tools.DATA_DIR / "reviews" / f"{scan_id}.json"


def _empty() -> dict:
    return {"corrections": {}, "added": {}, "rejected": {}, "dispatched": []}


def _load(raw: Any) -> dict:
    data = raw if isinstance(raw, dict) else _empty()
    for key, default in _empty().items():
        data.setdefault(key, default)
    return data


def read(scan_id: str) -> dict:
    return _load(read_json(_path(scan_id)))


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _text(value: str, name: str, limit: int, *, required: bool) -> str | None:
    cleaned = sanitize_text(value or "", limit)
    if required and not cleaned:
        raise ReviewError(f"{name} is required.")
    return cleaned or None


def _object_of(finding: Finding) -> str:
    return finding.object_id or finding.id.split("_", 1)[-1]


def apply(scan: ScanResult) -> ScanResult:
    """The scan as reviewed by a supervisor; identical to the model output when nothing was reviewed."""
    state = read(scan.scan_id)
    if not (state["corrections"] or state["added"] or state["rejected"]):
        return scan
    findings: list[Finding] = []
    for f in scan.findings:
        fix = state["corrections"].get(f.id)
        if fix:
            f = f.model_copy(update={
                "type": fix["type"], "severity": fix["severity"], "description": fix["description"],
                "needs_review": False, "review_reason": None, "review_status": "corrected",
                "original": FindingOriginal(type=f.type, severity=f.severity, description=f.description),
                "review_note": fix.get("reason"),
            })
        if f.id in state["rejected"]:
            f = f.model_copy(update={"review_status": "rejected", "review_note": state["rejected"][f.id].get("reason")})
        findings.append(f)
    for added in state["added"].values():
        status = "rejected" if added["id"] in state["rejected"] else "added"
        findings.append(Finding.model_validate({**added["finding"], "review_status": status}))
    return scan.model_copy(update={"findings": findings})


def _check_open(state: dict, finding_id: str) -> None:
    if finding_id in state["dispatched"]:
        raise Locked("This finding was already dispatched, so it can no longer be changed.")


def correct(scan: ScanResult, finding_id: str, *, type: str, severity: str, description: str, reason: str, actor: str) -> dict:
    model = {f.id: f for f in scan.findings}
    description_clean = _text(description, "description", DESCRIPTION_CHARS, required=True)
    reason_clean = _text(reason, "reason", REASON_CHARS, required=False)

    def change(raw: Any) -> dict:
        state = _load(raw)
        _check_open(state, finding_id)
        if finding_id in state["added"]:
            entry = state["added"][finding_id]
            entry["finding"].update(type=type, severity=severity, description=description_clean)
            entry.update(updated_by=actor, updated_at=_now())
        elif finding_id in model:
            original = model[finding_id]
            if (original.type, original.severity, original.description) == (type, severity, description_clean):
                state["corrections"].pop(finding_id, None)  # back to what the model said: nothing left to correct
            else:
                state["corrections"][finding_id] = {
                    "type": type, "severity": severity, "description": description_clean, "reason": reason_clean,
                    "by": actor, "at": _now(),
                }
        else:
            raise NotFound("Finding not found for this scan.")
        state["rejected"].pop(finding_id, None)  # correcting a finding means it is real
        return state

    return update_json(_path(scan.scan_id), change, default=None)


def add(scan: ScanResult, object_id: str, *, type: str, severity: str, description: str, reason: str, actor: str) -> str:
    box = next((o for o in scan.objects if o.id == object_id and o.label == "box"), None)
    if box is None:
        raise NotFound("No detected box with that id in this scan.")
    description_clean = _text(description, "description", DESCRIPTION_CHARS, required=True)
    finding_id = f"added_{'dmg' if type == 'box_damage' else 'hz'}_{object_id}"

    def change(raw: Any) -> dict:
        state = _load(raw)
        reviewed = _types_after_review(scan, state)
        if any(_object_of(f) == object_id and f.type == type and f.review_status != "rejected" for f in reviewed):
            raise ReviewError("That box already has a finding of this type; edit it instead.")
        state["rejected"].pop(finding_id, None)
        state["added"][finding_id] = {
            "id": finding_id, "by": actor, "at": _now(), "reason": _text(reason, "reason", REASON_CHARS, required=False),
            "finding": {
                "id": finding_id, "object_id": object_id, "type": type, "severity": severity,
                # A person, not the model, made this finding; confidence 1.0 marks it as asserted, not inferred.
                "confidence": 1.0, "bbox": box.bbox.model_dump(), "description": description_clean,
            },
        }
        return state

    update_json(_path(scan.scan_id), change, default=None)
    return finding_id


def _types_after_review(scan: ScanResult, state: dict) -> list[Finding]:
    rejected = state["rejected"]
    out = [f.model_copy(update={"type": state["corrections"][f.id]["type"]}) if f.id in state["corrections"] else f for f in scan.findings]
    out += [Finding.model_validate(a["finding"]) for a in state["added"].values()]
    return [f.model_copy(update={"review_status": "rejected"}) if f.id in rejected else f for f in out]


def revert(scan: ScanResult, finding_id: str) -> None:
    """Undo a supervisor change: drop a correction or rejection, or remove a supervisor-added finding."""

    def change(raw: Any) -> dict:
        state = _load(raw)
        _check_open(state, finding_id)
        found = False
        for key in ("corrections", "added", "rejected"):
            found = state[key].pop(finding_id, None) is not None or found
        if not found:
            raise NotFound("This finding has no supervisor change to undo.")
        return state

    update_json(_path(scan.scan_id), change, default=None)


def reject(scan_id: str, finding_id: str, reason: str, actor: str) -> bool:
    """Mark a finding rejected in the reviewed view; False once it was dispatched (the feedback log still records it)."""
    done: list[bool] = []

    def change(raw: Any) -> dict:
        state = _load(raw)
        if finding_id not in state["dispatched"]:
            state["rejected"][finding_id] = {"reason": _text(reason, "reason", REASON_CHARS, required=False), "by": actor, "at": _now()}
            done.append(True)
        return state

    update_json(_path(scan_id), change, default=None)
    return bool(done)


def mark_dispatched(scan_id: str, finding_ids: list[str]) -> None:
    def change(raw: Any) -> dict:
        state = _load(raw)
        state["dispatched"] = sorted(set(state["dispatched"]) | set(finding_ids))
        return state

    update_json(_path(scan_id), change, default=None)


def delete(scan_id: str) -> None:
    _path(scan_id).unlink(missing_ok=True)
