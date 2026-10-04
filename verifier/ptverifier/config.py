"""Daily-run configuration (config.yaml) and the schedule: which session falls on which contract day."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .exercises import Catalog

# Schedule letters, the same pattern given to script/start_time.py bitmap. Any letter but R is active.
SESSION_LETTERS = {"A": "PT-A", "B": "PT-B", "W": "Walk-A"}
REST = "R"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    rpc_url: str
    contract: str
    schedule: str  # one letter per contract day, '-' ignored
    sessions_dir: Path  # inbox/ is where Syncthing drops phone clips
    ntfy_server: str
    ntfy_topic: str
    ntfy_token: str | None
    relayer_keystore: Path | None
    verifier_keystore: Path | None
    whisper_model: str
    max_frame_gap_s: float
    retention_days: int | None

    @property
    def days(self) -> list[str]:
        return [c for c in self.schedule.upper() if c != "-"]

    @property
    def bitmap(self) -> int:
        return sum(1 << i for i, c in enumerate(self.days) if c != REST)

    def session_for(self, day: int) -> str | None:
        """Session name for a contract day, or None for a rest day / out of range."""
        if not 0 <= day < len(self.days) or self.days[day] == REST:
            return None
        return SESSION_LETTERS[self.days[day]]


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    schedule = str(raw["schedule"])
    bad = {c for c in schedule.upper() if c != "-" and c != REST and c not in SESSION_LETTERS}
    if bad:
        raise ConfigError(f"schedule has unknown letters {sorted(bad)}; use {sorted(SESSION_LETTERS)} and {REST}")
    ntfy = raw.get("ntfy") or {}
    if not ntfy.get("topic"):
        raise ConfigError("ntfy.topic is required (pick a long random name; anyone who knows it can read the words)")
    keystore = raw.get("relayer_keystore")
    vkeystore = raw.get("verifier_keystore")
    return Config(
        rpc_url=raw["rpc_url"],
        contract=raw["contract"],
        schedule=schedule,
        sessions_dir=Path(raw["sessions_dir"]),
        ntfy_server=ntfy.get("server", "https://ntfy.sh").rstrip("/"),
        ntfy_topic=ntfy["topic"],
        ntfy_token=ntfy.get("token"),
        relayer_keystore=Path(keystore) if keystore else None,
        verifier_keystore=Path(vkeystore) if vkeystore else None,
        whisper_model=raw.get("whisper_model", "small.en"),
        max_frame_gap_s=float(raw.get("max_frame_gap_s", 0.5)),
        retention_days=raw.get("retention_days"),
    )


def session_exercises(catalog: Catalog, session: str) -> tuple[list[str], list[str]]:
    """(verifiable exercise keys, TBD exercise keys) for a session, in session order."""
    keys = catalog.sessions[session]
    return [k for k in keys if k in catalog.exercises], [k for k in keys if k in catalog.tbd]
