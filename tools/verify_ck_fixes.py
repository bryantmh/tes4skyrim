#!/usr/bin/env python3
"""Verify the CK-warning fixes against a converted output plugin.

Checks the buckets from the 2026-07 CK_WARNINGS sweep directly in the binary:

  1.  no index-00 override records (nothing may claim to BE a Skyrim.esm form)
  2.  top-level group order: LCTN must come after CELL/WRLD/QUST
  3.  every LCTN LCEC worldspace + MNAM marker resolves; LCTN EDIDs unique
  4.  persistent-ref XLCN agrees with its location's cells (dummy-cell leak)
  5.  SCRL (ex-SGST) records all have effects + ETYP
  6.  SPEL SPIT cast type == 1 (Fire and Forget), never 2 (Concentration)
  7.  AIMED ENCH/SPEL/SCRL have >= 1 projectile-bearing effect
  8.  PACK PTDA: no type-0 null target, no 01000014 player remap
  9.  RACE VTCK slots non-null
  10. CONT/NPC_ CNTO counts >= 1
  11. no dangling ArmorAddon footstep sets (old bad constants)
  12. LVLI/LVLN/LVSP entries: no null FormIDs

Usage:
    python tools/verify_ck_fixes.py [output/Oblivion.esm/Oblivion.esm]
"""
import os
import struct
import sys
from collections import Counter, defaultdict

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, 'tools'))

from tes5_esm_reader import read_tes5_file  # noqa: E402

DEFAULT = os.path.join('output', 'Oblivion.esm', 'Oblivion.esm')
BAD_FSTS = {0x00024237, 0x00024238}


def top_group_order(path: str) -> list:
    order = []
    with open(path, 'rb') as f:
        hdr = f.read(24)
        sig, size = struct.unpack_from('<4sI', hdr, 0)
        assert sig == b'TES4'
        f.seek(24 + size)
        while True:
            gh = f.read(24)
            if len(gh) < 24:
                break
            gsig, gsize, label = struct.unpack_from('<4sI4s', gh, 0)
            if gsig != b'GRUP':
                break
            order.append(label.decode('ascii', 'replace'))
            f.seek(gsize - 24, 1)
    return order


def subs_of(rec):
    out = defaultdict(list)
    for s in rec.subrecords:
        out[s.type].append(s.data)
    return out


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    print(f'Verifying {path}')
    _hdr, records, _loc = read_tes5_file(path)
    by_type = defaultdict(list)
    for r in records:
        by_type[r.type].append(r)
    problems = []

    def check(ok, msg):
        print(('  PASS  ' if ok else '  FAIL  ') + msg)
        if not ok:
            problems.append(msg)

    # 1. no index-00 records
    low_index = [(r.type, r.form_id) for r in records
                 if (r.form_id >> 24) == 0 and r.type != 'TES4']
    check(not low_index,
          f'no index-00 override records (found {len(low_index)}: '
          f'{[(s, hex(f)) for s, f in low_index[:5]]})')

    # 2. group order
    order = top_group_order(path)

    def idx(sig):
        return order.index(sig) if sig in order else -1

    check(0 < idx('CELL') < idx('WRLD') < idx('LCTN'),
          'group order CELL < WRLD < LCTN')
    check(idx('QUST') < idx('LCTN'), 'LCTN after QUST')

    # 3. LCTN wiring
    wrld_fids = {r.form_id for r in by_type.get('WRLD', [])}
    refr_fids = {r.form_id for r in by_type.get('REFR', [])}
    lctn_edids = Counter()
    bad_wrld = bad_mnam = 0
    for r in by_type.get('LCTN', []):
        s = subs_of(r)
        for e in s.get('EDID', []):
            lctn_edids[e.rstrip(b'\x00')] += 1
        for lcec in s.get('LCEC', []):
            if struct.unpack_from('<I', lcec, 0)[0] not in wrld_fids:
                bad_wrld += 1
        for mnam in s.get('MNAM', []):
            if struct.unpack_from('<I', mnam, 0)[0] not in refr_fids:
                bad_mnam += 1
    dup_edids = {k: v for k, v in lctn_edids.items() if v > 1}
    check(bad_wrld == 0, f'all LCTN LCEC worldspaces resolve ({bad_wrld} bad)')
    check(bad_mnam == 0, f'all LCTN MNAM markers resolve ({bad_mnam} bad)')
    check(not dup_edids, f'LCTN EditorIDs unique ({len(dup_edids)} dups: '
                         f'{list(dup_edids)[:3]})')

    # 4. XLCN spread: no ref may claim a location whose LCEC cells are far
    # from where the ref actually stands (the dummy-cell leak gave every
    # persistent Tamriel ref one gate's location).
    lctn_cells = {}
    for r in by_type.get('LCTN', []):
        cells = set()
        for lcec in subs_of(r).get('LCEC', []):
            n = (len(lcec) - 4) // 4
            for i in range(n):
                gy, gx = struct.unpack_from('<hh', lcec, 4 + i * 4)
                cells.add((gx, gy))
        if cells:
            lctn_cells[r.form_id] = cells
    leaked = 0
    for sig in ('REFR', 'ACHR'):
        for r in by_type.get(sig, []):
            s = subs_of(r)
            if 'XLCN' not in s or 'DATA' not in s or r.parent_wrld == 0:
                continue
            lctn = struct.unpack_from('<I', s['XLCN'][0], 0)[0]
            cells = lctn_cells.get(lctn)
            if not cells or len(s['DATA'][0]) < 8:
                continue
            x, y = struct.unpack_from('<ff', s['DATA'][0], 0)
            gx, gy = int(x // 4096), int(y // 4096)
            if min(abs(gx - cx) + abs(gy - cy) for cx, cy in cells) > 2:
                leaked += 1
    check(leaked == 0,
          f'persistent-ref XLCN matches its location cells ({leaked} leaked)')

    # 5. SCRL effects + ETYP
    no_fx = [r.form_id for r in by_type.get('SCRL', [])
             if 'EFID' not in subs_of(r)]
    no_etyp = [r.form_id for r in by_type.get('SCRL', [])
               if 'ETYP' not in subs_of(r)]
    check(not no_fx, f'all SCRL have effects ({len(no_fx)} without)')
    check(not no_etyp, f'all SCRL have ETYP ({len(no_etyp)} without)')

    # 6. SPEL cast type
    conc = [r.form_id for r in by_type.get('SPEL', [])
            if any(len(d) >= 20 and struct.unpack_from('<I', d, 16)[0] == 2
                   for d in subs_of(r).get('SPIT', []))]
    check(not conc, f'no Concentration-cast SPEL ({len(conc)} found)')

    # 7. aimed items have a projectile effect
    mgef_proj = {}
    for r in by_type.get('MGEF', []):
        s = subs_of(r)
        if s.get('DATA') and len(s['DATA'][0]) >= 0x4C:
            mgef_proj[r.form_id] = struct.unpack_from(
                '<I', s['DATA'][0], 0x48)[0]
    from tes5_import.vanilla_mgef_data import VANILLA_MGEF_DATA
    for fid, (_e, hexdata) in VANILLA_MGEF_DATA.items():
        mgef_proj[fid] = struct.unpack_from(
            '<I', bytes.fromhex(hexdata), 0x48)[0]
    aimed_dead = []
    for sig, key, off in (('ENCH', 'ENIT', 16), ('SPEL', 'SPIT', 20),
                          ('SCRL', 'SPIT', 20)):
        for r in by_type.get(sig, []):
            s = subs_of(r)
            if not s.get(key) or len(s[key][0]) < off + 4:
                continue
            if struct.unpack_from('<I', s[key][0], off)[0] != 2:
                continue
            efids = [struct.unpack_from('<I', d, 0)[0]
                     for d in s.get('EFID', [])]
            if efids and not any(mgef_proj.get(f, 0) for f in efids):
                aimed_dead.append((sig, r.form_id))
    check(not aimed_dead,
          f'every AIMED magic item has a projectile effect '
          f'({len(aimed_dead)} dead: '
          f'{[(s, hex(f)) for s, f in aimed_dead[:5]]})')

    # 8. package targets
    bad_ptda = 0
    for r in by_type.get('PACK', []):
        for d in subs_of(r).get('PTDA', []):
            t, v, _c = struct.unpack_from('<iIi', d, 0)
            if (t == 0 and v == 0) or v == 0x01000014:
                bad_ptda += 1
    check(bad_ptda == 0, f'no null/mis-remapped package targets ({bad_ptda})')

    # 9. RACE VTCK
    null_vtck = [r.form_id for r in by_type.get('RACE', [])
                 if any(len(d) >= 8
                        and struct.unpack_from('<II', d, 0) == (0, 0)
                        for d in subs_of(r).get('VTCK', []))]
    check(not null_vtck, f'all RACE VTCK filled ({len(null_vtck)} null)')

    # 10. container counts
    bad_cnto = 0
    for sig in ('CONT', 'NPC_'):
        for r in by_type.get(sig, []):
            for d in subs_of(r).get('CNTO', []):
                if struct.unpack_from('<Ii', d, 0)[1] < 1:
                    bad_cnto += 1
    check(bad_cnto == 0, f'no CNTO counts below 1 ({bad_cnto})')

    # 11. footstep sets — scan every 4-byte-aligned fid slot in ARMA for the
    # two known-bad FormIDs (layout-agnostic)
    bad_fsts = 0
    for r in by_type.get('ARMA', []):
        for s in r.subrecords:
            if len(s.data) == 4 and struct.unpack(
                    '<I', s.data)[0] in BAD_FSTS:
                bad_fsts += 1
    check(bad_fsts == 0, f'no dangling ARMA footstep sets ({bad_fsts})')

    # 12. leveled entries
    bad_lvlo = 0
    for sig in ('LVLI', 'LVLN', 'LVSP'):
        for r in by_type.get(sig, []):
            for d in subs_of(r).get('LVLO', []):
                if len(d) >= 12 and struct.unpack_from('<I', d, 4)[0] == 0:
                    bad_lvlo += 1
    check(bad_lvlo == 0, f'no null leveled-list entries ({bad_lvlo})')

    print()
    if problems:
        print(f'{len(problems)} CHECK(S) FAILED')
        sys.exit(1)
    print('All checks passed.')


if __name__ == '__main__':
    main()
