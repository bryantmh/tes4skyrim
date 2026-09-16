"""Pipeline orchestration — convert all scripts, VMAD helpers, CLI."""

import argparse
import json
import os
import re
import struct

from core.worker_budget import worker_count

from asset_convert.game_paths import (current_namespace,
                                      set_namespace)
from script_convert.constants import (sanitize_name, papyrus_script_name,
                                     SERVICE_MENU_CALL, UDF_WIDE_TYPES,
                                     script_prefix)
from script_convert.conversation_sequence import (
    sequence_gate,
    split_counter_step,
    split_stage_advances,
    split_turn_handoff,
    state_writes_before_setstage,
    stepped_gate,
)
from script_convert.cross_ref import CrossRefGraph, master_names
from script_convert.converter import ScriptConverter
from script_convert.context_setup import (
    build_xref, chargen_menu_plan, deploy_static_scripts, load_bounds_cache,
    load_records, prepare_output_dir, quest_edids_by_fid,
    service_menu_topics, topic_unlock_globals)
from script_convert.message_menus import build_message_plan
from script_convert.commands_falloutnv import (quest_objective_indices,
                                               set_quest_objectives)
from script_convert.poll_interval import quest_script_delays
from script_convert.quest_fragments import (quest_fragment_psc,
                                            scripted_count, stage_fragments)
from script_convert.say_durations import scan_voice_durations
from script_convert.scro_refs import (preload_scro_refs, resolve_scro_aliases,
                                      scro_list)
from script_convert.symbols import property_declarations, IMPLICIT_NAMES
from script_convert.tes5.blocks import Kind, classify
from tes5_import.base.text_reader import info_result_script
from tes5_import.dialogue.conversations import (build_conversation_plan,
                                                build_script_chain_map,
                                                generate_driver_psc)
from tes5_import.dialogue.converter import (DIAL_TYPE_SERVICE,
                                            SERVICE_MENU_TOPICS)
from tes5_import.dialogue.unlocks import build_unlock_plan


# ===========================================================================
# Process-pool plumbing
#
# Script conversion is pure-Python CPU work (ScriptConverter holds the GIL),
# so batches run across a ProcessPoolExecutor. The read-only CrossRefGraph and
# plan dicts are shipped once per worker via the pool initializer; each job is
# a (kind, records) chunk whose .psc files the worker writes directly.
# ===========================================================================

_WORKER_CTX: dict = {}


def _new_stats() -> dict:
    return {
        'scpt_total': 0, 'scpt_ok': 0, 'scpt_err': 0,
        'info_total': 0, 'info_ok': 0, 'info_err': 0,
        'qust_total': 0, 'qust_ok': 0, 'qust_err': 0,
        'todo_count': 0, 'errors': [],
        # script name (lower) -> OBSE user-function parameter types, in order.
        # Collected AS each script converts, so the cross-script cast pass is
        # a lookup rather than a second read of every generated .psc.
        'udf_sigs': {},
        'udf_callers': {},
    }


def _load_music_cues(output_dir) -> dict:
    """{source_rel -> MUSC EditorID} for this plugin's converted music.

    Reads the same music_tracks.json the importer builds MUSC from, and derives
    the EditorID through the SHARED helper, so a StreamMusic property can only
    ever name a record the importer actually wrote.
    """
    from pathlib import Path as _P
    from .constants import music_cue_editor_id, music_type_editor_id

    # The manifest sits in the plugin's OUTPUT root; scripts are written to a
    # subfolder of it, so walk up until it turns up.
    d = _P(output_dir).resolve()
    for _ in range(4):
        cand = d / 'music_tracks.json'
        if cand.is_file():
            break
        d = d.parent
    else:
        return {}

    try:
        with open(cand, encoding='utf-8') as f:
            data = json.load(f)
        # Older builds wrapped the payload in a {'version','data'} envelope.
        data = data.get('data', data)
    except (AttributeError, ValueError, OSError):
        return {}

    plugin = data.get('plugin') or ''
    cues = {}
    for t in data.get('tracks') or []:
        rel = (t.get('source_rel') or '').lower()
        if not rel:
            continue
        cat = (t.get('category') or '').lower()
        # Special tracks get a per-cue MUSC (a script names one FILE); every
        # other category shares its folder's MUSC, which is what a bare
        # `StreamMusic dungeon` should resolve to.
        cues[rel] = (music_cue_editor_id(plugin, rel) if cat == 'special'
                     else music_type_editor_id(plugin, cat))
        if cat and cat != 'special':
            cues.setdefault('music/' + cat, music_type_editor_id(plugin, cat))
    return cues


def _script_worker_init(xref, output_dir, info_reveals, service_topics,
                        stage_reveals, say_durations=None,
                        quest_script_vars=None,
                        quest_edid_by_fid=None, topic_unlock_globals=None,
                        message_menus=None, mesh_bounds_cache=None,
                        chargen_menus=None, say_topics=None,
                        music_cues=None, namespace=None,
                        quest_delays=None, quest_objectives=None,
                        conversation_chains=None):
    """Seed one worker with the parent state that spawning does not carry.

    `namespace` is installed FIRST: the generated-script prefix derives from
    the active namespace, so any name built before it is wrong.

    `quest_objectives` seeds the FO3/FNV authored-QOBJ index; spawning does not
    carry the parent's module-level copy.
    See: docs/commentary/asset_convert_texture.md#per-game-asset-namespace
    See: docs/commentary/script_convert.md#fnv-unknown-objective-index
    """
    if namespace:
        set_namespace(namespace)
    # Windows spawns workers, so module-level caches loaded in the parent do
    # NOT carry over — each worker reloads the mesh-bounds cache or every
    # needs_havok_release() lookup answers 0 and no trap gets its release.
    if mesh_bounds_cache:
        from tes5_import.base.mesh_bounds import load_mesh_bounds
        load_mesh_bounds(mesh_bounds_cache, quiet=True)
    _WORKER_CTX.update(xref=xref, output_dir=output_dir,
                       info_reveals=info_reveals,
                       service_topics=service_topics,
                       stage_reveals=stage_reveals,
                       quest_script_vars=quest_script_vars or {},
                       quest_edid_by_fid=quest_edid_by_fid or {},
                       quest_delays=quest_delays or {})
    if quest_objectives:
        set_quest_objectives(quest_objectives)
    # Class-level, so every ScriptConverter a worker builds sees the measured
    # voice-line lengths (per INFO for the Begin fragments, per topic for the
    # SayLine fallback).
    ScriptConverter.say_durations = say_durations or {}
    # Topics a script drives via Say/SayTo.  Windows SPAWNS workers, so this
    # must be passed in explicitly -- a set built in the parent while scanning
    # scripts does not survive into the child, and an empty set here would
    # make info_needs_fragment() drop the timing fragments that SayLine needs.
    ScriptConverter.say_topics = set(say_topics or ())
    # DIAL EditorID -> unlock global, so a script `AddTopic X` opens the same
    # gate the INFO/QUST fragments do.
    ScriptConverter.topic_unlock_globals = topic_unlock_globals or {}
    ScriptConverter.conversation_chains = conversation_chains or {}
    # script EditorID -> button-MessageBox MESG plan; the importer writes the
    # records this makes the converter reference (message_menus.py).
    ScriptConverter.message_menus = message_menus or {}
    # ShowBirthsignMenu/ShowClassMenu → modal Message pages + per-choice
    # spell grants (message_menus.build_chargen_menus; importer authors the
    # MESG records at fixed FormIDs).
    ScriptConverter.chargen_menus = chargen_menus or {}
    # StreamMusic "<path>" -> the MUSC EditorID the importer authored for that
    # exact file.  Windows SPAWNS workers, so this has to ride initargs like
    # say_topics above; a dict built only in the parent leaves every worker with
    # an empty map and every StreamMusic falls back to the inert marker.
    ScriptConverter.set_music_cues(music_cues or {})


def _script_worker_run(job):
    kind, records = job
    ctx = _WORKER_CTX
    stats = _new_stats()
    if kind == 'scpt':
        _scpt_batch(records, ctx['output_dir'], ctx['xref'], stats)
    elif kind == 'info':
        _info_batch(records, ctx['output_dir'], ctx['xref'], stats,
                    ctx['info_reveals'], ctx['service_topics'])
    elif kind == 'qust':
        _qust_batch(records, ctx['output_dir'], ctx['xref'], stats,
                    ctx['stage_reveals'])
    return stats


def _merge_stats(into: dict, part: dict):
    for k, v in part.items():
        if isinstance(v, list):
            into[k].extend(v)
        elif isinstance(v, dict):
            into[k].update(v)
        else:
            into[k] += v


def _chunk(records: list, size: int):
    return [records[i:i + size] for i in range(0, len(records), size)]


# ===========================================================================
# High-level conversion functions
# ===========================================================================

def build_script_context(export_dir: str, output_dir: str) -> dict:
    """Everything a script-conversion worker needs, built ONCE per plugin.

    Returns {'initargs': tuple for _script_worker_init, 'scpt_work': [...],
    'info_work': [...], 'qust_work': [...], 'stats': dict}.  Shared by
    convert_all_scripts and tools/script/convert_scripts_subset.py, so a
    subset build is the SAME conversion as the full one.
    See: docs/commentary/script_convert.md#script-output-dir
    """
    prepare_output_dir(output_dir)
    bounds_cache = load_bounds_cache(export_dir)
    deploy_static_scripts(export_dir, output_dir)
    xref = build_xref(export_dir)
    by_type = load_records(export_dir, ('DIAL', 'INFO', 'QUST', 'SCPT', 'NPC_',
                                        'MESG'))
    unlock_plan = build_unlock_plan(by_type)
    print(f'    AddTopic unlocks: {len(unlock_plan["gated"])} gated topics, '
          f'{len(unlock_plan["info_reveals"])} revealer INFOs')
    stats = _new_stats()
    stats['scpt_total'] = len(by_type['SCPT'])
    scpt_work = [r for r in by_type['SCPT'] if r.get('SCTX', '').strip()]
    info_work = [r for r in by_type['INFO'] if r.get('FormID')]
    qust_work = [r for r in by_type['QUST'] if r.get('EditorID', '')]
    print(f'  Converting {len(scpt_work)} SCPT / {len(info_work)} INFO / '
          f'{len(qust_work)} QUST scripts...')
    say_durations = scan_voice_durations(export_dir)
    if say_durations:
        print(f'    voice durations: {len(say_durations)} lines/topics '
              f'measured (Say() timers)')
    say_topics = scan_say_topic_fids(by_type)
    ScriptConverter.say_topics = say_topics
    print(f'    script-driven topics: {len(say_topics)}')
    quest_script_vars = build_quest_script_vars(by_type)
    _write_conversation_driver(export_dir, output_dir, by_type,
                               quest_script_vars, say_durations)
    message_menus = build_message_plan(by_type['SCPT'], by_type['MESG'])
    if message_menus:
        print(f'    Button menus: {sum(len(v) for v in message_menus.values())} '
              f'MessageBox sites in {len(message_menus)} scripts')
    initargs = (xref, output_dir, unlock_plan['info_reveals'],
                service_menu_topics(by_type, SERVICE_MENU_TOPICS,
                                    DIAL_TYPE_SERVICE),
                unlock_plan['stage_reveals'], say_durations,
                quest_script_vars, quest_edids_by_fid(by_type),
                topic_unlock_globals(by_type, unlock_plan), message_menus,
                bounds_cache, chargen_menu_plan(export_dir), say_topics,
                _load_music_cues(output_dir), current_namespace(),
                quest_script_delays(by_type),
                quest_objective_indices(by_type),
                build_script_chain_map(by_type))
    return {'initargs': initargs, 'scpt_work': scpt_work,
            'info_work': info_work, 'qust_work': qust_work, 'stats': stats}


def _write_conversation_driver(export_dir: str, output_dir: str,
                               by_type: dict, quest_script_vars: dict,
                               say_durations: dict) -> None:
    """Generate the NPC-to-NPC conversation driver script of a masterless plugin.

    Built from the same plan the importer bound the driver quest's VMAD
    against; a dependent plugin's copy would collide with its master's name.
    """
    if master_names(export_dir):
        return
    conv_by_type = dict(by_type)
    conv_by_type.update(load_records(export_dir, ('ACHR', 'ACRE')))
    stem = os.path.splitext(
        os.path.basename(os.path.normpath(export_dir)))[0]
    plan = build_conversation_plan(conv_by_type, script_vars=quest_script_vars,
                                   plugin_stem=stem)
    psc = generate_driver_psc(plan, say_durations)
    if psc:
        write_psc(output_dir, plan['script_name'], psc)
        print(f"    NPC conversations: {len(plan['chains'])} chains "
              f"-> {plan['script_name']}.psc "
              f"({len(plan['skipped'])} skipped)")


def convert_all_scripts(export_dir: str, output_dir: str, workers: int = None) -> dict:
    """Convert all TES4 scripts from export directory to Papyrus .psc files.

    Args:
        export_dir: Path to export/Oblivion.esm (contains .txt files)
        output_dir: Path to write .psc files
        workers: Number of worker threads (default: cpu_count-1)

    Returns dict with conversion statistics.
    """
    if workers is None:
        workers = worker_count()

    ctx = build_script_context(export_dir, output_dir)
    initargs = ctx['initargs']
    stats = ctx['stats']
    scpt_work, info_work, qust_work = (ctx['scpt_work'], ctx['info_work'],
                                       ctx['qust_work'])
    jobs = ([('scpt', c) for c in _chunk(scpt_work, 48)]
            + [('info', c) for c in _chunk(info_work, 128)]
            + [('qust', c) for c in _chunk(qust_work, 8)])
    if workers <= 1 or len(jobs) <= 2:
        _script_worker_init(*initargs)
        for job in jobs:
            _merge_stats(stats, _script_worker_run(job))
        _WORKER_CTX.clear()
    else:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=min(workers, len(jobs)),
                                 initializer=_script_worker_init,
                                 initargs=initargs) as ex:
            for part in ex.map(_script_worker_run, jobs):
                _merge_stats(stats, part)

    _fix_udf_call_arg_types(output_dir, stats['udf_sigs'],
                            stats['udf_callers'])

    total = stats['scpt_ok'] + stats['info_ok'] + stats['qust_ok']
    errs = stats['scpt_err'] + stats['info_err'] + stats['qust_err']
    print('\n  Script conversion complete:')
    print(f'    SCPT: {stats["scpt_ok"]}/{stats["scpt_total"]} converted')
    print(f'    INFO: {stats["info_ok"]}/{stats["info_total"]} fragments')
    print(f'    QUST: {stats["qust_ok"]}/{stats["qust_total"]} stage scripts')
    print(f'    Total: {total} converted, {errs} errors, {stats["todo_count"]} TODOs')
    if stats['errors']:
        # One line per DISTINCT failure, with a count and an example: 2,393
        # identical messages say no more than one does, and hiding them
        # entirely (as this did) turned a total conversion failure into a
        # number nobody could act on.
        buckets = {}
        for msg in stats['errors']:
            buckets.setdefault(msg.split(': ', 1)[-1], []).append(msg)
        for text, msgs in sorted(buckets.items(), key=lambda kv: -len(kv[1]))[:8]:
            print(f'      [{len(msgs):5d}] {text}   e.g. {msgs[0].split(":")[0]}')

    return stats


def write_psc(output_dir: str, script_name: str, text: str) -> None:
    """Write one generated script, commenting out its dangling references.

    Morroblivion's scripts contain references the mod itself never defines --
    `fbmwMQHlaaluSuccess.hortvotes`, `fbmwMVRichTrader.follownow` -- pointing
    at records that exist in no plugin, master included.  Oblivion ignored the
    dangling name silently; Papyrus rejects the whole file, and a fragment
    that fails to compile takes its quest stage with it.

    This ran as a second sweep of the output tree, re-reading
    every `.psc` the converter had just written.  It needs nothing but the
    file's own lines, so it belongs at the write.
    """
    path = os.path.join(output_dir, script_name + '.psc')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(_comment_dangling(text))


def _comment_dangling(text: str) -> str:
    """Comment out statements whose SUBJECT was never declared in `text`.

    Only a statement whose leading `Owner.` is neither a declared property, a
    local, nor a Papyrus built-in is touched, so a legitimate call is never
    suppressed.  Mirrors ScriptConverter._dangling_cross_script_target, which
    handles the case where the owner DOES resolve but the variable does not.
    """
    lines = text.split('\n')
    known = set(IMPLICIT_NAMES)
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith('scriptname'):
            continue
        if classify(line) is Kind.HEADER:
            sig = _SIG_PARAMS_RE.search(stripped)
            if sig:
                known.update(
                    bits[1].strip('=').lower()
                    for bits in (q.split() for q in sig.group(1).split(','))
                    if len(bits) >= 2)
            continue
        decl = _DECL_RE.match(stripped)
        if decl and not stripped.startswith(';'):
            known.add(decl.group(1).lower())

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith(';'):
            continue
        m = _MEMBER_STMT_RE.match(line)
        if m and m.group(2).lower() not in known:
            lines[i] = (f'{m.group(1)};{stripped}  ;NE: {m.group(2)} is not '
                        f'declared anywhere (dangling in the original mod)')
    return '\n'.join(lines)


def _fix_udf_call_arg_types(output_dir: str, sigs: dict, callers: dict) -> None:
    """Insert the casts a cross-script `X.TES4Call(...)` needs to compile.

    An OBSE user function's parameter type is inferred from how its OWN body
    uses the value, so a caller cannot know it until every script has
    converted.  Both inputs are collected AS each script converts: `sigs` is
    {script -> parameter types} and `callers` is {script -> (calls, types)}.

    Until this re-derived both by reading every generated `.psc` and
    regexing `Function TES4Call` headers and `X Property Y` declarations out of
    the output -- 40,586 files held to recover 36 signatures, then narrowed to
    the files containing a call.  It now rewrites the exact call TEXT the
    converter recorded, so the file is a string substitution rather than a
    parse of emitted Papyrus.
    """
    if not sigs or not callers or not os.path.isdir(output_dir):
        return
    fixed = 0
    for script_name, (calls, types) in sorted(callers.items()):
        edits = []
        for prop, args in calls:
            want = sigs.get(types.get(prop.lower(), '').lower())
            if not want or len(args) != len(want):
                continue
            cast = [f'({a} as {t})'
                    if _needs_cast(types.get(a.lower(), ''), t, a) else a
                    for a, t in zip(args, want)]
            if list(cast) == list(args):
                continue
            edits.append((f'{prop}.TES4Call({", ".join(args)})',
                          f'{prop}.TES4Call({", ".join(cast)})'))
        if not edits:
            continue
        path = os.path.join(output_dir, script_name + '.psc')
        try:
            with open(path, encoding='utf-8') as fh:
                text = fh.read()
        except OSError:
            continue
        new_text = text
        for before, after in edits:
            new_text = new_text.replace(before, after)
        if new_text != text:
            try:
                with open(path, 'w', encoding='utf-8') as fh:
                    fh.write(new_text)
                fixed += 1
            except OSError:
                pass
    if fixed:
        print(f'    UDF call arg casts inserted in {fixed} script(s)')



def _needs_cast(have: str, want: str, arg: str) -> bool:
    """Papyrus converts freely UP, so only a DOWNCAST needs an explicit `as`."""
    return (bool(have) and have.lower() in UDF_WIDE_TYPES
            and want.lower() not in UDF_WIDE_TYPES and ' as ' not in arg)


# `Owner.member` at the head of a statement, which is the shape a dangling
# cross-script reference takes.  Anchored so it only sees the STATEMENT's
# subject, never an identifier deeper in an expression.
_MEMBER_STMT_RE = re.compile(r'^(\s*)([A-Za-z_]\w*)\.(\w+)')

# A declaration's name: `Int foo`, `Foo Property bar Auto`, `Actor[] baz`.
_DECL_RE = re.compile(r'^\s*(?:\w+(?:\[\])?)\s+(?:Property\s+)?(\w+)\b',
                      re.IGNORECASE)
# The parameter list of a Function/Event header.
_SIG_PARAMS_RE = re.compile(r'\((.*)\)')


def _scpt_batch(records: list, output_dir: str, xref: CrossRefGraph, stats: dict):
    """Convert a batch of SCPT records (runs in parent or worker process)."""
    for rec in records:
        formid = rec.get('FormID', '')
        edid = rec.get('EditorID', '')
        sctx = rec.get('SCTX', '')
        if not sctx or not sctx.strip():
            continue

        try:
            extends = xref.get_extends_class(formid)
            conv = ScriptConverter(xref)
            preload_scro_refs(conv, rec, xref)
            # Recover names the source text spells staler than the SCRO table
            # the engine runs off (see resolve_scro_aliases).
            conv.set_scro_aliases(resolve_scro_aliases(
                sctx, scro_list(rec), xref))
            conv.sc.quest_delay = _WORKER_CTX.get('quest_delays', {}).get(
                formid.upper(), 0.0)
            name = sanitize_name(edid or f'Script_{formid}')
            papyrus = conv.convert_standalone(name, sctx, extends, edid)

            # The FILENAME must match the ScriptName the converter emitted, or
            # the compiler cannot find the script by name.
            script_name = papyrus_script_name(name)
            write_psc(output_dir, script_name, papyrus)
            if conv.sc.udf_signature is not None:
                stats['udf_sigs'][script_name.lower()] = conv.sc.udf_signature
            if conv.sc.udf_calls:
                # BOTH type sources: an external ref becomes a property, and a
                # script-local `ref` is promoted to one.  The old pass saw both
                # because it regexed `X Property Y` out of the OUTPUT.
                types = {k.lower(): v
                         for k, v in conv.get_property_refs().items()}
                types.update(conv.sc.var_types)
                stats['udf_callers'][script_name] = (conv.sc.udf_calls, types)
            stats['scpt_ok'] += 1
            stats['todo_count'] += papyrus.count(';TODO')
        except Exception as e:
            stats['scpt_err'] += 1
            stats['errors'].append(f'SCPT {edid} ({formid}): {e}')


# Fragment lines that open the Skyrim service menus (appended to scripted
# INFOs under the Barter/Training topics; script-less ones get the shared
# static scripts of the same content instead).


def build_quest_script_vars(by_type: dict) -> dict:
    """quest fid (low-24) -> {script-local var index: name}.

    A TES4 `GetQuestVariable` condition stores the variable's SLSD INDEX; the
    name lives only in the SCPT the quest runs. `_seq_counter_condition` needs
    the name to emit a Papyrus property reference.
    """
    script_vars = {}
    for rec in by_type.get('SCPT', []):
        try:
            sfid = int(rec.get('FormID', ''), 16) & 0x00FFFFFF
        except ValueError:
            continue
        table = {}
        i = 0
        while f'Variable[{i}].Index' in rec:
            try:
                idx = int(rec[f'Variable[{i}].Index'])
            except (TypeError, ValueError):
                i += 1
                continue
            name = rec.get(f'Variable[{i}].Name')
            if name:
                table[idx] = name
            i += 1
        if table:
            script_vars[sfid] = table

    out = {}
    for rec in by_type.get('QUST', []):
        try:
            qfid = int(rec.get('FormID', ''), 16) & 0x00FFFFFF
            scri = int(rec.get('SCRI', '') or '0', 16) & 0x00FFFFFF
        except ValueError:
            continue
        if scri in script_vars:
            out[qfid] = script_vars[scri]
    return out





def _info_begin_fragment(body_lines: list, seq_gate: str,
                         length: float) -> list:
    """Fragment_1 (OnBegin): report the line, then hand the turn over.

    The handoff belongs here rather than in OnEnd because TES4's synchronous
    Say let the result script hand over in the frame the line STARTED. Only a
    body that steps the gate's own counter is a sequencer and gets one.
    See: docs/commentary/script_convert.md#turn-handoff
    """
    handoff = []
    if seq_gate and body_lines:
        counter_step, rest = split_counter_step(body_lines, seq_gate)
        if counter_step:
            gated_rest, _ = split_stage_advances(rest)
            handoff, _ = split_turn_handoff(counter_step, gated_rest)
    out = ['Function Fragment_1(ObjectReference akSpeakerRef)',
           f'  TES4Polyfill.LineBegan(akSpeakerRef, {length:g})']
    if handoff:
        out.append(f"  If {seq_gate}  ; still this line's turn")
        out.extend('  ' + b for b in handoff)
        out.append('  EndIf')
    return out + ['EndFunction', '']


def _info_end_fragment(body_lines: list, seq_gate: str, reveals,
                       service_kind, length: float) -> list:
    """Fragment_0 (OnEnd): unlocks, the TES4 result, then LineEnded LAST.

    A poll waiting on this speaker must see the result's state writes before
    it can issue the next line. The body is gated only when it owns the
    handoff; when the QUEST SCRIPT advances the counter instead, the value has
    already moved on and gating here would discard the whole body.
    See: docs/commentary/script_convert.md#sequenced-fragment-surgery
    """
    out = ['Function Fragment_0(ObjectReference akSpeakerRef)']
    out += [f'  {gname}.SetValue(1)' for gname in reveals]
    body_lines = state_writes_before_setstage(body_lines)
    counter_step, rest_body = split_counter_step(body_lines, seq_gate)
    if seq_gate and body_lines and counter_step:
        gated_rest, stage_advances = split_stage_advances(rest_body)
        _, end_rest = split_turn_handoff(counter_step, gated_rest)
        if end_rest:
            out.append(f"  If {stepped_gate(seq_gate, counter_step)}"
                       f"  ; turn still ours (counter stepped in OnBegin)")
            out.extend('  ' + b for b in end_rest)
            out.append('  EndIf')
        out.extend(stage_advances)
    else:
        out.extend(body_lines)
    if service_kind:
        out.append(SERVICE_MENU_CALL[service_kind])
    out.append(f'  TES4Polyfill.LineEnded(akSpeakerRef, {length:g})')
    return out + ['EndFunction', '']


def _info_batch(records: list, output_dir: str, xref: CrossRefGraph,
                stats: dict, info_reveals: dict = None,
                service_topics: dict = None):
    """Convert a batch of INFO records into TopicInfo fragment .psc files.

    EVERY INFO gets a fragment script `TES4_TIF__<fid>` (the importer writes
    the matching VMAD on every INFO — build_vmad_info_fragment, flags 0x03):

        Fragment_1 (OnBegin)  TES4Polyfill.LineBegan(akSpeakerRef, <length>)
        Fragment_0 (OnEnd)    [unlock globals] [TES4 result script]
                              [service menu]  TES4Polyfill.LineEnded(akSpeakerRef)

    The Begin/End hooks are how a converted `set T to Say topic` learns that
    the engine has started the line and how long it is (see
    TES4Polyfill.SayLine); they carry the speaker only, so no property is
    bound and no INFO can be missed.  The TES4 result script stays in the End
    fragment: Oblivion ran an INFO's result when the line FINISHED (the CS
    wiki's own scripted-conversation recipe writes `set Q.convTimer to <pause>`
    in results as an after-line pause, which only works at end).

    info_reveals ({info_fid24: [unlock global names]}) marks AddTopic revealer
    INFOs: their End fragment sets the unlock globals. Must stay in sync with
    the VMADs the importer writes (same unlock plan).

    service_topics ({dial_formid_str: 'barter'|'training'}) marks the service-
    menu topics; fragments for their INFOs also open the corresponding menu.
    """
    info_reveals = info_reveals or {}
    service_topics = service_topics or {}
    say_durations = ScriptConverter.say_durations or {}

    for rec in records:
        result_script = info_result_script(rec)
        has_script = bool(result_script.strip())
        formid = rec.get('FormID', '')
        if not formid:
            continue
        try:
            fid24 = int(formid, 16) & 0xFFFFFF
        except (TypeError, ValueError):
            fid24 = 0
        reveals = info_reveals.get(fid24, [])
        service_kind = service_topics.get(rec.get('ParentDIAL', ''), '')
        # Skip INFOs whose fragment would do nothing.  The engine BINDS an
        # INFO's fragment script when it selects that line -- loading and
        # linking the .pex before a word is spoken -- so a fragment with no
        # behaviour is a per-line cost paid on the dialogue path.  Must stay
        # in lockstep with the importer's VMAD writer: both call this.
        if not info_needs_fragment(rec, info_reveals, service_topics):
            stats['info_total'] += 1
            stats['info_ok'] += 1
            continue
        # This line's own measured length (all of its responses, played back
        # to back).  0 when the line has no voice file: SayLine then falls
        # back to the topic's longest line.
        try:
            length = float(say_durations.get(f'info:{formid.upper()}') or 0.0)
        except (TypeError, ValueError):
            length = 0.0
        seq_gate = sequence_gate(rec, _WORKER_CTX.get('quest_script_vars') or {},
                             _WORKER_CTX.get('quest_edid_by_fid') or {})

        if has_script:
            stats['info_total'] += 1

        try:
            body_lines = []
            prop_refs = {}
            if has_script:
                conv = ScriptConverter(xref)
                preload_scro_refs(conv, rec, xref)
                conv.set_scro_aliases(resolve_scro_aliases(
                    result_script, scro_list(rec), xref))
                body_lines = conv.convert_fragment(result_script, 'TopicInfo')
                prop_refs = dict(conv.sc.property_refs)

            script_name = f'{script_prefix("_TIF__")}{formid}'
            out_lines = [
                f'ScriptName {script_name} extends TopicInfo Hidden',
                '',
            ]
            declared = set()
            for gname in reveals:
                declared.add(gname.lower())
                out_lines.append(f'GlobalVariable Property {gname} Auto')
            if prop_refs:
                # Merge case-variant keys, most specific type wins — the same
                # rule the QUST-stage and standalone emitters already apply.
                # Without it this site declared whichever spelling sorted first:
                # _preload_scro_refs types a QUST SCRO as the generic `Quest`,
                # then _convert_ref adds the specific TES4_<script> type, and if
                # the two EditorID spellings differ in case they land under
                # different keys.  The generic one won and every cross-script
                # variable read through it failed ("field or property StartTimer
                # not found" on a plain Quest).
                out_lines += property_declarations(prop_refs,
                                                   declared)
            if declared:
                out_lines.append('')

            out_lines += _info_begin_fragment(body_lines, seq_gate, length)
            out_lines += _info_end_fragment(body_lines, seq_gate, reveals,
                                            service_kind, length)

            # GetInCell prefix-family helpers the fragment body calls by name.
            # Only a scripted INFO has a converter (and therefore a body).
            if has_script:
                out_lines.extend(conv.get_cell_family_helpers())

            papyrus = '\n'.join(out_lines)
            write_psc(output_dir, script_name, papyrus)
            if has_script:
                stats['info_ok'] += 1
            stats['todo_count'] += papyrus.count(';TODO')
        except Exception as e:
            stats['info_err'] += 1
            stats['errors'].append(f'INFO {formid}: {e}')


def _qust_batch(records: list, output_dir: str, xref: CrossRefGraph,
                stats: dict, stage_reveals: dict = None):
    """Write one `_QF_` script per QUST that has stage fragments.

    stage_reveals ({(quest_edid_lower, stage): [unlock global names]}) marks
    stages whose TES4 result scripts contained `AddTopic X`.
    See: docs/commentary/script_convert.md#quest-fragments
    """
    stage_reveals = stage_reveals or {}
    for rec in records:
        edid = rec.get('EditorID', '')
        fragments = stage_fragments(rec) if edid else []
        if not fragments:
            continue
        scripted = scripted_count(fragments)
        stats['qust_total'] += scripted
        try:
            script_name, papyrus = quest_fragment_psc(
                rec, edid, xref, fragments, stage_reveals)
            write_psc(output_dir, script_name, papyrus)
            stats['qust_ok'] += scripted
            stats['todo_count'] += papyrus.count(';TODO')
        except Exception as e:
            stats['qust_err'] += scripted
            stats['errors'].append(f'QUST {edid}: {e}')


# ===========================================================================
# VMAD binary helpers (for tes5_import integration)
# ===========================================================================

def build_vmad_quest_fragments(quest_edid: str, stage_fragments: list[tuple[int, int]],
                               property_values: dict = None,
                               attached_script: tuple = None,
                               alias_scripts: list = None,
                               quest_fid: int = 0) -> bytes:
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
        alias_scripts: optional [(alias_id, [(script_name, {prop: formid})])]
            binding scripts to this quest's reference aliases (how vanilla
            hosts player-side logic — JailQuestPlayerScript on JailQuest's
            alias 15, TutorialPlayerScript on TutorialEnchanting's alias 5).
        quest_fid: this quest's own output FormID.  The alias entry's
            ScriptPropertyObject names the QUEST, not the alias target —
            verified against Skyrim.esm, where every alias group's formID is
            the owning QUST's.

    Returns VMAD binary data.
    """
    script_name = papyrus_script_name(quest_edid, script_prefix('_QF_'))
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
    # only once the trailing count is read).
    #
    # Each entry (xEdit wbArrayS('Aliases', ...), byte layout confirmed by
    # parsing JailQuest / TutorialEnchanting / MQSkyHavenSparring out of
    # Skyrim.esm):
    #   ScriptPropertyObject  U16 unused, S16 aliasID, U32 formID (the QUEST's)
    #   S16 Version, S16 ObjectFormat
    #   S16 script count, then that many ordinary script entries
    alias_scripts = alias_scripts or []
    buf += struct.pack('<h', len(alias_scripts))
    for alias_id, scripts in alias_scripts:
        buf += struct.pack('<HhI', 0, alias_id, quest_fid)
        buf += struct.pack('<hh', 5, 2)          # version=5, objectFormat=2
        buf += struct.pack('<h', len(scripts))
        for sname, props in scripts:
            buf += _pack_wstring(sname)
            buf += struct.pack('<B', 0)          # flags=0
            buf += struct.pack('<H', len(props))
            for pname, fid in props.items():
                buf += _pack_wstring(pname)
                buf += struct.pack('<BB', 1, 1)  # type=Object, status=Edited
                buf += struct.pack('<HhI', 0, -1, fid)

    return bytes(buf)


# `[set X to] [ref.]Say[To] [target] <TopicEDID> [flags]` in raw TES4 script
# text.  Both forms are matched in one pass; which capture holds the topic
# depends on whether this was SayTo (target first) or Say (topic first).
_TES4_SAY_RE = re.compile(
    r'\b(?:\w+\s*\.\s*)?say(to)?\s+(\w+)(?:\s+(\w+))?', re.IGNORECASE)


def scan_say_topic_fids(by_type: dict) -> set:
    """DIAL FormIDs (upper hex, as INFO.ParentDIAL stores them) whose topic a
    script drives via Say/SayTo.

    Keyed by FORMID, not EditorID: an INFO record carries only
    `ParentDIAL=000000AA`, so the emitter would otherwise have to resolve a
    name it does not have.  Resolving here also means the lookup in
    info_needs_fragment() is a plain set membership test.
    """
    names = scan_say_topics(by_type)
    if not names:
        return set()
    out = set()
    for rec in by_type.get('DIAL', []):
        edid = (rec.get('EditorID') or '').strip().lower()
        fid = (rec.get('FormID') or '').strip().upper()
        if edid and fid and edid in names:
            out.add(fid)
    return out


def scan_say_topics(by_type: dict) -> set:
    """Topic EditorIDs (lowercase) that a TES4 script drives via Say/SayTo.

    Computed once, before the worker pool starts: the fragment emitter and
    the VMAD writer run in different processes.  A candidate must name a real
    DIAL, which drops the prose the regex also matches (Oblivion.esm: 98 raw
    candidates -> 31 topics).  Every script field the export uses is read on
    every record (SCTX, ResultScript, ResultScriptEnd, ScriptText).
    """
    dial_edids = {(r.get('EditorID') or '').strip().lower()
                  for r in by_type.get('DIAL', [])}
    dial_edids.discard('')
    if not dial_edids:
        return set()

    topics = set()
    for kind in ('SCPT', 'INFO', 'QUST'):
        for rec in by_type.get(kind, []):
            for field in ('SCTX', 'ResultScript', 'ResultScriptEnd',
                          'ScriptText'):
                text = rec.get(field) or ''
                if not text:
                    continue
                for raw in text.splitlines():
                    line = raw.split(';', 1)[0]
                    for m in _TES4_SAY_RE.finditer(line):
                        is_to = bool(m.group(1))
                        first, second = m.group(2), m.group(3)
                        # SayTo names the TARGET first, then the topic.
                        for cand in ((second, first) if is_to else (first,)):
                            if cand and cand.lower() in dial_edids:
                                topics.add(cand.lower())
                                break
    return topics


def info_needs_fragment(rec: dict, info_reveals: dict = None,
                        service_topics: dict = None) -> bool:
    """Does this INFO need a Papyrus fragment script at all?

    🛑 THE SINGLE SOURCE OF TRUTH: the fragment emitter (`_info_batch`) and the
    VMAD writer (`tes5_import.dialogue.converter`) must agree exactly, so both
    call this.  True when the INFO has a TES4 result script, reveals AddTopic
    unlock globals, opens a service menu, or sits on a script-driven topic
    whose SayLine needs the Begin/End hooks.

    See: docs/commentary/script_convert.md#info-fragment-stutter
    """
    from script_convert.converter import ScriptConverter
    info_reveals = info_reveals or {}
    service_topics = service_topics or {}

    result_script = info_result_script(rec).strip()
    if result_script:
        code = [ln for ln in result_script.splitlines()
                if ln.strip() and not ln.strip().startswith(';')]
        if code:
            return True

    try:
        fid24 = int(rec.get('FormID') or '0', 16) & 0xFFFFFF
    except (TypeError, ValueError):
        fid24 = 0
    if fid24 and fid24 in info_reveals:
        return True

    parent = (rec.get('ParentDIAL') or '').strip()
    if parent and parent in service_topics:
        return True

    # Script-driven topic: SayLine reads the line's start and length from the
    # Begin/End fragments, so these must keep theirs.
    return bool(parent) and parent.upper() in ScriptConverter.say_topics


def build_vmad_info_fragment(info_formid: str, property_values: dict = None,
                             script_name: str = None) -> bytes:
    """Build VMAD binary for an INFO record's fragment script.

    Every INFO carries BOTH fragments (flags 0x03): `Fragment_1` (OnBegin) and
    `Fragment_0` (OnEnd) — the line hooks TES4Polyfill.SayLine relies on, see
    _info_batch.  A flag bit with no matching function in the .pex makes the
    engine bind a missing function, so the emitter and this builder must never
    disagree: both are unconditional.

    Args:
        info_formid: INFO FormID string (e.g. "00012345")
        property_values: optional dict {property_name: formid} for script properties
        script_name: override the per-INFO TES4_TIF__ name with a shared static
            fragment script (e.g. TES4_ShowBarterMenu for the service-menu
            fallback INFO); the static scripts define both fragments too.

    Returns VMAD binary data.
    """
    script_name = script_name or f'{script_prefix("_TIF__")}{info_formid}'
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
    #
    # 🛑 THE ENTRIES ARE POSITIONAL, NOT NAME-BOUND.  The engine walks the set
    # flag bits in order (bit0 OnBegin, then bit1 OnEnd) and binds the Nth
    # entry to the Nth set bit; the FragmentName string is arbitrary.  Verified
    # against a real Skyrim.esm: of the 250 INFOs carrying BOTH, some name them
    # ('Fragment_0','Fragment_1') and others ('Fragment_1','Fragment_0') or
    # ('Fragment_1','Fragment_2') — the order of the ENTRIES is what decides,
    # so the Begin entry must be written FIRST.  Writing them the other way
    # round runs the End body when the line starts and vice versa.
    buf += struct.pack('<b', 2)        # Extra bind data version = 2
    buf += struct.pack('<B', 0x03)     # bit0=OnBegin, bit1=OnEnd
    buf += _pack_wstring(script_name)  # FileName

    # OnBegin entry FIRST (bit0) — reports the line's start and length.
    buf += struct.pack('<B', 1)
    buf += _pack_wstring(script_name)
    buf += _pack_wstring('Fragment_1')

    # OnEnd entry (bit1) — the result script, then the line-over hook.
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


def append_vmad_object_script(existing: bytes, script_name: str,
                              object_props: dict = None,
                              value_props: dict = None) -> bytes:
    """Add one more attached script to an already-built object VMAD.

    build_vmad_object_script writes a fixed "attached scripts = 1" count, so a
    record that needs TWO scripts (a converted TES4 creature SCRI *and* the
    generated TES4_GhostDissolve) has to have the count bumped and the second
    script's entry concatenated.  `existing` may be empty, in which case this
    is just build_vmad_object_script.

    Both scripts then run side by side, which is what Skyrim does natively --
    a record's VMAD is a list, and each attached script gets its own event
    handlers.  Layout is otherwise identical (version 5, objectFormat 2), and
    the entries after the count are a flat sequence, so appending is safe
    without reparsing the first script's properties.
    """
    if not existing:
        return build_vmad_object_script(script_name, object_props,
                                        value_props)

    # header is <H version><H objectFormat><H scriptCount>
    version, obj_format, count = struct.unpack_from('<HHH', existing, 0)
    body = existing[6:]

    tail = bytearray()
    tail += _pack_wstring(script_name)
    tail += struct.pack('<B', 0)                 # flags=0
    object_props = object_props or {}
    value_props = value_props or {}
    tail += struct.pack('<H', len(object_props) + len(value_props))
    for pname, fid in object_props.items():
        tail += _pack_wstring(pname)
        tail += struct.pack('<BB', _VMAD_PROP_OBJECT, 1)
        tail += struct.pack('<HhI', 0, -1, fid)
    for pname, (kind, value) in value_props.items():
        tail += _pack_wstring(pname)
        if kind == 'float':
            tail += struct.pack('<BB', _VMAD_PROP_FLOAT, 1)
            tail += struct.pack('<f', float(value))
        elif kind == 'bool':
            tail += struct.pack('<BB', _VMAD_PROP_BOOL, 1)
            tail += struct.pack('<B', 1 if value else 0)
        else:
            tail += struct.pack('<BB', _VMAD_PROP_INT, 1)
            tail += struct.pack('<i', int(value))

    return (struct.pack('<HHH', version, obj_format, count + 1)
            + body + bytes(tail))


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
