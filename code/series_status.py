"""Check which RTD series the ECB is still updating, so that a user can tell
which of the 278 series are usable and which are frozen.

Every vintage of every series downloads correctly (see the checks), but that says
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

The labels are written by checks/02_series_status.py for all 278 series, and
get_data.py uses them to say which of the series and vintages it was asked for
are behind.
"""

from __future__ import annotations

import pandas as pd

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


def change_vintages(changed: set[str], vintages: list[str]) -> list[str]:
    """The vintages at which the ECB changed anything in this series.

    changed holds the days of the log's rows (vintages.change_dates): a Replace row
    is dated by VALID_FROM and a Delete row by VALID_TO. Only the day matters,
    because every freeze happens at 15:30.
    """
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
    found: list[dict] = []
    run: dict | None = None
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


def series_status(table: pd.DataFrame, changed: set[str], freq: str) -> dict:
    """Everything the two tests find for one series, over every vintage of its table.

    table must have a column for every vintage date, including those in which the
    series did not change, because the second test counts vintages.
    """
    table = table.sort_index()
    vintages = list(table.columns)
    last_period, gap = last_periods(table, freq)
    months, overall = usual_gaps(gap)
    changes = change_vintages(changed, vintages)
    run = normal_run(changes, vintages)
    since = len(vintages) - 1 - vintages.index(changes[-1]) if changes else len(vintages)
    return {"status": status_by_vintage(vintages, freq, gap, months, overall, changes, run),
            "last_period": last_period, "gap": gap, "overall": overall,
            "changes": changes, "run": run, "since": since}
