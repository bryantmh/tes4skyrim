"""Capture a native crash from the running game, with a full minidump.

Attaches cdb to a live process, waits for the fault, then writes the exception
record, registers, stacks and a full-memory dump. `--analyze` re-opens a dump
offline and disassembles the engine frames the stack names, so the faulting
call site can be read without the game running.

The mechanism matters, and is the whole reason this file exists. cdb's `-c`
and `-c2` exception-handler commands DO NOT RUN for a `__fastfail` abort
(`int 29h`, c0000409): that exception is non-continuable, so it is delivered
straight to second chance, cdb stops at its own prompt, and with stdin closed
it hangs there capturing nothing. Driving cdb through STDIN instead works
because the second-chance break has already frozen the faulting thread; the
dump and stack commands are simply read from the pipe afterwards.

`.detach` is always the exit. cdb attaches invasively, so killing the debugger
kills the game with it.

Run `--selftest` before asking anyone to reproduce a crash: it proves the
capture fires against a synthetic fast-fail without costing a game launch.

See: docs/reference/python_tools.md#crash-capture
"""

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
from core.subprocess_flags import POPEN_FLAGS

#: Windows SDK debugger; the x64 build, matching the game.
_CDB = r'C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\cdb.exe'

#: Steam install: the copy the user plays, so the copy whose crashes matter.
_GAME_DIR = (r'C:\Program Files (x86)\Steam\steamapps'
             r'\common\Skyrim Special Edition')

#: SKSE must create the game; cdb spawning it trips an Engine Fixes popup.
_LOADER = 'skse64_loader.exe'


def launch_game(out_dir: str, tag: str = 'crash') -> int:
    """Start Skyrim under SKSE and attach to it; a process exit code.

    Reproduces a double-click (cwd is the game directory, loader run from
    there), then finds the pid from Python in a spin-fast retry. The launch
    shape is load-bearing -- see the doc for what breaks.

    See: docs/reference/python_tools.md#crash-capture
    """
    proc = subprocess.Popen([os.path.join(_GAME_DIR, _LOADER)],
                            cwd=_GAME_DIR, **POPEN_FLAGS)
    print('loader %s started in %s' % (proc.pid, _GAME_DIR), flush=True)
    for _ in range(200000):
        pid = find_pid('SkyrimSE.exe')
        if pid:
            return attach(pid, out_dir, tag)
    print('SkyrimSE.exe never appeared', flush=True)
    return 1

#: Default capture directory, kept out of the source tree.
_OUT = os.path.join('temp', 'crash')

#: Commands fed to cdb over stdin: run, then describe the fault it stops on.
_CAPTURE = """sxn eh
sxe c0000409
sxe -c ".echo MARKER_FASTFAIL; ~*kb 40" sbo
.echo MARKER_ARMED
g
.echo MARKER_CRASH
.exr -1
.lastevent
.echo MARKER_REGS
r
.echo MARKER_STACK
kb 100
.echo MARKER_CODE
u @rip-40 L30
.echo MARKER_MODULES
lm
.echo MARKER_THREADS
~*kb 15
.dump /ma "{dump}"
.echo MARKER_DUMPED
.detach
qd
"""

#: --watch: on a plain process exit, log who exited and let go (no dump).
_ON_EXIT = 'sxe -c ".echo MARKER_EXIT; kb 60; ~*kb 20; .detach; qd" epr\n'

#: Seconds between --watch scans for a newly started game.
_WATCH_POLL = 2.0

#: Raises FAST_FAIL, so --selftest exercises the real non-continuable path.
_VICTIM = '''import ctypes


def main():
    ctypes.windll.kernel32.RaiseFailFastException(None, None, 0)


if __name__ == '__main__':
    main()
'''

#: Sections of a capture log, in the order the debugger writes them.
_SECTIONS = ('MARKER_CRASH', 'MARKER_REGS', 'MARKER_STACK', 'MARKER_CODE',
             'MARKER_MODULES', 'MARKER_THREADS', 'MARKER_DUMPED')


def find_pid(image: str):
    """The pid of the first process matching `image`, or None."""
    out = subprocess.run(['tasklist', '/FO', 'CSV'],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.lower().startswith('"%s"' % image.lower()):
            return line.split(',')[1].strip('"')
    return None


def _prepare(out_dir: str, tag: str):
    """Make the capture directory and return its (log, dump) paths."""
    os.makedirs(out_dir, exist_ok=True)
    log = os.path.join(out_dir, '%s.txt' % tag)
    dump = os.path.join(out_dir, '%s.dmp' % tag)
    for stale in (log, dump):
        if os.path.exists(stale):
            os.remove(stale)
    return log, dump


def attach(pid: str, out_dir: str, tag: str = 'crash', first: str = '') -> int:
    """Attach to `pid` and wait for it to fault, running `first` before the
    capture commands; returns a process exit code."""
    log, dump = _prepare(out_dir, tag)
    with open(log, 'w', encoding='utf-8') as handle:
        proc = subprocess.Popen([_CDB, '-p', pid], stdin=subprocess.PIPE,
                                stdout=handle, stderr=subprocess.STDOUT,
                                text=True)
        proc.stdin.write(first + _CAPTURE.format(dump=dump))
        proc.stdin.flush()
    print('cdb %s attached to pid %s' % (proc.pid, pid), flush=True)
    print('log  %s' % log, flush=True)
    print('dump %s' % dump, flush=True)
    print('waiting for the fault; do not kill cdb (it would kill the target)',
          flush=True)
    return 0


#: --snapshot: every thread's stack and a full dump of a process that is already hung, then let go.
_SNAPSHOT = """.echo MARKER_THREADS
~*kb 40
.dump /ma "{dump}"
.echo MARKER_DUMPED
qd
"""


def snapshot(pid, out_dir: str = _OUT, tag: str = 'hang') -> str:
    """One non-invasive stack walk and full dump of a HUNG `pid` (never a running game); the log path."""
    log, dump = _prepare(out_dir, tag)
    with open(log, 'w', encoding='utf-8') as handle:
        subprocess.run([_CDB, '-pv', '-p', str(pid)], input=_SNAPSHOT.format(dump=dump), stdout=handle,
                       stderr=subprocess.STDOUT, text=True, timeout=900, **POPEN_FLAGS)
    print('hang snapshot %s / %s' % (log, dump), flush=True)
    return log


def pids_under(image: str, folder: str) -> list:
    """Pids of running `image` processes whose executable lies under `folder`."""
    script = ("Get-Process -Name %s -ErrorAction SilentlyContinue | "
              "ForEach-Object { '{0}|{1}' -f $_.Id, $_.Path }"
              % os.path.splitext(image)[0])
    out = subprocess.run(['powershell', '-NoProfile', '-Command', script],
                         capture_output=True, text=True, **POPEN_FLAGS).stdout
    root = os.path.normcase(os.path.abspath(folder)) + os.sep
    rows = (line.partition('|') for line in out.splitlines())
    return [pid for pid, _bar, path in rows
            if os.path.normcase(path).startswith(root)]


def watch(image: str, folder: str, out_dir: str) -> int:
    """Attach to every `image` started from `folder` until interrupted; a
    plain exit logs the exiting thread's stack (`pid<N>.txt`) and detaches."""
    seen = set()
    while True:
        for pid in pids_under(image, folder):
            if pid not in seen:
                seen.add(pid)
                attach(pid, out_dir, 'pid%s' % pid, _ON_EXIT)
        time.sleep(_WATCH_POLL)


def armed(log: str) -> bool:
    """Whether the capture got far enough to resume the target."""
    if not os.path.exists(log):
        return False
    with open(log, 'r', encoding='utf-8', errors='replace') as handle:
        return 'MARKER_ARMED' in handle.read()


def report(log: str, section: str = 'MARKER_CRASH', limit: int = 80) -> None:
    """Print one marked section of a capture log."""
    with open(log, 'r', encoding='utf-8', errors='replace') as handle:
        text = handle.read()
    start = text.find(section)
    if start < 0:
        print('%s not present; markers seen: %s'
              % (section, [m for m in _SECTIONS if m in text]), flush=True)
        return
    body = text[start:]
    rest = (_SECTIONS[_SECTIONS.index(section) + 1:]
            if section in _SECTIONS else ())
    for nxt in rest:
        end = body.find(nxt)
        if end > 0:
            body = body[:end]
            break
    for line in body.splitlines()[:limit]:
        print(line.rstrip(), flush=True)


def analyze(dump: str, out_dir: str, rvas=()) -> int:
    """Disassemble a saved dump offline; returns a process exit code."""
    if not os.path.exists(dump):
        print('no dump at %s' % dump, flush=True)
        return 1
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, 'analyze.txt')
    parts = ['.echo MARKER_CRASH', '.exr -1', '.lastevent', 'r', 'kb 100']
    for rva in rvas:
        parts += ['.echo ===RVA_%s===' % rva, 'ub %s L14' % rva,
                  '.echo ---AT---', 'u %s L8' % rva]
    parts += ['.echo MARKER_DUMPED', 'q']
    with open(out, 'w', encoding='utf-8') as handle:
        subprocess.run([_CDB, '-z', dump], input='\n'.join(parts) + '\n',
                       stdout=handle, stderr=subprocess.STDOUT, text=True,
                       timeout=900)
    print('wrote %s' % out, flush=True)
    report(out, 'MARKER_CRASH')
    return 0


def selftest(out_dir: str) -> int:
    """Prove the capture fires on a synthetic fast-fail; 0 when it passes."""
    os.makedirs(out_dir, exist_ok=True)
    victim = os.path.join(out_dir, 'victim.py')
    with open(victim, 'w', encoding='utf-8') as handle:
        handle.write(_VICTIM)
    log, dump = _prepare(out_dir, 'selftest')
    with open(log, 'w', encoding='utf-8') as handle:
        subprocess.run([_CDB, sys.executable, victim],
                       input=_CAPTURE.format(dump=dump), stdout=handle,
                       stderr=subprocess.STDOUT, text=True, timeout=300)
    size = os.path.getsize(dump) if os.path.exists(dump) else 0
    with open(log, 'r', encoding='utf-8', errors='replace') as handle:
        seen = 'MARKER_CRASH' in handle.read()
    print('selftest: crash_seen=%s dump_bytes=%d -> %s'
          % (seen, size, 'PASS' if seen and size else 'FAIL'), flush=True)
    return 0 if seen and size else 1


def _parse_args():
    """The parsed command line."""
    ap = argparse.ArgumentParser(description='Capture a native crash.')
    ap.add_argument('--process', default='SkyrimSE.exe',
                    help='image name to attach to')
    ap.add_argument('--pid', help='attach to this pid instead of searching')
    ap.add_argument('--out', default=_OUT, help='capture directory')
    ap.add_argument('--tag', default='crash', help='basename for log and dump')
    ap.add_argument('--analyze', metavar='DUMP',
                    help='post-mortem a saved dump instead of attaching')
    ap.add_argument('--rva', action='append', default=[],
                    help='extra address to disassemble with --analyze')
    ap.add_argument('--report', metavar='LOG',
                    help='print a captured log without attaching')
    ap.add_argument('--section', default='MARKER_CRASH',
                    help='which marked section --report prints')
    ap.add_argument('--selftest', action='store_true',
                    help='prove the capture works, no game needed')
    ap.add_argument('--game', action='store_true',
                    help='launch Skyrim under SKSE, then attach to it')
    ap.add_argument('--watch', metavar='DIR',
                    help='attach to every game started from DIR (test instances)')
    return ap.parse_args()


def main() -> int:
    """Dispatch the requested mode; returns a process exit code."""
    args = _parse_args()
    if args.selftest:
        return selftest(args.out)
    if args.analyze:
        return analyze(args.analyze, args.out, args.rva)
    if args.report:
        report(args.report, args.section)
        return 0
    if args.game:
        return launch_game(args.out, args.tag)
    if args.watch:
        return watch(args.process, args.watch, args.out)
    pid = args.pid or find_pid(args.process)
    if not pid:
        print('%s is not running' % args.process, flush=True)
        return 1
    return attach(pid, args.out, args.tag)


if __name__ == '__main__':
    sys.exit(main())
