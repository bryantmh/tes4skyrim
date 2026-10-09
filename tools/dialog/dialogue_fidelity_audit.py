"""Read-only TES4/TES5 dialogue comparison; does not build or launch a game.

Pass --pair SOURCE CONVERTED for each plugin, in master/load order. FormIDs
are compared by their owning filename and local ID, using each file's MAST
table; adding Skyrim.esm to the output therefore does not create false losses.
Masters without a supplied pair are reported as unindexed, not as missing.
Missing IDs and matching text are evidence to investigate, not proof that an
equivalent conversation is or is not playable under synthesized IDs.
"""

import argparse
import hashlib
import json
import mmap
import re
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tes4_export import tes4_reader as tes4
from tes5_import.base.tes5_reader import walk


def _key(fid, owners):
    if fid >> 24 >= len(owners):
        # Some original files contain stray records in undeclared slots. Do
        # not assign those to a different plugin or claim their owner exists.
        return f'{owners[-1].lower()}#unlisted-slot-{fid >> 24:02X}', fid & 0xFFFFFF
    return owners[fid >> 24].lower(), fid & 0xFFFFFF


def _label(key):
    return f'{key[0]}:{key[1]:06X}'


def _decode(data, encoding):
    return data.rstrip(b'\0').decode(encoding)


def _metadata(path, masters):
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'path': str(path.resolve()), 'sha256': digest, 'masters': masters}


def _payload(fid, flags, parent, subs, owners, encoding):
    return {
        'raw_formid': f'{fid:08X}', 'deleted': bool(flags & 0x20),
        'parent': _key(parent, owners) if parent else None,
        'edid': next((_decode(v, encoding) for k, v in subs if k == 'EDID'), ''),
        'type': next((v[0] for k, v in subs if k == 'DATA' and v), None),
        'choices': [_key(struct.unpack('<I', v)[0], owners)
                    for k, v in subs if k == 'TCLT' and len(v) == 4],
        'texts': [_decode(v, encoding) for k, v in subs if k == 'NAM1'],
        'script': next((_decode(v, encoding) for k, v in subs if k == 'SCTX'), ''),
    }


def read_source(path, encoding):
    header, headers = tes4.read_file(str(path), parse_subs=False)
    masters = [_decode(s.data, 'ascii') for s in header.subrecords if s.type == 'MAST']
    owners = masters + [path.name]
    signatures, topics, infos = {}, {}, {}
    with path.open('rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as raw:
        header_size = tes4.detect_header_size(raw)
        for record in headers:
            key = _key(record.form_id, owners)
            signatures[key] = (record.type, bool(record.flags & 0x20))
            if record.type not in ('DIAL', 'INFO'):
                continue
            parsed = tes4._read_record(raw, record.offset, len(raw), hdr_size=header_size)
            value = _payload(record.form_id, record.flags, record.parent_dial,
                             [(s.type, s.data) for s in parsed.subrecords], owners, encoding)
            (topics if record.type == 'DIAL' else infos)[key] = value
    return _metadata(path, masters), signatures, topics, infos


def read_converted(path, encoding):
    signatures, topics, infos = {}, {}, {}
    with path.open('rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as raw:
        header, _ = next(walk(raw, b'TES4', span=(0, len(raw))))
        masters = [_decode(v, 'ascii') for k, v in header.subs() if k == b'MAST']
        owners = masters + [path.name]
        for record, _ in walk(raw, bodies=()):
            signatures[_key(record.form_id, owners)] = (record.sig.decode('ascii'), record.deleted)
        for record, stack in walk(raw, b'DIAL', b'INFO'):
            parent = stack.of_type(7)
            value = _payload(record.form_id, record.flags, parent.label_fid if parent else 0,
                             [(k.decode('ascii'), v) for k, v in record.subs()], owners, encoding)
            (topics if record.sig == b'DIAL' else infos)[_key(record.form_id, owners)] = value
    return _metadata(path, masters), signatures, topics, infos


def _dangling(infos, signatures, indexed_owners):
    invalid, unindexed = [], []
    for key, info in infos.items():
        if info['deleted']:
            continue
        for target in info['choices']:
            row = {'info': _label(key), 'target': _label(target)}
            if target[0] not in indexed_owners:
                unindexed.append(row)
            elif signatures.get(target) != ('DIAL', False):
                invalid.append(row)
    return invalid, unindexed


def audit(pairs, source_encoding, output_encoding):
    source_meta, output_meta = [], []
    source_sigs, source_topics, source_infos = {}, {}, {}
    output_sigs, output_topics, output_infos = {}, {}, {}
    indexed = set()
    for source, output in pairs:
        if source.name.lower() != output.name.lower():
            raise ValueError('Paired filenames must match to identify record owners')
        indexed.add(source.name.lower())
        for reader, path, codec, metadata, signatures, topics, infos in (
            (read_source, source, source_encoding, source_meta, source_sigs, source_topics, source_infos),
            (read_converted, output, output_encoding, output_meta, output_sigs, output_topics, output_infos),
        ):
            meta, sigs, dials, responses = reader(path, codec)
            metadata.append(meta)
            signatures.update(sigs)
            topics.update(dials)
            infos.update(responses)
    source_invalid, source_unindexed = _dangling(source_infos, source_sigs, indexed)
    output_invalid, output_unindexed = _dangling(output_infos, output_sigs, indexed)
    texts = defaultdict(set)
    for key, info in output_infos.items():
        if not info['deleted']:
            for line in info['texts']:
                texts[line].add(_label(key))
    children = defaultdict(list)
    for key, info in source_infos.items():
        if not info['deleted']:
            children[info['parent']].append((key, info))
    missing_topics, critical = [], []
    for key, topic in sorted(source_topics.items()):
        if topic['deleted'] or topic['type'] != 1 or output_sigs.get(key) == ('DIAL', False):
            continue
        responses = children[key]
        missing_topics.append({'topic': _label(key), 'raw_formid': topic['raw_formid'],
                               'edid': topic['edid'], 'infos': len(responses)})
        for info_key, info in responses:
            if (output_sigs.get(info_key) == ('INFO', False)
                    or not re.search(r'\b(setstage|startquest|stopquest)\b', info['script'], re.I)):
                continue
            matches = sorted({hit for line in info['texts'] for hit in texts.get(line, ())})
            critical.append({'info': _label(info_key), 'raw_formid': info['raw_formid'],
                             'topic_edid': topic['edid'], 'source_script': info['script'],
                             'same_text_info_ids': matches})
    target_counts = Counter(row['target'] for row in output_invalid)
    affected = []
    for target, count in sorted(target_counts.items()):
        owner, local = target.rsplit(':', 1)
        key = (owner, int(local, 16))
        responses = children[key]
        affected.append({'topic': target, 'edid': source_topics.get(key, {}).get('edid'),
                         'source_target_live': source_sigs.get(key) == ('DIAL', False),
                         'links': count, 'infos': len(responses),
                         'infos_without_matching_text': sum(
                             not any(line in texts for line in info['texts'])
                             for _, info in responses)})
    return {
        'source_files': source_meta, 'converted_files': output_meta,
        'source_counts': {'topics': len(source_topics), 'infos': len(source_infos)},
        'source_dangling_choices': source_invalid, 'source_unindexed_choices': source_unindexed,
        'converted_dangling_choices': output_invalid, 'converted_unindexed_choices': output_unindexed,
        'affected_choice_targets': affected, 'missing_type1_topics': missing_topics,
        'missing_stage_fragment_candidates': critical,
        'limitations': [
            'Synthesized record IDs can replace original IDs; absence alone does not establish lost functionality.',
            'Identical text elsewhere does not establish an equivalent working conversation.',
            'PEX execution, alternate quest-stage paths, and in-game quest completion are not checked.',
            'Only the supplied plugins are indexed. No conversion or game launch is performed.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pair', nargs=2, action='append', required=True,
                        metavar=('SOURCE', 'CONVERTED'), help='Repeat in master/load order')
    parser.add_argument('--source-encoding', default='cp1252')
    parser.add_argument('--output-encoding', default='utf-8')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    pairs = [(Path(source), Path(output)) for source, output in args.pair]
    report = args.report.resolve()
    if report in {path.resolve() for pair in pairs for path in pair}:
        parser.error('Report must not overwrite an input plugin')
    result = audit(pairs, args.source_encoding, args.output_encoding)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({
        'source_dangling_choices': len(result['source_dangling_choices']),
        'converted_dangling_choices': len(result['converted_dangling_choices']),
        'affected_topics': len(result['affected_choice_targets']),
        'missing_type1_topics': len(result['missing_type1_topics']),
        'missing_stage_fragment_candidates': len(result['missing_stage_fragment_candidates']),
        'report': str(report),
    }, indent=2))


if __name__ == '__main__':
    main()
