"""Phase 3 coordinator: observed estimates, incidents, forecasts and recovery actions."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.config.loader import VehicleConfiguration
from app.contracts.actions import (
    ActionProposal,
    CommandRecord,
    CommandStatus,
    RecoveryOutcome,
)
from app.contracts.incidents import Acknowledgement, Incident
from app.contracts.telemetry import StreamEventType, TelemetryFrame
from app.forecasts.engine import forecast
from app.forecasts.estimator import ObservedStateEstimate, estimate_observed_state
from app.monitoring.engine import IncidentEngine
from app.storage.db import session_scope
from app.storage.models import CommandRow, ForecastRow, IncidentEventRow, IncidentRow

logger = logging.getLogger(__name__)


class OperationalService:
    def __init__(self, config: VehicleConfiguration, bus: Any, analysis: Any = None) -> None:
        self.config = config
        self.bus = bus
        self.analysis = analysis
        self.incident_engine = IncidentEngine(config)
        self.latest_estimate: ObservedStateEstimate | None = None
        self.latest_forecast = None
        self.proposals: dict[UUID, ActionProposal] = {}
        self.commands: dict[UUID, CommandRecord] = {}
        self._last_forecast_frame = 0
        self._command_start_load: dict[UUID, tuple[int, float]] = {}
        self._latest_frame: TelemetryFrame | None = None

    async def process_staleness(self, sim_time_s: float) -> None:
        if self._latest_frame is None:
            return
        age_s = max(0.0, sim_time_s - self._latest_frame.sim_time_s)
        incident = self.incident_engine.evaluate_stale(
            self._latest_frame.model_copy(update={"sim_time_s": sim_time_s}), age_s
        )
        if incident is not None:
            change = await asyncio.to_thread(self._persist_incident, incident)
            self.bus.publish(
                StreamEventType.INCIDENT, incident.model_dump(mode="json"), incident.run_id
            )
            await self._enqueue_incident_change(incident.id, change)

    async def process_frame(self, frame: TelemetryFrame) -> None:
        self._latest_frame = frame
        estimate = estimate_observed_state(frame, self.config.spacecraft)
        self.latest_estimate = estimate
        if estimate and (
            self.latest_forecast is None or frame.frame_seq - self._last_forecast_frame >= 5
        ):
            result = await asyncio.to_thread(forecast, estimate, self.config.spacecraft)
            self.latest_forecast = result
            self._last_forecast_frame = frame.frame_seq
            await asyncio.to_thread(self._persist_forecast, result)
            self.bus.publish(StreamEventType.FORECAST, result.model_dump(mode="json"), frame.run_id)
        forecast_crossing = bool(self.latest_forecast and self.latest_forecast.crossings)
        changed = self.incident_engine.evaluate(frame, forecast_crossing)
        for incident in changed:
            change = await asyncio.to_thread(self._persist_incident, incident)
            self.bus.publish(
                StreamEventType.INCIDENT, incident.model_dump(mode="json"), frame.run_id
            )
            await self._enqueue_incident_change(incident.id, change)
        failed = await asyncio.to_thread(self._observe_command_outcomes, frame)
        for incident_id in failed:
            await self._enqueue_safely(incident_id, "FAILED_RECOVERY")

    def list_incidents(self, run_id: UUID | None) -> list[Incident]:
        return sorted(
            (
                i
                for i in self.incident_engine.incidents.values()
                if run_id is None or i.run_id == run_id
            ),
            key=lambda i: i.last_seen_utc,
            reverse=True,
        )

    def acknowledge(self, incident_id: UUID, username: str) -> Incident:
        incident = self.incident_engine.incidents[incident_id]
        ack = Acknowledgement(
            acknowledged=True, acknowledged_by=username, acknowledged_at_utc=datetime.now(UTC)
        )
        updated = incident.model_copy(update={"acknowledgement": ack})
        self.incident_engine.incidents[incident_id] = updated
        self._persist_incident(updated)
        return updated

    async def compare(
        self, incident_id: UUID, duty: float = 0.0, action_id: str = "REDUCE_PAYLOAD_POWER"
    ) -> dict[str, Any]:
        """Baseline against one implemented alternative, from the same observed start.

        Both projections run from the identical estimate and the identical external
        schedule, so neither option is given privileged knowledge of the injected fault.
        Only actions marked enabled in actions.yaml may be offered.
        """
        if incident_id not in self.incident_engine.incidents:
            raise KeyError(incident_id)
        if action_id not in self.config.actions.enabled_ids:
            raise ValueError(f"{action_id} is not an enabled recovery action.")
        estimate = self.latest_estimate
        if estimate is None:
            raise ValueError("No valid observed state is available for comparison.")

        isolate = action_id == "ISOLATE_OPTIONAL_BRANCH"
        branch = self.config.spacecraft.power.optional_branch
        baseline, action = await asyncio.gather(
            asyncio.to_thread(forecast, estimate, self.config.spacecraft),
            asyncio.to_thread(
                forecast,
                estimate,
                self.config.spacecraft,
                payload_duty_percent=None if isolate else duty,
                isolate_branch=isolate,
            ),
        )
        await asyncio.to_thread(self._persist_forecast, baseline)
        await asyncio.to_thread(self._persist_forecast, action)
        now = datetime.now(UTC)
        proposal = ActionProposal(
            id=uuid4(),
            run_id=estimate.run_id,
            incident_id=incident_id,
            action_id=action_id,
            action_arguments=({"branch_id": branch.id} if isolate else {"duty_percent": duty}),
            evidence_frame_seq=estimate.frame_seq,
            control_revision=estimate.control_revision,
            created_at_utc=now,
            expires_at_utc=now + timedelta(seconds=30),
            baseline_forecast_id=baseline.id,
            action_forecast_id=action.id,
            input_validity={"battery_soc": (0.0, 100.0)},
            outcome_paths=(
                (
                    f"Measured load falls by about {branch.rated_w:.0f} W if the isolator responds."
                    if isolate
                    else "Measured load falls after application."
                ),
                "Reserve projection improves or remains unchanged.",
                (
                    "If the isolator does not respond, the command still applies and the "
                    "outcome is reported as no change."
                    if isolate
                    else "If demand does not fall, the outcome is reported as no change."
                ),
            ),
            trade_off=(
                (
                    f"Isolating branch {branch.id} removes its load and its heat. It does "
                    "not repair the branch: the hardware stays failed and its function is "
                    "unavailable until ground intervention. Essential power cannot be "
                    "isolated."
                )
                if isolate
                else "Reducing payload duty delays configured mission work."
            ),
        )
        self.proposals[proposal.id] = proposal
        return {
            "baseline": baseline.model_dump(mode="json"),
            "action": action.model_dump(mode="json"),
            "proposal": proposal.model_dump(mode="json"),
        }

    def accept_command(self, proposal_id: UUID, actor: str, idempotency_key: str) -> CommandRecord:
        proposal = self.proposals.get(proposal_id)
        if proposal is None:
            raise KeyError(proposal_id)
        existing = next(
            (
                c
                for c in self.commands.values()
                if c.run_id == proposal.run_id and c.idempotency_key == idempotency_key
            ),
            None,
        )
        if existing:
            return existing
        if proposal.expires_at_utc < datetime.now(UTC):
            raise TimeoutError("The proposal has expired; compare again.")
        estimate = self.latest_estimate
        if (
            estimate is None
            or estimate.run_id != proposal.run_id
            or estimate.control_revision != proposal.control_revision
        ):
            raise RuntimeError("The run controls changed; compare again before approval.")
        record = CommandRecord(
            id=uuid4(),
            run_id=proposal.run_id,
            proposal_id=proposal.id,
            action_id=proposal.action_id,
            action_arguments=proposal.action_arguments,
            actor_username=actor,
            idempotency_key=idempotency_key,
            status=CommandStatus.ACCEPTED,
            created_at_utc=datetime.now(UTC),
        )
        self.commands[record.id] = record
        self._command_start_load[record.id] = (0, estimate.load_w)
        self._persist_command(record)
        return record

    def mark_applied(self, command_id: UUID, tick_seq: int, load_w: float) -> None:
        record = self.commands[command_id]
        updated = record.model_copy(
            update={
                "status": CommandStatus.APPLIED,
                "applied_at_utc": datetime.now(UTC),
                "applied_tick_seq": tick_seq,
            }
        )
        self.commands[command_id] = updated
        _, before = self._command_start_load.get(command_id, (0, load_w))
        self._command_start_load[command_id] = (tick_seq, before)
        self._persist_command(updated)
        self.bus.publish(StreamEventType.COMMAND, updated.model_dump(mode="json"), updated.run_id)

    def _observe_command_outcomes(self, frame: TelemetryFrame) -> list[UUID]:
        failed: list[UUID] = []
        load = frame.value_if_valid("load_power")
        if not isinstance(load, (int, float)):
            return failed
        for cid, (tick, before) in list(self._command_start_load.items()):
            if frame.tick_seq - tick < 3:
                continue
            record = self.commands[cid]
            improved = float(load) < before - 10.0
            updated = record.model_copy(
                update={
                    "observed_outcome": RecoveryOutcome.IMPROVED
                    if improved
                    else RecoveryOutcome.NO_CHANGE,
                    "outcome_detail": (
                        f"Observed requested load changed from {before:.1f} W "
                        f"to {float(load):.1f} W."
                    ),
                }
            )
            self.commands[cid] = updated
            self._persist_command(updated)
            self.bus.publish(
                StreamEventType.COMMAND, updated.model_dump(mode="json"), updated.run_id
            )
            proposal = self.proposals.get(updated.proposal_id) if updated.proposal_id else None
            if not improved and proposal and proposal.incident_id:
                failed.append(proposal.incident_id)
            del self._command_start_load[cid]
        return failed

    def _persist_incident(self, i: Incident) -> tuple[str, ...]:
        payload = i.model_dump(mode="json")
        changes: list[str] = []
        with session_scope() as db:
            row = db.get(IncidentRow, i.id)
            if row is None:
                row = IncidentRow(
                    id=i.id,
                    run_id=i.run_id,
                    rule_id=i.rule_id,
                    rule_version=i.rule_version,
                    family=i.family,
                    subsystem=i.subsystem.value,
                    affected_entity=i.affected_entity,
                    affected_groups=list(i.affected_groups),
                    severity=i.severity.value,
                    lifecycle=i.lifecycle.value,
                    first_seen_utc=i.first_seen_utc,
                    last_seen_utc=i.last_seen_utc,
                    evidence=payload,
                )
                db.add(row)
                changes.append("INCIDENT_OPENED")
                # The event model has only the scalar foreign key (no ORM relationship),
                # so establish the parent row before its first timeline entry.
                db.flush()
                event = i.timeline[0] if i.timeline else None
                db.add(
                    IncidentEventRow(
                        incident_id=i.id,
                        at_utc=event.at_utc if event else i.first_seen_utc,
                        kind=event.kind if event else "OPENED",
                        detail=event.detail if event else f"{i.rule_id} incident opened.",
                        severity=i.severity.value,
                        lifecycle=i.lifecycle.value,
                        evidence_ids=list(event.evidence_ids) if event else [],
                    )
                )
            else:
                old_severity = row.severity
                old_lifecycle = row.lifecycle
                old_acknowledged = row.acknowledged
                row.severity, row.lifecycle, row.last_seen_utc, row.evidence = (
                    i.severity.value,
                    i.lifecycle.value,
                    i.last_seen_utc,
                    payload,
                )
                row.acknowledged = i.acknowledgement.acknowledged
                row.acknowledged_by = i.acknowledgement.acknowledged_by
                row.acknowledged_at_utc = i.acknowledgement.acknowledged_at_utc
                row.evidence_revision += 1
                evidence_ids = [item.evidence_id for item in i.observed_evidence[-3:]]
                if old_severity != i.severity.value:
                    rank = {"UNKNOWN": 0, "INFO": 1, "WARNING": 2, "CRITICAL": 3}
                    if rank.get(i.severity.value, 0) > rank.get(old_severity, 0):
                        changes.append("SEVERITY_ESCALATED")
                    db.add(
                        IncidentEventRow(
                            incident_id=i.id,
                            at_utc=i.last_seen_utc,
                            kind="SEVERITY_CHANGED",
                            detail=f"Severity changed from {old_severity} to {i.severity.value}.",
                            severity=i.severity.value,
                            lifecycle=i.lifecycle.value,
                            evidence_ids=evidence_ids,
                        )
                    )
                if old_lifecycle != i.lifecycle.value:
                    db.add(
                        IncidentEventRow(
                            incident_id=i.id,
                            at_utc=i.last_seen_utc,
                            kind="LIFECYCLE_CHANGED",
                            detail=(
                                f"Lifecycle changed from {old_lifecycle} to {i.lifecycle.value}."
                            ),
                            severity=i.severity.value,
                            lifecycle=i.lifecycle.value,
                            evidence_ids=evidence_ids,
                        )
                    )
                if not old_acknowledged and i.acknowledgement.acknowledged:
                    db.add(
                        IncidentEventRow(
                            incident_id=i.id,
                            at_utc=i.acknowledgement.acknowledged_at_utc or i.last_seen_utc,
                            kind="ACKNOWLEDGED",
                            detail=f"Acknowledged by {i.acknowledgement.acknowledged_by}.",
                            severity=i.severity.value,
                            lifecycle=i.lifecycle.value,
                            evidence_ids=evidence_ids,
                        )
                    )
        return tuple(changes)

    async def _enqueue_incident_change(self, incident_id: UUID, changes: tuple[str, ...]) -> None:
        """Hand an incident to the investigation queue without letting it affect the loop.

        Two rules from section 1.3 apply here. The enqueue opens a database session, so
        it runs in a worker thread rather than on the event loop that also serves SSE.
        And an AI-path failure must never delay or disable a deterministic warning, so
        every exception is logged and swallowed: the incident has already been persisted
        and published by the time this runs.
        """
        if not self.analysis:
            return
        for trigger in changes:
            await self._enqueue_safely(incident_id, trigger)

    async def _enqueue_safely(self, incident_id: UUID, trigger: str) -> None:
        if not self.analysis:
            return
        try:
            await asyncio.to_thread(self.analysis.enqueue_incident, incident_id, trigger)
        except Exception:  # noqa: BLE001 - the investigator must never break the live loop
            logger.exception(
                "investigation enqueue failed for incident %s (%s); "
                "telemetry and deterministic alerts are unaffected",
                incident_id,
                trigger,
            )

    def _persist_forecast(self, f: Any) -> None:
        with session_scope() as db:
            db.add(
                ForecastRow(
                    id=f.id,
                    run_id=f.run_id,
                    source_frame_seq=f.source_frame_seq,
                    source_sim_time_s=f.source_sim_time_s,
                    generated_at_utc=f.generated_at_utc,
                    model_version=f.model_version,
                    config_version=f.config_version,
                    horizon_s=f.horizon_s,
                    coverage=f.coverage.value,
                    action_id=f.action_id,
                    action_arguments=f.action_arguments,
                    assumptions=list(f.assumptions),
                    series=[s.model_dump(mode="json") for s in f.series],
                    crossings=[c.model_dump(mode="json") for c in f.crossings],
                )
            )

    def _persist_command(self, c: CommandRecord) -> None:
        with session_scope() as db:
            row = db.get(CommandRow, c.id)
            previous_status = row.status if row else None
            previous_outcome = row.observed_outcome if row else None
            if row is None:
                row = CommandRow(
                    id=c.id,
                    run_id=c.run_id,
                    actor_username=c.actor_username,
                    idempotency_key=c.idempotency_key,
                    proposal_id=c.proposal_id,
                    action_id=c.action_id,
                    action_arguments=c.action_arguments,
                    status=c.status.value,
                    status_reason=c.status_reason,
                    created_at=c.created_at_utc,
                    observed_outcome=c.observed_outcome.value,
                )
                db.add(row)
            else:
                row.status, row.applied_at_utc, row.applied_tick_seq = (
                    c.status.value,
                    c.applied_at_utc,
                    c.applied_tick_seq,
                )
                row.observed_outcome, row.outcome_detail = (
                    c.observed_outcome.value,
                    c.outcome_detail,
                )
            proposal = self.proposals.get(c.proposal_id) if c.proposal_id else None
            if proposal and proposal.incident_id:
                evidence_ids = [f"command:{c.id}"]
                if previous_status != c.status.value:
                    db.add(
                        IncidentEventRow(
                            incident_id=proposal.incident_id,
                            at_utc=c.applied_at_utc or c.created_at_utc,
                            kind="COMMAND_STATUS",
                            detail=f"{c.action_id} command status is {c.status.value}.",
                            lifecycle=None,
                            severity=None,
                            evidence_ids=evidence_ids,
                        )
                    )
                if (
                    previous_outcome != c.observed_outcome.value
                    and c.observed_outcome.value != "PENDING_OBSERVATION"
                ):
                    incident_row = db.get(IncidentRow, proposal.incident_id)
                    if incident_row is not None:
                        incident_row.evidence_revision += 1
                    db.add(
                        IncidentEventRow(
                            incident_id=proposal.incident_id,
                            at_utc=datetime.now(UTC),
                            kind="COMMAND_OUTCOME",
                            detail=c.outcome_detail
                            or f"{c.action_id} outcome is {c.observed_outcome.value}.",
                            lifecycle=None,
                            severity=None,
                            evidence_ids=evidence_ids,
                        )
                    )
