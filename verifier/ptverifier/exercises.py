"""Exercise definitions: load and validate exercises.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from .landmarks import JOINTS

Side = Literal["left", "right"]


class ExerciseConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Metric:
    name: str
    kind: Literal["angle", "height"]
    joints: tuple[str, ...]  # angle: (a, b, c), vertex b. height: (of, above).


@dataclass(frozen=True)
class Exercise:
    key: str
    name: str
    view: str
    kind: Literal["reps", "hold"]
    sides: Literal["both", "per_side"]
    metrics: dict[str, Metric]
    primary: str
    active: Literal["high", "low"]
    target: dict
    laterality: dict[str, float] | None = None
    thresholds: dict | None = None


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
    else:
        raise ExerciseConfigError(f"{ex}.{name}: metric must be angle or height")
    for j in joints:
        if j not in JOINTS:
            raise ExerciseConfigError(f"{ex}.{name}: unknown joint {j!r}")
    return Metric(name, kind, joints)


def _choice(ex: str, raw: dict, key: str, options: tuple[str, ...]) -> str:
    value = raw.get(key)
    if value not in options:
        raise ExerciseConfigError(f"{ex}.{key} must be one of {options}, got {value!r}")
    return value


def load_catalog(path: str | Path) -> Catalog:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise ExerciseConfigError("exercises.yaml: unsupported version")
    exercises: dict[str, Exercise] = {}
    tbd: dict[str, str] = {}
    for key, raw in data["exercises"].items():
        if raw.get("status") == "tbd":
            tbd[key] = raw.get("name", key)
            continue
        metrics = {name: _metric(key, name, m) for name, m in raw["metrics"].items()}
        if raw.get("primary") not in metrics:
            raise ExerciseConfigError(f"{key}.primary must name one of its metrics")
        laterality = raw.get("laterality")
        if laterality is not None and set(laterality) != {"left", "right"}:
            raise ExerciseConfigError(f"{key}.laterality needs left and right")
        exercises[key] = Exercise(
            key=key,
            name=raw["name"],
            view=raw.get("view", "side"),
            kind=_choice(key, raw, "kind", ("reps", "hold")),
            sides=_choice(key, raw, "sides", ("both", "per_side")),
            metrics=metrics,
            primary=raw["primary"],
            active=_choice(key, raw, "active", ("high", "low")),
            target=raw.get("target") or {},
            laterality=laterality,
            thresholds=raw.get("thresholds"),
        )
    sessions = data.get("sessions", {})
    for session, keys in sessions.items():
        for k in keys:
            if k not in exercises and k not in tbd:
                raise ExerciseConfigError(f"session {session} lists unknown exercise {k!r}")
    return Catalog(exercises, tbd, sessions)
