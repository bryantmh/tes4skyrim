"""Where finished, installable artefacts land inside the output directory.

`output/` is a WORKSPACE, not a delivery folder. It holds one working folder per
converted plugin (`output/Oblivion.esm/`), the baked LOD mod
(`output/AutoConvertLOD/`), export caches and manifests — all of it intermediate,
none of it what the user installs. The things they DO install were mixed in at
the same level, so finding the four files that actually ship meant knowing which
of a dozen entries were products and which were scaffolding.

Everything installable is collected here instead: every mod zip
(`<plugin>.zip`, `TESGameSelect.zip`, `AutoConvertLOD.zip`, and
`Body Slots Patch.zip`, the Skyrim load-order patch with its split skin meshes).

Deliberately its own tiny module: independent producers write here —
convert.py's zip and body-patch phases, the tools/release packagers,
tools/misc/convert_ui.py and the GUI's journal patch — all through
`write_mod_zip`, and they must not import the whole pipeline to learn one
folder name.

The name has a SPACE in it and is user-facing, so it is spelled exactly once,
here. Note for scanners: nothing in here is a converted plugin. `output/` is
scanned for plugin folders by `sibling_lod.converted_plugins` and
`gui.scan_converted`; both now accept EITHER `<folder>/<folder>` or a
`<plugin>.manifest.json` inside the folder, because an imported mod's folder is
named for the MOD rather than for any one plugin. `Finished Mods/` holds zips
and a loose .esp but no manifest, so it still satisfies neither test and is
never mistaken for a converted plugin.
"""

import zipfile
from pathlib import Path

FINISHED_DIR_NAME = "Finished Mods"

#: The Skyrim body-slot patch: its plugin, and the zip shipping it with the split skin meshes.
BODY_SLOTS_PATCH = "Body Slots Patch"

# Marks the export ROOT. Used to tell `export/<mod>/<plugin>/`
# (records nested inside a mod) from a plain `export/<plugin>/`.
REGISTRY_FILENAME = "sources.json"


def finished_dir(out_root) -> Path:
    """`out_root`'s finished-mods folder, created if it does not exist.

    Created on demand rather than up front: a run that packages nothing should
    not leave an empty folder promising deliverables it never made.
    """
    d = Path(out_root) / FINISHED_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
#  Writing a mod zip
# ---------------------------------------------------------------------------

def tree_members(root) -> list:
    """[(archive name, file)] for every file under `root`, relative to it."""
    root = Path(root)
    return [(str(p.relative_to(root)), p)
            for p in sorted(root.rglob("*")) if p.is_file()]


def write_mod_zip(zip_path, members, on_file=None) -> int:
    """Zip `members` -- (archive name, file path or bytes) -- to `zip_path`.

    Written under a temporary name and swapped in, so an interrupted run never
    leaves a truncated archive where the GUI looks for a finished one.
    `on_file(count, archive name)` runs after each member. Returns the count.
    """
    zip_path = Path(zip_path)
    tmp = zip_path.with_name(zip_path.name + ".part")
    count = 0
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for arcname, source in members:
                if isinstance(source, bytes):
                    zf.writestr(arcname, source)
                else:
                    zf.write(source, arcname=arcname)
                count += 1
                if on_file is not None:
                    on_file(count, arcname)
        tmp.replace(zip_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return count


# --- Shared-folder resolution ----------------------------------------------
# Plugins imported together from one mod archive share ONE asset payload but

def _registry():
    """`source_registry`, or None when it cannot be imported at all."""
    try:
        from asset_convert.sources import source_registry
    except ImportError:
        return None
    return source_registry


def asset_root(export_dir, plugin: str) -> Path:
    """`export/<group-or-plugin>/` — the SHARED meshes/textures/sound/trees."""
    reg = _registry()
    if reg is None or not export_dir:
        return Path(export_dir or '') / plugin
    return reg.asset_root(export_dir, plugin)


def record_dir(export_dir, plugin: str) -> Path:
    """`export/<group>/<plugin>/` — THIS plugin's records and own caches."""
    reg = _registry()
    if reg is None or not export_dir:
        return Path(export_dir or '') / plugin
    return reg.record_dir(export_dir, plugin)


def plugin_out_root(out_root, plugin: str, export_dir=None) -> Path:
    """The folder in `out_root` holding `plugin`'s converted artefacts.

    Mirrors the export side: plugins imported together from one mod archive
    share a single folder named for the mod, so the three converted ESMs of a
    resource pack sit side by side rather than in three trees that each hold a
    private copy of the same meshes.

    `export_dir` is where the source registry lives; without it (or for a
    plugin that is not an imported mod) this is `out_root/<plugin>` exactly as
    it has always been.
    """
    reg = _registry() if export_dir else None
    name = reg.asset_root_name(export_dir, plugin) if reg else plugin
    return Path(out_root) / name


def plugin_esm(out_root, plugin: str, export_dir=None) -> Path:
    """The converted plugin file itself: `output/<group-or-plugin>/<plugin>`.

    `out_root / plugin / plugin` was the idiom in a dozen places. It is wrong
    for an imported mod, whose plugins share one folder named for the MOD, and
    every copy of it had to be found and fixed by hand. Call this instead.
    """
    return plugin_out_root(out_root, plugin, export_dir) / plugin


def master_record_dir(export_dir, master: str) -> Path:
    """Where MASTER `master`'s exported records live.

    Identical to `record_dir`, named for the calling context: master lookups
    are where joining a name onto `export/` silently returns a path that does
    not exist, the importer then diffs overrides against nothing, and the
    plugin converts wrong with only a warning. Always resolve a master here.
    """
    return record_dir(export_dir, master)


# ---------------------------------------------------------------------------
#  One handle per plugin
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_EXPORT = REPO_ROOT / "export"
DEFAULT_OUTPUT = REPO_ROOT / "output"


class PluginPaths:
    """Every path belonging to one plugin. Read attributes, never build paths.

    Cheap to construct: each attribute resolves on access and the registry
    behind it is cached, so holding one of these is not holding a snapshot.
    """

    __slots__ = ("plugin", "export_root", "out_root")

    def __init__(self, plugin: str, export_root=None, out_root=None):
        self.plugin = plugin
        self.export_root = Path(export_root
                                if export_root is not None else DEFAULT_EXPORT)
        self.out_root = Path(out_root
                             if out_root is not None else DEFAULT_OUTPUT)

    @property
    def records(self) -> Path:
        """`export/<group>/<plugin>/` — this plugin's .txt dump and caches."""
        return record_dir(self.export_root, self.plugin)

    @property
    def assets(self) -> Path:
        """`export/<group>/` — meshes/textures/sound/trees, shared by the mod."""
        return asset_root(self.export_root, self.plugin)

    @property
    def source(self) -> Path:
        """`export/<group>/_source/` — plugin binaries and retained archive."""
        return self.assets / "_source"

    @property
    def out(self) -> Path:
        """`output/<group>/` — where converted artefacts land."""
        return plugin_out_root(self.out_root, self.plugin, self.export_root)

    @property
    def esm(self) -> Path:
        """`output/<group>/<plugin>` — the converted plugin file itself."""
        return self.out / self.plugin

    def master(self, master: str) -> "PluginPaths":
        """The same handle for one of this plugin's MASTERS.

        Masters are where the plain join hurt most: a master that IS converted
        resolved to a path that does not exist, the importer diffed every
        override against nothing, and the only symptom was a warning.
        """
        return PluginPaths(master, self.export_root, self.out_root)

    def __repr__(self):
        return f"PluginPaths({self.plugin!r})"


def paths(plugin: str, export_root=None, out_root=None) -> PluginPaths:
    """The path handle for `plugin`. The one entry point worth memorising."""
    return PluginPaths(plugin, export_root, out_root)


def asset_cache_chain(export_subdir, filename: str) -> tuple:
    """Every copy of asset cache `filename` this plugin needs, MASTERS FIRST.

    A child plugin caches only the assets it ships, so a master-owned mesh
    resolves to nothing: its collision, bounds and door panels all go
    missing at once.  Reading the masters' copies first and the plugin's
    own last lets an override win a shared key.  A masterless plugin yields
    a one-element chain.

    See: docs/commentary/tes5_import_navmesh.md#master-owned-cells
    """
    from tes5_import.overrides.nested import (export_master_names,
                                              export_root,
                                              master_export_dir)
    root = export_root(str(export_subdir))
    chain = []
    for name in export_master_names(str(export_subdir)):
        path = str(assets_for(master_export_dir(root, name)) / filename)
        if path not in chain:
            chain.append(path)
    own = str(assets_for(export_subdir) / filename)
    if own not in chain:
        chain.append(own)
    return tuple(chain)


def assets_for(export_subdir) -> Path:
    """The SHARED asset tree that `export_subdir`'s records belong to.

    Most of the pipeline is handed a plugin's RECORD directory (the folder of
    .txt dumps) and then reaches sideways for assets: `export_dir / 'meshes'`,
    `export_dir / 'collision_cache.bin'`, `export_dir / 'sound' / 'voice'`.
    That worked only while records and assets shared one folder. For an
    imported mod the records are one level deeper than the assets, so those
    joins point at a folder that does not exist and the lookup silently
    answers "nothing" -- no mesh bounds, no collision, no voice.

    Given `export/<Mod>/<plugin>/` this returns `export/<Mod>/`; given a
    plain `export/<plugin>/` it returns it unchanged. Pass a record dir and
    read assets from the result.
    """
    d = Path(export_subdir)
    parent = d.parent
    # `<root>/<mod>/<plugin>` is the only nested shape: the grandparent is the
    # export root, marked by the registry file sitting in it.
    if (parent.parent / REGISTRY_FILENAME).is_file() and not (
            d / REGISTRY_FILENAME).is_file():
        if (parent / REGISTRY_FILENAME).is_file():
            return d          # d is directly under the root: not nested
        return parent
    return d

