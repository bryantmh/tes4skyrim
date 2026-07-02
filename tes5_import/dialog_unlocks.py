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
them would risk dead content on any name-match miss. Topics revealed by BARK
lines (GREETING/HELLO) are also ungated: the revealing bark fires the moment
the player contacts the NPC, so in Oblivion they are effectively visible on
first talk (e.g. Azzan's "Join the Fighters Guild" via his FG-ad greeting) —
a gate would only add fragment-vs-menu timing risk. Choice (TCLT) targets
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

_RE_ADDTOPIC = re.compile(r'\baddtopic[\s,]+(\w+)', re.IGNORECASE)


def _low24(fid_str: str) -> int:
    try:
        return int(fid_str, 16) & 0xFFFFFF
    except (TypeError, ValueError):
        return 0


def _global_name(edid: str, fid24: int, taken: set) -> str:
    base = re.sub(r'[^A-Za-z0-9_]', '_', edid) if edid else f'{fid24:06X}'
    name = f'TES4Unlock_{base}'
    if name.lower() in taken:
        name = f'TES4Unlock_{base}_{fid24:06X}'
    taken.add(name.lower())
    return name


def build_unlock_plan(by_type: dict) -> dict:
    """Analyze the export and return the unlock plan:

    {
      'gated':         {topic_fid24: global_name},
      'info_reveals':  {info_fid24: sorted [global_name, ...]},
      'stage_reveals': {(quest_edid_lower, stage_index): sorted [global_name]},
    }
    """
    dials = by_type.get('DIAL', [])
    infos = by_type.get('INFO', [])
    qusts = by_type.get('QUST', [])

    from .dialog_converter import should_skip_dial, classify_topic

    dial_by_fid24 = {}
    dial_edid_to_fid24 = {}
    for d in dials:
        fid24 = _low24(d.get('FormID', ''))
        if not fid24:
            continue
        dial_by_fid24[fid24] = d
        edid = d.get('EditorID', '')
        if edid:
            dial_edid_to_fid24[edid.lower()] = fid24

    # --- Collect explicit AddTopic targets (data lists + script commands) ---
    explicit_targets = set()
    for rec in infos:
        i = 0
        while True:
            val = rec.get(f'AddTopic[{i}]')
            if val is None:
                break
            i += 1
            fid24 = _low24(val)
            if fid24:
                explicit_targets.add(fid24)
        script = rec.get('ResultScript', '')
        if script:
            for name in _RE_ADDTOPIC.findall(script):
                fid24 = dial_edid_to_fid24.get(name.lower())
                if fid24:
                    explicit_targets.add(fid24)
    stage_addtopics = defaultdict(list)   # (quest_edid_lower, stage) -> [fid24]
    for rec in qusts:
        quest_edid = rec.get('EditorID', '')
        if not quest_edid:
            continue
        i = 0
        while f'Stage[{i}].Index' in rec:
            try:
                stage_idx = int(rec.get(f'Stage[{i}].Index', '0'))
            except ValueError:
                stage_idx = 0
            scripts = [rec.get(f'Stage[{i}].ResultScript', '')]
            j = 0
            while f'Stage[{i}].Log[{j}].Flags' in rec or \
                    f'Stage[{i}].Log[{j}].Text' in rec:
                scripts.append(rec.get(f'Stage[{i}].Log[{j}].ResultScript', ''))
                j += 1
            for script in scripts:
                if not script:
                    continue
                for name in _RE_ADDTOPIC.findall(script):
                    fid24 = dial_edid_to_fid24.get(name.lower())
                    if fid24:
                        explicit_targets.add(fid24)
                        stage_addtopics[(quest_edid.lower(), stage_idx)].append(fid24)
            i += 1

    # --- Gate set: explicit targets minus skipped / bark topics. Choice
    # (TCLT) targets ARE gated when explicitly added — their TCLT-parent
    # INFOs become revealers below, so choosing the path still works and the
    # topic persists in the menu afterwards (Oblivion behavior). Choice
    # targets never explicitly added aren't in this set at all; they get a
    # non-top-level branch instead (choice-only reachability).
    gated = {}
    taken = set()
    for fid24 in sorted(explicit_targets):
        d = dial_by_fid24.get(fid24)
        if d is None or should_skip_dial(d):
            continue
        try:
            dtype = int(d.get('DATA.Type', '0'))
        except ValueError:
            dtype = 0
        if classify_topic(d.get('EditorID', ''), dtype)[3]:   # bark
            continue
        gated[fid24] = _global_name(d.get('EditorID', ''), fid24, taken)

    # --- Mention regex over gated topic FULL names (Oblivion auto-add) ---
    names_to_global = {}
    for fid24, gname in gated.items():
        full = dial_by_fid24[fid24].get('FULL', '').strip()
        if len(full) >= 4:
            names_to_global[full.lower()] = gname
    mention_re = None
    if names_to_global:
        alts = sorted((re.escape(n) for n in names_to_global), key=len,
                      reverse=True)
        mention_re = re.compile(r'\b(' + '|'.join(alts) + r')\b', re.IGNORECASE)

    # --- Revealer INFOs ---
    bark_cache = {}

    def _is_bark(topic_fid24):
        if topic_fid24 not in bark_cache:
            d = dial_by_fid24.get(topic_fid24)
            if d is None:
                bark_cache[topic_fid24] = False
            else:
                try:
                    dt = int(d.get('DATA.Type', '0'))
                except ValueError:
                    dt = 0
                bark_cache[topic_fid24] = classify_topic(
                    d.get('EditorID', ''), dt)[3]
        return bark_cache[topic_fid24]

    info_reveals = {}
    bark_revealed = set()   # globals revealed by GREETING/HELLO/other barks
    for rec in infos:
        info_fid24 = _low24(rec.get('FormID', ''))
        if not info_fid24:
            continue
        own_topic = _low24(rec.get('ParentDIAL', ''))
        globals_set = set()
        i = 0
        while True:
            val = rec.get(f'AddTopic[{i}]')
            if val is None:
                break
            i += 1
            g = gated.get(_low24(val))
            if g:
                globals_set.add(g)
        script = rec.get('ResultScript', '')
        if script:
            for name in _RE_ADDTOPIC.findall(script):
                g = gated.get(dial_edid_to_fid24.get(name.lower(), 0))
                if g:
                    globals_set.add(g)
        # A choice link to a gated topic also reveals it — in Oblivion,
        # offering a choice makes the target reachable regardless of its
        # added state, and once taken it stays known.
        i = 0
        while True:
            val = rec.get(f'Choice[{i}]')
            if val is None:
                break
            i += 1
            g = gated.get(_low24(val))
            if g:
                globals_set.add(g)
        val = rec.get('TCLT.Choice')
        if val:
            g = gated.get(_low24(val))
            if g:
                globals_set.add(g)
        if mention_re:
            i = 0
            while True:
                text = rec.get(f'Response[{i}].ResponseText')
                if text is None:
                    break
                i += 1
                for m in mention_re.findall(text):
                    globals_set.add(names_to_global[m.lower()])
        # Speaking a line of topic T already requires T unlocked — self-reveals
        # are meaningless and would only bloat the fragment count.
        globals_set.discard(gated.get(own_topic))
        if globals_set:
            info_reveals[info_fid24] = globals_set
            if _is_bark(own_topic):
                bark_revealed |= globals_set

    # --- Bark-revealed topics are NOT gated. A GREETING/HELLO revealer fires
    # the moment the player contacts the NPC — in Oblivion the topic is
    # effectively visible on first talk, so a gate only adds the risk of the
    # reveal fragment racing the menu (or a different greeting playing) and
    # locking the topic. Their own GetIsID/faction/stage conditions do the
    # real filtering. Gates stay only on topics revealed exclusively by
    # conversation lines / quest stages (e.g. "Rats" after Azzan's contract
    # line).
    if bark_revealed:
        gated = {f: g for f, g in gated.items() if g not in bark_revealed}
    kept = set(gated.values())
    info_reveals = {fid: sorted(gs & kept)
                    for fid, gs in info_reveals.items() if gs & kept}

    # --- Quest-stage revealers ---
    stage_reveals = {}
    for key, fids in stage_addtopics.items():
        gnames = sorted({gated[f] for f in fids if f in gated})
        if gnames:
            stage_reveals[key] = gnames

    return {'gated': gated, 'info_reveals': info_reveals,
            'stage_reveals': stage_reveals}


def create_unlock_globals(writer, plan: dict) -> dict:
    """Create one GLOB (float, 0.0) per gated topic. Returns {name: formid}."""
    import struct as _struct
    from .record_types.common import (pack_record, pack_string_subrecord,
                                      pack_subrecord)
    name_to_fid = {}
    for name in sorted(set(plan['gated'].values())):
        fid = writer.alloc_formid()
        subs = pack_string_subrecord('EDID', name)
        subs += pack_subrecord('FNAM', b'f')
        subs += pack_subrecord('FLTV', _struct.pack('<f', 0.0))
        writer.add_record('GLOB', pack_record('GLOB', fid, 0, subs))
        name_to_fid[name] = fid
    return name_to_fid
