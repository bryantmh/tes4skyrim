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
        # Head (bit 0) â†’ 30 (bit 0) + Hair(1) + LongHair(11) + Circlet(12) + Ears(13)
        # - full-face helm.  LongHair(41) also covered so the hairline headpart
        # (partition 141) is hidden, not just swapped in (see BIPED_SLOT_EXTRA).
        assert _convert_biped_flags(0x01) == (1 | (1 << 1) | (1 << 11) | (1 << 12) | (1 << 13))
        # Hair (bit 1) â†’ 31 (bit 1) + LongHair(11) + Circlet(12) - open-face helm
        assert _convert_biped_flags(0x02) == ((1 << 1) | (1 << 11) | (1 << 12))
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
        # Must be one of the census colors — all dark Redguard tones, never white
        assert (r, g, b) in {(45, 33, 30), (53, 39, 34), (79, 69, 64)}
        assert a == 0
        tinv = struct.unpack('<I', self._get_subrecord_data(result, 'TINV'))[0]
        assert tinv == 100
        tias = struct.unpack('<h', self._get_subrecord_data(result, 'TIAS'))[0]
        assert tias == -1
        # QNAM must agree with the tint (tinv=100 → exactly color/255)
        qnam = struct.unpack('<3f', self._get_subrecord_data(result, 'QNAM'))
        for got, want in zip(qnam, (r / 255.0, g / 255.0, b / 255.0)):
            assert abs(got - want) < 1e-6
        # Female Nord uses the FEMALE tint list index (24, not male 1)
        rec_f = dict(base, **{'RNAM.Race': '000224FD', 'ACBS.Flags': '1'})
        result_f = convert_NPC_(rec_f)
        tini_f = struct.unpack('<H', self._get_subrecord_data(result_f, 'TINI'))[0]
        assert tini_f == 24
        # Deterministic: same FormID → same pick
        assert (self._get_subrecord_data(result_f, 'TINC')
                == self._get_subrecord_data(convert_NPC_(rec_f), 'TINC'))

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
        <model basename>.nif, built by asset_convert/book_inam.py), and books
        sharing the same TES4 model must share one STAT."""
        class _FakeWriter:
            def __init__(self):
                self.records = []
                self._next = 0x01000000
            def alloc_formid(self):
                self._next += 1
                return self._next
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

    def _merchant_npc(self, fid='00000501', services='132227'):
        npc = self._trainer_npc(services=services)
        npc['FormID'] = fid
        npc['EditorID'] = 'TestMerchant'
        return npc

    def test_every_merchant_joins_one_marker_faction(self):
        """The Barter topic gates on ONE faction, so every merchant — whatever
        its service bitmask, chest-backed or not — must be a member of it."""
        from tes5_import.record_types.actors import (
            create_vendor_factions, get_merchant_faction_fid,
            get_vendor_faction_fids_for_actor)
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
        from tes5_import.record_types.actors import (create_vendor_factions,
                                                     get_merchant_faction_fid)
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
        from tes5_import.dialog_conditions import (FUNC_GET_IN_FACTION,
                                                   build_ctda)
        from tes5_import.dialog_converter import _build_service_fallback_info
        from tes5_import.record_types.actors import (create_vendor_factions,
                                                     get_merchant_faction_fid)
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

    def test_greeting_choice_reaches_response_topic(self):
        """A greeting bark whose INFO carries a Choice must keep that TCLT and
        the response topic must get a TOP-LEVEL branch — otherwise the NPC
        greets the player but the player cannot select the response (FGC01Rats:
        Arvena asks what happened in the basement, player can't answer). Also
        verifies a greeting Choice pointing at ANOTHER bark is dropped (it would
        dangle after the bark pass splits/merges topics)."""
        from tes5_import.dialog_converter import build_dialog_groups
        from tes5_import.text_reader import set_formid_index_offset
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
        from tes5_import.dialog_unlocks import build_unlock_plan
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
        from tes5_import.dialog_converter import compute_quest_priorities, convert_QUST
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
        from tes5_import.dialog_converter import (
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

        # No container may sit above the ceiling, so a normally-authored staged
        # quest outranks every container. (Three vanilla staged quests are
        # authored at 0, so this is NOT a universal min(staged) > max(zero) —
        # clamping to that would flatten all 125 containers onto one value.)
        from tes5_import.dialog_converter import ZERO_STAGE_TOP
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
        from tes5_import.dialog_converter import build_dialog_groups
        from tes5_import.text_reader import set_formid_index_offset
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

        # Arbitration lives on QUST.DNAM.Priority — NOT on the topic's PNAM.
        from tes5_import.dialog_converter import compute_quest_priorities
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
        from tes5_import.dialog_converter import build_dialog_groups
        from tes5_import.text_reader import set_formid_index_offset
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

        # Measured on QUST.DNAM.Priority — the byte the engine arbitrates on.
        # (The topics' PNAM all stay at the vanilla 50.0 default, so ordering
        # cannot be read there; see
        # test_zero_stage_quest_never_outranks_staged_greeting.)
        from tes5_import.dialog_converter import compute_quest_priorities
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
        from tes5_import.outfits import load_item_index
        from tes5_import.text_reader import set_formid_index_offset
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
        from tes5_import.outfits import split_inventory
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
        from tes5_import.outfits import split_inventory
        self._index(
            ARMO=[self._armo('00000001', 'Cuirass', self.BODY)],
            KEYM=[{'Signature': 'KEYM', 'FormID': '00000002', 'EditorID': 'Key'}],
        )
        outfit, carried = split_inventory([(1, 1), (2, 1)])
        assert not set(outfit) & {f for f, _ in carried}

    def test_armor_beats_clothing_for_a_contested_slot(self):
        """An NPC issued both armor and clothes was meant to wear the armor."""
        from tes5_import.outfits import split_inventory
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
        from tes5_import.outfits import split_inventory
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
        from tes5_import.outfits import split_inventory
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
        from tes5_import.outfits import is_outfit_eligible, split_inventory
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
        from tes5_import.outfits import is_outfit_eligible
        self._index(
            ARMO=[self._armo('00000010', 'Cuirass', self.BODY)],
            MISC=[{'Signature': 'MISC', 'FormID': '00000011', 'EditorID': 'Gold'}],
            LVLI=[self._lvli('00000001', 'LL0Loot', ['00000010', '00000011'])],
        )
        assert is_outfit_eligible(0x01) is False

    def test_empty_leveled_list_is_not_outfit_eligible(self):
        """An outfit entry that resolves to nothing is the CK's
        'Unable to find valid outfit form'."""
        from tes5_import.outfits import is_outfit_eligible
        self._index(LVLI=[self._lvli('00000001', 'LL0Empty', [])])
        assert is_outfit_eligible(0x01) is False

    def test_jewelry_does_not_contend(self):
        """Rings/amulets never conflict with armor, and an NPC wears two rings."""
        from tes5_import.outfits import split_inventory
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
        from tes5_import.outfits import is_outfit_eligible
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
        from tes5_import.outfits import split_inventory
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
        from tes5_import.outfits import split_inventory
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
        from tes5_import.outfits import split_inventory
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
        from tes5_import.outfits import split_inventory
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
        # PlayerRef 0x14 exists in NO data file (engine-hardcoded, same id in
        # Skyrim) — remapping it to 0x01000014 dangles every package/alias
        # reference to the player. Other low ids (Tamriel 0x3C!) are REAL
        # Oblivion.esm records and must keep remapping.
        from tes5_import.text_reader import set_formid_index_offset
        set_formid_index_offset(1)
        try:
            rec = {'A': '00000014', 'B': '00000100', 'C': '0000003C'}
            assert get_formid(rec, 'A') == 0x14           # PlayerRef stays put
            assert get_formid(rec, 'B') == 0x01000100     # real ids remap
            assert get_formid(rec, 'C') == 0x0100003C     # Tamriel IS remapped
        finally:
            set_formid_index_offset(0)

    def test_null_package_target_is_self(self):
        # A type-0 "Specific Reference" with FormID 0 is the CK's "Unable to
        # find Package Target Reference (00000000)"; vanilla's filler is
        # type 6 = Self.
        from tes5_import.pack_converter import _null_target
        assert struct.unpack('<iIi', _null_target())[0] == 6

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
        # An AIMED enchantment whose effects all map to projectile-less
        # Alch* MGEFs fires NOTHING in game; the converter must synthesize an
        # aimed MGEF clone with a projectile and swap it in.
        from tes5_import import magic_effects
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
        # The old Light/Clothing constants (0x24238/0x24237) were FormIDs
        # that do not exist in Skyrim.esm at all.
        from tes5_import.skyrim_overrides import (
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
        # A city-gate/Oblivion-gate door leads OUT to an exterior; claiming
        # the destination cell poisoned the worldspace's shared persistent
        # dummy cell, giving EVERY persistent ref in Tamriel one gate's
        # location ("Ref is not in its persistence location ..." x13, where
        # the CK then hangs).
        from tes5_import.locations import build_marker_locations
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
        from tes5_import.dialog_conditions import convert_ctda
        out = convert_ctda(self._raw_ctda(), offset=1,
                           run_on_target_ref=0x14)
        assert out is not None
        run_on, reference = struct.unpack_from('<II', out, 20)
        assert run_on == 2          # Reference
        assert reference == 0x14    # PlayerRef
        assert out[0] & 0x02 == 0   # flag bit cleared

    def test_drop_run_on_target(self):
        from tes5_import.dialog_conditions import convert_ctda
        assert convert_ctda(self._raw_ctda(), offset=1,
                            drop_run_on_target=True) is None

    def test_identity_conditions_are_never_retargeted(self):
        """GetIsID/GetIsRace/GetInFaction/... ask WHO is being addressed — only
        the dialogue target can answer, so they must stay RunOn=Target.

        Retargeting them onto a reference changes their meaning, and when that
        reference is the player it makes them UNPASSABLE: GetIsID compares the
        runtime actor's BASE form, and PlayerRef's base is vanilla Skyrim's
        0x00000007, never the converted TES4 player NPC_ 0x01000007. That
        silently killed 667 GREETING/bark INFOs across 101 topics — every
        affected NPC lost their whole topic list because the greeting that
        opens it could not pass (Pinarus Inventius kept only 'rumors').
        """
        import struct
        from tes5_import.dialog_conditions import convert_ctda
        # GetIsRace(69) is excluded here only because its PARAM is race-mapped
        # and this fixture's dummy FormID is not a real TES4 race (it would be
        # dropped for that unrelated reason); the exemption covers it too.
        for func in (72, 70, 68, 71, 73):
            raw = self._raw_ctda(func=func)
            # ...neither the retarget...
            out = convert_ctda(raw, offset=1, run_on_target_ref=0x14)
            assert out is not None, f'func {func} must not be dropped'
            run_on, reference = struct.unpack_from('<II', out, 20)
            assert (run_on, reference) == (1, 0), \
                f'identity func {func} was retargeted to {run_on}/{reference:#x}'
            # ...nor the drop applies to them.
            out = convert_ctda(raw, offset=1, drop_run_on_target=True)
            assert out is not None, f'identity func {func} must not be dropped'
            run_on, _ = struct.unpack_from('<II', out, 20)
            assert run_on == 1

    def test_default_still_target(self):
        import struct
        from tes5_import.dialog_conditions import convert_ctda
        out = convert_ctda(self._raw_ctda(), offset=1)
        run_on, reference = struct.unpack_from('<II', out, 20)
        assert run_on == 1 and reference == 0

    def test_subject_condition_untouched(self):
        import struct
        from tes5_import.dialog_conditions import convert_ctda
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
        from tes5_import.dialog_conditions import convert_ctda
        type_byte = 0x02                              # run-on-target
        raw = struct.pack('<B3xfHHII4x', type_byte, 1.0, 72, 0, 0x00000007, 0)
        out = convert_ctda(raw, offset=1)
        assert out is not None
        param1 = struct.unpack_from('<I', out, 12)[0]
        assert param1 == 0x00000007, \
            f'engine-fixed Player id was remapped to {param1:#010x}'
