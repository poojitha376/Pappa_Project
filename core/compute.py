"""Derived columns (3,4,5,8,9,10) and the per-cell highlight colours.

Column layout (0-indexed, matching Reqs.txt):

    0  minute / scheduled time
    1  CE  CHNG IN OI            (raw)
    2  PE  CHNG IN OI            (raw)
    3  col1[i] - col1[i-1]
    4  col2[i] - col2[i-1]
    5  col3 - col4
    6  CE  Vol                   (raw)
    7  PE  Vol                   (raw)
    8  col6[i] / col6[i-1]
    9  col7[i] / col7[i-1]
    10 col8 - col9
    11 CE  price                 (raw)
    12 PE  price                 (raw)

`build_table` takes raw rows (one per scheduled time, already ordered) and returns
display rows with formatted text + a {col_index: "#hex"} colour map.
"""
from __future__ import annotations

# "not too light" highlight palette (tunable)
YELLOW = "#F5D547"
ORANGE = "#F6A653"
GREEN = "#79C879"
RED = "#EE6B6B"
BLUE = "#6FA8F5"
PURPLE = "#B98BD9"

RAW_FIELDS = ("ce_chng_oi", "pe_chng_oi", "ce_vol", "pe_vol", "ce_price", "pe_price")


def _fmt_int(v):
    return "" if v is None else f"{round(v):,}"


def _fmt_ratio(v):
    return "" if v is None else f"{v:.4f}"


def _fmt_price(v):
    return "" if v is None else f"{v:.2f}"


def _diff(a, b):
    return None if a is None or b is None else a - b


def _ratio(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def build_table(raw_rows: list[dict]) -> list[dict]:
    """raw_rows: dicts with row_index, sched_time, captured_at, status + RAW_FIELDS."""
    out: list[dict] = []
    series: dict[int, list] = {1: [], 2: [], 8: [], 9: []}   # col -> [(row_pos, value)]

    for i, r in enumerate(raw_rows):
        prev = raw_rows[i - 1] if i > 0 else None

        c1, c2 = r.get("ce_chng_oi"), r.get("pe_chng_oi")
        c6, c7 = r.get("ce_vol"), r.get("pe_vol")
        c11, c12 = r.get("ce_price"), r.get("pe_price")

        p1 = prev.get("ce_chng_oi") if prev else None
        p2 = prev.get("pe_chng_oi") if prev else None
        p6 = prev.get("ce_vol") if prev else None
        p7 = prev.get("pe_vol") if prev else None
        p11 = prev.get("ce_price") if prev else None
        p12 = prev.get("pe_price") if prev else None

        c3 = _diff(c1, p1)
        c4 = _diff(c2, p2)
        c5 = _diff(c3, c4)
        c8 = _ratio(c6, p6)
        c9 = _ratio(c7, p7)
        c10 = _diff(c8, c9)

        text = {
            0: r["sched_time"],
            1: _fmt_int(c1), 2: _fmt_int(c2),
            3: _fmt_int(c3), 4: _fmt_int(c4), 5: _fmt_int(c5),
            6: _fmt_int(c6), 7: _fmt_int(c7),
            8: _fmt_ratio(c8), 9: _fmt_ratio(c9), 10: _fmt_ratio(c10),
            11: _fmt_price(c11), 12: _fmt_price(c12),
        }

        colors = {0: YELLOW}
        for col, val in ((1, c1), (2, c2), (6, c6), (7, c7)):
            if val is not None:                       # don't paint empty (missed) cells
                colors[col] = ORANGE

        if i == 0:
            if c11 is not None:
                colors[11] = YELLOW
            if c12 is not None:
                colors[12] = YELLOW
        else:
            col11c = _price_color(c1, p1, c11, p11)
            if col11c:
                colors[11] = col11c
            col12c = _price_color(c2, p2, c12, p12)
            if col12c:
                colors[12] = col12c

        for col, val in ((1, c1), (2, c2), (8, c8), (9, c9)):
            if val is not None:
                series[col].append((i, val))

        out.append({
            "row_index": r["row_index"],
            "sched_time": r["sched_time"],
            "captured_at": r.get("captured_at"),
            "status": r.get("status", "pending"),
            "text": text,
            "colors": colors,
        })

    # rule 10a: every cell in col 1 / col 2 where the up/down direction reverses -> red
    _mark_direction_changes(series[1], out, 1, RED)
    _mark_direction_changes(series[2], out, 2, RED)
    # rule 10b: same for col 8 / col 9 -> yellow
    _mark_direction_changes(series[8], out, 8, YELLOW)
    _mark_direction_changes(series[9], out, 9, YELLOW)
    return out


def _price_color(oi_curr, oi_prev, price_curr, price_prev):
    """Rules 0/1/2/3/4 for a price cell (col 11 uses CE OI, col 12 uses PE OI)."""
    if price_curr is None or price_prev is None:
        return None
    if price_curr == price_prev:
        return YELLOW                                   # rule 0a / 0b
    if oi_curr is None or oi_prev is None:
        return None
    oi_up = oi_curr > oi_prev
    oi_down = oi_curr < oi_prev
    price_up = price_curr > price_prev
    if oi_up and price_up:
        return GREEN                                    # rule 1
    if oi_up and not price_up:
        return RED                                      # rule 2
    if oi_down and not price_up:
        return BLUE                                     # rule 3
    if oi_down and price_up:
        return PURPLE                                   # rule 4
    return None                                         # oi unchanged: unspecified


def _mark_direction_changes(pts: list, out: list[dict], col: int, color: str):
    """Rule 10: colour every cell where the sequence flips inc<->dec.

    `pts` is [(row_position, value), ...] for the rows that have a value in this column.
    Direction of each step is compared with the step before it; a flip colours the row
    at the end of the flipping step. Runs across the whole day. Ties count as "dec"
    (per Reqs "if prev->next then inc else dec").
    """
    if len(pts) < 3:
        return
    prev_up = pts[1][1] > pts[0][1]
    for k in range(2, len(pts)):
        up = pts[k][1] > pts[k - 1][1]
        if up != prev_up:
            out[pts[k][0]]["colors"][col] = color
        prev_up = up
