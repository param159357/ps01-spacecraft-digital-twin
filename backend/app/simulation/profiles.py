"""Deterministic flight-systems and link profiles — CLAUDE.md section 7.5.

These are **simplified seeded profiles, not a validated attitude-control model**. They
produce plausible, reproducible readings for attitude, the selected reaction wheel, the
communication link and the computer reset counter. Nothing here integrates rigid-body
dynamics, and the channel dictionary marks every one of these channels as `PROFILE`
coverage so no screen implies their future behaviour is forecastable.

Everything is a pure function of simulated time plus a per-run phase offset derived from
the run seed, so a scenario replays identically and no hidden state is needed for the
continuous channels. Resets and packet accounting are the exceptions: both are genuine
counters and live in `SimulatorTruth`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.config.loader import FlightProfilesConfig
from app.contracts.common import OperatingMode


def _phase(seed: int, index: int) -> float:
    """Stable per-run, per-channel phase offset in [0, 2*pi).

    Derived arithmetically rather than from a random generator so that a profile value
    depends only on (seed, channel, sim_time) and never on call order.
    """
    mixed = (seed * 2654435761 + index * 40503) % 1000003
    return (mixed / 1000003.0) * 2.0 * math.pi


def _wave(sim_time_s: float, seed: int, index: int, period_s: float) -> float:
    """A smooth, bounded, deterministic oscillation in roughly [-1, 1].

    Two incommensurate periods keep it from looking like an obvious sine wave without
    introducing randomness that would break reproducibility.
    """
    a = math.sin(2.0 * math.pi * sim_time_s / period_s + _phase(seed, index))
    b = math.sin(2.0 * math.pi * sim_time_s / (period_s * 0.37) + _phase(seed, index + 97))
    return 0.75 * a + 0.25 * b


@dataclass(frozen=True)
class FlightProfileState:
    """Derived flight-systems values for one tick. All PROFILE coverage."""

    pointing_error_deg: float
    roll_rate_dps: float
    pitch_rate_dps: float
    yaw_rate_dps: float
    wheel_speed_rpm: float
    wheel_current_a: float
    cell_voltage_spread_v: float


def evaluate_flight(
    cfg: FlightProfilesConfig,
    sim_time_s: float,
    seed: int,
    mode: OperatingMode,
    dt_s: float = 1.0,
) -> FlightProfileState:
    """Attitude, wheel and cell-imbalance profiles for the current simulated time.

    A `MANEUVER` mode widens rates and pointing error; that is a configured profile
    response to a known command, not a fault.
    """
    scale = cfg.maneuver_scale if mode is OperatingMode.MANEUVER else 1.0

    roll = cfg.rate_amplitude_dps * scale * _wave(sim_time_s, seed, 1, cfg.rate_period_s)
    pitch = cfg.rate_amplitude_dps * scale * _wave(sim_time_s, seed, 2, cfg.rate_period_s * 1.13)
    yaw = cfg.rate_amplitude_dps * scale * _wave(sim_time_s, seed, 3, cfg.rate_period_s * 0.87)

    # Pointing error is a magnitude, so it is folded to be non-negative rather than
    # allowed to go below zero, which the channel's physical bounds forbid.
    pointing = cfg.pointing_bias_deg * scale + cfg.pointing_amplitude_deg * scale * abs(
        _wave(sim_time_s, seed, 4, cfg.pointing_period_s)
    )

    wheel_speed = cfg.wheel_bias_rpm + cfg.wheel_amplitude_rpm * scale * _wave(
        sim_time_s, seed, 5, cfg.wheel_period_s
    )

    # Simple consistency rather than an independent number: wheel current follows the
    # wheel's acceleration, so speed and current cannot disagree arbitrarily.
    previous_speed = cfg.wheel_bias_rpm + cfg.wheel_amplitude_rpm * scale * _wave(
        sim_time_s - dt_s, seed, 5, cfg.wheel_period_s
    )
    acceleration_rpm_s = (wheel_speed - previous_speed) / max(dt_s, 1e-9)
    wheel_current = cfg.wheel_idle_current_a + cfg.wheel_current_per_rpm_s * acceleration_rpm_s

    # Rule-only channel: a slow imbalance drift with no physical cell model behind it.
    spread = cfg.cell_spread_base_v + cfg.cell_spread_amplitude_v * abs(
        _wave(sim_time_s, seed, 6, cfg.cell_spread_period_s)
    )

    return FlightProfileState(
        pointing_error_deg=max(0.0, pointing),
        roll_rate_dps=roll,
        pitch_rate_dps=pitch,
        yaw_rate_dps=yaw,
        wheel_speed_rpm=wheel_speed,
        wheel_current_a=wheel_current,
        cell_voltage_spread_v=max(0.0, spread),
    )


# ------------------------------------------------------------------------ the link


@dataclass(frozen=True)
class ContactState:
    """Whether a high-rate pass is scheduled right now, and how far to the next change.

    Deliberate simplification, documented in docs/decisions.md: the configured contact
    window governs the *high-rate downlink* channels (`link_snr`, `packet_error_rate`).
    Basic telemetry keeps flowing between passes, as a continuous beacon would. A real
    loss of signal is modelled separately by the simulator's `contact_loss` injection,
    which stops delivery altogether and makes readings go stale.
    """

    expected: bool
    seconds_to_next_change: float


def evaluate_contact(cfg: FlightProfilesConfig, sim_time_s: float, cycle_s: float) -> ContactState:
    if cycle_s <= 0 or cfg.contact_minutes_per_cycle <= 0:
        return ContactState(expected=False, seconds_to_next_change=float("inf"))

    contact_s = min(cfg.contact_minutes_per_cycle * 60.0, cycle_s)
    phase = sim_time_s % cycle_s
    if phase < contact_s:
        return ContactState(expected=True, seconds_to_next_change=contact_s - phase)
    return ContactState(expected=False, seconds_to_next_change=cycle_s - phase)


def link_snr_db(
    cfg: FlightProfilesConfig, sim_time_s: float, seed: int, contact: ContactState
) -> float | None:
    """Link SNR during a scheduled pass, or `None` outside one.

    Outside a pass there is nothing to measure. Returning `None` rather than a low number
    is the difference between "no measurement" and "a bad link".
    """
    if not contact.expected:
        return None
    return cfg.link_snr_nominal_db + cfg.link_snr_amplitude_db * _wave(
        sim_time_s, seed, 7, cfg.link_period_s
    )


def packet_counts(
    cfg: FlightProfilesConfig, snr_db: float | None, dt_s: float
) -> tuple[int, int]:
    """Attempted and failed packets for this tick.

    Outside a pass nothing is attempted, which is what later makes the rolling error rate
    legitimately unavailable rather than zero.
    """
    if snr_db is None:
        return 0, 0
    attempted = max(0, int(round(cfg.packets_per_second * dt_s)))
    if attempted == 0:
        return 0, 0
    # Toy relationship: error rate falls off as SNR rises above the configured floor.
    margin_db = snr_db - cfg.link_snr_floor_db
    failure_fraction = cfg.packet_failure_at_floor / (1.0 + max(0.0, margin_db))
    failed = min(attempted, int(round(attempted * max(0.0, failure_fraction))))
    return attempted, failed


def packet_error_rate_percent(attempted: int, failed: int) -> float | None:
    """Rolling ratio, or `None` when the denominator is zero (section 7.5)."""
    if attempted <= 0:
        return None
    return 100.0 * failed / attempted
