"""Daily jobs on the NUC. Signs no claims (that comes after the contract's fork test passes).

    python -m ptverifier.daily seed      # relayer: seed today if needed, then push the words to the phone
    python -m ptverifier.daily words     # print today's words (no transaction)
    python -m ptverifier.daily verify    # check clips in <sessions_dir>/inbox for today and yesterday's grace

Run `seed` from Task Scheduler a few minutes after the day boundary, and `verify` every 15 minutes or
after Syncthing delivers clips. The relayer keystore password comes from PTV_RELAYER_PASSWORD.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from . import chain, notify
from .calibrate import DEFAULT_CATALOG, DEFAULT_MODEL, VIDEO_EXT
from .config import Config, load_config, session_exercises
from .exercises import Catalog, load_catalog
from .pose import extract
from .session import verify_day
from .words import day_words

ROOT = Path(__file__).resolve().parent.parent


def _local(ts: int) -> str:
    return datetime.fromtimestamp(ts).astimezone().strftime("%a %H:%M %Z")


def _check_schedule(cfg: Config, c: chain.Commitment) -> None:
    on_chain = c.schedule_bitmap()
    if on_chain != cfg.bitmap:
        raise SystemExit(f"config schedule bitmap {cfg.bitmap:#x} does not match the contract's {on_chain:#x}; fix config.yaml")


def _day_dir(cfg: Config, d: int) -> Path:
    return cfg.sessions_dir / "days" / f"day-{d:03d}"


def todays_words(cfg: Config, catalog: Catalog, c: chain.Commitment, d: int, challenge: bytes) -> dict:
    session = cfg.session_for(d)
    keys, tbd = session_exercises(catalog, session)
    words = day_words(challenge, keys)
    info = c.day(d)
    record = {"day": d, "session": session, "challenge": "0x" + challenge.hex(), "words": words, "tbd": tbd,
              "deadline": info.claim_deadline, "deadline_local": _local(info.claim_deadline)}
    out = _day_dir(cfg, d)
    out.mkdir(parents=True, exist_ok=True)
    (out / "words.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def cmd_seed(cfg: Config, catalog: Catalog, c: chain.Commitment, d: int, finalized: bool) -> None:
    session = cfg.session_for(d)
    if session is None:
        notify.send(cfg.ntfy_server, cfg.ntfy_topic, f"Day {d}: rest", "Rest day. Nothing to record.", cfg.ntfy_token)
        print(f"day {d}: rest")
        return
    info = c.day(d)
    if info.state == chain.UNSEEDED:
        if cfg.relayer_keystore is None:
            raise SystemExit("relayer_keystore not set in config.yaml")
        password = os.environ.get("PTV_RELAYER_PASSWORD")
        if password is None:
            raise SystemExit("set PTV_RELAYER_PASSWORD")
        tx = c.seed(d, chain.load_keystore(cfg.relayer_keystore, password))
        print(f"seeded day {d}: {tx}")
    elif info.state != chain.SEEDED:
        print(f"day {d} already {chain.STATE_NAMES[info.state]}")
        return
    challenge = c.wait_finalized(d, finalized=finalized)
    record = todays_words(cfg, catalog, c, d, challenge)
    names = {k: catalog.exercises[k].name for k in record["words"]}
    title, body = notify.words_message(d, session, record["words"], names, record["deadline_local"], record["tbd"])
    notify.send(cfg.ntfy_server, cfg.ntfy_topic, title, body, cfg.ntfy_token)
    print(f"{title}\n{body}")


def _ledger_path(cfg: Config) -> Path:
    return cfg.sessions_dir / "used_videos.json"


def _load_ledger(cfg: Config) -> dict[str, int]:
    p = _ledger_path(cfg)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def cmd_verify(cfg: Config, catalog: Catalog, c: chain.Commitment, days: list[int], transcriber, extractor) -> list[dict]:
    inbox = cfg.sessions_dir / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    reports = []
    ledger = _load_ledger(cfg)
    for d in days:
        session = cfg.session_for(d)
        info = c.day(d) if session else None
        if not session or info.state != chain.SEEDED or c.now() > info.claim_deadline:
            continue
        clips = [p for p in inbox.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXT]
        clips += [p for p in _day_dir(cfg, d).glob("*") if p.suffix.lower() in VIDEO_EXT]
        report = verify_day(d, info.challenge, session, catalog, clips, transcriber, extractor, ledger, cfg.max_frame_gap_s)
        report["deadline_local"] = _local(info.claim_deadline)
        # Matched clips move out of the inbox into the day's folder and are recorded as used for this day.
        day_dir = _day_dir(cfg, d)
        day_dir.mkdir(parents=True, exist_ok=True)
        for ex in report["exercises"].values():
            clip = ex["clip"]
            if clip and ex["passed"]:
                ledger[clip["sha256"]] = d
            if clip and (inbox / clip["file"]).exists():
                shutil.move(str(inbox / clip["file"]), day_dir / clip["file"])
        (day_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        reports.append(report)
    _ledger_path(cfg).write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    return reports


def cmd_cleanup(cfg: Config) -> list[Path]:
    """Deletes videos older than retention_days from inbox and day folders. Reports and the ledger stay."""
    if cfg.retention_days is None:
        return []
    cutoff = time.time() - cfg.retention_days * 86400
    gone = []
    for p in list((cfg.sessions_dir / "inbox").glob("*")) + list((cfg.sessions_dir / "days").glob("*/*")):
        if p.suffix.lower() in VIDEO_EXT and p.stat().st_mtime < cutoff:
            p.unlink()
            gone.append(p)
    return gone


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m ptverifier.daily", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    p.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    p.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    p.add_argument("--day", type=int, help="contract day index (default: today)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("--latest", action="store_true", help="use the latest block instead of waiting for finality (testing only)")
    sub.add_parser("words")
    sub.add_parser("verify")
    sub.add_parser("cleanup")
    a = p.parse_args(argv)

    cfg = load_config(a.config)
    catalog = load_catalog(a.catalog)
    if a.cmd == "cleanup":
        for g in cmd_cleanup(cfg):
            print(f"deleted {g}")
        return 0
    c = chain.Commitment(cfg.rpc_url, cfg.contract)
    _check_schedule(cfg, c)
    today = a.day if a.day is not None else c.current_day()
    if today is None:
        print("commitment hasn't started yet")
        return 0

    if a.cmd == "seed":
        cmd_seed(cfg, catalog, c, today, finalized=not a.latest)
    elif a.cmd == "words":
        session = cfg.session_for(today)
        if session is None:
            print(f"day {today}: rest")
        else:
            info = c.day(today)
            if info.state == chain.UNSEEDED:
                print(f"day {today} isn't seeded yet")
                return 1
            print(json.dumps(todays_words(cfg, catalog, c, today, info.challenge), indent=2))
    elif a.cmd == "verify":
        from .transcribe import WhisperTranscriber

        transcriber = WhisperTranscriber(cfg.whisper_model)
        days = [a.day] if a.day is not None else [d for d in (today - 1, today) if d >= 0]
        for r in cmd_verify(cfg, catalog, c, days, transcriber, lambda v: extract(v, a.model)):
            status = "PASS" if r["passed"] else "not yet"
            print(f"day {r['day']} {r['session']}: {status} ({r['score']}/{len(r['exercises'])}) deadline {r['deadline_local']}")
            for k, e in r["exercises"].items():
                print(f"  {k:24s} {e['word']:12s} {'ok' if e['passed'] else e['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
