"""Build TESGameSelect_NoNehrimStart.esp: a developer patch, never shipped.

Stops Nehrim's opening from starting on a new game, so `coc` from the main
menu lands where asked with no Nehrim quest running:

  Charactergen, MQ00   Start Game Enabled and Starts Enabled cleared in DNAM
                       (Charactergen stage 5 is `player.moveto PlayerMarkerStartCell`)
  TES4PlayerScripts    NEHRIM_GlobalplayerScript.StartQuest preset to -1, the
                       value the script sets after its one `SetStage MQ00 1`

Every record is copied verbatim from the converted Nehrim.esm but for those
fields. Run by hand only; the plugin is written beside TESGameSelect.esp, and
Install & play leaves it out of the installed mod (`install_mods.SIDECAR_NAMES`).

Usage:
  python tools/release/make_nehrim_no_start_patch.py   # -> output/TESGameSelect/
  python tools/release/make_nehrim_no_start_patch.py --outdir some/dir
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tes5_import.base.writer import (pack_record, pack_subrecord, pack_tes4_header,
                                pack_top_group, count_records_and_groups)
from tes5_import.dialogue.quest import QUST_START_GAME_ENABLED, QUST_STARTS_ENABLED
from tools.esm.tes5_esm_reader import read_tes5_file

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PATCH_NAME = 'TESGameSelect_NoNehrimStart.esp'
NEHRIM_ESM = os.path.join(ROOT, 'output', 'Nehrim.esm', 'Nehrim.esm')
#: Charactergen (moves the player into the start cave) and MQ00, as Nehrim-local ids.
QUEST_IDS = (0x0002466E, 0x00000811)
#: The converter's quest hosting Nehrim's player scripts on its PlayerRef alias.
PLAYER_SCRIPTS_EDID = 'TES4PlayerScripts'
PLAYER_SCRIPT = 'NEHRIM_GlobalplayerScript'
START_PROPERTY = 'StartQuest'
START_DONE = -1
#: VMAD property type Int, status Edited.
PROP_INT_EDITED = (3, 1)
#: Record-header Compressed flag; the reader hands back decompressed bodies.
FLAG_COMPRESSED = 0x00040000


def _wstring(text: str) -> bytes:
    """A U16-length UTF-8 string, as VMAD stores names."""
    raw = text.encode('utf-8')
    return struct.pack('<H', len(raw)) + raw


def clear_start_flags(sig: str, data: bytes) -> bytes:
    """DNAM with Start Game Enabled and Starts Enabled cleared; any other subrecord as is."""
    if sig != 'DNAM':
        return data
    flags = struct.unpack_from('<H', data)[0]
    flags &= ~(QUST_START_GAME_ENABLED | QUST_STARTS_ENABLED)
    return struct.pack('<H', flags) + data[2:]


def preset_start_quest(sig: str, data: bytes) -> bytes:
    """VMAD with StartQuest = -1 added to PLAYER_SCRIPT's properties; others as is.

    A script entry is name, status byte, U16 property count, properties; the
    new property goes first. Refuses when the script is absent or ambiguous,
    or already carries the property.
    """
    if sig != 'VMAD':
        return data
    name = _wstring(PLAYER_SCRIPT)
    if data.count(name) != 1 or _wstring(START_PROPERTY) in data:
        raise SystemExit(f'{PLAYER_SCRIPTS_EDID} VMAD: expected one {PLAYER_SCRIPT} '
                         f'without {START_PROPERTY}; the layout changed')
    pos = data.index(name) + len(name) + 1
    count = struct.unpack_from('<H', data, pos)[0]
    prop = _wstring(START_PROPERTY) + struct.pack('<BBi', *PROP_INT_EDITED, START_DONE)
    return data[:pos] + struct.pack('<H', count + 1) + prop + data[pos + 2:]


def repack(rec, fix) -> bytes:
    """`rec` repacked uncompressed, each subrecord's data run through `fix`."""
    out = b''.join(pack_subrecord(s.type, fix(s.type, s.data)) for s in rec.subrecords)
    return pack_record('QUST', rec.form_id, rec.flags & ~FLAG_COMPRESSED, out)


def _edid(rec) -> str:
    """The record's EditorID, or ''."""
    return next((s.data.rstrip(b'\0').decode('utf-8') for s in rec.subrecords
                 if s.type == 'EDID'), '')


def patched_quests(recs, own_index: int) -> list:
    """[(label, patched record bytes)] for every quest the patch overrides."""
    starters = {own_index | fid for fid in QUEST_IDS}
    out = []
    for rec in recs:
        if rec.type != 'QUST':
            continue
        if rec.form_id in starters:
            out.append((f'{rec.form_id:08X}', repack(rec, clear_start_flags)))
        elif _edid(rec) == PLAYER_SCRIPTS_EDID:
            out.append((PLAYER_SCRIPTS_EDID, repack(rec, preset_start_quest)))
    return out


def build(outdir: str, nehrim_esm: str = NEHRIM_ESM) -> bool:
    """Write the patch into `outdir`; False (with a message) when a quest is missing."""
    if not os.path.isfile(nehrim_esm):
        print(f'  Skipped {PATCH_NAME}: no converted Nehrim.esm at {nehrim_esm}')
        return False
    header, recs, _loc = read_tes5_file(nehrim_esm, parse_types={'QUST'})
    masters = [s.data.rstrip(b'\0').decode('utf-8')
               for s in header.subrecords if s.type == 'MAST']
    quests = patched_quests(recs, len(masters) << 24)
    if len(quests) != len(QUEST_IDS) + 1:
        print(f'  ERROR: {PATCH_NAME}: found only {[q for q, _ in quests]} in {nehrim_esm}')
        return False
    group = pack_top_group('QUST', b''.join(data for _label, data in quests))
    data = pack_tes4_header(
        masters + ['Nehrim.esm'], num_records=count_records_and_groups(group),
        author='TESConversion',
        description='Developer patch: no Nehrim quest starts on a new game',
        is_esm=False) + group
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, PATCH_NAME)
    with open(path, 'wb') as f:
        f.write(data)
    print(f'Wrote {path} ({len(data)} bytes; overrides {[q for q, _ in quests]})')
    return True


def main() -> int:
    """CLI entry point; 0 when the patch was written."""
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--outdir', default=os.path.join(ROOT, 'output', 'TESGameSelect'),
                    help='Output folder (default: output/TESGameSelect)')
    ap.add_argument('--nehrim-esm', default=NEHRIM_ESM,
                    help='Converted Nehrim.esm (default: output/Nehrim.esm/Nehrim.esm)')
    args = ap.parse_args()
    return 0 if build(args.outdir, args.nehrim_esm) else 1


if __name__ == '__main__':
    sys.exit(main())
