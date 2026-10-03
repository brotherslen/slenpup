"""Calibration mode: measure your own joint-angle ranges per exercise and side. Signs nothing.

Record one clip per exercise (per side for per-side exercises), named

    YYYY-MM-DD_<exercise>_<side>_<count>[-anything].mp4
    e.g. 2026-10-05_wall_slides_both_10.mp4, 2026-10-05_prone_y_right_4-set2.mp4

where <count> is the reps you actually did (or holds, for hold exercises). The count is what lets the
report check its suggested thresholds against reality.

    python -m ptverifier.calibrate record <video> [--exercise X --side S --count N]
    python -m ptverifier.calibrate watch                # process clips dropped in the inbox folder
    python -m ptverifier.calibrate report [exercise]    # pooled thresholds per side, as YAML to review

Nothing here touches the network or a key. Results are JSON plus a .npz of the smoothed series.
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

from . import signals
from .exercises import Catalog, Exercise, load_catalog
from .landmarks import compute_metric, joint_visibility
from .pose import PoseTrack, extract, sha256_file

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CATALOG = ROOT / "exercises.yaml"
DEFAULT_DATA = ROOT / "calibration"
DEFAULT_MODEL = ROOT / "models" / "pose_landmarker_full.task"
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".m4v"}
MIN_VISIBILITY = 0.5

FILENAME = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})_(?P<exercise>[a-z0-9_]+?)_(?P<side>left|right|both)_(?P<count>\d+)(?:-[\w-]*)?$"
)


def parse_name(path: Path) -> dict | None:
    m = FILENAME.match(path.stem)
    if not m:
        return None
    d = m.groupdict()
    d["count"] = int(d["count"])
    return d


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


def _pick_side(track: PoseTrack, ex: Exercise) -> str:
    """For both-sides exercises: the side the camera sees best, by visibility of the primary metric's joints."""
    joints = ex.metrics[ex.primary].joints
    score = {}
    for side in ("left", "right"):
        vis = np.stack([joint_visibility(track.frames, side, j) for j in joints])
        score[side] = float(np.nanmedian(np.nanmin(vis, axis=0))) if np.isfinite(vis).any() else 0.0
    return max(score, key=score.get)


def analyse(track: PoseTrack, ex: Exercise, side: str | None, declared: int | None) -> tuple[dict, dict[str, np.ndarray]]:
    """Returns (result, series). `side` is the working side; None (or "both") picks the camera side."""
    if ex.sides == "per_side" and side not in ("left", "right"):
        raise ValueError(f"{ex.key} is per-side; give --side left or right")
    side = side if side in ("left", "right") else _pick_side(track, ex)

    has_pose = np.isfinite(track.frames[:, 0, 0])
    result: dict = {
        "exercise": ex.key,
        "side": side,
        "declared_count": declared,
        "kind": ex.kind,
        "video": {"width": track.width, "height": track.height, "fps_nominal": track.fps_nominal, **_timing(track.t_ms)},
        "pose_rate": float(has_pose.mean()) if has_pose.size else 0.0,
        "visibility": {},
        "metrics": {},
    }
    joints = sorted({j for m in ex.metrics.values() for j in m.joints})
    for s in ("left", "right"):
        result["visibility"][s] = {j: float(np.nanmedian(joint_visibility(track.frames, s, j))) if has_pose.any() else 0.0 for j in joints}

    series: dict[str, np.ndarray] = {}
    grid = None
    for name, m in ex.metrics.items():
        result["metrics"][name] = {}
        for s in ("left", "right"):
            raw = compute_metric(track.frames, s, m.kind, m.joints, MIN_VISIBILITY)
            g, v = signals.resample(track.t_ms, raw)
            v = signals.smooth(v)
            result["metrics"][name][s] = signals.stats(v)
            series[f"{name}.{s}"] = v
            series[f"{name}.{s}.t"] = g
            if name == ex.primary and s == side:
                grid = g

    primary = series[f"{ex.primary}.{side}"]
    if grid is None or grid.size == 0:
        result["primary"] = {"metric": ex.primary, "error": "no usable frames for the primary metric on this side"}
        return result, series

    result["primary"] = {"metric": ex.primary, "active": ex.active, "valid_fraction": float(np.isfinite(primary).mean())}
    if ex.kind == "reps":
        extremes = signals.find_extremes(grid, primary, ex.active, signals.MIN_SPAN[ex.metrics[ex.primary].kind])
        suggestion = signals.suggest_rep_thresholds(extremes)
        result["primary"].update(
            {
                "active_extremes": [round(float(x), 3) for x in extremes.active],
                "rest_extremes": [round(float(x), 3) for x in extremes.rest],
                "suggested": suggestion,
                "detected_with_suggested": signals.count_reps(primary, suggestion["enter"], suggestion["exit"], ex.active) if suggestion else None,
            }
        )
    else:
        suggestion = signals.suggest_hold_threshold(primary)
        holds = signals.find_holds(grid, primary, suggestion["threshold"], ex.active) if suggestion else []
        result["primary"].update(
            {
                "suggested": suggestion,
                "holds_with_suggested": [{"start_s": round(s0, 2), "duration_s": round(d, 2)} for s0, d in holds],
            }
        )
    return result, series


# ------------------------------------------------------------------------------------------ storage


def save(data_dir: Path, meta: dict, result: dict, series: dict[str, np.ndarray]) -> Path:
    out_dir = data_dir / "results" / result["exercise"]
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{meta['date']}_{result['side']}_{meta['sha256'][:10]}"
    payload = {"version": 1, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **meta, **result}
    (out_dir / f"{stem}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    np.savez_compressed(out_dir / f"{stem}.npz", **series)
    return out_dir / f"{stem}.json"


def record(video: Path, catalog: Catalog, model: Path, data_dir: Path, exercise: str | None, side: str | None, count: int | None) -> Path:
    parsed = parse_name(video) or {}
    exercise = exercise or parsed.get("exercise")
    side = side or parsed.get("side")
    count = count if count is not None else parsed.get("count")
    if not exercise:
        raise ValueError(f"can't tell the exercise from {video.name}; pass --exercise or rename the file")
    ex = catalog.get(exercise)
    track = extract(video, model)
    result, series = analyse(track, ex, side, count)
    meta = {
        "date": parsed.get("date") or datetime.now().strftime("%Y-%m-%d"),
        "source_file": video.name,
        "sha256": sha256_file(video),
        "model": {"file": model.name, "sha256": sha256_file(model)},
    }
    return save(data_dir, meta, result, series)


# ------------------------------------------------------------------------------------------- report


def _load_results(data_dir: Path, exercise: str) -> list[tuple[dict, dict]]:
    out = []
    for path in sorted((data_dir / "results" / exercise).glob("*.json")):
        result = json.loads(path.read_text(encoding="utf-8"))
        with np.load(path.with_suffix(".npz")) as z:
            out.append((result, {k: z[k] for k in z.files}))
    return out


def report(catalog: Catalog, data_dir: Path, exercise: str) -> dict:
    """Pools every calibration clip for one exercise into per-side thresholds, then re-counts each clip
    with the pooled thresholds so you can see how they'd have done against what you actually did."""
    ex = catalog.get(exercise)
    clips = _load_results(data_dir, exercise)
    by_side: dict[str, list[tuple[dict, dict]]] = {}
    for result, series in clips:
        by_side.setdefault(result["side"], []).append((result, series))

    out: dict = {"exercise": exercise, "kind": ex.kind, "sides": {}}
    for side, items in sorted(by_side.items()):
        suggestions = [r["primary"].get("suggested") for r, _ in items if r["primary"].get("suggested")]
        entry: dict = {"clips": len(items)}
        if not suggestions:
            entry["error"] = "no clip produced a usable suggestion"
            out["sides"][side] = entry
            continue
        if ex.kind == "reps":
            rest = float(np.median([s["rest_median"] for s in suggestions]))
            act = float(np.median([s["active_median"] for s in suggestions]))
            pooled = {"enter": round(rest + 0.65 * (act - rest), 3), "exit": round(rest + 0.35 * (act - rest), 3)}
            entry["rest_median"], entry["active_median"] = round(rest, 3), round(act, 3)
            rows = []
            for r, s in items:
                v = s[f"{ex.primary}.{side}"]
                rows.append({"date": r["date"], "file": r["source_file"], "declared": r["declared_count"], "detected": signals.count_reps(v, pooled["enter"], pooled["exit"], ex.active)})
        else:
            pooled = {"threshold": round(float(np.median([s["threshold"] for s in suggestions])), 3)}
            rows = []
            for r, s in items:
                v, t = s[f"{ex.primary}.{side}"], s[f"{ex.primary}.{side}.t"]
                holds = signals.find_holds(t, v, pooled["threshold"], ex.active)
                rows.append({"date": r["date"], "file": r["source_file"], "declared": r["declared_count"], "detected": len(holds), "durations_s": [round(d, 1) for _, d in holds]})
        entry["thresholds"] = pooled
        entry["per_clip"] = rows
        entry["matches_declared"] = f"{sum(1 for x in rows if x['declared'] == x['detected'])}/{len(rows)}"
        out["sides"][side] = entry

    if ex.kind == "reps" and {"left", "right"} <= out["sides"].keys():
        l, r = out["sides"]["left"], out["sides"]["right"]
        if "active_median" in l and "active_median" in r:
            out["asymmetry"] = {
                "active_left": l["active_median"],
                "active_right": r["active_median"],
                "range_left": round(l["active_median"] - l["rest_median"], 3),
                "range_right": round(r["active_median"] - r["rest_median"], 3),
            }
    out["yaml_snippet"] = {exercise: {"thresholds": {s: e["thresholds"] for s, e in out["sides"].items() if "thresholds" in e}}}
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
        out = record(path, catalog, model, data_dir, None, None, None)
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
    r.add_argument("--count", type=int, help="reps (or holds) you actually did")

    sub.add_parser("watch", help="process clips dropped into <data>/inbox")

    rep = sub.add_parser("report", help="pooled per-side thresholds from all clips so far")
    rep.add_argument("exercise", nargs="?", help="default: every exercise with results")

    a = p.parse_args(argv)
    catalog = load_catalog(a.catalog)
    if a.cmd in ("record", "watch") and not a.model.exists():
        p.error(f"pose model not found at {a.model}; see verifier/README.md for the download")
    if a.cmd == "record":
        out = record(a.video, catalog, a.model, a.data, a.exercise, a.side, a.count)
        print(json.dumps(json.loads(out.read_text())["primary"], indent=2))
        print(f"saved {out}")
    elif a.cmd == "watch":
        watch(catalog, a.model, a.data)
    else:
        results = a.data / "results"
        keys = [a.exercise] if a.exercise else sorted(d.name for d in results.iterdir() if d.is_dir()) if results.exists() else []
        if not keys:
            print("no calibration results yet")
        for key in keys:
            print(yaml.safe_dump(report(catalog, a.data, key), sort_keys=False, width=120))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
