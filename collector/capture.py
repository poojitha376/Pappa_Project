"""One-shot capture for the GitHub Actions collector.

Each Actions run invokes this once. It grabs a single Sensibull snapshot, slots it into
the right row of today's data file, recomputes the day's table (values + highlight
colours) and rewrites the JSON that GitHub Pages serves. Git commit/push is done by the
workflow, not here.

    python collector/capture.py                 # capture the slot nearest to now (IST)
    python collector/capture.py --slot 5        # force row 5
    python collector/capture.py --all           # capture every row now (local testing)
    python collector/capture.py --finalize      # mark still-pending past rows as 'missed'
    python collector/capture.py --set-strikes 23500 23600   # update config, then capture
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from core.compute import RAW_FIELDS, build_table            # noqa: E402
from core.schedule_times import SCHEDULE                     # noqa: E402
from sources.sensibull import (contract_lot_size, fetch_snapshot,  # noqa: E402
                               nearest_weekly_expiry)

IST = ZoneInfo("Asia/Kolkata")
CONFIG_PATH = os.path.join(REPO, "config.json")
DATA_DIR = os.path.join(REPO, "data")

# how far from a scheduled time we still accept a capture for it (GitHub cron lags)
ACCEPT_EARLY = 4 * 60         # seconds before the slot
ACCEPT_LATE = 14 * 60        # seconds after the slot
MISS_AFTER = 25 * 60         # seconds past a slot with nothing -> 'missed'


def _now() -> datetime:
    return datetime.now(IST)


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")


def _empty_raw() -> list[dict]:
    return [
        {"row_index": i, "sched_time": t, "captured_at": None, "status": "pending",
         **{k: None for k in RAW_FIELDS}}
        for i, t in SCHEDULE
    ]


def data_path(trade_date: str) -> str:
    return os.path.join(DATA_DIR, f"{trade_date}.json")


def load_day(trade_date: str) -> dict:
    path = data_path(trade_date)
    if os.path.exists(path):
        with open(path) as f:
            day = json.load(f)
        day.setdefault("raw", _empty_raw())
        return day
    return {"date": trade_date, "run": {}, "raw": _empty_raw()}


def write_day(day: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    day["rows"] = build_table(day["raw"])
    day["updated_at"] = _now().isoformat(timespec="seconds")
    with open(data_path(day["date"]), "w") as f:
        json.dump(day, f, indent=1)
        f.write("\n")
    _refresh_index()


def _refresh_index() -> None:
    dates = sorted(
        f[:-5] for f in os.listdir(DATA_DIR)
        if f.endswith(".json") and f != "index.json"
    )
    with open(os.path.join(DATA_DIR, "index.json"), "w") as f:
        json.dump({"dates": dates, "updated_at": _now().isoformat(timespec="seconds")}, f, indent=1)
        f.write("\n")


def _slot_dt(now: datetime, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return now.replace(hour=h, minute=m, second=0, microsecond=0)


def pick_slot(now: datetime, raw: list[dict]) -> int | None:
    """Earliest not-yet-captured slot whose time is near `now`."""
    done = {r["row_index"] for r in raw if r["status"] in ("ok", "partial", "missed")}
    for row in raw:
        if row["row_index"] in done:
            continue
        delta = (now - _slot_dt(now, row["sched_time"])).total_seconds()
        if -ACCEPT_EARLY <= delta <= ACCEPT_LATE:
            return row["row_index"]
    return None


def mark_missed(now: datetime, raw: list[dict], *, force: bool = False) -> int:
    """Mark still-pending rows 'missed' once they are well past their slot
    (or all of them, when `force` — used by the end-of-day finalize run)."""
    n = 0
    for row in raw:
        if row["status"] != "pending":
            continue
        past = (now - _slot_dt(now, row["sched_time"])).total_seconds()
        if force or past > MISS_AFTER:
            row["status"] = "missed"
            row["captured_at"] = None
            n += 1
    return n


def do_capture(cfg: dict, day: dict, row_index: int) -> str:
    row = next(r for r in day["raw"] if r["row_index"] == row_index)
    snap = fetch_snapshot(
        cfg.get("symbol", "NIFTY"), int(cfg.get("underlying_token", 256265)),
        day["run"]["expiry"], cfg["ce_strike"], cfg["pe_strike"],
        lot_size=day["run"].get("lot_size"),
    )
    missing = [k for k in RAW_FIELDS if snap.get(k) is None]
    status = "error" if len(missing) == len(RAW_FIELDS) else "partial" if missing else "ok"
    row.update(snap)
    row["status"] = status
    row["captured_at"] = _now().isoformat(timespec="seconds")
    row["notes"] = ("missing " + ",".join(missing)) if missing else None
    print(f"row {row_index} ({row['sched_time']}) -> {status}: {snap}")
    return status


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", type=int)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--finalize", action="store_true")
    ap.add_argument("--set-strikes", nargs=2, type=float, metavar=("CE", "PE"))
    args = ap.parse_args()

    cfg = load_config()
    if args.set_strikes:
        cfg["ce_strike"], cfg["pe_strike"] = args.set_strikes
        save_config(cfg)
        print(f"config: CE {cfg['ce_strike']} / PE {cfg['pe_strike']}")

    if cfg.get("ce_strike") is None or cfg.get("pe_strike") is None:
        print("no strikes configured; nothing to capture "
              "(set them via the 'Run workflow' button or edit config.json)")
        return 0

    now = _now()
    trade_date = now.date().isoformat()
    day = load_day(trade_date)

    run = day.get("run", {})
    expiry = run.get("expiry") or nearest_weekly_expiry(cfg.get("symbol", "NIFTY"), on=now.date())
    lot_size = run.get("lot_size") or contract_lot_size(cfg.get("symbol", "NIFTY"), expiry)
    day["run"] = {"ce_strike": cfg["ce_strike"], "pe_strike": cfg["pe_strike"],
                  "expiry": expiry, "lot_size": lot_size}

    if args.finalize:
        n = mark_missed(now, day["raw"], force=True)
        print(f"finalize: {n} row(s) marked missed")
        write_day(day)
        return 0

    if args.all:
        for i, _ in SCHEDULE:
            do_capture(cfg, day, i)
        write_day(day)
        return 0

    slot = args.slot or pick_slot(now, day["raw"])
    if slot is None:
        print(f"no slot due near {now:%H:%M} IST")
        mark_missed(now, day["raw"])
        write_day(day)
        return 0

    try:
        do_capture(cfg, day, slot)
    except Exception as exc:                            # noqa: BLE001
        row = next(r for r in day["raw"] if r["row_index"] == slot)
        row["status"] = "error"
        row["notes"] = repr(exc)
        print(f"row {slot} capture FAILED: {exc!r}", file=sys.stderr)
        write_day(day)
        return 1

    mark_missed(now, day["raw"])
    write_day(day)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
