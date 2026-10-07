"""Run every check in order and say which passed.

    python checks/run_all.py              all nine
    python checks/run_all.py --offline    all but 06_current_data.py, the one that
                                          needs the ECB's server

Each check writes its own log to output/logs/ and stops with exit code 1 if it
finds a problem. 02, 03, 05 and 06 need the full build (python get_data.py --all)
and 01 every log; 07 and 09 need only the series they compare, and 04 and 08
nothing beyond the repository. 03 and 04 download the ECB's old vintage files
(36 MB) the first time.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(os.path.abspath(__file__)).parent
NEEDS_SERVER = "06_current_data.py"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--offline", action="store_true", help=f"skip {NEEDS_SERVER}")
    a = p.parse_args()

    results = []
    for script in sorted(HERE.glob("[0-9][0-9]_*.py")):
        if a.offline and script.name == NEEDS_SERVER:
            results.append((script.name, "skipped", 0.0))
            continue
        print(f"\n===== {script.name} =====", flush=True)
        start = time.time()
        code = subprocess.run([sys.executable, str(script)]).returncode
        results.append((script.name, "ok" if code == 0 else f"FAILED (exit {code})",
                        time.time() - start))

    print("\n===== summary =====")
    for name, outcome, seconds in results:
        print(f"  {name:<28} {outcome:<18} {seconds:6.0f} s")
    sys.exit(0 if all(o in ("ok", "skipped") for _, o, _ in results) else 1)


if __name__ == "__main__":
    main()
