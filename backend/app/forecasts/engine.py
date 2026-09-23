"""Deterministic power forecast from an observed-state estimate."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.config.loader import SpacecraftConfig
from app.contracts.actions import (
    CrossingKind,
    ForecastCoverage,
    ForecastResult,
    ForecastSeries,
    LimitCrossing,
)
from app.simulation import environment, models
from app.simulation.models import LoadRequest

from .estimator import ObservedStateEstimate


def forecast(
    estimate: ObservedStateEstimate,
    config: SpacecraftConfig,
    *,
    payload_duty_percent: float | None = None,
    isolate_branch: bool = False,
) -> ForecastResult:
    """Project 30 minutes without access to mutable simulator state or hidden fields.

    `isolate_branch` projects the effect of opening the optional branch's isolator. The
    branch's rated power is a configured vehicle parameter the ground legitimately knows,
    so subtracting it is a nominal-parameter projection, not a peek at hidden truth. It
    assumes the isolator responds — which is exactly why the outcome is judged afterwards
    from measured telemetry rather than assumed from the projection.
    """
    horizon = config.simulation.forecast_horizon_s
    step_s = 10.0
    duty = estimate.payload_duty_percent if payload_duty_percent is None else payload_duty_percent
    observed_payload_w = config.power.payload_nominal_w * estimate.payload_duty_percent / 100.0
    residual_w = max(0.0, estimate.load_w - observed_payload_w)
    projected_load_w = residual_w + config.power.payload_nominal_w * duty / 100.0
    if isolate_branch:
        projected_load_w = max(0.0, projected_load_w - config.power.optional_branch.rated_w)
    energy = estimate.energy_wh
    times = [estimate.sim_time_s]
    socs = [100.0 * energy / config.power.battery_capacity_wh]
    battery_temp = estimate.battery_temperature_c
    battery_temps = [battery_temp]
    crossings: list[LimitCrossing] = []
    warning_seen = socs[0] <= config.power.soc_warning_percent
    critical_seen = socs[0] <= config.power.soc_critical_percent

    elapsed = 0.0
    while elapsed < horizon:
        dt = min(step_s, horizon - elapsed)
        sim_t = estimate.sim_time_s + elapsed
        env = environment.evaluate(
            config.environment,
            sim_t,
            config.thermal.boundary_sunlight_c,
            config.thermal.boundary_eclipse_c,
        )
        result = models.step_electrical(
            power=config.power,
            energy_wh=energy,
            load=LoadRequest(
                essential_w=projected_load_w,
                payload_w=0.0,
                heater_w=0.0,
            ),
            illumination_factor=env.illumination_factor,
            generation_factor=estimate.generation_factor,
            dt_s=dt,
        )
        energy = result.next_energy_wh
        _, projected_current_a = models.battery_terminal(
            config.power, energy, result.battery_power_w
        )
        battery_node = config.thermal.nodes["battery"]
        battery_heat_w = (
            projected_current_a**2 * config.power.battery_series_resistance_ohm
            + estimate.heater_power_w
        )
        battery_temp = models.step_node(
            battery_node.heat_capacity_j_per_k,
            battery_node.conductance_w_per_k,
            battery_temp,
            env.boundary_c,
            battery_heat_w,
            dt,
        )
        elapsed += dt
        soc = 100.0 * energy / config.power.battery_capacity_wh
        times.append(estimate.sim_time_s + elapsed)
        socs.append(soc)
        battery_temps.append(battery_temp)
        for kind, limit, seen in (
            (CrossingKind.SOC_WARNING, config.power.soc_warning_percent, warning_seen),
            (CrossingKind.SOC_CRITICAL, config.power.soc_critical_percent, critical_seen),
        ):
            if not seen and soc <= limit:
                crossings.append(
                    LimitCrossing(
                        kind=kind,
                        channel_id="battery_soc",
                        limit_value=limit,
                        unit="%",
                        sim_time_s=estimate.sim_time_s + elapsed,
                        seconds_from_source=elapsed,
                    )
                )
                if kind is CrossingKind.SOC_WARNING:
                    warning_seen = True
                else:
                    critical_seen = True

    return ForecastResult(
        id=uuid4(),
        run_id=estimate.run_id,
        source_frame_seq=estimate.frame_seq,
        source_sim_time_s=estimate.sim_time_s,
        generated_at_utc=datetime.now(UTC),
        model_version=config.model_version,
        config_version=config.config_version,
        horizon_s=horizon,
        coverage=ForecastCoverage.PARTIAL,
        coverage_reasons=(
            "Power, battery reserve, and the selected battery thermal node are modelled.",
        ),
        assumptions=(
            "Future illumination follows the configured demonstration schedule.",
            "Observed non-payload demand and estimated sunlit generation persist.",
            "No future injected conditions or unknown commands are assumed.",
        )
        + (
            (
                "Assumes the optional branch isolator responds to the command; the "
                "actual outcome is judged from measured telemetry afterwards.",
            )
            if isolate_branch
            else ()
        ),
        limitations=("This is a fictional test-vehicle projection, not a calibrated probability.",),
        series=(
            ForecastSeries(
                channel_id="battery_soc", unit="%", times_s=tuple(times), values=tuple(socs)
            ),
            ForecastSeries(
                channel_id="battery_temperature",
                unit="degC",
                times_s=tuple(times),
                values=tuple(battery_temps),
            ),
        ),
        crossings=tuple(crossings),
        action_id=(
            "ISOLATE_OPTIONAL_BRANCH"
            if isolate_branch
            else ("REDUCE_PAYLOAD_POWER" if payload_duty_percent is not None else None)
        ),
        action_arguments=(
            {"branch_id": config.power.optional_branch.id}
            if isolate_branch
            else ({"duty_percent": duty} if payload_duty_percent is not None else {})
        ),
    )
