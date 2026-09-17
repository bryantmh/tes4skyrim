"""
Morrowind (TES3) export: reader, ID mapping, coordinates, LAND and integrity.

The measured invariants these lock in are recorded in
docs/commentary/tes4_export_morrowind.md.
"""

import os
import struct

import pytest

from tes4_export import morrowind_land as land
from tes4_export import tes3_reader as reader
from tes4_export.export_morrowind import MorrowindContext, convert_plugin
from tes4_export.morrowind_cell import parse_cell
from tes4_export.morrowind_ids import IdIndex, encode_editor_id, marker_formid
from tes4_export.morrowind_markers import classify, place_name
from tes4_export.morrowind_world import cell_grid, quadrant_suffix

MORROWIND_DATA = r'C:\Program Files (x86)\Steam\steamapps\common\Morrowind\Data Files'
MORROWIND_ESM = os.path.join(MORROWIND_DATA, 'Morrowind.esm')

needs_morrowind = pytest.mark.skipif(
    not os.path.exists(MORROWIND_ESM), reason='Morrowind is not installed')


@pytest.fixture(scope='module')
def morrowind_records():
    """Every record in Morrowind.esm, parsed once for the whole module."""
    _, records = reader.read_file(MORROWIND_ESM)
    return records


def test_editor_id_escape():
    """The escape reproduces Morroblivion's EditorIDs character for character."""
    assert encode_editor_id('ex_redoran_hut_01') == '0exUredoranUhutU01'
    assert encode_editor_id('in_velothismall_ramp_01') == '0inUvelothismallUrampU01'
    assert encode_editor_id('Tel Mora') == '0TelSMora'
    assert encode_editor_id("Balur's Farmhouse") == '0BalurAsSFarmhouse'
    assert encode_editor_id('Tel Aruhn, Bildren') == '0TelSAruhnVSBildren'


def test_id_index_is_case_insensitive():
    """Morrowind compares IDs case-insensitively, so one object stays one."""
    index = IdIndex()
    index.add('0TelSMora', '01234567')
    assert index.lookup('Tel Mora') == '01234567'
    assert index.lookup('TEL MORA') == '01234567'
    assert index.lookup('tel mora') == '01234567'


def test_engine_markers_are_named_not_converted():
    """A marker resolves to the engine's own FormID rather than a new record."""
    assert marker_formid('NorthMarker') == 0x03
    assert marker_formid('northmarker') == 0x03
    assert marker_formid('ex_redoran_hut_01') is None


def test_positions_are_not_scaled():
    """Morroblivion carried world positions across unchanged.

    See: docs/commentary/tes4_export_morrowind.md#coordinates-and-cell-splitting
    """
    assert cell_grid(106900.9, 117556.2) == (26, 28)
    assert cell_grid(-15560.6, -13774.8) == (-4, -4)


def test_quadrant_suffix_matches_morroblivion():
    """(x mod 2) + 2 * (y mod 2), south-west first."""
    assert quadrant_suffix(26, 28) == '00'
    assert quadrant_suffix(27, 28) == '01'
    assert quadrant_suffix(26, 29) == '02'
    assert quadrant_suffix(27, 29) == '03'


def test_derived_ids_are_stable_and_unique():
    """Derivation is hashed, so it never depends on allocation order."""
    first, second = MorrowindContext(), MorrowindContext()
    for name in ('alpha', 'beta', 'gamma'):
        first.register_own(name)
        second.register_own(name)
    forward = [first.resolve(n) for n in ('alpha', 'beta', 'gamma')]
    backward = [second.resolve(n) for n in ('gamma', 'beta', 'alpha')]
    assert forward == list(reversed(backward))
    assert len(set(forward)) == 3


def test_one_id_under_two_types_is_two_records():
    """TES3 ids are unique per type; a flat FormID space has to keep them apart.

    `Sound_Boat_Creak` is both a SOUN and a SCPT in Morrowind.esm, and keying
    derivation on the bare name gave both 00712579 -- one record silently
    replacing the other.
    See: docs/commentary/tes4_export_morrowind.md#per-type-id-namespaces
    """
    ctx = MorrowindContext()
    ctx.register_own('Sound_Boat_Creak', 'SOUN')
    ctx.register_own('Sound_Boat_Creak', 'SCPT')
    sound = ctx.resolve('Sound_Boat_Creak', 'SOUN')
    script = ctx.resolve('Sound_Boat_Creak', 'SCPT')
    assert sound and script and sound != script
    assert ctx.resolve('sound_boat_creak', 'SCPT') == script, 'case-insensitive'
    assert ctx.resolve('Sound_Boat_Creak') == sound, 'untyped takes the first'


def test_unconverted_base_object_is_never_referenced():
    """A reference whose base is missing crashes the engine, so it is dropped."""
    ctx = MorrowindContext()
    assert ctx.resolve('some_npc_we_do_not_convert') == ''
    ctx.register_own('a_static_we_do_convert')
    assert ctx.resolve('a_static_we_do_convert') != ''


def test_land_height_round_trip_is_exact():
    """Morrowind's deltas are whole bytes, so a quadrant re-encodes losslessly.

    Steps stay inside the signed byte a delta has to fit, which is what real
    terrain does -- the encoder clamps anything steeper.
    """
    heights = [float(((x * 3 + y * 5) % 41) - 20) * land.HEIGHT_SCALE
               for y in range(land.TES3_LAND_SIZE)
               for x in range(land.TES3_LAND_SIZE)]
    for quad in ((0, 0), (1, 0), (0, 1), (1, 1)):
        encoded = land.encode_heights(heights, quad)
        assert len(encoded) == 4 + 33 * 33 + 3
        assert _decode_tes4(encoded) == _source_quadrant(heights, quad)


def _decode_tes4(vhgt: bytes) -> list:
    """The absolute heights a TES4 VHGT blob encodes."""
    offset = struct.unpack_from('<f', vhgt, 0)[0]
    deltas = struct.unpack_from('<%db' % (33 * 33), vhgt, 4)
    out = []
    row = offset
    for y in range(33):
        row += deltas[y * 33]
        out.append(row * land.HEIGHT_SCALE)
        col = row
        for x in range(1, 33):
            col += deltas[y * 33 + x]
            out.append(col * land.HEIGHT_SCALE)
    return out


def _source_quadrant(heights: list, quad: tuple) -> list:
    """The 33x33 sub-grid a quadrant should reproduce."""
    x0, y0 = quad[0] * 32, quad[1] * 32
    return [heights[(y0 + y) * land.TES3_LAND_SIZE + x0 + x]
            for y in range(33) for x in range(33)]


@needs_morrowind
def test_land_round_trip_on_real_terrain(morrowind_records):
    """Real Morrowind terrain survives the split with no height error at all."""
    lands = [r for r in morrowind_records if r.type == 'LAND']
    checked = 0
    for rec in lands:
        vhgt = reader.get_subrecord(rec, 'VHGT')
        if vhgt is None:
            continue
        heights = land.decode_heights(vhgt.data)
        for quad in ((0, 0), (1, 0), (0, 1), (1, 1)):
            got = _decode_tes4(land.encode_heights(heights, quad))
            assert got == _source_quadrant(heights, quad)
        checked += 1
        if checked == 20:
            break
    assert checked == 20


def test_vtex_index_is_biased_by_one():
    """Zero means the default texture; every other value is an LTEX index + 1."""
    assert land.ltex_index(0) is None
    assert land.ltex_index(1) == 0
    assert land.ltex_index(39) == 38


def test_icon_paths_are_renamed_to_dds():
    """Records name icons .tga but the archives ship .dds, as the engine does."""
    from tes4_export.record_types.morrowind import as_dds
    assert as_dds('w' + chr(92) + 'tx_mace01.tga') == 'w' + chr(92) + 'tx_mace01.dds'
    assert as_dds('a.bmp') == 'a.dds'
    assert as_dds('a.dds') == 'a.dds'
    assert as_dds('no_extension') == 'no_extension'


@needs_morrowind
def test_morrowind_bsa_reads_every_file():
    """The TES3 archive is a different format from every later BSA."""
    from asset_convert.sources.bsa_extract_morrowind import (
        is_morrowind_bsa, iter_bsa)
    archive = os.path.join(MORROWIND_DATA, 'Morrowind.bsa')
    if not os.path.exists(archive):
        pytest.skip('Morrowind.bsa is not present')
    assert is_morrowind_bsa(archive)
    names = set()
    meshes = 0
    for name, payload in iter_bsa(archive):
        names.add(name.lower())
        if name.lower().endswith('.nif'):
            meshes += 1
            assert payload[:4] == b'NetI', name
    assert len(names) == 11090
    assert meshes == 5798


@needs_morrowind
def test_reader_walks_to_exact_eof(morrowind_records):
    """A 16-byte record header stride consumes Morrowind.esm exactly."""
    counts = {}
    for rec in morrowind_records:
        counts[rec.type] = counts.get(rec.type, 0) + 1
    assert len(morrowind_records) == 48295
    assert counts['STAT'] == 2788
    assert counts['CELL'] == 2538
    assert counts['LAND'] == 1390


@needs_morrowind
def test_cell_refs_parse_positionally(morrowind_records):
    """NAME and DATA repeat inside the reference run, so FRMR delimits it."""
    cells = [parse_cell(r) for r in morrowind_records if r.type == 'CELL']
    tel_mora = next(c for c in cells
                    if c.name == 'Tel Mora' and not c.interior)
    assert tel_mora.grid == (13, 14)
    assert tel_mora.region == "Azura's Coast Region"
    assert len(tel_mora.refs) == 197
    assert sum(len(c.refs) for c in cells) == 316116


@needs_morrowind
def test_conversion_emits_no_duplicate_formids(morrowind_records):
    """Every emitted record must own a distinct FormID."""
    ctx = MorrowindContext()
    out = convert_plugin(morrowind_records, ctx)
    seen = set()
    for records in out.values():
        for form_id, _ in records:
            assert form_id not in seen, f'duplicate FormID {form_id}'
            seen.add(form_id)
    assert len(seen) > 300000


@needs_morrowind
def test_every_land_lands_in_a_written_cell(morrowind_records):
    """Terrain must name a cell that exists, in the worldspace that exists.

    Emitting only the quadrants that held a reference stranded 1,060 of the
    5,560 LAND quadrants, because an empty quadrant still has terrain.
    """
    ctx = MorrowindContext()
    out = convert_plugin(morrowind_records, ctx)
    cells = {form_id for form_id, _ in out['CELL']}
    world = ctx.worldspace_id()
    assert [form_id for form_id, _ in out['WRLD']] == [world]
    for _, lines in out['LAND']:
        parent = next(l[11:] for l in lines if l.startswith('ParentCELL='))
        assert parent in cells, f'LAND in unwritten cell {parent}'
        assert f'ParentWRLD={world}' in lines


@needs_morrowind
def test_morroblivion_worldspace_is_joined_not_redefined(morrowind_records):
    """With Morroblivion present its worldspace is reused, never duplicated.

    Defining a second WRLD under the same EditorID would split the map rather
    than join it, which is the whole point of the Morroblivion path.
    """
    from tes4_export.morrowind_ids import IdIndex
    index = IdIndex()
    index.add('WrldMorrowind', '01380000')
    ctx = MorrowindContext(index)
    assert ctx.worldspace_id() == '01380000'
    assert not ctx.owns_worldspace()
    out = convert_plugin(morrowind_records[:4000], ctx)
    assert out['WRLD'] == []


@needs_morrowind
def test_worldspace_bounds_span_the_cells(morrowind_records):
    """An empty NAM0/NAM9 rectangle is a degenerate object-LOD quadtree.

    The engine builds worldspace extents while PARSING, so bounds of
    (0,0)-(0,0) around 305,858 references never finish loading.
    """
    ctx = MorrowindContext()
    out = convert_plugin(morrowind_records, ctx)
    lines = out['WRLD'][0][1]
    bounds = {k: float(next(l.split('=')[1] for l in lines
                            if l.startswith(k + '=')))
              for k in ('NAM0.MinX', 'NAM0.MinY', 'NAM9.MaxX', 'NAM9.MaxY')}
    assert bounds['NAM9.MaxX'] > bounds['NAM0.MinX']
    assert bounds['NAM9.MaxY'] > bounds['NAM0.MinY']

    grids = [(int(next(l[7:] for l in ls if l.startswith('XCLC.X='))),
              int(next(l[7:] for l in ls if l.startswith('XCLC.Y='))))
             for _, ls in out['CELL']
             if any(l.startswith('XCLC.X=') for l in ls)]
    assert bounds['NAM0.MinX'] <= min(g[0] for g in grids) * 4096
    assert bounds['NAM9.MaxX'] >= (max(g[0] for g in grids) + 1) * 4096


@needs_morrowind
def test_terrain_carries_texture_layers(morrowind_records):
    """LAND with no base texture layer crashed the game entering a cell.

    Vanilla Skyrim never ships DATA without the layers bit, and the terrain
    shader has no texture to draw with.
    """
    ctx = MorrowindContext()
    out = convert_plugin(morrowind_records, ctx)
    layered = 0
    for _, lines in out['LAND']:
        count = next((int(l[11:]) for l in lines
                      if l.startswith('LayerCount=')), 0)
        flags = int(next(l[11:] for l in lines if l.startswith('DATA.Flags=')))
        assert flags & 0x01, 'normals/heights bit must be set'
        assert bool(flags & 0x04) == bool(count), 'layers bit must match layers'
        if count:
            layered += 1
            assert any(l.endswith('.Type=BASE') for l in lines)
    assert layered > 0.9 * len(out['LAND']), 'most terrain must be textured'


def test_vtex_grid_shifts_one_column_east_borrowing_from_the_west_cell():
    """Ground column x shows VTEX column x-1; column 0 is the west cell's 15.

    See: docs/commentary/tes4_export_morrowind.md#vtex-is-offset-one-column
    """
    n = land.TES3_TEX_SIZE
    own = [y * 100 + x for y in range(n) for x in range(n)]
    west = [9000 + y for y in range(n) for _x in range(n)]
    shifted = land.shift_textures(own, west)
    for y in range(n):
        assert shifted[y * n] == 9000 + y
        assert shifted[y * n + 1:(y + 1) * n] == own[y * n:(y + 1) * n - 1]
    alone = land.shift_textures(own, None)
    assert [alone[y * n] for y in range(n)] == [own[y * n] for y in range(n)]


def test_landscape_icon_only_prefixes_a_bare_name():
    """Morrowind ships terrain textures flat; Oblivion under Landscape\\.

    Prefixing a full path invented a folder that does not exist and all 107
    terrain textures resolved to nothing.
    """
    from asset_convert.game_paths import set_namespace
    from tes5_import.record_types.common import landscape_texture_path
    set_namespace('tes4')
    sep = chr(92)
    assert landscape_texture_path('Bark01.dds') == sep.join(
        ('tes4', 'landscape', 'Bark01.dds'))
    assert landscape_texture_path('textures' + sep + 'a.dds') == sep.join(
        ('tes4', 'a.dds'))
    assert landscape_texture_path('landscape' + sep + 'a.dds') == sep.join(
        ('tes4', 'landscape', 'a.dds'))


def test_wrld_climate_names_a_record_that_exists():
    """CNAM.Vanilla is read verbatim; remapping it would leave it dangling.

    See: docs/commentary/tes5_import_landscape.md#wrld-climate
    """
    from tes5_import.record_types.world import _world_climate
    from tes5_import.base.text_reader import set_formid_index_offset
    set_formid_index_offset(1)
    try:
        assert _world_climate({'CNAM.Vanilla': '00000812'}) == 0x812
        assert _world_climate({'CNAM.Climate': '0000ABCD'}) == 0x100ABCD
        assert _world_climate({}) == 0x100015F
        assert _world_climate({'CNAM.Vanilla': 'zz'}) == 0x100015F
    finally:
        set_formid_index_offset(0)


@needs_morrowind
def test_collision_node_becomes_skyrim_collision():
    """A RootCollisionNode must build real collision and stop rendering.

    See: docs/commentary/asset_convert_nif.md#morrowind-collision
    """
    import io

    from asset_convert.nif import pyffi_monkey_patch
    from asset_convert.nif.nif_converter_morrowind import (
        attach_morrowind_collision, collision_triangles, find_collision_node)
    assert pyffi_monkey_patch, 'the 4.0.0.2 read layouts must be installed'
    from asset_convert.sources.bsa_extract_morrowind import iter_bsa
    from pyffi.formats.nif import NifFormat

    archive = os.path.join(MORROWIND_DATA, 'Morrowind.bsa')
    if not os.path.exists(archive):
        pytest.skip('Morrowind.bsa is not present')
    target = 'meshes\\i\\in_r_l_int_bridge_02.nif'
    payload = next((p for n, p in iter_bsa(archive)
                    if n.lower().replace('/', '\\') == target), None)
    assert payload is not None, 'sample mesh missing from the archive'

    data = NifFormat.Data()
    data.read(io.BytesIO(payload))
    root = data.roots[0]
    node = find_collision_node(root)
    assert node is not None, 'collision node not found by block type'

    tris = collision_triangles(node, root)
    assert len(tris) == 30
    span = max(v[0] for t in tris for v in t) - min(v[0] for t in tris
                                                    for v in t)
    assert 7.2 < span < 7.4, 'triangles are not in Skyrim havok units'

    before = sum(1 for b in root.tree()
                 if isinstance(b, NifFormat.NiTriBasedGeom))
    assert attach_morrowind_collision(root)
    after = sum(1 for b in root.tree()
                if isinstance(b, NifFormat.NiTriBasedGeom))
    assert after < before, 'collision geometry would still render'
    assert find_collision_node(root) is None, 'node not stripped'

    body = root.collision_object.body
    assert body.mass == 0.0
    assert body.havok_col_filter.layer == 1
    assert type(body.shape).__name__ == 'bhkMoppBvTreeShape'
    assert body.shape.shape.target is root


@needs_morrowind
def test_item_models_convert_to_dynamic_clutter():
    """An item record's model simulates; a fixture's stays static.

    A dynamic body must carry a CONVEX shape -- havok will not simulate the
    concave MOPP the static path builds -- on SKYL_CLUTTER with the authored
    DATA.Weight and a non-zero inertia tensor.
    See: docs/commentary/asset_convert_collision.md#morrowind-dynamic-clutter
    """
    import io

    from asset_convert.nif import pyffi_monkey_patch
    from asset_convert.nif.nif_converter_morrowind import (
        build_collision, collision_triangles, find_collision_node)
    assert pyffi_monkey_patch, 'the 4.0.0.2 read layouts must be installed'
    from asset_convert.sources.bsa_extract_morrowind import iter_bsa
    from pyffi.formats.nif import NifFormat

    archive = os.path.join(MORROWIND_DATA, 'Morrowind.bsa')
    if not os.path.exists(archive):
        pytest.skip('Morrowind.bsa is not present')
    target = 'meshes\\i\\in_r_l_int_bridge_02.nif'
    payload = next((p for n, p in iter_bsa(archive)
                    if n.lower().replace('/', '\\') == target), None)
    assert payload is not None, 'sample mesh missing from the archive'

    data = NifFormat.Data()
    data.read(io.BytesIO(payload))
    root = data.roots[0]
    tris = collision_triangles(find_collision_node(root), root)

    static = build_collision(root, tris).body
    assert static.mass == 0.0
    assert static.havok_col_filter.layer == 1

    dynamic = build_collision(root, tris, 4.5).body
    assert dynamic.mass == 4.5
    assert dynamic.havok_col_filter.layer == 4, 'not on SKYL_CLUTTER'
    assert dynamic.motion_system == 3 and dynamic.quality_type == 4
    assert 'Convex' in type(dynamic.shape).__name__ or \
        type(dynamic.shape).__name__ == 'bhkListShape'
    assert dynamic.inertia.m_11 > 0, 'a zero tensor spins freely'


@needs_morrowind
def test_triangles_survive_the_version_upgrade():
    """A Morrowind shape must ship the indices it declares.

    `Has Triangles` postdates 4.0.0.2, so pyffi reads the array but leaves the
    flag False; the Skyrim-version writer then honours the flag and emitted
    Num Triangles with no index data at all. The engine aborts the process on
    that (c0000409 FAST_FAIL_INVALID_ARG), so the invariant is asserted on the
    WRITTEN bytes, not on the in-memory blocks.
    See: docs/commentary/asset_convert_nif.md#morrowind-triangle-flag
    """
    import io as _io

    from asset_convert.nif import pyffi_monkey_patch
    from asset_convert.nif.nif_converter_morrowind import (
        is_morrowind, raise_triangle_flags)
    assert pyffi_monkey_patch, 'the 4.0.0.2 read layouts must be installed'
    from asset_convert.sources.bsa_extract_morrowind import iter_bsa
    from pyffi.formats.nif import NifFormat

    archive = os.path.join(MORROWIND_DATA, 'Morrowind.bsa')
    if not os.path.exists(archive):
        pytest.skip('Morrowind.bsa is not present')
    target = 'meshes\\i\\in_r_l_int_bridge_02.nif'
    payload = next((p for n, p in iter_bsa(archive)
                    if n.lower().replace('/', '\\') == target), None)
    assert payload is not None, 'sample mesh missing from the archive'

    data = NifFormat.Data()
    data.read(_io.BytesIO(payload))
    assert is_morrowind(data)

    shapes = [b for b in data.blocks if getattr(b, 'triangles', None)]
    assert shapes, 'sample carries no indexed geometry'
    assert not any(b.has_triangles for b in shapes), (
        'the flag is absent at 4.0.0.2 and must read False before the fix')
    expected = {id(b): len(b.triangles) for b in shapes}

    assert raise_triangle_flags(data) == len(shapes)

    buf = _io.BytesIO()
    data.write(buf)
    buf.seek(0)
    reloaded = NifFormat.Data()
    reloaded.read(buf)

    written = [b for b in reloaded.blocks
               if getattr(b, 'num_triangles', 0)]
    assert written, 'the round-trip lost every shape'
    for block in written:
        assert len(block.triangles) == block.num_triangles, (
            'declared %d indices, shipped %d'
            % (block.num_triangles, len(block.triangles)))
    assert sum(len(b.triangles) for b in written) == sum(expected.values())


@needs_morrowind
def test_skinned_mesh_gets_a_partition_on_the_instance():
    """A skinned Morrowind mesh must reach Skyrim with a NiSkinPartition.

    NiSkinPartition postdates 4.0.0.2, so no source mesh has one and Skyrim's
    renderer dereferences the null (EXCEPTION_ACCESS_VIOLATION at
    SkyrimSE+0E552FA). The partition must sit on the NiSkinInstance, as all 146
    vanilla instances measured do, and must survive the WRITE -- it is built
    after the version upgrade precisely because the 4.0.0.2 schema has no ref
    for it and the writer would drop it.
    See: docs/commentary/asset_convert_nif.md#morrowind-skin-partitions
    """
    import tempfile

    from asset_convert.nif import pyffi_monkey_patch
    from asset_convert.nif.nif_converter import convert_nif
    assert pyffi_monkey_patch, 'the 4.0.0.2 read layouts must be installed'
    from pyffi.formats.nif import NifFormat

    from asset_convert.sources.bsa_extract_morrowind import iter_bsa

    archive = os.path.join(MORROWIND_DATA, 'Morrowind.bsa')
    if not os.path.exists(archive):
        pytest.skip('Morrowind.bsa is not present')
    wanted = 'meshes' + os.sep + 'f' + os.sep + 'furn_bannerd_wa_shop_01.nif'
    payload = next((p for n, p in iter_bsa(archive)
                    if n.lower().replace('/', os.sep) == wanted), None)
    assert payload is not None, 'sample skinned mesh missing from the archive'

    with tempfile.TemporaryDirectory() as tmp:
        source = os.path.join(tmp, 'source.nif')
        with open(source, 'wb') as handle:
            handle.write(payload)
        target = os.path.join(tmp, 'skinned.nif')
        convert_nif(source, target)
        data = NifFormat.Data()
        with open(target, 'rb') as handle:
            data.read(handle)

    skins = [b for b in data.blocks if type(b).__name__ in
             ('NiSkinInstance', 'BSDismemberSkinInstance')]
    assert skins, 'the sample lost its skin instance in conversion'
    for skin in skins:
        partition = getattr(skin, 'skin_partition', None)
        assert partition is not None, 'no partition on the NiSkinInstance'
        assert partition.num_skin_partition_blocks, 'partition holds no blocks'
        for block in partition.skin_partition_blocks:
            assert block.num_vertices, 'partition block has no vertices'
            assert block.num_triangles, 'partition block has no triangles'
            assert block.num_weights_per_vertex == 4, (
                'vanilla uses 4 weights per vertex in 300 of 300 partitions')


@needs_morrowind
def test_no_reference_outlives_its_base_record(morrowind_records):
    """Emitting a reference to a missing base record would crash the engine.

    Checked with no converted plugin available, which is the standalone path
    and the weaker of the two: nothing can be borrowed from Morroblivion.
    """
    ctx = MorrowindContext()
    out = convert_plugin(morrowind_records, ctx)
    bases = {form_id for sig, records in out.items()
             if sig not in ('CELL', 'REFR', 'LAND')
             for form_id, _ in records}
    bases.update('%08X' % fid for fid in (1, 2, 3, 5, 6, 0x10, 0x3B))
    cells = {form_id for form_id, _ in out['CELL']}
    for _, lines in out['REFR']:
        name = next(line[5:] for line in lines if line.startswith('NAME='))
        assert name in bases, f'reference to unwritten base {name}'
        parent = next(line[11:] for line in lines
                      if line.startswith('ParentCELL='))
        assert parent in cells, f'reference in unwritten cell {parent}'


def test_place_name_collapses_the_comma_convention():
    """A town's forty interiors must yield one marker, not forty.

    See: docs/commentary/tes4_export_morrowind.md#map-markers
    """
    assert place_name('Balmora, Eight Plates') == 'Balmora'
    assert place_name('Vivec, St. Olms Canton') == 'Vivec'
    assert place_name('Nchuleft') == 'Nchuleft'


def test_size_outranks_kind_when_classifying():
    """A city holding an egg mine is a city; kind-first made it a mine."""
    assert classify('Gnisis', ['Gnisis, Madach Egg Mine'], 40) == 3
    assert classify('Abaelun Mine', ['Abaelun Mine'], 1) == 6
    assert classify('Urshilaku Camp', ['Urshilaku Camp, Yurt'], 10) == 1


def test_dwemer_names_are_told_by_consonant_clusters():
    """Bare "nch" is ordinary spelling and must not flag a ruin."""
    for name in ('Nchuleft', 'Mzahnch', 'Bthungthumz', 'Arkngthand'):
        assert classify(name, [name], 1) == 4, name
    for name in ('Llemis Ranch', 'Entrenched Shipwreck'):
        assert classify(name, [name], 1) != 4, name
