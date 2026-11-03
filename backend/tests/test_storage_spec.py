"""Spec for app/storage.py (Phase 2). Skipped until the module exists.

Planned interface (veto or rename freely; tests follow the plan wording "atomic JSON / JSONL writes with per-file locks"):
    read_json(path, default=None)      -> parsed value, or `default` if the file is missing
    write_json(path, data) -> None     temp file + rename, creates parent directories
    append_jsonl(path, record) -> None one line per record, locked per file
    read_jsonl(path) -> list[dict]     skips a torn trailing line instead of failing
"""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from spec_support import need

storage = need("app.storage", "read_json", "write_json", "append_jsonl", "read_jsonl")


def test_read_json_missing_file_returns_default(tmp_path):
    assert storage.read_json(tmp_path / "none.json", default={"a": 1}) == {"a": 1}


def test_write_then_read_round_trips_unicode(tmp_path):
    path = tmp_path / "d" / "x.json"
    storage.write_json(path, {"sku": "SKU-1", "note": "Déchirure 破損"})
    assert storage.read_json(path) == {"sku": "SKU-1", "note": "Déchirure 破損"}


def test_write_creates_missing_parent_directories(tmp_path):
    storage.write_json(tmp_path / "a" / "b" / "c.json", [])
    assert (tmp_path / "a" / "b" / "c.json").is_file()


def test_write_leaves_no_temp_files(tmp_path):
    storage.write_json(tmp_path / "x.json", {"a": 1})
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


def test_failed_rename_keeps_the_old_content_and_cleans_up(tmp_path, monkeypatch):
    path = tmp_path / "x.json"
    storage.write_json(path, {"v": 1})
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        storage.write_json(path, {"v": 2})
    monkeypatch.undo()
    assert json.loads(path.read_text()) == {"v": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


def test_unserialisable_data_never_clobbers_the_file(tmp_path):
    path = tmp_path / "x.json"
    storage.write_json(path, {"v": 1})
    with pytest.raises(TypeError):
        storage.write_json(path, {"v": object()})
    assert json.loads(path.read_text()) == {"v": 1}


def test_concurrent_appends_never_interleave_or_lose_lines(tmp_path):
    path = tmp_path / "log.jsonl"
    per_thread, threads = 25, 16

    def work(t):
        for i in range(per_thread):
            storage.append_jsonl(path, {"t": t, "i": i, "pad": "x" * 2000})

    with ThreadPoolExecutor(max_workers=threads) as pool:
        list(pool.map(work, range(threads)))
    lines = path.read_text().splitlines()
    assert len(lines) == per_thread * threads
    assert len({(r["t"], r["i"]) for r in map(json.loads, lines)}) == per_thread * threads


def test_concurrent_writers_always_leave_a_readable_file(tmp_path):
    path = tmp_path / "inventory.json"
    stop = threading.Event()
    seen_errors = []

    def reader():
        while not stop.is_set():
            try:
                if path.exists():
                    json.loads(path.read_text())
            except json.JSONDecodeError as exc:  # a torn read means the write was not atomic
                seen_errors.append(exc)

    t = threading.Thread(target=reader)
    t.start()
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda n: storage.write_json(path, {"n": n, "pad": "y" * 50_000}), range(200)))
    finally:
        stop.set()
        t.join()
    assert not seen_errors


def test_read_jsonl_skips_a_torn_last_line(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text('{"a": 1}\n{"a": 2}\n{"a": ')
    assert storage.read_jsonl(path) == [{"a": 1}, {"a": 2}]


def test_read_jsonl_missing_file_is_empty(tmp_path):
    assert storage.read_jsonl(tmp_path / "none.jsonl") == []


def test_append_is_append_only(tmp_path):
    path = tmp_path / "log.jsonl"
    storage.append_jsonl(path, {"n": 1})
    storage.append_jsonl(path, {"n": 2})
    assert storage.read_jsonl(path) == [{"n": 1}, {"n": 2}]


def test_different_files_do_not_share_a_lock(tmp_path):
    # A slow writer on one file must not block writes to another (per-file locks).
    done = threading.Event()
    t = threading.Thread(target=lambda: (storage.write_json(tmp_path / "a.json", {"pad": "z" * 5_000_000}), done.set()))
    t.start()
    storage.write_json(tmp_path / "b.json", {"ok": True})
    t.join()
    assert done.is_set() and storage.read_json(tmp_path / "b.json") == {"ok": True}
