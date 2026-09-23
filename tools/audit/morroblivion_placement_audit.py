"""Measure the placement corrections Morroblivion hand-applied to its own references.

Morroblivion shipped meshes on the wrong axis, re-seated others, and repointed
some records at a different object outright, then corrected every reference IT
placed.  A plugin converted against Morroblivion's meshes inherits none of that,
so those objects render on their side or floating.  This audit re-measures the
corrections and emits the tables `tes4_export/morroblivion_axis.py` ships.

Every figure is a DELTA -- Morroblivion's value minus Morrowind's, per
reference.  An absolute reading cannot tell a Morroblivion repair from rotation
Morrowind itself authored; a delta cancels the latter to zero.

The Morrowind side is read from the RAW TES3 file: its bases and its references
live inside the ESM, not in the export, which dumps neither.  Bases key on the
EditorID string, references on the 24-byte cell DATA subrecord.

Nothing is dropped silently: a candidate that misses the spike threshold is
reported under HELD with its histogram, so a missing correction is visible
rather than absent.

See: docs/audits/morroblivion_mesh_axis_rotation.md#the-correction
"""

import argparse
import math
import os
import re
import struct
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..')))

from output_layout import asset_root, record_dir
from tes4_export.tes3_reader import get_string, get_subrecord, read_file
from tes5_import.base.text_reader import parse_export_file

#: Record types that can own a model a reference places.
BASE_TYPES = ('LIGH', 'STAT', 'ACTI', 'CONT', 'DOOR', 'MISC', 'BOOK',
              'WEAP', 'ARMO', 'CLOT', 'ALCH', 'APPA', 'INGR', 'KEYM', 'FURN')

#: Reference types carrying a placement in a TES4 export.
REF_TYPES = ('REFR', 'ACHR', 'ACRE')

#: Rotation fields, in the order their correction is applied.
ROT_FIELDS = ('RotX', 'RotY', 'RotZ')

#: Extracted mesh tree under a plugin's export: what the plugin itself ships.
MESH_TREE = 'meshes'

#: Bytes of a TES3 reference DATA subrecord: three positions then three radians.
_TES3_PLACEMENT = 24

#: Horizontal distance within which two references are the same object.
MAX_XY = 12.0

#: Degrees within which two rotation deltas count as the same correction.
ROT_TOL = 8.0

#: Units within which two Z deltas count as the same re-seat.
Z_TOL = 0.6

#: Smallest Z delta worth recording.
MIN_DZ = 0.5

#: References a base needs before its correction is trusted.
MIN_REFS = 4

#: Share of references that must agree for a correction to be applied.
MIN_SHARE = 0.80

_STRIP = re.compile(r'[^a-z0-9]+')

#: Morroblivion prefixes a '0' and replaces '_' with 'U' in Morrowind EditorIDs.
_EDID_SEP = re.compile(r'[_u]')


def norm_edid(value: str) -> str:
    """Morroblivion's EditorID reduced to the Morrowind one it was mangled from.

    See: docs/audits/morroblivion_mesh_axis_rotation.md#pairing-the-bases
    """
    return _EDID_SEP.sub('', value.lower()).lstrip('0')


def norm_mesh(value: str) -> str:
    """A model path as an archive key: lower case, forward slashes, no leading one."""
    return (value or '').replace('\\\\', '/').replace(
        '\\', '/').strip().lstrip('/').lower()


def norm_name(value: str) -> str:
    """A cell's display name reduced to letters and digits."""
    return _STRIP.sub('', (value or '').lower())


def _records(export_root: str, plugin: str, sig: str):
    """Every record of one type from a plugin's export, or nothing."""
    path = os.path.join(record_dir(export_root, plugin), sig + '.txt')
    return parse_export_file(path) if os.path.isfile(path) else ()


def load_bases(export_root: str, plugin: str) -> dict:
    """{FormID low 6: (EditorID, mesh, signature)} over every base type."""
    out = {}
    for sig in BASE_TYPES:
        for rec in _records(export_root, plugin, sig):
            form_id = rec.get('FormID')
            if form_id:
                out[form_id[-6:].upper()] = (rec.get('EditorID', ''),
                                             norm_mesh(rec.get('Model.MODL')),
                                             sig)
    return out


def cell_keys(export_root: str, plugin: str) -> dict:
    """{cell FormID: key}; exteriors by grid, interiors by display name.

    See: docs/audits/morroblivion_mesh_axis_rotation.md#measure-the-delta
    """
    recs = list(_records(export_root, plugin, 'CELL'))
    grids = Counter(rec.get('ParentWRLD') for rec in recs
                    if rec.get('XCLC.X') is not None and rec.get('ParentWRLD'))
    main_world = grids.most_common(1)[0][0] if grids else None
    out = {}
    for rec in recs:
        form_id = rec.get('FormID')
        if not form_id:
            continue
        x, y = rec.get('XCLC.X'), rec.get('XCLC.Y')
        if x is not None and y is not None:
            world = rec.get('ParentWRLD')
            tag = '' if world in (main_world, None) else ':' + str(world)
            out[form_id] = 'g:%s,%s%s' % (x, y, tag)
        elif norm_name(rec.get('FULL')):
            out[form_id] = 'i:' + norm_name(rec.get('FULL'))
    return out


def placements(export_root: str, plugin: str, keep: set, cells: dict) -> dict:
    """{base low 6: {cell key: [(x, y, z, rotX, rotY, rotZ)]}} over every ref type."""
    out = defaultdict(lambda: defaultdict(list))
    for sig in REF_TYPES:
        for rec in _records(export_root, plugin, sig):
            name = rec.get('NAME')
            cell = cells.get(rec.get('ParentCELL') or '')
            if not name or not cell or name[-6:].upper() not in keep:
                continue
            try:
                row = (float(rec.get('PosX', 0)), float(rec.get('PosY', 0)),
                       float(rec.get('PosZ', 0))) + tuple(
                    math.degrees(float(rec.get(f, 0))) for f in ROT_FIELDS)
            except (TypeError, ValueError):
                continue
            out[name[-6:].upper()][cell].append(row)
    return out


def tes3_bases(records) -> dict:
    """{EditorID: mesh} for every TES3 base record carrying a model."""
    out = {}
    for rec in records:
        if rec.type not in BASE_TYPES or rec.deleted:
            continue
        sub = get_subrecord(rec, 'MODL')
        if sub is not None and rec.record_id:
            out[rec.record_id] = norm_mesh(get_string(sub))
    return out


def _tes3_cell_key(rec) -> str:
    """The grid or name key of one TES3 CELL, matching `cell_keys`."""
    name = get_string(rec.subrecords[0]) if rec.subrecords else ''
    data = get_subrecord(rec, 'DATA')
    if data is not None and len(data.data) >= 12:
        flags, x, y = struct.unpack('<3i', data.data[:12])
        if not flags & 0x01:
            return 'g:%d,%d' % (x, y)
    return 'i:' + norm_name(name) if name else ''


def _tes3_cell_refs(rec, keep: set):
    """(base EditorID, placement) for each reference in one TES3 cell.

    A reference is a NAME naming its base followed by the 24-byte DATA giving
    its position and rotation; the cell's own NAME and DATA are longer or come
    before the first FRMR.
    """
    base = None
    for sub in rec.subrecords:
        if sub.type == 'NAME':
            base = get_string(sub)
        elif sub.type == 'DATA' and len(sub.data) == _TES3_PLACEMENT \
                and base in keep:
            row = struct.unpack('<6f', sub.data)
            yield base, row[:3] + tuple(math.degrees(v) for v in row[3:])


def tes3_placements(records, keep: set) -> dict:
    """{base EditorID: {cell key: [(x, y, z, rotX, rotY, rotZ)]}} from the cells."""
    out = defaultdict(lambda: defaultdict(list))
    for rec in records:
        if rec.type != 'CELL' or rec.deleted:
            continue
        key = _tes3_cell_key(rec)
        for base, row in (_tes3_cell_refs(rec, keep) if key else ()):
            out[base][key].append(row)
    return out


def pair_xy(apos: list, bpos: list) -> list:
    """Nearest-neighbour (a, b) pairs by HORIZONTAL distance, each b used once.

    See: docs/audits/morroblivion_mesh_axis_rotation.md#measure-the-delta
    """
    used, pairs = set(), []
    for a in apos:
        best, index = None, -1
        for i, b in enumerate(bpos):
            if i in used:
                continue
            dist = math.hypot(a[0] - b[0], a[1] - b[1])
            if best is None or dist < best:
                best, index = dist, i
        if index >= 0 and best <= MAX_XY:
            used.add(index)
            pairs.append((a, bpos[index]))
    return pairs


def rot_spike(hist: Counter) -> tuple:
    """(degrees, share) of the largest cluster of rotation deltas, wrapping at 360."""
    total = sum(hist.values()) or 1
    best, value = 0, 0
    for candidate in hist:
        agree = sum(c for v, c in hist.items()
                    if min(abs(v - candidate), 360 - abs(v - candidate))
                    <= ROT_TOL)
        if agree > best:
            best, value = agree, candidate
    return value, best / total


def z_spike(hist: Counter) -> tuple:
    """(offset, share) of the largest cluster of Z deltas."""
    total = sum(hist.values()) or 1
    best, value = 0, 0.0
    for candidate in hist:
        agree = sum(c for v, c in hist.items() if abs(v - candidate) <= Z_TOL)
        if agree > best:
            best, value = agree, candidate
    return value, best / total


def pair_bases(mb_bases: dict, mw_meshes: dict) -> tuple:
    """({mb low 6: mw EditorID}, {mb low 6: (mesh, sig, edid, mw mesh)}, collisions).

    See: docs/audits/morroblivion_mesh_axis_rotation.md#pairing-the-bases
    """
    by_edid, collisions = {}, Counter()
    for edid, mesh in mw_meshes.items():
        key = norm_edid(edid)
        if key in by_edid:
            collisions[key] += 1
        by_edid.setdefault(key, (edid, mesh))
    pairs, meta = {}, {}
    for form_id, (edid, mesh, sig) in mb_bases.items():
        match = by_edid.get(norm_edid(edid)) if edid and mesh else None
        if match:
            pairs[form_id] = match[0]
            meta[form_id] = (mesh, sig, edid, match[1])
    return pairs, meta, collisions


def _ships_mesh(tree: str, mesh: str) -> bool:
    """Whether the converted plugin ships this mesh, rather than naming a stock one."""
    return os.path.isfile(os.path.join(tree, mesh.replace(chr(47), os.sep)))


def measure(export_root: str, source: str, target_esm: str) -> dict:
    """Rotation and Z delta histograms per mesh and per base.

    `source` is the converted plugin's export; `target_esm` the raw TES3 file
    holding the originals its references are measured against.
    """
    mb_bases = load_bases(export_root, source)
    records = read_file(target_esm)[1]
    pairs, meta, collisions = pair_bases(mb_bases, tes3_bases(records))
    mb = placements(export_root, source, set(pairs),
                    cell_keys(export_root, source))
    mw = tes3_placements(records, set(pairs.values()))
    tree = os.path.join(asset_root(export_root, source), MESH_TREE)

    rot = defaultdict(lambda: [Counter() for _ in ROT_FIELDS])
    dz, swaps, paired = defaultdict(Counter), {}, 0
    for low, mw_edid in pairs.items():
        here, there = mb.get(low), mw.get(mw_edid)
        mesh, sig, edid, mw_mesh = meta[low]
        if mw_mesh and not _ships_mesh(tree, mesh):
            swaps[mesh] = (mw_mesh, sig, swaps.get(mesh, ('', '', 0))[2] + 1)
        if not here or not there:
            continue
        for cell, apos in here.items():
            for pa, pb in pair_xy(apos, there.get(cell, [])):
                paired += 1
                for axis in range(len(ROT_FIELDS)):
                    rot[mesh][axis][round(pa[3 + axis] - pb[3 + axis]) % 360] += 1
                dz[(edid, mesh)][round(pa[2] - pb[2], 1)] += 1
    return {'rot': rot, 'dz': dz, 'swaps': swaps, 'paired': paired,
            'collisions': collisions, 'bases': len(pairs)}


def rot_rows(rot: dict) -> tuple:
    """(applied, held) rotation rows, each (mesh, axis, degrees, share, refs, hist)."""
    applied, held = [], []
    for mesh, axes in rot.items():
        for axis, hist in enumerate(axes):
            refs = sum(hist.values())
            degrees, share = rot_spike(hist)
            if refs < MIN_REFS or min(degrees, 360 - degrees) <= ROT_TOL:
                continue
            row = (mesh, ROT_FIELDS[axis], degrees, share, refs, hist)
            (applied if share >= MIN_SHARE else held).append(row)
    applied.sort(key=lambda r: (r[0], r[1]))
    held.sort(key=lambda r: -r[4])
    return applied, held


def z_rows(dz: dict) -> tuple:
    """(applied, held) Z rows, each (edid, mesh, offset, share, refs, hist)."""
    applied, held = [], []
    for (edid, mesh), hist in dz.items():
        refs = sum(hist.values())
        offset, share = z_spike(hist)
        if refs < MIN_REFS or abs(offset) < MIN_DZ:
            continue
        row = (edid, mesh, offset, share, refs, hist)
        (applied if share >= MIN_SHARE else held).append(row)
    applied.sort(key=lambda r: -abs(r[2]))
    held.sort(key=lambda r: -r[4])
    return applied, held


def _top(hist: Counter, fmt: str = '%s') -> str:
    """The four commonest histogram entries as 'value:count' pairs."""
    return ' '.join((fmt + ':%d') % (v, c) for v, c in hist.most_common(4))


def _write_rotation(fh, applied: list, held: list) -> None:
    """The rotation section: corrections to apply, then those held back."""
    fh.write('ROTATION -- applied (>=%d refs, >=%d%% agreeing)\n'
             % (MIN_REFS, MIN_SHARE * 100))
    for rows, title in ((applied, None),
                        (held, 'ROTATION -- held (below the spike threshold)')):
        if title:
            fh.write('\n%s\n' % title)
        for mesh, axis, deg, share, refs, hist in rows:
            fh.write('%5d %4.0f%% %-4s %6d  %-52s %s\n'
                     % (refs, share * 100, axis, deg, mesh, _top(hist, '%d')))


def _write_reseat(fh, applied: list, held: list) -> None:
    """The Z re-seat section: offsets to apply, then those held back."""
    fh.write('\n\nZ RE-SEAT -- applied, keyed on the base\n')
    for rows, title in ((applied, None),
                        (held, 'Z RE-SEAT -- held (no single offset fits)')):
        if title:
            fh.write('\n%s\n' % title)
        for edid, mesh, offset, share, refs, hist in rows:
            fh.write('%5d %4.0f%% %9.2f  %-34s %-40s %s\n'
                     % (refs, share * 100, offset, edid[:34], mesh,
                        _top(hist, '%.1f')))


def _write_swaps(fh, swaps: dict) -> None:
    """Every base Morroblivion repointed at a mesh outside its own tree."""
    fh.write('\n\nMESH SWAPS -- Morroblivion pointed the record at a stock '
             'Oblivion mesh\n')
    fh.write('The object is wrong, not its height: skip the substitution so '
             'the Morrowind mesh converts instead.\n')
    for mesh, (mw_mesh, sig, bases) in sorted(swaps.items(),
                                              key=lambda kv: -kv[1][2]):
        fh.write('%5d bases  %-6s %-40s <- %s\n' % (bases, sig, mesh, mw_mesh))
    fh.write('\nswapped meshes: %d over %d bases\n'
             % (len(swaps), sum(v[2] for v in swaps.values())))


def write_report(path: str, data: dict) -> None:
    """The full audit: applied corrections, held candidates, and mesh swaps."""
    applied_rot, held_rot = rot_rows(data['rot'])
    applied_z, held_z = z_rows(data['dz'])
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('Morroblivion placement corrections, measured as deltas\n')
        fh.write('%d bases paired, %d references paired on XY\n'
                 % (data['bases'], data['paired']))
        if data['collisions']:
            fh.write('EditorID collisions (pairing kept the first): %d\n'
                     % len(data['collisions']))
        fh.write('=' * 104 + '\n\n')
        _write_rotation(fh, applied_rot, held_rot)
        _write_reseat(fh, applied_z, held_z)
        _write_swaps(fh, data['swaps'])


def write_tables(path: str, data: dict) -> None:
    """The applied corrections as the literals `morroblivion_axis` ships."""
    applied_rot, _held = rot_rows(data['rot'])
    applied_z, _held_z = z_rows(data['dz'])
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('AXIS_ROTATION_DEG = {\n')
        for mesh, axis, deg, share, refs, _hist in applied_rot:
            fh.write("    ('%s', '%s'): %d,   # %d refs, %.0f%%\n"
                     % (mesh, axis, deg, refs, share * 100))
        fh.write('}\n\nZ_RESEAT = {\n')
        for edid, _mesh, offset, share, refs, _hist in applied_z:
            fh.write("    '%s': %.2f,   # %d refs, %.0f%%\n"
                     % (edid.lower(), offset, refs, share * 100))
        fh.write('}\n\nSTOCK_MESH_SWAPS = {\n')
        for mesh, (mw_mesh, _sig, bases) in sorted(data['swaps'].items()):
            fh.write("    '%s': '%s',   # %d bases\n" % (mesh, mw_mesh, bases))
        fh.write('}\n')


def main() -> None:
    """Measure one plugin's corrections against the originals it converted."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--export-root', default='export')
    ap.add_argument('--source', default='Morrowind_ob.esm',
                    help='export of the plugin holding the corrected references')
    ap.add_argument('--target', required=True,
                    help='raw TES3 ESM holding the originals to measure against')
    ap.add_argument('--out',
                    default='docs/audits/morroblivion_placement_results.txt')
    ap.add_argument('--tables', help='also write the tables as Python literals')
    args = ap.parse_args()

    data = measure(args.export_root, args.source, args.target)
    sys.stderr.write('paired %d bases, %d references\n'
                     % (data['bases'], data['paired']))
    write_report(args.out, data)
    sys.stderr.write('wrote %s\n' % args.out)
    if args.tables:
        write_tables(args.tables, data)
        sys.stderr.write('wrote %s\n' % args.tables)


if __name__ == '__main__':
    main()
