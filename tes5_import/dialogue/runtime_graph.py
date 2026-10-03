"""Source conversation graphs and bindings to the dialogue actually emitted.

Uses canonical (owner filename, local ID) identities across master exports.
Runtime routing follows the INFO the engine selected, rather than guessing a
linear path or treating a direct StartConversation head as the whole dialogue.
"""

import hashlib
import json
import os
import struct
from collections import defaultdict
from pathlib import Path

from core.plugin_masters import export_source, master_chain, master_dir, masters_from_export_header
from script_convert.constants import script_prefix, sanitize_name
from ..base.text_reader import parse_export_file
from ..base.tes5_reader import FLAG_DELETED, walk
from ..base.writer import pack_record, pack_string_subrecord, pack_subrecord, pack_uint32_subrecord

def plugin_name(export_dir):
    return Path(export_dir).name.split(' (', 1)[0]


def script_name(export_dir):
    return script_prefix('_DialogueGraph') + sanitize_name(Path(plugin_name(export_dir)).stem)


def sidecar_path(output_dir, export_dir):
    # Several plugins in a mod share one output directory.
    return Path(output_dir) / (plugin_name(export_dir) + '.dialogue.json')


def source_fingerprint(export_dir):
    digest = hashlib.sha256()
    folders = [(name, master_dir(export_dir, name)) for name in master_chain(export_dir)]
    folders.append((plugin_name(export_dir), str(export_dir)))
    for name, folder in folders:
        digest.update(name.lower().encode('utf-8'))
        for filename in ('_HEADER.txt', 'DIAL.txt', 'INFO.txt'):
            path = Path(folder) / filename
            digest.update(filename.encode('ascii'))
            if path.is_file():
                with path.open('rb') as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(block)
            else:
                digest.update(b'<missing>')
    return digest.hexdigest()


def load_manifest(export_dir, output_dir, required=False):
    path = sidecar_path(output_dir, export_dir)
    if not path.is_file():
        if required:
            raise ValueError('Conversation bindings are missing. Run step 6 (Import) '
                             'before step 8 (Scripts) with the dialogue-preservation fix.')
        return {}
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('source_fingerprint') != source_fingerprint(export_dir):
        raise ValueError('Conversation exports changed after Import. Run step 6 '
                         '(Import) again before step 8 (Scripts).')
    output = Path(output_dir) / manifest['plugin']
    expected = manifest.get('output_fingerprint')
    if (required or expected) and (not expected or not output.is_file()
                                   or file_fingerprint(output) != expected):
        raise ValueError('Conversation bindings do not match a completed plugin import. '
                         'Run step 6 (Import) again before step 8 (Scripts).')
    return manifest


def file_fingerprint(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def seal_manifest(export_dir, output_path):
    """Publish the output hash only after the actual plugin was saved."""
    path = sidecar_path(Path(output_path).parent, export_dir)
    manifest = json.loads(path.read_text(encoding='utf-8'))
    manifest['output_fingerprint'] = file_fingerprint(output_path)
    temporary = path.with_suffix('.json.part')
    temporary.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    temporary.replace(path)


def source_graph(export_dir):
    """Effective source Type-1 nodes and their reachable continuations."""
    if export_source(str(export_dir)) == 'TES3':
        return {'dials': {}, 'infos': {}}
    from .conversations import _conds, head_is_npc_addressed
    dials, infos = {}, {}
    dirs = [(name, master_dir(export_dir, name)) for name in master_chain(export_dir)]
    dirs.append((plugin_name(export_dir), str(export_dir)))
    for name, folder in dirs:
        if not os.path.isdir(folder):
            continue
        owners = [n.lower() for n in masters_from_export_header(folder)] + [name.lower()]

        def key(raw):
            fid = int(raw or '0', 16)
            if fid >> 24 >= len(owners):
                raise ValueError(f'Conversation FormID {fid:08X} exceeds the master table of {name}')
            return owners[fid >> 24], fid & 0xFFFFFF

        for sig, into in [('DIAL', dials), ('INFO', infos)]:
            if sig == 'INFO' and not dials:
                continue
            for record in parse_export_file(os.path.join(folder, sig + '.txt')):
                identity = key(record.get('FormID'))
                if int(record.get('RecordFlags', '0') or '0') & 0x20:
                    into.pop(identity, None)
                    continue
                row = dict(record)
                if sig == 'INFO':
                    row['_parent'] = key(row.get('ParentDIAL'))
                    row['_choices'] = [key(row[f'Choice[{i}]']) for i in range(int(row.get('ChoiceCount', '0') or '0'))]
                    if not row['_choices'] and row.get('TCLT.Choice'):
                        row['_choices'] = [key(row['TCLT.Choice'])]
                    row['_counters'] = []
                    if head_is_npc_addressed(row):
                        row['_counters'] = [(key(f'{p1:08X}'), p2, int(value))
                                            for flag, value, func, p1, p2 in _conds(row)
                                            if func == 79 and flag & 0xE0 == 0
                                            and value.is_integer()]
                into[identity] = row
    children = defaultdict(list)
    for identity, record in infos.items():
        children[record['_parent']].append(identity)
    # Existing scripted talks also advance a counter within the SAME topic,
    # without an explicit TCLT. Preserve that continuation until its last
    # counter value; explicit choices remain authoritative.
    for parent, members in children.items():
        counters = defaultdict(set)
        for iid in members:
            for quest, variable, value in infos[iid]['_counters']:
                counters[quest, variable].add(value)
        for counter, values in counters.items():
            last = first = min(values)
            while last + 1 in values:
                last += 1
            if last == first:
                continue
            for iid in members:
                row = infos[iid]
                if not row['_choices'] and any((q, v) == counter and first <= n < last
                                               for q, v, n in row['_counters']):
                    row['_choices'] = [parent]
    selected = {k for k, d in dials.items() if int(d.get('DATA.Type', '0') or '0') == 1 and children[k]}
    frontier = list(selected)
    while frontier:
        node = frontier.pop()
        for iid in children[node]:
            for target in infos[iid]['_choices']:
                if target in dials and children[target] and target not in selected:
                    selected.add(target)
                    frontier.append(target)
    return {'dials': {k: dials[k] for k in sorted(selected)},
            'infos': {k: infos[k] for k in sorted(infos) if infos[k]['_parent'] in selected}}


def bind_graph(export_dir, output_path, writer, master_index=None):
    """Bind source INFOs to final topics; report losses without stopping import."""
    graph = source_graph(export_dir)
    if not graph['infos']:
        return None
    slots = {name.lower(): i for i, name in enumerate(writer.masters)}
    slots[Path(output_path).name.lower()] = writer.own_index

    def output_id(identity):
        owner, local = identity
        if owner not in slots:
            return 0
        return slots[owner] << 24 | local

    parents = {}
    for blob in writer._top_groups.get('DIAL', []):
        for record, stack in walk(blob, b'INFO', span=(0, len(blob))):
            group = stack.of_type(7)
            if group:
                parents[record.form_id] = 0 if record.deleted else group.label_fid

    def parent_of(fid):
        if fid in parents:
            return parents[fid]
        if master_index is not None and master_index.signature(fid) == b'INFO':
            if struct.unpack_from('<I', master_index.record(fid), 8)[0] & FLAG_DELETED:
                return 0
            for kind, label in reversed(master_index.group_path(fid) or ()):
                if kind == 7:
                    return int.from_bytes(label, 'little')
        return 0

    info_bindings, topic_parents = {}, defaultdict(list)
    missing = []
    for identity, source in graph['infos'].items():
        fid = output_id(identity)
        parent = parent_of(fid) if fid else 0
        if not parent:
            missing.append(f'{identity[0]}:{identity[1]:06X}')
            continue
        info_bindings[identity] = (fid, parent)
        if parent not in topic_parents[source['_parent']]:
            topic_parents[source['_parent']].append(parent)
    topic_ids = sorted({p for values in topic_parents.values() for p in values})
    from .converter import CONV_KEEP_EDIDS
    writer.conversation_hidden_topics = {
        parent for key, source in graph['dials'].items()
        if int(source.get('DATA.Type', '0') or '0') == 1
        and source.get('EditorID', '') not in CONV_KEEP_EDIDS
        for parent in topic_parents[key]
    }
    topic_slot = {fid: i for i, fid in enumerate(topic_ids)}
    rows = []
    for identity, source in graph['infos'].items():
        if identity not in info_bindings:
            continue
        choices = []
        for target in source['_choices']:
            for fid in topic_parents.get(target, []):
                index = topic_slot[fid]
                if index not in choices:
                    choices.append(index)
        fid, parent = info_bindings[identity]
        rows.append({'formid': fid, 'parent': topic_slot[parent], 'choices': choices,
                     'next_speaker': int(source.get('DATA.NextSpeaker', '0') or '0')})
    name = script_name(export_dir)
    qfid = writer.derive_formid('SYNTH_QUST', name)
    properties = {f'T{i}': fid for i, fid in enumerate(topic_ids)}
    for row in rows:
        properties.setdefault(f'Owner{row["formid"] >> 24}', row['formid'])
    from script_convert.pipeline import build_vmad_quest_fragments
    body = pack_string_subrecord('EDID', name)
    body += pack_subrecord('VMAD', build_vmad_quest_fragments(name, [], attached_script=(name, properties), quest_fid=qfid))
    body += pack_subrecord('DNAM', struct.pack('<HBBII', 0x0011, 0, 0, 0, 0))
    body += pack_subrecord('NEXT', b'') + pack_uint32_subrecord('ANAM', 0)
    writer.add_record('QUST', pack_record('QUST', qfid, 0, body))
    manifest = {'version': 1, 'script': name, 'plugin': Path(output_path).name,
                'source_fingerprint': source_fingerprint(export_dir),
                'missing_infos': missing,
                'quest': qfid, 'topics': topic_ids, 'infos': rows,
                'topic_edids': sorted({d.get('EditorID', '').lower() for d in graph['dials'].values()})}
    destination = sidecar_path(Path(output_path).parent, export_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.json.part')
    temporary.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    temporary.replace(destination)
    print(f'  Conversation graph: {len(topic_ids)} topics, {len(rows)} responses bound')
    if missing:
        print(f'  WARNING: {len(missing)} conversation responses could not be bound; '
              f'conversion continues. First: {", ".join(missing[:5])}. '
              f'Full list: {destination.name}')
    return qfid


def generate_scripts(manifest):
    """Bound root plus small pure routing pages, safe for Papyrus limits."""
    name = manifest['script']
    infos = manifest['infos']
    chunks = [list(range(i, min(i + 32, len(infos)))) for i in range(0, len(infos), 32)]
    scripts = {}
    for number, indices in enumerate(chunks):
        page = f'{name}Page{number}'
        body = [f'Scriptname {page} Hidden', '', 'Int Function Select(Int aiOwner, Int aiLocal) Global']
        for i in indices:
            fid = infos[i]['formid']
            body += [f'  If aiOwner == {fid >> 24} && aiLocal == {fid & 0xFFFFFF}', f'    Return {i}', '  EndIf']
        body += ['  Return -1', 'EndFunction', '', 'Int Function ParentTopic(Int aiIndex) Global']
        for i in indices:
            body += [f'  If aiIndex == {i}', f'    Return {infos[i]["parent"]}', '  EndIf']
        body += ['  Return -1', 'EndFunction', '', 'Int Function Speaker(Int aiIndex) Global']
        for i in indices:
            body += [f'  If aiIndex == {i}', f'    Return {infos[i]["next_speaker"]}', '  EndIf']
        body += ['  Return 1', 'EndFunction', '', 'Int Function Choice(Int aiIndex, Int aiCandidate) Global']
        for i in indices:
            if infos[i]['choices']:
                body += [f'  If aiIndex == {i}', f'    Return Choice{i}(aiCandidate)', '  EndIf']
        body += ['  Return -1', 'EndFunction', '']
        for i in indices:
            choices = infos[i]['choices']
            if not choices:
                continue
            body += [f'Int Function Choice{i}(Int aiCandidate) Global']
            for candidate, target in enumerate(choices):
                body += [f'  If aiCandidate == {candidate}', f'    Return {target}', '  EndIf']
            body += ['  Return -1', 'EndFunction', '']
        scripts[page] = '\n'.join(body)

    owners = defaultdict(list)
    for i, row in enumerate(infos):
        owners[row['formid'] >> 24].append(i)
    lines = [f'Scriptname {name} extends TES4ConversationRunner', '']
    lines += [f'Topic Property T{i} Auto' for i in range(len(manifest['topics']))]
    lines += [f'Form Property Owner{owner} Auto' for owner in sorted(owners)]
    local = manifest['quest'] & 0xFFFFFF
    lines += ['', 'Bool Function Play(ObjectReference akA, ObjectReference akB, Topic akTopic) Global',
              f'  {name} runner = Game.GetFormFromFile({local}, "{manifest["plugin"]}") as {name}',
              '  If runner != None', '    Return runner.Enqueue(akA as Actor, akB as Actor, akTopic)',
              '  EndIf', '  Return False', 'EndFunction', '']
    lines += ['Int Function FormSlot(Int aiID)', '  Int slot = aiID / 16777216',
              '  If aiID < 0', '    If aiID % 16777216 != 0', '      slot -= 1',
              '    EndIf', '    slot += 256', '  EndIf', '  Return slot',
              'EndFunction', '', 'Int Function FindInfo(Int aiInfo)',
              '  Int owner = FormSlot(aiInfo)', '  Int localID = aiInfo % 16777216',
              '  If localID < 0', '    localID += 16777216', '  EndIf']
    for owner, indices in sorted(owners.items()):
        lines += [f'  If Owner{owner} != None && owner == FormSlot(Owner{owner}.GetFormID())']
        for number, group in enumerate(chunks):
            matching = [i for i in group if infos[i]['formid'] >> 24 == owner]
            if not matching:
                continue
            lo = min(infos[i]['formid'] & 0xFFFFFF for i in matching)
            hi = max(infos[i]['formid'] & 0xFFFFFF for i in matching)
            lines += [f'    If localID >= {lo} && localID <= {hi}',
                      f'      Return {name}Page{number}.Select({owner}, localID)', '    EndIf']
        lines += ['    Return -1', '  EndIf']
    lines += ['  Return -1', 'EndFunction', '', 'Topic Function TopicAt(Int aiIndex)']
    topic_chunks = [list(range(i, min(i + 32, len(manifest['topics']))))
                    for i in range(0, len(manifest['topics']), 32)]
    for number, indices in enumerate(topic_chunks):
        lines += [f'  If aiIndex >= {indices[0]} && aiIndex <= {indices[-1]}',
                  f'    Return Topics{number}(aiIndex)', '  EndIf']
    lines += ['  Return None', 'EndFunction', '']
    for number, indices in enumerate(topic_chunks):
        lines += [f'Topic Function Topics{number}(Int aiIndex)']
        for i in indices:
            lines += [f'  If aiIndex == {i}', f'    Return T{i}', '  EndIf']
        lines += ['  Return None', 'EndFunction', '']
    for function, kind, page_function, args, extra, result, fallback in [
        ('Matches', 'Bool', 'ParentTopic', 'Int aiInfo, Topic akTopic', '', 'akTopic == TopicAt(value)', 'False'),
        ('Route', 'Int', 'Speaker', 'Int aiInfo', '', 'value', '1'),
        ('NextChoice', 'Topic', 'Choice', 'Int aiInfo, Int aiCandidate', ', aiCandidate', 'TopicAt(value)', 'None'),
    ]:
        lines += [f'{kind} Function {function}({args})', '  Int index = FindInfo(aiInfo)', '  Int value = -1']
        for number, indices in enumerate(chunks):
            lines += [f'  If index >= {indices[0]} && index <= {indices[-1]}',
                      f'    value = {name}Page{number}.{page_function}(index{extra})', f'    Return {result}', '  EndIf']
        lines += [f'  Return {fallback}', 'EndFunction', '']
    scripts[name] = '\n'.join(lines)
    return scripts


def generate_script(manifest):
    """Root source; use generate_scripts to deploy its routing pages as well."""
    return generate_scripts(manifest)[manifest['script']]
