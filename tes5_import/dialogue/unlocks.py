"""AddTopic visibility -> Skyrim unlock-global analysis.

Oblivion's central dialogue-visibility mechanic (see the oblivion-dialog-system
skill): a Type-0/1 topic only appears in the player's topic list once it has
been ADDED — by an INFO's Add-Topics data list (NAME subrecords), an `AddTopic`
result-script command, a quest-stage result script, or automatically when a
spoken line's response text mentions the topic's name (Oblivion highlights and
auto-adds mentioned topic names). Skyrim has no AddTopic, so without a gate
every converted topic shows as soon as its quest runs (e.g. Azzan offering
"Rats" before the contract was ever discussed).

Re-expression in Skyrim terms:
  * one GLOB `TES4Unlock_<topic>` (0.0) per explicitly-AddTopic'd topic;
  * every INFO of a gated topic gets `GetGlobalValue(GLOB) == 1` injected;
  * every REVEAL event sets the global to 1 from a Papyrus fragment:
      - INFO Add-Topics data list / `AddTopic X` in its result script,
      - response text mentioning the gated topic's FULL name (whole word),
      - quest stage result scripts containing `AddTopic X`.
    INFO fragments fire OnEnd — the unlock lands right when the line finishes,
    before the topic menu refreshes, matching Oblivion's timing. Globals
    persist in saves, matching AddTopic's permanent player-knowledge model.

Gating is limited to topics that appear in an explicit Add-Topics data list or
AddTopic command: those are the designer-controlled reveals. Topics only ever
revealed by name-mention stay ungated (visible when conditions pass) — gating
them would risk dead content on any name-match miss. Topics revealed only by
BARK lines (GREETING/HELLO) that every one of the topic's speakers says are
also ungated: the revealing bark fires the moment the player contacts the NPC,
so in Oblivion they are effectively visible on first talk (e.g. Azzan's "Join
the Fighters Guild" via his FG-ad greeting). A bark said by a different NPC
reveals nothing on first talk with the topic's speaker, so that topic stays
gated (Sheogorath's greeting adds Haskill's "Greymarch"). Choice (TCLT) targets
that are explicitly added are gated like any other, with their TCLT-parent
INFOs as additional revealers (choosing the path unlocks and persists, like
Oblivion); choice targets never explicitly added are handled by the branch
level instead (non-top-level = choice-only reachability).

Both the importer (conditions, GLOB records, VMAD property bindings) and the
script pipeline (fragment .psc bodies) consume the same plan; keys are low-24
FormIDs so the plan is identical regardless of the load-order offset.
"""

import re
from collections import defaultdict
from ..base.conditions import read_getisid_fids
from ..base.text_reader import info_result_script

_RE_ADDTOPIC = re.compile(r'\baddtopic[\s,]+(\w+)', re.IGNORECASE)


def _low24(fid_str: str) -> int:
    """The low 24 bits of a hex FormID string, 0 when unparseable."""
    try:
        return int(fid_str, 16) & 0xFFFFFF
    except (TypeError, ValueError):
        return 0


def _global_name(edid: str, fid24: int, taken: set) -> str:
    """A unique TES4Unlock_<edid> global name, suffixed on collision."""
    base = re.sub(r'[^A-Za-z0-9_]', '_', edid) if edid else f'{fid24:06X}'
    name = f'TES4Unlock_{base}'
    if name.lower() in taken:
        name = f'TES4Unlock_{base}_{fid24:06X}'
    taken.add(name.lower())
    return name


def _speakers(rec: dict) -> set:
    """Raw GetIsID NPC FormIDs an INFO is gated to; {None} when anyone may say it."""
    return read_getisid_fids(rec, offset=0) or {None}


def _topic_speakers(infos: list, gated: dict) -> dict:
    """{global -> union of _speakers over the gated topic's own INFOs}."""
    out = defaultdict(set)
    for rec in infos:
        gname = gated.get(_low24(rec.get('ParentDIAL', '')))
        if gname:
            out[gname] |= _speakers(rec)
    return out


def _ungate_bark_only_topics(gated: dict, bark_speakers: dict,
                             convo_revealed: set, topic_speakers: dict) -> dict:
    """Drop the gate on topics revealed ONLY by barks their own speakers say.

    A topic stays gated when any NPC who speaks it never says a revealing bark.

    See: docs/commentary/tes5_import_dialogue.md#the-bark-ungating-exception
    """
    bark_only = {g for g, spk in bark_speakers.items()
                 if g not in convo_revealed
                 and (None in spk or topic_speakers.get(g, set()) <= spk)}
    return {f: g for f, g in gated.items() if g not in bark_only}


def _index_dials(dials: list) -> tuple:
    """(fid24 -> DIAL record, lowercase EditorID -> fid24)."""
    by_fid24, edid_to_fid24 = {}, {}
    for d in dials:
        fid24 = _low24(d.get('FormID', ''))
        if not fid24:
            continue
        by_fid24[fid24] = d
        edid = d.get('EditorID', '')
        if edid:
            edid_to_fid24[edid.lower()] = fid24
    return by_fid24, edid_to_fid24


def _indexed_values(rec: dict, key: str):
    """Yield rec['<key>[0]'], rec['<key>[1]'], ... until one is missing."""
    i = 0
    while True:
        val = rec.get('%s[%d]' % (key, i))
        if val is None:
            return
        i += 1
        yield val


def _script_addtopic_fids(script: str, edid_to_fid24: dict):
    """The gated-topic fid24s an `AddTopic X` command names."""
    for name in _RE_ADDTOPIC.findall(script or ''):
        fid24 = edid_to_fid24.get(name.lower())
        if fid24:
            yield fid24


def _info_explicit_targets(infos: list, edid_to_fid24: dict) -> set:
    """Topics an INFO adds by data list or result script."""
    targets = set()
    for rec in infos:
        targets.update(_low24(v) for v in _indexed_values(rec, 'AddTopic'))
        targets.update(_script_addtopic_fids(info_result_script(rec),
                                             edid_to_fid24))
    targets.discard(0)
    return targets


def _stage_scripts(rec: dict, i: int) -> list:
    """Every result script attached to quest stage entry `i`."""
    scripts = [rec.get('Stage[%d].ResultScript' % i, '')]
    j = 0
    while ('Stage[%d].Log[%d].Flags' % (i, j) in rec
           or 'Stage[%d].Log[%d].Text' % (i, j) in rec):
        scripts.append(rec.get('Stage[%d].Log[%d].ResultScript' % (i, j), ''))
        j += 1
    return scripts


def _quest_stage_addtopics(qusts: list, edid_to_fid24: dict) -> tuple:
    """(explicit targets, {(quest_edid_lower, stage): [fid24, ...]})."""
    targets = set()
    by_stage = defaultdict(list)
    for rec in qusts:
        quest_edid = rec.get('EditorID', '')
        if not quest_edid:
            continue
        i = 0
        while 'Stage[%d].Index' % i in rec:
            try:
                stage_idx = int(rec.get('Stage[%d].Index' % i, '0'))
            except ValueError:
                stage_idx = 0
            for script in _stage_scripts(rec, i):
                for fid24 in _script_addtopic_fids(script, edid_to_fid24):
                    targets.add(fid24)
                    by_stage[(quest_edid.lower(), stage_idx)].append(fid24)
            i += 1
    return targets, by_stage


def _scpt_addtopics(scpts: list, edid_to_fid24: dict) -> set:
    """Topics AddTopic'd from an object/quest SCPT -- visibility, not gating.

    See: docs/commentary/tes5_import_dialogue.md#script-addtopic-and-the-unlock-globals
    """
    found = set()
    for rec in scpts:
        found.update(_script_addtopic_fids(rec.get('SCTX', ''), edid_to_fid24))
    return found


def _is_bark_topic(d: dict, classify_topic) -> bool:
    """True when this DIAL classifies as a bark (GREETING/HELLO/...)."""
    if d is None:
        return False
    try:
        dtype = int(d.get('DATA.Type', '0'))
    except ValueError:
        dtype = 0
    return bool(classify_topic(d.get('EditorID', ''), dtype)[3])


def _build_gate_set(explicit_targets: set, dial_by_fid24: dict,
                    should_skip_dial, classify_topic) -> dict:
    """{topic fid24 -> global name} for every gateable explicit target.

    Skipped and bark topics are excluded.

    See: docs/commentary/tes5_import_dialogue.md#addtopic-unlock-gates
    """
    gated, taken = {}, set()
    for fid24 in sorted(explicit_targets):
        d = dial_by_fid24.get(fid24)
        if d is None or should_skip_dial(d):
            continue
        if _is_bark_topic(d, classify_topic):
            continue
        gated[fid24] = _global_name(d.get('EditorID', ''), fid24, taken)
    return gated


def _mention_regex(gated: dict, dial_by_fid24: dict) -> tuple:
    """({lowercase FULL name -> {global, ...}}, whole-word regex or None).

    Oblivion auto-adds every topic whose FULL name a spoken line mentions.
    """
    names_to_global = defaultdict(set)
    for fid24, gname in gated.items():
        full = dial_by_fid24[fid24].get('FULL', '').strip()
        if len(full) >= 4:
            names_to_global[full.lower()].add(gname)
    if not names_to_global:
        return names_to_global, None
    alts = sorted((re.escape(n) for n in names_to_global), key=len,
                  reverse=True)
    return names_to_global, re.compile(r'\b(' + '|'.join(alts) + r')\b',
                                       re.IGNORECASE)


def _info_explicit_globals(rec: dict, gated: dict,
                           edid_to_fid24: dict) -> set:
    """Globals this INFO reveals by data list, result script, or choice."""
    found = set()
    for val in _indexed_values(rec, 'AddTopic'):
        found.add(gated.get(_low24(val)))
    for fid24 in _script_addtopic_fids(info_result_script(rec), edid_to_fid24):
        found.add(gated.get(fid24))
    for val in _indexed_values(rec, 'Choice'):
        found.add(gated.get(_low24(val)))
    if rec.get('TCLT.Choice'):
        found.add(gated.get(_low24(rec['TCLT.Choice'])))
    found.discard(None)
    return found


def _info_mention_globals(rec: dict, mention_re, names_to_global: dict) -> set:
    """Globals this INFO reveals by naming the topic in its response text."""
    if mention_re is None:
        return set()
    found = set()
    i = 0
    while True:
        text = rec.get('Response[%d].ResponseText' % i)
        if text is None:
            break
        i += 1
        for m in mention_re.findall(text):
            found |= names_to_global[m.lower()]
    return found


def _build_info_reveals(infos: list, gated: dict, edid_to_fid24: dict,
                        dial_by_fid24: dict, mention_re,
                        names_to_global: dict, classify_topic) -> tuple:
    """({info fid24 -> globals}, {bark-revealed global -> speakers}, convo-revealed).

    A topic revealed only by its own speakers' barks is ungated afterwards.

    See: docs/commentary/tes5_import_dialogue.md#the-bark-ungating-exception
    """
    info_reveals, bark_speakers, convo_revealed = {}, defaultdict(set), set()
    bark_cache = {}
    for rec in infos:
        info_fid24 = _low24(rec.get('FormID', ''))
        if not info_fid24:
            continue
        explicit_set = _info_explicit_globals(rec, gated, edid_to_fid24)
        globals_set = explicit_set | _info_mention_globals(
            rec, mention_re, names_to_global)
        own_topic = _low24(rec.get('ParentDIAL', ''))
        globals_set.discard(gated.get(own_topic))
        if not globals_set:
            continue
        info_reveals[info_fid24] = globals_set
        if own_topic not in bark_cache:
            bark_cache[own_topic] = _is_bark_topic(
                dial_by_fid24.get(own_topic), classify_topic)
        if bark_cache[own_topic]:
            for gname in explicit_set:
                bark_speakers[gname] |= _speakers(rec)
        else:
            convo_revealed |= explicit_set
    return info_reveals, bark_speakers, convo_revealed


def _fragment_stages(qusts: list, quest_stage_fragments) -> dict:
    """quest_edid_lower -> {stage_index, ...} that own a Papyrus fragment."""
    frag_stages = defaultdict(set)
    for rec in qusts:
        qedid = (rec.get('EditorID', '') or '').lower()
        if not qedid:
            continue
        for stage_idx, _log in quest_stage_fragments(rec):
            frag_stages[qedid].add(stage_idx)
    return frag_stages


def _build_stage_reveals(qusts: list, infos: list, gated: dict,
                         stage_addtopics: dict, info_reveals: dict,
                         quest_stage_fragments) -> dict:
    """{(quest_edid_lower, stage): sorted globals} revealed at that stage.

    A stage reveals a global either by its own AddTopic, or because a
    revealing INFO's result script SetStages to it.
    """
    stage_reveals = defaultdict(set)
    for key, fids in stage_addtopics.items():
        gnames = {gated[f] for f in fids if f in gated}
        if gnames:
            stage_reveals[key] |= gnames

    frag_stages = _fragment_stages(qusts, quest_stage_fragments)
    info_by_fid24 = {}
    for rec in infos:
        f = _low24(rec.get('FormID', ''))
        if f:
            info_by_fid24[f] = rec
    for info_fid24, gnames in info_reveals.items():
        script = (info_by_fid24.get(info_fid24) or {}).get('ResultScript', '')
        if not script:
            continue
        for m in re.finditer(r'setstage\s+(\w+)\s+(\d+)', script,
                             re.IGNORECASE):
            qedid, stage = m.group(1).lower(), int(m.group(2))
            if stage in frag_stages.get(qedid, ()):
                stage_reveals[(qedid, stage)] |= set(gnames)
    return {k: sorted(v) for k, v in stage_reveals.items() if v}


def _drop_orphan_gates(gated: dict, info_reveals: dict,
                       stage_reveals: dict) -> dict:
    """Ungate any topic no revealer ever opens -- an unopenable door."""
    revealed = set()
    for gs in info_reveals.values():
        revealed.update(gs)
    for gs in stage_reveals.values():
        revealed.update(gs)
    orphans = {f: g for f, g in gated.items() if g not in revealed}
    if not orphans:
        return gated
    print(f'    WARNING: {len(orphans)} gated topics have NO revealer '
          f'(gate would never open) — leaving them ungated: '
          f'{sorted(orphans.values())[:5]}'
          f'{"..." if len(orphans) > 5 else ""}')
    return {f: g for f, g in gated.items() if g in revealed}


def build_unlock_plan(by_type: dict) -> dict:
    """Analyze the export and return the unlock plan:

    {
      'gated':         {topic_fid24: global_name},
      'info_reveals':  {info_fid24: sorted [global_name, ...]},
      'stage_reveals': {(quest_edid_lower, stage_index): sorted [global_name]},
      'script_added':  {topic_fid24, ...}  -- visibility only, never gated
    }
    """
    from .converter import should_skip_dial, classify_topic
    from .quest import quest_stage_fragments

    infos = by_type.get('INFO', [])
    qusts = by_type.get('QUST', [])
    dial_by_fid24, edid_to_fid24 = _index_dials(by_type.get('DIAL', []))

    explicit = _info_explicit_targets(infos, edid_to_fid24)
    stage_targets, stage_addtopics = _quest_stage_addtopics(qusts,
                                                            edid_to_fid24)
    explicit |= stage_targets
    script_added = _scpt_addtopics(by_type.get('SCPT', []), edid_to_fid24)

    gated = _build_gate_set(explicit, dial_by_fid24, should_skip_dial,
                            classify_topic)
    names_to_global, mention_re = _mention_regex(gated, dial_by_fid24)
    info_reveals, bark_speakers, convo_revealed = _build_info_reveals(
        infos, gated, edid_to_fid24, dial_by_fid24, mention_re,
        names_to_global, classify_topic)

    gated = _ungate_bark_only_topics(gated, bark_speakers, convo_revealed,
                                     _topic_speakers(infos, gated))
    kept = set(gated.values())
    info_reveals = {fid: sorted(gs & kept)
                    for fid, gs in info_reveals.items() if gs & kept}

    stage_reveals = _build_stage_reveals(qusts, infos, gated, stage_addtopics,
                                         info_reveals, quest_stage_fragments)
    gated = _drop_orphan_gates(gated, info_reveals, stage_reveals)
    return {'gated': gated, 'info_reveals': info_reveals,
            'stage_reveals': stage_reveals, 'script_added': script_added}


def create_unlock_globals(writer, plan: dict) -> dict:
    """Create one GLOB (float, 0.0) per gated topic. Returns {name: formid}."""
    import struct as _struct
    from ..record_types.common import (pack_record, pack_string_subrecord,
                                      pack_subrecord)
    name_to_fid = {}
    for name in sorted(set(plan['gated'].values())):
        fid = writer.derive_formid('UNLOCK_GLOB', name)
        subs = pack_string_subrecord('EDID', name)
        subs += pack_subrecord('FNAM', b'f')
        subs += pack_subrecord('FLTV', _struct.pack('<f', 0.0))
        writer.add_record('GLOB', pack_record('GLOB', fid, 0, subs))
        name_to_fid[name] = fid
    return name_to_fid
