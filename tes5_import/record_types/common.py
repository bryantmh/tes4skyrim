"""
Shared helper functions for TES5 record converters.
"""

from ..text_reader import get_float, get_formid, get_int, get_str
from ..writer import (
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


def _prefix_path(path: str) -> str:
    """Prefix asset path with tes4\\ namespace.
    Strips leading 'textures\\' if present since Skyrim auto-prefixes it."""
    if not path:
        return path
    p = path
    if p.lower().startswith('textures\\') or p.lower().startswith('textures/'):
        p = p[9:]
    if not p.lower().startswith('tes4\\') and not p.lower().startswith('tes4/'):
        return 'tes4\\' + p
    return p


def _common_header_subs(rec: dict, need_obnd: bool = True, need_full: bool = True,
                        obnd_sig: str = '') -> bytes:
    """Build common leading subrecords: EDID, OBND, FULL.

    obnd_sig: record type signature for type-aware OBND defaults.
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    if need_obnd:
        bounds = _OBND_DEFAULTS.get(obnd_sig, _OBND_DEFAULT)
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
    'GRAS': (-10, -10, 0, 10, 10, 10),
    'TREE': (-50, -50, 0, 50, 50, 150),
    # Actors
    'NPC_': (-12, -12, 0, 12, 12, 60),
    # Effects
    'ENCH': (-5, -5, 0, 5, 5, 5),
    'SPEL': (-5, -5, 0, 5, 5, 5),
}


def _simple_object(rec: dict, sig: str, has_full: bool = True,
                   has_model: bool = True, extra_subs: bytes = b'') -> bytes:
    """Generic simple-object converter.

    Produces: EDID + OBND + FULL + MODL + extra_subs.
    """
    subs = _common_header_subs(rec, need_full=has_full, obnd_sig=sig)
    if has_model:
        path = get_str(rec, 'Model.MODL')
        if path:
            subs += pack_string_subrecord('MODL', _prefix_path(path))
    subs += extra_subs
    return pack_record(sig, get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def _convert_biped_flags(tes4_flags: int) -> int:
    """Convert TES4 biped flags to TES5 first person flags.

    Returns PRIMARY equip slots plus equipment-conflict extras (e.g. helmets
    block the Circlet slot so you can't wear both simultaneously).
    Body-coverage extras (e.g. ForeArms on a cuirass) are NOT included here —
    they go on the ARMA (via ARMA_BODY_COVERAGE_EXTRA) so that the ARMO only
    occupies its own equipment slot and doesn't conflict with other equipped items.
    """
    from ..constants import BIPED_SLOT_MAP, BIPED_SLOT_EXTRA
    tes5 = 0
    for tes4_bit, tes5_bit in BIPED_SLOT_MAP.items():
        if tes4_flags & (1 << tes4_bit):
            tes5 |= (1 << tes5_bit)
    # Apply equipment-conflict extras (e.g. helmet → also block Circlet slot)
    for tes5_bit, extra_bits in BIPED_SLOT_EXTRA.items():
        if tes5 & (1 << tes5_bit):
            for eb in extra_bits:
                tes5 |= (1 << eb)
    return tes5
