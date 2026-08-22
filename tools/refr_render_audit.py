#!/usr/bin/env python3
"""Placed references the GAME will not draw but the Creation Kit will.

WHY THIS EXISTS -- "it loads fine in the CK, but objects are missing in game".

The CK draws a reference straight from the record: it loads a whole worldspace
at once and ignores enable state, enable parents and the cell-streaming window.
The game draws one only if it *also* survives all of those. So a plugin can be
structurally clean -- every gate in cell_grid_check, esm_group_anchors,
float_sanity_check and verify_ck_fixes green -- render fully populated in the
editor, and still be missing objects the moment you walk into the same cell.

This audit judges the record side of that gap, per reference:

  disabled        RecordFlags & 0x800 (Initially Disabled) and no enable parent
                  -- the game never draws it until a script enables it.
  enable_parent   carries XESP; drawn only when its parent is enabled (opposite
                  state if the XESP flag says so).  Reported, not condemned.
  dangling_parent XESP names a FormID nothing in the load order defines.
  zero_scale      XSCL below --min-scale: present in the record, invisible.
  no_base         NAME resolves to no record we can read.
  base_no_model   the base is a drawable type carrying no model path.
  bucket_mismatch the Persistent flag disagrees with the children sub-group the
                  reference was written into (8 = persistent, 9 = temporary).
                  The engine buckets by group; the CK is happy either way.

MESH-FILE absence is deliberately NOT judged here -- tools/missing_mesh_refs.py
owns that question and consults the BSAs as well as the loose tree.

Usage:
    python tools/refr_render_audit.py --plugin Oblivion.esm
    python tools/refr_render_audit.py --plugin Oblivion.esm --region -10 10 -10 10
    python tools/refr_render_audit.py --plugin Oblivion.esm --cell 13,21
    python tools/refr_render_audit.py --plugin Oblivion.esm --show bucket_mismatch

Exit code is 1 when any condemning class (disabled, dangling_parent,
zero_scale, no_base, bucket_mismatch) is non-empty, so it can gate a build.
"""

import argparse
import os
import struct
import sys
import zlib
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Reference RecordFlags. 0x400/0x800 are read back out of the pipeline's own
# writers (tes5_import/import_main.py uses 0x400 for Persistent throughout).
FLAG_PERSISTENT = 0x00000400
FLAG_DISABLED = 0x00000800

# Base types that are supposed to carry a model.  A reference to one of these
# with no model path draws nothing; a reference to anything else (map markers,
# sound markers, triggers) is invisible by design and must not be reported.
DRAWABLE = {
    'STAT', 'SCOL', 'MSTT', 'TREE', 'FLOR', 'FURN', 'ACTI', 'TACT', 'CONT',
    'DOOR', 'MISC', 'WEAP', 'ARMO', 'AMMO', 'INGR', 'ALCH', 'BOOK',
    'KEYM', 'SLGM', 'APPA', 'GRAS',
}

# LIGH is deliberately absent from DRAWABLE: a light is a pure emitter and
# carries no mesh in either game (20,665 of Oblivion.esm's placements are
# modelless lights, every one of them correct).

# Types whose reference is a marker/emitter: never condemned for having no
# model.  Kept explicit so a new invisible type does not silently join
# base_no_model.
INVISIBLE_OK = {'LVLI', 'LVLN', 'LVSP', 'NPC_', 'PACK', 'IDLM', 'ASPC', 'SOUN'}

_MODEL_SUB = {'ARMO': b'MOD2', 'ARMA': b'MOD2'}


def _subs(body):
    """signature -> payload (last wins, which is all these checks need)."""
    out, p, n = {}, 0, len(body)
    while p + 6 <= n:
        sig = body[p:p + 4]
        size = struct.unpack_from('<H', body, p + 4)[0]
        out[sig] = body[p + 6:p + 6 + size]
        p += 6 + size
    return out


def _decompress(body, flags):
    if flags & 0x00040000:
        try:
            return zlib.decompress(body[4:])
        except zlib.error:
            return b''
    return body


def _masters(path):
    with open(path, 'rb') as fh:
        head = fh.read(24)
        if len(head) < 24 or head[:4] != b'TES4':
            return []
        body = fh.read(struct.unpack_from('<I', head, 4)[0])
    out, pos = [], 0
    while pos + 6 <= len(body):
        sig = body[pos:pos + 4]
        size = struct.unpack_from('<H', body, pos + 4)[0]
        pos += 6
        if sig == b'MAST':
            out.append(body[pos:pos + size].rstrip(b'\0').decode('latin-1'))
        pos += size
    return out


def _records(path):
    """fid -> (signature, editorid, has_model)."""
    data = open(path, 'rb').read()
    out = {}
    p, n = 0, len(data)
    while p + 24 <= n:
        if data[p:p + 4] == b'GRUP':
            p += 24
            continue
        sig = data[p:p + 4]
        size, flags, fid = struct.unpack_from('<III', data, p + 4)
        body = _decompress(data[p + 24:p + 24 + size], flags)
        s = _subs(body)
        ssig = sig.decode('latin-1')
        edid = s.get(b'EDID', b'').split(bytes(1))[0].decode('latin-1') or None
        raw = s.get(_MODEL_SUB.get(ssig, b'MODL'), b'')
        modl = raw.split(bytes(1))[0].decode('latin-1')
        has_model = bool(modl) and '.' in modl and all(
            32 <= ord(c) < 127 for c in modl)
        out[fid] = (ssig, edid, has_model)
        p += 24 + size
    return out


def _walk(path):
    """Yield (cell_fid, gridpos_or_None, group_type, refr_dict) per placement."""
    data = open(path, 'rb').read()
    cellpos = {}
    found = []

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
            body = _decompress(data[p + 24:p + 24 + size], flags)
            if sig == b'CELL':
                xclc = _subs(body).get(b'XCLC')
                if xclc and len(xclc) >= 8:
                    cellpos[fid] = struct.unpack_from('<ii', xclc, 0)
            elif sig in (b'REFR', b'ACHR', b'ACRE'):
                cell = next((l for t, l in reversed(stack) if t == 6), None)
                gtype = next((t for t, _ in reversed(stack)
                              if t in (8, 9, 10)), None)
                found.append((cell, gtype, fid, flags, _subs(body)))
            p += 24 + size

    walk(0, len(data), [])
    for cell, gtype, fid, flags, s in found:
        yield cell, cellpos.get(cell), gtype, fid, flags, s


def _source_census(export_dir):
    """Authored counts from the TES4 export dump, or None if it is absent.

    The absolute number of disabled/tiny-scale references means NOTHING on its
    own: Oblivion.esm authors 1,007 disabled-with-no-parent placements and 12
    sub-0.01 scales, and a faithful conversion reproduces every one.  Only a
    DELTA against the authored data is a defect, so the audit compares rather
    than condemns whenever the export is available.
    """
    counts = Counter()
    seen = False
    for fn in ('REFR.txt', 'ACHR.txt', 'ACRE.txt'):
        fp = os.path.join(export_dir, fn)
        if not os.path.isfile(fp):
            continue
        seen = True
        flags, has_xesp, tiny, in_rec = 0, False, False, False
        with open(fp, 'r', encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.rstrip(chr(10))
                if line == '---RECORD_BEGIN---':
                    in_rec, flags, has_xesp, tiny = True, 0, False, False
                    counts['placements'] += 1
                elif line == '---RECORD_END---':
                    if has_xesp:
                        counts['enable_parent'] += 1
                    elif flags & FLAG_DISABLED:
                        counts['disabled'] += 1
                    if tiny:
                        counts['zero_scale'] += 1
                    in_rec = False
                elif in_rec:
                    if line.startswith('RecordFlags='):
                        flags = int(line.split('=', 1)[1])
                    elif line.startswith('XESP'):
                        has_xesp = True
                    elif line.startswith('XSCL.Scale='):
                        try:
                            tiny = float(line.split('=', 1)[1]) < 0.01
                        except ValueError:
                            pass
    return counts if seen else None


def _fmt(fid, sig, edid, cell, pos):
    where = '' if pos is None else '  cell %d,%d' % pos
    return '    %08X %s %s%s' % (fid, sig, edid or '-', where)


def _inventory(args, path, lookup):
    """Every placement in scope, with its mesh and whether that mesh ships.

    The field workflow for "object X is missing in game but the CK has it":
    stand in the cell, run this, and the line whose mesh is marked MISSING --
    or whose object you cannot see while the row says the mesh SHIPS -- names
    the reference to chase.  Presence is answered the way the engine answers
    it, loose file OR archive.
    """
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    from missing_mesh_refs import _records as _model_records
    from missing_mesh_refs import _bsa_meshes
    from output_layout import paths as _paths

    out = _paths(args.plugin, out_root=args.output_dir).out
    models = _model_records(path)
    bsa = _bsa_meshes(str(out))
    mroot = str(out / 'meshes')

    x0, x1, y0, y1 = args.region
    rows = []
    for cell, pos, gtype, fid, rflags, sub in _walk(path):
        if pos is None or not (x0 <= pos[0] <= x1 and y0 <= pos[1] <= y1):
            continue
        name = sub.get(b'NAME')
        base = struct.unpack_from('<I', name, 0)[0] if name and len(
            name) >= 4 else None
        rec = lookup(base) if base is not None else None
        sig, edid = (rec[0], rec[1]) if rec else ('?', None)
        modl = None
        mrec = models.get(base)
        if mrec:
            modl = mrec[2]
        if modl:
            rel = modl.replace(chr(92), os.sep).replace('/', os.sep)
            loose = os.path.isfile(os.path.join(mroot, rel))
            packed = ('meshes' + chr(92) +
                      modl.replace('/', chr(92))).lower() in bsa
            mark = 'loose' if loose else ('bsa' if packed else 'MISSING')
        else:
            mark = '-'
        flagbits = []
        if rflags & FLAG_DISABLED:
            flagbits.append('disabled')
        if rflags & FLAG_PERSISTENT:
            flagbits.append('persist')
        if sub.get(b'XESP'):
            flagbits.append('parented')
        rows.append((pos, fid, sig, edid or '-', mark, modl or '',
                     ','.join(flagbits)))

    rows.sort(key=lambda r: (r[0], r[2], r[3]))
    print()
    print('inventory -- %d placement(s) in scope' % len(rows))
    print('  %-9s %-8s %-5s %-30s %-8s %s'
          % ('cell', 'formid', 'type', 'editorid', 'mesh', 'flags'))
    for pos, fid, sig, edid, mark, modl, flags in rows[:args.max]:
        print('  %-9s %08X %-5s %-30s %-8s %s'
              % ('%d,%d' % pos, fid, sig, edid[:30], mark, flags))
        if modl:
            print('        %s' % modl)
    if len(rows) > args.max:
        print('  ... %d more (raise --max)' % (len(rows) - args.max))


def main():
    ap = argparse.ArgumentParser(
        description='Placed refs the game will not draw but the CK will')
    ap.add_argument('--plugin', required=True)
    ap.add_argument('--output-dir', default=os.path.join(ROOT, 'output'))
    ap.add_argument('--region', nargs=4, type=int,
                    metavar=('XMIN', 'XMAX', 'YMIN', 'YMAX'),
                    help='only exterior cells in this grid box')
    ap.add_argument('--cell', help='single exterior cell as "X,Y"')
    ap.add_argument('--min-scale', type=float, default=0.01)
    ap.add_argument('--export-dir', default=None,
                    help='TES4 export dump to diff against '
                         '(default: export/<plugin>)')
    ap.add_argument('--show', help='list every hit in this class')
    ap.add_argument('--inventory', action='store_true',
                    help='list EVERY placement in scope with its mesh '
                         'and whether that mesh ships (needs --cell '
                         'or --region)')
    ap.add_argument('--max', type=int, default=15)
    args = ap.parse_args()

    # Asset paths and EditorIDs carry Windows-1252 bytes; a cp1251/cp866
    # console would raise mid-report and throw away the findings.
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from output_layout import paths as _paths
    path = str(_paths(args.plugin, out_root=args.output_dir).esm)
    if not os.path.isfile(path):
        print('ERROR: no converted plugin at %s' % path)
        return 1

    masters = _masters(path)
    # Index byte -> record table, exactly as the engine routes it.  A vanilla
    # master we never convert has no output; its FormIDs are reported unchecked
    # rather than invented as dangling.
    tables, unchecked_slots = {}, set()
    for slot, name in enumerate(masters):
        mp = str(_paths(name, out_root=args.output_dir).esm)
        if os.path.isfile(mp):
            tables[slot] = _records(mp)
        else:
            unchecked_slots.add(slot)
    tables[len(masters)] = _records(path)

    def lookup(fid):
        return tables.get((fid >> 24) & 0xFF, {}).get(fid)

    def readable(fid):
        return ((fid >> 24) & 0xFF) not in unchecked_slots

    if args.cell:
        cx, cy = (int(v) for v in args.cell.replace(' ', '').split(','))
        args.region = [cx, cx, cy, cy]

    hits = defaultdict(list)
    by_base = defaultdict(Counter)
    total = 0
    unchecked = 0

    for cell, pos, gtype, fid, rflags, s in _walk(path):
        if args.region:
            x0, x1, y0, y1 = args.region
            if pos is None or not (x0 <= pos[0] <= x1 and y0 <= pos[1] <= y1):
                continue
        total += 1

        name = s.get(b'NAME')
        base = struct.unpack_from('<I', name, 0)[0] if name and len(
            name) >= 4 else None
        rec = lookup(base) if base is not None else None
        sig, edid, has_model = rec if rec else ('?', None, False)
        row = (fid, sig, edid, cell, pos)

        if base is not None and rec is None and readable(base):
            hits['no_base'].append(row)
        elif base is not None and rec is None:
            unchecked += 1
        elif rec and sig in DRAWABLE and not has_model:
            hits['base_no_model'].append(row)
            by_base['base_no_model'][edid or '%08X' % base] += 1

        xesp = s.get(b'XESP')
        if xesp and len(xesp) >= 4:
            parent = struct.unpack_from('<I', xesp, 0)[0]
            if lookup(parent) is None and readable(parent):
                hits['dangling_parent'].append(row)
            else:
                hits['enable_parent'].append(row)
        elif rflags & FLAG_DISABLED:
            # Disabled WITH an enable parent is the normal authored pattern;
            # disabled WITHOUT one means nothing can ever turn it on.
            hits['disabled'].append(row)
            by_base['disabled'][edid or '?'] += 1

        xscl = s.get(b'XSCL')
        if xscl and len(xscl) >= 4:
            scale = struct.unpack_from('<f', xscl, 0)[0]
            if scale < args.min_scale:
                hits['zero_scale'].append(row)

        # The engine buckets a reference by the children sub-group it was
        # written into; the flag must agree or the two disagree about which
        # list the reference lives on.
        if gtype in (8, 9):
            persistent = bool(rflags & FLAG_PERSISTENT)
            if persistent != (gtype == 8):
                hits['bucket_mismatch'].append(row)
                by_base['bucket_mismatch'][
                    'flag=%d group=%d' % (persistent, gtype)] += 1

    exp = args.export_dir
    if exp is None:
        cand = os.path.join(ROOT, 'export', args.plugin)
        exp = cand if os.path.isdir(cand) else None
    source = _source_census(exp) if exp else None
    # A whole-plugin scope is the only one comparable to a whole-plugin census.
    if args.region:
        source = None

    scope = 'whole plugin'
    if args.region:
        scope = 'X %d..%d  Y %d..%d' % tuple(args.region)
    print('plugin: %s   scope: %s' % (args.plugin, scope))
    print('placements examined: %d' % total)
    if unchecked:
        print('bases in an unconverted master (not judged): %d' % unchecked)
    print()

    order = ('disabled', 'bucket_mismatch', 'no_base', 'base_no_model',
             'zero_scale', 'dangling_parent', 'enable_parent')
    # ALWAYS invariants: nothing in the authored data can justify a reference
    # whose base or enable parent does not exist, or whose flag contradicts the
    # group it lives in.
    absolute = ('bucket_mismatch', 'no_base', 'dangling_parent')
    failed = False
    if source is not None:
        print('diffing against authored export: %s' % exp)
        print('  %-16s %8s %8s %8s' % ('class', 'output', 'authored', 'delta'))
    for cls in order:
        rows = hits.get(cls, [])
        if source is not None and cls in source or (
                source is not None and cls in ('disabled', 'zero_scale',
                                               'enable_parent')):
            want = source.get(cls, 0)
            delta = len(rows) - want
            bad = delta != 0 or (cls in absolute and rows)
            failed = failed or bad
            print('  %s  %-16s %8d %8d %+8d'
                  % ('FAIL' if bad else 'ok  ', cls, len(rows), want, delta))
            continue
        mark = 'FAIL' if (rows and cls in absolute) else ('note' if rows
                                                          else 'ok  ')
        failed = failed or (rows and cls in absolute)
        print('  %s  %-16s %d' % (mark, cls, len(rows)))
        for what, n in by_base.get(cls, Counter()).most_common(3):
            print('           %-40s x%d' % (what, n))
    if source is None:
        print()
        print('  NOTE: no export dump to diff against, so disabled /')
        print('        zero_scale / enable_parent counts are reported, not')
        print('        judged -- every one of them can be authored.')
        print('        Point --export-dir at the TES4 export to gate them.')

    if args.show:
        rows = hits.get(args.show, [])
        print()
        print('%s -- %d hit(s):' % (args.show, len(rows)))
        for row in rows[:args.max]:
            print(_fmt(*row))
        if len(rows) > args.max:
            print('    ... %d more (raise --max)' % (len(rows) - args.max))

    if args.inventory:
        if not args.region:
            print()
            print('--inventory needs --cell X,Y or --region (a whole-plugin')
            print('listing would be a million lines).')
            return 2
        _inventory(args, path, lookup)

    print()
    print('Mesh files are not judged here -- run:')
    print('  python tools/missing_mesh_refs.py --plugin %s' % args.plugin)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
