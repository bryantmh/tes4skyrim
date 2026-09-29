"""The keyword fallback names real Skyrim.esm races.

FormIDs checked against Skyrim.esm's RACE records (EDID by FormID); they match
docs/reference/creature_race_equivalence.md.
"""
from tes5_import.base.equivalents import resolve_creature_race

WOLF_RACE = 0x0001320A
CHAURUS_RACE = 0x000131EB


def test_wolf_and_dog_fall_back_to_wolf_race():
    """0x000131EB is ChaurusRace, so a wolf there renders as a Chaurus."""
    assert resolve_creature_race('CreatureWolf', 'Wolf')[0] == WOLF_RACE
    assert resolve_creature_race('CreatureWolfTimber', 'Timber Wolf')[0] == WOLF_RACE
    assert resolve_creature_race('CreatureDog', 'Dog')[0] == WOLF_RACE


def test_chaurus_stand_ins_fall_back_to_chaurus_race():
    """0x000131F3 is DwarvenSpiderRace, not ChaurusRace."""
    assert resolve_creature_race('SEScalon', 'Scalon')[0] == CHAURUS_RACE
    assert resolve_creature_race('CreatureLandDreugh', 'Land Dreugh')[0] == CHAURUS_RACE


def test_labels_name_the_formid_they_resolve_to():
    """A note's FormID must be the one returned, or reports mislead."""
    fid, note, _ = resolve_creature_race('CreatureWolf', 'Wolf')
    assert '%08X' % fid in note
    fid, note, _ = resolve_creature_race('SEScalon', 'Scalon')
    assert '%08X' % fid in note
