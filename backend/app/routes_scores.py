from fastapi import APIRouter, Depends

from app import scoring
from app.config import get_settings
from app.auth import Permission, require_permission

router = APIRouter(dependencies=[Depends(require_permission(Permission.SCORES))])


@router.get("/scores")
def scores() -> dict:
    """Report-only: pipeline agreement and agent score with their sample sizes, plus a breakdown by prompt version."""
    return scoring.report(get_settings().score_min_reviews)
