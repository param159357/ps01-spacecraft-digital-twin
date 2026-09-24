"""Test fixtures.

Tests run against a real PostgreSQL database (`<database>_test`), created on demand,
because the schema uses PostgreSQL-specific JSONB and UUID types.

`DATABASE_URL` is repointed at that test database *before* any module that builds an
engine is imported. That matters: the simulation loop writes through `session_scope()`
rather than a request dependency, so an override alone would leave it writing to the
application database. The application database is never touched by tests.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

# The suite drives the API from TEST_ORIGIN below, so it must own these two settings
# rather than inherit them. A deployment `.env` — a public HTTPS origin with
# COOKIE_SECURE=true — otherwise makes the API reject every test login as a cross-site
# request, and about thirty tests fail on a completely healthy codebase. Setting them
# here keeps `pytest` reproducible no matter what the local `.env` is pointed at.
os.environ["APP_ORIGIN"] = "http://localhost:3000"
os.environ["COOKIE_SECURE"] = "false"

from app.config.settings import get_settings  # noqa: E402

# Note: URL objects are passed around rather than strings. `str(URL)` masks the password
# as `***`, which silently produces an authentication failure.
_APP_URL = make_url(get_settings().database_url)
TEST_URL = _APP_URL.set(database=f"{_APP_URL.database}_test")
ADMIN_URL = _APP_URL.set(database="postgres")

os.environ["DATABASE_URL"] = TEST_URL.render_as_string(hide_password=False)
get_settings.cache_clear()

from fastapi.testclient import TestClient  # noqa: E402

from app.auth import hash_password, login_throttle  # noqa: E402
from app.contracts.common import Role  # noqa: E402
from app.main import app  # noqa: E402
from app.storage.db import Base  # noqa: E402
from app.storage.db import engine as app_engine  # noqa: E402
from app.storage.models import User  # noqa: E402

TEST_ORIGIN = "http://localhost:3000"

# Fixed passwords for tests only. Real accounts get generated ones from seed_users.py.
TEST_ACCOUNTS: dict[str, tuple[Role, str]] = {
    "simulator": (Role.SIMULATOR, "sim-test-pass"),
    "master": (Role.MASTER, "master-test-pass"),
    "power": (Role.POWER, "power-test-pass"),
    "thermal": (Role.THERMAL_PROPULSION, "thermal-test-pass"),
    "flight": (Role.FLIGHT_SYSTEMS, "flight-test-pass"),
}


@pytest.fixture(scope="session")
def engine():
    admin = create_engine(
        ADMIN_URL, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 5}
    )
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": TEST_URL.database},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{TEST_URL.database}"'))
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PostgreSQL is not reachable for tests: {exc}")
    finally:
        admin.dispose()

    # The application engine is already pointed at the test database by the env
    # repointing above, so the simulation loop and the API share it.
    Base.metadata.drop_all(app_engine)
    Base.metadata.create_all(app_engine)
    yield app_engine


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


@pytest.fixture
def db_session(session_factory) -> Session:
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def seeded_users(session_factory) -> dict[str, tuple[Role, str]]:
    session = session_factory()
    try:
        session.query(User).delete()
        for username, (role, password) in TEST_ACCOUNTS.items():
            session.add(
                User(
                    username=username,
                    password_hash=hash_password(password),
                    role=role.value,
                    active=True,
                )
            )
        session.commit()
    finally:
        session.close()
    return TEST_ACCOUNTS


@pytest.fixture
def clean_runs(engine, client):
    """Start each simulation test from an empty run history and a pristine runner.

    Depends on `client` so the app lifespan (which adopts the last run) has already
    finished: the truncation and the in-memory reset must both happen afterwards, or the
    runner would hold a run id whose row no longer exists.
    """
    from app.config.loader import load_configuration
    from app.contracts.common import RunStatus
    from app.simulation.truth import SimulatorTruth

    with engine.begin() as conn:
        conn.execute(text("TRUNCATE telemetry_frames, runs RESTART IDENTITY CASCADE"))

    runner = client.app.state.runner
    runner.run_id = None
    runner.status = RunStatus.STOPPED
    runner.frame_seq = 0
    runner.control_revision = 0
    runner.latest_frame = None
    runner.storage_error = None
    runner._last_publish_monotonic = None  # noqa: SLF001
    runner.truth = SimulatorTruth.initial(load_configuration().spacecraft)
    yield


@pytest.fixture
def client(engine, seeded_users):
    """TestClient with the app lifespan active, sending an allowed Origin by default.

    The lifespan starts the simulation runner, so tests exercise the real loop.
    """
    login_throttle._state.clear()  # noqa: SLF001 - isolate throttle state between tests

    with TestClient(app, headers={"Origin": TEST_ORIGIN}) as test_client:
        yield test_client


def login(client: TestClient, username: str) -> dict:
    """Sign in and return the parsed response. Cookies persist on the client."""
    _, password = TEST_ACCOUNTS[username]
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


def logout(client: TestClient) -> None:
    client.post("/api/auth/logout", headers=csrf_headers(client))


def csrf_headers(client: TestClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies.get("ps01_csrf", "")}
