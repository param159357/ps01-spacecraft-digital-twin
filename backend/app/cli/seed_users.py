"""Provision the five fixed console accounts using the configured database."""

from __future__ import annotations

import argparse
import secrets
import string
import sys
from collections.abc import Sequence

from sqlalchemy import select

from app.auth import hash_password, revoke_all_for_user
from app.contracts.common import ROLE_HOME, Role
from app.storage.db import session_scope
from app.storage.models import User

ACCOUNTS: list[tuple[str, Role]] = [
    ("simulator", Role.SIMULATOR),
    ("master", Role.MASTER),
    ("power", Role.POWER),
    ("thermal", Role.THERMAL_PROPULSION),
    ("flight", Role.FLIGHT_SYSTEMS),
]

_ALPHABET = "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")


def generate_password(length: int = 16) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Provision PS01 console accounts.")
    parser.add_argument(
        "--reset-passwords",
        action="store_true",
        help="Issue new passwords and revoke live sessions for existing accounts.",
    )
    parser.add_argument("--username", help="Limit the operation to one username.")
    args = parser.parse_args(argv)

    selected = [account for account in ACCOUNTS if args.username in (None, account[0])]
    if not selected:
        print(f"No such account: {args.username}", file=sys.stderr)
        return 2

    issued: list[tuple[str, str, str, str]] = []
    with session_scope() as db:
        for username, role in selected:
            user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
            if user is None:
                password = generate_password()
                db.add(
                    User(
                        username=username,
                        password_hash=hash_password(password),
                        role=role.value,
                        active=True,
                    )
                )
                issued.append((username, role.value, password, "created"))
                continue

            user.role = role.value
            if args.reset_passwords:
                password = generate_password()
                user.password_hash = hash_password(password)
                user.active = True
                revoke_all_for_user(db, user.id)
                issued.append((username, role.value, password, "password reset"))
            else:
                issued.append((username, role.value, "(unchanged)", "already present"))

    print()
    print("PS01 console accounts")
    print("=" * 78)
    print(f"{'USERNAME':<12} {'ROLE':<20} {'CONSOLE':<14} {'PASSWORD':<18} STATUS")
    print("-" * 78)
    for username, role_value, password, status in issued:
        console = ROLE_HOME[Role(role_value)]
        print(f"{username:<12} {role_value:<20} {console:<14} {password:<18} {status}")
    print("-" * 78)
    if any(password != "(unchanged)" for _, _, password, _ in issued):
        print("Passwords are shown once and are not recoverable. Store them now.")
        print("Re-run with --reset-passwords to issue new ones.")
    else:
        print("No password was changed. Use --reset-passwords to issue new ones.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
