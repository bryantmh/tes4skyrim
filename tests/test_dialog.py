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

from tes5_import.base.conditions import (
    CTDA_OR,
    FUNC_GET_IS_VOICE_TYPE,
    FUNC_GET_QUEST_RUNNING,
    build_ctda,
    build_or_chain,
    convert_ctda,
    convert_ctda_list,
    has_positive_getisid,
    needs_origin_gate,
    read_getisid_fids,
)
from tes5_import.dialogue.converter import DIAL_TYPE_COMBAT, DIAL_TYPE_CONVERSATION, DIAL_TYPE_DETECTION, DIAL_TYPE_MISC, DIAL_TYPE_PERSUASION, DIAL_TYPE_SERVICE, DIAL_TYPE_TOPIC, _EDID_SUBTYPE, classify_topic, convert_DIAL, convert_INFO, make_dlbr, make_dlvw, should_skip_dial
from tes5_import.dialogue.groups import build_dialog_groups
from tes5_import.dialogue.quest import (convert_QUST,
                                        set_assigned_var_names)
from tes5_import.base.tes5_reader import records
from tes5_import.base.text_reader import set_formid_index_offset


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
        # Mirrors PluginWriter: one new master (Skyrim.esm), so index 1 is
        # this plugin's own space — the bark splitter only reuses a donor
        # DIAL FormID the plugin owns.
        self.own_index = 1

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
    def add_record(self, sig, rec_bytes):
        self.records.append((sig, rec_bytes))

    def add_raw_group(self, sig, content):
        self.groups[sig] = content


def _walk_records(blob):
    """Yield (sig, formid, full_record_bytes), descending into nested GRUPs."""
    for rec in records(blob, bodies=(), span=(0, len(blob))):
        yield (rec.sig.decode('ascii', 'replace'), rec.form_id,
               blob[rec.offset:rec.end])


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

    def test_identity_run_on_target_is_dropped_in_a_say_topic(self):
        """An identity RunOn=Target condition must DROP in a Say-driven topic.

        `identity` (GetIsID & co.) vetoes RETARGETING onto a reference — a
        GetIsID pinned to a ref compares the wrong base form and can never
        pass. But it must not veto DROPPING: Actor.Say() has no dialogue
        target, so keeping RunOn=Target is a condition that can NEVER pass.

        CharacterGen stalled at stage 26 on exactly this. The 26->27 bridge is
        one GOODBYE INFO (0005144A) gated on GetIsID(UrielSeptim)[RunOnTarget];
        Baurus says it via Actor.Say(), so it never fired and `setstage 27`
        never ran.
        """
        raw = _tes4_ctda(type_byte=0x02, func=72, p1=0x00023F2E)
        assert convert_ctda(raw, offset=1, drop_run_on_target=True) is None

    def test_identity_run_on_target_kept_outside_say_topics(self):
        """Menu dialogue DOES have a target, so the condition stays on Target
        — this is what keeps GREETINGs working (667 INFOs regressed once)."""
        raw = _tes4_ctda(type_byte=0x02, func=72, p1=0x00023F2E)
        out = convert_ctda(raw, offset=1, drop_run_on_target=False)
        assert out is not None
        assert struct.unpack_from('<I', out, 20)[0] == 1

    def test_identity_is_never_retargeted_onto_a_reference(self):
        """GetIsID must never land on RunOn=Reference (the 667-GREETING
        regression).  In a 'ref'-disposition Say topic the listener is known
        and the authored identity is statically satisfied, so the condition
        is DROPPED — the one thing it must never become is a Reference pin."""
        raw = _tes4_ctda(type_byte=0x02, func=72, p1=0x00023F2E)
        assert convert_ctda(raw, offset=1, run_on_target_ref=0x14) is None

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
        # trainer topics (LehrerWortgewandheit05 / INFO 00207614).  The param
        # is TRANSLATED between the two games' actor-value tables (Speechcraft
        # 32 -> Speech 17), never load-order remapped.
        out = convert_ctda(_tes4_ctda(func=277, p1=32), offset=1)
        assert struct.unpack_from('<I', out, 12)[0] == 17
        # 14 GetActorValue(ptActorValue)
        out = convert_ctda(_tes4_ctda(func=14, p1=32), offset=1)
        assert struct.unpack_from('<I', out, 12)[0] == 17
        # 70 GetIsSex(ptSex) / 131 GetPCIsSex(ptSex) — 0/1 enums
        for func in (70, 131):
            out = convert_ctda(_tes4_ctda(func=func, p1=1), offset=1)
            assert struct.unpack_from('<I', out, 12)[0] == 1
        # 247 GetIsUsedItemType(ptFormType)
        out = convert_ctda(_tes4_ctda(func=247, p1=41), offset=1)
        assert struct.unpack_from('<I', out, 12)[0] == 41

    def test_actor_value_param_translated_between_tables(self):
        """ptActorValue is a RAW INDEX into each game's actor-value table, and
        the two tables share no entries — TES4 0 is Strength, TES5 0 is
        Aggression; TES4 5 is Endurance, TES5 5 is Assistance.

        Regression: the index was passed through unchanged, so every skill gate
        read an unrelated value. Skills must be translated to their TES5 slot.
        """
        for tes4_av, tes5_av in ((12, 10),   # Armorer     -> Smithing
                                 (14, 6),    # Blade       -> OneHanded
                                 (16, 6),    # Blunt       -> OneHanded
                                 (24, 21),   # Mysticism   -> Illusion
                                 (28, 8),    # Marksman    -> Archery
                                 (29, 17),   # Mercantile  -> Speech
                                 (30, 14),   # Security    -> Lockpicking
                                 (32, 17)):  # Speechcraft -> Speech
            for func in (14, 277):
                out = convert_ctda(_tes4_ctda(func=func, p1=tes4_av), offset=1)
                assert out is not None
                assert struct.unpack_from('<I', out, 12)[0] == tes5_av

    def test_attribute_conditions_dropped(self):
        """SKYRIM HAS NO ATTRIBUTES, so an attribute gate must be dropped.

        Regression: joining the Morroblivion Fighters Guild is gated on
        `GetActorValue Strength >= 30 AND GetActorValue Endurance >= 30`.
        Passed through verbatim those became Aggression/Assistance — 0-3 enums
        that can never reach 30 — so the recruiter always answered "you don't
        have enough experience" and the guild was unjoinable at any level. The
        Thieves Guild (Agility/Personality) failed the same way.

        Dropping fails OPEN, which is the faithful outcome: a Skyrim character
        cannot raise an attribute at all, so enforcing the gate would lock the
        content away permanently rather than merely early.
        """
        for attr in range(8):  # Strength .. Luck
            for func in (14, 277):
                assert convert_ctda(_tes4_ctda(func=func, p1=attr),
                                    offset=1) is None

    def test_quest_stage_param2_not_remapped(self):
        """59 GetStageDone(ptQuest, ptQuestStage): p1 is a FormID, p2 is the
        stage NUMBER and must stay a small integer."""
        out = convert_ctda(_tes4_ctda(func=59, p1=0x00012345, p2=30), offset=1)
        assert struct.unpack_from('<I', out, 12)[0] == 0x01012345
        assert struct.unpack_from('<I', out, 16)[0] == 30

    def test_formid_param_table_matches_xedit(self):
        """Spot-check the generated table against xEdit's TES5 definitions."""
        from tes5_import.generated.ctda_param_types import CTDA_FORMID_PARAMS
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

    def test_chargen_fallback_strips_only_the_choice_gate(self):
        """The fail-open fallback for an all-gated chargen topic: a copy of
        the first gated INFO with a new FormID and ONLY the choice-global
        CTDA removed.  Without it, any miss in the choice chain hid the
        Emperor's whole post-birthsign topic (all 13 INFOs failed together)
        and the quest soft-locked at stage 44."""
        from tes5_import.dialogue.groups import _strip_chargen_choice_gate
        from tes5_import.base.writer import (pack_record, pack_subrecord,
                                        pack_string_subrecord)
        glob_fid = 0x0118E177
        gate = struct.pack('<B3xIHHIIII I', 0,
                           struct.unpack('<I', struct.pack('<f', 6.0))[0],
                           74, 0, glob_fid, 0, 0, 0, 0xFFFFFFFF)
        keep = struct.pack('<B3xIHHIIII I', 0,
                           struct.unpack('<I', struct.pack('<f', 44.0))[0],
                           58, 0, 0x0102466E, 0, 0, 0, 0xFFFFFFFF)
        subs = pack_string_subrecord('NAM1', 'Your stars are not mine.')
        subs += pack_subrecord('CTDA', keep)
        subs += pack_subrecord('CTDA', gate)
        rec = pack_record('INFO', 0x01032472, 0, subs)
        out = _strip_chargen_choice_gate(rec, 0x0118E160, {glob_fid})
        assert out, 'fallback build failed'
        # New FormID, one CTDA gone, the stage gate and text intact.
        assert struct.unpack_from('<I', out, 12)[0] == 0x0118E160
        assert out.count(b'CTDA') == 1
        assert struct.unpack_from('<I', rec, 4)[0] - 38 == \
            struct.unpack_from('<I', out, 4)[0]   # 6+32 bytes removed
        assert b'Your stars are not mine.' in out

    def test_chargen_choice_conditions_read_the_menu_global(self):
        """GetIsPlayerBirthsign (224) is dead in Skyrim, so the Emperor's 13
        'Your stars are not mine. Today the <sign>...' INFOs all fell to the
        first one regardless of the pick.  With the chargen-menu plan
        registered, the condition becomes GetGlobalValue(<choice GLOB>) ==
        menu index + 1 — the same value the converted ShowBirthsignMenu
        writes on selection."""
        from tes5_import.base.conditions import set_chargen_choice
        glob_fid = 0x0118E177
        set_chargen_choice({224: (glob_fid, {0x01FD93: 5})})
        try:
            one = 0x3F800000    # 1.0f raw
            # == 1 (the authored form): passes only when the sign matches.
            out = convert_ctda(_tes4_ctda(func=224, p1=0x0001FD93, comp=one),
                               offset=1)
            assert out is not None
            assert struct.unpack_from('<H', out, 8)[0] == 74  # GetGlobalValue
            assert struct.unpack_from('<I', out, 12)[0] == glob_fid
            assert struct.unpack_from('<f', out, 4)[0] == 6.0  # index 5 + 1
            assert (out[0] >> 5) == 0                          # ==
            # == 0 (negated): must become != index+1.
            out = convert_ctda(_tes4_ctda(func=224, p1=0x0001FD93, comp=0),
                               offset=1)
            assert (out[0] >> 5) == 1                          # !=
            assert struct.unpack_from('<f', out, 4)[0] == 6.0
            # A sign that never made the menu: condition dropped as before.
            assert convert_ctda(_tes4_ctda(func=224, p1=0x0001AAAA, comp=one),
                                offset=1) is None
        finally:
            set_chargen_choice({})

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
        from a local Skyrim install by tools/disasm/dialog_engine_extract.py).
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

    #: Charactergen stage 17 log entry 1: GetQuestVariable(0002466E, 19) == 1.
    DEBUG_CTDA = '000000000000803f4f0000006e4602001300000000000000'

    def _chargen(self):
        """A one-stage Charactergen export whose entry 1 is the debug note."""
        return {
            'FormID': '0002466E', 'RecordFlags': '0',
            'EditorID': 'Charactergen', 'DATA.Flags': '0', 'StageCount': '1',
            'Stage[0].Index': '17', 'Stage[0].LogCount': '2',
            'Stage[0].Log[0].Flags': '0',
            'Stage[0].Log[1].Flags': '0',
            'Stage[0].Log[1].Text': 'DEBUG: Stage 17: Emperor approaches',
            'Stage[0].Log[1].ConditionCount': '1',
            'Stage[0].Log[1].Condition[0].Raw': self.DEBUG_CTDA,
        }

    def test_stage_log_entry_conditions_are_written(self):
        """A log entry's own CTDAs decide whether its text displays, and the
        importer used to drop them -- which is why 24 'DEBUG:'/'TEMP:' notes
        reached the player's journal during Oblivion's chargen. The variable
        name rides along as CIS2 so Skyrim can read it.

        See: docs/commentary/tes5_import_quest.md#stage-log-entry-conditions
        """
        sv = {0x02466E: {19: 'debug'}}
        out = convert_QUST(self._chargen(), script_vars=sv)
        assert len(_find_all_subrecords(out, b'CTDA')) == 1
        assert b'debug' in _find_subrecord(out, b'CIS2').lower()
        assert b'DEBUG: Stage 17' in _find_subrecord(out, b'CNAM')

    def test_never_assigned_gate_mints_no_objective(self):
        """The journal line is gated by its CTDA, but a HUD objective carries
        no condition of its own -- so an entry gated on a variable nothing
        assigns must not become one.

        See: docs/commentary/tes5_import_quest.md#stage-log-entry-conditions
        """
        sv = {0x02466E: {19: 'debug'}}
        set_assigned_var_names({'convtimer'})
        try:
            out = convert_QUST(self._chargen(), script_vars=sv)
        finally:
            set_assigned_var_names(None)
        assert _find_all_subrecords(out, b'QOBJ') == []

    def test_assigned_gate_still_mints_its_objective(self):
        """THE REGRESSION THAT MATTERS. Gating on a script variable is the
        ORDINARY way Oblivion picks between journal variants (SE46's Mania vs
        Dementia lines). Only a NEVER-ASSIGNED variable is a debug switch.

        See: docs/commentary/tes5_import_quest.md#stage-log-entry-conditions
        """
        sv = {0x02466E: {19: 'debug'}}
        set_assigned_var_names({'debug'})
        try:
            out = convert_QUST(self._chargen(), script_vars=sv)
        finally:
            set_assigned_var_names(None)
        assert len(_find_all_subrecords(out, b'QOBJ')) == 1

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

    def test_morrowind_journal_quest_record(self):
        """A journal QUST carries an objective per page and keeps its id.

        Both are what kept the journal empty: a QUST with no objectives never
        displays its name however its stages are set, and `derive_formid`
        already returns a final id, so remapping it again pushed the record
        past the plugin's own master index.
        """
        from tes5_import.dialogue.quest_morrowind import as_record
        quest = {'id': 'TR_m3_FG', 'name': 'Cursing Like a Witch',
                 'stages': {10: ('Sharnoga asked for help.', False),
                            40: ('I dealt with the witch.', True)}}
        set_formid_index_offset(1)
        try:
            out = as_record(quest, 0x0418E9F6)
        finally:
            set_formid_index_offset(0)
        assert struct.unpack_from('<I', out, 12)[0] == 0x0418E9F6
        qobj = _find_all_subrecords(out, b'QOBJ')
        assert [struct.unpack('<H', q)[0] for q in qobj] == [10, 40]
        assert len(_find_all_subrecords(out, b'NNAM')) == 2
        assert struct.unpack_from('<I', _find_subrecord(out, b'DNAM'),
                                  8)[0] == 8
        assert _find_all_subrecords(out, b'QSDT')[1][0] == 0x01

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


class TestAuthoredObjectives:
    """FO3/FNV Objective[] blocks are written as authored, not derived.

    See docs/commentary/tes5_import_quest.md#authored-objectives.
    """

    #: FNV GetDisabled (35), Run On 2 (Reference) 00177D36 -- a real VMQ01 target gate.
    REF_CTDA = '000000000000803f23000000000000000000000002000000367d1700'

    def _rec(self):
        """VMQ01 in miniature: a stage with no CNAM and two authored objectives."""
        return {
            'FormID': '000842DD', 'RecordFlags': '0', 'EditorID': 'VMQ01',
            'DATA.Flags': '0', 'DATA.Priority': '100', 'DATA.Delay': '0',
            'StageCount': '1', 'Stage[0].Index': '10', 'Stage[0].LogCount': '1',
            'Stage[0].Log[0].Flags': '0',
            'ObjectiveCount': '2',
            'Objective[0].Index': '40', 'Objective[0].Text': 'Head to Novac.',
            'Objective[0].TargetCount': '2',
            'Objective[0].Target[0].FormID': '0009289B',
            'Objective[0].Target[0].Flags': '1',
            'Objective[0].Target[0].ConditionCount': '1',
            'Objective[0].Target[0].Condition[0].Raw': self.REF_CTDA,
            'Objective[0].Target[1].FormID': '000CDF13',
            'Objective[0].Target[1].Flags': '0',
            'Objective[1].Index': '50', 'Objective[1].Text': 'Intercept the Khans.',
            'Objective[1].TargetCount': '1',
            'Objective[1].Target[0].FormID': '0009289B',
            'Objective[1].Target[0].Flags': '1',
        }

    def test_objectives_keep_their_authored_index_and_text(self):
        """QOBJ is the authored index; NNAM the authored line, verbatim."""
        out = convert_QUST(self._rec())
        qobjs = [struct.unpack('<H', q)[0] for q in _find_all_subrecords(out, b'QOBJ')]
        assert qobjs == [40, 50]
        nnams = [n.rstrip(b'\0') for n in _find_all_subrecords(out, b'NNAM')]
        assert nnams == [b'Head to Novac.', b'Intercept the Khans.']

    def test_targets_share_aliases_and_keep_their_conditions(self):
        """One alias per distinct target; the CTDA follows its QSTA with Run On kept."""
        out = convert_QUST(self._rec())
        qstas = _find_all_subrecords(out, b'QSTA')
        assert [struct.unpack_from('<i', q)[0] for q in qstas] == [0, 1, 0]
        assert [q[4] for q in qstas] == [1, 0, 1]
        assert struct.unpack('<I', _find_subrecord(out, b'ANAM'))[0] == 2
        ctda = _find_subrecord(out, b'CTDA')
        assert len(ctda) == 32
        assert struct.unpack_from('<H', ctda, 8)[0] == 35
        assert struct.unpack_from('<II', ctda, 20) == (2, 0x00177D36)
        order = _sub_order(out)
        assert order.index('CTDA') == order.index('QSTA') + 1

    def test_authored_objectives_make_the_quest_a_journal_quest(self):
        """No CNAM anywhere, yet the quest must list: type 8, not 0."""
        dnam = _find_subrecord(convert_QUST(self._rec()), b'DNAM')
        assert struct.unpack_from('<I', dnam, 8)[0] == 8


class TestFalloutConditions:
    """A 28-byte CTDA is Fallout's: function remapped, Run On carried.

    See docs/commentary/tes5_import_conditions.md#fallout-ctda.
    """

    def test_moved_function_is_renumbered(self):
        """GetIsVoiceType is 427 in FNV and 426 in Skyrim."""
        raw = bytes.fromhex('000000000000803fab010000dd420800000000000000000000000000')
        out = convert_ctda(raw)
        assert struct.unpack_from('<H', out, 8)[0] == 426

    def test_absent_function_is_dropped(self):
        """GetObjectiveCompleted (420) has no Skyrim function."""
        raw = bytes.fromhex('000000000000803fa4010000dd420800000000000000000000000000')
        assert convert_ctda(raw) is None

    def test_tes4_index_at_a_fallout_slot_is_not_misread(self):
        """A 24-byte raw is TES4: 427 there is GetPlantedExplosive and stays put."""
        raw = bytes.fromhex('000000000000803fab010000dd42080000000000000000')
        assert struct.unpack_from('<H', convert_ctda(raw), 8)[0] == 427

    def test_run_on_target_takes_the_tes4_target_path(self):
        """Fallout Run On 1 is TES4's run-on-target bit, dropped under drop_run_on_target."""
        raw = bytes.fromhex('000000000000803f2e000000000000000000000001000000'
                            '00000000')
        assert struct.unpack_from('<I', convert_ctda(raw), 20)[0] == 1
        assert convert_ctda(raw, drop_run_on_target=True) is None


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
        from tes5_import.dialogue.converter import voice_file_prefix
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
                {'FormID': '000C0006', 'ParentDIAL': '000B0003',
                 'ResultScriptEnd': 'AddTopic ratsTOPIC',
                 'QSTI.Quest': '000A0001', 'ResponseCount': '0',
                 'ChoiceCount': '0', 'ConditionCount': '0', 'DATA.Flags': '0'},
            ],
        }

    def test_unlock_plan(self):
        from tes5_import.dialogue.unlocks import build_unlock_plan
        plan = build_unlock_plan(self._by_type())
        # ratsTOPIC gated (data list); stageTopic gated (stage script AddTopic);
        # choiceTopic NOT gated (only a TCLT target, never explicitly added);
        # contract NOT gated (never added)
        assert plan['gated'] == {0x0B0002: 'TES4Unlock_ratsTOPIC',
                                 0x0B0004: 'TES4Unlock_stageTopic'}
        # data-list revealer + mention revealer
        assert plan['info_reveals'][0x0C0001] == ['TES4Unlock_ratsTOPIC']
        assert plan['info_reveals'][0x0C0002] == ['TES4Unlock_ratsTOPIC']
        assert plan['info_reveals'][0x0C0006] == ['TES4Unlock_ratsTOPIC']
        # stage script revealer
        assert plan['stage_reveals'] == {('fgquest', 10): ['TES4Unlock_stageTopic']}

    def _with_greeting(self, bt, add_topic):
        """Append a GREETING topic whose INFO explicitly adds `add_topic`."""
        bt['DIAL'].append({'FormID': '000B0005', 'EditorID': 'GREETING',
                           'DATA.Type': '6', 'QuestCount': '1',
                           'Quest[0]': '000A0001'})
        bt['INFO'].append({'FormID': '000C0005', 'ParentDIAL': '000B0005',
                           'QSTI.Quest': '000A0001', 'ResponseCount': '0',
                           'AddTopic[0]': add_topic,
                           'ChoiceCount': '0', 'ConditionCount': '0',
                           'DATA.Flags': '0'})
        return bt

    def test_bark_only_revealed_topics_are_not_gated(self):
        """A topic revealed ONLY by a GREETING/HELLO info must NOT be gated:
        the bark fires on first contact, so in Oblivion it's effectively
        visible immediately (Azzan's 'Join the Fighters Guild' via his FG-ad
        greeting). A gate only risks the fragment racing the topic menu."""
        from tes5_import.dialogue.unlocks import build_unlock_plan
        bt = self._by_type()
        # stageTopic is otherwise revealed only by the quest-stage script; give
        # it a bark revealer and drop the stage one so the bark is its sole
        # explicit revealer.
        bt['QUST'][0]['Stage[0].Log[0].ResultScript'] = ''
        plan = build_unlock_plan(self._with_greeting(bt, '000B0004'))
        assert 0x0B0004 not in plan['gated'], \
            "bark-only-revealed topic must be ungated"
        assert 0x0B0002 in plan['gated'], \
            "conversation-revealed topic stays gated"
        # no lingering revealer entries for the dropped global
        for gs in plan['info_reveals'].values():
            assert 'TES4Unlock_stageTopic' not in gs

    def test_topic_revealed_by_both_bark_and_conversation_stays_gated(self):
        """A greeting revealer belongs to whichever NPC that greeting is gated
        to, and says nothing about a DIFFERENT NPC whose reveal comes from a
        topic line.  Vanilla `contract` is added by three greetings AND by the
        Fighters Guild join line; ungating it on the greetings' account
        detached it from the join, desynchronising the menu.  So a topic with
        BOTH revealer kinds keeps its gate — the reveal is then explicit and
        idempotent from either side."""
        from tes5_import.dialogue.unlocks import build_unlock_plan
        # ratsTOPIC is already added by the `contract` conversation INFO.
        bt = self._with_greeting(self._by_type(), '000B0002')
        plan = build_unlock_plan(bt)
        assert 0x0B0002 in plan['gated'], \
            "topic with a conversation revealer must keep its gate"
        # both revealers set the same global
        gname = plan['gated'][0x0B0002]
        assert gname in plan['info_reveals'][0x0C0001], 'conversation revealer'
        assert gname in plan['info_reveals'][0x0C0005], 'bark revealer'

    def test_gated_choice_target_revealed_by_tclt_parent(self):
        """A gated topic that is also a choice target keeps its gate but the
        TCLT-parent INFO becomes a revealer, so taking the choice unlocks it
        permanently (Oblivion: choices work regardless of added state)."""
        from tes5_import.dialogue.unlocks import build_unlock_plan
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
        from tes5_import.dialogue.unlocks import build_unlock_plan, \
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
        from tes5_import.dialogue.unlocks import build_unlock_plan
        from tes5_import.base.conditions import FUNC_GET_GLOBAL_VALUE
        set_formid_index_offset(1)
        try:
            by_type = self._by_type()
            plan = build_unlock_plan(by_type)
            writer = _FakeWriter()
            from tes5_import.dialogue.unlocks import create_unlock_globals
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


class TestOriginGate:
    """Cross-plugin dialogue containment.

    Two converted plugins loaded together (Oblivion.esm + Nehrim.esm) share the
    whole condition namespace: Skyrim arbitrates dialogue across every loaded
    file, not per-plugin. A line is only scoped to its own plugin if some
    condition POSITIVELY names a form that plugin owns.
    """

    @staticmethod
    def _ctda(func, operator=0x00, comp=1.0, param1=0x00A082,
              run_on_target=False):
        type_byte = operator | (0x02 if run_on_target else 0x00)
        return (bytes([type_byte, 0, 0, 0]) + struct.pack('<f', comp)
                + struct.pack('<H', func) + bytes(2)
                + struct.pack('<I', param1) + bytes(4)).hex()

    def test_run_on_target_pin_does_not_scope_the_speaker(self):
        """A Target-side test is about the LISTENER, not who is talking.

        `GetIsID(PlayerRef)[Target] == 1` just means "the addressee is the
        player" — true of every conversation. Oblivion attaches it to greeting
        and rumour lines; counting it as a pin left 55 INFOs reachable by any
        actor in the load order.
        """
        rec = {'Condition[0].Raw': self._ctda(72, param1=0x00000007,
                                              run_on_target=True)}
        assert needs_origin_gate(rec)

    def test_subject_side_pin_still_scopes(self):
        """The same function on the SUBJECT does scope, and must stay ungated."""
        rec = {'Condition[0].Raw': self._ctda(72, run_on_target=False)}
        assert not needs_origin_gate(rec)

    def test_positive_identity_pin_needs_no_gate(self):
        """A positive test on a plugin-owned form excludes foreign actors.

        GetIsClass(68) counts: CLAS records are converted into the plugin at
        its own load-order index and the param takes the ordinary load-order
        remap, unlike GetIsRace whose param is rewritten to a vanilla race.
        """
        # GetIsClass / GetInFaction / GetIsID / GetFactionRank
        for func in (68, 71, 72, 73):
            rec = {'Condition[0].Raw': self._ctda(func)}
            assert not needs_origin_gate(rec), f'func {func} should count as pinned'

    def test_negated_identity_is_an_exclusion_not_an_audience(self):
        """GetIsID(X) == 0 means "anyone EXCEPT X" — it scopes nothing.

        Oblivion's Rumors channel (INFOGENERAL, 1854 lines) is built almost
        entirely this way. Treating it as pinned let 395 rumour lines reach
        Nehrim NPCs, which is what showed up in-game as the Oblivion Rumors
        topic on a Nehrim NPC.
        """
        assert needs_origin_gate({'Condition[0].Raw': self._ctda(72, comp=0.0)})
        assert needs_origin_gate({'Condition[0].Raw': self._ctda(72, 0x20, 1.0)})
        assert needs_origin_gate({'Condition[0].Raw': self._ctda(71, comp=0.0)})

    def test_race_and_cell_do_not_scope_across_plugins(self):
        """These do not resolve to a form this plugin uniquely owns.

        GetIsRace's param is rewritten to a VANILLA Skyrim race every converted
        plugin's NPCs share, so "GenericImperial" means every Imperial in the
        load order. Race was Oblivion's plugin boundary only because Oblivion
        was the only file loaded. GetInCell's param is a cell name, not an
        owned form reference.
        """
        for func in (67, 69):              # GetInCell / GetIsRace
            rec = {'Condition[0].Raw': self._ctda(func)}
            assert needs_origin_gate(rec), f'func {func} must not count as pinned'

    def test_unconditioned_line_is_gated(self):
        assert needs_origin_gate({})

    def test_one_positive_pin_anywhere_is_enough(self):
        """An exclusion alongside a real pin still leaves the line scoped."""
        rec = {'Condition[0].Raw': self._ctda(72, comp=0.0),
               'Condition[1].Raw': self._ctda(72, comp=1.0)}
        assert not needs_origin_gate(rec)


class TestPluginAuthoredRaceConditionsSurvive:
    """A GetIsRace naming a race the PLUGIN adds must not be dropped.

    TES4_RACE_FID_TO_EDID holds only the 15 vanilla Oblivion races, so
    Morroblivion's mwBMRieklingRace resolved to None and the whole condition
    was deleted.  A deleted condition fails OPEN: mwGenericRieklingGreeting's
    three greetings carry no INFO conditions of their own, so with the
    quest-level race gate gone they matched every actor in the plugin, and that
    quest's priority (47) outranks fbmwMSGreetings (44).  Fargoth, a Wood Elf,
    greeted the player with "What is it, human?" and so never played the
    greeting whose fragment unlocks his "ring" topic.

    The param must resolve exactly as _resolve_npc_race does, fallback
    included, because that is the race the actors were actually converted to.
    """

    @staticmethod
    def _race_ctda(race_fid: int) -> str:
        import struct
        return (bytes(4)
                + struct.pack('<f', 1.0)
                + struct.pack('<I', 69)          # GetIsRace
                + struct.pack('<I', race_fid)
                + bytes(8)).hex()

    def _params(self, race_fid: int) -> list:
        import struct
        from tes5_import.base.conditions import convert_ctda_list_with_strings
        rec = {'FormID': '010628F9', 'ConditionCount': '1',
               'Condition[0].Raw': self._race_ctda(race_fid)}
        out = convert_ctda_list_with_strings(rec, {}, 0x01000000)
        return [struct.unpack_from('<I', c, 12)[0] for c, _s in out]

    def test_plugin_authored_race_is_kept_not_dropped(self):
        """mwBMRieklingRace -> the same DEFAULT_RACE its NPCs converted to."""
        from tes5_import.base.constants import DEFAULT_RACE
        assert self._params(0x01A804D3) == [DEFAULT_RACE]

    def test_vanilla_race_keeps_its_own_skyrim_race(self):
        """WoodElf must NOT collapse onto the fallback -- that separation is
        what keeps the Riekling gate off Fargoth."""
        from tes5_import.base.constants import DEFAULT_RACE
        from tes5_import.base.equivalents import RACE_MAP
        got = self._params(0x000223C8)           # WoodElf
        assert got == [RACE_MAP['WoodElf']]
        assert got != [DEFAULT_RACE]


class TestVmConditionsEvaluateLast:
    """GetVMQuestVariable(629)/GetVMScriptVariable(630) cross into the Papyrus
    VM per evaluation, and the engine walks a topic's INFOs evaluating each
    one's conditions in order until the first failure — so a VM read placed
    before the cheap native gates runs for every candidate INFO on every
    Say() (CharGenMain: 59 INFOs, one visible main-thread hitch per line).
    AND-joined units are order-independent, so the converter moves VM reads
    last; an OR-group is one unit and must move (or stay) whole.
    """

    @staticmethod
    def _raw(func: int, p1: int, p2: int, or_flag: bool = False) -> str:
        import struct
        return (bytes([0x01 if or_flag else 0x00]) + bytes(3)
                + struct.pack('<f', 1.0)
                + struct.pack('<I', func)
                + struct.pack('<I', p1)
                + struct.pack('<I', p2)
                + bytes(4)).hex()

    def _convert(self, raws):
        from tes5_import.base.conditions import convert_ctda_list_with_strings
        rec = {'FormID': '01000001', 'ConditionCount': str(len(raws))}
        for i, raw in enumerate(raws):
            rec[f'Condition[{i}].Raw'] = raw
        # quest 0x2466E with var index 1 named convCount (the strings path)
        script_vars = {0x2466E: {1: 'convCount'}}
        out = convert_ctda_list_with_strings(rec, script_vars, 1)
        import struct
        return [struct.unpack_from('<H', c, 8)[0] for c, _s in out]

    def test_quest_variable_read_moves_after_getisid(self):
        funcs = self._convert([
            self._raw(79, 0x2466E, 1),      # GetQuestVariable -> 629 (VM)
            self._raw(72, 0x23F2B, 0),      # GetIsID (native)
        ])
        assert funcs == [72, 629]

    def test_or_group_containing_vm_read_moves_whole(self):
        funcs = self._convert([
            self._raw(79, 0x2466E, 1, or_flag=True),  # VM, ORed with next
            self._raw(58, 0x2466E, 0),                # GetStage, same group
            self._raw(72, 0x23F2B, 0),                # GetIsID (native)
        ])
        # the OR pair stays intact and moves after the standalone native
        assert funcs == [72, 629, 58]

    def test_native_only_lists_are_untouched(self):
        funcs = self._convert([
            self._raw(58, 0x2466E, 0),
            self._raw(72, 0x23F2B, 0),
        ])
        assert funcs == [58, 72]


if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])


class TestPlayerScriptQuest:
    """A TES4 script attached to the player's BASE record is rehosted on a
    start-game-enabled quest holding a reference alias forced to PlayerRef
    (0x00000014) — the mechanism 71 vanilla Skyrim quests use, and the only one
    available: the acting player is PlayerRef, whose signature is PLYR (a
    plugin cannot author an override) and whose base is Skyrim's own 0x07,
    never the converted plugin's shifted copy."""

    def _fake_plan(self, scripts):
        from tes5_import.base import object_scripts as object_scripts
        object_scripts._PLAYER_ALIAS_SCRIPTS.clear()
        object_scripts._PLAYER_ALIAS_SCRIPTS.extend(scripts)

    def test_no_quest_when_no_player_script(self):
        from tes5_import.dialogue.converter import make_player_script_quest
        self._fake_plan([])
        writer = _FakeWriter()
        assert make_player_script_quest(writer) == 0
        assert writer.records == []

    def test_quest_is_sge_with_a_playerref_alias(self):
        from tes5_import.dialogue.converter import make_player_script_quest
        self._fake_plan([('TES4_GlobalplayerScript', {})])
        writer = _FakeWriter()
        fid = make_player_script_quest(writer)
        assert fid
        sig, rec = writer.records[0]
        assert sig == 'QUST'
        body = rec[24:]
        # StartGameEnabled (0x0001) so the .seq file starts it on a new game.
        dnam = _find_subrecord(rec, b'DNAM')
        assert struct.unpack_from('<H', dnam, 0)[0] & 0x01
        # The alias is forced onto PlayerRef itself.
        assert struct.unpack('<I', _find_subrecord(rec, b'ALFR'))[0] == 0x00000014
        assert _find_subrecord(rec, b'ALID').rstrip(b'\x00') == b'Player'
        assert struct.unpack('<I', _find_subrecord(rec, b'ALST'))[0] == 0
        # Next Alias ID must account for the one we just wrote.
        assert struct.unpack('<I', _find_subrecord(rec, b'ANAM'))[0] == 1

    def test_subrecord_order_matches_vanilla(self):
        """EDID VMAD FULL DNAM — unanimous across all 912 vanilla QUSTs that
        carry a VMAD."""
        from tes5_import.dialogue.converter import make_player_script_quest
        self._fake_plan([('TES4_GlobalplayerScript', {})])
        writer = _FakeWriter()
        make_player_script_quest(writer)
        rec = writer.records[0][1]
        order = [rec[i:i + 4] for i in range(24, len(rec))
                 if rec[i:i + 4] in (b'EDID', b'VMAD', b'FULL', b'DNAM')]
        assert order[:4] == [b'EDID', b'VMAD', b'FULL', b'DNAM']

    def test_script_is_bound_to_the_alias_not_the_quest(self):
        """Vanilla puts the script in the VMAD's Aliases array (verified by
        parsing JailQuest / TutorialEnchanting out of Skyrim.esm), so the
        top-level Scripts array stays empty."""
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), '..'))
        from tools.dialog.quest_walkthrough import parse_vmad, parse_qust_alias_scripts
        from tes5_import.dialogue.converter import make_player_script_quest
        self._fake_plan([('TES4_GlobalplayerScript', {})])
        writer = _FakeWriter()
        make_player_script_quest(writer)
        vmad = _find_subrecord(writer.records[0][1], b'VMAD')
        top, tail = parse_vmad(vmad)
        assert top == []
        alias_scripts = parse_qust_alias_scripts(tail)
        assert [n for n, _p in alias_scripts] == ['TES4_GlobalplayerScript']


class TestQuestFragmentPropertiesAllBind:
    """Every property a QF_ quest fragment declares must be BOUND.

    An unbound VMAD property reads None for the whole session and the first use
    aborts the ENTIRE Papyrus function. convert_QUST once merged the whole
    well-known registry into every scripted quest; replacing that with a
    per-declared-name lookup fixed 100+ MB of bloat but bound ONLY names the
    registry held. Player is in no registry, so it stopped binding in 18 quests
    -- `UrielSeptimRef.SetLookAt(Player)` then aborted Charactergen stage 12
    before it unlocked CGEmperor01-24, leaving the Emperor with only 'Rumors'.
    """

    def _resolve(self, declared, well_known=None):
        from tes5_import.dialogue.quest import _resolve_declared_properties
        return _resolve_declared_properties(declared, well_known)

    def test_player_binds_to_playerref(self):
        """PlayerRef 0x14, never the converted TES4 player NPC_."""
        assert self._resolve({'Player'}) == {'Player': 0x14}

    def test_playerref_spelling_also_binds(self):
        assert self._resolve({'PlayerRef'}) == {'PlayerRef': 0x14}

    def test_engine_global_keeps_vanilla_formid(self):
        from tes5_import.base.object_scripts import ENGINE_GLOBAL_FORMIDS
        out = self._resolve({'GameHour'})
        assert out['GameHour'] == ENGINE_GLOBAL_FORMIDS['gamehour']

    def test_well_known_record_binds(self):
        out = self._resolve({'TES4Fame'}, well_known={'TES4Fame': 0x0100BEEF})
        assert out == {'TES4Fame': 0x0100BEEF}

    def test_player_wins_over_a_same_named_registry_entry(self):
        """The engine's player must never be displaced by a registry hit."""
        out = self._resolve({'Player'}, well_known={'Player': 0x01000007})
        assert out == {'Player': 0x14}

    def test_unresolvable_name_is_left_unbound(self):
        """A name that resolves to nothing is omitted, not bound to zero."""
        assert self._resolve({'NoSuchRecord'}) == {}

    def test_no_declared_properties_yields_nothing(self):
        assert self._resolve(set()) == {}
        assert self._resolve(None) == {}


# ---------------------------------------------------------------------------
# NPC-to-NPC conversation chains (npc_conversations.py)
# ---------------------------------------------------------------------------

class TestNpcConversationChains:
    """Oblivion's engine-scheduled NPC-to-NPC conversations have no Skyrim
    scheduler; quest-advancing chains are replayed by a generated driver
    (CharacterGen 26->27 is the canonical case). The plan is the mirroring
    contract between the importer (VMAD bindings) and the script pipeline
    (generated .psc) — these tests pin the analysis both sides share."""

    A_BASE, B_BASE = 0x000AAA01, 0x000AAA02
    QUEST = 0x000AAA10
    HELLO_D, MID_D, GBYE_D = 0x000000D2, 0x000AAA20, 0x000AAA21

    @staticmethod
    def _f(v: float) -> int:
        return struct.unpack('<I', struct.pack('<f', v))[0]

    def _info(self, fid, parent, text, conds, choices=(), result='', ns='0'):
        rec = {'Signature': 'INFO', 'FormID': f'{fid:08X}',
               'ParentDIAL': f'{parent:08X}', 'DATA.DialogType': '1',
               'DATA.NextSpeaker': ns, 'DATA.Flags': '0',
               'QSTI.Quest': f'{self.QUEST:08X}',
               'Response[0].ResponseText': text,
               'ConditionCount': str(len(conds))}
        for i, c in enumerate(conds):
            rec[f'Condition[{i}].Raw'] = c.hex()
        rec['ChoiceCount'] = str(len(choices))
        for i, ch in enumerate(choices):
            rec[f'Choice[{i}]'] = f'{ch:08X}'
        if result:
            rec['ResultScript'] = result
        return rec

    def _by_type(self, head_conds=None, goodbye_result='setstage MyQuest 27'):
        head_conds = head_conds if head_conds is not None else [
            _tes4_ctda(func=72, p1=self.A_BASE),
            _tes4_ctda(type_byte=0x02, func=72, p1=self.B_BASE),
            _tes4_ctda(func=58, comp=self._f(26.0), p1=self.QUEST),
        ]
        head = self._info(0x000AAA30, self.HELLO_D,
                          'Are you all right, sire?', head_conds,
                          choices=(self.MID_D,))
        mid = self._info(0x000AAA31, self.MID_D, 'Captain Renault?', [
            _tes4_ctda(func=72, p1=self.B_BASE),
            _tes4_ctda(func=58, comp=self._f(26.0), p1=self.QUEST),
        ], choices=(self.GBYE_D,))
        gbye = self._info(0x000AAA32, self.GBYE_D, 'She is dead.', [
            _tes4_ctda(func=72, p1=self.A_BASE),
            _tes4_ctda(func=58, comp=self._f(26.0), p1=self.QUEST),
        ], result=goodbye_result)
        return {
            'DIAL': [
                {'Signature': 'DIAL', 'FormID': f'{self.HELLO_D:08X}',
                 'EditorID': 'HELLO', 'DATA.Type': '1'},
                {'Signature': 'DIAL', 'FormID': f'{self.MID_D:08X}',
                 'EditorID': 'MidTopic', 'DATA.Type': '1'},
                {'Signature': 'DIAL', 'FormID': f'{self.GBYE_D:08X}',
                 'EditorID': 'GOODBYE', 'DATA.Type': '1'},
            ],
            'INFO': [head, mid, gbye],
            'QUST': [{'Signature': 'QUST', 'FormID': f'{self.QUEST:08X}',
                      'EditorID': 'MyQuest'}],
            'SCPT': [],
            'ACHR': [
                {'Signature': 'ACHR', 'FormID': '000AAB01',
                 'EditorID': 'ARef', 'NAME': f'{self.A_BASE:08X}'},
                {'Signature': 'ACHR', 'FormID': '000AAB02',
                 'EditorID': 'BRef', 'NAME': f'{self.B_BASE:08X}'},
            ],
            'ACRE': [],
        }

    def _plan(self, **kw):
        from tes5_import.dialogue.conversations import build_conversation_plan
        return build_conversation_plan(self._by_type(**kw),
                                       plugin_stem='Test')

    def test_quest_advancing_chain_is_restored(self):
        plan = self._plan()
        assert len(plan['chains']) == 1
        c = plan['chains'][0]
        assert c['subj']['ref_edid'] == 'ARef'
        assert c['tgt']['ref_edid'] == 'BRef'
        assert [h['topic_edid'] for h in c['hops']] == ['MidTopic', 'GOODBYE']
        # NextSpeaker=Target alternates: head is A, so Mid is B, GOODBYE is A.
        assert [h['speaker'] for h in c['hops']] == ['B', 'A']
        assert c['gates'] == [{'kind': 'stage', 'quest_fid': self.QUEST,
                               'quest_edid': 'MyQuest', 'op': '==',
                               'value': 26}]

    def test_dropped_mid_topic_is_undropped_not_skipped(self):
        """MidTopic is Type-1 chatter the NPC-to-NPC drop would remove; a
        restored chain must instead carry it (the driver Says it)."""
        plan = self._plan()
        assert plan['chains'][0]['undrop_topic_fids'] == [self.MID_D]

    def test_flavor_chain_stays_dropped(self):
        """No quest-advancing result anywhere -> not restored (the
        better-absent-than-wrong rule for pure chatter)."""
        plan = self._plan(goodbye_result='')
        assert plan['chains'] == []

    def test_negative_identity_is_not_an_address(self):
        """GetIsID(npc)[Target] == 0 EXCLUDES that npc — such a head is a
        player greeting with an exclusion, not an NPC-to-NPC opener."""
        conds = [
            _tes4_ctda(func=72, p1=self.A_BASE),
            _tes4_ctda(type_byte=0x02, comp=0, func=72, p1=self.B_BASE),
            _tes4_ctda(func=58, comp=self._f(26.0), p1=self.QUEST),
        ]
        plan = self._plan(head_conds=conds)
        assert plan['chains'] == []

    def test_unsupported_gate_skips_the_chain(self):
        """A gate the driver cannot compile must SKIP the chain — firing a
        conversation early is worse than leaving it absent."""
        conds = [
            _tes4_ctda(func=72, p1=self.A_BASE),
            _tes4_ctda(type_byte=0x02, func=72, p1=self.B_BASE),
            _tes4_ctda(func=45, comp=self._f(1.0), p1=self.A_BASE),
        ]
        plan = self._plan(head_conds=conds)
        assert plan['chains'] == []
        assert any('unsupported gate' in why for _fid, why in plan['skipped'])

    def test_generated_psc_shape(self):
        """Each hop waits out TES4Polyfill.SayLine plus the 0.6s beat.

        The measured length is only the FALLBACK argument, and the `!= None`
        guards disable a chain whose binding diverged instead of aborting the
        poll function.
        """
        from tes5_import.dialogue.conversations import generate_driver_psc
        plan = self._plan()
        psc = generate_driver_psc(plan, {'info:000AAA31': 3.0})
        assert psc.startswith('ScriptName TES4NPCConvTest extends Quest')
        assert 'Utility.Wait(TES4Polyfill.SayLine(Conv0A, Conv0T0, 4.00) + 0.6)' in psc
        assert 'TES4Polyfill.SayLine(Conv0B, Conv0T1, 3.00) + 0.6' in psc
        assert 'TES4Polyfill.SayLine(Conv0A, Conv0T2, 4.00) + 0.6' in psc
        assert '.Say(' not in psc
        assert 'Conv0Q0.GetStage() == 26' in psc
        assert 'Conv0T0 != None' in psc and 'Conv0Q0 != None' in psc

    def test_property_bindings_mirror_the_psc(self):
        """Every Conv* property the psc declares must be bound by
        chain_property_bindings + the caller's T0 — name-for-name."""
        import re as _re
        from tes5_import.dialogue.conversations import (chain_property_bindings,
                                                   generate_driver_psc)
        plan = self._plan()
        chain = plan['chains'][0]
        props = chain_property_bindings(chain, lambda f: 0x01000000 | f,
                                        lambda hop: 0x01BB0000)
        props['Conv0T0'] = 0x01AA0000
        psc = generate_driver_psc(plan, {})
        declared = set(_re.findall(r'Property (Conv\w+) Auto', psc))
        assert declared == set(props)

    def test_overlapping_chains_are_mutually_exclusive(self):
        """Two heads opening the same authored talk must retire each other,
        or the whole conversation replays (MS91 Weebam-Na/Mazoga)."""
        from tes5_import.dialogue.conversations import (build_conversation_plan,
                                                   generate_driver_psc)
        by_type = self._by_type()
        second = self._info(0x000AAA33, self.HELLO_D, 'You wanted me?', [
            _tes4_ctda(func=72, p1=self.B_BASE),
            _tes4_ctda(type_byte=0x02, func=72, p1=self.A_BASE),
            _tes4_ctda(func=58, comp=self._f(26.0), p1=self.QUEST),
        ], choices=(self.GBYE_D,))
        by_type['INFO'].append(second)
        plan = build_conversation_plan(by_type, plugin_stem='Test')
        assert len(plan['chains']) == 2
        assert plan['chains'][0]['exclusive_with'] == [1]
        assert plan['chains'][1]['exclusive_with'] == [0]
        psc = generate_driver_psc(plan, {})
        done1 = [l for l in psc.splitlines() if l.strip() == '_done1 = True']
        assert len(done1) == 2      # its own block and chain 0's


class TestScriptStartedChains:
    """`StartConversation A B topic` hands a whole chain to Oblivion's
    scheduler; Skyrim's Say plays ONE line, so the chain must be replayed.
    See: docs/commentary/tes5_import_dialogue.md#script-started-conversation-chains"""

    QUEST, TOPIC = 0x000BBB10, 0x000BBB20
    A_BASE, B_BASE = 0x000BBB01, 0x000BBB02

    @staticmethod
    def _f(v: float) -> int:
        """`v` as its raw float bits, the CTDA comparison encoding."""
        return struct.unpack('<I', struct.pack('<f', v))[0]

    def _line(self, fid, conv_value, npc_addressed=True):
        """One INFO gated on `conv == conv_value`."""
        conds = [_tes4_ctda(func=72, p1=self.A_BASE)]
        if npc_addressed:
            conds.append(_tes4_ctda(type_byte=0x02, func=72, p1=self.B_BASE))
        conds.append(_tes4_ctda(func=79, comp=self._f(float(conv_value)),
                                p1=self.QUEST, p2=9))
        rec = {'Signature': 'INFO', 'FormID': f'{fid:08X}',
               'ParentDIAL': f'{self.TOPIC:08X}', 'DATA.DialogType': '1',
               'DATA.NextSpeaker': '0',
               'QSTI.Quest': f'{self.QUEST:08X}',
               'ConditionCount': str(len(conds))}
        for i, c in enumerate(conds):
            rec[f'Condition[{i}].Raw'] = c.hex()
        return rec

    def _map(self, edid='MyConvo', lines=3, npc_addressed=True):
        """The chain map for one topic carrying `lines` gated INFOs."""
        from tes5_import.dialogue.conversations import build_script_chain_map
        infos = [self._line(0x000BBB30 + n, n, npc_addressed)
                 for n in range(lines)]
        return build_script_chain_map({
            'DIAL': [{'Signature': 'DIAL', 'FormID': f'{self.TOPIC:08X}',
                      'EditorID': edid, 'DATA.Type': '1'}],
            'INFO': infos,
        })

    def test_consecutive_counter_run_is_a_chain(self):
        """A run of conv==0..3 is four lines to replay."""
        assert self._map(lines=4) == {'myconvo': 4}

    def test_single_line_topic_is_not_a_chain(self):
        """One line needs no replay -- it keeps the plain Say."""
        assert self._map(lines=1) == {}

    def test_player_facing_topic_is_excluded(self):
        """No run-on-target identity means no chain: else GREETING loops."""
        assert self._map(edid='GREETING', npc_addressed=False) == {}


class TestForceGreetOncePerDay:
    def test_quest_gated_forcegreet_keeps_once_per_day(self):
        """convert_flags strips Once Per Day from quest-gated packages (the
        Renault secret-door regression), but a FORCE-GREET's daily latch is
        its only retire mechanism when the greeting advances no stage —
        CGBaurusGreetPlayer re-fired forever and stalled CharacterGen 56.
        Vanilla ships 0x400 on 57 of its 302 ForceGreet-template packages,
        including quest-gated ones, so restoring it is vanilla-legal."""
        from tes5_import.packages.converter import convert_PACK, PackContext
        rec = {'Signature': 'PACK', 'FormID': '0002C2F1',
               'EditorID': 'CGBaurusGreetPlayer',
               'PKDT.Type': '0', 'PKDT.Flags': '5124',
               'PLDT.Type': '2', 'PLDT.Location': '0', 'PLDT.Radius': '500',
               'PTDT.Type': '0', 'PTDT.Target': '00000014',
               'PTDT.Count': '200'}
        ctx = PackContext()
        ctx.quest_of = lambda fid: 0x0100AAAA          # quest-gated
        blob = convert_PACK(rec, ctx)
        pkdt = _find_subrecord(blob, b'PKDT')
        flags = struct.unpack_from('<I', pkdt, 0)[0]
        assert flags & 0x400, 'Once Per Day lost - the greet never retires'
        assert flags & 0x004, 'Must Complete lost'

    def test_non_forcegreet_quest_gated_still_strips_once_per_day(self):
        """The Renault fix must survive: an ordinary quest-gated package
        (UseItemAt/Travel) still loses the daily latch."""
        from tes5_import.packages.converter import (convert_flags, T4_ONCE_PER_DAY,
                                                T5_ONCE_PER_DAY)
        flags, _ = convert_flags(T4_ONCE_PER_DAY, 2, True, quest_gated=True)
        assert not (flags & T5_ONCE_PER_DAY)


class TestSaySpeakAsIdentityGate:
    """Actor-identity gates must fail OPEN inside a speak-as topic.

    TES4 `ArenaMatchPlayerRef.Say Announcer 1 ArenaMouth 1` resolves the
    speaker to ArenaMouth, so the INFO's `GetIsID ArenaMouth` passes.  Skyrim's
    Say has no speak-as argument: the speaker stays the emitting XMarker, which
    has no base NPC, no voice type and no race, so those gates can never pass.
    No INFO is selected and the line is SILENT while the caller's timers run on
    (the Arena announcer said nothing yet the gates opened).

    Keyed on the TOPIC, never the NPC -- SEThadon is a real actor who speaks
    his own lines AND lends his identity to a marker-spoken shout.
    See tes5_import/dialogue/speak_as.py.
    """

    ARENA_ANNOUNCER_DIAL = 0x046652

    def _ctda(self, func, param1, run_on_target=False):
        import struct
        return struct.pack('<B3xIIII', 0x02 if run_on_target else 0x00,
                           struct.unpack('<I', struct.pack('<f', 1.0))[0],
                           func, param1, 0)

    def _info(self, dial=None, raws=()):
        rec = {'ParentDIAL': f'{dial:08X}'} if dial is not None else {}
        for i, r in enumerate(raws):
            rec[f'Condition[{i}].Raw'] = r.hex()
        return rec

    def test_identity_gate_dropped_in_speak_as_topic(self):
        from tes5_import.base import conditions as dc
        dc.set_speak_as_topics({self.ARENA_ANNOUNCER_DIAL})
        try:
            out = dc.convert_ctda(self._ctda(72, 0x00046653), offset=0,
                                  in_speak_as_topic=True)
            assert out is None
        finally:
            dc.set_speak_as_topics(set())

    def test_playable_race_gate_dropped_in_speak_as_topic(self):
        """The QUST-level gate ArenaAnnouncer carries."""
        from tes5_import.base import conditions as dc
        out = dc.convert_ctda(self._ctda(254, 0), offset=0,
                              in_speak_as_topic=True)
        assert out is None

    def test_identity_gate_survives_outside_a_speak_as_topic(self):
        """A normal speaker keeps its authored identity gate."""
        from tes5_import.base import conditions as dc
        out = dc.convert_ctda(self._ctda(72, 0x0001C1A2), offset=0,
                              in_speak_as_topic=False)
        assert out is not None

    def test_thadons_own_lines_are_untouched(self):
        """SEThadon lends his identity to a marker shout in ANOTHER topic;
        that must not weaken the lines he delivers himself."""
        from tes5_import.base import conditions as dc
        dc.set_speak_as_topics({0x07B20F})       # some other, speak-as topic
        try:
            rec = self._info(dial=0x0000D2,      # Thadon's own topic
                             raws=[self._ctda(72, 0x00012106)])
            assert dc.is_speak_as_record(rec) is False
            out = dc.convert_ctda(self._ctda(72, 0x00012106), offset=0,
                                  in_speak_as_topic=dc.is_speak_as_record(rec))
            assert out is not None
        finally:
            dc.set_speak_as_topics(set())

    def test_target_run_identity_is_not_touched_by_this_rule(self):
        """RunOnTarget identities have their own, separate handling."""
        from tes5_import.base import conditions as dc
        out = dc.convert_ctda(self._ctda(72, 0x00046653, run_on_target=True),
                              offset=0, in_speak_as_topic=True)
        assert out is not None

    def test_is_speak_as_record_reads_the_parent_topic(self):
        from tes5_import.base import conditions as dc
        dc.set_speak_as_topics({self.ARENA_ANNOUNCER_DIAL})
        try:
            assert dc.is_speak_as_record(
                self._info(dial=self.ARENA_ANNOUNCER_DIAL)) is True
            assert dc.is_speak_as_record(self._info(dial=0x0000C8)) is False
            assert dc.is_speak_as_record({}) is False
        finally:
            dc.set_speak_as_topics(set())

    def test_scanner_finds_the_announcer_topic_not_the_npc(self):
        from tes5_import.dialogue.speak_as import scan_speak_as_topics
        by_type = {
            'DIAL': [{'FormID': '00046652', 'EditorID': 'Announcer'}],
            'NPC_': [{'FormID': '00046653', 'EditorID': 'ArenaMouth'}],
            'SCPT': [{'SCTX': 'ArenaMatchPlayerRef.Say Announcer 1 '
                              'ArenaMouth 1'}],
        }
        assert scan_speak_as_topics(by_type) == {0x046652}

    def test_scanner_ignores_a_plain_say(self):
        from tes5_import.dialogue.speak_as import scan_speak_as_topics
        by_type = {
            'DIAL': [{'FormID': '000000C8', 'EditorID': 'GREETING'}],
            'SCPT': [{'SCTX': 'SomeRef.Say GREETING'}],
        }
        assert scan_speak_as_topics(by_type) == set()


class TestDropNonActorSpeakerCtdas:
    """_drop_non_actor_speaker_ctdas re-packs a raw condition BLOCK.

    Its `sig` is a 4-byte slice off that buffer, but pack_subrecord takes a
    str -- so it raised AttributeError ('bytes' has no 'encode') on EVERY
    speak-as INFO whose owning quest carried conditions, and the caller's
    blanket `except` swallowed it as "ERROR info under <topic>", silently
    dropping all four of Morroblivion's mwDASheogorathSpeech lines.
    """

    @staticmethod
    def _ctda(func, type_byte=0, run_on=0):
        return struct.pack('<B3xIHHIIII I', type_byte, 0x3F800000, func, 0,
                           0, 0, run_on, 0, 0xFFFFFFFF)

    def _block(self):
        from tes5_import.base.conditions import (NON_ACTOR_SPEAKER_DROP,
                                                   CTDA_OR)
        from tes5_import.record_types.common import pack_subrecord
        drop_func = sorted(NON_ACTOR_SPEAKER_DROP)[0]
        keep_func = max(NON_ACTOR_SPEAKER_DROP) + 1
        assert keep_func not in NON_ACTOR_SPEAKER_DROP
        blob = pack_subrecord('CTDA', self._ctda(drop_func))
        blob += pack_subrecord('CIS2', b'::foo_var\x00')
        blob += pack_subrecord('CTDA', self._ctda(keep_func,
                                                  type_byte=CTDA_OR))
        return blob, keep_func

    def test_repacks_kept_conditions(self):
        from tes5_import.dialogue.groups import _drop_non_actor_speaker_ctdas
        from tes5_import.base.conditions import CTDA_OR
        blob, keep_func = self._block()
        out = _drop_non_actor_speaker_ctdas(blob)
        # Exactly one CTDA survives: the dropped one took its CIS2 with it.
        assert out[:4] == b'CTDA'
        assert len(out) == 6 + 32
        assert struct.unpack_from('<H', out, 6 + 8)[0] == keep_func
        # ...and its now-dangling OR flag was cleared.
        assert not (out[6] & CTDA_OR)

    def test_all_dropped_yields_empty(self):
        from tes5_import.dialogue.groups import _drop_non_actor_speaker_ctdas
        from tes5_import.base.conditions import NON_ACTOR_SPEAKER_DROP
        from tes5_import.record_types.common import pack_subrecord
        blob = pack_subrecord('CTDA',
                              self._ctda(sorted(NON_ACTOR_SPEAKER_DROP)[0]))
        assert _drop_non_actor_speaker_ctdas(blob) == b''
