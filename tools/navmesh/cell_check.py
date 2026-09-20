"""Run the CK CheckNavMesh rules on FRESHLY GENERATED cells (no ESM needed).

`tools/navmesh/check.py` validates a built ESM, which means a full conversion
before you can see whether a generator change worked.  This runs the same rule
set against `build.build_navmesh` output for named cells, so a fix can be
iterated in seconds.

    python tools/navmesh/cell_check.py XPAichan01 SancreTor03 Ondo
    python tools/navmesh/cell_check.py --export export/Nehrim.esm Cell01

Cells are named by EditorID and read from the audit index
(`export/<plugin>/audit_index3.pkl`, built by tools/navmesh/audit.py).
`--export` selects the plugin; it defaults to Oblivion.esm and must name an
export whose audit index already exists.
Exterior cells need their grid origin, so pass those to navmesh/audit.py
instead, or extend this with --formid.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from asset_convert.collision import collision_extract as ce
from tes5_import.navmesh import build
from tes5_import.navmesh.from_pgrd import (collect_doors, compute_adjacency,
                                      load_door_centroids)
import tools.navmesh.audit as na
from tools.navmesh.audit import build_index
import tools.navmesh.check as nc


def _export_arg(argv):
    """The --export value, defaulting to Oblivion.esm."""
    if '--export' in argv:
        return argv[argv.index('--export') + 1]
    return 'export/Oblivion.esm'


EXPORT = _export_arg(sys.argv)
ce.load_collision(os.path.join(EXPORT,'collision_cache.bin'), quiet=True)
# Without this the door panel centroids, threshold AXIS and doorway WIDTH are
# all unset, so this tool would generate doors the pipeline never generates.
load_door_centroids(os.path.join(EXPORT,'door_centers_cache.json'), quiet=True)
(base_model, refr_by_cell, pgrd_by_cell, land_by_cell,
 door_fids, cells) = build_index(EXPORT)

class M: pass
def check(verts,tris):
    """Build a NavMesh-alike and run the per-triangle rules."""
    nm=nc.NavMesh()
    nm.formid=0; nm.truncated=False; nm.version=12; nm.worldspace=0; nm.cell=0
    nm.grid=None; nm.edge_links=[]; nm.door_tris=[]; nm.cover_tris=0; nm.bbox=None
    nm.verts=[c for v in verts for c in v]
    adj=compute_adjacency(tris)
    nm.tris=[(t[0],t[1],t[2],adj[i][0],adj[i][1],adj[i][2],0x0800,0) for i,t in enumerate(tris)]
    from collections import Counter
    return Counter(r for r,_ in nc.check_navmesh(nm,None,local_mask=None))

def door_report(verts, tris, doors):
    """Per-door: the Door Triangle's size, and whether the mesh spans the
    doorway.  A door triangle an actor cannot stand on stops pathing through
    the door even though the topology looks correct — vanilla door triangles
    are min 992 / median 9,614 sq units (n=1,659 in Skyrim.esm)."""
    import math
    from tes5_import.navmesh.from_pgrd import build_door_links
    links = dict((ref, ti) for ti, ref in build_door_links(
        verts, tris, [(x, y, z, r, i + 1, tp, w)
                      for i, (x, y, z, r, _f, tp, w) in enumerate(doors)]))

    def _area(t):
        a, b, c = verts[t[0]], verts[t[1]], verts[t[2]]
        return abs((b[0]-a[0])*(c[1]-a[1])-(c[0]-a[0])*(b[1]-a[1]))/2.0

    def _covered(px, py):
        # Tolerant containment.  The threshold line lies exactly ON the door
        # quad's own boundary, so an exact point-in-triangle test reports the
        # doorway as uncovered even when the mesh is perfect — that artifact
        # cost a full debugging cycle.  Allow a small negative barycentric
        # slack so a point sitting on a shared edge counts as covered.
        for t in tris:
            A = [verts[t[k]] for k in range(3)]
            d = ((A[1][1]-A[2][1])*(A[0][0]-A[2][0])
                 + (A[2][0]-A[1][0])*(A[0][1]-A[2][1]))
            if abs(d) < 1e-9:
                continue
            l0 = ((A[1][1]-A[2][1])*(px-A[2][0])
                  + (A[2][0]-A[1][0])*(py-A[2][1])) / d
            l1 = ((A[2][1]-A[0][1])*(px-A[2][0])
                  + (A[0][0]-A[2][0])*(py-A[2][1])) / d
            if l0 >= -0.02 and l1 >= -0.02 and (1.0-l0-l1) >= -0.02:
                return True
        return False

    rows = []
    for i, (x, y, z, rz, _f, tp, w) in enumerate(doors):
        ti = links.get(i + 1)
        ar = _area(tris[ti]) if ti is not None else 0.0
        tx, ty = math.sin(rz), math.cos(rz)
        half = 0.5 * w if w else 45.0
        N = 16
        miss = sum(0 if _covered(x + tx * s, y + ty * s) else 1
                   for s in (-half + 2 * half * k / N for k in range(N + 1)))
        rows.append((w, ar, miss, N + 1))
    return rows


want_doors = '--doors' in sys.argv
_skip = {EXPORT} if '--export' in sys.argv else set()
for cellname in [a for a in sys.argv[1:]
                 if not a.startswith('--') and a not in _skip]:
    c=[x for x in cells if (x.get('EditorID') or '').lower()==cellname.lower()][0]
    fid=(c.get('FormID') or '').upper()
    nodes,edges=na._pgrd_nodes(pgrd_by_cell[fid])
    refrs=refr_by_cell.get(fid,[])
    doors=collect_doors(refrs,door_fids)
    v,t=build.build_navmesh(refrs, base_model, ce.get_collision, nodes, edges,
                            land_rec=land_by_cell.get(fid),
                            doors=[(x,y,z,r,tp,w) for (x,y,z,r,_f,tp,w) in doors])
    cnt=check(v,t)
    print('%-20s tris=%-6d %s'%(cellname,len(t),dict(cnt) if cnt else 'CLEAN'))
    if want_doors:
        for (w, ar, miss, n) in door_report(v, t, doors):
            flag = ''
            if ar < 992:
                flag += '  <== BELOW VANILLA MIN AREA'
            if miss:
                flag += '  <== DOORWAY NOT SPANNED'
            print('    door width=%6.1f  tri area=%8.1f  uncovered %d/%d%s'
                  % (w, ar, miss, n, flag))
