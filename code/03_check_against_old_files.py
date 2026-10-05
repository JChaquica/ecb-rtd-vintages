"""Check the vintages rebuilt from the API (02_build_vintages.py) against the
2001-2014 vintage files the ECB still posts as zip downloads on the RTD page.

Why this check matters: the RTD page says the API covers vintages "from 2015
onwards" and that earlier vintages must be taken from the zip files. In fact
the API history starts in January 2001. If the API reproduces the zip files,
one source (the API) is enough for 2001 to today.

How the two sources line up
---------------------------
* Series: the old files use old SDW codes as column names. The link is the
  API attribute COMPILATION ("block Quarterly; series: BM"): BM is the Excel
  column letter of the series in quarterly_YYYYMM.csv. 01_download_rtd_history.py
  saved it as OLD_FILE_BLOCK / OLD_FILE_COLUMN in series_metadata.csv.
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

A file "matches" when it has the same periods as the API vintage and every
value is identical.

Where the old file's column is empty there is nothing to compare, and the pair
gets one of two other labels. "old file column empty" means the rebuilt vintage
is empty too: the series did not exist yet. "old file column empty, rebuilt
vintage has data" means the ECB had stopped publishing the series in the
Bulletin but had not yet removed it from the log, so the rebuilt vintage holds
values the ECB's own file for that month leaves out (the five government bond
yields from February 2008 to March 2011, for example).

Output: data/raw/old_files/{monthly,quarterly,annual}.zip   downloaded once
        output/old_file_check.csv     one row per (series, old file): match or not, and why
        output/logs/03_check_against_old_files.txt   summary by series

    python code/03_check_against_old_files.py
"""

from __future__ import annotations

import csv
import io
import urllib.request
import zipfile
from datetime import timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OLD_DIR = ROOT / "data" / "raw" / "old_files"
METADATA = ROOT / "data" / "raw" / "series_metadata.csv"
VINTAGE_DIR = ROOT / "data" / "processed" / "raw_vintages"
CHECK_OUT = ROOT / "output" / "old_file_check.csv"
LOG_PATH = ROOT / "output" / "logs" / "03_check_against_old_files.txt"

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
# The label for a pair in which the old file's column is empty and the rebuilt
# vintage is not (see the module docstring).
EMPTY_BUT_REBUILT = "old file column empty, rebuilt vintage has data"


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


def main() -> None:
    OLD_DIR.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    meta = pd.read_csv(METADATA, dtype=str)
    for key, letters in SWAPPED.items():
        meta.loc[meta["KEY"] == key, "OLD_FILE_COLUMN"] = letters
    for key, (block, letters) in NO_LETTER.items():
        meta.loc[meta["KEY"] == key, ["OLD_FILE_BLOCK", "OLD_FILE_COLUMN"]] = [block, letters]
    meta = meta.dropna(subset=["OLD_FILE_COLUMN"])
    results = []
    for block in ["annual", "quarterly", "monthly"]:
        zip_path = OLD_DIR / f"{block}.zip"
        if not zip_path.exists():
            print(f"downloading {block}.zip")
            urllib.request.urlretrieve(ZIP_URL.format(block=block), zip_path)
        archive = zipfile.ZipFile(zip_path)
        names = sorted(n for n in archive.namelist() if n.endswith(".csv"))
        files = {n[-10:-4]: read_old_file(archive.read(n)) for n in names}  # YYYYMM -> rows
        print(f"{block}: {len(files)} files")

        for _, s in meta[meta["OLD_FILE_BLOCK"] == block].iterrows():
            vintage_file = VINTAGE_DIR / f"{s['KEY']}.csv"
            if not vintage_file.exists():
                continue
            api = pd.read_csv(vintage_file, index_col=0, dtype={"TIME_PERIOD": str})
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

    df = pd.DataFrame(results)
    df.to_csv(CHECK_OUT, index=False)

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
    ]
    LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:12]))
    print(f"details: {CHECK_OUT.relative_to(ROOT)}; summary: {LOG_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
