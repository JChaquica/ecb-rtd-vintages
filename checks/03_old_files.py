"""Check the vintages rebuilt from the API against the 2001-2014 vintage files the
ECB still posts as zip downloads on the RTD page.

Why this check matters: the RTD page says the API covers vintages "from 2015
onwards" and that earlier vintages must be taken from the zip files. In fact
the API history starts in January 2001. If the API reproduces the zip files,
one source (the API) is enough for 2001 to today.

How the two sources line up
---------------------------
* Series: the old files use old SDW codes as column names. The link is the
  API attribute COMPILATION ("block Quarterly; series: BM"): BM is the Excel
  column letter of the series in quarterly_YYYYMM.csv. download_metadata() in
  code/download.py saved it as OLD_FILE_BLOCK / OLD_FILE_COLUMN in
  data/raw/series_metadata.csv.
* Dates: the old files are labelled by Monthly Bulletin month (YYYYMM), not by
  freeze date. A freeze in the last days of a month fed the NEXT month's
  Bulletin (e.g. the 2001-01-31 freeze is in file 200102). So each API vintage is
  given the month of (freeze date + 7 days), and file YYYYMM is compared with
  the latest vintage assigned to that month or earlier. Months with no freeze
  repeat the previous vintage.
* Periods: quarterly rows are labelled "Q1-1999" (API: "1999-Q1"), annual rows
  "1991". Monthly rows are "Jan-70" (two-digit year) in the early files and
  "JAN-1970" in later ones (API: "1970-01").

* Two letters are wrong and ten are missing. The two M3 annual growth series
  have their letters swapped in COMPILATION, so they are compared with each
  other's column (SWAPPED). Ten series have no letter at all; each was matched
  by comparing its data with the columns no other series points to, and found
  identical to exactly one of them in every file (NO_LETTER).

Two counts
----------
1. One verdict per (series, old file). A file "matches" when it has the same
   periods as the API vintage and every value is identical. Where the old file's
   column is empty there is nothing to compare, and the pair gets one of two other
   labels. "old file column empty" means the rebuilt vintage is empty too: the
   series did not exist yet. "old file column empty, rebuilt vintage has data"
   means the ECB had stopped publishing the series in the Bulletin but had not yet
   removed it from the log, so the rebuilt vintage holds values the ECB's own file
   for that month leaves out (the five government bond yields from February 2008
   to March 2011, for example).

2. The same comparison counted cell by cell, so the result can be set beside a
   count made in cells. A cell is one period of one mapped column in one old file;
   NA, ND and a blank count as no number. Each cell gets one label:
       both numbers, equal
       both numbers, different
       zip has a number, rebuild has none
       rebuild has a number, zip has none
       neither has a number
   A rebuilt period with no row in the old file at all is counted separately, as
   "rebuild has a number, no row in the zip". The count is reported for all 278
   series, and again without the two raw material price series, which have no
   column letter in COMPILATION and are matched only by their data (NO_LETTER), so
   a count that relies on the letters cannot see them.

Output: data/raw/old_files/{monthly,quarterly,annual}.zip   downloaded once
        output/old_file_check.csv   one row per (series, old file): match or not, and why
        output/old_file_cells.csv   one row per (series, old file), cells by label
        output/logs/03_old_files.txt

    python checks/03_old_files.py
"""

from __future__ import annotations

import csv
import io
import os
import sys
import urllib.request
import zipfile
from datetime import timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(os.path.abspath(__file__)).parents[1] / "code"))

from common import LOG_DIR, METADATA, OUTPUT, RAW, ROOT, TABLE_DIR, require_full_build  # noqa: E402

OLD_DIR = RAW / "old_files"
CHECK_OUT = OUTPUT / "old_file_check.csv"
CELLS_OUT = OUTPUT / "old_file_cells.csv"
LOG_PATH = LOG_DIR / "03_old_files.txt"

ZIP_URL = "https://data.ecb.europa.eu/sites/default/files/2024-08/{block}.zip"
MONTHS = {m: f"{i:02d}" for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

# COMPILATION gives the adjusted M3 growth rate column FG and the unadjusted one
# EZ; the data are the other way round (the old codes agree: ...U2.Y... is in EZ).
SWAPPED = {"RTD.M.S0.N.M_M3BM_V_NC.A": "FG", "RTD.M.S0.Y.M_M3BM_V_NC.A": "EZ"}
# Series with no letter in COMPILATION: (block, column), found by matching the data.
NO_LETTER = {
    "RTD.Q.S0.S.G_GVAD_INFCOM_O.E": ("quarterly", "CS"),  # MNA.Q.Y.I7.W2.S1.S1.B.B1G._Z.J._Z.EUR.LR.N
    "RTD.M.S0.N.C_U202Y.E": ("monthly", "DI"),            # FM.M.U2.EUR.4F.BB.U2_2Y.YLD
    "RTD.M.S0.N.C_U203Y.E": ("monthly", "DJ"),            # FM.M.U2.EUR.4F.BB.U2_3Y.YLD
    "RTD.M.S0.N.C_U205Y.E": ("monthly", "DK"),            # FM.M.U2.EUR.4F.BB.U2_5Y.YLD
    "RTD.M.S0.N.C_U207Y.E": ("monthly", "DL"),            # FM.M.U2.EUR.4F.BB.U2_7Y.YLD
    "RTD.M.S0.N.C_U210Y.E": ("monthly", "DM"),            # FM.M.U2.EUR.4F.BB.U2_10Y.YLD
    "RTD.M.S0.N.P_P_DCOGO_DS.X": ("monthly", "ES"),       # STS.M.I7.N.PRIN.NS0060.4.000
    "RTD.M.S0.N.P_P_NCOGO_DS.X": ("monthly", "ET"),       # STS.M.I7.N.PRIN.NS0070.4.000
    "RTD.M.S0.N.P_R_TO.E": ("monthly", "EW"),             # RMP.M.I2.0100.E
    "RTD.M.S0.N.P_R_XNRGY.E": ("monthly", "EX"),          # RMP.M.I2.0200.E
}
RAW_MATERIALS = {"RTD.M.S0.N.P_R_TO.E", "RTD.M.S0.N.P_R_XNRGY.E"}
# The label for a pair in which the old file's column is empty and the rebuilt
# vintage is not (see the module docstring).
EMPTY_BUT_REBUILT = "old file column empty, rebuilt vintage has data"
MISSING = {"", "NA", "ND"}
LABELS = ["both numbers, equal", "both numbers, different",
          "zip has a number, rebuild has none", "rebuild has a number, zip has none",
          "neither has a number", "rebuild has a number, no row in the zip"]


def column_index(letters: str) -> int:
    """Excel column letters to a 0-based index: A -> 0, Z -> 25, BM -> 64."""
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def api_period(label: str) -> str | None:
    """Old-file row label to API TIME_PERIOD; None for header and blank rows."""
    label = label.strip()
    if label[:1] == "Q" and "-" in label:                 # Q1-1999 -> 1999-Q1
        q, year = label.split("-")
        return f"{year}-{q}"
    month = label[:3].title()                             # "JAN" and "Jan" -> "Jan"
    if month in MONTHS and "-" in label:                  # Jan-70 or JAN-1970 -> 1970-01
        year = int(label.split("-")[1])
        if year < 100:                                    # two-digit years before 2007
            year = 1900 + year if year >= 50 else 2000 + year
        return f"{year}-{MONTHS[month]}"
    if label.isdigit() and len(label) == 4:               # 1991 -> 1991
        return label
    return None


def read_old_file(raw: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(raw.decode("latin-1"))))


def old_column(rows: list[list[str]], j: int) -> pd.Series:
    """Column j of an old file as {TIME_PERIOD: value}, dropping NA cells."""
    values = {}
    for r in rows:
        if len(r) <= j:
            continue
        period = api_period(r[0])
        cell = r[j].strip()
        if period and cell not in ("", "NA"):
            values[period] = float(cell)
    return pd.Series(values, dtype=float)


def zip_column(rows: list[list[str]], j: int) -> dict[str, float | None]:
    """Every period row of column j, with None where the cell holds no number."""
    out: dict[str, float | None] = {}
    for r in rows:
        period = api_period(r[0]) if r else None
        if period is None:
            continue
        cell = r[j].strip() if len(r) > j else ""
        out[period] = None if cell in MISSING else float(cell)
    return out


def count_cells(old: dict[str, float | None], new: pd.Series) -> dict[str, int]:
    """One old file's column against one rebuilt vintage, cell by cell."""
    counts = dict.fromkeys(LABELS, 0)
    for period, z in old.items():
        a = new.get(period)
        if z is not None and a is not None:
            counts[LABELS[0] if z == a else LABELS[1]] += 1
        elif z is not None:
            counts[LABELS[2]] += 1
        elif a is not None:
            counts[LABELS[3]] += 1
        else:
            counts[LABELS[4]] += 1
    counts[LABELS[5]] = len(set(new.index) - set(old))
    return counts


def cell_summary(d: pd.DataFrame, title: str, n_files: int) -> list[str]:
    totals = d[LABELS].sum()
    disagree = d[LABELS[1:4] + LABELS[5:]].sum(axis=1)
    bad = d[disagree > 0]
    by_series = (bad.assign(cells=disagree[disagree > 0])
                   .groupby("key").agg(files=("old_file", "size"), cells=("cells", "sum"),
                                       first=("old_file", "min"), last=("old_file", "max"))
                   .sort_values("cells", ascending=False))
    return [
        f"== {title}: {d['key'].nunique()} series, {d['old_file'].nunique()} "
        f"bulletin months, {n_files} zip files",
        f"cells in mapped columns: {int(totals[LABELS[:5]].sum()):,}",
        *[f"  {label}: {int(totals[label]):,}" for label in LABELS],
        f"cells that disagree: {int(disagree.sum()):,}",
        "  by block: " + ", ".join(f"{b} {int(disagree[d['block'] == b].sum()):,}"
                                  for b in ["monthly", "quarterly", "annual"]),
        f"zip files with at least one disagreeing cell: "
        f"{bad[['block', 'old_file']].drop_duplicates().shape[0]} of {n_files}",
        "", "series with disagreeing cells:", by_series.to_string(), "",
    ]


def main() -> None:
    require_full_build()
    OLD_DIR.mkdir(parents=True, exist_ok=True)

    meta = pd.read_csv(METADATA, dtype=str)
    for key, letters in SWAPPED.items():
        meta.loc[meta["KEY"] == key, "OLD_FILE_COLUMN"] = letters
    for key, (block, letters) in NO_LETTER.items():
        meta.loc[meta["KEY"] == key, ["OLD_FILE_BLOCK", "OLD_FILE_COLUMN"]] = [block, letters]
    meta = meta.dropna(subset=["OLD_FILE_COLUMN"])
    results, cells = [], []
    unmatched: list[tuple[str, str]] = []   # (block, old code) of columns no series uses
    for block in ["annual", "quarterly", "monthly"]:
        zip_path = OLD_DIR / f"{block}.zip"
        if not zip_path.exists():
            print(f"downloading {block}.zip")
            urllib.request.urlretrieve(ZIP_URL.format(block=block), zip_path)
        archive = zipfile.ZipFile(zip_path)
        names = sorted(n for n in archive.namelist() if n.endswith(".csv"))
        files = {n[-10:-4]: read_old_file(archive.read(n)) for n in names}  # YYYYMM -> rows
        print(f"{block}: {len(files)} files")

        # Columns that hold data in some file but match none of the 278 series: series
        # the old files have and the web service does not.
        mapped = {column_index(c) for c in meta.loc[meta["OLD_FILE_BLOCK"] == block,
                                                    "OLD_FILE_COLUMN"]}
        codes: dict[int, str] = {}
        for rows in files.values():
            for r in rows:
                if r and api_period(r[0]):
                    for j, cell in enumerate(r[1:], 1):
                        if j not in mapped and j not in codes and cell.strip() not in MISSING:
                            codes[j] = rows[1][j] if len(rows) > 1 and len(rows[1]) > j else ""
        unmatched += [(block, codes[j]) for j in sorted(codes)]

        for _, s in meta[meta["OLD_FILE_BLOCK"] == block].iterrows():
            api = pd.read_csv(TABLE_DIR / f"{s['KEY']}.csv", index_col=0,
                              dtype={"TIME_PERIOD": str}, float_precision="round_trip")
            # Bulletin month of each API vintage = month of (freeze date + 7 days)
            bulletin = {v: (pd.Timestamp(v) + timedelta(days=7)).strftime("%Y%m")
                        for v in api.columns}
            j = column_index(s["OLD_FILE_COLUMN"])

            for yyyymm, rows in files.items():
                candidates = [v for v in api.columns if bulletin[v] <= yyyymm]
                v = candidates[-1]
                old = old_column(rows, j)
                new = api[v].dropna()
                same_periods = set(old.index) == set(new.index)
                common = old.index.intersection(new.index)
                close = old[common].to_numpy() == new[common].to_numpy()
                if len(old) == 0 and len(new) > 0:
                    status = EMPTY_BUT_REBUILT
                elif len(old) == 0:
                    status = "old file column empty"
                elif not same_periods:
                    status = "different periods"
                elif not close.all():
                    status = "different values"
                else:
                    status = "match"
                results.append({
                    "key": s["KEY"], "block": block, "old_file": yyyymm, "api_vintage": v,
                    "status": status, "n_old": len(old), "n_api": len(new),
                    "n_values_differ": int((~close).sum()),
                    "old_code": rows[1][j] if len(rows) > 1 and len(rows[1]) > j else "",
                })
                cells.append({"key": s["KEY"], "block": block, "old_file": yyyymm,
                              "api_vintage": v, **count_cells(zip_column(rows, j), new)})

    df = pd.DataFrame(results)
    df.to_csv(CHECK_OUT, index=False)
    by_cell = pd.DataFrame(cells)
    by_cell.to_csv(CELLS_OUT, index=False)

    # Where the old file's column is empty there is nothing to compare, so only
    # files where the column has data count as comparisons.
    with_data = df[~df["status"].str.startswith("old file column empty")]
    kept = df[df["status"] == EMPTY_BUT_REBUILT]
    by_series = (with_data.assign(match=with_data["status"].eq("match"))
                   .groupby("key")
                   .agg(files=("match", "size"), matched=("match", "sum")))
    failing = by_series[by_series["matched"] < by_series["files"]]
    matched = with_data[with_data["status"] == "match"]
    lines = [
        f"compared {len(df)} (series, old file) pairs for {df['key'].nunique()} series",
        "", "status counts:", df["status"].value_counts().to_string(), "",
        f"pairs where the old file has data: {len(with_data)}, of which match: {len(matched)}",
        f"values in matching pairs (all identical): {matched['n_old'].sum():,}",
        f"series matching in every file where their column has data: "
        f"{len(by_series) - len(failing)} of {len(by_series)}",
        f"pairs where the old file's column is empty but the rebuilt vintage has data: "
        f"{len(kept)}, in {kept['key'].nunique()} series, {kept['n_api'].sum():,} values",
        "", "series that fail in some file (key, files with data, matched):",
        failing.to_string() if len(failing) else "none",
        "", f"columns of the old files that hold data but match no RTD series, so are not in "
            f"the web service: {len(unmatched)} ("
            + ", ".join(f"{b} {sum(x == b for x, _ in unmatched)}"
                        for b in ["annual", "quarterly", "monthly"]) + "); their old codes:",
        *[f"  {b:<10} {code}" for b, code in unmatched],
    ]
    n_files = len(by_cell[["block", "old_file"]].drop_duplicates())
    cell_lines = (cell_summary(by_cell, "cell by cell, all series", n_files)
                  + cell_summary(by_cell[~by_cell["key"].isin(RAW_MATERIALS)],
                                 "cell by cell, without the two raw material series", n_files))
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text("\n".join(lines + ["", ""] + cell_lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:12]))
    print(f"details: {CHECK_OUT.relative_to(ROOT).as_posix()}, {CELLS_OUT.relative_to(ROOT).as_posix()}; "
          f"summary: {LOG_PATH.relative_to(ROOT).as_posix()}")
    sys.exit(1 if len(failing) else 0)


if __name__ == "__main__":
    main()
