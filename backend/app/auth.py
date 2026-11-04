import logging
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from functools import lru_cache
from typing import Annotated
from urllib.parse import urlparse

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app import audit
from app.config import get_settings
from app.storage import read_json

logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)


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
    INVENTORY = "inventory"
    COMPLIANCE = "compliance"
    AUDIT = "audit"
    SCORES = "scores"


MANAGER_PERMISSIONS = frozenset({
    Permission.SCAN, Permission.REVIEW, Permission.APPROVE, Permission.DELETE_SCAN,
    Permission.TICKETS, Permission.INVENTORY, Permission.COMPLIANCE, Permission.SCORES,
})
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),
    Role.OPERATIONS_MANAGER: MANAGER_PERMISSIONS,
    Role.FLOOR_MANAGER: MANAGER_PERMISSIONS,
    Role.TECHNICIAN: frozenset({Permission.TICKETS}),
    Role.REPORTER: frozenset(),
    Role.AUDITOR: frozenset({Permission.TICKETS, Permission.COMPLIANCE, Permission.AUDIT}),
    Role.DISPATCHER: frozenset({Permission.TICKETS}),
    Role.SAFETY_OFFICER: frozenset({Permission.TICKETS}),
    Role.INVENTORY_CONTROLLER: frozenset({Permission.TICKETS, Permission.INVENTORY}),
    Role.QUALITY_INSPECTOR: frozenset({Permission.TICKETS}),
    Role.VIEWER: frozenset({Permission.SCORES}),
    Role.CONTRACTOR: frozenset({Permission.TICKETS}),
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


class Directory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    users: list[User]

    @model_validator(mode="after")
    def unique_subjects(self) -> "Directory":
        subjects = [user.subject for user in self.users]
        if len(subjects) != len(set(subjects)):
            raise ValueError("User subjects must be unique.")
        return self


@lru_cache(maxsize=4)
def jwks_client(url: str) -> PyJWKClient:
    return PyJWKClient(url, timeout=5, cache_jwk_set=True, lifespan=300)


def verify_token(token: str) -> str:
    settings = get_settings()
    if not settings.oidc_audience or any(
        urlparse(url).scheme != "https" or not urlparse(url).netloc
        for url in (settings.oidc_issuer, settings.oidc_jwks_url)
    ):
        logger.error("OIDC issuer, audience and HTTPS JWKS URL must be configured")
        raise HTTPException(503, "Authentication is not configured.")
    try:
        key = jwks_client(settings.oidc_jwks_url).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            issuer=settings.oidc_issuer,
            audience=settings.oidc_audience,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except (jwt.PyJWKClientConnectionError, jwt.PyJWKSetError, jwt.PyJWKError):
        logger.warning("OIDC signing keys are unavailable")
        raise HTTPException(503, "Authentication service is unavailable.") from None
    except (jwt.InvalidTokenError, jwt.PyJWKClientError):
        raise HTTPException(401, "Invalid or expired access token.", headers={"WWW-Authenticate": "Bearer"}) from None
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise HTTPException(401, "Invalid access token.", headers={"WWW-Authenticate": "Bearer"})
    return subject


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
PROXY_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip")


def demo_user(request: Request) -> User | None:
    """The sign-in-free admin for a local demo, only for a direct loopback request that presents no token.

    Anything behind a proxy, from another machine, or carrying a token goes through normal authentication.
    """
    settings = get_settings()
    client = request.client.host if request.client else ""
    if not settings.demo_auth_bypass or client not in LOOPBACK_HOSTS or any(h in request.headers for h in PROXY_HEADERS):
        return None
    return User(subject="demo-admin", display_name="Demo session (sign-in bypassed)", grants=[
        Grant(role=Role.ADMIN, warehouse_id=settings.rbac_warehouse_id),
    ])


def current_user(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> User:
    if credentials is None:
        demo = demo_user(request)
        if demo is not None:
            return demo
        raise HTTPException(401, "Authentication required.", headers={"WWW-Authenticate": "Bearer"})
    subject = verify_token(credentials.credentials)
    try:
        directory = Directory.model_validate(read_json(get_settings().rbac_users_file))
    except (OSError, ValueError):
        logger.error("The RBAC user directory is unavailable or invalid")
        raise HTTPException(503, "Access configuration is unavailable.") from None
    user = next((entry for entry in directory.users if entry.subject == subject), None)
    if user is None or not user.active or (user.expires_at is not None and user.expires_at <= datetime.now(UTC)):
        raise HTTPException(403, "User access is disabled or not provisioned.")
    return user


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


def ticket_reader(user: CurrentUser) -> User:
    if not any(
        grant.role == Role.ADMIN or (
            grant.warehouse_id == get_settings().rbac_warehouse_id
            and Permission.TICKETS in ROLE_PERMISSIONS[grant.role]
        )
        for grant in user.grants
    ):
        raise HTTPException(403, "You do not have permission to access tickets.")
    return user


TicketReader = Annotated[User, Depends(ticket_reader)]
