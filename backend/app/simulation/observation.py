"""Turn hidden truth into observed telemetry.

This is the boundary the rest of the system lives behind. Everything downstream —
dashboards, rules, forecasts, the AI investigator — reads only what this module emits
plus the known commands in the frame context. It never receives `generation_factor`,
`extra_load_w`, `heater_stuck_on` or the exact node states.

Channels that Phase 2 does not model are reported as `null` with quality `MISSING` and a
reason. They are not filled with plausible numbers, and they are not zero.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import numpy as np

from app.config.loader import SpacecraftConfig, TelemetryConfig
from app.contracts.common import OperatingMode, QualityState, RunStatus, SourceKind
from app.contracts.telemetry import ChannelQuality, ChannelValue, FrameContext, TelemetryFrame

from .models import mass_from_pressure
from .truth import SimulatorTruth
from .twin import StepResult

# Channels with no model behind them yet. Phase 4 adds the flight-systems profiles.
UNMODELLED: dict[str, str] = {
    "cell_voltage_spread": "Cell imbalance profile is not modelled yet.",
    "pointing_error": "Flight-systems profiles arrive in Phase 4.",
    "roll_rate": "Flight-systems profiles arrive in Phase 4.",
    "pitch_rate": "Flight-systems profiles arrive in Phase 4.",
    "yaw_rate": "Flight-systems profiles arrive in Phase 4.",
    "wheel_speed": "Flight-systems profiles arrive in Phase 4.",
    "wheel_current": "Flight-systems profiles arrive in Phase 4.",
    "link_snr": "Contact-window model arrives in Phase 4.",
    "packet_error_rate": "Packet accounting arrives in Phase 4.",
    "computer_reset_count": "Reset events arrive in Phase 4.",
}


class SensorNoise:
    """Seeded, bounded measurement noise.

    Deterministic for a given run seed and tick order, so a scenario replays identically.
    Detection is never given the noise model or the fault-injection times.
    """

    def __init__(self, seed: int) -> None:
        self._rng = np.random.default_rng(seed)

    def add(self, value: float, sigma: float) -> float:
        if sigma <= 0:
            return value
        return float(value + self._rng.normal(0.0, sigma))


def _encodable(telemetry: TelemetryConfig, channel_id: str, value: float) -> float:
    """Clamp a noisy reading into the range the sensor can actually encode.

    A heater power meter cannot report -0.3 W because measurement noise happened to be
    negative; its encoding simply bottoms out at zero. Clamping here keeps `INVALID` for
    what it is meant to flag — a genuinely impossible value from the model or a corrupted
    frame — rather than firing on our own noise every time a channel sits at its limit.
    """
    channel = telemetry.by_id(channel_id)
    if channel is None:
        return value
    low, high = channel.bounds.physical
    if low is not None:
        value = max(low, value)
    if high is not None:
        value = min(high, value)
    return value


def _quality(
    telemetry: TelemetryConfig,
    channel_id: str,
    model_value: float,
    observed: ChannelValue,
    observed_at: datetime,
) -> tuple[ChannelValue, ChannelQuality]:
    """Validate the *model* value against physical bounds.

    The reported observation is preserved as-is; it is never silently corrected to hide a
    model error.
    """
    channel = telemetry.by_id(channel_id)
    if channel is None:
        return observed, ChannelQuality(state=QualityState.VALID, source_time_utc=observed_at)

    low, high = channel.bounds.physical
    if (low is not None and model_value < low) or (high is not None and model_value > high):
        return observed, ChannelQuality(
            state=QualityState.INVALID,
            reason=f"Model value {model_value:.4g} is outside physical bounds [{low}, {high}].",
            source_time_utc=observed_at,
        )
    return observed, ChannelQuality(
        state=QualityState.VALID,
        source_time_utc=observed_at,
        last_valid_value=observed,
        last_valid_time_utc=observed_at,
    )


def observe(
    *,
    config: SpacecraftConfig,
    telemetry: TelemetryConfig,
    truth: SimulatorTruth,
    result: StepResult,
    noise: SensorNoise,
    run_id: UUID,
    frame_seq: int,
    sim_epoch_utc: datetime,
    received_at_utc: datetime,
    run_status: RunStatus,
    control_revision: int,
    source: SourceKind,
    seconds_since_previous_frame: float,
) -> TelemetryFrame:
    """Build one observed frame from the post-step state."""
    sigma = config.observation.noise_sigma
    values: dict[str, ChannelValue] = {}
    quality: dict[str, ChannelQuality] = {}

    def emit(channel_id: str, raw: float, sigma_value: float, digits: int = 3) -> None:
        observed = round(_encodable(telemetry, channel_id, noise.add(raw, sigma_value)), digits)
        value, q = _quality(telemetry, channel_id, raw, observed, received_at_utc)
        values[channel_id] = value
        quality[channel_id] = q

    electrical = result.electrical

    # --- power -----------------------------------------------------------------
    emit("solar_voltage", result.solar_voltage_v, sigma.voltage_v)
    emit("solar_current", result.solar_current_a, sigma.current_a)
    emit("bus_voltage", result.bus_voltage_v, sigma.voltage_v)
    emit("load_power", electrical.requested_load_w, sigma.power_w, digits=2)
    emit("battery_voltage", result.battery_voltage_v, sigma.voltage_v)
    emit("battery_current", result.battery_current_a, sigma.current_a)

    # The state-of-charge estimate is defined on 0-100% by construction; the generic
    # encodable-range clamp keeps it there.
    emit("battery_soc", electrical.soc_percent, sigma.soc_percent, digits=2)

    # --- thermal ---------------------------------------------------------------
    emit("battery_temperature", truth.temperatures_c["battery"], sigma.temperature_c)
    emit("panel_temperature", truth.temperatures_c["panel"], sigma.temperature_c)
    emit("computer_temperature", truth.temperatures_c["computer"], sigma.temperature_c)
    emit("payload_temperature", truth.temperatures_c["payload"], sigma.temperature_c)
    emit("tank_temperature", truth.temperatures_c["tank"], sigma.temperature_c)
    emit("thruster_temperature", truth.temperatures_c["thruster"], sigma.temperature_c)

    # Observed heater activity, held apart from the commanded state in context.
    emit("heater_power", result.heater_served_w, sigma.power_w, digits=2)

    # --- propulsion ------------------------------------------------------------
    emit("tank_pressure", result.tank_pressure_bar, sigma.pressure_bar, digits=4)

    # Mass is DERIVED from the observed pressure and temperature, not weighed. A mass
    # change computed this way is not independent confirmation that gas left the tank.
    observed_pressure = float(values["tank_pressure"] or 0.0)
    observed_tank_c = float(values["tank_temperature"] or truth.temperatures_c["tank"])
    mass_estimate = mass_from_pressure(config.propellant, observed_pressure, observed_tank_c)
    emit("propellant_mass", mass_estimate, 0.0, digits=6)

    values["thruster_valve_open"] = truth.valve_open
    quality["thruster_valve_open"] = ChannelQuality(
        state=QualityState.VALID,
        source_time_utc=received_at_utc,
        last_valid_value=truth.valve_open,
        last_valid_time_utc=received_at_utc,
    )

    # --- ground-derived --------------------------------------------------------
    # The gap since the previous published frame. A client shows *live* age from its own
    # monotonic clock; this stored value is the historical equivalent of the same idea.
    values["telemetry_age"] = round(seconds_since_previous_frame, 3)
    quality["telemetry_age"] = ChannelQuality(
        state=QualityState.VALID,
        reason="Gap since the previous received frame, measured on the ground.",
        source_time_utc=received_at_utc,
    )

    # --- not modelled yet ------------------------------------------------------
    for channel_id, reason in UNMODELLED.items():
        values[channel_id] = None
        quality[channel_id] = ChannelQuality(
            state=QualityState.MISSING, reason=reason, source_time_utc=received_at_utc
        )

    context = FrameContext(
        vehicle_config_version=config.config_version,
        model_version=config.model_version,
        run_id=run_id,
        source=source,
        run_status=run_status,
        mode=OperatingMode(truth.conditions.mode),
        illumination=result.illumination,
        contact_expected=True,
        control_revision=control_revision,
        eclipse_next_change_s=round(result.seconds_to_illumination_change, 1),
        known_commands={
            **truth.conditions.known_commands,
            "heater_commanded_on": result.heater_commanded_on,
        },
    )

    return TelemetryFrame(
        run_id=run_id,
        frame_seq=frame_seq,
        tick_seq=truth.tick_seq,
        sim_time_s=round(truth.sim_time_s, 3),
        sim_epoch_utc=sim_epoch_utc,
        received_at_utc=received_at_utc,
        values=values,
        quality=quality,
        context=context,
        engineering={
            # `load_power` is requested demand. These say what was actually delivered.
            "available_solar_w": round(electrical.available_solar_w, 2),
            "solar_dispatched_w": round(electrical.solar_dispatched_w, 2),
            "curtailed_w": round(electrical.curtailed_w, 2),
            "served_load_w": round(electrical.served_load_w, 2),
            "unmet_load_w": round(electrical.unmet_load_w, 2),
            "charge_bus_w": round(electrical.charge_bus_w, 2),
            "discharge_bus_w": round(electrical.discharge_bus_w, 2),
            "payload_served_w": round(result.payload_served_w, 2),
            "shed_loads": ", ".join(electrical.shed) if electrical.shed else None,
            "computer_powered": result.computer_powered,
        },
    )
