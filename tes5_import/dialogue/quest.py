"""Oblivion QUST -> Skyrim QUST conversion.

Everything the quest record needs and the dialogue pass does not: script
property collection (SCRO refs, declared names, well-known refs), stage
fragments and journal detection, objective texts, quest priorities, DNAM
flags, and the quest-state CTDAs the dialogue pass gates barks on.

The dependency runs one way -- dialogue reads `compute_quest_priorities`,
`quest_state_ctdas`, `has_quest_state_condition` and
`bark_choice_gate_bytes` from here; nothing here imports dialogue.

See: docs/commentary/tes5_import_quest.md#quest-conversion
"""

import re
import struct

from ..base.constants import ENGINE_GLOBAL_FORMIDS
from ..base.conditions import (CTDA_OR, CTDA_RUN_ON_TARGET,
                                convert_ctda,
                                convert_ctda_list_with_strings,
                                convert_script_var_ctda)
from .objective_text import short_objective
from .quest_falloutnv import authored_objectives, has_authored_objectives
from ..base.text_reader import get_formid_index_offset, remap_formid
from ..record_types.common import (
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

_PLAYER_FORMID = 0x14
_PLAYER_BASE_FID = 0x07


def _collect_scro_properties(rec: dict, fid_to_edid: dict, prefix: str = '') -> dict:
    """Extract SCRO FormID refs from a record (optionally a stage-log prefix)
    into VMAD property name -> remapped FormID."""
    from script_convert.constants import safe_property_name
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
        if raw_fid in (0, _PLAYER_BASE_FID, _PLAYER_FORMID):
            continue
        edid = fid_to_edid.get(raw_fid)
        if not edid:
            continue
        safe = safe_property_name(edid)
        if safe.lower() in seen:
            continue
        seen.add(safe.lower())
        if edid.lower() in ENGINE_GLOBAL_FORMIDS:
            props[safe] = ENGINE_GLOBAL_FORMIDS[edid.lower()]
            continue
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


def _quest_well_known_refs(rec: dict, xref=None) -> dict:
    """{name: Papyrus type} of every property this quest's QF_ script
    declares. Best-effort: a converter failure yields {}.

    See: docs/commentary/tes5_import_quest.md#declared-properties-not-the-whole-registry
    """
    scripts = []
    record_script = get_str(rec, 'ResultScript')
    if record_script:
        scripts.append(record_script)
    for i in range(get_int(rec, 'StageCount')):
        log_count = get_int(rec, f'Stage[{i}].LogCount')
        if log_count:
            for j in range(log_count):
                src = get_str(rec, f'Stage[{i}].Log[{j}].ResultScript')
                if src.strip():
                    scripts.append(src)
        else:
            src = get_str(rec, f'Stage[{i}].ResultScript')
            if src.strip():
                scripts.append(src)
    if not scripts or xref is None:
        return {}

    from script_convert.converter import ScriptConverter
    conv = ScriptConverter(xref)
    for src in scripts:
        try:
            conv.convert_fragment(src, 'Quest')
        except Exception:
            continue
    return dict(conv._property_refs)


def _resolve_declared_properties(declared, well_known_props: dict = None) -> dict:
    """FormID bindings for the engine-hardcoded and synthesized names a QF_
    fragment declares. Names resolving to nothing are omitted, never
    bound to zero.

    See: docs/commentary/tes5_import_quest.md#declared-properties-not-the-whole-registry
    """
    out = {}
    for name in (declared or ()):
        low = name.lower()
        if low in ('player', 'playerref'):
            _dtype = (declared.get(name)
                      if isinstance(declared, dict) else None)
            out[name] = (_PLAYER_BASE_FID if _dtype == 'ActorBase'
                         else _PLAYER_FORMID)
        elif low in ENGINE_GLOBAL_FORMIDS:
            out[name] = ENGINE_GLOBAL_FORMIDS[low]
        elif well_known_props and name in well_known_props:
            out[name] = well_known_props[name]
    return out


def quest_stage_fragments(rec: dict) -> list:
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

_CTDA_OPS = {
    0x00: lambda a, b: a == b,
    0x20: lambda a, b: a != b,
    0x40: lambda a, b: a > b,
    0x60: lambda a, b: a >= b,
    0x80: lambda a, b: a < b,
    0xA0: lambda a, b: a <= b,
}


def target_live_at_stage(raw_hexes: list, stage_idx: int) -> bool:
    """Would Oblivion have shown this target's marker at `stage_idx`?

    A target with no conditions is always live; a condition that
    cannot be evaluated statically counts as passing.

    See: docs/commentary/tes5_import_quest.md#resolving-target-markers-per-stage
    """
    if not raw_hexes:
        return True

    groups = []
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
            target_stage = struct.unpack_from('<I', raw, 16)[0]
            done = stage_idx >= target_stage
            op = _CTDA_OPS.get(type_byte & 0xE0)
            value = bool(op(1.0 if done else 0.0, comp)) if op else True
        else:
            value = True

        current.append(value)
        if not (type_byte & 0x01):
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    return all(any(g) for g in groups)


_QUEST_STATE_FUNCS = frozenset({56, 58, 59, 99})

_PLAYER_PROGRESS_FUNCS = frozenset({71, 73})

_VAR_STATE_FUNCS = frozenset({53, 79})


def has_quest_state_condition(rec: dict) -> bool:
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


def quest_state_ctdas(rec: dict, offset: int, script_vars: dict = None) -> list:
    """Converted [(32-byte CTDA, cis2-or-None)] for just the TIMING
    conditions on `rec` -- the gates a promoted choice target must
    inherit. [] when the revealer has none.

    See: docs/commentary/tes5_import_quest.md#timing-ctdas-a-promoted-target-inherits
    """
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
            pair = convert_script_var_ctda(raw, script_vars or {}, offset)
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


def _any_reveal_path_is_ungated(revealer_gates: list) -> bool:
    """True when some revealer carries no conditions, so the bark is ungated."""
    return any(len(g) == 0 for g in revealer_gates)


def bark_choice_gate_bytes(revealer_gates: list) -> bytes:
    """Packed CTDA bytes gating a bark on its revealers, as one OR-chain.

    See: docs/commentary/tes5_import_quest.md#conditions-a-choice-target-inherits
    """
    from ..base.conditions import CTDA_OR
    if not revealer_gates:
        return b''
    if _any_reveal_path_is_ungated(revealer_gates):
        return b''
    if len(revealer_gates) == 1:
        return b''.join(_pack_gate_pair(p) for p in revealer_gates[0])
    if all(len(g) == 1 for g in revealer_gates):
        out = b''
        n = len(revealer_gates)
        for idx, g in enumerate(revealer_gates):
            c, cis2 = g[0]
            is_last = (idx == n - 1)
            tb = c[0] | CTDA_OR if not is_last else c[0] & ~CTDA_OR
            out += _pack_gate_pair((bytes([tb]) + c[1:], cis2))
        return out
    return b''.join(_pack_gate_pair(p) for p in revealer_gates[0])


_QUEST_PRIORITY_OVERRIDE: dict = {}

#: DNAM.Priority is a U8 but the engine band is 0-100; it also arbitrates AI packages.
QUEST_PRIORITY_MAX = 100

#: QUST DNAM flag bits carried over from TES4 DATA.Flags.
QUST_START_GAME_ENABLED = 0x01
QUST_ALLOW_REPEATED_STAGES = 0x08
QUST_STARTS_ENABLED = 0x10
#: Ceiling for stage-less container quests; staged ones keep their authored priority.
ZERO_STAGE_TOP = 49


def compute_quest_priorities(by_type: dict) -> dict:
    """FormID -> effective dialogue-arbitration priority (0-100) for every
    QUST. Staged quests keep their authored TES4 priority; stage-less
    conversation containers above the ceiling are order-preservingly
    compressed beneath it.

    See: docs/commentary/tes5_import_quest.md#quest-priority-arbitration
    """
    quest_priority = {get_formid(r, 'FormID'): get_int(r, 'DATA.Priority')
                      for r in by_type.get('QUST', [])
                      if get_formid(r, 'FormID')}
    staged_quest_fids = {get_formid(r, 'FormID') for r in by_type.get('QUST', [])
                        if get_formid(r, 'FormID') and get_int(r, 'StageCount')}
    zero_stage_fids = [f for f in quest_priority if f not in staged_quest_fids]
    if staged_quest_fids and zero_stage_fids:
        over = sorted((f for f in zero_stage_fids
                       if quest_priority[f] > ZERO_STAGE_TOP),
                      key=lambda f: (quest_priority[f], f))
        if over:
            distinct = sorted({quest_priority[f] for f in over})
            base = max(0, ZERO_STAGE_TOP - len(distinct) + 1)
            slot = {v: min(ZERO_STAGE_TOP, base + i)
                    for i, v in enumerate(distinct)}
            for f in over:
                quest_priority[f] = slot[quest_priority[f]]
    for fid, p in quest_priority.items():
        quest_priority[fid] = max(0, min(QUEST_PRIORITY_MAX, p))
    _QUEST_PRIORITY_OVERRIDE.clear()
    _QUEST_PRIORITY_OVERRIDE.update(quest_priority)
    return quest_priority


def _quest_dnam(rec: dict) -> bytes:
    """QUST DNAM: flags, effective priority, and the journal quest type.

    See: docs/commentary/tes5_import_quest.md#quest-priority-arbitration
    """
    tes4_flags = get_int(rec, 'DATA.Flags')
    fid = get_formid(rec, 'FormID')
    priority = _QUEST_PRIORITY_OVERRIDE.get(fid, get_int(rec, 'DATA.Priority'))
    priority = max(0, min(QUEST_PRIORITY_MAX, priority))
    flags = tes4_flags & (QUST_START_GAME_ENABLED | QUST_ALLOW_REPEATED_STAGES)
    if flags & QUST_START_GAME_ENABLED:
        flags |= QUST_STARTS_ENABLED
    qtype = 8 if (_quest_has_journal(rec)
                  or has_authored_objectives(rec)) else 0
    return struct.pack('<HBBII', flags, priority, 0, 0, qtype)


_GAMEPAD_TEXT_RE = re.compile(
    r'left stick|right stick|d-pad|dpad|right trigger|left trigger'
    r'|\bpress [ABXY]\b|\bbumper\b|holding [ABXY]\b|pull the (right|left)',
    re.IGNORECASE)
_PC_TEXT_RE = re.compile(
    r'\bmouse\b|\bshift\b|\bspacebar\b|\bTAB\b|\bctrl\b|\bclick\b'
    r'|number key|&sUActn', re.IGNORECASE)


_CONTROL_TOKENS = {
    'sUActnForward':   'press W',
    'sUActnBack':      'press S',
    'sUActnSldleft':   'press A',
    'sUActnSldright':  'press D',
    'sUActnRun':       'hold Shift',
    'sUActnActivate':  'press E',
    'sUActnMenumode':  'press Tab',
    'sUActnRdyitem':   'press R',
    'sUActnUse':       'click the left mouse button',
    'sUActnBlock':     'click the right mouse button',
    'sUActnCast':      'click the right mouse button',
    'sUActnCrouch':    'press Ctrl',
    'sUActnJump':      'press Space',
}
_CONTROL_TOKEN_RE = re.compile(r'&(\w+);')


def _expand_control_tokens(text: str) -> str:
    """Replace Oblivion `&sUActnX;` control tokens with Skyrim PC key names."""
    if not text or '&' not in text:
        return text
    return _CONTROL_TOKEN_RE.sub(
        lambda m: _CONTROL_TOKENS.get(m.group(1), m.group(0)), text)


def pc_stage_texts(texts: list) -> list:
    """Drop console-only journal variants and expand PC control tokens.

    `texts` is the stage's log entries in record order (None/'' preserved so
    callers keep their QSDT pairing). Returns a list of the same length with
    gamepad-only entries blanked to None and `&sUActnX;` tokens resolved to
    Skyrim's default PC key names in whatever survives.
    """
    real = [(i, t) for i, t in enumerate(texts) if t]
    if len(real) >= 2:
        pad = [i for i, t in real
               if _GAMEPAD_TEXT_RE.search(t) and not _PC_TEXT_RE.search(t)]
        pc = [i for i, t in real if _PC_TEXT_RE.search(t)]
        if pad and pc:
            texts = [None if i in pad else t for i, t in enumerate(texts)]
    return [_expand_control_tokens(t) if t else t for t in texts]


def quest_objective_texts(rec: dict, script_vars: dict = None) -> list:
    """Objective (stage_index, short text) pairs, in written order.

    Must stay identical to what convert_QUST emits -- the override
    builder rebuilds this same run for a translation plugin.

    See: docs/commentary/tes5_import_quest.md#stage-journal-text
    """
    out = []
    seen_stages = set()
    for i in range(get_int(rec, 'StageCount')):
        stage_idx = get_int(rec, f'Stage[{i}].Index')
        if stage_idx in seen_stages:
            continue
        txt = stage_objective_text(rec, i, script_vars)
        if not txt:
            continue
        seen_stages.add(stage_idx)
        out.append(short_objective(txt))
    return out


#: Script variables the plugin assigns; None until set_assigned_var_names().
_ASSIGNED_VAR_NAMES = None


def set_assigned_var_names(names) -> None:
    """Install the plugin's assigned-variable names for objective gating."""
    global _ASSIGNED_VAR_NAMES
    _ASSIGNED_VAR_NAMES = frozenset(names) if names is not None else None


def stage_objective_text(rec: dict, i: int, script_vars: dict = None) -> str:
    """The journal line stage `i` shows the player, or '' if it shows none.

    An entry whose conditions cannot pass is not the stage's objective: a
    quest's developer-only entries are gated on a script variable it never
    sets, so they would otherwise become HUD objectives.

    See: docs/commentary/tes5_import_quest.md#stage-log-entry-conditions
    """
    log_count = get_int(rec, f'Stage[{i}].LogCount')
    if log_count <= 0:
        return get_str(rec, f'Stage[{i}].LogEntry')
    texts = pc_stage_texts([get_str(rec, f'Stage[{i}].Log[{j}].Text')
                            for j in range(log_count)])
    for j, text in enumerate(texts):
        if text and not _gate_cannot_pass(rec, i, j, script_vars or {}):
            return text
    return ''


def _gate_cannot_pass(rec: dict, i: int, j: int, script_vars: dict) -> bool:
    """True when entry (i, j) tests a script variable nothing ever sets.

    Such a variable holds its 0 default forever, so a `== nonzero` test on one
    can never pass -- that is how Oblivion hides a developer entry. The journal
    line itself is gated by the CTDA we now write; this stops the entry ALSO
    minting a HUD objective, which carries no condition of its own.

    See: docs/commentary/tes5_import_quest.md#stage-log-entry-conditions
    """
    k = 0
    while True:
        raw_hex = rec.get(f'Stage[{i}].Log[{j}].Condition[{k}].Raw')
        if raw_hex is None:
            return False
        k += 1
        try:
            raw = bytes.fromhex(raw_hex)
        except ValueError:
            return False
        if len(raw) < 20 or struct.unpack_from('<H', raw, 8)[0] not in \
                _VAR_STATE_FUNCS:
            continue
        param1 = struct.unpack_from('<I', raw, 12)[0] & 0x00FFFFFF
        param2 = struct.unpack_from('<I', raw, 16)[0]
        if _ASSIGNED_VAR_NAMES is None:
            return False
        name = script_vars.get(param1, {}).get(param2)
        if name and name.lower() in _ASSIGNED_VAR_NAMES:
            continue
        if (raw[0] & 0xE0) == 0x00 and struct.unpack_from('<f', raw, 4)[0]:
            return True
    return False


def _quest_vmad_properties(rec, edid, fid_to_edid, well_known_props,
                           unlock_plan, unlock_globals, xref) -> dict:
    """VMAD property name -> FormID for a quest's QF_ fragment script.

    See: docs/commentary/tes5_import_quest.md#vmad-property-binding
    """
    prop_vals = (_collect_all_scro_properties(rec, fid_to_edid)
                 if fid_to_edid else {})
    declared = _quest_well_known_refs(rec, xref)
    for name, fid in _resolve_declared_properties(
            declared, well_known_props).items():
        if name.lower() in ('player', 'playerref'):
            _drop_case_variants(prop_vals, name)
            prop_vals[name] = fid
        elif name.lower() in ENGINE_GLOBAL_FORMIDS:
            prop_vals.setdefault(name, fid)
        else:
            prop_vals[name] = fid
    if xref is not None:
        _bind_placed_references(prop_vals, declared, xref)
    if unlock_plan and unlock_globals:
        ql = edid.lower()
        for (qkey, _stage), gnames in unlock_plan['stage_reveals'].items():
            if qkey != ql:
                continue
            for n in gnames:
                if n in unlock_globals:
                    prop_vals[n] = unlock_globals[n]
    return prop_vals


def _drop_case_variants(prop_vals: dict, name: str) -> None:
    """Remove other-cased spellings of *name* so the canonical one binds."""
    for k in [k for k in prop_vals
              if k.lower() == name.lower() and k != name]:
        del prop_vals[k]


def _bind_placed_references(prop_vals: dict, declared: dict, xref) -> None:
    """Rebind reference-typed properties from an actor BASE to its placed ref.

    See: docs/commentary/tes5_import_quest.md#vmad-property-binding
    """
    from script_convert.constants import wants_placed_reference
    offset = get_formid_index_offset()
    for name, ptype in declared.items():
        if not wants_placed_reference(ptype):
            continue
        if name.lower() in ('player', 'playerref'):
            continue
        raw_hex = xref.edid_to_formid.get(name.lower(), '')
        if not raw_hex or xref.record_type.get(raw_hex, '') not in (
                'NPC_', 'CREA', 'ACTI', 'LIGH'):
            continue
        ref_hex = xref.unique_placed_ref(raw_hex)
        if not ref_hex:
            continue
        try:
            fid = remap_formid(int(ref_hex, 16), offset)
        except ValueError:
            continue
        _drop_case_variants(prop_vals, name)
        prop_vals[name] = fid


def _stage_log_entry(rec: dict, i: int, j: int, text: str,
                     script_vars: dict) -> bytes:
    """QSDT [CTDA/CIS2] [CNAM] for ONE stage log entry.

    The entry's own conditions decide whether its text displays.

    See: docs/commentary/tes5_import_quest.md#stage-log-entry-conditions
    """
    subs = pack_uint8_subrecord(
        'QSDT', get_int(rec, f'Stage[{i}].Log[{j}].Flags') & 0x03)
    for ctda, cis2 in convert_ctda_list_with_strings(
            rec, script_vars, prefix=f'Stage[{i}].Log[{j}].'):
        subs += pack_subrecord('CTDA', ctda)
        if cis2:
            subs += pack_string_subrecord('CIS2', cis2)
    if text:
        subs += pack_string_subrecord('CNAM', text)
    return subs


def _quest_stages(rec: dict, stage_count: int,
                  script_vars: dict = None) -> bytes:
    """INDX plus a log-entry run for every stage, journal text included.

    See: docs/commentary/tes5_import_quest.md#stage-log-entry-conditions
    """
    subs = b''
    for i in range(stage_count):
        subs += pack_subrecord('INDX', struct.pack(
            '<HBB', get_int(rec, f'Stage[{i}].Index'), 0, 0))
        log_count = get_int(rec, f'Stage[{i}].LogCount')
        if log_count > 0:
            stage_texts = pc_stage_texts(
                [get_str(rec, f'Stage[{i}].Log[{j}].Text')
                 for j in range(log_count)])
            for j in range(log_count):
                subs += _stage_log_entry(rec, i, j, stage_texts[j],
                                         script_vars or {})
        else:
            complete = get_int(rec, f'Stage[{i}].CompleteQuest')
            subs += pack_uint8_subrecord('QSDT', 0x01 if complete else 0)
            txt = get_str(rec, f'Stage[{i}].LogEntry')
            if txt:
                subs += pack_string_subrecord('CNAM', txt)
    return subs


def _quest_targets(rec: dict) -> tuple:
    """(alias_by_fid, [(alias_id, tes4_flags, [raw ctda hex])]) from QSTA."""
    alias_by_fid, targets, t = {}, [], 0
    while f'Target[{t}].FormID' in rec:
        tfid = get_formid(rec, f'Target[{t}].FormID')
        if tfid:
            alias_id = alias_by_fid.setdefault(tfid, len(alias_by_fid))
            raws, k = [], 0
            while rec.get(f'Target[{t}].Condition[{k}].Raw') is not None:
                raws.append(rec[f'Target[{t}].Condition[{k}].Raw'])
                k += 1
            targets.append((alias_id, get_int(rec, f'Target[{t}].Flags') & 0x01,
                            raws))
        t += 1
    return alias_by_fid, targets


def _quest_objectives(rec: dict, stage_count: int, targets: list,
                      script_vars: dict = None) -> bytes:
    """One QOBJ per journal-bearing stage, carrying only its live targets.

    See: docs/commentary/tes5_import_quest.md#quest-targets-become-per-objective-aliases
    """
    subs = b''
    seen_stages = set()
    for i in range(stage_count):
        stage_idx = get_int(rec, f'Stage[{i}].Index')
        if stage_idx in seen_stages:
            continue
        txt = stage_objective_text(rec, i, script_vars)
        if not txt:
            continue
        seen_stages.add(stage_idx)
        subs += pack_subrecord('QOBJ', struct.pack('<H', stage_idx))
        subs += pack_uint32_subrecord('FNAM', 0)
        subs += pack_string_subrecord('NNAM', short_objective(txt))
        emitted = set()
        for alias_id, tflags, raws in targets:
            if alias_id in emitted or not target_live_at_stage(raws,
                                                               stage_idx):
                continue
            emitted.add(alias_id)
            subs += pack_subrecord('QSTA', struct.pack('<iB3x',
                                                       alias_id, tflags))
    return subs


def _quest_alias_packages(pack_plan, qfid: int, alias_by_fid: dict) -> dict:
    """alias_id -> [PACK FormID]; PACK reads back these same indices.

    See: docs/commentary/tes5_import_quest.md#package-aliases
    """
    alias_packages = {}
    if pack_plan is None:
        return alias_packages
    for ref_fid, alias_id in pack_plan.assign_aliases(qfid, alias_by_fid):
        pkgs = pack_plan.packages_for_alias(qfid, ref_fid)
        if pkgs:
            alias_packages[alias_id] = pkgs
    for ref_fid, alias_id in alias_by_fid.items():
        pkgs = pack_plan.packages_for_alias(qfid, ref_fid)
        if pkgs and alias_id not in alias_packages:
            alias_packages[alias_id] = pkgs
    return alias_packages


def quest_aliases(alias_by_fid: dict, alias_packages: dict,
                   fid_to_edid: dict) -> bytes:
    """Forced-reference aliases: ALST ALID FNAM ALFR [ALPC...] VTCK ALED.

    See: docs/commentary/tes5_import_quest.md#forced-reference-alias-layout
    """
    subs = b''
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
    return subs


def convert_QUST(rec: dict, fid_to_edid: dict = None,
                 well_known_props: dict = None,
                 unlock_plan: dict = None,
                 unlock_globals: dict = None,
                 pack_plan=None, xref=None,
                 script_vars: dict = None) -> bytes:
    """QUST -> Quest conversion (the original quest, not the synthetic one).

    Order: EDID [VMAD] FULL DNAM NEXT [stages] [objectives] ANAM [aliases].
    unlock_plan/unlock_globals bind the AddTopic unlock GLOB properties for
    stage result scripts that reveal topics; script_vars names the quest
    variables authored objective targets are gated on.

    See: docs/commentary/tes5_import_quest.md#quest-conversion
    """
    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    stage_frags = quest_stage_fragments(rec)
    from ..base.object_scripts import get_quest_script
    attached = get_quest_script(get_formid(rec, 'FormID'))
    if (stage_frags or attached) and edid:
        from script_convert.pipeline import build_vmad_quest_fragments
        prop_vals = _quest_vmad_properties(rec, edid, fid_to_edid,
                                           well_known_props, unlock_plan,
                                           unlock_globals, xref)
        subs += pack_subrecord('VMAD', build_vmad_quest_fragments(
            edid, stage_frags, property_values=prop_vals or None,
            attached_script=attached))

    full = get_str(rec, 'FULL')
    if full:
        subs += pack_string_subrecord('FULL', full)
    subs += pack_subrecord('DNAM', _quest_dnam(rec))
    subs += pack_subrecord('NEXT', b'')

    stage_count = get_int(rec, 'StageCount')
    subs += _quest_stages(rec, stage_count, script_vars)
    alias_by_fid, targets = _quest_targets(rec)
    subs += (authored_objectives(rec, alias_by_fid, script_vars or {},
                                 get_formid_index_offset())
             if has_authored_objectives(rec)
             else _quest_objectives(rec, stage_count, targets,
                                    script_vars))

    qfid = get_formid(rec, 'FormID')
    alias_packages = _quest_alias_packages(pack_plan, qfid, alias_by_fid)
    subs += pack_uint32_subrecord('ANAM', len(alias_by_fid))
    subs += quest_aliases(alias_by_fid, alias_packages, fid_to_edid)
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
