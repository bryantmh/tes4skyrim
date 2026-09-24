"""What a Morrowind spell merchant sells, and at what base price.

OpenMW's `SpellBuyingWindow::setPtr` offers every spell the merchant knows that
is an ordinary spell and, for an NPC, not one of its race's powers, at
`calcSpellCost * fSpellValueMult`. What an NPC knows is its authored list plus,
when its NPDT is the autocalculated form, what `autoCalcNpcSpells` picks from
the whole spell store. Both read what the text export flattens -- magnitude
ranges and the store's record order -- so the chain's binaries are read here.
Float math is float32, as the engine does it.
"""

import math
import struct

import numpy as np

from tes4_export.tes3_reader import (get_all_subrecords, get_string,
                                       get_subrecord, read_file)

from ..dialogue.morrowind_autocalc import parse_class, parse_race, parse_skill
from ..dialogue.morrowind_sidecar_source import (aidt_services, npc_is_autocalc,
                                                 npc_services, npc_stats,
                                                 plugin_chain)

#: SPDT: type, cost, flags.
_SPDT = '<iiI'

#: ENAM: effect, skill, attribute, range, area, duration, min, max.
_ENAM = '<hbbiiiii'

#: The MEDT head: school, base cost, flags.
_MEDT = '<ifi'

#: SPDT type Spell, and the SPDT flags Autocalc and Always Succeeds.
_TYPE_SPELL = 0
_F_AUTOCALC = 0x1
_F_ALWAYS = 0x4

#: ENAM range Target.
_RANGE_TARGET = 2

#: MGEF flags: TargetSkill, TargetAttribute, NoDuration, NoMagnitude, AppliedOnce.
_TARGET_SKILL = 0x1
_TARGET_ATTRIBUTE = 0x2
_NO_DURATION = 0x4
_NO_MAGNITUDE = 0x8
_APPLIED_ONCE = 0x1000

#: The MEDT bits a TES3 plugin may set; OpenMW replaces the rest with `_FIXED_FLAGS`.
_MODIFIABLE_FLAGS = 0x200 | 0x400 | 0x800

#: OpenMW `HardcodedFlags` (components/esm3/loadmgef.cpp): each engine effect's fixed flags.
_FIXED_FLAGS = (
    0x11c8, 0x11c0, 0x11c8, 0x11e0, 0x11e0, 0x11e0, 0x11e0,
    0x11d0, 0x11c0, 0x11c0, 0x11e0, 0x11c0, 0x11184, 0x11184, 0x1f0, 0x1f0, 0x1f0, 0x11d2, 0x11f0, 0x11d0,
    0x11d0, 0x11d1, 0x1d2, 0x1f0, 0x1d0, 0x1d0, 0x1d1, 0x1f0, 0x11d0, 0x11d0, 0x11d0, 0x11d0, 0x11d0, 0x11d0,
    0x11d0, 0x11d0, 0x11d0, 0x1d0, 0x1d0, 0x11c8, 0x31c0, 0x11c0, 0x11c0, 0x11c0, 0x1180, 0x11d8, 0x11d8,
    0x11d0, 0x11d0, 0x11180, 0x11180, 0x11180, 0x11180, 0x11180, 0x11180, 0x11180, 0x11180, 0x11c4, 0x111b8,
    0x1040, 0x104c, 0x104c, 0x104c, 0x104c, 0x1040, 0x1040, 0x1040, 0x11c0, 0x11c0, 0x1cc, 0x1cc, 0x1cc, 0x1cc,
    0x1cc, 0x1c2, 0x1c0, 0x1c0, 0x1c0, 0x1c1, 0x11c2, 0x11c0, 0x11c0, 0x11c0, 0x11c1, 0x11c0, 0x21192, 0x20190,
    0x20190, 0x20190, 0x21191, 0x11c0, 0x11c0, 0x11c0, 0x11c0, 0x11c0, 0x11c0, 0x11c0, 0x11c0, 0x11c0, 0x11c0,
    0x1c0, 0x11190, 0x9048, 0x9048, 0x9048, 0x9048, 0x9048, 0x9048, 0x9048, 0x9048, 0x9048, 0x9048, 0x9048,
    0x9048, 0x9048, 0x9048, 0x9048, 0x11c0, 0x1180, 0x1180, 0x5048, 0x5048, 0x5048, 0x5048, 0x5048, 0x5048,
    0x1188, 0x5048, 0x5048, 0x5048, 0x5048, 0x5048, 0x1048, 0x104c, 0x1048, 0x40, 0x11c8, 0x1048, 0x1048,
    0x1048, 0x1048, 0x1048, 0x1048)

#: MEDT school index -> (the TES3 skill that governs it, the GMST capping its autocalc picks).
_SCHOOLS = ((11, 'iautospellalterationmax'), (13, 'iautospellconjurationmax'),
            (10, 'iautospelldestructionmax'), (12, 'iautospellillusionmax'),
            (14, 'iautospellmysticismmax'), (15, 'iautospellrestorationmax'))

#: TES3 attribute indices the autocalc reads.
_INTELLIGENCE, _WILLPOWER, _LUCK = 1, 2, 7

#: AIDT services bit Spells.
_SERVICE_SPELLS = 0x800

#: Morrowind's own values (OpenMW-CS defaultgmsts.cpp) for a GMST the chain never sets.
_GMST_DEFAULTS = {
    'fspellvaluemult': 10.0, 'feffectcostmult': 0.5, 'fnpcbasemagickamult': 2.0,
    'fautospellchance': 80.0, 'iautospelltimescancast': 3,
    'iautospellattskillmin': 70, 'iautospellalterationmax': 5,
    'iautospellconjurationmax': 2, 'iautospelldestructionmax': 5,
    'iautospellillusionmax': 5, 'iautospellmysticismmax': 5,
    'iautospellrestorationmax': 5}

#: The largest C int, the autocalc's "no cheapest spell yet".
_INT_MAX = 2 ** 31 - 1

#: An effect index no MGEF record supplies: no school, no cost, no flags.
_NO_EFFECT = (-1, np.float32(0), 0)


# ---------------------------------------------------------------------------
#   Reading the chain
# ---------------------------------------------------------------------------


def _spell(rec):
    """`{type, cost, flags, effects}` for one SPEL, or None without SPDT."""
    spdt = get_subrecord(rec, 'SPDT')
    if spdt is None or len(spdt.data) < struct.calcsize(_SPDT):
        return None
    kind, cost, flags = struct.unpack_from(_SPDT, spdt.data)
    effects = [struct.unpack_from(_ENAM, sub.data)
               for sub in get_all_subrecords(rec, 'ENAM')
               if len(sub.data) >= struct.calcsize(_ENAM)]
    return {'type': kind, 'cost': cost, 'flags': flags, 'effects': effects}


def _effect(rec):
    """`(index, (school, base cost, flags))` for one MGEF, flags as OpenMW loads them."""
    index = get_subrecord(rec, 'INDX')
    medt = get_subrecord(rec, 'MEDT')
    if index is None or medt is None or len(medt.data) < struct.calcsize(_MEDT):
        return None, None
    at = struct.unpack_from('<i', index.data)[0]
    school, base, flags = struct.unpack_from(_MEDT, medt.data)
    flags &= _MODIFIABLE_FLAGS
    if 0 <= at < len(_FIXED_FLAGS):
        flags |= _FIXED_FLAGS[at]
    return at, (school, np.float32(base), flags)


def _gmst_value(rec):
    """A GMST's INTV or FLTV number, or None."""
    for sig, fmt in (('INTV', '<i'), ('FLTV', '<f')):
        sub = get_subrecord(rec, sig)
        if sub is not None and len(sub.data) >= 4:
            return struct.unpack_from(fmt, sub.data)[0]
    return None


def _spell_ids(rec) -> list:
    """The lowercased spell ids an NPC_, CREA or RACE lists in NPCS."""
    return [get_string(sub).lower() for sub in get_all_subrecords(rec, 'NPCS')]


def _take_spell(tables: dict, key: str, rec) -> None:
    """A SPEL in store order: an override keeps its slot, a deletion leaves it."""
    if rec.deleted:
        tables['spells'].pop(key, None)
    else:
        tables['spells'][key] = _spell(rec)


def _take_actor(tables: dict, key: str, rec) -> None:
    """An NPC_ or CREA, the last plugin's copy."""
    if rec.deleted:
        tables['actors'].pop(key, None)
    else:
        tables['actors'][key] = rec


def _take_race(tables: dict, key: str, rec) -> None:
    """A RACE's stat bonuses and the powers it grants."""
    tables['races'][key] = parse_race(rec)
    tables['powers'][key] = _spell_ids(rec)


def _take_class(tables: dict, key: str, rec) -> None:
    """A CLAS's attributes, skills and services."""
    tables['classes'][key] = parse_class(rec)


def _take_gmst(tables: dict, key: str, rec) -> None:
    """A numeric GMST."""
    value = _gmst_value(rec)
    if value is not None:
        tables['gmsts'][key] = value


#: Record type -> what folds it into the tables, for records TES3 keys by id.
_TAKERS = {'SPEL': _take_spell, 'NPC_': _take_actor, 'CREA': _take_actor,
           'RACE': _take_race, 'CLAS': _take_class, 'GMST': _take_gmst}


def _take(tables: dict, rec) -> None:
    """Fold one record into the tables; a later plugin overrides in place."""
    if rec.type == 'SKIL':
        index, skill = parse_skill(rec)
        if index is not None:
            tables['skills'][index] = skill
        return
    if rec.type == 'MGEF':
        index, effect = _effect(rec)
        if index is not None:
            tables['effects'][index] = effect
        return
    take = _TAKERS.get(rec.type)
    key = (rec.record_id or '').lower()
    if take is not None and key:
        take(tables, key, rec)


def read_tables(chain: list) -> dict:
    """The chain's spells in store order, effects, races, classes, skills,
    GMSTs and actors, each plugin read once and a later one winning."""
    tables = {'spells': {}, 'effects': {}, 'races': {}, 'powers': {},
              'classes': {}, 'skills': {}, 'gmsts': {}, 'actors': {}}
    for _name, path in chain:
        for rec in read_file(path)[1]:
            _take(tables, rec)
    tables['spells'] = {key: spell for key, spell in tables['spells'].items()
                        if spell is not None}
    tables['gmsts'] = {**_GMST_DEFAULTS, **tables['gmsts']}
    return tables


# ---------------------------------------------------------------------------
#   Spell cost (MWMechanics::calcEffectCost / calcSpellCost)
# ---------------------------------------------------------------------------


def _effect_cost(entry: tuple, effect: tuple, mult) -> np.float32:
    """`calcEffectCost` for a game spell."""
    _school, base, flags = effect
    low, high, duration = entry[6], entry[7], entry[5]
    if flags & _NO_MAGNITUDE:
        low = high = 1
    if flags & _NO_DURATION:
        duration = 1
    if not flags & _APPLIED_ONCE:
        duration = max(1, duration)
    x = np.float32(0.5) * np.float32(max(1, low) + max(1, high))
    x *= np.float32(0.1) * base
    x *= np.float32(duration)
    x += np.float32(0.05) * np.float32(max(0, entry[4])) * base
    return x * mult


def spell_cost(spell: dict, tables: dict) -> int:
    """`calcSpellCost`: the authored cost, or the effects' when autocalculated."""
    if not spell['flags'] & _F_AUTOCALC:
        return spell['cost']
    mult = np.float32(tables['gmsts']['feffectcostmult'])
    total = np.float32(0)
    for entry in spell['effects']:
        effect = tables['effects'].get(entry[0], _NO_EFFECT)
        cost = max(np.float32(0), _effect_cost(entry, effect, mult))
        if entry[3] == _RANGE_TARGET:
            cost *= np.float32(1.5)
        total += cost
    return int(math.floor(float(total) + 0.5))


# ---------------------------------------------------------------------------
#   Autocalculated NPC spells (MWMechanics::autoCalcNpcSpells)
# ---------------------------------------------------------------------------


def _school_term(entry: tuple, effect: tuple, mult) -> np.float32:
    """The cost term `calcWeakestSchool` weighs one effect by."""
    _school, base, flags = effect
    low = 1 if flags & _NO_MAGNITUDE else entry[6]
    high = 1 if flags & _NO_MAGNITUDE else entry[7]
    duration = 0 if flags & _NO_DURATION else entry[5]
    if not flags & _APPLIED_ONCE:
        duration = max(1, duration)
    x = np.float32(0.5) * np.float32(max(1, low) + max(1, high))
    x *= np.float32(0.1) * base
    x *= np.float32(1 + duration)
    x += np.float32(0.05) * np.float32(max(1, entry[4])) * base
    x *= mult
    if entry[3] == _RANGE_TARGET:
        x *= np.float32(1.5)
    return x


def _weakest_school(spell: dict, skills: list, tables: dict):
    """`calcWeakestSchool`: the governing skill of the effect hardest to cast, or None."""
    mult = np.float32(tables['gmsts']['feffectcostmult'])
    lowest = np.float32(np.finfo(np.float32).max)
    school = None
    for entry in spell['effects']:
        effect = tables['effects'].get(entry[0], _NO_EFFECT)
        skill = _SCHOOLS[effect[0]][0] if 0 <= effect[0] < len(_SCHOOLS) else None
        term = np.float32(2 * skills[skill]) if skill is not None else np.float32(0)
        chance = term - _school_term(entry, effect, mult)
        if chance < lowest:
            lowest, school = chance, skill
    return school


def _stat_at_least(values: list, index: int, floor: int) -> bool:
    """Whether the stat at `index` exists and reaches `floor`."""
    return 0 <= index < len(values) and values[index] >= floor


def _skills_suffice(spell: dict, stats: dict, tables: dict) -> bool:
    """`attrSkillCheck`: each skill or attribute an effect names reaches the GMST floor."""
    floor = tables['gmsts']['iautospellattskillmin']
    for entry in spell['effects']:
        flags = tables['effects'].get(entry[0], _NO_EFFECT)[2]
        if flags & _TARGET_SKILL and not _stat_at_least(
                stats['skills'], entry[1], floor):
            return False
        if flags & _TARGET_ATTRIBUTE and not _stat_at_least(
                stats['attributes'], entry[2], floor):
            return False
    return True


def _cast_chance(spell: dict, cost: int, skill: int, stats: dict) -> np.float32:
    """`calcAutoCastChance` against the school already chosen."""
    if spell['type'] != _TYPE_SPELL or spell['flags'] & _F_ALWAYS:
        return np.float32(100)
    attributes = stats['attributes']
    return (np.float32(2 * stats['skills'][skill]) - np.float32(cost)
            + np.float32(0.2) * np.float32(attributes[_WILLPOWER])
            + np.float32(0.1) * np.float32(attributes[_LUCK]))


class _SchoolCap:
    """One school's running autocalc tally: how many picked, and the cheapest."""

    def __init__(self, limit: int):
        """A tally that is already full when the school may pick nothing."""
        self.count = 0
        self.limit = limit
        self.reached = limit <= 0
        self.min_cost = _INT_MAX
        self.weakest = None

    def take(self, key: str, cost: int, selected: list, costs: dict) -> None:
        """Record `key` as picked; once full, drop the cheapest and find the next.

        The cheapest is sought across EVERY school's picks, as OpenMW does.
        """
        if self.reached:
            if self.weakest in selected:
                selected.remove(self.weakest)
            self.min_cost = _INT_MAX
            for other in selected:
                if costs[other] < self.min_cost:
                    self.min_cost, self.weakest = costs[other], other
            return
        self.count += 1
        if self.count == self.limit:
            self.reached = True
        if cost < self.min_cost:
            self.weakest, self.min_cost = key, cost


def _candidate(key: str, spell: dict, stats: dict, powers: set,
               tables: dict, costs: dict):
    """The governing skill an autocalc spell would be picked under, or None."""
    gmsts = tables['gmsts']
    if spell['type'] != _TYPE_SPELL or not spell['flags'] & _F_AUTOCALC:
        return None
    magicka = (np.float32(gmsts['fnpcbasemagickamult'])
               * np.float32(stats['attributes'][_INTELLIGENCE]))
    if magicka < gmsts['iautospelltimescancast'] * costs[key] or key in powers:
        return None
    if not _skills_suffice(spell, stats, tables):
        return None
    return _weakest_school(spell, stats['skills'], tables)


def autocalc_spells(stats: dict, powers: set, tables: dict, costs: dict) -> list:
    """`autoCalcNpcSpells`: the spell ids an autocalculated NPC knows."""
    gmsts = tables['gmsts']
    caps = {skill: _SchoolCap(int(gmsts[gmst])) for skill, gmst in _SCHOOLS}
    selected = []
    for key, spell in tables['spells'].items():
        skill = _candidate(key, spell, stats, powers, tables, costs)
        if skill is None:
            continue
        cap = caps[skill]
        if cap.reached and costs[key] <= cap.min_cost:
            continue
        if (_cast_chance(spell, costs[key], skill, stats)
                < np.float32(gmsts['fautospellchance'])):
            continue
        selected.append(key)
        cap.take(key, costs[key], selected, costs)
    return selected


# ---------------------------------------------------------------------------
#   What each merchant sells
# ---------------------------------------------------------------------------


def _known(rec, tables: dict, costs: dict) -> tuple:
    """(every spell id the actor knows, its race's powers)."""
    own = _spell_ids(rec)
    if rec.type != 'NPC_':
        return own, set()
    rnam = get_subrecord(rec, 'RNAM')
    race = get_string(rnam).lower() if rnam is not None else ''
    powers = set(tables['powers'].get(race, ()))
    if npc_is_autocalc(rec):
        own = autocalc_spells(npc_stats(rec, tables), powers, tables,
                              costs) + own
    return own, powers


def _services(rec, tables: dict) -> int:
    """`getServices` for an NPC_ or CREA."""
    return npc_services(rec, tables) if rec.type == 'NPC_' else aidt_services(rec)


def _school(spell: dict, tables: dict) -> int:
    """The MEDT school index of the spell's first effect, or -1."""
    if not spell['effects']:
        return -1
    return tables['effects'].get(spell['effects'][0][0], _NO_EFFECT)[0]


def morrowind_sales(root: str, plugin: str, wanted: set) -> dict:
    """{lowercased actor id: [(lowercased spell id, base price, school index)]}
    for each spell merchant in `wanted`, as the TES3 chain ending in `plugin`
    defines it."""
    chain = plugin_chain(root, plugin)
    if not chain:
        return {}
    tables = read_tables(chain)
    costs = {key: spell_cost(spell, tables)
             for key, spell in tables['spells'].items()}
    mult = np.float32(tables['gmsts']['fspellvaluemult'])
    sales = {}
    for key in sorted(wanted):
        rec = tables['actors'].get(key)
        if rec is None or not _services(rec, tables) & _SERVICE_SPELLS:
            continue
        known, powers = _known(rec, tables, costs)
        offered = [spell for spell in dict.fromkeys(known)
                   if spell in tables['spells'] and spell not in powers
                   and tables['spells'][spell]['type'] == _TYPE_SPELL]
        sales[key] = [(spell, max(1, int(np.float32(costs[spell]) * mult)),
                       _school(tables['spells'][spell], tables))
                      for spell in offered]
    return sales
