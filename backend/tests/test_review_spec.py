"""Spec for needs-review flags (Phase 5 MUST): low-confidence or disagreeing results are never shown as certain.

Planned interface:
    Finding.needs_review: bool = False, Finding.review_reason: str | None = None   (schema fields from the plan)
    app.review.flag_needs_review(findings, *, threshold=0.6, uninspected_ids=()) -> list[Finding]
        flags confidence < threshold, and findings whose box the inspection could not assess; returns new objects
    settings.review_confidence (default 0.6)
"""

import pytest
from spec_support import has_field, need

review = need("app.review", "flag_needs_review")

from app.schemas import BBox, Finding  # noqa: E402

if not has_field(Finding, "needs_review"):
    pytest.skip("Finding.needs_review is not in the schema yet", allow_module_level=True)

BOX = BBox(x1=0, y1=0, x2=5, y2=5)


def f(fid="dmg_box_1", confidence=0.9, type_="box_damage"):
    return Finding(id=fid, type=type_, severity="low", confidence=confidence, bbox=BOX, description="d")


def test_schema_defaults_are_confident():
    finding = f()
    assert finding.needs_review is False and finding.review_reason is None


def test_default_threshold_matches_the_plan():
    import app.config as config

    assert config.Settings(_env_file=None).review_confidence == 0.6


@pytest.mark.parametrize(("confidence", "flagged"), [(0.0, True), (0.59, True), (0.5999, True), (0.6, False), (0.61, False), (1.0, False)])
def test_threshold_boundary_is_strict(confidence, flagged):
    [out] = review.flag_needs_review([f(confidence=confidence)], threshold=0.6)
    assert out.needs_review is flagged
    assert bool(out.review_reason) is flagged


def test_reason_mentions_the_confidence():
    [out] = review.flag_needs_review([f(confidence=0.42)])
    assert "0.42" in out.review_reason or "42" in out.review_reason


def test_findings_the_inspection_could_not_assess_are_flagged_even_if_confident():
    [out] = review.flag_needs_review([f("dmg_box_1", 0.99)], uninspected_ids={"dmg_box_1"})
    assert out.needs_review and out.review_reason


def test_inputs_are_not_mutated_and_order_is_preserved():
    findings = [f("a", 0.1), f("b", 0.9), f("c", 0.3)]
    out = review.flag_needs_review(findings)
    assert [x.id for x in out] == ["a", "b", "c"] and [x.needs_review for x in out] == [True, False, True]
    assert all(x.needs_review is False for x in findings)


def test_empty_input_gives_empty_output():
    assert review.flag_needs_review([]) == []


def test_custom_threshold_is_honoured():
    [out] = review.flag_needs_review([f(confidence=0.7)], threshold=0.8)
    assert out.needs_review


@pytest.mark.parametrize("bad", [-0.1, 1.1])
def test_threshold_must_be_a_probability(bad):
    with pytest.raises(ValueError):
        review.flag_needs_review([f()], threshold=bad)
