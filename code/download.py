"""Download the full revision history (all vintages) of an RTD series from the ECB
Data Portal API.

How the API stores vintages
---------------------------
A plain request for an RTD series returns only the latest numbers. Adding
includeHistory=true returns every version the series has ever had. The CSV then
has three extra columns:

  ACTION      Replace = this value was published; Delete = this period was removed
  VALID_FROM  when the value was published (Replace rows). Its date is the vintage.
  VALID_TO    when the period was removed (Delete rows)

The rows are a log of changes, not full snapshots: a period only gets a new row
when its value changes. vintages.py turns the log into one full column per
vintage.

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
time.

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
Check 2 alone misses a cut that lands exactly at the end of a line. Check 1
catches it when the two downloads are cut in different places, but not when the
second request is answered with the same cut copy from the server's cache. That
case is left to checks/06_current_data.py, which compares the newest rebuilt
vintage with the data the ECB serves today. A rejected download is retried after
2, 4 and 6 minutes; after 4 rejected attempts the series is reported as failed
and nothing is saved. If that happens, run get_data.py again later. If a series
keeps failing: the server keeps one cached copy per "Accept" header, so asking
with a different Accept value (e.g. text/csv) gets a different copy, which may be
complete.

The same address works in a web browser, for example for real GDP:
https://data-api.ecb.europa.eu/service/data/RTD/Q.S0.S.G_GDPM_TO_C.E?includeHistory=true&format=csvdata&detail=dataonly

Writes: data/raw/history/<KEY>.csv       the change log of each series, as the API sent it
        data/raw/history/manifest.csv    when each was downloaded, its size and checksum
"""

from __future__ import annotations

import csv
import datetime
import hashlib
import io
import threading
import time
import urllib.error
import urllib.request

import pandas as pd

from common import HISTORY_DIR

API = "https://data-api.ecb.europa.eu/service/data/RTD"
PARALLEL_REQUESTS = 4
MANIFEST = HISTORY_DIR / "manifest.csv"
MANIFEST_FIELDS = ["series_key", "downloaded_at", "rows", "delete_rows", "bytes", "sha256", "url"]
_manifest_lock = threading.Lock()


def log_url(key: str) -> str:
    """The address of one series' full history. The key leaves out the leading "RTD."
    (that is the dataset name); detail=dataonly drops ~20 attribute columns that are
    the same on every row."""
    return f"{API}/{key[4:]}?includeHistory=true&format=csvdata&detail=dataonly"


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
    """One row per series, with its attributes but no observations (detail=nodata).

    This is how data/raw/series_metadata.csv was made. get_data.py calls it only
    when asked to with --update-series-list.
    """
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


def download_structure() -> bytes:
    """The ECB's code lists for the RTD (what each code in a series key means).
    This is how data/raw/rtd_structure.xml was made."""
    return fetch(f"{API.replace('/data/RTD', '/dataflow/ECB/RTD')}?references=all")


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


def download_log(key: str) -> tuple[bool, str]:
    """Save one series' history to data/raw/history/<KEY>.csv.

    Returns (succeeded, a line saying what happened). The file is kept only if two
    separate downloads are byte-for-byte identical and pass problem_with(); a file
    already on disk is replaced only by one that passes.
    """
    path = HISTORY_DIR / f"{key}.csv"
    url = log_url(key)
    start = time.time()
    reason = ""
    for attempt in range(1, 5):
        try:
            first = fetch(url)
            second = fetch(url)
        except urllib.error.HTTPError as e:
            return False, f"{key}: the ECB answered {e.code} {e.reason}"
        except (urllib.error.URLError, TimeoutError) as e:
            return False, f"{key}: cannot reach the ECB's server ({getattr(e, 'reason', e)})"
        reason = problem_with(first, key) or problem_with(second, key) or (
            "" if first == second else "two downloads differ")
        if not reason:
            break
        if attempt < 4:
            # The server's cache can keep serving the same cut-off copy for 20+
            # minutes, so retrying straight away just gets that copy again.
            wait = 120 * attempt
            print(f"    {key}: download {attempt} rejected ({reason}); "
                  f"trying again in {wait // 60} min", flush=True)
            time.sleep(wait)
    else:
        return False, f"{key}: 4 downloads rejected ({reason}); nothing saved"

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    # Write to a temporary name first, so a crash never leaves a half-written
    # file under the real name.
    tmp = path.with_suffix(".part")
    tmp.write_bytes(first)
    tmp.replace(path)

    rows = pd.read_csv(io.BytesIO(first), usecols=["ACTION"], dtype=str)["ACTION"]
    n_delete = int((rows == "Delete").sum())
    record_download(key, url, first, len(rows), n_delete)
    return True, (f"{key}: {len(rows):,} rows ({n_delete:,} removals), "
                  f"{time.time() - start:.0f} s")


def record_download(key: str, url: str, content: bytes, rows: int, deletes: int) -> None:
    """Add one downloaded log to data/raw/history/manifest.csv."""
    row = {"series_key": key,
           "downloaded_at": datetime.datetime.now(datetime.timezone.utc)
                                    .isoformat(timespec="seconds"),
           "rows": rows, "delete_rows": deletes, "bytes": len(content),
           "sha256": hashlib.sha256(content).hexdigest(), "url": url}
    with _manifest_lock:
        new = not MANIFEST.exists()
        with MANIFEST.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
            if new:
                writer.writeheader()
            writer.writerow(row)


def downloaded_on(key: str) -> str:
    """The day a series' log on disk was downloaded: from the manifest, or else the
    day the file was last written."""
    path = HISTORY_DIR / f"{key}.csv"
    if MANIFEST.exists():
        manifest = pd.read_csv(MANIFEST, dtype=str)
        mine = manifest[manifest["series_key"] == key]
        if len(mine):
            return mine["downloaded_at"].iloc[-1][:10]
    return datetime.date.fromtimestamp(path.stat().st_mtime).isoformat()
