"""Daily jobs on the NUC.

    python -m ptverifier.daily seed      # relayer: seed today if needed, then push the words to the phone
    python -m ptverifier.daily words     # print today's words (no transaction)
    python -m ptverifier.daily verify    # check inbox clips for today (and yesterday's grace); claim a passed day
    python -m ptverifier.daily verify --no-claim   # check only

Run `seed` from Task Scheduler a few minutes after the day boundary, and `verify` every 15 minutes.
Keystore passwords come from the OS credential store (see ptverifier.keys).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from . import chain, claim, keys, notify
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
        tx = c.seed(d, keys.load("relayer", cfg.relayer_keystore))
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


def _summary(report: dict) -> tuple[str, str]:
    d, total = report["day"], len(report["exercises"])
    if report.get("claim_tx"):
        return f"Day {d} claimed", f"{report['score']}/{total} positions confirmed. Tranche credited to you."
    lines = [f"{k}: {'ok' if e['passed'] else e['reason']}" for k, e in report["exercises"].items()]
    if report.get("claim_error"):
        lines.append(f"claim failed: {report['claim_error']}")
    lines.append(f"Deadline {report['deadline_local']}.")
    return f"Day {d}: {report['score']}/{total} so far", "\n".join(lines)


def _notify_if_changed(cfg: Config, day_dir: Path, report: dict) -> None:
    """One push per change in status, and nothing until at least one clip for the day has arrived."""
    if not any(e["clip"] for e in report["exercises"].values()):
        return
    title, body = _summary(report)
    marker = day_dir / "last_notice.txt"
    text = f"{title}\n{body}"
    if marker.exists() and marker.read_text(encoding="utf-8") == text:
        return
    notify.send(cfg.ntfy_server, cfg.ntfy_topic, title, body, cfg.ntfy_token)
    marker.write_text(text, encoding="utf-8")


def cmd_verify(
    cfg: Config, catalog: Catalog, c: chain.Commitment, days: list[int], transcriber, extractor, signer: tuple[bytes, bytes] | None = None
) -> list[dict]:
    """signer = (verifier key, relayer key) to claim passed days; None to check only."""
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
        if report["passed"] and signer:
            try:
                report["claim_tx"] = claim.submit(c, report, *signer)
            except Exception as exc:  # report it and keep the clips; the next run retries
                report["claim_error"] = str(exc)
        (day_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        _notify_if_changed(cfg, day_dir, report)
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
    v = sub.add_parser("verify")
    v.add_argument("--no-claim", action="store_true", help="check clips but don't sign or submit")
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
        signer = None
        if not a.no_claim:
            if not (cfg.verifier_keystore and cfg.relayer_keystore):
                raise SystemExit("verifier_keystore and relayer_keystore must be set to claim (or pass --no-claim)")
            signer = (keys.load("verifier", cfg.verifier_keystore), keys.load("relayer", cfg.relayer_keystore))
        for r in cmd_verify(cfg, catalog, c, days, transcriber, lambda v: extract(v, a.model), signer):
            status = "CLAIMED" if r.get("claim_tx") else "PASS" if r["passed"] else "not yet"
            if r.get("claim_error"):
                print(f"  claim error: {r['claim_error']}")
            print(f"day {r['day']} {r['session']}: {status} ({r['score']}/{len(r['exercises'])}) deadline {r['deadline_local']}")
            for k, e in r["exercises"].items():
                print(f"  {k:24s} {e['word']:12s} {'ok' if e['passed'] else e['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
