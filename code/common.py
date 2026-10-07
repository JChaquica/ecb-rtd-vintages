"""Where everything is, and the list of series. Shared by get_data.py and the checks."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

# abspath rather than resolve(): resolve() would turn a drive mapped with subst, a
# way round Excel's limit on path length (README.md), back into the long path.
ROOT = Path(os.path.abspath(__file__)).parents[1]

RAW = ROOT / "data" / "raw"
HISTORY_DIR = RAW / "history"                  # the ECB's change log of each series
METADATA = RAW / "series_metadata.csv"         # the ECB's list of the series
STRUCTURE = RAW / "rtd_structure.xml"          # the ECB's code lists for the RTD

PROCESSED = ROOT / "data" / "processed"
TABLE_DIR = PROCESSED / "raw_vintages"         # one revision table per series
WORKBOOK_DIR = PROCESSED / "vintages"          # one workbook per series per vintage
VARIABLE_LIST = PROCESSED / "variable_list.csv"
VINTAGE_DATES = PROCESSED / "vintage_dates.csv"
CHECKSUMS = PROCESSED / "checksums.csv"        # fingerprints of the published build
CONTENTS = PROCESSED / "contents.csv"          # what get_data.py has built so far
NOT_UP_TO_DATE = PROCESSED / "not_up_to_date.csv"

OUTPUT = ROOT / "output"
LOG_DIR = OUTPUT / "logs"

FREQ_CODE = {"Monthly": "M", "Quarterly": "Q", "Annual": "A"}


def variable_list() -> pd.DataFrame:
    """The 278 series, one row each, in the order that numbers them 1 to 278."""
    return pd.read_csv(VARIABLE_LIST, encoding="utf-8-sig", dtype=str).fillna("")


def vintage_dates() -> list[str]:
    """The vintage dates known to the repository, oldest first."""
    return pd.read_csv(VINTAGE_DATES, dtype=str)["vintage_date"].tolist()


def require_full_build(keys: list[str] | None = None, workbooks: bool = False) -> None:
    """Stop with a message unless data/processed/ holds these series (by default all
    278) with every vintage and every observation.

    A build of part of the data (a few series, a range of years) cannot be checked
    against sources that cover all of it. contents.csv, written by get_data.py,
    says what was built.
    """
    wanted = set(keys or variable_list()["ECB series key"])
    how = ("Run  python get_data.py --all  first (see README.md)." if keys is None else
           f"Run  python get_data.py --series {' '.join(sorted(wanted))}  first.")
    contents = (pd.read_csv(CONTENTS, dtype=str).fillna("") if CONTENTS.exists()
                else pd.DataFrame(columns=["ECB series key", "Form", "Vintages", "Observations"]))
    full = contents[(contents["Vintages"] == "all") & (contents["Observations"] == "all")]
    have = set(full.loc[full["Form"] == "table", "ECB series key"])
    if workbooks:
        have &= set(full.loc[full["Form"] == "workbooks", "ECB series key"])
    missing = wanted - have
    if missing:
        sys.exit(f"this check needs {'every series' if keys is None else 'these series'} with "
                 f"every vintage and observation, as tables{' and workbooks' if workbooks else ''}"
                 f"; {len(missing)} of {len(wanted)} are missing or cut down in "
                 f"data/processed/ (see contents.csv). {how}")
