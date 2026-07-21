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

from .text_reader import get_formid_index_offset, remap_formid
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
    FUNC_GET_IN_FACTION,
    FUNC_GET_IS_ID,
    FUNC_GET_IS_VOICE_TYPE,
    FUNC_GET_QUEST_RUNNING,
    build_ctda,
    build_or_chain,
    convert_ctda_list_with_strings,
    has_any_conditions,
    has_audience_condition,
    read_func_param_fids,
    read_getisid_fids,
)

_PLAYER_FORMID = 0x14

# Remapped FormIDs of TES4 topics that have NO INFOs at all (Oblivion.esm
# ships ~850 such placeholder shells). Oblivion never displays a topic without
# a valid INFO, so emitting them gives Skyrim dead DIALs that the CK reports
# as "Orphaned topic ... in quest TES4DialogueGeneric" (one warning each).
# Populated per build_dialog_groups run; convert_INFO drops choice links into
# the set so no TCLT dangles.
_EMPTY_DIAL_FIDS: set = set()

# (info_fid24, response_number) -> spoken text, collected alongside the
# voicemap so the audio pipeline can generate .lip files (LipGenerator needs
# the WAV *and* the transcript). Cleared per build_dialog_groups run; drained
# by import_main._write_lip_text via get_lip_texts().
_lip_texts: dict = {}


def get_lip_texts() -> dict:
    """Return the {(info_fid24, resp_num): text} map from the last
    build_dialog_groups run."""
    return _lip_texts

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
        # .strip() must match the PSC generator's filter exactly: E3 and
        # SEObelisks have a whitespace-only '\r\n' stage-100 result script,
        # which emitted a VMAD fragment entry with no matching .psc function.
        if log_count > 0:
            for j in range(log_count):
                if (get_str(rec, f'Stage[{i}].Log[{j}].Text') or
                        get_str(rec, f'Stage[{i}].Log[{j}].ResultScript').strip()):
                    frags.append((stage_idx, j))
        elif (get_str(rec, f'Stage[{i}].Text') or
              get_str(rec, f'Stage[{i}].ResultScript').strip()):
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


""" TES4 CTDA function indices used when resolving quest-target stage gates. """
_FUNC_GET_STAGE = 58
_FUNC_GET_STAGE_DONE = 59

# CTDA operator = the top 3 bits of the type byte.
_CTDA_OPS = {
    0x00: lambda a, b: a == b,
    0x20: lambda a, b: a != b,
    0x40: lambda a, b: a > b,
    0x60: lambda a, b: a >= b,
    0x80: lambda a, b: a < b,
    0xA0: lambda a, b: a <= b,
}


def _target_live_at_stage(raw_hexes: list, stage_idx: int) -> bool:
    """Would Oblivion have shown this quest target's marker at `stage_idx`?

    Oblivion gates each QSTA with conditions — overwhelmingly `GetStage <op> N`
    on the quest's own FormID, which is exactly "show this marker during this
    part of the quest". Skyrim has no equivalent (its objective targets are
    unconditional), so we resolve the gate here: evaluate the chain with
    GetStage == stage_idx and put the target only on the objectives where it
    holds.

    OR semantics follow the CTDA chain rule: bit 0 of the type byte ORs a
    condition with the NEXT one, so the chain is an AND of OR-groups. A
    condition we cannot evaluate (any function other than GetStage/GetStageDone
    — GetQuestVariable, GetDeadCount, …) is treated as PASSING: it is a runtime
    fact we cannot know, and dropping the target on a maybe would lose a marker
    Oblivion did show. A target with no conditions at all is always live.
    """
    if not raw_hexes:
        return True

    groups = []          # list of OR-groups; each group is a list of bools
    current = []
    for raw_hex in raw_hexes:
        try:
            raw = bytes.fromhex(raw_hex)
        except ValueError:
            continue
        if len(raw) < 20:
            continue
        type_byte = raw[0]
        comp = struct.unpack_from('<f', raw, 4)[0]
        func = struct.unpack_from('<H', raw, 8)[0]

        if func == _FUNC_GET_STAGE:
            op = _CTDA_OPS.get(type_byte & 0xE0)
            value = bool(op(float(stage_idx), comp)) if op else True
        elif func == _FUNC_GET_STAGE_DONE:
            # GetStageDone(quest, N): stage N has been completed. Approximate
            # with "we are at or past N" — the only monotonic reading available
            # from static data.
            target_stage = struct.unpack_from('<I', raw, 16)[0]
            done = stage_idx >= target_stage
            op = _CTDA_OPS.get(type_byte & 0xE0)
            value = bool(op(1.0 if done else 0.0, comp)) if op else True
        else:
            value = True                      # not statically knowable -> pass

        current.append(value)
        if not (type_byte & 0x01):            # no OR -> this group ends here
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    return all(any(g) for g in groups)


# TES4 CTDA functions that express quest TIMING (when a line is live). These are
# the conditions a choice-reached response topic must inherit from the greeting
# that reveals it, so a promoted top-level topic doesn't appear before its time.
# 56 GetQuestRunning, 58 GetStage, 59 GetStageDone, 99 GetQuestCompleted.
_QUEST_STATE_FUNCS = frozenset({56, 58, 59, 99})

# Additional PLAYER-progress gates a revealing greeting can carry, inherited by
# its choice targets alongside the quest-state ones. Oblivion questlines often
# track progress as the player's rank in a quest faction rather than a stage —
# Agronak's challenge greetings are gated GetFactionRank(ArenaCombatants)==7
# ON TARGET (the player), and without inheriting that the promoted "Yes, I wish
# to challenge you" topic sits in his menu from the first conversation. Only
# the run-on-target form is a progress gate (the subject form describes the
# SPEAKER, and the target topic already carries its own audience conditions).
# 71 GetInFaction, 73 GetFactionRank.
_PLAYER_PROGRESS_FUNCS = frozenset({71, 73})

# Legacy variable reads (53 GetScriptVariable / 79 GetQuestVariable) are ALSO
# timing gates ("set Arena.ChallengeAgronak to 1" both advances state and
# retires the greeting); they inherit as translated GetVMScriptVariable/
# GetVMQuestVariable conditions with their CIS2 variable name riding along.
_VAR_STATE_FUNCS = frozenset({53, 79})


def _has_quest_state_condition(rec: dict) -> bool:
    """True if `rec` has any quest-TIMING condition of its own (GetStage etc.)."""
    i = 0
    while True:
        raw_hex = rec.get(f'Condition[{i}].Raw')
        if raw_hex is None:
            return False
        i += 1
        if not raw_hex:
            continue
        try:
            raw = bytes.fromhex(raw_hex)
        except ValueError:
            continue
        if len(raw) >= 10 and struct.unpack_from('<H', raw, 8)[0] in \
                _QUEST_STATE_FUNCS:
            return True


def _quest_state_ctdas(rec: dict, offset: int, script_vars: dict = None) -> list:
    """Converted [(32-byte CTDA, cis2-or-None)] pairs for just the TIMING
    conditions on `rec` — the gates a promoted choice target must inherit.

    Reads Condition[i].Raw, keeps only:
      * quest-state functions (GetStage etc.),
      * run-on-target GetInFaction/GetFactionRank (player questline progress —
        Oblivion's faction-rank-as-stage idiom),
      * legacy variable reads, translated to GetVMScriptVariable/
        GetVMQuestVariable with their CIS2 variable name,
    converts + remaps them, and clears any dangling OR flag so the returned
    list is a standalone AND-group. Identity/voice conditions are deliberately
    excluded — the response topic already carries its own GetIsID; only the
    missing TIMING gate is inherited. Returns [] when the revealer has no
    timing conditions (e.g. an always-available greeting)."""
    from .dialog_conditions import (CTDA_OR, CTDA_RUN_ON_TARGET, convert_ctda,
                                    _convert_script_var_ctda)
    out = []
    i = 0
    while True:
        raw_hex = rec.get(f'Condition[{i}].Raw')
        if raw_hex is None:
            break
        i += 1
        if not raw_hex:
            continue
        try:
            raw = bytes.fromhex(raw_hex)
        except ValueError:
            continue
        if len(raw) < 10:
            continue
        func = struct.unpack_from('<H', raw, 8)[0]
        if func in _VAR_STATE_FUNCS:
            pair = _convert_script_var_ctda(raw, script_vars or {}, offset)
            if pair is not None:
                out.append(pair)
            continue
        if func not in _QUEST_STATE_FUNCS and not (
                func in _PLAYER_PROGRESS_FUNCS
                and raw[0] & CTDA_RUN_ON_TARGET):
            continue
        try:
            ctda = convert_ctda(raw, offset)
        except (ValueError, struct.error):
            continue
        if ctda is not None:
            out.append((ctda, None))
    # A trailing OR flag with nothing after it is invalid — clear it.
    if out and (out[-1][0][0] & CTDA_OR):
        out[-1] = (bytes([out[-1][0][0] & ~CTDA_OR]) + out[-1][0][1:],
                   out[-1][1])
    return out


def _pack_gate_pair(pair) -> bytes:
    """One inherited (CTDA, cis2) gate condition as packed subrecords."""
    ctda, cis2 = pair
    out = pack_subrecord('CTDA', ctda)
    if cis2:
        out += pack_string_subrecord('CIS2', cis2)
    return out


def _bark_choice_gate_bytes(revealer_gates: list) -> bytes:
    """Combine per-revealer timing gates into one CTDA block for the
    response topic's INFOs.

    revealer_gates is a list (one entry per greeting that reveals this topic)
    of lists of (converted CTDA bytes, cis2-or-None) pairs (that greeting's
    timing AND-group). Semantics: the response is available if ANY revealer is
    live (OR across revealers), and a revealer is live when ALL its conditions
    hold (AND within).

      * ANY revealer with an EMPTY gate → the response is always reachable from
        that greeting → no gate at all (return b'').
      * One revealer → emit its AND-group verbatim (covers stage-range gates
        like `GetStage>=30 AND GetStage<120`).
      * Several revealers each with exactly ONE condition → OR-chain them
        (bit 0 set on all but the last).
      * Several revealers where some carry an AND-group → a flat CTDA list
        can't express OR-of-ANDs, so use the FIRST revealer's group (the
        primary reveal path). Losing the gate entirely would let the topic leak
        into the menu, which is the bug we're fixing; a slightly-off timing on
        these ~31 multi-path topics is the lesser evil.
    """
    from .dialog_conditions import CTDA_OR
    if not revealer_gates:
        return b''
    if any(len(g) == 0 for g in revealer_gates):
        return b''                      # an always-available reveal path exists
    if len(revealer_gates) == 1:
        return b''.join(_pack_gate_pair(p) for p in revealer_gates[0])
    if all(len(g) == 1 for g in revealer_gates):
        # OR-chain of one condition per revealer.
        out = b''
        n = len(revealer_gates)
        for idx, g in enumerate(revealer_gates):
            c, cis2 = g[0]
            is_last = (idx == n - 1)
            tb = c[0] | CTDA_OR if not is_last else c[0] & ~CTDA_OR
            out += _pack_gate_pair((bytes([tb]) + c[1:], cis2))
        return out
    # Mixed AND-groups across revealers — use the first revealer's group.
    return b''.join(_pack_gate_pair(p) for p in revealer_gates[0])


# FormID -> effective priority (U8, 0-255), from the most recent
# compute_quest_priorities() call. _quest_dnam reads this so the WRITTEN
# QUST.DNAM.Priority reflects the zero-stage boost — the byte the engine
# actually arbitrates dialogue on, not a value only used to compute a derived
# DIAL PNAM (that alone left the QUST record's own priority unchanged, so the
# fix had no effect in-game: MG00General kept DNAM Priority=61 even after its
# bark topic's PNAM dropped to -1).
_QUEST_PRIORITY_OVERRIDE: dict = {}


def compute_quest_priorities(by_type: dict) -> dict:
    """FormID -> effective dialogue-arbitration priority for every QUST.

    Oblivion picks the first passing INFO in QUEST PRIORITY order (highest
    first), NOT file order — Azzan's low-priority(11) first-meeting intro
    would otherwise outrank the priority-60 Fighters Guild ad greeting that
    reveals the join topics. Skyrim arbitrates dialogue by the QUEST's own
    priority (QUST.DNAM.Priority), so the raw TES4 DATA.Priority value is
    carried over for ordinary (staged) quests — EXCEPT that every STAGED
    quest is boosted by a fixed offset so it universally outranks every
    zero-stage "conversation container" quest (MG00General, MQConversations,
    FGConversations, DarkConvSystem, ...), which exist purely to hold
    ambient/HELLO-channel chatter. In Oblivion GREETING and HELLO are
    separate channels, so a container quest's authored priority (sometimes
    deliberately HIGH, e.g. MG00General=61, to win its own HELLO-channel
    arbitration) never competed with a real quest's GREETING. Skyrim merges
    both into one HELO topic per quest, so MG00General outranked
    MG04Restore's priority-60 briefing outright: "Arielle only gives a
    generic greeting" with the journal at the correct stage and every record
    field individually correct. A quest with real stages represents actual
    narrative progress the player is mid-story with, so it must always beat
    a stage-less container quest's greeting.

    A fixed UPWARD shift on staged quests, not a downward clamp on zero-stage
    ones: DNAM.Priority is an unsigned byte (0-255), so a container quest at
    priority 0 already has no headroom to be pushed lower. Shifting every
    staged quest up by (max zero-stage priority - min staged priority + 1)
    guarantees the ENTIRE staged range sits above the ENTIRE zero-stage range
    while preserving both groups' internal relative order exactly (a uniform
    shift, not a rank remap) — 125 vanilla zero-stage quests span their own
    priority range (Dark00General=50, MQConversations=85, ...) that still
    governs arbitration AMONG container quests (two factions' idle chatter
    competing for the same generic NPC), and real quests already top out at
    90 in vanilla data, so the shifted range comfortably fits in a U8.

    Both convert_QUST (the WRITTEN QUST.DNAM.Priority byte) and
    build_dialog_groups (the DIAL PNAM assigned to merged bark topics) must
    read from THIS SAME table — the engine's real arbitration reads the
    quest's own priority, so writing an unrelated derived value into DIAL
    PNAM alone changes nothing in-game.
    """
    quest_priority = {get_formid(r, 'FormID'): get_int(r, 'DATA.Priority')
                      for r in by_type.get('QUST', [])
                      if get_formid(r, 'FormID')}
    staged_quest_fids = {get_formid(r, 'FormID') for r in by_type.get('QUST', [])
                        if get_formid(r, 'FormID') and get_int(r, 'StageCount')}
    zero_stage_fids = [f for f in quest_priority if f not in staged_quest_fids]
    if staged_quest_fids and zero_stage_fids:
        min_staged = min(quest_priority[f] for f in staged_quest_fids)
        max_zero_stage = max(quest_priority[f] for f in zero_stage_fids)
        if max_zero_stage >= min_staged:
            offset = max_zero_stage - min_staged + 1
            for fid in staged_quest_fids:
                quest_priority[fid] = min(255, quest_priority[fid] + offset)
    _QUEST_PRIORITY_OVERRIDE.clear()
    _QUEST_PRIORITY_OVERRIDE.update(quest_priority)
    return quest_priority


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

    Priority is the EFFECTIVE value from compute_quest_priorities() (staged
    quests shifted above every zero-stage quest) when available, falling
    back to the raw TES4 value for quests that table doesn't know about
    (e.g. unit tests that convert a QUST record in isolation) — see that
    function's docstring for why the raw DATA.Priority alone is not what the
    engine arbitrates dialogue on.
    """
    tes4_flags = get_int(rec, 'DATA.Flags')
    fid = get_formid(rec, 'FormID')
    priority = _QUEST_PRIORITY_OVERRIDE.get(fid, get_int(rec, 'DATA.Priority'))
    priority = max(0, min(255, priority))
    flags = tes4_flags & 0x09          # StartGameEnabled | AllowRepeatedStages
    if flags & 0x01:
        flags |= 0x10                  # StartsEnabled
    qtype = 8 if _quest_has_journal(rec) else 0
    return struct.pack('<HBBII', flags, priority, 0, 0, qtype)


def convert_QUST(rec: dict, fid_to_edid: dict = None,
                 well_known_props: dict = None,
                 unlock_plan: dict = None,
                 unlock_globals: dict = None,
                 pack_plan=None) -> bytes:
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

    # VMAD — quest stage script fragments (Papyrus) plus the converted TES4
    # quest script (SCRI), when either exists. The fragment list must match
    # the generated PSC exactly.
    stage_frags = _quest_stage_fragments(rec)
    from .object_scripts import get_quest_script
    attached = get_quest_script(get_formid(rec, 'FormID'))
    if (stage_frags or attached) and edid:
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
            edid, stage_frags, property_values=prop_vals or None,
            attached_script=attached))

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
    # Oblivion QSTA is QUEST-level: one entry per (target ref, condition set),
    # where the conditions are GetStage bounds saying WHEN that target's compass
    # marker is live. Skyrim QSTA is per-OBJECTIVE and vanilla leaves it
    # UNCONDITIONAL — the objective being Displayed is what selects the marker
    # (checked across Skyrim.esm: objectives read `QOBJ FNAM NNAM QSTA [QSTA…]`
    # with CTDAs the rare exception, and the right target simply sits on the
    # right objective).
    #
    # So the faithful mapping is to RESOLVE Oblivion's GetStage gates at build
    # time rather than replay them at runtime: for each objective (= stage), emit
    # only the targets whose TES4 conditions hold AT THAT STAGE, with no CTDAs.
    # Carrying every target on every objective (the previous design) makes the
    # engine face a list whose leading entries are false and it renders no marker
    # at all — objective shows in the journal, compass/map stay empty.
    alias_by_fid = {}
    targets = []          # (alias_id, tes4_flags_low_byte, [raw TES4 ctda hex])
    t = 0
    while f'Target[{t}].FormID' in rec:
        tfid = get_formid(rec, f'Target[{t}].FormID')
        if tfid:
            alias_id = alias_by_fid.setdefault(tfid, len(alias_by_fid))
            tflags = get_int(rec, f'Target[{t}].Flags') & 0x01
            raws = []
            k = 0
            while True:
                raw = rec.get(f'Target[{t}].Condition[{k}].Raw')
                if raw is None:
                    break
                raws.append(raw)
                k += 1
            targets.append((alias_id, tflags, raws))
        t += 1

    # Objectives — one per stage with journal text (objective index = stage
    # index, which is what the generated stage fragments display).
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

        live = [(a, f) for a, f, raws in targets
                if _target_live_at_stage(raws, stage_idx)]
        # An objective with no live target keeps its journal text but marks
        # nothing — same as vanilla's marker-less objectives ("Return when
        # you're ready"). If Oblivion gated every target away at this stage,
        # honour that rather than inventing a marker.
        emitted = set()
        for alias_id, tflags in live:
            if alias_id in emitted:
                continue          # same ref gated by several stage windows
            emitted.add(alias_id)
            subs += pack_subrecord('QSTA', struct.pack('<iB3x',
                                                       alias_id, tflags))

    # --- Package aliases -------------------------------------------------
    # A Skyrim quest package must hang off a reference alias (ALPC): that is
    # what lets it outrank the actor's standing schedule, which is exactly what
    # Oblivion achieved by putting a conditioned package at the top of the
    # actor's AI list.  pack_plan (built in Phase 0) says which refs this quest's
    # packages name; aliases are allocated here, and PACK reads back the SAME
    # indices, so the two cannot drift.
    qfid = get_formid(rec, 'FormID')
    alias_packages = {}       # alias_id -> [pack_fid, ...]
    if pack_plan is not None:
        for ref_fid, alias_id in pack_plan.assign_aliases(qfid, alias_by_fid):
            pkgs = pack_plan.packages_for_alias(qfid, ref_fid)
            if pkgs:
                alias_packages[alias_id] = pkgs
        # A ref that was already a quest target can also run packages.
        for ref_fid, alias_id in alias_by_fid.items():
            pkgs = pack_plan.packages_for_alias(qfid, ref_fid)
            if pkgs and alias_id not in alias_packages:
                alias_packages[alias_id] = pkgs

    subs += pack_uint32_subrecord('ANAM', len(alias_by_fid))  # Next Alias ID

    # Reference aliases (forced ref).  Layout and flag value both follow vanilla:
    # ALST, ALID, FNAM, ALFR, [ALPC...], VTCK, ALED.  **VTCK is present on
    # 2687/2687 vanilla forced-ref aliases — a 100% invariant** (empty = "no
    # voice-type override"), and every one of the 255 vanilla objective+forced-ref
    # quests carries it.
    # Flags 0x0292 = Optional (0x0002 — a fill failure must not block quest start,
    # or the dialogue dies with it) + Allow Dead (0x0010) + Allow Disabled
    # (0x0080) + Allow Reserved (0x0200); an attested vanilla combination.  The
    # old 0x109A added Allow Reuse/Allow Destroyed and appears nowhere in vanilla.
    for tfid, alias_id in sorted(alias_by_fid.items(), key=lambda kv: kv[1]):
        subs += pack_uint32_subrecord('ALST', alias_id)
        subs += pack_string_subrecord('ALID', _alias_name(tfid, alias_id,
                                                          fid_to_edid))
        subs += pack_uint32_subrecord('FNAM', 0x00000292)
        subs += pack_formid_subrecord('ALFR', tfid)
        for pfid in alias_packages.get(alias_id, ()):
            subs += pack_formid_subrecord('ALPC', pfid)
        subs += pack_formid_subrecord('VTCK', 0)
        subs += pack_subrecord('ALED', b'')

    return pack_record('QUST', qfid, get_int(rec, 'RecordFlags'), subs)


def _alias_name(ref_fid: int, alias_id: int, fid_to_edid: dict) -> str:
    """Stable, readable alias name.

    Papyrus property bindings and ALPC links resolve by index, but a name that
    tracks the reference makes the output legible in the CK/SSEEdit.  The player
    alias is named 'Player' because that is what every vanilla quest calls it.
    """
    if ref_fid == 0x00000014:
        return 'Player'
    edid = (fid_to_edid or {}).get(ref_fid, '')
    if edid:
        return edid[:32]
    return f'TES4Target{alias_id:02d}'


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
    # InfoRefusal (DATA.Type 6 Misc, not a Type-3 persuasion topic so not caught
    # by _SKIP_TYPES) is the persuasion/disposition refusal line ("That's
    # privileged information. I'm sorry."). Skyrim has no persuasion mechanic to
    # trigger it, and it is conditionless under the always-running Generic quest,
    # so as an IDLE bark it fired as EVERY NPC's walk-past line. No equivalent.
    'InfoRefusal',
})

# Oblivion Service-type topics that become real Skyrim service dialogue.
# 'Barter'/'Training' hold the voiced lines NPCs speak as those menus open in
# Oblivion; they convert to player-selectable Custom topics whose INFOs open
# the corresponding Skyrim menu via a Papyrus fragment (ShowBarterMenu /
# ShowTrainingMenu). Every other Service topic (BarterExit, ServiceRefusal,
# Repair, Recharge, Travel, ...) has no Skyrim mechanic and stays skipped.
# Maps EditorID -> (service kind, player prompt used as the DIAL FULL).
SERVICE_MENU_TOPICS = {
    'Barter':   ('barter', 'What have you got for sale?'),
    'Training': ('training', 'I would like some training.'),
}


def service_menu_kind(rec: dict) -> str:
    """'barter' / 'training' for the two convertible Service topics, else ''."""
    if get_int(rec, 'DATA.Type') != DIAL_TYPE_SERVICE:
        return ''
    info = SERVICE_MENU_TOPICS.get(get_str(rec, 'EditorID', ''))
    return info[0] if info else ''


def should_skip_dial(rec: dict) -> bool:
    dtype = get_int(rec, 'DATA.Type')
    if dtype in _SKIP_TYPES and not service_menu_kind(rec):
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
                 snam: bytes, priority: float = 50.0,
                 edid_override: str = None, formid_override: int = None) -> bytes:
    """DIAL — Dialog Topic. Order: EDID FULL PNAM [BNAM] QNAM DATA SNAM TIFC.

    quest_fid is the REMAPPED owning quest (original QSTI quest, or the synthetic
    generic dialogue quest for orphan/bark topics). edid_override/formid_override
    let the per-quest bark split emit multiple DIALs from one source record with
    unique EditorIDs and FormIDs.
    """
    subs = b''
    edid = edid_override if edid_override is not None else get_str(rec, 'EditorID')
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
    formid = (formid_override if formid_override is not None
              else get_formid(rec, 'FormID'))
    return pack_record('DIAL', formid, get_int(rec, 'RecordFlags'), subs)


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
    from script_convert.constants import resolve_property_formid
    props = {}
    for prop_edid in conv._property_refs:
        low = prop_edid.lower()
        if low in ('player', 'playerref'):
            props[prop_edid] = _PLAYER_FORMID
            continue
        fid_hex = resolve_property_formid(xref, prop_edid)
        if not fid_hex:
            continue
        try:
            raw_fid = int(fid_hex, 16)
        except (ValueError, TypeError):
            continue
        if raw_fid == 0:
            continue
        props[prop_edid] = remap_formid(raw_fid, offset)
    return props


# Shared static fragment scripts (script_convert/static_scripts) attached to
# service-menu INFOs that have no result script of their own. INFOs that DO
# have one keep their per-INFO TES4_TIF__ fragment — script_convert appends
# the menu call there instead.
SERVICE_MENU_SCRIPTS = {
    'barter': 'TES4_ShowBarterMenu',
    'training': 'TES4_ShowTrainingMenu',
}


def convert_INFO(rec: dict, *, injected_ctdas: bytes = b'',
                 fid_to_edid: dict = None, well_known_props: dict = None,
                 xref=None, reveal_props: dict = None,
                 service_menu: str = '', bark_dial_fids: set = None,
                 script_vars: dict = None) -> bytes:
    """INFO — Dialog response.

    Order: EDID [VMAD] ENAM CNAM [TCLT...] [TRDT NAM1 NAM2 NAM3]* CTDAs.
    injected_ctdas are the Skyrim-required gates (voice type / identity /
    unlock / quest-running), already packed and placed BEFORE the translated
    TES4 conditions so their OR chains stay isolated. reveal_props
    ({global_name: GLOB formid}) marks this INFO as an AddTopic revealer — it
    gets an OnEnd fragment (generated by script_convert) that sets those
    unlock globals, so a VMAD is emitted even without a result script.
    service_menu ('barter'/'training') attaches the fragment that opens the
    Skyrim barter/training menu when the line finishes.

    bark_dial_fids (non-None only for bark INFOs) is the set of all bark DIAL
    FormIDs (remapped). A bark INFO drops any choice that targets ANOTHER bark
    (those get split/merged in the bark pass, so the link would dangle), but
    KEEPS choices that target a conversation (CUST) topic — that is the vanilla
    "NPC greets you, then you pick a response" pattern (Skyrim HELO→CUST TCLT,
    e.g. C03SkorQuestStartBranchTopic). Dropping those left greetings with a
    line but no selectable response (FGC01Rats: Arvena asks what happened but
    the player can't answer).
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    # VMAD — result script -> Papyrus fragment (skip comment-only scripts),
    # and/or AddTopic unlock-global assignments, and/or the service-menu call.
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
    elif service_menu:
        from script_convert.pipeline import build_vmad_info_fragment
        subs += pack_subrecord('VMAD', build_vmad_info_fragment(
            info_fid, script_name=SERVICE_MENU_SCRIPTS[service_menu]))

    # ENAM (Flags U16 + Reset Hours U16)
    tes4_flags = get_int(rec, 'DATA.Flags')
    subs += pack_subrecord('ENAM', struct.pack('<HH',
                                               tes4_flags & _ENAM_COMPATIBLE_MASK, 0))

    # CNAM — favor level (None)
    subs += pack_subrecord('CNAM', struct.pack('<B', 0))

    # TCLT — choices (follow-up topic links). A bark INFO keeps only choices
    # that point at a CONVERSATION topic (the vanilla greeting→CUST-response
    # pattern); a choice that points at another bark is dropped, because barks
    # are split/merged by (quest, subtype) in the bark pass and the link would
    # dangle at a sub-topic that no longer exists under that FormID. Choices
    # into a zero-INFO topic are dropped too — those topics are never emitted
    # (see _EMPTY_DIAL_FIDS) and Oblivion never showed them either.
    def _keep_choice(cfid: int) -> bool:
        if not cfid:
            return False
        if bark_dial_fids is not None and cfid in bark_dial_fids:
            return False
        if cfid in _EMPTY_DIAL_FIDS:
            return False
        return True

    choice_count = get_int(rec, 'ChoiceCount')
    if choice_count > 0:
        for i in range(choice_count):
            cfid = get_formid(rec, f'Choice[{i}]')
            if _keep_choice(cfid):
                subs += pack_formid_subrecord('TCLT', cfid)
    else:
        cfid = get_formid(rec, 'TCLT.Choice')
        if _keep_choice(cfid):
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
    # The strings variant translates legacy GetScriptVariable/GetQuestVariable
    # reads into GetVMScriptVariable/GetVMQuestVariable, whose variable NAME
    # travels in a CIS2 subrecord right after the CTDA (`::WearingArmor_var`)
    # — this is what makes script-variable-gated dialogue (Owyn's raiment
    # check, the Arena match state machine) actually evaluate in Skyrim.
    subs += injected_ctdas
    # Say-driven topic? RunOn=Target conditions must be retargeted (or
    # dropped) — Actor.Say() has no dialogue target to evaluate them against.
    say_disp = _SAY_TOPIC_DISPOSITIONS.get(
        get_formid(rec, 'ParentDIAL') & 0xFFFFFF)
    say_ref = say_disp[1] if say_disp and say_disp[0] == 'ref' else None
    say_drop = bool(say_disp) and say_disp[0] == 'drop'
    for ctda, cis2 in convert_ctda_list_with_strings(
            rec, script_vars,
            run_on_target_ref=say_ref, drop_run_on_target=say_drop):
        subs += pack_subrecord('CTDA', ctda)
        if cis2:
            subs += pack_string_subrecord('CIS2', cis2)

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


# Say/SayTo/StartConversation-driven topics: raw24 DIAL fid ->
#   ('ref', final_fid)  retarget RunOn=Target conditions to that reference
#   ('drop', None)      drop RunOn=Target conditions (mixed/unknown targets)
# Populated by build_dialog_groups (same lifecycle as _EMPTY_DIAL_FIDS).
_SAY_TOPIC_DISPOSITIONS: dict = {}

_SAYTO_RE = re.compile(r'\bsayto[\s,]+(\w+)[\s,]+(\w+)', re.IGNORECASE)
_SAY_RE = re.compile(r'\bsay[\s,]+(\w+)', re.IGNORECASE)
_STARTCONV_RE = re.compile(r'\bstartconversation[\s,]+(\w+)(?:[\s,]+(\w+))?',
                           re.IGNORECASE)


def build_say_topic_dispositions(by_type: dict, remap) -> dict:
    """Map script-driven (Say/SayTo/StartConversation) topics to how their
    RunOn=Target conditions must be converted.

    Skyrim's Actor.Say() has no dialogue target, so a converted RunOn=Target
    condition in a Say-driven topic evaluates against nothing and can never
    pass — CharacterGen's Valen Dreth taunts (race-of-target picks the line)
    froze the whole intro this way, and 1,900+ INFOs across every scripted
    conversation share the defect.  The script call sites tell us who the
    target actually is: when it's unique (usually the player), the condition
    is retargeted to RunOn=Reference on that ref — equivalent semantics, and
    equally valid if the topic is also reachable as menu dialogue (there the
    target IS the player).  Topics with mixed/unresolvable targets drop their
    target conditions instead: the Oblivion call sites already select
    speaker+topic, so auto-pass is closer to intent than never-pass.
    """
    # Scripted call sites live in SCPT bodies and INFO/QUST result scripts.
    texts = [get_str(r, 'SCTX') or '' for r in by_type.get('SCPT', [])]
    for r in by_type.get('INFO', []):
        texts.append(get_str(r, 'ResultScript') or '')
    for r in by_type.get('QUST', []):
        i = 0
        while f'Stage[{i}].Index' in r:
            j = 0
            while (t := r.get(f'Stage[{i}].Log[{j}].ResultScript')) is not None:
                texts.append(t)
                j += 1
            i += 1

    dial_by_edid = {get_str(d, 'EditorID', '').lower():
                    get_formid(d, 'FormID') & 0xFFFFFF
                    for d in by_type.get('DIAL', [])
                    if get_str(d, 'EditorID')}
    ref_by_edid = {}
    for sig in ('ACHR', 'ACRE', 'REFR'):
        for r in by_type.get(sig, []):
            e = get_str(r, 'EditorID')
            if e:
                ref_by_edid[e.lower()] = remap(get_formid(r, 'FormID'))

    def target_fid(token: str):
        t = token.lower()
        if t in ('player', 'playerref'):
            return _PLAYER_FORMID
        return ref_by_edid.get(t)      # None when unresolvable

    votes = defaultdict(set)           # raw24 dial fid -> {fid or None}
    for text in texts:
        if not text:
            continue
        for line in text.replace('\\r\\n', '\n').splitlines():
            line = line.split(';', 1)[0]
            low = line.lower()
            if 'say' not in low and 'startconversation' not in low:
                continue
            for m in _SAYTO_RE.finditer(line):
                d = dial_by_edid.get(m.group(2).lower())
                if d is not None:
                    votes[d].add(target_fid(m.group(1)))
            for m in _STARTCONV_RE.finditer(line):
                if m.group(2):
                    d = dial_by_edid.get(m.group(2).lower())
                    if d is not None:
                        votes[d].add(target_fid(m.group(1)))
            # plain Say has no target at all; \b keeps this from eating SayTo
            stripped = _SAYTO_RE.sub(' ', line)
            for m in _SAY_RE.finditer(stripped):
                d = dial_by_edid.get(m.group(1).lower())
                if d is not None:
                    votes[d].add(None)

    out = {}
    for dfid, tgts in votes.items():
        real = {t for t in tgts if t is not None}
        if len(real) == 1:
            out[dfid] = ('ref', next(iter(real)))
        else:
            out[dfid] = ('drop', None)
    return out


def build_npc_to_vtyp_map(by_type: dict, num_new_masters: int) -> dict:
    """NPC/CREA FormID (remapped) -> VTYP FormID, from the VOICE the NPC
    actually used in Oblivion.

    Oblivion resolves an NPC's voice folder through its RACE record's VNAM
    (per-gender voice-race override), NOT the literal race: Khajiit->Argonian,
    WoodElf/DarkElf->HighElf, Orc->Nord, Breton females->Imperial. The BSA has
    NO recordings under khajiit/orc/wood elf/dark elf at all — assigning the
    literal race gave those NPCs a VTYP whose voice folder is empty, so every
    line was silent. Follow the same VNAM chain the engine uses so the
    assigned VTYP is the folder the recordings really live in.
    """
    from .skyrim_overrides import TES4_RACE_FID_TO_EDID, VOICE_TYPE_MAP
    # RACE fid24 -> per-gender voice race fid24 (0/missing = the race itself).
    race_voice = {}
    for rr in by_type.get('RACE', []):
        rfid = get_formid(rr, 'FormID') & 0x00FFFFFF
        if not rfid:
            continue
        m = get_formid(rr, 'VNAM.MaleVoice') & 0x00FFFFFF
        f = get_formid(rr, 'VNAM.FemaleVoice') & 0x00FFFFFF
        race_voice[rfid] = {'Male': m or rfid, 'Female': f or rfid}

    npc_to_vtyp = {}
    offset = num_new_masters
    for sig in ('NPC_', 'CREA'):
        for rec in by_type.get(sig, []):
            # Shift exactly as the record converters do, for any source index —
            # an override keeps its master's index and must still land on the
            # same key the converter stamps.
            remapped = remap_formid(int(rec.get('FormID', '0'), 16), offset)
            gender = 'Female' if (get_int(rec, 'ACBS.Flags') & 1) else 'Male'
            race_fid = get_formid(rec, 'RNAM.Race') & 0x00FFFFFF
            voice_race_fid = race_voice.get(race_fid, {}).get(gender, race_fid)
            race_edid = TES4_RACE_FID_TO_EDID.get(voice_race_fid, 'Imperial')
            vtyp = (VOICE_TYPE_MAP.get((race_edid, gender))
                    or VOICE_TYPE_MAP.get(('Imperial', gender), 0))
            if vtyp:
                npc_to_vtyp[remapped] = vtyp
    return npc_to_vtyp


# ===========================================================================
# Main dialogue group builder
# ===========================================================================

def _make_generic_quest(writer, edid: str, full: str) -> int:
    """Create a StartGameEnabled synthetic dialogue quest; return its FormID.

    Owns orphan/generic bark topics. Flags 0x0011 (StartGameEnabled +
    StartsEnabled), priority 0, form-version 0. Must be listed in the .seq file
    to actually run from a new game."""
    fid = writer.alloc_formid()
    q = pack_string_subrecord('EDID', edid)
    q += pack_string_subrecord('FULL', full)
    q += pack_subrecord('DNAM', struct.pack('<HBBII', 0x0011, 0, 0, 0, 0))
    q += pack_subrecord('NEXT', b'')
    q += pack_uint32_subrecord('ANAM', 0)
    writer.add_record('QUST', pack_record('QUST', fid, 0, q))
    return fid


def build_dialog_groups(by_type: dict, writer, npc_to_vtyp: dict,
                        fid_to_edid: dict = None, xref=None,
                        well_known_props: dict = None,
                        voice_map: dict = None,
                        unlock_plan: dict = None,
                        unlock_globals: dict = None,
                        script_vars: dict = None) -> set:
    """Build the DIAL/INFO/DLBR/DLVW hierarchy with original-quest ownership.

    Returns the set of quest FormIDs that must go in the .seq file (the
    synthetic generic dialogue quest; real SGE quests are added by the QUST
    pass in import_main).

    voice_map, when given, is filled with {info_fid_low24: voice filename
    prefix} so the audio pipeline can name extracted voice files the way the
    Skyrim engine will look them up (owning quest EDID + topic EDID).
    """

    _lip_texts.clear()
    dials = by_type.get('DIAL', [])
    infos = by_type.get('INFO', [])
    if not dials:
        return set()

    offset = get_formid_index_offset()

    def remap(fid: int) -> int:
        return remap_formid(fid, offset)

    # --- Synthetic generic dialogue quest (owns orphan conversation topics) ---
    generic_quest_fid = _make_generic_quest(writer, 'TES4DialogueGeneric',
                                            'TES4 Generic Dialogue')
    # Per-source-DIAL synthetic quests for the quest-less INFOs of bark topics.
    # Skyrim honors only one bark topic per subtype per quest, so GREETING and
    # HELLO (both HELO) cannot both dump their quest-less lines into one shared
    # generic quest — each bark DIAL with orphan lines gets its own quest.
    # Populated on demand by _build_bark_topics_per_quest; drained into SGE.
    bark_generic_quests = {}   # source DIAL EditorID -> synthetic quest FID

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

    # Quest-level dialogue conditions: in Oblivion a QUST's own CTDAs gate ALL
    # of that quest's dialogue (e.g. NQDBeggars = GetInFaction(Beggars), so its
    # conditionless beggar HELLO/GREETING lines only reach beggars). Skyrim has
    # no quest-level dialogue gate, so these must be injected into every INFO the
    # quest owns. Keyed by RAW quest FormID; converted (32-byte) CTDAs.
    quest_dialog_ctdas = {}
    for qr in by_type.get('QUST', []):
        qfid = get_formid(qr, 'FormID')
        if not qfid:
            continue
        pairs = convert_ctda_list_with_strings(qr, script_vars, offset)
        if pairs:
            quest_dialog_ctdas[qfid] = b''.join(
                pack_subrecord('CTDA', c)
                + (pack_string_subrecord('CIS2', s) if s else b'')
                for c, s in pairs)

    # VTYP FormID -> EditorID, so an NPC-specific line can record the folder its
    # speaker's voice type resolves to (voice files are relocated there).
    from .skyrim_overrides import CUSTOM_VTYP_EDIDS, VOICE_TYPE_MAP
    vtyp_edid_by_fid = {}
    for vt_edid, key in CUSTOM_VTYP_EDIDS.items():
        vt_fid = VOICE_TYPE_MAP.get(key)
        if vt_fid:
            vtyp_edid_by_fid[vt_fid] = vt_edid

    # TES4 quest priorities: Oblivion picks the first passing INFO in QUEST
    # PRIORITY order (highest first), NOT file order. Our flattened topics are
    # evaluated by Skyrim in physical INFO order, so the arbitration must be
    # baked in by sorting each topic's children by their own quest's priority
    # (stable — file order preserved within a quest). Without this, e.g.
    # Azzan's low-priority(11) first-meeting intro outranks the priority-60
    # Fighters Guild ad greeting that reveals the join topics.
    quest_priority = compute_quest_priorities(by_type)

    info_by_dial = defaultdict(list)
    for rec in infos:
        info_by_dial[get_formid(rec, 'ParentDIAL')].append(rec)

    # Zero-INFO placeholder topics are never emitted (nor linked to via TCLT):
    # a topic with no INFO can never be shown in Oblivion, and in Skyrim each
    # would be a dead DIAL the CK flags as an orphaned topic. Service-menu
    # topics are exempt — they synthesize a fallback INFO at build time.
    _EMPTY_DIAL_FIDS.clear()
    _EMPTY_DIAL_FIDS.update(
        get_formid(d, 'FormID') for d in dials
        if not info_by_dial.get(get_formid(d, 'FormID'))
        and not service_menu_kind(d))

    # Say-driven topics: how each one's RunOn=Target conditions convert.
    _SAY_TOPIC_DISPOSITIONS.clear()
    _SAY_TOPIC_DISPOSITIONS.update(build_say_topic_dispositions(by_type, remap))
    n_ref = sum(1 for v in _SAY_TOPIC_DISPOSITIONS.values() if v[0] == 'ref')
    print(f"    say-driven topics: {len(_SAY_TOPIC_DISPOSITIONS)} "
          f"({n_ref} retargeted to a unique ref, "
          f"{len(_SAY_TOPIC_DISPOSITIONS) - n_ref} drop target-conditions)")

    tclt_targets = collect_tclt_target_fids(by_type)
    # Remapped FormIDs of every bark DIAL (greetings + combat/detection/misc
    # barks). A bark INFO's choice that points into this set is dropped (the
    # target is split/merged by the bark pass); a choice pointing OUTSIDE it
    # (a conversation topic) is kept so greeting→response routing survives.
    bark_dial_fids = set()
    for d in dials:
        if should_skip_dial(d) or service_menu_kind(d):
            continue
        _c, _s, _snam, _is_bark = classify_topic(
            get_str(d, 'EditorID', ''), get_int(d, 'DATA.Type'))
        if _is_bark:
            bark_dial_fids.add(get_formid(d, 'FormID'))
    # Conversation topics reached by a BARK/greeting's choice. In vanilla Skyrim
    # these are TOP-LEVEL branches (DNAM=1): the greeting bark plays, then the
    # response appears as a menu topic (e.g. HELO→C03SkorQuestStartBranchTopic,
    # branch DNAM=1). So — unlike a choice target reached only from a
    # conversation topic (a mid-chain player line that must stay off the menu) —
    # a greeting-reached target must be top-level or the player has the line but
    # no way to pick it. FGC01Rats: Arvena's report-back greeting → FGC01Choice1.
    #
    # BUT this only holds when the revealing greeting is itself GATED. The
    # generic always-available HELLO/GREETING offers generic emotional-response
    # topics as choices (AnswerNegative/AnswerPositive/FollowupNegative/
    # SadGeneral etc.); those are mid-conversation replies in Oblivion, reached
    # only after picking the greeting's line. Promoting them to top-level makes
    # them PERMANENTLY visible in the NPC's topic menu (there is no timing gate
    # to hide them). So a target is promoted only when EVERY revealer carries a
    # real timing gate; a target revealed by any ungated bark stays a Normal
    # branch (reachable only via the in-conversation choice link), matching
    # Oblivion.
    bark_choice_targets = set()
    # In Oblivion a choice-reached response topic needs NO stage condition of its
    # own — it is only reachable while the revealing greeting is live, and the
    # greeting IS stage-gated (Arvena's "what did you find?" fires at
    # GetStage(FGC01Rats)==30). Once the response is promoted to a top-level
    # Skyrim topic it appears whenever ITS OWN INFO conditions pass — which are
    # just GetIsID(Arvena) — so it leaks in from the first conversation. Recover
    # the timing by inheriting the revealing greeting's QUEST-STATE conditions
    # (GetStage/GetStageDone/GetQuestRunning/GetQuestCompleted). Multiple
    # greetings may reveal the same response at different stages, so OR their
    # gates together (any live revealer makes the response available).
    # bark_choice_gate: target_dial_fid -> list[ list[converted CTDA] ] (one
    # inner list per revealer; ANY revealer's gate suffices).
    bark_choice_gate = defaultdict(list)
    conv_choice_targets = set()   # targets also offered by NON-bark choice links
    for info_rec in infos:
        is_bark_info = get_formid(info_rec, 'ParentDIAL') in bark_dial_fids
        targets_here = []
        for i in range(get_int(info_rec, 'ChoiceCount')):
            cfid = get_formid(info_rec, f'Choice[{i}]')
            if cfid and cfid not in bark_dial_fids:
                targets_here.append(cfid)
        cfid = get_formid(info_rec, 'TCLT.Choice')
        if cfid and cfid not in bark_dial_fids:
            targets_here.append(cfid)
        if not targets_here:
            continue
        if not is_bark_info:
            conv_choice_targets.update(targets_here)
            continue
        gate = _quest_state_ctdas(info_rec, offset, script_vars)
        for cfid in targets_here:
            bark_choice_gate[cfid].append(gate)  # gate may be [] (no timing)
    # Promote to top-level only when every revealer contributes a real timing
    # gate. If any revealer is ungated (e.g. the generic HELLO greeting), the
    # promoted topic would sit permanently in the menu — so leave it a Normal
    # branch instead (and drop the useless empty gate so nothing tries to gate
    # a topic we're no longer promoting).
    #
    # A target that is ALSO offered as a choice by a normal CONVERSATION line
    # must not be promoted-and-gated either: its conversation path is reachable
    # whenever the parent line is (no timing gate of its own), and stamping the
    # bark revealer's gate onto the INFO would dead-end that path. SE36: the
    # "tell me your story" choice is offered ungated from a conversation topic
    # AND from a GetStage==15 greeting; the inherited ==15 gate made the choice
    # unspeakable — and stage 15 is set BY that choice, so the quest froze. The
    # conversation link survives as TCLT, which is exactly Oblivion's shape.
    for cfid, gates in bark_choice_gate.items():
        if cfid in conv_choice_targets:
            continue
        if gates and all(len(g) > 0 for g in gates):
            bark_choice_targets.add(cfid)
    for cfid in list(bark_choice_gate):
        if cfid not in bark_choice_targets:
            del bark_choice_gate[cfid]
    unlock_plan = unlock_plan or {'gated': {}, 'info_reveals': {},
                                  'stage_reveals': {}}
    unlock_globals = unlock_globals or {}

    # Quest-level NPC sets (for fallback identity gating of conversation topics
    # whose own INFOs name no NPC but sibling topics in the same quest do).
    quest_npc_fids = defaultdict(set)
    for d in dials:
        # Service-menu topics' per-merchant GetIsIDs must not leak into their
        # quests' NPC sets (they'd widen identity gates on unrelated topics).
        if should_skip_dial(d) or service_menu_kind(d):
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
    bark_dials = []          # deferred to the global bark pass

    for dial_rec in dials:
        if should_skip_dial(dial_rec):
            stats['skipped'] += 1
            continue
        if get_formid(dial_rec, 'FormID') in _EMPTY_DIAL_FIDS:
            stats['skipped'] += 1
            continue
        # Bark topics (greetings, combat/detection barks) are emitted by the
        # global bark pass so they can be grouped by (quest, subtype) across ALL
        # bark DIALs — Skyrim honors only one bark topic per subtype per quest.
        edid = get_str(dial_rec, 'EditorID', '')
        if not service_menu_kind(dial_rec):
            _c, _s, _snam, is_bark = classify_topic(
                edid, get_int(dial_rec, 'DATA.Type'))
            if is_bark:
                bark_dials.append(dial_rec)
                continue
        try:
            content, dlbr_bytes, owner_qfid, dial_fid, dlbr_fid = _build_one_topic(
                dial_rec, info_by_dial, writer, remap, offset, generic_quest_fid,
                tclt_targets, bark_choice_targets, bark_choice_gate,
                unlock_plan, unlock_globals, npc_to_vtyp,
                quest_npc_fids, sge_quest_fids, quest_edid_by_fid,
                quest_priority, voice_map,
                fid_to_edid, xref, well_known_props,
                quest_dialog_ctdas, vtyp_edid_by_fid, stats, script_vars)
            if not content:      # dropped (e.g. service topic with no gate)
                stats['skipped'] += 1
                continue
            all_dial_content += content
            if dlbr_bytes:
                all_dlbr += dlbr_bytes
                view_branches[owner_qfid].append(dlbr_fid)
            view_topics[owner_qfid].append(dial_fid)
            stats['topics'] += 1
        except Exception as e:
            print(f"  ERROR topic {get_str(dial_rec, 'EditorID', '?')}: {e}")

    # --- Global bark pass: one topic per (owning quest, subtype) ---
    bark_ctx = dict(
        npc_to_vtyp=npc_to_vtyp, sge_quest_fids=sge_quest_fids,
        remap=remap, offset=offset, unlock_plan=unlock_plan,
        unlock_globals=unlock_globals, fid_to_edid=fid_to_edid,
        well_known_props=well_known_props, xref=xref, voice_map=voice_map,
        quest_edid_by_fid=quest_edid_by_fid, quest_priority=quest_priority,
        quest_dialog_ctdas=quest_dialog_ctdas, vtyp_edid_by_fid=vtyp_edid_by_fid,
        bark_dial_fids=bark_dial_fids, stats=stats, script_vars=script_vars)
    bark_content, bark_sge = _build_bark_pass(
        bark_dials, info_by_dial, writer, remap,
        bark_generic_quests, bark_ctx)
    all_dial_content += bark_content

    # --- One DLVW per owning quest ---
    all_dlvw = b''
    for qfid, branches in view_branches.items():
        dlvw_fid = writer.alloc_formid()
        all_dlvw += make_dlvw(dlvw_fid, f'TES4View_{qfid:08X}', qfid,
                              branches, view_topics.get(qfid, []))
        stats['views'] += 1

    if all_dial_content:
        writer.add_raw_group('DIAL', all_dial_content)
    if all_dlbr:
        writer.add_raw_group('DLBR', all_dlbr)
    if all_dlvw:
        writer.add_raw_group('DLVW', all_dlvw)

    print(f"    topics={stats['topics']} bark-topics={stats['bark_topics']} "
          f"infos={stats['infos']} "
          f"branches={stats['branches']} views={stats['views']} "
          f"skipped={stats['skipped']} voice-gated={stats['voice_gated']} "
          f"id-gated={stats['id_gated']} unlock-gated={stats['unlock_gated']} "
          f"revealers={stats['revealers']} quest-gated={stats['quest_gated']} "
          f"quest-cond-gated={stats['quest_cond_gated']}")

    # Synthetic quests that must run from a new game (in the .seq file): the
    # generic conversation-topic quest + the per-subtype generic bark quests.
    return {generic_quest_fid} | bark_sge


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
                     generic_quest_fid, tclt_targets, bark_choice_targets,
                     bark_choice_gate,
                     unlock_plan,
                     unlock_globals, npc_to_vtyp,
                     quest_npc_fids, sge_quest_fids, quest_edid_by_fid,
                     quest_priority, voice_map,
                     fid_to_edid, xref, well_known_props,
                     quest_dialog_ctdas, vtyp_edid_by_fid, stats,
                     script_vars=None):
    """Convert one DIAL topic and its child INFOs. Returns
    (dial_group_bytes, dlbr_bytes, owner_quest_fid, dial_fid, dlbr_fid)."""
    dial_fid = get_formid(dial_rec, 'FormID')
    edid = get_str(dial_rec, 'EditorID', '')
    dtype = get_int(dial_rec, 'DATA.Type')

    # Service-menu topics (Barter/Training): the Oblivion NPC lines become the
    # responses of a player-selectable topic whose prompt is synthesized and
    # whose INFOs open the Skyrim menu (fragment) — gated so the topic only
    # shows on NPCs that actually offer the service. Gate: barter -> the
    # merchant marker faction; training -> the trainer faction. ONE condition
    # either way: a Barter gate that OR-chained every vendor faction put 25-30
    # CTDAs on each INFO, past anything vanilla ships (max 22, max OR-run 20),
    # and the engine silently dropped every gated line — merchants lost the
    # topic while 1-condition Training kept working.
    service_kind = service_menu_kind(dial_rec)
    service_gate_bytes = b''
    if service_kind:
        from .record_types.actors import (get_merchant_faction_fid,
                                          get_trainer_faction_fid)
        gate_fid = (get_merchant_faction_fid() if service_kind == 'barter'
                    else get_trainer_faction_fid())
        if gate_fid:
            service_gate_bytes = pack_subrecord('CTDA', build_ctda(
                FUNC_GET_IN_FACTION, param1=gate_fid))
        if not service_gate_bytes:
            # No vendors/trainers exist in this file — drop the topic rather
            # than offer it ungated to every NPC.
            return b'', b'', 0, 0, 0
        dial_rec['FULL'] = SERVICE_MENU_TOPICS[edid][1]

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
        #
        # EXCEPTION: a target reached from a BARK/greeting choice must be
        # TOP-LEVEL, matching vanilla (every HELO→CUST branch is DNAM=1). A
        # greeting bark can't hold a menu, so the engine surfaces the response
        # as a top-level topic once the bark's TCLT points at it; a Normal
        # branch there leaves the player with the line but no way to select it.
        is_linked = (dial_fid in tclt_targets
                     and dial_fid not in bark_choice_targets
                     and (dial_fid & 0xFFFFFF) not in unlock_plan['gated'])
        dlbr_fid = writer.alloc_formid()
        dlbr_edid = (f'TES4_{edid}_Branch' if edid
                     else f'TES4_DLBR_{dlbr_fid:08X}')
        dlbr_bytes = make_dlbr(dlbr_fid, dlbr_edid, owner_qfid, dial_fid,
                               top_level=not is_linked)
        stats['branches'] += 1

    # --- Identity gating data for conversation topics ---
    # Service-menu topics must not inherit identity/voice gates: their generic
    # lines serve EVERY vendor/trainer, not just the NPCs named by sibling
    # GetIsID lines — the service-faction gate below is the real filter.
    topic_npc_fids = set()
    if not is_bark and not service_kind:
        topic_npc_fids = read_getisid_fids_for_topic(child_infos)
        for info_rec in child_infos:
            topic_npc_fids |= read_getisid_fids(info_rec, offset=offset)
        if not topic_npc_fids and orig_quest_fid:
            topic_npc_fids = quest_npc_fids.get(orig_quest_fid, set())

    # Voice types named anywhere in the topic (for generic siblings/greetings).
    topic_vtyps = (set() if service_kind
                   else _topic_voice_types(child_infos, npc_to_vtyp, offset))

    # AddTopic unlock gate: Oblivion's central visibility mechanic — this topic
    # only appears once a revealing line/script fired. Re-expressed as
    # GetGlobalValue(TES4Unlock_<topic>) == 1; revealer fragments set it.
    unlock_gate_bytes = b''
    gname = unlock_plan['gated'].get(dial_fid & 0xFFFFFF)
    gfid = unlock_globals.get(gname) if gname else None
    if gfid:
        unlock_gate_bytes = pack_subrecord('CTDA', build_ctda(
            FUNC_GET_GLOBAL_VALUE, param1=gfid))

    # Bark-choice timing gate: a top-level response topic reached from a
    # (stage-gated) greeting inherits that greeting's quest-state gate so it
    # only surfaces when Oblivion would have offered the choice.
    bark_choice_gate_bytes = _bark_choice_gate_bytes(
        bark_choice_gate.get(dial_fid, []))

    # Shared context passed to the per-INFO converter.
    info_ctx = dict(
        is_bark=is_bark, npc_to_vtyp=npc_to_vtyp, topic_vtyps=topic_vtyps,
        topic_npc_fids=topic_npc_fids, service_gate_bytes=service_gate_bytes,
        unlock_gate_bytes=unlock_gate_bytes,
        bark_choice_gate_bytes=bark_choice_gate_bytes, service_kind=service_kind,
        orig_quest_fid=orig_quest_fid, sge_quest_fids=sge_quest_fids,
        remap=remap, offset=offset, unlock_plan=unlock_plan,
        unlock_globals=unlock_globals, fid_to_edid=fid_to_edid,
        well_known_props=well_known_props, xref=xref, voice_map=voice_map,
        quest_edid_by_fid=quest_edid_by_fid, edid=edid,
        quest_dialog_ctdas=quest_dialog_ctdas, vtyp_edid_by_fid=vtyp_edid_by_fid,
        stats=stats, script_vars=script_vars)

    # Bark topics are handled by the global bark pass (grouped by quest+subtype
    # across ALL bark DIALs), never here — see _build_bark_pass.
    assert not is_bark, "bark topics must go through _build_bark_pass"

    # --- Conversation topic: single topic under its owning quest ---
    topic_children, child_count = _convert_topic_infos(
        child_infos, owner_qfid, info_ctx)

    # Guaranteed catch-all so every vendor/trainer offers the topic even when
    # no original line's conditions pass (most barter lines are GetIsID-gated
    # to specific merchants). Text-only, placed last so real lines win.
    if service_kind and service_gate_bytes:
        topic_children += _build_service_fallback_info(
            writer, service_kind, service_gate_bytes)
        child_count += 1
        stats['infos'] += 1

    dial_bytes = convert_DIAL(
        dial_rec, info_count=child_count, dlbr_fid=dlbr_fid,
        quest_fid=owner_qfid, category=category, subtype=subtype, snam=snam)
    content = dial_bytes
    if topic_children:
        content += pack_group(7, struct.pack('<I', dial_fid), topic_children)
    return content, dlbr_bytes, owner_qfid, dial_fid, dlbr_fid


def _convert_topic_infos(child_infos, owner_qfid, ctx):
    """Convert a list of child INFOs for a topic owned by owner_qfid.

    Returns (topic_children_bytes, child_count). owner_qfid is the REMAPPED
    owning quest of the topic these INFOs belong to; a per-INFO GetQuestRunning
    gate is injected only when an INFO's own quest differs from the owner (this
    never happens for the per-quest bark split, which passes matching owners)."""
    topic_children = b''
    child_count = 0
    for info_rec in child_infos:
        try:
            # Per-INFO quest gate: Oblivion only shows an INFO while its own
            # QSTI quest runs. When the topic's owner quest is not that quest
            # (shared conversation topics owned by the generic quest),
            # re-express the gating as GetQuestRunning. SGE quests are exempt
            # (always running from a new game via the .seq file).
            quest_gate_bytes = b''
            info_qfid = (get_formid(info_rec, 'QSTI.Quest')
                         or ctx['orig_quest_fid'])
            if (info_qfid and info_qfid not in ctx['sge_quest_fids']
                    and ctx['remap'](info_qfid) != owner_qfid):
                quest_gate_bytes = pack_subrecord('CTDA', build_ctda(
                    FUNC_GET_QUEST_RUNNING, param1=ctx['remap'](info_qfid)))
            # Quest-level dialogue conditions: Oblivion gates ALL of a quest's
            # dialogue on the QUST's own CTDAs (NQDBeggars = GetInFaction
            # (Beggars) — that, not any INFO condition, is what keeps the
            # conditionless beggar lines on beggars). Skyrim has no quest-level
            # dialogue gate, so the owning quest's conditions ride on each INFO.
            quest_cond_bytes = ctx['quest_dialog_ctdas'].get(info_qfid, b'')
            if quest_cond_bytes:
                ctx['stats']['quest_cond_gated'] += 1
            # Inherited greeting timing gate — but ONLY when this INFO doesn't
            # already state its own quest timing. An INFO with its own
            # GetStage/GetStageDone/GetQuestRunning knows when it should show;
            # ANDing the greeting's (possibly different) stage would suppress it.
            bc_gate = ctx.get('bark_choice_gate_bytes', b'')
            if bc_gate and _has_quest_state_condition(info_rec):
                bc_gate = b''
            if bc_gate:
                ctx['stats']['bark_choice_gated'] = \
                    ctx['stats'].get('bark_choice_gated', 0) + 1
            injected = _build_injected_ctdas(
                info_rec, ctx['is_bark'], ctx['npc_to_vtyp'],
                ctx['topic_vtyps'], ctx['topic_npc_fids'],
                ctx['service_gate_bytes'] + quest_gate_bytes + quest_cond_bytes
                + bc_gate,
                ctx['unlock_gate_bytes'], ctx['offset'], ctx['stats'],
                sibling_factions=ctx.get('sibling_factions'),
                sibling_npcs=ctx.get('sibling_npcs'))
            # Revealer INFO: its OnEnd fragment sets the unlock globals; bind
            # each global name -> GLOB FormID as a VMAD property.
            reveal_names = ctx['unlock_plan']['info_reveals'].get(
                get_formid(info_rec, 'FormID') & 0xFFFFFF)
            reveal_props = None
            if reveal_names:
                reveal_props = {n: ctx['unlock_globals'][n] for n in reveal_names
                                if n in ctx['unlock_globals']}
                if reveal_props:
                    ctx['stats']['revealers'] += 1
            topic_children += convert_INFO(
                info_rec, injected_ctdas=injected,
                fid_to_edid=ctx['fid_to_edid'],
                well_known_props=ctx['well_known_props'], xref=ctx['xref'],
                reveal_props=reveal_props, service_menu=ctx['service_kind'],
                bark_dial_fids=(ctx.get('bark_dial_fids')
                                if ctx['is_bark'] else None),
                script_vars=ctx.get('script_vars'))
            child_count += 1
            ctx['stats']['infos'] += 1
            if ctx['voice_map'] is not None:
                info_fid = get_formid(info_rec, 'FormID')
                prefix = voice_file_prefix(
                    ctx['quest_edid_by_fid'].get(owner_qfid, ''), ctx['edid'])
                # Response transcripts, keyed the way the voice FILENAME is
                # (fid24 + response number): LipGenerator pairs each WAV with
                # its spoken text to produce the .lip sync track.
                for ri in range(get_int(info_rec, 'ResponseCount')):
                    rtext = get_str(info_rec, f'Response[{ri}].ResponseText')
                    rnum = (get_int(info_rec, f'Response[{ri}].ResponseNumber')
                            or (ri + 1))
                    if rtext:
                        _lip_texts[(info_fid & 0xFFFFFF, rnum)] = rtext
                # Target voice-type folders. An NPC-specific line (GetIsID) is
                # voiced by that NPC's assigned VTYP, which is NOT always the
                # folder Oblivion filed the recording under (Arvena Thelas is a
                # Dark Elf but her lines sit in high elf/f/). The engine looks in
                # the NPC's VTYP folder, so record it so the renamer relocates
                # the file there instead of trusting the source race dir.
                own_npcs = read_getisid_fids(info_rec, offset=ctx['offset'],
                                             positive_only=True)
                vt_edids = sorted({
                    ctx['vtyp_edid_by_fid'].get(ctx['npc_to_vtyp'][n], '')
                    for n in own_npcs if n in ctx['npc_to_vtyp']} - {''})
                if vt_edids:
                    prefix = prefix + '\t' + ','.join(vt_edids)
                ctx['voice_map'][info_fid & 0xFFFFFF] = prefix
        except Exception as e:
            print(f"  ERROR info under {ctx['edid'] or '?'}: {e}")
    return topic_children, child_count


def _ctdas_scope_audience(ctda_bytes: bytes) -> bool:
    """True if these packed CTDAs already restrict WHO a line reaches.

    A quest whose own conditions name a faction or an actor (GetInFaction /
    GetIsID) has already scoped its dialogue's audience, so its conditionless
    lines must not be further narrowed by a sibling's conditions.
    """
    if not ctda_bytes:
        return False
    pos = 0
    while pos + 6 <= len(ctda_bytes):
        size = struct.unpack_from('<H', ctda_bytes, pos + 4)[0]
        body = ctda_bytes[pos + 6:pos + 6 + size]
        if len(body) >= 10:
            func = struct.unpack_from('<H', body, 8)[0]
            if func in (FUNC_GET_IN_FACTION, FUNC_GET_IS_ID):
                return True
        pos += 6 + size
    return False


def _build_bark_pass(bark_dials, info_by_dial, writer, remap,
                     bark_generic_quests, ctx):
    """Emit bark topics grouped by (owning quest, subtype) across ALL bark DIALs.

    Skyrim honors only ONE topic per bark subtype per owning quest (verified:
    every vanilla HELO topic has a distinct owner; no quest owns two). GREETING
    and HELLO are BOTH the HELO subtype, so an INFO from either that is owned by
    the same quest Q must share a single HELO topic under Q — not two. This pass
    regroups every bark INFO by (remapped quest, subtype code) globally and emits
    exactly one topic per group, mirroring vanilla's one-bark-per-quest layout.

    Quest-less INFOs of a given subtype go to a synthetic per-subtype
    always-running generic quest (one HELO generic quest, one IDLE generic
    quest, ...), so those don't collide either. Quest ownership provides the
    "only while my quest runs" gate natively, so no GetQuestRunning is injected.

    Returns (dial_group_bytes, sge_quest_fids) — the synthetic generic quests
    are StartGameEnabled and must be added to the .seq file to run from a new
    game."""
    # (owner_qfid, subtype) -> {'infos': [...], 'src': dial_rec,
    #                           'cat': category, 'snam': snam, 'dial_fid': fid}
    groups = {}
    order = []
    sge_extra = set()

    for dial_rec in bark_dials:
        dial_fid = get_formid(dial_rec, 'FormID')
        edid = get_str(dial_rec, 'EditorID', '')
        dtype = get_int(dial_rec, 'DATA.Type')
        category, subtype, snam, _is_bark = classify_topic(edid, dtype)
        child_infos = info_by_dial.get(dial_fid, [])
        # Priority-order the INFOs (highest quest priority first), matching the
        # conversation-topic sort so Skyrim's physical order == Oblivion's.
        child_infos = sorted(
            child_infos,
            key=lambda r: -ctx['quest_priority'].get(
                get_formid(r, 'QSTI.Quest'), 0))
        for info_rec in child_infos:
            raw_q = get_formid(info_rec, 'QSTI.Quest')
            if raw_q:
                owner_qfid = remap(raw_q)
            else:
                # Synthetic per-subtype generic quest (created once).
                snam_code = snam.decode('latin1')
                qkey = f'TES4Generic{snam_code}'
                if qkey not in bark_generic_quests:
                    qfid = _make_generic_quest(
                        writer, qkey, f'TES4 Generic {snam_code}')
                    bark_generic_quests[qkey] = qfid
                    sge_extra.add(qfid)
                owner_qfid = bark_generic_quests[qkey]
            key = (owner_qfid, subtype)
            if key not in groups:
                # Prefer a real DIAL FormID for the group's topic; the first
                # source DIAL seen for this key donates its record + (if unused)
                # its FormID.
                groups[key] = {'infos': [], 'src': dial_rec, 'cat': category,
                               'snam': snam, 'edid': edid, 'src_fid': dial_fid}
                order.append(key)
            groups[key]['infos'].append(info_rec)

    # Assign FormIDs: each group tries to reuse the original FormID of its
    # donor source DIAL, but a DIAL FormID can be claimed by only one group.
    claimed = set()
    content = b''
    for key in order:
        owner_qfid, subtype = key
        g = groups[key]
        src_fid = g['src_fid']
        if src_fid not in claimed:
            this_dial_fid = src_fid
            claimed.add(src_fid)
        else:
            this_dial_fid = writer.alloc_formid()
        this_edid = f"{g['edid']}_{owner_qfid:08X}" if g['edid'] else \
            f"TES4Bark_{subtype}_{owner_qfid:08X}"

        # Per-group INFO context: voice types are pooled from THIS group's INFOs
        # (a generic bark line inherits its siblings' voices). The voice-file
        # prefix MUST use the EditorID actually written into the DIAL record
        # (the split-suffixed one) — the engine builds the voice path from the
        # record's own EditorID, so a voicemap keyed on the pre-split name would
        # name every file something the game never looks for (= silent lines).
        # Barks carry no identity/unlock/service gates. Ownership is the group's
        # quest, so quest gates never fire (owner == info's own quest).
        group_ctx = dict(ctx)
        group_ctx['is_bark'] = True
        group_ctx['topic_vtyps'] = _topic_voice_types(
            g['infos'], ctx['npc_to_vtyp'], ctx['offset'])
        group_ctx['topic_npc_fids'] = set()
        group_ctx['service_gate_bytes'] = b''
        group_ctx['unlock_gate_bytes'] = b''
        group_ctx['bark_choice_gate_bytes'] = b''
        group_ctx['service_kind'] = ''
        group_ctx['edid'] = this_edid
        # No fallback quest for quest-less INFOs: their owner IS the synthetic
        # generic quest already, so leave info_qfid None -> no quest gate.
        group_ctx['orig_quest_fid'] = None
        # Audience the group's CONDITIONED siblings target, for conditionless
        # lines to inherit — but only when the owning quest's own CTDAs don't
        # already scope the audience (NQDBeggars does: GetInFaction(Beggars),
        # so its conditionless beggar lines must stay quest-scoped, NOT be
        # narrowed to whichever NPCs a sibling happens to name).
        raw_q = get_formid(g['infos'][0], 'QSTI.Quest')
        qctdas = ctx['quest_dialog_ctdas'].get(raw_q, b'')
        if _ctdas_scope_audience(qctdas):
            group_ctx['sibling_factions'] = set()
            group_ctx['sibling_npcs'] = set()
        else:
            sib_f, sib_n = set(), set()
            for ir in g['infos']:
                sib_f |= read_func_param_fids(ir, FUNC_GET_IN_FACTION,
                                              ctx['offset'])
                sib_n |= read_getisid_fids(ir, offset=ctx['offset'],
                                           positive_only=True)
            group_ctx['sibling_factions'] = sib_f
            group_ctx['sibling_npcs'] = sib_n

        topic_children, child_count = _convert_topic_infos(
            g['infos'], owner_qfid, group_ctx)
        if not child_count:
            continue
        # Topic priority carries Oblivion's arbitration. Oblivion picks the
        # passing bark from the highest-PRIORITY quest (NQDBeggars=12 beats
        # Generic=5, so a beggar begs instead of saying "Good day."). That used
        # to be baked into the INFO order of one shared topic; now that each
        # quest owns its own bark topic, the quest priority must ride on the
        # topic's PNAM (Skyrim: higher PNAM = considered first). Quest-less
        # groups keep the 50.0 default.
        priority = float(ctx['quest_priority'].get(raw_q, 50)) if raw_q else 50.0
        dial_bytes = convert_DIAL(
            g['src'], info_count=child_count, dlbr_fid=0,
            quest_fid=owner_qfid, category=g['cat'], subtype=subtype,
            snam=g['snam'], priority=priority, edid_override=this_edid,
            formid_override=this_dial_fid)
        content += dial_bytes
        content += pack_group(7, struct.pack('<I', this_dial_fid),
                              topic_children)
        ctx['stats']['bark_topics'] = ctx['stats'].get('bark_topics', 0) + 1

    return content, sge_extra


# Response text for the synthetic catch-all service INFOs (silent subtitle —
# no Oblivion audio exists for them, like the fallback greetings).
_SERVICE_FALLBACK_TEXT = {
    'barter': 'Take a look.',
    'training': "Let's begin.",
}


def _build_service_fallback_info(writer, service_kind: str,
                                 service_gate_bytes: bytes) -> bytes:
    """A minimal always-passing (for service NPCs) INFO that opens the menu."""
    from script_convert.pipeline import build_vmad_info_fragment
    info_fid = writer.alloc_formid()
    s = pack_subrecord('VMAD', build_vmad_info_fragment(
        '', script_name=SERVICE_MENU_SCRIPTS[service_kind]))
    s += pack_subrecord('ENAM', struct.pack('<HH', 0, 0))
    s += pack_subrecord('CNAM', struct.pack('<B', 0))
    s += pack_subrecord('TRDT', struct.pack('<IiI B3x I B3x', 0, 50, 0, 1, 0, 1))
    s += pack_string_subrecord('NAM1', _SERVICE_FALLBACK_TEXT[service_kind])
    s += pack_string_subrecord('NAM2', '')
    s += pack_string_subrecord('NAM3', '')
    s += service_gate_bytes
    return pack_record('INFO', info_fid, 0, s)


def _build_injected_ctdas(info_rec, is_bark, npc_to_vtyp, topic_vtyps,
                          topic_npc_fids, quest_gate_bytes, unlock_gate_bytes,
                          offset, stats, sibling_factions=None,
                          sibling_npcs=None):
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

    # Identity gate: a conversation INFO that never says WHO it is for would
    # otherwise show on every NPC (Oblivion relied on AddTopic for that). Only
    # inject when the INFO states no audience of its own — a line already gated
    # on a cell/faction/class/race HAS an audience, and bolting a sibling-derived
    # GetIsID OR-chain onto it narrows it to a handful of NPCs (AnvilTopic is
    # GetInCell(Anvil)-gated; injecting GetIsID stripped it from most of Anvil).
    id_bytes = b''
    if not is_bark and topic_npc_fids and not has_audience_condition(info_rec):
        id_bytes = build_or_chain(FUNC_GET_IS_ID, sorted(topic_npc_fids))
        stats['id_gated'] += 1

    # Sibling gate for a CONDITIONLESS bark line. Oblivion leaves some bark
    # INFOs with no conditions at all, relying on the quest's own CTDAs to scope
    # them (NQDBeggars = GetInFaction(Beggars)). Where the quest supplies no such
    # scope, an unconditional line would greet EVERY NPC (MS45's "I think we
    # should get out of here, quick!"). Its siblings under the same quest+topic
    # DO carry the intended audience (GetInFaction(HackdirtBrethren) /
    # GetIsID), so a conditionless line inherits their OR-chain rather than
    # going universal. Only fires when the INFO has zero conditions of its own.
    sib_bytes = b''
    if is_bark and not has_any_conditions(info_rec):
        if sibling_factions:
            sib_bytes = build_or_chain(FUNC_GET_IN_FACTION,
                                       sorted(sibling_factions))
            stats['sibling_gated'] += 1
        elif sibling_npcs:
            sib_bytes = build_or_chain(FUNC_GET_IS_ID, sorted(sibling_npcs))
            stats['sibling_gated'] += 1

    if unlock_gate_bytes:
        stats['unlock_gated'] += 1
    if quest_gate_bytes:
        stats['quest_gated'] += 1

    return (quest_gate_bytes + unlock_gate_bytes + voice_bytes + id_bytes
            + sib_bytes)
