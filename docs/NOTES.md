# Notes (how it works)

Built from `Reqs.txt`. Samples NIFTY option data at 27 fixed IST times per trading day,
computes derived columns, applies a highlight scheme, shows it as a table.

## Two ways to run

- **Local** (`run.py`) — persistent Sensibull websocket + scheduler + FastAPI dashboard
  on port 8000, captures written to `data.db` (SQLite). This is what the README covers.
- **GitHub Actions + Pages** (`.github/workflows/collect.yml` + `collector/capture.py` +
  `index.html`) — a scheduled workflow captures rows into `data/<date>.json` and Pages
  serves a static dashboard. Not currently in use; kept for later.

## Columns (0-indexed, per Reqs.txt)

| # | meaning | source |
|---|---|---|
| 0 | scheduled time | schedule |
| 1 | CE change in OI (contracts) | Sensibull `oi_change_quantity` ÷ lot size |
| 2 | PE change in OI (contracts) | Sensibull `oi_change_quantity` ÷ lot size |
| 3 | col1 curr − prev row | computed |
| 4 | col2 curr − prev row | computed |
| 5 | col3 − col4 | computed |
| 6 | CE volume (contracts) | Sensibull `volume` ÷ lot size |
| 7 | PE volume (contracts) | Sensibull `volume` ÷ lot size |
| 8 | col6 curr ÷ prev row | computed |
| 9 | col7 curr ÷ prev row | computed |
| 10 | col8 − col9 | computed |
| 11 | CE price (LTP) | Sensibull `last_price` |
| 12 | PE price (LTP) | Sensibull `last_price` |

Row 1 has no previous row, so cols 3,4,5,8,9,10 are blank there.

## Highlight rules

- col 0 → yellow; cols 1,2,6,7 → orange (only where a value was captured).
- col 11 / col 12, row 1 → yellow.
- col 11 (paired with col 1) and col 12 (paired with col 2), rows 2+:
  price unchanged → yellow · OI↑ & price↑ → green · OI↑ & price↓ → red ·
  OI↓ & price↓ → blue · OI↓ & price↑ → purple.
- col 1 and col 2: first cell where a continuous up/down run reverses → red.

Colours are defined in `core/compute.py`.

## Data source

NSE's own option-chain API blocks server-side clients and automating Zerodha Kite breaks
their ToS, so everything comes from **one unauthenticated Sensibull websocket**
(`wss://wsrelay.sensibull.com`, option-chain feed). Protocol details are in
`sources/sensibull.py`. Scraping Sensibull is against their terms — personal, low-frequency
use only.

- OI-change / volume come in *shares*; dividing by the expiry's lot size (NIFTY = 65 as of
  Sep 2026, looked up live) gives contracts, matching nseindia.com/option-chain.
- The free feed pushes a full snapshot roughly every ~45 s, so a captured price can be up
  to ~1 min old — fine against 10–15 min sampling. Faster option: the `quote-batch` feed.

## Assumptions

- `Reqs.txt` times past noon ("12:13am" …) are 24-h IST.
- Contiguity break (rule 10): direction set by the first non-equal step; an equal value is
  not a break, only a reversal is.
- Late start / laptop asleep → those rows show `missed`, no backfill.

## Not built yet

Google Sheets mirror (Reqs §4–6). `core/compute.py` already produces everything a
`sources/sheets.py` would need.

## Tests

`python tests/test_compute.py` — covers every derived value and highlight rule.
