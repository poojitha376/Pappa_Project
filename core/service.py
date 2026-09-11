"""Runtime service: owns the Sensibull feed and the sampling scheduler.

One instance is created by run.py and shared with the FastAPI app (for status and
for applying strike changes). The scheduler runs in its own thread and writes each
captured row straight to SQLite.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from core import db, xlsx_export
from core.compute import build_table
from core.schedule_times import SCHEDULE, LAST_ROW_INDEX
from sources.sensibull import SensibullFeed, nearest_weekly_expiry

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")

CAPTURE_WINDOW = 180        # seconds after a scheduled time we still capture it
MISS_AFTER = CAPTURE_WINDOW # older than this and still uncaptured -> 'missed'
SIM_INTERVAL = 2            # seconds between rows in --simulate mode

SERVICE: "Service | None" = None       # set by run.py; read by app.py


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")


class Service:
    def __init__(self, simulate: bool = False):
        self.cfg = load_config()
        self.simulate = simulate
        self.tz = ZoneInfo(self.cfg.get("timezone", "Asia/Kolkata"))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._expiry_cache: dict[str, str] = {}      # trade_date -> expiry
        self.feed: SensibullFeed | None = None
        self._thread: threading.Thread | None = None
        self.last_capture: dict | None = None

    # -- config --------------------------------------------------------------

    @property
    def ce_strike(self):
        return self.cfg.get("ce_strike")

    @property
    def pe_strike(self):
        return self.cfg.get("pe_strike")

    def reconfigure(self, ce_strike, pe_strike) -> dict:
        with self._lock:
            self.cfg["ce_strike"] = ce_strike
            self.cfg["pe_strike"] = pe_strike
            save_config(self.cfg)
        today = self.today_str()
        expiry = self._expiry_for(today)
        if ce_strike is not None and pe_strike is not None:
            db.upsert_run(today, ce_strike, pe_strike, expiry)
        return {"ce_strike": ce_strike, "pe_strike": pe_strike, "expiry": expiry}

    # -- time helpers -------------------------------------------------------

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def today_str(self) -> str:
        return self.now().date().isoformat()

    def _scheduled_dt(self, day: datetime, hhmm: str) -> datetime:
        h, m = map(int, hhmm.split(":"))
        return day.replace(hour=h, minute=m, second=0, microsecond=0)

    # -- expiry -----------------------------------------------------------

    def _expiry_for(self, trade_date: str) -> str | None:
        if trade_date in self._expiry_cache:
            return self._expiry_cache[trade_date]
        try:
            exp = nearest_weekly_expiry(self.cfg.get("symbol", "NIFTY"),
                                        on=datetime.fromisoformat(trade_date).date())
            self._expiry_cache[trade_date] = exp
            return exp
        except Exception as exc:                     # noqa: BLE001
            self._log(f"expiry lookup failed: {exc!r}")
            return next(iter(self._expiry_cache.values()), None)

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        db.init()
        today = self.today_str()
        expiry = self._expiry_for(today) or "1970-01-01"
        self.feed = SensibullFeed(
            self.cfg.get("symbol", "NIFTY"),
            int(self.cfg.get("underlying_token", 256265)),
            expiry,
        )
        self.feed.start()
        if self.ce_strike is not None and self.pe_strike is not None:
            db.upsert_run(today, self.ce_strike, self.pe_strike, expiry)
        self._thread = threading.Thread(
            target=self._simulate_loop if self.simulate else self._scheduler_loop,
            name="scheduler", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.feed:
            self.feed.stop()

    # -- capture ----------------------------------------------------------

    def _log(self, msg: str) -> None:
        print(f"[{datetime.now():%H:%M:%S}] scheduler: {msg}", flush=True)

    def capture(self, run_id: int, row_index: int, sched_time: str,
                trade_date: str | None = None) -> dict:
        trade_date = trade_date or self.today_str()
        ce, pe = self.ce_strike, self.pe_strike
        snap = self.feed.snapshot(ce, pe) if self.feed else {}
        missing = [k for k in ("ce_chng_oi", "pe_chng_oi", "ce_vol",
                               "pe_vol", "ce_price", "pe_price") if snap.get(k) is None]
        fresh = self.feed.is_fresh() if self.feed else False

        if len(missing) == 6:
            status, notes = "error", "no feed data" + ("" if fresh else " (feed stale)")
        elif missing:
            status = "partial"
            notes = "missing " + ",".join(missing) + ("" if fresh else "; feed stale")
        else:
            status, notes = "ok", None if fresh else "feed stale"

        db.save_sample(run_id, row_index, sched_time, values=snap,
                       status=status, notes=notes)
        self.last_capture = {
            "at": datetime.now(self.tz).isoformat(timespec="seconds"),
            "row_index": row_index, "sched_time": sched_time, "status": status,
        }
        self._log(f"row {row_index} ({sched_time}) -> {status} {snap}")
        self._export_xlsx(run_id, trade_date)
        return self.last_capture

    def _export_xlsx(self, run_id: int, trade_date: str) -> None:
        """Mirror the day's current table (same values, same colours the dashboard
        shows — computed by the unchanged core.compute.build_table) into Pappa.xlsx."""
        try:
            raw_rows = db.raw_rows_for_run(run_id, SCHEDULE)
            table = build_table(raw_rows)
            xlsx_export.export_day(trade_date, table, db.list_trade_dates())
        except Exception as exc:                            # noqa: BLE001
            self._log(f"xlsx export failed: {exc!r}")

    def _ensure_run(self, trade_date: str) -> int | None:
        if self.ce_strike is None or self.pe_strike is None:
            return None
        expiry = self._expiry_for(trade_date)
        if self.feed and expiry:
            self.feed.set_expiry(expiry)
        return db.upsert_run(trade_date, self.ce_strike, self.pe_strike, expiry)

    # -- loops ----------------------------------------------------------

    def _scheduler_loop(self) -> None:
        self._log(f"started (tz={self.tz}, simulate=False)")
        while not self._stop.is_set():
            now = self.now()
            trade_date = now.date().isoformat()
            run_id = self._ensure_run(trade_date)

            if run_id is None:
                self._stop.wait(30)
                continue

            done = db.captured_row_indexes(run_id)
            next_dt = None
            for row_index, hhmm in SCHEDULE:
                if row_index in done:
                    continue
                dt = self._scheduled_dt(now, hhmm)
                delta = (now - dt).total_seconds()
                if delta < 0:
                    next_dt = dt if next_dt is None else min(next_dt, dt)
                elif delta <= CAPTURE_WINDOW:
                    self.capture(run_id, row_index, hhmm, trade_date)
                    done.add(row_index)
                else:
                    db.save_sample(run_id, row_index, hhmm, values=None,
                                   status="missed", notes="not running at scheduled time")
                    self._export_xlsx(run_id, trade_date)
                    done.add(row_index)

            if next_dt is None:
                self._stop.wait(60)                  # nothing left today; roll over
            else:
                wait = max(0.0, (next_dt - self.now()).total_seconds())
                self._stop.wait(min(wait, 60))

    def _simulate_loop(self) -> None:
        self._log("started in --simulate mode (compressed day)")
        while not self._stop.is_set() and (self.ce_strike is None or self.pe_strike is None):
            self._log("waiting for CE/PE strikes to be set on the dashboard…")
            self._stop.wait(5)
        if self._stop.is_set():
            return
        trade_date = self.today_str()
        run_id = self._ensure_run(trade_date)
        # let the feed warm up
        for _ in range(60):
            if self._stop.is_set() or (self.feed and self.feed.is_fresh()):
                break
            time.sleep(1)
        for row_index, hhmm in SCHEDULE:
            if self._stop.is_set():
                return
            self.capture(run_id, row_index, hhmm)
            self._stop.wait(SIM_INTERVAL)
        self._log("simulate run complete")

    # -- status ---------------------------------------------------------

    def status(self) -> dict:
        return {
            "now": self.now().isoformat(timespec="seconds"),
            "trade_date": self.today_str(),
            "symbol": self.cfg.get("symbol", "NIFTY"),
            "expiry": self._expiry_cache.get(self.today_str()),
            "ce_strike": self.ce_strike,
            "pe_strike": self.pe_strike,
            "feed_fresh": self.feed.is_fresh() if self.feed else False,
            "feed_age_s": self.feed.age_seconds() if self.feed else None,
            "lot_size": self.feed.lot_size if self.feed else None,
            "last_capture": self.last_capture,
            "simulate": self.simulate,
        }
