"""
TES5 (Skyrim SE) binary file writer.

Writes ESM/ESP files with proper record headers, group hierarchy,
and subrecord structures for Skyrim Special Edition.

TES5 record header: 24 bytes
  sig[4] + dataSize[4] + flags[4] + formID[4] + vcs1[4] + formVersion[2] + vcs2[2]
GRUP header: 24 bytes
  'GRUP'[4] + groupSize[4] + label[4] + groupType[4] + stamp[4] + unknown[4]
Subrecord header: 6 bytes
  sig[4] + dataSize[2]
"""

import struct
import threading

RECORD_HEADER_SIZE = 24
GROUP_HEADER_SIZE = 24
SUBRECORD_HEADER_SIZE = 6
FORM_VERSION_SSE = 44
HEDR_VERSION_SSE = 1.7100000381469727  # float32 representation of 1.71


def pack_subrecord(sig: str, data: bytes) -> bytes:
    """Pack a single subrecord: header + data."""
    sig_bytes = sig.encode('ascii')[:4].ljust(4, b'\x00')
    if len(data) > 65535:
        # Use XXXX protocol for oversized subrecords
        xxxx = b'XXXX' + struct.pack('<H', 4) + struct.pack('<I', len(data))
        return xxxx + sig_bytes + struct.pack('<H', 0) + data
    return sig_bytes + struct.pack('<H', len(data)) + data


def pack_string_subrecord(sig: str, value: str) -> bytes:
    """Pack a null-terminated string subrecord."""
    data = value.encode('utf-8') + b'\x00'
    return pack_subrecord(sig, data)


def pack_record(sig: str, form_id: int, flags: int, subrecords: bytes,
                form_version: int = FORM_VERSION_SSE) -> bytes:
    """Pack a complete record: header + subrecord data."""
    # Clear compressed flag (0x00040000) since we write uncompressed data
    flags = flags & ~0x00040000
    sig_bytes = sig.encode('ascii')[:4].ljust(4, b'\x00')
    header = struct.pack('<4sIIIIHH',
                         sig_bytes,
                         len(subrecords),  # dataSize
                         flags,
                         form_id,
                         0,  # vcs1
                         form_version,
                         0)  # vcs2
    return header + subrecords


def pack_group(group_type: int, label: bytes, contents: bytes) -> bytes:
    """Pack a GRUP with its contents. label is 4 raw bytes."""
    total_size = GROUP_HEADER_SIZE + len(contents)
    header = struct.pack('<4sI4siII',
                         b'GRUP',
                         total_size,
                         label,
                         group_type,
                         0,  # stamp
                         0)  # unknown
    return header + contents


def pack_top_group(sig: str, contents: bytes) -> bytes:
    """Pack a type-0 (top-level) group for a record signature."""
    label = sig.encode('ascii')[:4].ljust(4, b'\x00')
    return pack_group(0, label, contents)


def _count_records_and_groups(blob: bytes) -> int:
    """Count every record and GRUP in a serialized group blob (recursively).

    Used to compute the TES4 header's HEDR record count from the actual written
    bytes. A GRUP header is 24 bytes with its total size at offset 4 (covering
    the header); a record header is 24 bytes with its DATA size at offset 4
    (NOT covering the header). Both GRUPs and records are counted, matching
    vanilla Skyrim.esm (HEDR ≈ records + groups).
    """
    count = 0
    pos = 0
    n = len(blob)
    while pos + 24 <= n:
        tag = blob[pos:pos + 4]
        size = struct.unpack_from('<I', blob, pos + 4)[0]
        count += 1
        if tag == b'GRUP':
            # Recurse into the group's children (size includes the 24B header).
            count += _count_records_and_groups(blob[pos + 24:pos + size])
            pos += size
        else:
            pos += 24 + size
    return count


def pack_tes4_header(masters: list, num_records: int = 0,
                     next_object_id: int = 0x800,
                     author: str = "TES4-to-TES5 Converter",
                     description: str = "",
                     is_esm: bool = True) -> bytes:
    """Pack the TES4 file header record."""
    subs = b''

    # HEDR
    hedr_data = struct.pack('<fII', HEDR_VERSION_SSE, num_records, next_object_id)
    subs += pack_subrecord('HEDR', hedr_data)

    # CNAM (author)
    if author:
        subs += pack_string_subrecord('CNAM', author)

    # SNAM (description)
    if description:
        subs += pack_string_subrecord('SNAM', description)

    # MAST + DATA pairs
    for master in masters:
        subs += pack_string_subrecord('MAST', master)
        subs += pack_subrecord('DATA', b'\x00' * 8)

    flags = 0x01 if is_esm else 0x00  # ESM flag
    return pack_record('TES4', 0, flags, subs, FORM_VERSION_SSE)


def pack_obnd(x1: int = 0, y1: int = 0, z1: int = 0,
              x2: int = 0, y2: int = 0, z2: int = 0) -> bytes:
    """Pack OBND (Object Bounds) subrecord — required on most TES5 records."""
    data = struct.pack('<6h', x1, y1, z1, x2, y2, z2)
    return pack_subrecord('OBND', data)


def pack_formid_subrecord(sig: str, form_id: int) -> bytes:
    """Pack a FormID subrecord."""
    return pack_subrecord(sig, struct.pack('<I', form_id))


def pack_float_subrecord(sig: str, value: float) -> bytes:
    """Pack a float32 subrecord."""
    return pack_subrecord(sig, struct.pack('<f', value))


def pack_uint32_subrecord(sig: str, value: int) -> bytes:
    """Pack a uint32 subrecord."""
    return pack_subrecord(sig, struct.pack('<I', value))


def pack_uint16_subrecord(sig: str, value: int) -> bytes:
    """Pack a uint16 subrecord."""
    return pack_subrecord(sig, struct.pack('<H', value))


def pack_uint8_subrecord(sig: str, value: int) -> bytes:
    """Pack a uint8 subrecord."""
    return pack_subrecord(sig, struct.pack('<B', value))


class PluginWriter:
    """High-level writer for building a TES5 plugin file.

    Usage:
        w = PluginWriter(masters=['Skyrim.esm'], is_esm=True)
        w.add_record('STAT', formid, flags, subrecords_bytes)
        w.write('output.esm')
    """

    def __init__(self, masters: list = None, is_esm: bool = True,
                 author: str = "TES4-to-TES5 Converter",
                 description: str = ""):
        self.masters = masters or []
        self.is_esm = is_esm
        self.author = author
        self.description = description
        # Groups: sig -> list of (record_bytes)
        # For CELL/WRLD/DIAL, we store pre-built group bytes
        self._top_groups = {}
        self._record_count = 0
        self._next_object_id = 0x800
        self._lock = threading.Lock()  # guards alloc_formid and add_record

    @property
    def next_object_id(self):
        return self._next_object_id

    @next_object_id.setter
    def next_object_id(self, val):
        self._next_object_id = val

    def alloc_formid(self) -> int:
        """Allocate a new FormID for generated records (ARMA, TXST, etc.). Thread-safe."""
        with self._lock:
            fid = self._next_object_id
            self._next_object_id += 1
        return fid

    def add_record(self, group_sig: str, record_bytes: bytes):
        """Add a packed record to a top-level group. Thread-safe."""
        with self._lock:
            if group_sig not in self._top_groups:
                self._top_groups[group_sig] = []
            self._top_groups[group_sig].append(record_bytes)
            self._record_count += 1

    def add_raw_group(self, group_sig: str, group_bytes: bytes):
        """Add pre-built group bytes (for CELL/WRLD/DIAL hierarchies)."""
        with self._lock:
            if group_sig not in self._top_groups:
                self._top_groups[group_sig] = []
            self._top_groups[group_sig].append(group_bytes)

    def write(self, filepath: str):
        """Write the complete plugin file using an atomic temp-then-rename approach.

        Writes to a .tmp sibling first so that a locked output file (e.g. held
        open by xEdit or Skyrim) gives a clear error rather than corrupting an
        existing plugin.  On success, the .tmp is renamed to the final path.
        """
        import os
        tmp_path = filepath + '.tmp'

        # Assemble the top-level group bodies first so the header's HEDR record
        # count can be computed from the ACTUAL written content. The count must
        # include records nested in CELL/WRLD/DIAL hierarchies (added via
        # add_raw_group, which never touched _record_count) plus every GRUP —
        # vanilla Skyrim.esm's HEDR ≈ records + groups. A count that omits the
        # hierarchies (our old 38,585 vs 1.17M real) leaves the header wildly
        # under-reporting, which the engine's loader does not tolerate cleanly.
        group_blobs = []
        for sig in self._group_order():
            if sig not in self._top_groups:
                continue
            contents = b''.join(self._top_groups[sig])
            if contents:
                group_blobs.append(pack_top_group(sig, contents))

        total_count = sum(_count_records_and_groups(b) for b in group_blobs)

        try:
            with open(tmp_path, 'wb') as f:
                # TES4 header
                header = pack_tes4_header(
                    self.masters,
                    num_records=total_count,
                    next_object_id=self._next_object_id,
                    author=self.author,
                    description=self.description,
                    is_esm=self.is_esm,
                )
                f.write(header)

                for blob in group_blobs:
                    f.write(blob)
        except Exception:
            # Clean up partial temp file so it doesn't litter the output dir
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

        # Atomic rename: replaces the target even if it already exists
        try:
            os.replace(tmp_path, filepath)
        except PermissionError as e:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise PermissionError(
                f"Cannot write to '{filepath}': file is locked (is it open in xEdit or Skyrim?). "
                f"Original error: {e}"
            ) from e

    def _group_order(self) -> list:
        """Canonical top-level group ordering for TES5.

        CRITICAL: QUST must come AFTER CELL, WRLD and DIAL (this is vanilla
        Skyrim.esm's order: …CELL, WRLD, DIAL, QUST, …). The engine/CK loads
        top-level groups in file order and resolves a quest's forced-reference
        aliases (ALFR) WHEN it loads the QUST group. If QUST precedes CELL/WRLD,
        the ACHR/REFR targets living in those cell groups are not in the form
        map yet, so every forced ref fails with "[QUESTS] Could not find forced
        ref (…)" and the alias fills NONE (no marker). A quest in a PLUGIN can
        still find a ref in a MASTER because masters load fully first — which is
        exactly why the same alias resolved in the test ESP but not in-file.
        DIAL is also placed before QUST to match vanilla.
        """
        order = [
            'GMST', 'KYWD', 'TXST', 'GLOB', 'CLAS', 'FACT', 'HDPT', 'EYES',
            'RACE', 'SOUN', 'SOPM', 'SNDR', 'MATT', 'STAT', 'ACTI', 'CONT', 'DOOR',
            'FLOR', 'FURN', 'GRAS', 'TREE', 'LIGH', 'MISC', 'KEYM', 'ARMO',
            'ARMA', 'BOOK', 'AMMO', 'ENCH', 'SPEL', 'ALCH', 'INGR', 'SCRL',
            'SLGM', 'VTYP', 'OTFT', 'NPC_', 'LVLN', 'LVLI', 'LVSP', 'WTHR',
            'CLMT', 'REGN', 'IDLE', 'PACK', 'EFSH', 'LSCR', 'ANIO',
            'WEAP', 'LCTN', 'NAVI',
            # References resolved by quests must exist before QUST loads:
            'CELL', 'WRLD',
            'SMBN', 'SMQN', 'SMEN',
            'DIAL', 'DLBR', 'DLVW',
            'QUST',
        ]
        # Append any groups not in the canonical order
        for sig in self._top_groups:
            if sig not in order:
                order.append(sig)
        return order
