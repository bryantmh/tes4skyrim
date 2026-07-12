"""
Tests for tools/oblivion_to_papyrus.py — TES4 script → Papyrus conversion.
"""

import os
import struct
import tempfile

import pytest
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from script_convert.cross_ref import CrossRefGraph
from script_convert.converter import ScriptConverter
from script_convert.constants import (
    BLOCK_MAP,
    TYPE_MAP,
    ACTOR_VALUE_MAP,
    FUNCTION_MAP,
    PAPYRUS_MAX_SCRIPT_NAME,
    papyrus_script_name,
    _safe_property_name,
)
from script_convert.pipeline import (
    _sanitize_name,
    _pack_wstring,
    build_vmad_quest_fragments,
    build_vmad_info_fragment,
    convert_all_scripts,
)


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def xref():
    """Empty CrossRefGraph for unit tests."""
    return CrossRefGraph()


@pytest.fixture
def xref_with_quests():
    """CrossRefGraph with some quest references."""
    x = CrossRefGraph()
    x.quest_edids = {'mq01', 'daazura', 'tg04mistake'}
    x.formid_to_edid['00012345'] = 'TestQuest'
    x.edid_to_formid['testquest'] = '00012345'
    return x


@pytest.fixture
def converter(xref):
    return ScriptConverter(xref)


@pytest.fixture
def converter_with_quests(xref_with_quests):
    return ScriptConverter(xref_with_quests)


# ===========================================================================
# CrossRefGraph tests
# ===========================================================================

class TestCrossRefGraph:
    def test_empty_graph(self, xref):
        assert len(xref.formid_to_edid) == 0
        assert len(xref.quest_edids) == 0
        assert not xref.is_quest_ref('anything')

    def test_is_quest_ref(self, xref_with_quests):
        assert xref_with_quests.is_quest_ref('MQ01')
        assert xref_with_quests.is_quest_ref('DAAzura')
        assert not xref_with_quests.is_quest_ref('SomeNPC')

    def test_extends_class_quest(self):
        xref = CrossRefGraph()
        xref.script_formid_to_type['1234'] = 1
        assert xref.get_extends_class('1234') == 'Quest'

    def test_extends_class_magic_effect(self):
        xref = CrossRefGraph()
        xref.script_formid_to_type['2345'] = 256
        assert xref.get_extends_class('2345') == 'ActiveMagicEffect'

    def test_extends_class_object(self):
        xref = CrossRefGraph()
        xref.script_formid_to_type['3456'] = 0
        assert xref.get_extends_class('3456') == 'ObjectReference'

    def test_extends_class_actor_attachment(self):
        xref = CrossRefGraph()
        xref.script_formid_to_type['AAAA'] = 0
        xref.record_scri['BBBB'] = 'AAAA'
        xref.record_type['BBBB'] = 'NPC_'
        assert xref.get_extends_class('AAAA') == 'Actor'

    def test_load_from_export(self, tmp_path):
        """Test loading from a minimal export directory."""
        qust_file = tmp_path / 'QUST.txt'
        qust_file.write_text(
            '---RECORD_BEGIN---\n'
            'Signature=QUST\n'
            'FormID=00012345\n'
            'EditorID=TestQuest\n'
            'RecordFlags=0\n'
            '---RECORD_END---\n'
        )
        scpt_file = tmp_path / 'SCPT.txt'
        scpt_file.write_text(
            '---RECORD_BEGIN---\n'
            'Signature=SCPT\n'
            'FormID=00054321\n'
            'EditorID=MyScript\n'
            'SCHR.Type=1\n'
            '---RECORD_END---\n'
        )
        xref = CrossRefGraph()
        xref.load_from_export(str(tmp_path))
        assert xref.formid_to_edid['00012345'] == 'TestQuest'
        assert 'testquest' in xref.quest_edids
        assert xref.script_formid_to_edid['00054321'] == 'MyScript'
        assert xref.script_formid_to_type['00054321'] == 1


# ===========================================================================
# Expression conversion tests
# ===========================================================================

class TestExpressionConversion:
    def test_simple_number(self, converter):
        assert converter._convert_expression('42', 'ObjectReference') == '42'

    def test_simple_variable(self, converter):
        assert converter._convert_expression('myVar', 'ObjectReference') == 'myVar'

    def test_player_substitution(self, converter):
        result = converter._convert_expression('player', 'ObjectReference')
        assert result == 'Game.GetPlayer()'

    def test_getself_substitution(self, converter):
        result = converter._convert_expression('getSelf', 'ObjectReference')
        assert result == 'Self'

    def test_comparison_simple(self, converter):
        result = converter._convert_expression('x == 1', 'ObjectReference')
        assert result == 'x == 1'

    def test_comparison_with_function(self, converter_with_quests):
        result = converter_with_quests._convert_expression(
            'getstage MQ01 == 10', 'Quest')
        assert 'MQ01.GetStage()' in result
        assert '== 10' in result

    def test_logical_or(self, converter_with_quests):
        result = converter_with_quests._convert_expression(
            'getstage MQ01 == 10 || getstage MQ01 == 15', 'Quest')
        assert '||' in result
        assert 'MQ01.GetStage()' in result

    def test_logical_and(self, converter):
        result = converter._convert_expression('x == 1 && y == 2', 'ObjectReference')
        assert '&&' in result

    def test_not_equal(self, converter):
        result = converter._convert_expression('x <> y', 'ObjectReference')
        assert '!=' in result

    def test_isactionref_eq_1(self, converter):
        result = converter._convert_expression('IsActionRef player == 1', 'ObjectReference')
        assert 'akActionRef' in result
        assert 'Game.GetPlayer()' in result
        assert '((' not in result  # No double parens

    def test_isactionref_eq_0(self, converter):
        result = converter._convert_expression('IsActionRef player == 0', 'ObjectReference')
        assert '!' in result or 'not' in result.lower()
        assert 'akActionRef' in result

    def test_getsecondspassed(self, converter):
        result = converter._convert_expression('GetSecondsPassed', 'ObjectReference')
        assert '0.5' in result


# ===========================================================================
# Line conversion tests
# ===========================================================================

class TestLineConversion:
    def test_set_to(self, converter):
        result = converter._convert_line('set myVar to 42', 'ObjectReference')
        assert result == 'myVar = 42'

    def test_set_to_expression(self, converter):
        result = converter._convert_line('set myVar to x', 'ObjectReference')
        assert result == 'myVar = x'

    def test_if_statement(self, converter):
        result = converter._convert_line('if x == 1', 'ObjectReference')
        assert result == 'If x == 1'

    def test_else(self, converter):
        result = converter._convert_line('else', 'ObjectReference')
        assert result == 'Else'

    def test_endif(self, converter):
        result = converter._convert_line('endif', 'ObjectReference')
        assert result == 'EndIf'

    def test_return(self, converter):
        result = converter._convert_line('return', 'ObjectReference')
        assert result == 'Return'

    def test_comment(self, converter):
        result = converter._convert_line('; This is a comment', 'ObjectReference')
        assert result == '; This is a comment'

    def test_empty_line(self, converter):
        result = converter._convert_line('', 'ObjectReference')
        assert result == ''

    def test_variable_declaration(self, converter):
        # Variable declarations are handled at script level (_parse_source),
        # _convert_line skips them (returns empty)
        result = converter._convert_line('short myCount', 'ObjectReference')
        assert result == ''

    def test_float_declaration(self, converter):
        result = converter._convert_line('float timer', 'ObjectReference')
        assert result == ''


# ===========================================================================
# Function conversion tests
# ===========================================================================

class TestFunctionConversion:
    def test_additem(self, converter):
        result = converter._emit_function('player', 'AddItem', 'Gold001 100', 'ObjectReference')
        assert 'Game.GetPlayer()' in result
        assert 'AddItem' in result
        assert 'Gold001' in result

    def test_enable(self, converter):
        result = converter._emit_function('myRef', 'Enable', '', 'ObjectReference')
        assert 'myRef.Enable()' in result

    def test_disable(self, converter):
        result = converter._emit_function(None, 'Disable', '', 'ObjectReference')
        assert 'Disable()' in result

    def test_messagebox(self, converter):
        result = converter._emit_function(None, 'MessageBox', '"Hello World"', 'ObjectReference')
        assert 'Debug.MessageBox' in result
        assert 'Hello World' in result

    def test_getpos_x(self, converter):
        result = converter._emit_function('myRef', 'GetPos', 'X', 'ObjectReference')
        assert 'GetPositionX' in result

    def test_getpos_z(self, converter):
        result = converter._emit_function('myRef', 'GetPos', 'Z', 'ObjectReference')
        assert 'GetPositionZ' in result

    def test_setpos(self, converter):
        result = converter._emit_function('myRef', 'SetPos', 'X 100', 'ObjectReference')
        assert 'SetPosition' in result
        assert '100' in result

    def test_getangle(self, converter):
        result = converter._emit_function('myRef', 'GetAngle', 'Z', 'ObjectReference')
        assert 'GetAngleZ' in result

    def test_setstage(self, converter_with_quests):
        result = converter_with_quests._emit_function(None, 'SetStage', 'MQ01 20', 'Quest')
        assert 'MQ01.SetStage' in result
        assert '20' in result

    def test_getstage(self, converter_with_quests):
        result = converter_with_quests._emit_function(None, 'GetStage', 'MQ01', 'Quest')
        assert 'MQ01.GetStage()' in result

    def test_startquest(self, converter):
        result = converter._emit_function(None, 'StartQuest', 'MyQuest', 'ObjectReference')
        assert 'MyQuest.Start()' in result

    def test_getrandompercent(self, converter):
        result = converter._emit_function(None, 'GetRandomPercent', '', 'ObjectReference')
        assert 'Utility.RandomInt(0, 99)' in result

    def test_kill(self, converter):
        result = converter._emit_function('myActor', 'Kill', '', 'Actor')
        assert 'myActor.Kill()' in result

    def test_getdead(self, converter):
        result = converter._emit_function('myActor', 'GetDead', '', 'Actor')
        assert 'IsDead' in result

    def test_actor_value_function(self, converter):
        result = converter._emit_function(None, 'GetActorValue', 'Blade', 'Actor')
        assert 'GetActorValue' in result
        assert 'OneHanded' in result

    def test_actor_value_alchemy(self, converter):
        result = converter._emit_function(None, 'ModActorValue', 'Alchemy 5', 'Actor')
        assert 'ModActorValue' in result
        assert 'Alchemy' in result

    def test_unknown_function_generates_todo(self, converter):
        result = converter._emit_function(None, 'SomeObscureFunc', 'arg1', 'ObjectReference')
        assert 'TODO' in result

    def test_isactionref(self, converter):
        result = converter._emit_function(None, 'IsActionRef', 'player', 'ObjectReference')
        assert 'akActionRef' in result
        assert 'Game.GetPlayer()' in result

    def test_getactionref(self, converter):
        result = converter._emit_function(None, 'GetActionRef', '', 'ObjectReference')
        assert result == 'akActionRef'

    def test_getself(self, converter):
        result = converter._emit_function(None, 'GetSelf', '', 'ObjectReference')
        assert result == 'Self'


# ===========================================================================
# Actor value mapping tests
# ===========================================================================

class TestActorValueMap:
    def test_blade_to_onehanded(self):
        assert ACTOR_VALUE_MAP['blade'] == 'OneHanded'

    def test_marksman_to_marksman(self):
        assert ACTOR_VALUE_MAP['marksman'] == 'Marksman'

    def test_security_to_lockpicking(self):
        assert ACTOR_VALUE_MAP['security'] == 'Lockpicking'

    def test_fatigue_to_stamina(self):
        assert ACTOR_VALUE_MAP['fatigue'] == 'Stamina'

    def test_mysticism_to_alteration(self):
        assert ACTOR_VALUE_MAP['mysticism'] == 'Alteration'

    def test_resistfire(self):
        assert ACTOR_VALUE_MAP['resistfire'] == 'FireResist'


# ===========================================================================
# Standalone script conversion tests
# ===========================================================================

class TestConvertStandalone:
    def test_simple_script(self, converter):
        source = """ScriptName TestScript

short myVar

Begin OnActivate
  set myVar to 1
  MessageBox "Activated!"
End
"""
        result = converter.convert_standalone('TestScript', source, 'ObjectReference', 'TestScript')
        assert 'ScriptName TES4_TestScript extends ObjectReference' in result
        assert 'Int Property myVar Auto' in result
        assert 'Event OnActivate(ObjectReference akActionRef)' in result
        assert 'myVar = 1' in result
        assert 'Debug.MessageBox' in result
        assert 'EndEvent' in result

    def test_gamemode_to_onupdate(self, converter):
        source = """ScriptName UpdateScript

Begin GameMode
  set x to 1
End
"""
        result = converter.convert_standalone('UpdateScript', source, 'ObjectReference', 'UpdateScript')
        assert 'Event OnUpdate()' in result
        assert 'RegisterForSingleUpdate' in result
        # Object/actor GameMode loops are gated on load state (OnCellAttach start /
        # OnCellDetach stop), NOT auto-started from OnInit — otherwise every
        # scripted object in the game begins ticking the moment the save loads.
        assert 'Event OnCellAttach()' in result
        assert 'Event OnCellDetach()' in result
        assert 'Event OnInit()' not in result
        # The OnUpdate re-registration only continues while still loaded.
        assert 'Is3DLoaded()' in result

    def test_gamemode_quest_still_uses_oninit(self, converter):
        # Quest scripts run globally, so their loop DOES self-start from OnInit.
        source = """ScriptName QUpdateScript

Begin GameMode
  set x to 1
End
"""
        result = converter.convert_standalone('QUpdateScript', source, 'Quest', 'QUpdateScript')
        assert 'Event OnInit()' in result
        assert 'Event OnCellAttach()' not in result

    def test_extends_quest(self, converter):
        source = """ScriptName QuestScript

Begin GameMode
End
"""
        result = converter.convert_standalone('QuestScript', source, 'Quest', 'QuestScript')
        assert 'extends Quest' in result

    def test_multiple_blocks(self, converter):
        source = """ScriptName MultiBlock

Begin OnActivate
  Enable
End

Begin OnDeath
  Disable
End
"""
        result = converter.convert_standalone('MultiBlock', source, 'Actor', 'MultiBlock')
        assert 'OnActivate' in result
        assert 'OnDeath' in result
        assert 'Enable()' in result
        assert 'Disable()' in result

    def test_float_variable(self, converter):
        source = """ScriptName FloatTest

float timer

Begin GameMode
End
"""
        result = converter.convert_standalone('FloatTest', source, 'ObjectReference', 'FloatTest')
        assert 'Float Property timer = 0.0 Auto' in result

    def test_variable_shadowing_a_tes4_command(self, converter):
        """A local whose name collides with a TES4 command must stay a variable.

        DiveRockScript declares `short message`; `if message == 0` was compiled as
        the TES4 `Message` COMMAND (`If Debug.Notification("") == 0`), which does
        not type-check. The declaration renamed it to myMessage (Message is a
        Papyrus type), but the reference kept the original spelling — so the
        original spelling must be recognised as a local too.
        """
        source = """ScriptName DiveRockScript

short message

Begin GameMode
  if message == 0
    set message to 1
  endif
End
"""
        result = converter.convert_standalone(
            'DiveRockScript', source, 'ObjectReference', 'DiveRockScript')
        assert 'Int Property myMessage Auto' in result
        assert 'If myMessage == 0' in result
        assert 'myMessage = 1' in result
        assert 'Debug.Notification' not in result

    def test_menumode_body_is_not_run_in_onupdate(self, converter_with_quests):
        """`begin MenuMode <id>` has no Skyrim equivalent and must NOT execute.

        These bodies used to be merged, unguarded, into the GameMode OnUpdate
        loop — so MQ01Script's MenuMode 1014/1030 blocks ran `setstage MQ01 70/84`
        on the first tick of a new game, blowing the tutorial quest through its
        whole stage machine and into stage 100's `stopquest MQ01`.
        """
        source = """ScriptName MQ01Script

short tutorialOff

Begin GameMode
  set tutorialOff to 0
End

Begin MenuMode 1014
  setstage MQ01 70
End
"""
        result = converter_with_quests.convert_standalone(
            'MQ01Script', source, 'Quest', 'MQ01Script')
        lines = result.split('\n')
        onupdate = lines[lines.index('Event OnUpdate()'):]
        onupdate = onupdate[:onupdate.index('EndEvent')]
        # The MenuMode SetStage must not appear anywhere inside OnUpdate...
        assert not any('SetStage(70)' in ln for ln in onupdate)
        # ...but must survive as a comment so it can be hand-ported.
        assert any(ln.lstrip().startswith(';') and 'SetStage(70)' in ln
                   for ln in lines)


# ===========================================================================
# Fragment conversion tests
# ===========================================================================

class TestConvertFragment:
    def test_simple_fragment(self, converter):
        source = "set myVar to 1\nmessagebox \"Done\""
        result = converter.convert_fragment(source, 'Quest')
        assert any('myVar = 1' in line for line in result)
        assert any('Debug.MessageBox' in line for line in result)

    def test_fragment_strips_scriptname(self, converter):
        source = "ScriptName foo\nset x to 1"
        result = converter.convert_fragment(source, 'Quest')
        assert not any('ScriptName' in line for line in result)

    def test_fragment_local_variables(self, converter):
        source = "short counter\nset counter to 0"
        result = converter.convert_fragment(source, 'Quest')
        assert any('Int counter' in line for line in result)

    def test_fragment_begin_end_stripped(self, converter):
        source = "Begin GameMode\nset x to 1\nEnd"
        result = converter.convert_fragment(source, 'Quest')
        assert not any('Begin' in line for line in result)
        assert not any(line.strip() == 'End' for line in result)


# ===========================================================================
# VMAD binary tests
# ===========================================================================

class TestVMADBuilders:
    def test_pack_wstring(self):
        result = _pack_wstring('Hello')
        assert result == struct.pack('<H', 5) + b'Hello'

    def test_pack_wstring_empty(self):
        result = _pack_wstring('')
        assert result == struct.pack('<H', 0)

    def test_vmad_quest_fragments_header(self):
        result = build_vmad_quest_fragments('TestQuest', [(10, 0), (20, 0)])
        # Check VMAD header
        version, obj_format = struct.unpack_from('<HH', result, 0)
        assert version == 5
        assert obj_format == 2

    def test_vmad_quest_fragments_script_count(self):
        result = build_vmad_quest_fragments('TestQuest', [(10, 0)])
        # After VMAD header (4 bytes), script count
        script_count = struct.unpack_from('<H', result, 4)[0]
        assert script_count == 1

    def test_vmad_quest_parses_to_exactly_its_length(self):
        """A QUST VMAD must end with the alias-script array count (S16).

        Per xEdit's wbVMADFragmentedQUST the QUST VMAD is
        Version, ObjectFormat, Scripts, ScriptFragmentsQuest, **Aliases** —
        and the engine parses it strictly. Omitting the trailing count runs the
        parser off the end of the buffer and it abandons the record's whole
        script/alias binding: every quest alias fills as NONE and every QF
        script property comes back None (journal objective shows, no marker).
        Vanilla ends with exactly these two bytes — Skyrim.esm's
        DBSideContract03 VMAD parses 643/643 only once they are read.

        So parse the whole thing back and require we consume every byte.
        """
        data = build_vmad_quest_fragments(
            'TestQuest', [(10, 0), (20, 1)],
            property_values={'SomeQuest': 0x01035713})
        off = 0

        def take(fmt):
            nonlocal off
            vals = struct.unpack_from(fmt, data, off)
            off += struct.calcsize(fmt)
            return vals

        def wstring():
            nonlocal off
            (length,) = take('<H')
            s = data[off:off + length].decode('latin1')
            off += length
            return s

        version, obj_format, script_count = take('<hhH')
        assert (version, obj_format) == (5, 2)
        for _ in range(script_count):
            wstring()                       # script name
            take('<B')                      # flags
            (prop_count,) = take('<H')
            for _ in range(prop_count):
                wstring()                   # property name
                prop_type, _status = take('<BB')
                assert prop_type == 1, 'object property'
                take('<HhI')                # unused, aliasId, formid

        frag_version, frag_count = take('<bH')
        assert frag_version == 2
        wstring()                           # fragment file name
        for _ in range(frag_count):
            take('<HhiB')
            wstring()                       # script name
            wstring()                       # fragment name

        (alias_count,) = take('<h')
        assert alias_count == 0

        assert off == len(data), (
            f'QUST VMAD must parse to exactly its length; consumed {off} '
            f'of {len(data)} — a truncated tail silently kills alias filling')

    @staticmethod
    def _strict_parse_qust_vmad(data):
        """Parse a QUST VMAD; returns (scripts, frag_count, frag_file) and
        asserts every byte is consumed."""
        off = 0

        def take(fmt):
            nonlocal off
            vals = struct.unpack_from(fmt, data, off)
            off += struct.calcsize(fmt)
            return vals

        def wstring():
            nonlocal off
            (length,) = take('<H')
            s = data[off:off + length].decode('latin1')
            off += length
            return s

        version, obj_format, script_count = take('<hhH')
        assert (version, obj_format) == (5, 2)
        scripts = []
        for _ in range(script_count):
            sname = wstring()
            take('<B')
            (prop_count,) = take('<H')
            props = {}
            for _ in range(prop_count):
                pname = wstring()
                prop_type, _status = take('<BB')
                assert prop_type == 1
                _un, _alias, fid = take('<HhI')
                props[pname] = fid
            scripts.append((sname, props))
        frag_version, frag_count = take('<bH')
        assert frag_version == 2
        frag_file = wstring()
        for _ in range(frag_count):
            take('<HhiB')
            wstring()
            wstring()
        (alias_count,) = take('<h')
        assert alias_count == 0
        assert off == len(data)
        return scripts, frag_count, frag_file

    def test_vmad_quest_attached_script_with_fragments(self):
        """Attached quest script rides alongside the QF fragment script."""
        data = build_vmad_quest_fragments(
            'TestQuest', [(10, 0)], property_values={'SomeRef': 0x01000800},
            attached_script=('TES4_TestQuestScript', {'OtherRef': 0x01000801}))
        scripts, frag_count, frag_file = self._strict_parse_qust_vmad(data)
        assert [s[0] for s in scripts] == ['TES4_QF_TestQuest',
                                          'TES4_TestQuestScript']
        assert scripts[0][1] == {'SomeRef': 0x01000800}
        assert scripts[1][1] == {'OtherRef': 0x01000801}
        assert frag_count == 1
        assert frag_file == 'TES4_QF_TestQuest'

    def test_vmad_quest_attached_script_no_fragments(self):
        """No fragments: only the attached script, and the fragments section
        carries count=0 with an EMPTY file name (vanilla: MS12PostQuest,
        WIThief01 in Skyrim.esm write exactly this shape)."""
        data = build_vmad_quest_fragments(
            'TestQuest', [], attached_script=('TES4_TestQuestScript', {}))
        scripts, frag_count, frag_file = self._strict_parse_qust_vmad(data)
        assert [s[0] for s in scripts] == ['TES4_TestQuestScript']
        assert frag_count == 0
        assert frag_file == ''

    def test_vmad_info_fragment_header(self):
        result = build_vmad_info_fragment('00012345')
        version, obj_format = struct.unpack_from('<HH', result, 0)
        assert version == 5
        assert obj_format == 2

    def test_vmad_info_fragment_no_persistent_scripts(self):
        result = build_vmad_info_fragment('00012345')
        # After header (4), 1 persistent script (holds properties)
        persistent_count = struct.unpack_from('<H', result, 4)[0]
        assert persistent_count == 1

    def test_vmad_info_script_name(self):
        result = build_vmad_info_fragment('AABBCCDD')
        # Script name should contain the FormID
        assert b'TES4_TIF__AABBCCDD' in result


# ===========================================================================
# Utility tests
# ===========================================================================

class TestUtilities:
    def test_sanitize_name_simple(self):
        assert _sanitize_name('TestScript') == 'TestScript'

    def test_sanitize_name_spaces(self):
        assert _sanitize_name('Test Script') == 'Test_Script'

    def test_sanitize_name_special(self):
        assert _sanitize_name('Test-Script!') == 'Test_Script_'


class TestScroRefTyping:
    """_add_scro_ref must key property_refs on the Papyrus-SAFE name.

    Keying on the raw EditorID created a second entry for any EditorID that gets
    renamed — MS14 is a vanilla Skyrim script name, so it becomes myMS14. The
    generic 'Quest' from the SCRO and the specific 'TES4_MS14Script' promoted by
    _convert_ref then lived under different keys, the downgrade guard never fired,
    and the generic type won the declaration: `Quest Property myMS14` with a body
    calling `myMS14.QuestDone` ("field or property QuestDone not found").
    """

    def _xref(self):
        x = CrossRefGraph()
        x.formid_to_edid['00017606'] = 'MS14'
        x.edid_to_formid['ms14'] = '00017606'
        x.record_type['00017606'] = 'QUST'
        x.quest_edids.add('ms14')
        x.record_scri['00017606'] = '0001B94A'
        x.script_formid_to_edid['0001B94A'] = 'MS14Script'
        x.script_formid_to_type['0001B94A'] = 1
        return x

    def test_scro_does_not_shadow_promoted_quest_script_type(self):
        from script_convert.pipeline import _add_scro_ref
        x = self._xref()
        conv = ScriptConverter(x)
        # SCRO preload runs first and seeds the generic base type...
        _add_scro_ref(conv, '00017606', x)
        # ...then the body promotes it to the quest's own script class.
        conv.convert_fragment('set MS14.QuestDone to 1', 'Quest')
        refs = conv.get_property_refs()
        # Exactly one entry, under the safe name, with the specific type.
        assert 'MS14' not in refs
        assert refs['myMS14'] == 'TES4_MS14Script'

    def test_scro_preload_after_promotion_does_not_downgrade(self):
        """_preload_stage_scro_refs runs once per stage; a later stage must not
        reset a type an earlier stage's body already promoted."""
        from script_convert.pipeline import _add_scro_ref
        x = self._xref()
        conv = ScriptConverter(x)
        conv.convert_fragment('set MS14.QuestDone to 1', 'Quest')
        _add_scro_ref(conv, '00017606', x)   # next stage re-seeds the SCRO
        assert conv.get_property_refs()['myMS14'] == 'TES4_MS14Script'


# ===========================================================================
# Type mapping tests
# ===========================================================================

class TestTypeMaps:
    def test_type_map_short(self):
        assert TYPE_MAP['short'] == 'Int'

    def test_type_map_long(self):
        assert TYPE_MAP['long'] == 'Int'

    def test_type_map_float(self):
        assert TYPE_MAP['float'] == 'Float'

    def test_type_map_ref(self):
        assert TYPE_MAP['ref'] == 'ObjectReference'

    def test_block_map_onactivate(self):
        event, end = BLOCK_MAP['onactivate']
        assert 'OnActivate' in event
        assert end == 'EndEvent'

    def test_block_map_gamemode(self):
        event, end = BLOCK_MAP['gamemode']
        assert 'OnUpdate' in event

    def test_block_map_ondeath(self):
        event, end = BLOCK_MAP['ondeath']
        assert 'OnDeath' in event


# ===========================================================================
# Arg parsing tests (comma handling)
# ===========================================================================

class TestArgParsing:
    def test_space_separated(self, converter):
        result = converter._convert_args('Gold001 100', 'additem', 'ObjectReference')
        assert 'Gold001' in result
        assert '100' in result
        assert ', ' in result

    def test_comma_separated(self, converter):
        result = converter._convert_args('DarkBrotherhood, 2', 'setfactionrank', 'ObjectReference')
        assert 'DarkBrotherhood' in result
        assert '2' in result
        # Should have exactly one comma
        assert result.count(',') == 1

    def test_actor_value_arg(self, converter):
        result = converter._convert_args('Blade', 'getactorvalue', 'Actor')
        assert '"OneHanded"' in result

    def test_actor_value_with_amount(self, converter):
        result = converter._convert_args('Health 50', 'setactorvalue', 'Actor')
        assert '"Health"' in result
        assert '50' in result


# ===========================================================================
# Integration test with export data
# ===========================================================================

class TestIntegration:
    def test_convert_all_scripts_with_empty_dir(self, tmp_path):
        export_dir = tmp_path / 'export'
        export_dir.mkdir()
        output_dir = tmp_path / 'output'

        stats = convert_all_scripts(str(export_dir), str(output_dir))
        assert stats['scpt_total'] == 0
        assert stats['info_total'] == 0
        assert stats['qust_total'] == 0
        assert stats['scpt_err'] == 0

    def test_convert_all_scripts_with_scpt(self, tmp_path):
        export_dir = tmp_path / 'export'
        export_dir.mkdir()
        output_dir = tmp_path / 'output'

        (export_dir / 'SCPT.txt').write_text(
            '---RECORD_BEGIN---\n'
            'Signature=SCPT\n'
            'FormID=00001234\n'
            'EditorID=TestScript\n'
            'SCHR.Type=0\n'
            'SCTX=ScriptName TestScript\\nshort myVar\\nBegin OnActivate\\nset myVar to 1\\nEnd\n'
            '---RECORD_END---\n',
            encoding='utf-8'
        )

        stats = convert_all_scripts(str(export_dir), str(output_dir))
        assert stats['scpt_ok'] == 1
        assert stats['scpt_err'] == 0
        assert os.path.exists(os.path.join(str(output_dir), 'TES4_TestScript.psc'))

    def test_convert_all_scripts_with_info(self, tmp_path):
        export_dir = tmp_path / 'export'
        export_dir.mkdir()
        output_dir = tmp_path / 'output'

        (export_dir / 'INFO.txt').write_text(
            '---RECORD_BEGIN---\n'
            'FormID=AABB0001\n'
            'ResultScript=set myVar to 1\n'
            '---RECORD_END---\n',
            encoding='utf-8'
        )

        stats = convert_all_scripts(str(export_dir), str(output_dir))
        assert stats['info_ok'] == 1
        assert os.path.exists(os.path.join(str(output_dir), 'TES4_TIF__AABB0001.psc'))

    def test_convert_all_scripts_report_written(self, tmp_path):
        export_dir = tmp_path / 'export'
        export_dir.mkdir()
        output_dir = tmp_path / 'output'

        convert_all_scripts(str(export_dir), str(output_dir))
        assert os.path.exists(os.path.join(str(output_dir), '_CONVERSION_REPORT.txt'))


# ===========================================================================
# Creation Kit PapyrusCompiler contracts
#
# Each of these was verified against Skyrim's own PapyrusCompiler.exe (see
# docs/script_conversion_plan.md).  A violated contract means the script does
# not compile, produces no .pex, and the record it is bound to silently does
# nothing in-game — so these are regression tests, not style checks.
# ===========================================================================

class TestPapyrusCompilerContracts:

    def test_script_name_never_exceeds_38_chars(self):
        """The CK rejects a ScriptName longer than 38 characters."""
        long_edid = 'TrigZoneCloseCurrentOblivionRdCitadel01SCRIPT'
        name = papyrus_script_name(long_edid)
        assert len(name) <= PAPYRUS_MAX_SCRIPT_NAME
        assert name.startswith('TES4_')

    def test_truncated_script_names_stay_unique(self):
        """Names that differ only past the 38-char cut must not collide."""
        a = papyrus_script_name('TrigZoneCloseCurrentOblivionRdCitadel01SCRIPT')
        b = papyrus_script_name('TrigZoneCloseCurrentOblivionRdCitadel02SCRIPT')
        assert a != b

    def test_short_script_name_is_left_alone(self):
        assert papyrus_script_name('SE38OdditySCRIPT') == 'TES4_SE38OdditySCRIPT'

    def test_script_name_is_deterministic(self):
        """The .psc name, the filename and the VMAD name all call this — they
        must agree, or the script never binds to its record."""
        assert (papyrus_script_name('SETombstoneUshnargraShadborgobSCRIPT')
                == papyrus_script_name('SETombstoneUshnargraShadborgobSCRIPT'))

    def test_temp_prefixed_names_are_renamed(self):
        """PapyrusCompiler reserves the ::temp* register namespace for itself."""
        for name in ('temp', 'tempstage', 'template', 'tempRef'):
            assert not _safe_property_name(name).startswith('temp')

    def test_temp_rename_is_case_sensitive(self):
        """`Temp` and `tmp` compile fine — only a lowercase `temp` prefix clashes."""
        assert _safe_property_name('Temp') == 'Temp'
        assert _safe_property_name('tmp') == 'tmp'
        assert _safe_property_name('atemp') == 'atemp'

    def test_vanilla_script_names_are_reserved(self):
        """A property may not reuse ANY Skyrim script name, not just a type."""
        for name in ('Door', 'DarkBrotherhood', 'MS14'):
            assert _safe_property_name(name) != name

    def test_reserved_rename_preserves_casing(self):
        assert _safe_property_name('DarkBrotherhood') == 'myDarkBrotherhood'

    def test_no_doubled_cast(self, xref):
        """`X as Int as Int` is a parse error."""
        conv = ScriptConverter(xref)
        assert conv._cast('GameDaysPassed.GetValue() as Int', 'Int') == \
            'GameDaysPassed.GetValue() as Int'
        assert conv._cast('someVar', 'Int') == 'someVar as Int'

    def test_quest_script_gamemode_is_gated_on_isrunning(self, xref):
        """TES4 quest-script GameMode only runs while the quest runs; Skyrim
        raises OnInit regardless, and SetStage on a stopped quest STARTS it."""
        src = 'scn QS\n\nshort n\n\nbegin gamemode\n  set n to 1\nend'
        out = ScriptConverter(xref).convert_standalone('QS', src, 'Quest', 'QS')
        assert 'If (!IsRunning())' in out

    def test_object_script_gamemode_is_not_isrunning_gated(self, xref):
        """Only quest scripts get the IsRunning gate; object scripts are gated
        on load state instead."""
        src = 'scn OS\n\nshort n\n\nbegin gamemode\n  set n to 1\nend'
        out = ScriptConverter(xref).convert_standalone('OS', src, 'ObjectReference', 'OS')
        assert 'IsRunning()' not in out

    def test_getisid_uses_getbaseobject_not_actor_cast(self, xref):
        """GetIsID compares against ANY base form — the SE38 oddities are MISC
        items, so `(Self as Actor).GetActorBase()` is an invalid cast."""
        src = 'scn S\n\nbegin onadd\n  if getIsID SomeItem == 1\n    return\n  endif\nend'
        out = ScriptConverter(xref).convert_standalone('S', src, 'ObjectReference', 'S')
        assert 'GetBaseObject()' in out
        assert 'as Actor).GetActorBase()' not in out

    def test_bool_function_compared_to_number_is_cast(self, xref):
        """Papyrus refuses to order a Bool; TES4's GetDetected returns Int 0/1."""
        src = 'scn S\n\nbegin gamemode\n  if SomeRef.getdetected player > 0\n    return\n  endif\nend'
        out = ScriptConverter(xref).convert_standalone('S', src, 'ObjectReference', 'S')
        assert 'as Int) > 0' in out

    def test_magic_effect_event_signatures_match_parent(self):
        """OnEffectStart/Finish signatures are fixed by ActiveMagicEffect.psc."""
        assert BLOCK_MAP['scripteffectstart'][0] == \
            'Event OnEffectStart(Actor akTarget, Actor akCaster)'
        assert BLOCK_MAP['scripteffectfinish'][0] == \
            'Event OnEffectFinish(Actor akTarget, Actor akCaster)'
