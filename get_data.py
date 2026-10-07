"""Get vintages of the ECB Real Time Database: all of them, or only the series, years
and form you ask for.

    python get_data.py              asks in the terminal what to get
    python get_data.py --list       lists the 278 series with their numbers
    python get_data.py --all        every series, every vintage, tables and workbooks
    python get_data.py --series "Real GDP" 21 --vintages 2008-2012 --form workbooks

Series can be named by their number in --list, their name, their ECB key (with or
without the leading "RTD.") or the name of an indicator folder, which takes every
series in it.

What it does
------------
1. Downloads the change log of each series asked for from the ECB's data portal
   (code/download.py), one request per series. A log already in data/raw/history/
   is used as it is unless --refresh asks for it again. The ECB only ever adds to a
   log, so a new download adds the vintages published since and leaves the earlier
   ones as they were.
2. Rebuilds every vintage of each series from its log (code/vintages.py) and
   compares the result with the published build's fingerprints in
   data/processed/checksums.csv.
3. Writes the forms asked for, cut to the vintages and observations asked for:
     raw        data/raw/history/<KEY>.csv, the ECB's log as downloaded (always kept)
     tables     data/processed/raw_vintages/<KEY>.csv, periods by vintage dates
     workbooks  data/processed/vintages/<Indicator>/<Variable>/vintage_<DATE>.xlsx
   A form of a series written before is replaced; other series are left alone.
4. Records what data/processed/ holds (contents.csv) and the stretches of vintages
   in which a series it holds had fallen behind or had no observations
   (not_up_to_date.csv; code/series_status.py says how that is judged).

A run with every series also rewrites data/processed/vintage_dates.csv and
variable_list.csv, which the repository keeps, and writes checksums.csv if it is
missing.
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import os
import re
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "code"))

import download                                  # noqa: E402  (needs the path above)
import series_status                             # noqa: E402
import vintages                                  # noqa: E402
import workbooks                                 # noqa: E402
from common import (CHECKSUMS, CONTENTS, FREQ_CODE, HISTORY_DIR, METADATA,  # noqa: E402
                    NOT_UP_TO_DATE, PROCESSED, ROOT, STRUCTURE, TABLE_DIR, VARIABLE_LIST,
                    VINTAGE_DATES, WORKBOOK_DIR, variable_list, vintage_dates)

FORMS = ["raw", "tables", "workbooks"]
DEFAULT_FORMS = ["tables", "workbooks"]
# Rough sizes, from the full build: a workbook, and one vintage column of a table.
WORKBOOK_BYTES = 11_700
TABLE_COLUMN_BYTES = 1_650

CONTENTS_COLUMNS = ["Folder", "Variable", "ECB series key", "Form", "Vintages",
                    "Observations", "Files", "Vintage dates", "Newest vintage",
                    "Log downloaded", "Same as published build", "Built"]
GAP_COLUMNS = ["Folder", "Variable", "ECB series key", "Problem", "First vintage affected",
               "Last vintage affected", "Vintages affected"]


# ---- Reading what was asked for --------------------------------------------------

def catalog() -> pd.DataFrame:
    """The 278 series, numbered 1 to 278 in the order of variable_list.csv."""
    cat = variable_list()
    cat.insert(0, "No", range(1, len(cat) + 1))
    return cat.reset_index(drop=True)


def print_list(cat: pd.DataFrame) -> None:
    for folder, rows in cat.groupby("Folder", sort=False):
        print(f"\n{folder}")
        for no, name, freq, key in zip(rows["No"], rows["Variable"], rows["Frequency"],
                                       rows["ECB series key"]):
            print(f"  {no:>3}  {name:<62} {freq:<9} {key}")


def print_indicators(cat: pd.DataFrame) -> None:
    """The 24 indicator folders with the numbers of their series, in two columns."""
    lines = [f"{lo:>3}{'-' + str(hi) if hi > lo else '':<4}  {folder} ({len(rows)})"
             for folder, rows in cat.groupby("Folder", sort=False)
             for lo, hi in [(rows["No"].min(), rows["No"].max())]]
    half = (len(lines) + 1) // 2
    for left, right in zip(lines[:half], lines[half:] + [""]):
        print(f"   {left:<44}{right}")


def resolve(token: str, cat: pd.DataFrame) -> list[int]:
    """Row positions of the series a word on the command line names."""
    t = token.strip()
    if t.isdigit() and 1 <= int(t) <= len(cat):
        return [int(t) - 1]
    if m := re.fullmatch(r"(\d+)-(\d+)", t):
        lo, hi = int(m[1]), int(m[2])
        if 1 <= lo <= hi <= len(cat):
            return list(range(lo - 1, hi))
    key = t.upper() if t.upper().startswith("RTD.") else f"RTD.{t.upper()}"
    for column, wanted in (("ECB series key", key.lower()), ("Variable", t.lower()),
                           ("Folder", t.lower())):
        hits = cat.index[cat[column].str.lower() == wanted].tolist()
        if hits:
            return hits
    names = cat["Variable"].str.lower().str.replace(" ", "").tolist()
    close = search(t, cat)[:5] or [names.index(n) for n in difflib.get_close_matches(
        t.lower().replace(" ", ""), names, n=5, cutoff=0.6)]
    hint = ("; did you mean: " + "; ".join(f'{cat.at[i, "No"]} "{cat.at[i, "Variable"]}"'
                                           for i in close)) if close else ""
    raise ValueError(f'no series called "{t}"{hint}. "python get_data.py --list" lists them all.')


def search(text: str, cat: pd.DataFrame) -> list[int]:
    """Series whose folder, name, key or frequency contain every word of text."""
    words = text.lower().split()
    haystack = cat["Folder"].str.cat(
        cat[["Variable", "ECB series key", "Frequency"]], sep=" ").str.lower()
    return [i for i, h in enumerate(haystack) if all(w in h for w in words)]


def parse_years(text: str, allow_latest: bool) -> str:
    """"all", "latest", or a range written as "2008-2012", "2008-", "-2012" or "2008"."""
    t = text.strip().lower()
    if t in ("", "all"):
        return "all"
    if allow_latest and t == "latest":
        return "latest"
    m = re.fullmatch(r"(\d{4})?\s*(-|to|:)?\s*(\d{4})?", t)
    if not m or not (m[1] or m[3]) or (m[1] and m[3] and int(m[1]) > int(m[3])):
        raise ValueError(f'"{text}" is not a year or a range of years such as 2008-2012')
    if m[1] and not m[2] and not m[3]:
        return m[1]
    return f"{m[1] or ''}-{m[3] or ''}"


def in_years(year: int, spec: str) -> bool:
    if spec == "all":
        return True
    lo, _, hi = spec.partition("-")
    hi = hi if "-" in spec else lo
    return (not lo or year >= int(lo)) and (not hi or year <= int(hi))


def pick_vintages(dates: list[str], spec: str) -> list[str]:
    if spec == "latest":
        return dates[-1:]
    return [d for d in dates if in_years(int(d[:4]), spec)]


def describe_years(spec: str) -> str:
    if spec in ("all", "latest") or "-" not in spec:
        return spec
    lo, hi = spec.split("-")
    return f"{lo} onward" if not hi else f"up to {hi}" if not lo else f"{lo} to {hi}"


# ---- Asking in the terminal --------------------------------------------------------

def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        sys.exit("\nno answer; stopped")


def ask_series(cat: pd.DataFrame) -> list[int]:
    print("\n1. Which series?")
    print("   Type words to search the 278 series (e.g. real gdp, hicp, unemployment rate),")
    print("   then the numbers of the ones you want (e.g. 57, 57 21, 55-59).")
    print('   "list" shows all 278, "all" takes every series, "clear" starts again.')
    print("   Press Enter when you have what you want.\n")
    print_indicators(cat)
    chosen: list[int] = []
    while True:
        answer = ask("\nseries> ")
        low = answer.lower()
        if not answer:
            if chosen:
                return chosen
            print("   nothing chosen yet")
            continue
        if low == "all":
            return list(range(len(cat)))
        if low == "list":
            print_list(cat)
            continue
        if low == "clear":
            chosen = []
            print("   cleared")
            continue
        if re.fullmatch(r"[\d\s,\-]+", answer):
            try:
                picked = [i for part in re.split(r"[\s,]+", answer) if part
                          for i in resolve(part, cat)]
            except ValueError:
                print(f"   numbers go from 1 to {len(cat)}")
                continue
            chosen += [i for i in dict.fromkeys(picked) if i not in chosen]
            print(f"   chosen ({len(chosen)}): "
                  + "; ".join(str(cat.at[i, "Variable"]) for i in chosen[:8])
                  + (f"; and {len(chosen) - 8} more" if len(chosen) > 8 else ""))
            continue
        hits = search(answer, cat)
        if not hits:
            print('   nothing matches; try fewer or shorter words, or "list"')
            continue
        for i in hits[:40]:
            mark = "*" if i in chosen else " "
            print(f"  {mark}{cat.at[i, 'No']:>3}  {cat.at[i, 'Folder']} / "
                  f"{cat.at[i, 'Variable']}  ({str(cat.at[i, 'Frequency']).lower()})")
        if len(hits) > 40:
            print(f"   ... and {len(hits) - 40} more; add a word to narrow it down")
        print("   type the numbers of the ones you want")


def ask_forms() -> list[str]:
    print("\n2. Which form?  (several are fine, e.g. 2 3; Enter for 2 3)")
    print("   1  raw        the ECB's change logs as downloaded, one CSV per series")
    print("   2  tables     one CSV per series: periods in rows, vintage dates in columns")
    print("   3  workbooks  one Excel file per series and vintage date (ALFRED's layout)")
    print("   The raw logs are kept whichever you choose, since the others are built from them.")
    while True:
        answer = ask("\nform> ").lower()
        if not answer:
            return DEFAULT_FORMS
        words = re.split(r"[\s,]+", answer)
        forms = [FORMS[int(w) - 1] if w in ("1", "2", "3") else w for w in words if w]
        if forms and all(f in FORMS for f in forms):
            return [f for f in FORMS if f in forms]
        print("   type 1, 2 or 3 (or raw, tables, workbooks)")


def ask_years(question: str, allow_latest: bool) -> str:
    print(question)
    while True:
        try:
            return parse_years(ask("\nyears> "), allow_latest)
        except ValueError as e:
            print(f"   {e}")


def interactive(cat: pd.DataFrame, dates: list[str]) -> argparse.Namespace:
    print("Vintages of the ECB Real Time Database (RTD)")
    print(f"{len(cat)} series; {len(dates)} vintages known to this repository, "
          f"{dates[0]} to {dates[-1]}.")
    print("The data come from the ECB's server as you ask for them. Ctrl+C stops at any time.")
    rows = ask_series(cat)
    forms = ask_forms()
    vintage_spec = observation_spec = "all"
    if "tables" in forms or "workbooks" in forms:
        vintage_spec = ask_years(
            f"\n3. Which vintages?  {len(dates)} from {dates[0]} to {dates[-1]}.\n"
            "   A year or a range of years (2015, 2008-2012, 2020-), \"latest\" for the\n"
            "   newest only, or Enter for all.", allow_latest=True)
        observation_spec = ask_years(
            "\n4. Which observations?  A year or a range of years of the periods\n"
            "   themselves (2007, 1999-2010, 2000-), or Enter for all.", allow_latest=False)
    keys = cat.loc[rows, "ECB series key"].tolist()
    on_disk = [k for k in keys if (HISTORY_DIR / f"{k}.csv").exists()]
    refresh = False
    if on_disk:
        days = sorted(download.downloaded_on(k) for k in on_disk)
        span = days[0] if days[0] == days[-1] else f"{days[0]} to {days[-1]}"
        print(f"\n{len(on_disk)} of the {len(keys)} logs are already on disk (downloaded "
              f"{span}).")
        refresh = ask("Download them again, to add any vintages published since? [y/N] "
                      ).lower().startswith("y")
    return argparse.Namespace(rows=rows, form=forms, vintages=vintage_spec,
                              observations=observation_spec, refresh=refresh, confirm=True)


# ---- Doing it ------------------------------------------------------------------------

def minutes(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m} min {s:02d} s" if m else f"{s} s"


def megabytes(n: float) -> str:
    return f"{n / 1e6:,.1f} MB" if n < 1e7 else f"{n / 1e6:,.0f} MB"


def command_line(keys: list[str], cat: pd.DataFrame, a: argparse.Namespace) -> str:
    """The command that asks for the same thing without questions."""
    if len(keys) == len(cat):
        parts = ["--all"]
    else:
        parts = ["--series", *(k.removeprefix("RTD.") for k in keys)]
    if a.form != DEFAULT_FORMS:
        parts += ["--form", *a.form]
    if a.vintages != "all":
        parts.append(f"--vintages={a.vintages}")
    if a.observations != "all":
        parts.append(f"--observations={a.observations}")
    if a.refresh:
        parts.append("--refresh")
    return "python get_data.py " + " ".join(parts)


def too_long(rows: list[int], cat: pd.DataFrame) -> list[str]:
    """Workbook paths that Excel could not open, given where the repository is."""
    return [str(p) for i in rows
            if len(str(p := WORKBOOK_DIR / str(cat.at[i, "Folder"]) / str(cat.at[i, "Variable"])
                       / "vintage_2000-01-01.xlsx")) > workbooks.MAX_PATH]


def download_logs(keys: list[str]) -> list[str]:
    """Download the logs of these series, a few at a time; return the keys that failed."""
    if not keys:
        return []
    at_once = (f", {download.PARALLEL_REQUESTS} at a time"
               if len(keys) > download.PARALLEL_REQUESTS else "")
    print(f"\nDownloading {len(keys)} log{'s' * (len(keys) != 1)} from the ECB{at_once}. "
          f"Its server takes 1 to 3 minutes to answer\nfor a series it has not been asked "
          f"for recently, and seconds for one it has.", flush=True)
    failed, start = [], time.time()
    pool = ThreadPoolExecutor(max_workers=download.PARALLEL_REQUESTS)
    try:
        futures = {pool.submit(download.download_log, k): k for k in keys}
        pending, n = set(futures), 0
        while pending:
            done, pending = wait(pending, timeout=30, return_when=FIRST_COMPLETED)
            for f in done:
                n += 1
                ok, line = f.result()
                print(f"  [{n}/{len(keys)}] {'' if ok else 'FAILED '}{line}", flush=True)
                if not ok:
                    failed.append(futures[f])
            if not done:
                print(f"  ... still waiting for the ECB ({minutes(time.time() - start)})",
                      flush=True)
    except KeyboardInterrupt:
        # Requests already sent cannot be called back, and Python would wait for them
        # before exiting. Stop now: a log is only ever saved whole, so nothing is lost.
        print("\nstopped. The logs downloaded so far are kept; run again for the others.",
              flush=True)
        os._exit(130)
    pool.shutdown()
    return failed


def upsert(path: Path, rows: list[dict], columns: list[str], replace: set[str],
           by: list[str]) -> None:
    """Replace some rows of a CSV in data/processed/ and keep the others.

    Old rows whose columns `by`, joined with "|", are in `replace` go; `rows` are
    added; the result is sorted by series.
    """
    old = (pd.read_csv(path, dtype=str).fillna("") if path.exists()
           else pd.DataFrame(columns=columns))
    if len(old):
        old = old[~old[by].astype(str).agg("|".join, axis=1).isin(replace)]
    new = pd.concat([old, pd.DataFrame(rows, columns=columns)], ignore_index=True)
    order = [c for c in ("Folder", "Variable", "Form", "First vintage affected") if c in columns]
    new.sort_values(order, kind="stable").to_csv(path, index=False)


def run(a: argparse.Namespace, cat: pd.DataFrame) -> int:
    start = time.time()
    rows = a.rows
    keys = cat.loc[rows, "ECB series key"].tolist()
    forms = a.form
    build = "tables" in forms or "workbooks" in forms
    everything = len(rows) == len(cat)
    known = vintage_dates()

    if "workbooks" in forms and (long := too_long(rows, cat)):
        sys.exit(f"Excel cannot open a file whose full path is longer than "
                 f"{workbooks.MAX_PATH} characters, and {len(long)} of the workbooks asked "
                 f"for would be, because this folder is {len(str(ROOT))} characters deep:\n  "
                 f"{long[0]}\nMove the repository to a shorter path, such as "
                 f"C:\\data\\ecb-rtd-vintages, and run again.")

    to_get = [k for k in keys if a.refresh or not (HISTORY_DIR / f"{k}.csv").exists()]
    if a.confirm:
        n_vintages = len(pick_vintages(known, a.vintages))
        first = dict(zip(cat["ECB series key"], cat["First vintage"]))
        n_books = sum(len([d for d in pick_vintages(known, a.vintages) if d >= first[k]])
                      for k in keys) if "workbooks" in forms else 0
        size = (n_books * WORKBOOK_BYTES
                + ("tables" in forms) * len(keys) * n_vintages * TABLE_COLUMN_BYTES)
        names = "; ".join(cat.loc[rows[:4], "Variable"]) + (
            f"; and {len(rows) - 4} more" if len(rows) > 4 else "")
        print("\nReady:")
        print(f"  series        {len(keys)}: {names}")
        print(f"  form          {', '.join(forms)}")
        if build:
            print(f"  vintages      {describe_years(a.vintages)} ({n_vintages} vintage dates)")
            print(f"  observations  {describe_years(a.observations)}")
        if to_get:
            lo = max(1, round(len(to_get) / download.PARALLEL_REQUESTS))
            print(f"  downloads     {len(to_get)} log{'s' * (len(to_get) != 1)} from the ECB, "
                  f"about {lo}-{3 * lo} minutes")
        else:
            print("  downloads     none; the logs are on disk")
        if build:
            files = ([f"{len(keys)} table{'s' * (len(keys) != 1)}"] * ("tables" in forms)
                     + [f"up to {n_books:,} workbooks"] * ("workbooks" in forms))
            print(f"  writes        {' and '.join(files)}, about {megabytes(size)}, "
                  f"in data/processed/")
        print(f"\nThe same without the questions:\n  {command_line(keys, cat, a)}")
        if ask("\nGo ahead? [Y/n] ").lower().startswith("n"):
            print("nothing done")
            return 0

    failed = download_logs(to_get)
    missing = [k for k in keys if not (HISTORY_DIR / f"{k}.csv").exists()]
    if missing:
        print(f"\nNo log for {len(missing)} series, so they are left out: "
              f"{', '.join(missing)}\nRun the same command again later to try them again.")
    keys = [k for k in keys if k not in missing]
    # Only a run that built all 278 series may rewrite the files the repository keeps.
    everything = everything and not missing
    if not build:
        print(f"\nThe logs are in {HISTORY_DIR.relative_to(ROOT).as_posix()}/ ({minutes(time.time() - start)}).")
        return 1 if failed else 0
    if not keys:
        return 1

    # Every vintage date: those the repository knows, from all 278 logs, and any newer
    # ones in the logs just read.
    changed = {k: vintages.change_dates(vintages.load_change_log(HISTORY_DIR / f"{k}.csv"))
               for k in keys}
    dates = sorted(set(known).union(*changed.values()))
    newer = [d for d in dates if d > known[-1]]
    taken = pick_vintages(dates, a.vintages)
    if not taken:
        sys.exit(f"no vintage dates in {describe_years(a.vintages)}; they run from "
                 f"{dates[0]} to {dates[-1]}")

    reference = (pd.read_csv(CHECKSUMS, dtype=str).set_index("ECB series key")
                 if CHECKSUMS.exists() else pd.DataFrame())
    by_key = cat.set_index("ECB series key")
    created = f"{datetime.datetime.now():%Y-%m-%d %H:%M}"
    PROCESSED.mkdir(parents=True, exist_ok=True)
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    contents, gaps, first_vintages, fingerprints = [], [], {}, []
    same = differ = 0
    trouble: list[str] = []

    print(f"\nBuilding {len(keys)} series from their logs: {len(taken)} of {len(dates)} "
          f"vintage dates, observations {describe_years(a.observations)}.", flush=True)
    for n, key in enumerate(keys, 1):
        s = {**by_key.loc[key].to_dict(), "ECB series key": key}
        h = vintages.load_change_log(HISTORY_DIR / f"{key}.csv")
        vintages.check_order(h)
        table = vintages.build_table(h, dates)
        with_data = [d for d in dates if table[d].notna().any()]
        s["First vintage"] = with_data[0] if with_data else ""
        first_vintages[key] = s["First vintage"]

        # Compare with the published build, over the vintages it had.
        if key in reference.index:
            up_to = str(reference.at[key, "Vintages up to"])
            n_values, digest = vintages.fingerprint(table, up_to)
            match = digest == reference.at[key, "SHA-256"]
            same, differ = same + match, differ + (not match)
            verdict = "yes" if match else "no"
        else:
            verdict = "not in checksums.csv"
        if everything and not CHECKSUMS.exists():
            n_values, digest = vintages.fingerprint(table, dates[-1])
            fingerprints.append({"ECB series key": key, "Vintages up to": dates[-1],
                                 "Values": n_values, "SHA-256": digest})

        # Which vintages it was behind in, over its whole history.
        status = series_status.series_status(table, changed[key], FREQ_CODE[s["Frequency"]])
        for e in series_status.episodes(status["status"]):
            gaps.append({"Folder": s["Folder"], "Variable": s["Variable"],
                         "ECB series key": key, **e})
        bad = [d for d in taken if status["status"][d] in ("behind", "no observations")]
        if bad:
            trouble.append(f"  {s['Variable']}: behind or empty in {len(bad)} of the "
                           f"{len(taken)} vintages taken, the first {bad[0]} and the last "
                           f"{bad[-1]}")

        # Cut it down to what was asked for. The vintages taken are rebuilt from the log
        # rather than picked out of the table, so that a period the ECB published without
        # a value (a gap inside the series) stays as a blank row, while a period found
        # only in vintages not taken drops out.
        cut = table if taken == dates else vintages.build_table(h, taken)
        cut = cut.loc[[in_years(int(p[:4]), a.observations) for p in cut.index]]
        common = {"Folder": s["Folder"], "Variable": s["Variable"], "ECB series key": key,
                  "Vintages": a.vintages, "Observations": a.observations,
                  "Vintage dates": len(taken), "Newest vintage": dates[-1],
                  "Log downloaded": download.downloaded_on(key),
                  "Same as published build": verdict, "Built": created}
        done = []
        try:
            if "tables" in forms:
                path = TABLE_DIR / f"{key}.csv"
                part = path.with_suffix(".part")
                cut.to_csv(part)
                part.replace(path)
                contents.append({**common, "Form": "table", "Files": 1})
                done.append(f"table of {cut.shape[1]} vintage{'s' * (cut.shape[1] != 1)}")
            if "workbooks" in forms:
                folder = WORKBOOK_DIR / s["Folder"] / s["Variable"]
                books = workbooks.write_series(folder, s, cut, dates[-1], created)
                contents.append({**common, "Form": "workbooks", "Files": books})
                done.append(f"{books} workbook{'s' * (books != 1)}")
        except PermissionError as e:
            sys.exit(f"\ncannot replace {e.filename}: another program has it open (Excel "
                     f"locks the files it opens). Close it and run the same command again.")
        note = "" if verdict == "yes" else (
            "; NOT the same as the published build" if verdict == "no" else "")
        print(f"  [{n}/{len(keys)}] {s['Folder']} / {s['Variable']}: "
              f"{', '.join(done)}{note}", flush=True)

    upsert(CONTENTS, contents, CONTENTS_COLUMNS,
           {f"{c['ECB series key']}|{c['Form']}" for c in contents}, ["ECB series key", "Form"])
    upsert(NOT_UP_TO_DATE, gaps, GAP_COLUMNS, set(keys), ["ECB series key"])

    if everything:
        # The files the repository keeps, rewritten from all 278 logs.
        pd.Series(dates, name="vintage_date").to_csv(VINTAGE_DATES, index=False)
        listed = workbooks.make_variable_list()
        listed["First vintage"] = listed["ECB series key"].map(first_vintages)
        listed.to_csv(VARIABLE_LIST, index=False, encoding="utf-8-sig")
        if not CHECKSUMS.exists():
            pd.DataFrame(fingerprints).to_csv(CHECKSUMS, index=False)
            print(f"\nwrote the fingerprints of this build to {CHECKSUMS.relative_to(ROOT).as_posix()}")

    print(f"\nDone in {minutes(time.time() - start)}. {PROCESSED.relative_to(ROOT).as_posix()}/ holds "
          f"what was asked for; contents.csv lists it.")
    if len(reference):
        up_to = reference["Vintages up to"].max()
        print(f"Same as the published build (vintages up to {up_to}): {same} of "
              f"{same + differ} series." + (
                  " The ECB has changed its record of the others since; their tables show "
                  "its record as it is now." if differ else ""))
    if newer and not everything:
        print(f"\nThe ECB has published {len(newer)} vintage{'s' * (len(newer) != 1)} since "
              f"{known[-1]}, found in the logs of the series asked for. A vintage in which "
              f"none of them changed is not\nlisted, but would equal the one before it. A "
              f"run with every series finds all of them.")
    if trouble:
        print(f"\nNot up to date in the vintages taken ({len(trouble)} of {len(keys)} series; "
              f"{NOT_UP_TO_DATE.relative_to(ROOT).as_posix()} has the details):")
        print("\n".join(trouble[:15]) + (f"\n  ... and {len(trouble) - 15} more"
                                         if len(trouble) > 15 else ""))
    return 1 if failed or missing else 0


def update_series_list() -> None:
    """Download the ECB's list of RTD series and its code lists again."""
    print("downloading the list of series and the code lists from the ECB ...", flush=True)
    meta = download.download_metadata()
    STRUCTURE.write_bytes(download.download_structure())
    meta.to_csv(METADATA, index=False)
    old = variable_list().set_index("ECB series key")["First vintage"]
    try:
        listed = workbooks.make_variable_list()
    except KeyError as e:
        sys.exit(f"the ECB's list now has a concept code with no plain name: {e}. The list "
                 f"of series is saved; add a name for it in code/workbooks.py and run again.")
    listed["First vintage"] = listed["ECB series key"].map(old).fillna("")
    listed.to_csv(VARIABLE_LIST, index=False, encoding="utf-8-sig")
    new = set(listed["ECB series key"]) - set(old.index)
    gone = set(old.index) - set(listed["ECB series key"])
    print(f"{len(listed)} series: {len(new)} new, {len(gone)} no longer listed. "
          f"Run  python get_data.py --all  to build them and fill in their first vintage.")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Get vintages of the ECB Real Time Database. With no options it asks "
                    "in the terminal.",
        epilog="Examples:  python get_data.py --series \"Real GDP\" --vintages 2008-2012   |   "
               "python get_data.py --all --form tables")
    p.add_argument("--series", nargs="+", metavar="S",
                   help="series by number (see --list), name, ECB key or indicator folder")
    p.add_argument("--all", action="store_true", help="every series")
    p.add_argument("--form", nargs="+", choices=FORMS, default=None,
                   help="raw, tables and/or workbooks (default: tables workbooks)")
    p.add_argument("--vintages", default="all",
                   help='years of the vintage dates: 2008-2012, 2015, 2020-, "latest" '
                        '(default: all)')
    p.add_argument("--observations", default="all",
                   help="years of the observations: 1999-2010, 2007, 2000- (default: all)")
    p.add_argument("--refresh", action="store_true",
                   help="download logs again even if they are on disk")
    p.add_argument("--list", action="store_true", help="list the series and stop")
    p.add_argument("--update-series-list", action="store_true",
                   help="for maintainers: download the ECB's list of RTD series again")
    a = p.parse_args()

    cat = catalog()
    if a.list:
        print_list(cat)
        return
    if a.update_series_list:
        update_series_list()
        return
    if a.series is None and not a.all:
        if a.form or a.vintages != "all" or a.observations != "all" or a.refresh:
            p.error("say which series: --series ... or --all")
        try:
            a = interactive(cat, vintage_dates())
        except KeyboardInterrupt:
            sys.exit("\nstopped; nothing done")
    else:
        if a.series and a.all:
            p.error("give --series or --all, not both")
        try:
            rows = (list(range(len(cat))) if a.all
                    else list(dict.fromkeys(i for t in a.series for i in resolve(t, cat))))
            a.vintages = parse_years(a.vintages, allow_latest=True)
            a.observations = parse_years(a.observations, allow_latest=False)
        except ValueError as e:
            p.error(str(e))
        a.rows, a.form, a.confirm = rows, [f for f in FORMS if f in (a.form or DEFAULT_FORMS)], False
    try:
        sys.exit(run(a, cat))
    except KeyboardInterrupt:
        sys.exit("\nstopped. A series being written when you stopped keeps its earlier files; "
                 "run again to finish.")


if __name__ == "__main__":
    main()
