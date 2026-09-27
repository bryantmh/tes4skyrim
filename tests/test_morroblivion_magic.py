"""Morroblivion's scripted magic restored to Morrowind's effects, and the table
that tells MorrowindRuntime which effect records it carries.

See: docs/commentary/tes4_export_morrowind.md#restored-magic
"""

from tes4_export import morroblivion_magic as mm
from tes4_export.record_types.morrowind_magic import effect_editor_id
from tes5_import.dialogue.morrowind_teleport import teleport_lines
from tes5_import.record_types import magic_variants
from tes5_import.record_types.magic_morrowind import MW_RUNTIME_EFFECTS, mw_converts

#: The TES4 actor value of Acrobatics, which lands on Skyrim's Stamina.
_ACROBATICS = 26


def _corpus(*texts) -> mm._Corpus:
    """A corpus of `(owning script, source)` texts, with no export read."""
    corpus = object.__new__(mm._Corpus)
    corpus.scripts, corpus.texts, corpus.cited, corpus.forms = {}, list(texts), set(), {}
    corpus._written = {}
    return corpus


def test_an_effect_converts_when_skyrim_or_the_runtime_carries_it():
    """Teleports convert through the runtime; Levitate waits for it; Fortify Health is native."""
    assert all(mw_converts(index, -1) for index in MW_RUNTIME_EFFECTS)
    assert not mw_converts(10, -1)
    assert mw_converts(80, -1)
    assert not mw_converts(83, -1)
    assert mw_converts(83, _ACROBATICS)


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
