"""Turn a series' change log (from download.py) into a real-time data "triangle":
one row per period, one column per vintage, so the column for vintage 2008-06-05
is the series exactly as it looked on that day.

The rule for rebuilding vintage v of a series
---------------------------------------------
For each period (e.g. 2005-Q3):
  1. take the rows for that period dated on or before v
     (Replace rows are dated by VALID_FROM, Delete rows by VALID_TO);
  2. the latest such row tells you its state at v:
       Replace -> its OBS_VALUE is the value in vintage v
       Delete  -> the period was not in vintage v (left blank);
  3. no rows at all -> the period had not been published yet (blank).

"Latest" is decided by the full time stamp, not only the day. The day says which
vintages a row belongs to, since vintages are named by day; the time says which of
a period's rows came last. Every row in the logs is stamped 15:30:00 except on
2021-01-20 and 2021-09-08, when the ECB re-sent most series by removing each
observation at 15:30:00 and entering its new value at 15:30:01. The new value is
the later row, so it is kept. Comparing only the day would see a removal and a new
value on one date and could not tell which came last; treating the removal as the
later one would empty the whole vintage. Should a removal ever be stamped after a
new value on the same day, the removal wins, as it should.

Two rows of one period with the very same time stamp would leave the order
undecided. check_order() stops the build if it finds such a pair, and
checks/01_logs.py reports how many same-day removals and new values the logs hold
and in which order.

Vintage dates
-------------
The vintage dates are the union of all publication dates across all RTD series
(the "freeze" days before each Governing Council meeting), so every table has the
same columns. A series that did not change on a given date simply repeats the
previous column. data/processed/vintage_dates.csv keeps the dates found in all
278 logs, so a table built for one series still gets a column for every vintage,
including those in which that series did not change.

Values are read with Python's own float conversion (float_precision="round_trip"),
so each one is exactly the number the ECB published. pandas' faster default is
off in the last binary digit for about 13,000 of the 4.1 million values, those
with 16 or 17 significant digits.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd


def load_change_log(path: Path) -> pd.DataFrame:
    """Read one raw history file; give every row its vintage date and its time stamp."""
    h = pd.read_csv(path, usecols=["KEY", "TIME_PERIOD", "OBS_VALUE", "ACTION",
                                   "VALID_FROM", "VALID_TO"],
                    dtype={"KEY": str, "TIME_PERIOD": str, "ACTION": str,
                           "VALID_FROM": str, "VALID_TO": str},
                    float_precision="round_trip")
    stamp = h["VALID_FROM"].where(h["ACTION"] == "Replace", h["VALID_TO"])
    # The day decides which vintages a row belongs to ...
    h["date"] = stamp.str[:10]
    # ... and the full time stamp decides which of a period's rows is the latest.
    h["stamp"] = pd.to_datetime(stamp, utc=True)
    return h.sort_values("stamp", kind="stable")


def check_order(h: pd.DataFrame) -> tuple[int, int, set[str], int]:
    """Make sure the time stamps decide the order of every period's rows.

    Stops if two rows of one period share a time stamp but differ, since nothing
    would then say which came last. Otherwise returns how many times a period has
    both a removal and a new value on one day, split by which is stamped later; the
    days on which that happens; and how many rows are exact copies of another row
    of their period, which are harmless because either copy gives the same result.
    """
    same = h[h.duplicated(["TIME_PERIOD", "stamp"], keep=False)]
    distinct = same.drop_duplicates(["TIME_PERIOD", "stamp", "ACTION", "OBS_VALUE"])
    clash = distinct[distinct.duplicated(["TIME_PERIOD", "stamp"], keep=False)]
    if not clash.empty:
        raise SystemExit(f"{h['KEY'].iloc[0]}: rows of one period share a time stamp "
                         f"but differ, so their order is undecided:\n{clash.head()}")
    copies = len(same) - len(distinct)

    last = h.groupby(["TIME_PERIOD", "date", "ACTION"])["stamp"].max().unstack("ACTION")
    if not {"Delete", "Replace"} <= set(last.columns):
        return 0, 0, set(), copies
    both = last.dropna(subset=["Delete", "Replace"])
    new_value_later = int((both["Replace"] > both["Delete"]).sum())
    removal_later = int((both["Delete"] > both["Replace"]).sum())
    return new_value_later, removal_later, set(both.index.get_level_values("date")), copies


def vintage_as_of(h: pd.DataFrame, v: str) -> pd.Series:
    """The series as published on date v: one value per period."""
    known = h[h["date"] <= v]
    latest = known.drop_duplicates("TIME_PERIOD", keep="last")  # last row per period
    latest = latest[latest["ACTION"] == "Replace"]               # drop deleted periods
    return latest.set_index("TIME_PERIOD")["OBS_VALUE"]


def build_table(h: pd.DataFrame, dates: list[str]) -> pd.DataFrame:
    """Every vintage of one series: rows = periods, columns = vintage dates."""
    table = pd.DataFrame({v: vintage_as_of(h, v) for v in dates}, columns=dates)
    table = table.sort_index()
    table.index.name = "TIME_PERIOD"
    return table


def change_dates(h: pd.DataFrame) -> set[str]:
    """The days on which the ECB added, revised or removed anything in this series."""
    return set(h["date"].dropna())


def fingerprint(table: pd.DataFrame, up_to: str) -> tuple[int, str]:
    """(number of values, SHA-256) of a table's vintages up to and including up_to.

    Built from the text "vintage, period, value" of every value, in a fixed order,
    with the value written by repr(), so it is the same on any machine. The
    published build's fingerprints are in data/processed/checksums.csv, and
    get_data.py compares every table it builds with them.
    """
    digest, n = hashlib.sha256(), 0
    for v in [c for c in table.columns if c <= up_to]:
        for period, value in table[v].dropna().items():
            digest.update(f"{v}\t{period}\t{value!r}\n".encode())
            n += 1
    return n, digest.hexdigest()
