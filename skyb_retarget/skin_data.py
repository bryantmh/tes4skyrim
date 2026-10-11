"""Read a skinned NIF into one vertex/weight table in bind (skeleton) space.

All matrices are PyFFI's row-vector convention (v' = v @ M, translation in
row 3), the convention of `asset_convert.havok.clip_retarget`.
"""
from dataclasses import dataclass, field

import numpy as np

from asset_convert.nif.sse_nif import read_nif
from pyffi.formats.nif import NifFormat


@dataclass
class SkinMesh:
    """Every skinned vertex of a NIF with a weight column per bone name."""
    bones: list = field(default_factory=list)
    verts: np.ndarray = None
    weights: np.ndarray = None

    def column(self, bone: str) -> np.ndarray:
        """Weights of `bone` per vertex (zeros when the mesh lacks it)."""
        if bone not in self.bones:
            return np.zeros(len(self.verts))
        return self.weights[:, self.bones.index(bone)]


def block_name(block) -> str:
    """A block's name as text."""
    name = getattr(block, 'name', b'')
    return name.decode('latin-1') if isinstance(name, bytes) else str(name)


def transform_matrix(t) -> np.ndarray:
    """Row-convention 4x4 of a NiAVObject or NiSkinData transform."""
    t = getattr(t, 'skin_transform', t)
    r = t.rotation
    m = np.eye(4)
    m[:3, :3] = np.array([[r.m_11, r.m_12, r.m_13], [r.m_21, r.m_22, r.m_23],
                          [r.m_31, r.m_32, r.m_33]]) * t.scale
    m[3, :3] = [t.translation.x, t.translation.y, t.translation.z]
    return m


def skinned_shapes(data) -> list:
    """Every NiTriBasedGeom with a skin instance."""
    return [b for b in data.blocks if isinstance(b, NifFormat.NiTriBasedGeom)
            and b.skin_instance is not None]


def shape_weights(shape) -> tuple:
    """(bone names, (V, B) weight matrix) from a shape's NiSkinData."""
    skin = shape.skin_instance
    names = [block_name(b) for b in skin.bones]
    w = np.zeros((shape.data.num_vertices, len(names)))
    for bi, bone in enumerate(skin.data.bone_list):
        for vw in bone.vertex_weights:
            w[vw.index, bi] += vw.weight
    return names, w


def node_worlds(data) -> dict:
    """{node name: row-convention world matrix} for every NiNode in the file."""
    out = {}

    def visit(node, parent):
        """Record `node` and recurse into its NiNode children."""
        world = transform_matrix(node) @ parent
        out[block_name(node)] = world
        for child in getattr(node, 'children', None) or []:
            if isinstance(child, NifFormat.NiNode):
                visit(child, world)

    for root in data.roots:
        if isinstance(root, NifFormat.NiNode):
            visit(root, np.eye(4))
    return out


def shape_bind_verts(shape, worlds: dict) -> np.ndarray:
    """A shape's vertices posed by the file's own bone nodes, weight-blended."""
    v = np.array([[p.x, p.y, p.z] for p in shape.data.vertices])
    vh = np.c_[v, np.ones(len(v))]
    names, w = shape_weights(shape)
    overall = transform_matrix(shape.skin_instance.data)
    out = np.zeros((len(v), 3))
    for bi, bone in enumerate(shape.skin_instance.data.bone_list):
        m = overall @ transform_matrix(bone) @ worlds[names[bi]]
        out += w[:, bi:bi + 1] * (vh @ m)[:, :3]
    return out / np.maximum(w.sum(1, keepdims=True), 1e-9)


def shape_mesh(shape, worlds: dict) -> SkinMesh:
    """One skinned shape as a SkinMesh (bind-space vertices, per-bone weights)."""
    names, w = shape_weights(shape)
    return SkinMesh(bones=names, verts=shape_bind_verts(shape, worlds), weights=w)


def read_skin(path: str) -> SkinMesh:
    """All skinned shapes of `path` merged into one SkinMesh."""
    data = read_nif(path)
    worlds = node_worlds(data)
    parts = [shape_mesh(s, worlds) for s in skinned_shapes(data)]
    bones = list(dict.fromkeys(b for p in parts for b in p.bones))
    weights = np.zeros((sum(len(p.verts) for p in parts), len(bones)))
    row = 0
    for p in parts:
        for i, b in enumerate(p.bones):
            weights[row:row + len(p.verts), bones.index(b)] += p.weights[:, i]
        row += len(p.verts)
    return SkinMesh(bones=bones, verts=np.vstack([p.verts for p in parts]),
                    weights=weights)
