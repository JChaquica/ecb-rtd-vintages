"""Check which RTD series the ECB is still updating, so that a user can tell
which of the 278 series are usable and which are frozen.

Every vintage of every series downloads correctly (scripts 01-03), but that says
nothing about whether the numbers are still being maintained. A series can appear
in all 261 vintages and still be stale: the ECB repeats its last observation,
vintage after vintage, and a user who treats a vintage as the data available on
that date is reading figures that were already many months old.

How a series is judged
----------------------
A series fails in a given vintage if either of two things is true. They catch
different failures, and neither finds both.

1. Its last observation is older than usual. For each vintage, count the periods
   between the vintage date and the last period that has a value, and compare
   that with the same count in the baseline years 2015-2022. A series fails when
   it is at least three months (monthly), two quarters (quarterly) or one year
   (annual) further behind than usual.

   The comparison is made against baseline vintages falling in the same calendar
   month, because publication lags are seasonal. An annual series ends two years
   back in a January vintage and one year back from May onward, so comparing a
   January vintage with a yearly average would mark every January as a failure. A
   calendar month with fewer than MIN_MONTH baseline vintages is too thin to take
   a median from, so those fall back to the median over all baseline vintages.

2. The ECB has not touched it for longer than it normally goes. The change log
   gives the vintages at which anything was added, revised or deleted. A series
   fails when the number of vintages since its last change exceeds the longest
   run between changes it had in the baseline years.

Test 2 alone would miss a series that is still being revised but has stopped
growing: real household equipment retail sales kept its December 2020 value as
the last observation for 25 vintages while the ECB went on revising its history
at every one of them. Test 1 alone would miss nothing here, but it depends on
estimating a normal lag, which test 2 does not, and the two agree on the latest
vintage, which is the result that matters most.

Each series in each vintage is one of four things:

  not yet published  the series did not exist yet in that vintage
  no observations    the vintage has the series but not one value in it
  behind             either test above failed
  current            neither did

Output: output/series_status.csv            one line per series: usable or not
        output/series_last_update.csv       last change and publication lag of each series
        output/series_gaps.csv              each stretch of vintages a series was behind
        output/series_status_by_vintage.csv the status of every series in every vintage
        output/logs/05_check_series_currency.txt

    python code/05_check_series_currency.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
HISTORY_DIR = ROOT / "data" / "raw" / "history"
RAW_VINTAGE_DIR = ROOT / "data" / "processed" / "raw_vintages"
VARIABLE_LIST = ROOT / "data" / "processed" / "vintages" / "_variable_list.csv"
OUT_DIR = ROOT / "output"
LOG_PATH = OUT_DIR / "logs" / "05_check_series_currency.txt"

FREQ_CODE = {"Monthly": "M", "Quarterly": "Q", "Annual": "A"}
# The stretch used to learn how each series normally behaves: the most recent
# long run of vintages before the problems this script finds.
BASELINE = ("2015", "2022")
# How much further behind than usual the last observation must be before a series
# counts as behind: three months, two quarters, one year. Anything smaller is the
# ordinary jitter of a release landing on the other side of a freeze date.
BEHIND_THRESHOLD = {"M": 3, "Q": 2, "A": 1}
# A calendar month needs at least this many baseline vintages before its own
# median is used instead of the median over all of them.
MIN_MONTH = 3
# A series is allowed at least this many vintages without a change, however
# regular it was in the baseline years, so that one ordinary late release does not
# condemn a series that changed at every single baseline vintage.
MIN_RUN = 2
# And no more than this, however irregular it has been: eight vintages is a full
# year under the schedule in force since 2015, and nothing the ECB still maintains
# goes a year untouched. The cap matters for the nine series with no observations
# at all in 2015-2022 (five government bond yields, two raw material price and two
# producer price series), whose run would otherwise be measured across that
# eight-year dormancy, which no later silence could exceed.
MAX_RUN = 8


def period_index(period: str, freq: str) -> int:
    """Number a period on a scale of months, quarters or years, so two can be subtracted."""
    period = str(period)
    if freq == "A":
        return int(period)
    if freq == "Q":
        year, quarter = period.split("-Q")
        return int(year) * 4 + int(quarter) - 1
    year, month = period.split("-")
    return int(year) * 12 + int(month) - 1


def vintage_index(vintage: str, freq: str) -> int:
    """The same scale for a vintage date: the period the date falls in."""
    year, month = int(vintage[:4]), int(vintage[5:7])
    if freq == "A":
        return year
    if freq == "Q":
        return year * 4 + (month - 1) // 3
    return year * 12 + (month - 1)


def last_periods(table: pd.DataFrame, freq: str) -> tuple[dict, dict]:
    """For each vintage, its last period with a value and how far back that period is.

    A vintage with no observations at all gets None for both.
    """
    last_period, gap = {}, {}
    for vintage in table.columns:
        values = table[vintage].dropna()
        if values.empty:
            last_period[vintage] = gap[vintage] = None
            continue
        # The table is sorted by period, so the last index is the latest period.
        last_period[vintage] = values.index[-1]
        gap[vintage] = vintage_index(vintage, freq) - period_index(values.index[-1], freq)
    return last_period, gap


def usual_gaps(gap: dict) -> tuple[dict, float]:
    """The series' normal publication lag, by calendar month and overall.

    Nine series have no observations in any baseline vintage, so for them every
    vintage with observations is used instead.
    """
    known = {v: g for v, g in gap.items() if g is not None}
    baseline = {v: g for v, g in known.items() if BASELINE[0] <= v[:4] <= BASELINE[1]} or known
    by_month: dict[str, list] = {}
    for vintage, g in baseline.items():
        by_month.setdefault(vintage[5:7], []).append(g)
    months = {month: pd.Series(gaps).median()
              for month, gaps in by_month.items() if len(gaps) >= MIN_MONTH}
    return months, pd.Series(list(baseline.values())).median()


def change_vintages(key: str, vintages: list[str]) -> list[str]:
    """The vintages at which the ECB changed anything in this series.

    A Replace row is dated by VALID_FROM and a Delete row by VALID_TO. Only the
    day matters, because every freeze happens at 15:30.
    """
    log = pd.read_csv(HISTORY_DIR / f"{key}.csv", usecols=["ACTION", "VALID_FROM", "VALID_TO"],
                      dtype=str)
    dates = log["VALID_FROM"].where(log["ACTION"] == "Replace", log["VALID_TO"]).str[:10]
    changed = set(dates.dropna())
    return [v for v in vintages if v in changed]


def normal_run(changed: list[str], vintages: list[str]) -> int:
    """The longest run of vintages the series normally goes without a change.

    Measured over the baseline years, or over the series' whole life if it changed
    fewer than twice in those years, and capped at MAX_RUN either way.
    """
    position = {v: i for i, v in enumerate(vintages)}
    at = [position[c] for c in changed]
    runs = [b - a for a, b in zip(at, at[1:])
            if BASELINE[0] <= vintages[b][:4] <= BASELINE[1]]
    if not runs:
        runs = [b - a for a, b in zip(at, at[1:])]
    return min(max(max(runs, default=MIN_RUN), MIN_RUN), MAX_RUN)


def status_by_vintage(vintages: list[str], freq: str, gap: dict, months: dict, overall: float,
                      changed: list[str], run: int) -> dict:
    """Label every vintage of one series, by the two tests in the module docstring."""
    first = changed[0] if changed else None
    position = {v: i for i, v in enumerate(vintages)}
    status = {}
    for i, vintage in enumerate(vintages):
        if first is None or vintage < first:
            status[vintage] = "not yet published"
            continue
        if gap[vintage] is None:
            status[vintage] = "no observations"
            continue
        usual = months.get(vintage[5:7], overall)
        stale = gap[vintage] - usual >= BEHIND_THRESHOLD[freq]
        untouched = i - position[max(c for c in changed if c <= vintage)] > run
        status[vintage] = "behind" if stale or untouched else "current"
    return status


def episodes(status: dict) -> list[dict]:
    """Group consecutive vintages with the same problem into one stretch each."""
    found, run = [], None
    for vintage, label in status.items():
        if label not in ("behind", "no observations"):
            run = None
        elif run and run["Problem"] == label:
            run["Last vintage affected"] = vintage
            run["Vintages affected"] += 1
        else:
            run = {"Problem": label, "First vintage affected": vintage,
                   "Last vintage affected": vintage, "Vintages affected": 1}
            found.append(run)
    return found


def main() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    variables = pd.read_csv(VARIABLE_LIST, encoding="utf-8-sig")

    status_rows, update_rows, gap_rows, matrix_rows = [], [], [], []
    latest = ""

    for s in variables.to_dict("records"):
        key, freq = s["ECB series key"], FREQ_CODE[s["Frequency"]]
        table = pd.read_csv(RAW_VINTAGE_DIR / f"{key}.csv", index_col=0)
        table.index = table.index.astype(str)   # annual periods would become numbers
        table = table.sort_index()
        vintages = list(table.columns)
        latest = vintages[-1]

        last_period, gap = last_periods(table, freq)
        months, overall = usual_gaps(gap)
        changed = change_vintages(key, vintages)
        run = normal_run(changed, vintages)
        since = len(vintages) - 1 - vintages.index(changed[-1]) if changed else len(vintages)
        status = status_by_vintage(vintages, freq, gap, months, overall, changed, run)
        behind = status[latest] == "behind"

        where = {"Folder": s["Folder"], "Variable": s["Variable"], "Frequency": s["Frequency"]}
        status_rows.append({**where, "Status": "Stopped" if behind else "Current",
                            "Last vintage with new data": changed[-1] if changed else "",
                            "Vintages since then": since,
                            "Normal run without a change (vintages)": run,
                            f"Last period in the {latest} vintage": last_period[latest],
                            "ECB series key": key})
        update_rows.append({**where, "Behind": "yes" if behind else "no",
                            "Last vintage with any change": changed[-1] if changed else "",
                            f"Vintages since then (to {latest})": since,
                            "Normal run without a change (vintages)": run,
                            f"Last period in the {latest} vintage": last_period[latest],
                            f"Usual gap between vintage date and last period, "
                            f"{BASELINE[0]}-{BASELINE[1]} (periods)": overall,
                            f"Gap in the {latest} vintage (periods)": gap[latest],
                            "Periods behind usual": (None if gap[latest] is None
                                                     else gap[latest] - overall),
                            "ECB series key": key})
        for e in episodes(status):
            gap_rows.append({**where, **e,
                             f"Still affected in the {latest} vintage":
                                 "yes" if e["Last vintage affected"] == latest else "no",
                             "Last period in those vintages":
                                 last_period[e["Last vintage affected"]],
                             "ECB series key": key})
        matrix_rows.append({"Folder": s["Folder"], "Variable": s["Variable"],
                            "ECB series key": key, **status})

    for rows, name in ((status_rows, "series_status.csv"),
                       (update_rows, "series_last_update.csv"),
                       (gap_rows, "series_gaps.csv"),
                       (matrix_rows, "series_status_by_vintage.csv")):
        pd.DataFrame(rows).to_csv(OUT_DIR / name, index=False, encoding="utf-8-sig")

    status = pd.DataFrame(status_rows)
    stopped = status[status["Status"] == "Stopped"]
    matrix = pd.DataFrame(matrix_rows).set_index("ECB series key").iloc[:, 2:]
    affected = matrix.isin(["behind", "no observations"]).sum()
    repeated = {r["ECB series key"] for r in gap_rows
                if r["Vintages affected"] >= 2 and r["First vintage affected"] >= BASELINE[0]}

    summary = [f"latest vintage: {latest}", f"series: {len(status)}", "",
               f"{len(status) - len(stopped):>4}  usable (up to date in the latest vintage)",
               f"{len(stopped):>4}  not usable (behind in the latest vintage)", "",
               "not usable, by the vintage at which the ECB last changed them:"]
    summary += [f"{n:>4}  {d}" for d, n in stopped["Last vintage with new data"]
                .value_counts().sort_index().items()]
    summary += ["", "not usable, by indicator:"]
    summary += [f"{n:>4}  {f}" for f, n in stopped["Folder"].value_counts().items()]
    summary += ["", f"{len(repeated):>4}  series behind or without observations for two or "
                    f"more vintages in a row since {BASELINE[0]}",
                "", "series affected per vintage:"]
    for years in ("2015", "2020", "2023", "2026"):
        same = affected[[v for v in affected.index if v[:4] == years]]
        summary.append(f"      {years}: {same.min()} to {same.max()}")
    LOG_PATH.write_text("\n".join(summary) + "\n", encoding="utf-8")
    print("\n".join(summary))
    print(f"\nwrote 4 files to {OUT_DIR.relative_to(ROOT)}; log: {LOG_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
