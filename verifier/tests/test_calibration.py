import json
import os
from pathlib import Path

import numpy as np
import pytest
import yaml

from ptverifier import calibrate, position, signals
from ptverifier.exercises import ExerciseConfigError, load_catalog
from ptverifier.landmarks import angle_deg, compute_metric

from .synthetic import FPS, posture_track

CATALOG_PATH = Path(__file__).resolve().parent.parent / "exercises.yaml"


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(CATALOG_PATH)


# ------------------------------------------------------------------------------------ geometry


def test_angle_deg():
    b = np.array([0.0, 0.0])
    assert angle_deg(np.array([1.0, 0.0]), b, np.array([0.0, 1.0])) == pytest.approx(90)
    assert angle_deg(np.array([1.0, 0.0]), b, np.array([-1.0, 0.0])) == pytest.approx(180)
    assert np.isnan(angle_deg(np.array([0.0, 0.0]), b, np.array([1.0, 0.0])))


@pytest.mark.parametrize("posture, expected", [("standing", 0), ("prone_y", 90)])
@pytest.mark.parametrize("mirror", [False, True])
def test_tilt_is_independent_of_facing(posture, expected, mirror):
    track = posture_track([(posture, 1)], mirror=mirror)
    tilt = compute_metric(track.frames, "left", "tilt", ("hip", "shoulder"), 0.5)
    assert np.nanmedian(tilt) == pytest.approx(expected, abs=4)


def test_metric_masks_low_visibility():
    track = posture_track([("standing", 1)], near="left")
    assert np.isfinite(compute_metric(track.frames, "left", "angle", ("hip", "shoulder", "elbow"), 0.5)).all()
    assert np.isnan(compute_metric(track.frames, "right", "angle", ("hip", "shoulder", "elbow"), 0.5)).all()


# ------------------------------------------------------------------------------------- signals


def test_resample_fills_short_gaps_only():
    t_ms = np.arange(0, 3000, 1000 / 30)
    v = np.ones_like(t_ms)
    v[30:36] = np.nan  # 0.2 s gap: filled
    v[60:90] = np.nan  # 1 s gap: left as NaN
    grid = signals.grid_for(t_ms)
    out = signals.resample(t_ms, v, grid)
    assert np.isfinite(out[(grid > 1.0) & (grid < 1.2)]).all()
    assert np.isnan(out[(grid > 2.1) & (grid < 2.9)]).all()


def test_spans_bridge_short_dropouts():
    t = np.arange(300) / FPS
    mask = np.zeros(300, bool)
    mask[30:240] = True
    mask[100:105] = False  # 0.17 s dropout: bridged
    found = signals.spans(t, mask)
    assert len(found) == 1 and found[0][1] == pytest.approx(7.0, abs=0.05)
    mask[150:170] = False  # 0.67 s dropout: splits the span
    assert len(signals.spans(t, mask)) == 2


def test_still_mask_ignores_movement():
    track = posture_track([("standing", 3), ("wall_slide", 6), ("standing", 3)])
    ex = load_catalog(CATALOG_PATH).get("wall_slides")
    grid, series = position.metric_series(track.t_ms, track.frames, ex, "left")
    best = signals.longest(signals.spans(grid, signals.still_mask(series, {k: m.kind for k, m in ex.metrics.items()})))
    assert best[1] == pytest.approx(6, abs=0.6)


# ------------------------------------------------------------------------------------- catalog


def test_catalog_loads(catalog):
    assert set(catalog.sessions) == {"PT-A", "PT-B", "Walk-A"}
    assert catalog.get("prone_y").sides == "per_side"
    assert catalog.get("bear_hold").min_position_s == 5
    with pytest.raises(ExerciseConfigError, match="TBD"):
        catalog.get("glute_bridge")
    with pytest.raises(ExerciseConfigError, match="unknown exercise"):
        catalog.get("burpees")


@pytest.mark.parametrize(
    "patch, message",
    [
        ({"sides": "one"}, "sides"),
        ({"metrics": {"x": {"angle": ["hip", "shoulder"]}}}, "3 joints"),
        ({"metrics": {"x": {"angle": ["hip", "shoulder", "toe"]}}}, "unknown joint"),
        ({"metrics": {"x": {"speed": 1}}}, "angle, height, or tilt"),
        ({"bands": {"left": {"torso": [0, 10]}}}, "exactly the metrics"),
        ({"bands": {"middle": {}}}, "left or right"),
        ({"bands": {"left": {"torso": [10, 0], "shoulder_flexion": [0, 1], "elbow": [0, 1]}}}, "low must be below"),
    ],
)
def test_catalog_rejects_bad_config(tmp_path, patch, message):
    data = yaml.safe_load(CATALOG_PATH.read_text())
    data["exercises"]["wall_slides"].update(patch)
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(data))
    with pytest.raises(ExerciseConfigError, match=message):
        load_catalog(bad)


def test_parse_name():
    assert calibrate.parse_name(Path("2026-10-05_wall_slides_both.mp4")) == {"date": "2026-10-05", "exercise": "wall_slides", "side": "both"}
    assert calibrate.parse_name(Path("2026-10-05_prone_y_right-set2.mov"))["exercise"] == "prone_y"
    assert calibrate.parse_name(Path("IMG_0042.mp4")) is None


# ------------------------------------------------------------------------------------ analysis


def test_analyse_finds_held_position(catalog):
    track = posture_track([("standing", 3), ("wall_slide", 8), ("standing", 2)], near="right")
    result = calibrate.analyse(track, catalog.get("wall_slides"), None)
    assert result["side"] == "right"
    pos = result["position"]
    assert pos["duration_s"] == pytest.approx(8, abs=0.6)
    assert pos["metrics"]["torso"]["p50"] == pytest.approx(0, abs=4)
    assert pos["metrics"]["shoulder_flexion"]["p50"] > 150
    assert len(pos["still_stretches"]) == 1  # the 2-3 s of standing are too short to list
    json.dumps(result)


def test_analyse_rejects_short_hold(catalog):
    track = posture_track([("standing", 1), ("wall_slide", 3), ("standing", 1)])
    # standing still counts as "still" too, so make every still stretch short
    result = calibrate.analyse(track, catalog.get("wall_slides"), None)
    assert "error" in result["position"]


def test_analyse_per_side_requires_side(catalog):
    with pytest.raises(ValueError, match="per-side"):
        calibrate.analyse(posture_track([("prone_y", 6)]), catalog.get("prone_y"), "both")


# ------------------------------------------------------------------------------------ position


def _bands_from(catalog, key, posture, side="left"):
    ex = catalog.get(key)
    result = calibrate.analyse(posture_track([(posture, 8)], near=side), ex, side)
    return {side: calibrate.pooled_bands(ex, [result])}


def test_position_needs_five_seconds(catalog):
    ex = catalog.get("bear_hold")
    bands = _bands_from(catalog, "bear_hold", "bear")
    five = posture_track([("standing", 2), ("bear", 5.3), ("standing", 2)])
    four = posture_track([("standing", 2), ("bear", 4.0), ("standing", 2)])
    assert position.check(five.t_ms, five.frames, ex, bands=bands).passed
    c = position.check(four.t_ms, four.frames, ex, bands=bands)
    assert not c.passed and c.held_s == pytest.approx(4, abs=0.6)


def test_position_both_sides_uses_better_side(catalog):
    ex = catalog.get("bear_hold")
    bands = _bands_from(catalog, "bear_hold", "bear", side="left")
    bands["right"] = bands["left"]
    track = posture_track([("bear", 7)], near="right")  # left side is low-visibility now
    c = position.check(track.t_ms, track.frames, ex, bands=bands)
    assert c.passed and c.side == "right"


def test_position_requires_bands(catalog):
    track = posture_track([("bear", 6)])
    with pytest.raises(ValueError, match="no calibrated bands"):
        position.check(track.t_ms, track.frames, catalog.get("bear_hold"))


# ------------------------------------------------------------------------- record + report flow


def test_record_and_report(tmp_path, catalog, monkeypatch):
    clips = {
        "2026-10-05_wall_slides_both.mp4": posture_track([("standing", 2), ("wall_slide", 9), ("standing", 2)], seed=1),
        "2026-10-06_wall_slides_both.mp4": posture_track([("standing", 2), ("wall_slide", 8), ("standing", 2)], seed=2),
        "2026-10-05_prone_y_left.mp4": posture_track([("prone_y", 9)], near="left", seed=3),
        "2026-10-06_prone_y_left.mp4": posture_track([("prone_y", 8)], near="left", seed=4),
        "2026-10-05_prone_y_right.mp4": posture_track([("prone_y_low", 9)], near="right", seed=5),
        "2026-10-05_bear_hold_both.mp4": posture_track([("standing", 2), ("bear", 9)], seed=6),
    }
    for name in clips:
        (tmp_path / name).write_bytes(name.encode())
    model = tmp_path / "model.task"
    model.write_bytes(b"model")
    monkeypatch.setattr(calibrate, "extract", lambda video, _model: clips[Path(video).name])

    data = tmp_path / "data"
    for name in clips:
        calibrate.record(tmp_path / name, catalog, model, data, None, None)

    wall = calibrate.report(catalog, data, "wall_slides")
    assert wall["sides"]["left"]["own_clips_pass"] == "2/2"
    assert wall["other_exercises_checked"] == 4
    assert wall["false_matches"] == []

    prone = calibrate.report(catalog, data, "prone_y")
    assert prone["sides"]["left"]["own_clips_pass"] == "2/2"
    assert prone["sides"]["right"]["own_clips_pass"] == "1/1"
    assert prone["false_matches"] == []
    assert prone["left_minus_right_midpoint"]["wrist_lift"] > 0  # the weaker right arm sits lower

    bear = calibrate.report(catalog, data, "bear_hold")
    assert bear["sides"]["left"]["own_clips_pass"] == "1/1"
    assert bear["false_matches"] == []

    # The snippet pastes straight into exercises.yaml and loads.
    data_yaml = yaml.safe_load(CATALOG_PATH.read_text())
    data_yaml["exercises"]["prone_y"]["bands"] = prone["yaml_snippet"]["prone_y"]["bands"]
    patched = tmp_path / "exercises.yaml"
    patched.write_text(yaml.safe_dump(data_yaml))
    assert set(load_catalog(patched).get("prone_y").bands) == {"left", "right"}


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
def test_mediapipe_end_to_end(tmp_path, catalog):
    import cv2

    from ptverifier.pose import extract

    img = cv2.imread(os.environ["PTV_SAMPLE_IMAGE"])
    h, w = img.shape[:2]
    video = tmp_path / "still.mp4"
    out = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 30, (w, h))
    for _ in range(int(7 * 30)):
        out.write(img)
    out.release()
    track = extract(video, calibrate.DEFAULT_MODEL)
    assert np.isfinite(track.frames[:, 11, 0]).mean() > 0.9
    assert np.all(np.diff(track.t_ms) > 0)
    result = calibrate.analyse(track, catalog.get("wall_slides"), None)
    assert result["position"]["duration_s"] >= 5
