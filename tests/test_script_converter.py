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
