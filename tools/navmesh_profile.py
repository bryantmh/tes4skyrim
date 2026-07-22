"""Profile navmesh generation: where does the time actually go?

Two views, because they answer different questions:

  STAGE  wall-clock per pipeline stage (voxelize / region / spanmesh / post),
         measured by wrapping the module boundaries build.py calls.  This is
         the number that decides a rewrite: Amdahl's law caps any speedup of a
         stage at that stage's share of the total.
  FUNC   cProfile per-function time, for finding the hot kernel INSIDE
         whichever stage the stage view indicts.

Runs SERIALLY on purpose: cProfile cannot see into worker processes, and
per-stage attribution across a pool would need timings shipped back per cell.
Fractions are what matter, and they carry to the parallel run because every
worker runs this same code.  Use a small cell subset.

    python tools/navmesh_profile.py --cell Wendir02
    python tools/navmesh_profile.py --cells Wendir02,ICMarketDistrict --stages
    python tools/navmesh_profile.py --cell grid:12:-8 --top 25
"""

import argparse
import cProfile
import os
import pstats
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asset_convert import collision_extract as ce  # noqa: E402
from tes5_import.navmesh import build, region, spanmesh, voxel  # noqa: E402
from tools.navmesh_probe import load_cell  # noqa: E402


# ---------------------------------------------------------------------------
# Stage timing
#
# Stages are wrapped where build.py calls them, so each number is true
# wall-clock inclusive of everything that stage does.  build.py does
# `from . import region, spanmesh, voxel` and looks attributes up at call time,
# so patching the module attribute is enough.  Whatever is left over appears as
# "(other)" -- recovered by subtraction so no time goes silently unattributed.
# ---------------------------------------------------------------------------

_ACC = {}


def _rss_note():
    """' rss=N.NGB' when the platform can tell us, else ''.

    Cheap Windows-native query so this works without psutil (not installed).
    """
    try:
        import ctypes
        from ctypes import wintypes

        class _PMC(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD),
                        ('PageFaultCount', wintypes.DWORD),
                        ('PeakWorkingSetSize', ctypes.c_size_t),
                        ('WorkingSetSize', ctypes.c_size_t),
                        ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                        ('QuotaPagedPoolUsage', ctypes.c_size_t),
                        ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                        ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                        ('PagefileUsage', ctypes.c_size_t),
                        ('PeakPagefileUsage', ctypes.c_size_t)]

        pmc = _PMC()
        pmc.cb = ctypes.sizeof(_PMC)
        if ctypes.windll.psapi.GetProcessMemoryInfo(
                ctypes.windll.kernel32.GetCurrentProcess(),
                ctypes.byref(pmc), pmc.cb):
            return '  rss=%.1fGB' % (pmc.WorkingSetSize / 2 ** 30)
    except Exception:
        pass
    return ''


def _wrap(mod, fname, label):
    orig = getattr(mod, fname, None)
    if orig is None:
        return
    def timed(*args, **kw):
        t0 = time.perf_counter()
        try:
            return orig(*args, **kw)
        finally:
            a = _ACC.setdefault(label, [0.0, 0])
            a[0] += time.perf_counter() - t0
            a[1] += 1
    timed.__name__ = fname
    setattr(mod, fname, timed)


def install_stage_timers():
    """Wrap only the calls build.py actually makes."""
    _wrap(spanmesh, 'build_mesh', 'spanmesh.build_mesh')
    _wrap(region, 'build_regions', 'region.build_regions')
    _wrap(region, 'keep_pathgrid_heights', 'region.keep_pathgrid_heights')
    for fn in ('erode_walkable', 'rasterize_triangles', 'rasterize',
               'build_heightfield', 'filter_low_hanging_obstacles',
               'filter_ledge_spans', 'filter_walkable_low_height'):
        _wrap(voxel, fn, 'voxel.' + fn)


def stage_report(total_wall):
    rows = sorted(_ACC.items(), key=lambda kv: -kv[1][0])
    named = sum(v[0] for _k, v in rows)
    print('\n%-40s %9s %7s %7s' % ('STAGE', 'SEC', 'SHARE', 'CALLS'))
    print('-' * 67)
    for k, (dt, n) in rows:
        print('%-40s %9.2f %6.1f%% %7d' % (k, dt, 100.0 * dt / total_wall, n))
    other = total_wall - named
    print('%-40s %9.2f %6.1f%%' % ('(other / unattributed)', other,
                                   100.0 * other / total_wall))
    print('-' * 67)
    print('%-40s %9.2f %6.1f%%' % ('TOTAL', total_wall, 100.0))

    # The headline: what a perfect rewrite of spanmesh would actually buy.
    sm = sum(dt for k, (dt, _n) in _ACC.items() if k.startswith('spanmesh'))
    if sm and total_wall:
        print('\nspanmesh share: %.1f%%' % (100.0 * sm / total_wall))
        for factor, lbl in ((20.0, '20x (C++ kernel)'),
                            (10.0, '10x (numba-ish)'),
                            (float('inf'), 'infinite (upper bound)')):
            newt = (total_wall - sm) + (0.0 if factor == float('inf')
                                        else sm / factor)
            print('  spanmesh %-24s -> total %5.2fx faster'
                  % (lbl, total_wall / newt))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--export', default='export/Oblivion.esm')
    ap.add_argument('--cell', help='single cell (EditorID, FormID, grid:X:Y)')
    ap.add_argument('--cells', help='comma-separated list of the same')
    ap.add_argument('--stages', action='store_true',
                    help='stage timing only; skip cProfile (truer wall-clock)')
    ap.add_argument('--top', type=int, default=20)
    ap.add_argument('--callers', help='also print callers of this function')
    ap.add_argument('--max-cells', type=int, default=6,
                    help='refuse to profile more than this many cells at once')
    a = ap.parse_args()

    names = []
    if a.cells:
        names += [c.strip() for c in a.cells.split(',') if c.strip()]
    if a.cell:
        names.append(a.cell)
    if not names:
        ap.error('need --cell or --cells')
    if len(names) > a.max_cells:
        ap.error('%d cells requested but --max-cells is %d.  The export index '
                 'is ~2 GB unpickled; profile SMALL subsets.'
                 % (len(names), a.max_cells))

    # The index pickle expands to several GB of live objects.  load_by_type now
    # memoises it per process, so these N calls share ONE graph -- but report
    # RSS anyway, because this tool wedged a 32 GB machine into swap once.
    idx = os.path.join(a.export, 'navmesh_index.pkl')
    if not os.path.exists(idx):
        ap.error('missing %s -- run a navmesh tool that builds it first; '
                 'this profiler must not trigger the multi-GB reindex.' % idx)

    ctxs = []
    for n in names:
        ctx = load_cell(a.export, n)
        print('cell %s (%s) exterior=%s refrs=%d nodes=%d%s'
              % (ctx['cell_fid'], ctx['cell'].get('EditorID'),
                 ctx['is_exterior'], len(ctx['refrs']), len(ctx['nodes']),
                 _rss_note()))
        ctxs.append(ctx)

    install_stage_timers()

    def run_all():
        out = []
        for ctx in ctxs:
            t0 = time.perf_counter()
            verts, tris = build.build_navmesh(
                ctx['refrs'], ctx['base_model'], ce.get_collision,
                ctx['nodes'], ctx['edges'],
                land_rec=ctx['land'] if ctx['is_exterior'] else None,
                origin_x=ctx['grid_x'] * 4096.0,
                origin_y=ctx['grid_y'] * 4096.0,
                doors=ctx.get('doors'))
            out.append((ctx['cell'].get('EditorID') or ctx['cell_fid'],
                        len(verts), len(tris), time.perf_counter() - t0))
        return out

    pr = None
    if not a.stages:
        pr = cProfile.Profile()
        pr.enable()
    t0 = time.perf_counter()
    per_cell = run_all()
    total = time.perf_counter() - t0
    if pr is not None:
        pr.disable()

    print('\n%-36s %8s %8s %8s' % ('CELL', 'VERTS', 'TRIS', 'SEC'))
    for (name, nv, nt, dt) in per_cell:
        print('%-36s %8d %8d %8.2f' % (name[:36], nv, nt, dt))

    stage_report(total)

    if pr is not None:
        # cProfile inflates absolute times; the RANKING is what it is for.
        # Re-run with --stages for uninflated stage shares.
        print('\n=== cProfile -- ranking only, absolute times inflated ===')
        st = pstats.Stats(pr)
        st.sort_stats('tottime').print_stats(a.top)
        if a.callers:
            st.print_callers(a.callers)


if __name__ == '__main__':
    main()
