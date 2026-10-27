"""Morrowind-specific NPC_ stat conversion.

An autocalc NPC_ (the 12-byte NPDT) authors no health, magicka or fatigue:
OpenMW derives them from the attributes it derives from race, class and
level. The run's TES3 tables give those, so each such NPC_ converts with the
pools Morrowind would give it.

See: docs/commentary/tes5_import_actors.md#morrowind-health-is-absolute
"""

from ..dialogue.morrowind_sidecar_source import npc_is_autocalc, npc_stats

#: Starting Health on all 11 RACE_MAP target races in Skyrim.esm.
TES5_RACE_BASE_HEALTH = 50

#: This run's TES3 tables (`chain_tables`), or empty for any other source.
_tables: list = []


def is_morrowind_npc(rec: dict) -> bool:
    """Whether this NPC_ record came from a Morrowind export.

    See: docs/commentary/tes4_export_morrowind.md#actors-and-placements
    """
    return bool(rec.get('MorrowindRace'))


def load_tes3_tables(tables) -> None:
    """Keep `tables` (None for a non-TES3 run) for `tes3_pools`."""
    _tables[:] = [tables] if tables else []


def tes3_pools(edid: str) -> dict:
    """`{health, magicka, fatigue}` OpenMW derives for autocalc NPC_ `edid`, else {}."""
    tables = _tables[0] if _tables else None
    rec = tables['actors'].get(edid.lower()) if tables else None
    if rec is None or rec.type != 'NPC_' or not npc_is_autocalc(rec):
        return {}
    stats = npc_stats(rec, tables, tables['gmsts']['fnpcbasemagickamult'])
    return {key: stats[key] for key in ('health', 'magicka', 'fatigue') if key in stats}
