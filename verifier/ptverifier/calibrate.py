"""Calibration mode: learn your own position for each exercise, per side. Signs nothing.

Record one clip per exercise (per side for per-side exercises). In each clip, get into the
exercise's position and hold it still for about 10 seconds. Name the clip

    YYYY-MM-DD_<exercise>_<side>[-note].mp4
    e.g. 2026-10-05_wall_slides_both.mp4, 2026-10-05_prone_y_right-set2.mp4

    python -m ptverifier.calibrate record <video> [--exercise X --side S]
    python -m ptverifier.calibrate watch                # process clips dropped in the inbox folder
    python -m ptverifier.calibrate report [exercise]    # pooled bands per side, as YAML to review

Calibration finds the longest stretch where you held still, measures every metric of the exercise
there, and stores the raw landmarks so bands can be recomputed later. Nothing here touches the
network or a key.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

from . import position, signals
from .exercises import SIDES, Catalog, Exercise, load_catalog
from .landmarks import joint_visibility
from .pose import PoseTrack, extract, sha256_file

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = ROOT / "exercises.yaml"
DEFAULT_DATA = ROOT / "calibration"
DEFAULT_MODEL = ROOT / "models" / "pose_landmarker_full.task"
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".m4v"}
ANGLE_LIMITS = {"angle": (0.0, 180.0), "tilt": (0.0, 180.0), "height": (-np.inf, np.inf)}

FILENAME = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<exercise>[a-z0-9_]+?)_(?P<side>left|right|both)(?:-[\w-]*)?$")


def parse_name(path: Path) -> dict | None:
    m = FILENAME.match(path.stem)
    return m.groupdict() if m else None


# ----------------------------------------------------------------------------------------- analysis


def _timing(t_ms: np.ndarray) -> dict:
    if t_ms.size < 2:
        return {"frames": int(t_ms.size)}
    dt = np.diff(t_ms)
    return {
        "frames": int(t_ms.size),
        "duration_s": float((t_ms[-1] - t_ms[0]) / 1000.0),
        "fps_measured": float(1000.0 / np.median(dt)) if np.median(dt) > 0 else None,
        "frame_gap_ms": {"median": float(np.median(dt)), "p99": float(np.percentile(dt, 99)), "max": float(dt.max())},
        "timestamps_monotonic": bool(np.all(dt > 0)),
    }


def _visibility(frames: np.ndarray, side: str, ex: Exercise) -> float:
    joints = sorted({j for m in ex.metrics.values() for j in m.joints})
    vis = np.stack([joint_visibility(frames, side, j) for j in joints])
    return float(np.nanmedian(np.nanmin(vis, axis=0))) if np.isfinite(vis).any() else 0.0


def pick_side(frames: np.ndarray, ex: Exercise) -> str:
    """For both-sides exercises: the side the camera sees best."""
    return max(SIDES, key=lambda s: _visibility(frames, s, ex))


def analyse(track: PoseTrack, ex: Exercise, side: str | None) -> dict:
    """Finds the longest still stretch on the working side and measures the position there."""
    if ex.sides == "per_side" and side not in SIDES:
        raise ValueError(f"{ex.key} is per-side; give --side left or right")
    side = side if side in SIDES else pick_side(track.frames, ex)
    has_pose = np.isfinite(track.frames[:, 0, 0]) if track.frames.size else np.zeros(0, bool)

    result: dict = {
        "exercise": ex.key,
        "side": side,
        "video": {"width": track.width, "height": track.height, "fps_nominal": track.fps_nominal, **_timing(track.t_ms)},
        "pose_rate": float(has_pose.mean()) if has_pose.size else 0.0,
        "visibility": {s: round(_visibility(track.frames, s, ex), 3) for s in SIDES},
    }
    grid, series = position.metric_series(track.t_ms, track.frames, ex, side)
    kinds = {name: m.kind for name, m in ex.metrics.items()}
    still = signals.spans(grid, signals.still_mask(series, kinds), min_s=ex.min_position_s) if grid.size else []
    best = signals.longest(still)
    if best is None:
        result["position"] = {"error": f"no still stretch of {ex.min_position_s:g}s with every joint visible"}
        return result
    start, dur = best
    inside = (grid >= start) & (grid <= start + dur)
    result["position"] = {
        "start_s": round(start, 2),
        "duration_s": round(dur, 2),
        "metrics": {name: signals.stats(v[inside]) for name, v in series.items()},
        # More than one entry means you also held still in some other posture (e.g. standing around);
        # calibration used the longest. If that wasn't the exercise, re-record with less idle time.
        "still_stretches": [{"start_s": round(s0, 2), "duration_s": round(d, 2)} for s0, d in still],
    }
    if ex.bands and side in ex.bands:
        check = position.check(track.t_ms, track.frames, ex, side)
        result["position"]["current_bands"] = {"held_s": round(check.held_s, 2), "passed": check.passed}
    return result


# ------------------------------------------------------------------------------------------ storage


def save(data_dir: Path, meta: dict, result: dict, track: PoseTrack) -> Path:
    out_dir = data_dir / "results" / result["exercise"]
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{meta['date']}_{result['side']}_{meta['sha256'][:10]}"
    payload = {"version": 2, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **meta, **result}
    (out_dir / f"{stem}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    np.savez_compressed(out_dir / f"{stem}.npz", t_ms=track.t_ms, frames=track.frames.astype(np.float32),
                        size=np.array([track.width, track.height]), fps=np.array([track.fps_nominal]))
    return out_dir / f"{stem}.json"


def load_track(json_path: Path) -> PoseTrack:
    with np.load(json_path.with_suffix(".npz")) as z:
        w, h = (int(x) for x in z["size"])
        return PoseTrack(z["t_ms"], z["frames"].astype(float), w, h, float(z["fps"][0]))


def record(video: Path, catalog: Catalog, model: Path, data_dir: Path, exercise: str | None, side: str | None) -> Path:
    parsed = parse_name(video) or {}
    exercise = exercise or parsed.get("exercise")
    side = side or parsed.get("side")
    if not exercise:
        raise ValueError(f"can't tell the exercise from {video.name}; pass --exercise or rename the file")
    ex = catalog.get(exercise)
    if ex.sides == "per_side" and side not in SIDES:
        raise ValueError(f"{ex.key} is per-side; name the clip ..._left or ..._right")
    track = extract(video, model)
    result = analyse(track, ex, side)
    meta = {
        "date": parsed.get("date") or datetime.now().strftime("%Y-%m-%d"),
        "source_file": video.name,
        "sha256": sha256_file(video),
        "model": {"file": model.name, "sha256": sha256_file(model)},
    }
    return save(data_dir, meta, result, track)


# ------------------------------------------------------------------------------------------- report


def _clips(data_dir: Path) -> list[tuple[Path, dict]]:
    root = data_dir / "results"
    if not root.exists():
        return []
    return [(p, json.loads(p.read_text(encoding="utf-8"))) for p in sorted(root.glob("*/*.json"))]


def pooled_bands(ex: Exercise, results: list[dict]) -> dict[str, tuple[float, float]] | None:
    """Per metric: from the lowest clip p05 to the highest clip p95, widened by BAND_MARGIN."""
    usable = [r["position"]["metrics"] for r in results if "metrics" in r.get("position", {})]
    if not usable:
        return None
    bands = {}
    for name, m in ex.metrics.items():
        lo = min(u[name]["p05"] for u in usable) - signals.BAND_MARGIN[m.kind]
        hi = max(u[name]["p95"] for u in usable) + signals.BAND_MARGIN[m.kind]
        floor, ceil = ANGLE_LIMITS[m.kind]
        bands[name] = (round(max(lo, floor), 3), round(min(hi, ceil), 3))
    return bands


def report(catalog: Catalog, data_dir: Path, exercise: str) -> dict:
    """Pools every clip of one exercise into per-side bands, then checks them two ways:
    every clip of this exercise should pass, and no clip of any other exercise should."""
    ex = catalog.get(exercise)
    every = _clips(data_dir)
    own = [(p, r) for p, r in every if r["exercise"] == exercise]
    others = [(p, r) for p, r in every if r["exercise"] != exercise]

    out: dict = {"exercise": exercise, "min_position_s": ex.min_position_s, "sides": {}}
    bands_by_side = {}
    for side in SIDES:
        results = [r for _, r in own if r["side"] == side]
        if not results:
            continue
        entry: dict = {"clips": len(results)}
        bands = pooled_bands(ex, results)
        if bands is None:
            entry["error"] = "no clip had a usable still stretch"
            out["sides"][side] = entry
            continue
        bands_by_side[side] = bands
        entry["bands"] = {k: list(v) for k, v in bands.items()}
        rows = []
        for p, r in own:
            if r["side"] != side:
                continue
            track = load_track(p)
            c = position.check(track.t_ms, track.frames, ex, side, bands_by_side)
            rows.append({"file": r["source_file"], "held_s": round(c.held_s, 1), "passed": c.passed})
        entry["own_clips_pass"] = f"{sum(x['passed'] for x in rows)}/{len(rows)}"
        entry["per_clip"] = rows
        out["sides"][side] = entry

    if bands_by_side:
        false = []
        for p, r in others:
            track = load_track(p)
            for side in bands_by_side:
                c = position.check(track.t_ms, track.frames, ex, side, bands_by_side)
                if c.passed:
                    false.append({"file": r["source_file"], "exercise": r["exercise"], "side": side, "held_s": round(c.held_s, 1)})
        out["other_exercises_checked"] = len(others)
        out["false_matches"] = false

    if {"left", "right"} <= bands_by_side.keys():
        out["left_minus_right_midpoint"] = {
            name: round(sum(bands_by_side["left"][name]) / 2 - sum(bands_by_side["right"][name]) / 2, 2) for name in ex.metrics
        }
    out["yaml_snippet"] = {exercise: {"bands": {s: {k: list(v) for k, v in b.items()} for s, b in bands_by_side.items()}}}
    return out


# -------------------------------------------------------------------------------------------- watch


def _wait_until_stable(path: Path, checks: int = 3, interval_s: float = 2.0) -> bool:
    """A clip copied over the network grows for a while; wait until its size stops changing."""
    last = -1
    stable = 0
    while stable < checks:
        if not path.exists():
            return False
        size = path.stat().st_size
        stable = stable + 1 if size == last and size > 0 else 0
        last = size
        time.sleep(interval_s)
    return True


def process_inbox_file(path: Path, catalog: Catalog, model: Path, data_dir: Path) -> None:
    done = data_dir / "processed"
    failed = data_dir / "failed"
    if path.suffix.lower() not in VIDEO_EXT or not _wait_until_stable(path):
        return
    try:
        out = record(path, catalog, model, data_dir, None, None)
        done.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), done / path.name)
        print(f"calibrated {path.name} -> {out}", flush=True)
    except Exception as exc:  # keep watching; leave a note next to the clip
        failed.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), failed / path.name)
        (failed / f"{path.name}.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        print(f"FAILED {path.name}: {exc}", file=sys.stderr, flush=True)


def watch(catalog: Catalog, model: Path, data_dir: Path) -> None:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    inbox = data_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    pending: set[Path] = set(p for p in inbox.iterdir() if p.is_file())

    class Handler(FileSystemEventHandler):
        def on_created(self, event):
            if not event.is_directory:
                pending.add(Path(event.src_path))

        def on_moved(self, event):
            if not event.is_directory:
                pending.add(Path(event.dest_path))

    observer = Observer()
    observer.schedule(Handler(), str(inbox), recursive=False)
    observer.start()
    print(f"watching {inbox} (Ctrl+C to stop)", flush=True)
    try:
        while True:
            while pending:
                process_inbox_file(pending.pop(), catalog, model, data_dir)
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()


# ---------------------------------------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m ptverifier.calibrate", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    p.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    p.add_argument("--data", type=Path, default=DEFAULT_DATA, help="calibration data folder (inbox, results, processed)")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="calibrate one clip")
    r.add_argument("video", type=Path)
    r.add_argument("--exercise")
    r.add_argument("--side", choices=["left", "right", "both"])

    sub.add_parser("watch", help="process clips dropped into <data>/inbox")

    rep = sub.add_parser("report", help="pooled per-side bands from all clips so far")
    rep.add_argument("exercise", nargs="?", help="default: every exercise with results")

    a = p.parse_args(argv)
    catalog = load_catalog(a.catalog)
    if a.cmd in ("record", "watch") and not a.model.exists():
        p.error(f"pose model not found at {a.model}; see verifier/README.md for the download")
    if a.cmd == "record":
        out = record(a.video, catalog, a.model, a.data, a.exercise, a.side)
        print(json.dumps(json.loads(out.read_text())["position"], indent=2))
        print(f"saved {out}")
    elif a.cmd == "watch":
        watch(catalog, a.model, a.data)
    else:
        keys = [a.exercise] if a.exercise else sorted({r["exercise"] for _, r in _clips(a.data)})
        if not keys:
            print("no calibration results yet")
        for key in keys:
            print(yaml.safe_dump(report(catalog, a.data, key), sort_keys=False, width=120))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
