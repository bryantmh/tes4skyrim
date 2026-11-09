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
import struct


def f32(value):
    """`value` rounded to the float32 precision a NIF float field stores."""
    return struct.unpack('<f', struct.pack('<f', value))[0]


#: The CMS `error` field (one 0.001 hu quantization step) as the float32 the engine multiplies by.
ERROR_F32 = f32(0.001)


def chunk_vertex(base, quant):
    """A chunk vertex as Havok decodes it: float32(q) * error + base, in float32."""
    return tuple(f32(f32(q * ERROR_F32) + f32(base[i])) for i, q in enumerate(quant))


def _quat_rotate(q, v):
    """Rotate vector v by quaternion q=(x,y,z,w): t = 2 cross(q.xyz, v), v' = v + w t + cross(q.xyz, t)."""
    x, y, z, w = q
    vx, vy, vz = v
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (
        vx + w * tx + (y * tz - z * ty),
        vy + w * ty + (z * tx - x * tz),
        vz + w * tz + (x * ty - y * tx),
    )


def _is_identity_rot(q, eps=1e-6):
    x, y, z, w = q
    return abs(x) < eps and abs(y) < eps and abs(z) < eps and abs(abs(w) - 1.0) < eps


def decode_cms(cms_data, engine=False):
    """Decode a bhkCompressedMeshShapeData block (PyFFI object).

    Returns a list of (shape_key, (v0, v1, v2)) with vertices as xyz tuples
    in havok units, ordered big tris first, then chunks in order. `engine`
    decodes unrotated chunk vertices in the engine's float32 steps.
    """
    big_verts = [(v.x, v.y, v.z) for v in cms_data.big_verts]
    out = []
    for bi in range(cms_data.num_big_tris):
        bt = cms_data.big_tris[bi]
        tri = (big_verts[bt.triangle_1], big_verts[bt.triangle_2],
               big_verts[bt.triangle_3])
        out.append((bi, tri))
    for ci in range(cms_data.num_chunks):
        ch = cms_data.chunks[ci]
        t = cms_data.chunk_transforms[ch.transform_index]
        verts = _chunk_verts(ch, t, engine)
        part = (ci + 1) << cms_data.bits_per_w_index
        out += [(part | key, tuple(verts[i] for i in idx))
                for key, idx in _chunk_triples(ch, cms_data.bits_per_index)]
    return out


def _chunk_verts(ch, t, engine):
    """A chunk's vertices in havok units, moved by its transform `t`."""
    t_rot = (t.rotation.x, t.rotation.y, t.rotation.z, t.rotation.w)
    base = (ch.translation.x + t.translation.x,
            ch.translation.y + t.translation.y,
            ch.translation.z + t.translation.z)
    offs = list(ch.vertices)
    rotate = not _is_identity_rot(t_rot)
    verts = []
    for n in range(ch.num_vertices // 3):
        quant = offs[3 * n:3 * n + 3]
        v = (chunk_vertex(base, quant) if engine and not rotate
             else tuple(base[i] + quant[i] / 1000.0 for i in range(3)))
        verts.append(_quat_rotate(t_rot, v) if rotate else v)
    return verts


def _chunk_triples(ch, bpi):
    """(key within the chunk, vertex index triple) per triangle: strips, then free triples."""
    indices = list(ch.indices)
    out = []
    offset = 0
    for slen in list(ch.strips):
        for j in range(slen - 2):
            out.append((((j & 1) << bpi) | (offset + j), indices[offset + j:offset + j + 3]))
        offset += slen
    while offset + 2 < ch.num_indices:
        out.append((offset, indices[offset:offset + 3]))
        offset += 3
    return out


def decode_cms_materials(cms_data):
    """(triangle, material CRC) per triangle, in `decode_cms` order."""
    palette = [m.material for m in cms_data.chunk_materials]
    bpw = cms_data.bits_per_w_index
    out = []
    for key, tri in decode_cms(cms_data):
        chunk = key >> bpw
        idx = (cms_data.big_tris[key].unknown_int_1 if chunk == 0
               else cms_data.chunks[chunk - 1].material_index)
        out.append((tri, palette[idx]))
    return out


def predict_keys(cms_data):
    """Set of engine shape keys for a bhkCompressedMeshShapeData block."""
    return {key for key, _tri in decode_cms(cms_data)}
