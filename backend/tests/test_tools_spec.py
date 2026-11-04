"""Spec for app/agent/tools.py (Phase 2): the three side-effecting tools, with arguments validated by Pydantic.

Planned interface (plain functions returning dicts; LangChain wrappers sit on top):
    DATA_DIR                      module-level Path; tests monkeypatch it
    create_incident_ticket(finding_id, title, severity, assignee_team, description, crop=None) -> {"ticket_id", "pdf_path", ...}
    adjust_inventory(sku, delta, reason) -> {"sku", "available", "quarantine"}
    log_compliance_event(category, location, severity, note) -> {"event_id", ...}
    ToolArgumentError(ValueError) for invalid arguments
Teams are Maintenance / Safety / Inventory; severities low / medium / high.
"""

import json
import re
from pathlib import Path

import pytest
from spec_support import need

tools = need("app.agent.tools", "create_incident_ticket", "adjust_inventory", "log_compliance_event", "ToolArgumentError")


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "DATA_DIR", tmp_path)
    (tmp_path / "inventory.json").write_text(json.dumps({"SKU-1": {"available": 10, "quarantine": 0}, "SKU-2": {"available": 0, "quarantine": 1}}))
    return tmp_path


def ticket(**overrides):
    args = dict(finding_id="dmg_box_1", title="Crushed corner on SKU-1", severity="medium", assignee_team="Maintenance", description="Box 1 is damaged.")
    args.update(overrides)
    return tools.create_incident_ticket(**args)


# ---- tickets --------------------------------------------------------------------------------------------------

def test_ticket_is_a_real_pdf_on_disk(data_dir):
    result = ticket()
    assert Path(result["pdf_path"]).parent == data_dir / "tickets"
    assert Path(result["pdf_path"]).read_bytes().startswith(b"%PDF")


def test_ticket_ids_are_unique_and_url_safe():
    ids = {ticket()["ticket_id"] for _ in range(25)}
    assert len(ids) == 25 and all(re.fullmatch(r"[A-Za-z0-9_-]+", i) for i in ids)


@pytest.mark.parametrize("severity", ["", "urgent", "LOW", None])
def test_invalid_severity_is_rejected(severity):
    with pytest.raises(tools.ToolArgumentError):
        ticket(severity=severity)


@pytest.mark.parametrize("team", ["", "Janitors", "maintenance", None])
def test_invalid_team_is_rejected(team):
    with pytest.raises(tools.ToolArgumentError):
        ticket(assignee_team=team)


@pytest.mark.parametrize("team", ["Maintenance", "Safety", "Inventory"])
def test_each_team_is_accepted(team):
    assert ticket(assignee_team=team)["ticket_id"]


@pytest.mark.parametrize("field", ["title", "description", "finding_id"])
def test_blank_required_text_is_rejected(field):
    with pytest.raises(tools.ToolArgumentError):
        ticket(**{field: "   "})


def test_overlong_text_is_capped_not_rejected_and_not_unbounded(data_dir):
    result = ticket(title="T" * 10_000, description="D" * 100_000)
    assert len(result.get("title", "T" * 200)) <= 200
    assert (data_dir / "tickets").exists()


@pytest.mark.parametrize(
    "dirty",
    ["<script>alert(1)</script>", "[click](http://evil.test)", "visit http://evil.test now", "<img src=x onerror=alert(1)> photo", "<b>bold</b>"],
)
def test_model_written_text_is_sanitised_before_it_reaches_pdf_and_logs(dirty, data_dir):
    result = ticket(description=dirty)
    stored = json.dumps(result)
    assert "<script" not in stored and "onerror" not in stored and "http://evil.test" not in stored
    assert "<b>" not in stored


def test_unicode_text_renders_without_error():
    assert ticket(title="Déchirure — 破損 ✓", description="Caja dañada")["ticket_id"]


def test_ticket_with_crop_embeds_the_image(data_dir):
    from PIL import Image

    big = ticket(crop=Image.new("RGB", (60, 60), (200, 10, 10)))
    plain = ticket()
    assert (data_dir / "tickets" / f"{big['ticket_id']}.pdf").stat().st_size > (data_dir / "tickets" / f"{plain['ticket_id']}.pdf").stat().st_size


# ---- inventory ------------------------------------------------------------------------------------------------

def read_inventory(data_dir):
    return json.loads((data_dir / "inventory.json").read_text())


def test_damaged_units_move_from_available_to_quarantine(data_dir):
    result = tools.adjust_inventory("SKU-1", 2, "crushed")
    assert (result["available"], result["quarantine"]) == (8, 2)
    assert read_inventory(data_dir)["SKU-1"] == {"available": 8, "quarantine": 2}


def test_total_units_are_conserved(data_dir):
    before = sum(v["available"] + v["quarantine"] for v in read_inventory(data_dir).values())
    tools.adjust_inventory("SKU-1", 4, "x")
    assert sum(v["available"] + v["quarantine"] for v in read_inventory(data_dir).values()) == before


@pytest.mark.parametrize("delta", [0, -1, 1.5, "1", None, True])
def test_delta_must_be_a_positive_integer(delta):
    with pytest.raises(tools.ToolArgumentError):
        tools.adjust_inventory("SKU-1", delta, "x")


def test_cannot_quarantine_more_than_is_available(data_dir):
    with pytest.raises(tools.ToolArgumentError):
        tools.adjust_inventory("SKU-1", 11, "x")
    assert read_inventory(data_dir)["SKU-1"] == {"available": 10, "quarantine": 0}


def test_empty_stock_cannot_go_negative():
    with pytest.raises(tools.ToolArgumentError):
        tools.adjust_inventory("SKU-2", 1, "x")


def test_unknown_sku_is_an_error_and_creates_nothing(data_dir):
    with pytest.raises(tools.ToolArgumentError):
        tools.adjust_inventory("SKU-NOPE", 1, "x")
    assert "SKU-NOPE" not in read_inventory(data_dir)


def test_concurrent_adjustments_never_lose_updates(data_dir):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(lambda _: tools.adjust_inventory("SKU-1", 1, "x"), range(10)))
    assert read_inventory(data_dir)["SKU-1"] == {"available": 0, "quarantine": 10}


# ---- compliance log -------------------------------------------------------------------------------------------

def test_compliance_event_is_appended_with_a_timestamp(data_dir):
    tools.log_compliance_event("walkway_obstruction", "Aisle 4", "high", "Box blocks tape")
    tools.log_compliance_event("walkway_obstruction", "Aisle 5", "low", "Second")
    log = json.loads((data_dir / "compliance_log.json").read_text())
    assert [e["location"] for e in log] == ["Aisle 4", "Aisle 5"] and all("timestamp" in e for e in log)


@pytest.mark.parametrize("bad", [dict(severity="severe"), dict(category=""), dict(location=" "), dict(note="")])
def test_invalid_compliance_arguments_are_rejected(bad):
    args = dict(category="walkway_obstruction", location="Aisle 4", severity="high", note="n")
    args.update(bad)
    with pytest.raises(tools.ToolArgumentError):
        tools.log_compliance_event(**args)


def test_compliance_note_is_sanitised():
    result = tools.log_compliance_event("c", "l", "low", "<script>x</script> see http://evil.test")
    assert "<script" not in json.dumps(result)


@pytest.mark.parametrize("markup_only", ["<img src=x onerror=alert(1)>", "<b></b>", "http://evil.test", "[](http://evil.test)"])
def test_text_that_is_only_markup_is_rejected_not_stored_blank(markup_only):
    with pytest.raises(tools.ToolArgumentError, match="no usable text"):
        ticket(description=markup_only)
