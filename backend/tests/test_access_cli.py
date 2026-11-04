import json
import stat
from datetime import UTC, datetime, timedelta

import pytest

import app.config as config
from app.auth import Directory, Grant, Permission, Role, can_view_ticket, has_permission
from app.storage import read_json
from scripts import access

WAREHOUSE = "warehouse-a"


@pytest.fixture
def path(tmp_path, monkeypatch):
    monkeypatch.setenv("RBAC_WAREHOUSE_ID", WAREHOUSE)
    config.get_settings.cache_clear()
    return tmp_path / "access.json"


def run(path, *argv):
    return access.main(["--file", str(path), *argv])


def users(path):
    return {u["subject"]: u for u in read_json(path)["users"]}


def test_the_first_admin_creates_the_file_with_the_default_warehouse(path, capsys):
    assert run(path, "add", "sub-1", "--name", "Ada", "--role", "admin") == 0
    assert users(path)["sub-1"]["grants"] == [{"role": "admin", "warehouse_id": WAREHOUSE, "area_id": None, "team": None}]
    assert "ok: 1 user(s)" in capsys.readouterr().out


def test_what_the_cli_writes_is_what_the_api_reads(path):
    run(path, "add", "boss", "--name", "Boss", "--role", "floor_manager")
    run(path, "add", "tech", "--name", "Tech", "--role", "technician", "--team", "Maintenance")
    directory = Directory.model_validate(read_json(path))
    boss, tech = directory.users
    assert has_permission(boss, Permission.APPROVE) and not has_permission(tech, Permission.APPROVE)
    # Team-scoped grants are object-scoped: they see only tickets assigned to them in that team.
    assigned = {"warehouse_id": WAREHOUSE, "assignee_team": "Maintenance", "assigned_to_user_id": "tech"}
    assert can_view_ticket(tech, assigned) and not can_view_ticket(tech, {**assigned, "assignee_team": "Safety"})


def test_adding_the_same_subject_twice_is_refused_and_changes_nothing(path, capsys):
    run(path, "add", "sub-1", "--name", "Ada", "--role", "admin")
    before = path.read_text()
    assert run(path, "add", "sub-1", "--name", "Other", "--role", "viewer") == 1
    assert "already exists" in capsys.readouterr().err and path.read_text() == before


def test_grants_can_be_added_and_revoked_and_duplicates_are_refused(path, capsys):
    run(path, "add", "sub-1", "--name", "Ada", "--role", "admin")
    assert run(path, "grant", "sub-1", "--role", "auditor", "--area", "dock") == 0
    assert [g["role"] for g in users(path)["sub-1"]["grants"]] == ["admin", "auditor"]
    assert run(path, "grant", "sub-1", "--role", "auditor", "--area", "dock") == 1
    assert "already exists" in capsys.readouterr().err
    assert run(path, "revoke", "sub-1", "--role", "auditor", "--area", "dock") == 0
    assert [g["role"] for g in users(path)["sub-1"]["grants"]] == ["admin"]
    assert run(path, "revoke", "sub-1", "--role", "auditor", "--area", "dock") == 1


def test_a_dispatcher_needs_a_team_and_the_file_is_left_alone(path, capsys):
    run(path, "add", "sub-1", "--name", "Ada", "--role", "admin")
    before = path.read_text()
    assert run(path, "add", "sub-2", "--name", "Dee", "--role", "dispatcher") == 1
    assert "team" in capsys.readouterr().err.lower() and path.read_text() == before


def test_unknown_users_and_roles_are_reported_not_crashed(path, capsys):
    assert run(path, "disable", "ghost") == 1 and "no user" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        run(path, "add", "x", "--name", "X", "--role", "wizard")


def test_disable_and_enable_toggle_access(path):
    run(path, "add", "sub-1", "--name", "Ada", "--role", "admin")
    run(path, "add", "sub-2", "--name", "Bo", "--role", "viewer")
    assert run(path, "disable", "sub-2") == 0 and users(path)["sub-2"]["active"] is False
    assert run(path, "enable", "sub-2") == 0 and users(path)["sub-2"]["active"] is True


def test_the_last_active_admin_cannot_be_locked_out(path, capsys):
    run(path, "add", "sub-1", "--name", "Ada", "--role", "admin")
    for argv in (["disable", "sub-1"], ["remove", "sub-1"], ["revoke", "sub-1", "--role", "admin"]):
        assert run(path, *argv) == 1
        assert "no active admin" in capsys.readouterr().err
    assert users(path)["sub-1"]["active"] is True and users(path)["sub-1"]["grants"]


def test_a_second_admin_makes_removing_the_first_fine(path):
    run(path, "add", "sub-1", "--name", "Ada", "--role", "admin")
    run(path, "add", "sub-2", "--name", "Bo", "--role", "admin")
    assert run(path, "remove", "sub-1") == 0 and list(users(path)) == ["sub-2"]


def test_an_expired_admin_does_not_count_as_the_one_that_keeps_access(path):
    run(path, "add", "old", "--name", "Old", "--role", "admin", "--expires", (datetime.now(UTC) - timedelta(days=1)).isoformat())
    run(path, "add", "sub-2", "--name", "Bo", "--role", "admin")
    assert run(path, "remove", "sub-2") == 1  # the expired admin cannot take over
    assert run(path, "remove", "sub-2", "--force") == 0


def test_force_allows_removing_the_last_admin(path):
    run(path, "add", "sub-1", "--name", "Ada", "--role", "admin")
    assert run(path, "remove", "sub-1", "--force") == 0 and users(path) == {}


def test_expiry_needs_a_timezone_and_a_valid_date(path, capsys):
    assert run(path, "add", "a", "--name", "A", "--role", "viewer", "--expires", "2030-01-01T00:00:00") == 1
    assert "timezone" in capsys.readouterr().err
    assert run(path, "add", "a", "--name", "A", "--role", "viewer", "--expires", "next week") == 1
    assert not path.exists() or users(path) == {}


def test_list_shows_state_scope_and_expiry(path, capsys):
    run(path, "add", "sub-1", "--name", "Ada", "--role", "admin")
    run(path, "add", "sub-2", "--name", "Bo", "--role", "technician", "--team", "Safety", "--area", "dock", "--expires", "2030-01-01T00:00:00+00:00")
    run(path, "disable", "sub-2")
    capsys.readouterr()
    assert run(path, "list") == 0
    out = capsys.readouterr().out
    assert "sub-1  Ada  [active]  admin @ warehouse-a" in out
    assert "DISABLED, expires 2030-01-01T00:00:00+00:00" in out and "technician @ warehouse-a/dock team=Safety" in out


def test_listing_an_empty_or_missing_directory_is_not_an_error(path, capsys):
    assert run(path, "list") == 0 and "No users" in capsys.readouterr().out


def test_a_corrupt_directory_is_never_overwritten(path, capsys):
    path.write_text("{not json")
    assert run(path, "add", "sub-1", "--name", "Ada", "--role", "admin") == 1
    assert path.read_text() == "{not json" and "error:" in capsys.readouterr().err
    path.write_text(json.dumps({"users": [{"subject": "x"}]}))
    assert run(path, "add", "sub-1", "--name", "Ada", "--role", "admin") == 1
    assert json.loads(path.read_text()) == {"users": [{"subject": "x"}]}


def test_the_file_is_not_world_readable(path):
    run(path, "add", "sub-1", "--name", "Ada", "--role", "admin")
    assert not path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO)


def test_grant_model_is_what_the_cli_uses():
    assert access.Grant is Grant and access.Role is Role
