"""
The Skyrim skin sections all three games' armor shares, and the split meshes behind them.

Vanilla Skyrim draws the body as one file (torso 32, forearm ring 34, calves
38) and both hands as another (33). Oblivion and Morrowind wear the lower
body and each hand separately, so the body is cut at the waist into torso,
legs (pelvis and thighs become 49) and calves (38), and the hands into left
(33) and right (59). Each section is its own file and addon: an item claiming
an addon's first slot drops the addon whole, and a partition draws only
where its own addon claims the slot.

The waist is the border between Oblivion's upper and lower body fitted onto
the Skyrim body, cut exactly; the hands split by the side of the bones each
triangle is weighted to. Weight variant _1 takes the cut found on _0, so the
pair stays topology-identical.

See: docs/commentary/asset_convert_armor.md#body-slot-layout
"""

import os
from functools import lru_cache
from typing import NamedTuple

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from asset_convert.character.body_wrap import field_path
from asset_convert.character.mesh_cut import LabeledSurface, cut
from asset_convert.character.morrowind_coverage import SKIN_FILES
from asset_convert.character.skyrim_overrides import (SBP_32_BODY, SBP_33_HANDS, SBP_49_LOWER_BODY,
                                                      SBP_59_RIGHT_HAND)
from asset_convert.character.wrap_mesh import (block_name, geom_triangles, geom_world,
                                               iter_skinned_geoms, read_nif, weld_groups)
from asset_convert.sources.skyrim_assets import get_body_nif_bytes

#: Vanilla skin mesh stem -> the kind of file it is split as.
SPLIT_STEMS = {'malebody': 'body', 'femalebody': 'body', 'malehands': 'hands',
               'femalehands': 'hands', 'malehandsargonian': 'hands',
               'femalehandsargonian': 'hands', '1stpersonmalehands': 'hands',
               '1stpersonfemalehands': 'hands', '1stpersonmalehandsargonian': 'hands',
               '1stpersonfemalehandsargonian': 'hands'}

#: Kind -> {file suffix ('' for the stem's own file): the sections it holds}.
SECTIONS = {'body': {'': SKIN_FILES[0][1], 'legs': SKIN_FILES[1][1], 'calves': SKIN_FILES[2][1]},
            'hands': {'': SKIN_FILES[3][1], 'right': SKIN_FILES[4][1]}}

#: Folder, under meshes, the split skin meshes are written to.
SLOT_MESH_DIR = chr(92).join(('actors', 'character', 'character assets', 'tes4'))

#: Weight variants every split skin mesh ships.
WEIGHTS = (0, 1)

#: Skin influences kept per vertex, as vanilla bodies carry.
_BONES_PER_VERTEX = 4

#: Bones per skin partition block when a split file is re-partitioned.
_BONES_PER_PARTITION = 18

#: Per-vertex unit vectors, renormalized after a cut interpolates them.
_UNIT_ATTRS = ('normals', 'tangents', 'bitangents')


class SlotShape(NamedTuple):
    """A skin shape in the shared slot layout.

    `arrays` holds per-vertex 'verts' (shape-local), 'uvs', 'weights'
    (vertex x skin bone) and, when the shape has them, 'normals', 'tangents',
    'bitangents' and 'colors'. `parts` is each triangle's body part.
    """

    shape: object
    bones: list
    arrays: dict
    tris: np.ndarray
    parts: np.ndarray


def _vectors(items, fields) -> np.ndarray:
    """A pyffi vector array as (n, len(fields))."""
    return np.array([[getattr(v, f) for f in fields] for v in items], dtype=np.float64)


def vertex_arrays(shape) -> dict:
    """The per-vertex arrays of a shape, shape-local: 'verts', 'uvs' and those of `_UNIT_ATTRS` and 'colors' it has."""
    d = shape.data
    n = d.num_vertices
    out = {'verts': _vectors(d.vertices, 'xyz'), 'uvs': _vectors(d.uv_sets[0], 'uv')}
    for attr in _UNIT_ATTRS:
        if len(getattr(d, attr, ())) == n and (attr != 'normals' or d.has_normals):
            out[attr] = _vectors(getattr(d, attr), 'xyz')
    if d.has_vertex_colors:
        out['colors'] = _vectors(d.vertex_colors, 'rgba')
    return out


def skin_weights(shape) -> np.ndarray:
    """A skin shape's (vertex x skin bone) weights."""
    sd = shape.skin_instance.data
    weights = np.zeros((shape.data.num_vertices, sd.num_bones))
    for bi in range(sd.num_bones):
        for vw in sd.bone_list[bi].vertex_weights:
            weights[vw.index, bi] += vw.weight
    return weights


def _shape_arrays(shape) -> dict:
    """The per-vertex arrays of a skin shape, its weights included."""
    return {**vertex_arrays(shape), 'weights': skin_weights(shape)}


def _triangle_parts(shape, tris) -> np.ndarray:
    """Each triangle's body part, read from the skin partition holding it."""
    skin = shape.skin_instance
    lookup = {}
    for block, info in zip(skin.skin_partition.skin_partition_blocks, skin.partitions):
        vmap = list(block.vertex_map)
        for t in block.triangles:
            key = tuple(sorted((vmap[t.v_1], vmap[t.v_2], vmap[t.v_3])))
            lookup[key] = info.body_part
    return np.array([lookup.get(tuple(sorted(t)), SBP_32_BODY) for t in tris.tolist()],
                    dtype=np.int64)


def _slot_shape(shape) -> SlotShape:
    """A vanilla skin shape with its own partitions."""
    tris = geom_triangles(shape)
    return SlotShape(shape, [block_name(b) for b in shape.skin_instance.bones],
                     _shape_arrays(shape), tris, _triangle_parts(shape, tris))


@lru_cache(maxsize=2)
def _waist(female: bool):
    """Oblivion's upper and lower body fitted onto the Skyrim body, or None without labels."""
    path = field_path(female)
    if not path.exists():
        return None
    with np.load(path, allow_pickle=False) as z:
        if 'ob_slot' not in z:
            return None
        surface = LabeledSurface.from_vertex_labels(z['dst0'], z['tris'], z['ob_slot'])
    return surface.subset((SBP_32_BODY, SBP_49_LOWER_BODY))


def piece_ids(verts, tris) -> np.ndarray:
    """Per vertex, the connected piece it belongs to, coincident vertices welded."""
    groups = weld_groups(verts)
    edges = groups[tris]
    adjacency = coo_matrix((np.ones(edges.size), (edges.ravel(), edges[:, [1, 2, 0]].ravel())),
                           shape=(groups.max() + 1,) * 2)
    return connected_components(adjacency, directed=False)[1][groups]


def _whole_pieces(verts, tris, field) -> np.ndarray:
    """`field` as +-1 per connected piece: the side most of the piece lies on."""
    piece = piece_ids(verts, tris)
    votes = np.bincount(piece, weights=np.sign(field))
    return np.where(votes[piece] < 0, -1.0, 1.0)


@lru_cache(maxsize=4)
def _waist_cuts(stem: str) -> tuple:
    """Per skin shape of the stem's _0 body, the cut along Oblivion's waist.

    An underwear overlay (a shape whose only partition is the torso) is not
    cut: each piece of it goes whole to the side it mostly lies on, so briefs
    and panties hide with the lower body and a bra stays with the torso.
    """
    waist = _waist(stem.startswith('female'))
    raw = get_body_nif_bytes(f'{stem}_0.nif')
    if waist is None or raw is None:
        return ()
    cuts = []
    for shape, skel in iter_skinned_geoms(read_nif(raw)):
        verts, tris = geom_world(shape, skel)[0], geom_triangles(shape)
        field = waist.field(verts, (SBP_49_LOWER_BODY,))
        if set(_triangle_parts(shape, tris).tolist()) == {SBP_32_BODY}:
            field = _whole_pieces(verts, tris, field)
        cuts.append(cut(tris, field))
    return tuple(cuts)


def _apply_cut(ss: SlotShape, c) -> SlotShape:
    """`ss` with the cut's crossing vertices added and its lower side made lower body."""
    arrays = {k: c.lerp(v) for k, v in ss.arrays.items()}
    for attr in _UNIT_ATTRS:
        if attr in arrays:
            arrays[attr] /= np.maximum(np.linalg.norm(arrays[attr], axis=1, keepdims=True), 1e-12)
    w = arrays['weights']
    w[w < -np.sort(-w, axis=1)[:, [_BONES_PER_VERTEX - 1]]] = 0.0
    arrays['weights'] = w / np.maximum(w.sum(axis=1, keepdims=True), 1e-12)
    parts = ss.parts[c.src]
    parts[c.side & (parts == SBP_32_BODY)] = SBP_49_LOWER_BODY
    return ss._replace(arrays=arrays, tris=c.tris, parts=parts)


def right_triangles(bones, weights, tris) -> np.ndarray:
    """Per triangle: do two of its vertices weigh most on a right-side bone?"""
    right_bone = np.array([' R ' in name for name in bones])
    return right_bone[np.argmax(weights, axis=1)][tris].sum(axis=1) >= 2


def _split_hands(ss: SlotShape) -> SlotShape:
    """`ss` with the triangles weighted to right-side bones made the right hand."""
    parts = ss.parts.copy()
    right = right_triangles(ss.bones, ss.arrays['weights'], ss.tris)
    parts[right & (parts == SBP_33_HANDS)] = SBP_59_RIGHT_HAND
    return ss._replace(parts=parts)


def write_hand_side(source, target, right: bool) -> bool:
    """Write one hand of the two-handed skinned wearable `source` to `target`; False if that hand is empty.

    Each triangle goes to the side of the bones it is weighted to, exactly as
    the split skin hands do; a shape with none of that side's triangles is
    dropped.
    See: docs/commentary/asset_convert_armor.md#split-pair-gauntlets
    """
    side = SBP_59_RIGHT_HAND if right else SBP_33_HANDS
    data = read_nif(source)
    shapes = []
    for shape, _skel in iter_skinned_geoms(data):
        arrays, tris = _shape_arrays(shape), geom_triangles(shape)
        bones = [block_name(b) for b in shape.skin_instance.bones]
        parts = np.where(right_triangles(bones, arrays['weights'], tris),
                         SBP_59_RIGHT_HAND, SBP_33_HANDS)
        shapes.append(SlotShape(shape, bones, arrays, tris, parts))
    if not any((ss.parts == side).any() for ss in shapes):
        return False
    _write_shapes(data, shapes, {side}, target)
    return True


def _stem(basename: str) -> str:
    """`<stem>` of `<stem>_<weight>.nif`."""
    head, _, tail = os.path.splitext(basename.lower())[0].rpartition('_')
    return head if head and tail.isdigit() else os.path.splitext(basename.lower())[0]


def _slotted(basename: str) -> tuple:
    """(a fresh read of a vanilla character asset or None, its skin shapes in the slot layout).

    A body whose waist field is unavailable keeps its vanilla partitions.
    """
    raw = get_body_nif_bytes(basename)
    if raw is None:
        return None, ()
    data = read_nif(raw)
    shapes = [_slot_shape(shape) for shape, _skel in iter_skinned_geoms(data)]
    kind = SPLIT_STEMS.get(_stem(basename))
    if kind == 'hands':
        return data, tuple(_split_hands(ss) for ss in shapes)
    cuts = _waist_cuts(_stem(basename)) if kind == 'body' else ()
    if len(cuts) != len(shapes):
        return data, tuple(shapes)
    return data, tuple(_apply_cut(ss, c) for ss, c in zip(shapes, cuts))


@lru_cache(maxsize=16)
def slot_shapes(basename: str) -> tuple:
    """A vanilla character asset's skin shapes in the slot layout; read-only."""
    return _slotted(basename)[1]


def _set_vectors(items, values, fields) -> None:
    """Write (n, len(fields)) values into a pyffi vector array, resizing it unless it is nested."""
    if hasattr(items, 'update_size'):
        items.update_size()
    for item, row in zip(items, values.tolist()):
        for f, x in zip(fields, row):
            setattr(item, f, x)


def _write_arrays(d, arrays: dict, used) -> None:
    """Fill a shape's NiTriShapeData with the `used` rows of `arrays`."""
    d.num_vertices = len(used)
    _set_vectors(d.vertices, arrays['verts'][used], 'xyz')
    d.uv_sets.update_size()
    _set_vectors(d.uv_sets[0], arrays['uvs'][used], 'uv')
    for attr in _UNIT_ATTRS:
        if attr in arrays:
            _set_vectors(getattr(d, attr), arrays[attr][used], 'xyz')
    if 'colors' in arrays:
        _set_vectors(d.vertex_colors, arrays['colors'][used], 'rgba')


def write_weights(sd, weights) -> None:
    """Rebuild NiSkinData's per-bone vertex weight lists from a (vertex x bone) matrix."""
    for bi in range(sd.num_bones):
        rows = np.flatnonzero(weights[:, bi] > 0.0)
        be = sd.bone_list[bi]
        be.num_vertices = len(rows)
        be.vertex_weights.update_size()
        for vw, row in zip(be.vertex_weights, rows.tolist()):
            vw.index, vw.weight = row, float(weights[row, bi])


def write_geometry(shape, arrays: dict, tris) -> np.ndarray:
    """Rewrite `shape`'s data to hold only `tris` over the rows of `arrays`; the rows kept, in order."""
    used, local = np.unique(tris, return_inverse=True)
    _write_arrays(shape.data, arrays, used)
    shape.data.set_triangles(local.reshape(-1, 3).tolist())
    shape.data.update_center_radius()
    return used


def _write_section(ss: SlotShape, sections) -> bool:
    """Rewrite `ss.shape` to hold only the triangles in `sections`; False if none are.

    A dismember skin's partitions are rebuilt with each triangle's body part,
    an Oblivion skin's plainly; tangents the data block does not hold are
    regenerated where the shape keeps them.
    """
    keep = np.isin(ss.parts, list(sections))
    if not keep.any():
        return False
    shape, skin = ss.shape, ss.shape.skin_instance
    used = write_geometry(shape, ss.arrays, ss.tris[keep])
    write_weights(skin.data, ss.arrays['weights'][used])
    skin.skin_partition = None
    dismember = hasattr(skin, 'partitions')
    shape.update_skin_partition(maxbonesperpartition=_BONES_PER_PARTITION,
                                maxbonespervertex=_BONES_PER_VERTEX, stripify=False,
                                trianglepartmap=ss.parts[keep].tolist() if dismember else None)
    for part in getattr(skin, 'partitions', ()):
        part.part_flag.pf_editor_visible = 1
    if 'tangents' not in ss.arrays:
        shape.update_tangent_space()
    return True


def _detach(data, shape) -> None:
    """Remove `shape` from its parent node's children."""
    for root in data.roots:
        for node in root.tree():
            children = list(getattr(node, 'children', ()))
            if shape in children:
                children.remove(shape)
                set_children(node, children)


def set_children(node, children) -> None:
    """Make `children` the whole of `node`'s child list."""
    node.num_children = len(children)
    node.children.update_size()
    for i, child in enumerate(children):
        node.children[i] = child


def _write_file(source: str, target, sections) -> bool:
    """Write the `sections` of a vanilla character asset to `target`; False if it is missing."""
    data, shapes = _slotted(source)
    if data is None:
        return False
    _write_shapes(data, shapes, sections, target)
    return True


def _write_shapes(data, shapes, sections, target) -> None:
    """Write `data` to `target` with each of `shapes` cut to its `sections`, empty ones removed."""
    for ss in shapes:
        if not _write_section(ss, sections):
            _detach(data, ss.shape)
    os.makedirs(os.path.dirname(str(target)), exist_ok=True)
    with open(target, 'wb') as f:
        data.write(f)


def split_mesh_path(stem: str, weight: int, suffix: str) -> str:
    """Meshes-relative path of a stem's split file `suffix` ('' for its own file)."""
    return SLOT_MESH_DIR + chr(92) + f'{stem}{suffix}_{weight}.nif'


def write_split_meshes(meshes_root) -> list:
    """Write every split skin mesh under `meshes_root`; the meshes-relative paths written."""
    written = []
    for stem, kind in SPLIT_STEMS.items():
        for weight in WEIGHTS:
            for suffix, sections in SECTIONS[kind].items():
                rel = split_mesh_path(stem, weight, suffix)
                target = os.path.join(meshes_root, *rel.split(chr(92)))
                if _write_file(f'{stem}_{weight}.nif', target, sections):
                    written.append(rel)
    return written
