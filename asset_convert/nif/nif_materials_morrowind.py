"""Havok materials for Morrowind meshes, from their authored texture names.

Morrowind carries no havok data, so every converted mesh used to collide as
stone. The only authored per-surface signal is the diffuse texture NAME, which
`tes4_export.record_types.morrowind_materials` classifies.

Two things make this harder than reading the shape that carries the triangles:

- The material must be sampled BEFORE the version upgrade, which replaces
  NiTexturingProperty with a BSLightingShaderProperty, so it is stashed on the
  root as `_mw_havok_material` and read back when collision is attached.
- A mesh with an authored RootCollisionNode collides with an UNTEXTURED proxy.
  43% of the corpus is that shape, so RENDER geometry is what gets sampled in
  every case; the proxy only decides WHERE collision is, never what it is.

See: docs/commentary/asset_convert_nif.md#morrowind-surface-materials
"""

from pyffi.formats.nif import NifFormat

from asset_convert.nif.shaders import base_texture_path
from tes4_export.record_types.morrowind_materials import material_of

#: TES4 material enum -> Skyrim havok material CRC; a negative key has no TES4 enum value.
_SKY_MATERIAL = {
    0: 3741512247,    # Stone       -> SKY_HAV_MAT_STONE
    1: 3839073443,    # Cloth       -> SKY_HAV_MAT_CLOTH
    2: 3106094762,    # Dirt        -> SKY_HAV_MAT_DIRT
    3: 3739830338,    # Glass       -> SKY_HAV_MAT_GLASS
    4: 1848600814,    # Grass       -> SKY_HAV_MAT_GRASS
    5: 1288358971,    # Metal       -> SKY_HAV_MAT_SOLID_METAL
    6: 2974920155,    # Organic     -> SKY_HAV_MAT_ORGANIC
    7: 591247106,     # Skin        -> SKY_HAV_MAT_SKIN
    8: 1024582599,    # Water       -> SKY_HAV_MAT_WATER
    9: 500811281,     # Wood        -> SKY_HAV_MAT_WOOD
    10: 1570821952,   # Heavy Stone -> SKY_HAV_MAT_HEAVY_STONE
    14: 398949039,    # Snow        -> SKY_HAV_MAT_SNOW
    -1: 3106094762,   # Sand        -> DIRT, the nearest havok material
    -2: 3106094762,   # Mud         -> DIRT
    -3: 3106094762,   # Gravel      -> DIRT
    -4: 1570821952,   # BrokenStone -> HEAVY_STONE
    -5: 3739830338,   # Ice         -> GLASS, what vanilla ice collides as
}

#: SKY_HAV_MAT_STONE, the fallback for a name that classifies to nothing.
STONE = 3741512247

#: Where `sample_materials` stashes its result for `havok_material` to read.
_STASH = '_mw_havok_material'


def _diffuse_name(shape) -> str:
    """The diffuse texture name on a pre-upgrade TES3 shape, or ''."""
    for prop in (getattr(shape, 'properties', None) or []):
        if isinstance(prop, NifFormat.NiTexturingProperty):
            raw = base_texture_path(prop)
            if isinstance(raw, bytes):
                return raw.decode('cp1252', 'replace')
            return str(raw or '')
    return ''


def _triangle_area(data) -> float:
    """Total triangle area of a shape, in its own local units."""
    verts = data.vertices
    total = 0.0
    for a, b, c in data.get_triangles():
        pa, pb, pc = verts[a], verts[b], verts[c]
        ux, uy, uz = pb.x - pa.x, pb.y - pa.y, pb.z - pa.z
        vx, vy, vz = pc.x - pa.x, pc.y - pa.y, pc.z - pa.z
        cx = uy * vz - uz * vy
        cy = uz * vx - ux * vz
        cz = ux * vy - uy * vx
        total += 0.5 * (cx * cx + cy * cy + cz * cz) ** 0.5
    return total


def _root_material(root) -> int:
    """The havok CRC the render geometry under `root` votes for, or 0."""
    weights = {}
    for shape in root.tree():
        if not isinstance(shape, NifFormat.NiTriBasedGeom) or shape.data is None:
            continue
        name = _diffuse_name(shape)
        if not name:
            continue
        material = material_of(name)
        weights[material] = weights.get(material, 0.0) + _triangle_area(shape.data)
    if not weights:
        return 0
    best = max(weights.items(), key=lambda kv: kv[1])[0]
    return _SKY_MATERIAL.get(best, STONE)


def sample_materials(data, stats=None) -> int:
    """Stash each root's havok material, read from its RENDER geometry.

    Must run BEFORE the version upgrade, while NiTexturingProperty still
    exists. Shapes vote by triangle area so a decal cannot outvote a wall.
    Returns how many roots resolved to something other than the fallback.
    """
    resolved = 0
    for root in data.roots:
        if root is None or not hasattr(root, 'tree'):
            continue
        crc = _root_material(root)
        if not crc:
            continue
        setattr(root, _STASH, crc)
        resolved += int(crc != STONE)
    if stats is not None and resolved:
        stats['mw_material_sampled'] = stats.get('mw_material_sampled', 0) + resolved
    return resolved


def havok_material(root) -> int:
    """The havok material sampled for this root, or the stone fallback."""
    return getattr(root, _STASH, STONE)


def carry_havok_material(old_root, new_root) -> None:
    """Move a sampled material onto the root replacing `old_root`."""
    material = getattr(old_root, _STASH, None)
    if material is not None:
        setattr(new_root, _STASH, material)
