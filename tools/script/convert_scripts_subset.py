#!/usr/bin/env python3
"""Rebuild a SUBSET of converted Papyrus scripts, exactly as the pipeline would.

Why: `convert.py --scripts-only` regenerates ~16,000 scripts (minutes).  While
iterating on one quest's conversation machinery you want to see the emitted
Papyrus for a handful of scripts NOW.  This runs the SAME context build the
pipeline runs (`pipeline.build_script_context`: measured line durations,
unlock plan, menu plans ...) and then converts only the
records you name -- so what it emits is byte-identical to what the full stage
would emit for those files, never an approximation.

    python tools/script/convert_scripts_subset.py -f Oblivion.esm \\
        --scpt CharGenQuest CGEmperorScript BaurusScript \\
        --info 00032B0B 0004F791 --qust Charactergen \\
        --out temp/subset_out [--compile]

    --quest Charactergen   convenience: every SCPT the quest's speakers carry,
                           every INFO of the quest, and the QUST itself.

--out defaults to temp/subset_scripts/<plugin>.  Written .psc files carry the
usual TES4_ names; nothing under output/ is touched unless you point --out
there.  --compile runs tools/script/compile_papyrus.py on the result.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from tes5_import.text_reader import parse_export_file  # noqa: E402
from script_convert import pipeline  # noqa: E402
from script_convert.cross_ref import _export_dirs_with_masters  # noqa: E402


def _quest_records(export_dir: str, quest_edid: str, by_type: dict):
    """(scpt_edids, info_fids, qust_edids) for one quest, from the export."""
    quests = [q for q in by_type['QUST']
              if (q.get('EditorID') or '').lower() == quest_edid.lower()]
    if not quests:
        raise SystemExit(f'quest {quest_edid!r} not in {export_dir}')
    q = quests[0]
    qfid = q['FormID']
    infos = [r for r in by_type['INFO'] if r.get('QSTI.Quest') == qfid]
    scpts = set()
    # The quest's own script
    scri = q.get('SCRI')
    scpt_by_fid = {r['FormID']: r for r in by_type['SCPT']}
    if scri and scri in scpt_by_fid:
        scpts.add(scpt_by_fid[scri].get('EditorID', ''))
    # Every actor a GetIsID (func 72) condition names -> its NPC_ SCRI
    import struct
    npc_by_fid = {r['FormID']: r for r in parse_export_file(
        os.path.join(export_dir, 'NPC_.txt'))}
    for r in infos:
        i = 0
        while f'Condition[{i}].Raw' in r:
            b = bytes.fromhex(r[f'Condition[{i}].Raw'])
            i += 1
            if len(b) < 20:
                continue
            fn = struct.unpack('<H', b[8:10])[0]
            if fn != 72:
                continue
            p1 = '%08X' % struct.unpack('<I', b[12:16])[0]
            npc = npc_by_fid.get(p1)
            if npc and npc.get('SCRI') in scpt_by_fid:
                scpts.add(scpt_by_fid[npc['SCRI']].get('EditorID', ''))
    return sorted(s for s in scpts if s), [r['FormID'] for r in infos], [q['EditorID']]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('-f', '--plugin', default='Oblivion.esm')
    ap.add_argument('--scpt', nargs='*', default=[], help='SCPT EditorIDs')
    ap.add_argument('--info', nargs='*', default=[], help='INFO FormIDs (8 hex)')
    ap.add_argument('--qust', nargs='*', default=[], help='QUST EditorIDs')
    ap.add_argument('--quest', help='expand to the whole quest (scripts+INFOs+QUST)')
    ap.add_argument('--out', default=None)
    ap.add_argument('--compile', action='store_true')
    args = ap.parse_args(argv)

    export_dir = str(ROOT / 'export' / args.plugin)
    out = args.out or str(ROOT / 'temp' / 'subset_scripts' / args.plugin)
    os.makedirs(out, exist_ok=True)

    ctx = pipeline.build_script_context(export_dir, out)
    scpt_work, info_work, qust_work = (ctx['scpt_work'], ctx['info_work'],
                                       ctx['qust_work'])

    want_scpt = {s.lower() for s in args.scpt}
    want_info = {s.upper().zfill(8) for s in args.info}
    want_qust = {s.lower() for s in args.qust}
    if args.quest:
        by_type = {}
        for sig in ('DIAL', 'INFO', 'QUST', 'SCPT'):
            p = os.path.join(export_dir, f'{sig}.txt')
            by_type[sig] = parse_export_file(p) if os.path.exists(p) else []
        s, i, q = _quest_records(export_dir, args.quest, by_type)
        want_scpt |= {x.lower() for x in s}
        want_info |= {x.upper() for x in i}
        want_qust |= {x.lower() for x in q}
        print(f'  quest {args.quest}: {len(s)} scripts, {len(i)} INFOs')

    scpt = [r for r in scpt_work if (r.get('EditorID') or '').lower() in want_scpt]
    info = [r for r in info_work if (r.get('FormID') or '').upper() in want_info]
    qust = [r for r in qust_work if (r.get('EditorID') or '').lower() in want_qust]
    missing = want_scpt - {(r.get('EditorID') or '').lower() for r in scpt}
    if missing:
        print(f'  ** SCPT not found / no body: {sorted(missing)}')

    pipeline._script_worker_init(*ctx['initargs'])
    stats = pipeline._new_stats()
    if scpt:
        pipeline._merge_stats(stats, pipeline._script_worker_run(('scpt', scpt)))
    if info:
        pipeline._merge_stats(stats, pipeline._script_worker_run(('info', info)))
    if qust:
        pipeline._merge_stats(stats, pipeline._script_worker_run(('qust', qust)))
    pipeline._WORKER_CTX.clear()
    # The dangling-reference pass now runs at the WRITE (pipeline.write_psc),
    # so only the cross-script UDF cast pass is still a sweep.
    pipeline._fix_udf_call_arg_types(
        out, stats['udf_sigs'], stats['udf_callers'])
    print(f'  wrote {len(scpt)} SCPT / {len(info)} INFO / {len(qust)} QUST -> {out}')
    for e in stats['errors'][:20]:
        print('  ERR', e)

    if args.compile:
        import subprocess
        cmd = [sys.executable,
               str(ROOT / 'tools' / 'script' / 'compile_papyrus.py'),
               '--src', out, '--out', os.path.join(out, 'pex')]
        header_dirs = []
        for source_export in _export_dirs_with_masters(export_dir):
            source = (ROOT / 'output' / Path(source_export).name /
                      'scripts' / 'source')
            if source.is_dir():
                header_dirs.append(str(source))
        static_headers = ROOT / 'script_convert' / 'static_scripts'
        if static_headers.is_dir():
            header_dirs.append(str(static_headers))
        if header_dirs:
            cmd.extend(['--extra-headers', ';'.join(dict.fromkeys(header_dirs))])
        print('  ' + ' '.join(cmd))
        return subprocess.call(cmd)
    return 0


if __name__ == '__main__':
    sys.exit(main())
