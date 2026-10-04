"""Verify one contract day from the clips the phone synced over. Signs nothing.

A day passes when every verifiable exercise in its session has a clip in which:
  1. that exercise's word for the day is the first of the day's words spoken (this is also how the clip
     is matched to the exercise, so phone filenames don't matter),
  2. frame timestamps run forward with no gap over max_frame_gap_s,
  3. after the word, the position is held in band for min_position_s: one side for both-sides exercises,
     each side for per-side exercises (turn around partway through the clip),
  4. the video file hasn't already counted for an earlier day.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from . import position
from .exercises import SIDES, Catalog
from .pose import PoseTrack, sha256_file
from .transcribe import Transcriber
from .words import day_words, find_word


@dataclass
class ClipCheck:
    file: str
    sha256: str
    exercise: str
    word_at_s: float
    passed: bool = False
    held_s: dict[str, float] = field(default_factory=dict)
    reason: str | None = None


def _continuity(track: PoseTrack, max_gap_s: float) -> str | None:
    if track.t_ms.size < 2:
        return "clip has no frames"
    dt = np.diff(track.t_ms)
    if not np.all(dt > 0):
        return "frame timestamps go backwards (edited or spliced clip)"
    if dt.max() > max_gap_s * 1000:
        return f"frame gap of {dt.max() / 1000:.2f}s (limit {max_gap_s}s): clip looks cut"
    return None


def check_clip(path: Path, sha: str, key: str, word_at: float, catalog: Catalog, extract: Callable[[Path], PoseTrack], max_gap_s: float) -> ClipCheck:
    ex = catalog.get(key)
    result = ClipCheck(path.name, sha, key, round(word_at, 2))
    if not ex.bands:
        result.reason = f"{key} has no calibrated bands"
        return result
    track = extract(path)
    if problem := _continuity(track, max_gap_s):
        result.reason = problem
        return result
    if ex.sides == "per_side":
        missing = [s for s in SIDES if s not in ex.bands]
        if missing:
            result.reason = f"{key} not calibrated for {', '.join(missing)}"
            return result
        checks = [position.check(track.t_ms, track.frames, ex, s, after_s=word_at) for s in SIDES]
    else:
        checks = [position.check(track.t_ms, track.frames, ex, after_s=word_at)]
    result.held_s = {c.side: round(c.held_s, 1) for c in checks}
    result.passed = all(c.passed for c in checks)
    if not result.passed:
        short = [f"{c.side} held {c.held_s:.1f}s" for c in checks if not c.passed]
        result.reason = f"position not held {ex.min_position_s:g}s after the word ({'; '.join(short)})"
    return result


def verify_day(
    day: int,
    challenge: bytes,
    session: str,
    catalog: Catalog,
    clips: list[Path],
    transcriber: Transcriber,
    extract: Callable[[Path], PoseTrack],
    used: dict[str, int],
    max_gap_s: float = 0.5,
) -> dict:
    """`used` maps video sha256 -> the day it already counted for."""
    keys = [k for k in catalog.sessions[session] if k in catalog.exercises]
    tbd = [k for k in catalog.sessions[session] if k in catalog.tbd]
    words = day_words(challenge, keys)

    checks: list[ClipCheck] = []
    unmatched: list[str] = []
    for path in sorted(clips):
        sha = sha256_file(path)
        spoken = transcriber.words(path)
        heard = {k: t for k, w in words.items() if (t := find_word(spoken, w)) is not None}
        if not heard:
            unmatched.append(path.name)
            continue
        key = min(heard, key=heard.get)
        if sha in used and used[sha] != day:
            checks.append(ClipCheck(path.name, sha, key, round(heard[key], 2), reason=f"video already counted for day {used[sha]}"))
            continue
        checks.append(check_clip(path, sha, key, heard[key], catalog, extract, max_gap_s))

    exercises = {}
    for k in keys:
        mine = [c for c in checks if c.exercise == k]
        best = next((c for c in mine if c.passed), mine[0] if mine else None)
        exercises[k] = {
            "word": words[k],
            "passed": bool(best and best.passed),
            "clip": asdict(best) if best else None,
            "reason": None if best and best.passed else (best.reason if best else "no clip with this word"),
        }
    score = sum(e["passed"] for e in exercises.values())
    return {
        "day": day,
        "session": session,
        "challenge": "0x" + challenge.hex(),
        "exercises": exercises,
        "not_verified_tbd": tbd,
        "unmatched_clips": unmatched,
        "score": score,
        "passed": bool(keys) and score == len(keys),
        "note": None if keys else f"{session} has no verifiable exercises yet; this day can't pass",
    }
