"""The backend-owned simulation run — CLAUDE.md section 2.3.

One API process owns exactly one live run. Browsers are clients: closing the simulator
tab does not stop this loop, and every connected console observes the same authoritative
sequence.

Per-tick order, deliberately:
  1. apply accepted control changes at the tick boundary
  2. advance truth with an explicit `dt_s`
  3. generate observed telemetry with seeded noise and quality markers
  4. store the frame in a short transaction
  5. publish the *committed* state to subscribers

A storage failure pauses the run and surfaces an explicit error. It is never shown as
successfully recorded data.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.config.loader import VehicleConfiguration
from app.config.settings import Settings
from app.contracts.common import RunStatus, SourceKind
from app.contracts.telemetry import StreamEventType, TelemetryFrame
from app.storage.db import session_scope
from app.storage.models import Run, TelemetryFrameRow

from . import observation, twin
from .bus import EventBus
from .observation import SensorNoise
from .truth import ConditionUpdate, InjectedConditions, SimulatorTruth

logger = logging.getLogger("ps01.runner")


class RunnerError(RuntimeError):
    """Raised for an operation that cannot be satisfied in the current state."""


class SimulationRunner:
    def __init__(self, config: VehicleConfiguration, settings: Settings) -> None:
        self.config = config
        self.settings = settings
        self.bus = EventBus()

        # One in-process lock guards run changes and command application. It is never
        # held across a network call, and never across the storage write.
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

        self.run_id: UUID | None = None
        self.status: RunStatus = RunStatus.STOPPED
        self.source: SourceKind = SourceKind.LIVE
        self.seed: int = config.spacecraft.simulation.seed
        self.sim_epoch_utc: datetime = datetime.now(UTC)
        self.control_revision: int = 0
        self.frame_seq: int = 0

        self.truth: SimulatorTruth = SimulatorTruth.initial(config.spacecraft)
        self._noise = SensorNoise(self.seed)
        self.latest_frame: TelemetryFrame | None = None

        self._last_publish_monotonic: float | None = None
        self.storage_error: str | None = None
        self.operations: Any | None = None
        self._pending_commands: list[tuple[UUID, float]] = []
        self._queued_command_ids: set[UUID] = set()

    # ------------------------------------------------------------------ lifecycle

    async def startup(self) -> None:
        """Recover the last live run, paused. The simulator resumes or resets it."""
        try:
            restored = await asyncio.to_thread(self._load_latest_run)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            self.storage_error = f"Could not read the last run: {exc}"
            logger.error("run recovery failed: %s", exc)
            return

        if restored is None:
            logger.info("no previous live run; waiting for the simulator to start one")
            return

        run_id, seed, epoch, control_revision, frame_seq, checkpoint = restored
        self.run_id = run_id
        self.seed = seed
        self.sim_epoch_utc = epoch
        self.control_revision = control_revision
        self.frame_seq = frame_seq
        self.truth = SimulatorTruth.from_checkpoint(checkpoint or {}, self.config.spacecraft)
        self._noise = SensorNoise(seed + self.truth.tick_seq)
        # A restart always comes back paused, never silently running.
        self.status = RunStatus.PAUSED
        await asyncio.to_thread(self._write_run_status, run_id, RunStatus.PAUSED)
        logger.info("recovered run %s at tick %s (paused)", run_id, self.truth.tick_seq)

        self._task = asyncio.create_task(self._loop(), name="ps01-sim-loop")

    async def shutdown(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self.run_id is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(self._checkpoint, self.run_id, self.truth)

    def _ensure_task(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="ps01-sim-loop")

    # ------------------------------------------------------------ run operations

    async def start(self) -> dict[str, Any]:
        async with self._lock:
            # The adopted run may have gone (a manual cleanup, a restored database).
            # Writing frames against a missing run would fail every tick, so verify it
            # still exists and start a fresh one if it does not.
            if self.run_id is not None and not await asyncio.to_thread(
                self._run_exists, self.run_id
            ):
                logger.warning("run %s is no longer in storage; starting a new run", self.run_id)
                self.run_id = None
            if self.run_id is None:
                await self._create_run_locked(InjectedConditions())
            self.status = RunStatus.RUNNING
            self.control_revision += 1
            run_id, revision = self.run_id, self.control_revision
        await asyncio.to_thread(self._write_run_status, run_id, RunStatus.RUNNING, revision)
        self._ensure_task()
        self._publish_run_changed("started")
        return self.run_metadata()

    async def pause(self) -> dict[str, Any]:
        async with self._lock:
            if self.run_id is None:
                raise RunnerError("There is no run to pause.")
            self.status = RunStatus.PAUSED
            self.control_revision += 1
            run_id, revision, truth = self.run_id, self.control_revision, self.truth.copy()
        await asyncio.to_thread(self._write_run_status, run_id, RunStatus.PAUSED, revision)
        await asyncio.to_thread(self._checkpoint, run_id, truth)
        self._publish_run_changed("paused")
        return self.run_metadata()

    async def reset(
        self,
        seed: int | None = None,
        initial_soc_percent: float | None = None,
        initial_sim_time_s: float | None = None,
    ) -> dict[str, Any]:
        """Start a brand-new run. Previous runs stay in the database untouched."""
        async with self._lock:
            previous = self.run_id
            await self._create_run_locked(InjectedConditions(), seed=seed)
            if initial_soc_percent is not None:
                self.truth.energy_wh = (
                    self.config.spacecraft.power.battery_capacity_wh * initial_soc_percent / 100.0
                )
                await asyncio.to_thread(self._checkpoint, self.run_id, self.truth)
            if initial_sim_time_s is not None:
                self.truth.sim_time_s = initial_sim_time_s
                await asyncio.to_thread(self._checkpoint, self.run_id, self.truth)
            self.status = RunStatus.PAUSED
        if previous is not None:
            await asyncio.to_thread(self._end_run, previous)
        self._publish_run_changed("reset")
        return self.run_metadata()

    async def update_conditions(self, update: ConditionUpdate) -> dict[str, Any]:
        """Apply bounded injection at the next tick boundary."""
        async with self._lock:
            if self.run_id is None:
                raise RunnerError("Start a run before changing conditions.")
            self.truth.conditions = update.apply_to(self.truth.conditions)
            self.control_revision += 1
            run_id, revision, truth = self.run_id, self.control_revision, self.truth.copy()
        await asyncio.to_thread(self._write_run_status, run_id, self.status, revision)
        await asyncio.to_thread(self._checkpoint, run_id, truth)
        self._publish_run_changed("conditions_changed")
        return {"control_revision": revision, "conditions": truth.conditions.known_commands}

    async def _create_run_locked(
        self, conditions: InjectedConditions, seed: int | None = None
    ) -> None:
        """Caller must hold the lock."""
        self.seed = seed if seed is not None else self.config.spacecraft.simulation.seed
        self.sim_epoch_utc = datetime.now(UTC)
        self.truth = SimulatorTruth.initial(self.config.spacecraft, conditions)
        self._noise = SensorNoise(self.seed)
        self.frame_seq = 0
        self.control_revision = 0
        self.latest_frame = None
        self._last_publish_monotonic = None
        self.storage_error = None
        self.run_id = await asyncio.to_thread(self._insert_run)
        self.status = RunStatus.PAUSED
        logger.info("created run %s (seed %s)", self.run_id, self.seed)

    # -------------------------------------------------------------------- the loop

    async def _loop(self) -> None:
        tick_s = self.settings.sim_tick_seconds
        next_at = time.monotonic()
        try:
            while True:
                next_at += tick_s
                delay = next_at - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                else:
                    # Fell behind (a slow write, a suspended laptop). Resynchronise
                    # rather than sprinting through a backlog of ticks.
                    next_at = time.monotonic()

                try:
                    await self._tick(tick_s)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - one bad tick must not kill the loop
                    logger.exception("simulation tick failed")
        except asyncio.CancelledError:
            logger.info("simulation loop stopped")
            raise

    async def _tick(self, tick_s: float) -> None:
        async with self._lock:
            if self.status is not RunStatus.RUNNING or self.run_id is None:
                return
            if self.storage_error is not None:
                return

            applied_commands = self._pending_commands
            self._pending_commands = []
            for _, duty in applied_commands:
                self.truth.conditions = ConditionUpdate(payload_duty_percent=duty).apply_to(
                    self.truth.conditions
                )
                self.control_revision += 1
            self.truth, result = twin.step(self.config.spacecraft, self.truth, tick_s, self.seed)

            # A simulated contact loss advances the vehicle but delivers nothing to the
            # ground. Clients keep their last values and watch them go stale — which is
            # a different thing from the app losing its connection.
            if self.truth.conditions.contact_loss:
                if self.operations is not None:
                    await self.operations.process_staleness(self.truth.sim_time_s)
                return

            now = time.monotonic()
            previous = self._last_publish_monotonic
            gap_s = tick_s if previous is None else now - previous
            self._last_publish_monotonic = now
            self.frame_seq += 1

            frame = observation.observe(
                config=self.config.spacecraft,
                telemetry=self.config.telemetry,
                truth=self.truth,
                result=result,
                noise=self._noise,
                run_id=self.run_id,
                frame_seq=self.frame_seq,
                sim_epoch_utc=self.sim_epoch_utc,
                received_at_utc=datetime.now(UTC),
                run_status=self.status,
                control_revision=self.control_revision,
                source=self.source,
                seconds_since_previous_frame=gap_s,
            )
            run_id = self.run_id
            truth_snapshot = self.truth.copy()

        # Storage happens off the lock, then publication happens only after the commit.
        try:
            await asyncio.to_thread(self._persist_frame, frame, truth_snapshot)
        except Exception as exc:  # noqa: BLE001
            await self._enter_storage_failure(str(exc))
            return

        async with self._lock:
            # The run may have been reset while the write was in flight. An outdated
            # frame is discarded rather than attached to the new run.
            if self.run_id != run_id:
                logger.info("discarding frame %s from superseded run %s", frame.frame_seq, run_id)
                return
            self.latest_frame = frame

        self.bus.publish(StreamEventType.TELEMETRY, frame.model_dump(mode="json"), run_id)
        if self.operations is not None:
            for command_id, _ in applied_commands:
                self._queued_command_ids.discard(command_id)
                load = frame.value_if_valid("load_power")
                self.operations.mark_applied(
                    command_id,
                    frame.tick_seq,
                    float(load) if isinstance(load, (int, float)) else 0.0,
                )
            await self.operations.process_frame(frame)

    async def queue_payload_command(self, command_id: UUID, duty_percent: float) -> None:
        """Queue an accepted command for the next simulation tick boundary."""
        async with self._lock:
            if command_id not in self._queued_command_ids:
                self._pending_commands.append((command_id, duty_percent))
                self._queued_command_ids.add(command_id)

    async def _enter_storage_failure(self, message: str) -> None:
        async with self._lock:
            self.storage_error = message
            self.status = RunStatus.PAUSED
        logger.error("storage failure, run paused: %s", message)
        self.bus.publish(
            StreamEventType.RUN_CHANGED,
            {
                "reason": "storage_error",
                "storage_error": message,
                "run_status": RunStatus.PAUSED.value,
            },
            self.run_id,
        )

    def _publish_run_changed(self, reason: str) -> None:
        self.bus.publish(
            StreamEventType.RUN_CHANGED,
            {"reason": reason, **self.run_metadata()},
            self.run_id,
        )

    # ------------------------------------------------------------------ snapshots

    def run_metadata(self) -> dict[str, Any]:
        """Operationally visible run state. Contains no hidden truth."""
        return {
            "run_id": str(self.run_id) if self.run_id else None,
            "status": self.status.value,
            "source": self.source.value,
            "control_revision": self.control_revision,
            "frame_seq": self.frame_seq,
            "sim_time_s": round(self.truth.sim_time_s, 3) if self.run_id else None,
            "sim_epoch_utc": self.sim_epoch_utc.isoformat() if self.run_id else None,
            "config_version": self.config.config_version,
            "model_version": self.config.spacecraft.model_version,
            "storage_error": self.storage_error,
            "tick_seconds": self.settings.sim_tick_seconds,
        }

    def snapshot(self) -> dict[str, Any]:
        """Run metadata plus the most recent committed frame."""
        return {
            "run": self.run_metadata(),
            "frame": self.latest_frame.model_dump(mode="json") if self.latest_frame else None,
        }

    def presenter_state(self) -> dict[str, Any]:
        """Simulator-only. Includes hidden injections and the truth checkpoint."""
        return {
            "run": self.run_metadata(),
            "truth": self.truth.presenter_view() if self.run_id else None,
        }

    # -------------------------------------------------------------------- storage

    def _insert_run(self) -> UUID:
        with session_scope() as db:
            run = Run(
                source=self.source.value,
                seed=self.seed,
                config_version=self.config.config_version,
                model_version=self.config.spacecraft.model_version,
                sim_epoch_utc=self.sim_epoch_utc,
                status=RunStatus.PAUSED.value,
                control_revision=0,
                private_checkpoint=self.truth.to_checkpoint(),
            )
            db.add(run)
            db.flush()
            return run.id

    def _load_latest_run(
        self,
    ) -> tuple[UUID, int, datetime, int, int, dict[str, Any] | None] | None:
        with session_scope() as db:
            run = db.execute(
                select(Run)
                .where(Run.source == SourceKind.LIVE.value, Run.ended_at.is_(None))
                .order_by(Run.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if run is None:
                return None
            last_seq = db.execute(
                select(TelemetryFrameRow.frame_seq)
                .where(TelemetryFrameRow.run_id == run.id)
                .order_by(TelemetryFrameRow.frame_seq.desc())
                .limit(1)
            ).scalar_one_or_none()
            epoch = run.sim_epoch_utc
            if epoch.tzinfo is None:
                epoch = epoch.replace(tzinfo=UTC)
            return (
                run.id,
                run.seed,
                epoch,
                run.control_revision,
                int(last_seq or 0),
                run.private_checkpoint,
            )

    def _run_exists(self, run_id: UUID) -> bool:
        with session_scope() as db:
            return db.get(Run, run_id) is not None

    def _write_run_status(
        self, run_id: UUID, status: RunStatus, control_revision: int | None = None
    ) -> None:
        with session_scope() as db:
            run = db.get(Run, run_id)
            if run is None:
                return
            run.status = status.value
            if control_revision is not None:
                run.control_revision = control_revision

    def _end_run(self, run_id: UUID) -> None:
        with session_scope() as db:
            run = db.get(Run, run_id)
            if run is not None:
                run.status = RunStatus.STOPPED.value
                run.ended_at = datetime.now(UTC)

    def _checkpoint(self, run_id: UUID, truth: SimulatorTruth) -> None:
        with session_scope() as db:
            run = db.get(Run, run_id)
            if run is not None:
                run.private_checkpoint = truth.to_checkpoint()

    def _persist_frame(self, frame: TelemetryFrame, truth: SimulatorTruth) -> None:
        """One short transaction: the frame, plus a periodic private checkpoint."""
        payload = frame.model_dump(mode="json")
        with session_scope() as db:
            db.add(
                TelemetryFrameRow(
                    run_id=frame.run_id,
                    frame_seq=frame.frame_seq,
                    tick_seq=frame.tick_seq,
                    sim_time_s=frame.sim_time_s,
                    received_at_utc=frame.received_at_utc,
                    values=payload["values"],
                    quality=payload["quality"],
                    context={**payload["context"], "engineering": payload["engineering"]},
                )
            )
            # Checkpoint occasionally so a restart resumes close to where it stopped,
            # without writing the whole truth blob every single second.
            if frame.frame_seq % 30 == 0:
                run = db.get(Run, frame.run_id)
                if run is not None:
                    run.private_checkpoint = truth.to_checkpoint()
