"""GetItemCount of an item the receiver's base record lists: the authored-count read.

See docs/commentary/script_convert.md#getitemcount-before-the-inventory-is-built.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from script_convert import authored_items
from script_convert.constants import PLAYER_ALIAS_EXTENDS
from script_convert.converter import ScriptConverter
from script_convert.cross_ref import CrossRefGraph, _new_scan_out, _scan_record_lines
from script_convert.emit.dispatch import emit_command
from script_convert.tes4 import nodes as N
from script_convert.tes4.parser import Mode, parse
from tes5_import.pipeline import _index_xref_record, _prescan_cross_ref_graph

_POLYFILL = (Path(__file__).resolve().parent.parent / 'script_convert'
             / 'static_scripts' / 'TES4Polyfill.psc')

#: The read every test of a placed container converts, and what it was before.
_NEW = 'TES4Polyfill.AuthoredItemCount(DropBoxRef, Orders, 1)'
_OLD = 'DropBoxRef.GetItemCount(Orders)'

#: The same two for a script's own reference.
_OWN_NEW = 'TES4Polyfill.AuthoredItemCount(Self, Orders, 1)'
_OWN_OLD = 'GetItemCount(Orders)'


def _rec(sig, fid, edid, items=None, **fields):
    """One export record as text; `items` is its item list as (FormID, count) pairs."""
    if items is not None:
        fields['ItemCount'] = len(items)
        for i, (item, count) in enumerate(items):
            fields.update({f'Item[{i}].FormID': item, f'Item[{i}].Count': count})
    extra = ''.join(f'{k}={v}\n' for k, v in fields.items())
    return (f'---RECORD_BEGIN---\nSignature={sig}\nFormID={fid}\n'
            f'EditorID={edid}\n{extra}---RECORD_END---\n')


def _script(fid, edid, kind=0):
    """A script record of `kind` (0 = object): only its name and kind matter."""
    return _rec('SCPT', fid, edid, **{'SCHR.Type': kind})


#: Export files of one plugin: items, scripts, the records listing items, their placements.
_WORLD = {
    'MISC.txt': (_rec('MISC', '0000000F', 'Gold001') + _rec('MISC', '0000B002', 'Hammer')
                 + _rec('MISC', '0000B003', 'Trinket')),
    'BOOK.txt': _rec('BOOK', '0000B001', 'Orders'),
    'LVLI.txt': _rec('LVLI', '0000B010', 'LootList', EntryCount=1,
                     **{'Entry[0].FormID': '0000B001', 'Entry[0].Count': 1}),
    'SCPT.txt': ''.join(_script(f'0000D00{i}', name) for i, name in enumerate(
        ('DropScript', 'TwinScript', 'OddScript', 'CourierScript', 'MixedScript',
         'PlayerScript', 'SpawnScript', 'PackScript'), 1)) + _script('0000D009', 'ErrandScript', kind=1),
    'CONT.txt': (
        _rec('CONT', '0000C001', 'DropBox', [('0000000F', 500), ('0000B001', 1)], SCRI='0000D001')
        + _rec('CONT', '0000C002', 'TwinBoxA', [('0000B002', 4)], SCRI='0000D002')
        + _rec('CONT', '0000C003', 'TwinBoxB', [('0000000F', 9), ('0000B002', 4)], SCRI='0000D002')
        + _rec('CONT', '0000C004', 'OddBoxA', [('0000B002', 1)], SCRI='0000D003')
        + _rec('CONT', '0000C005', 'OddBoxB', [('0000B002', 2)], SCRI='0000D003')
        + _rec('CONT', '0000C006', 'LeveledBox', [('0000B010', 1)])
        + _rec('CONT', '0000C007', 'ZeroBox', [('0000B001', 0)])
        + _rec('CONT', '0000C008', 'StockBox', [('0000B001', -5)])
        + _rec('CONT', '0000C009', 'DoubleBox', [('0000B001', 1), ('0000B001', 2)])
        + _rec('CONT', '0000C00A', 'MixedBox', [('0000B002', 1)], SCRI='0000D005')),
    'ACTI.txt': _rec('ACTI', '0000C00B', 'MixedSwitch', SCRI='0000D005'),
    'NPC_.txt': (_rec('NPC_', '0000A001', 'Courier', [('0000B001', 3)], SCRI='0000D004')
                 + _rec('NPC_', '00000007', 'Player', [('0000B001', 1)], SCRI='0000D006')),
    'CREA.txt': (_rec('CREA', '0000A002', 'SpawnTemplate', [('0000B002', 2)], SCRI='0000D007')
                 + _rec('CREA', '0000A003', 'PackLeader', [('0000B002', 2)], SCRI='0000D008')
                 + _rec('CREA', '0000A004', 'PackMember', [('0000B002', 2)], SCRI='0000D008')),
    'LVLC.txt': (_rec('LVLC', '0000B020', 'SpawnList', **{'TNAM.Template': '0000A002'})
                 + _rec('LVLC', '0000B021', 'PackList', **{'TNAM.Template': '0000A003'})),
    'QUST.txt': _rec('QUST', '0000F002', 'Errand', SCRI='0000D009'),
    'REFR.txt': (''.join(_rec('REFR', f'0000E00{n}', f'{name}Ref', NAME=f'0000C00{n}')
                         for n, name in ((1, 'DropBox'), (6, 'LeveledBox'), (7, 'ZeroBox'),
                                         (8, 'StockBox'), (9, 'DoubleBox')))
                 + _rec('REFR', '0000E011', 'DropBoxTwinRef', NAME='0000C001')
                 + _rec('REFR', '0000E012', 'OrdersRef', NAME='0000B001')),
    'ACHR.txt': (_rec('ACHR', '0000E010', 'CourierRef', NAME='0000A001')
                 + _rec('ACHR', '00000014', 'PlayerRef', NAME='00000007')),
    'ACRE.txt': _rec('ACRE', '0000E013', 'SpawnTemplateRef', NAME='0000A002'),
}


def _export(folder, files, masters=()):
    """Write one plugin's export folder and return its path."""
    folder.mkdir(parents=True)
    for name, text in files.items():
        (folder / name).write_text(text, encoding='utf-8')
    (folder / '_HEADER.txt').write_text(
        ''.join(f'Master[{i}]={m}\n' for i, m in enumerate(masters)), encoding='utf-8')
    return str(folder)


def _load(export_dir):
    """The graph of one export folder, scanned in this process."""
    graph = CrossRefGraph()
    graph.load_from_export(export_dir, workers=1)
    return graph


@pytest.fixture(scope='module')
def xref(tmp_path_factory):
    """The graph of `_WORLD`."""
    return _load(_export(tmp_path_factory.mktemp('items') / 'World.esm', _WORLD))


#: The index bytes of a master the plugin loads one slot above the number the master gives itself.
_ONE_SLOT_UP = {0: 0, 1: 2}


def _both_graphs(folder, rec, own=()):
    """One record of that master in the export scan's tables and in the importer's graph; `own` is the plugin's."""
    scan = _new_scan_out()
    _scan_record_lines(rec['Signature'], [f'{k}={v}' for k, v in rec.items()], scan, remap=_ONE_SLOT_UP)
    ctx = SimpleNamespace(master_export={'02' + rec['FormID'][2:]: dict(rec)})
    return scan, _prescan_cross_ref_graph(list(own), ctx, str(folder), lambda *_: None)


def _convert(graph, source, extends='ObjectReference', edid='T'):
    """One standalone script converted, as stripped lines."""
    text = ScriptConverter(graph).convert_standalone(edid, source, extends, edid)
    return [line.strip() for line in text.split('\n')]


def _read(graph, call, extends='ObjectReference', edid='T', declare='', also=''):
    """What `set n to <call>` becomes in the poll of a script named `edid`; `also` follows it."""
    source = f'scn {edid}\nshort n\n{declare}begin GameMode\nset n to {call}\n{also}end\n'
    found = [line[4:] for line in _convert(graph, source, extends, edid)
             if line.startswith('n = ')]
    assert len(found) == 1, found
    return found[0]


def _result(conv, call, also='', extends='Quest'):
    """What `set n to <call>` becomes in a stage or dialogue result; `also` follows it."""
    lines = conv.convert_fragment(f'short n\nset n to {call}\n{also}', extends)
    found = [line.strip()[4:] for line in lines if line.strip().startswith('n = ')]
    assert len(found) == 1, lines
    return found[0]


def _plain(call: str) -> str:
    """The command row's text for `Receiver.GetItemCount a b`."""
    head, _, args = call.partition(' ')
    return '%s(%s)' % (head, ', '.join(args.split()))


#: A read the rule converts: (TES4 call, the count the base record lists).
_ACCEPTED = (
    ('DropBoxRef.GetItemCount Orders', 1),
    ('DropBoxRef.GetItemCount Gold001', 500),
    ('CourierRef.GetItemCount Orders', 3),
    ('SpawnTemplateRef.GetItemCount Hammer', 2),
)

#: A read on a named receiver the rule declines: (why, TES4 call, declarations, the text when it is not the row's).
_DECLINED = (
    ('the base does not list the item', 'DropBoxRef.GetItemCount Trinket', '', ''),
    ('an actor that does not list the item', 'CourierRef.GetItemCount Hammer', '', ''),
    ('the item is only inside a leveled list', 'LeveledBoxRef.GetItemCount Orders', '', ''),
    ('a leveled list named as the item', 'LeveledBoxRef.GetItemCount LootList', '', ''),
    ('a placed reference named as the item', 'DropBoxRef.GetItemCount OrdersRef', '', ''),
    ('a count of zero', 'ZeroBoxRef.GetItemCount Orders', '', ''),
    ('a restocking, negative count', 'StockBoxRef.GetItemCount Orders', '', ''),
    ('the item listed twice', 'DoubleBoxRef.GetItemCount Orders', '', ''),
    ('the player', 'player.GetItemCount Orders', '', 'Game.GetPlayer().GetItemCount(Orders)'),
    ('the player reference', 'PlayerRef.GetItemCount Orders', '', 'Game.GetPlayer().GetItemCount(Orders)'),
    ('a reference variable', 'box.GetItemCount Orders', 'ref box\n', ''),
    ('a variable named like the reference', 'DropBoxRef.GetItemCount Orders', 'ref DropBoxRef\n', ''),
    ('a variable named like the item', 'DropBoxRef.GetItemCount Orders', 'ref Orders\n', ''),
    ("another script's variable as the item", 'DropBoxRef.GetItemCount Holder.Orders', '', ''),
    ('a base record as the receiver', 'DropBox.GetItemCount Orders', '', ''),
    ('a receiver that names nothing', 'NoSuchRef.GetItemCount Orders', '', ''),
    ('no argument', 'DropBoxRef.GetItemCount', '', 'DropBoxRef.GetItemCount()'),
    ('a second argument', 'DropBoxRef.GetItemCount Orders 2', '', ''),
)


class TestPlacedReceiver:
    """A receiver named by a placed reference's EditorID."""

    @pytest.mark.parametrize('call, count', _ACCEPTED)
    def test_a_listed_item_reads_through_the_authored_count(self, xref, call, count):
        """The base record lists the item once: its count rides along."""
        receiver, item = call.replace('.GetItemCount', '').split()
        assert _read(xref, call) == f'TES4Polyfill.AuthoredItemCount({receiver}, {item}, {count})'

    @pytest.mark.parametrize('why, call, declare, text', _DECLINED, ids=[row[0] for row in _DECLINED])
    def test_a_read_that_cannot_be_told_is_plain(self, xref, why, call, declare, text):
        """Each row is one reason to decline; the call converts as it did before."""
        assert _read(xref, call, declare=declare) == (text or _plain(call))

    def test_only_the_call_text_differs_from_the_plain_read(self, xref, tmp_path):
        """Properties, types and every other line are those of the plain call, also as a condition."""
        source = ('scn T\nshort n\nfloat f\nbegin GameMode\nset n to DropBoxRef.GetItemCount Orders\n'
                  'set f to DropBoxRef.GetItemCount Orders\n'
                  'if DropBoxRef.GetItemCount Orders < 1\nset n to 1\nendif\nend\n')
        plain = _load(_export(tmp_path / 'World.esm', _WORLD))
        plain.record_items.clear()
        new, old = _convert(xref, source), _convert(plain, source)
        assert f'n = {_NEW}' in new and f'f = {_NEW}' in new and f'If {_NEW} < 1' in new
        assert [line.replace(_NEW, _OLD) for line in new] == old

    def test_either_variable_table_hides_a_record(self, xref):
        """The converter knows a variable by its name or by its type; each alone is enough."""
        for name in ('dropboxref', 'orders'):
            named, typed = ScriptConverter(xref), ScriptConverter(xref)
            named.sc.local_vars.add(name)
            typed.sc.var_types[name] = 'ObjectReference'
            for conv in (named, typed):
                assert emit_command(conv, 'DropBoxRef', 'GetItemCount', 'ObjectReference',
                                    [N.Ident('Orders')]) == _OLD

    def test_quotes_and_the_script_type_do_not_matter(self, xref):
        """Oblivion accepts quotes around an EditorID; a quest or a dialogue result names the box the same way."""
        assert _read(xref, 'DropBoxRef.GetItemCount "Orders"') == _NEW
        assert _read(xref, '"DropBoxRef".GetItemCount Orders') == _NEW
        assert _read(xref, 'DropBoxRef.GetItemCount Orders', 'Quest') == _NEW
        assert _read(xref, 'DropBoxRef.GetItemCount Orders', 'TopicInfo') == _NEW


#: A read in a script records carry: (why, call, script type, script, declarations, the converted text).
_OWN = (
    ('one base record', 'GetItemCount Orders', 'ObjectReference', 'DropScript', '', _OWN_NEW),
    ('GetSelf is the own reference', 'GetSelf.GetItemCount Orders', 'ObjectReference', 'DropScript', '', _OWN_NEW),
    ('an actor script', 'GetItemCount Orders', 'Actor', 'CourierScript', '',
     'TES4Polyfill.AuthoredItemCount(Self, Orders, 3)'),
    ('two base records that agree', 'GetItemCount Hammer', 'ObjectReference', 'TwinScript', '',
     'TES4Polyfill.AuthoredItemCount(Self, Hammer, 4)'),
    ('two base records that disagree', 'GetItemCount Hammer', 'ObjectReference', 'OddScript', '',
     'GetItemCount(Hammer)'),
    ('a carrier without an item list', 'GetItemCount Hammer', 'ObjectReference', 'MixedScript', '',
     'GetItemCount(Hammer)'),
    ('no record carries the script', 'GetItemCount Orders', 'ObjectReference', 'T', '', _OWN_OLD),
    ('a quest has no inventory', 'GetItemCount Orders', 'Quest', 'DropScript', '', _OWN_OLD),
    ("a leveled list's template", 'GetItemCount Hammer', 'Actor', 'SpawnScript', '', 'GetItemCount(Hammer)'),
    ('one carrier of two is a template', 'GetItemCount Hammer', 'Actor', 'PackScript', '', 'GetItemCount(Hammer)'),
    ('a named read under a template', 'DropBoxRef.GetItemCount Orders', 'Actor', 'SpawnScript', '', _NEW),
    ('a variable named mySelf', 'mySelf.GetItemCount Orders', 'ObjectReference', 'DropScript', 'ref mySelf\n',
     'mySelf.GetItemCount(Orders)'),
)


class TestOwnReference:
    """A bare call acts on the reference the script is attached to; a named one does not."""

    @pytest.mark.parametrize('why, call, extends, edid, declare, text', _OWN, ids=[row[0] for row in _OWN])
    def test_the_records_carrying_the_script_answer(self, xref, why, call, extends, edid, declare, text):
        """Every record carrying the script must list the same count."""
        assert _read(xref, call, extends, edid, declare) == text

    def test_the_template_itself_lists_the_item(self, xref):
        """The template row above declines for the template, not for a count."""
        assert xref.listed_item_count(xref.script_owner_actors('SpawnScript'), '0000B002') == 2

    def test_a_magic_effect_or_a_dialogue_result_has_no_reference_of_its_own(self, xref):
        """Their bare call acts on the target or the speaker, whatever record names the script."""
        plain = {'ActiveMagicEffect': 'GetTargetActor().GetItemCount(Orders)',
                 'TopicInfo': 'akSpeakerRef.GetItemCount(Orders)', 'Quest': _OWN_OLD,
                 'ObjectReference': _OWN_NEW, 'Actor': _OWN_NEW}
        for extends, text in plain.items():
            conv = ScriptConverter(xref)
            conv.sc.edid = 'DropScript'
            assert emit_command(conv, None, 'GetItemCount', extends, [N.Ident('Orders')]) == text

    def test_a_script_on_the_player_is_plain(self, xref):
        """The player's script runs on an alias, and the player is never asked."""
        assert 'AuthoredItemCount' not in _read(
            xref, 'GetItemCount Orders', PLAYER_ALIAS_EXTENDS, 'PlayerScript')


_BOX, _BARE = 'DropBoxRef.GetItemCount Orders', 'GetItemCount Orders'

#: A read beside a removal: (which case, read, script type, script, declarations, the removal, does the read convert).
_BESIDE = (
    ('the same reference and item', _BOX, '', 'T', '', 'DropBoxRef.RemoveItem Orders 1\n', False),
    ('the same reference and item', _BOX, '', 'T', '', '"dropboxref".removeitem "ORDERS" 1\n', False),
    ('the same reference and item', _BOX, '', 'T', '', 'DropBoxRef.RemoveItemNS Orders 1\n', False),
    ('the same reference and item', _BOX, '', 'T', '', 'DropBoxRef.RemoveItem Orders\n', False),
    ('the same reference and item', _BOX, '', 'T', '', 'end\nbegin OnActivate\nDropBoxRef.RemoveItem Orders 1\n', False),
    ('the same reference and item', _BOX, '', 'T', '', 'if DropBoxRef.RemoveItem Orders 1\nendif\n', False),
    ('the same reference and item', _BOX, '', 'T', '', 'DropBoxRef.RemoveAllItems\n', False),
    ('the same reference and item', _BOX, '', 'T', '', 'DropBoxRef.RemoveAllItems PlayerRef\n', False),
    ('another item, or one that cannot be told', _BOX, '', 'T', '', 'DropBoxRef.RemoveItem Gold001 5\n', True),
    ('another item, or one that cannot be told', _BOX, '', 'T', 'ref thing\n', 'DropBoxRef.RemoveItem thing 1\n', False),
    ('another item, or one that cannot be told', _BOX, '', 'T', 'ref Gold001\n', 'DropBoxRef.RemoveItem Gold001 1\n', False),
    ('another item, or one that cannot be told', _BOX, '', 'T', '', 'DropBoxRef.RemoveItem NoSuchItem 1\n', False),
    ('another item, or one that cannot be told', _BOX, '', 'T', '', 'DropBoxRef.RemoveItem Holder.thing 1\n', False),
    ('the player or another reference', _BOX, '', 'T', '', 'player.RemoveItem Orders 1\n', True),
    ('the player or another reference', _BOX, '', 'T', '', 'PlayerRef.RemoveAllItems\n', True),
    ('the player or another reference', _BOX, '', 'T', '', 'StockBoxRef.RemoveItem Orders 1\n', True),
    ('the player or another reference', _BOX, '', 'T', '', 'if StockBoxRef.RemoveAllItems\nendif\n', True),
    ('the player or another reference', _BOX, '', 'T', '', 'DropBoxTwinRef.RemoveItem Orders 1\n', True),
    ('the player or another reference', _BOX, '', 'T', '', 'DropBoxTwinRef.RemoveAllItems\n', True),
    ('a receiver that cannot be told', _BOX, '', 'T', 'ref box\n', 'box.RemoveItem Orders 1\n', False),
    ('a receiver that cannot be told', _BOX, '', 'T', 'ref StockBoxRef\n', 'StockBoxRef.RemoveItem Orders 1\n', False),
    ('a receiver that cannot be told', _BOX, '', 'T', '', 'NoSuchRef.RemoveItem Orders 1\n', False),
    ('a receiver that cannot be told', _BOX, '', 'T', '', 'DropBox.RemoveItem Orders 1\n', False),
    ('a receiver that cannot be told', _BOX, '', 'T', '', 'Holder.box.RemoveItem Orders 1\n', False),
    ('the own reference on both sides', _BARE, '', 'DropScript', '', 'RemoveItem Orders 1\n', False),
    ('the own reference on both sides', _BARE, '', 'DropScript', '', 'GetSelf.RemoveItem Orders 1\n', False),
    ('the own reference on both sides', _BARE, '', 'DropScript', '', 'RemoveAllItems\n', False),
    ('the own reference on both sides', _BARE, '', 'DropScript', '', 'RemoveItem Gold001 1\n', True),
    ('a placement of a carrier of the script', _BARE, '', 'DropScript', '', 'DropBoxRef.RemoveItem Orders 1\n', False),
    ('a placement of a carrier of the script', _BARE, '', 'DropScript', '', 'DropBoxTwinRef.RemoveItem Orders 1\n', False),
    ('a placement of a carrier of the script', _BARE, '', 'DropScript', '', 'StockBoxRef.RemoveItem Orders 1\n', True),
    ('a placement of a carrier of the script', _BARE, '', 'DropScript', '', 'player.RemoveItem Orders 1\n', True),
    ('a placement of a carrier of the script', _BARE, '', 'DropScript', '', 'CourierRef.RemoveItem Orders 1\n', True),
    ('a bare removal beside a named read', _BOX, '', 'DropScript', '', 'RemoveItem Orders 1\n', False),
    ('a bare removal beside a named read', _BOX, 'Actor', 'CourierScript', '', 'RemoveItem Orders 1\n', True),
    ('a bare removal beside a named read', _BOX, '', 'T', '', 'RemoveItem Orders 1\n', False),
    ('a bare removal beside a named read', _BOX, 'Quest', 'T', '', 'RemoveItem Orders 1\n', False),
    ('a bare removal in a quest or effect script a record carries', _BOX, 'Quest', 'ErrandScript', '',
     'RemoveItem Orders 1\n', False),
    ('a bare removal in a quest or effect script a record carries', _BOX, 'ActiveMagicEffect', 'ErrandScript', '',
     'RemoveItem Orders 1\n', False),
    ('a dropped item', _BOX, '', 'T', '', 'DropBoxRef.Drop Orders 1\n', False),
    ('a dropped item', _BOX, '', 'T', '', 'DropBoxRef.Drop Gold001 1\n', True),
    ('a dropped item', _BOX, '', 'T', '', 'CourierRef.Drop Orders 1\n', True),
    ('a dropped item', _BARE, '', 'DropScript', '', 'Drop Orders 1\n', False),
)


class TestRemovals:
    """A script that also takes the counted item out of the same receiver keeps the plain read."""

    @pytest.mark.parametrize('case, read, extends, edid, declare, also, converts', _BESIDE,
                             ids=[row[0] for row in _BESIDE])
    def test_a_removal_that_may_take_the_item_out(self, xref, case, read, extends, edid, declare, also, converts):
        """The read stays plain when item and receiver are the same or cannot be told."""
        new, old = (_NEW, _OLD) if read == _BOX else (_OWN_NEW, _OWN_OLD)
        text = _read(xref, read, extends or 'ObjectReference', edid, declare, also)
        assert text == (new if converts else old)

    def test_the_other_item_is_judged_by_its_own_removal(self, xref):
        """Gold taken out of the box keeps the gold read plain, not the orders read."""
        assert _read(xref, 'DropBoxRef.GetItemCount Gold001',
                     also='DropBoxRef.RemoveItem Gold001 5\n') == 'DropBoxRef.GetItemCount(Gold001)'

    def test_every_removal_is_looked_at(self, xref):
        """Two items taken out of the box: both reads stay plain, whichever removal is met first."""
        lines = _convert(xref, 'scn T\nshort n\nshort g\nbegin GameMode\n'
                               'set n to DropBoxRef.GetItemCount Orders\nset g to DropBoxRef.GetItemCount Gold001\n'
                               'DropBoxRef.RemoveItem Gold001 5\nDropBoxRef.RemoveItem Orders 1\nend\n')
        assert f'n = {_OLD}' in lines and 'g = DropBoxRef.GetItemCount(Gold001)' in lines

    def test_an_actor_that_drops_the_item_keeps_the_plain_read(self, xref):
        """TES4 `Drop` takes the item out of the actor's inventory, as RemoveItem does."""
        read = 'CourierRef.GetItemCount Orders'
        assert _read(xref, read, also='CourierRef.Drop Orders 1\n') == 'CourierRef.GetItemCount(Orders)'
        assert _read(xref, read, also='CourierRef.Drop Hammer 1\n') == \
            'TES4Polyfill.AuthoredItemCount(CourierRef, Orders, 3)'

    def test_a_result_script_is_a_script_of_its_own(self, xref):
        """A removal in one stage or dialogue result says nothing about the next one."""
        conv = ScriptConverter(xref)
        removal = 'DropBoxRef.RemoveItem Orders 1\n'
        assert _result(conv, 'DropBoxRef.GetItemCount Orders') == _NEW
        assert _result(conv, 'DropBoxRef.GetItemCount Orders', removal) == _OLD
        assert _result(conv, 'DropBoxRef.GetItemCount Orders') == _NEW
        assert _result(conv, 'DropBoxRef.GetItemCount Orders', 'RemoveItem Orders 1\n',
                       'TopicInfo') == _OLD

    def test_a_script_does_not_inherit_the_one_before(self, xref):
        """One converter runs every script; each starts without the last one's removals."""
        conv = ScriptConverter(xref)
        head = 'short n\nbegin GameMode\nset n to DropBoxRef.GetItemCount Orders\n'
        first = conv.convert_standalone('A', f'scn A\n{head}DropBoxRef.RemoveItem Orders 1\nend\n')
        second = conv.convert_standalone('B', f'scn B\n{head}end\n')
        assert f'n = {_OLD}' in first and f'n = {_NEW}' in second

    def test_the_removers_are_the_rows_that_convert_to_them(self):
        """Every TES4 command whose row emits a call that takes items out, and no other."""
        assert authored_items._REMOVERS == {
            'removeitem': 'RemoveItem', 'removeitemns': 'RemoveItem', 'drop': 'DropObject',
            'removeallitems': 'RemoveAllItems'}

    def test_what_a_script_takes_out(self):
        """Receiver and item as lowercase source names; '' for the own reference and for any item."""
        tree = parse('"BoxRef".RemoveItem "Orders" 1\nRemoveItem thing 2\nBoxRef.RemoveAllItems\n'
                     'RemoveAllItems\nif GetSelf.RemoveItemNS Gold001 1\nendif\n'
                     'if SackRef.RemoveAllItems\nendif\nset n to UrnRef.RemoveAllItems PlayerRef\n'
                     'CourierRef.Drop Letter 1\nDrop Coin 2\n'
                     'BoxRef.AddItem Orders 1\nset n to BoxRef.GetItemCount Orders\n', Mode.FRAGMENT)
        assert authored_items.removals(N.walk_exprs_in(tree.body)) == {
            ('boxref', 'orders'), ('', 'thing'), ('boxref', ''), ('', ''), ('getself', 'gold001'),
            ('sackref', ''), ('urnref', ''), ('courierref', 'letter'), ('', 'coin')}


class TestSourceFormat:
    """Sources whose actors take items and scripts from a template keep every read plain."""

    def test_a_fallout_export_is_plain(self, xref, tmp_path):
        """A record type only FO3/FNV has marks the export; the same world without it converts."""
        files = dict(_WORLD, **{'TERM.txt': _rec('TERM', '0000F001', 'Terminal')})
        graph = _load(_export(tmp_path / 'Waste.esm', files))
        assert graph.is_fallout_source() and not xref.is_fallout_source()
        assert graph.listed_item_count(['0000C001'], '0000B001') == 1
        assert _read(graph, 'DropBoxRef.GetItemCount Orders') == _OLD
        assert _read(graph, 'GetItemCount Orders', edid='DropScript') == _OWN_OLD
        assert _read(xref, 'DropBoxRef.GetItemCount Orders') == _NEW

    def test_the_importers_graph_knows_the_source_too(self):
        """Hand-built from records, it answers from the record types it was given."""
        plain, waste = CrossRefGraph(), CrossRefGraph()
        for graph, sig in ((plain, 'CONT'), (waste, 'TERM')):
            _index_xref_record(graph, '0000F001', {'Signature': sig, 'FormID': '0000F001'},
                               lambda v, own_raw, own_key: v)
        assert waste.is_fallout_source() and not plain.is_fallout_source()


class TestItemLists:
    """The item lists the graph keeps, from the export and from the importer."""

    def test_every_listing_record_has_a_row(self, xref):
        """Entries stay in order, a leveled list is one entry, a record without a list has none."""
        assert xref.record_items['0000C001'] == (('0000000F', 500), ('0000B001', 1))
        assert xref.record_items['0000C006'] == (('0000B010', 1),)
        assert xref.record_items['0000C009'] == (('0000B001', 1), ('0000B001', 2))
        assert '0000C00B' not in xref.record_items and '0000B010' not in xref.record_items

    def test_a_masters_reference_and_record_resolve(self, tmp_path):
        """A dependent plugin's script names its master's placed container."""
        _export(tmp_path / 'World.esm', _WORLD)
        plugin = _export(tmp_path / 'Plugin.esp', {}, masters=['World.esm'])
        assert _read(_load(plugin), 'DropBoxRef.GetItemCount Orders') == _NEW

    def test_a_plugins_emptied_copy_replaces_the_masters_list(self, tmp_path):
        """The plugin overrides the container with no items: nothing is authored any more."""
        _export(tmp_path / 'World.esm', _WORLD)
        plugin = _export(tmp_path / 'Plugin.esp', {
            'CONT.txt': _rec('CONT', '0000C001', 'DropBox', [], SCRI='0000D001')},
            masters=['World.esm'])
        graph = _load(plugin)
        assert graph.record_items['0000C001'] == ()
        assert _read(graph, 'DropBoxRef.GetItemCount Orders') == 'DropBoxRef.GetItemCount(Orders)'

    def test_a_masters_item_ids_follow_the_plugins_load_order(self, tmp_path):
        """A second master numbers its own records 01; the plugin loads it as 02."""
        _export(tmp_path / 'World.esm', _WORLD)
        _export(tmp_path / 'Other.esp', {
            'MISC.txt': _rec('MISC', '01000B01', 'OtherThing')}, masters=['World.esm'])
        _export(tmp_path / 'Addon.esp', {
            'MISC.txt': _rec('MISC', '01000B01', 'AddonCoin'),
            'CONT.txt': _rec('CONT', '01000C01', 'AddonBox',
                             [('01000B01', 7), ('0000B001', 2)]),
            'REFR.txt': _rec('REFR', '01000E01', 'AddonBoxRef', NAME='01000C01')},
            masters=['World.esm'])
        graph = _load(_export(tmp_path / 'Plugin.esp', {},
                              masters=['World.esm', 'Other.esp', 'Addon.esp']))
        assert graph.record_items['02000C01'] == (('02000B01', 7), ('0000B001', 2))
        assert _read(graph, 'AddonBoxRef.GetItemCount AddonCoin') == \
            'TES4Polyfill.AuthoredItemCount(AddonBoxRef, AddonCoin, 7)'
        assert _read(graph, 'AddonBoxRef.GetItemCount OtherThing') == \
            'AddonBoxRef.GetItemCount(OtherThing)'

    def test_a_leveled_lists_template_is_kept(self, xref, tmp_path):
        """The record a leveled creature list names as its template, in the plugin's load order."""
        assert xref.leveled_templates == {'0000A002', '0000A003'}
        _export(tmp_path / 'World.esm', _WORLD)
        _export(tmp_path / 'Other.esp', {}, masters=['World.esm'])
        _export(tmp_path / 'Addon.esp', {
            'LVLC.txt': _rec('LVLC', '01000F01', 'AddonList', **{'TNAM.Template': '01000A01'})},
            masters=['World.esm'])
        graph = _load(_export(tmp_path / 'Plugin.esp', {},
                              masters=['World.esm', 'Other.esp', 'Addon.esp']))
        assert graph.leveled_templates == {'0000A002', '0000A003', '02000A01'}

    def test_the_importers_graph_keeps_the_template_too(self, tmp_path):
        """Both graph builders agree on a master's template from its own file; only an LVLC has one."""
        scan, graph = _both_graphs(tmp_path, {'Signature': 'LVLC', 'FormID': '01000F01',
                                              'TNAM.Template': '01000A01'})
        assert graph.leveled_templates == scan['leveled_templates'] == {'02000A01'}
        scan, graph = _both_graphs(tmp_path, {'Signature': 'CREA', 'FormID': '01000F02',
                                              'TNAM.Template': '01000A02'})
        assert graph.leveled_templates == scan['leveled_templates'] == set()

    def test_a_count_is_never_taken_for_an_id(self):
        """Eight digits of count read like a FormID; only the id fields are re-keyed."""
        out = _new_scan_out()
        _scan_record_lines('CONT', [
            'Signature=CONT', 'FormID=10000C01', 'ItemCount=1',
            'Item[0].FormID=10000B01', 'Item[0].Count=10000005'], out, remap={16: 3})
        assert out['record_items'] == {'03000C01': (('03000B01', 10000005),)}

    def test_the_importers_graph_indexes_the_item_list_too(self, tmp_path):
        """Both graph builders agree on a master's items from its own file, and on a record of the plugin."""
        scan, graph = _both_graphs(
            tmp_path, {'Signature': 'CONT', 'FormID': '01000C01', 'ItemCount': '2',
                       'Item[0].FormID': '01000B01', 'Item[0].Count': '7',
                       'Item[1].FormID': '01000B02', 'Item[1].Count': '-1'},
            own=[{'Signature': 'CONT', 'FormID': '03000C01', 'ItemCount': '1',
                  'Item[0].FormID': '0000B001', 'Item[0].Count': '5'}])
        assert scan['record_items'] == {'02000C01': (('02000B01', 7), ('02000B02', -1))}
        assert graph.record_items == dict(scan['record_items'], **{'03000C01': (('0000B001', 5),)})

    def test_the_importers_graph_moves_an_id_of_the_masters_master_too(self, tmp_path):
        """The limit the doc names: its re-keying shifts every id of a record by the record's own shift."""
        scan, graph = _both_graphs(
            tmp_path, {'Signature': 'CONT', 'FormID': '01000C01', 'ItemCount': '1',
                       'Item[0].FormID': '0000B001', 'Item[0].Count': '2'})
        assert scan['record_items'] == {'02000C01': (('0000B001', 2),)}
        assert graph.record_items == {'02000C01': (('0100B001', 2),)}
        assert graph.listed_item_count(['02000C01'], '0000B001') == 0

    def test_an_item_of_a_file_the_plugin_does_not_load_is_no_entry(self):
        """A master's item id naming a file outside the plugin's list, and an entry without an id, are dropped."""
        out = _new_scan_out()
        _scan_record_lines('CONT', [
            'Signature=CONT', 'FormID=01000C01', 'ItemCount=3',
            'Item[0].FormID=05000B01', 'Item[0].Count=3',
            'Item[1].FormID=0000B001', 'Item[1].Count=1'], out, remap=_ONE_SLOT_UP)
        assert out['record_items'] == {'02000C01': (('0000B001', 1),)}

    def test_no_item_is_never_a_listed_item(self, xref):
        """An item that cannot be told has no id; an entry without one is not its match."""
        graph = CrossRefGraph()
        graph.record_items['0000C001'] = (('', 3), ('0000B001', 1))
        assert graph.listed_item_count(['0000C001'], '') == 0
        assert graph.listed_item_count(['0000C001'], '0000B001') == 1
        assert xref.listed_item_count(['0000C001'], '') == 0

    def test_a_missing_or_unreadable_count_is_no_count(self, xref):
        """An entry without a usable count is listed with 0, which never becomes a read."""
        graph = CrossRefGraph()
        _index_xref_record(
            graph, '0000C001',
            {'Signature': 'CONT', 'FormID': '0000C001', 'ItemCount': '2',
             'Item[0].FormID': '0000B001', 'Item[1].FormID': '0000B002',
             'Item[1].Count': 'x'}, lambda v, own_raw, own_key: v)
        assert graph.record_items == {'0000C001': (('0000B001', 0), ('0000B002', 0))}
        assert graph.listed_item_count(['0000C001'], '0000B001') == 0


#: `AuthoredItemCount` in TES4Polyfill.psc from its signature to its EndFunction, whitespace aside.
_FUNCTION = (
    'Int Function AuthoredItemCount(ObjectReference akRef, Form akItem, Int aiAuthored) Global',
    'If akRef == None',
    'Return 0',
    'EndIf',
    'Int count = akRef.GetItemCount(akItem)',
    'If count != 0 || aiAuthored <= 0',
    'Return count',
    'EndIf',
    'If akRef.GetNumItems() > 0 && akRef.GetContainerForms().Length == 0 && akRef.GetNumItems() > 0',
    'If akRef.GetItemCount(akItem) == 0',
    'Return aiAuthored',
    'EndIf',
    'EndIf',
    'Return akRef.GetItemCount(akItem)',
    'EndFunction',
)

#: The item asked for, and the count its record lists.
_ITEM, _LISTED = 'item', 3


class _Inventory:
    """A reference's inventory as the three natives answer for it.

    `listed` is what the base record lists; `changes` what the reference holds
    on top, None while the inventory was never built.  Then the native count
    is 0 and GetContainerForms is empty, while GetNumItems still counts the
    record's entries (the last two as the script extender's source reads).
    """

    def __init__(self, listed=(), changes=None):
        """Start in one state; `calls` counts the natives asked."""
        self.listed, self.calls = dict(listed), 0
        self.changes = None if changes is None else dict(changes)

    def held(self, form) -> int:
        """What Oblivion answers: the listed count and every change since."""
        return max(0, self.listed.get(form, 0) + (self.changes or {}).get(form, 0))

    def _forms(self, built_only: bool = False) -> list:
        """One native asked: the forms held with a count above 0; none for `built_only` on one never built."""
        self.calls += 1
        if built_only and self.changes is None:
            return []
        return [form for form in {**self.listed, **(self.changes or {})} if self.held(form) > 0]

    def GetItemCount(self, form) -> int:
        """The native count: nothing at all while the inventory was never built."""
        return self.held(form) if form in self._forms(built_only=True) else 0

    def GetNumItems(self) -> int:
        """How many forms are held, the record's own included in either state."""
        return len(self._forms())

    def GetContainerForms(self) -> list:
        """The forms held; none while the reference carries no changes."""
        return self._forms(built_only=True)


#: State -> (what the record lists, the changes or None for never built, the true count, native calls made).
_STATES = {
    'never built': ({_ITEM: _LISTED, 'other': 1}, None, _LISTED, 5),
    'never built, the item alone': ({_ITEM: _LISTED}, None, _LISTED, 5),
    'built, untouched': ({_ITEM: _LISTED, 'other': 1}, {}, _LISTED, 1),
    'built, more of the item put in': ({_ITEM: _LISTED}, {_ITEM: 2}, _LISTED + 2, 1),
    'item taken, another left': ({_ITEM: _LISTED, 'other': 1}, {_ITEM: -_LISTED}, 0, 4),
    'everything taken': ({_ITEM: _LISTED, 'other': 1}, {_ITEM: -_LISTED, 'other': -1}, 0, 3),
    'emptied, then something else put in': ({_ITEM: _LISTED}, {_ITEM: -_LISTED, 'extra': 1}, 0, 4),
    'not a container': ({}, None, 0, 3),
}


def _authored_item_count(ref, form, authored: int) -> int:
    """`_FUNCTION` statement for statement, over an `_Inventory`; None is no reference."""
    if ref is None:
        return 0
    count = ref.GetItemCount(form)
    if count != 0 or authored <= 0:
        return count
    if ref.GetNumItems() > 0 and len(ref.GetContainerForms()) == 0 and ref.GetNumItems() > 0:
        if ref.GetItemCount(form) == 0:
            return authored
    return ref.GetItemCount(form)


class TestPolyfill:
    """The runtime half: `TES4Polyfill.AuthoredItemCount`."""

    def test_the_function_is_exactly_this(self):
        """Line for line; whoever changes it changes `_authored_item_count` and its table too."""
        lines = [' '.join(line.split()) for line in _POLYFILL.read_text(encoding='utf-8').split('\n')]
        start = lines.index(_FUNCTION[0])
        assert tuple(lines[start:lines.index('EndFunction', start) + 1]) == _FUNCTION
        assert lines.count(_FUNCTION[0]) == 1

    @pytest.mark.parametrize('state', _STATES)
    def test_each_state_reads_its_true_count(self, state):
        """Never built: the listed count.  Built: the native count, whatever it is."""
        listed, changes, true, calls = _STATES[state]
        inv = _Inventory(listed, changes)
        assert inv.held(_ITEM) == true
        assert _authored_item_count(inv, _ITEM, _LISTED) == true
        assert inv.calls == calls

    def test_no_reference_and_no_authored_count(self):
        """None holds nothing; without an authored count the native count stands; N is the one handed in."""
        assert _authored_item_count(None, _ITEM, _LISTED) == 0
        for authored in (0, -1):
            assert _authored_item_count(_Inventory({_ITEM: _LISTED}), _ITEM, authored) == 0
            assert _authored_item_count(_Inventory({_ITEM: _LISTED}, {}), _ITEM, authored) == _LISTED
        assert _authored_item_count(_Inventory({_ITEM: _LISTED}), _ITEM, 7) == 7
        assert _authored_item_count(_Inventory({_ITEM: _LISTED}, {}), _ITEM, 7) == _LISTED
