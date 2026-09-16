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


from tes5_import.navmesh import pool as navm_pool
from tes5_import.overrides import nested as overrides


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
