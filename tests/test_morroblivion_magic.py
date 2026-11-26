"""Morroblivion's scripted magic restored to Morrowind's effects, and the table
that tells MorrowindRuntime which effect records it carries.

See: docs/commentary/tes4_export_morrowind.md#restored-magic
"""

from tes4_export import morroblivion_magic as mm
from tes4_export.record_types.morrowind_magic import effect_editor_id
from tes5_import.dialogue.morrowind_teleport import copy_rows, teleport_lines
from tes5_import.record_types import magic_variants
from tes5_import.record_types.magic import A_SCRIPT, AV_NONE
from tes5_import.record_types.magic_morrowind import (MW_RUNTIME_EFFECTS, mw_actor_value,
                                                      mw_archetype, mw_attribute_variant,
                                                      mw_converts, mw_needs_runtime,
                                                      runtime_attribute_index)

#: The TES4 actor value of Acrobatics, which lands on Skyrim's JumpingBonus, as Jump does.
_ACROBATICS = 26


def _corpus(*texts) -> mm._Corpus:
    """A corpus of `(owning script, source)` texts, with no export read."""
    corpus = object.__new__(mm._Corpus)
    corpus.scripts, corpus.texts, corpus.cited, corpus.forms = {}, list(texts), set(), {}
    corpus._written = {}
    corpus.callers = mm._callers(corpus.texts)
    return corpus


def test_an_effect_converts_when_skyrim_or_the_runtime_carries_it():
    """Teleports, SwiftSwim and Levitate convert; Disintegrate Weapon waits for the runtime; Fortify Health is native."""
    assert all(mw_converts(index, -1) for index in MW_RUNTIME_EFFECTS)
    assert mw_converts(1, -1) and mw_needs_runtime(1)
    assert mw_converts(10, -1)
    assert not mw_converts(37, -1)
    assert mw_converts(80, -1)
    assert not mw_converts(83, -1)
    assert mw_converts(83, _ACROBATICS)


def test_fortify_acrobatics_raises_what_jump_raises():
    """Fortify Acrobatics moves the same actor value as the Jump effect, not Stamina."""
    assert mw_actor_value(83, _ACROBATICS) == mw_actor_value(9, -1)


def test_a_scripts_writes_leave_its_own_locals_out():
    """Globals, quest variables and moved references count; locals and the player do not."""
    text = ('short count\nref target\nset count to 1\nset PlayerInMorrowind to 1\n'
            'set mwTeleportManager.World to 2\nmwMarkerRat.MoveTo Player\n'
            'target.Disable\nPlayer.MoveTo mwMarkerRat\n')
    assert mm._writes(text) == {'playerinmorrowind', 'mwteleportmanager.world', 'mwmarkerrat'}


def test_a_placeholder_script_is_inert():
    """Blocks, declarations and `return` do nothing; any other line is a statement."""
    assert mm._inert('ScriptName Eloth\n\nBEGIN ScriptEffectStart\n\tRETURN ; nothing\nEND\n')
    assert not mm._inert('scn Ghost\nbegin ScriptEffectStart\n\tSetActorAlpha 0.02\nend\n')


def test_condition_parameters_are_read_from_the_raw_ctda():
    """Parameters 1 and 2 sit at bytes 12 and 16 of a CTDA, little-endian."""
    raw = '400000000000b4423a000000e23300010000000000000000'
    assert mm._condition_forms({'Condition[0].Raw': raw}) == {'010033E2', '00000000'}


def test_a_state_written_elsewhere_too_may_be_dropped():
    """PlayerInMorrowind is kept up by another script, so Recall's write is not missed."""
    recall = 'set PlayerInMorrowind to 1\n'
    corpus = _corpus(('mwmorrodefaultquestscript', 'set PlayerInMorrowind to 0\n'),
                     ('fbmwguildscript', 'if PlayerInMorrowind == 1\nendif\n'))
    assert corpus.unobserved(recall, {'mwspellrecallscript'})


def test_a_state_only_the_dropped_script_writes_must_stay():
    """A global other scripts read but only this one writes keeps the script."""
    corpus = _corpus(('mwblightscript', 'if mwPlayerBlightResistance > 50\nendif\n'))
    assert not corpus.unobserved('Let mwPlayerBlightResistance := 30\n', {'resist'})
    corpus = _corpus()
    corpus.forms, corpus.cited = {'mwplayerblightresistance': '01017D90'}, {'01017D90'}
    assert not corpus.unobserved('Let mwPlayerBlightResistance := 30\n', {'resist'})


def test_a_script_that_stages_a_quest_always_stays():
    """Advancing a quest is content, however the quest is read."""
    assert not _corpus().unobserved('SetStage fbmwILGnisisBlight 50\n', set())


def test_a_function_only_dropped_scripts_call_is_dropped_with_them():
    """JDLevitate reads what the Levitate stand-ins write; it goes with them, and its own writes are checked."""
    function = 'begin function {start}\nif JDLevitationData.accFact > 0\nendif\nset gLevitating to 1\nend\n'
    spell = 'set JDLevitationData.accFact to 2\nCall JDLevitate 1 0\n'
    corpus = _corpus(('jdlevitate', function), ('jdlevitationnormalscript', spell))
    corpus.scripts = {'01F8CCCD': ('jdlevitate', function), '01F8CCCE': ('jdlevitationnormalscript', spell)}
    dropped = corpus.with_callees({'jdlevitationnormalscript'})
    assert dropped == {'jdlevitationnormalscript', 'jdlevitate'}
    assert corpus.unobserved(spell, dropped)
    assert corpus.reach([spell], dropped) == [spell, function]
    assert corpus.with_callees(set()) == set()


def test_a_stand_in_without_a_script_is_restored_only_for_a_runtime_effect(monkeypatch):
    """Oblivion effects faking SwiftSwim are restored; one faking Fortify Health keeps Morroblivion's."""
    records = [('SPEL', '0106001E', {'EffectCount': '1', 'Effect[0].EFID': 'WABR'}, 'ob'),
               ('SPEL', '01060001', {'EffectCount': '1', 'Effect[0].EFID': 'FOHE'}, 'ob')]
    monkeypatch.setattr(mm, 'master_records', lambda _ctx, _types: records)
    vanilla = {('SPEL', '0106001E'): 1, ('SPEL', '01060001'): 80}
    lines = {1: ['EffectCount=1', 'Effect[0].MorrowindIndex=1'],
             80: ['EffectCount=1', 'Effect[0].MorrowindIndex=80']}
    found, _folders = mm._candidates(vanilla, None, lambda index, _ctx: lines[index])
    assert list(found) == [('SPEL', '0106001E')]
    assert found[('SPEL', '0106001E')][2] == set()


def test_the_effect_table_lists_each_runtime_effect_and_its_own_clones():
    """Base rows come from MGEF.txt; delivery clones this plugin emitted are added."""
    magic_variants.reset()
    try:
        clone = f'TES4{effect_editor_id(60)}FFSelf'
        magic_variants._parts[0x05000123] = (clone, b'', b'', b'')
        magic_variants._parts[0x04000456] = (clone, b'', b'', b'')
        rows = teleport_lines(['60=Patch.esp|02A345ED|Mark', '75=Patch.esp|02000001|RestoreHealth'],
                              'TR.esm', 5)
    finally:
        magic_variants.reset()
    assert rows == ['Patch.esp|02A345ED=60', 'TR.esm|05000123=60']


def test_the_effect_table_names_each_attribute_variant_and_its_clones():
    """Fortify Luck's variant is 79:7, and so are its delivery and Ability clones; Sanctuary's Ability clone is 42."""
    magic_variants.reset()
    try:
        variant = f'TES4{effect_editor_id(79)}Luck'
        for fid, edid in ((0x05000001, variant), (0x05000002, f'{variant}ConstantSelf'),
                          (0x05000003, f'{variant}ConstantSelfAbility'),
                          (0x05000004, f'TES4{effect_editor_id(42)}Ability')):
            magic_variants._parts[fid] = (edid, b'', b'', b'')
        rows = teleport_lines([], 'Morrowind.esm', 5)
    finally:
        magic_variants.reset()
    assert rows == ['Morrowind.esm|05000001=79:7', 'Morrowind.esm|05000002=79:7',
                    'Morrowind.esm|05000003=79:7', 'Morrowind.esm|05000004=42']


def test_the_effect_table_lists_jump_and_the_acrobatics_skill_effects():
    """Jump is 9; Fortify and Drain Acrobatics and their clones are 83:s20 and 21:s20; Fortify Blade is not listed."""
    magic_variants.reset()
    try:
        fortify = f'TES4{effect_editor_id(83)}Acrobatics'
        for fid, edid in ((0x05000001, fortify), (0x05000002, f'{fortify}FFSelf'),
                          (0x05000003, f'TES4{effect_editor_id(21)}Acrobatics'),
                          (0x05000004, f'TES4{effect_editor_id(83)}Blade')):
            magic_variants._parts[fid] = (edid, b'', b'', b'')
        rows = teleport_lines(['9=Morrowind.esm|05000009|Jump'], 'Morrowind.esm', 5)
    finally:
        magic_variants.reset()
    assert rows == ['Morrowind.esm|05000009=9', 'Morrowind.esm|05000001=83:s20',
                    'Morrowind.esm|05000002=83:s20', 'Morrowind.esm|05000003=21:s20']


def test_tes4_attribute_variants_are_the_same_runtime_effects():
    """Oblivion's Fortify Luck (TES4FOATLuck) and its clones are 79:7, as Morrowind's are."""
    magic_variants.reset()
    try:
        for fid, edid in ((0x00000001, 'TES4FOATLuck'), (0x00000002, 'TES4FOATLuckConstantSelf'),
                          (0x00000003, 'TES4DGATStrength'), (0x00000004, 'TES4FOSKBlade')):
            magic_variants._parts[fid] = (edid, b'', b'', b'')
        rows = copy_rows('Oblivion.esm', 0)
    finally:
        magic_variants.reset()
    assert rows == ['Oblivion.esm|00000001=79:7', 'Oblivion.esm|00000002=79:7',
                    'Oblivion.esm|00000003=22:0']
    assert runtime_attribute_index(-1, 'ABAT', 3) == 85 and runtime_attribute_index(-1, 'FOSK', 14) == -1


def test_attribute_effects_are_runtime_script_effects_per_attribute():
    """Fortify/Drain/Damage/Restore/Absorb Attribute: a Script effect, no actor value, one per attribute."""
    assert mw_archetype(79) == A_SCRIPT and mw_actor_value(79, 0) == AV_NONE
    assert mw_converts(17, 7) and not mw_converts(17, -1)
    assert mw_attribute_variant(85, 3) and not mw_attribute_variant(83, 3)
    assert mw_needs_runtime(79), 'the gap patch restores Morroblivion records carrying them'
