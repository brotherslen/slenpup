"""Signal processing for metric time series: resampling, smoothing, stillness, and spans."""

from __future__ import annotations

import numpy as np
from scipy.ndimage import median_filter, uniform_filter1d

RATE_HZ = 30.0
MAX_GAP_S = 0.5  # gaps in valid samples longer than this stay NaN instead of being interpolated
MAX_DROPOUT_S = 0.3  # a span survives a dropout this short (a few lost frames)

# A metric counts as "still" when its rolling 1 s standard deviation is under this.
STILL_STD = {"angle": 4.0, "height": 0.03, "tilt": 4.0}
# Calibrated bands are widened by this on each side, so a normal day's variation still passes.
BAND_MARGIN = {"angle": 8.0, "height": 0.05, "tilt": 8.0}


def grid_for(t_ms: np.ndarray, rate_hz: float = RATE_HZ) -> np.ndarray:
    """Uniform time grid in seconds covering the clip."""
    t = t_ms[np.isfinite(t_ms)] / 1000.0
    if t.size < 2:
        return np.empty(0)
    return np.arange(t[0], t[-1], 1.0 / rate_hz)


def resample(t_ms: np.ndarray, values: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """Values on `grid`. Short gaps are linearly filled; long gaps and the ends outside valid data stay NaN."""
    ok = np.isfinite(values) & np.isfinite(t_ms)
    out = np.full(grid.shape, np.nan)
    if ok.sum() < 2 or grid.size == 0:
        return out
    t = t_ms[ok] / 1000.0
    v = values[ok]
    out = np.interp(grid, t, v, left=np.nan, right=np.nan)
    idx = np.searchsorted(t, grid, side="right")
    prev_t = t[np.clip(idx - 1, 0, len(t) - 1)]
    next_t = t[np.clip(idx, 0, len(t) - 1)]
    out[(next_t - prev_t) > MAX_GAP_S] = np.nan
    return out


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
    }


def rolling_std(values: np.ndarray, window: int) -> np.ndarray:
    """Centered rolling standard deviation; NaN wherever the window touches a NaN."""
    out = np.full(values.shape, np.nan)
    if values.size < window:
        return out
    win = np.lib.stride_tricks.sliding_window_view(values, window)
    sd = np.std(win, axis=-1)  # NaN if any NaN in the window
    half = window // 2
    out[half : half + sd.size] = sd
    return out


def still_mask(series: dict[str, np.ndarray], kinds: dict[str, str], window_s: float = 1.0) -> np.ndarray:
    """True where every metric is finite and its rolling std is under its STILL_STD."""
    window = max(3, int(window_s * RATE_HZ))
    masks = [rolling_std(v, window) < STILL_STD[kinds[name]] for name, v in series.items()]
    return np.logical_and.reduce(masks)


def in_band_mask(series: dict[str, np.ndarray], bands: dict[str, tuple[float, float]]) -> np.ndarray:
    """True where every metric is finite and inside its [low, high] band."""
    masks = [np.isfinite(v) & (v >= bands[name][0]) & (v <= bands[name][1]) for name, v in series.items()]
    return np.logical_and.reduce(masks)


def spans(t: np.ndarray, mask: np.ndarray, min_s: float = 0.0, max_dropout_s: float = MAX_DROPOUT_S) -> list[tuple[float, float]]:
    """(start_s, duration_s) of runs where mask is True, bridging dropouts up to max_dropout_s."""
    out: list[tuple[float, float]] = []
    start = last = None
    for ti, ok in zip(t, mask):
        if ok:
            if start is None:
                start = ti
            last = ti
        elif start is not None and ti - last > max_dropout_s:
            if last - start >= min_s:
                out.append((float(start), float(last - start)))
            start = None
    if start is not None and last - start >= min_s:
        out.append((float(start), float(last - start)))
    return out


def longest(found: list[tuple[float, float]]) -> tuple[float, float] | None:
    return max(found, key=lambda s: s[1]) if found else None
