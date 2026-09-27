"""Converted escorts restart when the escorted target comes back in range.

Skyrim's Escort procedure never recovers once the escorted player strays far
enough, and EvaluatePackage does not restart a package that wins again (Jayred
Ice-Veins, SE02). The converter's TES4EscortWhenNear root runs Escort only while
GetWithinDistance(target, radius) holds and Waits otherwise, so the escort
starts fresh each time the target returns.
See: docs/commentary/tes5_import_package.md#escort-restarts-when-the-target-returns
"""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tes5_import.packages import escort_when_near as ewn
from tes5_import.packages.converter import PackContext, convert_PACK
from tes5_import.packages.templates import ESCORT


def _subs(record: bytes):
    """(signature, payload) pairs of a packed record."""
    body, out, off = record[24:], [], 0
    while off < len(body):
        size = struct.unpack_from('<H', body, off + 4)[0]
        out.append((body[off:off + 4], body[off + 6:off + 6 + size]))
        off += 6 + size
    return out


def _escort_rec():
    return {'Signature': 'PACK', 'FormID': '00000E9D', 'EditorID': 'X',
            'PKDT.Type': '2', 'PKDT.Flags': '0', 'PTDT.Type': '0',
            'PTDT.Target': '00000014', 'PLDT.Type': '3', 'PLDT.Radius': '512'}


def test_root_is_stacked_escort_gated_on_distance_then_wait():
    """Escort gated on GetWithinDistance(input 11, input 15), then an open-ended Wait."""
    subs = _subs(ewn.escort_root_record(0x01000800))
    names = [p.rstrip(b'\0') for s, p in subs if s == b'PNAM' and len(p) != 4]
    assert names == [b'Escort', b'Wait']
    ctda = next(p for s, p in subs if s == b'CTDA')
    flags, _comp, func, _pad, p1, p2 = struct.unpack_from('<B3xfHHII', ctda)
    assert (flags, func, p1, p2) == (0x08, 0x027F, 11, 15)
    fnams = [struct.unpack('<I', p)[0] for s, p in subs if s == b'FNAM']
    assert fnams == [1, 0]


def test_root_declares_the_instance_inputs():
    """The root declares 12 inputs, version 8, and XNAM 22."""
    subs = _subs(ewn.escort_root_record(0x01000800))
    pkcu = next(p for s, p in subs if s == b'PKCU')
    assert struct.unpack('<III', pkcu) == (12, 0, 8)
    assert next(p for s, p in subs if s == b'XNAM') == bytes([22])


def test_escort_points_at_the_installed_root():
    """A TES4 Escort instances the installed root with all 12 inputs."""
    ewn.set_escort_template_fid(0x01000800)
    try:
        pkcu = next(p for s, p in _subs(convert_PACK(_escort_rec(), PackContext()))
                    if s == b'PKCU')
        assert struct.unpack('<III', pkcu)[:2] == (12, 0x01000800)
    finally:
        ewn.set_escort_template_fid(0)


def test_no_installed_root_falls_back_to_vanilla_escort():
    ewn.set_escort_template_fid(0)
    assert ewn.escort_template() is ESCORT
