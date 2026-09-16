"""Restore Oblivion's engine-scheduled NPC-to-NPC conversation chains.

Oblivion has an ambient conversation scheduler Skyrim lacks: when two NPCs
idle near each other, the engine may start a conversation — the initiator
speaks a HELLO line whose `GetIsID(<npc>)[Target]` condition names the other
actor, then the engine walks the line's Choice (TCLT) links, alternating
speakers per each INFO's NextSpeaker, until a GOODBYE ends it.  Quest authors
lean on this: CharacterGen stage 26→27 IS such a chain (Baurus "Are you all
right, sire?" → Emperor "Captain Renault?" → Baurus GOODBYE "She's dead..."
whose result runs `setstage charactergen 27`), and so are conversations in
MQ13, MQ15, MQ16 (the endgame), TG01, TG03 and MS91.  Skyrim never evaluates
a HELO topic against anything but the player and has no chain-walking
scheduler, so converted as-is every one of these chains is silent and the
quests stall.

Skyrim's native vessel would be a SCEN scene, but scenes need quest-alias
plumbing per conversation and are unverifiable without many in-game cycles
(docs/commentary/tes5_import_dialogue.md Step 4 — deferred).  These chains,
however, are IDENTITY-PINNED: the head names both actors via GetIsID, so the
proven `Actor.Say()` machinery (which drives every scripted CharGen
conversation already) can replay them from a generated driver script:

  * the head INFO is re-homed onto a synthesized, hidden, quest-owned CUST
    topic (the same shape as the script-driven CharGenVoice — Say() reaches
    it, the menu never shows it), and
  * a generated `TES4NPCConv<plugin>` start-game-enabled quest polls the
    chain's gate (converted from the head's own conditions) plus the same
    guards Oblivion's scheduler applies (both actors loaded, alive, near,
    not fighting), then Says the topic sequence with measured waits.  Each
    line's INFO is picked by the engine from its own converted conditions —
    exactly how Oblivion picked the line within a Choice-linked topic — so
    per-line gating keeps full CTDA fidelity, and INFO End fragments (the
    setstage payloads) fire exactly as they do for every other Say().

Only QUEST-ADVANCING chains are restored (a chain whose closure carries a
setstage/startquest/quest-variable result).  Pure flavor chatter stays
dropped per the "better absent than wrong" decision in
docs/commentary/tes5_import_dialogue.md — restoring it wholesale is still
TODO.txt Later-Issues #16.

This module is the SHARED analysis both stages must agree on (the
message_menus / dialog_unlocks mirroring contract): tes5_import builds the
head topics + driver QUST/VMAD from the plan, script_convert generates the
matching .psc from the same plan.  Any divergence leaves VMAD properties
unbound, which the generated script guards against but cannot repair.
"""
import re
import struct

from ..base.text_reader import info_result_script

# TES4 CTDA layout constants (24-byte raw conditions from the export dump).
CTDA_OR = 0x01
CTDA_RUN_ON_TARGET = 0x02
_COMPARISON_MASK = 0xE0
_OP_BY_NIBBLE = {0x00: '==', 0x20: '!=', 0x40: '>',
                 0x60: '>=', 0x80: '<', 0xA0: '<='}

FUNC_GET_IS_ID = 72
FUNC_GET_STAGE = 58
FUNC_GET_QUEST_VARIABLE = 79
FUNC_GET_ITEM_COUNT = 47          # TES4 index (xEdit wbDefinitionsTES4)

_PLAYER_FIDS = {0x00000014, 0x00000007}

# Result-script content that makes a chain quest-advancing.  `set X.y to`
# counts: quest scripts poll their own variables to advance (MQ13.convDone,
# TG03Elven.TrackConversation).
_QUEST_ADVANCING_RE = re.compile(
    r'setstage|startquest|stopquest|\bset\s+\w+\.\w+\s+to\b', re.I)

_MAX_HOPS = 24


def _raw_fid(rec) -> int:
    try:
        return int(rec.get('FormID', '0') or '0', 16)
    except ValueError:
        return 0


def _conds(rec, prefix: str = ''):
    """Parsed TES4 conditions: (type_byte, comp_value, func, param1, param2)."""
    out, i = [], 0
    while True:
        raw_hex = rec.get(f'{prefix}Condition[{i}].Raw')
        if raw_hex is None:
            break
        i += 1
        try:
            raw = bytes.fromhex(raw_hex or '')
        except ValueError:
            continue
        if len(raw) < 20:
            continue
        raw = raw + b'\x00' * max(0, 24 - len(raw))
        out.append((raw[0],
                    struct.unpack_from('<f', raw, 4)[0],
                    struct.unpack_from('<H', raw, 8)[0],
                    struct.unpack_from('<I', raw, 12)[0],
                    struct.unpack_from('<I', raw, 16)[0]))
    return out


def _choices(rec):
    out, i = [], 0
    while True:
        v = rec.get(f'Choice[{i}]')
        if v is None:
            break
        i += 1
        try:
            out.append(int(v, 16))
        except ValueError:
            pass
    return out


def _is_positive_id(t, v):
    """A GetIsID that ASSERTS identity (== 1), not an exclusion (== 0)."""
    return (t & _COMPARISON_MASK) == 0x00 and v >= 0.5


def _subject_ids(conds):
    return [p1 for (t, v, f, p1, _p2) in conds
            if f == FUNC_GET_IS_ID and not (t & CTDA_RUN_ON_TARGET)
            and _is_positive_id(t, v)]


def _target_ids(conds):
    return [p1 for (t, v, f, p1, _p2) in conds
            if f == FUNC_GET_IS_ID and (t & CTDA_RUN_ON_TARGET)
            and _is_positive_id(t, v)]


def head_is_npc_addressed(rec) -> bool:
    """True for a HELLO INFO whose every target-side identity names an NPC."""
    tgt = _target_ids(_conds(rec))
    return bool(tgt) and all((p & 0xFFFFFF) not in _PLAYER_FIDS
                             and (p & 0xFFFFFF) != 0 for p in tgt)


def _build_gates(head_conds, quest_edid_by_fid, script_vars,
                 scpt_edid_by_qfid):
    """Compile the head's non-identity conditions into driver-poll gates.

    Returns (gates, None) or (None, reason) when a condition falls outside the
    supported set — the chain is then skipped rather than restored with a
    trigger looser than Oblivion's (a conversation firing EARLY is worse than
    one that stays absent).
    """
    gates = []
    for (t, comp, func, p1, p2) in head_conds:
        if func == FUNC_GET_IS_ID:
            if not _is_positive_id(t, comp) and not (t & CTDA_RUN_ON_TARGET):
                # "not spoken by X" exclusion: the pinned speaker either IS X
                # (chain impossible) or is not (gate vacuous).  The pinned
                # positive identity already decides which, so drop it.
                continue
            continue                      # consumed as speaker/listener identity
        if t & CTDA_OR:
            return None, f'OR-chained gate (func {func})'
        if t & CTDA_RUN_ON_TARGET:
            return None, f'run-on-target gate (func {func})'
        op = _OP_BY_NIBBLE.get(t & _COMPARISON_MASK)
        if op is None:
            return None, f'unknown comparison 0x{t:02X}'
        if func == FUNC_GET_STAGE:
            qedid = quest_edid_by_fid.get(p1 & 0xFFFFFF)
            if not qedid:
                return None, f'GetStage on unknown quest {p1:08X}'
            gates.append({'kind': 'stage', 'quest_fid': p1,
                          'quest_edid': qedid, 'op': op,
                          'value': int(round(comp))})
        elif func == FUNC_GET_QUEST_VARIABLE:
            name = (script_vars or {}).get(p1 & 0xFFFFFF, {}).get(p2)
            qedid = quest_edid_by_fid.get(p1 & 0xFFFFFF)
            # The var lives as a property on the CONVERTED QUEST SCRIPT, whose
            # Papyrus name comes from the SCPT EditorID (papyrus_script_name),
            # not from the quest's.
            sedid = scpt_edid_by_qfid.get(p1 & 0xFFFFFF)
            if not name or not qedid or not sedid:
                return None, f'GetQuestVariable {p1:08X}[{p2}] unresolvable'
            gates.append({'kind': 'var', 'quest_fid': p1,
                          'quest_edid': qedid, 'script_edid': sedid,
                          'var': name, 'op': op, 'value': comp})
        elif func == FUNC_GET_ITEM_COUNT:
            # Evaluated against the SPEAKER, same as Oblivion ran it against
            # the conversation initiator (TG01: Methredhel no longer holds
            # the stolen diary).
            gates.append({'kind': 'itemcount', 'item_fid': p1, 'op': op,
                          'value': int(round(comp))})
        else:
            return None, f'unsupported gate func {func}'
    return gates, None


def _stage_compatible(cand_conds, stage_eq):
    """Can this INFO pass while the head's GetStage==N gates hold?"""
    for (t, comp, func, p1, _p2) in cand_conds:
        if func != FUNC_GET_STAGE:
            continue
        if t & CTDA_OR:            # OR-chained: can't statically bound; accept
            continue
        head_val = stage_eq.get(p1 & 0xFFFFFF)
        if head_val is None:
            continue
        op = _OP_BY_NIBBLE.get(t & _COMPARISON_MASK)
        v = comp
        ok = {'==': head_val == v, '!=': head_val != v,
              '>': head_val > v, '>=': head_val >= v,
              '<': head_val < v, '<=': head_val <= v}.get(op, True)
        if not ok:
            return False
    return True


class _ConvIndexes:
    """The per-run lookup tables the chain walk reads.

    Built once from the export so the import and script pipelines derive the
    identical plan (the VMAD/psc mirroring contract).
    """

    __slots__ = ('dial_by_fid', 'info_by_dial', 'quest_edid_by_fid',
                 'scpt_edid_by_qfid', 'ref_by_base', 'say_driven',
                 'hello_fid', 'conv_keep', 'conv_type')

    def __init__(self, by_type: dict, conv_keep, conv_type, say_driven):
        """Index the export for the conversation walk."""
        dials = by_type.get('DIAL', [])
        self.conv_keep = conv_keep
        self.conv_type = conv_type
        self.say_driven = say_driven
        self.dial_by_fid = {_raw_fid(d): d for d in dials}
        self.quest_edid_by_fid = {
            _raw_fid(q) & 0xFFFFFF: q.get('EditorID', '')
            for q in by_type.get('QUST', []) if q.get('EditorID')}
        self.scpt_edid_by_qfid = _script_edid_by_quest(by_type)
        self.hello_fid = next((_raw_fid(d) for d in dials
                               if d.get('EditorID') == 'HELLO'), 0)
        self.info_by_dial = _infos_by_parent(by_type.get('INFO', []))
        self.ref_by_base = _refs_by_base(by_type)

    def topic_dropped(self, dial_fid: int) -> bool:
        """True when the NPC-to-NPC drop would remove this topic.

        An unknown topic counts as gone; a Say on a dropped topic plays
        nothing, so a chain must never route through one.
        """
        d = self.dial_by_fid.get(dial_fid)
        if d is None:
            return True
        try:
            dtype = int(d.get('DATA.Type', '0') or '0')
        except ValueError:
            dtype = 0
        if dtype != self.conv_type:
            return False
        if d.get('EditorID', '') in self.conv_keep:
            return False
        return (dial_fid & 0xFFFFFF) not in self.say_driven

    def unique_ref(self, base_fid: int):
        """The one named persistent placed ref of a base, else None."""
        refs = self.ref_by_base.get(base_fid & 0xFFFFFF, [])
        return refs[0] if len(refs) == 1 else None


# ---------------------------------------------------------------------------
# Chain analysis: indexes, the walk, and its post-passes
# ---------------------------------------------------------------------------

def _script_edid_by_quest(by_type: dict) -> dict:
    """quest low-24 fid -> the EditorID of the SCPT it runs."""
    scpt_edid_by_fid = {_raw_fid(s) & 0xFFFFFF: s.get('EditorID', '')
                        for s in by_type.get('SCPT', []) if s.get('EditorID')}
    out = {}
    for q in by_type.get('QUST', []):
        scri = q.get('SCRI', '')
        if not scri:
            continue
        try:
            sedid = scpt_edid_by_fid.get(int(scri, 16) & 0xFFFFFF)
        except ValueError:
            continue
        if sedid:
            out[_raw_fid(q) & 0xFFFFFF] = sedid
    return out


def _infos_by_parent(infos: list) -> dict:
    """ParentDIAL raw fid -> [INFO, ...] in export order."""
    out = {}
    for inf in infos:
        try:
            p = int(inf.get('ParentDIAL', '0') or '0', 16)
        except ValueError:
            continue
        out.setdefault(p, []).append(inf)
    return out


def _refs_by_base(by_type: dict) -> dict:
    """base low-24 -> [(ref EditorID, raw fid), ...] for named placed actors."""
    out = {}
    for sig in ('ACHR', 'ACRE'):
        for a in by_type.get(sig, []):
            edid = a.get('EditorID')
            if not edid:
                continue
            try:
                base = int(a.get('NAME', '0') or '0', 16) & 0xFFFFFF
            except ValueError:
                continue
            out.setdefault(base, []).append((edid, _raw_fid(a)))
    return out


def _same_quest_closure(head: dict, quest_raw: str, info_by_dial: dict) -> list:
    """Every INFO reachable from `head` by TCLT within the same quest."""
    seen, frontier, closure = set(), _choices(head), []
    while frontier:
        t = frontier.pop(0)
        if t in seen:
            continue
        seen.add(t)
        for inf in info_by_dial.get(t, []):
            if inf.get('QSTI.Quest', '') != quest_raw:
                continue
            closure.append(inf)
            frontier.extend(_choices(inf))
    return closure


def _is_quest_advancing(head: dict, closure: list) -> bool:
    """True when the head or its closure advances the quest."""
    return bool(_QUEST_ADVANCING_RE.search(info_result_script(head))
                or any(_QUEST_ADVANCING_RE.search(info_result_script(i))
                       for i in closure))


def _expected_speaker(cur: dict, cur_speaker: str) -> str:
    """Who speaks the next hop: NextSpeaker 0 Target, 1 Self, 2 Either."""
    try:
        ns = int(cur.get('DATA.NextSpeaker', '0') or '0')
    except ValueError:
        ns = 0
    if ns == 1:
        return cur_speaker
    return 'B' if cur_speaker == 'A' else 'A'


def _hop_speaker(inf_conds: list, expected: str, cur_speaker: str,
                 bases: dict, next_speaker: int):
    """Which participant speaks this INFO, or None when it is a third party."""
    isubj = {p & 0xFFFFFF for p in _subject_ids(inf_conds)}
    if not isubj:
        return expected
    if bases[expected] in isubj:
        return expected
    if bases[cur_speaker] in isubj and next_speaker != 0:
        return cur_speaker
    return None


def _next_hop(cur: dict, cur_speaker: str, quest_raw: str, stage_eq: dict,
              bases: dict, visited: set, idx: _ConvIndexes) -> tuple:
    """The next (INFO, topic fid, speaker) in the chain, or (None, 0, '')."""
    try:
        next_speaker = int(cur.get('DATA.NextSpeaker', '0') or '0')
    except ValueError:
        next_speaker = 0
    expected = _expected_speaker(cur, cur_speaker)
    for t in _choices(cur):
        for inf in idx.info_by_dial.get(t, []):
            if id(inf) in visited or inf.get('QSTI.Quest', '') != quest_raw:
                continue
            ic = _conds(inf)
            if not _stage_compatible(ic, stage_eq):
                continue
            speaker = _hop_speaker(ic, expected, cur_speaker, bases,
                                   next_speaker)
            if speaker is not None:
                return inf, t, speaker
    return None, 0, ''


def _walk_chain(head: dict, quest_raw: str, stage_eq: dict, bases: dict,
                idx: _ConvIndexes) -> tuple:
    """Linearize one chain into (hops, undropped topics), or (None, []).

    Runtime line selection stays with the engine -- each Say picks the first
    INFO whose converted conditions pass, Oblivion's own rule.  This walk only
    fixes the TOPIC sequence, each hop's speaker and its expected line length.

    See: docs/commentary/tes5_import_dialogue.md#the-npc-to-npc-conversation-scheduler
    """
    hops, undrop, visited = [], [], {id(head)}
    cur, cur_speaker = head, 'A'
    while len(hops) < _MAX_HOPS:
        nxt_info, nxt_topic, cur_speaker = _next_hop(
            cur, cur_speaker, quest_raw, stage_eq, bases, visited, idx)
        if nxt_info is None:
            break
        if nxt_topic not in idx.dial_by_fid:
            return None, [nxt_topic]
        if idx.topic_dropped(nxt_topic):
            undrop.append(nxt_topic)
        visited.add(id(nxt_info))
        hops.append({'topic_fid': nxt_topic,
                     'topic_edid': idx.dial_by_fid.get(nxt_topic, {})
                                   .get('EditorID', ''),
                     'speaker': cur_speaker,
                     'info_fid': _raw_fid(nxt_info)})
        cur = nxt_info
    return hops, undrop


def _build_one_chain(head: dict, idx: _ConvIndexes, script_vars: dict,
                     plan: dict, n: int, skip) -> dict:
    """One chain from its HELLO head, or None when it cannot be built."""
    head_fid = _raw_fid(head)
    head_conds = _conds(head)
    quest_raw = head.get('QSTI.Quest', '')
    try:
        quest_fid = int(quest_raw or '0', 16)
    except ValueError:
        quest_fid = 0
    if not quest_fid:
        skip(head_fid, 'no owning quest')
        return None

    closure = _same_quest_closure(head, quest_raw, idx.info_by_dial)
    if not _is_quest_advancing(head, closure):
        return None

    subj = _subject_ids(head_conds)
    tgt = _target_ids(head_conds)
    if len(set(subj)) != 1:
        skip(head_fid, f'{len(set(subj))} subject identities')
        return None
    a_base, b_base = subj[0], tgt[0]
    a_ref, b_ref = idx.unique_ref(a_base), idx.unique_ref(b_base)
    if not a_ref or not b_ref:
        skip(head_fid, 'no unique placed ref for a participant')
        return None

    gates, why = _build_gates(head_conds, idx.quest_edid_by_fid, script_vars,
                              idx.scpt_edid_by_qfid)
    if gates is None:
        skip(head_fid, why)
        return None
    stage_eq = {g['quest_fid'] & 0xFFFFFF: g['value']
                for g in gates if g['kind'] == 'stage' and g['op'] == '=='}

    bases = {'A': a_base & 0xFFFFFF, 'B': b_base & 0xFFFFFF}
    hops, undrop = _walk_chain(head, quest_raw, stage_eq, bases, idx)
    if hops is None:
        skip(head_fid,
             f'chain routes through unknown topic {undrop[0]:08X}')
        return None

    return {
        'index': n,
        'head_fid': head_fid,
        'head_topic_edid': f'{plan["quest_edid"]}Topic{n}',
        'owner_quest_fid': quest_fid,
        'owner_quest_edid': idx.quest_edid_by_fid.get(quest_fid & 0xFFFFFF,
                                                      ''),
        'subj': {'base': a_base, 'ref_edid': a_ref[0], 'ref_fid': a_ref[1]},
        'tgt': {'base': b_base, 'ref_edid': b_ref[0], 'ref_fid': b_ref[1]},
        'gates': gates,
        'hops': hops,
        'undrop_topic_fids': list(dict.fromkeys(undrop)),
    }


def _resolve_hello_hops(plan: dict) -> list:
    """Point every hop riding the shared HELLO topic at its restored chain.

    The raw HELLO DIAL does not survive conversion as one record -- the bark
    pass splits it per quest -- so such a hop must name the chain whose
    reparented head IS that INFO.  A hello-hop with no restored head has
    nowhere to live, so its chain is dropped.
    """
    head_chain_by_fid = {c['head_fid']: c['index'] for c in plan['chains']}
    kept = []
    for c in plan['chains']:
        ok = True
        for hop in c['hops']:
            if hop['topic_edid'] != 'HELLO':
                continue
            target = head_chain_by_fid.get(hop['info_fid'])
            if target is None:
                plan['skipped'].append(
                    (c['head_fid'], 'hop rides an unrestored HELLO INFO'))
                ok = False
                break
            hop['head_chain'] = target
        if ok:
            kept.append(c)
    return kept


def _renumber_chains(plan: dict, kept: list) -> list:
    """Make chain indices gapless -- they name properties in both pipelines."""
    old_to_new = {}
    for new_i, c in enumerate(kept):
        old_to_new[c['index']] = new_i
        c['index'] = new_i
        c['head_topic_edid'] = f'{plan["quest_edid"]}Topic{new_i}'
    for c in kept:
        for hop in c['hops']:
            if 'head_chain' in hop:
                hop['head_chain'] = old_to_new[hop['head_chain']]
    return kept


def _mark_exclusive_chains(chains: list) -> None:
    """Cross-link chains that share an INFO -- they are mutually exclusive.

    Two heads can open the SAME authored conversation, and Oblivion's
    scheduler ran whichever fired first, once; whichever of ours runs must
    retire the other or the whole talk replays.

    See: docs/commentary/tes5_import_dialogue.md#the-npc-to-npc-conversation-scheduler
    """
    info_sets = {id(c): {h['info_fid'] for h in c['hops']} | {c['head_fid']}
                 for c in chains}
    for c in chains:
        c['exclusive_with'] = sorted(
            d['index'] for d in chains
            if d is not c and (info_sets[id(d)] & info_sets[id(c)]))


def build_conversation_plan(by_type: dict, script_vars: dict = None,
                            plugin_stem: str = '', log=None) -> dict:
    """Detect and linearize the quest-advancing NPC-to-NPC HELLO chains.

    Deterministic pure analysis over export records — MUST produce identical
    output in the import and script pipelines (the VMAD/psc mirroring
    contract).  `script_vars` is packages.aliases.build_script_var_map's
    low-24 fid -> {index: name} table, used to resolve GetQuestVariable gates.
    """
    log = log or (lambda *_a, **_k: None)
    stem = re.sub(r'\W', '', plugin_stem or '')
    plan = {'quest_edid': f'TES4NPCConv{stem}',
            'script_name': f'TES4NPCConv{stem}',
            'chains': [], 'skipped': []}
    if not by_type.get('DIAL') or not by_type.get('INFO'):
        return plan

    from .converter import (build_say_topic_dispositions, CONV_KEEP_EDIDS,
                            DIAL_TYPE_CONVERSATION)
    idx = _ConvIndexes(by_type, CONV_KEEP_EDIDS, DIAL_TYPE_CONVERSATION,
                       set(build_say_topic_dispositions(by_type).keys()))
    if not idx.hello_fid:
        return plan

    def _skip(head_fid, reason):
        plan['skipped'].append((head_fid, reason))
        log(f'    npc-conversation skip {head_fid:08X}: {reason}')

    for head in idx.info_by_dial.get(idx.hello_fid, []):
        if not head_is_npc_addressed(head):
            continue
        chain = _build_one_chain(head, idx, script_vars, plan,
                                 len(plan['chains']), _skip)
        if chain is not None:
            plan['chains'].append(chain)

    plan['chains'] = _renumber_chains(plan, _resolve_hello_hops(plan))
    _mark_exclusive_chains(plan['chains'])
    return plan


# ---------------------------------------------------------------------------
# Generated driver script (script_convert side of the mirroring contract)
# ---------------------------------------------------------------------------

# Oblivion starts ambient conversations only between actors close together;
# fAIMaxSocialDistance-scale.  Also the poll cadence for the driver.
_CONVERSE_DISTANCE = 500.0
_POLL_SECONDS = 4.0
_FALLBACK_LINE_SECONDS = 4.0
_LINE_BEAT = 0.6            # breath between lines, like Oblivion's scheduler


def chain_property_bindings(chain, remap, resolve_hop_topic):
    """(property_name, output fid) pairs for one chain's VMAD.

    `remap` maps a RAW TES4 FormID to the output plugin space.
    `resolve_hop_topic(hop)` returns the OUTPUT DIAL FormID a hop's Say must
    target — the caller owns the mapping because bark topics (GOODBYE) are
    regrouped per quest and hello-hops point at another chain's synthesized
    head topic.  The chain's own head topic is bound separately.
    A resolver returning 0 leaves the property unbound; the generated script
    guards every topic against None and skips the chain.
    """
    i = chain['index']
    props = {f'Conv{i}A': remap(chain['subj']['ref_fid']),
             f'Conv{i}B': remap(chain['tgt']['ref_fid'])}
    for k, hop in enumerate(chain['hops']):
        fid = resolve_hop_topic(hop)
        if fid:
            props[f'Conv{i}T{k + 1}'] = fid
    seen_q = []
    n_item = n_var = 0
    for g in chain['gates']:
        if g['kind'] == 'itemcount':
            props[f'Conv{i}I{n_item}'] = remap(g['item_fid'])
            n_item += 1
        elif g['kind'] == 'var':
            # Script-typed property; the VMAD object is still the QUEST record
            # (the VM resolves the attached script instance from it).
            props[f'Conv{i}V{n_var}'] = remap(g['quest_fid'])
            n_var += 1
        elif g['quest_fid'] not in seen_q:
            seen_q.append(g['quest_fid'])
    for j, qfid in enumerate(seen_q):
        props[f'Conv{i}Q{j}'] = remap(qfid)
    return props


def _gate_exprs(chain):
    """Papyrus boolean terms for the chain's gates + the property decls."""
    i = chain['index']
    decls, terms = [], []
    seen_q = []
    for g in chain['gates']:
        if g['kind'] == 'stage' and g['quest_fid'] not in seen_q:
            seen_q.append(g['quest_fid'])
    qprop = {qfid: f'Conv{i}Q{j}' for j, qfid in enumerate(seen_q)}
    from script_convert.constants import (safe_property_name,
                                          papyrus_script_name)
    declared = set()
    n_item = n_var = 0
    guard = []
    for g in chain['gates']:
        if g['kind'] == 'itemcount':
            p = f'Conv{i}I{n_item}'
            n_item += 1
            decls.append(f'Form Property {p} Auto')
            guard.append(p)
            terms.append(f'Conv{i}A.GetItemCount({p}) {g["op"]} {g["value"]}')
        elif g['kind'] == 'stage':
            p = qprop[g['quest_fid']]
            if p not in declared:
                decls.append(f'Quest Property {p} Auto')
                declared.add(p)
                guard.append(p)
            terms.append(f'{p}.GetStage() {g["op"]} {g["value"]}')
        else:                                   # quest-variable gate
            p = f'Conv{i}V{n_var}'
            n_var += 1
            sname = papyrus_script_name(g['script_edid'])
            decls.append(f'{sname} Property {p} Auto')
            guard.append(p)
            var = safe_property_name(g['var'])
            val = g['value']
            vtxt = str(int(val)) if float(val).is_integer() else f'{val}'
            terms.append(f'{p}.{var} {g["op"]} {vtxt}')
    for p in reversed(guard):
        # A None property (binding divergence) must disable the chain, not
        # abort the whole poll function.
        terms.insert(0, f'{p} != None')
    return decls, terms


def build_script_chain_map(by_type: dict) -> dict:
    """topic EditorID (lower) -> line count of its NPC-to-NPC chain.

    `Actor.Say` plays one line, so a multi-line chain must be Said once per
    line; the engine's per-INFO CTDAs pick which, so only the COUNT is
    needed. Player-facing topics are excluded and keep the plain Say.
    See: docs/commentary/tes5_import_dialogue.md#script-started-conversation-chains
    """
    info_by_dial = _infos_by_parent(by_type.get('INFO', []))
    out = {}
    for dial in by_type.get('DIAL', []):
        edid = dial.get('EditorID', '')
        if not edid:
            continue
        n = _counter_chain_len(info_by_dial.get(_raw_fid(dial), []))
        if n > 1:
            out[edid.lower()] = n
    return out


def _counter_chain_len(infos: list) -> int:
    """Lines gating on a consecutive run of one quest variable, else 0.

    Every counted line must name a non-player listener via a run-on-target
    GetIsID -- the scheduler's own signature, and what separates an NPC
    chain from a player topic that happens to use a counter.
    """
    per_var = {}
    for inf in infos:
        if not head_is_npc_addressed(inf):
            continue
        for (t, v, f, p1, p2) in _conds(inf):
            if f == FUNC_GET_QUEST_VARIABLE and (t & _COMPARISON_MASK) == 0:
                per_var.setdefault((p1, p2), set()).add(int(v))
    if not per_var:
        return 0
    vals = max(per_var.values(), key=len)
    run = lo = min(vals)
    while run + 1 in vals:
        run += 1
    return run - lo + 1


def generate_driver_psc(plan, say_durations: dict = None) -> str:
    """The full TES4NPCConv<plugin>.psc source, or '' when no chains."""
    if not plan['chains']:
        return ''
    say_durations = say_durations or {}

    def _fallback(hop_or_head, topic_edid):
        """Length TES4Polyfill.SayLine assumes if the line the engine picks
        has no measured voice file (it normally returns the real length)."""
        if hop_or_head is not None:
            d = say_durations.get(f'info:{hop_or_head:08X}')
            if d:
                return min(float(d), 15.0)
        d = say_durations.get((topic_edid or '').lower())
        if d:
            return min(float(d), 10.0)
        return _FALLBACK_LINE_SECONDS

    def _say(actor, topic, hop_or_head, topic_edid):
        # SayLine blocks until the engine has begun the line and returns its
        # real length (+ tail); waiting that out is what Oblivion's scheduler
        # did between lines.  A dropped line returns 0 and the chain moves on.
        return (f'        Utility.Wait(TES4Polyfill.SayLine({actor}, {topic}, '
                f'{_fallback(hop_or_head, topic_edid):.2f}) + {_LINE_BEAT})')

    lines = [
        f'ScriptName {plan["script_name"]} extends Quest',
        '{Drives Oblivion engine-scheduled NPC-to-NPC conversations.',
        ' Generated by tes5_import.dialogue.conversations - do not edit.}',
        '',
    ]
    decls, bodies = [], []
    for chain in plan['chains']:
        i = chain['index']
        decls += [f'Actor Property Conv{i}A Auto',
                  f'Actor Property Conv{i}B Auto',
                  f'Topic Property Conv{i}T0 Auto']
        decls += [f'Topic Property Conv{i}T{k + 1} Auto'
                  for k in range(len(chain['hops']))]
        gdecls, terms = _gate_exprs(chain)
        decls += gdecls
        decls.append(f'Bool _done{i} = False')
        topic_guards = [f'Conv{i}T{k} != None'
                        for k in range(len(chain['hops']) + 1)]
        cond = ' && '.join([f'!_done{i}'] + topic_guards + terms
                           + [f'CanConverse(Conv{i}A, Conv{i}B)'])
        done_sets = [f'        _done{i} = True']
        done_sets += [f'        _done{j} = True'
                      for j in chain.get('exclusive_with', ())]
        body = [f'    ; {chain["owner_quest_edid"]}: head INFO '
                f'{chain["head_fid"]:08X}',
                f'    if {cond}']
        body += done_sets
        body.append(_say(f'Conv{i}A', f'Conv{i}T0', chain['head_fid'], 'HELLO'))
        for k, hop in enumerate(chain['hops']):
            spk = 'A' if hop['speaker'] == 'A' else 'B'
            body.append(_say(f'Conv{i}{spk}', f'Conv{i}T{k + 1}',
                             hop['info_fid'], hop['topic_edid']))
        body.append('    endif')
        bodies.append('\n'.join(body))

    lines += decls
    lines += [
        '',
        'Event OnInit()',
        f'    RegisterForSingleUpdate({_POLL_SECONDS})',
        'EndEvent',
        '',
        'Event OnUpdate()',
        '    CheckConversations()',
        f'    RegisterForSingleUpdate({_POLL_SECONDS})',
        'EndEvent',
        '',
        'Function CheckConversations()',
    ]
    lines.append('\n'.join(bodies))
    lines += [
        'EndFunction',
        '',
        'Bool Function CanConverse(Actor akA, Actor akB)',
        '    if akA == None || akB == None',
        '        return False',
        '    endif',
        '    if !akA.Is3DLoaded() || !akB.Is3DLoaded()',
        '        return False',
        '    endif',
        '    if akA.IsDead() || akB.IsDead()',
        '        return False',
        '    endif',
        '    if akA.IsInCombat() || akB.IsInCombat()',
        '        return False',
        '    endif',
        f'    return akA.GetDistance(akB) < {_CONVERSE_DISTANCE}',
        'EndFunction',
        '',
    ]
    return '\n'.join(lines)
