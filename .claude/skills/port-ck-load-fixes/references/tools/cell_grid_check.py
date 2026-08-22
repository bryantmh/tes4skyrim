#!/usr/bin/env python3
"""Verify worldspace CELL grid placement in a TES5 plugin.

Why this exists: the engine builds its grid-cell array by walking a
worldspace's type-4 block / type-5 sub-block GRUP tree and reading each cell's
coordinates from XCLC.  A cell filed inside that tree with NO XCLC leaves the
grid entry unallocated, and the cell-streaming tick then indexes a null array
(SkyrimSE.exe+050E6AD `mov rbx,[rax+rcx*8]` with rax=0 — the load is NOT
bounds-checked, because an allocated grid array is an assumed invariant).
That is a hard CTD as soon as the player streams near the cell.

The invariant, measured against vanilla: 0 of 16,942 exterior-block CELLs in
Skyrim.esm lack XCLC.  Any nonzero count here is a real defect.

Oblivion leaves the persistent bit (RecordFlags & 0x400) CLEAR on ~30 such
cells, so classifying on the flag alone is not enough — see
docs/world_land_navmesh_notes.md.

Usage:
    # check a converted plugin
    python tools/cell_grid_check.py output/Oblivion.esm/Oblivion.esm

    # confirm the invariant on vanilla (expects 0)
    python tools/cell_grid_check.py "<skyrim>/Data/Skyrim.esm"

    # also list duplicate grid squares within a worldspace
    python tools/cell_grid_check.py <esm> --duplicates

    # per-worldspace grid extents + cells outside CK's legal coordinate range
    python tools/cell_grid_check.py <esm> --extents

Exit code is 1 when any gridless block cell is found, so it can gate a build.
"""

import argparse
import math
import struct
import sys
import zlib
from collections import defaultdict

COMPRESSED = 0x00040000

# Creation Kit rejects an exterior cell whose |X| or |Y| exceeds this and tries
# to DELETE it during "Initializing References" (the compare is literally
# `cmp eax, 0x64` at CreationKit.exe+0x1bd3cdf / +0x1bd3cf8, inside
# TESObjectCELL's init-references virtual, guarded by an editor-only flag; the
# failure path logs "Unable to delete invalid coord cell (%i, %i)").
CK_MAX_CELL_COORD = 100


def _subs(buf):
    """First occurrence of each subrecord signature in a record body."""
    out, pos = {}, 0
    while pos + 6 <= len(buf):
        sig = buf[pos:pos + 4]
        size = struct.unpack_from('<H', buf, pos + 4)[0]
        pos += 6
        out.setdefault(sig, buf[pos:pos + size])
        pos += size
    return out


def scan(path, want_refs=False):
    """Walk the plugin, tracking GRUP nesting so block membership is exact.

    With `want_refs`, also collects what the teleport check needs: every
    REFR's position, its enclosing cell, and its XTEL destination door.
    """
    with open(path, 'rb') as fh:
        data = fh.read()

    worlds = {}                      # wrld fid -> editor id
    gridless = []                    # (fid, edid, wrld fid)
    grids = defaultdict(list)        # (wrld, gx, gy) -> [fid]
    n_block_cells = 0
    cells = {}                       # cell fid -> (edid, wrld, interior)
    refs = {}                        # refr fid -> (cell fid, x, y)
    xtel = {}                        # refr fid -> destination door fid
    cur_cell = None

    stack, pos = [], 0
    while pos + 24 <= len(data):
        while stack and pos >= stack[-1][0]:
            stack.pop()
        sig = data[pos:pos + 4]
        if sig == b'TES4':
            pos += 24 + struct.unpack_from('<I', data, pos + 4)[0]
            continue
        if sig == b'GRUP':
            gsize, label, gtype = struct.unpack_from('<IiI', data, pos + 4)[:3]
            stack.append((pos + gsize, gtype, label))
            pos += 24
            continue
        size, flags, fid = struct.unpack_from('<III', data, pos + 4)
        if sig in (b'WRLD', b'CELL'):
            body = data[pos + 24:pos + 24 + size]
            if flags & COMPRESSED:
                body = zlib.decompress(body[4:])
            d = _subs(body)
            edid = d.get(b'EDID', b'').split(b'\x00')[0].decode('ascii', 'replace')
            if sig == b'WRLD':
                worlds[fid] = edid
            else:
                cur_cell = fid
                wrld = next((g[2] for g in stack if g[1] == 1), None)
                dat = d.get(b'DATA', b'\0')
                cells[fid] = (edid, wrld, bool(dat[0] & 1))
                if any(g[1] in (4, 5) for g in stack):
                    n_block_cells += 1
                    xclc = d.get(b'XCLC')
                    if xclc is None:
                        gridless.append((fid, edid, wrld))
                    elif len(xclc) >= 8:
                        gx, gy = struct.unpack_from('<ii', xclc)
                        grids[(wrld, gx, gy)].append(fid)
        elif want_refs and sig in (b'REFR', b'ACHR'):
            body = data[pos + 24:pos + 24 + size]
            if flags & COMPRESSED:
                body = zlib.decompress(body[4:])
            d = _subs(body)
            dat = d.get(b'DATA')
            if dat and len(dat) >= 8:
                x, y = struct.unpack_from('<ff', dat)
                refs[fid] = (cur_cell, x, y)
            t = d.get(b'XTEL')
            if t and len(t) >= 16:
                # formid(4) + posX,posY,posZ(12) + rotX,rotY,rotZ(12) [+flags]
                door, tx, ty = struct.unpack_from('<Iff', t)
                xtel[fid] = (door, tx, ty)
        pos += 24 + size

    return worlds, gridless, grids, n_block_cells, cells, refs, xtel


def check_teleport_cells(worlds, grids, cells, refs, xtel):
    """XTEL doors whose destination grid square has no cell → CK crashes.

    Creation Kit's teleport-data init (`CreationKit.exe` 1.5.73 `+0x15f0580`,
    `..\\Shared\\TESForms\\World\\TESObjectCELL_Reference.cpp`) resolves the
    linked door, and for a door in an EXTERIOR cell re-derives the destination
    cell from the worldspace grid. It null-checks the door and the door's
    parent cell — it logs "Could not find linked door" and "Linked door … has
    no parent cell" — but it does NOT null-check the grid lookup before
    passing the result on (`call` at `+0x15f0893`). A miss returns null and
    the callee reads `[this+0x10]`: EXCEPTION_ACCESS_VIOLATION reading
    0x0000000000000010, which is exactly the captured crash.
    """
    bad = []
    for src, (door, tx, ty) in xtel.items():
        info = refs.get(door)
        if info is None:
            continue                      # unresolved door: CK logs, no crash
        cell_fid = info[0]
        cell = cells.get(cell_fid)
        if cell is None or cell[2]:
            continue                      # interior destination is safe
        wrld = cell[1]
        # The grid square comes from the XTEL's OWN destination coordinates
        # (CK reads them from the teleport extra at arg0+8), not from where
        # the door reference happens to stand.
        gx, gy = int(math.floor(tx / 4096.0)), int(math.floor(ty / 4096.0))
        if (wrld, gx, gy) not in grids:
            bad.append((src, door, cell_fid, wrld, gx, gy, tx, ty))
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('plugin')
    ap.add_argument('--duplicates', action='store_true',
                    help='also report grid squares claimed by more than one cell')
    ap.add_argument('--extents', action='store_true',
                    help='per-worldspace grid extents, plus every cell outside '
                         f'CK\'s legal +/-{CK_MAX_CELL_COORD} coordinate range')
    ap.add_argument('--holes', action='store_true',
                    help='report ENCLOSED grid holes (a missing (x,y) whose four '
                         'neighbours all exist). Vanilla Skyrim has 2, at arbitrary '
                         'coords; a hole at exactly (0,0) is the signature of a cell '
                         'whose TES4 record omitted XCLC')
    ap.add_argument('--teleport-cells', action='store_true',
                    help='XTEL doors whose exterior destination grid square '
                         'has no cell — CK dereferences the null and crashes')
    args = ap.parse_args()

    worlds, gridless, grids, n_block_cells, cells, refs, xtel = scan(
        args.plugin, want_refs=args.teleport_cells)

    print(f'exterior-block CELLs: {n_block_cells}')
    print(f'MISSING XCLC:         {len(gridless)}')
    for fid, edid, wrld in gridless:
        wname = worlds.get(wrld, '?')
        wtxt = f'{wname} ({wrld:08X})' if wrld is not None else '(no worldspace)'
        print(f'  {fid:08X}  {edid or "(no edid)":38s} world={wtxt}')

    if args.duplicates:
        dupes = {k: v for k, v in grids.items() if len(v) > 1}
        print(f'\nduplicate grid squares: {len(dupes)}')
        for (wrld, gx, gy), fids in sorted(dupes.items())[:40]:
            ids = ' '.join(f'{f:08X}' for f in fids)
            print(f'  {worlds.get(wrld, "?")} ({gx},{gy}): {ids}')

    n_tele = 0
    if args.teleport_cells:
        bad = check_teleport_cells(worlds, grids, cells, refs, xtel)
        n_tele = len(bad)
        print(f'\nXTEL doors resolved: {len(xtel)}')
        print(f'destination grid square MISSING: {n_tele}')
        for src, door, cell_fid, wrld, gx, gy, x, y in bad[:40]:
            wname = worlds.get(wrld, '?')
            cname = cells.get(cell_fid, ('?',))[0]
            print(f'  ref {src:08X} -> door {door:08X} in {cname} '
                  f'({cell_fid:08X}) world {wname}: pos ({x:.1f},{y:.1f}) '
                  f'= grid ({gx},{gy}) — no cell there')
        if n_tele > 40:
            print(f'  ... and {n_tele - 40} more')

    n_oob = 0
    if args.extents:
        by_world = defaultdict(list)
        for (wrld, gx, gy), fids in grids.items():
            by_world[wrld].append((gx, gy, fids))
        print('\nworldspace grid extents:')
        for wrld, cells in sorted(by_world.items(), key=lambda kv: -len(kv[1])):
            xs = [c[0] for c in cells]
            ys = [c[1] for c in cells]
            oob = [c for c in cells
                   if abs(c[0]) > CK_MAX_CELL_COORD
                   or abs(c[1]) > CK_MAX_CELL_COORD]
            n_oob += len(oob)
            mark = f'  <-- {len(oob)} OUTSIDE CK RANGE' if oob else ''
            print(f'  {worlds.get(wrld, "?"):34s} {len(cells):6d} cells  '
                  f'x[{min(xs)},{max(xs)}] y[{min(ys)},{max(ys)}]{mark}')
            for gx, gy, fids in sorted(oob)[:20]:
                print(f'      ({gx},{gy}) '
                      + ' '.join(f'{f:08X}' for f in fids))
        print(f'cells outside +/-{CK_MAX_CELL_COORD}: {n_oob}')

    n_holes = 0
    if args.holes:
        # Per-cell neighbour walk, NOT a bounding-box sweep: Tamriel's span makes
        # the bbox form O(range^2) and it never finishes.
        by_world = defaultdict(set)
        for (wrld, gx, gy) in grids:
            by_world[wrld].add((gx, gy))
        print()
        for wrld, cells in sorted(by_world.items(),
                                  key=lambda kv: -len(kv[1])):
            cand = set()
            for (x, y) in cells:
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    if (x + dx, y + dy) not in cells:
                        cand.add((x + dx, y + dy))
            holes = [p for p in cand
                     if all((p[0] + dx, p[1] + dy) in cells
                            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))]
            if holes:
                if (0, 0) in holes:
                    n_holes += 1
                flag = '  <-- (0,0): omitted XCLC' if (0, 0) in holes else ''
                print(f'  HOLE {worlds.get(wrld, "?"):32s} '
                      f'{len(holes):3d}  {sorted(holes)[:6]}{flag}')
        # Only a hole at (0,0) is diagnostic.  Vanilla ships 2 enclosed holes at
        # arbitrary coords (WindhelmWorld 29,11 and KatariahWorld -11,28), so
        # holes in general are legal and must not fail the gate.
        print(f'worldspaces holed at (0,0): {n_holes}  (vanilla: 0)')

    if n_tele:
        print(f'\nFAIL: {n_tele} teleport destination(s) resolve to no cell — '
              f'CK passes the null on unchecked and dies with '
              f'EXCEPTION_ACCESS_VIOLATION reading 0x10.')
        return 1

    if gridless or n_holes:
        if gridless:
            print('\nFAIL: a gridless cell in a block/sub-block leaves the grid array '
                  'unallocated -> null-index CTD while streaming.')
        if n_holes:
            print('FAIL: a hole at (0,0) means a cell whose TES4 record omitted '
                  'XCLC never filled its slot -> null-index CTD while streaming.')
        return 1
    print('\nOK: every exterior-block cell carries XCLC.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
