"""SQLite storage. One row per (trade day, scheduled time).

Raw captured values only; the derived columns (3,4,5,8,9,10) and the highlight colours
are computed on read in `core.compute` so there is a single source of truth.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data.db")

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT UNIQUE NOT NULL,          -- 'YYYY-MM-DD' (IST)
    ce_strike   REAL,
    pe_strike   REAL,
    expiry      TEXT,                          -- 'YYYY-MM-DD'
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS samples (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL REFERENCES runs(id),
    row_index    INTEGER NOT NULL,             -- 1..27
    sched_time   TEXT NOT NULL,                -- 'HH:MM' IST
    captured_at  TEXT,                         -- ISO-8601 UTC, null if missed
    ce_chng_oi   REAL,
    pe_chng_oi   REAL,
    ce_vol       REAL,
    pe_vol       REAL,
    ce_price     REAL,
    pe_price     REAL,
    status       TEXT NOT NULL DEFAULT 'pending',  -- ok | partial | error | missed | pending
    notes        TEXT,
    UNIQUE (run_id, row_index)
);
"""


def _conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn = c
    return c


def init() -> None:
    c = _conn()
    c.executescript(SCHEMA)
    c.commit()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- runs

def upsert_run(trade_date: str, ce_strike, pe_strike, expiry) -> int:
    """Create the day's run or update its strikes/expiry. Returns run id."""
    c = _conn()
    row = c.execute("SELECT id FROM runs WHERE trade_date = ?", (trade_date,)).fetchone()
    if row is None:
        cur = c.execute(
            "INSERT INTO runs (trade_date, ce_strike, pe_strike, expiry, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (trade_date, ce_strike, pe_strike, expiry, _utcnow()),
        )
        c.commit()
        return cur.lastrowid
    c.execute(
        "UPDATE runs SET ce_strike = ?, pe_strike = ?, expiry = ? WHERE id = ?",
        (ce_strike, pe_strike, expiry, row["id"]),
    )
    c.commit()
    return row["id"]


def get_run(trade_date: str):
    return _conn().execute(
        "SELECT * FROM runs WHERE trade_date = ?", (trade_date,)
    ).fetchone()


def list_trade_dates() -> list[str]:
    rows = _conn().execute(
        "SELECT trade_date FROM runs ORDER BY trade_date DESC"
    ).fetchall()
    return [r["trade_date"] for r in rows]


# ------------------------------------------------------------------------ samples

def save_sample(run_id: int, row_index: int, sched_time: str, *,
                values: dict | None, status: str, notes: str | None = None) -> None:
    """Insert or replace one captured row. `values` holds the 6 raw numbers or None."""
    v = values or {}
    captured_at = _utcnow() if status in ("ok", "partial") else None
    c = _conn()
    c.execute(
        """
        INSERT INTO samples (run_id, row_index, sched_time, captured_at,
                             ce_chng_oi, pe_chng_oi, ce_vol, pe_vol,
                             ce_price, pe_price, status, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (run_id, row_index) DO UPDATE SET
            captured_at = excluded.captured_at,
            ce_chng_oi  = excluded.ce_chng_oi,
            pe_chng_oi  = excluded.pe_chng_oi,
            ce_vol      = excluded.ce_vol,
            pe_vol      = excluded.pe_vol,
            ce_price    = excluded.ce_price,
            pe_price    = excluded.pe_price,
            status      = excluded.status,
            notes       = excluded.notes
        """,
        (run_id, row_index, sched_time, captured_at,
         v.get("ce_chng_oi"), v.get("pe_chng_oi"), v.get("ce_vol"), v.get("pe_vol"),
         v.get("ce_price"), v.get("pe_price"), status, notes),
    )
    c.commit()


def get_samples(run_id: int) -> list[sqlite3.Row]:
    return _conn().execute(
        "SELECT * FROM samples WHERE run_id = ? ORDER BY row_index", (run_id,)
    ).fetchall()


def captured_row_indexes(run_id: int) -> set[int]:
    rows = _conn().execute(
        "SELECT row_index FROM samples WHERE run_id = ? AND status IN ('ok','partial','missed')",
        (run_id,),
    ).fetchall()
    return {r["row_index"] for r in rows}


def raw_rows_for_run(run_id: int, schedule: list[tuple[int, str]]) -> list[dict]:
    """One dict per scheduled slot (pending if not yet captured) — the input shape
    core.compute.build_table expects. Pure data assembly, no calculation."""
    samples = {s["row_index"]: s for s in get_samples(run_id)}
    rows = []
    for row_index, hhmm in schedule:
        s = samples.get(row_index)
        rows.append({
            "row_index": row_index,
            "sched_time": hhmm,
            "captured_at": s["captured_at"] if s else None,
            "status": s["status"] if s else "pending",
            "ce_chng_oi": s["ce_chng_oi"] if s else None,
            "pe_chng_oi": s["pe_chng_oi"] if s else None,
            "ce_vol": s["ce_vol"] if s else None,
            "pe_vol": s["pe_vol"] if s else None,
            "ce_price": s["ce_price"] if s else None,
            "pe_price": s["pe_price"] if s else None,
        })
    return rows
