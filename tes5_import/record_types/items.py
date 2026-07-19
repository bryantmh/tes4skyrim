"""Item/object converters: STAT, ACTI, MISC, KEYM, DOOR, FLOR, FURN, GRAS, TREE, LIGH, SLGM, ANIO, CONT."""

import struct

from ..constants import LOD_SIZE_THRESHOLD, WORLD_MAP_SIZE_THRESHOLD
from .common import (
    VENDOR_KYWD,
    _common_header_subs,
    _prefix_path,
    _resolve_obnd,
    _simple_object,
    get_float,
    get_formid,
    get_int,
    get_str,
    pack_float_subrecord,
    pack_formid_subrecord,
    pack_keywords,
    pack_obnd,
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
    pack_uint8_subrecord,
    pack_uint32_subrecord,
)


def convert_STAT(rec: dict) -> bytes:
    """Convert STAT record, deriving LOD/world-map flags from mesh bounding box size."""
    flags = get_int(rec, 'RecordFlags')
    # Resolve OBND from converted mesh bounds (or type default as fallback).
    bounds = _resolve_obnd(rec, 'STAT')
    x1, y1, z1, x2, y2, z2 = bounds
    max_dim = max(x2 - x1, y2 - y1, z2 - z1)
    if max_dim >= LOD_SIZE_THRESHOLD:
        flags |= 0x8000       # Has Distant LOD — SSELodGen will build LOD for this object
    if max_dim >= WORLD_MAP_SIZE_THRESHOLD:
        flags |= 0x10000000   # Show in World Map
    subs = _common_header_subs(rec, need_full=False, obnd_override=bounds)
    path = get_str(rec, 'Model.MODL')
    if path:
        subs += pack_string_subrecord('MODL', _prefix_path(path))
    return pack_record('STAT', get_formid(rec, 'FormID'), flags, subs)


def convert_ACTI(rec: dict) -> bytes:
    extra = b''
    snam_fid = get_formid(rec, 'SNAM')
    if snam_fid:
        extra += pack_formid_subrecord('SNAM', snam_fid)
    return _simple_object(rec, 'ACTI', extra_subs=extra)


def convert_MISC(rec: dict) -> bytes:
    extra = b''
    # KSIZ/KWDA before DATA per TES5 MISC order (vendor sell-list filter)
    extra += pack_keywords([VENDOR_KYWD['Clutter']])
    value = get_int(rec, 'DATA.Value')
    weight = get_float(rec, 'DATA.Weight')
    extra += pack_subrecord('DATA', struct.pack('<If', value, weight))
    return _simple_object(rec, 'MISC', extra_subs=extra)


def convert_KEYM(rec: dict) -> bytes:
    extra = b''
    extra += pack_keywords([VENDOR_KYWD['Key']])
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


# --- FURN marker data -------------------------------------------------------
#
# TES5 FURN MNAM bits 0-23 enable NIF marker POSITION 0-23 (xEdit "Sit 0..23").
# The converted NIF's positions are the clustered SEATS produced by
# asset_convert/furniture_markers.py, NOT the original Oblivion entry markers,
# so the TES4 MNAM bitmask (which indexed the Oblivion NIF's entry list)
# CANNOT be passed through: dangling bits make the engine index past the
# NIF's position list and seat NPCs at garbage positions far from the mesh.
#
# The seat list is computed here with the SAME shared code the NIF converter
# uses (same clustering, same order), from the source NIF in the export dir.
# Populated once by load_furniture_seats() (called from import_main Phase 0).
#
# High MNAM flags: TES4 and TES5 share bit 30 (sit-type furniture) and
# bit 31 (bed-type) — verified against vanilla Skyrim (chairs/benches
# 0x40000001, beds 0x88000001).  Vanilla beds additionally set bit 27
# (0x08000000 "Must Exit to Talk").
_FURN_SEATS: dict = {}  # normalised MODL path -> seat list (see cluster_seats)
# Original TES4 base FormID (uppercase 8-hex string) -> origin shift for its
# model.  The NIF converter re-origins marker-bearing models to the vanilla
# floor-origin convention (the engine anchors seated actors to the REFR z),
# so every placed reference of these bases must be lowered by the same
# amount.  Applies to ALL record types sharing the model (FURN and STAT).
_BASE_ORIGIN_SHIFT: dict = {}


def _furn_model_key(modl: str) -> str:
    return modl.lower().replace('\\', '/').lstrip('/')


def get_base_origin_shift(base_fid: str) -> float:
    """Origin shift for a placed reference's base record (0.0 if none)."""
    return _BASE_ORIGIN_SHIFT.get(base_fid.upper(), 0.0)


def load_furniture_models(meshes_dir, by_type) -> int:
    """Compute seat lists + origin shifts for every marker-bearing model.

    meshes_dir: <export_dir>/meshes (source Oblivion NIFs from BSA extraction).
    by_type: full record dict (sig -> [records]) — every record type whose
    model is a marker-bearing NIF gets an origin-shift entry so its REFRs
    can be compensated.

    Returns the number of models resolved.  FURN models whose NIF is
    missing or unreadable fall back to a conservative single-seat FURN at
    convert time (their REFRs are left unshifted, matching the unshifted /
    missing mesh).
    """
    import os
    _FURN_SEATS.clear()
    _BASE_ORIGIN_SHIFT.clear()
    try:
        from asset_convert.furniture_markers import (furniture_model_info_job,
                                                     scan_marker_nifs)
    except ImportError as exc:
        print(f"  Furniture seats: asset_convert unavailable ({exc}), using fallback")
        return 0
    if not os.path.isdir(meshes_dir):
        print(f"  Furniture seats: meshes dir not found ({meshes_dir}), using fallback")
        return 0

    marker_models = scan_marker_nifs(meshes_dir)
    # PyFFI parsing is CPU-bound pure Python — run the per-NIF parses across
    # a process pool (threads would serialise on the GIL).
    jobs = [(key, os.path.join(meshes_dir, key.replace('/', os.sep)))
            for key in sorted(marker_models)]
    model_shift: dict = {}
    resolved = 0

    def _consume(results):
        nonlocal resolved
        for key, info, err in results:
            if err is not None:
                print(f"  Furniture seats: failed to read {key}: {err}")
                continue
            _FURN_SEATS[key] = info['seats']
            model_shift[key] = info['origin_shift']
            resolved += 1

    if len(jobs) < 8:
        _consume(map(furniture_model_info_job, jobs))
    else:
        from concurrent.futures import ProcessPoolExecutor
        workers = min(max(1, (os.cpu_count() or 4) - 1), len(jobs))
        with ProcessPoolExecutor(max_workers=workers) as ex:
            _consume(ex.map(furniture_model_info_job, jobs))

    shifted_bases = 0
    for recs in by_type.values():
        for rec in recs:
            modl = get_str(rec, 'Model.MODL')
            if not modl:
                continue
            shift = model_shift.get(_furn_model_key(modl))
            if shift and abs(shift) > 1e-4:
                fid = get_str(rec, 'FormID')
                if fid:
                    _BASE_ORIGIN_SHIFT[fid.upper()] = shift
                    shifted_bases += 1
    print(f"  Furniture seats: {resolved} marker models resolved, "
          f"{shifted_bases} base records need REFR z compensation")
    return resolved


def convert_FURN(rec: dict) -> bytes:
    extra = b''
    tes4_flags = get_int(rec, 'MNAM.Flags')

    # PNAM — 4 unknown bytes (empty placeholder, required by engine)
    extra += pack_subrecord('PNAM', b'\x00\x00\x00\x00')
    # FNAM — U16 flags (bit 1 = Ignored By Sandbox); pass 0
    extra += pack_subrecord('FNAM', struct.pack('<H', 0))

    modl = get_str(rec, 'Model.MODL')
    seats = _FURN_SEATS.get(_furn_model_key(modl)) if modl else None

    if seats == []:
        # NIF read successfully but has NO furniture markers: enabling any
        # MNAM bit would make the engine index a non-existent NIF position.
        # Emit no active markers (decorative furniture).
        extra += pack_uint32_subrecord('MNAM', tes4_flags & 0xC0000000)
        extra += pack_subrecord('WBDT', struct.pack('<Bb', 0, -1))
    elif seats:
        # Enable every clustered seat; per-record approach restriction is
        # carried by the FNPR entry flags below (Oblivion restricts by
        # enabling a SUBSET of entry markers — e.g. SEChair01F/R/L share a
        # NIF and enable different entries).
        mnam = (1 << len(seats)) - 1
        mnam |= tes4_flags & 0xC0000000
        any_sleep = any(s['sleep'] for s in seats)
        if any_sleep:
            mnam |= 0x08000000  # Must Exit to Talk (all vanilla beds set it)
        extra += pack_uint32_subrecord('MNAM', mnam)
        # WBDT — workbench data: type None, skill -1 (vanilla standard)
        extra += pack_subrecord('WBDT', struct.pack('<Bb', 0, -1))
        # FNPR — one per NIF marker position, in position order:
        # Type (1=Sit, 2=Sleep) + entry-point flags.  Only the entry
        # directions whose TES4 entry marker was enabled in this record's
        # bitmask are allowed; if the record enables none of a seat's
        # entries, allow all of them (seat unreachable otherwise).
        for seat in seats:
            enabled = 0
            for entry_index, flag in seat['members']:
                if tes4_flags & (1 << entry_index):
                    enabled |= flag
            if not enabled:
                enabled = seat['entry_flags']
            anim_type = 2 if seat['sleep'] else 1
            extra += pack_subrecord('FNPR', struct.pack('<HH', anim_type, enabled))
    else:
        # Source NIF unavailable: conservative single seat, all entries.
        is_sleep = bool(tes4_flags & 0x80000000)
        mnam = 0x00000001 | (tes4_flags & 0xC0000000)
        if is_sleep:
            mnam |= 0x08000000
        extra += pack_uint32_subrecord('MNAM', mnam)
        extra += pack_subrecord('WBDT', struct.pack('<Bb', 0, -1))
        extra += pack_subrecord('FNPR', struct.pack('<HH', 2 if is_sleep else 1, 0x0F))

    return _simple_object(rec, 'FURN', extra_subs=extra)


def convert_GRAS(rec: dict) -> bytes:
    """GRAS — Grass.

    Every working GRAS record (vanilla Skyrim.esm, USSEP, Beyond Skyrim's
    BSHeartland.esm, Skyrim Extended Cut, Legacy Orsinium) shares three
    structural invariants the generic object path violates:
      1. OBND is all ZEROS (never computed mesh bounds);
      2. MODT is present (BSHeartland proves a 12-byte version-2 stub
         with no texture hashes works);
      3. the model path lives under meshes\\landscape\\grass\\ (45/45
         surveyed MODL paths — the asset pipeline places a copy of each
         converted grass NIF there, see grass_profile.grass_model_dest).
    Grass is engine-instanced from LAND texture layers, not placed, so it
    skips the normal object treatment.
    """
    from asset_convert.grass_profile import grass_model_dest
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    subs += pack_obnd()  # all zeros — vanilla/BSHeartland invariant
    path = get_str(rec, 'Model.MODL')
    if path:
        subs += pack_string_subrecord('MODL', grass_model_dest(path))
        # MODT stub (version 2, no hashes) — present on every working GRAS
        subs += pack_subrecord('MODT', struct.pack('<III', 2, 0, 0))

    # TES5 GRAS DATA is the same layout, but TES4 values were tuned for
    # Oblivion's much coarser placement grid (iMinGrassSize=80 vs Skyrim's
    # 20 — 16x fewer placement points).  Passed through raw, Density up to
    # 100 with PositionRange up to 90 over-instances Skyrim's grass planter
    # and CTDs (no log) on cells where dense grass textures cover whole
    # quadrants.  Clamp both to the proven-working envelope: vanilla uses
    # Density 3-6 / PositionRange <=32; BSHeartland (working Oblivion-style
    # grass fields) goes up to Density 80 / PositionRange ~32.
    density = min(get_int(rec, 'DATA.Density'), 80)
    min_slope = get_int(rec, 'DATA.MinSlope')
    max_slope = get_int(rec, 'DATA.MaxSlope', 90)
    uf_water = get_int(rec, 'DATA.UnitFromWaterAmount')
    uf_type = get_int(rec, 'DATA.UnitFromWaterType')
    pos_range = min(get_float(rec, 'DATA.PositionRange'), 32.0)
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
    subs += pack_subrecord('DATA', bytes(data))
    return pack_record('GRAS', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


# TES5 TREE CNAM (48 bytes = 12 floats): Trunk Flexibility, Branch
# Flexibility, Trunk Amplitude, Front Amplitude, Back Amplitude, Side
# Amplitude, Front Frequency, Back Frequency, Side Frequency, Leaf
# Flexibility, Leaf Amplitude, Leaf Frequency.  Values from vanilla
# TreeReachTree01 — moderate sway that suits full-size trees.
_TREE_CNAM = struct.pack('<12f', 1.0, 1.0, 0.04, 0.03, 0.04, 0.034,
                         0.5, 0.5, 0.4, 1.0, 2.0, 1.0)


def convert_TREE(rec: dict) -> bytes:
    r"""TREE — Tree.

    The SPT converter generates one NIF per TREE record (seeded by the
    record's SNAM seed, leaf-textured by its ICON), named by lowercase
    EditorID: tes4\speedtrees\<editorid>.nif.  OBND comes from the TES4
    billboard dimensions (the tree's real world size); CNAM supplies the
    BSLeafAnimNode wind parameters TES4 has no source for.
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    bb_w = get_float(rec, 'BNAM.BillboardWidth')
    bb_h = get_float(rec, 'BNAM.BillboardHeight')
    if bb_w > 0 and bb_h > 0:
        half = min(int(bb_w / 2), 32767)
        bounds = (-half, -half, 0, half, half, min(int(bb_h), 32767))
    else:
        bounds = _resolve_obnd(rec, 'TREE')
    subs += pack_obnd(*bounds)
    model = get_str(rec, 'Model.MODL')
    if model and edid:
        subs += pack_string_subrecord('MODL', f'tes4\\speedtrees\\{edid.lower()}.nif')
    elif model:
        import os
        stem = os.path.splitext(os.path.basename(model.replace('\\', '/').lstrip('/')))[0]
        subs += pack_string_subrecord('MODL', f'tes4\\speedtrees\\{stem.lower()}.nif')
    subs += pack_subrecord('PFPC', struct.pack('<I', 0))
    subs += pack_subrecord('CNAM', _TREE_CNAM)
    # Same size-derived LOD flags as STAT: trees flow through the standard
    # object-LOD pipeline (decimated _far.nif -> LODGen) like any other object.
    flags = get_int(rec, 'RecordFlags')
    max_dim = max(bounds[3] - bounds[0], bounds[4] - bounds[1], bounds[5] - bounds[2])
    if max_dim >= LOD_SIZE_THRESHOLD:
        flags |= 0x8000       # Has Distant LOD
    if max_dim >= WORLD_MAP_SIZE_THRESHOLD:
        flags |= 0x10000000   # Show in World Map
    return pack_record('TREE', get_formid(rec, 'FormID'), flags, subs)


def convert_LIGH(rec: dict) -> bytes:
    """LIGH — Light. TES5 order: EDID OBND MODL FULL DATA FNAM SNAM"""
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    subs += pack_obnd(*_resolve_obnd(rec, 'LIGH'))
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
    flags &= ~0x10  # TES4 'Unused' bit
    # TES4 stores 0 for "engine default" here; xEdit's wbLIGHAfterLoad applies
    # the same substitution (0 falloff -> 1.0, 0 FOV -> 90).
    falloff = get_float(rec, 'DATA.FalloffExponent', 1.0) or 1.0
    fov = get_float(rec, 'DATA.FOV', 90.0) or 90.0
    value = get_int(rec, 'DATA.Value')
    weight = get_float(rec, 'DATA.Weight')
    # TES4 has no flicker-effect parameters — the animation was implied by the
    # flags alone.  Skyrim needs explicit period/amplitudes or animated lights
    # render static/degenerate.  Values follow vanilla Skyrim conventions
    # (torches: 0.333/0.5/16; pulse candles: 0.333/0.2/1).
    if flags & 0x08:      # Flicker
        period, int_amp, mov_amp = 0.333, 0.5, 16.0
    elif flags & 0x40:    # Flicker Slow
        period, int_amp, mov_amp = 0.667, 0.5, 16.0
    elif flags & 0x80:    # Pulse
        period, int_amp, mov_amp = 1.0, 0.2, 0.0
    elif flags & 0x100:   # Pulse Slow
        period, int_amp, mov_amp = 2.0, 0.2, 0.0
    else:
        period, int_amp, mov_amp = 1.0, 0.0, 0.0
    data = bytearray(48)
    struct.pack_into('<i', data, 0, time)
    struct.pack_into('<I', data, 4, radius)
    data[8] = r; data[9] = g; data[10] = b; data[11] = 0
    struct.pack_into('<I', data, 12, flags)
    struct.pack_into('<f', data, 16, falloff)
    struct.pack_into('<f', data, 20, fov)
    struct.pack_into('<f', data, 24, 1.0)  # Near clip (vanilla default)
    struct.pack_into('<f', data, 28, period)
    struct.pack_into('<f', data, 32, int_amp)
    struct.pack_into('<f', data, 36, mov_amp)
    struct.pack_into('<I', data, 40, value)
    struct.pack_into('<f', data, 44, weight)
    subs += pack_subrecord('DATA', bytes(data))

    # FNAM is required in TES5; TES4's default when absent is 1.0.
    fade = get_float(rec, 'FNAM.Fade', 1.0)
    subs += pack_float_subrecord('FNAM', fade)
    snam = get_formid(rec, 'SNAM.Sound')
    if snam:
        subs += pack_formid_subrecord('SNAM', snam)
    return pack_record('LIGH', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_SLGM(rec: dict) -> bytes:
    extra = b''
    extra += pack_keywords([VENDOR_KYWD['SoulGem']])
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
        # TES4 merchant chests use NEGATIVE counts for restocking stock
        # ("-100" = keep 100 for sale); Skyrim restocks via the respawn flag
        # and treats count < 1 as "adds nothing" (CK: "Container object has a
        # count of less then 1 ... will cause issues in game").
        count = abs(get_int(rec, f'Item[{i}].Count', 1)) or 1
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
