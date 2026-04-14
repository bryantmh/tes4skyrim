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


# ---------------------------------------------------------------------------
# Dialogue conversion tests
# ---------------------------------------------------------------------------

from tes5_import.dialog_converter import (
    _convert_ctda,
    _DIAL_SKIP_EDIDS,
    _DIAL_SKIP_TYPES,
    _EDID_TO_SUBTYPE_INT,
    _FUNC_DROP,
    _FUNC_GET_IS_ID,
    BARK_SUBTYPES,
    build_getisid_ctda,
    build_topic_npc_ctdas,
    build_voice_type_ctda,
    build_voice_type_ctdas_for_info,
    collect_topic_npc_fids,
    convert_DIAL,
    convert_INFO,
    convert_QUST,
    DIAL_TYPE_COMBAT,
    DIAL_TYPE_CONVERSATION,
    DIAL_TYPE_DETECTION,
    DIAL_TYPE_MISC,
    DIAL_TYPE_PERSUASION,
    DIAL_TYPE_SERVICE,
    DIAL_TYPE_TOPIC,
    info_has_positive_getisid,
    is_bark_topic,
    make_dlbr,
    make_dlvw,
    should_skip_dial,
)
from tes5_import.text_reader import set_formid_index_offset


class TestDialogueConversion:
    """Tests for dialogue record conversion (DIAL, INFO, QUST, DLBR, DLVW)."""

    # -- CTDA conversion --

    def test_ctda_tes4_to_tes5_size(self):
        """TES4 CTDA (24B) â†’ TES5 CTDA (32B)."""
        tes4_ctda = struct.pack('<B3x I HH II',
                                0x00,        # type: Equal
                                0x3F800000,  # CompValue = 1.0f
                                72, 0,       # GetIsID (func 72)
                                0x00001234,  # Param1
                                0)           # Param2
        result = _convert_ctda(tes4_ctda)
        assert result is not None
        assert len(result) == 32

    def test_ctda_drops_FUNC_DROP(self):
        """TES4-only functions (GetDisposition etc) should return None."""
        for func_idx in _FUNC_DROP:
            tes4_ctda = struct.pack('<B3x I HH II',
                                    0x00, 0x3F800000, func_idx, 0, 0, 0)
            result = _convert_ctda(tes4_ctda)
            assert result is None, f"func {func_idx} should be dropped"

    def test_ctda_use_global_flag(self):
        """CTDA Use Global flag is bit 2 (0x04), not bit 5 (0x20)."""
        set_formid_index_offset(1)  # offset=1 â†’ remap 0x00XXXX to 0x01XXXX
        try:
            # type=0x04 (Equal + UseGlobal): CompValue is a Global FormID
            tes4_ctda = struct.pack('<B3x I HH II',
                                    0x04,        # UseGlobal flag
                                    0x00001234,  # CompValue = Global FormID
                                    58, 0,       # GetStage
                                    0x00005678,  # Param1 = Quest FormID
                                    0)
            result = _convert_ctda(tes4_ctda)
            assert result is not None
            # CompValue should be remapped: 0x00001234 â†’ 0x01001234
            comp = struct.unpack_from('<I', result, 4)[0]
            assert comp == 0x01001234
        finally:
            set_formid_index_offset(0)

    def test_ctda_not_equal_not_treated_as_use_global(self):
        """type=0x20 (NotEqual) must NOT remap CompValue as a FormID."""
        set_formid_index_offset(1)
        try:
            # type=0x20 (NotEqual): CompValue is a float, NOT a FormID
            tes4_ctda = struct.pack('<B3x I HH II',
                                    0x20,        # NotEqual operator
                                    0x3F800000,  # CompValue = 1.0f
                                    72, 0,       # GetIsID
                                    0x00001234, 0)
            result = _convert_ctda(tes4_ctda)
            assert result is not None
            # CompValue should NOT be remapped (it's a float literal)
            comp = struct.unpack_from('<I', result, 4)[0]
            assert comp == 0x3F800000  # unchanged
        finally:
            set_formid_index_offset(0)

    def test_ctda_unknown_field_is_ffffffff(self):
        """TES5 CTDA bytes 28-31 must be 0xFFFFFFFF (vanilla convention)."""
        tes4_ctda = struct.pack('<B3x I HH II', 0, 0x3F800000, 72, 0, 0, 0)
        result = _convert_ctda(tes4_ctda)
        unknown = struct.unpack_from('<I', result, 28)[0]
        assert unknown == 0xFFFFFFFF

    # -- Voice type CTDA building --

    def test_build_voice_type_ctda_or(self):
        """Voice type CTDA with OR flag."""
        ctda = build_voice_type_ctda(0x01234567, is_or=True)
        assert len(ctda) == 32
        type_byte = ctda[0]
        assert type_byte & 0x01  # OR flag
        func_idx = struct.unpack_from('<H', ctda, 8)[0]
        assert func_idx == 426  # GetIsVoiceType
        param1 = struct.unpack_from('<I', ctda, 12)[0]
        assert param1 == 0x01234567
        comp = struct.unpack_from('<I', ctda, 4)[0]
        assert comp == 0x3F800000  # 1.0

    def test_build_voice_type_ctda_and(self):
        """Voice type CTDA without OR flag (last in chain)."""
        ctda = build_voice_type_ctda(0x01234567, is_or=False)
        type_byte = ctda[0]
        assert not (type_byte & 0x01)  # no OR flag

    def test_voice_type_ctdas_generic_info(self):
        """Generic INFO (no GetIsID) gets NO voice type injection.

        Generic lines fire for any NPC - adding GetIsVoiceType for ALL voice
        types would bloat the ESM and block NPCs with unrecognised voice types.
        """
        rec = {'ConditionCount': '0'}
        result = build_voice_type_ctdas_for_info(rec, {})
        assert result == b''

    def test_voice_type_ctdas_npc_specific_info(self):
        """NPC-specific INFO (has GetIsID) gets only that NPC's voice type."""
        npc_fid = 0x01001234
        npc_vtyp = 0x01000099
        npc_to_vtyp = {npc_fid: npc_vtyp}
        # Build condition with GetIsID pointing to the NPC
        raw_ctda = struct.pack('<B3x I HH II',
                               0x00, 0x3F800000,
                               72, 0,  # GetIsID
                               0x00001234, 0)  # NPC FormID (pre-remap)
        set_formid_index_offset(1)
        try:
            rec = {'ConditionCount': '1', 'Condition[0].Raw': raw_ctda.hex()}
            result = build_voice_type_ctdas_for_info(rec, npc_to_vtyp)
            # Should have exactly 1 CTDA (for the NPC's voice type)
            assert len(result) == 6 + 32
            # Extract the VTYP FormID from the single CTDA
            param1 = struct.unpack_from('<I', result, 6 + 12)[0]
            assert param1 == npc_vtyp
        finally:
            set_formid_index_offset(0)

    # -- QUST conversion --

    def test_qust_dnam_formver_zero(self):
        """QUST DNAM must have FormVer=0 (matching all Skyrim.esm quests)."""
        rec = {'FormID': '00012345', 'RecordFlags': '0',
               'EditorID': 'TestQuest', 'FULL': 'Test',
               'DATA.Flags': '1', 'DATA.Priority': '100',
               'StageCount': '0'}
        result = convert_QUST(rec)
        # Find DNAM subrecord
        dnam_data = _find_subrecord(result, b'DNAM')
        assert dnam_data is not None
        assert len(dnam_data) == 12
        formver = dnam_data[3]
        assert formver == 0, f"DNAM FormVer should be 0, got {formver}"

    def test_qust_no_has_dialogue_data_flag(self):
        """QUST DNAM must NOT have HasDialogueData (0x8000) even for dialogue quests."""
        rec = {'FormID': '00012345', 'RecordFlags': '0',
               'EditorID': 'TestDialogueQuest', 'FULL': 'Test',
               'DATA.Flags': '1', 'DATA.Priority': '50',
               'StageCount': '0'}
        result = convert_QUST(rec)
        dnam_data = _find_subrecord(result, b'DNAM')
        flags = struct.unpack_from('<H', dnam_data, 0)[0]
        assert not (flags & 0x8000), "HasDialogueData must NOT be set"
        assert flags & 0x0001, "StartGameEnabled must be set"
        assert flags & 0x0010, "StartsEnabled must be set"

    def test_qust_sge_preserves_priority(self):
        """SGE quest priority is preserved (no capping)."""
        rec = {'FormID': '00012345', 'RecordFlags': '0',
               'EditorID': 'TestQ', 'FULL': 'T',
               'DATA.Flags': '1', 'DATA.Priority': '100',
               'StageCount': '0'}
        result = convert_QUST(rec)
        dnam_data = _find_subrecord(result, b'DNAM')
        priority = dnam_data[2]
        assert priority == 100, f"Priority should be preserved, got {priority}"

    def test_qust_has_next_and_anam(self):
        """QUST must have NEXT (empty marker) and ANAM subrecords."""
        rec = {'FormID': '00012345', 'RecordFlags': '0',
               'EditorID': 'TestQ', 'FULL': 'T',
               'DATA.Flags': '0', 'DATA.Priority': '0',
               'StageCount': '0'}
        result = convert_QUST(rec)
        assert _find_subrecord(result, b'NEXT') is not None
        assert _find_subrecord(result, b'ANAM') is not None

    # -- DIAL conversion --

    def test_dial_has_qnam(self):
        """DIAL must always have QNAM when quest_fid_override is provided."""
        rec = {'FormID': '00012345', 'RecordFlags': '0',
               'EditorID': 'TestTopic', 'QuestCount': '0',
               'DATA.Type': '0'}
        result = convert_DIAL(rec, info_count=5, quest_fid_override=0x01999999)
        qnam = _find_subrecord(result, b'QNAM')
        assert qnam is not None
        assert struct.unpack_from('<I', qnam, 0)[0] == 0x01999999

    def test_dial_bark_no_bnam(self):
        """Bark topics (GREETING) must NOT have BNAM (no branch link)."""
        rec = {'FormID': '00012345', 'RecordFlags': '0',
               'EditorID': 'GREETING', 'QuestCount': '1',
               'Quest[0]': '01000001', 'DATA.Type': '2'}
        result = convert_DIAL(rec, info_count=10)
        bnam = _find_subrecord(result, b'BNAM')
        assert bnam is None, "Bark topics must not have BNAM"

    def test_dial_conversation_has_bnam(self):
        """Conversation topics get BNAM when dlbr_fid is provided."""
        rec = {'FormID': '00012345', 'RecordFlags': '0',
               'EditorID': 'SomeConversation', 'QuestCount': '1',
               'Quest[0]': '01000001', 'DATA.Type': '0'}
        result = convert_DIAL(rec, info_count=3, dlbr_fid=0x01AABBCC)
        bnam = _find_subrecord(result, b'BNAM')
        assert bnam is not None
        assert struct.unpack_from('<I', bnam, 0)[0] == 0x01AABBCC

    def test_dial_greeting_category_misc(self):
        """GREETING topic should have Category=7 (Misc)."""
        rec = {'FormID': '00012345', 'RecordFlags': '0',
               'EditorID': 'GREETING', 'QuestCount': '1',
               'Quest[0]': '01000001', 'DATA.Type': '2'}
        result = convert_DIAL(rec, info_count=5)
        data_sub = _find_subrecord(result, b'DATA')
        assert data_sub is not None
        category = data_sub[1]
        assert category == 7, f"GREETING category should be 7 (Misc), got {category}"

    def test_dial_attack_category_combat(self):
        """Attack topic should have Category=3 (Combat)."""
        rec = {'FormID': '00012345', 'RecordFlags': '0',
               'EditorID': 'Attack', 'QuestCount': '1',
               'Quest[0]': '01000001', 'DATA.Type': '0'}
        result = convert_DIAL(rec, info_count=5)
        data_sub = _find_subrecord(result, b'DATA')
        category = data_sub[1]
        assert category == 3, f"Attack category should be 3 (Combat), got {category}"

    def test_dial_snam_four_bytes(self):
        """SNAM must be exactly 4 bytes (ASCII subtype code)."""
        rec = {'FormID': '00012345', 'RecordFlags': '0',
               'EditorID': 'GREETING', 'QuestCount': '1',
               'Quest[0]': '01000001', 'DATA.Type': '0'}
        result = convert_DIAL(rec, info_count=1)
        snam = _find_subrecord(result, b'SNAM')
        assert snam is not None
        assert len(snam) == 4
        assert snam == b'HELO'

    def test_dial_tifc_matches_info_count(self):
        """TIFC must match the actual child INFO count."""
        rec = {'FormID': '00012345', 'RecordFlags': '0',
               'EditorID': 'TestTopic', 'QuestCount': '1',
               'Quest[0]': '01000001', 'DATA.Type': '0'}
        result = convert_DIAL(rec, info_count=42)
        tifc = _find_subrecord(result, b'TIFC')
        assert struct.unpack_from('<I', tifc, 0)[0] == 42

    # -- INFO conversion --

    def test_info_enam_flags(self):
        """INFO ENAM must preserve compatible TES4 flags."""
        rec = _make_info_rec(data_flags=0x37)  # all compatible bits
        result = convert_INFO(rec)
        enam = _find_subrecord(result, b'ENAM')
        assert enam is not None
        flags = struct.unpack_from('<H', enam, 0)[0]
        assert flags == 0x37

    def test_info_has_cnam(self):
        """INFO must have CNAM (Favor Level = 0)."""
        rec = _make_info_rec()
        result = convert_INFO(rec)
        cnam = _find_subrecord(result, b'CNAM')
        assert cnam is not None
        assert cnam[0] == 0

    def test_info_voice_ctdas_injected_before_tes4_conditions(self):
        """Voice type CTDAs must appear BEFORE TES4-converted conditions."""
        # Build a TES4 condition (GetIsRace)
        tes4_ctda = struct.pack('<B3x I HH II', 0, 0x3F800000, 66, 0, 0, 0)
        rec = _make_info_rec(conditions=[(tes4_ctda.hex(),)])
        vt_ctda = pack_subrecord('CTDA', build_voice_type_ctda(0x01000001))
        result = convert_INFO(rec, voice_type_ctdas=vt_ctda)
        # Find all CTDA subrecords
        ctdas = _find_all_subrecords(result, b'CTDA')
        assert len(ctdas) >= 2
        # First CTDA should be voice type (func 426)
        func0 = struct.unpack_from('<H', ctdas[0], 8)[0]
        assert func0 == 426, "First CTDA should be GetIsVoiceType"
        # Last CTDA should be TES4 condition (func 66)
        func_last = struct.unpack_from('<H', ctdas[-1], 8)[0]
        assert func_last == 66

    def test_info_bark_strips_quest_conditions(self):
        """Bark INFOs strip quest-dependent conditions (56, 58, 59, 99)
        since TES4 quests won't be running in TES5."""
        conditions = []
        for func in [56, 58, 59, 99]:  # Quest-dependent functions
            raw = struct.pack('<B3x I HH II', 0, 0x3F800000, func, 0, 0x1234, 0)
            conditions.append((raw.hex(),))
        # Add one non-quest condition (GetDead)
        non_quest = struct.pack('<B3x I HH II', 0, 0x3F800000, 77, 0, 0, 0)
        conditions.append((non_quest.hex(),))
        rec = _make_info_rec(conditions=conditions)
        result = convert_INFO(rec, is_bark=True)
        ctdas = _find_all_subrecords(result, b'CTDA')
        funcs = [struct.unpack_from('<H', c, 8)[0] for c in ctdas]
        # Quest functions should be STRIPPED from bark INFOs
        for func in [56, 58, 59, 99]:
            assert func not in funcs, \
                f"Quest func {func} should be STRIPPED from bark INFOs"
        # Non-quest function should be preserved
        assert 77 in funcs, "Non-quest func 77 should be preserved"

    def test_info_conversation_keeps_quest_conditions(self):
        """Conversation INFOs (is_bark=False) keep quest conditions."""
        raw = struct.pack('<B3x I HH II', 0, 0x3F800000, 58, 0, 0x1234, 0)
        rec = _make_info_rec(conditions=[(raw.hex(),)])
        result = convert_INFO(rec, is_bark=False)
        ctdas = _find_all_subrecords(result, b'CTDA')
        funcs = [struct.unpack_from('<H', c, 8)[0] for c in ctdas]
        assert 58 in funcs, "GetStage should be kept for conversation INFOs"

    def test_info_response_structure(self):
        """INFO responses must have TRDT + NAM1 + NAM2 + NAM3."""
        rec = _make_info_rec(responses=[('Happy dialogue text', 5, 100)])
        result = convert_INFO(rec)
        trdt = _find_subrecord(result, b'TRDT')
        assert trdt is not None
        assert len(trdt) == 24
        # Emotion type
        emotion = struct.unpack_from('<I', trdt, 0)[0]
        assert emotion == 5  # happy
        nam1 = _find_subrecord(result, b'NAM1')
        assert nam1 is not None
        assert b'Happy dialogue text' in nam1
        # NAM2 and NAM3 must always be present
        assert _find_subrecord(result, b'NAM2') is not None
        assert _find_subrecord(result, b'NAM3') is not None

    # -- Bark topic detection --

    def test_is_bark_topic(self):
        """Known bark topic EditorIDs are correctly detected."""
        assert is_bark_topic('GREETING')
        assert is_bark_topic('HELLO')
        assert is_bark_topic('Attack')
        assert is_bark_topic('Hit')
        assert is_bark_topic('Flee')
        assert is_bark_topic('GOODBYE')
        assert is_bark_topic('IdleChatter')
        assert not is_bark_topic('SomeConversation')
        assert not is_bark_topic('MQ01Topic')
        assert not is_bark_topic('')

    def test_is_bark_topic_by_dtype(self):
        """is_bark_topic returns True for combat/detection/misc DATA.Type."""
        assert is_bark_topic('UnknownEdid', dtype=DIAL_TYPE_COMBAT)
        assert is_bark_topic('UnknownEdid', dtype=DIAL_TYPE_DETECTION)
        assert is_bark_topic('UnknownEdid', dtype=DIAL_TYPE_MISC)
        # Topic/Conversation types are NOT bark by dtype alone
        assert not is_bark_topic('UnknownEdid', dtype=DIAL_TYPE_TOPIC)
        assert not is_bark_topic('UnknownEdid', dtype=DIAL_TYPE_CONVERSATION)
        # dtype=-1 (unknown) does not make it a bark
        assert not is_bark_topic('UnknownEdid', dtype=-1)

    def test_is_bark_topic_edid_overrides_dtype(self):
        """Known bark EditorID is bark even if dtype=Topic."""
        assert is_bark_topic('AnswerStatus', dtype=DIAL_TYPE_CONVERSATION)
        assert is_bark_topic('TRANSITION', dtype=DIAL_TYPE_CONVERSATION)
        # INFOGENERAL (Rumors) is intentionally NOT a bark — it's a conversation topic
        assert not is_bark_topic('INFOGENERAL', dtype=DIAL_TYPE_CONVERSATION)

    # -- should_skip_dial --

    def test_should_skip_dial_persuasion(self):
        """Persuasion topics (Type=3) are skipped."""
        rec = {'EditorID': 'ADMIREHATE', 'DATA.Type': '3'}
        assert should_skip_dial(rec)

    def test_should_skip_dial_service(self):
        """Service topics (Type=5) are skipped."""
        rec = {'EditorID': 'BarterBuyItem', 'DATA.Type': '5'}
        assert should_skip_dial(rec)

    def test_should_skip_dial_creature_responses(self):
        """Creature response topics are skipped by EditorID."""
        for edid in ('CreatureResponses', 'SECreatureResponses',
                     'TamrielGateResponses', 'ANY'):
            rec = {'EditorID': edid, 'DATA.Type': '1'}
            assert should_skip_dial(rec), f"{edid} should be skipped"

    def test_should_skip_dial_test_topics(self):
        """Test/debug topics (Test* prefix) are skipped."""
        for edid in ('TestDoggy', 'TestWolf', 'TestDialogue'):
            rec = {'EditorID': edid, 'DATA.Type': '0'}
            assert should_skip_dial(rec), f"{edid} should be skipped"

    def test_should_skip_dial_markn_test(self):
        """MarkNTest* topics are skipped."""
        rec = {'EditorID': 'MarkNTestQuest', 'DATA.Type': '0'}
        assert should_skip_dial(rec)

    def test_should_skip_dial_normal_topic_not_skipped(self):
        """Normal conversation topics are NOT skipped."""
        rec = {'EditorID': 'MQ01Topic', 'DATA.Type': '0'}
        assert not should_skip_dial(rec)

    def test_should_skip_dial_combat_not_skipped(self):
        """Combat topics (Type=2) are NOT skipped - they become barks."""
        rec = {'EditorID': 'Attack', 'DATA.Type': '2'}
        assert not should_skip_dial(rec)

    def test_should_skip_dial_detection_not_skipped(self):
        """Detection topics (Type=4) are NOT skipped - they become barks."""
        rec = {'EditorID': 'Noticed', 'DATA.Type': '4'}
        assert not should_skip_dial(rec)

    # -- _EDID_TO_SUBTYPE_INT expanded mappings --

    def test_edid_subtype_combat_barks(self):
        """Combat-related EditorIDs map to bark subtypes."""
        combat_edids = ['Yield', 'AcceptYield', 'Pickpocket', 'Assault',
                        'Murder', 'PowerAttack', 'AssaultNoCrime',
                        'MurderNoCrime', 'PickpocketNoCrime', 'StealNoCrime',
                        'TrespassNoCrime']
        for edid in combat_edids:
            assert edid in _EDID_TO_SUBTYPE_INT, f"{edid} missing from subtype map"
            assert _EDID_TO_SUBTYPE_INT[edid] in BARK_SUBTYPES, \
                f"{edid} subtype {_EDID_TO_SUBTYPE_INT[edid]} not in BARK_SUBTYPES"

    def test_edid_subtype_service_barks(self):
        """Service-related EditorIDs map to bark subtypes."""
        service_edids = ['BarterBuyItem', 'BarterSellItem', 'BarterExit',
                         'BarterStolen', 'Training', 'RepairExit',
                         'Recharge', 'RechargeExit', 'TrainingExit']
        for edid in service_edids:
            assert edid in _EDID_TO_SUBTYPE_INT, f"{edid} missing from subtype map"
            assert _EDID_TO_SUBTYPE_INT[edid] in BARK_SUBTYPES, \
                f"{edid} subtype {_EDID_TO_SUBTYPE_INT[edid]} not in BARK_SUBTYPES"

    def test_edid_subtype_system_barks(self):
        """System/transition EditorIDs map to Idle bark subtype."""
        for edid in ('AnswerStatus', 'TRANSITION'):
            assert _EDID_TO_SUBTYPE_INT[edid] == 94  # Idle
            assert 94 in BARK_SUBTYPES
        # INFOGENERAL is NOT a bark — it's the Rumors conversation topic
        assert 'INFOGENERAL' not in _EDID_TO_SUBTYPE_INT

    # -- DLBR / DLVW --

    def test_dlbr_structure(self):
        """DLBR must have EDID, QNAM, TNAM, DNAM, SNAM."""
        result = make_dlbr(0x01AABB, 'TestBranch', 0x01CCDD, 0x01EEFF, top_level=True)
        assert result[:4] == b'DLBR'
        assert _find_subrecord(result, b'EDID') is not None
        qnam = _find_subrecord(result, b'QNAM')
        assert struct.unpack_from('<I', qnam, 0)[0] == 0x01CCDD
        snam = _find_subrecord(result, b'SNAM')
        assert struct.unpack_from('<I', snam, 0)[0] == 0x01EEFF
        dnam = _find_subrecord(result, b'DNAM')
        assert struct.unpack_from('<I', dnam, 0)[0] == 1  # Top Level

    def test_dlvw_structure(self):
        """DLVW must have EDID, QNAM, BNAM[], TNAM[], ENAM, DNAM."""
        result = make_dlvw(0x01AABB, 'TestView', 0x01CCDD,
                           [0x01EE01, 0x01EE02], [0x01FF01])
        assert result[:4] == b'DLVW'
        assert _find_subrecord(result, b'QNAM') is not None
        bnam_list = _find_all_subrecords(result, b'BNAM')
        assert len(bnam_list) == 2
        tnam_list = _find_all_subrecords(result, b'TNAM')
        assert len(tnam_list) == 1

    # -- CREA VTCK --

    def test_crea_has_vtck(self):
        """Converted CREA (â†’NPC_) must have VTCK subrecord."""
        from tes5_import.skyrim_overrides import VOICE_TYPE_MAP, set_voice_type
        # Ensure at least one voice type is registered
        set_voice_type('Imperial', 'Male', 0x01AABB01)
        try:
            rec = {'Signature': 'CREA', 'FormID': '00055555', 'RecordFlags': '0',
                   'EditorID': 'TestDeer', 'FULL': 'Deer',
                   'ACBS.Flags': '0', 'ACBS.Level': '5',
                   'ACBS.CalcMin': '1', 'ACBS.CalcMax': '50',
                   'FactionCount': '0', 'ItemCount': '0',
                   'AIDT.Aggression': '0', 'AIDT.Confidence': '50',
                   'AIDT.Services': '0', 'AIPackageCount': '0',
                   'RNAM.Race': '00000000',
                   'DATA.CombatSkill': '30', 'DATA.MagicSkill': '30',
                   'DATA.StealthSkill': '30',
                   'DATA.Health': '50', 'DATA.Intelligence': '50',
                   'DATA.Strength': '50', 'SpellCount': '0'}
            result = convert_CREA(rec)
            vtck = _find_subrecord(result, b'VTCK')
            assert vtck is not None, "CREAâ†’NPC_ must have VTCK"
            vtyp_fid = struct.unpack_from('<I', vtck, 0)[0]
            assert vtyp_fid != 0, "VTCK FormID must not be zero"
        finally:
            # Restore original voice type map entry
            set_voice_type('Imperial', 'Male', 0x01AABB01)

    # -- VMAD injection --

    def test_qust_vmad_injected_when_stage_has_script(self):
        """QUST with stage ResultScript should have VMAD subrecord."""
        rec = {'FormID': '00012345', 'RecordFlags': '0',
               'EditorID': 'TestQuest', 'FULL': 'Test',
               'DATA.Flags': '0', 'DATA.Priority': '0',
               'StageCount': '1',
               'Stage[0].Index': '10',
               'Stage[0].LogCount': '1',
               'Stage[0].Log[0].Flags': '0',
               'Stage[0].Log[0].Text': 'Done',
               'Stage[0].Log[0].ResultScript': 'set x to 1'}
        result = convert_QUST(rec)
        vmad = _find_subrecord(result, b'VMAD')
        assert vmad is not None, "QUST with stage script must have VMAD"
        # Check VMAD structure: version=5, objectFormat=2
        assert struct.unpack_from('<HH', vmad, 0) == (5, 2)
        # Script name should contain the quest EditorID
        assert b'TES4_QF_TestQuest' in vmad

    def test_qust_no_vmad_when_no_scripts(self):
        """QUST without stage ResultScript should NOT have VMAD."""
        rec = {'FormID': '00012345', 'RecordFlags': '0',
               'EditorID': 'TestQuest', 'FULL': 'Test',
               'DATA.Flags': '0', 'DATA.Priority': '0',
               'StageCount': '1',
               'Stage[0].Index': '10',
               'Stage[0].LogCount': '1',
               'Stage[0].Log[0].Flags': '0',
               'Stage[0].Log[0].Text': 'Done'}
        result = convert_QUST(rec)
        vmad = _find_subrecord(result, b'VMAD')
        assert vmad is None, "QUST without scripts must NOT have VMAD"

    def test_info_vmad_injected_when_has_result_script(self):
        """INFO with ResultScript should have VMAD subrecord."""
        rec = {'FormID': '0000ABCD', 'RecordFlags': '0',
               'EditorID': 'TestInfo',
               'DATA.Flags': '0', 'ResponseCount': '0',
               'ConditionCount': '0', 'ChoiceCount': '0',
               'ResultScript': 'player.additem gold001 100'}
        result = convert_INFO(rec)
        vmad = _find_subrecord(result, b'VMAD')
        assert vmad is not None, "INFO with ResultScript must have VMAD"
        assert struct.unpack_from('<HH', vmad, 0) == (5, 2)
        assert b'TES4_TIF__0000ABCD' in vmad

    def test_info_no_vmad_when_no_result_script(self):
        """INFO without ResultScript should NOT have VMAD."""
        rec = {'FormID': '0000ABCD', 'RecordFlags': '0',
               'EditorID': 'TestInfo',
               'DATA.Flags': '0', 'ResponseCount': '0',
               'ConditionCount': '0', 'ChoiceCount': '0'}
        result = convert_INFO(rec)
        vmad = _find_subrecord(result, b'VMAD')
        assert vmad is None, "INFO without ResultScript must NOT have VMAD"

    # -- GetIsID injection (conversation topic NPC restriction) --

    def test_build_getisid_ctda_structure(self):
        """GetIsID CTDA must be 32 bytes with correct function index."""
        ctda = build_getisid_ctda(0x01001234, is_or=False)
        assert len(ctda) == 32
        func_idx = struct.unpack_from('<H', ctda, 8)[0]
        assert func_idx == _FUNC_GET_IS_ID
        param1 = struct.unpack_from('<I', ctda, 12)[0]
        assert param1 == 0x01001234
        # type_byte should have no OR flag
        assert ctda[0] & 0x01 == 0

    def test_build_getisid_ctda_or_flag(self):
        """GetIsID CTDA with is_or=True has OR flag set."""
        ctda = build_getisid_ctda(0x01001234, is_or=True)
        assert ctda[0] & 0x01 == 1

    def test_build_topic_npc_ctdas_or_chain(self):
        """Topic NPC CTDAs form an OR chain: NPC1(OR) | NPC2(AND)."""
        result = build_topic_npc_ctdas({0x01001000, 0x01002000})
        # Should contain 2 CTDA subrecords (each 6 header + 32 data = 38 bytes)
        assert len(result) == 2 * (6 + 32)
        # First CTDA header: 'CTDA' + U16 size(32)
        assert result[:4] == b'CTDA'
        # Parse both CTDAs
        ctda1 = result[6:38]     # first CTDA data
        ctda2 = result[44:76]    # second CTDA data
        # First should have OR flag
        assert ctda1[0] & 0x01 == 1, "First CTDA in chain must have OR flag"
        # Last should NOT have OR flag
        assert ctda2[0] & 0x01 == 0, "Last CTDA in chain must NOT have OR flag"

    def test_build_topic_npc_ctdas_empty(self):
        """Empty NPC set returns empty bytes."""
        assert build_topic_npc_ctdas(set()) == b''

    def test_build_topic_npc_ctdas_single(self):
        """Single NPC gets no OR flag."""
        result = build_topic_npc_ctdas({0x01001234})
        assert len(result) == 6 + 32
        ctda = result[6:38]
        assert ctda[0] & 0x01 == 0, "Single NPC CTDA must NOT have OR flag"

    def test_info_has_positive_getisid_true(self):
        """INFO with GetIsID(X)==1.0 returns True."""
        # Build a raw 24-byte TES4 CTDA: Equal + 1.0 + GetIsID + NPC FormID
        raw = struct.pack('<B3xfHHII', 0x00, 1.0, 72, 0, 0x00001234, 0)
        rec = {'ConditionCount': '1', 'Condition[0].Raw': raw.hex()}
        assert info_has_positive_getisid(rec) is True

    def test_info_has_positive_getisid_negative(self):
        """INFO with GetIsID(X)==0.0 (NOT check) returns False."""
        raw = struct.pack('<B3xfHHII', 0x00, 0.0, 72, 0, 0x00001234, 0)
        rec = {'ConditionCount': '1', 'Condition[0].Raw': raw.hex()}
        assert info_has_positive_getisid(rec) is False

    def test_info_has_positive_getisid_no_conditions(self):
        """INFO with no conditions returns False."""
        rec = {'ConditionCount': '0'}
        assert info_has_positive_getisid(rec) is False

    def test_info_has_positive_getisid_other_func(self):
        """INFO with GetInCell (func 71) returns False."""
        raw = struct.pack('<B3xfHHII', 0x00, 1.0, 71, 0, 0x00001234, 0)
        rec = {'ConditionCount': '1', 'Condition[0].Raw': raw.hex()}
        assert info_has_positive_getisid(rec) is False

    def test_collect_topic_npc_fids_positive_only(self):
        """collect_topic_npc_fids only returns NPCs from positive GetIsID."""
        # INFO with GetIsID(NPC_A)==1.0
        raw_pos = struct.pack('<B3xfHHII', 0x00, 1.0, 72, 0, 0x00001234, 0)
        # INFO with GetIsID(NPC_B)==0.0 (negative)
        raw_neg = struct.pack('<B3xfHHII', 0x00, 0.0, 72, 0, 0x00005678, 0)
        infos = [
            {'ConditionCount': '1', 'Condition[0].Raw': raw_pos.hex()},
            {'ConditionCount': '1', 'Condition[0].Raw': raw_neg.hex()},
        ]
        result = collect_topic_npc_fids(infos, offset=0)
        assert 0x00001234 in result
        assert 0x00005678 not in result

    def test_collect_topic_npc_fids_with_offset(self):
        """collect_topic_npc_fids remaps FormIDs with load-order offset."""
        raw = struct.pack('<B3xfHHII', 0x00, 1.0, 72, 0, 0x00001234, 0)
        infos = [{'ConditionCount': '1', 'Condition[0].Raw': raw.hex()}]
        result = collect_topic_npc_fids(infos, offset=1)
        assert 0x01001234 in result
        assert 0x00001234 not in result

    def test_getisid_injected_into_conditionless_info(self):
        """Conditionless INFO gets GetIsID when topic has NPC-specific siblings."""
        # Build injection CTDAs
        npc_ctdas = build_topic_npc_ctdas({0x01001234})
        rec = {'FormID': '0000ABCD', 'RecordFlags': '0',
               'EditorID': 'TestInfo', 'DATA.Flags': '0',
               'ResponseCount': '0', 'ConditionCount': '0', 'ChoiceCount': '0'}
        result = convert_INFO(rec, voice_type_ctdas=npc_ctdas)
        # Find CTDA subrecords in output
        ctdas = _find_all_subrecords(result, b'CTDA')
        assert len(ctdas) == 1, "Should have exactly 1 injected CTDA"
        func_idx = struct.unpack_from('<H', ctdas[0], 8)[0]
        assert func_idx == _FUNC_GET_IS_ID


# ---------------------------------------------------------------------------
# Reverse-engineering tests (verify against known Skyrim records)
# ---------------------------------------------------------------------------

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


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
