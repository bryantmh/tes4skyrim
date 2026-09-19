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
the native extension aborts the process with no Python traceback at all, which in
a pool worker surfaces only as an opaque `BrokenProcessPool`. With `--all`, each
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
from tes5_import.navmesh import cache_audit, pool as navm_pool
from tes5_import.navmesh import worker as navm_worker


def init_worker_for(export_dir, im, door_fids, base_model_by_fid, offset):
    """Initialise the in-process navmesh worker as the import does; geom_cache.

    Collision and door centers load as the MASTERS-FIRST chain: with only the
    plugin's own file, master-owned meshes have no collision and a child
    plugin's cells build different geometry than the import cached.

    See: docs/commentary/tes5_import_navmesh.md#master-owned-cells
    """
    collision = navm_pool.collision_cache_chain(export_dir)
    geom_cache = navm_pool.navmesh_geom_cache(collision[-1])
    navm_worker.init_worker(
        base_model_by_fid, door_fids, collision, offset, geom_cache,
        im.get_injected_formids(), disable_gc=False,
        door_centers_cache=cache_audit.door_centers_cache_path(collision))
    return geom_cache


def load_export(export_dir, offset):
    """(text_reader, by_type, door_fids, base_model_by_fid, jobs) for a plugin.

    Mirrors `pipeline_records`: the masters' export feeds both index builders,
    and the mesh namespace is set first because model keys carry it.  Skip
    either and the geometry rebuilt here silently differs from the import's.

    See: docs/commentary/tes5_import_navmesh.md#verifying-a-cache-against-fresh-geometry
    """
    from asset_convert.game_paths import namespace_for, set_namespace
    from tes5_import.base import text_reader as im
    from tes5_import.base.text_reader import (parse_export_directory,
                                         group_records_by_type,
                                         set_formid_index_offset)
    from tes5_import.overrides.nested import load_master_export

    set_namespace(namespace_for(export_dir))
    set_formid_index_offset(offset)
    print(f'parsing {export_dir} ...', flush=True)
    t0 = time.time()
    by_type = group_records_by_type(parse_export_directory(export_dir))
    print(f'  parsed in {time.time() - t0:.1f}s', flush=True)

    master_export = load_master_export(export_dir)
    if master_export:
        print(f'  {len(master_export)} master records', flush=True)
    door_fids = navm_pool.build_door_fid_set(by_type, master_export)
    base_model_by_fid = navm_pool.build_base_model_index(by_type,
                                                         master_export)
    jobs = navm_pool.gather_navm_jobs(by_type, door_fids, master_export)
    # FormIDs are pre-assigned in the parent in the real run; any stable value
    # works here since we are not writing a plugin.
    for i, job in enumerate(jobs):
        job['navm_fid'] = 0x01000000 + i
    return im, by_type, door_fids, base_model_by_fid, jobs


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--plugin', default='Oblivion.esm',
                    help='plugin name; export dir is export/<plugin>')
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
    args = ap.parse_args()

    if not (args.cell or args.all or args.uncached or args.sample):
        ap.error('pass --cell, --all, --uncached or --sample')

    export_dir = args.export or os.path.join('export', args.plugin)
    if not os.path.isdir(export_dir):
        ap.error(f'no such export dir: {export_dir}')

    faulthandler.enable()
    im, _by_type, door_fids, base_model_by_fid, jobs = load_export(
        export_dir, args.offset)
    print(f'  {len(jobs)} navmesh jobs', flush=True)

    from tes5_import.navmesh.from_pgrd import convert_PGRD

    geom_cache = init_worker_for(export_dir, im, door_fids, base_model_by_fid,
                                 args.offset)

    # Select the jobs to run.
    if args.cell:
        want = {int(c, 16) for c in args.cell}
        sel = [(i, j) for i, j in enumerate(jobs) if j['key'][0] in want]
        missing = want - {j['key'][0] for _i, j in sel}
        for m in sorted(missing):
            print(f'  (no navmesh job for cell {m:08X})', flush=True)
    else:
        sel = list(enumerate(jobs))
        if args.uncached:
            if not geom_cache:
                ap.error('--uncached needs a geometry cache '
                         '(missing collision_cache.bin?)')
            cache_dir, _tag = geom_cache
            have = set(os.listdir(cache_dir))
            sel = [(i, j) for i, j in sel
                   if '%08X_%08X.pkl' % j['key'] not in have]
            print(f'  {len(sel)} jobs have no geometry-cache entry', flush=True)
        if args.sample:
            # EVENLY SPACED, not the head and not random: jobs are dispatched
            # grouped by worldspace, so the first N are all one region (and
            # often all interiors).  A fixed stride covers the whole
            # population and is reproducible run to run.
            step = max(1, len(sel) // args.sample)
            sel = sel[::step][:args.sample]
        else:
            sel = sel[args.start:args.start + args.max]

    if args.no_cache:
        # COLD-CACHE timing: the geometry cache turns a real build into a
        # pickle load, so any run that hits it measures I/O, not generation.
        # Disabling it is the only way to time what an unprimed machine (or a
        # navmesh code change, which the input-keyed hash cannot see) pays.
        geom_cache = None
        print('  geometry cache DISABLED (cold-cache timing)', flush=True)

    print(f'\nrunning {len(sel)} job(s) in-process...', flush=True)
    failed = 0
    times = []
    for i, job in sel:
        cell, pgrd = job['key']
        _t0 = time.time()
        # Flushed BEFORE the call on purpose: if the native code aborts the
        # process, this is the line that names the culprit.
        print(f'  job[{i}] cell {cell:08X} pgrd {pgrd:08X} '
              f'refrs={len(job["refr_recs"])} '
              f'land={"Y" if job["land_rec"] is not None else "N"}', flush=True)
        try:
            nb, meta = convert_PGRD(
                job['pgrd_rec'], land_rec=job['land_rec'],
                cell_rec=job['cell_rec'], refr_recs=job['refr_recs'],
                base_model_by_fid=base_model_by_fid, door_fids=door_fids,
                navm_fid=job['navm_fid'], geom_cache=geom_cache,
                extra_door_refrs=job.get('extra_door_refrs'))
        except Exception:
            failed += 1
            traceback.print_exc()
            sys.stdout.flush()
            continue
        _dt = time.time() - _t0
        times.append(_dt)
        if nb is None:
            print(f'      -> no navmesh (no usable geometry) [{_dt:.2f}s]',
                  flush=True)
        else:
            m = meta or {}
            extra = []
            if m.get('geom_cached'):
                extra.append('geometry from cache')
            if m.get('door_refs'):
                extra.append(f'{len(m["door_refs"])} door links')
            print(f'      -> {len(nb)} bytes'
                  + (f' ({", ".join(extra)})' if extra else '')
                  + f' [{_dt:.2f}s]', flush=True)

    print(f'\n{len(sel) - failed}/{len(sel)} ran without raising', flush=True)
    if times:
        ts = sorted(times)
        n = len(ts)
        total = sum(ts)
        print('\nPER-JOB SECONDS  n=%d  total=%.1fs  mean=%.2f  '
              'p50=%.2f  p90=%.2f  max=%.2f'
              % (n, total, total / n, ts[n // 2], ts[min(n - 1, int(n * .9))],
                 ts[-1]))
        # Extrapolate to the whole plugin: the pool runs `workers` jobs at a
        # time, so wall-clock is (mean * jobs) / workers -- an ESTIMATE that
        # ignores scheduling tail and per-worker startup.
        if args.extrapolate:
            jobs_n, workers = args.extrapolate
            est = (total / n) * jobs_n / max(1, workers)
            print('  extrapolated: %d jobs / %d workers -> %.1f min'
                  % (jobs_n, workers, est / 60.0))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
