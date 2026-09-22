"""Morrowind-specific NPC_ stat conversion.

See: docs/commentary/tes5_import_actors.md#morrowind-health-is-absolute
"""

#: Starting Health on all 11 RACE_MAP target races in Skyrim.esm.
TES5_RACE_BASE_HEALTH = 50


def is_morrowind_npc(rec: dict) -> bool:
    """Whether this NPC_ record came from a Morrowind export.

    See: docs/commentary/tes4_export_morrowind.md#actors-and-placements
    """
    return bool(rec.get('MorrowindRace'))


def morrowind_health_and_level(health: int, level: int) -> tuple:
    """TES3 authored health -> (ACBS.HealthOffset, ACBS.Level), no level term.

    See: docs/commentary/tes5_import_actors.md#morrowind-health-is-absolute
    """
    offset = health - TES5_RACE_BASE_HEALTH
    return max(-32768, min(offset, 32767)), max(1, min(level, 65535))
