"""Phase 9 fresh-clone provisioning entry-point checks."""

from __future__ import annotations

import json
from datetime import date

from sqlalchemy import select, text

from app.cli import generate_history, seed_users
from app.contracts.common import Role
from app.storage.models import User


def test_container_seed_users_provisions_five_hashed_accounts(
    engine, session_factory, monkeypatch, capsys
):
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE users RESTART IDENTITY CASCADE"))

    issued = iter(f"TestOnlySecret{index:02d}" for index in range(5))
    monkeypatch.setattr(seed_users, "generate_password", lambda: next(issued))
    assert seed_users.main(["--reset-passwords"]) == 0
    output = capsys.readouterr().out

    with session_factory() as db:
        users = db.execute(select(User).order_by(User.username)).scalars().all()
        assert len(users) == 5
        assert {user.role for user in users} == {role.value for role in Role}
        assert all(user.active for user in users)
        assert all(user.password_hash.startswith("$argon2") for user in users)
        stored = json.dumps(
            [{"username": user.username, "password_hash": user.password_hash} for user in users]
        )
    for index in range(5):
        plaintext = f"TestOnlySecret{index:02d}"
        assert plaintext in output
        assert plaintext not in stored


def test_container_history_cli_generates_and_reuses_completed_month(
    engine, monkeypatch, capsys
):
    # Use a stable completed February to keep this true fresh-database check bounded.
    current = generate_history._last_completed_month()
    month = date(current.year - 2, 2, 1)
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE telemetry_frames, runs RESTART IDENTITY CASCADE"))

    monkeypatch.setattr(generate_history, "_last_completed_month", lambda: month)
    assert generate_history.main(["--completed-month"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["cached"] is False
    assert first["minutes"] in {40_320, 41_760}
    assert first["evidence_frames"] == 900

    assert generate_history.main(["--completed-month"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["cached"] is True
    assert second["run_id"] == first["run_id"]
