"""FO3/FNV authored navmeshes: their door links name doors the output can find.

See: docs/commentary/tes5_import_navmesh.md#fallout-door-links-renumbered
"""
import struct

from tes5_import.base.text_reader import get_formid, set_formid_index_offset
from tes5_import.base.writer import PluginWriter
from tes5_import.navmesh.edge_links import NavMeshView, extract_nvnm
from tes5_import.record_types.navm_falloutnv import parse_door_links, precompute_fallout_navmeshes


def test_a_door_link_names_the_door_as_the_output_numbers_it():
    """The Prospector Saloon's door 0010618E is 0110618E once Skyrim.esm sits before FalloutNV.esm."""
    set_formid_index_offset(1)
    try:
        links = parse_door_links(struct.pack('<IHxx', 0x0010618E, 46))
        door = get_formid({'FormID': '0010618E'}, 'FormID')
    finally:
        set_formid_index_offset(0)
    assert links == [(46, 0x0110618E)] and door == 0x0110618E


def _navm(fid, link_to=None):
    """A one-triangle FNV NAVM export record; with `link_to`, edge 1-2 is flagged as NVEX link 0 to it."""
    verts = struct.pack('<9f', 0, 0, 0, 100, 0, 0, 0, 100, 0)
    flags = 0x2 if link_to else 0
    rec = {'Signature': 'NAVM', 'FormID': fid, 'DATA.Cell': '00106185', 'ParentCELL': '00106185',
           'NVVX': verts.hex(), 'NVTR': struct.pack('<3H3hHH', 0, 1, 2, -1, 0, -1, flags, 0).hex()}
    if link_to:
        rec['NVEX'] = struct.pack('<IIH', 0, int(link_to, 16), 0).hex()
    return rec


def test_an_authored_edge_link_reaches_the_converted_neighbour():
    """NVEX link 0 on a flagged edge becomes a Skyrim edge link to the neighbour's output FormID.

    Unflagged, the same edge value 0 would read as the triangle itself.
    """
    by_type = {'NAVM': [_navm('00000A01', link_to='00000A02'), _navm('00000A02')], 'CELL': []}
    writer = PluginWriter(masters=['Skyrim.esm'])
    cache = precompute_fallout_navmeshes(by_type, writer)
    (bytes_a, meta_a), (_bytes_b, meta_b) = cache[(0x00106185, 0xA01)], cache[(0x00106185, 0xA02)]
    blob, _pre, _post = extract_nvnm(bytes_a)
    view = NavMeshView(meta_a['fid'], blob)
    assert view.links == [[0, meta_b['fid'], 0]]
    assert view.tris[0][4] == 0 and view.tris[0][6] & 0x2
    assert meta_a['edge_link_fids'] == [meta_b['fid']]
