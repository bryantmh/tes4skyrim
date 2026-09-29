"""A plugin restoring a record a later master deleted splices onto the live copy.

See: docs/commentary/tes5_import_override.md#undeleting-a-masters-record
"""

import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tes5_import.overrides import nested as OV
from tes5_import.overrides.builder import split_subrecords
from tes5_import.overrides.master_index import ChainedMasterIndex

CHILD = ['Skyrim.esm', 'Extra.esm', 'Oblivion.esm', 'DLC.esp']


def _record(sig, fid, flags, subs):
    """A packed record from (sig, payload) pairs."""
    body = b''.join(s + struct.pack('<H', len(p)) + p for s, p in subs)
    return (sig + struct.pack('<II', len(body), flags)
            + struct.pack('<I', fid) + b'\x00' * 8 + body)


class _Fake:
    """One converted master: records and GRUP paths keyed by id."""

    def __init__(self, name, own_index, masters, records, paths):
        """A master named `name` whose own records carry `own_index`."""
        self.path = f'output/{name}/{name}'
        self.own_index = own_index
        self.masters = masters
        self._records = records
        self._paths = paths

    def __contains__(self, fid):
        return fid in self._records

    def record(self, fid):
        return self._records.get(fid, b'')

    def signature(self, fid):
        return self._records.get(fid, b'')[:4]

    def group_path(self, fid):
        return self._paths.get(fid, ())


def _label(fid):
    """A GRUP label naming a cell."""
    return struct.pack('<I', fid)


def _data():
    """A DATA position/rotation payload."""
    return struct.pack('<6f', 1.0, 2.0, 3.0, 0.0, 0.0, 0.0)


def _chain():
    """Oblivion.esm (own index 1) at child slot 2; DLC.esp deletes its ref 01001234."""
    live = _record(b'REFR', 0x01001234, 0,
                   [(b'NAME', struct.pack('<I', 0x01000BBB)),
                    (b'XESP', struct.pack('<II', 0x01005555, 0)),
                    (b'DATA', _data())])
    cell_path = ((0, b'CELL'), (2, b'\x00' * 4), (3, b'\x00' * 4),
                 (6, _label(0x0100AAAA)), (9, _label(0x0100AAAA)))
    oblivion = _Fake('Oblivion.esm', 1, ['Skyrim.esm'],
                     {0x01001234: live}, {0x01001234: cell_path})
    stub = _record(b'REFR', 0x02001234, 0x20,
                   [(b'NAME', struct.pack('<I', 0x02000BBB))])
    dlc = _Fake('DLC.esp', 2, ['Skyrim.esm', 'Oblivion.esm'],
                {0x02001234: stub}, {0x02001234: cell_path})
    return ChainedMasterIndex([oblivion, dlc], base_slot=2,
                              child_masters=CHILD)


def test_live_skips_the_deleted_copy_and_restates_the_slot():
    """live() answers the owner's copy with its ids and labels in the child's slot."""
    record, path = _chain().live(0x02001234)
    assert struct.unpack_from('<II', record, 8) == (0, 0x02001234)
    subs = dict(split_subrecords(record))
    assert struct.unpack_from('<I', subs[b'XESP'])[0] == 0x02005555
    assert path[-1] == (9, _label(0x0200AAAA))


def _ref(flags):
    """The TES4 export of ref 01001234 (plugin space)."""
    return {'Signature': 'REFR', 'FormID': '01001234',
            'RecordFlags': str(flags), 'NAME': '01000BBB',
            'ParentCELL': '0100AAAA', 'PosX': '1.0', 'PosY': '2.0',
            'PosZ': '3.0', 'RotX': '0.0', 'RotY': '0.0', 'RotZ': '0.0'}


def test_undelete_splices_onto_the_live_copy(monkeypatch):
    """The restored ref ships with DATA and no Deleted flag, disabled as authored."""
    monkeypatch.setattr(OV, 'master_output_formid',
                        lambda src, manifest: 0x02001234)
    ctx = object.__new__(OV.OverrideContext)
    ctx.master_index = _chain()
    ctx.master_manifest = None
    ctx.master_export = {'01001234': dict(_ref(0x20))}
    ctx.shadowed = {'01001234': _ref(0)}
    ctx.stats, ctx.unmapped_keys = Counter(), Counter()
    ov = ctx.build(_ref(0x800), 'REFR')
    assert ov.status == 'emitted'
    assert struct.unpack_from('<I', ov.record_bytes, 8)[0] == 0x800
    assert b'DATA' in dict(split_subrecords(ov.record_bytes))


def test_load_master_export_keeps_the_shadowed_live_copy(tmp_path):
    """A later master's deleted copy wins the export, the live one is shadowed."""
    def write(name, masters, flags):
        """One export folder holding ref 00001234 with `flags`."""
        d = tmp_path / name
        d.mkdir()
        (d / '_HEADER.txt').write_text(
            ''.join(f'Master[{i}]={m}\n' for i, m in enumerate(masters)))
        (d / 'REFR.txt').write_text(
            '---RECORD_BEGIN---\nSignature=REFR\nFormID=00001234\n'
            f'RecordFlags={flags}\nNAME=00000BBB\n---RECORD_END---\n')
    write('Oblivion.esm', [], 0)
    write('DLC.esp', ['Oblivion.esm'], 32)
    write('Plugin.esp', ['Oblivion.esm', 'DLC.esp'], 0)
    shadowed = {}
    out = OV.load_master_export(str(tmp_path / 'Plugin.esp'), shadowed)
    assert out['00001234']['RecordFlags'] == '32'
    assert shadowed['00001234']['RecordFlags'] == '0'
