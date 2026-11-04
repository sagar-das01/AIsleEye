from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Role(StrEnum):
    ADMIN = "admin"
    OPERATIONS_MANAGER = "operations_manager"
    FLOOR_MANAGER = "floor_manager"
    TECHNICIAN = "technician"
    REPORTER = "reporter"
    AUDITOR = "auditor"
    DISPATCHER = "dispatcher"
    SAFETY_OFFICER = "safety_officer"
    INVENTORY_CONTROLLER = "inventory_controller"
    QUALITY_INSPECTOR = "quality_inspector"
    VIEWER = "viewer"
    CONTRACTOR = "contractor"


class Permission(StrEnum):
    SCAN = "scan"
    REVIEW = "review"
    APPROVE = "approve"
    DELETE_SCAN = "delete_scan"
    TICKETS = "tickets"
    ASSIGN = "assign"
    WORK = "work"
    INVENTORY = "inventory"
    RECONCILE = "reconcile"
    COMPLIANCE = "compliance"
    AUDIT = "audit"
    SCORES = "scores"
    USERS = "users"


MANAGER_PERMISSIONS = frozenset({
    Permission.SCAN, Permission.REVIEW, Permission.APPROVE, Permission.DELETE_SCAN, Permission.TICKETS,
    Permission.ASSIGN, Permission.INVENTORY, Permission.RECONCILE, Permission.COMPLIANCE, Permission.SCORES,
})
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.OPERATIONS_MANAGER: MANAGER_PERMISSIONS,
    Role.FLOOR_MANAGER: MANAGER_PERMISSIONS,
    Role.TECHNICIAN: frozenset({Permission.TICKETS, Permission.WORK}),
    Role.REPORTER: frozenset(),
    Role.AUDITOR: frozenset({Permission.TICKETS, Permission.COMPLIANCE, Permission.AUDIT}),
    Role.DISPATCHER: frozenset({Permission.TICKETS, Permission.ASSIGN}),
    Role.SAFETY_OFFICER: frozenset({Permission.TICKETS}),
    Role.INVENTORY_CONTROLLER: frozenset({Permission.TICKETS, Permission.INVENTORY, Permission.RECONCILE}),
    Role.QUALITY_INSPECTOR: frozenset({Permission.TICKETS}),
    Role.VIEWER: frozenset({Permission.SCORES}),
    Role.CONTRACTOR: frozenset({Permission.TICKETS, Permission.WORK}),
}


class Grant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role
    warehouse_id: str = Field(min_length=1, max_length=100)
    area_id: str | None = Field(default=None, min_length=1, max_length=100)
    team: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def dispatcher_requires_team(self) -> "Grant":
        if self.role == Role.DISPATCHER and self.team is None:
            raise ValueError("Dispatchers require an explicit team scope.")
        return self


class User(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Stable user ID; ticket ownership always references it, never a shared role.
    subject: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=120)
    active: bool = True
    expires_at: datetime | None = None
    grants: list[Grant] = Field(default_factory=list)

    @model_validator(mode="after")
    def expiry_requires_timezone(self) -> "User":
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("expires_at must include a timezone.")
        return self
