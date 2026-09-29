"""Quest-toggled distant statics ship Full LOD in the persistent cell, by one generic rule.

Synthetic data only: the rule is derived from the source records, never from a
per-plugin list, and both import paths (refs a plugin owns, new refs in a
master's cells) share it.

See: docs/commentary/tes5_import_override.md#full-lod-refs
"""

import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tes5_import.base.writer import PluginWriter
from tes5_import.overrides import nested as OV
from tes5_import.overrides.ref_state import full_lod_bases, is_full_lod_ref
from tes5_import.pipeline_records import _build_world_groups

VWD, PERS, ID = 0x8000, 0x400, 0x800
STAT_FID, TREE_FID, ACTI_FID, FX_FID = '01000A01', '01000A02', '01000A03', '01000A04'
PARENT = '01000B01'


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------

def _bases():
    """Rule bases: one plain STAT and one effect STAT; TREE/ACTI are absent by signature."""
    master_export = {
        STAT_FID: {'Signature': 'STAT', 'Model.MODL': 'Architecture\\Tower\\Tower01.NIF'},
        TREE_FID: {'Signature': 'TREE', 'Model.MODL': 'Trees\\Oak.spt'},
        ACTI_FID: {'Signature': 'ACTI', 'Model.MODL': 'Architecture\\Lever.NIF'},
        FX_FID: {'Signature': 'STAT', 'Model.MODL': 'Effects\\FXSmokeBig.NIF'},
    }
    return full_lod_bases((), master_export)


def _refr(fid='01000C01', flags=VWD, base=STAT_FID, parent=PARENT,
          cell='01000D02', wrld='0100003C'):
    """A TES4 REFR export record; None drops a field."""
    rec = {'Signature': 'REFR', 'FormID': fid, 'RecordFlags': str(flags),
           'NAME': base, 'ParentCELL': cell, 'ParentWRLD': wrld,
           'XESP.Reference': parent, 'XESP.Flags': '0',
           'PosX': '10.0', 'PosY': '20.0', 'PosZ': '30.0',
           'RotX': '0.0', 'RotY': '0.0', 'RotZ': '0.0'}
    return {k: v for k, v in rec.items() if v is not None}


def test_rule_selects_the_positive_case():
    """Exterior STAT REFR, source VWD, non-player enable parent, object model."""
    assert is_full_lod_ref('REFR', _refr(), _bases())
    assert is_full_lod_ref('REFR', _refr(flags=VWD | PERS | ID), _bases())


def test_rule_negatives():
    """Each failing clause alone keeps the ref out."""
    bases = _bases()
    assert not is_full_lod_ref('REFR', _refr(parent='00000014'), bases)
    assert not is_full_lod_ref('REFR', _refr(parent=None), bases)
    assert not is_full_lod_ref('REFR', _refr(flags=PERS | ID), bases)
    assert not is_full_lod_ref('REFR', _refr(base=TREE_FID), bases)
    assert not is_full_lod_ref('REFR', _refr(base=ACTI_FID), bases)
    assert not is_full_lod_ref('REFR', _refr(wrld=None), bases)
    assert not is_full_lod_ref('REFR', _refr(base=FX_FID), bases)
    assert not is_full_lod_ref('ACHR', _refr(), bases)


def test_player_is_the_full_formid_not_its_low_bits():
    """A plugin's own xx000014 is an ordinary parent; only 00000014 is the player."""
    assert is_full_lod_ref('REFR', _refr(parent='03000014'), _bases())


def test_plugin_own_stat_wins_and_is_found():
    """A STAT the plugin defines itself is a base too."""
    own = [{'Signature': 'STAT', 'FormID': '03000E01', 'Model.MODL': 'Mine\\Arch.NIF'}]
    assert is_full_lod_ref('REFR', _refr(base='03000E01'), full_lod_bases(own))


# ---------------------------------------------------------------------------
# Override path: a NEW ref in a master's cell
# ---------------------------------------------------------------------------

M_WRLD, M_PERS, M_GRID = 0x0100003C, 0x01023777, 0x01004535
WORLD_PATH = ((0, b'WRLD'), (1, struct.pack('<I', M_WRLD)))
GRID_PATH = WORLD_PATH + ((4, b'\x00\x00\x00\x00'), (5, b'\x00\x00\x00\x00'))


class _Index:
    """A master world with its persistent cell and one grid cell."""

    def group_path(self, fid):
        """The nesting of the two cells."""
        return {M_PERS: WORLD_PATH, M_GRID: GRID_PATH}.get(fid, ())

    def persistent_cell(self, wrld):
        """The master world's persistent cell."""
        return M_PERS if wrld == M_WRLD else 0


class _Ctx:
    """The OverrideContext pieces _attach_new_records reads."""

    def __init__(self):
        """Any plugin: nothing here names one."""
        self.master_manifest = None
        self.master_export = {}
        self.master_index = _Index()
        self.stats = Counter()
        self.full_lod_bases = _bases()


def _attach(monkeypatch, rec):
    """(pending entry, ctx) after attaching one new REFR."""
    monkeypatch.setattr(OV, 'master_output_formid',
                        lambda src, manifest: 0x01000000 | int(src, 16))
    ctx, pending = _Ctx(), []
    OV._attach_new_records([('REFR', rec)], ctx, pending)
    return pending[0], ctx


def _grid_refr(**kw):
    """A new REFR in the master's grid cell 00004535 of world 0000003C."""
    return _refr(cell='00004535', wrld='0000003C', **kw)


def test_override_path_ships_full_lod_in_the_persistent_cell(monkeypatch):
    """0x10400 set, VWD cleared, nested at (6, pers), (8, pers), listed."""
    (_fid, body, path), ctx = _attach(monkeypatch, _grid_refr(flags=VWD | ID))
    assert struct.unpack_from('<I', body, 8)[0] == 0x10C00
    label = struct.pack('<I', M_PERS)
    assert path == WORLD_PATH + ((6, label), (8, label))
    assert ctx.stats['full-lod'] == 1
    assert ctx.full_lod_refs == ['01000C01']
    assert ctx.stats['renest-pers-in-block'] == 0


def test_override_path_player_parent_stays_in_its_cell(monkeypatch):
    """A player-parented ref keeps its cell, temporary group and flags."""
    (_fid, body, path), _ctx = _attach(
        monkeypatch, _grid_refr(flags=VWD | ID, parent='00000014'))
    assert struct.unpack_from('<I', body, 8)[0] == VWD | ID
    label = struct.pack('<I', M_GRID)
    assert path == GRID_PATH + ((6, label), (9, label))


# ---------------------------------------------------------------------------
# Master path: refs the plugin owns in its own cells
# ---------------------------------------------------------------------------

W_FID, W_PERS, W_GRID = '0000003C', '00023777', '00004535'


def _world_by_type(refs):
    """A world with its persistent cell and one grid cell holding `refs`."""
    wrld = {'Signature': 'WRLD', 'FormID': W_FID, 'EditorID': 'TestWorld',
            'DATA.Flags': '0'}
    pers = {'Signature': 'CELL', 'FormID': W_PERS, 'RecordFlags': str(PERS),
            'ParentWRLD': W_FID, 'DATA.Flags': '2'}
    grid = {'Signature': 'CELL', 'FormID': W_GRID, 'RecordFlags': '0',
            'ParentWRLD': W_FID, 'DATA.Flags': '2', 'XCLC.X': '0', 'XCLC.Y': '0'}
    stats = [{'Signature': 'STAT', 'FormID': s, 'Model.MODL': m}
             for s, m in (('00000A01', 'Architecture\\Tower01.NIF'),
                          ('00000A04', 'Effects\\FXSmokeBig.NIF'))]
    return {'WRLD': [wrld], 'CELL': [pers, grid], 'REFR': refs, 'STAT': stats}


def _refs_by_group(raw):
    """{REFR low FormID: (flags, innermost group (type, label int))}."""
    found, stack, pos = {}, [], 0
    while pos + 24 <= len(raw):
        while stack and pos >= stack[-1][0]:
            stack.pop()
        sig = raw[pos:pos + 4]
        if sig == b'GRUP':
            gsize, label, gtype = struct.unpack_from('<I4sI', raw, pos + 4)
            stack.append((pos + gsize, gtype, struct.unpack('<I', label)[0]))
            pos += 24
            continue
        size, flags, fid = struct.unpack_from('<III', raw, pos + 4)
        if sig == b'REFR':
            found[fid & 0xFFFFFF] = (flags, stack[-1][1:])
        pos += 24 + size
    return found


def _build(refs):
    """Build the world; returns {low fid: (flags, group)}."""
    writer = PluginWriter(masters=['Skyrim.esm'])
    writer.next_object_id = 0x01100000
    _build_world_groups(_world_by_type(refs), writer)
    return _refs_by_group(b''.join(writer._top_groups['WRLD']))


def _own(fid, **kw):
    """A REFR Oblivion.esm-style: own ids, in grid cell 00004535."""
    kw.setdefault('base', '00000A01')
    kw.setdefault('parent', '00000B01')
    return _refr(fid=fid, cell=W_GRID, wrld=W_FID, **kw)


def test_master_path_moves_the_ref_to_the_persistent_cell(capsys):
    """The qualifying ref: type-8 group of the persistent cell, 0x10400, no VWD."""
    got = _build([_own('00000C01', flags=VWD),
                  _own('00000C02', flags=VWD, parent='00000014'),
                  _own('00000C03', flags=0),
                  _own('00000C04', flags=VWD, base='00000A04')])
    pers, grid = int(W_PERS, 16), int(W_GRID, 16)
    flags, group = got[0x000C01]
    assert group == (8, pers)
    assert flags & 0x10400 == 0x10400 and not flags & VWD
    assert got[0x000C02] == (VWD, (9, grid))
    assert got[0x000C03] == (0, (9, grid))
    assert got[0x000C04] == (VWD, (9, grid))
    out = capsys.readouterr().out
    assert "Full LOD (this plugin's own cells): 1 ref(s)" in out
    assert '00000C01' in out


def test_master_path_does_not_touch_the_export_record():
    """The navmesh pool buckets by ParentCELL, so the record keeps it."""
    ref = _own('00000C01', flags=VWD)
    _build([ref])
    assert ref['ParentCELL'] == W_GRID and ref['RecordFlags'] == str(VWD)


def test_effect_mesh_is_shared_and_separator_blind():
    """lod_gen and the import judge a model by one helper; doubled separators split cleanly."""
    from asset_convert.lod import lod_gen
    from asset_convert.lod.effect_mesh import is_effect_mesh
    assert lod_gen.is_effect_mesh is is_effect_mesh
    assert is_effect_mesh('Effects\\\\SEFXSmokeBig.NIF')
    assert is_effect_mesh('meshes/Dungeons/FX/mist.nif')
    assert not is_effect_mesh('Architecture\\kvatch\\Kvatchtree03.NIF')
    assert not is_effect_mesh('effects.nif')
