import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import audit, workflow
from app.agent import orchestrator as orch
from app.agent import tools
from app.config import get_settings
from app.main import app
from app.pipeline import scan as scan_mod
from app.schemas import BBox, DetectedObject, Finding, ImageSize, ScanResult

SCAN_ID = "0123456789abcdef"
client = TestClient(app)
BOX = BBox(x1=0, y1=0, x2=5, y2=5)


def box(i: int, confidence: float = 0.9) -> DetectedObject:
    return DetectedObject(id=f"box_{i}", label="box", bbox=BOX, confidence=confidence, mask_svg="")


def save_scan(world, boxes, findings=(), uninspected=()):
    scan = ScanResult(scan_id=SCAN_ID, image_size=ImageSize(width=100, height=80), objects=list(boxes),
                      findings=list(findings), timings_ms={}, uninspected_boxes=list(uninspected))
    (world / "cache" / f"{SCAN_ID}.json").write_text(scan.model_dump_json())
    return scan


def damaged(i: int) -> Finding:
    return Finding(id=f"dmg_box_{i}", type="box_damage", severity="medium", confidence=0.9, bbox=BOX, description="dent")


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_mod, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(tools, "DATA_DIR", tmp_path)
    (tmp_path / "cache").mkdir()
    (tmp_path / "inventory.json").write_text(json.dumps({
        "SKU-1042": {"available": 25, "quarantine": 0, "reorder_point": 24},
        "SKU-OLD": {"available": 3, "quarantine": 1},
    }))
    return tmp_path


def reconcile(**body):
    return client.post(f"/scan/{SCAN_ID}/reconcile", json={"sku": "SKU-1042", **body})


def test_board_rows_have_totals_and_low_stock_with_a_default_reorder_point(world):
    rows = {r["sku"]: r for r in client.get("/inventory/board").json()}
    assert rows["SKU-1042"] == {"sku": "SKU-1042", "name": None, "available": 25, "quarantine": 0, "total": 25, "reorder_point": 24, "low_stock": False}
    assert rows["SKU-OLD"]["reorder_point"] == tools.DEFAULT_REORDER_POINT and rows["SKU-OLD"]["low_stock"] is True
    assert client.get("/inventory").json()["SKU-OLD"] == {"available": 3, "quarantine": 1}  # legacy shape untouched


def test_board_is_empty_without_inventory(world):
    (world / "inventory.json").write_text("{}")
    assert client.get("/inventory/board").json() == []


def test_count_comes_from_masks_and_excludes_damaged_boxes(world):
    save_scan(world, [box(i) for i in range(23)], [damaged(1)])
    count = client.get(f"/scan/{SCAN_ID}/count").json()
    assert (count["boxes"], count["damaged"], count["counted"], count["needs_review"]) == (23, 1, 22, False)
    assert client.get(f"/scan/{SCAN_ID}/count", params={"exclude_damaged": False}).json()["counted"] == 23


def test_low_confidence_or_uninspected_boxes_need_review(world):
    save_scan(world, [box(1), box(2, confidence=0.3)])
    assert client.get(f"/scan/{SCAN_ID}/count").json()["needs_review"] is True
    save_scan(world, [box(1)], uninspected=["box_1"])
    assert client.get(f"/scan/{SCAN_ID}/count").json()["needs_review"] is True


def test_reconcile_sets_available_once_and_audits(world):
    save_scan(world, [box(i) for i in range(23)], [damaged(1)])
    first = reconcile()
    assert first.status_code == 200
    assert first.json()["available"] == 22 and first.json()["change"] == -3 and first.json()["previous_available"] == 25
    again = reconcile()
    assert again.json() == first.json() and again.headers["Idempotent-Replay"] == "true"
    inv = tools.read_inventory()["SKU-1042"]
    assert inv == {"available": 22, "quarantine": 0, "reorder_point": 24}
    rows = {r["sku"]: r for r in client.get("/inventory/board").json()}
    assert rows["SKU-1042"]["low_stock"] is True
    executed = [e for e in audit.read_all() if e["tool"] == "set_inventory_count"]
    assert [e["outcome"] for e in executed] == ["executed"]


def test_double_click_reconcile_changes_stock_exactly_once(world):
    save_scan(world, [box(i) for i in range(20)])
    with ThreadPoolExecutor(6) as pool:
        codes = list(pool.map(lambda _: reconcile().status_code, range(6)))
    assert codes == [200] * 6 and tools.read_inventory()["SKU-1042"]["available"] == 20
    assert sum(e["tool"] == "set_inventory_count" and e["outcome"] == "executed" for e in audit.read_all()) == 1


def test_a_second_different_count_for_the_same_sku_and_scan_is_rejected(world):
    save_scan(world, [box(i) for i in range(20)])
    reconcile()
    assert reconcile(exclude_damaged=False).status_code == 200  # no damaged boxes: same count, same result
    save_scan(world, [box(i) for i in range(21)])
    assert reconcile().status_code == 409


def test_over_cap_changes_are_rejected_and_audited(world, monkeypatch):
    monkeypatch.setenv("INVENTORY_MAX_ADJUST", "2")
    get_settings.cache_clear()
    save_scan(world, [box(i) for i in range(20)])
    response = reconcile()
    assert response.status_code == 422 and "limit" in response.json()["detail"]
    assert tools.read_inventory()["SKU-1042"]["available"] == 25
    assert [e["outcome"] for e in audit.read_all() if e["tool"] == "set_inventory_count"] == ["rejected"]


def test_needs_review_counts_require_explicit_confirmation(world):
    save_scan(world, [box(i, confidence=0.4) for i in range(24)])
    assert reconcile().status_code == 409
    assert reconcile(confirm_needs_review=True).status_code == 200


def test_unknown_sku_and_missing_scan_are_rejected(world):
    save_scan(world, [box(1)])
    assert client.post(f"/scan/{SCAN_ID}/reconcile", json={"sku": "SKU-NOPE"}).status_code == 422
    assert client.post("/scan/fedcba9876543210/reconcile", json={"sku": "SKU-1042"}).status_code == 404
    assert client.post(f"/scan/{SCAN_ID}/reconcile", json={"sku": "<script>"}).status_code == 422


@pytest.mark.parametrize("counted", [-1, True, 1.5, "3"])
def test_set_inventory_count_rejects_bad_counts(world, counted):
    with pytest.raises(tools.ToolArgumentError):
        tools.set_inventory_count("SKU-1042", counted, "test", max_adjust=20)


def test_adjust_inventory_keeps_reorder_point(world):
    tools.adjust_inventory("SKU-1042", 2, "damaged")
    assert tools.read_inventory()["SKU-1042"] == {"available": 23, "quarantine": 2, "reorder_point": 24}


def test_approval_details_reach_the_ticket_and_the_model_cannot_set_them(world, monkeypatch):
    monkeypatch.setenv("AGENT_RUNTIME", "plain")
    get_settings.cache_clear()
    save_scan(world, [box(1)], [damaged(1)])
    monkeypatch.setattr(orch, "default_step", lambda messages: [] if len(messages) > 2 else [
        orch.ToolCall("create_incident_ticket", dict(
            finding_id="dmg_box_1", title="t", severity="medium", assignee_team="Maintenance", description="d",
        )),
    ])
    response = client.post(f"/scan/{SCAN_ID}/approve", json={
        "finding_ids": ["dmg_box_1"], "skus": {"dmg_box_1": "SKU-1042"},
        "details": {"dmg_box_1": {"location": "Aisle 4", "instructions": "Rewrap"}, "other": {"location": "ignored"}},
    })
    assert response.status_code == 200
    [ticket] = workflow.list_tickets()
    assert (ticket["location"], ticket["instructions"], ticket["sku"], ticket["finding_type"]) == (
        "Aisle 4", "Rewrap", "SKU-1042", "box_damage",
    )


def test_seed_inventory_is_varied_valid_and_covers_the_demo_photos():
    seed = json.loads(tools.SEED_INVENTORY.read_text())
    assert len(seed) >= 15
    for sku, item in seed.items():
        assert sku.startswith("SKU-") and item["name"] and item["quarantine"] == 0
        assert item["available"] >= 0 and item["reorder_point"] > 0
    assert any(i["available"] < i["reorder_point"] for i in seed.values())  # the reorder badge shows out of the box
    manifest = json.loads((tools.BACKEND_DIR.parent / "demo_images" / "manifest.json").read_text())
    demo_skus = {e["sku"] for e in manifest if e.get("sku")}
    assert demo_skus <= set(seed)
