import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from app import annotations, feedback, replay, scoring
from app.auth import Permission, User, require_permission
from app.pipeline.inspect import prompt_version
from app.schemas import FindingType, ScanResult, Severity

logger = logging.getLogger(__name__)
router = APIRouter()

SCAN_ID = Path(pattern=r"^[0-9a-f]{16}$")
FINDING_ID = Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")
Reviewer = Annotated[User, Depends(require_permission(Permission.REVIEW))]


class FeedbackRequest(BaseModel):
    finding_id: str = Field(min_length=1, max_length=64)
    action: Literal["reject", "correct"]
    reason: str = ""
    corrected_type: FindingType | None = None


class CorrectionRequest(BaseModel):
    type: FindingType
    severity: Severity
    description: str = Field(min_length=1, max_length=500)
    reason: str = Field(default="", max_length=300)


class AddFindingRequest(CorrectionRequest):
    object_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


def _scan(scan_id: str) -> ScanResult:
    scan = replay.find_scan(scan_id)
    if scan is None:
        raise HTTPException(404, "Scan not found.")
    return scan


def _score(scan_id: str, verdicts: dict[str, str], reason: str = "") -> None:
    try:
        # The review judges the vision pipeline, so it is scored apart from the agent.
        scoring.update_pipeline_review(scan_id, verdicts, reason=reason, prompt_version=prompt_version())
    except Exception:
        logger.exception("Could not record the pipeline score")


@router.post("/scan/{scan_id}/feedback")
def post_feedback(scan_id: Annotated[str, SCAN_ID], body: FeedbackRequest, user: Reviewer) -> dict:
    scan = annotations.apply(_scan(scan_id))
    if body.finding_id not in {f.id for f in scan.findings}:
        raise HTTPException(422, "Unknown finding for this scan.")
    try:
        entry = feedback.record_feedback(scan_id, body.finding_id, body.action, body.reason, body.corrected_type)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if body.action == "reject":
        annotations.reject(scan_id, body.finding_id, entry["reason"], user.subject)
    _score(scan_id, {body.finding_id: body.action}, entry["reason"])
    return entry


@router.put("/scan/{scan_id}/findings/{finding_id}", response_model=ScanResult)
def correct_finding(
    scan_id: Annotated[str, SCAN_ID], finding_id: Annotated[str, FINDING_ID], body: CorrectionRequest, user: Reviewer,
) -> ScanResult:
    """Correct the type, severity or description the model proposed; returns the reviewed scan."""
    scan = _scan(scan_id)
    try:
        annotations.correct(scan, finding_id, type=body.type, severity=body.severity, description=body.description,
                            reason=body.reason, actor=user.subject)
    except annotations.ReviewError as exc:
        raise HTTPException(exc.status_code, exc.message) from None
    reviewed = annotations.apply(scan)
    corrected = next(f for f in reviewed.findings if f.id == finding_id)
    if corrected.review_status == "corrected":
        feedback.record_feedback(scan_id, finding_id, "correct", body.reason, body.type, corrected_severity=body.severity)
        _score(scan_id, {finding_id: "correct"}, body.reason)
    return reviewed


@router.post("/scan/{scan_id}/findings", response_model=ScanResult, status_code=201)
def add_finding(scan_id: Annotated[str, SCAN_ID], body: AddFindingRequest, user: Reviewer) -> ScanResult:
    """Add a finding the model missed, on one of the boxes it detected."""
    scan = _scan(scan_id)
    try:
        finding_id = annotations.add(scan, body.object_id, type=body.type, severity=body.severity,
                                     description=body.description, reason=body.reason, actor=user.subject)
    except annotations.ReviewError as exc:
        raise HTTPException(exc.status_code, exc.message) from None
    feedback.record_feedback(scan_id, finding_id, "missed", body.reason, body.type, corrected_severity=body.severity)
    return annotations.apply(scan)


@router.delete("/scan/{scan_id}/findings/{finding_id}/review", response_model=ScanResult)
def undo_review(scan_id: Annotated[str, SCAN_ID], finding_id: Annotated[str, FINDING_ID], user: Reviewer) -> ScanResult:
    """Undo a correction or rejection, or remove a supervisor-added finding."""
    scan = _scan(scan_id)
    try:
        annotations.revert(scan, finding_id)
    except annotations.ReviewError as exc:
        raise HTTPException(exc.status_code, exc.message) from None
    return annotations.apply(scan)
