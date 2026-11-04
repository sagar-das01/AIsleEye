import hashlib
import logging
import threading
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import audit, replay
from app.agent import orchestrator, tools
from app.auth import Permission, TicketReader, can_view_ticket, require_permission
from app.storage import read_json, write_json

logger = logging.getLogger(__name__)
router = APIRouter()

SCAN_ID = Path(pattern=r"^[0-9a-f]{16}$")
TICKET_ID = Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class ApproveRequest(BaseModel):
    finding_ids: list[str] = Field(min_length=1, max_length=100)
    skus: dict[str, Annotated[str, Field(min_length=1, max_length=60)]] = Field(default_factory=dict)


def _approval_key(scan_id: str, finding_ids: list[str]) -> str:
    return hashlib.sha256(f"{scan_id}|{','.join(sorted(set(finding_ids)))}".encode()).hexdigest()[:32]


@router.post("/scan/{scan_id}/approve", dependencies=[Depends(require_permission(Permission.APPROVE))])
def approve(scan_id: Annotated[str, SCAN_ID], body: ApproveRequest, response: Response) -> dict:
    scan = replay.find_scan(scan_id)
    if scan is None:
        raise HTTPException(404, "Scan not found.")
    unknown = sorted(set(body.finding_ids) - {f.id for f in scan.findings})
    if unknown:
        raise HTTPException(422, f"Unknown finding id(s): {', '.join(u[:40] for u in unknown[:5])}")

    key = _approval_key(scan_id, body.finding_ids)
    path = tools.DATA_DIR / "approvals" / f"{key}.json"
    with _locks_guard:
        lock = _locks.setdefault(key, threading.Lock())
    # One run per scan and finding set: a repeat or double-click gets the first result back.
    with lock:
        stored = read_json(path)
        if stored is not None:
            response.headers["Idempotent-Replay"] = "true"
            return stored
        run = orchestrator.run_approved_scan(scan, body.finding_ids, skus=body.skus)
        if run.error:
            raise HTTPException(502, "The agent service is unavailable. Please try again shortly.")
        payload = run.to_dict()
        payload["tickets"] = [a["result"] for a in payload["actions"] if a["tool"] == "create_incident_ticket" and a["status"] == "executed"]
        write_json(path, payload)
        return payload


@router.get("/inventory", dependencies=[Depends(require_permission(Permission.INVENTORY))])
def inventory() -> dict:
    return tools.read_inventory()


@router.get("/tickets")
def tickets(user: TicketReader) -> list[dict]:
    return [
        {**t, "pdf_url": f"/tickets/{t['ticket_id']}.pdf"}
        for t in tools.list_tickets() if can_view_ticket(user, t)
    ]


@router.get("/tickets/{ticket_id}.pdf")
def ticket_pdf(ticket_id: Annotated[str, TICKET_ID], user: TicketReader) -> FileResponse:
    ticket = next((t for t in tools.list_tickets() if t["ticket_id"] == ticket_id), None)
    if ticket is None or not can_view_ticket(user, ticket):
        raise HTTPException(404, "Ticket not found.")
    pdf = tools.tickets_dir() / f"{ticket_id}.pdf"
    if not pdf.is_file():
        raise HTTPException(404, "Ticket not found.")
    return FileResponse(pdf, media_type="application/pdf", filename=f"{ticket_id}.pdf")


@router.get("/compliance", dependencies=[Depends(require_permission(Permission.COMPLIANCE))])
def compliance() -> list[dict]:
    return tools.list_compliance()


@router.get("/audit", dependencies=[Depends(require_permission(Permission.AUDIT))])
def audit_log() -> list[dict]:
    return audit.read_all()
