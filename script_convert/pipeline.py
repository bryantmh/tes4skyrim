"""Pipeline orchestration — convert all scripts, VMAD helpers, CLI."""

import argparse
import os
import re
import struct

from tes5_import.text_reader import parse_export_file

from script_convert.constants import (_PAPYRUS_RESERVED, _RECORD_TYPE_PAPYRUS, _GLOBAL_CANONICAL,
                                     _sanitize_name, _safe_property_name, _canonical_global,
                                     _record_type_to_papyrus, papyrus_script_name,
                                     PAPYRUS_MAX_SCRIPT_NAME)
from script_convert.cross_ref import CrossRefGraph
from script_convert.converter import ScriptConverter


# ===========================================================================
# High-level conversion functions
# ===========================================================================

def convert_all_scripts(export_dir: str, output_dir: str, workers: int = None) -> dict:
    """Convert all TES4 scripts from export directory to Papyrus .psc files.

    Args:
        export_dir: Path to export/Oblivion.esm (contains .txt files)
        output_dir: Path to write .psc files
        workers: Number of worker threads (default: cpu_count-1)

    Returns dict with conversion statistics.
    """
    if workers is None:
        workers = max(1, (os.cpu_count() or 4) - 1)

    os.makedirs(output_dir, exist_ok=True)

    # Deploy static scripts (TES4Polyfill + shared service-menu fragments) so
    # they compile alongside the generated ones.
    static_dir = os.path.join(os.path.dirname(__file__), 'static_scripts')
    if os.path.isdir(static_dir):
        import shutil
        for name in os.listdir(static_dir):
            if name.endswith('.psc'):
                shutil.copy2(os.path.join(static_dir, name),
                             os.path.join(output_dir, name))

    # Phase 1: Build cross-reference graph
    print('  Building cross-reference graph...')
    xref = CrossRefGraph()
    xref.load_from_export(export_dir)
    print(f'    {len(xref.formid_to_edid)} FormID->EditorID mappings')
    print(f'    {len(xref.script_formid_to_edid)} scripts, {len(xref.quest_edids)} quests')

    # Phase 1.5: Analyze cross-script ref-as-int patterns
    scpt_path = os.path.join(export_dir, 'SCPT.txt')
    if os.path.exists(scpt_path):
        xref.build_ref_as_int_map(scpt_path)
        if xref.ref_as_int:
            print(f'    {len(xref.ref_as_int)} ref variables detected as integer-only (cross-script)')

    # Phase 1.6: AddTopic unlock plan — MUST be the same analysis the importer
    # runs, so the SetValue lines in the generated fragments match the VMAD
    # property bindings and GLOB records written into the ESM.
    from tes5_import.dialog_unlocks import build_unlock_plan
    by_type = {}
    for sig in ('DIAL', 'INFO', 'QUST'):
        path = os.path.join(export_dir, f'{sig}.txt')
        by_type[sig] = parse_export_file(path) if os.path.exists(path) else []
    unlock_plan = build_unlock_plan(by_type)
    print(f'    AddTopic unlocks: {len(unlock_plan["gated"])} gated topics, '
          f'{len(unlock_plan["info_reveals"])} revealer INFOs')

    stats = {
        'scpt_total': 0, 'scpt_ok': 0, 'scpt_err': 0,
        'info_total': 0, 'info_ok': 0, 'info_err': 0,
        'qust_total': 0, 'qust_ok': 0, 'qust_err': 0,
        'todo_count': 0, 'errors': [],
    }

    # Phase 2: Convert SCPT records
    scpt_path = os.path.join(export_dir, 'SCPT.txt')
    if os.path.exists(scpt_path):
        print('  Converting SCPT records...')
        _convert_scpt_records(scpt_path, output_dir, xref, stats)

    # Service-menu topics (Barter/Training): INFOs under them whose fragment
    # is generated here must ALSO open the Skyrim menu — the importer attaches
    # the shared static script only to INFOs WITHOUT their own fragment.
    from tes5_import.dialog_converter import SERVICE_MENU_TOPICS, DIAL_TYPE_SERVICE
    service_topics = {}
    for rec in by_type.get('DIAL', []):
        edid = rec.get('EditorID', '')
        if (edid in SERVICE_MENU_TOPICS
                and rec.get('DATA.Type', '') == str(DIAL_TYPE_SERVICE)):
            service_topics[rec.get('FormID', '')] = SERVICE_MENU_TOPICS[edid][0]

    # Phase 3: Convert INFO result scripts
    info_path = os.path.join(export_dir, 'INFO.txt')
    if os.path.exists(info_path):
        print('  Converting INFO result scripts...')
        _convert_info_scripts(info_path, output_dir, xref, stats,
                              info_reveals=unlock_plan['info_reveals'],
                              service_topics=service_topics)

    # Phase 4: Convert QUST stage scripts
    qust_path = os.path.join(export_dir, 'QUST.txt')
    if os.path.exists(qust_path):
        print('  Converting QUST stage scripts...')
        _convert_qust_scripts(qust_path, output_dir, xref, stats,
                              stage_reveals=unlock_plan['stage_reveals'])

    total = stats['scpt_ok'] + stats['info_ok'] + stats['qust_ok']
    errs = stats['scpt_err'] + stats['info_err'] + stats['qust_err']
    print(f'\n  Script conversion complete:')
    print(f'    SCPT: {stats["scpt_ok"]}/{stats["scpt_total"]} converted')
    print(f'    INFO: {stats["info_ok"]}/{stats["info_total"]} fragments')
    print(f'    QUST: {stats["qust_ok"]}/{stats["qust_total"]} stage scripts')
    print(f'    Total: {total} converted, {errs} errors, {stats["todo_count"]} TODOs')

    _write_report(output_dir, stats)
    return stats


def _convert_scpt_records(scpt_path: str, output_dir: str, xref: CrossRefGraph, stats: dict):
    """Convert all SCPT records from the export file."""
    records = parse_export_file(scpt_path)
    stats['scpt_total'] = len(records)

    for rec in records:
        formid = rec.get('FormID', '')
        edid = rec.get('EditorID', '')
        sctx = rec.get('SCTX', '')
        if not sctx or not sctx.strip():
            continue

        try:
            extends = xref.get_extends_class(formid)
            conv = ScriptConverter(xref)
            # Pre-populate external references from SCRO entries
            _preload_scro_refs(conv, rec, xref)
            name = _sanitize_name(edid or f'Script_{formid}')
            papyrus = conv.convert_standalone(name, sctx, extends, edid)

            # The FILENAME must match the ScriptName the converter emitted, or
            # the compiler cannot find the script by name.
            out_path = os.path.join(output_dir, papyrus_script_name(name) + '.psc')
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(papyrus)
            stats['scpt_ok'] += 1
            stats['todo_count'] += papyrus.count(';TODO')
        except Exception as e:
            stats['scpt_err'] += 1
            stats['errors'].append(f'SCPT {edid} ({formid}): {e}')


# Fragment lines that open the Skyrim service menus (appended to scripted
# INFOs under the Barter/Training topics; script-less ones get the shared
# static scripts of the same content instead).
_SERVICE_MENU_CALL = {
    'barter': '  (akSpeakerRef as Actor).ShowBarterMenu()',
    'training': '  Game.ShowTrainingMenu(akSpeakerRef as Actor)',
}


def _convert_info_scripts(info_path: str, output_dir: str, xref: CrossRefGraph,
                          stats: dict, info_reveals: dict = None,
                          service_topics: dict = None):
    """Convert INFO result scripts to TopicInfo fragment .psc files.

    info_reveals ({info_fid24: [unlock global names]}) marks AddTopic revealer
    INFOs: their OnEnd fragment sets the unlock globals (a fragment is
    generated even when the INFO has no result script). Must stay in sync with
    the VMADs the importer writes (same unlock plan).

    service_topics ({dial_formid_str: 'barter'|'training'}) marks the service-
    menu topics; fragments for their INFOs also open the corresponding menu.
    """
    records = parse_export_file(info_path)
    info_reveals = info_reveals or {}
    service_topics = service_topics or {}

    for rec in records:
        result_script = rec.get('ResultScript', '')
        has_script = bool(result_script and result_script.strip())
        formid = rec.get('FormID', '')
        try:
            fid24 = int(formid, 16) & 0xFFFFFF
        except (TypeError, ValueError):
            fid24 = 0
        reveals = info_reveals.get(fid24, [])
        service_kind = service_topics.get(rec.get('ParentDIAL', ''), '')
        if not has_script and not reveals:
            # Script-less service-menu INFOs use the shared static scripts.
            continue

        if has_script:
            stats['info_total'] += 1

        try:
            body_lines = []
            prop_refs = {}
            if has_script:
                conv = ScriptConverter(xref)
                _preload_scro_refs(conv, rec, xref)
                body_lines = conv.convert_fragment(result_script, 'TopicInfo')
                prop_refs = dict(conv._property_refs)

            script_name = f'TES4_TIF__{formid}'
            out_lines = [
                f'ScriptName {script_name} extends TopicInfo Hidden',
                '',
            ]
            declared = set()
            for gname in reveals:
                declared.add(gname.lower())
                out_lines.append(f'GlobalVariable Property {gname} Auto')
            if prop_refs:
                for pname, ptype in sorted(prop_refs.items()):
                    safe = _safe_property_name(pname)
                    if safe.lower() in declared:
                        continue
                    declared.add(safe.lower())
                    out_lines.append(f'{ptype} Property {safe} Auto')
            if declared:
                out_lines.append('')
            out_lines.append('Function Fragment_0(ObjectReference akSpeakerRef)')
            # Unlock the AddTopic-revealed topics first — OnEnd fires when the
            # line finishes, right before the topic menu refreshes.
            for gname in reveals:
                out_lines.append(f'  {gname}.SetValue(1)')
            out_lines.extend(body_lines)
            if service_kind:
                out_lines.append(_SERVICE_MENU_CALL[service_kind])
            out_lines.append('EndFunction')
            out_lines.append('')

            papyrus = '\n'.join(out_lines)
            out_path = os.path.join(output_dir, f'{script_name}.psc')
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(papyrus)
            if has_script:
                stats['info_ok'] += 1
            stats['todo_count'] += papyrus.count(';TODO')
        except Exception as e:
            stats['info_err'] += 1
            stats['errors'].append(f'INFO {formid}: {e}')


def _convert_qust_scripts(qust_path: str, output_dir: str, xref: CrossRefGraph,
                          stats: dict, stage_reveals: dict = None):
    """Convert QUST stage scripts to Quest fragment .psc files.

    A fragment is generated for every stage that has journal log text (CNAM),
    whether or not it also has a result script.  Each fragment calls
    SetObjectiveDisplayed / SetObjectiveCompleted so the quest appears in the
    Skyrim journal — without those calls CNAM text is never visible.

    stage_reveals ({(quest_edid_lower, stage): [unlock global names]}) marks
    stages whose TES4 result scripts contained `AddTopic X`: the fragment sets
    the unlock globals (the AddTopic command itself is a no-op in conversion).
    """
    records = parse_export_file(qust_path)
    stage_reveals = stage_reveals or {}

    for rec in records:
        edid = rec.get('EditorID', '')
        if not edid:
            continue

        stage_count_str = rec.get('StageCount', '0')
        try:
            stage_count = int(stage_count_str)
        except ValueError:
            continue

        # Collect all stages that need a fragment:
        # - stages with log text (need objective calls even if no result script)
        # - stages with result scripts (need script body)
        # Each entry: (stage_idx, log_idx, log_text, result_script, complete_flag, stage_arr_idx, log_arr_idx)
        fragments = []
        for i in range(stage_count):
            stage_idx_str = rec.get(f'Stage[{i}].Index', '0')
            try:
                stage_idx = int(stage_idx_str)
            except ValueError:
                continue

            log_count_str = rec.get(f'Stage[{i}].LogCount', '0')
            try:
                log_count = int(log_count_str)
            except ValueError:
                continue

            for j in range(log_count):
                log_text = rec.get(f'Stage[{i}].Log[{j}].Text', '')
                script = rec.get(f'Stage[{i}].Log[{j}].ResultScript', '')
                log_flags_str = rec.get(f'Stage[{i}].Log[{j}].Flags', '0')
                try:
                    log_flags = int(log_flags_str)
                except ValueError:
                    log_flags = 0
                complete_flag = bool(log_flags & 0x01)
                if log_text or (script and script.strip()):
                    fragments.append((stage_idx, j, log_text, script, complete_flag, i, j))

        if not fragments:
            continue

        # Count only fragments that have result scripts for stats
        scripted_count = sum(1 for f in fragments if f[3] and f[3].strip())
        stats['qust_total'] += scripted_count

        try:
            conv = ScriptConverter(xref)
            # Pre-populate external references from SCRO entries
            _preload_scro_refs(conv, rec, xref)
            script_name = papyrus_script_name(edid, 'TES4_QF_')
            out_lines = [
                f'ScriptName {script_name} extends Quest Hidden',
                '',
            ]

            for stage_idx, log_idx, log_text, script_src, complete_flag, stage_arr_idx, log_arr_idx in fragments:
                # Load per-stage SCROs for this fragment
                _preload_stage_scro_refs(conv, rec, xref, stage_arr_idx, log_arr_idx)
                func_name = f'Fragment_Stage_{stage_idx:04d}_Item_{log_idx}'
                out_lines.append(f'Function {func_name}()')
                # Objective tracking: make this stage's entry visible in the journal
                if log_text:
                    # Always display the objective first — Skyrim requires the
                    # objective to be in Displayed state before SetObjectiveCompleted
                    # will create a journal entry for it.
                    out_lines.append(f'  SetObjectiveDisplayed({stage_idx}, true)')
                    if complete_flag:
                        out_lines.append(f'  SetObjectiveCompleted({stage_idx}, true)')
                # AddTopic unlock globals revealed by this stage's TES4 script
                for gname in stage_reveals.get((edid.lower(), stage_idx), []):
                    out_lines.append(f'  {gname}.SetValue(1)')
                # Original result script body (if any)
                if script_src and script_src.strip():
                    body_lines = conv.convert_fragment(script_src, 'Quest')
                    out_lines.extend(body_lines)
                out_lines.append('EndFunction')
                out_lines.append('')

            # Insert property declarations after ScriptName line
            quest_globals = sorted({g for (q, _s), gs in stage_reveals.items()
                                    if q == edid.lower() for g in gs})
            for gi, gname in enumerate(quest_globals):
                out_lines.insert(2 + gi, f'GlobalVariable Property {gname} Auto')
            prop_refs = conv.get_property_refs()
            if prop_refs:
                # Merge case-variant keys: pick the most specific type (non-Quest wins)
                merged: dict[str, tuple[str, str]] = {}  # lower_name -> (canonical_name, type)
                for pname, ptype in sorted(prop_refs.items()):
                    key = pname.lower()
                    if key in merged:
                        existing_name, existing_type = merged[key]
                        # Keep the more specific type; prefer the first-seen
                        # (SCRO-canonical) name so it matches the VMAD binding.
                        if existing_type == 'Quest' and ptype != 'Quest':
                            merged[key] = (existing_name, ptype)
                        elif ptype == 'ActorBase' and existing_type != 'ActorBase':
                            # Base typing from a base-semantics function
                            # (SetEssential base) must win over ANY reference
                            # type — including Actor and Actor-derived TES4_*
                            # scripts. The VMAD binds this property to a base
                            # (NPC_/CREA) record, and a reference-typed property
                            # bound to a base is UNBINDABLE: Papyrus aborts the
                            # whole script's init, so the quest never finishes
                            # initialising and its aliases never fill. (FGC01Rats:
                            # QuillWeave, an NPC_ base, was typed as the Actor
                            # script TES4_FGC01QuillweaveScript.)
                            merged[key] = (existing_name, ptype)
                        # else: keep existing (already specific, or both Quest)
                    else:
                        merged[key] = (pname, ptype)
                insert_idx = 2  # After ScriptName + blank line
                declared = set()
                count = 0
                for pname, ptype in sorted(merged.values(), key=lambda x: x[0].lower()):
                    safe = _safe_property_name(pname)
                    if safe.lower() in declared:
                        continue
                    declared.add(safe.lower())
                    out_lines.insert(insert_idx + count, f'{ptype} Property {safe} Auto')
                    count += 1
                out_lines.insert(insert_idx + count, '')

            papyrus = '\n'.join(out_lines)
            out_path = os.path.join(output_dir, f'{script_name}.psc')
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(papyrus)
            stats['qust_ok'] += scripted_count
            stats['todo_count'] += papyrus.count(';TODO')
        except Exception as e:
            stats['qust_err'] += scripted_count
            stats['errors'].append(f'QUST {edid}: {e}')


def _write_report(output_dir: str, stats: dict):
    """Write a conversion summary report."""
    report_path = os.path.join(output_dir, '_CONVERSION_REPORT.txt')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('TES4 Script -> Papyrus Conversion Report\n')
        f.write('=' * 50 + '\n\n')
        f.write(f'SCPT records: {stats["scpt_ok"]}/{stats["scpt_total"]} converted\n')
        f.write(f'INFO fragments: {stats["info_ok"]}/{stats["info_total"]} converted\n')
        f.write(f'QUST stage scripts: {stats["qust_ok"]}/{stats["qust_total"]} converted\n')
        total = stats['scpt_ok'] + stats['info_ok'] + stats['qust_ok']
        errs = stats['scpt_err'] + stats['info_err'] + stats['qust_err']
        f.write(f'\nTotal: {total} converted, {errs} errors\n')
        f.write(f';TODO markers: {stats["todo_count"]}\n\n')

        if stats['errors']:
            f.write('Errors:\n')
            for err in stats['errors'][:100]:
                f.write(f'  {err}\n')
            if len(stats['errors']) > 100:
                f.write(f'  ... and {len(stats["errors"]) - 100} more\n')


# Player FormID — skip when pre-loading SCRO refs
_PLAYER_FORMID = '00000014'


def _preload_scro_refs(conv: 'ScriptConverter', rec: dict, xref: CrossRefGraph):
    """Pre-populate converter property_refs from SCRO entries in a record."""
    i = 0
    while True:
        key = f'SCRO[{i}]'
        fid = rec.get(key)
        if fid is None:
            break
        i += 1
        _add_scro_ref(conv, fid, xref)


def _preload_stage_scro_refs(conv: 'ScriptConverter', rec: dict, xref: CrossRefGraph,
                              stage_arr_idx: int, log_arr_idx: int):
    """Pre-populate converter property_refs from per-stage/log SCRO entries."""
    k = 0
    while True:
        key = f'Stage[{stage_arr_idx}].Log[{log_arr_idx}].SCRO[{k}]'
        fid = rec.get(key)
        if fid is None:
            break
        k += 1
        _add_scro_ref(conv, fid, xref)


def _add_scro_ref(conv: 'ScriptConverter', fid: str, xref: CrossRefGraph):
    """Add a single SCRO FormID as a property ref on the converter."""
    if fid == _PLAYER_FORMID:
        return
    edid = xref.formid_to_edid.get(fid)
    if not edid:
        return
    rtype = xref.record_type.get(fid, '')
    ptype = _record_type_to_papyrus(rtype)
    # Prefer attached SCPT-derived type for cross-script property accesses
    # (e.g. Arena.AnnounceWin). For QUST records, start with 'Quest' base type —
    # the specific type will be promoted later if the script body uses dot-notation
    # variable access (e.g. Arena.AnnounceWin) which the converter handles.
    if rtype != 'QUST':
        script_type = xref.get_record_script_type(edid)
        if script_type:
            ptype = script_type
    # Key on the Papyrus-SAFE name, which is what _convert_ref stores and what
    # _collect_scro_properties writes into the VMAD.  Keying on the raw EditorID
    # instead created a SECOND entry for any EditorID that gets renamed (MS14 is
    # a vanilla Skyrim script name, so it becomes myMS14): the generic 'Quest'
    # from this SCRO and the specific 'TES4_MS14Script' from _convert_ref lived
    # under different keys, so the downgrade guard below never fired and the
    # generic one won the declaration — leaving the body calling myMS14.QuestDone
    # on a plain Quest ("field or property QuestDone not found").
    key = _safe_property_name(edid)
    # Don't downgrade a type already upgraded by _convert_ref (e.g. Quest → TES4_FGQuestTrack).
    # _preload_stage_scro_refs is called once per stage and would otherwise reset types
    # that were promoted when a prior stage's result script accessed cross-script vars.
    cur = conv._property_refs.get(key, '')
    if cur and cur != 'Quest' and ptype == 'Quest':
        return
    # Never overwrite an ActorBase typing set by a base-semantics function
    # (SetEssential base). The SCRO here is the base record, so a reference /
    # Actor-script type would be UNBINDABLE against the base and abort the whole
    # script's init. ActorBase is a hard constraint, not a promotable guess.
    if cur == 'ActorBase':
        return
    conv._property_refs[key] = ptype


# ===========================================================================
# VMAD binary helpers (for tes5_import integration)
# ===========================================================================

def build_vmad_quest_fragments(quest_edid: str, stage_fragments: list[tuple[int, int]],
                               property_values: dict = None,
                               attached_script: tuple = None) -> bytes:
    """Build VMAD binary for a QUST record with stage script fragments and/or
    an attached quest script.

    Args:
        quest_edid: Quest EditorID
        stage_fragments: list of (stage_index, log_index) tuples; may be empty
            when only an attached script is present (vanilla then writes the
            fragments section with count=0 and an EMPTY file name — e.g.
            MS12PostQuest / WIThief01 in Skyrim.esm).
        property_values: optional dict {property_name: formid} for the QF
            fragment script's properties
        attached_script: optional (script_name, {prop: formid}) for the
            converted TES4 quest script (SCRI) to attach alongside

    Returns VMAD binary data.
    """
    script_name = papyrus_script_name(quest_edid, 'TES4_QF_')
    buf = bytearray()

    # VMAD header
    buf += struct.pack('<HH', 5, 2)  # version=5, objectFormat=2

    scripts = []
    if stage_fragments:
        scripts.append((script_name, property_values or {}))
    if attached_script:
        scripts.append(attached_script)

    buf += struct.pack('<H', len(scripts))
    for sname, props in scripts:
        buf += _pack_wstring(sname)
        buf += struct.pack('<B', 0)   # flags=0
        buf += struct.pack('<H', len(props))
        for pname, fid in props.items():
            buf += _pack_wstring(pname)
            buf += struct.pack('<BB', 1, 1)       # type=Object, status=Edited
            buf += struct.pack('<HhI', 0, -1, fid) # unused=0, alias=-1, FormID

    # Script fragments (quest type, wbScriptFragmentsQuest):
    #   S8  Extra bind data version = 2
    #   U16 FragmentCount
    #   LenString(U16) FileName
    buf += struct.pack('<b', 2)                  # Extra bind data version = 2
    buf += struct.pack('<H', len(stage_fragments))  # FragmentCount
    buf += _pack_wstring(script_name if stage_fragments else '')  # FileName
    for stage_idx, log_idx in stage_fragments:
        frag_name = f'Fragment_Stage_{stage_idx:04d}_Item_{log_idx}'
        buf += struct.pack('<H', stage_idx)   # Quest Stage (U16)
        buf += struct.pack('<h', 0)           # Unknown (S16)
        buf += struct.pack('<i', log_idx)     # Quest Stage Index = log entry index (S32)
        buf += struct.pack('<b', 1)           # Unknown (S8) — vanilla always 1
        buf += _pack_wstring(script_name)
        buf += _pack_wstring(frag_name)

    # Alias-script array (wbVMADFragmentedQUST: Version, ObjectFormat, Scripts,
    # ScriptFragmentsQuest, **Aliases**) — an S16 count followed by that many
    # alias-script entries.  A QUST VMAD is malformed without it, and the engine
    # parses VMAD strictly: running off the end of the buffer where it expects
    # this count aborts the record's whole script/alias binding, so EVERY quest
    # alias fills as NONE *and* every QF script property comes back None.  That
    # is the real reason converted quests showed a journal objective but never a
    # marker.  Verified against Skyrim.esm: vanilla QUST VMADs end with exactly
    # these two bytes (e.g. DBSideContract03's 643-byte VMAD parses to 643/643
    # only once the trailing count is read).  We attach no alias scripts, so 0.
    buf += struct.pack('<h', 0)

    return bytes(buf)


def build_vmad_info_fragment(info_formid: str, property_values: dict = None,
                             script_name: str = None) -> bytes:
    """Build VMAD binary for an INFO record with a result script fragment.

    Args:
        info_formid: INFO FormID string (e.g. "00012345")
        property_values: optional dict {property_name: formid} for script properties
        script_name: override the per-INFO TES4_TIF__ name with a shared static
            fragment script (e.g. TES4_ShowBarterMenu for service-menu INFOs)

    Returns VMAD binary data.
    """
    script_name = script_name or f'TES4_TIF__{info_formid}'
    buf = bytearray()

    # VMAD header
    buf += struct.pack('<HH', 5, 2)   # version=5, objectFormat=2

    # Attached scripts: 1 script with properties
    buf += struct.pack('<H', 1)       # 1 attached script
    buf += _pack_wstring(script_name)
    buf += struct.pack('<B', 0)       # flags=0
    # Properties
    if property_values:
        buf += struct.pack('<H', len(property_values))
        for pname, fid in property_values.items():
            buf += _pack_wstring(pname)
            buf += struct.pack('<BB', 1, 1)       # type=Object, status=Edited
            buf += struct.pack('<HhI', 0, -1, fid) # unused=0, alias=-1, FormID
    else:
        buf += struct.pack('<H', 0)   # propertyCount=0

    # Script fragments for INFO (wbScriptFragmentsInfo):
    #   S8  Extra bind data version = 2
    #   U8  Flags: bit0=OnBegin, bit1=OnEnd (no other bits defined for INFO)
    #   LenString(U16) FileName
    #   For each set bit in Flags, one fragment: S8 Unknown + LenString ScriptName + LenString FragmentName
    # Fragment count is implicit (popcount of Flags bits 0-1).
    buf += struct.pack('<b', 2)        # Extra bind data version = 2
    buf += struct.pack('<B', 0x02)     # Flags = OnEnd (1 fragment)
    buf += _pack_wstring(script_name)  # FileName

    # Fragment 0 — OnEnd
    buf += struct.pack('<B', 1)        # Unknown (always 1 in vanilla Skyrim.esm)
    buf += _pack_wstring(script_name)  # ScriptName
    buf += _pack_wstring('Fragment_0') # FragmentName

    return bytes(buf)


# Papyrus property object-type codes for the VMAD property record (objectFormat 2).
#   1 = Object (FormID + alias), 2 = wstring, 3 = Int32, 4 = Float, 5 = Bool
_VMAD_PROP_OBJECT = 1
_VMAD_PROP_INT = 3
_VMAD_PROP_FLOAT = 4
_VMAD_PROP_BOOL = 5


def build_vmad_object_script(script_name: str,
                             object_props: dict = None,
                             value_props: dict = None) -> bytes:
    """Build VMAD binary attaching a single Papyrus script to an object record.

    Unlike QUST/INFO VMADs this has NO fragment section — plain object scripts
    (ACTI/CONT/DOOR/FLOR/… on their placed instances or, as here, on the base
    record) run their own event handlers (OnActivate, OnLoad, …) directly.

    Args:
        script_name: full Papyrus script name (e.g. 'TES4_SE07AltarScript').
        object_props: {property_name: formid_int} — Object-typed properties
            bound to a record FormID (records/spells/quests/globals/actors).
        value_props: {property_name: (kind, value)} — literal-valued properties
            where kind is 'int' | 'float' | 'bool'.  Optional; usually the
            script's non-ref locals stay unbound and default to 0.

    Returns VMAD binary data (version 5, objectFormat 2).
    """
    object_props = object_props or {}
    value_props = value_props or {}
    buf = bytearray()

    # VMAD header
    buf += struct.pack('<HH', 5, 2)   # version=5, objectFormat=2

    # Attached scripts: exactly 1
    buf += struct.pack('<H', 1)
    buf += _pack_wstring(script_name)
    buf += struct.pack('<B', 0)       # flags=0

    total_props = len(object_props) + len(value_props)
    buf += struct.pack('<H', total_props)
    for pname, fid in object_props.items():
        buf += _pack_wstring(pname)
        buf += struct.pack('<BB', _VMAD_PROP_OBJECT, 1)   # type=Object, status=Edited
        buf += struct.pack('<HhI', 0, -1, fid)            # unused=0, alias=-1, FormID
    for pname, (kind, value) in value_props.items():
        buf += _pack_wstring(pname)
        if kind == 'float':
            buf += struct.pack('<BB', _VMAD_PROP_FLOAT, 1)
            buf += struct.pack('<f', float(value))
        elif kind == 'bool':
            buf += struct.pack('<BB', _VMAD_PROP_BOOL, 1)
            buf += struct.pack('<B', 1 if value else 0)
        else:  # int
            buf += struct.pack('<BB', _VMAD_PROP_INT, 1)
            buf += struct.pack('<i', int(value))

    return bytes(buf)


def _pack_wstring(s: str) -> bytes:
    """Pack a VMAD wstring: U16 length + UTF-8 bytes."""
    encoded = s.encode('utf-8')
    return struct.pack('<H', len(encoded)) + encoded


# ===========================================================================
# CLI
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(description='Convert TES4 scripts to Papyrus')
    parser.add_argument('export_dir', help='Path to export directory (e.g. export/Oblivion.esm)')
    parser.add_argument('-o', '--output', default=None,
                        help='Output dir for .psc files (default: output/oblivion.esm/scripts/source)')
    parser.add_argument('--workers', type=int, default=None, help='Worker threads')
    args = parser.parse_args()

    output_dir = args.output
    if output_dir is None:
        output_dir = os.path.join('output', 'oblivion.esm', 'scripts', 'source')

    convert_all_scripts(args.export_dir, output_dir, args.workers)


if __name__ == '__main__':
    main()
