#!/usr/bin/env python
"""pre-push gate: never push navmesh changes without refreshing the shared cache.

Installed as a `.git/hooks/pre-push` step (see `--install`).  On a push to
master that touches navmesh generation, it verifies the local geometry cache
was rebuilt against the NEW sources and, if so, publishes it as a release asset.

    python tools/navmesh/navmesh_cache_hook.py --install     # wire into .git/hooks
    python tools/navmesh/navmesh_cache_hook.py --check       # what would happen?
    python tools/navmesh/navmesh_cache_hook.py --run         # publish now (manual)

WHY A HOOK AND NOT CI
---------------------
The cache is built from `export/`, which is gitignored and hundreds of MB --
it exists only on a machine that has actually run the pipeline against a real
Oblivion install.  A GitHub Actions runner has neither the game nor the export,
so it cannot regenerate or validate the cache.  The only place the check can
run is the machine that did the conversion.

This means it only fires on a DIRECT push to master, not on pull requests -- a
PR merged through GitHub's UI never runs a local hook.  That is a real gap and
it is accepted deliberately: the alternative is no automation at all.  `--run`
covers the PR case manually, and `verify` is cheap enough to run any time.

WHAT COUNTS AS A NAVMESH CHANGE
-------------------------------
Exactly the files whose contents feed the cache tag
(import_main._navmesh_geom_cache): `tes5_import/navmesh/*.py` and
`tes5_import/navmesh/from_pgrd.py`.  Editing any of them changes the tag and
invalidates every entry, so the published cache MUST be rebuilt or it is dead
weight for every downloader.  `_geom_hash` also consumes collision geometry,
but per-mesh and via the export, so asset-side edits are not gated here.

WHICH PLUGINS
-------------
Only the ones we actually host: `navmesh_cache.PUBLISHABLE_PLUGINS`
(Oblivion, Nehrim, Morrowind_ob).  Every other cache under `export/` is a
local experiment -- a DLC, a landmass mod, a plugin run once to test
something.  Gating on those was actively harmful in both directions: a
throwaway run leaves a 0-entry cache that fails `verify` and BLOCKS the push,
and a large one gets zipped and uploaded as a release asset no downloader
wants.  `discover_plugins()` filters to the publishable set for exactly this.

WHY IT BLOCKS
-------------
A push that changes navmesh code without a matching cache leaves the published
asset silently useless: every entry misses, and downloaders pay full
generation while believing they have a cache.  Blocking is the only way the
mistake surfaces before the release rather than after.
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.navmesh import navmesh_cache as nc

#: Sources whose bytes feed the cache tag; a test asserts they match pool.
NAVMESH_PATHS = ('tes5_import/navmesh/', 'native/src/navgrow/grow.cpp')


def _tag_exclude() -> tuple[str, ...]:
    """pool._TAG_EXCLUDE as repo paths, read from source so the hook stays import-free."""
    pool = os.path.join(nc.repo_root(), 'tes5_import', 'navmesh', 'pool.py')
    with open(pool, encoding='utf-8') as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and getattr(node.targets[0], 'id', None) == '_TAG_EXCLUDE'):
            names = ast.literal_eval(node.value.args[0])
            return tuple(sorted('tes5_import/navmesh/' + n for n in names))
    raise LookupError('_TAG_EXCLUDE not found in ' + pool)


#: Under the prefix but NOT a tag source -- mirrors pool._TAG_EXCLUDE.
NAVMESH_EXCLUDE = _tag_exclude()

#: Non-tag files that change caching. See: docs/commentary/tes5_import_navmesh.md#what-gates-a-push
NAVMESH_FUNCS = {
    'asset_convert/collision/collision_extract.py': frozenset({
        'collision_digest',       # the per-mesh digest every cell hash consumes
        'collision_content_hash',
        '_serialize', '_deserialize',   # change the bytes a digest is taken over
        'load_collision',
    }),
}

HOOK_MARKER = '# >>> navmesh-cache-gate >>>'
HOOK_PRELUDE_MARKER = '# >>> navmesh-cache-stdin >>>'
HOOK_PRELUDE_END = '# <<< navmesh-cache-stdin <<<'
# git feeds the refs being pushed on STDIN, one
# "<local ref> <local sha> <remote ref> <remote sha>" line each.  The gate has
# to prompt and publish interactively, so python gets </dev/null -- which means
# python can never read that list itself.  So the ref list is captured into a
# variable and forwarded as --ref arguments.  (Passing them on stdin and
# redirecting were mutually exclusive; this hook previously did both and so
# gated EVERY push, on every branch, instead of only master.)
#
# The capture MUST happen before any other hook step, because stdin is a PIPE
# and therefore read-once: `git lfs pre-push` -- which --install appends after,
# and which ships here by default -- drains it to EOF.  A capture placed after
# it read nothing, forwarded no --ref, and pushed_refs() then returned None,
# which gates by design ("unreadable must gate rather than skip").  That made
# the gate fire on EVERY branch push whenever navmesh sources changed, exactly
# the bug the ref forwarding was added to fix.  The prelude is installed as a
# separate marked block at the TOP of the hook, and replays the captured lines
# into the rest of the hook so git-lfs still receives them.
HOOK_PRELUDE = '''%s
# Added by tools/navmesh/navmesh_cache_hook.py --install
# stdin is a read-once pipe and later steps (git-lfs) drain it, so capture the
# pushed-ref list HERE, before anything else, and replay it downstream.
navmesh_stdin=$(cat)
navmesh_refs=$(echo "$navmesh_stdin" | awk '$3 != "" {printf " --ref %%s", $3}')
%s
''' % (HOOK_PRELUDE_MARKER, HOOK_PRELUDE_END)
HOOK_SNIPPET = '''%s
# Added by tools/navmesh/navmesh_cache_hook.py --install
# Blocks a push to master that changes navmesh generation unless the shared
# geometry cache has been rebuilt, then publishes it as a release asset.
# $navmesh_refs was captured by the stdin prelude at the top of this hook,
# before git-lfs (or any other step) could drain the read-once pipe.
# shellcheck disable=SC2086
python tools/navmesh/navmesh_cache_hook.py --pre-push $navmesh_refs "$@" </dev/null || exit 1
# <<< navmesh-cache-gate <<<
''' % HOOK_MARKER


def git(*args: str) -> str:
    return subprocess.run(['git', *args], capture_output=True, text=True,
                          cwd=nc.repo_root()).stdout.strip()


def changed_paths(base: str, head: str) -> list[str]:
    out = git('diff', '--name-only', '%s..%s' % (base, head))
    return [p for p in out.splitlines() if p]


_HUNK_FUNC = re.compile(r"^@@ .*? @@\s*(?:def\s+)?([A-Za-z_]\w*)")


def touches_navmesh(paths: list[str], base: str = None,
                    head: str = 'HEAD') -> list[str]:
    """Which changed paths actually invalidate cached navmesh geometry.

    Files in NAVMESH_PATHS feed the tag directly, so any edit counts.  Files in
    NAVMESH_FUNCS only count when a hunk lands in one of the listed functions --
    otherwise an unrelated dialogue fix in import_main.py would block the push.
    """
    hits = [p for p in paths
            if any(p.startswith(n) or p == n for n in NAVMESH_PATHS)
            and p not in NAVMESH_EXCLUDE]
    for path in paths:
        funcs = NAVMESH_FUNCS.get(path)
        if funcs and (base is None
                      or _hunks_touch_funcs(path, funcs, base, head)):
            hits.append(path)
    return hits


def _hunks_touch_funcs(path: str, funcs: frozenset, base: str,
                       head: str) -> bool:
    """Does any hunk in *path* fall inside one of *funcs*?

    A hunk git cannot attribute (module scope, imports, constants) is treated
    as a HIT: it could be a shared constant the cache depends on, and a false
    block costs one --no-verify while a false pass ships a dead cache.
    """
    diff = git('diff', '-U0', '%s..%s' % (base, head), '--', path)
    if not diff:
        return False
    for line in diff.splitlines():
        if not line.startswith('@@'):
            continue
        m = _HUNK_FUNC.match(line)
        if not m:
            return True                 # unattributable -- assume it matters
        if m.group(1) in funcs:
            return True
    return False


def remote_master_base() -> str:
    """Best-effort merge-base with the pushed branch's upstream."""
    for ref in ('origin/master', 'master@{u}'):
        base = git('merge-base', 'HEAD', ref)
        if base:
            return base
    # No upstream (first push): fall back to the previous commit so the check
    # still inspects something rather than silently passing.
    return git('rev-parse', 'HEAD~1') or git('rev-parse', 'HEAD')


def next_tag() -> str:
    """The tag tag-on-push.yml will create for this push.

    Mirrors that workflow: tags are MAJOR.MMM, and the next one is the highest
    existing tag plus one thousandth.  The published asset is stamped with this
    so users know the first version it applies to.

    THREE DIGITS, SCALED BY WIDTH.  Tags up to 0.58 used two minor digits
    (MAJOR.MM, hundredths); everything from 0.581 on uses three (thousandths).
    A 2-digit tag is therefore worth TEN of the new units -- 0.58 is 0.580 --
    so the width of the minor field, not a fixed multiplier, decides the scale.
    Reading a legacy '0.58' as 58 thousandths instead of 580 would compute a
    "next" tag of 0.059, jumping the version backwards past 500 existing names.

    FETCH FIRST.  The workflow runs `git fetch --tags --force` before it
    computes anything, so it sees tags this clone may not have.  Reading only
    local refs predicts a number that is already taken -- measured: local
    topped out at 0.55 while the remote already had 0.56, so the cache would
    have been published as '0.56+' while CI tagged the code 0.57, labelling the
    cache one version behind the code it was built for.  A failed fetch (no
    network, no remote) falls back to local refs rather than blocking a push.
    """
    subprocess.run(['git', 'fetch', '--tags', '--force', '--quiet'],
                   capture_output=True, cwd=nc.repo_root(), timeout=60)
    tags = [t for t in git('tag', '-l').splitlines() if t]
    taken = set(tags)
    mils = 0
    for t in tags:
        parts = t.split('.')
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        # Scale by the minor field's width: 2 digits are hundredths (x10 to
        # reach thousandths), 3 digits are already thousandths.  Anything else
        # is not ours -- ignore it rather than misread its magnitude.
        if len(parts[1]) == 2:
            value = int(parts[0]) * 1000 + int(parts[1]) * 10
        elif len(parts[1]) == 3:
            value = int(parts[0]) * 1000 + int(parts[1])
        else:
            continue
        mils = max(mils, value)
    # Skip past any name already taken, exactly as the workflow's loop does (a
    # tag created out of order, or a race the concurrency group missed).
    while True:
        mils += 1
        candidate = '%d.%03d' % (mils // 1000, mils % 1000)
        if candidate not in taken:
            return candidate


def check(verbose: bool = True) -> tuple[bool, list[str], list[str]]:
    """(gated, navmesh_files_changed, plugins_with_stale_cache)."""
    base = remote_master_base()
    touched = touches_navmesh(changed_paths(base, 'HEAD'), base=base)

    # Run the check even when nothing obvious was touched.  The stamp
    # comparison is a single file read per plugin, and it catches the cases the
    # path list cannot see: a navmesh change that arrived via a merge, an
    # indirect edit (a constant in a module the tag hashes), or a cache that
    # was simply never rebuilt.  A cache that is already correct passes here,
    # so the only cost of checking always is the read itself.
    drifted = [p for p in nc.discover_plugins()
               if not cache_matches_tag(p, nc.source_tag(p))]

    if not touched and not drifted:
        if verbose:
            print('navmesh-cache: no navmesh source changes in %s..HEAD, '
                  'and every cache matches the current sources.' % base[:8])
        return False, [], []

    if verbose:
        if touched:
            print('navmesh-cache: navmesh sources changed:')
            for p in touched:
                print('  %s' % p)
        else:
            print('navmesh-cache: no navmesh source change in this push, but a '
                  'cache does not match the current sources:')
            for p in drifted:
                print('  %s' % p)

    stale = []
    for plugin in nc.discover_plugins():
        info = nc.verify(plugin, quiet=not verbose)
        # A cache is fresh iff it verifies AND was produced by these sources.
        # verify() reports the tag the CURRENT code computes; entries built by
        # older code carry a different tag inside each pickle and will miss.
        if not info['ok']:
            stale.append(plugin)
            continue
        if not cache_matches_tag(plugin, info['tag']):
            stale.append(plugin)
    return True, touched, stale


def cache_matches_tag(plugin: str, tag: str | None) -> bool:
    """Was the cache built by the CURRENT navmesh code?

    Exact, not heuristic: the import stamps the tag it used into
    `navmesh_geom_cache/CACHE_TAG` (import_main._navmesh_geom_cache), and the
    tag is a hash of the navmesh sources.  Equal stamp means the entries were
    produced by exactly this code, whatever their mtimes say.

    This is deliberately NOT an mtime comparison.  mtimes are rewritten by a
    checkout, a branch switch or an unzip, so an mtime rule rejects caches that
    are perfectly valid -- and the gate is only useful if it never cries wolf
    on a correct cache.  An unstamped cache (built before this existed, or
    hand-copied) is treated as stale, which is the safe direction: the cost is
    one regeneration, not wrong geometry.
    """
    if tag is None:
        return False
    stamp = os.path.join(nc.cache_dir(plugin), 'CACHE_TAG')
    try:
        with open(stamp) as fh:
            return fh.read().strip() == tag
    except OSError:
        return False


def run_publish(tag: str | None = None, dry_run: bool = False) -> int:
    tag = tag or next_tag()
    plugins = nc.discover_plugins()
    if not plugins:
        print('navmesh-cache: no caches to publish.')
        return 0
    print('navmesh-cache: publishing %s for tag %s' % (', '.join(plugins), tag))
    return nc.publish(plugins, tag, os.path.join(nc.repo_root(), 'temp'),
                      dry_run=dry_run)


def _is_master_ref(ref: str) -> bool:
    """Does this remote ref name master itself (not a branch merely ending in it)?"""
    return ref == 'master' or ref == 'refs/heads/master'


def pushed_refs(argv: list[str]) -> 'list[str] | None':
    """The remote refs this push updates, or None if they could not be read.

    The shell snippet reads git's stdin (the only place the ref list exists)
    and forwards each remote ref as `--ref <name>`, because python itself is
    run with </dev/null so the gate can prompt.  Stdin is still consulted as a
    fallback so a hook installed by an older version keeps working.
    """
    refs = [argv[i + 1] for i, a in enumerate(argv)
            if a == '--ref' and i + 1 < len(argv)]
    if refs:
        return refs
    try:
        if not sys.stdin.isatty():
            lines = [l.split() for l in sys.stdin.read().splitlines() if l.strip()]
            got = [p[2] for p in lines if len(p) >= 3]
            if got:
                return got
    except Exception:
        pass
    return None


def _try_adopt(stale: list) -> list:
    """Adopt every stale cache whose geometry still reproduces; return the rest.

    A pure refactor moves the tag without changing a single vertex, and
    regenerating then costs ~95 minutes to rebuild bytes already on disk.
    Adoption proves a sample reproduces and re-keys instead.  A cache that does
    NOT reproduce is left stale, so a real behaviour change still blocks.
    """
    try:
        from tools.navmesh import navmesh_adopt as na
    except Exception as exc:
        print('navmesh-cache: adoption unavailable (%s); '
              'falling back to regeneration.' % exc)
        return stale
    remaining = []
    for plugin in stale:
        print('')
        print('navmesh-cache: %s is stale -- checking whether its geometry '
              'still reproduces...' % plugin)
        try:
            rc = na.adopt(plugin, sample=na.SAMPLE_DEFAULT)
        except Exception as exc:
            print('navmesh-cache: adoption failed for %s (%s).'
                  % (plugin, exc))
            rc = na.ADOPT_REFUSED
        if rc != na.ADOPT_OK:
            remaining.append(plugin)
    return remaining


def pre_push(argv: list[str]) -> int:
    """git pre-push entry point.  Only gates pushes that update master."""
    # An unreadable ref list must gate rather than skip: a false block costs one
    # --no-verify, a false pass ships a dead cache to every downloader.
    refs = pushed_refs(argv)
    if refs is not None and not any(_is_master_ref(r) for r in refs):
        print('navmesh-cache: not pushing master (%s); skipping the gate.'
              % ', '.join(refs))
        return 0

    gated, touched, stale = check(verbose=True)
    if not gated:
        return 0

    if stale:
        stale = _try_adopt(stale)

    if stale:
        print('')
        if touched:
            print('PUSH BLOCKED: navmesh generation changed, but these caches '
                  'were not rebuilt:')
        else:
            print('PUSH BLOCKED: these caches were not built by the current '
                  'navmesh code:')
        for p in stale:
            print('  - %s' % p)
        print('')
        if touched:
            print('Editing navmesh sources changes the cache tag, so every '
                  'entry is invalidated.')
        print('Publishing as-is would ship a cache that misses for every '
              'downloader.')
        print('')
        print('Fix by re-running the import so the cache regenerates:')
        for p in stale:
            print('  python convert.py -f "%s" --import-only' % p)
        print('')
        print('Then push again.  To publish without pushing: '
              'python tools/navmesh/navmesh_cache_hook.py --run')
        print('To bypass this check once: git push --no-verify')
        return 1

    print('navmesh-cache: caches are current; publishing...')
    rc = run_publish()
    if rc != 0:
        print('')
        print('navmesh-cache: publish failed. The push is allowed to continue '
              '-- the archives are in temp/ and can be uploaded manually.')
    return 0


HOOK_END = '# <<< navmesh-cache-gate <<<'


def _existing_block(text: str) -> str:
    """The installed gate block, marker to marker, as it appears in *text*."""
    start = text.index(HOOK_MARKER)
    end = text.index(HOOK_END, start) + len(HOOK_END)
    return text[start:end]


def _insert_prelude(text: str) -> str:
    """Put the stdin capture at the TOP of the hook, just after the shebang.

    Everything below it then reads the ref list from $navmesh_stdin rather than
    the pipe, so an existing git-lfs step no longer steals it.
    """
    block_new = HOOK_PRELUDE.strip('\n')
    if HOOK_PRELUDE_MARKER in text:
        start = text.index(HOOK_PRELUDE_MARKER)
        end = text.index(HOOK_PRELUDE_END, start) + len(HOOK_PRELUDE_END)
        block = text[start:end]
        if block == block_new:
            return text
        return text.replace(block, block_new, 1)

    lines = text.split('\n')
    at = 1 if lines and lines[0].startswith('#!') else 0
    lines.insert(at, '\n' + block_new + '\n')
    text = '\n'.join(lines)
    # Any pre-existing step that expects git's ref list on stdin (git-lfs does)
    # must now be fed the captured copy, since the pipe is already at EOF.
    text = re.sub(r'^(\s*)(git lfs pre-push .*)$',
                  r'\1echo "$navmesh_stdin" | \2',
                  text, count=1, flags=re.M)
    return text


def install_hook() -> int:
    hooks = os.path.join(nc.repo_root(), '.git', 'hooks')
    path = os.path.join(hooks, 'pre-push')
    existing = ''
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            existing = fh.read()
        if HOOK_MARKER in existing:
            # Replace an existing block rather than reporting "already
            # installed": that left a hook whose snippet predates a fix (the
            # ref-forwarding one especially) silently in place forever.
            block = _existing_block(existing)
            updated = existing
            if block != HOOK_SNIPPET.strip('\n'):
                updated = updated.replace(block, HOOK_SNIPPET.strip('\n'), 1)
            # The gate block alone is not enough: without the stdin prelude the
            # ref list is already at EOF by the time it runs.
            updated = _insert_prelude(updated)
            if updated == existing:
                print('Already installed and up to date in %s' % path)
                return 0
            with open(path, 'w', encoding='utf-8', newline='\n') as fh:
                fh.write(updated)
            print('Updated the navmesh cache gate in %s' % path)
            return 0

    if not existing:
        existing = '#!/bin/sh\n'
    elif not existing.endswith('\n'):
        existing += '\n'

    # Append: an existing hook (git-lfs ships one here) must keep working.
    # _insert_prelude puts the stdin capture ABOVE that hook and rewires it to
    # the captured copy, so the gate below still sees the pushed refs.
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write(_insert_prelude(existing) + '\n' + HOOK_SNIPPET)
    try:
        os.chmod(path, 0o755)
    except OSError:
        pass
    print('Installed navmesh cache gate into %s' % path)
    if existing.strip() and 'git lfs' in existing:
        print('(appended after the existing git-lfs hook, which still runs)')
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--install', action='store_true',
                   help='wire into .git/hooks/pre-push')
    g.add_argument('--check', action='store_true',
                   help='report what a push would do; never uploads')
    g.add_argument('--run', action='store_true',
                   help='publish the cache now (manual / PR workflow)')
    g.add_argument('--pre-push', action='store_true',
                   help=argparse.SUPPRESS)
    ap.add_argument('--tag', help='release tag (default: the next one)')
    ap.add_argument('--dry-run', action='store_true',
                    help='with --run: archive but do not upload')
    args, extra = ap.parse_known_args(argv)

    if args.install:
        return install_hook()
    if args.pre_push:
        return pre_push(extra)
    if args.run:
        return run_publish(args.tag, args.dry_run)

    gated, touched, stale = check(verbose=True)
    if not gated:
        print('navmesh-cache: push would not be gated.')
        return 0
    if stale:
        print('\nnavmesh-cache: push WOULD BE BLOCKED (stale: %s)'
              % ', '.join(stale))
        return 1
    print('\nnavmesh-cache: caches current; push would publish for tag %s.'
          % next_tag())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
