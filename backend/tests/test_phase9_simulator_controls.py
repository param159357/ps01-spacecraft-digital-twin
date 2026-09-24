"""Simulator console controls: injected resets, the frame counter and reset settings.

These drive the real API and the real 1 Hz loop, so a reset here travels the whole
pipeline the console uses — condition update, tick boundary, twin, observed telemetry,
rule engine — rather than being asserted against a mock. Nothing writes an incident
directly; `RESET_RECURRENCE` has to be opened by the rule from observed evidence or the
test fails.
"""

from __future__ import annotations

import time

from tests.conftest import csrf_headers, login

RESET_POLL_SECONDS = 25.0


def _sim(client) -> None:
    login(client, "simulator")


def _state(client) -> dict:
    """The simulator's own view: commanded target plus what the vehicle has recorded."""
    response = client.get("/api/simulator/state")
    assert response.status_code == 200, response.text
    return response.json()


def _truth(client) -> dict:
    truth = _state(client)["truth"]
    assert truth is not None, "no run is active"
    return truth


def _fresh_run(client, **settings) -> None:
    _sim(client)
    reset = client.post(
        "/api/simulator/run",
        json={"operation": "RESET", **settings},
        headers=csrf_headers(client),
    )
    assert reset.status_code == 200, reset.text
    started = client.post(
        "/api/simulator/run", json={"operation": "START"}, headers=csrf_headers(client)
    )
    assert started.status_code == 200, started.text


def _inject_one_reset(client) -> int:
    """Exactly what the console's button does: read the target, raise it by one.

    The target is read back from the authoritative state rather than assumed, because
    the vehicle applies one event per increment per tick and a stale target would either
    skip an event or command nothing at all.
    """
    _sim(client)
    current = int(_truth(client)["hidden_conditions"]["computer_resets"])
    response = client.post(
        "/api/simulator/conditions",
        json={"computer_resets": current + 1},
        headers=csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    return current + 1


def _wait_for_resets(client, expected: int) -> dict:
    deadline = time.monotonic() + RESET_POLL_SECONDS
    truth = _truth(client)
    while time.monotonic() < deadline:
        truth = _truth(client)
        if int(truth["resets_applied"]) >= expected:
            return truth
        time.sleep(0.25)
    raise AssertionError(
        f"only {truth['resets_applied']} reset(s) recorded, expected {expected}; "
        f"events {truth['reset_events_s']}"
    )


# ------------------------------------------------------------------- injected resets


def test_one_injection_records_exactly_one_reset_event(client, clean_runs):
    """One click, one real event — and it does not keep firing afterwards."""
    _fresh_run(client)
    assert int(_truth(client)["resets_applied"]) == 0

    _inject_one_reset(client)
    truth = _wait_for_resets(client, 1)
    assert int(truth["resets_applied"]) == 1
    assert len(truth["reset_events_s"]) == 1

    # Several more ticks pass with the target unchanged. A raised target must not keep
    # producing events, or one click would become a stream of them.
    time.sleep(4.0)
    settled = _truth(client)
    assert int(settled["resets_applied"]) == 1
    assert len(settled["reset_events_s"]) == 1


def test_polling_the_simulator_state_creates_no_reset_events(client, clean_runs):
    """Reading state is a read. The console polls it every two seconds."""
    _fresh_run(client)
    for _ in range(20):
        truth = _truth(client)
        assert int(truth["resets_applied"]) == 0
        assert truth["reset_events_s"] == []
    time.sleep(2.0)
    assert int(_truth(client)["resets_applied"]) == 0


def test_two_injections_open_exactly_one_reset_recurrence_incident(client, clean_runs):
    """Two distinct events inside the window; the rule engine does the rest."""
    _fresh_run(client)
    _inject_one_reset(client)
    _wait_for_resets(client, 1)
    _inject_one_reset(client)
    truth = _wait_for_resets(client, 2)

    events = [float(t) for t in truth["reset_events_s"]]
    assert len(events) == 2
    assert len(set(events)) == 2, f"two clicks must give two distinct times, got {events}"

    # The incident is the rule's verdict, read from the operator side.
    deadline = time.monotonic() + RESET_POLL_SECONDS
    matching: list[dict] = []
    while time.monotonic() < deadline:
        login(client, "flight")
        incidents = client.get("/api/incidents").json()["incidents"]
        matching = [i for i in incidents if i["rule_id"] == "RESET_RECURRENCE"]
        if matching:
            break
        time.sleep(0.25)

    assert len(matching) == 1, f"expected exactly one incident, got {len(matching)}"
    incident = matching[0]
    assert incident["subsystem"] == "FLIGHT_SYSTEMS"
    assert incident["severity"] == "WARNING"
    assert "2 distinct computer reset events" in incident["observed_evidence"][-1]["text"]

    # The master sees the same record, and the power console does not.
    login(client, "master")
    master_ids = {i["id"] for i in client.get("/api/incidents").json()["incidents"]}
    assert incident["id"] in master_ids
    login(client, "power")
    power = client.get("/api/incidents").json()
    assert incident["id"] not in {i["id"] for i in power["incidents"]}


def test_the_reset_target_is_bounded(client, clean_runs):
    """The console disables its button at this bound; the API enforces it regardless."""
    _fresh_run(client)
    _sim(client)
    refused = client.post(
        "/api/simulator/conditions",
        json={"computer_resets": 21},
        headers=csrf_headers(client),
    )
    assert refused.status_code == 422
    negative = client.post(
        "/api/simulator/conditions",
        json={"computer_resets": -1},
        headers=csrf_headers(client),
    )
    assert negative.status_code == 422


def test_an_operator_cannot_inject_a_reset(client, clean_runs):
    """Injection is simulator-only. A console must not be able to fake its own evidence."""
    _fresh_run(client)
    for username in ("master", "power", "thermal", "flight"):
        login(client, username)
        denied = client.post(
            "/api/simulator/conditions",
            json={"computer_resets": 1},
            headers=csrf_headers(client),
        )
        assert denied.status_code == 403, username
        assert denied.json()["error"] == "ROLE_NOT_PERMITTED"


def test_a_new_run_starts_the_reset_counter_and_events_empty(client, clean_runs):
    """The counter is per run: a reset restarts it without implying a physical reset."""
    _fresh_run(client)
    _inject_one_reset(client)
    _wait_for_resets(client, 1)

    _fresh_run(client)
    truth = _truth(client)
    assert int(truth["resets_applied"]) == 0
    assert truth["reset_events_s"] == []
    assert int(truth["hidden_conditions"]["computer_resets"]) == 0


# --------------------------------------------------------------------- frame counter


def test_the_frame_sequence_advances_and_restarts_on_a_new_run(client, clean_runs):
    """The console reads the newest frame's own sequence, so it has to move.

    The panel previously read the run metadata's `frame_seq`, which is captured with the
    snapshot and then never moves, so it sat at 0 for the whole demonstration.
    """
    _fresh_run(client)
    login(client, "master")

    deadline = time.monotonic() + 15.0
    first = None
    while time.monotonic() < deadline:
        frame = client.get("/api/runs/current").json()["frame"]
        if frame is not None:
            first = int(frame["frame_seq"])
            break
        time.sleep(0.25)
    assert first is not None, "no frame was published"

    time.sleep(3.0)
    later = int(client.get("/api/runs/current").json()["frame"]["frame_seq"])
    assert later > first, f"frame sequence did not advance: {first} -> {later}"

    # A new run restarts the sequence rather than continuing the old one.
    run_before = client.get("/api/runs/current").json()["run"]["run_id"]
    _fresh_run(client)
    login(client, "master")
    deadline = time.monotonic() + 15.0
    after = None
    while time.monotonic() < deadline:
        body = client.get("/api/runs/current").json()
        frame = body["frame"]
        if frame is not None and body["run"]["run_id"] != run_before:
            after = int(frame["frame_seq"])
            break
        time.sleep(0.25)
    assert after is not None, "the new run published no frame"
    assert after < later, f"a new run must restart the sequence: {later} -> {after}"


# ----------------------------------------------------------- advanced reset settings


def test_advanced_reset_settings_are_applied_to_the_new_run(client, clean_runs):
    """Starting SOC, spacecraft clock and payload temperature, as the console sends them."""
    capacity_wh = 1000.0
    _sim(client)
    reset = client.post(
        "/api/simulator/run",
        json={
            "operation": "RESET",
            "initial_soc_percent": 42.0,
            "initial_sim_time_s": 2400.0,
            "initial_payload_temperature_c": 48.0,
        },
        headers=csrf_headers(client),
    )
    assert reset.status_code == 200, reset.text

    truth = _truth(client)
    assert abs(float(truth["energy_wh"]) - capacity_wh * 0.42) < 1.0
    assert abs(float(truth["sim_time_s"]) - 2400.0) < 1.0
    assert abs(float(truth["temperatures_c"]["payload"]) - 48.0) < 0.01

    # The run is created paused, so these are starting conditions rather than an edit to
    # a run already in progress.
    assert _state(client)["run"]["status"] == "PAUSED"


def test_advanced_reset_settings_reject_values_outside_their_bounds(client, clean_runs):
    _sim(client)
    for payload in (
        {"initial_soc_percent": 99.0},
        {"initial_soc_percent": 1.0},
        {"initial_sim_time_s": 5400.0},
        {"initial_sim_time_s": -1.0},
        {"initial_payload_temperature_c": 60.0},
        {"initial_payload_temperature_c": -30.0},
    ):
        response = client.post(
            "/api/simulator/run",
            json={"operation": "RESET", **payload},
            headers=csrf_headers(client),
        )
        assert response.status_code == 422, (payload, response.text)


def test_maneuver_mode_is_commanded_and_visible_as_context(client, clean_runs):
    """Mode is a known command, not a creation parameter, so the console sends it after
    the reset. It must reach both the simulator's own view and the operator's context."""
    _fresh_run(client)
    _sim(client)
    applied = client.post(
        "/api/simulator/conditions",
        json={"mode": "MANEUVER"},
        headers=csrf_headers(client),
    )
    assert applied.status_code == 200, applied.text
    assert _truth(client)["known_commands"]["mode"] == "MANEUVER"

    login(client, "flight")
    deadline = time.monotonic() + 15.0
    mode = None
    while time.monotonic() < deadline:
        frame = client.get("/api/runs/current").json()["frame"]
        if frame is not None and frame["context"]["mode"] == "MANEUVER":
            mode = frame["context"]["mode"]
            break
        time.sleep(0.25)
    assert mode == "MANEUVER", "the operator console never saw the commanded mode"


def test_two_rapid_injections_are_not_collapsed_into_one(client, clean_runs):
    """Both clicks count even when the second lands before the first has been applied.

    This is why the injection reads the commanded *target* rather than the number of
    resets already recorded. The vehicle applies one event per increment per tick, so a
    second click arriving inside the same second would read `resets_applied == 0`, command
    a target of 1 again, and silently produce only one event.
    """
    _fresh_run(client)
    first = _inject_one_reset(client)
    second = _inject_one_reset(client)
    assert (first, second) == (1, 2), "the second click must raise the target again"

    truth = _wait_for_resets(client, 2)
    events = [float(t) for t in truth["reset_events_s"]]
    assert len(events) == 2
    assert len(set(events)) == 2
