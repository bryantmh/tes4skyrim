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


OTHER = 0x0100DDDD
OTHER_PATH = ((0, b'CELL'), (2, b'\x01\x00\x00\x00'), (3, b'\x01\x00\x00\x00'))
LCTN = 0x0100EEEE


def _cell_label(fid):
    """A GRUP label naming a cell."""
    return struct.pack('<I', fid)


def _sigs(record):
    """The subrecord signatures of a packed record."""
    return [s for s, _p in split_subrecords(record)]


def _flip_ctx(monkeypatch, fid, master_flags):
    """A master REFR converted by convert_REFR, located in LCTN."""
    from tes5_import.base.text_reader import get_formid
    from tes5_import.record_types.world import set_cell_locations
    master = _refr(fid, master_flags)
    set_cell_locations({get_formid(master, 'ParentCELL'): LCTN})
    base = OV.convert_REFR(master)
    path = CELL_PATH + ((6, _cell_label(CELL)),
                        (8 if master_flags & 0x400 else 9, _cell_label(CELL)))
    return _ctx(monkeypatch, {fid: base}, {fid: path, CELL: CELL_PATH},
                {'%08X' % fid: master})


def test_persistence_flip_renests_and_gains_xlcn(monkeypatch):
    """temp -> persistent moves to (8, cell) and carries XLCN; back again to (9, cell)."""
    from tes5_import.record_types.world import set_cell_locations
    try:
        ctx = _flip_ctx(monkeypatch, 0x01001240, 0)
        ov = ctx.build(_refr(0x01001240, 0x400), 'REFR')
        assert ov.path == CELL_PATH + ((6, _cell_label(CELL)),
                                       (8, _cell_label(CELL)))
        assert b'XLCN' in _sigs(ov.record_bytes)
        ctx = _flip_ctx(monkeypatch, 0x01001241, 0x400)
        ov = ctx.build(_refr(0x01001241, 0), 'REFR')
        assert ov.path[-1] == (9, _cell_label(CELL))
        assert b'XLCN' not in _sigs(ov.record_bytes)
    finally:
        set_cell_locations({})


class _Writer:
    """Collects the top-level groups emit_nested_overrides writes."""

    def __init__(self):
        self.bodies = []

    def add_raw_group(self, label, body):
        self.bodies.append(body)


def _refr_headers(blob, fid):
    """How many REFR records in `blob` carry `fid`."""
    want = struct.pack('<I', fid)
    count, at = 0, blob.find(b'REFR')
    while at >= 0:
        count += blob[at + 12:at + 16] == want
        at = blob.find(b'REFR', at + 1)
    return count


def test_cell_move_ships_once_at_the_new_cell(monkeypatch):
    """A moved reference nests under its new cell and appears once."""
    fid = 0x01001242
    base = _record(b'REFR', fid, 0, [(b'NAME', struct.pack('<I', 0x0100BBBB)),
                                     (b'DATA', _data())])
    old_path = CELL_PATH + ((6, _cell_label(CELL)), (9, _cell_label(CELL)))
    ctx = _ctx(monkeypatch, {fid: base}, {fid: old_path, OTHER: OTHER_PATH},
               {'%08X' % fid: _refr(fid, 0)})
    ctx.emitted_wrld, ctx.anchored_wrld = {}, set()
    moved = _refr(fid, 0, ParentCELL='%08X' % OTHER)
    writer = _Writer()
    OV.build_nested_overrides({'REFR': [moved]}, ('REFR',), ctx, writer, 't')
    blob = b''.join(writer.bodies)
    assert _refr_headers(blob, fid) == 1
    assert ctx.stats['renested'] == 1
    assert blob.find(_cell_label(OTHER)) < blob.find(struct.pack('<I', fid))


def test_rehomed_reference_keeps_the_master_path(monkeypatch):
    """With no authored cell or persistence change the master's path stands."""
    fid = 0x01001243
    base = _record(b'REFR', fid, 0, [(b'NAME', struct.pack('<I', 0x0100BBBB)),
                                     (b'XSCL', struct.pack('<f', 2.0)),
                                     (b'DATA', _data())])
    rehomed = OTHER_PATH + ((6, _cell_label(OTHER)), (9, _cell_label(OTHER)))
    ctx = _ctx(monkeypatch, {fid: base}, {fid: rehomed, CELL: CELL_PATH},
               {'%08X' % fid: _refr(fid, 0, **{'XSCL.Scale': '2.0'})})
    ov = ctx.build(_refr(fid, 0, **{'XSCL.Scale': '3.0'}), 'REFR')
    assert ov.status == 'emitted'
    assert (getattr(ov, 'path', None) or ctx.master_index.group_path(fid)) == rehomed


def test_new_and_renested_references_share_one_chain(monkeypatch):
    """A new persistent ref and a re-flagged override land in one group."""
    fid = 0x01001244
    base = _record(b'REFR', fid, 0, [(b'NAME', struct.pack('<I', 0x0100BBBB)),
                                     (b'DATA', _data())])
    ctx = _ctx(monkeypatch, {fid: base},
               {fid: CELL_PATH + ((6, _cell_label(CELL)), (9, _cell_label(CELL))),
                CELL: CELL_PATH}, {'%08X' % fid: _refr(fid, 0)})
    ov = ctx.build(_refr(fid, 0x400), 'REFR')
    pending = []
    OV._attach_new_records([('REFR', _refr(0x03000800, 0x400))], ctx, pending)
    assert pending[0][2] == ov.path


def test_placement_faults():
    """Persistent in a block cell and temporary in the persistent cell are counted."""
    from tes5_import.overrides.ref_state import placement_fault
    world = ((0, b'WRLD'), (1, _cell_label(0x0100003C)))
    block = world + ((4, b'\x00\x00\x00\x00'), (5, b'\x00\x00\x00\x00'))
    pers = _cell_label(0x01023777)
    grid = _cell_label(0x01004535)
    assert placement_fault(block + ((6, grid), (8, grid))) == 'renest-pers-in-block'
    assert placement_fault(world + ((6, pers), (9, pers))) == 'renest-temp-in-persistent-cell'
    assert placement_fault(world + ((6, pers), (8, pers))) == ''
    assert placement_fault(block + ((6, grid), (9, grid))) == ''
