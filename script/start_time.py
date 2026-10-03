#!/usr/bin/env python3
"""Deploy-parameter helper for PTCommitment. Standard library only.

  start   Compute START_TIME (UTC unix seconds) for a local boundary hour in any IANA time zone.
  show    Print a START_TIME in UTC and in a time zone, across both DST seasons.
  bitmap  Turn a day pattern into NUM_DAYS and SCHEDULE_BITMAP.

The chain has no DST, so the boundary is one fixed UTC hour. --anchor picks which local season
the boundary hour is exact in; in the other season it lands an hour off.

Examples:
  python3 script/start_time.py start --tz America/Chicago --date 2026-10-20 --hour 4 --anchor standard
  python3 script/start_time.py show 1792656000 --tz America/Chicago --grace-hours 6
  python3 script/start_time.py bitmap ABWR-ABWR-ABWR      # any letter = active day, R = rest; '-' ignored
"""
import argparse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def season_offsets(tz: ZoneInfo, year: int) -> tuple[timedelta, timedelta]:
    jan = datetime(year, 1, 15, 12, tzinfo=tz).utcoffset()
    jul = datetime(year, 7, 15, 12, tzinfo=tz).utcoffset()
    return min(jan, jul), max(jan, jul)  # (standard, daylight)


def cmd_start(a):
    tz = ZoneInfo(a.tz)
    day = datetime.strptime(a.date, "%Y-%m-%d")
    standard, daylight = season_offsets(tz, day.year)
    offset = standard if a.anchor == "standard" else daylight
    local_naive = day.replace(hour=a.hour)
    utc = local_naive - offset
    ts = int(utc.replace(tzinfo=timezone.utc).timestamp())
    assert ts % 3600 == 0, "offset is not a whole hour; contract requires START_TIME on the hour"
    print(ts)
    show(ts, tz, a.grace_hours)


def show(ts: int, tz: ZoneInfo, grace_hours: float):
    utc = datetime.fromtimestamp(ts, timezone.utc)
    print(f"START_TIME={ts}")
    print(f"  UTC:   {utc:%Y-%m-%d %H:%M} (boundary {utc:%H}:00 UTC every day)")
    print(f"  local: {utc.astimezone(tz):%Y-%m-%d %H:%M %Z}  ({tz.key})")
    standard, daylight = season_offsets(tz, utc.year)
    for name, off in (("standard time", standard), ("daylight time", daylight)):
        b = (utc + off).time()
        dl = (utc + off + timedelta(days=1, hours=grace_hours)).time()
        print(f"  during {name}: day starts {b:%H:%M}, claim deadline next day {dl:%H:%M}")


def cmd_show(a):
    show(a.start_time, ZoneInfo(a.tz), a.grace_hours)


def cmd_bitmap(a):
    days = [ch for ch in a.pattern if ch != "-"]
    if not 1 <= len(days) <= 256:
        raise SystemExit("pattern must have 1..256 days")
    bitmap = 0
    for i, ch in enumerate(days):
        if ch.upper() != "R":
            bitmap |= 1 << i
    print(f"NUM_DAYS={len(days)}")
    print(f"SCHEDULE_BITMAP={hex(bitmap)}")
    print(f"active days: {bin(bitmap).count('1')}, rest days: {len(days) - bin(bitmap).count('1')}")
    for i, ch in enumerate(days):
        print(f"  day {i:3d}: {'rest' if ch.upper() == 'R' else ch}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(required=True)

    s = sub.add_parser("start")
    s.add_argument("--tz", required=True, help="IANA zone, e.g. America/Chicago")
    s.add_argument("--date", required=True, help="local date of day 0, YYYY-MM-DD")
    s.add_argument("--hour", type=int, required=True, help="local boundary hour, 0-23")
    s.add_argument("--anchor", choices=["standard", "daylight"], required=True)
    s.add_argument("--grace-hours", type=float, default=6)
    s.set_defaults(func=cmd_start)

    s = sub.add_parser("show")
    s.add_argument("start_time", type=int)
    s.add_argument("--tz", required=True)
    s.add_argument("--grace-hours", type=float, default=6)
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("bitmap")
    s.add_argument("pattern")
    s.set_defaults(func=cmd_bitmap)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
