"""
LOD generation for converted TES4→TES5 worldspaces.

Workflow:
  1. write_lod_settings()  — write LODSettings/<WRLD>.lod (required by LODGen.exe)
  2. write_lodgen_input()  — scan the converted ESM, emit the LODGen data text file
  3. run_lodgen()          — call LODGenx64.exe to bake object LOD NIFs

All three are orchestrated by generate_lod(), which convert.py calls as Phase 4.
"""

import hashlib as _hashlib
import math
import os
import re as _re
import shutil
import struct
import sys
from collections import Counter, namedtuple
from pathlib import Path

from asset_convert import case_paths
from asset_convert.game_paths import win_join
from asset_convert.lod.esm_scan import (FLAG_DISTANT_LOD, parse_esm,
                                        parse_esm_cached)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

from asset_convert import paths

sys.path.insert(0, str(paths.REPO))
from core.subprocess_flags import run_streamed, windows_cmd

#: LODGen 3.0.36.0; 2.2.0.0 let one bad model kill the process. See run_lodgen().
LODGEN_EXE = paths.LODGEN


# ---------------------------------------------------------------------------
# 1. LODSettings file
# ---------------------------------------------------------------------------

def _cover_grid(sw_x: int, sw_y: int, ne_x: int, ne_y: int) -> tuple:
    """Smallest anchored quadtree square covering [sw, ne): (sw_x, sw_y, size, max_lod).

    SW is anchored at a multiple of `max_lod` (the coarsest level emitted,
    capped at 32) because LODGen snaps each tile's origin DOWN to a multiple of
    its own level, so tiles start below the literal terrain corner.  `max_lod`
    is chosen first since it sets the anchor granularity, then both grow
    together until the aligned square covers the terrain.
    See: docs/commentary/asset_convert_terrain.md#lodsettings-must-cover-the-terrain
    """
    max_lod = 4
    while True:
        anchor = min(max_lod, 32)
        eff_sw_x = (sw_x // anchor) * anchor
        eff_sw_y = (sw_y // anchor) * anchor
        size = anchor
        while (eff_sw_x + size < ne_x or eff_sw_y + size < ne_y) and size < 4096:
            size <<= 1
        if max_lod >= min(size, 32) or max_lod >= 32:
            break
        max_lod <<= 1
    return eff_sw_x, eff_sw_y, size, min(max_lod, 32)


def write_lod_settings(worldspace_edid: str, sw_x: int, sw_y: int,
                       ne_x: int, ne_y: int, output_dir: Path) -> tuple:
    """Write LODSettings/<worldspace_edid>.lod; `<hhIII` = SWx i16, SWy i16,
    size u32, minLOD u32 (always 4), maxLOD u32.

    `sw`/`ne` must be the CELL extents: the grid must COVER the terrain or the
    worldspace CTDs on entry.  Returns (path, effective_sw_x, effective_sw_y)
    for the LODGen CellSW= header line.
    See: docs/commentary/asset_convert_terrain.md#lodsettings-must-cover-the-terrain
    """
    lod_dir = output_dir / "LODSettings"
    lod_dir.mkdir(parents=True, exist_ok=True)
    out = lod_dir / f"{worldspace_edid}.lod"

    eff_sw_x, eff_sw_y, size, max_lod = _cover_grid(sw_x, sw_y, ne_x, ne_y)
    out.write_bytes(struct.pack("<hhIII", eff_sw_x, eff_sw_y, size, 4, max_lod))
    print(f"  Wrote {out}")
    return out, eff_sw_x, eff_sw_y


# ---------------------------------------------------------------------------
# 2. Parse the converted ESM to build the LODGen input text file.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# LOD mesh resolution helpers
# ---------------------------------------------------------------------------

# Model/texture paths throughout this module come from the game's own binary
# formats and are always backslash-separated regardless of host OS.  See
# asset_convert/game_paths.py for why a plain `root / rel` is wrong off Windows.
_win_join = win_join


#: Object-LOD mesh suffixes in resolution order; `_far` is also what we generate.
LOD_SUFFIXES = ('_lod', '_far')


def far_nif_path(model_path: str, *search_roots) -> str:
    """First suffix in `LOD_SUFFIXES` found in `search_roots`, else `_far.nif`.

    Pass every tree the mesh could be SOURCED from, never the bake tree alone --
    it starts empty. With no roots the answer is the `_far.nif` we generate into.
    See: docs/commentary/asset_convert_terrain.md#object-lod-suffix-differs-by-game
    """
    if not model_path:
        return ''
    base = model_path
    if base.lower().endswith('.nif'):
        base = base[:-4]
    for suffix in LOD_SUFFIXES:
        candidate = base + suffix + '.nif'
        if any(_mesh_exists(candidate, r) for r in search_roots if r is not None):
            return candidate
    return base + '_far.nif'


def _normalize(path: str) -> str:
    """Normalize mesh path to lowercase backslash form with meshes\\ prefix.

    Paths in the converted ESM are stored without the 'meshes\\' prefix
    (e.g. 'tes4\\Architecture\\foo.nif').  LODGen expects paths relative to
    the Data folder (e.g. 'meshes\\tes4\\architecture\\foo.nif').
    """
    p = path.lower().replace('/', '\\').strip('\\')
    if p and not p.startswith('meshes\\'):
        p = 'meshes\\' + p
    return p


def _meshes_rel(path: str) -> str:
    """`path` lowercased, backslashed, relative to a meshes root."""
    rel = (path or '').lower().replace('/', '\\').lstrip('\\')
    if rel.startswith('meshes\\'):
        rel = rel[len('meshes\\'):]
    return rel


def _mesh_exists(path: str, output_meshes_dir: Path) -> bool:
    """True when the mesh exists under `output_meshes_dir`, in any case.

    See: docs/commentary/asset_convert_paths.md#case-resolver
    """
    if not path:
        return False
    return case_paths.exists([output_meshes_dir], _meshes_rel(path), 'lod_mesh')


# LODGenx64 casts every LOD mesh's root block to NiNode without checking. A
# root that is a bare geometry block throws
# "InvalidCastException: Unable to cast NiTriShape to NiNode" on a worker
# thread, which is UNHANDLED — the process dies and the ENTIRE worldspace gets
# no object LOD at all (two 4-triangle scum meshes cost Morrowind_ob all
# 75,000 of its LOD references).  nif_converter now wraps geometry roots so
# converted meshes are safe, but stale files from an older run, hand-authored
# _far.nif meshes and anything a future source ships can still trip it, and
# the failure mode is far too expensive to risk.  Screening costs one small
# header read per unique mesh.
_NIF_ROOT_SAFE_CACHE = {}

# Distinguishes "not yet computed" from a cached "this base yields no LOD".
_MISSING = object()


def _mesh_screen_path(path: str, output_meshes_dir: Path) -> Path:
    """The mesh `_lod_mesh_is_safe` reads (any case), else its lowercase path."""
    rel = _meshes_rel(path)
    return (case_paths.resolve([output_meshes_dir], rel, 'lod_screen')
            or _win_join(output_meshes_dir, rel))


def _lod_mesh_is_safe(path: str, output_meshes_dir: Path) -> bool:
    """False if this mesh's root block would crash LODGen's NiNode cast."""
    full = _mesh_screen_path(path, output_meshes_dir)
    key = str(full).lower()
    cached = _NIF_ROOT_SAFE_CACHE.get(key)
    if cached is not None:
        return cached

    if not full.exists():
        # 🔴 NOT cached, and that is the whole point.  Absence is TRANSIENT
        # here: the caller stages a mesh into this tree
        # (`_import_master_mesh`) and screens it immediately afterwards, so a
        # verdict taken before staging would be served from the cache after it
        # and drop a mesh that is now sitting there perfectly readable.
        #
        # This is what broke object LOD for every worldspace once `output_dir`
        # became the shared LOD mod rather than the plugin's own output: at
        # prefetch time that tree is EMPTY, so every mesh cached as unsafe and
        # each worldspace ended with "No LOD references found".  It was
        # invisible before, because the plugin's own meshes were already in
        # `output_dir` and only master-owned ones were ever staged.
        return False

    safe = _root_is_ninode(full)
    _NIF_ROOT_SAFE_CACHE[key] = safe
    return safe


def _screenable_mesh_paths(refs, stats, scope, search_roots=()):
    """Unique mesh paths the LODGen-input loop will screen, for prefetching.

    Filters with the loop's own `_lod_exclusion`, so the prefetch reads the
    same files and nothing more: the FULL model plus its tier paths, never
    via `_lod_meshes_for` (it stages master meshes as a side effect).
    See: docs/commentary/asset_convert_terrain.md#prescreening-the-lodgen-input
    """
    out = []
    seen_base = set()
    for ref in refs:
        if (ref['base_fid'] in seen_base
                or _lod_exclusion(ref, stats, scope) is not None):
            continue
        seen_base.add(ref['base_fid'])
        out += _base_mesh_paths(stats[ref['base_fid']], search_roots)
    return out


def _base_mesh_paths(stat, search_roots) -> list:
    """A base's full model, explicit LOD meshes, else its `_far` tier paths."""
    from asset_convert.lod.lod_far_gen import tier_path, TIER8, TIER16
    model = stat['model']
    explicit = [stat[k] for k in ('lod4', 'lod8', 'lod16') if stat.get(k)]
    if explicit:
        return [model, *explicit]
    far = far_nif_path(model, *search_roots)
    return [model, far, str(tier_path(Path(far), TIER8['suffix'])),
            str(tier_path(Path(far), TIER16['suffix']))]


def _prescreen_meshes(paths, output_meshes_dir: Path, workers: int = 16,
                      source_meshes=None):
    """Warm `_NIF_ROOT_SAFE_CACHE` for `paths` using parallel header reads.

    Screening is a ~200-byte header read per unique mesh, so the cost is
    entirely file-open LATENCY, not CPU: profiling Tamriel's `write_lodgen_input`
    showed 13.5 s of which 11.5 s was `_io.open` across 2,582 serial opens
    (~4.4 ms each) while every core sat idle. Issuing them concurrently
    overlaps that latency.

    THREADS, not processes: this is pure I/O plus a tiny parse, the results must
    land in this process's cache, and a pool would pay spawn cost per worldspace
    to move ~200-byte payloads.

    Purely a warm-up — every entry is computed by the same `_root_is_ninode`
    the serial path uses, and any mesh missed here is simply screened on demand
    later. Order-independent, so it cannot affect FormIDs or output bytes.

    🔴 A mesh that exists in NEITHER tree is skipped rather than cached as
    unsafe.  The serial loop stages master-owned meshes into
    `output_meshes_dir` and screens them right after, so caching "missing =
    unsafe" up front would answer the later question with a stale verdict.
    That is exactly what cost every worldspace its object LOD once
    `output_dir` became the shared LOD mod (see `_lod_mesh_is_safe`).

    `source_meshes` is where the serial loop stages FROM. Screening the source
    is equivalent to screening the staged copy — `_import_master_mesh` uses
    `shutil.copy2`, so the two are byte-identical — and it is what keeps the
    warm-up worth doing at all in the shared-LOD-mod case, where nothing is in
    `output_meshes_dir` yet. Resolution mirrors `_import_master_mesh`: this
    tree first, then the source dirs in order, first hit wins.
    """
    todo = []
    seen = set()
    for p in paths:
        if not p:
            continue
        full = _mesh_screen_path(p, output_meshes_dir)
        key = str(full).lower()
        if key in _NIF_ROOT_SAFE_CACHE or key in seen:
            continue
        seen.add(key)
        read_from = full if full.exists() else case_paths.resolve(
            [Path(m) for m in (source_meshes or [])], _meshes_rel(p),
            'lod_screen')
        if read_from is not None:
            todo.append((key, read_from))
    if len(todo) < 2:
        return
    from concurrent.futures import ThreadPoolExecutor
    n = max(1, min(workers, len(todo)))
    with ThreadPoolExecutor(max_workers=n) as ex:
        for (key, _full), safe in zip(
                todo, ex.map(lambda t: _root_is_ninode(t[1]), todo)):
            _NIF_ROOT_SAFE_CACHE[key] = safe


# Block types LODGen can safely cast to NiNode (NiNode and its subclasses as
# they appear as a NIF root).  Resolved lazily against NifFormat so the list
# cannot drift from what pyffi actually considers a NiNode subclass.
_NINODE_ROOT_NAMES = None


#: NiNode subclasses LODGen still rejects as a root; subclassing is not enough.
_LODGEN_BAD_ROOTS = frozenset({
    'NiBSAnimationNode', 'NiBSParticleNode', 'NiSwitchNode', 'NiLODNode',
    'NiBillboardNode', 'RootCollisionNode', 'AvoidNode',
})


def _ninode_root_names():
    """Root block type names LODGen can cast, minus the ones it throws on.

    See: docs/commentary/asset_convert_terrain.md#lodgen-rejects-animated-roots
    """
    global _NINODE_ROOT_NAMES
    if _NINODE_ROOT_NAMES is None:
        from asset_convert.lod.lod_far_gen import NifFormat
        names = set()
        for attr in dir(NifFormat):
            cls = getattr(NifFormat, attr, None)
            if (isinstance(cls, type)
                    and issubclass(cls, NifFormat.NiNode)):
                names.add(attr)
        _NINODE_ROOT_NAMES = names - _LODGEN_BAD_ROOTS
    return _NINODE_ROOT_NAMES


def _root_is_ninode(full: Path) -> bool:
    """True if this NIF's FIRST root block is an NiNode subclass.

    Reads only the NIF HEADER (version, block-type table, block-type index)
    instead of parsing the whole file.  The full `NifFormat.Data.read` this
    replaces cost ~14 ms per mesh, and with ~8,800 unique base models that was
    minutes of the object-LOD stage — all to learn one block's type name.

    Header layout, verified against real converted output (20.2.0.7, UV1=12,
    BSStream 83):

        "Gamebryo File Format, Version 20.2.0.7\n"
        u32 version, u8 endian, u32 user_version, u32 num_blocks,
        u32 user_version_2            <- BSStream; ONLY when version >= 20.2
        3 x export-info short strings (u8 length + bytes)  <- only with UV2
        u16 num_block_types
        num_block_types x (u32 length + ASCII name)
        u16 block_type_index[num_blocks]   (high bit is a flag)

    Root is block 0 for every mesh we ship. Both fields I first guessed wrong
    (the missing user_version_2 and the export-info strings) made this return
    False for EVERY mesh — caught by temp/root_check.py, which diffs this
    against the full parse. Re-run that after any edit here.

    Anything unreadable returns False, matching the old behaviour: unreadable
    here means unreadable for LODGen too, and one bad mesh aborts the entire
    worldspace, so exclusion is the safe answer.
    """
    try:
        with open(full, 'rb') as fh:
            head = fh.read(8192)
        nl = head.find(b'\n')
        if nl < 0 or nl > 128:
            return False
        p = nl + 1
        version = struct.unpack_from('<I', head, p)[0]
        p += 4
        if version < 0x0A000100:          # older layouts differ; use the slow path
            return _root_is_ninode_slow(full)
        p += 1                            # endian type
        p += 4                            # user version
        num_blocks = struct.unpack_from('<I', head, p)[0]
        p += 4
        if version >= 0x14020007:
            p += 4                        # user version 2 (BSStream)
            for _ in range(3):            # export info: creator / scripts
                ln = head[p]
                p += 1 + ln
        num_types = struct.unpack_from('<H', head, p)[0]
        p += 2
        if not num_types or num_types > 512:
            return False
        types = []
        for _ in range(num_types):
            ln = struct.unpack_from('<I', head, p)[0]
            p += 4
            if ln > 128 or p + ln > len(head):
                return False
            types.append(head[p:p + ln].decode('ascii', 'replace').rstrip('\x00'))
            p += ln
        if not num_blocks or p + 2 > len(head):
            return False
        idx0 = struct.unpack_from('<H', head, p)[0] & 0x7FFF
        if idx0 >= len(types):
            return False
        return types[idx0] in _ninode_root_names()
    except Exception:
        return False


def _root_is_ninode_slow(full: Path) -> bool:
    """Full-parse fallback for header shapes the fast path does not model.

    Judged by CLASS NAME, not `isinstance`: the rejected roots are NiNode
    subclasses, so an isinstance test accepts every one of them.
    See: docs/commentary/asset_convert_terrain.md#lodgen-rejects-animated-roots
    """
    try:
        from asset_convert.lod.lod_far_gen import NifFormat
        data = NifFormat.Data()
        with open(full, 'rb') as fh:
            data.read(fh)
        roots = data.roots
        return (bool(roots) and isinstance(roots[0], NifFormat.NiNode)
                and roots[0].__class__.__name__ not in _LODGEN_BAD_ROOTS)
    except Exception:
        return False


#: Min OBND dimension (units) to reach the level-8 ring; level 4 has no gate.
LOD8_MIN_SIZE = 800.0

#: Same for level 16, the ring the WORLD MAP renders.
LOD16_MIN_SIZE = 1200.0


def obnd_max_dim(stat: dict) -> float:
    obnd = stat.get('obnd')
    if not obnd:
        return 0.0
    x1, y1, z1, x2, y2, z2 = obnd
    return float(max(x2 - x1, y2 - y1, z2 - z1))


# ---------------------------------------------------------------------------
# Master-owned assets and what a child plugin may ship
_STAGED_MASTER_MESHES = set()


def _register_if_staged_scratch(rel: str, output_meshes_dir: Path,
                                master_meshes) -> None:
    """Mark an already-present mesh as scratch if a previous run staged it.

    Only a mesh that (a) has no `.nif.generated` marker, so nothing in this
    tree derived it, and (b) exists identically in a master tree, so staging is
    where it could have come from, is treated as scratch. Both conditions are
    required: the marker test alone would sweep a hand-placed mesh, and the
    master test alone would sweep a legitimately derived one.
    """
    r = _meshes_rel(rel)
    dst = _win_join(output_meshes_dir, r)
    if not dst.exists() or str(dst) in _STAGED_MASTER_MESHES:
        return
    # Derived here -> has a marker -> not scratch, leave it alone.
    if dst.with_suffix('.nif.generated').exists():
        return
    for mdir in (master_meshes or []):
        src = case_paths.resolve([Path(mdir)], r, 'lod_stage')
        if src is not None and src.stat().st_size == dst.stat().st_size:
            _STAGED_MASTER_MESHES.add(str(dst))
            return


def _import_master_mesh(rel: str, output_meshes_dir: Path,
                        master_meshes) -> bool:
    """Stage a master's mesh into this plugin's tree, if needed.

    LODGen resolves every listed mesh under the SINGLE PathData root it is
    given, so a mesh that only exists in the master's tree cannot merely be
    referenced — listing it makes LODGen abort with "file not found" and bake
    no tiles at all. PathData cannot be widened to cover both trees: it is also
    the TEXTURE root and it contains PathOutput, so pointing it elsewhere either
    shadows this plugin's own assets or writes the .bto into the master's tree.

    So the file is staged here for the duration of the bake and REMOVED
    afterwards (_drop_staged_master_meshes). The .bto has the geometry baked in
    by then, and the master already ships the mesh at that same path, so
    shipping a second byte-identical copy would only bloat the plugin — 2,015
    meshes / 211 MB for ElsweyrAnequina.

    Returns True when the mesh is available in this plugin's tree afterwards.
    """
    if not rel:
        return False
    if _mesh_exists(rel, output_meshes_dir):
        # Already here -- but "already here" includes scratch a previous run
        # staged and failed to remove (a kill between the copy and
        # _drop_staged_master_meshes, or any run predating one-bake). Returning
        # without registering it left that copy pinned forever: every later run
        # took this same branch, so the post-bake cleanup never saw it.
        #
        # A file this tree GENERATED carries a `.nif.generated` marker from
        # lod_far_gen; staging copies the .nif alone and never the marker. So an
        # unmarked mesh in a bake tree that derives nothing is staged scratch,
        # and re-registering it lets this run finish the previous one's cleanup.
        _register_if_staged_scratch(rel, output_meshes_dir, master_meshes)
        return True
    return _stage_master_mesh(_meshes_rel(rel), output_meshes_dir,
                              master_meshes)


def _stage_master_mesh(r: str, output_meshes_dir: Path, master_meshes) -> bool:
    """Copy the first master's `r` (any case) to its LOWERCASE path here.

    Only a file this copies is registered as scratch, never a pre-existing one.
    See: docs/commentary/asset_convert_paths.md#case-resolver
    """
    src = case_paths.resolve([Path(m) for m in (master_meshes or [])], r,
                             'lod_stage')
    if src is None:
        return False
    dst = _win_join(output_meshes_dir, r)
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    except OSError:
        return False
    _STAGED_MASTER_MESHES.add(str(dst))
    return True


def _file_digest(p: Path) -> bytes:
    h = _hashlib.sha1()
    with open(p, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.digest()


def _overrides_master_model(model: str, own_meshes_root: Path,
                            master_meshes) -> bool:
    """True if this plugin ships its OWN version of a master's full model.

    Reusing a master's _far.nif is only correct when the geometry it was
    derived from is the geometry this plugin places. A child that overrides
    rock01.nif with a different shape must derive its own rock01_far.nif, or
    its distant LOD would show the MASTER's rock.

    Byte-identical is not an override — that is the ordinary duplicate case,
    where reuse is exactly what we want.
    """
    rel = _meshes_rel(model)
    own = case_paths.resolve([own_meshes_root], rel, 'lod_mesh') if rel else None
    if own is None:
        return False
    for mm in master_meshes:
        src = case_paths.resolve([Path(mm)], rel, 'lod_mesh')
        if src is None:
            continue
        if own.stat().st_size != src.stat().st_size:
            return True
        return _file_digest(own) != _file_digest(src)
    # Master has no copy: this model is wholly this plugin's.
    return True


def _drop_staged_master_meshes() -> int:
    """Remove the master meshes staged for the bake; return how many went.

    Mirrors _prune_unaffected_tiles: output that is byte-for-byte what the
    master already ships is not this plugin's to carry.
    """
    n = 0
    for p in sorted(_STAGED_MASTER_MESHES):
        try:
            os.remove(p)
            n += 1
        except OSError:
            pass
    _STAGED_MASTER_MESHES.clear()
    return n


def _lod_meshes_for(stat: dict, output_meshes_dir: Path, master_meshes=None):
    """Return (lod4, lod8, lod16) mesh paths for a stat record.

    Every LOD object gets lod4. LOD8_MIN_SIZE gates lod8 and LOD16_MIN_SIZE
    gates lod16, but an AUTHORED _far.nif bypasses both: shipping one is a
    decision that the object belongs at distance. Trees pass every gate,
    reusing their 8-vert billboard at each level.
    `master_meshes` are searched for LOD meshes generated only into a master's
    output; each is staged into this plugin's tree so LODGen resolves it.
    See: docs/commentary/asset_convert_terrain.md#coarse-ring-size-gates
    """
    lod4  = stat.get('lod4', '')
    lod8  = stat.get('lod8', '')
    lod16 = stat.get('lod16', '')

    if lod4 or lod8 or lod16:
        return lod4, lod8, lod16

    model = stat.get('model', '')
    if not model:
        return '', '', ''

    far = far_nif_path(model, output_meshes_dir, *(master_meshes or ()))
    if not _import_master_mesh(far, output_meshes_dir, master_meshes):
        return '', '', ''

    from asset_convert.lod.lod_far_gen import (has_authored_lod,
                                              is_tree_model,
                                              tier_path,
                                              TIER8, TIER16)
    is_tree = is_tree_model(stat)

    dim = obnd_max_dim(stat)
    authored = has_authored_lod(output_meshes_dir, far)
    if dim < LOD8_MIN_SIZE and not authored:
        return far, '', ''
    if is_tree:
        return far, far, far

    def tier(spec):
        """The tier's mesh path if it resolves, else the base `_far` mesh."""
        path = str(tier_path(Path(far), spec['suffix']))
        return (path if _import_master_mesh(path, output_meshes_dir,
                                            master_meshes) else far)
    lod16 = tier(TIER16) if (authored or dim >= LOD16_MIN_SIZE) else ''
    return far, tier(TIER8), lod16


# ---------------------------------------------------------------------------
# 3. Build the LODGen input text file
# ---------------------------------------------------------------------------


# The LOD levels OBJECT LOD is baked at. Terrain goes out to 32, but object
# tiles stop at 16: censused across every converted worldspace, Oblivion.esm
# ships 734/204/59 .bto at levels 4/8/16 and ZERO at level 32 (same 4/8/16-only
# split in SEWorld and all 16 small worldspaces). Including 32 here cost a 4x
# wider footprint — a level-32 tile spans 32x32 cells, so it dragged 2,048 cells
# into the LODGen input to satisfy a tile that is never produced.
_OBJ_LOD_LEVELS = (4, 8, 16)


def _kept_tile_cells(only_cells, levels=_OBJ_LOD_LEVELS) -> set:
    """Every cell composited by a tile that `_prune_unaffected_tiles` keeps.

    A tile survives pruning when it covers ANY changed cell, and it composites
    all level x level cells of its footprint. So the cells whose objects can
    still reach a surviving tile are the UNION of those footprints — strictly
    wider than `only_cells` itself, and the exact set worth baking.

    Anything outside it lands only in tiles that are deleted moments later, so
    listing it makes LODGen bake geometry into a file destined for `unlink()`.
    That is the whole cost being avoided: DLCBattlehornCastle changes 14 cells
    and keeps 8 of 997 tiles, having baked all 997 from ~1M references.

    The largest level dominates the union (a level-16 tile spans 16x16 cells),
    so the result stays a wide neighbourhood, not a tight box — deliberately,
    because a coarse tile at the edit's edge really does draw those objects.

    Use `_kept_tile_cells_by_level` when the goal is to stop LODGen CREATING
    surplus tiles; this flat union only bounds their CONTENT.
    """
    kept = set()
    for cells in _kept_tile_cells_by_level(only_cells, levels).values():
        kept |= cells
    return kept


def _kept_tile_cells_by_level(only_cells, levels=_OBJ_LOD_LEVELS) -> dict:
    """Per-level footprints: {level: cells composited by that level's kept tiles}.

    LODGen has no switch for "bake only these tiles" — it derives the tile set
    from the references it is given, emitting a tile at EVERY level for any
    cell that carries one. So a single flat footprint (the level-32 union, 32x32
    cells wide) makes it bake a level-4 tile for all 1,024 of those cells, and
    `_prune_unaffected_tiles` then deletes nearly all of them: measured on
    DLCBattlehornCastle, 177 tiles baked to ship 8.

    Splitting per level is what removes that. A reference is listed for a level
    only when it falls inside a tile THAT level actually keeps, so a distant
    object still reaches the coarse level-32 tile that legitimately draws it
    while contributing no level-4 tile of its own.
    """
    by_level = {}
    for level in levels:
        # A tile's SW corner is floor-aligned to its own level.
        tiles = {((cx // level) * level, (cy // level) * level)
                 for cx, cy in only_cells}
        cells = set()
        for tx, ty in tiles:
            for dy in range(level):
                for dx in range(level):
                    cells.add((tx + dx, ty + dy))
        by_level[level] = cells
    return by_level


#: What decides whether a reference is in scope; `state` memoises `_initially_enabled`.
_Scope = namedtuple('_Scope', 'cell_wrld wrld_fid keep_cells ref_index state')

#: REFR header flags the selection reads (TES5 values).
FLAG_PERSISTENT = 0x00000400
FLAG_INITIALLY_DISABLED = 0x00000800

#: "Is Full LOD" on a REFR; the engine draws a persistent one itself.
FLAG_FULL_LOD = 0x00010000

#: The player's local FormID: an enable parent that is always enabled.
_PLAYER_REF = 0x000014

#: Longest enable-parent chain walked before the state counts as unknown.
_MAX_XESP_DEPTH = 16

#: Model folders holding effect meshes (cloud decks, ground mist), not objects.
_EFFECT_DIRS = frozenset({'effects', 'fx'})

#: Per-reference exclusions, with their log labels; see `_state_exclusion`.
_STATE_RULES = {
    'disabled': 'disabled at game start (enable-parent chain)',
    'full-lod': 'persistent + Is Full LOD (drawn by the engine)',
    'effects': 'not VWD and an effect mesh',
}

#: Kept references whose enable parent is not a scanned REFR (an actor).
_UNKNOWN_PARENT = 'enable parent not scanned (kept)'

#: Where a base's meshes are checked, sourced and staged; see `_resolve_base_entry`.
_BaseRoots = namedtuple('_BaseRoots',
                        'output_meshes_dir owned_meshes master_meshes '
                        'replace_tiles')


def _find_worldspace(worldspaces, worldspace_edid):
    """`(fid, info)` for `worldspace_edid`, else the first one (warned), else None."""
    for fid, w in worldspaces.items():
        if w['edid'].lower() == worldspace_edid.lower():
            return fid, w
    if not worldspaces:
        print("  Error: no worldspaces found in ESM")
        return None
    fid, w = next(iter(worldspaces.items()))
    print(f"  Warning: worldspace '{worldspace_edid}' not found, "
          f"using '{w['edid']}'")
    return fid, w


def _ref_cell(ref) -> tuple:
    """The grid cell `ref` stands in, from its position (4096 units a cell)."""
    return (int(math.floor(ref['x'] / 4096.0)),
            int(math.floor(ref['y'] / 4096.0)))


def _lod_exclusion(ref, stats, scope):
    """Why `ref` is left out of the LODGen input, or None to list it.

    In order: outside the worldspace, outside the kept tiles' footprint
    (position decides, not the parent CELL, which an override may not
    carry), no base, no model, base not flagged for distant LOD.
    See: docs/commentary/asset_convert_terrain.md#lodgen-input-shape
    """
    if (ref['parent_wrld'] != scope.wrld_fid
            and scope.cell_wrld.get(ref['parent_cell'], 0) != scope.wrld_fid):
        return 'worldspace'
    if scope.keep_cells is not None and _ref_cell(ref) not in scope.keep_cells:
        return 'footprint'
    if ref['base_fid'] not in stats:
        return 'no-base'
    stat = stats[ref['base_fid']]
    if not stat.get('model', ''):
        return 'no-model'
    if not (stat.get('flags', 0) & FLAG_DISTANT_LOD):
        return 'not-lod'
    return _state_exclusion(ref, stat, scope)


def _state_exclusion(ref, stat, scope):
    """The per-reference rule dropping `ref` (a `_STATE_RULES` key), or None.

    Static LOD cannot follow enable state, so it shows the game-start state.
    See: docs/commentary/asset_convert_terrain.md#object-lod-selection
    """
    if _initially_enabled(ref['form_id'], scope) is False:
        return 'disabled'
    flags = ref['flags']
    if flags & FLAG_PERSISTENT and flags & FLAG_FULL_LOD:
        return 'full-lod'
    if not flags & FLAG_DISTANT_LOD and _is_effect_mesh(stat['model']):
        return 'effects'
    return None


def _is_effect_mesh(model: str) -> bool:
    """True when the model sits in an `effects` or `fx` folder."""
    return bool(_EFFECT_DIRS & set(_meshes_rel(model).split('\\')[:-1]))


def _initially_enabled(fid, scope, depth=0):
    """True/False: is `fid` enabled at game start; None when it cannot be known.

    A ref with an enable parent takes the parent's state, inverted by the
    XESP opposite bit, and ignores its own Initially Disabled flag; the
    player is always enabled; an unscanned parent or an over-long chain is
    unknown.
    See: docs/commentary/asset_convert_terrain.md#object-lod-selection
    """
    if fid in scope.state:
        return scope.state[fid]
    ref = scope.ref_index.get(fid)
    if fid & 0x00FFFFFF == _PLAYER_REF:
        state = True
    elif ref is None or depth > _MAX_XESP_DEPTH:
        state = None
    elif ref.get('xesp'):
        parent = _initially_enabled(ref['xesp'][0], scope, depth + 1)
        state = None if parent is None else parent != bool(ref['xesp'][1] & 1)
    else:
        state = not ref['flags'] & FLAG_INITIALLY_DISABLED
    scope.state[fid] = state
    return state


def _owned_by_master(model: str, roots) -> bool:
    """True when a master already ships this model's LOD and tiles sit beside it."""
    return (not roots.replace_tiles
            and any(_mesh_exists(far_nif_path(model, m), m)
                    for m in roots.owned_meshes))


def _resolve_base_entry(base_fid, stat, roots, skipped_unsafe):
    """The base-object half of a LODGen row, or None when the base is not listed.

    Resolves the LOD meshes, stages the full model (LODGen falls back to
    it), and screens every listed mesh; an unsafe one is added to
    `skipped_unsafe` and drops the base, since one bad mesh aborts the bake.
    See: docs/commentary/asset_convert_terrain.md#write-lodgen-input-master-modes
    """
    model = stat.get('model', '')
    if _owned_by_master(model, roots):
        return None
    lods = _lod_meshes_for(stat, roots.output_meshes_dir, roots.master_meshes)
    if not any(lods):
        return None
    _import_master_mesh(model, roots.output_meshes_dir, roots.master_meshes)
    unsafe = [m for m in (model, *lods)
              if m and not _lod_mesh_is_safe(m, roots.output_meshes_dir)]
    if unsafe:
        skipped_unsafe.update(_normalize(m) for m in unsafe)
        return None
    stat_edid = stat.get('edid', f'{base_fid:08X}')
    return (f"{stat_edid}\t{stat.get('flags', 0):08X}\t\t"
            + '\t'.join(_normalize(m) for m in (model, *lods)))


def _ref_line(ref, base_entry: str) -> str:
    """One LODGen row: the REFR's id, flags, placement (degrees), then its base."""
    rx, ry, rz = (math.degrees(ref[k]) for k in ('rx', 'ry', 'rz'))
    return (f"{ref['form_id']:08X}\t{ref['flags']:08X}\t"
            f"{ref['x']:.4f}\t{ref['y']:.4f}\t{ref['z']:.4f}\t"
            f"{rx:.4f}\t{ry:.4f}\t{rz:.4f}\t"
            f"{ref['scale']:.4f}\t{base_entry}")


def _reference_lines(refs, stats, scope, roots):
    """(rows, unsafe mesh paths, {rule: Counter(model)}), bases memoised.

    The third item counts each `_STATE_RULES` drop and each `_UNKNOWN_PARENT` keep.
    See: docs/commentary/asset_convert_terrain.md#lodgen-input-shape
    """
    lines, skipped_unsafe, base_cache = [], set(), {}
    selection = {k: Counter() for k in (*_STATE_RULES, _UNKNOWN_PARENT)}
    for ref in refs:
        reason = _lod_exclusion(ref, stats, scope)
        if reason is None and _initially_enabled(ref['form_id'], scope) is None:
            selection[_UNKNOWN_PARENT][stats[ref['base_fid']]['model']] += 1
        if reason in selection:
            selection[reason][stats[ref['base_fid']]['model']] += 1
        if reason is not None:
            continue
        base_fid = ref['base_fid']
        entry = base_cache.get(base_fid, _MISSING)
        if entry is _MISSING:
            entry = base_cache[base_fid] = _resolve_base_entry(
                base_fid, stats[base_fid], roots, skipped_unsafe)
        if entry is not None:
            lines.append(_ref_line(ref, entry))
    return lines, skipped_unsafe, selection


def _log_selection(selection, listed: int) -> None:
    """Print each per-reference rule's count and its five commonest models."""
    dropped = sum(sum(selection[k].values()) for k in _STATE_RULES)
    print(f"  Object-LOD selection: {listed} reference(s) listed, "
          f"{dropped} dropped by per-reference rules")
    for key, models in selection.items():
        top = ', '.join(f"{m} x{n}" for m, n in models.most_common(5))
        print(f"    {_STATE_RULES.get(key, key)}: {sum(models.values())}"
              + (f" -- {top}" if top else ''))


def _warn_unsafe(skipped_unsafe) -> None:
    """Name the meshes screened out as unreadable or with a non-NiNode root."""
    if not skipped_unsafe:
        return
    print(f"  WARNING: {len(skipped_unsafe)} LOD mesh(es) excluded — "
          f"unreadable or non-NiNode root (would crash LODGen and lose "
          f"ALL of this worldspace's object LOD):")
    for m in sorted(skipped_unsafe)[:10]:
        print(f"    {m}")
    if len(skipped_unsafe) > 10:
        print(f"    ... and {len(skipped_unsafe) - 10} more")


def _write_input_file(output_dir: Path, edid: str, sw: tuple, lines) -> Path:
    """Write the header and rows next to LODGen; return the file.

    PathData and PathOutput are absolute (LODGen runs from its own folder)
    and PathData ends in a backslash; CellSW must equal the `.lod` SW.
    See: docs/commentary/asset_convert_terrain.md#lodgen-input-shape
    """
    dest = (Path(output_dir).resolve() / 'meshes' / 'terrain' / edid
            / 'Objects')
    path_data = str(Path(output_dir).resolve()).rstrip('\\/') + '\\'
    header = [
        "GameMode=TES5",
        f"Worldspace={edid}",
        f"CellSW={sw[0]} {sw[1]}",
        f"PathData={path_data}",
        f"PathOutput={dest}",
    ]
    out_txt = LODGEN_EXE.parent / f"LODGen {edid}.txt"
    with open(out_txt, 'w', encoding='utf-8') as f:
        f.write('\n'.join(header) + '\n')
        f.write('\n'.join(lines) + '\n')
    return out_txt


def write_lodgen_input(esm_path: Path, output_dir: Path,
                       worldspace_edid: str,
                       _parsed=None,
                       cell_sw: tuple = None,
                       master_dirs=None, master_mesh_dirs=None,
                       replace_tiles=False, only_cells=None) -> Path:
    """Parse the converted ESM and write the LODGen input text file.

    `master_dirs`: masters whose shipped LOD drops a ref (off under
    `replace_tiles`). `master_mesh_dirs`: where meshes are sourced and staged
    from. `only_cells`: list only refs landing in a kept tile. `cell_sw` is
    the `.lod` SW (else MNAM). Returns the file, or None when nothing is listed.
    See: docs/commentary/asset_convert_terrain.md#write-lodgen-input-master-modes
    See: docs/commentary/asset_convert_terrain.md#prescreening-the-lodgen-input
    """
    if _parsed is not None:
        worldspaces, cells, stats, refs = _parsed
    else:
        print(f"  Parsing ESM: {esm_path.name}")
        worldspaces, cells, stats, refs = parse_esm(esm_path)
    found = _find_worldspace(worldspaces, worldspace_edid)
    if found is None:
        return None
    wrld_fid, wrld_info = found
    edid = wrld_info['edid']
    sw = cell_sw if cell_sw is not None else (wrld_info['sw_x'],
                                              wrld_info['sw_y'])
    output_meshes_dir = output_dir / 'meshes'
    master_meshes = [Path(d) / 'meshes' for d in (master_mesh_dirs or [])]
    roots = _BaseRoots(output_meshes_dir,
                       [Path(d) / 'meshes' for d in (master_dirs or [])],
                       master_meshes, replace_tiles)
    keep_cells = _kept_tile_cells(only_cells) if only_cells else None
    scope = _Scope({fid: c['parent_wrld'] for fid, c in cells.items()},
                   wrld_fid, keep_cells,
                   {r['form_id']: r for r in refs if r is not None}, {})
    _prescreen_meshes(_screenable_mesh_paths(
                          refs, stats, scope,
                          (output_meshes_dir, *master_meshes)),
                      output_meshes_dir, source_meshes=master_meshes)
    lines, skipped_unsafe, selection = _reference_lines(refs, stats, scope,
                                                        roots)
    _warn_unsafe(skipped_unsafe)
    _log_selection(selection, len(lines))
    if not lines:
        print(f"  No LOD references found for worldspace '{edid}'")
        return None
    out_txt = _write_input_file(output_dir, edid, sw, lines)
    if keep_cells is not None:
        print(f"  Restricted to the {len(keep_cells)} cell(s) covered by the "
              f"tiles this run keeps: {len(lines)} of {len(refs)} references "
              f"listed")
    print(f"  LODGen input: {out_txt} ({len(lines)} references)")
    return out_txt


_LODGEN_ERR_RE = _re.compile(r'Error processing (\S+)')

#: One line per finished tile; counting them detects a run that died partway.
_LODGEN_TILE_RE = _re.compile(r'Finished LOD level \d+ coord ')

#: The one fault 3.x does not catch per object — it unwinds the whole run.
_LODGEN_FATAL = 'NullReferenceException'


def run_lodgen(lodgen_input: Path, output_dir: Path) -> bool:
    """Invoke LODGen to bake the worldspace's object-LOD .bto tiles.

    Success is judged by tiles produced, never the exit code: 3.x returns
    nonzero whenever any object failed even on a complete bake. A run killed
    by `NullReferenceException` loses every unwritten tile, so it is retried
    with the faulting models stripped from the input; False means the bake is
    still incomplete.
    See: docs/commentary/asset_convert_terrain.md#lodgen-nullreference-retry
    """
    if not LODGEN_EXE.exists():
        print(f"  ERROR: LODGen not found at {LODGEN_EXE}")
        return False

    banned: set = set()
    for attempt in range(_LODGEN_MAX_ATTEMPTS):
        out, code = _invoke_lodgen(lodgen_input)
        skipped = sorted(set(_LODGEN_ERR_RE.findall(out)))
        if skipped:
            print(f"  WARNING: LODGen could not process {len(skipped)} "
                  f"model(s); they have no distant LOD and will pop in at "
                  f"load distance: {', '.join(skipped)}")

        fatal = [e for e in skipped if e not in banned]
        if _LODGEN_FATAL not in out or not fatal:
            break
        banned |= set(fatal)
        print(f"  LODGen died on {_LODGEN_FATAL} after "
              f"{len(_LODGEN_TILE_RE.findall(out))} tile(s) — every later tile "
              f"was lost. Re-running without {', '.join(fatal)} "
              f"(attempt {attempt + 2}).")
        if not _drop_lodgen_refs(lodgen_input, banned):
            break

    tiles = _lodgen_output_dir(lodgen_input)
    baked = len(list(tiles.glob('*.bto'))) if tiles else 0
    if not baked:
        print(f"  WARNING: LODGen produced no .bto tiles (exit code {code})")
        return False
    if banned and _LODGEN_FATAL in out:
        print(f"  WARNING: LODGen still died after excluding {len(banned)} "
              f"model(s); object LOD is INCOMPLETE ({baked} tiles).")
        return False
    return True


#: Bounds a pathological input; a clean run never retries at all.
_LODGEN_MAX_ATTEMPTS = 4


def _invoke_lodgen(lodgen_input: Path):
    """Run LODGen once; return (combined stdout+stderr, exit code).

    PathOutput is embedded in the input file, so LODGen reads it from there.
    Output is streamed, not captured: a bake runs for minutes.
    See: docs/commentary/asset_convert_terrain.md#lodgen-output-must-stream
    """
    cmd = [
        str(LODGEN_EXE),
        str(lodgen_input),
        "--dontFixTangents",
        "--removeUnseenFaces",
    ]
    print(f"  Running: {' '.join(cmd)}")
    return run_streamed(windows_cmd(cmd), cwd=str(LODGEN_EXE.parent))


def _drop_lodgen_refs(lodgen_input: Path, banned: set) -> bool:
    """Rewrite the input without `banned` EditorIDs. True if anything changed.

    A reference row is the 9 REFR fields followed by `base_entry`, so the base
    record's EditorID — which is the name LODGen prints in its error — sits at
    index 9. Header lines carry no tab and are copied through untouched.
    """
    try:
        text = lodgen_input.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return False
    lines = text.splitlines()
    kept = [ln for ln in lines
            if len(ln.split('\t')) <= _LODGEN_EDID_FIELD
            or ln.split('\t')[_LODGEN_EDID_FIELD] not in banned]
    if len(kept) == len(lines):
        return False
    try:
        lodgen_input.write_text('\n'.join(kept) + '\n', encoding='utf-8')
    except OSError:
        return False
    return True


#: Index of the base EditorID in a LODGen reference row (9 REFR fields first).
_LODGEN_EDID_FIELD = 9


def _lodgen_output_dir(lodgen_input: Path):
    """The PathOutput directory declared in a LODGen input file."""
    try:
        for line in lodgen_input.read_text(encoding='utf-8',
                                           errors='replace').splitlines():
            if line.startswith('PathOutput='):
                return Path(line.split('=', 1)[1].strip())
            if '\t' in line:  # reference rows follow the header
                break
    except OSError:
        pass
    return None


def _far_owner_dirs(referenced_models, far_nif_dirs) -> dict:
    """{plugin output root: models it ships the FULL mesh for}.

    Routing per model rather than per run is the point: one worldspace draws
    objects from every selected plugin, so no single tree owns them all, and
    generating from a tree that lacks the source mesh produces nothing at all.
    Later dirs win, matching load order -- if two plugins ship the same path,
    the one that overrides it is the geometry actually placed.
    """
    by_dir: dict = {}
    for model in referenced_models:
        owner = None
        for d in far_nif_dirs:
            if _mesh_exists(model, Path(d) / 'meshes'):
                owner = Path(d)
        if owner is not None:
            by_dir.setdefault(owner, set()).add(model)
    return by_dir


def _overlays_by_asset_dir(far_nif_dirs, overlay_manifest_dirs) -> dict:
    """Map each asset dir to the APPLY_HILIGHT2 diffuses of its own plugin.

    The two lists are index-aligned by `create_lod._supplier_overlay_dirs`.
    See: docs/commentary/asset_convert_shader.md#detail-overlay-diffuses
    """
    from asset_convert.texture import texture_prune
    out: dict = {}
    absent = []
    for d, export_dir in zip(far_nif_dirs or (), overlay_manifest_dirs or ()):
        name = texture_prune.OVERLAY_MANIFEST_NAME
        if not (Path(export_dir) / name).is_file():
            absent.append(Path(d).name)
            continue
        keys = texture_prune.read_manifest(Path(export_dir), name)
        if keys:
            out[Path(d)] = keys
    if absent:
        print(f"  NOTE: no detail-overlay manifest for {', '.join(absent)}"
              " — re-run their meshes stage, or their overlay diffuses stay"
              " see-through at distance")
    return out


def _authored_far_dirs(referenced_models, far_nif_dirs) -> dict:
    """{model: (dir, far path)} for a hand-made LOD mesh at or after its owner.

    The last dir shipping an authored `_far`/`_lod` wins, as for the full
    model, but never one earlier than the dir whose full model is placed:
    that LOD was made from different geometry.
    See: docs/commentary/asset_convert_terrain.md#authored-far-from-any-plugin
    """
    from asset_convert.lod.lod_far_gen import has_authored_lod
    out = {}
    for model in referenced_models:
        owner = authored = None
        for i, d in enumerate(Path(x) for x in far_nif_dirs):
            far_rel = far_nif_path(model, d / 'meshes')
            if _mesh_exists(model, d / 'meshes'):
                owner = i
            if has_authored_lod(d / 'meshes', far_rel):
                authored = (i, d, far_rel)
        if authored and (owner is None or authored[0] >= owner):
            out[model] = authored[1:]
    return out


def _stage_authored_far(far_rel: str, src_meshes: Path, out_meshes: Path) -> bool:
    """Stage an authored LOD mesh, first removing a generated one and its tiers."""
    from asset_convert.lod.lod_far_gen import tier_path, TIER8, TIER16
    dst = _win_join(out_meshes, _meshes_rel(far_rel))
    if dst.with_suffix('.nif.generated').exists():
        for p in (dst, tier_path(dst, TIER8['suffix']),
                  tier_path(dst, TIER16['suffix'])):
            p.unlink(missing_ok=True)
            p.with_suffix('.nif.generated').unlink(missing_ok=True)
    return _import_master_mesh(far_rel, out_meshes, [src_meshes])


def _derive_far_meshes(stats, output_dir, referenced_models, far_nif_dirs,
                       overlay_manifest_dirs=None):
    """Derive every referenced model's LOD mesh into the bake tree.

    Generated files are written straight to `output_dir`; a plugin's AUTHORED
    _far/_lod (`_authored_far_dirs`) is staged in instead, and removed after
    the bake.
    See: docs/commentary/asset_convert_terrain.md#generated-far-nif-belong-to-the-lod-mod
    """
    from asset_convert.lod.lod_far_gen import generate_missing_far_nifs
    if not far_nif_dirs:
        generate_missing_far_nifs(stats, output_dir / 'meshes',
                                  referenced_models=referenced_models,
                                  force_regen_generated=True,
                                  tex_roots=(output_dir / 'textures',))
        return
    overlays = _overlays_by_asset_dir(far_nif_dirs, overlay_manifest_dirs)
    authored = _authored_far_dirs(referenced_models, far_nif_dirs)
    by_dir = _far_owner_dirs(set(referenced_models) - set(authored),
                             far_nif_dirs)
    made = 0
    for d, models in by_dir.items():
        made += generate_missing_far_nifs(
            stats, d / 'meshes', referenced_models=models,
            force_regen_generated=True,
            tex_roots=(output_dir / 'textures', d / 'textures'),
            gen_meshes_dir=output_dir / 'meshes',
            overlay_diffuses=overlays.get(Path(d)))
    staged = sum(_stage_authored_far(far_rel, d / 'meshes', output_dir / 'meshes')
                 for d, far_rel in authored.values())
    if made:
        print(f"  Derived {made} _far.nif mesh(es) into the LOD tree")
    print(f"  Authored LOD meshes staged: {staged} of {len(authored)}")


# ---------------------------------------------------------------------------
# 5. Top-level orchestration
# ---------------------------------------------------------------------------

def generate_lod(esm_path: Path, output_dir: Path,
                 worldspace_edid: str = 'Tamriel',
                 master_dirs=None, master_texture_dirs=None,
                 master_mesh_dirs=None,
                 overlay_paths=None, only_cells=None,
                 far_nif_dirs=None, overlay_manifest_dirs=None) -> bool:
    """
    Full LOD generation pipeline:
      1. Write LODSettings/<worldspace>.lod
      2. Parse ESM → LODGen input text
      3. Run LODGenx64.exe

    Args:
        esm_path:          Path to the converted .esm/.esp holding the WRLD/
                           CELL/REFR records. For an OVERRIDE plugin this is
                           the MASTER's output, not the plugin's own — the
                           plugin's records arrive via `overlay_paths`.
        overlay_paths:     Plugins applied ON TOP of esm_path, in load order.
                           References merge by FormID so a moved, rescaled or
                           deleted REFR REPLACES the master's entry instead of
                           being drawn twice.
        only_cells:        Restrict output to tiles covering these (x, y)
                           cells; None means the whole worldspace.
        output_dir:        Output dir owning the assets and receiving the
                           generated LOD (contains meshes/, textures/, …).
        worldspace_edid:   Editor ID of the worldspace to generate LOD for
        master_dirs:       Converted output dirs of this plugin's masters, for
                           TILE OWNERSHIP. Anything they already ship LOD for is
                           skipped, so an override plugin bakes only what IT
                           introduces. Only set when a MASTER owns the
                           worldspace — when THIS plugin owns it, the master
                           ships no tiles for it and nothing may be skipped.
        master_mesh_dirs:  Converted output dirs of this plugin's masters, for
                           MESH REUSE, always. A plugin routinely places a
                           master's models in its OWN worldspace; those models
                           and their _far.nif LOD were converted into the
                           master's output only. Reusing them is both correct
                           and far cheaper than re-deriving them here, and
                           without it the full model is absent from this tree,
                           so screening rejects the object and its LOD is lost.
                           Distinct from master_dirs because the tile-ownership
                           skip above is NOT valid when this plugin owns the
                           worldspace, while mesh reuse always is.
        master_texture_dirs: Converted output dirs of every other plugin,
                           always. A plugin regularly places a master's models
                           in its OWN worldspace, and their textures exist only
                           in the master's output; the .bto tiles baked here
                           still reference them, so they are copied in.
        far_nif_dirs:      Plugin output dirs receiving newly DERIVED _far.nif
                           meshes: each model's goes to the dir shipping its
                           full model, is STAGED into `output_dir` for the bake
                           and dropped after. None writes them under
                           `output_dir` — the single-plugin behaviour.
                           See: docs/commentary/asset_convert_terrain.md#generated-far-nif-belong-to-the-lod-mod
        overlay_manifest_dirs: Per-plugin EXPORT asset dirs holding the
                           APPLY_HILIGHT2 diffuse manifests, index-aligned with
                           `far_nif_dirs`.
                           See: docs/commentary/asset_convert_shader.md#detail-overlay-diffuses

    Returns True on success.
    """
    print(f"\n[LOD] Generating object LOD for worldspace '{worldspace_edid}'")

    # Parse ESM once; reuse data for both LODSettings and LODGen input.
    #
    # Served from _PARSED_ESM_CACHE, because this function runs once per
    # WORLDSPACE and the parse is 5.7 s over 613 MB — 18 worldspaces meant
    # ~103 s spent re-deriving identical data.
    #
    # The overlay merge below MUTATES all four structures (dict.update, list
    # append/replace), so take shallow copies before touching them or the
    # second worldspace inherits the first one's merged state.  Copies are
    # cheap next to the parse: the per-record dicts are shared, and nothing
    # here mutates an individual record.
    print(f"  Parsing ESM: {esm_path.name}")
    worldspaces, cells, stats, refs = parse_esm_cached(esm_path)
    if overlay_paths:
        worldspaces = dict(worldspaces)
        cells = dict(cells)
        stats = dict(stats)
        refs = list(refs)

    # Apply override plugins on top, in load order. References merge BY FORMID
    # so a plugin that moved, rescaled or re-based one of the master's objects
    # replaces it rather than adding a second copy at the old spot, and a
    # DELETED override (header flag 0x20) removes it from LOD entirely — the
    # object is gone in-game, so a distant copy of it would be a floating
    # ghost. STAT/CELL/worldspace tables merge by key the same way.
    for ov_path in (overlay_paths or []):
        ov_path = Path(ov_path)
        print(f"  Applying override plugin: {ov_path.name}")
        o_wrld, o_cells, o_stats, o_refs = parse_esm_cached(ov_path)
        worldspaces.update(o_wrld)
        cells.update(o_cells)
        stats.update(o_stats)
        by_fid = {r['form_id']: i for i, r in enumerate(refs)}
        added = replaced = removed = 0
        for r in o_refs:
            idx = by_fid.get(r['form_id'])
            if r['flags'] & 0x20:          # deleted by the author
                if idx is not None:
                    refs[idx] = None
                    removed += 1
                continue
            if idx is None:
                by_fid[r['form_id']] = len(refs)
                refs.append(r)
                added += 1
            else:
                refs[idx] = r
                replaced += 1
        refs = [r for r in refs if r is not None]
        print(f"    references: {added} added, {replaced} replaced, "
              f"{removed} deleted")

    wrld_fid  = None
    wrld_info = None
    for fid, w in worldspaces.items():
        if w['edid'].lower() == worldspace_edid.lower():
            wrld_fid  = fid
            wrld_info = w
            break
    if wrld_info is None and worldspaces:
        wrld_fid, wrld_info = next(iter(worldspaces.items()))
    if wrld_info is None:
        print("  ERROR: no worldspaces found, skipping LOD generation")
        return False

    edid = wrld_info['edid']

    # Measure the extents from the CELLS this worldspace actually contains.
    # WRLD.MNAM is the wrong source on its own: 57 of 84 TES4 worldspaces leave
    # it zeroed, which collapsed the LOD grid to 1x1 and CTD'd on entry (see
    # write_lod_settings).  Cells always carry XCLC, so this is the reliable
    # measure; MNAM is only consulted when it is populated AND wider, so a
    # worldspace whose authored map area exceeds its cells keeps that area.
    grid_xs, grid_ys = [], []
    for c in cells.values():
        if c.get('parent_wrld') != wrld_fid:
            continue
        if c.get('grid_x') is None:
            continue
        grid_xs.append(c['grid_x'])
        grid_ys.append(c['grid_y'])
    if grid_xs:
        # +1: NE is exclusive, a cell at x occupies [x, x+1).
        sw_x, sw_y = min(grid_xs), min(grid_ys)
        ne_x, ne_y = max(grid_xs) + 1, max(grid_ys) + 1
        if wrld_info['ne_x'] > wrld_info['sw_x']:   # MNAM authored — union it
            sw_x = min(sw_x, wrld_info['sw_x'])
            sw_y = min(sw_y, wrld_info['sw_y'])
            ne_x = max(ne_x, wrld_info['ne_x'])
            ne_y = max(ne_y, wrld_info['ne_y'])
        print(f"  LOD extents from {len(grid_xs)} cells: "
              f"SW=({sw_x},{sw_y}) NE=({ne_x},{ne_y})")
    else:
        sw_x, sw_y = wrld_info['sw_x'], wrld_info['sw_y']
        ne_x, ne_y = wrld_info['ne_x'], wrld_info['ne_y']

    _, eff_sw_x, eff_sw_y = write_lod_settings(
        edid, sw_x, sw_y, ne_x, ne_y, output_dir,
    )

    # Ensure Objects output dir exists
    objects_dir = output_dir / 'meshes' / 'terrain' / edid / 'Objects'
    objects_dir.mkdir(parents=True, exist_ok=True)

    # Generate _far.nif LOD meshes for any LOD-flagged objects that don't have one.
    # Only process models that are actually placed in this worldspace.
    # Must happen before writing the LODGen input so the new files are found.
    #
    # When only some tiles survive, a model placed nowhere near them never
    # reaches a shipped tile, so QEM-decimating it is wasted work — the same
    # footprint used for the LODGen input applies here.
    cell_wrld_map = {fid: c['parent_wrld'] for fid, c in cells.items()}
    keep_cells = _kept_tile_cells(only_cells) if only_cells else None
    referenced_models = set()
    for ref in refs:
        pw = ref['parent_wrld']
        if pw != wrld_fid and cell_wrld_map.get(ref['parent_cell'], 0) != wrld_fid:
            continue
        if keep_cells is not None:
            if (int(math.floor(ref['x'] / 4096.0)),
                    int(math.floor(ref['y'] / 4096.0))) not in keep_cells:
                continue
        base_fid = ref['base_fid']
        if base_fid in stats:
            m = stats[base_fid].get('model', '')
            if m:
                referenced_models.add(m)

    # Drop models a MASTER already generated a _far.nif for and reuse that file
    # instead of re-deriving it. QEM-decimating a mesh we already have costs
    # seconds each, and the result is the same file — ElsweyrAnequina rebuilt
    # 882 of Oblivion.esm's 1,173 billboards this way.
    #
    # This is keyed off master_MESH_dirs, not master_dirs: a plugin that owns
    # its worldspace still places its masters' models in it, so mesh reuse
    # applies even though the master ships no tiles for that worldspace.
    # Reuse is only valid when this plugin does NOT override the full model.
    # A child that ships its own rock01.nif needs its OWN rock01_far.nif —
    # the master's was derived from the master's geometry, so reusing it would
    # draw the master's shape in this plugin's distant LOD.
    #
    # Skipped entirely in the `far_nif_dirs` (one-bake) case. That filter asks
    # "does the master already have a _far.nif I can reuse instead of deriving
    # my own?", which only means something when THIS plugin's tree is one of
    # two trees in play. In the standalone-LOD-mod bake there is no "this
    # plugin": every selected plugin contributes, `output_dir` owns no meshes at
    # all, and each model is routed to its own owner and derived there exactly
    # once. Applying it anyway would compare every model against an empty tree,
    # conclude nothing is overridden, and drop from the work list every model
    # any plugin already had a _far.nif for — including ones another plugin
    # overrides with different geometry.
    own_meshes_root = output_dir / 'meshes'
    master_meshes = [Path(d) / 'meshes' for d in (master_mesh_dirs or [])]
    if master_meshes and not far_nif_dirs:
        before = len(referenced_models)
        referenced_models = {
            m for m in referenced_models
            if _overrides_master_model(m, own_meshes_root, master_meshes)
            or not any(_mesh_exists(far_nif_path(m, mm), mm) for mm in master_meshes)
        }
        skipped = before - len(referenced_models)
        if skipped:
            print(f"  Reusing {skipped} master _far.nif LOD mesh(es); "
                  f"generating only this plugin's "
                  f"{len(referenced_models)}")

    _derive_far_meshes(stats, output_dir, referenced_models, far_nif_dirs,
                       overlay_manifest_dirs)

    # Write LOD input (all LOD-flagged objects) and run LODGenx64 once.
    # LODGen resolves every mesh under the single PathData root (output_dir),
    # so only meshes that exist THERE may be listed.
    lodgen_txt = write_lodgen_input(esm_path, output_dir, edid,
                                    _parsed=(worldspaces, cells, stats, refs),
                                    cell_sw=(eff_sw_x, eff_sw_y),
                                    master_dirs=master_dirs,
                                    master_mesh_dirs=master_mesh_dirs,
                                    replace_tiles=bool(only_cells),
                                    only_cells=only_cells)
    ok = False
    if lodgen_txt:
        # Remove stale tiles first: LODGen only rewrites tiles that still have
        # refs, so old (oversized) .bto would otherwise linger.
        stale = list(objects_dir.glob('*.bto'))
        for f in stale:
            f.unlink()
        if stale:
            print(f"  Removed {len(stale)} stale .bto tiles")
        ok = run_lodgen(lodgen_txt, output_dir)

    # An override plugin ships only the tiles its edits touch. LODGen has no
    # per-tile switch and bakes the whole worldspace in one pass, so the
    # unaffected tiles are pruned here instead. They are byte-for-byte what the
    # master already ships, so keeping them would only duplicate the master's
    # LOD and enlarge the plugin for no visual difference.
    if only_cells:
        kept = _prune_unaffected_tiles(objects_dir, '.bto', only_cells)
        print(f"  Kept {kept} .bto tile(s) covering the changed cells; "
              f"the rest are the master's and were pruned")

    # Fill in any LOD texture the .bto files reference but that does not exist:
    # atlas normal maps (synthesized) and any diffuse that lives only in a
    # master's output because this plugin baked the master's models into its LOD.
    _lod_tex_root = textures_root(output_dir)
    _src_tex_roots = [textures_root(Path(d))
                      for d in (master_texture_dirs or master_dirs or [])]
    _fill_missing_lod_textures(objects_dir, _lod_tex_root,
                               master_tex_roots=_src_tex_roots)

    # The master meshes staged for LODGen have served their purpose: the
    # geometry is baked into the .bto and the master ships the mesh itself.
    dropped = _drop_staged_master_meshes()
    if dropped:
        print(f"  Dropped {dropped} master mesh(es) staged for the bake; "
              f"the master already ships them at the same paths")

    if ok:
        print(f"[LOD] Object LOD generation complete.")
    else:
        print(f"[LOD] LOD generation finished with warnings.")
    return ok


def _prune_unaffected_tiles(tile_dir: Path, suffix: str, only_cells) -> int:
    """Delete LOD tiles that cover none of `only_cells`. Returns the kept count.

    Tiles are named `<worldspace>.<level>.<x>.<y><suffix>`, where (x, y) is the
    tile's SW cell corner and it spans `level` cells in each direction. A tile
    is kept when ANY cell it composites was changed — an edit near a tile
    boundary changes the neighbouring tile's edge too, so overlap (not just the
    edited cell's own tile) is the right test.
    """
    only = set(only_cells)
    kept = 0
    for tile in list(tile_dir.glob(f'*{suffix}')):
        parts = tile.name[:-len(suffix)].split('.')
        try:
            level, tx, ty = int(parts[-3]), int(parts[-2]), int(parts[-1])
        except (ValueError, IndexError):
            kept += 1          # unrecognised name: never delete blind
            continue
        if suffix == '.bto' and level not in _OBJ_LOD_LEVELS:
            # A level the reference footprint no longer feeds. Keeping it would
            # ship a tile whose objects were never listed, so it can only be
            # emptier than the master's equivalent.
            tile.unlink()
            continue
        if any((tx + dx, ty + dy) in only
               for dy in range(level) for dx in range(level)):
            kept += 1
        else:
            tile.unlink()
    return kept


def textures_root(plugin_out_dir: Path) -> Path:
    """The plugin's textures directory, whatever case it was created with.

    Different stages have created 'textures' and 'Textures' (Morrowind_ob has
    the capitalised one, Oblivion.esm the lowercase), and this lookup also runs
    on case-sensitive filesystems, so probe rather than assume.
    """
    for name in ('textures', 'Textures'):
        p = plugin_out_dir / name
        if p.is_dir():
            return p
    return plugin_out_dir / 'textures'


_BTO_TEX_RE = _re.compile(rb'[A-Za-z0-9_\\/ .-]{3,200}?\.dds', _re.IGNORECASE)


def _bto_texture_refs(bto_dir: Path) -> set:
    """Texture paths referenced by the .bto tiles, relative to the textures root.

    LODGen writes full paths ('data\\textures\\tes4\\...\\foo.dds'), so a ref
    resolves directly against textures/ — nothing needs copying or renaming.
    """
    refs = set()
    for bto in bto_dir.glob('*.bto'):
        for m in _BTO_TEX_RE.finditer(bto.read_bytes()):
            s = m.group(0).decode('latin-1').lower().replace('/', '\\')
            for prefix in ('data\\textures\\', 'textures\\'):
                if s.startswith(prefix):
                    s = s[len(prefix):]
                    break
            refs.add(s)
    return refs


#: Suffixes a derived map carries, longest first so `_msn` beats `_n`.
_MAP_SUFFIXES = ('_msn', '_em', '_sk', '_n', '_g', '_m', '_s', '_e', '_p')


def _destem_lod_texture(rel: str) -> str:
    """`roadwasteland01_lod_n.dds` -> `roadwasteland01_n.dds`, else ''.

    See: docs/commentary/asset_convert_terrain.md#authored-lod-texture-names
    """
    head, _, name = rel.rpartition('\\')
    if not name.lower().endswith('.dds'):
        return ''
    stem = name[:-4]
    tail = ''
    for suffix in _MAP_SUFFIXES:
        if stem.lower().endswith(suffix):
            tail = stem[-len(suffix):]
            stem = stem[:-len(suffix)]
            break
    for marker in ('_lod', 'lod'):
        if stem.lower().endswith(marker):
            base = stem[:-len(marker)] + tail + '.dds'
            return (head + '\\' + base) if head else base
    return ''


def _copy_lod_destem(rel: str, dest: Path, tex_root: Path,
                     master_tex_roots=None) -> bool:
    """Satisfy a missing `*_lod.dds` from the full-size texture it names.

    See: docs/commentary/asset_convert_terrain.md#authored-lod-texture-names
    """
    base = _destem_lod_texture(rel)
    if not base:
        return False
    src = case_paths.resolve(_tex_roots(tex_root, master_tex_roots), base,
                             'lod_texture')
    if src is None:
        return False
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    except OSError:
        return False
    return True


def _tex_roots(tex_root, master_tex_roots) -> list:
    """This plugin's textures root, then each master's, in order."""
    return [Path(tex_root)] + [Path(m) for m in (master_tex_roots or [])]


def _find_lod_texture(name, tex_root, master_tex_roots):
    """`name` (any case) here, else in a master, else this plugin's own path."""
    hit = case_paths.resolve(_tex_roots(tex_root, master_tex_roots), name,
                             'lod_texture')
    return hit if hit is not None else _win_join(tex_root, name)


def _synth_lod_normal(rel: str, dest: Path, tex_root: Path,
                      master_tex_roots=None) -> bool:
    """Write the `_n` an atlas diffuse needs: its source normal, else flat."""
    stem = rel[:-len('_n.dds')]
    base = stem[:-2] if stem.endswith('_a') else stem
    src_normal = _find_lod_texture(f'{base}_n.dds', tex_root, master_tex_roots)
    diffuse = _find_lod_texture(f'{stem}.dds', tex_root, master_tex_roots)
    if not diffuse.exists():
        diffuse = _find_lod_texture(f'{base}.dds', tex_root, master_tex_roots)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src_normal.exists() and src_normal != dest:
            shutil.copy2(src_normal, dest)
        else:
            _write_flat_normal_for(diffuse, dest)
        return True
    except Exception:
        return False


def _fill_missing_lod_textures(bto_dir: Path, tex_root: Path,
                               master_tex_roots=None):
    """Create the LOD textures the .bto tiles reference but that don't exist.

    Mostly NORMAL maps (`_synth_lod_normal`): LODGen writes each atlas diffuse
    but no matching atlas normal, and object LOD renders unlit against a
    missing _n.  A missing DIFFUSE is an FO3/FNV `_lod` texture Bethesda
    dropped from its BSAs, recovered by `_copy_lod_destem`.

    A plugin can also bake a MASTER's models into its own LOD (Morrowind_ob
    places Oblivion architecture in its worldspace), and those diffuse textures
    live only in the master's output.  A .bto references a texture by PATH, and
    Data holds exactly ONE file per path — the master already ships that path,
    so the child's tiles resolve against the master's copy.  Copying it in
    would duplicate the master's asset byte-for-byte (828 files, 165 MB for
    ElsweyrAnequina) to gain nothing, so a texture a master already ships is
    left to the master and only genuinely absent ones are handled here.
    """
    roots = _tex_roots(tex_root, master_tex_roots)
    missing = sorted(r for r in _bto_texture_refs(bto_dir)
                     if case_paths.resolve(roots, r, 'lod_texture') is None)
    if not missing:
        return

    synth = 0
    unresolved = []
    for rel in missing:
        dest = case_paths.write_path(tex_root, rel)
        if not rel.endswith('_n.dds'):
            if _copy_lod_destem(rel, dest, tex_root, master_tex_roots):
                synth += 1
            else:
                unresolved.append(rel)
            continue
        if _synth_lod_normal(rel, dest, tex_root, master_tex_roots):
            synth += 1
        else:
            unresolved.append(rel)

    if synth:
        print(f"  Synthesized {synth} object-LOD normal maps.")
    if unresolved:
        print(f"  WARNING: {len(unresolved)} LOD textures missing: "
              + ", ".join(unresolved[:5])
              + ("..." if len(unresolved) > 5 else ""))


def _write_flat_normal_for(atlas_diffuse: Path, dest: Path):
    """Write a flat (128,128,255) normal DDS sized to the atlas diffuse."""
    size = 512
    try:
        from PIL import Image
        if atlas_diffuse and atlas_diffuse.exists():
            size = Image.open(atlas_diffuse).size[0]
    except Exception:
        pass
    _ensure_flat_normal_dds(dest, size)


def _ensure_flat_normal_dds(path: Path, size: int):
    """Write an uncompressed flat-normal RGBA DDS (128,128,255,255) of side=size."""
    import numpy as _np
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = _np.zeros((size, size, 4), dtype=_np.uint8)
    arr[:, :, 0] = 128
    arr[:, :, 1] = 128
    arr[:, :, 2] = 255
    arr[:, :, 3] = 255
    # DDS uncompressed A8R8G8B8 header
    hdr = b'DDS ' + struct.pack('<I', 124)
    hdr += struct.pack('<I', 0x1 | 0x2 | 0x4 | 0x1000 | 0x8)   # caps/h/w/pf/pitch
    hdr += struct.pack('<I', size) + struct.pack('<I', size)
    hdr += struct.pack('<I', size * 4)                          # pitch
    hdr += struct.pack('<I', 0) + struct.pack('<I', 0)
    hdr += b'\x00' * 44
    hdr += struct.pack('<II', 32, 0x41)                         # RGB|ALPHAPIXELS
    hdr += struct.pack('<I', 0)                                 # not fourcc
    hdr += struct.pack('<I', 32)                                # bit count
    hdr += struct.pack('<IIII', 0x00ff0000, 0x0000ff00, 0x000000ff, 0xff000000)
    hdr += struct.pack('<I', 0x1000)
    hdr += struct.pack('<IIII', 0, 0, 0, 0)
    # BGRA byte order for A8R8G8B8
    bgra = arr[:, :, [2, 1, 0, 3]].tobytes()
    path.write_bytes(hdr + bgra)


# ---------------------------------------------------------------------------
# CLI entry point (for standalone testing)
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="Generate object LOD for a converted TES5 plugin")
    parser.add_argument('esm', help='Path to converted ESM/ESP')
    parser.add_argument('output_dir', help='Plugin output directory (containing meshes/, textures/)')
    parser.add_argument('--worldspace', default='Tamriel', help='Worldspace EditorID')
    args = parser.parse_args()

    ok = generate_lod(
        Path(args.esm),
        Path(args.output_dir),
        args.worldspace,
    )
    sys.exit(0 if ok else 1)
