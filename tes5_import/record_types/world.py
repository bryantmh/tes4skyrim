"""World/cell converters: LTEX, CELL, WRLD, REFR, ACHR, ACRE, LAND, REGN, LSCR, EFSH."""

import struct

from ..constants import MAP_MARKER_TYPE_MAP, MATT_MAP, map_lock_level
from ..skyrim_overrides import TES4_MARKER_FORMID_TO_SKYRIM
from .common import (
    _prefix_path,
    get_float,
    get_formid,
    get_int,
    get_str,
    pack_float_subrecord,
    pack_formid_subrecord,
    pack_obnd,
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
    pack_uint8_subrecord,
)


def convert_LTEX(rec: dict, writer=None) -> tuple:
    """LTEX — needs companion TXST record in TES5.
    Returns (ltex_bytes, txst_bytes_or_None, txst_formid)."""
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    icon_path = get_str(rec, 'ICON')
    material = get_int(rec, 'HNAM.Material')
    matt_fid = MATT_MAP.get(material, 0x00012F34)

    # Create TXST record
    txst_fid = 0
    txst_bytes = None
    if icon_path and writer:
        txst_fid = writer.alloc_formid()
        txst_subs = b''
        txst_edid = f"TES4_{edid}_TXST" if edid else f"TES4_LTEX_{get_formid(rec, 'FormID'):08X}_TXST"
        txst_subs += pack_string_subrecord('EDID', txst_edid)
        txst_subs += pack_obnd()
        # Oblivion LTEX ICON is relative to Textures\Landscape\ — prepend landscape\
        full_icon = 'landscape\\' + icon_path
        diffuse = _prefix_path(full_icon)
        base_no_ext = diffuse.rsplit('.', 1)[0] if '.' in diffuse else diffuse
        txst_subs += pack_string_subrecord('TX00', diffuse)
        # Normal map (TX01): derive from diffuse with _n suffix
        txst_subs += pack_string_subrecord('TX01', base_no_ext + '_n.dds')
        # No DNAM: landscape TXST records in vanilla Skyrim omit DNAM. The
        # 'No Specular Map' flag (0x0001) only applies to the object shader, not
        # the landscape shader. Writing it causes undefined landscape rendering.
        txst_bytes = pack_record('TXST', txst_fid, 0, txst_subs)

    # TNAM — Texture Set FormID
    if txst_fid:
        subs += pack_formid_subrecord('TNAM', txst_fid)

    # MNAM — Material Type FormID (TES5 uses MNAM, not HNAM, for the MATT reference)
    if matt_fid:
        subs += pack_formid_subrecord('MNAM', matt_fid)

    # HNAM — Havok Data: Friction (U8) + Restitution (U8) = 2 bytes.
    # TES4 LTEX.HNAM has Material(U8)+Friction(U8)+Restitution(U8). In TES5 the
    # material moved to MNAM, so HNAM only carries friction and restitution.
    friction = get_int(rec, 'HNAM.Friction', 30)
    restitution = get_int(rec, 'HNAM.Restitution', 30)
    subs += pack_subrecord('HNAM', struct.pack('<BB', friction, restitution))

    # SNAM — Specular exponent. Passed through from TES4 when present.
    # WARNING: SNAM is a Phong exponent. Setting it to 0 gives pow(NdotH, 0) = 1.0
    # everywhere → the entire landscape becomes blindingly bright white.
    # TES4 landscapes typically use ~30. Leave absent when not in source data.
    spec = get_int(rec, 'SNAM.Specular', -1)
    if spec >= 0:
        subs += pack_uint8_subrecord('SNAM', spec)

    # GNAM — Grass references (one subrecord per GRAS FormID)
    gc = get_int(rec, 'GrassCount')
    for i in range(gc):
        gfid = get_formid(rec, f'Grass[{i}]')
        if gfid:
            subs += pack_formid_subrecord('GNAM', gfid)

    ltex_bytes = pack_record('LTEX', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)
    return ltex_bytes, txst_bytes, txst_fid


def convert_CELL(rec: dict) -> bytes:
    """Convert CELL record."""
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    full = get_str(rec, 'FULL')
    if full:
        subs += pack_string_subrecord('FULL', full)

    # DATA — TES5 uses uint16 flags (not uint8)
    flags = get_int(rec, 'DATA.Flags')
    flags &= ~0x08  # Remove Oblivion interior flag
    flags &= ~0x40  # Remove Hand Changed flag
    subs += pack_subrecord('DATA', struct.pack('<H', flags & 0xFFFF))

    # XCLC — grid coordinates (exterior cells)
    x = get_int(rec, 'XCLC.X', None)
    if x is not None:
        y = get_int(rec, 'XCLC.Y')
        subs += pack_subrecord('XCLC', struct.pack('<iiI', x, y, 0))  # 12 bytes in TES5

    # Interior lighting (XCLL)
    if get_str(rec, 'XCLL.AmbientR'):
        ar = get_int(rec, 'XCLL.AmbientR')
        ag = get_int(rec, 'XCLL.AmbientG')
        ab = get_int(rec, 'XCLL.AmbientB')
        dr = get_int(rec, 'XCLL.DirectionalR')
        dg = get_int(rec, 'XCLL.DirectionalG')
        db = get_int(rec, 'XCLL.DirectionalB')
        fr = get_int(rec, 'XCLL.FogR')
        fg = get_int(rec, 'XCLL.FogG')
        fb = get_int(rec, 'XCLL.FogB')
        fog_near = get_float(rec, 'XCLL.FogNear')
        fog_far = get_float(rec, 'XCLL.FogFar')
        rot_xy = get_int(rec, 'XCLL.DirectionalRotXY')
        rot_z = get_int(rec, 'XCLL.DirectionalRotZ')
        dir_fade = get_float(rec, 'XCLL.DirectionalFade', 1.0)
        clip_dist = get_float(rec, 'XCLL.FogClipDist')

        # TES5 XCLL is 92 bytes — ambient, directional, fog near, fog far,
        # directional rotation, directional fade, fog clip, fog power,
        # ambient colors (X+,X-,Y+,Y-,Z+,Z-), specular, fresnel
        xcll = bytearray(92)
        xcll[0] = ar; xcll[1] = ag; xcll[2] = ab; xcll[3] = 0
        xcll[4] = dr; xcll[5] = dg; xcll[6] = db; xcll[7] = 0
        # Fog near color = same as fog
        xcll[8] = fr; xcll[9] = fg; xcll[10] = fb; xcll[11] = 0
        struct.pack_into('<f', xcll, 12, fog_near)
        struct.pack_into('<f', xcll, 16, fog_far)
        struct.pack_into('<i', xcll, 20, rot_xy)
        struct.pack_into('<i', xcll, 24, rot_z)
        struct.pack_into('<f', xcll, 28, dir_fade)
        struct.pack_into('<f', xcll, 32, clip_dist)
        struct.pack_into('<f', xcll, 36, 1.0)  # Fog power
        # Fog far color = same as fog
        xcll[40] = fr; xcll[41] = fg; xcll[42] = fb; xcll[43] = 0
        # Fog max = 1.0
        struct.pack_into('<f', xcll, 44, 1.0)
        subs += pack_subrecord('XCLL', bytes(xcll))

    # Ownership
    xown = get_formid(rec, 'XOWN.Owner')
    if xown:
        subs += pack_formid_subrecord('XOWN', xown)

    # Water height
    wh = get_str(rec, 'XCLW.WaterHeight')
    if wh:
        subs += pack_float_subrecord('XCLW', get_float(rec, 'XCLW.WaterHeight'))

    return pack_record('CELL', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_WRLD(rec: dict) -> bytes:
    subs = b''
    edid = get_str(rec, 'EditorID')

    # Oblivion's Tamriel (FormID 0x3C) conflicts with Skyrim's Tamriel.
    # After load-order remapping it becomes 0x0100003C which overrides Skyrim's
    # worldspace. Rename to avoid the override.
    if edid == 'Tamriel':
        edid = 'TES4Tamriel'

    if edid:
        subs += pack_string_subrecord('EDID', edid)
    full = get_str(rec, 'FULL')
    if full:
        subs += pack_string_subrecord('FULL', full)

    wnam = get_formid(rec, 'WNAM.Parent')
    if wnam:
        subs += pack_formid_subrecord('WNAM', wnam)

    # TES4 CNAM/NAM2/SNAM reference TES4 records — omit (would be dangling refs)

    # DNAM — land/water defaults
    subs += pack_subrecord('DNAM', struct.pack('<ff', -2048.0, 0.0))

    # Map dimensions (MNAM) — after DNAM per xEdit order
    mnam_str = get_str(rec, 'MNAM.UsableDimX')
    if mnam_str:
        dx = get_int(rec, 'MNAM.UsableDimX')
        dy = get_int(rec, 'MNAM.UsableDimY')
        nwx = get_int(rec, 'MNAM.NWCellX')
        nwy = get_int(rec, 'MNAM.NWCellY')
        sex = get_int(rec, 'MNAM.SECellX')
        sey = get_int(rec, 'MNAM.SECellY')
        # Camera defaults from Skyrim's Tamriel worldspace
        mnam = struct.pack('<iihhhhfff', dx, dy, nwx, nwy, sex, sey, 50000.0, 80000.0, 50.0)
        subs += pack_subrecord('MNAM', mnam)

    # ONAM — World Map Offset Data (after MNAM per xEdit order)
    subs += pack_subrecord('ONAM', struct.pack('<ffff', 1.0, 0.0, 0.0, 0.0))

    # NAMA — Distant LOD multiplier
    subs += pack_float_subrecord('NAMA', 1.0)

    # DATA — flags (after NAMA per xEdit order)
    data_flags = get_int(rec, 'DATA.Flags')
    data_flags &= ~0x04  # Clear Oblivion flag (bit 2)
    # Move No LOD Water: bit $10 → bit $08
    if data_flags & 0x10:
        data_flags = (data_flags & ~0x10) | 0x08
    subs += pack_uint8_subrecord('DATA', data_flags)

    # NAM0 — World Object Bounds Min (X, Y as floats scaled by 1/wbCellSizeFactor=1/4096)
    # NAM9 — World Object Bounds Max. Required by SSELodGen for world map generation.
    # TES4 exports raw coordinates — divide by 4096 to get Skyrim's cell-unit scale.
    n0x_raw = get_float(rec, 'NAM0.MinX')
    n0y_raw = get_float(rec, 'NAM0.MinY')
    n9x_raw = get_float(rec, 'NAM9.MaxX')
    n9y_raw = get_float(rec, 'NAM9.MaxY')
    CELL_SCALE = 4096.0
    subs += pack_subrecord('NAM0', struct.pack('<ff', n0x_raw / CELL_SCALE, n0y_raw / CELL_SCALE))
    subs += pack_subrecord('NAM9', struct.pack('<ff', n9x_raw / CELL_SCALE, n9y_raw / CELL_SCALE))

    return pack_record('WRLD', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_REFR(rec: dict) -> bytes:
    """REFR — placed object reference.

    TES5 order (from wbDefinitionsTES5.pas):
    EDID VMAD NAME XMBO XPRM ... XTEL XLOC XEZN ... XOWN XESP XLKR
    ... XSCL ... XMRK/FNAM/FULL/TNAM ... DATA
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    # NAME = base object FormID (required)
    # For invisible marker base objects, substitute the Skyrim.esm equivalent
    # so REFRs point into Skyrim.esm (index 0) rather than our remapped copy.
    name_raw = int(rec.get('NAME', '0') or '0', 16)
    skyrim_marker = TES4_MARKER_FORMID_TO_SKYRIM.get(name_raw)
    if skyrim_marker is not None:
        name_fid = skyrim_marker  # Already a Skyrim.esm FormID — no offset
    else:
        name_fid = get_formid(rec, 'NAME')
    if name_fid:
        subs += pack_formid_subrecord('NAME', name_fid)

    # Teleport door (XTEL)
    xtel_door = get_formid(rec, 'XTEL.Door')
    if xtel_door:
        px = get_float(rec, 'XTEL.PosX')
        py = get_float(rec, 'XTEL.PosY')
        pz = get_float(rec, 'XTEL.PosZ')
        rx = get_float(rec, 'XTEL.RotX')
        ry = get_float(rec, 'XTEL.RotY')
        rz = get_float(rec, 'XTEL.RotZ')
        # TES5 XTEL is 32 bytes: Door(4) + Pos(12) + Rot(12) + Flags(4)
        # Flags: 0x0001 = No Alarm. Always 0 for converted doors.
        subs += pack_subrecord('XTEL', struct.pack('<IffffffI', xtel_door, px, py, pz, rx, ry, rz, 0))

    # Lock — XLOC is 20 bytes in TES5: Level(1)+pad(3)+Key(4)+Flags(1)+pad(3)+pad(8)
    lock_level = get_int(rec, 'XLOC.Level', -1)
    if lock_level >= 0:
        tes5_level = map_lock_level(lock_level)
        lock_key = get_formid(rec, 'XLOC.Key')
        lock_flags = get_int(rec, 'XLOC.Flags')
        subs += pack_subrecord('XLOC', struct.pack('<BxxxIBxxx8x', tes5_level, lock_key, lock_flags))

    # Ownership (XOWN)
    xown = get_formid(rec, 'XOWN.Owner')
    if xown:
        subs += pack_formid_subrecord('XOWN', xown)

    # Enable parent (XESP)
    xesp_ref = get_formid(rec, 'XESP.Reference')
    if xesp_ref:
        xesp_flags = get_int(rec, 'XESP.Flags')
        subs += pack_subrecord('XESP', struct.pack('<II', xesp_ref, xesp_flags))

    # Scale (XSCL)
    scale = get_float(rec, 'XSCL.Scale')
    if scale and scale != 1.0:
        subs += pack_float_subrecord('XSCL', scale)

    # XTRG does NOT exist in TES5 — skip it entirely

    # Map Marker (XMRK + FNAM + FULL + TNAM)
    if get_str(rec, 'MapMarker') == '1':
        subs += pack_subrecord('XMRK', b'')
        marker_full = get_str(rec, 'MapMarker.FULL')
        if marker_full:
            subs += pack_string_subrecord('FULL', marker_full)
        marker_type = get_int(rec, 'MapMarker.Type')
        tes5_marker = MAP_MARKER_TYPE_MAP.get(marker_type, 0)
        subs += pack_subrecord('TNAM', struct.pack('<BB', tes5_marker, 0))

    # Position/Rotation (DATA)
    px = get_float(rec, 'PosX')
    py = get_float(rec, 'PosY')
    pz = get_float(rec, 'PosZ')
    rx = get_float(rec, 'RotX')
    ry = get_float(rec, 'RotY')
    rz = get_float(rec, 'RotZ')
    subs += pack_subrecord('DATA', struct.pack('<ffffff', px, py, pz, rx, ry, rz))

    flags = get_int(rec, 'RecordFlags')
    if get_int(rec, 'VWD') == 1:
        flags |= 0x8000  # Visible When Distant — required for SSELodGen object LOD
    return pack_record('REFR', get_formid(rec, 'FormID'), flags, subs)


def convert_ACHR(rec: dict) -> bytes:
    """ACHR — placed NPC reference. TES4 ACRE also maps here."""
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    name_fid = get_formid(rec, 'NAME')
    if name_fid:
        subs += pack_formid_subrecord('NAME', name_fid)

    xesp_ref = get_formid(rec, 'XESP.Reference')
    if xesp_ref:
        xesp_flags = get_int(rec, 'XESP.Flags')
        subs += pack_subrecord('XESP', struct.pack('<II', xesp_ref, xesp_flags))

    scale = get_float(rec, 'XSCL.Scale')
    if scale and scale != 1.0:
        subs += pack_float_subrecord('XSCL', scale)

    px = get_float(rec, 'PosX')
    py = get_float(rec, 'PosY')
    pz = get_float(rec, 'PosZ')
    rx = get_float(rec, 'RotX')
    ry = get_float(rec, 'RotY')
    rz = get_float(rec, 'RotZ')
    subs += pack_subrecord('DATA', struct.pack('<ffffff', px, py, pz, rx, ry, rz))

    flags = get_int(rec, 'RecordFlags')
    return pack_record('ACHR', get_formid(rec, 'FormID'), flags, subs)


def convert_ACRE(rec: dict) -> bytes:
    """ACRE → ACHR (placed creature → placed NPC)."""
    return convert_ACHR(rec)


def convert_LAND(rec: dict) -> bytes:
    """LAND record — landscape vertex data."""
    subs = b''

    # DATA flags
    data_flags = get_int(rec, 'DATA.Flags')
    subs += pack_subrecord('DATA', struct.pack('<I', data_flags))

    # VNML — vertex normals (raw hex)
    vnml_hex = get_str(rec, 'VNML')
    if vnml_hex:
        subs += pack_subrecord('VNML', bytes.fromhex(vnml_hex))

    # VHGT — vertex heights (raw hex)
    vhgt_hex = get_str(rec, 'VHGT')
    if vhgt_hex:
        subs += pack_subrecord('VHGT', bytes.fromhex(vhgt_hex))

    # VCLR — vertex colors (raw hex)
    vclr_hex = get_str(rec, 'VCLR')
    if vclr_hex:
        subs += pack_subrecord('VCLR', bytes.fromhex(vclr_hex))

    # Layers (BTXT/ATXT/VTXT)
    # TES5 limit: max 6 alpha layers per quadrant (indices 0–5).
    # Strategy: two-pass approach.
    #   Pass 1: collect all alpha layers per quadrant; merge same-texture layers
    #           by taking the max opacity per vertex position.
    #   Pass 2: sort by coverage score (sum of opacities) descending, keep top 6,
    #           write in coverage order so the most visually significant layers survive.
    _MAX_ALPHA_LAYERS = 6
    layer_count = get_int(rec, 'LayerCount')

    # Pass 1: collect layers
    # base_layers: quad -> (tex, order_index) — we keep first BASE seen per quad
    base_layers: dict = {}
    # alpha_layers: quad -> list of [tex, {pos: opacity}]
    alpha_layers: dict = {}

    for i in range(layer_count):
        pfx = f'Layer[{i}]'
        ltype = get_str(rec, f'{pfx}.Type')
        if ltype == 'BASE':
            tex = get_formid(rec, f'{pfx}.BTXT.Texture')
            quad = get_int(rec, f'{pfx}.BTXT.Quadrant')
            if quad not in base_layers:
                base_layers[quad] = tex
        elif ltype == 'ALPHA':
            tex = get_formid(rec, f'{pfx}.ATXT.Texture')
            quad = get_int(rec, f'{pfx}.ATXT.Quadrant')
            if tex == 0:
                continue
            # Collect vtxt as pos->opacity dict
            vtxt_count = get_int(rec, f'{pfx}.VTXTCount')
            vtxt: dict = {}
            for vi in range(vtxt_count):
                vpos = get_int(rec, f'{pfx}.VT[{vi}].Pos')
                opacity = get_float(rec, f'{pfx}.VT[{vi}].Opacity')
                vtxt[vpos] = opacity
            # Merge duplicate textures in the same quadrant: keep max opacity per vertex
            if quad not in alpha_layers:
                alpha_layers[quad] = []
            existing = next((e for e in alpha_layers[quad] if e[0] == tex), None)
            if existing is not None:
                for pos, op in vtxt.items():
                    if op > existing[1].get(pos, 0.0):
                        existing[1][pos] = op
            else:
                alpha_layers[quad].append([tex, vtxt])

    # Pass 2: emit base layers first, then sorted alpha layers
    for quad in sorted(base_layers):
        tex = base_layers[quad]
        btxt = struct.pack('<IBBxx', tex, quad, 0)
        subs += pack_subrecord('BTXT', btxt)

        layers_for_quad = alpha_layers.get(quad, [])
        # Sort by coverage score descending (sum of opacity values), keep top 6
        layers_for_quad.sort(key=lambda e: sum(e[1].values()), reverse=True)
        for alpha_idx, (tex, vtxt) in enumerate(layers_for_quad[:_MAX_ALPHA_LAYERS]):
            atxt = struct.pack('<IBBH', tex, quad, 0, alpha_idx)
            subs += pack_subrecord('ATXT', atxt)
            if vtxt:
                vtxt_data = bytearray()
                for vpos, opacity in sorted(vtxt.items()):
                    vtxt_data += struct.pack('<HHf', vpos, 0, opacity)
                subs += pack_subrecord('VTXT', bytes(vtxt_data))

    # VTEX does NOT exist in TES5 — skip it entirely

    flags = get_int(rec, 'RecordFlags')
    return pack_record('LAND', get_formid(rec, 'FormID'), flags, subs)


def convert_REGN(rec: dict) -> bytes:
    """REGN — Region. TES5 order: EDID RCLR WNAM RPLI/RPLD RDAT/ICON/RDMP/etc."""
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    # RCLR — Map color (before WNAM)
    r = get_int(rec, 'RCLR.R')
    g = get_int(rec, 'RCLR.G')
    b = get_int(rec, 'RCLR.B')
    if r or g or b:
        subs += pack_subrecord('RCLR', struct.pack('<BBBB', r, g, b, 0))

    # WNAM — Worldspace
    wnam = get_formid(rec, 'WNAM.Worldspace')
    if wnam:
        subs += pack_formid_subrecord('WNAM', wnam)

    # Region Data Entries — ICON goes inside RDAT, not at top level
    icon = get_str(rec, 'ICON')
    if icon:
        # Map name region data entry (type 4 = Map)
        subs += pack_subrecord('RDAT', struct.pack('<IBBxx', 4, 0, 0))
        subs += pack_string_subrecord('ICON', _prefix_path(icon))

    return pack_record('REGN', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_LSCR(rec: dict) -> bytes:
    """LSCR — Loading Screen. No OBND per xEdit.

    TES5 order: EDID ICON DESC CTDA NNAM SNAM RNAM ONAM XNAM MOD2
    NNAM is a FormID → STAT (the loading screen 3D model), required.
    ICON omitted: TES5 loading screens use 3D models, not 2D textures.
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    desc = get_str(rec, 'DESC')
    if desc:
        subs += pack_string_subrecord('DESC', desc)
    # NNAM — Loading Screen NIF: FormID → STAT|NULL (required, 4 bytes)
    # TES4 doesn't have a 3D model ref; use NULL (0)
    subs += pack_formid_subrecord('NNAM', 0)
    return pack_record('LSCR', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_WATR(rec: dict) -> bytes:
    """WATR — Water Type conversion.

    TES5 order: EDID FULL NNAM ANAM FNAM MNAM SNAM XNAM DATA DNAM GNAM NAM0 NAM1
    TES5 DATA is 228 bytes, heavily restructured from TES4.
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    # NNAM — Noise map texture (TES5 uses separate field)
    texture = get_str(rec, 'TNAM.Texture')
    if texture:
        subs += pack_string_subrecord('NNAM', _prefix_path(texture))

    # ANAM — Opacity
    opacity = get_int(rec, 'ANAM.Opacity', 128)
    subs += pack_uint8_subrecord('ANAM', opacity)

    # FNAM — Flags
    flags = get_int(rec, 'FNAM.Flags')
    subs += pack_uint8_subrecord('FNAM', flags)

    # MNAM — Material ID (string)
    mat_id = get_str(rec, 'MNAM.MaterialID')
    if mat_id:
        subs += pack_string_subrecord('MNAM', mat_id)

    # SNAM — Sound (open water sound)
    sound_fid = get_formid(rec, 'SNAM.Sound')
    if sound_fid:
        subs += pack_formid_subrecord('SNAM', sound_fid)

    # DATA — Water properties (228 bytes in TES5)
    # Preserve wind velocity/direction from TES4, fill rest with reasonable defaults
    data = bytearray(228)
    wind_vel = get_float(rec, 'DATA.WindVelocity', 0.3)
    wind_dir = get_float(rec, 'DATA.WindDirection', 0.0)
    # Byte 0-3: Unknown float
    struct.pack_into('<f', data, 0, 0.1)     # Unknown
    struct.pack_into('<f', data, 4, 0.1)     # Unknown
    struct.pack_into('<f', data, 8, 0.1)     # Unknown
    struct.pack_into('<f', data, 12, wind_vel)
    struct.pack_into('<f', data, 16, wind_dir)
    # Sun specular power
    struct.pack_into('<f', data, 20, 100.0)
    # Reflectivity amount
    struct.pack_into('<f', data, 24, 0.5)
    # Fresnel amount
    struct.pack_into('<f', data, 28, 0.025)
    # Scroll speeds (UV for layers)
    struct.pack_into('<f', data, 36, 0.3)
    struct.pack_into('<f', data, 40, 0.3)
    # Fog amount
    struct.pack_into('<f', data, 64, 0.01)
    # Fog near plane distance
    struct.pack_into('<f', data, 68, 1000.0)
    # Fog far plane distance
    struct.pack_into('<f', data, 72, 100000.0)
    # Shallow color (RGBA at offset 76): blue-ish
    data[76] = 64; data[77] = 96; data[78] = 128; data[79] = 200
    # Deep color (RGBA at offset 80): darker blue
    data[80] = 32; data[81] = 48; data[82] = 96; data[83] = 255
    # Reflection color (RGBA at offset 84): light
    data[84] = 200; data[85] = 200; data[86] = 200; data[87] = 128
    # Depth
    struct.pack_into('<f', data, 100, 150.0)
    subs += pack_subrecord('DATA', bytes(data))

    # DNAM — Visual data (196 bytes in TES5) — fill with defaults
    dnam = bytearray(196)
    struct.pack_into('<f', dnam, 0, 10.0)    # Depth normals
    struct.pack_into('<f', dnam, 4, 1.0)     # Depth reflections
    struct.pack_into('<f', dnam, 8, 0.5)     # Depth refraction
    struct.pack_into('<f', dnam, 12, 1.0)    # Depth specular lighting
    subs += pack_subrecord('DNAM', bytes(dnam))

    return pack_record('WATR', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_EFSH(rec: dict) -> bytes:
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    icon = get_str(rec, 'ICON')
    if icon:
        subs += pack_string_subrecord('ICON', _prefix_path(icon))
    ico2 = get_str(rec, 'ICO2')
    if ico2:
        subs += pack_string_subrecord('ICO2', _prefix_path(ico2))

    # DATA — TES5 EFSH DATA is larger but compatible at the start
    flags = get_int(rec, 'DATA.Flags')
    data = bytearray(128)
    data[0] = flags
    fr = get_int(rec, 'DATA.FillColorR')
    fg = get_int(rec, 'DATA.FillColorG')
    fb = get_int(rec, 'DATA.FillColorB')
    data[16] = fr; data[17] = fg; data[18] = fb
    struct.pack_into('<f', data, 20, get_float(rec, 'DATA.FillAlphaFadeInTime'))
    struct.pack_into('<f', data, 24, get_float(rec, 'DATA.FillAlphaFull'))
    struct.pack_into('<f', data, 28, get_float(rec, 'DATA.FillAlphaFadeOutTime'))
    struct.pack_into('<f', data, 32, get_float(rec, 'DATA.FillAlphaPersistPercent'))
    struct.pack_into('<f', data, 36, get_float(rec, 'DATA.FillAlphaPulseAmp'))
    struct.pack_into('<f', data, 40, get_float(rec, 'DATA.FillAlphaPulseFreq'))
    subs += pack_subrecord('DATA', bytes(data))

    return pack_record('EFSH', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)
