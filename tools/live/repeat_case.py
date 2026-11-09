"""Runs one case (or a case range) several times in a row from its checkpoint; a fix counts only when every run passes.

    python -m tools.live.repeat_case --cases <folder> --case <name> [--times 3] [--instance 2] [--out DIR]
    python -m tools.live.repeat_case --cases <folder> --from <a> --to <b> --tool questtest --new-game 3

Each run's runner output and runtime log are kept as `<out>/<label>_<n>.out` / `.log`. Stops at the
first failure unless `--keep-going`; prints `k/N passed` and exits 0 only when all N passed.

See: docs/commentary/headless_testing.md#repeat-a-case
"""
import argparse
import os
import shutil
import subprocess
import sys

from core.subprocess_flags import POPEN_FLAGS
from tools.live import test_instance

#: Runtime log each case tool writes under the profile's SKSE folder.
RUNTIME_LOG = {'mwtest': 'MorrowindRuntime.log', 'questtest': 'TESRuntime.log'}


def parse_args():
    """The command line."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--cases', required=True, help='case folder or file')
    p.add_argument('--case', help='run just this case (same as --from X --to X)')
    p.add_argument('--from', dest='from_case')
    p.add_argument('--to', dest='to_case')
    p.add_argument('--times', type=int, default=3)
    p.add_argument('--instance', type=int, default=1)
    p.add_argument('--tool', choices=sorted(RUNTIME_LOG), default='mwtest')
    p.add_argument('--timeout', type=float, default=3600)
    p.add_argument('--out', default=os.path.join(test_instance.REPO, 'temp', 'repeat_case'))
    p.add_argument('--label', help='file prefix; the case name by default')
    p.add_argument('--keep-going', action='store_true', help='run all N even after a failure')
    p.add_argument('extra', nargs='*', help='further test_instance case options, after --')
    return p.parse_args()


def command(args) -> list:
    """The test_instance command line for one run; with `--load` among the extra options the run starts at the
    folder's first case (a folder's first case has no checkpoint before it, so `--from` would be refused)."""
    first = None if '--load' in args.extra else args.case or args.from_case
    last = args.case or args.to_case
    cmd = [sys.executable, '-m', 'tools.live.test_instance', '--instance', str(args.instance), args.tool,
           '--cases', args.cases, '--fast', '--stop-on-fail', '--timeout', str(args.timeout)]
    cmd += ['--from', first] if first else []
    cmd += ['--to', last] if last else []
    return cmd + args.extra


def one_run(args, n: int, label: str) -> bool:
    """Run `n`: the runner's output and the runtime log kept under `args.out`; whether it passed."""
    out = os.path.join(args.out, '%s_%d' % (label, n))
    with open(out + '.out', 'w', encoding='utf-8') as f:
        code = subprocess.run(command(args), cwd=test_instance.REPO, stdout=f, stderr=subprocess.STDOUT,
                              **POPEN_FLAGS).returncode
    log = os.path.join(test_instance.MY_GAMES, 'SKSE', RUNTIME_LOG[args.tool])
    if os.path.isfile(log):
        shutil.copyfile(log, out + '.log')
    with open(out + '.out', encoding='utf-8', errors='replace') as f:
        verdict = [line.strip()[:300] for line in f if line.startswith(('ok ', 'FAIL', 'GAME EXITED', 'runner'))]
    print('run %d: %s  (%s.out)' % (n, 'PASS' if code == 0 else 'FAIL', out), flush=True)
    for line in verdict:
        print('    ' + line, flush=True)
    return code == 0


def main() -> int:
    """Run the case `--times` times; 0 when every run passed."""
    args = parse_args()
    test_instance.use_instance(args.instance)
    os.makedirs(args.out, exist_ok=True)
    label = args.label or args.case or args.from_case or 'cases'
    passed = 0
    for n in range(1, args.times + 1):
        if one_run(args, n, label):
            passed += 1
        elif not args.keep_going:
            break
    print('%s: %d/%d passed%s' % (label, passed, args.times,
                                  '' if passed == args.times else ' -- NOT verified'), flush=True)
    return 0 if passed == args.times else 1


if __name__ == '__main__':
    sys.exit(main())
