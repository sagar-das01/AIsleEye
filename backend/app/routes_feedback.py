import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from app import feedback, replay, scoring
from app.auth import Permission, require_permission
from app.pipeline.inspect import prompt_version
from app.schemas import FindingType

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(require_permission(Permission.REVIEW))])

SCAN_ID_PATTERN = r"^[0-9a-f]{16}$"


class FeedbackRequest(BaseModel):
    finding_id: str = Field(min_length=1, max_length=64)
    action: Literal["reject", "correct"]
    reason: str = ""
    corrected_type: FindingType | None = None


@router.post("/scan/{scan_id}/feedback")
def post_feedback(scan_id: Annotated[str, Path(pattern=SCAN_ID_PATTERN)], body: FeedbackRequest) -> dict:
    scan = replay.find_scan(scan_id)
    if scan is None:
        raise HTTPException(404, "Scan not found.")
    if body.finding_id not in {f.id for f in scan.findings}:
        raise HTTPException(422, "Unknown finding for this scan.")
    try:
        entry = feedback.record_feedback(scan_id, body.finding_id, body.action, body.reason, body.corrected_type)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    try:
        # The review judges the vision pipeline, so it is scored apart from the agent.
        scoring.update_pipeline_review(scan_id, {body.finding_id: body.action}, reason=entry["reason"], prompt_version=prompt_version())
    except Exception:
        logger.exception("Could not record the pipeline score")
    return entry
