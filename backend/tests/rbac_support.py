"""Shared helpers for tests that exercise real demo sign-in, sessions and authorization."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import personas, sessions, workflow
from app.agent import tools
from app.auth import CSRF_HEADER, SESSION_COOKIE
from app.config import get_settings
from app.main import app

ORIGIN = "http://localhost:3000"  # conftest's CORS_ORIGINS


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("DEMO_SIGN_IN_ENABLED", "true")
    monkeypatch.setenv("SESSION_FILE", str(tmp_path / "sessions.json"))
    monkeypatch.setenv("RBAC_USERS_FILE", str(tmp_path / "users.json"))
    get_settings.cache_clear()
    monkeypatch.setattr(tools, "DATA_DIR", tmp_path)
    return SimpleNamespace(path=tmp_path)


def anonymous() -> TestClient:
    return TestClient(app, headers={"Origin": ORIGIN})


def sign_in(persona_id: str) -> TestClient:
    client = anonymous()
    response = client.post("/auth/demo-sign-in", json={"persona_id": persona_id})
    assert response.status_code == 200, response.text
    client.headers[CSRF_HEADER] = response.json()["csrf_token"]
    return client


def session_for(user, monkeypatch) -> TestClient:
    """A session for an ad-hoc scoped user, to test policies the seeded personas do not cover."""
    real = personas.find_user
    monkeypatch.setattr(personas, "find_user", lambda uid: user if uid == user.subject else real(uid))
    token, session = sessions.create(user.subject)
    client = anonymous()
    client.cookies.set(SESSION_COOKIE, token)
    client.headers[CSRF_HEADER] = session.csrf_token
    return client


def seed_ticket(world, ticket_id="T-1", *, pdf=True, **fields) -> dict:
    ticket = workflow.insert_ticket({
        "ticket_id": ticket_id, "finding_id": "dmg_box_1", "finding_type": "box_damage", "title": f"Ticket {ticket_id}",
        "severity": "high", "assignee_team": "Maintenance", "description": "Crushed carton", "scan_id": "0123456789abcdef",
        **fields,
    })
    if pdf:
        (world.path / "tickets" / f"{ticket_id}.pdf").write_bytes(b"%PDF-1.4 test")
    return ticket


def assign(client: TestClient, ticket_id: str, assignee: str, **extra):
    body = {"assignee_id": assignee, "location": "Aisle 4, bay 2", "instructions": "Restack and wrap the pallet.", **extra}
    return client.post(f"/tickets/{ticket_id}/assignment", json=body)
