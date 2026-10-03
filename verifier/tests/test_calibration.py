import json
import os
from pathlib import Path

import numpy as np
import pytest

from ptverifier import calibrate, signals
from ptverifier.exercises import ExerciseConfigError, load_catalog
from ptverifier.landmarks import angle_deg, compute_metric

from . import synthetic

CATALOG_PATH = Path(__file__).resolve().parent.parent / "exercises.yaml"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(CATALOG_PATH)


# ------------------------------------------------------------------------------------ geometry


def test_angle_deg():
    b = np.array([0.0, 0.0])
    assert angle_deg(np.array([1.0, 0.0]), b, np.array([0.0, 1.0])) == pytest.approx(90)
    assert angle_deg(np.array([1.0, 0.0]), b, np.array([-1.0, 0.0])) == pytest.approx(180)
    assert angle_deg(np.array([1.0, 0.0]), b, np.array([1.0, 1.0])) == pytest.approx(45)
    assert np.isnan(angle_deg(np.array([0.0, 0.0]), b, np.array([1.0, 0.0])))


def test_metric_masks_low_visibility():
    _, theta = synthetic.rep_profile(2, 20, 160)
    track = synthetic.arm_track(theta, near="left")
    near = compute_metric(track.frames, "left", "angle", ("hip", "shoulder", "elbow"), 0.5)
    far = compute_metric(track.frames, "right", "angle", ("hip", "shoulder", "elbow"), 0.5)
    assert np.isfinite(near).all()
    assert np.isnan(far).all()
    assert np.nanmax(near) == pytest.approx(160, abs=3)


def test_height_metric_in_torso_lengths():
    track = synthetic.knee_lift_track(np.full(30, 0.25))
    lift = compute_metric(track.frames, "left", "height", ("knee", "ankle"), 0.5)
    assert np.nanmedian(lift) == pytest.approx(0.25, abs=0.02)


# ------------------------------------------------------------------------------------- signals


def test_resample_fills_short_gaps_only():
    t_ms = np.arange(0, 3000, 1000 / 30)
    v = np.ones_like(t_ms)
    v[30:36] = np.nan  # 0.2 s gap: filled
    v[60:90] = np.nan  # 1 s gap: left as NaN
    grid, out = signals.resample(t_ms, v)
    assert np.isfinite(out[(grid > 1.0) & (grid < 1.2)]).all()
    assert np.isnan(out[(grid > 2.1) & (grid < 2.9)]).all()


@pytest.mark.parametrize("n", [1, 7, 10, 11])
def test_count_reps(n):
    _, v = synthetic.rep_profile(n, 20, 160)
    assert signals.count_reps(v, enter=111, exit=69, active="high") == n
    assert signals.count_reps(-v, enter=-111, exit=-69, active="low") == n


def test_partial_rep_at_start_and_after_gap_not_counted():
    _, v = synthetic.rep_profile(5, 20, 160)
    start_mid = v[np.argmax(v > 150):]  # clip starts at the top of rep 1
    assert signals.count_reps(start_mid, 111, 69, "high") == 4
    gapped = v.copy()
    top = np.flatnonzero(v > 150)[0] + 200  # inside a later rep
    i = top + np.argmax(v[top:] > 150)
    gapped[i - 3 : i + 3] = np.nan
    assert signals.count_reps(gapped, 111, 69, "high") == 4


def test_jitter_is_not_movement():
    rng = np.random.default_rng(1)
    still = 97 + rng.normal(0, 0.3, 300)
    t = np.arange(still.size) / synthetic.FPS
    ex = signals.find_extremes(t, still, "high", signals.MIN_SPAN["angle"])
    assert ex.active.size == 0 and ex.rest.size == 0
    assert signals.suggest_rep_thresholds(ex) is None


def test_find_holds():
    v = synthetic.hold_profile(5, hold_s=10, rest_s=3, low=0.0, high=0.3)
    t = np.arange(v.size) / synthetic.FPS
    holds = signals.find_holds(t, v, threshold=0.15, active="high")
    assert len(holds) == 5
    assert all(d == pytest.approx(10, abs=0.1) for _, d in holds)
    v[200] = np.nan  # a single dropped frame inside the first hold is bridged
    assert len(signals.find_holds(t, v, 0.15, "high")) == 5


# ------------------------------------------------------------------------------------- catalog


def test_catalog_loads(catalog):
    assert set(catalog.sessions) == {"PT-A", "PT-B", "Walk-A"}
    assert catalog.get("prone_y").laterality == {"left": 1.0, "right": 0.5}
    assert catalog.get("bear_hold").kind == "hold"
    with pytest.raises(ExerciseConfigError, match="TBD"):
        catalog.get("glute_bridge")
    with pytest.raises(ExerciseConfigError, match="unknown exercise"):
        catalog.get("burpees")


@pytest.mark.parametrize(
    "patch, message",
    [
        ({"primary": "nope"}, "primary"),
        ({"kind": "sets"}, "kind"),
        ({"metrics": {"x": {"angle": ["hip", "shoulder"]}}, "primary": "x"}, "3 joints"),
        ({"metrics": {"x": {"angle": ["hip", "shoulder", "toe"]}}, "primary": "x"}, "unknown joint"),
    ],
)
def test_catalog_rejects_bad_config(tmp_path, patch, message):
    import yaml

    data = yaml.safe_load(CATALOG_PATH.read_text())
    data["exercises"]["wall_slides"].update(patch)
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(data))
    with pytest.raises(ExerciseConfigError, match=message):
        load_catalog(bad)


def test_parse_name():
    assert calibrate.parse_name(Path("2026-10-05_wall_slides_both_10.mp4")) == {
        "date": "2026-10-05",
        "exercise": "wall_slides",
        "side": "both",
        "count": 10,
    }
    assert calibrate.parse_name(Path("2026-10-05_prone_y_right_4-set2.mov"))["exercise"] == "prone_y"
    assert calibrate.parse_name(Path("IMG_0042.mp4")) is None


# ------------------------------------------------------------------------------------ analysis


def test_analyse_reps_picks_camera_side_and_counts(catalog):
    _, theta = synthetic.rep_profile(10, 25, 155)
    track = synthetic.arm_track(theta, near="right")
    result, series = calibrate.analyse(track, catalog.get("wall_slides"), None, 10)
    assert result["side"] == "right"
    p = result["primary"]
    assert p["detected_with_suggested"] == 10
    assert p["suggested"]["active_median"] == pytest.approx(155, abs=3)
    assert p["suggested"]["rest_median"] == pytest.approx(25, abs=3)
    assert result["video"]["timestamps_monotonic"]
    assert result["metrics"]["shoulder_flexion"]["left"] == {"n": 0}  # far side hidden
    json.dumps(result)  # serializable


def test_analyse_per_side_requires_side(catalog):
    _, theta = synthetic.rep_profile(3, 25, 155)
    with pytest.raises(ValueError, match="per-side"):
        calibrate.analyse(synthetic.arm_track(theta), catalog.get("prone_y"), "both", 3)


def test_analyse_hold(catalog):
    lift = synthetic.hold_profile(5, hold_s=10, rest_s=4, low=0.0, high=0.2)
    result, _ = calibrate.analyse(synthetic.knee_lift_track(lift), catalog.get("bear_hold"), None, 5)
    holds = result["primary"]["holds_with_suggested"]
    assert len(holds) == 5
    assert all(h["duration_s"] == pytest.approx(10, abs=0.3) for h in holds)


def test_record_and_report_pooled_per_side(tmp_path, catalog, monkeypatch):
    """Left and right calibrated separately (asymmetric ranges), then pooled thresholds re-count every clip."""
    tracks = {}
    clips = [
        ("2026-10-05_prone_y_left_8.mp4", "left", 8, 0.10, 0.60),
        ("2026-10-06_prone_y_left_9.mp4", "left", 9, 0.12, 0.62),
        ("2026-10-05_prone_y_right_4.mp4", "right", 4, 0.10, 0.45),
        ("2026-10-06_prone_y_right_4.mp4", "right", 4, 0.08, 0.43),
    ]
    for name, side, n, low, high in clips:
        _, lift = synthetic.rep_profile(n, low, high)
        # wrist height above shoulder = lift torso lengths: put the arm straight up/down via theta
        theta = np.degrees(np.arccos(np.clip(-lift * 200 / 280, -1, 1)))
        tracks[name] = synthetic.arm_track(theta, near=side, seed=n)
        (tmp_path / name).write_bytes(name.encode())
    model = tmp_path / "model.task"
    model.write_bytes(b"model")
    monkeypatch.setattr(calibrate, "extract", lambda video, _model: tracks[Path(video).name])

    data = tmp_path / "data"
    for name, *_ in clips:
        calibrate.record(tmp_path / name, catalog, model, data, None, None, None)
    assert len(list((data / "results" / "prone_y").glob("*.json"))) == 4

    rep = calibrate.report(catalog, data, "prone_y")
    assert rep["sides"]["left"]["matches_declared"] == "2/2"
    assert rep["sides"]["right"]["matches_declared"] == "2/2"
    assert rep["asymmetry"]["range_right"] < rep["asymmetry"]["range_left"]
    th = rep["yaml_snippet"]["prone_y"]["thresholds"]
    assert th["left"]["enter"] > th["right"]["enter"]


def test_watch_inbox_moves_bad_clip_to_failed(tmp_path, catalog, monkeypatch):
    monkeypatch.setattr(calibrate, "_wait_until_stable", lambda p: True)
    clip = tmp_path / "IMG_0042.mp4"
    clip.write_bytes(b"x")
    calibrate.process_inbox_file(clip, catalog, tmp_path / "m.task", tmp_path / "data")
    assert (tmp_path / "data" / "failed" / "IMG_0042.mp4").exists()
    assert "can't tell the exercise" in (tmp_path / "data" / "failed" / "IMG_0042.mp4.txt").read_text()


# --------------------------------------------------------------------------- real model (optional)


@pytest.mark.skipif(
    not (os.environ.get("PTV_SAMPLE_IMAGE") and calibrate.DEFAULT_MODEL.exists()),
    reason="set PTV_SAMPLE_IMAGE to a photo of a person and download the model to run",
)
def test_mediapipe_end_to_end(tmp_path):
    import cv2

    from ptverifier.pose import extract

    img = cv2.imread(os.environ["PTV_SAMPLE_IMAGE"])
    h, w = img.shape[:2]
    video = tmp_path / "still.mp4"
    out = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 30, (w, h))
    for _ in range(45):
        out.write(img)
    out.release()
    track = extract(video, calibrate.DEFAULT_MODEL)
    assert track.frames.shape[0] >= 40
    assert np.isfinite(track.frames[:, 11, 0]).mean() > 0.9
    assert np.all(np.diff(track.t_ms) > 0)
