"""
The NPC stat autocalc the sidecar runs for a 12-byte NPDT, checked against
OpenMW's formula worked by hand for a Wood Elf Commoner at level 2:

  Personality = 40 race + 10 class + round(1.0 + 1.0 + 0.5) = 52, the growth
  being two major skills and one minor governed by it; Luck governs none.
  Speechcraft = 25 major + 5 base + 5 specialization + 1 * 1.5 = 36.5 -> 36,
  rounded half to even as round_ieee_754 does.
  Illusion, a minor outside the specialization = 10 + 5 + 1.0 = 16.
"""

from tes5_import.dialogue.morrowind_autocalc import (autocalc_attributes,
                                                     autocalc_skills)

#: Personality and Luck, Speechcraft and Mercantile, Illusion: TES3 indices.
PERSONALITY, LUCK = 6, 7
MERCANTILE, SPEECHCRAFT, ILLUSION = 24, 25, 12

#: Wood Elf: no Personality skill bonus; male Personality 40, Luck 40.
RACE = {'bonus': {19: 10, 21: 10, 23: 15, 16: 5, 20: 5},
        'male': [30, 40, 30, 50, 50, 30, 40, 40],
        'female': [30, 40, 30, 50, 50, 30, 40, 40]}

#: Commoner: Personality favored, Stealth; Speechcraft and Mercantile major, Illusion minor.
CLASS = {'attributes': [PERSONALITY, 5], 'specialization': 2,
         'minor': {ILLUSION, 8, 21, 19, 1},
         'major': {SPEECHCRAFT, MERCANTILE, 23, 20, 22}, 'services': 0}

#: Only the skills governed by Personality matter to its growth.
SKILLS = {SPEECHCRAFT: {'attribute': PERSONALITY, 'specialization': 2},
          MERCANTILE: {'attribute': PERSONALITY, 'specialization': 2},
          ILLUSION: {'attribute': PERSONALITY, 'specialization': 1}}


def test_personality_grows_by_its_skills_and_luck_does_not():
    """The worked Personality and Luck above."""
    values = autocalc_attributes(RACE, CLASS, SKILLS, level=2, female=False)
    assert values[PERSONALITY] == 52
    assert values[LUCK] == 40


def test_major_skill_in_the_class_specialization():
    """The worked Speechcraft and Illusion above."""
    values = autocalc_skills(RACE, CLASS, SKILLS, level=2)
    assert values[SPEECHCRAFT] == 36
    assert values[ILLUSION] == 16


def test_stats_cap_at_100():
    """A derived skill never passes 100 however high the level."""
    values = autocalc_skills(RACE, CLASS, SKILLS, level=200)
    assert values[SPEECHCRAFT] == 100
