"""Spec for retention and the pinned demo store (Phase 4 + Phase 5 MUST): app/retention.py, DELETE /scan/{id}, DEMO_MODE=replay.

Planned interface:
    purge_expired(cache_dir, ttl_days, *, now=None) -> list[str]      removes scan JSONs older than the TTL, returns their ids
    DEMO_DIR                                                          pinned store (data/demo); never purged, never deleted via the API
    settings: cache_ttl_days (default 7), demo_mode ("" | "replay")
"""

import json
import os
import time
from pathlib import Path

import pytest
from spec_support import need

retention = need("app.retention", "purge_expired")

DAY = 86400


def entry(directory: Path, scan_id: str, age_days: float, suffix=".json") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{scan_id}{suffix}"
    path.write_text("{}")
    t = time.time() - age_days * DAY
    os.utime(path, (t, t))
    return path


def test_default_ttl_is_seven_days():
    import app.config as config

    assert config.Settings(_env_file=None).cache_ttl_days == 7


def test_expired_entries_are_removed_and_fresh_ones_kept(tmp_path):
    old = entry(tmp_path, "0" * 16, 8)
    fresh = entry(tmp_path, "1" * 16, 6)
    assert retention.purge_expired(tmp_path, 7) == ["0" * 16]
    assert not old.exists() and fresh.exists()


def test_age_exactly_at_the_ttl_is_kept(tmp_path):
    now = 1_000_000_000.0
    path = entry(tmp_path, "a" * 16, 0)
    os.utime(path, (now - 7 * DAY, now - 7 * DAY))
    assert retention.purge_expired(tmp_path, 7, now=now) == []
    assert path.exists()


def test_missing_cache_dir_is_not_an_error(tmp_path):
    assert retention.purge_expired(tmp_path / "absent", 7) == []


def test_only_scan_json_files_are_touched(tmp_path):
    keep = [entry(tmp_path, "notes", 100, ".txt"), entry(tmp_path, ".gitkeep", 100, ""), entry(tmp_path, "b" * 16, 100, ".pdf")]
    retention.purge_expired(tmp_path, 7)
    assert all(p.exists() for p in keep)


def test_subdirectories_are_never_descended_into(tmp_path):
    nested = entry(tmp_path / "sub", "c" * 16, 100)
    retention.purge_expired(tmp_path, 7)
    assert nested.exists()


def test_pinned_demo_store_is_never_purged_even_when_passed_in_by_mistake(tmp_path, monkeypatch):
    demo = tmp_path / "demo"
    pinned = entry(demo, "d" * 16, 365)
    monkeypatch.setattr(retention, "DEMO_DIR", demo, raising=False)
    retention.purge_expired(demo, 7)
    assert pinned.exists()


def test_ttl_zero_or_negative_is_refused_rather_than_wiping_everything(tmp_path):
    entry(tmp_path, "e" * 16, 1)
    with pytest.raises(ValueError):
        retention.purge_expired(tmp_path, 0)
    with pytest.raises(ValueError):
        retention.purge_expired(tmp_path, -3)


def test_symlinks_are_not_followed_out_of_the_cache(tmp_path):
    outside = entry(tmp_path / "outside", "f" * 16, 100)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / ("9" * 16 + ".json")).symlink_to(outside)
    retention.purge_expired(cache, 7)
    assert outside.exists()


# ---- API ------------------------------------------------------------------------------------------------------

def _delete_route_exists(main):
    return "delete" in main.app.openapi()["paths"].get("/scan/{scan_id}", {})


@pytest.fixture
def api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    main = need("app.main", "app")
    scan_mod = need("app.pipeline.scan", "CACHE_DIR")
    if not _delete_route_exists(main):
        pytest.skip("DELETE /scan/{scan_id} is not implemented yet")
    monkeypatch.setattr(scan_mod, "CACHE_DIR", tmp_path / "cache")
    return TestClient(main.app), tmp_path


def test_delete_removes_a_cached_scan_and_is_then_404(api):
    client, tmp = api
    path = entry(tmp / "cache", "0123456789abcdef", 0)
    assert client.delete("/scan/0123456789abcdef").status_code in (200, 204)
    assert not path.exists()
    assert client.get("/scan/0123456789abcdef").status_code == 404
    assert client.delete("/scan/0123456789abcdef").status_code == 404


def test_delete_rejects_malformed_ids(api):
    client, _ = api
    assert client.delete("/scan/..%2Fx").status_code in (404, 422)
    assert client.delete("/scan/NOT-HEX").status_code == 422


def test_delete_never_touches_the_pinned_demo_store(api, monkeypatch):
    client, tmp = api
    pinned = entry(tmp / "demo", "0123456789abcdef", 0)
    monkeypatch.setattr(retention, "DEMO_DIR", tmp / "demo", raising=False)
    assert client.delete("/scan/0123456789abcdef").status_code == 404
    assert pinned.exists()
