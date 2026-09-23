"""Observed-state estimation boundary for monitoring and forecasts.

This module deliberately has no dependency on ``simulation.truth``.  Every estimate is
derived from valid received channels, public frame context, and nominal configuration.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config.loader import SpacecraftConfig
from app.contracts.common import Illumination
from app.contracts.telemetry import TelemetryFrame


@dataclass(frozen=True)
class ObservedStateEstimate:
    run_id: object
    frame_seq: int
    sim_time_s: float
    control_revision: int
    energy_wh: float
    load_w: float
    solar_w: float
    generation_factor: float
    battery_current_a: float
    battery_temperature_c: float
    payload_temperature_c: float
    heater_power_w: float
    payload_duty_percent: float
    illumination: Illumination


def estimate_observed_state(
    frame: TelemetryFrame, config: SpacecraftConfig
) -> ObservedStateEstimate | None:
    """Return an immutable estimate, or ``None`` when required evidence is invalid."""
    required = {
        name: frame.value_if_valid(name)
        for name in (
            "battery_soc",
            "load_power",
            "solar_voltage",
            "solar_current",
            "battery_current",
            "battery_temperature",
            "payload_temperature",
            "heater_power",
        )
    }
    if any(value is None or isinstance(value, bool) for value in required.values()):
        return None
    solar_w = max(0.0, float(required["solar_voltage"]) * float(required["solar_current"]))
    generation_factor = (
        min(1.5, solar_w / config.power.solar_rated_w)
        if frame.context.illumination is Illumination.SUNLIGHT
        else 1.0
    )
    duty = float(frame.context.known_commands.get("payload_duty_percent", 100.0))
    return ObservedStateEstimate(
        run_id=frame.run_id,
        frame_seq=frame.frame_seq,
        sim_time_s=frame.sim_time_s,
        control_revision=frame.context.control_revision,
        energy_wh=float(required["battery_soc"]) * config.power.battery_capacity_wh / 100.0,
        load_w=float(required["load_power"]),
        solar_w=solar_w,
        generation_factor=generation_factor,
        battery_current_a=float(required["battery_current"]),
        battery_temperature_c=float(required["battery_temperature"]),
        payload_temperature_c=float(required["payload_temperature"]),
        heater_power_w=float(required["heater_power"]),
        payload_duty_percent=duty,
        illumination=frame.context.illumination,
    )
