#!/usr/bin/env python
"""Publish / install the shared navmesh geometry cache.

Navmesh generation is the slowest part of an import (seconds per cell, ~8,200
cells for Oblivion).  The result is cached in
`export/<plugin>/navmesh_geom_cache/` -- but that cache is gitignored, so
everyone who downloads the converter regenerates it from scratch.  This tool
ships it as a GitHub Release asset instead:

    python tools/navmesh/navmesh_cache.py verify   --plugin Oblivion.esm
    python tools/navmesh/navmesh_cache.py archive  --plugin Oblivion.esm --out-dir temp
    python tools/navmesh/navmesh_cache.py publish  --tag 0.56
    python tools/navmesh/navmesh_cache.py install  --plugin Oblivion.esm --tag 0.56

Release assets do not count against repository size, allow 2 GB per file and
are not bandwidth-metered on public repos, so this costs nothing on a free
plan -- whereas committing 339 MB of churning binaries would bloat history
permanently (git keeps every version forever) and Git LFS's free tier is 1 GB
of bandwidth per MONTH, about three clones.

WHAT IS AND IS NOT SHIPPED
--------------------------
Shipped: `navmesh_geom_cache/*.pkl` only.  Each holds a hash string, a float32
vertex array, an int32 triangle array and a ledge list -- the GENERATED walking
surface, our own algorithm's output.  It is the same category of data as the
navmesh already inside the built ESM, cannot be rendered or reversed into an
Oblivion asset, and names no source file.

NOT shipped, ever: `collision_cache.bin`.  It maps Oblivion mesh PATHS to
verbatim Havok collision triangles lifted from Bethesda's NIFs -- derived
Bethesda asset data, keyed by asset name.  Every user already builds their own
during the mesh phase from the copy of the game they own.  The manifest carries
only a one-way SHA-1 of its contents, which proves a local cache matches the
publisher's without carrying any of it.

Also never shipped: anything else under `export/`.  `navmesh_index.pkl` and
`audit_index3.pkl` are ~2.1 GB each, so archiving globs
`navmesh_geom_cache/*.pkl` specifically rather than `export/**/*.pkl`.

STALENESS
---------
A stale cache is worse than no cache: it would silently produce navmesh from an
old algorithm with no visible symptom.  Three independent guards:

  1. Every .pkl embeds a per-cell `hash` covering the pathgrid, the placed
     REFRs, the LAND heights, the collision of the meshes THAT CELL places, and
     the navmesh source tag.  A mismatch makes that entry miss -- the loader
     falls back to generating.  This is enforced by the importer itself
     (pgrd_to_navm._geom_cache_load), not by this tool.
  2. `verify` checks the cache against the CURRENT navmesh sources before any
     publish, so an out-of-date cache cannot be released.
  3. The manifest records the source tag, the collision content hash and the
     starting tag, so `install` can warn loudly on a mismatch.

Guard 1 means a wrong cache is always slow, never incorrect.  Guards 2 and 3
exist so the user finds out immediately instead of after a long import.

PARTIAL INVALIDATION
--------------------
Collision enters each cell's hash PER MESH (collision_extract.collision_digest),
so a user who replaces a few meshes only loses the cells that place them; the
rest of the downloaded cache still hits.  Before that change the collision cache
entered as one whole-file hash and a single replaced mesh invalidated all ~8,200
entries.
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import shutil
import subprocess
import sys
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.subprocess_flags import POPEN_FLAGS

MANIFEST_NAME = 'navmesh_cache_manifest.json'
CACHE_DIRNAME = 'navmesh_geom_cache'

# Set to 1/true to skip the automatic download (metered connections, or a user
# who would rather generate locally).  Defined here so the GUI menu item and
# convert.py agree on the name instead of repeating the literal -- the same
# reason worker_budget exports WORKERS_ENV_VAR.
NO_DOWNLOAD_ENV_VAR = 'TESCONV_NO_CACHE_DOWNLOAD'


# ---------------------------------------------------------------------------
# Repo / plugin helpers
# ---------------------------------------------------------------------------

def repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def export_dir(plugin: str) -> str:
    return os.path.join(repo_root(), 'export', plugin)


def cache_dir(plugin: str) -> str:
    return os.path.join(export_dir(plugin), CACHE_DIRNAME)


def collision_path(plugin: str) -> str:
    return os.path.join(export_dir(plugin), 'collision_cache.bin')


def asset_name(plugin: str) -> str:
    """Release asset filename for a plugin ('Oblivion.esm' -> navmesh-cache-Oblivion.zip)."""
    stem = plugin[:-4] if plugin.lower().endswith(('.esm', '.esp')) else plugin
    return 'navmesh-cache-%s.zip' % stem.replace(' ', '_')


def source_tag(plugin: str) -> str | None:
    """The navmesh source tag the CURRENT code would use for this plugin."""
    from tes5_import.navmesh import pool as navm_pool
    got = navm_pool.navmesh_geom_cache(collision_path(plugin))
    return got[1] if got else None


def collision_hash(plugin: str) -> str | None:
    """Content hash of the plugin's collision cache, or None if absent."""
    import asset_convert.collision.collision_extract as ce
    path = collision_path(plugin)
    if not os.path.exists(path):
        return None
    if ce.load_collision(path, quiet=True) == 0:
        return None
    return ce.collision_content_hash()


#: Hosted caches, case-insensitive. See: docs/commentary/tes5_import_navmesh.md#publishable-plugins
PUBLISHABLE_PLUGINS = ('Oblivion.esm', 'Nehrim.esm', 'Morrowind_ob.esm')


def is_publishable(plugin: str) -> bool:
    return plugin.lower() in {p.lower() for p in PUBLISHABLE_PLUGINS}


def discover_plugins(all_plugins: bool = False) -> list[str]:
    """Plugins under export/ whose navmesh cache we publish.

    Restricted to PUBLISHABLE_PLUGINS by default -- this feeds the pre-push
    gate and `publish`, and neither should ever touch a plugin we do not host.
    Pass all_plugins=True for local inspection (`verify` with no --plugin),
    where seeing every cache is the point and nothing gets uploaded.
    """
    root = os.path.join(repo_root(), 'export')
    if not os.path.isdir(root):
        return []
    found = sorted(d for d in os.listdir(root)
                   if os.path.isdir(os.path.join(root, d, CACHE_DIRNAME)))
    if all_plugins:
        return found
    return [d for d in found if is_publishable(d)]


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

def verify(plugin: str, sample: int = 0, quiet: bool = False) -> dict:
    """Check the on-disk cache against the CURRENT navmesh sources.

    Each entry stores a hash of its own inputs, and the *tag* component of that
    hash covers the navmesh sources.  We cannot recompute a full per-cell hash
    here without re-reading every cell's REFRs (that is the import's job), so
    the check is structural: every entry must load, and the cache must be
    non-empty.  The authoritative freshness signal is the tag, which we record
    in the manifest and compare on install.

    Returns a dict with 'ok', 'entries', 'unreadable', 'tag', 'collision'.
    """
    cdir = cache_dir(plugin)
    result = {'plugin': plugin, 'ok': False, 'entries': 0, 'unreadable': [],
              'tag': None, 'collision': None}
    if not os.path.isdir(cdir):
        if not quiet:
            print('  %s: no cache directory (%s)' % (plugin, cdir))
        return result

    files = sorted(f for f in os.listdir(cdir) if f.endswith('.pkl'))
    result['entries'] = len(files)
    check = files
    if sample and sample < len(files):
        stride = max(1, len(files) // sample)
        check = files[::stride][:sample]

    for name in check:
        path = os.path.join(cdir, name)
        try:
            with open(path, 'rb') as fh:
                blob = pickle.load(fh)
            # Shape check: a truncated or foreign pickle must not be published.
            if not isinstance(blob, dict) or 'hash' not in blob \
                    or 'verts' not in blob or 'tris' not in blob:
                result['unreadable'].append(name)
        except Exception:
            result['unreadable'].append(name)

    result['tag'] = source_tag(plugin)
    result['collision'] = collision_hash(plugin)
    result['ok'] = (result['entries'] > 0 and not result['unreadable']
                    and result['tag'] is not None)

    if not quiet:
        print('  %s: %d entries, %d unreadable, tag=%s'
              % (plugin, result['entries'], len(result['unreadable']),
                 (result['tag'] or '<none>')[:12]))
        if result['collision'] is None:
            print('    WARNING: no collision cache -- run the mesh phase first;'
                  ' a published cache would miss for everyone.')
        for name in result['unreadable'][:5]:
            print('    unreadable: %s' % name)
    return result


# ---------------------------------------------------------------------------
# archive
# ---------------------------------------------------------------------------

def archive(plugin: str, out_dir: str, tag: str, quiet: bool = False) -> str | None:
    """Zip a plugin's cache + manifest.  Returns the zip path, or None."""
    info = verify(plugin, quiet=quiet)
    if not info['ok']:
        print('  %s: REFUSING to archive -- cache did not verify.' % plugin)
        return None

    os.makedirs(out_dir, exist_ok=True)
    zip_path = os.path.join(out_dir, asset_name(plugin))
    cdir = cache_dir(plugin)
    manifest = {
        'plugin': plugin,
        'starting_tag': tag,
        'source_tag': info['tag'],
        'collision_hash': info['collision'],
        'entries': info['entries'],
        'format': 'navmesh_geom_cache/v4-permesh-collision',
    }

    files = sorted(f for f in os.listdir(cdir) if f.endswith('.pkl'))
    if not quiet:
        print('  %s: zipping %d entries -> %s' % (plugin, len(files), zip_path))
    # ZIP_DEFLATED: the pickles hold float arrays and compress usefully.
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2, sort_keys=True))
        # Ship the stamp too, so an installed cache is recognised as built by
        # this navmesh code.  Without it the installer would leave the cache
        # uncertified and the next `verify` would call a perfectly good
        # download stale.  install() writes it from the MANIFEST rather than
        # trusting this copy, so it can never certify a mismatched archive.
        zf.writestr('CACHE_TAG', info['tag'])
        for name in files:
            zf.write(os.path.join(cdir, name), name)

    if not quiet:
        mb = os.path.getsize(zip_path) / (1 << 20)
        print('    %.1f MB' % mb)
        if os.path.getsize(zip_path) > 2 * (1 << 30):
            print('    ERROR: over the 2 GB per-asset release limit.')
            return None
    return zip_path


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------

#: Repo the caches are published from. See: docs/commentary/tes5_import_navmesh.md#the-download-path-never-runs-git
CACHE_REPO = 'bryantmh/tes4skyrim'


def api_repo() -> str:
    """'owner/name' for the GitHub API.

    A CONSTANT: git must not be reachable from any end-user path.
    See: docs/commentary/tes5_import_navmesh.md#the-download-path-never-runs-git
    """
    return CACHE_REPO


def _api_releases(timeout: int = 20) -> list:
    """Every release, via the ANONYMOUS REST API.  [] on any failure.

    Reading releases needs no credentials on a public repo, so downloading a
    cache must NOT require the GitHub CLI: `gh` is a developer tool that
    approximately no end user has installed, and gating the whole feature on it
    would leave almost everyone silently regenerating navmesh.  Publishing
    still uses gh (it genuinely needs auth); only the read path is anonymous.
    """
    repo = api_repo()
    if not repo:
        return []
    url = 'https://api.github.com/repos/%s/releases?per_page=100' % repo
    req = urllib.request.Request(url, headers={
        'User-Agent': 'tes4skyrim-navmesh-cache',
        'Accept': 'application/vnd.github+json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as fh:
            return json.load(fh)
    except Exception:
        return []


def _download(url: str, dest: str, quiet: bool = False,
              timeout: int = 60) -> bool:
    """Stream *url* to *dest* with a coarse progress line.  False on failure."""
    req = urllib.request.Request(url, headers={
        'User-Agent': 'tes4skyrim-navmesh-cache'})
    tmp = dest + '.part'
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            total = int(resp.headers.get('Content-Length') or 0)
            done = 0
            step = max(1, total // 10) if total else (8 << 20)
            nxt = step
            with open(tmp, 'wb') as out:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
                    if not quiet and done >= nxt:
                        nxt += step
                        # Prefixed so the line is attributable in a long
                        # conversion log -- a bare "40%" tells the user nothing
                        # about what is downloading.
                        if total:
                            print('    Navmesh cache: %d%% (%.1f/%.1f MB)'
                                  % (done * 100 // total, done / (1 << 20),
                                     total / (1 << 20)), flush=True)
                        else:
                            print('    Navmesh cache: %.1f MB'
                                  % (done / (1 << 20)), flush=True)
        os.replace(tmp, dest)
        return True
    except Exception as exc:
        if not quiet:
            print('    download failed (%s)' % exc)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def gh_repo() -> list:
    """['--repo', 'owner/name'] for gh, or [] if it cannot be determined.

    Names the repo explicitly rather than relying on the process CWD.  CALLERS
    MUST ALREADY REQUIRE gh: this spawns git, which an end user need not have.
    The automatic download uses api_repo() instead.
    See: docs/commentary/tes5_import_navmesh.md#the-download-path-never-runs-git
    """
    out = subprocess.run(['git', 'remote', 'get-url', 'origin'],
                         capture_output=True, text=True, cwd=repo_root(),
                         **POPEN_FLAGS)
    url = out.stdout.strip() if out.returncode == 0 else ''
    if not url:
        return []
    # git@github.com:owner/name.git  |  https://github.com/owner/name(.git)
    if url.startswith('git@'):
        path = url.split(':', 1)[-1]
    else:
        path = url.split('github.com/', 1)[-1] if 'github.com/' in url else ''
    path = path[:-4] if path.endswith('.git') else path
    return ['--repo', path] if path.count('/') == 1 else []


def cache_release_tag(tag: str, until: str = None) -> str:
    """Name for a cache release: 'navmesh-cache-0.56+' or '...-0.56-0.72'.

    Deliberately NOT the bare code tag.  This repo ships code as annotated
    TAGS, not Releases (tag-on-push.yml), and GitHub renders a real Release
    above a plain tag on the same page.  A release literally named '0.56' would
    therefore sit at the top of /releases looking like THE 0.56 download, while
    containing nothing but a navmesh cache.  A distinct name keeps the code tag
    the only thing called '0.56'.

    The version RANGE is what a downloader actually needs: a cache is valid
    from the version it was built for until the next navmesh change.  That
    upper bound does not exist at publish time -- nobody knows whether 0.56's
    cache survives to 0.57 or to 0.72 -- so a release is born OPEN-ENDED
    ('0.56+', read "0.56 and above") and is renamed to a closed range
    ('0.56-0.72') by close_cache_release() at the moment a navmesh change
    invalidates it.  The newest cache release therefore always ends in '+'.
    """
    return ('navmesh-cache-%s-%s' % (tag, until) if until
            else 'navmesh-cache-%s+' % tag)


def parse_cache_release_tag(name: str) -> tuple | None:
    """('0.56', '0.72'|None) from a cache release name, or None if not one."""
    prefix = 'navmesh-cache-'
    if not name.startswith(prefix):
        return None
    body = name[len(prefix):]
    if body.endswith('+'):
        return (body[:-1], None)
    # '0.56-0.72' -> two MAJOR.MINOR halves; the separator is the '-' between
    # them, and versions themselves never contain one.
    parts = body.split('-')
    if len(parts) == 2:
        return (parts[0], parts[1])
    return None


def _version_key(tag: str) -> tuple | None:
    """Comparable key for a MAJOR.MINOR tag, NORMALIZED TO THOUSANDTHS.

    The minor field's WIDTH sets its scale -- the same trap previous_tag() and
    the hook's next_tag() each document.  Tags through 0.58 are MAJOR.MM
    (hundredths); 0.581 onward are MAJOR.MMM (thousandths).  Comparing the raw
    integers makes '0.586' look 10x larger than '0.57' relative to reality, and
    ranks '0.10' above '0.9' when 0.100 is far below 0.900.

    Scaling here matters because the range check ("does this cache cover my
    version?") is a direct comparison of these keys: an unscaled '0.57' start
    would wrongly appear to cover version 0.100-0.569.

    Two spellings that scale alike ('0.58'/'0.580', '0.10'/'0.100') are one
    version written two ways, not two releases, so they compare EQUAL by
    design.  No real tag pair is lost to that: the thousandths era begins at
    0.581, so every value a 3-digit field can express below the switch is only
    a zero-padded legacy name.
    """
    parts = tag.split('.')
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    major, minor = parts
    # The minor field is a FRACTION written without its point: '0.9' is nine
    # tenths, '0.58' fifty-eight hundredths, '0.586' five-eighty-six
    # thousandths.  Scale by the field's own width rather than enumerating
    # widths, so this stays correct if the scheme ever gets finer.  A field
    # WIDER than thousandths must still yield a key -- returning None would
    # make resolve_cache_release(), latest_cache_release() and auto_install()'s
    # range check skip that release SILENTLY, indistinguishable from "no cache
    # exists".  Integer division there loses only sub-thousandth precision,
    # which no published tag has ever used.
    scaled = int(minor) * 1000 // (10 ** len(minor))
    # Scaling is deliberately NOT injective over spellings, and that is correct:
    # two widths that scale alike are two spellings of ONE version, never two
    # releases.  '0.10' and '0.100' both mean 100 thousandths -- and 0.100 could
    # never be a thousandths-era tag anyway, because that era starts at 0.581
    # (_SCHEME_SWITCH_MILS), far above 100.  Every value a 3-digit field can
    # express below the switch is therefore just a zero-padded legacy name, so
    # collapsing them is the intended behaviour, exactly as for '0.58'/'0.580'.
    return (int(major), scaled)


# Last version issued under the 2-digit MAJOR.MM scheme, in thousandths.
# 0.58 was the final hundredths tag; 0.581 began MAJOR.MMM.  Stepping down to
# or below this point must produce a 2-digit name, because that is how every
# release at or before it was actually published.
_SCHEME_SWITCH_MILS = 580


def _raw_minor_field(tag: str) -> tuple:
    """(major, minor, minor_width) from *tag*'s RAW digits, not _version_key.

    See: docs/commentary/tes5_import_navmesh.md#cache-tag-steps-in-its-own-scheme
    """
    parts = tag.split('.')
    return int(parts[0]), int(parts[1]), len(parts[1])


def previous_tag(tag: str) -> str:
    """The code tag one step below *tag* ('0.582' -> '0.581', '0.73' -> '0.72').

    Mirrors tag-on-push.yml's numbering, so a range closes on the last version
    the cache was actually valid for rather than the one that broke it.

    The step size follows the VALUE's scheme, not the spelling's width.  Tags
    through 0.58 are MAJOR.MM (hundredths); 0.581 onward are MAJOR.MMM
    (thousandths).  Legacy tags must keep decrementing by a hundredth --
    rewriting '0.57' as '0.569' would name a release that never existed and 404
    the download.  The 3-digit boundary steps back into the old scheme's last
    name: 0.580 -> 0.58.

    Any minor width is accepted, because _version_key() accepts any: a 1-digit
    '0.9' is a real legacy tag, and a wider field still yields a key there.  If
    this returned *tag* unchanged for those, close_cache_release() would build
    `cache_release_tag(start, previous_tag(new_tag))` with an upper bound equal
    to new_tag itself -- a closed range advertising the very version that
    SUPERSEDED it, i.e. a cache promising to serve a build it is known stale
    for.  Stepping off the scaled value keeps the two functions in agreement.
    """
    if _version_key(tag) is None:
        return tag
    major, minor, minor_width = _raw_minor_field(tag)
    # A 1-digit field is a legacy name too ('0.9'), and it decrements in tenths
    # in its own spelling.  Widths beyond 3 have never been published, but must
    # still step rather than return the input unchanged -- see the docstring:
    # close_cache_release() would otherwise close a range on the very version
    # that superseded it.  Both fold into the generic "step one unit at this
    # width" below.
    if minor_width <= 2:
        unit = 10 ** minor_width
        cents = major * unit + minor - 1
        if cents < 0:
            return tag
        return '%d.%0*d' % (cents // unit, minor_width, cents % unit)
    if minor_width > 3:
        unit = 10 ** minor_width
        wide = major * unit + minor - 1
        if wide < 0:
            return tag
        return '%d.%0*d' % (wide // unit, minor_width, wide % unit)
    mils = major * 1000 + minor - 1
    # Below the switchover every name was 2-digit, so a step that lands
    # there must be spelled in the old scheme: 0.581 -> 0.58, never
    # '0.580' (never published) nor '0.579' (never existed).  Above it,
    # every thousandth is its own real tag -- 1.000 -> 0.999 -- so the
    # 2-digit spelling applies ONLY on the legacy side of the boundary.
    if mils <= _SCHEME_SWITCH_MILS:
        cents = mils // 10
        return '%d.%02d' % (cents // 100, cents % 100)
    return '%d.%03d' % (mils // 1000, mils % 1000)


def tag_exists(tag: str) -> bool:
    return subprocess.run(['git', 'rev-parse', '-q', '--verify',
                           'refs/tags/%s' % tag],
                          capture_output=True, cwd=repo_root()).returncode == 0


def release_target(tag: str) -> str:
    """A `target_commitish` GitHub will accept for the release at *tag*.

    MUST be a COMMIT, never the tag name.  tag-on-push.yml creates ANNOTATED
    tags, so `refs/tags/0.616` resolves to a tag OBJECT; passing the name makes
    the API reject the whole create with
    'Release.target_commitish is invalid' (HTTP 422, measured 2026-08-26 --
    it closed the previous cache release and then failed to create the new
    one, leaving no open range at all).  `^{commit}` peels the annotation to
    the commit underneath, which the API accepts.

    Falls back to 'master' when the tag is not present locally -- publishing
    can run before the tag has been fetched.
    """
    if tag_exists(tag):
        out = subprocess.run(['git', 'rev-parse', '%s^{commit}' % tag],
                             capture_output=True, text=True, cwd=repo_root())
        sha = out.stdout.strip()
        if out.returncode == 0 and sha:
            return sha
    return 'master'


def cache_release_notes(tag: str) -> str:
    """Body for a cache release: say what it is NOT, and where the code is."""
    return '\n'.join((
        '## This is not the converter -- it is a build cache',
        '',
        'This release contains only a **prebuilt navmesh geometry cache**, an',
        'optional speed-up for the Import phase. It contains no code and no',
        'game assets.',
        '',
        '### Get the converter here',
        '',
        'The converter is released as a **tag**, not as a GitHub Release.',
        'Download the latest tag:',
        '',
        '- **https://github.com/bryantmh/tes4skyrim/tags** -- pick the highest',
        '  version (this cache was built for `%s`).' % tag,
        '- Or clone and check it out: `git clone ... && git checkout %s`' % tag,
        '',
        '### What this cache is for',
        '',
        'Navmesh generation is the slowest part of an import -- a few seconds',
        'per cell across thousands of cells. Installing this skips it:',
        '',
        '```bash',
        '# run the Meshes phase at least once first',
        'python tools/navmesh/navmesh_cache.py install --plugin Oblivion.esm',
        '```',
        '',
        'Measured on Nehrim (2,929 cells): the navmesh stage drops from',
        '**192 s to 3.8 s**, producing a byte-identical plugin.',
        '',
        'Every entry carries a hash of the inputs it was built from, so',
        'anything that does not match is regenerated automatically. A stale or',
        'mismatched cache costs time, never correctness. Replacing a few meshes',
        'only invalidates the cells that place them.',
        '',
        '### Which versions this cache covers',
        '',
        'Built for **`%s`**, and valid for every version from `%s` up to the' % (tag, tag),
        'next release that changes navmesh generation. While this release is',
        'named `%s` it is the current one; once a later' % (cache_release_tag(tag),),
        'cache supersedes it, it is renamed to a closed range (for example',
        '`navmesh-cache-%s-0.72`) so the exact span stays on the label.' % tag,
        '',
        'Using it outside that range is harmless -- the entries simply miss and',
        'regenerate.',
        '',
        # Machine-readable, for auto_install's pre-download check: comparing
        # this against the local source tag turns "download 115 MB, then find
        # out it misses" into a metadata request.  Kept last and prefixed so it
        # reads as a footnote rather than instructions.
        '<!-- navmesh-source-tag: %s -->' % (source_tag_for_notes() or ''),
    ))


def source_tag_for_notes() -> str | None:
    """Source tag of any locally-cached plugin (they all share one tag)."""
    for plugin in discover_plugins():
        tag = source_tag(plugin)
        if tag:
            return tag
    return None


def resolve_cache_release(tag: str) -> str | None:
    """The cache release COVERING version *tag*, whatever its range is named.

    `--tag 0.60` must find the cache that serves 0.60 -- which may be published
    as 'navmesh-cache-0.56+' (still open) or 'navmesh-cache-0.56-0.72' (closed
    later).  Guessing 'navmesh-cache-0.60+' would 404 in both cases, so match
    on the RANGE rather than the name.
    """
    want = _version_key(tag)
    if want is None:
        return None
    out = subprocess.run(
        ['gh', 'release', 'list', *gh_repo(), '--limit', '100',
         '--json', 'tagName', '--jq', '.[].tagName'],
        capture_output=True, text=True, cwd=repo_root())
    if out.returncode != 0:
        return None
    best = None
    for line in out.stdout.splitlines():
        name = line.strip()
        parsed = parse_cache_release_tag(name)
        if not parsed:
            continue
        lo = _version_key(parsed[0])
        if lo is None or lo > want:
            continue
        hi = _version_key(parsed[1]) if parsed[1] else None
        if hi is not None and want > hi:
            continue
        # Several ranges can cover a version only if they overlap; take the
        # one starting closest to it.
        if best is None or lo > best[0]:
            best = (lo, name)
    return best[1] if best else None


def close_cache_release(new_tag: str) -> str | None:
    """Close the open-ended cache release that *new_tag* supersedes.

    Renames 'navmesh-cache-0.56+' to 'navmesh-cache-0.56-0.72' when publishing
    at 0.73, so the old release states the exact range it covers instead of
    claiming everything from 0.56 upward forever.  Returns the new name, or
    None if there was nothing to close.

    Only the release TAG and title change; the assets are untouched, so old
    download links keep working via the release page.  (Direct asset URLs
    contain the tag and will move -- acceptable, since the install tool
    resolves releases by name rather than hard-coding URLs.)
    """
    open_rel = latest_cache_release()
    if not open_rel:
        return None
    parsed = parse_cache_release_tag(open_rel)
    if not parsed or parsed[1] is not None:
        return None                     # already closed, nothing to do
    start = parsed[0]
    if _version_key(start) is None or _version_key(new_tag) is None:
        return None
    if _version_key(start) >= _version_key(new_tag):
        return None                     # republishing the same/older version

    # The upper bound must not fall BELOW the start, or the closed range
    # excludes every version -- including the one it begins at.  This is not
    # hypothetical: previous_tag() steps in the SPELLING's scheme while the
    # range check compares thousandths-scaled keys, so closing '0.581+' against
    # a 2-digit successor ('0.59') steps in hundredths to '0.58' = (0, 580),
    # one unit under the start's (0, 581).  The result, 'navmesh-cache-0.581-
    # 0.58', matched nothing at all and silently retired a good cache.
    #
    # Clamping to `start` is the honest close: the release covered exactly the
    # one version it opened at, which is precisely true when its successor is
    # the very next release.  The guard above already rejected a successor at
    # or below the start, so this can only ever narrow, never invert.
    until = previous_tag(new_tag)
    if _version_key(until) is None or _version_key(until) < _version_key(start):
        until = start

    closed = cache_release_tag(start, until)
    if closed == open_rel:
        return None
    print('Closing %s -> %s (superseded by %s)' % (open_rel, closed, new_tag))
    rc = subprocess.run(
        ['gh', 'release', 'edit', *gh_repo(), open_rel, '--tag', closed,
         '--title', 'Navmesh cache for %s-%s (not the converter)'
         % (start, until)],
        cwd=repo_root()).returncode
    if rc != 0:
        print('WARNING: could not rename %s; it will keep claiming "%s and '
              'above".' % (open_rel, start))
        return None
    return closed


def latest_cache_release() -> str | None:
    """Newest `navmesh-cache-*` release tag, or None.

    Cache releases are created with --latest=false so they never outrank the
    code tags, which means `gh release download` with no tag cannot find them --
    this resolves one explicitly.  Sorted by the numeric code tag they carry so
    'navmesh-cache-0.9' does not beat 'navmesh-cache-0.10'.
    """
    out = subprocess.run(
        ['gh', 'release', 'list', *gh_repo(), '--limit', '100',
         '--json', 'tagName', '--jq', '.[].tagName'],
        capture_output=True, text=True, cwd=repo_root())
    if out.returncode != 0:
        return None
    best = None
    for line in out.stdout.splitlines():
        parsed = parse_cache_release_tag(line.strip())
        if not parsed:
            continue
        key = _version_key(parsed[0])
        if key is None:
            continue
        if best is None or key > best[0]:
            best = (key, line.strip())
    return best[1] if best else None


def have_gh() -> bool:
    """Is the GitHub CLI installed AND logged in?

    Presence is not enough: an installed-but-unauthenticated `gh` fails deep
    inside `release create` with an opaque error, after the archives have
    already been built.  Checking auth up front turns that into one actionable
    line.  `gh auth status` exits non-zero when no host is logged in, and
    honours GH_TOKEN/GITHUB_TOKEN, so CI works without an interactive login.
    """
    if shutil.which('gh') is None:
        return False
    try:
        return subprocess.run(['gh', 'auth', 'status'],
                              capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def publish(plugins: list[str], tag: str, out_dir: str,
            dry_run: bool = False) -> int:
    """Archive each plugin and upload to the release for *tag*."""
    zips = []
    for plugin in plugins:
        path = archive(plugin, out_dir, tag)
        if path is None:
            print('ERROR: %s did not archive; nothing was uploaded.' % plugin)
            return 1
        zips.append(path)

    if dry_run:
        print('\n--dry-run: built %d archive(s), skipping upload:' % len(zips))
        for z in zips:
            print('  %s' % z)
        return 0

    if not have_gh():
        print('')
        if shutil.which('gh') is None:
            print('ERROR: the GitHub CLI (gh) is not installed, so the '
                  'archives cannot be uploaded.')
            print('Install it with:  winget install --id GitHub.cli '
                  '--scope user')
        else:
            print('ERROR: gh is installed but not logged in, so the archives '
                  'cannot be uploaded.')
            print('Log in ONCE with:  gh auth login')
            print('(GitHub.com -> HTTPS -> browser; or set GH_TOKEN for a '
                  'non-interactive run.)')
        print('')
        print('The archives are built and waiting in %s -- re-run '
              '`python tools/navmesh/navmesh_cache_hook.py --run` once gh works:'
              % out_dir)
        for z in zips:
            print('  %s' % z)
        return 1

    # `gh release create` fails if the release exists; create-or-upload.
    # Close the previous open-ended range FIRST: 'navmesh-cache-0.56+' becomes
    # 'navmesh-cache-0.56-0.72' when publishing 0.73, so exactly one cache
    # release ever claims "and above" -- the newest one.
    close_cache_release(tag)

    rel_tag = cache_release_tag(tag)
    exists = subprocess.run(['gh', 'release', 'view', *gh_repo(), rel_tag],
                            capture_output=True, text=True).returncode == 0
    if not exists:
        print('\nCreating cache release %s...' % rel_tag)
        rc = subprocess.run(
            ['gh', 'release', 'create', *gh_repo(), rel_tag,
             '--title',
             'Navmesh cache for %s and above (not the converter)' % tag,
             '--notes', cache_release_notes(tag),
             # NEVER let a cache release become "Latest": this repo ships code
             # as TAGS, not releases, and GitHub sorts a real Release above a
             # plain tag.  Without this the top of the Releases page would
             # advertise a build artifact as if it were the converter.
             #
             # --latest=false ALONE IS NOT ENOUGH (measured 2026-08-09, the
             # first real publish): /releases/latest still returned the cache
             # release, because that endpoint serves the newest non-draft,
             # non-prerelease release and there was nothing else to promote in
             # its place.  --prerelease excludes it unconditionally, and shows
             # a "Pre-release" label that reinforces "this is not the build".
             '--latest=false', '--prerelease',
             # Point the release at the code tag it belongs to, so the "N
             # commits to master since this release" line reads correctly.
             '--target', release_target(tag)]
        ).returncode
        if rc != 0:
            print('ERROR: could not create release %s.' % rel_tag)
            return rc

    print('\nUploading %d asset(s) to %s...' % (len(zips), rel_tag))
    rc = subprocess.run(['gh', 'release', 'upload', *gh_repo(), rel_tag, *zips,
                         '--clobber']).returncode
    if rc != 0:
        print('ERROR: upload failed.')
        return rc
    print('Done.')
    return 0


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------

# install() return codes.  A caller that reports WHY nothing was installed
# cannot do so from a bare 1: "built by different navmesh code" and "that file
# is not a usable zip" send the user to completely different fixes, and naming
# the wrong one is worse than saying nothing.  Both are non-zero, so every
# existing `rc == 0` / `rc != 0` test keeps its meaning.
INSTALL_OK        = 0
INSTALL_FAILED    = 1   # unusable archive, failed download, no release, ...
INSTALL_MISMATCH  = 2   # a valid archive built by different navmesh code


def install(plugin: str, tag: str | None, zip_path: str | None,
            force: bool = False) -> int:
    """Unpack a cache archive into export/<plugin>/navmesh_geom_cache/.

    Returns one of the INSTALL_* codes above.  Never raises for a bad archive:
    a corrupt, truncated or 0-byte zip is exactly what a half-finished download
    leaves behind, and letting BadZipFile escape made one such file abort
    auto_install() entirely -- skipping the other drop-in candidates AND the
    HTTPS fallback that would have fixed it.
    """
    tmp_zip = None
    if zip_path is None:
        if not have_gh():
            if shutil.which('gh') is None:
                print('ERROR: gh is not installed and no --zip was given.')
                print('Install it (winget install --id GitHub.cli --scope '
                      'user), or download %s from the release page and pass '
                      '--zip.' % asset_name(plugin))
            else:
                print('ERROR: gh is installed but not logged in, and no --zip '
                      'was given.')
                print('Run `gh auth login` once, or download %s from the '
                      'release page and pass --zip.' % asset_name(plugin))
            return INSTALL_FAILED
        os.makedirs(os.path.join(repo_root(), 'temp'), exist_ok=True)
        tmp_zip = os.path.join(repo_root(), 'temp', asset_name(plugin))
        # Always resolve an explicit cache release.  Two traps otherwise:
        # `--tag 0.56` would look for a release literally named '0.56' (there
        # is none — the code ships as a tag), and a bare `gh release download`
        # takes the LATEST release, which a cache release never is
        # (--latest=false).  Newest-first among navmesh-cache-* is the honest
        # default.
        rel_tag = resolve_cache_release(tag) if tag else latest_cache_release()
        if not rel_tag:
            if tag:
                print('ERROR: no navmesh-cache release covering %s.' % tag)
            else:
                print('ERROR: no navmesh-cache release found in this repo.')
            print('Pass --zip to install a local archive instead.')
            return INSTALL_FAILED
        args = ['gh', 'release', 'download', *gh_repo(), rel_tag,
                '--pattern', asset_name(plugin), '--output', tmp_zip,
                '--clobber']
        print('Downloading %s...' % asset_name(plugin))
        if subprocess.run(args).returncode != 0:
            print('ERROR: download failed.')
            return INSTALL_FAILED
        zip_path = tmp_zip

    # Opening is the step a truncated / still-downloading / 0-byte file fails
    # at, and it must be an ordinary failure rather than an exception: the
    # import phase calls this inside a conversion, where a bad file in a folder
    # must never be able to stop a build (nor hide the candidates behind it).
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        print('ERROR: %s is not a readable zip (%s) -- it may be a partial '
              'download.' % (zip_path, exc))
        return INSTALL_FAILED

    with zf:
        try:
            manifest = json.loads(zf.read(MANIFEST_NAME))
        except KeyError:
            print('ERROR: %s has no %s -- refusing to install an '
                  'unidentified archive.' % (zip_path, MANIFEST_NAME))
            return INSTALL_FAILED
        except (ValueError, zipfile.BadZipFile, OSError) as exc:
            # A manifest that is present but unreadable (corrupt member, bad
            # JSON) is the same class of problem as an unopenable zip.
            print('ERROR: %s has an unreadable %s (%s).'
                  % (zip_path, MANIFEST_NAME, exc))
            return INSTALL_FAILED

        want_tag = source_tag(plugin)
        want_col = collision_hash(plugin)

        # Only a SOURCE-TAG mismatch is worth refusing over.  The tag hashes the
        # navmesh generator itself, so a different one means every single entry
        # is keyed by code that no longer exists and the archive is pure dead
        # weight -- installing it buys nothing and only muddies the cache dir.
        if want_tag and manifest.get('source_tag') != want_tag:
            print('\nWARNING: this cache was built by different navmesh code, '
                  'so every entry would miss.')
            print('Entries that do not match are regenerated automatically, so '
                  'the result stays CORRECT -- you just would not save time.')
            if not force:
                print('Re-run with --force to install anyway.')
                return INSTALL_MISMATCH

        # A COLLISION mismatch must NOT block.  It was refusing for almost
        # everyone: collision_content_hash folds in every mesh the user
        # extracted, so it only matches someone whose game version, DLC set,
        # mod state and extraction completeness are identical to the
        # publisher's.  One extra or missing mesh changed the hash and the
        # drop-in "did not work if it was in the folder" -- with the real
        # reason (a partial-hit warning) reading like a fatal error.
        #
        # Invalidation is PER MESH (collision_extract.collision_digest), so a
        # differing collision set costs only the cells that place the meshes
        # that actually differ; the rest of the archive still hits.  Refusing
        # the whole cache to avoid a partial one is strictly worse.
        if want_col and manifest.get('collision_hash') != want_col:
            print('\nNote: your meshes differ from the publisher\'s, so some '
                  'cells will regenerate.')
            print('The rest of the cache still applies (invalidation is per '
                  'mesh), and the result is identical either way.')

        dest = cache_dir(plugin)
        os.makedirs(dest, exist_ok=True)
        names = [n for n in zf.namelist() if n.endswith('.pkl')]
        # Flat archive by construction; reject any path component so a crafted
        # zip cannot write outside the cache dir.
        for name in names:
            if os.path.basename(name) != name:
                print('ERROR: archive contains a path (%s); refusing.' % name)
                return INSTALL_FAILED
        print('Installing %d entries into %s...' % (len(names), dest))
        for name in names:
            with zf.open(name) as src, open(os.path.join(dest, name), 'wb') as dst:
                shutil.copyfileobj(src, dst)

        # Certify the installed cache ONLY when it really was built by this
        # navmesh code.  Under --force the archive is knowingly mismatched, so
        # stamping it would tell every later check that a stale cache is
        # current; remove any inherited stamp instead.  Entries still
        # self-validate either way, so the worst case stays "slow", not
        # "wrong".
        stamp = os.path.join(dest, 'CACHE_TAG')
        if want_tag and manifest.get('source_tag') == want_tag:
            with open(stamp, 'w') as fh:
                fh.write(want_tag)
        elif os.path.exists(stamp):
            os.remove(stamp)

    if tmp_zip and os.path.exists(tmp_zip):
        os.remove(tmp_zip)
    print('Done. Supported from tag %s.' % manifest.get('starting_tag', '?'))
    return INSTALL_OK


# ---------------------------------------------------------------------------
# Drop-in auto-install (called by the import phase; no command to learn)
# ---------------------------------------------------------------------------

# Where a user drops a downloaded cache zip.  Checked automatically at the top
# of every import, so the whole workflow is "download the zip, put it here,
# press Import" -- no CLI, no docs, no flags.
DROPIN_DIRNAME = 'navmesh_cache'


def dropin_dir() -> str:
    return os.path.join(repo_root(), DROPIN_DIRNAME)


def find_dropins(plugin: str) -> list[str]:
    """Every cache zip for *plugin* sitting in navmesh_cache/, best first.

    Deliberately forgiving about WHERE and WHAT the file is called.  Every
    strictness here shows up as "I put the zip in the folder and nothing
    happened", which is the single most-reported failure of this feature and is
    indistinguishable, from the user's side, from the cache being broken.

    ALL candidates are returned, not just the best one.  Returning only
    `hits[0]` made a single unusable file fatal to the whole feature: a
    truncated download, a `.part` rename or a 0-byte placeholder sitting at the
    SHALLOWEST level shadows every deeper match, install() raises BadZipFile,
    and auto_install()'s outer handler swallows it -- so neither the good
    nested zip nor the HTTPS download is ever tried.  The depth-3 walk added
    exactly the nesting that makes that collision likely, so the caller must be
    able to move on to the next candidate.
    """
    ddir = dropin_dir()
    if not os.path.isdir(ddir):
        return []
    want = asset_name(plugin)
    hits = []
    exact = os.path.join(ddir, want)
    if os.path.exists(exact):
        hits.append(exact)

    # Be forgiving about the filename AND the depth.  Browsers rename
    # duplicates ("...(1).zip"), users rename things, and -- the case the old
    # top-level-only scan missed -- extracting a download commonly leaves the
    # asset one directory down (navmesh_cache/tes4skyrim-0.586/foo.zip), or the
    # user drags in the whole folder they downloaded.  Walk a bounded depth so
    # a nested drop is found without scanning an arbitrary tree.
    stem = want[:-4].lower()
    rest = []
    for root, dirs, files in os.walk(ddir):
        # depth 0 is navmesh_cache/ itself; the relative path carries a LEADING
        # separator, so `count(os.sep)` on it is already the folder level.
        # Prune only BELOW the documented three levels -- `>= 3` stopped at two
        # and contradicted both this docstring and navmesh_cache/README.md.
        depth = root[len(ddir):].count(os.sep)
        if depth >= 3:
            dirs[:] = []        # do not descend past level 3...
        dirs.sort()             # ...but still scan the files AT level 3
        for f in sorted(files):
            if f.lower().endswith('.zip') and stem in f.lower():
                path = os.path.join(root, f)
                if path != exact:
                    rest.append(path)
    # Shallowest match first: a file the user placed directly beats one left
    # inside an extracted folder.  The exact-name match at the top level, if it
    # exists, already leads.
    rest.sort(key=lambda p: (p.count(os.sep), p))
    return hits + rest


def _find_dropin(plugin: str) -> str | None:
    """The single best drop-in candidate, or None.  See find_dropins()."""
    got = find_dropins(plugin)
    return got[0] if got else None


def _local_version_str() -> str:
    """This build's version for display ('0.586+geefacb3', '0.0-dev', '?')."""
    try:
        import version
        return version.current_version()
    except Exception:
        return '?'


def _local_version_key() -> tuple | None:
    """(major, minor) for this build, or None if it cannot be determined.

    version.current_version() returns things like '0.586' or '0.586+geefacb3'
    (a checkout past the tag), so strip any local suffix before parsing.  None
    means "unknown" and makes the range check permissive rather than refusing
    everything -- a build we cannot place must still get a cache.

    UNKNOWN MUST NOT PARSE AS A NUMBER.  version.DEV_VERSION is the literal
    '0.0-dev', and the suffix strip below turns it into '0.0' -- which is a
    perfectly valid key of (0, 0), sorts BELOW every published range start, and
    therefore made the range check reject every release with "no published
    cache covers this build".  That silently disabled the download for exactly
    the population this range matching was added to serve: dev checkouts and
    source drops cut from an untagged commit.  A version we cannot place is
    None (permissive), never zero (excluded from everything).
    """
    try:
        import version
        raw = version.current_version() or ''
        dev = getattr(version, 'DEV_VERSION', '0.0-dev')
    except Exception:
        return None
    if not raw or raw == dev:
        return None
    # version.version_key() is the SAME question, already answered there, and it
    # strips the local suffix itself (via _base_tag).  Re-implementing the split
    # and the scaling here let the two drift: version.py scales only a 2-digit
    # minor, whereas _version_key() scales by the field's own width, so a
    # hypothetical '0.1234' produced a different key from each and the range
    # check would disagree with every other version comparison in the program.
    # One parser, one answer.
    key = version.version_key(raw)
    # A 0.0 base is the dev placeholder in any spelling, not a real release.
    return None if key is None or key == (0, 0) else key


def auto_install(plugin: str, quiet: bool = False,
                 allow_download: bool = True) -> bool:
    """Get this plugin's navmesh cache in place, with nothing for the user to do.

    Called at the top of the import phase, so the cache "just works" from the
    GUI and the CLI alike -- there is no command to learn and no flag to pass.
    Order of preference:

      1. The local cache is already current -> nothing to do.
      2. A zip dropped in `navmesh_cache/` -> install it (works offline, and is
         the answer for anyone without the GitHub CLI).
      3. Download the matching release asset -> install it.

    Downloading is skipped unless it would actually help: the manifest is
    checked FIRST via the release's own metadata, so a user whose navmesh code
    differs never pays for a ~115 MB transfer that would miss anyway.

    Every failure path is non-fatal and quiet-ish.  This runs inside a
    conversion; a missing, corrupt or mismatched cache must never stop a build
    that would otherwise succeed -- the navmesh simply regenerates, which is
    exactly what would have happened without any of this.
    """
    try:
        from tools.navmesh import navmesh_cache_hook as _hook
        if _hook.cache_matches_tag(plugin, source_tag(plugin)):
            # Say so.  Silence here is indistinguishable from the feature being
            # broken -- users reported "it does not download" for a cache that
            # was simply already in place and working perfectly.
            if not quiet:
                print('  Navmesh cache: already up to date -- navmesh '
                      'generation will be mostly skipped.')
            return False

        # EVERY candidate, not just the best one.  A truncated or 0-byte file
        # at the shallowest level used to shadow a perfectly good nested zip
        # and abort the whole function, taking the HTTPS fallback with it.
        for cand in find_dropins(plugin):
            if not quiet:
                print('  Navmesh cache: found %s, installing...'
                      % os.path.basename(cand))
            try:
                rc = install(plugin, None, cand)
            except Exception as exc:
                # install() handles the failures it knows about, but a drop-in
                # is an arbitrary user-supplied file -- anything it raises must
                # cost that ONE candidate, never the remaining ones or the
                # download below.
                rc = INSTALL_FAILED
                if not quiet:
                    print('  Navmesh cache: %s could not be read (%s).'
                          % (os.path.basename(cand), exc))
            if rc == INSTALL_OK:
                if not quiet:
                    print('  Navmesh cache: installed -- navmesh generation '
                          'will be mostly skipped.')
                return True
            if not quiet:
                # Name the ACTUAL cause.  Reporting every failure as "built by
                # different navmesh code" sent users chasing a version mismatch
                # when the real problem was a bad archive -- a wrong diagnosis
                # is worse than the generic wording it replaced.
                print('  Navmesh cache: %s' % (
                    'that drop-in was built by different navmesh code, so '
                    'every entry would miss; trying anything else.'
                    if rc == INSTALL_MISMATCH else
                    'that drop-in could not be installed; trying anything '
                    'else.'))

        if not allow_download:
            # Opting out is a deliberate choice, but still say why nothing
            # happened -- otherwise this looks identical to the feature being
            # broken, which is the whole class of report this path caused.
            if not quiet:
                print('  Navmesh cache: download disabled '
                      '(TESCONV_NO_CACHE_DOWNLOAD); generating normally.')
                print('    To use one offline, download %s from '
                      'https://github.com/%s/releases and drop it in %s/'
                      % (asset_name(plugin), api_repo(), DROPIN_DIRNAME))
            return False

        # ANONYMOUS HTTPS -- deliberately not gh.  Release assets on a public
        # repo need no credentials, and requiring the GitHub CLI would mean
        # almost every real user silently falls back to regenerating.
        want = source_tag(plugin)
        releases = _api_releases()
        if not releases:
            # [] means the API call FAILED (offline, proxy, rate limit) just as
            # much as it means "no releases" -- and staying silent about it is
            # why this reads as "the download does not work".  Say so, and point
            # at the offline route that always works.
            if not quiet:
                print('  Navmesh cache: could not reach the releases API '
                      '(offline or blocked); generating normally.')
                print('    To use a cache offline, download %s from '
                      'https://github.com/%s/releases and drop it in %s/'
                      % (asset_name(plugin), api_repo(), DROPIN_DIRNAME))
            return False

        # Pick the cache release COVERING this build's version.  A cache is
        # published as a RANGE -- 'navmesh-cache-0.586+' means "0.586 and
        # above", closed to '0.586-0.72' once superseded -- and that range is
        # the contract stated on the release page, so honour it here.
        #
        # The source tag is used as a TIE-BREAK, never as the sole gate.
        # Requiring an exact tag match was strictly narrower than the advertised
        # range: a user one commit past the release (any dev checkout, and every
        # source drop cut from an untagged commit) hashed differently and was
        # told there was no cache at all, even though the release explicitly
        # covers their version.  Entries self-validate regardless, so the worst
        # case of accepting a range match is a partial hit -- never wrong
        # geometry.
        mine = _local_version_key()
        best = None                 # ((range_start, exact_tag_match), asset)
        for rel in releases:
            parsed = parse_cache_release_tag(rel.get('tag_name', ''))
            if not parsed:
                continue
            lo = _version_key(parsed[0])
            hi = _version_key(parsed[1]) if parsed[1] else None
            if lo is None:
                continue
            # Range check, skipped when the local version is unknown (a bare
            # source drop) -- then the newest cache is the best guess.
            if mine is not None:
                if mine < lo:
                    continue
                if hi is not None and mine > hi:
                    continue
            found = None
            for a in rel.get('assets', ()):
                if a.get('name') == asset_name(plugin):
                    found = a
                    break
            if not found:
                continue
            body = rel.get('body') or ''
            marker = 'navmesh-source-tag:'
            exact = False
            if marker in body and want:
                got = body.split(marker, 1)[1].split('-->', 1)[0].strip()
                exact = (got == want)
            # HIGHEST COVERING RANGE START FIRST; the source tag only breaks a
            # tie between equally-current releases.  Ranking the tag above the
            # range inverts that and picks an OBSOLETE cache: the source tag is
            # not a proxy for "newer" -- it matches across distant versions
            # whenever the navmesh code simply did not change, so an ancient
            # release can carry a matching tag and outrank the current one.
            # That is not hypothetical: close_cache_release() is what retires a
            # superseded release, and its own failure path warns the release
            # "will keep claiming '<start> and above'" -- a stale open-ended
            # range is a state this code explicitly expects to exist.
            rank = (lo, 1 if exact else 0)
            if best is None or rank > best[0]:
                best = (rank, found)

        asset = best[1] if best else None

        if not asset:
            if not quiet:
                print('  Navmesh cache: no published cache covers this build '
                      '(%s) for %s; generating normally.'
                      % (_local_version_str(), plugin))
            return False

        os.makedirs(os.path.join(repo_root(), 'temp'), exist_ok=True)
        tmp = os.path.join(repo_root(), 'temp', asset_name(plugin))
        if not quiet:
            # Announce loudly and BEFORE the transfer starts.  This is a
            # ~115 MB download in the middle of a conversion; a user watching
            # the log must be able to see what it is doing and why, rather than
            # wondering whether the import has hung.
            print('  Navmesh cache: downloading %s (%.0f MB) -- this replaces '
                  'minutes of navmesh generation.'
                  % (asset['name'], asset.get('size', 0) / (1 << 20)),
                  flush=True)
        if not _download(asset['browser_download_url'], tmp, quiet=quiet):
            if not quiet:
                print('  Navmesh cache: download failed; generating normally.')
            return False

        rc = install(plugin, None, tmp)
        try:
            os.remove(tmp)
        except OSError:
            pass
        if not quiet:
            print('  Navmesh cache: %s' % (
                'installed -- navmesh generation will be mostly skipped.'
                if rc == 0 else
                'downloaded cache does not match; generating normally.'))
        return rc == 0
    except Exception as exc:                      # never break an import
        if not quiet:
            print('  Navmesh cache: skipped (%s)' % exc)
        return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description='Publish / install the shared navmesh geometry cache.')
    sub = ap.add_subparsers(dest='cmd', required=True)

    def add_plugin(p):
        p.add_argument('--plugin', action='append', dest='plugins',
                       help='plugin folder under export/ (repeatable; '
                            'default: the publishable set -- %s; `verify` '
                            'with no --plugin reports every local cache)'
                            % ', '.join(PUBLISHABLE_PLUGINS))

    p_ver = sub.add_parser('verify', help='check the local cache is publishable')
    add_plugin(p_ver)
    p_ver.add_argument('--sample', type=int, default=0,
                       help='only read N entries (default: all)')

    p_arc = sub.add_parser('archive', help='zip the cache without uploading')
    add_plugin(p_arc)
    p_arc.add_argument('--out-dir', default='temp')
    p_arc.add_argument('--tag', default='unreleased',
                       help='starting tag recorded in the manifest')

    p_pub = sub.add_parser('publish', help='archive + upload to a release')
    add_plugin(p_pub)
    p_pub.add_argument('--tag', required=True, help='release tag, e.g. 0.56')
    p_pub.add_argument('--out-dir', default='temp')
    p_pub.add_argument('--dry-run', action='store_true')

    p_ins = sub.add_parser('install', help='download + unpack a published cache')
    p_ins.add_argument('--plugin', required=True)
    p_ins.add_argument('--tag', help='release tag (default: latest)')
    p_ins.add_argument('--zip', dest='zip_path', help='install a local zip')
    p_ins.add_argument('--force', action='store_true',
                       help='install even if the manifest does not match')

    args = ap.parse_args(argv)

    if args.cmd == 'install':
        return install(args.plugin, args.tag, args.zip_path, args.force)

    # An explicit --plugin is taken at face value (inspecting or archiving a
    # non-published cache by hand is legitimate); the default list is the
    # publishable set, except for `verify`, which is read-only and more useful
    # when it reports every cache on the machine.
    plugins = args.plugins or discover_plugins(all_plugins=args.cmd == 'verify')
    if not plugins:
        print('No plugins with a navmesh cache found under export/.')
        return 1

    if args.cmd in ('archive', 'publish'):
        skipped = [p for p in plugins if not is_publishable(p)]
        if skipped and not args.plugins:
            print('Skipping (not in PUBLISHABLE_PLUGINS): %s'
                  % ', '.join(skipped))
        plugins = [p for p in plugins if is_publishable(p)] \
            if not args.plugins else plugins
        if not plugins:
            print('No publishable navmesh caches found under export/ (%s).'
                  % ', '.join(PUBLISHABLE_PLUGINS))
            return 1

    if args.cmd == 'verify':
        ok = True
        for plugin in plugins:
            ok &= verify(plugin, sample=args.sample)['ok']
        return 0 if ok else 1

    if args.cmd == 'archive':
        for plugin in plugins:
            if archive(plugin, args.out_dir, args.tag) is None:
                return 1
        return 0

    if args.cmd == 'publish':
        return publish(plugins, args.tag, args.out_dir, args.dry_run)

    return 1


if __name__ == '__main__':
    raise SystemExit(main())
