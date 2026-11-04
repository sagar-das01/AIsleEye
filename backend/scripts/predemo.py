"""Pre-demo checklist: everything that can be checked by a machine, and a reminder of what cannot.

Usage (from backend/):
    python -m scripts.predemo            offline checks only (no network, no model call)
    python -m scripts.predemo --live     also one tiny model call, to prove the key works

Exit code 1 if any check fails; warnings do not fail it.
"""

import argparse
import os
import socket
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

OK, WARN, FAIL = "ok", "warn", "FAIL"
BY_HAND = [
    "Browser zoom and window size set for the recording",
    "Phone capture tested (camera upload on the phone, on the same network)",
    "Backup screen video recorded and playable",
    "Demo script rehearsed three times",
]
Result = tuple[str, str]


def check_key() -> Result:
    from app.config import get_settings

    return (OK, "model API key is set") if get_settings().model_api_key.strip() else (FAIL, "MODEL_API_KEY is empty (replay mode still works)")


def check_demo_images() -> Result:
    from app import replay
    from app.pipeline.scan import scan_id_for

    entries = replay.load_manifest()
    if not entries:
        return FAIL, "demo_images/manifest.json is missing or empty"
    missing = [e["id"] for e in entries if replay.demo_image_path(e["id"]) is None]
    if missing:
        return FAIL, f"demo photos missing: {', '.join(missing)}"
    ids = [scan_id_for(replay.demo_image_path(e["id"]).read_bytes()) for e in entries]
    if len(set(ids)) != len(ids):
        return FAIL, "two demo photos are byte-identical"
    return OK, f"{len(entries)} demo photos in order: " + ", ".join(e["id"] for e in entries)


def check_pinned_store() -> Result:
    from app import replay
    from app.pipeline.scan import scan_id_for

    problems = []
    entries = replay.load_manifest()
    for e in entries:
        path = replay.demo_image_path(e["id"])
        if path is None:
            continue
        pinned = replay.load_pinned(scan_id_for(path.read_bytes()))
        if pinned is None:
            problems.append(f"{e['id']}: no valid pinned result")
            continue
        damaged = sum(f.type == "box_damage" for f in pinned.findings)
        blocked = sum(f.type == "walkway_blocked" for f in pinned.findings)
        want = e["expected"]
        if (damaged, blocked) != (want["damaged"], want["blocked"]):
            problems.append(f"{e['id']}: pinned result has {damaged} damaged and {blocked} blocked, expected {want['damaged']} and {want['blocked']}")
    return (FAIL, "; ".join(problems)) if problems else (OK, f"pinned results present and as expected for {len(entries)} photos")


def check_replay_offline() -> Result:
    """Scan every demo photo and approve its findings in replay mode with the model API pointed at a dead address."""
    from fastapi.testclient import TestClient

    from app import audit, config, main, replay, scoring
    from app.agent import tools

    saved = {k: os.environ.get(k) for k in ("DEMO_MODE", "MODEL_API_BASE_URL", "DEMO_AUTH_BYPASS")}
    os.environ.update(DEMO_MODE="replay", MODEL_API_BASE_URL="http://127.0.0.1:9", DEMO_AUTH_BYPASS="true")
    config.get_settings.cache_clear()
    originals = (tools.DATA_DIR, audit.AUDIT_PATH, scoring.SCORES_PATH)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tools.DATA_DIR, audit.AUDIT_PATH, scoring.SCORES_PATH = Path(tmp), Path(tmp) / "audit.jsonl", Path(tmp) / "scores.jsonl"
            (Path(tmp) / "inventory.json").write_text((config.DATA_DIR / "inventory.seed.json").read_text())
            client = TestClient(main.app, client=("127.0.0.1", 50000))  # as the dashboard on this machine would appear
            for e in replay.load_manifest():
                data = replay.demo_image_path(e["id"]).read_bytes()
                res = client.post("/scan", files={"image": (f"{e['id']}.jpg", data, "image/jpeg")})
                if res.status_code != 200 or res.json().get("replay") is not True:
                    return FAIL, f"{e['id']}: replay scan answered {res.status_code}"
                findings = res.json()["findings"]
                if findings:
                    approved = client.post(f"/scan/{res.json()['scan_id']}/approve", json={"finding_ids": [f["id"] for f in findings]})
                    if approved.status_code != 200 or any(a["status"] != "executed" for a in approved.json()["actions"]):
                        return FAIL, f"{e['id']}: approving in replay mode did not complete"
        return OK, "every demo photo scans and approves with the network off"
    finally:
        tools.DATA_DIR, audit.AUDIT_PATH, scoring.SCORES_PATH = originals
        for key, value in saved.items():
            os.environ.pop(key, None) if value is None else os.environ.__setitem__(key, value)
        config.get_settings.cache_clear()


def check_langfuse() -> Result:
    import httpx

    from app.config import get_settings

    s = get_settings()
    if not s.langfuse_enabled:
        return OK, "Langfuse tracing is disabled (fine for the demo)"
    try:
        httpx.get(f"{s.langfuse_base_url.rstrip('/')}/api/public/health", timeout=3).raise_for_status()
    except httpx.HTTPError:
        return WARN, f"Langfuse is enabled but {s.langfuse_base_url} is unreachable: start it (scripts/langfuse-local.sh up) or set LANGFUSE_ENABLED=false"
    return OK, f"Langfuse reachable at {s.langfuse_base_url}"


def check_ports() -> Result:
    busy = []
    for port in (8000, 3001):
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                busy.append(port)
    return (WARN, f"port(s) {', '.join(map(str, busy))} already in use (a demo may already be running)") if busy else (OK, "ports 8000 and 3001 are free")


def check_live_key() -> Result:
    from app.clients import get_client, reasoning_kwargs
    from app.config import get_settings

    try:
        out = get_client().responses.create(model=get_settings().muse_model, input="Reply with exactly the word: OK", **reasoning_kwargs())
    except Exception as exc:
        return FAIL, f"model call failed ({type(exc).__name__}): check the key, the network and the base URL"
    return (OK, "live model call works") if (out.output_text or "").strip() else (FAIL, "live model call returned nothing")


def run_checks(live: bool = False, checks: list[tuple[str, Callable[[], Result]]] | None = None) -> tuple[list[tuple[str, str, str]], bool]:
    plan = checks or [
        ("API key", check_key),
        ("Demo photos", check_demo_images),
        ("Pinned store", check_pinned_store),
        ("Replay with network off", check_replay_offline),
        ("Langfuse", check_langfuse),
        ("Ports", check_ports),
        *([("Live model call", check_live_key)] if live else []),
    ]
    rows = []
    for name, fn in plan:
        try:
            status, message = fn()
        except Exception as exc:
            status, message = FAIL, f"check crashed ({type(exc).__name__}: {str(exc)[:120]})"
        rows.append((name, status, message))
    return rows, all(status != FAIL for _, status, _ in rows)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true", help="also make one small model call")
    args = parser.parse_args(argv)
    rows, passed = run_checks(live=args.live)
    for name, status, message in rows:
        print(f"[{status:>4}] {name}: {message}")
    print("\nStill to do by hand:")
    for item in BY_HAND:
        print(f"  [ ] {item}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
