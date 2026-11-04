"""Enable or disable seeded demo accounts from the command line (the same flags the admin Accounts screen sets).

Usage (from backend/):
  python -m scripts.access list
  python -m scripts.access disable tech-b
  python -m scripts.access enable tech-b

Roles and scopes are fixed by the persona seed in app/personas.py and cannot be edited here. Disabling an account
revokes its sessions at once. Disabling the last active admin is refused unless --force is given, so this also
works as the recovery path if the admin account was switched off.
"""

import argparse
import os
import sys
from pathlib import Path

from app import personas, sessions
from app.config import get_settings
from app.identity import Role


class AccessError(Exception):
    pass


def _admins(active_only: bool = True) -> list[str]:
    return [u.subject for u in personas.all_users() if (u.active or not active_only) and any(g.role == Role.ADMIN for g in u.grants)]


def describe(user) -> str:
    roles = ", ".join(sorted({g.role.value + (f" ({g.team})" if g.team else "") for g in user.grants}))
    return f"{user.subject:<22} {'active' if user.active else 'DISABLED':<8} {user.display_name} — {roles}"


def set_active(user_id: str, active: bool, *, force: bool = False) -> None:
    if user_id not in personas.BY_ID:
        raise AccessError(f"no demo account {user_id!r}; run 'list' to see them")
    if not active and not force and _admins() == [user_id]:
        raise AccessError("this would leave no active admin (use --force to do it anyway)")
    personas.set_active(user_id, active)
    if not active:
        sessions.revoke_user(user_id)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.access", description=__doc__.split("\n\n")[0])
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--file", type=Path, help="account flags file (default: RBAC_USERS_FILE)")
    common.add_argument("--force", action="store_true", help="allow disabling the last active admin")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", parents=[common])
    for name in ("disable", "enable"):
        sub.add_parser(name, parents=[common]).add_argument("user_id")
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.file:
        os.environ["RBAC_USERS_FILE"] = str(args.file)
        get_settings.cache_clear()
    try:
        if args.command == "list":
            print("\n".join(describe(u) for u in personas.all_users()))
            return 0
        set_active(args.user_id, args.command == "enable", force=args.force)
    except (AccessError, personas.UserStoreError, sessions.SessionStoreError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"ok: {args.user_id} {'enabled' if args.command == 'enable' else 'disabled'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
