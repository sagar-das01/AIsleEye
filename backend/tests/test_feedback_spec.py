"""Spec for supervisor feedback (Phase 5 SHOULD): app/feedback.py + POST /scan/{scan_id}/feedback.

Planned interface:
    FEEDBACK_PATH                                          module-level Path (tests monkeypatch it)
    record_feedback(scan_id, finding_id, action, reason, corrected_type=None) -> dict    action in {"reject","correct"}
Feedback is stored for evaluation only; it never changes prompts, thresholds or permissions.
"""

import json

import pytest
from spec_support import need

feedback = need("app.feedback", "record_feedback", "FEEDBACK_PATH")


@pytest.fixture(autouse=True)
def path(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback, "FEEDBACK_PATH", tmp_path / "feedback.jsonl")
    return tmp_path / "feedback.jsonl"


def test_reject_is_stored_with_a_timestamp(path):
    entry = feedback.record_feedback("0123456789abcdef", "dmg_box_1", "reject", "Just a shadow")
    assert json.loads(path.read_text()) == entry
    assert {"scan_id", "finding_id", "action", "reason", "timestamp"} <= set(entry)


def test_correct_requires_a_corrected_type():
    with pytest.raises(ValueError):
        feedback.record_feedback("0123456789abcdef", "dmg_box_1", "correct", "Wrong label")
    entry = feedback.record_feedback("0123456789abcdef", "dmg_box_1", "correct", "It is a dent", corrected_type="box_damage")
    assert entry["corrected_type"] == "box_damage"


@pytest.mark.parametrize("action", ["", "approve", "delete", None, "REJECT"])
def test_only_reject_and_correct_are_accepted(action):
    with pytest.raises(ValueError):
        feedback.record_feedback("0123456789abcdef", "dmg_box_1", action, "r")


def test_corrected_type_must_be_a_known_finding_type():
    with pytest.raises(ValueError):
        feedback.record_feedback("0123456789abcdef", "dmg_box_1", "correct", "r", corrected_type="ufo")


def test_reason_is_length_capped_and_stripped_of_markup(path):
    feedback.record_feedback("0123456789abcdef", "dmg_box_1", "reject", "<script>x</script>" + "r" * 5000)
    stored = json.loads(path.read_text())["reason"]
    assert len(stored) <= 500 and "<script" not in stored


def test_blank_reason_is_allowed_for_reject_but_stored_as_empty(path):
    feedback.record_feedback("0123456789abcdef", "dmg_box_1", "reject", "")
    assert json.loads(path.read_text())["reason"] == ""


def test_feedback_is_append_only(path):
    for i in range(3):
        feedback.record_feedback("0123456789abcdef", f"f{i}", "reject", "r")
    assert len(path.read_text().splitlines()) == 3


def test_feedback_never_touches_settings():
    import app.config as config

    before = config.get_settings().model_dump()
    feedback.record_feedback("0123456789abcdef", "dmg_box_1", "reject", "r")
    assert config.get_settings().model_dump() == before


def test_feedback_endpoint_validates_and_404s_unknown_scans(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    main = need("app.main", "app")
    if not any(getattr(r, "path", "") == "/scan/{scan_id}/feedback" for r in main.app.routes):
        pytest.skip("POST /scan/{scan_id}/feedback is not implemented yet")
    client = TestClient(main.app)
    url = "/scan/0123456789abcdef/feedback"
    assert client.post(url, json={}).status_code == 422
    assert client.post(url, json={"finding_id": "dmg_box_1", "action": "approve", "reason": "x"}).status_code == 422
    assert client.post("/scan/fedcba9876543210/feedback", json={"finding_id": "x", "action": "reject", "reason": "x"}).status_code == 404
