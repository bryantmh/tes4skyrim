#!/usr/bin/env python3
"""
Build the definitive per-line voice table for a converted ESM.

One row per (INFO, response, resolved speaker): who says the line, the exact
file path the Skyrim engine will resolve at runtime, and whether that file
exists and is structurally valid audio. Speakers are resolved the way the
ENGINE sees them, not the way the importer intended:

  - positive subject-run GetIsID conditions name explicit NPCs; each NPC's
    row uses the NPC record's WRITTEN VTCK folder (what the engine reads).
    If the INFO also carries a GetIsVoiceType chain that excludes that NPC's
    VTCK, the NPC can never say the line -> GATE_BLOCKED (flagged: either
    intended exclusion or a converter contradiction).
  - otherwise the GetIsVoiceType chain defines the speaker set at
    voice-type granularity (one row per voice type).
  - otherwise the line is UNGATED: any speaker qualifies; one row per
    existing voice-type folder.

File statuses:
  OK             file exists and parses (FUZE header + lip + RIFF xwm, or
                 bare RIFF xwm), audio payload sane
  OK_NOLIP       valid but bare .xwm (no transcript -> mouth won't move)
  SUSPECT_SHORT  valid container but audio payload < min bytes (may be
                 truncated/corrupt)
  INVALID        file exists but fails structural parse
  MISSING        engine lookup path has no file; reason column explains
                 (no_source_recording / source_is_broken_stub / unknown)
  GATE_BLOCKED   NPC named by GetIsID but voice gate excludes their VTCK

Usage:
    python tools/voice_line_table.py                       # summary
    python tools/voice_line_table.py --csv temp/voice_table.csv
    python tools/voice_line_table.py --npc Azzan           # one NPC, verbose
    python tools/voice_line_table.py --status MISSING INVALID
"""

import argparse
import csv
import re
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.tes5_esm_reader import read_tes5_file, _get, _all, _zstring  # noqa: E402
from tools.voice_audit import voice_file_prefix  # noqa: E402

FUNC_GET_IS_ID = 72
FUNC_GET_IS_VOICE_TYPE = 426

_SRC_RE = re.compile(r'_([0-9a-fA-F]{8})_(\d+)\.(mp3|wav)$')

# xwm payload smaller than this is almost certainly a truncated encode
MIN_AUDIO_BYTES = 600


def parse_ctdas(rec):
    """Yield (func, param1, run_on, comp_value, comp_type) per CTDA."""
    for c in _all(rec, 'CTDA'):
        if len(c.data) < 28:
            continue
        yield (struct.unpack_from('<H', c.data, 8)[0],
               struct.unpack_from('<I', c.data, 12)[0],
               struct.unpack_from('<I', c.data, 20)[0],
               struct.unpack_from('<f', c.data, 4)[0],
               (c.data[0] >> 5) & 7)


def check_file(path: Path):
    """Structural validation. Returns (status, detail)."""
    try:
        b = path.read_bytes()
    except OSError as e:
        return 'INVALID', f'unreadable: {e}'
    if path.suffix.lower() == '.fuz':
        if len(b) < 12 or b[:4] != b'FUZE':
            return 'INVALID', 'bad FUZE magic'
        lipsz = struct.unpack_from('<I', b, 8)[0]
        xwm = b[12 + lipsz:]
        if len(xwm) < 44 or xwm[:4] != b'RIFF':
            return 'INVALID', f'no RIFF after lip ({lipsz}B lip, {len(b)}B total)'
        if len(xwm) < MIN_AUDIO_BYTES:
            return 'SUSPECT_SHORT', f'{len(xwm)}B audio'
        return 'OK', ''
    # bare xwm/wav
    if len(b) < 44 or b[:4] != b'RIFF':
        return 'INVALID', 'not RIFF'
    if len(b) < MIN_AUDIO_BYTES:
        return 'SUSPECT_SHORT', f'{len(b)}B audio'
    return 'OK_NOLIP', ''


def scan_source(source_dir: Path):
    """{fid24: {resp: max_size}} over the extracted Oblivion voice tree."""
    src = defaultdict(dict)
    if not source_dir or not source_dir.exists():
        return src
    for f in source_dir.rglob('*'):
        if not f.is_file():
            continue
        m = _SRC_RE.search(f.name)
        if not m:
            continue
        fid24 = int(m.group(1), 16) & 0xFFFFFF
        resp = int(m.group(2))
        src[fid24][resp] = max(src[fid24].get(resp, 0), f.stat().st_size)
    return src


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('--esm', default='output/Oblivion.esm/Oblivion.esm')
    ap.add_argument('--voice-dir',
                    default='output/Oblivion.esm/sound/Voice/Oblivion.esm')
    ap.add_argument('--source-dir',
                    default='export/Oblivion.esm/sound/Voice/oblivion.esm')
    ap.add_argument('--csv', default=None)
    ap.add_argument('--npc', default=None,
                    help='print every row for one NPC EditorID')
    ap.add_argument('--status', nargs='*', default=None,
                    help='print rows with these statuses')
    ap.add_argument('--examples', type=int, default=10)
    args = ap.parse_args()

    print(f'Parsing {args.esm} ...')
    header, records, _ = read_tes5_file(args.esm)
    vtyp_edid = {}
    npc_edid, npc_vtck = {}, {}
    qust_edid, dial_info = {}, {}
    infos = []
    for r in records:
        if r.type == 'VTYP':
            e = _get(r, 'EDID')
            vtyp_edid[r.form_id] = _zstring(e.data) if e else ''
        elif r.type in ('NPC_',):
            e = _get(r, 'EDID')
            v = _get(r, 'VTCK')
            npc_edid[r.form_id] = _zstring(e.data) if e else f'{r.form_id:08X}'
            npc_vtck[r.form_id] = (struct.unpack('<I', v.data)[0]
                                   if v and len(v.data) == 4 else 0)
        elif r.type == 'QUST':
            e = _get(r, 'EDID')
            qust_edid[r.form_id] = _zstring(e.data) if e else ''
        elif r.type == 'DIAL':
            e = _get(r, 'EDID')
            q = _get(r, 'QNAM')
            dial_info[r.form_id] = (
                _zstring(e.data) if e else '',
                struct.unpack('<I', q.data)[0] if q and len(q.data) == 4 else 0)
        elif r.type == 'INFO':
            if _get(r, 'TRDT') is not None:
                infos.append(r)
    print(f'  {len(infos)} voiced INFOs, {len(npc_edid)} NPCs')

    voice_dir = Path(args.voice_dir)
    folders = {d.name.lower(): d.name for d in voice_dir.iterdir()
               if d.is_dir()}
    src = scan_source(Path(args.source_dir))

    file_cache = {}

    def lookup(folder_edid: str, stem: str):
        """Engine-style lookup: .fuz first, then .xwm/.wav."""
        key = folder_edid.lower()
        real = folders.get(key)
        if real is None:
            return None
        for ext in ('.fuz', '.xwm', '.wav'):
            p = voice_dir / real / (stem + ext)
            ck = str(p).lower()
            if ck in file_cache:
                if file_cache[ck] is not None:
                    return p
                continue
            exists = p.exists()
            file_cache[ck] = p if exists else None
            if exists:
                return p
        return None

    rows = []           # dicts
    stat = Counter()
    for r in infos:
        fid24 = r.form_id & 0xFFFFFF
        dedid, qfid = dial_info.get(r.parent_dial, ('', 0))
        prefix = voice_file_prefix(qust_edid.get(qfid, ''), dedid)
        gate = set()
        ids = []
        for func, p1, run_on, comp, ctype in parse_ctdas(r):
            if func == FUNC_GET_IS_VOICE_TYPE:
                ve = vtyp_edid.get(p1)
                if ve:
                    gate.add(ve)
            elif (func == FUNC_GET_IS_ID and run_on == 0 and ctype == 0
                    and comp == 1.0):
                ids.append(p1)
        resp_nums = [t.data[12] for t in _all(r, 'TRDT') if len(t.data) >= 13]

        if ids:
            speakers = [('npc', n) for n in ids]
        elif gate:
            speakers = [('vt', g) for g in sorted(gate)]
        else:
            speakers = [('vt', folders[f]) for f in sorted(folders)]

        for kind, who in speakers:
            if kind == 'npc':
                name = npc_edid.get(who, f'{who:08X}')
                vt = vtyp_edid.get(npc_vtck.get(who, 0), '')
                if not vt:
                    status_all = 'NO_VTCK'
                elif gate and vt not in gate:
                    status_all = 'GATE_BLOCKED'
                else:
                    status_all = None
                folder = vt
            else:
                name = ''
                folder = who
                status_all = None

            for n in resp_nums:
                stem = f'{prefix}_{fid24:08x}_{n}'
                if status_all:
                    status, detail = status_all, folder
                else:
                    p = lookup(folder, stem)
                    if p is None:
                        srcsz = src.get(fid24, {}).get(n)
                        if srcsz is None:
                            status, detail = 'MISSING', 'no_source_recording'
                        elif srcsz < 1024:
                            status, detail = ('MISSING',
                                              f'source_is_broken_stub({srcsz}B)')
                        else:
                            status, detail = 'MISSING', 'unknown'
                    else:
                        status, detail = check_file(p)
                stat[status] += 1
                rows.append({'info': f'{r.form_id:08X}', 'resp': n,
                             'topic': dedid,
                             'quest': qust_edid.get(qfid, ''),
                             'speaker': name or f'<{folder}>',
                             'folder': folder, 'stem': stem,
                             'status': status, 'detail': detail})

    print('\n=== LINE TABLE SUMMARY ===')
    total = sum(stat.values())
    for s, c in stat.most_common():
        print(f'  {s}: {c}  ({100*c/total:.1f}%)')
    print(f'  total rows: {total}')

    # MISSING breakdown by reason
    reasons = Counter(x['detail'] for x in rows if x['status'] == 'MISSING')
    if reasons:
        print('\n  MISSING by reason:')
        for k, v in reasons.most_common():
            print(f'    {k}: {v}')
    unknown = [x for x in rows
               if x['status'] == 'MISSING' and x['detail'] == 'unknown']
    if unknown:
        print(f'\n--- MISSING/unknown (real bugs) — first {args.examples} ---')
        for x in unknown[:args.examples]:
            print(f"  {x['speaker']:24s} [{x['folder']}] {x['stem']}  "
                  f"topic={x['topic']}")
    bad = [x for x in rows if x['status'] in ('INVALID', 'SUSPECT_SHORT')]
    if bad:
        print(f'\n--- INVALID/SUSPECT — first {args.examples} ---')
        for x in bad[:args.examples]:
            print(f"  {x['speaker']:24s} [{x['folder']}] {x['stem']}  "
                  f"{x['detail']}")

    if args.npc:
        sel = [x for x in rows if x['speaker'].lower() == args.npc.lower()]
        print(f'\n--- {args.npc}: {len(sel)} line-rows ---')
        for x in sel:
            print(f"  {x['status']:13s} {x['stem']}  topic={x['topic']} "
                  f"{x['detail']}")
    if args.status:
        sel = [x for x in rows if x['status'] in args.status]
        print(f'\n--- rows with status {args.status}: {len(sel)} ---')
        for x in sel[:200]:
            print(f"  {x['speaker']:24s} [{x['folder']}] {x['stem']}  "
                  f"topic={x['topic']} {x['detail']}")

    if args.csv:
        out = Path(args.csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f'\nFull table: {out} ({len(rows)} rows)')


if __name__ == '__main__':
    main()
