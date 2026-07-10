#!/usr/bin/env python3
"""Dump NiSkinInstance / NiSkinData / NiSkinPartition details for NIF files.

Prints per-partition bone/vertex/triangle counts plus consistency checks that
mirror what the engine assumes at render time (the checks that, when violated,
produce vmovdqa/memcpy access violations in BSBatchRenderer):

  - partition bone indices within NiSkinInstance bone array
  - vertex map indices within geometry vertex count
  - triangle indices within partition vertex count
  - bones-per-partition GPU palette limits
  - union of partition vertices vs geometry vertex count

Usage:
    python tools/skin_partition_dump.py <nif> [<nif> ...]
    python tools/skin_partition_dump.py <dir>          # scans *.nif recursively
"""

import sys
import os
import time
from pathlib import Path

if not hasattr(time, 'clock'):
    time.clock = time.perf_counter

from pyffi.formats.nif import NifFormat

# GPU bone-palette limits: Oblivion-era hardware allowed more, but Skyrim
# (LE and SSE) cap skinned draw calls at 80 bones per partition; vanilla
# assets stay well under (typically <= 60).
SKYRIM_MAX_BONES_PER_PARTITION = 80


def dump_nif(path: str) -> int:
    """Print skin info for one NIF. Returns number of problems found."""
    problems = 0
    data = NifFormat.Data()
    with open(path, 'rb') as f:
        data.read(f)

    print(f'\n=== {path}')
    print(f'    Version: 0x{data.version:08X} UserVer: {data.user_version} '
          f'UV2: {getattr(data, "user_version_2", "?")}')

    found = False
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not isinstance(block, (NifFormat.NiTriShape,
                                      NifFormat.NiTriStrips,
                                      getattr(NifFormat, 'BSTriShape', ()))):
                continue
            skin = getattr(block, 'skin_instance', None)
            if skin is None:
                continue
            found = True
            name = bytes(block.name).rstrip(b'\x00').decode('latin-1', 'replace')
            geom_data = getattr(block, 'data', None)
            n_geom_verts = getattr(geom_data, 'num_vertices', 0) if geom_data else 0
            n_skin_bones = skin.num_bones
            print(f'  Geometry "{name}" ({block.__class__.__name__}) '
                  f'verts={n_geom_verts} skin={skin.__class__.__name__} '
                  f'bones={n_skin_bones}')

            if n_skin_bones > SKYRIM_MAX_BONES_PER_PARTITION:
                print(f'    !! {n_skin_bones} total skin bones exceeds the SSE '
                      f'per-shape renderer cap of '
                      f'{SKYRIM_MAX_BONES_PER_PARTITION} (CTD on render)')
                problems += 1

            sd = skin.data
            if sd is not None and sd.num_bones != n_skin_bones:
                print(f'    !! NiSkinData bone count {sd.num_bones} != '
                      f'NiSkinInstance bone count {n_skin_bones}')
                problems += 1

            sp = skin.skin_partition
            if sp is None and sd is not None:
                sp = getattr(sd, 'skin_partition', None)
            if sp is None:
                print('    (no NiSkinPartition)')
                continue

            covered = set()
            for pi in range(sp.num_skin_partition_blocks):
                p = sp.skin_partition_blocks[pi]
                nb = p.num_bones
                nv = p.num_vertices
                nt = p.num_triangles
                ns = p.num_strips
                nw = p.num_weights_per_vertex
                has_map = p.has_vertex_map or p.num_vertices == len(p.vertex_map)
                print(f'    Partition[{pi}]: bones={nb} verts={nv} tris={nt} '
                      f'strips={ns} weightsPerVert={nw} '
                      f'hasVertMap={bool(p.has_vertex_map)} '
                      f'hasVertWeights={bool(p.has_vertex_weights)} '
                      f'hasBoneIndices={bool(p.has_bone_indices)}')

                if nb > SKYRIM_MAX_BONES_PER_PARTITION:
                    print(f'      !! {nb} bones exceeds Skyrim per-partition '
                          f'limit of {SKYRIM_MAX_BONES_PER_PARTITION}')
                    problems += 1
                bad_bone = [b for b in p.bones if b >= n_skin_bones]
                if bad_bone:
                    print(f'      !! bone indices out of range: {bad_bone[:8]} '
                          f'(skin has {n_skin_bones})')
                    problems += 1
                if has_map:
                    bad_v = [v for v in p.vertex_map if v >= n_geom_verts]
                    covered.update(int(v) for v in p.vertex_map)
                    if bad_v:
                        print(f'      !! vertex map indices out of range: '
                              f'{bad_v[:8]} (geometry has {n_geom_verts})')
                        problems += 1
                for t in p.triangles:
                    if t.v_1 >= nv or t.v_2 >= nv or t.v_3 >= nv:
                        print(f'      !! triangle ({t.v_1},{t.v_2},{t.v_3}) '
                              f'indexes past partition vert count {nv}')
                        problems += 1
                        break
                if p.has_bone_indices:
                    bad_pal = 0
                    for bi in p.bone_indices:
                        for k in range(nw):
                            if bi[k] >= nb:
                                bad_pal += 1
                                break
                    if bad_pal:
                        print(f'      !! {bad_pal} vertices reference palette '
                              f'slots >= {nb}')
                        problems += 1

            if n_geom_verts and covered and len(covered) != n_geom_verts:
                print(f'    NOTE: partitions cover {len(covered)} of '
                      f'{n_geom_verts} geometry vertices')

    if not found:
        print('  (no skinned geometry)')
    return problems


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    paths = []
    for a in args:
        p = Path(a)
        if p.is_dir():
            paths.extend(sorted(p.rglob('*.nif')))
        else:
            paths.append(p)
    total = 0
    for p in paths:
        try:
            total += dump_nif(str(p))
        except Exception as e:
            print(f'\n=== {p}\n  ERROR: {e}')
            total += 1
    print(f'\n{total} problem(s) found across {len(paths)} file(s).')
    sys.exit(1 if total else 0)


if __name__ == '__main__':
    main()
