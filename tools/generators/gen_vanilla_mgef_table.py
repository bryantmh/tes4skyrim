"""Generate tes5_import/generated/vanilla_mgef_data.py from the Skyrim.esm MGEF dump.

The import pipeline sometimes clones a vanilla magic effect to fit an item's
casting type and delivery (tes5_import/record_types/magic_variants.py) --
that requires the vanilla effect's full 152-byte DATA struct at conversion
time.  End users running the converter do not have the references/ dump, so
this tool bakes the DATA blobs for every vanilla MGEF the mapping tables can
resolve to into a committed Python module.

Rerun whenever MGEF_CODE_TO_SKYRIM / MGEF_AV_CODE_TO_SKYRIM or the Morrowind
art donors (tes5_import/record_types/magic_art_morrowind.py) gain new FormIDs:

    python tools/generators/gen_vanilla_mgef_table.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tes5_import.base.equivalents import (
    MGEF_AV_CODE_TO_SKYRIM,
    MGEF_CODE_TO_SKYRIM,
)
from tes5_import.record_types.magic_art_morrowind import DONORS

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DUMP = os.path.join(ROOT, 'references', 'Skyrim.esm', 'MGEF.txt')
OUT = os.path.join(ROOT, 'tes5_import', 'generated', 'vanilla_mgef_data.py')

# A TES5 MGEF DATA struct is exactly this long (xEdit wbMGEFData; verified
# against every one of Skyrim.esm's 950 MGEF records).  Anything shorter means
# the dump truncated the hex, which is how the committed table silently ended
# up holding 96-byte blobs missing their last 14 fields.
MGEF_DATA_SIZE = 152


def wanted_fids() -> set:
    """Every vanilla MGEF a mapping table resolves to, plus the Morrowind art donors."""
    fids = set(MGEF_CODE_TO_SKYRIM.values()) | DONORS
    for per_av in MGEF_AV_CODE_TO_SKYRIM.values():
        fids.update(per_av.values())
    fids.discard(0)
    return fids


def read_dump(want: set) -> dict:
    """{fid: (edid, data_hex)} for the wanted vanilla MGEFs.

    Raises if any wanted blob is not a full MGEF_DATA_SIZE struct.  The dump
    marks a truncated hex string with a trailing '...'; treating that as data
    (rather than as an error) is what produced the 96-byte table.
    """
    found = {}
    truncated = []
    cur = {}
    with open(DUMP, encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if line == '---RECORD_BEGIN---':
                cur = {}
            elif line == '---RECORD_END---':
                fid = cur.get('FormID')
                if fid in want and 'DATA' in cur:
                    edid, data_hex = cur.get('EditorID', ''), cur['DATA']
                    if len(data_hex) != MGEF_DATA_SIZE * 2:
                        truncated.append((fid, edid, len(data_hex) // 2))
                    else:
                        found[fid] = (edid, data_hex)
            elif line.startswith('FormID='):
                cur['FormID'] = int(line[7:], 16)
            elif line.startswith('EditorID='):
                cur['EditorID'] = line[9:]
            elif line.startswith('DATA.hex='):
                cur['DATA'] = line[9:].strip()

    if truncated:
        detail = ', '.join(f'{e or f"{f:08X}"}={n}B' for f, e, n in truncated[:8])
        raise SystemExit(
            f'ERROR: {len(truncated)} MGEF DATA blob(s) are not '
            f'{MGEF_DATA_SIZE} bytes: {detail}\n'
            f'The dump at {DUMP} is truncated. Regenerate it with:\n'
            f'  python tools/esm/tes5_esm_reader.py "<path>/Skyrim.esm" '
            f'--outdir references/Skyrim.esm --types MGEF')
    return found


def main():
    want = wanted_fids()
    found = read_dump(want)
    missing = want - set(found)
    if missing:
        # A wanted FormID absent from Skyrim.esm means a mapping table points at
        # an effect that does not exist — the entry can never resolve in game.
        print('WARNING: referenced by a mapping table but NOT in Skyrim.esm: '
              + ', '.join(f'{f:08X}' for f in sorted(missing)))

    with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write('"""Vanilla Skyrim.esm MGEF DATA blobs — GENERATED FILE.\n'
                '\n'
                'Produced by tools/generators/gen_vanilla_mgef_table.py from the\n'
                'references/Skyrim.esm dump; committed so the converter works\n'
                'without the dump.  {fid: (editor_id, DATA hex)}, where every\n'
                f'blob is exactly {MGEF_DATA_SIZE} bytes (the full TES5 MGEF\n'
                'DATA struct — the generator refuses to write a short one).\n'
                '"""\n\n'
                f'MGEF_DATA_SIZE = {MGEF_DATA_SIZE}\n\n'
                'VANILLA_MGEF_DATA = {\n')
        for fid in sorted(found):
            edid, data_hex = found[fid]
            f.write(f"    0x{fid:08X}: ({edid!r},\n        '{data_hex}'),\n")
        f.write('}\n')
    print(f'Wrote {len(found)} MGEF DATA blobs ({MGEF_DATA_SIZE} bytes each) '
           f'to {OUT}')


if __name__ == '__main__':
    main()
