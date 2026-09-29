"""Case-blind asset paths: find, write and audit files whose recorded case differs from disk.

Records and NIFs keep their author's mixed case, extracted trees are
lowercase and a mod's loose files keep whatever case it shipped, so on a
case-sensitive filesystem every lookup ignores case, every write reuses the
spelling already on disk, and the packer merges case-variant folders.  An
exact hit is always tried first, so Windows never lists a folder.

See: docs/commentary/asset_convert_paths.md#case-resolver
"""

import os
import time
from collections import namedtuple
from pathlib import Path

#: dir -> (mtime_ns, listed_at_ns, {lower name: [names]}), or None when unreadable.
_LISTINGS = {}

#: A listing read this soon after its folder changed may miss a same-tick create.
_RACY_NS = 2_000_000_000

#: site -> {field: count}, for this process only; workers hand theirs back.
_COUNTS = {}

#: (root, lowercase rel) pairs already logged as a CASE COLLISION.
_COLLIDED = set()

_FIELDS = ('exact', 'resolved', 'missed', 'collisions')

#: dup_groups: sibling folders differing only by case; file_collisions: files sharing one key.
Census = namedtuple('Census', 'dup_groups file_collisions files')


# ---------------------------------------------------------------------------
# Folder listings
# ---------------------------------------------------------------------------


def split_rel(rel) -> list:
    """`rel`'s segments; either separator, empty segments dropped."""
    return [p for p in str(rel).replace('/', '\\').split('\\') if p]


def _read(path):
    """A fresh listing entry for folder `path`, or None when it cannot be listed."""
    stamp = time.time_ns()
    try:
        mtime = os.stat(path).st_mtime_ns
        names = sorted(os.listdir(path))
    except OSError:
        return None
    index = {}
    for name in names:
        index.setdefault(name.lower(), []).append(name)
    return mtime, stamp, index


def _stale(path, entry) -> bool:
    """True when folder `path` may have changed since `entry` was read."""
    if entry is None:
        return True
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        return True
    return mtime != entry[0] or entry[1] - mtime < _RACY_NS


def _norm(root) -> str:
    """`root` as the listing cache keys it: one spelling per folder."""
    return os.path.normpath(os.fspath(root))


def _listing(path, fresh=False) -> dict:
    """`{lower: [names]}` for `path`; `fresh` re-reads it when it may have changed."""
    entry = _LISTINGS.get(path, False)
    if entry is False or (fresh and _stale(path, entry)):
        entry = _LISTINGS[path] = _read(path)
    return entry[2] if entry else {}


def _matches(root, parts, fresh=False) -> list:
    """Every existing path under `root` spelling `parts` in any case.

    Branches into every case-variant folder, so an empty `Dementia/` beside
    `dementia/x.dds` cannot hide the file.
    """
    found = [_norm(root)]
    for seg in parts:
        key = seg.lower()
        found = [os.path.join(d, n) for d in found
                 for n in _listing(d, fresh).get(key, ())]
        if not found:
            break
    return found


def _ranked(paths, root) -> list:
    """`paths` with the all-lowercase spelling first, then by path string."""
    cut = len(str(root))
    return sorted(paths, key=lambda p: (p[cut:] != p[cut:].lower(), p))


def _as_list(roots) -> list:
    """Accept one root or an ordered collection of them."""
    if not roots:
        return []
    if isinstance(roots, (str, os.PathLike)):
        return [roots]
    return list(roots)


def invalidate(root=None) -> None:
    """Forget cached listings under `root` (all of them when None)."""
    if root is None:
        _LISTINGS.clear()
        return
    top = _norm(root)
    for key in [k for k in _LISTINGS if k == top or k.startswith(top + os.sep)]:
        del _LISTINGS[key]


# ---------------------------------------------------------------------------
# Finding
# ---------------------------------------------------------------------------


def _counts(site) -> dict:
    """The live counter for `site`."""
    return _COUNTS.setdefault(site, dict.fromkeys(_FIELDS, 0))


def _collided(site, root, rel, hits) -> None:
    """Log and count two spellings of one path, once per key."""
    key = (str(root), '/'.join(split_rel(rel)).lower())
    if key in _COLLIDED:
        return
    _COLLIDED.add(key)
    _counts(site)['collisions'] += 1
    print('  CASE COLLISION [%s]: %s -> %s' % (site, key[1], ', '.join(hits)))


def variants(root, rel) -> list:
    """Every existing spelling of `rel` under `root` (files or folders), ranked."""
    parts = split_rel(rel)
    got = _matches(root, parts) or _matches(root, parts, fresh=True)
    return _ranked(got, root)


def _files(root, parts) -> list:
    """The existing files spelling `parts` under `root`, ranked."""
    for fresh in (False, True):
        hits = [p for p in _matches(root, parts, fresh) if os.path.isfile(p)]
        if hits:
            return _ranked(hits, root)
    return []


def resolve(roots, rel, site='?'):
    """The existing file `rel` names, searching `roots` in order, or None.

    Each root is tried exactly, then case-blind, before the next one; two
    spellings of one file in a root answer with the lowercase one and log
    CASE COLLISION.
    See: docs/commentary/asset_convert_paths.md#case-resolver
    """
    counts = _counts(site)
    parts = split_rel(rel)
    for root in _as_list(roots):
        exact = Path(root).joinpath(*parts)
        if parts and exact.is_file():
            counts['exact'] += 1
            return exact
        hits = _files(root, parts)
        if hits:
            counts['resolved'] += 1
            if len(hits) > 1:
                _collided(site, root, rel, hits)
            return Path(hits[0])
    counts['missed'] += 1
    return None


def exists(roots, rel, site='?') -> bool:
    """True when `resolve` finds `rel` under one of `roots`."""
    return resolve(roots, rel, site) is not None


def rglob(root, pattern) -> list:
    """Every path under `root` matching `pattern`, ignoring case, sorted."""
    return sorted(Path(root).rglob(pattern, case_sensitive=False))


def list_prefix(root, rel_dir, prefix) -> list:
    """The files in every spelling of `root/rel_dir` whose name starts with `prefix`, any case."""
    low = prefix.lower()
    out = []
    for d in variants(root, rel_dir):
        out += [Path(d, n) for names in _listing(d, fresh=True).values()
                for n in names
                if n.lower().startswith(low) and os.path.isfile(os.path.join(d, n))]
    return sorted(out)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _kind(parent, names, want_dir) -> list:
    """The `names` in `parent` that are folders (`want_dir`) or not."""
    return [n for n in names if os.path.isdir(os.path.join(parent, n)) == want_dir]


def _spelling(parent, seg, want_dir) -> tuple:
    """(the existing spelling of `seg` in `parent`, else lowercase; True when new).

    The cached listing answers first; only a miss re-reads the folder, so an
    unchanged ancestor is never listed twice. Two or more spellings collide.
    """
    key = seg.lower()
    names = (_kind(parent, _listing(parent).get(key, ()), want_dir)
             or _kind(parent, _listing(parent, fresh=True).get(key, ()), want_dir))
    if len(names) == 1:
        return names[0], False
    if not names:
        return key, True
    ranked = _ranked([os.path.join(parent, n) for n in names], parent)
    _collided('write_path', parent, seg, ranked)
    return os.path.basename(ranked[0]), False


def write_path(root, rel) -> Path:
    """Where to write `rel` under `root`, creating its parent folders.

    Each segment reuses the one spelling already on disk, is lowercase when
    none exists, and takes the lowercase one when several do. Listings are
    cached; a folder that gains a new entry here is dropped from the cache.
    See: docs/commentary/asset_convert_paths.md#write-rule
    """
    parts = split_rel(rel)
    path, grown = _norm(root), []
    for i, seg in enumerate(parts):
        name, new = _spelling(path, seg, i < len(parts) - 1)
        if new:
            grown.append(path)
        path = os.path.join(path, name)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    for folder in grown:
        _LISTINGS.pop(folder, None)
    return out


# ---------------------------------------------------------------------------
# Census and report
# ---------------------------------------------------------------------------


def collisions(keyed) -> list:
    """The path groups of `keyed` ({archive key: [paths]}) holding two or more files."""
    return sorted(tuple(sorted(v)) for v in keyed.values() if len(v) > 1)


def _twins(base, names) -> list:
    """Groups of `names` in folder `base` that differ only by case."""
    by_key = {}
    for n in names:
        by_key.setdefault(n.lower(), []).append(os.path.join(base, n))
    return [tuple(sorted(v)) for v in by_key.values() if len(v) > 1]


def _tops(root, subdirs) -> list:
    """(lowercase archive top, folder) for every spelling of each subdir, or the root itself."""
    if not subdirs:
        return [('', str(root))]
    names = sorted({n.lower() for n in subdirs})
    return [(n, d) for n in names for d in variants(root, n) if os.path.isdir(d)]


def census(root, subdirs=None) -> Census:
    """Case twins under `root`: folder twins (warn), file collisions (fail), file count.

    With `subdirs`, every spelling of each one is walked as the single tree
    the packer merges it into, so those top folders are not twins.  File keys
    are lowercase archive paths.
    See: docs/commentary/asset_convert_paths.md#census
    """
    groups, keyed = [], {}
    for top, folder in _tops(root, subdirs):
        for base, dirs, files in os.walk(folder):
            dirs.sort()
            groups += _twins(base, dirs)
            rel = os.path.relpath(base, folder)
            prefix = [top] + ([] if rel == '.' else rel.split(os.sep))
            for f in files:
                key = '/'.join([p for p in prefix if p] + [f]).lower()
                keyed.setdefault(key, []).append(os.path.join(base, f))
    return Census(sorted(groups), collisions(keyed),
                  sum(len(v) for v in keyed.values()))


def census_line(label, c) -> str:
    """One log line for census `c` of `label`."""
    verdict = 'FAIL' if c.file_collisions else ('WARN' if c.dup_groups else 'ok')
    return ('Case census %s: %s -- %d files, %d case-twin folder groups, '
            '%d file collisions' % (label, verdict, c.files, len(c.dup_groups),
                                    len(c.file_collisions)))


def snapshot_counts() -> dict:
    """This process's per-site counts, then reset them (a worker hands these back)."""
    out = {site: dict(c) for site, c in _COUNTS.items()}
    _COUNTS.clear()
    return out


def merge_counts(counts) -> None:
    """Add a worker's `snapshot_counts()` into this process's counts."""
    for site, c in (counts or {}).items():
        mine = _counts(site)
        for field in _FIELDS:
            mine[field] += c.get(field, 0)


def report(printer=print) -> None:
    """Print each site's exact/resolved/missed/collision counts, then reset them."""
    for site in sorted(_COUNTS):
        c = _COUNTS[site]
        printer('  Case paths: %s exact %d / resolved %d / missed %d / '
                'collisions %d' % (site, *(c[f] for f in _FIELDS)))
    _COUNTS.clear()
