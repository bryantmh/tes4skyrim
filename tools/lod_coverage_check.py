#!/usr/bin/env python3
"""Worldspaces a built plugin ships with NO distant-object LOD.

WHY THIS EXISTS -- "the Creation Kit shows everything, the game does not".

The CK loads a whole worldspace at once and draws every reference from the
record, at any distance.  The game draws full models only inside the
uGridsToLoad window (5x5 cells by default); everything beyond that comes from
the object-LOD meshes under

    meshes/terrain/<WorldspaceEditorID>/Objects/*.bto     (objects)
    meshes/terrain/<WorldspaceEditorID>/*.btr             (terrain)

If those were never generated, distant buildings, rocks and trees are simply
absent in game and pop in as the player walks within ~2 cells -- while the
editor shows a fully populated world.  No record-level audit can see this:
the plugin is perfectly valid, the assets just are not there.

Both the loose output tree and the plugin's BSAs are searched, because the
game reads either.

Usage:
    python tools/lod_coverage_check.py --plugin Oblivion.esm
    python tools/lod_coverage_check.py --plugin Oblivion.esm --min-cells 4

Exit code is 1 when a worldspace with at least --min-cells exterior cells has
no object LOD, so it can gate a build.
"""

import argparse
import os
import struct
import sys
import zlib
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _subs(body):
    out, p, n = {}, 0, len(body)
    while p + 6 <= n:
        sig = body[p:p + 4]
        size = struct.unpack_from('<H', body, p + 4)[0]
        out[sig] = body[p + 6:p + 6 + size]
        p += 6 + size
    return out


def _worldspaces(path):
    """WRLD fid -> EditorID, and a count of its exterior block cells."""
    data = open(path, 'rb').read()
    names, cells = {}, Counter()

    def walk(off, end, stack):
        p = off
        while p + 24 <= end:
            if data[p:p + 4] == b'GRUP':
                gsize, label, gtype = struct.unpack_from('<IiI', data, p + 4)
                walk(p + 24, p + gsize, stack + [(gtype, label)])
                p += gsize
                continue
            sig = data[p:p + 4]
            size, flags, fid = struct.unpack_from('<III', data, p + 4)
            body = data[p + 24:p + 24 + size]
            if flags & 0x00040000:
                try:
                    body = zlib.decompress(body[4:])
                except zlib.error:
                    body = b''
            if sig == b'WRLD':
                edid = _subs(body).get(b'EDID', b'')
                names[fid] = edid.split(bytes(1))[0].decode('latin-1')
            elif sig == b'CELL' and any(t == 4 for t, _ in stack):
                # A type-1 GRUP's label is the owning worldspace's FormID.
                wrld = next((l for t, l in stack if t == 1), None)
                if wrld is not None and _subs(body).get(b'XCLC'):
                    cells[wrld] += 1
            p += 24 + size

    walk(0, len(data), [])
    return names, cells


def _lod_names(out_dir):
    """Lowercased LOD-ish relative paths from the loose tree and the BSAs."""
    found = set()
    terrain = os.path.join(out_dir, 'meshes', 'terrain')
    for base, _dirs, files in os.walk(terrain):
        rel = os.path.relpath(base, os.path.join(out_dir, 'meshes'))
        for fn in files:
            if fn.lower().endswith(('.bto', '.btr')):
                found.add(os.path.join('meshes', rel, fn).lower()
                          .replace('/', os.sep))
    tools_dir = os.path.join(ROOT, 'tools')
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    from bsa_list_names import list_names
    if os.path.isdir(out_dir):
        for fn in sorted(os.listdir(out_dir)):
            if not fn.lower().endswith('.bsa'):
                continue
            try:
                for n in list_names(os.path.join(out_dir, fn)):
                    low = n.lower()
                    if low.endswith(('.bto', '.btr')):
                        found.add(low.replace(chr(92), os.sep))
            except Exception as exc:
                print('  WARN unreadable archive %s: %s' % (fn, exc))
    return found


def main():
    ap = argparse.ArgumentParser(
        description='Worldspaces shipped without distant-object LOD')
    ap.add_argument('--plugin', required=True)
    ap.add_argument('--output-dir', default=os.path.join(ROOT, 'output'))
    ap.add_argument('--min-cells', type=int, default=4,
                    help='ignore worldspaces smaller than this many cells')
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from output_layout import paths as _paths
    p = _paths(args.plugin, out_root=args.output_dir)
    path = str(p.esm)
    if not os.path.isfile(path):
        print('ERROR: no converted plugin at %s' % path)
        return 1

    names, cells = _worldspaces(path)
    lod = _lod_names(str(p.out))
    print('plugin: %s' % args.plugin)
    print('LOD meshes shipped (loose + BSA): %d' % len(lod))
    print()

    bad = []
    for wrld, n in cells.most_common():
        if n < args.min_cells:
            continue
        edid = names.get(wrld) or '%08X' % wrld
        key = os.path.join('meshes', 'terrain', edid.lower()) + os.sep
        objs = sum(1 for f in lod if f.startswith(key) and f.endswith('.bto'))
        terr = sum(1 for f in lod if f.startswith(key) and f.endswith('.btr'))
        mark = 'ok  ' if objs else 'FAIL'
        if not objs:
            bad.append((edid, n))
        print('  %s  %-32s cells %-6d objectLOD %-6d terrainLOD %d'
              % (mark, edid, n, objs, terr))

    print()
    if bad:
        print('%d worldspace(s) ship NO object LOD.' % len(bad))
        print('In game every object beyond the loaded cell grid is missing;')
        print('the Creation Kit will still show the world fully populated.')
        print('Build it with:  python convert.py -f %s --lod-only'
              % args.plugin)
        return 1
    print('Every worldspace ships object LOD.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
