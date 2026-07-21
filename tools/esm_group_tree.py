"""Dump the GRUP nesting around a record in a TES5 ESM/ESP.

Walks the file's group structure and prints the tree surrounding a target
record (by FormID), including every sibling group of the record's children
group and a summary of each child group's contents.

Usage:
  python tools/esm_group_tree.py <plugin> --formid 011A575C [--ignore-index]
  python tools/esm_group_tree.py <plugin> --top CELL --max-depth 3
"""
import argparse
import mmap
import struct
import sys

GROUP_TYPE_NAMES = {
    0: 'top',
    1: 'world children',
    2: 'interior block',
    3: 'interior sub-block',
    4: 'exterior block',
    5: 'exterior sub-block',
    6: 'cell children',
    7: 'topic children',
    8: 'cell persistent children',
    9: 'cell temporary children',
    10: 'cell visible-distant children',
}


def group_label_str(gtype: int, label: bytes) -> str:
    if gtype == 0:
        return label.decode('ascii', 'replace')
    if gtype in (2, 3):
        return f'block {struct.unpack("<i", label)[0]}'
    if gtype in (4, 5):
        y, x = struct.unpack('<hh', label)
        return f'grid ({x},{y})'
    return f'{struct.unpack("<I", label)[0]:08X}'


def walk(mm, start, end, depth, ctx):
    """Yield events while walking [start, end)."""
    pos = start
    while pos < end:
        tag = mm[pos:pos + 4]
        if tag == b'GRUP':
            gsize, label, gtype = struct.unpack('<I4sI', mm[pos + 4:pos + 16])
            yield ('group', pos, depth, gtype, label, gsize)
            yield from walk(mm, pos + 24, pos + gsize, depth + 1, ctx)
            pos += gsize
        else:
            dsize, flags, fid = struct.unpack('<III', mm[pos + 4:pos + 16])
            comp_extra = 0
            yield ('record', pos, depth, tag.decode('ascii', 'replace'),
                   fid, flags, dsize)
            pos += 24 + dsize + comp_extra


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('plugin')
    ap.add_argument('--formid', help='Target record FormID (hex)')
    ap.add_argument('--ignore-index', action='store_true',
                    help='Match FormID ignoring the load-order index byte')
    ap.add_argument('--context', type=int, default=2,
                    help='Sibling records to show around the target')
    args = ap.parse_args()

    target = int(args.formid, 16) if args.formid else None
    mask = 0x00FFFFFF if args.ignore_index else 0xFFFFFFFF

    with open(args.plugin, 'rb') as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)

    # TES4 header record first
    dsize = struct.unpack('<I', mm[4:8])[0]
    pos = 24 + dsize

    stack = []  # (event tuple) for groups currently open
    # First pass: find target record position and remember enclosing groups.
    found = None
    enclosing = []
    for ev in walk(mm, pos, len(mm), 0, None):
        kind, off, depth, *rest = ev
        if kind == 'group':
            while stack and stack[-1][2] >= depth:
                stack.pop()
            stack.append(ev)
        elif kind == 'record':
            sig, fid, flags, rsize = rest
            while stack and stack[-1][2] > depth:
                stack.pop()
            if target is not None and (fid & mask) == (target & mask):
                found = ev
                enclosing = list(stack)
                print(f'--- match {sig} {fid:08X} at 0x{off:X} '
                      f'(flags=0x{flags:08X}, size={rsize}) ---')
                for g in enclosing:
                    _, goff, gdepth, gtype, glabel, gsize = g
                    print('  ' * gdepth +
                          f'GRUP type {gtype} ({GROUP_TYPE_NAMES.get(gtype, "?")}) '
                          f'label={group_label_str(gtype, glabel)} '
                          f'size={gsize} at 0x{goff:X}')
                print('  ' * (found[2]) + f'>> {sig} {fid:08X}')

                # Dump the subtree that follows this record if it is a CELL/
                # WRLD/DIAL: its children group should come immediately after.
                nxt = off + 24 + rsize
                if nxt < len(mm) and mm[nxt:nxt + 4] == b'GRUP':
                    gsize2, label2, gtype2 = struct.unpack(
                        '<I4sI', mm[nxt + 4:nxt + 16])
                    if gtype2 in (1, 6, 7):
                        print(f'  children GRUP type {gtype2} '
                              f'({GROUP_TYPE_NAMES.get(gtype2, "?")}) '
                              f'label={group_label_str(gtype2, label2)} '
                              f'size={gsize2}:')
                        counts = {}
                        for ev2 in walk(mm, nxt + 24, nxt + gsize2, 1, None):
                            k2, off2, d2, *r2 = ev2
                            if k2 == 'group':
                                gt2, gl2, gs2 = r2
                                print('    ' * d2 +
                                      f'GRUP type {gt2} '
                                      f'({GROUP_TYPE_NAMES.get(gt2, "?")}) '
                                      f'label={group_label_str(gt2, gl2)} '
                                      f'size={gs2}')
                            else:
                                s2, f2, fl2, sz2 = r2
                                counts.setdefault((d2, s2), []).append(
                                    (f2, fl2))
                        for (d2, s2), lst in sorted(counts.items()):
                            sample = ' '.join(f'{f:08X}' for f, _ in lst[:6])
                            pflags = {fl for _, fl in lst}
                            print('    ' * d2 +
                                  f'{s2} x{len(lst)}  [{sample}'
                                  f'{" ..." if len(lst) > 6 else ""}] '
                                  f'flags={{{", ".join(f"0x{p:08X}" for p in sorted(pflags))}}}')
                else:
                    print('  (no children GRUP follows)')
    if target is not None and not found:
        print(f'FormID {args.formid} not found')
        sys.exit(1)


if __name__ == '__main__':
    main()
