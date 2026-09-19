#!/usr/bin/env python
"""Re-key a navmesh cache the current code did not build, once proven identical.

The cache tag hashes the navmesh SOURCES, so a rename, a docstring edit or a
file split invalidates all ~8,200 entries even though the geometry is unchanged.
Regenerating then costs ~95 minutes to arrive back at the bytes already on disk.

    python tools/navmesh/navmesh_adopt.py --plugin Oblivion.esm --dry-run
    python tools/navmesh/navmesh_adopt.py --plugin Oblivion.esm

Phase A  rebuild a stratified sample and compare; any mismatch REFUSES.
Phase B  re-key every entry to the hash the current code asks for, then stamp.

Phase B gets each cell's new hash from `meta['geom_hash']` -- the value
convert_PGRD just computed -- rather than re-deriving the pathgrid, doors and
origin here, which would duplicate ~100 lines of convert_PGRD and drift from it.

Publishing afterwards is the ordinary `navmesh_cache.py publish` path: after
Phase B the local cache IS the correctly-keyed copy.

See: docs/commentary/tes5_import_navmesh.md#verifying-a-cache-against-fresh-geometry
"""
from __future__ import annotations

import argparse
import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from tools.navmesh import job_trace, navmesh_cache as nc
from tes5_import.navmesh import cache_audit as navm_verify

ADOPT_OK = 0
ADOPT_REFUSED = 1
ADOPT_NOTHING = 2

#: Cells rebuilt and compared before a cache may be re-keyed.
SAMPLE_DEFAULT = 40


def environment() -> dict:
    """Versions that change generated geometry without moving the source tag."""
    info = {'python': '%d.%d' % sys.version_info[:2]}
    try:
        import shapely
        info['shapely'] = shapely.__version__
        info['geos'] = '.'.join(str(x) for x in shapely.geos_version)
    except Exception:
        pass
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    try:
        for name in sorted(os.listdir(os.path.join(root, 'native', 'dist'))):
            if name.startswith('_navgrow_native') and name.endswith('.pyd'):
                info['navgrow'] = name
    except OSError:
        pass
    return info


def entry_paths(cache_dir: str) -> list:
    """Every .pkl in a cache directory, sorted."""
    return sorted(os.path.join(cache_dir, n) for n in os.listdir(cache_dir)
                  if n.endswith('.pkl'))


def rekey(path: str, want: str) -> bool:
    """Rewrite one entry's stored hash in place.  True when it changed."""
    try:
        with open(path, 'rb') as fh:
            blob = pickle.load(fh)
    except Exception:
        return False
    if blob.get('hash') == want:
        return False
    blob['hash'] = want
    tmp = '%s.tmp%d' % (path, os.getpid())
    with open(tmp, 'wb') as fh:
        pickle.dump(blob, fh, pickle.HIGHEST_PROTOCOL)
    os.replace(tmp, path)
    return True


def load_plugin(plugin: str, offset: int = 1):
    """(import_main, jobs, geom_cache) with the worker initialised for it.

    `offset` MUST match the load-order index the cache was generated under
    (import_main sets it to len(masters) - num_tes4_masters; 1 for a standalone
    plugin behind Skyrim.esm).  A wrong offset changes every FormID that feeds
    the per-cell hash, so every entry misses and adoption sees nothing to
    compare.
    """
    export_dir = os.path.join('export', plugin)
    im, _bt, door_fids, base_model_by_fid, jobs = job_trace.load_export(
        export_dir, offset)
    geom_cache = job_trace.init_worker_for(export_dir, im, door_fids,
                                           base_model_by_fid, offset)
    return im, jobs, geom_cache


def commit(jobs: list, cache_dir: str) -> tuple:
    """Re-key every entry that has a job.  (re-keyed, skipped).

    Hashes come from `cell_geom_key`, so NO geometry is built.  The caller
    stamps CACHE_TAG only after this returns, leaving an interrupted run
    unstamped -- read as stale, which is safe.

    See: docs/commentary/tes5_import_navmesh.md#verifying-a-cache-against-fresh-geometry
    """
    from tes5_import.navmesh import worker as navm_worker
    from tes5_import.navmesh.from_pgrd import cell_geom_key
    by_key = {j['key']: j for j in jobs}
    done = skipped = 0
    for path in entry_paths(cache_dir):
        stem = os.path.basename(path)[:-4]
        try:
            key = tuple(int(x, 16) for x in stem.split('_'))
        except ValueError:
            skipped += 1
            continue
        job = by_key.get(key)
        if job is None:
            skipped += 1
            continue
        fresh = cell_geom_key(job['pgrd_rec'], job['land_rec'],
                              job['cell_rec'], job['refr_recs'],
                              navm_worker._BASE_MODEL_BY_FID,
                              navm_worker._DOOR_FIDS, navm_worker._GEOM_CACHE,
                              job.get('extra_door_refrs'))
        if not fresh:
            skipped += 1
            continue
        if rekey(path, fresh):
            done += 1
    return done, skipped


def adopt(plugin: str, sample: int = SAMPLE_DEFAULT,
          dry_run: bool = False,
          offset: int = 1) -> int:
    """Prove the cache reproduces, then re-key it to the current source tag."""
    want = nc.source_tag(plugin)
    cache_dir = nc.cache_dir(plugin)
    if not want or not os.path.isdir(cache_dir) or not entry_paths(cache_dir):
        print('%s: no cache to adopt (%s).' % (plugin, cache_dir))
        return ADOPT_NOTHING

    n_entries = len(entry_paths(cache_dir))
    print('%s: %d entries, target tag %s' % (plugin, n_entries, want[:12]))
    print('  environment: %s' % environment())
    print('  proving %d sampled cells reproduce...' % sample, flush=True)
    _im, jobs, geom_cache = load_plugin(plugin, offset)
    checked, bad = navm_verify.prove_cache(jobs, geom_cache, sample)
    if not checked:
        print('  REFUSED: no sampled cell had a cache entry to compare.')
        return ADOPT_REFUSED
    if bad:
        print('  REFUSED: cell %08X differs after %d compared -- a REAL '
              'behaviour change, not a refactor.' % (bad[0][0], checked))
        print('  Regenerate instead: python convert.py -f "%s" --import-only'
              % plugin)
        return ADOPT_REFUSED
    print('  %d/%d sampled cells identical.' % (checked, checked))
    if dry_run:
        print('  --dry-run: nothing written.')
        return ADOPT_OK

    done, skipped = commit(jobs, cache_dir)
    with open(os.path.join(cache_dir, 'CACHE_TAG'), 'w') as fh:
        fh.write(want)
    print('  re-keyed %d entries (%d skipped); stamped %s'
          % (done, skipped, want[:12]))
    print('  publish with: python tools/navmesh/navmesh_cache.py publish '
          '--tag <version>')
    return ADOPT_OK


def _run_each_in_its_own_process(plugins: list, args) -> int:
    """Adopt each plugin in a FRESH interpreter; return the worst status.

    Per-plugin state lives in module globals, so a second plugin loaded into
    the same process builds against the first one's data and REFUSES falsely.

    See: docs/commentary/tes5_import_navmesh.md#adoption-needs-one-process-per-plugin
    """
    import subprocess
    worst = ADOPT_OK
    for plugin in plugins:
        cmd = [sys.executable, os.path.abspath(__file__),
               '--plugin', plugin, '--sample', str(args.sample),
               '--offset', str(args.offset)]
        if args.dry_run:
            cmd.append('--dry-run')
        worst = max(worst, subprocess.run(cmd).returncode)
    return worst


def main(argv=None) -> int:
    """CLI entry point.  Returns the worst per-plugin adopt status."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--plugin', action='append', dest='plugins',
                    help='plugin folder under export/ (repeatable)')
    ap.add_argument('--sample', type=int, default=SAMPLE_DEFAULT,
                    help='cells to rebuild and compare (default 40)')
    ap.add_argument('--dry-run', action='store_true',
                    help='prove only; write nothing')
    ap.add_argument('--offset', type=int, default=1,
                    help='load-order index offset the cache was built with '
                         '(default 1: one new master ahead of the plugin)')
    args = ap.parse_args(argv)
    plugins = args.plugins or nc.discover_plugins()
    if not plugins:
        print('No plugins with a navmesh cache found under export/.')
        return 1
    if len(plugins) > 1:
        return _run_each_in_its_own_process(plugins, args)
    return adopt(plugins[0], args.sample, args.dry_run, args.offset)


if __name__ == '__main__':
    raise SystemExit(main())
