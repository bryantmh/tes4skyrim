"""Run navmesh (PGRD->NAVM) jobs by FormID, in-process, with real tracebacks.

WHY THIS EXISTS (and why the other navmesh tools don't cover it)

`tes5_import.navmesh.worker.run_job` deliberately catches per-cell errors so one bad
cell cannot abort the 2,900-job pool, and the pool's workers run under
`pythonw.exe` where stdout goes nowhere. So when the import reports

    WARNING: 2 cells produced no navmesh:
      cell 012217C1 pgrd 01221843: IndexError: list index out of range

this is how you get the actual traceback: it calls `convert_PGRD` directly, in
this process, for the cell you name.

It also finds jobs that kill the interpreter outright. A C++ exception escaping
the native extension aborts the process with no Python traceback at all, and on
the import's thread pool it takes the whole import down. With `--all`, each
job's identity is flushed BEFORE the call, so a hard crash leaves the culprit as
the last line printed (this is how Nehrim's cell 011E4FEC was found -- see
docs/commentary/performance.md).

`tools/navmesh/cell_check.py` names cells by EditorID out of an audit index and
checks CK rules; this takes the raw FormID straight from an error message and
cares only about reproducing the failure.

    # one cell, full traceback
    python tools/navmesh/job_trace.py --plugin Nehrim.esm --cell 012217C1

    # walk jobs in dispatch order to find one that hard-crashes
    python tools/navmesh/job_trace.py --plugin Nehrim.esm --all --max 100

    # only the jobs with no geometry-cache entry (the ones that recompute)
    python tools/navmesh/job_trace.py --plugin Nehrim.esm --uncached --max 20
"""

import argparse
import faulthandler
import os
import sys
import time
import traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
from core.navmesh_options import set_navmesh_plugin
from output_layout import asset_cache_chain
from tes5_import.base.mesh_bounds import load_mesh_bounds
from tes5_import.base.obstacles import index_obstacles
from tes5_import.navmesh import cache_audit, pool as navm_pool
from tes5_import.navmesh import worker as navm_worker
from tes5_import.record_types.world_morrowind import register_tes3_locks


def init_worker_for(export_dir, im, door_fids, base_model_by_fid, offset):
    """Initialise the in-process navmesh context as the import does; the geometry cache.

    Collision and door centers load as the MASTERS-FIRST chain: with only the
    plugin's own file, master-owned meshes have no collision and a child
    plugin's cells build different geometry than the import cached.

    See: docs/commentary/tes5_import_navmesh.md#master-owned-cells
    """
    collision = navm_pool.collision_cache_chain(export_dir)
    geom_cache = navm_pool.navmesh_geom_cache(collision[-1])
    door_centers = cache_audit.door_centers_cache_path(collision)
    cache_audit.init_context(base_model_by_fid, door_fids, collision, offset, geom_cache,
                             im.get_injected_formids(), door_centers)
    return geom_cache


def run_on_pool(sel, workers, no_cache):
    """Run the selected jobs on the import's own thread pool; print wall-clock and throughput."""
    jobs = [dict(job, prove=no_cache) for _i, job in sel]
    t0 = time.time()
    results = navm_pool.run_pooled(jobs, workers)
    dt = time.time() - t0
    failed = sum(1 for _nb, meta in results.values() if (meta or {}).get('error'))
    built = sum(1 for nb, _meta in results.values() if nb)
    print(f'\n{len(jobs)} jobs on {workers} threads: '
          f'{dt:.1f}s ({len(jobs) / max(dt, 1e-9):.2f} jobs/s), {built} built, {failed} failed',
          flush=True)
    return 1 if failed else 0


def load_export(export_dir, offset):
    """(text_reader, by_type, door_fids, base_model_by_fid, jobs) for a plugin.

    Every step the import performs between parsing and gathering is repeated
    here -- namespace, index offset, the runtime-only exclusion, the deleted
    records, and the leveled placements that become ACHR.  Skip one and the
    geometry rebuilt here silently differs from the import's.

    See: docs/commentary/tes5_import_navmesh.md#leveled-placements-never-carve
    """
    from asset_convert.game_paths import namespace_for, set_namespace
    from tes5_import.base import text_reader as im
    from tes5_import.base.text_reader import (parse_export_directory,
                                         group_records_by_type,
                                         set_formid_index_offset)
    from tes5_import.overrides.nested import load_master_export
    from tes5_import.pipeline import drop_author_deleted_records
    from tes5_import.registry import RUNTIME_ONLY_TYPES

    set_namespace(namespace_for(export_dir))
    set_formid_index_offset(offset)
    print(f'parsing {export_dir} ...', flush=True)
    t0 = time.time()
    records = parse_export_directory(export_dir, exclude=RUNTIME_ONLY_TYPES)
    by_type = group_records_by_type(
        drop_author_deleted_records(records, None))
    print(f'  parsed in {time.time() - t0:.1f}s', flush=True)

    master_export = load_master_export(export_dir)
    if master_export:
        print(f'  {len(master_export)} master records', flush=True)
    n_lvl = navm_pool.drop_leveled_placements(by_type, master_export)
    if n_lvl:
        print(f'  {n_lvl} leveled placements become ACHR (not carved)',
              flush=True)
    load_mesh_bounds(asset_cache_chain(export_dir, 'mesh_bounds_cache.json'), quiet=True)
    register_tes3_locks(by_type, export_dir, master_export)
    door_fids = navm_pool.build_door_fid_set(by_type, master_export)
    index_obstacles(by_type, master_export)
    base_model_by_fid = navm_pool.build_base_model_index(by_type,
                                                         master_export)
    jobs = navm_pool.gather_navm_jobs(by_type, door_fids, master_export)
    # FormIDs are pre-assigned in the parent in the real run; any stable value
    # works here since we are not writing a plugin.
    for i, job in enumerate(jobs):
        job['navm_fid'] = 0x01000000 + i
    return im, by_type, door_fids, base_model_by_fid, jobs


def parse_args():
    """The command line, with `export_dir` resolved."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--plugin', default='Oblivion.esm',
                    help='plugin name (its pins apply); export dir is export/<plugin>')
    ap.add_argument('--export', help='explicit export dir (overrides --plugin)')
    ap.add_argument('--cell', action='append', default=[],
                    help='cell FormID hex (repeatable)')
    ap.add_argument('--all', action='store_true',
                    help='run jobs in dispatch order (finds hard crashes)')
    ap.add_argument('--uncached', action='store_true',
                    help='only jobs with no geometry-cache entry')
    ap.add_argument('--start', type=int, default=0)
    ap.add_argument('--max', type=int, default=50)
    ap.add_argument('--no-cache', action='store_true',
                    help='ignore the geometry cache: time a REAL build '
                         '(what an unprimed machine, or any navmesh code '
                         'change, actually costs)')
    ap.add_argument('--sample', type=int, metavar='N',
                    help='run N jobs drawn evenly across the whole job list '
                         'instead of the first --max. Dispatch order is '
                         'grouped by worldspace, so the head of the list is '
                         'not representative of the population')
    ap.add_argument('--extrapolate', nargs=2, type=int,
                    metavar=('JOBS', 'WORKERS'),
                    help='project the measured mean onto a full run')
    ap.add_argument('--offset', type=int, default=1,
                    help='load-order FormID index offset (default 1)')
    ap.add_argument('--workers', type=int, metavar='N',
                    help="run the selection on the import's own pool of N threads")
    args = ap.parse_args()
    if not (args.cell or args.all or args.uncached or args.sample):
        ap.error('pass --cell, --all, --uncached or --sample')
    args.export_dir = args.export or os.path.join('export', args.plugin)
    if not os.path.isdir(args.export_dir):
        ap.error(f'no such export dir: {args.export_dir}')
    set_navmesh_plugin(args.plugin)
    return args


def uncached(sel, geom_cache):
    """The selected jobs with no geometry-cache entry."""
    if not geom_cache:
        raise SystemExit('--uncached needs a geometry cache (missing collision_cache.bin?)')
    have = set(os.listdir(geom_cache[0]))
    sel = [(i, j) for i, j in sel if '%08X_%08X.pkl' % j['key'] not in have]
    print(f'  {len(sel)} jobs have no geometry-cache entry', flush=True)
    return sel


def select_jobs(args, jobs, geom_cache):
    """[(index, job)] the command line picks.

    --sample takes an even stride, not the head: jobs are dispatched grouped
    by worldspace, so the first N are one region (often all interiors).
    """
    if args.cell:
        want = {int(c, 16) for c in args.cell}
        sel = [(i, j) for i, j in enumerate(jobs) if j['key'][0] in want]
        for m in sorted(want - {j['key'][0] for _i, j in sel}):
            print(f'  (no navmesh job for cell {m:08X})', flush=True)
        return sel
    sel = list(enumerate(jobs))
    if args.uncached:
        sel = uncached(sel, geom_cache)
    if args.sample:
        return sel[::max(1, len(sel) // args.sample)][:args.sample]
    return sel[args.start:args.start + args.max]


def describe(nb, meta, dt):
    """One job's result line."""
    if nb is None:
        return f'      -> no navmesh (no usable geometry) [{dt:.2f}s]'
    m = meta or {}
    extra = ((['geometry from cache'] if m.get('geom_cached') else [])
             + ([f'{len(m["door_refs"])} door links'] if m.get('door_refs') else []))
    return (f'      -> {len(nb)} bytes' + (f' ({", ".join(extra)})' if extra else '')
            + f' [{dt:.2f}s]')


def run_one(i, job, base_model_by_fid, door_fids, geom_cache):
    """Seconds one job took, or None when it raised (traceback printed).

    The job's identity is flushed BEFORE the call, so a native abort leaves
    the culprit as the last line printed.
    """
    cell, pgrd = job['key']
    t0 = time.time()
    print(f'  job[{i}] cell {cell:08X} pgrd {pgrd:08X} '
          f'refrs={len(job["refr_recs"])} '
          f'land={"Y" if job["land_rec"] is not None else "N"}', flush=True)
    try:
        nb, meta = navm_worker.convert_job(job, base_model_by_fid, door_fids, geom_cache)
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        return None
    dt = time.time() - t0
    print(describe(nb, meta, dt), flush=True)
    return dt


def report_times(times, extrapolate):
    """Print the per-job summary and, given (jobs, workers), the projected full run.

    The projection is mean * jobs / workers: it ignores the scheduling tail
    and worker startup.
    """
    ts = sorted(times)
    n, total = len(ts), sum(ts)
    print('\nPER-JOB SECONDS  n=%d  total=%.1fs  mean=%.2f  p50=%.2f  p90=%.2f  max=%.2f'
          % (n, total, total / n, ts[n // 2], ts[min(n - 1, int(n * .9))], ts[-1]))
    if extrapolate:
        jobs_n, workers = extrapolate
        print('  extrapolated: %d jobs / %d workers -> %.1f min'
              % (jobs_n, workers, (total / n) * jobs_n / max(1, workers) / 60.0))


def run_serial(sel, base_model_by_fid, door_fids, geom_cache, extrapolate):
    """Run each selected job in turn; 1 when any raised."""
    print(f'\nrunning {len(sel)} job(s) in-process...', flush=True)
    times = [run_one(i, job, base_model_by_fid, door_fids, geom_cache) for i, job in sel]
    ok = [t for t in times if t is not None]
    print(f'\n{len(ok)}/{len(sel)} ran without raising', flush=True)
    if ok:
        report_times(ok, extrapolate)
    return 0 if len(ok) == len(sel) else 1


def main():
    """Load the export, pick the jobs and run them as the command line asks; the exit code."""
    args = parse_args()
    faulthandler.enable()
    im, _by_type, door_fids, base_model_by_fid, jobs = load_export(args.export_dir, args.offset)
    print(f'  {len(jobs)} navmesh jobs', flush=True)
    geom_cache = init_worker_for(args.export_dir, im, door_fids, base_model_by_fid, args.offset)
    sel = select_jobs(args, jobs, geom_cache)
    if args.no_cache:
        geom_cache = None
        print('  geometry cache DISABLED (cold-cache timing)', flush=True)
    if args.workers:
        return run_on_pool(sel, args.workers, args.no_cache)
    return run_serial(sel, base_model_by_fid, door_fids, geom_cache, args.extrapolate)


if __name__ == '__main__':
    raise SystemExit(main())

