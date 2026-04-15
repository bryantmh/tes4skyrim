"""Dialog converter — QUST, DIAL, INFO, DLBR, DLVW conversion.

All dialog conversion logic lives here. Moved from dialog_misc.py
(which now only has SOUN/PACK/WTHR) and import_main.py.

Key fixes vs the old code:
1. Complete TES4→TES5 condition function remap/drop table (59 indices)
   - Fixes FGD00JoinFG: index 249=GetPCFame→IsInDialogueWithPlayer was
     creating a circular dependency that broke ALL dialogue.
2. Type=1 (Conversation) topics now get DLBR — they're not exclusively
   chain topics. INFOGENERAL (Rumors) appears as a top-level dialog option.
3. Simplified condition injection: barks get voice types, conversation
   topics get NPC restriction (GetIsID) only when needed.
"""

import struct
from collections import defaultdict

from .text_reader import get_formid_index_offset
from .record_types.common import (
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

# ---------------------------------------------------------------------------
# Condition function index tables
# ---------------------------------------------------------------------------

_FUNC_GET_IS_ID = 72           # GetIsID(npc_formid)
_FUNC_GET_IS_VOICE_TYPE = 426  # GetIsVoiceType(vtyp_formid)
_FUNC_GET_STAGE = 58           # GetStage(quest_formid)
_FUNC_GET_QUEST_RUNNING = 56   # GetQuestRunning(quest_formid)

# TES4 function indices that are REUSED in TES5 for a DIFFERENT function.
# These MUST be remapped or dropped, otherwise they evaluate as the wrong
# TES5 function and cause unpredictable behaviour.
#
# Index 249 is the critical one: TES4=GetPCFame, TES5=IsInDialogueWithPlayer.
# If left as-is on a StartGameEnabled quest, it creates a circular dependency
# (quest only "runs" during dialogue → dialogue checks if quest is running)
# that breaks ALL dialogue when the quest has a stage set.
#
# REMAP: TES4 index → TES5 index (same semantic function, different index)
_FUNC_REMAP = {
    101: 263,   # IsWeaponOut → IsWeaponOut
    116: 459,   # GetCrimeGold → GetCrimeGold
    127: 497,   # CanPayCrimeGold → CanPayCrimeGold
}

# DROP: TES4 indices with no TES5 equivalent.
# Includes both TES4-only functions AND reused-index functions where the
# TES4 meaning has no semantic equivalent in TES5.
_FUNC_DROP = frozenset({
    # --- TES4-only functions (not in TES5 at all) ---
    40,   # GetVampire — replaced by keyword checks in TES5
    76,   # GetDisposition — disposition system removed
    # --- Reused index: TES4=GetTalkedToPC, TES5=GetActorValue ---
    50,   # TES4=GetTalkedToPC → TES5=GetActorValue (zero param1 survives as junk)
    104,  # IsYielding — not in TES5
    160,  # GetFurnitureMarkerID — removed in TES5
    171,  # IsPlayerInJail — not in TES5
    201,  # GetPCFactionSubmitAuthority — removed
    251,  # GetPCInfamy — no infamy system in TES5
    # --- Reused indices: TES4 function completely different from TES5 ---
    81,   # TES4=GetArmorRating      → TES5=IsRotating
    109,  # TES4=GetWeaponSkillType  → TES5=IsWeaponSkillType (semantics changed)
    180,  # TES4=GetDetectionLevel   → TES5=HasSameEditorLocAsRef
    197,  # TES4=GetPCFactionSteal   → TES5=GetPCEnemyofFaction
    224,  # TES4=GetIsPlayerBirthsign→ TES5=GetVATSMode
    227,  # TES4=HasVampireFed       → TES5=GetCannibal
    249,  # TES4=GetPCFame           → TES5=IsInDialogueWithPlayer
    258,  # TES4=GetUsedItemLevel    → TES5=HasAssociationType
    259,  # TES4=GetUsedItemActivate → TES5=HasFamilyRelationship
    264,  # TES4=GetBarterGold       → TES5=HasSpell
    274,  # TES4=GetArmorRatingUpperBody → TES5=IsSmallBump
    305,  # TES4=GetInvestmentGold   → TES5=GetPlayerAction
    313,  # TES4=IsActorEvil         → TES5=GetPairedAnimation
    323,  # TES4=WhichServiceMenu    → TES5=GetCombatState
    329,  # TES4=IsTurnArrest        → TES5=IsFleeing
    362,  # TES4=GetPlayerHasLastRiddenHorse → TES5=HasLinkedRef
    365,  # TES4=GetPlayerInSEWorld  → TES5=IsChild
    # --- TES4 supplemental function indices ---
    1107, # OBSE: IsAmmo
    1122, # OBSE: HasSpell (could remap to TES5 264 but semantics differ)
    1124, # OBSE: IsClassSkill
    1884, # OBSE: GetPCTrainingSessionsUsed
})


def _convert_ctda(raw: bytes) -> 'bytes | None':
    """Convert a 24-byte TES4 CTDA to 32-byte TES5 CTDA.

    Returns None for functions that must be dropped (no TES5 equivalent).
    Remaps function indices where TES4 and TES5 share the same semantic
    function at different indices.
    """
    offset = get_formid_index_offset()
    data = raw + b'\x00' * max(0, 24 - len(raw))

    type_byte = data[0]
    comp_raw = struct.unpack_from('<I', data, 4)[0]
    func_idx = struct.unpack_from('<H', data, 8)[0]
    param1 = struct.unpack_from('<I', data, 12)[0]
    param2 = struct.unpack_from('<I', data, 16)[0]
    run_on = struct.unpack_from('<I', data, 20)[0]

    # Drop TES4-only and reused-index functions
    if func_idx in _FUNC_DROP:
        return None

    # Remap function indices that moved to a different slot in TES5
    func_idx = _FUNC_REMAP.get(func_idx, func_idx)

    def _remap(v: int) -> int:
        if offset and (v >> 24) == 0x00 and (v & 0x00FFFFFF) >= 0x100:
            return (v & 0x00FFFFFF) | (offset << 24)
        return v

    # Use Global flag: bit 2 (0x04) — comparison value is a Global FormID
    if type_byte & 0x04:
        comp_raw = _remap(comp_raw)
    param1 = _remap(param1)
    param2 = _remap(param2)

    return struct.pack('<B3xIHHIIIII',
                       type_byte, comp_raw,
                       func_idx, 0,
                       param1, param2, run_on,
                       0,           # Reference (RunOn=2)
                       0xFFFFFFFF)  # Unknown


# ---------------------------------------------------------------------------
# QUST conversion
# ---------------------------------------------------------------------------

_PLAYER_FORMID = 0x14


def _collect_scro_properties(rec: dict, fid_to_edid: dict) -> dict:
    """Extract SCRO FormID references from a record for VMAD properties."""
    from script_convert.constants import _safe_property_name
    props = {}
    seen_lower = set()
    i = 0
    while True:
        key = f'SCRO[{i}]'
        fid_str = rec.get(key)
        if fid_str is None:
            break
        i += 1
        try:
            raw_fid = int(fid_str, 16)
        except (ValueError, TypeError):
            continue
        if raw_fid == _PLAYER_FORMID or raw_fid == 0:
            continue
        edid = fid_to_edid.get(raw_fid)
        if not edid:
            continue
        safe = _safe_property_name(edid)
        low = safe.lower()
        if low in seen_lower:
            continue
        seen_lower.add(low)
        remapped = get_formid(rec, key)
        if remapped:
            props[safe] = remapped
    return props


def _collect_all_scro_properties(rec: dict, fid_to_edid: dict) -> dict:
    """Collect SCRO properties from record-level and all stage/log levels."""
    from script_convert.constants import _safe_property_name
    props = _collect_scro_properties(rec, fid_to_edid)
    stage_count = get_int(rec, 'StageCount')
    for i in range(stage_count):
        log_count = get_int(rec, f'Stage[{i}].LogCount')
        for j in range(log_count):
            prefix = f'Stage[{i}].Log[{j}].'
            k = 0
            while True:
                key = f'{prefix}SCRO[{k}]'
                if rec.get(key) is None:
                    break
                fid_str = rec[key]
                try:
                    raw_fid = int(fid_str, 16)
                except (ValueError, TypeError):
                    k += 1
                    continue
                if raw_fid != _PLAYER_FORMID and raw_fid != 0:
                    edid = fid_to_edid.get(raw_fid)
                    if edid:
                        safe = _safe_property_name(edid)
                        if safe.lower() not in {k2.lower() for k2 in props}:
                            remapped = get_formid(rec, key)
                            if remapped:
                                props[safe] = remapped
                k += 1
    return props


def convert_QUST(rec: dict, fid_to_edid: dict = None,
                 well_known_props: dict = None) -> bytes:
    """QUST — Quest conversion.

    DNAM is 12 bytes: Flags(U16) Priority(U8) FormVersion(U8=0) Unknown(4) Type(U32).
    TES4 quests with SGE get StartsEnabled in TES5 so they actually run.
    HasDialogueData (0x8000) is NEVER set — Skyrim.esm never uses it.
    Dialog topics are owned by the universal TES4Dialogue quest, not these.
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    # VMAD — quest stage script fragments
    stage_count = get_int(rec, 'StageCount')
    stage_frags = []
    for i in range(stage_count):
        log_count = get_int(rec, f'Stage[{i}].LogCount')
        stage_idx = get_int(rec, f'Stage[{i}].Index')
        if log_count > 0:
            for j in range(log_count):
                has_script = bool(get_str(rec, f'Stage[{i}].Log[{j}].ResultScript'))
                if has_script:
                    stage_frags.append((stage_idx, j))
        else:
            has_script = bool(get_str(rec, f'Stage[{i}].ResultScript'))
            if has_script:
                stage_frags.append((stage_idx, 0))
    if stage_frags and edid:
        from script_convert.pipeline import build_vmad_quest_fragments
        prop_vals = _collect_all_scro_properties(rec, fid_to_edid) if fid_to_edid else {}
        if well_known_props:
            prop_vals.update(well_known_props)
        vmad = build_vmad_quest_fragments(edid, stage_frags,
                                          property_values=prop_vals or None)
        subs += pack_subrecord('VMAD', vmad)

    full = get_str(rec, 'FULL')
    if full:
        subs += pack_string_subrecord('FULL', full)

    # DNAM
    flags = get_int(rec, 'DATA.Flags')
    priority = get_int(rec, 'DATA.Priority')
    safe_flags = flags & 0x0009  # StartGameEnabled + AllowRepeatedStages

    # TES4 quests with SGE need StartsEnabled in TES5 to actually run.
    if safe_flags & 0x0001:
        safe_flags |= 0x0010  # StartsEnabled

    dnam = struct.pack('<HBBII', safe_flags, priority, 0, 0, 0)
    subs += pack_subrecord('DNAM', dnam)

    # NEXT — required empty marker
    subs += pack_subrecord('NEXT', b'')

    # Stages
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

    # Objectives (mirror each stage with journal text as an objective)
    for i in range(stage_count):
        stage_idx = get_int(rec, f'Stage[{i}].Index')
        log_count = get_int(rec, f'Stage[{i}].LogCount')
        if log_count > 0:
            for j in range(log_count):
                obj_text = get_str(rec, f'Stage[{i}].Log[{j}].Text')
                if obj_text:
                    subs += pack_subrecord('QOBJ', struct.pack('<H', stage_idx))
                    subs += pack_uint32_subrecord('FNAM', 0)
                    subs += pack_string_subrecord('NNAM', obj_text)
        else:
            obj_text = get_str(rec, f'Stage[{i}].LogEntry')
            if obj_text:
                subs += pack_subrecord('QOBJ', struct.pack('<H', stage_idx))
                subs += pack_uint32_subrecord('FNAM', 0)
                subs += pack_string_subrecord('NNAM', obj_text)

    # ANAM — required next alias ID
    subs += pack_uint32_subrecord('ANAM', 0)

    return pack_record('QUST', get_formid(rec, 'FormID'),
                       get_int(rec, 'RecordFlags'), subs)


# ---------------------------------------------------------------------------
# DIAL conversion
# ---------------------------------------------------------------------------

# TES4 DATA.Type values
DIAL_TYPE_TOPIC = 0
DIAL_TYPE_CONVERSATION = 1
DIAL_TYPE_COMBAT = 2
DIAL_TYPE_PERSUASION = 3
DIAL_TYPE_DETECTION = 4
DIAL_TYPE_SERVICE = 5
DIAL_TYPE_MISC = 6

# Known EditorID → (TES5 subtype enum, SNAM 4-char code)
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

# Category overrides for bark subtypes
_SUBTYPE_CATEGORY = {
    79: 7, 78: 7, 94: 7, 77: 7, 76: 7, 75: 7, 68: 7, 67: 7, 66: 7, 70: 7, 17: 7,
    26: 3, 29: 3, 30: 3, 38: 3, 49: 3,
    57: 5, 63: 5,
}

# All bark subtypes (non-interactive, no DLBR needed)
BARK_SUBTYPES = frozenset(_SUBTYPE_CATEGORY.keys())

# EditorID → known subtype int (for bark classification)
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
    # Additional barks
    'Yield': 26, 'AcceptYield': 26, 'Pickpocket': 38,
    'Assault': 26, 'Murder': 26, 'PowerAttack': 26,
    'AssaultNoCrime': 26, 'MurderNoCrime': 26,
    'PickpocketNoCrime': 38, 'StealNoCrime': 38, 'TrespassNoCrime': 49,
    'BarterBuyItem': 70, 'BarterSellItem': 70, 'BarterExit': 70,
    'BarterStolen': 70, 'Training': 67, 'RepairExit': 67,
    'Recharge': 67, 'RechargeExit': 67, 'TrainingExit': 67,
    # Type=1 system topics that are barks (not player-selectable)
    'AnswerStatus': 94, 'TRANSITION': 94,
}

# Types to skip entirely
_DIAL_SKIP_TYPES = frozenset({DIAL_TYPE_PERSUASION, DIAL_TYPE_SERVICE})
_DIAL_SKIP_EDIDS = frozenset({
    'CreatureResponses', 'SECreatureResponses',
    'TamrielGateResponses', 'ANY',
})


def should_skip_dial(rec: dict) -> bool:
    """Return True if this DIAL topic should be skipped entirely."""
    dtype = get_int(rec, 'DATA.Type')
    if dtype in _DIAL_SKIP_TYPES:
        return True
    edid = get_str(rec, 'EditorID', '')
    if edid in _DIAL_SKIP_EDIDS:
        return True
    if edid.startswith('Test') or edid.startswith('MarkNTest'):
        return True
    return False


def is_bark_topic(edid: str, dtype: int = -1) -> bool:
    """Return True if this DIAL topic is a bark (non-interactive).

    Type 2 (Combat), 4 (Detection), 6 (Misc) → always bark.
    Known bark EditorIDs → bark.
    Everything else (Type 0 Topic, Type 1 Conversation) → NOT bark.
    """
    if dtype in (DIAL_TYPE_COMBAT, DIAL_TYPE_DETECTION, DIAL_TYPE_MISC):
        return True
    if _EDID_TO_SUBTYPE_INT.get(edid or '', -1) in BARK_SUBTYPES:
        return True
    return False


def convert_DIAL(rec: dict, info_count: int = 0, dlbr_fid: int = 0,
                 quest_fid_override: int = 0) -> bytes:
    """DIAL — Dialog Topic conversion.

    TES5 order: EDID FULL PNAM BNAM QNAM DATA SNAM TIFC
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    full = get_str(rec, 'FULL')
    if full:
        subs += pack_string_subrecord('FULL', full)

    subs += pack_subrecord('PNAM', struct.pack('<f', 50.0))

    if dlbr_fid:
        subs += pack_formid_subrecord('BNAM', dlbr_fid)

    # QNAM — required
    qfid = quest_fid_override
    if not qfid:
        qcount = get_int(rec, 'QuestCount')
        if qcount > 0:
            qfid = get_formid(rec, 'Quest[0]')
    if qfid:
        subs += pack_formid_subrecord('QNAM', qfid)

    # Resolve subtype
    dtype = get_int(rec, 'DATA.Type')
    subtype_int, snam_code = _EDID_TO_SUBTYPE.get(edid or '', (0, b'CUST'))
    if subtype_int == 0:
        if dtype == DIAL_TYPE_COMBAT:
            subtype_int, snam_code = 26, b'ATCK'
        elif dtype == DIAL_TYPE_DETECTION:
            subtype_int, snam_code = 57, b'NOTA'
        elif dtype == DIAL_TYPE_MISC:
            subtype_int, snam_code = 94, b'IDLE'

    category = _SUBTYPE_CATEGORY.get(subtype_int, 0)

    subs += pack_subrecord('DATA', struct.pack('<BBH', 0, category, subtype_int))
    subs += pack_subrecord('SNAM', snam_code)
    subs += pack_uint32_subrecord('TIFC', info_count)

    return pack_record('DIAL', get_formid(rec, 'FormID'),
                       get_int(rec, 'RecordFlags'), subs)


# ---------------------------------------------------------------------------
# INFO conversion
# ---------------------------------------------------------------------------

def _build_info_script_properties(result_script: str, xref) -> dict:
    """Build VMAD property bindings for an INFO result script."""
    if not xref:
        return {}
    from script_convert.converter import ScriptConverter
    offset = get_formid_index_offset()
    try:
        conv = ScriptConverter(xref)
        conv.convert_fragment(result_script, 'TopicInfo')
    except Exception:
        return {}
    prop_vals = {}
    for prop_edid, _ptype in conv._property_refs.items():
        low = prop_edid.lower()
        if low in ('player', 'playerref'):
            prop_vals[prop_edid] = _PLAYER_FORMID
            continue
        fid_hex = xref.edid_to_formid.get(low, '')
        if not fid_hex:
            continue
        try:
            raw_fid = int(fid_hex, 16)
        except (ValueError, TypeError):
            continue
        if raw_fid == 0:
            continue
        if offset and (raw_fid >> 24) == 0x00 and (raw_fid & 0x00FFFFFF) >= 0x100:
            raw_fid = (raw_fid & 0x00FFFFFF) | (offset << 24)
        prop_vals[prop_edid] = raw_fid
    return prop_vals


def convert_INFO(rec: dict, voice_type_ctdas: bytes = b'',
                 is_bark: bool = False,
                 fid_to_edid: dict = None,
                 well_known_props: dict = None,
                 xref=None) -> bytes:
    """INFO — Dialog response conversion.

    TES5 order: EDID [VMAD] ENAM CNAM [TCLT...] [TRDT NAM1 NAM2 NAM3]* CTDAs
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    # VMAD
    info_fid = get_str(rec, 'FormID') or ''
    result_script = get_str(rec, 'ResultScript')
    if result_script and info_fid:
        # Strip comment-only lines — TES4 ";Choice" directives are metadata,
        # not executable code. Generating VMAD for them creates references to
        # non-existent .pex scripts which can block TCLT processing.
        code_lines = [ln for ln in result_script.strip().splitlines()
                      if ln.strip() and not ln.strip().startswith(';')]
        if code_lines:
            from script_convert.pipeline import build_vmad_info_fragment
            prop_vals = _build_info_script_properties(result_script, xref)
            if well_known_props:
                prop_vals.update(well_known_props)
            vmad = build_vmad_info_fragment(info_fid,
                                           property_values=prop_vals or None)
            subs += pack_subrecord('VMAD', vmad)

    # ENAM
    tes4_flags = get_int(rec, 'DATA.Flags')
    subs += pack_subrecord('ENAM', struct.pack('<HH', tes4_flags & 0x37, 0))

    # CNAM
    subs += pack_subrecord('CNAM', struct.pack('<B', 0))

    # TCLT
    choice_count = get_int(rec, 'ChoiceCount')
    if choice_count > 0:
        for i in range(choice_count):
            cfid = get_formid(rec, f'Choice[{i}]')
            if cfid:
                subs += pack_formid_subrecord('TCLT', cfid)
    else:
        cfid = get_formid(rec, 'TCLT.Choice')
        if cfid:
            subs += pack_formid_subrecord('TCLT', cfid)

    # Responses
    rc = get_int(rec, 'ResponseCount')
    for i in range(rc):
        emotion = get_int(rec, f'Response[{i}].EmotionType')
        emotion_val = get_int(rec, f'Response[{i}].EmotionValue')
        text = get_str(rec, f'Response[{i}].ResponseText')
        actor_notes = get_str(rec, f'Response[{i}].ActorNotes')
        resp_num = get_int(rec, f'Response[{i}].ResponseNumber') or (i + 1)
        subs += pack_subrecord('TRDT', struct.pack('<IiI B3x I B3x',
                                                    emotion, emotion_val, 0,
                                                    resp_num, 0, 1))
        if text:
            subs += pack_string_subrecord('NAM1', text)
        subs += pack_string_subrecord('NAM2', actor_notes if actor_notes else '')
        subs += pack_string_subrecord('NAM3', '')

    # Voice type CTDAs first
    if voice_type_ctdas:
        subs += voice_type_ctdas

    # TES4 conditions — convert with remap/drop.
    # GetStageDone, GetQuestRunning, GetQuestCompleted) since TES4 quests
    # won't be running in TES5.
    # Must fix OR chain integrity: if a dropped condition had the OR flag,
    # the previous condition's OR semantics must be preserved.
    cc = get_int(rec, 'ConditionCount')
    converted_ctdas = []
    for i in range(cc):
        raw_hex = rec.get(f'Condition[{i}].Raw', '')
        if raw_hex:
            try:
                raw = bytes.fromhex(raw_hex)
                ctda = _convert_ctda(raw)
                if ctda is not None:
                    converted_ctdas.append(ctda)
                else:
                    # Condition was dropped. If it had the OR flag (bit 0),
                    # we need to carry that OR forward to the next surviving
                    # condition so the OR chain isn't broken.
                    if raw[0] & 0x01 and converted_ctdas:
                        # This dropped condition was OR'd with the previous.
                        # Nothing to do — the previous condition already has
                        # its own OR/AND flag. Dropping an OR participant
                        # is equivalent to removing it from the OR group.
                        pass
            except (ValueError, struct.error):
                pass

    # Fix OR chain: if the LAST converted CTDA has the OR flag, clear it.
    # A trailing OR flag with nothing after it is invalid.
    if converted_ctdas:
        last = converted_ctdas[-1]
        if last[0] & 0x01:
            converted_ctdas[-1] = bytes([last[0] & ~0x01]) + last[1:]

    for ctda in converted_ctdas:
        subs += pack_subrecord('CTDA', ctda)

    return pack_record('INFO', get_formid(rec, 'FormID'),
                       get_int(rec, 'RecordFlags'), subs)


# ---------------------------------------------------------------------------
# DLBR / DLVW creation
# ---------------------------------------------------------------------------

def make_dlbr(fid: int, edid: str, quest_fid: int, dial_fid: int,
              top_level: bool = True) -> bytes:
    """Create a DLBR (Dialog Branch) record."""
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_formid_subrecord('QNAM', quest_fid)
    subs += pack_uint32_subrecord('TNAM', 0)   # Player
    subs += pack_uint32_subrecord('DNAM', 1 if top_level else 0)
    subs += pack_formid_subrecord('SNAM', dial_fid)
    return pack_record('DLBR', fid, 0, subs)


def make_dlvw(fid: int, edid: str, quest_fid: int,
              branch_fids: list, topic_fids: list) -> bytes:
    """Create a DLVW (Dialog View) record."""
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_formid_subrecord('QNAM', quest_fid)
    for bfid in branch_fids:
        subs += pack_formid_subrecord('BNAM', bfid)
    for tfid in topic_fids:
        subs += pack_formid_subrecord('TNAM', tfid)
    subs += pack_uint32_subrecord('ENAM', 0)
    subs += pack_uint8_subrecord('DNAM', 0)
    return pack_record('DLVW', fid, 0, subs)


# ---------------------------------------------------------------------------
# CTDA helpers for voice type / NPC restriction injection
# ---------------------------------------------------------------------------

def _build_ctda_equal_1(func_idx: int, param1_fid: int,
                        is_or: bool = False) -> bytes:
    """Build a 32-byte CTDA: func(param1) == 1.0, with optional OR flag."""
    type_byte = 0x01 if is_or else 0x00
    return struct.pack('<B3xIHHIIIII',
                       type_byte, 0x3F800000,
                       func_idx, 0,
                       param1_fid, 0, 0, 0, 0xFFFFFFFF)


def build_voice_type_ctda(vtyp_fid: int, is_or: bool = False) -> bytes:
    """Build a single GetIsVoiceType CTDA."""
    return _build_ctda_equal_1(_FUNC_GET_IS_VOICE_TYPE, vtyp_fid, is_or)


def build_getisid_ctda(npc_fid: int, is_or: bool = False) -> bytes:
    """Build a single GetIsID CTDA."""
    return _build_ctda_equal_1(_FUNC_GET_IS_ID, npc_fid, is_or)


def build_quest_running_ctda(quest_fid: int) -> bytes:
    """Build a GetQuestRunning(quest_fid) == 1.0 CTDA (AND, no OR)."""
    return _build_ctda_equal_1(_FUNC_GET_QUEST_RUNNING, quest_fid)


def build_voice_type_ctdas_for_info(rec: dict, npc_to_vtyp: dict,
                                     topic_vtyps: set = None) -> bytes:
    """Build packed GetIsVoiceType CTDA subrecords for an INFO.

    NPC-specific INFOs: map GetIsID NPC → voice type.
    Generic INFOs: use topic_vtyps from NPC-specific siblings.
    Bark INFOs without GetIsID: skip voice type (generic barks match any NPC).
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

    if not npc_fids:
        # For greeting/goodbye barks (HELO/GBYE): use topic_vtyps collected
        # from NPC-specific siblings, same as conversation topics.
        # For other barks (combat, flee, etc.): skip — they're generic.
        vtyp_fids = topic_vtyps if topic_vtyps else set()
        if not vtyp_fids:
            return b''
    else:
        vtyp_fids = set()
        for npc_fid in npc_fids:
            remapped = npc_fid
            if offset and (npc_fid >> 24) == 0x00 and (npc_fid & 0x00FFFFFF) >= 0x100:
                remapped = (npc_fid & 0x00FFFFFF) | (offset << 24)
            vtyp = npc_to_vtyp.get(remapped, 0) or npc_to_vtyp.get(npc_fid, 0)
            if vtyp:
                vtyp_fids.add(vtyp)

    if not vtyp_fids:
        return b''

    result = b''
    vtyp_list = sorted(vtyp_fids)
    for idx, vfid in enumerate(vtyp_list):
        is_last = (idx == len(vtyp_list) - 1)
        result += pack_subrecord('CTDA', build_voice_type_ctda(vfid, is_or=not is_last))
    return result


def build_topic_npc_ctdas(npc_fids: set) -> bytes:
    """Build packed GetIsID CTDA subrecords for topic NPC restriction."""
    if not npc_fids:
        return b''
    result = b''
    npc_list = sorted(npc_fids)
    for idx, nfid in enumerate(npc_list):
        is_last = (idx == len(npc_list) - 1)
        result += pack_subrecord('CTDA', build_getisid_ctda(nfid, is_or=not is_last))
    return result


def info_has_positive_getisid(rec: dict) -> bool:
    """Return True if INFO has at least one GetIsID(X)==1.0 condition."""
    cc = get_int(rec, 'ConditionCount')
    for i in range(cc):
        raw_hex = rec.get(f'Condition[{i}].Raw', '')
        if not raw_hex or len(raw_hex) < 24:
            continue
        try:
            raw = bytes.fromhex(raw_hex)
            func_idx = struct.unpack_from('<H', raw, 8)[0]
            if func_idx != _FUNC_GET_IS_ID:
                continue
            comp_type = (raw[0] >> 5) & 0x07
            comp_val = struct.unpack_from('<f', raw, 4)[0]
            if comp_type == 0 and comp_val == 1.0:
                return True
        except (ValueError, struct.error):
            continue
    return False


def collect_topic_npc_fids(child_infos: list, offset: int) -> set:
    """Collect remapped NPC FormIDs from GetIsID(X)==1.0 in a topic."""
    npc_fids = set()
    for info_rec in child_infos:
        cc = get_int(info_rec, 'ConditionCount')
        for i in range(cc):
            raw_hex = info_rec.get(f'Condition[{i}].Raw', '')
            if not raw_hex or len(raw_hex) < 24:
                continue
            try:
                raw = bytes.fromhex(raw_hex)
                func_idx = struct.unpack_from('<H', raw, 8)[0]
                if func_idx != _FUNC_GET_IS_ID:
                    continue
                comp_type = (raw[0] >> 5) & 0x07
                comp_val = struct.unpack_from('<f', raw, 4)[0]
                if comp_type != 0 or comp_val != 1.0:
                    continue
                param1 = struct.unpack_from('<I', raw, 12)[0]
                remapped = param1
                if offset and (param1 >> 24) == 0x00 and (param1 & 0x00FFFFFF) >= 0x100:
                    remapped = (param1 & 0x00FFFFFF) | (offset << 24)
                npc_fids.add(remapped)
            except (ValueError, struct.error):
                continue
    return npc_fids


# ---------------------------------------------------------------------------
# Stage gating (AddTopic script parsing)
# ---------------------------------------------------------------------------

def build_topic_stage_gating(by_type: dict, offset: int) -> dict:
    """Build topic gating map from AddTopic commands in TES4 scripts.

    Returns: {topic_fid: [(quest_fid, min_stage), ...]}
    """
    import re
    from collections import defaultdict as _defaultdict

    re_setstage = re.compile(r'\bsetstage\s+(\w+)\s+(\d+)', re.IGNORECASE)
    re_addtopic = re.compile(r'\baddtopic\s+(\w+)', re.IGNORECASE)

    infos = by_type.get('INFO', [])
    quests = by_type.get('QUST', [])
    dials = by_type.get('DIAL', [])

    dial_edid_to_fid = {}
    for rec in dials:
        edid = get_str(rec, 'EditorID')
        fid = get_formid(rec, 'FormID')
        if edid and fid:
            dial_edid_to_fid[edid.lower()] = fid

    quest_edid_to_fid = {}
    for rec in quests:
        edid = get_str(rec, 'EditorID')
        fid = get_formid(rec, 'FormID')
        if edid and fid:
            quest_edid_to_fid[edid.lower()] = fid

    topic_gating_raw: dict[str, list] = _defaultdict(list)

    for rec in infos:
        script = get_str(rec, 'ResultScript')
        if not script:
            continue
        addtopics = re_addtopic.findall(script)
        setstages = re_setstage.findall(script)
        if addtopics and setstages:
            quest_edid, stage_str = setstages[0]
            stage = int(stage_str)
            for at in addtopics:
                topic_gating_raw[at.lower()].append((quest_edid.lower(), stage))

    for rec in quests:
        quest_edid = get_str(rec, 'EditorID')
        if not quest_edid:
            continue
        stage_count = get_int(rec, 'StageCount')
        for i in range(stage_count):
            stage_idx = get_int(rec, f'Stage[{i}].Index')
            scripts = []
            s = get_str(rec, f'Stage[{i}].ResultScript')
            if s:
                scripts.append(s)
            log_count = get_int(rec, f'Stage[{i}].LogCount')
            for j in range(log_count):
                s = get_str(rec, f'Stage[{i}].Log[{j}].ResultScript')
                if s:
                    scripts.append(s)
            for script in scripts:
                addtopics = re_addtopic.findall(script)
                for at in addtopics:
                    topic_gating_raw[at.lower()].append(
                        (quest_edid.lower(), stage_idx))

    result: dict[int, list] = {}
    for topic_edid, gate_list in topic_gating_raw.items():
        topic_fid = dial_edid_to_fid.get(topic_edid)
        if not topic_fid:
            continue
        by_quest: dict[str, list] = _defaultdict(list)
        for q, s in gate_list:
            by_quest[q].append(s)
        gates = []
        for q, stages in by_quest.items():
            min_stage = min(stages)
            if min_stage <= 0:
                continue
            quest_fid = quest_edid_to_fid.get(q)
            if quest_fid:
                gates.append((quest_fid, min_stage))
        if gates:
            result[topic_fid] = gates

    return result


def build_stage_gate_ctdas(gates: list) -> bytes:
    """Build CTDA subrecords for stage gating: GetStage(quest) >= min_stage."""
    if not gates:
        return b''
    result = b''
    for idx, (quest_fid, min_stage) in enumerate(sorted(gates)):
        is_last = (idx == len(gates) - 1)
        type_byte = 0x60  # >=
        if not is_last:
            type_byte |= 0x01  # OR
        comp_float = struct.pack('<f', float(min_stage))
        comp_raw = struct.unpack('<I', comp_float)[0]
        ctda = struct.pack('<B3xIHHIIIII',
                           type_byte, comp_raw, _FUNC_GET_STAGE, 0,
                           quest_fid, 0, 0, 0, 0xFFFFFFFF)
        result += pack_subrecord('CTDA', ctda)
    return result


# ---------------------------------------------------------------------------
# Pre-scan helpers
# ---------------------------------------------------------------------------

def collect_tclt_target_fids(by_type: dict) -> set:
    """Collect DIAL FormIDs that are TCLT choice targets.

    These topics are only reachable as child options from a parent INFO's
    choice list. They should NOT get their own DLBR (dialog branch) because
    they are not root-level dialog options.
    """
    offset = get_formid_index_offset()
    targets = set()
    for rec in by_type.get('INFO', []):
        cc = get_int(rec, 'ChoiceCount')
        for i in range(cc):
            cfid = get_formid(rec, f'Choice[{i}]')
            if cfid:
                targets.add(cfid)
        # Also check the legacy single-TCLT field
        cfid = get_formid(rec, 'TCLT.Choice')
        if cfid:
            targets.add(cfid)
    return targets


def build_npc_to_vtyp_map(by_type: dict, num_new_masters: int) -> dict:
    """Build NPC FormID → VTYP FormID mapping."""
    from .skyrim_overrides import TES4_RACE_FID_TO_EDID, VOICE_TYPE_MAP
    npc_to_vtyp = {}
    offset = num_new_masters
    for sig in ('NPC_', 'CREA'):
        for rec in by_type.get(sig, []):
            raw_fid = int(rec.get('FormID', '0'), 16)
            if (raw_fid >> 24) == 0x00 and (raw_fid & 0x00FFFFFF) >= 0x100:
                remapped_fid = (raw_fid & 0x00FFFFFF) | (offset << 24)
            else:
                remapped_fid = raw_fid
            tes4_race_fid = get_formid(rec, 'RNAM.Race')
            race_edid = TES4_RACE_FID_TO_EDID.get(
                tes4_race_fid & 0x00FFFFFF, 'Imperial')
            tes4_flags = get_int(rec, 'ACBS.Flags')
            gender = 'Female' if (tes4_flags & 1) else 'Male'
            vtyp = VOICE_TYPE_MAP.get((race_edid, gender))
            if not vtyp:
                vtyp = VOICE_TYPE_MAP.get(('Imperial', gender), 0)
            if vtyp:
                npc_to_vtyp[remapped_fid] = vtyp
    return npc_to_vtyp


# ---------------------------------------------------------------------------
# Main dialog group builder
# ---------------------------------------------------------------------------

def build_dialog_groups(by_type: dict, writer, npc_to_vtyp: dict,
                        fid_to_edid: dict = None, xref=None,
                        well_known_props: dict = None) -> set:
    """Build DIAL/INFO/DLBR/DLVW group hierarchy.

    Architecture: ONE universal dialog quest (TES4Dialogue) owns ALL topics.
    This matches Oblivion's model where all topics are in one pool and
    conditions on individual INFOs control what actually appears.
    The SEQ file only needs 1 entry (the universal dialog quest).

    Returns: set containing the universal quest FormID (for SEQ).
    """
    from .writer import pack_group

    dials = by_type.get('DIAL', [])
    infos = by_type.get('INFO', [])

    if not dials:
        return set()

    # --- Create universal dialog quest (SGE + StartsEnabled) ---
    dialog_quest_fid = writer.alloc_formid()
    q_subs = pack_string_subrecord('EDID', 'TES4Dialogue')
    q_subs += pack_string_subrecord('FULL', 'TES4 Dialogue')
    q_subs += pack_subrecord('DNAM', struct.pack('<HBBII', 0x0011, 0, 0, 0, 0))
    q_subs += pack_subrecord('NEXT', b'')
    q_subs += pack_uint32_subrecord('ANAM', 0)
    writer.add_record('QUST', pack_record('QUST', dialog_quest_fid, 0, q_subs))

    # --- Build SGE quest set for quest-running gating ---
    # In Oblivion, dialog only shows when its QSTI quest is running.
    # Since we put everything on a universal always-running quest, we must
    # explicitly add GetQuestRunning(orig_quest) conditions to INFOs whose
    # original quest is NOT StartGameEnabled (SGE). This restores the
    # implicit gating that Oblivion's QSTI system provided.
    sge_quest_fids: set[int] = set()
    for qust_rec in by_type.get('QUST', []):
        qflags = get_int(qust_rec, 'DATA.Flags')
        if qflags & 0x01:  # StartGameEnabled
            qfid = get_formid(qust_rec, 'FormID')
            if qfid:
                sge_quest_fids.add(qfid)
    print(f"  SGE quests: {len(sge_quest_fids)} (non-SGE will get "
          f"GetQuestRunning gating)")

    # --- Pre-collect skipped DIAL FormIDs and strip invalid TCLT refs ---
    skipped_dial_fids = set()
    for d in dials:
        if should_skip_dial(d):
            fid = get_formid(d, 'FormID')
            if fid:
                skipped_dial_fids.add(fid)

    if skipped_dial_fids:
        tclt_stripped = 0
        for rec in infos:
            choice_count = get_int(rec, 'ChoiceCount')
            if choice_count > 0:
                kept_raw = []
                for i in range(choice_count):
                    raw_val = rec.get(f'Choice[{i}]', '0')
                    cfid = get_formid(rec, f'Choice[{i}]')
                    if cfid in skipped_dial_fids:
                        tclt_stripped += 1
                    else:
                        kept_raw.append(raw_val)
                rec['ChoiceCount'] = str(len(kept_raw))
                for i in range(choice_count):
                    rec.pop(f'Choice[{i}]', None)
                for i, raw in enumerate(kept_raw):
                    rec[f'Choice[{i}]'] = raw
            else:
                cfid = get_formid(rec, 'TCLT.Choice')
                if cfid and cfid in skipped_dial_fids:
                    del rec['TCLT.Choice']
                    tclt_stripped += 1
        if tclt_stripped:
            print(f"  Stripped {tclt_stripped} TCLT references to "
                  f"{len(skipped_dial_fids)} skipped DIALs")

    # Group INFOs by parent DIAL
    info_by_dial = defaultdict(list)
    for rec in infos:
        dial_fid = get_formid(rec, 'ParentDIAL')
        info_by_dial[dial_fid].append(rec)

    offset = get_formid_index_offset()

    print(f"  Building DIAL hierarchy ({len(dials)} topics, {len(infos)} infos, "
          f"{len(npc_to_vtyp)} NPC->VTYP mappings)...")

    # TCLT targets — topics only reachable via parent INFO choice links
    tclt_target_fids = collect_tclt_target_fids(by_type)

    # Stage gating
    topic_stage_gates = build_topic_stage_gating(by_type, offset)

    # Pre-build quest-level NPC map for fallback NPC restriction.
    # Uses ORIGINAL TES4 quest ownership for sibling grouping.
    quest_npc_fids: dict[int, set] = defaultdict(set)
    for dial_rec in dials:
        if should_skip_dial(dial_rec):
            continue
        qfid = get_formid(dial_rec, 'Quest[0]')
        if not qfid:
            continue
        dfid = get_formid(dial_rec, 'FormID')
        child = info_by_dial.get(dfid, [])
        if child:
            npcs = collect_topic_npc_fids(child, offset)
            if npcs:
                quest_npc_fids[qfid] |= npcs

    # Counters
    dial_converted = info_converted = dlbr_created = dlvw_created = 0
    skipped_count = total_npc_injected = total_stage_gated = 0
    total_quest_gated = 0
    tclt_suppressed = 0
    all_dial_content = b''
    all_dlbr_records = b''

    all_branch_fids = []
    all_topic_fids = []

    for dial_rec in dials:
        dial_fid = get_formid(dial_rec, 'FormID')
        dial_edid = get_str(dial_rec, 'EditorID', '')
        orig_quest_fid = get_formid(dial_rec, 'Quest[0]')
        dtype = get_int(dial_rec, 'DATA.Type')

        if should_skip_dial(dial_rec):
            skipped_count += 1
            continue

        try:
            bark = is_bark_topic(dial_edid, dtype=dtype)
            dlbr_fid = 0
            child_infos = info_by_dial.get(dial_fid, [])

            # Non-bark topics get DLBR.
            # TCLT targets also get DLBR (vanilla Skyrim requires BNAM
            # on TCLT target DIALs) but with top_level=False so they
            # don't appear in the top-level topic menu.
            if not bark and child_infos:
                is_tclt_target = dial_fid in tclt_target_fids
                if is_tclt_target:
                    tclt_suppressed += 1
                dlbr_fid = writer.alloc_formid()
                dlbr_edid = (f'TES4_{dial_edid}_Branch' if dial_edid
                             else f'TES4_DLBR_{dlbr_fid:08X}')
                dlbr_bytes = make_dlbr(dlbr_fid, dlbr_edid,
                                       dialog_quest_fid,
                                       dial_fid,
                                       top_level=not is_tclt_target)
                all_dlbr_records += dlbr_bytes
                dlbr_created += 1
                all_branch_fids.append(dlbr_fid)

            all_topic_fids.append(dial_fid)

            # --- Voice type / NPC restriction injection ---
            topic_vtyps = set()
            topic_npc_fids_set = set()

            # Collect voice types from NPC-specific GetIsID in sibling INFOs
            for info_rec in child_infos:
                cc = get_int(info_rec, 'ConditionCount')
                for ci in range(cc):
                    raw_hex = info_rec.get(f'Condition[{ci}].Raw', '')
                    if not raw_hex or len(raw_hex) < 32:
                        continue
                    try:
                        raw = bytes.fromhex(raw_hex)
                        func_idx = struct.unpack_from('<H', raw, 8)[0]
                        if func_idx == _FUNC_GET_IS_ID:
                            param1 = struct.unpack_from('<I', raw, 12)[0]
                            remapped = param1
                            if offset and (param1 >> 24) == 0x00 \
                                    and (param1 & 0x00FFFFFF) >= 0x100:
                                remapped = ((param1 & 0x00FFFFFF)
                                            | (offset << 24))
                            vtyp = (npc_to_vtyp.get(remapped, 0)
                                    or npc_to_vtyp.get(param1, 0))
                            if vtyp:
                                topic_vtyps.add(vtyp)
                    except (ValueError, struct.error):
                        continue

            if not bark:
                topic_npc_fids_set = collect_topic_npc_fids(child_infos,
                                                            offset)
                if not topic_npc_fids_set and orig_quest_fid:
                    topic_npc_fids_set = quest_npc_fids.get(
                        orig_quest_fid, set())

            # --- Convert child INFOs ---
            topic_children = b''
            child_info_count = 0
            npc_injected = stage_gated = quest_gated = 0
            stage_gate_ctdas = build_stage_gate_ctdas(
                topic_stage_gates.get(dial_fid, []))

            # Build quest-running gate if original quest is NOT SGE.
            # This restores Oblivion's implicit QSTI gating.
            quest_running_ctda = b''
            if orig_quest_fid and orig_quest_fid not in sge_quest_fids:
                # Remap the quest FormID to output space
                remapped_qfid = orig_quest_fid
                if offset and (orig_quest_fid >> 24) == 0x00 \
                        and (orig_quest_fid & 0x00FFFFFF) >= 0x100:
                    remapped_qfid = ((orig_quest_fid & 0x00FFFFFF)
                                     | (offset << 24))
                quest_running_ctda = pack_subrecord(
                    'CTDA', build_quest_running_ctda(remapped_qfid))

            for info_rec in child_infos:
                try:
                    voice_ctdas = build_voice_type_ctdas_for_info(
                        info_rec, npc_to_vtyp, topic_vtyps=topic_vtyps)
                    npc_ctdas = b''
                    if not bark and topic_npc_fids_set \
                            and not info_has_positive_getisid(info_rec):
                        npc_ctdas = build_topic_npc_ctdas(
                            topic_npc_fids_set)
                        npc_injected += 1
                    injected_ctdas = voice_ctdas + npc_ctdas

                    if stage_gate_ctdas:
                        injected_ctdas = stage_gate_ctdas + injected_ctdas
                        stage_gated += 1

                    # Quest-running gate goes FIRST (outermost AND)
                    if quest_running_ctda:
                        injected_ctdas = quest_running_ctda + injected_ctdas
                        quest_gated += 1

                    info_bytes = convert_INFO(
                        info_rec, voice_type_ctdas=injected_ctdas,
                        is_bark=bark, fid_to_edid=fid_to_edid,
                        well_known_props=well_known_props, xref=xref)
                    topic_children += info_bytes
                    child_info_count += 1
                    info_converted += 1
                except Exception as e:
                    print(f"  ERROR converting INFO: {e}")

            total_npc_injected += npc_injected
            total_stage_gated += stage_gated
            total_quest_gated += quest_gated

            # Convert DIAL — ALL topics owned by universal quest
            dial_bytes = convert_DIAL(dial_rec, info_count=child_info_count,
                                      dlbr_fid=dlbr_fid,
                                      quest_fid_override=dialog_quest_fid)
            dial_group_content = dial_bytes
            if topic_children:
                dial_group_content += pack_group(
                    7, struct.pack('<I', dial_fid), topic_children)
            all_dial_content += dial_group_content
            dial_converted += 1

        except Exception as e:
            print(f"  ERROR building DIAL group for {dial_edid or '?'}: {e}")

    # Create ONE DLVW for the universal dialog quest
    all_dlvw_records = b''
    if all_branch_fids:
        dlvw_fid = writer.alloc_formid()
        dlvw_bytes = make_dlvw(dlvw_fid, 'TES4_DialogueView',
                               dialog_quest_fid,
                               all_branch_fids, all_topic_fids)
        all_dlvw_records = dlvw_bytes
        dlvw_created = 1

    # --- Create fallback generic greetings for all voice types ---
    # Ensures every NPC with a voice type has at least one greeting.
    # Uses low priority (10.0) so real converted greetings take precedence.
    all_vtyp_fids = sorted(set(npc_to_vtyp.values()))
    if all_vtyp_fids:
        fallback_dial_fid = writer.alloc_formid()
        fallback_infos = b''
        fallback_count = 0
        for vtyp_fid in all_vtyp_fids:
            info_fid = writer.alloc_formid()
            f_subs = b''
            f_subs += pack_subrecord('ENAM', struct.pack('<HH', 0, 0))
            f_subs += pack_subrecord('CNAM', struct.pack('<B', 0))
            # Minimal response: "Hello."
            f_subs += pack_subrecord('TRDT', struct.pack(
                '<IiI B3x I B3x', 0, 0, 0, 1, 0, 1))
            f_subs += pack_string_subrecord('NAM1', 'Hello.')
            f_subs += pack_string_subrecord('NAM2', '')
            f_subs += pack_string_subrecord('NAM3', '')
            # Single GetIsVoiceType condition (AND)
            f_subs += pack_subrecord('CTDA', build_voice_type_ctda(vtyp_fid))
            fallback_infos += pack_record('INFO', info_fid, 0, f_subs)
            fallback_count += 1

        # Build DIAL record for fallback topic
        fb_subs = pack_string_subrecord('EDID', 'TES4FallbackHello')
        fb_subs += pack_string_subrecord('FULL', 'Fallback Hello')
        fb_subs += pack_subrecord('PNAM', struct.pack('<f', 10.0))
        fb_subs += pack_formid_subrecord('QNAM', dialog_quest_fid)
        fb_subs += pack_subrecord('DATA', struct.pack('<BBH', 0, 7, 79))
        fb_subs += pack_subrecord('SNAM', b'HELO')
        fb_subs += pack_uint32_subrecord('TIFC', fallback_count)
        fb_dial = pack_record('DIAL', fallback_dial_fid, 0, fb_subs)

        fb_child_group = pack_group(
            7, struct.pack('<I', fallback_dial_fid), fallback_infos)
        all_dial_content += fb_dial + fb_child_group
        info_converted += fallback_count
        dial_converted += 1
        print(f"    Fallback greetings: {fallback_count} INFOs for "
              f"{len(all_vtyp_fids)} voice types")

    # Write all groups
    if all_dial_content:
        writer.add_raw_group('DIAL', all_dial_content)
    if all_dlbr_records:
        writer.add_raw_group('DLBR', all_dlbr_records)
    if all_dlvw_records:
        writer.add_raw_group('DLVW', all_dlvw_records)

    print(f"    Topics: {dial_converted}, infos: {info_converted}, "
          f"branches: {dlbr_created}, views: {dlvw_created}, "
          f"skipped: {skipped_count}, "
          f"GetIsID injected: {total_npc_injected}, "
          f"stage-gated: {total_stage_gated}, "
          f"quest-gated: {total_quest_gated}, "
          f"TCLT-suppressed: {tclt_suppressed}")

    return {dialog_quest_fid}
