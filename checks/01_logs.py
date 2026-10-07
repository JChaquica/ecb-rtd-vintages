"""What the ECB's change logs hold, and a test that their time stamps decide the
order of every period's rows.

Why this check matters: code/vintages.py takes, for each period, the latest row on
or before a vintage date, and decides "latest" by the full time stamp. That is only
sound if no two rows of one period share a time stamp but differ. check_order()
stops the build if any do; this check runs it over all 278 logs and reports what it
found: how often a period has both a removal and a new value on the same day, on
which days, and which of the two is stamped later.

It also lists, for each series, how many periods it has, its first vintage and how
many values its newest vintage holds, and confirms that the dates in the logs are
the vintage dates in data/processed/vintage_dates.csv.

Output: output/logs/01_logs.txt

    python checks/01_logs.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.path.abspath(__file__)).parents[1] / "code"))

from common import HISTORY_DIR, LOG_DIR, ROOT, variable_list, vintage_dates  # noqa: E402
from vintages import build_table, change_dates, check_order, load_change_log  # noqa: E402

LOG_PATH = LOG_DIR / "01_logs.txt"


def main() -> None:
    keys = sorted(variable_list()["ECB series key"])
    missing = [k for k in keys if not (HISTORY_DIR / f"{k}.csv").exists()]
    if missing:
        sys.exit(f"{len(missing)} of the {len(keys)} logs are not in "
                 f"{HISTORY_DIR.relative_to(ROOT).as_posix()}/. Run  python get_data.py --all  first.")
    dates = vintage_dates()

    new_later = removal_later = copies = 0
    days: set[str] = set()
    found: set[str] = set()
    lines = []
    for key in keys:
        h = load_change_log(HISTORY_DIR / f"{key}.csv")
        n, r, d, c = check_order(h)
        new_later, removal_later, days, copies = (new_later + n, removal_later + r,
                                                   days | d, copies + c)
        found |= change_dates(h)
        table = build_table(h, dates)
        first = table.notna().any().idxmax()  # first vintage with any data
        lines.append(f"{key}\t{len(table)} periods\tfirst vintage {first}\t"
                     f"latest vintage has {table[dates[-1]].notna().sum()} values")

    order_note = (f"same-day removal and new value for one period: {new_later + removal_later:,} "
                  f"cases, on {', '.join(sorted(days)) or 'no day'}; the new value is stamped "
                  f"later in {new_later:,}, the removal in {removal_later:,}. "
                  f"Rows that repeat another row of their period at the same time stamp: "
                  f"{copies:,}; rows that share a time stamp but differ: none.")
    same_dates = sorted(found) == dates
    date_note = (f"vintage dates in the logs: {len(found)}, {min(found)} to {max(found)}; "
                 + ("the same as in data/processed/vintage_dates.csv" if same_dates else
                    f"NOT the same as the {len(dates)} in data/processed/vintage_dates.csv"))
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text("\n".join([order_note, date_note, "", *lines]) + "\n", encoding="utf-8")
    print(order_note)
    print(date_note)
    print(f"log: {LOG_PATH.relative_to(ROOT).as_posix()}")
    sys.exit(0 if same_dates else 1)


if __name__ == "__main__":
    main()
