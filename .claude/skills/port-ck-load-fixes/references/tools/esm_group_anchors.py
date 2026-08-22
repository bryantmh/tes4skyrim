"""Report GRUP records whose owning record does not immediately precede them.

The engine binds a type-1 (world children), type-6 (cell children) or type-7
(topic children) GRUP to a record ONLY by physical adjacency: xEdit's
TwbGroupRecord.InformPrevMainRecord (wbImplementation.pas ~18023) attaches the
group to the previous record iff

    grsGroupType in [1, 6, 7] and aPrevMainRecord.FixedFormID = GroupLabel

A group of one of those types that is not preceded by its owner is attached to
nothing, so every record inside it is unreachable by the engine — as invisible
as if it had never been written. This is a silent failure: the file loads, the
records are present in xEdit, and nothing appears in-game.

It also checks TOP-LEVEL GROUP ORDER, a second silent failure of the same
shape. A REFR resolves its NAME (base object) while the CELL group is being
parsed, so a base-object type written AFTER CELL/WRLD is not in the form map
yet and every reference to it is dropped. Measured on Oblivion.esm
(ckpe.log 2026-08-22): the MSTT group landed after CELL and CK deleted
**1,852 references** with "Missing/Invalid base object (01xxxxxx)" followed by
"Ref will be deleted", even though all 18 distinct base records were present
in the file. Nothing else catches this — the plugin is structurally valid,
xEdit shows the bases, and only the load order of the groups is wrong.

Usage:
    python tools/esm_group_anchors.py output/<Plugin>/<Plugin>
    python tools/esm_group_anchors.py <plugin> --verbose
    python tools/esm_group_anchors.py <plugin> --order-only
    python tools/esm_group_anchors.py <plugin> --reference <Skyrim.esm>
"""

import argparse
import struct
import sys

_HEADER_SIZE = 24
_OWNED = {1: 'world children', 6: 'cell children', 7: 'topic children'}

# Top-level signatures vanilla writes BEFORE its CELL group. Measured from the
# real Skyrim.esm and Dawnguard.esm, which agree on every entry here; pass
# --reference to re-derive it from a plugin instead of trusting this copy.
_VANILLA_BEFORE_CELL = [
    'GMST', 'KYWD', 'LCRT', 'AACT', 'TXST', 'GLOB', 'CLAS', 'FACT', 'HDPT',
    'HAIR', 'EYES', 'RACE', 'SOUN', 'ASPC', 'MGEF', 'SCPT', 'LTEX', 'ENCH',
    'SPEL', 'SCRL', 'ACTI', 'TACT', 'ARMO', 'BOOK', 'CONT', 'DOOR', 'INGR',
    'LIGH', 'MISC', 'APPA', 'STAT', 'SCOL', 'MSTT', 'PWAT', 'GRAS', 'TREE',
    'CLDC', 'FLOR', 'FURN', 'WEAP', 'AMMO', 'NPC_', 'LVLN', 'KEYM', 'ALCH',
    'IDLM', 'COBJ', 'PROJ', 'HAZD', 'SLGM', 'LVLI', 'WTHR', 'CLMT', 'SPGD',
    'RFCT', 'REGN', 'NAVI',
]


def top_level_order(path):
    """[signature] of every top-level GRUP, in file order."""
    with open(path, 'rb') as fh:
        hdr_size = struct.unpack_from('<I', fh.read(24), 4)[0]
        fh.seek(24 + hdr_size)
        order = []
        while True:
            g = fh.read(24)
            if len(g) < 24 or g[:4] != b'GRUP':
                break
            gsize, label = struct.unpack_from('<I4s', g, 4)
            order.append(label.decode('latin1'))
            fh.seek(gsize - 24, 1)
    return order


def check_order(path, reference=None):
    """[(sig, our_index, cell_index)] for groups written too late."""
    order = top_level_order(path)
    if 'CELL' not in order:
        return order, []
    cell = order.index('CELL')
    if reference:
        ref = top_level_order(reference)
        before = set(ref[:ref.index('CELL')]) if 'CELL' in ref else set()
    else:
        before = set(_VANILLA_BEFORE_CELL)
    late = [(sig, i, cell) for i, sig in enumerate(order)
            if i > cell and sig in before]
    return order, late


def scan(path):
    """Returns (anchored, orphans) where orphans is [(gtype, owner_fid), ...]."""
    with open(path, 'rb') as f:
        data = f.read()
    if data[:4] != b'TES4':
        raise SystemExit(f"Not a plugin file: {path}")

    anchored, orphans = [], []

    def walk(off, end):
        prev = None
        while off + _HEADER_SIZE <= end:
            sig = data[off:off + 4]
            size = struct.unpack_from('<I', data, off + 4)[0]
            if sig == b'GRUP':
                label = data[off + 8:off + 12]
                gtype = struct.unpack_from('<i', data, off + 12)[0]
                if gtype in _OWNED and len(label) == 4:
                    owner = struct.unpack('<I', label)[0]
                    entry = (gtype, owner)
                    (anchored if prev == owner else orphans).append(entry)
                walk(off + _HEADER_SIZE, off + size)
                off += size
                prev = None       # a group breaks the record/group adjacency
            else:
                prev = struct.unpack_from('<I', data, off + 12)[0]
                off += _HEADER_SIZE + size

    walk(_HEADER_SIZE + struct.unpack_from('<I', data, 4)[0], len(data))
    return anchored, orphans


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('plugin')
    ap.add_argument('--verbose', action='store_true',
                    help='list every orphaned group, not just a summary')
    ap.add_argument('--order-only', action='store_true',
                    help='only run the top-level group-order check (seconds, '
                         'vs a full walk of a 600 MB file)')
    ap.add_argument('--reference',
                    help='derive the canonical order from this plugin '
                         '(e.g. a real Skyrim.esm) instead of the built-in list')
    args = ap.parse_args()

    order, late = check_order(args.plugin, args.reference)
    print(f"{args.plugin}")
    print(f"  top-level groups: {len(order)}")
    if late:
        print(f"  GROUPS WRITTEN AFTER CELL that vanilla puts before it: "
              f"{len(late)}")
        for sig, i, cell in late:
            print(f"    {sig} at #{i}, CELL at #{cell} — every reference to a "
                  f"{sig} base object will be dropped at load")
    else:
        print("  group order OK: no base-object type written after CELL")
    if args.order_only:
        return 1 if late else 0

    anchored, orphans = scan(args.plugin)
    print(f"  owned groups correctly anchored: {len(anchored)}")
    print(f"  ORPHANED (unreachable in-engine): {len(orphans)}")
    if orphans:
        by_type = {}
        for gtype, owner in orphans:
            by_type.setdefault(gtype, []).append(owner)
        for gtype in sorted(by_type):
            fids = by_type[gtype]
            print(f"    type {gtype} ({_OWNED[gtype]}): {len(fids)}")
            shown = fids if args.verbose else fids[:10]
            for fid in shown:
                print(f"      owner {fid:08X} has no preceding record")
            if not args.verbose and len(fids) > len(shown):
                print(f"      ... and {len(fids) - len(shown)} more "
                      f"(use --verbose)")
    return 1 if (orphans or late) else 0


if __name__ == '__main__':
    sys.exit(main())
