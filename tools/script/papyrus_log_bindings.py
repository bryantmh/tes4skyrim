"""Classify "cannot be bound" property failures in a Papyrus log.

A VMAD object property binds only when the FormID it names is of the type the
script DECLARES. When it is not, the engine logs

    error: Property <P> on script <S> attached to <owner> (<fid>)
      cannot be bound because <form> (<fid>) is not the right type

and the property reads None for the rest of the session -- which silently
aborts whatever function touches it (see project_unbound_vmad_property_aborts).

This groups those failures by the DECLARED Papyrus type and the ACTUAL record
type of the FormID, so a whole class can be fixed at once instead of one script
at a time. The declared type is read from the converted .psc; the actual record
type from the output plugin.

Usage:
  # Summarise every binding failure, grouped by declared -> actual type:
  python tools/script/papyrus_log_bindings.py <Papyrus.0.log> --plugin Morrowind_ob.esm

  # Only one class, listing every property in it:
  python tools/script/papyrus_log_bindings.py <log> --plugin Morrowind_ob.esm \
      --declared Armor --verbose

  # Only failures whose target FormID does not exist at all (nullptr form):
  python tools/script/papyrus_log_bindings.py <log> --plugin Morrowind_ob.esm --missing
"""
import argparse
import os
import re
from collections import Counter, defaultdict

from asset_convert.sources import output_files

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# "Property P on script S attached to <owner> (FID) cannot be bound because
#  <form> (FID) is not the right type"
_LINE_RE = re.compile(
    r'Property (\w+) on script (\w+) attached to (.*?) '
    r'\(([0-9A-Fa-f]{8})\) cannot be bound because (.*?) '
    r'\(([0-9A-Fa-f]{8})\) is not the right type')


def load_output_types(plugin: str) -> dict:
    """{formid_low24: record signature} for the converted plugin."""
    from tools.dialog.dialog_emulator import read_tes5_file
    path = os.path.join(ROOT, 'output', plugin, plugin)
    if not os.path.isfile(path):
        raise SystemExit(f'no such plugin output: {path}')
    _hdr, recs, _loc = read_tes5_file(path)
    return {r.form_id & 0x00FFFFFF: r.type for r in recs}


def load_declared_types(plugin: str) -> dict:
    """{(script_low, prop_low): declared Papyrus type} from converted sources."""
    src_dir = os.path.join(ROOT, 'output', plugin, 'scripts', 'source')
    decl_re = re.compile(r'^\s*([A-Za-z_]\w*)\s+Property\s+(\w+)', re.M)
    out = {}
    output_files.unpack(src_dir, '.psc')
    if not os.path.isdir(src_dir):
        return out
    for fn in os.listdir(src_dir):
        if not fn.endswith('.psc'):
            continue
        script_low = fn[:-4].lower()
        try:
            text = open(os.path.join(src_dir, fn), encoding='utf-8',
                        errors='replace').read()
        except OSError:
            continue
        for m in decl_re.finditer(text):
            out[(script_low, m.group(2).lower())] = m.group(1)
    return out


def main():
    ap = argparse.ArgumentParser(
        description='Classify Papyrus "cannot be bound" property failures')
    ap.add_argument('log', help='Papyrus.N.log')
    ap.add_argument('--plugin', default='Morrowind_ob.esm',
                    help='plugin whose output/<plugin> supplies record types')
    ap.add_argument('--declared', help='only this declared Papyrus type')
    ap.add_argument('--actual', help='only this actual record signature')
    ap.add_argument('--missing', action='store_true',
                    help='only targets with no record in the plugin at all')
    ap.add_argument('--verbose', '-v', action='store_true',
                    help='list every distinct property, not just counts')
    ap.add_argument('--max', type=int, default=40,
                    help='max rows to list per section (default 40)')
    args = ap.parse_args()

    out_types = load_output_types(args.plugin)
    declared = load_declared_types(args.plugin)

    seen = set()
    pairs = Counter()
    by_pair = defaultdict(list)
    total = 0
    for line in open(args.log, encoding='utf-8', errors='replace'):
        m = _LINE_RE.search(line)
        if not m:
            continue
        total += 1
        prop, script, _owner, _ofid, _label, tfid = m.groups()
        key = (script.lower(), prop.lower(), tfid.lower())
        if key in seen:
            continue
        seen.add(key)
        dtype = declared.get((script.lower(), prop.lower()), '?')
        atype = out_types.get(int(tfid, 16) & 0x00FFFFFF, '<absent>')
        if args.declared and dtype != args.declared:
            continue
        if args.actual and atype != args.actual:
            continue
        if args.missing and atype != '<absent>':
            continue
        pairs[(dtype, atype)] += 1
        by_pair[(dtype, atype)].append((script, prop, tfid))

    print(f'log lines with a binding failure: {total}')
    print(f'distinct (script, property, target): {len(seen)}')
    print(f'shown after filters: {sum(pairs.values())}')
    print()
    print(f'{"declared":<20} {"actual":<12} {"count":>7}')
    print('-' * 42)
    for (dtype, atype), n in pairs.most_common(args.max):
        print(f'{dtype:<20} {atype:<12} {n:>7}')

    if args.verbose:
        for (dtype, atype), n in pairs.most_common(args.max):
            print()
            print(f'== {dtype} <- {atype} ({n}) ==')
            for script, prop, tfid in by_pair[(dtype, atype)][:args.max]:
                print(f'  {script}.{prop}  -> {tfid}')


if __name__ == '__main__':
    main()
