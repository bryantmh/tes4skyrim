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

from core.tes4_encoding import ENCODING_AUTO, choice, decode, normalize, pin

#: TES4 record header; FO3/FNV use 24, resolved per file by detect_header_size.
RECORD_HEADER_SIZE = 20
#: GRUP header size; 24 for FO3/FNV, resolved per file alongside the record header.
GROUP_HEADER_SIZE = 20
SUBRECORD_HEADER_SIZE = 6
FLAG_COMPRESSED = 0x00040000

#: HEDR sits right after the record header, so probing both offsets identifies the layout.
_HEADER_SIZES = (20, 24)


def detect_header_size(mm) -> int:
    """The record header size for this file: 20 for TES4, 24 for FO3/FNV."""
    for size in _HEADER_SIZES:
        if mm[size:size + 4] == b"HEDR":
            return size
    return RECORD_HEADER_SIZE


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
    is_vwd: bool = False  # True if in VWD group (group type 10)
    # Byte offset of the record header in the source file. Lets worker
    # processes re-read a record from their own mmap of the file instead of
    # having the whole Record pickled across the process boundary.
    offset: int = -1


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


def read_group_records(mm, size: int, hdr_size: int, label: bytes) -> list:
    """Every fully-parsed record inside one top-level GRUP, or [] if absent.

    Reads a single group without parsing the whole file, for callers that need
    one type's whole-file view (FO3/FNV LTEX resolves its texture through the
    TXST group).
    """
    pos = hdr_size + struct.unpack_from("<I", mm, 4)[0]
    out = []
    while pos + hdr_size <= size:
        grup_size = struct.unpack_from("<I", mm, pos + 4)[0]
        if grup_size <= 0:
            break
        if mm[pos:pos + 4] == b"GRUP" and mm[pos + 8:pos + 12] == label:
            inner, end = pos + hdr_size, pos + grup_size
            while inner + hdr_size <= end:
                rec = _read_record(mm, inner, size, hdr_size=hdr_size)
                out.append(rec)
                inner += hdr_size + rec.data_size
            break
        pos += grup_size
    return out


def read_file(filepath: str, parse_subs: bool = True) -> tuple:
    """
    Read a TES4 ESM/ESP file and return (header_record, records_by_group).

    parse_subs=False skips subrecord parsing (and decompression) for all
    records except the TES4 header — each Record then only carries its header
    fields, hierarchy info and byte offset. Use this when the subrecord data
    will be re-read elsewhere (e.g. by export worker processes).

    Returns:
        header: Record (the TES4 file header)
        groups: dict mapping top-group signature -> list of Record
        group_tree: list of (group_type, label, records) for hierarchical data
    """
    with open(filepath, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            header, all_records = _parse_file(mm, parse_subs)
        finally:
            mm.close()
    return header, all_records


def _parse_file(mm, parse_subs: bool = True) -> tuple:
    """Parse all records from a memory-mapped file."""
    file_size = len(mm)
    pos = 0
    hdr_size = detect_header_size(mm)

    header = _read_record(mm, pos, file_size, hdr_size=hdr_size)
    pos += hdr_size + header.data_size
    all_records = []

    # Read top-level groups
    while pos < file_size:
        if pos + 4 > file_size:
            break
        sig = mm[pos:pos + 4]
        if sig != b"GRUP":
            break  # Unexpected non-group at top level

        if pos + hdr_size > file_size:
            break

        group_size = struct.unpack_from("<I", mm, pos + 4)[0]
        group_end = pos + group_size

        # Parse this top-level group (and any nested groups within)
        mm[pos + 8:pos + 12].decode("ascii", errors="replace")
        struct.unpack_from("<I", mm, pos + 12)[0]

        _parse_group(mm, pos, group_end, file_size, all_records, 0, 0, 0,
                     parse_subs=parse_subs, hdr_size=hdr_size)
        pos = group_end

    return header, all_records


def _parse_group(mm, start: int, end: int, file_size: int,
                 records: list, current_wrld: int, current_cell: int,
                 current_dial: int, is_vwd: bool = False,
                 parse_subs: bool = True, hdr_size: int = RECORD_HEADER_SIZE):
    """Recursively parse records and sub-groups within a GRUP."""
    pos = start + hdr_size
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
        is_vwd = (group_type == 10)  # VWD if group type is 10
    elif group_type == 7:  # Topic children
        current_dial = struct.unpack_from("<I", label_bytes, 0)[0]

    while pos < end and pos < file_size:
        if pos + 4 > file_size:
            break

        sig = mm[pos:pos + 4]
        if sig == b"GRUP":
            if pos + hdr_size > file_size:
                break
            sub_size = struct.unpack_from("<I", mm, pos + 4)[0]
            sub_end = pos + sub_size
            _parse_group(mm, pos, sub_end, file_size, records,
                         current_wrld, current_cell, current_dial, is_vwd,
                         parse_subs=parse_subs, hdr_size=hdr_size)
            pos = sub_end
            continue
        rec = _read_record(mm, pos, file_size, parse_subs, hdr_size=hdr_size)
        if rec is None:
            break
        rec.parent_wrld = current_wrld
        rec.parent_cell = current_cell
        rec.parent_dial = current_dial
        rec.is_vwd = is_vwd
        current_wrld, current_cell, current_dial = _advance_hierarchy(
            rec, current_wrld, current_cell, current_dial)
        records.append(rec)
        pos += hdr_size + rec.data_size


def _advance_hierarchy(rec, wrld: int, cell: int, dial: int) -> tuple:
    """The (wrld, cell, dial) context this record establishes for its children."""
    if rec.type == "CELL":
        return wrld, rec.form_id, dial
    if rec.type == "WRLD":
        return rec.form_id, cell, dial
    if rec.type == "DIAL":
        return wrld, cell, rec.form_id
    return wrld, cell, dial


def _read_record(mm, pos: int, file_size: int, parse_subs: bool = True,
                 hdr_size: int = RECORD_HEADER_SIZE) -> Record:
    """Read a single record (header + subrecords) from the memory-mapped file."""
    if pos + hdr_size > file_size:
        return None

    sig = mm[pos:pos + 4].decode("ascii", errors="replace")
    data_size = struct.unpack_from("<I", mm, pos + 4)[0]
    flags = struct.unpack_from("<I", mm, pos + 8)[0]
    form_id = struct.unpack_from("<I", mm, pos + 12)[0]

    rec = Record(type=sig, data_size=data_size, flags=flags, form_id=form_id,
                 offset=pos)

    if not parse_subs:
        return rec

    data_start = pos + hdr_size
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
    """Extract a null-terminated string from a subrecord.

    Decoded in the run's TES4 codec (see core.tes4_encoding): cp1252 for
    Western installs, cp1251 for the Russian one. Decoding as UTF-8 turns
    every high byte into U+FFFD, destroying the text beyond recovery -- the
    export text is the only input the import stage gets.
    """
    if sub is None:
        return ""
    return decode(sub.data.rstrip(b"\x00"))


#: Subrecords whose payloads reveal the install language.
_TEXT_SUBS = frozenset(("FULL", "DESC"))

#: Bytes undefined in cp1252 but Cyrillic capitals in cp1251: never Western.
_SIGNAL_BYTES = frozenset((0x81, 0x8D, 0x8F, 0x90, 0x9D))

#: Detection sampling: stride to ~3000 probes, at most 4000 records or 64 KB.
_PROBE_DIVISOR = 3000
_PROBE_CAP = 4000
_SIZE_CAP = 65536

#: A verdict needs this much text, at this high-byte density, for cp1251.
_FIELDS_MIN = 8
_BYTES_MIN = 64
_DENSITY_MIN = 0.10


def decide_codec(payloads) -> str:
    """'cp1251' when sampled string payloads read as Russian, else 'cp1252'.

    Any signal byte (unrepresentable in cp1252) decides at once; otherwise a
    high-byte density >= 10% over >= 8 fields and >= 64 bytes decides.
    Shared by the TES4 and TES3 detectors so the rule cannot drift between
    them. Measured on TES4 FULL/DESC: EN Oblivion.esm 0.03%, RU 73.23%.
    """
    high = total = fields = 0
    size = 0
    for payload in payloads:
        for byte in payload:
            if byte in _SIGNAL_BYTES:
                return "cp1251"
            if byte >= 0x80:
                high += 1
            total += 1
        fields += 1
        size += len(payload)
        if fields >= _FIELDS_MIN and size >= _BYTES_MIN \
                and high / total >= _DENSITY_MIN:
            return "cp1251"
        if size >= _SIZE_CAP:
            break
    return "cp1252"


def _text_payloads(source_path: str, records: list):
    """FULL/DESC payloads spread across `source_path`.

    Walks the binary directly instead of parsing every record: stride-samples
    up to 4000 records (placement records carrying no FULL outnumber text
    records a hundred to one), skipping compressed records. Capped at 64 KB
    of text. Yields bytes.
    """
    taken = 0
    size = 0
    step = max(1, len(records) // _PROBE_DIVISOR)
    with open(source_path, "rb") as fh:
        mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            hdr_size = detect_header_size(mm)
            for rec in records[::step]:
                if taken >= _PROBE_CAP or size >= _SIZE_CAP:
                    break
                taken += 1
                if rec.offset < 0:
                    continue
                if rec.flags & FLAG_COMPRESSED or rec.data_size <= 0:
                    continue
                start = rec.offset + hdr_size
                chunk = mm[start:start + rec.data_size]
                for sub in parse_subrecords(bytes(chunk)):
                    if sub.type in _TEXT_SUBS and sub.data:
                        size += len(sub.data)
                        yield sub.data
        finally:
            mm.close()


def detect_codec(source_path: str, records: list) -> str:
    """'cp1251' when the plugin binary reads as Russian, else 'cp1252'.

    Any signal byte (unrepresentable in cp1252) decides at once; otherwise a
    high-byte density >= 10% over >= 8 fields and >= 64 bytes decides, sampled
    across the whole file. Measured on FULL/DESC: EN Oblivion.esm 0.03%, RU
    73.23% (12,668 fields).

    See: docs/reference/pipeline.md#tes4-text-encoding
    """
    return decide_codec(_text_payloads(source_path, records))


def configure_for_source(source_path: str, records: list) -> str:
    """Pin the run's TES4 codec for this plugin binary and return it.

    An explicit ``TESCONV_TES4_ENCODING`` (CLI flag, GUI setting, config key)
    wins; ``auto`` scans the binary. Pinning to the env var carries the
    decision to the export worker processes.
    """
    raw = choice()
    codec = detect_codec(source_path, records) if raw == ENCODING_AUTO else raw
    codec = pin(normalize(codec))
    print(f"  Text encoding: {codec}")
    return codec


def get_formid_str(form_id: int) -> str:
    """Format a FormID as 8-digit hex."""
    return f"{form_id:08X}"
