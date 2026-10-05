"""A small web page for taking part of the vintage data instead of all of it.

The whole dataset is 1.4 GB: 67,532 workbooks, one per variable per vintage.
Most people want a few series, or a few dates, and should not have to fetch the
rest to get them. This script starts a web server on this machine and opens a
page that lists the 278 variables and the 261 vintage dates. You tick what you
want and the server sends back one zip file holding exactly those files.

    python code/06_vintage_picker.py             # opens the page in a browser
    python code/06_vintage_picker.py --port 8000 # a fixed port, no browser

The page needs nothing from the internet and the server nothing outside the
standard library, so it also runs where pandas is not installed. Nothing leaves
this machine: the server listens on 127.0.0.1 only, and it only ever reads the
files under data/processed/.

What it serves
--------------
GET  /               the page (code/vintage_picker.html)
GET  /catalog.json   the 278 variables, the 261 dates, which files exist and how
                     big they are, so the page can show a size before you ask
POST /download       a selection (JSON) -> one zip file

Which files exist
-----------------
04_make_readable_vintages.py writes a workbook only for a vintage in which the
series has at least one observation, so a variable has between 1 and 261 of
them. output/series_status_by_vintage.csv records why one is missing ("not yet
published" before the series began, "no observations" for the gaps described in
README.md) and marks the vintages in which a series had fallen behind. Both the
page and the zip say so rather than leaving it to be noticed: the page strikes
through the dates that hold nothing for the variables ticked and marks the ones
where something had fallen behind, and the zip carries not_up_to_date.csv, a row
per run of dates in which a series chosen was behind or had no file.

Two shapes to choose from, the same two the repository holds
-----------------------------------------------------------
  workbooks  data/processed/vintages/<Indicator>/<Variable>/vintage_<date>.xlsx
             copied as they are, one file per variable per date, ALFRED layout
  table      data/processed/raw_vintages/<KEY>.csv, cut down to the vintage
             dates you chose: periods in rows, vintages in columns

Output: a zip file in the browser's download folder, holding the files, the rows
        of _variable_list.csv for the variables chosen, and SELECTION.txt, which
        records what was asked for and when.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import socket
import sys
import tempfile
import textwrap
import threading
import webbrowser
import zipfile
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1]
PAGE = Path(__file__).resolve().parent / "vintage_picker.html"
PROCESSED = ROOT / "data" / "processed"
VINTAGE_DIR = PROCESSED / "vintages"
RAW_VINTAGE_DIR = PROCESSED / "raw_vintages"
VARIABLE_LIST = VINTAGE_DIR / "_variable_list.csv"
VINTAGE_DATES = PROCESSED / "vintage_dates.csv"
STATUS_BY_VINTAGE = ROOT / "output" / "series_status_by_vintage.csv"

# One character per vintage date in the availability string sent to the page.
MISSING, CURRENT, BEHIND = "0", "1", "2"
AVAILABILITY = {"current": CURRENT, "behind": BEHIND}   # anything else: no file

# The columns of _variable_list.csv the page shows or the zip's manifest needs.
SHOWN = ["Folder", "Variable", "Frequency", "Unit", "Adjustment", "Area",
         "ECB title", "ECB series key", "First vintage"]


def wrap(text: str) -> list[str]:
    """A paragraph of SELECTION.txt, broken at the width the rest of it uses."""
    return textwrap.wrap(" ".join(text.split()), 79)


def read_csv(path: Path) -> list[dict[str, str]]:
    """Rows of a CSV written by the other scripts (they use a UTF-8 BOM)."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def folder_sizes(folder: Path) -> tuple[int, int]:
    """(number of files, total bytes) of one variable's workbooks."""
    if not folder.is_dir():
        return 0, 0
    files = [e for e in os.scandir(folder) if e.name.endswith(".xlsx")]
    return len(files), sum(e.stat().st_size for e in files)


def build_catalog() -> dict:
    """Everything the page needs to draw itself, read once when the server starts.

    A variable's 'avail' is one character per vintage date: 0 no file, 1 a file,
    2 a file but the series had fallen behind on that date. 'xlsxBytes' is the
    size of all its workbooks together, which the page divides by the number it
    found to estimate the size of a selection.
    """
    if not VARIABLE_LIST.exists():
        sys.exit(f"missing {VARIABLE_LIST.relative_to(ROOT)}\n"
                 "Run code/04_make_readable_vintages.py first, or run this script "
                 "in a copy of the repository that has data/processed/.")

    dates = [r["vintage_date"] for r in read_csv(VINTAGE_DATES)]
    status = {r["ECB series key"]: r for r in read_csv(STATUS_BY_VINTAGE)} \
        if STATUS_BY_VINTAGE.exists() else {}

    variables = []
    for v in read_csv(VARIABLE_LIST):
        key = v["ECB series key"]
        row = status.get(key, {})
        # No status file: fall back to "every date from the first vintage on".
        avail = "".join(AVAILABILITY.get(row.get(d, ""), MISSING) for d in dates) \
            if row else "".join(CURRENT if d >= v["First vintage"] else MISSING
                                for d in dates)
        n_files, n_bytes = folder_sizes(VINTAGE_DIR / v["Folder"] / v["Variable"])
        table = RAW_VINTAGE_DIR / f"{key}.csv"
        variables.append({
            **{k: v.get(k, "") for k in SHOWN},
            "avail": avail,
            "onDisk": n_files,
            "xlsxBytes": n_bytes,
            "csvBytes": table.stat().st_size if table.exists() else 0,
        })

    return {
        "generated": f"{datetime.datetime.now():%Y-%m-%d %H:%M}",
        "root": str(ROOT),
        "dates": dates,
        "variables": variables,
        "have": {"workbooks": any(v["onDisk"] for v in variables),
                 "tables": any(v["csvBytes"] for v in variables)},
    }


def cut_table(path: Path, keep: list[str]) -> bytes:
    """One raw_vintages table with only the chosen vintage columns.

    Periods with no value in any of them are dropped, so asking for a single
    vintage gives a two-column file of just that vintage's observations rather
    than one mostly empty row per period the series has ever had.
    """
    with path.open(encoding="utf-8", newline="") as f:
        rows = csv.reader(f)
        header = next(rows)
        at = [header.index(d) for d in keep if d in header]
        out = [[header[0], *(header[i] for i in at)]]
        for row in rows:
            values = [row[i] for i in at]
            if any(values):
                out.append([row[0], *values])
    text = "\r\n".join(",".join(cell for cell in row) for row in out) + "\r\n"
    return text.encode("utf-8")


def stale_runs(chosen: list[dict], picked: list[int], dates: list[str]) -> list[dict]:
    """Where, inside this selection, a series is behind or has no file at all.

    One row per run of consecutive chosen dates with the same trouble, so the 23
    vintages in which the unemployment series stood still are one row and not 23.
    """
    label = {MISSING: "no file", BEHIND: "behind"}
    rows: list[dict] = []
    for v in chosen:
        run = None
        for i in picked:
            this = label.get(v["avail"][i])
            if run and (this != run["Status"]):
                rows.append(run)
                run = None
            if this is None:
                continue
            if run:
                run["Last vintage"], run["Vintages"] = dates[i], run["Vintages"] + 1
            else:
                run = {"Folder": v["Folder"], "Variable": v["Variable"],
                       "ECB series key": v["ECB series key"], "Status": this,
                       "First vintage": dates[i], "Last vintage": dates[i],
                       "Vintages": 1}
        if run:
            rows.append(run)
    return rows


STALE_COLUMNS = ["Folder", "Variable", "ECB series key", "Status",
                 "First vintage", "Last vintage", "Vintages"]


def stale_csv(rows: list[dict]) -> bytes:
    """not_up_to_date.csv: the rows above, in the shape of the other output files."""
    out = [",".join(STALE_COLUMNS)]
    out += [",".join(csv_cell(str(r[c])) for c in STALE_COLUMNS) for r in rows]
    return ("\r\n".join(out) + "\r\n").encode("utf-8")


def selection_note(chosen: list[dict], dates: list[str], formats: list[str],
                   n_files: int, stale: list[dict]) -> bytes:
    """A short record of what was asked for, put in the zip as SELECTION.txt."""
    span = f"{dates[0]} to {dates[-1]}" if len(dates) > 1 else dates[0]
    behind = [r for r in stale if r["Status"] == "behind"]
    absent = [r for r in stale if r["Status"] == "no file"]
    n_behind = sum(r["Vintages"] for r in behind)
    n_absent = sum(r["Vintages"] for r in absent)
    lines = [
        "Vintages of the ECB Real Time Database, 2001-2026",
        "A selection taken with code/06_vintage_picker.py.",
        "",
        f"Taken on       {datetime.datetime.now():%Y-%m-%d %H:%M}",
        f"Variables      {len(chosen)} of 278",
        f"Vintage dates  {len(dates)} ({span})",
        f"Shapes         {', '.join(formats)}",
        f"Files          {n_files}",
        "",
        "vintages/     one workbook per variable per vintage date, as the ECB had the",
        "              series on that date (first sheet describes it, second holds it).",
        "raw_vintages/ one table per series, periods in rows and the chosen vintage",
        "              dates in columns, which is the easier shape for revisions.",
        "_variable_list.csv  the rows of the full variable list for these variables.",
    ]

    lines += ["", "What is not sound in this selection", "-" * 35]
    if not stale:
        lines += ["Nothing: every variable chosen has a file on every date chosen, and none of",
                  "them had fallen behind on any of those dates."]
    else:
        if behind:
            n = len({r["Variable"] for r in behind})
            lines += wrap(
                f"Behind: {n} of the {len(chosen)} variables chosen "
                f"had fallen behind in {n_behind} of the "
                f"series-dates chosen, so that vintage does not show what was known on its "
                f"date. A series counts as behind when its last observation is older than is "
                f"usual for it by three months, two quarters or one year, by frequency.")
        if absent:
            n = len({r["Variable"] for r in absent})
            lines += wrap(
                f"No file: {n} of the variables chosen {'has' if n == 1 else 'have'} none "
                f"for {n_absent} of the series-dates chosen, and {'it was' if n == 1 else 'they were'} "
                f"skipped there. Either the series had not been published yet, or the ECB held "
                f"no observations for it in that vintage.")
        lines += ["",
                  "not_up_to_date.csv lists every one of them, a row per run of consecutive dates.",
                  "output/series_gaps.csv in the repository says the same for all 278 series."]

    lines += [
        "",
        "Source: European Central Bank, euro area Real Time Database,",
        "https://data.ecb.europa.eu/data/datasets/RTD/data-information",
        "The vintages after 2014 were rebuilt from the ECB's revision logs; the method",
        "and the checks are in docs/rtd_vintages_report.pdf in the repository.",
        "",
        "The variables in this zip, and what is wrong with them here:",
    ]
    for v in chosen:
        mine = [r for r in stale if r["Variable"] == v["Variable"]]
        trouble = [f"{word} {sum(r['Vintages'] for r in mine if r['Status'] == s)}"
                   for s, word in (("behind", "behind in"), ("no file", "no file for"))
                   if any(r["Status"] == s for r in mine)]
        note = f"  ({', '.join(trouble)} of {len(dates)} dates)" if trouble else ""
        lines.append(f"  {v['Folder']} / {v['Variable']}  [{v['ECB series key']}]{note}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def build_zip(catalog: dict, want: dict, log=print) -> tuple[Path, str]:
    """Write the selection to a temporary zip file; return it and a file name.

    Workbooks are stored, not deflated: an .xlsx is already a zip, so
    compressing it again costs seconds per hundred files and saves almost
    nothing. The CSV tables do compress, so those are deflated.
    """
    variables, dates = catalog["variables"], catalog["dates"]
    chosen = [variables[i] for i in want["variables"]]
    chosen_dates = [dates[i] for i in want["vintages"]]
    formats = want["formats"]

    fd, name = tempfile.mkstemp(prefix="rtd_selection_", suffix=".zip")
    os.close(fd)
    path = Path(name)
    stamp = f"{datetime.datetime.now():%Y%m%d-%H%M}"
    n_files = 0

    with zipfile.ZipFile(path, "w") as zf:
        if "workbooks" in formats:
            for v in chosen:
                src = VINTAGE_DIR / v["Folder"] / v["Variable"]
                for i in want["vintages"]:
                    if v["avail"][i] == MISSING:
                        continue                 # no workbook was ever written
                    f = src / f"vintage_{dates[i]}.xlsx"
                    if f.exists():
                        zf.write(f, f"vintages/{v['Folder']}/{v['Variable']}/{f.name}",
                                 compress_type=zipfile.ZIP_STORED)
                        n_files += 1
                log(f"  {v['Folder']} / {v['Variable']}: {n_files} files so far")

        if "tables" in formats:
            for v in chosen:
                src = RAW_VINTAGE_DIR / f"{v['ECB series key']}.csv"
                if src.exists():
                    zf.writestr(f"raw_vintages/{src.name}", cut_table(src, chosen_dates),
                                compress_type=zipfile.ZIP_DEFLATED)
                    n_files += 1

        header = ",".join(SHOWN)
        rows = [",".join(csv_cell(v[k]) for k in SHOWN) for v in chosen]
        zf.writestr("_variable_list.csv", "\r\n".join([header, *rows]) + "\r\n",
                    compress_type=zipfile.ZIP_DEFLATED)

        # Which of the files asked for are stale, and which were never written.
        stale = stale_runs(chosen, want["vintages"], dates)
        if stale:
            zf.writestr("not_up_to_date.csv", stale_csv(stale),
                        compress_type=zipfile.ZIP_DEFLATED)
            behind = sum(r["Vintages"] for r in stale if r["Status"] == "behind")
            absent = sum(r["Vintages"] for r in stale if r["Status"] == "no file")
            log(f"  {behind} series-dates behind, {absent} with no file "
                f"-> not_up_to_date.csv")
        zf.writestr("SELECTION.txt",
                    selection_note(chosen, chosen_dates, formats, n_files, stale),
                    compress_type=zipfile.ZIP_DEFLATED)

    return path, f"rtd_vintages_{stamp}.zip"


def csv_cell(text: str) -> str:
    """Quote a field the way the other scripts' CSVs do."""
    return f'"{text}"' if any(c in text for c in ',"\n') else text


class Handler(BaseHTTPRequestHandler):
    server_version = "rtd-vintage-picker"
    catalog: dict = {}

    def log_message(self, fmt: str, *args) -> None:
        pass                                     # the work logs itself; this is noise

    def send_bytes(self, body: bytes, kind: str, status=HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            if not PAGE.exists():
                self.send_bytes(b"vintage_picker.html is missing", "text/plain",
                                HTTPStatus.NOT_FOUND)
                return
            self.send_bytes(PAGE.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/catalog.json":
            self.send_bytes(json.dumps(self.catalog).encode("utf-8"),
                            "application/json; charset=utf-8")
        else:
            self.send_bytes(b"not found", "text/plain", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path != "/download":
            self.send_bytes(b"not found", "text/plain", HTTPStatus.NOT_FOUND)
            return
        try:
            want = self.read_selection()
        except ValueError as e:
            self.send_in_page_error(str(e))
            return

        n = len(want["variables"])
        print(f"\nbuilding a zip: {n} variable{'s' * (n != 1)}, "
              f"{len(want['vintages'])} vintage dates, {', '.join(want['formats'])}",
              flush=True)
        path = None
        try:
            path, name = build_zip(self.catalog, want)
            size = path.stat().st_size
            print(f"sending {name} ({size / 1e6:.1f} MB)", flush=True)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Content-Length", str(size))   # lets the browser show progress
            self.end_headers()
            with path.open("rb") as f:
                while chunk := f.read(1 << 20):
                    self.wfile.write(chunk)
            print("sent", flush=True)
        except (BrokenPipeError, ConnectionResetError):
            print("the browser gave up before the file was sent", flush=True)
        except Exception as e:                   # noqa: BLE001 - report, keep serving
            print(f"failed: {e}", flush=True)
            try:
                self.send_in_page_error(f"{type(e).__name__}: {e}")
            except Exception:
                pass
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    def read_selection(self) -> dict:
        """The posted selection, checked. Indices only, so no path can be asked for."""
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8")
        if self.headers.get("Content-Type", "").startswith(
                "application/x-www-form-urlencoded"):
            body = parse_qs(body).get("selection", [""])[0]
        try:
            want = json.loads(body)
        except json.JSONDecodeError:
            raise ValueError("the selection was not readable")

        n_vars, n_dates = len(self.catalog["variables"]), len(self.catalog["dates"])
        variables = sorted({i for i in want.get("variables", [])
                            if isinstance(i, int) and 0 <= i < n_vars})
        vintages = sorted({i for i in want.get("vintages", [])
                           if isinstance(i, int) and 0 <= i < n_dates})
        formats = [f for f in ("workbooks", "tables") if f in want.get("formats", [])]
        if not variables:
            raise ValueError("no variables were chosen")
        if not vintages:
            raise ValueError("no vintage dates were chosen")
        if not formats:
            raise ValueError("no shape was chosen")
        return {"variables": variables, "vintages": vintages, "formats": formats}

    def send_in_page_error(self, message: str) -> None:
        """The download goes to a hidden frame, so hand the message to the page."""
        safe = json.dumps(message)
        html = (f"<!doctype html><meta charset=utf-8><script>"
                f"parent.postMessage({{rtdError: {safe}}}, '*')</script>"
                f"{message}").encode("utf-8")
        self.send_bytes(html, "text/html; charset=utf-8")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, help="a fixed port (default: any free one)")
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser")
    args = ap.parse_args()

    print("reading the catalogue ...", flush=True)
    Handler.catalog = build_catalog()
    cat = Handler.catalog
    on_disk = sum(v["onDisk"] for v in cat["variables"])
    print(f"{len(cat['variables'])} variables, {len(cat['dates'])} vintage dates, "
          f"{on_disk} workbooks on disk")
    if not cat["have"]["workbooks"]:
        print("note: data/processed/vintages/ has no workbooks; only the tables "
              "can be downloaded")
    if not cat["have"]["tables"]:
        print("note: data/processed/raw_vintages/ is empty; only the workbooks "
              "can be downloaded")

    port = args.port or free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"\nthe picker is at {url}\npress Ctrl+C to stop it\n", flush=True)
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
