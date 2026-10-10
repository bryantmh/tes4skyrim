"""A PGRD that edits a MASTER's cell must still produce a NAVM.

A pathgrid converts to a navmesh, which is a different record type, so it can
never be expressed as an override of the master's PGRD bytes. It used to be
listed in OVERRIDE_UNMAPPABLE_TYPES and was dropped in the override pass,
before the navmesh generator ever saw it: ElsweyrAnequina lost the navmesh of
all 863 Oblivion.esm Tamriel cells it edits (547 NAVM shipped where 1,351
pathgrids exist), so the landmass rendered fine and nothing in it could path.

The navmesh it produces still has to OVERRIDE the master's navmesh for that
cell, under the master's own NAVM FormID -- shipping a derived id leaves the
master's navmesh loaded alongside ours, two navmeshes over the same ground.

See: docs/commentary/tes5_import_navmesh.md#master-owned-cells
"""

from collections import Counter


from tes5_import.base.text_reader import set_formid_index_offset
from tes5_import.navmesh import pool as navm_pool
from tes5_import.overrides import nested as overrides


def test_empty_navmesh_stage_keeps_cell_indexes_and_resets_door_links():
    from types import SimpleNamespace
    from tes5_import.pipeline_records import _phase4a_navmesh
    from tes5_import.record_types import world
    by_type = {'STAT': [{'FormID': '01000001', 'Model.MODL': 'tree.nif'}],
               'DOOR': [{'FormID': '01000002', 'Model.MODL': 'door.nif'}],
               'REFR': [{'FormID': '01000003', 'NAME': '01000002'}]}
    ctx = SimpleNamespace(master_export={}, master_index=None,
                          relinked_master_navms=['previous run'])
    st = SimpleNamespace(by_type=by_type, writer=SimpleNamespace(), ctx=ctx,
                         num_tes4_masters=1)
    phases = []
    world.set_door_navmesh_links({0x01000003: (1, 2)})
    _phase4a_navmesh(st, '.', phases.append, ())
    assert not st.navm_cache and not st.navm_metas
    assert st.base_model_by_fid and st.door_fids
    assert ctx.relinked_master_navms == []
    assert not world._DOOR_NAVMESH_LINK
    assert phases == ['navmesh generation']


def test_pathgrid_jobs_still_include_master_geometry():
    cell = {'FormID': '01000001', 'DATA.Flags': '1'}
    pathgrid = {'FormID': '01000002', 'ParentCELL': cell['FormID']}
    master_ref = {'Signature': 'REFR', 'FormID': '00000003',
                  'ParentCELL': cell['FormID'], 'NAME': '00000004'}
    jobs = navm_pool.gather_navm_jobs({'CELL': [cell], 'PGRD': [pathgrid]},
                                     master_export={'00000003': master_ref})
    assert len(jobs) == 1
    assert jobs[0]['pgrd_rec'] == pathgrid
    assert jobs[0]['refr_recs'] == [master_ref]


class _FakeMasterIndex:
    def record(self, fid):
        return b''

    def group_path(self, fid):
        return ((0, b'WRLD'),)

    def land(self, fid):
        return 0


class _Ctx:
    """Minimal stand-in for OverrideContext (only what the routing touches)."""

    def __init__(self, master_export=None, navm_cache=None):
        self.master_export = master_export or {}
        self.master_manifest = {}
        self.master_index = _FakeMasterIndex()
        self.stats = Counter()
        self.navm_cache = navm_cache or {}
        self.navm_metas = []
        self.land_cache = None

    def master_record(self, rec):
        return self.master_export.get((rec.get('FormID') or '').upper())

    build = overrides.OverrideContext.build


class _NavmMasterIndex:
    """Master index that navmeshed some cells and not others."""

    def __init__(self, by_cell):
        self._by_cell = by_cell

    def navms(self, cell_formid):
        """Every NAVM FormID this master put in the cell."""
        return self._by_cell.get(cell_formid, [])


def _job(cell, pgrd, fid):
    """One precompute job, shaped as precompute_navmeshes builds it."""
    return {'key': (cell, pgrd), 'navm_fid': fid}


def test_pgrd_is_not_an_unmappable_type():
    """ROAD really does convert to nothing and stays unmappable; PGRD does not."""
    assert 'PGRD' not in overrides.OVERRIDE_UNMAPPABLE_TYPES
    assert 'ROAD' in overrides.OVERRIDE_UNMAPPABLE_TYPES


def test_pgrd_nests_under_its_parent_cell():
    assert overrides._NEW_NESTED_PARENT.get('PGRD') == 'ParentCELL'


def test_overriding_pgrd_routes_as_a_new_record():
    """build() must return None (= "new record") even when the master has it.

    It must also NOT be counted as inexpressible -- that was the silent drop.
    """
    ctx = _Ctx(master_export={'0000610A': {'FormID': '0000610A',
                                           'Signature': 'PGRD'}})
    rec = {'FormID': '0000610A', 'ParentCELL': '0000610B', 'Signature': 'PGRD'}

    assert ctx.build(rec, 'PGRD') is None
    assert ctx.stats['no-path'] == 0


def test_pgrd_geometry_comes_from_the_precompute_cache():
    """_navm_of keys by (ParentCELL, PGRD), matching _gather_navm_jobs."""
    navm_bytes = b'NAVM' + b'\x00' * 20
    meta = {'fid': 0x0500BEEF}
    ctx = _Ctx(navm_cache={(0x0000610B, 0x0000610A): (navm_bytes, meta)})
    rec = {'FormID': '0000610A', 'ParentCELL': '0000610B'}

    got_bytes, got_meta = overrides._navm_of(rec, ctx)
    assert got_bytes == navm_bytes
    assert got_meta is meta


def test_pgrd_with_no_generated_geometry_is_skipped_cleanly():
    """convert_PGRD declines a too-sparse pathgrid; that is not an error."""
    ctx = _Ctx(navm_cache={})
    rec = {'FormID': '0000610A', 'ParentCELL': '0000610B'}

    assert overrides._navm_of(rec, ctx) == (b'', {})


def test_master_owned_cell_adopts_the_masters_navm_ids():
    """A plugin editing a master's cell OVERRIDES its navmesh, not duplicates.

    The split case too: ids are paired in file order.
    """
    idx = _NavmMasterIndex({0x01003660: [0x01E5591F, 0x0141DD34]})
    jobs = [_job(0x01003660, 0x11, 0x05AAAA01),
            _job(0x01003660, 0x12, 0x05AAAA02)]

    assert navm_pool._adopt_master_navm_fids(jobs, idx) == 2
    assert [j['navm_fid'] for j in jobs] == [0x01E5591F, 0x0141DD34]


def test_a_masterless_plugin_keeps_every_derived_id():
    """No master index => byte-identical output for Oblivion.esm/Nehrim.esm."""
    jobs = [_job(0x01003660, 0x11, 0x05AAAA01)]

    assert navm_pool._adopt_master_navm_fids(jobs, None) == 0
    assert jobs[0]['navm_fid'] == 0x05AAAA01


def test_a_cell_the_master_never_navmeshed_keeps_its_derived_id():
    """A cell the plugin itself adds is a new record, not an override."""
    idx = _NavmMasterIndex({0x01003660: [0x01E5591F]})
    jobs = [_job(0xDEADBEEF, 0x11, 0x05AAAA01)]

    assert navm_pool._adopt_master_navm_fids(jobs, idx) == 0
    assert jobs[0]['navm_fid'] == 0x05AAAA01


def test_splits_beyond_the_masters_count_keep_their_own_ids():
    """Our split may be finer than the master's; the extras are new records."""
    idx = _NavmMasterIndex({0x01003660: [0x01E5591F]})
    jobs = [_job(0x01003660, 0x11, 0x05AAAA01),
            _job(0x01003660, 0x12, 0x05AAAA02)]

    assert navm_pool._adopt_master_navm_fids(jobs, idx) == 1
    assert [j['navm_fid'] for j in jobs] == [0x01E5591F, 0x05AAAA02]


def _refr(fid, cell, flags=0):
    """A minimal REFR export record parented to `cell`."""
    rec = {'Signature': 'REFR', 'FormID': '%08X' % fid,
           'ParentCELL': '%08X' % cell}
    if flags:
        rec['RecordFlags'] = str(flags)
    return rec


def test_navmesh_geometry_merges_the_masters_references():
    """A child plugin carves with the master's furnishing, not just its own.

    UL restates 45 of CloudRulerTempleExterior02's 121 Oblivion.esm refs, so
    navmeshing from `by_type` alone left 109 statics invisible and the mesh
    full of holes.
    """
    by_type = {'REFR': [_refr(0x01000001, 0xAAAA)]}
    master = {'000000AA': _refr(0x000000AA, 0xAAAA),
              '000000BB': _refr(0x000000BB, 0xAAAA)}

    merged = navm_pool._merged(by_type, master, 'REFR')

    assert sorted(r['FormID'] for r in merged) == [
        '000000AA', '000000BB', '01000001']


def test_the_plugins_own_reference_overrides_the_masters():
    """An edited ref appears ONCE, in the plugin's version."""
    own = _refr(0x000000AA, 0xAAAA)
    own['PosX'] = '128.0'
    by_type = {'REFR': [own]}
    master = {'000000AA': _refr(0x000000AA, 0xAAAA)}

    merged = navm_pool._merged(by_type, master, 'REFR')

    assert len(merged) == 1
    assert merged[0]['PosX'] == '128.0'


def test_a_reference_the_plugin_deletes_carves_nothing():
    """A deleted override must not resurrect the master's ref."""
    by_type = {'REFR': [_refr(0x000000AA, 0xAAAA,
                              flags=overrides.DELETED_FLAG)]}
    master = {'000000AA': _refr(0x000000AA, 0xAAAA)}

    assert navm_pool._merged(
        by_type, master, 'REFR') == []


def test_a_masterless_plugin_merges_nothing():
    """Oblivion.esm/Nehrim.esm keep the exact list they always had."""
    own = [_refr(0x00000001, 0xAAAA)]

    assert navm_pool._merged({'REFR': own}, None, 'REFR') == own


def test_an_override_replaces_the_master_after_the_index_shift():
    """With Skyrim.esm prepended, UL's LAND override must win over Oblivion's."""
    own = {'Signature': 'LAND', 'FormID': '00007667', 'LayerCount': '29'}
    master = {'00007667': {'Signature': 'LAND', 'FormID': '00007667',
                           'LayerCount': '26'}}
    set_formid_index_offset(1)
    try:
        merged = navm_pool._merged({'LAND': [own]}, master, 'LAND')
    finally:
        set_formid_index_offset(0)

    assert merged == [own]
