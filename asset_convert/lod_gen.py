"""
LOD generation for converted TES4→TES5 worldspaces.

Workflow:
  1. write_lod_settings()  — write LODSettings/<WRLD>.lod (required by LODGen.exe)
  2. write_lodgen_input()  — scan the converted ESM, emit the LODGen data text file
  3. run_lodgen()          — call LODGenx64.exe to bake object LOD NIFs

All three are orchestrated by generate_lod(), which convert.py calls as Phase 4.
"""

import math
import os
import struct
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.parent.resolve()
LODGEN_EXE = (
    SCRIPT_DIR / "tools" / "LODGenx64.exe"
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

    # Round SW down and NE up to the nearest power of 2 boundary
    raw_w = ne_x - sw_x
    raw_h = ne_y - sw_y
    size = 1 << math.ceil(math.log2(max(raw_w, raw_h, 1)))
    # Centre the grid: expand SW symmetrically
    eff_sw_x = -(size // 2)
    eff_sw_y = -(size // 2)

    out.write_bytes(struct.pack("<hhIII", eff_sw_x, eff_sw_y, size, 4, 32))
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
    return (output_meshes_dir / rel).exists()


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


def _lod_meshes_for(stat: dict, output_meshes_dir: Path):
    """
    Return (lod4, lod8, lod16) mesh paths for a stat record.

    - Trees use their billboard-card _far.nif at every level — the cards are
      8 verts each, so distant forests stay visible for almost no cost.
    - Other LOD objects (0x8000) get lod4; lod8 only if they're big enough
      to matter at level-8 distances (_LOD8_MIN_SIZE).
    - World-map objects (0x10000000) additionally get lod16 so LODGenx64
      bakes tiles for the far ring / world-map view.
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
    if not _mesh_exists(far, output_meshes_dir):
        return '', '', ''

    from .lod_far_gen import is_tree_model, _tier_path, _TIER8, _TIER16
    if is_tree_model(stat):
        return far, far, far

    flags = stat.get('flags', 0)
    lod8_mesh = lod16_mesh = ''
    if _obnd_max_dim(stat) >= _LOD8_MIN_SIZE:
        far8 = str(_tier_path(Path(far), _TIER8['suffix']))
        lod8_mesh = far8 if _mesh_exists(far8, output_meshes_dir) else far
    if flags & 0x10000000:
        far16 = str(_tier_path(Path(far), _TIER16['suffix']))
        lod16_mesh = far16 if _mesh_exists(far16, output_meshes_dir) else far
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


def write_lodgen_input(esm_path: Path, output_dir: Path,
                       worldspace_edid: str,
                       _parsed=None,
                       cell_sw: tuple = None) -> Path:
    """
    Parse the converted ESM and write the LODGen input text file.

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
    output_meshes_dir = output_dir / 'meshes'

    # Index cells by form_id → parent_wrld for fast lookup
    cell_wrld = {fid: c['parent_wrld'] for fid, c in cells.items()}

    # Collect exterior REFR records in this worldspace whose base is a STAT/ACTI/etc.
    lines = []

    for ref in refs:
        # Must be in our worldspace
        if ref['parent_wrld'] != wrld_fid:
            pc = ref['parent_cell']
            if cell_wrld.get(pc, 0) != wrld_fid:
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
        lod4, lod8, lod16 = _lod_meshes_for(stat, output_meshes_dir)
        if not (lod4 or lod8 or lod16):
            continue
        mat = ''
        stat_edid   = stat.get('edid', f'{base_fid:08X}')
        stat_flags  = f"{stat_flags_val:08X}"
        base_entry  = f"{stat_edid}\t{stat_flags}\t{mat}\t{_normalize(model)}\t{_normalize(lod4)}\t{_normalize(lod8)}\t{_normalize(lod16)}"

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

    print(f"  LODGen input: {out_txt} ({len(lines)} references)")
    return out_txt


def run_lodgen(lodgen_input: Path, output_dir: Path) -> bool:
    """Invoke LODGenx64.exe on the prepared input file."""
    if not LODGEN_EXE.exists():
        print(f"  ERROR: LODGenx64.exe not found at {LODGEN_EXE}")
        return False

    # Ensure output terrain/Objects dir exists (LODGen may not create it)
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
    result = subprocess.run(cmd, cwd=str(LODGEN_EXE.parent))
    if result.returncode != 0:
        print(f"  WARNING: LODGenx64.exe exited with code {result.returncode}")
        return False
    return True


# ---------------------------------------------------------------------------
# 5. Top-level orchestration
# ---------------------------------------------------------------------------

def generate_lod(esm_path: Path, output_dir: Path,
                 worldspace_edid: str = 'Tamriel') -> bool:
    """
    Full LOD generation pipeline:
      1. Write LODSettings/<worldspace>.lod
      2. Parse ESM → LODGen input text
      3. Run LODGenx64.exe

    Args:
        esm_path:          Path to the converted .esm/.esp
        output_dir:        The per-plugin output directory (contains meshes/, textures/, etc.)
        worldspace_edid:   Editor ID of the worldspace to generate LOD for

    Returns True on success.
    """
    print(f"\n[LOD] Generating object LOD for worldspace '{worldspace_edid}'")

    # Parse ESM once; reuse data for both LODSettings and LODGen input
    print(f"  Parsing ESM: {esm_path.name}")
    worldspaces, cells, stats, refs = _parse_esm(esm_path)

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
    _, eff_sw_x, eff_sw_y = write_lod_settings(
        edid,
        wrld_info['sw_x'], wrld_info['sw_y'],
        wrld_info['ne_x'], wrld_info['ne_y'],
        output_dir,
    )

    # Ensure Objects output dir exists
    objects_dir = output_dir / 'meshes' / 'terrain' / edid / 'Objects'
    objects_dir.mkdir(parents=True, exist_ok=True)

    # Generate _far.nif LOD meshes for any LOD-flagged objects that don't have one.
    # Only process models that are actually placed in this worldspace.
    # Must happen before writing the LODGen input so the new files are found.
    cell_wrld_map = {fid: c['parent_wrld'] for fid, c in cells.items()}
    referenced_models = set()
    for ref in refs:
        pw = ref['parent_wrld']
        if pw != wrld_fid and cell_wrld_map.get(ref['parent_cell'], 0) != wrld_fid:
            continue
        base_fid = ref['base_fid']
        if base_fid in stats:
            m = stats[base_fid].get('model', '')
            if m:
                referenced_models.add(m)

    from .lod_far_gen import generate_missing_far_nifs
    generate_missing_far_nifs(stats, output_dir / 'meshes',
                               referenced_models=referenced_models,
                               force_regen_generated=True,
                               tex_root=output_dir / 'textures')

    # Write LOD input (all LOD-flagged objects) and run LODGenx64 once
    lodgen_txt = write_lodgen_input(esm_path, output_dir, edid,
                                    _parsed=(worldspaces, cells, stats, refs),
                                    cell_sw=(eff_sw_x, eff_sw_y))
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

    # Promote LOD object textures from meshes/tes4/ subdirectories to the
    # textures root so .bto files can find them by bare filename.
    _promote_lod_textures(objects_dir, output_dir / 'textures',
                          output_dir / 'meshes' / 'tes4')

    if ok:
        print(f"[LOD] Object LOD generation complete.")
    else:
        print(f"[LOD] LOD generation finished with warnings.")
    return ok


def _promote_lod_textures(bto_dir: Path, tex_root: Path, search_dir: Path):
    """Copy LOD textures referenced by .bto files up to the textures root.

    .bto files reference LOD textures by bare filename (e.g. 'CastleWallLOD01.dds').
    Skyrim resolves these from the textures root (textures\\<basename>.dds).
    Converted textures live in subdirectories under *search_dir* (e.g.
    output/.../meshes/tes4/ or output/.../textures/tes4/), so we recursively
    search both and copy missing textures to *tex_root*.
    Also searches *tex_root* itself recursively if a texture lives in a subdir there.
    """
    import re as _re
    import shutil

    needed = set()
    for bto in bto_dir.glob("*.bto"):
        for m in _re.finditer(rb'[A-Za-z0-9_]{3,}\.dds', bto.read_bytes(), _re.IGNORECASE):
            needed.add(m.group(0).decode('latin-1').lower())

    if not needed:
        return

    # Build a name → path index from both search_dir and tex_root subdirs
    index: dict = {}
    for sd in [search_dir, tex_root]:
        if sd.exists():
            for dds in sd.rglob('*.dds'):
                key = dds.name.lower()
                if key not in index:
                    index[key] = dds

    copied = 0
    missing = set()
    for name in needed:
        dest = tex_root / name
        if dest.exists():
            continue
        src = index.get(name)
        if src:
            tex_root.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            copied += 1
        else:
            missing.add(name)

    if copied:
        print(f"  Promoted {copied} LOD textures to textures root.")

    # Synthesize missing atlas NORMAL maps (<name>_a_n.dds).  LODGen writes the
    # atlas diffuse (<name>_a.dds) but here does not emit the atlas normal, so
    # object LOD would reference a missing _n and render unlit.  For each needed
    # _a_n we build it from the atlas's source normal (single-texture atlas) or
    # fall back to a flat normal at the atlas resolution.
    synth = 0
    still_missing = set()
    for name in list(missing):
        # Any missing NORMAL map referenced by a .bto: object LOD renders unlit
        # (or the engine can choke) without it.  Build one from the best source
        # normal we can find, else a flat normal sized to the paired diffuse.
        if not name.endswith('_n.dds'):
            still_missing.add(name)
            continue
        dest = tex_root / name
        stem = name[:-len('_n.dds')]             # e.g. 'lcstone01_a' or 'brumawoodpost_grey'
        # candidate source normals: the non-atlas normal of the same base
        base = stem[:-2] if stem.endswith('_a') else stem
        src_normal = index.get(f'{base}_n.dds')
        diffuse = (index.get(f'{stem}.dds') or index.get(f'{base}.dds')
                   or (tex_root / f'{stem}.dds'))
        try:
            if src_normal and src_normal.exists() and src_normal != dest:
                shutil.copy2(src_normal, dest)
            else:
                _write_flat_normal_for(diffuse, dest)
            synth += 1
        except Exception:
            still_missing.add(name)

    if synth:
        print(f"  Synthesized {synth} object-LOD normal maps.")
    if still_missing:
        print(f"  WARNING: {len(still_missing)} LOD textures not found: "
              + ", ".join(sorted(still_missing)[:5])
              + ("..." if len(still_missing) > 5 else ""))


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
