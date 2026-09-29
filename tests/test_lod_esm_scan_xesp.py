"""esm_scan reads each REFR's XESP enable parent, normalized, with its flags.

Object-LOD selection walks enable-parent chains across plugins, so the parent
id must be in the same global index space as `form_id`.
"""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asset_convert.lod.esm_scan import global_file_index, parse_esm

MASTERS = ('Skyrim.esm', 'Oblivion.esm')


def _rec(sig: bytes, fid: int, body: bytes = b'', flags: int = 0) -> bytes:
    """One record: header plus `body`."""
    return sig + struct.pack('<IIIII', len(body), flags, fid, 0, 0) + body


def _grup(label: int, gtype: int, payload: bytes) -> bytes:
    """One GRUP around `payload`."""
    return (b'GRUP' + struct.pack('<I', 24 + len(payload))
            + struct.pack('<I', label) + struct.pack('<III', gtype, 0, 0)
            + payload)


def _sub(sig: bytes, payload: bytes) -> bytes:
    """One subrecord."""
    return sig + struct.pack('<H', len(payload)) + payload


def _plugin(tmp_path: Path, payload: bytes) -> Path:
    """A minimal plugin with a two-entry master list."""
    hdr = b''.join(_sub(b'MAST', m.encode() + b'\0') + _sub(b'DATA', bytes(8))
                   for m in MASTERS)
    p = tmp_path / 'ov.esp'
    p.write_bytes(_rec(b'TES4', 0, hdr) + payload)
    return p


def _refr(fid: int, xesp: bytes = b'') -> bytes:
    """A REFR placing base 0x01000100, with an optional raw XESP payload."""
    body = (_sub(b'NAME', struct.pack('<I', 0x01000100))
            + _sub(b'DATA', struct.pack('<6f', 1, 2, 3, 0, 0, 0)))
    if xesp:
        body += _sub(b'XESP', xesp)
    return _rec(b'REFR', fid, body)


def test_xesp_parent_and_opposite_flag_are_read(tmp_path):
    """Parent in a master, opposite bit set; a ref without XESP reads None."""
    payload = _grup(0x0100003C, 1, _grup(0x02000001, 6,
                    _refr(0x02000002, struct.pack('<II', 0x01005820, 1))
                    + _refr(0x02000003)))
    _w, _c, _s, refs = parse_esm(_plugin(tmp_path, payload))
    by_id = {r['form_id'] & 0xFFFFFF: r for r in refs}
    oblivion = global_file_index('oblivion.esm') << 24
    assert by_id[0x000002]['xesp'] == (oblivion | 0x005820, 1)
    assert by_id[0x000003]['xesp'] is None
