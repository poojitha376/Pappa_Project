"""Sensibull option-chain feed.

Single unauthenticated source for every raw column the dashboard needs:

    col 1  CE CHNG IN OI  <- chain[strike]["call"]["oi_change_quantity"]
    col 2  PE CHNG IN OI  <- chain[strike]["put"]["oi_change_quantity"]
    col 6  CE Vol         <- chain[strike]["call"]["volume"]
    col 7  PE Vol         <- chain[strike]["put"]["volume"]
    col 11 CE price        <- chain[strike]["call"]["last_price"]
    col 12 PE price        <- chain[strike]["put"]["last_price"]

Discovered by reverse-engineering web.sensibull.com's bundle (see the project plan).
Note: scraping Sensibull is against their site terms; this is intended only for
personal, low-frequency use.
"""
from __future__ import annotations

import json
import struct
import threading
import time
import zlib
from datetime import date, datetime

import requests
import websocket

INSTRUMENTS_URL = "https://api.sensibull.com/v1/instruments/{symbol}"
WS_URL = "wss://wsrelay.sensibull.com/broker/2?consumerType=platform_no_plan"
WS_ORIGIN = "https://web.sensibull.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# The anonymous option-chain feed pushes a full snapshot roughly every ~45s (plus a
# 15s heartbeat ping). So "fresh" is generous and a reconnect only fires after several
# missed snapshots. A capture value can therefore be up to ~1 min old, which is well
# inside the 10-15 min spacing of the sampling schedule.
STALE_AFTER = 150         # seconds without an option-chain frame -> treat as stale
RECONNECT_AFTER = 150     # seconds of total silence (no ping either) -> reconnect
RECV_TIMEOUT = 20


def get_instruments(symbol: str) -> list[dict]:
    r = requests.get(INSTRUMENTS_URL.format(symbol=symbol),
                     headers={"User-Agent": UA}, timeout=15)
    r.raise_for_status()
    return r.json()["data"]


def nearest_weekly_expiry(symbol: str, on: date | None = None) -> str:
    """Earliest option expiry that is today or later, as 'YYYY-MM-DD'."""
    on = on or date.today()
    expiries = sorted({
        d["expiry"] for d in get_instruments(symbol)
        if not d.get("is_underlying") and d.get("expiry")
    })
    for e in expiries:
        if e >= on.isoformat():
            return e
    raise RuntimeError(f"no future expiry found for {symbol}")


def contract_lot_size(symbol: str, expiry: str) -> int:
    """Lot size (shares per contract) for that expiry's options.

    Sensibull reports OI-change and volume in *shares*; NSE's option-chain page shows
    them in *contracts*. Dividing by this makes cols 1,2,6,7 match NSE.
    """
    sizes = [
        int(d["lot_size"]) for d in get_instruments(symbol)
        if not d.get("is_underlying") and d.get("expiry") == expiry and d.get("lot_size")
    ]
    if not sizes:
        raise RuntimeError(f"no lot size for {symbol} {expiry}")
    return max(set(sizes), key=sizes.count)              # most common


def parse_option_chain_packet(buf: bytes) -> tuple[int, str, dict]:
    """buf[0] == 3. Returns (underlying_token, 'YYYYMMDD', chain_json)."""
    token = struct.unpack(">i", buf[1:5])[0]
    packet_date = buf[5:13].decode("ascii")
    payload = zlib.decompress(buf[13:], 47)          # gzip
    return token, packet_date, json.loads(payload)


def _subscribe_payload(underlying_token: int, expiry: str) -> str:
    return json.dumps({
        "msgCommand": "subscribe",
        "dataSource": "option-chain",
        "brokerId": 2,
        "tokens": [],
        "underlyingExpiry": [{"underlying": underlying_token, "expiry": expiry}],
        "uniqueId": "",
    })


def extract_row(chain: dict, ce_strike, pe_strike, lot_size: int) -> dict:
    """Pull the 6 values for the two strikes out of a decoded chain.

    OI-change and volume are divided by `lot_size` so they are in contracts, matching
    what nseindia.com/option-chain shows. Price is per-share, left as-is.
    """
    ce = _leg(chain, ce_strike, "call")
    pe = _leg(chain, pe_strike, "put")
    ls = lot_size or 1
    return {
        "ce_chng_oi": _div(_num(ce, "oi_change_quantity"), ls),
        "pe_chng_oi": _div(_num(pe, "oi_change_quantity"), ls),
        "ce_vol": _div(_num(ce, "volume"), ls),
        "pe_vol": _div(_num(pe, "volume"), ls),
        "ce_price": _num(ce, "last_price"),
        "pe_price": _num(pe, "last_price"),
    }


def fetch_snapshot(symbol: str, underlying_token: int, expiry: str,
                   ce_strike, pe_strike, *, lot_size: int | None = None,
                   timeout: float = 90) -> dict:
    """One-shot: connect, wait for one option-chain packet, return the 6 values.

    Used by the GitHub Actions collector (no long-lived process). Raises on failure to
    receive any packet within `timeout` seconds.
    """
    if lot_size is None:
        lot_size = contract_lot_size(symbol, expiry)
    deadline = time.time() + timeout
    ws = websocket.create_connection(
        WS_URL, timeout=15, origin=WS_ORIGIN, header=[f"User-Agent: {UA}"],
    )
    try:
        ws.settimeout(RECV_TIMEOUT)
        ws.send(_subscribe_payload(underlying_token, expiry))
        while time.time() < deadline:
            try:
                msg = ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not msg or isinstance(msg, str) or msg[0] != 3:
                continue
            _tok, _d, obj = parse_option_chain_packet(msg)
            return extract_row(obj.get("chain", {}), ce_strike, pe_strike, lot_size)
        raise TimeoutError(f"no option-chain packet within {timeout}s")
    finally:
        try:
            ws.close()
        except Exception:                              # noqa: BLE001
            pass


class SensibullFeed:
    """Persistent websocket that keeps the latest option chain in memory."""

    def __init__(self, symbol: str, underlying_token: int, expiry: str,
                 lot_size: int | None = None, *, verbose=True):
        self.symbol = symbol
        self.underlying_token = underlying_token
        self.expiry = expiry
        self.lot_size = lot_size or contract_lot_size(symbol, expiry)
        self.verbose = verbose

        self._chain: dict = {}
        self._meta: dict = {}
        self._updated_at: float = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sensibull-feed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def set_expiry(self, expiry: str, lot_size: int | None = None) -> None:
        """Switch to a new expiry (e.g. when the trade date rolls over)."""
        if expiry != self.expiry:
            self._log(f"expiry {self.expiry} -> {expiry}, reconnecting")
            self.expiry = expiry
            self.lot_size = lot_size or contract_lot_size(self.symbol, expiry)
            with self._lock:
                self._chain = {}
                self._updated_at = 0.0

    # -- reads -----------------------------------------------------------------

    def is_fresh(self) -> bool:
        return (time.time() - self._updated_at) < STALE_AFTER and bool(self._chain)

    def age_seconds(self) -> float | None:
        return None if not self._updated_at else round(time.time() - self._updated_at, 1)

    def snapshot(self, ce_strike, pe_strike) -> dict:
        """The 6 values for the two strikes (OI-change & volume in contracts,
        matching NSE). Missing legs come back as None."""
        with self._lock:
            chain = self._chain
        return extract_row(chain, ce_strike, pe_strike, self.lot_size)

    # -- internals -----------------------------------------------------------

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[{datetime.now():%H:%M:%S}] sensibull: {msg}", flush=True)

    def _subscribe_msg(self) -> str:
        return _subscribe_payload(self.underlying_token, self.expiry)

    def _run(self) -> None:
        backoff = 1
        while not self._stop.is_set():
            ws = None
            try:
                ws = websocket.create_connection(
                    WS_URL, timeout=15, origin=WS_ORIGIN, header=[f"User-Agent: {UA}"],
                )
                ws.settimeout(RECV_TIMEOUT)
                ws.send(self._subscribe_msg())
                self._log(f"connected, subscribed {self.symbol} {self.expiry}")
                backoff = 1
                last_oc = time.time()                       # last option-chain packet
                current_expiry = self.expiry

                while not self._stop.is_set():
                    if self.expiry != current_expiry:      # expiry switched under us
                        break
                    try:
                        msg = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        if time.time() - last_oc > RECONNECT_AFTER:
                            self._log("no option-chain data, reconnecting")
                            break
                        continue
                    if not msg:
                        continue
                    if isinstance(msg, str) or msg[0] != 3:
                        continue                            # 253 = ping, ignore
                    last_oc = time.time()
                    try:
                        _tok, _d, obj = parse_option_chain_packet(msg)
                    except Exception as exc:                # noqa: BLE001
                        self._log(f"bad packet: {exc!r}")
                        continue
                    with self._lock:
                        self._chain = obj.get("chain", {})
                        self._meta = {k: v for k, v in obj.items() if k != "chain"}
                        self._updated_at = time.time()
            except Exception as exc:                        # noqa: BLE001
                self._log(f"connection error: {exc!r}")
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:                       # noqa: BLE001
                        pass
            if not self._stop.is_set():
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)


# --------------------------------------------------------------------------- helpers

def _strike_key(chain: dict, strike) -> str | None:
    if strike is None:
        return None
    for cand in (str(int(float(strike))), str(strike), f"{float(strike):.1f}"):
        if cand in chain:
            return cand
    return None


def _leg(chain: dict, strike, side: str) -> dict | None:
    key = _strike_key(chain, strike)
    if key is None:
        return None
    return chain.get(key, {}).get(side)


def _num(leg: dict | None, field: str):
    if not leg:
        return None
    val = leg.get(field)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _div(val, lot_size: int):
    """Shares -> contracts, rounded to a whole number (as NSE displays it)."""
    return None if val is None else round(val / lot_size)


if __name__ == "__main__":
    import sys

    sym = "NIFTY"
    ce = float(sys.argv[1]) if len(sys.argv) > 1 else 23500
    pe = float(sys.argv[2]) if len(sys.argv) > 2 else 23600
    exp = nearest_weekly_expiry(sym)
    ls = contract_lot_size(sym, exp)
    print(f"{sym} nearest expiry: {exp}  lot size: {ls}  "
          f"(OI-change & volume shown in contracts, like NSE)")
    feed = SensibullFeed(sym, 256265, exp, ls)
    feed.start()
    try:
        for _ in range(10):
            time.sleep(3)
            print(f"age={feed.age_seconds()}s  fresh={feed.is_fresh()}  "
                  f"{feed.snapshot(ce, pe)}")
    finally:
        feed.stop()
