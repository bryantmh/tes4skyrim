"""Generate tes5_import/vanilla_mgef_data.py from the Skyrim.esm MGEF dump.

The import pipeline sometimes needs to synthesize a companion MGEF that is an
"aimed variant" of a vanilla magic effect (see tes5_import/magic_effects.py) —
that requires the vanilla effect's full 152-byte DATA struct at conversion
time.  End users running the converter do not have the references/ dump, so
this tool bakes the DATA blobs for every vanilla MGEF the mapping tables can
resolve to into a committed Python module.

Rerun whenever MGEF_CODE_TO_SKYRIM / MGEF_AV_CODE_TO_SKYRIM gain new FormIDs:

    python tools/gen_vanilla_mgef_table.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tes5_import.skyrim_overrides import (  # noqa: E402
    MGEF_AV_CODE_TO_SKYRIM,
    MGEF_CODE_TO_SKYRIM,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUMP = os.path.join(ROOT, 'references', 'Skyrim.esm', 'MGEF.txt')
OUT = os.path.join(ROOT, 'tes5_import', 'vanilla_mgef_data.py')


def wanted_fids() -> set:
    fids = set(MGEF_CODE_TO_SKYRIM.values())
    for per_av in MGEF_AV_CODE_TO_SKYRIM.values():
        fids.update(per_av.values())
    fids.discard(0)
    return fids


def read_dump(want: set) -> dict:
    """{fid: (edid, data_hex)} for the wanted vanilla MGEFs."""
    found = {}
    cur = {}
    with open(DUMP, encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if line == '---RECORD_BEGIN---':
                cur = {}
            elif line == '---RECORD_END---':
                fid = cur.get('FormID')
                if fid in want and 'DATA' in cur:
                    found[fid] = (cur.get('EditorID', ''), cur['DATA'])
            elif line.startswith('FormID='):
                cur['FormID'] = int(line[7:], 16)
            elif line.startswith('EditorID='):
                cur['EditorID'] = line[9:]
            elif line.startswith('DATA.hex='):
                cur['DATA'] = line[9:].split('...')[0].strip()
    return found


def main():
    want = wanted_fids()
    found = read_dump(want)
    missing = want - set(found)
    if missing:
        print('WARNING: not found in dump: '
              + ', '.join(f'{f:08X}' for f in sorted(missing)))

    with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write('"""Vanilla Skyrim.esm MGEF DATA blobs — GENERATED FILE.\n'
                '\n'
                'Produced by tools/gen_vanilla_mgef_table.py from the\n'
                'references/Skyrim.esm dump; committed so the converter works\n'
                'without the dump.  {fid: (editor_id, 152-byte DATA hex)}.\n'
                '"""\n\n'
                'VANILLA_MGEF_DATA = {\n')
        for fid in sorted(found):
            edid, data_hex = found[fid]
            f.write(f"    0x{fid:08X}: ({edid!r},\n        '{data_hex}'),\n")
        f.write('}\n')
    print(f'Wrote {len(found)} MGEF DATA blobs to {OUT}')


if __name__ == '__main__':
    main()
