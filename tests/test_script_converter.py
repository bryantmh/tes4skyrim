"""
Tests for script_convert/ — TES4 script → Papyrus conversion.
"""

import os
import struct
from pathlib import Path

import pytest
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from script_convert.cross_ref import CrossRefGraph
from script_convert.converter import ScriptConverter
from script_convert.constants import (
    BLOCK_MAP,
    TYPE_MAP,
    ACTOR_VALUE_MAP,
    TES4_ATTRIBUTES,
    PAPYRUS_MAX_SCRIPT_NAME,
    papyrus_script_name,
    _safe_property_name,
    PLAYER_ALIAS_EXTENDS,
)
from script_convert.tes4 import nodes as N
from script_convert.emit import expr as _E
from script_convert.emit import script as _S
from script_convert.tes5.blocks import (
    Kind,
    classify,
    hoist_quest_start_above_writes,
    scan,
)
from script_convert.tes4.lexer import T, tokenize
from script_convert.tes4.parser import (
    Mode,
    is_self_contained,
    parse,
    split_call_args,
    split_param_names,
    split_trailing_comment,
)
from script_convert.objective_completion import (
    _target_closes,
    objective_lines,
    parallel_stages,
    residue_stages,
)
from script_convert.pipeline import (
    _sanitize_name,
    _pack_wstring,
    _superseded_stages,
    build_vmad_quest_fragments,
    build_vmad_info_fragment,
    convert_all_scripts,
)


# ===========================================================================
# Node-path shims
#
# `_convert_line` / `_convert_expression` / `_convert_function_call` were the
# string seams into the converter.  The parser owns that job now, so these
# helpers do what those methods did internally: parse the source, emit the
# node.  Tests keep asserting on the same converted text.
# ===========================================================================

def conv_expr(converter, source, extends='ObjectReference'):
    """Convert one TES4 EXPRESSION, via the parse tree."""
    tree = _parse(f'if {source}\nendif')
    if not tree.body or not hasattr(tree.body[0], 'cond'):
        return source.strip()
    return _E.emit(converter, tree.body[0].cond, extends)


def conv_line(converter, source, extends='ObjectReference'):
    """Convert one TES4 STATEMENT, via the parse tree.

    An empty body means the line emits nothing on its own: a declaration is
    hoisted to `tree.variables`, and a block CLOSER (`endif`, `else`) belongs
    to the walk that owns the block, not to a line.
    """
    tree = _parse(source)
    body = [s for s in tree.body if not isinstance(s, N.Blank)]
    if not body:
        # A lone comment rides on the NEXT statement's `.comment`, so a
        # fragment holding only one parses to an empty body; a declaration
        # goes to `tree.variables` and a block closer belongs to the walk.
        stripped = source.strip()
        return stripped if stripped.startswith(';') else ''
    lines = _S.emit_stmt(converter, body[0], extends, 0)
    return lines[0].strip() if lines else ''


def _parse(source):
    from script_convert.tes4.parser import Mode, parse as _p
    return _p(source, Mode.FRAGMENT)


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

    def test_load_from_export_includes_masters(self, tmp_path):
        """An override plugin resolves EditorIDs owned by its MASTER.

        Translation.esp authors no GLOBs but 430 of its scripts read Nehrim's
        (SetGewitter, VarTrapMine); without the master's records the names are
        emitted as bare identifiers and the compiler rejects them.
        """
        master = tmp_path / 'Master.esm'
        master.mkdir()
        (master / 'GLOB.txt').write_text(
            '---RECORD_BEGIN---\n'
            'Signature=GLOB\n'
            'FormID=00020A0F\n'
            'EditorID=SetGewitter\n'
            'FNAM.Type=s\n'
            'FLTV.Value=0.0\n'
            '---RECORD_END---\n'
        )
        plugin = tmp_path / 'Plugin.esp'
        plugin.mkdir()
        (plugin / '_HEADER.txt').write_text('Master[0]=Master.esm\n')

        xref = CrossRefGraph()
        xref.load_from_export(str(plugin))
        assert xref.edid_to_formid['setgewitter'] == '00020A0F'
        assert xref.record_type['00020A0F'] == 'GLOB'
        assert xref.global_types['setgewitter'] == 's'

    def test_plugin_record_overrides_master(self, tmp_path):
        """Masters are scanned FIRST so the plugin's own version wins."""
        master = tmp_path / 'Master.esm'
        master.mkdir()
        (master / 'GLOB.txt').write_text(
            '---RECORD_BEGIN---\n'
            'Signature=GLOB\n'
            'FormID=00001111\n'
            'EditorID=SharedGlobal\n'
            'FNAM.Type=s\n'
            'FLTV.Value=1.0\n'
            '---RECORD_END---\n'
        )
        plugin = tmp_path / 'Plugin.esp'
        plugin.mkdir()
        (plugin / '_HEADER.txt').write_text('Master[0]=Master.esm\n')
        (plugin / 'GLOB.txt').write_text(
            '---RECORD_BEGIN---\n'
            'Signature=GLOB\n'
            'FormID=00001111\n'
            'EditorID=SharedGlobal\n'
            'FNAM.Type=f\n'
            'FLTV.Value=7.0\n'
            '---RECORD_END---\n'
        )
        xref = CrossRefGraph()
        xref.load_from_export(str(plugin))
        assert xref.global_types['sharedglobal'] == 'f'
        assert xref.global_values['sharedglobal'] == 7.0


# ===========================================================================
# Expression conversion tests
# ===========================================================================

class TestExpressionConversion:
    def test_simple_number(self, converter):
        assert conv_expr(converter, '42', 'ObjectReference') == '42'

    def test_simple_variable(self, converter):
        assert conv_expr(converter, 'myVar', 'ObjectReference') == 'myVar'

    def test_player_substitution(self, converter):
        result = conv_expr(converter, 'player', 'ObjectReference')
        assert result == 'Game.GetPlayer()'

    def test_getself_substitution(self, converter):
        result = conv_expr(converter, 'getSelf', 'ObjectReference')
        assert result == 'Self'

    def test_comparison_simple(self, converter):
        result = conv_expr(converter, 'x == 1', 'ObjectReference')
        assert result == 'x == 1'

    def test_comparison_with_function(self, converter_with_quests):
        result = conv_expr(converter_with_quests, 
            'getstage MQ01 == 10', 'Quest')
        assert 'MQ01.GetStage()' in result
        assert '== 10' in result

    def test_logical_or(self, converter_with_quests):
        result = conv_expr(converter_with_quests, 
            'getstage MQ01 == 10 || getstage MQ01 == 15', 'Quest')
        assert '||' in result
        assert 'MQ01.GetStage()' in result

    def test_logical_and(self, converter):
        result = conv_expr(converter, 'x == 1 && y == 2', 'ObjectReference')
        assert '&&' in result

    def test_not_equal(self, converter):
        result = conv_expr(converter, 'x <> y', 'ObjectReference')
        assert '!=' in result

    def test_isactionref_eq_1(self, converter):
        result = conv_expr(converter, 'IsActionRef player == 1', 'ObjectReference')
        assert 'akActionRef' in result
        assert 'Game.GetPlayer()' in result
        assert '((' not in result  # No double parens

    def test_isactionref_eq_0(self, converter):
        result = conv_expr(converter, 'IsActionRef player == 0', 'ObjectReference')
        assert '!' in result or 'not' in result.lower()
        assert 'akActionRef' in result

    def test_getsecondspassed(self, converter):
        result = conv_expr(converter, 'GetSecondsPassed', 'ObjectReference')
        assert '0.5' in result


# ===========================================================================
# Line conversion tests
# ===========================================================================

class TestLineConversion:
    def test_set_to(self, converter):
        result = conv_line(converter, 'set myVar to 42', 'ObjectReference')
        assert result == 'myVar = 42'

    def test_set_to_expression(self, converter):
        result = conv_line(converter, 'set myVar to x', 'ObjectReference')
        assert result == 'myVar = x'

    def test_if_statement(self, converter):
        result = conv_line(converter, 'if x == 1', 'ObjectReference')
        assert result == 'If x == 1'

    def test_else_belongs_to_the_block_not_the_line(self, converter):
        # A closer is emitted by the walk that owns the `If` (emit/script.py),
        # so on its own it converts to nothing.  The string path had to emit
        # `Else` here and then count keywords afterwards to check the block
        # balanced -- which is what `_balance_if_endif` existed to repair.
        assert conv_line(converter, 'else', 'ObjectReference') == ''

    def test_endif_belongs_to_the_block_not_the_line(self, converter):
        assert conv_line(converter, 'endif', 'ObjectReference') == ''

    def test_block_closers_are_emitted_by_the_walk(self, converter):
        # The pair really is emitted -- by the statement that owns the body.
        out = _S.emit_stmt(
            converter, _parse('if a == 1\nset b to 2\nelse\nset b to 3\nendif')
            .body[0], 'ObjectReference', 0)
        assert [ln.strip() for ln in out] == [
            'If a == 1', 'b = 2', 'Else', 'b = 3', 'EndIf']

    def test_return(self, converter):
        result = conv_line(converter, 'return', 'ObjectReference')
        assert result == 'Return'

    def test_comment(self, converter):
        result = conv_line(converter, '; This is a comment', 'ObjectReference')
        assert result == '; This is a comment'

    def test_empty_line(self, converter):
        result = conv_line(converter, '', 'ObjectReference')
        assert result == ''

    def test_variable_declaration(self, converter):
        # Variable declarations are handled at script level (_parse_source),
        # _convert_line skips them (returns empty)
        result = conv_line(converter, 'short myCount', 'ObjectReference')
        assert result == ''

    def test_float_declaration(self, converter):
        result = conv_line(converter, 'float timer', 'ObjectReference')
        assert result == ''




def convert_args(conv, args_src, func_name, extends):
    """`_convert_args` with `args_src` parsed into argument NODES."""
    from script_convert.tes4.lexer import tokenize
    from script_convert.tes4.parser import Parser
    call = Parser(tokenize('%s %s' % (func_name, args_src))).parse_expression()
    conv._arg_nodes = tuple(getattr(call, 'args', ()) or ())
    return conv._convert_args(args_src, func_name, extends)


def emit_function(conv, ref_name, func_name, args_src, extends):
    """`_emit_function` with `args_src` parsed into argument NODES."""
    from script_convert.tes4.lexer import tokenize
    from script_convert.tes4.parser import Parser
    args = ()
    if args_src.strip():
        call = Parser(tokenize(f'{func_name} {args_src}')).parse_expression()
        args = tuple(getattr(call, 'args', ()) or ())
    return conv._emit_function(ref_name, func_name, extends, args=args)


# ===========================================================================
# Function conversion tests
# ===========================================================================

class TestFunctionConversion:
    def test_additem(self, converter):
        result = emit_function(converter, 'player', 'AddItem', 'Gold001 100', 'ObjectReference')
        assert 'Game.GetPlayer()' in result
        assert 'AddItem' in result
        assert 'Gold001' in result

    def test_enable(self, converter):
        result = emit_function(converter, 'myRef', 'Enable', '', 'ObjectReference')
        assert 'myRef.Enable()' in result

    def test_disable(self, converter):
        result = emit_function(converter, None, 'Disable', '', 'ObjectReference')
        assert 'Disable()' in result

    def test_messagebox(self, converter):
        result = emit_function(converter, None, 'MessageBox', '"Hello World"', 'ObjectReference')
        assert 'Debug.MessageBox' in result
        assert 'Hello World' in result

    def test_getpos_x(self, converter):
        result = emit_function(converter, 'myRef', 'GetPos', 'X', 'ObjectReference')
        assert 'GetPositionX' in result

    def test_getpos_z(self, converter):
        result = emit_function(converter, 'myRef', 'GetPos', 'Z', 'ObjectReference')
        assert 'GetPositionZ' in result

    def test_setpos(self, converter):
        result = emit_function(converter, 'myRef', 'SetPos', 'X 100', 'ObjectReference')
        assert 'SetPosition' in result
        assert '100' in result

    def test_getangle(self, converter):
        result = emit_function(converter, 'myRef', 'GetAngle', 'Z', 'ObjectReference')
        assert 'GetAngleZ' in result

    def test_setstage(self, converter_with_quests):
        result = emit_function(converter_with_quests, None, 'SetStage', 'MQ01 20', 'Quest')
        assert 'MQ01.SetStage' in result
        assert '20' in result

    def test_getstage(self, converter_with_quests):
        result = emit_function(converter_with_quests, None, 'GetStage', 'MQ01', 'Quest')
        assert 'MQ01.GetStage()' in result

    def test_startquest(self, converter):
        result = emit_function(converter, None, 'StartQuest', 'MyQuest', 'ObjectReference')
        assert 'MyQuest.Start()' in result

    def test_getrandompercent(self, converter):
        result = emit_function(converter, None, 'GetRandomPercent', '', 'ObjectReference')
        assert 'Utility.RandomInt(0, 99)' in result

    def test_kill(self, converter):
        result = emit_function(converter, 'myActor', 'Kill', '', 'Actor')
        assert 'myActor.Kill()' in result

    def test_getdead(self, converter):
        result = emit_function(converter, 'myActor', 'GetDead', '', 'Actor')
        assert 'IsDead' in result

    def test_actor_value_function(self, converter):
        result = emit_function(converter, None, 'GetActorValue', 'Blade', 'Actor')
        assert 'GetActorValue' in result
        assert 'OneHanded' in result

    def test_actor_value_alchemy(self, converter):
        result = emit_function(converter, None, 'ModActorValue', 'Alchemy 5', 'Actor')
        assert 'ModActorValue' in result
        assert 'Alchemy' in result

    def test_unknown_function_generates_todo(self, converter):
        result = emit_function(converter, None, 'SomeObscureFunc', 'arg1', 'ObjectReference')
        assert 'TODO' in result

    def test_isactionref(self, converter):
        result = emit_function(converter, None, 'IsActionRef', 'player', 'ObjectReference')
        assert 'akActionRef' in result
        assert 'Game.GetPlayer()' in result

    def test_getactionref(self, converter):
        result = emit_function(converter, None, 'GetActionRef', '', 'ObjectReference')
        assert result == 'akActionRef'

    def test_getself(self, converter):
        result = emit_function(converter, None, 'GetSelf', '', 'ObjectReference')
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

    def test_mysticism_to_illusion(self):
        # Mysticism was folded into Illusion in Skyrim; must agree with the
        # record side (skyrim_overrides.TES4_SKILL_TO_TES5_INDEX maps 24 -> 21).
        assert ACTOR_VALUE_MAP['mysticism'] == 'Illusion'

    def test_resistfire(self):
        assert ACTOR_VALUE_MAP['resistfire'] == 'FireResist'

    def test_attributes_have_no_mapping(self):
        """Skyrim has no attributes -- none may alias onto a live actor value.

        They used to (strength->UnarmedDamage, endurance->HealRate,
        agility/speed->SpeedMult), which broke every Morroblivion guild: the
        Fighters Guild gates each rank on `Player.GetAV Strength >= 30 &&
        Player.GetAV Endurance >= 30` and UnarmedDamage sits near 0, so no
        character could qualify at any level.
        """
        for attr in TES4_ATTRIBUTES:
            assert attr not in ACTOR_VALUE_MAP

    def test_attribute_read_is_stubbed_open(self, converter):
        """A read of a removed attribute yields a value that passes the gate."""
        result = conv_expr(converter, 
            'Player.GetAV Strength >= 30 && Player.GetAV Endurance >= 30',
            'Quest')
        assert result == '100.0 >= 30 && 100.0 >= 30'

    def test_attribute_write_is_dropped(self, converter):
        result = conv_line(converter, 'Player.SetAV Strength 50',
                                                  'Quest')
        assert result.lstrip().startswith(';')
        assert 'SetActorValue' not in result

    def test_skill_read_still_maps(self, converter):
        """Skills survive the attribute no-op -- only attributes are stubbed."""
        result = conv_expr(converter, 'Player.GetAV Armorer >= 10',
                                               'Quest')
        assert result == 'Game.GetPlayer().GetActorValue("Smithing") >= 10'


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
        # Object/actor GameMode loops are gated on load state (OnCellAttach
        # start) so that not every scripted object in the game begins ticking
        # the moment the save loads.
        assert 'Event OnCellAttach()' in result
        # The OnUpdate re-registration only continues while still loaded.
        # Routed through SafeGameModeGate, NOT a bare Is3DLoaded(): that call
        # throws on a reference held in a container (no native object bound)
        # and the throw aborts the event before it can re-arm, killing the
        # poll permanently.  See TES4Polyfill.SafeGameModeGate.
        assert 'If (TES4Polyfill.SafeGameModeGate(Self))' in result
        assert 'If (Is3DLoaded())' not in result
        # NO OnCellDetach unregister: cell-transition events have no
        # guaranteed order, so the old cell's detach could land after
        # OnLoad/OnCellAttach re-armed the poll for the new cell and kill a
        # loaded actor's loop mid-scene (CharacterGen escorts going mute).
        # The Is3DLoaded() arm gate winds the loop down by itself.
        assert 'UnregisterForUpdate()' not in result
        # Arm-first: the re-register must be the FIRST thing OnUpdate does,
        # so a runtime abort anywhere in the body cannot kill the poll.
        body = result.split('Event OnUpdate()', 1)[1].split('EndEvent', 1)[0]
        assert body.index('RegisterForSingleUpdate') < body.index('x = 1')

    def test_gamemode_loop_starts_when_already_loaded(self, converter):
        """OnCellAttach alone is not enough to start a GameMode poll.

        OnCellAttach only fires when a cell BECOMES attached.  A persistent actor
        standing in an already-attached cell when the script is first bound (new
        game, or the player is simply already there) never receives it, so the
        loop would never start and a GameMode-set variable stays 0 forever —
        which is what left Arielle (MG04Restore) standing still: her travel
        package waits on `startconv == 1`, and only her GameMode body sets it.

        The OnInit start MUST stay GATED.  An UNCONDITIONAL OnInit register is
        what once made every scripted object in the game tick at load and
        flooded the engine — that must not come back.

        The gate is currently `Is3DLoaded()` (ScriptConverter._GAMEMODE_GATE).
        See test_self_enable_deadlock_is_a_known_open_regression below for the
        cost of that choice and why the cell-attachment form was reverted.
        """
        source = """ScriptName UpdateScript

Begin GameMode
  set x to 1
End
"""
        result = converter.convert_standalone('UpdateScript', source,
                                              'ObjectReference', 'UpdateScript')
        assert 'Event OnInit()' in result, \
            'a GameMode poll must also start for an already-loaded reference'
        init = result.split('Event OnInit()', 1)[1].split('EndEvent', 1)[0]
        assert 'If (TES4Polyfill.SafeGameModeGate(Self))' in init, \
            'OnInit registration must stay gated (anti-storm)'
        assert 'RegisterForSingleUpdate' in init

    def test_gamemode_gate_is_cell_scoped_not_3d_scoped(self, converter):
        """The poll gate must survive a reference having no 3D.

        Oblivion's GameMode is cell-scoped: an attached cell ticks its refs
        whether or not they are visible.  Two idioms depend on it, and a
        3D-only gate deadlocks both:

        * self-ENABLE — an initially-disabled placement whose own GameMode body
          calls Enable().  No 3D while disabled, so the poll never starts and
          the Enable() that would grant 3D never runs (~200 Nehrim refs,
          Celebro the intro companion among them).
        * self-DISABLE — Nehrim MQ00LichtScript Disable()s itself in state 0,
          then five seconds later runs the plugin's only `SetStage MQ00 2`,
          whose result script holds the only EnablePlayerControls.  Under a 3D
          gate the quest pins at stage 1 and the player never regains control.

        The gate keeps the container guard (GetParentCell() first, so
        Is3DLoaded() is never called on a held item), so this asserts the
        polyfill call is emitted, not a bare Is3DLoaded().
        """
        source = """ScriptName EnableScript

Begin GameMode
  if ( GetStage MQ00 == 5 )
    enable
  endif
End
"""
        result = converter.convert_standalone('EnableScript', source,
                                              'ObjectReference', 'EnableScript')
        init = result.split('Event OnInit()', 1)[1].split('EndEvent', 1)[0]
        assert 'If (TES4Polyfill.SafeGameModeGate(Self))' in init
        assert 'Is3DLoaded()' not in result, \
            'the gate must go through the polyfill, never a bare 3D test'

        polyfill = (Path(__file__).resolve().parents[1] / 'script_convert' /
                    'static_scripts' / 'TES4Polyfill.psc').read_text(
                        encoding='utf-8', errors='replace')
        body = polyfill.split('Bool Function SafeGameModeGate', 1)[1] \
                       .split('EndFunction', 1)[0]
        assert 'IsAttached()' in body, \
            'SafeGameModeGate fell back to a 3D-only test — see the ' \
            'self-disable deadlock (Nehrim MQ00, controls never re-enabled)'

    def test_gamemode_oninit_not_duplicated(self, converter):
        """A script with its own OnInit must not get a second one."""
        source = """ScriptName UpdateScript

Begin OnInit
  set x to 2
End

Begin GameMode
  set x to 1
End
"""
        result = converter.convert_standalone('UpdateScript', source,
                                              'ObjectReference', 'UpdateScript')
        assert result.count('Event OnInit()') == 1

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


class TestLeadingDigitRemoteFields:
    """Remote fields must resolve through Papyrus' leading-digit rename."""

    @staticmethod
    def _xref(fields):
        x = CrossRefGraph()
        x.edid_to_formid['1flightquest'] = '01000003'
        x.formid_to_edid['01000003'] = '1FlightQuest'
        x.record_type['01000003'] = 'QUST'
        x.record_scri['01000003'] = '01000001'
        x.script_formid_to_edid['01000001'] = '1FlightScript'
        x.script_all_vars['1flightscript'] = fields
        return x

    def test_known_field_uses_the_attached_script_class(self):
        conv = ScriptConverter(self._xref({'summoned': 'Int'}))
        out = conv.convert_standalone(
            'ReturnScript',
            ('scn ReturnScript\nbegin gamemode\n'
             '  set FlightQuest.Summoned to 0\nend\n'),
            'Quest', 'ReturnScript')
        assert 'TES4_1FlightScript Property d1FlightQuest Auto' in out
        assert 'd1FlightQuest.Summoned = 0' in out

    def test_missing_field_is_neutralised_in_reads_and_writes(self):
        conv = ScriptConverter(self._xref({}))
        out = conv.convert_standalone(
            'ReturnScript',
            ('scn ReturnScript\nbegin gamemode\n'
             '  if FlightQuest.Summoned == 1\n'
             '    set FlightQuest.Summoned to 0\n'
             '  endif\nend\n'),
            'Quest', 'ReturnScript')
        assert 'If 0 == 1' in out
        assert ';d1FlightQuest.Summoned = 0' in out
        assert out.count('dangling in the original script') == 2


# ===========================================================================
# Stale source names recovered from the SCRO table
# ===========================================================================

class TestScroAliasRecovery:
    """Oblivion runs the COMPILED script, so the SCRO table outranks the text.

    Knights.esp's quest-stage result scripts still read
    `player.additem NDArmorCuirass 1` and `player.additem NDLL0WeaponSword 1`,
    names no record in the plugin carries, while the SCROs those same stages
    ship bind NDArmorHeavyCuirass1 and NDLL0WeaponSwordLvl100.  Unrecovered the
    names reach the compiler undefined, which fails the CHECKER and emits no
    .pex for the WHOLE script — every other stage of the quest dies with it.
    """

    def _xref(self):
        x = CrossRefGraph()
        for fid, edid, rtype in (
                ('01002D3F', 'ND02', 'QUST'),
                ('01002D3E', 'ND03', 'QUST'),
                ('01000ECE', 'NDArmorHeavyCuirass1', 'ARMO'),
                ('01000FCA', 'NDLL0WeaponSwordLvl100', 'LVLI'),
        ):
            x.formid_to_edid[fid] = edid
            x.edid_to_formid[edid.lower()] = fid
            x.record_type[fid] = rtype
        return x

    def test_rename_with_an_inserted_word_is_recovered(self):
        """NDArmorCuirass -> NDArmorHeavyCuirass1 is NOT a prefix relation."""
        from script_convert.pipeline import resolve_scro_aliases
        body = ('; quickstart\nsetstage ND02 0\nsetstage ND02 10\n'
                'setstage ND02 60\nplayer.additem NDArmorCuirass 1\n'
                'setstage ND03 10')
        aliases = resolve_scro_aliases(
            body, ['00000014', '01002D3F', '01000ECE', '01002D3E'], self._xref())
        assert aliases == {'ndarmorcuirass': 'NDArmorHeavyCuirass1'}

    def test_alias_binds_the_property_with_the_records_own_type(self):
        from script_convert.pipeline import resolve_scro_aliases
        x = self._xref()
        conv = ScriptConverter(x)
        body = 'player.additem NDArmorCuirass 1\nsetstage ND03 10'
        conv.set_scro_aliases(resolve_scro_aliases(
            body, ['00000014', '01000ECE', '01002D3E'], x))
        out = '\n'.join(conv.convert_fragment(body, 'Quest'))
        assert 'NDArmorHeavyCuirass1' in out
        assert 'NDArmorCuirass,' not in out
        assert conv.get_property_refs()['NDArmorHeavyCuirass1'] == 'Armor'

    def test_a_live_editorid_is_never_redirected(self):
        """Every name resolves, so there is nothing to recover."""
        from script_convert.pipeline import resolve_scro_aliases
        body = 'setstage ND02 10\nsetstage ND03 10'
        assert resolve_scro_aliases(
            body, ['01002D3F', '01002D3E'], self._xref()) == {}

    def test_ambiguity_is_left_alone(self):
        """Two unspelled SCROs cannot be told apart — bind neither."""
        from script_convert.pipeline import resolve_scro_aliases
        body = 'player.additem NDArmorCuirass 1\nplayer.additem NDMystery 1'
        assert resolve_scro_aliases(
            body, ['00000014', '01000ECE', '01000FCA'], self._xref()) == {}

    def test_an_unnameable_scro_abandons_recovery(self):
        """A master-owned SCRO this export cannot name could be the target."""
        from script_convert.pipeline import resolve_scro_aliases
        body = 'player.additem NDArmorCuirass 1'
        assert resolve_scro_aliases(
            body, ['00000014', '0001BEEF'], self._xref()) == {}

    def test_a_quoted_editorid_counts_as_spelled(self):
        """Oblivion's parser accepts quotes around any EditorID, and the vanilla
        scripts use them.  Stripping the whole literal made TG03Elven's
        `PlaceAtMe "TG03LlathasasBust"` look like a SCRO the body never spells,
        and that stage's `IsXBox` — an OBSE command with no FUNCTION_MAP entry —
        then looked like the rename it paired with, binding a variable to a
        statue."""
        from script_convert.pipeline import resolve_scro_aliases
        x = CrossRefGraph()
        for fid, edid, rtype in (
                ('00008032', 'TG03LlathasasBust', 'STAT'),
                ('00034EA2', 'TG03Elven', 'QUST'),
        ):
            x.formid_to_edid[fid] = edid
            x.edid_to_formid[edid.lower()] = fid
            x.record_type[fid] = rtype
        body = ('BustMarker.PlaceAtMe "TG03LlathasasBust" 1,0,0\n'
                'If IsXBox == 1\n  AddAchievement 25\nEndIf\n'
                'StopQuest TG03Elven')
        assert resolve_scro_aliases(
            body, ['00008032', '00034EA2'], x) == {}


# ===========================================================================
# Zero-argument commands read bare
# ===========================================================================

class TestBareZeroArgCommands:

    def test_getcurrentweatherpercent_reaches_the_real_handler(self):
        """Takes no arguments, so it is ALWAYS read bare.  Unrouted it survived
        as an undefined identifier; the stubbed spelling returned a constant 0,
        which made every `< 0.1` transition test permanently true."""
        conv = ScriptConverter(CrossRefGraph())
        out = '\n'.join(conv.convert_fragment(
            'if getCurrentWeatherPercent < .1\n  return\nendif', 'Quest'))
        assert 'Weather.GetCurrentWeatherTransition()' in out
        assert 'getCurrentWeatherPercent' not in out

    def test_getweatherpercent_is_no_longer_stubbed_to_zero(self):
        conv = ScriptConverter(CrossRefGraph())
        out = '\n'.join(conv.convert_fragment(
            'if getWeatherPercent < .1\n  return\nendif', 'Quest'))
        assert 'Weather.GetCurrentWeatherTransition()' in out

    def test_isplayerslastriddenhorse_alias_is_neutralised(self):
        """The other authored spelling of GetPlayerHasLastRiddenHorse (0x1153).
        Skyrim tracks no last-ridden horse, so it neutralises to 0 — but it must
        be ROUTED, or the name survives undefined and kills the script."""
        conv = ScriptConverter(CrossRefGraph())
        out = '\n'.join(conv.convert_fragment(
            'if HorseRef.IsPlayersLastRiddenHorse == 0\n  return\nendif',
            'Quest'))
        assert 'IsPlayersLastRiddenHorse ==' not in out
        assert ';NE:' in out


# ===========================================================================
# Raw FormIDs in form-argument positions
# ===========================================================================

class TestRawFormIdOperands:

    def test_getisid_resolves_a_short_raw_formid(self):
        """`GetIsID 7` names the Player NPC_ at 0x00000007.  A number in a FORM
        slot is never a literal, so the 6-digit floor the bare-identifier path
        uses must not apply — left a literal, the comparison became
        `Form == Int` and the checker rejected the whole script."""
        x = CrossRefGraph()
        x.formid_to_edid['00000007'] = 'Player'
        x.edid_to_formid['player'] = '00000007'
        x.record_type['00000007'] = 'NPC_'
        conv = ScriptConverter(x)
        out = '\n'.join(conv.convert_fragment(
            'if ( GetIsID 7 == 0 )\n  return\nendif', 'ActiveMagicEffect'))
        assert 'GetBaseObject() == Player' in out
        assert '== 7' not in out
        # No `d7` artifact from naming a property after the digit.
        assert 'd7' not in conv.get_property_refs()


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
        result = convert_args(converter, 'Gold001 100', 'additem', 'ObjectReference')
        assert 'Gold001' in result
        assert '100' in result
        assert ', ' in result

    def test_comma_separated(self, converter):
        result = convert_args(converter, 'DarkBrotherhood, 2', 'setfactionrank', 'ObjectReference')
        assert 'DarkBrotherhood' in result
        assert '2' in result
        # Should have exactly one comma
        assert result.count(',') == 1

    def test_actor_value_arg(self, converter):
        result = convert_args(converter, 'Blade', 'getactorvalue', 'Actor')
        assert '"OneHanded"' in result

    def test_actor_value_with_amount(self, converter):
        result = convert_args(converter, 'Health 50', 'setactorvalue', 'Actor')
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


# ===========================================================================
# Creation Kit PapyrusCompiler contracts
#
# Each of these was verified against Skyrim's own PapyrusCompiler.exe (see
# docs/commentary/script_convert.md).  A violated contract means the script does
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


# ===========================================================================
# Quest objective completion (TES4 log -> TES5 objective states)
# ===========================================================================

def _stage_gate(op: int, value: float, or_next: bool = False) -> str:
    """A raw TES4 CTDA hex for `GetStage <op> value` on the quest itself.

    Byte layout matches the real records (verified against FGC01Rats): type byte
    (op in the top 3 bits, 0x01 = OR with next), comparison float at +4,
    function index at +8 (58 = GetStage).
    """
    raw = (bytes([op | (0x01 if or_next else 0x00), 0, 0, 0])
           + struct.pack('<f', float(value))
           + struct.pack('<H', 58) + b'\x00' * 2
           + b'\x13\x57\x03\x00' + b'\x00' * 8)
    return raw.hex()


_EQ = 0x00
_GE = 0x60
_LE = 0xA0


def _stage_done_gate(stage, op=_EQ, value=1):
    """A CTDA hex for GetStageDone(quest, stage) - func 59, stage is param 2."""
    raw = (bytes([op, 0, 0, 0])
           + struct.pack('<f', float(value))
           + struct.pack('<H', 59) + b'\x00' * 2
           + b'\x13\x57\x03\x00' + struct.pack('<I', int(stage))
           + b'\x00' * 4)
    return raw.hex()


def _frags(*stages):
    """fragments tuples as _convert_qust_scripts builds them."""
    return [(s, 0, f'Log text {s}.', '', False, i, 0)
            for i, s in enumerate(stages)]


class TestQuestObjectiveCompletion:
    """Oblivion's journal is an append-only log with no notion of a completed
    objective; Skyrim's is a set of independently-stated objectives.  The
    completion points are recovered from the TES4 quest-target stage gates.
    """

    def test_objective_completes_when_its_own_step_ends(self):
        """The core bug: walking stages must tick off the steps left behind.

        One target live only at stage 10 and another only at 20 => reaching 20
        finishes step 10.
        """
        rec = {
            'Target[0].FormID': '0000BC69',
            'Target[0].Condition[0].Raw': _stage_gate(_EQ, 10),
            'Target[1].FormID': '0000BC72',
            'Target[1].Condition[0].Raw': _stage_gate(_EQ, 20),
        }
        sup = _superseded_stages(rec, _frags(10, 20))
        assert sup[(20, 0)] == [10], "stage 20 must complete objective 10"
        assert sup[(10, 0)] == [], "nothing precedes stage 10"

    def test_parallel_objectives_stay_open(self):
        """A target live across 40..50 keeps BOTH objectives open — a quest can
        have several objectives outstanding at once, so 50 must NOT close 40."""
        rec = {
            'Target[0].FormID': '0000BC72',
            'Target[0].Condition[0].Raw': _stage_gate(_GE, 40),
            'Target[0].Condition[1].Raw': _stage_gate(_LE, 50),
            'Target[1].FormID': '0000BC69',
            'Target[1].Condition[0].Raw': _stage_gate(_EQ, 55),
        }
        sup = _superseded_stages(rec, _frags(40, 50, 55))
        assert sup[(50, 0)] == [], \
            "stage 50 shares 40's live marker — 40 is still in progress"
        assert sup[(55, 0)] == [40, 50], \
            "both parallel objectives close together when the marker goes dark"

    def test_not_a_blanket_sweep_of_lower_indices(self):
        """Regression: completing every lower-numbered objective would tick a
        still-live parallel step.  Only the finished step may be completed."""
        rec = {
            'Target[0].FormID': '0000BC72',
            'Target[0].Condition[0].Raw': _stage_gate(_GE, 10),
            'Target[0].Condition[1].Raw': _stage_gate(_LE, 90),  # live throughout
            'Target[1].FormID': '0000BC69',
            'Target[1].Condition[0].Raw': _stage_gate(_EQ, 20),
        }
        sup = _superseded_stages(rec, _frags(10, 20, 90))
        assert 10 not in sup[(20, 0)], \
            "objective 10's marker is still live at 20 — it must stay open"
        assert 10 not in sup[(90, 0)] or sup[(90, 0)] == [20], \
            "only genuinely-finished steps close"

    def test_objective_completed_exactly_once(self):
        """An objective is closed by the FIRST stage that ends it, not re-closed
        by every later stage."""
        rec = {
            'Target[0].FormID': '0000BC69',
            'Target[0].Condition[0].Raw': _stage_gate(_EQ, 10),
            'Target[1].FormID': '0000BC72',
            'Target[1].Condition[0].Raw': _stage_gate(_EQ, 20),
            'Target[2].FormID': '0000BC73',
            'Target[2].Condition[0].Raw': _stage_gate(_EQ, 30),
        }
        sup = _superseded_stages(rec, _frags(10, 20, 30))
        closes = [s for done in sup.values() for s in done]
        assert closes.count(10) == 1, "objective 10 must be completed once"
        assert sup[(30, 0)] == [20]

    def test_no_targets_falls_back_to_linear_log(self):
        """A quest with no QSTA gates has nothing to read, so each entry is
        closed when the log moves on — Oblivion's linear default."""
        sup = _superseded_stages({}, _frags(10, 20, 30))
        assert sup[(20, 0)] == [10]
        assert sup[(30, 0)] == [20]

    def test_unbounded_target_never_blocks_completion(self):
        """MS48's shape: a target gated `GetStage >= 50` stays live to the end
        of the quest, so its liveness says nothing about whether a step ended.
        Reading it as still-in-progress stranded objectives 50..90 forever."""
        rec = {
            'Target[0].FormID': '00028A7A',
            'Target[0].Condition[0].Raw': _stage_gate(_GE, 50),
        }
        frags = _frags(50, 60, 70)
        assert residue_stages(rec, frags) == [], \
            "an open-ended gate must not leave objectives unresolvable"
        sup = _superseded_stages(rec, frags)
        assert sup[(60, 0)] == [50]
        assert sup[(70, 0)] == [60]

    def test_getstagedone_target_counts_as_closing(self):
        """fbmwBMStones' six ritual targets are gated GetStageDone, not a stage
        window. They DO close - just order-independently - so they must stay in
        the liveness test; dropping them re-ordered the rituals sequentially."""
        assert _target_closes([_stage_done_gate(60)]), \
            "a GetStageDone gate has a closing edge"
        assert not _target_closes([_stage_gate(_GE, 50)]), \
            "an open-ended GetStage gate has none"
        assert _target_closes([_stage_gate(_LE, 90)])

    def test_terminal_stage_is_never_superseded(self):
        """SE44: stage 200 ends the quest one way, 201 the other. TES4 has no
        fail bit, so both carry QSDT 0x01 - mutually exclusive endings that must
        never close each other."""
        rec = {
            'Stage[0].Index': 200, 'Stage[0].LogCount': 1,
            'Stage[0].Log[0].Flags': 0x01,
            'Stage[1].Index': 201, 'Stage[1].LogCount': 1,
            'Stage[1].Log[0].Flags': 0x01,
        }
        frags = [(200, 0, 'Rewarded.', '', True, 0, 0),
                 (201, 0, 'He is dead.', '', True, 1, 0)]
        sup = _superseded_stages(rec, frags)
        assert sup[(201, 0)] == [], \
            "a quest-ending stage must not be closed by the other ending"

    def test_residue_objectives_are_swept_at_runtime(self):
        """An objective no static rule can finish is closed only if the player
        actually saw it - a branch never taken was never Displayed."""
        lines = objective_lines({}, [8, 50], 60, 0)
        assert '  If IsObjectiveDisplayed(8) && !IsObjectiveCompleted(8)' in lines
        assert '    SetObjectiveCompleted(8, true)' in lines
        assert '  SetObjectiveDisplayed(60, true)' in lines
        assert not any('IsObjectiveDisplayed(60)' in x for x in lines), \
            "a stage never sweeps itself or anything later"

    def test_parallel_quests_are_exempt_from_the_sweep(self):
        """MQ11's six city gates are closable in any order, so an earlier one is
        still a live task when a later one is displayed."""
        assert 40 in parallel_stages('MQ11')
        assert 45 in parallel_stages('mq11'), "lookup is case-insensitive"
        assert parallel_stages('MS48') == frozenset(), \
            "a quest absent from the table is swept normally"


# ===========================================================================
# TES4-only functions made functional (pme/sme, IsSpellTarget, OnAlarm, ...)
# ===========================================================================

@pytest.fixture
def xref_magic():
    """CrossRefGraph stocked with MGEF/EFSH/SPEL/PACK records the new
    handlers resolve through."""
    x = CrossRefGraph()
    # EFSH records (converted, so bindable as EffectShader properties)
    for fid, edid in [('0014A0A2', 'effectSoulTrap'),
                      ('0018B576', 'effectEnchantConjuration'),
                      ('0018B57B', 'effectEnchantMysticism')]:
        x.formid_to_edid[fid] = edid
        x.edid_to_formid[edid.lower()] = fid
        x.record_type[fid] = 'EFSH'
    # MGEF codes: STRP has its own shader; DSPL only the enchant shader;
    # BABO (bound boots) falls back to its school's (conjuration) glow.
    x.mgef_shaders['strp'] = ('0014A0A2', '0018B57B', 4)
    x.mgef_shaders['dspl'] = ('00000000', '0018B57B', 4)
    x.mgef_shaders['babo'] = ('00000000', '00000000', 1)
    # Spells: first effect DRHE -> AlchDamageHealth; pure-SEFF spell -> filler
    x.spell_effects['testdrainspell'] = [('SEFF', 69), ('DRHE', 8)]
    x.spell_effects['testscriptspell'] = [('SEFF', 69)]
    # A PACK record for GetIsCurrentPackage/GetCurrentAIPackage
    x.formid_to_edid['00023456'] = 'TestWanderPkg'
    x.edid_to_formid['testwanderpkg'] = '00023456'
    x.record_type['00023456'] = 'PACK'
    return x


class TestMagicEffectVisuals:
    def test_pme_own_shader(self, xref_magic):
        conv = ScriptConverter(xref_magic)
        result = conv_line(conv, 'pme STRP', 'ObjectReference')
        assert 'effectSoulTrap.Play(Self, -1.0)' in result
        assert conv._property_refs['effectSoulTrap'] == 'EffectShader'

    def test_pme_duration_and_ref(self, xref_magic):
        conv = ScriptConverter(xref_magic)
        conv._property_refs['SomeRef'] = 'ObjectReference'
        result = conv_line(conv, 'SomeRef.pme STRP 5', 'ObjectReference')
        assert 'effectSoulTrap.Play(SomeRef, 5)' in result

    def test_pme_enchant_fallback(self, xref_magic):
        conv = ScriptConverter(xref_magic)
        result = conv_line(conv, 'pme DSPL', 'ObjectReference')
        assert 'effectEnchantMysticism.Play(Self, -1.0)' in result

    def test_pme_school_fallback(self, xref_magic):
        conv = ScriptConverter(xref_magic)
        result = conv_line(conv, 'pme BABO', 'ObjectReference')
        assert 'effectEnchantConjuration.Play(Self, -1.0)' in result

    def test_sme_stops(self, xref_magic):
        conv = ScriptConverter(xref_magic)
        result = conv_line(conv, 'sme STRP', 'ObjectReference')
        assert 'effectSoulTrap.Stop(Self)' in result

    def test_pme_unknown_code_is_ne_not_todo(self, xref_magic):
        conv = ScriptConverter(xref_magic)
        result = conv_line(conv, 'pme XXXX', 'ObjectReference')
        assert ';TODO' not in result


class TestIsSpellTarget:
    def test_resolves_first_surviving_effect(self, xref_magic):
        # SEFF drops, DRHE -> AlchDamageHealth 0x0003EB42
        assert xref_magic.get_spell_first_skyrim_mgef('TestDrainSpell') == 0x0003EB42

    def test_pure_script_spell_uses_filler(self, xref_magic):
        # matches the importer's first filler (AlchRestoreHealth)
        assert xref_magic.get_spell_first_skyrim_mgef('TestScriptSpell') == 0x0003EB15

    def test_emits_polyfill_call(self, xref_magic):
        conv = ScriptConverter(xref_magic)
        result = conv_line(conv, 'if player.IsSpellTarget TestDrainSpell',
                                    'ObjectReference')
        assert 'TES4Polyfill.HasMagicEffectByID(Game.GetPlayer(), 0x0003EB42)' in result
        assert ';TODO' not in result


class TestAnimAndPackage:
    def test_isanimplaying_bare(self, converter):
        result = conv_line(converter, 'if isAnimPlaying == 0', 'ObjectReference')
        assert 'GetAnimationVariableBool("bAnimPlaying")' in result
        assert ';TODO' not in result

    def test_isanimplaying_on_ref(self, converter):
        converter._property_refs['DoorRef'] = 'ObjectReference'
        result = conv_line(converter, 'if DoorRef.IsAnimPlaying == 0',
                                         'ObjectReference')
        assert 'DoorRef.GetAnimationVariableBool("bAnimPlaying")' in result

    def test_getiscurrentpackage(self, xref_magic):
        conv = ScriptConverter(xref_magic)
        result = conv_line(conv, 'if GetIsCurrentPackage TestWanderPkg',
                                    'Actor')
        assert 'GetCurrentPackage() == TestWanderPkg' in result
        assert conv._property_refs['TestWanderPkg'] == 'Package'

    def test_getcurrentaipackage_vs_form(self, xref_magic):
        conv = ScriptConverter(xref_magic)
        result = conv_line(conv, 'if ( GetCurrentAIPackage == TestWanderPkg )',
                                    'Actor')
        assert 'GetCurrentPackage() == TestWanderPkg' in result

    def test_getcurrentaipackage_vs_number_stays_neutral(self, xref_magic):
        conv = ScriptConverter(xref_magic)
        result = conv_line(conv, 'if ( GetCurrentAIPackage != 5 )', 'Actor')
        assert 'GetCurrentPackage()' not in result


class TestOnAlarmBlock:
    def test_onalarm_becomes_combat_state_guard(self, converter):
        source = ('scriptname TestAlarm\n'
                  'begin onAlarm 4, player\n'
                  '  set doOnce to 1\n'
                  'end\n')
        result = converter.convert_standalone('TestAlarm', source, 'Actor',
                                              'TestAlarm')
        text = result if isinstance(result, str) else '\n'.join(result)
        assert 'Event OnCombatStateChanged(Actor akTarget, int aeCombatState)' in text
        assert 'If aeCombatState != 0' in text
        assert 'No Papyrus equivalent for OnAlarm' not in text

    def test_onstartcombat_gets_state_guard(self, converter):
        source = ('scriptname TestSC\n'
                  'begin onStartCombat\n'
                  '  set doOnce to 1\n'
                  'end\n')
        result = converter.convert_standalone('TestSC', source, 'Actor', 'TestSC')
        text = result if isinstance(result, str) else '\n'.join(result)
        assert 'If aeCombatState == 1' in text


class TestSetAlert:
    """SetAlert maps to Skyrim's native Actor.SetAlert, NOT DrawWeapon.

    Oblivion's SetAlert sets the AI combat-readiness flag; it does not block
    dialogue. Mapping `SetAlert 1` to DrawWeapon() while `SetAlert 0` was a
    no-op left CharacterGen's Uriel permanently weapon-drawn after the prison
    ambush, so he could never initiate the conversation with the player and
    the intro soft-locked with controls disabled.
    """

    def test_setalert_1_alerts_not_draws(self, converter):
        result = conv_line(converter, 'UrielSeptimRef.setalert 1', 'Quest')
        assert 'UrielSeptimRef.SetAlert(true)' in result
        assert 'DrawWeapon' not in result

    def test_setalert_0_stands_down(self, converter):
        result = conv_line(converter, 'UrielSeptimRef.setalert 0', 'Quest')
        assert 'UrielSeptimRef.SetAlert(false)' in result

    def test_setalert_bare_ref_casts_to_actor(self, converter):
        result = conv_line(converter, 'setalert 1', 'Quest')
        assert '(Self as Actor).SetAlert(true)' in result


class TestSingletonFixes:
    def test_getiscreature_polyfill(self, converter):
        result = conv_line(converter, 'if GetIsCreature == 0', 'ActiveMagicEffect')
        assert 'TES4Polyfill.GetIsCreature(GetTargetActor())' in result

    def test_isguard_polyfill(self, converter):
        result = conv_line(converter, 'if IsGuard == 0', 'ActiveMagicEffect')
        assert 'TES4Polyfill.IsGuard(GetTargetActor())' in result

    def test_hasvampirefed_polyfill(self, converter):
        result = conv_line(converter, 'if player.HasVampireFed == 1',
                                         'ObjectReference')
        assert 'TES4Polyfill.HasVampireFed()' in result

    def test_setfactionreaction_mixed_separators(self, converter):
        # Must NOT emit SetReaction: that writes the XNAM 'Modifier' field,
        # which Skyrim ignores (1,035 of 1,036 vanilla relations store 0).
        # Combat is gated on the Group Combat Reaction enum, written by
        # SetAlly/SetEnemy.
        result = conv_line(converter, 
            'setfactionreaction FacA, FacB 20', 'ObjectReference')
        assert 'FacA.SetAlly(FacB, true, true)' in result
        assert 'SetReaction' not in result
        assert ';TODO' not in result

    def test_setfactionreaction_negative_makes_enemy(self, converter):
        """A negative flip writes the Group Combat Reaction enum and nothing
        else — combat initiation is the ENGINE's job once the packages
        authorise combat behaviour (pack_converter.DEFAULT_INTERRUPT).  The
        FactionWar member-pairing push was removed: it sampled actors
        probabilistically and paired them with relationship ranks that
        silently no-op between non-unique actors."""
        result = conv_line(converter, 
            'setfactionreaction FacA FacB -100', 'ObjectReference')
        assert 'FacA.SetEnemy(FacB, false, false)' in result
        assert 'FactionWar' not in result

    def test_setfactionreaction_strong_positive_becalms(self, converter):
        result = conv_line(converter, 
            'setfactionreaction FacA FacB 100', 'ObjectReference')
        assert 'FacA.SetAlly(FacB, true, true)' in result
        assert 'FactionPeace' not in result

    def test_playerfaction_flip_is_mirrored_to_vanilla(self, converter):
        """The runtime player is never a member of the CONVERTED PlayerFaction
        (membership lives on Skyrim's own Player NPC), so a flip against it
        must also land on vanilla PlayerFaction to reach the actual player.
        Mode is an int: 1 enemy, 0 neutral, 2 friend — the neutral clear must
        mirror too (CharacterGen stage 23 stands the assassins down from
        hunting the player with `setfactionreaction MythicDawnCG
        PlayerFaction 0`)."""
        result = conv_line(converter, 
            'setfactionreaction FacA PlayerFaction -100', 'ObjectReference')
        assert 'TES4Polyfill.MirrorPlayerFactionRelation(FacA, 1)' in result
        result = conv_line(converter, 
            'setfactionreaction FacA PlayerFaction 0', 'ObjectReference')
        assert 'TES4Polyfill.MirrorPlayerFactionRelation(FacA, 0)' in result
        result = conv_line(converter, 
            'setfactionreaction FacA PlayerFaction 100', 'ObjectReference')
        assert 'TES4Polyfill.MirrorPlayerFactionRelation(FacA, 2)' in result

    def test_setfactionreaction_variable_amount_branches(self, converter):
        """A non-literal amount still has to reach a real enum tier."""
        result = conv_line(converter, 
            'setfactionreaction FacA FacB someVar', 'ObjectReference')
        assert 'SetEnemy' in result and 'SetAlly' in result
        assert 'SetReaction' not in result

    def test_pushactoraway(self, converter):
        converter._property_refs['MarkerRef'] = 'ObjectReference'
        converter._property_refs['VictimRef'] = 'ObjectReference'
        result = conv_line(converter, 'MarkerRef.pushActorAway VictimRef 30',
                                         'ObjectReference')
        assert 'MarkerRef.PushActorAway((VictimRef as Actor), 30)' in result
        assert ';TODO' not in result

    def test_getarmorrating(self, converter):
        converter._property_refs['GuardRef'] = 'Actor'
        result = conv_line(converter, 'if GuardRef.GetArmorRating > 20',
                                         'ObjectReference')
        assert 'GuardRef.GetActorValue("DamageResist")' in result

    def test_if_without_space_is_condition(self, converter):
        result = conv_line(converter, 'if((myVar == 1))', 'ObjectReference')
        assert result.lstrip().lower().startswith('if')
        assert ';TODO' not in result

    def test_setactorrefraction(self, converter):
        result = conv_line(converter, 'SetActorRefraction 1', 'Actor')
        assert 'TES4Polyfill.SetActorRefraction(Self, 1)' in result


# ===========================================================================
# 2026-07-19 quest-bug sweep regressions (MG04 sleep / Say timers / guards)
# ===========================================================================

class TestMenuModeSleepConversion:
    SRC = '''Scriptname TestSleep

short sleepcheck
short time

begin gamemode
if ( sleepcheck > 0 )
	SetStage MG04Restore 40
endif
end

Begin menumode
if ( isPCSleeping == 1 )
	set sleepcheck to 1
endif
End

Begin MenuMode 1014
set time to 99
End
'''

    def test_sleep_menumode_becomes_sleep_events(self, converter):
        result = converter.convert_standalone('TestSleep', self.SRC, 'Quest',
                                              'TestSleep')
        assert 'Event OnSleepStart(float afSleepStartTime, float afDesiredSleepEndTime)' in result
        assert 'Event OnSleepStop(bool abInterrupted)' in result
        assert 'Function TES4_MenuModeSleepBody()' in result
        # isPCSleeping inside the sleep body reads the managed flag
        assert 'If (TES4_PCSleeping == 1)' in result
        assert 'RegisterForSleep()' in result
        # body is executable (not commented out)
        assert ';  If (TES4_PCSleeping' not in result

    def test_menu_id_block_stays_commented(self, converter):
        result = converter.convert_standalone('TestSleep', self.SRC, 'Quest',
                                              'TestSleep')
        assert 'begin MenuMode 1014' in result
        assert ';  time = 99' in result

    def test_non_sleep_bare_menumode_stays_commented(self, converter):
        src = ('Scriptname TestNoSleep\n\nshort x\n\n'
               'begin gamemode\nset x to 1\nend\n\n'
               'Begin menumode\nset x to 2\nEnd\n')
        result = converter.convert_standalone('TestNoSleep', src, 'Quest',
                                              'TestNoSleep')
        assert 'OnSleepStart' not in result
        assert 'RegisterForSleep' not in result


class TestInfoFragmentVmadLayout:
    """Every INFO VMAD declares BOTH fragments: Fragment_1 (OnBegin) and
    Fragment_0 (OnEnd) -- the line hooks TES4Polyfill.SayLine relies on.

    Fragment entries are POSITIONAL: the engine binds the Nth entry to the
    Nth set flag bit, so the Begin entry must be written FIRST.  Verified
    against Skyrim.esm, where the 250 both-fragment INFOs use every naming
    order -- only position is load-bearing.
    """

    @staticmethod
    def parse(d):
        import struct
        pos = 4
        n = struct.unpack_from('<H', d, pos)[0]
        pos += 2
        for _ in range(n):
            ln = struct.unpack_from('<H', d, pos)[0]
            pos += 2 + ln + 1
            pc = struct.unpack_from('<H', d, pos)[0]
            pos += 2
            for _ in range(pc):
                pl = struct.unpack_from('<H', d, pos)[0]
                pos += 2 + pl
                t = d[pos]
                pos += 2
                pos += 8 if t == 1 else 4
        pos += 1                      # extra bind data version
        flags = d[pos]
        pos += 1
        ln = struct.unpack_from('<H', d, pos)[0]
        pos += 2 + ln
        frags = []
        for _ in range(bin(flags).count('1')):
            pos += 1
            ln = struct.unpack_from('<H', d, pos)[0]
            pos += 2 + ln
            ln = struct.unpack_from('<H', d, pos)[0]
            frags.append(d[pos + 2:pos + 2 + ln].decode())
            pos += 2 + ln
        return flags, frags, pos, len(d)

    def test_vmad_sets_both_bits_and_writes_begin_entry_first(self):
        flags, frags, used, total = self.parse(
            build_vmad_info_fragment('00032B0B', {'CharacterGen': 0x0102466E}))
        assert flags == 0x03, 'bit0 (OnBegin) and bit1 (OnEnd) must both be set'
        assert frags == ['Fragment_1', 'Fragment_0'], \
            'the OnBegin entry must come FIRST -- binding is positional'
        assert used == total          # no trailing garbage

    def test_shared_static_script_gets_both_too(self):
        flags, frags, used, total = self.parse(
            build_vmad_info_fragment('', script_name='TES4_ShowBarterMenu'))
        assert flags == 0x03 and frags == ['Fragment_1', 'Fragment_0']
        assert used == total


class TestInfoFragmentEmission:
    """The generated TES4_TIF__ script: Begin reports the line's own measured
    length, End runs the result and then the line-over hook LAST."""

    def _emit(self, tmp_path, rec, durations=None, quest_vars=None,
              quest_names=None):
        from script_convert import pipeline
        from script_convert.converter import ScriptConverter
        from script_convert.cross_ref import CrossRefGraph
        saved = ScriptConverter.say_durations
        saved_topics = ScriptConverter.say_topics
        ScriptConverter.say_durations = durations or {}
        # These cases test what a fragment CONTAINS, not whether one is
        # emitted (info_needs_fragment decides that, and drops a line with no
        # result script whose topic no script drives).  Give the record a
        # parent topic and mark that topic script-driven, so the emitter takes
        # the same path a real scripted line does.
        rec = dict(rec)
        rec.setdefault('ParentDIAL', '000000AA')
        ScriptConverter.say_topics = set(saved_topics) | {'000000AA'}
        pipeline._WORKER_CTX['quest_script_vars'] = quest_vars or {}
        pipeline._WORKER_CTX['quest_edid_by_fid'] = quest_names or {}
        stats = pipeline._new_stats()
        try:
            pipeline._info_batch([rec], str(tmp_path), CrossRefGraph(), stats)
        finally:
            ScriptConverter.say_durations = saved
            ScriptConverter.say_topics = saved_topics
        assert not stats['errors'], stats['errors']
        return (tmp_path / f"TES4_TIF__{rec['FormID']}.psc").read_text()

    def test_scriptless_line_gets_begin_and_end_hooks(self, tmp_path):
        psc = self._emit(tmp_path, {'FormID': '00032469'},
                         {'info:00032469': 12.62})
        begin = psc.split('Function Fragment_1', 1)[1].split('EndFunction')[0]
        end = psc.split('Function Fragment_0', 1)[1].split('EndFunction')[0]
        assert 'TES4Polyfill.LineBegan(akSpeakerRef, 12.62)' in begin
        assert 'TES4Polyfill.LineEnded(akSpeakerRef, 12.62)' in end

    def test_unmeasured_line_reports_zero(self, tmp_path):
        psc = self._emit(tmp_path, {'FormID': '00000ABC'})
        assert 'TES4Polyfill.LineBegan(akSpeakerRef, 0)' in psc

    def test_result_runs_before_line_ended(self, tmp_path):
        """A poll waiting on this speaker (SayLine's busy wait) proceeds the
        moment LineEnded runs, so the result's state writes must be visible
        by then -- the hook is the LAST statement of the End fragment."""
        psc = self._emit(tmp_path, {
            'FormID': '00032B0B',
            'ResultScript': 'set characterGen.speaker to 3',
        })
        end = psc.split('Function Fragment_0', 1)[1]
        assert end.index('speaker = 3') < end.index('TES4Polyfill.LineEnded')
        assert 'Fragment_1' in psc

    def test_fragment_never_writes_a_timer(self, tmp_path):
        """Fragments carry the speaker only; the conversation timer belongs to
        the calling script (TES4Polyfill.SayLine returns its value)."""
        psc = self._emit(tmp_path, {'FormID': '00032B0B'})
        assert 'convTimer' not in psc and 'Property' not in psc


class TestMultiResponseLineDuration:
    """An INFO's responses play back to back: the line length is their SUM.

    Taking the MAX under-charged every multi-response INFO, so the owning
    script's `timer <= 0` gate reopened mid-line and the poller re-Said over
    the still-playing line.  The engine drops a Say on an actor already
    talking, so the remaining responses are never heard.

    Measured in game 2026-08-15: Uriel Septim's CharacterGen greeting is
    5.51 + 1.59 + 5.51 = 12.62s but was charged 5.51 -- he spoke two of his
    three responses and the quest never left stage 42.
    """

    def _scan(self, tmp_path, files):
        import os
        from script_convert import say_durations as sd
        d = tmp_path / "sound" / "voice" / "ob.esm" / "imperial" / "m"
        d.mkdir(parents=True)
        for nm in files:
            (d / nm).write_bytes(b"")
        real = sd.mp3_duration
        sd.mp3_duration = lambda p: files[os.path.basename(p)]
        try:
            return sd.scan_voice_durations(str(tmp_path), use_cache=False)
        finally:
            sd.mp3_duration = real

    def test_responses_are_summed_not_maxed(self, tmp_path):
        got = self._scan(tmp_path, {
            "q_greeting_00032469_1.mp3": 5.51,
            "q_greeting_00032469_2.mp3": 1.59,
            "q_greeting_00032469_3.mp3": 5.51,
        })
        assert abs(got["info:00032469"] - 12.61) < 0.02

    def test_topic_max_is_over_whole_lines(self, tmp_path):
        """The call-site fallback must cover the longest LINE, so a topic
        whose longest line is multi-response reports the summed length."""
        got = self._scan(tmp_path, {
            "q_greeting_00032469_1.mp3": 5.51,
            "q_greeting_00032469_2.mp3": 1.59,
            "q_greeting_00032469_3.mp3": 5.51,
            "q_greeting_000AAAAA_1.mp3": 9.0,
        })
        assert abs(got["greeting"] - 12.61) < 0.02


class TestSayTimerConversion:
    def test_getsecondspassed_measures_real_elapsed_time(self, converter):
        """getSecondsPassed drains timers in MEASURED real time.

        The old emission substituted the registration interval as a constant,
        which assumed every tick took exactly that long — under VM load ticks
        run late and every counted timer drained slower than real time, so
        all conversation pacing floated with load (and changed whenever the
        poll cadence changed).  The prologue measures the actual elapsed
        time per pass instead; the clamp resets it across suspensions
        (unload/menu/save-load), which TES4's GameMode never counted.
        """
        src = ('Scriptname TestTick\n\nfloat timer\n\n'
               'begin gamemode\nset timer to timer - GetSecondsPassed\nend\n')
        result = converter.convert_standalone('TestTick', src, 'Quest',
                                              'TestTick')
        assert 'Float TES4_SecondsPassed' in result
        assert 'Utility.GetCurrentRealTime()' in result
        assert 'timer - TES4_SecondsPassed' in result
        # the prologue must run before the body's first decrement
        body = result.split('Event OnUpdate()', 1)[1]
        assert body.index('TES4_LastTick = TES4_Now') \
            < body.index('timer - TES4_SecondsPassed')

    def test_getsecondspassed_outside_a_poll_keeps_the_constant(self, converter):
        """A script with no GameMode/ScriptEffectUpdate block has no prologue,
        so the substitution must stay a literal there."""
        src = ('Scriptname TestNoPoll\n\nfloat timer\n\n'
               'begin onActivate\nset timer to timer - GetSecondsPassed\nend\n')
        result = converter.convert_standalone('TestNoPoll', src,
                                              'ObjectReference', 'TestNoPoll')
        assert 'TES4_SecondsPassed' not in result

    def test_bare_getsecondspassed_resets_the_realtime_baseline(self, converter):
        src = ('Scriptname TestReset\n\nfloat timer\n\n'
               'begin gamemode\nset timer to 10\nGetSecondsPassed\nend\n')
        result = converter.convert_standalone('TestReset', src, 'Quest',
                                              'TestReset')
        assert 'TES4_LastTick = Utility.GetCurrentRealTime()' in result
        assert ';TODO' not in result


class TestGenericScriptSyntaxRecovery:
    def test_legacy_actor_events(self, converter):
        src = ('Scriptname TestEvents\n'
               'begin OnKnockout\nStopCombat\nend\n'
               'begin OnMurder Player\nDeleteReference\nend\n')
        result = converter.convert_standalone('TestEvents', src, 'Actor',
                                              'TestEvents')
        assert 'Event OnEnterBleedout()' in result
        assert 'Event OnMurder(Actor akKiller)' in result
        murder = result.split('Event OnMurder(Actor akKiller)', 1)[1]
        assert 'If akKiller == Game.GetPlayer()' in murder

    def test_authored_todo_is_a_source_note(self, converter):
        src = ('Scriptname TestNotes\nshort value\n'
               '; TODO: original author note\n'
               'begin GameMode\nset value to 1 ; TODO tune value\nend\n')
        result = converter.convert_standalone('TestNotes', src, 'Quest',
                                              'TestNotes')
        assert '; Source note: original author note' in result
        assert '; Source note: tune value' in result

    def test_banners_and_quoted_commands_are_recovered(self, converter):
        src = ('Scriptname TestRecovery\n'
               'begin GameMode\n:================\n----------------\n'
               '"EnableLinkedPathPoints"\nend\n')
        result = converter.convert_standalone('TestRecovery', src, 'Quest',
                                              'TestRecovery')
        assert ';:================' in result
        assert ';----------------' in result
        assert ';NE: EnableLinkedPathPoints' in result

    def test_leading_logical_operator_continues_condition(self, converter):
        src = ('Scriptname TestContinuedIf\nref a\nref b\nref c\n'
               'begin GameMode\n'
               'if a.GetDisabled == 0 && b.GetDisabled == 0\n'
               '  && c.GetDisabled == 0\nset a to 0\nendif\nend\n')
        result = converter.convert_standalone('TestContinuedIf', src, 'Quest',
                                              'TestContinuedIf')
        condition = next(line for line in result.splitlines()
                         if 'TES4Polyfill.GetDisabled' in line)
        assert condition.count('TES4Polyfill.GetDisabled') == 3

    def test_bool_arithmetic_casts_only_the_operand(self, converter):
        src = ('Scriptname TestPlayableCount\nshort count\nref item\n'
               'begin GameMode\nlet count += IsPlayable2 item\nend\n')
        result = converter.convert_standalone('TestPlayableCount', src, 'Quest',
                                              'TestPlayableCount')
        assert '(TES4SKSE.GetBaseForm(item).IsPlayable() as Int)' in result

    def test_say_assignment_becomes_a_blocking_sayline(self, converter):
        """`set T to ref.Say topic` -> T := TES4Polyfill.SayLine(ref, topic, fallback).

        TES4 returned the selected line's length synchronously and the script
        went on at once; SayLine blocks until the engine has BEGUN the line and
        returns that line's real length (+ tail).  The pre-charge closes this
        poll's own `T <= 0` guard for the ~2s a SayLine can take, so a second
        poll tick cannot start a duplicate.
        """
        converter._property_refs['ThadonRef'] = 'Actor'
        result = conv_line(converter, 
            'set timer to ThadonRef.Say DeathSpeech01', 'Quest')
        lines = [l.strip() for l in result.split('\n')]
        assert lines[0].startswith('timer = 1.75')       # pre-charge
        assert lines[1] == 'timer = TES4Polyfill.SayLine(ThadonRef, DeathSpeech01, 3)'
        # nothing else Says the line
        assert result.count('.Say(') == 0

    def test_sayline_uses_the_topics_measured_maximum_as_fallback(self, converter):
        from script_convert.converter import ScriptConverter
        saved = ScriptConverter.say_durations
        ScriptConverter.say_durations = {'chargentaunt2': 14.63}
        try:
            result = conv_line(converter, 
                'set timer to SayTo player CharGenTaunt2 1', 'Actor')
        finally:
            ScriptConverter.say_durations = saved
        assert 'TES4Polyfill.SayLine(Self, CharGenTaunt2, 14.63)' in result
        # The pre-charge is FIXED at SAY_START_WAIT + 0.25 -- it covers the
        # window SayLine can block for, not the line, so a 14.63s line does not
        # hold the guard for 14.63s the way the old length-scaled charge did.
        assert 'timer = 1.75  ;' in result

    def test_precharge_outlasts_saylines_start_timeout(self):
        """The pre-charge must cover the whole window SayLine can BLOCK for.

        SayLine returns fast on a line the engine accepts, but on a DROPPED
        line it waits SAY_START_WAIT and returns 0.0.  If the pre-charge is
        shorter, the caller's `T <= 0` guard reopens while SayLine is still
        blocked and a second poll tick re-enters -- the duplicate-line class
        the pre-charge exists to prevent.  Keep the two in step.
        """
        import re as _re
        from script_convert.converter import SAY_START_WAIT
        src = open('script_convert/static_scripts/TES4Polyfill.psc',
                   encoding='utf-8').read()
        m = _re.search(
            r'Float Function SAY_START_WAIT\(\) Global\s*\n\s*Return\s+([\d.]+)',
            src)
        assert m, 'SAY_START_WAIT not found in TES4Polyfill.psc'
        assert float(m.group(1)) == SAY_START_WAIT, (
            f'converter SAY_START_WAIT={SAY_START_WAIT} but the polyfill waits {m.group(1)}')

    def test_sayline_returns_length_only_no_tail(self):
        """SayLine must NOT add a tail to the value it returns.

        The tail is the engine's End-fragment overhead.  Adding it to the
        return value charged it to the CALLER'S COUNTDOWN, i.e. as silence
        after every line -- and 26 of the 31 audible gaps in the 2026-08-16
        recording handed off to a DIFFERENT actor, for whom the padding buys
        nothing.  It belongs in _IsSpeaking, where only a re-Say on the same
        actor pays it.
        """
        src = open('script_convert/static_scripts/TES4Polyfill.psc',
                   encoding='utf-8').read()
        body = src[src.index('Float Function SayLine('):]
        body = body[:body.index(chr(10) + 'EndFunction')]
        assert 'Return len + tail' not in body
        assert 'Return len' in body
        # and the grace it replaced must be enforced on the speaker instead
        assert 'Variable03' in src, 'the End-grace stamp is gone'
        isspeaking = src[src.index('Bool Function _IsSpeaking('):]
        isspeaking = isspeaking[:isspeaking.index(chr(10) + 'EndFunction')]
        assert 'SAY_GRACE()' in isspeaking, (
            '_IsSpeaking must hold the post-End grace, or a re-Say can be dropped')

    def test_stage_timer_guard_waits_one_pass_at_the_new_stage(self, converter):
        """`GetStage()==N && <timer> <= 0` must not fire the pass stage N arrives.

        The timer is charged by stage N's OWN fragment, and nothing orders that
        charge before the guard is first tested.  The timer's resting state is
        <= 0 (and it goes NEGATIVE whenever a line is dropped), so the guard is
        already satisfied the instant stage N is set.

        Measured 2026-08-16 (temp/chargen_rec_4.log): CharacterGen sat at
        convTimer = -0.076 for four seconds after a dropped line, so
        `GetStage()==16 && convTimer<=0` fired the moment stage 16 arrived.
        SetStage(17) ran, the force-greet took the player into the menu, and
        the Emperor's stage-16 line never played -- INFO 00032B11 is gated on
        `GetStage CharacterGen == 16` and is the only CharGenVoice entry for
        that stage, so at 17 nothing qualifies at all.
        """
        src = ('scn T\n\nfloat convTimer\n\nbegin gamemode\n'
               'if getstage CharacterGen == 16 && convTimer <= 0\n'
               'setstage CharacterGen 17\n'
               'endif\nend\n')
        out = converter.convert_standalone('T', src, 'Quest', 'T')
        guard = [l for l in out.split('\n') if 'GetStage() == 16' in l]
        assert guard, out
        assert 'TES4_LastStage_CharacterGen == 16' in guard[0], guard[0]
        # declared as -1 so the FIRST pass at any stage cannot satisfy it
        assert 'Int TES4_LastStage_CharacterGen = -1' in out
        # and updated at the END of the poll, after every guard above it
        body = out[out.index('Event OnUpdate()'):]
        upd = body.index('TES4_LastStage_CharacterGen = CharacterGen.GetStage()')
        assert upd > body.index('GetStage() == 16'), 'latch updated before the guard'

    def test_one_latch_per_quest_regardless_of_spelling(self, converter):
        """TES4 spells the same quest both ways in one file.

        CharacterGen's own poll uses `characterGen` on some lines and
        `CharacterGen` on others.  Keying the latch on the raw spelling emitted
        TWO variables for one quest, and a guard could compare against the one
        the poll tail never updated -- so the guard would never open and the
        beat would hang.  Papyrus is case-insensitive, so the duplicate
        declarations COMPILED; only an in-game stall would have shown it.
        """
        src = ('scn T\n\nfloat convTimer\n\nbegin gamemode\n'
               'if getstage characterGen == 16 && convTimer <= 0\n'
               'setstage characterGen 17\n'
               'endif\n'
               'if getstage CharacterGen == 45 && convTimer <= 0\n'
               'setstage CharacterGen 46\n'
               'endif\nend\n')
        out = converter.convert_standalone('T', src, 'Quest', 'T')
        decls = [l for l in out.split('\n') if l.startswith('Int TES4_LastStage')]
        assert len(decls) == 1, decls
        updates = [l for l in out.split('\n')
                   if 'TES4_LastStage' in l and '.GetStage()' in l
                   and not l.strip().startswith('If')]
        assert len(updates) == 1, updates
        # both guards must reference that single latch
        guards = [l for l in out.split('\n') if l.strip().startswith('If ')
                  and 'TES4_LastStage' in l]
        assert len(guards) == 2, guards
        name = decls[0].split()[1]
        assert all(name in g for g in guards), guards

    def test_stage_guard_without_a_timer_is_untouched(self, converter):
        """Only the timer-gated shape races; a plain stage test must not change."""
        src = ('scn T\n\nshort x\n\nbegin gamemode\n'
               'if getstage CharacterGen == 16\nset x to 1\nendif\nend\n')
        out = converter.convert_standalone('T', src, 'Quest', 'T')
        assert 'TES4_LastStage' not in out

    def test_sayline_waits_out_another_actors_line(self):
        """A Say issued while ANOTHER actor is mid-line is refused by Skyrim.

        The caller then sits out the whole SAY_START_WAIT and returns 0.0, and
        its poll retries a tick later -- the 2-3s cluster of gaps.  Measured
        2026-08-16: 13 of 17 drops in temp/chargen_rec_5.log had a DIFFERENT
        actor speaking while the dropped actor was silent, because each
        participant's guard is `speaker == N && convTimer <= 0` and convTimer
        counts the AUDIO length, so it reaches zero before the previous
        speaker's End fragment has run.

        Waiting is strictly cheaper than being refused: the wait ends when the
        other line does, a refusal costs the full timeout plus a retry.
        """
        src = open('script_convert/static_scripts/TES4Polyfill.psc',
                   encoding='utf-8').read()
        body = src[src.index('Float Function SayLine('):]
        body = body[:body.index(chr(10) + 'EndFunction')]
        assert '_OtherLineInProgress()' in body, (
            'SayLine must wait out another actor before issuing its Say')
        # the record has to be both SET when a line begins and RELEASED when it
        # ends early, or a skipped line strands the next speaker
        began = src[src.index('Function LineBegan('):]
        began = began[:began.index(chr(10) + 'EndFunction')]
        assert 'Variable07' in began, 'LineBegan must record the game-wide line'
        ended = src[src.index('Function LineEnded('):]
        ended = ended[:ended.index(chr(10) + 'EndFunction')]
        assert 'Variable07", 0.0' in ended, (
            'LineEnded must release the record, or a skipped line stalls the next')

    def test_authored_offset_survives(self, converter):
        converter._property_refs['ThadonRef'] = 'Actor'
        result = conv_line(converter, 
            'set timer to (ThadonRef.Say DeathSpeech01) + 2', 'Quest')
        assert 'TES4Polyfill.SayLine(ThadonRef, DeathSpeech01, 3) + 2' in result

    def test_short_timer_rounds_the_length_up(self, converter):
        """A TES4 `short` holding a Say length truncates in Papyrus; ceil so
        the tail that covers the End fragment's latency survives."""
        converter._var_types['saylen'] = 'Int'
        converter._property_refs['ThadonRef'] = 'Actor'
        result = conv_line(converter, 
            'set saylen to ThadonRef.Say DeathSpeech01', 'Quest')
        assert 'saylen = Math.Ceiling(TES4Polyfill.SayLine(ThadonRef, DeathSpeech01, 3))' in result

    def test_measure_then_deliver_pair_speaks_once(self, converter):
        """Oblivion's `set L to ref.Say T` / `ref.Say T` idiom: SayLine both
        measures and delivers, so the bare delivery is dropped."""
        converter._property_refs['ArmandRef'] = 'Actor'
        lines = [
            conv_line(converter, 'set InfoLength to ArmandRef.Say TG01Armand1', 'Quest'),
            conv_line(converter, 'ArmandRef.SayTo Player TG01Armand1', 'Quest'),
        ]
        out = converter._postprocess_lines(lines)
        joined = '\n'.join(out)
        assert joined.count('SayLine(') == 1
        assert '.Say(' not in joined

    def test_polls_are_suspended_while_the_player_is_in_dialogue(self, converter):
        """TES4 GameMode never ran while a menu was open.  Actor AND quest
        polls skip the pass while the player is in dialogue with anyone (or
        that actor is still speaking the Goodbye line), so a poll cannot
        Say() over a live menu line or fire a stage that sends another actor
        in over it (both measured in game, CharacterGen 42-50)."""
        src = ('scn T\n\nshort x\n\nbegin gamemode\nset x to 1\n'
               'sayto player SomeTopic\nend\n')
        actor = converter.convert_standalone('T', src, 'Actor', 'T')
        body = actor.split('Event OnUpdate()', 1)[1]
        # own dialogue OR any dialogue (Baurus's torch line fired into the
        # player's conversation with the Emperor)
        assert ('If IsInDialogueWithPlayer() || '
                'TES4Polyfill.PlayerIsInDialogue()') in body
        assert body.index('IsInDialogueWithPlayer') < body.index('x = 1')
        # Quest polls are gated too (stage 45->50 fired from the quest poll
        # while the player was still in the Emperor's dialogue and sent
        # Baurus in over it); the countdown pausing in a menu is Oblivion's
        # own behaviour now that SayLine returns real line lengths.
        quest = converter.convert_standalone('T', src, 'Quest', 'T')
        qbody = quest.split('Event OnUpdate()', 1)[1]
        assert 'If TES4Polyfill.PlayerIsInDialogue()' in qbody
        assert 'If IsInDialogueWithPlayer()' not in qbody
        # A poll that never speaks is NOT gated: the gate on ~210 quest polls
        # starved the VM (End fragments 11-17s late -> repeats).
        silent = ('scn T\n\nshort x\n\nbegin gamemode\nset x to 1\nend\n')
        for ext in ('Actor', 'Quest'):
            out = converter.convert_standalone('T', silent, ext, 'T')
            assert 'PlayerIsInDialogue' not in out

    def test_say_driving_script_polls_fast(self, converter):
        """The `T <= 0` guard is what starts the next line, so the poll tick
        is dead air between lines: a script with a timer-Say ticks at 0.15s;
        an ordinary actor script keeps 0.5.

        0.1 for all of them once overloaded the VM and LENGTHENED the gaps,
        but that was measured while SayLine still blocked on two fixed
        Utility.Wait calls and fragments blocked the dispatch path.  With
        those gone the contention is gone too."""
        say = ('scn T\n\nfloat t\n\nbegin gamemode\n'
               'if t <= 0\nset t to Say SomeTopic\nendif\nend\n')
        out = converter.convert_standalone('T', say, 'Actor', 'T')
        assert 'RegisterForSingleUpdate(0.15)' in out
        plain = ('scn T\n\nshort x\n\nbegin gamemode\nset x to 1\nend\n')
        out = converter.convert_standalone('T', plain, 'Actor', 'T')
        assert 'RegisterForSingleUpdate(0.5)' in out

    def test_countdown_and_overrides_are_plain(self, converter):
        """The timer is an ordinary countdown again: no park-safe decrement,
        no guarded override, no beat companion.  TES4 semantics need none of
        them once SayLine returns the length at line START (an override right
        after the Say replaces the length before any countdown, exactly as
        `set convTimer to 12` did in Oblivion)."""
        src = ('Scriptname T\n\nfloat convTimer\n\nbegin gamemode\n'
               'if convTimer > 0\n set convTimer to convTimer - getSecondsPassed\nendif\n'
               'if convTimer <= 0\n set convTimer to Say SomeTopic\n'
               ' set convTimer to 12\n set convTimer to convTimer + 2.5\nendif\n'
               'end\n')
        result = converter.convert_standalone('T', src, 'Actor', 'T')
        assert 'convTimer = convTimer - TES4_SecondsPassed' in result
        assert '_tes4Tick' not in result
        assert 'PendingBeat' not in result
        assert 'convTimer = 12' in result and 'If convTimer <= 0  ; not while' not in result
        assert 'convTimer = convTimer + 2.5' in result


class TestFilterGuardTes4Type:
    def test_guard_kept_when_property_bound_as_tes4_script(self, xref):
        xref.edid_to_formid['cgassassin01ref'] = '00012345'
        xref.record_type['00012345'] = 'ACHR'
        conv = ScriptConverter(xref)
        conv._property_refs['CGAssassin01Ref'] = 'TES4_CGAssassinScript'
        guard = conv._block_filter_guard('onhit', 'CGAssassin01Ref')
        assert guard == 'akAggressor == CGAssassin01Ref'


class TestGameHourFractional:
    """GameHour is a FLOAT global in Skyrim (FormID 0x38, FNAM=102).

    Truncating the read with `as Int` collapsed every hour-boundary window
    (`>= 23.98 || <= 0.02`) into an always-true whole-hour test, so the guarded
    body ran every frame — the Erodans-Kapelle chapel bell and Oblivion's
    BellTowerScript rang continuously instead of once on the hour.
    """

    def test_gamehour_read_is_not_truncated(self, converter):
        assert conv_expr(converter, 'GameHour', 'ObjectReference') \
            == 'GameHour.GetValue()'

    def test_hour_boundary_window_survives(self, converter):
        out = conv_expr(converter, 
            '( GameHour >= 23.98 ) || ( GameHour <= 0.02 )', 'ObjectReference')
        assert 'as Int' not in out
        assert '23.98' in out and '0.02' in out

    def test_integer_global_still_truncated(self, xref):
        """A genuinely short global keeps its cast — only floats are exempt."""
        xref.edid_to_formid['myshortglobal'] = '00099001'
        xref.record_type['00099001'] = 'GLOB'
        xref.formid_to_edid['00099001'] = 'MyShortGlobal'
        xref.global_types['myshortglobal'] = 's'
        conv = ScriptConverter(xref)
        assert conv_expr(conv, 'MyShortGlobal', 'ObjectReference') \
            == 'MyShortGlobal.GetValue() as Int'

    def test_float_typed_global_not_truncated(self, xref):
        xref.edid_to_formid['myfloatglobal'] = '00099002'
        xref.record_type['00099002'] = 'GLOB'
        xref.formid_to_edid['00099002'] = 'MyFloatGlobal'
        xref.global_types['myfloatglobal'] = 'f'
        conv = ScriptConverter(xref)
        assert conv_expr(conv, 'MyFloatGlobal', 'ObjectReference') \
            == 'MyFloatGlobal.GetValue()'


class TestEnumActorValues:
    """TES4 stores Aggression/Confidence on 0-100; TES5 defines them as small
    enums (xEdit wbAggressionEnum 0-3, wbConfidenceEnum 0-4).  Writing the raw
    TES4 number is rejected by the engine ("attempt made to set illegal
    value") and leaves the trait UNCHANGED, so every scripted "turn hostile"
    beat silently did nothing.
    """

    def test_aggression_100_becomes_tier(self, converter):
        out = conv_line(converter, 
            'SetActorValue Aggression, 100', 'ObjectReference')
        assert 'SetActorValue("Aggression", 2)' in out

    def test_low_aggression_fights_enemies_not_bystanders(self, converter):
        """`setav aggression 10` must NOT become "attack neutrals on sight".

        TES4 aggression is half of a per-target rule — attack when
        disposition(actor->target) < aggression - 5 (UESP Oblivion:Aggression).
        10 only beats a disposition below 5, so it means "join this specific
        fight", not "turn on bystanders". TES5 tier 2 attacks Neutrals, and the
        player is a Neutral to most factions, so 10 -> 2 made converted guards
        hostile to the player.

        This is CharacterGen stage 22: the Emperor's guards get
        `setav aggression 10` so they respond to the Mythic Dawn ambush, and
        their disposition toward the player is ~47. Landing them on tier 2 made
        them attack the player from stage 22 on. UESP names the failure mode
        directly: "a guard would attack the whole town if their aggression were
        sufficiently raised."
        """
        out = conv_line(converter, 
            'SetActorValue Aggression, 10', 'ObjectReference')
        assert 'SetActorValue("Aggression", 1)' in out

    def test_high_aggression_still_attacks_on_sight(self, converter):
        """The real "now attack anyone" beats (90/100) must keep tier 2."""
        for value in (70, 90, 100):
            out = conv_line(converter, 
                f'SetActorValue Aggression, {value}', 'ObjectReference')
            assert 'SetActorValue("Aggression", 2)' in out, value

    def test_aggression_five_never_initiates(self, converter):
        """<=5 is Oblivion's "never attack" floor."""
        out = conv_line(converter, 
            'SetActorValue Aggression, 5', 'ObjectReference')
        assert 'SetActorValue("Aggression", 0)' in out

    def test_frenzy_range_attacks_everyone(self, converter):
        """>=106 is Frenzy: attacks anyone, including allies."""
        out = conv_line(converter, 
            'SetActorValue Aggression, 110', 'ObjectReference')
        assert 'SetActorValue("Aggression", 3)' in out

    def test_in_range_value_passes_through(self, converter):
        """An already-legal tier is a deliberate value, not re-bucketed."""
        out = conv_line(converter, 
            'SetActorValue Aggression, 0', 'ObjectReference')
        assert 'SetActorValue("Aggression", 0)' in out

    def test_confidence_scaled(self, converter):
        """Oblivion 100 = fearless → Foolhardy (4), the only tier that never
        flees.  Mapping it to Brave (3) left actors with a nonzero flee score
        and made them run away constantly."""
        out = conv_line(converter, 
            'SetActorValue Confidence, 100', 'ObjectReference')
        assert 'SetActorValue("Confidence", 4)' in out

    def test_confidence_tiers_span_full_range(self, converter):
        """Must mirror _convert_aidt: all five tiers are reachable."""
        for raw, tier in ((100, 4), (75, 3), (50, 2), (20, 1), (5, 0)):
            out = conv_line(converter, 
                f'SetActorValue Confidence, {raw}', 'ObjectReference')
            assert f'SetActorValue("Confidence", {tier})' in out, (raw, out)

    def test_non_enum_actor_value_untouched(self, converter):
        out = conv_line(converter, 
            'SetActorValue Health, 100', 'ObjectReference')
        assert 'SetActorValue("Health", 100)' in out

    def test_variable_operand_left_alone(self, converter):
        """A non-literal cannot be bucketed at conversion time."""
        conv_out = conv_line(converter, 
            'SetActorValue Aggression, myVar', 'ObjectReference')
        assert 'myVar' in conv_out


class TestZeroArgRefReceiver:
    """Oblivion let the receiver of a zero-argument `ref.` command follow a
    comma instead of a dot: `StopCombat, Player` means `Player.StopCombat`.
    Treating it as an argument emitted `IsInCombat(Player)` ("function takes 0
    parameters not 1") or dropped it and acted on the wrong actor.
    """

    def test_stopcombat_comma_receiver(self, converter):
        out = conv_line(converter, 'StopCombat, Player', 'ObjectReference')
        assert out == 'Game.GetPlayer().StopCombat()'

    def test_isincombat_comma_receiver_in_comparison(self, converter):
        out = conv_expr(converter, 'IsInCombat, Player == 1', 'ObjectReference')
        assert out == 'Game.GetPlayer().IsInCombat()'

    def test_getdeadcount_prefix_not_split(self, xref):
        """`GetDead` must not match the prefix of `GetDeadCount`."""
        xref.edid_to_formid['narel'] = '00099010'
        xref.record_type['00099010'] = 'NPC_'
        xref.formid_to_edid['00099010'] = 'Narel'
        conv = ScriptConverter(xref)
        out = conv_expr(conv, 'GetDeadCount Narel == 1', 'ObjectReference')
        assert out == 'Narel.GetDeadCount() == 1'

    def test_arg_taking_function_keeps_its_argument(self, xref):
        """GetInFaction takes a real argument — it must NOT be promoted."""
        xref.edid_to_formid['myfaction'] = '00099011'
        xref.record_type['00099011'] = 'FACT'
        xref.formid_to_edid['00099011'] = 'MyFaction'
        conv = ScriptConverter(xref)
        out = conv_expr(conv, 'GetInFaction, MyFaction == 1', 'ObjectReference')
        assert 'MyFaction' in out and 'IsInFaction(' in out


class TestLocalVariableShadowsPlayer:
    """TES4 scripts may declare `Short Player` as their own flag
    (StartCelleAufzugTriggerZone01Script does).  Rewriting that to
    Game.GetPlayer() produced the un-assignable `Game.GetPlayer() = 1`.
    """

    def test_local_wins_in_value_position(self, converter):
        converter._local_vars = {'player'}
        converter._var_types = {'player': 'Int'}
        assert converter._convert_ref('Player', 'ObjectReference') == 'Player'

    def test_keyword_wins_as_receiver(self, converter):
        """A Short has no methods, so `Player.GetDistance` is the keyword."""
        converter._local_vars = {'player'}
        converter._var_types = {'player': 'Int'}
        assert converter._convert_ref('Player', 'ObjectReference',
                                      as_receiver=True) == 'Game.GetPlayer()'

    def test_keyword_used_when_no_local(self, converter):
        assert converter._convert_ref('Player', 'ObjectReference') \
            == 'Game.GetPlayer()'


class TestEarlyReturnKeepsPolling:
    """TES4 `return` ends only THIS FRAME's GameMode pass — the script runs
    again next frame.  Papyrus OnUpdate is one-shot and self-rescheduling, so:

    * the poll is armed FIRST with a LONG (5s) abort-insurance interval, so a
      RUNTIME ABORT in the body ("Cannot call X on a None object" ends the
      event at that line) cannot kill the poll for the rest of the game;
    * every early `Return` re-arms at the REAL interval itself (115 such
      Returns existed across 96 scripts; MG05RockScript fires one shock bolt
      per tick and used `return` to serialize six);
    * the bottom arm sets the cadence, measured from the END of the pass.

    🛑 The top arm must NOT be the real interval.  RegisterForSingleUpdate
    counts from now, so a top arm at `interval` starts the next pass
    `interval` after this one STARTED; a pass longer than that overlaps
    itself and the pile grows without bound (measured 2026-08-16: 251
    concurrent TES4_MQ01Script.OnUpdate stacks, the whole VM starved).
    """

    SRC = """Scriptname TestEarlyReturn
short foo
begin gamemode
if ( foo == 0 )
    return
endif
set foo to 1
End
"""

    def test_quest_script_top_arm_is_long_insurance_only(self, converter):
        out = converter.convert_standalone('T', self.SRC, 'Quest', 'T')
        body = out.split('Event OnUpdate()')[1].split('EndEvent')[0]
        # The 5s insurance arm precedes both the IsRunning() guard and the
        # body, so an abort cannot stop the loop -- but the real interval is
        # never armed from the top.
        assert body.index('RegisterForSingleUpdate(5.0)')             < body.index('IsRunning()')
        assert body.count('RegisterForSingleUpdate(5.0)') == 1
        assert body.lstrip().startswith('RegisterForSingleUpdate(5.0)')

    def test_early_return_re_arms_at_the_real_interval(self, converter):
        out = converter.convert_standalone('T', self.SRC, 'Quest', 'T')
        body = out.split('Event OnUpdate()')[1].split('EndEvent')[0]
        # (the IsRunning() gate's own Return keeps the 5s insurance: a quest
        # that is not running need not poll faster)
        ret = body.index('Return', body.index('foo == 0'))
        # the statement immediately before the authored Return is the arm
        before = body[:ret].rstrip().splitlines()[-1].strip()
        assert before == 'RegisterForSingleUpdate(0.5)'
        # and the bottom arm is still there
        assert body.rstrip().endswith('RegisterForSingleUpdate(0.5)')

    def test_object_script_uses_the_load_gated_form(self, converter):
        """An object/actor script's poll is MEANT to stop on unload, so both
        the insurance arm and the spliced re-arm carry the load gate — not an
        unconditional call that would keep ticking forever."""
        out = converter.convert_standalone('T', self.SRC, 'ObjectReference',
                                           'T')
        body = out.split('Event OnUpdate()')[1].split('EndEvent')[0]
        idx = body.index('Return')
        before = body[:idx]
        assert before.count('If (TES4Polyfill.SafeGameModeGate(Self))') == 2
        assert 'RegisterForSingleUpdate(5.0)' in before
        assert 'RegisterForSingleUpdate(0.5)' in before

    def test_value_returning_function_untouched(self, converter):
        """`Return <value>` belongs to an OBSE user function, not a GameMode
        early-out, and must not have a poll re-arm spliced in front of it."""
        converter._udf_returns = True
        assert conv_line(converter, 'return', 'Quest') == 'Return 0'


class TestNoPollFreeze:
    """A poll that never SPEAKS is never frozen: the 2026-08-14 attempt froze
    every poll (Utility.IsInMenuMode / TES4_LastSpeaker) and shifted every
    conversation beat; the 2026-08-16 dialogue gate on every poll starved the
    VM.  Only scripts with a Say/SayTo carry TES4Polyfill.PlayerIsInDialogue
    (see TestSayTimerConversion)."""

    SRC = """Scriptname T
short foo
begin gamemode
set foo to 1
End
"""

    def test_silent_polls_are_not_frozen(self, converter):
        for extends in ('Quest', 'ObjectReference', 'Actor'):
            out = converter.convert_standalone('T', self.SRC, extends, 'T')
            assert 'IsInMenuMode' not in out
            assert 'TES4_LastSpeaker' not in out
            assert 'PlayerIsInDialogue' not in out


class TestChargenMenus:
    """ShowBirthsignMenu/ShowClassMenu → modal Message pages (see
    message_menus.build_chargen_menus).  TES4's menus paused the game and
    scripted scenes depend on that beat: CharacterGen's Emperor carries an
    authored Goodbye at the birthsign point and re-force-greets afterwards —
    a no-op dumped the player into a free-roam gap mid-scene where Baurus's
    pending torch force-greet could steal them."""

    PLAN = {
        'birthsign': {
            'pages': [('TES4Msg_ChargenBirthsign_01', 'Title',
                       ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I',
                        'More ...']),
                      ('TES4Msg_ChargenBirthsign_02', 'Title', ['J', 'K'])],
            'actions': [['SpellA'], [], [], [], [], [], [], [], [],
                        ['SpellJ1', 'SpellJ2'], []],
        },
    }

    def test_menu_emission(self, converter):
        converter.chargen_menus = self.PLAN
        out = conv_line(converter, 'ShowBirthsignMenu', 'Quest')
        assert 'TES4Msg_ChargenBirthsign_01.Show()' in out
        # page chaining: "More ..." is button 9, global index = 9*page+button
        assert 'If TES4_menuPick1 == 9' in out
        assert '9 + TES4Msg_ChargenBirthsign_02.Show()' in out
        # the chosen sign's spells are granted
        assert 'If TES4_menuPick1 == 0' in out
        assert 'Game.GetPlayer().AddSpell(SpellA, false)' in out
        assert 'ElseIf TES4_menuPick1 == 9' in out
        assert 'Game.GetPlayer().AddSpell(SpellJ1, false)' in out
        # properties minted for VMAD binding
        assert converter._property_refs['TES4Msg_ChargenBirthsign_01'] == 'Message'
        assert converter._property_refs['SpellA'] == 'Spell'

    def test_menu_is_reentrancy_latched(self, converter):
        """Message.Show() parks only its own thread; an OnUpdate tick queued
        behind the open menu re-enters the body while the menu is STILL OPEN
        (the poll re-arms at the top of OnUpdate, so the next tick lands
        0.1s later on another thread).

        That pass must RETURN, not fall through.  TES4's menu was modal to
        the whole GameMode pass: the `setstage 44` on the next source line
        did not run until the player had chosen.  Falling through ran it
        mid-menu, and stage 44's fragment force-greets the Emperor
        (`UrielSeptimRef.evp`) at a player still locked in the menu — the
        greet is consumed with nobody able to receive it, so the menu closes
        onto a silent Emperor and CharacterGen soft-locks.  Verified live
        through the game bridge (2026-08-15): stage 43 advanced to 44
        instantly while the choice global was still 0.
        """
        converter.chargen_menus = self.PLAN
        converter._current_event = 'Event OnUpdate()'
        out = conv_line(converter, 'ShowBirthsignMenu', 'Quest')
        assert 'If TES4_ChargenMenuBusy' in out
        assert 'TES4_ChargenMenuBusy = True' in out
        assert 'TES4_ChargenMenuBusy = False' in out
        # A latched-out pass returns before reaching the menu...
        assert out.index('If TES4_ChargenMenuBusy') < out.index('Return')
        assert out.index('Return') < out.index('.Show()')
        # ...and the latch is only taken once the guard has passed.
        assert out.index('TES4_ChargenMenuBusy = True') < out.index('.Show()')
        assert converter._uses_chargen_menus

    def test_oneshot_menu_site_falls_through(self, converter):
        """A ONE-SHOT site (quest-stage fragment, OnActivate) must NOT
        Return on a latched-out pass: nothing repeating re-enters it, so the
        latch can only trip on a genuine race, and a Return would DROP the
        authored tail instead of deferring it.  CharacterGen stage 87 puts
        `MQ02.SetStage(20)`, the end-of-chargen topic unlocks and the
        autosave after its class menu — skipping those is worse than showing
        the menu twice."""
        converter.chargen_menus = self.PLAN
        converter._current_event = 'Function Fragment_Stage_0087_Item_0()'
        out = conv_line(converter, 'ShowBirthsignMenu', 'Quest')
        assert 'If !TES4_ChargenMenuBusy' in out
        assert 'Return' not in out
        assert out.rstrip().endswith('EndIf')
        assert out.index('If !TES4_ChargenMenuBusy') < out.index('.Show()')

    def test_menu_persists_choice_to_global(self, converter):
        """The pick lands in the choice GLOB as index+1 (0 = unchosen) so
        the rewritten GetIsPlayerBirthsign conditions can match it — the
        Emperor's 'Your stars are not mine. Today the <sign>...' line must
        agree with the sign actually picked.  A failed pick (Show() -1)
        must never be persisted: the SetValue is guarded, and the dialogue
        side keeps an ungated fallback line for the unchosen case."""
        plan = {'birthsign': dict(self.PLAN['birthsign'],
                                  choice_global='TES4ChargenBirthsignChoice')}
        converter.chargen_menus = plan
        out = conv_line(converter, 'ShowBirthsignMenu', 'Quest')
        assert 'TES4ChargenBirthsignChoice.SetValue(TES4_menuPick1 + 1)' in out
        assert out.index('If TES4_menuPick1 >= 0') \
            < out.index('.SetValue(TES4_menuPick1 + 1)')
        assert (converter._property_refs['TES4ChargenBirthsignChoice']
                == 'GlobalVariable')

    def test_menu_show_retries_on_display_failure(self, converter):
        """Show() returns -1 when the box cannot display (a menu/dialogue
        transition still in flight — this menu opens 0.1s after an
        authored Goodbye closes the conversation).  The emission retries
        briefly instead of swallowing the player's choice."""
        converter.chargen_menus = self.PLAN
        out = conv_line(converter, 'ShowBirthsignMenu', 'Quest')
        assert 'While TES4_menuPick1 < 0 && TES4_menuRetry1 < 20' in out
        assert 'Utility.Wait(0.5)' in out

    def test_no_plan_stays_noop(self, converter):
        """A plugin without BSGN records keeps the inert conversion."""
        converter.chargen_menus = {}
        out = conv_line(converter, 'ShowBirthsignMenu', 'Quest')
        assert 'Show()' not in out
        assert ';NE: ShowBirthsignMenu' in out

    def test_pagination_contract(self):
        """Both sides derive identical pages: 13 labels → 9 + More, then 4."""
        from script_convert.message_menus import _paged
        pages = _paged('P_%02d', 'T', [chr(65 + i) for i in range(13)])
        assert len(pages) == 2
        assert pages[0][2][-1] == 'More ...'
        assert len(pages[0][2]) == 10 and len(pages[1][2]) == 4


class TestStartCombatIsForced:
    """TES4 StartCombat forces the fight regardless of aggression, disposition
    or faction relations; Skyrim's native is only a nudge the combat AI drops
    when the actor's Aggression is 0 or the target is not hostile to it
    (CharacterGen stage 74: the aggression-0 final assassin, whose only
    faction the Emperor's faction Friends, must still kill the Emperor).
    TES4Polyfill.ForceCombat supplies the preconditions before the native.
    """

    def test_npc_startcombat_routes_through_forcecombat(self, converter):
        """ForceCombat carries the conversion-owned enemy-faction pair: the
        earlier relationship-rank approach silently no-ops between
        non-unique actors (the final assassin is non-unique), so the pair
        hostility comes from AddToFaction into record-side mutual enemies."""
        src = ('scn T\n\nbegin gamemode\n'
               '\tCGAssassinFinal.startcombat UrielSeptimRef\nend\n')
        out = converter.convert_standalone('T', src, 'Quest', 'T')
        assert ('TES4Polyfill.ForceCombat(' in out
                and 'TES4ForceCombatAttackers, TES4ForceCombatVictims)' in out)
        assert '.StartCombat(' not in out
        # faction properties minted for VMAD binding to the import's records
        assert 'Faction Property TES4ForceCombatAttackers Auto' in out
        assert 'Faction Property TES4ForceCombatVictims Auto' in out

    def test_player_attacker_keeps_plain_native(self, converter):
        """The player's combat is player-driven; forcing would brand the
        target the player's archenemy for the rest of the save."""
        src = ('scn T\n\nbegin gamemode\n'
               '\tplayer.startcombat BanditRef\nend\n')
        out = converter.convert_standalone('T', src, 'Quest', 'T')
        assert 'TES4Polyfill.ForceCombat(' not in out
        assert '.StartCombat(' in out

    def test_bare_startcombat_in_a_non_actor_script_casts_self(self, converter):
        """Nehrim's UNUSED MQ33Sarantha02Script (attached to nothing, so it
        extends ObjectReference) does `StartCombat, Player`.  ForceCombat's
        parameter is Actor-typed and Papyrus refuses an ObjectReference there
        (Checker error: cannot convert type ... to type Actor), which took the
        whole plugin's compile pass to 3738/3739."""
        src = ('scn T\n\nbegin gamemode\n'
               '\tStartCombat, Player\nend\n')
        out = converter.convert_standalone('T', src, 'ObjectReference', 'T')
        assert 'TES4Polyfill.ForceCombat((Self as Actor), Game.GetPlayer()' in out
        # An actor script keeps the plain Self
        out = converter.convert_standalone('T', src, 'Actor', 'T')
        assert 'TES4Polyfill.ForceCombat(Self, Game.GetPlayer()' in out

    def test_moddisposition_hostile_idiom_is_forced_too(self, converter):
        """`ModDisposition <target> -100` is the same "attack now" idiom and
        has the same aggression-0 failure mode."""
        src = ('scn T\n\nbegin gamemode\n'
               '\tUngolimRef.ModDisposition player -100\nend\n')
        out = converter.convert_standalone('T', src, 'Quest', 'T')
        assert 'TES4Polyfill.ForceCombat(' in out


class TestJailIsNotExpulsion:
    """`IsPlayerInJail` (TES4 opcode 0x10AB) means "is the player serving a jail
    sentence".  All four spellings emitted
    TES4CyrodiilCrimeFaction.IsPlayerExpelled() — an unrelated question that is
    never true, since nothing expels the player from the synthesized crime
    faction.  Skyrim has the exact native: vanilla Actor.psc declares
    `bool Function IsArrested() native`, "Is this actor currently arrested?".

    TG00FindThievesGuildScript's stage 10 is the entry point of the whole
    Thieves Guild questline and was gated on this.
    """

    @pytest.mark.parametrize('spelling', [
        'IsPlayerInJail', 'GetPlayerInJail', 'IsPlayerInPrison', 'SentToJail',
    ])
    def test_maps_to_isarrested(self, converter, spelling):
        out = conv_expr(converter, spelling, 'Quest')
        assert out == 'Game.GetPlayer().IsArrested()'
        assert 'Expelled' not in out

    def test_does_not_register_a_crime_faction_property(self, converter):
        conv_expr(converter, 'IsPlayerInJail', 'Quest')
        assert 'TES4CyrodiilCrimeFaction' not in converter.get_property_refs()


class TestScriptAddTopicOpensTheGate:
    """A script `AddTopic X` is the THIRD reveal route for Oblivion's topic
    visibility model, alongside INFO fragments and quest stages.  It emitted an
    inert comment, so 19 gated topics lost that route — including TGGrayFox,
    whose reveal is reading the wanted poster / the mysterious note.
    """

    def test_gated_topic_emits_the_setvalue(self, converter):
        converter.topic_unlock_globals = {'tggrayfox': 'TES4Unlock_TGGrayFox'}
        out = conv_line(converter, 'AddTopic TGGrayFox', 'ObjectReference')
        assert out == 'TES4Unlock_TGGrayFox.SetValue(1)'
        assert converter.get_property_refs()['TES4Unlock_TGGrayFox'] \
            == 'GlobalVariable'

    def test_ungated_topic_stays_inert(self, converter):
        """An ungated topic is already visible and has no global to set."""
        converter.topic_unlock_globals = {}
        converter._line_comments = []
        out = conv_line(converter, 'AddTopic SomeUngatedTopic',
                                      'ObjectReference')
        assert 'SetValue' not in out
        assert 'TES4Unlock_' not in str(converter.get_property_refs())


class TestBareMenuModeRuns:
    """A BARE `begin MenuMode` (no menu id) is time-and-inventory bookkeeping
    that Oblivion runs on the frames where GameMode does NOT — wait/sleep and
    inventory.  Commenting it out deleted real logic: MelisandeScript's body
    holds the ONLY `set MS40.cureready to 1` in the plugin, so MS40's
    vampirism cure could never be handed over.  Menu-ID blocks (the MQ01
    stage-blowout case) must still stay inert.
    """

    BARE = """Scriptname TestBareMenu
short flag
begin gamemode
set flag to 1
End
begin MenuMode
set flag to 2
End
"""

    WITH_ID = """Scriptname TestMenuId
short flag
begin gamemode
set flag to 1
End
begin MenuMode 1014
set flag to 2
End
"""

    SLEEP = """Scriptname TestSleepMenu
short flag
begin gamemode
set flag to 1
End
begin MenuMode
if ( isPCSleeping == 1 )
    set flag to 2
endif
End
"""

    def test_bare_body_runs_in_the_update_loop(self, converter):
        out = converter.convert_standalone('T', self.BARE, 'Quest', 'T')
        body = out.split('Event OnUpdate()')[1].split('EndEvent')[0]
        assert 'flag = 2' in body, 'bare MenuMode body must execute'
        assert 'NOT executed' not in out

    def test_menu_id_body_stays_inert(self, converter):
        out = converter.convert_standalone('T', self.WITH_ID, 'Quest', 'T')
        assert 'NOT executed' in out
        body = out.split('Event OnUpdate()')[1].split('EndEvent')[0]
        assert 'flag = 2' not in body

    def test_sleep_idiom_still_routes_to_onsleepstart(self, converter):
        """The isPCSleeping exception must keep its own route, not be
        swallowed by the new bare-block merge."""
        out = converter.convert_standalone('T', self.SLEEP, 'Quest', 'T')
        assert 'TES4_MenuModeSleepBody' in out
        assert 'Event OnSleepStart' in out
        body = out.split('Event OnUpdate()')[1].split('EndEvent')[0]
        assert 'flag = 2' not in body


class TestIsPCAMurdererIsNotZero:
    """IsPCAMurderer takes NO arguments, so it is always read bare — the bare
    fallback returned the literal 0 and the real handler was unreachable dead
    code.  DarkBrotherhoodScript's site is the ONLY trigger for the entire
    Dark Brotherhood questline, so `If 0 == 1` meant it could never begin.
    """

    def test_bare_read_asks_the_crime_faction(self, converter):
        out = conv_line(converter, 'if IsPCAMurderer == 1', 'Quest')
        assert '0 == 1' not in out
        assert 'GetCrimeGoldViolent()' in out
        assert converter.get_property_refs()['TES4CyrodiilCrimeFaction'] \
            == 'Faction'

    def test_uses_the_murder_band_not_any_violence(self, converter):
        """`> 0` is R4-1's ASSAULT test — it would make the player a murderer
        for a bar brawl.  Murder is the 1000-gold band."""
        from script_convert.constants import TES4_MURDER_BOUNTY
        out = conv_line(converter, 'if IsPCAMurderer == 1', 'Quest')
        assert f'>= {TES4_MURDER_BOUNTY}' in out


class TestGetDetectionLevelIsDetection:
    """GetDetectionLevel has the same shape as GetDetected (opcode 0x10B4, 1
    Actor param, "Actor Reference" receiver) and every one of the plugin's 56
    sites is a threshold test (>=2, >=3, ==3) — never a numeric read.  It was
    a flat 0, killing all 7 of Dark04Execution's guard-aggro triggers among
    others.
    """

    def test_receiver_and_argument_swap(self, converter):
        """`Player` converts to Game.GetPlayer(); what matters is that the
        TARGET became the receiver and the OBSERVER the argument."""
        out = conv_line(converter, 
            'if GuardRef.GetDetectionLevel Player == 3', 'Quest')
        assert '.IsDetectedBy(GuardRef)' in out, \
            'observer/target must swap, as for GetDetected'
        assert 'GuardRef.IsDetectedBy' not in out

    def test_threshold_is_rescaled_to_the_tes4_range(self, converter):
        """`true as Int` is 1, so a raw Bool would make every `>= 2` / `>= 3`
        site permanently false — trading one dead form for another."""
        for op, num in (('==', 3), ('>=', 2), ('>=', 3)):
            out = conv_line(converter, 
                f'if GuardRef.GetDetectionLevel Player {op} {num}', 'Quest')
            assert '* 3)' in out, f'{op} {num} must be rescaled'

    def test_undetected_fails_every_threshold(self):
        """0 must fail ==3, >=2 and >=3; 3 must satisfy all three."""
        for detected, expected in ((False, False), (True, True)):
            val = (1 if detected else 0) * 3
            assert ((val == 3) is expected)
            assert ((val >= 2) is expected)
            assert ((val >= 3) is expected)


class TestPlaySoundPropertyIsNotQuoted:
    """Vanilla writes the EditorID quoted (`PlaySound "AMBBaenlinDeath"`).
    Registering the RAW argument kept the quotes, and _safe_property_name
    turned each into an underscore — declaring a second, never-referenced
    `Sound Property _X_ Auto` beside the real one (75 across 23 files).
    """

    def test_quoted_editorid_registers_the_stripped_name(self, converter):
        out = conv_line(converter, 'PlaySound "AMBBaenlinDeath"', 'Quest')
        props = converter.get_property_refs()
        assert 'AMBBaenlinDeath' in props
        assert not [p for p in props if p.startswith('_') and p.endswith('_')]
        assert 'AMBBaenlinDeath.Play(' in out

    def test_unquoted_editorid_still_works(self, converter):
        out = conv_line(converter, 'PlaySound AMBBaenlinMiss', 'Quest')
        assert 'AMBBaenlinMiss' in converter.get_property_refs()
        assert 'AMBBaenlinMiss.Play(' in out


class TestInferExtendsDoesNotBreakBinding:
    """Papyrus binds a script to a form only when the declared base type
    matches, so an `extends Actor` script on a WEAP/ACTI/CONT/DOOR is rejected
    outright ("Unable to bind script X because their base types do not match")
    and never runs.  `_infer_extends` upgraded 88 non-actor scripts that way;
    67 were logged as unbindable in-game.  Four distinct causes, one per test.
    """

    def test_objectreference_shared_call_does_not_upgrade(self):
        # `GetDistance` is declared on ObjectReference, not just Actor — it is
        # in `_OBJREF_SHARED_FUNCTIONS` for exactly this reason.  It upgraded
        # 101 scripts, `GoblinHeadScript` (on GoblinShamanStaff, a WEAP) among
        # them.
        src = 'scn X\n\nbegin gamemode\n\tif getdistance SomeMarker > 500\n\tendif\nend'
        assert ScriptConverter._infer_extends(src, 'ObjectReference') == 'ObjectReference'

    def test_comment_and_string_text_does_not_upgrade(self):
        # `DAMalacathStatueScript` ("...not kill them!"), `SE09AltarScript`
        # (";StartCombat to get the scene rolling"), `ICUmbacanoExitDoorScript`
        # ("; evp the post guards").
        for src in ('scn X\n\nbegin gamemode\n\tMessageBox "do not kill them!"\nend',
                    'scn X\n\nbegin gamemode\n\t;StartCombat to get it rolling\nend',
                    'scn X\n\nbegin gamemode\n\t; evp the post guards\nend'):
            assert ScriptConverter._infer_extends(src, 'ObjectReference') == 'ObjectReference'

    def test_local_named_like_an_actor_function_does_not_upgrade(self):
        # `MS05DreamworldAmuletScript` declares `short isEquipped`; reading or
        # assigning it is not a call.
        src = ('scn X\n\nshort isEquipped\n\nbegin gamemode\n'
               '\tif isEquipped == 1\n\tendif\nend')
        assert ScriptConverter._infer_extends(src, 'ObjectReference') == 'ObjectReference'

    def test_actor_event_body_does_not_upgrade(self):
        # `OnEquipped(Actor akActor)` supplies the subject itself, so an
        # actor-only call inside it says nothing about the script's own type —
        # the `MGBloodwormHelmScript*` helms ride on ARMO records.
        src = ('scn X\n\nBegin OnEquip Player\n\taddspell SomeSpell\nEnd\n'
               'Begin OnUnequip Player\n\tremovespell SomeSpell\nEnd')
        assert ScriptConverter._infer_extends(src, 'ObjectReference') == 'ObjectReference'

    def test_a_genuine_self_acting_actor_call_still_upgrades(self):
        # The upgrade must still fire for its real purpose: `SEShambles2`'s
        # bare `getdead`, `DAPeryiteIlvelScript`'s `setghost`.
        for src in ('scn X\n\nbegin gamemode\n\tif getdead == 1\n\tendif\nend',
                    'scn X\n\nbegin gamemode\n\tsetghost 1\nend'):
            assert ScriptConverter._infer_extends(src, 'ObjectReference') == 'Actor'


class TestBareActorCallUsesTheEventActor:
    """Inside an event that hands us the actor it is about, TES4's implicit
    subject for an actor-only call is THAT actor, not the item.  The helms'
    bare `addspell` is cast on the WEARER; `(Self as Actor)` on an ARMO is
    None, so the helm's whole effect was silently lost.
    """

    def test_bare_addspell_in_onequipped_targets_akactor(self, converter):
        converter._current_event = 'Event OnEquipped(Actor akActor)'
        out = conv_line(converter, 'addspell MG15BloodWormHelm25',
                                      'ObjectReference')
        assert out.strip().startswith('akActor.AddSpell(')

    def test_bare_call_outside_an_actor_event_still_casts_self(self, converter):
        converter._current_event = 'Event OnUpdate()'
        out = conv_line(converter, 'addspell MG15BloodWormHelm25',
                                      'ObjectReference')
        assert '(Self as Actor).AddSpell(' in out


class TestSharedScriptUsesTheCommonBaseType:
    """A script attached to BOTH an actor and a non-actor record cannot be
    `Actor` — Papyrus would refuse to bind the non-actor copies.  Oblivion puts
    `NoActivationScript` on a DOOR and an NPC_; scanning for the first actor
    attachment and returning early left every DOOR copy unbound, so the empty
    `OnActivate` that BLOCKS activation never ran on the doors.
    """

    def _graph(self, attachments):
        from script_convert.cross_ref import CrossRefGraph
        g = CrossRefGraph()
        g.script_formid_to_type['00000001'] = 0
        for i, sig in enumerate(attachments):
            rec = f'0000A{i:03d}'
            g.record_type[rec] = sig
            g.record_scri[rec] = '00000001'
        return g

    def test_actor_and_door_share_objectreference(self):
        g = self._graph(['NPC_', 'DOOR'])
        assert g.get_extends_class('00000001') == 'ObjectReference'

    def test_actor_only_still_extends_actor(self):
        g = self._graph(['NPC_', 'CREA'])
        assert g.get_extends_class('00000001') == 'Actor'

    def test_non_actor_only_stays_objectreference(self):
        g = self._graph(['DOOR', 'ACTI'])
        assert g.get_extends_class('00000001') == 'ObjectReference'


class TestGetCurrentAIPackageNumeric:
    """R9-1: `GetCurrentAIPackage == <n>` compared a package TYPE code.

    Skyrim's Actor.GetCurrentPackage() returns the Package form and neither
    vanilla Package.psc nor SKSE exposes its type, so the numeric comparison
    was flattened to the literal 0 — `If (0 == 5)` killed MG17's whole flee
    sequence.  The set of packages an actor can run is fixed at conversion
    time by its own AIPackage list, so the test is reconstructed as a
    disjunction over that actor's packages of the requested type.
    """

    def _graph(self):
        from script_convert.cross_ref import CrossRefGraph
        g = CrossRefGraph()
        # Two Wander (5) packages and one Travel (6) on one actor.
        for fid, edid, ptype in (('0000B001', 'WanderA', 5),
                                 ('0000B002', 'WanderB', 5),
                                 ('0000B003', 'TravelA', 6)):
            g.record_type[fid] = 'PACK'
            g.formid_to_edid[fid] = edid
            g.edid_to_formid[edid.lower()] = fid
            g.pack_type[fid] = ptype
        g.record_type['0000A001'] = 'NPC_'
        g.formid_to_edid['0000A001'] = 'Guard'
        g.edid_to_formid['guard'] = '0000A001'
        g.actor_packages['0000A001'] = ['0000B001', '0000B002', '0000B003']
        # A placed reference onto that base, to exercise the NAME chain.
        g.record_type['0000C001'] = 'ACHR'
        g.formid_to_edid['0000C001'] = 'GuardRef'
        g.edid_to_formid['guardref'] = '0000C001'
        g.record_base['0000C001'] = '0000A001'
        return g

    def test_wander_expands_to_the_actors_wander_packages(self):
        g = self._graph()
        assert g.get_actor_packages_of_type('Guard', 5) == ['WanderA', 'WanderB']

    def test_travel_picks_only_the_travel_package(self):
        g = self._graph()
        assert g.get_actor_packages_of_type('Guard', 6) == ['TravelA']

    def test_placed_reference_follows_the_name_chain(self):
        g = self._graph()
        assert g.get_actor_packages_of_type('GuardRef', 5) == ['WanderA', 'WanderB']

    def test_unknown_actor_returns_empty_so_caller_keeps_the_noop(self):
        g = self._graph()
        assert g.get_actor_packages_of_type('NoSuchActor', 5) == []
        assert g.get_actor_packages_of_type('Guard', 9) == []

    def test_bare_call_resolves_through_the_owning_script(self):
        g = self._graph()
        g.script_formid_to_edid['0000D001'] = 'GuardScript'
        g.record_scri['0000A001'] = '0000D001'
        assert g.get_script_owner_packages_of_type('GuardScript', 5) == [
            'WanderA', 'WanderB']

    def test_equality_emits_an_or_chain(self):
        conv = ScriptConverter(self._graph())
        out = conv.convert_standalone(
            'T', 'scn T\nbegin gamemode\nif Guard.GetCurrentAIPackage == 5\n'
            'set x to 1\nendif\nend', 'Quest', 'T')
        assert 'GetCurrentPackage() == WanderA' in out
        assert 'GetCurrentPackage() == WanderB' in out
        assert '||' in out
        assert '0 == 5' not in out

    def test_inequality_emits_an_and_chain(self):
        conv = ScriptConverter(self._graph())
        out = conv.convert_standalone(
            'T', 'scn T\nbegin gamemode\nif Guard.GetCurrentAIPackage != 5\n'
            'set x to 1\nendif\nend', 'Quest', 'T')
        assert 'GetCurrentPackage() != WanderA' in out
        assert 'GetCurrentPackage() != WanderB' in out
        assert '&&' in out

    def test_a_pack_editorid_comparand_still_converts_directly(self):
        conv = ScriptConverter(self._graph())
        out = conv.convert_standalone(
            'T', 'scn T\nbegin gamemode\nif Guard.GetCurrentAIPackage == WanderA\n'
            'set x to 1\nendif\nend', 'Quest', 'T')
        assert 'GetCurrentPackage() == WanderA' in out
        assert '||' not in out


class TestPlayerControlsShadow:
    """R9-2: GetPlayerControlsDisabled was the literal 0.

    Skyrim has both WRITERS as natives but no getter.  Flattening the read was
    not inert: MG18Script polls it to sequence Mannimarco's confrontation, so
    `== 1` was permanently false (he never spoke) while `== 0` was permanently
    true (he attacked at once).  The writers now shadow the state into the
    synthesized TES4ControlsDisabled global and the read returns it.
    """

    def test_read_returns_the_global(self, converter):
        out = converter.convert_standalone(
            'T', 'scn T\nbegin gamemode\nif GetPlayerControlsDisabled == 1\n'
            'set x to 1\nendif\nend', 'Quest', 'T')
        assert 'TES4ControlsDisabled.GetValue() == 1' in out
        assert '0 == 1' not in out

    def test_disable_writes_the_shadow(self, converter):
        out = converter.convert_standalone(
            'T', 'scn T\nbegin gamemode\nDisablePlayerControls\nend', 'Quest', 'T')
        assert 'Game.DisablePlayerControls()' in out
        assert 'TES4ControlsDisabled.SetValue(1)' in out

    def test_enable_clears_the_shadow(self, converter):
        out = converter.convert_standalone(
            'T', 'scn T\nbegin gamemode\nEnablePlayerControls\nend', 'Quest', 'T')
        assert 'Game.EnablePlayerControls()' in out
        assert 'TES4ControlsDisabled.SetValue(0)' in out

    def test_writer_declares_the_property_even_without_a_read(self, converter):
        # The only reader (MG18Script) is a DIFFERENT script from the two
        # writers, so the shadow must not be gated on a same-script read.
        out = converter.convert_standalone(
            'T', 'scn T\nbegin gamemode\nDisablePlayerControls\nend', 'Quest', 'T')
        assert 'GlobalVariable Property TES4ControlsDisabled Auto' in out

    def test_a_trailing_source_comment_does_not_strand_the_shadow(self, converter):
        out = converter.convert_standalone(
            'T', 'scn T\nbegin gamemode\nDisablePlayerControls ; cutscene\nend',
            'Quest', 'T')
        lines = [ln.strip() for ln in out.splitlines()]
        i = next(i for i, ln in enumerate(lines)
                 if ln.startswith('Game.DisablePlayerControls()'))
        assert lines[i + 1] == 'TES4ControlsDisabled.SetValue(1)'


# ===========================================================================
# Runtime game-setting writes (OBSE SetNumericGameSetting) and fall damage
#
# Skyrim has vanilla Papyrus GMST *readers* but no writer — SKSE's
# Game.SetGameSettingFloat does NOT compile against the vanilla headers this
# pipeline builds with (verified against papyrus.exe: "undefined function
# SetGameSettingFloat", while the getter resolves).  So the settings that have
# a per-actor equivalent go through Actor.ForceActorValue instead.
# ===========================================================================

class TestRuntimeGameSettingWrites:
    _SRC = ('scn T\nfloat orig\n'
            'begin scripteffectstart\n'
            '  set orig to GetGameSetting fJumpHeightMin\n'
            '  SetNumericGameSetting fJumpHeightMin 9000\n'
            'end\n')

    def test_write_becomes_an_actor_value(self, converter):
        out = converter.convert_standalone(
            'T', self._SRC, 'ActiveMagicEffect', 'T')
        assert 'akTarget.ForceActorValue("JumpingBonus", 9000)' in out
        # SKSE-only, does not compile against vanilla headers.
        assert 'SetGameSettingFloat' not in out

    def test_read_uses_the_same_channel_as_the_write(self, converter):
        """The save/restore pattern these scripts use ("remember the old
        value, set a new one, put it back") reads back a number the write
        never changed if the getter still goes to the global GMST."""
        out = converter.convert_standalone(
            'T', self._SRC, 'ActiveMagicEffect', 'T')
        assert 'akTarget.GetActorValue("JumpingBonus")' in out
        assert 'Game.GetGameSettingFloat("fJumpHeightMin")' not in out

    def test_a_setting_with_no_actor_value_keeps_a_visible_marker(self, converter):
        """A call that silently does nothing is the dangerous conversion; a
        marker is the healthy failure (docs/commentary/script_convert.md)."""
        out = converter.convert_standalone(
            'T', 'scn T\nbegin gamemode\nSetNumericGameSetting fNoSuchSetting 5\nend',
            'Quest', 'T')
        assert ';TODO' in out and 'fNoSuchSetting' in out


class TestResetFallDamageTimerIsPaired:
    """ResetFallDamageTimer applies a lasting actor value, so it MUST be undone
    when the effect ends.

    A no-op on one half of a paired on/off command is a latent soft-lock, not a
    cosmetic gap — here it would leave the actor permanently damage-resistant.
    """

    _SRC = ('scn T\n'
            'begin scripteffectupdate\n  ResetFallDamageTimer\nend\n'
            'begin scripteffectfinish\n  Return\nend\n')

    def test_suppression_is_emitted(self, converter):
        out = converter.convert_standalone(
            'T', self._SRC, 'ActiveMagicEffect', 'T')
        assert 'TES4Polyfill.SuppressFallDamage(' in out

    def test_restore_lands_in_the_teardown_event(self, converter):
        out = converter.convert_standalone(
            'T', self._SRC, 'ActiveMagicEffect', 'T')
        lines = [ln.strip() for ln in out.splitlines()]
        start = lines.index('Event OnEffectFinish(Actor akTarget, Actor akCaster)')
        end = lines.index('EndEvent', start)
        assert any('TES4Polyfill.RestoreFallDamage(akTarget)' in ln
                   for ln in lines[start:end])

    def test_restore_is_synthesized_when_there_is_no_teardown_block(self, converter):
        out = converter.convert_standalone(
            'T', 'scn T\nbegin scripteffectupdate\n  ResetFallDamageTimer\nend\n',
            'ActiveMagicEffect', 'T')
        assert 'Event OnEffectFinish(' in out
        assert 'TES4Polyfill.RestoreFallDamage(akTarget)' in out

    def test_the_flag_does_not_leak_between_scripts(self, converter):
        """The converter instance is reused across every SCPT in a job."""
        converter.convert_standalone('T', self._SRC, 'ActiveMagicEffect', 'T')
        other = converter.convert_standalone(
            'U', 'scn U\nbegin scripteffectfinish\n  Return\nend\n',
            'ActiveMagicEffect', 'U')
        assert 'RestoreFallDamage' not in other


class TestQuotedEditorIds:
    """Oblivion's parser accepts quotes around any EditorID, and Nehrim's
    authors use them constantly (173 sites).  Left in, the property sanitiser
    turned each quote into an underscore, so `SetStage "MQ01Tate" 20` produced
    the property `_MQ01Tate_` while the SAME script's unquoted
    `GetStage MQ01Tate` produced `MQ01Tate`.  Only the unquoted spelling
    matches an EditorID, so only it was bound in the VMAD — `_MQ01Tate_` stayed
    None and every `_MQ01Tate_.SetStage(...)` threw.  MQ01Tate was stranded at
    stage 15, never reaching the stage 40 that is the only thing starting MQ01,
    so MQ00 could never complete either.
    """

    @pytest.fixture
    def converter(self):
        return ScriptConverter(CrossRefGraph())

    def test_quoted_and_unquoted_name_the_same_property(self, converter):
        quoted = conv_line(converter, 'SetStage "MQ01Tate" 20', 'Quest')
        bare = conv_line(converter, 'SetStage MQ01Tate 20', 'Quest')
        assert quoted == bare == 'MQ01Tate.SetStage(20)'

    @pytest.mark.parametrize('line,expected', [
        ('if ( GetStage "MQ01Tate" == 15 )', 'If (MQ01Tate.GetStage() == 15)'),
        ('StartQuest "NQ05"', 'NQ05.Start()'),
        ('StopQuest "Charactergen"', 'Charactergen.Stop()'),
    ])
    def test_quest_commands_unquote(self, converter, line, expected):
        assert conv_line(converter, line, 'Quest') == expected

    def test_dotted_member_access_unquotes_both_sides(self, converter):
        # 1AlmanachDerBeschwoerungSCN: the assignment TARGET went through
        # _convert_ref (mangling the quotes) while the VALUE went through
        # _convert_expression (leaving them), emitting un-parseable Papyrus.
        out = conv_line(converter, 
            'Set "NQ16"."NQ16CountBooksVar" to "NQ16"."NQ16CountBooksVar" +1',
            'Quest')
        assert out == 'NQ16.NQ16CountBooksVar = NQ16.NQ16CountBooksVar + 1'
        assert '"' not in out

    @pytest.mark.parametrize('line', [
        'Message "Ihr habt den Erfolg verdient!"',
        'MessageBox "Ihr habt Punkte erhalten."',
    ])
    def test_real_string_literals_keep_their_quotes(self, converter, line):
        assert '"' in conv_line(converter, line, 'Quest')

    def test_safe_property_name_strips_wrapping_quotes(self):
        assert _safe_property_name('"MQ01Tate"') == _safe_property_name('MQ01Tate')


class TestPlayerBaseScriptRidesAQuestAlias:
    """A TES4 script on the player's BASE record (NPC_ 0x07) cannot run there
    in Skyrim: the acting player is PlayerRef 0x14 (signature PLYR, so a plugin
    cannot override it), whose base is Skyrim's OWN 0x07 — never the converted
    plugin's shifted copy.  Vanilla hosts player-side logic on a quest's
    PlayerRef reference alias (JailQuestPlayerScript, TutorialPlayerScript;
    71 Skyrim.esm quests force an alias to 0x14), so the script is emitted
    against that alias's base type.  Nehrim's GlobalplayerScript holds the whole
    XP economy AND the only `SetStage MQ00 1`, which starts the main quest.
    """

    _SRC = ('scn GlobalplayerScript\n'
            'short StartQuest\n'
            'begin gamemode\n'
            '  if ( StartQuest == 0 )\n'
            '    SetStage MQ00 1\n'
            '    set StartQuest to -1\n'
            '  endif\n'
            '  set foo to GetLevel\n'
            'end\n')

    @pytest.fixture
    def out(self):
        return ScriptConverter(CrossRefGraph()).convert_standalone(
            'GlobalplayerScript', self._SRC, PLAYER_ALIAS_EXTENDS,
            'GlobalplayerScript')

    def test_extends_reference_alias(self, out):
        assert out.splitlines()[0].startswith(
            f'ScriptName TES4_GlobalplayerScript extends {PLAYER_ALIAS_EXTENDS}')

    def test_the_stage_call_survives(self, out):
        assert 'MQ00.SetStage(1)' in out

    def test_no_self_as_actor_cast(self, out):
        """`Self` is the ReferenceAlias, so the cast the compiler rejects must
        never be emitted; the alias's filled reference is the subject."""
        assert 'Self as Actor' not in out
        assert 'GetActorReference().GetLevel()' in out

    def test_poll_is_not_load_gated(self, out):
        """The player is always loaded, so the update loop registers
        unconditionally — an Is3DLoaded() gate is for placed objects."""
        assert 'Is3DLoaded' not in out
        assert 'RegisterForSingleUpdate' in out


class TestPlayerIsNeverAScriptTypedProperty:
    """`player`/`playerref` is a converter keyword emitted as
    `Game.GetPlayer()`, never a bound property — even though the player's base
    NPC_ has EditorID "Player" and CAN carry a SCRI.  Typing it made every
    caller declare `TES4_GlobalplayerScript Property Player`, which then failed
    to convert to ObjectReference at each use (242 Nehrim scripts)."""

    def test_get_record_script_type_ignores_the_player(self):
        xref = CrossRefGraph()
        xref.edid_to_formid['player'] = '00000007'
        xref.record_scri['00000007'] = '00004E1A'
        xref.script_formid_to_edid['00004E1A'] = 'GlobalplayerScript'
        assert xref.get_record_script_type('Player') == ''
        assert xref.get_record_script_type('PlayerRef') == ''



class TestGetIsClassReadsTheActorBase:
    """GetPCIsClass/GetIsClass were absent from FUNCTION_MAP entirely, so the
    call survived untranslated and Papyrus parsed `GetPCIsClass
    CharactergenClass` as a bare name after a name — a syntax error that failed
    the WHOLE script.  Morroblivion's fbmwChargenQuestScript is the site, and
    the Chargen-and-Transport start menu imports it, so the compile failure
    took the Imperial City transport NPC down with it.

    Skyrim reads the class off the ActorBase (`ActorBase.GetClass()`); Actor has
    no GetClass() of its own.
    """

    def test_bare_player_read_converts(self, converter):
        out = conv_line(converter, 'if GetPCIsClass CharactergenClass', 'Quest')
        assert 'GetPCIsClass' not in out
        assert 'Game.GetPlayer().GetActorBase().GetClass() == CharactergenClass' in out

    def test_compared_form_converts(self, converter):
        out = conv_line(converter, 'if GetPCIsClass CharactergenClass == 0', 'Quest')
        assert 'GetPCIsClass' not in out
        assert 'GetActorBase().GetClass() == CharactergenClass' in out

    def test_explicit_ref_goes_through_the_actor_base(self, converter):
        out = conv_line(converter, 'if ActorRef.GetIsClass Warrior == 1', 'Quest')
        assert 'GetIsClass' not in out
        assert 'GetActorBase().GetClass() == Warrior' in out

    def test_argument_is_typed_class(self, converter):
        conv_line(converter, 'if GetPCIsClass CharactergenClass', 'Quest')
        assert converter.get_property_refs()['CharactergenClass'] == 'Class'


class TestSvConstructIsAStringLiteral:
    """sv_Construct is the one OBSE string command with an exact Papyrus
    equivalent — it builds a string_var from a literal, and Papyrus String IS
    that literal.  It fell through to the inert ar_/sv_ catch-all, so
    `quizQuestion = sv_Construct "..."` survived as an undefined identifier and
    failed the whole script: Morroblivion's fbmwChargenQuestScript (the class
    quiz), which the Chargen-and-Transport start menu imports.
    """

    def test_literal_passes_through(self, converter):
        out = conv_line(converter, 'set q to sv_Construct "Hello there."', 'Quest')
        assert 'sv_Construct' not in out
        assert '"Hello there."' in out

    def test_destruct_stays_a_no_op(self, converter):
        """Papyrus strings are garbage-collected — there is nothing to free."""
        out = conv_line(converter, 'set q to sv_Destruct', 'Quest')
        assert 'NE: sv_Destruct' in out


class TestMoveToBindsItsDestination:
    """MoveTo's destination is a PLACED REFERENCE that nothing else in the
    script necessarily declares, so the call has to register it as a property.

    `player.moveto` is a COMPOUND FUNCTION_MAP entry, so the `Player.`-prefixed
    form short-cut past the dedicated handler and emitted a bare identifier no
    property backed — the compiler then rejected the whole script.  A plain
    `ref.MoveTo` looked fine, which hid it.  Morroblivion's
    CATChargenAndTransport is the site (`Player.MoveTo CGPlayerStartMarker1`).
    """

    def test_player_prefixed_form_binds_the_target(self, converter):
        out = conv_line(converter, 'Player.MoveTo CGPlayerStartMarker1', 'Quest')
        assert out == 'Game.GetPlayer().MoveTo(CGPlayerStartMarker1)'
        assert converter.get_property_refs()['CGPlayerStartMarker1'] == 'ObjectReference'

    def test_explicit_ref_form_binds_the_target(self, converter):
        out = conv_line(converter, 'fbmwfargothref.moveto mwCGFargothStartMarker', 'Quest')
        assert converter.get_property_refs()['mwCGFargothStartMarker'] == 'ObjectReference'

    def test_space_separated_offsets_survive(self, converter):
        """Oblivion writes the offsets space-separated; comma-splitting glued
        them onto the target name and the call did not parse."""
        out = conv_line(converter, 'ref.MoveTo SomeMarker 0 100 0', 'Quest')
        assert out == 'ref.MoveTo(SomeMarker, 0, 100, 0)'

    def test_player_target_is_not_a_property(self, converter):
        """`player` is a converter keyword, never a bound property."""
        out = conv_line(converter, 'Player.MoveTo Player', 'Quest')
        assert out == 'Game.GetPlayer().MoveTo(Game.GetPlayer())'
        assert 'Player' not in converter.get_property_refs()


class TestTriggerEntryFires:
    """A converted `begin OnTrigger` must fire on the ENTRY frame too.

    Skyrim does not deliver OnTrigger for a fast crossing -- which is exactly
    what walking over a tripwire or pressure plate is -- so stepping on the
    Vilverin plate ran nothing at all.  Vanilla is unanimous: Tripwire.pex,
    PressurePlate.pex, TrapTriggerBase.pex and TrapTriggerHinge.pex ALL define
    OnTriggerEnter, and vanilla's Tripwire never defines OnTrigger.

    The body stays on OnTrigger (per-frame semantics: Nehrim's Magieverbot
    scripts count their own executions), and a generated OnTriggerEnter
    delegates to it.  BOTH are required -- see docs/commentary/script_convert.md.
    """

    SRC = """scn T
short triggered
begin onTrigger
  set triggered to 1
end
"""

    def test_body_stays_on_the_repeating_event(self, converter):
        out = converter.convert_standalone('T', self.SRC, 'ObjectReference', 'T')
        body = out.split('Event OnTrigger(')[1].split('EndEvent')[0]
        assert 'triggered = 1' in body

    def test_entry_event_is_emitted_and_delegates(self, converter):
        out = converter.convert_standalone('T', self.SRC, 'ObjectReference', 'T')
        assert 'Event OnTriggerEnter(ObjectReference akActionRef)' in out
        entry = out.split('Event OnTriggerEnter(')[1].split('EndEvent')[0]
        assert 'OnTrigger(akActionRef)' in entry

    def test_block_filter_survives_the_delegation(self, converter):
        """The filter guard lives in the OnTrigger body, so the entry path
        inherits it rather than running unfiltered."""
        src = "scn T\nshort x\nbegin onTrigger player\n  set x to 1\nend\n"
        out = converter.convert_standalone('T', src, 'ObjectReference', 'T')
        body = out.split('Event OnTrigger(')[1].split('EndEvent')[0]
        assert 'Game.GetPlayer()' in body

    def test_actor_and_mob_variants_also_get_entry(self, converter):
        for block in ('onTriggerActor', 'onTriggerMob'):
            src = f"scn T\nshort x\nbegin {block}\n  set x to 1\nend\n"
            out = converter.convert_standalone('T', src, 'ObjectReference', 'T')
            assert 'Event OnTriggerEnter(' in out, block

    def test_authored_entry_block_is_not_duplicated(self, converter):
        """Papyrus allows one definition per event, so a script that authors
        its own OnTriggerEnter must not also get a generated one."""
        src = ("scn T\nshort x\nbegin onTrigger\n  set x to 1\nend\n"
               "begin onTriggerEnter\n  set x to 2\nend\n")
        out = converter.convert_standalone('T', src, 'ObjectReference', 'T')
        assert out.count('Event OnTriggerEnter(') == 1


class TestPhysicalTrapDamage:
    """A physical trap's damage lives in TES4's ENGINE, not in its script.

    When an OL_TRAP (layer 14) body struck an actor, Oblivion read the magic
    variables fTrapDamage / fLevelledDamage / fTrapPushBack off the striking
    object's script and applied `fTrapDamage + fLevelledDamage * level` plus
    pushback.  Nothing in the script body says so, which is why converted
    swinging maces and logs connected but dealt ZERO damage.

    Skyrim keeps the layer-14 contact detection but dispatches it as
    OnTrapHitStart and leaves the damage to the script (vanilla
    TrapHitBase.psc -> native ProcessTrapHit).  In-game confirmed 2026-08-09;
    see docs/commentary/script_convert.md.
    """

    # CTrapSwingMace01SCRIPT's shape: armed at 0, 20 on release, 5 after 6s.
    SRC = """scn T
short triggered
float fTrapDamage
float fTrapPushBack
float fLevelledDamage
begin onActivate
  set fTrapDamage to 20
  set fTrapPushBack to 900
  set fLevelledDamage to 1.5
  set triggered to 1
end
"""

    def _handler(self, converter, src=None, extends='ObjectReference'):
        out = converter.convert_standalone('T', src or self.SRC, extends, 'T')
        assert 'Event OnTrapHitStart(' in out, out
        return out.split('Event OnTrapHitStart(')[1].split('EndEvent')[0]

    def test_handler_applies_levelled_damage_via_processtraphit(self, converter):
        body = self._handler(converter)
        assert 'fTrapDamage + fLevelledDamage * victim.GetLevel()' in body
        assert '.ProcessTrapHit(Self, totalDamage, fTrapPushBack,' in body

    def test_variables_are_read_live_not_baked(self, converter):
        """The authored lifecycle (0 while held -> 20 -> 5) only survives if
        the handler reads the properties at hit time.  Baking the literals in
        would arm the trap permanently and ignore the decay."""
        body = self._handler(converter)
        assert '20' not in body and '1.5' not in body, \
            'damage numbers must come from the live properties, not literals'

    def test_unarmed_trap_deals_nothing(self, converter):
        """TES4 leaves the variables at 0 until the trap fires, so brushing a
        held mace must be harmless."""
        body = self._handler(converter)
        assert 'totalDamage <= 0.0' in body
        guard = body.split('totalDamage <= 0.0')[1].split('EndIf')[0]
        assert 'Return' in guard

    def test_non_actor_hits_are_ignored(self, converter):
        body = self._handler(converter)
        assert 'akTarget as Actor' in body
        assert 'victim == None' in body

    def test_flat_only_trap_omits_the_level_term(self, converter):
        """A script declaring fTrapDamage alone must not reference variables
        it never declared -- that would not compile."""
        src = ("scn T\nfloat fTrapDamage\nbegin onActivate\n"
               "  set fTrapDamage to 10\nend\n")
        body = self._handler(converter, src)
        assert 'fLevelledDamage' not in body
        assert 'fTrapPushBack' not in body
        assert 'Float totalDamage = fTrapDamage' in body

    def test_scripts_without_trap_variables_get_no_handler(self, converter):
        src = "scn T\nshort x\nbegin onActivate\n  set x to 1\nend\n"
        out = converter.convert_standalone('T', src, 'ObjectReference', 'T')
        assert 'OnTrapHitStart' not in out

    def test_quest_scripts_get_no_handler(self, converter):
        """OnTrapHitStart is an ObjectReference event; emitting it on a Quest
        script would not compile."""
        out = converter.convert_standalone('T', self.SRC, 'Quest', 'T')
        assert 'OnTrapHitStart' not in out


class TestDestroyDoesNotCancelTheClip:
    """SetDestroyed(1) must not tear down the animation started just above it.

    TES4 pairs `playgroup <grp>` with `setDestroyed 1` on the next line
    (CTrigTripwire01SCRIPT, CTrapLogs01SCRIPT, CTrapCaveIn01SCRIPT).  In
    Oblivion that was harmless -- with no destruction data it only blocked
    re-activation, and Oblivion ships ZERO DEST subrecords.  Skyrim's
    SetDestroyed still RESETS THE REFERENCE'S 3D, killing the
    NiControllerSequence before a frame drew.
    """

    def test_destroy_after_animation_is_deferred(self, converter):
        src = ("scn T\nbegin onActivate\n"
               "  playgroup forward 0\n  setDestroyed 1\nend\n")
        out = converter.convert_standalone('T', src, 'ObjectReference', 'T')
        assert 'TES4Polyfill.DestroyAfterAnimation(Self, TES4DestroyedRefs)' in out
        assert 'TES4Polyfill.SetDestroyed(Self, TES4DestroyedRefs, true)' not in out

    def test_unrelated_destroy_is_left_alone(self, converter):
        """Only the object that was just animated is at risk."""
        src = "scn T\nbegin onActivate\n  setDestroyed 1\nend\n"
        out = converter.convert_standalone('T', src, 'ObjectReference', 'T')
        assert 'DestroyAfterAnimation' not in out

    def test_setdestroyed_zero_is_never_deferred(self, converter):
        """OnReset re-arms the trap; deferring that would be wrong."""
        src = ("scn T\nbegin onActivate\n  playgroup forward 0\nend\n"
               "begin onReset\n  setDestroyed 0\nend\n")
        out = converter.convert_standalone('T', src, 'ObjectReference', 'T')
        assert 'TES4Polyfill.SetDestroyed(Self, TES4DestroyedRefs, false)' in out


class TestGetDestroyedReadsWhatSetDestroyedWrote:
    """TES4's destroyed flag must survive the round trip.

    Skyrim kept ObjectReference.SetDestroyed but ships NO reader for the flag,
    and GetCurrentDestructionStage() reads the unrelated DEST stage system that
    this conversion never writes -- so every `getdestroyed` used to be a read
    that could not become true.  MS48OblivionGateScript's ONLY `setstage ms48
    50` is gated on `getdestroyed == 1`, so the Kvatch quest pinned at stage 10
    after the gate was closed (measured in game 2026-08-27).  Both halves now
    go through the polyfill's TES4DestroyedRefs FormList.
    """

    def test_read_and_write_share_the_formlist(self, converter):
        src = ("scn T\nbegin gamemode\n"
               "  if getdestroyed == 1\n    setDestroyed 0\n"
               "  endif\nend\n")
        out = converter.convert_standalone('T', src, 'ObjectReference', 'T')
        assert 'TES4Polyfill.GetDestroyed(Self, TES4DestroyedRefs)' in out
        assert 'TES4Polyfill.SetDestroyed(Self, TES4DestroyedRefs, false)' in out
        # The dead reads that caused the bug must not come back.
        assert 'GetCurrentDestructionStage' not in out
        assert converter.get_property_refs()['TES4DestroyedRefs'] == 'FormList'

    def test_ref_prefixed_read_uses_the_polyfill(self, converter):
        src = ("scn T\nbegin gamemode\n"
               "  if MS48OblivionGate.getdestroyed == 1\n"
               "    setstage MS48 50\n  endif\nend\n")
        out = converter.convert_standalone('T', src, 'ObjectReference', 'T')
        assert 'TES4Polyfill.GetDestroyed(MS48OblivionGate, TES4DestroyedRefs)' in out
        assert 'GetCurrentDestructionStage' not in out

    def test_effect_setdestroyed_targets_the_affected_actor(self, converter):
        """ActiveMagicEffect Self is the effect object, not the TES4 subject."""
        src = ("scn T\nbegin ScriptEffectStart\n"
               "  setDestroyed 1\nend\n")
        out = converter.convert_standalone(
            'T', src, 'ActiveMagicEffect', 'T')
        assert ('TES4Polyfill.SetDestroyed(GetTargetActor(), '
                'TES4DestroyedRefs, true)') in out

    def test_gate_close_marks_the_gate_destroyed(self, converter):
        """The engine call that closes a gate feeds the same FormList, which is
        what lets the gate's own `getdestroyed` poll advance the quest."""
        src = ("scn T\nbegin onActivate\n"
               "  CloseCurrentOblivionGate\nend\n")
        out = converter.convert_standalone('T', src, 'ObjectReference', 'T')
        assert 'TES4Polyfill.CloseCurrentOblivionGate(TES4DestroyedRefs)' in out


class TestDisablingAGateStillAdvancesTheQuest:
    """Closing a gate must remove it AND advance the quest.

    Removing a closed gate needs Disable() (SetDestroyed only makes it
    non-interactable), but MS48 and MS94 open their poll with
    `if getdisabled == 1 / return` ABOVE the `getdestroyed` setstage. In
    Oblivion those are independent bits and closing set only destroyed, so the
    preamble never fired for a closed gate. Disabling ours would strand the
    setstage forever -- the measured MS48-at-stage-10 defect. The polyfill
    keeps them independent: a DESTROYED ref never reports as disabled.
    """

    SRC = ("scn T\nbegin gamemode\n"
           "  if getdisabled == 1\n    return\n  endif\n"
           "  if getdestroyed == 1 && getstage MS48 < 50\n"
           "    setstage MS48 50\n  endif\nend\n")

    def test_getdisabled_routes_through_the_polyfill(self, converter):
        out = converter.convert_standalone('T', self.SRC, 'ObjectReference', 'T')
        assert 'TES4Polyfill.GetDisabled(Self, TES4DestroyedRefs)' in out
        # The bare native would let a disabled-but-destroyed gate short-circuit
        # the setstage below it.
        assert 'If IsDisabled()' not in out

    def test_the_destroyed_branch_is_still_reachable(self, converter):
        out = converter.convert_standalone('T', self.SRC, 'ObjectReference', 'T')
        lines = [l.strip() for l in out.splitlines()]
        dis = next(i for i, l in enumerate(lines) if 'GetDisabled(' in l)
        des = next(i for i, l in enumerate(lines) if 'GetDestroyed(' in l)
        # Preamble still comes first (faithful), but it now reads False for a
        # destroyed gate, so the setstage below it can run.
        assert dis < des
        assert 'MS48.SetStage(50)' in out or 'ms48.SetStage(50)' in out


class TestBaseItemPropertiesKeepTheirRecordType:
    """An attached TES4 script must not retype a BASE-OBJECT property.

    TES4 attaches scripts to base items freely (mwCWUItemScript rides 195 of
    Morroblivion's clothing records). The converter preferred that script class
    over the record class so cross-script property reads would work -- but the
    VM refuses to bind an `extends ObjectReference` script class to a base
    record, and the property then reads None. From the game's Papyrus log:

        Property fbmwEngravedRingofHealing on script TES4_TIF__013236A5 ...
          cannot be bound because (1B001677) is not the right type
        error: Cannot add None to a container
          [ (00000014)].Actor.RemoveItem() - "<native>"

    So `player.removeitem fbmwEngravedRingofHealing 1` no-oped and the ring
    stayed in the player's inventory after being handed to Fargoth, while the
    quest still advanced (native errors are non-fatal).
    """

    @staticmethod
    def _xref_with_scripted_item(rtype: str):
        x = CrossRefGraph()
        x.formid_to_edid['01001677'] = 'fbmwRing'
        x.edid_to_formid['fbmwring'] = '01001677'
        x.record_type['01001677'] = rtype
        x.record_scri['01001677'] = '01000AAA'
        x.script_formid_to_edid['01000AAA'] = 'mwCWUItemScript'
        return x

    def test_clot_item_property_is_armor_not_the_script_class(self):
        conv = ScriptConverter(self._xref_with_scripted_item('CLOT'))
        src = "scn T\nbegin onActivate\n  player.removeitem fbmwRing 1\nend\n"
        out = conv.convert_standalone('T', src, 'ObjectReference', 'T')
        assert 'Armor Property fbmwRing' in out
        assert 'TES4_mwCWUItemScript Property fbmwRing' not in out

    def test_reference_types_still_take_the_script_class(self):
        """The cross-script access this preference exists for must survive."""
        conv = ScriptConverter(self._xref_with_scripted_item('CONT'))
        src = "scn T\nbegin onActivate\n  player.removeitem fbmwRing 1\nend\n"
        out = conv.convert_standalone('T', src, 'ObjectReference', 'T')
        assert 'TES4_mwCWUItemScript Property fbmwRing' in out


class TestGetInCellSplitsInteriorFromExterior:
    """`GetInCell` must not declare a Cell property for an EXTERIOR cell.

    A Papyrus `Cell` property binds only to an interior -- every one of the 43
    Cell properties in vanilla Skyrim's own scripts names an interior, and none
    names an exterior. Declaring one for an exterior grid cell produced, at
    runtime, "cannot be bound because (...) is not the right type", and the
    property then read None. Measured in one session: 773 such failures, and in
    MS08BoatScript the split was exact -- 44 interior bound, 41 exterior failed.

    Exteriors are still part of the TES4 prefix match, so they must not simply
    be dropped: they are matched by worldspace + grid coordinates instead.
    """

    @staticmethod
    def _xref():
        x = CrossRefGraph()
        # One interior and one exterior in the same prefix family.
        for fid, edid, sig in (('01000001', 'BravilCastleBarracks', 'CELL'),
                               ('01000002', 'BravilBeach01', 'CELL'),
                               ('0100003C', 'Tamriel', 'WRLD')):
            x.formid_to_edid[fid] = edid
            x.edid_to_formid[edid.lower()] = fid
            x.record_type[fid] = sig
        x.cell_geom['01000001'] = (True, '', None, None)
        x.cell_geom['01000002'] = (False, '0100003C', 17, -12)
        return x

    def test_split_separates_the_two(self):
        interior, exterior = self._xref().split_cell_family('Bravil')
        assert interior == ['BravilCastleBarracks']
        assert exterior == [('Tamriel', 17, -12)]

    def test_exterior_gets_no_cell_property(self):
        conv = ScriptConverter(self._xref())
        src = ("scn T\nbegin GameMode\n"
               "  if player.GetInCell Bravil == 1\n"
               "    set x to 1\n  endif\nend\n")
        out = conv.convert_standalone('T', src, 'Quest', 'T')
        out += '\n'.join(conv.get_cell_family_helpers())
        assert 'Cell Property BravilCastleBarracks' in out
        assert 'Cell Property BravilBeach01' not in out

    def test_exterior_is_matched_by_grid_instead(self):
        conv = ScriptConverter(self._xref())
        src = ("scn T\nbegin GameMode\n"
               "  if player.GetInCell Bravil == 1\n"
               "    set x to 1\n  endif\nend\n")
        out = conv.convert_standalone('T', src, 'Quest', 'T')
        helpers = '\n'.join(conv.get_cell_family_helpers())
        # The worldspace must be a declared property, not just cited: the
        # helpers are emitted AFTER the declarations, so registering the ref
        # while emitting them would leave an undefined identifier.
        assert 'WorldSpace Property Tamriel' in out
        assert 'TES4_gx == 17' in helpers and 'TES4_gy == -12' in helpers


class TestWeatherFunctions:
    """Scripted weather drives the REAL converted records now.

    The old stubs (';NE: ... weather not converted') existed because the CLMT
    chain was gated off; with WTHR/CLMT/REGN converted, the Oblivion-gate
    storm scripts must force the converted OblivionStormTamriel.  Signatures
    verified against references/skse64-master/scripts/vanilla/Weather.psc.
    """

    def _convert(self, body):
        conv = ScriptConverter(CrossRefGraph())
        src = f"scn T\nbegin GameMode\n{body}\nend\n"
        return conv.convert_standalone('T', src, 'Quest', 'T')

    def test_forceweather_is_instant_but_never_engine_locked(self):
        """abOverride must be False: Oblivion holds scripted weather by
        re-applying it every GameMode pass, not by an engine lock.  Mapping
        to True let a fast-travel away from an Oblivion gate strand
        OblivionStormTamriel over the whole world forever — the release call
        lives in the same unloaded script's update loop."""
        out = self._convert('  forceweather OblivionStormTamriel 1')
        assert 'OblivionStormTamriel.ForceActive(False)' in out
        assert 'Weather Property OblivionStormTamriel Auto' in out
        assert ';NE:' not in out

    def test_setweather_transitions_naturally_without_lock(self):
        out = self._convert('  setweather OblivionStormTamriel 1')
        assert 'OblivionStormTamriel.SetActive(False, False)' in out

    def test_release_weather_override(self):
        out = self._convert('  ReleaseWeatherOverride')
        assert 'Weather.ReleaseOverride()' in out

    def test_get_is_current_weather_compares_converted_record(self):
        out = self._convert('  if getiscurrentweather OblivionStormTamriel == 0\n'
                            '    set x to 1\n  endif')
        assert 'Weather.GetCurrentWeather() == OblivionStormTamriel' in out
        assert 'Weather Property OblivionStormTamriel Auto' in out


class TestObjRefSharedFunctionsNeverCastToActor:
    """A bare TES4 call to a function that exists on ObjectReference must NOT
    become `(Self as Actor).F()`.

    `(Self as Actor)` on a non-actor reference is **None** at runtime, so the
    call aborts — and Papyrus substitutes 0 for the aborted result rather than
    stopping the script.  That silently INVERTS distance guards: MS48Oblivion-
    GateScript (an ACTI) has TES4 `if getdistance player < 1000`, which became
    `If (Self as Actor).GetDistance(Player) < 1000` -> `0 < 1000` -> always
    true, so the Oblivion gate called `OblivionStormTamriel.ForceActive()`
    every 0.1s while the player transitioned worldspaces
    (crash-2026-08-09-23-34-53, "Cannot call getDistance() on a None object"
    x34 in Papyrus.0.log immediately before the CTD).

    `_ACTOR_ONLY_FUNCTIONS` and `_OBJREF_SHARED_FUNCTIONS` deliberately
    overlap; every site that consults the first must subtract the second.
    """

    def _convert(self, body, extends='ObjectReference'):
        conv = ScriptConverter(CrossRefGraph())
        src = f"scn T\nbegin GameMode\n{body}\nend\n"
        return conv.convert_standalone('T', src, extends, 'T')

    def test_bare_getdistance_is_not_cast_to_actor(self):
        out = self._convert('  if getdistance player < 1000\n'
                            '    set x to 1\n  endif')
        assert '(Self as Actor).GetDistance' not in out, \
            'cast yields None at runtime -> aborted call -> 0 -> guard inverts'
        assert 'GetDistance(' in out

    def test_every_objref_shared_function_stays_uncast(self):
        """The invariant across the whole overlap, not just getdistance."""
        from script_convert.constants import (
            _ACTOR_ONLY_FUNCTIONS, _OBJREF_SHARED_FUNCTIONS)
        overlap = sorted(_ACTOR_ONLY_FUNCTIONS & _OBJREF_SHARED_FUNCTIONS)
        assert overlap, 'fixture expects the two sets to overlap'
        for fn in overlap:
            out = self._convert(f'  {fn}')
            assert f'(Self as Actor).{fn}' not in out.lower(), \
                f'{fn} is valid on ObjectReference and must not be cast'

    def test_shared_function_on_topicinfo_gets_a_receiver_not_a_bare_call(self):
        """Removing the bogus `as Actor` must not leave the call receiverless.

        TopicInfo/ActiveMagicEffect have no implicit reference, so a bare
        `AddItem(...)` is `undefined function` at compile time — and an
        uncompilable script takes every script naming its type down with it.
        Route the receiver instead; just don't cast it to Actor.
        """
        conv = ScriptConverter(CrossRefGraph())
        src = "scn T\nbegin GameMode\n  additem gold001 5\nend\n"
        out = conv.convert_standalone('T', src, 'TopicInfo', 'T')
        add = [l.strip() for l in out.splitlines() if 'AddItem' in l]
        assert add, 'AddItem was dropped entirely'
        for line in add:
            assert not line.startswith('AddItem('), \
                f'bare receiverless call will not compile: {line}'
            assert '(Self as Actor).AddItem' not in line, \
                'must not reintroduce the None-yielding cast'


class TestInfoFragmentSkipping:
    """Only INFOs whose fragment DOES something get one.

    The engine BINDS an INFO's fragment script when it selects that line --
    loading and linking the .pex before anything is spoken -- so a fragment
    with no behaviour is a cost paid on the dialogue path itself.  Every INFO
    used to get one (19,278 .pex against vanilla Skyrim's ~5,500).
    """

    def _emit(self, tmp_path, rec, say_topics=(), info_reveals=None,
              service_topics=None):
        from script_convert import pipeline
        from script_convert.converter import ScriptConverter
        from script_convert.cross_ref import CrossRefGraph
        saved = ScriptConverter.say_topics
        ScriptConverter.say_topics = set(say_topics)
        stats = pipeline._new_stats()
        try:
            pipeline._info_batch([rec], str(tmp_path), CrossRefGraph(), stats,
                                 info_reveals or {}, service_topics or {})
        finally:
            ScriptConverter.say_topics = saved
        assert not stats['errors'], stats['errors']
        return (tmp_path / f"TES4_TIF__{rec['FormID']}.psc").exists()

    def test_plain_player_line_gets_no_fragment(self, tmp_path):
        """A menu line with no result script needs no fragment: the player
        picked it, so no SayLine is waiting on Begin/End timing."""
        assert not self._emit(tmp_path,
                              {'FormID': '00001111', 'ParentDIAL': '000000AA'})

    def test_script_driven_topic_keeps_its_timing_fragment(self, tmp_path):
        """SayLine blocks until OnBegin reports the line started, so a topic a
        script drives via Say/SayTo MUST keep its fragment."""
        assert self._emit(tmp_path,
                          {'FormID': '00002222', 'ParentDIAL': '000000AA'},
                          say_topics={'000000AA'})

    def test_result_script_keeps_its_fragment(self, tmp_path):
        assert self._emit(tmp_path, {'FormID': '00003333',
                                     'ParentDIAL': '000000BB',
                                     'ResultScript': 'set MyQuest.x to 1'})

    def test_comment_only_result_script_is_not_a_reason(self, tmp_path):
        """A result script of nothing but comments produces no code, so the
        fragment would be empty."""
        assert not self._emit(tmp_path, {'FormID': '00004444',
                                         'ParentDIAL': '000000BB',
                                         'ResultScript': '; nothing here\n'})

    def test_unlock_revealer_keeps_its_fragment(self, tmp_path):
        assert self._emit(tmp_path,
                          {'FormID': '00005555', 'ParentDIAL': '000000BB'},
                          info_reveals={0x005555: ['TES4Unlock_Topic']})

    def test_service_topic_keeps_its_fragment(self, tmp_path):
        assert self._emit(tmp_path,
                          {'FormID': '00006666', 'ParentDIAL': '000000CC'},
                          service_topics={'000000CC': 'barter'})

    def test_emitter_and_importer_agree(self):
        """🛑 The .pex emitter and the VMAD writer must never disagree: a flag
        bit with no function behind it makes the engine bind a missing
        function.  Both call info_needs_fragment, so assert it is decisive for
        the same record either side asks about."""
        from script_convert.pipeline import info_needs_fragment
        from script_convert.converter import ScriptConverter
        saved = ScriptConverter.say_topics
        ScriptConverter.say_topics = {'000000AA'}
        try:
            driven = {'FormID': '00007777', 'ParentDIAL': '000000AA'}
            plain = {'FormID': '00008888', 'ParentDIAL': '000000FF'}
            assert info_needs_fragment(driven) is True
            assert info_needs_fragment(plain) is False
        finally:
            ScriptConverter.say_topics = saved


class TestObjectReferenceMethodsDoNotPromoteToActor:
    """A method declared on ObjectReference must never retype its receiver.

    The Imperial City Arena softlocked because `Say` promoted its receiver to
    `Actor`.  Its four announcer speakers (ArenaMatchPlayerRef,
    ArenaGalleryMarkerRef, ICArenaPlayerMarkerRef, ICMonsterFightPlayerRef) are
    XMarker **STAT** refs, so `Actor Property` refused to bind, the property was
    None, and the first call on it aborted the whole announcer function.
    See docs/commentary/script_convert.md.
    """

    def test_say_does_not_promote_receiver(self, converter):
        result = conv_line(converter, 
            'ArenaMatchPlayerRef.Say Announcer 1 ArenaMouth 1', 'Quest')
        assert 'ArenaMatchPlayerRef.Say(' in result
        assert converter._property_refs.get('ArenaMatchPlayerRef') != 'Actor'

    def test_say_keeps_existing_objectreference_type(self, converter):
        converter._property_refs['ArenaGalleryMarkerRef'] = 'ObjectReference'
        conv_line(converter, 'ArenaGalleryMarkerRef.Say Announcer', 'Quest')
        assert converter._property_refs['ArenaGalleryMarkerRef'] == 'ObjectReference'

    def test_cast_source_does_not_promote(self, converter):
        result = conv_line(converter, 
            'SEHaskillSummonMarker.Cast SummonSpell Player', 'Quest')
        assert '.Cast(SEHaskillSummonMarker' in result
        assert converter._property_refs.get('SEHaskillSummonMarker') != 'Actor'

    def test_pms_subject_does_not_promote(self, converter):
        result = conv_line(converter, 
            'SEXedPuzStatue1.pms effectSoulTrap', 'Quest')
        assert '.Play(SEXedPuzStatue1' in result
        assert converter._property_refs.get('SEXedPuzStatue1') != 'Actor'

    def test_getangle_does_not_promote(self, converter):
        result = conv_line(converter, 
            'set x to SEXedPuzStatue2.GetAngle Z', 'Quest')
        assert 'SEXedPuzStatue2.GetAngleZ()' in result
        assert converter._property_refs.get('SEXedPuzStatue2') != 'Actor'

    def test_moveto_does_not_promote_subject(self, converter):
        result = conv_line(converter, 
            'SEHaskillSummonMarker.MoveTo SEHaskillSummonReturnMarker', 'Quest')
        assert 'SEHaskillSummonMarker.MoveTo(' in result
        assert converter._property_refs.get('SEHaskillSummonMarker') != 'Actor'

    def test_actor_only_call_still_promotes(self, converter):
        """The guard must not disarm genuine Actor-only promotion."""
        conv_line(converter, 'SomeGuardRef.EVP', 'Quest')
        assert converter._property_refs.get('SomeGuardRef') == 'Actor'


class TestQuestStartDoesNotClobberSeededWrites:
    """`Quest.Start()` must not wipe property writes the author seeded first.

    TES4 quest variables persist across StartQuest, so "seed then start" is a
    common authored idiom.  TES5 `Quest.Start()` on a stopped quest re-inits
    its scripts and resets every Auto property, silently erasing the seed.
    This softlocked the Imperial City Arena: `Arena.ReadyMatch = 1` then
    `Arena.Start()` left ReadyMatch at 0, so the announcer never fired.
    See docs/commentary/script_convert.md.
    """

    def _hoist(self, converter, body):
        # `converter` is unused: the hoist moved to `tes5.blocks` when the
        # `__new__(ScriptConverter)` hack that called it from the pipeline was
        # deleted -- it never touched instance state, only three class-level
        # regexes.  The parameter stays so the cases below read unchanged.
        return hoist_quest_start_above_writes(body)

    def test_start_hoisted_above_its_own_writes(self, converter):
        out = self._hoist(converter, [
            '  Arena.ReadyMatch = 1',
            '  Arena.ChorrolMatch = 1',
            '  Arena.Start()',
        ])
        assert out.index('  Arena.Start()') == 0
        assert out[1:] == ['  Arena.ReadyMatch = 1', '  Arena.ChorrolMatch = 1']

    def test_writes_after_start_are_left_alone(self, converter):
        body = ['  Arena.Start()', '  Arena.ReadyMatch = 1']
        assert self._hoist(converter, body) == body

    def test_other_quests_writes_are_not_a_barrier(self, converter):
        out = self._hoist(converter, [
            '  Arena.ReadyMatch = 1',
            '  Other.Something = 2',
            '  Arena.Start()',
        ])
        assert out[0] == '  Arena.Start()'

    def test_unrelated_statement_before_write_is_preserved(self, converter):
        out = self._hoist(converter, [
            '  Game.GetPlayer().RemoveItem(Gold001, 25)',
            '  Spec.BetAmount = 1',
            '  Spec.Start()',
        ])
        assert out[0] == '  Game.GetPlayer().RemoveItem(Gold001, 25)'
        assert out[1] == '  Spec.Start()'
        assert out[2] == '  Spec.BetAmount = 1'

    def test_branch_between_blocks_the_hoist(self, converter):
        body = [
            '  Arena.ReadyMatch = 1',
            '  If (x == 1)',
            '  Arena.Start()',
            '  EndIf',
        ]
        assert self._hoist(converter, body) == body

    def test_start_with_no_preceding_write_is_untouched(self, converter):
        body = ['  DoThing()', '  Arena.Start()']
        assert self._hoist(converter, body) == body

    def test_comparison_is_not_mistaken_for_a_write(self, converter):
        body = ['  If Arena.ReadyMatch == 1', '  Arena.Start()']
        assert self._hoist(converter, body) == body


# ===========================================================================
# TES4 lexer  (script_convert/tes4/lexer.py)
# ===========================================================================

class TestTes4Lexer:
    """The lexer is the foundation of the parse tree: if it loses a token, the
    parser cannot rebuild the statement, so every case here is about NOT
    dropping input rather than about producing a pretty token list."""

    def _kinds(self, src):
        return [(t.kind.name, t.text) for t in tokenize(src)
                if t.kind is not T.EOF]

    def test_trailing_comment_is_kept_as_a_token(self):
        toks = tokenize('short done\t\t; set to 1 when Ob 13 turned on')
        assert toks[-2].kind is T.COMMENT
        assert toks[-2].text == '; set to 1 when Ob 13 turned on'

    def test_comment_mid_expression_does_not_eat_following_lines(self):
        # The whole reason the tree exists: a comment ends at the newline, so
        # a following statement is still tokenised.
        toks = tokenize('if x == 1  ; why\nset y to 2')
        assert any(t.kind is T.IDENT and t.text == 'set' for t in toks)

    def test_member_dot_is_an_operator_not_a_number(self):
        assert self._kinds('BaurusRef.getdisposition player') == [
            ('IDENT', 'BaurusRef'), ('OP', '.'),
            ('IDENT', 'getdisposition'), ('IDENT', 'player')]

    def test_leading_dot_number_is_a_number(self):
        assert self._kinds('set x to .5') == [
            ('IDENT', 'set'), ('IDENT', 'x'), ('IDENT', 'to'), ('NUMBER', '.5')]

    def test_two_char_operators_beat_one_char(self):
        assert self._kinds('if a <= 1 && b != 2') == [
            ('IDENT', 'if'), ('IDENT', 'a'), ('OP', '<='), ('NUMBER', '1'),
            ('OP', '&&'), ('IDENT', 'b'), ('OP', '!='), ('NUMBER', '2')]

    def test_string_literal_keeps_its_quotes_and_spaces(self):
        toks = tokenize('MessageBox "Hello there, friend"')
        assert toks[1].kind is T.STRING
        assert toks[1].text == '"Hello there, friend"'

    def test_semicolon_inside_a_string_is_not_a_comment(self):
        toks = tokenize('MessageBox "a ; b"')
        assert toks[1].text == '"a ; b"'
        assert not any(t.kind is T.COMMENT for t in toks)

    def test_unterminated_string_runs_to_end_of_line(self):
        # Oblivion's compiler accepted this; refusing it would fail a script
        # the source plugin actually ships.
        toks = tokenize('MessageBox "oops\nset x to 1')
        assert toks[1].kind is T.STRING
        assert any(t.kind is T.IDENT and t.text == 'set' for t in toks)

    def test_stray_backtick_does_not_raise(self):
        # MG09Script line 132 ships a bare '`' after `endif` in Oblivion.esm.
        toks = tokenize('endif`')
        assert [t.text for t in toks if t.kind is not T.EOF] == ['endif', '`']

    def test_newlines_are_significant(self):
        toks = tokenize('a\nb')
        assert sum(1 for t in toks if t.kind is T.NEWLINE) == 1

# ===========================================================================
# TES4 parser  (script_convert/tes4/parser.py)
# ===========================================================================

class TestTes4Parser:
    """Verified against every script body in all 10 exports (19,013 bodies):
    zero crashes, and 17 Raw statements total -- all of them authored typos
    (a bare `-----` separator or a `:` where the author meant `;`)."""

    def _block(self, src, btype='gamemode'):
        tree = parse(f'scn X\nbegin {btype}\n{src}\nend\n')
        return tree.blocks[0].body

    def test_block_owns_its_body(self):
        # The whole point of the tree: nesting is structural, so it cannot
        # come out unbalanced and need `_balance_if_endif` to repair it.
        body = self._block('\tif a == 1\n\t\tset b to 2\n\tendif')
        assert len(body) == 1
        assert isinstance(body[0], N.If)
        assert len(body[0].body) == 1
        assert isinstance(body[0].body[0], N.Assign)

    def test_command_absorbs_its_arguments_before_a_comparison(self):
        # `if getstage charactergen == 74` means (getstage charactergen) == 74.
        cond = self._block('\tif getstage charactergen == 74\n\t\tset a to 1\n\tendif')[0].cond
        assert isinstance(cond, N.BinOp) and cond.op == '=='
        assert isinstance(cond.left, N.Call)
        assert cond.left.name == 'getstage'
        assert [a.name for a in cond.left.args] == ['charactergen']

    def test_command_on_both_sides_of_an_operator(self):
        # Absorbing only the leftmost operand silently dropped the right-hand
        # arguments (Knights.esp NDBrellinSCRIPT).
        cond = self._block(
            '\tif getstage ND10 >= 20 && getstage ND10 < 50\n\t\tset a to 1\n\tendif')[0].cond
        assert cond.op == '&&'
        assert isinstance(cond.left.left, N.Call)
        assert isinstance(cond.right.left, N.Call)

    def test_parenthesised_command_call(self):
        cond = self._block(
            '\tif ( GetStageDone ND10 100 == 1 ) && ( Active == 0 )\n'
            '\t\tset a to 1\n\tendif')[0].cond
        assert cond.op == '&&'
        assert isinstance(cond.left.left, N.Call)
        assert len(cond.left.left.args) == 2

    def test_receiver_and_whitespace_separated_args(self):
        stmt = self._block('\tplayer.additem Gold001 100')[0]
        call = stmt.expr
        assert call.name == 'additem'
        assert call.receiver.name == 'player'
        assert len(call.args) == 2

    def test_command_as_a_value_keeps_its_arguments(self):
        # `set t to SayTo BaurusRef, CharGenMain 1` -- Say returns the line
        # duration, which CharacterGen stores in a timer.
        stmt = self._block(
            '\tset CharacterGen.convTimer to SayTo BaurusRef, CharGenMain 1')[0]
        assert isinstance(stmt.value, N.Call)
        assert stmt.value.name == 'SayTo'
        assert len(stmt.value.args) == 3

    def test_quoted_editor_id_receiver_is_unquoted(self):
        # Nehrim writes references quoted; 890 statements were affected.
        call = self._block('\t"NQ15W02TresorRef" . AddItem "Gold001" , 100')[0].expr
        assert call.receiver.name == 'NQ15W02TresorRef'
        assert call.name == 'AddItem'

    def test_quoted_member_on_both_sides_of_an_assignment(self):
        stmt = self._block('\tSet "NQ16"."NQ16CountVar" to "NQ16"."NQ16CountVar" + 1')[0]
        assert stmt.target.owner.name == 'NQ16'
        assert stmt.target.name == 'NQ16CountVar'
        assert stmt.value.left.name == 'NQ16CountVar'

    def test_variables_hoist_out_of_blocks(self):
        tree = parse('scn X\nshort a\nbegin gamemode\nfloat b\nend\n')
        assert [(v.vtype, v.name) for v in tree.variables] == [
            ('short', 'a'), ('float', 'b')]

    def test_duplicate_declaration_is_deduped(self):
        # SE08QuestScript declares PasswallBattleBegin twice; the current
        # converter emits one property, so the parser keeps one.
        tree = parse('scn X\nshort a\nshort a\n')
        assert len(tree.variables) == 1

    def test_digit_leading_script_name(self):
        # `scn 01FlayerBladeScript` lexes as NUMBER + IDENT (31 Nehrim scripts).
        assert parse('scn 01FlayerBladeScript\n').name == '01FlayerBladeScript'

    def test_block_filter_is_preserved(self):
        # The filter RESTRICTS the block; dropping it fires for everyone.
        tree = parse('scn X\nbegin OnHit CGAssassinFinal\n\tkill\nend\n')
        assert tree.blocks[0].btype == 'onhit'
        assert tree.blocks[0].filter == 'CGAssassinFinal'

    def test_elseif_chain_and_else(self):
        body = self._block(
            '\tif a == 1\n\t\tset b to 1\n\telseif a == 2\n\t\tset b to 2\n'
            '\telse\n\t\tset b to 3\n\tendif')
        node = body[0]
        assert len(node.elifs) == 1
        assert len(node.orelse) == 1

    def test_trailing_comment_attaches_to_its_statement(self):
        # A comment on the NODE cannot eat the rest of an expression, which is
        # what `_repair_commented_condition` exists to clean up in the text path.
        stmt = self._block('\tset a to 1  ; why')[0]
        assert stmt.comment == '; why'
        assert isinstance(stmt.value, N.Literal)

    def test_unparseable_line_degrades_to_a_comment_not_a_crash(self):
        # AkarusScript ships a bare `-----` separator with no `;`.  It is not
        # an expression -- emitted as one it became a chain of unary minuses
        # and failed to compile -- so the parser absorbs the authored damage
        # and yields a Comment, keeping the text.
        body = self._block('\t------------------')
        assert isinstance(body[0], N.Comment)
        assert set(body[0].text) <= {';', '-'}

    def test_fragment_mode_parses_a_bare_statement_list(self):
        # An INFO result script has no begin/end -- a parser PARAMETER, not a
        # reason for a second hand-written line loop.
        tree = parse('set a to 1\nplayer.additem Gold001 10\n', Mode.FRAGMENT)
        assert len(tree.body) == 2
        assert not tree.blocks

    def test_negative_argument_without_a_comma(self):
        # `Player.SetFactionRank SEHeretic -1` passes -1; the current
        # converter emits `SetFactionRank(SEHeretic, -1)`.  Treating the `-`
        # as a binary operator silently dropped the number on 229 bodies.
        call = self._block('\tPlayer.SetFactionRank SEHeretic -1')[0].expr
        assert call.name == 'SetFactionRank'
        assert len(call.args) == 2
        assert isinstance(call.args[1], N.Unary)

    def test_negative_argument_after_a_comma(self):
        call = self._block('\trotate z, -30')[0].expr
        assert len(call.args) == 2

    def test_operator_after_a_bare_name_is_not_an_argument(self):
        # `x + 1` on a plain variable must stay arithmetic, not become a
        # call taking `+ 1` as an argument.
        stmt = self._block('\tset a to b + 1')[0]
        assert isinstance(stmt.value, N.BinOp)
        assert stmt.value.op == '+'


# ===========================================================================
# Symbol table  (script_convert/symbols.py)
# ===========================================================================

class TestSymbols:
    """Verified against the generated corpus: 39,590 scripts, 36 recovered
    UDF signatures, 526 TES4Call arguments and 184,608 `Owner.member`
    statements, with ZERO disagreements against the two whole-tree grep
    passes it replaces."""

    def test_obse_call_args_join_across_an_operator(self):
        # `Call GlobalScriptExpGained 30 * ( x - y ), 1, 1, -1` is FOUR
        # arguments; the first is spelled with spaces around the operator.
        # Naive whitespace splitting emitted `TES4Call(30, *, (...), ...)`
        # and a bare `*` is not an expression.
        assert split_call_args('30 * ( x - y ), 1, 1, -1') == [
            '30 * ( x - y )', '1', '1', '-1']

    def test_obse_call_args_keep_a_quoted_filename_whole(self):
        # `IsModLoaded "Voice Overs V002.esp"` became three arguments once and
        # emitted `IsModLoaded("Voice, Overs, V002.esp(")`, which converted to
        # a bare `If True` and fired a warning unconditionally.
        assert split_call_args('"Voice Overs V002.esp"') == [
            '"Voice Overs V002.esp"']

    def test_obse_call_args_treat_a_sign_as_a_new_argument(self):
        # `-` introduces the next argument far more often than it continues
        # this one; the comma form covers subtraction unambiguously.
        assert split_call_args('Foo 1 -1') == ['Foo', '1', '-1']
        assert split_call_args('10, 1, -1') == ['10', '1', '-1']

    def test_obse_call_args_stop_at_a_comment(self):
        assert split_call_args('KnightFollow ; set to 1 to follow') == [
            'KnightFollow']

    def test_digit_leading_editor_id_is_one_identifier(self):
        # `01FlayerBladeScript`, `1TrapFireMineWorldRef` -- splitting the digit
        # run off turned one argument into two on 709 Nehrim argument tails.
        assert split_call_args('01FlayerBladeScript') == ['01FlayerBladeScript']
        assert [t.text for t in tokenize('1TrapFireMineWorldRef')
                if t.kind is T.IDENT] == ['1TrapFireMineWorldRef']

    def test_number_is_still_a_number(self):
        assert [t.kind.name for t in tokenize('100') if t.kind is not T.EOF] \
            == ['NUMBER']
        assert [t.kind.name for t in tokenize('1.5') if t.kind is not T.EOF] \
            == ['NUMBER']

    def test_non_ascii_identifier(self):
        # Nehrim is German: `MQ32Spiegelsch<umlaut>ssel01SCN` is one EditorID,
        # and an ASCII-only character class tore it into three tokens.
        name = 'MQ32Spiegelsch\u00fcssel01SCN'
        assert [t.text for t in tokenize(name) if t.kind is T.IDENT] == [name]

    def test_split_param_names(self):
        # `begin Function{...}` accepts commas, whitespace, or a mix.
        assert split_param_names('{ a, b, c }') == ['a', 'b', 'c']
        assert split_param_names('{ refRuneSpell levelRequired}') == [
            'refRuneSpell', 'levelRequired']
        assert split_param_names('{ }') == []

    def test_split_trailing_comment_respects_strings(self):
        assert split_trailing_comment('a == 1  ; why') == ('a == 1', '; why')
        assert split_trailing_comment('MessageBox "a ; b"') == (
            'MessageBox "a ; b"', '')

    def test_is_self_contained_detects_a_truncated_condition(self):
        # This is what tells a condition EATEN by a mid-expression comment
        # from one that merely carries an ordinary trailing comment.
        # Blanket-rewriting the latter to `True` silently deleted real guards.
        assert is_self_contained('(x == 1)')
        assert is_self_contained('a && b')
        assert not is_self_contained('(False ')      # unbalanced
        assert not is_self_contained('x == ')        # dangling operator
        assert not is_self_contained('a and')        # TES4 spells some as words

class TestTes5Blocks:
    """The single structural classifier the post-emit passes share.

    Before it existed each pass carried its own keyword list and they
    disagreed; see docs/commentary/script_convert.md §5.
    """

    def test_classify_keywords(self):
        assert classify('If x') is Kind.IF
        assert classify('  ElseIf y  ') is Kind.ELSEIF
        assert classify('Else') is Kind.ELSE
        assert classify('EndIf') is Kind.ENDIF
        assert classify('While x') is Kind.WHILE
        assert classify('EndWhile') is Kind.ENDWHILE
        assert classify('Return') is Kind.RETURN
        assert classify('Event OnInit()') is Kind.HEADER
        assert classify('EndEvent') is Kind.END_HEADER
        assert classify('foo.Bar()') is Kind.OTHER

    def test_typed_function_header_is_a_header(self):
        # An OBSE user function returning a value; matching only a leading
        # `Function ` missed these, so nothing inside them was balanced.
        assert classify('Int Function TES4Call(Form a)') is Kind.HEADER
        assert classify('Bool Function TES4_IsInANQDune(ObjectReference r)') is Kind.HEADER

    def test_paren_opener_is_an_opener(self):
        # The dead-code pass matched only `if `, so `If(x)` read as a plain
        # statement and a Return inside it looked top-level.
        assert classify('If(x)') is Kind.IF
        assert classify('While(x)') is Kind.WHILE
        assert classify('ElseIf(x)') is Kind.ELSEIF

    def test_comment_only_line_is_never_a_keyword(self):
        assert classify('; EndIf') is Kind.OTHER
        assert classify(';  Return  ;dead code after Return') is Kind.OTHER

    def test_inline_comment_does_not_hide_the_keyword(self):
        assert classify('EndIf ; closes the guard') is Kind.ENDIF
        assert classify('If x ; note') is Kind.IF

    def test_scan_reports_depth_inside_the_header(self):
        depths = [(l.text, len(l.stack)) for l in scan(
            ['Event A()', 'If x', 'foo', 'EndIf', 'bar', 'EndEvent'])]
        assert depths == [('Event A()', 0), ('If x', 0), ('foo', 1),
                          ('EndIf', 1), ('bar', 0), ('EndEvent', 0)]

    def test_scan_flattens_multiline_entries(self):
        # A converted statement can be one string holding several lines; read
        # as one, a blob starting with `If` counted a phantom open block.
        out = [l.text for l in scan(['Event A()', 'If x\n  foo\nEndIf', 'EndEvent'])]
        assert out == ['Event A()', 'If x', '  foo', 'EndIf', 'EndEvent']

    def test_scan_header_resets_the_stack(self):
        # An unclosed If must not leak into the next function.
        lines = list(scan(['Event A()', 'If x', 'Event B()', 'foo', 'EndEvent']))
        # `foo` is inside B, at B's top level -- A's unclosed If is gone.
        assert lines[-2].text == 'foo'
        assert lines[-2].stack == () and lines[-2].in_header

    def test_scan_tolerates_an_orphan_closer(self):
        lines = list(scan(['Event A()', 'EndIf', 'foo', 'EndEvent']))
        assert lines[2].stack == ()

class TestDeadCodeAfterReturn:
    """`If(x)` used to hide a Return's real depth, deleting live code."""

class TestTypeOf:
    """The one property/local type lookup the coercion passes share."""

    def test_locals_win_over_properties(self, converter):
        converter._var_types = {'x': 'Int'}
        converter._property_refs = {'x': 'ObjectReference'}
        assert converter.type_of('x') == 'Int'
        assert converter.type_of('x', locals_first=False) == 'ObjectReference'

    def test_type_of_keeps_the_authored_spelling(self, converter):
        # type_of must NOT match a case variant: making it do so stopped the
        # startquest handler from registering the script's own spelling, and
        # the emitted property became the record's `TG02taxes` instead of the
        # script's `TG02Taxes` (2 files).
        converter._property_refs = {'Owner': 'TES4_Remote'}
        assert converter.type_of('Owner') == 'TES4_Remote'
        assert converter.type_of('OWNER') == ''

    def test_cross_script_resolver_is_case_insensitive(self, converter):
        # The resolvers DO need it: `Owner.Var` may spell Owner any way.
        converter._property_refs = {'Owner': 'TES4_Remote'}
        for spelling in ('Owner', 'owner', 'OWNER'):
            assert converter._property_type_ci(spelling) == 'TES4_Remote'

    def test_dotted_name_is_not_matched_by_its_owner(self, converter):
        # EmfridDEMO's script holds a variable `emfridDEMO`.  Matching the
        # dot-split TAIL case-insensitively resolved `EmfridDEMO.emfridDEMO`
        # to the OWNER's script type, and the assignment came out `= None`
        # (TES4_TIF__00028A2E, 1 compile failure).
        converter._property_refs = {'EmfridDEMO': 'TES4_EmfridDEMOScript'}
        assert converter.type_of('EmfridDEMO.emfridDEMO') == ''
        assert converter.type_of('EmfridDEMO') == 'TES4_EmfridDEMOScript'

    def test_undeclared_name_has_no_type(self, converter):
        converter._property_refs = {}
        converter._var_types = {}
        assert converter.type_of('nothing') == ''


class TestScaleEnumAv:
    """Tier ladders as data, verified against the thresholds they replaced."""

    def test_aggression_tiers(self, converter):
        assert converter._scale_enum_av('aggression', '5') == '0'
        assert converter._scale_enum_av('aggression', '10') == '1'
        assert converter._scale_enum_av('aggression', '65') == '2'
        assert converter._scale_enum_av('aggression', '106') == '3'

    def test_aggression_boundary_is_gt_five_not_ge_six(self, converter):
        # The threshold is `> 5`, not `>= 6`: the ladder's floor must be just
        # above 5 so a fractional 5.5 lands on tier 1, as `raw <= 5` did.
        assert converter._scale_enum_av('aggression', '5') == '0'
        assert converter._scale_enum_av('aggression', '5.5') == '1'
        assert converter._scale_enum_av('aggression', '6') == '1'

    def test_confidence_tiers(self, converter):
        for value, tier in (('0', '0'), ('15', '1'), ('40', '2'),
                            ('70', '3'), ('100', '4')):
            assert converter._scale_enum_av('confidence', value) == tier

    def test_value_already_in_range_passes_through(self, converter):
        # A deliberate Skyrim-style tier is not re-bucketed.
        assert converter._scale_enum_av('aggression', '2') == '2'

    def test_non_literal_operand_is_declined(self, converter):
        assert converter._scale_enum_av('aggression', 'someVar') is None

    def test_non_enum_actor_value_is_declined(self, converter):
        assert converter._scale_enum_av('health', '50') is None
