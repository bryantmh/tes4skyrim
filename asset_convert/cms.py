"""bhkCompressedMeshShapeData decoding (Skyrim collision).

Decodes CMS chunk/big-tri data into world (havok-unit) triangles paired with
the engine shape keys, replicating hkpCompressedMeshShape::getChildShape.

Shape-key layout (validated against vanilla Skyrim meshes: the walked MOPP
terminal key set exactly equals the predicted key set):

    key = (chunk_index + 1) << bits_per_w_index      (chunk triangles)
        | winding          << bits_per_index
        | first_index_offset

    key = big_tri_index                              (big triangles, part 0)

Within a chunk, triangles enumerate strips first (each strip of length L
yields L-2 sliding-window triangles at offsets base..base+L-3, winding =
window ordinal parity), then the remaining indices as independent triples
(winding 0) at stride 3.

Chunk vertex decode (per NifSkope drawCMS and Havok):
    v = chunk.translation + transform.translation + u16_offsets / 1000
    rotated by the chunk transform rotation.

PyFFI 2.2.3 field-name quirks: chunk welding array is `indices_2`
(NumWeldingInfo/WeldingInfo in newer nif.xml); big-tri welding is
`unknown_short_1`.
"""


def _quat_rotate(q, v):
    """Rotate vector v by quaternion q=(x,y,z,w)."""
    x, y, z, w = q
    vx, vy, vz = v
    # t = 2 * cross(q.xyz, v)
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    # v' = v + w*t + cross(q.xyz, t)
    return (
        vx + w * tx + (y * tz - z * ty),
        vy + w * ty + (z * tx - x * tz),
        vz + w * tz + (x * ty - y * tx),
    )


def _is_identity_rot(q, eps=1e-6):
    x, y, z, w = q
    return abs(x) < eps and abs(y) < eps and abs(z) < eps and abs(abs(w) - 1.0) < eps


def decode_cms(cms_data):
    """Decode a bhkCompressedMeshShapeData block (PyFFI object).

    Returns a list of (shape_key, (v0, v1, v2)) with vertices as xyz tuples
    in havok units, ordered big tris first, then chunks in order.
    """
    bpw = cms_data.bits_per_w_index
    bpi = cms_data.bits_per_index
    out = []

    big_verts = [(v.x, v.y, v.z) for v in cms_data.big_verts]
    for bi in range(cms_data.num_big_tris):
        bt = cms_data.big_tris[bi]
        tri = (big_verts[bt.triangle_1], big_verts[bt.triangle_2],
               big_verts[bt.triangle_3])
        out.append((bi, tri))

    transforms = []
    for ti in range(cms_data.num_transforms):
        t = cms_data.chunk_transforms[ti]
        transforms.append(((t.translation.x, t.translation.y, t.translation.z),
                           (t.rotation.x, t.rotation.y, t.rotation.z,
                            t.rotation.w)))

    for ci in range(cms_data.num_chunks):
        ch = cms_data.chunks[ci]
        t_trans, t_rot = transforms[ch.transform_index]
        base = (ch.translation.x + t_trans[0],
                ch.translation.y + t_trans[1],
                ch.translation.z + t_trans[2])
        offs = list(ch.vertices)
        verts = []
        rotate = not _is_identity_rot(t_rot)
        for n in range(ch.num_vertices // 3):
            v = (base[0] + offs[3 * n] / 1000.0,
                 base[1] + offs[3 * n + 1] / 1000.0,
                 base[2] + offs[3 * n + 2] / 1000.0)
            if rotate:
                v = _quat_rotate(t_rot, v)
            verts.append(v)

        indices = list(ch.indices)
        strips = list(ch.strips)
        part = (ci + 1) << bpw
        offset = 0
        for slen in strips:
            for j in range(slen - 2):
                a, b, c = (indices[offset + j], indices[offset + j + 1],
                           indices[offset + j + 2])
                key = part | ((j & 1) << bpi) | (offset + j)
                out.append((key, (verts[a], verts[b], verts[c])))
            offset += slen
        while offset + 2 < ch.num_indices:
            a, b, c = indices[offset], indices[offset + 1], indices[offset + 2]
            key = part | offset
            out.append((key, (verts[a], verts[b], verts[c])))
            offset += 3

    return out


def predict_keys(cms_data):
    """Set of engine shape keys for a bhkCompressedMeshShapeData block."""
    return {key for key, _tri in decode_cms(cms_data)}
