"""Oblivion (TES4) -> Skyrim (TES5) dialogue / quest conversion.

Rewritten per the `oblivion-to-skyrim-dialog` skill. The guiding standard is
BEHAVIORAL FIDELITY: a converted plugin should make NPCs say the same lines, to
the same people, at the same times, advancing the same quests — not merely
contain records of the right signatures.

Architecture (the key change from the old universal-quest design):

  * Quest ownership follows the two engines' actual gating models:
      - Oblivion evaluates each INFO only while that INFO's own QSTI quest is
        running (a topic's INFO list is a union across quests).
      - Skyrim's DIAL is owned by exactly ONE quest (QNAM) and its INFOs are
        only evaluated while that quest runs; vanilla models shared subjects
        as one DIAL per quest (Skyrim.esm has ~288 separate HELO topics).
    So: a SINGLE-quest topic is owned by its original quest (remapped) —
    native gating, and the runtime voice path (built from the owning Quest
    EditorID + Topic EditorID + InfoFormID) keeps matching the extracted
    audio. A SHARED (multi-quest) or quest-less topic cannot be faithfully
    owned by any one quest, so it is owned by the always-running synthetic
    quest `TES4DialogueGeneric`, and each of its INFOs gets a
    GetQuestRunning(own QSTI quest) gate reproducing Oblivion's per-INFO
    visibility. (Start-Game-Enabled quests are exempt from the injected gate —
    they run from a new game via the SEQ file, so the gate is redundant.)

  * Skyrim needs structure Oblivion has no source for, which we synthesize:
      - VTYP per speaking NPC (kept as custom TES4* voice types so the converted
        audio folders match) and GetIsVoiceType conditions per INFO.
      - DLBR branches (top-level vs. linked) from topic type + the TCLT graph.
      - One DLVW per owning quest (CK metadata only).

  * AddTopic visibility (no Skyrim equivalent) is re-expressed as conditions:
    GetStage gates derived from `AddTopic`/`SetStage` script analysis, plus
    GetIsID identity gates so conversation topics don't leak to every NPC.

  * Result scripts -> Papyrus VMAD fragments (via script_convert). This is the
    one transform with no data-only path; the fragment is built from the TES4
    SCTX source.

CTDA condition translation lives in dialog_conditions.py.
"""

import re
import struct
from collections import defaultdict

from .text_reader import get_formid_index_offset
from .writer import pack_group
from .record_types.common import (
    get_formid,
    get_int,
    get_str,
    pack_formid_subrecord,
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
    pack_uint8_subrecord,
    pack_uint32_subrecord,
)
from .dialog_conditions import (
    FUNC_GET_GLOBAL_VALUE,
    FUNC_GET_IS_ID,
    FUNC_GET_IS_VOICE_TYPE,
    FUNC_GET_QUEST_RUNNING,
    build_ctda,
    build_or_chain,
    convert_ctda_list,
    has_positive_getisid,
    read_getisid_fids,
)

_PLAYER_FORMID = 0x14

# TES4 DIAL.Type enum
DIAL_TYPE_TOPIC = 0
DIAL_TYPE_CONVERSATION = 1
DIAL_TYPE_COMBAT = 2
DIAL_TYPE_PERSUASION = 3
DIAL_TYPE_DETECTION = 4
DIAL_TYPE_SERVICE = 5
DIAL_TYPE_MISC = 6


# ===========================================================================
# QUST conversion
# ===========================================================================

def _collect_scro_properties(rec: dict, fid_to_edid: dict, prefix: str = '') -> dict:
    """Extract SCRO FormID refs from a record (optionally a stage-log prefix)
    into VMAD property name -> remapped FormID."""
    from script_convert.constants import _safe_property_name
    props = {}
    seen = set()
    i = 0
    while True:
        key = f'{prefix}SCRO[{i}]'
        fid_str = rec.get(key)
        if fid_str is None:
            break
        i += 1
        try:
            raw_fid = int(fid_str, 16)
        except (ValueError, TypeError):
            continue
        if raw_fid in (0, _PLAYER_FORMID):
            continue
        edid = fid_to_edid.get(raw_fid)
        if not edid:
            continue
        safe = _safe_property_name(edid)
        if safe.lower() in seen:
            continue
        seen.add(safe.lower())
        remapped = get_formid(rec, key)
        if remapped:
            props[safe] = remapped
    return props


def _collect_all_scro_properties(rec: dict, fid_to_edid: dict) -> dict:
    """Collect SCRO properties from record level and every stage log entry."""
    props = _collect_scro_properties(rec, fid_to_edid)
    stage_count = get_int(rec, 'StageCount')
    for i in range(stage_count):
        log_count = get_int(rec, f'Stage[{i}].LogCount')
        for j in range(log_count):
            for name, fid in _collect_scro_properties(
                    rec, fid_to_edid, prefix=f'Stage[{i}].Log[{j}].').items():
                props.setdefault(name, fid)
    return props


def _quest_stage_fragments(rec: dict) -> list:
    """List (stage_index, log_index) tuples that need a Papyrus fragment.

    A fragment is emitted for any stage log entry that has journal text or a
    result script — the PSC generator emits Fragment_Stage_NNNN_Item_N for each,
    and the VMAD fragment list must match exactly or the function never fires.
    """
    frags = []
    stage_count = get_int(rec, 'StageCount')
    for i in range(stage_count):
        stage_idx = get_int(rec, f'Stage[{i}].Index')
        log_count = get_int(rec, f'Stage[{i}].LogCount')
        if log_count > 0:
            for j in range(log_count):
                if (get_str(rec, f'Stage[{i}].Log[{j}].Text') or
                        get_str(rec, f'Stage[{i}].Log[{j}].ResultScript')):
                    frags.append((stage_idx, j))
        elif (get_str(rec, f'Stage[{i}].Text') or
              get_str(rec, f'Stage[{i}].ResultScript')):
            frags.append((stage_idx, 0))
    return frags


def _quest_has_journal(rec: dict) -> bool:
    """True if any stage carries journal log text (the quest is a real,
    player-visible quest rather than a dialogue/control quest)."""
    stage_count = get_int(rec, 'StageCount')
    for i in range(stage_count):
        log_count = get_int(rec, f'Stage[{i}].LogCount')
        if log_count > 0:
            for j in range(log_count):
                if get_str(rec, f'Stage[{i}].Log[{j}].Text'):
                    return True
        elif get_str(rec, f'Stage[{i}].LogEntry'):
            return True
    return False


def _quest_dnam(rec: dict) -> bytes:
    """DNAM (12 bytes): Flags(U16) Priority(U8) FormVer(U8=0) Unknown(4) Type(U32).

    TES4 flags -> TES5: keep StartGameEnabled (0x01) and AllowRepeatedStages
    (0x08). A quest that was Start-Game-Enabled in Oblivion also gets
    StartsEnabled (0x10) so it actually runs from a new game in Skyrim — which
    is what makes its dialogue reachable. HasDialogueData (0x8000) is never set
    (Skyrim.esm never uses it and it blocks dialogue processing).

    Type: quests with journal stages get 8 (Side Quest) so they appear in the
    journal. Type 0 (None) is Skyrim's journal-INVISIBLE control-quest type —
    a Type-0 quest is never listed, so it can't be tracked and its objective
    targets never produce compass/map markers (vanilla: only 16 of ~396
    objective-bearing quests are Type 0).
    """
    tes4_flags = get_int(rec, 'DATA.Flags')
    priority = get_int(rec, 'DATA.Priority')
    flags = tes4_flags & 0x09          # StartGameEnabled | AllowRepeatedStages
    if flags & 0x01:
        flags |= 0x10                  # StartsEnabled
    qtype = 8 if _quest_has_journal(rec) else 0
    return struct.pack('<HBBII', flags, priority, 0, 0, qtype)


def convert_QUST(rec: dict, fid_to_edid: dict = None,
                 well_known_props: dict = None,
                 unlock_plan: dict = None,
                 unlock_globals: dict = None) -> bytes:
    """QUST — Quest conversion (original quest, not the synthetic dialogue one).

    Order: EDID [VMAD] FULL DNAM NEXT [stages] [objectives] ANAM [aliases].
    unlock_plan/unlock_globals bind the AddTopic unlock GLOB properties for
    stage result scripts that reveal topics (the generated QF fragment sets
    them via SetValue).
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    # VMAD — quest stage script fragments (Papyrus), only if there are stages
    # with text/script. The fragment list must match the generated PSC exactly.
    stage_frags = _quest_stage_fragments(rec)
    if stage_frags and edid:
        from script_convert.pipeline import build_vmad_quest_fragments
        prop_vals = (_collect_all_scro_properties(rec, fid_to_edid)
                     if fid_to_edid else {})
        if well_known_props:
            prop_vals.update(well_known_props)
        if unlock_plan and unlock_globals:
            ql = edid.lower()
            for (qkey, _stage), gnames in unlock_plan['stage_reveals'].items():
                if qkey == ql:
                    for n in gnames:
                        if n in unlock_globals:
                            prop_vals[n] = unlock_globals[n]
        subs += pack_subrecord('VMAD', build_vmad_quest_fragments(
            edid, stage_frags, property_values=prop_vals or None))

    full = get_str(rec, 'FULL')
    if full:
        subs += pack_string_subrecord('FULL', full)

    subs += pack_subrecord('DNAM', _quest_dnam(rec))
    subs += pack_subrecord('NEXT', b'')

    # Stages
    stage_count = get_int(rec, 'StageCount')
    for i in range(stage_count):
        stage_idx = get_int(rec, f'Stage[{i}].Index')
        subs += pack_subrecord('INDX', struct.pack('<HBB', stage_idx, 0, 0))
        log_count = get_int(rec, f'Stage[{i}].LogCount')
        if log_count > 0:
            for j in range(log_count):
                log_flags = get_int(rec, f'Stage[{i}].Log[{j}].Flags')
                subs += pack_uint8_subrecord('QSDT', log_flags & 0x03)
                txt = get_str(rec, f'Stage[{i}].Log[{j}].Text')
                if txt:
                    subs += pack_string_subrecord('CNAM', txt)
        else:
            complete = get_int(rec, f'Stage[{i}].CompleteQuest')
            subs += pack_uint8_subrecord('QSDT', 0x01 if complete else 0)
            txt = get_str(rec, f'Stage[{i}].LogEntry')
            if txt:
                subs += pack_string_subrecord('CNAM', txt)

    # --- Quest targets -> reference aliases + per-objective targets ---
    # Oblivion QSTA: quest-level (REFR FormID + flags + conditions, usually
    # GetStage bounds that gate WHEN the compass marker shows). Skyrim QSTA:
    # per-OBJECTIVE (alias index + flags + conditions); markers appear while
    # a displayed objective has a passing target. Mapping: one forced-ref
    # alias per unique target ref, and every objective carries every target
    # WITH its converted conditions — the GetStage conditions then gate the
    # marker per stage at runtime exactly as Oblivion did. The stage
    # fragments (script_convert) call SetObjectiveDisplayed(stage).
    targets = []          # (alias_id, tes4_flags_low_byte, [ctda bytes])
    alias_by_fid = {}
    t = 0
    while f'Target[{t}].FormID' in rec:
        tfid = get_formid(rec, f'Target[{t}].FormID')
        if tfid:
            alias_id = alias_by_fid.setdefault(tfid, len(alias_by_fid))
            tflags = get_int(rec, f'Target[{t}].Flags') & 0x01
            ctdas = convert_ctda_list(rec, prefix=f'Target[{t}].')
            targets.append((alias_id, tflags, ctdas))
        t += 1

    target_subs = b''
    for alias_id, tflags, ctdas in targets:
        target_subs += pack_subrecord('QSTA', struct.pack('<iB3x',
                                                          alias_id, tflags))
        for ctda in ctdas:
            target_subs += pack_subrecord('CTDA', ctda)

    # Objectives — one per stage with journal text (objective index = stage
    # index, which is what the generated stage fragments display), each
    # carrying all quest targets.
    seen_stages = set()
    for i in range(stage_count):
        stage_idx = get_int(rec, f'Stage[{i}].Index')
        if stage_idx in seen_stages:
            continue
        log_count = get_int(rec, f'Stage[{i}].LogCount')
        texts = ([get_str(rec, f'Stage[{i}].Log[{j}].Text')
                  for j in range(log_count)] if log_count > 0
                 else [get_str(rec, f'Stage[{i}].LogEntry')])
        txt = next((x for x in texts if x), None)
        if not txt:
            continue
        seen_stages.add(stage_idx)
        subs += pack_subrecord('QOBJ', struct.pack('<H', stage_idx))
        subs += pack_uint32_subrecord('FNAM', 0)
        subs += pack_string_subrecord('NNAM', txt)
        subs += target_subs

    subs += pack_uint32_subrecord('ANAM', len(alias_by_fid))  # Next Alias ID

    # Reference aliases (forced ref). Flags: Optional (0x0002 — a fill
    # failure must not block quest start, or dialogue dies with it) +
    # Allow Reuse (0x0008) + Allow Dead (0x0010) + Allow Disabled (0x0080) +
    # Allow Destroyed (0x1000).
    for tfid, alias_id in sorted(alias_by_fid.items(), key=lambda kv: kv[1]):
        subs += pack_uint32_subrecord('ALST', alias_id)
        subs += pack_string_subrecord('ALID', f'TES4Target{alias_id:02d}')
        subs += pack_uint32_subrecord('FNAM', 0x0000109A)
        subs += pack_formid_subrecord('ALFR', tfid)
        subs += pack_subrecord('ALED', b'')

    return pack_record('QUST', get_formid(rec, 'FormID'),
                       get_int(rec, 'RecordFlags'), subs)


# ===========================================================================
# DIAL topic classification (Type -> Category/Subtype/SNAM, bark detection)
# ===========================================================================

# Known reserved EditorID -> (TES5 subtype enum, SNAM 4-char code, category).
# Category enum: 0 Topic, 3 Combat, 5 Detection, 6 Service, 7 Misc.
# Reserved Oblivion EditorID -> (Subtype, SNAM, Category).
# Subtype/Category/SNAM values are VERIFIED against real Skyrim.esm DATA bytes
# (DATA = flags U8 + category U8 + subtype U16). The xEdit display enum numbers
# differ from the on-disk subtype values, so these come from the actual master:
#   Hello=73 GoodBye=72 Idle=88 (all category 7 Misc);
#   Attack=20 PowerAttack=21 Bash=22 Hit=23 Flee=24 Bleedout=25 Death=27
#   Block=29 Taunt=30 Steal=32 Trespass=43 (all category 3 Combat);
#   NoticeAlert=51 NormalToCombat(?) ... detection codes (category 5 Detection).
_EDID_SUBTYPE = {
    'GREETING':       (73, b'HELO', 7),
    'HELLO':          (73, b'HELO', 7),
    'GOODBYE':        (72, b'GBYE', 7),
    'IDLE':           (88, b'IDLE', 7),
    'Idle':           (88, b'IDLE', 7),
    'IdleChatter':    (88, b'IDLE', 7),
    'Attack':         (20, b'ATCK', 3),
    'PowerAttack':    (21, b'POAT', 3),
    'Hit':            (23, b'HIT_', 3),
    'Block':          (29, b'BLOC', 3),
    'Bash':           (22, b'BASH', 3),
    'Flee':           (24, b'FLEE', 3),
    'Bleedout':       (25, b'BLED', 3),
    'Yield':          (24, b'FLEE', 3),   # nearest combat de-escalation bark
    'Steal':          (32, b'STEA', 3),
    'Assault':        (36, b'ASSA', 3),
    'Murder':         (37, b'MURD', 3),
    'Trespass':       (43, b'TRES', 3),
    'NoticeCorpse':   (70, b'NOTI', 7),
    'Corpse':         (70, b'NOTI', 7),
    'TimeToGo':       (71, b'TITG', 7),
    'ObserveCombat':  (69, b'OBCO', 7),
    # Detection (category 5)
    'NoticedSomething': (51, b'NOTA', 5),
    'Noticed':        (51, b'NOTA', 5),
    'Seen':           (51, b'NOTA', 5),
    'Lost':           (57, b'LOTN', 5),
    'Unseen':         (57, b'LOTN', 5),
    # Oblivion NPC-to-NPC conversation system topics — never player-selectable
    'AnswerStatus':   (88, b'IDLE', 7),
    'TRANSITION':     (88, b'IDLE', 7),
}

# Subtypes that are barks (situational, not player-selectable, no DLBR/no BNAM).
_BARK_SUBTYPES = frozenset(
    sub for (sub, _snam, _cat) in _EDID_SUBTYPE.values() if sub != 0
)

# DIAL topics to skip entirely (mechanics with no Skyrim equivalent, or test data)
_SKIP_TYPES = frozenset({DIAL_TYPE_PERSUASION, DIAL_TYPE_SERVICE})
_SKIP_EDIDS = frozenset({
    'CreatureResponses', 'SECreatureResponses', 'TamrielGateResponses', 'ANY',
})


def should_skip_dial(rec: dict) -> bool:
    dtype = get_int(rec, 'DATA.Type')
    if dtype in _SKIP_TYPES:
        return True
    edid = get_str(rec, 'EditorID', '')
    if edid in _SKIP_EDIDS:
        return True
    if edid.startswith('Test') or edid.startswith('MarkNTest'):
        return True
    return False


def classify_topic(edid: str, dtype: int):
    """Return (category, subtype, snam_code, is_bark) for a DIAL topic.

    Maps the coarse TES4 Type enum + reserved EditorID onto Skyrim's finer
    Category/Subtype/SNAM, per the skill's dial-info mapping.
    """
    info = _EDID_SUBTYPE.get(edid or '')
    if info:
        subtype, snam, category = info
        return category, subtype, snam, (subtype in _BARK_SUBTYPES)

    # Fall back on the TES4 Type enum (Skyrim subtype/category from real data).
    if dtype == DIAL_TYPE_COMBAT:
        return 3, 20, b'ATCK', True       # Attack (category 3 Combat)
    if dtype == DIAL_TYPE_DETECTION:
        return 5, 51, b'NOTA', True       # Notice Alert (category 5 Detection)
    if dtype == DIAL_TYPE_MISC:
        return 7, 88, b'IDLE', True       # Idle (category 7 Misc)
    # Type 0 Topic, 1 Conversation, 3 Persuasion (if not skipped) -> Custom topic
    return 0, 0, b'CUST', False


def convert_DIAL(rec: dict, *, info_count: int, dlbr_fid: int,
                 quest_fid: int, category: int, subtype: int,
                 snam: bytes, priority: float = 50.0) -> bytes:
    """DIAL — Dialog Topic. Order: EDID FULL PNAM [BNAM] QNAM DATA SNAM TIFC.

    quest_fid is the REMAPPED owning quest (original QSTI quest, or the synthetic
    generic dialogue quest for orphan/bark topics).
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    full = get_str(rec, 'FULL')
    if full:
        subs += pack_string_subrecord('FULL', full)
    subs += pack_subrecord('PNAM', struct.pack('<f', priority))
    if dlbr_fid:
        subs += pack_formid_subrecord('BNAM', dlbr_fid)
    if quest_fid:
        subs += pack_formid_subrecord('QNAM', quest_fid)
    # DATA = TopicFlags(U8) + Category(U8) + Subtype(U16), per xEdit
    # wbDefinitionsTES5 and verified against real Skyrim.esm (Hello =
    # 00 07 49 00: category 7 Misc, subtype 0x49=73). Writing category into
    # the U16 puts the subtype byte where the engine reads category, and an
    # out-of-range category crashes the engine at startup while it indexes
    # its per-category topic dispatch tables.
    subs += pack_subrecord('DATA', struct.pack('<BBH', 0, category & 0xFF, subtype))
    subs += pack_subrecord('SNAM', snam)
    subs += pack_uint32_subrecord('TIFC', info_count)
    return pack_record('DIAL', get_formid(rec, 'FormID'),
                       get_int(rec, 'RecordFlags'), subs)


# ===========================================================================
# INFO conversion
# ===========================================================================

# ENAM flag bits that are bit-compatible between TES4 DATA.Flags and TES5 ENAM:
#   0x01 Goodbye, 0x02 Random, 0x04 Say once, 0x10 Info Refusal, 0x20 Random end
# (0x08 Run Immediately and 0x40 Run for Rumors have no faithful TES5 meaning.)
_ENAM_COMPATIBLE_MASK = 0x37


def _build_info_script_properties(result_script: str, xref) -> dict:
    """Build VMAD property bindings for an INFO result script via ScriptConverter."""
    if not xref:
        return {}
    from script_convert.converter import ScriptConverter
    offset = get_formid_index_offset()
    try:
        conv = ScriptConverter(xref)
        conv.convert_fragment(result_script, 'TopicInfo')
    except Exception:
        return {}
    props = {}
    for prop_edid in conv._property_refs:
        low = prop_edid.lower()
        if low in ('player', 'playerref'):
            props[prop_edid] = _PLAYER_FORMID
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
        props[prop_edid] = raw_fid
    return props


def convert_INFO(rec: dict, *, injected_ctdas: bytes = b'',
                 fid_to_edid: dict = None, well_known_props: dict = None,
                 xref=None, reveal_props: dict = None) -> bytes:
    """INFO — Dialog response.

    Order: EDID [VMAD] ENAM CNAM [TCLT...] [TRDT NAM1 NAM2 NAM3]* CTDAs.
    injected_ctdas are the Skyrim-required gates (voice type / identity /
    unlock / quest-running), already packed and placed BEFORE the translated
    TES4 conditions so their OR chains stay isolated. reveal_props
    ({global_name: GLOB formid}) marks this INFO as an AddTopic revealer — it
    gets an OnEnd fragment (generated by script_convert) that sets those
    unlock globals, so a VMAD is emitted even without a result script.
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    # VMAD — result script -> Papyrus fragment (skip comment-only scripts),
    # and/or AddTopic unlock-global assignments.
    info_fid = get_str(rec, 'FormID') or ''
    result_script = get_str(rec, 'ResultScript')
    code_lines = []
    if result_script:
        code_lines = [ln for ln in result_script.strip().splitlines()
                      if ln.strip() and not ln.strip().startswith(';')]
    if info_fid and (code_lines or reveal_props):
        from script_convert.pipeline import build_vmad_info_fragment
        prop_vals = (_build_info_script_properties(result_script, xref)
                     if code_lines else {})
        if code_lines and well_known_props:
            prop_vals.update(well_known_props)
        if reveal_props:
            prop_vals.update(reveal_props)
        subs += pack_subrecord('VMAD', build_vmad_info_fragment(
            info_fid, property_values=prop_vals or None))

    # ENAM (Flags U16 + Reset Hours U16)
    tes4_flags = get_int(rec, 'DATA.Flags')
    subs += pack_subrecord('ENAM', struct.pack('<HH',
                                               tes4_flags & _ENAM_COMPATIBLE_MASK, 0))

    # CNAM — favor level (None)
    subs += pack_subrecord('CNAM', struct.pack('<B', 0))

    # TCLT — choices (follow-up topic links)
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

    # Responses (TRDT 12B -> 24B; text + emotion preserved)
    rc = get_int(rec, 'ResponseCount')
    for i in range(rc):
        emotion = get_int(rec, f'Response[{i}].EmotionType')
        emotion_val = max(0, min(100, get_int(rec, f'Response[{i}].EmotionValue')))
        text = get_str(rec, f'Response[{i}].ResponseText')
        actor_notes = get_str(rec, f'Response[{i}].ActorNotes')
        resp_num = get_int(rec, f'Response[{i}].ResponseNumber') or (i + 1)
        # EmotionType(U32) EmotionVal(U32) Unused(4) RespNum(U8) Unused(3)
        # Sound(FormID=0) Flags(U8=1 UseEmotionAnim) Unused(3)
        subs += pack_subrecord('TRDT', struct.pack('<IiI B3x I B3x',
                                                   emotion, emotion_val, 0,
                                                   resp_num, 0, 1))
        if text:
            subs += pack_string_subrecord('NAM1', text)
        subs += pack_string_subrecord('NAM2', actor_notes or '')
        subs += pack_string_subrecord('NAM3', '')

    # Injected Skyrim-required gates FIRST, then translated TES4 conditions.
    subs += injected_ctdas
    for ctda in convert_ctda_list(rec):
        subs += pack_subrecord('CTDA', ctda)

    return pack_record('INFO', get_formid(rec, 'FormID'),
                       get_int(rec, 'RecordFlags'), subs)


# ===========================================================================
# DLBR / DLVW synthesis
# ===========================================================================

def make_dlbr(fid: int, edid: str, quest_fid: int, dial_fid: int,
              top_level: bool) -> bytes:
    """DLBR — Dialog Branch. top_level controls menu visibility vs link-only."""
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_formid_subrecord('QNAM', quest_fid)
    subs += pack_uint32_subrecord('TNAM', 0)            # Player
    subs += pack_uint32_subrecord('DNAM', 1 if top_level else 0)
    subs += pack_formid_subrecord('SNAM', dial_fid)
    return pack_record('DLBR', fid, 0, subs)


def make_dlvw(fid: int, edid: str, quest_fid: int,
              branch_fids: list, topic_fids: list) -> bytes:
    """DLVW — Dialog View (CK metadata; no runtime effect)."""
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_formid_subrecord('QNAM', quest_fid)
    for bfid in branch_fids:
        subs += pack_formid_subrecord('BNAM', bfid)
    for tfid in topic_fids:
        subs += pack_formid_subrecord('TNAM', tfid)
    subs += pack_uint32_subrecord('ENAM', 0)
    subs += pack_uint8_subrecord('DNAM', 0)
    return pack_record('DLVW', fid, 0, subs)


# ===========================================================================
# Voice file naming
# ===========================================================================

def voice_file_prefix(quest_edid: str, topic_edid: str) -> str:
    """The `<quest>_<topic>` prefix Skyrim uses to resolve a voice file.

    Runtime path: Sound\\Voice\\<plugin>\\<VoiceType>\\<prefix>_<fid8>_<n>.fuz
    built from the OWNING quest EditorID + topic EditorID. Truncation rule
    verified against all ~54K joinable filenames in the vanilla Voices BSA:
      - topic has an EditorID: quest[:10] + '_' + topic[:25 - len(questpart)]
        (combined prefix capped at 26 chars; topic gets the slack when the
        quest is shorter than 10)
      - topic has NO EditorID: quest uncut + '_' (double underscore before
        the FormID)
    All lowercase. The FormID component is the 8-hex value with the
    load-order byte zeroed.
    """
    if topic_edid:
        q = quest_edid[:10]
        return f"{q}_{topic_edid[:25 - len(q)]}".lower()
    return f"{quest_edid}_".lower()


# ===========================================================================
# Pre-scan helpers
# ===========================================================================

def collect_tclt_target_fids(by_type: dict) -> set:
    """DIAL FormIDs that are TCLT choice targets (reachable only via a parent
    INFO's choice list) -> these get a Normal (non-top-level) branch."""
    targets = set()
    for rec in by_type.get('INFO', []):
        cc = get_int(rec, 'ChoiceCount')
        for i in range(cc):
            cfid = get_formid(rec, f'Choice[{i}]')
            if cfid:
                targets.add(cfid)
        cfid = get_formid(rec, 'TCLT.Choice')
        if cfid:
            targets.add(cfid)
    return targets


def build_npc_to_vtyp_map(by_type: dict, num_new_masters: int) -> dict:
    """NPC/CREA FormID (remapped) -> VTYP FormID, from race + gender.

    Voice types are the custom TES4* records (kept so the converted audio
    folders match). VNAM voice-race override is honored when present.
    """
    from .skyrim_overrides import TES4_RACE_FID_TO_EDID, VOICE_TYPE_MAP
    npc_to_vtyp = {}
    offset = num_new_masters
    for sig in ('NPC_', 'CREA'):
        for rec in by_type.get(sig, []):
            raw_fid = int(rec.get('FormID', '0'), 16)
            if (raw_fid >> 24) == 0x00 and (raw_fid & 0x00FFFFFF) >= 0x100:
                remapped = (raw_fid & 0x00FFFFFF) | (offset << 24)
            else:
                remapped = raw_fid
            # Voice-race override (NPC_ VNAM) takes precedence over the NPC race.
            voice_race_fid = (get_formid(rec, 'VNAM.Male')
                              or get_formid(rec, 'VNAM.Female')
                              or get_formid(rec, 'RNAM.Race'))
            race_edid = TES4_RACE_FID_TO_EDID.get(
                voice_race_fid & 0x00FFFFFF, 'Imperial')
            gender = 'Female' if (get_int(rec, 'ACBS.Flags') & 1) else 'Male'
            vtyp = (VOICE_TYPE_MAP.get((race_edid, gender))
                    or VOICE_TYPE_MAP.get(('Imperial', gender), 0))
            if vtyp:
                npc_to_vtyp[remapped] = vtyp
    return npc_to_vtyp


# ===========================================================================
# Main dialogue group builder
# ===========================================================================

def build_dialog_groups(by_type: dict, writer, npc_to_vtyp: dict,
                        fid_to_edid: dict = None, xref=None,
                        well_known_props: dict = None,
                        voice_map: dict = None,
                        unlock_plan: dict = None,
                        unlock_globals: dict = None) -> set:
    """Build the DIAL/INFO/DLBR/DLVW hierarchy with original-quest ownership.

    Returns the set of quest FormIDs that must go in the .seq file (the
    synthetic generic dialogue quest; real SGE quests are added by the QUST
    pass in import_main).

    voice_map, when given, is filled with {info_fid_low24: voice filename
    prefix} so the audio pipeline can name extracted voice files the way the
    Skyrim engine will look them up (owning quest EDID + topic EDID).
    """

    dials = by_type.get('DIAL', [])
    infos = by_type.get('INFO', [])
    if not dials:
        return set()

    offset = get_formid_index_offset()

    def remap(fid: int) -> int:
        if offset and fid and (fid >> 24) == 0x00 and (fid & 0x00FFFFFF) >= 0x100:
            return (fid & 0x00FFFFFF) | (offset << 24)
        return fid

    # --- Synthetic generic dialogue quest (owns orphan/bark topics only) ---
    generic_quest_fid = writer.alloc_formid()
    gq = pack_string_subrecord('EDID', 'TES4DialogueGeneric')
    gq += pack_string_subrecord('FULL', 'TES4 Generic Dialogue')
    gq += pack_subrecord('DNAM', struct.pack('<HBBII', 0x0011, 0, 0, 0, 0))
    gq += pack_subrecord('NEXT', b'')
    gq += pack_uint32_subrecord('ANAM', 0)
    writer.add_record('QUST', pack_record('QUST', generic_quest_fid, 0, gq))

    # --- Pre-scan ---
    skipped_fids = {get_formid(d, 'FormID') for d in dials if should_skip_dial(d)}
    _strip_dead_tclt(infos, skipped_fids)

    # SGE quests are running from a new game (via the .seq file), so injected
    # GetQuestRunning gates on them are redundant. Raw (unremapped) FormIDs.
    sge_quest_fids = {get_formid(r, 'FormID') for r in by_type.get('QUST', [])
                      if (get_int(r, 'DATA.Flags') & 0x01)
                      and get_formid(r, 'FormID')}

    # Quest EDID lookup (remapped FormID space) for voice filename prefixes.
    quest_edid_by_fid = {get_formid(r, 'FormID'): get_str(r, 'EditorID', '')
                         for r in by_type.get('QUST', [])
                         if get_formid(r, 'FormID')}
    quest_edid_by_fid[generic_quest_fid] = 'TES4DialogueGeneric'

    # TES4 quest priorities: Oblivion picks the first passing INFO in QUEST
    # PRIORITY order (highest first), NOT file order. Our flattened topics are
    # evaluated by Skyrim in physical INFO order, so the arbitration must be
    # baked in by sorting each topic's children by their own quest's priority
    # (stable — file order preserved within a quest). Without this, e.g.
    # Azzan's low-priority(11) first-meeting intro outranks the priority-60
    # Fighters Guild ad greeting that reveals the join topics.
    quest_priority = {get_formid(r, 'FormID'): get_int(r, 'DATA.Priority')
                      for r in by_type.get('QUST', [])
                      if get_formid(r, 'FormID')}

    info_by_dial = defaultdict(list)
    for rec in infos:
        info_by_dial[get_formid(rec, 'ParentDIAL')].append(rec)

    tclt_targets = collect_tclt_target_fids(by_type)
    unlock_plan = unlock_plan or {'gated': {}, 'info_reveals': {},
                                  'stage_reveals': {}}
    unlock_globals = unlock_globals or {}

    # Quest-level NPC sets (for fallback identity gating of conversation topics
    # whose own INFOs name no NPC but sibling topics in the same quest do).
    quest_npc_fids = defaultdict(set)
    for d in dials:
        if should_skip_dial(d):
            continue
        qfid = get_formid(d, 'Quest[0]')
        if not qfid:
            continue
        npcs = read_getisid_fids_for_topic(info_by_dial.get(get_formid(d, 'FormID'), []))
        if npcs:
            quest_npc_fids[qfid] |= npcs

    print(f"  Dialogue: {len(dials)} topics, {len(infos)} infos, "
          f"{len(npc_to_vtyp)} NPC->VTYP, {len(unlock_plan['gated'])} "
          f"AddTopic-gated topics, {len(unlock_plan['info_reveals'])} "
          f"revealer INFOs")

    stats = defaultdict(int)
    all_dial_content = b''
    all_dlbr = b''
    # Per-owning-quest view aggregation (one DLVW per quest).
    view_branches = defaultdict(list)
    view_topics = defaultdict(list)

    for dial_rec in dials:
        if should_skip_dial(dial_rec):
            stats['skipped'] += 1
            continue
        try:
            content, dlbr_bytes, owner_qfid, dial_fid, dlbr_fid = _build_one_topic(
                dial_rec, info_by_dial, writer, remap, offset, generic_quest_fid,
                tclt_targets, unlock_plan, unlock_globals, npc_to_vtyp,
                quest_npc_fids, sge_quest_fids, quest_edid_by_fid,
                quest_priority, voice_map,
                fid_to_edid, xref, well_known_props, stats)
            all_dial_content += content
            if dlbr_bytes:
                all_dlbr += dlbr_bytes
                view_branches[owner_qfid].append(dlbr_fid)
            view_topics[owner_qfid].append(dial_fid)
            stats['topics'] += 1
        except Exception as e:
            print(f"  ERROR topic {get_str(dial_rec, 'EditorID', '?')}: {e}")

    # --- One DLVW per owning quest ---
    all_dlvw = b''
    for qfid, branches in view_branches.items():
        dlvw_fid = writer.alloc_formid()
        all_dlvw += make_dlvw(dlvw_fid, f'TES4View_{qfid:08X}', qfid,
                              branches, view_topics.get(qfid, []))
        stats['views'] += 1

    # --- Fallback generic greetings (one per voice type) ---
    all_dial_content += _build_fallback_greetings(
        writer, generic_quest_fid, npc_to_vtyp, stats)

    if all_dial_content:
        writer.add_raw_group('DIAL', all_dial_content)
    if all_dlbr:
        writer.add_raw_group('DLBR', all_dlbr)
    if all_dlvw:
        writer.add_raw_group('DLVW', all_dlvw)

    print(f"    topics={stats['topics']} infos={stats['infos']} "
          f"branches={stats['branches']} views={stats['views']} "
          f"skipped={stats['skipped']} voice-gated={stats['voice_gated']} "
          f"id-gated={stats['id_gated']} unlock-gated={stats['unlock_gated']} "
          f"revealers={stats['revealers']} quest-gated={stats['quest_gated']}")

    return {generic_quest_fid}


def read_getisid_fids_for_topic(child_infos: list) -> set:
    """Union of GetIsID NPC FormIDs across a topic's child INFOs."""
    npcs = set()
    for info_rec in child_infos:
        npcs |= read_getisid_fids(info_rec, positive_only=True)
    return npcs


def _strip_dead_tclt(infos: list, skipped_fids: set):
    """Remove TCLT choices that point at skipped topics."""
    if not skipped_fids:
        return
    for rec in infos:
        cc = get_int(rec, 'ChoiceCount')
        if cc > 0:
            kept = [rec.get(f'Choice[{i}]', '0') for i in range(cc)
                    if get_formid(rec, f'Choice[{i}]') not in skipped_fids]
            for i in range(cc):
                rec.pop(f'Choice[{i}]', None)
            rec['ChoiceCount'] = str(len(kept))
            for i, raw in enumerate(kept):
                rec[f'Choice[{i}]'] = raw
        else:
            cfid = get_formid(rec, 'TCLT.Choice')
            if cfid and cfid in skipped_fids:
                rec.pop('TCLT.Choice', None)


def _topic_voice_types(child_infos: list, npc_to_vtyp: dict, offset: int) -> set:
    """Voice types of all NPCs named (via GetIsID) anywhere in a topic."""
    vtyps = set()
    for info_rec in child_infos:
        for npc_fid in read_getisid_fids(info_rec, offset=offset, positive_only=True):
            vt = npc_to_vtyp.get(npc_fid)
            if vt:
                vtyps.add(vt)
    return vtyps


def _build_one_topic(dial_rec, info_by_dial, writer, remap, offset,
                     generic_quest_fid, tclt_targets, unlock_plan,
                     unlock_globals, npc_to_vtyp,
                     quest_npc_fids, sge_quest_fids, quest_edid_by_fid,
                     quest_priority, voice_map,
                     fid_to_edid, xref, well_known_props, stats):
    """Convert one DIAL topic and its child INFOs. Returns
    (dial_group_bytes, dlbr_bytes, owner_quest_fid, dial_fid, dlbr_fid)."""
    dial_fid = get_formid(dial_rec, 'FormID')
    edid = get_str(dial_rec, 'EditorID', '')
    dtype = get_int(dial_rec, 'DATA.Type')
    child_infos = info_by_dial.get(dial_fid, [])
    # Oblivion's arbitration: highest quest priority wins, then file order.
    # Skyrim walks the topic's INFO list in physical order, so bake it in.
    child_infos = sorted(
        child_infos,
        key=lambda r: -quest_priority.get(get_formid(r, 'QSTI.Quest'), 0))

    category, subtype, snam, is_bark = classify_topic(edid, dtype)

    # --- Owning quest. A single-quest topic is owned by its original quest
    # (remapped): Skyrim then only evaluates its INFOs while that quest runs,
    # which is exactly Oblivion's QSTI gating. A SHARED topic (multiple QSTI
    # quests) has no single faithful owner — Skyrim would gate every INFO by
    # whichever quest we picked — so it is owned by the always-running generic
    # quest and each INFO is gated on its own quest below.
    orig_quest_fid = get_formid(dial_rec, 'Quest[0]')
    quest_count = get_int(dial_rec, 'QuestCount')
    if orig_quest_fid and quest_count <= 1:
        owner_qfid = remap(orig_quest_fid)
    else:
        owner_qfid = generic_quest_fid

    # --- DLBR (conversation topics only; barks have no branch) ---
    dlbr_fid = 0
    dlbr_bytes = b''
    if not is_bark and child_infos:
        # Branch visibility mirrors Oblivion's reachability: a topic that is a
        # choice (TCLT) target and is NEVER explicitly AddTopic'd can only be
        # reached via the choice in Oblivion (nothing ever adds it to the
        # menu), so it gets a Normal (non-top-level) branch — e.g. "Yes. Sign
        # me up." must not sit in Azzan's topic menu. Choice targets that ARE
        # explicitly added stay top-level; their unlock gate hides them until
        # revealed (their TCLT parents are revealers too).
        is_linked = (dial_fid in tclt_targets
                     and (dial_fid & 0xFFFFFF) not in unlock_plan['gated'])
        dlbr_fid = writer.alloc_formid()
        dlbr_edid = (f'TES4_{edid}_Branch' if edid
                     else f'TES4_DLBR_{dlbr_fid:08X}')
        dlbr_bytes = make_dlbr(dlbr_fid, dlbr_edid, owner_qfid, dial_fid,
                               top_level=not is_linked)
        stats['branches'] += 1

    # --- Identity gating data for conversation topics ---
    topic_npc_fids = set()
    if not is_bark:
        topic_npc_fids = read_getisid_fids_for_topic(child_infos)
        for info_rec in child_infos:
            topic_npc_fids |= read_getisid_fids(info_rec, offset=offset)
        if not topic_npc_fids and orig_quest_fid:
            topic_npc_fids = quest_npc_fids.get(orig_quest_fid, set())

    # Voice types named anywhere in the topic (for generic siblings/greetings).
    topic_vtyps = _topic_voice_types(child_infos, npc_to_vtyp, offset)

    # AddTopic unlock gate: Oblivion's central visibility mechanic — this topic
    # only appears once a revealing line/script fired. Re-expressed as
    # GetGlobalValue(TES4Unlock_<topic>) == 1; revealer fragments set it.
    unlock_gate_bytes = b''
    gname = unlock_plan['gated'].get(dial_fid & 0xFFFFFF)
    gfid = unlock_globals.get(gname) if gname else None
    if gfid:
        unlock_gate_bytes = pack_subrecord('CTDA', build_ctda(
            FUNC_GET_GLOBAL_VALUE, param1=gfid))

    # --- Convert child INFOs ---
    topic_children = b''
    child_count = 0
    for info_rec in child_infos:
        try:
            # Per-INFO quest gate: Oblivion only shows an INFO while its own
            # QSTI quest runs. When the topic's owner quest is not that quest
            # (shared topics owned by the generic quest), re-express the
            # gating as GetQuestRunning. SGE quests are exempt (always
            # running from a new game via the .seq file).
            quest_gate_bytes = b''
            info_qfid = get_formid(info_rec, 'QSTI.Quest') or orig_quest_fid
            if (info_qfid and info_qfid not in sge_quest_fids
                    and remap(info_qfid) != owner_qfid):
                quest_gate_bytes = pack_subrecord('CTDA', build_ctda(
                    FUNC_GET_QUEST_RUNNING, param1=remap(info_qfid)))
            injected = _build_injected_ctdas(
                info_rec, is_bark, npc_to_vtyp, topic_vtyps, topic_npc_fids,
                quest_gate_bytes, unlock_gate_bytes, offset, stats)
            # Revealer INFO: its OnEnd fragment sets the unlock globals; bind
            # each global name -> GLOB FormID as a VMAD property.
            reveal_names = unlock_plan['info_reveals'].get(
                get_formid(info_rec, 'FormID') & 0xFFFFFF)
            reveal_props = None
            if reveal_names:
                reveal_props = {n: unlock_globals[n] for n in reveal_names
                                if n in unlock_globals}
                if reveal_props:
                    stats['revealers'] += 1
            topic_children += convert_INFO(
                info_rec, injected_ctdas=injected, fid_to_edid=fid_to_edid,
                well_known_props=well_known_props, xref=xref,
                reveal_props=reveal_props)
            child_count += 1
            stats['infos'] += 1
            if voice_map is not None:
                info_fid = get_formid(info_rec, 'FormID')
                voice_map[info_fid & 0xFFFFFF] = voice_file_prefix(
                    quest_edid_by_fid.get(owner_qfid, ''), edid)
        except Exception as e:
            print(f"  ERROR info under {edid or '?'}: {e}")

    dial_bytes = convert_DIAL(
        dial_rec, info_count=child_count, dlbr_fid=dlbr_fid,
        quest_fid=owner_qfid, category=category, subtype=subtype, snam=snam)
    content = dial_bytes
    if topic_children:
        content += pack_group(7, struct.pack('<I', dial_fid), topic_children)
    return content, dlbr_bytes, owner_qfid, dial_fid, dlbr_fid


def _build_injected_ctdas(info_rec, is_bark, npc_to_vtyp, topic_vtyps,
                          topic_npc_fids, quest_gate_bytes, unlock_gate_bytes,
                          offset, stats):
    """Build the Skyrim-required gates for one INFO, ordered for OR-chain safety.

    Order (outermost AND first): [quest-running gate] [AddTopic unlock gate]
    [voice-type OR-chain] [identity OR-chain]. Each OR-chain is internally
    isolated. Single-quest topics get no quest gate — quest ownership
    provides it natively.
    """
    # Voice types: from this INFO's own GetIsID NPCs, else the topic's voice set.
    own_npcs = read_getisid_fids(info_rec, offset=offset, positive_only=True)
    if own_npcs:
        vtyps = {npc_to_vtyp[n] for n in own_npcs if n in npc_to_vtyp}
    else:
        # Generic INFO: inherit the topic's voice types (greetings included).
        vtyps = set(topic_vtyps)
    voice_bytes = b''
    if vtyps:
        voice_bytes = build_or_chain(FUNC_GET_IS_VOICE_TYPE, sorted(vtyps))
        stats['voice_gated'] += 1

    # Identity gate: conversation INFO lacking its own positive GetIsID.
    id_bytes = b''
    if not is_bark and topic_npc_fids and not has_positive_getisid(info_rec):
        id_bytes = build_or_chain(FUNC_GET_IS_ID, sorted(topic_npc_fids))
        stats['id_gated'] += 1

    if unlock_gate_bytes:
        stats['unlock_gated'] += 1
    if quest_gate_bytes:
        stats['quest_gated'] += 1

    return quest_gate_bytes + unlock_gate_bytes + voice_bytes + id_bytes


def _build_fallback_greetings(writer, generic_quest_fid, npc_to_vtyp, stats):
    """One low-priority 'Hello.' greeting per voice type so every NPC can greet.

    Priority 10 < 50 so any real converted greeting wins.
    """
    vtyp_fids = sorted(set(npc_to_vtyp.values()))
    if not vtyp_fids:
        return b''
    fallback_dial_fid = writer.alloc_formid()
    infos = b''
    for vtyp_fid in vtyp_fids:
        info_fid = writer.alloc_formid()
        s = pack_subrecord('ENAM', struct.pack('<HH', 0, 0))
        s += pack_subrecord('CNAM', struct.pack('<B', 0))
        s += pack_subrecord('TRDT', struct.pack('<IiI B3x I B3x', 0, 0, 0, 1, 0, 1))
        s += pack_string_subrecord('NAM1', 'Hello.')
        s += pack_string_subrecord('NAM2', '')
        s += pack_string_subrecord('NAM3', '')
        s += pack_subrecord('CTDA', build_ctda(FUNC_GET_IS_VOICE_TYPE, param1=vtyp_fid))
        infos += pack_record('INFO', info_fid, 0, s)
        stats['infos'] += 1

    d = pack_string_subrecord('EDID', 'TES4FallbackHello')
    d += pack_string_subrecord('FULL', 'Fallback Hello')
    d += pack_subrecord('PNAM', struct.pack('<f', 10.0))
    d += pack_formid_subrecord('QNAM', generic_quest_fid)
    # flags, Category=7 (Misc), Subtype=73 (Hello) — real on-disk values
    d += pack_subrecord('DATA', struct.pack('<BBH', 0, 7, 73))
    d += pack_subrecord('SNAM', b'HELO')
    d += pack_uint32_subrecord('TIFC', len(vtyp_fids))
    dial = pack_record('DIAL', fallback_dial_fid, 0, d)
    stats['topics'] += 1
    print(f"    Fallback greetings: {len(vtyp_fids)} voice types")
    return dial + pack_group(7, struct.pack('<I', fallback_dial_fid), infos)
