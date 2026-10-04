"""Synthetic side-view pose tracks: a body moving between known postures."""

from __future__ import annotations

import numpy as np

from ptverifier.landmarks import NUM_LANDMARKS, index
from ptverifier.pose import PoseTrack

FPS = 30.0

# Near-side joint pixel positions for a few postures, side view, facing right.
POSTURES = {
    # standing, arms overhead against the wall (wall slide top)
    "wall_slide": {"shoulder": (500, 300), "elbow": (520, 170), "wrist": (530, 50), "hip": (500, 500), "knee": (505, 620), "ankle": (500, 740)},
    # standing, arms down (rest between sets)
    "standing": {"shoulder": (500, 300), "elbow": (505, 420), "wrist": (510, 520), "hip": (500, 500), "knee": (505, 620), "ankle": (500, 740)},
    # face down, left arm raised forward and up (prone Y)
    "prone_y": {"shoulder": (600, 600), "elbow": (730, 560), "wrist": (850, 520), "hip": (400, 605), "knee": (250, 610), "ankle": (100, 615)},
    # face down, arm raised less (a weaker right side)
    "prone_y_low": {"shoulder": (600, 600), "elbow": (735, 585), "wrist": (860, 570), "hip": (400, 605), "knee": (250, 610), "ankle": (100, 615)},
    # bear hold: hands and feet, knees bent ~90, hips at shoulder height
    "bear": {"shoulder": (600, 450), "elbow": (605, 550), "wrist": (610, 650), "hip": (400, 455), "knee": (420, 555), "ankle": (320, 600)},
}


def posture_track(segments: list[tuple[str, float]], near: str = "left", noise_px: float = 1.5, seed: int = 0, mirror: bool = False) -> PoseTrack:
    """Holds each (posture, seconds) in turn with 1 s linear transitions between them. The near side is
    clearly visible; the far side gets the same pose at low visibility, as a side camera sees it."""
    rng = np.random.default_rng(seed)
    keys = []
    for i, (name, secs) in enumerate(segments):
        if i:
            prev = segments[i - 1][0]
            for k in range(int(FPS)):
                keys.append((prev, name, (k + 1) / FPS))
        keys += [(name, name, 0.0)] * int(secs * FPS)
    n = len(keys)
    frames = np.full((n, NUM_LANDMARKS, 4), np.nan)
    frames[:, :, 2:] = 0.0
    far = "right" if near == "left" else "left"
    for joint in POSTURES["standing"]:
        xy = np.array([(1 - w) * np.array(POSTURES[a][joint]) + w * np.array(POSTURES[b][joint]) for a, b, w in keys], dtype=float)
        if mirror:
            xy[:, 0] = 1280 - xy[:, 0]
        for side, vis in ((near, 0.95), (far, 0.3)):
            i = index(side, joint)
            frames[:, i, :2] = xy + rng.normal(0, noise_px, (n, 2))
            frames[:, i, 2] = vis
            frames[:, i, 3] = 1.0
    return PoseTrack(np.arange(n) * 1000.0 / FPS, frames, 1280, 720, FPS)


def concat(*tracks: PoseTrack) -> PoseTrack:
    """Back-to-back clips as one continuous track (e.g. left side, turn around, right side)."""
    t, frames, offset = [], [], 0.0
    for tr in tracks:
        t.append(tr.t_ms + offset)
        frames.append(tr.frames)
        offset = t[-1][-1] + 1000.0 / FPS
    return PoseTrack(np.concatenate(t), np.concatenate(frames), tracks[0].width, tracks[0].height, FPS)
