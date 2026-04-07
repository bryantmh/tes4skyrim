"""Item/object converters: STAT, ACTI, MISC, KEYM, DOOR, FLOR, FURN, GRAS, TREE, LIGH, SLGM, ANIO, CONT."""

import struct

from .common import (
    _add_model,
    _common_header_subs,
    _prefix_path,
    _simple_object,
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
    pack_uint32_subrecord,
)


def convert_STAT(rec: dict) -> bytes:
    """STAT → STAT (add OBND)."""
    subs = _common_header_subs(rec, need_full=False, obnd_sig='STAT')
    _add_model([subs], rec)
    # Collect model as bytes
    model_path = get_str(rec, 'Model.MODL')
    model_subs = b''
    if model_path:
        model_subs = pack_string_subrecord('MODL', _prefix_path(model_path))
    return pack_record('STAT', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'),
                       subs + model_subs)


def convert_STAT_v2(rec: dict) -> bytes:
    return _simple_object(rec, 'STAT', has_full=False)


def convert_ACTI(rec: dict) -> bytes:
    extra = b''
    snam_fid = get_formid(rec, 'SNAM')
    if snam_fid:
        extra += pack_formid_subrecord('SNAM', snam_fid)
    return _simple_object(rec, 'ACTI', extra_subs=extra)


def convert_MISC(rec: dict) -> bytes:
    extra = b''
    value = get_int(rec, 'DATA.Value')
    weight = get_float(rec, 'DATA.Weight')
    extra += pack_subrecord('DATA', struct.pack('<If', value, weight))
    return _simple_object(rec, 'MISC', extra_subs=extra)


def convert_KEYM(rec: dict) -> bytes:
    extra = b''
    value = get_int(rec, 'DATA.Value')
    weight = get_float(rec, 'DATA.Weight')
    extra += pack_subrecord('DATA', struct.pack('<If', value, weight))
    return _simple_object(rec, 'KEYM', extra_subs=extra)


def convert_DOOR(rec: dict) -> bytes:
    extra = b''
    # TES5 DOOR has SNAM (open)/ ANAM (close)/ BNAM (loop)
    snam = get_formid(rec, 'SNAM.Open')
    if snam:
        extra += pack_formid_subrecord('SNAM', snam)
    anam = get_formid(rec, 'ANAM.Close')
    if anam:
        extra += pack_formid_subrecord('ANAM', anam)
    bnam = get_formid(rec, 'BNAM.Loop')
    if bnam:
        extra += pack_formid_subrecord('BNAM', bnam)
    fnam = get_int(rec, 'FNAM.Flags', -1)
    if fnam >= 0:
        # TES4 bit 0 = "Oblivion gate" — no TES5 equivalent, clear it.
        # TES4 bits 1-3 (Automatic, Hidden, Minimal Use) map directly to TES5 bits 1-3.
        fnam = fnam & ~0x01
        extra += pack_uint8_subrecord('FNAM', fnam)
    return _simple_object(rec, 'DOOR', extra_subs=extra)


def convert_FLOR(rec: dict) -> bytes:
    extra = b''
    pfig = get_formid(rec, 'PFIG')
    if pfig:
        extra += pack_formid_subrecord('PFIG', pfig)
    return _simple_object(rec, 'FLOR', extra_subs=extra)


def convert_FURN(rec: dict) -> bytes:
    extra = b''

    # TES4 MNAM is a U32 bitmask:
    #  bits 0-23  → active sit marker slots (Sit 0 … Sit 23)
    #  bit 30 (0x40000000) → Is Perch (chair/bench type)
    #  bit 31 (0x80000000) → Sleep (bed type)
    # TES5 MNAM has the same bit layout for bits 0-23 and the high flags.
    # Additionally TES5 requires:
    #  PNAM  — placeholder unknown (4 zero bytes)
    #  FNAM  — flags U16 (0 = default)
    #  MNAM  — active markers U32 (same bitmask as TES4)
    #  Markers array: for each active bit 0–23: ENAM(U32) + NAM0(4B) + FNMK(FormID)
    #  FNPR array: defines anim types available at this furniture
    furn_flags = get_int(rec, 'MNAM.Flags')

    is_sleep = bool(furn_flags & 0x80000000)   # bed
    # is_perch = bool(furn_flags & 0x40000000)  # chair/bench (unused but noted)

    # PNAM — 4 unknown bytes (empty placeholder, required by engine)
    extra += pack_subrecord('PNAM', b'\x00\x00\x00\x00')

    # FNAM — U16 flags (bit 1 = Ignored By Sandbox); pass 0
    extra += pack_subrecord('FNAM', struct.pack('<H', 0))

    # MNAM — active markers bitmask (same value as TES4)
    extra += pack_uint32_subrecord('MNAM', furn_flags)

    # Markers array — one ENAM+NAM0+FNMK group per active sit slot (bits 0-23)
    # NAM0: 4 bytes — first 2 unknown (zeros), last 2 = disabled entry-points U16 (0 = all enabled)
    for bit in range(24):
        if furn_flags & (1 << bit):
            extra += pack_subrecord('ENAM', struct.pack('<I', bit))
            extra += pack_subrecord('NAM0', b'\x00\x00\x00\x00')
            extra += pack_subrecord('FNMK', struct.pack('<I', 0))  # NULL keyword

    # FNPR — entry points: defines what animations work at this furniture
    # Format: Type(U16) + EntryPoints(U16 flags: 0x01=Front, 0x02=Behind, 0x04=Right, 0x08=Left)
    # For beds: AnimType=2 (Lay), entry=Front
    # For perch/chairs: AnimType=1 (Sit), entry=Front
    if furn_flags & 0x00FFFFFF:  # any active markers
        anim_type = 2 if is_sleep else 1  # Lay or Sit
        entry_points = 0x01               # Front
        extra += pack_subrecord('FNPR', struct.pack('<HH', anim_type, entry_points))

    return _simple_object(rec, 'FURN', extra_subs=extra)


def convert_GRAS(rec: dict) -> bytes:
    extra = b''
    # TES5 GRAS DATA is similar structure
    density = get_int(rec, 'DATA.Density')
    min_slope = get_int(rec, 'DATA.MinSlope')
    max_slope = get_int(rec, 'DATA.MaxSlope', 90)
    uf_water = get_int(rec, 'DATA.UnitFromWaterAmount')
    uf_type = get_int(rec, 'DATA.UnitFromWaterType')
    pos_range = get_float(rec, 'DATA.PositionRange')
    h_range = get_float(rec, 'DATA.HeightRange')
    c_range = get_float(rec, 'DATA.ColorRange')
    wave = get_float(rec, 'DATA.WavePeriod')
    flags = get_int(rec, 'DATA.Flags')
    data = bytearray(32)
    data[0] = density
    data[1] = min_slope
    data[2] = max_slope
    struct.pack_into('<H', data, 4, uf_water)
    struct.pack_into('<I', data, 8, uf_type)
    struct.pack_into('<f', data, 12, pos_range)
    struct.pack_into('<f', data, 16, h_range)
    struct.pack_into('<f', data, 20, c_range)
    struct.pack_into('<f', data, 24, wave)
    data[28] = flags
    extra += pack_subrecord('DATA', bytes(data))
    return _simple_object(rec, 'GRAS', has_full=False, extra_subs=extra)


def convert_TREE(rec: dict) -> bytes:
    """TREE — Tree. Convert SPT model path → tes4\speedtrees\{stem}.nif"""
    subs = _common_header_subs(rec, need_full=False, obnd_sig='TREE')
    model = get_str(rec, 'Model.MODL')
    if model:
        # TES4 TREE MODL is like "\\DBush03.spt" — remap to our NIF output path
        import os
        stem = os.path.splitext(os.path.basename(model.replace('\\', '/').lstrip('/')))[0]
        nif_path = f'tes4\\speedtrees\\{stem}.nif'
        subs += pack_string_subrecord('MODL', nif_path)
    return pack_record('TREE', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_LIGH(rec: dict) -> bytes:
    """LIGH — Light. TES5 order: EDID OBND MODL FULL DATA FNAM SNAM"""
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    subs += pack_obnd(-6, -6, 0, 6, 6, 20)  # LIGH default bounds
    model = get_str(rec, 'Model.MODL')
    if model:
        subs += pack_string_subrecord('MODL', _prefix_path(model))
    full = get_str(rec, 'FULL')
    if full:
        subs += pack_string_subrecord('FULL', full)

    # DATA (48 bytes)
    time = get_int(rec, 'DATA.Time')
    radius = get_int(rec, 'DATA.Radius', 128)
    r = get_int(rec, 'DATA.Color.R')
    g = get_int(rec, 'DATA.Color.G')
    b = get_int(rec, 'DATA.Color.B')
    flags = get_int(rec, 'DATA.Flags')
    falloff = get_float(rec, 'DATA.FalloffExponent', 1.0)
    fov = get_float(rec, 'DATA.FOV', 90.0)
    value = get_int(rec, 'DATA.Value')
    weight = get_float(rec, 'DATA.Weight')
    data = bytearray(48)
    struct.pack_into('<i', data, 0, time)
    struct.pack_into('<I', data, 4, radius)
    data[8] = r; data[9] = g; data[10] = b; data[11] = 0
    struct.pack_into('<I', data, 12, flags)
    struct.pack_into('<f', data, 16, falloff)
    struct.pack_into('<f', data, 20, fov)
    struct.pack_into('<I', data, 40, value)
    struct.pack_into('<f', data, 44, weight)
    subs += pack_subrecord('DATA', bytes(data))

    fade = get_float(rec, 'FNAM.Fade')
    if fade:
        subs += pack_float_subrecord('FNAM', fade)
    snam = get_formid(rec, 'SNAM.Sound')
    if snam:
        subs += pack_formid_subrecord('SNAM', snam)
    return pack_record('LIGH', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_SLGM(rec: dict) -> bytes:
    extra = b''
    value = get_int(rec, 'DATA.Value')
    weight = get_float(rec, 'DATA.Weight')
    extra += pack_subrecord('DATA', struct.pack('<If', value, weight))
    soul = get_int(rec, 'SOUL', -1)
    if soul >= 0:
        extra += pack_uint8_subrecord('SOUL', soul)
    slcp = get_int(rec, 'SLCP.Capacity', -1)
    if slcp >= 0:
        extra += pack_uint8_subrecord('SLCP', slcp)
    return _simple_object(rec, 'SLGM', extra_subs=extra)


def convert_ANIO(rec: dict) -> bytes:
    """ANIO — Animated Object. No OBND per xEdit; just EDID + MODL + BNAM."""
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    model = get_str(rec, 'Model.MODL')
    if model:
        subs += pack_string_subrecord('MODL', _prefix_path(model))
    bnam = get_str(rec, 'BNAM')
    if bnam:
        subs += pack_string_subrecord('BNAM', bnam)
    return pack_record('ANIO', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_CONT(rec: dict) -> bytes:
    extra = b''
    # Items (CNTO)
    i = 0
    while True:
        fid = get_formid(rec, f'Item[{i}].FormID')
        if not fid:
            break
        count = get_int(rec, f'Item[{i}].Count', 1)
        extra += pack_subrecord('CNTO', struct.pack('<Ii', fid, count))
        i += 1
    # DATA: Flags(1) + Weight(4) = 5 bytes in TES4
    # TES5: same DATA structure
    flags = get_int(rec, 'DATA.Flags')
    weight = get_float(rec, 'DATA.Weight')
    extra += pack_subrecord('DATA', struct.pack('<Bf', flags, weight))
    snam = get_formid(rec, 'SNAM.OpenSound')
    if snam:
        extra += pack_formid_subrecord('SNAM', snam)
    qnam = get_formid(rec, 'QNAM.CloseSound')
    if qnam:
        extra += pack_formid_subrecord('QNAM', qnam)
    return _simple_object(rec, 'CONT', extra_subs=extra)


# ---------------------------------------------------------------------------
# Equipment converters
# ---------------------------------------------------------------------------
