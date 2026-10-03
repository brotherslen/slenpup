"""Signal processing for metric time series: resampling, smoothing, rep and hold detection, stats."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import median_filter, uniform_filter1d
from scipy.signal import find_peaks

RATE_HZ = 30.0
MAX_GAP_S = 0.5  # gaps in valid samples longer than this stay NaN instead of being interpolated


def resample(t_ms: np.ndarray, values: np.ndarray, rate_hz: float = RATE_HZ) -> tuple[np.ndarray, np.ndarray]:
    """Uniform-rate series in seconds. Short gaps are linearly filled; long gaps stay NaN."""
    ok = np.isfinite(values) & np.isfinite(t_ms)
    if ok.sum() < 2:
        return np.empty(0), np.empty(0)
    t = t_ms[ok] / 1000.0
    v = values[ok]
    grid = np.arange(t[0], t[-1], 1.0 / rate_hz)
    out = np.interp(grid, t, v)
    # Blank grid points that fall inside a long gap between valid samples.
    idx = np.searchsorted(t, grid, side="right")
    prev_t = t[np.clip(idx - 1, 0, len(t) - 1)]
    next_t = t[np.clip(idx, 0, len(t) - 1)]
    out[(next_t - prev_t) > MAX_GAP_S] = np.nan
    return grid, out


def smooth(values: np.ndarray, window: int = 5) -> np.ndarray:
    """Median then mean filter, NaN-preserving (filters run per finite run)."""
    out = np.full_like(values, np.nan)
    finite = np.isfinite(values)
    if not finite.any():
        return out
    edges = np.flatnonzero(np.diff(np.concatenate(([0], finite.astype(int), [0]))))
    for start, stop in zip(edges[::2], edges[1::2]):
        seg = values[start:stop]
        if len(seg) >= window:
            seg = uniform_filter1d(median_filter(seg, size=window, mode="nearest"), size=window, mode="nearest")
        out[start:stop] = seg
    return out


def stats(values: np.ndarray) -> dict:
    v = values[np.isfinite(values)]
    if v.size == 0:
        return {"n": 0}
    p = np.percentile(v, [5, 25, 50, 75, 95])
    return {
        "n": int(v.size),
        "min": float(v.min()),
        "p05": float(p[0]),
        "p25": float(p[1]),
        "p50": float(p[2]),
        "p75": float(p[3]),
        "p95": float(p[4]),
        "max": float(v.max()),
        "mean": float(v.mean()),
        "std": float(v.std()),
    }


@dataclass
class Extremes:
    active: np.ndarray  # values at the working end of each rep (peaks if active=high)
    rest: np.ndarray  # values at the rest end between reps
    active_t: np.ndarray
    rest_t: np.ndarray


# Below these p05-p95 ranges a clip is treated as "no movement", so tracking jitter can't pose as reps.
MIN_SPAN = {"angle": 10.0, "height": 0.05}


def find_extremes(t: np.ndarray, values: np.ndarray, active: str, min_span: float, min_period_s: float = 0.8) -> Extremes:
    """Active and rest extremes, using a prominence of 30% of the clip's p05-p95 range."""
    v = np.where(np.isfinite(values), values, np.nanmedian(values) if np.isfinite(values).any() else 0.0)
    sign = 1.0 if active == "high" else -1.0
    span = np.nanpercentile(values, 95) - np.nanpercentile(values, 5) if np.isfinite(values).any() else 0.0
    if not span >= min_span:
        return Extremes(np.empty(0), np.empty(0), np.empty(0), np.empty(0))
    distance = max(1, int(min_period_s * RATE_HZ))
    a_idx, _ = find_peaks(sign * v, prominence=0.3 * span, distance=distance)
    r_idx, _ = find_peaks(-sign * v, prominence=0.3 * span, distance=distance)
    return Extremes(v[a_idx], v[r_idx], t[a_idx], t[r_idx])


def suggest_rep_thresholds(ex: Extremes) -> dict | None:
    """Enter-active at 65% of the way from rest to active, back-to-rest at 35%. Medians, so a few bad reps don't move it."""
    if ex.active.size == 0 or ex.rest.size == 0:
        return None
    a = float(np.median(ex.active))
    r = float(np.median(ex.rest))
    return {"rest_median": r, "active_median": a, "enter": r + 0.65 * (a - r), "exit": r + 0.35 * (a - r)}


def count_reps(values: np.ndarray, enter: float, exit: float, active: str) -> int:
    """Hysteresis counter: a rep is rest -> past `enter` -> back past `exit`.

    If a clip (or the stretch after a tracking gap) starts already past `enter`, that partial rep
    isn't counted. A gap resets the state, so a rep split by lost tracking isn't counted either.
    """
    sign = 1.0 if active == "high" else -1.0
    enter_s, exit_s = sign * enter, sign * exit
    state = "unknown"
    reps = 0
    for x in values:
        if not np.isfinite(x):
            state = "unknown"
            continue
        s = sign * x
        if state == "unknown":
            state = "partial" if s >= enter_s else "rest"
        elif state == "rest" and s >= enter_s:
            state = "active"
        elif state in ("active", "partial") and s <= exit_s:
            reps += state == "active"
            state = "rest"
    return reps


def suggest_hold_threshold(values: np.ndarray) -> dict | None:
    """Midpoint between the low (p10) and high (p90) clusters of a hold clip."""
    v = values[np.isfinite(values)]
    if v.size < 2:
        return None
    lo, hi = np.percentile(v, [10, 90])
    if not hi > lo:
        return None
    return {"low_p10": float(lo), "high_p90": float(hi), "threshold": float((lo + hi) / 2)}


def find_holds(t: np.ndarray, values: np.ndarray, threshold: float, active: str, min_s: float = 2.0, max_dropout_s: float = 0.3) -> list[tuple[float, float]]:
    """(start, duration) of spans past `threshold`, bridging dropouts up to max_dropout_s."""
    sign = 1.0 if active == "high" else -1.0
    inside = np.isfinite(values) & (sign * values >= sign * threshold)
    holds: list[tuple[float, float]] = []
    start = None
    last_in = None
    for ti, ok in zip(t, inside):
        if ok:
            if start is None:
                start = ti
            last_in = ti
        elif start is not None and ti - last_in > max_dropout_s:
            if last_in - start >= min_s:
                holds.append((float(start), float(last_in - start)))
            start = None
    if start is not None and last_in - start >= min_s:
        holds.append((float(start), float(last_in - start)))
    return holds
