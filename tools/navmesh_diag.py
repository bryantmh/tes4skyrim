"""Quantify navmesh quality defects for a cell.

Reports, for one cell's generated navmesh:
  * STEEP triangles (a walkable tri that reads as a near-vertical face)
  * ISLANDS (disconnected components — NPCs can't cross between them)
  * WRONG-FLOOR (a pathgrid node whose nearest navmesh vertex is far in Z)

    python tools/navmesh_diag.py <CellEditorID_or_FormID>
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asset_convert import collision_extract as ce
from tes5_import.navmesh import build, params
from tools.navmesh_probe import load_cell

CELL = sys.argv[1] if len(sys.argv) > 1 else 'AnvilFightersGuild'
ctx = load_cell('export/Oblivion.esm', CELL)
v, t = build.build_navmesh(
    ctx['refrs'], ctx['base_model'], ce.get_collision, ctx['nodes'], ctx['edges'],
    land_rec=ctx['land'] if ctx['is_exterior'] else None,
    origin_x=ctx['grid_x'] * 4096.0, origin_y=ctx['grid_y'] * 4096.0)
v = np.asarray(v)
print('cell %s: %d verts %d tris' % (CELL, len(v), len(t)))

# 1. Steep triangles (a walkable tri should be near-horizontal).
steep = 0
for (a, b, c) in t:
    e1 = v[b] - v[a]
    e2 = v[c] - v[a]
    n = np.cross(e1, e2)
    ln = np.linalg.norm(n)
    if ln < 1e-9:
        continue
    cosz = abs(n[2]) / ln
    if cosz < math.cos(math.radians(50)):
        steep += 1
print('  STEEP triangles (>50deg): %d (%.0f%%)' % (steep, 100 * steep / max(1, len(t))))

# 2. Islands (connected components).
parent = list(range(len(v)))
def find(x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x
def union(a, b):
    parent[find(a)] = find(b)
for (a, b, c) in t:
    union(a, b); union(b, c)
comps = {}
for (a, b, c) in t:
    comps.setdefault(find(a), []).append((a, b, c))
sizes = sorted((len(x) for x in comps.values()), reverse=True)
print('  ISLANDS: %d components, tri counts %s' % (len(sizes), sizes[:8]))

# 3. Wrong-floor: pathgrid node whose nearest navmesh vertex is far in Z.
zerr = []
for (nx, ny, nz) in ctx['nodes']:
    d = (v[:, 0] - nx) ** 2 + (v[:, 1] - ny) ** 2
    near = d < (params.SEED_SNAP * 1.5) ** 2
    if near.any():
        zerr.append(float(np.min(np.abs(v[near, 2] - nz))))
if zerr:
    zerr.sort()
    bad = sum(1 for z in zerr if z > params.MAX_CLIMB * 2)
    print('  WRONG-FLOOR: %d/%d nodes with nearest-vert |dz|>%.0f (median %.0f max %.0f)'
          % (bad, len(zerr), params.MAX_CLIMB * 2, zerr[len(zerr) // 2], zerr[-1]))
