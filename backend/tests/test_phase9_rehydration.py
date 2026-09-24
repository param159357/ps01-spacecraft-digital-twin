"""Incident lifecycle rehydration across an API restart.

The defect these cover: the engine's deduplication cache lives in this process. A restart
inside a run left it empty, so the next frame carrying a condition that had never stopped
found no active incident for its key and opened a second one. One real problem read as two
incidents — in the console, in the history counts and in the investigation queue.

Every test here drives real `OperationalService` instances against the test database and
builds its frames directly, so nothing reads simulator truth, a hidden injection or
`private_checkpoint`. A restart is simulated the way it actually happens: a brand new
service object, with an empty engine, pointed at the same run.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.config.loader import load_configuration
from app.contracts.common import (
    Illumination,
    OperatingMode,
    QualityState,
    Role,
    RunStatus,
    SourceKind,
    may_act_on,
)
from app.contracts.incidents import Lifecycle
from app.contracts.telemetry import ChannelQuality, FrameContext, TelemetryFrame
from app.operations import OperationalService
from app.storage.models import IncidentEventRow, IncidentRow, Run

RUN_ID = uuid4()
OTHER_RUN_ID = uuid4()

# ENERGY_RESERVE drives these: the warning threshold is 20%, so 15% is a plain, valid
# observation that opens an incident, and 60% is a plain healthy one. A frame carrying
# only this channel leaves every other rule without evidence, which keeps the test about
# one incident rather than the whole vehicle.
LOW_SOC = 15.0
HEALTHY_SOC = 60.0
KEY = ("POWER_ENERGY", "battery")


@pytest.fixture(autouse=True)
def _runs(session_factory):
    """Incidents carry a foreign key to their run, so both runs must exist."""
    with session_factory() as db:
        for run_id, label in ((RUN_ID, "rehydration"), (OTHER_RUN_ID, "rehydration-other")):
            if db.get(Run, run_id) is None:
                db.add(
                    Run(
                        id=run_id,
                        source="LIVE",
                        seed=11,
                        config_version="test",
                        model_version="test",
                        sim_epoch_utc=datetime(2026, 9, 24, tzinfo=UTC),
                        status="STOPPED",
                        control_revision=0,
                        label=label,
                    )
                )
        db.commit()
    yield
    # Each test starts from an empty incident table so the row counts below mean what
    # they say. Runs are left alone; other tests share the database.
    with session_factory() as db:
        for run_id in (RUN_ID, OTHER_RUN_ID):
            for row in db.execute(
                select(IncidentRow).where(IncidentRow.run_id == run_id)
            ).scalars():
                db.execute(
                    IncidentEventRow.__table__.delete().where(
                        IncidentEventRow.incident_id == row.id
                    )
                )
                db.delete(row)
        db.commit()


def _service() -> OperationalService:
    """A fresh process's worth of state: empty engine, empty rule timers."""
    return OperationalService(load_configuration(), SimpleNamespace(publish=lambda *a, **k: None))


def _frame(
    sim_time_s: float,
    *,
    frame_seq: int,
    soc: float | None,
    valid: bool = True,
    run_id: UUID = RUN_ID,
) -> TelemetryFrame:
    config = load_configuration()
    now = datetime(2026, 9, 24, 12, tzinfo=UTC) + timedelta(seconds=sim_time_s)
    return TelemetryFrame(
        run_id=run_id,
        frame_seq=frame_seq,
        tick_seq=frame_seq,
        sim_time_s=sim_time_s,
        sim_epoch_utc=datetime(2026, 9, 24, tzinfo=UTC),
        received_at_utc=now,
        values={"battery_soc": soc},
        quality={
            "battery_soc": ChannelQuality(
                state=QualityState.VALID if valid else QualityState.MISSING,
                reason=None if valid else "injected dropout",
                source_time_utc=now,
            )
        },
        context=FrameContext(
            vehicle_config_version=config.spacecraft.config_version,
            model_version=config.spacecraft.model_version,
            run_id=run_id,
            source=SourceKind.LIVE,
            run_status=RunStatus.RUNNING,
            mode=OperatingMode.NOMINAL,
            illumination=Illumination.SUNLIGHT,
            contact_expected=True,
            control_revision=0,
        ),
    )


def _drive(
    service: OperationalService,
    times: range,
    *,
    soc: float | None,
    valid: bool = True,
    run_id: UUID = RUN_ID,
) -> None:
    """Feed a span of frames through the real `process_frame` path.

    `asyncio.run` per span follows the convention in the rest of this suite, which has no
    pytest-asyncio plugin. The service keeps its state on the object rather than on the
    loop, so a fresh loop per span changes nothing these tests depend on.
    """

    async def spin() -> None:
        for t in times:
            await service.process_frame(
                _frame(float(t), frame_seq=t, soc=soc, valid=valid, run_id=run_id)
            )

    asyncio.run(spin())


def _reserve(service: OperationalService, run_id: UUID = RUN_ID):
    return next(
        (
            i
            for i in service.incident_engine.incidents.values()
            if i.run_id == run_id and (i.family, i.affected_entity) == KEY
        ),
        None,
    )


def _active_rows(session_factory, run_id: UUID = RUN_ID) -> int:
    with session_factory() as db:
        return (
            db.execute(
                select(func.count())
                .select_from(IncidentRow)
                .where(
                    IncidentRow.run_id == run_id,
                    IncidentRow.family == KEY[0],
                    IncidentRow.affected_entity == KEY[1],
                    IncidentRow.lifecycle.in_(("PENDING", "OPEN", "RECOVERING")),
                )
            ).scalar()
            or 0
        )


# ------------------------------------------------------------------- the core defect


def test_a_restart_continues_the_same_incident(session_factory):
    """Instance 1 opens it; instance 2 is the restart and must not open a second."""
    first = _service()
    _drive(first, range(0, 8), soc=LOW_SOC)
    opened = _reserve(first)
    assert opened is not None and opened.lifecycle is Lifecycle.OPEN
    assert _active_rows(session_factory) == 1

    # The restart. Same run, same observed condition, a service that has never seen it.
    second = _service()
    assert second.incident_engine.incidents == {}
    _drive(second, range(8, 14), soc=LOW_SOC)

    continued = _reserve(second)
    assert continued is not None
    assert continued.id == opened.id, "a restart must continue the incident, not replace it"
    assert continued.lifecycle is Lifecycle.OPEN
    assert continued.first_seen_utc == opened.first_seen_utc
    assert continued.last_seen_utc > opened.last_seen_utc
    # And the database still holds exactly one active row for the deduplication key.
    assert _active_rows(session_factory) == 1


def test_the_restart_preserves_the_incident_record(session_factory):
    """Severity, acknowledgement, evidence, timeline and evidence revision all carry."""
    first = _service()
    _drive(first, range(0, 8), soc=LOW_SOC)
    opened = _reserve(first)
    assert opened is not None
    acknowledged = first.acknowledge(opened.id, "power")
    assert acknowledged.acknowledgement.acknowledged is True

    with session_factory() as db:
        revision_before = db.get(IncidentRow, opened.id).evidence_revision
    assert revision_before > 0, "acknowledgement is a meaningful transition"

    second = _service()
    _drive(second, range(8, 14), soc=LOW_SOC)
    continued = _reserve(second)

    assert continued is not None
    assert continued.acknowledgement.acknowledged is True
    assert continued.acknowledgement.acknowledged_by == "power"
    assert continued.severity is opened.severity
    assert continued.rule_id == opened.rule_id
    assert continued.dedup_key == opened.dedup_key
    assert continued.timeline[0].kind == "OPENED"
    assert continued.observed_evidence, "observed evidence survives the restart"
    with session_factory() as db:
        # The revision lives on the row and is never reset by rehydration, so provider
        # deduplication keeps working across a restart.
        assert db.get(IncidentRow, opened.id).evidence_revision >= revision_before


def test_another_run_gets_its_own_incident(session_factory):
    """Rehydration is per run. A reset must not inherit the previous run's incident."""
    first = _service()
    _drive(first, range(0, 8), soc=LOW_SOC)
    original = _reserve(first)
    assert original is not None

    second = _service()
    _drive(second, range(0, 8), soc=LOW_SOC, run_id=OTHER_RUN_ID)

    fresh = _reserve(second, OTHER_RUN_ID)
    assert fresh is not None
    assert fresh.id != original.id
    assert fresh.run_id == OTHER_RUN_ID
    assert _reserve(second, RUN_ID) is None, "the other run's incident was not imported"
    assert _active_rows(session_factory, RUN_ID) == 1
    assert _active_rows(session_factory, OTHER_RUN_ID) == 1


def test_a_resolved_incident_is_not_restored_as_active(session_factory):
    """Section 8.2: a recurrence after resolution is a new incident, not the old one."""
    first = _service()
    _drive(first, range(0, 8), soc=LOW_SOC)
    opened = _reserve(first)
    assert opened is not None
    # Healthy for longer than the dwell, so it resolves.
    _drive(first, range(8, 30), soc=HEALTHY_SOC)
    assert _reserve(first).lifecycle is Lifecycle.RESOLVED
    assert _active_rows(session_factory) == 0

    second = _service()
    _drive(second, range(30, 40), soc=LOW_SOC)
    recurrence = _reserve(second)

    assert recurrence is not None
    assert recurrence.id != opened.id, "a resolved incident must not be reanimated"
    assert recurrence.lifecycle is Lifecycle.OPEN


# --------------------------------------------------------------------- dwell after restart


def test_the_healthy_dwell_starts_fresh_after_a_restart():
    """Requirement: recovery is demonstrated after the restart, not inherited.

    Rule timers are deliberately not rehydrated, so the first healthy frame after a
    restart begins a new dwell and reports RECOVERING rather than resolving at once.
    """
    dwell = float(load_configuration().rules.defaults["healthy_dwell_s"])
    first = _service()
    _drive(first, range(0, 8), soc=LOW_SOC)
    # Nearly recovered before the restart: healthy for almost the full dwell.
    _drive(first, range(8, 8 + int(dwell) - 1), soc=HEALTHY_SOC)
    assert _reserve(first).lifecycle is Lifecycle.RECOVERING

    restart_at = 8 + int(dwell)
    second = _service()
    _drive(second, range(restart_at, restart_at + 2), soc=HEALTHY_SOC)
    assert _reserve(second).lifecycle is Lifecycle.RECOVERING, (
        "the pre-restart healthy period must not resolve it on the first frame back"
    )

    # Once a full dwell of healthy observations has been received, it resolves.
    _drive(second, range(restart_at + 2, restart_at + int(dwell) + 3), soc=HEALTHY_SOC)
    assert _reserve(second).lifecycle is Lifecycle.RESOLVED


def test_missing_data_does_not_satisfy_the_healthy_dwell_after_a_restart():
    """Section 1.3: missing data is not healthy, so it cannot run down the dwell clock.

    Without the streak break this resolves on the strength of a dropout: the dwell is
    elapsed simulated time since the first healthy sample, and a long gap with no evidence
    at all would otherwise count in full.
    """
    dwell = int(float(load_configuration().rules.defaults["healthy_dwell_s"]))
    first = _service()
    _drive(first, range(0, 8), soc=LOW_SOC)
    assert _reserve(first) is not None

    second = _service()
    # A couple of healthy frames start a dwell, then the channel drops out for far longer
    # than the dwell, then one healthy frame returns.
    _drive(second, range(8, 11), soc=HEALTHY_SOC)
    assert _reserve(second).lifecycle is Lifecycle.RECOVERING
    _drive(second, range(11, 11 + dwell * 3), soc=None, valid=False)
    assert _reserve(second).lifecycle is Lifecycle.RECOVERING, (
        "a dropout must not resolve an incident"
    )

    resumed = 11 + dwell * 3
    _drive(second, range(resumed, resumed + 2), soc=HEALTHY_SOC)
    assert _reserve(second).lifecycle is Lifecycle.RECOVERING, (
        "the dwell must be earned again from observations received after the dropout"
    )
    _drive(second, range(resumed + 2, resumed + dwell + 3), soc=HEALTHY_SOC)
    assert _reserve(second).lifecycle is Lifecycle.RESOLVED


# ------------------------------------------------------- duplicates that already exist


def _insert_active_row(session_factory, incident_id: UUID, first_seen: datetime) -> None:
    """A row shaped like one this engine writes, inserted directly.

    This is what the database looks like after a restart that happened before this fix:
    two active rows against one deduplication key.
    """
    payload = {
        "id": str(incident_id),
        "run_id": str(RUN_ID),
        "rule_id": "ENERGY_RESERVE",
        "rule_version": "test",
        "family": KEY[0],
        "subsystem": "POWER",
        "affected_groups": ["POWER"],
        "affected_entity": KEY[1],
        "severity": "WARNING",
        "lifecycle": "OPEN",
        "first_seen_utc": first_seen.isoformat(),
        "last_seen_utc": first_seen.isoformat(),
        "observed_evidence": [
            {
                "text": "Battery reserve is low.",
                "evidence_id": "frame:1",
                "channel_id": "battery_soc",
            }
        ],
        "timeline": [
            {"at_utc": first_seen.isoformat(), "kind": "OPENED", "detail": "Reserve incident."}
        ],
    }
    with session_factory() as db:
        db.add(
            IncidentRow(
                id=incident_id,
                run_id=RUN_ID,
                rule_id="ENERGY_RESERVE",
                rule_version="test",
                family=KEY[0],
                subsystem="POWER",
                affected_entity=KEY[1],
                affected_groups=["POWER"],
                severity="WARNING",
                lifecycle="OPEN",
                first_seen_utc=first_seen,
                last_seen_utc=first_seen,
                evidence=payload,
            )
        )
        db.commit()


def test_a_pre_existing_duplicate_is_superseded_without_losing_history(session_factory):
    base = datetime(2026, 9, 24, 11, tzinfo=UTC)
    canonical_id, duplicate_id = uuid4(), uuid4()
    # Inserted out of order, so passing depends on the ordering rather than on luck.
    _insert_active_row(session_factory, duplicate_id, base + timedelta(minutes=5))
    _insert_active_row(session_factory, canonical_id, base)
    assert _active_rows(session_factory) == 2

    service = _service()
    _drive(service, range(0, 8), soc=LOW_SOC)

    # The earliest row is the one continued, deterministically.
    continued = _reserve(service)
    assert continued is not None
    assert continued.id == canonical_id
    assert duplicate_id not in service.incident_engine.incidents
    assert service.superseded_duplicates == {duplicate_id: canonical_id}

    with session_factory() as db:
        # History is intact: the duplicate row is still there, untouched, with an
        # append-only note recording what it duplicates.
        duplicate = db.get(IncidentRow, duplicate_id)
        assert duplicate is not None
        assert duplicate.lifecycle == "OPEN", "the duplicate's own fields are not rewritten"
        notes = (
            db.execute(
                select(IncidentEventRow).where(
                    IncidentEventRow.incident_id == duplicate_id,
                    IncidentEventRow.kind == "DUPLICATE_SUPERSEDED",
                )
            )
            .scalars()
            .all()
        )
        assert len(notes) == 1
        assert str(canonical_id) in notes[0].detail
        assert notes[0].evidence_ids == [f"incident:{canonical_id}"]
        # No third row was opened.
        assert _active_rows(session_factory) == 2


def test_the_note_is_written_once_however_often_the_api_restarts(session_factory):
    base = datetime(2026, 9, 24, 11, tzinfo=UTC)
    canonical_id, duplicate_id = uuid4(), uuid4()
    _insert_active_row(session_factory, canonical_id, base)
    _insert_active_row(session_factory, duplicate_id, base + timedelta(minutes=5))

    for start in (0, 20, 40):
        service = _service()
        _drive(service, range(start, start + 8), soc=LOW_SOC)

    with session_factory() as db:
        notes = (
            db.execute(
                select(func.count())
                .select_from(IncidentEventRow)
                .where(
                    IncidentEventRow.incident_id == duplicate_id,
                    IncidentEventRow.kind == "DUPLICATE_SUPERSEDED",
                )
            ).scalar()
            or 0
        )
    assert notes == 1


def test_the_role_scoped_view_shows_one_incident_not_two(session_factory):
    """What the POWER console would receive from `GET /incidents` after a restart."""
    base = datetime(2026, 9, 24, 11, tzinfo=UTC)
    canonical_id, duplicate_id = uuid4(), uuid4()
    _insert_active_row(session_factory, canonical_id, base)
    _insert_active_row(session_factory, duplicate_id, base + timedelta(minutes=5))

    service = _service()
    _drive(service, range(0, 8), soc=LOW_SOC)

    # The same two steps the endpoint performs: list for the run, then scope by role.
    listed = service.list_incidents(RUN_ID)
    visible = [i for i in listed if may_act_on(Role.POWER, i.subsystem)]
    keys = [(i.family, i.affected_entity) for i in visible]

    assert len(keys) == len(set(keys)), f"the console would show a duplicate: {keys}"
    assert [i.id for i in visible] == [canonical_id]
    assert not any(may_act_on(Role.FLIGHT_SYSTEMS, i.subsystem) for i in listed)


# --------------------------------------------------------------------------- containment


def test_a_storage_failure_leaves_monitoring_running(monkeypatch, session_factory):
    """Requirement 10: contain the failure; never disable deterministic monitoring."""
    service = _service()
    calls = {"n": 0}

    def boom(_run_id):
        calls["n"] += 1
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(service, "_load_active_incidents", boom)
    _drive(service, range(0, 8), soc=LOW_SOC)

    # Rules still ran and still opened the incident from observed telemetry.
    assert _reserve(service) is not None
    # And the read was retried a bounded number of times rather than on every frame.
    from app.operations import MAX_REHYDRATE_ATTEMPTS

    assert calls["n"] == MAX_REHYDRATE_ATTEMPTS
    assert RUN_ID not in service._rehydrated_runs  # noqa: SLF001


def test_rehydration_needs_no_simulator_truth(session_factory):
    """The frames driving every test above carry no injected conditions at all."""
    frame = _frame(1.0, frame_seq=1, soc=LOW_SOC)
    assert frame.context.known_commands == {}
    assert frame.engineering == {}

    first = _service()
    _drive(first, range(0, 8), soc=LOW_SOC)
    opened = _reserve(first)
    second = _service()
    _drive(second, range(8, 12), soc=LOW_SOC)

    assert _reserve(second).id == opened.id
    with session_factory() as db:
        row = db.get(IncidentRow, opened.id)
        # Nothing simulator-only reached the stored evidence the rehydration reads back.
        stored = str(row.evidence)
        for hidden in ("computer_resets", "branch_stuck_on", "isolator_failed", "checkpoint"):
            assert hidden not in stored


def test_identity_comes_from_the_primary_key_not_the_payload(session_factory):
    """A row whose stored payload names a different incident is still its own row.

    Caught on a real server: a duplicated row had been cloned with its `evidence` JSON
    intact, so rebuilding the incident from the payload gave it the *other* row's id. The
    duplicate report then named one incident as both the duplicate and the canonical one,
    and a later write would have gone to the wrong row.
    """
    base = datetime(2026, 9, 24, 11, tzinfo=UTC)
    canonical_id, clone_id = uuid4(), uuid4()
    _insert_active_row(session_factory, canonical_id, base)
    # The clone keeps the canonical row's payload — including its `id` — exactly as a
    # copied row would.
    with session_factory() as db:
        payload = dict(db.get(IncidentRow, canonical_id).evidence)
        db.add(
            IncidentRow(
                id=clone_id,
                run_id=RUN_ID,
                rule_id="ENERGY_RESERVE",
                rule_version="test",
                family=KEY[0],
                subsystem="POWER",
                affected_entity=KEY[1],
                affected_groups=["POWER"],
                severity="WARNING",
                lifecycle="OPEN",
                first_seen_utc=base + timedelta(minutes=2),
                last_seen_utc=base + timedelta(minutes=2),
                evidence=payload,
            )
        )
        db.commit()
    assert payload["id"] == str(canonical_id)

    service = _service()
    _drive(service, range(0, 8), soc=LOW_SOC)

    # The clone is recognised as a separate row and named correctly in the report.
    assert service.superseded_duplicates == {clone_id: canonical_id}
    assert _reserve(service).id == canonical_id
    with session_factory() as db:
        note = (
            db.execute(
                select(IncidentEventRow).where(
                    IncidentEventRow.incident_id == clone_id,
                    IncidentEventRow.kind == "DUPLICATE_SUPERSEDED",
                )
            )
            .scalars()
            .one()
        )
        assert str(canonical_id) in note.detail
        # And nothing was written against the canonical row pretending to be the clone.
        assert (
            db.execute(
                select(func.count())
                .select_from(IncidentEventRow)
                .where(
                    IncidentEventRow.incident_id == canonical_id,
                    IncidentEventRow.kind == "DUPLICATE_SUPERSEDED",
                )
            ).scalar()
            == 0
        )
