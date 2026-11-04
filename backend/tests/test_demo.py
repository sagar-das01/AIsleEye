import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import app.agent.orchestrator as orch
import app.agent.tools as tools
import app.audit as audit
import app.config as config
from app import main, replay
from app.agent import rules
from app.pipeline import preprocess
from app.pipeline import scan as scan_mod
from app.schemas import BBox, Finding, ImageSize, ScanResult

client = TestClient(main.app)
BOX = BBox(x1=0, y1=0, x2=5, y2=5)


def finding(fid, type_="box_damage", severity="medium", sku=None, description="Box: dent."):
    return Finding(id=fid, type=type_, severity=severity, confidence=0.9, bbox=BOX, description=description, sku=sku)


def make_scan(*findings):
    return ScanResult(scan_id="0123456789abcdef", image_size=ImageSize(width=10, height=10), objects=[], findings=list(findings), timings_ms={})


# ---- committed demo assets ------------------------------------------------------------------------------------------

def test_manifest_lists_photos_that_exist_with_unique_ids():
    entries = replay.load_manifest()
    assert 5 <= len(entries) <= 8
    assert len({e["id"] for e in entries}) == len(entries)
    for e in entries:
        assert replay.demo_image_path(e["id"]) is not None, e["id"]
        assert set(e["expected"]) == {"damaged", "blocked"} and e["title"] and e["description"]


def test_demo_set_covers_clean_damaged_blocked_and_mixed():
    expected = [e["expected"] for e in replay.load_manifest()]
    assert sum(1 for x in expected if x == {"damaged": 0, "blocked": 0}) >= 2
    assert sum(1 for x in expected if x["damaged"] and not x["blocked"]) >= 2
    assert sum(1 for x in expected if x["blocked"] and not x["damaged"]) >= 2
    assert any(x["damaged"] and x["blocked"] for x in expected)


def test_demo_photos_are_valid_jpegs_within_the_upload_limits():
    for e in replay.load_manifest():
        path = replay.demo_image_path(e["id"])
        assert path.stat().st_size < 1_000_000
        with Image.open(path) as img:
            assert img.format == "JPEG" and max(img.size) <= preprocess.MAX_SIDE


def test_every_demo_photo_has_a_pinned_result_that_matches_what_it_shows():
    for entry in replay.demo_entries():
        assert entry["pinned"], f"{entry['id']} has no pinned result: run scripts.freeze_demo"
        result = replay.load_pinned(entry["scan_id"])
        assert result.scan_id == entry["scan_id"]
        damaged = sum(f.type == "box_damage" for f in result.findings)
        blocked = sum(f.type == "walkway_blocked" for f in result.findings)
        assert {"damaged": damaged, "blocked": blocked} == entry["expected"], entry["id"]


def test_pinned_files_are_named_by_the_hash_of_their_photo_and_contain_no_timings():
    for entry in replay.demo_entries():
        path = replay.demo_image_path(entry["id"])
        assert entry["scan_id"] == hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        assert (replay.DEMO_DIR / f"{entry['scan_id']}.json").is_file()
        assert replay.load_pinned(entry["scan_id"]).timings_ms == {}


def test_pinned_results_carry_no_image_data_or_local_paths():
    for f in replay.DEMO_DIR.glob("*.json"):
        text = f.read_text()
        assert "data:image" not in text and "/Users/" not in text


def test_suggested_skus_exist_in_the_seed_inventory():
    seed = json.loads(tools.SEED_INVENTORY.read_text())
    for e in replay.load_manifest():
        if e["sku"]:
            assert e["sku"] in seed and e["expected"]["damaged"] > 0


def test_pinned_store_is_not_inside_the_cache_so_a_purge_cannot_reach_it():
    assert replay.DEMO_DIR not in scan_mod.CACHE_DIR.parents and scan_mod.CACHE_DIR not in replay.DEMO_DIR.parents
    assert replay.DEMO_DIR != scan_mod.CACHE_DIR


# ---- demo endpoints -------------------------------------------------------------------------------------------------

def test_demo_list_describes_each_photo_and_whether_it_is_pinned():
    body = client.get("/demo").json()
    assert {e["id"] for e in body} == {e["id"] for e in replay.load_manifest()}
    first = body[0]
    assert first["image_url"] == f"/demo/{first['id']}.jpg" and first["pinned"] is True and len(first["scan_id"]) == 16


def test_demo_photo_is_served_byte_for_byte_so_its_hash_matches_the_pinned_result():
    entry = replay.demo_entries()[0]
    res = client.get(entry["image_url"])
    assert res.status_code == 200 and res.headers["content-type"] == "image/jpeg"
    assert scan_mod.scan_id_for(res.content) == entry["scan_id"]


@pytest.mark.parametrize("name", ["nope", "..%2Fsecret", "A" * 5, "x" * 41, "a.b"])
def test_unknown_or_hostile_demo_names_are_refused(name):
    assert client.get(f"/demo/{name}.jpg").status_code in (404, 422)


def test_demo_list_survives_a_missing_manifest(monkeypatch, tmp_path):
    monkeypatch.setattr(replay, "DEMO_IMAGES_DIR", tmp_path)
    assert client.get("/demo").json() == []


# ---- replay mode and API fallback -----------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fresh_settings(monkeypatch):
    yield
    monkeypatch.undo()  # at teardown, so the env changes below never leak into other tests
    config.get_settings.cache_clear()


def use_env(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config.get_settings.cache_clear()


@pytest.fixture
def replay_env(monkeypatch):
    use_env(monkeypatch, DEMO_MODE="replay")


def test_replay_mode_serves_every_demo_photo_without_any_model(replay_env, monkeypatch):
    class NoNetwork:
        def __getattr__(self, name):
            raise AssertionError("network used in replay mode")

    for mod in ("app.pipeline.segment", "app.pipeline.inspect"):
        monkeypatch.setattr(f"{mod}.get_async_client", lambda: NoNetwork())
    for entry in replay.demo_entries():
        data = client.get(entry["image_url"]).content
        res = client.post("/scan", files={"image": ("d.jpg", data, "image/jpeg")})
        assert res.status_code == 200, entry["id"]
        assert res.json()["scan_id"] == entry["scan_id"] and res.json()["replay"] is True


def test_health_reports_the_demo_mode(replay_env):
    assert client.get("/health").json()["demo_mode"] == "replay"


def test_health_reports_live_mode_by_default():
    assert client.get("/health").json()["demo_mode"] == ""


def test_a_failing_model_api_falls_back_to_the_pinned_result_and_says_so(monkeypatch):
    import httpx
    import openai

    async def boom(*a, **k):
        raise openai.APIConnectionError(request=httpx.Request("POST", "https://x.test"))

    monkeypatch.setattr(main, "run_scan", boom)
    entry = replay.demo_entries()[0]
    data = client.get(entry["image_url"]).content
    res = client.post("/scan", files={"image": ("d.jpg", data, "image/jpeg")})
    assert res.status_code == 200 and res.json()["replay"] is True and res.json()["scan_id"] == entry["scan_id"]


def test_a_failing_model_api_is_still_a_502_for_a_photo_with_no_saved_result(monkeypatch):
    import httpx
    import openai

    async def boom(*a, **k):
        raise openai.APIConnectionError(request=httpx.Request("POST", "https://x.test"))

    monkeypatch.setattr(main, "run_scan", boom)
    import io

    buf = io.BytesIO()
    Image.new("RGB", (30, 20), (1, 2, 3)).save(buf, format="JPEG")
    assert client.post("/scan", files={"image": ("d.jpg", buf.getvalue(), "image/jpeg")}).status_code == 502


def test_live_results_are_not_marked_as_replays(monkeypatch):
    live = make_scan()

    async def fake(*a, **k):
        return live

    monkeypatch.setattr(main, "run_scan", fake)
    data = client.get(replay.demo_entries()[0]["image_url"]).content
    assert client.post("/scan", files={"image": ("d.jpg", data, "image/jpeg")}).json()["replay"] is False


def test_get_scan_falls_back_to_pinned_results_and_flags_them(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_mod, "CACHE_DIR", tmp_path / "empty-cache")
    entry = replay.demo_entries()[0]
    assert client.get(f"/scan/{entry['scan_id']}").json()["replay"] is True


# ---- rule-based dispatcher ------------------------------------------------------------------------------------------

def test_walkway_findings_go_to_safety_with_a_compliance_entry():
    calls = rules.plan(make_scan(finding("hz_box_1", "walkway_blocked", "high")), ["hz_box_1"])
    assert [(n, a.get("assignee_team")) for n, a in calls] == [("create_incident_ticket", "Safety"), ("log_compliance_event", None)]
    assert all(a["severity"] == "high" for _, a in calls)


def test_damage_goes_to_maintenance_and_quarantines_only_when_a_sku_is_known():
    plain = rules.plan(make_scan(finding("dmg_box_1")), ["dmg_box_1"])
    assert [n for n, _ in plain] == ["create_incident_ticket"] and plain[0][1]["assignee_team"] == "Maintenance"
    with_sku = rules.plan(make_scan(finding("dmg_box_1", sku="SKU-1042")), ["dmg_box_1"])
    assert [n for n, _ in with_sku] == ["create_incident_ticket", "adjust_inventory"]
    assert with_sku[1][1] == {"finding_id": "dmg_box_1", "sku": "SKU-1042", "delta": 1, "reason": "Damaged box quarantined"}


def test_only_approved_findings_are_planned_and_severity_is_unchanged():
    scan = make_scan(finding("a", severity="low"), finding("b", severity="high"))
    calls = rules.plan(scan, ["b"])
    assert [a["finding_id"] for _, a in calls] == ["b"] and calls[0][1]["severity"] == "high"


def test_long_descriptions_are_shortened_for_tickets():
    calls = rules.plan(make_scan(finding("a", description="x" * 2000)), ["a"])
    assert len(calls[0][1]["description"]) <= 400


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "DATA_DIR", tmp_path)
    monkeypatch.setattr(audit, "AUDIT_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(scan_mod, "CACHE_DIR", tmp_path / "cache")
    (tmp_path / "inventory.json").write_text(json.dumps({"SKU-1042": {"available": 3, "quarantine": 0}}))
    (tmp_path / "cache").mkdir()
    return tmp_path


def run(world, monkeypatch, runtime, scan, ids, skus=None):
    use_env(monkeypatch, AGENT_RUNTIME=runtime)
    return orch.run_approved_scan(scan, ids, skus=skus)


def test_rules_runtime_dispatches_through_the_same_guard_without_a_model(world, monkeypatch):
    monkeypatch.setattr(orch, "get_chat_model", lambda model=None: pytest.fail("the model must not be used"))
    scan = make_scan(finding("dmg_box_1"), finding("hz_box_2", "walkway_blocked", "high"))
    result = run(world, monkeypatch, "rules", scan, ["dmg_box_1", "hz_box_2"], skus={"dmg_box_1": "SKU-1042"})
    assert [a.status for a in result.actions] == ["executed"] * 4
    assert result.summary == "Dispatched 4 of 4 action(s) with the built-in rules." and result.error is None
    assert tools.read_inventory()["SKU-1042"] == {"available": 2, "quarantine": 1}
    assert len(list((world / "tickets").glob("*.pdf"))) == 2
    assert [e["outcome"] for e in audit.read_all()] == ["executed"] * 4


def test_rules_cannot_exceed_what_the_guard_allows(world, monkeypatch):
    (world / "inventory.json").write_text(json.dumps({"SKU-1042": {"available": 0, "quarantine": 0}}))
    result = run(world, monkeypatch, "rules", make_scan(finding("dmg_box_1")), ["dmg_box_1"], skus={"dmg_box_1": "SKU-1042"})
    assert [a.status for a in result.actions] == ["executed", "rejected"]
    assert "Stock" in result.actions[1].reason


def test_replay_mode_forces_the_rules_even_when_a_model_runtime_is_configured(world, monkeypatch):
    use_env(monkeypatch, DEMO_MODE="replay")
    monkeypatch.setattr(orch, "get_chat_model", lambda model=None: pytest.fail("offline demo must not call the model"))
    result = run(world, monkeypatch, "langchain", make_scan(finding("dmg_box_1")), ["dmg_box_1"])
    assert [a.status for a in result.actions] == ["executed"]


def test_approving_in_replay_mode_works_end_to_end_offline(world, replay_env, monkeypatch):
    monkeypatch.setattr(orch, "get_chat_model", lambda model=None: pytest.fail("offline demo must not call the model"))
    scan = make_scan(finding("dmg_box_1"), finding("hz_box_2", "walkway_blocked", "high"))
    (world / "cache" / f"{scan.scan_id}.json").write_text(scan.model_dump_json())
    res = client.post(f"/scan/{scan.scan_id}/approve", json={"finding_ids": ["dmg_box_1", "hz_box_2"]})
    assert res.status_code == 200 and len(res.json()["tickets"]) == 2 and res.json()["fallback"] is None
    assert client.post(f"/scan/{scan.scan_id}/approve", json={"finding_ids": ["hz_box_2", "dmg_box_1"]}).json() == res.json()


def test_demo_photos_dispatch_correctly_from_their_pinned_results(world, monkeypatch):
    for entry in replay.demo_entries():
        scan = replay.load_pinned(entry["scan_id"])
        ids = [f.id for f in scan.findings]
        skus = {f.id: entry["sku"] for f in scan.findings if f.type == "box_damage" and entry["sku"]}
        result = run(world, monkeypatch, "rules", scan, ids, skus=skus)
        assert all(a.status == "executed" for a in result.actions), entry["id"]
        expected_tickets = len(ids)
        assert sum(a.tool == "create_incident_ticket" for a in result.actions) == expected_tickets
        (world / "tickets" / "index.jsonl").unlink(missing_ok=True)  # each photo starts without open tickets
        (world / "inventory.json").write_text(json.dumps({s: {"available": 40, "quarantine": 0} for s in json.loads(tools.SEED_INVENTORY.read_text())}))


def test_a_model_outage_during_approval_is_flagged_in_the_response(world, monkeypatch):
    scan = make_scan(finding("dmg_box_1"))
    (world / "cache" / f"{scan.scan_id}.json").write_text(scan.model_dump_json())

    def boom(model=None):
        raise RuntimeError("api down")

    use_env(monkeypatch, AGENT_RUNTIME="langchain")
    monkeypatch.setattr(orch, "get_chat_model", boom)
    body = client.post(f"/scan/{scan.scan_id}/approve", json={"finding_ids": ["dmg_box_1"]}).json()
    assert body["fallback"] == "rules" and [a["status"] for a in body["actions"]] == ["executed"]


def test_replay_flag_defaults_to_false_and_old_cache_files_still_load():
    old = make_scan().model_dump()
    old.pop("replay")
    assert ScanResult.model_validate(old).replay is False


def test_a_pinned_demo_scan_can_be_approved_without_ever_being_in_the_cache(world, monkeypatch):
    use_env(monkeypatch, AGENT_RUNTIME="rules")
    entry = next(e for e in replay.demo_entries() if e["expected"]["blocked"])
    res = client.post(f"/scan/{entry['scan_id']}/approve", json={"finding_ids": [f.id for f in replay.load_pinned(entry["scan_id"]).findings]})
    assert res.status_code == 200 and len(res.json()["tickets"]) == entry["expected"]["blocked"]
    assert not (world / "cache" / f"{entry['scan_id']}.json").exists()


def test_approve_and_get_agree_on_where_a_scan_comes_from(world):
    entry = replay.demo_entries()[0]
    assert replay.find_scan(entry["scan_id"]).replay is True
    assert replay.find_scan("0000000000000000") is None
