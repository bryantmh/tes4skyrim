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
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))
from subprocess_flags import POPEN_FLAGS, windows_cmd  # noqa: E402

# LODGen 3.0.36.0. This replaced the 2.2.0.0 build of the same name, which
# let an unparseable model throw out of a worker thread and take the whole
# process down, losing every tile after it. See run_lodgen().
LODGEN_EXE = (
    SCRIPT_DIR / "external" / "lodgen" / "LODGenx64.exe"
)


# ---------------------------------------------------------------------------
# 1. LODSettings file
#
# Format: little-endian binary
#   int16  SW cell X
#   int16  SW cell Y
#   int16  NE cell X  (or width — docs are unclear; use NE)
#   int16  NE cell Y
#
# LODGen.pas reads SWCellX/SWCellY at offset 0 (TES5 game mode).
# The game also reads this file to know the extent of terrain LOD tiles.
# ---------------------------------------------------------------------------

def write_lod_settings(worldspace_edid: str, sw_x: int, sw_y: int,
                       ne_x: int, ne_y: int, output_dir: Path) -> tuple:
    """Write LODSettings/<worldspace_edid>.lod.

    16-byte format (TES5):
      int16  SW cell X
      int16  SW cell Y
      uint32 grid width  (NE_X - SW_X, rounded up to power of 2)
      uint32 min LOD level  (always 4)
      uint32 max LOD level  (always 32)

    Returns (path, effective_sw_x, effective_sw_y) so callers can use the
    same SW coordinates in the LODGen CellSW= header line.
    """
    lod_dir = output_dir / "LODSettings"
    lod_dir.mkdir(parents=True, exist_ok=True)
    out = lod_dir / f"{worldspace_edid}.lod"

    # The grid must COVER the terrain.  The engine builds its terrain-LOD
    # quadtree from this header (root at SW, `size` cells across, subdivided to
    # `min_lod`); a tile outside that square has no node, and the per-frame
    # walk indexes the node array without a bounds check —
    # SkyrimSE.exe+050E6AD `mov rbx,[rax+rcx*8]` with rax=0, a hard CTD the
    # moment the worldspace streams.
    #
    # 57 of 84 TES4 worldspaces author no usable MNAM map dimensions, so
    # sw==ne==0 arrives here and the old maths produced size=1 — a 1x1 grid —
    # while LODGen still emitted .btr tiles out to (-32,-32).  Every converted
    # worldspace got `SWx=0 SWy=1`; Kvatch crashed on entry.  Callers now pass
    # the extents measured from the CELLS, which always exist.
    #
    # Vanilla (extracted from Skyrim - Meshes0.bsa) confirms both the layout
    # and that SW is REAL, not centred: japhetsfollyworld (-9,-6) size 16
    # maxLOD 16; dlc01falmervalley (-16,-13) size 32; skuldafnworld (0,-21)
    # size 64.  maxLOD tracks size — it is not always 32.
    # The root must contain every TILE, and LODGen snaps each tile's origin
    # DOWN to a multiple of its own level (a level-16 tile covering cell -9
    # is named ...16.-16.y), so tiles start below the literal terrain corner.
    # Anchor SW at a multiple of max_lod and size the square from there.
    #
    # max_lod is the coarsest level emitted, capped at 32; it is chosen first
    # because it sets the anchor granularity.  Grow both together until the
    # aligned square covers [sw, ne) — growing is always safe, and the pair is
    # recomputed each round so the anchor tracks the level.
    max_lod = 4
    while True:
        anchor = min(max_lod, 32)
        eff_sw_x = (sw_x // anchor) * anchor
        eff_sw_y = (sw_y // anchor) * anchor
        size = anchor
        while (eff_sw_x + size < ne_x or eff_sw_y + size < ne_y) and size < 4096:
            size <<= 1
        # The square is anchored and covers the terrain; accept unless a
        # coarser level would still be emitted inside it (max_lod < size).
        if max_lod >= min(size, 32) or max_lod >= 32:
            break
        max_lod <<= 1
    max_lod = min(max_lod, 32)

    out.write_bytes(struct.pack("<hhIII", eff_sw_x, eff_sw_y, size, 4, max_lod))
    print(f"  Wrote {out}")
    return out, eff_sw_x, eff_sw_y


# ---------------------------------------------------------------------------
# 2. Parse the converted ESM to build the LODGen input text file.
#
# LODGen input format (from LODGen.pas reverse engineering):
#
#   Header lines (key=value):
#     GameMode=TES5
#     Worldspace=<EditorID>
#     CellSW=<x> <y>
#     PathData=<tes5 data dir>
#     PathOutput=<output meshes dir>
#     Resource=<bsa path>    (0 or more)
#
#   Data lines (tab-separated, one per REFR):
#     <FormID hex>  <RecordFlags hex>  <X>  <Y>  <Z>  <rX>  <rY>  <rZ>  <scale>
#         <EDID>  <StatFlags hex>  <material>  <full mesh>  <lod4 mesh>  <lod8 mesh>  <lod16 mesh>
#
# We generate LOD for:
#   - STAT/ACTI/MSTT/TREE references in exterior cells of the worldspace
#   - whose base object model path has a companion _far.nif in the output tree
#   - OR whose base STAT record has MNAM LOD entries
#
# In practice for converted Oblivion content: the _far.nif files were skipped
# by bsa_extract.  We use _far.nif as LOD4/LOD8/LOD16 if it exists, otherwise
# use the full model as the LOD mesh (LODGen will simplify it).
# ---------------------------------------------------------------------------

# ESM binary constants (TES5)
_REC_HDR   = 24
_GRP_HDR   = 24
_SUB_HDR   = 6
_FLAG_COMP       = 0x00040000
_FLAG_DISTANT_LOD  = 0x00008000   # Has Distant LOD — SSELodGen bakes LOD for this object
_FLAG_WORLD_MAP    = 0x10000000   # Show in World Map — object appears on the world map
_FLAG_PERSISTENT = 0x00000400  # on REFR


def _sub(subrecords, tag):
    for s in subrecords:
        if s[0] == tag:
            return s[1]
    return None


def _parse_subrecords(data: bytes):
    subs = []
    pos = 0
    while pos + _SUB_HDR <= len(data):
        tag  = data[pos:pos+4].decode('ascii', errors='replace')
        size = struct.unpack_from('<H', data, pos+4)[0]
        pos += _SUB_HDR
        subs.append((tag, data[pos:pos+size]))
        pos += size
    return subs


def _read_record(data: bytes, pos: int):
    if pos + _REC_HDR > len(data):
        return None, pos
    sig       = data[pos:pos+4].decode('ascii', errors='replace')
    data_size = struct.unpack_from('<I', data, pos+4)[0]
    flags     = struct.unpack_from('<I', data, pos+8)[0]
    form_id   = struct.unpack_from('<I', data, pos+12)[0]
    end       = pos + _REC_HDR + data_size

    raw = data[pos+_REC_HDR:end]
    if flags & _FLAG_COMP and len(raw) >= 4:
        import zlib
        try:
            raw = zlib.decompress(raw[4:])
        except Exception:
            pass

    subs = _parse_subrecords(raw)
    return {'sig': sig, 'flags': flags, 'form_id': form_id, 'subs': subs}, end


def _zstr(b: bytes) -> str:
    return b.rstrip(b'\x00').decode('latin-1', errors='replace')


# Parsed-ESM cache, keyed on file identity.
#
# generate_lod() is called ONCE PER WORLDSPACE and re-parsed the whole plugin
# every time.  Oblivion.esm ships 18 worldspaces and the parse is 5.7 s over
# 613 MB (1,017,612 refs), so ~103 s of the object-LOD stage was spent
# re-deriving byte-for-byte identical data.
#
# Keyed on (path, mtime_ns, size) so a rebuilt ESM is re-parsed rather than
# served stale.  Only the most recent file is kept: the caller walks one plugin
# (plus its overlays) per run, so a 1-entry cache hits on every worldspace
# without pinning several hundred MB per extra plugin.
#
# The returned structures are treated as READ-ONLY by callers — write_lodgen_input
# builds its own per-worldspace views and generate_lod merges overlays into a
# COPY (see _merge_overlay below); if that ever stops being true this must hand
# out deep copies instead.
_PARSED_ESM_CACHE: dict = {}


def _parse_esm_cached(esm_path: Path):
    """`_parse_esm` memoised on (path, mtime, size). See _PARSED_ESM_CACHE."""
    try:
        st = esm_path.stat()
        key = (str(esm_path).lower(), st.st_mtime_ns, st.st_size)
    except OSError:
        return _parse_esm(esm_path)
    hit = _PARSED_ESM_CACHE.get(key)
    if hit is None:
        hit = _parse_esm(esm_path)
        _PARSED_ESM_CACHE.clear()
        _PARSED_ESM_CACHE[key] = hit
    return hit


def _parse_esm(esm_path: Path):
    """
    Minimal ESM parser. Returns dicts:
      worldspaces: {form_id: {edid, mnam_sw_x, mnam_sw_y, mnam_ne_x, mnam_ne_y}}
      cells:       {form_id: {parent_wrld, grid_x, grid_y}}
      stats:       {form_id: {edid, flags, model, lod4, lod8, lod16}}
      refs:        [{form_id, flags, base_fid, parent_wrld, parent_cell,
                     x,y,z, rx,ry,rz, scale}]
    """
    raw = esm_path.read_bytes()
    n   = len(raw)

    worldspaces = {}
    cells       = {}
    stats       = {}
    refs        = []

    # We do a single linear scan using a recursive group parser.
    pos = 0
    # Skip file header (first record)
    if n < _REC_HDR:
        return worldspaces, cells, stats, refs
    hdr_size = struct.unpack_from('<I', raw, 4)[0]
    pos = _REC_HDR + hdr_size

    def parse_group(start, end, parent_wrld, parent_cell):
        nonlocal pos
        p = start + _GRP_HDR
        grp_type = struct.unpack_from('<I', raw, start+12)[0]
        label    = raw[start+8:start+12]

        pw = parent_wrld
        pc = parent_cell
        if grp_type == 1:                   # world children
            pw = struct.unpack_from('<I', label)[0]
        elif grp_type in (6, 8, 9, 10):     # cell children
            pc = struct.unpack_from('<I', label)[0]

        while p < end and p < n:
            if p + 4 > n:
                break
            sig4 = raw[p:p+4]
            if sig4 == b'GRUP':
                if p + _GRP_HDR > n:
                    break
                g_size = struct.unpack_from('<I', raw, p+4)[0]
                parse_group(p, p + g_size, pw, pc)
                p += g_size
            else:
                rec, next_p = _read_record(raw, p)
                if rec is None:
                    break
                _dispatch(rec, pw, pc)
                if rec['sig'] == 'CELL':
                    pc = rec['form_id']
                elif rec['sig'] == 'WRLD':
                    pw = rec['form_id']
                p = next_p

    def _dispatch(rec, pw, pc):
        sig = rec['sig']
        fid = rec['form_id']
        subs = rec['subs']

        if sig == 'WRLD':
            edid = _zstr(_sub(subs, 'EDID') or b'')
            sw_x = sw_y = ne_x = ne_y = 0
            mnam = _sub(subs, 'MNAM')
            if mnam and len(mnam) >= 16:
                # MNAM: usable dim X(i16), Y(i16), NW_x(i16), NW_y(i16), SE_x(i16), SE_y(i16), ...
                # Layout: usableX(i32), usableY(i32), NWcell_x(i16), NWcell_y(i16),
                #         SEcell_x(i16), SEcell_y(i16)
                nw_x = struct.unpack_from('<h', mnam, 8)[0]
                nw_y = struct.unpack_from('<h', mnam, 10)[0]
                se_x = struct.unpack_from('<h', mnam, 12)[0]
                se_y = struct.unpack_from('<h', mnam, 14)[0]
                # SW = min corners, NE = max corners
                sw_x = min(nw_x, se_x)
                sw_y = min(nw_y, se_y)
                ne_x = max(nw_x, se_x)
                ne_y = max(nw_y, se_y)
            worldspaces[fid] = {
                'edid': edid, 'sw_x': sw_x, 'sw_y': sw_y,
                'ne_x': ne_x, 'ne_y': ne_y,
            }

        elif sig == 'CELL':
            grid_x = grid_y = None
            xclc = _sub(subs, 'XCLC')
            if xclc and len(xclc) >= 8:
                grid_x = struct.unpack_from('<i', xclc, 0)[0]
                grid_y = struct.unpack_from('<i', xclc, 4)[0]
            cells[fid] = {'parent_wrld': pw, 'grid_x': grid_x, 'grid_y': grid_y}

        elif sig in ('STAT', 'ACTI', 'MSTT', 'TREE'):
            edid  = _zstr(_sub(subs, 'EDID') or b'')
            model = ''
            modl  = _sub(subs, 'MODL')
            if modl:
                model = _zstr(modl)
            # MNAM LOD entries (STAT only: sequence of MNAM subs with LOD mesh paths)
            lod4 = lod8 = lod16 = ''
            mnam_subs = [s for s in subs if s[0] == 'MNAM']
            if len(mnam_subs) >= 1:
                lod4 = _zstr(mnam_subs[0][1])
            if len(mnam_subs) >= 2:
                lod8 = _zstr(mnam_subs[1][1])
            if len(mnam_subs) >= 3:
                lod16 = _zstr(mnam_subs[2][1])
            # OBND bounds (for tree billboard sizing)
            obnd = _sub(subs, 'OBND')
            bounds = None
            if obnd and len(obnd) >= 12:
                bounds = struct.unpack_from('<6h', obnd)
            stats[fid] = {
                'edid': edid,
                'sig': sig,
                'flags': rec['flags'],
                'model': model,
                'obnd': bounds,
                'lod4': lod4, 'lod8': lod8, 'lod16': lod16,
            }

        elif sig == 'REFR':
            base_fid = 0
            name = _sub(subs, 'NAME')
            if name and len(name) >= 4:
                base_fid = struct.unpack_from('<I', name)[0]
            x = y = z = rx = ry = rz = 0.0
            data_sub = _sub(subs, 'DATA')
            if data_sub and len(data_sub) >= 24:
                x, y, z, rx, ry, rz = struct.unpack_from('<6f', data_sub)
            scale = 1.0
            xscl = _sub(subs, 'XSCL')
            if xscl and len(xscl) >= 4:
                scale = struct.unpack_from('<f', xscl)[0]
            refs.append({
                'form_id': fid, 'flags': rec['flags'], 'base_fid': base_fid,
                'parent_wrld': pw, 'parent_cell': pc,
                'x': x, 'y': y, 'z': z,
                'rx': rx, 'ry': ry, 'rz': rz,
                'scale': scale,
            })

    # Walk top-level GRUPs
    p = pos
    while p < n:
        if p + 4 > n:
            break
        if raw[p:p+4] != b'GRUP':
            break
        if p + _GRP_HDR > n:
            break
        g_size = struct.unpack_from('<I', raw, p+4)[0]
        parse_group(p, p + g_size, 0, 0)
        p += g_size

    return worldspaces, cells, stats, refs


# ---------------------------------------------------------------------------
# LOD mesh resolution helpers
# ---------------------------------------------------------------------------

def _win_join(root: Path, rel: str) -> Path:
    """Resolve a backslash-form (BSA/record/.bto-internal) relative path
    against a real filesystem root.

    Model/texture paths throughout this module come from the game's own
    binary formats and are always Bethesda/Windows-style backslash-separated,
    regardless of host OS. `root / rel` (pathlib's `/`) or `os.path.join`
    only split on the HOST's own separator, so a multi-segment backslash rel
    silently collapses into one flat filename on Linux instead of real nested
    directories -- split explicitly here instead.
    """
    return root.joinpath(*rel.replace('/', '\\').split('\\'))


def _far_nif_path(model_path: str) -> str:
    """Return the expected _far.nif path for a given model path."""
    if not model_path:
        return ''
    base = model_path
    if base.lower().endswith('.nif'):
        base = base[:-4]
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


def _mesh_exists(path: str, output_meshes_dir: Path) -> bool:
    """Return True if a mesh file exists in the tes4 output meshes directory."""
    if not path:
        return False
    # Strip leading 'meshes\\' if present — output_meshes_dir IS the meshes root
    rel = path.lower().replace('/', '\\').lstrip('\\')
    if rel.startswith('meshes\\'):
        rel = rel[len('meshes\\'):]
    return _win_join(output_meshes_dir, rel).exists()


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


def _lod_mesh_is_safe(path: str, output_meshes_dir: Path) -> bool:
    """False if this mesh's root block would crash LODGen's NiNode cast."""
    rel = path.lower().replace('/', '\\').lstrip('\\')
    if rel.startswith('meshes\\'):
        rel = rel[len('meshes\\'):]
    full = _win_join(output_meshes_dir, rel)
    key = str(full).lower()
    cached = _NIF_ROOT_SAFE_CACHE.get(key)
    if cached is not None:
        return cached

    safe = _root_is_ninode(full)
    _NIF_ROOT_SAFE_CACHE[key] = safe
    return safe


# Block types LODGen can safely cast to NiNode (NiNode and its subclasses as
# they appear as a NIF root).  Resolved lazily against NifFormat so the list
# cannot drift from what pyffi actually considers a NiNode subclass.
_NINODE_ROOT_NAMES = None


def _ninode_root_names():
    global _NINODE_ROOT_NAMES
    if _NINODE_ROOT_NAMES is None:
        from .lod_far_gen import NifFormat
        names = set()
        for attr in dir(NifFormat):
            cls = getattr(NifFormat, attr, None)
            if (isinstance(cls, type)
                    and issubclass(cls, NifFormat.NiNode)):
                names.add(attr)
        _NINODE_ROOT_NAMES = names
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
    """Full-parse fallback for header shapes the fast path does not model."""
    try:
        from .lod_far_gen import NifFormat
        data = NifFormat.Data()
        with open(full, 'rb') as fh:
            data.read(fh)
        roots = data.roots
        return bool(roots) and isinstance(roots[0], NifFormat.NiNode)
    except Exception:
        return False


# Objects smaller than this (max OBND dimension, game units) are only baked
# into the near LOD-4 tiles.  A level-8 tile starts ~2 cells out; small
# clutter is invisible there but its baked geometry still costs disk/VRAM.
_LOD8_MIN_SIZE = 400.0


def _obnd_max_dim(stat: dict) -> float:
    obnd = stat.get('obnd')
    if not obnd:
        return 0.0
    x1, y1, z1, x2, y2, z2 = obnd
    return float(max(x2 - x1, y2 - y1, z2 - z1))


# ---------------------------------------------------------------------------
# Master-owned assets and what a child plugin may ship
#
# The rule, for MESHES and TEXTURES alike: a child never ships a file its
# master already ships at the same path. The game's Data folder holds exactly
# one file per path, so the child's .bto tiles resolve against the master's
# copy — a second byte-identical copy only bloats the plugin (ElsweyrAnequina:
# 2,015 meshes / 211 MB plus 828 textures / 165 MB).
#
# Textures need nothing staged: they are read at RUNTIME, so the master's copy
# is simply left in place (_fill_missing_lod_textures skips anything a master
# ships). Meshes are read by LODGen at BUILD time under a single PathData root
# that cannot span both trees, so they are staged here and dropped afterwards.
_STAGED_MASTER_MESHES = set()


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
        return True
    r = rel.lower().replace('/', '\\').lstrip('\\')
    if r.startswith('meshes\\'):
        r = r[len('meshes\\'):]
    for mdir in (master_meshes or []):
        src = _win_join(Path(mdir), r)
        if not src.exists():
            continue
        dst = _win_join(output_meshes_dir, r)
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            # Only a file WE created is scratch; never a pre-existing one.
            _STAGED_MASTER_MESHES.add(str(dst))
            return True
        except OSError:
            return False
    return False


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
    rel = (model or '').lower().replace('/', '\\').lstrip('\\')
    if not rel:
        return False
    if rel.startswith('meshes\\'):
        rel = rel[len('meshes\\'):]
    own = own_meshes_root / rel
    if not own.is_file():
        return False
    for mm in master_meshes:
        src = Path(mm) / rel
        if not src.is_file():
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
    """
    Return (lod4, lod8, lod16) mesh paths for a stat record.

    - Trees use their billboard-card _far.nif at every level — the cards are
      8 verts each, so distant forests stay visible for almost no cost.
    - Other LOD objects (0x8000) get lod4; lod8 only if they're big enough
      to matter at level-8 distances (_LOD8_MIN_SIZE).
    - World-map objects (0x10000000) additionally get lod16 so LODGenx64
      bakes tiles for the far ring / world-map view.

    `master_meshes` lets an override plugin rebuilding a whole tile use LOD
    meshes that were only generated into a MASTER's output; each one found is
    copied into this plugin's tree so LODGen can resolve it.
    """
    lod4  = stat.get('lod4', '')
    lod8  = stat.get('lod8', '')
    lod16 = stat.get('lod16', '')

    if lod4 or lod8 or lod16:
        return lod4, lod8, lod16

    model = stat.get('model', '')
    if not model:
        return '', '', ''

    far = _far_nif_path(model)
    if not _import_master_mesh(far, output_meshes_dir, master_meshes):
        return '', '', ''

    from .lod_far_gen import is_tree_model, _tier_path, _TIER8, _TIER16
    if is_tree_model(stat):
        return far, far, far

    flags = stat.get('flags', 0)
    lod8_mesh = lod16_mesh = ''
    if _obnd_max_dim(stat) >= _LOD8_MIN_SIZE:
        far8 = str(_tier_path(Path(far), _TIER8['suffix']))
        lod8_mesh = (far8 if _import_master_mesh(far8, output_meshes_dir,
                                                 master_meshes) else far)
    if flags & 0x10000000:
        far16 = str(_tier_path(Path(far), _TIER16['suffix']))
        lod16_mesh = (far16 if _import_master_mesh(far16, output_meshes_dir,
                                                   master_meshes) else far)
    return far, lod8_mesh, lod16_mesh


# ---------------------------------------------------------------------------
# 3. Build the LODGen input text file
#
# Trees flow through the generic object path, but their _far.nif is a
# crossed-quad billboard card built from Oblivion's shipped billboard render
# (lod_far_gen.generate_tree_billboard_far) rather than decimated geometry —
# vanilla-style flat tree LOD, ~8 verts per instance.  (LODGen's own
# FlatTextures mechanism baked "objpassthru" card shapes into the .bto that
# never rendered in-game; real billboard NIFs use the proven object path.)
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


def write_lodgen_input(esm_path: Path, output_dir: Path,
                       worldspace_edid: str,
                       _parsed=None,
                       cell_sw: tuple = None,
                       master_dirs=None, master_mesh_dirs=None,
                       replace_tiles=False, only_cells=None) -> Path:
    """
    Parse the converted ESM and write the LODGen input text file.

    `master_dirs` lists the converted output dirs of this plugin's MASTERS.
    An override plugin re-uses its masters' records wholesale, so every ref
    whose LOD mesh the master already ships is DROPPED here: the master's own
    LOD run already baked it, and re-baking it would have this plugin ship a
    duplicate copy of the master's entire object LOD to gain the handful of
    objects it actually introduces.

    `master_mesh_dirs` is where MESHES are sourced from, and unlike
    `master_dirs` it is set even when THIS plugin owns the worldspace. LODGen
    resolves every listed mesh under the one PathData root it is given, so a
    master-owned model must be copied into this tree to be listable at all.

    `replace_tiles` turns that off. When this plugin REPLACES whole tiles
    (because it changed cells the master also covers), the tile it writes is
    the only one the engine loads for those cells, so it must contain the
    master's objects as well — otherwise every tree, rock and building in the
    rebuilt tiles disappears. The two modes are mutually exclusive: skip the
    master's objects only when shipping tiles ALONGSIDE the master's.

    `only_cells` restricts the listed references to those that can land in a
    tile this run actually KEEPS (see `_kept_tile_cells`). Without it an
    override plugin lists the master's entire worldspace, LODGen bakes every
    tile, and `_prune_unaffected_tiles` then deletes almost all of them —
    ElsweyrAnequina fed 189,702 references to bake 997 tiles and kept 127;
    DLCBattlehornCastle kept 8. The refs are still needed (replace_tiles means
    the rebuilt tiles must carry the master's objects too), just only within
    the surviving tiles' footprint.

    Returns path to the written file, or None if no LOD refs found.
    """
    if _parsed is not None:
        worldspaces, cells, stats, refs = _parsed
    else:
        print(f"  Parsing ESM: {esm_path.name}")
        worldspaces, cells, stats, refs = _parse_esm(esm_path)

    # Find worldspace form_id
    wrld_fid = None
    wrld_info = None
    for fid, w in worldspaces.items():
        if w['edid'].lower() == worldspace_edid.lower():
            wrld_fid = fid
            wrld_info = w
            break
    if wrld_fid is None:
        # Fall back to first worldspace
        if worldspaces:
            wrld_fid, wrld_info = next(iter(worldspaces.items()))
            print(f"  Warning: worldspace '{worldspace_edid}' not found, "
                  f"using '{wrld_info['edid']}'")
        else:
            print("  Error: no worldspaces found in ESM")
            return None

    edid = wrld_info['edid']
    # Use the effective SW coords from LODSettings if provided; otherwise use raw MNAM values.
    # CellSW= in the LODGen input MUST match the SW in the .lod file.
    if cell_sw is not None:
        sw_x, sw_y = cell_sw
    else:
        sw_x = wrld_info['sw_x']
        sw_y = wrld_info['sw_y']
    # LODGen resolves every listed mesh under the single PathData root, so a
    # mesh may only be listed if it exists in THIS output dir — a path that
    # resolves in some other plugin's tree makes LODGen abort with "file not
    # found" (exit 404) and no tiles at all get baked.
    output_meshes_dir = output_dir / 'meshes'
    # Two different questions, two different lists (see the docstring):
    #   owned_meshes  — does a master already SHIP LOD for this base? (skip it)
    #   master_meshes — where can this base's meshes be SOURCED from? (import)
    owned_meshes  = [Path(d) / 'meshes' for d in (master_dirs or [])]
    master_meshes = [Path(d) / 'meshes' for d in (master_mesh_dirs or [])]

    # Index cells by form_id → parent_wrld for fast lookup
    cell_wrld = {fid: c['parent_wrld'] for fid, c in cells.items()}

    # Cells whose objects can still reach a tile that survives pruning. Refs
    # outside this footprint are baked into tiles that are deleted immediately
    # afterwards, so screening and listing them is pure waste.
    keep_cells = _kept_tile_cells(only_cells) if only_cells else None

    # Collect exterior REFR records in this worldspace whose base is a STAT/ACTI/etc.
    lines = []
    skipped_unsafe = set()
    # Per-BASE memo. Tamriel has 180,702 LOD references but only ~900 distinct
    # bases, and resolving a base's LOD tiers stats several files while
    # screening its meshes fully parses them. Doing that per REFERENCE instead
    # of per base is what made this loop appear to hang.
    base_cache = {}

    for ref in refs:
        # Must be in our worldspace
        if ref['parent_wrld'] != wrld_fid:
            pc = ref['parent_cell']
            if cell_wrld.get(pc, 0) != wrld_fid:
                continue

        # Drop refs that cannot land in a surviving tile. Position decides the
        # cell, not the parent CELL record: an override plugin's refs are
        # merged from two files and a ref's own cell is not always present in
        # `cells`, while its X/Y always place it on the grid. Skyrim cell size
        # is 4096 units and floor division is correct for negatives.
        if keep_cells is not None:
            if (int(math.floor(ref['x'] / 4096.0)),
                    int(math.floor(ref['y'] / 4096.0))) not in keep_cells:
                continue

        base_fid = ref['base_fid']
        if base_fid not in stats:
            continue

        stat = stats[base_fid]
        model = stat.get('model', '')
        if not model:
            continue

        stat_flags_val = stat.get('flags', 0)
        stat_is_lod = bool(stat_flags_val & (_FLAG_DISTANT_LOD | _FLAG_WORLD_MAP))
        if not stat_is_lod:
            continue
        # Resolve this BASE once (see base_cache): which LOD meshes it uses,
        # whether they are safe for LODGen, and whether a master already
        # covers it.
        #
        # That last skip is only valid when this plugin ships tiles ALONGSIDE
        # the master's. When it REPLACES whole tiles (replace_tiles), the tile
        # it writes is the only one the engine loads for those cells, so it
        # must contain the master's objects too — skipping them deleted every
        # tree, rock and building from the rebuilt tiles (74 KB against the
        # master's 9.8 MB).
        base_entry = base_cache.get(base_fid, _MISSING)
        if base_entry is _MISSING:
            base_entry = None
            skip = (not replace_tiles
                    and any(_mesh_exists(_far_nif_path(model), m)
                            for m in owned_meshes))
            if not skip:
                lod4, lod8, lod16 = _lod_meshes_for(
                    stat, output_meshes_dir, master_meshes)
                if lod4 or lod8 or lod16:
                    # The FULL model is listed too (LODGen falls back to it),
                    # so it must exist in THIS tree — a master-owned model is
                    # otherwise absent, and screening reads "missing" as
                    # "unsafe" and drops the object entirely. That is how
                    # ElsweyrAnequina lost 882 meshes' worth of object LOD
                    # while their _far.nif files sat here perfectly readable.
                    _import_master_mesh(model, output_meshes_dir,
                                        master_meshes)
                    # One mesh LODGen cannot parse aborts the whole
                    # worldspace, so screen each listed mesh (and the full
                    # model it falls back to) up front.
                    unsafe = [m for m in (model, lod4, lod8, lod16)
                              if m and not _lod_mesh_is_safe(
                                  m, output_meshes_dir)]
                    if unsafe:
                        for m in unsafe:
                            skipped_unsafe.add(_normalize(m))
                    else:
                        stat_edid = stat.get('edid', f'{base_fid:08X}')
                        base_entry = (
                            f"{stat_edid}\t{stat_flags_val:08X}\t\t"
                            f"{_normalize(model)}\t{_normalize(lod4)}\t"
                            f"{_normalize(lod8)}\t{_normalize(lod16)}")
            base_cache[base_fid] = base_entry
        if base_entry is None:
            continue

        # Reference line
        ref_fid   = f"{ref['form_id']:08X}"
        ref_flags = f"{ref['flags']:08X}"
        scale     = ref['scale']
        # Rotations in ESM are radians; LODGen expects degrees
        rx = math.degrees(ref['rx'])
        ry = math.degrees(ref['ry'])
        rz = math.degrees(ref['rz'])

        line = (f"{ref_fid}\t{ref_flags}\t"
                f"{ref['x']:.4f}\t{ref['y']:.4f}\t{ref['z']:.4f}\t"
                f"{rx:.4f}\t{ry:.4f}\t{rz:.4f}\t"
                f"{scale:.4f}\t{base_entry}")
        lines.append(line)

    if skipped_unsafe:
        print(f"  WARNING: {len(skipped_unsafe)} LOD mesh(es) excluded — "
              f"unreadable or non-NiNode root (would crash LODGen and lose "
              f"ALL of this worldspace's object LOD):")
        for m in sorted(skipped_unsafe)[:10]:
            print(f"    {m}")
        if len(skipped_unsafe) > 10:
            print(f"    ... and {len(skipped_unsafe) - 10} more")

    if not lines:
        print(f"  No LOD references found for worldspace '{edid}'")
        return None

    # Build header.
    # PathData points to our output directory so LODGen finds the extracted
    # _far.nif meshes there rather than looking in the Skyrim SE Data folder.
    # Must have a trailing backslash or LODGen will concatenate without a separator.
    # Resolve to absolute — LODGen runs with cwd=tools/ so a relative PathData
    # ("output\...") would fail its Data-directory existence check, and a
    # relative PathOutput would silently write the .bto under tools\.
    dest      = (Path(output_dir).resolve() / 'meshes' / 'terrain' / edid
                 / 'Objects')
    path_data = str(Path(output_dir).resolve()).rstrip('\\/') + '\\'
    header = [
        f"GameMode=TES5",
        f"Worldspace={edid}",
        f"CellSW={sw_x} {sw_y}",
        f"PathData={path_data}",
        f"PathOutput={dest}",
    ]

    out_txt = LODGEN_EXE.parent / f"LODGen {edid}.txt"
    with open(out_txt, 'w', encoding='utf-8') as f:
        f.write('\n'.join(header) + '\n')
        f.write('\n'.join(lines) + '\n')

    if keep_cells is not None:
        print(f"  Restricted to the {len(keep_cells)} cell(s) covered by the "
              f"tiles this run keeps: {len(lines)} of {len(refs)} references "
              f"listed")
    print(f"  LODGen input: {out_txt} ({len(lines)} references)")
    return out_txt


_LODGEN_ERR_RE = _re.compile(r'Error processing (\S+)')


def run_lodgen(lodgen_input: Path, output_dir: Path) -> bool:
    """Invoke LODGen to bake the worldspace's object-LOD .bto tiles.

    `LODGenx64.exe` is 3.0.36.0. It replaced the 2.2.0.0 build because 2.2
    handles no exceptions: a model it cannot parse throws on a ThreadPool
    worker and kills the whole process, so every tile not yet written is
    silently lost. Measured on Nehrim: 28 of 418 tiles baked, twice in a
    row, because of ONE model
    (`LeyawiinHouseLower01`, 5 references in the entire game). 3.x catches the
    same `ArgumentOutOfRangeException` per object, prints
    `Error processing <EditorID>`, and carries on with the rest — so one bad
    model costs only its own LOD copy (it pops in at load distance) instead of
    the entire worldspace.

    The fault is inside LODGen, not the mesh: repairing that model's tangent
    flag and recomputing its missing normals each made 2.2 crash EARLIER.

    Verified equivalent, not merely tolerable: on the same input 3.x emits the
    same tile set with the same block structure as 2.2 (39 BSSegmentedTriShape
    / BSMultiBoundNode per tile, NIF 20.2.0.7), differing only in slightly
    tighter mesh reduction.

    Note 3.x's exit code is NOT a success signal — it returns 0 on a clean run
    but a nonzero value when any object failed, even though the bake completed
    and every tile was written. Success is therefore judged by tiles produced.
    """
    if not LODGEN_EXE.exists():
        print(f"  ERROR: LODGen not found at {LODGEN_EXE}")
        return False

    # PathOutput is embedded in the input file; LODGen reads it from there.
    cmd = [
        str(LODGEN_EXE),
        str(lodgen_input),
        "--dontFixTangents",
        "--removeUnseenFaces",
        # --skyblivionTexPath is NOT used: it prepends an extra 'tes4\\' to texture paths
        # already under textures\\tes4\\, doubling the prefix and causing null-ptr crashes.
    ]
    print(f"  Running: {' '.join(cmd)}")
    # Capture output so it reaches the GUI log instead of a popped-up console
    # window (which never exists under the console-less GUI launcher).
    result = subprocess.run(windows_cmd(cmd), cwd=str(LODGEN_EXE.parent),
                            capture_output=True, text=True, **POPEN_FLAGS)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")

    # Objects LODGen could not parse. It skipped them and kept going; report
    # them so a model that loses its distant LOD is visible in the log rather
    # than silently absent in-game.
    skipped = sorted(set(_LODGEN_ERR_RE.findall(
        (result.stdout or "") + (result.stderr or ""))))
    if skipped:
        print(f"  WARNING: LODGen could not process {len(skipped)} model(s); "
              f"they have no distant LOD and will pop in at load distance: "
              f"{', '.join(skipped)}")

    # Tiles on disk are the only trustworthy success signal (see docstring).
    tiles = _lodgen_output_dir(lodgen_input)
    baked = len(list(tiles.glob('*.bto'))) if tiles else 0
    if not baked:
        print(f"  WARNING: LODGen produced no .bto tiles "
              f"(exit code {result.returncode})")
        return False
    return True


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


# ---------------------------------------------------------------------------
# 5. Top-level orchestration
# ---------------------------------------------------------------------------

def generate_lod(esm_path: Path, output_dir: Path,
                 worldspace_edid: str = 'Tamriel',
                 master_dirs=None, master_texture_dirs=None,
                 master_mesh_dirs=None,
                 overlay_paths=None, only_cells=None) -> bool:
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
        master_texture_dirs: Converted output dirs of this plugin's masters,
                           always. A plugin regularly places a master's models
                           in its OWN worldspace, and their textures exist only
                           in the master's output; the .bto tiles baked here
                           still reference them, so they are copied in.

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
    worldspaces, cells, stats, refs = _parse_esm_cached(esm_path)
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
        o_wrld, o_cells, o_stats, o_refs = _parse_esm(ov_path)
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
    own_meshes_root = output_dir / 'meshes'
    master_meshes = [Path(d) / 'meshes' for d in (master_mesh_dirs or [])]
    if master_meshes:
        before = len(referenced_models)
        referenced_models = {
            m for m in referenced_models
            if _overrides_master_model(m, own_meshes_root, master_meshes)
            or not any(_mesh_exists(_far_nif_path(m), mm) for mm in master_meshes)
        }
        skipped = before - len(referenced_models)
        if skipped:
            print(f"  Reusing {skipped} master _far.nif LOD mesh(es); "
                  f"generating only this plugin's "
                  f"{len(referenced_models)}")

    from .lod_far_gen import generate_missing_far_nifs
    generate_missing_far_nifs(stats, output_dir / 'meshes',
                               referenced_models=referenced_models,
                               force_regen_generated=True,
                               tex_root=output_dir / 'textures')

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
    _fill_missing_lod_textures(
        objects_dir, _textures_root(output_dir),
        master_tex_roots=[_textures_root(Path(d))
                          for d in (master_texture_dirs or master_dirs or [])])

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


def _textures_root(plugin_out_dir: Path) -> Path:
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


def _fill_missing_lod_textures(bto_dir: Path, tex_root: Path,
                               master_tex_roots=None):
    """Create the LOD textures the .bto tiles reference but that don't exist.

    Mostly these are NORMAL maps: LODGen writes each atlas diffuse
    (<name>_a.dds) but no matching atlas normal (<name>_a_n.dds), and object LOD
    renders unlit against a missing _n.  Each one is written at the exact path
    the .bto asks for, built from the atlas's source normal when there is one
    (single-texture atlas) and otherwise a flat normal sized to the diffuse.

    A plugin can also bake a MASTER's models into its own LOD (Morrowind_ob
    places Oblivion architecture in its worldspace), and those diffuse textures
    live only in the master's output.  A .bto references a texture by PATH, and
    Data holds exactly ONE file per path — the master already ships that path,
    so the child's tiles resolve against the master's copy.  Copying it in
    would duplicate the master's asset byte-for-byte (828 files, 165 MB for
    ElsweyrAnequina) to gain nothing, so a texture a master already ships is
    left to the master and only genuinely absent ones are handled here.
    """
    def _in_master(rel):
        return any(_win_join(mr, rel).exists() for mr in (master_tex_roots or []))

    missing = sorted(r for r in _bto_texture_refs(bto_dir)
                     if not _win_join(tex_root, r).exists() and not _in_master(r))
    if not missing:
        return

    synth = 0
    unresolved = []
    for rel in missing:
        dest = _win_join(tex_root, rel)
        if not rel.endswith('_n.dds'):
            # Nothing to copy: a master-shipped path was filtered out above,
            # so anything reaching here exists in no tree we know of.
            unresolved.append(rel)
            continue
        stem = rel[:-len('_n.dds')]              # 'tes4\...\lcstone01_a'
        # An atlas ('..._a') borrows the normal of the texture it was built from.
        base = stem[:-2] if stem.endswith('_a') else stem

        # Look in this plugin's textures first, then any master's — a master's
        # model baked into our LOD keeps its textures in the master's output,
        # and using its real normal beats falling back to a flat one.
        def _find(name):
            p = _win_join(tex_root, name)
            if p.exists():
                return p
            for mr in (master_tex_roots or []):
                q = _win_join(mr, name)
                if q.exists():
                    return q
            return p          # non-existent local path (callers test .exists())

        src_normal = _find(f'{base}_n.dds')
        diffuse = _find(f'{stem}.dds')
        if not diffuse.exists():
            diffuse = _find(f'{base}.dds')
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if src_normal.exists() and src_normal != dest:
                shutil.copy2(src_normal, dest)
            else:
                _write_flat_normal_for(diffuse, dest)
            synth += 1
        except Exception:
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
    from PIL import Image
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
