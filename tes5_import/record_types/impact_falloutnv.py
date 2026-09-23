"""FO3/FNV impacts and explosions: IPCT, IPDS and EXPL records, so a gun's
`INAM` names its own ballistic impact set and a projectile its explosion.

FNV's IPCT `DATA` (24 bytes) and `DODT` (36) are byte-compatible with
TES5's; the IPDS is twelve IPCT slots in a fixed material order that map to
Skyrim MATTs; EXPL keeps every DATA field but reorders them.
See: docs/commentary/tes4_export_falloutnv.md#impacts
"""

import struct

from ..base.text_reader import get_hex_bytes
from ..base.writer import (pack_formid_subrecord, pack_obnd, pack_record,
                           pack_string_subrecord, pack_subrecord)
from .common import get_float, get_formid, get_int, get_str, prefix_path
from .projectile_falloutnv import sndr_of
from .reference_falloutnv import convertible

#: IPCT DATA flag bit 0: the impact places no decal.
_NO_DECAL_DATA = 0x01
#: DODT decal data size, identical in both games.
_DODT_SIZE = 36
#: Skyrim MATT FormIDs per FNV IPDS slot name, the vanilla material each names first.
IMPACT_SLOT_MATERIALS = (
    ('Stone', (0x12F34, 0x12F36, 0x12F35)),
    ('Dirt', (0x12F38, 0x1C151)),
    ('Grass', (0x12F46,)),
    ('Glass', (0x12F39,)),
    ('Metal', (0x12F3C, 0x624B4, 0x12F3A)),
    ('Wood', (0x12F42, 0x12F41)),
    ('Organic', (0x12F3F,)),
    ('Cloth', (0x12F37, 0x388FC)),
    ('Water', (0x12F40,)),
    ('HollowMetal', (0x12F3B,)),
    ('OrganicBug', (0x10D5CC,)),
    ('OrganicGlow', (0xD309F,)),
)


def convert_IPCT(rec: dict, writer=None) -> bytes:
    """A FNV IPCT as a TES5 IPCT: model, DATA with result Default, the decal
    (DODT + TXST) when both are authored, else No Decal Data, and both
    sounds as SNDRs."""
    subs = pack_string_subrecord('EDID', get_str(rec, 'EditorID'))
    model = get_str(rec, 'Model.MODL')
    if model:
        subs += pack_string_subrecord('MODL', prefix_path(model))
    data = bytearray(bytes.fromhex(get_str(rec, 'DATA') or '').ljust(24, b'\0')[:24])
    dodt = get_hex_bytes(rec, 'DODT')
    decal = (len(dodt) == _DODT_SIZE and get_formid(rec, 'DNAM')
             and convertible(rec.get('DNAM')))
    if not decal:
        data[20] |= _NO_DECAL_DATA
    data[21:24] = b'\0\0\0'
    subs += pack_subrecord('DATA', bytes(data))
    if decal:
        subs += pack_subrecord('DODT', dodt)
        subs += pack_formid_subrecord('DNAM', get_formid(rec, 'DNAM'))
    for sig in ('SNAM', 'NAM1'):
        fid = sndr_of(writer, get_formid(rec, sig))
        if fid:
            subs += pack_formid_subrecord(sig, fid)
    return pack_record('IPCT', get_formid(rec, 'FormID'),
                       get_int(rec, 'RecordFlags'), subs)


def _link(rec: dict, key: str) -> int:
    """A FormID field, 0 when it names a record this run does not write."""
    return get_formid(rec, key) if convertible(rec.get(key)) else 0


def convert_EXPL(rec: dict, writer=None) -> bytes:
    """A FNV EXPL as a TES5 EXPL.

    TES5 DATA (52 bytes, the size 136 of Skyrim.esm's 143 use): light,
    sound 1, sound 2 (SNDRs), impact set, placed object, spawn projectile
    (none), force, damage, radius, IS radius, vertical offset (0, as in
    116), flags, sound level. FNV's radiation block has no TES5 field; a
    link to a record type this run does not write is nulled.
    See: docs/commentary/tes4_export_falloutnv.md#explosions
    """
    subs = pack_string_subrecord('EDID', get_str(rec, 'EditorID'))
    subs += pack_obnd(*(get_int(rec, f'OBND.{k}') for k in
                        ('X1', 'Y1', 'Z1', 'X2', 'Y2', 'Z2')))
    if get_str(rec, 'FULL'):
        subs += pack_string_subrecord('FULL', get_str(rec, 'FULL'))
    model = get_str(rec, 'Model.MODL')
    if model:
        subs += pack_string_subrecord('MODL', prefix_path(model))
    for sig in ('EITM', 'MNAM'):
        if _link(rec, sig):
            subs += pack_formid_subrecord(sig, _link(rec, sig))
    subs += pack_subrecord('DATA', struct.pack(
        '<6I5f2I', _link(rec, 'DATA.Light'),
        sndr_of(writer, get_formid(rec, 'DATA.Sound1')),
        sndr_of(writer, get_formid(rec, 'DATA.Sound2')),
        _link(rec, 'DATA.ImpactDataSet'), _link(rec, 'INAM'), 0,
        get_float(rec, 'DATA.Force'), get_float(rec, 'DATA.Damage'),
        get_float(rec, 'DATA.Radius'), get_float(rec, 'DATA.ISRadius'), 0.0,
        get_int(rec, 'DATA.Flags'), get_int(rec, 'DATA.SoundLevel', 1)))
    return pack_record('EXPL', get_formid(rec, 'FormID'),
                       get_int(rec, 'RecordFlags'), subs)


def convert_IPDS(rec: dict, writer=None) -> bytes:
    """A FNV IPDS as a TES5 IPDS: one PNAM (MATT, IPCT) pair per material
    each filled slot names."""
    subs = pack_string_subrecord('EDID', get_str(rec, 'EditorID'))
    for slot, materials in IMPACT_SLOT_MATERIALS:
        ipct = get_formid(rec, f'DATA.{slot}')
        if ipct:
            for matt in materials:
                subs += pack_subrecord('PNAM', struct.pack('<II', matt, ipct))
    return pack_record('IPDS', get_formid(rec, 'FormID'),
                       get_int(rec, 'RecordFlags'), subs)
