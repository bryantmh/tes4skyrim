"""Every converted TES3/TES4 actor's disposition toward the player, and its aggression, as faction ranks.

Oblivion and Morrowind keep one disposition per actor, and dialogue conditions,
scripts and the attack rule all read it. Skyrim has no such value, so it is the
actor's rank in TES4DispositionFaction: dialogue reads it with GetFactionRank,
TES4Polyfill reads and writes it for Get/ModDisposition, and MorrowindRuntime
writes every change it makes. The authored Aggression (TES3 Fight) the rule
compares against is the actor's rank in TES4AggressionFaction; an actor past
the line joins TES4DispositionHostileFaction, an Enemy of the player. A TES3
hostile creature joins TES3HostileCreatureFaction instead, also an Enemy of
the player alone, and every Guard-class NPC joins TES3GuardFaction, a marker
MorrowindRuntime reads to set guards on such a creature once it fights.

See: docs/commentary/tes5_import_actors.md#faction-player-disposition
"""

import struct

from ..base.owned_records import adopted_or_derived, clamp_rank, hidden_faction, owner_row
from ..base.writer import pack_subrecord

#: The hidden faction whose rank is the actor's disposition toward the player.
DISPOSITION_FACTION = 'TES4DispositionFaction'

#: The hidden faction whose rank is the actor's TES4 Aggression.
AGGRESSION_FACTION = 'TES4AggressionFaction'

#: The hidden faction an actor joins once its disposition falls below aggression - 5.
HOSTILE_FACTION = 'TES4DispositionHostileFaction'

#: A TES3 creature's hostile faction: guards attack its members.
HOSTILE_CREATURE_FACTION = 'TES3HostileCreatureFaction'

#: Every TES3 Guard-class NPC.
GUARD_FACTION = 'TES3GuardFaction'

#: Each faction's row name in MorrowindRuntime's effects_formid.txt.
_RUNTIME_ROWS = {DISPOSITION_FACTION: 'disposition', AGGRESSION_FACTION: 'aggression',
                 HOSTILE_FACTION: 'hostile', HOSTILE_CREATURE_FACTION: 'hostile_creature',
                 GUARD_FACTION: 'guard'}

#: Skyrim.esm PlayerFaction, and XNAM Group Combat Reaction Enemy.
_PLAYER_FACTION, _ENEMY = 0x00000DB1, 1

#: Each faction's Enemies. The guards' faction is only a marker: MorrowindRuntime sets them on creatures in combat.
_ENEMIES = {HOSTILE_FACTION: ('PlayerFaction',), HOSTILE_CREATURE_FACTION: ('PlayerFaction',)}

#: EditorID -> this run's FormID; empty for a source without dispositions.
_fids: dict = {}

#: Holds True for a TES3 source's run, which attacks by OpenMW's rule.
_tes3_run: list = []


def _faction(fid: int, edid: str) -> bytes:
    """The hidden FACT for `edid`, an Enemy of each faction `_ENEMIES` names."""
    relations = b''.join(
        pack_subrecord('XNAM', struct.pack('<IiI', _fids.get(enemy, _PLAYER_FACTION), 0, _ENEMY))
        for enemy in _ENEMIES.get(edid, ()))
    return hidden_faction(fid, edid, relations)


def create_disposition_factions(writer, master_index, wanted: bool, tes3: bool = False) -> dict:
    """{EditorID: FormID} of the factions, a master's adopted; {} unless `wanted`.

    `tes3` selects OpenMW's attack rule and adds its guard and creature factions.
    See: docs/commentary/tes5_import_actors.md#tes3-fight-targets-the-player
    """
    _fids.clear()
    _tes3_run[:] = [True] if wanted and tes3 else []
    if not wanted:
        return {}
    edids = [DISPOSITION_FACTION, AGGRESSION_FACTION, HOSTILE_FACTION]
    edids += [HOSTILE_CREATURE_FACTION, GUARD_FACTION] if tes3 else []
    own = []
    for edid in edids:
        _fids[edid], mine = adopted_or_derived(writer, master_index, 'FACT', edid)
        own += [edid] if mine else []
    for edid in own:
        writer.add_record('FACT', _faction(_fids[edid], edid))
    return dict(_fids)


def disposition_faction() -> int:
    """TES4DispositionFaction's FormID, or 0 when this run has none."""
    return _fids.get(DISPOSITION_FACTION, 0)


def disposition_memberships(disposition: int, aggression: int) -> list:
    """[(faction, rank)]: the starting disposition toward the player and the authored Aggression."""
    if not _fids:
        return []
    return [(_fids[DISPOSITION_FACTION], clamp_rank(disposition)),
            (_fids[AGGRESSION_FACTION], clamp_rank(aggression))]


def tes3_rule() -> bool:
    """True when this run's actors attack by OpenMW's rule: a TES3 source."""
    return bool(_tes3_run)


def hostile_memberships(hostile: bool, creature: bool = False, guard: bool = False) -> list:
    """[(faction, 0)]: the player's enemy faction for a `hostile` actor (a TES3
    `creature`'s own one), and the guards' faction for a TES3 `guard`."""
    enemy = _fids.get(HOSTILE_CREATURE_FACTION) if creature else None
    enemy = hostile and (enemy or _fids.get(HOSTILE_FACTION))
    guards = guard and _fids.get(GUARD_FACTION)
    return [(fid, 0) for fid in (enemy, guards) if fid]


def disposition_rows(plugin: str, masters: list) -> list:
    """`disposition`/`aggression`/`hostile=plugin|FormID`, for MorrowindRuntime."""
    return [owner_row(_RUNTIME_ROWS[edid], fid, plugin, masters) for edid, fid in _fids.items()]
