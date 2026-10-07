"""Compare chosen vintages of real GDP with Eurostat's releases as they were published.

Why this check matters: checks/07_eurostat.py tests the newest vintage, and
checks/08_vintage_dates.py the dates. Neither shows that an earlier vintage, say that
of 15 July 2020, holds what was known on 15 July 2020. Only Eurostat's releases as
they were published can show that, and Eurostat's API serves current figures only,
so the published figures have to be looked up by hand, in the release pages.

This script does everything else. It reads the real GDP table that
get_data.py writes, finds the vintages from 2015 that add a new quarter
(each of those must rest on a Eurostat release), computes the growth rate of the
newest quarter in each, and picks a shortlist: the first two such vintages of each
year, and the vintages in EVENTS, where something unusual happened.

The figures looked up are kept in data/raw/eurostat_release_lookups.csv, one row per
vintage, with the date of the release, its growth rate as printed (one decimal), the
address of the release and a note. That file is the input, so rerunning the script
never loses a lookup. For each row the script checks that the vintage's growth rate,
rounded to one decimal, equals the release's, and whether the release came out on or
before the vintage date. The release to look up is the latest Eurostat release of
euro-area GDP published before the vintage was frozen.

Output: output/spot_check_table.csv       every vintage from 2015, with its newest quarter
        output/spot_check_shortlist.csv   the shortlist, with the lookups and the result
        output/logs/09_eurostat_releases.txt

The script fails (exit code 1) if a looked-up figure disagrees with its vintage.

    python checks/09_eurostat_releases.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(os.path.abspath(__file__)).parents[1] / "code"))

from common import LOG_DIR, OUTPUT, RAW, ROOT, TABLE_DIR, require_full_build  # noqa: E402

GDP_KEY = "RTD.Q.S0.S.G_GDPM_TO_C.E"
REAL_GDP = TABLE_DIR / f"{GDP_KEY}.csv"
LOOKUPS = RAW / "eurostat_release_lookups.csv"
OUT_ALL = OUTPUT / "spot_check_table.csv"
OUT_SHORT = OUTPUT / "spot_check_shortlist.csv"
LOG_PATH = LOG_DIR / "09_eurostat_releases.txt"

FIRST_DATE = "2015-01-01"
PER_YEAR = 2

# Vintages worth checking whether or not they add a quarter. The 2020 ones follow the
# first estimate of the pandemic fall and its revisions; on the two 2021 dates the ECB
# re-sent most series by removing every observation and entering it again.
EVENTS = {"2020-04-29", "2020-06-03", "2020-07-15", "2021-01-20", "2021-09-08"}


def newest_quarters(table: pd.DataFrame) -> pd.DataFrame:
    """For each vintage, its newest quarter, that quarter's level and its growth."""
    rows, previous = [], None
    for vintage in [c for c in table.columns if c >= FIRST_DATE]:
        s = table[vintage].dropna()
        newest = s.index[-1]
        rows.append({
            "vintage_date": vintage,
            "newest_quarter": newest,
            "adds_a_quarter": previous is not None and newest != previous,
            "level": s.iloc[-1],
            "rtd_growth_pct": (s.iloc[-1] / s.iloc[-2] - 1) * 100,
        })
        previous = newest
    return pd.DataFrame(rows)


def main() -> None:
    require_full_build([GDP_KEY])
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    table = pd.read_csv(REAL_GDP, index_col="TIME_PERIOD", dtype={"TIME_PERIOD": str},
                        float_precision="round_trip").sort_index()
    every = newest_quarters(table)
    every.to_csv(OUT_ALL, index=False)

    added = every[every["adds_a_quarter"]]
    picked = set(added.groupby(added["vintage_date"].str[:4]).head(PER_YEAR)["vintage_date"])
    short = every[every["vintage_date"].isin(picked | EVENTS)].copy()
    short["why"] = short["adds_a_quarter"].map({True: "adds a quarter", False: "event"})

    lookups = pd.read_csv(LOOKUPS, dtype=str) if LOOKUPS.exists() else pd.DataFrame(
        columns=["vintage_date", "eurostat_release_date", "eurostat_growth_pct", "source_url", "note"])
    stray = set(lookups["vintage_date"]) - set(short["vintage_date"])
    short = short.merge(lookups, on="vintage_date", how="left")
    short["rtd_growth_1dp"] = short["rtd_growth_pct"].round(1)
    looked_up = short["eurostat_growth_pct"].notna()
    short["matches"] = pd.NA
    short.loc[looked_up, "matches"] = (
        short.loc[looked_up, "rtd_growth_1dp"] == short.loc[looked_up, "eurostat_growth_pct"].astype(float))
    short["released_by_vintage_date"] = pd.NA
    short.loc[looked_up, "released_by_vintage_date"] = (
        short.loc[looked_up, "eurostat_release_date"] <= short.loc[looked_up, "vintage_date"])
    short.to_csv(OUT_SHORT, index=False)

    done = short[looked_up]
    wrong = done[done["matches"] == False]  # noqa: E712  (pandas boolean with NA)
    early = done[done["released_by_vintage_date"] == False]  # noqa: E712
    lines = [
        f"vintages of real GDP from 2015: {len(every)}, of which {int(every['adds_a_quarter'].sum())} "
        f"add a quarter",
        f"shortlisted: {len(short)}; looked up in Eurostat's releases: {len(done)}; "
        f"agreeing at one decimal: {int((done['matches'] == True).sum())}",  # noqa: E712
        f"looked-up figures that disagree: {len(wrong)}",
        f"vintages holding a figure Eurostat published only after the vintage date: {len(early)}",
        "",
    ]
    for _, r in short.iterrows():
        if pd.isna(r["eurostat_growth_pct"]):
            result = "to look up"
        else:
            result = (f"Eurostat {float(r['eurostat_growth_pct']):+.1f}% on {r['eurostat_release_date']}: "
                      f"{'agrees' if r['matches'] else 'DISAGREES'}"
                      f"{'' if r['released_by_vintage_date'] else ', published after the vintage date'}")
        lines.append(f"  {r['vintage_date']}  {r['newest_quarter']}  {r['rtd_growth_pct']:+6.2f}%  "
                     f"{r['why']:<14}  {result}")
    if stray:
        lines += ["", "lookups for vintages not in the shortlist, ignored: " + ", ".join(sorted(stray))]

    LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"log: {LOG_PATH.relative_to(ROOT).as_posix()}")
    sys.exit(1 if len(wrong) else 0)


if __name__ == "__main__":
    main()
