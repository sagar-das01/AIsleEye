"""Ticket lifecycle: list, details, assignment, acknowledgement, notes, resolution, history and the in-app inbox."""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import personas, workflow
from app.agent import tools
from app.auth import (
    CurrentUser, TicketAssigner, TicketReader, User, can_assign_ticket, can_view_ticket, can_work_on, is_current_assignee,
)

router = APIRouter()

TICKET_ID = Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")
NOTIFICATION_ID = Path(pattern=r"^N-[0-9a-f]{12}$")
PERSONA_ID_PATTERN = r"^[a-z0-9-]{1,40}$"


class AssignmentRequest(BaseModel):
    assignee_id: str = Field(pattern=PERSONA_ID_PATTERN)
    expected_version: int | None = Field(default=None, ge=1)
    reason: str | None = Field(default=None, max_length=300)
    location: str | None = Field(default=None, max_length=300)
    instructions: str | None = Field(default=None, max_length=1000)


class AcknowledgeRequest(BaseModel):
    expected_version: int | None = Field(default=None, ge=1)


class NoteRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class ResolveRequest(BaseModel):
    notes: str = Field(min_length=1, max_length=1000)
    expected_version: int | None = Field(default=None, ge=1)


def _names() -> dict[str, str]:
    return {p.id: p.display_name for p in personas.PERSONAS}


def _view(ticket: dict, user: User, names: dict[str, str]) -> dict:
    return {
        **ticket,
        "pdf_url": f"/tickets/{ticket['ticket_id']}.pdf",
        "assigned_to_name": names.get(ticket.get("assigned_to_user_id") or ""),
        "capabilities": {
            "assign": ticket["status"] != "resolved" and can_assign_ticket(user, ticket),
            "acknowledge": ticket["status"] == "open" and is_current_assignee(user, ticket),
            "resolve": ticket["status"] == "in_progress" and is_current_assignee(user, ticket),
            "note": ticket["status"] != "resolved" and (is_current_assignee(user, ticket) or can_assign_ticket(user, ticket)),
        },
    }


def _visible(ticket_id: str, user: User) -> dict:
    ticket = workflow.get_ticket(ticket_id)
    # Inaccessible and missing tickets look the same, so IDs cannot be probed.
    if ticket is None or not can_view_ticket(user, ticket):
        raise HTTPException(404, "Ticket not found.")
    return ticket


@router.get("/tickets")
def tickets(
    user: TicketReader,
    status: Annotated[Literal["open", "in_progress", "resolved", "active"] | None, Query()] = None,
    mine: bool = False,
) -> list[dict]:
    names = _names()
    rows = [t for t in workflow.list_tickets() if can_view_ticket(user, t)]
    if status == "active":
        rows = [t for t in rows if t["status"] in workflow.ACTIVE_STATUSES]
    elif status:
        rows = [t for t in rows if t["status"] == status]
    if mine:
        rows = [t for t in rows if t.get("assigned_to_user_id") == user.subject]
    return [_view(t, user, names) for t in rows]


# Declared before /tickets/{ticket_id} so "T-1.pdf" is never parsed as a ticket ID.
@router.get("/tickets/{ticket_id}.pdf")
def ticket_pdf(ticket_id: Annotated[str, TICKET_ID], user: TicketReader) -> FileResponse:
    _visible(ticket_id, user)
    pdf = tools.tickets_dir() / f"{ticket_id}.pdf"
    if not pdf.is_file():
        raise HTTPException(404, "Ticket not found.")
    return FileResponse(pdf, media_type="application/pdf", filename=f"{ticket_id}.pdf")


@router.get("/tickets/{ticket_id}")
def ticket_detail(ticket_id: Annotated[str, TICKET_ID], user: TicketReader) -> dict:
    return _view(_visible(ticket_id, user), user, _names())


@router.get("/tickets/{ticket_id}/history")
def ticket_history(ticket_id: Annotated[str, TICKET_ID], user: TicketReader) -> list[dict]:
    _visible(ticket_id, user)
    names = _names()
    return [{**e, "actor_name": names.get(e["actor_id"], e["actor_id"])} for e in workflow.history(ticket_id)]


@router.post("/tickets/{ticket_id}/assignment")
def assign_ticket(ticket_id: Annotated[str, TICKET_ID], body: AssignmentRequest, user: TicketReader) -> dict:
    ticket = _visible(ticket_id, user)
    if not can_assign_ticket(user, ticket):
        raise HTTPException(403, "You do not have permission to assign this ticket.")
    assignee = personas.find_user(body.assignee_id)
    updated = workflow.assign(
        ticket_id, user.subject, body.assignee_id,
        expected_version=body.expected_version, reason=body.reason, location=body.location, instructions=body.instructions,
        eligible=lambda current: assignee is not None and can_work_on(assignee, current),
    )
    return _view(updated, user, _names())


@router.post("/tickets/{ticket_id}/acknowledge")
def acknowledge_ticket(ticket_id: Annotated[str, TICKET_ID], body: AcknowledgeRequest, user: TicketReader) -> dict:
    _visible(ticket_id, user)
    updated = workflow.acknowledge(
        ticket_id, user.subject, expected_version=body.expected_version,
        authorized=lambda current: is_current_assignee(user, current),
    )
    return _view(updated, user, _names())


@router.post("/tickets/{ticket_id}/notes")
def add_ticket_note(ticket_id: Annotated[str, TICKET_ID], body: NoteRequest, user: TicketReader) -> dict:
    _visible(ticket_id, user)
    return workflow.add_note(
        ticket_id, user.subject, body.text,
        authorized=lambda current: is_current_assignee(user, current) or can_assign_ticket(user, current),
    )


@router.post("/tickets/{ticket_id}/resolve")
def resolve_ticket(ticket_id: Annotated[str, TICKET_ID], body: ResolveRequest, user: TicketReader) -> dict:
    _visible(ticket_id, user)
    updated = workflow.resolve(
        ticket_id, user.subject, body.notes, expected_version=body.expected_version,
        authorized=lambda current: is_current_assignee(user, current),
    )
    return _view(updated, user, _names())


@router.get("/technicians")
def technicians(
    user: TicketAssigner, ticket_id: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9_-]{1,64}$")] = None,
) -> list[dict]:
    """Eligible assignees only, never a general user directory."""
    ticket: dict = {}
    if ticket_id:
        ticket = _visible(ticket_id, user)
        if not can_assign_ticket(user, ticket):
            raise HTTPException(403, "You do not have permission to assign this ticket.")
    open_load: dict[str, int] = {}
    for t in workflow.list_tickets():
        if t["status"] in workflow.ACTIVE_STATUSES and t.get("assigned_to_user_id"):
            open_load[t["assigned_to_user_id"]] = open_load.get(t["assigned_to_user_id"], 0) + 1
    return [
        {"user_id": u.subject, "display_name": u.display_name, "active_tickets": open_load.get(u.subject, 0)}
        for u in personas.all_users()
        if can_work_on(u, ticket)
    ]


@router.get("/notifications")
def notifications(user: CurrentUser) -> dict:
    inbox = workflow.notifications_for(user.subject)
    return {"unread": sum(n["read_at"] is None for n in inbox), "items": inbox}


@router.post("/notifications/{notification_id}/read")
def read_notification(notification_id: Annotated[str, NOTIFICATION_ID], user: CurrentUser) -> dict:
    return workflow.mark_read(notification_id, user.subject)

