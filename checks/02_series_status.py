"""Find which RTD series the ECB is still updating, so that a user can tell which of
the 278 series are usable and which are frozen.

Every vintage of every series is rebuilt correctly (the other checks show it), but
that says nothing about whether the numbers are still being maintained. A series
can appear in all 261 vintages and still be stale. code/series_status.py says how a
series is judged; this script applies it to all 278 series and writes the result.

Output: output/series_status.csv            one line per series: usable or not
        output/series_last_update.csv       last change and publication lag of each series
        output/series_gaps.csv              each stretch of vintages a series was behind
        output/series_status_by_vintage.csv the status of every series in every vintage
        output/logs/02_series_status.txt

    python checks/02_series_status.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(os.path.abspath(__file__)).parents[1] / "code"))

from common import (FREQ_CODE, HISTORY_DIR, LOG_DIR, OUTPUT, ROOT, TABLE_DIR,  # noqa: E402
                    require_full_build, variable_list)
from series_status import BASELINE, episodes, series_status  # noqa: E402
from vintages import change_dates, load_change_log  # noqa: E402

LOG_PATH = LOG_DIR / "02_series_status.txt"


def main() -> None:
    require_full_build()
    variables = variable_list()

    status_rows, update_rows, gap_rows, matrix_rows = [], [], [], []
    latest = ""

    for s in variables.to_dict("records"):
        key, freq = s["ECB series key"], FREQ_CODE[s["Frequency"]]
        table = pd.read_csv(TABLE_DIR / f"{key}.csv", index_col=0, dtype={"TIME_PERIOD": str})
        table.index = table.index.astype(str)   # annual periods would become numbers
        vintages = list(table.columns)
        latest = vintages[-1]

        found = series_status(table, change_dates(load_change_log(HISTORY_DIR / f"{key}.csv")),
                              freq)
        status, last_period, gap = found["status"], found["last_period"], found["gap"]
        changed, run, since, overall = (found["changes"], found["run"], found["since"],
                                        found["overall"])
        behind = status[latest] == "behind"

        where = {"Folder": s["Folder"], "Variable": s["Variable"], "Frequency": s["Frequency"]}
        status_rows.append({**where, "Status": "Stopped" if behind else "Current",
                            "Last vintage with new data": changed[-1] if changed else "",
                            "Vintages since then": since,
                            "Normal run without a change (vintages)": run,
                            f"Last period in the {latest} vintage": last_period[latest],
                            "ECB series key": key})
        update_rows.append({**where, "Behind": "yes" if behind else "no",
                            "Last vintage with any change": changed[-1] if changed else "",
                            f"Vintages since then (to {latest})": since,
                            "Normal run without a change (vintages)": run,
                            f"Last period in the {latest} vintage": last_period[latest],
                            f"Usual gap between vintage date and last period, "
                            f"{BASELINE[0]}-{BASELINE[1]} (periods)": overall,
                            f"Gap in the {latest} vintage (periods)": gap[latest],
                            "Periods behind usual": (None if gap[latest] is None
                                                     else gap[latest] - overall),
                            "ECB series key": key})
        for e in episodes(status):
            gap_rows.append({**where, **e,
                             f"Still affected in the {latest} vintage":
                                 "yes" if e["Last vintage affected"] == latest else "no",
                             "Last period in those vintages":
                                 last_period[e["Last vintage affected"]],
                             "ECB series key": key})
        matrix_rows.append({"Folder": s["Folder"], "Variable": s["Variable"],
                            "ECB series key": key, **status})

    for rows, name in ((status_rows, "series_status.csv"),
                       (update_rows, "series_last_update.csv"),
                       (gap_rows, "series_gaps.csv"),
                       (matrix_rows, "series_status_by_vintage.csv")):
        pd.DataFrame(rows).to_csv(OUTPUT / name, index=False, encoding="utf-8-sig")

    status = pd.DataFrame(status_rows)
    stopped = status[status["Status"] == "Stopped"]
    matrix = pd.DataFrame(matrix_rows).set_index("ECB series key").iloc[:, 2:]
    affected = matrix.isin(["behind", "no observations"]).sum()
    repeated = {r["ECB series key"] for r in gap_rows
                if r["Vintages affected"] >= 2 and r["First vintage affected"] >= BASELINE[0]}

    summary = [f"latest vintage: {latest}", f"series: {len(status)}", "",
               f"{len(status) - len(stopped):>4}  usable (up to date in the latest vintage)",
               f"{len(stopped):>4}  not usable (behind in the latest vintage)", "",
               "not usable, by the vintage at which the ECB last changed them:"]
    summary += [f"{n:>4}  {d}" for d, n in stopped["Last vintage with new data"]
                .value_counts().sort_index().items()]
    summary += ["", "not usable, by indicator:"]
    summary += [f"{n:>4}  {f}" for f, n in stopped["Folder"].value_counts().items()]
    summary += ["", f"{len(repeated):>4}  series behind or without observations for two or "
                    f"more vintages in a row since {BASELINE[0]}",
                "", "series affected per vintage:"]
    for years in ("2015", "2020", "2023", "2026"):
        same = affected[[v for v in affected.index if v[:4] == years]]
        summary.append(f"      {years}: {same.min()} to {same.max()}")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text("\n".join(summary) + "\n", encoding="utf-8")
    print("\n".join(summary))
    print(f"\nwrote 4 files to {OUTPUT.relative_to(ROOT).as_posix()}; log: {LOG_PATH.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
