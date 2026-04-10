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
    SCRIPT_DIR / "external" / "xEdit" / "Build" / "Edit Scripts" / "LODGenx64.exe"
)
SSELODGEN_EXE = SCRIPT_DIR / "tools" / "sseLodGen.exe"


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
_FLAG_COMP = 0x00040000
_FLAG_VWD  = 0x00008000   # Visible When Distant on STAT record
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
            stats[fid] = {
                'edid': edid,
                'flags': rec['flags'],
                'model': model,
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

    Paths in the converted ESM are stored without the leading 'meshes\\' folder
    (e.g. 'tes4\\Architecture\\foo.nif').  LODGen expects paths relative to the
    Data folder (e.g. 'meshes\\tes4\\architecture\\foo.nif'), so we add the
    prefix here if it is not already present.
    """
    p = path.lower().replace('/', '\\').strip('\\')
    if p and not p.startswith('meshes\\'):
        p = 'meshes\\' + p
    return p


def _lod_meshes_for(stat: dict, output_meshes_dir: Path):
    """
    Return (lod4, lod8, lod16) mesh paths for a stat record.

    Priority:
      1. Explicit MNAM LOD entries from the STAT record
      2. _far.nif variant of the full model (if the file exists on disk)
      3. Full model as fallback (LODGen will use it directly)
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
    far_on_disk = output_meshes_dir / far
    if far_on_disk.exists():
        return far, far, far

    # Use full model as LOD — LODGen will just place it unchanged
    return model, model, model


# ---------------------------------------------------------------------------
# 3. Build the LODGen input text file
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
    seen_bases = set()

    for ref in refs:
        # Must be in our worldspace
        if ref['parent_wrld'] != wrld_fid:
            # Also accept refs whose parent_cell is in this worldspace
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

        lod4, lod8, lod16 = _lod_meshes_for(stat, output_meshes_dir)
        if not (lod4 or lod8 or lod16):
            continue

        # Include refs that have:
        #   a) an explicit MNAM LOD mesh,
        #   b) a _far.nif companion on disk, OR
        #   c) the VWD (IsVisibleWhenDistant) flag on the base STAT record —
        #      xEdit includes all VWD statics even if they only have the full model.
        # Skip refs with no LOD mesh AND no VWD flag to avoid feeding LODGen the
        # entire full-res world geometry.
        model_norm = _normalize(model)
        has_dedicated_lod = (
            stat.get('lod4') or stat.get('lod8') or stat.get('lod16')
            or (lod4 and _normalize(lod4) != model_norm)
        )
        stat_is_vwd = bool(stat.get('flags', 0) & _FLAG_VWD)
        if not has_dedicated_lod and not stat_is_vwd:
            continue

        # Build the base-object cache entry (tab-separated)
        mat = ''
        stat_edid   = stat.get('edid', f'{base_fid:08X}')
        stat_flags  = f"{stat.get('flags', 0):08X}"
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
    dest      = output_dir / 'meshes' / 'terrain' / edid / 'Objects'
    path_data = str(output_dir).rstrip('\\/') + '\\'
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


# ---------------------------------------------------------------------------
# 4. Run LODGenx64.exe
# ---------------------------------------------------------------------------

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
        "--skyblivionTexPath",   # rewrite 'textures\' → 'textures\tes4\' in LOD nifs
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

    wrld_info = None
    for fid, w in worldspaces.items():
        if w['edid'].lower() == worldspace_edid.lower():
            wrld_info = w
            break
    if wrld_info is None and worldspaces:
        wrld_info = next(iter(worldspaces.values()))
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

    # Write LODGen input (reuse already-parsed data).
    # Pass the effective SW so CellSW= matches the .lod file.
    lodgen_txt = write_lodgen_input(esm_path, output_dir, edid,
                                    _parsed=(worldspaces, cells, stats, refs),
                                    cell_sw=(eff_sw_x, eff_sw_y))
    if lodgen_txt is None:
        return False  # No references — not an error per se, just nothing to do

    # Run LODGen
    ok = run_lodgen(lodgen_txt, output_dir)

    # LOD object textures are referenced by bare filename in the .bto files
    # (e.g. 'CastleWallLOD01.dds'), so Skyrim looks them up as textures\<name>.dds.
    # The extraction pipeline puts them in subdirectories; promote them to the root.
    # LOD textures are referenced by bare filename in .bto files (e.g. 'CastleWallLOD01.dds').
    # They live in output/.../textures/tes4/<subdir>/ after asset_pipeline copies them.
    # Skyrim looks them up as textures\<basename>.dds, so copy them to the textures root.
    _promote_lod_textures(objects_dir, output_dir / 'textures', output_dir / 'textures' / 'tes4')

    if ok:
        print(f"[LOD] Object LOD generation complete.")
    else:
        print(f"[LOD] LOD generation finished with warnings.")
    return ok


def generate_terrain_lod(esm_path: Path, output_dir: Path,
                         worldspace_edid: str = 'TES4Tamriel') -> bool:
    """Launch sseLodGen.exe to generate terrain LOD (.btr files).

    sseLodGen is an xEdit fork that generates terrain LOD from the LAND records
    in the plugin.  It requires a GUI click to confirm options, but this function
    pre-populates its settings INI so everything is ready to go — the user only
    needs to click OK.

    The LODSettings/<worldspace>.lod file must already exist (written by
    generate_lod / write_lod_settings) before calling this.

    Args:
        esm_path:        Path to the converted .esm/.esp in output_dir.
        output_dir:      Per-plugin output directory (output/Oblivion.esm/).
        worldspace_edid: Editor ID of the worldspace to generate terrain LOD for.

    Returns True if sseLodGen was launched successfully, False otherwise.
    """
    if not SSELODGEN_EXE.exists():
        print(f"  ERROR: sseLodGen.exe not found at {SSELODGEN_EXE}")
        return False

    lod_file = output_dir / 'LODSettings' / f'{worldspace_edid}.lod'
    if not lod_file.exists():
        print(f"  ERROR: LODSettings/{worldspace_edid}.lod not found — run generate_lod first")
        return False

    # -----------------------------------------------------------------------
    # Pre-populate the sseLodGen settings INI.
    # Settings file lives next to the exe: sseLodGenLODGen.ini
    # (wbAppName=SSE from exe name, wbToolName=LODGen)
    # -----------------------------------------------------------------------
    ini_path = SSELODGEN_EXE.parent / 'sseLodGenLODGen.ini'
    section = 'SSE LOD Options'
    _write_sselodgen_ini(ini_path, section, worldspace_edid)

    # -----------------------------------------------------------------------
    # Build command line.
    # -sse       — game mode (also detected from exe name, but be explicit)
    # -lodgen    — tool mode (also detected from exe name)
    # -D:<path>  — data path (our output_dir, so it finds the LODSettings file
    #              and the plugin)
    # -O:<path>  — output path (same: write .btr files here)
    # -autoload  — skip the plugin selection dialog, load all in data path
    # plugin     — positional: the file to load
    # -----------------------------------------------------------------------
    cmd = [
        str(SSELODGEN_EXE),
        f'-D:{output_dir}',
        f'-O:{output_dir}',
        '-autoload',
        esm_path.name,
    ]

    print(f"\n[TerrainLOD] Launching sseLodGen for terrain LOD generation.")
    print(f"  Settings pre-populated in: {ini_path}")
    print(f"  Data/output dir: {output_dir}")
    print(f"  Worldspace: {worldspace_edid} (pre-selected, click OK to generate)")
    print(f"  Running: {' '.join(cmd)}")

    result = subprocess.run(cmd, cwd=str(SSELODGEN_EXE.parent))
    if result.returncode != 0:
        print(f"  WARNING: sseLodGen exited with code {result.returncode}")
        return False

    # Check that .btr files were actually produced
    btr_dir = output_dir / 'meshes' / 'terrain' / worldspace_edid
    btr_files = list(btr_dir.glob('*.btr'))
    if btr_files:
        print(f"[TerrainLOD] Generated {len(btr_files)} .btr files.")
    else:
        print(f"[TerrainLOD] WARNING: no .btr files found in {btr_dir}")

    return True


def _write_sselodgen_ini(ini_path: Path, section: str, worldspace_edid: str):
    """Write sseLodGen settings INI with terrain LOD options pre-set."""
    import configparser

    cfg = configparser.ConfigParser()
    if ini_path.exists():
        cfg.read(ini_path, encoding='utf-8')

    if not cfg.has_section(section):
        cfg.add_section(section)

    # Terrain LOD on, objects/trees off (we handle objects separately)
    cfg.set(section, 'ObjectsLOD', '0')
    cfg.set(section, 'TreesLOD', '0')
    cfg.set(section, 'TerrainLOD', '1')

    # Standard terrain LOD quality settings
    cfg.set(section, 'TerrainLODResolution', '32')   # vertices per side per tile
    cfg.set(section, 'TerrainLODQuality', '2')        # medium
    cfg.set(section, 'TerrainLOD4', '1')
    cfg.set(section, 'TerrainLOD8', '1')
    cfg.set(section, 'TerrainLOD16', '1')
    cfg.set(section, 'TerrainLOD32', '0')
    cfg.set(section, 'TerrainUnderWater', '1')
    cfg.set(section, 'TerrainLODDiffuseFormat', 'DXT1')
    cfg.set(section, 'TerrainLODNormalFormat', 'BC5')

    ini_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ini_path, 'w', encoding='utf-8') as f:
        cfg.write(f)


def _promote_lod_textures(bto_dir: Path, tex_root: Path, search_dir: Path):
    """Copy LOD textures from search_dir to tex_root/<basename>.dds.

    .bto files reference LOD textures by bare filename (e.g. 'CastleWallLOD01.dds').
    Skyrim resolves these from the textures root (textures/<basename>.dds).
    The converted textures live under search_dir (output/.../textures/tes4/) in
    subdirectories matching the BSA folder structure, so we copy them up to tex_root.
    """
    import re as _re
    import shutil

    needed = set()
    for bto in bto_dir.glob("*.bto"):
        for m in _re.finditer(rb'[A-Za-z0-9_]{3,}\.dds', bto.read_bytes(), _re.IGNORECASE):
            needed.add(m.group(0).decode('latin-1').lower())

    if not needed:
        return

    if not search_dir.exists():
        print(f"  WARNING: textures/tes4/ not found — run asset_pipeline before LOD generation")
        return

    copied = 0
    missing = set()
    for name in needed:
        dest = tex_root / name
        if dest.exists():
            continue
        found = next(search_dir.rglob(name), None)
        if found:
            tex_root.mkdir(parents=True, exist_ok=True)
            shutil.copy2(found, dest)
            copied += 1
        else:
            missing.add(name)

    if copied:
        print(f"  Promoted {copied} LOD textures to textures root.")
    if missing:
        print(f"  WARNING: {len(missing)} LOD textures not found in textures/tes4/: "
              + ", ".join(sorted(missing)[:5]) + ("..." if len(missing) > 5 else ""))


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
