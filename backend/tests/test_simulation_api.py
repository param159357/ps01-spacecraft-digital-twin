"""Phase 2 acceptance checks — the shared live run.

Covers: one backend-owned run that keeps ticking with no browser attached, two clients
observing the same sequence, reset starting a new run, and the hard boundary that keeps
simulator truth out of every operational response.
"""

from __future__ import annotations

import time

import pytest

from app.contracts.common import Role
from tests.conftest import csrf_headers, login, logout

# Hidden simulator fields. None of these may ever appear in an operational payload.
HIDDEN_FIELDS = ("generation_factor", "extra_load_w", "heater_stuck_on", "contact_loss")


def _start_run(client) -> dict:
    login(client, "simulator")
    response = client.post(
        "/api/simulator/run", json={"operation": "START"}, headers=csrf_headers(client)
    )
    assert response.status_code == 200, response.text
    return response.json()


def _wait_for_frames(client, minimum: int = 2, timeout_s: float = 20.0) -> dict:
    """Poll the current run until the loop has published at least `minimum` frames."""
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        last = client.get("/api/runs/current").json()
        if (last["run"]["frame_seq"] or 0) >= minimum:
            return last
        time.sleep(0.4)
    pytest.fail(f"no telemetry after {timeout_s}s; last response was {last}")


# ---------------------------------------------------------------------- run control


def test_no_run_exists_before_the_simulator_starts_one(client, clean_runs):
    login(client, "master")
    body = client.get("/api/runs/current").json()
    assert body["run"]["run_id"] is None
    assert body["frame"] is None


def test_simulator_starts_a_run_and_telemetry_flows(client, clean_runs):
    metadata = _start_run(client)
    assert metadata["status"] == "RUNNING"
    assert metadata["run_id"] is not None

    body = _wait_for_frames(client)
    frame = body["frame"]
    assert frame is not None
    assert frame["frame_seq"] >= 2
    assert frame["sim_time_s"] > 0

    # Coherent power telemetry, in the right units.
    assert 0.0 <= frame["values"]["battery_soc"] <= 100.0
    assert frame["values"]["load_power"] > 0
    assert frame["quality"]["battery_soc"]["state"] == "VALID"
    assert frame["context"]["illumination"] in ("SUNLIGHT", "ECLIPSE")


def test_loop_keeps_running_with_no_client_attached(client, clean_runs):
    """Closing the simulator browser must not stop the backend simulation."""
    _start_run(client)
    first = _wait_for_frames(client)["run"]["frame_seq"]

    # Sign the simulator out entirely — the equivalent of closing that laptop.
    logout(client)
    time.sleep(3.0)

    login(client, "master")
    later = client.get("/api/runs/current").json()["run"]["frame_seq"]
    assert later > first


def test_pause_stops_advancing_and_start_resumes(client, clean_runs):
    _start_run(client)
    _wait_for_frames(client)

    assert client.post(
        "/api/simulator/run", json={"operation": "PAUSE"}, headers=csrf_headers(client)
    ).status_code == 200

    time.sleep(1.5)
    paused_at = client.get("/api/runs/current").json()["run"]["frame_seq"]
    time.sleep(2.0)
    still = client.get("/api/runs/current").json()

    assert still["run"]["frame_seq"] == paused_at
    assert still["run"]["status"] == "PAUSED"

    client.post("/api/simulator/run", json={"operation": "START"}, headers=csrf_headers(client))
    resumed = _wait_for_frames(client, minimum=paused_at + 1)
    assert resumed["run"]["frame_seq"] > paused_at


def test_reset_starts_a_new_run_and_keeps_the_previous_one(client, clean_runs):
    first = _start_run(client)
    _wait_for_frames(client)

    reset = client.post(
        "/api/simulator/run", json={"operation": "RESET"}, headers=csrf_headers(client)
    )
    assert reset.status_code == 200
    new_run = reset.json()

    assert new_run["run_id"] != first["run_id"]
    assert new_run["frame_seq"] == 0
    # A new run comes back paused; it is never silently left running.
    assert new_run["status"] == "PAUSED"

    # Previous run history is retained, not deleted.
    runs = client.get("/api/runs").json()["runs"]
    ids = {r["run_id"] for r in runs}
    assert first["run_id"] in ids and new_run["run_id"] in ids


# ------------------------------------------------------------- truth isolation


def test_operational_responses_never_carry_hidden_truth(client, clean_runs):
    _start_run(client)
    client.post(
        "/api/simulator/conditions",
        json={"generation_factor": 0.4, "extra_load_w": 120.0, "heater_stuck_on": True},
        headers=csrf_headers(client),
    )
    _wait_for_frames(client)
    logout(client)

    for username in ("master", "power", "thermal", "flight"):
        login(client, username)
        for path in ("/api/runs/current", "/api/telemetry?limit=5"):
            raw = client.get(path).text
            for field in HIDDEN_FIELDS:
                assert field not in raw, f"{field} leaked to {username} via {path}"
        logout(client)


def test_simulator_state_exposes_truth_only_to_the_simulator(client, clean_runs):
    _start_run(client)
    client.post(
        "/api/simulator/conditions",
        json={"extra_load_w": 90.0},
        headers=csrf_headers(client),
    )
    state = client.get("/api/simulator/state").json()

    assert state["truth"]["hidden_conditions"]["extra_load_w"] == 90.0
    assert "energy_wh" in state["truth"]
    logout(client)

    login(client, "master")
    assert client.get("/api/simulator/state").status_code == 403


def test_known_commands_are_visible_but_hidden_conditions_are_not(client, clean_runs):
    """Operators legitimately know what was commanded; that is what makes a later
    actuator mismatch detectable. They still never see the injected fault."""
    _start_run(client)
    client.post(
        "/api/simulator/conditions",
        json={"payload_duty_percent": 50.0, "heater_stuck_on": True},
        headers=csrf_headers(client),
    )
    _wait_for_frames(client)
    logout(client)

    login(client, "master")
    context = client.get("/api/runs/current").json()["frame"]["context"]
    assert context["known_commands"]["payload_duty_percent"] == 50.0
    assert "heater_commanded_on" in context["known_commands"]
    assert "heater_stuck_on" not in context["known_commands"]


def test_condition_bounds_are_enforced(client, clean_runs):
    _start_run(client)
    for payload in (
        {"generation_factor": 1.5},
        {"extra_load_w": -10.0},
        {"payload_duty_percent": 500.0},
        {"unknown_field": 1},
    ):
        response = client.post(
            "/api/simulator/conditions", json=payload, headers=csrf_headers(client)
        )
        assert response.status_code == 422, payload


# ------------------------------------------------------------------ role filtering


def test_frames_are_filtered_by_role(client, clean_runs):
    _start_run(client)
    _wait_for_frames(client)
    logout(client)

    login(client, "master")
    master_channels = set(client.get("/api/runs/current").json()["frame"]["values"])
    logout(client)

    login(client, "flight")
    flight_body = client.get("/api/runs/current").json()
    flight_channels = set(flight_body["frame"]["values"])

    assert len(master_channels) == 28
    assert flight_channels < master_channels
    assert "wheel_speed" in flight_channels
    assert "tank_pressure" not in flight_channels


def test_history_rejects_a_channel_outside_the_role_scope(client, clean_runs):
    _start_run(client)
    _wait_for_frames(client)
    logout(client)

    login(client, "flight")
    denied = client.get("/api/telemetry?channels=tank_pressure")
    assert denied.status_code == 403
    assert denied.json()["error"] == "CHANNEL_NOT_PERMITTED"

    allowed = client.get("/api/telemetry?channels=wheel_speed,bus_voltage")
    assert allowed.status_code == 200
    assert set(allowed.json()["frames"][0]["values"]) == {"wheel_speed", "bus_voltage"}


def test_unmodelled_channels_are_missing_not_zero(client, clean_runs):
    """A channel with no model behind it reports null with a reason, never a
    plausible-looking value and never zero."""
    _start_run(client)
    _wait_for_frames(client)
    logout(client)

    login(client, "flight")
    frame = client.get("/api/runs/current").json()["frame"]

    assert frame["values"]["wheel_speed"] is None
    assert frame["quality"]["wheel_speed"]["state"] == "MISSING"
    assert frame["quality"]["wheel_speed"]["reason"]


# ------------------------------------------------------------------- history query


def test_history_is_bounded_and_ordered(client, clean_runs):
    _start_run(client)
    _wait_for_frames(client, minimum=3)
    logout(client)

    login(client, "master")
    body = client.get("/api/telemetry?limit=2").json()
    assert len(body["frames"]) == 2
    assert body["truncated"] is True
    assert body["frames"][0]["frame_seq"] < body["frames"][1]["frame_seq"]

    assert client.get("/api/telemetry?limit=99999").status_code == 422


# -------------------------------------------------------------------- live stream
#
# The SSE stream is verified against a real Uvicorn server rather than through
# Starlette's TestClient: that transport does not stream a never-ending response
# faithfully, and the reverse-proxy path is what actually matters. See
# docs/validation.md for the recorded curl and two-browser checks. What is unit-tested
# here is the part with real logic — bounded fan-out and role filtering.


def test_bus_fans_out_to_every_subscriber(client, clean_runs):
    from app.contracts.telemetry import StreamEventType

    bus = client.app.state.runner.bus
    master = bus.subscribe(Role.MASTER)
    power = bus.subscribe(Role.POWER)

    event = bus.publish(StreamEventType.TELEMETRY, {"frame_seq": 7}, None)

    assert master.queue.get_nowait().event_seq == event.event_seq
    assert power.queue.get_nowait().event_seq == event.event_seq
    bus.unsubscribe(master)
    bus.unsubscribe(power)


def test_slow_subscriber_is_dropped_rather_than_buffered_without_bound(client, clean_runs):
    """A client that cannot keep up must be asked to resubscribe, not allowed to grow an
    unbounded backlog."""
    from app.contracts.telemetry import StreamEventType
    from app.simulation.bus import MAX_QUEUE

    bus = client.app.state.runner.bus
    subscriber = bus.subscribe(Role.MASTER)

    for _ in range(MAX_QUEUE + 5):
        bus.publish(StreamEventType.TELEMETRY, {}, None)

    assert subscriber.overflowed is True
    assert subscriber.queue.qsize() <= MAX_QUEUE
    bus.unsubscribe(subscriber)


def test_frame_filtering_is_applied_per_role(client, clean_runs):
    """The same published frame reaches each role with only its own channels."""
    from app.api.serialization import filter_frame

    _start_run(client)
    body = _wait_for_frames(client)
    logout(client)

    raw = client.app.state.runner.latest_frame.model_dump(mode="json")
    assert body is not None

    master_view = filter_frame(raw, Role.MASTER)
    power_view = filter_frame(raw, Role.POWER)

    assert len(master_view["values"]) == 28
    assert set(power_view["values"]) < set(master_view["values"])
    assert "wheel_speed" not in power_view["values"]
    # Both views describe the same authoritative frame.
    assert master_view["frame_seq"] == power_view["frame_seq"]


def test_stream_requires_a_session(client, clean_runs):
    logout(client)
    assert client.get("/api/stream").status_code == 401


# ------------------------------------------------------------------ contact loss

def test_injected_contact_loss_stops_delivery_without_pausing_the_run(client, clean_runs):
    """A simulated contact loss leaves the app connected while readings go stale — a
    different thing from the run being paused or the browser disconnecting."""
    _start_run(client)
    before = _wait_for_frames(client)["run"]["frame_seq"]

    client.post(
        "/api/simulator/conditions", json={"contact_loss": True}, headers=csrf_headers(client)
    )
    time.sleep(3.0)
    during = client.get("/api/runs/current").json()

    assert during["run"]["frame_seq"] == before or during["run"]["frame_seq"] <= before + 1
    assert during["run"]["status"] == "RUNNING"

    # The vehicle kept advancing behind the outage.
    truth = client.get("/api/simulator/state").json()["truth"]
    assert truth["sim_time_s"] > during["frame"]["sim_time_s"]

    client.post(
        "/api/simulator/conditions", json={"contact_loss": False}, headers=csrf_headers(client)
    )
    after = _wait_for_frames(client, minimum=during["run"]["frame_seq"] + 1)
    assert after["run"]["frame_seq"] > during["run"]["frame_seq"]
