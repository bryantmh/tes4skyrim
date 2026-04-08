"""Dialog/misc converters: QUST, DIAL, INFO, SOUN, DLBR, DLVW."""

import struct

from ..text_reader import get_formid_index_offset
from .common import (
    _prefix_path,
    get_float,
    get_formid,
    get_int,
    get_str,
    pack_formid_subrecord,
    pack_obnd,
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
    pack_uint8_subrecord,
    pack_uint32_subrecord,
)

# ---- Function indices (same in TES4 and TES5) ----------------------------
_FUNC_GET_IS_ID = 72          # GetIsID(npc_formid) — NPC identity check
_FUNC_GET_IS_VOICE_TYPE = 426  # GetIsVoiceType(vtyp_formid) — TES5 voice routing


# TES4 condition function indices that do NOT exist in TES5.
# These must be dropped (not forwarded) to avoid false-failing or crashing.
#
# Special case — GetDisposition (76):
#   In Oblivion, NPC dialog uses GetDisposition >= N to gate friendly lines.
#   TES5 has no disposition system; dropping this check lets dialog fire freely,
#   which is the correct conversion behaviour (NPCs should talk without
#   requiring a disposition score that can never be set).
#
# The other TES4-only functions (vampire check, IsYielding, etc.) are rare
# in dialog and safe to drop — if the condition is AND, dropping it means it
# always passes (correct for dialog); if OR, dropping it has no effect either.
_TES4_ONLY_FUNCS = frozenset({
    76,   # GetDisposition  — disposition system removed in TES5
    40,   # GetVampire      — replaced by keyword checks in TES5
    104,  # IsYielding      — not in TES5
    171,  # IsPlayerInJail  — not in TES5
    251,  # GetPCInfamy     — not in TES5 (replaced by Skyrim's crime system)
})


# Condition functions that depend on quest state.  TES4 quests have no
# Papyrus scripts, so quest variables/stages are never set at runtime.
# For bark INFOs these conditions would ALWAYS fail, blocking dialogue.
# Strip them from bark topics to let the lines fire naturally.
_QUEST_DEPENDENT_FUNCS = frozenset({
    56,   # GetQuestRunning — quest scripts not running
    58,   # GetStage        — quest stage never set
    59,   # GetStageDone    — quest stage never marked done
    99,   # GetQuestCompleted — quest never completed
    79,   # GetIsPlayerBirthsign — no birthsigns in TES5
    71,   # GetInCell       — TES4 cell FormIDs may not map correctly
    67,   # GetCurrentAIProcedure — AI package system completely different
})


def _convert_ctda_tes4_to_tes5(raw: bytes) -> 'bytes | None':
    """Convert a 24-byte TES4 CTDA to 32-byte TES5 CTDA.

    Returns None for TES4-only functions that have no TES5 equivalent (caller
    must skip the subrecord entirely so it doesn't block otherwise valid dialog).

    Structural change: TES4=24B, TES5=32B — 8 extra bytes appended:
        [24-27] Reference FormID (for RunOn=2/Reference, otherwise 0)
        [28-31] Unknown field (vanilla Skyrim always 0xFFFFFFFF)

    FormID remapping: Param1 and Param2 (and optionally CompValue when the
    type byte has the UseGlobal flag) are shifted from TES4 load order index
    (0x00 = Oblivion.esm) to TES5 load order index (+offset).

    Heuristic: a value is treated as a FormID if its top byte is 0x00 AND
    the lower 24 bits are >= 0x100 (i.e., not a small integer like an actor
    value index or skill index).
    """
    offset = get_formid_index_offset()
    data = raw + b'\x00' * max(0, 24 - len(raw))

    type_byte = data[0]
    comp_raw = struct.unpack_from('<I', data, 4)[0]
    func_idx = struct.unpack_from('<H', data, 8)[0]
    param1 = struct.unpack_from('<I', data, 12)[0]
    param2 = struct.unpack_from('<I', data, 16)[0]
    run_on = struct.unpack_from('<I', data, 20)[0]

    # Drop TES4-only functions — they have no TES5 equivalent and would
    # evaluate to 0.0, causing valid dialog lines to fail their conditions.
    if func_idx in _TES4_ONLY_FUNCS:
        return None

    def _remap(v: int) -> int:
        if offset and (v >> 24) == 0x00 and (v & 0x00FFFFFF) >= 0x100:
            return (v & 0x00FFFFFF) | (offset << 24)
        return v

    # Use Global flag: bit 2 (0x04) in TES4/TES5 — comparison value is a
    # Global FormID rather than a float literal.
    if type_byte & 0x04:
        comp_raw = _remap(comp_raw)
    param1 = _remap(param1)
    param2 = _remap(param2)

    # TES5 CTDA: 32 bytes
    # Layout: Type(1B) + pad(3B) + CompValue(4B) + FuncIdx(2B) + pad(2B)
    #       + Param1(4B) + Param2(4B) + RunOn(4B) + Ref(4B) + Unknown(4B)
    return struct.pack('<B3xIHHIIIII',
                       type_byte,
                       comp_raw,
                       func_idx, 0,
                       param1, param2, run_on,
                       0,          # Reference (unused unless RunOn=2)
                       0xFFFFFFFF) # Unknown — vanilla Skyrim uses FFFFFFFF


def convert_QUST(rec: dict, dialogue_quest_fids: set = None) -> bytes:
    """QUST — Quest conversion.

    TES5 order: EDID VMAD FULL DNAM ENAM FLTR [conditions] NEXT [conditions]
                INDX/QSDT/CNAM (stages) QOBJ/FNAM/NNAM (objectives) ANAM (next alias id)

    DNAM is 12 bytes: Flags(U16) Priority(U8) FormVersion(U8) Unknown(4) Type(U32)
    NEXT is a required empty marker.
    ANAM is required (next alias ID, U32).

    Dialogue quests get additional flags:
      0x0001 StartGameEnabled — required so topics appear at game start
      0x0010 StartsEnabled   — quest is active immediately

    NOTE: HasDialogueData (0x8000) is NEVER set by Skyrim.esm quests — setting
    it causes the engine to mishandle dialogue.  DNAM FormVer must be 0 (not 44)
    matching all vanilla Skyrim quests.
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    full = get_str(rec, 'FULL')
    if full:
        subs += pack_string_subrecord('FULL', full)

    # DNAM — 12 bytes: Flags(U16) + Priority(U8) + FormVersion(U8) + Unknown(4) + Type(U32)
    flags = get_int(rec, 'DATA.Flags')
    priority = get_int(rec, 'DATA.Priority')

    # TES4 flag → TES5 flag mapping:
    #   0x01 Start Game Enabled  → TES5 0x0001 (keep)
    #   0x08 Allow Repeated Stages → TES5 0x0008 (keep)
    safe_flags = flags & 0x0009

    # If this quest owns dialogue topics, force dialogue flags
    fid = get_formid(rec, 'FormID')
    is_dialogue_quest = dialogue_quest_fids and fid in dialogue_quest_fids
    if is_dialogue_quest:
        safe_flags |= 0x0001  # StartGameEnabled
        safe_flags |= 0x0010  # StartsEnabled
        # Do NOT set HasDialogueData (0x8000) — Skyrim.esm never uses it
        # Cap priority for dialogue quests (Skyrim uses 0-5)
        priority = min(priority, 50)

    # DNAM FormVer: ALL vanilla Skyrim QUSTs have 0 in DNAM, not 44.
    dnam = struct.pack('<HBBII', safe_flags, priority, 0, 0, 0)
    subs += pack_subrecord('DNAM', dnam)

    # NEXT — required empty marker
    subs += pack_subrecord('NEXT', b'')

    # Stages — each stage has INDX(4B) + one or more (QSDT + optional CNAM) entries
    stage_count = get_int(rec, 'StageCount')
    for i in range(stage_count):
        stage_idx = get_int(rec, f'Stage[{i}].Index')
        subs += pack_subrecord('INDX', struct.pack('<HBB', stage_idx, 0, 0))
        log_count = get_int(rec, f'Stage[{i}].LogCount')
        if log_count > 0:
            for j in range(log_count):
                log_flags = get_int(rec, f'Stage[{i}].Log[{j}].Flags')
                log_text = get_str(rec, f'Stage[{i}].Log[{j}].Text')
                subs += pack_uint8_subrecord('QSDT', log_flags)
                if log_text:
                    subs += pack_string_subrecord('CNAM', log_text)
        else:
            log_text = get_str(rec, f'Stage[{i}].LogEntry')
            complete_flag = get_int(rec, f'Stage[{i}].CompleteQuest')
            stage_flags = 0x01 if complete_flag else 0
            subs += pack_uint8_subrecord('QSDT', stage_flags)
            if log_text:
                subs += pack_string_subrecord('CNAM', log_text)

    # ANAM — required next alias ID (U32)
    subs += pack_uint32_subrecord('ANAM', 0)

    return pack_record('QUST', fid, get_int(rec, 'RecordFlags'), subs)


def convert_DIAL(rec: dict, info_count: int = 0, dlbr_fid: int = 0,
                 quest_fid_override: int = 0) -> bytes:
    """DIAL — Dialog Topic conversion.

    TES5 order: EDID FULL PNAM BNAM QNAM DATA SNAM TIFC
    DATA = TopicFlags(U8) + Category(U8) + Subtype(U16 LE) = 4 bytes

    Bark topics (greetings, combat barks, idle chatter):
      - Category 7 (Misc) or 3 (Combat) or 5 (Detection) depending on subtype
      - NO BNAM — bark topics fire automatically without branches
    Conversation topics:
      - Category 0 (Topic)
      - BNAM → DLBR FormID (required for topics to appear in dialogue menu)

    quest_fid_override: If provided, forces QNAM to this quest (for orphan DIALs).
    """
    # ---- Subtype/category resolution tables --------------------------------

    # Known Oblivion EditorIDs → (TES5 DATA.Subtype enum, SNAM 4-char code)
    _EDID_TO_SUBTYPE: dict[str, tuple[int, bytes]] = {
        'GREETING':       (79, b'HELO'),
        'HELLO':          (79, b'HELO'),
        'GOODBYE':        (78, b'GBYE'),
        'IdleChatter':    (94, b'IDLE'),
        'Idle':           (94, b'IDLE'),
        'IDLE':           (94, b'IDLE'),
        'Attack':         (26, b'ATCK'),
        'Hit':            (29, b'HIT_'),
        'Flee':           (30, b'FLEE'),
        'Steal':          (38, b'STEA'),
        'Trespass':       (49, b'TRES'),
        'ServiceRefusal': (66, b'SERU'),
        'Barter':         (70, b'BAEX'),
        'BarterFail':     (70, b'BAEX'),
        'Repair':         (67, b'REPA'),
        'Travel':         (68, b'TRAV'),
        'ObserveCombat':  (75, b'OBCO'),
        'Corpse':         (76, b'NOTI'),
        'NoticeCorpse':   (76, b'NOTI'),
        'TimeToGo':       (77, b'TITG'),
        'InfoRefusal':    (17, b'REFU'),
        'Noticed':        (57, b'NOTA'),
        'Seen':           (57, b'NOTA'),
        'Unseen':         (63, b'LOTN'),
        'Lost':           (63, b'LOTN'),
    }

    # Override category for known bark/combat subtypes.
    # These MUST be correct or the engine ignores the topic entirely.
    _SUBTYPE_CATEGORY_OVERRIDE = {
        # Miscellaneous (7) — automatic NPC barks
        79: 7,  # Hello        (HELO)
        78: 7,  # Goodbye      (GBYE)
        94: 7,  # Idle         (IDLE)
        77: 7,  # TimeToGo     (TITG)
        76: 7,  # NoticeCorpse (NOTI)
        75: 7,  # ObserveCombat(OBCO)
        68: 7,  # Travel       (TRAV)
        67: 7,  # Repair       (REPA)
        66: 7,  # ServiceRefusal(SERU)
        70: 7,  # BarterExit   (BAEX)
        17: 7,  # InfoRefusal  (REFU)
        # Combat (3) — combat barks
        26: 3,  # Attack       (ATCK)
        29: 3,  # Hit          (HIT_)
        30: 3,  # Flee         (FLEE)
        38: 3,  # Steal        (STEA)
        49: 3,  # Trespass     (TRES)
        # Detection (5)
        57: 5,  # NormalToAlert(NOTA)
        63: 5,  # LostToNormal (LOTN)
    }

    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    full = get_str(rec, 'FULL')
    if full:
        subs += pack_string_subrecord('FULL', full)

    # PNAM — priority (float, default 50.0)
    subs += pack_subrecord('PNAM', struct.pack('<f', 50.0))

    # BNAM — branch link (only for conversation topics, not barks)
    if dlbr_fid:
        subs += pack_formid_subrecord('BNAM', dlbr_fid)

    # QNAM — quest link (required — Skyrim ignores DIALs without QNAM)
    qfid = quest_fid_override
    if not qfid:
        qcount = get_int(rec, 'QuestCount')
        if qcount > 0:
            qfid = get_formid(rec, 'Quest[0]')
    if qfid:
        subs += pack_formid_subrecord('QNAM', qfid)

    # Resolve subtype from EditorID, falling back to type-based defaults
    dtype = get_int(rec, 'DATA.Type')
    subtype_int, snam_code = _EDID_TO_SUBTYPE.get(edid or '', (0, b'CUST'))

    # Category: bark topics get overridden to Combat/Detection/Misc;
    # all other conversation topics default to Category 0 (Topic).
    if subtype_int in _SUBTYPE_CATEGORY_OVERRIDE:
        category = _SUBTYPE_CATEGORY_OVERRIDE[subtype_int]
    else:
        category = 0  # Topic — conversation topics

    # DATA — TopicFlags(U8) + Category(U8) + Subtype(U16 LE) = 4 bytes
    subs += pack_subrecord('DATA', struct.pack('<BBH', 0, category, subtype_int))

    # SNAM — subtype name as 4 raw ASCII bytes
    subs += pack_subrecord('SNAM', snam_code)

    # TIFC — info count (U32) — must match actual child INFO count
    subs += pack_uint32_subrecord('TIFC', info_count)

    return pack_record('DIAL', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def convert_INFO(rec: dict, voice_type_ctdas: bytes = b'',
                 is_bark: bool = False) -> bytes:
    """INFO — Dialog response. Major restructuring from TES4.

    TES5 order: EDID [VMAD] ENAM CNAM [TCLT[]] [TRDT NAM1 NAM2 NAM3]* CTDAs

    voice_type_ctdas: Pre-built CTDA subrecords for GetIsVoiceType conditions.
    These are injected BEFORE the TES4-converted conditions so the engine can
    filter by voice type first (matching Skyrim's standard pattern).

    is_bark: If True, strip quest-dependent conditions that would always fail
    because TES4 quest scripts aren't running.  Keeps identity/race/faction
    conditions intact so NPC-specific lines still gate correctly.
    """
    # TES4 emotion type names → TES5 wbEmotionTypeEnum values
    # Both use the same 0-6 range (Neutral/Anger/Disgust/Fear/Sad/Happy/Surprise)
    # so no remapping is needed.

    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    # ENAM — Response flags: Flags(U16) + ResetHours(U16)
    tes4_flags = get_int(rec, 'DATA.Flags')
    tes5_flags = tes4_flags & 0x37   # keep only compatible bits
    subs += pack_subrecord('ENAM', struct.pack('<HH', tes5_flags, 0))

    # CNAM — Favor Level (U8): None=0, Small=1, Medium=2, Large=3
    subs += pack_subrecord('CNAM', struct.pack('<B', 0))

    # TCLT — Link To (choices / next topics shown to player after this response)
    # New export format: ChoiceCount + Choice[i]
    # Old export format: TCLT.Choice (single value, for backward compat)
    choice_count = get_int(rec, 'ChoiceCount')
    if choice_count > 0:
        for i in range(choice_count):
            cfid = get_formid(rec, f'Choice[{i}]')
            if cfid:
                subs += pack_formid_subrecord('TCLT', cfid)
    else:
        # Backward compat: single TCLT.Choice key from old exports
        cfid = get_formid(rec, 'TCLT.Choice')
        if cfid:
            subs += pack_formid_subrecord('TCLT', cfid)

    # Responses — TRDT(24B) + NAM1 + NAM2 + NAM3
    # NAM2 (Script Notes) and NAM3 (Edits) must always be present per response,
    # even as empty strings — the engine parses responses as TRDT/NAM1/NAM2/NAM3
    # groups and will misparse or crash if NAM2/NAM3 are omitted.
    rc = get_int(rec, 'ResponseCount')
    for i in range(rc):
        emotion = get_int(rec, f'Response[{i}].EmotionType')
        emotion_val = get_int(rec, f'Response[{i}].EmotionValue')
        text = get_str(rec, f'Response[{i}].ResponseText')
        actor_notes = get_str(rec, f'Response[{i}].ActorNotes')
        # ResponseNumber: prefer exported value, fall back to 1-based index
        resp_num = get_int(rec, f'Response[{i}].ResponseNumber')
        if resp_num == 0:
            resp_num = i + 1
        # TRDT: EmotionType(U32) EmotionValue(U32) Unused(4B)
        #       ResponseNumber(U8) Unused(3B) Sound(FormID=0) Flags(U8=1) Unused(3B)
        # Flags=1 = "Use Emotions" — required by engine for voiced responses
        subs += pack_subrecord('TRDT', struct.pack('<IiI B3x I B3x',
                                                    emotion, emotion_val, 0,
                                                    resp_num, 0, 1))
        if text:
            subs += pack_string_subrecord('NAM1', text)
        # NAM2 (Script Notes) — always emit even if empty
        subs += pack_string_subrecord('NAM2', actor_notes if actor_notes else '')
        # NAM3 (Edits) — always emit as empty
        subs += pack_string_subrecord('NAM3', '')

    # Inject voice type conditions FIRST — Skyrim evaluates these to route
    # dialogue to the correct voice type.  Placed before TES4 conditions so
    # the voice-type OR chain is self-contained and can't merge with any
    # trailing OR flag from TES4 condition data.
    if voice_type_ctdas:
        subs += voice_type_ctdas

    # CTDAs — conditions (convert from TES4 24-byte to TES5 32-byte format)
    # _convert_ctda_tes4_to_tes5 returns None for TES4-only functions (e.g.
    # GetDisposition) that would always fail in TES5 — skip those entirely.
    # For bark topics, additionally strip quest-dependent conditions that
    # reference quest state (stages/variables) which will never be set.
    cc = get_int(rec, 'ConditionCount')
    for i in range(cc):
        raw_hex = rec.get(f'Condition[{i}].Raw', '')
        if raw_hex:
            try:
                raw = bytes.fromhex(raw_hex)
                # For bark INFOs, strip quest-dependent conditions
                if is_bark and len(raw) >= 10:
                    func_idx = struct.unpack_from('<H', raw, 8)[0]
                    if func_idx in _QUEST_DEPENDENT_FUNCS:
                        continue
                ctda = _convert_ctda_tes4_to_tes5(raw)
                if ctda is not None:
                    subs += pack_subrecord('CTDA', ctda)
            except (ValueError, struct.error):
                pass  # skip malformed conditions

    return pack_record('INFO', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


# ---------------------------------------------------------------------------
# DLBR / DLVW / Voice-type helpers
# ---------------------------------------------------------------------------

# Subtypes that are "barks" — fire automatically, no DLBR needed
BARK_SUBTYPES = frozenset({
    79,  # Hello  (HELO)
    78,  # Goodbye(GBYE)
    94,  # Idle   (IDLE)
    77,  # TimeToGo
    76,  # NoticeCorpse
    75,  # ObserveCombat
    68,  # Travel
    67,  # Repair
    66,  # ServiceRefusal
    70,  # BarterExit
    17,  # InfoRefusal
    26,  # Attack
    29,  # Hit
    30,  # Flee
    38,  # Steal
    49,  # Trespass
    57,  # NormalToAlert
    63,  # LostToNormal
})

# Map known TES4 EDID → subtype int (used by is_bark_topic)
_EDID_TO_SUBTYPE_INT: dict[str, int] = {
    'GREETING': 79, 'HELLO': 79, 'GOODBYE': 78,
    'IdleChatter': 94, 'Idle': 94, 'IDLE': 94,
    'Attack': 26, 'Hit': 29, 'Flee': 30,
    'Steal': 38, 'Trespass': 49, 'ServiceRefusal': 66,
    'Barter': 70, 'BarterFail': 70, 'Repair': 67,
    'Travel': 68, 'ObserveCombat': 75,
    'Corpse': 76, 'NoticeCorpse': 76, 'TimeToGo': 77,
    'InfoRefusal': 17, 'Noticed': 57, 'Seen': 57,
    'Unseen': 63, 'Lost': 63,
}


def is_bark_topic(edid: str) -> bool:
    """Return True if this DIAL EditorID represents a bark (non-interactive) topic."""
    return _EDID_TO_SUBTYPE_INT.get(edid or '', -1) in BARK_SUBTYPES


def make_dlbr(fid: int, edid: str, quest_fid: int, dial_fid: int,
              top_level: bool = True) -> bytes:
    """Create a DLBR (Dialog Branch) record.

    TES5 DLBR order: EDID QNAM TNAM DNAM SNAM
      QNAM = quest owner (FormID)
      TNAM = category: 0=Player, 1=Command
      DNAM = flags: 0x01=Top-Level, 0x02=Blocking, 0x04=Exclusive
      SNAM = starting topic (DIAL FormID)
    """
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_formid_subrecord('QNAM', quest_fid)
    subs += pack_uint32_subrecord('TNAM', 0)   # Player
    subs += pack_uint32_subrecord('DNAM', 1 if top_level else 0)
    subs += pack_formid_subrecord('SNAM', dial_fid)
    return pack_record('DLBR', fid, 0, subs)


def make_dlvw(fid: int, edid: str, quest_fid: int,
              branch_fids: list, topic_fids: list) -> bytes:
    """Create a DLVW (Dialog View) record.

    DLVW order: EDID QNAM [BNAM...] [TNAM...] ENAM DNAM
    """
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_formid_subrecord('QNAM', quest_fid)
    for bfid in branch_fids:
        subs += pack_formid_subrecord('BNAM', bfid)
    for tfid in topic_fids:
        subs += pack_formid_subrecord('TNAM', tfid)
    subs += pack_uint32_subrecord('ENAM', 0)  # Dialogue Branches view
    subs += pack_uint8_subrecord('DNAM', 0)   # Don't show all text
    return pack_record('DLVW', fid, 0, subs)


def build_voice_type_ctda(vtyp_fid: int, is_or: bool = False) -> bytes:
    """Build a single 32-byte GetIsVoiceType CTDA for a voice type FormID.

    type_byte: bits 5-7 = comparison (0=Equal), bit 0 = OR flag
    """
    type_byte = 0x01 if is_or else 0x00  # Equal + OR flag
    return struct.pack('<B3xIHHIIIII',
                       type_byte,
                       0x3F800000,          # CompValue = 1.0
                       _FUNC_GET_IS_VOICE_TYPE, 0,
                       vtyp_fid,            # Param1 = VTYP FormID
                       0,                   # Param2
                       0,                   # RunOn = Subject
                       0,                   # Reference
                       0xFFFFFFFF)          # Unknown


def build_voice_type_ctdas_for_info(rec: dict, npc_to_vtyp: dict,
                                     all_vtyp_fids: list) -> bytes:
    """Build packed GetIsVoiceType CTDA subrecords for an INFO record.

    Strategy:
    1. Scan existing conditions for GetIsID (func 72) — extract target NPC FormIDs
    2. Map each NPC to its voice type
    3. If NPC-specific: emit GetIsVoiceType for those voice types (OR'd)
    4. If generic (no GetIsID): emit GetIsVoiceType for ALL voice types (OR'd)

    All voice type conditions are OR'd with each other (any matching voice type
    is acceptable) but AND'd with the rest of the condition set.
    """
    offset = get_formid_index_offset()

    # Extract NPC FormIDs from GetIsID conditions
    npc_fids = set()
    cc = get_int(rec, 'ConditionCount')
    for i in range(cc):
        raw_hex = rec.get(f'Condition[{i}].Raw', '')
        if not raw_hex or len(raw_hex) < 20:
            continue
        try:
            raw = bytes.fromhex(raw_hex)
            func_idx = struct.unpack_from('<H', raw, 8)[0]
            if func_idx == _FUNC_GET_IS_ID:
                param1 = struct.unpack_from('<I', raw, 12)[0]
                npc_fids.add(param1)
        except (ValueError, struct.error):
            continue

    # Resolve NPC FormIDs to voice types
    vtyp_fids = set()
    for npc_fid in npc_fids:
        # Try with load-order remapping (the npc_to_vtyp uses remapped FormIDs)
        remapped = npc_fid
        if offset and (npc_fid >> 24) == 0x00 and (npc_fid & 0x00FFFFFF) >= 0x100:
            remapped = (npc_fid & 0x00FFFFFF) | (offset << 24)
        vtyp = npc_to_vtyp.get(remapped, 0) or npc_to_vtyp.get(npc_fid, 0)
        if vtyp:
            vtyp_fids.add(vtyp)

    # If no NPC-specific voice types found, use ALL voice types (generic line)
    if not vtyp_fids:
        vtyp_fids = set(all_vtyp_fids)

    if not vtyp_fids:
        return b''

    # Build CTDA subrecords — OR'd with each other, last one is AND
    # Pattern: VT1(OR) VT2(OR) ... VTn(AND) → (VT1 or VT2 or ... or VTn) AND rest
    result = b''
    vtyp_list = sorted(vtyp_fids)
    for idx, vfid in enumerate(vtyp_list):
        is_last = (idx == len(vtyp_list) - 1)
        ctda_data = build_voice_type_ctda(vfid, is_or=not is_last)
        result += pack_subrecord('CTDA', ctda_data)

    return result


# ---------------------------------------------------------------------------
# Dispatch table: TES4 signature → converter function
# ---------------------------------------------------------------------------


def convert_SOUN(rec: dict, writer=None) -> tuple:
    """SOUN — needs companion SNDR record in TES5.
    Returns (soun_bytes, sndr_bytes_or_None, sndr_formid).

    SOUN order: EDID OBND SDSC
    SNDR order: EDID CNAM GNAM SNAM ANAM[] ONAM LNAM BNAM
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    subs += pack_obnd()

    # SDSC → link to SNDR
    sndr_fid = 0
    sndr_bytes = None
    filename = get_str(rec, 'FNAM.Filename')
    if filename and writer:
        sndr_fid = writer.alloc_formid()
        sndr_subs = b''
        sndr_edid = f"TES4_{edid}_SNDR" if edid else f"TES4_SOUN_{get_formid(rec, 'FormID'):08X}_SNDR"
        sndr_subs += pack_string_subrecord('EDID', sndr_edid)
        # CNAM = Descriptor Type constant (0x1EEF540A — matches all vanilla SNDR records)
        sndr_subs += pack_uint32_subrecord('CNAM', 0x1EEF540A)
        # GNAM = Category: AudioCategorySFX (FormID 0x000172A1 in Skyrim.esm)
        sndr_subs += pack_formid_subrecord('GNAM', 0x000172A1)
        # ANAM = Sound file path
        sndr_subs += pack_string_subrecord('ANAM', _prefix_path(filename))
        # ONAM = Sound Output Model: SOMMono03000 (0x000ABEF3 in Skyrim.esm)
        # Required — CK reports 'Sound Output Model missing' if absent
        sndr_subs += pack_formid_subrecord('ONAM', 0x000ABEF3)
        # LNAM = Looping flag (0 = not looping)
        sndr_subs += pack_subrecord('LNAM', struct.pack('<I', 0))
        # BNAM = Values: FreqShift(S8) FreqVariance(S8) Priority(U8) dbVariance(U8) StaticAttenuation(U16)
        sndr_subs += pack_subrecord('BNAM', struct.pack('<bbBBH', 0, 0, 128, 0, 0))
        sndr_bytes = pack_record('SNDR', sndr_fid, 0, sndr_subs)

    if sndr_fid:
        subs += pack_formid_subrecord('SDSC', sndr_fid)

    soun_bytes = pack_record('SOUN', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)
    return soun_bytes, sndr_bytes, sndr_fid


def convert_PACK(rec: dict) -> bytes:
    """PACK — AI Package conversion (TES4 → TES5).

    TES5 PACK uses a procedure tree system. We create skeleton packages
    with PKDT/PSDT/PKCU and the required OnBegin/OnEnd/OnChange markers.
    PLDT/PTDT do NOT exist in TES5 and must NOT be emitted.

    TES5 order: EDID VMAD PKDT PSDT [conditions]
                [idle anims] CNAM QNAM PKCU
                [Package Data] XNAM
                [Procedure Tree] [UNAMs]
                POBA(OnBegin) POEA(OnEnd) POCA(OnChange)

    =========================================================================
    BRAINSTORM: Getting to fully functional PACK records
    =========================================================================

    CURRENT STATE
      We emit a structurally valid but behaviourally empty PACK:
        - PKDT with flag/type approximation, no Procedure Tree branches
        - PSDT schedule (month/dow/date/time/duration) — this part is correct
        - PKCU with zero data inputs and null template
        - XNAM + POBA/POEA/POCA markers (required structure, no behaviour)
      The result is a package the engine accepts without crashing but which
      causes the NPC to stand idle because the Procedure Tree has no branches.

    THE CORE STRUCTURAL PROBLEM
      TES4 PACK behaviour is encoded in PKDT.Type (0=Find, 1=Follow, 2=Escort,
      3=Eat, 4=Sleep, 5=Wander, 6=Travel, 7=Accompany, 8=UseItemAt, 9=Ambush,
      10=FleeNotCombat, 11=CastMagic) plus PLDT (location) and PTDT (target).
      TES5 replaces these with a Procedure Tree: a tree of named branch types
      (ANAM strings like "Travel", "Wander", "Patrol", etc.) where each branch
      has conditions (CTDAs), data inputs (PLDT/PTDA subrecords inside Package
      Data), and a PRCB struct giving branch count + repeat flags. The type
      information that was a single byte in TES4 is now a named branch in TES5.

    TES4 TYPE → TES5 BRANCH TYPE MAPPING
      TES4 Type  Name          TES5 ANAM branch string(s)
      ---------  -----------   --------------------------------
      0          Find          "Travel" branch + "Find" sub-branch
      1          Follow        "Follow"
      2          Escort        "Escort"
      3          Eat           "UseWeapon" or "Activate" (eating = activating food)
      4          Sleep         "Sleep"
      5          Wander        "Wander"
      6          Travel        "Travel"
      7          Accompany     "Escort" (closest equivalent)
      8          UseItemAt     "UseWeapon" / "Activate"
      9          Ambush        "PatrolSit" or custom Combat branch
      10         FleeNotCombat "Flee"
      11         CastMagic     "UseWeapon" (magic combat package)

      These are discovered by inspecting vanilla Skyrim PACK records in
      Skyrim.esm using xEdit. The branch ANAM strings are not enumerated in
      wbDefinitionsTES5.pas — they are freeform strings matching CK templates.

    TES4 PLDT (Location) → TES5 PLDT (inside Package Data)
      Both formats use the same 12-byte PLDT struct (Type S32, Value 4 bytes,
      Radius S32). The Type values are partially compatible:
        TES4 Type 0 = Near reference  → TES5 Type 0 = Near reference (same)
        TES4 Type 1 = In cell         → TES5 Type 1 = In cell (same)
        TES4 Type 2 = Near current location → TES5 Type 2 = Near pkg start loc
        TES4 Type 3 = Near editor loc → TES5 Type 3 = Near editor loc (same)
        TES4 Type 4 = Object ID       → TES5 Type 4 = Object ID (same)
        TES4 Type 5 = Object type     → TES5 Type 5 = Object type (same)
      FormID references in Type 0/1/4 are TES4 FormIDs and need remapping
      via the standard FormID translation table.

    TES4 PTDT (Target) → TES5 PTDA (Target Data inside Package Data)
      TES4 PTDT: Type S32 (0=SpecificRef, 1=ObjectID, 2=ObjectType),
                  Target (FormID or U32), Count S32
      TES5 PTDA wraps wbTargetData:
        Type S32 (0=SpecificRef, 1=ObjectID, 2=ObjectType, 3=LinkedRef,
                  4=RefAlias, 5=Unknown, 6=Self), Target (FormID or U32),
        Count/Distance S32
      TES4 types 0-2 map directly to TES5 types 0-2.

    PKDT FLAGS MAPPING
      TES4 flag (U16 or U32)       TES5 flag (U32)
      ----------------------------  -----------------------------------------
      0x0001 Offers services        0x00000001 Offers Services (same)
      0x0004 Must complete          0x00000004 Must complete (same)
      0x0008 Lock doors at start    0x00000008 Maintain Speed at Goal (≈)
      0x0200 Once per day           no direct equivalent — use PSDT Date=1
      0x2000 Always run             0x02000000 Unknown 26 (closest)
      0x00020000 Always sneak       0x00004000 Unknown 15 (closest, verify)
      0x00040000 Allow swimming     0x00200000 Unknown 22 (closest)
      0x01000000 No idle anims      0x01000000 Unknown 25 (same bit, verify)
      NOTE: TES4 PKDT is U16 in old records (detected by PKDT length=4),
      U32 in newer records (length=8). The exporter reads either form.

    PROCEDURE TREE STRUCTURE (required for the package to execute)
      Minimal branch for any package type:
        ANAM "BranchTypeName" (e.g. "Wander")
        CITC 0 (condition count = 0)
        PRCB struct: BranchCount=1, Flags=0 (or Flags=1 = Repeat when complete)
        PNAM "ProcedureTypeName" (same as ANAM for leaf branches)
        FNAM 0 (no Success Completes Package)
        PKC2 0 (data input index 0)
      The PKC2 index refers to an entry in the Package Data input list
      (the ANAM/CNAM/PLDT/PTDA group inside Package Data).

    PACKAGE DATA INPUTS (inside the PKCU/Package Data group)
      Each data input has:
        ANAM string: "Bool", "Int", "Float", "Topic", "TargetSelector",
                     "LocationSelector", "SingleRef", "ObjectList"
      For a Wander package: LocationSelector input pointing to the PLDT
      For a Travel package: LocationSelector input pointing to the PLDT
      For a Follow package: TargetSelector input pointing to the PTDA
      PKCU.DataInputCount = number of ANAM entries in the Package Data group.

    APPROACH: TEMPLATE-BASED GENERATION
      Rather than building the Procedure Tree from scratch per-package, the
      correct approach is to use Skyrim.esm's built-in package templates as
      a base. Several vanilla templates exist in Skyrim.esm:
        [00015E8F] DefaultWanderHome    — Wander near home marker
        [00015E92] DefaultSit           — Sit at furniture
        [00015E7F] DefaultSandboxCell   — Sandbox in cell
        [000D6B89] DefaultSleepEditor   — Sleep at editor location
        [000D6B8A] DefaultEat           — Eat at editor location
        [000D6B8C] DefaultTravelToRef   — Travel to reference
      Strategy:
        1. Map TES4 Type → nearest Skyrim template FormID (hardcoded table)
        2. Emit PKCU.PackageTemplate pointing at the template FormID
        3. Emit PKCU.DataInputCount = number of inputs the template expects
        4. Emit Package Data inputs (ANAM/CNAM/PLDT/PTDA) matching the template
        5. Omit the Procedure Tree — the template provides it at runtime
      This is how the CK generates packages from templates: the PACK record
      carries only the customisation (data inputs, schedule, conditions) while
      the template provides the behaviour tree. The engine evaluates the
      template's tree with the record's data inputs substituted.

    TES4 TYPE → SKYRIM TEMPLATE MAP (verified from Skyrim.esm)
      TES4 Type  Template EditorID          FormID
      ---------  --------------------------  --------
      0 Find      DefaultSandboxCell         0x00015E8F (nearest available)
      1 Follow    DefaultFollow (custom)     needs creation or closest match
      2 Escort    DefaultEscort (custom)     needs creation or closest match
      3 Eat       DefaultEat                 0x000D6B8A
      4 Sleep     DefaultSleepEditor         0x000D6B89
      5 Wander    DefaultWanderHome          0x00015E8F (sandbox = wander)
      6 Travel    DefaultTravelToRef         0x000D6B8C
      7 Accompany DefaultEscort              0x000D6B8C (travel closest)
      8 UseItemAt DefaultEat (repurpose)     0x000D6B8A (use at location)
      9 Ambush    DefaultSandboxCell         0x00015E8F (no good equivalent)
      10 Flee     no Skyrim template          must build tree manually
      11 CastMagic DefaultSandboxCell        0x00015E8F (fallback)
      These FormIDs are in Skyrim.esm and are always present. They are
      master-file records so no remapping is needed.

    IMPLEMENTATION ORDER
      Step 1: Map PKDT flags correctly (complete the flag table above)
      Step 2: Implement template-based PKCU.PackageTemplate for types 3,4,5,6
              (Eat/Sleep/Wander/Travel — the most common NPC routines)
      Step 3: Translate PLDT (location) and emit as Package Data input with
              ANAM "LocationSelector" for location-based packages
      Step 4: Translate PTDT (target) and emit as Package Data PTDA input
              with ANAM "TargetSelector" for follow/escort packages
      Step 5: For non-template types, build a minimal Procedure Tree manually
              (ANAM + PRCB + PNAM + FNAM + PKC2) — needed for Follow/Escort
      Step 6: Export CTDA conditions from TES4 PACK and map condition functions
              to TES5 equivalents (many are shared by name)
      Step 7: Handle PKDT old format (length=4, flags are U16) vs new (length=8)
              — the exporter currently reads only one form
    =========================================================================
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    # PKDT — Package Data (12 bytes in TES5)
    # Flags(U32) + Type(U8) + InterruptOverride(U8) + PreferredSpeed(U8) + pad(U8)
    # + InterruptFlags(U16) + pad(U16) = 12 bytes
    tes4_flags = get_int(rec, 'PKDT.Flags')

    # TES5 type 18 = Package (generic)
    tes5_type = 18

    # Map TES4 flags to TES5 flags (best effort)
    tes5_flags = 0
    if tes4_flags & 0x04:    # MustComplete
        tes5_flags |= 0x04
    if tes4_flags & 0x08:    # LockDoorsAtStart
        tes5_flags |= 0x08
    if tes4_flags & 0x200:   # OncePerDay
        tes5_flags |= 0x200
    if tes4_flags & 0x2000:  # AlwaysRun
        tes5_flags |= 0x2000000
    if tes4_flags & 0x4000:  # AlwaysSneak
        tes5_flags |= 0x4000

    subs += pack_subrecord('PKDT', struct.pack('<IBBBBHH',
                                                tes5_flags, tes5_type, 0, 0, 0, 0, 0))

    # PSDT — Schedule Data (12 bytes)
    month = get_int(rec, 'PSDT.Month')
    dow = get_int(rec, 'PSDT.DayOfWeek')
    date = get_int(rec, 'PSDT.Date')
    time_val = get_int(rec, 'PSDT.Time')
    duration = get_int(rec, 'PSDT.Duration')
    subs += pack_subrecord('PSDT', struct.pack('<bbbbb3xi',
                                                month, dow, date, time_val, 0, duration))

    # PKCU — Package Use (12 bytes: DataInputCount + PackageTemplate + VersionCounter)
    # Null template = custom package
    subs += pack_subrecord('PKCU', struct.pack('<III', 0, 0, 0))

    # XNAM — Marker (empty, required)
    subs += pack_subrecord('XNAM', b'')

    # OnBegin marker (required)
    subs += pack_subrecord('POBA', b'')
    subs += pack_formid_subrecord('INAM', 0)  # Idle = NULL

    # OnEnd marker (required)
    subs += pack_subrecord('POEA', b'')
    subs += pack_formid_subrecord('INAM', 0)  # Idle = NULL

    # OnChange marker (required)
    subs += pack_subrecord('POCA', b'')
    subs += pack_formid_subrecord('INAM', 0)  # Idle = NULL

    return pack_record('PACK', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)


def _wthr_cloud_sig(layer: int) -> bytes:
    """Build the 4-byte cloud texture signature for a given layer index (0-28).

    Layer 0-16:  first byte is chr(0x30 + layer), rest is '0TX'
                 e.g. layer 0 = '00TX', layer 1 = '10TX', layer 10 = ':0TX'
    Layer 17-28: first byte is chr(0x41 + (layer - 17)), rest is '0TX'
                 e.g. layer 17 = 'A0TX', layer 18 = 'B0TX'
    """
    if layer <= 16:
        return bytes([0x30 + layer]) + b'0TX'
    else:
        return bytes([0x41 + (layer - 17)]) + b'0TX'


def convert_WTHR(rec: dict) -> bytes:
    """WTHR — Weather conversion.

    TES5 subrecord order (from wbDefinitionsTES5.pas):
    EDID, cloud textures (00TX..L0TX), DNAM(unused), CNAM(unused), ANAM(unused),
    BNAM(unused), LNAM, MNAM, NNAM, ONAM(unused), RNAM, QNAM, PNAM, JNAM,
    NAM0, FNAM, DATA, NAM1, SNAM(sounds), TNAM, IMSP, HNAM, DALC×4,
    NAM2(unused), NAM3(unused), MODL/MODT(aurora), GNAM
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    # Cloud layer textures — use proper pack_subrecord with correct 4-byte signatures
    lower_cloud = get_str(rec, 'CNAM.LowerCloudLayer')
    upper_cloud = get_str(rec, 'DNAM.UpperCloudLayer')
    if lower_cloud:
        # Layer 0 = signature '00TX' (bytes 0x30,0x30,0x54,0x58)
        sig = _wthr_cloud_sig(0)
        path_bytes = _prefix_path(lower_cloud).encode('utf-8') + b'\x00'
        subs += sig + struct.pack('<H', len(path_bytes)) + path_bytes
    if upper_cloud:
        # Layer 1 = signature '10TX' (bytes 0x31,0x30,0x54,0x58)
        sig = _wthr_cloud_sig(1)
        path_bytes = _prefix_path(upper_cloud).encode('utf-8') + b'\x00'
        subs += sig + struct.pack('<H', len(path_bytes)) + path_bytes

    # LNAM — unknown (4 bytes)
    subs += pack_subrecord('LNAM', struct.pack('<I', 0))

    # NAM0 — Color data (TES5 expects up to 272 bytes for weather colors)
    nam0 = bytearray(272)
    for i in range(0, 272, 4):
        nam0[i] = 128; nam0[i+1] = 128; nam0[i+2] = 128; nam0[i+3] = 255
    subs += pack_subrecord('NAM0', bytes(nam0))

    # FNAM — Fog distances (TES5: 32 bytes — 8 floats)
    fog_day_near = get_float(rec, 'FNAM.FogDayNear', 100.0)
    fog_day_far = get_float(rec, 'FNAM.FogDayFar', 100000.0)
    fog_night_near = get_float(rec, 'FNAM.FogNightNear', 100.0)
    fog_night_far = get_float(rec, 'FNAM.FogNightFar', 100000.0)
    fnam = struct.pack('<ffffffff',
                        fog_day_near, fog_day_far,
                        fog_night_near, fog_night_far,
                        1.0, 1.0,    # Day/Night power
                        1.0, 1.0)    # Day/Night max
    subs += pack_subrecord('FNAM', fnam)

    # DATA — Weather Data (19 bytes in TES5)
    wind_speed = get_int(rec, 'DATA.WindSpeed')
    trans_delta = get_int(rec, 'DATA.TransDelta')
    sun_glare = get_int(rec, 'DATA.SunGlare')
    sun_damage = get_int(rec, 'DATA.SunDamage')
    data = struct.pack('<B2xBBBBBBBBB3xBBBB',
                        wind_speed, trans_delta, sun_glare, sun_damage,
                        0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    subs += pack_subrecord('DATA', data)

    # NAM1 — Disabled cloud layers (U32)
    subs += pack_uint32_subrecord('NAM1', 0xFFFFFFFF)  # All disabled (no valid cloud data)

    # Sounds — SNAM (after NAM1 per xEdit)
    sc = get_int(rec, 'SoundCount')
    for i in range(sc):
        sfid = get_formid(rec, f'Sound[{i}].FormID')
        stype = get_int(rec, f'Sound[{i}].Type')
        if sfid:
            subs += pack_subrecord('SNAM', struct.pack('<II', sfid, stype))

    # IMSP — Image Spaces (4 FormIDs: sunrise/day/sunset/night) — after sounds
    subs += pack_subrecord('IMSP', struct.pack('<IIII', 0, 0, 0, 0))

    # DALC — Directional Ambient Lighting Colors (4 sections × 24 bytes)
    for _section in range(4):
        dalc = bytearray(24)
        for i in range(0, 24, 4):
            dalc[i] = 128; dalc[i+1] = 128; dalc[i+2] = 128; dalc[i+3] = 0
        subs += pack_subrecord('DALC', bytes(dalc))

    return pack_record('WTHR', get_formid(rec, 'FormID'), get_int(rec, 'RecordFlags'), subs)
