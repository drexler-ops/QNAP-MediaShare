"""Admin commands, run inside the api container, e.g.:

    docker compose exec api python -m app.cli users
    docker compose exec api python -m app.cli reset-password <name>     # they pick a new one at next sign-in
    docker compose exec api python -m app.cli admin-password admin      # locked-out admin: print a new password
    docker compose exec api python -m app.cli scan
"""
from __future__ import annotations

import argparse
import secrets

from sqlalchemy import func, select

from .auth import hash_password
from .db import session_scope
from .jobs import enqueue
from .models import User


def _user(s, name: str) -> User:
    user = s.scalar(select(User).where(func.lower(User.name) == name.lower()))
    if user is None:
        raise SystemExit(f"no user named {name!r}")
    return user


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("users", help="list users")
    rp = sub.add_parser("reset-password", help="make a user choose a new password at next sign-in")
    rp.add_argument("name")
    ap = sub.add_parser("admin-password", help="set a password directly and make the user an active admin")
    ap.add_argument("name")
    ap.add_argument("password", nargs="?")
    sub.add_parser("scan", help="queue a library scan now")
    sub.add_parser("regroup", help="queue duplicate regrouping now")
    args = parser.parse_args()

    with session_scope() as s:
        if args.cmd == "users":
            for u in s.scalars(select(User).order_by(User.name)):
                flags = [f for f, on in (("admin", u.is_admin), ("inactive", not u.is_active),
                                         ("must-set-password", u.must_set_password)) if on]
                print(f"{u.id:>3}  {u.name:<16} folder={u.home_folder or '-':<12} {' '.join(flags)}")
        elif args.cmd == "reset-password":
            user = _user(s, args.name)
            user.must_set_password, user.password_hash = True, None
            print(f"{user.name} will be asked to choose a new password at next sign-in.")
        elif args.cmd == "admin-password":
            user = _user(s, args.name)
            password = args.password or secrets.token_urlsafe(12)
            user.password_hash = hash_password(password)
            user.must_set_password, user.is_admin, user.is_active = False, True, True
            print(f"{user.name} is an active admin. Password: {password}")
        elif args.cmd == "scan":
            enqueue(s, "scan", {"trigger": "cli"})
            print("scan queued")
        elif args.cmd == "regroup":
            enqueue(s, "group", unique=True)
            print("regroup queued")


if __name__ == "__main__":
    main()
