"""Profile navmesh generation: where does the time actually go?

Two views, because they answer different questions:

  STAGE  wall-clock per pipeline stage (geometry / grow / union / clean),
         measured by wrapping the module boundaries build.py calls.  This is
         the number that decides a rewrite: Amdahl's law caps any speedup of a
         stage at that stage's share of the total.
  FUNC   cProfile per-function time, for finding the hot kernel INSIDE
         whichever stage the stage view indicts.

Runs SERIALLY on purpose: cProfile cannot see into worker processes, and
per-stage attribution across a pool would need timings shipped back per cell.
Fractions are what matter, and they carry to the parallel run because every
worker runs this same code.  Use a small cell subset.

    python tools/navmesh/perf.py --cell Wendir02
    python tools/navmesh/perf.py --cells Wendir02,ICMarketDistrict --stages
    python tools/navmesh/perf.py --cell 0000A1B2 --top 25

Cells load through the lazy per-cell index (`NavIndex`), so a run costs one
cell's records rather than the multi-GB export pickle.
"""

import argparse
import cProfile
import os
import pstats
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tes5_import.navmesh import (
    corridor, corridor_clean, corridor_doors, corridor_grow,
    corridor_union, world,
)
from tools.navmesh.index import NavIndex


# ---------------------------------------------------------------------------
# Stage timing
# ---------------------------------------------------------------------------

_ACC = {}

# Labels that run INSIDE another timed stage; excluded from the "(other)"
# subtraction so it cannot go negative.  See stage_report.
_NESTED = set()


def rss_note():
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
        k32, ps = ctypes.WinDLL('kernel32'), ctypes.WinDLL('psapi')
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        ps.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(_PMC), wintypes.DWORD]
        if ps.GetProcessMemoryInfo(k32.GetCurrentProcess(),
                                   ctypes.byref(pmc), pmc.cb):
            return '  rss=%.1fGB peak=%.1fGB' % (
                pmc.WorkingSetSize / 2 ** 30, pmc.PeakWorkingSetSize / 2 ** 30)
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
    """Wrap the stages the CORRIDOR build actually runs.

    These used to wrap voxel/region/spanmesh, which stopped being on the build
    path when the corridor model landed and have since been deleted — so every
    stage read 0.00 and the whole run showed up as "(other)".
    """
    _wrap(world, 'gather_cell_geometry', 'world.gather_cell_geometry')

    # Surface samplers and strip planning — folded in from the retired
    # tools/navmesh/corridor_profile.py.  corridor.py imports the union/doors/
    # clean modules INSIDE build_corridors, so patching the module attribute is
    # what the call actually resolves through.
    _wrap(corridor, '_surface_sampler', 'corridor._surface_sampler')
    _wrap(corridor_grow, 'wall_slab_sampler', 'corridor_grow.wall_slab_sampler')
    _wrap(corridor, '_build_corridor_strips', 'corridor._build_corridor_strips')
    for fn, label in (('grow_batch', 'corridor_grow.grow_batch(native)'),):
        _NESTED.add(label)
        _wrap(corridor_grow, fn, label)
    _NESTED.add('corridor._plan_stations')
    _wrap(corridor, '_plan_stations', 'corridor._plan_stations')
    _wrap(corridor_doors, 'door_footprints', 'corridor_doors.door_footprints')

    _wrap(corridor_union, 'build_union_mesh', 'corridor_union.build_union_mesh')
    for fn in ('_split_plan_overlaps', '_merge_at_pathgrid_nodes',
               'stitch_shared_nodes', 'split_t_junctions', '_weld_sheets',
               '_triangulate', '_emit_surfaces', '_has_coverage',
               '_sheet_coverage', '_close_land_slits', '_sheet_parts',
               '_junction_transfers', '_levels_batch', '_destack',
               '_drop_walls', '_fill_boundary_notches', '_drop_point_attached',
               '_claim_door_edges'):
        label = 'corridor_union.' + fn
        _NESTED.add(label)
        _wrap(corridor_union, fn, label)
    for fn in ('finalize', 'decimate'):
        _wrap(corridor_clean, fn, 'corridor_clean.' + fn)


def stage_report(total_wall):
    rows = sorted(_ACC.items(), key=lambda kv: -kv[1][0])
    print('\n%-40s %9s %7s %7s' % ('STAGE', 'SEC', 'SHARE', 'CALLS'))
    print('-' * 67)
    for k, (dt, n) in rows:
        # Sub-stages are marked, so a nested row is never mistaken for another
        # slice of the total.
        mark = '  ' if k in _NESTED else ''
        print('%-40s %9.2f %6.1f%% %7d'
              % (mark + k, dt, 100.0 * dt / total_wall, n))
    # Only TOP-LEVEL stages may be subtracted.  The sub-stage rows run INSIDE
    # build_union_mesh, so counting them here made "(other)" negative (-63.5% on
    # Moranda) -- which reads as a measurement bug rather than the double-count
    # it actually is.
    named = sum(dt for k, (dt, _n) in _ACC.items() if k not in _NESTED)
    other = total_wall - named
    print('%-40s %9.2f %6.1f%%' % ('(other / unattributed)', other,
                                   100.0 * other / total_wall))
    print('-' * 67)
    print('%-40s %9.2f %6.1f%%' % ('TOTAL', total_wall, 100.0))

    # The headline: what a perfect rewrite of the union stage would buy.  Amdahl
    # caps any speedup at that stage's share, so this is the number that decides
    # whether a C++ kernel is worth writing at all.
    sm = _ACC.get('corridor_union.build_union_mesh', (0.0, 0))[0]
    if sm and total_wall:
        print('\nbuild_union_mesh share: %.1f%%' % (100.0 * sm / total_wall))
        for factor, lbl in ((20.0, '20x (C++ kernel)'),
                            (10.0, '10x (numba-ish)'),
                            (float('inf'), 'infinite (upper bound)')):
            newt = (total_wall - sm) + (0.0 if factor == float('inf')
                                        else sm / factor)
            print('  union %-24s -> total %5.2fx faster'
                  % (lbl, total_wall / newt))


def _args():
    """(parser, parsed args, cell names), the names validated against --max-cells."""
    ap = argparse.ArgumentParser()
    ap.add_argument('--export', default='export/Oblivion.esm')
    ap.add_argument('--cell', help='single cell (EditorID or FormID)')
    ap.add_argument('--cells', help='comma-separated list of the same')
    ap.add_argument('--stages', action='store_true',
                    help='stage timing only; skip cProfile (truer wall-clock)')
    ap.add_argument('--top', type=int, default=20)
    ap.add_argument('--callers', help='also print callers of this function')
    ap.add_argument('--sort', default='tottime', choices=('tottime', 'cumulative'),
                    help='cProfile ranking key')
    ap.add_argument('--max-cells', type=int, default=12,
                    help='refuse to profile more than this many cells at once')
    a = ap.parse_args()
    names = [c.strip() for c in (a.cells or '').split(',') if c.strip()]
    if a.cell:
        names.append(a.cell)
    if not names:
        ap.error('need --cell or --cells')
    if len(names) > a.max_cells:
        ap.error('%d cells requested but --max-cells is %d; profile SMALL '
                 'subsets.' % (len(names), a.max_cells))
    return ap, a, names


def _load_cells(ap, export, names):
    """One loaded CellCtx per name, each announced with the process RSS."""
    index = NavIndex(export)
    ctxs = []
    for n in names:
        ctx = index.cell(n)
        if ctx is None:
            ap.error('cell %s not found' % n)
        print('cell %s (%s) exterior=%s refrs=%d nodes=%d%s'
              % (ctx.fid, ctx.name, ctx.exterior, len(ctx.refrs),
                 len(ctx.nodes), rss_note()))
        ctxs.append(ctx)
    return ctxs


def _run_all(ctxs):
    """Build every loaded cell once: [(name, verts, tris, seconds)]."""
    out = []
    for ctx in ctxs:
        t0 = time.perf_counter()
        verts, tris = ctx.build()
        out.append((ctx.name or ctx.fid, len(verts), len(tris),
                    time.perf_counter() - t0))
    return out


def _print_profile(pr, a):
    """The cProfile ranking: absolute times are inflated, so rank only (--stages for real shares)."""
    print('\n=== cProfile -- ranking only, absolute times inflated ===')
    st = pstats.Stats(pr)
    st.sort_stats(a.sort).print_stats(a.top)
    if a.callers:
        st.print_callers(a.callers)


def main():
    """Profile the named cells' navmesh builds by stage, and by function unless --stages."""
    ap, a, names = _args()
    ctxs = _load_cells(ap, a.export, names)
    install_stage_timers()
    pr = None if a.stages else cProfile.Profile()
    if pr is not None:
        pr.enable()
    t0 = time.perf_counter()
    per_cell = _run_all(ctxs)
    total = time.perf_counter() - t0
    if pr is not None:
        pr.disable()
    print('\n%-36s %8s %8s %8s' % ('CELL', 'VERTS', 'TRIS', 'SEC'))
    for (name, nv, nt, dt) in per_cell:
        print('%-36s %8d %8d %8.2f' % (name[:36], nv, nt, dt))
    stage_report(total)
    if pr is not None:
        _print_profile(pr, a)


if __name__ == '__main__':
    main()
