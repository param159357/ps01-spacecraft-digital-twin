"""Provision the five console accounts.

There is no public signup and no role selector: accounts exist only because this script
creates them. Passwords are generated here and printed once to the operator running the
script. They are never written to source, to a committed file or to the application log.

Usage (from the backend directory, with the virtualenv active):

    python ../scripts/seed_users.py                 # create missing accounts
    python ../scripts/seed_users.py --reset-passwords  # new passwords for all accounts
    python ../scripts/seed_users.py --username master --reset-passwords

Each run prints a table. Copy it somewhere safe; it cannot be recovered afterwards.
"""

from __future__ import annotations

import argparse
import secrets
import string
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

from app.auth import hash_password, revoke_all_for_user  # noqa: E402
from app.contracts.common import ROLE_HOME, Role  # noqa: E402
from app.storage.db import session_scope  # noqa: E402
from app.storage.models import User  # noqa: E402

# One account per console. Usernames are fixed so docs/demo.md can reference them.
ACCOUNTS: list[tuple[str, Role]] = [
    ("simulator", Role.SIMULATOR),
    ("master", Role.MASTER),
    ("power", Role.POWER),
    ("thermal", Role.THERMAL_PROPULSION),
    ("flight", Role.FLIGHT_SYSTEMS),
]

# Unambiguous alphabet: no 0/O or 1/l/I, so a password read off a screen at a demo
# desk cannot be mistyped.
_ALPHABET = "".join(
    c for c in (string.ascii_letters + string.digits) if c not in "0O1lI"
)


def generate_password(length: int = 16) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision PS01 console accounts.")
    parser.add_argument(
        "--reset-passwords",
        action="store_true",
        help="Issue a new password for existing accounts and revoke their live sessions.",
    )
    parser.add_argument(
        "--username",
        help="Limit the operation to one username.",
    )
    args = parser.parse_args()

    selected = [a for a in ACCOUNTS if args.username in (None, a[0])]
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

            # Keep the role authoritative even if it was changed by hand.
            if user.role != role.value:
                user.role = role.value

            if args.reset_passwords:
                password = generate_password()
                user.password_hash = hash_password(password)
                user.active = True
                # A password change must invalidate anything still signed in.
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

    if any(p != "(unchanged)" for _, _, p, _ in issued):
        print("Passwords are shown once and are not recoverable. Store them now.")
        print("Re-run with --reset-passwords to issue new ones.")
    else:
        print("No password was changed. Use --reset-passwords to issue new ones.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
