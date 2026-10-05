"""Turn each series' change log (from 01_download_rtd_history.py) into a
real-time data "triangle": one row per period, one column per vintage, so the
column for vintage 2008-06-05 is the series exactly as it looked on that day.

The rule for rebuilding vintage v of a series
---------------------------------------------
For each period (e.g. 2005-Q3):
  1. take the rows for that period dated on or before v
     (Replace rows are dated by VALID_FROM, Delete rows by VALID_TO);
  2. the latest such row tells you its state at v:
       Replace -> its OBS_VALUE is the value in vintage v
       Delete  -> the period was not in vintage v (left blank);
  3. no rows at all -> the period had not been published yet (blank).

If a Delete and a Replace share the same date, the Replace wins. The ECB uses
that pattern to record a full re-send of a series: on 2021-01-20, for example,
it deleted every GDP observation and re-published all of them at the same time.
Treating the Delete as the later event would wrongly empty the whole vintage.

Vintage dates are the union of all publication dates across all RTD series (the
"freeze" days before each Governing Council meeting), so every output file has
the same columns. A series that did not change on a given date simply repeats
the previous column.

These are the raw vintages: correct, but one file per series and named by ECB
code. 04_make_readable_vintages.py turns them into labelled files, one per
vintage date.

Output: data/processed/raw_vintages/<KEY>.csv  rows = TIME_PERIOD, columns = vintage dates
        data/processed/vintage_dates.csv       the list of vintage dates
        output/logs/02_build_vintages.txt

    python code/02_build_vintages.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
HISTORY_DIR = ROOT / "data" / "raw" / "history"
OUT_DIR = ROOT / "data" / "processed" / "raw_vintages"
DATES_OUT = ROOT / "data" / "processed" / "vintage_dates.csv"
LOG_PATH = ROOT / "output" / "logs" / "02_build_vintages.txt"


def load_change_log(path: Path) -> pd.DataFrame:
    """Read one raw history file and give every row a single 'date' and an order."""
    h = pd.read_csv(path, usecols=["KEY", "TIME_PERIOD", "OBS_VALUE", "ACTION",
                                   "VALID_FROM", "VALID_TO"],
                    dtype={"KEY": str, "TIME_PERIOD": str, "ACTION": str,
                           "VALID_FROM": str, "VALID_TO": str})
    # Keep only the day. Every row is stamped 15:30:00, except on 20 January and
    # 8 September 2021, when a removal at 15:30:00 is followed by its new value at
    # 15:30:01. The sort below puts those two in the same order.
    h["date"] = h["VALID_FROM"].where(h["ACTION"] == "Replace", h["VALID_TO"]).str[:10]
    # Sort so that, within the same date, Delete (0) comes before Replace (1).
    h["order"] = (h["ACTION"] == "Replace").astype(int)
    return h.sort_values(["date", "order"])


def vintage_as_of(h: pd.DataFrame, v: str) -> pd.Series:
    """The series as published on date v: one value per period."""
    known = h[h["date"] <= v]
    latest = known.drop_duplicates("TIME_PERIOD", keep="last")  # last row per period
    latest = latest[latest["ACTION"] == "Replace"]               # drop deleted periods
    return latest.set_index("TIME_PERIOD")["OBS_VALUE"]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    logs = {f.stem: load_change_log(f) for f in sorted(HISTORY_DIR.glob("RTD.*.csv"))}

    all_dates = sorted(set().union(*(set(h["date"].dropna()) for h in logs.values())))
    pd.Series(all_dates, name="vintage_date").to_csv(DATES_OUT, index=False)
    print(f"{len(logs)} series, {len(all_dates)} vintage dates "
          f"({all_dates[0]} to {all_dates[-1]})")

    lines = []
    for key, h in logs.items():
        columns = {v: vintage_as_of(h, v) for v in all_dates}
        table = pd.DataFrame(columns)        # rows = periods, columns = vintages
        table = table.sort_index()
        table.index.name = "TIME_PERIOD"
        table.to_csv(OUT_DIR / f"{key}.csv")

        first = table.notna().any().idxmax()  # first vintage with any data
        lines.append(f"{key}\t{len(table)} periods\tfirst vintage {first}\t"
                     f"latest vintage has {table[all_dates[-1]].notna().sum()} values")

    LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(logs)} files to {OUT_DIR.relative_to(ROOT)}; log: {LOG_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
