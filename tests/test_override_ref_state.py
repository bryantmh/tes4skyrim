"""A reference override carries the author's header flags and cell.

See: docs/commentary/tes5_import_override.md#override-reference-state
"""

import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tes5_import.overrides import nested as OV
from tes5_import.overrides.builder import split_subrecords

CELL = 0x0100AAAA
CELL_PATH = ((0, b'CELL'), (2, b'\x00\x00\x00\x00'), (3, b'\x00\x00\x00\x00'))


def _record(sig, fid, flags, subs):
    """A packed record from (sig, payload) pairs."""
    body = b''.join(s + struct.pack('<H', len(p)) + p for s, p in subs)
    return (sig + struct.pack('<II', len(body), flags)
            + struct.pack('<I', fid) + b'\x00' * 8 + body)


def _data():
    """A DATA position/rotation payload."""
    return struct.pack('<6f', 1.0, 2.0, 3.0, 0.0, 0.0, 0.0)


class _Index:
    """A converted master: {fid: record bytes}, {fid: GRUP path}."""

    def __init__(self, records, paths):
        self._records = dict(records)
        self._paths = dict(paths)

    def record(self, fid):
        return self._records.get(fid, b'')

    def group_path(self, fid):
        return self._paths.get(fid, ())


def _ctx(monkeypatch, records, paths, master_export):
    """An OverrideContext over fake masters; source ids map to output ids 1:1."""
    monkeypatch.setattr(OV, 'master_output_formid',
                        lambda src, manifest: int(src, 16))
    ctx = object.__new__(OV.OverrideContext)
    ctx.master_index = _Index(records, paths)
    ctx.master_manifest = None
    ctx.master_export = dict(master_export)
    ctx.stats = Counter()
    ctx.unmapped_keys = Counter()
    return ctx


def _refr(fid, flags, **extra):
    """A TES4 REFR export record in master CELL."""
    rec = {'Signature': 'REFR', 'FormID': '%08X' % fid,
           'RecordFlags': str(flags), 'NAME': '0100BBBB',
           'ParentCELL': '%08X' % CELL, 'PosX': '1.0', 'PosY': '2.0',
           'PosZ': '3.0', 'RotX': '0.0', 'RotY': '0.0', 'RotZ': '0.0'}
    rec.update(extra)
    return rec


def _flags(record):
    """The header flags of a packed record."""
    return struct.unpack_from('<I', record, 8)[0]


def test_flags_only_disable_is_emitted(monkeypatch):
    """An author's +Initially Disabled ships; the body stays the master's."""
    fid = 0x01001234
    base = _record(b'REFR', fid, 0, [(b'NAME', struct.pack('<I', 0x0100BBBB)),
                                     (b'DATA', _data())])
    ctx = _ctx(monkeypatch, {fid: base}, {fid: CELL_PATH},
               {'%08X' % fid: _refr(fid, 0)})
    ov = ctx.build(_refr(fid, 0x800), 'REFR')
    assert ov.status == 'emitted'
    assert _flags(ov.record_bytes) == 0x800
    assert ov.record_bytes[12:] == base[12:]


def test_taken_bits_keep_the_master_runs_own_bits(monkeypatch):
    """+VWD and a LIGH's +0x200 move; a bit the author left alone survives."""
    fid, light = 0x01001235, 0x0100CCCC
    base = _record(b'REFR', fid, 0x20000000,
                   [(b'NAME', struct.pack('<I', light)), (b'DATA', _data())])
    master = _refr(fid, 0, NAME='%08X' % light)
    ctx = _ctx(monkeypatch, {fid: base}, {fid: CELL_PATH},
               {'%08X' % fid: master,
                '%08X' % light: {'Signature': 'LIGH', 'FormID': '%08X' % light}})
    ov = ctx.build(_refr(fid, 0x8200, NAME='%08X' % light), 'REFR')
    assert _flags(ov.record_bytes) == 0x20008200


def test_bit9_is_not_taken_for_a_non_light(monkeypatch):
    """0x200 on a non-LIGH ref is left to the master run."""
    fid = 0x01001236
    base = _record(b'REFR', fid, 0, [(b'NAME', struct.pack('<I', 0x0100BBBB)),
                                     (b'DATA', _data())])
    ctx = _ctx(monkeypatch, {fid: base}, {fid: CELL_PATH},
               {'%08X' % fid: _refr(fid, 0)})
    assert ctx.build(_refr(fid, 0x200), 'REFR').status == 'unchanged'


def test_map_marker_keeps_persistent(monkeypatch):
    """Clearing 0x400 on a map marker keeps it; the master run forces it."""
    fid = 0x01001237
    base = _record(b'REFR', fid, 0x400,
                   [(b'NAME', struct.pack('<I', 0x10)), (b'XMRK', b''),
                    (b'DATA', _data())])
    ctx = _ctx(monkeypatch, {fid: base}, {fid: CELL_PATH},
               {'%08X' % fid: _refr(fid, 0x400)})
    ov = ctx.build(_refr(fid, 0x800), 'REFR')
    assert _flags(ov.record_bytes) == 0xC00


def test_non_reference_flags_stay_ignored(monkeypatch):
    """An ACTI whose only change is a header bit is still unchanged."""
    fid = 0x01003EF8
    base = _record(b'ACTI', fid, 0, [(b'EDID', b'Thing\x00')])
    master = {'Signature': 'ACTI', 'FormID': '%08X' % fid, 'RecordFlags': '0',
              'EditorID': 'Thing'}
    ctx = _ctx(monkeypatch, {fid: base}, {fid: ((0, b'ACTI'),)},
               {'%08X' % fid: master})
    ov = ctx.build(dict(master, RecordFlags='1024'), 'ACTI')
    assert ov.status == 'unchanged'
    assert [s for s, _p in split_subrecords(base)] == [b'EDID']
