"""Authoritative ticket workflow store: mutable tickets, lifecycle events and notifications in one atomic JSON file.

Every check (version, assignment, status) and its mutation happen under the same lock and atomic replacement, so a
stale or concurrent request can never apply half a transition. This is safe for the documented single-worker
deployment only; multi-worker use needs a transactional database with conditional updates and an outbox.
"""

import logging
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app import audit
from app.config import get_settings
from app.sanitize import sanitize_text
from app.storage import lock_for, read_json, read_jsonl, update_json

logger = logging.getLogger(__name__)

STATUSES = ("open", "in_progress", "resolved")
ACTIVE_STATUSES = frozenset({"open", "in_progress"})
NOTE_CHARS, DETAIL_CHARS, REASON_CHARS = 1000, 300, 300


class WorkflowError(Exception):
    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFound(WorkflowError):
    status_code = 404


class Conflict(WorkflowError):
    status_code = 409


class Invalid(WorkflowError):
    status_code = 422


class StoreUnavailable(WorkflowError):
    status_code = 503


def _data_dir() -> Path:
    from app.agent import tools  # late import: tools writes new tickets through this module

    return tools.tickets_dir()


def store_path() -> Path:
    return _data_dir() / "workflow.json"


def legacy_index_path() -> Path:
    return _data_dir() / "index.jsonl"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _empty() -> dict:
    return {"schema": 1, "tickets": {}, "events": [], "notifications": [], "migration_issues": []}


def _clean(value: Any, name: str, max_len: int, *, required: bool = True) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise Invalid(f"{name} is required.")
        return None
    if not isinstance(value, str):
        raise Invalid(f"{name} must be text.")
    cleaned = sanitize_text(value, max_len)
    if not cleaned:
        if required:
            raise Invalid(f"{name} has no usable text after removing markup.")
        return None
    return cleaned


# ---- migration ------------------------------------------------------------------------------------------------

def _known_user(user_id: Any) -> bool:
    from app import personas

    return isinstance(user_id, str) and user_id in personas.BY_ID


def _migrate(state: dict, legacy: list[dict]) -> int:
    imported = 0
    for record in legacy:
        ticket_id = record.get("ticket_id") if isinstance(record, dict) else None
        if not isinstance(ticket_id, str) or not ticket_id:
            state["migration_issues"].append({"at": _now(), "issue": "legacy record without a ticket_id was skipped"})
            continue
        if ticket_id in state["tickets"]:
            continue
        status = record.get("status") if record.get("status") in STATUSES else "open"
        assignee = record.get("assigned_to_user_id")
        if assignee is not None and not _known_user(assignee):
            # Never guess an owner: an unknown identity goes to the manager-only unassigned queue and is reported.
            state["migration_issues"].append({"at": _now(), "ticket_id": ticket_id, "issue": "unknown assignee cleared"})
            assignee = None
        state["tickets"][ticket_id] = new_ticket_record({**record, "status": status, "assigned_to_user_id": assignee}, legacy=True)
        imported += 1
    return imported


def _ensure_migrated() -> None:
    legacy = legacy_index_path()
    if not legacy.is_file():
        return
    with lock_for(store_path()):
        if not legacy.is_file():
            return
        records = read_jsonl(legacy)

        def apply(raw: Any) -> dict:
            state = _valid(raw)
            _migrate(state, records)
            return state

        try:
            update_json(store_path(), apply, default=None)
        except ValueError as exc:
            raise StoreUnavailable("Ticket storage is unavailable.") from exc
        # Keep a backup, then retire the legacy index so there is exactly one mutable ticket source.
        backup = legacy.with_name(f"index.{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}.migrated.jsonl")
        shutil.move(str(legacy), backup)
        logger.info("Migrated %d legacy ticket record(s); backup at %s", len(records), backup.name)


def _valid(state: Any) -> dict:
    if state is None:
        return _empty()
    if not isinstance(state, dict) or not isinstance(state.get("tickets"), dict):
        raise StoreUnavailable("Ticket storage is unavailable.")
    for key in ("events", "notifications", "migration_issues"):
        state.setdefault(key, [])
    return state


# ---- reading --------------------------------------------------------------------------------------------------

def _state() -> dict:
    _ensure_migrated()
    try:
        return _valid(read_json(store_path()))
    except ValueError as exc:
        raise StoreUnavailable("Ticket storage is unavailable.") from exc


def list_tickets() -> list[dict]:
    return list(_state()["tickets"].values())


def get_ticket(ticket_id: str) -> dict | None:
    return _state()["tickets"].get(ticket_id)


def history(ticket_id: str) -> list[dict]:
    return [e for e in _state()["events"] if e["ticket_id"] == ticket_id]


def notifications_for(user_id: str) -> list[dict]:
    state = _state()
    inbox = []
    for note in state["notifications"]:
        if note["recipient_id"] != user_id:
            continue
        ticket = state["tickets"].get(note["ticket_id"], {})
        current = ticket.get("assigned_to_user_id") == user_id and ticket.get("assignment_version") == note["assignment_version"]
        # An obsolete notification keeps only a neutral reference; the old assignee must not see ticket details.
        inbox.append({
            **{k: note[k] for k in ("notification_id", "ticket_id", "event_type", "created_at", "read_at")},
            "current": current,
            "title": note["title"] if current else "This ticket is no longer assigned to you.",
            "severity": note.get("severity") if current else None,
        })
    return sorted(inbox, key=lambda n: n["created_at"], reverse=True)


def migration_issues() -> list[dict]:
    return _state()["migration_issues"]


# ---- writing --------------------------------------------------------------------------------------------------

def new_ticket_record(fields: dict, *, legacy: bool = False) -> dict:
    now = _now()
    return {
        "ticket_id": fields["ticket_id"],
        "scan_id": fields.get("scan_id"),
        "finding_id": fields.get("finding_id"),
        "finding_type": fields.get("finding_type"),
        "title": fields.get("title", ""),
        "severity": fields.get("severity"),
        "assignee_team": fields.get("assignee_team"),
        "description": fields.get("description", ""),
        "warehouse_id": fields.get("warehouse_id") or get_settings().rbac_warehouse_id,
        "area_id": fields.get("area_id"),
        "location": fields.get("location"),
        "instructions": fields.get("instructions"),
        "sku": fields.get("sku"),
        "status": fields.get("status", "open"),
        "created_at": fields.get("created_at") or now,
        "updated_at": now,
        "version": 1,
        "assigned_to_user_id": fields.get("assigned_to_user_id"),
        "assigned_by_user_id": None,
        "assigned_at": None,
        "assignment_version": 0,
        "acknowledged_by": None,
        "acknowledged_at": None,
        "resolved_by": None,
        "resolved_at": None,
        "resolution_notes": None,
        "legacy": legacy,
    }


def _event(state: dict, ticket: dict, actor_id: str, event_type: str, **details: Any) -> dict:
    event = {
        "event_id": f"EV-{uuid.uuid4().hex[:12]}",
        "ticket_id": ticket["ticket_id"],
        "type": event_type,
        "actor_id": actor_id,
        "at": _now(),
        "version": ticket["version"],
        **details,
    }
    state["events"].append(event)
    return event


def _mutate(fn: Callable[[dict], Any]) -> Any:
    _ensure_migrated()
    box: dict[str, Any] = {}

    def apply(raw: Any) -> dict:
        state = _valid(raw)
        box["result"] = fn(state)
        return state

    try:
        update_json(store_path(), apply, default=None)
    except ValueError as exc:  # corrupt JSON; workflow errors are not ValueErrors, so they propagate untouched
        raise StoreUnavailable("Ticket storage is unavailable.") from exc
    return box["result"]


def _mirror(ticket: dict, action: str, actor_id: str, **details: Any) -> None:
    # The workflow events are the durable record; the audit log is a best-effort mirror for one shared timeline.
    try:
        audit.record(ticket.get("scan_id") or "workflow", ticket.get("finding_id"), f"ticket:{action}",
                     {"actor_id": actor_id, "ticket_id": ticket["ticket_id"], **details}, "executed")
    except Exception:
        logger.exception("Could not mirror a ticket event to the audit log")


def _ticket(state: dict, ticket_id: str) -> dict:
    ticket = state["tickets"].get(ticket_id)
    if ticket is None:
        raise NotFound("Ticket not found.")
    return ticket


def _check_version(ticket: dict, expected: int | None) -> None:
    if expected is not None and expected != ticket["version"]:
        raise Conflict("This ticket changed since you loaded it. Refresh and try again.")


def _touch(ticket: dict) -> None:
    ticket["version"] += 1
    ticket["updated_at"] = _now()


def insert_ticket(ticket: dict) -> dict:
    record = new_ticket_record(ticket)

    def apply(state: dict) -> dict:
        state["tickets"][record["ticket_id"]] = record
        _event(state, record, "agent", "created", status="open")
        return record

    return _mutate(apply)


def update_details(ticket_id: str, actor_id: str, *, location: str | None, instructions: str | None) -> dict:
    location = _clean(location, "location", DETAIL_CHARS, required=False)
    instructions = _clean(instructions, "instructions", NOTE_CHARS, required=False)

    def apply(state: dict) -> dict:
        ticket = _ticket(state, ticket_id)
        changed = {k: v for k, v in (("location", location), ("instructions", instructions)) if v and v != ticket.get(k)}
        if not changed:
            return ticket
        ticket.update(changed)
        _touch(ticket)
        _event(state, ticket, actor_id, "details_updated", fields=sorted(changed))
        return ticket

    return _mutate(apply)


def assign(
    ticket_id: str, actor_id: str, assignee_id: str, *, expected_version: int | None, reason: str | None,
    location: str | None, instructions: str | None, eligible: Callable[[dict], bool],
) -> dict:
    location = _clean(location, "location", DETAIL_CHARS, required=False)
    instructions = _clean(instructions, "instructions", NOTE_CHARS, required=False)
    reason = _clean(reason, "reason", REASON_CHARS, required=False)
    outcome: dict[str, Any] = {}

    def apply(state: dict) -> dict:
        ticket = _ticket(state, ticket_id)
        _check_version(ticket, expected_version)
        if ticket["status"] == "resolved":
            raise Conflict("Resolved tickets cannot be reassigned.")
        if ticket.get("assigned_to_user_id") == assignee_id:
            return ticket  # repeat of the same assignment: no new event or notification
        if not eligible(ticket):
            raise Invalid("That person is not an active technician eligible for this ticket's team and area.")
        if location:
            ticket["location"] = location
        if instructions:
            ticket["instructions"] = instructions
        if not ticket.get("location") or not ticket.get("instructions"):
            raise Invalid("Add the location and instructions before assigning a technician.")
        previous = ticket.get("assigned_to_user_id")
        if previous and not reason:
            raise Invalid("A reason is required to reassign a ticket.")
        ticket.update({
            "assigned_to_user_id": assignee_id, "assigned_by_user_id": actor_id, "assigned_at": _now(),
            "assignment_version": ticket["assignment_version"] + 1,
            "status": "open", "acknowledged_by": None, "acknowledged_at": None,
        })
        _touch(ticket)
        event_type = "reassigned" if previous else "assigned"
        _event(state, ticket, actor_id, event_type, assignee_id=assignee_id, previous_assignee_id=previous, reason=reason)
        _notify(state, ticket, assignee_id, event_type)
        outcome.update(event=event_type, previous=previous)
        return ticket

    ticket = _mutate(apply)
    if outcome:
        _mirror(ticket, outcome["event"], actor_id, assignee_id=assignee_id, previous_assignee_id=outcome["previous"])
    return ticket


def _notify(state: dict, ticket: dict, recipient_id: str, event_type: str) -> None:
    key = f"{ticket['ticket_id']}:{ticket['assignment_version']}:{recipient_id}:{event_type}"
    if any(n["dedupe_key"] == key for n in state["notifications"]):
        return
    # Stored in the same atomic write as the assignment, so the intent can never be lost or orphaned.
    state["notifications"].append({
        "notification_id": f"N-{uuid.uuid4().hex[:12]}",
        "dedupe_key": key,
        "recipient_id": recipient_id,
        "ticket_id": ticket["ticket_id"],
        "assignment_version": ticket["assignment_version"],
        "event_type": event_type,
        "title": ticket["title"],
        "severity": ticket["severity"],
        "channel": "in_app",
        "delivery": "stored",
        "created_at": _now(),
        "read_at": None,
    })


def acknowledge(ticket_id: str, actor_id: str, *, expected_version: int | None, authorized: Callable[[dict], bool]) -> dict:
    changed: list[bool] = []

    def apply(state: dict) -> dict:
        ticket = _ticket(state, ticket_id)
        if not authorized(ticket):
            raise NotFound("Ticket not found.")
        if ticket["status"] == "in_progress" and ticket["acknowledged_by"] == actor_id:
            return ticket  # repeated tap: already acknowledged, nothing new
        _check_version(ticket, expected_version)
        if ticket["status"] != "open":
            raise Conflict(f"Only new tickets can be acknowledged; this one is {ticket['status'].replace('_', ' ')}.")
        ticket.update({"status": "in_progress", "acknowledged_by": actor_id, "acknowledged_at": _now()})
        _touch(ticket)
        _event(state, ticket, actor_id, "acknowledged", status="in_progress")
        changed.append(True)
        return ticket

    ticket = _mutate(apply)
    if changed:
        _mirror(ticket, "acknowledged", actor_id)
    return ticket


def add_note(ticket_id: str, actor_id: str, text: str, *, authorized: Callable[[dict], bool]) -> dict:
    text = _clean(text, "note", NOTE_CHARS)

    def apply(state: dict) -> dict:
        ticket = _ticket(state, ticket_id)
        if not authorized(ticket):
            raise NotFound("Ticket not found.")
        if ticket["status"] == "resolved":
            raise Conflict("Resolved tickets are closed for notes.")
        _touch(ticket)
        return _event(state, ticket, actor_id, "note", note=text)

    event = _mutate(apply)
    _mirror({"ticket_id": ticket_id}, "note", actor_id)
    return event


def resolve(ticket_id: str, actor_id: str, notes: str, *, expected_version: int | None, authorized: Callable[[dict], bool]) -> dict:
    notes_clean = _clean(notes, "completion notes", NOTE_CHARS)
    changed: list[bool] = []

    def apply(state: dict) -> dict:
        ticket = _ticket(state, ticket_id)
        if not authorized(ticket):
            raise NotFound("Ticket not found.")
        if ticket["status"] == "resolved" and ticket["resolved_by"] == actor_id:
            return ticket
        _check_version(ticket, expected_version)
        if ticket["status"] != "in_progress":
            raise Conflict("Acknowledge the ticket before resolving it.")
        ticket.update({"status": "resolved", "resolved_by": actor_id, "resolved_at": _now(), "resolution_notes": notes_clean})
        _touch(ticket)
        _event(state, ticket, actor_id, "resolved", status="resolved", note=notes_clean)
        changed.append(True)
        return ticket

    ticket = _mutate(apply)
    if changed:
        _mirror(ticket, "resolved", actor_id)
    return ticket


def mark_read(notification_id: str, user_id: str) -> dict:
    def apply(state: dict) -> dict:
        note = next((n for n in state["notifications"] if n["notification_id"] == notification_id), None)
        if note is None or note["recipient_id"] != user_id:
            raise NotFound("Notification not found.")
        if note["read_at"] is None:
            note["read_at"] = _now()  # reading never touches the ticket's status
        return note

    note = _mutate(apply)
    return {k: note[k] for k in ("notification_id", "ticket_id", "read_at")}
