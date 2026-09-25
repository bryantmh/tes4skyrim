"""Audit converted actor health against the TES4 source pool.

The engine derives an NPC's max health as

    manual:     RACE.StartingHealth + ACBS.HealthOffset
    auto-calc:  ... + (Level-1) * fNPCHealthLevelBonus
                    + (Level-1) * iAVDhmsLevelUp * wHealth / (wHealth + wMagicka + wStamina)

with fNPCHealthLevelBonus = 5.0, iAVDhmsLevelUp = 10 and w* the CLAS attribute
weights. TES4 DATA.Health is a FINAL, fully-calculated pool, so a faithful
conversion makes that sum reproduce it exactly. This reads the BUILT esm (not
the converter's own functions, so it catches anything the pipeline does after
ACBS is packed) and reports every actor whose health does not match its TES4
source.

Usage:
    python tools/audit/actor_health_audit.py output/Nehrim.esm/Nehrim.esm export/Nehrim.esm
    python tools/audit/actor_health_audit.py <built.esm> <export_dir> [--limit N] [--all]

See: docs/commentary/tes5_import_actors.md#health-offset
"""
import argparse
import collections
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tools.esm.tes5_esm_reader import read_tes5_file, _get

HEALTH_LEVEL_BONUS = 5
HMS_POINTS_PER_LEVEL = 10
AUTO_CALC_STATS = 0x10
PC_LEVEL_MULT = 0x80
DEFAULT_RACE_BASE = 50.0


def _sub(rec, sig):
    s = _get(rec, sig)
    return s.data if s is not None else None


def _fid(rec, sig):
    d = _sub(rec, sig)
    return struct.unpack('<I', d[:4])[0] if d and len(d) >= 4 else 0


def parse_export(path):
    out = {}
    cur = None
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            line = line.rstrip('\n')
            if line.startswith('---RECORD_BEGIN---'):
                if cur and cur.get('EditorID'):
                    out[cur['EditorID']] = cur
                cur = {}
            elif cur is not None and '=' in line:
                k, v = line.split('=', 1)
                cur[k.strip()] = v.split()[0].strip() if v.strip() else ''
    if cur and cur.get('EditorID'):
        out[cur['EditorID']] = cur
    return out


def load_built(esm):
    """(race StartingHealth by fid, class HMS weights by fid, NPC_ records)."""
    _hdr, recs, _loc = read_tes5_file(esm, parse_types={'RACE', 'NPC_', 'CLAS'})
    races, classes, npcs = {}, {}, []
    for r in recs:
        d = _sub(r, 'DATA')
        if r.type == 'RACE' and d and len(d) >= 48:
            races[r.form_id] = struct.unpack_from('<f', d, 36)[0]
        elif r.type == 'CLAS' and d and len(d) >= 35:
            classes[r.form_id] = tuple(d[32:35])
        elif r.type == 'NPC_':
            npcs.append(r)
    return races, classes, npcs


def engine_health(r, races, classes):
    """(max health the engine computes for a fixed-level NPC_, ACBS flags, level)."""
    acbs = _sub(r, 'ACBS')
    fl, _mo, _so, lvl, _cn, _cx, _sp, _dp, _tf, ho, _bo = struct.unpack(
        '<IhhHHHHhHhH', acbs[:24])
    final = races.get(_fid(r, 'RNAM'), DEFAULT_RACE_BASE) + ho
    if fl & AUTO_CALC_STATS:
        w = classes.get(_fid(r, 'CNAM'), (0, 0, 0))
        share = (lvl - 1) * HMS_POINTS_PER_LEVEL * w[0] // sum(w) if sum(w) else 0
        final += (lvl - 1) * HEALTH_LEVEL_BONUS + share
    return final, fl, lvl


def audit(src, races, classes, npcs):
    """Tally every built NPC_ against its source pool; returns (counts, mismatch rows)."""
    counts, rows = collections.Counter(), []
    for r in npcs:
        e = _sub(r, 'EDID')
        edid = e.rstrip(b'\x00').decode('ascii', 'replace') if e else None
        s = src.get(edid)
        acbs = _sub(r, 'ACBS')
        if not s or not acbs or len(acbs) < 24:
            counts['unmatched'] += 1
            continue
        t4h = int(s.get('DATA.Health', '50') or 50)
        final, fl, lvl = engine_health(r, races, classes)
        if t4h <= 0:
            counts['corpse'] += 1
        elif fl & PC_LEVEL_MULT:
            counts['level_mult'] += 1
        else:
            counts['dead_bug'] += final <= 0
            exact = abs(final - t4h) < 0.6
            counts['exact' if exact else 'mismatch'] += 1
            if not exact:
                rows.append((edid, t4h, final, bool(fl & AUTO_CALC_STATS), lvl))
    return counts, rows


def report(name, counts, rows, limit):
    """Print the tally and up to `limit` mismatch rows (None prints all)."""
    total = counts['exact'] + counts['mismatch']
    pct = 100.0 * counts['exact'] / total if total else 0.0
    print(f'{name}: fixed-level actors {total}')
    print(f'  exact      : {counts["exact"]} ({pct:.1f}%)')
    print(f'  mismatch   : {counts["mismatch"]}')
    print(f'  SPAWN-DEAD : {counts["dead_bug"]}   <- positive TES4 health but <=0 in game')
    print(f'  skipped    : {counts["level_mult"]} pc-level-mult, {counts["corpse"]} zero-health '
          f'corpse props, {counts["unmatched"]} not in export')
    if not rows:
        return
    print('\n  editorid                              tes4    built  autocalc  level')
    show = rows if limit is None else rows[:limit]
    for edid, t4h, final, auto, lvl in show:
        print(f'  {edid[:36]:36s} {t4h:7d} {final:8.0f} {str(auto):>9s} {lvl:6d}')
    if len(rows) > len(show):
        print(f'  ... {len(rows) - len(show)} more (--all)')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('esm', help='built TES5 esm')
    ap.add_argument('export_dir', help='TES4 export dir (NPC_.txt / CREA.txt)')
    ap.add_argument('--limit', type=int, default=20, help='mismatches to print')
    ap.add_argument('--all', action='store_true', help='print every mismatch')
    args = ap.parse_args()

    src = {}
    for sig in ('NPC_', 'CREA'):
        p = os.path.join(args.export_dir, f'{sig}.txt')
        if os.path.isfile(p):
            src.update(parse_export(p))
    if not src:
        print(f'no NPC_/CREA exports under {args.export_dir}')
        return 1
    counts, rows = audit(src, *load_built(args.esm))
    report(os.path.basename(args.esm), counts, rows, None if args.all else args.limit)
    return 0 if counts['mismatch'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
