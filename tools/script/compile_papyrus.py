#!/usr/bin/env python3
"""
Compile Papyrus .psc scripts and report categorized error statistics.

Usage:
    python tools/script/compile_papyrus.py [--src DIR] [--out DIR] [--headers DIR]
    python tools/script/compile_papyrus.py --errors-detail  # Show first failing file per error category
"""
import argparse
import collections
import concurrent.futures
import os
import re
import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
from asset_convert.sources import output_files
from core.subprocess_flags import windows_cmd

def find_compiler():
    p = _PROJECT_ROOT / 'external' / 'papyrus-compiler' / 'papyrus.exe'
    if p.exists():
        return str(p)
    raise FileNotFoundError('papyrus.exe not found')

def find_skyrim_headers():
    """The vanilla .psc headers, via the pipeline's own lookup.

    Shared with the compile phase so this tool cannot disagree with a real
    build about where the headers are -- it also covers the Data/Scripts/Source
    layout and unpacks Data/Scripts.zip on CK builds that ship the sources only
    as that archive.  Off Windows, where winreg does not exist, that lookup
    resolves the install from conversion_config.json's tes5DataPath instead.
    """
    sys.path.insert(0, str(_PROJECT_ROOT))
    from convert import load_config
    from papyrus_compile import find_skyrim_source_scripts
    try:
        cfg = load_config()
    except (FileNotFoundError, OSError):
        cfg = {}
    headers = find_skyrim_source_scripts(cfg)
    if headers:
        return headers
    # Fallback for a non-registry (manually copied) install.
    p = Path(r'C:\Program Files (x86)\Steam\steamapps\common\Skyrim Special Edition\Data\Source\Scripts')
    if (p / 'Debug.psc').exists():
        return str(p)
    raise FileNotFoundError('Cannot find Skyrim papyrus headers')

def compile_one(args):
    f, compiler, out_dir, headers, polyfill_dir = args
    cmd = [compiler, 'compile', '-i', str(f), '-o', str(out_dir), '-h', headers]
    for extra in (polyfill_dir or '').split(';'):
        if extra:
            cmd.extend(['-h', extra])
    r = subprocess.run(windows_cmd(cmd), capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        combined = r.stdout + '\n' + r.stderr
        for line in combined.split('\n'):
            m = re.search(r'error:\s*(.+)', line)
            if m:
                raw = m.group(1).strip()
                key = re.sub(r'\d+', 'N', raw)
                key = re.sub(r"'[^']*'", 'X', key)
                return ('err', key, raw, f.name)
        return ('err', 'UNKNOWN', combined[:200], f.name)
    return ('ok', None, None, f.name)

def _parse_args():
    """The command line."""
    parser = argparse.ArgumentParser(description='Compile Papyrus scripts')
    parser.add_argument('--src', default=str(_PROJECT_ROOT / 'output' / 'oblivion.esm' / 'scripts' / 'source'))
    parser.add_argument('--out', default=str(_PROJECT_ROOT / 'temp' / 'pex'))
    parser.add_argument('--headers', default=None)
    parser.add_argument('--extra-headers', default=None,
                        help='additional header dir (e.g. output/<plugin>/scripts/source '
                             'when compiling a subset that names other converted types)')
    parser.add_argument('--errors-detail', action='store_true', help='Show sample file per error category')
    parser.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 4) - 1))
    return parser.parse_args()


def main():
    """Compile every `.psc` in `--src`, archived ones included, and summarize the errors."""
    args = _parse_args()
    compiler = find_compiler()
    headers = args.headers or find_skyrim_headers()
    src_dir = Path(args.src)
    output_files.unpack(src_dir, '.psc')
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Use polyfill dir so scripts can import TES4Polyfill
    polyfill_dir = str(src_dir) if (src_dir / 'TES4Polyfill.psc').exists() else None
    if args.extra_headers:
        polyfill_dir = f'{polyfill_dir};{args.extra_headers}' if polyfill_dir else args.extra_headers

    files = sorted(src_dir.glob('*.psc'))
    print(f'Compiling {len(files)} files with {args.workers} workers...')

    errors = collections.Counter()
    error_samples = {}  # key -> (raw_error, filename)
    ok = 0
    work = [(f, compiler, str(out_dir), headers, polyfill_dir) for f in files]

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for status, key, raw, fname in ex.map(compile_one, work):
            if status == 'ok':
                ok += 1
            else:
                errors[key] += 1
                if key not in error_samples:
                    error_samples[key] = (raw, fname)

    total = len(files)
    print(f'\nOK: {ok}/{total} ({ok*100/total:.1f}%)')
    print(f'Failed: {total - ok}')
    print()

    for key, count in errors.most_common(40):
        print(f'{count:5d}  {key}')
        if args.errors_detail and key in error_samples:
            raw, fname = error_samples[key]
            print(f'       -> {fname}: {raw}')

if __name__ == '__main__':
    main()
