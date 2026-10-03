"""Synthetic side-view pose tracks with known rep and hold counts."""

from __future__ import annotations

import numpy as np

from ptverifier.landmarks import NUM_LANDMARKS, index
from ptverifier.pose import PoseTrack

FPS = 30.0
SHOULDER = np.array([500.0, 300.0])
HIP = np.array([500.0, 500.0])  # torso = 200 px
KNEE = np.array([600.0, 520.0])
ANKLE = np.array([600.0, 700.0])


def rep_profile(n_reps: int, low: float, high: float, rest_s=1.0, move_s=1.5, top_s=0.5, lead_s=1.0, tail_s=1.0) -> tuple[np.ndarray, np.ndarray]:
    """(t_seconds, value): rest at `low`, smooth rise to `high`, brief top, smooth return."""
    seg = []
    seg.append(np.full(int(lead_s * FPS), low))
    for _ in range(n_reps):
        up = low + (high - low) * (1 - np.cos(np.linspace(0, np.pi, int(move_s * FPS)))) / 2
        seg += [up, np.full(int(top_s * FPS), high), up[::-1], np.full(int(rest_s * FPS), low)]
    seg.append(np.full(int(tail_s * FPS), low))
    v = np.concatenate(seg)
    return np.arange(v.size) / FPS, v


def _blank(n: int) -> np.ndarray:
    frames = np.full((n, NUM_LANDMARKS, 4), np.nan)
    frames[:, :, 2:] = 0.0
    return frames


def _place(frames, side, joint, xy, vis):
    i = index(side, joint)
    frames[:, i, :2] = xy
    frames[:, i, 2] = vis
    frames[:, i, 3] = 1.0


def arm_track(theta_deg: np.ndarray, near: str = "left", noise_px: float = 2.0, seed: int = 0) -> PoseTrack:
    """Shoulder flexion = theta (angle hip-shoulder-elbow), elbow straight. Near side clearly visible,
    far side the same pose but low visibility, as a side-view camera sees it."""
    rng = np.random.default_rng(seed)
    n = theta_deg.size
    th = np.radians(theta_deg)
    direction = np.stack([np.sin(th), np.cos(th)], axis=1)
    frames = _blank(n)
    for side, vis in ((near, 0.95), ("right" if near == "left" else "left", 0.3)):
        jitter = lambda: rng.normal(0, noise_px, (n, 2))  # noqa: E731
        _place(frames, side, "shoulder", SHOULDER + jitter(), vis)
        _place(frames, side, "hip", HIP + jitter(), vis)
        _place(frames, side, "elbow", SHOULDER + 150 * direction + jitter(), vis)
        _place(frames, side, "wrist", SHOULDER + 280 * direction + jitter(), vis)
        _place(frames, side, "knee", KNEE + jitter(), vis)
        _place(frames, side, "ankle", ANKLE + jitter(), vis)
    return PoseTrack(np.arange(n) * 1000.0 / FPS, frames, 1280, 720, FPS)


def knee_lift_track(lift_torso: np.ndarray, near: str = "left", noise_px: float = 1.5, seed: int = 0) -> PoseTrack:
    """Knee raised `lift_torso` torso-lengths above the ankle (bear hold)."""
    rng = np.random.default_rng(seed)
    n = lift_torso.size
    frames = _blank(n)
    for side, vis in ((near, 0.95), ("right" if near == "left" else "left", 0.3)):
        jitter = lambda: rng.normal(0, noise_px, (n, 2))  # noqa: E731
        ankle = np.tile(ANKLE, (n, 1))
        knee = ankle - np.stack([np.zeros(n), 200 * lift_torso], axis=1)
        _place(frames, side, "shoulder", SHOULDER + jitter(), vis)
        _place(frames, side, "hip", HIP + jitter(), vis)
        _place(frames, side, "knee", knee + jitter(), vis)
        _place(frames, side, "ankle", ankle + jitter(), vis)
    return PoseTrack(np.arange(n) * 1000.0 / FPS, frames, 1280, 720, FPS)


def hold_profile(n_holds: int, hold_s: float, rest_s: float, low: float, high: float) -> np.ndarray:
    seg = [np.full(int(rest_s * FPS), low)]
    for _ in range(n_holds):
        seg += [np.full(int(hold_s * FPS), high), np.full(int(rest_s * FPS), low)]
    return np.concatenate(seg)
