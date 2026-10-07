"""Compare the newest vintage with Eurostat, a source outside the ECB.

Why this check matters: checks/03_old_files.py can only test 2001-2014,
because that is all the ECB publishes as vintage files, and
checks/06_current_data.py compares the rebuild with the ECB's own data. From
2015 a check against a body outside the ECB is the only one that is not circular.
Eurostat compiles the euro-area national accounts that the RTD series are taken
from, so the newest vintage should carry the same figures.

What it reads: the newest column of the tables in data/processed/raw_vintages, the
same tables get_data.py turns into workbooks. It tests the data users get, not a
second rebuild of them.

What it does not show: Eurostat serves only its current figures, not the ones it
published in the past, so this checks the content of the newest vintage, not of the
earlier ones. checks/08_vintage_dates.py checks the dates of the vintages, and
checks/09_eurostat_releases.py compares chosen earlier vintages with Eurostat's
releases as they were published.

How the two sources line up
---------------------------
* Series: nine quarterly series have a direct Eurostat counterpart: GDP and private
  consumption in real and nominal terms, real government consumption, investment,
  exports and imports, and total employment. They are listed in SERIES below.
* Area: the RTD euro area is a moving concept, and its newest vintage is on the
  current membership, so the comparison uses Eurostat's EA21.
* Levels or growth: Eurostat's volumes are chain-linked to 2015 (CLV15_MEUR). The
  RTD's volumes are chain-linked to a different reference year, so their levels sit
  a constant factor above Eurostat's and are compared on quarter-on-quarter growth,
  which does not depend on the reference year. The level ratio is reported as well:
  if it drifted, the two would not be the same series. Current prices and persons
  have no reference year and are compared on levels.

What counts as agreement
------------------------
* Level series: equal at the number of decimals Eurostat prints, read from its file
  (one for millions of euro, two for thousands of persons). The RTD carries more
  decimals, so the unrounded values differ by up to half a unit in that last digit.
* Volume series: growth equal to within 0.001 percentage points.

Eurostat revises its figures, so every downloaded file is kept in data/raw/eurostat/,
exactly as Eurostat sent it, and recorded in data/raw/eurostat/manifest.csv with its
address, the time it was fetched and a checksum. The repository holds the files of
5 October 2026, so the check runs without the internet; delete the folder to compare
with Eurostat's figures of the day.

Output: output/eurostat_check.csv            one row per series
        output/eurostat_check_quarters.csv   one row per series and quarter
        output/logs/07_eurostat.txt

    python checks/07_eurostat.py
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(os.path.abspath(__file__)).parents[1] / "code"))

from common import (LOG_DIR, OUTPUT, RAW, ROOT, TABLE_DIR, VINTAGE_DATES,  # noqa: E402
                    require_full_build)

EUROSTAT_DIR = RAW / "eurostat"
OUT = OUTPUT / "eurostat_check.csv"
OUT_QUARTERS = OUTPUT / "eurostat_check_quarters.csv"
LOG_PATH = LOG_DIR / "07_eurostat.txt"

GEO = "EA21"
API = ("https://ec.europa.eu/eurostat/api/dissemination/sdmx/3.0/data/dataflow/"
       "ESTAT/{dataset}/1.0/{key}?format=csvdata")
GROWTH_TOLERANCE = 0.001  # percentage points

# label, RTD series, Eurostat dataset, Eurostat key without the area, compared on
SERIES = [
    ("Real GDP", "RTD.Q.S0.S.G_GDPM_TO_C.E",
     "namq_10_gdp", "Q.CLV15_MEUR.SCA.B1GQ", "growth"),
    ("Nominal GDP", "RTD.Q.S0.S.G_GDPM_TO_U.E",
     "namq_10_gdp", "Q.CP_MEUR.SCA.B1GQ", "level"),
    ("Real private consumption", "RTD.Q.S0.S.G_FCHI_TO_C.E",
     "namq_10_gdp", "Q.CLV15_MEUR.SCA.P31_S14_S15", "growth"),
    ("Nominal private consumption", "RTD.Q.S0.S.G_FCHI_TO_U.E",
     "namq_10_gdp", "Q.CP_MEUR.SCA.P31_S14_S15", "level"),
    ("Real government consumption", "RTD.Q.S0.S.G_FCGG_TO_C.E",
     "namq_10_gdp", "Q.CLV15_MEUR.SCA.P3_S13", "growth"),
    ("Real gross fixed capital formation", "RTD.Q.S0.S.G_GFCF_TO_C.E",
     "namq_10_gdp", "Q.CLV15_MEUR.SCA.P51G", "growth"),
    ("Real exports of goods and services", "RTD.Q.S0.S.G_XGS_TO_C.E",
     "namq_10_gdp", "Q.CLV15_MEUR.SCA.P6", "growth"),
    ("Real imports of goods and services", "RTD.Q.S0.S.G_MGS_TO_C.E",
     "namq_10_gdp", "Q.CLV15_MEUR.SCA.P7", "growth"),
    ("Total employment", "RTD.Q.S0.S.L_TEMP_TO.M",
     "namq_10_a10_e", "Q.THS_PER.TOTAL.SCA.EMP_DC", "level"),
]


def eurostat(dataset: str, key: str) -> tuple[pd.Series, int]:
    """One Eurostat series and the number of decimals Eurostat prints for it.

    Downloads it the first time and keeps the file, recording it in the manifest.
    """
    EUROSTAT_DIR.mkdir(parents=True, exist_ok=True)
    path = EUROSTAT_DIR / f"{dataset}__{key.replace('.', '_')}_{GEO}.csv"
    if not path.exists():
        url = API.format(dataset=dataset, key=f"{key}.{GEO}")
        with urllib.request.urlopen(url, timeout=120) as response:
            data = response.read()
            modified = response.headers.get("Last-Modified", "")
        path.write_bytes(data)          # as sent, so that its checksum is the one recorded
        record_download(path, url, data, modified)
    text = path.read_text(encoding="utf-8")

    raw = pd.read_csv(io.StringIO(text), dtype={"TIME_PERIOD": str, "OBS_VALUE": str})
    printed = raw["OBS_VALUE"].dropna()
    decimals = int(printed.map(lambda v: len(v.split(".")[1]) if "." in v else 0).max())
    series = raw.set_index("TIME_PERIOD")["OBS_VALUE"].astype(float).sort_index()
    return series[~series.index.duplicated()], decimals


def record_download(path: Path, url: str, data: bytes, modified: str) -> None:
    """Add one downloaded file to data/raw/eurostat/manifest.csv."""
    manifest = EUROSTAT_DIR / "manifest.csv"
    row = {
        "file": path.name,
        "url": url,
        "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "server_last_modified": modified,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "observations": max(len(data.decode("utf-8").splitlines()) - 1, 0),
    }
    new = not manifest.exists()
    with manifest.open("a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(row))
        if new:
            writer.writeheader()
        writer.writerow(row)


def newest_vintage(key: str, date: str) -> pd.Series:
    """One series as it stands in the newest vintage, read from its table."""
    table = pd.read_csv(TABLE_DIR / f"{key}.csv", usecols=["TIME_PERIOD", date],
                        dtype={"TIME_PERIOD": str}, float_precision="round_trip")
    return table.dropna(subset=[date]).set_index("TIME_PERIOD")[date].sort_index()


def compare(label: str, key: str, dataset: str, estat_key: str, how: str,
            date: str) -> tuple[dict, pd.DataFrame]:
    rtd = newest_vintage(key, date)
    est, decimals = eurostat(dataset, estat_key)
    quarters = rtd.index.intersection(est.index)
    r, e = rtd[quarters], est[quarters]
    ratio = r / e
    growth_gap = (r.pct_change() * 100 - e.pct_change() * 100).dropna()

    places = f"{decimals} decimal{'s' if decimals != 1 else ''}"
    if how == "level":
        differing = int((r.round(decimals) != e.round(decimals)).sum())
        measure = f"quarters that differ at {places}"
        worst, compared = differing, len(quarters)
    else:
        differing = int((growth_gap.abs() > GROWTH_TOLERANCE).sum())
        measure = "largest growth difference (percentage points)"
        worst, compared = float(growth_gap.abs().max()), len(growth_gap)

    row = {
        "series": label,
        "rtd_key": key,
        "eurostat": f"{dataset} {estat_key}.{GEO}",
        "compared_on": f"levels at {places}" if how == "level" else "quarterly growth",
        "quarters": compared,
        "first": quarters.min(),
        "last": quarters.max(),
        "measure": measure,
        "worst": worst,
        "quarters_outside_tolerance": differing,
        "largest_unrounded_level_difference": float((r - e).abs().max()),
        "level_ratio_min": float(ratio.min()),
        "level_ratio_max": float(ratio.max()),
        "agrees": differing == 0,
    }
    detail = pd.DataFrame({"series": label, "rtd_key": key, "quarter": quarters,
                           "rtd": r.to_numpy(), "eurostat": e.to_numpy(),
                           "level_ratio": ratio.to_numpy()})
    detail["growth_difference_pp"] = growth_gap.reindex(quarters).to_numpy()
    return row, detail


def main() -> None:
    require_full_build([key for _, key, *_ in SERIES])
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    latest = pd.read_csv(VINTAGE_DATES)["vintage_date"].iloc[-1]

    rows, details = [], []
    for label, key, dataset, estat_key, how in SERIES:
        row, detail = compare(label, key, dataset, estat_key, how, latest)
        rows.append(row)
        details.append(detail)
    summary = pd.DataFrame(rows)
    summary.to_csv(OUT, index=False)
    pd.concat(details, ignore_index=True).to_csv(OUT_QUARTERS, index=False)

    lines = [
        f"vintage of {latest} against Eurostat ({GEO}); Eurostat files in "
        f"{EUROSTAT_DIR.relative_to(ROOT).as_posix()}, listed in manifest.csv",
        f"series that agree: {int(summary['agrees'].sum())} of {len(summary)}",
        "",
    ]
    for r in rows:
        mark = "ok  " if r["agrees"] else "FAIL"
        if r["compared_on"] == "quarterly growth":
            lines.append(f"{mark} {r['series']:<36} {r['quarters']} quarters {r['first']} to "
                         f"{r['last']}, largest growth difference {r['worst']:.1e} pp; "
                         f"levels {r['level_ratio_min']:.5f} to {r['level_ratio_max']:.5f} "
                         f"times Eurostat's")
        else:
            lines.append(f"{mark} {r['series']:<36} {r['quarters']} quarters {r['first']} to "
                         f"{r['last']}, {r['worst']} differ at Eurostat's "
                         f"{r['compared_on'].removeprefix('levels at ')}; unrounded values "
                         f"differ by at most {r['largest_unrounded_level_difference']:g}")

    LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"log: {LOG_PATH.relative_to(ROOT).as_posix()}")
    sys.exit(0 if summary["agrees"].all() else 1)


if __name__ == "__main__":
    main()
