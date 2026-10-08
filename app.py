"""FastAPI dashboard: live table + strike inputs.

Reads captured rows from SQLite and renders them with the highlight rules applied.
The heavy lifting (feed + scheduler) lives in core.service, started by run.py.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from core import db, service
from core.compute import RAW_FIELDS, build_table
from core.schedule_times import SCHEDULE
from core.service import StrikesLockedError

SCHED_TIME_BY_ROW = dict(SCHEDULE)

BASE_DIR = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

app = FastAPI(title="Pappa live options collector")


@app.on_event("startup")
def _startup() -> None:
    db.init()


def _raw_rows_for(trade_date: str):
    run = db.get_run(trade_date)
    samples = {}
    if run is not None:
        samples = {s["row_index"]: s for s in db.get_samples(run["id"])}
    rows = []
    for row_index, hhmm in SCHEDULE:
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
    return run, rows


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {})


@app.get("/api/status")
def api_status():
    svc = service.SERVICE
    if svc is None:
        return {"running": False, "note": "scheduler not started (run.py not running)"}
    return {"running": True, **svc.status()}


@app.get("/api/dates")
def api_dates():
    svc = service.SERVICE
    today = svc.today_str() if svc else None
    dates = db.list_trade_dates()
    if today and today not in dates:
        dates = [today] + dates
    return {"dates": dates, "today": today}


@app.get("/api/table")
def api_table(date: str | None = None):
    svc = service.SERVICE
    if date is None:
        date = svc.today_str() if svc else (db.list_trade_dates() or [None])[0]
    if date is None:
        return {"date": None, "rows": [], "run": None}
    run, raw_rows = _raw_rows_for(date)
    table = build_table(raw_rows)
    return JSONResponse({
        "date": date,
        "run": None if run is None else {
            "ce_strike": run["ce_strike"],
            "pe_strike": run["pe_strike"],
            "expiry": run["expiry"],
        },
        "rows": table,
    })


class ConfigIn(BaseModel):
    ce_strike: float | None = None
    pe_strike: float | None = None


@app.post("/api/config")
def api_config(cfg: ConfigIn):
    svc = service.SERVICE
    if svc is None:
        raise HTTPException(503, "scheduler not started; run.py is not running")
    for name, val in (("ce_strike", cfg.ce_strike), ("pe_strike", cfg.pe_strike)):
        if val is not None and val <= 0:
            raise HTTPException(422, f"{name} must be positive")
    try:
        return svc.reconfigure(cfg.ce_strike, cfg.pe_strike)
    except StrikesLockedError as exc:
        raise HTTPException(423, str(exc)) from exc


class ManualEntryIn(BaseModel):
    row_index: int
    ce_chng_oi: float | None = None
    pe_chng_oi: float | None = None
    ce_vol: float | None = None
    pe_vol: float | None = None
    ce_price: float | None = None
    pe_price: float | None = None


@app.post("/api/manual")
def api_manual(entry: ManualEntryIn, date: str | None = None):
    """Fill in one row by hand — e.g. a checkpoint that passed before today's strikes
    were saved. Goes through the same db.save_sample + core.compute.build_table path
    as an automatic capture, so the derived columns and highlight colours are worked
    out identically either way."""
    svc = service.SERVICE
    if svc is None:
        raise HTTPException(503, "scheduler not started; run.py is not running")
    trade_date = date or svc.today_str()
    run = db.get_run(trade_date)
    if run is None:
        raise HTTPException(409, "save today's strikes before entering a row by hand")
    sched_time = SCHED_TIME_BY_ROW.get(entry.row_index)
    if sched_time is None:
        raise HTTPException(422, "unknown row")
    if trade_date == svc.today_str() and sched_time > svc.now().strftime("%H:%M"):
        raise HTTPException(422, f"{sched_time} hasn't happened yet — it'll capture itself")

    values = {f: getattr(entry, f) for f in RAW_FIELDS}
    if all(v is None for v in values.values()):
        raise HTTPException(422, "enter at least one value")
    missing = [f for f, v in values.items() if v is None]
    status = "manual" if not missing else "partial"

    db.save_sample(run["id"], entry.row_index, sched_time, values=values,
                   status=status, notes="manual entry" + (f"; missing {','.join(missing)}" if missing else ""))
    svc.export_xlsx(run["id"], trade_date)
    return {"ok": True, "row_index": entry.row_index, "status": status}
