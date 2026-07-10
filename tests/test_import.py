"""
Tests for TES5 import - verifies binary output is correctly structured.

Tests the writer, record converters, and group hierarchy.
"""

import os
import struct
import tempfile

import pytest

from tes5_import.record_types.actors import (
    convert_CREA,
    convert_GLOB,
    convert_LVLC,
    convert_LVLI,
    convert_NPC_,
)
from tes5_import.record_types.common import _convert_biped_flags
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
from tes5_import.record_types.world import (
    convert_CELL,
    convert_LAND,
    convert_REFR,
)
from tes5_import.text_reader import (
    get_formid,
    get_int,
    parse_record_block,
    unescape_value,
)
from tes5_import.writer import (
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
        # _convert_biped_flags returns PRIMARY equip slots plus equipment-conflict
        # extras (helmets block Circlet/Ears so they can't be worn simultaneously).
        # Body-coverage extras (ForeArms, Calves, etc.) go on ARMA, not ARMO.
        # Head (bit 0) â†’ 30 (bit 0) + Hair(1) + Circlet(12) + Ears(13) - full-face helm
        assert _convert_biped_flags(0x01) == (1 | (1 << 1) | (1 << 12) | (1 << 13))
        # Hair (bit 1) â†’ 31 (bit 1) + Circlet(12) - open-face helm blocks circlets
        assert _convert_biped_flags(0x02) == ((1 << 1) | (1 << 12))
        # Upper body (bit 2) â†’ 32 (bit 2) - no extra equipment conflicts
        assert _convert_biped_flags(0x04) == 0x04
        # Lower body (bit 3) â†’ 44-LowerBody (bit 14)
        assert _convert_biped_flags(0x08) == (1 << 14)
        # Hand (bit 4) â†’ 33-Hands (bit 3)
        assert _convert_biped_flags(0x10) == (1 << 3)
        # Foot (bit 5) â†’ 37 (bit 7)
        assert _convert_biped_flags(0x20) == 0x80
        # Amulet (bit 8) â†’ 35 (bit 5)
        assert _convert_biped_flags(0x100) == 0x20
        # Shield (bit 13) â†’ 39 (bit 9)
        assert _convert_biped_flags(0x2000) == 0x200
        # Upper+Lower body combined
        assert _convert_biped_flags(0x0C) == 0x04 | (1 << 14)

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
        from tes5_import.writer import PluginWriter
        rec = {'Signature': 'ARMO', 'FormID': '00000300', 'RecordFlags': '0',
               'EditorID': 'IronArmor', 'FULL': 'Iron Armor',
               'BMDT.BipedFlags': '4', 'BMDT.GeneralFlags': '0',
               'DATA.ArmorRating': '20', 'DATA.Value': '100', 'DATA.Weight': '30.0',
               'Male.BipedModel.MODL': 'Armor\\Iron\\M.nif'}
        writer = PluginWriter(masters=['Skyrim.esm'])
        writer.next_object_id = 0x01002000
        result = convert_ARMO(rec, writer=writer)
        # ARMO should have MODL subrecord referencing the ARMA
        assert self._has_subrecord(result, 'MODL')
        modl_data = self._get_subrecord_data(result, 'MODL')
        arma_ref = struct.unpack('<I', modl_data)[0]
        assert arma_ref == 0x01002000
        # Writer should have an ARMA record
        assert 'ARMA' in writer._top_groups
        assert len(writer._top_groups['ARMA']) == 1

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
               'EditorID': 'GameHour', 'FNAM.Type': 'f', 'FLTV.Value': '12.0'}
        result = convert_GLOB(rec)
        self._check_record(result, 'GLOB')
        fnam = self._get_subrecord_data(result, 'FNAM')
        assert fnam == bytes([ord('f')])

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
        # INAM (pickup sound) and CNAM must be present - missing INAM causes BookMenu crash
        assert self._has_subrecord(result, 'INAM'), "BOOK must have INAM (pickup sound)"
        assert self._has_subrecord(result, 'CNAM'), "BOOK must have CNAM"
        inam_data = self._get_subrecord_data(result, 'INAM')
        assert len(inam_data) == 4, "INAM must be a 4-byte FormID"
        inam_fid = struct.unpack_from('<I', inam_data)[0]
        assert inam_fid != 0, "INAM must not be null"

    def test_book_html_font_face_remapped(self):
        """<font face=N> in Oblivion DESC must be remapped to face=3 for Skyrim BookMenu."""
        from tes5_import.record_types.equipment import _fix_book_html
        # Oblivion face=5 (handwriting) and face=1 (decorative) both â†’ face=3
        assert '<font face=3>' in _fix_book_html('<font face=5>text')
        assert '<font face=3>' in _fix_book_html('<FONT face=1>text')
        assert '</font>' in _fix_book_html('text</font>')
        assert 'face=5' not in _fix_book_html('<font face=5>text')

    def test_book_html_img_prefixed(self):
        """IMG src paths must be prefixed with tes4/ for Skyrim texture namespace."""
        from tes5_import.record_types.equipment import _fix_book_html
        result = _fix_book_html('<IMG src="Book/fancy_font/h_62x62.dds" width=62 height=62>')
        assert 'tes4/Book/fancy_font/h_62x62.dds' in result
        # Already-prefixed paths must not be double-prefixed
        result2 = _fix_book_html('<IMG src="tes4/Book/fancy_font/h.dds">')
        assert result2.count('tes4/') == 1

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
        from tes5_import.record_types.actors import convert_GLOB
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
        import mmap
        with open(self.SKYRIM_ESM, 'rb') as f:
            mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            pos = 0
            while pos < len(mm) - 24:
                sig = mm[pos:pos+4]
                if sig == b'GRUP':
                    pos += 24
                    continue
                sz = struct.unpack_from('<I', mm, pos+4)[0]
                rec_fid = struct.unpack_from('<I', mm, pos+12)[0]
                if rec_fid == fid:
                    rec = bytes(mm[pos:pos+24+sz])
                    mm.close()
                    return rec
                pos += 24 + sz
            mm.close()
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

import re
from asset_convert.audio_converter import _VOICE_FILENAME_RE, _TES4_VOICE_TYPE_MAP


class TestVoiceFileNaming:
    """Tests for the voice file regex and naming conventions."""

    def test_regex_captures_prefix(self):
        """Regex must capture quest_topic prefix as group(1)."""
        m = _VOICE_FILENAME_RE.match('arenaannouncer_announcer_0004216e_1.mp3')
        assert m is not None
        assert m.group(1) == 'arenaannouncer_announcer'
        assert m.group(2) == '0004216e'
        assert m.group(3) == '1'
        assert m.group(4) == 'mp3'

    def test_regex_complex_prefix(self):
        """Regex handles multi-underscore prefixes correctly."""
        m = _VOICE_FILENAME_RE.match('ms45_dar_ma_00012345_2.wav')
        assert m is not None
        assert m.group(1) == 'ms45_dar_ma'
        assert m.group(2) == '00012345'
        assert m.group(3) == '2'

    def test_regex_uppercase_formid(self):
        """Regex is case-insensitive for FormID hex digits."""
        m = _VOICE_FILENAME_RE.match('questname_topicname_0004ABCD_1.xwm')
        assert m is not None
        assert m.group(2) == '0004ABCD'

    def test_regex_fuz_extension(self):
        """Regex matches .fuz files."""
        m = _VOICE_FILENAME_RE.match('quest_topic_00001234_1.fuz')
        assert m is not None
        assert m.group(4) == 'fuz'

    def test_regex_rejects_short_formid(self):
        """Regex requires exactly 8 hex chars for FormID."""
        m = _VOICE_FILENAME_RE.match('quest_topic_01234_1.mp3')
        assert m is None

    def test_regex_rejects_no_prefix(self):
        """Regex requires at least one underscore-separated prefix."""
        m = _VOICE_FILENAME_RE.match('00012345_1.mp3')
        assert m is None

    def test_voice_type_map_coverage(self):
        """Voice type map covers all 10 playable races Ã— 2 genders."""
        playable_races = [
            'Argonian', 'Breton', 'DarkElf', 'HighElf', 'Imperial',
            'Khajiit', 'Nord', 'Orc', 'Redguard', 'WoodElf',
        ]
        for race in playable_races:
            for gender in ('M', 'F'):
                assert (race, gender) in _TES4_VOICE_TYPE_MAP, \
                    f"Missing voice type for ({race}, {gender})"

    def test_voice_type_map_sheogorath(self):
        """Sheogorath has Male voice type only."""
        assert ('Sheogorath', 'M') in _TES4_VOICE_TYPE_MAP

    def test_voice_type_naming_convention(self):
        """All voice types follow TES4{Male|Female}Race naming."""
        for (race, gender), vtype in _TES4_VOICE_TYPE_MAP.items():
            sex = 'Male' if gender == 'M' else 'Female'
            assert vtype.startswith(f'TES4{sex}'), \
                f"Voice type {vtype} for ({race}, {gender}) has wrong prefix"


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
    asset_convert/furniture_markers.py; MNAM bit i enables NIF position i, so
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
        """Unresolvable model: single conservative seat, all entry points."""
        from tes5_import.record_types.items import convert_FURN
        rec = self._furn_rec('Clutter\\SEfurniture\\SEChair01.NIF', 0x40000004)
        mnam, fnprs = self._unpack(convert_FURN(rec))
        assert mnam == 0x40000001, hex(mnam)
        assert fnprs == [(1, 0x0F)]

    def test_seat_count_matches_converted_nif(self):
        """The NIF converter must emit exactly the positions MNAM enables."""
        import time
        if not hasattr(time, 'clock'):
            time.clock = time.perf_counter
        from pyffi.formats.nif import NifFormat as NF
        from asset_convert.nif_converter import convert_nif
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
        from tes5_import.record_types.actors import (
            create_trainer_records, create_vendor_factions,
            get_trainer_class_fid, get_trainer_faction_fid)
        from tes5_import.constants import TES5_SKILL_ORDER
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

    def test_trainer_unmappable_skill_skipped(self):
        from tes5_import.record_types.actors import (
            create_trainer_records, get_trainer_class_fid)
        writer = PluginWriter(masters=['Skyrim.esm'])
        # Teaches=1 (Athletics) has no Skyrim skill -> not a trainer
        npc = self._trainer_npc(teaches='1')
        create_trainer_records({'NPC_': [npc], 'CLAS': []}, writer)
        assert get_trainer_class_fid(0x00000500) == 0

    def test_service_menu_kind(self):
        from tes5_import.dialog_converter import (service_menu_kind,
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
        from tes5_import.dialog_converter import convert_INFO
        rec = {'Signature': 'INFO', 'FormID': '00062116', 'RecordFlags': '0',
               'ParentDIAL': '0000010F', 'DATA.Flags': '2',
               'ResponseCount': '1', 'Response[0].EmotionType': '0',
               'Response[0].EmotionValue': '50',
               'Response[0].ResponseNumber': '1',
               'Response[0].ResponseText': 'Take a look.'}
        result = convert_INFO(rec, service_menu='barter')
        assert b'TES4_ShowBarterMenu' in result
        result = convert_INFO(rec, service_menu='training')
        assert b'TES4_ShowTrainingMenu' in result
        # Without a service menu there is no VMAD at all
        assert b'VMAD' not in convert_INFO(rec)

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

        # Every emitted keyword must be tradable at the matching TES4 vendor
        from tes5_import.record_types.actors import _keywords_for_services
        assert VENDOR_KYWD['Arrow'] in _keywords_for_services(1 << 0)
        assert VENDOR_KYWD['Clutter'] in _keywords_for_services(1 << 10)

    def test_barter_topic_dialogue(self):
        """End-to-end: Barter topic converts with prompt, gate and fragment."""
        from tes5_import.dialog_converter import build_dialog_groups
        from tes5_import.record_types.actors import (
            create_trainer_records, create_vendor_factions)
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
        # Both the original line and the synthetic fallback carry the shared
        # barter fragment (the script name appears 3x per VMAD: attached
        # script, fragment FileName, fragment ScriptName)
        assert dial_group.count(b'TES4_ShowBarterMenu') == 6
        assert b'Take a look.' in dial_group


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
