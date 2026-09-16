"""Assemble the DIAL/INFO/DLBR/DLVW hierarchy from converted records.

The record-level conversion lives in `converter.py`; this module decides
topic OWNERSHIP and walks the topic/INFO tree.  Calls run one way only --
nothing here is called from `converter.py`.

See: docs/commentary/tes5_import_dialogue.md#branches-views-topic-ownership
"""

import re
import struct
from collections import defaultdict
from ..base.text_reader import get_formid_index_offset, info_result_script
from .quest import (bark_choice_gate_bytes, compute_quest_priorities,
                    has_quest_state_condition, quest_state_ctdas)
from ..base.writer import pack_group
from ..record_types.common import (get_formid, get_int, get_str,
                                   pack_record, pack_string_subrecord,
                                   pack_subrecord)
from ..base.conditions import (
    FUNC_GET_GLOBAL_VALUE,
    FUNC_GET_IN_FACTION,
    FUNC_GET_IS_ID,
    FUNC_GET_IS_VOICE_TYPE,
    FUNC_GET_OFFERS_SERVICES_NOW,
    FUNC_GET_QUEST_RUNNING,
    build_ctda,
    build_or_chain,
    convert_ctda,
    convert_ctda_list_with_strings,
    has_any_conditions,
    has_audience_condition,
    needs_origin_gate,
    read_func_param_fids,
    read_getisid_fids,
    shared_state_conditions,
)

from .converter import (DIAL_TYPE_CONVERSATION, SERVICE_MENU_SCRIPTS,
    SERVICE_MENU_TOPICS, CONV_KEEP_EDIDS,
    is_npc_to_npc_conversation, make_conversation_quest,
    make_generic_quest, register_conversation_chains,
    build_say_topic_dispositions, classify_topic, collect_tclt_target_fids,
    convert_DIAL, convert_INFO, make_dlbr, make_dlvw, service_menu_kind,
    should_skip_dial, voice_file_prefix,
    GREET_TOPIC_BY_QUEST, EMPTY_DIAL_FIDS, SAY_TOPIC_DISPOSITIONS,
    lip_texts, startable_quests)


def _scan_startable_quests(by_type: dict) -> set:
    """Fill `startable_quests`; return start-game-enabled quest FormIDs.

    Startable = start-game-enabled, or named by a StartQuest/SetStage in the
    plugin.  Stays EMPTY with no SCPT records: empty means "unknown".

    See: docs/commentary/tes5_import_dialogue.md#branches-views-topic-ownership
    """
    sge_quest_fids = {get_formid(r, 'FormID') for r in by_type.get('QUST', [])
                      if (get_int(r, 'DATA.Flags') & 0x01)
                      and get_formid(r, 'FormID')}
    startable = set(sge_quest_fids)
    start_re = re.compile(r'\b(?:startquest|setstage)\s+"?([A-Za-z_]\w*)"?',
                          re.IGNORECASE)
    qfid_by_edid = {get_str(r, 'EditorID', '').lower(): get_formid(r, 'FormID')
                    for r in by_type.get('QUST', []) if get_str(r, 'EditorID')}

    def harvest(text):
        """Add every quest a StartQuest/SetStage in `text` names."""
        for m in start_re.finditer(text or ''):
            f = qfid_by_edid.get(m.group(1).lower())
            if f:
                startable.add(f)

    for r in by_type.get('SCPT', []):
        harvest(r.get('SCTX', ''))
    for r in by_type.get('QUST', []):
        for k, v in r.items():
            if k.endswith('ResultScript'):
                harvest(v if isinstance(v, str) else '')
    for r in by_type.get('INFO', []):
        harvest(info_result_script(r))

    startable_quests.clear()
    if by_type.get('SCPT'):
        startable_quests.update(startable)
    return sge_quest_fids


def _quest_lookup_tables(by_type, dials, fid_to_edid, generic_quest_fid,
                         offset, script_vars):
    """Quest tables: (EDID by FormID, FormID by EDID, quest-level CTDAs).

    The EDID map drives voice filenames and falls back to `fid_to_edid`, which
    is keyed by RAW source FormID, so the index byte is un-shifted first.
    CTDAs are keyed by RAW quest FormID.

    See: docs/commentary/tes5_import_dialogue.md#voice-files-lip-sync-audio
    """
    quest_edid_by_fid = {get_formid(r, 'FormID'): get_str(r, 'EditorID', '')
                         for r in by_type.get('QUST', [])
                         if get_formid(r, 'FormID')}
    if fid_to_edid:
        for rec in dials:
            qfid = get_formid(rec, 'Quest[0]')
            if not qfid or quest_edid_by_fid.get(qfid):
                continue
            raw = (((((qfid >> 24) & 0xFF) - offset) & 0xFF) << 24) \
                | (qfid & 0x00FFFFFF)
            edid = fid_to_edid.get(raw) or fid_to_edid.get(qfid)
            if edid:
                quest_edid_by_fid[qfid] = edid
    quest_edid_by_fid[generic_quest_fid] = 'TES4DialogueGeneric'
    quest_fid_by_edid = {e.lower(): f
                         for f, e in quest_edid_by_fid.items() if e}

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
    return quest_edid_by_fid, quest_fid_by_edid, quest_dialog_ctdas


def _branch_is_linked(dial_rec, dial_fid, tclt_targets, bark_choice_targets,
                      unlock_plan, stats) -> bool:
    """True when this topic's DLBR must be a Normal (non-top-level) branch.

    A TCLT target never explicitly AddTopic'd stays off the menu; one reached
    from a bark/greeting choice does not.  Script-driven Conversation topics
    are forced Normal, except CONV_KEEP_EDIDS.

    See: docs/commentary/tes5_import_dialogue.md#branches-views-topic-ownership
    """
    is_linked = (dial_fid in tclt_targets
                 and dial_fid not in bark_choice_targets
                 and (dial_fid & 0xFFFFFF) not in unlock_plan['gated']
                 and (dial_fid & 0xFFFFFF)
                 not in unlock_plan.get('script_added', ()))
    if (get_int(dial_rec, 'DATA.Type') == DIAL_TYPE_CONVERSATION
            and get_str(dial_rec, 'EditorID', '') not in CONV_KEEP_EDIDS
            and (dial_fid & 0xFFFFFF) in SAY_TOPIC_DISPOSITIONS):
        is_linked = True
        stats['script_topic_unlisted'] = \
            stats.get('script_topic_unlisted', 0) + 1
    return is_linked


def _bark_dial_fids(dials) -> set:
    """Remapped FormIDs of every bark DIAL (greeting, combat, detection).

    A bark INFO's choice pointing INTO this set is dropped -- the bark pass
    splits or merges that target -- while one pointing outside it is kept, so
    greeting-to-response routing survives.

    See: docs/commentary/tes5_import_dialogue.md#branches-views-topic-ownership
    """
    out = set()
    for d in dials:
        if should_skip_dial(d) or service_menu_kind(d):
            continue
        _c, _s, _snam, is_bark = classify_topic(
            get_str(d, 'EditorID', ''), get_int(d, 'DATA.Type'))
        if is_bark:
            out.add(get_formid(d, 'FormID'))
    return out


def _scan_bark_choice_links(dials, infos, offset, script_vars):
    """Bark topics and the conversation topics their choices reveal.

    Returns (bark_dial_fids, bark_choice_targets, bark_choice_gate).  A target
    is promoted to a top-level branch only when EVERY revealer supplies a real
    timing gate, and never when a normal conversation line also offers it --
    an inherited gate would dead-end that path.  The gate map holds one CTDA
    list per revealer; any live revealer suffices.

    See: docs/commentary/tes5_import_dialogue.md#branches-views-topic-ownership
    """
    bark_dial_fids = _bark_dial_fids(dials)

    bark_choice_targets = set()
    bark_choice_gate = defaultdict(list)
    conv_choice_targets = set()
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
        gate = quest_state_ctdas(info_rec, offset, script_vars)
        for cfid in targets_here:
            bark_choice_gate[cfid].append(gate)

    for cfid, gates in bark_choice_gate.items():
        if cfid in conv_choice_targets:
            continue
        if gates and all(len(g) > 0 for g in gates):
            bark_choice_targets.add(cfid)
    for cfid in list(bark_choice_gate):
        if cfid not in bark_choice_targets:
            del bark_choice_gate[cfid]
    return bark_dial_fids, bark_choice_targets, bark_choice_gate


def _quest_npc_sets(dials, info_by_dial) -> dict:
    """Per-quest NPC FormID sets, for fallback identity gating.

    Service-menu topics are excluded: their per-merchant GetIsIDs would widen
    the identity gate on every other topic the quest owns.

    See: docs/commentary/tes5_import_dialogue.md#voice-types-conditions
    """
    quest_npc_fids = defaultdict(set)
    for d in dials:
        if should_skip_dial(d) or service_menu_kind(d):
            continue
        qfid = get_formid(d, 'Quest[0]')
        if not qfid:
            continue
        npcs = read_getisid_fids_for_topic(
            info_by_dial.get(get_formid(d, 'FormID'), []))
        if npcs:
            quest_npc_fids[qfid] |= npcs
    return quest_npc_fids


def _emit_conversation_topics(dials, topic_args, bark_dials, conv_synth_fids,
                              view_branches, view_topics, stats):
    """Convert every non-bark topic; return (DIAL content, DLBR content).

    Bark topics are appended to `bark_dials` for the global pass instead --
    Skyrim honors one bark topic per subtype per quest, so they must be
    grouped across ALL bark DIALs.  `view_branches`, `view_topics`,
    `conv_synth_fids` and `stats` are filled in place.

    See: docs/commentary/tes5_import_dialogue.md#branches-views-topic-ownership
    """
    all_dial_content = b''
    all_dlbr = b''
    for dial_rec in dials:
        if (should_skip_dial(dial_rec)
                or get_formid(dial_rec, 'FormID') in EMPTY_DIAL_FIDS):
            stats['skipped'] += 1
            continue
        edid = get_str(dial_rec, 'EditorID', '')
        if not service_menu_kind(dial_rec):
            _c, _s, _snam, is_bark = classify_topic(
                edid, get_int(dial_rec, 'DATA.Type'))
            if is_bark:
                bark_dials.append(dial_rec)
                continue
        try:
            (content, dlbr_bytes, owner_qfid, dial_fid,
             dlbr_fid) = _build_one_topic(dial_rec, *topic_args)
            if not content:
                stats['skipped'] += 1
                continue
            if dial_rec.get('_synth_conv') is not None:
                conv_synth_fids[int(dial_rec['_synth_conv'])] = dial_fid
            all_dial_content += content
            if dlbr_bytes:
                all_dlbr += dlbr_bytes
                view_branches[owner_qfid].append(dlbr_fid)
            view_topics[owner_qfid].append(dial_fid)
            stats['topics'] += 1
        except Exception as e:
            print(f"  ERROR topic {get_str(dial_rec, 'EditorID', '?')}: {e}")
    return all_dial_content, all_dlbr


def build_dialog_groups(by_type: dict, writer, npc_to_vtyp: dict,
                        fid_to_edid: dict = None, xref=None,
                        well_known_props: dict = None,
                        voice_map: dict = None,
                        unlock_plan: dict = None,
                        unlock_globals: dict = None,
                        script_vars: dict = None,
                        master_index=None,
                        plugin_stem: str = '') -> set:
    """Build the DIAL/INFO/DLBR/DLVW hierarchy with original-quest ownership.

    Returns the set of quest FormIDs that must go in the .seq file (the
    synthetic generic dialogue quest; real SGE quests are added by the QUST
    pass in import_main).

    voice_map, when given, is filled with {info_fid_low24: voice filename
    prefix} so the audio pipeline can name extracted voice files the way the
    Skyrim engine will look them up (owning quest EDID + topic EDID).
    """

    # Which topics a script drives via Say/SayTo.  The IMPORT stage runs
    # independently of the script stage (`--import-only` never invokes it), so
    # this must be recomputed here rather than inherited: an empty set would
    # make info_needs_fragment() drop the Begin/End fragments SayLine depends
    # on, silently breaking every scripted conversation's timing.  Both stages
    # derive it from the same export with the same function, so they agree.
    from script_convert.converter import ScriptConverter
    from script_convert.pipeline import scan_say_topic_fids
    if not ScriptConverter.say_topics:
        ScriptConverter.say_topics = scan_say_topic_fids(by_type)

    lip_texts.clear()
    dials = by_type.get('DIAL', [])
    infos = by_type.get('INFO', [])
    if not dials:
        return set()

    offset = get_formid_index_offset()

    # --- Synthetic generic dialogue quest (owns orphan conversation topics) ---
    generic_quest_fid = make_generic_quest(writer, 'TES4DialogueGeneric',
                                            'TES4 Generic Dialogue',
                                            master_index=master_index)
    # A quest REUSED from a master is already started by the master's .seq;
    # re-listing it here would start the master's quest from this plugin.
    generic_is_ours = not (master_index is not None
                           and generic_quest_fid in master_index)
    # Per-source-DIAL synthetic quests for the quest-less INFOs of bark topics.
    # Skyrim honors only one bark topic per subtype per quest, so GREETING and
    # HELLO (both HELO) cannot both dump their quest-less lines into one shared
    # generic quest — each bark DIAL with orphan lines gets its own quest.
    # Populated on demand by _build_bark_topics_per_quest; drained into SGE.
    bark_generic_quests = {}   # source DIAL EditorID -> synthetic quest FID

    # --- Pre-scan ---
    # Say-driven topics MUST be resolved before the first should_skip_dial call:
    # is_npc_to_npc_conversation consults _SAY_TOPIC_DISPOSITIONS to spare the
    # 293 scripted Type-1 topics (CharGen, Announcers, Daedric speeches) from
    # the NPC-to-NPC drop. With an empty map every one of them would be skipped
    # and the tutorial would lose its dialogue.
    SAY_TOPIC_DISPOSITIONS.clear()
    SAY_TOPIC_DISPOSITIONS.update(build_say_topic_dispositions(by_type))
    n_ref = sum(1 for v in SAY_TOPIC_DISPOSITIONS.values() if v[0] == 'ref')
    print(f"    say-driven topics: {len(SAY_TOPIC_DISPOSITIONS)} "
          f"({n_ref} retargeted to a unique ref, "
          f"{len(SAY_TOPIC_DISPOSITIONS) - n_ref} drop target-conditions)")

    # --- NPC-to-NPC conversation chains ------------------------------------
    # Quest-advancing engine-scheduled conversations (CharacterGen 26→27,
    # MQ16, MS91, TG01/03, ...) are replayed by a generated driver quest; the
    # registration below reparents each chain's head onto a synthesized hidden
    # topic and spares the chain's member topics from the drop that follows.
    # Masterless plugins only: a dependent plugin's chains live against its
    # master's records and its driver script would collide with the master's.
    conv_plan = None
    conv_synth_fids = {}
    if master_index is None:
        from .conversations import build_conversation_plan
        conv_plan = build_conversation_plan(
            by_type, script_vars=script_vars, plugin_stem=plugin_stem)
        if conv_plan['chains']:
            register_conversation_chains(conv_plan, dials, infos, offset)

    skipped_fids = {get_formid(d, 'FormID') for d in dials if should_skip_dial(d)}
    _strip_dead_tclt(infos, skipped_fids)
    n_conv = sum(1 for d in dials if is_npc_to_npc_conversation(d))
    n_chains = len(conv_plan['chains']) if conv_plan else 0
    print(f"    NPC-to-NPC conversation topics dropped: {n_conv}; "
          f"quest-advancing chains restored: {n_chains}")

    sge_quest_fids = _scan_startable_quests(by_type)

    quest_edid_by_fid, quest_fid_by_edid, quest_dialog_ctdas = \
        _quest_lookup_tables(by_type, dials, fid_to_edid, generic_quest_fid,
                             offset, script_vars)

    # VTYP FormID -> EditorID, so an NPC-specific line can record the folder its
    # speaker's voice type resolves to (voice files are relocated there).
    # Read the mapping the writer RECORDED, never rebuild it from
    # CUSTOM_VTYP_EDIDS: that table is Oblivion's race list, so walking it
    # backwards labels a FormID with whatever race name happens to point at it.
    # After localised races join the map, `VOICE_TYPE_MAP[('HighElf','Male')]`
    # is the *Hochelf* voice type — and the reconstruction stamped it
    # `TES4MaleHighElf`, sending 42 Nehrim voice files to a folder no speaker
    # ever reads.  It also cannot see a VTYP no fixed race points at.
    from ..base.equivalents import (CUSTOM_VTYP_EDIDS, VOICE_TYPE_MAP,
                                   VTYP_EDID_BY_FID)
    vtyp_edid_by_fid = dict(VTYP_EDID_BY_FID)
    if not vtyp_edid_by_fid:        # no VTYPs written this run (override plugin)
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
    EMPTY_DIAL_FIDS.clear()
    EMPTY_DIAL_FIDS.update(
        get_formid(d, 'FormID') for d in dials
        if not info_by_dial.get(get_formid(d, 'FormID'))
        and not service_menu_kind(d))

    # (_SAY_TOPIC_DISPOSITIONS is built in the pre-scan above — the NPC-to-NPC
    # drop needs it before the first should_skip_dial call.)

    tclt_targets = collect_tclt_target_fids(by_type)
    bark_dial_fids, bark_choice_targets, bark_choice_gate = \
        _scan_bark_choice_links(dials, infos, offset, script_vars)
    unlock_plan = unlock_plan or {'gated': {}, 'info_reveals': {},
                                  'stage_reveals': {}}
    unlock_globals = unlock_globals or {}

    quest_npc_fids = _quest_npc_sets(dials, info_by_dial)

    print(f"  Dialogue: {len(dials)} topics, {len(infos)} infos, "
          f"{len(npc_to_vtyp)} NPC->VTYP, {len(unlock_plan['gated'])} "
          f"AddTopic-gated topics, {len(unlock_plan['info_reveals'])} "
          f"revealer INFOs")

    stats = defaultdict(int)
    view_branches = defaultdict(list)
    view_topics = defaultdict(list)
    bark_dials = []

    topic_args = (info_by_dial, writer, offset, generic_quest_fid,
                  tclt_targets, bark_choice_targets, bark_choice_gate,
                  unlock_plan, unlock_globals, npc_to_vtyp,
                  quest_npc_fids, sge_quest_fids, quest_edid_by_fid,
                  quest_priority, voice_map,
                  fid_to_edid, xref, well_known_props,
                  quest_dialog_ctdas, vtyp_edid_by_fid, stats, script_vars,
                  quest_fid_by_edid)
    all_dial_content, all_dlbr = _emit_conversation_topics(
        dials, topic_args, bark_dials, conv_synth_fids,
        view_branches, view_topics, stats)

    # --- Global bark pass: one topic per (owning quest, subtype) ---
    bark_ctx = dict(
        npc_to_vtyp=npc_to_vtyp, sge_quest_fids=sge_quest_fids,
        offset=offset, unlock_plan=unlock_plan,
        unlock_globals=unlock_globals, fid_to_edid=fid_to_edid,
        well_known_props=well_known_props, xref=xref, voice_map=voice_map,
        quest_edid_by_fid=quest_edid_by_fid, quest_priority=quest_priority,
        quest_fid_by_edid=quest_fid_by_edid,
        quest_dialog_ctdas=quest_dialog_ctdas, vtyp_edid_by_fid=vtyp_edid_by_fid,
        bark_dial_fids=bark_dial_fids, stats=stats, script_vars=script_vars)
    bark_content, bark_sge = _build_bark_pass(
        bark_dials, info_by_dial, writer,
        bark_generic_quests, bark_ctx)
    all_dial_content += bark_content

    # --- NPC-conversation driver quest (all topic FormIDs now known) ---
    if conv_plan and conv_plan['chains']:
        conv_qfid = make_conversation_quest(
            writer, conv_plan, conv_synth_fids,
            bark_ctx.get('bark_topic_fids', {}), offset, plugin_stem)
        bark_sge = bark_sge | {conv_qfid}

    # --- One DLVW per owning quest ---
    all_dlvw = b''
    for qfid, branches in view_branches.items():
        dlvw_fid = writer.derive_formid('DLVW', qfid)
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
    return ({generic_quest_fid} if generic_is_ours else set()) | bark_sge


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


def _service_gate(service_kind: str) -> bytes:
    """The CTDAs gating a Barter/Training menu topic, or b''.

    Two ANDed conditions: WHO offers the service (merchant marker faction for
    barter, trainer faction for training) and whether they offer it right now.

    See: docs/commentary/tes5_import_dialogue.md#branches-views-topic-ownership
    """
    from ..record_types.actor_common import (get_merchant_faction_fid,
                                             get_trainer_faction_fid)
    gate_fid = (get_merchant_faction_fid() if service_kind == 'barter'
                else get_trainer_faction_fid())
    if not gate_fid:
        return b''
    return (pack_subrecord('CTDA', build_ctda(
        FUNC_GET_IN_FACTION, param1=gate_fid))
        + pack_subrecord('CTDA', build_ctda(FUNC_GET_OFFERS_SERVICES_NOW)))


def _info_gate_bytes(info_rec, owner_qfid, ctx):
    """One INFO's (quest-running, quest-level, bark-choice) gate bytes.

    Oblivion gates an INFO on its own QSTI quest and on the QUST's own CTDAs;
    Skyrim has neither gate, so both ride on the INFO.  A speak-as line drops
    the actor-only conditions, and an inherited greeting gate is skipped when
    the INFO already states its own quest timing.

    See: docs/commentary/tes5_import_dialogue.md#voice-types-conditions
    """
    quest_gate_bytes = b''
    info_qfid = (get_formid(info_rec, 'QSTI.Quest')
                 or ctx['orig_quest_fid'])
    if (info_qfid and info_qfid not in ctx['sge_quest_fids']
            and info_qfid != owner_qfid):
        quest_gate_bytes = pack_subrecord('CTDA', build_ctda(
            FUNC_GET_QUEST_RUNNING, param1=info_qfid))

    quest_cond_bytes = ctx['quest_dialog_ctdas'].get(info_qfid, b'')
    if quest_cond_bytes and _is_speak_as(info_rec):
        quest_cond_bytes = _drop_non_actor_speaker_ctdas(quest_cond_bytes)
    if quest_cond_bytes:
        ctx['stats']['quest_cond_gated'] += 1

    bc_gate = ctx.get('bark_choice_gate_bytes', b'')
    if bc_gate and has_quest_state_condition(info_rec):
        bc_gate = b''
    if bc_gate:
        ctx['stats']['bark_choice_gated'] = \
            ctx['stats'].get('bark_choice_gated', 0) + 1
    return quest_gate_bytes, quest_cond_bytes, bc_gate


def _record_voice_entry(info_rec, owner_qfid, ctx) -> None:
    """Record one INFO's voice-file prefix and response transcripts.

    The prefix is built from the OWNING quest's EditorID; an NPC-specific line
    also names the voice-type folders its speakers resolve to, because
    Oblivion filed some recordings under the wrong race directory.

    See: docs/commentary/tes5_import_dialogue.md#voice-files-lip-sync-audio
    """
    info_fid = get_formid(info_rec, 'FormID')
    prefix = voice_file_prefix(
        ctx['quest_edid_by_fid'].get(owner_qfid, ''), ctx['edid'])
    for ri in range(get_int(info_rec, 'ResponseCount')):
        rtext = get_str(info_rec, f'Response[{ri}].ResponseText')
        rnum = (get_int(info_rec, f'Response[{ri}].ResponseNumber')
                or (ri + 1))
        if rtext:
            lip_texts[(info_fid & 0xFFFFFF, rnum)] = rtext
    own_npcs = read_getisid_fids(info_rec, offset=ctx['offset'],
                                 positive_only=True)
    vt_edids = sorted({
        ctx['vtyp_edid_by_fid'].get(ctx['npc_to_vtyp'][n], '')
        for n in own_npcs if n in ctx['npc_to_vtyp']} - {''})
    if vt_edids:
        prefix = prefix + '\t' + ','.join(vt_edids)
    ctx['voice_map'][info_fid & 0xFFFFFF] = prefix


def _build_one_topic(dial_rec, info_by_dial, writer, offset,
                     generic_quest_fid, tclt_targets, bark_choice_targets,
                     bark_choice_gate,
                     unlock_plan,
                     unlock_globals, npc_to_vtyp,
                     quest_npc_fids, sge_quest_fids, quest_edid_by_fid,
                     quest_priority, voice_map,
                     fid_to_edid, xref, well_known_props,
                     quest_dialog_ctdas, vtyp_edid_by_fid, stats,
                     script_vars=None, quest_fid_by_edid=None):
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
        service_gate_bytes = _service_gate(service_kind)
        if not service_gate_bytes:
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
    #
    # ...but only when that quest can ever RUN.  Oblivion's QSTI is an
    # organisational grouping the engine never gates on, so a topic filed under
    # a quest nothing starts still worked there; Skyrim's QNAM is a hard
    # runtime gate, so the same topic would be permanently dead.  Those fall
    # back to the always-running generic quest, exactly like a shared topic.
    # (Nehrim's MQ01Topic01 is filed under the vestigial MQ01, and its INFO
    # holds the only `SetStage MQ00 65` — MQ00's completion stage.)
    orig_quest_fid = get_formid(dial_rec, 'Quest[0]')
    quest_count = get_int(dial_rec, 'QuestCount')
    if (orig_quest_fid and quest_count <= 1
            and (not startable_quests or orig_quest_fid in startable_quests)):
        owner_qfid = orig_quest_fid
    else:
        owner_qfid = generic_quest_fid

    # --- DLBR (conversation topics only; barks have no branch) ---
    dlbr_fid = 0
    dlbr_bytes = b''
    if not is_bark and child_infos:
        is_linked = _branch_is_linked(
            dial_rec, dial_fid, tclt_targets, bark_choice_targets,
            unlock_plan, stats)
        dlbr_fid = writer.derive_formid('DLBR', dial_fid)
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

    bark_gate_bytes = bark_choice_gate_bytes(
        bark_choice_gate.get(dial_fid, []))

    # A conditionless line in a topic whose every other line shares a world-
    # state gate inherits that gate. Oblivion could leave the line loose because
    # the TOPIC was AddTopic-gated; Skyrim has no such implicit scoping, so the
    # loose line would keep the topic alive forever (see shared_state_conditions).
    shared_state_bytes = b''
    if not is_bark and not service_kind:
        parts = []
        for raw_hex in shared_state_conditions(child_infos):
            try:
                ctda = convert_ctda(bytes.fromhex(raw_hex), offset)
            except (ValueError, struct.error):
                continue
            if ctda is not None:
                parts.append(pack_subrecord('CTDA', ctda))
        shared_state_bytes = b''.join(parts)

    # Chargen fail-open fallback ids: fixed slots in the reserved gap
    # (base+0x60..0x7F), shared across every topic of this import run via
    # the writer, so adding them can never shift an allocated id.
    if not hasattr(writer, 'chargen_fallback_box'):
        _cb = getattr(writer, 'chargen_fid_base', 0)
        writer.chargen_fallback_box = ([_cb + 0x60, _cb + 0x80] if _cb
                                       else None)

    # Shared context passed to the per-INFO converter.
    info_ctx = dict(
        chargen_fallback_fids=writer.chargen_fallback_box,
        is_bark=is_bark, npc_to_vtyp=npc_to_vtyp, topic_vtyps=topic_vtyps,
        topic_npc_fids=topic_npc_fids, service_gate_bytes=service_gate_bytes,
        unlock_gate_bytes=unlock_gate_bytes,
        shared_state_bytes=shared_state_bytes,
        bark_choice_gate_bytes=bark_gate_bytes, service_kind=service_kind,
        orig_quest_fid=orig_quest_fid, sge_quest_fids=sge_quest_fids,
        offset=offset, unlock_plan=unlock_plan,
        unlock_globals=unlock_globals, fid_to_edid=fid_to_edid,
        well_known_props=well_known_props, xref=xref, voice_map=voice_map,
        quest_edid_by_fid=quest_edid_by_fid, edid=edid,
        quest_fid_by_edid=quest_fid_by_edid,
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
    # Chargen-choice fail-open bookkeeping — see _strip_chargen_choice_gate.
    chargen_gated = 0
    first_gated_bytes = None
    for info_rec in child_infos:
        try:
            quest_gate_bytes, quest_cond_bytes, bc_gate = _info_gate_bytes(
                info_rec, owner_qfid, ctx)
            injected = _build_injected_ctdas(
                info_rec, ctx['is_bark'], ctx['npc_to_vtyp'],
                ctx['topic_vtyps'], ctx['topic_npc_fids'],
                ctx['service_gate_bytes'] + quest_gate_bytes + quest_cond_bytes
                + bc_gate,
                ctx['unlock_gate_bytes'], ctx['offset'], ctx['stats'],
                sibling_factions=ctx.get('sibling_factions'),
                sibling_npcs=ctx.get('sibling_npcs'),
                shared_state_bytes=ctx.get('shared_state_bytes', b''))
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
            info_bytes = convert_INFO(
                info_rec, injected_ctdas=injected,
                fid_to_edid=ctx['fid_to_edid'],
                well_known_props=ctx['well_known_props'], xref=ctx['xref'],
                reveal_props=reveal_props, service_menu=ctx['service_kind'],
                bark_dial_fids=(ctx.get('bark_dial_fids')
                                if ctx['is_bark'] else None),
                script_vars=ctx.get('script_vars'))
            topic_children += info_bytes
            child_count += 1
            ctx['stats']['infos'] += 1
            if _has_chargen_choice_cond(info_rec):
                chargen_gated += 1
                if first_gated_bytes is None:
                    first_gated_bytes = info_bytes
            if ctx['voice_map'] is not None:
                _record_voice_entry(info_rec, owner_qfid, ctx)
        except Exception as e:
            print(f"  ERROR info under {ctx['edid'] or '?'}: {e}")

    # An ALL-gated chargen-choice topic gets an ungated fail-open fallback
    # appended LAST (engine walks INFOs in order, so the fallback only
    # speaks when no gated line passes) — see _strip_chargen_choice_gate.
    fid_box = ctx.get('chargen_fallback_fids')
    if (fid_box and first_gated_bytes is not None
            and chargen_gated == child_count and child_count > 0
            and fid_box[0] < fid_box[1]):
        from ..base.conditions import CHARGEN_CHOICE
        glob_fids = {g for g, _m in CHARGEN_CHOICE.values()}
        fallback = _strip_chargen_choice_gate(
            first_gated_bytes, fid_box[0], glob_fids)
        if fallback:
            fid_box[0] += 1
            topic_children += fallback
            child_count += 1
            ctx['stats']['infos'] += 1
            ctx['stats']['chargen_fallbacks'] = \
                ctx['stats'].get('chargen_fallbacks', 0) + 1
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


def _build_bark_pass(bark_dials, info_by_dial, writer,
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
                owner_qfid = raw_q
            else:
                # Synthetic per-subtype generic quest (created once).
                snam_code = snam.decode('latin1')
                qkey = f'TES4Generic{snam_code}'
                if qkey not in bark_generic_quests:
                    qfid = make_generic_quest(
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
    # donor source DIAL, but a DIAL FormID can be claimed by only one group —
    # and only when this plugin OWNS the donor. A dependent plugin's bark
    # INFOs point at its MASTER's shared GREETING/HELLO record; reusing that
    # fid here emits an OVERRIDE of the master's topic, re-keyed to this
    # plugin's quest. Morrowind_ob shipped Oblivion's GREETING_0102466E
    # re-keyed to its own chargen quest (twice), which clobbered the
    # CharacterGen Emperor's greeting topic — his choices vanished and only
    # 'Rumors' survived whenever Morrowind_ob was loaded.
    claimed = set()
    content = b''
    for key in order:
        owner_qfid, subtype = key
        g = groups[key]
        src_fid = g['src_fid']
        if src_fid not in claimed and \
                (src_fid >> 24) == writer.own_index:
            this_dial_fid = src_fid
            claimed.add(src_fid)
        else:
            this_dial_fid = writer.derive_formid('BARK_DIAL', key)
        this_edid = f"{g['edid']}_{owner_qfid:08X}" if g['edid'] else \
            f"TES4Bark_{subtype}_{owner_qfid:08X}"
        # Remember this quest's GREETING so a ForceGreet package can open it.
        if (g['edid'] or '').upper() == 'GREETING':
            GREET_TOPIC_BY_QUEST.setdefault(owner_qfid, this_dial_fid)
        # (owner quest, subtype) -> output DIAL, so the NPC-conversation driver
        # can Say a bark-grouped hop (a chain's GOODBYE line lives in its
        # quest's GBYE group, not at the source GOODBYE DIAL's FormID).
        ctx.setdefault('bark_topic_fids', {})[key] = this_dial_fid

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
        # PNAM stays at the vanilla 50.0 DEFAULT — quest arbitration rides on
        # QUST.DNAM.Priority (see compute_quest_priorities), never here.
        # Skyrim.esm leaves PNAM at 50.0 on 659 of 664 Misc/greeting topics and
        # 5375 of 6535 player topics; greetings are NEVER ranked above the topic
        # list. Writing the quest priority here instead put FGC01Rats' GREETING
        # at PNAM 161 against its player topics' 50.0, and Pinarus lost every
        # topic he owned (mountain-lion AND training) — two unrelated topics on
        # one NPC, which no per-topic condition bug could explain.
        dial_bytes = convert_DIAL(
            g['src'], info_count=child_count, dlbr_fid=0,
            quest_fid=owner_qfid, category=g['cat'], subtype=subtype,
            snam=g['snam'], edid_override=this_edid,
            formid_override=this_dial_fid)
        content += dial_bytes
        content += pack_group(7, struct.pack('<I', this_dial_fid),
                              topic_children)
        ctx['stats']['bark_topics'] = ctx['stats'].get('bark_topics', 0) + 1

    return content, sge_extra


# Response text for the synthetic catch-all service INFOs (silent subtitle —
# no Oblivion audio exists for them, like the fallback greetings).
SERVICE_FALLBACK_TEXT = {
    'barter': 'Take a look.',
    'training': "Let's begin.",
}


def _has_chargen_choice_cond(info_rec: dict) -> bool:
    """True when this INFO carries a raw TES4 chargen-identity condition
    (GetIsPlayerBirthsign 224 / GetPCIsClass 129) that convert_ctda rewrites
    to a GetGlobalValue read of the menu-choice global."""
    from ..base.conditions import CHARGEN_CHOICE
    if not CHARGEN_CHOICE:
        return False
    i = 0
    while True:
        raw = info_rec.get(f'Condition[{i}].Raw')
        if raw is None:
            return False
        i += 1
        try:
            b = bytes.fromhex(raw)
        except ValueError:
            continue
        if len(b) >= 12 and \
                struct.unpack_from('<H', b, 8)[0] in CHARGEN_CHOICE:
            return True


def _strip_chargen_choice_gate(record_bytes: bytes, new_fid: int,
                               glob_fids: set) -> bytes:
    """A copy of a converted INFO with its choice-global CTDA removed and a
    new FormID — the fail-open FALLBACK for an all-gated chargen topic.

    The Emperor's post-birthsign topic is 13 INFOs, EVERY one gated
    GetGlobalValue(<choice>) == index+1.  Skyrim HIDES a topic whose INFOs
    all fail, so any miss in the choice chain (the menu never shown, a
    Show() display failure, an old save) left the player with no way to
    continue the conversation — the stage-45 handoff lives in these INFOs'
    fragments, and the quest soft-locked.  TES4 could not fail this way: its
    engine resolved GetIsPlayerBirthsign natively and the player always has
    a birthsign.  Appending an ungated copy of the FIRST line restores that
    always-continues contract: the engine walks INFOs in order, so a valid
    choice still selects its matching gated line, and the fallback only
    speaks when nothing else can.  It keeps the source INFO's VMAD, so the
    original's End fragment (SetStage etc.) runs identically.
    """
    sig, data_size, flags, _fid, vcs, ver, unk = struct.unpack_from(
        '<4sIIIIHH', record_bytes, 0)
    if sig != b'INFO' or flags & 0x40000:        # never compressed here
        return b''
    payload = record_bytes[24:24 + data_size]
    out = bytearray()
    off = 0
    while off + 6 <= len(payload):
        ssig = payload[off:off + 4]
        (ssize,) = struct.unpack_from('<H', payload, off + 4)
        chunk = payload[off:off + 6 + ssize]
        off += 6 + ssize
        if ssig == b'CTDA' and ssize == 32:
            func = struct.unpack_from('<H', chunk, 6 + 8)[0]
            p1 = struct.unpack_from('<I', chunk, 6 + 12)[0]
            if func == 74 and p1 in glob_fids:
                continue
        out += chunk
    return struct.pack('<4sIIIIHH', b'INFO', len(out), flags, new_fid,
                       vcs, ver, unk) + bytes(out)


def _build_service_fallback_info(writer, service_kind: str,
                                 service_gate_bytes: bytes) -> bytes:
    """A minimal always-passing (for service NPCs) INFO that opens the menu."""
    from script_convert.pipeline import build_vmad_info_fragment
    info_fid = writer.derive_formid('SERVICE_INFO', service_kind)
    s = pack_subrecord('VMAD', build_vmad_info_fragment(
        '', script_name=SERVICE_MENU_SCRIPTS[service_kind]))
    s += pack_subrecord('ENAM', struct.pack('<HH', 0, 0))
    s += pack_subrecord('CNAM', struct.pack('<B', 0))
    s += pack_subrecord('TRDT', struct.pack('<IiI B3x I B3x', 0, 50, 0, 1, 0, 1))
    s += pack_string_subrecord('NAM1', SERVICE_FALLBACK_TEXT[service_kind])
    s += pack_string_subrecord('NAM2', '')
    s += pack_string_subrecord('NAM3', '')
    s += service_gate_bytes
    return pack_record('INFO', info_fid, 0, s)


def _is_speak_as(info_rec: dict) -> bool:
    """True when this INFO belongs to a speak-as topic."""
    from ..base.conditions import is_speak_as_record
    return is_speak_as_record(info_rec)


def _drop_non_actor_speaker_ctdas(cond_bytes: bytes) -> bytes:
    """Remove subject-run actor-identity CTDAs from a packed condition block.

    Used only on QUST-level conditions inherited by a speak-as INFO, whose
    speaker is a non-actor reference (see _NON_ACTOR_SPEAKER_DROP).  Walks the
    packed CTDA/CIS2 subrecords, drops the offending conditions with any CIS2
    that trails them, and clears a dangling OR flag on whatever ends up last --
    the same OR repair convert_ctda_list does.
    """
    from ..base.conditions import NON_ACTOR_SPEAKER_DROP, CTDA_OR, CTDA_RUN_ON_TARGET
    from ..record_types.common import pack_subrecord
    kept, pos = [], 0
    while pos + 6 <= len(cond_bytes):
        sig = cond_bytes[pos:pos + 4]
        size = struct.unpack_from('<H', cond_bytes, pos + 4)[0]
        data = cond_bytes[pos + 6:pos + 6 + size]
        pos += 6 + size
        if sig == b'CTDA' and len(data) >= 32:
            type_byte = data[0]
            func = struct.unpack_from('<H', data, 8)[0]
            run_on = struct.unpack_from('<I', data, 20)[0]
            if (func in NON_ACTOR_SPEAKER_DROP and run_on == 0
                    and not (type_byte & CTDA_RUN_ON_TARGET)):
                # Skip a CIS2/CIS1 that belongs to this condition.
                if (pos + 6 <= len(cond_bytes)
                        and cond_bytes[pos:pos + 4] in (b'CIS1', b'CIS2')):
                    nsz = struct.unpack_from('<H', cond_bytes, pos + 4)[0]
                    pos += 6 + nsz
                continue
        kept.append((sig, data))
    if not kept:
        return b''
    # Repair: a trailing OR flag with nothing after it is invalid.
    for idx in range(len(kept) - 1, -1, -1):
        if kept[idx][0] == b'CTDA':
            sig, data = kept[idx]
            if data[0] & CTDA_OR:
                kept[idx] = (sig, bytes([data[0] & ~CTDA_OR]) + data[1:])
            break
    # `sig` is a 4-byte slice off the packed buffer; pack_subrecord takes str.
    return b''.join(pack_subrecord(sig.decode('ascii'), data)
                    for sig, data in kept)


def _build_injected_ctdas(info_rec, is_bark, npc_to_vtyp, topic_vtyps,
                          topic_npc_fids, quest_gate_bytes, unlock_gate_bytes,
                          offset, stats, sibling_factions=None,
                          sibling_npcs=None, shared_state_bytes=b''):
    """Build the Skyrim-required gates for one INFO, ordered for OR-chain safety.

    Order (outermost AND first): [quest-running gate] [AddTopic unlock gate]
    [voice-type OR-chain] [identity OR-chain]. Each OR-chain is internally
    isolated. Single-quest topics get no quest gate — quest ownership
    provides it natively.
    """
    # Voice types: from this INFO's own GetIsID NPCs, else the topic's voice set.
    own_npcs = read_getisid_fids(info_rec, offset=offset, positive_only=True)
    # A SPEAK-AS topic is spoken by the emitting reference -- an XMarker STAT,
    # a shrine ACTI, a door -- and a non-actor has NO voice type, so this gate
    # can never pass and every line in the topic is silent.  Keyed on the
    # TOPIC, never the NPC: SEThadon is a real actor who also lends his
    # identity to a marker-spoken shout, and keying on him stripped gates from
    # lines he speaks himself.  See talking_activators.py.
    from ..base.conditions import is_speak_as_record
    _speak_as_info = is_speak_as_record(info_rec)
    if _speak_as_info:
        vtyps = set()
    elif own_npcs:
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

    # Inherited topic state gate for a CONDITIONLESS conversation line (see
    # shared_state_conditions). Only when the line states nothing of its own —
    # a line with conditions already knows when it applies.
    state_bytes = b''
    if (not is_bark and shared_state_bytes
            and not has_any_conditions(info_rec)):
        state_bytes = shared_state_bytes
        stats['shared_state_gated'] = stats.get('shared_state_gated', 0) + 1

    if unlock_gate_bytes:
        stats['unlock_gated'] += 1
    if quest_gate_bytes:
        stats['quest_gated'] += 1

    from ..record_types.actor_common import get_origin_faction_fid
    origin_faction_fid = get_origin_faction_fid()
    origin_bytes = b''
    if origin_faction_fid and needs_origin_gate(info_rec):
        origin_bytes = build_or_chain(FUNC_GET_IN_FACTION,
                                      [origin_faction_fid])
        stats['origin_gated'] = stats.get('origin_gated', 0) + 1

    return (origin_bytes + quest_gate_bytes + unlock_gate_bytes + state_bytes
            + voice_bytes + id_bytes + sib_bytes)
