"""Profile navmesh generation for one cell: where does the time actually go?

    python tools/navmesh_profile.py --cell Wendir02
    python tools/navmesh_profile.py --cell grid:12:-8 --top 25
"""

import argparse
import cProfile
import os
import pstats
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asset_convert import collision_extract as ce  # noqa: E402
from tes5_import.navmesh import build  # noqa: E402
from tools.navmesh_probe import load_cell  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--export', default='export/Oblivion.esm')
    ap.add_argument('--cell', required=True)
    ap.add_argument('--top', type=int, default=20)
    ap.add_argument('--callers', help='also print callers of this function')
    a = ap.parse_args()

    ctx = load_cell(a.export, a.cell)
    print('cell %s (%s) exterior=%s refrs=%d nodes=%d'
          % (ctx['cell_fid'], ctx['cell'].get('EditorID'),
             ctx['is_exterior'], len(ctx['refrs']), len(ctx['nodes'])))

    pr = cProfile.Profile()
    pr.enable()
    verts, tris = build.build_navmesh(
        ctx['refrs'], ctx['base_model'], ce.get_collision,
        ctx['nodes'], ctx['edges'],
        land_rec=ctx['land'] if ctx['is_exterior'] else None,
        origin_x=ctx['grid_x'] * 4096.0, origin_y=ctx['grid_y'] * 4096.0,
        doors=ctx.get('doors'))
    pr.disable()
    print('navmesh: %d verts %d tris' % (len(verts), len(tris)))

    st = pstats.Stats(pr)
    st.sort_stats('cumulative').print_stats(a.top)
    st.sort_stats('tottime').print_stats(a.top)
    if a.callers:
        st.print_callers(a.callers)


if __name__ == '__main__':
    main()
