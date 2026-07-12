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
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile

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
OUR_SRC = os.path.join(ROOT, 'output', 'oblivion.esm', 'scripts', 'source')
STATIC_SRC = os.path.join(ROOT, 'script_convert', 'static_scripts')


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
        r = subprocess.run(cmd, cwd=OUR_SRC, capture_output=True, text=True,
                           timeout=120)
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
    args = ap.parse_args()

    if not os.path.isfile(CK):
        sys.exit(f'CK compiler not found: {CK}')

    names = list(args.scripts)
    if args.list:
        with open(args.list) as f:
            names += [ln.strip() for ln in f if ln.strip() and not ln.startswith('#')]
    if args.all:
        names = [os.path.splitext(f)[0] for f in os.listdir(OUR_SRC)
                 if f.endswith('.psc')]
    names = sorted(set(names))
    if not names:
        sys.exit('No scripts specified. Use names, --list FILE, or --all.')

    print(f'CK compiler: {CK}')
    print(f'Import dirs: {import_dirs()}')
    print(f'Checking {len(names)} scripts...\n')

    out_dir = tempfile.mkdtemp(prefix='ckcompile_')
    failures = []
    for name in names:
        nm, ok, out = compile_one(name, out_dir)
        if not ok:
            failures.append((nm, out))
            print(f'FAIL {nm}')
            # CK error lines look like: <path>(line,col): <message>
            for line in out.splitlines():
                s = line.strip()
                if re.search(r'\(\d+,\d+\):', s) or 'compilation failed' in s.lower():
                    print(f'     {s}')
    print(f'\n{len(names) - len(failures)}/{len(names)} compiled clean; '
          f'{len(failures)} failed.')
    if failures:
        print('\nFailed scripts:', ', '.join(f[0] for f in failures))


if __name__ == '__main__':
    main()
