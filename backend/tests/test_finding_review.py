import json

import pytest
from fastapi.testclient import TestClient

from app import audit, scoring, workflow
from app.agent import orchestrator as orch
from app.agent import tools
from app.config import get_settings
from app.main import app
from app.pipeline import scan as scan_mod
from app.schemas import BBox, DetectedObject, Finding, ImageSize, ScanResult

SCAN_ID = "0123456789abcdef"
client = TestClient(app)
BOX = BBox(x1=0, y1=0, x2=5, y2=5)


@pytest.fixture
def world(tmp_path, monkeypatch):
    from app import feedback

    monkeypatch.setenv("AGENT_RUNTIME", "rules")
    get_settings.cache_clear()
    monkeypatch.setattr(scan_mod, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(tools, "DATA_DIR", tmp_path)
    monkeypatch.setattr(feedback, "FEEDBACK_PATH", tmp_path / "feedback.jsonl")
    (tmp_path / "cache").mkdir()
    (tmp_path / "inventory.json").write_text(json.dumps({"SKU-1": {"available": 10, "quarantine": 0}}))
    scan = ScanResult(
        scan_id=SCAN_ID, image_size=ImageSize(width=100, height=80), timings_ms={},
        objects=[DetectedObject(id=f"box_{i}", label="box", bbox=BOX, confidence=0.9, mask_svg="") for i in range(3)],
        findings=[
            Finding(id="dmg_box_0", type="box_damage", severity="low", confidence=0.7, bbox=BOX, description="Small dent.",
                    needs_review=True, review_reason="Low confidence"),
            Finding(id="hz_box_1", type="walkway_blocked", severity="high", confidence=0.9, bbox=BOX, description="On the lane."),
        ],
    )
    (tmp_path / "cache" / f"{SCAN_ID}.json").write_text(scan.model_dump_json())
    return tmp_path


def correct(finding_id="dmg_box_0", **body):
    payload = {"type": "box_damage", "severity": "high", "description": "Corner fully crushed.", "reason": "Model undersold it", **body}
    return client.put(f"/scan/{SCAN_ID}/findings/{finding_id}", json=payload)


def feedback_lines(world):
    path = world / "feedback.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_correction_replaces_model_values_and_keeps_the_original(world):
    response = correct()
    assert response.status_code == 200
    fixed = next(f for f in response.json()["findings"] if f["id"] == "dmg_box_0")
    assert (fixed["severity"], fixed["description"], fixed["review_status"]) == ("high", "Corner fully crushed.", "corrected")
    assert fixed["original"] == {"type": "box_damage", "severity": "low", "description": "Small dent."}
    assert fixed["needs_review"] is False and fixed["review_note"] == "Model undersold it"
    assert client.get(f"/scan/{SCAN_ID}").json()["findings"][0]["review_status"] == "corrected"
    # The model's output itself is untouched: corrections live in a separate review store.
    raw = json.loads((world / "cache" / f"{SCAN_ID}.json").read_text())
    assert raw["findings"][0]["severity"] == "low"
    [entry] = feedback_lines(world)
    assert (entry["action"], entry["corrected_type"], entry["corrected_severity"]) == ("correct", "box_damage", "high")


def test_changing_the_type_moves_the_finding_to_the_other_dispatch_path(world):
    correct("hz_box_1", type="box_damage", severity="medium", description="Not in the lane; carton is torn.")
    response = client.post(f"/scan/{SCAN_ID}/approve", json={"finding_ids": ["hz_box_1"], "skus": {"hz_box_1": "SKU-1"}})
    tools_used = [(a["tool"], a["status"]) for a in response.json()["actions"]]
    assert ("log_compliance_event", "executed") not in tools_used  # no walkway compliance entry any more
    [ticket] = workflow.list_tickets()
    assert ticket["assignee_team"] == "Maintenance" and ticket["finding_type"] == "box_damage"
    assert tools.read_inventory()["SKU-1"]["quarantine"] == 1


def test_corrected_description_reaches_the_agent(world, monkeypatch):
    seen = {}
    real = orch.build_user_message
    monkeypatch.setattr(orch, "build_user_message", lambda scan, *a: seen.setdefault("msg", real(scan, *a)))
    correct()
    client.post(f"/scan/{SCAN_ID}/approve", json={"finding_ids": ["dmg_box_0"]})
    assert "Corner fully crushed." in seen["msg"] and "Small dent." not in seen["msg"]


def test_reverting_to_the_model_values_or_undo_restores_the_proposal(world):
    correct()
    same = correct(severity="low", description="Small dent.")
    assert same.json()["findings"][0]["review_status"] == "proposed"
    correct()
    undone = client.delete(f"/scan/{SCAN_ID}/findings/dmg_box_0/review")
    assert undone.json()["findings"][0]["review_status"] == "proposed"
    assert client.delete(f"/scan/{SCAN_ID}/findings/dmg_box_0/review").status_code == 404


def test_supervisor_can_add_a_missed_finding_on_a_detected_box(world):
    response = client.post(f"/scan/{SCAN_ID}/findings", json={
        "object_id": "box_2", "type": "box_damage", "severity": "medium", "description": "Water stain on the side.",
    })
    assert response.status_code == 201
    added = next(f for f in response.json()["findings"] if f["review_status"] == "added")
    assert (added["id"], added["object_id"], added["bbox"]) == ("added_dmg_box_2", "box_2", BOX.model_dump())
    assert client.get(f"/scan/{SCAN_ID}/count").json()["damaged"] == 2
    approve = client.post(f"/scan/{SCAN_ID}/approve", json={"finding_ids": ["added_dmg_box_2"]})
    assert approve.status_code == 200 and len(approve.json()["tickets"]) == 1
    assert feedback_lines(world)[0]["action"] == "missed"


@pytest.mark.parametrize(("body", "status"), [
    ({"object_id": "box_0", "type": "box_damage"}, 422),       # already has a damage finding: edit it instead
    ({"object_id": "walkway_0", "type": "box_damage"}, 404),
    ({"object_id": "box_9", "type": "walkway_blocked"}, 404),
    ({"object_id": "box_2", "type": "spill"}, 422),
])
def test_adding_is_limited_to_detected_boxes_without_that_finding(world, body, status):
    response = client.post(f"/scan/{SCAN_ID}/findings", json={"severity": "low", "description": "x", **body})
    assert response.status_code == status


def test_added_findings_can_be_edited_and_removed(world):
    client.post(f"/scan/{SCAN_ID}/findings", json={"object_id": "box_2", "type": "walkway_blocked", "severity": "low", "description": "Edge of lane."})
    edited = correct("added_hz_box_2", type="walkway_blocked", severity="high", description="Fully in the lane.")
    assert next(f for f in edited.json()["findings"] if f["id"] == "added_hz_box_2")["severity"] == "high"
    removed = client.delete(f"/scan/{SCAN_ID}/findings/added_hz_box_2/review").json()
    assert "added_hz_box_2" not in {f["id"] for f in removed["findings"]}


def test_rejected_findings_are_persisted_and_cannot_be_dispatched(world):
    client.post(f"/scan/{SCAN_ID}/feedback", json={"finding_id": "hz_box_1", "action": "reject", "reason": "tape is old"})
    assert client.get(f"/scan/{SCAN_ID}").json()["findings"][1]["review_status"] == "rejected"
    response = client.post(f"/scan/{SCAN_ID}/approve", json={"finding_ids": ["hz_box_1"]})
    assert response.status_code == 422 and "Rejected" in response.json()["detail"]
    correct("hz_box_1", type="walkway_blocked", description="Actually blocks the lane.")  # correcting un-rejects
    assert client.post(f"/scan/{SCAN_ID}/approve", json={"finding_ids": ["hz_box_1"]}).status_code == 200


def test_dispatched_findings_are_locked(world):
    client.post(f"/scan/{SCAN_ID}/approve", json={"finding_ids": ["dmg_box_0"]})
    assert correct().status_code == 409
    assert client.delete(f"/scan/{SCAN_ID}/findings/dmg_box_0/review").status_code == 409
    assert correct("hz_box_1", type="walkway_blocked").status_code == 200  # findings not yet dispatched stay editable


def test_corrections_are_not_scored_as_agreement(world):
    correct()
    client.post(f"/scan/{SCAN_ID}/approve", json={"finding_ids": ["dmg_box_0", "hz_box_1"]})
    reviews = scoring.read_lines()
    latest = [r for r in reviews if "pipeline_human_agreement" in r.get("scores", {})][-1]
    assert latest["scores"]["pipeline_human_agreement"] == 0.5  # one correction (a miss), one plain approval


def test_review_is_served_on_later_reads_and_delete_clears_it(world):
    correct()
    assert client.get(f"/scan/{SCAN_ID}").json()["findings"][0]["severity"] == "high"
    assert client.delete(f"/scan/{SCAN_ID}").status_code == 204
    assert not (world / "reviews" / f"{SCAN_ID}.json").exists()


def test_input_is_validated_and_sanitized(world):
    assert correct(severity="extreme").status_code == 422
    assert correct(description="").status_code == 422
    assert correct("nope").status_code == 404
    response = correct(description="<script>x</script>Torn flap")
    assert "<script>" not in response.json()["findings"][0]["description"]
    assert client.put("/scan/fedcba9876543210/findings/dmg_box_0", json={"type": "box_damage", "severity": "low", "description": "x"}).status_code == 404


def test_corrections_are_audited_as_authorized_actions(world):
    correct()
    assert any(e["tool"] == "authorize:review" for e in audit.read_all())


def test_re_approving_while_tickets_are_active_replays_and_says_so(world):
    first = client.post(f"/scan/{SCAN_ID}/approve", json={"finding_ids": ["dmg_box_0"]}).json()
    assert first["already_dispatched"] is False
    again = client.post(f"/scan/{SCAN_ID}/approve", json={"finding_ids": ["dmg_box_0"]})
    assert again.headers["Idempotent-Replay"] == "true" and again.json()["already_dispatched"] is True
    assert again.json()["approved_at"] == first["approved_at"] and len(workflow.list_tickets()) == 1


def test_replays_show_the_tickets_current_status(world, monkeypatch):
    from app import routes_agent

    first = client.post(f"/scan/{SCAN_ID}/approve", json={"finding_ids": ["dmg_box_0"]}).json()
    ticket_id = first["tickets"][0]["ticket_id"]
    monkeypatch.setattr(routes_agent, "DOUBLE_CLICK_S", 0)
    store = json.loads(workflow.store_path().read_text())
    store["tickets"][ticket_id].update(status="in_progress", assigned_to_user_id="tech-a")
    workflow.store_path().write_text(json.dumps(store))
    again = client.post(f"/scan/{SCAN_ID}/approve", json={"finding_ids": ["dmg_box_0"]}).json()
    assert again["already_dispatched"] is True
    assert (again["tickets"][0]["status"], again["tickets"][0]["assigned_to_user_id"]) == ("in_progress", "tech-a")


def test_once_its_tickets_are_resolved_the_same_findings_dispatch_a_new_incident(world, monkeypatch):
    from app import routes_agent

    first = client.post(f"/scan/{SCAN_ID}/approve", json={"finding_ids": ["dmg_box_0"]}).json()
    store = json.loads(workflow.store_path().read_text())
    store["tickets"][first["tickets"][0]["ticket_id"]]["status"] = "resolved"
    workflow.store_path().write_text(json.dumps(store))
    # Within the double-click window it is still a replay...
    assert client.post(f"/scan/{SCAN_ID}/approve", json={"finding_ids": ["dmg_box_0"]}).json()["already_dispatched"] is True
    # ...after it, a resolved incident can be reported again.
    monkeypatch.setattr(routes_agent, "DOUBLE_CLICK_S", 0)
    second = client.post(f"/scan/{SCAN_ID}/approve", json={"finding_ids": ["dmg_box_0"]}).json()
    assert second["already_dispatched"] is False and second["tickets"][0]["ticket_id"] != first["tickets"][0]["ticket_id"]
    assert len(workflow.list_tickets()) == 2


def test_legacy_stored_approvals_without_a_timestamp_still_work(world):
    first = client.post(f"/scan/{SCAN_ID}/approve", json={"finding_ids": ["hz_box_1"]}).json()
    path = next((world / "approvals").glob("*.json"))
    stored = json.loads(path.read_text())
    stored.pop("approved_at")
    stored.pop("already_dispatched")
    path.write_text(json.dumps(stored))
    again = client.post(f"/scan/{SCAN_ID}/approve", json={"finding_ids": ["hz_box_1"]}).json()
    assert again["already_dispatched"] is True and again["tickets"][0]["ticket_id"] == first["tickets"][0]["ticket_id"]
