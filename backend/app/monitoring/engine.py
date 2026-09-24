"""Observation-only rules and deduplicated incident lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from app.config.loader import VehicleConfiguration
from app.contracts.common import Subsystem
from app.contracts.incidents import EvidenceItem, Incident, IncidentEvent, Lifecycle, Severity
from app.contracts.telemetry import TelemetryFrame


@dataclass
class _RuleState:
    first_bad_sim_s: float | None = None
    healthy_since_sim_s: float | None = None


def _distinct_event_times(raw: object) -> set[float]:
    """Parse the observed reset-event times.

    Published as a short comma-separated label in engineering detail, which is why this
    accepts a string. A set is returned so a repeated timestamp counts once: "distinct
    reset events" is the rule's own wording, and two samples of one event are not two
    events.
    """
    if not isinstance(raw, str) or not raw.strip():
        return set()
    times: set[float] = set()
    for part in raw.split(","):
        try:
            times.add(float(part.strip()))
        except ValueError:
            continue
    return times


@dataclass
class IncidentEngine:
    config: VehicleConfiguration
    incidents: dict[UUID, Incident] = field(default_factory=dict)
    _states: dict[tuple[UUID, str, str], _RuleState] = field(default_factory=dict)

    @property
    def _hysteresis_c(self) -> float:
        rule = self.config.rules.by_id("THERMAL_LIMIT")
        return float((rule.params if rule else {}).get("hysteresis_c", 0.0))

    def _active(self, run_id: UUID, family: str, entity: str) -> Incident | None:
        return next(
            (
                i
                for i in self.incidents.values()
                if i.dedup_key == (str(run_id), family, entity) and i.is_active
            ),
            None,
        )

    def evaluate(self, frame: TelemetryFrame, forecast_crossing: bool = False) -> list[Incident]:
        signals: list[
            tuple[str, str, str, Subsystem, Severity, bool, str, str, float | None, str | None]
        ] = []
        soc = frame.value_if_valid("battery_soc")
        bus = frame.value_if_valid("bus_voltage")
        load = frame.value_if_valid("load_power")
        solar_v = frame.value_if_valid("solar_voltage")
        solar_a = frame.value_if_valid("solar_current")
        batt_a = frame.value_if_valid("battery_current")
        unmet = frame.engineering.get("unmet_load_w")

        if isinstance(soc, (int, float)):
            critical = soc <= self.config.spacecraft.power.soc_critical_percent
            bad = soc <= self.config.spacecraft.power.soc_warning_percent or forecast_crossing
            signals.append(
                (
                    "ENERGY_RESERVE",
                    "POWER_ENERGY",
                    "battery",
                    Subsystem.POWER,
                    Severity.CRITICAL if critical else Severity.WARNING,
                    bad,
                    "Battery reserve is below or projected to cross the configured reserve.",
                    "battery_soc",
                    float(soc),
                    "%",
                )
            )
        if all(isinstance(v, (int, float)) for v in (solar_v, solar_a, batt_a, load)):
            observed_solar = float(solar_v) * float(solar_a)
            expected = self.config.spacecraft.power.solar_rated_w
            bad = (
                frame.context.illumination.value == "SUNLIGHT"
                and observed_solar < 0.8 * expected
                and float(batt_a) > 0
                and float(load) > 20
            )
            signals.append(
                (
                    "GENERATION_SHORTFALL",
                    "POWER_GENERATION",
                    "solar_array",
                    Subsystem.POWER,
                    Severity.WARNING,
                    bad,
                    "Observed sunlit generation is below nominal while the battery discharges.",
                    "solar_current",
                    float(solar_a),
                    "A",
                )
            )
        if isinstance(bus, (int, float)):
            bad = float(bus) < 26.0 or isinstance(unmet, (int, float)) and float(unmet) > 0.5
            signals.append(
                (
                    "SUPPLY_SHORTAGE",
                    "POWER_SUPPLY",
                    "power_bus",
                    Subsystem.POWER,
                    Severity.CRITICAL,
                    bad,
                    "Bus voltage or served-load evidence indicates a supply shortage.",
                    "bus_voltage",
                    float(bus),
                    "V",
                )
            )
        # ACTUATOR_MISMATCH: what the ground commanded against what it observes.
        # Both sides are legitimately available — the commanded thermostat state is a
        # known command in frame context, and heater power is a measured channel — so
        # this needs no access to simulator truth. It reports a discrepancy; it does not
        # claim to know whether the actuator, its sensor or the command path is at fault.
        heater_w = frame.value_if_valid("heater_power")
        commanded_on = frame.context.known_commands.get("heater_commanded_on")
        rule = self.config.rules.by_id("ACTUATOR_MISMATCH")
        if isinstance(heater_w, (int, float)) and isinstance(commanded_on, bool) and rule:
            tolerance = float(rule.params.get("power_tolerance_w", 5.0))
            rated = self.config.spacecraft.power.heater_rated_w
            observed_on = float(heater_w) > tolerance
            bad = observed_on != commanded_on
            detail = (
                f"Heater commanded {'ON' if commanded_on else 'OFF'} but drawing "
                f"{float(heater_w):.1f} W against a rated {rated:.0f} W."
            )
            signals.append(
                (
                    "ACTUATOR_MISMATCH",
                    "COMMAND_RESPONSE",
                    "battery_heater",
                    Subsystem.THERMAL_PROPULSION,
                    Severity.WARNING,
                    bad,
                    detail,
                    "heater_power",
                    float(heater_w),
                    "W",
                )
            )

        for channel in (
            "battery_temperature",
            "payload_temperature",
            "computer_temperature",
            "panel_temperature",
        ):
            value = frame.value_if_valid(channel)
            meta = self.config.telemetry.by_id(channel)
            if isinstance(value, (int, float)) and meta:
                lo, hi = meta.bounds.operating
                # Section 8.2: the recovery boundary sits inside the warning boundary, so
                # a temperature hovering on its limit does not re-cross on every sample
                # and flap the incident between OPEN and RECOVERING. The band only
                # narrows the limits while an incident is already active.
                band = self._hysteresis_c if self._active(frame.run_id, "THERMAL", channel) else 0.0
                lo_limit = None if lo is None else lo + band
                hi_limit = None if hi is None else hi - band
                bad = (lo_limit is not None and value < lo_limit) or (
                    hi_limit is not None and value > hi_limit
                )
                signals.append(
                    (
                        "THERMAL_LIMIT",
                        "THERMAL",
                        channel,
                        Subsystem.THERMAL_PROPULSION,
                        Severity.WARNING,
                        bad,
                        f"{channel} is outside its configured operating range.",
                        channel,
                        float(value),
                        meta.unit,
                    )
                )

        # RESET_RECURRENCE (section 8.1): distinct reset events inside the configured
        # window. Both inputs are observed: the cumulative counter is a channel, and the
        # event times are published in the frame's engineering detail, which operators
        # already read. The hidden `computer_resets` injection is never consulted — the
        # rule must be derivable by a ground team from telemetry alone.
        reset_rule = self.config.rules.by_id("RESET_RECURRENCE")
        reset_params = reset_rule.params if reset_rule else {}
        window_s = float(reset_params.get("window_s", 900.0))
        min_events = int(reset_params.get("min_events", 2))
        reset_count = frame.value_if_valid("computer_reset_count")
        # A counter that is not VALID means the evidence is absent. Absent evidence is
        # neither a reset nor a clean bill of health, so the rule abstains entirely
        # rather than feeding a healthy sample into the dwell timer.
        if isinstance(reset_count, (int, float)):
            events = _distinct_event_times(frame.engineering.get("reset_events_s"))
            recent = sorted(t for t in events if 0.0 <= frame.sim_time_s - t <= window_s)
            bad = len(recent) >= min_events
            if recent:
                when = ", ".join(f"{t:.0f}" for t in recent)
                detail = (
                    f"{len(recent)} distinct computer reset events within the last "
                    f"{window_s:.0f} s at spacecraft times {when} s."
                )
            else:
                detail = "No reset events within the configured window."
            signals.append(
                (
                    "RESET_RECURRENCE",
                    "AVIONICS",
                    "computer",
                    Subsystem.FLIGHT_SYSTEMS,
                    Severity.WARNING,
                    bad,
                    detail,
                    "computer_reset_count",
                    float(reset_count),
                    self.config.telemetry.by_id("computer_reset_count").unit
                    if self.config.telemetry.by_id("computer_reset_count")
                    else None,
                )
            )

        changed: list[Incident] = []
        recovered = self._apply(
            frame,
            "DATA_STALE",
            "DATA_AVAILABILITY",
            "telemetry",
            Subsystem.FLIGHT_SYSTEMS,
            Severity.WARNING,
            False,
            "Fresh telemetry resumed during expected contact.",
            "telemetry_age",
            0.0,
            "s",
        )
        if recovered is not None:
            changed.append(recovered)
        for signal in signals:
            updated = self._apply(frame, *signal)
            if updated is not None:
                changed.append(updated)
        return changed

    def evaluate_stale(self, frame: TelemetryFrame, age_s: float) -> Incident | None:
        """Evaluate receipt age while the spacecraft delivers no new frame."""
        severity = Severity.CRITICAL if age_s >= 10.0 else Severity.WARNING
        return self._apply(
            frame,
            "DATA_STALE",
            "DATA_AVAILABILITY",
            "telemetry",
            Subsystem.FLIGHT_SYSTEMS,
            severity,
            age_s >= 3.0 and frame.context.contact_expected,
            f"No valid frame for {age_s:.1f} seconds during expected contact.",
            "telemetry_age",
            age_s,
            "s",
        )

    def _apply(
        self,
        frame: TelemetryFrame,
        rule_id: str,
        family: str,
        entity: str,
        subsystem: Subsystem,
        severity: Severity,
        bad: bool,
        text: str,
        channel: str,
        value: float | None,
        unit: str | None,
    ) -> Incident | None:
        key = (frame.run_id, family, entity)
        state = self._states.setdefault(key, _RuleState())
        active = self._active(frame.run_id, family, entity)
        rule = self.config.rules.by_id(rule_id)
        persistence = float(
            (rule.params if rule else {}).get(
                "persistence_s", self.config.rules.defaults.get("persistence_s", 3)
            )
        )
        dwell = float(self.config.rules.defaults.get("healthy_dwell_s", 10))
        now = frame.received_at_utc
        if bad:
            state.healthy_since_sim_s = None
            state.first_bad_sim_s = (
                frame.sim_time_s if state.first_bad_sim_s is None else state.first_bad_sim_s
            )
            if frame.sim_time_s - state.first_bad_sim_s < persistence and active is None:
                return None
            evidence = EvidenceItem(
                text=text,
                evidence_id=f"frame:{frame.frame_seq}",
                channel_id=channel,
                value=value,
                unit=unit,
                observed_at_utc=now,
            )
            if active is None:
                incident = Incident(
                    id=uuid4(),
                    run_id=frame.run_id,
                    rule_id=rule_id,
                    rule_version=self.config.rules.config_version,
                    family=family,
                    subsystem=subsystem,
                    affected_groups=(subsystem.value,),
                    affected_entity=entity,
                    severity=severity,
                    lifecycle=Lifecycle.OPEN,
                    first_seen_utc=now,
                    last_seen_utc=now,
                    source_frames=(frame.frame_seq,),
                    observed_evidence=(evidence,),
                    suspected_causes=(
                        "Cause not established; investigate from observed evidence.",
                    ),
                    timeline=(
                        IncidentEvent(
                            at_utc=now,
                            kind="OPENED",
                            detail=text,
                            severity=severity,
                            lifecycle=Lifecycle.OPEN,
                            evidence_ids=(evidence.evidence_id,),
                        ),
                    ),
                )
            else:
                incident = active.model_copy(
                    update={
                        "last_seen_utc": now,
                        "severity": severity,
                        "lifecycle": Lifecycle.OPEN,
                        "source_frames": (*active.source_frames[-19:], frame.frame_seq),
                        "observed_evidence": (*active.observed_evidence[-9:], evidence),
                    }
                )
            self.incidents[incident.id] = incident
            return incident
        state.first_bad_sim_s = None
        if active is None:
            return None
        state.healthy_since_sim_s = (
            frame.sim_time_s if state.healthy_since_sim_s is None else state.healthy_since_sim_s
        )
        lifecycle = (
            Lifecycle.RESOLVED
            if frame.sim_time_s - state.healthy_since_sim_s >= dwell
            else Lifecycle.RECOVERING
        )
        if lifecycle == active.lifecycle:
            return None
        incident = active.model_copy(update={"last_seen_utc": now, "lifecycle": lifecycle})
        self.incidents[incident.id] = incident
        return incident
