"""
TES3 (Morrowind) binary file reader.

TES3 shares no container structure with TES4: records are a flat sequence with
no GRUP tree, carry no FormID and no version field, and identify each other by
case-insensitive string. Subrecord sizes are u32, not u16.

    record header:    16 bytes (type[4] + dataSize[4] + unused[4] + flags[4])
    subrecord header:  8 bytes (type[4] + dataSize[4])

Deletion is signalled by a DELE subrecord, not by a header flag.

See: docs/commentary/tes4_export_morrowind.md#the-tes3-container
"""

import mmap
import struct
from dataclasses import dataclass, field

from .tes4_reader import Subrecord

RECORD_HEADER_SIZE = 16
SUBRECORD_HEADER_SIZE = 8

#: File magic of the TES3 header record, which is also the whole-file magic.
TES3_MAGIC = b"TES3"


@dataclass
class Tes3Record:
    """A parsed TES3 record. `record_id` is the NAME subrecord, the identity."""
    type: str
    flags: int
    subrecords: list = field(default_factory=list)
    record_id: str = ""
    offset: int = -1
    deleted: bool = False


def is_tes3(path: str) -> bool:
    """True when this file is a Morrowind plugin rather than a TES4-family one."""
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == TES3_MAGIC
    except OSError:
        return False


def parse_subrecords(data: bytes) -> list:
    """Every subrecord in one record's data block."""
    subs = []
    pos = 0
    length = len(data)
    while pos + SUBRECORD_HEADER_SIZE <= length:
        sig = data[pos:pos + 4].decode("ascii", errors="replace")
        size = struct.unpack_from("<I", data, pos + 4)[0]
        pos += SUBRECORD_HEADER_SIZE
        if pos + size > length:
            break
        subs.append(Subrecord(type=sig, data=data[pos:pos + size]))
        pos += size
    return subs


def get_string(sub: Subrecord) -> str:
    """A TES3 string subrecord as text, decoded cp1252.

    See: docs/commentary/tes4_export_morrowind.md#the-tes3-container
    """
    if sub is None:
        return ""
    return sub.data.split(b"\x00", 1)[0].decode("cp1252", errors="replace")


def get_subrecord(rec: Tes3Record, sig: str) -> Subrecord:
    """The first subrecord with this signature, or None."""
    for sub in rec.subrecords:
        if sub.type == sig:
            return sub
    return None


def get_all_subrecords(rec: Tes3Record, sig: str) -> list:
    """Every subrecord with this signature, in file order."""
    return [sub for sub in rec.subrecords if sub.type == sig]


def read_file(filepath: str) -> tuple:
    """Read a Morrowind ESM/ESP; return (header_record, records).

    Records keep file order, which is the only ordering TES3 has.
    """
    with open(filepath, "rb") as fh:
        mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            return _parse_file(mm)
        finally:
            mm.close()


def _parse_file(mm) -> tuple:
    """Walk the flat record stream to EOF."""
    size = len(mm)
    pos = 0
    header = None
    records = []
    while pos + RECORD_HEADER_SIZE <= size:
        rec = _read_record(mm, pos, size)
        if rec is None:
            break
        data_size = struct.unpack_from("<I", mm, pos + 4)[0]
        if rec.type == "TES3":
            header = rec
        else:
            records.append(rec)
        pos += RECORD_HEADER_SIZE + data_size
    return header, records


def _read_record(mm, pos: int, file_size: int) -> Tes3Record:
    """One record and its subrecords, or None past the end."""
    if pos + RECORD_HEADER_SIZE > file_size:
        return None
    sig = mm[pos:pos + 4].decode("ascii", errors="replace")
    data_size = struct.unpack_from("<I", mm, pos + 4)[0]
    flags = struct.unpack_from("<I", mm, pos + 12)[0]

    data_end = pos + RECORD_HEADER_SIZE + data_size
    if data_end > file_size:
        return None

    rec = Tes3Record(type=sig, flags=flags, offset=pos)
    rec.subrecords = parse_subrecords(bytes(mm[pos + RECORD_HEADER_SIZE:data_end]))
    rec.deleted = any(s.type == "DELE" for s in rec.subrecords)
    name = get_subrecord(rec, "NAME")
    if name is not None:
        rec.record_id = get_string(name)
    elif sig == "SCPT":
        rec.record_id = script_name(rec)
    return rec


def script_name(rec: Tes3Record) -> str:
    """A script's id: the 32-byte name at the head of SCHD.

    See: docs/commentary/tes4_export_morrowind.md#scripts
    """
    schd = get_subrecord(rec, "SCHD")
    if schd is None:
        return ""
    return schd.data[:32].split(b"\x00", 1)[0].decode("cp1252", errors="replace")


def read_masters(filepath: str) -> list:
    """The master filenames this plugin declares, in load order."""
    header, _ = _read_header_only(filepath)
    if header is None:
        return []
    return [get_string(s) for s in get_all_subrecords(header, "MAST")]


def file_type(header: Tes3Record) -> int:
    """The TES3 HEDR file type: 0 = esp, 1 = esm, 32 = ess."""
    hedr = get_subrecord(header, "HEDR")
    return struct.unpack_from("<i", hedr.data, 4)[0] if hedr else 0


def _read_header_only(filepath: str) -> tuple:
    """(header record, its data size) without walking the whole file."""
    with open(filepath, "rb") as fh:
        head = fh.read(RECORD_HEADER_SIZE)
        if len(head) < RECORD_HEADER_SIZE or head[:4] != TES3_MAGIC:
            return None, 0
        data_size = struct.unpack_from("<I", head, 4)[0]
        flags = struct.unpack_from("<I", head, 12)[0]
        rec = Tes3Record(type="TES3", flags=flags, offset=0)
        rec.subrecords = parse_subrecords(fh.read(data_size))
    return rec, data_size
