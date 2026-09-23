"""Configured environment schedule.

This is a *configured demonstration cycle*, not an orbital calculation. It repeats a
sunlight period followed by an eclipse period and says so wherever it is displayed.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config.loader import EnvironmentConfig
from app.contracts.common import Illumination


@dataclass(frozen=True)
class EnvironmentState:
    illumination: Illumination
    illumination_factor: float
    seconds_to_next_change: float
    boundary_c: float

    @property
    def is_sunlit(self) -> bool:
        return self.illumination is Illumination.SUNLIGHT


def evaluate(
    env: EnvironmentConfig,
    sim_time_s: float,
    boundary_sunlight_c: float,
    boundary_eclipse_c: float,
) -> EnvironmentState:
    """Illumination at a simulated time, plus the time remaining in the current phase."""
    sunlight_s = env.sunlight_minutes * 60.0
    eclipse_s = env.eclipse_minutes * 60.0
    period_s = sunlight_s + eclipse_s

    if period_s <= 0:
        return EnvironmentState(Illumination.SUNLIGHT, 1.0, float("inf"), boundary_sunlight_c)

    phase = sim_time_s % period_s
    if phase < sunlight_s:
        return EnvironmentState(
            illumination=Illumination.SUNLIGHT,
            illumination_factor=1.0,
            seconds_to_next_change=sunlight_s - phase,
            boundary_c=boundary_sunlight_c,
        )
    return EnvironmentState(
        illumination=Illumination.ECLIPSE,
        illumination_factor=0.0,
        seconds_to_next_change=period_s - phase,
        boundary_c=boundary_eclipse_c,
    )


def schedule_boundaries(env: EnvironmentConfig, start_s: float, horizon_s: float) -> list[float]:
    """Simulated times where illumination changes inside a window.

    A forecast integrates through these exactly rather than stepping over them, so a
    projection cannot miss an eclipse entry that falls between output points.
    """
    sunlight_s = env.sunlight_minutes * 60.0
    eclipse_s = env.eclipse_minutes * 60.0
    period_s = sunlight_s + eclipse_s
    if period_s <= 0:
        return []

    boundaries: list[float] = []
    first_period = int(start_s // period_s)
    t_base = first_period * period_s
    while t_base <= start_s + horizon_s:
        for offset in (sunlight_s, period_s):
            t = t_base + offset
            if start_s < t <= start_s + horizon_s:
                boundaries.append(t)
        t_base += period_s
    return sorted(boundaries)
