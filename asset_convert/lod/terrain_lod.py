"""Terrain LOD generation for TES4→TES5 worldspaces.

Reads LAND records (VHGT heights, VCLR vertex colors, ATXT/VTXT texture layers)
from the converted ESM and produces:
  meshes/terrain/<WRLD>/<WRLD>.<level>.<tx>.<ty>.btr   — heightmap NIF per tile
  textures/terrain/<WRLD>/<WRLD>.<level>.<tx>.<ty>.dds  — per-tile diffuse (DXT1)
  textures/terrain/<WRLD>/<WRLD>.<level>.<tx>.<ty>_n.dds — per-tile normal (flat)

LOD levels generated: 4, 8, 16 (cells per tile side).
Each tile covers level×level cells.

Vertex layout per tile: (level*32+1) × (level*32+1) vertices.
Each cell contributes exactly 33 verts per side (32 intervals), sharing the
boundary vertex with its neighbor, so a level-N tile has N*32+1 verts per side.
Local step = CELL_SIZE / (level*32) so that level verts × step = CELL_SIZE.
Heights are in Skyrim units (1 unit ≈ 1.4 cm).  Cell size = 4096 units.
"""

import mmap
import struct
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from core.worker_budget import worker_count

# Apply all PyFFI patches (time.clock fix, nif.xml condition fixes) before import
from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from asset_convert.lod.terrain_lod_falloutnv import edid_keyed_lod_tiles, resolve_edid_keyed
from asset_convert.lod.terrain_nif import (CELL_SIZE, PYFFI_AVAILABLE,
                                           build_terrain_nif, tile_solid_mask)
from output_layout import assets_for
from asset_convert.texture.dds_codec import (
    TEX_SIZE,
    write_dds_dxt1,
    write_normal_dds,
)
from tes5_import.base.tes5_reader import (GRP_TOP,
                                     GRP_WORLD_CHILDREN,
                                     header_end, read_group,
                                     read_record, records, walk)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VERTS_SIDE  = 33       # vertices per cell side in TES5 LAND records (32 intervals)
DELTA_SCALE = 8.0      # each int8 delta = 8 Skyrim units of height

# ---------------------------------------------------------------------------
# Plugin byte cache
_RAW_CACHE_KEY = None
_RAW_CACHE_BUF = None


def _plugin_bytes(esm_path: Path) -> bytes:
    """Read a plugin's bytes, reusing the last file read (keyed on mtime+size)."""
    global _RAW_CACHE_KEY, _RAW_CACHE_BUF
    esm_path = Path(esm_path)
    try:
        st = esm_path.stat()
        key = (str(esm_path).lower(), st.st_mtime_ns, st.st_size)
    except OSError:
        return esm_path.read_bytes()
    if key == _RAW_CACHE_KEY and _RAW_CACHE_BUF is not None:
        return _RAW_CACHE_BUF
    # Drop the old buffer BEFORE reading the new one so the two never coexist.
    _RAW_CACHE_KEY = None
    _RAW_CACHE_BUF = None
    buf = esm_path.read_bytes()
    _RAW_CACHE_KEY = key
    _RAW_CACHE_BUF = buf
    return buf


def _drop_plugin_bytes():
    """Release the cached plugin buffer (call before spawning worker pools)."""
    global _RAW_CACHE_KEY, _RAW_CACHE_BUF
    _RAW_CACHE_KEY = None
    _RAW_CACHE_BUF = None
    _WRLD_FID_CACHE.clear()


# (path, mtime, size, edid) -> raw WRLD FormID or None. `find_worldspace_fid`
# is a full linear scan of the plugin, and `parse_land_records` ran it twice
# for the same (file, edid) on every worldspace: once to resolve the defining
# plugin's id, then again inside `scan_land_file`. Measured 0.64 s of the 2.36 s
# parse on Oblivion.esm. Cleared with the byte cache so a rebuilt plugin cannot
# serve a stale id.
_WRLD_FID_CACHE: dict = {}


def _worldspace_fid_cached(esm_path: Path, raw: bytes, edid: str):
    """`find_worldspace_fid` memoised per (plugin file, worldspace EditorID)."""
    try:
        st = Path(esm_path).stat()
        key = (str(esm_path).lower(), st.st_mtime_ns, st.st_size, edid)
    except OSError:
        return find_worldspace_fid(raw, len(raw), edid)
    if key in _WRLD_FID_CACHE:
        return _WRLD_FID_CACHE[key]
    val = find_worldspace_fid(raw, len(raw), edid)
    _WRLD_FID_CACHE[key] = val
    return val

LOD_LEVELS  = [4, 8, 16, 32]

# Diffuse DDS size per LOD level.  A level-N tile spans N cells; it is only ever
# viewed at distance, so per-cell texel density can stay low.  The old flat
# 1024/2048 gave every one of the ~960 LOD4 tiles a 683KB diffuse + 1.4MB normal
# => 3GB.  Scaling with level (roughly constant texels/cell) keeps quality where
# it's seen and cuts the total ~8x.
TEX_SIZE_BY_LEVEL = {4: 256, 8: 512, 16: 1024, 32: 2048}

# Normal maps carry far less perceptible detail than diffuse at LOD distance,
# so bake them at half the diffuse resolution (BC5 is 2x DXT1 per texel, so this
# is the single biggest size win).
NORMAL_SIZE_DIVISOR = 2

# ---------------------------------------------------------------------------
# LAND record parsing
# ---------------------------------------------------------------------------

def find_worldspace_fid(raw: bytes, n: int, edid: str):
    """FormID of the WRLD named `edid`, or None.

    Scoped to the top-level WRLD block, which holds every WRLD in the file,
    and skips each worldspace's children -- so it never touches the ~1.17M
    cell records behind them.  `n` is accepted for call compatibility.
    """
    for rec in records(raw, b'WRLD'):
        if rec.string(b'EDID') == edid:
            return rec.form_id
    return None


def detect_terrain_worldspaces(esm_path: Path, include_children: bool = False):
    """ROOT worldspaces in an ESM, largest first.

    Filenames don't tell us the worldspace EditorID (Oblivion.esm's worldspace
    is 'TES4Tamriel'; Nehrim.esm's is 'NehrimWorldspace', not 'Nehrim'), so the
    WRLD records have to be read.

    A child worldspace whose PNAM borrows the parent's LOD renders inside the
    parent's terrain grid, so it is excluded by default; a child that keeps
    its own LOD (FNV's Freeside, the Strip) is listed like a root. Pass
    include_children=True to list every worldspace.
    See: docs/commentary/asset_convert_terrain.md#child-worldspaces-with-their-own-lod

    Reads ONLY the top-level WRLD block: the WRLD records themselves and the
    size of each one's child group. It deliberately does NOT walk into cell
    children to count LAND records. That count was pure ranking information,
    and paying for it meant stepping through 1.17 MILLION records in Python —
    0.42s for Oblivion.esm and ~1.6s across a load order, which is a visible
    UI stall. A worldspace with no terrain simply produces no tiles, so there
    was never anything to gate on. Reading the headers alone is ~0.001s.

    Returns [(size_hint, wrld_fid, edid), ...] sorted largest-first, where
    size_hint is the byte size of the worldspace's child group — a monotonic
    stand-in for "how much is in it", used only for display order.
    """
    fh = open(esm_path, 'rb')
    try:
        try:
            raw = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        except (ValueError, OSError):
            raw = fh.read()      # empty file, or a filesystem that won't map
        try:
            return _scan_wrld_block(raw, include_children)
        finally:
            if isinstance(raw, mmap.mmap):
                raw.close()
    finally:
        fh.close()


def _top_wrld_group(raw):
    """The top-level GRUP labelled 'WRLD', or None.

    Every WRLD record in a file lives inside it, so nothing else need be read.
    """
    p = header_end(raw)
    while p + 24 <= len(raw):
        group = read_group(raw, p)
        if group is None or group.end <= p:
            return None
        if group.type == GRP_TOP and group.label_sig == b'WRLD':
            return group
        p = group.end
    return None


#: WRLD PNAM bit: the child renders inside its parent's LOD grid.
PARENT_USE_LOD_DATA = 0x02


def _borrows_parent_lod(rec) -> bool:
    """True when this child worldspace renders inside its parent's LOD grid.

    A WRLD carrying no PNAM predates the importer emitting one and keeps the
    old reading.
    See: docs/commentary/asset_convert_terrain.md#child-worldspaces-with-their-own-lod
    """
    pnam = rec.sub(b'PNAM')
    if not pnam or len(pnam) < 2:
        return True
    return bool(struct.unpack_from('<H', pnam)[0] & PARENT_USE_LOD_DATA)


def _scan_wrld_block(raw, include_children: bool):
    """The WRLD-block reader behind `detect_terrain_worldspaces`."""
    if len(raw) < 24:
        return []

    edid_by_fid: dict = {}
    parent_by_fid: dict = {}
    size_by_fid: dict = {}

    block = _top_wrld_group(raw)
    if block is None:
        return []

    q, cur = block.start + 24, None
    while q + 24 <= block.end:
        group = read_group(raw, q)
        if group is not None:
            if group.end <= q:
                break
            if group.type == GRP_WORLD_CHILDREN:
                owner = group.label_fid or cur
                if owner is not None:
                    size_by_fid[owner] = (size_by_fid.get(owner, 0)
                                          + group.end - group.start)
            q = group.end
            continue
        rec, nxt = read_record(raw, q, block.end)
        if rec is None:
            break
        if rec.sig == b'WRLD':
            edid = rec.string(b'EDID')
            if edid:
                edid_by_fid[rec.form_id] = edid
            wnam = rec.sub(b'WNAM')
            if wnam and len(wnam) >= 4 and _borrows_parent_lod(rec):
                parent_by_fid[rec.form_id] = struct.unpack_from('<I', wnam)[0]
            cur = rec.form_id
        q = nxt

    ranked = []
    for fid in edid_by_fid:
        parent = parent_by_fid.get(fid, 0)
        if parent and not include_children:
            continue
        ranked.append((size_by_fid.get(fid, 0), fid,
                       edid_by_fid.get(fid, f'{fid:08X}')))
    ranked.sort(key=lambda t: (-t[0], t[2].lower()))
    return ranked


def _master_record_dir(export_dir, master: str):
    """Where `master`'s records live, given any plugin's record folder.

    Masters used to resolve as `export_dir.parent / master`. That holds only
    while every plugin owns a top-level folder; an imported mod's plugins are
    nested inside their mod's folder, making `.parent` the mod rather than the
    export root.
    """
    import os as _os
    from pathlib import Path as _Path
    d = _Path(export_dir).parent
    root = d
    for cand in (d, d.parent):
        if cand and (cand / 'sources.json').is_file():
            root = cand
            break
    try:
        from output_layout import record_dir as _rd
        got = _rd(root, master)
        if _os.path.isdir(got):
            return got
    except ImportError:
        pass
    return root / master


def master_names(export_dir: Path):
    """The TES4 master file names listed in an export's _HEADER.txt."""
    header = Path(export_dir) / '_HEADER.txt'
    if not header.is_file():
        return []
    names = []
    for line in header.read_text(encoding='utf-8', errors='replace').splitlines():
        if line.startswith('Master['):
            _, _, val = line.partition('=')
            val = val.strip()
            if val:
                names.append(val)
    return names


def _scan_cell_coords(esm_path: Path, coords: dict):
    """Collect {cell FormID -> (x, y)} from every CELL carrying XCLC.

    Keys are NORMALIZED load-order-wide FormIDs. Every caller accumulates
    several files into one `coords` dict, and a raw id is meaningful only inside
    the file it came from — the index byte indexes THAT file's master list. Two
    plugins' 02s routinely name unrelated records, so raw keys let one plugin's
    cell silently inherit another's grid coordinates.
    """
    from asset_convert.lod.esm_scan import formid_remap_table
    gmap = formid_remap_table(Path(esm_path))
    for rec in records(esm_path.read_bytes(), b'CELL'):
        xclc = rec.sub(b'XCLC')
        if xclc and len(xclc) >= 8:
            fid = gmap[rec.form_id >> 24] | (rec.form_id & 0x00FFFFFF)
            coords[fid] = struct.unpack_from('<2i', xclc)


def lod_capable_worldspaces(export_dir: Path, out_root: Path = None,
                            plugin: str = None):
    """Every worldspace a plugin should get distant LOD for.

    The authority is the SOURCE GAME's own shipped LOD assets. Whoever built
    the plugin decided which worldspaces are seen from a distance and baked
    LOD for exactly those; that judgement is authored data, and it is better
    than anything we can infer. Measured over the whole load order: of the 20
    worldspaces with shipped LOD, every single one also contains LOD-flagged
    references, so the list has no false positives at all.

    Inferring it instead was tried and is worse in both directions. Deriving
    the set from the converted ESM's WRLD records offers every worldspace that
    merely HAS terrain, which pulls in Bethesda's debug worlds (TestGatekeeper,
    TestShambles, CubeWorldspace ...) -- they bake no object tiles, and
    `generate_lod` returning False for them dragged an otherwise clean
    84-worldspace run down to "create_lod: FAILED for all plugins". Filtering
    THAT list back down needs a full reference scan of every ESM, which is
    seconds of work to reconstruct a fact the export already states for free.

    A plugin shipping no LOD assets is not a plugin whose worldspaces were
    missed. City worldspaces are folded into their parent's LOD grid (a WNAM
    parent, which `detect_terrain_worldspaces` already excludes), and an ESP
    that extends a master's landmass ships LOD for the MASTER's worldspace:
    ElsweyrPelletine.esp supplies TES4Tamriel tiles, not tiles of its own.

    `out_root` is accepted for call compatibility and used only to tell a
    missing conversion apart from a missing export in the reason text.

    Returns (worldspaces, reason); `reason` is None unless nothing qualified.
    A reason is a WARNING for the caller to surface, not an error: the usual
    cause is a deleted or never-run export.
    """
    export_dir = Path(export_dir)
    # The messages below tell the user to re-run `convert.py -f <name>`, so it
    # must be the PLUGIN. A single-plugin imported mod keeps its records in the
    # mod's folder, whose name is the mod label and not a valid -f argument.
    name = plugin or export_dir.name

    if not export_dir.is_dir():
        return [], (f"{name}: no export folder — run the Export stage "
                    f"(convert.py -f {name} --export-only).")

    try:
        shipped = shipped_lod_worldspaces(export_dir) or []
    except Exception as exc:
        return [], f"{name}: could not read the export's LOD assets ({exc})."

    if shipped:
        return shipped, None

    # No shipped LOD. Distinguish "this plugin genuinely has none" from "the
    # assets that would have told us were deleted", because the two look
    # identical from here and only one of them is the user's problem.
    _assets = assets_for(export_dir)
    lod_dirs = [_assets / 'meshes' / 'landscape' / 'lod',
                _assets / 'textures' / 'landscapelod' / 'generated']
    if not any(d.is_dir() for d in lod_dirs):
        return [], (f"{name}: the export has no landscape-LOD folders. If you "
                    f"deleted them, re-run the Extract stage "
                    f"(convert.py -f {name} --extract-only); otherwise this "
                    f"plugin simply ships no distant LOD of its own.")
    return [], (f"{name}: ships no distant LOD of its own — its worldspaces "
                f"are covered by whichever plugin does (a city world renders "
                f"on its parent's LOD grid).")


def worldspace_edids(export_dir):
    """{FormID: EditorID} from this export's WRLD.txt and every master's.

    Masters are scanned too, and resolved through the registry rather than as
    sibling directories.

    See: docs/commentary/asset_convert_terrain.md#why-the-wrld-scan-includes-masters
    """
    edid_by_fid = {}

    def _scan(directory):
        """Fold one record directory's WRLD EditorIDs into the map."""
        wrld_txt = directory / 'WRLD.txt'
        if not wrld_txt.is_file():
            return
        cur_fid = None
        for line in wrld_txt.read_text(encoding='utf-8',
                                       errors='replace').splitlines():
            if line.startswith('FormID='):
                try:
                    cur_fid = int(line[7:].strip(), 16)
                except ValueError:
                    cur_fid = None
            elif line.startswith('EditorID=') and cur_fid is not None:
                edid_by_fid.setdefault(cur_fid, line[9:].strip())

    _scan(export_dir)
    for master in master_names(export_dir):
        _scan(_master_record_dir(export_dir, master))
    return edid_by_fid


def shipped_lod_worldspaces(export_dir: Path):
    """Return the worldspace EditorIDs the SOURCE game shipped distant-LOD for.

    Oblivion/Nehrim generate distant LOD offline and ship it as assets keyed by
    the worldspace's *decimal* FormID:
        meshes\\landscape\\lod\\<formid>.<x>.<y>.<level>.nif   (object LOD)
        textures\\landscapelod\\generated\\<formid>.*.dds       (LOD textures)
    (Oblivion also ships DistantLOD\\<edid>_<x>_<y>.lod terrain files, but the
    extract step drops those; the mesh/texture prefixes are an equivalent, and
    already-extracted, signal.)

    We treat "the source shipped LOD for it" as the authority on which
    worldspaces deserve LOD — that is exactly the vanilla set and it naturally
    excludes child worldspaces (Anvil/Bravil/… inside Tamriel, whose overlay
    LAND records never got their own LOD).

    Scans the extract dir's LOD folders for the decimal-FormID prefixes, maps
    them back to EditorIDs via the export's WRLD.txt, and returns
        [(edid, tes4_formid), ...]  sorted by descending shipped-tile count.
    """
    export_dir = Path(export_dir)

    # 1. Collect decimal FormID prefixes from shipped LOD assets.
    from collections import Counter
    counts = Counter()
    for sub in ('meshes/landscape/lod', 'textures/landscapelod/generated'):
        d = export_dir / sub
        if not d.is_dir():
            continue
        for f in d.iterdir():
            head = f.name.split('.', 1)[0]
            if head.isdigit():
                counts[int(head)] += 1
    by_edid = edid_keyed_lod_tiles(export_dir)
    if not counts and not by_edid:
        return []

    edid_by_fid = worldspace_edids(export_dir)

    # The importer renames Oblivion's 'Tamriel' worldspace to 'TES4Tamriel'
    # (tes5_import/record_types/world.py) so it doesn't override Skyrim's
    # Tamriel. LOD generation looks worldspaces up by EDID in the CONVERTED
    # ESM, so return the post-rename name to match.
    def _converted_edid(name):
        return 'TES4Tamriel' if name == 'Tamriel' else name

    result = [(_converted_edid(edid_by_fid.get(fid, f'{fid:08X}')), fid)
              for fid in counts]
    result.sort(key=lambda t: -counts[t[1]])
    return result + resolve_edid_keyed(by_edid, edid_by_fid, _converted_edid,
                                        {fid for _e, fid in result})


def parse_land_records(esm_path: Path, worldspace_edid: str = 'TES4Tamriel',
                        overlay_paths=None, deleted=None):
    """Parse LAND + CELL water data for one worldspace from the output ESM.

    `overlay_paths` are plugins to apply ON TOP, in load order; everything is
    keyed by grid coordinate, so a later file's LAND replaces the earlier one.

    Returns (lands, cell_water, default_water_height):
      lands:      (cell_x, cell_y) -> {heights: ndarray(33,33 float32),
                                       colors:  ndarray(33,33,3 uint8),
                                       layers:  BTXT/ATXT/VTXT dict}
      cell_water: (cell_x, cell_y) -> (has_water: bool, height: float or None)
                  height is the cell XCLW override; None = use worldspace default
      default_water_height: WRLD DNAM default water height (0.0 if absent)
    See: docs/commentary/asset_convert_terrain.md#land-scan-scoping
    """
    lands = {}
    cell_water = {}
    wrld_water = {'default': None}
    cell_coords = {}       # cell FormID -> (x, y), shared across load order

    try:
        from asset_convert.lod.esm_scan import formid_remap_table
        _base_raw = _plugin_bytes(Path(esm_path))
        _raw_fid = _worldspace_fid_cached(Path(esm_path), _base_raw,
                                          worldspace_edid)
        del _base_raw
        if _raw_fid is None:
            base_wrld_fid = None
        else:
            _bmap = formid_remap_table(Path(esm_path))
            base_wrld_fid = _bmap[_raw_fid >> 24] | (_raw_fid & 0x00FFFFFF)
    except OSError:
        base_wrld_fid = None

    for _i, _path in enumerate([esm_path] + list(overlay_paths or [])):
        scan_land_file(Path(_path), worldspace_edid, lands, cell_water,
                        wrld_water, cell_coords,
                        allow_unscoped=(_i == 0),
                        known_wrld_fid=base_wrld_fid, deleted=deleted)
    default_wh = (wrld_water['default']
                  if wrld_water['default'] is not None else 0.0)
    return lands, cell_water, default_wh


def scan_land_file(esm_path: Path, worldspace_edid: str,
                    lands: dict, cell_water: dict, wrld_water: dict,
                    cell_coords: dict, count_only: bool = False,
                    allow_unscoped: bool = True, known_wrld_fid=None,
                    deleted=None):
    """Scan one plugin's LAND/CELL/WRLD data into the shared accumulators.

    `known_wrld_fid` scopes the scan to a worldspace an override edits without
    defining; `allow_unscoped` decides whether an unresolvable worldspace takes
    every LAND record (True, for the defining file) or none (False, mandatory
    for overlays).  Every FormID is normalized into the load-order-wide space
    first, since the accumulators are shared across the whole stack.
    See: docs/commentary/asset_convert_terrain.md#land-scan-scoping
    """
    raw = _plugin_bytes(Path(esm_path))
    n   = len(raw)

    from asset_convert.lod.esm_scan import formid_remap_table
    _gmap = formid_remap_table(Path(esm_path))

    def g(fid: int) -> int:
        return _gmap[fid >> 24] | (fid & 0x00FFFFFF)

    _found = _worldspace_fid_cached(esm_path, raw, worldspace_edid)
    target_wrld_fid = None if _found is None else g(_found)
    if target_wrld_fid is not None:
        print(f"  Filtering to worldspace '{worldspace_edid}' (FormID={target_wrld_fid:#010x})")
    elif known_wrld_fid is not None:
        target_wrld_fid = known_wrld_fid
        print(f"  Scoping {esm_path.name} to '{worldspace_edid}' via the "
              f"defining plugin (FormID={target_wrld_fid:#010x})")
    elif allow_unscoped:
        print(f"  WARNING: Worldspace '{worldspace_edid}' not found — collecting all LAND records")
    else:
        print(f"  '{worldspace_edid}' not defined by {esm_path.name} and no "
              f"FormID known; taking no LAND from it")
        return

    def prune(group, _stack) -> bool:
        """Skip a type-1 GRUP belonging to a worldspace we do not want."""
        return (target_wrld_fid is not None
                and group.type == GRP_WORLD_CHILDREN
                and g(group.label_fid) != target_wrld_fid)

    def scoped(stack) -> bool:
        """True when this record sits in the worldspace being collected."""
        if target_wrld_fid is None:
            return True
        wrld = stack.worldspace
        return wrld is not None and g(wrld) == target_wrld_fid

    for rec, stack in walk(raw, b'CELL', b'WRLD', b'LAND', prune=prune):
        fid = g(rec.form_id)
        if rec.sig == b'CELL':
            _take_cell(rec, fid, cell_coords,
                       cell_water if scoped(stack) else None)
        elif rec.sig == b'WRLD':
            if target_wrld_fid is not None and fid == target_wrld_fid:
                dnam = rec.sub(b'DNAM')
                if dnam and len(dnam) >= 8:
                    wrld_water['default'] = struct.unpack_from('<f', dnam, 4)[0]
        elif rec.sig == b'LAND' and scoped(stack):
            cell = stack.cell
            coords = cell_coords.get(None if cell is None else g(cell))
            if coords is not None:
                _take_land(rec, coords, lands, count_only, allow_unscoped, g,
                           deleted)


def _take_cell(rec, fid: int, cell_coords: dict, cell_water) -> None:
    """Learn a CELL's grid coords, and its water when `cell_water` is given.

    An OVERRIDE's CELL carries only the fields its author changed, so XCLC is
    usually absent and its coords are the master's, learned earlier in load
    order; one saying nothing about water keeps what the master established.
    `cell_water` is None outside the target worldspace, where the coords are
    still worth learning.
    """
    xclc = rec.sub(b'XCLC')
    if xclc and len(xclc) >= 8:
        coords = struct.unpack_from('<2i', xclc)
        cell_coords[fid] = coords
    else:
        coords = cell_coords.get(fid)
    if coords is None or cell_water is None:
        return
    data = rec.sub(b'DATA')
    if data is None and coords in cell_water:
        return
    flags = 0
    if data:
        flags = data[0] | (data[1] << 8 if len(data) >= 2 else 0)
    wh = None
    xclw = rec.sub(b'XCLW')
    if xclw and len(xclw) >= 4:
        v = struct.unpack_from('<f', xclw)[0]
        if -1e9 < v < 1e9:
            wh = v
    cell_water[coords] = (bool(flags & 0x02), wh)


def _take_land(rec, coords, lands: dict, count_only: bool,
               allow_unscoped: bool, remap, deleted=None) -> None:
    """Decode one LAND into `lands`, or erase the cell it deletes.

    An OVERLAY's LAND with no VHGT is the author DELETING that cell's terrain
    (overlays only); it is erased and added to `deleted`, so no baked LOD
    refills it. `count_only` stores presence as a real parse would; `remap`
    re-stamps the layer LTEX ids into load-order space.
    See: docs/commentary/asset_convert_terrain.md#no-terrain-without-an-authored-source
    """
    if count_only:
        if rec.sub(b'VHGT') is not None:
            lands[coords] = True
        return
    land = _decode_land(rec.body, lambda b, t: rec.sub(t.encode()), remap)
    if land is not None:
        lands[coords] = land
    elif not allow_unscoped:
        lands.pop(coords, None)
        if deleted is not None:
            deleted.add(coords)


def _decode_land(body, _sub, remap=None):
    """Decode VHGT → heights (33×33), VCLR → colors (33×33,3), layers."""
    vhgt = _sub(body, 'VHGT')
    if vhgt is None or len(vhgt) < 4 + VERTS_SIDE * VERTS_SIDE:
        return None

    # VHGT format (UESP wiki / xEdit confirmed):
    #   Offset: float — starting accumulator value in "delta units"
    #   delta[row][col]: int8 — cumulative delta; row start comes from delta[row][0]
    #
    # Accumulation (all in delta units, i.e. 1 unit = DELTA_SCALE game units):
    #   current = Offset
    #   for row 0..32:
    #       current += delta[row][0]       ← first column updates the accumulator
    #       row_start = current
    #       for col 1..32:
    #           current += delta[row][col]
    #           h[row][col] = current * DELTA_SCALE
    #       h[row][0] = row_start * DELTA_SCALE
    #
    # xEdit confirms: to shift terrain by ShiftZ game units,
    #   Offset += ShiftZ / DELTA_SCALE  → Offset is in delta units.
    vhgt_offset = struct.unpack_from('<f', vhgt, 0)[0]
    deltas = np.frombuffer(vhgt[4:4 + VERTS_SIDE*VERTS_SIDE], dtype=np.int8
                           ).reshape(VERTS_SIDE, VERTS_SIDE)

    # Vectorised form of the two nested accumulations described above.  The
    # scalar version ran 33x33 = 1,089 Python iterations per LAND record, and
    # Tamriel has tens of thousands of them — it dominated the SERIAL LAND
    # parse that runs before the terrain-LOD tile pool starts (~18x faster
    # here).
    #
    # Both accumulations are plain prefix sums, and they are done in INT32:
    #   row starts   = cumsum(delta[:, 0])        (down column 0)
    #   within a row = row_start + cumsum(...)    (across, seeded at column 0)
    # The deltas are int8, so integer prefix sums are EXACT — there is no
    # accumulation-order rounding to reproduce, which a float32 cumsum could
    # not have matched against the scalar loop's mixed float32/Python-float
    # accumulator anyway.  The single float conversion happens at the end, so
    # each height is (offset + integer) * DELTA_SCALE computed once.
    steps = deltas.astype(np.int32)
    steps[:, 0] = np.cumsum(steps[:, 0])          # row starts, exact
    np.cumsum(steps, axis=1, out=steps)           # across, seeded by column 0
    heights = ((steps + np.float32(vhgt_offset)) * DELTA_SCALE
               ).astype(np.float32)

    vclr = _sub(body, 'VCLR')
    if vclr and len(vclr) >= VERTS_SIDE * VERTS_SIDE * 3:
        colors = np.frombuffer(vclr[:VERTS_SIDE*VERTS_SIDE*3], dtype=np.uint8
                               ).reshape(VERTS_SIDE, VERTS_SIDE, 3).copy()
    else:
        colors = np.full((VERTS_SIDE, VERTS_SIDE, 3), 255, dtype=np.uint8)

    # Full per-quadrant texture layer structure (BTXT/ATXT/VTXT) for the
    # diffuse compositor.  decode_land_layers takes the raw record body.
    from asset_convert.lod.terrain_lod_textures import decode_land_layers
    layers = decode_land_layers(body, remap)

    return {'heights': heights, 'colors': colors, 'layers': layers}


# ---------------------------------------------------------------------------
# Tile assembly
# ---------------------------------------------------------------------------

def _assemble_tile(lands, tile_x, tile_y, level, synthetic=frozenset()):
    """Merge level x level cells into ((level*32+1)^2) height + colour grids.

    tile_x, tile_y: the tile's SW cell. Cell (cx, cy) fills rows cy*32..+32
    and columns cx*32..+32, sharing its boundary with each neighbour; cells in
    `synthetic` are written first so a real LAND wins every shared vertex.
    Missing cells are edge-extended from the nearest real row/column.
    Returns (heights (tv,tv) float32, colors (tv,tv,3) uint8), tv = level*32+1.
    See: docs/commentary/asset_convert_terrain.md#no-terrain-without-an-authored-source
    """
    tv = level * 32 + 1
    out_h = np.full((tv, tv), np.nan, dtype=np.float32)
    out_c = np.full((tv, tv, 3), 100, dtype=np.uint8)
    cells = sorted(((cx, cy) for cy in range(level) for cx in range(level)),
                   key=lambda c: (tile_x + c[0], tile_y + c[1]) not in synthetic)
    for cx, cy in cells:
        land = lands.get((tile_x + cx, tile_y + cy))
        if land is None:
            continue
        out_h[cy*32:cy*32+33, cx*32:cx*32+33] = land['heights']
        out_c[cy*32:cy*32+33, cx*32:cx*32+33] = land['colors']
    if np.any(np.isnan(out_h)):
        fill_missing(out_h, out_c)
    return out_h, out_c


def fill_missing(h: np.ndarray, c: np.ndarray):
    """In-place fill of NaN cells in h (and corresponding rows in c) by
    edge-extending from the nearest valid row/column.

    Strategy:
      1. For each column, forward-fill NaN rows downward from the first valid row,
         then backward-fill upward from the last valid row.
      2. If an entire column is NaN, copy from the nearest non-NaN column.
    """
    tv = h.shape[0]
    nan = np.isnan(h)
    if not nan.any():
        return
    valid = ~nan
    rows = np.arange(tv)
    cols = np.arange(tv)

    # Step 1: per-column forward fill, then backward fill for anything the
    # forward pass could not reach.  Both are running-extremum scans over the
    # row axis, so they vectorise across all columns at once — the old
    # row×column Python loop was ~1.4s on a LOD32 tile.
    ff = np.maximum.accumulate(np.where(valid, rows[:, None], -1), axis=0)
    bf = np.minimum.accumulate(np.where(valid, rows[:, None], tv)[::-1],
                               axis=0)[::-1]
    src_row = np.where(ff >= 0, ff, bf)

    col_has = valid.any(axis=0)
    fill = nan & col_has[None, :]          # columns with at least one real value
    if fill.any():
        src_row = np.clip(src_row, 0, tv - 1)
        h[fill] = h[src_row, cols[None, :]][fill]
        c[fill] = c[src_row, cols[None, :]][fill]

    # Step 2: columns that are entirely NaN take the nearest valid column
    # (ties go left, matching the old left-then-right search).
    if not col_has.all():
        valid_cols = np.where(col_has)[0]
        if len(valid_cols):
            nearest = valid_cols[np.abs(cols[:, None]
                                        - valid_cols[None, :]).argmin(axis=1)]
            empty = ~col_has
            h[:, empty] = h[:, nearest[empty]]
            c[:, empty] = c[:, nearest[empty]]

    # Fallback: any remaining NaN → 0
    nan_mask = np.isnan(h)
    if nan_mask.any():
        h[nan_mask] = 0.0


# ---------------------------------------------------------------------------
# LOD water (vanilla-style)
# ---------------------------------------------------------------------------

def _cell_water_height(cell_water, key, default_wh):
    """Water height for a cell, or None if the cell has no water."""
    cw = cell_water.get(key)
    if cw is None or not cw[0]:
        return None
    return cw[1] if cw[1] is not None else default_wh


def _tile_water_quads(lands, cell_water, tile_x, tile_y, level, default_wh):
    """Return [(cx, cy, water_height_world), ...] for cells in this tile that
    need a LOD water quad (cell has water and its terrain dips below the water
    surface), matching how vanilla terrain LOD only carries water quads where
    water is actually visible.  cx/cy are cell offsets within the tile."""
    quads = []
    for cx in range(level):
        for cy in range(level):
            key = (tile_x + cx, tile_y + cy)
            wh = _cell_water_height(cell_water, key, default_wh)
            if wh is None:
                continue
            land = lands.get(key)
            if land is not None and float(land['heights'].min()) >= wh:
                continue   # terrain entirely above water in this cell
            quads.append((cx, cy, wh))
    return quads


# ---------------------------------------------------------------------------
# DDS writing (DXT1 via PIL/Pillow or pure-Python fallback)
# ---------------------------------------------------------------------------
# Diffuse tile compositing + heightmap normal maps
# ---------------------------------------------------------------------------

# Per-cell pixel resolution when compositing the diffuse atlas.  A level-N tile
# is N cells per side, so the atlas is N*CELL_DIFFUSE_PX per side; clamped to the
# per-level TEX_SIZE on write.
CELL_DIFFUSE_PX = 64


_EMPTY_LAYERS = {'base': {}, 'alpha': {}}

# Per-cell composited diffuse cache, shared by every tile a worker builds.
# A cell appears in one tile per LOD level (4 levels), so caching removes most
# of the ~70,000 composite_cell calls a Tamriel run makes for 14,686 cells.
# Each entry is CELL_DIFFUSE_PX² × 3 bytes (12 KB at 64px); the cap simply
# bounds a long-lived worker rather than targeting a memory budget.
_CELL_IMG_CACHE = {}
_CELL_IMG_CACHE_MAX = 16384


def _bake_tile(baked_tiles, key):
    """The path of the baked tile holding cell `key`, or None."""
    from asset_convert.lod import terrain_lod_baked as tb
    if not baked_tiles:
        return None
    return baked_tiles.get(tuple((c // tb.TILE_CELLS) * tb.TILE_CELLS
                                 for c in key))


def _baked_crop(baked_tiles, key, layers, offsets=None):
    """The game's own LOD image of cell `key` when a quadrant is unpainted, else None.

    `baked_tiles` is {(tile x, tile y): path} of 32-cell south-up tiles;
    `offsets` (from `_filler_offsets`) shift it toward the painted composite.
    See: docs/commentary/asset_convert_terrain.md#terrain-lod-filler
    """
    from asset_convert.lod import terrain_lod_baked as tb
    from asset_convert.lod.terrain_lod_textures import unpainted_quad
    path = _bake_tile(baked_tiles, key)
    if path is None or not any(unpainted_quad(layers, q) for q in range(4)):
        return None
    tile = tuple((c // tb.TILE_CELLS) * tb.TILE_CELLS for c in key)
    crop = tb.cell_crop(tb.load_tile_rgb(str(path)), key[0] - tile[0],
                        key[1] - tile[1], CELL_DIFFUSE_PX)
    if not offsets:
        return crop
    shift = tb.offset_image(offsets, key, CELL_DIFFUSE_PX)
    return np.clip(crop + shift, 0, 255).astype(np.uint8)


def _cell_image(lands, key, ctx, h33, wh):
    """One cell's composited diffuse, cached per (cell, water, height patch).

    `ctx` is (ltex_map, tex_root, baked_tiles, offsets). A cell recurs in one
    tile per LOD level; the key carries the only per-tile inputs.
    """
    from asset_convert.lod.terrain_lod_textures import composite_cell, vclr_tint
    ltex_map, tex_root, baked_tiles, offsets = ctx
    ck = (key, wh, h33.tobytes(), vclr_tint())
    img = _CELL_IMG_CACHE.get(ck)
    if img is not None:
        return img
    land = lands.get(key)
    layers = land['layers'] if land is not None else _EMPTY_LAYERS
    colors = land.get('colors') if land is not None else None
    img = composite_cell(layers, colors, ltex_map, tex_root, key[0], key[1],
                         cell_px=CELL_DIFFUSE_PX, tex_size=128, heights=h33,
                         water_height=wh,
                         baked=_baked_crop(baked_tiles, key, layers, offsets))
    if len(_CELL_IMG_CACHE) >= _CELL_IMG_CACHE_MAX:
        _CELL_IMG_CACHE.clear()
    _CELL_IMG_CACHE[ck] = img
    return img


def _quad_states(land, baked_tiles, key) -> np.ndarray:
    """[[TL, TR], [BL, BR]] of cell `key`: 1 painted, 0 baked filler, -1 neither."""
    from asset_convert.lod.terrain_lod_textures import unpainted_quad
    layers = land['layers'] if land is not None else _EMPTY_LAYERS
    baked = _bake_tile(baked_tiles, key) is not None

    def one(q):
        """The state of quadrant `q`."""
        if not unpainted_quad(layers, q):
            return 1
        return 0 if baked else -1
    return np.array([[one(2), one(3)], [one(0), one(1)]], dtype=np.int8)


def _place(canvas, state, at, img, quads) -> None:
    """Write a cell image and its 2x2 quadrant states at canvas cell (row, col) `at`."""
    r, c = at
    px = CELL_DIFFUSE_PX
    canvas[r * px:(r + 1) * px, c * px:(c + 1) * px] = img
    state[2 * r:2 * r + 2, 2 * c:2 * c + 2] = quads


def _margin_ring(canvas, state, lands, tile, level, water, ctx) -> None:
    """Composite the cells around the tile into the canvas's one-cell border.

    A border cell with no entry in `lands` stays unknown; the others use their
    own heights, which equal what their own tile assembles.
    """
    cell_water, default_wh = water
    for cy in range(-1, level + 1):
        for cx in range(-1, level + 1):
            if 0 <= cx < level and 0 <= cy < level:
                continue
            key = (tile[0] + cx, tile[1] + cy)
            land = lands.get(key)
            if land is None:
                continue
            wh = _cell_water_height(cell_water, key, default_wh)
            _place(canvas, state, (level - cy, cx + 1),
                   _cell_image(lands, key, ctx, land['heights'], wh),
                   _quad_states(land, ctx[2], key))


def _composite_tile_diffuse(lands, tile_x, tile_y, level, ltex_map, tex_root,
                            tile_heights, cell_water, default_wh,
                            baked_tiles=None, offsets=None):
    """Composite a level-N tile diffuse from its cells' real landscape textures.

    tile_heights is the FILLED tile grid from _assemble_tile (row 0 = south),
    for the murk. Unpainted quadrants take the colour-matched bake; when any
    does, a one-cell ring is composited and the filler feathered into the
    painted neighbours. Returns (atlas RGB, side_px), row 0 = north.
    See: docs/commentary/asset_convert_terrain.md#terrain-lod-filler-match
    """
    from asset_convert.lod.terrain_lod_baked import feather_filler
    px = CELL_DIFFUSE_PX
    ctx = (ltex_map, tex_root, baked_tiles, offsets)
    canvas = np.zeros(((level + 2) * px, (level + 2) * px, 3), dtype=np.uint8)
    state = np.full((2 * (level + 2),) * 2, -1, dtype=np.int8)
    for cy in range(level):
        for cx in range(level):
            key = (tile_x + cx, tile_y + cy)
            h33 = tile_heights[cy*32:cy*32+33, cx*32:cx*32+33]
            wh = _cell_water_height(cell_water, key, default_wh)
            _place(canvas, state, (level - cy, cx + 1),
                   _cell_image(lands, key, ctx, h33, wh),
                   _quad_states(lands.get(key), baked_tiles, key))
    if (state == 0).any():
        _margin_ring(canvas, state, lands, (tile_x, tile_y), level,
                     (cell_water, default_wh), ctx)
        canvas = feather_filler(canvas, state, px // 2).astype(np.uint8)
    return canvas[px:(level + 1) * px, px:(level + 1) * px].copy(), level * px


def _heightmap_normal_rgb(heights: np.ndarray, out_px: int) -> np.ndarray:
    """Derive a model-space terrain-LOD normal map (RGB uint8) from a height grid.

    The .btr has no vertex normals, so distant terrain is lit ONLY by this
    map.  Vanilla's layout is R = east, G = up, B = north: measured by
    correlating Skyrim.esm's LAND slopes for tile tamriel.4.0.0 against its
    vanilla _n.dds (r = 0.96 / 0.93 / 0.95; image row 0 = north).  heights is
    in game units; we resize to out_px and take the gradient.
    """
    from PIL import Image
    # heights row 0 = SOUTH (LAND convention); the diffuse tile is written with
    # image row 0 = NORTH, and the normal map shares its UVs — flip to match.
    hh = np.flipud(np.nan_to_num(heights.astype(np.float32)))
    im = Image.fromarray(hh).resize((out_px, out_px), Image.BILINEAR)
    hh = np.asarray(im, dtype=np.float32)
    # world-space spacing between output samples (game units)
    span = CELL_SIZE * (heights.shape[0] - 1) / 32.0  # tile world span
    dpx = span / out_px
    grow, gx = np.gradient(hh, dpx)
    # image rows run north→south, so ∂h/∂y_world = -∂h/∂row
    gy = -grow
    nz = np.ones_like(gx)
    nx, ny, nzz = -gx, -gy, nz
    norm = np.sqrt(nx*nx + ny*ny + nzz*nzz) + 1e-6
    nx, ny, nzz = nx/norm, ny/norm, nzz/norm
    rgb = np.stack([(nx*0.5+0.5), (nzz*0.5+0.5), (ny*0.5+0.5)], axis=-1)
    return np.clip(rgb*255, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# Per-tile worker — pool initializer + task function
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Shared-memory `lands`
_SHM_ALIGN = 4


def lands_layout(lands: dict):
    """Compute the byte layout of `lands` without materialising it.

    Returns (total_size, plan), where plan is a list of
    `(key, h_off, c_off, base, [(quad, [(fid, off, shape), ...])])`.

    Split from the write so the buffer can be sized exactly and filled DIRECTLY
    into shared memory. Building a bytearray first would hold a second full
    copy in the parent — 1.2 GB on Tamriel-with-overlays, at the very moment 29
    workers are being spawned.
    """
    size = 0

    def _reserve(nbytes: int) -> int:
        nonlocal size
        off = size
        size += nbytes
        if size % _SHM_ALIGN:
            size += _SHM_ALIGN - size % _SHM_ALIGN
        return off

    plan = []
    for key, v in lands.items():
        h_off = _reserve(VERTS_SIDE * VERTS_SIDE * 4)
        c_off = _reserve(VERTS_SIDE * VERTS_SIDE * 3)
        layers = v.get('layers') or {}
        alpha_plan = []
        for quad, entries in (layers.get('alpha') or {}).items():
            packed = []
            for fid, grid in entries:
                packed.append((fid, _reserve(grid.size * 4), grid.shape))
            alpha_plan.append((quad, packed))
        plan.append((key, h_off, c_off, dict(layers.get('base') or {}),
                     alpha_plan))
    return size, plan


def write_lands(lands: dict, plan, mv: memoryview):
    """Fill `mv` from `lands` following `plan`, and build the worker index."""
    index = {}
    for key, h_off, c_off, base, alpha_plan in plan:
        v = lands[key]
        h = np.ascontiguousarray(v['heights'], dtype=np.float32)
        mv[h_off:h_off + h.nbytes] = h.view(np.uint8).reshape(-1).data
        c = np.ascontiguousarray(v['colors'], dtype=np.uint8)
        mv[c_off:c_off + c.nbytes] = c.reshape(-1).data
        alpha_idx = {}
        for quad, packed in alpha_plan:
            entries = (v.get('layers') or {}).get('alpha', {})[quad]
            out = []
            for (fid, off, shape), (_f, grid) in zip(packed, entries):
                g = np.ascontiguousarray(grid, dtype=np.float32)
                mv[off:off + g.nbytes] = g.view(np.uint8).reshape(-1).data
                out.append((fid, off, shape))
            alpha_idx[quad] = out
        index[key] = (h_off, c_off, base, alpha_idx)
    return index


def pack_lands(lands: dict):
    """Pack `lands` into one flat buffer + a picklable index.

    Returns (buffer: bytearray, index: dict). The index holds only offsets and
    small scalars, so it pickles cheaply to every worker.

    Kept for tests and the single-process path; the pool writes straight into
    shared memory via `lands_layout` + `write_lands` instead, to avoid holding
    a second copy in the parent.
    """
    buf = bytearray()
    index = {}

    def _put(arr) -> int:
        off = len(buf)
        buf.extend(arr.tobytes())
        if len(buf) % _SHM_ALIGN:
            buf.extend(bytes(_SHM_ALIGN - len(buf) % _SHM_ALIGN))
        return off

    for key, v in lands.items():
        h_off = _put(np.ascontiguousarray(v['heights'], dtype=np.float32))
        c_off = _put(np.ascontiguousarray(v['colors'], dtype=np.uint8))
        layers = v.get('layers') or {}
        # `decode_land_layers` returns alpha entries as (ltex_fid, grid), sorted
        # into ATXT layer order with the layer index already dropped — see
        # terrain_lod_textures.decode_land_layers. The order IS the data, so it
        # is preserved verbatim here.
        alpha_idx = {}
        for quad, entries in (layers.get('alpha') or {}).items():
            packed = []
            for fid, grid in entries:
                packed.append((fid,
                               _put(np.ascontiguousarray(grid,
                                                         dtype=np.float32)),
                               grid.shape))
            alpha_idx[quad] = packed
        index[key] = (h_off, c_off, dict(layers.get('base') or {}), alpha_idx)
    return buf, index


def _unpack_cell(mv: memoryview, entry):
    """Rebuild one cell's dict as numpy VIEWS over the shared buffer."""
    h_off, c_off, base, alpha_idx = entry
    heights = np.frombuffer(mv, dtype=np.float32, count=VERTS_SIDE * VERTS_SIDE,
                            offset=h_off).reshape(VERTS_SIDE, VERTS_SIDE)
    colors = np.frombuffer(mv, dtype=np.uint8, count=VERTS_SIDE * VERTS_SIDE * 3,
                           offset=c_off).reshape(VERTS_SIDE, VERTS_SIDE, 3)
    alpha = {}
    for quad, entries in alpha_idx.items():
        out = []
        for fid, off, shape in entries:
            n = int(np.prod(shape))
            out.append((fid,
                        np.frombuffer(mv, dtype=np.float32, count=n,
                                      offset=off).reshape(shape)))
        alpha[quad] = out
    return {'heights': heights, 'colors': colors,
            'layers': {'base': base, 'alpha': alpha}}


class SharedLands:
    """dict-like read-only view of the packed `lands`, backed by shared memory.

    Only the cells a worker actually touches are materialised, and each is a
    set of views over the shared buffer — so the arrays themselves are never
    copied into the process.
    """

    __slots__ = ('_mv', '_index', '_cache')

    def __init__(self, mv, index):
        self._mv = mv
        self._index = index
        self._cache = {}

    def __contains__(self, key):
        return key in self._index

    def __len__(self):
        return len(self._index)

    def __iter__(self):
        return iter(self._index)

    def keys(self):
        return self._index.keys()

    def get(self, key, default=None):
        if key not in self._index:
            return default
        return self[key]

    def __getitem__(self, key):
        hit = self._cache.get(key)
        if hit is None:
            hit = _unpack_cell(self._mv, self._index[key])
            self._cache[key] = hit
        return hit


# Per-process global set by _worker_init; avoids pickling lands on every task.
_worker_lands      = None
# Kept alive for the process lifetime: if the SharedMemory handle is garbage
# collected the mapping goes with it and every view becomes invalid memory.
_worker_shm        = None
_worker_mesh_dir   = None
_worker_tex_dir    = None
_worker_ltex_map   = None
_worker_tex_root   = None
_worker_cell_water = None
_worker_default_wh = 0.0
_worker_baked      = None
_worker_synthetic  = frozenset()
_worker_offsets    = None


def _worker_init(lands, mesh_dir_s, tex_dir_s, ltex_map, tex_root_s,
                 cell_water, default_wh, baked_tiles=None,
                 synthetic=frozenset(), offsets=None, vclr_tint=None):
    """Called once per worker process to stash shared read-only data.

    `lands` is either a plain dict (single-process fallback) or the tuple
    `(shm_name, nbytes, index)`, in which case the buffer is MAPPED rather than
    copied — see the SharedLands comment above. `vclr_tint` is the parent's
    resolved mode, so a spawned worker never re-reads the config.
    """
    from asset_convert.lod.terrain_lod_textures import set_vclr_tint
    set_vclr_tint(vclr_tint)
    global _worker_lands, _worker_mesh_dir, _worker_tex_dir
    global _worker_ltex_map, _worker_tex_root
    global _worker_cell_water, _worker_default_wh, _worker_shm, _worker_baked
    global _worker_synthetic, _worker_offsets
    if isinstance(lands, tuple):
        from multiprocessing import shared_memory
        shm_name, nbytes, index = lands
        # Held in a module global for the process lifetime; dropping it would
        # unmap the block out from under every view built on it.
        _worker_shm = shared_memory.SharedMemory(name=shm_name)
        _worker_lands = SharedLands(
            memoryview(_worker_shm.buf)[:nbytes], index)
    else:
        _worker_lands  = lands
    _worker_mesh_dir   = Path(mesh_dir_s)
    _worker_tex_dir    = Path(tex_dir_s)
    _worker_ltex_map   = ltex_map
    # A list of roots (own output first, then masters'); load_texture_rgb
    # searches them in order.
    _worker_tex_root   = ([Path(p) for p in tex_root_s]
                          if isinstance(tex_root_s, (list, tuple))
                          else Path(tex_root_s))
    _worker_cell_water = cell_water
    _worker_default_wh = default_wh
    _worker_baked      = baked_tiles
    _worker_synthetic  = synthetic
    _worker_offsets    = offsets


def _worker_stats() -> dict:
    """This worker's counters since the last call, keyed by kind, then reset."""
    from asset_convert.lod.terrain_lod_textures import (filler_stats,
                                                         texture_stats)
    return {'textures': texture_stats(), 'filler': filler_stats()}


def _process_tile(args):
    """Worker task for one tile.  lands/dirs come from the process global.

    args: (tile_x, tile_y, level, worldspace_edid)
    Returns (tag, ok, error_msg, stats delta for `_collect`).
    """
    tile_x, tile_y, level, worldspace_edid = args
    tag = f'{worldspace_edid}.{level}.{tile_x}.{tile_y}'

    try:
        heights, colors = _assemble_tile(_worker_lands, tile_x, tile_y, level,
                                         _worker_synthetic)

        water_quads = _tile_water_quads(_worker_lands, _worker_cell_water,
                                        tile_x, tile_y, level, _worker_default_wh)

        solid = tile_solid_mask(_worker_lands, tile_x, tile_y, level)
        nif_bytes = build_terrain_nif(heights, tile_x, tile_y, level,
                                      worldspace_edid,
                                      water_quads=water_quads,
                                      solid_mask=solid)
        if nif_bytes is None:
            return tag, True, None, _worker_stats()
        (_worker_mesh_dir / f'{tag}.btr').write_bytes(nif_bytes)

        tex_size = TEX_SIZE_BY_LEVEL.get(level, TEX_SIZE)

        # Diffuse: composite real landscape textures per LAND alpha layers.
        atlas, _side = _composite_tile_diffuse(
            _worker_lands, tile_x, tile_y, level,
            _worker_ltex_map, _worker_tex_root,
            heights, _worker_cell_water, _worker_default_wh, _worker_baked,
            _worker_offsets)
        write_dds_dxt1(atlas, _worker_tex_dir / f'{tag}.dds', size=tex_size)

        # Normal map: derive from the tile heightmap so distant terrain is lit.
        # Baked at half the diffuse resolution (BC5 is 2x DXT1/texel).
        normal_size = max(64, tex_size // NORMAL_SIZE_DIVISOR)
        normal_rgb = _heightmap_normal_rgb(heights, normal_size)
        write_normal_dds(normal_rgb, _worker_tex_dir / f'{tag}_n.dds')

        return tag, True, None, _worker_stats()
    except Exception as e:
        import traceback
        return (tag, False, f"{e}\n{traceback.format_exc()}",
                _worker_stats())


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------

def _queue_tiles(lands, bounds, worldspace_edid, only_cells):
    """The (tx, ty, level, edid) tile tasks to bake.

    A tile is queued only when a cell it covers is in `lands` (LAND or a
    synthetic cell from the baked LOD), so no tile is baked purely from
    edge-extended heights. `only_cells` restricts an override to the tiles its
    edits touch. Tripwire: only full runs synthesize, so a partial run's tiles
    lack horizon cells a full run has.
    See: docs/commentary/asset_convert_terrain.md#no-terrain-without-an-authored-source
    """
    min_x, min_y, max_x, max_y = bounds
    work = []
    for level in LOD_LEVELS:
        tx_start = (min_x // level) * level
        ty_start = (min_y // level) * level
        tx_end   = (max_x // level + 1) * level
        ty_end   = (max_y // level + 1) * level

        n_level = 0
        for ty in range(ty_start, ty_end, level):
            for tx in range(tx_start, tx_end, level):
                cells = [(tx + cx, ty + cy)
                         for cy in range(level) for cx in range(level)]
                if not any(c in lands for c in cells):
                    continue
                if only_cells is not None and not any(c in only_cells
                                                      for c in cells):
                    continue
                work.append((tx, ty, level, worldspace_edid))
                n_level += 1
        print(f"  LOD {level}: {n_level} tiles queued")
    return work


def _deps_ok() -> bool:
    """True when Pillow and pyffi are importable; says which is missing."""
    try:
        __import__('PIL')
    except ImportError:
        print("  ERROR: Pillow not installed — pip install Pillow")
        return False
    if not PYFFI_AVAILABLE:
        print("  ERROR: pyffi not available")
        return False
    return True


def _parse_world(esm_path: Path, worldspace_edid: str, overlay_paths):
    """{'lands', 'cell_water', 'default_wh', 'deleted'} for a worldspace; None without LAND."""
    srcs = ', '.join([esm_path.name] + [Path(p).name
                                        for p in (overlay_paths or [])])
    print(f"\n[TerrainLOD] Parsing LAND records from {srcs}...")
    deleted = set()
    lands, cell_water, default_wh = parse_land_records(
        esm_path, worldspace_edid, overlay_paths, deleted)
    if not lands:
        print("  No LAND records found.")
        return None
    n_water = sum(1 for hw, _ in cell_water.values() if hw)
    print(f"  Found {len(lands)} LAND records; {n_water} water cells "
          f"(default water height {default_wh}).")
    return {'lands': lands, 'cell_water': cell_water,
            'default_wh': default_wh, 'deleted': deleted}


def _horizon_cells(world: dict, lod_source_dirs, worldspace_edid: str,
                   only_cells) -> frozenset:
    """Synthesize cells from the baked LOD meshes where no LAND is; their keys.

    Full runs only: a partial run keeps the tiles it touches as they were.
    See: docs/commentary/asset_convert_terrain.md#no-terrain-without-an-authored-source
    """
    if only_cells is not None or not lod_source_dirs:
        return frozenset()
    from asset_convert.lod import terrain_lod_baked as tb
    heights = tb.baked_heights(tb.lod_meshes(lod_source_dirs, worldspace_edid))
    keys = tb.synthesize(world, heights)
    wet = sum(1 for k in keys if k in world['cell_water'])
    print(f"  Synthetic horizon cells: {len(keys)} from the baked LOD meshes "
          f"({len(world['deleted'])} overlay-deleted cells kept empty; "
          f"{wet} with water).")
    return frozenset(keys)


def _cell_bounds(lands) -> tuple:
    """(min_x, min_y, max_x, max_y) over the cells of `lands`, printed."""
    all_x = [k[0] for k in lands]
    all_y = [k[1] for k in lands]
    print(f"  Cell range: X=[{min(all_x)},{max(all_x)}] "
          f"Y=[{min(all_y)},{max(all_y)}]")
    return min(all_x), min(all_y), max(all_x), max(all_y)


def _tile_dirs(output_dir: Path, worldspace_edid: str) -> tuple:
    """(mesh dir, texture dir) for this worldspace's tiles, created."""
    mesh_dir = output_dir / 'meshes' / 'terrain' / worldspace_edid
    tex_dir = output_dir / 'textures' / 'terrain' / worldspace_edid
    mesh_dir.mkdir(parents=True, exist_ok=True)
    tex_dir.mkdir(parents=True, exist_ok=True)
    return mesh_dir, tex_dir


def _texture_setup(esm_path, overlay_paths, output_dir, extra_texture_roots):
    """(LTEX -> texture map, ordered textures/ roots) for the compositor.

    Overlays merge on top so an LTEX the plugin adds or re-points wins.
    See: docs/commentary/asset_convert_terrain.md#terrain-lod-texture-lookup
    """
    from asset_convert.lod.terrain_lod_textures import build_ltex_texture_map
    ltex_map = build_ltex_texture_map(
        [esm_path] + [Path(ov) for ov in (overlay_paths or [])])
    tex_roots = [output_dir / 'textures']
    tex_roots += [Path(r) for r in (extra_texture_roots or [])]
    print(f"  Resolved {len(ltex_map)} LTEX landscape textures "
          f"across {len(tex_roots)} texture root(s).")
    return ltex_map, tex_roots


def _share_lands(lands, n_workers: int) -> tuple:
    """(worker `lands` argument, SharedMemory or None).

    With more than one worker the cells are written ONCE, straight into a
    shared block sized in advance, instead of pickled into every worker.
    See: docs/commentary/asset_convert_terrain.md#terrain-lod-shared-lands
    """
    if n_workers <= 1:
        return lands, None
    from multiprocessing import shared_memory
    size, plan = lands_layout(lands)
    shm = shared_memory.SharedMemory(create=True, size=max(size, 1))
    index = write_lands(lands, plan, memoryview(shm.buf)[:size])
    print(f"  Shared {size/1e6:.0f} MB of LAND data across "
          f"{n_workers} worker(s) (one copy, not {n_workers}).")
    return (shm.name, size, index), shm


def _release(shm) -> None:
    """Unmap and free a shared block; both are needed or the segment leaks."""
    if shm is None:
        return
    shm.close()
    try:
        shm.unlink()
    except FileNotFoundError:
        pass


def _collect(results, work) -> tuple:
    """(per-level ok counts, failures, merged worker stats) from tile results."""
    per_level_ok, failed, stats = {}, 0, {}
    for (tag, ok, err, delta), item in zip(results, work):
        for kind, counts in delta.items():
            stats.setdefault(kind, Counter()).update(counts)
        if ok:
            per_level_ok[item[2]] = per_level_ok.get(item[2], 0) + 1
        else:
            failed += 1
            print(f"  WARNING: {tag}: {err}")
    return per_level_ok, failed, stats


def _bake_tiles(world: dict, work, init: tuple) -> tuple:
    """Bake `work` in a pool; (per-level ok counts, failures, merged stats).

    Takes `world['lands']` out of the dict so no parent reference outlives
    the copy published to the workers. chunksize=1 because tiles differ ~20x
    in cost and arrive sorted most expensive first.
    See: docs/commentary/asset_convert_terrain.md#terrain-lod-shared-lands
    """
    n_workers = worker_count()
    print(f"  Using {n_workers} worker process(es).")
    lands = world.pop('lands')
    shm = None
    try:
        lands_arg, shm = _share_lands(lands, n_workers)
        del lands
        with ProcessPoolExecutor(max_workers=n_workers,
                                 initializer=_worker_init,
                                 initargs=(lands_arg,) + init) as pool:
            return _collect(pool.map(_process_tile, work, chunksize=1), work)
    finally:
        _release(shm)


def _baked_sources(lod_source_dirs, worldspace_edid: str) -> dict:
    """{(tile x, tile y): baked diffuse path} from the owner's and suppliers' exports.

    See: docs/commentary/asset_convert_terrain.md#terrain-lod-filler
    """
    from asset_convert.lod.terrain_lod_baked import baked_textures
    tiles = baked_textures(lod_source_dirs, worldspace_edid)
    print(f"  Baked LOD tiles for unpainted ground: {len(tiles)} "
          f"(from {len(lod_source_dirs or [])} export dir(s)).")
    return {k: str(v) for k, v in tiles.items()}


#: Cell image size (px) the filler colour match is fitted at; means barely depend on it.
_FIT_PX = 16


def _dry(world: dict, key) -> bool:
    """True when LAND cell `key` lies wholly above its water (or has none)."""
    wh = _cell_water_height(world['cell_water'], key, world['default_wh'])
    return wh is None or float(world['lands'][key]['heights'].min()) > wh


def _fit_cell(world, key, textures, baked):
    """(composite - bake) over `key`'s painted quadrants, or None when it has none."""
    from asset_convert.lod import terrain_lod_baked as tb
    from asset_convert.lod import terrain_lod_textures as tlt
    land = world['lands'][key]
    quads = [q for q in range(4) if not tlt.unpainted_quad(land['layers'], q)]
    path = _bake_tile(baked, key)
    if not quads or path is None or not _dry(world, key):
        return None
    tile = tuple((c // tb.TILE_CELLS) * tb.TILE_CELLS for c in key)
    crop = tb.cell_crop(tb.load_tile_rgb(path), key[0] - tile[0],
                        key[1] - tile[1], _FIT_PX).astype(np.float64)
    ours = tlt.composite_cell(land['layers'], land['colors'], textures[0],
                              textures[1], key[0], key[1], cell_px=_FIT_PX)
    return tb.cell_offset(ours.astype(np.float64), crop, quads,
                          tlt.quad_blocks(_FIT_PX))


def _filler_offsets(world: dict, textures, baked) -> dict:
    """The per-cell colour shift from the bake to our composite, or {} with no bake.

    Fitted on painted dry cells near a filler cell, smoothed over every filler
    cell and its neighbours. `textures` is (ltex_map, texture roots).
    See: docs/commentary/asset_convert_terrain.md#terrain-lod-filler-match
    """
    from asset_convert.lod import terrain_lod_baked as tb
    from asset_convert.lod import terrain_lod_textures as tlt
    lands = world['lands']
    core = [k for k, land in lands.items() if _bake_tile(baked, k) and any(
        tlt.unpainted_quad(land['layers'], q) for q in range(4))]
    if not core:
        return {}
    near = {nb for k in core for nb, _r in tb.window(k, tb.OFFSET_RADIUS)
            if nb in lands}
    fitted = {k: v for k in near
              if (v := _fit_cell(world, k, textures, baked)) is not None}
    targets = {nb for k in core for nb, _r in tb.window(k, 1)}
    offsets = tb.smooth_offsets(fitted, targets)
    tlt.texture_stats()
    tlt.filler_stats()
    print(f"  Filler colour match: {len(fitted)} painted cells fitted, "
          f"{len(core)} filler cells; worldspace shift "
          f"{tuple(round(float(v), 1) for v in offsets['prior'])}")
    return offsets


def _report_bake(per_level_ok: dict, failed: int, stats: dict) -> bool:
    """Print the bake summary; False when no landscape texture was found.

    See: docs/commentary/asset_convert_terrain.md#terrain-lod-texture-counts
    """
    from asset_convert.lod.terrain_lod_textures import texture_report
    for level in LOD_LEVELS:
        print(f"  LOD {level}: {per_level_ok.get(level, 0)} tiles generated")
    if failed:
        print(f"  {failed} tiles failed")
    lines, ok = texture_report(stats.get('textures', Counter()))
    filler = stats.get('filler', Counter())
    lines.append(f"  Terrain-LOD filler: {filler['baked']} baked, "
                 f"{filler['default']} default (no source) unpainted quadrants")
    print('\n'.join(lines))
    if not ok:
        print("  ERROR: not one landscape texture was found on disk; every "
              "tile is flat grey")
        return False
    print(f"[TerrainLOD] Done — {sum(per_level_ok.values())} tiles generated.")
    return True


def generate_terrain_lod(esm_path: Path, output_dir: Path,
                         worldspace_edid: str = 'TES4Tamriel',
                         overlay_paths=None,
                         only_cells=None,
                         extra_texture_roots=None,
                         lod_source_dirs=None,
                         vclr_tint=None) -> bool:
    """Generate terrain LOD (.btr + .dds) for every tile of one worldspace.

    `overlay_paths` apply on top of `esm_path` in load order; `only_cells`
    restricts output to tiles covering those cells (heights are still parsed
    worldspace-wide); `extra_texture_roots` are further textures/ roots for
    the compositor; `lod_source_dirs` are the owner's and suppliers' export
    record dirs, in load order, whose shipped LOD fills unpainted ground;
    `vclr_tint` names the VCLR mode. True on success.
    See: docs/commentary/asset_convert_terrain.md#generate-terrain-lod-arguments
    """
    if not _deps_ok():
        return False
    from asset_convert.lod.terrain_lod_textures import set_vclr_tint
    tint = set_vclr_tint(vclr_tint)
    print(f"  VCLR tint: {tint}")
    world = _parse_world(esm_path, worldspace_edid, overlay_paths)
    if world is None:
        return False
    synthetic = _horizon_cells(world, lod_source_dirs, worldspace_edid,
                               only_cells)
    bounds = _cell_bounds(world['lands'])
    mesh_dir, tex_dir = _tile_dirs(output_dir, worldspace_edid)
    ltex_map, tex_roots = _texture_setup(esm_path, overlay_paths, output_dir,
                                         extra_texture_roots)
    work = _queue_tiles(world['lands'], bounds, worldspace_edid, only_cells)
    if not work:
        print("  No tiles to generate.")
        return False
    work.sort(key=lambda w: -w[2])
    _drop_plugin_bytes()
    baked = _baked_sources(lod_source_dirs, worldspace_edid)
    init = (str(mesh_dir), str(tex_dir), ltex_map, [str(r) for r in tex_roots],
            world['cell_water'], world['default_wh'], baked, synthetic,
            _filler_offsets(world, (ltex_map, tex_roots), baked), tint)
    return _report_bake(*_bake_tiles(world, work, init))


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Generate terrain LOD for a converted TES5 plugin')
    parser.add_argument('esm', help='Path to converted ESM/ESP')
    parser.add_argument('output_dir', help='Plugin output directory')
    parser.add_argument('--worldspace', default='TES4Tamriel')
    args = parser.parse_args()
    generate_terrain_lod(Path(args.esm), Path(args.output_dir), args.worldspace)
