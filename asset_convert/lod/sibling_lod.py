"""Merged LOD for the tiles two or more SIBLING plugins both change.

A sibling group is every converted plugin that shares a master and edits the
same worldspace: Tamriel.esp, ElsweyrAnequina.esp, Morrowind_ob.esm and
DLCBattlehornCastle.esp all extend Oblivion.esm's TES4Tamriel.

Each of those bakes its LOD independently, as "master + itself" — which is
correct in isolation and wrong the moment two of them are installed together.
LOD tiles are FILES on a fixed grid (meshes/terrain/<wrld>/Objects/<wrld>.4.-32.-32.bto),
so two plugins that touch the same tile ship the same path and the loser is
whichever the mod manager overwrites. That plugin's terrain edits and distant
objects vanish from the tile, leaving the other plugin's version of the world.

The overlap is far wider than the authored edits suggest, because a tile covers
a BLOCK of cells and any single changed cell dirties the whole tile. Measured on
the current output: Tamriel.esp and Morrowind_ob.esm collide on 530 cells but
6,144 level-16 tiles.

The fix is the one the generators already support: bake the contested tiles ONCE
from the master with EVERY sibling stacked as an overlay, in load order. The
existing `overlay_paths` merge is by FormID, so a REFR one sibling moved and
another left alone resolves exactly as the engine would resolve it. The result
goes to its own mod folder so it can be installed last and win the overwrite
deliberately, instead of the outcome depending on install order.

Only CONTESTED tiles are baked. A tile just one sibling touches is already
correct in that sibling's own output and re-shipping it here would only add
another copy to keep in sync.
"""

from __future__ import annotations

import os
import shutil
import struct
from pathlib import Path

from asset_convert.lod.terrain_lod import (shipped_lod_worldspaces, master_names)
from tes5_import.base.tes5_reader import FLAG_PERSISTENT, records, walk


# Shared-folder resolution -- see output_layout. An imported mod's plugins keep
# their records in `export/<Mod>/<plugin>/` and write into `output/<Mod>/`, so a
# name must never be joined onto a root by hand.

def record_dir(export_root, plugin: str) -> Path:
    try:
        from output_layout import record_dir
        return record_dir(export_root, plugin)
    except ImportError:
        return Path(export_root) / plugin


def _out_root(out_root, plugin: str, export_root=None) -> Path:
    try:
        from output_layout import plugin_out_root
        return plugin_out_root(out_root, plugin,
                               str(export_root) if export_root else None)
    except ImportError:
        return Path(out_root) / plugin


#: Mod ALL generated LOD ships in. See: docs/commentary/asset_convert_terrain.md#one-lod-folder-not-one-per-plugin
LOD_DIR_NAME = "AutoConvertLOD"

#: Superseded merged-tile folder, recognised only to clean up an old install.
MERGED_DIR_NAME = "ZZZ Merged Sibling LOD"


def _lod_mod_deliverables(lod_dir: Path) -> list:
    """Mesh subtrees the LOD mod OWNS: the baked tiles and the cloud banks.

    Everything else under `meshes/` is scratch, EXCEPT a file carrying a
    `.nif.generated` marker. One list, so a new generator writing here has one
    place to register itself instead of the sweep eating its output.
    See: docs/commentary/asset_convert_terrain.md#generated-far-nif-belong-to-the-lod-mod
    """
    from asset_convert.lod.worldmap_clouds import out_dir as _cloud_dir

    return [
        lod_dir / 'meshes' / 'terrain',
        lod_dir / 'meshes' / _cloud_dir().replace(chr(92), '/'),
    ]


def _has_generated(d: Path) -> bool:
    """True if anything under `d` was DERIVED here rather than staged in."""
    for _r, _dirs, files in os.walk(d):
        if any(f.endswith('.nif.generated') for f in files):
            return True
    return False


def _count_files(d: Path) -> int:
    """Files under `d`, counted for the removal tally."""
    return sum(len(f) for _r, _dirs, f in os.walk(d))


def _derived_here(f: Path) -> bool:
    """True for a generated mesh or its marker, which the sweep must keep."""
    return (f.name.endswith('.nif.generated')
            or f.with_suffix('.nif.generated').exists())


def drop_staged_meshes(lod_dir: Path) -> int:
    """Delete the meshes a previous bake staged into the LOD mod.

    Staged models are scratch: LODGen resolves geometry under a single
    PathData root, so `lod_gen._import_master_mesh` copies each AUTHORED _far
    in for the duration of the bake and `_drop_staged_master_meshes` removes it
    afterwards. Staging copies the .nif alone and never a marker, so a staged
    mesh has no provenance to lose.

    A GENERATED _far.nif is different and must SURVIVE: it is derived straight
    into this tree and carries lod_far_gen's `.nif.generated` marker, which is
    what tells the two apart.
    See: docs/commentary/asset_convert_terrain.md#generated-far-nif-belong-to-the-lod-mod

    What the mod genuinely owns is listed by `_lod_mod_deliverables` and is
    never touched -- the tiles, and the world-map cloud banks that
    `merge_cloud_bank` writes here precisely BECAUSE the per-plugin copies
    conflict. Sweeping by "everything that is not terrain/" deleted those
    banks, which was survivable only because create_lod happens to rewrite
    them later in the same run; a sweep-only invocation would have left every
    worldspace's MODL pointing at a missing mesh.

    Scratch is removed as whole SUBTREES where a top-level directory holds no
    deliverable (one rmtree, not thousands of unlinks), and file-by-file only
    inside a directory that also holds one. Returns the number of files
    removed.

    The sweep is needed because the in-process set cannot survive a run.
    `_import_master_mesh` early-returns for a mesh that is already present and
    so never registers it, which means anything a killed or pre-one-bake run
    left behind is invisible to the post-bake cleanup and pins itself forever
    -- shadowing, since the LOD mod installs last to win the tile overwrite,
    every plugin's current copy of that mesh.
    """
    meshes = lod_dir / 'meshes'
    if not meshes.is_dir():
        return 0

    keep = [Path(os.path.normcase(str(d))) for d in _lod_mod_deliverables(lod_dir)]

    def _protected(d: Path) -> bool:
        """True if `d` is, or contains, a deliverable."""
        nd = Path(os.path.normcase(str(d)))
        return any(k == nd or nd in k.parents or k in nd.parents for k in keep)

    def _is_deliverable_dir(d: Path) -> bool:
        """True only for a deliverable root or something inside one."""
        nd = Path(os.path.normcase(str(d)))
        return any(k == nd or k in nd.parents for k in keep)

    n = 0
    for child in sorted(meshes.iterdir()):
        if not child.is_dir():
            continue
        if not _protected(child) and not _has_generated(child):
            n += _count_files(child)
            shutil.rmtree(child, ignore_errors=True)
            continue
        # Mixed: this subtree holds a deliverable somewhere beneath it, so
        # recurse and drop only the branches that hold none.
        stack = [child]
        while stack:
            cur = stack.pop()
            for sub in sorted(cur.iterdir()):
                if sub.is_dir():
                    if _protected(sub) or _has_generated(sub):
                        stack.append(sub)
                    else:
                        n += _count_files(sub)
                        shutil.rmtree(sub, ignore_errors=True)
                elif not _is_deliverable_dir(cur) and not _derived_here(sub):
                    # A loose file inside a directory that merely CONTAINS a
                    # deliverable is still scratch; only files sitting in the
                    # deliverable directory itself are kept.
                    sub.unlink()
                    n += 1
    return n


def touched_worldspace_fids(plugin_esm: Path) -> set:
    """Every worldspace FormID `plugin_esm` actually has WRLD/CELL/LAND/REFR under.

    Answers the question the master chain cannot: a plugin DEPENDING on the
    worldspace's owner is not the same as it EDITING that worldspace. Attaching
    an overlay that touches nothing costs a full parse of the file per
    worldspace and contributes zero records.

    Measured on the current 12-plugin selection: all 9 Oblivion.esm dependents
    were stacked onto all 18 of its worldspaces (162 overlay parses, 114 s), yet
    most touch exactly ONE worldspace. Scoping on this leaves 7 useful parses.

    Records are attributed by their enclosing type-1 GRUP label, which is the
    DEFINING file's WRLD FormID, so an override plugin that edits a master's
    worldspace without shipping a WRLD record of its own is still detected.
    A plugin's own WRLD records count too, so a file that defines a worldspace
    but has yet to place anything in it is not mistaken for uninvolved.

    Judged from THIS FILE'S OWN records only — a plugin's scope is what it
    itself places, never what another file's numbering implies. The returned
    ids are NORMALIZED (`lod_gen._formid_remap_table`) so they can be compared
    against a worldspace id resolved from a different file.

    Both halves matter. A raw FormID's index byte offsets into its own file's
    master list, so Morrowind_ob.esm's 02xxxxxx and Tamriel.esp's 02xxxxxx are
    both "self" and name unrelated records; the two collide on 4 CELL ids, and
    resolving one plugin's cells against the other's table reads that
    coincidence as 183 overrides, dragging Morrowind INTERIOR objects into
    Cyrodiil's distant terrain.

    One linear header walk; record bodies are skipped, never parsed.
    """
    from asset_convert.lod.esm_scan import formid_remap_table
    plugin_esm = Path(plugin_esm)
    gmap = formid_remap_table(plugin_esm)
    found: set = set()

    def g(fid: int) -> int:
        return gmap[fid >> 24] | (fid & 0x00FFFFFF)

    for rec, stack in walk(plugin_esm.read_bytes(), bodies=()):
        if rec.sig == b'WRLD':
            found.add(g(rec.form_id))
        elif rec.sig in (b'CELL', b'LAND', b'REFR') and stack.worldspace:
            found.add(g(stack.worldspace))
    return found


def master_chain(name: str, export_root: Path, known: list[str]) -> set[str]:
    """Every plugin `name` depends on, directly or transitively.

    Transitive because the dependency that matters can be indirect:
    Translation.esp lists only Nehrim.esm, and whether it can touch a
    worldspace Nehrim owns is decided by walking through Nehrim.
    """
    seen: set[str] = set()
    stack = [name]
    while stack:
        cur = stack.pop()
        for m in master_names(record_dir(export_root, cur)):
            if m in seen or m not in known:
                continue
            seen.add(m)
            stack.append(m)
    return seen


def converted_plugins(out_root: Path) -> list[str]:
    """Every plugin with a converted ESM in `out_root`, in name order.

    Two folder shapes both count. A plugin converted on its own lives in a
    folder named after it (`output/Oblivion.esm/Oblivion.esm`). Plugins
    imported together from one mod archive share a folder named after the MOD,
    and are found by their `<name>.manifest.json`.

    Either way the plugin file itself must be present — output/ also collects
    the `Finished Mods` folder, this step's own merged folder, and whatever
    else the pipeline drops at the root.
    """
    if not out_root.is_dir():
        return []
    names = []
    for p in sorted(out_root.iterdir()):
        if not p.is_dir() or p.name in (LOD_DIR_NAME, MERGED_DIR_NAME):
            continue
        if (p / p.name).is_file():
            names.append(p.name)
            continue
        # A GROUP folder is named for the mod, not for any one plugin, so the
        # `<folder>/<folder>` test above cannot see the plugins inside it.
        # Every converted plugin writes `<name>.manifest.json` beside itself,
        # which is what distinguishes a real plugin from a stray .esp copied
        # into the tree.
        for man in sorted(p.glob('*.manifest.json')):
            plugin = man.name[:-len('.manifest.json')]
            if (p / plugin).is_file():
                names.append(plugin)
    return sorted(set(names), key=str.lower)


def plugins_txt_order() -> list[str]:
    """Plugin names in the user's real Skyrim load order, or [] if unavailable.

    `%LOCALAPPDATA%/Skyrim Special Edition/plugins.txt` is what the game itself
    reads, so it is the only authoritative answer to "which of these two wins".
    Names are returned verbatim and in file order; the leading `*` (active
    flag) is stripped, and inactive entries are kept because they still record
    a position the user chose.
    """
    plugins_txt = (Path(os.environ.get("LOCALAPPDATA", ""))
                   / "Skyrim Special Edition" / "plugins.txt")
    if not plugins_txt.exists():
        return []
    order: list[str] = []
    try:
        with open(plugins_txt, "r", encoding="utf-8-sig", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                name = line.lstrip("*").strip()
                if name and name not in order:
                    order.append(name)
    except OSError:
        return []
    return order


def _master_rank(names: list[str], export_root: Path) -> dict[str, int]:
    """Master DEPTH per plugin: 0 for a plugin depending on none of `names`.

    Depth is what keeps a master from overwriting its own dependent's LOD. A
    dependent's tiles are baked as "master + dependent", so they already
    contain the master's terrain; the master's own tiles do not contain the
    dependent's. Applying the master LAST would therefore undo the dependent —
    so a master must always sort BEFORE anything that depends on it, however
    the alphabet falls.

    Cycles cannot occur in a real master list, but a malformed header could
    produce one, so the walk carries its own `seen` set and reports 0 rather
    than recursing forever.
    """
    depth: dict[str, int] = {}

    def d(name: str, seen: frozenset = frozenset()) -> int:
        if name in depth:
            return depth[name]
        if name in seen:
            return 0
        val = 1 + max([d(m, seen | {name})
                       for m in master_names(record_dir(export_root, name))
                       if m in names], default=-1)
        depth[name] = val
        return val

    for n in names:
        d(n)
    return depth


def create_lod_order(names: list[str], export_root: Path) -> list[str]:
    """The default plugin order for a Create LOD run, lowest priority first.

    Two sources, in the order the user asked for:

    1. `plugins.txt` — the real Skyrim load order. Whatever it lists comes
       FIRST, in exactly its own order, because that is the order the game
       itself resolves these plugins in and the user chose it.
    2. Everything else, appended at the bottom, sorted alphabetically but
       constrained by MASTERS: a plugin never sorts before one of its own
       masters. Alphabetical alone would let ``AAAPatch.esp`` (mastered on
       ``Tamriel.esp``) apply first and then be overwritten by the very plugin
       it patches; sorting by master depth first makes the dependent always
       land after the thing it depends on.

    This differs from `load_order`, which puts unlisted plugins FIRST so an
    unpositioned plugin can never outrank a positioned one. That rule is right
    for a merge the user never looked at. Here the list is shown, reorderable
    and confirmed before anything runs, so the user's stated preference —
    plugins.txt first, the rest appended at the bottom — is what is built.
    """
    rank = {n.lower(): i for i, n in enumerate(plugins_txt_order())}
    listed = sorted((n for n in names if n.lower() in rank),
                    key=lambda n: rank[n.lower()])
    rest = [n for n in names if n.lower() not in rank]
    depth = _master_rank(names, export_root)
    # .esm before .esp at equal depth, matching the engine's own split, then
    # name for a stable, predictable tiebreak.
    rest.sort(key=lambda n: (depth.get(n, 0),
                             not n.lower().endswith('.esm'), n.lower()))
    return listed + rest


def worldspaces_by_plugin(names: list[str], export_root: Path,
                          out_root: Path = None) -> dict[str, list[str]]:
    """{plugin: [worldspace EDID, ...]} for each of `names`.

    The same authority `worldspace_owner` routes on, so the dialog offers
    exactly the set a run would build and unticking one genuinely removes work.

    `out_root` is what makes MOD-ADDED worldspaces visible: a plugin that adds
    a landmass ships no LOD assets, so without the converted ESM to read
    terrain from it looks like it has no worldspaces at all. Optional only so
    callers that genuinely want the shipped-only set can omit it.

    Returned per plugin, not flattened, because the dialog has to recompute the
    worldspace list as plugins are ticked on and off. Scanning is the expensive
    part, so it happens ONCE when the dialog opens and every later toggle is a
    dict lookup.
    """
    out, _reasons = worldspaces_by_plugin_diagnosed(names, export_root,
                                                    out_root)
    return out


def worldspaces_by_plugin_diagnosed(names: list[str], export_root: Path,
                                    out_root: Path = None):
    """`worldspaces_by_plugin` plus why each empty plugin came back empty.

    Returns ({plugin: [edid, ...]}, {plugin: reason}). Only plugins with
    nothing to offer appear in the reason map.
    """
    from asset_convert.lod.terrain_lod import lod_capable_worldspaces

    out: dict[str, list[str]] = {}
    reasons: dict[str, str] = {}
    for name in names:
        try:
            found, why = lod_capable_worldspaces(
                record_dir(export_root, name), out_root, plugin=name)
        except Exception as exc:
            found, why = [], f"{name}: scan failed ({exc})."
        out[name] = [edid for edid, _fid in found]
        if why:
            reasons[name] = why

    _add_edited_worldspaces(names, export_root, out, reasons)
    return out, reasons


def _add_edited_worldspaces(names, export_root: Path, out: dict,
                            reasons: dict) -> None:
    """Join a plugin to any ALREADY-QUALIFIED worldspace it adds cells to.

    Widens who contributes to a worldspace someone else's shipped LOD already
    qualified; it never qualifies one. Morrowind-native content ships no
    Oblivion-format LOD and so contributed nothing to a grid it fills.
    See: docs/commentary/asset_convert_terrain.md#lod-for-plugins-that-only-edit
    """
    qualified = {edid for edids in out.values() for edid in edids}
    if not qualified:
        return
    for name in names:
        missing = qualified.difference(out.get(name, ()))
        if not missing:
            continue
        edited = _edited_worldspace_edids(record_dir(export_root, name))
        joined = [edid for edid in missing if edid in edited]
        if joined:
            out.setdefault(name, []).extend(joined)
            reasons.pop(name, None)


def _edited_worldspace_edids(export_dir: Path) -> frozenset:
    """EDIDs of every worldspace this export puts EXTERIOR cells in.

    Read from the CELL dump's ParentWRLD, not the converted ESM: this runs
    before the bake, for plugins that may not be converted yet.
    """
    from asset_convert.lod.terrain_lod import worldspace_edids
    cell_txt = Path(export_dir) / 'CELL.txt'
    if not cell_txt.is_file():
        return frozenset()
    edid_by_fid = worldspace_edids(Path(export_dir))
    found = set()
    for line in cell_txt.read_text(encoding='utf-8',
                                   errors='replace').splitlines():
        if not line.startswith('ParentWRLD='):
            continue
        try:
            fid = int(line[11:].strip(), 16)
        except ValueError:
            continue
        edid = edid_by_fid.get(fid)
        if edid:
            found.add(edid)
    return frozenset(found)


def lod_worldspaces(names: list[str], export_root: Path,
                    out_root: Path = None) -> list[str]:
    """Every worldspace the selected plugins would generate LOD for.

    Ordered by first appearance across `names` so the biggest, most-edited
    worldspaces (which is the order `shipped_lod_worldspaces` already returns
    per plugin) surface at the top.
    """
    return merge_worldspaces(names, worldspaces_by_plugin(names, export_root,
                                                          out_root))


def merge_worldspaces(names, by_plugin: dict[str, list[str]]) -> list[str]:
    """Flatten `worldspaces_by_plugin` for `names`, first appearance wins.

    Split out from the scan so the dialog can re-flatten on every tick without
    re-reading a single export directory.
    """
    seen: list[str] = []
    for name in names:
        for edid in by_plugin.get(name, ()):
            if edid not in seen:
                seen.append(edid)
    return seen


def worldspace_owner(edid: str, order: list[str], export_root: Path,
                     out_root: Path = None):
    """The plugin whose records a worldspace's LOD is baked FROM.

    The FIRST plugin in load order that can supply `edid`'s terrain. Shipped
    LOD assets alone were the old test, which silently skipped every worldspace
    a MOD adds — those ship no LOD, so the bake printed "no selected plugin
    ships LOD for it" and generated nothing for entire landmasses. Ownership
    now falls back to "defines the worldspace with terrain in its converted
    ESM", which is the property the bake actually needs.

    Ownership matters because the bake reads WRLD/CELL/LAND/REFR out of ONE
    file and applies the rest as overlays. Sourcing from a later plugin that
    merely EXTENDS the worldspace would drop everything the owner holds:
    Tamriel.esp adds a landmass around Cyrodiil, and building from it alone
    left all of Oblivion.esm's terrain missing and edge-extended into flat
    plateaus at the vanilla border.
    """
    return owner_map([edid], order, export_root, out_root).get(edid)


def _shipped_edids(export_dir: Path) -> frozenset:
    """EDIDs a plugin shipped LOD assets for."""
    try:
        return frozenset(e for e, _f in
                         (shipped_lod_worldspaces(export_dir) or []))
    except Exception:
        return frozenset()


def _defined_edids(esm: Path) -> frozenset:
    """EDIDs of every ROOT worldspace defined in a converted ESM."""
    from asset_convert.lod.terrain_lod import detect_terrain_worldspaces
    try:
        return frozenset(e for _sz, _f, e in detect_terrain_worldspaces(esm))
    except Exception:
        return frozenset()


def defined_worldspaces_by_plugin(names: list[str], export_root: Path,
                                  out_root: Path) -> dict[str, list[str]]:
    """{plugin: [EDID, ...]} for every worldspace its converted ESM defines.

    Ignores shipped LOD, so it also lists worldspaces the source never gave
    distant LOD. `owner_map`'s pass 2 resolves exactly these, so each is
    bakeable when named explicitly.
    """
    out: dict[str, list[str]] = {}
    for name in names:
        esm = _out_root(out_root, name, export_root) / name
        out[name] = (sorted(_defined_edids(esm), key=str.lower)
                     if esm.is_file() else [])
    return out


def owner_map(edids, order: list[str], export_root: Path,
              out_root: Path = None) -> dict:
    """{worldspace EDID: owning plugin}, resolved in ONE pass over the order.

    Two-pass precedence, and the order of the passes is the point: a plugin
    that SHIPPED LOD for a worldspace outranks any later plugin that merely
    defines it. That is what keeps Oblivion.esm owning TES4Tamriel rather than
    Tamriel.esp, which only extends it — sourcing from the extender would drop
    all of Oblivion's terrain. Pass 2 then covers mod-added worldspaces, which
    ship no LOD at all and used to be skipped entirely.

    Resolving the whole load order at once rather than per worldspace: the
    per-worldspace form re-listed every plugin's export dir for each of 80
    worldspaces.
    """
    wanted = set(edids)
    out: dict = {}
    for name in order:                      # pass 1: shipped LOD wins
        for e in _shipped_edids(record_dir(export_root, name)) & wanted:
            out.setdefault(e, name)
    if out_root is not None:
        for name in order:                  # pass 2: defined in the ESM
            rest = wanted - out.keys()
            if not rest:
                break
            esm = _out_root(out_root, name, export_root) / name
            if esm.is_file():
                for e in _defined_edids(esm) & rest:
                    out.setdefault(e, name)
    return out


def dependents_of(names: list[str], export_root: Path) -> dict[str, set[str]]:
    """{plugin: every plugin in `names` that depends on it, transitively}.

    Deselecting a plugin has to deselect everything built on top of it: a
    dependent's LOD is baked as "master + dependent", so with the master
    dropped there is no terrain to overlay onto and the dependent's tiles would
    come out as its own isolated edits floating in nothing.

    Transitive because the dependency that matters can be indirect —
    Translation.esp lists only Nehrim.esm, so dropping Nehrim must drop
    Translation even though nothing names the two together.
    """
    direct: dict[str, list[str]] = {
        n: [m for m in master_names(record_dir(export_root, n)) if m in names]
        for n in names}

    out: dict[str, set[str]] = {n: set() for n in names}
    for n in names:
        # Walk UP from each plugin to every master it rests on, and record the
        # plugin against each. Cheaper and cycle-safe compared with walking
        # down from every master, and a malformed header that made A master B
        # and B master A terminates on the `seen` guard instead of recursing.
        stack = list(direct[n])
        seen: set[str] = set()
        while stack:
            m = stack.pop()
            if m in seen or m == n:
                continue
            seen.add(m)
            out[m].add(n)
            stack.extend(direct.get(m, ()))
    return out


def load_order(names: list[str], export_root: Path,
                explicit: list[str] | None = None) -> list[str]:
    """The order sibling overlays are applied in — i.e. who wins a conflict.

    The LAST overlay applied replaces earlier ones for any shared FormID, so
    this order IS the conflict resolution, not a cosmetic detail.

    Three sources, in descending authority:

    1. `explicit` — an order the user arranged by hand in the GUI. Absolute:
       whatever they dragged is what runs.
    2. `plugins.txt` — the real Skyrim load order, which is what the game
       itself obeys. Anything it does not mention sorts BEFORE everything it
       does, so a plugin the user never placed can never outrank one they did.
    3. Structural fallback, used only when plugins.txt is missing or lists
       none of these plugins: master-depth, then .esm before .esp, then name.

    The structural fallback alone used to be the whole implementation, and its
    alphabetical tiebreak is an ARBITRARY winner for two siblings that edit the
    same reference — "ElsweyrAnequina before Tamriel" was alphabetical accident
    rather than anything the user chose.

    Unlisted plugins used to be appended AFTER the ranked ones, which handed
    the highest priority — the last word on every contested tile — to exactly
    the plugins the user never positioned. DLCBattlehornCastle.esp (14 changed
    cells, absent from plugins.txt) thereby outranked ElsweyrAnequina.esp
    (1,855 cells) and Tamriel.esp (99,910), and won every tile the three
    shared, so merged tiles disagreed with the load order the game itself
    obeys. Sorting them first makes an unknown plugin the LOWEST priority,
    which is also what the engine does with a plugin that is not in the list:
    it is not loaded at all.
    """
    if explicit:
        # Honour the user's arrangement; anything they never saw (a plugin
        # converted since) still has to run, but it sorts BEFORE their choices
        # for the same reason as the plugins.txt case below — a plugin the
        # user never positioned must not win a tile against one they did.
        chosen = [n for n in explicit if n in names]
        return sorted(n for n in names if n not in chosen) + chosen

    lo = [n.lower() for n in plugins_txt_order()]
    if lo:
        rank = {name: i for i, name in enumerate(lo)}
        listed = [n for n in names if n.lower() in rank]
        if listed:
            unlisted = sorted(n for n in names if n.lower() not in rank)
            return unlisted + sorted(listed, key=lambda n: rank[n.lower()])

    depth: dict[str, int] = {}

    def d(name: str, seen: frozenset = frozenset()) -> int:
        if name in depth:
            return depth[name]
        if name in seen:
            return 0
        masters = master_names(record_dir(export_root, name))
        val = 1 + max([d(m, seen | {name}) for m in masters if m in names],
                      default=-1)
        depth[name] = val
        return val

    for n in names:
        d(n)
    # .esm before .esp at equal depth, matching the engine's own split.
    return sorted(names, key=lambda n: (depth[n],
                                        not n.lower().endswith('.esm'), n))


# LOD assets live in exactly these three trees. Tile filenames are identical
# across plugins (TES4Tamriel.16.-16.0.bto and so on), which is precisely why
# they collide in the Data folder — and what makes a plain name comparison the
# honest way to report the collision.
_LOD_SUBDIRS = (
    ('meshes', 'terrain'),      # .btr terrain + Objects/*.bto
    ('textures', 'terrain'),    # composited diffuse/normal .dds
)


def _wrld_land_bounds(esm: Path, wrld_fid: int):
    """(minX, minY, maxX, maxY) of a plugin's real terrain in worldspace
    `wrld_fid`, measured from its exterior CELL grid, or None.

    Why not the WRLD record: a dependent plugin overrides the master's WRLD
    without touching MNAM/NAM0/NAM9, so every sibling reports the SAME
    rectangle and the "union" collapses to the master's.  Measured on the real
    outputs, all five TES4Tamriel contributors return X[-241664,245760]
    (487424 x 434176) while their combined land actually spans 1572864 x
    1183744 -- a deck 3.2x too small, which is precisely the overwrite bug
    merge_cloud_bank exists to prevent.

    Cells are read straight out of the built ESM: CELL records carry XCLC
    (grid X, grid Y) and sit inside the GRUP tree under their worldspace.
    Persistent cells (RecordFlags 0x400) are skipped -- they hold the
    worldspace's persistent refs, are commonly parked at a dummy (0,0), and
    would drag the extent toward the origin.
    """
    try:
        data = esm.read_bytes()
    except OSError:
        return None

    xs: list = []
    ys: list = []
    for rec, stack in walk(data):
        if (rec.sig != b'CELL' or stack.worldspace != wrld_fid
                or rec.flags & FLAG_PERSISTENT):
            continue
        xclc = rec.sub(b'XCLC')
        if xclc and len(xclc) >= 8:
            gx, gy = struct.unpack_from('<2i', xclc)
            xs.append(gx)
            ys.append(gy)

    if not xs:
        return None
    min_gx, max_gx = min(xs), max(xs)
    min_gy, max_gy = min(ys), max(ys)
    return (min_gx * 4096.0, min_gy * 4096.0,
            (max_gx + 1) * 4096.0, (max_gy + 1) * 4096.0)


def _wrld_formid(esm: Path, edid: str):
    """FormID of the WRLD named `edid` in a built ESM, or None."""
    try:
        data = esm.read_bytes()
    except OSError:
        return None
    for rec in records(data, b'WRLD'):
        if rec.string(b'EDID') == edid:
            return rec.form_id
    return None


def _wrld_bounds(esm: Path, edid: str):
    """(minX, minY, maxX, maxY) from a plugin's WRLD record, or None.

    Reads the built ESM rather than the export so the bounds are exactly what
    the engine will see, including anything the override path rewrote.

    MNAM's NW/SE cell corners are preferred: they are what the map frames,
    which on Skyrim itself is only a third of the NAM0/NAM9 landmass
    rectangle -- the rest is unexplorable filler the map never shows.
    NAM0/NAM9 is the fallback when those corners are absent.
    """
    try:
        data = esm.read_bytes()
    except OSError:
        return None
    for rec in records(data, b'WRLD'):
        if rec.string(b'EDID') != edid:
            continue
        subs = rec.sub_map()
        n0 = subs.get(b'NAM0')
        n9 = subs.get(b'NAM9')
        n0 = struct.unpack('<2f', n0) if n0 and len(n0) == 8 else None
        n9 = struct.unpack('<2f', n9) if n9 and len(n9) == 8 else None
        mnam = subs.get(b'MNAM')
        mnam = struct.unpack_from('<4h', mnam, 8) if (
            mnam and len(mnam) >= 16) else None
        if mnam:
            from asset_convert.lod.worldmap_clouds import framed_rect
            rect = framed_rect(mnam[0], mnam[1], mnam[2], mnam[3])
            if rect:
                return rect
        if n0 and n9:
            return (n0[0], n0[1], n9[0], n9[1])
    return None


def merge_cloud_bank(out_root: Path, merged_dir: Path, edid: str,
                     master: str, plugins: list[str],
                     export_root: Path = None) -> str:
    """One world-map cloud bank covering the UNION of every sibling's bounds.

    Same overwrite problem the LOD tiles have, one level up.  The bank is a
    FILE at a fixed path (meshes/tes4/worldmapclouds/<worldspace>.nif) and each
    sibling generates its own sized to ITS OWN NAM0/NAM9 rectangle -- correct
    in isolation, wrong together.  Tamriel.esp and ElsweyrAnequina.esp both
    extend TES4Tamriel in different directions, so whichever the mod manager
    installs last supplies the bank for both, and it is sized for only one of
    them.  The plugin that loses gets a deck that stops short of its terrain.

    The bounds have the same problem in the record: the winning WRLD override
    supplies NAM0/NAM9 for everyone, and the map is drawn over exactly that
    rectangle.  So the honest fit is the union of every sibling's rectangle --
    that is the extent the map will actually show once they are all installed.

    Written into the merged folder, which installs last and wins the overwrite
    deliberately, exactly like the merged tiles.  Returns the Data-relative
    path written, or None when no bank could be built.
    """
    from asset_convert.lod.worldmap_clouds import (generate_cloud_bank, cloud_model_path,
                                  compute_center)

    # Union of every sibling's real LAND, not of their WRLD records: a
    # dependent overrides the WRLD without touching MNAM/NAM, so record-based
    # bounds are identical for all of them and the union collapses to the
    # master's (see _wrld_land_bounds).  Fall back to the record only when a
    # plugin contributes no cells of its own.
    boxes = []
    for name in [master] + list(plugins):
        esm = _out_root(out_root, name, export_root) / name
        if not esm.is_file():
            continue
        box = None
        fid = _wrld_formid(esm, edid)
        if fid is not None:
            box = _wrld_land_bounds(esm, fid)
        if box is None:
            box = _wrld_bounds(esm, edid)
        if box:
            boxes.append(box)
    if not boxes:
        return None

    min_x = min(b[0] for b in boxes)
    min_y = min(b[1] for b in boxes)
    max_x = max(b[2] for b in boxes)
    max_y = max(b[3] for b in boxes)
    width = abs(max_x - min_x)
    height = abs(max_y - min_y)
    if width <= 0.0 or height <= 0.0:
        return None

    center = compute_center(min_x, min_y, max_x, max_y)
    if not generate_cloud_bank(edid, width, height, str(merged_dir),
                               center=center,
                               land_rect=(min_x, min_y, max_x, max_y)):
        return None
    return cloud_model_path(edid)
