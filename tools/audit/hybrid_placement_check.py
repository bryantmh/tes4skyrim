"""Check that every whitelisted world mesh sits where Morroblivion placed it.

Hybrid mode puts a Morroblivion mesh at the authored Morrowind placement,
plus Morroblivion's measured corrections and the origin shift. Morroblivion's
own placement of the same reference is where that mesh was authored to sit, so
each hybrid reference is paired with Morroblivion's (same cell, nearest
horizontally) and the height and rotation deltas are compared per mesh. A mesh
whose median height delta or rotation delta is off is reported, worst first.

See: docs/commentary/tes4_export_morrowind.md#hybrid-placement-check
"""

import argparse
import math
import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..')))

from output_layout import record_dir
from tes4_export.morroblivion_hybrid import load_whitelist
from tes5_import.base.text_reader import parse_export_file
from tools.audit.morroblivion_placement_audit import (BASE_TYPES, REF_TYPES, ROT_FIELDS, cell_keys,
                                                      norm_mesh, pair_xy)

#: A median height delta past this many units is reported.
MAX_DZ = 1.0

#: A median rotation delta past this many degrees, on any axis, is reported.
MAX_ROT = 5.0


def _records(export_root: str, plugin: str, sig: str):
    """Every record of one type from a plugin's export, or nothing."""
    path = os.path.join(record_dir(export_root, plugin), sig + '.txt')
    return parse_export_file(path) if os.path.isfile(path) else ()


def bases(export_root: str, plugin: str, meshes: set) -> dict:
    """{base FormID low 6: (mesh, origin shift)} of every base wearing one of `meshes`."""
    out = {}
    for sig in BASE_TYPES:
        for rec in _records(export_root, plugin, sig):
            mesh = norm_mesh(rec.get('Model.MODL'))
            if mesh in meshes and rec.get('FormID'):
                out[rec['FormID'][-6:].upper()] = (mesh, float(rec.get('Model.OriginShift') or 0))
    return out


def placed(export_root: str, plugin: str, base_meshes: dict) -> dict:
    """{mesh: {cell key: [(x, y, z, rotX, rotY, rotZ)]}}, z lowered by each base's origin shift times scale."""
    cells = cell_keys(export_root, plugin)
    out = defaultdict(lambda: defaultdict(list))
    for sig in REF_TYPES:
        for rec in _records(export_root, plugin, sig):
            base = base_meshes.get((rec.get('NAME') or '')[-6:].upper())
            cell = cells.get(rec.get('ParentCELL') or '')
            if base is None or not cell:
                continue
            mesh, shift = base
            rot = [float(rec.get(f) or 0) for f in ROT_FIELDS]
            lift = shift * float(rec.get('XSCL.Scale') or 1) * math.cos(rot[0]) * math.cos(rot[1])
            out[mesh][cell].append((float(rec.get('PosX') or 0), float(rec.get('PosY') or 0),
                                    float(rec.get('PosZ') or 0) - lift) + tuple(map(math.degrees, rot)))
    return out


def _wrapped(delta: float) -> float:
    """A rotation delta in degrees folded into [0, 180]."""
    delta = abs(delta) % 360
    return min(delta, 360 - delta)


def compare(hybrid: dict, morroblivion: dict) -> list:
    """[(mesh, pairs, median dz, worst median rotation delta)] per mesh both place."""
    rows = []
    for mesh, cells in hybrid.items():
        dz, rot = [], [[] for _ in ROT_FIELDS]
        for cell, ours in cells.items():
            for a, b in pair_xy(ours, morroblivion.get(mesh, {}).get(cell, [])):
                dz.append(a[2] - b[2])
                for axis in range(len(ROT_FIELDS)):
                    rot[axis].append(_wrapped(a[3 + axis] - b[3 + axis]))
        if dz:
            rows.append((mesh, len(dz), statistics.median(dz), max(statistics.median(r) for r in rot)))
    return rows


def main() -> None:
    """Pair, compare and report."""
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--export-root', default='export')
    parser.add_argument('--plugin', default='Morrowind.esm', help='a plugin exported in hybrid mode')
    parser.add_argument('--source', default='Morrowind_ob.esm', help="Morroblivion's export")
    args = parser.parse_args()
    world, _worn = load_whitelist()
    meshes = {norm_mesh(m) for m in world.values() if m}
    ours = placed(args.export_root, args.plugin, bases(args.export_root, args.plugin, meshes))
    theirs = placed(args.export_root, args.source, {k: (m, 0.0) for k, (m, _s) in
                                                    bases(args.export_root, args.source, meshes).items()})
    rows = compare(ours, theirs)
    bad = sorted((r for r in rows if abs(r[2]) > MAX_DZ or r[3] > MAX_ROT), key=lambda r: -abs(r[2]) - r[3])
    print(f'{len(meshes)} whitelisted world meshes; {len(ours)} placed by {args.plugin}; '
          f'{len(rows)} paired with {args.source}; {len(bad)} off')
    for mesh, pairs, dz, rot in bad:
        print(f'  {mesh:60s} refs {pairs:5d}  dz {dz:+8.2f}  rot {rot:6.1f}')


if __name__ == '__main__':
    main()
