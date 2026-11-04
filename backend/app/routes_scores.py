from fastapi import APIRouter

from app import scoring
from app.config import get_settings

router = APIRouter()


@router.get("/scores")
def scores() -> dict:
    """Report-only: pipeline agreement and agent score with their sample sizes, plus a breakdown by prompt version."""
    return scoring.report(get_settings().score_min_reviews)
