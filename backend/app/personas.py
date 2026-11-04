"""Seeded demo personas. Sign-in picks one by ID; its roles and scopes always come from here, never from the browser."""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.identity import Grant, Role, User
from app.storage import read_json, update_json


@dataclass(frozen=True)
class Persona:
    id: str
    label: str
    display_name: str
    description: str
    core: bool
    grants: tuple[tuple[Role, str | None], ...]  # (role, team scope)


PERSONAS: tuple[Persona, ...] = (
    Persona("admin", "Admin", "Avery (Admin)", "Everything, including user access and the audit log.", True,
            ((Role.ADMIN, None),)),
    Persona("ops-manager", "Operations Manager", "Omar (Operations Manager)", "Warehouse-wide backlog, assignment and verification.", True,
            ((Role.OPERATIONS_MANAGER, None),)),
    Persona("floor-manager", "Floor Manager", "Fatima (Floor Manager)", "Scan, review, approve and assign technicians.", True,
            ((Role.FLOOR_MANAGER, None),)),
    Persona("tech-a", "Technician A", "Tariq (Technician A)", "Sees and works only on tickets assigned to Tariq.", True,
            ((Role.TECHNICIAN, None),)),
    Persona("tech-b", "Technician B", "Bea (Technician B)", "Sees and works only on tickets assigned to Bea.", True,
            ((Role.TECHNICIAN, None),)),
    Persona("reporter", "Reporter", "Rui (Floor Associate)", "No operational access in this MVP.", True,
            ((Role.REPORTER, None),)),
    Persona("auditor", "Auditor", "Ada (Auditor)", "Read-only tickets, compliance log and audit log.", True,
            ((Role.AUDITOR, None),)),
    Persona("dispatcher", "Dispatcher", "Dev (Safety Dispatcher)", "Assigns Safety tickets only.", False,
            ((Role.DISPATCHER, "Safety"),)),
    Persona("safety-officer", "Safety Officer", "Sol (Safety Officer)", "Reads Safety tickets.", False,
            ((Role.SAFETY_OFFICER, None),)),
    Persona("inventory-controller", "Inventory Controller", "Ines (Inventory Controller)", "Stock, reconciliation and Inventory tickets.", False,
            ((Role.INVENTORY_CONTROLLER, None),)),
    Persona("quality-inspector", "Quality Inspector", "Quinn (Quality Inspector)", "Reads damaged-goods tickets.", False,
            ((Role.QUALITY_INSPECTOR, None),)),
    Persona("viewer", "Viewer", "Vik (Viewer)", "Score cards only.", False,
            ((Role.VIEWER, None),)),
    Persona("contractor", "Contractor", "Cleo (Contractor)", "Works only on individually assigned tickets.", False,
            ((Role.CONTRACTOR, None),)),
)
BY_ID = {p.id: p for p in PERSONAS}


class UserStoreError(RuntimeError):
    pass


def _overrides_path() -> Path:
    return get_settings().rbac_users_file


def _overrides() -> dict[str, dict]:
    try:
        raw = read_json(_overrides_path(), default={"users": {}})
    except (OSError, ValueError) as exc:
        raise UserStoreError("unreadable user store") from exc
    # A broken file must never silently re-enable a disabled account, so its shape is checked, not defaulted.
    if not isinstance(raw, dict) or not isinstance(raw.get("users"), dict):
        raise UserStoreError("invalid user store")
    return raw["users"]


def _user(persona: Persona, override: dict) -> User:
    warehouse = get_settings().rbac_warehouse_id
    return User(
        subject=persona.id,
        display_name=persona.display_name,
        active=override.get("active", True) is True,
        grants=[Grant(role=role, warehouse_id=warehouse, team=team) for role, team in persona.grants],
    )


def find_user(user_id: str) -> User | None:
    persona = BY_ID.get(user_id)
    return None if persona is None else _user(persona, _overrides().get(user_id, {}))


def all_users() -> list[User]:
    overrides = _overrides()
    return [_user(p, overrides.get(p.id, {})) for p in PERSONAS]


def set_active(user_id: str, active: bool) -> User:
    if user_id not in BY_ID:
        raise KeyError(user_id)

    def apply(raw: object) -> dict:
        if raw is None:
            raw = {"users": {}}
        if not isinstance(raw, dict) or not isinstance(raw.get("users"), dict):
            raise UserStoreError("invalid user store")
        data = raw
        data["users"][user_id] = {"active": active, "updated_at": datetime.now(UTC).isoformat()}
        return data

    update_json(_overrides_path(), apply)
    return find_user(user_id)  # type: ignore[return-value]
