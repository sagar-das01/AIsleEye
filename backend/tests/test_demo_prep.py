import os
import re
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.config as config
from app import replay
from scripts import predemo

ROOT = Path(__file__).resolve().parents[2]
DEMO_SH = ROOT / "scripts" / "demo.sh"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    config.get_settings.cache_clear()


def test_the_repository_as_committed_passes_every_offline_check():
    rows, passed = predemo.run_checks(checks=[
        ("photos", predemo.check_demo_images), ("pinned", predemo.check_pinned_store), ("replay", predemo.check_replay_offline),
    ])
    assert passed, rows
    assert all(status == predemo.OK for _, status, _ in rows)


def test_the_replay_check_leaves_the_environment_and_data_as_it_found_them():
    before = {k: os.environ.get(k) for k in ("DEMO_MODE", "MODEL_API_BASE_URL", "DEMO_SIGN_IN_ENABLED", "SESSION_FILE", "RBAC_USERS_FILE")}
    from app import audit
    from app.agent import tools

    data_dir, audit_path = tools.DATA_DIR, audit.AUDIT_PATH
    assert predemo.check_replay_offline()[0] == predemo.OK
    assert {k: os.environ.get(k) for k in before} == before
    assert tools.DATA_DIR == data_dir and audit.AUDIT_PATH == audit_path


def test_a_missing_pinned_result_is_a_failure_that_names_the_photo(monkeypatch, tmp_path):
    monkeypatch.setattr(replay, "DEMO_DIR", tmp_path)
    status, message = predemo.check_pinned_store()
    assert status == predemo.FAIL and "clean_pallet_a" in message


def test_a_pinned_result_that_no_longer_matches_the_expectation_is_a_failure(monkeypatch, tmp_path):
    from app.pipeline.scan import scan_id_for

    entry = next(e for e in replay.load_manifest() if e["expected"]["damaged"] == 1 and not e["expected"]["blocked"])
    sid = scan_id_for(replay.demo_image_path(entry["id"]).read_bytes())
    pinned = replay.load_pinned(sid).model_copy(update={"findings": []})
    (tmp_path / f"{sid}.json").write_text(pinned.model_dump_json())
    monkeypatch.setattr(replay, "DEMO_DIR", tmp_path)
    status, message = predemo.check_pinned_store()
    assert status == predemo.FAIL and f"{entry['id']}: pinned result has 0 damaged" in message


def test_a_missing_demo_photo_is_a_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(replay, "DEMO_IMAGES_DIR", tmp_path)
    (tmp_path / "manifest.json").write_text((ROOT / "demo_images" / "manifest.json").read_text())
    status, message = predemo.check_demo_images()
    assert status == predemo.FAIL and "missing" in message


def test_langfuse_is_fine_when_disabled_and_a_warning_when_enabled_but_down(monkeypatch):
    assert predemo.check_langfuse()[0] == predemo.OK
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://127.0.0.1:9")
    config.get_settings.cache_clear()
    status, message = predemo.check_langfuse()
    assert status == predemo.WARN and "LANGFUSE_ENABLED=false" in message


def test_a_warning_does_not_fail_the_run_but_a_failure_or_a_crash_does():
    def crash():
        raise RuntimeError("boom")

    rows, passed = predemo.run_checks(checks=[("a", lambda: (predemo.OK, "x")), ("b", lambda: (predemo.WARN, "y"))])
    assert passed and [r[1] for r in rows] == [predemo.OK, predemo.WARN]
    rows, passed = predemo.run_checks(checks=[("a", lambda: (predemo.OK, "x")), ("b", crash)])
    assert not passed and rows[1][1] == predemo.FAIL and "boom" in rows[1][2]


def test_an_empty_key_fails_the_key_check(monkeypatch):
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(model_api_key="  "))
    assert predemo.check_key()[0] == predemo.FAIL


def test_the_live_check_reports_a_working_and_a_broken_key(monkeypatch):
    import app.clients as clients

    class Client:
        def __init__(self, result):
            self.responses = SimpleNamespace(create=result)

    monkeypatch.setattr(clients, "get_client", lambda: Client(lambda **kw: SimpleNamespace(output_text="OK")))
    assert predemo.check_live_key()[0] == predemo.OK

    def deny(**kw):
        raise RuntimeError("401 sk-secret-value")

    monkeypatch.setattr(clients, "get_client", lambda: Client(deny))
    status, message = predemo.check_live_key()
    assert status == predemo.FAIL and "sk-secret-value" not in message


def test_main_exit_code_and_the_hand_checklist(monkeypatch, capsys):
    monkeypatch.setattr(predemo, "run_checks", lambda live=False: ([("x", predemo.FAIL, "broken")], False))
    assert predemo.main([]) == 1
    out = capsys.readouterr().out
    assert "[FAIL] x: broken" in out and "Backup screen video" in out and "rehearsed three times" in out
    monkeypatch.setattr(predemo, "run_checks", lambda live=False: ([("x", predemo.OK, "fine")], True))
    assert predemo.main([]) == 0


# ---- scripts/demo.sh ----------------------------------------------------------------------------------------------------

def test_demo_script_is_executable_and_valid_bash():
    assert DEMO_SH.stat().st_mode & stat.S_IXUSR
    assert subprocess.run(["bash", "-n", str(DEMO_SH)], capture_output=True).returncode == 0


def test_demo_script_rejects_unknown_arguments_before_doing_anything():
    res = subprocess.run([str(DEMO_SH), "--bogus"], capture_output=True, text=True, timeout=20)
    assert res.returncode == 2 and "usage:" in res.stderr


def test_demo_script_holds_no_secrets_and_never_echoes_the_key():
    text = DEMO_SH.read_text()
    assert not re.search(r"(sk|pk)-[A-Za-z0-9_-]{8,}", text)
    assert not re.search(r"echo[^\n]*\$\{?MODEL_API_KEY", text)
    assert not re.search(r"^\s*(export\s+)?MODEL_API_KEY=\S", text, re.M)


def test_demo_script_runs_the_checks_and_offers_replay_without_a_key():
    text = DEMO_SH.read_text()
    assert "scripts.predemo" in text and "replay" in text and "DEMO_MODE=replay" in text
