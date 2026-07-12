"""Build a minimal alias-fill test plugin to factorize quest-alias failures.

Generates an ESP (masters: Skyrim.esm + Oblivion.esm) containing five
StartGameEnabled quests plus the required .seq file. Each quest isolates one
variable of the alias-fill chain, so a single in-game `sqv` sweep decodes
which layer is broken:

  TESTVanillaAlias  minimal quest built by our writer, alias forced to a
                    VANILLA ref (AstridRef) — writer sanity.
  TESTOblivionAlias same minimal quest, alias forced to Oblivion.esm's
                    ArvenaThelasRef — target-identity test.
  TESTRatsNoVmad    byte-level clone of the converted FGC01Rats QUST with the
                    VMAD removed — structure test.
  TESTRatsVmad      full clone including VMAD — VMAD-interaction test.
  TESTRatsDialog    full clone that ALSO owns a re-owned copy of FGC01Rats'
                    entire dialogue tree (DIAL+INFO+DLBR+DLVW) — the dialogue-
                    ownership probe. This is the one remaining structural
                    difference between the alias-FILLING clones above and the
                    original FGC01Rats, which does NOT fill: the real quest
                    owns dialogue, the clones don't. If TESTRatsDialog fails to
                    fill while TESTRatsVmad fills, quest-owned dialogue
                    registration is aborting alias fill at quest start.

All quests are Start Game Enabled, so aliases attempt to fill at new-game
start with no dialogue needed. In-game (new game):
  sqv TESTVanillaAlias
  sqv TESTOblivionAlias
  sqv TESTRatsNoVmad
  sqv TESTRatsVmad
  sqv TESTRatsDialog

Usage:
  python tools/make_alias_test_esp.py                # defaults
  python tools/make_alias_test_esp.py --source output/Oblivion.esm/Oblivion.esm \
      --quest FGC01Rats --outdir output/Oblivion.esm
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tes5_import.writer import (pack_record, pack_subrecord, pack_tes4_header,
                                pack_top_group, pack_group, pack_string_subrecord,
                                pack_formid_subrecord, pack_uint8_subrecord,
                                pack_uint16_subrecord, pack_uint32_subrecord,
                                _count_records_and_groups)
from tools.tes5_esm_reader import read_tes5_file

VANILLA_ASTRID_REF = 0x0001BDE8   # AstridRef (Skyrim.esm, unloaded interior)
OBLIVION_ARVENA_REF = 0x0103572C  # ArvenaThelasRef (master index 01 = Oblivion.esm)
ALIAS_FLAGS = 0x0292              # Optional|AllowDead|AllowDisabled|AllowReserved
SGE_FLAGS = 0x0011                # StartGameEnabled | StartsEnabled


def minimal_quest(edid: str, full: str, target_fid: int, objective_text: str,
                  sge: bool = True) -> bytes:
    """Build the subrecords of a minimal quest with one forced-ref alias and one
    objective pointing at it (mirrors the converter's QUST shape).

    sge=False builds a NON-start-game-enabled quest (DNAM flags 0) — like the
    real FGC01Rats, which is started only by SetStage/Start from a script, not
    at new-game. Console-drive it: `setstage <edid> 10` then `sqv <edid>`. If
    its alias fills, SetStage-started quests fill fine and the real quest's
    non-fill is about its dialogue tree, not the start path.
    """
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_string_subrecord('FULL', full)
    # DNAM: flags, priority 60, formver 0, unknown 0, type 8 (SideQuest)
    dnam_flags = SGE_FLAGS if sge else 0
    subs += pack_subrecord('DNAM', struct.pack('<HBBII', dnam_flags, 60, 0, 0, 8))
    subs += pack_subrecord('NEXT', b'')
    # One stage so the quest is journal-real
    subs += pack_subrecord('INDX', struct.pack('<HBB', 10, 0, 0))
    subs += pack_uint8_subrecord('QSDT', 0)
    subs += pack_string_subrecord('CNAM', f'{full} journal entry.')
    # One objective -> alias 0
    subs += pack_uint16_subrecord('QOBJ', 10)
    subs += pack_uint32_subrecord('FNAM', 0)
    subs += pack_string_subrecord('NNAM', objective_text)
    subs += pack_subrecord('QSTA', struct.pack('<iI', 0, 0))
    # Aliases
    subs += pack_uint32_subrecord('ANAM', 1)  # next alias id
    subs += pack_uint32_subrecord('ALST', 0)
    subs += pack_string_subrecord('ALID', 'TESTTarget00')
    subs += pack_uint32_subrecord('FNAM', ALIAS_FLAGS)
    subs += pack_formid_subrecord('ALFR', target_fid)
    subs += pack_formid_subrecord('VTCK', 0)
    subs += pack_subrecord('ALED', b'')
    return subs


def clone_quest(src_subs: list, edid: str, full: str, keep_vmad: bool) -> bytes:
    """Rebuild a converted QUST's subrecords with new EDID/FULL, forced SGE
    flags, and optionally the VMAD stripped. Everything else is verbatim."""
    out = b''
    for s in src_subs:
        if s.type == 'EDID':
            out += pack_string_subrecord('EDID', edid)
        elif s.type == 'FULL':
            out += pack_string_subrecord('FULL', full)
        elif s.type == 'VMAD' and not keep_vmad:
            continue
        elif s.type == 'DNAM':
            flags = struct.unpack_from('<H', s.data, 0)[0] | SGE_FLAGS
            out += pack_subrecord('DNAM', struct.pack('<H', flags) + s.data[2:])
        else:
            out += pack_subrecord(s.type, s.data)
    return out


# FormID subrecords whose targets must be remapped when cloning a quest's
# dialogue tree so the clones reference each other instead of the originals.
_DIALOG_FID_SUBS = {'QNAM', 'BNAM', 'SNAM', 'TNAM', 'TPIC', 'PNAM', 'TCLT', 'TCLF'}


def clone_dialogue_tree(recs: list, quest_fid: int, new_quest_fid: int,
                        next_fid: int, suffix: str):
    """Clone every DIAL (with its INFO children), DLBR, and DLVW owned by
    quest_fid, re-owned to new_quest_fid. In-set FormID references are
    remapped to the clones; out-of-set references stay (valid master links).

    Returns (dial_groups_bytes, dlbr_records_bytes, dlvw_records_bytes,
             cloned_record_count, next_fid).
    """
    def sub(r, sig):
        return next((s.data for s in r.subrecords if s.type == sig), None)

    def qnam_of(r):
        d = sub(r, 'QNAM')
        return struct.unpack('<I', d)[0] if d else 0

    dials = [r for r in recs if r.type == 'DIAL' and qnam_of(r) == quest_fid]
    dlbrs = [r for r in recs if r.type == 'DLBR' and qnam_of(r) == quest_fid]
    dlvws = [r for r in recs if r.type == 'DLVW' and qnam_of(r) == quest_fid]
    dial_fids = {r.form_id for r in dials}
    infos = [r for r in recs if r.type == 'INFO' and r.parent_dial in dial_fids]

    # FormID remap: original -> clone (quest + every cloned record)
    remap = {quest_fid: new_quest_fid}
    originals = dials + dlbrs + dlvws + infos
    for r in originals:
        remap[r.form_id] = next_fid
        next_fid += 1

    def rebuild(r, is_dial):
        out = b''
        for s in r.subrecords:
            if s.type == 'EDID':
                name = s.data.rstrip(b'\0').decode('latin1')
                out += pack_string_subrecord('EDID', f'{name}{suffix}')
            elif s.type in _DIALOG_FID_SUBS and len(s.data) == 4:
                fid = struct.unpack('<I', s.data)[0]
                out += pack_subrecord(s.type, struct.pack('<I', remap.get(fid, fid)))
            else:
                out += pack_subrecord(s.type, s.data)
        return out

    infos_by_dial = {}
    for r in infos:
        infos_by_dial.setdefault(r.parent_dial, []).append(r)

    dial_bytes = b''
    for d in dials:
        new_fid = remap[d.form_id]
        dial_bytes += pack_record('DIAL', new_fid, d.flags, rebuild(d, True))
        children = b''
        for i in infos_by_dial.get(d.form_id, []):
            children += pack_record('INFO', remap[i.form_id], i.flags,
                                    rebuild(i, False))
        if children:
            dial_bytes += pack_group(7, struct.pack('<I', new_fid), children)

    dlbr_bytes = b''.join(pack_record('DLBR', remap[r.form_id], r.flags,
                                      rebuild(r, False)) for r in dlbrs)
    dlvw_bytes = b''.join(pack_record('DLVW', remap[r.form_id], r.flags,
                                      rebuild(r, False)) for r in dlvws)

    print(f'  dialogue clone: {len(dials)} DIALs, {len(infos)} INFOs, '
          f'{len(dlbrs)} DLBRs, {len(dlvws)} DLVWs')
    return dial_bytes, dlbr_bytes, dlvw_bytes, len(originals), next_fid


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--source', default='output/Oblivion.esm/Oblivion.esm',
                    help='Converted ESM to clone the quest record from')
    ap.add_argument('--quest', default='FGC01Rats',
                    help='EditorID of the QUST to clone')
    ap.add_argument('--outdir', default='output/Oblivion.esm',
                    help='Directory to write TestAlias.esp and seq/ into')
    args = ap.parse_args()

    print(f'Reading {args.source} ...')
    _hdr, recs, _loc = read_tes5_file(args.source)
    src = next((r for r in recs if r.type == 'QUST' and any(
        s.type == 'EDID' and s.data.rstrip(b'\0').decode('latin1') == args.quest
        for s in r.subrecords)), None)
    if src is None:
        sys.exit(f'QUST {args.quest} not found in {args.source}')
    print(f'Cloning QUST {src.form_id:08X} {args.quest} '
          f'({len(src.subrecords)} subrecords)')

    quests = [
        (0x02000800, minimal_quest('TESTVanillaAlias', 'TEST Vanilla Alias',
                                   VANILLA_ASTRID_REF, 'Find Astrid')),
        (0x02000801, minimal_quest('TESTOblivionAlias', 'TEST Oblivion Alias',
                                   OBLIVION_ARVENA_REF, 'Find Arvena')),
        (0x02000802, clone_quest(src.subrecords, 'TESTRatsNoVmad',
                                 'TEST Rats NoVMAD', keep_vmad=False)),
        (0x02000803, clone_quest(src.subrecords, 'TESTRatsVmad',
                                 'TEST Rats VMAD', keep_vmad=True)),
        # The dialogue-ownership probe: same record again, but this clone also
        # OWNS a re-owned copy of the quest's full dialogue tree — the one
        # structural difference between the (filling) clones and the
        # (non-filling) original.
        (0x02000804, clone_quest(src.subrecords, 'TESTRatsDialog',
                                 'TEST Rats Dialog', keep_vmad=True)),
        # The start-path probe: NON-SGE minimal quest (like real FGC01Rats,
        # DNAM flags 0). Not started at new-game; console-drive it with
        # `setstage TESTRatsSetStage 10` then `sqv`. Fills ⇒ SetStage-start is
        # fine (blame dialogue tree); NONE ⇒ SetStage-started quests don't fill.
        (0x02000805, minimal_quest('TESTRatsSetStage', 'TEST Rats SetStage',
                                   OBLIVION_ARVENA_REF, 'Find Arvena', sge=False)),
    ]

    dial_bytes, dlbr_bytes, dlvw_bytes, n_dialog, _ = clone_dialogue_tree(
        recs, src.form_id, 0x02000804, 0x02001000, '_TESTDLG')

    grup = b''.join(pack_record('QUST', fid, 0, subs) for fid, subs in quests)
    group_blobs = [pack_top_group('QUST', grup)]
    if dial_bytes:
        group_blobs.append(pack_top_group('DIAL', dial_bytes))
    if dlbr_bytes:
        group_blobs.append(pack_top_group('DLBR', dlbr_bytes))
    if dlvw_bytes:
        group_blobs.append(pack_top_group('DLVW', dlvw_bytes))

    # CORRECT record count = records + groups (same rule as the main writer /
    # vanilla Skyrim.esm HEDR). The old hand count (len(quests)+n_dialog) omitted
    # the top-level GRUPs and the DIAL child groups.
    correct_count = sum(_count_records_and_groups(b) for b in group_blobs)

    def build(count):
        d = pack_tes4_header(['Skyrim.esm', 'Oblivion.esm'],
                             num_records=count, next_object_id=0x2000,
                             author='alias-fill factorization test',
                             is_esm=False)
        return d + b''.join(group_blobs)

    esp_path = os.path.join(args.outdir, 'TestAlias.esp')
    with open(esp_path, 'wb') as f:
        f.write(build(correct_count))
    print(f'Wrote {esp_path} (HEDR numRecords={correct_count})')

    # HEDR-undercount probe: identical bytes but a deliberately wrong (tiny)
    # record count, mimicking the bug in the real Oblivion.esm (38,585 vs 1.17M).
    # If aliases fill in TestAlias.esp but NOT in TestAliasBadHedr.esp, a wrong
    # HEDR count alone breaks alias fill — the "cascading failure in a big file"
    # is really "the header lies about how many records follow".
    bad_esp = os.path.join(args.outdir, 'TestAliasBadHedr.esp')
    with open(bad_esp, 'wb') as f:
        f.write(build(1))
    print(f'Wrote {bad_esp} (HEDR numRecords=1  <-- deliberately wrong)')

    # SEQ lists only the SGE quests (0x800..0x804). TESTRatsSetStage (0x805) is
    # deliberately non-SGE and console-driven, so it must NOT be in the .seq.
    # Both ESPs share the same FormIDs, so a .seq is written for each name; the
    # two ESPs must be tested SEPARATELY (never enable both at once).
    SGE_TEST_FIDS = {0x02000800, 0x02000801, 0x02000802, 0x02000803, 0x02000804}
    seq_dir = os.path.join(args.outdir, 'seq')
    os.makedirs(seq_dir, exist_ok=True)
    seq_body = b''.join(struct.pack('<I', fid) for fid, _ in quests
                        if fid in SGE_TEST_FIDS)
    for seq_name in ('TestAlias.seq', 'TestAliasBadHedr.seq'):
        with open(os.path.join(seq_dir, seq_name), 'wb') as f:
            f.write(seq_body)
    print(f'Wrote {seq_dir}\\TestAlias.seq and TestAliasBadHedr.seq')
    print('\nTest A (correct header): enable ONLY TestAlias.esp after '
          'Oblivion.esm, new game, then:')
    for name in ('TESTVanillaAlias', 'TESTOblivionAlias', 'TESTRatsNoVmad',
                 'TESTRatsVmad', 'TESTRatsDialog'):
        print(f'  sqv {name}')
    print('  setstage TESTRatsSetStage 10 ; sqv TESTRatsSetStage')
    print('\nTest B (HEDR undercount probe): DISABLE TestAlias.esp, enable ONLY '
          'TestAliasBadHedr.esp (same FormIDs, HEDR says 1 record), new game:')
    print('  sqv TESTRatsVmad   <-- if NONE here but filled in Test A, the wrong')
    print('                          HEDR count alone breaks alias fill.')
    print('Then, for the start-path probe (non-SGE — drive it manually):')
    print('  setstage TESTRatsSetStage 10')
    print('  sqv TESTRatsSetStage')


if __name__ == '__main__':
    main()
