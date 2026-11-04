from collections.abc import Iterable, Mapping

from app.schemas import Finding

DEFAULT_THRESHOLD = 0.6


def flag_needs_review(
    findings: list[Finding],
    *,
    threshold: float = DEFAULT_THRESHOLD,
    uninspected_ids: Iterable[str] = (),
    disagreements: Mapping[str, str] | None = None,
) -> list[Finding]:
    """Return copies of the findings, flagged when they should not be shown as certain."""
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1")
    uninspected = set(uninspected_ids)
    disagreements = disagreements or {}
    out = []
    for f in findings:
        reason = None
        if f.confidence < threshold:
            reason = f"Low confidence ({f.confidence:.2f}); check this one in person."
        elif f.id in uninspected:
            reason = "The inspection could not assess this box; check it in person."
        elif f.id in disagreements:
            reason = disagreements[f.id]
        out.append(f.model_copy(update={"needs_review": reason is not None, "review_reason": reason}))
    return out
