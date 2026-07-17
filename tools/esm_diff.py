"""Semantic diff of two TES5 plugin files.

Compares two ESM/ESP files top-level group by top-level group:
  1. byte-identical groups are reported as OK;
  2. differing groups are broken down by record — records are keyed by
     (signature, FormID) and compared by content hash, so the tool
     distinguishes "same records, different order" (a harmless reorder,
     e.g. from historical thread-completion nondeterminism) from real
     added/removed/changed records.

Usage:
    python tools/esm_diff.py A.esm B.esm            # summary of all groups
    python tools/esm_diff.py A.esm B.esm -g CELL    # detail one group
    python tools/esm_diff.py A.esm B.esm --list-changed 20
"""

import argparse
import hashlib
import mmap
import struct
import sys
from collections import Counter

RECORD_HEADER_SIZE = 24
GROUP_HEADER_SIZE = 24


def _iter_top_groups(mm):
    """Yield (label, start, end) for each top-level GRUP."""
    n = len(mm)
    # Skip TES4 header record
    if mm[0:4] != b'TES4':
        raise ValueError('not a TES5 plugin (no TES4 header)')
    data_size = struct.unpack_from('<I', mm, 4)[0]
    pos = RECORD_HEADER_SIZE + data_size
    while pos + GROUP_HEADER_SIZE <= n:
        if mm[pos:pos + 4] != b'GRUP':
            raise ValueError(f'expected GRUP at 0x{pos:X}')
        size = struct.unpack_from('<I', mm, pos + 4)[0]
        label = mm[pos + 8:pos + 12].decode('ascii', errors='replace')
        yield label, pos, pos + size
        pos += size


def _iter_records(mm, start, end):
    """Recursively yield (sig, formid, record_bytes) for every record in a group."""
    pos = start + GROUP_HEADER_SIZE
    while pos + RECORD_HEADER_SIZE <= end:
        tag = mm[pos:pos + 4]
        size = struct.unpack_from('<I', mm, pos + 4)[0]
        if tag == b'GRUP':
            yield from _iter_records(mm, pos, pos + size)
            pos += size
        else:
            total = RECORD_HEADER_SIZE + size
            fid = struct.unpack_from('<I', mm, pos + 12)[0]
            yield tag.decode('ascii', errors='replace'), fid, mm[pos:pos + total]
            pos += total


def _record_index(mm, start, end):
    """{(sig, fid): sha1-of-bytes} plus a Counter of duplicate keys."""
    index = {}
    dupes = Counter()
    for sig, fid, blob in _iter_records(mm, start, end):
        key = (sig, fid)
        if key in index:
            dupes[key] += 1
        index[key] = hashlib.sha1(blob).hexdigest()
    return index, dupes


def _open(path):
    f = open(path, 'rb')
    return mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)


def main():
    ap = argparse.ArgumentParser(description='Semantic diff of two TES5 plugins')
    ap.add_argument('file_a')
    ap.add_argument('file_b')
    ap.add_argument('-g', '--group', help='only diff this top-level group signature')
    ap.add_argument('--list-changed', type=int, default=10, metavar='N',
                    help='max changed/added/removed FormIDs to list per group')
    args = ap.parse_args()

    mm_a = _open(args.file_a)
    mm_b = _open(args.file_b)

    groups_a = {label: (s, e) for label, s, e in _iter_top_groups(mm_a)}
    groups_b = {label: (s, e) for label, s, e in _iter_top_groups(mm_b)}

    labels = [l for l in groups_a if l in groups_b]
    only_a = [l for l in groups_a if l not in groups_b]
    only_b = [l for l in groups_b if l not in groups_a]
    if args.group:
        labels = [l for l in labels if l == args.group]

    identical = reordered = differing = 0
    for label in labels:
        sa, ea = groups_a[label]
        sb, eb = groups_b[label]
        if (ea - sa) == (eb - sb) and mm_a[sa:ea] == mm_b[sb:eb]:
            identical += 1
            print(f'  {label}: OK (byte-identical, {ea - sa:,} bytes)')
            continue
        idx_a, dup_a = _record_index(mm_a, sa, ea)
        idx_b, dup_b = _record_index(mm_b, sb, eb)
        added = [k for k in idx_b if k not in idx_a]
        removed = [k for k in idx_a if k not in idx_b]
        changed = [k for k in idx_a if k in idx_b and idx_a[k] != idx_b[k]]
        if not added and not removed and not changed:
            reordered += 1
            print(f'  {label}: SAME RECORDS, different order/structure '
                  f'({len(idx_a)} records)')
            continue
        differing += 1
        print(f'  {label}: DIFFERS — {len(idx_a)}/{len(idx_b)} records, '
              f'+{len(added)} -{len(removed)} ~{len(changed)}')
        n = args.list_changed
        for tag, keys in (('+', added), ('-', removed), ('~', changed)):
            for sig, fid in keys[:n]:
                print(f'      {tag} {sig} {fid:08X}')
            if len(keys) > n:
                print(f'      {tag} ... and {len(keys) - n} more')

    for label in only_a:
        print(f'  {label}: only in {args.file_a}')
    for label in only_b:
        print(f'  {label}: only in {args.file_b}')

    print(f'\n{identical} identical, {reordered} reordered, {differing} differing, '
          f'{len(only_a) + len(only_b)} unmatched groups')
    return 0 if (differing == 0 and not only_a and not only_b) else 1


if __name__ == '__main__':
    sys.exit(main())
