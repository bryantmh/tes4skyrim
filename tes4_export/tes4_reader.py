"""
TES4 (Oblivion) binary file reader.

Parses ESM/ESP files into an in-memory record structure.
TES4 record header: 20 bytes (type[4] + dataSize[4] + flags[4] + formID[4] + vc[4])
TES4 group header:  20 bytes ("GRUP"[4] + groupSize[4] + label[4] + groupType[4] + stamp[4])
Subrecord header:    6 bytes (type[4] + dataSize[2])
"""

import mmap
import struct
import zlib
from dataclasses import dataclass, field

RECORD_HEADER_SIZE = 20
GROUP_HEADER_SIZE = 20
SUBRECORD_HEADER_SIZE = 6
FLAG_COMPRESSED = 0x00040000


@dataclass
class Subrecord:
    """A single subrecord within a record."""
    type: str
    data: bytes


@dataclass
class Record:
    """A parsed TES4 record with all subrecords."""
    type: str
    data_size: int
    flags: int
    form_id: int
    subrecords: list = field(default_factory=list)
    # Hierarchy info set during parsing
    parent_cell: int = 0
    parent_wrld: int = 0
    parent_dial: int = 0


@dataclass
class GroupInfo:
    """Metadata about a GRUP."""
    group_type: int
    label: bytes
    label_int: int
    size: int
    offset: int


def parse_subrecords(data: bytes) -> list:
    """Parse subrecords from raw record data."""
    subs = []
    pos = 0
    length = len(data)
    while pos + SUBRECORD_HEADER_SIZE <= length:
        sig = data[pos:pos + 4].decode("ascii", errors="replace")
        size = struct.unpack_from("<H", data, pos + 4)[0]
        pos += SUBRECORD_HEADER_SIZE
        if pos + size > length:
            break
        subs.append(Subrecord(type=sig, data=data[pos:pos + size]))
        pos += size
    return subs


def read_file(filepath: str) -> tuple:
    """
    Read a TES4 ESM/ESP file and return (header_record, records_by_group).

    Returns:
        header: Record (the TES4 file header)
        groups: dict mapping top-group signature -> list of Record
        group_tree: list of (group_type, label, records) for hierarchical data
    """
    with open(filepath, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            header, all_records = _parse_file(mm)
        finally:
            mm.close()
    return header, all_records


def _parse_file(mm) -> tuple:
    """Parse all records from a memory-mapped file."""
    file_size = len(mm)
    pos = 0

    # Read TES4 header record first
    header = _read_record(mm, pos, file_size)
    pos += RECORD_HEADER_SIZE + header.data_size
    all_records = []

    # Read top-level groups
    while pos < file_size:
        if pos + 4 > file_size:
            break
        sig = mm[pos:pos + 4]
        if sig != b"GRUP":
            break  # Unexpected non-group at top level

        if pos + GROUP_HEADER_SIZE > file_size:
            break

        group_size = struct.unpack_from("<I", mm, pos + 4)[0]
        group_end = pos + group_size

        # Parse this top-level group (and any nested groups within)
        mm[pos + 8:pos + 12].decode("ascii", errors="replace")
        struct.unpack_from("<I", mm, pos + 12)[0]

        _parse_group(mm, pos, group_end, file_size, all_records, 0, 0, 0)
        pos = group_end

    return header, all_records


def _parse_group(mm, start: int, end: int, file_size: int,
                 records: list, current_wrld: int, current_cell: int,
                 current_dial: int):
    """Recursively parse records and sub-groups within a GRUP."""
    pos = start + GROUP_HEADER_SIZE
    group_type = struct.unpack_from("<I", mm, start + 12)[0]
    label_bytes = mm[start + 8:start + 12]

    # Track hierarchy based on group type
    if group_type == 1:  # World children
        current_wrld = struct.unpack_from("<I", label_bytes, 0)[0]
    elif group_type in (2, 3):  # Interior cell block/sub-block
        pass
    elif group_type in (4, 5):  # Exterior cell block/sub-block
        pass
    elif group_type in (6, 8, 9, 10):  # Cell children / persistent / temporary / VWD
        current_cell = struct.unpack_from("<I", label_bytes, 0)[0]
    elif group_type == 7:  # Topic children
        current_dial = struct.unpack_from("<I", label_bytes, 0)[0]

    while pos < end and pos < file_size:
        if pos + 4 > file_size:
            break

        sig = mm[pos:pos + 4]
        if sig == b"GRUP":
            if pos + GROUP_HEADER_SIZE > file_size:
                break
            sub_size = struct.unpack_from("<I", mm, pos + 4)[0]
            sub_end = pos + sub_size
            _parse_group(mm, pos, sub_end, file_size, records,
                         current_wrld, current_cell, current_dial)
            pos = sub_end
        else:
            rec = _read_record(mm, pos, file_size)
            if rec is None:
                break

            # Set hierarchy info
            rec.parent_wrld = current_wrld
            rec.parent_cell = current_cell
            rec.parent_dial = current_dial

            # If this is a CELL, update current_cell for children
            if rec.type == "CELL":
                current_cell = rec.form_id
            elif rec.type == "WRLD":
                current_wrld = rec.form_id
            elif rec.type == "DIAL":
                current_dial = rec.form_id

            records.append(rec)
            pos += RECORD_HEADER_SIZE + rec.data_size


def _read_record(mm, pos: int, file_size: int) -> Record:
    """Read a single record (header + subrecords) from the memory-mapped file."""
    if pos + RECORD_HEADER_SIZE > file_size:
        return None

    sig = mm[pos:pos + 4].decode("ascii", errors="replace")
    data_size = struct.unpack_from("<I", mm, pos + 4)[0]
    flags = struct.unpack_from("<I", mm, pos + 8)[0]
    form_id = struct.unpack_from("<I", mm, pos + 12)[0]

    rec = Record(type=sig, data_size=data_size, flags=flags, form_id=form_id)

    data_start = pos + RECORD_HEADER_SIZE
    data_end = data_start + data_size
    if data_end > file_size:
        return rec  # Return with no subrecords

    raw_data = mm[data_start:data_end]

    # Handle compressed records
    if flags & FLAG_COMPRESSED and len(raw_data) >= 4:
        try:
            raw_data = zlib.decompress(raw_data[4:])
        except zlib.error:
            return rec  # Return with no subrecords on decompression failure

    rec.subrecords = parse_subrecords(bytes(raw_data))
    return rec


def get_subrecord(rec: Record, sig: str) -> Subrecord:
    """Get the first subrecord matching a signature, or None."""
    for sub in rec.subrecords:
        if sub.type == sig:
            return sub
    return None


def get_all_subrecords(rec: Record, sig: str) -> list:
    """Get all subrecords matching a signature."""
    return [sub for sub in rec.subrecords if sub.type == sig]


def get_string(sub: Subrecord) -> str:
    """Extract a null-terminated string from a subrecord."""
    if sub is None:
        return ""
    return sub.data.rstrip(b"\x00").decode("utf-8", errors="replace")


def get_formid_str(form_id: int) -> str:
    """Format a FormID as 8-digit hex."""
    return f"{form_id:08X}"
