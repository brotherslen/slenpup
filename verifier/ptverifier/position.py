"""Position confirmation: is every metric of an exercise inside its band for long enough?

Shared by calibration (to check learned bands) and, later, by daily session verification.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import signals
from .exercises import Exercise
from .landmarks import compute_metric

MIN_VISIBILITY = 0.5


def metric_series(t_ms: np.ndarray, frames: np.ndarray, ex: Exercise, side: str) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Smoothed series for every metric of `ex` on `side`, on a common 30 Hz grid."""
    grid = signals.grid_for(t_ms)
    series = {}
    for name, m in ex.metrics.items():
        raw = compute_metric(frames, side, m.kind, m.joints, MIN_VISIBILITY)
        series[name] = signals.smooth(signals.resample(t_ms, raw, grid))
    return grid, series


@dataclass
class PositionCheck:
    side: str
    held_s: float  # longest continuous time with every metric in band
    start_s: float | None
    required_s: float

    @property
    def passed(self) -> bool:
        return self.held_s >= self.required_s


def check_side(
    t_ms: np.ndarray, frames: np.ndarray, ex: Exercise, side: str, bands: dict[str, tuple[float, float]], after_s: float | None = None
) -> PositionCheck:
    """Longest hold on one side. With after_s, only time after that point counts (the spoken word)."""
    grid, series = metric_series(t_ms, frames, ex, side)
    if grid.size == 0:
        return PositionCheck(side, 0.0, None, ex.min_position_s)
    mask = signals.in_band_mask(series, bands)
    if after_s is not None:
        mask &= grid >= after_s
    best = signals.longest(signals.spans(grid, mask))
    return PositionCheck(side, best[1] if best else 0.0, best[0] if best else None, ex.min_position_s)


def check(
    t_ms: np.ndarray, frames: np.ndarray, ex: Exercise, side: str | None = None, bands: dict | None = None, after_s: float | None = None
) -> PositionCheck:
    """One position check. For a both-sides exercise with no side given, the better of the two
    calibrated sides counts (the camera sees one side clearly). For per-side exercises pass the side."""
    bands = bands or ex.bands
    if not bands:
        raise ValueError(f"{ex.key} has no calibrated bands yet")
    if side is not None:
        return check_side(t_ms, frames, ex, side, bands[side], after_s)
    results = [check_side(t_ms, frames, ex, s, b, after_s) for s, b in bands.items()]
    return max(results, key=lambda r: r.held_s)
