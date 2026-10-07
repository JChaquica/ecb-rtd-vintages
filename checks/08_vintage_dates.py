"""Check the vintage dates against the ECB's calendar of monetary policy decisions.

Why this check matters: checks/07_eurostat.py tests the content of the
newest vintage and checks/03_old_files.py the vintages of 2001-2014. Neither
says whether the vintage dates after 2014 are the right ones. The RTD page says the
data are frozen "on a working day ahead of the Governing Council meeting at precise
time 3.30 p.m.", and the dates of those meetings are published by the ECB itself, so
they are a record of the dates that does not come from the RTD.

The check runs both ways.
* From each vintage to the next decision: is the vintage on the working day before
  it? A vintage that fails is on a date the calendar does not explain.
* From each decision to the vintages: was a vintage frozen for it? A decision that
  fails is a meeting with no vintage, which the first direction cannot see.

The calendar
------------
The ECB lists its monetary policy publications for each year at
/press/govcdec/mopo/<year>/, from 2001, with an isoDate for each. The list also holds
meeting accounts and other releases, so only entries titled exactly "Monetary policy
decisions" are kept. That leaves 24 in 2001, two a month, against one vintage a month;
twelve a year from 2002 to 2014, except thirteen in 2008, which adds 8 October; and
eight a year from 2015 to 2025. The pages are kept in data/raw/ecb_calendar/, exactly
as the ECB sent them, and recorded in its manifest.csv. The repository holds the pages
of 5 October 2026, so the check runs without the internet until the vintages reach a
year it has no page for.

"Working day" means the previous weekday. Public holidays are not modelled; every
vintage from 2015 falls on the weekday before its decision, so none is needed there.

What it reports
---------------
From 2015 the check is exact, and the script fails (exit code 1) if a vintage is not
on the working day before a decision, or if a decision has no vintage and is not one
of the KNOWN_GAPS below. Before 2015 it lists the exceptions without failing: 13
vintages not on the working day before a decision, and 16 decisions with no vintage
of their own.

Output: output/vintage_date_check.csv    one row per vintage
        output/decision_date_check.csv   one row per decision
        output/logs/08_vintage_dates.txt

It needs only data/processed/vintage_dates.csv, which the repository keeps, so it
runs before any data are downloaded.

    python checks/08_vintage_dates.py
"""

from __future__ import annotations

import csv
import hashlib
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(os.path.abspath(__file__)).parents[1] / "code"))

from common import LOG_DIR, OUTPUT, RAW, ROOT, VINTAGE_DATES  # noqa: E402

CALENDAR_DIR = RAW / "ecb_calendar"
OUT_VINTAGES = OUTPUT / "vintage_date_check.csv"
OUT_DECISIONS = OUTPUT / "decision_date_check.csv"
LOG_PATH = LOG_DIR / "08_vintage_dates.txt"

FIRST_YEAR = 2001
TESTED_FROM = "2015-01-01"
INDEX = "https://www.ecb.europa.eu/press/govcdec/mopo/{year}/html/index_include.en.html"
HEADERS = {"User-Agent": "Mozilla/5.0 (research; RTD vintage date check)"}
TITLE = "monetary policy decisions"

# Decisions from 2015 known to have no vintage, with the reason. Any other one fails.
KNOWN_GAPS = {
    "2026-07-23": "no series changed between the vintages of 10 June and 9 September 2026",
}

ENTRY = re.compile(r'<dt\s+isoDate="(\d{4}-\d{2}-\d{2})"[^>]*>(.*?)</dd>', re.S | re.I)
ENTRY_TITLE = re.compile(r'<div class="title">\s*<a[^>]*>\s*([^<]+?)\s*</a>', re.S | re.I)


def calendar_page(year: int) -> str | None:
    """The ECB's list of monetary policy publications for one year, kept on disk."""
    CALENDAR_DIR.mkdir(parents=True, exist_ok=True)
    path = CALENDAR_DIR / f"govcdec_mopo_{year}.html"
    if not path.exists():
        url = INDEX.format(year=year)
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=90) as response:
                data = response.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        path.write_bytes(data)          # as sent, so that its checksum is the one recorded
        record_download(path, url, data)
    return path.read_bytes().decode("utf-8", "replace")


def record_download(path: Path, url: str, data: bytes) -> None:
    """Add one downloaded page to data/raw/ecb_calendar/manifest.csv."""
    manifest = CALENDAR_DIR / "manifest.csv"
    row = {"file": path.name, "url": url,
           "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    new = not manifest.exists()
    with manifest.open("a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(row))
        if new:
            writer.writeheader()
        writer.writerow(row)


def decision_dates(last_year: int) -> list[date]:
    """Every day on which the ECB published its monetary policy decisions."""
    found = set()
    for year in range(FIRST_YEAR, last_year + 1):
        page = calendar_page(year)
        if page is None:
            sys.exit(f"no calendar page for {year}; cannot check the dates")
        for day, body in ENTRY.findall(page):
            title = ENTRY_TITLE.search(body)
            if title and title.group(1).strip().lower() == TITLE:
                found.add(date.fromisoformat(day))
    return sorted(found)


def working_day_before(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:  # Saturday or Sunday
        d -= timedelta(days=1)
    return d


def main() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    vintages = [date.fromisoformat(v) for v in pd.read_csv(VINTAGE_DATES)["vintage_date"]]
    decisions = decision_dates(vintages[-1].year)
    tested = date.fromisoformat(TESTED_FROM)

    # From each vintage to the next decision.
    frozen_for: dict[date, list[date]] = {d: [] for d in decisions}
    vintage_rows = []
    for v in vintages:
        later = [d for d in decisions if d >= v]
        d = later[0] if later else None
        if d is not None:
            frozen_for[d].append(v)
        vintage_rows.append({
            "vintage_date": v,
            "next_decision": d or "",
            "days_before_decision": (d - v).days if d else "",
            "on_working_day_before": d is not None and v == working_day_before(d),
        })
    by_vintage = pd.DataFrame(vintage_rows)
    by_vintage.to_csv(OUT_VINTAGES, index=False)

    # From each decision to the vintages.
    decision_rows = []
    for d in decisions:
        before = [v for v in vintages if v <= d]
        after = [v for v in vintages if v > d]
        own = frozen_for[d]
        if own:
            status = "vintage on the working day before" if working_day_before(d) in own \
                else "vintage on another day"
        elif not after:
            status = "after the newest vintage"
        elif d.isoformat() in KNOWN_GAPS:
            status = "no vintage (known: " + KNOWN_GAPS[d.isoformat()] + ")"
        else:
            status = "no vintage"
        decision_rows.append({
            "decision_date": d,
            "vintages_frozen_for_it": " ".join(v.isoformat() for v in own),
            "previous_vintage": before[-1] if before else "",
            "next_vintage": after[0] if after else "",
            "status": status,
        })
    by_decision = pd.DataFrame(decision_rows)
    by_decision.to_csv(OUT_DECISIONS, index=False)

    recent_v = by_vintage[by_vintage["vintage_date"] >= tested]
    recent_d = by_decision[(by_decision["decision_date"] >= tested)
                           & (by_decision["status"] != "after the newest vintage")]
    off_day = recent_v[~recent_v["on_working_day_before"]]
    unexplained = recent_d[recent_d["status"] == "no vintage"]
    known = recent_d[recent_d["status"].str.startswith("no vintage (known")]
    early_v = by_vintage[(by_vintage["vintage_date"] < tested)
                         & ~by_vintage["on_working_day_before"]]
    early_d = by_decision[(by_decision["decision_date"] < tested)
                          & by_decision["status"].str.startswith("no vintage")]
    pending = by_decision[by_decision["status"] == "after the newest vintage"]

    lines = [
        f"decisions in the ECB's calendar: {len(decisions)}, {decisions[0]} to {decisions[-1]}",
        f"vintages: {len(vintages)}, {vintages[0]} to {vintages[-1]}",
        "",
        f"vintages on the working day before a decision: "
        f"{int(by_vintage['on_working_day_before'].sum())} of {len(by_vintage)}",
        f"   from 2015: {len(recent_v) - len(off_day)} of {len(recent_v)}",
        f"decisions from 2015 with a vintage on the working day before: "
        f"{int((recent_d['status'] == 'vintage on the working day before').sum())} "
        f"of {len(recent_d)}",
    ]
    for _, r in known.iterrows():
        lines.append(f"   no vintage for the decision of {r['decision_date']}: "
                     f"{KNOWN_GAPS[r['decision_date'].isoformat()]}")
    for _, r in unexplained.iterrows():
        lines.append(f"   NO VINTAGE for the decision of {r['decision_date']}, between the "
                     f"vintages of {r['previous_vintage']} and {r['next_vintage']}")
    for _, r in off_day.iterrows():
        lines.append(f"   VINTAGE OFF THE CALENDAR: {r['vintage_date']}, next decision "
                     f"{r['next_decision']}")
    for _, r in pending.iterrows():
        lines.append(f"   decision of {r['decision_date']} is after the newest vintage; "
                     f"download the logs again to add it")

    lines += ["", f"before 2015, vintages not on the working day before a decision: {len(early_v)}"]
    lines += [f"   {r['vintage_date']}  next decision {r['next_decision']}, "
              f"{r['days_before_decision']} days later" for _, r in early_v.iterrows()]
    lines += [f"before 2015, decisions with no vintage of their own: {len(early_d)}"]
    lines += [f"   {r['decision_date']}  between the vintages of {r['previous_vintage']} and "
              f"{r['next_vintage']}" for _, r in early_d.iterrows()]

    LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"log: {LOG_PATH.relative_to(ROOT).as_posix()}")
    sys.exit(1 if len(off_day) or len(unexplained) else 0)


if __name__ == "__main__":
    main()
