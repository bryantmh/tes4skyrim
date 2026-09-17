"""A child plugin's navmesh must link to its MASTERS' neighbouring navmeshes.

`build_edge_links` indexed only this plugin's own navmeshes, so at the edge of
a plugin's region the neighbour was absent: the seam was skipped AND the
pruning pass deleted the links the master already had.  Because an override
keeps the master's NAVM FormID, the stripped record wins, so every Unique
Landscapes region shipped as a sealed navmesh island -- measured 815 boundary
cells, 0 links out, and 710 of them had a working link in Oblivion.esm.

See: docs/commentary/tes5_import_navmesh.md#cross-plugin-edge-links
"""

import struct

from tes5_import.navmesh import edge_links


def _nvnm(wrld, gx, gy, verts, tris, links=()):
    """A minimal exterior NVNM blob NavMeshView can decode."""
    out = struct.pack('<IiI', 12, 0, wrld) + struct.pack('<hh', gy, gx)
    out += struct.pack('<I', len(verts))
    for v in verts:
        out += struct.pack('<3f', *v)
    out += struct.pack('<I', len(tris))
    for t in tris:
        out += struct.pack('<6h2H', *t)
    out += struct.pack('<I', len(links))
    for lk in links:
        out += struct.pack('<IIh', *lk)
    out += struct.pack('<I', 0) * 2 + struct.pack('<I', 1)
    out += struct.pack('<ff', 0.0, 0.0) + struct.pack('<6f', *([0.0] * 6))
    out += struct.pack('<I', 0)
    return out


def _seam_mesh(wrld, gx, gy, at_max_x):
    """One triangle with an edge lying on a cell's X seam."""
    x = (gx + 1) * edge_links.CELL_SIZE if at_max_x else gx * edge_links.CELL_SIZE
    y0 = gy * edge_links.CELL_SIZE
    verts = [(x, y0 + 100.0, 0.0), (x, y0 + 900.0, 0.0),
             (x + (-400.0 if at_max_x else 400.0), y0 + 500.0, 0.0)]
    tris = [(0, 1, 2, -1, -1, -1, 0, 0)]
    return _nvnm(wrld, gx, gy, verts, tris)


def _packed(blob):
    """Wrap an NVNM blob in an uncompressed NAVM record."""
    sub = b'NVNM' + struct.pack('<H', len(blob)) + blob
    return (b'NAVM' + struct.pack('<IIIIHH', len(sub), 0, 0xDEAD, 0, 44, 0)
            + sub)


class _FakeMasterIndex:
    """Serves one packed NAVM record per FormID."""

    def __init__(self, records):
        """Store the {formid: record bytes} map this index answers from."""
        self._records = records

    def record(self, fid):
        """The packed NAVM record for `fid`, or b'' when absent."""
        return self._records.get(fid, b'')


def _one_cell_cache(packed):
    """A navm_cache holding our single exterior mesh at grid (0,0)."""
    return {(1, 1): (packed,
                     {'fid': 0x01000001, 'is_exterior': True,
                      'wrld_fid': 0x3C, 'grid_x': 0, 'grid_y': 0})}


def test_a_master_neighbour_is_loaded_and_linked():
    """The seam against a master-owned cell produces reciprocal links."""
    navm_cache = _one_cell_cache(_packed(_seam_mesh(0x3C, 0, 0, True)))
    theirs = _packed(_seam_mesh(0x3C, 1, 0, False))
    relinked = []

    made = edge_links.build_edge_links(
        navm_cache, verbose=False,
        master_index=_FakeMasterIndex({0x00000AAA: theirs}),
        master_navms={(0x3C, 1, 0): 0x00000AAA},
        relinked_masters=relinked)

    assert made == 2, 'one seam pair = two reciprocal links'
    assert len(relinked) == 1, 'the master mesh must ship as an override'
    assert relinked[0][1]['fid'] == 0x00000AAA


def test_a_link_to_a_master_survives_pruning():
    """live_fids must include the masters, or _prune_links deletes the link."""
    navm_cache = _one_cell_cache(_packed(_seam_mesh(0x3C, 0, 0, True)))
    theirs = _packed(_seam_mesh(0x3C, 1, 0, False))

    edge_links.build_edge_links(
        navm_cache, verbose=False,
        master_index=_FakeMasterIndex({0x00000AAA: theirs}),
        master_navms={(0x3C, 1, 0): 0x00000AAA},
        relinked_masters=[])

    blob, _pre, _suf = edge_links.extract_nvnm(navm_cache[(1, 1)][0])
    view = edge_links.NavMeshView(0x01000001, blob)
    assert [lk[1] for lk in view.links] == [0x00000AAA]


def test_a_masterless_plugin_is_unaffected():
    """No master index => the old single-plugin behaviour, byte for byte."""
    before = _packed(_seam_mesh(0x3C, 0, 0, True))
    navm_cache = _one_cell_cache(before)
    relinked = []

    made = edge_links.build_edge_links(navm_cache, verbose=False,
                                       relinked_masters=relinked)

    assert made == 0
    assert relinked == []
    assert navm_cache[(1, 1)][0] == before


class _FakeCtx:
    """Just the OverrideContext surface _append_relinked_navms touches."""

    def __init__(self, relinked):
        """Hold the relinked list and a master index that answers a path."""
        self.relinked_master_navms = relinked
        self.master_index = self

    def group_path(self, fid):
        """A single fixed nesting, enough to keep the record non-orphan."""
        return ((0, b'WRLD'),)


def test_relinked_masters_are_queued_only_once():
    """build_nested_overrides runs per signature group; the list is consumed."""
    from tes5_import.overrides import nested

    ctx = _FakeCtx([(b'NAVMbytes', {'fid': 0x00000AAA})])
    first, second = [], []

    nested._append_relinked_navms(first, ctx)
    nested._append_relinked_navms(second, ctx)

    assert len(first) == 1, 'the first pass ships the master navmesh'
    assert second == [], 'the second pass must not ship it again'


def test_a_master_keeps_links_reaching_beyond_our_region():
    """Pruning must not strip a re-emitted master's own outward links."""
    far = 0x0000BBBB
    theirs_blob = _seam_mesh(0x3C, 1, 0, False)
    theirs = _packed(_nvnm(0x3C, 1, 0,
                           [(4096.0, 100.0, 0.0), (4096.0, 900.0, 0.0),
                            (4496.0, 500.0, 0.0)],
                           [(0, 1, 2, -1, -1, -1, 0, 0)],
                           links=[(0, far, 3)]))
    assert theirs_blob
    navm_cache = _one_cell_cache(_packed(_seam_mesh(0x3C, 0, 0, True)))
    relinked = []

    edge_links.build_edge_links(
        navm_cache, verbose=False,
        master_index=_FakeMasterIndex({0x00000AAA: theirs}),
        master_navms={(0x3C, 1, 0): 0x00000AAA, (0x3C, 2, 0): far},
        relinked_masters=relinked)

    assert len(relinked) == 1
    blob, _pre, _suf = edge_links.extract_nvnm(relinked[0][0])
    view = edge_links.NavMeshView(0x00000AAA, blob)
    assert far in {lk[1] for lk in view.links}, (
        "the master's pre-existing outward link must survive")


def test_a_master_link_into_a_cell_we_rewrote_is_dropped():
    """Keeping it would leave a one-way portal: our new mesh has new triangles."""
    ours_fid = 0x01000001
    theirs = _packed(_nvnm(0x3C, 1, 0,
                           [(4096.0, 100.0, 0.0), (4096.0, 900.0, 0.0),
                            (4496.0, 500.0, 0.0)],
                           [(0, 1, 2, -1, -1, -1, 0, 0)],
                           links=[(0, ours_fid, 7)]))
    navm_cache = _one_cell_cache(_packed(_seam_mesh(0x3C, 0, 0, True)))
    relinked = []

    edge_links.build_edge_links(
        navm_cache, verbose=False,
        master_index=_FakeMasterIndex({0x00000AAA: theirs}),
        master_navms={(0x3C, 1, 0): 0x00000AAA},
        relinked_masters=relinked)

    blob, _pre, _suf = edge_links.extract_nvnm(relinked[0][0])
    master = edge_links.NavMeshView(0x00000AAA, blob)
    ours = edge_links.NavMeshView(
        ours_fid, edge_links.extract_nvnm(navm_cache[(1, 1)][0])[0])

    to_ours = [lk for lk in master.links if lk[1] == ours_fid]
    assert len(to_ours) == 1, 'exactly one link, the freshly matched seam'
    assert to_ours[0][2] != 7, 'the stale triangle index must not survive'
    assert 0x00000AAA in {lk[1] for lk in ours.links}, 'and it is reciprocal'
