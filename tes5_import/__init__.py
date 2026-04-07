"""
TES5 Import Package — Convert TES4 exports to Skyrim SE binary format.

This package contains:
- constants: Lookup tables and mappings
- writer: Binary packing for TES5 records/groups
- text_reader: KEY=VALUE text parser (shared with tes4_export)
- record_types/: Per-group converter functions
- import_main: Import orchestrator
"""

from .constants import (
    BIPED_SLOT_MAP,
    DEFAULT_RACE,
    ENCH_CAST_TYPE_MAP,
    ENCH_TYPE_MAP,
    IMPORT_DISPATCH,
    MAP_MARKER_TYPE_MAP,
    MATT_MAP,
    RACE_MAP,
    SKIP_TYPES,
    TES4_SKILL_TO_TES5,
    TES5_SKILL_ORDER,
    TYPE_MAP,
    WEAPON_TYPE_MAP,
)

__all__ = [
    'RACE_MAP', 'DEFAULT_RACE', 'BIPED_SLOT_MAP', 'WEAPON_TYPE_MAP',
    'ENCH_TYPE_MAP', 'ENCH_CAST_TYPE_MAP', 'MAP_MARKER_TYPE_MAP',
    'MATT_MAP', 'TES4_SKILL_TO_TES5', 'TES5_SKILL_ORDER',
    'IMPORT_DISPATCH', 'TYPE_MAP', 'SKIP_TYPES',
]
