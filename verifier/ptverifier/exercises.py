"""Exercise definitions: load and validate exercises.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from .landmarks import JOINTS

SIDES = ("left", "right")


class ExerciseConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Metric:
    name: str
    kind: Literal["angle", "height", "tilt"]
    joints: tuple[str, ...]  # angle: (a, b, c), vertex b. height: (of, above). tilt: (from, to).


@dataclass(frozen=True)
class Exercise:
    key: str
    name: str
    view: str
    sides: Literal["both", "per_side"]
    metrics: dict[str, Metric]
    min_position_s: float
    bands: dict[str, dict[str, tuple[float, float]]] | None = None  # side -> metric -> (low, high)


@dataclass(frozen=True)
class Catalog:
    exercises: dict[str, Exercise]
    tbd: dict[str, str]
    sessions: dict[str, list[str]] = field(default_factory=dict)

    def get(self, key: str) -> Exercise:
        if key in self.tbd:
            raise ExerciseConfigError(f"{key} is marked TBD in exercises.yaml; fill it in first")
        if key not in self.exercises:
            raise ExerciseConfigError(f"unknown exercise {key!r}; known: {', '.join(sorted(self.exercises))}")
        return self.exercises[key]


def _metric(ex: str, name: str, raw: dict) -> Metric:
    if "angle" in raw:
        joints = tuple(raw["angle"])
        if len(joints) != 3:
            raise ExerciseConfigError(f"{ex}.{name}: angle needs 3 joints")
        kind = "angle"
    elif "height" in raw:
        joints = (raw["height"]["of"], raw["height"]["above"])
        kind = "height"
    elif "tilt" in raw:
        joints = (raw["tilt"]["from"], raw["tilt"]["to"])
        kind = "tilt"
    else:
        raise ExerciseConfigError(f"{ex}.{name}: metric must be angle, height, or tilt")
    for j in joints:
        if j not in JOINTS:
            raise ExerciseConfigError(f"{ex}.{name}: unknown joint {j!r}")
    return Metric(name, kind, joints)


def _bands(ex: str, raw, metrics: dict[str, Metric]):
    if raw is None:
        return None
    out = {}
    for side, per_metric in raw.items():
        if side not in SIDES:
            raise ExerciseConfigError(f"{ex}.bands: side must be left or right, got {side!r}")
        if set(per_metric) != set(metrics):
            raise ExerciseConfigError(f"{ex}.bands.{side}: needs exactly the metrics {sorted(metrics)}")
        out[side] = {}
        for name, band in per_metric.items():
            lo, hi = (float(x) for x in band)
            if not lo < hi:
                raise ExerciseConfigError(f"{ex}.bands.{side}.{name}: low must be below high")
            out[side][name] = (lo, hi)
    return out


def load_catalog(path: str | Path) -> Catalog:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if data.get("version") != 2:
        raise ExerciseConfigError("exercises.yaml: unsupported version (expected 2)")
    default_min = float(data.get("min_position_s", 5))
    exercises: dict[str, Exercise] = {}
    tbd: dict[str, str] = {}
    for key, raw in data["exercises"].items():
        if raw.get("status") == "tbd":
            tbd[key] = raw.get("name", key)
            continue
        metrics = {name: _metric(key, name, m) for name, m in raw["metrics"].items()}
        sides = raw.get("sides")
        if sides not in ("both", "per_side"):
            raise ExerciseConfigError(f"{key}.sides must be both or per_side, got {sides!r}")
        exercises[key] = Exercise(
            key=key,
            name=raw["name"],
            view=raw.get("view", "side"),
            sides=sides,
            metrics=metrics,
            min_position_s=float(raw.get("min_position_s", default_min)),
            bands=_bands(key, raw.get("bands"), metrics),
        )
    sessions = data.get("sessions", {})
    for session, keys in sessions.items():
        for k in keys:
            if k not in exercises and k not in tbd:
                raise ExerciseConfigError(f"session {session} lists unknown exercise {k!r}")
    return Catalog(exercises, tbd, sessions)
