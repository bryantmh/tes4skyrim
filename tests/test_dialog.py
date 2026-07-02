"""
Tests for TES4->TES5 dialog/quest conversion (dialog_converter / dialog_conditions).

All byte-layout assertions are pinned to facts verified against real
Skyrim.esm data and xEdit wbDefinitionsTES5:
  - DIAL DATA on-disk = TopicFlags(U8) + Category(U8) + Subtype(U16); vanilla
    Hello = 00 07 49 00 (category 7 Misc, subtype 0x49=73). Swapping the
    fields puts an out-of-range value where the engine reads category ->
    EXCEPTION_ACCESS_VIOLATION at startup while topics initialize.
  - Subtype NUMBERS come from real data (Hello=73), not xEdit's display enum
    (shifted; the field is cpIgnore there and synced from SNAM).
  - CTDA is 32 bytes with raw function indices (GetIsID=72, GetIsVoiceType=426)
    and a runOn=0 / reference=0 / param3=-1 tail on ordinary conditions.
  - Quest visibility: Oblivion shows an INFO only while its own QSTI quest
    runs; Skyrim gates a DIAL's INFOs by the owning QNAM quest running.
"""

import struct

import pytest

from tes5_import.dialog_conditions import (
    CTDA_OR,
    FUNC_GET_IS_VOICE_TYPE,
    FUNC_GET_QUEST_RUNNING,
    build_ctda,
    build_or_chain,
    convert_ctda,
    convert_ctda_list,
    has_positive_getisid,
    read_getisid_fids,
)
from tes5_import.dialog_converter import (
    DIAL_TYPE_COMBAT,
    DIAL_TYPE_CONVERSATION,
    DIAL_TYPE_DETECTION,
    DIAL_TYPE_MISC,
    DIAL_TYPE_PERSUASION,
    DIAL_TYPE_SERVICE,
    DIAL_TYPE_TOPIC,
    _EDID_SUBTYPE,
    build_dialog_groups,
    classify_topic,
    convert_DIAL,
    convert_INFO,
    convert_QUST,
    make_dlbr,
    make_dlvw,
    should_skip_dial,
)
from tes5_import.text_reader import set_formid_index_offset


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_subrecord(record_bytes: bytes, sig: bytes) -> 'bytes | None':
    pos = 24
    while pos < len(record_bytes) - 6:
        sub_sig = record_bytes[pos:pos+4]
        sub_size = struct.unpack_from('<H', record_bytes, pos+4)[0]
        if sub_sig == sig:
            return record_bytes[pos+6:pos+6+sub_size]
        pos += 6 + sub_size
    return None


def _find_all_subrecords(record_bytes: bytes, sig: bytes) -> list:
    results = []
    pos = 24
    while pos < len(record_bytes) - 6:
        sub_sig = record_bytes[pos:pos+4]
        sub_size = struct.unpack_from('<H', record_bytes, pos+4)[0]
        if sub_sig == sig:
            results.append(record_bytes[pos+6:pos+6+sub_size])
        pos += 6 + sub_size
    return results


def _sub_order(record_bytes: bytes) -> list:
    order = []
    pos = 24
    while pos < len(record_bytes) - 6:
        order.append(record_bytes[pos:pos+4].decode('ascii'))
        pos += 6 + struct.unpack_from('<H', record_bytes, pos+4)[0]
    return order


def _tes4_ctda(type_byte=0x00, comp=0x3F800000, func=72, p1=0, p2=0) -> bytes:
    """24-byte TES4 CTDA."""
    return struct.pack('<B3xIHHII4x', type_byte, comp, func, 0, p1, p2)


class _FakeWriter:
    def __init__(self):
        self._next = 0x01F00000
        self.records = []
        self.groups = {}

    def alloc_formid(self):
        self._next += 1
        return self._next

    def add_record(self, sig, rec_bytes):
        self.records.append((sig, rec_bytes))

    def add_raw_group(self, sig, content):
        self.groups[sig] = content


def _walk_records(blob):
    """Yield (sig, formid, full_record_bytes), descending into nested GRUPs."""
    pos = 0
    while pos + 24 <= len(blob):
        sig = blob[pos:pos+4].decode('ascii', 'replace')
        size = struct.unpack_from('<I', blob, pos+4)[0]
        if sig == 'GRUP':
            yield from _walk_records(blob[pos+24:pos+size])
            pos += size
        else:
            fid = struct.unpack_from('<I', blob, pos+12)[0]
            yield sig, fid, blob[pos:pos+24+size]
            pos += 24 + size


# ---------------------------------------------------------------------------
# CTDA conversion
# ---------------------------------------------------------------------------

class TestCTDAConversion:
    def test_size_and_field_positions(self):
        out = convert_ctda(_tes4_ctda(func=72, p1=0x1234), offset=0)
        assert out is not None and len(out) == 32
        assert struct.unpack_from('<H', out, 8)[0] == 72
        assert struct.unpack_from('<I', out, 12)[0] == 0x1234

    def test_vanilla_tail(self):
        """Ordinary conditions end with runOn=0, reference=0, param3=-1
        (the dominant tail in vanilla Skyrim.esm INFO CTDAs)."""
        out = convert_ctda(_tes4_ctda(), offset=0)
        assert struct.unpack_from('<IIi', out, 20) == (0, 0, -1)
        built = build_ctda(FUNC_GET_IS_VOICE_TYPE, param1=0x01001234)
        assert struct.unpack_from('<IIi', built, 20) == (0, 0, -1)

    def test_run_on_target_translated(self):
        """TES4 type bit 0x02 (Run on target) -> TES5 RunOn=1 with the bit
        cleared; in TES5 that bit means 'use aliases'."""
        out = convert_ctda(_tes4_ctda(type_byte=0x02), offset=0)
        assert out[0] & 0x02 == 0
        assert struct.unpack_from('<I', out, 20)[0] == 1

    def test_use_global_flag_remaps_compvalue(self):
        """Only type bit 0x04 (Use Global) makes CompValue a FormID."""
        out = convert_ctda(_tes4_ctda(type_byte=0x04, comp=0x00001234, func=58,
                                      p1=0x5678), offset=1)
        assert struct.unpack_from('<I', out, 4)[0] == 0x01001234

    def test_notequal_operator_not_treated_as_use_global(self):
        out = convert_ctda(_tes4_ctda(type_byte=0x20, comp=0x3F800000), offset=1)
        assert struct.unpack_from('<I', out, 4)[0] == 0x3F800000

    def test_param_remapping(self):
        out = convert_ctda(_tes4_ctda(p1=0x00012345, p2=0x00023456), offset=1)
        assert struct.unpack_from('<I', out, 12)[0] == 0x01012345
        assert struct.unpack_from('<I', out, 16)[0] == 0x01023456

    def test_engine_fixed_formids_not_remapped(self):
        """Player (0x14) and other low engine FormIDs pass through."""
        out = convert_ctda(_tes4_ctda(p1=0x14), offset=1)
        assert struct.unpack_from('<I', out, 12)[0] == 0x14

    def test_dropped_functions(self):
        """TES4-only / index-reused functions return None (e.g. 76
        GetDisposition removed; 224 reused as GetVATSMode in TES5)."""
        for func in (76, 224, 249, 264):
            assert convert_ctda(_tes4_ctda(func=func), offset=0) is None

    def test_remapped_functions(self):
        """Same-name functions at moved indices are remapped (xEdit join)."""
        out = convert_ctda(_tes4_ctda(func=101), offset=0)  # IsWeaponOut
        assert struct.unpack_from('<H', out, 8)[0] == 263

    def test_gettalkedtopc_passes_through(self):
        """Func 50 = GetTalkedToPC in BOTH games (xEdit tables agree) —
        it must NOT be dropped; it gates most first/repeat greetings."""
        out = convert_ctda(_tes4_ctda(func=50), offset=0)
        assert out is not None
        assert struct.unpack_from('<H', out, 8)[0] == 50

    def test_or_chain_repaired_after_drop(self):
        """Dropping the last member of an OR chain must clear the dangling
        OR flag on the new last condition."""
        rec = {
            'Condition[0].Raw': _tes4_ctda(type_byte=CTDA_OR).hex(),
            'Condition[1].Raw': _tes4_ctda(func=76).hex(),   # dropped
        }
        out = convert_ctda_list(rec, offset=0)
        assert len(out) == 1
        assert out[0][0] & CTDA_OR == 0

    def test_build_or_chain_flags(self):
        """OR flag on all but the last chain member."""
        packed = build_or_chain(FUNC_GET_IS_VOICE_TYPE, [1, 2, 3])
        ctdas = []
        pos = 0
        while pos < len(packed):
            size = struct.unpack_from('<H', packed, pos+4)[0]
            ctdas.append(packed[pos+6:pos+6+size])
            pos += 6 + size
        assert len(ctdas) == 3
        assert all(c[0] & CTDA_OR for c in ctdas[:-1])
        assert ctdas[-1][0] & CTDA_OR == 0

    def test_read_getisid_fids(self):
        rec = {
            'Condition[0].Raw': _tes4_ctda(func=72, p1=0x00012345).hex(),
            'Condition[1].Raw': _tes4_ctda(type_byte=0x20, func=72,
                                           p1=0x00099999).hex(),  # negated
        }
        fids = read_getisid_fids(rec, offset=1)
        assert fids == {0x01012345}
        assert has_positive_getisid(rec)


# ---------------------------------------------------------------------------
# DIAL
# ---------------------------------------------------------------------------

class TestDIAL:
    def test_data_byte_order(self):
        """DIAL DATA on-disk = TopicFlags(U8) + Category(U8) + Subtype(U16).

        The engine indexes per-category topic tables by byte 1 at startup;
        writing the subtype there yields an out-of-range category and an
        EXCEPTION_ACCESS_VIOLATION while initializing topics (the 2026-06-18
        crash: TESTopic "Seen" / TESQuest "Charactergen").
        """
        rec = {'FormID': '00000105', 'RecordFlags': '0',
               'EditorID': 'Seen', 'DATA.Type': '4'}
        out = convert_DIAL(rec, info_count=0, dlbr_fid=0, quest_fid=0x01000ABC,
                           category=5, subtype=51, snam=b'NOTA')
        data = _find_subrecord(out, b'DATA')
        assert data is not None and len(data) == 4
        assert data[0] == 0, "byte 0 = topic flags"
        assert data[1] == 5, "byte 1 = category (Detection)"
        assert struct.unpack_from('<H', data, 2)[0] == 51, "bytes 2-3 = subtype"

    def test_subrecord_order(self):
        rec = {'FormID': '00001234', 'RecordFlags': '0',
               'EditorID': 'TestTopic', 'FULL': 'Test', 'DATA.Type': '0'}
        out = convert_DIAL(rec, info_count=3, dlbr_fid=0x01F00001,
                           quest_fid=0x01000ABC, category=0, subtype=0,
                           snam=b'CUST')
        assert _sub_order(out) == ['EDID', 'FULL', 'PNAM', 'BNAM', 'QNAM',
                                   'DATA', 'SNAM', 'TIFC']
        assert struct.unpack('<I', _find_subrecord(out, b'TIFC'))[0] == 3
        assert _find_subrecord(out, b'SNAM') == b'CUST'

    def test_classify_reserved_edids_match_vanilla(self):
        """Reserved-EDID subtypes must be the REAL on-disk numbers from
        Skyrim.esm (xEdit's display enum is shifted: real Hello=73 not 79)."""
        expected = {
            'GREETING': (7, 73, b'HELO'),
            'GOODBYE':  (7, 72, b'GBYE'),
            'Attack':   (3, 20, b'ATCK'),
            'Hit':      (3, 23, b'HIT_'),
            'Flee':     (3, 24, b'FLEE'),
            'Trespass': (3, 43, b'TRES'),
            'Seen':     (5, 51, b'NOTA'),
            'Lost':     (5, 57, b'LOTN'),
        }
        for edid, (cat, sub, snam) in expected.items():
            c, s, sn, bark = classify_topic(edid, DIAL_TYPE_TOPIC)
            assert (c, s, sn) == (cat, sub, snam), edid
            assert bark

    def test_classify_categories_in_engine_range(self):
        for edid in list(_EDID_SUBTYPE) + ['SomeCustomTopic', '']:
            for dtype in range(7):
                cat, sub, snam, _bark = classify_topic(edid, dtype)
                assert 0 <= cat <= 7, f"{edid}/{dtype}: category {cat}"
                assert len(snam) == 4
                assert 0 <= sub <= 102

    def test_classify_type_fallbacks(self):
        assert classify_topic('Foo', DIAL_TYPE_COMBAT)[3] is True
        assert classify_topic('Foo', DIAL_TYPE_DETECTION)[0] == 5
        assert classify_topic('Foo', DIAL_TYPE_MISC)[0] == 7
        cat, sub, snam, bark = classify_topic('Foo', DIAL_TYPE_CONVERSATION)
        assert (cat, sub, snam, bark) == (0, 0, b'CUST', False)

    def test_should_skip(self):
        assert should_skip_dial({'DATA.Type': str(DIAL_TYPE_PERSUASION)})
        assert should_skip_dial({'DATA.Type': str(DIAL_TYPE_SERVICE)})
        assert should_skip_dial({'DATA.Type': '0', 'EditorID': 'ANY'})
        assert should_skip_dial({'DATA.Type': '0', 'EditorID': 'TestFoo'})
        assert not should_skip_dial({'DATA.Type': '0', 'EditorID': 'Rumors'})


# ---------------------------------------------------------------------------
# INFO
# ---------------------------------------------------------------------------

class TestINFO:
    def _rec(self, **extra):
        rec = {'FormID': '00012345', 'RecordFlags': '0',
               'DATA.Flags': '0', 'ResponseCount': '0',
               'ChoiceCount': '0', 'ConditionCount': '0'}
        rec.update(extra)
        return rec

    def test_enam_flag_masking(self):
        """Only bit-compatible flags survive (Goodbye/Random/SayOnce/
        InfoRefusal/RandomEnd = 0x37); Run Immediately (0x08) must not."""
        out = convert_INFO(self._rec(**{'DATA.Flags': str(0xFF)}))
        enam = _find_subrecord(out, b'ENAM')
        assert len(enam) == 4
        assert struct.unpack_from('<H', enam, 0)[0] == 0x37

    def test_trdt_24_bytes(self):
        out = convert_INFO(self._rec(
            ResponseCount='1', **{
                'Response[0].ResponseText': 'Hello there.',
                'Response[0].EmotionType': '1',
                'Response[0].EmotionValue': '150',   # clamped to 100
                'Response[0].ResponseNumber': '1',
            }))
        trdt = _find_subrecord(out, b'TRDT')
        assert len(trdt) == 24
        assert struct.unpack_from('<Ii', trdt, 0) == (1, 100)
        assert trdt[12] == 1  # response number
        assert _find_subrecord(out, b'NAM1').rstrip(b'\x00') == b'Hello there.'
        assert _find_subrecord(out, b'NAM2') is not None
        assert _find_subrecord(out, b'NAM3') is not None

    def test_tclt_choices(self):
        set_formid_index_offset(1)
        try:
            out = convert_INFO(self._rec(
                ChoiceCount='2', **{'Choice[0]': '00001111',
                                    'Choice[1]': '00002222'}))
        finally:
            set_formid_index_offset(0)
        tclts = _find_all_subrecords(out, b'TCLT')
        assert [struct.unpack('<I', t)[0] for t in tclts] \
            == [0x01001111, 0x01002222]

    def test_injected_ctdas_precede_converted(self):
        """Injected gates come BEFORE translated TES4 conditions so their OR
        chains stay isolated from any trailing TES4 OR flags."""
        from tes5_import.record_types.common import pack_subrecord
        injected = pack_subrecord(
            'CTDA', build_ctda(FUNC_GET_IS_VOICE_TYPE, param1=0x01000AAA))
        out = convert_INFO(
            self._rec(ConditionCount='1',
                      **{'Condition[0].Raw': _tes4_ctda(func=72,
                                                        p1=0x1234).hex()}),
            injected_ctdas=injected)
        ctdas = _find_all_subrecords(out, b'CTDA')
        assert len(ctdas) == 2
        assert struct.unpack_from('<H', ctdas[0], 8)[0] == FUNC_GET_IS_VOICE_TYPE
        assert struct.unpack_from('<H', ctdas[1], 8)[0] == 72


# ---------------------------------------------------------------------------
# QUST
# ---------------------------------------------------------------------------

class TestQUST:
    def test_dnam_layout(self):
        """DNAM = Flags(U16) Priority(U8) FormVer(U8=0) Unknown(4) Type(U32);
        SGE quests also get StartsEnabled (0x10) so they run from a new game."""
        out = convert_QUST({'FormID': '00010602', 'RecordFlags': '0',
                            'EditorID': 'MQ01', 'DATA.Flags': '1',
                            'DATA.Priority': '30', 'StageCount': '0'})
        dnam = _find_subrecord(out, b'DNAM')
        assert len(dnam) == 12
        flags, prio, formver = struct.unpack_from('<HBB', dnam, 0)
        assert flags & 0x0001 and flags & 0x0010
        assert not flags & 0x8000, "HasDialogueData must never be set"
        assert prio == 30
        assert formver == 0

    def test_non_sge_quest_not_start_enabled(self):
        out = convert_QUST({'FormID': '00010603', 'RecordFlags': '0',
                            'EditorID': 'LateQuest', 'DATA.Flags': '0',
                            'StageCount': '0'})
        flags = struct.unpack_from('<H', _find_subrecord(out, b'DNAM'), 0)[0]
        assert flags & 0x0011 == 0

    def test_stages_and_anam(self):
        out = convert_QUST({
            'FormID': '00010604', 'RecordFlags': '0', 'EditorID': 'QTest',
            'DATA.Flags': '0', 'StageCount': '1',
            'Stage[0].Index': '10', 'Stage[0].LogCount': '1',
            'Stage[0].Log[0].Flags': '1',
            'Stage[0].Log[0].Text': 'Journal text.',
        })
        indx = _find_subrecord(out, b'INDX')
        assert len(indx) == 4, "TES5 INDX is 4 bytes (U16 index + flags + pad)"
        assert struct.unpack_from('<H', indx, 0)[0] == 10
        qsdt = _find_subrecord(out, b'QSDT')
        assert qsdt == b'\x01'  # Complete Quest flag preserved
        assert _find_subrecord(out, b'CNAM').rstrip(b'\x00') == b'Journal text.'
        assert _find_subrecord(out, b'ANAM') is not None
        assert _sub_order(out).index('NEXT') < _sub_order(out).index('INDX')


# ---------------------------------------------------------------------------
# DLBR / DLVW
# ---------------------------------------------------------------------------

class TestBranches:
    def test_dlbr_structure(self):
        out = make_dlbr(0x01F00001, 'TES4_Test_Branch', 0x01000ABC,
                        0x01000DEF, top_level=True)
        assert _sub_order(out) == ['EDID', 'QNAM', 'TNAM', 'DNAM', 'SNAM']
        assert struct.unpack('<I', _find_subrecord(out, b'DNAM'))[0] == 1
        assert struct.unpack('<I', _find_subrecord(out, b'SNAM'))[0] == 0x01000DEF
        linked = make_dlbr(0x01F00002, 'B', 0x01000ABC, 0x01000DEF,
                           top_level=False)
        assert struct.unpack('<I', _find_subrecord(linked, b'DNAM'))[0] == 0

    def test_dlvw_structure(self):
        out = make_dlvw(0x01F00003, 'TES4View', 0x01000ABC,
                        [0x01F00001], [0x01000DEF, 0x01000DF0])
        order = _sub_order(out)
        assert order == ['EDID', 'QNAM', 'BNAM', 'TNAM', 'TNAM', 'ENAM', 'DNAM']


# ---------------------------------------------------------------------------
# Quest ownership + per-INFO gating (build_dialog_groups)
# ---------------------------------------------------------------------------

class TestQuestOwnership:
    def _mini_by_type(self):
        """One shared 2-quest topic with 2 INFOs (one on an SGE quest, one
        not) and one single-quest topic."""
        return {
            'QUST': [
                {'FormID': '000A0001', 'EditorID': 'SGEQuest',
                 'DATA.Flags': '1', 'StageCount': '0'},
                {'FormID': '000A0002', 'EditorID': 'LateQuest',
                 'DATA.Flags': '0', 'StageCount': '0'},
            ],
            'DIAL': [
                {'FormID': '000B0001', 'EditorID': 'SharedTopic',
                 'DATA.Type': '0', 'QuestCount': '2',
                 'Quest[0]': '000A0001', 'Quest[1]': '000A0002'},
                {'FormID': '000B0002', 'EditorID': 'SingleTopic',
                 'DATA.Type': '0', 'QuestCount': '1',
                 'Quest[0]': '000A0002'},
            ],
            'INFO': [
                {'FormID': '000C0001', 'ParentDIAL': '000B0001',
                 'QSTI.Quest': '000A0001', 'ResponseCount': '0',
                 'ChoiceCount': '0', 'ConditionCount': '0', 'DATA.Flags': '0'},
                {'FormID': '000C0002', 'ParentDIAL': '000B0001',
                 'QSTI.Quest': '000A0002', 'ResponseCount': '0',
                 'ChoiceCount': '0', 'ConditionCount': '0', 'DATA.Flags': '0'},
                {'FormID': '000C0003', 'ParentDIAL': '000B0002',
                 'QSTI.Quest': '000A0002', 'ResponseCount': '0',
                 'ChoiceCount': '0', 'ConditionCount': '0', 'DATA.Flags': '0'},
            ],
        }

    def test_quest_ownership_and_per_info_gating(self):
        """Single-quest topics are owned by their quest (Skyrim's native
        quest-running gating = Oblivion's QSTI gating); shared topics go to
        the always-running generic quest and each INFO whose own QSTI quest
        is not Start-Game-Enabled gets a GetQuestRunning gate — Oblivion's
        per-INFO visibility re-expressed in Skyrim terms."""
        set_formid_index_offset(1)
        try:
            writer = _FakeWriter()
            seq_fids = build_dialog_groups(self._mini_by_type(), writer,
                                           npc_to_vtyp={})
            recs = {(sig, fid): rec_bytes
                    for sig, fid, rec_bytes in _walk_records(writer.groups['DIAL'])}

            generic_fid = struct.unpack_from('<I', writer.records[0][1], 12)[0]
            assert generic_fid in seq_fids

            qnam = _find_subrecord(recs[('DIAL', 0x010B0001)], b'QNAM')
            assert struct.unpack('<I', qnam)[0] == generic_fid, \
                "shared topic must be owned by the generic quest"

            qnam = _find_subrecord(recs[('DIAL', 0x010B0002)], b'QNAM')
            assert struct.unpack('<I', qnam)[0] == 0x010A0002, \
                "single-quest topic must be owned by its original quest"

            def ctda_funcs(rec_bytes):
                return [struct.unpack_from('<H', c, 8)[0]
                        for c in _find_all_subrecords(rec_bytes, b'CTDA')]

            # INFO on SGE quest: no GetQuestRunning gate needed
            assert FUNC_GET_QUEST_RUNNING not in ctda_funcs(recs[('INFO', 0x010C0001)])
            # INFO on non-SGE quest under shared topic: gated on its OWN quest
            gates = [c for c in _find_all_subrecords(recs[('INFO', 0x010C0002)], b'CTDA')
                     if struct.unpack_from('<H', c, 8)[0] == FUNC_GET_QUEST_RUNNING]
            assert len(gates) == 1
            assert struct.unpack_from('<I', gates[0], 12)[0] == 0x010A0002
            # INFO under single-quest topic: ownership gates natively
            assert FUNC_GET_QUEST_RUNNING not in ctda_funcs(recs[('INFO', 0x010C0003)])
        finally:
            set_formid_index_offset(0)

    def test_fallback_greetings_use_real_hello_subtype(self):
        """One low-priority Hello per voice type; DATA must carry the REAL
        on-disk Hello subtype (73) in the U16, category 7 in byte 1."""
        set_formid_index_offset(1)
        try:
            writer = _FakeWriter()
            build_dialog_groups(self._mini_by_type(), writer,
                                npc_to_vtyp={0x01001000: 0x01002000})
            fallback = None
            for sig, fid, rec_bytes in _walk_records(writer.groups['DIAL']):
                if sig == 'DIAL' and _find_subrecord(rec_bytes, b'EDID') \
                        == b'TES4FallbackHello\x00':
                    fallback = rec_bytes
            assert fallback is not None
            data = _find_subrecord(fallback, b'DATA')
            assert data[1] == 7
            assert struct.unpack_from('<H', data, 2)[0] == 73
            assert _find_subrecord(fallback, b'SNAM') == b'HELO'
        finally:
            set_formid_index_offset(0)


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
