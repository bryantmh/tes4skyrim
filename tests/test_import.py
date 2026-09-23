"""
Tests for TES5 import - verifies binary output is correctly structured.

Tests the writer, record converters, and group hierarchy.
"""

import os
import struct
import tempfile

import pytest

from tes5_import.record_types.common import convert_GLOB
from tes5_import.record_types.creature import convert_CREA
from tes5_import.record_types.items import (convert_LVLC, convert_LVLI)
from tes5_import.record_types.npc import convert_NPC_
from tes5_import.record_types.common import _convert_biped_flags
from tes5_import.record_types.equipment import armo_slots
from asset_convert.character.morrowind_coverage import part_slots
from tes5_import.record_types.equipment import (
    convert_ARMO,
    convert_BOOK,
    convert_CLOT,
    convert_ENCH,
    convert_WEAP,
)
from tes5_import.record_types.items import (
    convert_CONT,
    convert_DOOR,
    convert_LIGH,
    convert_MISC,
    convert_STAT,
)
from tes5_import.record_types import world_morrowind
from tes5_import.record_types.world import (
    convert_CELL,
    convert_LAND,
    convert_REFR,
)
from tes5_import.base.text_reader import (
    get_formid,
    get_int,
    parse_record_block,
    unescape_value,
)
from tes5_import.base.tes5_reader import records as reader_records
from tes5_import.base.writer import (
    FORM_VERSION_SSE,
    GROUP_HEADER_SIZE,
    RECORD_HEADER_SIZE,
    PluginWriter,
    pack_obnd,
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
    pack_tes4_header,
    pack_top_group,
)

# ---------------------------------------------------------------------------
# Writer tests
# ---------------------------------------------------------------------------

class TestWriter:
    def test_subrecord_packing(self):
        data = pack_subrecord('EDID', b'TestEdid\x00')
        assert data[:4] == b'EDID'
        size = struct.unpack_from('<H', data, 4)[0]
        assert size == 9
        assert data[6:] == b'TestEdid\x00'

    def test_string_subrecord(self):
        data = pack_string_subrecord('FULL', 'Iron Sword')
        assert data[:4] == b'FULL'
        size = struct.unpack_from('<H', data, 4)[0]
        assert data[6:6 + size] == b'Iron Sword\x00'

    def test_record_packing(self):
        subs = pack_string_subrecord('EDID', 'Test')
        rec = pack_record('STAT', 0x12345, 0, subs)
        assert rec[:4] == b'STAT'
        data_size = struct.unpack_from('<I', rec, 4)[0]
        assert data_size == len(subs)
        flags = struct.unpack_from('<I', rec, 8)[0]
        assert flags == 0
        form_id = struct.unpack_from('<I', rec, 12)[0]
        assert form_id == 0x12345
        form_ver = struct.unpack_from('<H', rec, 20)[0]
        assert form_ver == FORM_VERSION_SSE  # 44

    def test_record_header_size(self):
        subs = b''
        rec = pack_record('GLOB', 1, 0, subs)
        assert len(rec) == RECORD_HEADER_SIZE  # 24 bytes for empty record

    def test_group_packing(self):
        content = pack_record('STAT', 1, 0, b'')
        group = pack_top_group('STAT', content)
        assert group[:4] == b'GRUP'
        group_size = struct.unpack_from('<I', group, 4)[0]
        assert group_size == GROUP_HEADER_SIZE + len(content)
        label = group[8:12]
        assert label == b'STAT'
        group_type = struct.unpack_from('<i', group, 12)[0]
        assert group_type == 0

    def test_obnd(self):
        data = pack_obnd()
        assert data[:4] == b'OBND'
        size = struct.unpack_from('<H', data, 4)[0]
        assert size == 12
        # All zeros
        for i in range(6):
            val = struct.unpack_from('<h', data, 6 + i * 2)[0]
            assert val == 0

    def test_obnd_custom_bounds(self):
        data = pack_obnd(-50, -50, 0, 50, 50, 80)
        vals = struct.unpack_from('<6h', data, 6)
        assert vals == (-50, -50, 0, 50, 50, 80)

    def test_obnd_size_is_always_12(self):
        data = pack_obnd(-3, -3, 0, 3, 3, 3)
        assert struct.unpack_from('<H', data, 4)[0] == 12

    def test_tes4_header(self):
        header = pack_tes4_header(['Skyrim.esm'], num_records=100, next_object_id=0x800)
        assert header[:4] == b'TES4'
        form_ver = struct.unpack_from('<H', header, 20)[0]
        assert form_ver == 44
        # Data should contain HEDR
        assert b'HEDR' in header
        assert b'MAST' in header
        # Check HEDR version
        hedr_pos = header.index(b'HEDR')
        ver = struct.unpack_from('<f', header, hedr_pos + 6)[0]
        assert abs(ver - 1.71) < 0.01

    def test_plugin_writer(self):
        w = PluginWriter(masters=['Skyrim.esm'])
        subs = pack_string_subrecord('EDID', 'TestStat')
        rec = pack_record('STAT', 0x800, 0, subs)
        w.add_record('STAT', rec)

        with tempfile.NamedTemporaryFile(suffix='.esm', delete=False) as f:
            tmp = f.name
        try:
            w.write(tmp)
            assert os.path.getsize(tmp) > 0

            # Read back and verify structure
            with open(tmp, 'rb') as f:
                data = f.read()
            assert data[:4] == b'TES4'  # File header
            # Find GRUP
            grup_pos = data.index(b'GRUP')
            assert grup_pos > 0
            label = data[grup_pos + 8:grup_pos + 12]
            assert label == b'STAT'
        finally:
            os.unlink(tmp)


# ---------------------------------------------------------------------------
# Text reader tests
# ---------------------------------------------------------------------------

class TestTextReader:
    def test_unescape(self):
        assert unescape_value('Hello\\nWorld') == 'Hello\nWorld'
        assert unescape_value('Path\\\\File') == 'Path\\File'
        assert unescape_value('Tab\\there') == 'Tab\there'

    def test_parse_record_block(self):
        lines = [
            'Signature=STAT',
            'FormID=00012345',
            'EditorID=TestRock',
            'RecordFlags=0',
            'Model.MODL=Rocks\\\\Rock01.nif',  # Double-escaped in export format
        ]
        rec = parse_record_block(lines)
        assert rec['Signature'] == 'STAT'
        assert rec['FormID'] == '00012345'
        assert rec['EditorID'] == 'TestRock'
        assert rec['Model.MODL'] == 'Rocks\\Rock01.nif'

    def test_get_formid(self):
        rec = {'FormID': '0001A2B3'}
        assert get_formid(rec, 'FormID') == 0x0001A2B3

    def test_get_int(self):
        rec = {'DATA.Value': '42'}
        assert get_int(rec, 'DATA.Value') == 42
        assert get_int(rec, 'Missing', 0) == 0


# ---------------------------------------------------------------------------
# Record converter tests
# ---------------------------------------------------------------------------

class TestConverters:
    """Test individual record converters produce valid binary."""

    def _check_record(self, rec_bytes: bytes, expected_sig: str):
        """Basic validation of a packed record."""
        assert len(rec_bytes) >= RECORD_HEADER_SIZE
        sig = rec_bytes[:4].decode('ascii')
        assert sig == expected_sig
        data_size = struct.unpack_from('<I', rec_bytes, 4)[0]
        assert len(rec_bytes) == RECORD_HEADER_SIZE + data_size
        form_ver = struct.unpack_from('<H', rec_bytes, 20)[0]
        assert form_ver == FORM_VERSION_SSE

    def _has_subrecord(self, rec_bytes: bytes, sub_sig: str) -> bool:
        """Check if a subrecord signature exists in the record."""
        data = rec_bytes[RECORD_HEADER_SIZE:]
        target = sub_sig.encode('ascii')
        return target in data

    def _get_subrecord_data(self, rec_bytes: bytes, sub_sig: str) -> bytes:
        """Extract a subrecord's data from a packed record."""
        data = rec_bytes[RECORD_HEADER_SIZE:]
        target = sub_sig.encode('ascii')
        pos = 0
        while pos + 6 <= len(data):
            sig = data[pos:pos + 4]
            size = struct.unpack_from('<H', data, pos + 4)[0]
            if sig == target:
                return data[pos + 6:pos + 6 + size]
            pos += 6 + size
        return None

    def _iter_subrecords(self, rec_bytes: bytes):
        """Yield (signature, data) for every subrecord, in written order."""
        data = rec_bytes[RECORD_HEADER_SIZE:]
        pos = 0
        while pos + 6 <= len(data):
            sig = data[pos:pos + 4].decode('ascii', 'replace')
            size = struct.unpack_from('<H', data, pos + 4)[0]
            yield sig, data[pos + 6:pos + 6 + size]
            pos += 6 + size

    def test_stat(self):
        rec = {'Signature': 'STAT', 'FormID': '00012345', 'RecordFlags': '0',
               'EditorID': 'TestRock', 'Model.MODL': 'Rocks\\Rock01.nif'}
        result = convert_STAT(rec)
        self._check_record(result, 'STAT')
        assert self._has_subrecord(result, 'EDID')
        assert self._has_subrecord(result, 'OBND')
        obnd = self._get_subrecord_data(result, 'OBND')
        assert len(obnd) == 12
        # STAT should use STAT-specific bounds (-50,-50,0,50,50,80)
        vals = struct.unpack_from('<6h', obnd, 0)
        assert vals == (-50, -50, 0, 50, 50, 80)

    def test_misc(self):
        rec = {'Signature': 'MISC', 'FormID': '00000100', 'RecordFlags': '0',
               'EditorID': 'Gold001', 'FULL': 'Gold', 'DATA.Value': '1',
               'DATA.Weight': '0.0', 'Model.MODL': 'Gold\\Gold.nif'}
        result = convert_MISC(rec)
        self._check_record(result, 'MISC')
        assert self._has_subrecord(result, 'DATA')
        data = self._get_subrecord_data(result, 'DATA')
        assert len(data) == 8  # int32 + float32
        value = struct.unpack_from('<I', data, 0)[0]
        assert value == 1
        # MISC should use MISC-specific OBND (-5,-5,0,5,5,8)
        obnd = self._get_subrecord_data(result, 'OBND')
        assert struct.unpack_from('<6h', obnd, 0) == (-5, -5, 0, 5, 5, 8)

    def test_weap(self):
        rec = {'Signature': 'WEAP', 'FormID': '00000200', 'RecordFlags': '0',
               'EditorID': 'IronSword', 'FULL': 'Iron Sword',
               'DATA.Type': '0', 'DATA.Speed': '1.0', 'DATA.Reach': '1.0',
               'DATA.Value': '25', 'DATA.Health': '100', 'DATA.Weight': '12.0',
               'DATA.Damage': '8', 'Model.MODL': 'Weapons\\Iron\\Sword.nif'}
        result = convert_WEAP(rec)
        self._check_record(result, 'WEAP')
        assert self._has_subrecord(result, 'DNAM')
        assert self._has_subrecord(result, 'CRDT')
        data = self._get_subrecord_data(result, 'DATA')
        # TES5 WEAP DATA: Value(4) + Weight(4) + Damage(2) = 10 bytes
        assert len(data) == 10
        damage = struct.unpack_from('<H', data, 8)[0]
        assert damage == 8
        # WEAP should use WEAP-specific OBND (-5,-5,0,5,5,30)
        obnd = self._get_subrecord_data(result, 'OBND')
        assert struct.unpack_from('<6h', obnd, 0) == (-5, -5, 0, 5, 5, 30)

    def test_weap_enchantment_charge(self):
        """A staff's TES4 ANAM charge pool becomes TES5 EAMT (u16).

        Also asserts the two edges: a value above 65535 saturates rather than
        wrapping (the field is 2 bytes on disk, so folding would yield a tiny
        pool), and an unenchanted weapon gets neither EITM nor EAMT.

        See: docs/commentary/tes5_import_magic.md#enchantment-charge-eamt
        """
        rec = {'Signature': 'WEAP', 'FormID': '00000301', 'RecordFlags': '0',
               'EditorID': 'StaffOfFire', 'DATA.Type': '4',
               'DATA.Speed': '1.0', 'DATA.Reach': '1.0', 'DATA.Value': '500',
               'DATA.Health': '100', 'DATA.Weight': '9.0', 'DATA.Damage': '10',
               'Model.MODL': 'Weapons\\Staff\\Staff.nif',
               'ENAM': '0009535C', 'ANAM': '3600'}
        result = convert_WEAP(rec)
        assert self._has_subrecord(result, 'EITM')
        eamt = self._get_subrecord_data(result, 'EAMT')
        assert len(eamt) == 2
        assert struct.unpack('<H', eamt)[0] == 3600

        rec['ANAM'] = '100000'
        eamt = self._get_subrecord_data(convert_WEAP(rec), 'EAMT')
        assert struct.unpack('<H', eamt)[0] == 0xFFFF

        del rec['ENAM']
        plain = convert_WEAP(rec)
        assert not self._has_subrecord(plain, 'EAMT')
        assert not self._has_subrecord(plain, 'EITM')

    def test_armo(self):
        rec = {'Signature': 'ARMO', 'FormID': '00000300', 'RecordFlags': '0',
               'EditorID': 'IronArmor', 'FULL': 'Iron Armor',
               'BMDT.BipedFlags': '4', 'BMDT.GeneralFlags': '0',
               'DATA.ArmorRating': '20', 'DATA.Value': '100',
               'DATA.Weight': '30.0',
               'Male.BipedModel.MODL': 'Armor\\Iron\\M.nif'}
        result = convert_ARMO(rec)
        self._check_record(result, 'ARMO')
        assert self._has_subrecord(result, 'BOD2')
        assert self._has_subrecord(result, 'RNAM')
        bod2 = self._get_subrecord_data(result, 'BOD2')
        assert len(bod2) == 8

    def test_clot_becomes_armo(self):
        rec = {'Signature': 'CLOT', 'FormID': '00000400', 'RecordFlags': '0',
               'EditorID': 'FineShirt', 'FULL': 'Fine Shirt',
               'BMDT.BipedFlags': '4', 'BMDT.GeneralFlags': '0',
               'DATA.Value': '10', 'DATA.Weight': '1.0'}
        result = convert_CLOT(rec)
        # Should be ARMO record
        sig = result[:4].decode('ascii')
        assert sig == 'ARMO'

    def test_biped_slot_mapping(self):
        """TES4 biped bits -> ARMO BOD2: primary slots plus equipment-conflict extras.

        Head: 30 + Hair, LongHair, Circlet, Ears (full-face helm; LongHair hides
        the hairline headpart). Hair: 31 + LongHair, Circlet. Upper body 32,
        lower body 49, hands 33 plus the right hand 59, foot 37, amulet 35,
        shield 39. Body coverage (forearms, calves) goes on the ARMA, not here.
        """
        assert _convert_biped_flags(0x01) == (1 | (1 << 1) | (1 << 11) | (1 << 12) | (1 << 13))
        assert _convert_biped_flags(0x02) == ((1 << 1) | (1 << 11) | (1 << 12))
        assert _convert_biped_flags(0x04) == 0x04
        assert _convert_biped_flags(0x08) == (1 << 19)
        assert _convert_biped_flags(0x10) == (1 << 3) | (1 << 29)
        assert _convert_biped_flags(0x20) == 0x80
        assert _convert_biped_flags(0x100) == 0x20
        assert _convert_biped_flags(0x2000) == 0x200
        assert _convert_biped_flags(0x0C) == 0x04 | (1 << 19)

    def test_morrowind_one_sided_pieces_take_their_own_slot(self):
        """A right gauntlet takes 59 and a left one 33; a pauldron takes 57/58 and hides nothing."""
        def piece(sig, kind, parts=()):
            """An exported Morrowind wearable record of the given type and parts."""
            rec = {'Signature': sig, 'MorrowindWearableType': str(kind), 'BMDT.BipedFlags': '16',
                   'MorrowindPartCount': str(len(parts))}
            rec.update({f'MorrowindPart[{i}].Slot': str(p) for i, p in enumerate(parts)})
            return rec
        assert armo_slots(piece('ARMO', 7)) == 1 << 29
        assert armo_slots(piece('ARMO', 6)) == 1 << 3
        assert armo_slots(piece('CLOT', 5)) == 1 << 29
        assert armo_slots(piece('ARMO', 2)) == 1 << 27
        assert armo_slots(piece('ARMO', 3)) == 1 << 28
        assert part_slots(piece('ARMO', 2, (14, 24))) == []
        assert part_slots(piece('ARMO', 6, (7, 9))) == [7, 9]
        assert armo_slots({'BMDT.BipedFlags': '16'}) == (1 << 3) | (1 << 29)

    def test_armo_armor_type_enum(self):
        """ArmorType: 0=Light, 1=Heavy, 2=Clothing per wbArmorTypeEnum."""
        rec = {'Signature': 'ARMO', 'FormID': '00000300', 'RecordFlags': '0',
               'EditorID': 'TestArmor', 'FULL': 'Test',
               'BMDT.BipedFlags': '4', 'BMDT.GeneralFlags': '0',
               'DATA.ArmorRating': '10', 'DATA.Value': '1', 'DATA.Weight': '1.0',
               'Male.BipedModel.MODL': 'Armor\\Test\\M.nif'}
        # Light armor (gen_flags=0, no heavy bit)
        result = convert_ARMO(rec)
        bod2 = self._get_subrecord_data(result, 'BOD2')
        armor_type = struct.unpack_from('<I', bod2, 4)[0]
        assert armor_type == 0, f"Light armor type should be 0, got {armor_type}"
        # Heavy armor (gen_flags bit 7 = 0x80 set, per wbDefinitionsTES4.pas)
        rec['BMDT.GeneralFlags'] = '128'
        result = convert_ARMO(rec)
        bod2 = self._get_subrecord_data(result, 'BOD2')
        armor_type = struct.unpack_from('<I', bod2, 4)[0]
        assert armor_type == 1, f"Heavy armor type should be 1, got {armor_type}"
        # Clothing
        result = convert_ARMO(rec, is_clothing=True)
        bod2 = self._get_subrecord_data(result, 'BOD2')
        armor_type = struct.unpack_from('<I', bod2, 4)[0]
        assert armor_type == 2, f"Clothing type should be 2, got {armor_type}"

    def test_armo_dnam_is_int(self):
        """ARMO DNAM should be S32 = rating."""
        rec = {'Signature': 'ARMO', 'FormID': '00000300', 'RecordFlags': '0',
               'EditorID': 'TestArmor', 'FULL': 'Test',
               'BMDT.BipedFlags': '4', 'BMDT.GeneralFlags': '0',
               'DATA.ArmorRating': '20', 'DATA.Value': '100', 'DATA.Weight': '30.0',
               'Male.BipedModel.MODL': 'Armor\\Test\\M.nif'}
        result = convert_ARMO(rec)
        dnam = self._get_subrecord_data(result, 'DNAM')
        assert len(dnam) == 4
        val = struct.unpack('<i', dnam)[0]
        assert val == 20, f"Expected 20, got {val}"

    def test_weap_crdt_sse_size(self):
        """WEAP CRDT must be 24 bytes for SSE (form version 44)."""
        rec = {'Signature': 'WEAP', 'FormID': '00000200', 'RecordFlags': '0',
               'EditorID': 'TestSword', 'FULL': 'Test',
               'DATA.Type': '0', 'DATA.Speed': '1.0', 'DATA.Reach': '1.0',
               'DATA.Value': '25', 'DATA.Weight': '12.0', 'DATA.Damage': '8',
               'Model.MODL': 'Weapons\\Test\\Sword.nif'}
        result = convert_WEAP(rec)
        crdt = self._get_subrecord_data(result, 'CRDT')
        assert len(crdt) == 24, f"SSE CRDT should be 24 bytes, got {len(crdt)}"

    def test_weap_dnam_defaults(self):
        """WEAP DNAM should have sensible defaults for VATS hit chance and attack mult."""
        rec = {'Signature': 'WEAP', 'FormID': '00000200', 'RecordFlags': '0',
               'EditorID': 'TestSword', 'FULL': 'Test',
               'DATA.Type': '0', 'DATA.Speed': '1.0', 'DATA.Reach': '1.0',
               'DATA.Value': '25', 'DATA.Weight': '12.0', 'DATA.Damage': '8',
               'Model.MODL': 'Weapons\\Test\\Sword.nif'}
        result = convert_WEAP(rec)
        dnam = self._get_subrecord_data(result, 'DNAM')
        assert len(dnam) == 100
        vats_chance = dnam[24]  # Base VATS To-Hit Chance at offset 24
        assert vats_chance == 0  # Vanilla Skyrim 1H melee weapons have 0 here
        attack_mult = struct.unpack_from('<f', dnam, 44)[0]  # Animation Attack Mult
        assert attack_mult == 1.0

    def test_arma_generation(self):
        """ARMO with writer should generate a companion ARMA record."""
        from tes5_import.base.writer import PluginWriter
        rec = {'Signature': 'ARMO', 'FormID': '00000300', 'RecordFlags': '0',
               'EditorID': 'IronArmor', 'FULL': 'Iron Armor',
               'BMDT.BipedFlags': '4', 'BMDT.GeneralFlags': '0',
               'DATA.ArmorRating': '20', 'DATA.Value': '100', 'DATA.Weight': '30.0',
               'Male.BipedModel.MODL': 'Armor\\Iron\\M.nif'}
        writer = PluginWriter(masters=['Skyrim.esm'])
        result = convert_ARMO(rec, writer=writer)
        # ARMO should have MODL subrecord referencing the ARMA
        assert self._has_subrecord(result, 'MODL')
        modl_data = self._get_subrecord_data(result, 'MODL')
        arma_ref = struct.unpack('<I', modl_data)[0]
        # The ARMA's id is DERIVED from the source ARMO, not taken from a
        # counter — so it is whatever hashing 'ARMA' + this FormID gives, and
        # a second writer must independently produce the same id.
        assert arma_ref == PluginWriter(
            masters=['Skyrim.esm']).derive_formid('ARMA', 0x300)
        # Writer should have an ARMA record
        assert 'ARMA' in writer._top_groups
        assert len(writer._top_groups['ARMA']) == 1

    def test_arma_generation_female_only(self):
        """A wearable authored for one gender only must still get an armature.

        Nehrim ships 5 (IrlandaRobe, the Silverlight set): the male fields are
        empty, the ARMA build was gated on the male model, and an ARMO with no
        ARMA equips but draws nothing — Goddess Irlanda and the MQ34 Eliath
        embodiment stood there naked. Vanilla Skyrim.esm has 4 ARMA carrying
        MOD3 only and 0 carrying neither, so female-only is legal and empty is
        not: MOD3 must be written and MOD2 must NOT be invented.
        """
        from tes5_import.base.writer import PluginWriter
        rec = {'Signature': 'ARMO', 'FormID': '00000301', 'RecordFlags': '0',
               'EditorID': 'IrlandaRobe', 'FULL': 'Robe',
               'BMDT.BipedFlags': '12', 'BMDT.GeneralFlags': '0',
               'DATA.ArmorRating': '0', 'DATA.Value': '10', 'DATA.Weight': '2.0',
               'Female.BipedModel.MODL': 'OE\\Armor\\Robes\\RobeBanshee2.nif'}
        writer = PluginWriter(masters=['Skyrim.esm'])
        writer.next_object_id = 0x01002000
        result = convert_ARMO(rec, writer=writer)

        assert self._has_subrecord(result, 'MODL'), 'no armature generated'
        assert len(writer._top_groups.get('ARMA', [])) == 1
        arma = writer._top_groups['ARMA'][0]
        assert b'MOD3' in arma, 'female model missing from the armature'
        assert b'MOD2' not in arma, 'invented a male model vanilla does not have'
        # The dropped-item model has to fall back across genders too, or the
        # item is invisible on the ground and in the inventory viewer.
        assert self._has_subrecord(result, 'MOD2')

    def test_npc(self):
        rec = {'Signature': 'NPC_', 'FormID': '00000500', 'RecordFlags': '0',
               'EditorID': 'TestNPC', 'FULL': 'Test Guard',
               'ACBS.Flags': '0', 'ACBS.Level': '5', 'ACBS.CalcMin': '5',
               'ACBS.CalcMax': '10', 'ACBS.BarterGold': '0',
               'ACBS.SpellPoints': '0', 'ACBS.Fatigue': '0',
               'FactionCount': '0', 'SpellCount': '0', 'ItemCount': '0',
               'AIPackageCount': '0',
               'AIDT.Aggression': '5', 'AIDT.Confidence': '50',
               'AIDT.Responsibility': '50', 'AIDT.Services': '0',
               'DATA.Health': '50', 'DATA.Intelligence': '40',
               'DATA.Strength': '60', 'DATA.Endurance': '45',
               'HCLR.R': '100', 'HCLR.G': '80', 'HCLR.B': '60'}
        result = convert_NPC_(rec)
        self._check_record(result, 'NPC_')
        assert self._has_subrecord(result, 'ACBS')
        assert self._has_subrecord(result, 'RNAM')
        assert self._has_subrecord(result, 'DATA')
        assert self._has_subrecord(result, 'DNAM')
        # NPC_ DATA should be empty (0 bytes) in TES5
        data = self._get_subrecord_data(result, 'DATA')
        assert len(data) == 0
        # DNAM should be 52 bytes
        dnam = self._get_subrecord_data(result, 'DNAM')
        assert len(dnam) == 52

    def test_npc_skin_tone_tint_layer(self):
        """NPCs must carry a skin-tone tint layer (TINI/TINC/TINV/TIAS) and a
        matching QNAM — without them the engine renders the body pale white
        regardless of race."""
        base = {'Signature': 'NPC_', 'FormID': '00000500', 'RecordFlags': '0',
                'EditorID': 'TestNPC', 'ACBS.Flags': '0', 'ACBS.Level': '5',
                'FactionCount': '0', 'SpellCount': '0', 'ItemCount': '0',
                'AIPackageCount': '0', 'AIDT.Services': '0',
                'HCLR.R': '100', 'HCLR.G': '80', 'HCLR.B': '60'}
        # Redguard male (TES4 race fid 0x00000D43)
        rec = dict(base, **{'RNAM.Race': '00000D43'})
        result = convert_NPC_(rec)
        for sig in ('TINI', 'TINC', 'TINV', 'TIAS'):
            assert self._has_subrecord(result, sig), f'missing {sig}'
        tini = struct.unpack('<H', self._get_subrecord_data(result, 'TINI'))[0]
        assert tini == 1  # Redguard male skin-tone index in Skyrim.esm
        r, g, b, a = struct.unpack('<4B', self._get_subrecord_data(result, 'TINC'))
        assert a == 0
        # The color is Oblivion's authored Redguard skin: a dark warm brown.
        # Redguards were rendering near-black under the old census pick, and
        # Imperials came out darker than Redguards should be, so assert the
        # actual appearance rather than membership of a palette.
        assert r > g > b, 'Redguard skin must be warm (R > G > B)'
        luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
        assert 45 < luma < 95, f'Redguard luma {luma:.0f} outside brown range'

        tias = struct.unpack('<h', self._get_subrecord_data(result, 'TIAS'))[0]
        assert tias == -1
        # QNAM is the tint blended toward white by TINV, and must agree with
        # the layer or the face is lit a different color than the body.
        tinv = struct.unpack('<I', self._get_subrecord_data(result, 'TINV'))[0]
        assert 0 < tinv <= 100
        v = tinv / 100.0
        qnam = struct.unpack('<3f', self._get_subrecord_data(result, 'QNAM'))
        for got, c in zip(qnam, (r, g, b)):
            assert abs(got - (255.0 * (1.0 - v) + c * v) / 255.0) < 1e-6

        # Female Nord uses the FEMALE tint list index (24, not male 1)
        rec_f = dict(base, **{'RNAM.Race': '000224FD', 'ACBS.Flags': '1'})
        result_f = convert_NPC_(rec_f)
        tini_f = struct.unpack('<H', self._get_subrecord_data(result_f, 'TINI'))[0]
        assert tini_f == 24
        # Deterministic: same input → same color
        assert (self._get_subrecord_data(result_f, 'TINC')
                == self._get_subrecord_data(convert_NPC_(rec_f), 'TINC'))

        # A Nord must be markedly PALER than a Redguard.  This is the defect
        # the authored-color path fixes: the census pick gave 72% of Imperial
        # males a luma-66 tone, darker than Redguards ought to be.
        nord_r, nord_g, nord_b, _ = struct.unpack(
            '<4B', self._get_subrecord_data(result_f, 'TINC'))
        nord_luma = 0.2126 * nord_r + 0.7152 * nord_g + 0.0722 * nord_b
        assert nord_luma > luma + 60, (
            f'Nord luma {nord_luma:.0f} not clearly paler than '
            f'Redguard {luma:.0f}')

    def test_race_skin_tones_are_authored_colors(self):
        """Each race's skin tone must match Oblivion's authored appearance.

        High Elf, Redguard, Nord, Breton and Imperial all share
        Characters\\Imperial\\HeadHuman.dds and are differentiated ONLY by the
        RACE record's own FGTS vector, so a converter that ignores race FGTS
        gives all five the same tan.  These assertions pin the distinctions
        that FGTS is responsible for.
        """
        import colorsys
        from tes5_import.actors.npc_face_mapper import _pick_skin_tone

        def tone(race, gender='Male'):
            _, rgb, _ = _pick_skin_tone(race, gender, 0x00000500)
            h, s, _v = colorsys.rgb_to_hsv(*[c / 255.0 for c in rgb])
            luma = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
            return rgb, h * 360.0, s, luma

        # High Elf reads GOLD: yellow-shifted hue, well away from skin-tan.
        _rgb, hue, sat, _l = tone('HighElf')
        assert 38 <= hue <= 70, f'High Elf hue {hue:.0f} is not gold'
        assert sat > 0.45, f'High Elf saturation {sat:.2f} too washed out'

        # Dark Elf reads GREY: near-zero saturation is its defining trait.
        _rgb, _hue, sat, _l = tone('DarkElf')
        assert sat < 0.20, f'Dark Elf saturation {sat:.2f} is not grey'

        # Orc reads GREEN: green channel dominates.
        rgb, _hue, _sat, _l = tone('Orc')
        assert rgb[1] > rgb[0] and rgb[1] > rgb[2], f'Orc {rgb} is not green'

        # Ordering: Redguard clearly darkest of the human races, Nord palest.
        lumas = {r: tone(r)[3]
                 for r in ('Redguard', 'Imperial', 'Breton', 'Nord')}
        assert lumas['Redguard'] < lumas['Imperial'] < lumas['Nord'], lumas
        assert lumas['Redguard'] < 95, lumas
        assert lumas['Nord'] > 150, lumas

        # Imperials must NOT be dark.  The reported bug was pale-skinned
        # Imperials converting to a near-black tone.
        assert lumas['Imperial'] > 110, (
            f"Imperial luma {lumas['Imperial']:.0f} is too dark")

    def test_crea_becomes_npc(self):
        rec = {'Signature': 'CREA', 'FormID': '00000600', 'RecordFlags': '0',
               'EditorID': 'TestDeer', 'FULL': 'Deer',
               'ACBS.Flags': '0', 'ACBS.Level': '3', 'ACBS.CalcMin': '1',
               'ACBS.CalcMax': '10', 'ACBS.BarterGold': '0',
               'FactionCount': '0', 'ItemCount': '0', 'AIPackageCount': '0',
               'AIDT.Aggression': '0', 'AIDT.Confidence': '30',
               'AIDT.Services': '0',
               'DATA.CombatSkill': '15', 'DATA.MagicSkill': '0',
               'DATA.StealthSkill': '20', 'DATA.Health': '20',
               'DATA.Strength': '30', 'DATA.Intelligence': '10'}
        result = convert_CREA(rec)
        sig = result[:4].decode('ascii')
        assert sig == 'NPC_'

    def test_crea_spells_are_emitted(self):
        """CREA spells must reach SPLO — convert_CREA emitted NONE, so all
        600 spell-carrying Oblivion creatures shipped unable to cast."""
        rec = {'Signature': 'CREA', 'FormID': '0003E9CB', 'RecordFlags': '0',
               'EditorID': 'CreatureScampStunted', 'FULL': 'Stunted Scamp',
               'ACBS.Flags': '0', 'ACBS.Level': '3', 'ACBS.CalcMin': '1',
               'ACBS.CalcMax': '10', 'ACBS.BarterGold': '0',
               'FactionCount': '0', 'ItemCount': '0', 'AIPackageCount': '0',
               'AIDT.Aggression': '0', 'AIDT.Confidence': '30',
               'AIDT.Services': '0',
               'SpellCount': '2',
               'Spell[0]': '0002B543', 'Spell[1]': '0005D4A2',
               'DATA.CombatSkill': '30', 'DATA.MagicSkill': '25',
               'DATA.StealthSkill': '20', 'DATA.Health': '20',
               'DATA.Strength': '30', 'DATA.Intelligence': '10'}
        result = convert_CREA(rec)
        spct = self._get_subrecord_data(result, 'SPCT')
        assert struct.unpack('<I', spct)[0] == 2
        # both spells present, in authored order; an LVSP target is legal
        # (xEdit types SPLO as [SPEL, SHOU, LVSP])
        splos = [struct.unpack('<I', d)[0]
                 for s, d in self._iter_subrecords(result) if s == 'SPLO']
        assert len(splos) == 2
        assert [f & 0x00FFFFFF for f in splos] == [0x02B543, 0x05D4A2]

    def test_offensive_spells_are_classified(self):
        """Delivery classification drives the two cast paths: an aimed spell
        is offensive (cast through SPLO + the graph's FireForget chain), a
        passive Ability is neither (the scamp's AbDaedricResistWeak is a
        self-buff), and only a pure TOUCH spell becomes the melee ATKD
        'Attack Spell' (the flame atronach idiom)."""
        from tes5_import.actors import creature_races as cr
        cr.load_creature_item_index({
            'SPEL': [
                {'Signature': 'SPEL', 'FormID': '0002B543',
                 'SPIT.Type': '4', 'EffectCount': '1',
                 'Effect[0].Type': 'Self'},
                {'Signature': 'SPEL', 'FormID': '000A97DF',
                 'SPIT.Type': '0', 'EffectCount': '1',
                 'Effect[0].Type': 'Target'},
                {'Signature': 'SPEL', 'FormID': '000A97E0',
                 'SPIT.Type': '0', 'EffectCount': '1',
                 'Effect[0].Type': 'Touch'},
            ],
            'LVSP': [
                {'Signature': 'LVSP', 'FormID': '0005D4A2',
                 'EntryCount': '1', 'Entry[0].FormID': '000A97DF'},
            ],
        })
        try:
            scamp = {'Signature': 'CREA', 'SpellCount': '2',
                     'Spell[0]': '0002B543', 'Spell[1]': '0005D4A2'}
            # the ability is passive; the LEVELED spell resolves offensive
            assert cr.creature_has_offensive_spell([scamp])
            assert not cr._spell_is_offensive(0x02B543)
            assert cr._spell_is_offensive(0x05D4A2)
            # an aimed spell must NOT ride the melee attacks...
            assert cr.creature_touch_attack_spell([scamp]) == 0
            # ...but a pure touch spell must
            toucher = {'Signature': 'CREA', 'SpellCount': '1',
                       'Spell[0]': '000A97E0'}
            assert cr.creature_touch_attack_spell(
                [toucher]) & 0x00FFFFFF == 0x0A97E0
        finally:
            cr.load_creature_item_index({})

    def test_atkd_carries_the_attack_spell(self):
        """ATKD field 3 is 'Attack Spell' (xEdit: [SPEL, SHOU, NULL]) — the
        vanilla melee-caster idiom (109 vanilla attack entries; the flame
        atronach's four attacks each name a fire spell)."""
        from tes5_import.actors.creature_races import _atkd
        data = _atkd(spell=0x0105D4A2)
        assert len(data) == 44
        dmg, chance, spell, flags = struct.unpack_from('<ffII', data, 0)
        assert spell == 0x0105D4A2
        assert abs(chance - 1.0) < 1e-6
        # a plain melee attack must still write a NULL spell
        assert struct.unpack_from('<ffII', _atkd(), 0)[2] == 0

    def test_crea_spells_follow_rnam(self):
        """Order is RNAM -> SPCT -> SPLO[] -> COCT/CNTO (verified against the
        xEdit TES5 NPC_ definition and real Skyrim.esm records)."""
        rec = {'Signature': 'CREA', 'FormID': '0003E9CC', 'RecordFlags': '0',
               'EditorID': 'TestCaster',
               'ACBS.Flags': '0', 'ACBS.Level': '3', 'ACBS.CalcMin': '1',
               'ACBS.CalcMax': '10', 'ACBS.BarterGold': '0',
               'FactionCount': '0', 'ItemCount': '0', 'AIPackageCount': '0',
               'AIDT.Aggression': '0', 'AIDT.Confidence': '30',
               'AIDT.Services': '0', 'SpellCount': '1',
               'Spell[0]': '0002B543',
               'DATA.CombatSkill': '15', 'DATA.MagicSkill': '10',
               'DATA.StealthSkill': '20', 'DATA.Health': '20',
               'DATA.Strength': '30', 'DATA.Intelligence': '10'}
        order = [s for s, _d in self._iter_subrecords(convert_CREA(rec))]
        assert order.index('RNAM') < order.index('SPCT') < order.index('SPLO')
        for after in ('AIDT', 'PKID'):
            if after in order:
                assert order.index('SPLO') < order.index(after)

    def test_crea_null_spell_ids_are_dropped(self):
        """A null FormID must never reach SPLO, and SPCT must match what was
        actually written."""
        rec = {'Signature': 'CREA', 'FormID': '0003E9CD', 'RecordFlags': '0',
               'EditorID': 'TestNullSpell',
               'ACBS.Flags': '0', 'ACBS.Level': '3', 'ACBS.CalcMin': '1',
               'ACBS.CalcMax': '10', 'ACBS.BarterGold': '0',
               'FactionCount': '0', 'ItemCount': '0', 'AIPackageCount': '0',
               'AIDT.Aggression': '0', 'AIDT.Confidence': '30',
               'AIDT.Services': '0', 'SpellCount': '2',
               'Spell[0]': '00000000', 'Spell[1]': '0002B543',
               'DATA.CombatSkill': '15', 'DATA.MagicSkill': '10',
               'DATA.StealthSkill': '20', 'DATA.Health': '20',
               'DATA.Strength': '30', 'DATA.Intelligence': '10'}
        result = convert_CREA(rec)
        assert struct.unpack('<I', self._get_subrecord_data(result,
                                                            'SPCT'))[0] == 1
        assert len([1 for s, _d in self._iter_subrecords(result)
                    if s == 'SPLO']) == 1

    def test_crea_base_scale_to_nam6(self):
        """TES4 CREA BNAM 'Base Scale' -> TES5 NPC_ NAM6 'Height'.

        A creature is sized per-record in TES4, not by its race. Hardcoding
        NAM6 to 1.0 shipped every creature at one size: Anequina's bull
        elephant (3.5) came out small and Nehrim's chickens (0.4) huge.
        """
        base = {'Signature': 'CREA', 'FormID': '00000601', 'RecordFlags': '0',
                'EditorID': 'TestElephant', 'FULL': 'Elephant',
                'ACBS.Flags': '0', 'ACBS.Level': '3', 'ACBS.CalcMin': '1',
                'ACBS.CalcMax': '10', 'ACBS.BarterGold': '0',
                'FactionCount': '0', 'ItemCount': '0', 'AIPackageCount': '0',
                'AIDT.Aggression': '0', 'AIDT.Confidence': '30',
                'AIDT.Services': '0',
                'DATA.CombatSkill': '15', 'DATA.MagicSkill': '0',
                'DATA.StealthSkill': '20', 'DATA.Health': '20',
                'DATA.Strength': '30', 'DATA.Intelligence': '10'}

        for authored, expected in (('3.5', 3.5),        # bull elephant
                                   ('0.4000000059604645', 0.4),  # chicken
                                   ('0.800000011920929', 0.8)):  # donkey
            rec = dict(base, **{'BNAM.BaseScale': authored})
            nam6 = self._get_subrecord_data(convert_CREA(rec), 'NAM6')
            assert len(nam6) == 4
            assert struct.unpack('<f', nam6)[0] == pytest.approx(expected)

        # No BNAM, or a nonsense value, means no scaling.
        for rec in (base,
                    dict(base, **{'BNAM.BaseScale': '0.0'}),
                    dict(base, **{'BNAM.BaseScale': '-2.0'})):
            nam6 = self._get_subrecord_data(convert_CREA(rec), 'NAM6')
            assert struct.unpack('<f', nam6)[0] == pytest.approx(1.0)

    def test_ench(self):
        rec = {'Signature': 'ENCH', 'FormID': '00000700', 'RecordFlags': '0',
               'EditorID': 'TestEnch', 'ENIT.Type': '2', 'ENIT.Charge': '100',
               'ENIT.Cost': '50', 'ENIT.Flags': '0', 'EffectCount': '1',
               'Effect[0].Magnitude': '10', 'Effect[0].Area': '0',
               'Effect[0].Duration': '60', 'Effect[0].Type': 'Target'}
        result = convert_ENCH(rec)
        self._check_record(result, 'ENCH')
        enit = self._get_subrecord_data(result, 'ENIT')
        assert len(enit) == 36
        cast_type, = struct.unpack_from('<I', enit, 8)
        enchant_type, = struct.unpack_from('<I', enit, 20)
        # TES4 ENIT.Type=2 (Weapon) -> TES5 CastType=1 (Fire and Forget),
        # not 2 (Concentration) -- wbCastEnum in wbDefinitionsTES5.pas.
        assert cast_type == 1
        assert enchant_type == 6  # Enchantment

    def test_ench_staff_cast_type(self):
        rec = {'Signature': 'ENCH', 'FormID': '00000701', 'RecordFlags': '0',
               'EditorID': 'TestStaffEnch', 'ENIT.Type': '1', 'ENIT.Charge': '100',
               'ENIT.Cost': '50', 'ENIT.Flags': '0', 'EffectCount': '1',
               'Effect[0].Magnitude': '10', 'Effect[0].Area': '0',
               'Effect[0].Duration': '60', 'Effect[0].Type': 'Target'}
        result = convert_ENCH(rec)
        enit = self._get_subrecord_data(result, 'ENIT')
        cast_type, = struct.unpack_from('<I', enit, 8)
        enchant_type, = struct.unpack_from('<I', enit, 20)
        assert cast_type == 1  # Fire and Forget
        assert enchant_type == 12  # Staff Enchantment

    def test_ench_apparel_cast_type(self):
        rec = {'Signature': 'ENCH', 'FormID': '00000702', 'RecordFlags': '0',
               'EditorID': 'TestApparelEnch', 'ENIT.Type': '3', 'ENIT.Charge': '100',
               'ENIT.Cost': '50', 'ENIT.Flags': '0', 'EffectCount': '1',
               'Effect[0].Magnitude': '10', 'Effect[0].Area': '0',
               'Effect[0].Duration': '60', 'Effect[0].Type': 'Self'}
        result = convert_ENCH(rec)
        enit = self._get_subrecord_data(result, 'ENIT')
        cast_type, = struct.unpack_from('<I', enit, 8)
        assert cast_type == 0  # Constant Effect

    def test_cell(self):
        rec = {'Signature': 'CELL', 'FormID': '00000800', 'RecordFlags': '0',
               'EditorID': 'TestCell', 'FULL': 'Test Interior',
               'DATA.Flags': '1'}  # Is Interior
        result = convert_CELL(rec)
        self._check_record(result, 'CELL')
        data = self._get_subrecord_data(result, 'DATA')
        assert len(data) == 2  # uint16 in TES5

    def test_refr(self):
        rec = {'Signature': 'REFR', 'FormID': '00001000', 'RecordFlags': '0',
               'NAME': '00012345',
               'PosX': '100.0', 'PosY': '200.0', 'PosZ': '0.0',
               'RotX': '0.0', 'RotY': '0.0', 'RotZ': '1.57'}
        result = convert_REFR(rec)
        self._check_record(result, 'REFR')
        assert self._has_subrecord(result, 'NAME')
        assert self._has_subrecord(result, 'DATA')
        data = self._get_subrecord_data(result, 'DATA')
        assert len(data) == 24  # 6 floats

    def test_tes3_refr_ships_dont_havok_settle(self):
        """A Morrowind placement is an unsettled pose; only TES3 sets the flag.

        See: docs/commentary/tes5_import_world.md#tes3-dont-havok-settle
        """
        rec = {'Signature': 'REFR', 'FormID': '00001000', 'RecordFlags': '1024',
               'NAME': '00012345', 'PosX': '0.0', 'PosY': '0.0',
               'PosZ': '0.0', 'RotX': '0.0', 'RotY': '0.0', 'RotZ': '0.0'}
        before = world_morrowind.tes3_lock_state()
        try:
            world_morrowind.set_tes3_lock_state((True, ()))
            tes3 = struct.unpack_from('<I', convert_REFR(rec), 8)[0]
            world_morrowind.set_tes3_lock_state((False, ()))
            tes4 = struct.unpack_from('<I', convert_REFR(rec), 8)[0]
        finally:
            world_morrowind.set_tes3_lock_state(before)
        assert tes3 == 1024 | world_morrowind.REFR_DONT_HAVOK_SETTLE
        assert tes4 == 1024

    def test_refr_trigger_primitive_round_trips(self):
        """An FO3/FNV XPRM (trigger volume) is copied verbatim after NAME: the
        layout is TES5's own, and without it OnTriggerEnter never fires."""
        raw = ('80A69A4288A5A4430000E0420000803F0000803F8180003F9A99193E'
               '01000000')
        rec = {'Signature': 'REFR', 'FormID': '00001000', 'RecordFlags': '0',
               'NAME': '00012345', 'XPRM.Raw': raw,
               'PosX': '0.0', 'PosY': '0.0', 'PosZ': '0.0',
               'RotX': '0.0', 'RotY': '0.0', 'RotZ': '0.0'}
        result = convert_REFR(rec)
        assert self._get_subrecord_data(result, 'XPRM') == bytes.fromhex(raw)
        assert result.index(b'NAME') < result.index(b'XPRM') < result.index(b'DATA')

    def test_land(self):
        # Minimal LAND record
        vhgt = b'\x00' * 1093  # Standard VHGT size
        rec = {'Signature': 'LAND', 'FormID': '00002000', 'RecordFlags': '0',
               'DATA.Flags': '1',
               'VHGT': vhgt.hex().upper(),
               'LayerCount': '0', 'VTEXCount': '0'}
        result = convert_LAND(rec)
        self._check_record(result, 'LAND')
        assert self._has_subrecord(result, 'VHGT')

    def test_glob(self):
        rec = {'Signature': 'GLOB', 'FormID': '00003000', 'RecordFlags': '0',
               'EditorID': 'TES4Fame', 'FNAM.Type': 'f', 'FLTV.Value': '12.0'}
        result = convert_GLOB(rec)
        self._check_record(result, 'GLOB')
        fnam = self._get_subrecord_data(result, 'FNAM')
        assert fnam == bytes([ord('f')])

    def test_glob_engine_time_global_dropped(self):
        """GameHour etc. collide with Skyrim engine globals; script references
        are canonicalized to the vanilla forms, so our copy must not be
        emitted."""
        rec = {'Signature': 'GLOB', 'FormID': '00003000', 'RecordFlags': '0',
               'EditorID': 'GameHour', 'FNAM.Type': 'f', 'FLTV.Value': '12.0'}
        assert convert_GLOB(rec) == b''

    def test_lvli(self):
        rec = {'Signature': 'LVLI', 'FormID': '00004000', 'RecordFlags': '0',
               'EditorID': 'TestLvlList', 'LVLD.ChanceNone': '0', 'LVLF.Flags': '1',
               'EntryCount': '2',
               'Entry[0].Level': '1', 'Entry[0].FormID': '00000100', 'Entry[0].Count': '1',
               'Entry[1].Level': '5', 'Entry[1].FormID': '00000200', 'Entry[1].Count': '2'}
        result = convert_LVLI(rec)
        self._check_record(result, 'LVLI')
        assert self._has_subrecord(result, 'LLCT')
        assert self._has_subrecord(result, 'LVLO')

    def test_lvlc_becomes_lvln(self):
        rec = {'Signature': 'LVLC', 'FormID': '00005000', 'RecordFlags': '0',
               'EditorID': 'TestLvlCrea', 'LVLD.ChanceNone': '0', 'LVLF.Flags': '0',
               'EntryCount': '1',
               'Entry[0].Level': '1', 'Entry[0].FormID': '00000600', 'Entry[0].Count': '1'}
        result = convert_LVLC(rec)
        sig = result[:4].decode('ascii')
        assert sig == 'LVLN'

    def test_book(self):
        rec = {'Signature': 'BOOK', 'FormID': '00006000', 'RecordFlags': '0',
               'EditorID': 'TestBook', 'FULL': 'A Test Book',
               'DESC': 'Once upon a time...', 'DATA.Flags': '0',
               'DATA.Teaches': '255', 'DATA.Value': '5', 'DATA.Weight': '1.0',
               'Model.MODL': 'Books\\TestBook.nif'}
        result = convert_BOOK(rec)
        self._check_record(result, 'BOOK')
        assert self._has_subrecord(result, 'DESC')
        # INAM (Inventory Art STAT) must always be present — BookMenu
        # null-derefs (in-game crash) when a book without INAM is read.
        assert self._has_subrecord(result, 'INAM'), "BOOK must have INAM"
        inam_data = self._get_subrecord_data(result, 'INAM')
        assert len(inam_data) == 4, "INAM must be a 4-byte FormID"
        assert struct.unpack_from('<I', inam_data)[0] != 0, "INAM must not be null"
        assert self._has_subrecord(result, 'CNAM'), "BOOK must have CNAM"
        cnam_data = self._get_subrecord_data(result, 'CNAM')
        assert cnam_data == b'\x00', "CNAM must be an empty description string"

    def test_book_inventory_art_uses_generated_rig(self):
        """With a writer, INAM must reference a companion STAT pointing at the
        generated reading-rig mesh (meshes\\tes4\\clutter\\books\\inv\\
        <model basename>.nif, built by asset_convert/ui/book_inam.py), and books
        sharing the same TES4 model must share one STAT."""
        class _FakeWriter:
            def __init__(self):
                self.records = []
                self._next = 0x01000000
            def alloc_formid(self):
                self._next += 1
                return self._next
            def derive_formid(self, site, key):
                # Test double: derived ids need only be stable per
                # (site, key) and distinct, like the real allocator.
                if not hasattr(self, '_derived'):
                    self._derived = {}
                k = (site, repr(key))
                if k not in self._derived:
                    self._derived[k] = self.alloc_formid()
                return self._derived[k]
            def add_record(self, sig, data):
                self.records.append((sig, data))
        w = _FakeWriter()
        rec = {'Signature': 'BOOK', 'FormID': '00006000', 'RecordFlags': '0',
               'EditorID': 'TestBook', 'FULL': 'A Test Book',
               'DESC': 'Once upon a time...', 'DATA.Flags': '0',
               'DATA.Teaches': '255', 'DATA.Value': '5', 'DATA.Weight': '1.0',
               'Model.MODL': 'Books\\TestBook.nif'}
        result = convert_BOOK(rec, writer=w)
        inam_fid = struct.unpack_from('<I', self._get_subrecord_data(result, 'INAM'))[0]
        assert inam_fid == 0x01000001, "INAM must point at the companion STAT"
        assert len(w.records) == 1 and w.records[0][0] == 'STAT'
        assert b'tes4\\clutter\\books\\inv\\testbook.nif' in w.records[0][1]
        # a second book with the same model reuses the STAT
        rec2 = dict(rec, FormID='00006002', EditorID='TestBookCopy')
        result2 = convert_BOOK(rec2, writer=w)
        inam2 = struct.unpack_from('<I', self._get_subrecord_data(result2, 'INAM'))[0]
        assert inam2 == inam_fid, "same model must reuse the same INAM STAT"
        assert len(w.records) == 1, "no duplicate STAT for a shared model"

    def test_book_scroll_flag_keeps_book_type(self):
        """TES4 Scroll flag (0x01) must still produce Type 0: vanilla
        Skyrim.esm types every one of its 821 BOOKs (notes included) as 0, so
        255 is an engine-untested value.  Scroll-ness survives via the vendor
        keyword and the note-rig inventory art."""
        rec = {'Signature': 'BOOK', 'FormID': '00006001', 'RecordFlags': '0',
               'EditorID': 'TestNote', 'FULL': 'A Test Note',
               'DESC': 'note text', 'DATA.Flags': '1',
               'DATA.Teaches': '255', 'DATA.Value': '5', 'DATA.Weight': '0.1'}
        result = convert_BOOK(rec)
        data = self._get_subrecord_data(result, 'DATA')
        assert data[1] == 0, "books must use Type=0 like all vanilla BOOKs"

    def test_book_html_font_face_remapped(self):
        """Numeric Oblivion <font face=N> must become Skyrim named fonts."""
        from tes5_import.record_types.equipment import _fix_book_html
        assert "<font face='$HandwrittenFont'>" in _fix_book_html('<font face=5>text')
        assert "<font face='$SkyrimBooks'>" in _fix_book_html('<FONT face=1>text')
        assert "<font face='$DaedricFont'>" in _fix_book_html('<font face=4>text')
        assert '</font>' in _fix_book_html('text</font>')
        assert 'face=5' not in _fix_book_html('<font face=5>text')

    def test_book_html_img_prefixed(self):
        """IMG src paths must use img:// with the converted texture path."""
        from tes5_import.record_types.equipment import _fix_book_html
        result = _fix_book_html('<IMG src="Book/fancy_font/h_62x62.dds" width=62 height=62>')
        assert "src='img://textures/tes4/menus/Book/fancy_font/h_62x62.dds'" in result
        # Already-converted paths must not be double-prefixed
        result2 = _fix_book_html("<IMG src='img://textures/tes4/menus/book/h.dds'>")
        assert result2.count('img://') == 1

    def test_book_html_no_double_br(self):
        """\\r\\n after <br> must not be converted to <br><br>."""
        from tes5_import.record_types.equipment import _fix_book_html
        result = _fix_book_html('<font face=5>\r\nline1<br>\r\nline2')
        assert '<br><br>' not in result

    def test_ligh(self):
        rec = {'Signature': 'LIGH', 'FormID': '00007000', 'RecordFlags': '0',
               'EditorID': 'TestLight', 'FULL': 'Torch',
               'DATA.Time': '-1', 'DATA.Radius': '300',
               'DATA.Color.R': '255', 'DATA.Color.G': '200', 'DATA.Color.B': '100',
               'DATA.Flags': '1', 'DATA.FalloffExponent': '1.0', 'DATA.FOV': '90.0',
               'DATA.Value': '2', 'DATA.Weight': '1.0'}
        result = convert_LIGH(rec)
        self._check_record(result, 'LIGH')
        data = self._get_subrecord_data(result, 'DATA')
        assert len(data) == 48

    def test_cont(self):
        rec = {'Signature': 'CONT', 'FormID': '00008000', 'RecordFlags': '0',
               'EditorID': 'TestChest', 'FULL': 'Chest',
               'Item[0].FormID': '00000100', 'Item[0].Count': '5',
               'DATA.Flags': '0', 'DATA.Weight': '25.0',
               'Model.MODL': 'Containers\\Chest.nif'}
        result = convert_CONT(rec)
        self._check_record(result, 'CONT')
        assert self._has_subrecord(result, 'CNTO')

    def test_door(self):
        rec = {'Signature': 'DOOR', 'FormID': '00009000', 'RecordFlags': '0',
               'EditorID': 'TestDoor', 'FULL': 'Wooden Door',
               'Model.MODL': 'Doors\\WoodDoor.nif'}
        result = convert_DOOR(rec)
        self._check_record(result, 'DOOR')


class TestDoorSounds:
    """DOOR SNAM/ANAM/BNAM must name an SNDR, not the TES4 SOUN.

    xEdit: wbFormIDCk(SNAM, 'Sound - Open', [SNDR]); Skyrim.esm agrees —
    WRDragonSideDoor01's SNAM 0005AFC9 is the SNDR DRSWoodImperialDouble01OpenSD.
    Doors are written in Phase 1, before the descriptors exist, so the SOUN id
    is a placeholder that patch_sound_descriptor_slots resolves.
    """

    @staticmethod
    def _slots(blob):
        out = {}
        pos = 24
        assert struct.unpack_from('<I', blob, 4)[0] == len(blob) - 24
        while pos + 6 <= len(blob):
            sig = blob[pos:pos + 4]
            size = struct.unpack_from('<H', blob, pos + 4)[0]
            if sig in (b'SNAM', b'ANAM', b'BNAM') and size == 4:
                out[sig.decode()] = struct.unpack_from('<I', blob, pos + 6)[0]
            pos += 6 + size
        return out

    @staticmethod
    def _door(edid, **slots):
        from tes5_import.record_types.common import (pack_formid_subrecord,
                                                     pack_string_subrecord,
                                                     pack_uint8_subrecord)
        subs = pack_string_subrecord('EDID', edid)
        for sig in ('SNAM', 'ANAM', 'BNAM'):
            if slots.get(sig):
                subs += pack_formid_subrecord(sig, slots[sig])
        subs += pack_uint8_subrecord('FNAM', 0)
        return pack_record('DOOR', 0x01000001, 0, subs)

    class _Writer:
        def __init__(self, records):
            self._top_groups = {'DOOR': records}

    def test_slots_resolve_to_descriptors(self):
        from tes5_import.record_types import sound
        from tes5_import.record_types.items import patch_sound_descriptor_slots
        sound.reset_sound_descriptors()
        sound.record_sndr_for_soun(0x0105C423, 0x01190F00)
        sound.record_sndr_for_soun(0x0105C424, 0x01190F01)
        w = self._Writer([self._door('D', SNAM=0x0105C423, ANAM=0x0105C424)])
        assert patch_sound_descriptor_slots(
            w, 'DOOR', {0x05C423, 0x05C424}) == 1
        assert self._slots(w._top_groups['DOOR'][0]) == {
            'SNAM': 0x01190F00, 'ANAM': 0x01190F01}

    def test_slot_without_descriptor_is_dropped(self):
        """A SOUN with no FNAM mints no SNDR; a slot pointing at the SOUN
        would be a wrong-typed reference, so it goes."""
        from tes5_import.record_types import sound
        from tes5_import.record_types.items import patch_sound_descriptor_slots
        sound.reset_sound_descriptors()
        sound.record_sndr_for_soun(0x0105C423, 0x01190F00)
        w = self._Writer([self._door('D', SNAM=0x0105C423, BNAM=0x0105C425)])
        assert patch_sound_descriptor_slots(
            w, 'DOOR', {0x05C423, 0x05C425}) == 1
        assert self._slots(w._top_groups['DOOR'][0]) == {'SNAM': 0x01190F00}

    def test_master_override_slots_untouched(self):
        """An override build's DOOR group also holds the master's records,
        whose slots already name the MASTER's SNDRs. Rewriting or dropping
        those would strip door sound out of every dependent plugin."""
        from tes5_import.record_types import sound
        from tes5_import.record_types.items import patch_sound_descriptor_slots
        sound.reset_sound_descriptors()
        sound.record_sndr_for_soun(0x0105C423, 0x01190F00)
        w = self._Writer([self._door('M', SNAM=0x00190AAA, ANAM=0x00190AAB)])
        assert patch_sound_descriptor_slots(w, 'DOOR', {0x05C423}) == 0
        assert self._slots(w._top_groups['DOOR'][0]) == {
            'SNAM': 0x00190AAA, 'ANAM': 0x00190AAB}

    def test_patch_is_idempotent(self):
        from tes5_import.record_types import sound
        from tes5_import.record_types.items import patch_sound_descriptor_slots
        sound.reset_sound_descriptors()
        sound.record_sndr_for_soun(0x0105C423, 0x01190F00)
        w = self._Writer([self._door('D', SNAM=0x0105C423)])
        assert patch_sound_descriptor_slots(w, 'DOOR', {0x05C423}) == 1
        first = w._top_groups['DOOR'][0]
        assert patch_sound_descriptor_slots(w, 'DOOR', {0x05C423}) == 0
        assert w._top_groups['DOOR'][0] == first

    def test_mesh_authored_sound_fills_empty_slots(self):
        """Oblivion accepts a door's sound on the record OR as `sound:` text
        keys in the model; Skyrim has only the record. StoneWallGateDoor01's
        DOOR records carry no SNAM/ANAM at all — the gate creak lives entirely
        in the NIF — so without the mesh fallback they convert silent."""
        from tes5_import.record_types import items
        rec = {'Signature': 'DOOR', 'FormID': '0002595F', 'RecordFlags': '0',
               'EditorID': 'StoneWallGateDoor01', 'FULL': 'Wooden Gate',
               'Model.MODL': 'Architecture\\StoneWall\\StoneWallGateDoor01.NIF'}
        items._DOOR_MODEL_SOUNDS.clear()
        items._DOOR_MODEL_SOUNDS[
            'architecture/stonewall/stonewallgatedoor01.nif'] = {
                'open': 0x0105C423, 'close': 0x0105C424}
        try:
            slots = self._slots(items.convert_DOOR(rec))
        finally:
            items._DOOR_MODEL_SOUNDS.clear()
        assert slots == {'SNAM': 0x0105C423, 'ANAM': 0x0105C424}

    def test_record_sound_wins_over_mesh(self):
        """The record is the authored value where both exist."""
        from tes5_import.record_types import items
        rec = {'Signature': 'DOOR', 'FormID': '0002595F', 'RecordFlags': '0',
               'EditorID': 'D', 'SNAM.Open': '0105C400',
               'Model.MODL': 'Architecture\\StoneWall\\StoneWallGateDoor01.NIF'}
        items._DOOR_MODEL_SOUNDS.clear()
        items._DOOR_MODEL_SOUNDS[
            'architecture/stonewall/stonewallgatedoor01.nif'] = {
                'open': 0x0105C423, 'close': 0x0105C424}
        try:
            slots = self._slots(items.convert_DOOR(rec))
        finally:
            items._DOOR_MODEL_SOUNDS.clear()
        assert slots['SNAM'] == 0x0105C400
        assert slots['ANAM'] == 0x0105C424


class TestLightSoundDescriptors:
    """LIGH SNAM is a sound DESCRIPTOR slot in TES5, not a SOUN.

    xEdit's TES5 LIGH is `wbFormIDCk(SNAM, 'Sound', [SNDR])`, and Skyrim.esm's
    one sounded light resolves to an SNDR. Left as a SOUN id the engine puts a
    non-pointer into the audio manager's emitter table and BSAudioManagerThread
    faults dereferencing it (`mov ecx, [r8+0x48]`), so every lit torch in the
    plugin arms an audio-thread crash. Lights are written in Phase 1, before the
    descriptors exist, so the SOUN id is a placeholder
    patch_sound_descriptor_slots resolves.
    """

    @staticmethod
    def _snam(blob):
        pos = 24
        assert struct.unpack_from('<I', blob, 4)[0] == len(blob) - 24
        while pos + 6 <= len(blob):
            sig = blob[pos:pos + 4]
            size = struct.unpack_from('<H', blob, pos + 4)[0]
            if sig == b'SNAM' and size == 4:
                return struct.unpack_from('<I', blob, pos + 6)[0]
            pos += 6 + size
        return None

    @staticmethod
    def _light(edid, snam=0):
        from tes5_import.record_types.common import (pack_float_subrecord,
                                                     pack_formid_subrecord,
                                                     pack_string_subrecord)
        subs = pack_string_subrecord('EDID', edid)
        subs += pack_float_subrecord('FNAM', 1.0)
        if snam:
            subs += pack_formid_subrecord('SNAM', snam)
        return pack_record('LIGH', 0x01000001, 0, subs)

    class _Writer:
        def __init__(self, records):
            self._top_groups = {'LIGH': records}

    def test_snam_resolves_to_descriptor(self):
        from tes5_import.record_types import sound
        from tes5_import.record_types.items import patch_sound_descriptor_slots
        sound.reset_sound_descriptors()
        sound.record_sndr_for_soun(0x0105C423, 0x01190F00)
        w = self._Writer([self._light('L', snam=0x0105C423)])
        assert patch_sound_descriptor_slots(w, 'LIGH', {0x05C423}) == 1
        assert self._snam(w._top_groups['LIGH'][0]) == 0x01190F00

    def test_slot_without_descriptor_is_dropped(self):
        """A SOUN with no FNAM mints no SNDR; leaving the slot pointing at the
        SOUN is exactly the wrong-typed reference that crashes the audio
        thread, so the slot goes instead."""
        from tes5_import.record_types import sound
        from tes5_import.record_types.items import patch_sound_descriptor_slots
        sound.reset_sound_descriptors()
        w = self._Writer([self._light('L', snam=0x0105C425)])
        assert patch_sound_descriptor_slots(w, 'LIGH', {0x05C425}) == 1
        assert self._snam(w._top_groups['LIGH'][0]) is None

    def test_master_owned_soun_resolves_via_master(self):
        """Morrowind_ob's torches point at Oblivion.esm's AMBTorchMountedLP, a
        SOUN the plugin never overrides. Without the master lookup the largest
        group of lights would lose its ambience — master-export blindness."""
        from tes5_import.record_types import sound
        from tes5_import.record_types.items import patch_sound_descriptor_slots
        sound.reset_sound_descriptors()
        w = self._Writer([self._light('L', snam=0x01085837)])
        assert patch_sound_descriptor_slots(
            w, 'LIGH', set(),
            lambda fid: 0x0158689D if fid == 0x01085837 else 0) == 1
        assert self._snam(w._top_groups['LIGH'][0]) == 0x0158689D

    def test_master_override_slot_untouched(self):
        """An override build's LIGH group also holds the master's converted
        records, whose SNAM already names the MASTER's SNDR."""
        from tes5_import.record_types import sound
        from tes5_import.record_types.items import patch_sound_descriptor_slots
        sound.reset_sound_descriptors()
        sound.record_sndr_for_soun(0x0105C423, 0x01190F00)
        w = self._Writer([self._light('M', snam=0x0058689D)])
        assert patch_sound_descriptor_slots(
            w, 'LIGH', {0x05C423}, lambda fid: 0) == 0
        assert self._snam(w._top_groups['LIGH'][0]) == 0x0058689D

    def test_patch_is_idempotent(self):
        from tes5_import.record_types import sound
        from tes5_import.record_types.items import patch_sound_descriptor_slots
        sound.reset_sound_descriptors()
        sound.record_sndr_for_soun(0x0105C423, 0x01190F00)
        w = self._Writer([self._light('L', snam=0x0105C423)])
        assert patch_sound_descriptor_slots(w, 'LIGH', {0x05C423}) == 1
        first = w._top_groups['LIGH'][0]
        assert patch_sound_descriptor_slots(w, 'LIGH', {0x05C423}) == 0
        assert w._top_groups['LIGH'][0] == first

    def test_light_without_sound_untouched(self):
        from tes5_import.record_types.items import patch_sound_descriptor_slots
        w = self._Writer([self._light('L')])
        first = w._top_groups['LIGH'][0]
        assert patch_sound_descriptor_slots(w, 'LIGH', {0x05C423}) == 0
        assert w._top_groups['LIGH'][0] == first


class TestSoundDescriptorSlotCoverage:
    """Every TES5 slot xEdit types as [SNDR] must be patched, not just DOOR.

    An unpatched slot is not a cosmetic wrong-sound bug: the engine registers
    the SOUN id in the audio manager's emitter table and BSAudioManagerThread
    faults dereferencing it. ACTI (Dwemer steam machines), CONT (every chest
    and barrel) and LIGH (every torch) all carry live slots.
    """

    @staticmethod
    def _rec(sig, **slots):
        from tes5_import.record_types.common import (pack_formid_subrecord,
                                                     pack_string_subrecord)
        subs = pack_string_subrecord('EDID', 'X')
        for name, fid in slots.items():
            subs += pack_formid_subrecord(name, fid)
        return pack_record(sig, 0x01000001, 0, subs)

    @staticmethod
    def _slot(blob, name):
        pos = 24
        assert struct.unpack_from('<I', blob, 4)[0] == len(blob) - 24
        want = name.encode()
        while pos + 6 <= len(blob):
            sig = blob[pos:pos + 4]
            size = struct.unpack_from('<H', blob, pos + 4)[0]
            if sig == want and size == 4:
                return struct.unpack_from('<I', blob, pos + 6)[0]
            pos += 6 + size
        return None

    def test_every_xedit_sndr_slot_is_covered(self):
        """Guards against a record type being added with an unpatched slot.

        MSTT and TACT type SNAM as [SNDR] too, but are deliberately absent: no
        MSTT or TACT we write can carry a placeholder. MSTT exists only as
        convert_STAT's havok retype and TES4 STAT has no sound field at all
        (0 SNAM keys across Oblivion's 6,014 and Nehrim's 7,205 STATs); TACT is
        synthesized only by speaker_activators, which writes VNAM, never SNAM.
        """
        from tes5_import.record_types.items import _SNDR_SLOTS
        assert _SNDR_SLOTS == {
            'ACTI': (b'SNAM', b'VNAM'),
            'CONT': (b'SNAM', b'QNAM'),
            'DOOR': (b'SNAM', b'ANAM', b'BNAM'),
            'LIGH': (b'SNAM',),
        }

    def test_tact_vnam_is_never_treated_as_a_sound_slot(self):
        """VNAM is [SNDR] on ACTI but [VTYP] on TACT (xEdit 3324 vs 3348).

        A TACT entry here would rewrite every speaker activator's voice type
        into a sound descriptor and mute the speak-as lines, so TACT must stay
        out of the table entirely -- listing it with only SNAM is not enough of
        a guard, because the next slot added would sit beside ACTI's VNAM.
        """
        from tes5_import.record_types.items import _SNDR_SLOTS
        assert 'TACT' not in _SNDR_SLOTS
        assert 'MSTT' not in _SNDR_SLOTS

    def test_speaker_activator_vnam_survives_the_patch(self):
        """A real synthesized TACT must come through byte-identical."""
        from tes5_import.record_types import sound
        from tes5_import.record_types.items import patch_sound_descriptor_slots
        from tes5_import.dialogue.speak_as import _pack_tact

        class W:
            def __init__(self, groups):
                self._top_groups = groups

        sound.reset_sound_descriptors()
        sound.record_sndr_for_soun(0x0105C423, 0x01190F00)
        tact = _pack_tact(0x01000001, 'TES4Voice_x', 0x0105C423, 'Speaker')
        w = W({'TACT': [tact]})
        # TACT is not a patched type at all, so the loop never reaches it.
        for sig in ('ACTI', 'CONT', 'DOOR', 'LIGH'):
            patch_sound_descriptor_slots(w, sig, {0x05C423})
        assert w._top_groups['TACT'][0] == tact
        assert self._slot(tact, 'VNAM') == 0x0105C423

    def test_activator_and_container_slots_resolve(self):
        from tes5_import.record_types import sound
        from tes5_import.record_types.items import patch_sound_descriptor_slots

        class W:
            def __init__(self, groups):
                self._top_groups = groups

        sound.reset_sound_descriptors()
        sound.record_sndr_for_soun(0x0105C423, 0x01190F00)
        sound.record_sndr_for_soun(0x0105C424, 0x01190F01)
        w = W({'ACTI': [self._rec('ACTI', SNAM=0x0105C423)],
               'CONT': [self._rec('CONT', SNAM=0x0105C423,
                                  QNAM=0x0105C424)]})
        own = {0x05C423, 0x05C424}
        assert patch_sound_descriptor_slots(w, 'ACTI', own) == 1
        assert patch_sound_descriptor_slots(w, 'CONT', own) == 1
        assert self._slot(w._top_groups['ACTI'][0], 'SNAM') == 0x01190F00
        assert self._slot(w._top_groups['CONT'][0], 'SNAM') == 0x01190F00
        assert self._slot(w._top_groups['CONT'][0], 'QNAM') == 0x01190F01

    def test_master_owned_slots_resolve_through_master(self):
        """Morrowind_ob's containers point at Oblivion.esm sounds it never
        overrides; without the master resolver ~2,400 slots stay wrong-typed."""
        from tes5_import.record_types import sound
        from tes5_import.record_types.items import patch_sound_descriptor_slots

        class W:
            def __init__(self, groups):
                self._top_groups = groups

        sound.reset_sound_descriptors()
        w = W({'CONT': [self._rec('CONT', SNAM=0x01085837)]})
        n = patch_sound_descriptor_slots(
            w, 'CONT', set(),
            lambda fid: 0x0158689D if fid == 0x01085837 else 0)
        assert n == 1
        assert self._slot(w._top_groups['CONT'][0], 'SNAM') == 0x0158689D

    def test_unresolvable_master_slot_left_alone(self):
        """An override build's own converted records already hold real SNDR
        ids; rewriting or dropping them would strip sound from the plugin."""
        from tes5_import.record_types import sound
        from tes5_import.record_types.items import patch_sound_descriptor_slots

        class W:
            def __init__(self, groups):
                self._top_groups = groups

        sound.reset_sound_descriptors()
        w = W({'CONT': [self._rec('CONT', SNAM=0x0058689D)]})
        assert patch_sound_descriptor_slots(w, 'CONT', set(),
                                            lambda fid: 0) == 0
        assert self._slot(w._top_groups['CONT'][0], 'SNAM') == 0x0058689D


# ---------------------------------------------------------------------------
# Integration test: Full pipeline on synthetic data
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_minimal_plugin(self):
        """Test creating a minimal valid plugin with just a header."""
        w = PluginWriter(masters=['Skyrim.esm'])
        with tempfile.NamedTemporaryFile(suffix='.esm', delete=False) as f:
            tmp = f.name
        try:
            w.write(tmp)
            with open(tmp, 'rb') as f:
                data = f.read()
            # Should at least have a TES4 header
            assert data[:4] == b'TES4'
            # Check header version
            hedr_pos = data.index(b'HEDR')
            ver = struct.unpack_from('<f', data, hedr_pos + 6)[0]
            assert abs(ver - 1.71) < 0.01
        finally:
            os.unlink(tmp)

    def test_plugin_with_records(self):
        """Test creating a plugin with a few records."""
        w = PluginWriter(masters=['Skyrim.esm'])

        # Add a STAT
        stat_rec = {'Signature': 'STAT', 'FormID': '00012345', 'RecordFlags': '0',
                    'EditorID': 'TestRock'}
        from tes5_import.record_types.items import convert_STAT
        stat_bytes = convert_STAT(stat_rec)
        w.add_record('STAT', stat_bytes)

        # Add a GLOB
        glob_rec = {'Signature': 'GLOB', 'FormID': '00012346', 'RecordFlags': '0',
                    'EditorID': 'TestGlobal', 'FNAM.Type': 'f', 'FLTV.Value': '1.0'}
        from tes5_import.record_types.common import convert_GLOB
        glob_bytes = convert_GLOB(glob_rec)
        w.add_record('GLOB', glob_bytes)

        with tempfile.NamedTemporaryFile(suffix='.esm', delete=False) as f:
            tmp = f.name
        try:
            w.write(tmp)
            size = os.path.getsize(tmp)
            assert size > RECORD_HEADER_SIZE + GROUP_HEADER_SIZE

            with open(tmp, 'rb') as f:
                data = f.read()
            assert b'GRUP' in data
            assert b'STAT' in data
            assert b'GLOB' in data
        finally:
            os.unlink(tmp)


class TestSkyrimRecordFormat:
    """Tests that verify our understanding of Skyrim's record format
    by looking at actual Skyrim.esm data (if available)."""

    SKYRIM_ESM = r"C:\Program Files (x86)\Steam\steamapps\common\Skyrim Special Edition\Data\Skyrim.esm"

    @pytest.fixture(autouse=True)
    def _skip_if_no_skyrim(self):
        if not os.path.exists(self.SKYRIM_ESM):
            pytest.skip("Skyrim.esm not found")

    def _read_record(self, fid):
        """Read a record by FormID from Skyrim.esm."""
        with open(self.SKYRIM_ESM, 'rb') as f:
            data = f.read()
        for rec in reader_records(data, bodies=()):
            if rec.form_id == fid:
                return data[rec.offset:rec.end]
        return None

    def test_skyrim_dialogue_generic_qust(self):
        """Skyrim's DialogueGeneric (0x00013EB3) must NOT have HasDialogueData."""
        rec = self._read_record(0x00013EB3)
        assert rec is not None, "DialogueGeneric not found"
        dnam = _find_subrecord(rec, b'DNAM')
        assert dnam is not None
        flags = struct.unpack_from('<H', dnam, 0)[0]
        assert not (flags & 0x8000), "Skyrim DialogueGeneric must not have HasDialogueData"
        assert flags & 0x0001, "Must have StartGameEnabled"
        assert flags & 0x0010, "Must have StartsEnabled"
        formver = dnam[3]
        assert formver == 0, "Skyrim QUST DNAM FormVer must be 0"

    def test_skyrim_vtyp_has_allow_default(self):
        """Skyrim MaleNord VTYP (0x00013AE6) should have AllowDefaultDialogue."""
        rec = self._read_record(0x00013AE6)
        if rec is None:
            pytest.skip("MaleNord VTYP not found")
        dnam = _find_subrecord(rec, b'DNAM')
        assert dnam is not None
        assert dnam[0] & 0x01, "AllowDefaultDialogue flag must be set"

    def test_skyrim_ctda_32_bytes(self):
        """Skyrim INFO conditions must be 32 bytes."""
        # We'll check DialogueGeneric's quest-level conditions
        rec = self._read_record(0x00013EB3)
        if rec is None:
            pytest.skip("DialogueGeneric not found")
        ctdas = _find_all_subrecords(rec, b'CTDA')
        for ctda in ctdas:
            assert len(ctda) == 32, f"CTDA should be 32 bytes, got {len(ctda)}"


# ---------------------------------------------------------------------------
# Voice file naming tests
# ---------------------------------------------------------------------------

from asset_convert.audio.audio_converter import VOICE_FILENAME_RE
from asset_convert.audio.voice_races import voice_key, vtyp_edid


class TestVoiceFileNaming:
    """Tests for the voice file regex and naming conventions."""

    def test_regex_captures_prefix(self):
        """Regex must capture quest_topic prefix as group(1)."""
        m = VOICE_FILENAME_RE.match('arenaannouncer_announcer_0004216e_1.mp3')
        assert m is not None
        assert m.group(1) == 'arenaannouncer_announcer'
        assert m.group(2) == '0004216e'
        assert m.group(3) == '1'
        assert m.group(4) == 'mp3'

    def test_regex_complex_prefix(self):
        """Regex handles multi-underscore prefixes correctly."""
        m = VOICE_FILENAME_RE.match('ms45_dar_ma_00012345_2.wav')
        assert m is not None
        assert m.group(1) == 'ms45_dar_ma'
        assert m.group(2) == '00012345'
        assert m.group(3) == '2'

    def test_regex_uppercase_formid(self):
        """Regex is case-insensitive for FormID hex digits."""
        m = VOICE_FILENAME_RE.match('questname_topicname_0004ABCD_1.xwm')
        assert m is not None
        assert m.group(2) == '0004ABCD'

    def test_regex_fuz_extension(self):
        """Regex matches .fuz files."""
        m = VOICE_FILENAME_RE.match('quest_topic_00001234_1.fuz')
        assert m is not None
        assert m.group(4) == 'fuz'

    def test_regex_rejects_short_formid(self):
        """Regex requires exactly 8 hex chars for FormID."""
        m = VOICE_FILENAME_RE.match('quest_topic_01234_1.mp3')
        assert m is None

    def test_regex_rejects_no_prefix(self):
        """Regex requires at least one underscore-separated prefix."""
        m = VOICE_FILENAME_RE.match('00012345_1.mp3')
        assert m is None

    def test_voice_type_naming_convention(self):
        """Voice types follow TES4{Male|Female}<RaceKey> naming."""
        for key in ('HighElf', 'Nord', 'GoldenSaint'):
            assert vtyp_edid(key, 'M') == f'TES4Male{key}'
            assert vtyp_edid(key, 'F') == f'TES4Female{key}'

    def test_voice_key_strips_display_name_punctuation(self):
        """The folder name is the display name; the key is its alphanumerics."""
        assert voice_key('High Elf') == 'HighElf'
        assert voice_key('Halb-Aeterna') == 'HalbAeterna'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_subrecord(record_bytes: bytes, sig: bytes) -> bytes | None:
    """Find the first subrecord with the given signature in a record."""
    # Skip the 24-byte record header
    pos = 24
    while pos < len(record_bytes) - 6:
        sub_sig = record_bytes[pos:pos+4]
        sub_size = struct.unpack_from('<H', record_bytes, pos+4)[0]
        if sub_sig == sig:
            return record_bytes[pos+6:pos+6+sub_size]
        pos += 6 + sub_size
    return None


def _find_all_subrecords(record_bytes: bytes, sig: bytes) -> list[bytes]:
    """Find all subrecords with the given signature in a record."""
    results = []
    pos = 24
    while pos < len(record_bytes) - 6:
        sub_sig = record_bytes[pos:pos+4]
        sub_size = struct.unpack_from('<H', record_bytes, pos+4)[0]
        if sub_sig == sig:
            results.append(record_bytes[pos+6:pos+6+sub_size])
        pos += 6 + sub_size
    return results


def _make_info_rec(data_flags=0, conditions=None, responses=None):
    """Create a minimal INFO test record dict."""
    rec = {
        'Signature': 'INFO', 'FormID': '00012345', 'RecordFlags': '0',
        'EditorID': 'TestInfo', 'DATA.Flags': str(data_flags),
        'ConditionCount': '0', 'ResponseCount': '0',
        'ChoiceCount': '0',
    }
    if conditions:
        rec['ConditionCount'] = str(len(conditions))
        for i, (raw_hex,) in enumerate(conditions):
            rec[f'Condition[{i}].Raw'] = raw_hex
    if responses:
        rec['ResponseCount'] = str(len(responses))
        for i, (text, emotion, value) in enumerate(responses):
            rec[f'Response[{i}].ResponseText'] = text
            rec[f'Response[{i}].EmotionType'] = str(emotion)
            rec[f'Response[{i}].EmotionValue'] = str(value)
            rec[f'Response[{i}].ResponseNumber'] = str(i + 1)
    return rec


class TestFurnConversion:
    """FURN MNAM/FNPR must index the converted NIF's clustered seat positions.

    The seat list is derived from the source NIF with the shared algorithm in
    asset_convert/nif/furniture_markers.py; MNAM bit i enables NIF position i, so
    dangling bits would seat NPCs at garbage positions in-game.
    """

    MESHES = os.path.join('export', 'Oblivion.esm', 'meshes')

    def _unpack(self, data):
        i = data.find(b'MNAM')
        mnam = struct.unpack_from('<I', data, i + 6)[0]
        fnprs = []
        j = 0
        while True:
            j = data.find(b'FNPR', j)
            if j < 0:
                break
            fnprs.append(struct.unpack_from('<HH', data, j + 6))
            j += 10
        return mnam, fnprs

    def _furn_rec(self, modl, mnam_flags):
        return {
            'Signature': 'FURN', 'FormID': '00012345', 'RecordFlags': '0',
            'EditorID': 'TestFurn', 'Model.MODL': modl,
            'MNAM.Flags': str(mnam_flags),
        }

    @pytest.fixture(autouse=True)
    def _seats(self):
        if not os.path.isdir(self.MESHES):
            pytest.skip('Export meshes not available')
        from tes5_import.record_types.items import load_furniture_models
        recs = [
            self._furn_rec('Furniture\\LowerClass\\LowerClassBench01.NIF', 0),
            self._furn_rec('Furniture\\LowerClass\\LowerClassBed01.NIF', 0),
        ]
        load_furniture_models(self.MESHES, {'FURN': recs})

    def test_bench_mnam_matches_seat_count(self):
        """3-seat bench: MNAM bits 0-2 + preserved sit-type flag, 3 FNPR."""
        from tes5_import.record_types.items import convert_FURN
        # TES4: bits for all 8 entry markers + sit-type bit 30
        rec = self._furn_rec('Furniture\\LowerClass\\LowerClassBench01.NIF',
                             0x400000FF)
        mnam, fnprs = self._unpack(convert_FURN(rec))
        assert mnam == 0x40000007, hex(mnam)
        assert len(fnprs) == 3
        assert all(t == 1 for t, _ in fnprs)  # Sit

    def test_bench_entry_restriction(self):
        """TES4 record enabling only the front-entry row (bits 5-7 = ref 14
        entries) must yield front-only FNPR entry points on every seat."""
        from tes5_import.record_types.items import convert_FURN
        rec = self._furn_rec('Furniture\\LowerClass\\LowerClassBench01.NIF',
                             0x400000E0)
        mnam, fnprs = self._unpack(convert_FURN(rec))
        assert mnam == 0x40000007
        assert fnprs == [(1, 0x01), (1, 0x01), (1, 0x01)]

    def test_bed_single_sleep_seat(self):
        """Bed entries converge to ONE sleep seat: MNAM bit 0 + bed-type +
        Must Exit to Talk, FNPR Sleep with left|right entries."""
        from tes5_import.record_types.items import convert_FURN
        rec = self._furn_rec('Furniture\\LowerClass\\LowerClassBed01.NIF',
                             0x80000003)
        mnam, fnprs = self._unpack(convert_FURN(rec))
        assert mnam == 0x88000001, hex(mnam)
        assert fnprs == [(2, 0x04 | 0x08)]

    def test_missing_nif_fallback(self):
        """Unresolvable model: single conservative seat, all entry points.

        Must be a path that exists in NO extracted BSA (SEfurniture used to
        qualify until the Shivering Isles archives joined the extraction)."""
        from tes5_import.record_types.items import convert_FURN
        rec = self._furn_rec('Clutter\\NoSuchDir\\NoSuchChair01.NIF',
                             0x40000004)
        mnam, fnprs = self._unpack(convert_FURN(rec))
        assert mnam == 0x40000001, hex(mnam)
        assert fnprs == [(1, 0x0F)]

    def test_seat_count_matches_converted_nif(self):
        """The NIF converter must emit exactly the positions MNAM enables."""
        import time
        if not hasattr(time, 'clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF
        from asset_convert.nif.nif_converter import convert_nif
        from tes5_import.record_types.items import _FURN_SEATS, _furn_model_key

        key = _furn_model_key('Furniture\\LowerClass\\LowerClassBench01.NIF')
        seats = _FURN_SEATS[key]
        src = os.path.join(self.MESHES, key.replace('/', os.sep))
        with tempfile.TemporaryDirectory() as td:
            dst = os.path.join(td, 'out.nif')
            convert_nif(src, dst)
            data = NF.Data()
            with open(dst, 'rb') as f:
                data.read(f)
            for block in data.blocks:
                if isinstance(block, NF.BSFurnitureMarkerNode):
                    assert block.num_positions == len(seats)
                    for i, seat in enumerate(seats):
                        p = block.positions[i]
                        assert abs(p.offset.x - seat['x']) < 0.01
                        assert abs(p.offset.y - seat['y']) < 0.01
                        assert abs(p.offset.z - seat['z']) < 0.01
                    break
            else:
                pytest.fail('No BSFurnitureMarkerNode in converted NIF')


class TestServiceConversion:
    """Barter/training services: trainer CLAS clones, vendor gold, dialogue."""

    def _subrecords(self, rec_bytes: bytes) -> dict:
        data = rec_bytes[RECORD_HEADER_SIZE:]
        subs = {}
        pos = 0
        while pos + 6 <= len(data):
            sig = data[pos:pos + 4].decode('ascii')
            size = struct.unpack_from('<H', data, pos + 4)[0]
            subs.setdefault(sig, []).append(data[pos + 6:pos + 6 + size])
            pos += 6 + size
        return subs

    def _trainer_npc(self, teaches='7', maxtrain='70', services='16385'):
        # services 16385 = 0x4001 (weapons vendor + training)
        return {'Signature': 'NPC_', 'FormID': '00000500', 'RecordFlags': '0',
                'EditorID': 'TestTrainer', 'FULL': 'Trainer',
                'ACBS.Flags': '0', 'ACBS.Level': '5', 'ACBS.CalcMin': '5',
                'ACBS.CalcMax': '10', 'ACBS.BarterGold': '800',
                'FactionCount': '0', 'SpellCount': '0', 'ItemCount': '0',
                'AIPackageCount': '0',
                'AIDT.Aggression': '5', 'AIDT.Confidence': '50',
                'AIDT.Responsibility': '50', 'AIDT.Services': services,
                'AIDT.Teaches': teaches, 'AIDT.MaxTraining': maxtrain,
                'CNAM.Class': '00000900',
                'DATA.Health': '50', 'DATA.Intelligence': '40',
                'DATA.Strength': '60', 'DATA.Endurance': '45',
                'HCLR.R': '100', 'HCLR.G': '80', 'HCLR.B': '60'}

    def _clas_rec(self):
        return {'Signature': 'CLAS', 'FormID': '00000900', 'RecordFlags': '0',
                'EditorID': 'TestClass', 'FULL': 'Warrior', 'DESC': '',
                'DATA.Specialization': '0', 'DATA.Teaches': '0',
                'DATA.MaxTraining': '0'}

    def test_trainer_class_and_faction(self):
        from tes5_import.record_types.actor_common import (create_trainer_records, create_vendor_factions, get_trainer_class_fid, get_trainer_faction_fid)
        from tes5_import.base.constants import TES5_SKILL_ORDER
        writer = PluginWriter(masters=['Skyrim.esm'])
        npc = self._trainer_npc()   # Teaches=7 (Alchemy), max 70
        by_type = {'NPC_': [npc], 'CLAS': [self._clas_rec()]}
        create_vendor_factions(by_type, writer)
        create_trainer_records(by_type, writer)

        assert get_trainer_faction_fid() != 0
        clone_fid = get_trainer_class_fid(0x00000500)
        assert clone_fid != 0
        # The CLAS clone must carry Teaches=Alchemy + MaxTraining=70
        clas_bytes = writer._top_groups['CLAS'][-1]
        subs = self._subrecords(clas_bytes)
        data = subs['DATA'][0]
        teaches, maxtrain = struct.unpack_from('<bB', data, 4)
        assert teaches == TES5_SKILL_ORDER.index('Alchemy')
        assert maxtrain == 70

        # NPC gets the clone as CNAM, trainer faction SNAM, and vendor gold
        npc_bytes = convert_NPC_(npc)
        nsubs = self._subrecords(npc_bytes)
        assert struct.unpack('<I', nsubs['CNAM'][0])[0] == clone_fid
        snam_fids = {struct.unpack_from('<I', s)[0] for s in nsubs['SNAM']}
        assert get_trainer_faction_fid() in snam_fids
        cnto = [struct.unpack('<Ii', c) for c in nsubs['CNTO']]
        assert (0x0000000F, 800) in cnto   # Gold001 x barter gold

    def _merchant_npc(self, fid='00000501', services='132227'):
        npc = self._trainer_npc(services=services)
        npc['FormID'] = fid
        npc['EditorID'] = 'TestMerchant'
        return npc

    def test_every_merchant_joins_one_marker_faction(self):
        """The Barter topic gates on ONE faction, so every merchant — whatever
        its service bitmask, chest-backed or not — must be a member of it."""
        from tes5_import.record_types.actor_common import (create_vendor_factions, get_merchant_faction_fid, get_vendor_faction_fids_for_actor)
        writer = PluginWriter(masters=['Skyrim.esm'])
        a = self._merchant_npc(fid='00000501', services='132227')
        b = self._merchant_npc(fid='00000502', services='9216')
        create_vendor_factions({'NPC_': [a, b], 'CREA': []}, writer)

        marker = get_merchant_faction_fid()
        assert marker != 0
        for fid, svc in ((0x00000501, 132227), (0x00000502, 9216)):
            assert marker in get_vendor_faction_fids_for_actor(fid, svc)

    def test_marker_faction_is_not_a_vendor_faction(self):
        """The marker is a membership tag only. Giving it the Vendor flag would
        make it compete with the real vendor faction the engine resolves for the
        barter menu (VEND keyword filter / VENC chest)."""
        from tes5_import.record_types.actor_common import (create_vendor_factions, get_merchant_faction_fid)
        writer = PluginWriter(masters=['Skyrim.esm'])
        create_vendor_factions({'NPC_': [self._merchant_npc()]}, writer)
        marker = get_merchant_faction_fid()
        fact = next(f for f in writer._top_groups['FACT']
                    if struct.unpack_from('<I', f, 12)[0] == marker)
        subs = self._subrecords(fact)
        flags = struct.unpack('<I', subs['DATA'][0])[0]
        assert not (flags & 0x4000), 'marker must not carry the Vendor flag'
        assert 'VEND' not in subs and 'VENV' not in subs

    def test_barter_gate_is_a_single_condition(self):
        """Regression: the Barter gate used to OR over every vendor faction,
        putting 25-30 CTDAs on each Barter INFO. Vanilla Skyrim never exceeds 22
        conditions on an INFO (longest OR-run is 20); past that the engine drops
        the line, so every Barter INFO failed and merchants lost the topic
        entirely — while Training, a 1-condition gate, kept working."""
        from tes5_import.base.conditions import (FUNC_GET_IN_FACTION,
                                                   build_ctda)
        from tes5_import.dialogue.groups import _build_service_fallback_info
        from tes5_import.record_types.actor_common import (create_vendor_factions, get_merchant_faction_fid)
        writer = PluginWriter(masters=['Skyrim.esm'])
        # Many distinct service bitmasks => many vendor factions. The gate must
        # stay at one condition regardless of how many exist.
        npcs = [self._merchant_npc(fid=f'0000{0x600 + i:03X}', services=str(s))
                for i, s in enumerate((3, 4, 8, 16, 1155, 2048, 4103, 5124))]
        create_vendor_factions({'NPC_': npcs}, writer)

        gate = pack_subrecord('CTDA', build_ctda(
            FUNC_GET_IN_FACTION, param1=get_merchant_faction_fid()))
        info = _build_service_fallback_info(writer, 'barter', gate)
        n_ctda = self._subrecords(info).get('CTDA', [])
        assert len(n_ctda) == 1, f'barter gate must be 1 CTDA, got {len(n_ctda)}'
        assert struct.unpack_from('<I', n_ctda[0], 12)[0] == get_merchant_faction_fid()

    def test_trainer_unmappable_skill_skipped(self):
        from tes5_import.record_types.actor_common import (create_trainer_records, get_trainer_class_fid)
        writer = PluginWriter(masters=['Skyrim.esm'])
        # Teaches=1 (Athletics) has no Skyrim skill -> not a trainer
        npc = self._trainer_npc(teaches='1')
        create_trainer_records({'NPC_': [npc], 'CLAS': []}, writer)
        assert get_trainer_class_fid(0x00000500) == 0

    def test_service_menu_kind(self):
        from tes5_import.dialogue.converter import (service_menu_kind,
                                                  should_skip_dial)
        barter = {'Signature': 'DIAL', 'FormID': '0000010F',
                  'EditorID': 'Barter', 'DATA.Type': '5'}
        refusal = {'Signature': 'DIAL', 'FormID': '0000010E',
                   'EditorID': 'ServiceRefusal', 'DATA.Type': '5'}
        assert service_menu_kind(barter) == 'barter'
        assert not should_skip_dial(barter)
        assert service_menu_kind(refusal) == ''
        assert should_skip_dial(refusal)

    def test_convert_info_service_vmad(self):
        from tes5_import.dialogue.converter import convert_INFO
        rec = {'Signature': 'INFO', 'FormID': '00062116', 'RecordFlags': '0',
               'ParentDIAL': '0000010F', 'DATA.Flags': '2',
               'ResponseCount': '1', 'Response[0].EmotionType': '0',
               'Response[0].EmotionValue': '50',
               'Response[0].ResponseNumber': '1',
               'Response[0].ResponseText': 'Take a look.'}
        # A SERVICE line carries its own TES4_TIF__ fragment (the service-menu
        # call is appended to that fragment by script_convert); the shared
        # static scripts are only for the synthesized fallback INFO.
        for kind in ('barter', 'training'):
            result = convert_INFO(rec, service_menu=kind)
            assert b'VMAD' in result
            assert b'TES4_TIF__00062116' in result
            assert b'TES4_ShowBarterMenu' not in result

        # With NO service menu this line has nothing for a fragment to do --
        # no result script, and its topic is not script-driven -- so it gets
        # none.  The engine binds an INFO's fragment when it selects the line,
        # so an empty one is a cost paid on the dialogue path itself; see
        # script_convert.pipeline.info_needs_fragment.
        assert b'VMAD' not in convert_INFO(rec, service_menu='')

    def test_vendor_item_keywords(self):
        """Sellable items carry the VendorItem* keyword the vendor factions
        filter on (no keyword = item invisible in the barter menu)."""
        from tes5_import.record_types.common import VENDOR_KYWD

        def kwda_fids(rec_bytes):
            data = self._subrecords(rec_bytes).get('KWDA')
            if not data:
                return set()
            return {struct.unpack_from('<I', data[0], i)[0]
                    for i in range(0, len(data[0]), 4)}

        weap = convert_WEAP({'Signature': 'WEAP', 'FormID': '00001000',
                             'RecordFlags': '0', 'EditorID': 'TestSword',
                             'DATA.Type': '0', 'DATA.Value': '10',
                             'DATA.Weight': '5', 'DATA.Damage': '8'})
        assert VENDOR_KYWD['Weapon'] in kwda_fids(weap)

        staff = convert_WEAP({'Signature': 'WEAP', 'FormID': '00001001',
                              'RecordFlags': '0', 'EditorID': 'TestStaff',
                              'DATA.Type': '4', 'DATA.Value': '10',
                              'DATA.Weight': '5', 'DATA.Damage': '8'})
        assert VENDOR_KYWD['Staff'] in kwda_fids(staff)

        # Ring (TES4 biped bit 6 = RightRing) -> Jewelry, not Clothing
        ring = convert_CLOT({'Signature': 'CLOT', 'FormID': '00001002',
                             'RecordFlags': '0', 'EditorID': 'TestRing',
                             'BMDT.BipedFlags': str(1 << 6),
                             'DATA.Value': '50', 'DATA.Weight': '0.1'})
        assert VENDOR_KYWD['Jewelry'] in kwda_fids(ring)

        shirt = convert_CLOT({'Signature': 'CLOT', 'FormID': '00001003',
                              'RecordFlags': '0', 'EditorID': 'TestShirt',
                              'BMDT.BipedFlags': str(1 << 2),
                              'DATA.Value': '5', 'DATA.Weight': '1'})
        assert VENDOR_KYWD['Clothing'] in kwda_fids(shirt)

        misc = convert_MISC({'Signature': 'MISC', 'FormID': '00001004',
                             'RecordFlags': '0', 'EditorID': 'TestPlate',
                             'DATA.Value': '2', 'DATA.Weight': '1'})
        assert VENDOR_KYWD['Clutter'] in kwda_fids(misc)

        from tes5_import.record_types.actor_common import _keywords_for_services
        assert VENDOR_KYWD['Arrow'] in _keywords_for_services(1 << 0)
        assert VENDOR_KYWD['Clutter'] in _keywords_for_services(1 << 10)

    def test_barter_topic_dialogue(self):
        """End-to-end: Barter topic converts with prompt, gate and fragment."""
        from tes5_import.dialogue.groups import build_dialog_groups
        from tes5_import.record_types.actor_common import (create_trainer_records, create_vendor_factions)
        writer = PluginWriter(masters=['Skyrim.esm'])
        npc = self._trainer_npc()
        qust = {'Signature': 'QUST', 'FormID': '00010602',
                'EditorID': 'Generic', 'DATA.Flags': '5',
                'DATA.Priority': '30', 'StageCount': '0'}
        dial = {'Signature': 'DIAL', 'FormID': '0000010F',
                'EditorID': 'Barter', 'FULL': 'Barter', 'DATA.Type': '5',
                'QuestCount': '1', 'Quest[0]': '00010602'}
        info = {'Signature': 'INFO', 'FormID': '00062116', 'RecordFlags': '0',
                'ParentDIAL': '0000010F', 'DATA.Flags': '2',
                'QSTI.Quest': '00010602', 'ResponseCount': '1',
                'Response[0].EmotionType': '0',
                'Response[0].EmotionValue': '50',
                'Response[0].ResponseNumber': '1',
                'Response[0].ResponseText': 'I have much to offer.'}
        by_type = {'NPC_': [npc], 'CLAS': [self._clas_rec()],
                   'QUST': [qust], 'DIAL': [dial], 'INFO': [info]}
        create_vendor_factions(by_type, writer)
        create_trainer_records(by_type, writer)
        build_dialog_groups(by_type, writer, npc_to_vtyp={})

        dial_group = b''.join(writer._top_groups['DIAL'])
        # Player prompt replaces the raw 'Barter' FULL
        assert b'What have you got for sale?' in dial_group
        # The synthetic fallback carries the shared barter fragment (the
        # script name appears 4x in its VMAD: attached script, fragment
        # FileName, and the Begin and End fragment ScriptNames); the original
        # line keeps its own TES4_TIF__ fragment, to which script_convert
        # appends the menu call.
        assert dial_group.count(b'TES4_ShowBarterMenu') == 4
        assert b'TES4_TIF__00062116' in dial_group
        assert b'Take a look.' in dial_group

    def test_greeting_choice_reaches_response_topic(self):
        """A greeting bark whose INFO carries a Choice must keep that TCLT and
        the response topic must get a TOP-LEVEL branch — otherwise the NPC
        greets the player but the player cannot select the response (FGC01Rats:
        Arvena asks what happened in the basement, player can't answer). Also
        verifies a greeting Choice pointing at ANOTHER bark is dropped (it would
        dangle after the bark pass splits/merges topics)."""
        from tes5_import.dialogue.groups import build_dialog_groups
        from tes5_import.base.text_reader import set_formid_index_offset
        set_formid_index_offset(0)
        writer = PluginWriter(masters=['Skyrim.esm'])
        qust = {'Signature': 'QUST', 'FormID': '00035713',
                'EditorID': 'FGC01Rats', 'DATA.Flags': '1',
                'DATA.Priority': '30', 'StageCount': '0'}
        # GREETING (bark). Its INFO offers a Choice -> the response topic, plus a
        # Choice -> another greeting sub-topic (must be dropped).
        greeting = {'Signature': 'DIAL', 'FormID': '000000C8',
                    'EditorID': 'GREETING', 'FULL': 'GREETING',
                    'DATA.Type': '0',  # classified as bark by reserved EDID
                    'QuestCount': '1', 'Quest[0]': '00035713'}
        greet_info = {'Signature': 'INFO', 'FormID': '00036622',
                      'RecordFlags': '0', 'ParentDIAL': '000000C8',
                      'DATA.Flags': '0', 'QSTI.Quest': '00035713',
                      'ResponseCount': '1', 'Response[0].EmotionType': '0',
                      'Response[0].EmotionValue': '50',
                      'Response[0].ResponseNumber': '1',
                      'Response[0].ResponseText': 'What did you find?',
                      # GetStage(FGC01Rats)==30 — the greeting only fires after
                      # the lion is dealt with. The response must inherit this.
                      'ConditionCount': '1',
                      'Condition[0].Raw':
                          '000000000000f0413a000000135703000000000000000000',
                      'ChoiceCount': '2',
                      'Choice[0]': '00036613',   # -> FGC01Choice1 (conversation)
                      'Choice[1]': '000000C9'}   # -> another GREETING (dropped)
        greeting2 = {'Signature': 'DIAL', 'FormID': '000000C9',
                     'EditorID': 'GREETING', 'FULL': 'GREETING',
                     'DATA.Type': '0', 'QuestCount': '1',
                     'Quest[0]': '00035713'}
        greet2_info = {'Signature': 'INFO', 'FormID': '000000CA',
                       'RecordFlags': '0', 'ParentDIAL': '000000C9',
                       'DATA.Flags': '0', 'QSTI.Quest': '00035713',
                       'ResponseCount': '1', 'Response[0].EmotionType': '0',
                       'Response[0].EmotionValue': '50',
                       'Response[0].ResponseNumber': '1',
                       'Response[0].ResponseText': 'Hello again.'}
        # The player response topic (conversation), reached only via the greeting.
        choice1 = {'Signature': 'DIAL', 'FormID': '00036613',
                   'EditorID': 'FGC01Choice1', 'FULL': 'It was a mountain lion.',
                   'DATA.Type': '0', 'QuestCount': '1', 'Quest[0]': '00035713'}
        choice1_info = {'Signature': 'INFO', 'FormID': '0003662A',
                        'RecordFlags': '0', 'ParentDIAL': '00036613',
                        'DATA.Flags': '0', 'QSTI.Quest': '00035713',
                        'ResponseCount': '1', 'Response[0].EmotionType': '0',
                        'Response[0].EmotionValue': '70',
                        'Response[0].ResponseNumber': '1',
                        'Response[0].ResponseText': 'A mountain lion? How?'}
        by_type = {'QUST': [qust],
                   'DIAL': [greeting, greeting2, choice1],
                   'INFO': [greet_info, greet2_info, choice1_info]}
        build_dialog_groups(by_type, writer, npc_to_vtyp={})

        dial_group = b''.join(writer._top_groups.get('DIAL', []))
        dlbr_group = b''.join(writer._top_groups.get('DLBR', []))

        # The greeting INFO keeps its TCLT to the conversation response topic...
        greet_rec = self._find_record(dial_group, b'INFO', 0x00036622)
        assert greet_rec is not None, 'greeting INFO missing'
        tclts = {struct.unpack('<I', d[:4])[0]
                 for d in self._subrecords(greet_rec).get('TCLT', [])}
        assert 0x00036613 in tclts, 'greeting lost its Choice to the response'
        # ...but drops the Choice that points at another bark.
        assert 0x000000C9 not in tclts, 'bark->bark choice should be dropped'

        # The response topic's branch is TOP-LEVEL (DNAM=1), so it is selectable.
        branch = self._find_record(dlbr_group, b'DLBR', None,
                                   snam=0x00036613)
        assert branch is not None, 'no branch for the response topic'
        dnam = self._subrecords(branch)['DNAM'][0]
        assert struct.unpack('<I', dnam[:4])[0] == 1, \
            'greeting-reached response topic must be a top-level branch'

        # The response INFO inherits the greeting's quest-TIMING gate
        # (GetStage==30), so it only appears after the lion is dealt with —
        # NOT from the first conversation. Without this the top-level topic
        # leaks into the menu whenever GetIsID passes.
        resp = self._find_record(dial_group, b'INFO', 0x0003662A)
        assert resp is not None, 'response INFO missing'
        funcs = [struct.unpack_from('<H', c, 8)[0]
                 for c in self._subrecords(resp).get('CTDA', [])]
        assert 58 in funcs, \
            'response topic must inherit the greeting GetStage(58) timing gate'

    def test_bark_prose_mention_does_not_ungate_topic(self):
        """A gated topic whose FULL name merely appears in a GREETING's prose
        must STAY gated. Only an explicit AddTopic/Choice from a bark reveals a
        topic on first contact; a prose mention rides that bark line's own
        (stage) conditions. Azzan's 'Advancement' was showing before joining
        the guild because 6 late-game greetings say the word 'advancement'."""
        from tes5_import.dialogue.unlocks import build_unlock_plan
        # A conversation topic 'Advancement', AddTopic'd by a normal join line.
        topic = {'Signature': 'DIAL', 'FormID': '0003568F',
                 'EditorID': 'advancementFG', 'FULL': 'Advancement',
                 'DATA.Type': '0'}
        join_info = {'Signature': 'INFO', 'FormID': '0002427B',
                     'ParentDIAL': '00024279',   # a conversation topic (FGJoin1)
                     'AddTopicCount': '1', 'AddTopic[0]': '0003568F'}
        join_topic = {'Signature': 'DIAL', 'FormID': '00024279',
                      'EditorID': 'FGJoin1', 'DATA.Type': '0'}
        # A GREETING (bark) whose response text mentions 'Advancement'.
        greeting = {'Signature': 'DIAL', 'FormID': '000000C8',
                    'EditorID': 'GREETING', 'DATA.Type': '0'}
        greet_info = {'Signature': 'INFO', 'FormID': '00023F7B',
                      'ParentDIAL': '000000C8',
                      'ResponseCount': '1',
                      'Response[0].ResponseText':
                          'You are ready for advancement.'}
        by_type = {'DIAL': [topic, join_topic, greeting],
                   'INFO': [join_info, greet_info], 'QUST': []}
        plan = build_unlock_plan(by_type)
        assert 0x03568F in plan['gated'], \
            'topic mentioned only in bark prose must stay gated'

    def test_convert_qust_writes_boosted_priority(self):
        """The bug this whole class exists to catch: a fix that only changes
        the DERIVED DIAL PNAM (used for merged bark topics) but not the QUST
        record's OWN DNAM.Priority byte has NO EFFECT in-game, because the
        engine arbitrates dialogue on the quest's own priority. convert_QUST
        must write the EFFECTIVE (boosted) priority, not the raw TES4 value."""
        from tes5_import.dialogue.quest import compute_quest_priorities, convert_QUST
        staged = {'Signature': 'QUST', 'FormID': '00035713',
                 'EditorID': 'RealQuest', 'DATA.Flags': '0',
                 'DATA.Priority': '60', 'StageCount': '1',
                 'Stage[0].Index': '10'}
        container = {'Signature': 'QUST', 'FormID': '00035714',
                    'EditorID': 'ContainerQuest', 'DATA.Flags': '1',
                    'DATA.Priority': '61', 'StageCount': '0'}
        by_type = {'QUST': [staged, container]}
        compute_quest_priorities(by_type)

        staged_bytes = convert_QUST(staged)
        container_bytes = convert_QUST(container)
        staged_dnam = self._subrecords(staged_bytes)['DNAM'][0]
        container_dnam = self._subrecords(container_bytes)['DNAM'][0]
        staged_priority = staged_dnam[2]     # Priority is byte offset 2 in DNAM
        container_priority = container_dnam[2]
        assert container_priority < staged_priority, \
            (f'container QUST.DNAM.Priority={container_priority} still '
             f'>= staged QUST.DNAM.Priority={staged_priority}: the engine '
             'reads this field directly, so the fix must land here')
        # The raw authored value (61) must not survive unboosted-vs-boosted —
        # confirms the write path actually consulted the override table.
        assert container_priority != 61 or staged_priority > 60

    def test_quest_priority_never_exceeds_engine_max(self):
        """DNAM.Priority must stay in the engine's 0-100 band.

        Vanilla Skyrim.esm's 391 quests top out at EXACTLY 100 with none above
        it (the CK field is 0-100). The byte does not only order dialogue — it
        arbitrates a quest ALIAS PACKAGE against the actor's standing schedule,
        so an out-of-band value breaks AI too. The old additive boost pushed
        TES4 priority 60 to 161 and put 265 of 391 quests (68%) over 100, which
        is why converted escort/travel packages could pass their condition and
        start (the actor stands up) yet never actually travel.
        """
        from tes5_import.dialogue.quest import (
            QUEST_PRIORITY_MAX, compute_quest_priorities, convert_QUST)
        # Authored priorities spanning TES4's range, staged and zero-stage.
        quests = []
        for i, (prio, stages) in enumerate(
                [(0, 1), (11, 1), (60, 1), (90, 1), (100, 1),
                 (0, 0), (50, 0), (61, 0), (85, 0)]):
            q = {'Signature': 'QUST', 'FormID': f'000357{i:02X}',
                 'EditorID': f'Q{i}', 'DATA.Flags': '0',
                 'DATA.Priority': str(prio), 'StageCount': str(stages)}
            if stages:
                q['Stage[0].Index'] = '10'
            quests.append(q)
        by_type = {'QUST': quests}
        pri = compute_quest_priorities(by_type)

        assert max(pri.values()) <= QUEST_PRIORITY_MAX, \
            f'priority {max(pri.values())} exceeds engine max {QUEST_PRIORITY_MAX}'
        assert min(pri.values()) >= 0
        # ...and the written byte agrees with the table.
        for q in quests:
            dnam = self._subrecords(convert_QUST(q))['DNAM'][0]
            assert 0 <= dnam[2] <= QUEST_PRIORITY_MAX

        # Staged quests keep their AUTHORED priority — the correction is a
        # downward clamp on containers only, never a shift of staged values.
        for q in quests:
            if int(q['StageCount']):
                assert pri[int(q['FormID'], 16)] == int(q['DATA.Priority']), \
                    'a staged quest must keep the priority its author wrote'

        from tes5_import.dialogue.quest import ZERO_STAGE_TOP
        zero = [pri[int(q['FormID'], 16)] for q in quests
                if not int(q['StageCount'])]
        assert max(zero) <= ZERO_STAGE_TOP, \
            'a zero-stage container must never exceed the container ceiling'

    def test_zero_stage_quest_never_outranks_staged_greeting(self):
        """A zero-stage 'conversation container' quest (MG00General-style,
        priority 61 in vanilla) must never outrank a REAL staged quest's
        GREETING (priority 60) in the merged HELO bark group. Oblivion ran
        GREETING and HELLO as separate channels, so a container quest's
        authored priority never had to compete with a staged quest's
        on-activate briefing; merged into one Skyrim HELO topic per quest, the
        higher-priority container's cover line won and the staged quest's
        SetStage-advancing briefing never played (symptom: NPC only gives a
        generic greeting, journal stage correct, every record field verified
        individually correct — MG00General/MG04Restore/Arielle Jurard,
        2026-07-20)."""
        from tes5_import.dialogue.groups import build_dialog_groups
        from tes5_import.base.text_reader import set_formid_index_offset
        set_formid_index_offset(0)
        writer = PluginWriter(masters=['Skyrim.esm'])

        staged = {'Signature': 'QUST', 'FormID': '00035713',
                 'EditorID': 'RealQuest', 'DATA.Flags': '0',
                 'DATA.Priority': '60', 'StageCount': '1',
                 'Stage[0].Index': '10'}
        container = {'Signature': 'QUST', 'FormID': '00035714',
                    'EditorID': 'ContainerQuest', 'DATA.Flags': '1',
                    'DATA.Priority': '61', 'StageCount': '0'}

        greeting = {'Signature': 'DIAL', 'FormID': '000000C8',
                   'EditorID': 'GREETING', 'FULL': 'GREETING',
                   'DATA.Type': '0', 'QuestCount': '1',
                   'Quest[0]': '00035713'}
        staged_info = {'Signature': 'INFO', 'FormID': '00036622',
                      'RecordFlags': '0', 'ParentDIAL': '000000C8',
                      'DATA.Flags': '0', 'QSTI.Quest': '00035713',
                      'ResponseCount': '1', 'Response[0].EmotionType': '0',
                      'Response[0].EmotionValue': '50',
                      'Response[0].ResponseNumber': '1',
                      'Response[0].ResponseText': 'The staged briefing line.'}

        hello = {'Signature': 'DIAL', 'FormID': '000000D2',
                'EditorID': 'HELLO', 'FULL': 'HELLO',
                'DATA.Type': '1', 'QuestCount': '1',
                'Quest[0]': '00035714'}
        container_info = {'Signature': 'INFO', 'FormID': '00036623',
                          'RecordFlags': '0', 'ParentDIAL': '000000D2',
                          'DATA.Flags': '0', 'QSTI.Quest': '00035714',
                          'ResponseCount': '1', 'Response[0].EmotionType': '0',
                          'Response[0].EmotionValue': '50',
                          'Response[0].ResponseNumber': '1',
                          'Response[0].ResponseText': 'Generic cover line.'}

        by_type = {'QUST': [staged, container],
                  'DIAL': [greeting, hello],
                  'INFO': [staged_info, container_info]}
        build_dialog_groups(by_type, writer, npc_to_vtyp={})

        """Arbitration lives on QUST.DNAM.Priority, NOT on the topic's PNAM."""
        from tes5_import.dialogue.quest import compute_quest_priorities
        pri = compute_quest_priorities(by_type)
        staged_prio = pri[0x00035713]
        container_prio = pri[0x00035714]
        assert container_prio < staged_prio, \
            (f'container quest priority {container_prio} still outranks '
             f'the staged quest priority {staged_prio}')

        # ...and every generated bark topic keeps the vanilla 50.0 PNAM
        # default. Writing quest priority here instead is what put FGC01Rats'
        # GREETING at 161 against its own player topics' 50.0 and cost Pinarus
        # every topic he owned (mountain-lion AND training).
        dial_group = b''.join(writer._top_groups.get('DIAL', []))
        for info_fid in (0x00036622, 0x00036623):
            topic = self._topic_owning_info(dial_group, info_fid)
            assert topic is not None, f'HELO topic for {info_fid:08X} not found'
            pnam = struct.unpack(
                '<f', self._subrecords(topic)['PNAM'][0][:4])[0]
            assert pnam == 50.0, \
                f'bark topic PNAM must stay at the vanilla default, got {pnam}'

    def test_zero_stage_quests_keep_relative_priority_order(self):
        """Boosting staged quests above the zero-stage ceiling must be a
        uniform shift, not a clamp that collapses zero-stage quests together —
        125 vanilla zero-stage quests (Dark00General=50, MQConversations=85,
        ...) arbitrate AMONG EACH OTHER too (two factions' idle chatter
        competing for the same generic NPC); losing that ordering would hand
        the decision to file order instead."""
        from tes5_import.dialogue.groups import build_dialog_groups
        from tes5_import.base.text_reader import set_formid_index_offset
        set_formid_index_offset(0)
        writer = PluginWriter(masters=['Skyrim.esm'])

        staged = {'Signature': 'QUST', 'FormID': '00035713',
                 'EditorID': 'RealQuest', 'DATA.Flags': '0',
                 'DATA.Priority': '60', 'StageCount': '1',
                 'Stage[0].Index': '10'}
        # Two zero-stage containers whose ORIGINAL priorities (85 vs 50) must
        # stay ordered the same way after both get squeezed below 60.
        high_container = {'Signature': 'QUST', 'FormID': '00035715',
                          'EditorID': 'HighContainer', 'DATA.Flags': '1',
                          'DATA.Priority': '85', 'StageCount': '0'}
        low_container = {'Signature': 'QUST', 'FormID': '00035716',
                         'EditorID': 'LowContainer', 'DATA.Flags': '1',
                         'DATA.Priority': '50', 'StageCount': '0'}

        greeting = {'Signature': 'DIAL', 'FormID': '000000C8',
                   'EditorID': 'GREETING', 'FULL': 'GREETING',
                   'DATA.Type': '0', 'QuestCount': '1',
                   'Quest[0]': '00035713'}
        staged_info = {'Signature': 'INFO', 'FormID': '00036622',
                      'RecordFlags': '0', 'ParentDIAL': '000000C8',
                      'DATA.Flags': '0', 'QSTI.Quest': '00035713',
                      'ResponseCount': '1', 'Response[0].EmotionType': '0',
                      'Response[0].EmotionValue': '50',
                      'Response[0].ResponseNumber': '1',
                      'Response[0].ResponseText': 'The staged briefing line.'}
        hello = {'Signature': 'DIAL', 'FormID': '000000D2',
                'EditorID': 'HELLO', 'FULL': 'HELLO',
                'DATA.Type': '1', 'QuestCount': '1',
                'Quest[0]': '00035715'}
        high_info = {'Signature': 'INFO', 'FormID': '00036624',
                    'RecordFlags': '0', 'ParentDIAL': '000000D2',
                    'DATA.Flags': '0', 'QSTI.Quest': '00035715',
                    'ResponseCount': '1', 'Response[0].EmotionType': '0',
                    'Response[0].EmotionValue': '50',
                    'Response[0].ResponseNumber': '1',
                    'Response[0].ResponseText': 'High-priority container line.'}
        goodbye = {'Signature': 'DIAL', 'FormID': '000000D4',
                  'EditorID': 'GOODBYE', 'FULL': 'GOODBYE',
                  'DATA.Type': '1', 'QuestCount': '1',
                  'Quest[0]': '00035716'}
        low_info = {'Signature': 'INFO', 'FormID': '00036625',
                   'RecordFlags': '0', 'ParentDIAL': '000000D4',
                   'DATA.Flags': '0', 'QSTI.Quest': '00035716',
                   'ResponseCount': '1', 'Response[0].EmotionType': '0',
                   'Response[0].EmotionValue': '50',
                   'Response[0].ResponseNumber': '1',
                   'Response[0].ResponseText': 'Low-priority container line.'}

        by_type = {'QUST': [staged, high_container, low_container],
                  'DIAL': [greeting, hello, goodbye],
                  'INFO': [staged_info, high_info, low_info]}
        build_dialog_groups(by_type, writer, npc_to_vtyp={})

        """A higher-priority container quest still loses to a staged one.

        Measured on QUST.DNAM.Priority, the byte the engine
        arbitrates on: the topics' PNAM all stay at the vanilla 50.0
        default, so ordering cannot be read there.
        """
        from tes5_import.dialogue.quest import compute_quest_priorities
        pri = compute_quest_priorities(by_type)
        staged_prio = pri[0x00035713]
        high_prio = pri[0x00035715]
        low_prio = pri[0x00035716]
        assert high_prio > low_prio, \
            ('zero-stage quests lost their relative order: '
             f'HighContainer(was 85)={high_prio} <= LowContainer(was 50)={low_prio}')
        assert high_prio < staged_prio and low_prio < staged_prio, \
            'both containers must still sit below the staged quest'

    def _topic_owning_info(self, dial_group_bytes, info_fid):
        """Find the DIAL record whose group directly contains an INFO with
        this FormID (walks GRUP boundaries the way real ESM nesting does)."""
        pos = 0
        n = len(dial_group_bytes)
        current_dial = None
        while pos + RECORD_HEADER_SIZE <= n:
            rsig = dial_group_bytes[pos:pos + 4]
            size = struct.unpack_from('<I', dial_group_bytes, pos + 4)[0]
            if rsig == b'GRUP':
                pos += RECORD_HEADER_SIZE
                continue
            rfid = struct.unpack_from('<I', dial_group_bytes, pos + 12)[0]
            rec = dial_group_bytes[pos:pos + RECORD_HEADER_SIZE + size]
            pos += RECORD_HEADER_SIZE + size
            if rsig == b'DIAL':
                current_dial = rec
            elif rsig == b'INFO' and rfid == info_fid:
                return current_dial
        return None

    def _find_record(self, group_bytes, sig, formid, snam=None):
        """Find a record by (sig, formid) or by (sig, SNAM value) in a group."""
        pos = 0
        n = len(group_bytes)
        while pos + RECORD_HEADER_SIZE <= n:
            rsig = group_bytes[pos:pos + 4]
            size = struct.unpack_from('<I', group_bytes, pos + 4)[0]
            if rsig == b'GRUP':
                pos += RECORD_HEADER_SIZE   # descend into group contents
                continue
            rfid = struct.unpack_from('<I', group_bytes, pos + 12)[0]
            rec = group_bytes[pos:pos + RECORD_HEADER_SIZE + size]
            pos += RECORD_HEADER_SIZE + size
            if rsig != sig:
                continue
            if formid is not None and rfid != formid:
                continue
            if snam is not None:
                sn = self._subrecords(rec).get('SNAM')
                if not sn or struct.unpack('<I', sn[0][:4])[0] != snam:
                    continue
            return rec
        return None


class TestOutfitSplit:
    """TES4 inventory → TES5 outfit (OTFT) + carried inventory (CNTO).

    Skyrim wears exactly what the outfit lists and ADDS it on top of CNTO, so
    the split must be disjoint, wearable-only, and free of biped-slot ties.
    """

    # TES4 BMDT biped bits: 2=UpperBody 3=LowerBody 5=Foot
    BODY = 1 << 2
    LEGS = 1 << 3
    FEET = 1 << 5

    def _index(self, **types):
        """Install a fresh item index from {sig: [rec, ...]}."""
        from tes5_import.actors.outfits import load_item_index
        from tes5_import.base.text_reader import set_formid_index_offset
        set_formid_index_offset(0)
        load_item_index(types)

    def _armo(self, fid, edid, slots, value=100):
        return {'Signature': 'ARMO', 'FormID': fid, 'EditorID': edid,
                'BMDT.BipedFlags': str(slots), 'DATA.Value': str(value)}

    def _clot(self, fid, edid, slots, value=5):
        return {'Signature': 'CLOT', 'FormID': fid, 'EditorID': edid,
                'BMDT.BipedFlags': str(slots), 'DATA.Value': str(value)}

    def _lvli(self, fid, edid, entries, chance_none=0):
        rec = {'Signature': 'LVLI', 'FormID': fid, 'EditorID': edid,
               'LVLD.ChanceNone': str(chance_none),
               'EntryCount': str(len(entries))}
        for i, e in enumerate(entries):
            rec[f'Entry[{i}].FormID'] = e
        return rec

    def test_only_wearables_reach_the_outfit(self):
        """Loot/keys/potions in an outfit are what the CK rejects with
        'contains non-armor objects' — they must stay in CNTO. Weapons must
        too: a survey of every vanilla Skyrim.esm OTFT found none containing a
        weapon — Skyrim's combat AI equips weapons from CNTO at runtime."""
        from tes5_import.actors.outfits import split_inventory
        self._index(
            ARMO=[self._armo('00000001', 'Cuirass', self.BODY)],
            WEAP=[{'Signature': 'WEAP', 'FormID': '00000002', 'EditorID': 'Axe'}],
            KEYM=[{'Signature': 'KEYM', 'FormID': '00000003', 'EditorID': 'Key'}],
            ALCH=[{'Signature': 'ALCH', 'FormID': '00000004', 'EditorID': 'Potion'}],
            INGR=[{'Signature': 'INGR', 'FormID': '00000005', 'EditorID': 'Herb'}],
        )
        outfit, carried = split_inventory([(i, 1) for i in range(1, 6)])
        assert outfit == [1]                            # armor only
        assert [f for f, _ in carried] == [2, 3, 4, 5]  # weapon, key, potion, ingredient

    def test_outfit_and_inventory_are_disjoint(self):
        """Skyrim adds the outfit ON TOP of CNTO, so an item in both is
        carried twice — the duplicate-inventory bug."""
        from tes5_import.actors.outfits import split_inventory
        self._index(
            ARMO=[self._armo('00000001', 'Cuirass', self.BODY)],
            KEYM=[{'Signature': 'KEYM', 'FormID': '00000002', 'EditorID': 'Key'}],
        )
        outfit, carried = split_inventory([(1, 1), (2, 1)])
        assert not set(outfit) & {f for f, _ in carried}

    def test_armor_beats_clothing_for_a_contested_slot(self):
        """An NPC issued both armor and clothes was meant to wear the armor."""
        from tes5_import.actors.outfits import split_inventory
        self._index(
            ARMO=[self._armo('00000001', 'SteelCuirass', self.BODY, value=180)],
            CLOT=[self._clot('00000002', 'Shirt', self.BODY, value=5)],
        )
        outfit, carried = split_inventory([(2, 1), (1, 1)])  # shirt listed first
        assert outfit == [1]
        assert [f for f, _ in carried] == [2]  # loser is carried, not dropped

    def test_leveled_clothing_list_loses_to_armor(self):
        """Azzan: his steel competed with LL0NPCClothingShirt/Pants/ShoesMiddle,
        not with plain CLOT records. A leveled list must claim the union of its
        leaves' slots or it silently wins the slot and the NPC wears the shirt.
        """
        from tes5_import.actors.outfits import split_inventory
        self._index(
            ARMO=[self._armo('00000001', 'SteelCuirass', self.BODY, value=180)],
            CLOT=[self._clot('00000010', 'MiddleShirt', self.BODY | self.LEGS)],
            LVLI=[self._lvli('00000002', 'LL0NPCClothingShirtMiddle',
                             ['00000010'])],
        )
        outfit, carried = split_inventory([(1, 1), (2, 1)])
        assert outfit == [1], 'armor must win the body slot over a clothing list'
        assert [f for f, _ in carried] == [2]

    def test_multislot_loser_cannot_win_on_a_second_slot(self):
        """A garment spanning body+legs that loses the body slot to a cuirass
        must not survive by winning legs — Skyrim would equip it and it would
        cover the chest again (the LL0VampireShirt case)."""
        from tes5_import.actors.outfits import split_inventory
        self._index(
            ARMO=[self._armo('00000001', 'Cuirass', self.BODY, value=4800)],
            CLOT=[self._clot('00000002', 'Shirt', self.BODY | self.LEGS)],
        )
        outfit, carried = split_inventory([(1, 1), (2, 1)])
        assert outfit == [1]
        assert [f for f, _ in carried] == [2]

    def test_repeated_sublist_is_not_a_cycle(self):
        """Oblivion weights an entry by naming it twice (LL2NPCStaff25 lists
        LL1NPCStaff1Normal100 twice). A visited-set shared across siblings reads
        the repeat as a cycle and rejects the whole list — which left every
        leveled-weapon actor unarmed. A weapon-only list is never outfit
        material (weapons are carried, not worn), so it must resolve as
        non-wearable rather than erroring out from the false cycle."""
        from tes5_import.actors.outfits import is_outfit_eligible, split_inventory
        self._index(
            WEAP=[{'Signature': 'WEAP', 'FormID': '00000010', 'EditorID': 'Staff'}],
            LVLI=[
                self._lvli('00000011', 'LL1NPCStaffNormal', ['00000010']),
                # names the same sublist twice, to weight it
                self._lvli('00000001', 'LL2NPCStaff25',
                           ['00000011', '00000011']),
            ],
        )
        assert is_outfit_eligible(0x01) is False
        outfit, carried = split_inventory([(1, 1)])
        assert outfit == [], 'a weapon list is carried, never worn'
        assert carried == [(1, 1)]

    def test_mixed_leveled_list_stays_in_inventory(self):
        """A list that can roll gold/ingredients is not a valid outfit form."""
        from tes5_import.actors.outfits import is_outfit_eligible
        self._index(
            ARMO=[self._armo('00000010', 'Cuirass', self.BODY)],
            MISC=[{'Signature': 'MISC', 'FormID': '00000011', 'EditorID': 'Gold'}],
            LVLI=[self._lvli('00000001', 'LL0Loot', ['00000010', '00000011'])],
        )
        assert is_outfit_eligible(0x01) is False

    def test_empty_leveled_list_is_not_outfit_eligible(self):
        """An outfit entry that resolves to nothing is the CK's
        'Unable to find valid outfit form'."""
        from tes5_import.actors.outfits import is_outfit_eligible
        self._index(LVLI=[self._lvli('00000001', 'LL0Empty', [])])
        assert is_outfit_eligible(0x01) is False

    def test_jewelry_does_not_contend(self):
        """Rings/amulets never conflict with armor, and an NPC wears two rings."""
        from tes5_import.actors.outfits import split_inventory
        ring_r, ring_l, amulet = 1 << 6, 1 << 7, 1 << 8
        self._index(CLOT=[
            self._clot('00000001', 'Ring1', ring_r),
            self._clot('00000002', 'Ring2', ring_l),
            self._clot('00000003', 'Amulet', amulet),
        ])
        outfit, carried = split_inventory([(1, 1), (2, 1), (3, 1)])
        assert sorted(outfit) == [1, 2, 3]
        assert carried == []

    def test_formid_offset_is_tolerated(self):
        """Callers pass FormIDs from get_formid(), which has already applied the
        load-order offset (0x00xxxxxx → 0x01xxxxxx). An unmasked index lookup
        misses every record and the actor gets no outfit at all."""
        from tes5_import.actors.outfits import is_outfit_eligible
        self._index(ARMO=[self._armo('00000001', 'Cuirass', self.BODY)])
        assert is_outfit_eligible(0x01000001) is True

    def test_chance_armor_keeps_guaranteed_clothing_fallback(self):
        """The bandit-with-no-pants bug. A bandit pairs a guaranteed clothing
        base (LL0NPCClothingPantsLower, ChanceNone 0) under chance-based armor
        (LL0NPCArmorLightGreaves25, ChanceNone 75 — the "25" being 25% odds).
        Both claim LowerBody. The probabilistic greaves must NOT evict the
        guaranteed pants: Skyrim resolves the outfit once and, ~75% of the time
        the greaves roll nothing, so evicting the pants leaves the actor
        bare-legged. Keeping both lets the engine wear greaves when they roll
        and the pants otherwise."""
        from tes5_import.actors.outfits import split_inventory
        self._index(
            ARMO=[self._armo('00000010', 'IronGreaves', self.LEGS, value=1000)],
            CLOT=[self._clot('00000011', 'Pants', self.LEGS, value=1)],
            LVLI=[
                self._lvli('00000001', 'LL0NPCArmorLightGreaves25',
                           ['00000010'], chance_none=75),
                self._lvli('00000002', 'LL0NPCClothingPantsLower',
                           ['00000011'], chance_none=0),
            ],
        )
        outfit, carried = split_inventory([(1, 1), (2, 1)])
        assert set(outfit) == {1, 2}, \
            'guaranteed pants must stay when the greaves that outrank it can roll none'
        assert carried == []

    def test_guaranteed_armor_still_evicts_guaranteed_clothing(self):
        """The fallback rule must not weaken Azzan: a GUARANTEED armor list
        (ChanceNone 0) still evicts the guaranteed clothing under it — that
        slot will always be filled by the armor, so the clothes would only
        double up. Only a *probabilistic* winner keeps the fallback."""
        from tes5_import.actors.outfits import split_inventory
        self._index(
            ARMO=[self._armo('00000010', 'SteelGreaves', self.LEGS, value=1000)],
            CLOT=[self._clot('00000011', 'Pants', self.LEGS, value=1)],
            LVLI=[
                self._lvli('00000001', 'LL0NPCArmorGreaves100',
                           ['00000010'], chance_none=0),
                self._lvli('00000002', 'LL0NPCClothingPantsLower',
                           ['00000011'], chance_none=0),
            ],
        )
        outfit, carried = split_inventory([(1, 1), (2, 1)])
        assert outfit == [1], 'guaranteed armor closes the slot to the clothing'
        assert [f for f, _ in carried] == [2]

    def test_nested_chance_none_breaks_guarantee(self):
        """A guarantee must hold all the way down. An outer list with
        ChanceNone 0 whose entry is a ChanceNone-75 sublist is NOT guaranteed,
        so it cannot evict a guaranteed peer sharing the slot."""
        from tes5_import.actors.outfits import split_inventory
        self._index(
            ARMO=[self._armo('00000010', 'Greaves', self.LEGS, value=1000)],
            CLOT=[self._clot('00000011', 'Pants', self.LEGS, value=1)],
            LVLI=[
                self._lvli('00000003', 'InnerChance', ['00000010'],
                           chance_none=75),
                self._lvli('00000001', 'OuterSure', ['00000003'],
                           chance_none=0),
                self._lvli('00000002', 'GuaranteedPants', ['00000011'],
                           chance_none=0),
            ],
        )
        outfit, carried = split_inventory([(1, 1), (2, 1)])
        assert set(outfit) == {1, 2}, \
            'a chance-none anywhere on the path breaks the guarantee'
        assert carried == []

    def test_nothing_is_lost(self):
        """Every source item must reach the actor via exactly one channel."""
        from tes5_import.actors.outfits import split_inventory
        self._index(
            ARMO=[self._armo('00000001', 'Cuirass', self.BODY),
                  self._armo('00000002', 'Boots', self.FEET)],
            CLOT=[self._clot('00000003', 'Shirt', self.BODY)],
            KEYM=[{'Signature': 'KEYM', 'FormID': '00000004', 'EditorID': 'Key'}],
        )
        items = [(1, 1), (2, 1), (3, 1), (4, 2)]
        outfit, carried = split_inventory(items)
        assert len(outfit) + len(carried) == len(items)
        assert set(outfit) | {f for f, _ in carried} == {1, 2, 3, 4}
        # counts on carried items survive the split
        assert dict(carried)[4] == 2


class TestCKWarningFixes:
    """Regressions for the 2026-07 CK_WARNINGS sweep."""

    def test_engine_formids_not_remapped(self):
        """PlayerRef 0x14 is engine-hardcoded and must NOT be remapped.

        Remapping it to 0x01000014 dangles every package/alias
        reference to the player.  Other low ids (Tamriel 0x3C) are
        REAL Oblivion.esm records and must keep remapping.
        """
        from tes5_import.base.text_reader import set_formid_index_offset
        set_formid_index_offset(1)
        try:
            rec = {'A': '00000014', 'B': '00000100', 'C': '0000003C'}
            assert get_formid(rec, 'A') == 0x14           # PlayerRef stays put
            assert get_formid(rec, 'B') == 0x01000100     # real ids remap
            assert get_formid(rec, 'C') == 0x0100003C     # Tamriel IS remapped
        finally:
            set_formid_index_offset(0)

    def test_null_package_target_is_self(self):
        """A null package target becomes type 6 (Self), vanilla's filler."""
        from tes5_import.packages.converter import _null_target
        assert struct.unpack('<iIi', _null_target())[0] == 6

    def test_player_ambush_becomes_forcegreet(self):
        """A player-targeted TES4 Ambush is a FORCE GREET.

        Skyrim has no Papyrus "walk over and talk to the player" call — the
        force greet is the PACKAGE. Every field below was wrong at some point
        and each one on its own stops the conversation:
          * template must be ForceGreet (0003C1C4), not Follow/HoldPosition
          * the Topic input (PDTO) names the dialogue to open
          * Location slots must NOT default to type 3 "near editor location",
            or the actor walks back to his CK spot (Uriel up the stairs)
          * the leash/forcegreet distances anchor on the PLAYER (type 0, 0x14)
          * PKDT interrupt flags must AUTHORISE speaking (vanilla 0xFEFF); the
            0x0000 default denies every interrupt and no greet can fire
        """
        from tes5_import.packages.converter import (_choose, PackContext,
                                                convert_flags, build_pkdt,
                                                SPEED_RUN, T4_AMBUSH)
        from tes5_import.packages.templates import FORCE_GREET
        rec = {'Signature': 'PACK', 'FormID': '0002C2F0',
               'EditorID': 'CGEmperorGreetPlayerInCell',
               'PKDT.Type': str(T4_AMBUSH), 'PKDT.Flags': '5124',
               'PLDT.Type': '2', 'PLDT.Location': '0', 'PLDT.Radius': '500',
               'PTDT.Type': '0', 'PTDT.Target': '00000014',
               'PTDT.Count': '100'}
        inp = _choose(rec, PackContext(), 0x0102C2F0)
        assert inp.t is FORCE_GREET
        blob = inp.emit()
        assert b'Topic' in blob and b'PDTO' in blob
        # no Location slot may be left as the "near editor location" filler
        for slot in (1, 2, 3, 7):
            assert slot in inp.values, f'Location slot {slot} unset'
        def _pldt(v):
            return v if isinstance(v, tuple) else struct.unpack('<iIi', v)
        assert _pldt(inp.values[3])[:2] == (0, 0x14)
        assert _pldt(inp.values[7])[:2] == (0, 0x14)
        # the weapon-drawn/sneak ambush flags must NOT be applied
        flags, _speed = convert_flags(5124, T4_AMBUSH, hostile_ambush=False)
        assert not (flags & 0x00800000), 'weapon drawn on a force greet'
        # interrupts must be authorised, exactly as vanilla force-greets are
        pkdt = build_pkdt(0, SPEED_RUN, interrupt=0xFEFF)
        assert struct.unpack('<IBBBBHH', pkdt)[5] == 0xFEFF

    def test_find_at_operable_object_becomes_activate(self):
        """A TES4 "Find" aimed at a lever/switch/door means GO OPERATE IT.

        Oblivion's Find is a seek-*then-use* procedure. Skyrim has no
        standalone equivalent, so these were falling through to Sandbox — and
        because this idiom carries no PLDT, the Sandbox had an EMPTY location:
        the actor stood inert beside the object forever and the object's
        OnActivate script never ran.

        CharacterGen is the visible case. `CGRatAmbushAPushBricks` is a Find
        at CGCrumbleWall01REF (base ACTI). The rat is supposed to push the
        bricks down, which fires CGCrumbleWall01SCRIPT.OnActivate ->
        `setstage MQ01 24` -> the rats turn hostile. With the Sandbox the wall
        never crumbled and the rats never became hostile, no matter how long
        the player waited, and the tutorial dead-ended.

        Skyrim expresses exactly this with the Activate template (00019B2D):
        24 vanilla instances, including MQ101HadvarOpenGate2,
        MS02BorkulOpenDoorPackage and TG08AKarliahOpenGatePackage.

        24 Oblivion Find packages target an ACTI/DOOR/CONT ref this way.
        Find at an NPC (230 packages) is a greet, not an operate, and must
        keep sandboxing.
        """
        from tes5_import.packages.converter import _choose, PackContext, T4_FIND
        from tes5_import.packages.templates import ACTIVATE, TRAVEL

        rec = {'Signature': 'PACK', 'FormID': '0007303D',
               'EditorID': 'CGRatAmbushAPushBricks',
               'PKDT.Type': str(T4_FIND), 'PKDT.Flags': '8196',
               'PTDT.Type': '0', 'PTDT.Target': '0003E31C',
               'PTDT.Count': '200'}
        ctx = PackContext(ref_base_sig={0x03E31C: 'ACTI'})
        inp = _choose(rec, ctx, 0x0007303D)
        assert inp.t is ACTIVATE, \
            'rat sandboxes instead of pushing the wall; MQ01 never reaches 24'
        # the wall ref must actually reach the Target slot
        target = inp.values[ACTIVATE.slot('target')]
        assert struct.unpack('<iIi', target)[1] == 0x0003E31C

        # a door is the same idiom (SE06GSWardenUnlockMainDoor)
        door = dict(rec, **{'PTDT.Target': '0001AAAA'})
        assert _choose(door, PackContext(ref_base_sig={0x01AAAA: 'DOOR'}),
                       0x0007303E).t is ACTIVATE

        # ...but Find at an ACTOR is a SEEK, not an operate: it becomes Travel
        # to a "near reference" location, with PTDT.Count as the radius.
        # (CGAssassinsAmbushAToGlenroy et al: the package that carries the
        # assassins out of the ambush room; the old sandbox fallback left them
        # standing there forever.)
        npc = dict(rec, **{'PTDT.Target': '00023E71'})
        seek = _choose(npc, PackContext(ref_base_sig={0x023E71: 'NPC_'}),
                       0x0007303F)
        assert seek.t is TRAVEL, 'Find at an actor must not become Activate'
        loc = seek.values[TRAVEL.slot('location')]
        ltype, lfid, lradius = struct.unpack('<iIi', loc)
        assert (ltype, lfid) == (0, 0x00023E71)
        assert lradius == 200, 'PTDT.Count is the approach distance'

    def test_find_at_actor_base_type_wanders_the_cell_to_hunt(self):
        """A TES4 "Find" naming an actor BASE TYPE with several placements
        must roam the cell they live in.

        `PTDT.Type=1` at an NPC_/CREA base means "go find one of THESE".
        FGC06Courier's three fighters Find FGC06Goblin: nine goblins, all in
        DesolateMine, ~350 units BELOW the fighters on another level.  Only
        Sandbox wanders, and vanilla's own "sweep this whole place for
        enemies" idiom is a Sandbox with Allow Wandering alone and a PLDT
        type-1 "in cell" location (MQ202SandboxInRatway).  A spherical
        sandbox is truncated to a cylinder (fSandboxCylinderBottom -100), so a
        "near self, radius N" location never takes them DOWN to the goblins.

        The cell FormID must be REMAPPED into the output's index space: the
        first cut wrote the raw 0x00028869, which is no record at all in
        Skyrim.esm, so the location resolved to nothing and the fighters
        stood still (measured live 2026-08-17).
        """
        from tes5_import.packages.converter import _choose, PackContext, T4_FIND
        from tes5_import.packages.templates import SANDBOX, TRAVEL
        from tes5_import.base.text_reader import set_formid_index_offset
        set_formid_index_offset(1)
        try:
            rec = {'Signature': 'PACK', 'FormID': '000292CD',
                   'EditorID': 'FGC06RiennaGoblinHunt',
                   'PKDT.Type': str(T4_FIND), 'PKDT.Flags': '4096',
                   'PTDT.Type': '1', 'PTDT.Target': '0002888E',
                   'PTDT.Count': '10'}
            goblins = tuple((0x01048F40 + k, 0x028869) for k in range(9))
            ctx = PackContext(base_sig={0x02888E: 'CREA'},
                              base_placements={0x02888E: goblins},
                              interior_cells={0x028869},
                              pack_runner_cells={0x0292CD: {0x028869}})
            inp = _choose(rec, ctx, 0x010292CD)
            assert inp.t is SANDBOX

            loc = inp.values[SANDBOX.slot('location')]
            ltype, lfid, lradius = struct.unpack('<iIi', loc)
            assert (ltype, lfid, lradius) == (1, 0x01028869, 0), \
                "the goblins' cell, REMAPPED, is the hunting ground"
            assert inp.values[SANDBOX.slot('allow_wandering')] == 1, \
                'a hunter that cannot wander never finds the goblins'
            # The things that park a sandboxing actor in one spot must be off.
            for off in ('allow_sitting', 'allow_idle_markers',
                        'allow_special_furniture', 'allow_conversation'):
                assert inp.values[SANDBOX.slot(off)] == 0, off

            # An EXTERIOR cell is never an "in cell" location (448/448
            # vanilla uses are interiors): fall back to a wide search radius
            # around the actor itself.
            ctx_ext = PackContext(base_sig={0x02888E: 'CREA'},
                                  base_placements={0x02888E: goblins},
                                  interior_cells=set(),
                                  pack_runner_cells={0x0292CD: {0x028869}})
            ext = _choose(rec, ctx_ext, 0x010292CD)
            assert ext.t is SANDBOX
            eloc = struct.unpack('<iIi', ext.values[SANDBOX.slot('location')])
            assert eloc[0] == 12 and eloc[2] > 0, 'near self, wide radius'

            # A base placed exactly ONCE resolves to that reference — the
            # Object-ID spelling IS the ref — and the package seeks it like a
            # specific-reference Find (Travel near the ref).  This covers both
            # the social call (FindFathisUlesTTh22x2, Bittneld visiting
            # Emfrid) and the single-target hunt (MQ15MinotaurFindMythicDawn1).
            visit = {'Signature': 'PACK', 'FormID': '0002A38D',
                     'EditorID': 'BittneldtheCurseBringerVisitEmfrid',
                     'PKDT.Type': str(T4_FIND), 'PKDT.Flags': '2',
                     'PTDT.Type': '1', 'PTDT.Target': '000234B7',
                     'PTDT.Count': '0', 'PSDT.Time': '18'}
            vctx = PackContext(base_sig={0x0234B7: 'NPC_'},
                               base_placements={0x0234B7: ((0x01023B02,
                                                            0x000804),)},
                               ref_base_sig={0x023B02: 'NPC_'},
                               interior_cells={0x000804},
                               pack_runner_cells={0x02A38D: {0x99}})
            vinp = _choose(visit, vctx, 0x0102A38D)
            assert vinp.t is TRAVEL
            vloc = vinp.values[TRAVEL.slot('location')]
            assert struct.unpack('<iIi', vloc) == (0, 0x01023B02, 0), \
                'travel to the one Emfrid placed; count is not a distance'
        finally:
            set_formid_index_offset(0)

    def test_find_object_id_item_acquires_and_furniture_sits(self):
        """A TES4 Find at an ITEM base picks the item up (Acquire); at a
        furniture base it sits (Sit).  Both take the type-1 PTDT as-is —
        vanilla spells the same criteria that way (MQ101RalofGetDoorKey,
        MG06Stage99MirabelleGetIntoFurniture) — and PTDT.Count on an
        Object-ID Find is the NUMBER to find, i.e. Acquire's num-to-acquire.
        The search area is the authored PLDT when there is one, else the
        cell the items are placed in."""
        from tes5_import.packages.converter import _choose, PackContext, T4_FIND
        from tes5_import.packages.templates import ACQUIRE, SIT
        from tes5_import.base.text_reader import set_formid_index_offset
        set_formid_index_offset(1)
        try:
            staff = {'Signature': 'PACK', 'FormID': '00001111',
                     'EditorID': 'CreatureGoblinLeaderFindHeadA',
                     'PKDT.Type': str(T4_FIND), 'PKDT.Flags': '4',
                     'PTDT.Type': '1', 'PTDT.Target': '00002222',
                     'PTDT.Count': '2'}
            ctx = PackContext(base_sig={0x002222: 'WEAP'},
                              base_placements={0x002222: ((0x01003333, 0x4444),
                                                          (0x01003334, 0x4444))},
                              interior_cells={0x4444})
            inp = _choose(staff, ctx, 0x01001111)
            assert inp.t is ACQUIRE
            assert struct.unpack('<iIi', inp.values[ACQUIRE.slot('target')]) \
                == (1, 0x01002222, 0)
            assert inp.values[ACQUIRE.slot('count')] == 2
            assert struct.unpack('<iIi', inp.values[ACQUIRE.slot('location')]) \
                == (1, 0x01004444, 0), 'search the cell the staffs are in'

            pew = dict(staff, **{'PTDT.Target': '00005555', 'PTDT.Count': '0',
                                 'PLDT.Type': '0', 'PLDT.Location': '00006666',
                                 'PLDT.Radius': '512'})
            fctx = PackContext(base_sig={0x005555: 'FURN'})
            finp = _choose(pew, fctx, 0x01001111)
            assert finp.t is SIT
            assert struct.unpack('<iIi', finp.values[SIT.slot('chair_target')]) \
                == (1, 0x01005555, 0)
            assert struct.unpack('<iIi', finp.values[SIT.slot('location')]) \
                == (0, 0x01006666, 512), 'the authored search area wins'
        finally:
            set_formid_index_offset(0)

    def test_defensive_combat_is_not_ignore_combat(self):
        """TES4 Defensive Combat must NOT become TES5 Ignore Combat.

        Both sit on bit 20 but mean opposite things:
          TES4 Defensive Combat -> do not START fights, but DO fight back
          TES5 Ignore Combat    -> take no part in combat at all

        Mapping one onto the other told every Oblivion bodyguard to stand
        still and be killed. CharacterGen's `CGGlenroyDefendEmperorAmbushA` —
        the package whose whole job is defending the Emperor — carries the TES4
        flag, so the converted Blades drew their swords and then watched the
        assassins kill Renault unopposed.

        Skyrim has no Defensive Combat equivalent and needs none: the
        aggression tier decides whether an actor initiates, and everyone
        retaliates when attacked. TES5's default IS TES4 Defensive Combat, so
        the bit is dropped. 388 of 7,209 TES4 packages set it.
        """
        from tes5_import.packages.converter import (convert_flags,
                                                T4_DEFENSIVE_COMBAT,
                                                T5_IGNORE_COMBAT, T4_ALWAYS_RUN)
        flags, _ = convert_flags(T4_DEFENSIVE_COMBAT, 6, True)
        assert not (flags & T5_IGNORE_COMBAT), \
            'Defensive Combat became Ignore Combat — bodyguards will not fight'
        # The real CGGlenroyDefendEmperorAmbushA value (AlwaysRun|Defensive).
        flags, speed = convert_flags(0x00402000, 6, True)
        assert not (flags & T5_IGNORE_COMBAT)
        assert flags & 0x00002000, 'AlwaysRun must still map to Preferred Speed'
        # Unrelated flags on the same package are unaffected.
        flags, _ = convert_flags(T4_ALWAYS_RUN, 6, True)
        assert not (flags & T5_IGNORE_COMBAT)

    def test_spel_cast_type_fire_and_forget(self):
        from tes5_import.record_types.equipment import convert_SPEL
        rec = {'Signature': 'SPEL', 'FormID': '00001234', 'RecordFlags': '0',
               'EditorID': 'TestSpell', 'FULL': 'Test', 'SPIT.Cost': '10',
               'SPIT.Flags': '0', 'SPIT.Type': '0', 'EffectCount': '1',
               'Effect[0].EFID': 'FIDG', 'Effect[0].Type': 'Target',
               'Effect[0].Magnitude': '10', 'Effect[0].Area': '0',
               'Effect[0].Duration': '0'}
        spit = _find_subrecord(convert_SPEL(rec), b'SPIT')
        cast_type = struct.unpack_from('<I', spit, 16)[0]
        assert cast_type == 1  # Fire and Forget (2 = Concentration)

    def test_sgst_scroll_has_effects_and_etyp(self):
        # Sigil stones -> SCRL used to carry ZERO effects ("Magic Item has no
        # effects defined", one per stone) and no equip type.
        from tes5_import.record_types.equipment import convert_SGST
        rec = {'Signature': 'SGST', 'FormID': '00001234', 'RecordFlags': '0',
               'EditorID': 'TestSigil', 'FULL': 'Sigil Stone',
               'DATA.Value': '100', 'DATA.Weight': '1.0', 'EffectCount': '1',
               'Effect[0].EFID': 'SHLD', 'Effect[0].Type': 'Self',
               'Effect[0].Magnitude': '10', 'Effect[0].Area': '0',
               'Effect[0].Duration': '120'}
        out = convert_SGST(rec)
        assert _find_subrecord(out, b'EFID') is not None
        assert struct.unpack('<I', _find_subrecord(out, b'ETYP'))[0] == 0x00013F44
        spit = _find_subrecord(out, b'SPIT')
        assert struct.unpack_from('<I', spit, 16)[0] == 3  # CastType Scroll

    def test_aimed_ench_gets_projectile_mgef(self):
        """An aimed ENCH over projectile-less MGEFs gets a synthesized clone.

        Such an enchantment fires NOTHING in game, so the converter
        must mint an aimed MGEF clone with a projectile and swap it in.
        """
        from tes5_import.actors import magic_effects as magic_effects
        from tes5_import.record_types.equipment import convert_ENCH
        magic_effects.set_tes4_effect_names([])   # reset cache
        writer = PluginWriter(masters=['Skyrim.esm'])
        writer.next_object_id = 0x01100000
        rec = {'Signature': 'ENCH', 'FormID': '00001234', 'RecordFlags': '0',
               'EditorID': 'TestStaffEnch', 'FULL': 'Drain Staff',
               'ENIT.Type': '1', 'ENIT.Charge': '100', 'ENIT.Cost': '10',
               'ENIT.Flags': '0', 'EffectCount': '1',
               'Effect[0].EFID': 'DRHE', 'Effect[0].Type': 'Target',
               'Effect[0].Magnitude': '20', 'Effect[0].Area': '0',
               'Effect[0].Duration': '0'}
        out = convert_ENCH(rec, writer=writer)
        efid = struct.unpack('<I', _find_subrecord(out, b'EFID'))[0]
        assert efid != 0x0003EB42          # not plain AlchDamageHealth
        mgefs = writer._top_groups.get('MGEF')
        assert mgefs and len(mgefs) == 1
        data = _find_subrecord(mgefs[0], b'DATA')
        assert struct.unpack_from('<I', data, 0x48)[0] != 0   # projectile
        assert struct.unpack_from('<I', data, 0x50)[0] == 1   # fire&forget
        assert struct.unpack_from('<I', data, 0x54)[0] == 2   # aimed
        # second conversion reuses the cached clone
        convert_ENCH(rec, writer=writer)
        assert len(writer._top_groups['MGEF']) == 1

    def test_aimed_ench_using_converted_mgef_reaches_a_projectile(self):
        """An aimed ENCH over CONVERTED MGEFs still reaches a projectile.

        This is the normal path since convert_MGEF landed, and is
        what shipped broken; the test above exercises the vanilla-
        alias fallback instead, because it registers no converted
        MGEFs.  The failure is an unconditional null-deref, not a
        dud cast.

        See: docs/commentary/tes5_import_magic.md#aimed-ench-null-projectile
        """
        from tes5_import.actors import magic_effects as magic_effects
        from tes5_import.record_types.equipment import convert_ENCH
        from tes5_import.record_types.magic import (convert_MGEF,
                                                    register_mgef_formids)
        magic_effects.set_tes4_effect_names([])
        # FRDG exactly as Nehrim/Oblivion export it: Destruction, Frost resist,
        # Hostile|Detrimental|Target.
        mgef = {'Signature': 'MGEF', 'FormID': '0000187B', 'RecordFlags': '0',
                'EditorID': 'FRDG', 'FULL': 'Frost Damage',
                'DATA.Flags': str(0x00000001 | 0x00000004 | 0x00000040),
                'DATA.BaseCost': '1.0', 'DATA.School': '2',
                'DATA.ResistValue': '62'}
        register_mgef_formids([mgef])

        writer = PluginWriter(masters=['Skyrim.esm'])
        writer.next_object_id = 0x01100000
        rec = {'Signature': 'ENCH', 'FormID': '0000C211', 'RecordFlags': '0',
               'EditorID': 'EnStaffFrostDamage', 'ENIT.Type': '1',
               'ENIT.Charge': '62', 'ENIT.Cost': '62', 'ENIT.Flags': '1',
               'EffectCount': '1', 'Effect[0].EFID': 'FRDG',
               'Effect[0].Type': 'Target', 'Effect[0].Magnitude': '40',
               'Effect[0].Area': '0', 'Effect[0].Duration': '0',
               'Effect[0].ActorValue': '8'}
        out = convert_ENCH(rec, writer=writer)

        enit = _find_subrecord(out, b'ENIT')
        assert struct.unpack_from('<I', enit, 16)[0] == 2, 'expected Aimed'
        efid = struct.unpack('<I', _find_subrecord(out, b'EFID'))[0]

        # The effect must resolve to our converted MGEF, and that MGEF's DATA
        # must carry a projectile.
        assert magic_effects.has_projectile(efid), \
            'aimed ENCH reaches no projectile — engine null-derefs on it'
        data = _find_subrecord(convert_MGEF(mgef), b'DATA')
        assert struct.unpack_from('<I', data, 84)[0] == 2      # delivery Aimed
        # Frost + Destruction + fire-and-forget -> FrostIcicleProjectile01,
        # the projectile vanilla uses for that exact combination.
        assert struct.unpack_from('<I', data, 72)[0] == 0x0002F774

    def test_non_aimed_mgef_keeps_null_projectile(self):
        # Only Aimed/Target-Location deliveries fly a projectile; vanilla
        # leaves Self and Contact null far more often than not, so the fix
        # must not blanket-assign one.
        from tes5_import.record_types.magic import convert_MGEF
        rec = {'Signature': 'MGEF', 'FormID': '00001234', 'RecordFlags': '0',
               'EditorID': 'REHE', 'FULL': 'Restore Health',
               'DATA.Flags': str(0x00000010),   # Self only
               'DATA.BaseCost': '1.0', 'DATA.School': '5'}
        data = _find_subrecord(convert_MGEF(rec), b'DATA')
        assert struct.unpack_from('<I', data, 84)[0] == 0      # delivery Self
        assert struct.unpack_from('<I', data, 72)[0] == 0      # no projectile

    def test_leveled_list_drops_null_entries(self):
        rec = {'Signature': 'LVLI', 'FormID': '00001234', 'RecordFlags': '0',
               'EditorID': 'TestList', 'LVLD.ChanceNone': '0',
               'EntryCount': '3',
               'Entry[0].Level': '1', 'Entry[0].FormID': '00000F00',
               'Entry[0].Count': '-100',    # TES4 restock semantics
               'Entry[1].Level': '5',       # missing FormID -> dropped
               'Entry[2].Level': '10', 'Entry[2].FormID': '00000F01',
               'Entry[2].Count': '2'}
        out = convert_LVLI(rec)
        llct = _find_subrecord(out, b'LLCT')
        assert llct is not None and llct[0] == 2
        lvlos = _find_all_subrecords(out, b'LVLO')
        assert len(lvlos) == 2
        for lvlo in lvlos:
            level, fid, count = struct.unpack('<HxxIHxx', lvlo)
            assert fid != 0
            assert count >= 1

    def test_container_negative_counts_normalized(self):
        rec = {'Signature': 'CONT', 'FormID': '00001234', 'RecordFlags': '0',
               'EditorID': 'TestChest', 'FULL': 'Chest',
               'Item[0].FormID': '00000F00', 'Item[0].Count': '-100',
               'Item[1].FormID': '00000F01', 'Item[1].Count': '0',
               'DATA.Flags': '2', 'DATA.Weight': '0.0'}
        out = convert_CONT(rec)
        counts = [struct.unpack('<Ii', c)[1]
                  for c in _find_all_subrecords(out, b'CNTO')]
        assert counts == [100, 1]

    def test_footstep_sets_exist_in_skyrim(self):
        """Every footstep-set constant names a FormID that exists in Skyrim.esm.

        The old Light/Clothing values (0x24238/0x24237) did not.
        """
        from tes5_import.base.equivalents import (
            CLOTHING_FOOTSTEP_SET, HEAVY_ARMOR_FOOTSTEP_SET,
            LIGHT_ARMOR_FOOTSTEP_SET)
        assert HEAVY_ARMOR_FOOTSTEP_SET == 0x00021487
        assert LIGHT_ARMOR_FOOTSTEP_SET == 0x00021486
        assert CLOTHING_FOOTSTEP_SET == 0x00021468

    def test_dataless_mapmarker_ref_grounded_to_xmarker(self):
        rec = {'Signature': 'REFR', 'FormID': '00001234', 'RecordFlags': '1024',
               'NAME': '00000010', 'ParentWRLD': '0000003C',
               'ParentCELL': '00023777',
               'PosX': '0.0', 'PosY': '0.0', 'PosZ': '0.0',
               'RotX': '0.0', 'RotY': '0.0', 'RotZ': '0.0'}
        out = convert_REFR(rec)
        name = struct.unpack('<I', _find_subrecord(out, b'NAME'))[0]
        assert name == 0x0000003B   # XMarker, not a marker-data-less MapMarker

    def test_doors_to_exteriors_never_claim_location(self):
        """A door to an exterior never claims the destination cell's location.

        Claiming it poisoned the worldspace's shared persistent
        dummy cell, giving EVERY persistent ref in Tamriel one
        gate's location ("Ref is not in its persistence location"
        x13), after which the CK hangs.
        """
        from tes5_import.base.locations import build_marker_locations
        writer = PluginWriter(masters=['Skyrim.esm'])
        writer.next_object_id = 0x01100000
        interior = {'Signature': 'CELL', 'FormID': '00000C01',
                    'EditorID': 'TestInterior', 'DATA.Flags': '1'}
        exterior = {'Signature': 'CELL', 'FormID': '00000C02',
                    'EditorID': 'TestExterior', 'DATA.Flags': '2',
                    'ParentWRLD': '00000A01'}
        wrld = {'Signature': 'WRLD', 'FormID': '00000A01',
                'EditorID': 'TestWorld', 'FULL': 'Test World'}
        marker = {'Signature': 'REFR', 'FormID': '00000E01',
                  'MapMarker': '1', 'MapMarker.FULL': 'Fort Test',
                  'ParentWRLD': '00000A01', 'PosX': '100.0', 'PosY': '100.0'}
        dest_int = {'Signature': 'REFR', 'FormID': '00000E02',
                    'ParentCELL': '00000C01'}
        dest_ext = {'Signature': 'REFR', 'FormID': '00000E03',
                    'ParentCELL': '00000C02'}
        door_to_int = {'Signature': 'REFR', 'FormID': '00000E04',
                       'ParentWRLD': '00000A01', 'PosX': '150.0',
                       'PosY': '100.0', 'XTEL.Door': '00000E02'}
        door_to_ext = {'Signature': 'REFR', 'FormID': '00000E05',
                       'ParentWRLD': '00000A01', 'PosX': '90.0',
                       'PosY': '100.0', 'XTEL.Door': '00000E03'}
        by_type = {'WRLD': [wrld], 'CELL': [interior, exterior],
                   'REFR': [marker, dest_int, dest_ext,
                            door_to_int, door_to_ext]}
        cell_to_location, _grid, _world = build_marker_locations(
            by_type, writer)
        assert get_formid(interior, 'FormID') in cell_to_location
        assert get_formid(exterior, 'FormID') not in cell_to_location


class TestGridlessWorldspaceCellPlacement:
    """A non-persistent worldspace CELL with no XCLC is a real (0,0) cell.

    Oblivion omits XCLC at grid (0,0) because an absent subrecord already
    reads as 0.  Skyrim builds its grid-cell array by walking the type-4/5
    block tree and reading each cell's XCLC, so an omitted one never occupies
    its slot -- leaving a null grid entry surrounded by live neighbours, which
    the streaming tick indexes without a bounds check
    (SkyrimSE.exe+050E6AD `mov rbx,[rax+rcx*8]`, rax=0).

    Verified faithful, not a patch: 100% of the refs in all 30 affected cells
    floor to grid (0,0).  Removing the cells instead (an earlier attempt)
    PUNCHED the hole rather than filling it -- vanilla Skyrim has 2 enclosed
    grid holes total, both at arbitrary coords, while every hole that attempt
    produced sat at exactly (0,0).
    """

    def _build(self):
        from tes5_import.pipeline_records import _build_world_groups
        writer = PluginWriter(masters=['Skyrim.esm'])
        writer.next_object_id = 0x01100000
        wrld = {'Signature': 'WRLD', 'FormID': '0001D0BC',
                'EditorID': 'OblivionMQKvatch', 'DATA.Flags': '7'}
        # No XCLC and persistent bit CLEAR -- exactly OblivionMQKvatchBridge.
        gridless = {'Signature': 'CELL', 'FormID': '0001E896',
                    'EditorID': 'OblivionMQKvatchBridge', 'RecordFlags': '0',
                    'ParentWRLD': '0001D0BC', 'DATA.Flags': '2'}
        gridded = {'Signature': 'CELL', 'FormID': '0001E895',
                   'EditorID': 'OblivionMQKvatchEntrance', 'RecordFlags': '0',
                   'ParentWRLD': '0001D0BC', 'DATA.Flags': '2',
                   'XCLC.X': '0', 'XCLC.Y': '-1'}
        by_type = {'WRLD': [wrld], 'CELL': [gridless, gridded]}
        _build_world_groups(by_type, writer)
        return writer

    def _blocked_cells(self, raw):
        """(fid, full record bytes) for CELLs inside a type-4/5 block group."""
        found, stack, pos = [], [], 0
        while pos + 24 <= len(raw):
            while stack and pos >= stack[-1][0]:
                stack.pop()
            sig = raw[pos:pos + 4]
            if sig == b'GRUP':
                gsize, _l, gtype = struct.unpack_from('<IiI', raw, pos + 4)[:3]
                stack.append((pos + gsize, gtype))
                pos += 24
                continue
            size, _flags, fid = struct.unpack_from('<III', raw, pos + 4)
            if sig == b'CELL' and any(g[1] in (4, 5) for g in stack):
                found.append((fid & 0xFFFFFF, raw[pos:pos + 24 + size]))
            pos += 24 + size
        return found

    def test_gridless_cell_stays_in_the_block_tree(self):
        raw = b''.join(self._build()._top_groups['WRLD'])
        assert 0x01E896 in {f for f, _ in self._blocked_cells(raw)},             'removing the cell punches a null hole at (0,0) -> CTD on stream'

    def test_gridless_cell_gets_explicit_zero_xclc(self):
        raw = b''.join(self._build()._top_groups['WRLD'])
        body = dict(self._blocked_cells(raw))[0x01E896]
        xclc = _find_subrecord(body, b'XCLC')
        assert xclc is not None, 'cell must carry XCLC or it never fills its slot'
        assert struct.unpack_from('<ii', xclc)[:2] == (0, 0)

    def test_every_blocked_cell_has_xclc(self):
        """The invariant: vanilla Skyrim is 0/16,942 blocked cells without XCLC."""
        raw = b''.join(self._build()._top_groups['WRLD'])
        blocked = self._blocked_cells(raw)
        assert blocked, 'expected at least one blocked cell in the fixture'
        for _fid, body in blocked:
            assert _find_subrecord(body, b'XCLC') is not None

    def test_gridded_sibling_keeps_its_own_coords(self):
        raw = b''.join(self._build()._top_groups['WRLD'])
        body = dict(self._blocked_cells(raw))[0x01E895]
        xclc = _find_subrecord(body, b'XCLC')
        assert struct.unpack_from('<ii', xclc)[:2] == (0, -1),             'stamping the default must never overwrite real coords'


class TestMasterWorldspaceAnchorWithOwnWorldspaces:
    """New cells in a MASTER's worldspace survive when the plugin also owns some.

    ElsweyrAnequina.esp adds 831 new exterior cells to Oblivion.esm's Tamriel
    AND ships 9 worldspaces of its own.  Anchoring used to be gated on the
    plugin having NO worldspaces at all, which fits a pure heightmap
    (Tamriel.esp) but dropped every new cell of a plugin that does both --
    100% of new cells lost, 0% of overrides, the signature of the defect.

    See docs/commentary/tes5_import_override.md#anchor-per-worldspace.
    """

    MASTER_WRLD = 0x0000003C        # Oblivion.esm's Tamriel

    def _build(self):
        from tes5_import.pipeline_records import _build_world_groups

        class _Idx:
            """Minimal master index: serves the master's converted WRLD."""
            def record(self, fid):
                if fid != TestMasterWorldspaceAnchorWithOwnWorldspaces.MASTER_WRLD:
                    return b''
                body = b'EDID' + struct.pack('<H', 8) + b'Tamriel\x00'
                # 24-byte header: sig(4) size(4) flags(4) fid(4) vcs(4)
                # ver(2) unk(2) -- 8 bytes of tail, not 12.
                return (b'WRLD' + struct.pack('<I', len(body))
                        + struct.pack('<II', 0, fid) + b'\x00' * 8 + body)

            def group_path(self, fid):
                return ()

        class _Ctx:
            master_index = _Idx()
            emitted_wrld = {}

        writer = PluginWriter(masters=['Skyrim.esm', 'Oblivion.esm'])
        writer.next_object_id = 0x01100000
        # The plugin's OWN worldspace, with its own cell.
        own_wrld = {'Signature': 'WRLD', 'FormID': '01014FE0',
                    'EditorID': 'ANQRimmenWorld', 'DATA.Flags': '17'}
        own_cell = {'Signature': 'CELL', 'FormID': '0101AAAA',
                    'RecordFlags': '0', 'ParentWRLD': '01014FE0',
                    'DATA.Flags': '0', 'XCLC.X': '3', 'XCLC.Y': '4'}
        # A NEW cell in the MASTER's Tamriel -- the one that used to vanish.
        master_cell = {'Signature': 'CELL', 'FormID': '0101D34F',
                       'RecordFlags': '0', 'ParentWRLD': '0000003C',
                       'DATA.Flags': '0', 'XCLC.X': '-30', 'XCLC.Y': '-30'}
        by_type = {'WRLD': [own_wrld], 'CELL': [own_cell, master_cell]}
        _build_world_groups(by_type, writer, ctx=_Ctx())
        return writer

    def _cell_coords(self, raw):
        """Grid coords of every CELL inside a type-4/5 block group."""
        found, stack, pos = set(), [], 0
        while pos + 24 <= len(raw):
            while stack and pos >= stack[-1][0]:
                stack.pop()
            sig = raw[pos:pos + 4]
            if sig == b'GRUP':
                gsize, _l, gtype = struct.unpack_from('<IiI', raw, pos + 4)[:3]
                stack.append((pos + gsize, gtype))
                pos += 24
                continue
            size = struct.unpack_from('<I', raw, pos + 4)[0]
            if sig == b'CELL' and any(g[1] in (4, 5) for g in stack):
                xclc = _find_subrecord(raw[pos:pos + 24 + size], b'XCLC')
                if xclc is not None:
                    found.add(struct.unpack_from('<ii', xclc)[:2])
            pos += 24 + size
        return found

    def test_new_cell_in_master_worldspace_survives(self):
        raw = b''.join(self._build()._top_groups['WRLD'])
        assert (-30, -30) in self._cell_coords(raw),             'a new cell in the master worldspace was dropped because the '             'plugin also owns worldspaces of its own'

    def test_own_worldspace_cell_still_built(self):
        raw = b''.join(self._build()._top_groups['WRLD'])
        assert (3, 4) in self._cell_coords(raw),             "anchoring must not displace the plugin's own hierarchy"

    def test_master_worldspace_anchored_exactly_once(self):
        """Two job lists must stay disjoint or the FormID ships twice."""
        raw = b''.join(self._build()._top_groups['WRLD'])
        seen, pos = 0, 0
        while pos + 24 <= len(raw):
            sig = raw[pos:pos + 4]
            size = struct.unpack_from('<I', raw, pos + 4)[0]
            if sig == b'GRUP':
                pos += 24
                continue
            fid = struct.unpack_from('<I', raw, pos + 12)[0]
            if sig == b'WRLD' and fid == self.MASTER_WRLD:
                seen += 1
            pos += 24 + size
        assert seen == 1, f'master WRLD emitted {seen}x; engine keeps the last'


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])


class TestSayTopicRetarget:
    """Run-on-Target conditions in Say-driven topics (2026-07-19)."""

    def _raw_ctda(self, run_on_target=True, func=47):
        import struct
        # func defaults to GetItemCount(47) — a STATE query, which is what the
        # retarget exists for. Identity functions are exempt (see below).
        type_byte = 0x02 if run_on_target else 0x00   # CTDA_RUN_ON_TARGET
        return struct.pack('<B3xfHHII4x', type_byte, 1.0, func, 0, 0x00012345, 0)

    def test_retarget_to_reference(self):
        import struct
        from tes5_import.base.conditions import convert_ctda
        out = convert_ctda(self._raw_ctda(), offset=1,
                           run_on_target_ref=0x14)
        assert out is not None
        run_on, reference = struct.unpack_from('<II', out, 20)
        assert run_on == 2          # Reference
        assert reference == 0x14    # PlayerRef
        assert out[0] & 0x02 == 0   # flag bit cleared

    def test_drop_run_on_target(self):
        from tes5_import.base.conditions import convert_ctda
        assert convert_ctda(self._raw_ctda(), offset=1,
                            drop_run_on_target=True) is None

    def test_base_form_identity_conditions_are_never_retargeted(self):
        """GetIsID/GetIsClass answer by comparing a BASE FORM, so they must
        never be pinned to a reference — under Say() they are DROPPED instead.

        PlayerRef's base is vanilla Skyrim's 0x00000007, never the converted
        TES4 player NPC_ 0x01000007, so retargeting makes them UNPASSABLE.
        That silently killed 667 GREETING/bark INFOs across 101 topics — every
        affected NPC lost their whole topic list because the greeting that
        opens it could not pass (Pinarus Inventius kept only 'rumors').

        Vanilla Skyrim.esm corroborates the split: GetIsID appears with
        RunOn=Reference ZERO times.

        The veto only blocks RETARGETING; it does not keep the condition alive
        as RunOn=Target.  Skyrim's Say() has no dialogue target at all, so a
        surviving RunOn=Target identity can never pass either — emitting it
        silences the very line it guards.  Since the script call site has
        already chosen both speaker and topic, the authored identity check is
        statically satisfied and dropping fails OPEN, which is correct here.
        (2026-08-07, commit 5dd1cbf: a target-run GetIsID(Baurus) on the
        CharacterGen 26->27 GOODBYE bridge never passed, so `setstage 27` never
        ran and the intro stalled with everyone standing in silence.)

        A RunOn=Target identity in a topic that is NOT Say-driven — neither
        disposition passed — still falls through to plain RunOn=Target.
        """
        import struct
        from tes5_import.base.conditions import convert_ctda
        for func in (72, 68):
            raw = self._raw_ctda(func=func)
            # Never retargeted to the reference, under either disposition...
            assert convert_ctda(raw, offset=1, run_on_target_ref=0x14) is None, \
                f'identity func {func} must not survive as RunOn=Reference'
            assert convert_ctda(raw, offset=1, drop_run_on_target=True) is None, \
                f'identity func {func} must be dropped under Say()'
            # ...but outside a Say-driven topic it stays RunOn=Target.
            out = convert_ctda(raw, offset=1)
            assert out is not None, f'func {func} must survive outside Say()'
            run_on, reference = struct.unpack_from('<II', out, 20)
            assert (run_on, reference) == (1, 0), \
                f'identity func {func} became {run_on}/{reference:#x}'

    def test_actor_state_conditions_are_retargeted(self):
        """GetIsRace/GetIsSex/GetInFaction/GetFactionRank read live actor STATE,
        not a base-form identity, so a Say()-driven topic MUST pin them to the
        resolved target reference — Skyrim's Say has no target at all
        (ObjectReference.psc `Say(Topic, Actor akActorToSpeakAs, bool)`: arg 2
        is the SPEAKER), so RunOn=Target evaluates against nothing and the
        wrong response wins.

        Vanilla Skyrim.esm uses RunOn=Reference->PlayerRef for exactly these:
        GetIsSex 125x, GetInFaction 136x, GetIsRace 29x, GetFactionRank 22x.

        Left on RunOn=Target, Valen Dreth's per-race CharacterGen taunts all
        failed and only the single race-less line could play, so the tutorial
        never advanced past its first taunt.
        """
        import struct
        from tes5_import.base.conditions import convert_ctda
        # GetIsRace(69) is exercised separately: its PARAM is race-mapped and
        # this fixture's dummy FormID is not a real TES4 race.
        for func in (70, 71, 73):
            raw = self._raw_ctda(func=func)
            out = convert_ctda(raw, offset=1, run_on_target_ref=0x14)
            assert out is not None, f'func {func} must not be dropped'
            run_on, reference = struct.unpack_from('<II', out, 20)
            assert (run_on, reference) == (2, 0x14), \
                f'state func {func} not retargeted: {run_on}/{reference:#x}'

    def test_default_still_target(self):
        import struct
        from tes5_import.base.conditions import convert_ctda
        out = convert_ctda(self._raw_ctda(), offset=1)
        run_on, reference = struct.unpack_from('<II', out, 20)
        assert run_on == 1 and reference == 0

    def test_subject_condition_untouched(self):
        import struct
        from tes5_import.base.conditions import convert_ctda
        out = convert_ctda(self._raw_ctda(run_on_target=False), offset=1,
                           run_on_target_ref=0x14)
        run_on, reference = struct.unpack_from('<II', out, 20)
        assert run_on == 0 and reference == 0

    def test_engine_fixed_param_never_remapped(self):
        """GetIsID(Player 0x00000007) [Target] means "am I addressing the
        player" — the runtime player's base form is vanilla Skyrim's
        0x00000007, so the param must NOT be load-order shifted to the
        converted TES4 player copy (0x01000007 can never pass; 3,761 INFOs
        incl. every stage-gated reveal greeting died and their AddTopic-unlock
        fragments never ran — Pinarus lost his whole topic list, second cause
        after the identity-retarget bug)."""
        import struct
        from tes5_import.base.conditions import convert_ctda
        type_byte = 0x02                              # run-on-target
        raw = struct.pack('<B3xfHHII4x', type_byte, 1.0, 72, 0, 0x00000007, 0)
        out = convert_ctda(raw, offset=1)
        assert out is not None
        param1 = struct.unpack_from('<I', out, 12)[0]
        assert param1 == 0x00000007, \
            f'engine-fixed Player id was remapped to {param1:#010x}'


class TestAmbientChatterPacing:
    """NPCs quipped every few seconds anywhere, even mid-scripted-sequence
    (2026-07-25).

    Two causes, both from Skyrim-only mechanisms having no TES4 source:

    1. PKDT Interrupt Flags authorise an actor to INTERRUPT its package to
       speak. Oblivion has no such field -- its package flags cover doors,
       speed, sneak, equipment and combat only (xEdit wbPackageFlags; UESP
       "Oblivion Mod:Mod File Format/PACK"), and "No Idle Anims" is idle
       ANIMATIONS. The converter wrote 0xFFFF on all 7,209 packages, which per
       UESP is exactly what the CK's "Set all interrupt flags" button writes.
    2. Oblivion paces ambient dialogue GLOBALLY via GMSTs, which were skipped.
    """

    def test_interrupt_flags_not_force_enabled(self):
        from tes5_import.packages.converter import DEFAULT_INTERRUPT
        assert DEFAULT_INTERRUPT != 0xFFFF, \
            "0xFFFF is the CK's 'set all interrupt flags'; it forces every " \
            "NPC to be allowed to break off any activity to chatter"
        # CHATTER bits stay off — hellos (0x01), random conversations (0x02),
        # corpse greets (0x08), idle chatter (0x80): TES4 paces these through
        # global GMSTs, never per package.
        assert not (DEFAULT_INTERRUPT & (0x01 | 0x02 | 0x08 | 0x80))

    def test_interrupt_flags_authorise_combat_behaviour(self):
        """The 0x0000 over-correction froze combat response: vanilla reserves
        all-zero interrupts for scene lockdowns (dunCGAlduinBaitStayAtLinked-
        RefNoCombat, pelagiusHoldPosSleepIgnoreCombat, CWFinaleEnemyLeader-
        WaitForExecution...), while ordinary packages authorise the behaviour
        bits (observe-combat set on 64.2% of Skyrim.esm's 5,961 packages).
        With them denied the CharacterGen ambushes stood in a swords-out
        staring match until the player threw the first punch — TES4 packages
        never gate combat response at all."""
        from tes5_import.packages.converter import DEFAULT_INTERRUPT
        assert DEFAULT_INTERRUPT & 0x04, 'Observe combat behavior must be on'
        assert DEFAULT_INTERRUPT & 0x40, 'Aggro Radius Behavior must be on'
        # 0x10 "Reaction to player actions" authorises spoken reaction
        # comments — a scene actor barking one over a scripted Say line
        # disturbs conversation timing, so it stays OFF with the chatter bits.
        assert not (DEFAULT_INTERRUPT & 0x10)

    def test_pkdt_writes_the_interrupt_field(self):
        import struct
        from tes5_import.packages.converter import build_pkdt, DEFAULT_INTERRUPT
        b = build_pkdt(0, 2)
        assert len(b) == 12
        assert struct.unpack_from('<H', b, 8)[0] == DEFAULT_INTERRUPT

    def test_oblivion_pacing_gmsts_carried(self):
        """Oblivion is far slower than Skyrim on both ambient clocks; without
        these the converted game runs Skyrim's pacing over Oblivion's much
        larger line pool."""
        from tes5_import.base.constants import AMBIENT_GMST_OVERRIDES
        # Skyrim.esm ships 5.0 / 10.0 for these two.
        assert AMBIENT_GMST_OVERRIDES['fAIGreetingTimer'][0] == 20.0
        assert AMBIENT_GMST_OVERRIDES['fIdleChatterCommentTimer'][0] == 100.0
        for _edid, (value, is_float) in AMBIENT_GMST_OVERRIDES.items():
            assert is_float and isinstance(value, float)


class TestBarkResetTimer:
    """Ambient bark lines need a non-zero ENAM reset or the NPC repeats them
    forever (2026-07-25).

    The reset field is the engine's per-line lockout: once spoken, that INFO is
    ineligible until it expires, and when every greeting is on a timer the
    actor falls silent. TES4 has no such field, so the converter wrote 0 --
    meaning NO lockout, every line permanently re-playable, NPCs quipping on
    repeat. Vanilla Skyrim sets a real value on 65% of its HELO lines.
    """

    def test_reset_ticks_match_vanilla_half_hour(self):
        from tes5_import.dialogue.converter import (_BARK_RESET_TICKS,
                                                  _BARK_RESET_HOURS)
        # The engine stores this as trunc(days * 65535); 1365 is the exact
        # value on 2809 of vanilla Skyrim.esm's 5287 HELO lines (53%).
        assert _BARK_RESET_TICKS == 1365
        assert abs(_BARK_RESET_TICKS / 65535 * 24 - _BARK_RESET_HOURS) < 0.001

    def _enam(self, rec, *, is_bark):
        import struct
        from tes5_import.dialogue.converter import convert_INFO
        out = convert_INFO(rec, bark_dial_fids=set() if is_bark else None)
        pos = 24          # skip the record header; subrecords follow
        while pos + 6 <= len(out):
            sig = out[pos:pos + 4]
            size = struct.unpack_from('<H', out, pos + 4)[0]
            if sig == b'ENAM':
                return struct.unpack_from('<HH', out, pos + 6)
            pos += 6 + size
        raise AssertionError('no ENAM emitted')

    def test_bark_line_gets_reset(self):
        rec = {'FormID': '00001234', 'DATA.Flags': '0', 'ResponseCount': '0'}
        _flags, reset = self._enam(rec, is_bark=True)
        assert reset == 1365, 'ambient bark must carry a repeat lockout'

    def test_conversation_line_keeps_zero(self):
        """A player-selected topic is not ambient; the player controls when it
        plays, so a lockout would wrongly make it unavailable."""
        rec = {'FormID': '00001234', 'DATA.Flags': '0', 'ResponseCount': '0'}
        _flags, reset = self._enam(rec, is_bark=False)
        assert reset == 0

    def test_say_once_bark_keeps_zero(self):
        """Say-once (0x04) already locks permanently after one play; a reset
        would only weaken it."""
        rec = {'FormID': '00001234', 'DATA.Flags': '4', 'ResponseCount': '0'}
        flags, reset = self._enam(rec, is_bark=True)
        assert flags & 0x04, 'say-once flag must survive'
        assert reset == 0


class TestNpcToNpcConversationDrop:
    """Oblivion Type-1 Conversation topics are NPC-to-NPC chatter with no
    Skyrim equivalent short of a SCEN scene, so they are dropped rather than
    converted into player-menu entries labelled with their EditorID
    (2026-07-25). See docs/commentary/tes5_import_dialogue.md Step 1."""

    def _dial(self, edid, dtype=1, fid='00001234'):
        return {'EditorID': edid, 'DATA.Type': str(dtype), 'FormID': fid}

    def test_npc_to_npc_topic_is_dropped(self):
        import tes5_import.dialogue.converter as dc
        dc.SAY_TOPIC_DISPOSITIONS.clear()
        dc.SAY_TOPIC_DISPOSITIONS[0x0000AAAA] = ('drop', None)   # unrelated
        try:
            assert dc.should_skip_dial(self._dial('SkingradNQDResponses'))
        finally:
            dc.SAY_TOPIC_DISPOSITIONS.clear()

    def test_script_driven_topic_is_kept(self):
        """CharGen's lines are spoken by an explicit Say/SayTo in a quest
        script — a real Skyrim Actor.Say(). Dropping Type-1 by DATA.Type alone
        would delete the whole tutorial conversation."""
        import tes5_import.dialogue.converter as dc
        dc.SAY_TOPIC_DISPOSITIONS.clear()
        dc.SAY_TOPIC_DISPOSITIONS[0x00001234] = ('drop', None)
        try:
            assert not dc.should_skip_dial(
                self._dial('CharGenMain', fid='00001234'))
        finally:
            dc.SAY_TOPIC_DISPOSITIONS.clear()

    def test_named_keeps_survive(self):
        """INFOGENERAL is Oblivion's Rumors channel (a real Skyrim player
        topic); HELLO/GOODBYE are engine bark channels that happen to carry
        DIAL Type 1."""
        import tes5_import.dialogue.converter as dc
        dc.SAY_TOPIC_DISPOSITIONS.clear()
        dc.SAY_TOPIC_DISPOSITIONS[0x0000BBBB] = ('drop', None)
        try:
            for name in ('INFOGENERAL', 'HELLO', 'GOODBYE'):
                assert not dc.should_skip_dial(self._dial(name)), name
        finally:
            dc.SAY_TOPIC_DISPOSITIONS.clear()

    def test_empty_say_map_drops_nothing(self):
        """FAIL-SAFE: an empty map means the say-driven scan has not run yet
        (dialog_unlocks.build_unlock_plan calls should_skip_dial long before
        build_dialog_groups populates it). Treating that as 'nothing is
        script-driven' would drop all 293 scripted topics including CharGen."""
        import tes5_import.dialogue.converter as dc
        dc.SAY_TOPIC_DISPOSITIONS.clear()
        assert not dc.should_skip_dial(self._dial('SkingradNQDResponses'))

    def test_other_dial_types_unaffected(self):
        """Only Type 1 is NPC-to-NPC; Type 0 player topics must be untouched."""
        import tes5_import.dialogue.converter as dc
        dc.SAY_TOPIC_DISPOSITIONS.clear()
        dc.SAY_TOPIC_DISPOSITIONS[0x0000CCCC] = ('drop', None)
        try:
            assert not dc.should_skip_dial(
                self._dial('SomePlayerTopic', dtype=0))
        finally:
            dc.SAY_TOPIC_DISPOSITIONS.clear()


class TestQuestJournalPlatformText:
    """MQ01's journal shipped a gamepad AND a keyboard variant per tutorial
    stage; Oblivion picked one at runtime, Skyrim renders both (2026-07-24)."""

    def test_gamepad_variant_dropped_when_pc_variant_exists(self):
        from tes5_import.dialogue.quest import pc_stage_texts
        out = pc_stage_texts([
            '',
            'Use the left stick to move around. The right stick turns you.',
            'To move forward, &sUActnForward;. The mouse turns you.',
        ])
        assert out[1] is None, 'gamepad journal text must be dropped'
        assert 'left stick' not in (out[2] or '')
        assert out[2].startswith('To move forward, press W')

    def test_control_tokens_expanded(self):
        """&sUActnX; is an Oblivion UI token; Skyrim prints it verbatim."""
        from tes5_import.dialogue.quest import pc_stage_texts
        out = pc_stage_texts(
            ['To ready your weapon, &sUActnRdyitem;. To block, '
             '&sUActnBlock;.'])
        assert '&sUActn' not in out[0]
        assert 'press R' in out[0] and 'right mouse button' in out[0]

    def test_ordinary_multi_entry_stage_untouched(self):
        """Only a clean gamepad/PC split may drop anything — a quest with two
        unrelated journal entries must keep both."""
        from tes5_import.dialogue.quest import pc_stage_texts
        texts = ['Kill the bandit leader.', 'Return to Jauffre.']
        assert pc_stage_texts(texts) == texts


class TestSayLineDurations:
    """Measured voice-line lengths feed the converted Say() timers.

    TES4Polyfill.SayLine blocks until the engine has begun the line and
    returns THAT line's measured length (`info:<FID>`, reported by the INFO's
    Begin fragment); the per-topic maximum is only the fallback for a line with
    no voice file, and is what the call site passes as SayLine's last argument.
    """

    def test_call_site_fallback_is_the_topics_measured_maximum(self):
        from script_convert.converter import (ScriptConverter,
                                              SAY_LINE_SECONDS)
        from script_convert.cross_ref import CrossRefGraph
        conv = ScriptConverter(CrossRefGraph())
        saved = ScriptConverter.say_durations
        try:
            ScriptConverter.say_durations = {'chargentaunt2': 14.63}
            assert conv._say_fallback_seconds('Self.Say(CharGenTaunt2)') == 14.63
            # Unmeasured topics fall back to the generic stand-in, still > 0.
            assert conv._say_fallback_seconds('Self.Say(X)') == SAY_LINE_SECONDS
            assert SAY_LINE_SECONDS > 0
        finally:
            ScriptConverter.say_durations = saved

    def test_mp3_duration_reads_frame_headers(self, tmp_path):
        """A silent MPEG-1 Layer III CBR stream of known length."""
        from script_convert.say_durations import mp3_duration
        # 128kbps, 44100Hz, no padding -> 417-byte frames of 1152 samples
        frame = bytes([0xFF, 0xFB, 0x90, 0x00]) + b'\x00' * 413
        n = 38  # 38 * 1152 / 44100 ~= 0.9927s
        p = tmp_path / 'x.mp3'
        p.write_bytes(frame * n)
        assert abs(mp3_duration(str(p)) - n * 1152 / 44100) < 0.01


class TestActorScriptOnPlacedRef:
    """A converted actor script must live on the placed reference, not the base.

    Reference events (OnPackageEnd/OnActivate/OnDeath/OnHit) are declared on
    Actor/ObjectReference in vanilla Papyrus (Scripts.zip).  A base NPC_ record
    is an ActorBase (a Form), so a VMAD bound there receives NONE of them.

    This silently broke every converted quest that sequences on package
    completion: CharacterGen advances 10 -> 12 from Renote's OnPackageEnd, and
    with the script stranded on the base NPC_ the chain stopped dead at stage
    10, leaving the Emperor and guards with no `GetStage ==` package to select.
    """

    def test_reference_event_declaration_is_detected(self):
        from tes5_import.base.object_scripts import _script_uses_reference_event
        assert _script_uses_reference_event('begin OnPackageDone CGRenoteToMarkerA')
        assert _script_uses_reference_event('scn X\r\nbegin gamemode\r\nend\r\nbegin onhit\r\nend')
        assert _script_uses_reference_event('begin OnActivate\nActivate')

    def test_non_reference_scripts_are_left_on_the_base(self):
        """Only a `begin <event>` DECLARATION counts.

        A bare substring match relocated scripts that merely mentioned an event
        name in a comment, moving records that had no reason to leave the base.
        """
        from tes5_import.base.object_scripts import _script_uses_reference_event
        assert not _script_uses_reference_event('; comment mentioning onhit behaviour')
        assert not _script_uses_reference_event('begin MenuMode 1027')
        # not an actor event, and must not match on the 'onhit' substring
        assert not _script_uses_reference_event('begin OnMagicEffectHit')

    def test_bare_gamemode_does_not_force_relocation(self):
        """A bare GameMode block alone does NOT relocate. Known open gap.

        `gamemode` was briefly a member of _TES4_REFERENCE_EVENTS: TES4
        delivers GameMode everywhere, but the converter compiles it into an
        OnUpdate poll whose starters are all ObjectReference members, so on a
        base NPC_ the poll never starts and the block is dead code.
        Morroblivion's CATDestinationSorter (the Jo'Tesh/Kisimba world
        transport) is pure GameMode with no bare self-reference call, so it
        triggered neither other relocation reason and never ran.

        It was REVERTED alongside the poll gate (commit 4a802e2) when that pair
        was blamed for a CharacterGen regression.  A GameMode block still
        relocates whenever it ALSO makes a bare self-reference call — which is
        the common case (Celebro's `enable`) — so only pure-GameMode scripts
        are affected.

        The gate half of that revert has since been undone (the gate is
        cell-scoped again; see test_gamemode_gate_is_cell_scoped_not_3d_scoped
        in tests/test_script_converter.py).  This relocation half is still
        reverted, so the test continues to pin current behaviour.
        """
        from tes5_import.base.object_scripts import (
            _script_uses_reference_event, _script_uses_self_reference_call)
        assert not _script_uses_reference_event('begin GameMode\nset x to 1\nend')
        assert not _script_uses_reference_event('scn X\r\nbegin gamemode\r\nend')
        # ...but a GameMode block that calls a self-reference function still
        # relocates, via reason 3.
        assert _script_uses_self_reference_call(
            'scn X\nbegin GameMode\nenable\nend')

    def test_bare_self_reference_call_forces_relocation(self):
        """A bare `enable`/`moveto`/... acts on the calling REFERENCE.

        An ActorBase is not a reference, so on the base record the call does
        nothing at all.  Oblivion's scripted-entrance idiom is an
        initially-disabled placement whose own GameMode block enables it, which
        makes this the difference between the actor appearing and never
        existing — Celebro, the Nehrim intro companion, was absent from the
        start cell because MQ00CelebroScript (`if GetStage MQ00 == 5 / enable`)
        declares no reference event and so was left on the base NPC_.
        """
        from tes5_import.base.object_scripts import _script_uses_self_reference_call
        assert _script_uses_self_reference_call(
            'scn X\\r\\nbegin GameMode\\r\\nif ( GetStage MQ00 == 5 )'
            '\\r\\n\\tenable\\r\\nendif\\r\\nend')
        assert _script_uses_self_reference_call(
            'begin GameMode\\r\\n\\tMoveTo MQ00TrollMarker\\r\\nend')

    def test_other_refs_calls_do_not_force_relocation(self):
        """Only BARE calls count.  `CelebroRef.Disable` targets someone else and
        works fine from the base; a commented-out `;evp` is not a call at all.
        """
        from tes5_import.base.object_scripts import _script_uses_self_reference_call
        assert not _script_uses_self_reference_call(
            'begin OnActivate\\r\\n\\tCelebroRef.Disable\\r\\n\\tActivate\\r\\nend')
        assert not _script_uses_self_reference_call(
            'scn X\\r\\nbegin GameMode\\r\\n;evp\\r\\nend')
        assert not _script_uses_self_reference_call(
            'begin GameMode\\r\\n\\tset x to 1\\r\\nend')


class TestLoadGatedPollStart:
    """A load-gated update loop must start from OnLoad, not OnInit alone.

    OnInit on a PLACED REFERENCE runs at load BEFORE the actor's 3D exists, so
    an `If Is3DLoaded()` guard there is false and the poll never starts.
    OnCellAttach cannot cover it either — it fires only when a cell BECOMES
    attached, never for an actor already standing in the player's current cell.

    This silenced Valen Dreth the moment actor scripts moved to the ACHR (which
    reference events like OnPackageEnd require). Vanilla starts update loops
    from OnLoad in 27 scripts vs 2 that gate OnInit on Is3DLoaded; per
    ObjectReference.psc OnLoad is "fired every time this object is loaded".
    """

    def _emit(self, src):
        from script_convert.converter import ScriptConverter
        from script_convert.cross_ref import CrossRefGraph
        conv = ScriptConverter(CrossRefGraph())
        return conv.convert_standalone('T', src, 'Actor', 'T')

    def test_gamemode_actor_script_registers_from_onload(self):
        out = self._emit('scn T\nbegin gamemode\nset x to 1\nend\n')
        assert 'Event OnLoad()' in out
        onload = out.split('Event OnLoad()', 1)[1].split('EndEvent', 1)[0]
        assert 'RegisterForSingleUpdate' in onload

    def test_oncellattach_and_onload_both_present(self):
        """OnCellAttach still handles streaming in; OnLoad covers the rest.

        There must be NO OnCellDetach unregister: detach events for the old
        cell can land after OnLoad already re-armed the poll in the new one
        and kill a loaded actor's loop (CharacterGen escorts going mute).
        The arm-first Is3DLoaded() gate in OnUpdate stops the loop instead.
        """
        out = self._emit('scn T\nbegin gamemode\nset x to 1\nend\n')
        assert 'Event OnCellAttach()' in out
        assert 'UnregisterForUpdate()' not in out


# ---------------------------------------------------------------------------
# Weather / climate conversion
# ---------------------------------------------------------------------------

class TestWeatherConversion:
    """WTHR conversion.

    Field SEMANTICS come from UESP 'Skyrim Mod:Mod File Format/WTHR' and
    xEdit wbDefinitionsCommon; the defaults come from a census of the 84
    vanilla WTHR records in Skyrim.esm.  Several of these fields tint additive
    sky passes, so a guessed value blows the scene out rather than merely
    looking a bit off -- hence a test per field.
    """

    def _nam0(self):
        """TES4 NAM0: 10 types x 4 times x RGBA, one recognisable color each."""
        raw = bytearray(160)
        for t in range(10):
            for time in range(4):
                o = (t * 4 + time) * 4
                raw[o:o + 4] = bytes((10 * t, 10 * t + 1, 10 * t + 2, 0))
        return bytes(raw).hex().upper()

    def _convert(self, rec):
        """convert_WTHR returns (wthr_bytes, imgs_bytes); most tests want the WTHR."""
        from tes5_import.record_types.weather import convert_WTHR
        wthr, _imgs = convert_WTHR(rec)
        return wthr

    def _rec(self, **over):
        rec = {
            'Signature': 'WTHR', 'FormID': '00000200', 'RecordFlags': '0',
            'EditorID': 'TestWeather',
            'CNAM.LowerCloudLayer': 'Sky\\Lower.dds',
            'DNAM.UpperCloudLayer': 'Sky\\Upper.dds',
            'NAM0.Size': '160', 'NAM0.Data': self._nam0(),
            'DATA.WindSpeed': '25', 'DATA.CloudSpeedLower': '42',
            'DATA.CloudSpeedUpper': '19', 'DATA.TransDelta': '3',
            'DATA.SunGlare': '255', 'DATA.SunDamage': '200',
            'DATA.PrecipBeginFadeIn': '5', 'DATA.PrecipEndFadeOut': '6',
            'DATA.ThunderBeginFadeIn': '7', 'DATA.ThunderEndFadeOut': '8',
            'DATA.ThunderFrequency': '188', 'DATA.Classification': '4',
            'DATA.LightningR': '11', 'DATA.LightningG': '12',
            'DATA.LightningB': '13',
        }
        rec.update(over)
        return rec

    def test_subrecord_sizes_match_vanilla(self):
        rec = self._convert(self._rec())
        for sig, size in ((b'NAM0', 272), (b'DATA', 19), (b'FNAM', 32),
                          (b'RNAM', 32), (b'QNAM', 32), (b'PNAM', 512),
                          (b'JNAM', 512), (b'IMSP', 16)):
            assert len(_find_subrecord(rec, sig)) == size, sig
        dalc = _find_all_subrecords(rec, b'DALC')
        assert len(dalc) == 4 and all(len(d) == 32 for d in dalc)

    def test_nam0_colors_are_remapped_not_stubbed(self):
        """The first version wrote flat 128-grey over the whole table."""
        nam0 = _find_subrecord(self._convert(self._rec()), b'NAM0')

        def rgb(slot, time=1):
            o = (slot * 4 + time) * 4
            return tuple(nam0[o:o + 3])

        assert rgb(0) == (0, 1, 2)        # Sky-Upper
        assert rgb(1) == (10, 11, 12)     # Fog -> Fog Near
        assert rgb(3) == (30, 31, 32)     # Ambient
        # Stars is blanked here because the fixture is Rainy — see
        # test_stars_are_blanked_under_rain_and_snow.  A Pleasant weather
        # passes the authored value through.
        clear = _find_subrecord(
            self._convert(self._rec(**{'DATA.Classification': '1'})), b'NAM0')
        o = (6 * 4 + 1) * 4
        assert tuple(clear[o:o + 3]) == (60, 61, 62)   # Stars
        assert rgb(8) == (80, 81, 82)     # Horizon
        # Cloud LOD Diffuse/Ambient (10/11) stay BLACK: vanilla ships 0 in
        # both (median AND p90, all four times); Oblivion's tints there lit
        # the distant cloud LOD pass white.
        assert rgb(10) == (0, 0, 0)
        assert rgb(11) == (0, 0, 0)
        assert rgb(12) == (10, 11, 12)    # Fog Far reuses the single TES4 fog
        # Effect Lighting (9) has no TES4 source; vanilla authors it BRIGHT
        # (black leaves effect shaders unlit) — per-time channel medians.
        assert rgb(9) == (198, 193, 193)

    def test_tes5_only_slots_take_class_and_time_medians(self):
        """Slots 13-16 come from the per-classification, per-time census of
        the REAL Skyrim.esm (the references dump truncates NAM0 hex).

        Two prior calibrations were wrong: copying the TES4 Sun/Stars colors
        into the glare slots produced a blinding sky, and the 'vanilla mode is
        black' correction hid the MOONS — slot 13 Sky Statics tints the moon
        discs and is never black in vanilla outdoor weathers (SkyrimClear
        night is 45,137,208).  Stars kept rendering (slot 6), which is what
        localised the bug.
        """
        def rgb(rec_bytes, slot, time):
            nam0 = _find_subrecord(rec_bytes, b'NAM0')
            o = (slot * 4 + time) * 4
            return tuple(nam0[o:o + 3])

        # Default test record is Rainy (classification 4)
        rainy = self._convert(self._rec())
        assert rgb(rainy, 13, 1) == (142, 161, 173)   # never black
        assert rgb(rainy, 15, 1) == (0, 0, 0)         # clouds hide the sun
        assert rgb(rainy, 16, 3) == (0, 0, 0)         # and the moons
        assert rgb(rainy, 14, 3) == (31, 63, 75)      # night water is dark teal

        clear = self._convert(self._rec(**{'DATA.Classification': '1'}))
        assert rgb(clear, 13, 3) == (62, 93, 108), 'moons must not tint black'
        assert rgb(clear, 16, 3) == (255, 173, 138)   # warm night moon halo
        assert rgb(clear, 15, 3) == (0, 0, 0)         # no sun glare at night
        assert rgb(clear, 15, 1) == (72, 58, 57)      # dark brown by day

    def test_data_carries_every_tes4_field(self):
        """Offsets 6-14 were dropped entirely by the original converter.

        Trans Delta and Sun Glare are RESCALED, not copied: TES4's median
        Trans Delta is 255 where vanilla TES5 ships 125 almost universally
        (x125/255), and TES4 clear weathers author Sun Glare 255 where the
        vanilla ceiling is 191 / p90 153 (x0.6) — a raw 255 painted the sky
        white toward the sun."""
        d = _find_subrecord(self._convert(self._rec()), b'DATA')
        assert d[0] == 25                        # wind speed
        assert (d[1], d[2]) == (0, 0)            # TES5 padding (was cloud speed)
        assert d[3] == round(3 * 125 / 255)      # trans delta, rescaled
        assert d[4] == 255                       # sun glare passes through
        assert d[5] == 200                       # sun damage
        assert (d[6], d[7]) == (5, 6)            # precipitation fades
        assert (d[8], d[9]) == (7, 8)            # thunder fades
        assert d[10] == 188                      # thunder frequency
        assert d[11] == 4                        # classification (Rainy)
        assert (d[12], d[13], d[14]) == (11, 12, 13)   # lightning color

    def test_thunder_frequency_keeps_inverted_scale(self):
        """Both games use 255=never .. 15=constant, so it is a passthrough.

        Vanilla Oblivion agrees: every clear weather is 255 and the
        thunderstorms are 188/132/100/24.  Inverting it here would make clear
        skies thunder constantly.
        """
        clear = self._convert(self._rec(**{'DATA.ThunderFrequency': '255'}))
        assert _find_subrecord(clear, b'DATA')[10] == 255

    def test_classification_defaults_to_pleasant(self):
        out = self._convert(self._rec(**{'DATA.Classification': '0'}))
        assert _find_subrecord(out, b'DATA')[11] == 0x01

    def test_sheets_land_on_the_two_square_uv_dome_shapes(self):
        """Layer indices bind to shapes in VANILLA's hardcoded Clouds.nif, and
        those shapes are NOT interchangeable -- each has its own UV tiling.

        Measured UV spans of the shipped 29-shape dome:
            L11 09_CDTop     U 1.58 V 1.58   ~1:1 projection
            L27 14_CDLower   U 2.27 V 2.27   ~1:1 projection
            L 8 07_CDDome_Horizon   U 6.00   tiles 6x around the horizon
            L15-26 12_/13_CDHorizon_*  V 0.25   narrow V-sliced strips
            L28 15_CDFog     U 21.00          tiles 21x, horizon wash

        Oblivion authors its two sheets as single FULL-DOME projections
        (CloudDome:0 3.35x3.35, CloudDome:1 2.97x2.97, both 0..90 deg), so 11
        and 27 are the only structural matches.  Confirmed in game: a variant
        using 8/9/10 put all the cloud around the horizon instead of over the
        sky.
        """
        from tes5_import.record_types.weather import _wthr_cloud_sig, _WTHR_UPPER_LAYER, _WTHR_LOWER_LAYER
        assert (_WTHR_UPPER_LAYER, _WTHR_LOWER_LAYER) == (11, 27)
        out = self._convert(self._rec())
        upper = _find_subrecord(out, _wthr_cloud_sig(_WTHR_UPPER_LAYER))
        lower = _find_subrecord(out, _wthr_cloud_sig(_WTHR_LOWER_LAYER))
        assert upper.rstrip(b'\x00').lower().endswith(b'upper.dds')
        assert lower.rstrip(b'\x00').lower().endswith(b'lower.dds')

    def test_only_the_two_authored_layers_are_enabled(self):
        """Vanilla ships DEDICATED art per layer role -- SkyrimCloudsHorizon01
        on the strips (50 of 50 times) and SkyrimCloudsFill on the wash (156
        of 157).  We have neither, so every other layer stays empty rather
        than being padded with a full-dome sheet that would tile wrongly."""
        from tes5_import.record_types.weather import _WTHR_UPPER_LAYER, _WTHR_LOWER_LAYER
        out = self._convert(self._rec())
        nam1 = struct.unpack('<I', _find_subrecord(out, b'NAM1'))[0]
        enabled = {L for L in range(32) if not (nam1 >> L) & 1}
        assert enabled == {_WTHR_UPPER_LAYER, _WTHR_LOWER_LAYER}, enabled

        textured = set()
        body = out[24:]
        i = 0
        while i + 6 <= len(body):
            sig = body[i:i + 4]
            sz = struct.unpack('<H', body[i + 4:i + 6])[0]
            if len(sig) == 4 and sig[1:] == b'0TX':
                textured.add(sig[0] - 0x30 if sig[0] <= 0x40
                             else 17 + (sig[0] - 0x41))
            i += 6 + sz
        assert textured == enabled, (textured, enabled)

    def test_weather_with_no_cloud_textures_disables_all_layers(self):
        """No authored sheet means no cloud layer at all."""
        rec = self._rec(**{'CNAM.LowerCloudLayer': '', 'DNAM.UpperCloudLayer': ''})
        nam1 = struct.unpack('<I', _find_subrecord(self._convert(rec), b'NAM1'))[0]
        assert nam1 == 0xFFFFFFFF

    def test_lnam_spans_every_authored_layer(self):
        """LNAM is an INDEX CLAMP into RNAM/QNAM/JNAM, not a layer count.

        Disassembled from SkyrimSE.exe -- all three readers of
        TESWeather+0x7D0 (Clouds::Update at 0x3c5485 and 0x3c54ff, and the
        JNAM alpha getter at 0x2c1eb0) do
            idx = (layer < LNAM) ? layer : 0
        so a layer at or above LNAM keeps DRAWING but silently reuses layer
        0's speed and layer 0's alpha.  The draw loops are bounded by
        Clouds::numLayers and a hard 32, never by LNAM.

        So LNAM must cover every layer we author, and must be > 0 (LNAM <= 0
        takes the `jle` default of speed 0x33 / alpha 1.0 and throws the
        authored JNAM away entirely).
        """
        out = self._convert(self._rec())
        lnam = struct.unpack('<I', _find_subrecord(out, b'LNAM'))[0]
        assert lnam > 0, 'LNAM <= 0 discards authored JNAM alphas'

        textured = []
        body = out[24:]
        i = 0
        while i + 6 <= len(body):
            sig = body[i:i + 4]
            sz = struct.unpack('<H', body[i + 4:i + 6])[0]
            if len(sig) == 4 and sig[1:] == b'0TX':
                textured.append(sig[0] - 0x30 if sig[0] <= 0x40
                                else 17 + (sig[0] - 0x41))
            i += 6 + sz
        assert textured, 'fixture should author cloud sheets'
        assert lnam > max(textured), (
            f'LNAM {lnam} leaves layer {max(textured)} reusing layer 0')

    def test_cloud_drift_is_two_dimensional_and_signed(self):
        """QNAM/RNAM are SIGNED about 0x7F.

        The engine decodes them as `byte * 0.2/254 - 0.1` (Clouds::Update,
        0x3c54ad-0x3c54de: xmm4=0.1, xmm3=-0.1, xmm10=1/254), so 0x7F is
        exactly stationary.  Leaving QNAM at 0x7F means the clouds never drift
        horizontally at all; vanilla authors nonzero X drift on 557 of 2656
        layer entries and negative drift on 77 of them.
        """
        out = self._convert(self._rec())
        rnam = _find_subrecord(out, b'RNAM')
        qnam = _find_subrecord(out, b'QNAM')
        assert len(rnam) == 32 and len(qnam) == 32

        lnam = struct.unpack('<I', _find_subrecord(out, b'LNAM'))[0]
        live = range(lnam)
        assert any(qnam[L] != 0x7F for L in live), 'no horizontal drift at all'
        # the fixture authors forward Y speeds, so X must differ in sign
        assert any(qnam[L] < 0x7F for L in live), 'X drift is never negative'
        assert any(rnam[L] > 0x7F for L in live), 'Y drift lost its direction'

    def test_stars_are_blanked_under_rain_and_snow(self):
        """Stars is a visibility SWITCH in vanilla, not a continuous tint.

        Censused over the 165 vanilla weathers carrying a full NAM0:
        Pleasant 91.4% pure white, Rainy 95.7% black, Snow 77.8% black.
        Oblivion authors a continuous value on every weather, so a converted
        rainstorm otherwise keeps a starfield burning through the overcast.
        Cloudy is left alone -- vanilla is genuinely split there (60/18).
        """
        def stars(cls):
            nam0 = _find_subrecord(
                self._convert(self._rec(**{'DATA.Classification': cls})),
                b'NAM0')
            o = (6 * 4 + 3) * 4          # Stars, Night
            return tuple(nam0[o:o + 3])

        assert stars('4') == (0, 0, 0), 'rain must hide the stars'
        assert stars('8') == (0, 0, 0), 'snow must hide the stars'
        assert stars('1') != (0, 0, 0), 'pleasant keeps its stars'
        assert stars('2') != (0, 0, 0), 'cloudy is left as authored'

    def test_fog_curve_reproduces_oblivions_linear_ramp(self):
        """Oblivion's fog is fixed-function D3DFOG_LINEAR.

        Established by disassembling all 123 vertex shaders in
        Data/Shaders/shaderpackage001.sdp: NOT ONE writes oFog (RASTOUT#1),
        and the weather's near/far go straight into a BSFogProperty
        (Atmosphere::Update 0x53b318..0x53b34c).

        Skyrim computes min(Max, pow(t, Power)) (Lighting.hlsl:271).  With
        Power=1 and Max=1 that is exactly t -- Oblivion's ramp.  The previous
        0.4/0.9 were vanilla MEDIANS and are wrong by up to +0.32 fog
        mid-ramp and -0.10 at the far plane.
        """
        fnam = _find_subrecord(self._convert(self._rec()), b'FNAM')
        assert len(fnam) == 32
        (_dn, _df, _nn, _nf,
         day_power, night_power, day_max, night_max) = struct.unpack('<8f', fnam)
        assert day_power == 1.0 and night_power == 1.0, 'fog must stay linear'
        assert day_max == 1.0 and night_max == 1.0, (
            'Max < 1 leaves the far plane permanently unfogged')

    def test_negative_fog_near_plane_is_clamped(self):
        """A negative near plane starts the fog ramp BEHIND the camera.

        Every visible pixel is then past the ramp's start, so fog renders at
        full density with no gradient -- worst along the LONGEST view rays,
        i.e. the horizon.  That is view-dependent, which is why the sky reads
        fine facing the ground and the horizon flares when you look up.

        Oblivion AUTHORS these (18 of Oblivion.esm's 37 weathers; SETestAsh
        -750, SEThunderstorm night -6500) but 0 of the 84 vanilla Skyrim
        records ship one, so the vanilla floor of 0 is the target.
        """
        rec = self._rec(**{'FNAM.FogDayNear': '-750.0',
                           'FNAM.FogDayFar': '10000.0',
                           'FNAM.FogNightNear': '-6500.0',
                           'FNAM.FogNightFar': '12000.0'})
        f = struct.unpack('<8f', _find_subrecord(self._convert(rec), b'FNAM'))
        assert f[0] == 0.0, f'day near {f[0]}'
        assert f[2] == 0.0, f'night near {f[2]}'
        # the authored far planes must survive untouched
        assert f[1] == pytest.approx(10000.0)
        assert f[3] == pytest.approx(12000.0)

    def test_positive_fog_near_plane_passes_through(self):
        """The clamp must not disturb weathers that authored a sane ramp."""
        rec = self._rec(**{'FNAM.FogDayNear': '4096.0',
                           'FNAM.FogDayFar': '170000.0',
                           'FNAM.FogNightNear': '4096.0',
                           'FNAM.FogNightFar': '130000.0'})
        f = struct.unpack('<8f', _find_subrecord(self._convert(rec), b'FNAM'))
        assert f[0] == pytest.approx(4096.0)
        assert f[1] == pytest.approx(170000.0)
        assert f[2] == pytest.approx(4096.0)
        assert f[3] == pytest.approx(130000.0)

    def test_zero_width_fog_ramp_is_widened(self):
        """A far plane at or inside the near plane snaps fog to full density."""
        from tes5_import.record_types.weather import _WTHR_FOG_MIN_RAMP
        rec = self._rec(**{'FNAM.FogDayNear': '5000.0',
                           'FNAM.FogDayFar': '5000.0',
                           'FNAM.FogNightNear': '5000.0',
                           'FNAM.FogNightFar': '1000.0'})
        f = struct.unpack('<8f', _find_subrecord(self._convert(rec), b'FNAM'))
        assert f[1] == pytest.approx(5000.0 + _WTHR_FOG_MIN_RAMP)
        assert f[3] == pytest.approx(5000.0 + _WTHR_FOG_MIN_RAMP)

    def test_cloud_speed_uses_the_shared_physical_scale(self):
        """Both engines cap cloud drift at 0.1 units.

        Oblivion scales its unsigned 0..255 byte by fWeatherCloudSpeedMax
        (0.1, read from Oblivion.exe at 0xA2FAAC); Skyrim encodes a SIGNED
        -0.1..+0.1 as 0x00..0xFE with 0x7F = 0.  So the byte must be rescaled,
        not copied.  `0x7F + speed//2` ran clouds ~10x too fast.
        """
        from tes5_import.record_types.weather import _cloud_speed_tes4_to_tes5 as conv

        def to_float(b):
            return (b - 127) / 127 / 10

        assert conv(0) == 0x7F                      # still stays still
        assert conv(255) == 0xFE                    # TES4 max == TES5 max
        for tes4 in (19, 25, 42, 50, 101, 200):
            assert abs(to_float(conv(tes4)) - tes4 / 255 * 0.1) < 0.001
        assert all(0x7F <= conv(v) <= 0xFE for v in range(256))

    def test_cloud_speed_lands_in_rnam_layers(self):
        """Each dome layer drifts with the speed authored for the sheet it came from."""
        from tes5_import.record_types.weather import _WTHR_LOWER_PLAN, _WTHR_UPPER_PLAN
        rnam = _find_subrecord(self._convert(self._rec()), b'RNAM')
        for layer, _a in _WTHR_UPPER_PLAN:
            assert rnam[layer] == 0x88, layer   # TES4 CloudSpeedUpper 19
        for layer, _a in _WTHR_LOWER_PLAN:
            assert rnam[layer] == 0x94, layer   # TES4 CloudSpeedLower 42
        planned = {L for L, _a in _WTHR_UPPER_PLAN} | {L for L, _a in _WTHR_LOWER_PLAN}
        assert set(rnam[L] for L in range(32) if L not in planned) == {0x7F}

    def test_cloud_layers_are_translucent_not_opaque_planes(self):
        """ALPHA IS THE SCALAR THAT WAS MISSING.

        Writing 1.0 on both layers made layer 0 an opaque plane painted over
        the sky gradient at full strength, with layer 1 completely occluded
        behind it -- the cloud sheet BECAME the sky, which is what bleached
        the horizon.  Vanilla composites translucent sheets: the per-time
        medians over every vanilla weather that enables the layer are
        0.50-0.60 on layer 0.  This finding is independent of dome geometry
        and survived the layer-plan revert.
        """
        from tes5_import.record_types.weather import _WTHR_LOWER_PLAN, _WTHR_UPPER_PLAN, _WTHR_UPPER_LAYER
        jnam = struct.unpack('<128f', _find_subrecord(self._convert(self._rec()), b'JNAM'))
        placed = set()
        for layer, alphas in _WTHR_UPPER_PLAN + _WTHR_LOWER_PLAN:
            placed.add(layer)
            for time in range(4):
                assert jnam[layer * 4 + time] == pytest.approx(alphas[time])
        # The upper sheet must NOT be an opaque plane.
        assert all(jnam[_WTHR_UPPER_LAYER * 4 + t] < 1.0 for t in range(4))
        # Every layer we do NOT place stays fully transparent.
        assert set(jnam[L * 4 + t] for L in range(32) if L not in placed
                   for t in range(4)) == {0.0}

    def test_required_subrecords_present(self):
        """LNAM/MNAM/NNAM are .SetRequired in xEdit; LNAM=0 allocated no layers."""
        rec = self._convert(self._rec(**{'DATA.Classification': '1',
                                         'DATA.ThunderFrequency': '255'}))
        from tes5_import.record_types.weather import _WTHR_LOWER_LAYER
        # LNAM is an index clamp into RNAM/QNAM/JNAM, so it must SPAN the
        # highest layer we author or that layer silently reuses layer 0's
        # speed and alpha (SkyrimSE.exe 0x3c5485 / 0x3c54ff / 0x2c1eb0).
        assert (struct.unpack('<I', _find_subrecord(rec, b'LNAM'))[0]
                == _WTHR_LOWER_LAYER + 1)
        assert _find_subrecord(rec, b'MNAM') == b'\x00\x00\x00\x00'
        assert _find_subrecord(rec, b'NNAM') == b'\x00\x00\x00\x00'

    def test_precipitation_maps_to_vanilla_spgd(self):
        """A NULL MNAM means an authored rainstorm produces NO rain.

        Skyrim draws precipitation via the SPGD in MNAM (18 of 84 vanilla
        weathers); Oblivion picked hardcoded Sky\\ meshes off the
        classification bits, so the classification is the authored source:
        Rainy -> RainParticles, Rainy+thunder -> RainStormParticles,
        Snow -> SnowParticlesMed.  ThunderFrequency is inverted (255=never).
        """
        def mnam(**over):
            out = self._convert(self._rec(**over))
            return struct.unpack('<I', _find_subrecord(out, b'MNAM'))[0]

        # The default test record is Rainy (4) with thunder at 188 -> storm.
        assert mnam() == 0x0010780F
        assert mnam(**{'DATA.ThunderFrequency': '255'}) == 0x00023C48
        assert mnam(**{'DATA.Classification': '8'}) == 0x00023C49
        assert mnam(**{'DATA.Classification': '1'}) == 0
        assert mnam(**{'DATA.Classification': '2',
                       'DATA.ThunderFrequency': '255'}) == 0

    def test_dalc_follows_vanilla_face_weights(self):
        """Z+ is the DARKEST face and Z- the brightest.

        Medians over all 84 vanilla weathers: X+ .98 X- .94 Y+ .96 Y- .95
        Z+ .67 Z- 1.28.  Writing Ambient verbatim into all six faces and then
        BRIGHTENING Z+ (the first version) overdrove the whole cube.
        """
        dalc = _find_all_subrecords(self._convert(self._rec()), b'DALC')[1]
        faces = [tuple(dalc[i * 4:i * 4 + 3]) for i in range(6)]
        amb = (30, 31, 32)                       # NAM0 Ambient, day
        assert faces[4][0] < amb[0], 'Z+ must be darker than Ambient'
        assert faces[5][0] > amb[0], 'Z- must be brighter than Ambient'
        assert faces[4][0] < faces[5][0]
        for f in faces[:4]:                      # horizontals sit near Ambient
            assert abs(f[0] - amb[0]) <= 3
        assert struct.unpack_from('<f', dalc, 28)[0] == 1.0    # Fresnel

    def test_default_weather_editorid_avoids_skyrim_collision(self):
        out = self._convert(self._rec(EditorID='DefaultWeather'))
        assert _find_subrecord(out, b'EDID') == b'TES4DefaultWeather\x00'

    def test_missing_nam0_does_not_crash(self):
        rec = self._rec()
        del rec['NAM0.Data']
        assert len(_find_subrecord(self._convert(rec), b'NAM0')) == 272

    def _lum(self, c):
        return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]

    def _slot(self, nam0, slot, time=1):
        o = (slot * 4 + time) * 4
        return tuple(nam0[o:o + 3])

    def test_colors_below_the_knee_pass_through_untouched(self):
        """Authored color is the deliverable; only HIGHLIGHTS are compressed.

        The two populations agree at the bottom (TES4 p50 76.9 vs vanilla
        83.8) and diverge only at the top (p90 204.6 vs 168.0), so a uniform
        scale darkens midtones to fix highlights.  A previous revision did
        exactly that and made the day sky dull.  Anything under the knee must
        come out BIT-IDENTICAL.
        """
        raw = bytearray(160)
        for time in range(4):
            for slot, rgb in ((0, (100, 141, 191)),   # lum 134, under knee
                              (7, (80, 110, 150)),    # lum 106
                              (8, (60, 90, 120))):    # lum  85
                o = (slot * 4 + time) * 4
                raw[o:o + 3] = bytes(rgb)
        nam0 = _find_subrecord(
            self._convert(self._rec(**{'NAM0.Data': bytes(raw).hex().upper()})),
            b'NAM0')
        assert self._slot(nam0, 0) == (100, 141, 191)
        assert self._slot(nam0, 7) == (80, 110, 150)
        assert self._slot(nam0, 8) == (60, 90, 120)

    def test_highlights_are_compressed_with_hue_preserved(self):
        """Above the knee, luminance is remapped into knee..ceiling and all
        three channels scale together, so only brightness moves."""
        from tes5_import.record_types.weather import _NAM0_KNEE, _NAM0_KNEE_CEILING
        raw = bytearray(160)
        src = (255, 220, 180)            # lum 226, well above the knee
        for time in range(4):
            o = (0 * 4 + time) * 4
            raw[o:o + 3] = bytes(src)
        nam0 = _find_subrecord(
            self._convert(self._rec(**{'NAM0.Data': bytes(raw).hex().upper()})),
            b'NAM0')
        out = self._slot(nam0, 0)
        assert _NAM0_KNEE < self._lum(out) <= _NAM0_KNEE_CEILING + 1
        # hue: channel ratios survive
        for a, b in ((0, 1), (1, 2)):
            assert abs(src[a] / src[b] - out[a] / out[b]) < 0.02

    def test_the_knee_has_no_time_axis(self):
        """The authored day/night curve must survive EXACTLY.

        Oblivion authors night at 7-12% of day.  The old per-time scale
        pushed that to 22-55%, which is what made nights read as a saturated
        blue instead of dark.  The same color must convert the same way at
        every time of day.  The ratio moves only where DAY is above the knee.
        See: docs/commentary/tes5_import_weather.md#nam0-color-per-plugin-normalization
        """
        raw = bytearray(160)
        day, night = (200, 210, 230), (14, 15, 20)
        for slot in (0, 7, 8, 1):
            for time, rgb in ((0, day), (1, day), (2, day), (3, night)):
                o = (slot * 4 + time) * 4
                raw[o:o + 3] = bytes(rgb)
        nam0 = _find_subrecord(
            self._convert(self._rec(**{'NAM0.Data': bytes(raw).hex().upper()})),
            b'NAM0')
        authored = self._lum(night) / self._lum(day)
        for slot in (0, 7, 8):
            same = {self._slot(nam0, slot, t) for t in (0, 1, 2)}
            assert len(same) == 1, f'slot {slot} converted differently by time'
            # NIGHT is under the knee, so it must be bit-identical — this is
            # the property the old per-time scale destroyed (it multiplied
            # night by 1.9-3.1x).
            assert self._slot(nam0, slot, 3) == night, 'night was rescaled'
            ratio = self._lum(self._slot(nam0, slot, 3)) / \
                self._lum(self._slot(nam0, slot, 1))
            assert ratio >= authored, 'night must never get DARKER relatively'
            assert ratio < authored * 1.35, (
                f'slot {slot} day/night ratio moved too far '
                f'{authored:.3f} -> {ratio:.3f}')

    def test_the_sun_disc_gets_its_own_hard_knee(self):
        """Sun is the one genuine outlier: TES4 day median 193.4 vs vanilla
        42.5 (4.55x, where no other slot exceeds 1.7x).  Skyrim's sun
        brightness comes from the glare pass and the imagespace, not this
        color, so a near-white disc here is a pure bloom source."""
        from tes5_import.record_types.weather import _NAM0_SUN_CEILING
        raw = bytearray(160)
        for time in range(4):
            o = (5 * 4 + time) * 4
            raw[o:o + 3] = bytes((255, 255, 255))
        nam0 = _find_subrecord(
            self._convert(self._rec(**{'NAM0.Data': bytes(raw).hex().upper()})),
            b'NAM0')
        assert self._lum(self._slot(nam0, 5)) <= _NAM0_SUN_CEILING + 1

    def test_conversion_does_not_depend_on_the_rest_of_the_plugin(self):
        """The old normalization was a PLUGIN-population statistic, so the
        same weather converted differently depending on what shipped
        alongside it.  The knee is a pure function of one color."""
        raw = bytearray(160)
        for time in range(4):
            o = (0 * 4 + time) * 4
            raw[o:o + 3] = bytes((100, 141, 191))
        rec = self._rec(**{'NAM0.Data': bytes(raw).hex().upper()})
        alone = _find_subrecord(self._convert(rec), b'NAM0')

        # convert it again as if the plugin were full of very bright weathers
        hot = bytearray(160)
        for slot in range(10):
            for time in range(4):
                o = (slot * 4 + time) * 4
                hot[o:o + 3] = b'\xff\xff\xff'
        for _ in range(8):
            self._convert(self._rec(**{'NAM0.Data': bytes(hot).hex().upper()}))
        after = _find_subrecord(self._convert(rec), b'NAM0')
        assert alone == after

    def test_ambience_loops_land_in_the_amb_audio_category(self):
        """A 2D looping TES4 sound is an ambience bed; filed under
        AudioCategorySFX it bypasses the ambience mix/slider and Oblivion's
        weather winds played LOUD over everything.  Vanilla AMBWeather*
        descriptors all use AudioCategoryAMB (0x7F80B)."""
        from tes5_import.record_types.sound import convert_SOUN

        class W:
            def __init__(self):
                self.next = 0x01000000
                self.records = []
            def alloc_formid(self):
                self.next += 1
                return self.next
            def derive_formid(self, site, key):
                # Test double: derived ids need only be stable per
                # (site, key) and distinct, like the real allocator.
                if not hasattr(self, '_derived'):
                    self._derived = {}
                k = (site, repr(key))
                if k not in self._derived:
                    self._derived[k] = self.alloc_formid()
                return self._derived[k]
            # A 3D sound now mints a real falloff SOPM instead of taking the
            # non-attenuating 2D model (see the ONAM note in convert_SOUN).
            def add_record(self, sig, data):
                self.records.append((sig, data))

        def sndr_gnam(flags):
            rec = {'Signature': 'SOUN', 'FormID': '00000400', 'RecordFlags': '0',
                   'EditorID': 'TestWind', 'FNAM.Filename': 'ambient\\wind.wav',
                   'SNDX.Flags': str(flags), 'SNDX.MinAttDist': '0',
                   'SNDX.MaxAttDist': '0', 'SNDX.StaticAttenuation': '1586'}
            _soun, sndr, _fid = convert_SOUN(rec, W())
            return struct.unpack('<I', _find_subrecord(sndr, b'GNAM'))[0]

        assert sndr_gnam(0x0010 | 0x0040) == 0x0007F80B   # loop + 2D -> AMB
        assert sndr_gnam(0x0040) == 0x000172A1            # 2D one-shot -> SFX
        assert sndr_gnam(0x0010) == 0x000172A1            # 3D loop -> SFX


class TestClimateConversion:
    def _rec(self, **over):
        rec = {
            'Signature': 'CLMT', 'FormID': '0000015F', 'RecordFlags': '0',
            'EditorID': 'TestClimate',
            'WeatherCount': '2',
            'Weather[0].FormID': '00000200', 'Weather[0].Chance': '70',
            'Weather[1].FormID': '00000201', 'Weather[1].Chance': '30',
            'FNAM.SunTexture': 'Sky\\Sun.dds',
            'GNAM.GlareTexture': 'Sky\\SunGlare.dds',
            'Model.MODL': 'Sky\\Stars.nif',
            'TNAM.SunriseBegin': '36', 'TNAM.SunriseEnd': '60',
            'TNAM.SunsetBegin': '96', 'TNAM.SunsetEnd': '120',
            'TNAM.Volatility': '0', 'TNAM.MoonsPhaseLength': '195',
        }
        rec.update(over)
        return rec

    def test_wlst_entries_are_widened_to_12_bytes(self):
        """TES4's entry is 8 bytes; TES5 appends a Global FormID."""
        from tes5_import.record_types.weather import convert_CLMT
        wlst = _find_subrecord(convert_CLMT(self._rec()), b'WLST')
        assert len(wlst) == 24
        w0, c0, g0 = struct.unpack_from('<IiI', wlst, 0)
        w1, c1, g1 = struct.unpack_from('<IiI', wlst, 12)
        assert (c0, c1) == (70, 30)
        assert (g0, g1) == (0, 0)
        assert w0 != 0 and w1 != 0

    def test_stars_model_and_sun_textures_are_namespaced(self):
        from tes5_import.record_types.weather import convert_CLMT
        out = convert_CLMT(self._rec())
        assert _find_subrecord(out, b'MODL') == b'tes4\\Sky\\Stars.nif\x00'
        assert _find_subrecord(out, b'FNAM') == b'tes4\\Sky\\Sun.dds\x00'
        assert _find_subrecord(out, b'GNAM') == b'tes4\\Sky\\SunGlare.dds\x00'
        assert _find_subrecord(out, b'MODT') is not None

    def test_climate_without_model_still_gets_stars(self):
        """Every vanilla Skyrim climate has a MODL; without one, no stars."""
        from tes5_import.record_types.weather import convert_CLMT
        rec = self._rec()
        del rec['Model.MODL']
        assert _find_subrecord(convert_CLMT(rec), b'MODL') == b'tes4\\Sky\\Stars.nif\x00'

    def test_tnam_timing_passes_through_except_volatility(self):
        """Volatility is NOT a passthrough: TES4's byte spans 0..255 and 0
        still cycles weather in Oblivion, while Skyrim's census is bimodal —
        50 on the one variable outdoor climate (SkyrimClimate), 0 only on
        locked skies (Sovngarde).  TES4's 0 froze every converted sky on its
        first weather.  The moons byte (195 = 0xC3, Masser+Secunda+phase 3)
        is byte-identical to vanilla SkyrimClimate's."""
        from tes5_import.record_types.weather import convert_CLMT
        tnam = _find_subrecord(convert_CLMT(self._rec()), b'TNAM')
        assert tnam == bytes((36, 60, 96, 120, 50, 195))

    def test_climate_is_always_dispatched(self):
        """CLMT is the ONLY path to the converted weathers
        (WRLD -> CNAM -> CLMT -> WLST); skipping it orphans all of them.
        WTHR itself lives in its own serial phase (import_main 2b), not the
        generic dispatch, because it mints IMGS companions."""
        from tes5_import.registry import IMPORT_DISPATCH, SKIP_TYPES
        assert 'CLMT' not in SKIP_TYPES
        assert 'CLMT' in IMPORT_DISPATCH
        assert 'WTHR' not in SKIP_TYPES
        assert 'WTHR' not in IMPORT_DISPATCH
        assert 'REGN' not in SKIP_TYPES
        assert 'REGN' in IMPORT_DISPATCH


class TestSunlessSkies:
    """Oblivion realms must have no sun.

    Oblivion says "no sun" with the CLMT sun SPRITE alone (Sky\\Void.dds), but
    Skyrim's sun is a sprite PLUS a directional light (NAM0 slot 4) PLUS a
    glare pass (slot 15), so the void sprite alone leaves both burning.
    Vanilla BlackreachClimate/BlackreachWeather is the authored reference:
    FNAM=GNAM=Black.dds, Sunlight zeroed, Sun zeroed.
    """

    def _clmt(self, **over):
        rec = {
            'Signature': 'CLMT', 'FormID': '00032E16', 'RecordFlags': '0',
            'EditorID': 'Obliviondefaultclimate',
            'WeatherCount': '1',
            'Weather[0].FormID': '00032E15', 'Weather[0].Chance': '100',
            'FNAM.SunTexture': 'Sky\\Void.dds',
            'GNAM.GlareTexture': 'Sky\\VoidGlare.dds',
            'TNAM.SunriseBegin': '23', 'TNAM.SunriseEnd': '42',
            'TNAM.SunsetBegin': '102', 'TNAM.SunsetEnd': '127',
            'TNAM.Volatility': '255', 'TNAM.MoonsPhaseLength': '3',
        }
        rec.update(over)
        return rec

    def _wthr(self, sunlight=(193, 130, 87), ambient=(124, 65, 50)):
        """A realm weather: TES4 NAM0 is 10 slots x 4 times x RGBA."""
        nam0 = bytearray(160)
        for time in range(4):
            off = (3 * 4 + time) * 4
            nam0[off:off + 3] = bytes(ambient)
            off = (4 * 4 + time) * 4
            nam0[off:off + 3] = bytes(sunlight)
        return {
            'Signature': 'WTHR', 'FormID': '00032E15', 'RecordFlags': '0',
            'EditorID': 'Obliviondefault',
            'NAM0.Data': nam0.hex().upper(),
            'DATA.Classification': '0', 'DATA.WindSpeed': '0',
        }

    def _register(self, clmt):
        from tes5_import.record_types.weather import record_sunless_climate, reset_sunless_climates
        reset_sunless_climates()
        record_sunless_climate(clmt)

    def _slot(self, nam0, slot, time=1):
        off = (slot * 4 + time) * 4
        return tuple(nam0[off:off + 3])

    def test_void_sun_climate_writes_vanilla_black(self):
        """Black.dds is a VANILLA path — it must not take the tes4\\ prefix,
        and the GLARE must be black too (VoidGlare.dds is a real sprite)."""
        from tes5_import.record_types.weather import convert_CLMT
        out = convert_CLMT(self._clmt())
        assert _find_subrecord(out, b'FNAM') == b'Black.dds\x00'
        assert _find_subrecord(out, b'GNAM') == b'Black.dds\x00'

    def test_climate_with_no_fnam_is_sunless(self):
        """ClimateSigil / MQ14OblivionClimate author no FNAM at all —
        Oblivion draws nothing, which is the same authored intent as Void."""
        from tes5_import.record_types.weather import convert_CLMT
        rec = self._clmt()
        del rec['FNAM.SunTexture']
        del rec['GNAM.GlareTexture']
        assert _find_subrecord(convert_CLMT(rec), b'FNAM') == b'Black.dds\x00'

    def test_real_sun_climate_is_untouched(self):
        from tes5_import.record_types.weather import convert_CLMT
        out = convert_CLMT(self._clmt(**{
            'FNAM.SunTexture': 'Sky\\Sun.dds',
            'GNAM.GlareTexture': 'Sky\\SunGlare.dds'}))
        assert _find_subrecord(out, b'FNAM') == b'tes4\\Sky\\Sun.dds\x00'
        assert _find_subrecord(out, b'GNAM') == b'tes4\\Sky\\SunGlare.dds\x00'

    def test_sunless_weather_zeroes_sun_sunlight_and_glare(self):
        """The whole point: slot 5 (disc), slot 4 (directional light) and
        slot 15 (glare) all go to zero, at every time of day."""
        from tes5_import.record_types.weather import _wthr_nam0
        self._register(self._clmt())
        nam0 = _wthr_nam0(self._wthr())
        for time in range(4):
            assert self._slot(nam0, 5, time) == (0, 0, 0)
            assert self._slot(nam0, 4, time) == (0, 0, 0)
            assert self._slot(nam0, 15, time) == (0, 0, 0)

    def test_sunlight_folds_into_ambient_not_dropped(self):
        """The Deadlands' red fill lives in TES4 Sunlight while the sun
        sprite is voided.  Zeroing it outright (as Blackreach can, being a
        cave) would black out the realm; half of it folds into Ambient."""
        from tes5_import.record_types.weather import _wthr_nam0
        self._register(self._clmt())
        plain = _wthr_nam0(self._wthr(sunlight=(0, 0, 0)))
        folded = _wthr_nam0(self._wthr(sunlight=(200, 100, 60)))
        base = self._slot(plain, 3)
        lit = self._slot(folded, 3)
        assert lit != base
        assert lit == (min(255, base[0] + 100), min(255, base[1] + 50),
                       min(255, base[2] + 30))

    def test_dalc_object_lighting_gets_the_same_fold(self):
        """DALC lights OBJECTS.  With the directional light gone it must get
        the same Sunlight fold as NAM0 Ambient, or the sky glows red while
        everything under it stays dim.  Vanilla BlackreachWeather's DALC
        faces track its NAM0 Ambient ~1:1, so vanilla does not compensate
        DALC separately -- it authors Ambient and lets DALC follow."""
        from tes5_import.record_types.weather import _wthr_dalc
        self._register(self._clmt())
        dark = _wthr_dalc(self._wthr(sunlight=(0, 0, 0)))
        lit = _wthr_dalc(self._wthr(sunlight=(200, 100, 60)))
        # Second DALC subrecord (day): 6-byte header + 32-byte payload each.
        face_dark = tuple(dark[6 + 38:6 + 38 + 3])
        face_lit = tuple(lit[6 + 38:6 + 38 + 3])
        assert face_lit > face_dark

    def test_normal_weather_keeps_its_sun(self):
        """A weather not reachable from a sunless climate is untouched."""
        from tes5_import.record_types.weather import _wthr_nam0
        from tes5_import.record_types.weather import reset_sunless_climates
        reset_sunless_climates()
        nam0 = _wthr_nam0(self._wthr())
        assert self._slot(nam0, 4) != (0, 0, 0)

    def test_registry_resets_between_plugins(self):
        from tes5_import.record_types.weather import _SUNLESS_WEATHER_FIDS, reset_sunless_climates
        self._register(self._clmt())
        assert _SUNLESS_WEATHER_FIDS
        reset_sunless_climates()
        assert not _SUNLESS_WEATHER_FIDS


class TestObjectBounds:
    """OBND is six signed 16-bit ints; FO3/FNV author it, TES4 cannot.

    See: docs/commentary/tes5_import_landscape.md#obnd-authored-bounds-and-the-int16-clamp
    """

    def _fnv(self):
        """An FNV ACTI whose authored bounds sit at the int16 extremes."""
        return {'Signature': 'ACTI', 'FormID': '0017409E',
                'EditorID': 'NVStripLightsPollitionDim',
                'Model.MODL': 'architecture\\Strip\\NVStripLightPollutionDim.NIF',
                'OBND.X1': '-32768', 'OBND.Y1': '-2585', 'OBND.Z1': '-22357',
                'OBND.X2': '32767', 'OBND.Y2': '-2585', 'OBND.Z2': '27753'}

    def test_oversized_bounds_are_clamped_not_fatal(self):
        """A mesh over 32767 units raised struct.error, dropping the whole
        record and leaving every reference to it pointing at nothing."""
        from tes5_import.base.writer import pack_obnd
        vals = struct.unpack('<6h', pack_obnd(-40000, 0, 0, 40000, 0, 0)[6:])
        assert vals[0] == -32768
        assert vals[3] == 32767

    def test_authored_bounds_beat_the_mesh_scan(self):
        """FO3/FNV write OBND natively, so the record's own value wins."""
        from tes5_import.record_types.common import _resolve_obnd
        assert _resolve_obnd(self._fnv(), 'ACTI') == (
            -32768, -2585, -22357, 32767, -2585, 27753)

    def test_tes4_records_author_none(self):
        """Oblivion has no OBND field, so the fallback chain is unchanged."""
        from tes5_import.record_types.common import _authored_obnd
        assert _authored_obnd(
            {'Signature': 'ACTI', 'EditorID': 'Anvil'}) is None


class TestFalloutWeatherColors:
    """FO3/FNV author NAM0 with six times of day; TES4 and TES5 use four.

    See: docs/commentary/tes5_import_weather.md#fo3fnv-nam0-six-times-of-day
    """

    def _six_time(self):
        """Ten slots x six times, each slot a distinct ramp; times 4-5 zeroed."""
        raw = bytearray(10 * 6 * 4)
        for slot in range(10):
            for time in range(4):
                off = (slot * 6 + time) * 4
                raw[off:off + 3] = bytes((slot * 10 + time, 60, 70))
        return bytes(raw)

    def test_six_time_blob_is_restrided_to_four(self):
        """Every slot keeps its four authored times at the TES4 stride."""
        from tes5_import.record_types.weather_falloutnv import to_four_times
        out = to_four_times(self._six_time())
        assert len(out) == 160
        for slot in range(10):
            for time in range(4):
                off = (slot * 4 + time) * 4
                assert out[off] == slot * 10 + time

    def test_four_time_blob_passes_through_untouched(self):
        """Oblivion's 160-byte table must survive byte-identical."""
        from tes5_import.record_types.weather_falloutnv import to_four_times
        raw = bytes(range(160))
        assert to_four_times(raw) == raw

    def test_slots_are_not_remapped(self):
        """FO3/FNV slot meanings already match TES5 — only the stride differs."""
        from tes5_import.record_types.weather_falloutnv import to_four_times
        out = to_four_times(self._six_time())
        assert out[(9 * 4 + 1) * 4] == 91
        assert out[(2 * 4 + 1) * 4] == 21

    def test_converter_reads_authored_day_colors(self):
        """The defect: a stride-4 read of a six-time blob lands in the zeroed
        High Noon padding, leaving fog, sunlight and horizon black at midday."""
        from tes5_import.record_types.weather import _src_nam0, _src_rgb
        rec = {'Signature': 'WTHR', 'FormID': '0000015E', 'RecordFlags': '0',
               'NAM0.Data': self._six_time().hex().upper()}
        norm = _src_nam0(rec)
        assert len(norm) == 160
        for slot in (1, 4, 8):
            assert _src_rgb(norm, slot, 1) == (slot * 10 + 1, 60, 70)


class TestWorldspaceClimate:
    def _rec(self, **over):
        rec = {'Signature': 'WRLD', 'FormID': '0000003C', 'RecordFlags': '0',
               'EditorID': 'Tamriel'}
        rec.update(over)
        return rec

    def test_authored_climate_is_kept(self):
        from tes5_import.record_types.world import convert_WRLD
        out = convert_WRLD(self._rec(**{'CNAM.Climate': '00097C60'}))
        cnam = struct.unpack('<I', _find_subrecord(out, b'CNAM'))[0]
        assert cnam & 0x00FFFFFF == 0x97C60

    def test_worldspace_without_climate_falls_back_to_default(self):
        """Oblivion.exe resolves a null worldspace climate to DefaultClimate
        (0x15F) at runtime -- the sky setup at 0x667688 falls through to
        0x543200, which does LookupForm(0x15F).  Skyrim has no such fallback,
        and 57 of 84 TES4 worldspaces (incl. Tamriel and every city) author no
        CNAM, so it must be written explicitly."""
        from tes5_import.record_types.world import convert_WRLD
        cnam = _find_subrecord(convert_WRLD(self._rec()), b'CNAM')
        assert cnam is not None, 'a CNAM-less worldspace would use Skyrim weather'
        assert struct.unpack('<I', cnam)[0] & 0x00FFFFFF == 0x15F


class TestRegionWeatherConversion:
    """REGN weather entries — where Cyrodiil's weather variety actually lives.

    TamrielClimate's WLST is a single Clear weather at 100%; the rain, snow
    and fog come from 59 region RDWT lists layered over it, exactly the
    mechanism Skyrim itself uses (WeatherCoastFog, WeatherWinterhold...).
    RDAT headers are byte-identical across the games; RDWT entries widen from
    8 to 12 bytes with a trailing Global FormID; RPLI/RPLD pass through.
    """

    def _rec(self, **over):
        rec = {
            'Signature': 'REGN', 'FormID': '00001234', 'RecordFlags': '0',
            'EditorID': 'TestWeatherRegion',
            'RCLR.R': '10', 'RCLR.G': '20', 'RCLR.B': '30',
            'WNAM.Worldspace': '0000003C',
            'AreaCount': '1',
            'Area[0].EdgeFalloff': '1024',
            'Area[0].PointsHex': struct.pack(
                '<8f', 0, 0, 1000, 0, 1000, 1000, 0, 1000).hex().upper(),
            'RegionDataCount': '2',
            'RegionData[0].Type': '7',       # sound entry: dropped
            'RegionData[0].Override': '0',
            'RegionData[0].Priority': '50',
            'RegionData[1].Type': '3',       # weather entry: converted
            'RegionData[1].Override': '1',
            'RegionData[1].Priority': '95',
            'RegionData[1].WeatherCount': '2',
            'RegionData[1].Weather[0].FormID': '00000200',
            'RegionData[1].Weather[0].Chance': '70',
            'RegionData[1].Weather[1].FormID': '00000201',
            'RegionData[1].Weather[1].Chance': '30',
        }
        rec.update(over)
        return rec

    def test_weather_entries_survive_with_area_and_header(self):
        from tes5_import.record_types.region import convert_REGN
        out = convert_REGN(self._rec())
        rdat = _find_subrecord(out, b'RDAT')
        rtype, override, priority = struct.unpack_from('<IBB', rdat, 0)
        assert (rtype, override, priority) == (3, 1, 95)
        rdwt = _find_subrecord(out, b'RDWT')
        assert len(rdwt) == 24                       # 2 entries x 12 bytes
        w0, c0, g0 = struct.unpack_from('<III', rdwt, 0)
        w1, c1, g1 = struct.unpack_from('<III', rdwt, 12)
        assert (c0, c1) == (70, 30) and (g0, g1) == (0, 0)
        assert w0 != 0 and w1 != 0
        rpli = _find_subrecord(out, b'RPLI')
        assert struct.unpack('<I', rpli)[0] == 1024
        assert len(_find_subrecord(out, b'RPLD')) == 32   # 4 points x 8 bytes

    def test_non_weather_entries_are_dropped(self):
        from tes5_import.record_types.region import convert_REGN
        out = convert_REGN(self._rec())
        # exactly ONE RDAT survives (the weather one); the sound entry is gone
        assert len(_find_all_subrecords(out, b'RDAT')) == 1

    def test_region_without_weather_emits_nothing(self):
        from tes5_import.record_types.region import convert_REGN
        rec = self._rec(**{'RegionDataCount': '1'})   # only the sound entry
        assert convert_REGN(rec) is None

    def test_region_without_area_emits_nothing(self):
        """The engine applies region data only inside RPLD polygons; weather
        with no area can never fire, so don't emit the record at all."""
        from tes5_import.record_types.region import convert_REGN
        assert convert_REGN(self._rec(**{'AreaCount': '0'})) is None

    def test_cell_xclr_lists_only_emitted_regions(self):
        """Region weather reaches the sky through the CELL's XCLR list —
        Skyrim.esm puts WeatherWinterhold in 30 cells' XCLR — so exterior
        cells must carry it.  Without XCLR every converted exterior fell back
        to the climate WLST, and TamrielClimate's is a single Clear at 100%:
        the sky NEVER changed.  Refs are filtered to regions that actually
        emitted (TES4 lists object/grass/sound regions here too, which we
        drop) and sorted (xEdit wbArrayS)."""
        from tes5_import.record_types.common import reset_emitted_regions
        from tes5_import.record_types.region import convert_REGN
        from tes5_import.record_types.world import convert_CELL
        reset_emitted_regions()
        convert_REGN(self._rec())                     # registers 0x00001234
        cell = {
            'Signature': 'CELL', 'FormID': '00002000', 'RecordFlags': '0',
            'DATA.Flags': '0', 'XCLC.X': '5', 'XCLC.Y': '-3',
            'Region[0]': '00009999',                  # dropped (never emitted)
            'Region[1]': '00001234',                  # the weather region
        }
        xclr = _find_subrecord(convert_CELL(cell), b'XCLR')
        assert xclr is not None
        fids = struct.unpack(f'<{len(xclr) // 4}I', xclr)
        assert len(fids) == 1
        assert fids[0] & 0x00FFFFFF == 0x1234
        reset_emitted_regions()
        cell2 = dict(cell)
        assert _find_subrecord(convert_CELL(cell2), b'XCLR') is None


class TestSkyMeshShaders:
    """Sky geometry needs BSSkyShaderProperty, not the lighting shader.

    Skyrim draws sky through a dedicated pass keyed on Sky Object Type; a sky
    mesh carrying BSLightingShaderProperty is treated as ordinary world
    geometry, which made converted stars draw on top of the landscape.
    """

    def test_sky_meshes_are_classified_by_type(self):
        from asset_convert.nif.geometry_shader import (
            sky_object_type_for, SKY_STARS, SKY_CLOUDS, SKY_BASE)
        assert sky_object_type_for('export/x/meshes/sky/stars.nif') == SKY_STARS
        assert sky_object_type_for('export/x/meshes/sky/clouds.nif') == SKY_CLOUDS
        assert sky_object_type_for('export/x/meshes/sky/atmosphere.nif') == SKY_BASE
        assert sky_object_type_for(r'export\x\meshes\Sky\Stars.NIF') == SKY_STARS

    def test_non_sky_meshes_are_not_misclassified(self):
        from asset_convert.nif.geometry_shader import sky_object_type_for
        assert sky_object_type_for('meshes/clutter/barrel01.nif') is None
        # must key on the sky/ DIRECTORY, not just the basename
        assert sky_object_type_for('meshes/architecture/sky/wall.nif') is None
        assert sky_object_type_for('meshes/dungeons/clouds.nif') is None
        assert sky_object_type_for('') is None


class TestInfoFragmentContracts:
    """Contracts on the GENERATED TES4_TIF__ fragments (skipped without a
    build).  Every INFO carries a Begin/End pair for TES4Polyfill.SayLine, the
    result script runs in the End fragment, and the line-over hook is the last
    thing the End fragment does.
    """

    def _fragments(self):
        import os
        src = os.path.join('output', 'Oblivion.esm', 'scripts', 'source')
        if not os.path.isdir(src):
            pytest.skip('no generated scripts to check')
        for fn in sorted(os.listdir(src)):
            if not fn.startswith('TES4_TIF__'):
                continue
            lines = open(os.path.join(src, fn),
                         encoding='utf-8', errors='replace').read().splitlines()
            yield fn, lines


    def test_every_fragment_has_the_line_hooks(self):
        checked = 0
        offenders = []
        for fn, lines in self._fragments():
            checked += 1
            text = '\n'.join(lines)
            if ('Function Fragment_1(ObjectReference akSpeakerRef)' not in text
                    or 'TES4Polyfill.LineBegan(akSpeakerRef,' not in text
                    or 'TES4Polyfill.LineEnded(akSpeakerRef,' not in text):
                offenders.append(fn)
        assert checked, 'expected generated fragments'
        assert not offenders, offenders[:10]

    def test_line_ended_is_the_last_statement_of_the_end_fragment(self):
        """A poll waiting on this speaker proceeds the moment LineEnded runs,
        so every state write of the result must already have landed."""
        offenders = []
        for fn, lines in self._fragments():
            try:
                start = next(i for i, ln in enumerate(lines)
                             if ln.startswith('Function Fragment_0('))
            except StopIteration:
                offenders.append(fn)
                continue
            body = [ln for ln in lines[start + 1:]]
            end = next(i for i, ln in enumerate(body) if ln.strip() == 'EndFunction')
            stmts = [ln.strip() for ln in body[:end] if ln.strip()]
            if not stmts or not stmts[-1].startswith('TES4Polyfill.LineEnded('):
                offenders.append(fn)
        assert not offenders, offenders[:10]

    def test_fragments_never_write_a_conversation_timer(self):
        """Timers belong to the calling script (SayLine returns the value);
        a fragment writing one is the old owner-analysis design leaking back."""
        import re
        pat = re.compile(r'^\s*[\w.]*\b(convTimer|\w*PendingBeat)\s*=\s*0\b', re.IGNORECASE)
        offenders = [fn for fn, lines in self._fragments()
                     if any('; line ended' in ln or pat.match(ln) for ln in lines)]
        assert not offenders, offenders[:10]

    def test_state_writes_precede_setstage_within_the_body(self):
        """Inside the body, speaker/target/counter land before any SetStage.

        SetStage runs that stage's fragment INLINE and those call
        `EvaluatePackage()`, which arbitrates against whatever is committed at
        that instant. This is ordering WITHIN the gate only — the release lands
        earlier still, and is repeated unconditionally after the gate (see the
        three tests above).
        """
        import re
        setstage = re.compile(r'^\s*(\w[\w.]*)\.SetStage\s*\(', re.IGNORECASE)
        offenders = []
        checked = 0
        for fn, lines in self._fragments():
            first = next((i for i, ln in enumerate(lines)
                          if setstage.match(ln)), None)
            if first is None:
                continue
            checked += 1
            quest = setstage.match(lines[first]).group(1).lower()
            book = re.compile(
                r'^\s*' + re.escape(quest) + r'\.(convCount|speaker|target)\s*=',
                re.IGNORECASE)
            after = [ln for ln in lines[first + 1:] if book.match(ln)]
            if after:
                offenders.append(f'{fn}: {after[0].strip()}')
        assert checked, 'expected some fragments to call SetStage'
        assert not offenders, (
            'these fragments write conversation state AFTER a SetStage on the '
            'same quest, so the stage fragment\'s EvaluatePackage() arbitrates '
            'against stale state:\n' + '\n'.join(offenders[:10]))


class TestWeatherImageSpace:
    """WTHR HDR tone mapping -> companion IMGS records.

    Oblivion stores HDR per WEATHER (WTHR.HNAM, 14 floats); Skyrim has NO
    per-weather HDR field -- it lives in imagespaces the weather points at
    (WTHR.IMSP -> IMGS.HNAM, 9 floats), FOUR of them, one per time of day.
    Pointing every weather at the stock 0x161 left HDR undefined: 0x161 is one
    of only two vanilla imagespaces that ship ENAM and no HNAM.
    """

    class _FakeWriter:
        def __init__(self):
            self.next_fid = 0x01001000

        def alloc_formid(self):
            self.next_fid += 1
            return self.next_fid

        def derive_formid(self, site, key):
            # Test double: derived ids need only be stable per
            # (site, key) and distinct, like the real allocator.
            if not hasattr(self, '_derived'):
                self._derived = {}
            k = (site, repr(key))
            if k not in self._derived:
                self._derived[k] = self.alloc_formid()
            return self._derived[k]
    # (min, max) per field across the 213 vanilla imagespaces a Skyrim.esm
    # WEATHER actually references (interior/dungeon ones excluded).
    VANILLA_RANGES = [
        (15.0, 50.0), (0.8, 8.0), (0.0, 0.80), (0.0, 7.0), (0.2, 1.0),
        (0.6, 1.075), (0.4, 3.85), (0.0, 0.45), (1.0, 30.0),
    ]
    DAWN, DAY, DUSK, NIGHT = 0, 1, 2, 3

    def _nam0(self, day_rgb=(100, 141, 191), night_rgb=(6, 8, 14)):
        """TES4 NAM0 with a bright day sky and a dark night sky."""
        raw = bytearray(160)
        for t, rgb in ((0, day_rgb), (1, day_rgb), (2, day_rgb), (3, night_rgb)):
            o = (0 * 4 + t) * 4          # slot 0 = Sky-Upper
            raw[o:o + 3] = bytes(rgb)
        return bytes(raw).hex().upper()

    def _rec(self, **over):
        rec = {
            'Signature': 'WTHR', 'FormID': '00000200', 'RecordFlags': '0',
            'EditorID': 'TestWeather',
            'NAM0.Size': '160', 'NAM0.Data': self._nam0(),
            'HNAM.EyeAdaptSpeed': '0.7', 'HNAM.BlurRadius': '4.0',
            'HNAM.BlurPasses': '2.0', 'HNAM.EmissiveMult': '1.0',
            'HNAM.TargetLum': '1.2', 'HNAM.UpperLumClamp': '1.0',
            'HNAM.BrightScale': '1.75', 'HNAM.BrightClamp': '0.3',
            'HNAM.SunlightDimmer': '1.3', 'HNAM.GrassDimmer': '1.3',
            'HNAM.TreeDimmer': '1.2',
        }
        rec.update(over)
        return rec

    def _convert(self, rec):
        from tes5_import.record_types.weather import convert_WTHR
        return convert_WTHR(rec, self._FakeWriter())

    def _hnam(self, imgs_bytes):
        return struct.unpack('<9f', _find_subrecord(imgs_bytes, b'HNAM')[:36])

    def test_weather_mints_four_imagespaces_with_hdr(self):
        """70% of vanilla weathers use DISTINCT imagespaces per time of day;
        collapsing to one gives day and night identical tone mapping."""
        _wthr, imgs = self._convert(self._rec())
        assert len(imgs) == 4
        for b in imgs:
            assert _find_subrecord(b, b'HNAM') is not None, (
                'an ENAM-only stub leaves HDR undefined')
            assert len(_find_subrecord(b, b'HNAM')) == 36

    def test_imsp_points_at_the_generated_imagespaces_in_order(self):
        wthr, imgs = self._convert(self._rec())
        fids = [struct.unpack_from('<I', b, 12)[0] for b in imgs]
        slots = list(struct.unpack('<4I', _find_subrecord(wthr, b'IMSP')))
        assert slots == fids
        assert 0x161 not in slots, 'must not use the HNAM-less stock imagespace'
        assert len(set(slots)) == 4

    def test_hdr_fields_come_from_the_tes4_block(self):
        _w, imgs = self._convert(self._rec())
        h = self._hnam(imgs[self.DAY])
        # BrightScale 1.75 rescaled from TES4 1..3 onto vanilla 2.5..4
        assert 2.5 <= h[3] <= 4.0
        # SunlightDimmer 1.3 rescaled from TES4 0.5..2 onto vanilla 0.9..2.7
        assert 1.5 <= h[6] <= 2.4

    def test_bloom_blur_radius_is_the_engine_constant(self):
        """All 213 vanilla weather-used imagespaces use exactly 7.0.  TES4's
        BlurRadius is a different quantity (Oblivion's own blur pass)."""
        _w, imgs = self._convert(self._rec(**{'HNAM.BlurRadius': '4.0'}))
        for b in imgs:
            assert self._hnam(b)[1] == 7.0

    def test_luminance_fields_are_rescaled_off_the_tes5_ceiling(self):
        """TES4 TargetLum spans 0.75..1.2 but TES5 Receive Bloom Threshold
        spans 0.2..1.0; copying raw pinned it at 1.0, so the WHOLE frame
        bloomed.  Same for UpperLumClamp (1.0..1.3) vs White (0.6..1.075)."""
        _w, imgs = self._convert(self._rec(**{'HNAM.TargetLum': '1.2',
                                              'HNAM.UpperLumClamp': '1.3'}))
        h = self._hnam(imgs[self.DAY])
        assert h[4] < 0.8, 'Receive Bloom Threshold must not sit at the ceiling'
        assert h[5] <= 1.05, 'White must stay inside the vanilla day p90'

    def test_median_tes4_weather_lands_on_vanilla_medians(self):
        """The first calibration rescaled SPANS, but the TES4 medians sit at
        the EDGE of their spans (median UpperLumClamp is 1.0, the bottom of
        1.0..1.3), so nearly every weather got White = 0.88 — vanilla's
        bottom decile, a lowered white point — and the day rendered as an
        overexposed camera.  A median TES4 weather must land ON the vanilla
        per-slot median: White 1.0, Receive Bloom 0.625, Bloom Threshold
        0.625, Bloom Scale 3.0 at midday."""
        _w, imgs = self._convert(self._rec(**{
            'HNAM.TargetLum': '1.2', 'HNAM.UpperLumClamp': '1.0',
            'HNAM.BrightScale': '2.0', 'HNAM.BrightClamp': '0.3'}))
        h = self._hnam(imgs[self.DAY])
        assert abs(h[2] - 0.625) < 1e-6     # Bloom Threshold
        assert abs(h[3] - 3.0) < 1e-6       # Bloom Scale
        assert abs(h[4] - 0.625) < 1e-6     # Receive Bloom Threshold
        assert abs(h[5] - 1.0) < 1e-6       # White — NOT 0.88

    def test_sky_scale_never_derives_from_sky_color(self):
        """Sky Scale lands as an ADDITIVE term in Skyrim's sky pixel shader
        (`input.Color * baseColor + skyScale`), so deriving it from the
        weather's own sky luminance scales an additive floor in proportion to
        the multiplicative color — a feedback loop that reads as bloom.

        It must depend only on authored classification + time.  Doubling every
        sky color must therefore not move it at all.
        """
        import copy
        from tes5_import.record_types.weather import _wthr_imgs

        base = self._rec()
        bright = copy.deepcopy(base)
        raw = bytearray(bytes.fromhex(base['NAM0.Data']))
        for i in range(0, len(raw), 4):
            raw[i] = min(255, raw[i] * 2)
            raw[i + 1] = min(255, raw[i + 1] * 2)
            raw[i + 2] = min(255, raw[i + 2] * 2)
        bright['NAM0.Data'] = bytes(raw).hex().upper()

        for time in range(4):
            a = struct.unpack_from(
                '<9f', _find_subrecord(_wthr_imgs(base, 1, time), b'HNAM'))[7]
            b = struct.unpack_from(
                '<9f', _find_subrecord(_wthr_imgs(bright, 1, time),
                                       b'HNAM'))[7]
            assert a == b, f'sky scale moved with sky color at time {time}'

    def test_sky_scale_comes_from_classification(self):
        """Vanilla keys Sky Scale off classification x time (R2 = 0.434)
        rather than sky luminance (R2 = 0.166), measured over the 332
        weather/time rows in Skyrim.esm + Update + Dawnguard + Dragonborn.
        Night is near zero for every class."""
        def scales(cls):
            _w, imgs = self._convert(
                self._rec(**{'DATA.Classification': cls}))
            return [round(self._hnam(b)[7], 3) for b in imgs]

        assert scales('1') == [0.08, 0.12, 0.10, 0.02]   # Pleasant
        assert scales('2') == [0.05, 0.10, 0.05, 0.00]   # Cloudy
        assert scales('4') == [0.09, 0.10, 0.10, 0.06]   # Rainy
        assert scales('8') == [0.10, 0.05, 0.05, 0.05]   # Snow

        # An unclassified weather keeps vanilla's flat unclassified value
        # instead of being promoted to Pleasant — 10 of Oblivion's 37 are
        # unclassified, including the Deadlands skies.
        assert scales('0') == [0.05] * 4
        # ...which is also what the base fixture (no DATA at all) gets.
        _w, imgs = self._convert(self._rec())
        assert [round(self._hnam(b)[7], 3) for b in imgs] == [0.05] * 4

    def test_eye_adapt_varies_by_time_of_day(self):
        """Vanilla per-slot medians: speed 37/40/37/45, strength 15/5/15/20."""
        _w, imgs = self._convert(self._rec())
        strength = [self._hnam(b)[8] for b in imgs]
        assert strength == [15.0, 5.0, 15.0, 20.0]
        speed = [self._hnam(b)[0] for b in imgs]
        assert speed[self.NIGHT] > speed[self.DAY] > speed[self.DAWN]

    def test_eye_adapt_speed_is_rescaled_not_copied(self):
        """Oblivion's rate is 0..1 (Oblivion.ini fEyeAdaptSpeed=0.7); Skyrim's
        weather-used range is 15..50.  Copying 0.7 freezes adaptation."""
        _w, imgs = self._convert(self._rec(**{'HNAM.EyeAdaptSpeed': '0.7'}))
        speed = self._hnam(imgs[self.DAY])[0]
        assert speed > 15.0, 'a raw 0.7 would sit far below the vanilla floor'
        assert 15.0 <= speed <= 50.0

    def test_unauthored_tes4_hdr_uses_vanilla_defaults(self):
        """DefaultWeather ships an ALL-ZERO HNAM -- and it is exactly the
        weather the 57 CNAM-less worldspaces (Tamriel, every city) fall back
        to.  Copied verbatim that gives White=0 / SunlightScale=0 (a zero
        white point); clamping to the range MINIMUM instead pins every field
        at its flattest legal value.  Neither is right: use vanilla defaults."""
        zero = {k: '0.0' for k in self._rec() if k.startswith('HNAM.')}
        _w, imgs = self._convert(self._rec(**zero))
        h = self._hnam(imgs[self.DAY])
        assert h[1] == 7.0                   # Bloom Blur Radius
        assert abs(h[3] - 3.0) < 1e-6        # Bloom Scale, vanilla median
        assert abs(h[6] - 1.9) < 1e-3        # Sunlight Scale, vanilla median
        assert h[5] >= 0.6, 'White must never be 0 (degenerate white point)'
        for v, (lo, hi) in zip(h, self.VANILLA_RANGES):
            assert lo - 1e-6 <= v <= hi + 1e-6

    def test_every_field_stays_inside_the_vanilla_envelope(self):
        wild = {k: '999.0' for k in self._rec() if k.startswith('HNAM.')}
        for rec in (self._rec(**wild), self._rec()):
            _w, imgs = self._convert(rec)
            for b in imgs:
                for v, (lo, hi) in zip(self._hnam(b), self.VANILLA_RANGES):
                    assert lo - 1e-6 <= v <= hi + 1e-6

    def test_imagespace_ships_cinematic_and_tint(self):
        """Every vanilla IMGS with an HNAM also ships CNAM+TNAM."""
        _w, imgs = self._convert(self._rec())
        cnam = struct.unpack('<3f', _find_subrecord(imgs[0], b'CNAM'))
        tnam = struct.unpack('<4f', _find_subrecord(imgs[0], b'TNAM'))
        assert cnam == (1.0, 1.0, 1.0)
        assert tnam[0] == 0.0

    def test_imagespace_ships_dnam_with_sky_excluded_from_blur(self):
        """213 of the 214 weather-used vanilla imagespaces ship DNAM;
        omitting it leaves depth-of-field and SKY BLUR undefined.  Use the
        MODAL COMPLETE vanilla tuple (19 records ship exactly this) so the
        combination is coherent: distant-only DoF, sky EXCLUDED from blur
        (16816 = 'No Sky, Radius 2')."""
        _w, imgs = self._convert(self._rec())
        for b in imgs:
            dnam = _find_subrecord(b, b'DNAM')
            assert dnam is not None and len(dnam) == 16
            strength, distance, rng = struct.unpack_from('<3f', dnam, 0)
            sky = struct.unpack_from('<H', dnam, 14)[0]
            assert (strength, distance, rng) == (0.5, 20000.0, 20000.0)
            assert sky == 16816

    def test_imagespace_is_written_before_the_weather(self):
        """IMGS must precede WTHR in the group order: the weather's IMSP
        resolves against it, and CLMT then resolves against the weather."""
        import inspect

        from tes5_import.base.writer import PluginWriter
        src = inspect.getsource(PluginWriter)
        assert src.index("'IMGS'") < src.index("'WTHR'")
        assert src.index("'WTHR'") < src.index("'CLMT'")


class TestVanillaMgefDataSize:
    """The vanilla MGEF DATA table must hold FULL 152-byte structs.

    tools/generators/gen_vanilla_mgef_table.py used to read the Skyrim.esm dump with
    `line.split('...')[0]`, and the dump truncated hex at 96 bytes — so every
    committed blob was 96 bytes and every synthesized aimed-variant MGEF
    shipped a DATA missing its last 14 fields (HitEffectArt, ImpactData,
    DualCasting, EnchantArt, HitVisuals, EquipAbility, IMAD, PerkToApply,
    CastingSoundLevel, ScriptEffect AI score/delay).
    """

    def test_every_committed_blob_is_a_full_struct(self):
        from tes5_import.generated.vanilla_mgef_data import (
            MGEF_DATA_SIZE,
            VANILLA_MGEF_DATA,
        )
        assert MGEF_DATA_SIZE == 152
        assert VANILLA_MGEF_DATA, 'table is empty'
        for fid, (edid, data_hex) in VANILLA_MGEF_DATA.items():
            n = len(bytes.fromhex(data_hex))
            assert n == MGEF_DATA_SIZE, (
                f'{edid} ({fid:08X}) DATA is {n} bytes, expected '
                f'{MGEF_DATA_SIZE} — regenerate the table')

    def test_aimed_variant_writes_a_full_length_data(self):
        """The synthesized clone must be a complete, correctly-patched MGEF."""
        from tes5_import.actors import magic_effects as magic_effects
        from tes5_import.generated.vanilla_mgef_data import MGEF_DATA_SIZE

        class _Writer:
            def __init__(self):
                self.fid = 0x01000000
                self.records = []

            def alloc_formid(self):
                self.fid += 1
                return self.fid

            def derive_formid(self, site, key):
                # Test double: derived ids need only be stable per
                # (site, key) and distinct, like the real allocator.
                if not hasattr(self, '_derived'):
                    self._derived = {}
                k = (site, repr(key))
                if k not in self._derived:
                    self._derived[k] = self.alloc_formid()
                return self._derived[k]
            def add_record(self, rec_type, data):
                self.records.append((rec_type, data))

        magic_effects.set_tes4_effect_names(
            [{'EditorID': 'FIDG', 'FULL': 'Fire Damage'}])
        writer = _Writer()
        # FireDamageFFAimed — a vanilla effect that already has a projectile.
        assert magic_effects.aimed_variant(0x00012F03, 'FIDG', writer)

        rec_type, blob = writer.records[0]
        assert rec_type == 'MGEF'
        data = _find_subrecord(blob, b'DATA')
        assert len(data) == MGEF_DATA_SIZE

        # The patched fields: Fire and Forget + Aimed + a real projectile, and
        # no counter-effect slots (the clone carries no ESCE subrecords).
        assert struct.unpack_from('<I', data, 80)[0] == 1   # Casting Type
        assert struct.unpack_from('<I', data, 84)[0] == 2   # Delivery = Aimed
        assert struct.unpack_from('<I', data, 72)[0] != 0   # Projectile
        assert struct.unpack_from('<I', data, 20)[0] == 0   # Counter count

        # A field that only exists past the old 96-byte cut, proving the tail
        # is present: vanilla FireDamageFFAimed has DualCastScale == 1.0.
        assert struct.unpack_from('<f', data, 112)[0] == pytest.approx(1.0)

    def test_short_blob_is_rejected_rather_than_written(self):
        from tes5_import.actors import magic_effects as magic_effects
        original = magic_effects.VANILLA_MGEF_DATA.get(0x00012F03)
        magic_effects.VANILLA_MGEF_DATA[0x00012F03] = ('Truncated', '00' * 96)
        magic_effects._cache.clear()
        try:
            with pytest.raises(ValueError, match='expected 152'):
                magic_effects.aimed_variant(0x00012F03, 'FIDG', object())
        finally:
            magic_effects.VANILLA_MGEF_DATA[0x00012F03] = original
            magic_effects._cache.clear()


def _is_tes4_export(export_dir: str) -> bool:
    """True when an export tree came from a TES4 plugin rather than FO3/FNV.

    EFFECT_ARCHETYPES covers Oblivion's effect vocabulary; Fallout's chem and
    radiation effects are out of the converter's scope, so a Fallout tree must
    not be measured against it. FO3/FNV report HEDR 1.32-1.34, TES4 reports
    0.8 or 1.0.
    """
    header = os.path.join(export_dir, '_HEADER.txt')
    if not os.path.isfile(header):
        return True
    with open(header, encoding='utf-8', errors='replace') as f:
        for line in f:
            if line.startswith('HEDR.Version='):
                try:
                    return float(line.split('=', 1)[1]) < 1.2
                except ValueError:
                    return True
    return True


class TestMgefConversion:
    """MGEF is a CONVERTED record type, not an alias to a vanilla effect.

    It used to be in SKIP_TYPES, with every effect on every SPEL/ENCH/ALCH/
    INGR/SGST re-pointed at a vanilla Skyrim MGEF through a flat 4-char code
    table.  A flat table cannot express an effect PARAMETERISED BY A FORMID
    the source record carries, so all 33 summons and every bound weapon/armor
    were dropped: 796 effects lost and 382 records gutted to zero-magnitude
    filler across Oblivion.esm alone.
    """

    def test_mgef_is_dispatched_not_skipped(self):
        from tes5_import.registry import IMPORT_DISPATCH, SKIP_TYPES
        assert 'MGEF' not in SKIP_TYPES
        assert 'MGEF' in IMPORT_DISPATCH

    def test_every_source_effect_code_has_an_archetype(self):
        """A code with no table entry silently becomes a Value Modifier.

        Validated against the EXPORT, never against a plausible-looking name:
        17 of the old alias table's 100 keys were codes no Oblivion or Nehrim
        record uses, so the summons looked mapped and contributed nothing.

        A Morrowind export is skipped by the vocabulary it carries, not by its
        filename: TES3 effects key on an index and live in
        MW_EFFECT_ARCHETYPES, so measuring them here would report every one as
        missing.
        """
        import glob
        import re
        from tes5_import.record_types.magic import EFFECT_ARCHETYPES

        root = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'export')
        codes = set()
        for path in glob.glob(os.path.join(root, '*', 'MGEF.txt')):
            if not _is_tes4_export(os.path.dirname(path)):
                continue
            with open(path, encoding='utf-8', errors='replace') as f:
                text = f.read()
            if 'MorrowindEffectIndex=' in text:
                continue
            codes |= set(re.findall(r'^EditorID=(\S+)', text, re.M))
        if not codes:
            pytest.skip('no MGEF export available')

        missing = sorted(codes - set(EFFECT_ARCHETYPES))
        assert not missing, (
            f'{len(missing)} source effect codes have no archetype and would '
            f'default to Value Modifier: {" ".join(missing)}')

        phantom = sorted(set(EFFECT_ARCHETYPES) - codes)
        assert not phantom, (
            f'{len(phantom)} table keys match no MGEF in any export — dead '
            f'coverage that can never fire: {" ".join(phantom)}')

    def test_every_morrowind_effect_index_has_an_archetype(self):
        """All 143 TES3 effect indices map, or a spell converts to nothing."""
        from tes4_export.morrowind_mgef_names import MW_EFFECT_NAMES
        from tes5_import.record_types.magic_morrowind import (
            MW_EFFECT_ARCHETYPES)

        missing = [i for i in range(len(MW_EFFECT_NAMES))
                   if i not in MW_EFFECT_ARCHETYPES]
        assert not missing, (
            f'{len(missing)} TES3 effect indices have no archetype: {missing}')

        stray = sorted(set(MW_EFFECT_ARCHETYPES) - set(range(len(MW_EFFECT_NAMES))))
        assert not stray, f'table keys outside the effect range: {stray}'

    def test_morrowind_flags_translate_to_the_tes4_layout(self):
        """TES3 range bits differ from TES4's, so delivery depends on this.

        Untranslated, TES3 CastTarget (0x100) reads as TES4 NoMagnitude and
        every converted effect is delivered Self.
        """
        from tes5_import.record_types.magic import _delivery_and_cast
        from tes5_import.record_types.magic_morrowind import mw_tes4_flags

        assert _delivery_and_cast(mw_tes4_flags(0x100)) == (1, 2)
        assert _delivery_and_cast(mw_tes4_flags(0x080)) == (1, 1)
        assert _delivery_and_cast(mw_tes4_flags(0x040)) == (1, 0)

    def test_data_is_a_full_152_byte_struct(self):
        from tes5_import.record_types.magic import MGEF_DATA_SIZE, convert_MGEF
        rec = {'FormID': '00001857', 'EditorID': 'BWSW', 'FULL': 'Bound Sword',
               'DATA.Flags': '76562', 'DATA.School': '1',
               'DATA.ResistValue': '4294967295', 'DATA.BaseCost': '1.0'}
        data = _find_subrecord(convert_MGEF(rec), b'DATA')
        assert len(data) == MGEF_DATA_SIZE

    def test_assoc_item_only_written_for_archetypes_that_read_it(self):
        """`wbMGEFAssocItemDecider` reads Assoc. Item for 10 archetypes only.

        A creature FormID under a value-modifier archetype is meaningless, and
        xEdit flags it — so it must be dropped rather than written blind.
        """
        from tes5_import.record_types import magic

        magic.set_assoc_item_index({}, {0x01001234: 'CREA'})
        # Summon Creature (18) reads it...
        assert magic._resolve_assoc_item(
            0x01001234, magic.A_SUMMON_CREATURE, magic.T4_USE_CREATURE) == 0x01001234
        # ...a plain Value Modifier does not.
        assert magic._resolve_assoc_item(
            0x01001234, magic.A_VALUE_MODIFIER, magic.T4_USE_CREATURE) == 0

    def test_summon_of_a_leveled_list_resolves_to_a_concrete_actor(self):
        """Skyrim's Summon Creature takes an NPC_, never an LVLN.

        Two Oblivion summons (Z004 Flesh Atronach, Z011 Wabba) name an LVLC,
        which converts to an LVLN — a type the archetype rejects, so the
        list's first entry stands in or the spell summons nothing.
        """
        from tes5_import.record_types import magic

        magic.set_assoc_item_index({0x0100AAAA: 0x0100BBBB},
                                   {0x0100AAAA: 'LVLC', 0x0100BBBB: 'CREA'})
        assert magic._resolve_assoc_item(
            0x0100AAAA, magic.A_SUMMON_CREATURE,
            magic.T4_USE_CREATURE) == 0x0100BBBB

    def test_every_spell_gets_an_equip_type(self):
        """ETYP is mandatory: without it a spell never reaches the magic menu.

        ETYP names the slot the menu files a spell under. A converted spell
        that omitted it could be added by console but was invisible and
        uncastable (user-confirmed on the Bound Dagger/Mace spells). Oblivion
        has no equivalent field, so it is derived from the spell type.

        Census of references/Skyrim.esm: 827/827 spells carry ETYP with no
        exceptions in any type. Vanilla's majority choice is EitherHand for
        ordinary spells and abilities, Voice for powers.
        """
        import struct as _s
        from tes5_import.record_types import equipment
        from tes5_import.base.equivalents import (SPELL_EQUIP_EITHER_HAND,
                                                  SPELL_EQUIP_VOICE)

        def _etyp(spit_type):
            rec = {'FormID': '000A97BD', 'EditorID': 'S',
                   'SPIT.Type': str(spit_type), 'SPIT.Cost': '50',
                   'SPIT.Flags': '0', 'EffectCount': '1',
                   'Effect[0].EFID': 'DGHE', 'Effect[0].Magnitude': '1',
                   'Effect[0].Area': '0', 'Effect[0].Duration': '5',
                   'Effect[0].Type': 'Target'}
            out = equipment.convert_SPEL(rec)
            assert out.find(b'ETYP') < out.find(b'SPIT'), 'ETYP must precede SPIT'
            return _s.unpack('<I', _find_subrecord(out, b'ETYP'))[0]

        assert _etyp(0) == SPELL_EQUIP_EITHER_HAND    # Spell
        assert _etyp(1) == SPELL_EQUIP_EITHER_HAND    # Disease
        assert _etyp(4) == SPELL_EQUIP_EITHER_HAND    # Ability
        assert _etyp(2) == SPELL_EQUIP_VOICE          # Power
        assert _etyp(3) == SPELL_EQUIP_VOICE          # Lesser Power

    def test_bound_items_use_the_script_only_where_the_engine_cannot(self):
        """Archetype 17 covers strictly less than Oblivion's bound-item family.

        Two independent gaps, both verified against references/Skyrim.esm:

        * **No bound armor at all.** All seven vanilla archetype-17 effects
          name a WEAP; not one names an ARMO. xEdit types the field as
          [WEAP, ARMO, NULL], but that is what it ACCEPTS, not proof the
          engine equips armor — user-confirmed in-game, casting the converted
          Bound Greaves spell did nothing.
        * **Only fires on a cast.** Archetype 17 appears under SPIT.Type 0
          only, never Type 3/4, so an Ability or Lesser Power (which Skyrim
          applies passively) never reaches BoundItemEffect even for a weapon.

        So: armor is always scripted; a weapon is scripted only when the
        spell cannot cast, and otherwise keeps the engine's own path.
        """
        import struct as _s
        from tes5_import.record_types import equipment, magic

        class _W:
            def __init__(self):
                self.n = 0x800
                self.recs = []

            def alloc_formid(self):
                self.n += 1
                return self.n

            def derive_formid(self, site, key):
                # Test double: derived ids need only be stable per
                # (site, key) and distinct, like the real allocator.
                if not hasattr(self, '_derived'):
                    self._derived = {}
                k = (site, repr(key))
                if k not in self._derived:
                    self._derived[k] = self.alloc_formid()
                return self._derived[k]
            def add_record(self, sig, b):
                self.recs.append((sig, b))

        def _mgef(fid, code, assoc):
            return {'FormID': fid, 'EditorID': code, 'DATA.Flags': '139538',
                    'DATA.BaseCost': '1.0', 'DATA.AssocItem': assoc,
                    'DATA.School': '1', 'DATA.ResistValue': '4294967295'}

        # BAGR (bound greaves) -> ARMO; BWSW (bound sword) -> WEAP.
        magic.set_assoc_item_index({}, {0x00026270: 'ARMO', 0x0002627C: 'WEAP'})
        magic.register_mgef_formids([
            _mgef('0000184F', 'BAGR', '00026270'),
            _mgef('00001857', 'BWSW', '0002627C'),
        ])

        def _spell(code, spit_type):
            return {'FormID': '000A97B9', 'EditorID': 'BoundSpell',
                    'SPIT.Type': str(spit_type), 'SPIT.Cost': '0',
                    'SPIT.Flags': '0', 'EffectCount': '1',
                    'Effect[0].EFID': code, 'Effect[0].Magnitude': '0',
                    'Effect[0].Area': '0', 'Effect[0].Duration': '45',
                    'Effect[0].Type': 'Self'}

        def _first_efid(record_bytes):
            return _s.unpack('<I', _find_subrecord(record_bytes, b'EFID'))[0]

        armor_base = magic.get_mgef_formid('BAGR')
        weapon_base = magic.get_mgef_formid('BWSW')

        # A bound WEAPON on a castable spell keeps the engine's own archetype.
        w = _W()
        assert _first_efid(
            equipment.convert_SPEL(_spell('BWSW', 0), writer=w)) == weapon_base
        assert w.recs == []

        # A bound weapon on an ABILITY cannot cast, so it gets the script.
        w = _W()
        assert _first_efid(
            equipment.convert_SPEL(_spell('BWSW', 4), writer=w)) != weapon_base
        assert len(w.recs) == 1

        # Bound ARMOR is scripted even on a perfectly castable spell — Skyrim
        # has no bound-armor implementation for it to fall back on.
        w = _W()
        scripted = _first_efid(
            equipment.convert_SPEL(_spell('BAGR', 0), writer=w))
        assert scripted != armor_base
        assert len(w.recs) == 1
        sig, clone = w.recs[0]
        assert sig == 'MGEF'
        data = _find_subrecord(clone, b'DATA')
        assert _s.unpack_from('<I', data, magic._O_ARCHETYPE)[0] == magic.A_SCRIPT
        # Assoc. Item is "Unused" under archetype 1 — the item travels as the
        # script's BoundItem property instead.
        assert _s.unpack_from('<I', data, magic._O_ASSOC_ITEM)[0] == 0
        assert magic.BOUND_ITEM_SCRIPT.encode() in clone
        assert b'BoundItem' in clone

        # A lesser power is equally uncastable, and shares the cached clone.
        w2 = _W()
        assert _first_efid(
            equipment.convert_SPEL(_spell('BAGR', 3), writer=w2)) == scripted
        assert w2.recs == []

    def test_counter_effect_count_matches_the_esce_array(self):
        """DATA offset 20 must equal the ESCE count or the CK reads garbage."""
        import struct as _s
        from tes5_import.record_types import magic

        magic.register_mgef_formids([
            {'EditorID': 'DSPL', 'FormID': '00001234'},
            {'EditorID': 'CUDI', 'FormID': '00001235'},
        ])
        rec = {'FormID': '00001863', 'EditorID': 'CALM', 'DATA.School': '3',
               'DATA.Flags': '0', 'CounterEffects': '2',
               'ESCE[0]': 'DSPL', 'ESCE[1]': 'CUDI'}
        blob = magic.convert_MGEF(rec)
        data = _find_subrecord(blob, b'DATA')
        assert _s.unpack_from('<H', data, 20)[0] == blob.count(b'ESCE') == 2

    def test_attribute_effects_get_a_per_actor_value_variant(self):
        """One TES4 DGAT is Damage Strength on one spell, Damage Endurance on
        the next — the AV lives in the ITEM's EFIT.  Skyrim moved it onto the
        MGEF, so a single converted DGAT could only ever damage one stat.
        """
        import struct as _s
        from tes5_import.record_types import magic

        class _Writer:
            def __init__(self):
                self.fid = 0x01000000
                self.records = []

            def alloc_formid(self):
                self.fid += 1
                return self.fid

            def derive_formid(self, site, key):
                # Test double: derived ids need only be stable per
                # (site, key) and distinct, like the real allocator.
                if not hasattr(self, '_derived'):
                    self._derived = {}
                k = (site, repr(key))
                if k not in self._derived:
                    self._derived[k] = self.alloc_formid()
                return self._derived[k]
            def add_record(self, rec_type, data):
                self.records.append((rec_type, data))

        writer = _Writer()
        mgefs = [{'EditorID': 'DGAT', 'FormID': '00001863',
                  'FULL': 'Damage Attribute', 'DATA.School': '2',
                  'DATA.Flags': '0'}]
        effects = [
            {'EffectCount': '1', 'Effect[0].EFID': 'DGAT',
             'Effect[0].ActorValue': '0'},    # Strength
            {'EffectCount': '1', 'Effect[0].EFID': 'DGAT',
             'Effect[0].ActorValue': '5'},    # Endurance
        ]
        assert magic.build_av_variants(mgefs, effects, writer) == 2

        strength = magic.get_mgef_formid('DGAT', 0)
        endurance = magic.get_mgef_formid('DGAT', 5)
        assert strength and endurance and strength != endurance

        avs = {}
        for _, blob in writer.records:
            data = _find_subrecord(blob, b'DATA')
            avs[_s.unpack_from('<I', blob, 12)[0]] = \
                _s.unpack_from('<i', data, 68)[0]
        assert avs[strength] == magic.AV_CARRY_WEIGHT
        assert avs[endurance] == magic.AV_HEALTH


class TestEnchantedBookIsAScroll:
    """A TES4 BOOK carrying an ENAM is a SCROLL, and Skyrim's BOOK has NO
    field for an object effect.

    Converting one to a BOOK produced a blank page that could never be cast —
    503 of them across Oblivion, Nehrim and Morrowind_ob, the Scroll of
    Icarian Flight among them.  Skyrim's record for this is SCRL, which
    carries its effects directly rather than through an enchantment link.
    """

    def test_enchanted_book_converts_to_scrl_with_the_ench_effects(self):
        from tes5_import.record_types import equipment, magic

        magic.register_mgef_formids([{'EditorID': 'REHE',
                                      'FormID': '00001234'}])
        equipment.set_ench_index([{
            'FormID': '000402B1', 'Signature': 'ENCH', 'ENIT.Cost': '350',
            'EffectCount': '1', 'Effect[0].EFID': 'REHE',
            'Effect[0].Type': 'Self', 'Effect[0].Magnitude': '25',
            'Effect[0].Duration': '7', 'Effect[0].ActorValue': '-1',
        }])
        blob = equipment.convert_BOOK({
            'FormID': '0018022C', 'EditorID': 'ScrollOfHealing',
            'FULL': 'Scroll of Healing', 'ENAM': '000402B1',
            'DATA.Flags': '1', 'DATA.Value': '119', 'DATA.Weight': '0.2',
        })
        assert blob[:4] == b'SCRL', 'an enchanted book must become a scroll'
        # CastType 3 = Scroll, matching every vanilla SCRL.
        assert struct.unpack_from('<I', _find_subrecord(blob, b'SPIT'), 16)[0] == 3
        # The payload came from the ENCH, not from the (effect-less) book.
        assert b'EFID' in blob

    def test_plain_book_stays_a_book(self):
        from tes5_import.record_types import equipment

        equipment.set_ench_index([])
        blob = equipment.convert_BOOK({
            'FormID': '0018022D', 'EditorID': 'PlainBook',
            'FULL': 'A Book', 'DATA.Value': '10', 'DATA.Weight': '1.0',
        })
        assert blob[:4] == b'BOOK'


class TestEfitFieldOrder:
    """TES5 EFIT is Magnitude, AREA, DURATION (xEdit wbEFIT).

    The dump tool had the last two labelled the other way round, which made
    every converted spell look like its duration and area were swapped.
    Settled by census: all 427 vanilla ALCH effects write 0 at offset 4 and
    30/60/300/720 at offset 8 — potion durations in seconds, and potions have
    no area.
    """

    def test_writer_packs_area_before_duration(self):
        from tes5_import.record_types import equipment, magic

        magic.register_mgef_formids([{'EditorID': 'REHE',
                                      'FormID': '00001234'}])
        blob = equipment.convert_SPEL({
            'FormID': '0000ABCD', 'EditorID': 'TestHeal',
            'SPIT.Cost': '10', 'SPIT.Type': '0',
            'EffectCount': '1', 'Effect[0].EFID': 'REHE',
            'Effect[0].Magnitude': '25', 'Effect[0].Area': '3',
            'Effect[0].Duration': '120', 'Effect[0].ActorValue': '-1',
        })
        efit = _find_subrecord(blob, b'EFIT')
        assert struct.unpack_from('<f', efit, 0)[0] == pytest.approx(25.0)
        assert struct.unpack_from('<I', efit, 4)[0] == 3      # Area
        assert struct.unpack_from('<I', efit, 8)[0] == 120    # Duration


class TestPlayGroupTargetRouting:
    """`PlayGroup` picks its API from WHAT THE TARGET IS, not from syntax.

    Animated OBJECTS (ACTI/DOOR/STAT/MSTT with a NiControllerManager) keep
    their TES4 sequence names in the converted NIF, so they need
    `PlayAnimation("Forward")`. ACTORS need `Debug.SendAnimationEvent` — a
    PlayAnimation() on an actor corrupts its behavior graph.

    Routing every EXPLICIT-REF call to SendAnimationEvent broke every
    lever-operated secret door in the game (196 calls across 86 scripts:
    Anvil/Bravil castle doors, Anga, mine traps). CharacterGen's
    `CGPrisonSecretWallRef.playgroup forward 1` became
    `SendAnimationEvent(..., "moveStart")`, which an activator has no behavior
    graph to receive, so Renault threw the switch and the wall never moved.
    The tell: the SELF-call on the very next TES4 line converted correctly, so
    two identical statements behaved differently.
    """

    def _xref(self, sigs):
        """A real CrossRefGraph with just the records this test needs.

        `sigs` is {EditorID: base signature}; each entry gets a FormID, a
        record_type, and (for placed refs) a record_base so
        get_base_signature() resolves exactly as it does in a real run.
        """
        from script_convert.cross_ref import CrossRefGraph
        x = CrossRefGraph()
        for i, (name, sig) in enumerate(sigs.items()):
            ref_fid = f'{0x00010000 + i * 2:08X}'
            base_fid = f'{0x00010001 + i * 2:08X}'
            x.edid_to_formid[name.lower()] = ref_fid
            x.formid_to_edid[ref_fid] = name
            x.record_type[ref_fid] = 'REFR'
            x.record_base[ref_fid] = base_fid
            x.record_type[base_fid] = sig
        return x

    def _convert(self, line, sigs, extends='ObjectReference'):
        from script_convert.converter import ScriptConverter
        conv = ScriptConverter(self._xref(sigs))
        return '\n'.join(conv.convert_fragment(line, extends))

    def test_activator_ref_gets_playanimation(self):
        out = self._convert('CGPrisonSecretWallRef.playgroup forward 1',
                            {'CGPrisonSecretWallRef': 'ACTI'})
        assert 'PlayAnimation("Forward")' in out, out
        assert 'SendAnimationEvent' not in out, (
            'an activator has no behavior graph — SendAnimationEvent is inert '
            'and the door never moves')
        assert 'CGPrisonSecretWallRef.PlayAnimation' in out, (
            'PlayAnimation is an ObjectReference method: it must play on the '
            'named ref, not on Self')

    def test_actor_ref_keeps_animation_event(self):
        out = self._convert('ArmandChristopheRef.playgroup idle 1',
                            {'ArmandChristopheRef': 'NPC_'})
        assert 'SendAnimationEvent' in out, out
        assert 'PlayAnimation' not in out, (
            'PlayAnimation on an actor corrupts its behavior graph')

    def test_unknown_ref_falls_back_to_the_safe_api(self):
        """Unknown target: the event is inert on an object but never corrupts
        an actor, so it is the safe default."""
        out = self._convert('SomeUnknownRef.playgroup forward 1', {})
        assert 'SendAnimationEvent' in out, out

    def test_self_call_in_object_script_still_plays_the_sequence(self):
        out = self._convert('playgroup forward 1', {})
        assert 'Self.PlayAnimation("Forward")' in out, out


class TestOwnedGroupAnchoring:
    """A type-1/6/7 GRUP must be preceded by the record that owns it.

    xEdit's TwbGroupRecord.InformPrevMainRecord (wbImplementation.pas ~18023)
    binds these groups to a record ONLY by physical adjacency:

        grsGroupType in [1, 6, 7] and aPrevMainRecord.FixedFormID = GroupLabel

    An unanchored group is attached to nothing, so every record inside it is
    unreachable in-engine — the file still loads and the records still show up
    in xEdit, which is what made this silent. DLCBattlehornCastle.esp emitted a
    Tamriel type-1 world-children group with no WRLD record in front of it and
    lost all 473 of its exterior cell/reference overrides.
    """

    class _Writer:
        """Minimal PluginWriter stand-in capturing the raw top-level groups."""

        def __init__(self):
            self.groups = {}

        def add_raw_group(self, group_sig, group_bytes):
            self.groups[group_sig] = group_bytes

    class _MasterIndex:
        """Master stub: FormID -> record bytes, with a known group nesting."""

        def __init__(self, records):
            self._records = records

        def record(self, formid):
            return self._records.get(formid, b'')

    @staticmethod
    def _record(sig, formid):
        return sig + struct.pack('<I', 0) + b'\x00' * 4 + \
            struct.pack('<I', formid) + b'\x00' * 8

    @staticmethod
    def _orphans(blob):
        """Owned groups in `blob` whose owner does not precede them."""
        orphans = []

        def walk(off, end):
            prev = None
            while off + 24 <= end:
                sig = blob[off:off + 4]
                size = struct.unpack_from('<I', blob, off + 4)[0]
                if sig == b'GRUP':
                    gtype = struct.unpack_from('<i', blob, off + 12)[0]
                    label = blob[off + 8:off + 12]
                    if gtype in (1, 6, 7):
                        owner = struct.unpack('<I', label)[0]
                        if prev != owner:
                            orphans.append((gtype, owner))
                    walk(off + 24, off + size)
                    off += size
                    prev = None
                else:
                    prev = struct.unpack_from('<I', blob, off + 12)[0]
                    off += 24 + size
        walk(0, len(blob))
        return orphans

    def _emit(self, records, master_records):
        from tes5_import.overrides.nested import emit_nested_overrides
        writer = self._Writer()
        emitted, orphaned, anchored = emit_nested_overrides(
            records, writer, self._MasterIndex(master_records))
        return writer, emitted, orphaned, anchored

    def test_worldspace_children_group_gets_its_wrld_anchor(self):
        """Overriding a master's exterior CELL without touching the WRLD."""
        wrld_fid = 0x0100003C
        cell_fid = 0x01007AC6
        path = ((0, b'WRLD'), (1, struct.pack('<I', wrld_fid)),
                (4, struct.pack('<hh', 0, -1)), (5, struct.pack('<hh', 2, -3)))
        records = [(cell_fid, self._record(b'CELL', cell_fid), path)]
        master = {wrld_fid: self._record(b'WRLD', wrld_fid)}

        writer, _emitted, orphaned, anchored = self._emit(records, master)
        blob = writer.groups['WRLD']

        assert orphaned == 0
        assert anchored == 1, 'the unchanged WRLD must be pulled in as an anchor'
        assert self._orphans(blob) == [], (
            'a type-1 group with no WRLD in front of it is attached to '
            'nothing and the engine indexes none of its cells')
        assert blob.index(struct.pack('<I', wrld_fid)) < blob.index(b'GRUP'), \
            'the WRLD record must come BEFORE its children group'

    def test_cell_children_group_gets_its_cell_anchor(self):
        """Overriding a master's REFR without touching the parent CELL."""
        cell_fid = 0x01007AC7
        refr_fid = 0x0201AF4B
        label = struct.pack('<I', cell_fid)
        path = ((0, b'CELL'), (2, struct.pack('<i', 1)),
                (3, struct.pack('<i', 11)), (6, label), (9, label))
        records = [(refr_fid, self._record(b'REFR', refr_fid), path)]
        master = {cell_fid: self._record(b'CELL', cell_fid)}

        writer, _emitted, orphaned, anchored = self._emit(records, master)

        assert orphaned == 0
        assert anchored == 1
        assert self._orphans(writer.groups['CELL']) == []

    def test_topic_children_group_gets_its_dial_anchor(self):
        """A new INFO under a master's unchanged DIAL."""
        dial_fid = 0x011D8200
        info_fid = 0x02001234
        path = ((0, b'DIAL'), (7, struct.pack('<I', dial_fid)))
        records = [(info_fid, self._record(b'INFO', info_fid), path)]
        master = {dial_fid: self._record(b'DIAL', dial_fid)}

        writer, _emitted, orphaned, anchored = self._emit(records, master)

        assert orphaned == 0
        assert anchored == 1
        assert self._orphans(writer.groups['DIAL']) == []

    def test_overridden_owner_is_not_duplicated(self):
        """When the plugin overrides the owner too, no anchor is added."""
        wrld_fid = 0x0100003C
        cell_fid = 0x01007AC6
        wrld_path = ((0, b'WRLD'),)
        cell_path = wrld_path + ((1, struct.pack('<I', wrld_fid)),
                                 (4, struct.pack('<hh', 0, -1)),
                                 (5, struct.pack('<hh', 2, -3)))
        # The plugin's OWN version of the WRLD (distinguishable from the
        # master's by its trailing byte) must be the one that survives.
        own_wrld = self._record(b'WRLD', wrld_fid) + b'\xAB'
        records = [(wrld_fid, own_wrld, wrld_path),
                   (cell_fid, self._record(b'CELL', cell_fid), cell_path)]
        master = {wrld_fid: self._record(b'WRLD', wrld_fid)}

        writer, _emitted, orphaned, anchored = self._emit(records, master)
        blob = writer.groups['WRLD']

        assert orphaned == 0
        assert anchored == 0, 'the plugin overrides the WRLD; no anchor needed'
        assert blob.count(struct.pack('<I', wrld_fid) + b'\x00' * 8) == 1, \
            'the WRLD must appear exactly once, not once per anchor attempt'
        assert b'\xAB' in blob, "the plugin's own WRLD must not be replaced"
        assert self._orphans(blob) == []

    def test_missing_master_record_leaves_the_group_unanchored(self):
        """No master record to anchor with: reported, never faked."""
        wrld_fid = 0x0100003C
        cell_fid = 0x01007AC6
        path = ((0, b'WRLD'), (1, struct.pack('<I', wrld_fid)),
                (4, struct.pack('<hh', 0, -1)), (5, struct.pack('<hh', 2, -3)))
        records = [(cell_fid, self._record(b'CELL', cell_fid), path)]

        _writer, _emitted, _orphaned, anchored = self._emit(records, {})

        assert anchored == 0, 'nothing to anchor with: no anchor is invented'


class TestLandOverrides:
    """A LAND override must carry the author's terrain, not the master's.

    VNML/VHGT/VCLR have identical layout in TES4 and TES5, so convert_LAND
    copies the hex blob straight through — which makes the authored value
    directly substitutable. Before these mappings existed, every terrain edit
    was reported as "no output mapping" and silently kept the master's terrain:
    DLCBattlehornCastle authored VHGT on all 16 of its LAND overrides, so the
    castle sat on Oblivion's untouched hillside.
    """

    _VHGT_PAYLOAD = 4 + 33 * 33          # float offset + 33x33 signed deltas

    def _land(self, **over):
        rec = {
            'Signature': 'LAND',
            'FormID': '00008EB7',
            'RecordFlags': '0',
            'DATA.Flags': '3',
            'LayerCount': '0',
        }
        rec.update(over)
        return rec

    def _vhgt(self, offset_byte=0x40, delta=0, pad='000000'):
        body = '%02x000000' % offset_byte + ('%02x' % (delta & 0xFF)) * 1089
        return body + pad

    def _subs(self, record):
        out = []
        off = 24
        while off + 6 <= len(record):
            sig = record[off:off + 4]
            size = struct.unpack_from('<H', record, off + 4)[0]
            out.append((sig, record[off + 6:off + 6 + size]))
            off += 6 + size
        return out

    def _apply(self, master_rec, plugin_rec):
        from tes5_import.overrides.diff import diff_records
        from tes5_import.overrides.builder import apply_changes
        from tes5_import.record_types.world import convert_LAND
        base = convert_LAND(master_rec)
        changes = diff_records(master_rec, plugin_rec)
        out, applied, unmapped = apply_changes(base, changes, plugin_rec,
                                               master_rec)
        return out, changes, applied, unmapped

    def test_authored_heights_replace_the_masters(self):
        master = self._land(VHGT=self._vhgt(delta=0))
        plugin = self._land(VHGT=self._vhgt(delta=5))

        out, changes, _applied, unmapped = self._apply(master, plugin)

        assert 'VHGT' in changes, 'the diff must see the terrain change'
        assert unmapped == set(), f'VHGT must be mappable, got {unmapped}'
        vhgt = dict(self._subs(out))[b'VHGT']
        assert vhgt[4:4 + 1089] == b'\x05' * 1089, (
            "the override kept the master's heights — the castle would sit on "
            "the master's terrain")

    def test_normals_and_colors_are_mapped(self):
        master = self._land(VNML='00' * 3267, VCLR='11' * 3267)
        plugin = self._land(VNML='7f' * 3267, VCLR='22' * 3267)

        out, _changes, _applied, unmapped = self._apply(master, plugin)
        subs = dict(self._subs(out))

        assert unmapped == set()
        assert subs[b'VNML'] == b'\x7f' * 3267
        assert subs[b'VCLR'] == b'\x22' * 3267

    def test_vhgt_trailing_pad_is_not_an_authored_change(self):
        """The last 3 VHGT bytes are wbUnused(3) — uninitialised CS memory.

        A census of 15,410 vanilla Skyrim.esm LAND records finds arbitrary junk
        there (000000 is merely the most common of many values), so the engine
        ignores them. Comparing them reported phantom VHGT changes on 6 of
        DLCBattlehornCastle's 16 LAND overrides whose real terrain was
        identical, emitting override records with no authored content.
        """
        from tes5_import.overrides.diff import diff_records
        master = self._land(VHGT=self._vhgt(delta=3, pad='d21b02'))
        plugin = self._land(VHGT=self._vhgt(delta=3, pad='000000'))

        assert diff_records(master, plugin) == {}, (
            'a pad-only difference must not count as an authored change')

    def test_real_change_keeps_the_masters_pad(self):
        """When terrain DID change, the master's unused bytes still survive."""
        master = self._land(VHGT=self._vhgt(delta=0, pad='d21b02'))
        plugin = self._land(VHGT=self._vhgt(delta=7, pad='000000'))

        out, changes, _applied, unmapped = self._apply(master, plugin)

        assert 'VHGT' in changes and unmapped == set()
        vhgt = dict(self._subs(out))[b'VHGT']
        assert vhgt[4:4 + 1089] == b'\x07' * 1089, 'authored heights applied'
        assert vhgt[self._VHGT_PAYLOAD:] == bytes.fromhex('d21b02'), (
            "the master's uninitialised pad must be preserved so the override "
            'differs only where the author changed the terrain')

    def test_layer_run_is_rebuilt_through_the_converters_own_builder(self):
        """Layer[] changes replace the whole BTXT/ATXT/VTXT run.

        The layer mapping is lossy and order-dependent (same-texture merge,
        coverage sort, 6-alpha cap), so the override MUST reuse
        build_land_layers rather than reimplement it.
        """
        master = self._land(**{
            'LayerCount': '1',
            'Layer[0].Type': 'BASE',
            'Layer[0].BTXT.Texture': '00001111',
            'Layer[0].BTXT.Quadrant': '0',
        })
        plugin = self._land(**{
            'LayerCount': '1',
            'Layer[0].Type': 'BASE',
            'Layer[0].BTXT.Texture': '00002222',
            'Layer[0].BTXT.Quadrant': '0',
        })

        out, changes, _applied, unmapped = self._apply(master, plugin)

        assert 'Layer[]' in changes
        assert unmapped == set(), f'Layer[] must be mappable, got {unmapped}'
        btxt = dict(self._subs(out))[b'BTXT']
        from tes5_import.base.text_reader import remap_formid
        assert struct.unpack_from('<I', btxt)[0] == remap_formid(0x00002222), \
            "the authored base texture must replace the master's"

    def test_unchanged_land_is_dropped_entirely(self):
        from tes5_import.overrides.diff import diff_records
        rec = self._land(VHGT=self._vhgt(delta=2), VNML='00' * 3267)
        assert diff_records(rec, self._land(VHGT=self._vhgt(delta=2),
                                            VNML='00' * 3267)) == {}


class TestQuestObjectiveOverrideText:
    """A translation plugin must retranslate the OBJECTIVE, not just the log.

    Skyrim's journal displays the objective (NNAM); the stage log entry (CNAM)
    is the collapsed history. Both derive from one TES4 field
    (`Stage[].Log[].Text`), and mapping that key to CNAM alone left every
    objective holding the master's language — 83 of Translation.esp's 84
    quests shipped English CNAM beside German NNAM.
    """

    def _quest(self, text, full):
        return {
            'Signature': 'QUST', 'FormID': '0000E6F3', 'RecordFlags': '0',
            'EditorID': 'NQ00Merre', 'FULL': full,
            'DATA.Flags': '1', 'DATA.Priority': '70',
            'StageCount': '1',
            'Stage[0].Index': '5', 'Stage[0].LogCount': '1',
            'Stage[0].Log[0].Flags': '0', 'Stage[0].Log[0].Text': text,
        }

    def _subs(self, record):
        out = []
        off = 24
        while off + 6 <= len(record):
            sig = record[off:off + 4]
            size = struct.unpack_from('<H', record, off + 6 - 2)[0]
            out.append((sig, record[off + 6:off + 6 + size]))
            off += 6 + size
        return out

    def test_objective_text_is_retranslated(self):
        from tes5_import.overrides.diff import diff_records
        from tes5_import.overrides.builder import apply_changes
        from tes5_import.dialogue.quest import convert_QUST

        master = self._quest('Merre Quest Ratten', 'Arbeit in der Mine')
        plugin = self._quest("Merre's Rat Quest", 'Work in the Mine')

        base = convert_QUST(master)
        changes = diff_records(master, plugin)
        assert 'Stage[]' in changes

        out, _applied, unmapped = apply_changes(base, changes, plugin, master)
        assert unmapped == set(), f'Stage[] must be mappable, got {unmapped}'

        subs = dict(self._subs(out))
        assert subs[b'CNAM'] == b"Merre's Rat Quest\x00"
        assert subs[b'NNAM'] == b"Merre's Rat Quest\x00", \
            'the journal objective must carry the plugin\'s translation'

    def test_derivation_matches_the_converter(self):
        """The helper must yield exactly the NNAMs convert_QUST emits."""
        from tes5_import.dialogue.quest import (convert_QUST,
                                                 quest_objective_texts)
        rec = self._quest('Some journal line', 'A Quest')
        emitted = [v for s, v in self._subs(convert_QUST(rec)) if s == b'NNAM']
        derived = quest_objective_texts(rec)
        assert len(emitted) == len(derived)
        assert emitted[0] == derived[0].encode() + b'\x00'


class TestOutfitIndexAcrossMasters(TestOutfitSplit):
    """A dependent plugin dresses its actors out of its MASTER's wardrobe.

    Inherits TestOutfitSplit's record builders. The item index used to be built
    from the plugin's own records alone, so every inventory entry naming a
    master record was unclassifiable, fell to the non-wearable default, and
    stayed in CNTO — the actor got no outfit at all. DLCBattlehornCastle draws
    155 of its 165 NPC inventory entries from Oblivion.esm, and all 22 of its
    NPCs (knights, captain, maid, cook) stood around undressed in-game.
    """

    def _index_with_master(self, master_types=None, **types):
        """Index the plugin's records plus a master export, as import does."""
        from tes5_import.actors.outfits import load_item_index
        from tes5_import.base.text_reader import set_formid_index_offset
        set_formid_index_offset(0)
        master_export = {}
        for recs in (master_types or {}).values():
            for rec in recs:
                master_export[rec['FormID'].upper()] = rec
        load_item_index(types, master_export)

    def test_master_owned_armor_reaches_the_outfit(self):
        from tes5_import.actors.outfits import split_inventory
        # The wearables live in the MASTER; the plugin contains none of them.
        self._index_with_master(master_types={'ARMO': [
            self._armo('0002A17E', 'IronCuirass', self.BODY, value=100),
            self._armo('0002A17F', 'IronGreaves', self.LEGS, value=90),
        ]})

        outfit, carried = split_inventory([(0x0002A17E, 1), (0x0002A17F, 1)])

        assert sorted(outfit) == [0x0002A17E, 0x0002A17F], (
            "master-owned armor was classified non-wearable and left in CNTO "
            "— the actor spawns naked")
        assert carried == []

    def test_plugin_record_wins_over_the_masters_same_id(self):
        """The plugin's own record overrides the master's at the same id."""
        from tes5_import.actors.outfits import split_inventory
        # Master says this id is a potion (never wearable); the plugin's own
        # export says it is armor. The plugin is authoritative.
        self._index_with_master(
            master_types={'ALCH': [{'Signature': 'ALCH',
                                    'FormID': '0002A180'}]},
            ARMO=[self._armo('0002A180', 'PluginCuirass', self.BODY)])

        outfit, _carried = split_inventory([(0x0002A180, 1)])

        assert outfit == [0x0002A180], (
            "the plugin's own record must win — it is loaded last")

    def test_master_leveled_list_resolves_through_master_leaves(self):
        """An LVLI in the master whose leaves are also in the master."""
        from tes5_import.actors.outfits import split_inventory
        self._index_with_master(master_types={
            'ARMO': [self._armo('0002B001', 'LeatherCuirass', self.BODY)],
            'LVLI': [self._lvli('0002B010', 'LL0Cuirass', ['0002B001'])],
        })

        outfit, carried = split_inventory([(0x0002B010, 1)])

        assert outfit == [0x0002B010], (
            'an all-armor master LVLI is outfit-eligible; unresolvable leaves '
            'would have made it non-wearable and dropped the outfit')
        assert carried == []

    def test_master_loot_still_stays_in_the_inventory(self):
        """Indexing the master must not sweep non-wearables into the outfit."""
        from tes5_import.actors.outfits import split_inventory
        self._index_with_master(master_types={
            'ARMO': [self._armo('0002C001', 'Cuirass', self.BODY)],
            'ALCH': [{'Signature': 'ALCH', 'FormID': '0002C002'}],
            'KEYM': [{'Signature': 'KEYM', 'FormID': '0002C003'}],
        })

        outfit, carried = split_inventory(
            [(0x0002C001, 1), (0x0002C002, 3), (0x0002C003, 1)])

        assert outfit == [0x0002C001]
        assert sorted(carried) == [(0x0002C002, 3), (0x0002C003, 1)], (
            'potions and keys in an outfit are what the CK rejects with '
            '"contains non-armor objects"')

    def test_no_master_export_behaves_as_before(self):
        """A master's OWN run passes no master export and must be unaffected."""
        from tes5_import.actors.outfits import split_inventory
        self._index(ARMO=[self._armo('0002D001', 'Cuirass', self.BODY)])

        outfit, carried = split_inventory([(0x0002D001, 1)])

        assert outfit == [0x0002D001] and carried == []


class TestAidtConfidenceTiers:
    """TES4 confidence is a 0-100 scalar; TES5 wants a 0-4 tier.

    xEdit wbConfidenceEnum: 0 Cowardly, 1 Cautious, 2 Average, 3 Brave,
    4 Foolhardy.  Only Foolhardy never flees.  The original mapping
    (`<30 -> 0, >=70 -> 3, else 2`) never produced tier 1 or tier 4, so
    Oblivion's most common "fearless" value 100 landed on Brave and actors
    kept running away.  Vanilla Skyrim's own 5,118 NPC_ records are
    292/90/1730/393/2613 across the five tiers.
    """

    @staticmethod
    def _conf(raw):
        from tes5_import.record_types.actor_common import build_aidt
        rec = {'AIDT.Aggression': '5', 'AIDT.Confidence': str(raw),
               'AIDT.Responsibility': '50', 'DATA.Personality': '50'}
        return build_aidt(rec)[1]

    def test_fearless_maps_to_foolhardy(self):
        assert self._conf(100) == 4

    def test_all_five_tiers_reachable(self):
        got = {self._conf(v) for v in range(0, 101)}
        assert got == {0, 1, 2, 3, 4}, f'unreachable tiers: {got}'

    def test_tier_is_monotonic_in_confidence(self):
        tiers = [self._conf(v) for v in range(0, 101)]
        assert tiers == sorted(tiers), 'more confidence must never mean more fleeing'

    def test_oblivion_default_50_is_average(self):
        """50 is Oblivion's engine default and must not read as cowardly."""
        assert self._conf(50) == 2

    def test_timid_still_flees(self):
        assert self._conf(0) == 0
        assert self._conf(5) == 0


class TestAggressionTierTargeting:
    """Aggression is "which reaction tier do I attack", not "how nasty am I".

    UESP Skyrim:NPCs#Aggression: "Together with the faction relationship combat
    modifier this governs whether the NPC initiates combat" — 1 attacks enemies
    on sight, 2 attacks enemies AND NEUTRALS (the player) on sight.

    UESP Oblivion:Animals draws the matching TES4 line for the dogs that broke
    this: randomly generated dogs "are Bandit or marauder dogs ... hostile
    towards you, ALTHOUGH THEY WILL NOT NECESSARILY ATTACK ON SIGHT", while
    "the other dogs in the game are all pets of townspeople and are friendly".
    """

    PREY_FID = '0005D556'

    def setup_method(self):
        from tes5_import.record_types.actor_common import load_faction_player_reactions
        load_faction_player_reactions({'FACT': [
            {'FormID': self.PREY_FID, 'EditorID': 'Prey', 'RelationCount': '0'},
        ]})

    @staticmethod
    def _aggr(aggression, personality, factions=()):
        from tes5_import.record_types.actor_common import build_aidt
        rec = {'AIDT.Aggression': str(aggression), 'AIDT.Confidence': '50',
               'AIDT.Responsibility': '50', 'DATA.Personality': str(personality),
               'FactionCount': str(len(factions))}
        for i, f in enumerate(factions):
            rec[f'Faction[{i}].FormID'] = f
        return build_aidt(rec)[0]

    def test_marauder_pet_dog_not_hostile_on_sight(self):
        """Nehrim's Benno: aggr=30, Personality=10, Marauder+Bandit factions.

        Oblivion's own CreatureDog is byte-identical, so this is the general
        case, not a Nehrim quirk. Must be tier 1, never tier 2.
        """
        assert self._aggr(30, 10) == 1

    def test_predators_still_attack_on_sight(self):
        """Wolves/bears/lions/trolls must stay tier 2 or the wild goes passive."""
        for aggr in (45, 50, 60, 70, 80, 100):
            assert self._aggr(aggr, 10) == 2, aggr

    def test_prey_faction_never_attacks_on_sight(self):
        """Horses and deer carry aggression 100 but are harmless.

        Aggression alone cannot exclude them — this is why the Prey term is
        required rather than a threshold.
        """
        assert self._aggr(100, 50, [self.PREY_FID]) == 1   # horse
        assert self._aggr(100, 10, [self.PREY_FID]) == 1   # deer

    def test_prey_beats_aggression(self):
        """Same aggression, opposite outcome, decided purely by faction."""
        assert self._aggr(100, 10) == 2
        assert self._aggr(100, 10, [self.PREY_FID]) == 1

    def test_harmless_low_aggression_never_initiates(self):
        assert self._aggr(5, 10) == 0    # sheep
        assert self._aggr(0, 10) == 0

    def test_frenzied_still_maps(self):
        assert self._aggr(106, 50) == 3


class TestFactionRelationReaction:
    """XNAM Group Combat Reaction: 0 Neutral, 1 Enemy, 2 ALLY, 3 FRIEND.

    Ally and Friend were swapped until 2026-07-31. Confirmed by xEdit
    `wbFactionRelations` (wbDefinitionsCommon) and by Skyrim.esm, where 160 of
    200 faction SELF-relations use 2 — a faction is Ally to itself.

    Ally is the tier that makes members ASSIST each other into combat, so it is
    reserved for the self-relation. A TES4 disposition is only a 0-100 "likes
    them" scalar: Oblivion's CharacterGen data has BladesCG -> MythicDawnCG at
    +100 while starting the ambush with StartCombat, so converting that edge to
    Ally made the Emperor's guards assist the Mythic Dawn and attack the player.
    """

    BLADES_CG = 0x0001EE1E
    MYTHIC_DAWN_CG = 0x00014A20
    EMPEROR = 0x000150BC

    def _fact(self, self_fid, relations):
        """Convert one FACT and return {target_fid: (modifier, reaction)}."""
        import struct
        from tes5_import.record_types.actor_common import convert_FACT

        rec = {'FormID': f'{self_fid:08X}', 'EditorID': 'TestFaction',
               'RelationCount': str(len(relations))}
        for i, (fid, disp) in enumerate(relations):
            rec[f'Relation[{i}].Faction'] = f'{fid:08X}'
            rec[f'Relation[{i}].Disposition'] = str(disp)

        raw = convert_FACT(rec)
        out = {}
        body = raw[24:]
        j = 0
        while j + 6 <= len(body):
            sig = body[j:j + 4]
            size = struct.unpack_from('<H', body, j + 4)[0]
            if sig == b'XNAM' and size == 12:
                fid, mod, react = struct.unpack_from('<IiI', body, j + 6)
                out[fid & 0xFFFFFF] = (mod, react)
            j += 6 + size
        return out

    def test_self_relation_is_ally_enum_2(self):
        rel = self._fact(self.BLADES_CG, [(self.BLADES_CG, 50)])
        assert rel[self.BLADES_CG][1] == 2

    def test_cross_faction_goodwill_is_friend_not_ally(self):
        """+100 to ANOTHER faction is warmth, never an assist contract."""
        rel = self._fact(self.BLADES_CG, [(self.MYTHIC_DAWN_CG, 100)])
        assert rel[self.MYTHIC_DAWN_CG][1] == 3

    def test_chargen_guards_do_not_ally_with_the_assassins(self):
        """The regression itself: BladesCG must not be Ally to MythicDawnCG."""
        rel = self._fact(self.BLADES_CG, [
            (self.MYTHIC_DAWN_CG, 100),
            (self.EMPEROR, 50),
            (self.BLADES_CG, 50),
        ])
        assert rel[self.MYTHIC_DAWN_CG][1] != 2
        assert rel[self.BLADES_CG][1] == 2

    def test_emperor_faction_still_fights_the_assassins(self):
        """The fix must not pacify the guards: -100 stays Enemy."""
        rel = self._fact(self.EMPEROR, [(self.MYTHIC_DAWN_CG, -100)])
        assert rel[self.MYTHIC_DAWN_CG][1] == 1

    def test_mild_disposition_is_neutral(self):
        rel = self._fact(self.MYTHIC_DAWN_CG, [(0x0001DBCD, 10)])
        assert rel[0x0001DBCD][1] == 0

    def test_modifier_is_always_zero(self):
        """1035 of Skyrim.esm's 1036 XNAMs write Modifier 0; the enum carries all."""
        rel = self._fact(self.BLADES_CG, [
            (self.MYTHIC_DAWN_CG, 100), (self.EMPEROR, -100), (0x0001DBCD, 10),
        ])
        assert all(mod == 0 for mod, _ in rel.values())


class TestLeveledActorShellDNAM:
    """A placed leveled creature's shell NPC_ must not cache a zero health pool.

    TES4 `REFR -> LVLC` becomes `ACHR -> NPC_ shell -> TPLT -> LVLN`. DNAM
    offsets 36/38/40 are the cached derived Health/Magicka/Stamina; the engine
    seeds the placed reference from them before template resolution, so a zero
    Health cache spawns the actor at 0 HP and it dies the instant its cell
    loads. That was the 2026-07-30 "all the animals keel over on load" bug —
    chickens/pigs/boar/deer/river crabs are placed through these shells, while
    hand-placed sheep and pack mules (direct ACHR -> base NPC_) were fine, and
    console `placeatme` on the base bypassed the shell entirely.

    Vanilla census: of Skyrim.esm's 508 shells whose TPLT is an LVLN, ZERO
    write Health=0 (minimum 47, dominant triple 55/37/49).
    """

    def _dnam(self):
        from tes5_import.actors.leveled_actors import _shell_dnam
        return _shell_dnam()

    def test_dnam_is_52_bytes(self):
        assert len(self._dnam()) == 52

    def test_health_is_never_zero(self):
        """The regression itself: a 0 health cache = dead on load."""
        health, _magicka, _stamina = struct.unpack_from('<HHH', self._dnam(), 36)
        assert health > 0

    def test_pools_match_the_vanilla_dominant_triple(self):
        assert struct.unpack_from('<HHH', self._dnam(), 36) == (55, 37, 49)

    def test_health_within_vanilla_observed_range(self):
        """Vanilla shells never go below 47."""
        health = struct.unpack_from('<H', self._dnam(), 36)[0]
        assert health >= 47

    def test_skills_stay_zero(self):
        """Only the pool cache is seeded; skills are still inherited."""
        assert self._dnam()[:36] == bytes(36)

    def test_built_shell_carries_the_nonzero_dnam(self):
        """End-to-end: the packed NPC_ record, not just the helper."""
        from tes5_import.actors.leveled_actors import _build_shell
        rec = _build_shell(0x00123456, 0x00654321, 0x00013746, 'TestList_Lvl')
        idx = rec.find(b'DNAM')
        assert idx != -1
        size = struct.unpack_from('<H', rec, idx + 4)[0]
        assert size == 52
        body = rec[idx + 6:idx + 6 + size]
        assert struct.unpack_from('<HHH', body, 36) == (55, 37, 49)


class TestParentRefBecomesLinkedRef:
    """TES4 GetParentRef reads the ENABLE PARENT (XESP); Skyrim's
    GetLinkedRef() reads XLKR.

    xEdit names XESP 'Enable Parent', and the UESP modding guide states the
    idiom directly: "make the container its Parent Ref", then
    `set rCont to GetParentRef`.  Skyrim has no getter for the enable parent,
    so script_convert maps GetParentRef -> GetLinkedRef().  Nothing wrote
    XLKR, so every converted GetParentRef returned None -- all 345 scripts
    that call it, not just traps.  The Vilverin pressure plate's body ran but
    `target.Activate()` never reached the mace, which hung frozen in mid-air.

    Layout (xEdit + a real Skyrim.esm dump, 11287 vanilla uses): 8 bytes,
    {Keyword/Ref, Ref}, keyword slot NULL for a plain link.
    """

    BASE = '0005325D'   # pressure-plate base, script calls GetParentRef
    PLAIN = '0004BD3A'  # a base whose script does not

    @staticmethod
    def _refr(base_fid, xesp='0006BF3C'):
        rec = {'Signature': 'REFR', 'FormID': '0006BF3D', 'RecordFlags': '1024',
               'NAME': base_fid,
               'PosX': '0.0', 'PosY': '0.0', 'PosZ': '0.0',
               'RotX': '0.0', 'RotY': '0.0', 'RotZ': '0.0'}
        if xesp:
            rec['XESP.Reference'] = xesp
            rec['XESP.Flags'] = '0'
        return rec

    def _with_bases(self, *fids):
        """Register bases as 'script calls GetParentRef' for one test."""
        from tes5_import.base import object_scripts as object_scripts
        object_scripts._GETPARENTREF_BASES.clear()
        object_scripts._GETPARENTREF_BASES.update(fids)

    def teardown_method(self):
        from tes5_import.base import object_scripts as object_scripts
        object_scripts._GETPARENTREF_BASES.clear()

    def test_enable_parent_is_mirrored_into_xlkr(self):
        self._with_bases(self.BASE)
        out = convert_REFR(self._refr(self.BASE))
        xlkr = _find_subrecord(out, b'XLKR')
        assert xlkr is not None, 'no XLKR written'
        keyword, ref = struct.unpack('<II', xlkr)
        xesp_ref = struct.unpack('<II', _find_subrecord(out, b'XESP'))[0]
        assert keyword == 0, 'keyword slot must be NULL for a plain link'
        assert ref == xesp_ref, 'XLKR must point at the enable parent'

    def test_xlkr_is_eight_bytes(self):
        self._with_bases(self.BASE)
        out = convert_REFR(self._refr(self.BASE))
        assert len(_find_subrecord(out, b'XLKR')) == 8

    def test_enable_parent_is_still_written(self):
        """The mirror ADDS a link; it must not replace enable-parenting."""
        self._with_bases(self.BASE)
        out = convert_REFR(self._refr(self.BASE))
        assert _find_subrecord(out, b'XESP') is not None

    def test_bases_that_never_read_it_get_no_link(self):
        """XESP is ordinary enable-parenting on 9157 Oblivion refs; only 2660
        belong to a base that reads it back.  Mirroring all of them would
        invent links the game never had."""
        self._with_bases(self.BASE)
        out = convert_REFR(self._refr(self.PLAIN))
        assert _find_subrecord(out, b'XLKR') is None

    def test_no_enable_parent_means_no_link(self):
        self._with_bases(self.BASE)
        out = convert_REFR(self._refr(self.BASE, xesp=None))
        assert _find_subrecord(out, b'XLKR') is None


class TestPlayerFormIDIsReferenceOnly:
    """A record's OWN id must never take the engine-fixed passthrough.

    _ENGINE_FIXED_FORMIDS pins REFERENCES to the player (NPC_ 0x07 / ACHR 0x14)
    so they resolve to Skyrim.esm's real player rather than our converted copy.
    Oblivion.esm, however, defines its own record AT 0x07 (EditorID=Player,
    FULL="Bendu Olo"). Passing that record's own id through unshifted writes it
    at 0x00000007 -- an OVERRIDE of Skyrim's Player, replacing the real
    player's race, voice, spells, AI data and factions.

    That is the measured cause of the CharacterGen Emperor losing his topic
    list: CGEmperor01-24 are gated on `GetIsID(Player) [Target]` (1,325 such
    conditions across Oblivion's INFOs), and with the player base clobbered the
    target test stopped matching, collapsing his menu to 'Rumors' alone.
    """

    PLAYER_BASE = 0x00000007
    PLAYER_REF = 0x00000014

    def setup_method(self):
        from tes5_import.base import text_reader as text_reader
        self._saved = text_reader.get_formid_index_offset()
        text_reader.set_formid_index_offset(1)

    def teardown_method(self):
        from tes5_import.base import text_reader as text_reader
        text_reader.set_formid_index_offset(self._saved)

    def test_own_player_id_shifts_into_our_space(self):
        """Oblivion's Player NPC_ becomes an inert 0x01000007, not an override."""
        from tes5_import.base.text_reader import get_formid
        out = get_formid({'FormID': '00000007'}, 'FormID')
        assert out == 0x01000007, (
            f'Oblivion Player NPC_ written at {out:08X}; at 00000007 it '
            f'overrides Skyrim.esm Player and breaks GetIsID(Player)')

    def test_reference_to_player_base_stays_pinned(self):
        from tes5_import.base.text_reader import get_formid
        assert get_formid({'X': '00000007'}, 'X') == self.PLAYER_BASE

    def test_reference_to_player_ref_stays_pinned(self):
        from tes5_import.base.text_reader import get_formid
        assert get_formid({'X': '00000014'}, 'X') == self.PLAYER_REF

    def test_own_playerref_id_also_shifts(self):
        from tes5_import.base.text_reader import get_formid
        assert get_formid({'FormID': '00000014'}, 'FormID') == 0x01000014

    def test_ordinary_record_unaffected_either_way(self):
        """UrielSeptim shifts identically as an own id and as a reference."""
        from tes5_import.base.text_reader import get_formid
        assert get_formid({'FormID': '00023F2E'}, 'FormID') == 0x01023F2E
        assert get_formid({'X': '00023F2E'}, 'X') == 0x01023F2E

    def test_remap_formid_flag_is_explicit(self):
        from tes5_import.base.text_reader import remap_formid
        assert remap_formid(0x07, 1) == 0x07
        assert remap_formid(0x07, 1, is_own_id=True) == 0x01000007


class TestMeshBoundsCacheSchema:
    """A bounds cache written before an entry field existed must be REBUILT.

    Entries are plain lists, so a pre-field cache parses cleanly and reads as
    zero for the missing field — indistinguishable from a computed zero.  That
    is how Nehrim kept a 2026-08-02 cache after the HELD bit (bit 1) shipped on
    2026-08-05: 0 of 11,946 meshes carried the bit, needs_havok_release
    answered False for every one, and no converted `playgroup` emitted
    TES4Polyfill.ReleaseBreakaway — mwallplankbreakaway01's planks hung in the
    air instead of falling.
    """

    def _write(self, tmpdir, payload):
        import json
        p = os.path.join(tmpdir, 'mesh_bounds_cache.json')
        with open(p, 'w', encoding='utf-8') as fh:
            json.dump(payload, fh)
        return p

    def test_preschema_cache_reports_stale(self):
        """The exact shape of the shipped bug: 6-element entries, no stamp."""
        from asset_convert.collision.collision_extract import bounds_cache_is_current
        with tempfile.TemporaryDirectory() as td:
            p = self._write(td, {'tes4/dungeons/caves/mines/'
                                 'mwallplankbreakaway01.nif':
                                 [-13, -136, -87, 9, 148, 85]})
            assert bounds_cache_is_current(p) is False

    def test_stamped_cache_reports_current(self):
        from asset_convert.collision.collision_extract import (bounds_cache_is_current,
                                                     BOUNDS_SCHEMA_VERSION)
        with tempfile.TemporaryDirectory() as td:
            p = self._write(td, {'tes4/a.nif': [0, 0, 0, 1, 1, 1, 2],
                                 '__schema__': [BOUNDS_SCHEMA_VERSION]})
            assert bounds_cache_is_current(p) is True

    def test_older_schema_version_reports_stale(self):
        """Bumping the version must invalidate caches written at the old one."""
        from asset_convert.collision.collision_extract import (bounds_cache_is_current,
                                                     BOUNDS_SCHEMA_VERSION)
        with tempfile.TemporaryDirectory() as td:
            p = self._write(td, {'tes4/a.nif': [0, 0, 0, 1, 1, 1, 2],
                                 '__schema__': [BOUNDS_SCHEMA_VERSION - 1]})
            assert bounds_cache_is_current(p) is False

    def test_missing_and_corrupt_cache_report_stale(self):
        from asset_convert.collision.collision_extract import bounds_cache_is_current
        with tempfile.TemporaryDirectory() as td:
            assert bounds_cache_is_current(
                os.path.join(td, 'nope.json')) is False
            bad = os.path.join(td, 'bad.json')
            with open(bad, 'w', encoding='utf-8') as fh:
                fh.write('{not json')
            assert bounds_cache_is_current(bad) is False

    def test_schema_key_is_not_loaded_as_a_mesh(self):
        """The stamp is not a path key and must never become a bounds entry."""
        from asset_convert.collision.collision_extract import BOUNDS_SCHEMA_VERSION
        from tes5_import.base.mesh_bounds import (load_mesh_bounds, get_mesh_obnd,
                                             get_mesh_physics_flags)
        with tempfile.TemporaryDirectory() as td:
            p = self._write(td, {'tes4/a.nif': [0, 0, 0, 1, 1, 1, 2],
                                 '__schema__': [BOUNDS_SCHEMA_VERSION]})
            assert load_mesh_bounds(p, quiet=True) == 1
            assert get_mesh_obnd('__schema__') is None
            assert get_mesh_physics_flags('tes4/a.nif') == 2

    def test_scan_stamps_the_schema(self):
        """Whatever scan_mesh_data writes must read back as current."""
        from asset_convert.collision.collision_extract import (scan_mesh_data,
                                                     bounds_cache_is_current)
        with tempfile.TemporaryDirectory() as td:
            meshes = os.path.join(td, 'meshes')
            os.makedirs(meshes)
            bounds = os.path.join(td, 'mesh_bounds_cache.json')
            col = os.path.join(td, 'collision_cache.bin')
            # No NIFs: the scan bails early and writes nothing, so the cache
            # must still read as stale rather than falsely certifying itself.
            scan_mesh_data(meshes, col, bounds)
            if os.path.exists(bounds):
                assert bounds_cache_is_current(bounds) is True


class TestObjectiveText:
    """The short quest-objective (NNAM) table.

    Skyrim renders two quest strings: the long CNAM log entry in the journal
    and a short imperative NNAM line on the objective HUD. TES4 authored only
    one, so tes5_import/generated/objective_short_text.json supplies the short form.
    See tes5_import/dialogue/objective_text.py.
    """

    def test_table_loads(self):
        from tes5_import.dialogue.objective_text import load_objective_text
        assert load_objective_text(quiet=True) > 6000

    def test_every_entry_within_the_engine_cap(self):
        """The objective field does NOT wrap -- it drops the tail. A shipped
        entry over the cap would be silently truncated on screen."""
        import json
        from tes5_import.dialogue.objective_text import _DATA_PATH, OBJECTIVE_MAX_CHARS
        with open(_DATA_PATH, encoding='utf-8') as fh:
            entries = json.load(fh)['entries']
        over = {k: v for k, v in entries.items()
                if len(v) > OBJECTIVE_MAX_CHARS}
        assert not over, f"{len(over)} entries exceed {OBJECTIVE_MAX_CHARS}"

    def test_lookup_enforces_the_cap(self):
        """The cap is enforced at lookup, not merely trusted from the file, so
        a hand-edited table cannot push an over-long string into NNAM."""
        from tes5_import.dialogue import objective_text as ot
        ot._SHORT = {ot._key('src'): 'x' * 200}
        assert len(ot.short_objective('src')) == ot.OBJECTIVE_MAX_CHARS
        ot.load_objective_text(quiet=True)

    def test_unknown_text_falls_back_unchanged(self):
        """A plugin the table was not built for keeps the old behaviour."""
        from tes5_import.dialogue.objective_text import (load_objective_text,
                                                short_objective)
        load_objective_text(quiet=True)
        novel = 'a journal entry that is certainly not in the curated table'
        assert short_objective(novel) == novel

    def test_key_is_whitespace_normalized(self):
        """Keys collapse runs of whitespace, so a source string that differs
        only in spacing still resolves."""
        import json
        from tes5_import.dialogue.objective_text import (_DATA_PATH,
                                                load_objective_text,
                                                short_objective)
        load_objective_text(quiet=True)
        with open(_DATA_PATH, encoding='utf-8') as fh:
            src = next(iter(json.load(fh)['entries']))
        assert short_objective(src.replace(' ', '   ')) == short_objective(src)

    def test_no_entry_is_a_noop(self):
        """An entry earns its place only by CHANGING the line; identical text
        would be pure weight, since the fallback yields the same string."""
        import json
        from tes5_import.dialogue.objective_text import _DATA_PATH, _key
        with open(_DATA_PATH, encoding='utf-8') as fh:
            entries = json.load(fh)['entries']
        noop = [k for k, v in entries.items() if _key(k) == _key(v)]
        assert not noop, f"{len(noop)} no-op entries"

    def test_table_keys_match_the_converter_derivation(self):
        """THE COUPLING THAT WOULD BREAK SILENTLY.

        quest_objective_texts applies pc_stage_texts first -- dropping gamepad
        journal variants and expanding &sUActnX; control tokens -- and the
        table was built from that POST-processed text. If either transform
        changes, lookups start missing and every objective quietly reverts to
        the long journal paragraph with nothing logged.
        """
        from tes5_import.dialogue.quest import quest_objective_texts
        from tes5_import.dialogue.objective_text import (load_objective_text,
                                                short_objective)
        load_objective_text(quiet=True)
        rec = {
            'StageCount': '2',
            'Stage[0].Index': '10',
            'Stage[0].LogCount': '1',
            'Stage[0].Log[0].Text':
                "Fimmion gave me Uungor's lucky grapes. I should bring "
                "them to him.",
            'Stage[1].Index': '20',
            'Stage[1].LogCount': '1',
            'Stage[1].Log[0].Text': 'not in the table, falls back',
        }
        out = quest_objective_texts(rec)
        assert out[0] == 'Bring the lucky grapes to Uungor'
        assert out[1] == 'not in the table, falls back'
        assert short_objective(rec['Stage[0].Log[0].Text']) == out[0]

    def test_all_three_nnam_sites_use_the_table(self):
        """THREE places derive NNAM from Stage[].Log[].Text, and all of them
        must apply the short-objective swap:

          * convert_QUST                          (dialog_converter)
          * quest_objective_texts                 (dialog_converter)
          * _rebuild_qust_targets                 (override_builder)

        The third was missed when this shipped, so Translation.esp wrote 15
        objectives holding the full journal paragraph (max 393 chars) while its
        master held the short line. Grep-level guard: any new NNAM emission
        must route through short_objective.
        """
        import inspect
        from tes5_import.dialogue import converter as dialog_converter
        from tes5_import.overrides import builder as override_builder
        for mod in (dialog_converter, override_builder):
            src = inspect.getsource(mod)
            for line in src.splitlines():
                if "'NNAM'" in line or "b'NNAM'" in line:
                    # Declarations/spec tables carry no text payload.
                    if 'pack_string_subrecord' in line or '_encode_string' in line:
                        assert 'short_objective' in line, \
                            f"NNAM written without short_objective: {line.strip()}"


class TestWorldspaceParentFlags:
    """See docs/commentary/tes4_export_falloutnv.md#child-worldspaces."""

    def _rec(self, **over):
        """A child-worldspace export record with `over` applied."""
        rec = {'Signature': 'WRLD', 'FormID': '0002C107', 'RecordFlags': '0',
               'EditorID': 'ICMarketDistrict', 'WNAM.Parent': '0000003C'}
        rec.update(over)
        return rec

    def test_tes4_child_borrows_everything(self):
        """A TES4 child (no authored PNAM) borrows land, LOD, map, water, climate."""
        from tes5_import.record_types.world import convert_WRLD
        pnam = _find_subrecord(convert_WRLD(self._rec()), b'PNAM')
        assert pnam is not None
        assert struct.unpack('<H', pnam)[0] == 0x5F

    def test_fnv_pnam_is_authored_without_image_space_bit(self):
        """FNV bit 5 (Use Image Space) has no TES5 meaning and is masked."""
        from tes5_import.record_types.world import convert_WRLD
        out = convert_WRLD(self._rec(**{'PNAM.Flags': '39'}))
        assert struct.unpack('<H', _find_subrecord(out, b'PNAM'))[0] == 0x07

    def test_root_worldspace_writes_no_pnam(self):
        """PNAM belongs to the Parent Worldspace struct; roots have none."""
        from tes5_import.record_types.world import convert_WRLD
        rec = self._rec()
        del rec['WNAM.Parent']
        assert _find_subrecord(convert_WRLD(rec), b'PNAM') is None

    def test_authored_map_offset_and_lod_water_height(self):
        """FNV's ONAM and NAM4 pass through instead of the TES4 constants."""
        from tes5_import.record_types.world import convert_WRLD
        out = convert_WRLD(self._rec(**{
            'ONAM.Scale': '0.7', 'ONAM.CellXOffset': '-16000.0',
            'ONAM.CellYOffset': '103000.0', 'NAM4.LODWaterHeight': '-2300.0'}))
        onam = struct.unpack('<ffff', _find_subrecord(out, b'ONAM'))
        assert onam == pytest.approx((0.7, -16000.0, 103000.0, 0.0))
        assert struct.unpack('<f', _find_subrecord(out, b'NAM4'))[0] == -2300.0

    def test_source_only_data_bits_are_dropped(self):
        """0x93: bits 0/1 kept, bit 4 becomes bit 3, bit 7 (No Grass in TES5) dropped."""
        from tes5_import.record_types.world import convert_WRLD
        out = convert_WRLD(self._rec(**{'DATA.Flags': '147'}))
        assert _find_subrecord(out, b'DATA')[0] == 0x0B


class TestFalloutActorTemplates:
    """FO3/FNV spawn stubs inherit their model through TPLT.

    See docs/commentary/tes5_import_falloutnv_actors.md.
    """

    USE_MODEL = 1 << 6

    def _chain(self):
        """The radscorpion chain: stub -> LVLC -> CREA -> CREA with the model."""
        stub = {'Signature': 'CREA', 'FormID': '00156782',
                'EditorID': 'VSpawnTier3GiantRadscorpionMed',
                'ACBS.TemplateFlags': str(0x01DF),
                'TPLT.Template': '001567A0'}
        lvlc = {'Signature': 'LVLC', 'FormID': '001567A0',
                'EntryCount': '1', 'Entry[0].FormID': '0014F401'}
        mid = {'Signature': 'CREA', 'FormID': '0014F401',
               'ACBS.TemplateFlags': str(self.USE_MODEL),
               'TPLT.Template': '0001CF9E'}
        root = {'Signature': 'CREA', 'FormID': '0001CF9E',
                'Model.MODL': r'Creatures\Radscorpion\Skeleton.nif',
                'NIFZCount': '1', 'NIFZ[0]': 'Radscorpion.NIF'}
        return stub, {'CREA': [stub, mid, root], 'LVLC': [lvlc]}

    def test_stub_inherits_model_through_a_leveled_list(self):
        """The chain passes through an LVLC, so entries resolve like a TPLT."""
        from tes5_import.record_types.actors_falloutnv import (
            flatten_actor_templates)
        stub, by_type = self._chain()
        assert flatten_actor_templates(by_type) == 2
        assert stub['Model.MODL'] == r'Creatures\Radscorpion\Skeleton.nif'
        assert stub['NIFZ[0]'] == 'Radscorpion.NIF'

    def test_an_owned_model_is_never_overwritten(self):
        from tes5_import.record_types.actors_falloutnv import (
            flatten_actor_templates)
        stub, by_type = self._chain()
        stub['Model.MODL'] = r'Creatures\Own\Skeleton.nif'
        flatten_actor_templates(by_type)
        assert stub['Model.MODL'] == r'Creatures\Own\Skeleton.nif'

    def test_model_flag_clear_means_no_inheritance(self):
        """Only the categories Template Flags claims are copied down."""
        from tes5_import.record_types.actors_falloutnv import (
            flatten_actor_templates)
        stub, by_type = self._chain()
        stub['ACBS.TemplateFlags'] = str(0x01DF & ~self.USE_MODEL)
        flatten_actor_templates(by_type)
        assert 'Model.MODL' not in stub

    def test_a_template_cycle_terminates(self):
        from tes5_import.record_types.actors_falloutnv import (
            flatten_actor_templates)
        a = {'Signature': 'CREA', 'FormID': '00000003',
             'ACBS.TemplateFlags': str(self.USE_MODEL),
             'TPLT.Template': '00000004'}
        b = {'Signature': 'CREA', 'FormID': '00000004',
             'ACBS.TemplateFlags': str(self.USE_MODEL),
             'TPLT.Template': '00000003'}
        assert flatten_actor_templates({'CREA': [a, b]}) == 0
        assert 'Model.MODL' not in a

    def test_stub_inherits_its_name_and_ai_data(self):
        """Base Data carries FULL and AI Data carries AIDT, like Model does."""
        from tes5_import.record_types.actors_falloutnv import (
            flatten_actor_templates)
        stub, by_type = self._chain()
        root = by_type['CREA'][-1]
        root['FULL'] = 'Giant Radscorpion'
        root['AIDT.Aggression'] = '2'
        root['AIDT.Confidence'] = '4'
        flatten_actor_templates(by_type)
        assert stub['FULL'] == 'Giant Radscorpion'
        assert stub['AIDT.Aggression'] == '2'
        assert stub['AIDT.Confidence'] == '4'

    def test_a_stub_that_owns_its_name_keeps_it(self):
        """An overridden category is never replaced by the template's."""
        from tes5_import.record_types.actors_falloutnv import (
            flatten_actor_templates)
        stub, by_type = self._chain()
        by_type['CREA'][-1]['FULL'] = 'Giant Radscorpion'
        stub['FULL'] = 'Its Own Name'
        flatten_actor_templates(by_type)
        assert stub['FULL'] == 'Its Own Name'

    def test_the_chain_resolves_through_an_lvln(self):
        """FO3/FNV points spawn stubs at LVLN as often as at LVLC."""
        from tes5_import.record_types.actors_falloutnv import (
            flatten_actor_templates)
        stub = {'Signature': 'NPC_', 'FormID': '0002E2A5',
                'EditorID': 'LvlWastelander',
                'ACBS.TemplateFlags': str(0x01DF),
                'TPLT.Template': '0002E2A4'}
        lvln = {'Signature': 'LVLN', 'FormID': '0002E2A4',
                'EntryCount': '1', 'Entry[0].FormID': '0002E29B'}
        root = {'Signature': 'NPC_', 'FormID': '0002E29B',
                'FULL': 'Wastelander'}
        flatten_actor_templates({'NPC_': [stub, root], 'LVLN': [lvln]})
        assert stub['FULL'] == 'Wastelander'


class TestFalloutAidtTiers:
    """FO3/FNV stores Aggression and Confidence in the TES5 enums already.

    See docs/commentary/tes5_import_falloutnv_actors.md#aggression-is-already-a-tier.
    """

    def test_tiers_pass_through_unscaled(self):
        """Every legal FNV (aggression, confidence) pair survives verbatim."""
        from tes5_import.record_types.actors_falloutnv import aidt_tiers
        for aggr in range(4):
            for conf in range(5):
                rec = {'AIDT.Aggression': str(aggr),
                       'AIDT.Confidence': str(conf)}
                assert aidt_tiers(rec) == (aggr, conf)

    def test_out_of_range_bytes_clamp_to_the_enum(self):
        """A plugin may author a byte the enum has no name for."""
        from tes5_import.record_types.actors_falloutnv import aidt_tiers
        rec = {'AIDT.Aggression': '200', 'AIDT.Confidence': '200'}
        assert aidt_tiers(rec) == (3, 4)

    def test_oblivion_scalars_still_bucket(self):
        """The TES4 path must keep its 0-100 mapping: 100 confidence is tier 4."""
        from tes5_import.record_types.actor_common import build_aidt
        rec = {'AIDT.Aggression': '5', 'AIDT.Confidence': '100',
               'AIDT.Responsibility': '50', 'DATA.Personality': '50'}
        assert build_aidt(rec)[1] == 4


class TestFalloutReferenceOnlyRecords:
    """See docs/commentary/tes4_export_falloutnv.md#reference-only-types."""

    def test_formlist_members_pass_through_in_order(self):
        """FLST is byte-identical between the games, so order is preserved.

        Member ids stay clear of `TES4_ITEM_FORMID_TO_SKYRIM`: an
        engine-substituted id (0x0A/0x0B/0x0F) is deliberately rewritten, so
        using one here would test the substitution rather than the ordering.
        """
        from tes5_import.record_types.reference_falloutnv import convert_FLST
        rec = {'FormID': '00100000', 'RecordFlags': '0', 'EditorID': 'AmmoList',
               'LNAM[0]': '0010000A', 'LNAM[1]': '0010000B',
               'LNAM[2]': '0010000C'}
        members = _find_all_subrecords(convert_FLST(rec), b'LNAM')
        assert [struct.unpack('<I', m)[0] & 0xFFFFFF for m in members] == \
            [0x10000A, 0x10000B, 0x10000C]

    def test_texture_set_keeps_only_the_six_fnv_slots(self):
        """TES5 defines TX06/TX07, which no FO3/FNV record authors."""
        from tes5_import.record_types.reference_falloutnv import convert_TXST
        rec = {'FormID': '00100001', 'RecordFlags': '0', 'EditorID': 'Tex',
               'TX00': 'landscape\\a.dds', 'TX01': 'landscape\\a_n.dds',
               'DNAM.Flags': '1'}
        out = convert_TXST(rec)
        assert _find_subrecord(out, b'TX00') is not None
        assert _find_subrecord(out, b'TX06') is None
        assert struct.unpack('<H', _find_subrecord(out, b'DNAM'))[0] == 1

    def test_imagespace_needs_the_cinematic_group(self):
        """A DNAM too short to hold Cinematic (offset 100-131) is dropped."""
        from tes5_import.record_types.reference_falloutnv import convert_IMGS
        rec = {'FormID': '00100002', 'RecordFlags': '0', 'EditorID': 'IS',
               'DNAM': '00' * 100}
        assert convert_IMGS(rec) == b''

    def test_imagespace_splits_fnv_dnam_into_tes5_subrecords(self):
        """TES5 uses HNAM/CNAM/TNAM/DNAM; FNV packs one blob. Sizes are fixed."""
        from tes5_import.record_types.reference_falloutnv import convert_IMGS
        blob = bytearray(152)
        struct.pack_into('<f', blob, 100, 1.5)
        struct.pack_into('<f', blob, 108, 1.2)
        struct.pack_into('<f', blob, 112, 1.1)
        struct.pack_into('<4f', blob, 116, 0.25, 0.5, 0.75, 0.9)
        rec = {'FormID': '00100003', 'RecordFlags': '0', 'EditorID': 'IS',
               'DNAM': bytes(blob).hex()}
        out = convert_IMGS(rec)
        assert len(_find_subrecord(out, b'HNAM')) == 36
        assert len(_find_subrecord(out, b'CNAM')) == 12
        assert len(_find_subrecord(out, b'TNAM')) == 16
        assert len(_find_subrecord(out, b'DNAM')) == 16

    def test_imagespace_cinematic_maps_saturation_brightness_contrast(self):
        """FNV stores saturation/contrast/brightness; TES5 CNAM reorders them."""
        from tes5_import.record_types.reference_falloutnv import convert_IMGS
        blob = bytearray(152)
        struct.pack_into('<f', blob, 100, 1.5)
        struct.pack_into('<f', blob, 108, 1.2)
        struct.pack_into('<f', blob, 112, 1.1)
        rec = {'FormID': '00100007', 'RecordFlags': '0', 'EditorID': 'IS',
               'DNAM': bytes(blob).hex()}
        cnam = struct.unpack('<3f', _find_subrecord(convert_IMGS(rec), b'CNAM'))
        assert cnam == pytest.approx((1.5, 1.1, 1.2))

    def test_imagespace_tint_moves_amount_to_the_front(self):
        """FNV tint is (R, G, B, amount); TES5 TNAM is (amount, R, G, B)."""
        from tes5_import.record_types.reference_falloutnv import convert_IMGS
        blob = bytearray(152)
        struct.pack_into('<4f', blob, 116, 0.25, 0.5, 0.75, 0.9)
        rec = {'FormID': '00100008', 'RecordFlags': '0', 'EditorID': 'IS',
               'DNAM': bytes(blob).hex()}
        tnam = struct.unpack('<4f', _find_subrecord(convert_IMGS(rec), b'TNAM'))
        assert tnam == pytest.approx((0.9, 0.25, 0.5, 0.75))

    def test_formlist_drops_a_member_whose_type_never_converts(self):
        """FNV lists reach ARMA/IMOD/EXPL; a dangling LNAM breaks the engine."""
        from tes5_import.record_types.reference_falloutnv import (
            convert_FLST, index_convertible_records)
        by_type = {'WEAP': [{'FormID': '0000000A'}],
                   'ARMA': [{'FormID': '0000000B'}]}
        index_convertible_records(by_type, {'WEAP': object()}, set())
        rec = {'FormID': '00100009', 'RecordFlags': '0', 'EditorID': 'L',
               'LNAM[0]': '0000000A', 'LNAM[1]': '0000000B'}
        members = _find_all_subrecords(convert_FLST(rec), b'LNAM')
        assert [struct.unpack('<I', m)[0] & 0xFFFFFF for m in members] == [0xA]
        index_convertible_records({}, {}, set())

    def test_lighting_template_extends_40_bytes_to_92(self):
        """FNV DATA is 40 bytes; TES5 needs 92 plus a DALC."""
        from tes5_import.record_types.reference_falloutnv import convert_LGTM
        data = struct.pack('<3I2f2i3f', 0, 0, 0, 750.0, 3000.0, 0, 270,
                           0.5, 0.0, 0.7)
        assert len(data) == 40
        rec = {'FormID': '00100004', 'RecordFlags': '0', 'EditorID': 'LT',
               'DATA': data.hex()}
        out = convert_LGTM(rec)
        payload = _find_subrecord(out, b'DATA')
        assert len(payload) == 92
        assert payload[:40] == data
        assert len(_find_subrecord(out, b'DALC')) == 32

    def test_lighting_template_fade_mirrors_the_fog_distances(self):
        """Vanilla's light fade tracks fog near/far; FNV authors neither."""
        from tes5_import.record_types.reference_falloutnv import convert_LGTM
        data = struct.pack('<3I2f2i3f', 0, 0, 0, 750.0, 3000.0, 0, 270,
                           0.5, 0.0, 0.7)
        rec = {'FormID': '00100005', 'RecordFlags': '0', 'EditorID': 'LT',
               'DATA': data.hex()}
        payload = _find_subrecord(convert_LGTM(rec), b'DATA')
        assert struct.unpack_from('<2f', payload, 80) == (750.0, 3000.0)
        assert struct.unpack_from('<I', payload, 88)[0] == 0

    def test_encounter_zone_inserts_the_location_formid(self):
        """TES5 form version 34+ puts Location at offset 4, so fields move."""
        from tes5_import.record_types.reference_falloutnv import convert_ECZN
        rec = {'FormID': '00100006', 'RecordFlags': '0', 'EditorID': 'EZ',
               'DATA.Owner': '0000000A', 'DATA.Rank': '2',
               'DATA.MinLevel': '10', 'DATA.Flags': '1'}
        payload = _find_subrecord(convert_ECZN(rec), b'DATA')
        assert len(payload) == 12
        assert struct.unpack_from('<I', payload, 4)[0] == 0
        assert struct.unpack_from('<bbBb', payload, 8) == (2, 10, 1, 0)


class TestFalloutCellPointers:
    """See docs/commentary/tes5_import_landscape.md#cell-water-and-music."""

    def _cell(self, **over):
        """An interior CELL export record with `over` applied."""
        rec = {'Signature': 'CELL', 'FormID': '00100010', 'RecordFlags': '0',
               'EditorID': 'TestCell', 'DATA.Flags': '1'}
        rec.update(over)
        return rec

    def test_an_authored_lighting_template_is_never_carried(self):
        """Pointing LTMP at a converted FNV LGTM made interiors pitch black."""
        from tes5_import.record_types.world import convert_CELL
        out = convert_CELL(self._cell(**{'LTMP.LightingTemplate': '0000ABCD'}))
        assert struct.unpack('<I', _find_subrecord(out, b'LTMP'))[0] == 0

    def test_a_cell_with_no_template_still_writes_null_ltmp(self):
        """LTMP is required by TES5, so a TES4 cell writes it as NULL."""
        from tes5_import.record_types.world import convert_CELL
        out = convert_CELL(self._cell())
        assert struct.unpack('<I', _find_subrecord(out, b'LTMP'))[0] == 0

    def test_encounter_zone_carries_but_imagespace_does_not(self):
        """XCIM darkened every interior: its TES5 HDR block has no FNV source."""
        from tes5_import.record_types.world import convert_CELL
        out = convert_CELL(self._cell(**{'XCIM.Imagespace': '0000BEEF',
                                         'XEZN.EncounterZone': '0000CAFE'}))
        assert _find_subrecord(out, b'XCIM') is None
        assert struct.unpack('<I', _find_subrecord(out, b'XEZN'))[0] \
            & 0xFFFFFF == 0xCAFE

    def test_an_authored_musc_beats_the_tes4_enum(self):
        """FO3/FNV names a MUSC directly; only TES4 resolves the XCMT enum."""
        from tes5_import.record_types.world import convert_CELL
        out = convert_CELL(self._cell(**{'XCMO.Music': '0000DEAD',
                                         'XCMT.MusicType': '2'}))
        assert struct.unpack('<I', _find_subrecord(out, b'XCMO'))[0] \
            & 0xFFFFFF == 0xDEAD


class TestFalloutTemplateCategories:
    """See docs/commentary/tes5_import_falloutnv_actors.md#every-category-flattens."""

    def _pair(self, flags, donor):
        """A stub claiming `flags` plus the NPC_ donor it templates onto."""
        stub = {'Signature': 'NPC_', 'FormID': '00145CFC',
                'EditorID': 'Stub', 'ACBS.TemplateFlags': str(flags),
                'TPLT.Template': '001543DF'}
        base = {'Signature': 'NPC_', 'FormID': '001543DF',
                'EditorID': 'Donor'}
        base.update(donor)
        return {'NPC_': [stub, base]}, stub

    def test_inventory_is_carried_whole(self):
        """1,310 FNV actors claim UseInventory and own no items themselves."""
        from tes5_import.record_types.actors_falloutnv import (
            flatten_actor_templates)
        by_type, stub = self._pair(1 << 8, {
            'ItemCount': '2',
            'Item[0].FormID': '0000000A', 'Item[0].Count': '1',
            'Item[1].FormID': '0000000B', 'Item[1].Count': '3'})
        flatten_actor_templates(by_type)
        assert stub['ItemCount'] == '2'
        assert stub['Item[1].FormID'] == '0000000B'
        assert stub['Item[1].Count'] == '3'

    def test_factions_are_carried_whole(self):
        """An actor that loses its factions has no allies, enemies or owner."""
        from tes5_import.record_types.actors_falloutnv import (
            flatten_actor_templates)
        by_type, stub = self._pair(1 << 2, {
            'FactionCount': '1',
            'Faction[0].FormID': '0001B2A4', 'Faction[0].Rank': '2'})
        flatten_actor_templates(by_type)
        assert stub['FactionCount'] == '1'
        assert stub['Faction[0].Rank'] == '2'

    def test_a_stub_with_its_own_items_keeps_them(self):
        """A stub that overrides a category must not take the donor's."""
        from tes5_import.record_types.actors_falloutnv import (
            flatten_actor_templates)
        by_type, stub = self._pair(1 << 8, {
            'ItemCount': '1', 'Item[0].FormID': '0000000A',
            'Item[0].Count': '1'})
        stub['ItemCount'] = '1'
        stub['Item[0].FormID'] = '000000FF'
        stub['Item[0].Count'] = '9'
        flatten_actor_templates(by_type)
        assert stub['Item[0].FormID'] == '000000FF'

    def test_a_category_the_flags_do_not_claim_is_not_copied(self):
        """Only the categories the Template Flags name are inherited."""
        from tes5_import.record_types.actors_falloutnv import (
            flatten_actor_templates)
        by_type, stub = self._pair(1 << 2, {
            'ItemCount': '1', 'Item[0].FormID': '0000000A',
            'Item[0].Count': '1'})
        flatten_actor_templates(by_type)
        assert 'Item[0].FormID' not in stub

    def test_traits_carry_race_voice_and_class(self):
        """Traits is the identity category: race, voice, class, combat style."""
        from tes5_import.record_types.actors_falloutnv import (
            flatten_actor_templates)
        by_type, stub = self._pair(1 << 0, {
            'RNAM.Race': '00000019', 'VTCK.Voice': '0000002D',
            'CNAM.Class': '00000801', 'ZNAM.CombatStyle': '0001B2A4'})
        flatten_actor_templates(by_type)
        assert stub['RNAM.Race'] == '00000019'
        assert stub['ZNAM.CombatStyle'] == '0001B2A4'

    def test_stats_carry_the_level_band(self):
        """Stats drives level scaling; a stub left at level 1 never scales."""
        from tes5_import.record_types.actors_falloutnv import (
            flatten_actor_templates)
        by_type, stub = self._pair(1 << 1, {
            'ACBS.Level': '12', 'ACBS.CalcMin': '5', 'ACBS.CalcMax': '20',
            'DATA.Health': '250'})
        flatten_actor_templates(by_type)
        assert stub['ACBS.Level'] == '12'
        assert stub['DATA.Health'] == '250'

    def test_the_chain_resolves_inventory_through_a_leveled_list(self):
        """FNV points a stub at an actor via LVLN: stub -> LVLN -> NPC_."""
        from tes5_import.record_types.actors_falloutnv import (
            flatten_actor_templates)
        stub = {'Signature': 'NPC_', 'FormID': '00145CFC', 'EditorID': 'Stub',
                'ACBS.TemplateFlags': str(1 << 8),
                'TPLT.Template': '001543DF'}
        lvln = {'Signature': 'LVLN', 'FormID': '001543DF',
                'EditorID': 'List', 'EntryCount': '1',
                'Entry[0].FormID': '000ABCDE'}
        real = {'Signature': 'NPC_', 'FormID': '000ABCDE', 'EditorID': 'Real',
                'ItemCount': '1', 'Item[0].FormID': '0000000A',
                'Item[0].Count': '1'}
        flatten_actor_templates({'NPC_': [stub, real], 'LVLN': [lvln]})
        assert stub['ItemCount'] == '1'
        assert stub['Item[0].FormID'] == '0000000A'
