import logging
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app import personas, sessions
from app.auth import SESSION_COOKIE, Grant, Permission, Role, User, can_view_ticket, has_permission
from app.config import ROOT_DIR, Settings, get_settings
from app.storage import read_json, write_json
from tests.rbac_support import anonymous, seed_ticket, session_for, sign_in, world  # noqa: F401

pytestmark = pytest.mark.rbac

PROTECTED = [
    ("GET", "/me"), ("GET", "/auth/session"), ("GET", "/tickets"), ("GET", "/tickets/T-1"), ("GET", "/tickets/T-1.pdf"),
    ("GET", "/tickets/T-1/history"), ("POST", "/tickets/T-1/assignment"), ("POST", "/tickets/T-1/acknowledge"),
    ("POST", "/tickets/T-1/notes"), ("POST", "/tickets/T-1/resolve"), ("GET", "/technicians"), ("GET", "/notifications"),
    ("POST", "/notifications/N-0123456789ab/read"), ("GET", "/inventory"), ("GET", "/inventory/board"),
    ("GET", "/compliance"), ("GET", "/audit"), ("GET", "/scores"), ("GET", "/admin/accounts"),
    ("POST", "/admin/accounts/tech-a/active"), ("GET", "/demo"), ("GET", "/demo/clean_pallet_a.jpg"),
    ("POST", "/scan"), ("GET", "/scan/0123456789abcdef"), ("DELETE", "/scan/0123456789abcdef"),
    ("POST", "/scan/0123456789abcdef/approve"), ("POST", "/scan/0123456789abcdef/feedback"),
    ("GET", "/scan/0123456789abcdef/count"), ("POST", "/scan/0123456789abcdef/reconcile"),
]


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_every_data_endpoint_requires_a_session(world, method, path):
    assert anonymous().request(method, path).status_code == 401


def test_health_and_persona_list_are_public(world):
    client = anonymous()
    assert client.get("/health").json()["demo_sign_in"] is True
    listing = client.get("/auth/demo-personas").json()
    assert listing["enabled"] and "not verified" in listing["notice"]
    ids = [p["id"] for p in listing["personas"]]
    assert {"admin", "ops-manager", "floor-manager", "tech-a", "tech-b", "reporter", "auditor"} <= set(ids)
    assert ids.index("tech-b") < ids.index("dispatcher")  # core roles first
    assert {p["id"] for p in listing["personas"] if p["core"]} == {
        "admin", "ops-manager", "floor-manager", "tech-a", "tech-b", "reporter", "auditor",
    }


def test_every_persona_can_sign_in_without_credentials(world):
    for persona in personas.PERSONAS:
        me = sign_in(persona.id).get("/me").json()
        assert me["subject"] == persona.id and me["display_name"] == persona.display_name


@pytest.mark.parametrize("replay", [False, True])
def test_demo_sign_in_is_disabled_by_default_even_in_replay_mode(world, monkeypatch, replay):
    client = sign_in("admin")
    monkeypatch.setenv("DEMO_SIGN_IN_ENABLED", "false")
    if replay:
        monkeypatch.setenv("DEMO_MODE", "replay")
    get_settings.cache_clear()
    assert anonymous().get("/auth/demo-personas").json() == {
        "enabled": False, "notice": "Demo sign-in is disabled on this server.", "personas": [],
    }
    assert anonymous().post("/auth/demo-sign-in", json={"persona_id": "admin"}).status_code == 403
    assert client.get("/me").status_code == 401  # no silent fallback to an existing or anonymous identity


def test_default_settings_keep_demo_sign_in_off(monkeypatch):
    monkeypatch.delenv("DEMO_SIGN_IN_ENABLED", raising=False)
    assert Settings(_env_file=None).demo_sign_in_enabled is False


@pytest.mark.parametrize("body", [
    {"persona_id": "nobody"}, {"persona_id": "admin", "grants": [{"role": "admin"}]},
    {"persona_id": "tech-a", "role": "admin"}, {"persona_id": "../admin"}, {},
])
def test_unknown_personas_and_browser_supplied_grants_are_rejected(world, body):
    response = anonymous().post("/auth/demo-sign-in", json=body)
    assert response.status_code in {404, 422}
    assert SESSION_COOKIE not in response.cookies


def test_sign_in_sets_a_hardened_cookie_and_never_returns_the_token(world):
    response = anonymous().post("/auth/demo-sign-in", json={"persona_id": "floor-manager"})
    cookie = response.headers["set-cookie"]
    token = response.cookies[SESSION_COOKIE]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie and "Max-Age=28800" in cookie
    assert "Secure" not in cookie  # documented localhost-only default
    assert token not in response.text and response.json()["csrf_token"]
    stored = read_json(get_settings().session_file)
    assert token not in str(stored) and len(stored["sessions"]) == 1


def test_secure_cookie_setting(world, monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "true")
    get_settings.cache_clear()
    response = anonymous().post("/auth/demo-sign-in", json={"persona_id": "admin"})
    assert "Secure" in response.headers["set-cookie"]


def test_session_endpoint_returns_identity_and_csrf_token(world):
    client = sign_in("tech-a")
    body = client.get("/auth/session").json()
    assert body["user_id"] == "tech-a" and body["roles"] == ["technician"]
    assert set(body["permissions"]) == {"tickets", "work"}
    assert body["csrf_token"] == client.headers["X-CSRF-Token"]
    assert client.get("/auth/session").headers["cache-control"] == "no-store"


def test_mutations_require_the_session_csrf_token(world):
    client = sign_in("admin")
    good = client.headers.pop("X-CSRF-Token")
    assert client.post("/admin/accounts/tech-b/active", json={"active": True}).status_code == 403
    client.headers["X-CSRF-Token"] = "forged"
    assert client.post("/admin/accounts/tech-b/active", json={"active": True}).status_code == 403
    client.headers["X-CSRF-Token"] = sign_in("ops-manager").headers["X-CSRF-Token"]  # another session's token
    assert client.post("/admin/accounts/tech-b/active", json={"active": True}).status_code == 403
    client.headers["X-CSRF-Token"] = good
    assert client.post("/admin/accounts/tech-b/active", json={"active": True}).status_code == 200


def test_unexpected_origins_are_rejected_for_sign_in_and_mutations(world):
    evil = {"Origin": "https://evil.example"}
    assert anonymous().post("/auth/demo-sign-in", json={"persona_id": "admin"}, headers=evil).status_code == 403
    client = sign_in("admin")
    assert client.post("/admin/accounts/tech-b/active", json={"active": True}, headers=evil).status_code == 403
    assert client.get("/me", headers=evil).status_code == 200  # reads are not state changes


def test_logout_revokes_the_session(world):
    client = sign_in("tech-a")
    token = client.cookies[SESSION_COOKIE]
    assert client.post("/auth/logout").status_code == 204
    replay = anonymous()
    replay.cookies.set(SESSION_COOKIE, token)
    assert replay.get("/me").status_code == 401
    assert anonymous().post("/auth/logout").status_code == 204  # idempotent when already signed out


def test_logout_requires_csrf(world):
    client = sign_in("tech-a")
    client.headers.pop("X-CSRF-Token")
    assert client.post("/auth/logout").status_code == 403
    assert client.get("/me").status_code == 200


def test_switching_persona_revokes_the_previous_session(world):
    client = sign_in("tech-a")
    old = client.cookies[SESSION_COOKIE]
    switched = client.post("/auth/demo-sign-in", json={"persona_id": "tech-b"})
    assert switched.status_code == 200 and client.get("/me").json()["subject"] == "tech-b"
    stale = anonymous()
    stale.cookies.set(SESSION_COOKIE, old)
    assert stale.get("/me").status_code == 401


@pytest.mark.parametrize("token", ["forged", "", "x" * 300])
def test_forged_sessions_fail(world, token):
    client = anonymous()
    client.cookies.set(SESSION_COOKIE, token)
    assert client.get("/me").status_code == 401


def test_expired_sessions_fail_and_are_pruned(world):
    client = sign_in("tech-a")
    path = get_settings().session_file
    data = read_json(path)
    for record in data["sessions"].values():
        record["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    write_json(path, data)
    assert client.get("/me").status_code == 401
    sign_in("tech-b")
    assert len(read_json(path)["sessions"]) == 1


@pytest.mark.parametrize("content", ["{not json", '{"sessions": []}', '{"sessions": {"abc": {"user_id": "x"}}}'])
def test_corrupt_session_storage_fails_closed_with_503(world, content):
    client = sign_in("admin")
    digest = next(iter(read_json(get_settings().session_file)["sessions"]))
    if "abc" in content:
        content = content.replace("abc", digest)
    get_settings().session_file.write_text(content)
    assert client.get("/me").status_code == 503


def test_corrupt_user_store_fails_closed_with_503(world):
    client = sign_in("admin")
    get_settings().rbac_users_file.write_text('{"users": []}')
    assert client.get("/me").status_code == 503
    assert anonymous().post("/auth/demo-sign-in", json={"persona_id": "admin"}).status_code == 503


def test_sessions_survive_restart(world):
    client = sign_in("tech-a")
    get_settings.cache_clear()
    assert client.get("/me").status_code == 200


def test_admin_disabling_a_user_revokes_access_immediately(world):
    admin, tech = sign_in("admin"), sign_in("tech-b")
    assert admin.post("/admin/accounts/tech-b/active", json={"active": False}).json()["active"] is False
    assert tech.get("/me").status_code == 401
    assert anonymous().post("/auth/demo-sign-in", json={"persona_id": "tech-b"}).status_code == 403
    listing = anonymous().get("/auth/demo-personas").json()["personas"]
    assert next(p for p in listing if p["id"] == "tech-b")["active"] is False
    admin.post("/admin/accounts/tech-b/active", json={"active": True})
    assert sign_in("tech-b").get("/me").status_code == 200
    actions = [e["tool"] for e in read_json_lines(world)]
    assert actions.count("admin:set_active") == 2


def read_json_lines(world):
    from app import audit

    return audit.read_all()


def test_admin_cannot_lock_themselves_out_and_others_cannot_administer(world):
    admin = sign_in("admin")
    assert admin.post("/admin/accounts/admin/active", json={"active": False}).status_code == 422
    assert admin.post("/admin/accounts/ghost/active", json={"active": False}).status_code == 404
    assert len(admin.get("/admin/accounts").json()) == len(personas.PERSONAS)
    for persona in ("ops-manager", "floor-manager", "tech-a", "auditor"):
        client = sign_in(persona)
        assert client.get("/admin/accounts").status_code == 403
        assert client.post("/admin/accounts/tech-a/active", json={"active": False}).status_code == 403


@pytest.mark.parametrize(("persona", "allowed"), [
    ("admin", {"/tickets", "/inventory", "/compliance", "/audit", "/scores", "/admin/accounts", "/technicians"}),
    ("ops-manager", {"/tickets", "/inventory", "/compliance", "/scores", "/technicians"}),
    ("floor-manager", {"/tickets", "/inventory", "/compliance", "/scores", "/technicians"}),
    ("tech-a", {"/tickets"}),
    ("contractor", {"/tickets"}),
    ("auditor", {"/tickets", "/compliance", "/audit"}),
    ("dispatcher", {"/tickets", "/technicians"}),
    ("inventory-controller", {"/tickets", "/inventory"}),
    ("safety-officer", {"/tickets"}),
    ("quality-inspector", {"/tickets"}),
    ("viewer", {"/scores"}),
    ("reporter", set()),
])
def test_role_matrix_on_read_endpoints(world, persona, allowed):
    client = sign_in(persona)
    for path in ["/tickets", "/inventory", "/compliance", "/audit", "/scores", "/admin/accounts", "/technicians"]:
        response = client.get(path)
        assert response.status_code == (200 if path in allowed else 403), (persona, path, response.text)


@pytest.mark.parametrize("persona", ["tech-a", "contractor", "reporter", "auditor", "viewer"])
@pytest.mark.parametrize(("method", "path"), [
    ("POST", "/scan"), ("POST", "/scan/0123456789abcdef/approve"), ("POST", "/scan/0123456789abcdef/feedback"),
    ("DELETE", "/scan/0123456789abcdef"), ("POST", "/scan/0123456789abcdef/reconcile"),
])
def test_non_managers_cannot_mutate_operations(world, persona, method, path):
    assert sign_in(persona).request(method, path, json={}).status_code == 403


def test_replay_does_not_bypass_authorization(world, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "replay")
    get_settings.cache_clear()
    client = sign_in("tech-a")
    assert client.post("/scan").status_code == 403
    assert client.get("/demo").status_code == 403


def test_orphan_pdf_does_not_bypass_ticket_authorization(world):
    (world.path / "tickets").mkdir()
    (world.path / "tickets" / "T-orphan.pdf").write_bytes(b"%PDF test")
    assert sign_in("admin").get("/tickets/T-orphan.pdf").status_code == 404


def test_admin_access_is_global_and_grants_are_not_cross_combined():
    admin = User(subject="a", display_name="A", grants=[Grant(role=Role.ADMIN, warehouse_id="elsewhere")])
    assert all(has_permission(admin, permission) for permission in Permission)
    scoped = User(subject="alice", display_name="A", grants=[
        Grant(role=Role.FLOOR_MANAGER, warehouse_id="warehouse-b"),
        Grant(role=Role.TECHNICIAN, warehouse_id="default"),
    ])
    assert not has_permission(scoped, Permission.APPROVE)
    assert not can_view_ticket(scoped, {"assigned_to_user_id": "bob", "warehouse_id": "default"})


def test_floor_scopes_deny_other_areas_and_global_data(world, monkeypatch):
    seed_ticket(world, "T-unscoped")
    seed_ticket(world, "T-floor-1", area_id="floor-1")
    seed_ticket(world, "T-floor-2", area_id="floor-2")
    manager = User(subject="fm-floor-1", display_name="F1", grants=[
        Grant(role=Role.FLOOR_MANAGER, warehouse_id="default", area_id="floor-1"),
    ])
    client = session_for(manager, monkeypatch)
    assert [t["ticket_id"] for t in client.get("/tickets").json()] == ["T-floor-1"]
    assert client.get("/tickets/T-floor-2").status_code == 404
    assert client.get("/inventory").status_code == 403  # warehouse-wide data needs a warehouse-wide grant


def test_other_warehouse_grants_see_nothing(world, monkeypatch):
    seed_ticket(world, "T-here")
    other = User(subject="om-b", display_name="B", grants=[Grant(role=Role.OPERATIONS_MANAGER, warehouse_id="warehouse-b")])
    client = session_for(other, monkeypatch)
    assert client.get("/tickets").status_code == 403
    assert client.get("/tickets/T-here.pdf").status_code == 403


def test_expired_contractor_is_rejected(world, monkeypatch):
    contractor = User(subject="c-old", display_name="C", expires_at=datetime.now(UTC) - timedelta(seconds=1),
                      grants=[Grant(role=Role.CONTRACTOR, warehouse_id="default")])
    assert session_for(contractor, monkeypatch).get("/tickets").status_code == 401


@pytest.mark.parametrize(("persona", "ticket", "expected"), [
    ("safety-officer", {"assignee_team": "Safety"}, True),
    ("safety-officer", {"assignee_team": "Maintenance"}, False),
    ("inventory-controller", {"assignee_team": "Inventory"}, True),
    ("inventory-controller", {"assignee_team": "Safety"}, False),
    ("quality-inspector", {"finding_type": "box_damage"}, True),
    ("quality-inspector", {"finding_type": "walkway_blocked"}, False),
    ("dispatcher", {"assignee_team": "Safety"}, True),
    ("dispatcher", {"assignee_team": "Maintenance"}, False),
])
def test_specialist_ticket_boundaries(world, persona, ticket, expected):
    seed_ticket(world, "T-x", **ticket)
    assert (sign_in(persona).get("/tickets/T-x").status_code == 200) is expected


def test_dispatcher_grant_requires_team_and_expiry_requires_timezone():
    with pytest.raises(ValidationError):
        Grant(role=Role.DISPATCHER, warehouse_id="default")
    with pytest.raises(ValidationError):
        User(subject="x", display_name="x", expires_at=datetime(2030, 1, 1))


def test_relative_state_files_are_repo_relative(monkeypatch):
    monkeypatch.setenv("SESSION_FILE", "backend/data/s.json")
    monkeypatch.setenv("RBAC_USERS_FILE", "backend/data/u.json")
    settings = Settings(_env_file=None)
    assert settings.session_file == ROOT_DIR / "backend/data/s.json"
    assert settings.rbac_users_file == ROOT_DIR / "backend/data/u.json"


def test_session_secrets_never_appear_in_logs_or_audit(world, caplog):
    caplog.set_level(logging.DEBUG)
    client = sign_in("admin")
    token, csrf = client.cookies[SESSION_COOKIE], client.headers["X-CSRF-Token"]
    client.post("/admin/accounts/tech-a/active", json={"active": True})
    client.post("/auth/logout")
    from app import audit

    trail = str(audit.read_all())
    assert "auth:sign_in" in trail and "auth:logout" in trail
    for secret in (token, csrf):
        assert secret not in caplog.text and secret not in trail


def test_session_store_rejects_unknown_records_without_resurrecting_revoked_ones(world):
    token, _ = sessions.create("tech-a")
    sessions.revoke(token)
    sessions.create("tech-b")
    assert sessions.lookup(token) is None
