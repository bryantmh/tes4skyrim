#!/usr/bin/env python3
"""
Audit ESM -> voice-file mappings for the converted plugin.

The naming rule is IMPORTED from the converter, never mirrored here: a
local copy silently drifted from the converter's and made this audit
agree with the very bug it was written to catch.

For every INFO in the built TES5 ESM, recomputes the voice filename the
engine will look up at runtime (Sound\\Voice\\<plugin>\\<VTYP>\\
<questEDID_topicEDID truncated>_<fid8 lo24>_<respnum>.fuz|.xwm) and checks it
against the files actually on disk. Classifies every miss:

  NO_SOURCE_AUDIO    Oblivion itself shipped no recording for this INFO
                     (silent in the original game too — not our bug).
  PREFIX_MISMATCH    audio for this InfoID exists on disk but under a
                     DIFFERENT prefix -> engine never finds it (naming bug).
  RESP_MISMATCH      right prefix on disk but response number differs.
  MISSING_IN_VTYP    file exists (right name) in other voice folders but not
                     in one the INFO's GetIsVoiceType conditions route to
                     (folder routing / relocation bug).
  NO_VTYP_FOLDER     a GetIsVoiceType param routes to a voice-type folder
                     that does not exist at all.
  NOT_ORGANIZED      source audio exists but nothing landed on disk under
                     any prefix (extraction/conversion loss).

Also cross-checks the importer's voicemap against the recomputed prefixes
and reports orphan disk files no INFO can resolve.

Usage:
    python tools/dialog/voice_audit.py
    python tools/dialog/voice_audit.py --esm output/Oblivion.esm/Oblivion.esm \
        --voice-dir output/Oblivion.esm/sound/Voice/Oblivion.esm \
        --source-dir export/Oblivion.esm/sound/Voice/oblivion.esm
    python tools/dialog/voice_audit.py --csv temp/voice_audit.csv --examples 5
"""

import argparse
import csv
import re
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from tools.esm.tes5_esm_reader import (read_tes5_file, _get, _all, _zstring,
                                   master_edids)
from tes5_import.dialogue.converter import voice_file_prefix

FUNC_GET_IS_VOICE_TYPE = 426
FUNC_GET_IS_ID = 72

_DISK_RE = re.compile(r'^(.+)_([0-9a-f]{8})_(\d+)\.(fuz|xwm|wav|mp3|lip)$',
                      re.IGNORECASE)


def _codec_of(source_dir) -> str:
    """The text codec the converted plugin was written in.

    Read off the export's `_HEADER.txt` ENCODING line, searching up from the
    voice tree when the audit points inside it. Western exports predate the
    line and decode as cp1252, which is what they were written with.
    """
    from core.plugin_masters import export_encoding
    folder = Path(source_dir) if source_dir else None
    for _ in range(5):
        if folder is None or not str(folder):
            break
        if (folder / "_HEADER.txt").is_file():
            return export_encoding(str(folder))
        folder = folder.parent
    return "cp1252"


def _edid_text(data: bytes, codec: str) -> str:
    """A null-terminated EditorID in the export's codec, never raising."""
    try:
        return data.rstrip(b"\x00").decode(codec, errors="replace")
    except (LookupError, ValueError):
        return _zstring(data)


def parse_esm(esm_path, codec: str = "cp1252"):
    """Return (infos, dial_by_fid, qust_edid, vtyp_edid).

    EditorIDs decode in `codec`, the export's codec: decoding Cyrillic
    voice-type names as UTF-8 yields U+FFFD and every folder lookup misses.
    infos: list of dicts {fid, dial_fid, vtyps(set of VTYP fids),
                          resp_nums(list), has_getisid(bool)}
    """
    header, records, _loc = read_tes5_file(str(esm_path))
    dial_by_fid = {}
    qust_edid = {}
    vtyp_edid = {}
    infos = []
    for rec in records:
        if rec.type == 'DIAL':
            edid = _get(rec, 'EDID')
            qnam = _get(rec, 'QNAM')
            dial_by_fid[rec.form_id] = (
                _edid_text(edid.data, codec) if edid else '',
                struct.unpack('<I', qnam.data)[0] if qnam and len(qnam.data) == 4 else 0)
        elif rec.type == 'QUST':
            edid = _get(rec, 'EDID')
            qust_edid[rec.form_id] = _edid_text(edid.data, codec) if edid else ''
        elif rec.type == 'VTYP':
            edid = _get(rec, 'EDID')
            vtyp_edid[rec.form_id] = _edid_text(edid.data, codec) if edid else ''
        elif rec.type == 'INFO':
            vtyps = set()
            has_getisid = False
            for c in _all(rec, 'CTDA'):
                if len(c.data) < 16:
                    continue
                func = struct.unpack_from('<H', c.data, 8)[0]
                param1 = struct.unpack_from('<I', c.data, 12)[0]
                if func == FUNC_GET_IS_VOICE_TYPE:
                    vtyps.add(param1)
                elif func == FUNC_GET_IS_ID:
                    has_getisid = True
            resp_nums = []
            for t in _all(rec, 'TRDT'):
                if len(t.data) >= 13:
                    resp_nums.append(t.data[12])
            if resp_nums:
                infos.append({'fid': rec.form_id,
                              'dial_fid': rec.parent_dial,
                              'vtyps': vtyps,
                              'resp_nums': resp_nums,
                              'has_getisid': has_getisid})
    # A DEPENDENT plugin's topics are often owned by a quest in a MASTER (907
    # of Morroblivion's DIALs name an Oblivion.esm quest), and the engine
    # builds the voice filename from that quest's EditorID all the same.
    # Without the masters every one of those recomputes to an EMPTY prefix and
    # the audit reports a mismatch against a voicemap that is actually right —
    # the audit would agree with the bug it exists to catch.
    qust_edid.update(_master_qust_edids(esm_path, header))
    return infos, dial_by_fid, qust_edid, vtyp_edid


def _master_qust_edids(esm_path, header) -> dict:
    """{QUST FormID (this plugin's index space) -> EditorID} for every master."""
    return master_edids(esm_path, header, 'QUST')


def scan_voice_dir(voice_dir: Path):
    """Return (folders, files_by_folder, disk_by_fid).

    files_by_folder: {folder_lower: set of 'prefix_fid8_n' stems (no ext)}
    disk_by_fid: {fid24: set of (folder_lower, prefix, resp_num)}
    """
    folders = {}
    files_by_folder = defaultdict(set)
    disk_by_fid = defaultdict(set)
    for d in sorted(voice_dir.iterdir()):
        if not d.is_dir():
            continue
        fl = d.name.lower()
        folders[fl] = d.name
        for f in d.iterdir():
            if not f.is_file():
                continue
            m = _DISK_RE.match(f.name)
            if not m or m.group(4).lower() == 'lip':
                continue
            prefix, fid_hex, num = (m.group(1).lower(), m.group(2).lower(),
                                    int(m.group(3)))
            files_by_folder[fl].add(f'{prefix}_{fid_hex}_{num}')
            disk_by_fid[int(fid_hex, 16) & 0xFFFFFF].add((fl, prefix, num))
    return folders, files_by_folder, disk_by_fid


def scan_source_dir(source_dir: Path):
    """{fid24: set of (race, gender, resp_idx)} from the Oblivion extraction."""
    src = defaultdict(set)
    if not source_dir or not source_dir.exists():
        return src
    for f in source_dir.rglob('*'):
        if not f.is_file():
            continue
        m = _DISK_RE.match(f.name)
        if not m or m.group(4).lower() == 'lip':
            continue
        fid24 = int(m.group(2), 16) & 0xFFFFFF
        gender = f.parent.name.lower()[:1]
        race = f.parent.parent.name.lower()
        src[fid24].add((race, gender, int(m.group(3))))
    return src


def load_voicemap(path: Path):
    vm = {}
    if not path or not path.exists():
        return vm
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line or line.startswith('#') or '=' not in line:
            continue
        fid_hex, value = line.split('=', 1)
        prefix = value.split('\t', 1)[0]
        vtyps = value.split('\t', 1)[1].split(',') if '\t' in value else []
        try:
            vm[int(fid_hex, 16) & 0xFFFFFF] = (prefix.lower(), vtyps)
        except ValueError:
            pass
    return vm


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--esm', default='output/Oblivion.esm/Oblivion.esm')
    ap.add_argument('--voice-dir',
                    default='output/Oblivion.esm/sound/Voice/Oblivion.esm')
    ap.add_argument('--source-dir',
                    default='export/Oblivion.esm/sound/Voice/oblivion.esm')
    ap.add_argument('--voicemap', default=None,
                    help='defaults to <esm>.voicemap.txt')
    ap.add_argument('--csv', default=None, help='write full per-miss CSV')
    ap.add_argument('--examples', type=int, default=8,
                    help='examples to print per problem class')
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

    esm_path = Path(args.esm)
    voice_dir = Path(args.voice_dir)
    source_dir = Path(args.source_dir) if args.source_dir else None
    vm_path = Path(args.voicemap) if args.voicemap else \
        esm_path.with_name(esm_path.name + '.voicemap.txt')

    print(f'Parsing ESM: {esm_path}')
    codec = _codec_of(args.source_dir)
    print(f'  Text codec: {codec}')
    infos, dial_by_fid, qust_edid, vtyp_edid = parse_esm(esm_path, codec)
    print(f'  {len(infos)} voiced INFOs, {len(dial_by_fid)} DIALs, '
          f'{len(qust_edid)} QUSTs, {len(vtyp_edid)} VTYPs')

    print(f'Scanning voice dir: {voice_dir}')
    folders, files_by_folder, disk_by_fid = scan_voice_dir(voice_dir)
    n_files = sum(len(v) for v in files_by_folder.values())
    print(f'  {len(folders)} voice-type folders, {n_files} audio files')

    print(f'Scanning source dir: {source_dir}')
    src_by_fid = scan_source_dir(source_dir)
    print(f'  {len(src_by_fid)} distinct source InfoIDs')

    voicemap = load_voicemap(vm_path)
    print(f'Voicemap: {len(voicemap)} entries ({vm_path})')

    all_folders = set(folders)
    misses = []          # (class, vtyp_folder, expected_stem, info_fid, note)
    vm_mismatch = []
    stats = Counter()
    expected_stems = defaultdict(set)   # folder -> stems we expect there
    no_gate_reported = set()            # ungated stems already counted as a miss

    for info in infos:
        fid = info['fid']
        fid24 = fid & 0xFFFFFF
        dial_edid, qnam = dial_by_fid.get(info['dial_fid'], ('', 0))
        prefix = voice_file_prefix(qust_edid.get(qnam, ''), dial_edid)

        # Voicemap consistency (docs: must be 0 mismatches)
        vm_entry = voicemap.get(fid24)
        if vm_entry is not None and vm_entry[0] != prefix:
            vm_mismatch.append((fid24, prefix, vm_entry[0]))

        # Folders the engine may look in: the GetIsVoiceType set, else any
        # (no voice gate = any speaker's folder must have it).
        if info['vtyps']:
            target_folders = []
            for vt in sorted(info['vtyps']):
                edid = vtyp_edid.get(vt, '')
                if not edid:
                    stats['vtyp_param_unresolved'] += 1
                    continue
                target_folders.append(edid.lower())
        else:
            target_folders = sorted(all_folders)
            stats['no_voice_gate_infos'] += 1

        for tf in target_folders:
            if tf not in all_folders:
                misses.append(('NO_VTYP_FOLDER', tf,
                               f'{prefix}_{fid24:08x}_*', fid, dial_edid))
                stats['NO_VTYP_FOLDER'] += 1
                continue
            for n in info['resp_nums']:
                stem = f'{prefix}_{fid24:08x}_{n}'
                expected_stems[tf].add(stem)
                if stem in files_by_folder[tf]:
                    stats['found'] += 1
                    continue
                if not info['vtyps']:
                    # No GetIsVoiceType gate, so the engine resolves the
                    # SPEAKER's own folder at runtime and this audit cannot
                    # know which one that is. Present in ANY folder is
                    # therefore reachable. Demanding it in every folder turned
                    # one line into one miss per folder: on Nehrim that
                    # inflated 405 ungated lines into 2042 "misses" and made a
                    # correctly routed voice tree look broken.
                    if any(stem in files_by_folder[f] for f in all_folders):
                        stats['no_gate_reachable'] += 1
                        continue
                    if stem in no_gate_reported:
                        continue        # report a genuine miss once, not per folder
                    no_gate_reported.add(stem)
                # Classify the miss
                on_disk = disk_by_fid.get(fid24, set())
                same_prefix_elsewhere = [e for e in on_disk
                                         if e[1] == prefix and e[2] == n]
                other_prefix = {e[1] for e in on_disk if e[1] != prefix}
                if same_prefix_elsewhere:
                    cls = 'MISSING_IN_VTYP'
                    note = ('on disk in: '
                            + ','.join(sorted({e[0] for e in same_prefix_elsewhere})))
                elif other_prefix:
                    cls = 'PREFIX_MISMATCH'
                    note = 'disk prefix: ' + ','.join(sorted(other_prefix)[:3])
                elif any(e[2] == n for e in on_disk):
                    cls = 'RESP_MISMATCH'
                    note = f'disk resp nums: {sorted({e[2] for e in on_disk})}'
                elif fid24 in src_by_fid:
                    if any(s[2] == n for s in src_by_fid[fid24]):
                        cls = 'NOT_ORGANIZED'
                        note = ('source folders: '
                                + ','.join(sorted({f"{s[0]}/{s[1]}"
                                                   for s in src_by_fid[fid24]})))
                    else:
                        cls = 'NO_SOURCE_AUDIO'
                        note = (f'source has resp '
                                f'{sorted({s[2] for s in src_by_fid[fid24]})}, '
                                f'not {n}')
                else:
                    cls = 'NO_SOURCE_AUDIO'
                stats[cls] += 1
                misses.append((cls, tf, stem, fid, dial_edid))

    # Orphans: disk stems no INFO expects in that folder. A stem expected in
    # ANOTHER folder is a harmless unreferenced copy (generic lines land in
    # every recorded race folder; the gate may cover fewer); a stem expected
    # NOWHERE is stale (old prefix / deleted INFO) and worth attention.
    all_expected = set()
    for stems in expected_stems.values():
        all_expected |= stems
    orphans = []
    copies = 0
    for tf, stems in files_by_folder.items():
        for s in sorted(stems - expected_stems.get(tf, set())):
            if s in all_expected:
                copies += 1
            else:
                orphans.append((tf, s))

    print('\n=== SUMMARY ===')
    print(f"  expected file lookups OK: {stats['found']}")
    for cls in ('NO_SOURCE_AUDIO', 'PREFIX_MISMATCH', 'RESP_MISMATCH',
                'MISSING_IN_VTYP', 'NO_VTYP_FOLDER', 'NOT_ORGANIZED'):
        if stats[cls]:
            print(f'  {cls}: {stats[cls]}')
    print(f"  INFOs with no voice gate: {stats['no_voice_gate_infos']} "
          f"({stats['no_gate_reachable']} lookups reachable in the speaker's "
          f"own folder, counted once)")
    if stats['vtyp_param_unresolved']:
        print(f"  GetIsVoiceType params not resolving to a VTYP record: "
              f"{stats['vtyp_param_unresolved']}")
    print(f'  voicemap prefix mismatches: {len(vm_mismatch)}')
    print(f'  unreferenced copies (stem expected in another folder): {copies}')
    print(f'  stale orphan files (stem expected nowhere): {len(orphans)}')

    by_cls = defaultdict(list)
    for m in misses:
        by_cls[m[0]].append(m)
    for cls, items in sorted(by_cls.items()):
        print(f'\n--- {cls} ({len(items)}) — first {args.examples} ---')
        for cls_, tf, stem, fid, dial in items[:args.examples]:
            print(f'  [{tf}] {stem}  (INFO {fid:08X}, topic {dial}) ')
        # folder histogram for routing problems
        fh = Counter(m[1] for m in items)
        print('  by folder: ' + ', '.join(f'{k}={v}'
                                          for k, v in fh.most_common(8)))

    if vm_mismatch:
        print(f'\n--- voicemap mismatches — first {args.examples} ---')
        for fid24, computed, mapped in vm_mismatch[:args.examples]:
            print(f'  {fid24:06X}: esm computes "{computed}" '
                  f'voicemap has "{mapped}"')

    if orphans:
        print(f'\n--- stale orphan files — first {args.examples} ---')
        for tf, s in orphans[:args.examples]:
            print(f'  [{tf}] {s}')
        fh = Counter(o[0] for o in orphans)
        print('  by folder: ' + ', '.join(f'{k}={v}'
                                          for k, v in fh.most_common(16)))

    if args.csv:
        out = Path(args.csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['class', 'vtyp_folder', 'expected_stem',
                        'info_fid', 'topic_edid'])
            for m in misses:
                w.writerow([m[0], m[1], m[2], f'{m[3]:08X}', m[4]])
            for tf, s in orphans:
                w.writerow(['ORPHAN', tf, s, '', ''])
        print(f'\nFull report: {out}')


if __name__ == '__main__':
    main()
