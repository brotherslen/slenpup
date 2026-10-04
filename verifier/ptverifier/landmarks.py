"""MediaPipe pose landmark indices and per-frame metric computation."""

from __future__ import annotations

import numpy as np

# MediaPipe Pose Landmarker: 33 landmarks. Only the ones the exercises use are named here.
_INDEX = {
    "left": {"shoulder": 11, "elbow": 13, "wrist": 15, "hip": 23, "knee": 25, "ankle": 27, "heel": 29, "foot_index": 31},
    "right": {"shoulder": 12, "elbow": 14, "wrist": 16, "hip": 24, "knee": 26, "ankle": 28, "heel": 30, "foot_index": 32},
}
JOINTS = frozenset(_INDEX["left"])
NUM_LANDMARKS = 33

# Per-frame landmark array layout: (33, 4) = x_px, y_px, visibility, presence. NaN rows = no pose.
X, Y, VIS, PRES = 0, 1, 2, 3


def index(side: str, joint: str) -> int:
    return _INDEX[side][joint]


def angle_deg(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Angle ABC in degrees, vectorized over leading axes. a, b, c: (..., 2)."""
    ba = a - b
    bc = c - b
    dot = np.sum(ba * bc, axis=-1)
    norm = np.linalg.norm(ba, axis=-1) * np.linalg.norm(bc, axis=-1)
    with np.errstate(invalid="ignore", divide="ignore"):
        cos = np.clip(dot / norm, -1.0, 1.0)
    return np.degrees(np.arccos(cos))


def joint_xy(frames: np.ndarray, side: str, joint: str) -> np.ndarray:
    """(T, 2) pixel positions of one joint across frames."""
    return frames[:, index(side, joint), :2]


def joint_visibility(frames: np.ndarray, side: str, joint: str) -> np.ndarray:
    return frames[:, index(side, joint), VIS]


def torso_length(frames: np.ndarray, side: str) -> float:
    """Median shoulder-hip distance in pixels over the clip; the unit for height metrics."""
    d = np.linalg.norm(joint_xy(frames, side, "shoulder") - joint_xy(frames, side, "hip"), axis=-1)
    d = d[np.isfinite(d)]
    return float(np.median(d)) if d.size else float("nan")


def compute_metric(frames: np.ndarray, side: str, kind: str, joints: tuple[str, ...], min_visibility: float) -> np.ndarray:
    """Metric time series (T,). Frames where any involved joint is below min_visibility are NaN."""
    vis = np.min(np.stack([joint_visibility(frames, side, j) for j in joints]), axis=0)
    if kind == "angle":
        a, b, c = (joint_xy(frames, side, j) for j in joints)
        values = angle_deg(a, b, c)
    elif kind == "height":
        of, above = (joint_xy(frames, side, j) for j in joints)
        # Image y grows downward, so "of is higher than above" is above_y - of_y.
        values = (above[:, 1] - of[:, 1]) / torso_length(frames, side)
    elif kind == "tilt":
        start, end = (joint_xy(frames, side, j) for j in joints)
        seg = end - start
        with np.errstate(invalid="ignore", divide="ignore"):
            # Angle from image-up (0, -1); facing left or right gives the same value.
            values = np.degrees(np.arccos(np.clip(-seg[:, 1] / np.linalg.norm(seg, axis=-1), -1.0, 1.0)))
    else:
        raise ValueError(kind)
    values = values.astype(float)
    values[~(vis >= min_visibility)] = np.nan
    return values
