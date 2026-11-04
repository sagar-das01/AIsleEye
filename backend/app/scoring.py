"""Report-only scores. Nothing here changes a prompt, a permission or a limit."""

import logging
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app import observability
from app.config import DATA_DIR
from app.storage import append_jsonl, read_jsonl

logger = logging.getLogger(__name__)

SCORES_PATH: Path = DATA_DIR / "scores.jsonl"
DEFAULT_WEIGHTS = {"validity": 0.4, "guard_clean": 0.4, "within_cap": 0.2}
REVIEW_VALUES = ("approve", "reject", "correct")
HUMAN_RATING_WEIGHT = 0.5


def tool_call_validity(proposed: int, valid: int) -> float | None:
    if valid > proposed:
        raise ValueError("valid calls cannot exceed proposed calls")
    return None if proposed == 0 else valid / proposed


def agent_score(
    validity: float, rejections: int, within_cap: bool, rating: float | None = None, weights: dict[str, float] = DEFAULT_WEIGHTS
) -> float:
    """0 to 1 from automatic checks only; a human rating, when one exists, counts at half weight."""
    if set(weights) != set(DEFAULT_WEIGHTS) or abs(sum(weights.values()) - 1.0) > 1e-6:
        raise ValueError(f"weights need the keys {sorted(DEFAULT_WEIGHTS)} and must sum to 1")
    base = weights["validity"] * validity + weights["guard_clean"] * (rejections == 0) + weights["within_cap"] * bool(within_cap)
    if rating is not None:
        base = (base + HUMAN_RATING_WEIGHT * rating) / (1 + HUMAN_RATING_WEIGHT)
    return min(1.0, max(0.0, base))


def pipeline_human_agreement(reviews: list[str]) -> float | None:
    """Approved findings over reviewed findings; a corrected finding counts as a miss."""
    unknown = set(reviews) - set(REVIEW_VALUES)
    if unknown:
        raise ValueError(f"unknown review value(s): {sorted(unknown)}")
    return None if not reviews else reviews.count("approve") / len(reviews)


def score_id(scan_id: str, name: str) -> str:
    return f"{scan_id}-{name}"


def record_scores(
    scan_id: str,
    scores: dict[str, Any],
    *,
    prompt_version: str | None = None,
    n: dict[str, int] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Append one line to the local score log (the source of truth), then mirror it to the trace, best effort."""
    scores = {k: v for k, v in scores.items() if v is not None}
    line = observability.mask({
        "timestamp": datetime.now(UTC).isoformat(),
        "scan_id": scan_id,
        "prompt_version": prompt_version,
        "scores": scores,
        "n": n or {},
        **(extra or {}),
    })
    append_jsonl(SCORES_PATH, line)
    for name, value in line["scores"].items():
        if isinstance(value, (bool, int, float, str)):
            observability.create_score(scan_id, name, value if isinstance(value, str) else float(value), score_id(scan_id, name))


def read_lines() -> list[dict]:
    return read_jsonl(SCORES_PATH)


def latest_entries(lines: list[dict]) -> list[dict]:
    """One entry per scan and score name, the most recent: a score that is re-sent replaces the old one."""
    latest: dict[tuple[str, str], dict] = {}
    for line in lines:
        for name, value in (line.get("scores") or {}).items():
            latest[(line.get("scan_id", ""), name)] = {
                "scan_id": line.get("scan_id"),
                "prompt_version": line.get("prompt_version"),
                "name": name,
                "value": value,
                "n": int((line.get("n") or {}).get(name, 1)),
            }
    return list(latest.values())


def _card(entries: list[dict], name: str, min_n: int, fmt: str) -> dict:
    rows = [e for e in entries if e.get("name") == name and isinstance(e.get("value"), (int, float)) and not isinstance(e["value"], bool)]
    n = sum(int(e.get("n", 1)) for e in rows)
    if n < min_n or n == 0:
        return {"value": None, "n": n, "label": f"n/a ({n} so far)"}
    value = sum(e["value"] * int(e.get("n", 1)) for e in rows) / n
    return {"value": value, "n": n, "label": f"{format(value, fmt)} (n={n})"}


def summarize(entries: list[dict], min_reviews: int = 5) -> dict:
    return {
        "pipeline_agreement": _card(entries, "pipeline_human_agreement", min_reviews, ".0%"),
        "agent_score": _card(entries, "agent_score", min_reviews, ".2f"),
    }


def report(min_reviews: int) -> dict:
    entries = latest_entries(read_lines())

    def values(name: str) -> list[float]:
        return [float(e["value"]) for e in entries if e["name"] == name and isinstance(e["value"], (int, float))]

    validity, latency = values("tool_call_validity"), values("latency_ms")
    components = {
        "tool_call_validity": statistics.fmean(validity) if validity else None,
        "guard_rejections": sum(values("guard_rejections")),
        "duplicate_avoided": sum(values("duplicate_avoided")),
        "within_iteration_cap": statistics.fmean(values("within_iteration_cap")) if values("within_iteration_cap") else None,
        "latency_ms_p50": statistics.median(latency) if latency else None,
    }
    by_version = {}
    for version in sorted({e["prompt_version"] for e in entries if e.get("prompt_version")}):
        by_version[version] = summarize([e for e in entries if e.get("prompt_version") == version], min_reviews)
    return {**summarize(entries, min_reviews), "components": components, "by_prompt_version": by_version}


def update_pipeline_review(scan_id: str, verdicts: dict[str, str], *, reason: str = "", prompt_version: str | None = None) -> float | None:
    """Merge reviewer verdicts for one scan into its running snapshot and re-send pipeline_human_agreement."""
    previous = next((line for line in reversed(read_lines()) if line.get("scan_id") == scan_id and "reviews" in line), None)
    reviews = {**((previous or {}).get("reviews") or {}), **verdicts}
    value = pipeline_human_agreement(list(reviews.values()))
    scores: dict[str, Any] = {"pipeline_human_agreement": value}
    if reason:
        scores["pipeline_review_reason"] = reason
    record_scores(
        scan_id,
        scores,
        prompt_version=prompt_version or (previous or {}).get("prompt_version"),
        n={"pipeline_human_agreement": len(reviews)},
        extra={"reviews": reviews},
    )
    return value
