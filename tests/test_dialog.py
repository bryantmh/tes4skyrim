"""
Comprehensive tests for TES4-TES5 dialog/quest conversion.

Tests cover:
- CTDA format conversion (24B-32B)
- Voice type routing (GetIsVoiceType injection)
- Bark vs conversation classification
- DIAL/INFO/QUST/DLBR/DLVW record structure
- Quest condition gating (stages, factions)
- NPC-specific vs generic dialog filtering
- Dialog skip logic (persuasion, service, creature responses)
- End-to-end dialog evaluation simulation
"""

import struct
import pytest

from tes5_import.dialog_converter import (
    _convert_ctda,
    _DIAL_SKIP_EDIDS,
    _DIAL_SKIP_TYPES,
    _EDID_TO_SUBTYPE_INT,
    _FUNC_DROP,
    BARK_SUBTYPES,
    DIAL_TYPE_COMBAT,
    DIAL_TYPE_CONVERSATION,
    DIAL_TYPE_DETECTION,
    DIAL_TYPE_MISC,
    DIAL_TYPE_PERSUASION,
    DIAL_TYPE_SERVICE,
    DIAL_TYPE_TOPIC,
    build_voice_type_ctda,
    build_voice_type_ctdas_for_info,
    convert_DIAL,
    convert_INFO,
    convert_QUST,
    is_bark_topic,
    make_dlbr,
    make_dlvw,
    should_skip_dial,
)
from tes5_import.text_reader import set_formid_index_offset
from tes5_import.writer import pack_subrecord


# ---------------------------------------------------------------------------
# Record parsing helpers
# ---------------------------------------------------------------------------

def _find_subrecord(record_bytes: bytes, sig: bytes) -> bytes | None:
    """Find the first subrecord with the given signature."""
    pos = 24  # skip record header
    while pos < len(record_bytes) - 6:
        sub_sig = record_bytes[pos:pos+4]
        sub_size = struct.unpack_from('<H', record_bytes, pos+4)[0]
        if sub_sig == sig:
            return record_bytes[pos+6:pos+6+sub_size]
        pos += 6 + sub_size
    return None


def _find_all_subrecords(record_bytes: bytes, sig: bytes) -> list[bytes]:
    """Find all subrecords with a given signature."""
    results = []
    pos = 24
    while pos < len(record_bytes) - 6:
        sub_sig = record_bytes[pos:pos+4]
        sub_size = struct.unpack_from('<H', record_bytes, pos+4)[0]
        if sub_sig == sig:
            results.append(record_bytes[pos+6:pos+6+sub_size])
        pos += 6 + sub_size
    return results


def _make_ctda(func_idx, param1=0, param2=0, comp_value=0x3F800000,
               type_byte=0x00, run_on=0):
    """Build a 24-byte TES4 CTDA."""
    return struct.pack('<B3xIHHIII',
                       type_byte, comp_value, func_idx, 0,
                       param1, param2, run_on)


def _make_info_rec(formid='00012345', data_flags=0, conditions=None,
                   responses=None, choices=None, result_script=None,
                   parent_dial='00011111'):
    """Create a minimal INFO test record dict."""
    rec = {
        'Signature': 'INFO', 'FormID': formid, 'RecordFlags': '0',
        'EditorID': f'TestInfo_{formid}',
        'DATA.Flags': str(data_flags),
        'ConditionCount': '0', 'ResponseCount': '0',
        'ChoiceCount': '0', 'ParentDIAL': parent_dial,
    }
    if conditions:
        rec['ConditionCount'] = str(len(conditions))
        for i, raw_bytes in enumerate(conditions):
            rec[f'Condition[{i}].Raw'] = raw_bytes.hex()
    if responses:
        rec['ResponseCount'] = str(len(responses))
        for i, (text, emotion, value) in enumerate(responses):
            rec[f'Response[{i}].ResponseText'] = text
            rec[f'Response[{i}].EmotionType'] = str(emotion)
            rec[f'Response[{i}].EmotionValue'] = str(value)
            rec[f'Response[{i}].ResponseNumber'] = str(i + 1)
    if choices:
        rec['ChoiceCount'] = str(len(choices))
        for i, cfid in enumerate(choices):
            rec[f'Choice[{i}]'] = f'{cfid:08X}'
    if result_script:
        rec['ResultScript'] = result_script
    return rec


def _make_dial_rec(formid='00011111', edid='TestTopic', dtype=0,
                   quest_fid=None):
    """Create a minimal DIAL test record dict."""
    rec = {
        'Signature': 'DIAL', 'FormID': formid, 'RecordFlags': '0',
        'EditorID': edid, 'DATA.Type': str(dtype),
    }
    if quest_fid:
        rec['QuestCount'] = '1'
        rec['Quest[0]'] = f'{quest_fid:08X}'
    else:
        rec['QuestCount'] = '0'
    return rec


def _make_qust_rec(formid='00099999', edid='TestQuest', full='Test Quest',
                   flags=0, priority=0, stages=None):
    """Create a minimal QUST test record dict."""
    rec = {
        'FormID': formid, 'RecordFlags': '0',
        'EditorID': edid, 'FULL': full,
        'DATA.Flags': str(flags), 'DATA.Priority': str(priority),
        'StageCount': '0',
    }
    if stages:
        rec['StageCount'] = str(len(stages))
        for i, (idx, logs) in enumerate(stages):
            rec[f'Stage[{i}].Index'] = str(idx)
            rec[f'Stage[{i}].LogCount'] = str(len(logs))
            for j, (log_flags, text, script) in enumerate(logs):
                rec[f'Stage[{i}].Log[{j}].Flags'] = str(log_flags)
                rec[f'Stage[{i}].Log[{j}].Text'] = text
                if script:
                    rec[f'Stage[{i}].Log[{j}].ResultScript'] = script
    return rec


# ===========================================================================
# CTDA Conversion Tests
# ===========================================================================

class TestCTDAConversion:
    """Verify 24B TES4 - 32B TES5 CTDA conversion."""

    def test_output_size_32_bytes(self):
        raw = _make_ctda(72, param1=0x1234)
        result = _convert_ctda(raw)
        assert result is not None
        assert len(result) == 32

    def test_preserves_function_index(self):
        for func in (58, 72, 77):
            raw = _make_ctda(func)
            result = _convert_ctda(raw)
            assert result is not None
            assert struct.unpack_from('<H', result, 8)[0] == func

    def test_drops_tes4_only_functions(self):
        for func in _FUNC_DROP:
            raw = _make_ctda(func)
            assert _convert_ctda(raw) is None

    def test_get_disposition_dropped(self):
        """GetDisposition(76) is the most common dropped function (1751 uses)."""
        raw = _make_ctda(76, comp_value=struct.unpack('<I', struct.pack('<f', 70.0))[0])
        assert _convert_ctda(raw) is None

    def test_unknown_field_0xFFFFFFFF(self):
        raw = _make_ctda(58)
        result = _convert_ctda(raw)
        assert struct.unpack_from('<I', result, 28)[0] == 0xFFFFFFFF

    def test_reference_field_zero(self):
        raw = _make_ctda(72)
        result = _convert_ctda(raw)
        assert struct.unpack_from('<I', result, 24)[0] == 0

    def test_use_global_flag_bit2(self):
        """UseGlobal (bit 2 = 0x04) remaps CompValue as FormID."""
        set_formid_index_offset(1)
        try:
            raw = _make_ctda(58, param1=0x00005678,
                             comp_value=0x00001234, type_byte=0x04)
            result = _convert_ctda(raw)
            comp = struct.unpack_from('<I', result, 4)[0]
            assert comp == 0x01001234  # remapped
        finally:
            set_formid_index_offset(0)

    def test_not_equal_bit5_not_use_global(self):
        """type=0x20 (NotEqual) must NOT remap CompValue."""
        set_formid_index_offset(1)
        try:
            float_1 = struct.unpack('<I', struct.pack('<f', 1.0))[0]
            raw = _make_ctda(72, param1=0x00001234, comp_value=float_1,
                             type_byte=0x20)
            result = _convert_ctda(raw)
            comp = struct.unpack_from('<I', result, 4)[0]
            assert comp == float_1  # unchanged
        finally:
            set_formid_index_offset(0)

    def test_or_flag_preserved(self):
        raw = _make_ctda(72, type_byte=0x01)  # OR flag
        result = _convert_ctda(raw)
        assert result[0] & 0x01

    def test_formid_params_remapped(self):
        set_formid_index_offset(2)
        try:
            raw = _make_ctda(72, param1=0x00001234, param2=0x00005678)
            result = _convert_ctda(raw)
            p1 = struct.unpack_from('<I', result, 12)[0]
            p2 = struct.unpack_from('<I', result, 16)[0]
            assert p1 == 0x02001234
            assert p2 == 0x02005678
        finally:
            set_formid_index_offset(0)

    def test_small_int_params_not_remapped(self):
        """Params < 0x100 are small ints (actor values etc), not FormIDs."""
        set_formid_index_offset(1)
        try:
            raw = _make_ctda(14, param1=0x00000035)  # GetActorValue(Alchemy)
            result = _convert_ctda(raw)
            p1 = struct.unpack_from('<I', result, 12)[0]
            assert p1 == 0x00000035  # NOT remapped
        finally:
            set_formid_index_offset(0)

    def test_run_on_preserved(self):
        """RunOn field passes through unchanged."""
        raw = _make_ctda(72, run_on=2)  # Reference
        result = _convert_ctda(raw)
        assert struct.unpack_from('<I', result, 20)[0] == 2


# ===========================================================================
# Voice Type CTDA Building
# ===========================================================================

class TestVoiceTypeCTDA:
    """Tests for voice type condition generation."""

    def test_ctda_structure(self):
        ctda = build_voice_type_ctda(0x01234567, is_or=True)
        assert len(ctda) == 32
        assert ctda[0] & 0x01  # OR flag
        func = struct.unpack_from('<H', ctda, 8)[0]
        assert func == 426  # GetIsVoiceType
        p1 = struct.unpack_from('<I', ctda, 12)[0]
        assert p1 == 0x01234567

    def test_or_flag_true(self):
        ctda = build_voice_type_ctda(0x01, is_or=True)
        assert ctda[0] & 0x01

    def test_or_flag_false(self):
        ctda = build_voice_type_ctda(0x01, is_or=False)
        assert not (ctda[0] & 0x01)

    def test_comp_value_1_0(self):
        ctda = build_voice_type_ctda(0x01)
        comp = struct.unpack_from('<I', ctda, 4)[0]
        assert comp == 0x3F800000  # 1.0f

    def test_npc_specific_info_gets_npc_vtyp(self):
        """INFO with GetIsID(npc) - GetIsVoiceType(npc's voice type)."""
        npc_fid = 0x01001234
        npc_vtyp = 0x01000099
        npc_to_vtyp = {npc_fid: npc_vtyp}
        # Build GetIsID condition targeting the NPC
        ctda_raw = _make_ctda(72, param1=0x00001234)  # GetIsID
        set_formid_index_offset(1)
        try:
            rec = _make_info_rec(conditions=[ctda_raw])
            result = build_voice_type_ctdas_for_info(rec, npc_to_vtyp)
            assert len(result) == 6 + 32  # one CTDA subrecord
            p1 = struct.unpack_from('<I', result, 6 + 12)[0]
            assert p1 == npc_vtyp
        finally:
            set_formid_index_offset(0)

    def test_generic_info_gets_no_vtyp(self):
        """Generic INFO (no GetIsID) - no voice type injection."""
        rec = _make_info_rec()  # no conditions
        result = build_voice_type_ctdas_for_info(rec, {})
        assert result == b''

    def test_generic_info_gets_topic_vtyps(self):
        """Generic INFO with topic_vtyps - gets voice types from siblings."""
        rec = _make_info_rec()  # no conditions
        topic_vtyps = {0x01000011, 0x01000022}
        result = build_voice_type_ctdas_for_info(rec, {}, topic_vtyps=topic_vtyps)
        # Should have 2 CTDAs (one per voice type)
        assert len(result) == 2 * (6 + 32)
        # All should be GetIsVoiceType (426)
        ctdas = []
        pos = 0
        while pos < len(result):
            assert result[pos:pos+4] == b'CTDA'
            size = struct.unpack_from('<H', result, pos+4)[0]
            ctdas.append(result[pos+6:pos+6+size])
            pos += 6 + size
        funcs = {struct.unpack_from('<H', c, 8)[0] for c in ctdas}
        assert funcs == {426}

    def test_generic_info_empty_topic_vtyps_no_ctda(self):
        """Generic INFO with empty topic_vtyps - no voice type injection."""
        rec = _make_info_rec()
        result = build_voice_type_ctdas_for_info(rec, {}, topic_vtyps=set())
        assert result == b''

    def test_multiple_npc_targets_or_chain(self):
        """INFO with multiple GetIsID conditions - OR'd voice type chain."""
        npc1 = 0x01001111
        npc2 = 0x01002222
        vtyp1 = 0x01000011
        vtyp2 = 0x01000022
        npc_to_vtyp = {npc1: vtyp1, npc2: vtyp2}
        conds = [
            _make_ctda(72, param1=0x00001111),  # GetIsID npc1
            _make_ctda(72, param1=0x00002222),  # GetIsID npc2
        ]
        set_formid_index_offset(1)
        try:
            rec = _make_info_rec(conditions=conds)
            result = build_voice_type_ctdas_for_info(rec, npc_to_vtyp)
            # Should have 2 CTDAs
            assert len(result) == 2 * (6 + 32)
            # First has OR, last has AND
            first_type = result[6]
            second_type = result[6 + 38]
            assert first_type & 0x01  # OR
            assert not (second_type & 0x01)  # AND (last in chain)
        finally:
            set_formid_index_offset(0)

    def test_same_vtyp_deduped(self):
        """Two NPCs with same voice type - one CTDA, not two."""
        npc1 = 0x01001111
        npc2 = 0x01002222
        shared_vtyp = 0x01000011
        npc_to_vtyp = {npc1: shared_vtyp, npc2: shared_vtyp}
        conds = [
            _make_ctda(72, param1=0x00001111),
            _make_ctda(72, param1=0x00002222),
        ]
        set_formid_index_offset(1)
        try:
            rec = _make_info_rec(conditions=conds)
            result = build_voice_type_ctdas_for_info(rec, npc_to_vtyp)
            assert len(result) == 6 + 32  # exactly one CTDA
        finally:
            set_formid_index_offset(0)

    def test_unknown_npc_no_vtyp(self):
        """NPC not in npc_to_vtyp map - no voice type generated."""
        conds = [_make_ctda(72, param1=0x0000FFFF)]  # unknown NPC
        set_formid_index_offset(1)
        try:
            rec = _make_info_rec(conditions=conds)
            result = build_voice_type_ctdas_for_info(rec, {})
            assert result == b''
        finally:
            set_formid_index_offset(0)


# ===========================================================================
# QUST Conversion Tests
# ===========================================================================

class TestQUSTConversion:
    """Verify QUST record structure matches Skyrim format."""

    def test_dnam_12_bytes(self):
        rec = _make_qust_rec()
        result = convert_QUST(rec)
        dnam = _find_subrecord(result, b'DNAM')
        assert dnam is not None
        assert len(dnam) == 12

    def test_dnam_formver_zero(self):
        """DNAM FormVer must be 0, matching all vanilla Skyrim quests."""
        rec = _make_qust_rec()
        result = convert_QUST(rec)
        dnam = _find_subrecord(result, b'DNAM')
        assert dnam[3] == 0

    def test_sge_quest_gets_starts_enabled(self):
        """Any quest with SGE gets StartsEnabled added."""
        rec = _make_qust_rec(formid='00099999', flags=1)  # SGE set
        result = convert_QUST(rec)
        dnam = _find_subrecord(result, b'DNAM')
        flags = struct.unpack_from('<H', dnam, 0)[0]
        assert flags & 0x0001  # StartGameEnabled preserved
        assert flags & 0x0010  # StartsEnabled added

    def test_no_has_dialogue_data(self):
        """HasDialogueData (0x8000) must NEVER be set."""
        rec = _make_qust_rec(formid='00099999', flags=1)
        result = convert_QUST(rec)
        dnam = _find_subrecord(result, b'DNAM')
        flags = struct.unpack_from('<H', dnam, 0)[0]
        assert not (flags & 0x8000)

    def test_sge_quest_preserves_priority(self):
        """SGE quests keep their original priority."""
        rec = _make_qust_rec(priority=100, formid='00099999', flags=1)
        result = convert_QUST(rec)
        dnam = _find_subrecord(result, b'DNAM')
        assert dnam[2] == 100

    def test_non_sge_quest_no_starts_enabled(self):
        """Quest without SGE should NOT get SGE or StartsEnabled."""
        rec = _make_qust_rec(formid='00099999', flags=0)
        result = convert_QUST(rec)
        dnam = _find_subrecord(result, b'DNAM')
        flags = struct.unpack_from('<H', dnam, 0)[0]
        assert not (flags & 0x0001)  # SGE not forced
        assert not (flags & 0x0010)  # StartsEnabled not set

    def test_non_dialogue_quest_preserves_priority(self):
        rec = _make_qust_rec(priority=100)
        result = convert_QUST(rec)
        dnam = _find_subrecord(result, b'DNAM')
        assert dnam[2] == 100

    def test_has_next_and_anam(self):
        rec = _make_qust_rec()
        result = convert_QUST(rec)
        assert _find_subrecord(result, b'NEXT') is not None
        assert _find_subrecord(result, b'ANAM') is not None

    def test_stage_indx_4_bytes(self):
        """TES5 INDX is 4 bytes: U16 stage + U8 flags + U8 unknown."""
        stages = [(10, [(0, 'First stage', None)])]
        rec = _make_qust_rec(stages=stages)
        result = convert_QUST(rec)
        indx = _find_subrecord(result, b'INDX')
        assert indx is not None
        assert len(indx) == 4
        stage_idx = struct.unpack_from('<H', indx, 0)[0]
        assert stage_idx == 10

    def test_stage_log_qsdt_cnam(self):
        """Each stage log entry gets QSDT + optional CNAM."""
        stages = [(10, [(0, 'Log text here', None)])]
        rec = _make_qust_rec(stages=stages)
        result = convert_QUST(rec)
        qsdt = _find_subrecord(result, b'QSDT')
        assert qsdt is not None
        cnam = _find_subrecord(result, b'CNAM')
        assert cnam is not None
        assert b'Log text here' in cnam

    def test_vmad_present_when_stage_has_script(self):
        stages = [(10, [(0, 'Done', 'set x to 1')])]
        rec = _make_qust_rec(edid='TestQuest', stages=stages)
        result = convert_QUST(rec)
        vmad = _find_subrecord(result, b'VMAD')
        assert vmad is not None
        # VMAD version=5, objectFormat=2
        assert struct.unpack_from('<HH', vmad, 0) == (5, 2)
        assert b'TES4_QF_TestQuest' in vmad

    def test_no_vmad_without_scripts(self):
        stages = [(10, [(0, 'Done', None)])]
        rec = _make_qust_rec(stages=stages)
        result = convert_QUST(rec)
        assert _find_subrecord(result, b'VMAD') is None

    def test_edid_preserved(self):
        rec = _make_qust_rec(edid='MQ01')
        result = convert_QUST(rec)
        edid = _find_subrecord(result, b'EDID')
        assert edid is not None
        assert b'MQ01' in edid

    def test_full_preserved(self):
        rec = _make_qust_rec(full='Main Quest Part 1')
        result = convert_QUST(rec)
        full = _find_subrecord(result, b'FULL')
        assert full is not None
        assert b'Main Quest Part 1' in full


# ===========================================================================
# DIAL Conversion Tests
# ===========================================================================

class TestDIALConversion:
    """Verify DIAL record structure."""

    def test_data_4_bytes(self):
        rec = _make_dial_rec()
        result = convert_DIAL(rec, info_count=1)
        data = _find_subrecord(result, b'DATA')
        assert len(data) == 4

    def test_greeting_hello_subtype(self):
        rec = _make_dial_rec(edid='GREETING', dtype=DIAL_TYPE_TOPIC)
        result = convert_DIAL(rec)
        snam = _find_subrecord(result, b'SNAM')
        assert snam == b'HELO'
        data = _find_subrecord(result, b'DATA')
        assert data[1] == 7  # Category = Misc

    def test_attack_combat_subtype(self):
        rec = _make_dial_rec(edid='Attack', dtype=DIAL_TYPE_COMBAT)
        result = convert_DIAL(rec)
        snam = _find_subrecord(result, b'SNAM')
        assert snam == b'ATCK'
        data = _find_subrecord(result, b'DATA')
        assert data[1] == 3  # Category = Combat

    def test_conversation_topic_category_0(self):
        rec = _make_dial_rec(edid='MQ01EarlierRumors')
        result = convert_DIAL(rec)
        data = _find_subrecord(result, b'DATA')
        assert data[1] == 0  # Category = Topic
        snam = _find_subrecord(result, b'SNAM')
        assert snam == b'CUST'  # Custom conversation topic

    def test_bark_no_bnam(self):
        rec = _make_dial_rec(edid='GREETING', dtype=DIAL_TYPE_TOPIC)
        result = convert_DIAL(rec, info_count=5)
        assert _find_subrecord(result, b'BNAM') is None

    def test_conversation_has_bnam(self):
        rec = _make_dial_rec(edid='MQ01Topic', quest_fid=0x01AABB)
        result = convert_DIAL(rec, info_count=3, dlbr_fid=0x01CCDD)
        bnam = _find_subrecord(result, b'BNAM')
        assert bnam is not None
        assert struct.unpack_from('<I', bnam, 0)[0] == 0x01CCDD

    def test_qnam_from_quest(self):
        rec = _make_dial_rec(quest_fid=0x01999999)
        result = convert_DIAL(rec)
        qnam = _find_subrecord(result, b'QNAM')
        assert qnam is not None
        assert struct.unpack_from('<I', qnam, 0)[0] == 0x01999999

    def test_qnam_override(self):
        """quest_fid_override forces QNAM for orphan DIALs."""
        rec = _make_dial_rec()  # no quest
        result = convert_DIAL(rec, quest_fid_override=0x02AABB)
        qnam = _find_subrecord(result, b'QNAM')
        assert struct.unpack_from('<I', qnam, 0)[0] == 0x02AABB

    def test_pnam_priority_50(self):
        rec = _make_dial_rec()
        result = convert_DIAL(rec)
        pnam = _find_subrecord(result, b'PNAM')
        assert struct.unpack_from('<f', pnam, 0)[0] == pytest.approx(50.0)

    def test_tifc_matches_info_count(self):
        rec = _make_dial_rec()
        result = convert_DIAL(rec, info_count=42)
        tifc = _find_subrecord(result, b'TIFC')
        assert struct.unpack_from('<I', tifc, 0)[0] == 42

    def test_snam_always_4_bytes(self):
        for edid in ('GREETING', 'Attack', 'MQ01Topic', 'IdleChatter'):
            rec = _make_dial_rec(edid=edid)
            result = convert_DIAL(rec)
            snam = _find_subrecord(result, b'SNAM')
            assert snam is not None
            assert len(snam) == 4, f"{edid}: SNAM should be 4 bytes"

    def test_generic_combat_dtype_to_attack_subtype(self):
        """Unknown EditorID with Combat dtype - Attack subtype."""
        rec = _make_dial_rec(edid='UnknownCombat', dtype=DIAL_TYPE_COMBAT)
        result = convert_DIAL(rec)
        snam = _find_subrecord(result, b'SNAM')
        assert snam == b'ATCK'

    def test_generic_detection_dtype_to_nota_subtype(self):
        rec = _make_dial_rec(edid='UnknownDetection', dtype=DIAL_TYPE_DETECTION)
        result = convert_DIAL(rec)
        snam = _find_subrecord(result, b'SNAM')
        assert snam == b'NOTA'


# ===========================================================================
# INFO Conversion Tests
# ===========================================================================

class TestINFOConversion:
    """Verify INFO record structure and condition handling."""

    def test_enam_flags_masked(self):
        """Only compatible flag bits (0x37) pass through."""
        rec = _make_info_rec(data_flags=0xFF)
        result = convert_INFO(rec)
        enam = _find_subrecord(result, b'ENAM')
        flags = struct.unpack_from('<H', enam, 0)[0]
        assert flags == 0x37  # masked

    def test_cnam_favor_level_zero(self):
        rec = _make_info_rec()
        result = convert_INFO(rec)
        cnam = _find_subrecord(result, b'CNAM')
        assert cnam is not None
        assert cnam[0] == 0

    def test_voice_ctdas_before_tes4_conditions(self):
        """Voice type CTDAs must precede converted TES4 conditions."""
        tes4_cond = _make_ctda(66)  # GetIsRace
        rec = _make_info_rec(conditions=[tes4_cond])
        vt_ctda = pack_subrecord('CTDA', build_voice_type_ctda(0x01))
        result = convert_INFO(rec, voice_type_ctdas=vt_ctda)
        ctdas = _find_all_subrecords(result, b'CTDA')
        assert len(ctdas) >= 2
        # First = voice type (426), last = TES4 condition (66)
        assert struct.unpack_from('<H', ctdas[0], 8)[0] == 426
        assert struct.unpack_from('<H', ctdas[-1], 8)[0] == 66

    def test_bark_preserves_quest_conditions(self):
        """Bark INFOs now preserve quest-dependent conditions since
        .seq + VMAD means quests run and stages evaluate correctly."""
        quest_conds = [_make_ctda(f) for f in (56, 58, 59)]
        non_quest = _make_ctda(77)  # GetDead
        all_conds = quest_conds + [non_quest]
        rec = _make_info_rec(conditions=all_conds)
        result = convert_INFO(rec, is_bark=True)
        ctdas = _find_all_subrecords(result, b'CTDA')
        funcs = {struct.unpack_from('<H', c, 8)[0] for c in ctdas}
        # All quest-dependent functions should now be preserved
        for qf in (56, 58, 59):
            assert qf in funcs, f"Quest func {qf} should be preserved on bark INFOs"
        assert 77 in funcs  # GetDead preserved

    def test_bark_preserves_get_in_cell(self):
        """GetInCell (71) must NOT be stripped from barks â€” it provides
        location-based filtering that prevents wrong city greetings."""
        conds = [_make_ctda(71, param1=0x00001234)]  # GetInCell
        rec = _make_info_rec(conditions=conds)
        result = convert_INFO(rec, is_bark=True)
        ctdas = _find_all_subrecords(result, b'CTDA')
        funcs = {struct.unpack_from('<H', c, 8)[0] for c in ctdas}
        assert 71 in funcs

    def test_bark_preserves_get_current_ai_procedure(self):
        """GetCurrentAIProcedure (67) must NOT be stripped from barks."""
        conds = [_make_ctda(67, param1=0)]
        rec = _make_info_rec(conditions=conds)
        result = convert_INFO(rec, is_bark=True)
        ctdas = _find_all_subrecords(result, b'CTDA')
        funcs = {struct.unpack_from('<H', c, 8)[0] for c in ctdas}
        assert 67 in funcs

    def test_conversation_keeps_quest_conditions(self):
        """Conversation INFOs preserve all quest conditions."""
        conds = [_make_ctda(58, param1=0x00001234)]  # GetStage
        rec = _make_info_rec(conditions=conds)
        result = convert_INFO(rec, is_bark=False)
        ctdas = _find_all_subrecords(result, b'CTDA')
        funcs = {struct.unpack_from('<H', c, 8)[0] for c in ctdas}
        assert 58 in funcs

    def test_FUNC_DROP_dropped(self):
        """TES4-only condition functions don't appear in output."""
        conds = [_make_ctda(76)]  # GetDisposition
        rec = _make_info_rec(conditions=conds)
        result = convert_INFO(rec)
        ctdas = _find_all_subrecords(result, b'CTDA')
        assert len(ctdas) == 0

    def test_response_trdt_24_bytes(self):
        rec = _make_info_rec(responses=[('Hello there', 5, 100)])
        result = convert_INFO(rec)
        trdt = _find_subrecord(result, b'TRDT')
        assert trdt is not None
        assert len(trdt) == 24

    def test_response_has_nam1_nam2_nam3(self):
        """Each response must have NAM1, NAM2, NAM3."""
        rec = _make_info_rec(responses=[('Some text', 0, 0)])
        result = convert_INFO(rec)
        assert _find_subrecord(result, b'NAM1') is not None
        assert _find_subrecord(result, b'NAM2') is not None
        assert _find_subrecord(result, b'NAM3') is not None

    def test_response_emotion(self):
        rec = _make_info_rec(responses=[('Grr', 1, 80)])  # Anger, value=80
        result = convert_INFO(rec)
        trdt = _find_subrecord(result, b'TRDT')
        emotion = struct.unpack_from('<I', trdt, 0)[0]
        emotion_val = struct.unpack_from('<i', trdt, 4)[0]
        assert emotion == 1
        assert emotion_val == 80

    def test_tclt_choices(self):
        rec = _make_info_rec(choices=[0x01AABB, 0x01CCDD])
        result = convert_INFO(rec)
        tclts = _find_all_subrecords(result, b'TCLT')
        assert len(tclts) == 2
        fids = [struct.unpack_from('<I', t, 0)[0] for t in tclts]
        assert 0x01AABB in fids
        assert 0x01CCDD in fids

    def test_vmad_present_with_result_script(self):
        rec = _make_info_rec(formid='0000ABCD',
                             result_script='player.additem gold001 100')
        result = convert_INFO(rec)
        vmad = _find_subrecord(result, b'VMAD')
        assert vmad is not None
        assert struct.unpack_from('<HH', vmad, 0) == (5, 2)
        assert b'TES4_TIF__0000ABCD' in vmad

    def test_no_vmad_without_result_script(self):
        rec = _make_info_rec()
        result = convert_INFO(rec)
        assert _find_subrecord(result, b'VMAD') is None


# ===========================================================================
# Bark Topic Detection Tests
# ===========================================================================

class TestBarkTopicDetection:
    """Verify is_bark_topic classification."""

    @pytest.mark.parametrize("edid", [
        'GREETING', 'HELLO', 'GOODBYE', 'IdleChatter', 'Idle',
        'Attack', 'Hit', 'Flee', 'Steal', 'Trespass',
        'ServiceRefusal', 'Barter', 'Repair', 'Travel',
        'ObserveCombat', 'Corpse', 'NoticeCorpse', 'TimeToGo',
        'InfoRefusal', 'Noticed', 'Seen', 'Unseen', 'Lost',
    ])
    def test_known_bark_edids(self, edid):
        assert is_bark_topic(edid)

    @pytest.mark.parametrize("edid", [
        'MQ01Topic', 'DarkBrotherhoodSanctuary', 'SomeConversation',
        'MS45Topic', 'TG01Contact', '',
    ])
    def test_known_non_bark_edids(self, edid):
        assert not is_bark_topic(edid)

    def test_combat_dtype_is_bark(self):
        assert is_bark_topic('UnknownEdid', dtype=DIAL_TYPE_COMBAT)

    def test_detection_dtype_is_bark(self):
        assert is_bark_topic('UnknownEdid', dtype=DIAL_TYPE_DETECTION)

    def test_misc_dtype_is_bark(self):
        assert is_bark_topic('UnknownEdid', dtype=DIAL_TYPE_MISC)

    def test_topic_dtype_not_bark(self):
        assert not is_bark_topic('UnknownEdid', dtype=DIAL_TYPE_TOPIC)

    def test_conversation_dtype_not_bark(self):
        assert not is_bark_topic('UnknownEdid', dtype=DIAL_TYPE_CONVERSATION)

    def test_edid_overrides_dtype(self):
        """Known bark EditorID overrides dtype=Conversation."""
        # INFOGENERAL is now a selectable Rumors topic, not a bark
        assert not is_bark_topic('INFOGENERAL', dtype=DIAL_TYPE_CONVERSATION)
        assert is_bark_topic('AnswerStatus', dtype=DIAL_TYPE_CONVERSATION)
        assert is_bark_topic('TRANSITION', dtype=DIAL_TYPE_CONVERSATION)

    def test_system_topics_are_barks(self):
        """AnswerStatus/TRANSITION - Idle bark (94). INFOGENERAL is Rumors (not bark)."""
        for edid in ('AnswerStatus', 'TRANSITION'):
            assert _EDID_TO_SUBTYPE_INT[edid] == 94
            assert 94 in BARK_SUBTYPES
        assert 'INFOGENERAL' not in _EDID_TO_SUBTYPE_INT


# ===========================================================================
# should_skip_dial Tests
# ===========================================================================

class TestShouldSkipDial:
    """Verify skip logic for dialog topics."""

    def test_persuasion_skipped(self):
        rec = {'EditorID': 'ADMIREHATE', 'DATA.Type': '3'}
        assert should_skip_dial(rec)

    def test_service_skipped(self):
        rec = {'EditorID': 'BarterBuyItem', 'DATA.Type': '5'}
        assert should_skip_dial(rec)

    def test_creature_responses_skipped(self):
        for edid in _DIAL_SKIP_EDIDS:
            rec = {'EditorID': edid, 'DATA.Type': '1'}
            assert should_skip_dial(rec), f"{edid} should be skipped"

    def test_test_topics_skipped(self):
        for edid in ('TestDoggy', 'TestWolf', 'TestDialogue'):
            rec = {'EditorID': edid, 'DATA.Type': '0'}
            assert should_skip_dial(rec), f"{edid} should be skipped"

    def test_markn_test_skipped(self):
        rec = {'EditorID': 'MarkNTestQuest', 'DATA.Type': '0'}
        assert should_skip_dial(rec)

    def test_normal_topic_not_skipped(self):
        rec = {'EditorID': 'MQ01Topic', 'DATA.Type': '0'}
        assert not should_skip_dial(rec)

    def test_combat_not_skipped(self):
        rec = {'EditorID': 'Attack', 'DATA.Type': '2'}
        assert not should_skip_dial(rec)

    def test_greeting_not_skipped(self):
        rec = {'EditorID': 'GREETING', 'DATA.Type': '0'}
        assert not should_skip_dial(rec)


# ===========================================================================
# DLBR / DLVW Structure Tests
# ===========================================================================

class TestDLBR:
    """Verify Dialog Branch record structure."""

    def test_record_signature(self):
        result = make_dlbr(0x01AA, 'Branch', 0x01BB, 0x01CC)
        assert result[:4] == b'DLBR'

    def test_has_required_subrecords(self):
        result = make_dlbr(0x01AA, 'TestBranch', 0x01BB, 0x01CC)
        assert _find_subrecord(result, b'EDID') is not None
        assert _find_subrecord(result, b'QNAM') is not None
        assert _find_subrecord(result, b'TNAM') is not None
        assert _find_subrecord(result, b'DNAM') is not None
        assert _find_subrecord(result, b'SNAM') is not None

    def test_qnam_quest_fid(self):
        result = make_dlbr(0x01AA, 'B', 0x01CCDD, 0x01EE)
        qnam = _find_subrecord(result, b'QNAM')
        assert struct.unpack_from('<I', qnam, 0)[0] == 0x01CCDD

    def test_snam_dial_fid(self):
        result = make_dlbr(0x01AA, 'B', 0x01BB, 0x01EEFF)
        snam = _find_subrecord(result, b'SNAM')
        assert struct.unpack_from('<I', snam, 0)[0] == 0x01EEFF

    def test_tnam_player(self):
        result = make_dlbr(0x01AA, 'B', 0x01BB, 0x01CC)
        tnam = _find_subrecord(result, b'TNAM')
        assert struct.unpack_from('<I', tnam, 0)[0] == 0  # Player

    def test_dnam_top_level(self):
        result = make_dlbr(0x01AA, 'B', 0x01BB, 0x01CC, top_level=True)
        dnam = _find_subrecord(result, b'DNAM')
        assert struct.unpack_from('<I', dnam, 0)[0] == 1

    def test_dnam_not_top_level(self):
        result = make_dlbr(0x01AA, 'B', 0x01BB, 0x01CC, top_level=False)
        dnam = _find_subrecord(result, b'DNAM')
        assert struct.unpack_from('<I', dnam, 0)[0] == 0


class TestDLVW:
    """Verify Dialog View record structure."""

    def test_record_signature(self):
        result = make_dlvw(0x01AA, 'View', 0x01BB, [], [])
        assert result[:4] == b'DLVW'

    def test_has_required_subrecords(self):
        result = make_dlvw(0x01AA, 'V', 0x01BB, [0x01CC], [0x01DD])
        assert _find_subrecord(result, b'EDID') is not None
        assert _find_subrecord(result, b'QNAM') is not None
        assert _find_subrecord(result, b'ENAM') is not None
        assert _find_subrecord(result, b'DNAM') is not None

    def test_branch_fids(self):
        result = make_dlvw(0x01AA, 'V', 0x01BB, [0x01CC, 0x01DD], [])
        bnams = _find_all_subrecords(result, b'BNAM')
        assert len(bnams) == 2

    def test_topic_fids(self):
        result = make_dlvw(0x01AA, 'V', 0x01BB, [], [0x01EE, 0x01FF])
        tnams = _find_all_subrecords(result, b'TNAM')
        assert len(tnams) == 2


# ===========================================================================
# Dialog Evaluation Simulation Tests
# ===========================================================================

class TestDialogEvaluationSimulation:
    """Simulate how Skyrim evaluates dialog to verify correct NPC filtering.

    These tests model the engine's condition evaluation to verify that:
    - Only the correct NPC sees NPC-specific dialog
    - Quest-gated dialog doesn't appear before the required stage
    - Faction-restricted dialog respects faction membership
    """

    @staticmethod
    def _evaluate_ctdas(ctda_list: list[bytes], npc_vtyp: int,
                        npc_fid: int = 0, quest_stages: dict = None,
                        faction_ranks: dict = None) -> bool:
        """Simulate Skyrim's CTDA evaluation for a set of conditions.

        Returns True if ALL non-OR'd conditions pass (with OR chains evaluated
        as any-true groups).
        """
        if not ctda_list:
            return True

        quest_stages = quest_stages or {}
        faction_ranks = faction_ranks or {}

        # Group conditions into AND groups, where OR-connected conditions
        # form a single group
        groups = []
        current_or_group = []

        for ctda in ctda_list:
            type_byte = ctda[0]
            is_or = bool(type_byte & 0x01)
            current_or_group.append(ctda)
            if not is_or:
                groups.append(current_or_group)
                current_or_group = []

        if current_or_group:
            groups.append(current_or_group)

        # Each group must have at least one passing condition
        for group in groups:
            group_pass = False
            for ctda in group:
                if TestDialogEvaluationSimulation._eval_single(
                        ctda, npc_vtyp, npc_fid, quest_stages, faction_ranks):
                    group_pass = True
                    break
            if not group_pass:
                return False
        return True

    @staticmethod
    def _eval_single(ctda: bytes, npc_vtyp: int, npc_fid: int,
                     quest_stages: dict, faction_ranks: dict) -> bool:
        """Evaluate a single CTDA condition."""
        type_byte = ctda[0]
        comp_type = (type_byte >> 5) & 0x07  # bits 5-7
        comp_raw = struct.unpack_from('<I', ctda, 4)[0]
        comp_float = struct.unpack_from('<f', ctda, 4)[0]
        func_idx = struct.unpack_from('<H', ctda, 8)[0]
        param1 = struct.unpack_from('<I', ctda, 12)[0]
        param2 = struct.unpack_from('<I', ctda, 16)[0]

        # Evaluate the function
        if func_idx == 426:  # GetIsVoiceType
            result = 1.0 if param1 == npc_vtyp else 0.0
        elif func_idx == 72:  # GetIsID
            result = 1.0 if param1 == npc_fid else 0.0
        elif func_idx == 58:  # GetStage
            result = float(quest_stages.get(param1, 0))
        elif func_idx == 59:  # GetStageDone
            result = 1.0 if quest_stages.get(param1, 0) >= param2 else 0.0
        elif func_idx == 56:  # GetQuestRunning
            result = 1.0  # dialogue quests always running
        elif func_idx == 79:  # GetFactionRank
            result = float(faction_ranks.get(param1, -1))
        elif func_idx == 77:  # GetDead
            result = 0.0  # NPC is alive
        elif func_idx == 66:  # GetIsRace
            result = 0.0  # simplified â€” not testing race
        else:
            result = 1.0  # unknown functions pass by default

        # Compare
        if comp_type == 0:  # Equal
            return abs(result - comp_float) < 0.01
        elif comp_type == 1:  # NotEqual
            return abs(result - comp_float) >= 0.01
        elif comp_type == 2:  # Greater
            return result > comp_float
        elif comp_type == 3:  # GreaterOrEqual
            return result >= comp_float
        elif comp_type == 4:  # Less
            return result < comp_float
        elif comp_type == 5:  # LessOrEqual
            return result <= comp_float
        return False

    def test_npc_specific_only_fires_for_correct_npc(self):
        """NPC-specific dialog should only fire for the targeted NPC."""
        target_vtyp = 0x01000011
        other_vtyp = 0x01000022
        target_npc = 0x01001234

        # Build INFO conditions: GetIsVoiceType(target) AND GetIsID(target_npc)
        vt_ctda = build_voice_type_ctda(target_vtyp, is_or=False)
        id_ctda = struct.pack('<B3xIHHIIIII',
                              0x00, 0x3F800000, 72, 0,
                              target_npc, 0, 0, 0, 0xFFFFFFFF)
        conditions = [vt_ctda, id_ctda]

        # Target NPC: should pass
        assert self._evaluate_ctdas(conditions, target_vtyp, npc_fid=target_npc)
        # Different NPC, different voice type: should fail
        assert not self._evaluate_ctdas(conditions, other_vtyp, npc_fid=0x01005555)
        # Same voice type, wrong NPC: should fail (GetIsID blocks)
        assert not self._evaluate_ctdas(conditions, target_vtyp, npc_fid=0x01005555)

    def test_voice_type_or_chain(self):
        """OR chain of voice types: any matching type passes."""
        vt1 = 0x01000011
        vt2 = 0x01000022
        vt3 = 0x01000033
        conditions = [
            build_voice_type_ctda(vt1, is_or=True),
            build_voice_type_ctda(vt2, is_or=True),
            build_voice_type_ctda(vt3, is_or=False),  # last = AND
        ]
        assert self._evaluate_ctdas(conditions, vt1)
        assert self._evaluate_ctdas(conditions, vt2)
        assert self._evaluate_ctdas(conditions, vt3)
        assert not self._evaluate_ctdas(conditions, 0x01000099)

    def test_quest_stage_gating(self):
        """Dialog with GetStage >= 10 only fires after quest reaches stage 10."""
        quest_fid = 0x01099999
        # GetStage(quest) >= 10
        comp_10 = struct.unpack('<I', struct.pack('<f', 10.0))[0]
        stage_ctda = struct.pack('<B3xIHHIIIII',
                                 0x60,  # GreaterOrEqual (3 << 5)
                                 comp_10, 58, 0,
                                 quest_fid, 0, 0, 0, 0xFFFFFFFF)
        conditions = [stage_ctda]

        # Before stage 10: should fail
        assert not self._evaluate_ctdas(conditions, 0x01, quest_stages={quest_fid: 5})
        # At stage 10: should pass
        assert self._evaluate_ctdas(conditions, 0x01, quest_stages={quest_fid: 10})
        # After stage 10: should pass
        assert self._evaluate_ctdas(conditions, 0x01, quest_stages={quest_fid: 20})

    def test_faction_gating(self):
        """Faction-gated dialog only appears for faction members."""
        faction_fid = 0x01AABB
        # GetFactionRank(faction) >= 0
        comp_0 = struct.unpack('<I', struct.pack('<f', 0.0))[0]
        faction_ctda = struct.pack('<B3xIHHIIIII',
                                   0x60,  # GreaterOrEqual
                                   comp_0, 79, 0,
                                   faction_fid, 0, 0, 0, 0xFFFFFFFF)
        conditions = [faction_ctda]

        # Not in faction (rank -1): should fail
        assert not self._evaluate_ctdas(conditions, 0x01,
                                         faction_ranks={faction_fid: -1})
        # In faction (rank 0+): should pass
        assert self._evaluate_ctdas(conditions, 0x01,
                                     faction_ranks={faction_fid: 0})
        assert self._evaluate_ctdas(conditions, 0x01,
                                     faction_ranks={faction_fid: 5})

    def test_combined_vtyp_and_stage_gating(self):
        """Voice type + quest stage conditions must BOTH pass."""
        vtyp = 0x01000011
        quest = 0x01099999
        comp_20 = struct.unpack('<I', struct.pack('<f', 20.0))[0]

        conditions = [
            build_voice_type_ctda(vtyp, is_or=False),  # AND
            struct.pack('<B3xIHHIIIII',
                        0x60, comp_20, 58, 0,  # GetStage >= 20
                        quest, 0, 0, 0, 0xFFFFFFFF),
        ]

        # Right voice type, wrong stage: fail
        assert not self._evaluate_ctdas(conditions, vtyp,
                                         quest_stages={quest: 10})
        # Right voice type, right stage: pass
        assert self._evaluate_ctdas(conditions, vtyp,
                                     quest_stages={quest: 20})
        # Wrong voice type, right stage: fail
        assert not self._evaluate_ctdas(conditions, 0x01000099,
                                         quest_stages={quest: 20})

    def test_bark_without_quest_conditions_fires(self):
        """Bark with only voice type condition fires for matching NPC."""
        vtyp = 0x01000011
        conditions = [build_voice_type_ctda(vtyp, is_or=False)]
        assert self._evaluate_ctdas(conditions, vtyp)
        assert not self._evaluate_ctdas(conditions, 0x01000099)

    def test_generic_info_fires_for_any_npc(self):
        """Generic INFO (no conditions) fires for any NPC."""
        assert self._evaluate_ctdas([], npc_vtyp=0x01000011)
        assert self._evaluate_ctdas([], npc_vtyp=0x01000099)


# ===========================================================================
# Consistency Tests (cross-checking between systems)
# ===========================================================================

class TestConsistency:
    """Cross-check consistency between different parts of the dialog system."""

    def test_get_in_cell_not_tes4_only(self):
        """GetInCell (71) must NOT be in _FUNC_DROP â€” it provides
        location-based filtering with properly remapped FormIDs."""
        assert 71 not in _FUNC_DROP

    def test_get_current_ai_proc_not_tes4_only(self):
        """GetCurrentAIProcedure (67) must NOT be in _FUNC_DROP."""
        assert 67 not in _FUNC_DROP

    def test_conversation_type1_is_chain_topic(self):
        """TES4 DATA.Type=1 (Conversation) topics are chain topics: they're
        only reachable via TCLT links, NEVER shown as top-level dialog."""
        # Chain topics don't get BNAM or DLBR â€” the import_main._build_dialog_groups
        # checks `dtype == 1` to skip DLBR creation. Verify the classification:
        assert not is_bark_topic('SomeConversation', dtype=DIAL_TYPE_CONVERSATION)
        # Type 1 is not a bark, but it IS a chain â€” tested by exclusion in import_main

    def test_edid_subtype_all_in_bark_subtypes(self):
        """Every EditorID subtype mapping should be a bark subtype."""
        for edid, subtype in _EDID_TO_SUBTYPE_INT.items():
            assert subtype in BARK_SUBTYPES, \
                f"{edid} maps to subtype {subtype} which is not in BARK_SUBTYPES"

    def test_bark_subtypes_have_category_override(self):
        """Every bark subtype should have a category override."""
        from tes5_import.dialog_converter import convert_DIAL
        # We test indirectly by converting a DIAL for each bark subtype's EditorID
        tested = set()
        for edid, subtype in _EDID_TO_SUBTYPE_INT.items():
            if subtype in tested:
                continue
            tested.add(subtype)
            rec = _make_dial_rec(edid=edid)
            result = convert_DIAL(rec)
            data = _find_subrecord(result, b'DATA')
            category = data[1]
            assert category in (3, 5, 7), \
                f"{edid} (subtype {subtype}) has category {category}, expected 3/5/7"

    def test_FUNC_DROP_complete(self):
        """_FUNC_DROP includes all known TES4-only functions plus reused indices."""
        # Known TES4-only functions must be present
        tes4_only = {40, 76, 104, 160, 171, 201, 251}
        assert tes4_only.issubset(_FUNC_DROP)
        # Known critical reused-index functions must be present
        critical_reused = {249, 365, 81, 109, 180, 197, 224, 227}
        assert critical_reused.issubset(_FUNC_DROP)
        # Removed from DROP (same function at same index in both engines):
        # 79=GetQuestVariable, 128=GetFatiguePercentage/GetStaminaPercentage,
        # 215=GetDoorDefaultOpen/GetDefaultOpen, 327=IsRidingHorse/IsRidingMount,
        # 339=IsPlayersLastRiddenHorse/IsPlayersLastRiddenMount
        for kept in (79, 128, 215, 327, 339):
            assert kept not in _FUNC_DROP, f"func {kept} should NOT be dropped"
        # Total size: 7 TES4-only + 17 reused + 4 OBSE = 28
        assert len(_FUNC_DROP) == 28

    def test_all_dial_skip_types_are_valid(self):
        """Skip types must be valid TES4 DATA.Type values."""
        valid = {0, 1, 2, 3, 4, 5, 6}
        for dt in _DIAL_SKIP_TYPES:
            assert dt in valid

    def test_skip_edids_not_in_bark_edids(self):
        """Skip EditorIDs shouldn't also be bark topic EditorIDs."""
        for edid in _DIAL_SKIP_EDIDS:
            assert edid not in _EDID_TO_SUBTYPE_INT, \
                f"{edid} is in both skip and bark EditorID sets"


# ===========================================================================
# VMAD Property Tests
# ===========================================================================

class TestVMADProperties:
    """Test VMAD property encoding for script external references."""

    def test_quest_vmad_with_properties(self):
        """QUST VMAD should encode SCRO-based properties."""
        from script_convert.pipeline import build_vmad_quest_fragments
        props = {'SomeNPC': 0x01001234, 'SomeFaction': 0x01005678}
        vmad = build_vmad_quest_fragments('TestQuest', [(10, 0)],
                                          property_values=props)
        # VMAD header: version(2) + objectFormat(2) + scriptCount(2) = 6
        ver, obj_fmt = struct.unpack_from('<HH', vmad, 0)
        assert ver == 5
        assert obj_fmt == 2
        # scriptCount = 1
        sc = struct.unpack_from('<H', vmad, 4)[0]
        assert sc == 1
        # After script name + flags, propertyCount should be 2
        # Script name: TES4_QF_TestQuest
        name = 'TES4_QF_TestQuest'
        name_offset = 6  # after header
        name_len = struct.unpack_from('<H', vmad, name_offset)[0]
        assert name_len == len(name)
        # flags at name_offset + 2 + name_len
        flags_offset = name_offset + 2 + name_len
        flags = vmad[flags_offset]
        assert flags == 0
        # propertyCount
        prop_count = struct.unpack_from('<H', vmad, flags_offset + 1)[0]
        assert prop_count == 2

    def test_quest_vmad_without_properties(self):
        """QUST VMAD with no properties should have propertyCount=0."""
        from script_convert.pipeline import build_vmad_quest_fragments
        vmad = build_vmad_quest_fragments('TestQuest', [(10, 0)])
        name = 'TES4_QF_TestQuest'
        name_offset = 6
        flags_offset = name_offset + 2 + len(name)
        prop_count = struct.unpack_from('<H', vmad, flags_offset + 1)[0]
        assert prop_count == 0

    def test_info_vmad_with_properties(self):
        """INFO VMAD should encode SCRO-based properties."""
        from script_convert.pipeline import build_vmad_info_fragment
        props = {'SomeNPC': 0x01001234}
        vmad = build_vmad_info_fragment('00012345', property_values=props)
        # header(4) + scriptCount(2) = 6
        sc = struct.unpack_from('<H', vmad, 4)[0]
        assert sc == 1  # now 1 script (was 0 before properties)
        # Find propertyCount after script name + flags
        name = 'TES4_TIF__00012345'
        name_offset = 6
        name_len = struct.unpack_from('<H', vmad, name_offset)[0]
        assert name_len == len(name)
        flags_offset = name_offset + 2 + name_len
        prop_count = struct.unpack_from('<H', vmad, flags_offset + 1)[0]
        assert prop_count == 1

    def test_vmad_property_object_format(self):
        """VMAD Object property should have correct binary layout."""
        from script_convert.pipeline import build_vmad_quest_fragments
        props = {'TestProp': 0xDEADBEEF}
        vmad = build_vmad_quest_fragments('TestQ', [(1, 0)],
                                          property_values=props)
        # Navigate to the property data
        name = 'TES4_QF_TestQ'
        name_offset = 6
        flags_offset = name_offset + 2 + len(name)
        prop_count_offset = flags_offset + 1
        prop_count = struct.unpack_from('<H', vmad, prop_count_offset)[0]
        assert prop_count == 1
        # Property starts after prop_count
        p_offset = prop_count_offset + 2
        # Property name
        p_name_len = struct.unpack_from('<H', vmad, p_offset)[0]
        p_name = vmad[p_offset + 2: p_offset + 2 + p_name_len].decode('utf-8')
        assert p_name == 'TestProp'
        # Type=Object(1), Status=Edited(1)
        type_offset = p_offset + 2 + p_name_len
        p_type, p_status = struct.unpack_from('<BB', vmad, type_offset)
        assert p_type == 1  # Object
        assert p_status == 1  # Edited
        # Value: unused(U16=0) + alias(I16=-1) + FormID(U32)
        unused, alias, formid = struct.unpack_from('<HhI', vmad, type_offset + 2)
        assert unused == 0
        assert alias == -1
        assert formid == 0xDEADBEEF


# ===========================================================================
# TCLT FormID Remapping Tests
# ===========================================================================

class TestTCLTRemapping:
    """Verify TCLT (topic choice link) FormIDs are remapped exactly once.

    Bug fixed: TCLT stripping code read with get_formid (+1 offset), wrote back
    the remapped value, then convert_INFO read with get_formid again (+1 offset)
    - double remapping (prefix 02 instead of 01 for Oblivion.esm records).
    """

    def test_tclt_single_remapping(self):
        """TCLT FormID should be remapped once (00-01), not twice (00-02)."""
        # Simulate a TES4 export INFO with one TCLT choice
        rec = _make_info_rec()
        rec['ChoiceCount'] = '1'
        rec['Choice[0]'] = '00014B3F'  # TES4 FormID with prefix 00

        set_formid_index_offset(1)
        try:
            info_bytes = convert_INFO(rec, voice_type_ctdas=b'', is_bark=False)
            # Extract TCLT subrecords
            pos = 24  # skip record header
            tclt_fids = []
            while pos < len(info_bytes) - 6:
                sig = info_bytes[pos:pos + 4]
                size = struct.unpack_from('<H', info_bytes, pos + 4)[0]
                if sig == b'TCLT' and size == 4:
                    fid = struct.unpack_from('<I', info_bytes, pos + 6)[0]
                    tclt_fids.append(fid)
                pos += 6 + size
            assert len(tclt_fids) == 1
            assert tclt_fids[0] == 0x01014B3F, (
                f"Expected 0x01014B3F (prefix 01), got 0x{tclt_fids[0]:08X}")
        finally:
            set_formid_index_offset(0)

    def test_tclt_stripping_preserves_raw_formids(self):
        """TCLT stripping should preserve raw export FormIDs for later remapping."""
        rec = _make_info_rec()
        rec['ChoiceCount'] = '2'
        rec['Choice[0]'] = '0000AAAA'
        rec['Choice[1]'] = '0000BBBB'

        # Simulate the stripping step from import_main.py
        # Only strip Choice[1], keep Choice[0]
        skipped = {0x0100BBBB}  # remapped FormID of the one to skip

        set_formid_index_offset(1)
        try:
            from tes5_import.text_reader import get_formid
            choice_count = int(rec.get('ChoiceCount', '0'))
            kept_raw = []
            for i in range(choice_count):
                raw_val = rec.get(f'Choice[{i}]', '0')
                cfid = get_formid(rec, f'Choice[{i}]')
                if cfid not in skipped:
                    kept_raw.append(raw_val)
            rec['ChoiceCount'] = str(len(kept_raw))
            for i in range(choice_count):
                rec.pop(f'Choice[{i}]', None)
            for i, raw in enumerate(kept_raw):
                rec[f'Choice[{i}]'] = raw

            # Now convert â€” should apply offset exactly once
            info_bytes = convert_INFO(rec, voice_type_ctdas=b'', is_bark=False)
            pos = 24
            tclt_fids = []
            while pos < len(info_bytes) - 6:
                sig = info_bytes[pos:pos + 4]
                size = struct.unpack_from('<H', info_bytes, pos + 4)[0]
                if sig == b'TCLT' and size == 4:
                    fid = struct.unpack_from('<I', info_bytes, pos + 6)[0]
                    tclt_fids.append(fid)
                pos += 6 + size
            assert len(tclt_fids) == 1
            assert tclt_fids[0] == 0x0100AAAA  # kept, single remap
        finally:
            set_formid_index_offset(0)


# ===========================================================================
# Voice Type Injection Scope Tests
# ===========================================================================

class TestVoiceTypeInjectionScope:
    """Verify that voice type injection matches Skyrim's pattern:
    - Bark INFOs: GetIsVoiceType injected (for voice-based routing)
    - Conversation INFOs: NO GetIsVoiceType (GetIsID sufficient)

    In Skyrim.esm, only 13% of INFOs have GetIsVoiceType (barks/radiant).
    87% of INFOs have no voice type â€” conversation topics use GetIsID.
    """

    def test_conversation_info_no_voice_type(self):
        """Non-bark INFO should NOT have GetIsVoiceType injected."""
        npc_fid = 0x01001234
        vtyp_fid = 0x01000011
        npc_to_vtyp = {npc_fid: vtyp_fid}

        # An INFO with GetIsID condition â€” this is a conversation line
        rec = _make_info_rec(conditions=[_make_ctda(72, param1=0x00001234)])
        set_formid_index_offset(1)
        try:
            # When called directly, the function DOES produce voice types
            result = build_voice_type_ctdas_for_info(rec, npc_to_vtyp)
            assert len(result) > 0  # function itself works

            # But for non-bark topics, the caller should pass b''
            # This test documents the expected caller behavior
            voice_ctdas = b''  # non-bark: no voice type injection
            info_bytes = convert_INFO(rec, voice_type_ctdas=voice_ctdas,
                                      is_bark=False)
            # Parse CTDAs from info_bytes â€” should NOT contain func 426
            ctda_funcs = _extract_ctda_funcs(info_bytes)
            assert 426 not in ctda_funcs
        finally:
            set_formid_index_offset(0)

    def test_bark_info_gets_voice_type(self):
        """Bark INFO should have GetIsVoiceType injected."""
        npc_fid = 0x01001234
        vtyp_fid = 0x01000011
        npc_to_vtyp = {npc_fid: vtyp_fid}

        rec = _make_info_rec(conditions=[_make_ctda(72, param1=0x00001234)])
        set_formid_index_offset(1)
        try:
            voice_ctdas = build_voice_type_ctdas_for_info(rec, npc_to_vtyp)
            info_bytes = convert_INFO(rec, voice_type_ctdas=voice_ctdas,
                                      is_bark=True)
            ctda_funcs = _extract_ctda_funcs(info_bytes)
            assert 426 in ctda_funcs  # GetIsVoiceType present
            assert 72 in ctda_funcs   # GetIsID also present
        finally:
            set_formid_index_offset(0)

    def test_conditionless_conversation_info_stays_conditionless(self):
        """Generic conversation INFO with no conditions should stay conditionless.

        Skyrim has 7,137 INFOs (23%) with no conditions â€” this is normal.
        """
        rec = _make_info_rec()  # no conditions
        info_bytes = convert_INFO(rec, voice_type_ctdas=b'', is_bark=False)
        ctda_funcs = _extract_ctda_funcs(info_bytes)
        assert len(ctda_funcs) == 0


def _extract_ctda_funcs(record_bytes: bytes) -> set:
    """Extract all CTDA function indices from a packed record."""
    funcs = set()
    # Skip 24-byte TES5 record header
    pos = 24
    while pos < len(record_bytes) - 6:
        sig = record_bytes[pos:pos+4]
        if pos + 6 > len(record_bytes):
            break
        size = struct.unpack_from('<H', record_bytes, pos + 4)[0]
        if sig == b'CTDA' and size >= 12:
            func_idx = struct.unpack_from('<H', record_bytes, pos + 6 + 8)[0]
            funcs.add(func_idx)
        pos += 6 + size
    return funcs

    def test_collect_scro_properties(self):
        """_collect_scro_properties extracts and remaps SCRO entries."""
        from tes5_import.dialog_converter import _collect_scro_properties
        set_formid_index_offset(1)
        try:
            fid_to_edid = {0x00001234: 'TestActor'}
            rec = {'SCRO[0]': '00001234'}
            props = _collect_scro_properties(rec, fid_to_edid)
            assert 'TestActor' in props
            assert props['TestActor'] == 0x01001234  # remapped
        finally:
            set_formid_index_offset(0)

    def test_collect_scro_skips_player(self):
        """_collect_scro_properties skips the player FormID (0x14)."""
        from tes5_import.dialog_converter import _collect_scro_properties
        fid_to_edid = {0x14: 'Player'}
        rec = {'SCRO[0]': '00000014'}
        props = _collect_scro_properties(rec, fid_to_edid)
        assert len(props) == 0

    def test_collect_all_scro_includes_stage_refs(self):
        """_collect_all_scro_properties includes stage-level SCRO entries."""
        from tes5_import.dialog_converter import _collect_all_scro_properties
        set_formid_index_offset(1)
        try:
            fid_to_edid = {0x00001234: 'QuestTarget', 0x00005678: 'StageNPC'}
            rec = {
                'SCRO[0]': '00001234',
                'StageCount': '1',
                'Stage[0].LogCount': '1',
                'Stage[0].Log[0].SCRO[0]': '00005678',
            }
            props = _collect_all_scro_properties(rec, fid_to_edid)
            assert 'QuestTarget' in props
            assert 'StageNPC' in props
            assert props['QuestTarget'] == 0x01001234
            assert props['StageNPC'] == 0x01005678
        finally:
            set_formid_index_offset(0)

    def test_convert_qust_with_fid_to_edid(self):
        """convert_QUST with fid_to_edid populates VMAD properties."""
        set_formid_index_offset(1)
        try:
            fid_to_edid = {0x00009ABC: 'SomeGlobal'}
            rec = {
                'Signature': 'QUST',
                'FormID': '00099000',
                'EditorID': 'TestQ',
                'RecordFlags': '0',
                'DATA.Flags': '0',
                'DATA.Priority': '50',
                'StageCount': '1',
                'Stage[0].Index': '10',
                'Stage[0].LogCount': '1',
                'Stage[0].Log[0].ResultScript': 'set somevar to 1',
                'Stage[0].Log[0].Flags': '0',
                'Stage[0].Log[0].Text': '',
                'SCRO[0]': '00009ABC',
            }
            result = convert_QUST(rec, fid_to_edid=fid_to_edid)
            vmad_data = _find_subrecord(result, b'VMAD')
            assert vmad_data is not None
            # propertyCount should be > 0
            name = 'TES4_QF_TestQ'
            name_offset = 6  # after VMAD header
            flags_offset = name_offset + 2 + len(name)
            prop_count = struct.unpack_from('<H', vmad_data, flags_offset + 1)[0]
            assert prop_count == 1
        finally:
            set_formid_index_offset(0)


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
