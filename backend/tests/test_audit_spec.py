"""Spec for app/audit.py (Phase 5 MUST). Append-only record of every executed or rejected agent action.

Planned interface:
    AUDIT_PATH                       module-level Path (tests monkeypatch it)
    record(scan_id, finding_id, tool, arguments, outcome, reason=None) -> dict   outcome in {"executed", "rejected"}
    read_all() -> list[dict]         oldest first
Entries never contain image data.
"""

import json
import re
from datetime import datetime
from threading import Thread

import pytest
from spec_support import need

audit = need("app.audit", "record", "read_all", "AUDIT_PATH")


@pytest.fixture(autouse=True)
def audit_path(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "AUDIT_PATH", tmp_path / "audit_log.jsonl")
    return tmp_path / "audit_log.jsonl"


def test_entry_has_the_documented_fields():
    entry = audit.record("0123456789abcdef", "dmg_box_1", "create_incident_ticket", {"title": "t"}, "executed")
    assert {"timestamp", "scan_id", "finding_id", "tool", "arguments", "outcome"} <= set(entry)
    assert audit.read_all() == [entry]


def test_timestamp_is_utc_iso8601():
    ts = audit.record("s", "f", "t", {}, "executed")["timestamp"]
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    assert parsed.utcoffset().total_seconds() == 0


def test_rejections_are_logged_with_their_reason():
    audit.record("s", "f", "delete_inventory", {}, "rejected", reason="tool not allowed")
    [entry] = audit.read_all()
    assert entry["outcome"] == "rejected" and entry["reason"] == "tool not allowed"


@pytest.mark.parametrize("outcome", ["", "maybe", None])
def test_unknown_outcomes_are_refused(outcome):
    with pytest.raises(ValueError):
        audit.record("s", "f", "t", {}, outcome)


def test_log_is_append_only_and_ordered():
    for i in range(5):
        audit.record("s", f"f{i}", "t", {"i": i}, "executed")
    assert [e["finding_id"] for e in audit.read_all()] == [f"f{i}" for i in range(5)]


def test_image_data_is_never_written(audit_path):
    audit.record("s", "f", "t", {"crop": "data:image/jpeg;base64," + "A" * 5000, "note": "ok"}, "executed")
    raw = audit_path.read_text()
    assert "data:image" not in raw and "A" * 200 not in raw
    assert json.loads(raw)["arguments"]["note"] == "ok"


def test_each_entry_is_one_line_even_with_newlines_in_values(audit_path):
    audit.record("s", "f", "t", {"note": "line1\nline2\r\nline3"}, "executed")
    assert len(audit_path.read_text().splitlines()) == 1


def test_concurrent_recording_loses_nothing():
    threads = [Thread(target=lambda n=n: [audit.record("s", f"f{n}-{i}", "t", {}, "executed") for i in range(20)]) for n in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(audit.read_all()) == 200


def test_reading_an_absent_log_gives_an_empty_list():
    assert audit.read_all() == []


def test_secrets_in_arguments_are_not_persisted(audit_path):
    audit.record("s", "f", "t", {"api_key": "sk-" + "a" * 26}  # built at runtime so the repo secret scan stays clean, "executed")
    assert not re.search(r"sk-[A-Za-z0-9]{20,}", audit_path.read_text())
