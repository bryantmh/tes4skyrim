"""An overlay's LAND with no height map ERASES the cell's distant terrain.

A cell whose terrain the author deleted ships a LAND that keeps its texture
layers but carries no VNML/VHGT -- the "water only, no landscape" case (DATA
flags clear bit 0x01; vanilla writes 28 and lets the engine auto-calc normals).

`decode_land` returns None for such a record because there are no heights to
decode. The scan then simply skipped it, which for an OVERLAY is wrong: the
master's heightmap stayed in `lands`, so distant terrain kept rendering the very
ground the plugin removed. Reported in-game on TWMP_ValenwoodImproved as "a
large chunk of land that is still there that is supposed to be deleted".

The base file keeps the old behaviour: it has no earlier terrain to erase, and a
malformed record there must not silently delete a cell.
"""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asset_convert.lod import terrain_lod


VERTS = terrain_lod.VERTS_SIDE


def _sub(sig: bytes, payload: bytes) -> bytes:
    return sig + struct.pack('<H', len(payload)) + payload


def _land_body(with_height: bool) -> bytes:
    """A LAND payload, with or without its VHGT array."""
    body = _sub(b'DATA', struct.pack('<I', 31 if with_height else 28))
    if with_height:
        body += _sub(b'VHGT', struct.pack('<f', 0.0)
                     + b'\x00' * (VERTS * VERTS) + b'\x00' * 3)
    return body


def _finder(body):
    """The `_sub` accessor `decode_land` is called with."""
    def get(blob, name):
        want = name.encode() if isinstance(name, str) else name
        i = 0
        while i + 6 <= len(blob):
            sig = blob[i:i + 4]
            size = struct.unpack_from('<H', blob, i + 4)[0]
            if sig == want:
                return blob[i + 6:i + 6 + size]
            i += 6 + size
        return None
    return get


def test_decode_land_returns_none_without_heights():
    body = _land_body(with_height=False)
    assert terrain_lod.decode_land(body, _finder(body)) is None


def test_decode_land_returns_data_with_heights():
    body = _land_body(with_height=True)
    assert terrain_lod.decode_land(body, _finder(body)) is not None


def _apply(lands, coords, body, is_overlay):
    """The scan's decision for one LAND record, mirrored exactly."""
    land = terrain_lod.decode_land(body, _finder(body))
    if land is not None:
        lands[coords] = land
    elif is_overlay:
        lands.pop(coords, None)


def test_overlay_without_heights_erases_the_masters_terrain():
    """The cell must LEAVE the dict so its tile bakes as water."""
    coords = (-30, -49)
    lands = {}
    _apply(lands, coords, _land_body(True), is_overlay=False)   # master
    assert coords in lands, "master terrain must land in the dict first"

    _apply(lands, coords, _land_body(False), is_overlay=True)   # overlay
    assert coords not in lands, (
        "an overlay that deletes the landscape must remove the master's "
        "heightmap, not be skipped")


def test_overlay_with_heights_still_replaces():
    """The ordinary regrade case must keep working."""
    coords = (-30, -49)
    lands = {coords: 'master-terrain'}
    _apply(lands, coords, _land_body(True), is_overlay=True)
    assert lands[coords] != 'master-terrain'
    assert lands[coords] is not None


def test_base_file_without_heights_does_not_delete():
    """No earlier terrain exists to erase, so nothing is popped.

    Guarding on overlay-only also means a malformed base record cannot silently
    remove a cell from the world.
    """
    coords = (-30, -49)
    lands = {coords: 'previously-seen'}
    _apply(lands, coords, _land_body(False), is_overlay=False)
    assert lands[coords] == 'previously-seen'


def test_erasing_a_cell_the_master_never_had_is_harmless():
    """pop() with a default must not raise when the key is absent."""
    lands = {}
    _apply(lands, (1, 2), _land_body(False), is_overlay=True)
    assert lands == {}
