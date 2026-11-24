"""Reading one setting out of Vortex's own state, read-only, while Vortex may be running.

Vortex keeps its settings in a LevelDB database (`%APPDATA%/Vortex/state.v2`): sorted table files
(`.ldb`, blocks usually Snappy-compressed) plus a write-ahead log (`.log`, locked while Vortex
runs). Keys are Vortex's state paths joined by `###`; values are JSON. The newest write of a key
(highest sequence number) wins. Nothing here ever writes to that folder.
See: docs/commentary/core_gui.md#vortex-staging
"""

import json
import os
import struct
from pathlib import Path

#: Vortex's state database.
STATE_DIR = Path(os.environ.get("APPDATA", "")) / "Vortex" / "state.v2"
#: The key holding a game's staging folder.
STAGING_KEY = "settings###mods###installPath###{game}"
#: A LevelDB table's footer: two block handles, padding, and the magic number.
FOOTER_SIZE = 48
#: Value-type tag of a live (not deleted) entry in an internal key.
TYPE_VALUE = 1

# ---------------------------------------------------------------------------
#  Decoding
# ---------------------------------------------------------------------------


def varint(buf: bytes, pos: int) -> tuple:
    """(value, next position) of the little-endian base-128 integer at `pos`."""
    out = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        out |= (b & 0x7F) << shift
        if b < 0x80:
            return out, pos
        shift += 7


def snappy(data: bytes) -> bytes:
    """Decompress one raw Snappy block."""
    size, pos = varint(data, 0)
    out = bytearray()
    while pos < len(data):
        tag = data[pos]
        pos += 1
        kind = tag & 3
        if kind == 0:
            n = tag >> 2
            if n >= 60:
                extra = n - 59
                n = int.from_bytes(data[pos:pos + extra], "little")
                pos += extra
            out += data[pos:pos + n + 1]
            pos += n + 1
            continue
        if kind == 1:
            length, offset = ((tag >> 2) & 7) + 4, ((tag >> 5) << 8) | data[pos]
            pos += 1
        else:
            width = 2 if kind == 2 else 4
            length, offset = (tag >> 2) + 1, int.from_bytes(data[pos:pos + width], "little")
            pos += width
        for _ in range(length):
            out.append(out[-offset])
    return bytes(out[:size])


def block_entries(block: bytes) -> list:
    """(key, value) for every entry in one table block (prefix-compressed keys)."""
    restarts = struct.unpack_from("<I", block, len(block) - 4)[0]
    end = len(block) - 4 - 4 * restarts
    out, key, pos = [], b"", 0
    while pos < end:
        shared, pos = varint(block, pos)
        unshared, pos = varint(block, pos)
        size, pos = varint(block, pos)
        key = key[:shared] + block[pos:pos + unshared]
        pos += unshared
        out.append((key, block[pos:pos + size]))
        pos += size
    return out


def read_block(data: bytes, offset: int, size: int) -> bytes:
    """One table block, decompressed (its trailer is a type byte and a checksum)."""
    raw = data[offset:offset + size]
    return snappy(raw) if data[offset + size] == 1 else raw


def table_entries(path, want: bytes) -> list:
    """(user key, sequence, live, value) for the entries of one `.ldb` table in blocks that can hold `want`.

    An index entry's key is at or past every key in its block and before every key in the next, so
    only blocks with the previous index key <= `want` <= their own are decompressed.
    """
    data = Path(path).read_bytes()
    if len(data) < FOOTER_SIZE:
        return []
    footer = data[-FOOTER_SIZE:]
    _off, pos = varint(footer, 0)
    _size, pos = varint(footer, pos)
    index_off, pos = varint(footer, pos)
    index_size, _pos = varint(footer, pos)
    out, previous = [], b""
    for key, handle in block_entries(read_block(data, index_off, index_size)):
        if previous > want:
            break
        previous = key[:-8]
        if previous < want:
            continue
        off, p = varint(handle, 0)
        size, _p = varint(handle, p)
        for ikey, value in block_entries(read_block(data, off, size)):
            tag = int.from_bytes(ikey[-8:], "little")
            out.append((ikey[:-8], tag >> 8, (tag & 0xFF) == TYPE_VALUE, value))
    return out


def log_entries(path) -> list:
    """(user key, sequence, live, value) for every write in a `.log`; [] when it is locked."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return []
    records, chunk, pos = [], b"", 0
    while pos + 7 <= len(data):
        length, kind = struct.unpack_from("<H", data, pos + 4)[0], data[pos + 6]
        if length == 0 and kind == 0:
            pos = (pos // 32768 + 1) * 32768
            continue
        chunk += data[pos + 7:pos + 7 + length]
        pos += 7 + length
        if kind in (1, 4):
            records.append(chunk)
            chunk = b""
        elif kind == 2:
            chunk = data[pos - length:pos]
    return [entry for rec in records for entry in _batch(rec)]


def _batch(rec: bytes) -> list:
    """The writes in one log record (a write batch)."""
    seq, count = struct.unpack_from("<QI", rec, 0)
    out, pos = [], 12
    for i in range(count):
        kind = rec[pos]
        klen, pos = varint(rec, pos + 1)
        key = rec[pos:pos + klen]
        pos += klen
        value = b""
        if kind == TYPE_VALUE:
            vlen, pos = varint(rec, pos)
            value = rec[pos:pos + vlen]
            pos += vlen
        out.append((key, seq + i, kind == TYPE_VALUE, value))
    return out


# ---------------------------------------------------------------------------
#  Settings
# ---------------------------------------------------------------------------

def read_setting(key: str, state_dir=STATE_DIR):
    """The JSON value of one Vortex state key, or None when absent or unreadable."""
    want = key.encode("utf-8")
    best = None
    folder = Path(state_dir)
    if not folder.is_dir():
        return None
    for path in sorted(folder.glob("*.ldb")) + sorted(folder.glob("*.log")):
        try:
            entries = table_entries(path, want) if path.suffix == ".ldb" else log_entries(path)
        except (OSError, IndexError, struct.error, ValueError):
            continue
        for user_key, seq, live, value in entries:
            if user_key == want and (best is None or seq > best[0]):
                best = (seq, live, value)
    if best is None or not best[1]:
        return None
    try:
        return json.loads(best[2].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def staging_folder(game: str, state_dir=STATE_DIR) -> str:
    """Vortex's staging folder for `game` with its placeholders filled in, or "" when unknown."""
    value = read_setting(STAGING_KEY.format(game=game), state_dir)
    if not isinstance(value, str) or not value:
        return ""
    userdata = str(Path(state_dir).parent)
    return os.path.normpath(value.replace("{game}", game).replace("{GAME}", game)
                            .replace("{USERDATA}", userdata).replace("{userdata}", userdata))
