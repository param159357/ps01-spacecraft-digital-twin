"""Authentication, role routing and authorization boundary checks.

These are the Phase 1 acceptance checks: five accounts reach their own console, a
specialist attempting a recovery command gets 403, and a missing session gets 401.
"""

from __future__ import annotations

from app.auth import login_throttle
from tests.conftest import TEST_ACCOUNTS, TEST_ORIGIN, csrf_headers, login

# ----------------------------------------------------------------- session boundary


def test_health_needs_no_session(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_missing_session_returns_401(client):
    for path in ("/api/auth/me", "/api/config/channels", "/api/runs/current", "/api/incidents"):
        response = client.get(path)
        assert response.status_code == 401, path
        assert response.json()["error"] == "NOT_AUTHENTICATED"


def test_invalid_credentials_return_401_without_revealing_the_account(client):
    unknown = client.post(
        "/api/auth/login", json={"username": "nobody", "password": "whatever"}
    )
    wrong = client.post("/api/auth/login", json={"username": "master", "password": "wrong"})

    assert unknown.status_code == 401
    assert wrong.status_code == 401
    # Identical body: an attacker cannot tell which usernames exist.
    assert unknown.json() == wrong.json()


def test_login_rejects_a_disallowed_origin(client):
    response = client.post(
        "/api/auth/login",
        json={"username": "master", "password": TEST_ACCOUNTS["master"][1]},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "ORIGIN_NOT_ALLOWED"


# ------------------------------------------------------------------- role routing


def test_each_account_lands_on_its_own_console(client):
    """Five accounts, five routes. The server supplies the destination."""
    expected = {
        "simulator": "/simulator/",
        "master": "/master/",
        "power": "/power/",
        "thermal": "/thermal/",
        "flight": "/flight/",
    }
    for username, home in expected.items():
        body = login(client, username)
        assert body["user"]["home_route"] == home, username
        assert body["user"]["role"] == TEST_ACCOUNTS[username][0].value

        me = client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["home_route"] == home

        client.post("/api/auth/logout", headers=csrf_headers(client))


def test_session_cookie_is_httponly_and_csrf_cookie_is_not(client):
    response = client.post(
        "/api/auth/login",
        json={"username": "master", "password": TEST_ACCOUNTS["master"][1]},
    )
    cookies = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith("ps01_session="))
    csrf_cookie = next(c for c in cookies if c.startswith("ps01_csrf="))

    assert "HttpOnly" in session_cookie
    assert "samesite=lax" in session_cookie.lower()
    # The CSRF value must be readable by the page so it can be echoed in a header.
    assert "HttpOnly" not in csrf_cookie


def test_logout_revokes_the_session(client):
    login(client, "master")
    assert client.get("/api/auth/me").status_code == 200

    response = client.post("/api/auth/logout", headers=csrf_headers(client))
    assert response.status_code == 204
    assert client.get("/api/auth/me").status_code == 401


# --------------------------------------------------------------------- authorization


def test_specialist_cannot_execute_a_recovery_command(client):
    """Phase 1 acceptance: a specialist command attempt returns 403."""
    for username in ("power", "thermal", "flight", "simulator"):
        login(client, username)
        response = client.post("/api/commands", json={}, headers=csrf_headers(client))
        assert response.status_code == 403, username
        assert response.json()["error"] == "ROLE_NOT_PERMITTED"
        client.post("/api/auth/logout", headers=csrf_headers(client))


def test_master_reaches_the_command_endpoint_and_is_told_it_is_unbuilt(client):
    """The master passes authorization, then gets an honest 503 rather than a fake
    success — the endpoint exists but Phase 3 implements it."""
    login(client, "master")
    response = client.post("/api/commands", json={}, headers=csrf_headers(client))
    assert response.status_code == 503
    body = response.json()
    assert body["error"] == "NOT_IMPLEMENTED_YET"
    assert body["phase"] == 3


def test_operational_roles_cannot_read_simulator_truth(client):
    """Operational accounts never retrieve hidden simulator fields."""
    for username in ("master", "power", "thermal", "flight"):
        login(client, username)
        response = client.get("/api/simulator/state")
        assert response.status_code == 403, username
        assert response.json()["error"] == "ROLE_NOT_PERMITTED"
        client.post("/api/auth/logout", headers=csrf_headers(client))


def test_only_the_simulator_controls_the_run(client):
    """Role authorization is decided before the request body is even validated."""
    login(client, "master")
    denied = client.post(
        "/api/simulator/run", json={"operation": "PAUSE"}, headers=csrf_headers(client)
    )
    assert denied.status_code == 403
    assert denied.json()["error"] == "ROLE_NOT_PERMITTED"
    client.post("/api/auth/logout", headers=csrf_headers(client))

    login(client, "simulator")
    # The simulator passes authorization and reaches real validation.
    invalid = client.post("/api/simulator/run", json={}, headers=csrf_headers(client))
    assert invalid.status_code == 422

    accepted = client.post(
        "/api/simulator/run", json={"operation": "START"}, headers=csrf_headers(client)
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "RUNNING"
    client.post("/api/simulator/run", json={"operation": "PAUSE"}, headers=csrf_headers(client))


# ----------------------------------------------------------------------------- CSRF


def test_mutation_without_a_csrf_token_is_rejected(client):
    login(client, "master")
    response = client.post("/api/commands", json={})
    assert response.status_code == 403
    assert response.json()["error"] == "CSRF_INVALID"


def test_mutation_from_a_foreign_origin_is_rejected(client):
    login(client, "master")
    headers = csrf_headers(client) | {"Origin": "https://evil.example"}
    response = client.post("/api/commands", json={}, headers=headers)
    assert response.status_code == 403
    assert response.json()["error"] == "ORIGIN_NOT_ALLOWED"


# ------------------------------------------------------------ role-filtered contract


def test_channel_visibility_is_filtered_by_role(client):
    login(client, "power")
    power_channels = {c["id"] for c in client.get("/api/config/channels").json()["channels"]}
    client.post("/api/auth/logout", headers=csrf_headers(client))

    login(client, "master")
    master_channels = {c["id"] for c in client.get("/api/config/channels").json()["channels"]}

    # The master sees everything; a specialist sees its own scope plus declared
    # cross-system dependencies, and strictly less than the master.
    assert len(master_channels) == 28
    assert power_channels < master_channels
    assert "battery_soc" in power_channels
    assert "wheel_speed" not in power_channels


def test_coverage_reports_nothing_as_implemented_yet(client):
    login(client, "master")
    body = client.get("/api/config/coverage").json()
    assert all(rule["implemented"] is False for rule in body["rules"])
    assert all(action["enabled"] is False for action in body["actions"])
    assert body["executable_by_this_role"] is True


# ------------------------------------------------------------------------ throttling


def test_repeated_failures_are_throttled(client):
    login_throttle._state.clear()  # noqa: SLF001
    last = None
    for _ in range(login_throttle.max_attempts + 1):
        last = client.post(
            "/api/auth/login",
            json={"username": "master", "password": "wrong"},
            headers={"Origin": TEST_ORIGIN},
        )
    assert last is not None
    assert last.status_code == 429
    assert last.json()["error"] == "TOO_MANY_ATTEMPTS"
    login_throttle._state.clear()  # noqa: SLF001
