"""The crime pass: bounty realms, jails, and the arrest force-greet topic."""

import struct

from tes5_import.dialogue import arrest
from tes5_import.record_types import crime

#: This plugin's own index byte when it has no masters.
_OWN = 0x00000000


def _fid(n: int) -> str:
    """A raw export FormID string for this (masterless) plugin."""
    return '%08X' % (_OWN | n)


def _cell(n, world=None, interior=False, edid=''):
    """A CELL record."""
    rec = {'Signature': 'CELL', 'FormID': _fid(n), 'EditorID': edid or f'C{n}',
           'DATA.Flags': '1' if interior else '0'}
    if world:
        rec['ParentWRLD'] = _fid(world)
    return rec


def _ref(n, cell, base, door=None, x=0.0, y=0.0):
    """A REFR record, optionally a teleport door into `door`."""
    rec = {'Signature': 'REFR', 'FormID': _fid(n), 'ParentCELL': _fid(cell),
           'NAME': '%08X' % base, 'PosX': str(x), 'PosY': str(y),
           'RecordFlags': '0'}
    if door:
        rec['XTEL.Door'] = _fid(door)
    return rec


def _world_with_isles() -> dict:
    """Two root worldspaces, a border door into the second, and one jail."""
    toggle = r'scn Door\r\nBegin OnActivate\r\n\tsetPlayerInSEWorld 1\r\n\tActivate\r\nend'
    return {
        'WRLD': [{'Signature': 'WRLD', 'FormID': _fid(0x10), 'EditorID': 'Main',
                  'FULL': 'Mainland'},
                 {'Signature': 'WRLD', 'FormID': _fid(0x11), 'EditorID': 'Isles',
                  'FULL': 'Isles'}],
        'CELL': [_cell(0x20, world=0x10), _cell(0x21, world=0x11),
                 _cell(0x30, interior=True, edid='Gate'),
                 _cell(0x31, interior=True, edid='Jail')],
        'SCPT': [{'Signature': 'SCPT', 'FormID': _fid(0x40), 'SCTX': toggle}],
        'DOOR': [{'Signature': 'DOOR', 'FormID': _fid(0x41), 'SCRI': _fid(0x40)},
                 {'Signature': 'DOOR', 'FormID': _fid(0x42)}],
        'CONT': [{'Signature': 'CONT', 'FormID': _fid(0x11), 'EditorID': 'StolenGoods'}],
        'REFR': [
            _ref(0x50, 0x20, 0x41, door=0x51, x=100, y=200),
            _ref(0x51, 0x30, 0x42, door=0x50),
            _ref(0x52, 0x30, 0x42, door=0x53),
            _ref(0x53, 0x21, 0x42, door=0x52, x=5, y=6),
            _ref(0x60, 0x20, 0x04, door=0x61, x=1000, y=2000),
            _ref(0x61, 0x31, 0x04, door=0x60),
            _ref(0x62, 0x31, 0x11),
        ],
    }


def _plan(by_type):
    """A crime plan for a masterless plugin named Test.esm."""
    masters = crime._Masters('', {}, '')
    return crime._Plan(crime._World(by_type, masters, 'Test.esm'), {})


class TestRealms:
    def test_border_door_splits_a_second_realm(self):
        """Cells behind a SetPlayerInSEWorld 1 door form their own realm."""
        plan = _plan(_world_with_isles())
        assert plan.owned == ['TES4CrimeFaction_Test', 'TES4CrimeFaction_Test_Isles']
        assert plan.cell_realm(int(_fid(0x21), 16)) == 'TES4CrimeFaction_Test_Isles'
        assert plan.cell_realm(int(_fid(0x30), 16)) == 'TES4CrimeFaction_Test_Isles'
        assert plan.cell_realm(int(_fid(0x20), 16)) == 'TES4CrimeFaction_Test'

    def test_without_a_border_it_is_one_realm(self):
        """With no toggling door script, every root worldspace shares one bounty."""
        by_type = _world_with_isles()
        by_type['SCPT'][0]['SCTX'] = r'scn Door\r\nBegin OnActivate\r\n\tActivate\r\nend'
        assert _plan(by_type).owned == ['TES4CrimeFaction_Test']

    def test_unplaced_interior_takes_the_default_realm(self):
        """An interior no door reaches reports to the main realm."""
        plan = _plan(_world_with_isles())
        assert plan.cell_realm(int(_fid(0x31), 16)) == 'TES4CrimeFaction_Test'


class TestRenamedWorldspace:
    def test_master_world_renamed_for_this_plugin_is_its_own(self):
        """Arktwend's cells on Morrowind's grid form its own realm once renamed."""
        from core.worldspace_names import set_worldspace_plugins
        masters = crime._Masters('', {}, '')
        masters.names = ['Morrowind_ob.esm']
        masters.records = {'00380000': {'Signature': 'WRLD',
                                        'EditorID': 'WrldMorrowind'}}
        masters.maps, masters.sidecars = [{0: 0}], [{'default': 'TES4CrimeFaction_Morrowind_ob'}]
        masters.renames = [{}]
        cell = {'Signature': 'CELL', 'FormID': '01000020', 'EditorID': 'C',
                'ParentWRLD': '00380000', 'DATA.Flags': '0'}
        set_worldspace_plugins(['Arktwend_English.esm', 'Morrowind_ob.esm'])
        try:
            plan = crime._Plan(crime._World({'CELL': [cell]}, masters,
                                            'Arktwend_English.esm'), {})
        finally:
            set_worldspace_plugins([])
        assert plan.owned == ['TES4CrimeFaction_Arktwend_English']


class TestJails:
    def test_prison_marker_into_an_interior_is_a_jail_with_its_chest(self):
        """The marker teleporting into another interior is a jail; its chest is there."""
        jails = _plan(_world_with_isles()).jails
        assert [(j['marker'], j['chest']) for j in jails] == [(0x60, 0x62)]
        assert (jails[0]['x'], jails[0]['y']) == (1000.0, 2000.0)
        assert jails[0]['realm'] == 'TES4CrimeFaction_Test'

    def test_morroblivion_mangled_chest_name_folds(self):
        """All three engines' evidence-chest ids fold to one key."""
        assert crime._fold('0stolenUgoods') == crime._fold('stolen_goods') == 'stolengoods'

    def test_interior_anchor_is_the_door_onto_the_world(self):
        """An interior's anchor is where its door opens onto a worldspace."""
        anchors = _plan(_world_with_isles()).anchors
        assert anchors[0x30][0] in (0x10, 0x11)


class _Writer:
    """The one PluginWriter call the return-marker pass makes."""

    def derive_formid(self, site, key):
        """A fixed derived id, recording what it was keyed on."""
        self.keyed = (site, key)
        return 0x00ABCDEF


class TestReturnMarkers:
    def test_paired_markers_get_no_return_marker(self):
        """A jail marker whose target teleports back already releases the prisoner."""
        by_type = _world_with_isles()
        count = len(by_type['REFR'])
        assert crime._link_jail_returns(_plan(by_type), by_type, _Writer()) == 0
        assert len(by_type['REFR']) == count

    def test_one_way_marker_gets_a_persistent_return_marker(self):
        """ServeTime's release needs the target to teleport back, or its loading screen stays up."""
        by_type = _world_with_isles()
        marker = next(r for r in by_type['REFR'] if r['FormID'] == _fid(0x60))
        marker.update({'XTEL.PosX': '7', 'XTEL.PosY': '8', 'XTEL.PosZ': '9'})
        del next(r for r in by_type['REFR'] if r['FormID'] == _fid(0x61))['XTEL.Door']
        writer = _Writer()
        assert crime._link_jail_returns(_plan(by_type), by_type, writer) == 1
        added = by_type['REFR'][-1]
        assert writer.keyed == ('JAIL_RETURN', _fid(0x60))
        assert marker['XTEL.Door'] == added['FormID'] == _fid(0xABCDEF)
        assert added['ParentCELL'] == _fid(0x31)
        assert int(added['RecordFlags']) & 0x400
        assert added['XTEL.Door'] == _fid(0x60)
        assert (added['PosX'], added['PosY'], added['PosZ']) == ('7', '8', '9')
        assert (added['XTEL.PosX'], added['XTEL.PosY']) == ('1000', '2000')


def _ctda(func: int, op: int = 0x00, value: float = 1.0) -> str:
    """A raw TES4 CTDA as the export prints it."""
    return struct.pack('<B3xfHH4x4x', op, value, func, 0).hex()


class TestBountyConditions:
    def test_player_bounty_runs_on_the_guard(self):
        """GetCrimeGold asked of the target (the player) runs on the subject."""
        from tes5_import.base.conditions import convert_ctda
        raw = bytes.fromhex(_ctda(116, 0x42, 0.0))
        out = convert_ctda(raw, offset=0, run_on_target_ref=0x14)
        func, run_on, ref = struct.unpack_from('<H', out, 8)[0], *struct.unpack_from('<II', out, 20)
        assert (func, run_on, ref, out[0] & 0x02) == (459, 0, 0, 0)


class TestArrestTopic:
    def test_is_guard_true_is_required(self):
        """IsGuard == 1 and IsGuard != 0 both mark a guard-only line."""
        assert arrest.requires_guard({'Condition[0].Raw': _ctda(125)})
        assert arrest.requires_guard({'Condition[0].Raw': _ctda(125, 0x20, 0.0)})

    def test_or_or_false_is_not(self):
        """An OR'd, false or unrelated condition does not."""
        assert not arrest.requires_guard({'Condition[0].Raw': _ctda(125, 0x01)})
        assert not arrest.requires_guard({'Condition[0].Raw': _ctda(125, 0x00, 0.0)})
        assert not arrest.requires_guard({'Condition[0].Raw': _ctda(72)})

    def test_shared_copy_names_the_greeting_and_drops_its_response(self):
        """The copy keeps choices and conditions, shares the response via DNAM."""
        body = b''.join(sig + struct.pack('<H', len(d)) + d for sig, d in (
            (b'ENAM', b'\x01\x00\x10\x05'), (b'CNAM', b'\x00'),
            (b'TCLT', b'\x01\x00\x00\x00'), (b'TRDT', b'\x00' * 24),
            (b'NAM1', b'hi\x00'), (b'NAM2', b'\x00'), (b'NAM3', b'\x00'),
            (b'CTDA', b'\x00' * 32)))
        copy = arrest._shared_copy(body, 0x01000123, 0x01000999, 0)
        subs = [sig for sig, _d in arrest._subrecords(copy[24:])]
        assert subs == [b'ENAM', b'CNAM', b'TCLT', b'DNAM', b'CTDA']
        data = dict(arrest._subrecords(copy[24:]))
        assert data[b'DNAM'] == struct.pack('<I', 0x01000123)
        assert data[b'ENAM'] == b'\x01\x00\x00\x00'
        assert struct.unpack_from('<I', copy, 12)[0] == 0x01000999
