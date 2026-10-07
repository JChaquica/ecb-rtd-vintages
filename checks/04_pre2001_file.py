"""Describe the pre-2001 download on the RTD page, rtdb38.zip.

The page describes it as 38 variables with vintages before January 2001, in CSV.
This script counts what is actually in it: how many workbooks, which of them
hold vintage columns, the first and last vintage in each, and whether any
vintage date is stored as a plain number rather than as a date (Excel then
displays it as an integer such as 38231 instead of 1 September 2004).

Layout of a workbook: the first sheet has a few label rows (Block, Name,
Description, Units), then one header row whose cells are the vintage dates,
then one row per period. The header row is found as the row with the most
cells holding a date between 1995 and 2015, whether typed as a date or as a
number.

Output: data/raw/old_files/rtdb38.zip            downloaded once
        output/pre2001_bundle.csv                one row per workbook
        output/logs/04_pre2001_file.txt          summary

    python checks/04_pre2001_file.py
"""

from __future__ import annotations

import os
import sys
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd
import xlrd

sys.path.insert(0, str(Path(os.path.abspath(__file__)).parents[1] / "code"))

from common import LOG_DIR, OUTPUT, RAW, ROOT  # noqa: E402

ZIP_PATH = RAW / "old_files" / "rtdb38.zip"
ZIP_URL = "https://data.ecb.europa.eu/sites/default/files/2024-08/rtdb38.zip"
CSV_OUT = OUTPUT / "pre2001_bundle.csv"
LOG_PATH = LOG_DIR / "04_pre2001_file.txt"

# Excel serial numbers for 1 January 1995 and 1 January 2015.
LOW, HIGH = 34700, 42005
FIRST_RTD_DAY = pd.Timestamp("2001-01-01")


def serial_to_date(x: float) -> pd.Timestamp:
    return pd.Timestamp("1899-12-30") + pd.Timedelta(days=int(x))


def vintage_header(sheet: xlrd.sheet.Sheet) -> tuple[int, list[tuple[int, int, float]]]:
    """Row index of the vintage header and its (column, cell type, serial) triples."""
    best_row, best = -1, list[tuple[int, int, float]]()
    for r in range(min(sheet.nrows, 15)):
        cells = [(c, sheet.cell_type(r, c), sheet.cell_value(r, c)) for c in range(1, sheet.ncols)]
        dates = [(c, t, float(v)) for c, t, v in cells
                 if t in (xlrd.XL_CELL_DATE, xlrd.XL_CELL_NUMBER) and LOW <= float(v) <= HIGH]
        if len(dates) > len(best):
            best_row, best = r, dates
    return best_row, best


def has_numbers(sheet: xlrd.sheet.Sheet, header_row: int, c: int) -> bool:
    """True when the vintage column holds at least one number (missing cells read 'NaN')."""
    return any(sheet.cell_type(r, c) == xlrd.XL_CELL_NUMBER
               for r in range(header_row + 1, sheet.nrows))


def main() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not ZIP_PATH.exists():
        print("downloading rtdb38.zip")
        ZIP_PATH.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(ZIP_URL, ZIP_PATH)

    archive = zipfile.ZipFile(ZIP_PATH)
    names = sorted(archive.namelist())
    workbooks = [n for n in names if n.lower().endswith(".xls")]
    others = [n for n in names if not n.endswith("/") and n not in workbooks]

    rows = []
    for name in workbooks:
        book = xlrd.open_workbook(file_contents=archive.read(name))
        sheet = book.sheet_by_index(0)
        header_row, header = vintage_header(sheet)
        # A workbook "has vintage columns" when its header row holds at least two
        # dates; legend.xls, positions.xls and the Area-Wide Model file do not.
        has_vintages = len(header) >= 2
        dates = [serial_to_date(v) for _, _, v in header] if has_vintages else []
        as_number = [serial_to_date(v) for _, t, v in header
                     if t == xlrd.XL_CELL_NUMBER] if has_vintages else []
        # The header can run past the last column with data: the 10-year bond
        # yield has vintage dates to June 2009 but empty columns after January 2008.
        filled = [serial_to_date(v) for c, _, v in header
                  if has_numbers(sheet, header_row, c)] if has_vintages else []
        name_row = [sheet.cell_value(r, 1) for r in range(min(sheet.nrows, 4))
                    if sheet.ncols > 1 and sheet.cell_value(r, 0) == "Name:"]
        rows.append({
            "workbook": name.split("/")[-1],
            "sheets": ", ".join(book.sheet_names()),
            "old_series_code": name_row[0] if name_row else "",
            "has_vintage_columns": has_vintages,
            "vintage_columns": len(header) if has_vintages else 0,
            "first_vintage": min(dates).date() if dates else "",
            "last_vintage": max(dates).date() if dates else "",
            "last_vintage_with_data": max(filled).date() if filled else "",
            "vintages_before_2001": sum(d < FIRST_RTD_DAY for d in dates),
            "dates_stored_as_numbers": len(as_number),
            "numbers_first": min(as_number).date() if as_number else "",
            "numbers_last": max(as_number).date() if as_number else "",
        })

    df = pd.DataFrame(rows)
    df.to_csv(CSV_OUT, index=False)

    v = df[df["has_vintage_columns"]]
    last_counts = (v.groupby(["last_vintage", "last_vintage_with_data"]).size()
                   .rename("workbooks").sort_values(ascending=False))
    pre = v[v["vintages_before_2001"] > 0]
    num = v[v["dates_stored_as_numbers"] > 0]
    lines = [
        f"files in rtdb38.zip: {len([n for n in names if not n.endswith('/')])}",
        f"  Excel workbooks: {len(workbooks)}",
        f"  other files: {', '.join(others)}",
        f"workbooks with vintage columns: {len(v)}",
        f"workbooks without: {', '.join(df.loc[~df['has_vintage_columns'], 'workbook'])}",
        "", "last vintage in the header, last vintage with numbers, number of workbooks:",
        last_counts.to_string(),
        "", "workbooks with vintages before January 2001:",
        pre[["workbook", "vintages_before_2001", "first_vintage"]].to_string(index=False)
        if len(pre) else "none",
        "", "workbooks with vintage dates stored as numbers:",
        num[["workbook", "dates_stored_as_numbers", "numbers_first", "numbers_last"]]
        .to_string(index=False) if len(num) else "none",
    ]
    LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"details: {CSV_OUT.relative_to(ROOT).as_posix()}; summary: {LOG_PATH.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
