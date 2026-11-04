from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import auth
from app.agent import tools
from app.auth import Directory, Grant, Permission, Role, User, can_view_ticket, has_permission
from app.config import ROOT_DIR, Settings, get_settings
from app.main import app
from app.storage import append_jsonl, write_json

pytestmark = pytest.mark.rbac
client = TestClient(app)
WAREHOUSE = "warehouse-a"
ISSUER = "https://identity.example.test/"


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def world(tmp_path, monkeypatch, signing_key):
    monkeypatch.setenv("OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("OIDC_AUDIENCE", "aisleeye-api")
    monkeypatch.setenv("OIDC_JWKS_URL", f"{ISSUER}jwks")
    monkeypatch.setenv("RBAC_USERS_FILE", str(tmp_path / "access.json"))
    monkeypatch.setenv("RBAC_WAREHOUSE_ID", WAREHOUSE)
    get_settings.cache_clear()
    auth.jwks_client.cache_clear()
    public = jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key(), as_dict=True)
    public.update(kid="test-key", use="sig", alg="RS256")
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda self: {"keys": [public]})
    monkeypatch.setattr(tools, "DATA_DIR", tmp_path)
    write_json(tmp_path / "access.json", {"users": []})
    yield SimpleNamespace(path=tmp_path, key=signing_key)
    auth.jwks_client.cache_clear()


def provision(world, role=Role.ADMIN, subject="alice", warehouse=WAREHOUSE, **grant_options):
    user = User(subject=subject, display_name=subject, grants=[
        Grant(role=role, warehouse_id=warehouse, **grant_options),
    ])
    write_json(world.path / "access.json", {"users": [user.model_dump(mode="json")]})
    return user


def token(world, **overrides):
    now = datetime.now(UTC)
    claims = {
        "sub": "alice", "iss": ISSUER, "aud": "aisleeye-api",
        "iat": now, "exp": now + timedelta(minutes=5),
        **overrides,
    }
    return jwt.encode(claims, world.key, algorithm="RS256", headers={"kid": "test-key"})


def headers(world, **claims):
    return {"Authorization": f"Bearer {token(world, **claims)}"}


def seed_ticket(world, ticket_id="T-own", **options):
    ticket = {
        "ticket_id": ticket_id, "status": "open", "assigned_to_user_id": "alice",
        "warehouse_id": WAREHOUSE, "assignee_team": "Maintenance", **options,
    }
    append_jsonl(world.path / "tickets" / "index.jsonl", ticket)
    (world.path / "tickets" / f"{ticket_id}.pdf").write_bytes(b"%PDF-1.4 test")
    return ticket


PROTECTED = [
    ("GET", "/me"), ("GET", "/tickets"), ("GET", "/tickets/T-own.pdf"),
    ("GET", "/inventory"), ("GET", "/compliance"), ("GET", "/audit"), ("GET", "/scores"),
    ("GET", "/demo"), ("GET", "/demo/clean_pallet_a.jpg"),
    ("POST", "/scan"), ("GET", "/scan/0123456789abcdef"),
    ("DELETE", "/scan/0123456789abcdef"), ("POST", "/scan/0123456789abcdef/approve"),
    ("POST", "/scan/0123456789abcdef/feedback"),
]


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_every_data_endpoint_requires_authentication(world, method, path):
    response = client.request(method, path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_health_remains_public(world):
    assert client.get("/health").status_code == 200


def test_oidc_identity_uses_trusted_local_grants_not_token_roles(world):
    provision(world, Role.TECHNICIAN)
    response = client.get("/me", headers=headers(world, roles=["admin"]))
    assert response.status_code == 200
    assert response.json()["grants"][0]["role"] == "technician"
    assert "approve" not in response.json()["permissions"]


@pytest.mark.parametrize("claims", [
    {"exp": datetime.now(UTC) - timedelta(minutes=1)},
    {"iat": datetime.now(UTC) + timedelta(hours=1)},
    {"nbf": datetime.now(UTC) + timedelta(hours=1)},
    {"iss": "https://other.example.test/"},
    {"aud": "some-other-api"},
    {"sub": ""},
])
def test_invalid_claims_are_rejected(world, claims):
    provision(world)
    assert client.get("/me", headers=headers(world, **claims)).status_code == 401


@pytest.mark.parametrize("claim", ["exp", "iat", "sub", "iss", "aud"])
def test_required_claims_cannot_be_omitted(world, claim):
    provision(world)
    claims = jwt.decode(token(world), options={"verify_signature": False})
    claims.pop(claim)
    encoded = jwt.encode(claims, world.key, algorithm="RS256", headers={"kid": "test-key"})
    assert client.get("/me", headers={"Authorization": f"Bearer {encoded}"}).status_code == 401


def test_wrong_signature_and_symmetric_algorithm_are_rejected(world):
    provision(world)
    claims = jwt.decode(token(world), options={"verify_signature": False})
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    for key, algorithm in [(other_key, "RS256"), ("not-a-trusted-secret-long-enough-for-hmac", "HS256")]:
        encoded = jwt.encode(claims, key, algorithm=algorithm, headers={"kid": "test-key"})
        assert client.get("/me", headers={"Authorization": f"Bearer {encoded}"}).status_code == 401


@pytest.mark.parametrize("raw", ["not-a-jwt", "a.b.c", ""])
def test_malformed_bearer_is_rejected(world, raw):
    assert client.get("/me", headers={"Authorization": f"Bearer {raw}"}).status_code == 401


def test_unconfigured_authentication_fails_closed(world, monkeypatch):
    monkeypatch.setenv("OIDC_AUDIENCE", "")
    get_settings.cache_clear()
    assert client.get("/me", headers=headers(world)).status_code == 503


@pytest.mark.parametrize("setting", ["OIDC_ISSUER", "OIDC_JWKS_URL"])
def test_insecure_identity_urls_fail_closed(world, monkeypatch, setting):
    monkeypatch.setenv(setting, "http://identity.example.test/")
    get_settings.cache_clear()
    assert client.get("/me", headers=headers(world)).status_code == 503


def test_identity_outage_is_distinct_from_invalid_identity(world, monkeypatch):
    def unavailable(self):
        raise jwt.PyJWKClientConnectionError("private connection details")

    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", unavailable)
    response = client.get("/me", headers=headers(world))
    assert response.status_code == 503
    assert "private" not in response.text


def test_invalid_signing_key_set_fails_explicitly(world, monkeypatch):
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda self: {"keys": []})
    assert client.get("/me", headers=headers(world)).status_code == 503


def test_unknown_signing_key_is_not_trusted(world):
    provision(world)
    claims = jwt.decode(token(world), options={"verify_signature": False})
    encoded = jwt.encode(claims, world.key, algorithm="RS256", headers={"kid": "unknown"})
    assert client.get("/me", headers={"Authorization": f"Bearer {encoded}"}).status_code == 401


@pytest.mark.parametrize("directory", [
    None, {"users": [{"subject": "alice", "display_name": "A", "grants": [{"role": "invented", "warehouse_id": WAREHOUSE}]}]},
    {"users": [{"subject": "alice", "display_name": "A"}] * 2},
])
def test_invalid_directory_fails_closed(world, directory):
    write_json(world.path / "access.json", directory)
    assert client.get("/me", headers=headers(world)).status_code == 503


def test_missing_or_unreadable_directory_does_not_grant_access(world):
    (world.path / "access.json").unlink()
    assert client.get("/me", headers=headers(world)).status_code == 503
    (world.path / "access.json").write_text("{broken")
    assert client.get("/me", headers=headers(world)).status_code == 503


def test_unprovisioned_user_is_forbidden(world):
    assert client.get("/me", headers=headers(world)).status_code == 403


def test_role_revocation_and_disabling_apply_on_the_next_request(world):
    provision(world)
    authorization = headers(world)
    assert client.get("/audit", headers=authorization).status_code == 200
    user = provision(world, Role.TECHNICIAN)
    assert client.get("/audit", headers=authorization).status_code == 403
    write_json(world.path / "access.json", {"users": [user.model_copy(update={"active": False}).model_dump(mode="json")]})
    assert client.get("/me", headers=authorization).status_code == 403


def test_expired_contractor_is_forbidden(world):
    user = provision(world, Role.CONTRACTOR)
    user.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    write_json(world.path / "access.json", {"users": [user.model_dump(mode="json")]})
    assert client.get("/tickets", headers=headers(world)).status_code == 403


@pytest.mark.parametrize(("role", "allowed"), [
    (Role.ADMIN, {"/tickets", "/inventory", "/compliance", "/audit", "/scores"}),
    (Role.OPERATIONS_MANAGER, {"/tickets", "/inventory", "/compliance", "/scores"}),
    (Role.FLOOR_MANAGER, {"/tickets", "/inventory", "/compliance", "/scores"}),
    (Role.TECHNICIAN, {"/tickets"}),
    (Role.CONTRACTOR, {"/tickets"}),
    (Role.AUDITOR, {"/tickets", "/compliance", "/audit"}),
    (Role.INVENTORY_CONTROLLER, {"/tickets", "/inventory"}),
    (Role.SAFETY_OFFICER, {"/tickets"}),
    (Role.QUALITY_INSPECTOR, {"/tickets"}),
    (Role.VIEWER, {"/scores"}),
    (Role.REPORTER, set()),
])
def test_role_matrix_on_existing_read_endpoints(world, role, allowed):
    provision(world, role)
    for path in ["/tickets", "/inventory", "/compliance", "/audit", "/scores"]:
        response = client.get(path, headers=headers(world))
        assert response.status_code == (200 if path in allowed else 403), (role, path, response.text)


@pytest.mark.parametrize("role", [Role.TECHNICIAN, Role.CONTRACTOR, Role.REPORTER, Role.AUDITOR, Role.VIEWER])
@pytest.mark.parametrize(("method", "path"), [
    ("POST", "/scan"), ("POST", "/scan/0123456789abcdef/approve"),
    ("POST", "/scan/0123456789abcdef/feedback"), ("DELETE", "/scan/0123456789abcdef"),
])
def test_non_managers_cannot_mutate_operations(world, role, method, path):
    provision(world, role)
    assert client.request(method, path, headers=headers(world), json={}).status_code == 403


@pytest.mark.parametrize("role", [Role.TECHNICIAN, Role.CONTRACTOR])
def test_assignment_filters_lists_and_downloads(world, role):
    provision(world, role)
    seed_ticket(world)
    seed_ticket(world, "T-other", assigned_to_user_id="bob")
    seed_ticket(world, "T-unassigned", assigned_to_user_id=None)
    seed_ticket(world, "T-other-warehouse", warehouse_id="warehouse-b")
    authorization = headers(world)
    assert [ticket["ticket_id"] for ticket in client.get("/tickets", headers=authorization).json()] == ["T-own"]
    assert client.get("/tickets/T-own.pdf", headers=authorization).status_code == 200
    for ticket_id in ["T-other", "T-unassigned", "T-other-warehouse", "T-missing"]:
        assert client.get(f"/tickets/{ticket_id}.pdf", headers=authorization).status_code == 404


def test_orphan_pdf_does_not_bypass_ticket_authorization(world):
    provision(world)
    (world.path / "tickets").mkdir()
    (world.path / "tickets" / "T-orphan.pdf").write_bytes(b"%PDF test")
    assert client.get("/tickets/T-orphan.pdf", headers=headers(world)).status_code == 404


def test_admin_access_is_global_and_grants_are_not_cross_combined(world):
    user = provision(world, Role.ADMIN, warehouse="warehouse-b")
    seed_ticket(world)
    assert client.get("/tickets/T-own.pdf", headers=headers(world)).status_code == 200
    assert all(has_permission(user, permission) for permission in Permission)
    scoped = User(subject="alice", display_name="A", grants=[
        Grant(role=Role.FLOOR_MANAGER, warehouse_id="warehouse-b"),
        Grant(role=Role.TECHNICIAN, warehouse_id=WAREHOUSE),
    ])
    assert not has_permission(scoped, Permission.APPROVE)
    assert not can_view_ticket(scoped, {"assigned_to_user_id": "bob", "warehouse_id": WAREHOUSE})


def test_replay_does_not_bypass_authorization(world, monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "replay")
    get_settings.cache_clear()
    provision(world, Role.TECHNICIAN)
    assert client.post("/scan", headers=headers(world)).status_code == 403
    assert client.get("/demo", headers=headers(world)).status_code == 403


def test_legacy_unassigned_tickets_remain_manager_only(world):
    seed_ticket(world, assigned_to_user_id=None, warehouse_id=None)
    provision(world, Role.TECHNICIAN)
    assert client.get("/tickets", headers=headers(world)).json() == []
    provision(world, Role.FLOOR_MANAGER)
    assert len(client.get("/tickets", headers=headers(world)).json()) == 1


def test_floor_scopes_deny_unscoped_legacy_resources_and_global_data(world):
    provision(world, Role.FLOOR_MANAGER, area_id="floor-1")
    seed_ticket(world)
    seed_ticket(world, "T-floor-1", area_id="floor-1")
    seed_ticket(world, "T-floor-2", area_id="floor-2")
    authorization = headers(world)
    assert [t["ticket_id"] for t in client.get("/tickets", headers=authorization).json()] == ["T-floor-1"]
    assert client.get("/inventory", headers=authorization).status_code == 403
    assert client.get("/scan/0123456789abcdef", headers=authorization).status_code == 403


@pytest.mark.parametrize(("role", "grant_options", "ticket_options", "expected"), [
    (Role.DISPATCHER, {"team": "Maintenance"}, {"assignee_team": "Maintenance"}, True),
    (Role.DISPATCHER, {"team": "Maintenance"}, {"assignee_team": "Safety"}, False),
    (Role.SAFETY_OFFICER, {}, {"assignee_team": "Safety"}, True),
    (Role.SAFETY_OFFICER, {}, {"assignee_team": "Maintenance"}, False),
    (Role.INVENTORY_CONTROLLER, {}, {"assignee_team": "Inventory"}, True),
    (Role.INVENTORY_CONTROLLER, {}, {"assignee_team": "Safety"}, False),
    (Role.QUALITY_INSPECTOR, {}, {"finding_type": "box_damage"}, True),
    (Role.QUALITY_INSPECTOR, {}, {"finding_type": "walkway_blocked"}, False),
])
def test_specialist_ticket_boundaries(world, role, grant_options, ticket_options, expected):
    user = provision(world, role, **grant_options)
    assert can_view_ticket(user, seed_ticket(world, **ticket_options)) is expected


def test_directory_rejects_ambiguous_dispatch_and_expiry():
    with pytest.raises(ValidationError):
        Grant(role=Role.DISPATCHER, warehouse_id=WAREHOUSE)
    with pytest.raises(ValidationError):
        User(subject="a", display_name="A", expires_at=datetime(2026, 1, 1))
    with pytest.raises(ValidationError):
        Directory.model_validate({"users": [], "allow_anonymous": True})


def test_relative_access_directory_is_repo_relative(world):
    settings = Settings(_env_file=None, rbac_users_file="backend/data/access.json")
    assert settings.rbac_users_file == ROOT_DIR / "backend/data/access.json"


def test_authentication_secrets_never_appear_in_errors_or_logs(world, caplog):
    provision(world)
    secret_token = token(world, aud="wrong-api")
    response = client.get("/me", headers={"Authorization": f"Bearer {secret_token}"})
    assert response.status_code == 401
    assert secret_token not in response.text + caplog.text
