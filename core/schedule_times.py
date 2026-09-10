"""The 27 fixed clock times (IST) at which a row is drawn.

`Reqs.txt` lists row 13 onward with an "am" suffix (12:13am, 13:13am, ...); those are
typos for 24-hour IST market times and are treated as such here.
"""

SCHEDULE = [
    (1, "09:18"),
    (2, "09:28"),
    (3, "09:43"),
    (4, "09:58"),
    (5, "10:13"),
    (6, "10:28"),
    (7, "10:43"),
    (8, "10:58"),
    (9, "11:13"),
    (10, "11:28"),
    (11, "11:43"),
    (12, "11:58"),
    (13, "12:13"),
    (14, "12:28"),
    (15, "12:43"),
    (16, "12:58"),
    (17, "13:13"),
    (18, "13:28"),
    (19, "13:43"),
    (20, "13:58"),
    (21, "14:13"),
    (22, "14:28"),
    (23, "14:43"),
    (24, "14:58"),
    (25, "15:13"),
    (26, "15:28"),
    (27, "15:40"),
]

FIRST_ROW_INDEX = SCHEDULE[0][0]
LAST_ROW_INDEX = SCHEDULE[-1][0]

# row_index -> "HH:MM"
TIME_BY_ROW = {idx: hhmm for idx, hhmm in SCHEDULE}
