"""Video to per-frame pose landmarks with MediaPipe Pose Landmarker. Everything stays on this machine."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .landmarks import NUM_LANDMARKS


@dataclass
class PoseTrack:
    """Landmarks for every decoded frame of one clip."""

    t_ms: np.ndarray  # (T,) frame timestamps from the container, milliseconds
    frames: np.ndarray  # (T, 33, 4): x_px, y_px, visibility, presence; NaN where no pose was found
    width: int
    height: int
    fps_nominal: float


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def extract(video: str | Path, model_path: str | Path) -> PoseTrack:
    """Runs the pose model over every frame, CPU only.

    Measured 2026-10-03 on a 4-vCPU Xeon @ 2.1 GHz, 720p30: lite 64 fps, full 56 fps, heavy 18 fps.
    Not yet measured on the NUC8i5; expect the same order for full (a 10-minute session in a few minutes).
    """
    import cv2
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise FileNotFoundError(f"cannot open video {video}")
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)

    options = vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(model_path)),
        running_mode=vision.RunningMode.VIDEO,
        num_poses=1,
    )
    times: list[float] = []
    rows: list[np.ndarray] = []
    last_ts = -1
    with vision.PoseLandmarker.create_from_options(options) as landmarker:
        index = 0
        while True:
            ok, bgr = cap.read()
            if not ok:
                break
            t = float(cap.get(cv2.CAP_PROP_POS_MSEC))
            if t <= 0 and index > 0 and fps > 0:
                t = index * 1000.0 / fps  # some containers report 0; fall back to nominal rate
            # The landmarker requires strictly increasing integer timestamps.
            ts = max(int(round(t)), last_ts + 1)
            last_ts = ts
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
            result = landmarker.detect_for_video(image, ts)
            row = np.full((NUM_LANDMARKS, 4), np.nan)
            if result.pose_landmarks:
                for i, lm in enumerate(result.pose_landmarks[0]):
                    row[i] = (lm.x * width, lm.y * height, lm.visibility, lm.presence)
            times.append(t)
            rows.append(row)
            index += 1
    cap.release()
    frames = np.stack(rows) if rows else np.empty((0, NUM_LANDMARKS, 4))
    return PoseTrack(np.asarray(times, dtype=float), frames, width, height, fps)
