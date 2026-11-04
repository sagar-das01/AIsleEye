import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app import audit, personas, sessions
from app.auth import (
    SESSION_COOKIE, CurrentUser, Permission, User, check_origin, has_permission, require_permission, resolve_session,
)
from app.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()

DEMO_NOTICE = "Demo sign-in: identities are not verified. Use only with synthetic, disposable data."
PERSONA_ID = Path(pattern=r"^[a-z0-9-]{1,40}$")


class SignInRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")  # roles and scopes come from the seed, never from the browser

    persona_id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9-]+$")


class ActiveRequest(BaseModel):
    active: bool


def permissions_of(user: User) -> list[str]:
    return [p.value for p in Permission if has_permission(user, p)]


def _identity(user: User) -> dict:
    return {
        "user_id": user.subject,
        "display_name": user.display_name,
        "roles": sorted({g.role.value for g in user.grants}),
        "grants": [g.model_dump(mode="json") for g in user.grants],
        "permissions": permissions_of(user),
    }


def _audit(action: str, actor: str, **details: object) -> None:
    audit.record("access", None, action, {"actor_id": actor, **details}, "executed")


def _user_store_error() -> HTTPException:
    logger.error("The demo user store is unavailable or invalid")
    return HTTPException(503, "Access configuration is unavailable.")


@router.get("/auth/demo-personas")
def demo_personas() -> dict:
    if not get_settings().demo_sign_in_enabled:
        return {"enabled": False, "notice": "Demo sign-in is disabled on this server.", "personas": []}
    try:
        users = {u.subject: u for u in personas.all_users()}
    except personas.UserStoreError:
        raise _user_store_error() from None
    return {
        "enabled": True,
        "notice": DEMO_NOTICE,
        "personas": [
            {
                "id": p.id, "label": p.label, "display_name": p.display_name, "description": p.description,
                "core": p.core, "active": users[p.id].active,
            }
            for p in personas.PERSONAS
        ],
    }


@router.post("/auth/demo-sign-in")
def demo_sign_in(body: SignInRequest, request: Request, response: Response) -> dict:
    settings = get_settings()
    if not settings.demo_sign_in_enabled:
        raise HTTPException(403, "Demo sign-in is disabled on this server.")
    check_origin(request)
    try:
        user = personas.find_user(body.persona_id)
    except personas.UserStoreError:
        raise _user_store_error() from None
    if user is None:
        raise HTTPException(404, "Unknown demo persona.")
    if not user.active:
        raise HTTPException(403, "This demo account is disabled.")
    try:
        # Switching roles: the previous session dies first so two identities never share one browser cookie.
        if previous := request.cookies.get(SESSION_COOKIE):
            sessions.revoke(previous)
        token, session = sessions.create(user.subject)
    except sessions.SessionStoreError:
        logger.error("The session store is unavailable or invalid")
        raise HTTPException(503, "Session storage is unavailable.") from None
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=settings.session_ttl_hours * 3600, httponly=True, samesite="lax",
        secure=settings.session_cookie_secure, path="/",
    )
    _audit("auth:sign_in", user.subject)
    return {**_identity(user), "csrf_token": session.csrf_token, "expires_at": session.expires_at.isoformat(), "notice": DEMO_NOTICE}


@router.post("/auth/logout", status_code=204)
def logout(request: Request) -> Response:
    response = Response(status_code=204)
    try:
        user, _ = resolve_session(request)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        # Already signed out or expired: just clear the browser's cookie.
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response
    try:
        sessions.revoke(request.cookies[SESSION_COOKIE])
    except sessions.SessionStoreError:
        raise HTTPException(503, "Session storage is unavailable.") from None
    response.delete_cookie(SESSION_COOKIE, path="/")
    _audit("auth:logout", user.subject)
    return response


@router.get("/auth/session")
def current_session(request: Request) -> dict:
    user, session = resolve_session(request)
    return {**_identity(user), "csrf_token": session.csrf_token, "expires_at": session.expires_at.isoformat(), "notice": DEMO_NOTICE}


@router.get("/me")
def me(user: CurrentUser) -> dict:
    return {**user.model_dump(mode="json"), "permissions": permissions_of(user)}


@router.get("/admin/accounts", dependencies=[Depends(require_permission(Permission.USERS))])
def list_users() -> list[dict]:
    try:
        users = personas.all_users()
    except personas.UserStoreError:
        raise _user_store_error() from None
    return [{**_identity(u), "active": u.active} for u in users]


@router.post("/admin/accounts/{user_id}/active")
def set_user_active(
    user_id: Annotated[str, PERSONA_ID], body: ActiveRequest,
    admin: Annotated[User, Depends(require_permission(Permission.USERS))],
) -> dict:
    if user_id == admin.subject and not body.active:
        raise HTTPException(422, "You cannot disable your own account.")
    try:
        user = personas.set_active(user_id, body.active)
        if not body.active:
            sessions.revoke_user(user_id)
    except KeyError:
        raise HTTPException(404, "User not found.") from None
    except personas.UserStoreError:
        raise _user_store_error() from None
    except sessions.SessionStoreError:
        raise HTTPException(503, "Session storage is unavailable.") from None
    _audit("admin:set_active", admin.subject, user_id=user_id, active=body.active)
    return {**_identity(user), "active": user.active}
