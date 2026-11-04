"""Opaque server-side demo sessions. Only a SHA-256 digest of each token is stored; the raw token lives in the cookie."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import get_settings
from app.storage import read_json, update_json


class SessionStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class Session:
    session_id: str
    user_id: str
    csrf_token: str
    created_at: datetime
    expires_at: datetime


def _path() -> Path:
    return get_settings().session_file


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _load(raw: object) -> dict[str, dict]:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or not isinstance(raw.get("sessions"), dict):
        raise SessionStoreError("invalid session store")
    return raw["sessions"]


def _read() -> dict[str, dict]:
    try:
        return _load(read_json(_path()))
    except (OSError, ValueError) as exc:
        raise SessionStoreError("unreadable session store") from exc


def _update(fn) -> None:
    def apply(raw: object) -> dict:
        store = _load(raw)
        now = datetime.now(UTC).isoformat()
        # Expired sessions are dropped on every write so the file cannot grow without bound.
        store = {k: v for k, v in store.items() if v.get("expires_at", "") > now}
        fn(store)
        return {"sessions": store}

    try:
        update_json(_path(), apply)
    except (OSError, ValueError) as exc:
        raise SessionStoreError("unwritable session store") from exc


def _to_session(digest: str, record: dict) -> Session:
    try:
        return Session(
            session_id=digest[:16],
            user_id=str(record["user_id"]),
            csrf_token=str(record["csrf_token"]),
            created_at=datetime.fromisoformat(record["created_at"]),
            expires_at=datetime.fromisoformat(record["expires_at"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SessionStoreError("invalid session record") from exc


def create(user_id: str) -> tuple[str, Session]:
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    record = {
        "user_id": user_id,
        "csrf_token": secrets.token_urlsafe(32),
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=get_settings().session_ttl_hours)).isoformat(),
    }
    digest = _digest(token)
    _update(lambda store: store.__setitem__(digest, record))
    return token, _to_session(digest, record)


def lookup(token: str) -> Session | None:
    digest = _digest(token)
    record = _read().get(digest)
    if record is None:
        return None
    session = _to_session(digest, record)
    if session.expires_at <= datetime.now(UTC):
        return None
    return session


def revoke(token: str) -> None:
    digest = _digest(token)
    _update(lambda store: store.pop(digest, None))


def revoke_user(user_id: str) -> None:
    def drop(store: dict) -> None:
        for key in [k for k, v in store.items() if v.get("user_id") == user_id]:
            del store[key]

    _update(drop)
