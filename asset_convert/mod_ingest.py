"""Import a mod archive (or an extracted mod folder) as a conversion source.

The pipeline normally converts a plugin sitting in the Oblivion `Data`
directory, pulling its assets out of the BSAs beside it. Most downloadable mods
are not shaped like that: they are a `.zip`/`.7z`/`.rar` holding loose
`Meshes\\`/`Textures\\` folders, or BSAs, or both, plus one or more plugins.

Ingest bridges the two. Its ONLY job is to produce the same
`export/<plugin>/` tree the BSA extractor already produces:

    export/<plugin>/
        meshes/  textures/  sound/  trees/  misc/     <- assets
        _source/<plugin>                              <- the TES4 binary
        _source/<original archive>                    <- retained for re-import
        _source/.mod_ingest_manifest.json             <- cache key

Because the output shape is identical, every stage after extraction --
export, import, meshes, scripts, LOD -- is untouched and cannot tell the
difference.

Layout rule (the one thing mods disagree about):

    If a `Data` folder exists anywhere in the archive, the payload is that
    folder's contents, however deeply nested. Otherwise the payload is the
    archive root.

Verified against three real Oblivion mods, which use three different layouts:
  * Elsweyr Anequina  -- loose Meshes/Textures/DistantLOD + 3 BSAs at the ROOT
  * TWMP High Rock    -- TWMP_HighRock\\Data\\ with a BSA and TWO plugins
  * TWMP Skyrim       -- TWMP_Skyrim\\Data\\ with TWO plugins and no assets

Precedence inside one import: BSAs are extracted FIRST and loose files overlay
them, because that is the engine's own rule (a loose file always wins over the
same path inside an archive).
"""
import hashlib
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from . import archive, bsa_extract, source_registry

MANIFEST_NAME = '.mod_ingest_manifest.json'

# How deep to follow archives-inside-archives before giving up. Mods that nest
# do so once (a wrapper zip around the real one); 3 is generous and bounds a
# malicious chain.
MAX_NEST_DEPTH = 3

# Refuse to unpack more than this in one import. A zip bomb is small on disk
# and enormous unpacked; the largest legitimate mod archive measured here
# (Elsweyr Anequina) is ~1 GB unpacked, so 64 GB is far above any real mod
# while still bounding the damage.
MAX_TOTAL_BYTES = 64 * 1024 ** 3

PLUGIN_EXTS = ('.esp', '.esm')


class IngestError(RuntimeError):
    """The archive could not be imported."""


# ---------------------------------------------------------------------------
#  Inspection (read-only)
# ---------------------------------------------------------------------------

class ArchiveManifest:
    """What an archive contains, without extracting anything.

    Everything the import dialog needs to render itself, and everything
    `ingest` needs to do the work.
    """

    def __init__(self, path, is_folder=False):
        self.path = Path(path)
        self.is_folder = is_folder
        self.payload_root = ''      # '' = archive root, else 'X/Data'
        self.data_folder = None     # the Data dir found, for display
        self.plugins = []           # member paths, payload-relative
        self.bsas = []              # member paths, payload-relative
        self.nested = []            # nested archive member paths
        self.counts = {}            # category -> file count
        self.total_bytes = 0
        self.fomod = None           # payload-relative ModuleConfig.xml, or None
        self.ambiguous_data = []    # >1 equally shallow Data dirs
        self.members = []           # all Member objects (payload-relative)

    @property
    def asset_only(self) -> bool:
        """True for a mod that ships assets but no plugin (a replacer/pack)."""
        return not self.plugins

    @property
    def target_name(self) -> str:
        """The `export/<name>/` folder this mod's assets go in.

        Normally the primary plugin. An asset-only mod has no plugin to name
        itself after, so it uses its own label -- which is what the Source
        dropdown shows, so the two always agree.
        """
        return Path(self.plugins[0]).name if self.plugins else self.label

    @property
    def label(self) -> str:
        """A human name for this mod, used as the source-scope label.

        Nexus filenames carry site metadata: "Elsweyr Anequina-25023-March-
        2014-1561735850.rar", "Skyrim esp-40005-0-1.rar". Strip the trailing
        id/date/version run, but never strip so much that the name stops
        identifying the mod -- if the archive name is uninformative, the
        top-level folder inside it usually is not ("TWMP_Skyrim").
        """
        import re
        stem = self.path.name if self.is_folder else self.path.stem
        cleaned = re.split(r'-\d{3,}', stem)[0].strip(' -_')

        # Uploaders tack the packaging format onto the name ("Tamriel Landscape
        # Pack bsa", "Skyrim esp"). It describes the download, not the mod.
        had_suffix = bool(re.search(r'[\s_-]+(esp|esm|bsa|data|loose|files?)\s*$',
                                    cleaned, flags=re.I))
        trimmed = re.sub(r'[\s_-]+(esp|esm|bsa|data|loose|files?)\s*$', '',
                         cleaned, flags=re.I).strip(' -_') if had_suffix \
            else cleaned

        # A named top-level folder beats a vague archive name. "Skyrim esp"
        # trims to "Skyrim", which is still not the mod -- "TWMP_Skyrim" is.
        # So prefer the folder whenever it CONTAINS the trimmed name (it is the
        # same mod, spelled more fully) or the trimmed name is too short to
        # identify anything.
        wrapper = self._wrapper_folder()
        if wrapper and (len(trimmed) < 4
                        or (had_suffix
                            and trimmed.lower().replace(' ', '_')
                            in wrapper.lower())):
            return wrapper
        return trimmed or cleaned or stem

    def _wrapper_folder(self):
        """The single top-level folder the payload lives under, if any."""
        if self.payload_root:
            head = self.payload_root.split('/')[0]
            if head.lower() != 'data':
                return head
        return None

    def summary(self) -> str:
        bits = [f"{sum(self.counts.values())} files"]
        for cat in ('meshes', 'textures', 'sound', 'trees', 'misc'):
            if self.counts.get(cat):
                bits.append(f"{self.counts[cat]} {cat}")
        return ', '.join(bits)


def _find_payload_root(member_paths):
    """Apply the layout rule. Returns (payload_root, ambiguous_list).

    payload_root is '' for "archive root", else a forward-slash prefix.
    """
    # Every directory named "Data", by depth. A path contributes its Data dir
    # whether or not the archive carries explicit directory entries -- some
    # archives (and every folder walk) only list files.
    candidates = {}
    for p in member_paths:
        parts = p.split('/')
        for i, part in enumerate(parts[:-1]):      # never the filename itself
            if part.lower() == 'data':
                prefix = '/'.join(parts[:i + 1])
                candidates.setdefault(prefix, i)   # i = depth (0 = top level)

    if not candidates:
        return '', []

    shallowest = min(candidates.values())
    tied = sorted(p for p, d in candidates.items() if d == shallowest)
    # A single shallowest Data dir wins. Several at the same depth is genuinely
    # ambiguous -- report it rather than silently picking one.
    return tied[0], (tied if len(tied) > 1 else [])


def _payload_relative(member_path, payload_root):
    """Strip payload_root from a member path, or None if outside it."""
    if not payload_root:
        return member_path
    prefix = payload_root + '/'
    if member_path.lower().startswith(prefix.lower()):
        return member_path[len(prefix):]
    return None


def _folder_members(root):
    """List a directory the same way `archive.list_members` lists an archive."""
    root = Path(root)
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            full = Path(dirpath) / fn
            rel = full.relative_to(root).as_posix()
            try:
                size = full.stat().st_size
            except OSError:
                size = 0
            out.append(archive.Member(rel, size, False))
    return sorted(out, key=lambda m: m.path.lower())


def inspect(path, max_depth=MAX_NEST_DEPTH) -> ArchiveManifest:
    """Read-only: what would be imported from `path`. Writes nothing.

    `path` may be an archive file or an already-extracted mod folder.
    """
    path = Path(path)
    if path.is_dir():
        man = ArchiveManifest(path, is_folder=True)
        members = _folder_members(path)
    elif path.is_file():
        if not archive.is_archive(path):
            raise IngestError(
                f'{path.name} is not a mod archive '
                f'(expected {", ".join(sorted(archive.ARCHIVE_EXTS))}).')
        man = ArchiveManifest(path, is_folder=False)
        members = archive.list_members(path)
    else:
        raise IngestError(f'not found: {path}')

    files = [m for m in members if not m.is_dir]
    if not files:
        raise IngestError(f'{path.name} is empty.')

    root, ambiguous = _find_payload_root([m.path for m in files])
    man.payload_root = root
    man.data_folder = root or None
    man.ambiguous_data = ambiguous

    for m in files:
        rel = _payload_relative(m.path, root)
        if rel is None:
            continue                       # outside the payload: readmes etc.
        man.members.append(archive.Member(rel, m.size, False))
        man.total_bytes += m.size
        ext = os.path.splitext(rel)[1].lower()
        if ext in PLUGIN_EXTS:
            man.plugins.append(rel)
        elif ext == '.bsa':
            man.bsas.append(rel)
        elif ext in archive.ARCHIVE_EXTS and max_depth > 0:
            man.nested.append(rel)
        if rel.lower().endswith('fomod/moduleconfig.xml'):
            man.fomod = rel
        cat, _ = bsa_extract.split_category(rel)
        man.counts[cat] = man.counts.get(cat, 0) + 1

    man.plugins.sort(key=str.lower)
    man.bsas.sort(key=str.lower)
    man.nested.sort(key=str.lower)

    # A mod with NO plugin is still a mod: texture/mesh replacers and resource
    # packs ship assets only (e.g. "Tamriel Landscape Pack" = one BSA of 2,018
    # meshes/textures/trees). They convert perfectly well -- there is simply
    # nothing to export or import, so those steps are unavailable rather than
    # the whole archive being unusable.
    # `misc` alone does not count: an archive of nothing but readmes and
    # screenshots lands entirely there, and importing it would create an empty
    # source. Require a plugin, a BSA, or at least one real asset category.
    real_assets = any(man.counts.get(c) for c in bsa_extract.ASSET_CATEGORIES)
    if not man.plugins and not man.bsas and not real_assets:
        raise IngestError(
            f'{path.name} contains no plugin and no assets, so there is '
            f'nothing to convert.')
    return man


# ---------------------------------------------------------------------------
#  Ingest
# ---------------------------------------------------------------------------

def _sha1_file(path, chunk=4 * 1024 * 1024):
    h = hashlib.sha1()
    with open(path, 'rb') as fh:
        while True:
            blk = fh.read(chunk)
            if not blk:
                break
            h.update(blk)
    return h.hexdigest()


def _cache_key(path, is_folder):
    """Identity of the source, for the idempotence manifest."""
    if is_folder:
        # Hashing a whole mod folder would cost as much as re-importing it.
        # Size + newest mtime is enough to notice a changed folder.
        total, newest = 0, 0.0
        for dirpath, _d, files in os.walk(path):
            for fn in files:
                try:
                    st = os.stat(os.path.join(dirpath, fn))
                except OSError:
                    continue
                total += st.st_size
                newest = max(newest, st.st_mtime)
        return {'kind': 'folder', 'size': total, 'mtime': round(newest, 3)}
    st = os.stat(path)
    return {'kind': 'archive', 'size': st.st_size, 'sha1': _sha1_file(path)}


def _read_manifest(plugin_dir):
    p = Path(plugin_dir) / source_registry.SOURCE_SUBDIR / MANIFEST_NAME
    if not p.is_file():
        return None
    try:
        with open(p, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write_manifest(plugin_dir, data):
    d = Path(plugin_dir) / source_registry.SOURCE_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    with open(d / MANIFEST_NAME, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2, sort_keys=True)


def _place_payload(staged_root, plugin_dir, counts, log):
    """Route every staged file into `plugin_dir` by asset category.

    Returns the number of files placed. Loose files overwrite whatever the BSA
    pass put there, which is the engine's own precedence rule.
    """
    placed = 0
    for dirpath, _dirs, files in os.walk(staged_root):
        for fn in sorted(files):
            full = Path(dirpath) / fn
            rel = full.relative_to(staged_root).as_posix()
            if not bsa_extract._should_extract_file(rel):
                continue
            ext = os.path.splitext(rel)[1].lower()
            # Plugins and BSAs are handled separately; they are not assets.
            if ext in PLUGIN_EXTS or ext == '.bsa':
                continue
            out_rel = bsa_extract.categorize(rel)
            target = archive.safe_join(plugin_dir, out_rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                target.unlink()
            shutil.move(str(full), str(target))
            cat = out_rel.split('/', 1)[0]
            counts[cat] = counts.get(cat, 0) + 1
            placed += 1
    return placed


def _stage(src, staged, manifest, log, depth=0, budget=None):
    """Unpack `src`'s payload into `staged/`, recursing into nested archives.

    `budget` is a one-element list carrying the remaining byte allowance, so
    the cap spans the whole recursion rather than each archive separately.
    """
    if budget is None:
        budget = [MAX_TOTAL_BYTES]

    if manifest.is_folder:
        # Copy the payload subtree out of the folder.
        root = Path(src)
        base = root / manifest.payload_root if manifest.payload_root else root
        for m in manifest.members:
            srcf = base / m.path
            if not srcf.is_file():
                continue
            dstf = archive.safe_join(staged, m.path)
            dstf.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(srcf, dstf)
        return

    if manifest.total_bytes > budget[0]:
        raise IngestError(
            f'{Path(src).name} unpacks to '
            f'{manifest.total_bytes / 1024**3:.1f} GB, over the '
            f'{MAX_TOTAL_BYTES / 1024**3:.0f} GB import limit.')
    budget[0] -= manifest.total_bytes

    if manifest.payload_root:
        # Extract only the payload subtree, then lift it to the staging root.
        with tempfile.TemporaryDirectory(prefix='tesconv_arc_') as tmp:
            archive.extract_all(src, tmp)
            base = Path(tmp) / manifest.payload_root
            if not base.is_dir():
                raise IngestError(
                    f'expected {manifest.payload_root!r} inside '
                    f'{Path(src).name}, but it was not extracted')
            for item in base.iterdir():
                dest = Path(staged) / item.name
                if dest.exists():
                    if dest.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                shutil.move(str(item), str(dest))
    else:
        archive.extract_all(src, staged)

    # Nested archives: unpack each into the SAME staging tree. The outer
    # archive's files were written first, so `_place_payload` moving files out
    # later means an outer file already sitting at a path is overwritten by the
    # nested one only if the nested archive is expanded after -- which is why
    # nested expansion happens here, before placement, and outer-wins is
    # enforced by extracting nested content into a scratch dir and only filling
    # gaps.
    if depth >= MAX_NEST_DEPTH:
        if manifest.nested:
            log(f"  Nested archives at depth {depth} skipped "
                f"(limit {MAX_NEST_DEPTH}): "
                f"{', '.join(manifest.nested[:3])}")
        return

    for rel in manifest.nested:
        inner = Path(staged) / rel
        if not inner.is_file():
            continue
        log(f"  Nested archive: {rel}")
        try:
            inner_man = inspect(inner, max_depth=MAX_NEST_DEPTH - depth - 1)
        except IngestError as exc:
            # A nested archive with no plugin is normal (a texture pack option).
            # Unpack it for its assets rather than skipping it.
            inner_man = None
            log(f"    ({exc})")
        with tempfile.TemporaryDirectory(prefix='tesconv_nest_') as tmp:
            if inner_man is not None:
                _stage(inner, tmp, inner_man, log, depth + 1, budget)
            else:
                archive.extract_all(inner, tmp)
            # Outer wins: only copy what the outer archive did not provide.
            for dirpath, _d, files in os.walk(tmp):
                for fn in files:
                    full = Path(dirpath) / fn
                    rel_in = full.relative_to(tmp).as_posix()
                    dest = archive.safe_join(staged, rel_in)
                    if dest.exists():
                        continue
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(full), str(dest))
        inner.unlink(missing_ok=True)


def _resolve_members(requested, man):
    """Map user-supplied plugin names onto this archive's member paths.

    `man.plugins` are PAYLOAD-relative ('TWMP_HighRock.esp'), but the obvious
    thing to type -- and what a listing tool prints -- is the full archive path
    ('TWMP_HighRock/Data/TWMP_HighRock.esp'). Accept either, plus a bare
    filename, so `--plugin-member` cannot fail on a technicality the user has
    no way to know about.
    """
    if not requested:
        # An asset-only mod legitimately has none; the caller registers it
        # under the mod's own name instead.
        return list(man.plugins)

    by_full, by_name = {}, {}
    for rel in man.plugins:
        full = f'{man.payload_root}/{rel}' if man.payload_root else rel
        by_full[full.lower()] = rel
        by_name.setdefault(os.path.basename(rel).lower(), rel)

    chosen, unknown = [], []
    for want in requested:
        key = str(want).replace('\\', '/').strip().lower()
        hit = (by_full.get(key)
               or by_name.get(os.path.basename(key))
               or (key if key in {p.lower() for p in man.plugins} else None))
        if hit is None:
            unknown.append(want)
        else:
            # `hit` may be a lower-cased key; map back to the real member path.
            match = next((p for p in man.plugins if p.lower() == hit.lower()),
                         hit)
            if match not in chosen:
                chosen.append(match)
    if unknown:
        raise IngestError(
            f'not in this archive: {", ".join(map(str, unknown))}\n'
            f'available: {", ".join(man.plugins) or "(none -- asset-only mod)"}')
    if not chosen:
        raise IngestError('no plugins selected to import')
    return chosen


def ingest(path, export_dir, plugin_members=None, keep_archive=True,
           force=False, log=print, manifest=None):
    """Import `path` (archive or folder) into `export_dir`.

    `plugin_members`: which plugins to register (default: all found).
    `keep_archive`:   retain a copy of the archive under `_source/` so the
                      import can be re-run after the download is deleted.
    Returns a dict of per-plugin results.
    """
    path = Path(path)
    export_dir = Path(export_dir)
    man = manifest or inspect(path)

    chosen = _resolve_members(plugin_members, man)

    key = _cache_key(path, man.is_folder)
    group_id = f"{_slug(man.label)}-{(key.get('sha1') or str(key['size']))[:8]}"

    # An asset-only mod is registered under its own name -- there is no plugin
    # to name it after, but its assets still need an export/<name>/ folder.
    names = [Path(p).name for p in chosen] or [man.target_name]

    # Idempotence: if every registered name already carries a manifest with
    # this exact key, there is nothing to do.
    if not force:
        group_dir = export_dir / (source_registry._sanitize_folder(man.label)
                                  or names[0])
        got = (_read_manifest(group_dir) or {})
        # Re-import when the archive changed OR when this run would add a
        # plugin the group folder does not already hold: a --plugin-member
        # import of one plugin must not mark the whole mod as done.
        #
        # Only PLUGINS are looked for. An asset-only mod registers under its
        # own label, which is not a file and never lands in _source/ (only the
        # retained archive does), so requiring it here could never be satisfied
        # and every run re-extracted the whole archive.
        have = {q.name.lower()
                for q in (group_dir / source_registry.SOURCE_SUBDIR).glob('*')
                } if group_dir.is_dir() else set()
        missing = [n for n in names if chosen and n.lower() not in have]
        if got.get('key') == key and not missing:
            log(f"  {path.name}: already imported (unchanged), skipping.")
            return {n: {'cached': True} for n in names}

    log(f"Importing {path.name}")
    log(f"  Layout: {'Data folder ' + man.payload_root if man.payload_root else 'archive root'}")
    if chosen:
        log(f"  Plugins: {', '.join(names)}")
    else:
        log("  No plugin (asset-only mod) -- Export/Import/Scripts will be "
            "unavailable")
    if man.bsas:
        log(f"  BSAs: {len(man.bsas)}")

    results = {}
    primary = names[0]
    # ONE asset tree per mod, named for the mod. Every plugin in the archive
    # reads from it, so there is nothing to copy or hard-link per plugin.
    group_name = source_registry._sanitize_folder(man.label) or primary
    primary_dir = export_dir / group_name

    with tempfile.TemporaryDirectory(prefix='tesconv_ingest_') as staging:
        staged = Path(staging) / 'payload'
        staged.mkdir(parents=True, exist_ok=True)
        _stage(path, staged, man, log)

        counts = {}

        # 1) BSAs first -- loose files must be able to overwrite them.
        for rel in man.bsas:
            bsa_path = staged / rel
            if not bsa_path.is_file():
                continue
            log(f"  Extracting {Path(rel).name}...")
            # Into the GROUP folder, the same tree the loose payload lands
            # in -- otherwise loose files could not overwrite BSA content.
            bsa_extract.extract_bsa(bsa_path, str(export_dir), force=True,
                                    source_name=group_name)
            bsa_path.unlink(missing_ok=True)

        # 2) Loose payload overlays whatever the BSAs wrote.
        placed = _place_payload(staged, primary_dir, counts, log)
        log(f"  Placed {placed} loose files")

        # 3) The plugin binaries: all into the ONE shared _source/.
        dest_dir = primary_dir / source_registry.SOURCE_SUBDIR
        dest_dir.mkdir(parents=True, exist_ok=True)
        for rel in chosen:
            name = Path(rel).name
            staged_plugin = staged / rel
            if not staged_plugin.is_file():
                raise IngestError(f'plugin missing after extraction: {rel}')
            dest = dest_dir / name
            if dest.exists():
                dest.unlink()
            shutil.move(str(staged_plugin), str(dest))

    # 4) Retain the archive so re-import survives the download being deleted.
    retained = ''
    if keep_archive and not man.is_folder:
        # Created here, not just in the plugin loop: an asset-only mod has no
        # plugin to write into _source/, but still retains its archive there.
        src_dir = primary_dir / source_registry.SOURCE_SUBDIR
        src_dir.mkdir(parents=True, exist_ok=True)
        dest = src_dir / path.name
        if not dest.is_file() or dest.stat().st_size != path.stat().st_size:
            log(f"  Retaining archive ({path.stat().st_size / 1024**2:.0f} MB)")
            shutil.copy2(path, dest)
        retained = os.path.relpath(dest, export_dir.parent).replace('\\', '/')

    stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    caps = capabilities_for(primary_dir, has_plugin=bool(chosen))
    # `counts` above tallies only the LOOSE files this pass placed; anything
    # that came out of a contained BSA was written by extract_bsa. Recount the
    # finished tree so the reported total is what actually landed.
    counts = _count_tree(primary_dir)
    for i, name in enumerate(names):
        rel = chosen[i] if chosen else ''
        entry = {
            'kind': 'folder' if man.is_folder else 'archive',
            'archive_original': str(path),
            'archive_retained': retained,
            'archive_size': key.get('size', 0),
            'archive_sha1': key.get('sha1', ''),
            'plugin': name if chosen else '',
            'plugin_member': rel,
            'payload_root': man.payload_root,
            'plugin_path': (os.path.relpath(
                primary_dir / source_registry.SOURCE_SUBDIR / name,
                export_dir.parent).replace('\\', '/') if chosen else ''),
            'group_id': group_id,
            'group_label': man.label,
            # EVERY plugin the archive holds -- not just the ones this run
            # imported. Recording the narrowed selection made a --plugin-member
            # import disagree with the entries a full import had written.
            'group_plugins': ([Path(q).name for q in man.plugins]
                              if chosen else []),
            'group_dir': group_name,
            # The counts describe the SHARED asset tree, so every member
            # carries them. Keying on `name == primary` meant a
            # --plugin-member import stamped them on whichever single plugin
            # it happened to import and left the siblings reading zero.
            'counts': counts,
            'capabilities': caps,
            'ingested_utc': stamp,
        }
        source_registry.put(export_dir, name, entry)
        _write_manifest(primary_dir, {'key': key, 'group_id': group_id})
        results[name] = {'cached': False, 'counts': counts,
                         'capabilities': caps}

    total = sum(counts.values())
    if chosen:
        log(f"  Imported {len(names)} plugin(s): " + ', '.join(names))
    else:
        log(f"  Imported asset-only mod {primary!r} ({total} files)")
    return results


# ---------------------------------------------------------------------------
#  Capabilities — which pipeline steps this mod can actually run
# ---------------------------------------------------------------------------

# step key -> what has to be present for it to do anything.
# Keys match gui.STEPS / convert.py's do_* phases.
#
# 'extract' is absent deliberately: it pulls assets out of the BSAs sitting in
# a game Data folder, and an imported mod HAS NO such folder -- ingest already
# unpacked its BSAs into export/<name>/. Re-running it only prints "already
# imported, skipping", so it is offered for directory sources only (see
# `available_steps`).
STEP_REQUIREMENTS = {
    'export':     ('plugin',),
    'import_':    ('plugin',),
    'scripts':    ('plugin',),
    'meshes':     ('meshes', 'textures'),
    'speedtrees': ('trees',),
    'creatures':  ('plugin',),
    'sounds':     ('sound',),
    # The Nemesis patch describes generated creature projects, which only a
    # plugin can produce (creatures come from CREA records).
    'nemesis':    ('plugin',),
    'pack':       (),
    'pack_zip':   (),
}


def _count_tree(plugin_dir) -> dict:
    """{category: file count} for a finished export/<name>/ tree."""
    plugin_dir = Path(plugin_dir)
    out = {}
    for cat in bsa_extract.ASSET_CATEGORIES + ('misc',):
        d = plugin_dir / cat
        if d.is_dir():
            n = sum(1 for p in d.rglob('*') if p.is_file())
            if n:
                out[cat] = n
    return out


def capabilities_for(plugin_dir, has_plugin=True) -> dict:
    """What `export/<name>/` actually contains, as {'plugin','meshes',...}.

    Drives which pipeline steps are offered: an asset-only texture pack has no
    plugin to export and no sounds to convert, so those steps would do nothing
    and are greyed out rather than silently succeeding on an empty input.
    """
    plugin_dir = Path(plugin_dir)
    caps = {'plugin': bool(has_plugin)}
    for cat in bsa_extract.ASSET_CATEGORIES + ('misc',):
        d = plugin_dir / cat
        caps[cat] = bool(d.is_dir() and any(d.rglob('*')))
    return caps


def available_steps(capabilities) -> set:
    """Step keys runnable for an IMPORTED MOD with these capabilities.

    Note `extract` is never included: ingest already unpacked this mod's BSAs,
    so the step would only re-check the cache. Directory sources are not gated
    at all -- their BSAs really do need extracting.
    """
    caps = capabilities or {}
    out = set()
    for step, needs in STEP_REQUIREMENTS.items():
        if not needs or any(caps.get(n) for n in needs):
            out.add(step)
    return out


def reingest(plugin, export_dir, log=print, force=False):
    """Re-run the import for an already-registered plugin.

    This is what `--extract-only` calls for an imported mod: it replaces BSA
    extraction, using the retained archive so it works long after the original
    download is gone.
    """
    export_dir = Path(export_dir)
    entry = source_registry.get(export_dir, plugin)
    if not entry:
        raise IngestError(f'{plugin} is not an imported mod')

    src = source_registry.retained_archive(export_dir, plugin)
    if src is None:
        original = entry.get('archive_original') or '(unrecorded)'
        raise IngestError(
            f"{plugin} was imported from {original}, but no copy remains. "
            f"Re-import the mod (Mods > Import Mod Archive...) to restore it.")

    man = inspect(src)
    members = entry.get('group_plugins') or (
        [entry['plugin']] if entry.get('plugin') else [])

    if not members:
        # Asset-only mod: nothing to select, re-run the whole payload.
        return ingest(src, export_dir, keep_archive=True, force=force,
                      log=log, manifest=man)

    # Map recorded plugin names back to this archive's member paths.
    want = {m.lower() for m in members}
    chosen = [p for p in man.plugins if Path(p).name.lower() in want]
    if not chosen:
        raise IngestError(
            f'{Path(src).name} no longer contains {", ".join(members)}')
    return ingest(src, export_dir, plugin_members=chosen,
                  keep_archive=True, force=force, log=log, manifest=man)


def remove(plugin, export_dir, log=print):
    """Delete an imported plugin's export tree and registry entry.

    The asset tree is SHARED by every plugin from the same archive, so it is
    deleted only when this is the last one still registered. Removing one
    plugin of three must not take the other two's meshes with it.
    """
    export_dir = Path(export_dir)
    entry = source_registry.get(export_dir, plugin)
    if not entry:
        return False
    name = entry.get('plugin') or plugin
    group_dir = source_registry.asset_root(export_dir, name)
    siblings = [n for n in source_registry.group_members(export_dir, name)
                if n.lower() != name.lower()]

    # This plugin's own records go regardless.
    rec = source_registry.record_dir(export_dir, name)
    if rec.is_dir() and rec != group_dir:
        shutil.rmtree(rec, ignore_errors=True)
        log(f"  Removed {rec}")

    if siblings:
        # Its binary, but not the payload the others still read.
        binary = group_dir / source_registry.SOURCE_SUBDIR / name
        if binary.is_file():
            binary.unlink()
        log(f"  Kept shared assets in {group_dir} "
            f"({len(siblings)} plugin(s) still use them)")
    elif group_dir.is_dir():
        shutil.rmtree(group_dir, ignore_errors=True)
        log(f"  Removed {group_dir}")

    source_registry.remove(export_dir, name)
    return True


def _slug(text):
    out = ''.join(c.lower() if c.isalnum() else '-' for c in str(text))
    while '--' in out:
        out = out.replace('--', '-')
    return out.strip('-') or 'mod'
