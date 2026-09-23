"""Every place plugins come from — `export/sources.json`.

A **source** is one place plugins come from. There are two kinds and they are
deliberately the same concept:

  * `kind: "directory"` — a game Data folder (Oblivion, Nehrim, a second
    install...). Plugins are read in place; assets come from the BSAs beside
    them.
  * `kind: "archive"` / `"folder"` — a mod imported from a `.zip`/`.7z`/`.rar`
    or an extracted mod folder. The plugin binary lives at
    `export/<plugin>/_source/<plugin>` and its assets under `export/<plugin>/`,
    exactly where the BSA extractor would have put them.

This unification matters because the pipeline originally assumed ONE global
Oblivion directory (`conversion_config.json: tes4DataPath`), and anyone with
both Oblivion and Nehrim was silently rewriting that single field. Whichever
directory happened to be in the box when a conversion ran got stamped as that
plugin's origin, so `Oblivion.esm` could end up recorded against the Nehrim
folder. Listing directories here, per plugin, is what fixes that.

**A plugin in no source still resolves exactly as it always did** — `tes4Data
Path` remains the fallback, so an absent or empty registry reproduces the
original behaviour byte for byte.

Entry shape (see `docs/commentary/asset_convert_mod_ingest.md`):

    {
      "version": 1,
      "sources": {
        "ElsweyrAnequina.esp": {
          "kind": "archive",              # "archive" | "folder"
          "archive_original": "C:/.../Elsweyr Anequina.rar",
          "archive_retained": "export/ElsweyrAnequina.esp/_source/....rar",
          "archive_sha1": "...", "archive_size": 418100265,
          "plugin_member": "ElsweyrAnequina.esp",
          "payload_root": "",             # "" = archive root
          "group_id": "elsweyr-anequina-3f2a",
          "group_plugins": ["ElsweyrAnequina.esp"],
          "group_label": "Elsweyr Anequina",
          "counts": {...}, "ingested_utc": "..."
        }
      }
    }
"""
import copy
import json
import os
from pathlib import Path

REGISTRY_NAME = 'sources.json'
REGISTRY_VERSION = 2

# Subfolder of export/<plugin>/ holding the plugin binary and the retained
# archive. Deliberately not the export root: `_source` can never collide with
# an asset category (meshes/textures/sound/trees/misc), and it keeps
# parse_export_directory from ever seeing a stray .esp.
SOURCE_SUBDIR = '_source'


# ---------------------------------------------------------------------------
#  Reading, upgrading and writing the registry file
# ---------------------------------------------------------------------------

def registry_path(export_dir) -> Path:
    return Path(export_dir) / REGISTRY_NAME


# Parsed-registry cache, keyed by (path, mtime_ns, size). Resolving one
# plugin's folder costs up to three loads (asset_root -> get, group_members ->
# get + load), and every phase resolves at least once, so re-reading and
# re-parsing the JSON each time is pure waste. Keyed on the file's own stat so
# an external edit -- or this process's own save() -- is picked up on the very
# next call.
_CACHE = {}


def load(export_dir) -> dict:
    """The whole registry. Never raises: a corrupt file reads as empty.

    A broken registry must not take down a Data-directory conversion that does
    not need it, so this degrades to "no imported mods" rather than failing.

    The result is a fresh copy each call: callers mutate what they get back
    (`put` edits `data['sources']` in place), and handing out the cached dict
    would let one caller's edit leak into every later reader.
    """
    return copy.deepcopy(_load_raw(export_dir))


def _load_raw(export_dir) -> dict:
    """The cached, SHARED registry dict. Internal: callers must not mutate it."""
    path = registry_path(export_dir)
    try:
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return {'version': REGISTRY_VERSION, 'sources': {}}

    hit = _CACHE.get(key)
    if hit is None:
        try:
            with open(path, encoding='utf-8') as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return {'version': REGISTRY_VERSION, 'sources': {}}
        if not isinstance(data, dict):
            return {'version': REGISTRY_VERSION, 'sources': {}}
        sources = data.get('sources')
        if not isinstance(sources, dict):
            data['sources'] = {}
        _upgrade(data)
        # One entry is enough: every call in a run uses the same export dir,
        # and a stale key can never be read (the stat is part of the key).
        _CACHE.clear()
        _CACHE[key] = data
        hit = data
    return hit


def _upgrade(data: dict) -> None:
    """Bring an older registry's entries up to `REGISTRY_VERSION`, in memory.

    Read-time only: the file is rewritten when something next calls `save`, so
    a version bump costs nothing until the registry is edited anyway.

    v1 -> v2: `payload_root` became a LIST of BAIN sub-package prefixes, and
    the sub-package selection joined it. An entry written at v1 records the
    old string and no selection, so a re-import of a complex archive offered
    no options at all.
    See: docs/commentary/asset_convert_mod_ingest.md#payload-roots
    """
    if data.get('version', 1) >= REGISTRY_VERSION:
        return
    for entry in data.get('sources', {}).values():
        if not isinstance(entry, dict):
            continue
        root = entry.get('payload_root')
        if isinstance(root, str):
            entry['payload_root'] = [root] if root else []
        entry.setdefault('subpackages', None)
        entry.setdefault('subpackages_all', None)
    data['version'] = REGISTRY_VERSION


def _sources(export_dir) -> dict:
    """The `sources` mapping, READ-ONLY -- never mutate what this returns.

    `load()` deep-copies so callers like `put()` can edit their result safely,
    but the read paths (`get`, `group_members`, `groups`) only ever look. They
    resolve up to three times per folder lookup, and copying the whole registry
    three times per lookup was ~68% of the cost.
    """
    data = _load_raw(export_dir)
    sources = data.get('sources')
    return sources if isinstance(sources, dict) else {}


def save(export_dir, data) -> None:
    path = registry_path(export_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data.setdefault('version', REGISTRY_VERSION)
    tmp = path.with_suffix('.json.tmp')
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)
    # Drop the parse cache explicitly. The (mtime, size) key alone is NOT
    # enough: this filesystem stamps mtime at ~0.5 ms resolution (measured --
    # 60 rapid writes produced 12 distinct values), so two put() calls in a
    # loop can land in one tick and leave a same-size registry looking
    # unchanged. Every write goes through here, so this is the one place that
    # has to remember.
    _CACHE.clear()


# ---------------------------------------------------------------------------
#  Directory sources (game Data folders)
# ---------------------------------------------------------------------------

def directories(export_dir) -> list:
    """Registered Data directories as [{'path', 'label'}], in added order."""
    data = load(export_dir)
    out = []
    seen = set()
    for row in data.get('directories') or []:
        if isinstance(row, str):
            row = {'path': row}
        if not isinstance(row, dict):
            continue
        path = (row.get('path') or '').strip()
        if not path:
            continue
        key = os.path.normcase(os.path.normpath(path))
        if key in seen:
            continue
        seen.add(key)
        out.append({'path': path,
                    'label': row.get('label') or label_for_directory(path)})
    return out


def label_for_directory(path) -> str:
    """A short human name for a Data folder.

    'D:/Other Games/Nehrim At Fate's Edge/Data' -> 'Nehrim At Fate's Edge'.
    The parent of `Data` is the install name, which is what distinguishes two
    directories in a list; the literal word "Data" never does.
    """
    p = Path(str(path).rstrip('/\\'))
    if p.name.lower() == 'data' and p.parent.name:
        return p.parent.name
    return p.name or str(path)


def add_directory(export_dir, path, label=None) -> bool:
    """Register a Data directory. True if it was newly added."""
    path = str(path).strip()
    if not path:
        return False
    data = load(export_dir)
    rows = data.setdefault('directories', [])
    key = os.path.normcase(os.path.normpath(path))
    for row in rows:
        existing = row.get('path') if isinstance(row, dict) else row
        if os.path.normcase(os.path.normpath(str(existing))) == key:
            return False
    rows.append({'path': path, 'label': label or label_for_directory(path)})
    save(export_dir, data)
    return True


def remove_directory(export_dir, path) -> bool:
    """Unregister a Data directory. The folder itself is never touched."""
    data = load(export_dir)
    rows = data.get('directories') or []
    key = os.path.normcase(os.path.normpath(str(path)))
    kept = [r for r in rows
            if os.path.normcase(os.path.normpath(
                str(r.get('path') if isinstance(r, dict) else r))) != key]
    if len(kept) == len(rows):
        return False
    data['directories'] = kept
    save(export_dir, data)
    return True


def directory_for(export_dir, plugin: str):
    """The registered directory containing `plugin`, or None.

    Resolves a plugin to its OWN install rather than to whichever directory a
    path field happens to hold, which is what made Oblivion.esm get recorded
    against the Nehrim folder.
    """
    if not plugin:
        return None
    for row in directories(export_dir):
        if os.path.isfile(os.path.join(row['path'], plugin)):
            return row['path']
    return None


def migrate_known_directories(export_dir, extra_dirs=(),
                              include_history=True) -> int:
    """Seed the directory list from paths the project already knows about.

    Before sources were unified there was ONE `tes4DataPath` plus a per-plugin
    record of the folder each conversion ran from (`.conversion_state.json`
    "sources"). Someone with both Oblivion and Nehrim had been retyping that
    single field, so their second install is only discoverable through that
    history. Registering both up front means the dropdown is correct on first
    launch instead of after the user re-adds a folder by hand.

    Returns how many directories were newly registered.
    """
    candidates = [str(p) for p in extra_dirs if p]
    # `include_history` reads the project-wide conversion state, which lives
    # outside export_dir -- so it is opt-out, or a caller working against a
    # throwaway export dir (a test) would silently pick up the real machine's
    # folders.
    if include_history:
        try:
            import version as version_info
            state = version_info._load_state()
            recorded = state.get('sources')
            if isinstance(recorded, dict):
                candidates += [v for v in recorded.values()
                               if isinstance(v, str)]
        except Exception:
            pass

    added = 0
    for path in candidates:
        path = path.strip()
        # Only register folders that still exist AND hold plugins: a stale
        # entry from a moved install would otherwise sit in the list forever.
        if not path or not os.path.isdir(path):
            continue
        try:
            has_plugin = any(n.lower().endswith(('.esm', '.esp'))
                             for n in os.listdir(path))
        except OSError:
            continue
        if has_plugin and add_directory(export_dir, path):
            added += 1
    return added


def all_sources(export_dir, extra_dirs=()) -> list:
    """Every source, as [{'id', 'kind', 'label', 'path'|'plugins'}].

    Directories first (in registration order), then imported mods newest
    first. `extra_dirs` are paths to include even if unregistered -- the
    configured tes4DataPath, so a fresh install with an empty registry still
    shows its game folder.
    """
    out = []
    seen = set()
    for path in list(extra_dirs) + [r['path'] for r in directories(export_dir)]:
        path = str(path or '').strip()
        if not path:
            continue
        key = os.path.normcase(os.path.normpath(path))
        if key in seen:
            continue
        seen.add(key)
        out.append({'id': f'dir:{key}', 'kind': 'directory',
                    'label': label_for_directory(path), 'path': path})
    for gid, label, plugs in groups(export_dir):
        # An asset-only mod registers under its own name with no plugin, so
        # "1 plugin" would misdescribe it.
        entry = get(export_dir, plugs[0]) if plugs else None
        asset_only = bool(entry and not entry.get('plugin'))
        out.append({'id': f'mod:{gid}', 'kind': 'mod', 'label': label,
                    'plugins': plugs, 'asset_only': asset_only})
    return out


def _key(plugin: str) -> str:
    """Case-insensitive key: the GUI's combo and the CLI's -f differ in case
    for the same file often enough that keying on the raw string would split
    one plugin's history in two (same rule as version.py's _plugin_key)."""
    return (plugin or '').strip().lower()


def get(export_dir, plugin: str):
    """The registry entry for `plugin`, or None if it is not an imported mod."""
    if not plugin:
        return None
    sources = _sources(export_dir)
    want = _key(plugin)
    for name, entry in sources.items():
        if _key(name) == want and isinstance(entry, dict):
            # Carry the canonical name so callers never have to re-derive it
            # from a differently-cased -f argument.
            entry = dict(entry)
            entry.setdefault('plugin', name)
            return entry
    return None


def mod_plugins(export_dir, name: str) -> list:
    """The plugins a mod ships when `name` is that MOD's folder or label, not
    a plugin; [] for a plugin name, an asset-only mod, or an unknown name.

    See: docs/reference/pipeline.md#-f-takes-the-plugin
    """
    if get(export_dir, name):
        return []
    want = _key(name)
    return sorted((entry['plugin'] for entry in _sources(export_dir).values()
                   if isinstance(entry, dict) and entry.get('plugin')
                   and want in (_key(entry.get('group_dir')),
                                _key(entry.get('group_label')))),
                  key=str.lower)


def put(export_dir, plugin: str, entry: dict) -> None:
    """Insert or replace one plugin's entry."""
    data = load(export_dir)
    sources = data.setdefault('sources', {})
    # Drop any existing differently-cased key for the same plugin so the two
    # cannot drift apart.
    want = _key(plugin)
    for name in [n for n in sources if _key(n) == want]:
        sources.pop(name)
    sources[plugin] = entry
    save(export_dir, data)


def remove(export_dir, plugin: str) -> bool:
    """Delete `plugin`'s entry. True if something was removed."""
    data = load(export_dir)
    sources = data.get('sources', {})
    want = _key(plugin)
    hit = [n for n in sources if _key(n) == want]
    for name in hit:
        sources.pop(name)
    if hit:
        save(export_dir, data)
    return bool(hit)


def plugins(export_dir) -> list:
    """Every imported plugin name, sorted case-insensitively."""
    return sorted(load(export_dir).get('sources', {}), key=str.lower)


def groups(export_dir) -> list:
    """Imported mods as [(group_id, label, [plugin, ...])], newest first.

    One archive can contain several plugins (both TWMP archives ship two); they
    share a group_id so the GUI can offer "the mod you just imported" as a
    single source scope.
    """
    sources = _sources(export_dir)
    by_group = {}
    for name, entry in sources.items():
        if not isinstance(entry, dict):
            continue
        gid = entry.get('group_id') or name
        slot = by_group.setdefault(
            gid, {'label': entry.get('group_label') or name,
                  'plugins': [], 'when': entry.get('ingested_utc') or ''})
        slot['plugins'].append(name)
        # Newest timestamp in the group represents the group.
        when = entry.get('ingested_utc') or ''
        if when > slot['when']:
            slot['when'] = when

    out = [(gid, slot['label'], sorted(slot['plugins'], key=str.lower),
            slot['when'])
           for gid, slot in by_group.items()]
    out.sort(key=lambda row: (row[3], row[1].lower()), reverse=True)
    return [(gid, label, plugs) for gid, label, plugs, _ in out]


# ---------------------------------------------------------------------------
#  Asset folder resolution — the GROUP tree
# ---------------------------------------------------------------------------

def _sanitize_folder(name: str) -> str:
    """A mod label reduced to something safe to use as a folder name.

    Labels come from archive filenames, so they can carry characters Windows
    forbids in a path. Collapsing them here keeps the folder name derivable
    from the label alone -- no second field to keep in sync.
    """
    cleaned = ''.join('_' if c in r'<>:"/\|?*' else c for c in (name or ''))
    cleaned = ' '.join(cleaned.split()).strip(' .')
    return cleaned


def asset_root_name(export_dir, plugin: str) -> str:
    """The folder name holding `plugin`'s ASSETS.

    The mod's group folder for an imported mod, else the plugin's own name.
    Returned as a bare name rather than a path so both `export/` and `output/`
    can build their own root from it -- the two must agree, and they only do
    that reliably if the name is computed once.
    """
    entry = get(export_dir, plugin)
    if not entry:
        return plugin
    label = _sanitize_folder(entry.get('group_label') or '')
    return label or plugin


def asset_root(export_dir, plugin: str) -> Path:
    """`export/<group-or-plugin>/` — where `plugin`'s shared assets live."""
    return Path(export_dir) / asset_root_name(export_dir, plugin)


def record_dir(export_dir, plugin: str) -> Path:
    """Where `plugin`'s own record dump (STAT.txt, _HEADER.txt...) lives.

    Nested inside the group folder for an imported mod so that three plugins
    sharing one asset tree still keep three distinct record sets.

    The test is "does this mod SHIP more than one plugin", NOT "is the folder
    named after this plugin". An archive called `MyMod.esp.zip` that holds two
    plugins yields the label `MyMod.esp`, and keying on the name put that one
    plugin's records loose in the group root -- mixed in with meshes/ and
    _source/ -- while its sibling nested correctly.

    🛑 It reads the ARCHIVE's plugin list (`group_plugins`), never how many
    members are currently REGISTERED. This answer has to be stable for the
    life of the folder: keying it on the live registry meant importing a
    second plugin from the same archive silently MOVED the first one's record
    directory, stranding an .txt dump that was already exported and leaving
    every later stage to report "No export directory" and skip.
    """
    root = asset_root(export_dir, plugin)
    return root / plugin if _ships_multiple_plugins(export_dir, plugin) else root


def _ships_multiple_plugins(export_dir, plugin: str) -> bool:
    """True when `plugin`'s source archive holds more than one plugin.

    Authored data, fixed when the archive was inspected -- unlike the set of
    registered members, which grows as the user imports more of them.
    """
    entry = get(export_dir, plugin)
    if not entry:
        return False
    shipped = entry.get('group_plugins')
    if isinstance(shipped, list) and shipped:
        return len({str(n).lower() for n in shipped}) > 1
    # Pre-`group_plugins` entry: fall back to the live membership, which is
    # what this used to do and is right for every entry written since.
    return len(group_members(export_dir, plugin)) > 1


def group_members(export_dir, plugin: str) -> list:
    """Every plugin sharing `plugin`'s asset tree, itself included.

    Derived from the live registry rather than the stored `group_plugins`
    field: a partial import writes a narrowed list into the entries it
    creates, so the field disagrees with reality exactly when it matters.
    """
    entry = get(export_dir, plugin)
    if not entry:
        return [plugin]
    gid = entry.get('group_id')
    if not gid:
        return [entry.get('plugin') or plugin]
    sources = _sources(export_dir)
    out = [name for name, row in sources.items()
           if isinstance(row, dict) and row.get('group_id') == gid
           and row.get('plugin')]
    return sorted(out, key=str.lower) or [plugin]


def source_dir(export_dir, plugin: str) -> Path:
    """`export/<group-or-plugin>/_source/` — binaries and retained archive.

    One `_source/` per MOD, not per plugin: every plugin in an archive comes
    out of the same download, so retaining that archive once per plugin stored
    the same ~1 GB several times over.
    """
    return asset_root(export_dir, plugin) / SOURCE_SUBDIR


def plugin_binary(export_dir, plugin: str):
    """Absolute path to an imported plugin's TES4 binary, or None.

    Returns None when the plugin is not registered OR when its recorded binary
    has gone missing, so callers fall through to the Data directory rather than
    handing a non-existent path to the exporter.
    """
    entry = get(export_dir, plugin)
    if not entry:
        return None
    name = entry.get('plugin') or plugin
    recorded = entry.get('plugin_path')
    if recorded:
        # Stored relative to the repo root for portability; accept absolute too.
        cand = Path(recorded)
        if not cand.is_absolute():
            cand = Path(export_dir).parent / recorded
        if cand.is_file():
            return cand
    cand = source_dir(export_dir, name) / name
    return cand if cand.is_file() else None


def retained_archive(export_dir, plugin: str):
    """The archive copy kept for re-import, or None if not retained/missing.

    One archive can register several plugins, but only ONE copy of it is kept
    (under the primary plugin's _source/). Every plugin in the group records
    the same path, so a secondary plugin resolves it just as well as the
    primary -- otherwise re-running a secondary would report the archive gone
    while it sits on disk beside its sibling.
    """
    entry = get(export_dir, plugin)
    if not entry:
        return None
    for field in ('archive_retained', 'archive_original'):
        val = entry.get(field)
        if not val:
            continue
        cand = Path(val)
        if not cand.is_absolute():
            cand = Path(export_dir).parent / val
        if cand.is_file():
            return cand
    return None
