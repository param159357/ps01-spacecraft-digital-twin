"""A specialist may recommend an action; only the master may approve it.

The workflow this covers: a power engineer sees a power incident, asks for a comparison,
and the master sees that exact proposal and approves it. Proposals live in one process
and are not persisted, so this is discovery, not durability — a restart loses them and
the operator compares again.
"""

from __future__ import annotations

from tests.conftest import csrf_headers, login


def _open_power_incident(client) -> str:
    """Drive the simulator until a power incident exists, and return its id."""
    login(client, "simulator")
    client.post(
        "/api/simulator/run",
        json={"operation": "RESET", "seed": 4242, "initial_soc_percent": 18},
        headers=csrf_headers(client),
    )
    client.post(
        "/api/simulator/conditions",
        json={"extra_load_w": 250},
        headers=csrf_headers(client),
    )
    client.post("/api/simulator/run", json={"operation": "START"}, headers=csrf_headers(client))

    import time

    for _ in range(120):
        login(client, "master")
        incidents = client.get("/api/incidents").json()["incidents"]
        power = [i for i in incidents if i["subsystem"] == "POWER"]
        if power:
            return power[0]["id"]
        time.sleep(0.25)
    raise AssertionError("no power incident opened")


def test_power_can_raise_a_comparison_that_master_can_see_and_approve(client, clean_runs):
    incident_id = _open_power_incident(client)

    # The power specialist raises a recommendation.
    login(client, "power")
    created = client.post(
        f"/api/incidents/{incident_id}/compare",
        json={"action_id": "REDUCE_PAYLOAD_POWER", "duty_percent": 50},
        headers=csrf_headers(client),
    )
    assert created.status_code == 200, created.text
    proposal = created.json()["proposal"]
    assert proposal["requested_by"] == "power"
    assert proposal["requested_by_role"] == "POWER"

    # The specialist can see its own recommendation listed.
    listed = client.get(f"/api/incidents/{incident_id}/proposals")
    assert listed.status_code == 200, listed.text
    assert listed.json()["approval_role"] == "MASTER"
    assert proposal["id"] in {p["id"] for p in listed.json()["proposals"]}

    # ...but cannot approve it.
    refused = client.post(
        "/api/commands",
        json={"proposal_id": proposal["id"], "idempotency_key": "power-tries"},
        headers=csrf_headers(client),
    )
    assert refused.status_code == 403

    # The master discovers the same proposal and approves that exact one.
    login(client, "master")
    seen = client.get(f"/api/incidents/{incident_id}/proposals").json()["proposals"]
    match = next(p for p in seen if p["id"] == proposal["id"])
    assert match["requested_by"] == "power"
    assert match["action_arguments"]["duty_percent"] == 50

    approved = client.post(
        "/api/commands",
        json={"proposal_id": match["id"], "idempotency_key": "master-approves"},
        headers=csrf_headers(client),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["proposal_id"] == proposal["id"]
    assert approved.json()["action_arguments"]["duty_percent"] == 50


def test_proposal_listing_is_scoped_to_the_incident_subsystem(client, clean_runs):
    incident_id = _open_power_incident(client)

    login(client, "power")
    client.post(
        f"/api/incidents/{incident_id}/compare",
        json={"action_id": "REDUCE_PAYLOAD_POWER", "duty_percent": 0},
        headers=csrf_headers(client),
    )

    # A flight specialist may not read a power incident's recommendations.
    login(client, "flight")
    assert client.get(f"/api/incidents/{incident_id}/proposals").status_code == 403

    # Unauthenticated access is refused outright.
    client.post("/api/auth/logout", headers=csrf_headers(client))
    assert client.get(f"/api/incidents/{incident_id}/proposals").status_code == 401


def test_an_expired_recommendation_is_not_offered_to_master(client, clean_runs):
    from datetime import UTC, datetime, timedelta

    incident_id = _open_power_incident(client)
    login(client, "power")
    created = client.post(
        f"/api/incidents/{incident_id}/compare",
        json={"action_id": "REDUCE_PAYLOAD_POWER", "duty_percent": 0},
        headers=csrf_headers(client),
    )
    proposal_id = created.json()["proposal"]["id"]

    # Age it past its TTL the way the clock would.
    service = client.app.state.runner.operations
    from uuid import UUID

    stored = service.proposals[UUID(proposal_id)]
    service.proposals[UUID(proposal_id)] = stored.model_copy(
        update={"expires_at_utc": datetime.now(UTC) - timedelta(seconds=1)}
    )

    login(client, "master")
    listed = client.get(f"/api/incidents/{incident_id}/proposals").json()["proposals"]
    assert proposal_id not in {p["id"] for p in listed}

    # And it still cannot be approved.
    refused = client.post(
        "/api/commands",
        json={"proposal_id": proposal_id, "idempotency_key": "too-late"},
        headers=csrf_headers(client),
    )
    assert refused.status_code == 409
