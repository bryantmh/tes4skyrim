"""Echo a background case run's .out file and each case result; flag a stall; exit when the run ends.

    python tools/validate/safe_run.py python tools/live/watch_lane.py <run .out> <instance> [stall seconds]

A stall is no new `test: PASS/FAIL` line in the instance's runtime log (Morrowind or TES4) for that long;
a busy log with no case progress counts. The run ends when the .out file prints its `N passed, ...` totals.
Meant to run under Monitor, with the Monitor timeout at twice the run's expected time.
See: docs/commentary/headless_testing.md#parallel-instances
"""
import glob
import os
import re
import sys
import time

from tools.live import test_instance

RESULT = re.compile(r'test: (PASS|FAIL)')
TOTALS = re.compile(r'\d+ passed, \d+ failed')


def runtime_logs(slot: int) -> list:
    """Every SKSE `*Runtime.log` of test instance `slot`."""
    return glob.glob(os.path.join(test_instance.my_games(slot), 'SKSE', '*Runtime.log'))


def new_text(path: str, seen: int) -> tuple:
    """`(text appended since seen, new size)`; a shrunk file is read from the start."""
    size = os.path.getsize(path) if os.path.exists(path) else 0
    seen = 0 if size < seen else seen
    if size == seen:
        return '', size
    with open(path, encoding='utf-8', errors='replace') as f:
        f.seek(seen)
        return f.read(), size


def main() -> None:
    """Poll loop; one stdout line per event."""
    out, slot = sys.argv[1], int(sys.argv[2])
    stall = float(sys.argv[3]) if len(sys.argv) > 3 else 180.0
    seen = {log: os.path.getsize(log) for log in runtime_logs(slot)}
    seen[out] = 0
    progress, warned = time.time(), False
    while True:
        text, seen[out] = new_text(out, seen[out])
        for line in text.splitlines():
            print(line[:300], flush=True)
            if TOTALS.search(line):
                return
        for log in runtime_logs(slot):
            text, seen[log] = new_text(log, seen.get(log, 0))
            for line in filter(RESULT.search, text.splitlines()):
                print(line[:200], flush=True)
                progress, warned = time.time(), False
        if time.time() - progress > stall and not warned:
            print('STALLED: no case result for %d s -- read the log and act' % (time.time() - progress), flush=True)
            warned = True
        time.sleep(5)


if __name__ == '__main__':
    main()
