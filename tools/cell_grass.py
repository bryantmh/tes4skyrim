"""List the grass types (and their models) a given exterior cell can spawn.

Walks a TES4 export directory: CELL.txt (grid coords) -> LAND.txt (texture
layers per cell) -> LTEX.txt (GNAM grass links) -> GRAS.txt (model paths).
Useful for narrowing down which grass NIF is responsible for a
location-specific crash or visual problem.

Usage:
    python tools/cell_grass.py <export_dir> --wrld <FormID_or_EditorID> \
        --cell X,Y [--cell X,Y ...]
    python tools/cell_grass.py export/Oblivion.esm --wrld Tamriel --cell 20,20 --cell 10,10
"""
import argparse
from collections import defaultdict


def read_records(path, wanted_keys):
    """Light KEY=VALUE record reader: yields dicts of only the wanted keys
    (prefix match for indexed keys like Layer[)."""
    rec = {}
    try:
        f = open(path, encoding='utf-8')
    except OSError:
        return
    with f:
        for line in f:
            line = line.rstrip('\n')
            if line.startswith('---RECORD_BEGIN'):
                rec = {}
            elif line.startswith('---RECORD_END'):
                yield rec
            elif '=' in line and not line.startswith('#'):
                key, val = line.split('=', 1)
                if any(key == w or key.startswith(w) for w in wanted_keys):
                    rec[key] = val


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('export_dir')
    ap.add_argument('--wrld', default='Tamriel',
                    help='Worldspace FormID or EditorID (default Tamriel)')
    ap.add_argument('--cell', action='append', required=True,
                    help='Grid coordinate X,Y (repeatable)')
    args = ap.parse_args()

    # Resolve worldspace
    wrld_fid = None
    for rec in read_records(f'{args.export_dir}/WRLD.txt', ('FormID', 'EditorID')):
        if rec.get('EditorID') == args.wrld or rec.get('FormID') == args.wrld.upper():
            wrld_fid = rec['FormID']
            break
    if not wrld_fid:
        print(f"Worldspace {args.wrld!r} not found")
        return 1

    targets = set()
    for c in args.cell:
        x, y = c.split(',')
        targets.add((int(x), int(y)))

    # Cell FormIDs at the target grid coords
    cell_fids = {}
    for rec in read_records(f'{args.export_dir}/CELL.txt',
                            ('FormID', 'ParentWRLD', 'XCLC.X', 'XCLC.Y')):
        if rec.get('ParentWRLD') != wrld_fid or 'XCLC.X' not in rec:
            continue
        coord = (int(rec['XCLC.X']), int(rec['XCLC.Y']))
        if coord in targets:
            cell_fids[rec['FormID']] = coord

    # LAND layers for those cells
    cell_ltex = defaultdict(set)   # coord -> LTEX fids
    for rec in read_records(f'{args.export_dir}/LAND.txt',
                            ('ParentCELL', 'Layer[')):
        coord = cell_fids.get(rec.get('ParentCELL'))
        if not coord:
            continue
        for key, val in rec.items():
            if key.endswith('.BTXT.Texture') or key.endswith('.ATXT.Texture'):
                cell_ltex[coord].add(val)

    # LTEX -> grass fids
    ltex_grass = {}
    ltex_edid = {}
    for rec in read_records(f'{args.export_dir}/LTEX.txt',
                            ('FormID', 'EditorID', 'GrassCount', 'Grass[')):
        fids = [v for k, v in rec.items() if k.startswith('Grass[')]
        ltex_grass[rec.get('FormID')] = fids
        ltex_edid[rec.get('FormID')] = rec.get('EditorID', '?')

    # GRAS -> model
    gras = {}
    for rec in read_records(f'{args.export_dir}/GRAS.txt',
                            ('FormID', 'EditorID', 'Model.MODL')):
        gras[rec.get('FormID')] = (rec.get('EditorID', '?'),
                                   rec.get('Model.MODL', '?').replace('\\\\', '\\'))

    for coord in sorted(targets):
        print(f"\n=== Cell ({coord[0]}, {coord[1]}) ===")
        if coord not in cell_ltex:
            print("  (no LAND layers found)")
            continue
        grass_here = {}
        for lt in sorted(cell_ltex[coord]):
            gfids = ltex_grass.get(lt, [])
            if gfids:
                print(f"  LTEX {lt} {ltex_edid.get(lt)}: {len(gfids)} grass")
                for g in gfids:
                    edid, modl = gras.get(g, ('?', '?'))
                    grass_here[g] = (edid, modl)
        print(f"  -> {len(grass_here)} distinct grass types:")
        for g, (edid, modl) in sorted(grass_here.items()):
            print(f"     {g} {edid}: {modl}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
