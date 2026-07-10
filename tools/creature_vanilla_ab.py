"""Build A/B test ESPs that repoint a generated creature RACE at selected
VANILLA canine asset layers (behavior project / skeleton.nif / body NIF).

Purpose: bisect which generated asset layer breaks an engine subsystem with
in-game tests. The three layers swap independently via --layers:

  --layers behavior,skeleton,body   (default: all three — the original test)
      full vanilla asset set: 'does the fault live in records/cache or in
      the generated assets at all?'
  --layers behavior
      vanilla behavior project only (our skeleton.nif + body). The vanilla
      character file loads the VANILLA rig/clips, so the dog will look
      frozen/partially posed (bone names don't match our NIF) — judge ONLY
      whether the actor TRANSLATES/moves around, not animation quality.
      moves  -> our project stack (project/character/behavior/rig hkx,
                clips, animationdata, setdata) is the faulty layer.
      idle   -> our skeleton.nif/body side is implicated.
  --layers skeleton,body
      vanilla NIFs with OUR behavior project (complementary test; same
      frozen-pose caveat, our clips target Bip01 bone names).
      moves  -> our skeleton.nif/body is the faulty layer.
      idle   -> our project stack is the faulty layer.

The RACE can be given as --race/--arma FormIDs (hex) or found automatically
by --edid (RACE EditorID, e.g. TES4CreatureDogRace: the ARMA is located via
its RNAM back-reference).

Usage:
  python tools/creature_vanilla_ab.py output/Oblivion.esm/Oblivion.esm \
      --edid TES4CreatureDogRace --layers behavior \
      [-o output/TES4CreatureABTest.esp]
"""

import argparse
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tes5_import.writer import (pack_record, pack_subrecord,  # noqa: E402
                                pack_string_subrecord, pack_tes4_header,
                                pack_top_group)

VANILLA_BEHAVIOR = 'Actors\\Canine\\DogProject.hkx'
VANILLA_SKELETON = 'Actors\\Canine\\Character Assets Dog\\skeleton.nif'
VANILLA_BODY = 'Actors\\Canine\\Character Assets Dog\\dog.nif'


def iter_records(esm_bytes: bytes):
    """Yield (sig, flags, form_id, subrecord_bytes) for every record."""
    pos, end = 0, len(esm_bytes)
    tes4_size = struct.unpack_from('<I', esm_bytes, 4)[0]
    stack = [(24 + tes4_size, end)]
    while stack:
        pos, gend = stack.pop()
        while pos < gend:
            sig = esm_bytes[pos:pos + 4]
            size = struct.unpack_from('<I', esm_bytes, pos + 4)[0]
            if sig == b'GRUP':
                stack.append((pos + size, gend))
                gend = pos + size
                pos += 24
                continue
            flags = struct.unpack_from('<I', esm_bytes, pos + 8)[0]
            fid = struct.unpack_from('<I', esm_bytes, pos + 12)[0]
            data = esm_bytes[pos + 24:pos + 24 + size]
            if flags & 0x00040000:
                data = zlib.decompress(data[4:])
            yield sig.decode('ascii'), flags, fid, data
            pos += 24 + size


def iter_subrecords(data: bytes):
    pos = 0
    while pos < len(data):
        sig = data[pos:pos + 4].decode('ascii')
        size = struct.unpack_from('<H', data, pos + 4)[0]
        if sig == 'XXXX':
            real = struct.unpack_from('<I', data, pos + 6)[0]
            sig2 = data[pos + 10:pos + 14].decode('ascii')
            yield sig2, data[pos + 16:pos + 16 + real]
            pos += 16 + real
        else:
            yield sig, data[pos + 6:pos + 6 + size]
            pos += 6 + size


def get_edid(subs: bytes):
    for sig, val in iter_subrecords(subs):
        if sig == 'EDID':
            return val.rstrip(b'\x00').decode('utf-8', 'replace')
    return None


def find_by_edid(esm_bytes: bytes, edid: str):
    """Locate the RACE with the given EditorID and the first ARMA whose
    RNAM points back at it. Returns ((flags, subs), fid) pairs."""
    race = race_fid = None
    for sig, flags, fid, data in iter_records(esm_bytes):
        if sig == 'RACE' and get_edid(data) == edid:
            race, race_fid = (flags, data), fid
            break
    if race is None:
        sys.exit(f'RACE with EditorID {edid!r} not found')
    for sig, flags, fid, data in iter_records(esm_bytes):
        if sig != 'ARMA':
            continue
        for ssig, val in iter_subrecords(data):
            if ssig == 'RNAM' and len(val) == 4 \
                    and struct.unpack('<I', val)[0] == race_fid:
                return race, race_fid, (flags, data), fid
    sys.exit(f'no ARMA references RACE {race_fid:08X}')


def find_by_fid(esm_bytes: bytes, want_fid: int, want_sig: str):
    for sig, flags, fid, data in iter_records(esm_bytes):
        if fid == want_fid:
            assert sig == want_sig, (sig, want_sig)
            return (flags, data), fid
    sys.exit(f'{want_sig} {want_fid:08X} not found')


def patch_race(subs_in: bytes, layers: set) -> bytes:
    out = b''
    for sig, val in iter_subrecords(subs_in):
        if sig == 'ANAM' and 'skeleton' in layers:
            out += pack_string_subrecord('ANAM', VANILLA_SKELETON)
        elif (sig == 'MODL' and 'behavior' in layers
              and val.rstrip(b'\x00').lower().endswith(b'.hkx')):
            out += pack_string_subrecord('MODL', VANILLA_BEHAVIOR)
        else:
            out += pack_subrecord(sig, val)
    return out


def patch_arma(subs_in: bytes, layers: set) -> bytes:
    out = b''
    for sig, val in iter_subrecords(subs_in):
        if sig == 'MOD2' and 'body' in layers:
            out += pack_string_subrecord('MOD2', VANILLA_BODY)
        else:
            out += pack_subrecord(sig, val)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('esm')
    ap.add_argument('--edid', help='RACE EditorID to patch '
                                   '(e.g. TES4CreatureDogRace)')
    ap.add_argument('--race', help='generated race FormID (hex)')
    ap.add_argument('--arma', help='body ARMA FormID of that race (hex)')
    ap.add_argument('--layers', default='behavior,skeleton,body',
                    help='comma list of layers to point at vanilla assets: '
                         'behavior,skeleton,body (default: all)')
    ap.add_argument('-o', '--out', default=None)
    args = ap.parse_args()

    layers = {s.strip().lower() for s in args.layers.split(',') if s.strip()}
    bad = layers - {'behavior', 'skeleton', 'body'}
    if bad or not layers:
        sys.exit(f'bad --layers: {sorted(bad) or "(empty)"}')

    esm = open(args.esm, 'rb').read()
    if args.edid:
        race, race_fid, arma, arma_fid = find_by_edid(esm, args.edid)
    elif args.race and args.arma:
        race_fid = int(args.race, 16)
        arma_fid = int(args.arma, 16)
        race, _ = find_by_fid(esm, race_fid, 'RACE')
        arma, _ = find_by_fid(esm, arma_fid, 'ARMA')
    else:
        sys.exit('need --edid or both --race and --arma')

    out = args.out or ('output/TES4CreatureAB_%s.esp'
                       % '_'.join(sorted(layers)))
    new_race = pack_record('RACE', race_fid, race[0] & ~0x00040000,
                           patch_race(race[1], layers))
    new_arma = pack_record('ARMA', arma_fid, arma[0] & ~0x00040000,
                           patch_arma(arma[1], layers))

    esm_name = os.path.basename(args.esm)
    header = pack_tes4_header(['Skyrim.esm', esm_name], num_records=2,
                              author='creature A/B test', is_esm=False)
    body = pack_top_group('RACE', new_race) + pack_top_group('ARMA', new_arma)
    with open(out, 'wb') as f:
        f.write(header + body)
    print(f'wrote {out} ({os.path.getsize(out)} bytes): '
          f'RACE {race_fid:08X} + ARMA {arma_fid:08X} -> vanilla '
          f'{"+".join(sorted(layers))}')


if __name__ == '__main__':
    main()
