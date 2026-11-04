import hmac
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app import audit, personas, sessions
from app.config import get_settings
from app.identity import ROLE_PERMISSIONS, Grant, Permission, Role, User

__all__ = ["ROLE_PERMISSIONS", "Grant", "Permission", "Role", "User"]

logger = logging.getLogger(__name__)


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
SESSION_COOKIE = "aisleeye_session"
CSRF_HEADER = "X-CSRF-Token"


def check_origin(request: Request) -> None:
    """Browsers always send Origin on cross-site mutations; an unknown one is refused before anything changes."""
    origin = request.headers.get("origin")
    if origin is not None and origin not in get_settings().cors_origins:
        logger.warning("Rejected a state-changing request from an unexpected origin")
        raise HTTPException(403, "Request origin is not allowed.")


def is_active(user: User) -> bool:
    return user.active and (user.expires_at is None or user.expires_at > datetime.now(UTC))


def resolve_session(request: Request) -> tuple[User, sessions.Session]:
    if not get_settings().demo_sign_in_enabled:
        raise HTTPException(401, "Demo sign-in is disabled.")
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "Authentication required.")
    try:
        session = sessions.lookup(token)
    except sessions.SessionStoreError:
        logger.error("The session store is unavailable or invalid")
        raise HTTPException(503, "Session storage is unavailable.") from None
    if session is None:
        raise HTTPException(401, "Your session has expired. Sign in again.")
    try:
        user = personas.find_user(session.user_id)
    except personas.UserStoreError:
        logger.error("The demo user store is unavailable or invalid")
        raise HTTPException(503, "Access configuration is unavailable.") from None
    if user is None or not is_active(user):
        raise HTTPException(401, "This demo account is disabled. Choose another role.")
    if request.method not in SAFE_METHODS:
        check_origin(request)
        if not hmac.compare_digest(request.headers.get(CSRF_HEADER, ""), session.csrf_token):
            raise HTTPException(403, "Missing or invalid CSRF token. Refresh the page and try again.")
    return user, session


def current_user(request: Request) -> User:
    return resolve_session(request)[0]


CurrentUser = Annotated[User, Depends(current_user)]


def grant_matches(grant: Grant, warehouse_id: str, area_id: str | None = None, team: str | None = None) -> bool:
    return (
        grant.warehouse_id == warehouse_id
        and (grant.area_id is None or grant.area_id == area_id)
        and (grant.team is None or grant.team == team)
    )


def has_permission(user: User, permission: Permission) -> bool:
    warehouse = get_settings().rbac_warehouse_id
    return any(
        grant.role == Role.ADMIN
        or (permission in ROLE_PERMISSIONS[grant.role] and grant_matches(grant, warehouse))
        for grant in user.grants
    )


def require_permission(permission: Permission) -> Callable[[User, Request], User]:
    def check(user: CurrentUser, request: Request) -> User:
        if not has_permission(user, permission):
            logger.warning("Permission denied: actor=%s capability=%s", user.subject, permission)
            raise HTTPException(403, "You do not have permission for this operation.")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            audit.record(
                str(request.path_params.get("scan_id", "access")), None, f"authorize:{permission}",
                {"actor_id": user.subject, "resource": request.path_params}, "executed",
            )
        return user

    return check


def can_view_ticket(user: User, ticket: dict) -> bool:
    warehouse = ticket.get("warehouse_id") or get_settings().rbac_warehouse_id
    for grant in user.grants:
        if grant.role == Role.ADMIN:
            return True
        if Permission.TICKETS not in ROLE_PERMISSIONS[grant.role] or not grant_matches(
            grant, warehouse, ticket.get("area_id"), ticket.get("assignee_team"),
        ):
            continue
        if grant.role in {Role.TECHNICIAN, Role.CONTRACTOR}:
            if ticket.get("assigned_to_user_id") == user.subject:
                return True
            continue
        if grant.role == Role.SAFETY_OFFICER and ticket.get("assignee_team") != "Safety":
            continue
        if grant.role == Role.INVENTORY_CONTROLLER and ticket.get("assignee_team") != "Inventory":
            continue
        if grant.role == Role.QUALITY_INSPECTOR and ticket.get("finding_type") != "box_damage":
            continue
        return True
    return False


def has_scoped_permission(user: User, permission: Permission) -> bool:
    """Held in at least one grant for this warehouse, whatever its area or team; objects are then checked one by one."""
    return any(
        grant.role == Role.ADMIN or (
            grant.warehouse_id == get_settings().rbac_warehouse_id and permission in ROLE_PERMISSIONS[grant.role]
        )
        for grant in user.grants
    )


def ticket_reader(user: CurrentUser) -> User:
    if not has_scoped_permission(user, Permission.TICKETS):
        raise HTTPException(403, "You do not have permission to access tickets.")
    return user


def ticket_assigner(user: CurrentUser) -> User:
    if not has_scoped_permission(user, Permission.ASSIGN):
        raise HTTPException(403, "You do not have permission to assign tickets.")
    return user


TicketReader = Annotated[User, Depends(ticket_reader)]
TicketAssigner = Annotated[User, Depends(ticket_assigner)]


def _ticket_scope(ticket: dict) -> tuple[str, str | None, str | None]:
    return (
        ticket.get("warehouse_id") or get_settings().rbac_warehouse_id,
        ticket.get("area_id"),
        ticket.get("assignee_team"),
    )


def can_assign_ticket(user: User, ticket: dict) -> bool:
    """Assignment is a per-grant capability: the ASSIGN grant itself must cover the ticket's warehouse, area and team."""
    scope = _ticket_scope(ticket)
    return any(
        grant.role == Role.ADMIN or (Permission.ASSIGN in ROLE_PERMISSIONS[grant.role] and grant_matches(grant, *scope))
        for grant in user.grants
    )


def _responder_grants(user: User):
    # Admin holds every permission for oversight, but is never a responder that work can be assigned to.
    return (g for g in user.grants if g.role != Role.ADMIN and Permission.WORK in ROLE_PERMISSIONS[g.role])


def is_current_assignee(user: User, ticket: dict) -> bool:
    """Acknowledge, resolve and work notes belong to the individually assigned responder only."""
    if ticket.get("assigned_to_user_id") != user.subject:
        return False
    scope = _ticket_scope(ticket)
    return any(grant_matches(grant, *scope) for grant in _responder_grants(user))


def can_work_on(user: User, ticket: dict, *, team: str | None = None) -> bool:
    """Whether `user` is an eligible assignee for the ticket (active responder whose grant covers its scope)."""
    warehouse, area, ticket_team = _ticket_scope(ticket)
    return is_active(user) and any(
        grant_matches(grant, warehouse, area, team or ticket_team) for grant in _responder_grants(user)
    )
