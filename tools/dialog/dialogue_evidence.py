"""Standalone, read-only evidence verifier for the English dialogue loss report.

Requires only Python's standard library. Repeat --pair ORIGINAL CONVERTED in
master/load order. Reads TES4/TES5 plugins directly; writes JSON to stdout.
Neither the converter nor its binary readers are imported.
"""

import argparse
import hashlib
import json
import mmap
import struct
import zlib
from pathlib import Path


def subrecords(body):
    pos, extended = 0, None
    while pos < len(body):
        if pos + 6 > len(body):
            raise ValueError('Truncated subrecord header')
        tag, length = struct.unpack_from('<4sH', body, pos)
        pos += 6
        if extended is not None:
            length, extended = extended, None
        end = pos + length
        if end > len(body):
            raise ValueError('Truncated subrecord body')
        value = body[pos:end]
        if tag == b'XXXX':
            if length != 4:
                raise ValueError('Invalid XXXX size')
            extended = struct.unpack('<I', value)[0]
        else:
            yield tag, value
        pos = end
    if extended is not None:
        raise ValueError('XXXX without following subrecord')


def records(raw, header_size, start, end, parent=0):
    pos = start
    while pos < end:
        if pos + header_size > end:
            raise ValueError(f'Truncated record/group at {pos:X}')
        sig, size = struct.unpack_from('<4sI', raw, pos)
        if sig == b'GRUP':
            stop = pos + size
            if size < header_size or stop > end:
                raise ValueError(f'Invalid group size at {pos:X}')
            group_type = struct.unpack_from('<I', raw, pos + 12)[0]
            label = struct.unpack_from('<I', raw, pos + 8)[0]
            child_parent = label if group_type == 7 else parent
            # DIAL/INFO live in DIAL or INFO top groups. Skip asset/world data.
            if group_type != 0 or raw[pos + 8:pos + 12] in (b'DIAL', b'INFO'):
                yield from records(raw, header_size, pos + header_size, stop, child_parent)
        else:
            stop = pos + header_size + size
            if stop > end:
                raise ValueError(f'Invalid record size at {pos:X}')
            flags, fid = struct.unpack_from('<II', raw, pos + 8)
            if sig in (b'TES4', b'DIAL', b'INFO'):
                body = raw[pos + header_size:stop]
                if flags & 0x40000:
                    expanded_size = struct.unpack_from('<I', body)[0]
                    body = zlib.decompress(body[4:])
                    if len(body) != expanded_size:
                        raise ValueError('Invalid decompressed size')
                yield sig, fid, flags, parent, pos, raw[pos:pos + header_size], list(subrecords(body))
            if sig == b'DIAL':
                parent = fid
        pos = stop


def read(path, header_size, encoding):
    topics, infos = {}, {}
    with path.open('rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as raw:
        if raw[:4] != b'TES4' or raw[header_size:header_size + 4] != b'HEDR':
            raise ValueError(f'Unexpected {header_size}-byte plugin format: {path}')
        digest = hashlib.sha256(raw).hexdigest()
        owners = []

        def identity(fid):
            slot = fid >> 24
            if slot >= len(owners):
                raise ValueError(f'Undeclared dialogue FormID slot: {fid:08X}')
            return f'{owners[slot].lower()}:{fid & 0xFFFFFF:06X}'

        for sig, fid, flags, parent, offset, header, subs in records(raw, header_size, 0, len(raw)):
            if sig == b'TES4':
                owners = [value.rstrip(b'\0').decode('ascii') for tag, value in subs if tag == b'MAST']
                owners.append(path.name)
                continue
            choices = [value for tag, value in subs if tag == b'TCLT']
            row = {
                'raw_formid': f'{fid:08X}', 'deleted': bool(flags & 0x20),
                'file_offset_hex': f'{offset:X}', 'record_header_hex': header.hex(' '),
                'parent': identity(parent) if parent and sig == b'INFO' else None,
                'editor_id': next((v.rstrip(b'\0').decode(encoding) for k, v in subs if k == b'EDID'), ''),
                'choices': [identity(struct.unpack('<I', value)[0]) for value in choices],
                'tclt_payload_hex': [value.hex(' ') for value in choices],
                'result_script': next((v.rstrip(b'\0').decode(encoding) for k, v in subs if k == b'SCTX'), ''),
                'result_script_payload_hex': next((v.hex(' ') for k, v in subs if k == b'SCTX'), ''),
                'texts': [v.rstrip(b'\0').decode(encoding) for k, v in subs if k == b'NAM1'],
            }
            (topics if sig == b'DIAL' else infos)[identity(fid)] = row
    return {'filename': path.name, 'sha256': digest, 'masters': owners[:-1]}, topics, infos


def inspect(pairs):
    original_files, converted_files = [], []
    original_topics, original_infos, converted_topics, converted_infos = {}, {}, {}, {}
    indexed = set()
    for original, converted in pairs:
        if original.name.lower() != converted.name.lower():
            raise ValueError('Paired plugin filenames must match')
        indexed.add(original.name.lower())
        for path, size, codec, files, topics, infos in (
            (original, 20, 'cp1252', original_files, original_topics, original_infos),
            (converted, 24, 'utf-8', converted_files, converted_topics, converted_infos),
        ):
            meta, dials, responses = read(path, size, codec)
            files.append(meta)
            topics.update(dials)
            infos.update(responses)

    def missing(infos, topics):
        invalid, unindexed = [], []
        for key, info in sorted(infos.items()):
            if info['deleted']:
                continue
            for choice in info['choices']:
                row = {'info': key, 'target': choice}
                if choice.rsplit(':', 1)[0] not in indexed:
                    unindexed.append(row)
                elif choice not in topics or topics[choice]['deleted']:
                    invalid.append(row)
        return invalid, unindexed

    original_invalid, original_unindexed = missing(original_infos, original_topics)
    converted_invalid, converted_unindexed = missing(converted_infos, converted_topics)
    selected = [
        'knights.esp:002CB8', 'knights.esp:002CD0', 'knights.esp:002CCE',
        'knights.esp:002CCF', 'knights.esp:002A11', 'knights.esp:002BC7',
        'oblivion.esm:0C9FB2', 'oblivion.esm:07BA9E',
    ]
    examples = []
    output_texts = {text for info in converted_infos.values() if not info['deleted'] for text in info['texts']}
    for key in selected:
        original = original_topics.get(key) or original_infos.get(key)
        if original is None:
            continue
        original = dict(original)
        lines = original.pop('texts')
        original['response_previews'] = [' '.join(line.split()[:8]) for line in lines]
        original['response_utf8_sha256'] = [hashlib.sha256(line.encode('utf-8')).hexdigest() for line in lines]
        converted = converted_topics.get(key) or converted_infos.get(key)
        converted = {k: v for k, v in converted.items() if k != 'texts'} if converted else None
        examples.append({'identity': key, 'source': original, 'converted': converted,
                         'same_response_text_found_in_output': any(line in output_texts for line in lines)})
        if key in original_topics:
            original['child_infos'] = [
                {'identity': iid, 'raw_formid': info['raw_formid'], 'choices': info['choices'],
                 'tclt_payload_hex': info['tclt_payload_hex'], 'file_offset_hex': info['file_offset_hex']}
                for iid, info in sorted(original_infos.items()) if info['parent'] == key and not info['deleted']
            ]
    return {
        'original_files': original_files, 'converted_files': converted_files,
        'original_dangling_count': len(original_invalid), 'original_unindexed_count': len(original_unindexed),
        'converted_dangling_count': len(converted_invalid), 'converted_unindexed_count': len(converted_unindexed),
        'affected_info_count': len({r['info'] for r in converted_invalid}),
        'affected_target_count': len({r['target'] for r in converted_invalid}),
        'original_dangling_links': original_invalid, 'converted_dangling_links': converted_invalid,
        'examples': examples,
        'limitations': 'Record absence is verified; synthesized replacements and quest completion require separate assessment.',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pair', nargs=2, action='append', required=True, metavar=('ORIGINAL', 'CONVERTED'))
    args = parser.parse_args()
    result = inspect([(Path(a), Path(b)) for a, b in args.pair])
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == '__main__':
    main()
