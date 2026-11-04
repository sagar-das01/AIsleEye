import pytest

from app import personas, sessions
from app.config import get_settings
from scripts import access


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setenv("RBAC_USERS_FILE", str(tmp_path / "users.json"))
    monkeypatch.setenv("SESSION_FILE", str(tmp_path / "sessions.json"))
    get_settings.cache_clear()
    return tmp_path


def test_list_shows_every_persona_with_its_status(files, capsys):
    assert access.main(["list"]) == 0
    out = capsys.readouterr().out
    assert all(p.id in out for p in personas.PERSONAS)
    assert "dispatcher" in out and "(Safety)" in out


def test_disable_revokes_sessions_and_enable_restores(files, capsys):
    token, _ = sessions.create("tech-b")
    assert access.main(["disable", "tech-b"]) == 0
    assert personas.find_user("tech-b").active is False and sessions.lookup(token) is None
    capsys.readouterr()
    access.main(["list"])
    assert "DISABLED" in capsys.readouterr().out
    assert access.main(["enable", "tech-b"]) == 0
    assert personas.find_user("tech-b").active is True


def test_the_last_active_admin_needs_force(files, capsys):
    assert access.main(["disable", "admin"]) == 1
    assert "no active admin" in capsys.readouterr().err
    assert personas.find_user("admin").active is True
    assert access.main(["disable", "admin", "--force"]) == 0
    assert access.main(["enable", "admin"]) == 0  # the recovery path


def test_unknown_accounts_and_broken_files_are_errors(files, capsys):
    assert access.main(["disable", "ghost"]) == 1
    assert "no demo account" in capsys.readouterr().err
    (files / "users.json").write_text("[]")
    assert access.main(["list"]) == 1
    assert access.main(["enable", "tech-a"]) == 1


def test_file_option_points_at_another_flags_file(files, tmp_path):
    other = tmp_path / "other.json"
    assert access.main(["disable", "--file", str(other), "tech-a"]) == 0
    assert other.is_file()
