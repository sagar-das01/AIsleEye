import io
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image as PdfImage
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table

from app.config import BACKEND_DIR, DATA_DIR
from app.sanitize import sanitize_text
from app.storage import read_json, update_json

SEVERITIES = ("low", "medium", "high")
TEAMS = ("Maintenance", "Safety", "Inventory")
SEED_INVENTORY = BACKEND_DIR / "data" / "inventory.seed.json"

TITLE_CHARS, TEXT_CHARS, LABEL_CHARS = 120, 1000, 100


class ToolArgumentError(ValueError):
    pass


def _clean(value: Any, name: str, max_len: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ToolArgumentError(f"{name} must be a non-empty string.")
    cleaned = sanitize_text(value, max_len)
    if not cleaned:
        raise ToolArgumentError(f"{name} has no usable text after removing markup.")
    return cleaned


def _choice(value: Any, name: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise ToolArgumentError(f"{name} must be one of {', '.join(allowed)}.")
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ---- tickets --------------------------------------------------------------------------------------------------

def tickets_dir() -> Path:
    return DATA_DIR / "tickets"


def list_tickets() -> list[dict]:
    from app import workflow

    return workflow.list_tickets()


def _render_pdf(path: Path, ticket: dict, crop: Any) -> None:
    styles = getSampleStyleSheet()
    story: list = [Paragraph(f"Incident ticket {ticket['ticket_id']}", styles["Title"]), Spacer(1, 2 * mm)]
    # The PDF is never re-rendered, so it must not pass for the ticket's current assignment or status.
    story += [Paragraph("Creation snapshot: see the app for the current assignee and status.", styles["Italic"]), Spacer(1, 3 * mm)]
    story.append(Paragraph(ticket["title"], styles["Heading2"]))
    meta = [
        ["Severity", ticket["severity"]],
        ["Assigned team", ticket["assignee_team"]],
        ["Finding", ticket["finding_id"]],
        ["Scan", ticket.get("scan_id") or "-"],
        ["Created", ticket["created_at"]],
        ["Location", ticket.get("location") or "-"],
        ["Instructions", ticket.get("instructions") or "-"],
    ]
    story += [Table(meta, hAlign="LEFT", colWidths=[35 * mm, 110 * mm]), Spacer(1, 5 * mm)]
    story += [Paragraph(ticket["description"], styles["BodyText"]), Spacer(1, 5 * mm)]
    if crop is not None:
        buf = io.BytesIO()
        crop.convert("RGB").save(buf, format="JPEG", quality=85)
        buf.seek(0)
        width, height = ImageReader(buf).getSize()
        scale = min(1.0, 120 * mm / width)
        buf.seek(0)
        story.append(PdfImage(buf, width=width * scale, height=height * scale))
    SimpleDocTemplate(str(path), pagesize=A4, title=ticket["title"], author="AIsleEye").build(story)


def create_incident_ticket(
    finding_id: str, title: str, severity: str, assignee_team: str, description: str, crop: Any = None,
    *, scan_id: str | None = None, trusted: dict | None = None,
) -> dict:
    """`trusted` carries server- or supervisor-supplied context (finding type, SKU, location, instructions), never model output."""
    from app import workflow

    trusted = trusted or {}
    ticket = {
        "ticket_id": f"T-{uuid.uuid4().hex[:10]}",
        "scan_id": scan_id,
        "finding_id": _clean(finding_id, "finding_id", LABEL_CHARS),
        "title": _clean(title, "title", TITLE_CHARS),
        "severity": _choice(severity, "severity", SEVERITIES),
        "assignee_team": _choice(assignee_team, "assignee_team", TEAMS),
        "description": _clean(description, "description", TEXT_CHARS),
        "status": "open",
        "created_at": _now(),
        "finding_type": trusted.get("finding_type"),
        "sku": trusted.get("sku"),
        "location": trusted.get("location"),
        "instructions": trusted.get("instructions"),
    }
    pdf = tickets_dir() / f"{ticket['ticket_id']}.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    _render_pdf(pdf, ticket, crop)
    stored = workflow.insert_ticket(ticket)
    return {**stored, "pdf_path": str(pdf)}


# ---- inventory ------------------------------------------------------------------------------------------------

def inventory_path() -> Path:
    path = DATA_DIR / "inventory.json"
    if not path.exists() and SEED_INVENTORY.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SEED_INVENTORY, path)
    return path


DEFAULT_REORDER_POINT = 10


def read_inventory() -> dict:
    return read_json(inventory_path(), default={})


def stock_board() -> list[dict]:
    """Inventory rows for the stock board; `reorder_point` is optional in the file and defaults for older data."""
    rows = []
    for sku, item in sorted(read_inventory().items()):
        reorder = item.get("reorder_point", DEFAULT_REORDER_POINT)
        rows.append({
            "sku": sku, "available": item["available"], "quarantine": item["quarantine"],
            "total": item["available"] + item["quarantine"], "reorder_point": reorder,
            "low_stock": item["available"] < reorder,
        })
    return rows


def adjust_inventory(sku: str, delta: int, reason: str, *, scan_id: str | None = None, finding_id: str | None = None) -> dict:
    if isinstance(delta, bool) or not isinstance(delta, int) or delta <= 0:
        raise ToolArgumentError("delta must be a positive integer.")
    sku = _clean(sku, "sku", LABEL_CHARS)
    _clean(reason, "reason", LABEL_CHARS * 3)

    def move(inventory: dict) -> dict:
        item = inventory.get(sku)
        if item is None:
            raise ToolArgumentError(f"Unknown SKU {sku}.")
        if item["available"] < delta:
            raise ToolArgumentError(f"Only {item['available']} unit(s) of {sku} are available.")
        inventory[sku] = {**item, "available": item["available"] - delta, "quarantine": item["quarantine"] + delta}
        return inventory

    updated = update_json(inventory_path(), move, default={})
    return {"sku": sku, **updated[sku]}


def set_inventory_count(sku: str, counted: int, reason: str, *, max_adjust: int) -> dict:
    """Set available stock to a supervisor-confirmed physical count, within a cap; quarantine is untouched."""
    if isinstance(counted, bool) or not isinstance(counted, int) or counted < 0:
        raise ToolArgumentError("counted must be a whole number of zero or more.")
    sku = _clean(sku, "sku", LABEL_CHARS)
    _clean(reason, "reason", LABEL_CHARS * 3)
    before: dict[str, int] = {}

    def apply(inventory: dict) -> dict:
        item = inventory.get(sku)
        if item is None:
            raise ToolArgumentError(f"Unknown SKU {sku}.")
        change = counted - item["available"]
        if abs(change) > max_adjust:
            raise ToolArgumentError(f"A change of {change:+d} exceeds the {max_adjust}-unit reconcile limit; recount or adjust in person.")
        before["available"] = item["available"]
        inventory[sku] = {**item, "available": counted}
        return inventory

    updated = update_json(inventory_path(), apply, default={})
    return {"sku": sku, "previous_available": before["available"], "change": counted - before["available"], **updated[sku]}


# ---- compliance log -------------------------------------------------------------------------------------------

def compliance_path() -> Path:
    return DATA_DIR / "compliance_log.json"


def list_compliance() -> list[dict]:
    return read_json(compliance_path(), default=[])


def log_compliance_event(
    category: str, location: str, severity: str, note: str, *, scan_id: str | None = None, finding_id: str | None = None
) -> dict:
    event = {
        "event_id": f"E-{uuid.uuid4().hex[:10]}",
        "timestamp": _now(),
        "category": _clean(category, "category", LABEL_CHARS),
        "location": _clean(location, "location", LABEL_CHARS),
        "severity": _choice(severity, "severity", SEVERITIES),
        "note": _clean(note, "note", TEXT_CHARS),
        "scan_id": scan_id,
        "finding_id": finding_id,
    }
    update_json(compliance_path(), lambda log: [*log, event], default=[])
    return event
