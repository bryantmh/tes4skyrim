"""
A TES3 NPC's attributes and skills, as OpenMW derives them for an autocalc
NPC_ (the 12-byte NPDT): the race's base values, the class bonuses, the
per-level growth from each skill's governing attribute and specialization.

The persuasion and barter formulae read the SPEAKER's Personality, Luck,
Speechcraft and Mercantile, and most NPCs author none of them -- the values
exist only once `MWClass::Npc` autocalculates them. This is that calculation,
run at import so the runtime reads a number instead of a race and a class.

See: docs/commentary/morrowind_runtime.md#npc-stats
"""

import struct

from tes4_export.tes3_reader import get_subrecord

#: RADT: 7 (skill, bonus) int pairs, 8 (male, female) int pairs, 4 floats, flags.
_RADT_FORMAT = '<14i16i4fi'

#: CLDT: 2 attributes, specialization, 5 x (minor, major) skills, playable, services.
_CLDT_FORMAT = '<2ii10iii'

#: SKDT: governing attribute, specialization, four use values.
_SKDT_FORMAT = '<ii4f'

#: TES3 has eight attributes and 27 skills; every stat caps at 100 when derived.
ATTRIBUTE_COUNT = 8
SKILL_COUNT = 27
_STAT_CAP = 100

#: A minor skill adds 0.5 to its attribute's growth per level, a major 1.0, any other 0.2.
_GROWTH_MINOR, _GROWTH_MAJOR, _GROWTH_OTHER = 0.5, 1.0, 0.2

#: Class skills start with a bonus: 10 for a minor, 25 for a major.
_BONUS_MINOR, _BONUS_MAJOR = 10, 25

#: Every skill starts at 5; one in the class's specialization gets 5 more and grows 0.5 faster.
_SKILL_BASE = 5
_SPEC_BONUS = 5
_SPEC_GROWTH = 0.5

#: A class skill grows 1.0 per level, any other 0.1.
_CLASS_GROWTH, _OTHER_GROWTH = 1.0, 0.1


def parse_race(rec) -> dict:
    """`{'bonus': {skill: bonus}, 'male': [8], 'female': [8]}` from RADT."""
    data = get_subrecord(rec, 'RADT')
    if data is None or len(data.data) < struct.calcsize(_RADT_FORMAT):
        return {}
    values = struct.unpack_from(_RADT_FORMAT, data.data)
    bonus = {values[i]: values[i + 1] for i in range(0, 14, 2)
             if values[i] >= 0}
    attributes = values[14:30]
    return {'bonus': bonus, 'male': list(attributes[0::2]),
            'female': list(attributes[1::2])}


def parse_class(rec) -> dict:
    """`{'attributes', 'specialization', 'minor', 'major', 'services'}` from CLDT."""
    data = get_subrecord(rec, 'CLDT')
    if data is None or len(data.data) < struct.calcsize(_CLDT_FORMAT):
        return {}
    values = struct.unpack_from(_CLDT_FORMAT, data.data)
    skills = values[3:13]
    return {'attributes': [a for a in values[:2] if a >= 0],
            'specialization': values[2],
            'minor': set(skills[0::2]), 'major': set(skills[1::2]),
            'services': values[14]}


def parse_skill(rec) -> tuple:
    """`(index, {'attribute', 'specialization', 'use': [4]})` from INDX and
    SKDT, or `(None, {})` when the record lacks either."""
    index = get_subrecord(rec, 'INDX')
    data = get_subrecord(rec, 'SKDT')
    if index is None or data is None or len(data.data) < 24:
        return None, {}
    values = struct.unpack_from(_SKDT_FORMAT, data.data)
    return (struct.unpack_from('<i', index.data)[0],
            {'attribute': values[0], 'specialization': values[1],
             'use': list(values[2:6])})


def _class_growth(index: int, clazz: dict) -> float:
    """What one skill adds to its attribute's per-level growth."""
    if index in clazz['major']:
        return _GROWTH_MAJOR
    if index in clazz['minor']:
        return _GROWTH_MINOR
    return _GROWTH_OTHER


def autocalc_attributes(race: dict, clazz: dict, skills: dict, level: int,
                        female: bool) -> list:
    """`MWClass::autoCalculateAttributes`: eight values, capped at 100."""
    values = list(race['female' if female else 'male'])
    for attribute in clazz['attributes']:
        values[attribute] += 10
    for attribute in range(ATTRIBUTE_COUNT):
        growth = sum(_class_growth(index, clazz)
                     for index, skill in skills.items()
                     if skill['attribute'] == attribute)
        values[attribute] = min(round(values[attribute] + (level - 1) * growth),
                                _STAT_CAP)
    return values


def autocalc_skills(race: dict, clazz: dict, skills: dict, level: int) -> list:
    """`MWClass::autoCalculateSkills`: 27 values, capped at 100."""
    values = [0] * SKILL_COUNT
    for index in clazz['minor']:
        if 0 <= index < SKILL_COUNT:
            values[index] += _BONUS_MINOR
    for index in clazz['major']:
        if 0 <= index < SKILL_COUNT:
            values[index] += _BONUS_MAJOR
    for index in range(SKILL_COUNT):
        skill = skills.get(index, {})
        in_class = index in clazz['minor'] or index in clazz['major']
        growth = _CLASS_GROWTH if in_class else _OTHER_GROWTH
        spec_bonus = 0
        if skill.get('specialization') == clazz['specialization']:
            growth += _SPEC_GROWTH
            spec_bonus = _SPEC_BONUS
        values[index] = min(round(values[index] + _SKILL_BASE
                                  + race['bonus'].get(index, 0) + spec_bonus
                                  + (level - 1) * growth), _STAT_CAP)
    return values
