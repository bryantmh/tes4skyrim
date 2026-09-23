"""FO3/FNV marker base objects that have no Oblivion counterpart.

FO3/FNV reuse the low FormID space Oblivion fills with real content -- 0x21 is
FNV's CollisionMarker and Oblivion's FlameNode3, 0x15 is MultiBoundMarker and
JailPants -- so these substitutions must never reach a TES4 plugin. The table
is selected per source, not merged into TES4_MARKER_FORMID_TO_SKYRIM.

See: docs/commentary/tes4_export_falloutnv.md#marker-base-objects
See: docs/commentary/tes4_export_falloutnv.md#child-worldspaces
"""

from .common import get_float, get_int

_XMARKER = 0x0000003B
_XMARKER_HEADING = 0x00000034

#: FO3/FNV marker base FormID -> the Skyrim.esm invisible marker to stand in.
FALLOUT_MARKER_FORMID_TO_SKYRIM = {
    0x00000015: _XMARKER,          # MultiBoundMarker
    0x0000001F: _XMARKER,          # RoomMarker
    0x00000021: _XMARKER,          # CollisionMarker
    0x00000023: _XMARKER,          # AudioMarker
    0x00000032: _XMARKER_HEADING,  # COCMarkerHeading
    0x00000033: _XMARKER,          # RadiationMarker
}

#: Record types only FO3/FNV emit; their presence identifies the source game.
FALLOUT_ONLY_SIGS = ('NAVM', 'TERM', 'MSTT', 'IDLM', 'PWAT', 'CCRD', 'REPU')

#: True while converting an FO3/FNV source; set once per plugin at import start.
_IS_FALLOUT_SOURCE = []


def register_fallout_source(by_type: dict):
    """Detect and record whether this run's source plugin is FO3/FNV."""
    _IS_FALLOUT_SOURCE.clear()
    if any(by_type.get(sig) for sig in FALLOUT_ONLY_SIGS):
        _IS_FALLOUT_SOURCE.append(True)


def is_fallout_source() -> bool:
    """True when this run's source plugin is FO3/FNV."""
    return bool(_IS_FALLOUT_SOURCE)


def marker_substitute(name_raw: int):
    """The Skyrim invisible marker standing in for an FO3/FNV marker base."""
    if not _IS_FALLOUT_SOURCE:
        return None
    return FALLOUT_MARKER_FORMID_TO_SKYRIM.get(name_raw)


#: TES5 PNAM bits: land, LOD, map, water, climate, sky cell (bit 5 is FO3-only).
TES5_PARENT_USE_MASK = 0x5F

#: DATA bits TES4, FO3/FNV and TES5 all define: Small World, Can't Fast Travel.
_SHARED_WORLD_FLAGS = 0x03


def parent_use_flags(rec: dict):
    """PNAM for a child worldspace: the authored FO3/FNV bits, else None.

    See: docs/commentary/tes4_export_falloutnv.md#child-worldspaces
    """
    flags = get_int(rec, 'PNAM.Flags', None)
    return None if flags is None else flags & TES5_PARENT_USE_MASK


def world_map_offset(rec: dict) -> tuple:
    """ONAM (scale, x, y, z): the authored FO3/FNV map offset, else identity."""
    return (get_float(rec, 'ONAM.Scale', 1.0),
            get_float(rec, 'ONAM.CellXOffset'),
            get_float(rec, 'ONAM.CellYOffset'), 0.0)


def tes5_world_flags(flags: int) -> int:
    """WRLD DATA flags rebased onto the TES5 bit layout."""
    return (flags & _SHARED_WORLD_FLAGS) | (0x08 if flags & 0x10 else 0)
