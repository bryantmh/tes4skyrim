"""Pick which navmesh cells get re-built and compared against the cache.

The cache tag hashes the navmesh sources, so it cannot see a shapely/GEOS or
`.pyd` change -- and once a cache may be ADOPTED (re-keyed after proving the
geometry is unchanged) a matching tag no longer proves the entries were produced
by the current code.  Re-building a sample of cache hits on every import is what
restores the "a wrong cache is slow, never incorrect" guarantee.

See: docs/commentary/tes5_import_navmesh.md#verifying-a-cache-against-fresh-geometry
"""

import os
from collections import defaultdict

#: Cells re-built and compared against the cache on every import, by default.
VERIFY_DEFAULT = 40

#: Env var overriding VERIFY_DEFAULT; 0 disables verification entirely.
VERIFY_ENV_VAR = 'TESCONV_NAVMESH_VERIFY'


def verify_budget(explicit: int = None) -> int:
    """How many cells to verify: explicit value, else the env var, else default."""
    if explicit is not None:
        return max(0, explicit)
    raw = os.environ.get(VERIFY_ENV_VAR, '').strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return VERIFY_DEFAULT


def _stratum(job: dict) -> str:
    """Which sampling bucket this navmesh job belongs to."""
    if job.get('extra_door_refrs'):
        return 'door'
    if job.get('land_rec') is not None:
        return 'exterior'
    return 'crowded' if len(job.get('refr_recs') or ()) >= 100 else 'interior'


def mark_jobs(jobs: list, budget: int) -> int:
    """Flag up to *budget* jobs with job['verify'], spread across strata.

    Returns how many were flagged.  The PARENT owns this choice: `initargs` are
    copied into every worker, so a per-worker budget would multiply by the
    worker count and rebuild a large fraction of the cache instead of a sample.
    """
    if budget <= 0 or not jobs:
        return 0
    buckets = defaultdict(list)
    for i, job in enumerate(jobs):
        buckets[_stratum(job)].append(i)
    chosen = []
    names = sorted(buckets)
    per = max(1, budget // len(names))
    for name in names:
        idx = buckets[name]
        step = max(1, len(idx) // per)
        chosen.extend(idx[::step][:per])
    picked = sorted(set(chosen))[:budget]
    for i in picked:
        jobs[i]['verify'] = True
    return len(picked)


def report(cache: dict) -> list:
    """Cells whose cached geometry did not reproduce, as [(key, meta), ...]."""
    return [(key, m) for key, (_b, m) in cache.items()
            if m and m.get('verify_mismatch')]


def verified_count(cache: dict) -> int:
    """How many cells actually ran a verification build."""
    return sum(1 for (_b, m) in cache.values() if m and m.get('verified'))


def uncertify(geom_cache) -> bool:
    """Drop CACHE_TAG so the next run does not trust a cache that failed.

    Only the stamp goes.  THIS run has already read most of the entries, and
    each verified cell that mismatched kept its own fresh build, so deleting
    them mid-run would strand the reads still to come while fixing nothing.  An
    unstamped cache is what `verify` and the pre-push gate call stale -- exactly
    the state a cache that failed to reproduce belongs in.
    """
    if not geom_cache:
        return False
    try:
        os.remove(os.path.join(geom_cache[0], 'CACHE_TAG'))
        return True
    except OSError:
        return False


def report_failures(cache: dict) -> bool:
    """Print cells that produced no navmesh.  True if any did.

    Reported in the PARENT: workers run under pythonw.exe where stdout goes
    nowhere, so a failed cell would otherwise vanish silently and leave the
    plugin a navmesh short with nothing in the log.  A run with failures must
    not stamp the cache -- entries are missing, and stamping would advertise a
    partial cache as a full one to anyone who downloads it.
    """
    failures = [(key, m) for key, (b, m) in cache.items()
                if b is None and m and m.get('error')]
    if not failures:
        return False
    print('    WARNING: %d cells produced no navmesh:' % len(failures))
    for (cell_fid, pgrd_fid), m in failures[:20]:
        print('      cell %08X pgrd %08X: %s'
              % (cell_fid, pgrd_fid, m['error']))
    if len(failures) > 20:
        print('      ... and %d more' % (len(failures) - 20))
    return True


def report_verification(cache: dict, geom_cache) -> bool:
    """Print the verification result; unstamp a cache that did not reproduce.

    True when a mismatch was found.  The plugin is still correct either way:
    a mismatching cell keeps the fresh build made to test it.

    A mismatch means the cached geometry is not what this code builds -- which
    the tag cannot detect, since it hashes sources rather than output.
    """
    checked = verified_count(cache)
    if not checked:
        return False
    bad = report(cache)
    if not bad:
        print('    Navmesh cache verified: %d cells rebuilt, all identical.'
              % checked)
        return False
    print('    WARNING: navmesh cache FAILED verification -- %d/%d rebuilt '
          'cells differ from the cached geometry.' % (len(bad), checked))
    for (cell_fid, pgrd_fid), _m in bad[:10]:
        print('      cell %08X pgrd %08X' % (cell_fid, pgrd_fid))
    uncertify(geom_cache)
    print('      Mismatched cells used their FRESH build, so this plugin is '
          'correct; the cache is now marked stale.')
    return True


def prove_cache(jobs: list, geom_cache, sample: int, quiet: bool = False):
    """Rebuild a sample and compare against the STORED payload.  (checked, bad).

    Ignores each entry's stored hash: that hash is exactly what a source change
    invalidated, so requiring it to match would refuse before comparing a
    single vertex.  Geometry is the authority on whether a cache still applies.
    """
    from . import navm_worker
    from .pgrd_to_navm import cached_geometry, geom_equal
    picked = list(jobs)
    mark_jobs(picked, sample)
    checked, bad = 0, []
    for job in [j for j in picked if j.get('verify')]:
        stored = cached_geometry(geom_cache, *job['key'])
        if stored is None:
            continue
        key, (_b, meta) = navm_worker.run_job(job)
        fresh = (meta or {}).get('geometry')
        if fresh is None:
            continue
        checked += 1
        ok = geom_equal(stored, fresh)
        if not ok:
            bad.append(key)
        if not quiet:
            print('      %08X %s' % (key[0], 'identical' if ok else 'MISMATCH'),
                  flush=True)
    return checked, bad


def _rekey_one(path: str, want: str) -> bool:
    """Rewrite one entry's stored hash in place.  True when it changed."""
    import pickle
    try:
        with open(path, 'rb') as fh:
            blob = pickle.load(fh)
        if blob.get('hash') == want:
            return False
        blob['hash'] = want
        tmp = '%s.tmp%d' % (path, os.getpid())
        with open(tmp, 'wb') as fh:
            pickle.dump(blob, fh, pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def rekey_cache(jobs: list, geom_cache) -> tuple:
    """Re-key every entry that has a job to the CURRENT tag.  (done, skipped).

    Hashes come from `cell_geom_key`, so no geometry is built.
    """
    from . import navm_worker
    from .pgrd_to_navm import cell_geom_key
    cache_dir = geom_cache[0]
    by_key = {j['key']: j for j in jobs}
    done = skipped = 0
    for name in sorted(os.listdir(cache_dir)):
        if not name.endswith('.pkl'):
            continue
        try:
            key = tuple(int(x, 16) for x in name[:-4].split('_'))
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
                              navm_worker._DOOR_FIDS, geom_cache,
                              job.get('extra_door_refrs'))
        if not fresh:
            skipped += 1
        elif _rekey_one(os.path.join(cache_dir, name), fresh):
            done += 1
    return done, skipped


def adopt_if_unchanged(jobs: list, geom_cache, sample: int = None) -> bool:
    """Salvage a stale cache whose geometry still reproduces.  True if adopted.

    Called before the navmesh pool dispatches.  A source edit moves the tag, so
    every entry misses and an output-neutral refactor costs a FULL
    regeneration; proving a sample and re-keying makes it a sample plus a hash
    rewrite.  A cache that does not reproduce is left alone.  False (no work)
    when CACHE_TAG already matches: that cache is this code's own.

    See: docs/commentary/tes5_import_navmesh.md#verifying-a-cache-against-fresh-geometry
    """
    if geom_cache is None:
        return False
    budget = verify_budget() if sample is None else sample
    if budget <= 0:
        return False
    cache_dir, tag = geom_cache
    try:
        with open(os.path.join(cache_dir, 'CACHE_TAG')) as fh:
            if fh.read().strip() == tag:
                return False
    except OSError:
        pass
    if not any(n.endswith('.pkl') for n in os.listdir(cache_dir)):
        return False
    print('  Navmesh cache: built by different navmesh code -- checking '
          'whether its geometry still reproduces (%d cells)...' % budget,
          flush=True)
    checked, bad = prove_cache(jobs, geom_cache, budget)
    if not checked:
        print('    no comparable entries; regenerating.', flush=True)
        return False
    if bad:
        print('    %d/%d differ -- a real geometry change; regenerating.'
              % (len(bad), checked), flush=True)
        return False
    done, _skipped = rekey_cache(jobs, geom_cache)
    try:
        with open(os.path.join(cache_dir, 'CACHE_TAG'), 'w') as fh:
            fh.write(tag)
    except OSError:
        pass
    print('    %d/%d identical; adopted %d entries instead of regenerating.'
          % (checked, checked, done), flush=True)
    for job in jobs:
        job.pop('verify', None)
    return True


def prepare(jobs: list, geom_cache) -> None:
    """Ready the navmesh cache for this run: adopt if salvageable, then sample.

    The one call the import makes.  Adoption rescues a cache whose tag moved
    but whose geometry still reproduces; marking then picks the cells this run
    re-verifies.
    """
    if not geom_cache:
        return
    from . import navm_worker
    if navm_worker._GEOM_CACHE != geom_cache:
        raise RuntimeError(
            'navmesh worker context must be initialized before cache prepare')
    adopt_if_unchanged(jobs, geom_cache)
    mark_jobs(jobs, verify_budget())
