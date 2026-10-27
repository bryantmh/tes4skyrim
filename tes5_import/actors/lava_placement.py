"""Place emissive lava surfaces over Oblivion's realm water.

Skyrim's water shader has no diffuse texture and no emissive term (see
`asset_convert/lava_surface.py` for the disassembly that establishes this), so
a WATR record alone cannot render as lava however its colors are set.  Vanilla
Dawnguard solves it by layering a mesh with a BSEffectShaderProperty over the
water; this module builds the record side of that layer:

  * one generated STAT per lava WATR (the mesh is written by
    `asset_convert.lava_surface`), and
  * one placed REFR per affected cell, sitting at that cell's water height.

The WATR conversion is untouched and keeps doing the physics — swim, damage,
fog, underwater tint.  This only adds what the player sees.

WHICH CELLS COUNT
-----------------
The lava is identified from AUTHORED data, never from an EditorID or a
filename:

  * a WATR is lava when it declares itself so: `MNAM.MaterialID` == "lava",
    Oblivion's own material tag.
  * a cell reaches that water either through its own `XCWT.Water` or, failing
    that, by inheriting its worldspace's `NAM2.Water` -- exactly how the
    engine resolves which water a cell renders.

Interior cells carry an explicit `XCLW.WaterHeight`.  Exterior cells almost
never do, and inherit the worldspace default (TES5 WRLD DNAM water height,
which the converter writes as 0.0) — the same value the engine uses to place
the water plane itself, so the lava lands exactly on it.
"""

from __future__ import annotations

import struct

from asset_convert.game_paths import current_namespace

from ..base.text_reader import get_float, get_formid, get_int, get_str
from ..base.writer import (pack_obnd, pack_record, pack_string_subrecord,
                     pack_subrecord)
from ..record_types.world import cell_water_height

def lava_mesh_rel() -> str:
    """The generated lava plane, under the ACTIVE game namespace."""
    return current_namespace() + '\\water\\lavasurface.nif'


# A lava plane is one exterior cell across; interiors get the same mesh, which
# is larger than most interior water bodies but is clipped by the room's own
# geometry exactly as Oblivion's own water plane was.
_CELL_SIZE = 4096.0


def _is_lava_watr(rec: dict) -> bool:
    """True when the WATR record is authored as lava."""
    return get_str(rec, 'MNAM.MaterialID', '').strip().lower() == 'lava'


def collect_lava_water_fids(by_type: dict) -> set:
    """Source FormIDs of every WATR the source declares to BE lava.

    The signal is the record's own `MNAM.MaterialID`, which Oblivion authors
    as the literal string "lava".  That is the only lava declaration in the
    data, and it needs no help: on Oblivion.esm exactly the two lava records
    carry it (OblivionLavaTest01, OblivionCitadelLavaPlane) and nothing else
    sets MNAM at all.

    Nothing here looks at worldspaces.  Whether a worldspace is an "Oblivion
    realm" says nothing about whether its water is lava — a realm could use
    ordinary water and a normal worldspace could hold a lava pool.  Lava is a
    property of the WATER, so the water is what gets asked.
    """
    return {get_formid(rec, 'FormID')
            for rec in by_type.get('WATR', [])
            if _is_lava_watr(rec)}


def scroll_for(by_type: dict, lava_fids: set):
    """Authored UV scroll speeds of the lava, for the mesh's controllers.

    Several records may qualify; the one that actually authors motion wins,
    so a 2-byte stub record cannot silence a full one.
    """
    best = (0.0, 0.0)
    for rec in by_type.get('WATR', []):
        if get_formid(rec, 'FormID') not in lava_fids:
            continue
        sx = get_float(rec, 'DATA.ScrollXSpeed', 0.0)
        sy = get_float(rec, 'DATA.ScrollYSpeed', 0.0)
        if abs(sx) + abs(sy) > abs(best[0]) + abs(best[1]):
            best = (sx, sy)
    return best


def build_lava_stat(stat_fid: int) -> bytes:
    """The STAT wrapping the generated lava mesh.

    TES5 STAT order: EDID OBND MODL DNAM.
    """
    half = int(_CELL_SIZE / 2)
    subs = b''
    subs += pack_string_subrecord('EDID', 'TES4LavaSurface')
    subs += pack_obnd(-half, -half, 0, half, half, 0)
    subs += pack_string_subrecord('MODL', lava_mesh_rel())
    # DNAM: Max Angle (30-degree default, unused for a flat plane) + a null
    # Directional Material.
    subs += pack_subrecord('DNAM', struct.pack('<fI', 0.0, 0))
    return pack_record('STAT', stat_fid, 0, subs)


def build_lava_refr(refr_fid: int, stat_fid: int, x: float, y: float,
                    z: float) -> bytes:
    """A placed reference of the lava STAT at a cell's water height."""
    subs = b''
    subs += pack_subrecord('NAME', struct.pack('<I', stat_fid))
    subs += pack_subrecord('DATA',
                           struct.pack('<ffffff', x, y, z, 0.0, 0.0, 0.0))
    return pack_record('REFR', refr_fid, 0, subs)


class LavaPlanner:
    """Decides which cells get a lava plane, and mints their FormIDs."""

    def __init__(self, by_type: dict, writer):
        """Find the lava waters; index each worldspace and its water, for cells with no XCWT."""
        self.writer = writer
        self.lava_fids = collect_lava_water_fids(by_type)
        self.enabled = bool(self.lava_fids)
        self.scroll = scroll_for(by_type, self.lava_fids) if self.enabled else (0.0, 0.0)
        self.stat_fid = (writer.derive_formid('LAVA_STAT', 'TES4LavaSurface')
                         if self.enabled else 0)
        self.placed = 0
        self._world_water = {}
        self._worlds = {}
        for rec in by_type.get('WRLD', []):
            self._worlds[get_formid(rec, 'FormID')] = rec
            nam2 = get_formid(rec, 'NAM2.Water')
            if nam2:
                self._world_water[get_formid(rec, 'FormID')] = nam2

    def emit_stat(self) -> None:
        """Register the lava STAT once per PLUGIN, not once per planner.

        Interior and exterior cells are built by two different functions, each
        with its own planner, and both need the STAT to exist.  The guard
        lives on the writer so the second one is a no-op instead of emitting a
        duplicate record with the same FormID.
        """
        if not self.enabled:
            return
        if getattr(self.writer, '_lava_stat_emitted', False):
            return
        self.writer._lava_stat_emitted = True
        self.writer.add_record('STAT', build_lava_stat(self.stat_fid))

    def cell_has_lava(self, cell_rec: dict) -> bool:
        """True when the cell's water, its own or its worldspace's, is lava."""
        if not self.enabled:
            return False
        own = get_formid(cell_rec, 'XCWT.Water')
        if own:
            return own in self.lava_fids
        world = get_formid(cell_rec, 'ParentWRLD')
        return self._world_water.get(world) in self.lava_fids

    def refr_for(self, cell_rec: dict):
        """The lava REFR bytes for this cell, or None when it has no lava.

        An exterior plane goes at the cell's grid center; an interior one at
        the cell's own origin, where Oblivion's interior water plane sits.
        """
        if not self.cell_has_lava(cell_rec):
            return None
        cell_fid = get_formid(cell_rec, 'FormID')
        x = get_int(cell_rec, 'XCLC.X', None)
        if x is None:
            wx = wy = 0.0
        else:
            y = get_int(cell_rec, 'XCLC.Y', 0)
            wx = (x + 0.5) * _CELL_SIZE
            wy = (y + 0.5) * _CELL_SIZE
        z = cell_water_height(cell_rec, self._worlds.get(get_formid(cell_rec, 'ParentWRLD')))
        # Keyed on the source cell FormID: authored data, so the id is stable
        # across builds and save games.
        refr_fid = self.writer.derive_formid('LAVA_REFR', cell_fid)
        self.placed += 1
        return build_lava_refr(refr_fid, self.stat_fid, wx, wy, z)
