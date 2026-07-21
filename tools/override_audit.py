"""Audit override coverage for a plugin conversion.

For every record in a plugin's export that overrides a master record, this
reports what happens to it on the override path WITHOUT running a conversion:

  - emitted:      diff found authored changes and every change has a mapping
  - partial:      emitted, but some authored changes have no mapping (listed)
  - unchanged:    authorially identical to the master (correctly dropped)
  - no-base:      the master's conversion has no record to override (dropped)
  - no-path:      record type has no override path in import_main at all

Usage:
  python tools/override_audit.py export/Translation.esp [--out-root output]
  python tools/override_audit.py export/Translation.esp --keys   # unmapped detail
"""
import argparse
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tes5_import.export_diff import diff_records
from tes5_import.master_manifest import load_master_manifests
from tes5_import.override_merge import load_master_index
from tes5_import.overrides import (load_master_export, master_output_formid,
                                   OVERRIDE_UNMAPPABLE_TYPES)
from tes5_import.override_builder import RECONVERT_KEYS, apply_changes
from tes5_import.constants import IMPORT_DISPATCH, SKIP_TYPES
from tes5_import.text_reader import (parse_export_directory,
                                     group_records_by_type,
                                     set_formid_index_offset)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('export_dir', help='Plugin export dir (export/<Plugin>)')
    ap.add_argument('--out-root', default='output',
                    help='Root holding converted masters (default: output)')
    ap.add_argument('--keys', action='store_true',
                    help='Per-key unmapped detail with example records')
    args = ap.parse_args()

    header = os.path.join(args.export_dir, '_HEADER.txt')
    masters = []
    with open(header, 'r', encoding='utf-8') as f:
        for line in f:
            if line.startswith('Master['):
                masters.append(line.partition('=')[2].strip())
    if not masters:
        print('Plugin has no TES4 masters; nothing to audit.')
        return

    n = len(masters)
    tes5_masters = ['Skyrim.esm'] + masters
    # The arithmetic FormID fallback (master_output_formid) shifts by the
    # count of newly prepended masters, exactly as import_main does.
    set_formid_index_offset(len(tes5_masters) - n)
    master_index = load_master_index(tes5_masters, n, args.out_root)
    manifest = load_master_manifests(tes5_masters, n, args.out_root)
    master_export = load_master_export(args.export_dir)

    plugin_records = list(parse_export_directory(args.export_dir))
    by_type = group_records_by_type(plugin_records)

    stats = defaultdict(Counter)          # sig -> outcome counter
    unmapped_by_key = Counter()
    unmapped_examples = defaultdict(list)  # key -> [(sig, fid, edid)]

    for sig, recs in sorted(by_type.items()):
        for rec in recs:
            src_fid = (rec.get('FormID') or '').upper()
            master_rec = master_export.get(src_fid)
            if master_rec is None:
                stats[sig]['new'] += 1
                continue
            if sig in SKIP_TYPES:
                stats[sig]['skipped-type'] += 1
                continue
            out_fid = master_output_formid(src_fid, manifest)
            base = master_index.record(out_fid) if out_fid else b''
            if not base:
                stats[sig]['no-base'] += 1
                continue
            changes = diff_records(master_rec, rec)
            if not changes:
                stats[sig]['unchanged'] += 1
                continue
            if sig in OVERRIDE_UNMAPPABLE_TYPES:
                stats[sig]['no-path'] += 1
                continue
            if any((sig, key) in RECONVERT_KEYS for key in changes):
                stats[sig]['reconvert'] += 1
                continue
            _bytes, _applied, unmapped = apply_changes(base, changes, rec,
                                                       master_rec)
            if unmapped:
                stats[sig]['partial'] += 1
                for key in unmapped:
                    unmapped_by_key[key] += 1
                    if len(unmapped_examples[key]) < 3:
                        unmapped_examples[key].append(
                            (sig, src_fid, rec.get('EditorID', '?')))
            else:
                stats[sig]['emitted'] += 1

    outcomes = ['emitted', 'partial', 'reconvert', 'unchanged', 'no-base',
                'no-path', 'skipped-type', 'new']
    print(f"{'type':<6}" + ''.join(f'{o:>14}' for o in outcomes))
    totals = Counter()
    for sig in sorted(stats):
        row = stats[sig]
        totals.update(row)
        print(f"{sig:<6}" + ''.join(f'{row.get(o, 0):>14}' for o in outcomes))
    print(f"{'TOTAL':<6}" + ''.join(f'{totals.get(o, 0):>14}' for o in outcomes))

    if unmapped_by_key:
        print(f"\nUnmapped authored changes "
              f"({sum(unmapped_by_key.values())} across "
              f"{len(unmapped_by_key)} keys):")
        for key, count in unmapped_by_key.most_common():
            line = f"  {key:<28} {count:>5}"
            if args.keys:
                ex = ', '.join(f'{s} {f} {e}'
                               for s, f, e in unmapped_examples[key])
                line += f"   e.g. {ex}"
            print(line)


if __name__ == '__main__':
    main()
