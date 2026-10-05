"""Download the full revision history (all vintages) of every series in the ECB
Real Time Database (RTD) from the ECB Data Portal API.

How the API stores vintages
---------------------------
A plain request for an RTD series returns only the latest numbers. Adding
includeHistory=true returns every version the series has ever had. The CSV then
has three extra columns:

  ACTION      Replace = this value was published; Delete = this period was removed
  VALID_FROM  when the value was published (Replace rows). Its date is the vintage.
  VALID_TO    when the period was removed (Delete rows)

The rows are a log of changes, not full snapshots: a period only gets a new row
when its value changes. 02_build_vintages.py turns the log into one full column
per vintage.

Why one request per series
--------------------------
The API also accepts wildcard keys ("Q...." = all quarterly series), which is
about 100 times faster. But on 3 October 2026 any key with a wildcard came back
WITHOUT the Delete rows, even when it matched a single series (for real GDP: 240
Delete rows with the full key, 0 with "Q.S0.S.G_GDPM_TO_C."). Without them,
periods the ECB removed (e.g. GDP for 1991-1994 from Nov 2005) wrongly stay in
later vintages. So every series is requested with its full key. (Full keys
joined with "+" did return the Delete rows, in a test with real and nominal GDP.)

A single-series request is slow the first time (often 1-3 minutes, sometimes
"504 Gateway Timeout"), so failed requests are retried and 4 run at the same
time. If the script stops, run it again: files already on disk are re-checked
(below), which is quick, and only missing or bad ones are downloaded again.

Truncated downloads: never trust "HTTP 200"
-------------------------------------------
The server sometimes cuts a response short but still reports success. On
3 Oct 2026, 13 of the 278 series came back cut off mid-line at least once, and
the cut copy stayed in the server's cache, so retrying straight away returned the
same cut copy. Delete rows come last in each file, so a cut file typically loses
all of them, and the vintages built from it are wrong. So a file is kept only if
  1. two separate downloads are byte-for-byte identical (the second request is
     answered from the server's cache in about a second), and
  2. it passes problem_with(): ends with a newline, every row has as many fields
     as the header, every row is this series, every Replace row has VALID_FROM
     and every Delete row has VALID_TO.
Check 2 alone misses a cut that lands exactly at the end of a line; check 1
catches it. A rejected download is retried after 2, 4 and 6 minutes; after 4
rejected attempts the series is logged as FAILED (never saved). If that happens,
run the script again later. If a series keeps failing, see README.md, Section 5:
the server keeps one cached copy per "Accept" header, so asking with a different
Accept value (e.g. text/csv) gets a different copy, which may be complete.

The same URL works in a web browser - see README.md, Route A.

Output: data/raw/series_metadata.csv   one row per series: key, title, unit, old-file column
        data/raw/rtd_structure.xml     the ECB's code lists for RTD
        data/raw/history/<KEY>.csv     the change log of each series, as the API sent it
        output/logs/01_download_rtd_history.txt

    python code/01_download_rtd_history.py                         # all series (1-3 hours)
    python code/01_download_rtd_history.py Q.S0.S.G_GDPM_TO_C.E    # just these series
"""

from __future__ import annotations

import csv
import io
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
HISTORY_DIR = RAW / "history"
METADATA = RAW / "series_metadata.csv"
STRUCTURE = RAW / "rtd_structure.xml"
LOG_PATH = ROOT / "output" / "logs" / "01_download_rtd_history.txt"

API = "https://data-api.ecb.europa.eu/service/data/RTD"
PARALLEL_REQUESTS = 4


def fetch(url: str, tries: int = 6) -> bytes:
    """GET a URL, retrying on timeouts and server errors (5xx)."""
    # Send "Accept: */*" like a browser or curl does (Python sends none by default).
    # This does not prevent cut-off files (any cached copy can be cut); it just
    # makes the script ask for the same copy a browser gets.
    request = urllib.request.Request(url, headers={"Accept": "*/*"})
    for attempt in range(1, tries + 1):
        try:
            with urllib.request.urlopen(request, timeout=900) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == tries:  # 404 = no such series: retrying won't help
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == tries:
                raise
        time.sleep(30 * attempt)
    raise RuntimeError("unreachable")


def download_metadata() -> pd.DataFrame:
    """One row per series, with its attributes but no observations (detail=nodata)."""
    meta = pd.read_csv(io.BytesIO(fetch(f"{API}?detail=nodata&format=csvdata")), dtype=str)

    # COMPILATION looks like '... notes</a>; block Quarterly; series: BM'.
    # BM is the column letter of this series in the old 2001-2014 files
    # (quarterly_YYYYMM.csv), so keep it to compare with those files later.
    parts = meta["COMPILATION"].str.extract(r"block (\w+); series: (\w+)")
    meta["OLD_FILE_BLOCK"] = parts[0].str.lower()
    meta["OLD_FILE_COLUMN"] = parts[1]

    keep = ["KEY", "FREQ", "RT_ECON_CONCEPT", "RT_DENOM", "ADJUSTMENT", "TITLE",
            "TITLE_COMPL", "UNIT", "UNIT_MULT", "DOM_SER_IDS",
            "OLD_FILE_BLOCK", "OLD_FILE_COLUMN"]
    return meta[keep]


def problem_with(content: bytes, key: str) -> str | None:
    """Return what is wrong with a downloaded history file, or None if it looks whole."""
    if not content.endswith(b"\n"):
        return "does not end with a newline (cut off mid-line)"
    rows = list(csv.reader(io.StringIO(content.decode("utf-8"))))
    header, data = rows[0], rows[1:]
    if not data:
        return "no data rows"
    if any(len(row) != len(header) for row in data):
        return "a row has the wrong number of fields"
    col = {name: i for i, name in enumerate(header)}
    for row in data:
        if row[col["KEY"]] != key:
            return f"a row belongs to another series ({row[col['KEY']]})"
        action = row[col["ACTION"]]
        if action == "Replace" and not row[col["VALID_FROM"]]:
            return "a Replace row has no VALID_FROM"
        if action == "Delete" and not row[col["VALID_TO"]]:
            return "a Delete row has no VALID_TO"
        if action not in ("Replace", "Delete"):
            return f"unknown ACTION {action!r}"
    return None


def download_series(key: str) -> str:
    """Save one series' history to data/raw/history/<KEY>.csv; return a log line.

    The file is kept only if two separate downloads are byte-for-byte identical
    and pass problem_with(). A cut at the end of a line passes problem_with(), but
    two cuts at exactly the same byte are very unlikely, so the comparison catches
    it. A file already on disk counts as the first copy, so a rerun re-checks every
    file against the server instead of trusting it.
    """
    path = HISTORY_DIR / f"{key}.csv"
    # The key in the URL leaves out the leading "RTD." (that is the dataset name).
    # detail=dataonly drops ~20 attribute columns that are the same on every row.
    url = f"{API}/{key[4:]}?includeHistory=true&format=csvdata&detail=dataonly"
    start = time.time()

    first = path.read_bytes() if path.exists() else None
    if first is None:
        note = "downloaded"
    elif problem_with(first, key):
        note = "file on disk was bad; downloaded again"
    else:
        note = "checked file on disk"
    for attempt in range(1, 5):
        try:
            if first is None or problem_with(first, key):
                first = fetch(url)
            second = fetch(url)
        except Exception as e:
            return f"{key}\tFAILED ({e})"
        problem = problem_with(first, key) or problem_with(second, key)
        if problem is None and first == second:
            break
        reason = problem or "two downloads differ"
        note = "downloaded again"
        first = None
        if attempt < 4:
            # The server's cache can keep serving the same cut-off copy for 20+
            # minutes, so retrying straight away just gets that copy again.
            wait = 120 * attempt
            print(f"    {key}: attempt {attempt} rejected ({reason}); "
                  f"waiting {wait // 60} min", flush=True)
            time.sleep(wait)
    else:
        return f"{key}\tFAILED (4 attempts rejected: {reason})"

    if not path.exists() or path.read_bytes() != first:
        # Write to a temporary name first, so a crash never leaves a half-written
        # file under the real name.
        tmp = path.with_suffix(".part")
        tmp.write_bytes(first)
        tmp.replace(path)

    rows = pd.read_csv(path, usecols=["ACTION"], dtype=str)
    n_delete = (rows["ACTION"] == "Delete").sum()
    return (f"{key}\tOK, {note}\t{len(rows)} rows ({n_delete} Delete)\t"
            f"{time.time() - start:.0f}s")


def main() -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    meta = download_metadata()
    meta.to_csv(METADATA, index=False)
    # The ECB's code lists for RTD (what each code in a series key means);
    # 04_make_readable_vintages.py reads the concept names from it.
    STRUCTURE.write_bytes(fetch(f"{API.replace('/data/RTD', '/dataflow/ECB/RTD')}?references=all"))
    print(f"{len(meta)} series in RTD; list saved to {METADATA.relative_to(ROOT)}")

    keys = meta["KEY"].tolist()
    if len(sys.argv) > 1:
        keys = [k if k.startswith("RTD.") else f"RTD.{k}" for k in sys.argv[1:]]

    log = []
    with ThreadPoolExecutor(max_workers=PARALLEL_REQUESTS) as pool:
        for i, line in enumerate(pool.map(download_series, keys), 1):
            print(f"[{i}/{len(keys)}] {line}", flush=True)
            log.append(line)

    LOG_PATH.write_text("\n".join(log) + "\n", encoding="utf-8")
    failed = sum("FAILED" in line for line in log)
    print(f"done; {failed} failed (run the script again to retry them). "
          f"Log: {LOG_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
