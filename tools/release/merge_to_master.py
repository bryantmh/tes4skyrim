#!/usr/bin/env python
"""Merge the current branch into master locally, with the navmesh cache in step.

The navmesh cache gate is a PRE-PUSH hook, so it only ever sees a direct push
to master -- a merge performed on GitHub runs no local hook and would publish
nothing.  This does the merge here, where the hook can run.

    python tools/release/merge_to_master.py --dry-run   # what would happen
    python tools/release/merge_to_master.py             # merge, then push

Order matters, and it is the opposite of the obvious one: the cache is settled
AFTER the merge.  Adoption hashes the WORKING TREE's navmesh sources, and the
merge is what brings the branch's navmesh changes into that tree -- so a cache
adopted first is already stale when the pre-push gate looks at it, and the push
is blocked despite a clean adoption moments earlier.  (Measured: a merge at
00:17 adopted cleanly beforehand, was refused, and only pushed after the gate
re-adopted at 00:22.)

Nothing here force-pushes, rebases, or touches the index beyond the merge
itself.  A dirty tree aborts: a merge is hard to unpick, and uncommitted work
in these files is exactly what would make it ambiguous.

See: docs/commentary/tes5_import_navmesh.md#the-shared-navmesh-cache--design-rationale
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from core.subprocess_flags import run_streamed

MASTER = 'master'


def repo_root() -> str:
    """The repository root, three levels above this file."""
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))


def git(*args, check=False):
    """Run a git command in the repo root, capturing its output."""
    return subprocess.run(['git', *args], cwd=repo_root(), check=check,
                          capture_output=True, text=True)


def pushed_ok() -> bool:
    """Is local master already on origin?  The truth about a push's outcome.

    git can exit non-zero on a push it actually delivered, so the REF is the
    authority on whether the work landed -- not the exit code.
    """
    git('fetch', 'origin', MASTER)
    out = git('rev-list', '--left-right', '--count',
              'origin/%s...%s' % (MASTER, MASTER)).stdout.split()
    return len(out) == 2 and out[1] == '0'


def current_branch() -> str:
    """The branch HEAD is on, or '' when detached."""
    out = git('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip()
    return '' if out == 'HEAD' else out


def is_dirty() -> bool:
    """True when the working tree or index has uncommitted changes."""
    return bool(git('status', '--porcelain').stdout.strip())


def cache_is_current() -> bool:
    """Does every publishable cache match the working tree's navmesh sources?"""
    from tools.navmesh import navmesh_cache as nc
    return all(nc.cache_matches_tag(p, nc.source_tag(p))
               for p in nc.discover_plugins())


def ensure_cache(sample: int, dry_run: bool) -> bool:
    """Adopt any stale cache; True when every cache ends up current."""
    if cache_is_current():
        print('navmesh cache: already current.')
        return True
    print('navmesh cache: stale -- attempting adoption '
          '(proves the geometry is unchanged, then re-keys).')
    if dry_run:
        print('  --dry-run: would run tools/navmesh/navmesh_adopt.py')
        return False
    rc = subprocess.run(
        [sys.executable, os.path.join('tools', 'navmesh', 'navmesh_adopt.py'),
         '--sample', str(sample)]).returncode
    if rc != 0 or not cache_is_current():
        print('')
        print('navmesh cache: adoption did not settle every cache.')
        print('A cache that will not adopt has REAL geometry changes; '
              'regenerate it:')
        print('  python convert.py -f "<plugin>" --import-only')
        return False
    return True


def do_merge(branch: str, sample: int, dry_run: bool) -> int:
    """Merge *branch* into master, settle the cache, then push.

    Adoption runs AFTER the merge, never before: the merge is what brings the
    navmesh source changes into the tree, so a cache adopted beforehand is
    already stale by the time the pre-push gate looks at it -- which is exactly
    how a merge that had just adopted cleanly still had its push blocked.
    """
    steps = [
        ('checkout master', ('checkout', MASTER)),
        ('merge %s' % branch, ('merge', '--no-ff', branch,
                               '-m', 'Merge branch %r' % branch)),
        ('settle cache', None),
        ('push master', ('push', 'origin', MASTER)),
    ]
    for label, args in steps:
        if args is None:
            print('\n# navmesh cache, now that the merge has landed')
            if not dry_run and not ensure_cache(sample, dry_run):
                return 1
            continue
        print('\n$ git %s' % ' '.join(args))
        if dry_run:
            continue
        output, rc = run_streamed(['git', *args], cwd=repo_root())
        if rc == 0:
            continue
        if args[0] == 'push' and pushed_ok():
            print('\ngit exited %d but master IS up to date on origin -- '
                  'treating the push as done.' % rc)
            continue
        print('\nFAILED at: %s (git exited %d)' % (label, rc))
        print('--- git said ---')
        print(output.strip() or '(no output)')
        print('----------------')
        if args[0] == 'merge':
            print('Resolve the conflict, then re-run; or `git merge --abort` '
                  'to back out.')
        else:
            print('The merge is committed locally. Re-run this script, or '
                  '`git push origin %s`.' % MASTER)
            print('The pre-push hook publishes the navmesh cache, so do not '
                  'bypass it with --no-verify.')
        return rc
    return 0


def main(argv=None) -> int:
    """Entry point.  Returns 0 when the merge (or dry run) succeeded."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--branch', help='branch to merge (default: current)')
    ap.add_argument('--sample', type=int, default=40,
                    help='cells adoption rebuilds to prove the cache (40)')
    ap.add_argument('--dry-run', action='store_true',
                    help='print every step without running it')
    args = ap.parse_args(argv)

    branch = args.branch or current_branch()
    if not branch:
        print('HEAD is detached; check out a branch first.')
        return 1
    if branch == MASTER:
        print('Already on %s -- nothing to merge.' % MASTER)
        return 1
    if is_dirty():
        print('Working tree is dirty. Commit or set your changes aside first:')
        print(git('status', '--short').stdout)
        return 1

    print('Merging %r into %s.' % (branch, MASTER))
    return do_merge(branch, args.sample, args.dry_run)


if __name__ == '__main__':
    raise SystemExit(main())
