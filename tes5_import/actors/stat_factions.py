"""Every converted actor's TES4 attributes and kept skills, as hidden faction ranks.

Skyrim has no attribute actor values. Each attribute, and each skill the sheet
keeps (Athletics, Hand-to-Hand, Acrobatics), is a hidden conversion-owned
faction; a converted NPC_ or creature joins each at its authored value, so a
dialogue condition reads it with GetFactionRank and a script with
Get/SetFactionRank, with or without MorrowindRuntime. Factions never make
their members allies: 200 of Skyrim.esm's 1,084 factions list themselves as
Ally because membership alone does not.

See: docs/commentary/morrowind_runtime.md#npc-attributes
"""

from ..base.owned_records import (TES4_ATTRIBUTE_NAMES, adopt_or_write, hidden_faction,
                                  owner_row)
from ..base.text_reader import get_int

#: The kept skills by TES4 actor value, as NPC_ DATA names them.
KEPT_SKILLS = {13: 'Athletics', 17: 'HandToHand', 26: 'Acrobatics'}

#: TES4 actor value -> its faction's EditorID: the eight attributes, then the kept skills.
STAT_FACTIONS = {**{av: f'TES4Attribute{name}' for av, name in enumerate(TES4_ATTRIBUTE_NAMES)},
                 **{av: f'TES4Skill{name}' for av, name in KEPT_SKILLS.items()}}

#: A faction rank is a signed byte; the few creatures authored above it hold the most it can.
RANK_MAX = 127

#: TES4 actor value -> this run's faction FormID; empty for a source without attributes.
_factions: dict = {}


def create_stat_factions(writer, master_index, wanted: bool) -> dict:
    """{EditorID: FormID} of every stat faction, a master's adopted; {} when not `wanted`."""
    _factions.clear()
    if not wanted:
        return {}
    for av, edid in STAT_FACTIONS.items():
        _factions[av], _written = adopt_or_write(
            writer, master_index, 'FACT', edid, lambda new, e=edid: hidden_faction(new, e))
    return {STAT_FACTIONS[av]: fid for av, fid in _factions.items()}


def stat_faction(av: int) -> int:
    """The faction holding TES4 actor value `av`, or 0."""
    return _factions.get(av, 0)


def stat_memberships(rec: dict, creature: bool = False) -> list:
    """[(faction, rank)] for an NPC_'s or creature's authored attributes and, for
    an NPC_, its kept skills; a creature's skills are its specialization's."""
    names = dict(enumerate(TES4_ATTRIBUTE_NAMES))
    if not creature:
        names.update(KEPT_SKILLS)
    return [(_factions[av], max(0, min(get_int(rec, f'DATA.{name}'), RANK_MAX)))
            for av, name in names.items() if av in _factions]


def faction_rows(plugin: str, masters: list) -> list:
    """`faction.<av>=Plugin|FormID` per stat faction, for MorrowindRuntime."""
    return [owner_row(f'faction.{av}', fid, plugin, masters)
            for av, fid in sorted(_factions.items())]
