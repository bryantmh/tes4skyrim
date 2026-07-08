"""Build an A/B test ESP that repoints a generated creature RACE at the
complete VANILLA canine asset set (behavior project, skeleton, dog.nif body).

Purpose: isolate 'records/cache layer' vs 'generated havok/mesh assets' for
the invisible-creature bug with ONE in-game test:
  - dog appears (as a vanilla dog) -> record generation + cache registration
    are fine; the fault is inside our generated assets.
  - dog still invisible -> the record/import side is at fault even though it
    mirrors vanilla byte patterns.

Usage:
  python tools/creature_vanilla_ab.py output/Oblivion.esm/Oblivion.esm \
      --race 0118EA8C --arma 0118EA8E [-o output/TES4CreatureABTest.esp]

The overridden ARMA gets vanilla dog.nif; the other ARMAs of the skin are
left alone (their Bip01-skinned parts simply won't bind to the vanilla
skeleton, which is harmless for this test).
"""

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tes5_import.writer import (pack_record, pack_subrecord,  # noqa: E402
                                pack_string_subrecord, pack_tes4_header,
                                pack_top_group)

VANILLA_BEHAVIOR = 'Actors\\Canine\\DogProject.hkx'
VANILLA_SKELETON = 'Actors\\Canine\\Character Assets Dog\\skeleton.nif'
VANILLA_BODY = 'Actors\\Canine\\Character Assets Dog\\dog.nif'


def find_record(esm_bytes: bytes, form_id: int):
    """Scan all GRUPs for a record with the given raw FormID.
    Returns (sig, flags, subrecord_bytes)."""
    pos, end = 0, len(esm_bytes)
    # skip TES4 header record
    tes4_size = struct.unpack_from('<I', esm_bytes, 4)[0]
    pos = 24 + tes4_size
    while pos < end:
        sig = esm_bytes[pos:pos + 4]
        size = struct.unpack_from('<I', esm_bytes, pos + 4)[0]
        if sig == b'GRUP':
            inner, gend = pos + 24, pos + size
            r = _scan_group(esm_bytes, inner, gend, form_id)
            if r:
                return r
            pos += size
        else:
            pos += 24 + size
    return None


def _scan_group(b: bytes, pos: int, end: int, form_id: int):
    while pos < end:
        sig = b[pos:pos + 4]
        size = struct.unpack_from('<I', b, pos + 4)[0]
        if sig == b'GRUP':
            r = _scan_group(b, pos + 24, pos + size, form_id)
            if r:
                return r
            pos += size
        else:
            fid = struct.unpack_from('<I', b, pos + 12)[0]
            if fid == form_id:
                flags = struct.unpack_from('<I', b, pos + 8)[0]
                data = b[pos + 24:pos + 24 + size]
                if flags & 0x00040000:
                    import zlib
                    data = zlib.decompress(data[4:])
                return sig.decode('ascii'), flags, data
            pos += 24 + size
    return None


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


def patch_race(subs_in: bytes) -> bytes:
    out = b''
    for sig, val in iter_subrecords(subs_in):
        if sig == 'ANAM':
            out += pack_string_subrecord('ANAM', VANILLA_SKELETON)
        elif sig == 'MODL' and val.rstrip(b'\x00').lower().endswith(b'.hkx'):
            out += pack_string_subrecord('MODL', VANILLA_BEHAVIOR)
        else:
            out += pack_subrecord(sig, val)
    return out


def patch_arma(subs_in: bytes) -> bytes:
    out = b''
    for sig, val in iter_subrecords(subs_in):
        if sig == 'MOD2':
            out += pack_string_subrecord('MOD2', VANILLA_BODY)
        else:
            out += pack_subrecord(sig, val)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('esm')
    ap.add_argument('--race', required=True, help='generated race FormID (hex)')
    ap.add_argument('--arma', required=True,
                    help='body ARMA FormID of that race (hex)')
    ap.add_argument('-o', '--out', default='output/TES4CreatureABTest.esp')
    args = ap.parse_args()

    race_fid = int(args.race, 16)
    arma_fid = int(args.arma, 16)
    esm = open(args.esm, 'rb').read()

    race = find_record(esm, race_fid)
    arma = find_record(esm, arma_fid)
    if not race or not arma:
        sys.exit(f'record not found: race={race is not None} '
                 f'arma={arma is not None}')
    assert race[0] == 'RACE' and arma[0] == 'ARMA', (race[0], arma[0])

    new_race = pack_record('RACE', race_fid, race[1] & ~0x00040000,
                           patch_race(race[2]))
    new_arma = pack_record('ARMA', arma_fid, arma[1] & ~0x00040000,
                           patch_arma(arma[2]))

    esm_name = os.path.basename(args.esm)
    header = pack_tes4_header(['Skyrim.esm', esm_name], num_records=2,
                              author='creature A/B test', is_esm=False)
    body = pack_top_group('RACE', new_race) + pack_top_group('ARMA', new_arma)
    with open(args.out, 'wb') as f:
        f.write(header + body)
    print(f'wrote {args.out} ({os.path.getsize(args.out)} bytes): '
          f'RACE {race_fid:08X} + ARMA {arma_fid:08X} -> vanilla canine '
          f'assets')


if __name__ == '__main__':
    main()
