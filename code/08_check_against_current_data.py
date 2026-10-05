"""Compare the newest rebuilt vintage with the data the ECB serves today.

Why this check matters: the server sometimes cuts a history file short, and the
Delete rows come last in the file. 01_download_rtd_history.py rejects a file
that ends in the middle of a row or that differs between two downloads, but a
file cut exactly at the end of a row and served twice from the server's cache
passes both tests. The periods whose removal was lost would then stay in every
later vintage, the newest one included.

A request without includeHistory returns each series as it stands now, and it
does not depend on the history files at all. Until the ECB's next release it
has to equal the newest rebuilt vintage: the same periods and the same values,
for every series. Three requests (the annual, the quarterly and the monthly
series) fetch all 278 series in a few seconds.

What it does not show: if the lost removal is of a period the ECB later
published again, the newest vintage is right and some earlier ones are wrong.
For 2001-2014 those are covered by 03_check_against_old_files.py.

Two values count as equal when they agree to 14 significant digits. Some of the
ECB's numbers carry 16 or 17, and 02_build_vintages.py reads them with pandas'
default parser, which is not exact in the last digit of numbers that long.

Run it soon after 01_download_rtd_history.py. Once the ECB has published a new
release its current data move on, and differences are to be expected until the
history is downloaded again.

Output: output/logs/08_check_against_current_data.txt

    python code/08_check_against_current_data.py
"""

from __future__ import annotations

import datetime
import io
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW_VINTAGE_DIR = ROOT / "data" / "processed" / "raw_vintages"
VINTAGE_DATES = ROOT / "data" / "processed" / "vintage_dates.csv"
LOG_PATH = ROOT / "output" / "logs" / "08_check_against_current_data.txt"

API = "https://data-api.ecb.europa.eu/service/data/RTD"
# Largest relative difference between two values that still counts as equal.
TOLERANCE = 1e-14


def fetch(url: str, tries: int = 4) -> bytes:
    """GET a URL, retrying on timeouts and server errors (5xx)."""
    request = urllib.request.Request(url, headers={"Accept": "text/csv"})
    for attempt in range(1, tries + 1):
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == tries:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == tries:
                raise
        time.sleep(30 * attempt)
    raise RuntimeError("unreachable")


def current_data() -> pd.Series:
    """Every value the ECB serves now, indexed by (series key, period).

    A key with the last four parts left empty matches every series of one
    frequency. Such a request drops the Delete rows of a history, but no history
    is asked for here, so nothing is lost.
    """
    parts = []
    for frequency in ("A", "Q", "M"):
        text = fetch(f"{API}/{frequency}....?format=csvdata&detail=dataonly")
        parts.append(pd.read_csv(io.BytesIO(text), usecols=["KEY", "TIME_PERIOD", "OBS_VALUE"],
                                 dtype={"KEY": str, "TIME_PERIOD": str},
                                 float_precision="round_trip"))
    data = pd.concat(parts, ignore_index=True).dropna(subset=["OBS_VALUE"])
    return data.set_index(["KEY", "TIME_PERIOD"])["OBS_VALUE"]


def newest_vintage(date: str) -> pd.Series:
    """Every value of the newest rebuilt vintage, indexed the same way."""
    parts = []
    for path in sorted(RAW_VINTAGE_DIR.glob("RTD.*.csv")):
        table = pd.read_csv(path, usecols=["TIME_PERIOD", date], dtype={"TIME_PERIOD": str},
                            float_precision="round_trip")
        table = table.dropna(subset=[date])
        table.insert(0, "KEY", path.stem)
        parts.append(table.rename(columns={date: "OBS_VALUE"}))
    data = pd.concat(parts, ignore_index=True)
    return data.set_index(["KEY", "TIME_PERIOD"])["OBS_VALUE"]


def main() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    latest = pd.read_csv(VINTAGE_DATES)["vintage_date"].iloc[-1]

    rebuilt = newest_vintage(latest)
    current = current_data()

    only_rebuilt = rebuilt.index.difference(current.index)
    only_current = current.index.difference(rebuilt.index)
    both = rebuilt.index.intersection(current.index)
    mine, theirs = rebuilt[both].to_numpy(), current[both].to_numpy()
    largest = pd.concat([rebuilt[both].abs(), current[both].abs()], axis=1).max(axis=1).to_numpy()
    relative = pd.Series(abs(mine - theirs), index=both).div(largest).fillna(0)  # 0/0: both zero
    differ = both[(relative > TOLERANCE).to_numpy()]

    lines = [
        f"checked on {datetime.date.today()} against the vintage of {latest}",
        f"series: {rebuilt.index.get_level_values(0).nunique()} in the rebuilt vintage, "
        f"{current.index.get_level_values(0).nunique()} in the ECB's current data",
        f"periods only in the rebuilt vintage: {len(only_rebuilt):,}",
        f"periods only in the ECB's current data: {len(only_current):,}",
        f"values compared: {len(both):,}",
        f"exactly equal: {int((mine == theirs).sum()):,}",
        f"largest relative difference: {relative.max():.3e}",
        f"values that differ by more than {TOLERANCE:g}: {len(differ):,}",
    ]
    # Name the series, so a cut history file can be downloaded again.
    for label, index in (("only in the rebuilt vintage", only_rebuilt),
                         ("only in the ECB's current data", only_current),
                         ("different value", differ)):
        counts = pd.Series(index.get_level_values(0)).value_counts()
        lines += [f"  {label}: {key} ({n} periods)" for key, n in counts.items()]

    LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"log: {LOG_PATH.relative_to(ROOT)}")
    sys.exit(1 if len(only_rebuilt) or len(only_current) or len(differ) else 0)


if __name__ == "__main__":
    main()
