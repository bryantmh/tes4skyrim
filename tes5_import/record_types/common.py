"""
Shared helper functions for TES5 record converters.
"""

import struct

from asset_convert.game_paths import current_namespace
from ..base.mesh_bounds import get_mesh_obnd
from ..base.text_reader import get_float, get_formid, get_int, get_str
from ..base.writer import (
    pack_float_subrecord,
    pack_formid_subrecord,
    pack_obnd,
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
    pack_uint8_subrecord,
    pack_uint16_subrecord,
    pack_uint32_subrecord,
)


# ---------------------------------------------------------------------------
#   Shared subrecord helpers
# ---------------------------------------------------------------------------


def prefix_path(path: str) -> str:
    """Prefix an AUTHORED asset path with the ACTIVE game namespace, always.

    Strips a leading 'textures\\' since Skyrim auto-prefixes it. MUST agree
    with asset_convert's rewrite_tex_path: if the record side and the asset
    copy disagree, every record names a path no archive ships.
    See: docs/commentary/asset_convert_texture.md#per-game-asset-namespace
    """
    if not path:
        return path
    p = path
    if p.lower().startswith('textures\\') or p.lower().startswith('textures/'):
        p = p[9:]
    return current_namespace() + '\\' + p


def landscape_texture_path(icon_path: str) -> str:
    """The shipped path of an LTEX ICON, whatever the source game.

    Oblivion names a bare file relative to Textures\\Landscape\\, so the
    folder is prepended. Morrowind's is already a full path under Textures\\
    and must be left alone -- prefixing it invented a landscape\\ folder that
    does not exist, and all 107 terrain textures resolved to nothing.
    See: docs/commentary/tes4_export_morrowind.md#land-terrain
    """
    lowered = icon_path.lower().replace('/', '\\')
    if not (lowered.startswith('textures\\')
            or lowered.startswith('landscape\\')):
        icon_path = 'landscape\\' + icon_path
    return prefix_path(icon_path)


def _common_header_subs(rec: dict, need_obnd: bool = True, need_full: bool = True,
                        obnd_sig: str = '', obnd_override: tuple = None) -> bytes:
    """Build common leading subrecords: EDID, VMAD, OBND, FULL.

    VMAD carries the converted TES4 object script (SCPT via SCRI) when
    one was bound, and Skyrim orders it right after EDID.

    The `object_scripts` import stays INSIDE the body.
    See: docs/reference/tes5_import_architecture.md#object-scripts-import-is-deferred

    obnd_sig: record type signature for type-aware OBND defaults.
    obnd_override: explicit (x1,y1,z1,x2,y2,z2) tuple; skips mesh lookup.
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    from ..base.object_scripts import get_object_vmad
    subs += get_object_vmad(get_formid(rec, 'FormID'))
    if need_obnd:
        bounds = obnd_override if obnd_override is not None else _resolve_obnd(rec, obnd_sig)
        subs += pack_obnd(*bounds)
    if need_full:
        full = get_str(rec, 'FULL')
        if full:
            subs += pack_string_subrecord('FULL', full)
    return subs


# Per-type OBND defaults (x1, y1, z1, x2, y2, z2).
# Values are conservative estimates based on vanilla Skyrim object sizes.
# Small items use tight bounds; large objects use wider bounds.
_OBND_DEFAULT = (-10, -10, 0, 10, 10, 20)
_OBND_DEFAULTS = {
    # Small items
    'MISC': (-5, -5, 0, 5, 5, 8),
    'KEYM': (-3, -3, 0, 3, 3, 3),
    'INGR': (-4, -4, 0, 4, 4, 6),
    'ALCH': (-4, -4, 0, 4, 4, 10),
    'AMMO': (-2, -2, 0, 2, 2, 18),
    'SLGM': (-4, -4, 0, 4, 4, 6),
    'SCRL': (-5, -5, 0, 5, 5, 3),
    # Medium items
    'BOOK': (-8, -6, 0, 8, 6, 3),
    'WEAP': (-5, -5, 0, 5, 5, 30),
    'ARMO': (-15, -15, 0, 15, 15, 15),
    'LIGH': (-6, -6, 0, 6, 6, 20),
    # Interactive objects
    'DOOR': (-30, -5, 0, 30, 5, 60),
    'CONT': (-20, -15, 0, 20, 15, 30),
    'ACTI': (-15, -15, 0, 15, 15, 30),
    'FLOR': (-10, -10, 0, 10, 10, 15),
    'FURN': (-30, -30, 0, 30, 30, 50),
    # Large objects
    'STAT': (-50, -50, 0, 50, 50, 80),
    # GRAS intentionally absent: grass OBND is always all-zeros (see convert_GRAS)
    'TREE': (-50, -50, 0, 50, 50, 150),
    # Actors
    'NPC_': (-12, -12, 0, 12, 12, 60),
    # Effects
    'ENCH': (-5, -5, 0, 5, 5, 5),
    'SPEL': (-5, -5, 0, 5, 5, 5),
}


#: The six OBND keys, in TES5 subrecord order. FO3/FNV author them; TES4 cannot.
_OBND_KEYS = ('OBND.X1', 'OBND.Y1', 'OBND.Z1', 'OBND.X2', 'OBND.Y2', 'OBND.Z2')


def _authored_obnd(rec: dict) -> tuple:
    """The record's own OBND, or None when it authors none."""
    if not any(k in rec for k in _OBND_KEYS):
        return None
    return tuple(get_int(rec, k) for k in _OBND_KEYS)


def _resolve_obnd(rec: dict, obnd_sig: str) -> tuple:
    """Resolve OBND bounds for a record.

    Authored bounds win; then mesh bounds (from pre-scanned converted NIFs);
    then per-type defaults, then the global default.

    Returns a (x1, y1, z1, x2, y2, z2) int tuple.
    """
    authored = _authored_obnd(rec)
    if authored is not None:
        return authored
    path = get_str(rec, 'Model.MODL')
    if path:
        key = prefix_path(path).lower().replace('\\', '/')
        bounds = get_mesh_obnd(key)
        if bounds is not None:
            return bounds
    return _OBND_DEFAULTS.get(obnd_sig, _OBND_DEFAULT)


# Skyrim VendorItem* KYWD FormIDs (verified against Skyrim.esm). Vendors only
# buy/sell items whose keywords appear in their faction's VEND formlist, so
# every sellable converted item must carry the matching keyword.
VENDOR_KYWD = {
    'Weapon':     0x0008F958,
    'Armor':      0x0008F959,
    'Jewelry':    0x0008F95A,
    'Clothing':   0x0008F95B,
    'Food':       0x0008CDEA,
    'Ingredient': 0x0008CDEB,
    'Potion':     0x0008CDEC,
    'Poison':     0x0008CDED,
    'Book':       0x000937A2,
    'SoulGem':    0x000937A3,
    'Staff':      0x000937A4,
    'SpellTome':  0x000937A5,
    'Clutter':    0x000914E9,
    'AnimalHide': 0x000914EA,
    'OreIngot':   0x000914EC,
    'Gem':        0x000914ED,
    'Tool':       0x000914EE,
    'Key':        0x000914EF,
    'Arrow':      0x000917E7,
    'FoodRaw':    0x000A0E56,
    'Scroll':     0x000A0E57,
}


def pack_keywords(kwd_fids) -> bytes:
    """KSIZ + KWDA subrecords for a keyword FormID list (b'' when empty)."""
    fids = [f for f in kwd_fids if f]
    if not fids:
        return b''
    out = pack_uint32_subrecord('KSIZ', len(fids))
    out += pack_subrecord('KWDA', b''.join(struct.pack('<I', f) for f in fids))
    return out


def _simple_object(rec: dict, sig: str, has_full: bool = True,
                   has_model: bool = True, extra_subs: bytes = b'') -> bytes:
    """Generic simple-object converter.

    Produces: EDID + OBND + FULL + MODL + extra_subs.
    """
    subs = _common_header_subs(rec, need_full=has_full, obnd_sig=sig)
    if has_model:
        path = get_str(rec, 'Model.MODL')
        if path:
            subs += pack_string_subrecord('MODL', prefix_path(path))
    subs += extra_subs
    return pack_record(sig, get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def _convert_biped_flags(tes4_flags: int) -> int:
    """Convert source biped flags to TES5 first person flags.

    Returns PRIMARY equip slots plus equipment-conflict extras; the source
    game selects the slot table.  Both imports are function-local to break
    the cycle equipment_falloutnv -> common -> equipment_falloutnv.
    See: docs/commentary/asset_convert_armor.md#biped-slot-conversion
    """
    from ..base.constants import BIPED_SLOT_MAP, BIPED_SLOT_EXTRA
    from .equipment_falloutnv import biped_slot_tables
    slot_map, slot_extra = biped_slot_tables(BIPED_SLOT_MAP, BIPED_SLOT_EXTRA)
    tes5 = 0
    for tes4_bit, tes5_bit in slot_map.items():
        if tes4_flags & (1 << tes4_bit):
            tes5 |= (1 << tes5_bit)
    for tes5_bit, extra_bits in slot_extra.items():
        if tes5 & (1 << tes5_bit):
            for eb in extra_bits:
                tes5 |= (1 << eb)
    return tes5


#: TES4 music enum -> MUSC FormID, read by the CELL, WRLD and REGN converters.
_MUSIC_BY_ENUM: dict = {}

#: WRLD FormID -> its authored SNAM music enum, read by convert_REGN.
_WORLD_MUSIC_ENUM: dict = {}

#: Remapped FormIDs of the regions convert_REGN emitted; convert_CELL filters XCLR against it.
_EMITTED_REGION_FIDS: set = set()

#: TES4's implicit default when a CELL/WRLD authors no music: enum 0, "Default".
TES4_DEFAULT_MUSIC_ENUM = 0


def register_music_types(by_enum: dict):
    """Register {tes4 XCMT/SNAM enum -> MUSC FormID} for CELL/WRLD emission."""
    _MUSIC_BY_ENUM.clear()
    _MUSIC_BY_ENUM.update(by_enum or {})


def register_world_music(by_world: dict):
    """Register {WRLD FormID -> authored SNAM enum} for convert_REGN."""
    _WORLD_MUSIC_ENUM.clear()
    _WORLD_MUSIC_ENUM.update(by_world or {})


def music_for_enum(enum_value):
    """The MUSC FormID registered for `enum_value`, or None."""
    return _MUSIC_BY_ENUM.get(enum_value)


def world_music_enum(wrld_fid):
    """The authored SNAM music enum for `wrld_fid`, or None."""
    return _WORLD_MUSIC_ENUM.get(wrld_fid)


def reset_emitted_regions():
    """Called at import start so a multi-plugin run doesn't leak regions."""
    _EMITTED_REGION_FIDS.clear()


def note_emitted_region(fid):
    """Record that `fid` was emitted, so a CELL may reference it in XCLR."""
    _EMITTED_REGION_FIDS.add(fid)


def region_was_emitted(fid) -> bool:
    """True when convert_REGN emitted `fid`."""
    return fid in _EMITTED_REGION_FIDS


# ---------------------------------------------------------------------------
#   Game settings and globals
# ---------------------------------------------------------------------------

#: TES4 globals Skyrim already ships at the SAME FormIDs; never re-emitted.
_ENGINE_GLOBALS = {'gamehour', 'gamedayspassed', 'gameday', 'gamemonth',
                   'gameyear', 'timescale'}

#: Globals whose authored default is replaced. See: docs/commentary/tes5_import_quest.md#global-default-overrides
_GLOBAL_DEFAULTS = {'vardistantblur': -1.0}


def convert_GLOB(rec: dict) -> bytes:
    """GLOB → GLOB, dropping the globals Skyrim's engine already defines.

    See: docs/commentary/tes5_import_quest.md#engine-globals
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid and edid.lower() in _ENGINE_GLOBALS:
        return b''
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    type_char = get_str(rec, 'FNAM.Type', 'f')
    subs += pack_uint8_subrecord('FNAM', ord(type_char[0]) if type_char else ord('f'))
    value = _GLOBAL_DEFAULTS.get((edid or '').lower(), get_float(rec, 'FLTV.Value'))
    subs += pack_float_subrecord('FLTV', value)
    return pack_record('GLOB', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_GMST(rec: dict) -> bytes:
    """GMST → GMST; the EditorID's leading s/f/i prefix types the DATA value."""
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    value_str = get_str(rec, 'DATA.Value')
    if edid and edid.startswith('s'):
        subs += pack_string_subrecord('DATA', value_str)
    elif edid and edid.startswith('f'):
        try:
            subs += pack_float_subrecord('DATA', float(value_str))
        except ValueError:
            subs += pack_uint32_subrecord('DATA', 0)
    else:
        try:
            subs += pack_uint32_subrecord('DATA', int(value_str))
        except ValueError:
            subs += pack_uint32_subrecord('DATA', 0)
    return pack_record('GMST', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)
