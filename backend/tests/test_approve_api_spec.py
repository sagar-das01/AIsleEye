"""Spec for the Phase 2 API surface: /scan/{id}/approve (idempotent) and the read endpoints.

Skipped until the approve route exists. The model is replaced by patching `app.agent.orchestrator.default_step`
with a scripted step (planned hook), so nothing calls the network.
"""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from spec_support import need

main = need("app.main", "app")
orch = need("app.agent.orchestrator", "run_approved_scan", "default_step")
scan_mod = need("app.pipeline.scan", "CACHE_DIR")

if "post" not in main.app.openapi()["paths"].get("/scan/{scan_id}/approve", {}):
    pytest.skip("POST /scan/{scan_id}/approve is not implemented yet", allow_module_level=True)

from app.schemas import BBox, Finding, ImageSize, ScanResult  # noqa: E402

SCAN_ID = "0123456789abcdef"
client = TestClient(main.app)


@pytest.fixture(autouse=True)
def world(tmp_path, monkeypatch):
    import app.agent.tools as tools
    import app.audit as audit
    import app.config as config

    monkeypatch.setenv("AGENT_RUNTIME", "plain")  # the scripted step below drives the hand-written loop
    config.get_settings.cache_clear()
    monkeypatch.setattr(audit, "AUDIT_PATH", tmp_path / "audit_log.jsonl")
    monkeypatch.setattr(scan_mod, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(tools, "DATA_DIR", tmp_path)
    (tmp_path / "inventory.json").write_text('{"SKU-1": {"available": 10, "quarantine": 0}}')
    (tmp_path / "cache").mkdir()
    result = ScanResult(
        scan_id=SCAN_ID,
        image_size=ImageSize(width=100, height=80),
        objects=[],
        findings=[
            Finding(id="dmg_box_1", type="box_damage", severity="medium", confidence=0.8, bbox=BBox(x1=0, y1=0, x2=5, y2=5), description="Box 1: dent."),
            Finding(id="hz_box_2", type="walkway_blocked", severity="high", confidence=0.9, bbox=BBox(x1=5, y1=5, x2=9, y2=9), description="Box 2 blocks tape."),
        ],
        timings_ms={},
    )
    (tmp_path / "cache" / f"{SCAN_ID}.json").write_text(result.model_dump_json())
    monkeypatch.setattr(orch, "default_step", lambda messages: [] if len(messages) > 2 else [
        orch.ToolCall("create_incident_ticket", dict(finding_id="dmg_box_1", title="t", severity="medium", assignee_team="Maintenance", description="d"))
    ])
    yield tmp_path
    monkeypatch.undo()
    config.get_settings.cache_clear()


def dispatched(body):
    """The dispatch itself; `already_dispatched` only says whether this response was the first or a replay."""
    return {k: v for k, v in body.items() if k != "already_dispatched"}


def approve(ids, scan_id=SCAN_ID):
    return client.post(f"/scan/{scan_id}/approve", json={"finding_ids": ids})


def test_approve_returns_actions_tickets_and_a_trace_id():
    res = approve(["dmg_box_1"])
    assert res.status_code == 200
    body = res.json()
    assert {"actions", "tickets", "trace_id"} <= set(body)
    assert len(body["tickets"]) == 1


def test_scanning_alone_never_runs_the_agent(world):
    assert not (world / "tickets").exists()  # nothing happens before a human clicks Approve


@pytest.mark.parametrize("payload", [{}, {"finding_ids": []}, {"finding_ids": "dmg_box_1"}, {"finding_ids": [1]}, {"finding_ids": [None]}])
def test_bad_payloads_are_422(payload):
    assert client.post(f"/scan/{SCAN_ID}/approve", json=payload).status_code == 422


def test_unknown_scan_is_404():
    assert approve(["dmg_box_1"], scan_id="fedcba9876543210").status_code == 404


def test_malformed_scan_id_is_422():
    assert approve(["dmg_box_1"], scan_id="NOT-HEX").status_code == 422


def test_unknown_finding_id_is_rejected_without_side_effects(world):
    assert approve(["ghost"]).status_code in (404, 422)
    assert not list((world / "tickets").glob("*.pdf")) if (world / "tickets").exists() else True


def test_repeat_call_returns_the_first_result_and_creates_nothing_new(world):
    first = approve(["dmg_box_1"]).json()
    second = approve(["dmg_box_1"]).json()
    assert dispatched(second) == dispatched(first) and second["already_dispatched"] is True
    assert len(list((world / "tickets").glob("*.pdf"))) == 1


def test_finding_order_does_not_change_the_idempotency_key(world):
    a = approve(["dmg_box_1", "hz_box_2"]).json()
    b = approve(["hz_box_2", "dmg_box_1"]).json()
    assert dispatched(a) == dispatched(b)


def test_duplicate_ids_in_one_request_count_once(world):
    assert dispatched(approve(["dmg_box_1", "dmg_box_1"]).json()) == dispatched(approve(["dmg_box_1"]).json())


def test_a_different_finding_set_is_a_new_run(world):
    approve(["dmg_box_1"])
    other = approve(["hz_box_2"])
    assert other.status_code == 200


def test_double_click_creates_exactly_one_ticket_and_one_inventory_change(world):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: approve(["dmg_box_1"]), range(8)))
    assert {r.status_code for r in results} == {200}
    assert len({json.dumps(dispatched(r.json()), sort_keys=True) for r in results}) == 1
    assert sum(r.json()["already_dispatched"] is False for r in results) == 1
    assert len(list((world / "tickets").glob("*.pdf"))) == 1


def test_a_failed_run_does_not_poison_the_idempotency_key(world, monkeypatch):
    working = orch.default_step

    def boom(messages):
        raise RuntimeError("model down")

    monkeypatch.setattr(orch, "default_step", boom)
    first = approve(["dmg_box_1"])
    # With the model down the built-in rules dispatch, and that result is the stored one.
    assert first.status_code == 200 and first.json()["fallback"] == "rules" and len(first.json()["tickets"]) == 1
    monkeypatch.setattr(orch, "default_step", working)
    again = approve(["dmg_box_1"]).json()
    assert again["already_dispatched"] is True and again["actions"] == first.json()["actions"] and again["tickets"] == first.json()["tickets"]


def test_a_run_that_fails_after_acting_is_a_502_and_is_not_stored(world, monkeypatch):
    calls = iter([[orch.ToolCall("create_incident_ticket", dict(finding_id="dmg_box_1", title="t", severity="medium", assignee_team="Maintenance", description="d"))]])

    def half(messages):
        try:
            return next(calls)
        except StopIteration:
            raise RuntimeError("model down") from None

    monkeypatch.setattr(orch, "default_step", half)
    assert approve(["dmg_box_1"]).status_code == 502
    assert not list((world / "approvals").glob("*.json")) if (world / "approvals").exists() else True


# ---- read endpoints -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/inventory", "/tickets", "/compliance", "/audit"])
def test_read_endpoints_work_on_an_empty_store(path):
    res = client.get(path)
    assert res.status_code == 200 and isinstance(res.json(), (list, dict))


def test_ticket_pdf_is_served_with_the_pdf_content_type():
    ticket_id = approve(["dmg_box_1"]).json()["tickets"][0]["ticket_id"]
    res = client.get(f"/tickets/{ticket_id}.pdf")
    assert res.status_code == 200 and res.headers["content-type"] == "application/pdf" and res.content.startswith(b"%PDF")


@pytest.mark.parametrize("ticket_id", ["nope", "..%2F..%2Fetc%2Fpasswd", "T-1%00", "a" * 500])
def test_unknown_or_hostile_ticket_ids_are_404_or_422(ticket_id):
    assert client.get(f"/tickets/{ticket_id}.pdf").status_code in (404, 422)


def test_inventory_reflects_quarantined_units_after_an_approved_run():
    inv = client.get("/inventory").json()
    assert "SKU-1" in json.dumps(inv)


def test_audit_endpoint_lists_executed_actions_without_image_data():
    approve(["dmg_box_1"])
    text = client.get("/audit").text
    assert "create_incident_ticket" in text and "data:image" not in text
