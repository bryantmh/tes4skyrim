"""Adding a mod (archive or folder) to a game, with no toolkit: what it holds, which parts and
plugins to take, which masters are still missing, and which game it belongs to.

Nothing is written until the user confirms. Reading an archive (and the masters of every plugin
in it) takes real seconds on a large `.7z`, so `inspect_mod` runs on a worker thread.
See: docs/commentary/asset_convert_mod_ingest.md#payload-roots
"""

import os
import tempfile
from pathlib import Path

from asset_convert.sources import archive as archive_io
from asset_convert.sources import mod_ingest, source_registry
from core.gui.config import EXPORT_DIR, scan_plugins
from core.plugin_masters import get_masters_from_binary
from output_layout import record_dir
from source_paths import resolve_plugin_path

#: Missing masters named in the warning before it says "and N more".
MAX_NAMED_MASTERS = 6

# ---------------------------------------------------------------------------
#  Reading a mod
# ---------------------------------------------------------------------------


def _staged_plugins(manifest, tmp) -> list:
    """(member path, file on disk) for every plugin the mod holds, extracting in ONE pass."""
    if manifest.is_folder:
        roots = [manifest.path / r for r in manifest.payload_root]
        return [(rel, next((b / rel for b in roots if (b / rel).is_file()), manifest.path / rel))
                for rel in manifest.plugins]
    members = [f"{root}/{rel}".lstrip('/') for rel in manifest.plugins
               for root in (manifest.payload_root or [''])]
    try:
        archive_io.extract_all(manifest.path, tmp, members=members)
    except Exception:
        return []
    out = []
    for rel in manifest.plugins:
        want = Path(rel).name.lower()
        hit = next((p for p in (Path(tmp) / m for m in members)
                    if p.is_file() and p.name.lower() == want), None)
        if hit is not None:
            out.append((rel, hit))
    return out


def masters_by_plugin(manifest) -> dict:
    """{plugin member: [masters outside the mod]}, read with one extraction of the archive."""
    if not manifest.plugins:
        return {}
    own = {Path(p).name.lower() for p in manifest.plugins}
    out = {}
    with tempfile.TemporaryDirectory(prefix="tesconv_mast_") as tmp:
        for rel, target in _staged_plugins(manifest, tmp):
            try:
                out[rel] = [m for m in get_masters_from_binary(str(target)) if m.lower() not in own]
            except Exception:
                continue
    return out


def inspect_mod(path) -> tuple:
    """(manifest, masters by plugin) for an archive or folder. Slow: call off the UI thread."""
    manifest = mod_ingest.inspect(path)
    return manifest, masters_by_plugin(manifest)


def summary(manifest) -> str:
    """What the mod holds, in one or two lines."""
    info = [] if manifest.all_subpackages else [mod_ingest.layout_description(manifest.payload_root)]
    info.append(manifest.summary())
    if manifest.bsas:
        info.append(f"{len(manifest.bsas)} BSA(s) will be extracted")
    if manifest.nested:
        info.append(f"{len(manifest.nested)} nested archive(s)")
    if manifest.ambiguous_data:
        info.append(f"Several equally shallow Data folders; using {manifest.payload_root[0]}")
    return " · ".join(i for i in info if i)


def plugins_for_subpackages(manifest, chosen) -> list:
    """The payload-relative plugins the `chosen` parts ship, deduplicated (a compilation repeats names)."""
    if not manifest.all_subpackages:
        return list(manifest.plugins)
    keep, seen = [], set()
    for root in chosen:
        prefix = (root + '/').lower()
        for mem in manifest.raw_members:
            path = str(mem.path)
            if (os.path.splitext(path)[1].lower() in mod_ingest.PLUGIN_EXTS
                    and path.lower().startswith(prefix)):
                rel = path[len(prefix):]
                if rel.lower() not in seen:
                    seen.add(rel.lower())
                    keep.append(rel)
    return sorted(keep, key=str.lower)


def missing_masters(by_plugin: dict, chosen: list) -> set:
    """Masters of the chosen plugins that have no export records yet (resolved through `record_dir`)."""
    return {m for rel in chosen for m in by_plugin.get(rel) or ()
            if not record_dir(EXPORT_DIR, m).is_dir()}


def missing_text(missing: set) -> str:
    """The warning naming masters to convert first, or ""."""
    if not missing:
        return ""
    shown = sorted(missing)
    head = shown[:MAX_NAMED_MASTERS]
    tail = f", and {len(shown) - len(head)} more" if len(shown) > len(head) else ""
    return "Convert these masters first: " + ", ".join(head) + tail


def game_for(masters, games: list) -> str:
    """The id of the game whose Data folder holds one of `masters`, or ""."""
    wanted = {m.lower() for m in masters}
    for game in games:
        if wanted & {p.lower() for p in scan_plugins(game["path"])}:
            return game["id"]
    return ""


# ---------------------------------------------------------------------------
#  The add-a-mod dialog
# ---------------------------------------------------------------------------

class ImportPick:
    """Which parts and plugins of an inspected mod to take; plugins follow the ticked parts."""

    def __init__(self, manifest, by_plugin: dict):
        """Every part and plugin starts ticked."""
        self.manifest = manifest
        self.by_plugin = by_plugin or {}
        self.subs = {name: True for name in manifest.all_subpackages}
        self.plugin_state = {}
        self.keep = True

    def plugins(self) -> list:
        """The plugin members the ticked parts ship."""
        return plugins_for_subpackages(self.manifest, [n for n, on in self.subs.items() if on])

    def plugin_rows(self) -> list:
        """(member, ticked) per plugin the ticked parts ship."""
        return [(rel, self.plugin_state.get(rel, True)) for rel in self.plugins()]

    def chosen_plugins(self) -> list:
        """The ticked plugin members."""
        return [rel for rel, on in self.plugin_rows() if on]

    def chosen_subs(self) -> list:
        """The ticked parts."""
        return [n for n, on in self.subs.items() if on]

    def masters(self) -> set:
        """Every master the ticked plugins need from outside the mod."""
        return {m for rel in self.chosen_plugins() for m in self.by_plugin.get(rel) or ()}

    def problem(self) -> str:
        """Why the choice cannot be imported, or ""."""
        if self.manifest.plugins and not self.chosen_plugins():
            return "No plugins ticked."
        if self.subs and not self.chosen_subs():
            return "No parts ticked."
        return ""

    def keep_text(self) -> str:
        """The keep-a-copy option, sized in MB; "" for a folder."""
        if self.manifest.is_folder:
            return ""
        try:
            size_mb = self.manifest.path.stat().st_size / 1024 ** 2
        except OSError:
            size_mb = 0
        return f"Keep a copy of the archive ({size_mb:.0f} MB) so steps can be re-run later"

    def ingest(self, log) -> dict:
        """Import the choice; `log` receives progress lines. Slow: call off the UI thread."""
        return mod_ingest.ingest(self.manifest.path, EXPORT_DIR, plugin_members=self.chosen_plugins() or None,
                                 keep_archive=self.keep, manifest=self.manifest, log=log,
                                 subpackages=self.chosen_subs() if self.subs else None)


# ---------------------------------------------------------------------------
#  Added mods
# ---------------------------------------------------------------------------

def added_mods() -> list:
    """Every added mod as {'id', 'label', 'plugins', 'asset_only', 'entry'}, newest first."""
    out = []
    for gid, label, plugs in source_registry.groups(EXPORT_DIR):
        entry = source_registry.get(EXPORT_DIR, plugs[0]) or {} if plugs else {}
        out.append({"id": gid, "label": label, "plugins": plugs,
                    "asset_only": bool(entry) and not entry.get("plugin"), "entry": entry})
    return out


def plugin_paths(mod: dict) -> list:
    """Where an added mod's plugin files are kept."""
    return [resolve_plugin_path(plugin, "", str(EXPORT_DIR)) for plugin in mod["plugins"]]


def plugin_masters(path) -> list:
    """The masters one plugin file names; [] when unreadable."""
    try:
        return get_masters_from_binary(str(path))
    except Exception:
        return []


def mod_masters(mod: dict) -> list:
    """The masters an added mod's plugins need from outside the mod."""
    own = {p.lower() for p in mod["plugins"]}
    return [m for path in plugin_paths(mod) for m in plugin_masters(path) if m.lower() not in own]


def parts_of(plugin: str) -> tuple:
    """(every part the mod offers, the parts installed now). Slow for an old import: call off the UI thread."""
    every = mod_ingest.known_subpackages(plugin, EXPORT_DIR)
    entry = source_registry.get(EXPORT_DIR, plugin) or {}
    return every, list(entry.get("subpackages") or every)


def reimport(plugin: str, parts, log) -> dict:
    """Import an added mod again from its kept archive with these parts. Slow: call off the UI thread."""
    return mod_ingest.reingest(plugin, EXPORT_DIR, log=log, force=True, subpackages=parts)


def remove(plugins: list, log) -> None:
    """Delete an added mod's imported files; converted output stays."""
    for name in plugins:
        try:
            mod_ingest.remove(name, EXPORT_DIR, log=log)
        except Exception as exc:
            log(f"Could not remove {name}: {exc}")
