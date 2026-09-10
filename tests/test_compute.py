"""Hand-checked fixture covering derived columns + every highlight rule."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.compute import build_table, YELLOW, ORANGE, GREEN, RED, BLUE, PURPLE


def raw(row_index, t, c1, c2, c6, c7, c11, c12, status="ok"):
    return {
        "row_index": row_index, "sched_time": t, "captured_at": "x", "status": status,
        "ce_chng_oi": c1, "pe_chng_oi": c2, "ce_vol": c6, "pe_vol": c7,
        "ce_price": c11, "pe_price": c12,
    }


def test_derived_and_colours():
    rows = [
        #        idx  time    c1    c2   c6     c7     c11     c12
        raw(1, "09:18", 100,  200, 1000,  2000, 50.00,  80.00),
        raw(2, "09:28", 150,  180, 1500,  2500, 55.00,  80.00),  # c1 up  -> price up  => green col11 ; c2 down price== => yellow col12
        raw(3, "09:43", 220,  150, 3000,  2000, 52.00,  85.00),  # c1 up  price down  => red col11 ; c2 down price up => purple col12
        raw(4, "09:58", 210,  140, 1500,  4000, 60.00,  82.00),  # c1 DOWN (break!) price up => purple col11 ; c2 down price down => blue col12
        raw(5, "10:13", 190,  130, 1500,  4000, 58.00,  90.00),  # c1 down price down => blue col11 ; c2 down (break) price up => purple col12
    ]
    t = build_table(rows)

    # ---- derived columns on row 2 (index 1)
    r2 = t[1]["text"]
    assert r2[3] == f"{150-100:,}"          # col3 = c1 diff
    assert r2[4] == f"{180-200:,}"          # col4 = c2 diff
    assert r2[5] == f"{(150-100)-(180-200):,}"   # col5
    assert r2[8] == f"{1500/1000:.4f}"      # col8 ratio
    assert r2[9] == f"{2500/2000:.4f}"      # col9 ratio
    assert r2[10] == f"{1500/1000 - 2500/2000:.4f}"

    # ---- row 1: no derived, prices yellow
    assert t[0]["text"][3] == "" and t[0]["text"][8] == ""
    assert t[0]["colors"][11] == YELLOW and t[0]["colors"][12] == YELLOW

    # ---- base colours everywhere
    for row in t:
        assert row["colors"][0] == YELLOW
        for c in (1, 2, 6, 7):
            assert row["colors"].get(c) in (ORANGE, RED)   # RED only where a break lands

    # ---- price-cell rules
    assert t[1]["colors"][11] == GREEN     # c1 up, price up
    assert t[1]["colors"][12] == YELLOW    # price unchanged
    assert t[2]["colors"][11] == RED       # c1 up, price down
    assert t[2]["colors"][12] == PURPLE    # c2 down, price up
    assert t[3]["colors"][11] == PURPLE    # c1 down, price up
    assert t[3]["colors"][12] == BLUE      # c2 down, price down
    assert t[4]["colors"][11] == BLUE      # c1 down, price down

    # ---- rule 10: first contiguity break
    # c1 series 100,150,220,210,... increasing then row 4 (index 3) drops -> red col1
    assert t[3]["colors"][1] == RED
    assert [i for i, r in enumerate(t) if r["colors"].get(1) == RED] == [3]
    # c2 series 200,180,150,140,130 strictly decreasing -> never breaks -> no red col2
    assert all(r["colors"].get(2) != RED for r in t)


def test_missing_rows_are_safe():
    rows = [
        raw(1, "09:18", 100, 200, 1000, 2000, 50.0, 80.0),
        {"row_index": 2, "sched_time": "09:28", "captured_at": None, "status": "missed",
         "ce_chng_oi": None, "pe_chng_oi": None, "ce_vol": None, "pe_vol": None,
         "ce_price": None, "pe_price": None},
        raw(3, "09:43", 150, 150, 1500, 2500, 55.0, 85.0),
    ]
    t = build_table(rows)
    assert t[1]["text"][1] == "" and t[1]["text"][8] == ""
    assert 11 not in t[1]["colors"] and 12 not in t[1]["colors"]
    # row 3 has no valid previous numbers (row 2 missing) -> no diff
    assert t[2]["text"][3] == ""


if __name__ == "__main__":
    test_derived_and_colours()
    test_missing_rows_are_safe()
    print("ok")
