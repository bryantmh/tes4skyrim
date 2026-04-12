"""
Complete TES4→TES5 dialog conversion system.

Converts: QUST, DIAL, INFO → QUST, DIAL, INFO, DLBR, DLVW, VTYP

Architecture
============
Skyrim dialog is structured differently from Oblivion:

TES4 (Oblivion):
  - DIAL owns INFOs directly; DIAL lists quests via QSTI
  - INFO has conditions that gate who/when it plays
  - Type 0=Topic (selectable), 1=Conversation (chain via TCLT),
    2=Combat, 3=Persuasion, 4=Detection, 5=Service, 6=Misc

TES5 (Skyrim):
  - Every DIAL MUST have QNAM (owning quest). Quest must be running for dialog to fire.
  - DLBR (Dialog Branch) links a top-level DIAL to a QUST; DIAL refs DLBR via BNAM.
  - Every INFO MUST have GetIsVoiceType condition to route to correct NPC voice.
  - NPC-specific INFOs also need GetIsID conditions.
  - Greetings = DIAL with SNAM='HELO', Category=7(Misc), Subtype=4(ForceGreet);
    top-level topics = SNAM='CUST', Category=0(Topic), Subtype=0(Custom).

Pipeline Steps
==============
1. Build NPC→VoiceType map from NPC_/CREA race+gender
2. Build DIAL→INFO index, INFO→quest map
3. Classify DIALs: greeting, topic, conversation-chain, combat, detection, misc
4. For each quest with dialog: create QUST record
5. For each DIAL: create DIAL record with correct category/subtype/SNAM
6. For each top-level Topic DIAL: create DLBR (dialog branch)
7. For each quest: create DLVW (dialog view, CK metadata)
8. For each INFO: convert conditions (TES4 24B→TES5 32B), inject GetIsVoiceType,
   inject GetIsID, build responses, build TCLT links
9. Assemble DIAL group hierarchy (type 7=topic children groups)
"""

import struct
from collections import defaultdict

from .text_reader import get_float, get_formid, get_int, get_str
from .writer import (
    PluginWriter,
    pack_float_subrecord,
    pack_formid_subrecord,
    pack_group,
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
    pack_uint8_subrecord,
    pack_uint16_subrecord,
    pack_uint32_subrecord,
)

# ---------------------------------------------------------------------------
# Condition function IDs
# ---------------------------------------------------------------------------
FUNC_GETISID = 72           # GetIsID(npc_fid) — NPC identity check
FUNC_GETISVOICETYPE = 426   # GetIsVoiceType(vtyp_fid) — voice type check
FUNC_GETQUESTRUNNING = 56
FUNC_GETSTAGE = 58
FUNC_GETSTAGEDONE = 59
FUNC_GETQUESTCOMPLETED = 99
FUNC_GETDISPOSITION = 76    # TES4-only, drop
FUNC_GETVAMPIRE = 40        # TES4-only, drop
FUNC_ISYIELDING = 104       # TES4-only, drop
FUNC_GETPLAYERINJAIL = 171  # TES4-only, drop
FUNC_GETPCINFAMY = 251      # TES4-only, drop
FUNC_GETISBIRTHSIGN = 79    # TES4-only, drop

# TES4-only condition functions that must be dropped
_DROPPED_FUNCS = frozenset({
    FUNC_GETDISPOSITION, FUNC_GETVAMPIRE, FUNC_ISYIELDING,
    FUNC_GETPLAYERINJAIL, FUNC_GETPCINFAMY, FUNC_GETISBIRTHSIGN,
})

# TES4 DIAL Type classification
_TYPE_TOPIC = 0           # Selectable conversation topic
_TYPE_CONVERSATION = 1    # Chain topic (reached only via TCLT)
_TYPE_COMBAT = 2
_TYPE_PERSUASION = 3      # Skip (Oblivion speechcraft minigame)
_TYPE_DETECTION = 4
_TYPE_SERVICE = 5         # Skip (barter/repair etc. hardcoded in TES5)
_TYPE_MISC = 6

# Maximum number of NPC FormIDs in a topic's donor pool before we consider
# the topic "generic" and stop injecting GetIsID / topic-level VTYP.
# GREETING has 3288 NPCs in its pool and should NOT have GetIsID injected.
_MAX_TOPIC_NPC_INJECT = 30

# TES5 DIAL Category→Subtype→SNAM mapping
# For bark topics (combat/detect/misc).
# Values verified from Skyrim.esm DIAL records (DCETAttack, DCETNormalToAlert, etc.)
_BARK_MAPPING = {
    _TYPE_COMBAT:    (3, 20, b'ATCK'),     # Category=Combat, Subtype=20(Attack), SNAM=ATCK
    _TYPE_DETECTION: (5, 51, b'NOTA'),     # Category=Detection, Subtype=51(NormalToAlert), SNAM=NOTA
    _TYPE_MISC:      (7, 88, b'IDLE'),     # Category=Misc, Subtype=88(Idle), SNAM=IDLE
}

# Per-EditorID overrides for known TES4 bark topics.
# Maps EditorID → (Category, Subtype, SNAM) using correct Skyrim equivalents.
# Detection state transitions (Cat=5): NOTA=51, ALTC=52, NOTC=53, LOTN=57
# Combat (Cat=3): ATCK=20, HIT_=23, FLEE=24, DETH=27
# Misc (Cat=7): IDLE=88, HELO=73, GBYE=72
_EDID_BARK_OVERRIDE = {
    # Detection states
    'Noticed':       (5, 51, b'NOTA'),   # NPC notices player sneaking → NormalToAlert
    'Seen':          (5, 52, b'ALTC'),   # NPC spots player → AlertToCombat
    'Unseen':        (5, 57, b'LOTN'),   # NPC lost track → LostToNormal
    'Lost':          (5, 57, b'LOTN'),   # Player fully lost → LostToNormal
    # Misc barks
    'Idle':          (7, 88, b'IDLE'),
    'IdleChatter':   (7, 88, b'IDLE'),
    'ObserveCombat': (7, 88, b'IDLE'),
    'Corpse':        (7, 88, b'IDLE'),
    'NoticeCorpse':  (7, 88, b'IDLE'),
    'TimeToGo':      (7, 88, b'IDLE'),
    'InfoRefusal':   (7, 88, b'IDLE'),
    'GREETING':      (7, 73, b'HELO'),   # Hello bark
    'HELLO':         (7, 73, b'HELO'),
    'GOODBYE':       (7, 72, b'GBYE'),
    # Combat barks
    'Attack':        (3, 20, b'ATCK'),
    'Hit':           (3, 23, b'HIT_'),
    'Flee':          (3, 24, b'FLEE'),
    'Steal':         (3, 43, b'TRES'),
    'Trespass':      (3, 43, b'TRES'),
    'ServiceRefusal':(7, 88, b'IDLE'),
    'Barter':        (7, 88, b'IDLE'),
    'Repair':        (7, 88, b'IDLE'),
    'Travel':        (7, 88, b'IDLE'),
}


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Step 1: Parse & classify DIAL records
# ---------------------------------------------------------------------------

def _parse_dials(by_type: dict) -> dict:
    """Parse DIAL records into a lookup: TES4 raw FormID → dial info dict."""
    dials = {}
    for rec in by_type.get('DIAL', []):
        raw_fid = int(rec.get('FormID', '0'), 16)
        tes5_fid = get_formid(rec, 'FormID')
        dial_type = get_int(rec, 'DATA.Type')
        edid = get_str(rec, 'EditorID')
        full = get_str(rec, 'FULL')

        # Collect quest associations
        quest_fids = []
        i = 0
        while True:
            q = rec.get(f'Quest[{i}]')
            if q is None:
                break
            quest_fids.append(int(q, 16))
            i += 1

        dials[raw_fid] = {
            'raw_fid': raw_fid,
            'tes5_fid': tes5_fid,
            'edid': edid,
            'full': full,
            'type': dial_type,
            'quest_fids_raw': quest_fids,  # TES4 raw FormIDs
        }
    return dials


# ---------------------------------------------------------------------------
# Step 2: Parse INFO records; build INFO→DIAL and INFO→quest indexes
# ---------------------------------------------------------------------------

def _parse_infos(by_type: dict) -> list:
    """Parse INFO records. Returns list of info dicts."""
    infos = []
    for rec in by_type.get('INFO', []):
        raw_fid = int(rec.get('FormID', '0'), 16)
        tes5_fid = get_formid(rec, 'FormID')
        parent_dial_raw = int(rec.get('ParentDIAL', '0'), 16)
        dial_type = get_int(rec, 'DATA.DialogType')
        flags = get_int(rec, 'DATA.Flags')
        quest_raw = int(rec.get('QSTI.Quest', '0'), 16)

        # Parse conditions (raw 24-byte TES4 CTDA)
        conditions_raw = []
        i = 0
        while True:
            cond_hex = rec.get(f'Condition[{i}].Raw')
            if cond_hex is None:
                break
            conditions_raw.append(bytes.fromhex(cond_hex))
            i += 1

        # Parse responses
        responses = []
        resp_count = get_int(rec, 'ResponseCount')
        for j in range(resp_count):
            responses.append({
                'emotion_type': get_int(rec, f'Response[{j}].EmotionType'),
                'emotion_value': get_int(rec, f'Response[{j}].EmotionValue'),
                'response_number': get_int(rec, f'Response[{j}].ResponseNumber'),
                'text': get_str(rec, f'Response[{j}].ResponseText'),
                'notes': get_str(rec, f'Response[{j}].ActorNotes'),
            })

        # Parse AddTopic links
        add_topics = []
        i = 0
        while True:
            at = rec.get(f'AddTopic[{i}]')
            if at is None:
                break
            add_topics.append(int(at, 16))
            i += 1

        # Parse TCLT (topic choice links) - TES4 doesn't have these directly,
        # but AddTopic records are essentially TCLT equivalents
        tclt_raw = []
        i = 0
        while True:
            tc = rec.get(f'TCLT[{i}]')
            if tc is None:
                break
            tclt_raw.append(int(tc, 16))
            i += 1

        infos.append({
            'raw_fid': raw_fid,
            'tes5_fid': tes5_fid,
            'parent_dial_raw': parent_dial_raw,
            'dial_type': dial_type,
            'flags': flags,
            'quest_raw': quest_raw,
            'conditions_raw': conditions_raw,
            'responses': responses,
            'add_topics': add_topics,  # TES4 AddTopic → TES5 TCLT
            'tclt_raw': tclt_raw,
        })
    return infos


# ---------------------------------------------------------------------------
# Step 3: Parse QUST records
# ---------------------------------------------------------------------------

def _parse_qusts(by_type: dict) -> dict:
    """Parse QUST records. Returns raw_fid → qust dict."""
    qusts = {}
    for rec in by_type.get('QUST', []):
        raw_fid = int(rec.get('FormID', '0'), 16)
        tes5_fid = get_formid(rec, 'FormID')
        edid = get_str(rec, 'EditorID')
        full = get_str(rec, 'FULL')
        flags = get_int(rec, 'DATA.Flags')
        priority = get_int(rec, 'DATA.Priority')

        # Parse stages
        stages = []
        sc = get_int(rec, 'StageCount')
        for i in range(sc):
            stage_idx = get_int(rec, f'Stage[{i}].Index')
            log_count = get_int(rec, f'Stage[{i}].LogCount')
            logs = []
            for j in range(log_count):
                logs.append({
                    'flags': get_int(rec, f'Stage[{i}].Log[{j}].Flags'),
                    'text': get_str(rec, f'Stage[{i}].Log[{j}].Text'),
                })
            stages.append({
                'index': stage_idx,
                'logs': logs,
            })

        # Parse quest conditions
        q_conditions = []
        i = 0
        while True:
            cond_hex = rec.get(f'Condition[{i}].Raw')
            if cond_hex is None:
                break
            q_conditions.append(bytes.fromhex(cond_hex))
            i += 1

        qusts[raw_fid] = {
            'raw_fid': raw_fid,
            'tes5_fid': tes5_fid,
            'edid': edid,
            'full': full,
            'flags': flags,
            'priority': priority,
            'stages': stages,
            'conditions_raw': q_conditions,
        }
    return qusts


# ---------------------------------------------------------------------------
# Step 4: Condition conversion (TES4 24B → TES5 32B)
# ---------------------------------------------------------------------------

def _remap_formid_in_ctda(raw_fid: int, offset: int) -> int:
    """Apply load order offset to a FormID inside a condition."""
    if raw_fid == 0:
        return 0
    high = (raw_fid >> 24) & 0xFF
    return ((high + offset) << 24) | (raw_fid & 0x00FFFFFF)


def _convert_ctda_24_to_32(raw24: bytes, formid_offset: int) -> bytes:
    """Convert a single TES4 24-byte CTDA to TES5 32-byte CTDA.

    TES4: Type(1) + Unused(3) + CompValue(f32) + FuncIdx(U16) + Pad(2) +
          Param1(4) + Param2(4) + RunOn(4) = 24 bytes
    TES5: Same first 24 bytes + Reference(4) + Param3(S32=-1) = 32 bytes

    FormIDs in Param1/Param2 must be remapped via formid_offset.
    """
    if len(raw24) < 24:
        return raw24 + b'\x00' * (32 - len(raw24))

    type_byte = raw24[0]
    comp_value = raw24[4:8]
    func_idx = struct.unpack_from('<H', raw24, 8)[0]
    param1 = struct.unpack_from('<I', raw24, 12)[0]
    param2 = struct.unpack_from('<I', raw24, 16)[0]
    run_on = struct.unpack_from('<I', raw24, 20)[0]

    # Check if this is a dropped function
    if func_idx in _DROPPED_FUNCS:
        return None  # Signal to caller to skip this condition

    # Remap FormID params based on function type
    # Functions where param1 is a FormID:
    _FORMID_P1_FUNCS = {
        72,    # GetIsID
        56,    # GetQuestRunning
        58,    # GetStage
        59,    # GetStageDone
        99,    # GetQuestCompleted
        68,    # GetIsClass
        69,    # GetIsRace
        71,    # GetInCell
        73,    # GetInFaction
        74,    # GetFactionRank
        161,   # GetIsCurrentPackage
        84,    # GetDead (param1=0 usually but can be FormID)
    }
    if func_idx in _FORMID_P1_FUNCS and param1 != 0:
        param1 = _remap_formid_in_ctda(param1, formid_offset)

    # Use Global flag (bit 2 = 0x04): comp_value is a Global FormID
    use_global = bool(type_byte & 0x04)
    if use_global:
        glob_fid = struct.unpack_from('<I', comp_value, 0)[0]
        if glob_fid:
            glob_fid = _remap_formid_in_ctda(glob_fid, formid_offset)
            comp_value = struct.pack('<I', glob_fid)

    # Build 32-byte CTDA
    result = struct.pack('<B3s', type_byte, raw24[1:4])
    result += comp_value
    result += struct.pack('<HH', func_idx, 0)  # func + padding
    result += struct.pack('<I', param1)
    result += struct.pack('<I', param2)
    result += struct.pack('<I', run_on)
    result += struct.pack('<I', 0)    # Reference (unused for RunOn=0/1)
    result += struct.pack('<i', -1)   # Param3 (TES5 new, default -1)
    return result



def _build_getisid_ctda(npc_fid: int, is_or: bool = False) -> bytes:
    """Build a TES5 CTDA for GetIsID(npc_fid) == 1.0"""
    type_byte = 0x00
    if is_or:
        type_byte |= 0x01
    return struct.pack(
        '<B3sfHHIIIIi',
        type_byte,
        b'\x00\x00\x00',
        1.0,
        FUNC_GETISID,
        0,
        npc_fid,          # param1 = NPC_ FormID
        0,
        0,                # run_on = Subject
        0,
        -1,
    )


# ---------------------------------------------------------------------------
# Step 5: Analyze INFO conditions to extract NPC→VoiceType associations
# ---------------------------------------------------------------------------

def _extract_npc_fids_from_conditions(conditions_raw: list, formid_offset: int) -> set:
    """Extract NPC FormIDs from GetIsID conditions in an INFO record."""
    npc_fids = set()
    for raw in conditions_raw:
        if len(raw) < 24:
            continue
        func_idx = struct.unpack_from('<H', raw, 8)[0]
        if func_idx == FUNC_GETISID:
            type_byte = raw[0]
            comp_value = struct.unpack_from('<f', raw, 4)[0]
            # Only positive GetIsID (==1.0, not !=1.0)
            comp_type = (type_byte >> 5) & 0x07
            if comp_type == 0 and comp_value == 1.0:  # EqualTo 1.0
                raw_param1 = struct.unpack_from('<I', raw, 12)[0]
                npc_fids.add(_remap_formid_in_ctda(raw_param1, formid_offset))
    return npc_fids


# ---------------------------------------------------------------------------
# Step 6: Build the complete dialog system
# ---------------------------------------------------------------------------

def build_dialog_system(by_type: dict, writer: PluginWriter,
                        well_known_props: dict,
                        dialogue_quest_fids: set):
    """Main entry point: builds all dialog records (QUST, DIAL, INFO, DLBR, DLVW).

    Called from import_main.py Phase 5.
    """
    from .text_reader import get_formid_index_offset
    formid_offset = get_formid_index_offset()

    # --- Parse all source data ---
    print("  Building dialog system...")
    dials = _parse_dials(by_type)
    infos = _parse_infos(by_type)
    qusts = _parse_qusts(by_type)

    # --- Index: group INFOs by parent DIAL ---
    infos_by_dial = defaultdict(list)
    for info in infos:
        infos_by_dial[info['parent_dial_raw']].append(info)

    # --- Index: collect all quest FIDs that own dialog ---
    # A quest owns dialog if any DIAL lists it in its QuestCount
    quest_dials = defaultdict(list)   # quest_raw_fid → [dial dicts]
    orphan_dials = []                 # DIALs without quest association
    for dial in dials.values():
        if dial['quest_fids_raw']:
            # Assign to first quest (primary owner)
            primary_quest = dial['quest_fids_raw'][0]
            quest_dials[primary_quest].append(dial)
        else:
            orphan_dials.append(dial)

    # --- Classify and skip ---
    # Skip persuasion (type 3) and service (type 5)
    skipped_dials = set()
    for dial in dials.values():
        if dial['type'] in (_TYPE_PERSUASION, _TYPE_SERVICE):
            skipped_dials.add(dial['raw_fid'])

    # --- Build NPC FID → set of voice type FIDs for injection ---
    # topic_npc_fids: all NPC FormIDs found in GetIsID conditions within each
    # DIAL topic.  Used to inject GetIsID on generic sibling INFOs, but only
    # when the set is small (<= _MAX_TOPIC_NPC_INJECT) so that broad topics
    # like GREETING (3288 NPCs) aren't injected onto every generic INFO.
    topic_npc_fids = defaultdict(set)     # dial_raw_fid → set of NPC FIDs
    for info in infos:
        dial_raw = info['parent_dial_raw']
        if dial_raw in skipped_dials:
            continue
        npc_fids = _extract_npc_fids_from_conditions(info['conditions_raw'], formid_offset)
        topic_npc_fids[dial_raw].update(npc_fids)

    # --- Create catch-all quest for orphan DIALs ---
    generic_quest_fid = writer.alloc_formid()
    _write_quest_record(writer, generic_quest_fid, 'TES4DialogueGeneric',
                        'TES4 Generic Dialogue', flags=0x0011, priority=0)

    # --- Phase A: Write QUST records ---
    print("  Phase A: Writing QUST records...")
    quest_count = 0
    # Quests that own dialog get dialogue-appropriate flags
    dialogue_quests = set()  # quest raw FIDs that own dialog

    for q_raw, q_data in qusts.items():
        q_fid = q_data['tes5_fid']
        owns_dialog = q_raw in quest_dials
        if owns_dialog:
            dialogue_quests.add(q_raw)

        # Determine flags — RESPECT TES4 StartGameEnabled flag.
        # Only quests that were already StartGameEnabled in TES4 should be
        # auto-running in TES5. Quest-started dialog (EmfridDEMO etc.) must
        # NOT be forced on, or their dialog appears on all NPCs.
        tes4_flags = q_data['flags']
        tes5_flags = 0
        if tes4_flags & 0x01:  # TES4 StartGameEnabled
            tes5_flags |= 0x0001  # TES5 StartGameEnabled
            tes5_flags |= 0x0010  # TES5 StartsEnabled

        priority = min(q_data['priority'], 100)

        _write_quest_record(
            writer, q_fid, q_data['edid'], q_data['full'],
            flags=tes5_flags, priority=priority,
            stages=q_data['stages'],
            conditions=q_data['conditions_raw'],
            formid_offset=formid_offset,
        )
        quest_count += 1

    print(f"    Wrote {quest_count} QUSTs ({len(dialogue_quests)} with dialog)")

    # --- Phase B: Write DIAL + DLBR + INFO records, assemble groups ---
    print("  Phase B: Writing DIAL/INFO/DLBR/DLVW records...")
    dial_count = 0
    info_count = 0
    dlbr_count = 0
    dlvw_count = 0

    # Track DLBR and DLVW per quest for DLVW generation
    quest_branches = defaultdict(list)   # quest_tes5_fid → [dlbr_fid]
    quest_topics = defaultdict(list)     # quest_tes5_fid → [dial_tes5_fid]

    # All DIAL group content
    all_dial_content = b''

    for dial_raw, dial_data in sorted(dials.items(), key=lambda x: x[1]['tes5_fid']):
        if dial_raw in skipped_dials:
            continue

        dial_fid = dial_data['tes5_fid']
        dial_type = dial_data['type']
        edid = dial_data['edid']
        full = dial_data['full']

        # Determine owning quest — prefer a StartGameEnabled quest from the list
        quest_tes5_fid = generic_quest_fid
        quest_raw = None
        if dial_data['quest_fids_raw']:
            # Try to find a StartGameEnabled quest first
            for q_raw_candidate in dial_data['quest_fids_raw']:
                if q_raw_candidate in qusts and (qusts[q_raw_candidate]['flags'] & 0x01):
                    quest_raw = q_raw_candidate
                    break
            # Fall back to first quest in the list
            if quest_raw is None:
                quest_raw = dial_data['quest_fids_raw'][0]
            if quest_raw in qusts:
                quest_tes5_fid = qusts[quest_raw]['tes5_fid']

        # For GREETING and barks, assign to generic always-running quest
        # GREETING spans 294 quests; it needs a permanently running host.
        is_greeting = edid == 'GREETING'
        if is_greeting or dial_type in (_TYPE_COMBAT, _TYPE_DETECTION, _TYPE_MISC):
            quest_tes5_fid = generic_quest_fid
        is_topic = dial_type == _TYPE_TOPIC and not is_greeting
        is_chain = dial_type == _TYPE_CONVERSATION
        is_bark = dial_type in (_TYPE_COMBAT, _TYPE_DETECTION, _TYPE_MISC)
        needs_dlbr = is_topic and not is_chain

        # Determine TES5 category, subtype, SNAM
        # EditorID overrides take priority (precise per-topic mapping).
        # Subtype values verified from Skyrim.esm (DialogueGenericHello, DCETAttack, etc.)
        if edid and edid in _EDID_BARK_OVERRIDE:
            category, subtype, snam = _EDID_BARK_OVERRIDE[edid]
        elif is_greeting:
            category, subtype, snam = 7, 73, b'HELO'  # Misc/Hello (Sub=73 from Skyrim.esm)
        elif is_topic or is_chain:
            category, subtype, snam = 0, 0, b'CUST'   # Topic/Custom
        elif is_bark:
            category, subtype, snam = _BARK_MAPPING.get(
                dial_type, (7, 88, b'IDLE'))
        else:
            category, subtype, snam = 7, 88, b'IDLE'  # Fallback: Idle bark

        # Create DLBR if needed
        dlbr_fid = 0
        if needs_dlbr:
            dlbr_fid = writer.alloc_formid()
            dlbr_edid = f"TES4_{edid}_Branch" if edid else f"TES4_DIAL_{dial_fid:08X}_Branch"
            _write_dlbr_record(writer, dlbr_fid, dlbr_edid, quest_tes5_fid, dial_fid,
                               branch_type=1)  # TopLevel
            dlbr_count += 1
            quest_branches[quest_tes5_fid].append(dlbr_fid)

        quest_topics[quest_tes5_fid].append(dial_fid)

        # Get INFOs for this DIAL
        dial_infos = infos_by_dial.get(dial_raw, [])
        info_count_this_dial = len(dial_infos)

        # Write DIAL record
        dial_bytes = _pack_dial_record(
            dial_fid, edid, full, quest_tes5_fid, dlbr_fid,
            category, subtype, snam, info_count_this_dial,
        )

        # Build INFO records for this DIAL
        info_group_content = b''
        for info_data in dial_infos:
            info_bytes = _pack_info_record(
                info_data, dial_data,
                topic_npc_fids,
                formid_offset, dials,
            )
            if info_bytes:
                info_group_content += info_bytes
                info_count += 1

        # Assemble: DIAL record + topic children group (type 7)
        dial_group_bytes = dial_bytes
        if info_group_content:
            dial_group_bytes += pack_group(
                7, struct.pack('<I', dial_fid), info_group_content)

        all_dial_content += dial_group_bytes
        dial_count += 1

    # Write DIAL top-level group
    if all_dial_content:
        writer.add_raw_group('DIAL', all_dial_content)

    # --- Phase C: Write DLVW records (one per quest with dialog) ---
    for quest_fid, branches in quest_branches.items():
        dlvw_fid = writer.alloc_formid()
        topics = quest_topics.get(quest_fid, [])
        _write_dlvw_record(writer, dlvw_fid, quest_fid, branches, topics)
        dlvw_count += 1

    print(f"    Wrote {dial_count} DIALs, {info_count} INFOs, "
          f"{dlbr_count} DLBRs, {dlvw_count} DLVWs")


# ---------------------------------------------------------------------------
# Record packing helpers
# ---------------------------------------------------------------------------

def _write_quest_record(writer: PluginWriter, fid: int, edid: str,
                        full: str, flags: int = 0x0011, priority: int = 50,
                        stages: list = None, conditions: list = None,
                        formid_offset: int = 0):
    """Write a TES5 QUST record.

    QUST order: EDID [FULL] DNAM [conditions] NEXT [stages] ANAM
    DNAM = Flags(U16) + Priority(U8) + FormVersion(U8=0) + Unknown(4B) + Type(U32)
    """
    subs = b''
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    if full:
        subs += pack_string_subrecord('FULL', full)

    # DNAM — 12 bytes: Flags(U16) + Priority(U8) + FormVer(U8=0) + Unk(4B) + Type(U32)
    dnam_data = struct.pack('<HBB4sI', flags, min(priority, 255), 0, b'\x00' * 4, 0)
    subs += pack_subrecord('DNAM', dnam_data)

    # Quest conditions (before NEXT marker)
    if conditions:
        for raw in conditions:
            ctda32 = _convert_ctda_24_to_32(raw, formid_offset)
            if ctda32:
                subs += pack_subrecord('CTDA', ctda32)

    # NEXT marker (required, separates pre-conditions from post-conditions)
    subs += pack_subrecord('NEXT', b'')

    # Stages
    if stages:
        for stage in stages:
            # INDX = Stage Index(U16) + Flags(U8) + Unknown(U8) = 4 bytes
            subs += pack_subrecord('INDX', struct.pack('<HBB', stage['index'], 0, 0))
            for log in stage.get('logs', []):
                # QSDT = stage flags (U8)
                subs += pack_subrecord('QSDT', struct.pack('<B', log.get('flags', 0)))
                log_text = log.get('text', '')
                if log_text:
                    subs += pack_string_subrecord('CNAM', log_text)

    # ANAM = Next Alias ID (U32) — always 0 for simple converted quests
    subs += pack_uint32_subrecord('ANAM', 0)

    rec_bytes = pack_record('QUST', fid, 0, subs)
    writer.add_record('QUST', rec_bytes)


def _write_dlbr_record(writer: PluginWriter, fid: int, edid: str,
                        quest_fid: int, start_dial_fid: int,
                        branch_type: int = 1):
    """Write a TES5 DLBR (Dialog Branch) record.

    DLBR order: EDID QNAM TNAM DNAM SNAM
    """
    subs = b''
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    subs += pack_formid_subrecord('QNAM', quest_fid)
    subs += pack_uint32_subrecord('TNAM', 0)              # Category: 0=Player
    subs += pack_uint32_subrecord('DNAM', branch_type)    # 1=TopLevel
    subs += pack_formid_subrecord('SNAM', start_dial_fid) # Starting topic

    rec_bytes = pack_record('DLBR', fid, 0, subs)
    writer.add_record('DLBR', rec_bytes)


def _write_dlvw_record(writer: PluginWriter, fid: int, quest_fid: int,
                        branch_fids: list, topic_fids: list):
    """Write a TES5 DLVW (Dialog View) record.

    DLVW order: EDID QNAM [BNAM]* [TNAM]* ENAM DNAM
    """
    subs = b''
    edid = f"TES4_DLVW_{fid:08X}"
    subs += pack_string_subrecord('EDID', edid)
    subs += pack_formid_subrecord('QNAM', quest_fid)
    for br_fid in branch_fids:
        subs += pack_formid_subrecord('BNAM', br_fid)
    # ENAM = view category (0 = Dialogue Branches)
    subs += pack_uint32_subrecord('ENAM', 0)
    # DNAM = show all text (1 = True)
    subs += pack_uint8_subrecord('DNAM', 1)

    rec_bytes = pack_record('DLVW', fid, 0, subs)
    writer.add_record('DLVW', rec_bytes)


def _pack_dial_record(fid: int, edid: str, full: str, quest_fid: int,
                      dlbr_fid: int, category: int, subtype: int,
                      snam: bytes, info_count: int) -> bytes:
    """Pack a TES5 DIAL record.

    DIAL order: EDID FULL PNAM BNAM QNAM DATA SNAM TIFC
    """
    subs = b''
    if edid:
        subs += pack_string_subrecord('EDID', edid)
    if full:
        subs += pack_string_subrecord('FULL', full)
    # PNAM = Priority (float, default 50.0)
    subs += pack_float_subrecord('PNAM', 50.0)
    # BNAM = Dialog Branch link (only for top-level topics with DLBR)
    if dlbr_fid:
        subs += pack_formid_subrecord('BNAM', dlbr_fid)
    # QNAM = Owning quest (required)
    subs += pack_formid_subrecord('QNAM', quest_fid)
    # DATA = TopicFlags(U8) + Category(U8) + Subtype(U16) = 4 bytes
    subs += pack_subrecord('DATA', struct.pack('<BBH', 0, category, subtype))
    # SNAM = Subtype name (4 chars as U32)
    subs += pack_subrecord('SNAM', snam[:4].ljust(4, b'\x00'))
    # TIFC = Info count (U32)
    subs += pack_uint32_subrecord('TIFC', info_count)

    return pack_record('DIAL', fid, 0, subs)


def _pack_info_record(info_data: dict, dial_data: dict,
                      topic_npc_fids: dict,
                      formid_offset: int, all_dials: dict) -> bytes:
    """Pack a TES5 INFO record.

    INFO order: EDID ENAM CNAM [TCLT]* [TRDT NAM1 NAM2 NAM3]* CTDAs

    NPC routing:
    1. Convert all TES4 conditions to TES5 format (24B→32B)
    3. Inject GetIsID conditions for NPC-specific dialog
    4. OR-chain multiple voice types / NPC IDs
    """
    info_fid = info_data['tes5_fid']
    dial_raw = info_data['parent_dial_raw']
    dial_type = info_data['dial_type']
    flags = info_data['flags']

    subs = b''

    # EDID (optional for INFOs, but useful for debugging)
    # Skip to save space — INFOs typically don't have EDIDs in vanilla Skyrim

    # ENAM = Response Flags(U16) + ResetHours(U16)
    # Map TES4 flags: bits 0,1,2,4,5 are compatible (Goodbye, Random, SayOnce,
    # InfoRefusal, RandomEnd)
    tes5_enam_flags = flags & 0x37  # keeps bits 0,1,2,4,5
    subs += pack_subrecord('ENAM', struct.pack('<HH', tes5_enam_flags, 0))

    # CNAM = Favor Level (U8, always 0=None)
    subs += pack_uint8_subrecord('CNAM', 0)

    # TCLT = Link To (AddTopic references → next topic choices)
    all_topic_links = info_data['add_topics'] + info_data['tclt_raw']
    for raw_at in all_topic_links:
        remapped = _remap_formid_in_ctda(raw_at, formid_offset)
        # Verify the target DIAL exists in our data
        if raw_at in all_dials:
            subs += pack_formid_subrecord('TCLT', remapped)

    # Responses: [TRDT + NAM1 + NAM2 + NAM3]*
    for resp in info_data['responses']:
        # TRDT = 24 bytes: Emotion(U32) + Value(U32) + Unused(4) +
        #        RespNum(U8) + Unused(3) + Sound(FormID=0) + Flags(U8) + Unused(3)
        trdt = struct.pack('<II4sB3sIB3s',
                           resp['emotion_type'],
                           resp['emotion_value'],
                           b'\x00' * 4,
                           resp['response_number'],
                           b'\x00' * 3,
                           0,        # sound FormID
                           0,        # flags
                           b'\x00' * 3)
        subs += pack_subrecord('TRDT', trdt)
        # NAM1 = Response text
        subs += pack_string_subrecord('NAM1', resp.get('text', ''))
        # NAM2 = Script notes (actor notes)
        subs += pack_string_subrecord('NAM2', resp.get('notes', ''))
        # NAM3 = Edits (empty — required field in TRDT group)
        subs += pack_string_subrecord('NAM3', '')

    # --- Build conditions ---
    ctda_list = []

    # A) Determine NPC FIDs this INFO targets (from GetIsID in TES4 conditions)
    info_npc_fids = _extract_npc_fids_from_conditions(
        info_data['conditions_raw'], formid_offset)

    # B) Inject GetIsID conditions for NPC-specific dialog only.
    # Do NOT inject GetIsVoiceType — vanilla Skyrim generic/greeting INFOs have
    # no voice type conditions; the engine routes audio via the NPC's VTCK.
    # Adding GetIsVoiceType with plugin-local VTYPs blocks all dialog evaluation.
    is_conversation = dial_type in (_TYPE_TOPIC, _TYPE_CONVERSATION)
    if is_conversation and info_npc_fids:
        # This INFO explicitly targets specific NPCs — inject GetIsID for them.
        npc_list = sorted(info_npc_fids)
        for i, npc_fid in enumerate(npc_list):
            is_last = (i == len(npc_list) - 1)
            is_or = not is_last if len(npc_list) > 1 else False
            ctda_list.append(_build_getisid_ctda(npc_fid, is_or=is_or))
    elif is_conversation and not info_npc_fids:
        # Generic INFO — only inject sibling-donor GetIsID if the topic
        # has a SMALL, well-defined NPC set.  Large topics (GREETING etc.)
        # are intentionally open and must NOT get sibling injection.
        topic_npcs = topic_npc_fids.get(dial_raw, set())
        if 0 < len(topic_npcs) <= _MAX_TOPIC_NPC_INJECT:
            npc_list = sorted(topic_npcs)
            for i, npc_fid in enumerate(npc_list):
                is_last = (i == len(npc_list) - 1)
                is_or = not is_last if len(npc_list) > 1 else False
                ctda_list.append(_build_getisid_ctda(npc_fid, is_or=is_or))

    # C) Convert TES4 conditions (excluding GetIsID which we already handled,
    #    and excluding dropped functions)
    for raw in info_data['conditions_raw']:
        if len(raw) < 24:
            continue
        func_idx = struct.unpack_from('<H', raw, 8)[0]
        if func_idx == FUNC_GETISID:
            continue  # Already handled above
        if func_idx in _DROPPED_FUNCS:
            continue
        ctda32 = _convert_ctda_24_to_32(raw, formid_offset)
        if ctda32:
            ctda_list.append(ctda32)

    # Write all conditions
    for ctda in ctda_list:
        subs += pack_subrecord('CTDA', ctda)

    return pack_record('INFO', info_fid, 0, subs)
