"""Manage the RBAC access directory (the JSON file named by RBAC_USERS_FILE) without hand-editing it.

Usage (from backend/):
  python -m scripts.access list
  python -m scripts.access add SUBJECT --name "Ada Admin" --role admin
  python -m scripts.access grant SUBJECT --role technician --team Maintenance
  python -m scripts.access revoke SUBJECT --role technician --team Maintenance
  python -m scripts.access disable SUBJECT | enable SUBJECT | remove SUBJECT

SUBJECT is the exact `sub` claim of the user's OIDC token. Every change is validated against the same model the API
uses and written atomically. Removing, disabling or revoking the last active admin is refused unless --force is given.
"""

import argparse
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from app.auth import Directory, Grant, Role, User
from app.config import get_settings
from app.storage import read_json, update_json

EMPTY = {"users": []}


class AccessError(Exception):
    pass


def _active_admins(directory: Directory) -> int:
    now = datetime.now(UTC)
    return sum(
        user.active and (user.expires_at is None or user.expires_at > now) and any(g.role == Role.ADMIN for g in user.grants)
        for user in directory.users
    )


def mutate(path: Path, change: Callable[[Directory], None], *, force: bool = False) -> Directory:
    """Apply one change under the file lock; nothing is written unless the result validates."""
    result: list[Directory] = []

    def update(raw: dict) -> dict:
        before = Directory.model_validate(raw)
        after = before.model_copy(deep=True)
        change(after)
        after = Directory.model_validate(after.model_dump(mode="json"))  # attribute edits skip validators, so validate again
        if not force and _active_admins(before) > 0 and _active_admins(after) == 0:
            raise AccessError("this would leave no active admin (use --force to do it anyway)")
        result.append(after)
        return after.model_dump(mode="json")

    update_json(path, update, default=EMPTY)
    return result[0]


def _find(directory: Directory, subject: str) -> User:
    user = next((u for u in directory.users if u.subject == subject), None)
    if user is None:
        raise AccessError(f"no user with subject {subject!r}")
    return user


def add_user(path: Path, subject: str, name: str, grant: Grant, expires_at: datetime | None = None, *, force: bool = False) -> Directory:
    def change(directory: Directory) -> None:
        if any(u.subject == subject for u in directory.users):
            raise AccessError(f"{subject!r} already exists; use 'grant' to add access")
        directory.users.append(User(subject=subject, display_name=name, grants=[grant], expires_at=expires_at))

    return mutate(path, change, force=force)


def add_grant(path: Path, subject: str, grant: Grant, *, force: bool = False) -> Directory:
    def change(directory: Directory) -> None:
        user = _find(directory, subject)
        if grant in user.grants:
            raise AccessError("that grant already exists")
        user.grants.append(grant)

    return mutate(path, change, force=force)


def revoke_grant(path: Path, subject: str, grant: Grant, *, force: bool = False) -> Directory:
    def change(directory: Directory) -> None:
        user = _find(directory, subject)
        if grant not in user.grants:
            raise AccessError("that grant does not exist")
        user.grants.remove(grant)

    return mutate(path, change, force=force)


def set_active(path: Path, subject: str, active: bool, *, force: bool = False) -> Directory:
    def change(directory: Directory) -> None:
        _find(directory, subject).active = active

    return mutate(path, change, force=force)


def remove_user(path: Path, subject: str, *, force: bool = False) -> Directory:
    def change(directory: Directory) -> None:
        directory.users.remove(_find(directory, subject))

    return mutate(path, change, force=force)


def load(path: Path) -> Directory:
    return Directory.model_validate(read_json(path, default=EMPTY))


def describe(user: User) -> str:
    state = "active" if user.active else "DISABLED"
    if user.expires_at is not None:
        state += f", expires {user.expires_at.isoformat()}"
    grants = "; ".join(
        f"{g.role.value} @ {g.warehouse_id}" + (f"/{g.area_id}" if g.area_id else "") + (f" team={g.team}" if g.team else "")
        for g in user.grants
    ) or "no grants"
    return f"{user.subject}  {user.display_name}  [{state}]  {grants}"


def _grant_from(args: argparse.Namespace) -> Grant:
    return Grant(role=Role(args.role), warehouse_id=args.warehouse or get_settings().rbac_warehouse_id, area_id=args.area, team=args.team)


def build_parser() -> argparse.ArgumentParser:
    # SUPPRESS lets the options be given before or after the command without one overwriting the other.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--file", type=Path, default=argparse.SUPPRESS, help="access directory (default: RBAC_USERS_FILE)")
    common.add_argument("--force", action="store_true", default=argparse.SUPPRESS, help="allow leaving no active admin")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter, parents=[common])
    sub = parser.add_subparsers(dest="command", required=True)

    def scope(p: argparse.ArgumentParser) -> None:
        p.add_argument("--role", required=True, choices=[r.value for r in Role])
        p.add_argument("--warehouse", help="default: RBAC_WAREHOUSE_ID")
        p.add_argument("--area")
        p.add_argument("--team")

    sub.add_parser("list", parents=[common])
    add = sub.add_parser("add", parents=[common])
    add.add_argument("subject")
    add.add_argument("--name", required=True)
    add.add_argument("--expires", help="ISO 8601 with a timezone, e.g. 2027-01-31T00:00:00+00:00")
    scope(add)
    for name in ("grant", "revoke"):
        p = sub.add_parser(name, parents=[common])
        p.add_argument("subject")
        scope(p)
    for name in ("disable", "enable", "remove"):
        sub.add_parser(name, parents=[common]).add_argument("subject")
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    path = getattr(args, "file", None) or get_settings().rbac_users_file
    force = getattr(args, "force", False)
    try:
        if args.command == "list":
            users = load(path).users
            print("\n".join(describe(u) for u in users) if users else f"No users in {path}")
            return 0
        if args.command == "add":
            expires = datetime.fromisoformat(args.expires) if args.expires else None
            directory = add_user(path, args.subject, args.name, _grant_from(args), expires, force=force)
        elif args.command == "grant":
            directory = add_grant(path, args.subject, _grant_from(args), force=force)
        elif args.command == "revoke":
            directory = revoke_grant(path, args.subject, _grant_from(args), force=force)
        elif args.command in ("disable", "enable"):
            directory = set_active(path, args.subject, args.command == "enable", force=force)
        else:
            directory = remove_user(path, args.subject, force=force)
    except ValidationError as exc:  # a ValueError subclass, so it has to come first
        print(f"error: {_first_validation_message(exc)}", file=sys.stderr)
        return 1
    except (AccessError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"ok: {len(directory.users)} user(s) in {path}")
    return 0


def _first_validation_message(exc: ValidationError) -> str:
    error = exc.errors()[0]
    where = ".".join(str(part) for part in error["loc"] if part != "users")
    return f"{where}: {error['msg']}".strip(": ")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
