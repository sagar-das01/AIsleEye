import hashlib
import logging
import threading
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Response
from pydantic import BaseModel, Field

from app import annotations, audit, observability, replay
from app.agent import orchestrator, tools
from app.auth import Permission, User, require_permission
from app.config import get_settings
from app.schemas import ScanResult
from app.storage import read_json, write_json

logger = logging.getLogger(__name__)
router = APIRouter()

SCAN_ID = Path(pattern=r"^[0-9a-f]{16}$")
SKU = Annotated[str, Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9_.-]+$")]

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class FindingDetails(BaseModel):
    location: str | None = Field(default=None, max_length=300)
    instructions: str | None = Field(default=None, max_length=1000)


class ApproveRequest(BaseModel):
    finding_ids: list[str] = Field(min_length=1, max_length=100)
    skus: dict[str, Annotated[str, Field(min_length=1, max_length=60)]] = Field(default_factory=dict)
    # Supervisor-confirmed context so the ticket is actionable; it is stored as given, never invented by the model.
    details: dict[str, FindingDetails] = Field(default_factory=dict, max_length=100)


class ReconcileRequest(BaseModel):
    sku: SKU
    exclude_damaged: bool = True
    confirm_needs_review: bool = False


def _approval_key(scan_id: str, finding_ids: list[str]) -> str:
    return hashlib.sha256(f"{scan_id}|{','.join(sorted(set(finding_ids)))}".encode()).hexdigest()[:32]


@router.post("/scan/{scan_id}/approve", dependencies=[Depends(require_permission(Permission.APPROVE))])
def approve(scan_id: Annotated[str, SCAN_ID], body: ApproveRequest, response: Response) -> dict:
    scan = replay.find_scan(scan_id)
    if scan is None:
        raise HTTPException(404, "Scan not found.")
    # The agent dispatches what the supervisor reviewed: corrections and added findings, never rejected ones.
    scan = annotations.apply(scan)
    unknown = sorted(set(body.finding_ids) - {f.id for f in scan.findings})
    if unknown:
        raise HTTPException(422, f"Unknown finding id(s): {', '.join(u[:40] for u in unknown[:5])}")
    rejected = sorted({f.id for f in scan.findings if f.review_status == "rejected"} & set(body.finding_ids))
    if rejected:
        raise HTTPException(422, f"Rejected finding(s) cannot be dispatched: {', '.join(r[:40] for r in rejected[:5])}")

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
        details = {k: v.model_dump() for k, v in body.details.items() if k in body.finding_ids}
        run = orchestrator.run_approved_scan(scan, body.finding_ids, skus=body.skus, details=details)
        if run.error:
            raise HTTPException(502, "The agent service is unavailable. Please try again shortly.")
        payload = run.to_dict()
        payload["tickets"] = [a["result"] for a in payload["actions"] if a["tool"] == "create_incident_ticket" and a["status"] == "executed"]
        write_json(path, payload)
        annotations.mark_dispatched(scan_id, body.finding_ids)
        return payload


@router.get("/inventory", dependencies=[Depends(require_permission(Permission.INVENTORY))])
def inventory() -> dict:
    return tools.read_inventory()


@router.get("/inventory/board", dependencies=[Depends(require_permission(Permission.INVENTORY))])
def inventory_board() -> list[dict]:
    return tools.stock_board()


def shelf_count(scan: ScanResult, exclude_damaged: bool) -> dict:
    """Box count from the scan's own masks (no extra model call); low-confidence boxes make it "needs review"."""
    boxes = [o for o in scan.objects if o.label == "box"]
    damaged = sum(f.type == "box_damage" and f.review_status != "rejected" for f in scan.findings)
    threshold = get_settings().review_confidence
    low = sum(o.confidence < threshold for o in boxes)
    return {
        "scan_id": scan.scan_id, "boxes": len(boxes), "damaged": damaged,
        "counted": max(0, len(boxes) - damaged) if exclude_damaged else len(boxes),
        "exclude_damaged": exclude_damaged, "low_confidence": low,
        "needs_review": low > 0 or bool(scan.uninspected_boxes),
    }


@router.get("/scan/{scan_id}/count", dependencies=[Depends(require_permission(Permission.INVENTORY))])
def scan_count(scan_id: Annotated[str, SCAN_ID], exclude_damaged: bool = True) -> dict:
    scan = replay.find_scan(scan_id)
    if scan is None:
        raise HTTPException(404, "Scan not found.")
    return shelf_count(annotations.apply(scan), exclude_damaged)


@router.post("/scan/{scan_id}/reconcile")
def reconcile(
    scan_id: Annotated[str, SCAN_ID], body: ReconcileRequest, response: Response,
    user: Annotated[User, Depends(require_permission(Permission.RECONCILE))],
) -> dict:
    """Guarded "Adjust to counted": human-initiated, capped, once per SKU per scan, always audited."""
    scan = replay.find_scan(scan_id)
    if scan is None:
        raise HTTPException(404, "Scan not found.")
    count = shelf_count(annotations.apply(scan), body.exclude_damaged)
    args = {"sku": body.sku, "counted": count["counted"], "actor_id": user.subject}

    def rejected(status: int, reason: str) -> HTTPException:
        audit.record(scan_id, None, "set_inventory_count", args, "rejected", reason)
        return HTTPException(status, reason)

    if count["needs_review"] and not body.confirm_needs_review:
        raise rejected(409, "The box count needs review (low-confidence or uninspected boxes). Confirm it before adjusting stock.")
    path = tools.DATA_DIR / "reconciles" / f"{scan_id}_{hashlib.sha256(body.sku.encode()).hexdigest()[:16]}.json"
    with _locks_guard:
        lock = _locks.setdefault(str(path), threading.Lock())
    with lock, observability.span("reconcile", scan_id=scan_id, as_type="tool", sku=body.sku, counted=count["counted"]):
        stored = read_json(path)
        if stored is not None:
            if stored["counted"] != count["counted"]:
                raise rejected(409, f"{body.sku} was already reconciled for this scan.")
            response.headers["Idempotent-Replay"] = "true"
            return stored
        try:
            result = tools.set_inventory_count(
                body.sku, count["counted"], f"Shelf count from scan {scan_id}", max_adjust=get_settings().inventory_max_adjust,
            )
        except tools.ToolArgumentError as exc:
            raise rejected(422, str(exc)) from None
        payload = {**result, "counted": count["counted"], "scan_id": scan_id, "count": count}
        write_json(path, payload)
        audit.record(scan_id, None, "set_inventory_count", args, "executed")
        return payload


@router.get("/compliance", dependencies=[Depends(require_permission(Permission.COMPLIANCE))])
def compliance() -> list[dict]:
    return tools.list_compliance()


@router.get("/audit", dependencies=[Depends(require_permission(Permission.AUDIT))])
def audit_log() -> list[dict]:
    return audit.read_all()
