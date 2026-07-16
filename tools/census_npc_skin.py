"""Census vanilla NPC skin-tone data from a Skyrim.esm text dump.

Joins RACE tint-mask definitions with NPC_ tint layers to answer:
  - Which TINI index is the Skin Tone layer for each race + gender?
  - What TINC colors / TINV interpolation values do vanilla NPCs actually
    use on that layer?
  - What QNAM (texture lighting) values accompany them?

Usage:
  python -m tools.census_npc_skin [--dump references/Skyrim.esm] [--race NordRace]
"""

import argparse
import re
import struct
from collections import Counter, defaultdict
from pathlib import Path

_SKIN_TONE_MASK_TYPE = 6  # RACE TINP enum: 6 = "Skin Tone"


def iter_records(path: Path):
    """Yield one ordered [(key, value), ...] list per record.

    Order matters: RACE tint layers omit TINP for mask-type None, so
    subrecords must be paired by walking the sequence, not by position
    in per-key lists.
    """
    rec = None
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.rstrip('\n')
            if line.startswith('---RECORD_BEGIN'):
                rec = []
                continue
            if line.startswith('---RECORD_END'):
                if rec is not None:
                    yield rec
                rec = None
                continue
            if rec is None or '=' not in line or line.startswith('#'):
                continue
            key, _, val = line.partition('=')
            # Strip the "  (uint=... float=...)" annotation suffix
            val = re.sub(r'\s+\(uint=.*\)$', '', val)
            rec.append((key, val))


def as_dict(rec) -> dict:
    d = defaultdict(list)
    for k, v in rec:
        d[k].append(v)
    return d


def race_skin_indices(race_path: Path) -> dict:
    """Return {race_edid: {'Male': tini, 'Female': tini}} for skin-tone layers.

    Within a RACE record the male tint-mask array is emitted before the
    female one, so skin-tone layers appear in [male, female] order.
    Pairing walks the subrecord sequence: a TINP belongs to the most
    recent TINI (layers with mask type None have no TINP at all).
    """
    out = {}
    for rec in iter_records(race_path):
        edid = ''
        cur_tini = None
        skin = []
        for key, val in rec:
            if key == 'EditorID':
                edid = val
            elif key == 'TINI':
                cur_tini = int(val)
            elif key == 'TINP' and cur_tini is not None:
                if int(val) == _SKIN_TONE_MASK_TYPE:
                    skin.append(cur_tini)
        if not skin:
            continue
        out[edid] = {'Male': skin[0],
                     'Female': skin[1] if len(skin) > 1 else skin[0]}
    return out


def _rgba(uint_hex: str) -> tuple:
    """8-digit hex uint (little-endian file bytes) → (R, G, B, A)."""
    u = int(uint_hex, 16)
    return (u & 0xFF, (u >> 8) & 0xFF, (u >> 16) & 0xFF, (u >> 24) & 0xFF)


def census(dump_dir: Path, only_race: str = None):
    races = race_skin_indices(dump_dir / 'RACE.txt')
    # race FormID -> edid (for joining NPC_.RNAM)
    fid_to_edid = {}
    for raw in iter_records(dump_dir / 'RACE.txt'):
        rec = as_dict(raw)
        edid = (rec.get('EditorID') or [''])[0]
        fid = (rec.get('FormID') or ['0'])[0]
        fid_to_edid[int(fid, 16) & 0x00FFFFFF] = edid

    stats = defaultdict(lambda: {'n': 0, 'colors': Counter(), 'tinv': Counter(),
                                 'tias': Counter(), 'qnam': []})

    for raw in iter_records(dump_dir / 'NPC_.txt'):
        rec = as_dict(raw)
        rnam = rec.get('RNAM')
        if not rnam:
            continue
        redid = fid_to_edid.get(int(rnam[0], 16) & 0x00FFFFFF, '?')
        if redid not in races:
            continue
        if only_race and redid != only_race:
            continue
        flags = int((rec.get('ACBS.Flags') or ['0'])[0], 16)
        gender = 'Female' if flags & 1 else 'Male'
        skin_idx = races[redid][gender]

        tinis = [int(v) for v in rec.get('TINI', [])]
        tincs = rec.get('TINC', [])
        tinvs = rec.get('TINV', [])
        tiass = rec.get('TIAS', [])
        for i, tini in enumerate(tinis):
            if tini != skin_idx or i >= len(tincs):
                continue
            key = (redid, gender)
            s = stats[key]
            s['n'] += 1
            tinv = int(tinvs[i], 16) if i < len(tinvs) else -1
            s['colors'][(_rgba(tincs[i]), tinv)] += 1
            if i < len(tinvs):
                s['tinv'][int(tinvs[i], 16)] += 1
            if i < len(tiass):
                s['tias'][int(tiass[i])] += 1
            qn = rec.get('QNAM.hex')
            if qn:
                s['qnam'].append(struct.unpack('<3f', bytes.fromhex(qn[0])))
            break

    print(f"{'race':24} {'gen':6} {'TINI':4} {'n':>4}  top colors (RGBA x count) | TINV | TIAS | mean QNAM")
    for (redid, gender), s in sorted(stats.items()):
        idx = races[redid][gender]
        top = ', '.join(f'{c}@v{v}x{n}' for (c, v), n in s['colors'].most_common(4))
        tinv = ', '.join(f'{v}x{n}' for v, n in s['tinv'].most_common(3))
        tias = ', '.join(f'{v}x{n}' for v, n in s['tias'].most_common(2))
        if s['qnam']:
            k = len(s['qnam'])
            mq = tuple(round(sum(q[i] for q in s['qnam']) / k, 3) for i in range(3))
        else:
            mq = None
        print(f'{redid:24} {gender:6} {idx:4} {s["n"]:4}  {top} | {tinv} | {tias} | {mq}')

    print('\nRace skin-tone TINI indices (all races with skin-tone layers):')
    for redid, gd in sorted(races.items()):
        if not only_race or redid == only_race:
            print(f'  {redid:30} Male={gd["Male"]:3}  Female={gd["Female"]:3}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dump', default='references/Skyrim.esm',
                    help='Skyrim.esm text dump directory')
    ap.add_argument('--race', default=None, help='limit to one race EditorID')
    a = ap.parse_args()
    census(Path(a.dump), a.race)
