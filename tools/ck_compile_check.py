"""Compile converted Papyrus scripts with Skyrim's BUNDLED CK PapyrusCompiler.

The CK compiler (Papyrus Compiler/PapyrusCompiler.exe) is stricter than the MIT
compiler bundled in this app, so it surfaces type errors the app compiler misses
— in particular property-type mismatches that would fail VMAD binding at runtime.

Usage:
  # Check specific scripts by name (without .psc):
  python tools/ck_compile_check.py TES4_QF_FGC01Rats TES4_MG16MageScript

  # Check every script named in a scan file (one ScriptName per line):
  python tools/ck_compile_check.py --list suspects.txt

  # Check ALL converted scripts (slow):
  python tools/ck_compile_check.py --all

  # Check another plugin's scripts (default plugin is Oblivion.esm):
  python tools/ck_compile_check.py --plugin Morrowind_ob.esm --failed
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from subprocess_flags import windows_cmd  # noqa: E402

SSE = r'C:\Program Files (x86)\Steam\steamapps\common\Skyrim Special Edition'
CK = os.path.join(SSE, 'Papyrus Compiler', 'PapyrusCompiler.exe')
# The native type headers (MiscObject.psc, GlobalVariable.psc, Package.psc,
# Topic.psc, ...) live in Data/Source/Scripts on this install — list it FIRST.
# Data/Scripts/Source is a secondary/partial mirror on some installs.
VANILLA_SRC = os.path.join(SSE, 'Data', 'Source', 'Scripts')
VANILLA_SRC2 = os.path.join(SSE, 'Data', 'Scripts', 'Source')
# Papyrus flags file — required by the compiler. Search the known locations.
_FLG_CANDIDATES = [
    os.path.join(SSE, 'Data', 'Source', 'Scripts', 'TESV_Papyrus_Flags.flg'),
    os.path.join(SSE, 'Data', 'Scripts', 'Source', 'TESV_Papyrus_Flags.flg'),
    os.path.join(SSE, 'Papyrus Compiler', 'TESV_Papyrus_Flags.flg'),
]
FLAGS = next((p for p in _FLG_CANDIDATES if os.path.isfile(p)), _FLG_CANDIDATES[0])
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_SRC = os.path.join(ROOT, 'script_convert', 'static_scripts')


def plugin_dirs(plugin: str) -> tuple:
    """(source dir, compiled .pex dir) for a plugin's converted scripts."""
    base = os.path.join(ROOT, 'output', plugin, 'scripts')
    return os.path.join(base, 'source'), base


# Default plugin; `main()` rebinds these when --plugin is given.
OUR_SRC, OUR_PEX = plugin_dirs('oblivion.esm')


def import_dirs():
    dirs = [OUR_SRC, STATIC_SRC]
    for d in (VANILLA_SRC, VANILLA_SRC2):
        if os.path.isdir(d):
            dirs.append(d)
    return [d for d in dirs if os.path.isdir(d)]


def compile_one(script_name: str, out_dir: str) -> tuple:
    """Compile a single script; return (name, ok, output)."""
    src = os.path.join(OUR_SRC, script_name + '.psc')
    if not os.path.isfile(src):
        return (script_name, False, f'source not found: {src}')
    imports = ';'.join(import_dirs())
    cmd = [CK, script_name + '.psc',
           f'-import={imports}',
           f'-output={out_dir}',
           f'-f={os.path.basename(FLAGS)}']
    try:
        r = subprocess.run(windows_cmd(cmd), cwd=OUR_SRC, capture_output=True,
                           text=True, timeout=120)
        out = ((r.stdout or '') + (r.stderr or '')).replace('\x00', '')
        # CK compiler prints "Compilation succeeded." on success and
        # "compilation failed" / "N error(s)" with N>0 on failure. Trust those
        # markers rather than a bare substring match on "error" (the success
        # banner literally says "0 error(s)").
        ok = ('Compilation succeeded' in out
              and 'compilation failed' not in out.lower())
        return (script_name, ok, out.strip())
    except subprocess.TimeoutExpired:
        return (script_name, False, 'TIMEOUT')
    except Exception as e:
        return (script_name, False, f'EXC {e}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('scripts', nargs='*', help='Script names (no .psc)')
    ap.add_argument('--list', help='File with one ScriptName per line')
    ap.add_argument('--all', action='store_true', help='Compile every converted script')
    ap.add_argument('--plugin', default='oblivion.esm',
                    help='Plugin whose output/<plugin>/scripts to check')
    ap.add_argument('--failed', action='store_true',
                    help='Only scripts with a .psc but no .pex (i.e. the ones '
                         'the last build failed to compile)')
    ap.add_argument('--max', type=int, help='Stop after N scripts')
    ap.add_argument('--summary', action='store_true',
                    help='Print error classes with counts instead of every error')
    ap.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 4) - 1),
                    help='Parallel compiler processes')
    args = ap.parse_args()

    global OUR_SRC, OUR_PEX
    OUR_SRC, OUR_PEX = plugin_dirs(args.plugin)
    if not os.path.isdir(OUR_SRC):
        sys.exit(f'No converted scripts for plugin {args.plugin!r}: {OUR_SRC}')

    if not os.path.isfile(CK):
        sys.exit(f'CK compiler not found: {CK}')

    names = list(args.scripts)
    if args.list:
        with open(args.list) as f:
            names += [ln.strip() for ln in f if ln.strip() and not ln.startswith('#')]
    if args.all or args.failed:
        names = [os.path.splitext(f)[0] for f in os.listdir(OUR_SRC)
                 if f.endswith('.psc')]
    if args.failed:
        built = {os.path.splitext(f)[0].lower() for f in os.listdir(OUR_PEX)
                 if f.endswith('.pex')}
        names = [n for n in names if n.lower() not in built]
    names = sorted(set(names))
    if args.max:
        names = names[:args.max]
    if not names:
        sys.exit('No scripts specified. Use names, --list FILE, --all or --failed.')

    print(f'CK compiler: {CK}')
    print(f'Import dirs: {import_dirs()}')
    print(f'Checking {len(names)} scripts...\n')

    out_dir = tempfile.mkdtemp(prefix='ckcompile_')
    failures = []
    # PapyrusCompiler.exe is a subprocess, so threads (not processes) are the
    # right pool here — the work is entirely spawn-and-wait.
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(compile_one, n, out_dir): n for n in names}
        for i, fut in enumerate(as_completed(futs), 1):
            nm, ok, out = fut.result()
            if not ok:
                failures.append((nm, out))
                if not args.summary:
                    print(f'FAIL {nm}', flush=True)
                    # CK error lines look like: <path>(line,col): <message>
                    for line in out.splitlines():
                        s = line.strip()
                        if re.search(r'\(\d+,\d+\):', s) or 'compilation failed' in s.lower():
                            print(f'     {s}', flush=True)
            if args.summary and i % 25 == 0:
                print(f'  ...{i}/{len(names)} ({len(failures)} failed)', flush=True)

    print(f'\n{len(names) - len(failures)}/{len(names)} compiled clean; '
          f'{len(failures)} failed.')

    if args.summary and failures:
        # Bucket by the compiler message with the specific identifier stripped,
        # so "no viable alternative at input 'sin'" and "...'cos'" collapse into
        # one class worth one fix.
        buckets = {}
        for nm, out in failures:
            for line in out.splitlines():
                m = re.search(r'\(\d+,\d+\):\s*(.*)$', line.strip())
                if not m:
                    continue
                key = re.sub(r"'[^']*'", "'X'", m.group(1)).strip()
                buckets.setdefault(key, []).append((nm, m.group(1).strip()))
        print(f'\n{len(buckets)} distinct error classes:\n')
        for key, hits in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
            scripts = sorted({h[0] for h in hits})
            print(f'{len(hits):5d} errs / {len(scripts):4d} scripts | {key}')
            print(f'        e.g. {scripts[0]}: {hits[0][1][:120]}')
    elif failures:
        print('\nFailed scripts:', ', '.join(f[0] for f in failures))


if __name__ == '__main__':
    main()
