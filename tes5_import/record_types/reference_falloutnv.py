"""FO3/FNV record types that carry no geometry: FLST, TXST, IMGS, LGTM, ECZN.

Four exist only to be pointed at -- `XCIM`, `LTMP`, `XEZN` on a CELL -- so
converting them is what makes those pointers legal rather than dangling
FormIDs the CK rejects. FLST is referenced by records and scripts alike.

See: docs/commentary/tes4_export_falloutnv.md#reference-only-types
"""

import struct

from ..base.text_reader import get_hex_bytes
from ..base.writer import (pack_formid_subrecord, pack_record,
                           pack_string_subrecord, pack_subrecord)
from .common import get_formid, get_int, get_str, prefix_path

#: FNV TXST slots; TES5 adds TX06/TX07, which no FO3/FNV record authors.
_TEXTURE_SLOTS = ('TX00', 'TX01', 'TX02', 'TX03', 'TX04', 'TX05')

#: Shortest FO3/FNV IMGS DNAM carrying the Cinematic group (offsets 100-131).
_IMGS_DNAM_MIN = 132

#: FNV DNAM offsets: Cinematic saturation, contrast value, brightness value.
_IMGS_SATURATION, _IMGS_CONTRAST, _IMGS_BRIGHTNESS = 100, 108, 112

#: FNV DNAM offset of the Cinematic tint: color RGB then amount, 4 floats.
_IMGS_TINT = 116

#: TES5 HNAM has no FO3/FNV source; these are the vanilla Skyrim.esm medians.
_IMGS_HNAM_MEDIANS = (37.0, 7.0, 0.4, 3.0, 0.55, 0.95, 1.5, 0.13, 12.0)

#: TES5 DNAM (depth of field): the strength/distance/range/radius vanilla uses.
_IMGS_DOF = (0.5, 20000.0, 20000.0, 16816)

#: Vanilla Skyrim.esm CNAM ranges, clamped per field: saturation, brightness, contrast.
_IMGS_CNAM_RANGES = ((0.4, 2.45), (0.9, 1.5), (1.0, 2.0))

#: The 40-byte prefix FNV and TES5 LGTM DATA share, before TES5's additions.
_LGTM_SHARED = 40

#: LGTM DATA offset 12: the fog near/far pair the light-fade distances mirror.
_LGTM_FOG_OFFSET = 12

#: DALC is required by TES5; 6 directional colors, specular and fresnel power.
_DALC_SIZE = 32

#: Vanilla fresnel power, the one non-zero DALC field in Skyrim.esm's 92 LGTM.
_DALC_FRESNEL = 1.0


#: Source FormIDs whose own record type this run writes; FLST members must be in it.
_CONVERTIBLE = set()


def index_convertible_records(by_type: dict, dispatch, skip,
                              master_export: dict = None) -> int:
    """Index every source FormID whose type will actually be written, the
    plugin's own records and its masters'.

    A FLST may name any type at all, and FO3/FNV lists reach ARMA and IMOD,
    neither of which converts. Writing those members verbatim produces a
    dangling FormID, which the engine treats as a broken reference.

    See: docs/commentary/tes4_export_falloutnv.md#formlist-members
    """
    _CONVERTIBLE.clear()
    recs = [(sig, r) for sig, rs in by_type.items() for r in rs]
    recs += [(r.get('Signature'), r) for r in (master_export or {}).values()]
    for sig, rec in recs:
        fid = rec.get('FormID')
        if fid and sig in dispatch and sig not in skip:
            _CONVERTIBLE.add(fid.upper())
    return len(_CONVERTIBLE)


def convertible(fid: str) -> bool:
    """Whether a source FormID names a record this run writes."""
    return not _CONVERTIBLE or (fid or '').upper() in _CONVERTIBLE


def convert_FLST(rec: dict) -> bytes:
    """A FNV FLST as a TES5 FLST: byte-identical, so members pass through.

    A member whose own record type this run does not write is DROPPED rather
    than left dangling. TES5 reads a FLST positionally only where a script
    indexes one, and every such list here is homogeneous.

    See: docs/commentary/tes4_export_falloutnv.md#formlist-members
    """
    subs = pack_string_subrecord('EDID', get_str(rec, 'EditorID'))
    i = 0
    while (key := f'LNAM[{i}]') in rec:
        i += 1
        if not convertible(rec.get(key)):
            continue
        subs += pack_formid_subrecord('LNAM', get_formid(rec, key))
    return pack_record('FLST', get_formid(rec, 'FormID'),
                       get_int(rec, 'RecordFlags'), subs)


def convert_TXST(rec: dict) -> bytes:
    """A FNV TXST as a TES5 TXST: its texture slots and specular flag.

    See: docs/commentary/tes4_export_falloutnv.md#texture-sets
    """
    subs = pack_string_subrecord('EDID', get_str(rec, 'EditorID'))
    subs += pack_subrecord('OBND', struct.pack(
        '<6h', get_int(rec, 'OBND.X1'), get_int(rec, 'OBND.Y1'),
        get_int(rec, 'OBND.Z1'), get_int(rec, 'OBND.X2'),
        get_int(rec, 'OBND.Y2'), get_int(rec, 'OBND.Z2')))
    for slot in _TEXTURE_SLOTS:
        path = get_str(rec, slot)
        if path:
            subs += pack_string_subrecord(slot, prefix_path(path))
    subs += pack_subrecord('DNAM', struct.pack(
        '<H', get_int(rec, 'DNAM.Flags')))
    return pack_record('TXST', get_formid(rec, 'FormID'),
                       get_int(rec, 'RecordFlags'), subs)


def _imgs_cinematic(dnam: bytes) -> bytes:
    """TES5 CNAM (saturation, brightness, contrast) from FNV's Cinematic group.

    Clamped to the range vanilla Skyrim.esm uses: FNV authors these on a
    different scale, and a value outside it washes the screen out entirely.
    """
    raw = (struct.unpack_from('<f', dnam, _IMGS_SATURATION)[0],
           struct.unpack_from('<f', dnam, _IMGS_BRIGHTNESS)[0],
           struct.unpack_from('<f', dnam, _IMGS_CONTRAST)[0])
    return struct.pack('<3f', *(min(max(v, lo), hi) for v, (lo, hi)
                                in zip(raw, _IMGS_CNAM_RANGES)))


def _imgs_tint(dnam: bytes) -> bytes:
    """TES5 TNAM (amount, R, G, B) from FNV's Cinematic tint (R, G, B, amount)."""
    r, g, b, amount = struct.unpack_from('<4f', dnam, _IMGS_TINT)
    return struct.pack('<4f', amount, r, g, b)


def convert_IMGS(rec: dict) -> bytes:
    """A FNV IMGS as a TES5 IMGS.

    The games do NOT share a layout: FNV packs everything into one 132/148/152
    byte DNAM, while TES5 splits it across HNAM/CNAM/TNAM/DNAM (36/12/16/16).
    Only the Cinematic and Tint groups have a shared meaning; TES5's HDR block
    has no FNV source and takes the vanilla medians.

    See: docs/commentary/tes4_export_falloutnv.md#imagespaces
    """
    dnam = get_hex_bytes(rec, 'DNAM')
    if len(dnam) < _IMGS_DNAM_MIN:
        return b''
    subs = pack_string_subrecord('EDID', get_str(rec, 'EditorID'))
    subs += pack_subrecord('HNAM', struct.pack('<9f', *_IMGS_HNAM_MEDIANS))
    subs += pack_subrecord('CNAM', _imgs_cinematic(dnam))
    subs += pack_subrecord('TNAM', _imgs_tint(dnam))
    subs += pack_subrecord('DNAM', struct.pack('<3f2xH', *_IMGS_DOF))
    return pack_record('IMGS', get_formid(rec, 'FormID'),
                       get_int(rec, 'RecordFlags'), subs)


def _lgtm_tes5_tail(fog_near: float, fog_far: float) -> bytes:
    """The 52 bytes TES5 appends to FNV's LGTM DATA, in xEdit field order.

    Ambient block, far fog color, fog max, the light-fade start/end pair and a
    trailing unused u32. FNV authors none of them: the ambient block is zero in
    56 of Skyrim.esm's 92 records and the unused u32 in all 92, so only the
    fade pair is filled, from the fog distances every vanilla record mirrors.
    """
    return (b'\x00' * _DALC_SIZE
            + b'\x00' * 4
            + struct.pack('<f', 0.0)
            + struct.pack('<2f', fog_near, fog_far)
            + b'\x00' * 4)


def convert_LGTM(rec: dict) -> bytes:
    """A FNV LGTM as a TES5 LGTM: the shared 40-byte prefix, extended.

    See: docs/commentary/tes4_export_falloutnv.md#lighting-templates
    """
    data = get_hex_bytes(rec, 'DATA')
    if len(data) < _LGTM_SHARED:
        return b''
    fog_near, fog_far = struct.unpack_from('<2f', data, _LGTM_FOG_OFFSET)
    subs = pack_string_subrecord('EDID', get_str(rec, 'EditorID'))
    subs += pack_subrecord('DATA', data[:_LGTM_SHARED]
                           + _lgtm_tes5_tail(fog_near, fog_far))
    subs += pack_subrecord('DALC', b'\x00' * (_DALC_SIZE - 4)
                           + struct.pack('<f', _DALC_FRESNEL))
    return pack_record('LGTM', get_formid(rec, 'FormID'),
                       get_int(rec, 'RecordFlags'), subs)


def convert_ECZN(rec: dict) -> bytes:
    """A FNV ECZN as a TES5 ECZN, form version 34+.

    TES5 inserts a `Location` FormID at offset 4 and appends a max level, so
    the fields are reordered rather than extended. TES4 has no LCTN source,
    so Location is NULL and max level 0 (unbounded).

    See: docs/commentary/tes4_export_falloutnv.md#encounter-zones
    """
    subs = pack_string_subrecord('EDID', get_str(rec, 'EditorID'))
    subs += pack_subrecord('DATA', struct.pack(
        '<IIbbBb',
        get_formid(rec, 'DATA.Owner'),
        0,
        get_int(rec, 'DATA.Rank'),
        get_int(rec, 'DATA.MinLevel'),
        get_int(rec, 'DATA.Flags'),
        0))
    return pack_record('ECZN', get_formid(rec, 'FormID'),
                       get_int(rec, 'RecordFlags'), subs)
