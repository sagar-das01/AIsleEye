import hashlib
import logging
import threading
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app import audit
from app.agent import orchestrator, tools
from app.pipeline.scan import load_cached
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


@router.post("/scan/{scan_id}/approve")
def approve(scan_id: Annotated[str, SCAN_ID], body: ApproveRequest, response: Response) -> dict:
    scan = load_cached(scan_id)
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


@router.get("/inventory")
def inventory() -> dict:
    return tools.read_inventory()


@router.get("/tickets")
def tickets() -> list[dict]:
    return [{**t, "pdf_url": f"/tickets/{t['ticket_id']}.pdf"} for t in tools.list_tickets()]


@router.get("/tickets/{ticket_id}.pdf")
def ticket_pdf(ticket_id: Annotated[str, TICKET_ID]) -> FileResponse:
    pdf = tools.tickets_dir() / f"{ticket_id}.pdf"
    if not pdf.is_file():
        raise HTTPException(404, "Ticket not found.")
    return FileResponse(pdf, media_type="application/pdf", filename=f"{ticket_id}.pdf")


@router.get("/compliance")
def compliance() -> list[dict]:
    return tools.list_compliance()


@router.get("/audit")
def audit_log() -> list[dict]:
    return audit.read_all()
