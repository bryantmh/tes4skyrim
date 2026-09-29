"""Frostcrag Reborn's quest-toggled tower refs ship Full LOD in the persistent cell.

See: docs/commentary/tes5_import_override.md#full-lod-whitelist
"""

import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tes5_import.overrides import nested as OV

WRLD, PERS, GRID = 0x0100003C, 0x01023777, 0x01004535
WORLD_PATH = ((0, b'WRLD'), (1, struct.pack('<I', WRLD)))
GRID_PATH = WORLD_PATH + ((4, b'\x00\x00\x00\x00'), (5, b'\x00\x00\x00\x00'))


class _Index:
    """Tamriel with its persistent cell and one grid cell."""

    def group_path(self, fid):
        return {PERS: WORLD_PATH, GRID: GRID_PATH}.get(fid, ())

    def persistent_cell(self, wrld):
        return PERS if wrld == WRLD else 0


class _Ctx:
    """The OverrideContext pieces _attach_new_records reads, for FR."""

    def __init__(self):
        """FR: three TES4 masters, so its own records carry index 03."""
        self.export_dir = 'export/Frostcrag Reborn 4.1.5 noUOP/DLCFrostcragReborn.esp'
        self.num_tes4_masters = 3
        self.master_manifest = None
        self.master_export = {}
        self.master_index = _Index()
        self.stats = Counter()


def _refr(fid, flags):
    """A FR REFR export record in Tamriel grid cell 00004535."""
    return {'Signature': 'REFR', 'FormID': fid, 'RecordFlags': str(flags),
            'ParentCELL': '00004535', 'ParentWRLD': '0000003C',
            'NAME': '01003EF1', 'PosX': '1.0', 'PosY': '2.0', 'PosZ': '3.0',
            'RotX': '0.0', 'RotY': '0.0', 'RotZ': '0.0'}


def _attach(monkeypatch, rec):
    """(pending entry, ctx) after attaching one new REFR."""
    monkeypatch.setattr(OV, 'master_output_formid',
                        lambda src, manifest: 0x01000000 | int(src, 16))
    ctx, pending = _Ctx(), []
    OV._attach_new_records([('REFR', rec)], ctx, pending)
    return pending[0], ctx


def test_whitelisted_ref_goes_full_lod_in_the_persistent_cell(monkeypatch):
    """0x10400 set, VWD cleared, nested at (6, 01023777), (8, 01023777)."""
    (_fid, body, path), ctx = _attach(monkeypatch, _refr('0301624E', 0x8800))
    assert struct.unpack_from('<I', body, 8)[0] == 0x10C00
    label = struct.pack('<I', PERS)
    assert path == WORLD_PATH + ((6, label), (8, label))
    assert ctx.stats['full-lod'] == 1
    assert ctx.stats['renest-pers-in-block'] == 0


def test_other_ref_stays_in_its_cell(monkeypatch):
    """A ref off the whitelist keeps its cell, group type and flags."""
    (_fid, body, path), _ctx = _attach(monkeypatch, _refr('03016250', 0x8800))
    assert struct.unpack_from('<I', body, 8)[0] == 0x8800
    assert path == GRID_PATH + ((6, struct.pack('<I', GRID)),
                                (9, struct.pack('<I', GRID)))


def test_whitelist_keys_on_the_plugins_own_index():
    """Only FR's own records match: same low bits under a master's index do not."""
    from tes5_import.overrides.ref_state import is_full_lod_ref
    assert is_full_lod_ref('DLCFrostcragReborn.esp', '03008CD0', 3)
    assert not is_full_lod_ref('DLCFrostcragReborn.esp', '01008CD0', 3)
    assert not is_full_lod_ref('Knights.esp', '03008CD0', 3)
