"""Run the CK CheckNavMesh rules on FRESHLY GENERATED cells (no ESM needed).

`tools/navmesh_check.py` validates a built ESM, which means a full conversion
before you can see whether a generator change worked.  This runs the same rule
set against `build.build_navmesh` output for named cells, so a fix can be
iterated in seconds.

    python tools/navmesh_cell_check.py XPAichan01 SancreTor03 Ondo

Cells are named by EditorID and read from the audit index
(`export/<plugin>/audit_index3.pkl`, built by tools/navmesh_audit.py).
Exterior cells need their grid origin, so pass those to navmesh_audit.py
instead, or extend this with --formid.
"""
import os, sys, pickle
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),'tools'))
import numpy as np
from asset_convert import collision_extract as ce
from tes5_import.navmesh import build
from tes5_import.pgrd_to_navm import _collect_doors, _compute_adjacency
import tools.navmesh_audit as na
import navmesh_check as nc
EXPORT='export/Oblivion.esm'
ce.load_collision(os.path.join(EXPORT,'collision_cache.bin'), quiet=True)
with open(os.path.join(EXPORT,'audit_index3.pkl'),'rb') as fh:
    base_model, refr_by_cell, pgrd_by_cell, land_by_cell, door_fids, cells = pickle.load(fh)

class M: pass
def check(verts,tris):
    """Build a NavMesh-alike and run the per-triangle rules."""
    nm=nc.NavMesh()
    nm.formid=0; nm.truncated=False; nm.version=12; nm.worldspace=0; nm.cell=0
    nm.grid=None; nm.edge_links=[]; nm.door_tris=[]; nm.cover_tris=0; nm.bbox=None
    nm.verts=[c for v in verts for c in v]
    adj=_compute_adjacency(tris)
    nm.tris=[(t[0],t[1],t[2],adj[i][0],adj[i][1],adj[i][2],0x0800,0) for i,t in enumerate(tris)]
    from collections import Counter
    return Counter(r for r,_ in nc.check_navmesh(nm,None,local_mask=None))

for cellname in sys.argv[1:]:
    c=[x for x in cells if (x.get('EditorID') or '').lower()==cellname.lower()][0]
    fid=(c.get('FormID') or '').upper()
    nodes,edges=na._pgrd_nodes(pgrd_by_cell[fid])
    refrs=refr_by_cell.get(fid,[])
    doors=_collect_doors(refrs,door_fids)
    v,t=build.build_navmesh(refrs, base_model, ce.get_collision, nodes, edges,
                            land_rec=land_by_cell.get(fid),
                            doors=[(x,y,z,r,tp) for (x,y,z,r,_f,tp) in doors])
    cnt=check(v,t)
    print('%-20s tris=%-6d %s'%(cellname,len(t),dict(cnt) if cnt else 'CLEAN'))
