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
    while pos + 6 <= len(record_bytes):
        sub_sig = record_bytes[pos:pos+4]
        sub_size = struct.unpack_from('<H', record_bytes, pos+4)[0]
        if sub_sig == sig:
            return record_bytes[pos+6:pos+6+sub_size]
        pos += 6 + sub_size
    return None


def _find_all_subrecords(record_bytes: bytes, sig: bytes) -> list:
    results = []
    pos = 24
    while pos + 6 <= len(record_bytes):
        sub_sig = record_bytes[pos:pos+4]
        sub_size = struct.unpack_from('<H', record_bytes, pos+4)[0]
        if sub_sig == sig:
            results.append(record_bytes[pos+6:pos+6+sub_size])
        pos += 6 + sub_size
    return results


def _sub_order(record_bytes: bytes) -> list:
    order = []
    pos = 24
    while pos + 6 <= len(record_bytes):
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
        """Both params shift when both are FormIDs — func 60
        GetFactionRankDifference(ptFaction, ptActor)."""
        out = convert_ctda(_tes4_ctda(func=60, p1=0x00012345, p2=0x00023456),
                           offset=1)
        assert struct.unpack_from('<I', out, 12)[0] == 0x01012345
        assert struct.unpack_from('<I', out, 16)[0] == 0x01023456

    def test_engine_fixed_formids_not_remapped(self):
        """Player (0x14) and other low engine FormIDs pass through."""
        out = convert_ctda(_tes4_ctda(p1=0x14), offset=1)
        assert struct.unpack_from('<I', out, 12)[0] == 0x14

    def test_value_params_are_never_remapped(self):
        """A non-FormID param must survive the load-order shift untouched.

        Regression: every param was remapped unconditionally, so
        GetBaseActorValue(Speechcraft=32) became FormID 0x01000020. Skyrim
        indexes its actor-value table with that param directly, so the engine
        read 16.7M entries past the end and crashed (EXCEPTION_ACCESS_VIOLATION
        in the dialogue menu) the moment any converted NPC was spoken to.
        """
        # 277 GetBaseActorValue(ptActorValue) — the crash from Nehrim's
        # trainer topics (LehrerWortgewandheit05 / INFO 00207614).
        out = convert_ctda(_tes4_ctda(func=277, p1=32), offset=1)
        assert struct.unpack_from('<I', out, 12)[0] == 32
        # 14 GetActorValue(ptActorValue)
        out = convert_ctda(_tes4_ctda(func=14, p1=32), offset=1)
        assert struct.unpack_from('<I', out, 12)[0] == 32
        # 70 GetIsSex(ptSex) / 131 GetPCIsSex(ptSex) — 0/1 enums
        for func in (70, 131):
            out = convert_ctda(_tes4_ctda(func=func, p1=1), offset=1)
            assert struct.unpack_from('<I', out, 12)[0] == 1
        # 247 GetIsUsedItemType(ptFormType)
        out = convert_ctda(_tes4_ctda(func=247, p1=41), offset=1)
        assert struct.unpack_from('<I', out, 12)[0] == 41

    def test_quest_stage_param2_not_remapped(self):
        """59 GetStageDone(ptQuest, ptQuestStage): p1 is a FormID, p2 is the
        stage NUMBER and must stay a small integer."""
        out = convert_ctda(_tes4_ctda(func=59, p1=0x00012345, p2=30), offset=1)
        assert struct.unpack_from('<I', out, 12)[0] == 0x01012345
        assert struct.unpack_from('<I', out, 16)[0] == 30

    def test_formid_param_table_matches_xedit(self):
        """Spot-check the generated table against xEdit's TES5 definitions."""
        from tes5_import.ctda_param_types import CTDA_FORMID_PARAMS
        assert CTDA_FORMID_PARAMS[72] == frozenset({1})      # GetIsID
        assert CTDA_FORMID_PARAMS[59] == frozenset({1})      # GetStageDone
        assert CTDA_FORMID_PARAMS[60] == frozenset({1, 2})   # GetFactionRankDiff
        assert 277 not in CTDA_FORMID_PARAMS                 # GetBaseActorValue
        assert 14 not in CTDA_FORMID_PARAMS                  # GetActorValue
        assert 70 not in CTDA_FORMID_PARAMS                  # GetIsSex

    def test_dropped_functions(self):
        """TES4-only / index-reused functions return None (e.g. 224 is reused
        as GetVATSMode in TES5, 104 IsYielding was removed)."""
        for func in (104, 224, 249, 264):
            assert convert_ctda(_tes4_ctda(func=func), offset=0) is None

    def test_disposition_becomes_relationship_rank(self):
        """GetDisposition maps onto Skyrim's relationship rank, not dropped.

        Skyrim's disposition system is Relationship Rank (-4..4, default 0),
        read by CONDITION 403 against the player. Oblivion's 0-100 scale tiers
        onto it, so the ORDER of a game's dialogue tiers survives: a line
        gated on high disposition stays gated more tightly than a neutral one.
        Dropping the condition instead (the old behaviour) made every tier of
        a greeting fire at once on the same NPC.

        403 is the CTDA index (opcode 0x1193 - 0x1000); the function occupies
        ROW 419 of the engine's table, and using the row number instead emits
        GetObjectiveCompleted(Quest, Integer). Vanilla Skyrim.esm uses 403 in
        298 INFO conditions and 419 in none.
        """
        cases = [(10.0, -2), (30.0, -1), (50.0, 0), (70.0, 1), (90.0, 2)]
        for disposition, want_rank in cases:
            comp = struct.unpack('<I', struct.pack('<f', disposition))[0]
            out = convert_ctda(_tes4_ctda(func=76, comp=comp), offset=0)
            assert out is not None, f'disposition {disposition} was dropped'
            assert struct.unpack_from('<H', out, 8)[0] == 403
            assert struct.unpack_from('<f', out, 4)[0] == want_rank
            # The parameter is an ACTOR (engine param type 0x06), so the player
            # is PlayerRef 0x14 -- the placed reference -- NOT the player base
            # NPC 0x7 that GetIsID (param type 0x15, ObjectID) takes. Passing
            # 0x7 handed the engine a TESNPC where it dereferenced an Actor and
            # crashed on the first GREETING (EXCEPTION_ACCESS_VIOLATION, player
            # TESNPC 0x7 in RSI). Vanilla Skyrim.esm passes 0x14 or 0, never
            # 0x7, in all 234 uses.
            assert struct.unpack_from('<I', out, 12)[0] == 0x00000014

    def test_disposition_param_survives_load_order_offset(self):
        """PlayerRef 0x14 must not be shifted onto the converted plugin.

        Engine-fixed forms below object id 0x100 pass through unchanged; a
        remapped 0x01000014 would be a dangling reference.
        """
        comp = struct.unpack('<I', struct.pack('<f', 70.0))[0]
        out = convert_ctda(_tes4_ctda(func=76, comp=comp), offset=1)
        assert struct.unpack_from('<I', out, 12)[0] == 0x00000014

    def test_synthesized_player_param_matches_engine_param_type(self):
        """A player param must match its function's engine parameter TYPE.

        The engine distinguishes references from base forms, and the right
        player id differs between them:

            type 0x04 ObjectReferenceID / 0x06 Actor -> PlayerRef  0x14
            type 0x15 ObjectID          / 0x19 Actor Base -> Player 0x07

        Feeding a base form to a function that dereferences an Actor crashes
        the process. Reusing GetIsID's 0x7 for GetRelationshipRank did exactly
        that: EXCEPTION_ACCESS_VIOLATION on the first GREETING.

        Skipped when the extracted engine tables are absent (they are produced
        from a local Skyrim install by tools/dialog_engine_extract.py).
        """
        import json
        import os
        tables = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), 'tes5_import',
            'dialog_engine_tables.json')
        if not os.path.exists(tables):
            pytest.skip('engine tables not extracted')
        with open(tables, encoding='utf-8') as fh:
            funcs = {f['ctda_index']: f for f in json.load(fh)['functions']
                     if f.get('ctda_index') is not None}

        REFERENCE_TYPES = {0x04, 0x06}
        BASE_FORM_TYPES = {0x15, 0x19}
        comp = struct.unpack('<I', struct.pack('<f', 70.0))[0]
        # (TES4 function the converter rewrites, the id it synthesizes)
        for tes4_func in (76,):          # GetDisposition -> GetRelationshipRank
            out = convert_ctda(_tes4_ctda(func=tes4_func, comp=comp), offset=0)
            assert out is not None
            emitted_func = struct.unpack_from('<H', out, 8)[0]
            emitted_param = struct.unpack_from('<I', out, 12)[0]
            spec = funcs.get(emitted_func)
            assert spec, f'function {emitted_func} not in the engine table'
            ptype = spec['params'][0]['type']
            if ptype in REFERENCE_TYPES:
                assert emitted_param == 0x00000014, (
                    f'{spec["name"]} takes {spec["params"][0]["name"]} '
                    f'(type {ptype:#04x}, a reference) but got '
                    f'{emitted_param:#x}; PlayerRef is 0x14')
            elif ptype in BASE_FORM_TYPES:
                assert emitted_param == 0x00000007, (
                    f'{spec["name"]} takes a base form but got '
                    f'{emitted_param:#x}; the Player NPC is 0x07')

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
            'Condition[1].Raw': _tes4_ctda(func=104).hex(),  # dropped
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

    def test_emotion_response_topics_are_not_converted(self):
        """Oblivion's emotion-response channels have no Skyrim equivalent.

        The engine picks these after a player line to voice the NPC's reaction;
        Skyrim has no such channel, so converting them produced player-visible
        topics ("SadGeneral", "AngerReceive", ...) hanging off every greeting.
        """
        for edid in ('SadGeneral', 'QuestionGeneral', 'FearGeneral',
                     'AngerReceive', 'HappyReceive', 'SurpriseReceive',
                     'FollowupNegative', 'FollowupPositive', 'AnswerNegative',
                     'AnswerPositive', 'AnswerStatus', 'NeutralReceive',
                     'Question'):
            assert should_skip_dial(
                {'DATA.Type': str(DIAL_TYPE_CONVERSATION), 'EditorID': edid}), \
                f'{edid} should not be converted'

        # Rumors is the ONE Oblivion conversation channel Skyrim does have
        # (subtype 2 RUMO), so it must still convert...
        assert not should_skip_dial(
            {'DATA.Type': str(DIAL_TYPE_CONVERSATION),
             'EditorID': 'INFOGENERAL'})
        # ...and CharGenEmperor sits inside the emotion block's FormID range
        # (0002410E..0002411C) but is a real main-quest conversation. This is
        # why the skip list names topics instead of skipping the range.
        assert not should_skip_dial(
            {'DATA.Type': str(DIAL_TYPE_CONVERSATION),
             'EditorID': 'CharGenEmperor'})


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

    def test_journal_quests_get_side_quest_type(self):
        """Quests with journal stages must be Type 8 (Side Quest). Type 0
        (None) is Skyrim's journal-INVISIBLE control-quest type — the quest
        never lists in the journal, can't be tracked, and its objective
        targets never produce compass/map markers."""
        out = convert_QUST({
            'FormID': '00010607', 'RecordFlags': '0', 'EditorID': 'QJournal',
            'DATA.Flags': '0', 'StageCount': '1',
            'Stage[0].Index': '10', 'Stage[0].LogCount': '1',
            'Stage[0].Log[0].Flags': '0', 'Stage[0].Log[0].Text': 'Entry.',
        })
        dnam = _find_subrecord(out, b'DNAM')
        assert struct.unpack_from('<I', dnam, 8)[0] == 8
        # dialogue/control quest without journal text stays Type 0
        out = convert_QUST({'FormID': '00010608', 'RecordFlags': '0',
                            'EditorID': 'QCtl', 'DATA.Flags': '0',
                            'StageCount': '0'})
        dnam = _find_subrecord(out, b'DNAM')
        assert struct.unpack_from('<I', dnam, 8)[0] == 0

    def test_non_sge_quest_not_start_enabled(self):
        out = convert_QUST({'FormID': '00010603', 'RecordFlags': '0',
                            'EditorID': 'LateQuest', 'DATA.Flags': '0',
                            'StageCount': '0'})
        flags = struct.unpack_from('<H', _find_subrecord(out, b'DNAM'), 0)[0]
        assert flags & 0x0011 == 0

    def test_quest_targets_become_aliases_and_objective_targets(self):
        """TES4 quest-level targets (REFR + GetStage conditions) -> one
        forced-ref alias per unique target, plus an UNCONDITIONAL QSTA(alias,
        flags) on each objective whose stage the TES4 conditions admit.

        Vanilla objective targets carry no CTDAs — the displayed objective is
        what selects the marker — so Oblivion's GetStage gates are resolved at
        build time instead of replayed at runtime.
        """
        set_formid_index_offset(1)
        try:
            out = convert_QUST({
                'FormID': '00010605', 'RecordFlags': '0', 'EditorID': 'QMark',
                'DATA.Flags': '0', 'StageCount': '1',
                'Stage[0].Index': '10', 'Stage[0].LogCount': '1',
                'Stage[0].Log[0].Flags': '0',
                'Stage[0].Log[0].Text': 'Find the thing.',
                'Target[0].FormID': '0001656A',
                'Target[0].Flags': '1',
                'Target[0].ConditionCount': '1',
                'Target[0].Condition[0].Raw':
                    _tes4_ctda(func=58, comp=0x41200000, p1=0x00010605).hex(),
            })
        finally:
            set_formid_index_offset(0)
        qsta = _find_subrecord(out, b'QSTA')
        assert qsta is not None and len(qsta) == 8
        alias, tflags = struct.unpack_from('<iB', qsta, 0)
        assert alias == 0 and tflags == 1
        assert struct.unpack('<I', _find_subrecord(out, b'ALST'))[0] == 0
        assert struct.unpack('<I', _find_subrecord(out, b'ALFR'))[0] == 0x0101656A
        assert struct.unpack('<I', _find_subrecord(out, b'ANAM'))[0] == 1
        fnam_alias = _find_all_subrecords(out, b'FNAM')[-1]
        assert struct.unpack('<I', fnam_alias)[0] & 0x0002, \
            "alias must be Optional so a fill failure can't block quest start"
        assert _find_subrecord(out, b'VTCK') is not None, \
            "VTCK is on 2687/2687 vanilla forced-ref aliases"
        order = _sub_order(out)
        assert order.index('QOBJ') < order.index('QSTA') < order.index('ANAM') \
            < order.index('ALST') < order.index('ALED')
        # Objective targets are unconditional, exactly like vanilla.
        assert not _find_all_subrecords(out, b'CTDA')

    def test_target_only_on_the_stages_its_conditions_admit(self):
        """A target gated `GetStage == 20` marks only objective 20 — not 10.

        Carrying every target onto every objective (with its conditions
        replayed as CTDAs) leaves the engine with a list whose leading entries
        are false, and it draws no marker at all: the objective shows in the
        journal but the compass and map stay empty.
        """
        set_formid_index_offset(1)
        try:
            out = convert_QUST({
                'FormID': '00010609', 'RecordFlags': '0', 'EditorID': 'QGate',
                'DATA.Flags': '0', 'StageCount': '2',
                'Stage[0].Index': '10', 'Stage[0].LogCount': '1',
                'Stage[0].Log[0].Flags': '0',
                'Stage[0].Log[0].Text': 'Go see her.',
                'Stage[1].Index': '20', 'Stage[1].LogCount': '1',
                'Stage[1].Log[0].Flags': '0',
                'Stage[1].Log[0].Text': 'Now search the cellar.',
                # target 0 lives only at stage 10, target 1 only at stage 20
                'Target[0].FormID': '0001656A', 'Target[0].Flags': '0',
                'Target[0].ConditionCount': '1',
                'Target[0].Condition[0].Raw':
                    _tes4_ctda(func=58, comp=0x41200000, p1=0x00010609).hex(),
                'Target[1].FormID': '0001656B', 'Target[1].Flags': '0',
                'Target[1].ConditionCount': '1',
                'Target[1].Condition[0].Raw':
                    _tes4_ctda(func=58, comp=0x41A00000, p1=0x00010609).hex(),
            })
        finally:
            set_formid_index_offset(0)

        # Walk the objectives and collect the alias each one marks.
        marks = {}
        pos, current = 24, None
        while pos + 6 <= len(out):
            sig = out[pos:pos + 4]
            size = struct.unpack_from('<H', out, pos + 4)[0]
            body = out[pos + 6:pos + 6 + size]
            if sig == b'QOBJ':
                current = struct.unpack('<H', body)[0]
                marks[current] = []
            elif sig == b'QSTA' and current is not None:
                marks[current].append(struct.unpack_from('<i', body, 0)[0])
            pos += 6 + size

        assert marks == {10: [0], 20: [1]}, \
            "each objective must mark only the target Oblivion gated to it"

    def test_duplicate_stage_objectives_deduped(self):
        """One objective per stage index — the engine keys objectives by
        index, and the stage fragment displays SetObjectiveDisplayed(stage)."""
        out = convert_QUST({
            'FormID': '00010606', 'RecordFlags': '0', 'EditorID': 'QDup',
            'DATA.Flags': '0', 'StageCount': '2',
            'Stage[0].Index': '10', 'Stage[0].LogCount': '2',
            'Stage[0].Log[0].Flags': '0', 'Stage[0].Log[0].Text': 'First.',
            'Stage[1].Log[1].Flags': '0', 'Stage[0].Log[1].Text': 'Second.',
            'Stage[1].Index': '10', 'Stage[1].LogCount': '1',
            'Stage[1].Log[0].Flags': '0', 'Stage[1].Log[0].Text': 'Third.',
        })
        qobjs = _find_all_subrecords(out, b'QOBJ')
        assert len(qobjs) == 1
        assert struct.unpack('<H', qobjs[0])[0] == 10

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

    def test_voice_file_prefix_rules(self):
        """Transcribed from SkyrimSE.exe (va 0x1403a6060), NOT fitted to
        Oblivion's filenames — those follow a different rule and fitting
        them yields a name the engine never requests.

            if lenQ + lenT > 25:
                if lenQ > 10: Q, T = Q[:10], T[:15]
                else:         T = T[:25 - lenQ]
        """
        from tes5_import.dialog_converter import voice_file_prefix
        # <= 25 combined: used verbatim. The old quest[:10] cap mangled
        # these into mg04restor_/fgd00joinf_ so the audio was never found.
        assert voice_file_prefix('MG04Restore', 'MG04Choice1A') \
            == 'mg04restore_mg04choice1a'
        assert voice_file_prefix('FGD00JoinFG', 'FGJoin1') \
            == 'fgd00joinfg_fgjoin1'
        assert voice_file_prefix('MQ01', 'Rats') == 'mq01_rats'
        # Exactly 25 -> still verbatim (boundary is `jbe`, i.e. <= 25).
        assert voice_file_prefix('MG04Restore', 'MG04Choice12AB') \
            == 'mg04restore_mg04choice12ab'
        # > 25 with quest > 10 -> quest[:10] + topic[:15].
        assert voice_file_prefix('ArenaDialogue', 'ArenaBetChoice1A') \
            == 'arenadialo_arenabetchoice1'
        assert voice_file_prefix('DialogueGeneric',
                                 'DialogueGenericSharedInfo') \
            == 'dialoguege_dialoguegeneric'
        # > 25 with quest <= 10 -> quest kept, topic absorbs the cut.
        assert voice_file_prefix('DA05', 'DA05SindingWhyKillTheSpriggan') \
            == 'da05_da05sindingwhykillthe'
        # No topic EditorID -> trailing underscore before the FormID.
        assert voice_file_prefix('DGIntimidateQuest', '') \
            == 'dgintimidatequest_'

    def test_voice_map_filled_with_owner_quest_prefix(self):
        """voice_map keys are INFO low-24 FormIDs; prefixes use the CONVERTED
        owning quest (generic for shared topics, original for single-quest)."""
        set_formid_index_offset(1)
        try:
            writer = _FakeWriter()
            vmap = {}
            build_dialog_groups(self._mini_by_type(), writer, npc_to_vtyp={},
                                voice_map=vmap)
            assert vmap[0x0C0001].startswith('tes4dialog'), \
                "shared-topic INFO named under the generic quest"
            assert vmap[0x0C0003] == 'latequest_singletopic'
        finally:
            set_formid_index_offset(0)

    def _greeting_by_type(self):
        """A shared GREETING (Hello bark) topic whose two INFOs are owned by two
        different quests — the case that must split into one HELO topic per
        quest (Skyrim honors only one bark topic per subtype per quest)."""
        return {
            'QUST': [
                {'FormID': '000A0001', 'EditorID': 'QuestA',
                 'DATA.Flags': '1', 'StageCount': '0'},
                {'FormID': '000A0002', 'EditorID': 'QuestB',
                 'DATA.Flags': '1', 'StageCount': '0'},
            ],
            'DIAL': [
                {'FormID': '000B00C8', 'EditorID': 'GREETING',
                 'DATA.Type': '0', 'QuestCount': '2',
                 'Quest[0]': '000A0001', 'Quest[1]': '000A0002'},
            ],
            'INFO': [
                {'FormID': '000C0001', 'ParentDIAL': '000B00C8',
                 'QSTI.Quest': '000A0001', 'ResponseCount': '0',
                 'ChoiceCount': '0', 'ConditionCount': '0', 'DATA.Flags': '0'},
                {'FormID': '000C0002', 'ParentDIAL': '000B00C8',
                 'QSTI.Quest': '000A0002', 'ResponseCount': '0',
                 'ChoiceCount': '0', 'ConditionCount': '0', 'DATA.Flags': '0'},
            ],
        }

    def test_bark_greeting_split_per_quest(self):
        """A shared GREETING topic must emit ONE HELO topic per owning quest
        (no single shared topic, no fallback). Skyrim honors only one bark
        topic of a subtype per quest, so each quest's greeting needs its own
        topic. Every emitted topic keeps the real Hello subtype (73/HELO)."""
        set_formid_index_offset(1)
        try:
            writer = _FakeWriter()
            build_dialog_groups(self._greeting_by_type(), writer,
                                npc_to_vtyp={})
            helo_owners = []
            for sig, fid, rec_bytes in _walk_records(writer.groups['DIAL']):
                if sig != 'DIAL':
                    continue
                data = _find_subrecord(rec_bytes, b'DATA')
                if data and struct.unpack_from('<H', data, 2)[0] == 73:
                    assert data[1] == 7, "Hello category must be 7 (Misc)"
                    assert _find_subrecord(rec_bytes, b'SNAM') == b'HELO'
                    qnam = _find_subrecord(rec_bytes, b'QNAM')
                    helo_owners.append(struct.unpack('<I', qnam)[0])
            # One HELO topic per owning quest (QuestA, QuestB), each distinct.
            assert len(helo_owners) == 2, helo_owners
            assert len(set(helo_owners)) == 2, "each quest owns its own HELO topic"
            # No fallback greeting topic remains.
            edids = [_find_subrecord(rb, b'EDID')
                     for sig, fid, rb in _walk_records(writer.groups['DIAL'])
                     if sig == 'DIAL']
            assert not any(e and b'Fallback' in e for e in edids)
        finally:
            set_formid_index_offset(0)


class TestAddTopicUnlocks:
    """AddTopic visibility -> unlock globals. Oblivion only shows a topic once
    it has been ADDED (data Add-Topics list, AddTopic script command, or a
    spoken line mentioning the topic's name). Skyrim has no AddTopic, so the
    conversion gates each explicitly-added topic on a GLOB the revealing
    fragments set."""

    def _by_type(self):
        return {
            'QUST': [
                {'FormID': '000A0001', 'EditorID': 'FGQuest',
                 'DATA.Flags': '1', 'StageCount': '1',
                 'Stage[0].Index': '10', 'Stage[0].LogCount': '1',
                 'Stage[0].Log[0].Flags': '0',
                 'Stage[0].Log[0].Text': 'Journal.',
                 'Stage[0].Log[0].ResultScript': 'AddTopic stageTopic'},
            ],
            'DIAL': [
                {'FormID': '000B0001', 'EditorID': 'contract',
                 'DATA.Type': '0', 'QuestCount': '1', 'Quest[0]': '000A0001',
                 'FULL': 'Contract'},
                {'FormID': '000B0002', 'EditorID': 'ratsTOPIC',
                 'DATA.Type': '0', 'QuestCount': '1', 'Quest[0]': '000A0001',
                 'FULL': 'Rats'},
                {'FormID': '000B0003', 'EditorID': 'choiceTopic',
                 'DATA.Type': '1', 'QuestCount': '1', 'Quest[0]': '000A0001',
                 'FULL': 'A choice'},
                {'FormID': '000B0004', 'EditorID': 'stageTopic',
                 'DATA.Type': '0', 'QuestCount': '1', 'Quest[0]': '000A0001',
                 'FULL': 'Stage things'},
            ],
            'INFO': [
                # Revealer: adds ratsTOPIC via data list, links choiceTopic via TCLT
                {'FormID': '000C0001', 'ParentDIAL': '000B0001',
                 'QSTI.Quest': '000A0001', 'ResponseCount': '1',
                 'Response[0].ResponseText': 'Arvena has a rat problem.',
                 'Response[0].EmotionType': '0', 'Response[0].EmotionValue': '0',
                 'Response[0].ResponseNumber': '1',
                 'AddTopic[0]': '000B0002',
                 'ChoiceCount': '1', 'Choice[0]': '000B0003',
                 'ConditionCount': '0', 'DATA.Flags': '0'},
                # Mention revealer: response text names the gated topic "Rats"
                {'FormID': '000C0002', 'ParentDIAL': '000B0004',
                 'QSTI.Quest': '000A0001', 'ResponseCount': '1',
                 'Response[0].ResponseText': 'Ask Azzan about Rats sometime.',
                 'Response[0].EmotionType': '0', 'Response[0].EmotionValue': '0',
                 'Response[0].ResponseNumber': '1',
                 'ChoiceCount': '0', 'ConditionCount': '0', 'DATA.Flags': '0'},
                # Gated topic's own INFO
                {'FormID': '000C0003', 'ParentDIAL': '000B0002',
                 'QSTI.Quest': '000A0001', 'ResponseCount': '0',
                 'ChoiceCount': '0', 'ConditionCount': '0', 'DATA.Flags': '0'},
                # Choice-target topic INFO (must never be unlock-gated)
                {'FormID': '000C0004', 'ParentDIAL': '000B0003',
                 'QSTI.Quest': '000A0001', 'ResponseCount': '0',
                 'ChoiceCount': '0', 'ConditionCount': '0', 'DATA.Flags': '0'},
            ],
        }

    def test_unlock_plan(self):
        from tes5_import.dialog_unlocks import build_unlock_plan
        plan = build_unlock_plan(self._by_type())
        # ratsTOPIC gated (data list); stageTopic gated (stage script AddTopic);
        # choiceTopic NOT gated (only a TCLT target, never explicitly added);
        # contract NOT gated (never added)
        assert plan['gated'] == {0x0B0002: 'TES4Unlock_ratsTOPIC',
                                 0x0B0004: 'TES4Unlock_stageTopic'}
        # data-list revealer + mention revealer
        assert plan['info_reveals'][0x0C0001] == ['TES4Unlock_ratsTOPIC']
        assert plan['info_reveals'][0x0C0002] == ['TES4Unlock_ratsTOPIC']
        # stage script revealer
        assert plan['stage_reveals'] == {('fgquest', 10): ['TES4Unlock_stageTopic']}

    def test_bark_revealed_topics_are_not_gated(self):
        """A topic revealed by a GREETING/HELLO info must NOT be gated: the
        bark fires on first contact, so in Oblivion it's effectively visible
        immediately (Azzan's 'Join the Fighters Guild' via his FG-ad
        greeting). A gate only risks the fragment racing the topic menu."""
        from tes5_import.dialog_unlocks import build_unlock_plan
        bt = self._by_type()
        bt['DIAL'].append({'FormID': '000B0005', 'EditorID': 'GREETING',
                           'DATA.Type': '6', 'QuestCount': '1',
                           'Quest[0]': '000A0001'})
        bt['INFO'].append({'FormID': '000C0005', 'ParentDIAL': '000B0005',
                           'QSTI.Quest': '000A0001', 'ResponseCount': '0',
                           'AddTopic[0]': '000B0002',
                           'ChoiceCount': '0', 'ConditionCount': '0',
                           'DATA.Flags': '0'})
        plan = build_unlock_plan(bt)
        assert 0x0B0002 not in plan['gated'], \
            "greeting-revealed topic must be ungated"
        assert 0x0B0004 in plan['gated'], \
            "conversation/stage-revealed topic stays gated"
        # no lingering revealer entries for the dropped global
        for gs in plan['info_reveals'].values():
            assert 'TES4Unlock_ratsTOPIC' not in gs

    def test_gated_choice_target_revealed_by_tclt_parent(self):
        """A gated topic that is also a choice target keeps its gate but the
        TCLT-parent INFO becomes a revealer, so taking the choice unlocks it
        permanently (Oblivion: choices work regardless of added state)."""
        from tes5_import.dialog_unlocks import build_unlock_plan
        bt = self._by_type()
        # make choiceTopic explicitly added by the stage script too
        bt['QUST'][0]['Stage[0].Log[0].ResultScript'] = \
            'AddTopic stageTopic\r\nAddTopic choiceTopic'
        plan = build_unlock_plan(bt)
        assert 0x0B0003 in plan['gated']
        # the TCLT parent (infoA links Choice[0]=choiceTopic) now reveals it
        assert plan['gated'][0x0B0003] in plan['info_reveals'][0x0C0001]

    def test_choice_only_topic_gets_normal_branch(self):
        """A TCLT-target topic never explicitly added is choice-only in
        Oblivion — its branch must be Normal (DNAM=0), not Top-Level, or it
        leaks into the topic menu (e.g. Azzan's 'Yes. Sign me up.')."""
        from tes5_import.dialog_unlocks import build_unlock_plan, \
            create_unlock_globals
        set_formid_index_offset(1)
        try:
            bt = self._by_type()
            plan = build_unlock_plan(bt)
            writer = _FakeWriter()
            globals_map = create_unlock_globals(writer, plan)
            build_dialog_groups(bt, writer, npc_to_vtyp={},
                                unlock_plan=plan, unlock_globals=globals_map)
            dnam_by_start = {}
            for sig, fid, rb in _walk_records(writer.groups['DLBR']):
                start = struct.unpack('<I', _find_subrecord(rb, b'SNAM'))[0]
                dnam_by_start[start] = struct.unpack(
                    '<I', _find_subrecord(rb, b'DNAM'))[0]
            assert dnam_by_start[0x010B0003] == 0, \
                "choice-only topic must be a Normal branch"
            assert dnam_by_start[0x010B0001] == 1, \
                "ordinary topic stays top-level"
            assert dnam_by_start[0x010B0002] == 1, \
                "gated (explicitly added) topic stays top-level"
        finally:
            set_formid_index_offset(0)

    def test_infos_sorted_by_quest_priority(self):
        """Oblivion picks the first passing INFO by QUEST PRIORITY (desc);
        Skyrim walks physical order — the converter must sort each topic's
        children by their own quest's priority."""
        set_formid_index_offset(1)
        try:
            bt = {
                'QUST': [
                    {'FormID': '000A0001', 'EditorID': 'LowPrio',
                     'DATA.Flags': '1', 'DATA.Priority': '11',
                     'StageCount': '0'},
                    {'FormID': '000A0002', 'EditorID': 'HighPrio',
                     'DATA.Flags': '1', 'DATA.Priority': '60',
                     'StageCount': '0'},
                ],
                'DIAL': [
                    {'FormID': '000B0001', 'EditorID': 'GREETING',
                     'DATA.Type': '6', 'QuestCount': '2',
                     'Quest[0]': '000A0001', 'Quest[1]': '000A0002'},
                ],
                'INFO': [
                    {'FormID': '000C0001', 'ParentDIAL': '000B0001',
                     'QSTI.Quest': '000A0001', 'ResponseCount': '0',
                     'ChoiceCount': '0', 'ConditionCount': '0',
                     'DATA.Flags': '0'},
                    {'FormID': '000C0002', 'ParentDIAL': '000B0001',
                     'QSTI.Quest': '000A0002', 'ResponseCount': '0',
                     'ChoiceCount': '0', 'ConditionCount': '0',
                     'DATA.Flags': '0'},
                ],
            }
            writer = _FakeWriter()
            build_dialog_groups(bt, writer, npc_to_vtyp={})
            order = [fid for sig, fid, _ in _walk_records(writer.groups['DIAL'])
                     if sig == 'INFO']
            assert order.index(0x010C0002) < order.index(0x010C0001), \
                "priority-60 quest's INFO must precede the priority-11 one"
        finally:
            set_formid_index_offset(0)

    def test_gates_and_revealer_vmads_in_output(self):
        from tes5_import.dialog_unlocks import build_unlock_plan
        from tes5_import.dialog_conditions import FUNC_GET_GLOBAL_VALUE
        set_formid_index_offset(1)
        try:
            by_type = self._by_type()
            plan = build_unlock_plan(by_type)
            writer = _FakeWriter()
            from tes5_import.dialog_unlocks import create_unlock_globals
            globals_map = create_unlock_globals(writer, plan)
            build_dialog_groups(by_type, writer, npc_to_vtyp={},
                                unlock_plan=plan, unlock_globals=globals_map)
            recs = {(sig, fid): rb
                    for sig, fid, rb in _walk_records(writer.groups['DIAL'])}

            def ctda_list(rb):
                return [(struct.unpack_from('<H', c, 8)[0],
                         struct.unpack_from('<I', c, 12)[0])
                        for c in _find_all_subrecords(rb, b'CTDA')]

            # gated topic INFO carries GetGlobalValue(unlock GLOB) == 1
            gates = [c for c in ctda_list(recs[('INFO', 0x010C0003)])
                     if c[0] == FUNC_GET_GLOBAL_VALUE]
            assert gates == [(FUNC_GET_GLOBAL_VALUE,
                              globals_map['TES4Unlock_ratsTOPIC'])]
            # choice-target topic INFO has no unlock gate
            assert not [c for c in ctda_list(recs[('INFO', 0x010C0004)])
                        if c[0] == FUNC_GET_GLOBAL_VALUE]
            # revealer INFO (no result script) still gets a VMAD binding the GLOB
            vmad = _find_subrecord(recs[('INFO', 0x010C0001)], b'VMAD')
            assert vmad is not None
            assert b'TES4_TIF__000C0001' in vmad
            assert struct.pack('<I', globals_map['TES4Unlock_ratsTOPIC']) in vmad
        finally:
            set_formid_index_offset(0)


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
