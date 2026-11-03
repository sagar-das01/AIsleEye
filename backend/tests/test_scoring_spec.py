"""Spec for app/scoring.py (Phase 6 MUST). Two score families, report-only.

Planned interface:
    SCORES_PATH                                         module-level Path (tests monkeypatch it)
    tool_call_validity(proposed: int, valid: int) -> float | None     None when nothing was proposed
    agent_score(validity, rejections, within_cap, rating=None, weights=DEFAULT_WEIGHTS) -> float   0..1
    pipeline_human_agreement(reviews: list[str]) -> float | None      reviews in {"approve","reject","correct"}
    score_id(scan_id, name) -> str                      stable, so re-sent Langfuse scores overwrite
    record_scores(scan_id, scores: dict) -> None        append-only SCORES_PATH, mirror is best-effort
    summarize(entries, min_reviews=5) -> dict           {"pipeline_agreement": {"value"|None, "n", "label"}, "agent_score": {...}}
"""

import json

import pytest
from spec_support import need

scoring = need("app.scoring", "tool_call_validity", "agent_score", "pipeline_human_agreement", "score_id", "record_scores", "summarize", "SCORES_PATH")


@pytest.fixture(autouse=True)
def scores_path(tmp_path, monkeypatch):
    monkeypatch.setattr(scoring, "SCORES_PATH", tmp_path / "scores.jsonl")
    return tmp_path / "scores.jsonl"


@pytest.mark.parametrize(("proposed", "valid", "expected"), [(5, 5, 1.0), (5, 4, 0.8), (3, 0, 0.0), (1, 1, 1.0)])
def test_tool_call_validity(proposed, valid, expected):
    assert scoring.tool_call_validity(proposed, valid) == pytest.approx(expected)


def test_no_proposed_calls_is_not_applicable_rather_than_perfect():
    assert scoring.tool_call_validity(0, 0) is None


def test_valid_cannot_exceed_proposed():
    with pytest.raises(ValueError):
        scoring.tool_call_validity(2, 3)


@pytest.mark.parametrize(
    ("validity", "rejections", "within_cap", "expected"),
    [
        (1.0, 0, True, 1.0),
        (0.8, 1, True, 0.4 * 0.8 + 0.0 + 0.2),
        (1.0, 0, False, 0.8),
        (0.0, 3, False, 0.0),
        (0.5, 0, True, 0.4 * 0.5 + 0.4 + 0.2),
    ],
)
def test_agent_score_default_weights(validity, rejections, within_cap, expected):
    assert scoring.agent_score(validity, rejections, within_cap) == pytest.approx(expected)


def test_guard_clean_is_all_or_nothing():
    assert scoring.agent_score(1.0, 1, True) == scoring.agent_score(1.0, 9, True)


def test_agent_score_is_always_between_zero_and_one():
    for v in (0.0, 0.3, 1.0):
        for r in (0, 1, 50):
            for cap in (True, False):
                assert 0.0 <= scoring.agent_score(v, r, cap) <= 1.0


def test_custom_weights_are_honoured_and_must_sum_to_one():
    assert scoring.agent_score(1.0, 5, False, weights={"validity": 1.0, "guard_clean": 0.0, "within_cap": 0.0}) == 1.0
    with pytest.raises(ValueError):
        scoring.agent_score(1.0, 0, True, weights={"validity": 0.9, "guard_clean": 0.9, "within_cap": 0.9})


def test_human_rating_is_blended_at_half_weight_when_present():
    base = scoring.agent_score(1.0, 0, True)
    blended = scoring.agent_score(1.0, 0, True, rating=0.0)
    assert 0.0 < blended < base


# ---- pipeline agreement ---------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("reviews", "expected"),
    [(["approve"] * 4, 1.0), (["approve", "reject"], 0.5), (["approve", "correct"], 0.5), (["reject"], 0.0), (["approve", "approve", "correct", "reject"], 0.5)],
)
def test_pipeline_agreement(reviews, expected):
    assert scoring.pipeline_human_agreement(reviews) == pytest.approx(expected)


def test_pipeline_agreement_with_no_reviews_is_not_applicable():
    assert scoring.pipeline_human_agreement([]) is None


def test_unknown_review_values_are_refused():
    with pytest.raises(ValueError):
        scoring.pipeline_human_agreement(["approve", "meh"])


# ---- ids, storage and the minimum-sample rule -----------------------------------------------------------------

def test_score_ids_are_stable_and_distinct_per_name():
    assert scoring.score_id("0123456789abcdef", "agent_score") == scoring.score_id("0123456789abcdef", "agent_score")
    assert scoring.score_id("0123456789abcdef", "agent_score") != scoring.score_id("0123456789abcdef", "latency_ms")


def test_record_scores_appends_one_line_per_call_and_keeps_history(scores_path):
    scoring.record_scores("0123456789abcdef", {"agent_score": 0.9})
    scoring.record_scores("0123456789abcdef", {"agent_score": 0.7})
    lines = [json.loads(line) for line in scores_path.read_text().splitlines()]
    assert len(lines) == 2 and lines[0]["scan_id"] == "0123456789abcdef"


def test_scores_file_never_holds_image_data(scores_path):
    scoring.record_scores("0123456789abcdef", {"note": "data:image/png;base64,AAAA"})
    assert "data:image" not in scores_path.read_text()


@pytest.mark.parametrize(("n", "shown"), [(0, False), (4, False), (5, True), (50, True)])
def test_pipeline_score_is_hidden_below_the_minimum_review_count(n, shown):
    entries = [{"name": "pipeline_human_agreement", "value": 1.0, "n": 1} for _ in range(n)]
    card = scoring.summarize(entries, min_reviews=5)["pipeline_agreement"]
    assert (card["value"] is not None) is shown and card["n"] == n
    if not shown:
        assert f"{n} so far" in card["label"]


def test_summary_reports_sample_size_next_to_every_value():
    entries = [{"name": "agent_score", "value": 0.5, "n": 1}] * 7
    card = scoring.summarize(entries, min_reviews=5)["agent_score"]
    assert card["n"] == 7 and card["value"] == pytest.approx(0.5)


def test_scoring_is_report_only():
    import app.config as config

    before = config.get_settings().model_dump()
    scoring.record_scores("0123456789abcdef", {"agent_score": 0.0})
    assert config.get_settings().model_dump() == before
