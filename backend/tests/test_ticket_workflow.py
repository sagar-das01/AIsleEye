import json
import threading

import pytest

from app import audit, workflow
from app.agent import tools
from app.guard import Guard
from app.storage import append_jsonl
from tests.rbac_support import assign, seed_ticket, sign_in, world  # noqa: F401

pytestmark = pytest.mark.rbac


def events(ticket_id="T-1"):
    return [e["type"] for e in workflow.history(ticket_id)]


def test_unassigned_tickets_are_manager_only(world):
    seed_ticket(world)
    assert sign_in("tech-a").get("/tickets").json() == []
    manager = sign_in("floor-manager").get("/tickets").json()
    assert [t["ticket_id"] for t in manager] == ["T-1"]
    assert manager[0]["status"] == "open" and manager[0]["capabilities"]["assign"] is True


def test_assignment_requires_actionable_details(world):
    seed_ticket(world)
    manager = sign_in("floor-manager")
    response = manager.post("/tickets/T-1/assignment", json={"assignee_id": "tech-a"})
    assert response.status_code == 422 and "location" in response.json()["detail"]
    assert assign(manager, "T-1", "tech-a").status_code == 200


@pytest.mark.parametrize("assignee", ["floor-manager", "auditor", "reporter", "ghost", "admin"])
def test_only_active_eligible_responders_can_be_assigned(world, assignee):
    seed_ticket(world)
    assert assign(sign_in("floor-manager"), "T-1", assignee).status_code == 422


def test_disabled_technicians_are_not_eligible(world):
    seed_ticket(world)
    admin = sign_in("admin")
    admin.post("/admin/accounts/tech-b/active", json={"active": False})
    assert [t["user_id"] for t in admin.get("/technicians").json()] == ["tech-a", "contractor"]
    assert assign(admin, "T-1", "tech-b").status_code == 422


def test_full_lifecycle_new_in_progress_history(world):
    seed_ticket(world)
    manager, tech = sign_in("floor-manager"), sign_in("tech-a")
    assigned = assign(manager, "T-1", "tech-a").json()
    assert assigned["assigned_to_user_id"] == "tech-a" and assigned["assigned_to_name"] == "Tariq (Technician A)"

    [new] = tech.get("/tickets", params={"status": "open", "mine": True}).json()
    assert new["capabilities"] == {"assign": False, "acknowledge": True, "resolve": False, "note": True}
    inbox = tech.get("/notifications").json()
    assert inbox["unread"] == 1 and inbox["items"][0]["title"] == "Ticket T-1"

    acked = tech.post("/tickets/T-1/acknowledge", json={"expected_version": new["version"]}).json()
    assert acked["status"] == "in_progress" and acked["acknowledged_by"] == "tech-a"
    assert tech.post("/tickets/T-1/notes", json={"text": "Pallet restacked, wrapping now."}).status_code == 200
    assert tech.post("/tickets/T-1/resolve", json={"notes": "  "}).status_code == 422
    done = tech.post("/tickets/T-1/resolve", json={"notes": "Rewrapped and moved to bay 3."}).json()
    assert done["status"] == "resolved" and done["resolution_notes"] == "Rewrapped and moved to bay 3."
    assert [t["ticket_id"] for t in tech.get("/tickets", params={"status": "resolved"}).json()] == ["T-1"]
    assert events() == ["created", "assigned", "acknowledged", "note", "resolved"]
    history = tech.get("/tickets/T-1/history").json()
    assert history[1]["actor_name"] == "Fatima (Floor Manager)"
    trail = [e["tool"] for e in audit.read_all()]
    assert {"ticket:assigned", "ticket:acknowledged", "ticket:note", "ticket:resolved"} <= set(trail)


def test_repeated_acknowledge_and_resolve_create_one_event(world):
    seed_ticket(world)
    assign(sign_in("floor-manager"), "T-1", "tech-a")
    tech = sign_in("tech-a")
    for _ in range(3):
        assert tech.post("/tickets/T-1/acknowledge", json={}).status_code == 200
    for _ in range(2):
        assert tech.post("/tickets/T-1/resolve", json={"notes": "Done."}).status_code == 200
    assert events().count("acknowledged") == 1 and events().count("resolved") == 1


def test_concurrent_acknowledgements_create_one_transition(world):
    seed_ticket(world)
    assign(sign_in("floor-manager"), "T-1", "tech-a")
    tech = sign_in("tech-a")
    results: list[int] = []
    threads = [threading.Thread(target=lambda: results.append(tech.post("/tickets/T-1/acknowledge", json={"expected_version": 2}).status_code)) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [200] * 6 and events().count("acknowledged") == 1


def test_resolution_requires_acknowledgement_and_opening_never_acknowledges(world):
    seed_ticket(world)
    assign(sign_in("floor-manager"), "T-1", "tech-a")
    tech = sign_in("tech-a")
    tech.get("/tickets/T-1")
    tech.get("/tickets/T-1.pdf")
    note = tech.get("/notifications").json()["items"][0]
    assert tech.post(f"/notifications/{note['notification_id']}/read").json()["read_at"]
    assert workflow.get_ticket("T-1")["status"] == "open"
    assert tech.get("/notifications").json()["unread"] == 0
    assert tech.post("/tickets/T-1/resolve", json={"notes": "x"}).status_code == 409


def test_stale_versions_conflict(world):
    seed_ticket(world)
    manager = sign_in("floor-manager")
    assign(manager, "T-1", "tech-a")
    assert assign(manager, "T-1", "tech-b", reason="Shift change", expected_version=1).status_code == 409
    assert sign_in("tech-a").post("/tickets/T-1/acknowledge", json={"expected_version": 1}).status_code == 409


def test_technicians_are_isolated_from_each_other(world):
    seed_ticket(world, "T-a")
    seed_ticket(world, "T-b")
    manager = sign_in("floor-manager")
    assign(manager, "T-a", "tech-a")
    assign(manager, "T-b", "tech-b")
    a, b, admin = sign_in("tech-a"), sign_in("tech-b"), sign_in("admin")
    assert [t["ticket_id"] for t in a.get("/tickets").json()] == ["T-a"]
    assert [t["ticket_id"] for t in b.get("/tickets").json()] == ["T-b"]
    assert {t["ticket_id"] for t in admin.get("/tickets").json()} == {"T-a", "T-b"}
    for path in ["/tickets/T-b", "/tickets/T-b.pdf", "/tickets/T-b/history"]:
        assert a.get(path).status_code == 404
    for path, body in [("acknowledge", {}), ("notes", {"text": "x"}), ("resolve", {"notes": "x"})]:
        assert a.post(f"/tickets/T-b/{path}", json=body).status_code == 404
    assert a.get("/notifications").json()["items"][0]["ticket_id"] == "T-a"


def test_technicians_cannot_assign_approve_or_change_stock(world):
    seed_ticket(world)
    assign(sign_in("floor-manager"), "T-1", "tech-a")
    tech = sign_in("tech-a")
    assert assign(tech, "T-1", "tech-a").status_code == 403
    assert tech.get("/technicians").status_code == 403
    assert tech.post("/scan/0123456789abcdef/approve", json={"finding_ids": ["x"]}).status_code == 403
    assert tech.post("/scan/0123456789abcdef/reconcile", json={"sku": "SKU-1"}).status_code == 403


def test_reassignment_revokes_old_access_and_resets_to_new(world):
    seed_ticket(world)
    manager, a, b = sign_in("floor-manager"), sign_in("tech-a"), sign_in("tech-b")
    assign(manager, "T-1", "tech-a")
    a.post("/tickets/T-1/acknowledge", json={})
    assert assign(manager, "T-1", "tech-b").status_code == 422  # reason required
    moved = assign(manager, "T-1", "tech-b", reason="Tariq is off shift").json()
    assert moved["status"] == "open" and moved["acknowledged_by"] is None and moved["assignment_version"] == 2
    assert a.get("/tickets").json() == [] and a.get("/tickets/T-1.pdf").status_code == 404
    assert a.post("/tickets/T-1/resolve", json={"notes": "done"}).status_code == 404
    old = a.get("/notifications").json()["items"][0]
    assert old["current"] is False and "no longer assigned" in old["title"] and old["severity"] is None
    assert b.get("/notifications").json()["items"][0]["event_type"] == "reassigned"
    assert events() == ["created", "assigned", "acknowledged", "reassigned"]


def test_stale_assignee_action_after_reassignment_is_atomic(world):
    seed_ticket(world)
    manager, a = sign_in("floor-manager"), sign_in("tech-a")
    assign(manager, "T-1", "tech-a")
    from app.auth import is_current_assignee
    from app.personas import find_user

    user = find_user("tech-a")
    assign(manager, "T-1", "tech-b", reason="Swap")
    with pytest.raises(workflow.NotFound):
        workflow.acknowledge("T-1", "tech-a", expected_version=None, authorized=lambda t: is_current_assignee(user, t))


def test_resolved_tickets_cannot_be_reassigned_or_noted(world):
    seed_ticket(world)
    manager, tech = sign_in("floor-manager"), sign_in("tech-a")
    assign(manager, "T-1", "tech-a")
    tech.post("/tickets/T-1/acknowledge", json={})
    tech.post("/tickets/T-1/resolve", json={"notes": "Done."})
    assert assign(manager, "T-1", "tech-b", reason="x").status_code == 409
    assert manager.post("/tickets/T-1/notes", json={"text": "late"}).status_code == 409


def test_repeat_assignment_does_not_duplicate_notifications(world):
    seed_ticket(world)
    manager = sign_in("floor-manager")
    for _ in range(3):
        assert assign(manager, "T-1", "tech-a").status_code == 200
    assert len(sign_in("tech-a").get("/notifications").json()["items"]) == 1
    assert events().count("assigned") == 1


def test_notifications_are_private(world):
    seed_ticket(world)
    assign(sign_in("floor-manager"), "T-1", "tech-a")
    note = sign_in("tech-a").get("/notifications").json()["items"][0]
    assert sign_in("tech-b").post(f"/notifications/{note['notification_id']}/read").status_code == 404
    assert sign_in("admin").get("/notifications").json()["items"] == []


def test_assignment_and_notification_survive_restart(world):
    seed_ticket(world)
    assign(sign_in("floor-manager"), "T-1", "tech-a")
    stored = json.loads(workflow.store_path().read_text())
    assert stored["tickets"]["T-1"]["assigned_to_user_id"] == "tech-a" and len(stored["notifications"]) == 1


def test_dispatcher_assigns_within_team_only(world):
    seed_ticket(world, "T-safety", assignee_team="Safety")
    seed_ticket(world, "T-maint", assignee_team="Maintenance")
    dispatcher = sign_in("dispatcher")
    assert assign(dispatcher, "T-safety", "tech-a").status_code == 200
    assert assign(dispatcher, "T-maint", "tech-a").status_code == 404


def test_auditor_is_read_only(world):
    seed_ticket(world)
    auditor = sign_in("auditor")
    assert auditor.get("/tickets/T-1").json()["capabilities"] == {"assign": False, "acknowledge": False, "resolve": False, "note": False}
    assert assign(auditor, "T-1", "tech-a").status_code == 403
    assert auditor.post("/tickets/T-1/notes", json={"text": "x"}).status_code == 404


def test_work_notes_are_sanitized(world):
    seed_ticket(world)
    manager = sign_in("floor-manager")
    assign(manager, "T-1", "tech-a")
    manager.post("/tickets/T-1/notes", json={"text": "<script>alert(1)</script>Check the strap"})
    note = workflow.history("T-1")[-1]["note"]
    assert "<script>" not in note and "Check the strap" in note


def test_expired_evidence_does_not_break_details_or_resolution(world):
    seed_ticket(world, pdf=False)
    manager, tech = sign_in("floor-manager"), sign_in("tech-a")
    assign(manager, "T-1", "tech-a")
    assert tech.get("/tickets/T-1").status_code == 200
    assert tech.get("/tickets/T-1.pdf").status_code == 404
    tech.post("/tickets/T-1/acknowledge", json={})
    assert tech.post("/tickets/T-1/resolve", json={"notes": "Done without the photo."}).status_code == 200


def test_legacy_index_is_migrated_once_and_backed_up(world):
    legacy = world.path / "tickets" / "index.jsonl"
    append_jsonl(legacy, {"ticket_id": "T-old", "status": "open", "title": "Old", "severity": "low", "assignee_team": "Safety", "created_at": "2026-01-01T00:00:00+00:00"})
    append_jsonl(legacy, {"ticket_id": "T-owned", "status": "open", "title": "Owned", "severity": "low", "assignee_team": "Safety", "assigned_to_user_id": "oidc|123"})
    append_jsonl(legacy, {"status": "open"})
    (world.path / "tickets" / "T-old.pdf").write_bytes(b"%PDF old")
    admin = sign_in("admin")
    tickets = {t["ticket_id"]: t for t in admin.get("/tickets").json()}
    assert set(tickets) == {"T-old", "T-owned"}
    assert tickets["T-old"]["created_at"] == "2026-01-01T00:00:00+00:00" and tickets["T-old"]["legacy"] is True
    assert tickets["T-owned"]["assigned_to_user_id"] is None  # never guessed or kept for an unknown identity
    assert admin.get("/tickets/T-old.pdf").status_code == 200
    assert not legacy.exists() and len(list((world.path / "tickets").glob("index.*.migrated.jsonl"))) == 1
    assert len(workflow.migration_issues()) == 2
    append_jsonl(legacy, {"ticket_id": "T-old", "status": "open", "title": "Old again"})
    assert len(admin.get("/tickets").json()) == 2  # repeatable: known IDs are not imported twice


def test_corrupt_workflow_store_fails_with_503(world):
    seed_ticket(world)
    workflow.store_path().write_text("{broken")
    assert sign_in("admin").get("/tickets").status_code == 503


def test_in_progress_tickets_still_block_duplicates():
    guard = Guard("s1", ["f1"], {}, {}, open_tickets=[{"ticket_id": "T-9", "scan_id": "s1", "finding_id": "f1", "status": "in_progress"}])
    verdict = guard.evaluate("create_incident_ticket", {"finding_id": "f1"})
    assert not verdict.allowed and verdict.duplicate_of == "T-9"
    guard = Guard("s1", ["f1"], {}, {}, open_tickets=[{"ticket_id": "T-9", "scan_id": "s1", "finding_id": "f1", "status": "resolved"}])
    assert guard.evaluate("create_incident_ticket", {"finding_id": "f1"}).allowed


def test_agent_tickets_carry_trusted_context_into_the_store(world):
    ticket = tools.create_incident_ticket(
        "dmg_box_1", "Crushed box", "high", "Maintenance", "Corner crushed", scan_id="0123456789abcdef",
        trusted={"finding_type": "box_damage", "sku": "SKU-1042", "location": "Aisle 4", "instructions": "Quarantine it"},
    )
    stored = workflow.get_ticket(ticket["ticket_id"])
    assert stored["location"] == "Aisle 4" and stored["finding_type"] == "box_damage" and stored["version"] == 1
    assert workflow.history(ticket["ticket_id"])[0]["type"] == "created"
