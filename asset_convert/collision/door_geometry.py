"""A door model's doorway, measured off its ORIGINAL NIF at the CLOSED pose.

`scan_door_axes` (collision_extract) writes the result to
door_panel_axis_cache.json; the navmesh sizes, orients and places every Door
Triangle from it.  Also home to the small NIF transform helpers both modules use.

See: docs/commentary/tes5_import_navmesh.md#door-closed-pose-cache
"""

import math

#: Node type holding a TES3 model's collision geometry, never the visible door.
_COLLISION_ROOT = 'RootCollisionNode'

#: Gap, in world units, two shape boxes may leave and still count as one door; stray far-off pieces exceed it.
_TOUCH_SLACK = 64.0

#: Identity rotation, the walk's starting frame.
_IDENT = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]


def pal_name(sp, off):
    """Resolve a NiStringPalette offset to a node name, or None."""
    if sp is None or off is None or off < 0:
        return None
    try:
        pal = sp.palette.palette
    except AttributeError:
        return None
    if isinstance(pal, str):
        pal = pal.encode('latin1')
    end = pal.find(b'\x00', off)
    return pal[off:end if end >= 0 else None].decode('ascii', 'replace')


def mat_mul(A, B):
    """The 3x3 matrix product A·B."""
    return [[sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)]


def quat_mat(w, x, y, z):
    """The rotation matrix of quaternion (w, x, y, z)."""
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]


def _euler_xyz_mat(ax, ay, az):
    """The rotation matrix of XYZ Euler angles (radians), applied X then Y then Z."""
    cx, sx = math.cos(ax), math.sin(ax)
    cy, sy = math.cos(ay), math.sin(ay)
    cz, sz = math.cos(az), math.sin(az)
    Rx = [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]
    Ry = [[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]]
    Rz = [[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]]
    return mat_mul(mat_mul(Rx, Ry), Rz)


def _close_sequence(data):
    """The NIF's 'Close' NiControllerSequence, or None."""
    for b in data.blocks:
        if type(b).__name__ != 'NiControllerSequence':
            continue
        nm = b.name
        if isinstance(nm, bytes):
            nm = nm.decode('ascii', 'replace')
        if nm.lower() == 'close':
            return b
    return None


def _final_key_rotation(d):
    """The rotation of a keyframe data block's LAST key, or None when it has no rotation keys.

    An all-zero final XYZ rotation is still a closed pose: key data existing is
    what marks the node as the moving door.
    """
    if int(getattr(d, 'rotation_type', 0) or 0) == 4:
        groups = [getattr(g, 'keys', None) or [] for g in d.xyz_rotations]
        if not any(len(ks) for ks in groups):
            return None
        return _euler_xyz_mat(*[float(ks[-1].value) if len(ks) else 0.0 for ks in groups])
    qk = getattr(d, 'quaternion_keys', None) or []
    if not len(qk):
        return None
    q = qk[-1].value
    return quat_mat(q.w, q.x, q.y, q.z)


def _interp_pose(itp):
    """(R, T, keyed) of one interpolator's final pose; R or T is None when it sets neither."""
    R = T = None
    keyed = False
    dt = getattr(itp, 'translation', None)
    if dt is not None and abs(dt.x) < 1e30:
        T = [dt.x, dt.y, dt.z]
    dr = getattr(itp, 'rotation', None)
    if dr is not None and abs(getattr(dr, 'w', 3e38)) < 1e30:
        R = quat_mat(dr.w, dr.x, dr.y, dr.z)
    d = getattr(itp, 'data', None)
    if d is not None:
        rot = _final_key_rotation(d)
        if rot is not None:
            R, keyed = rot, True
        tks = getattr(d.translations, 'keys', None) or []
        if len(tks):
            v = tks[-1].value
            T, keyed = [v.x, v.y, v.z], True
    return R, T, keyed


def _close_pose_overrides(data):
    """{node name: (R|None, T|None, keyed)}: each node's local transform in the CLOSED pose.

    Read off the 'Close' sequence's final key values, else the interpolator's
    own finite transform; `keyed` marks the moving leaf, not a retargeted frame.
    """
    seq = _close_sequence(data)
    out = {}
    for cb in (getattr(seq, 'controlled_blocks', None) or []) if seq is not None else ():
        nn = pal_name(getattr(cb, 'string_palette', None),
                      getattr(cb, 'node_name_offset', None))
        itp = getattr(cb, 'interpolator', None)
        if not nn or itp is None:
            continue
        R, T, keyed = _interp_pose(itp)
        if R is not None or T is not None:
            out[nn] = (R, T, keyed)
    return out


def _node_local(node, o):
    """(local rotation, local translation) of `node`; the Close-pose override `o` wins."""
    if o and o[0] is not None:
        lr = o[0]
    else:
        m = getattr(node, 'rotation', None)
        lr = ([[m.m_11, m.m_12, m.m_13], [m.m_21, m.m_22, m.m_23],
               [m.m_31, m.m_32, m.m_33]] if m is not None else _IDENT)
    if o and o[1] is not None:
        return lr, o[1]
    tr = getattr(node, 'translation', None)
    return lr, ([tr.x, tr.y, tr.z] if tr is not None else [0.0, 0.0, 0.0])


def _shape_box(node, Rw, Tw, sw):
    """(lo, hi) placed bbox of a NiTriShape/NiTriStrips with vertices, else None."""
    if type(node).__name__ not in ('NiTriShape', 'NiTriStrips') or getattr(node, 'data', None) is None:
        return None
    vs = node.data.vertices
    if not len(vs):
        return None
    pts = [[Rw[i][0] * v.x * sw + Rw[i][1] * v.y * sw + Rw[i][2] * v.z * sw + Tw[i]
            for i in range(3)] for v in vs]
    return [min(p[i] for p in pts) for i in range(3)], [max(p[i] for p in pts) for i in range(3)]


def _door_shapes(node, frame, keyed, ovr, out):
    """Append (keyed, lo, hi) for each render shape under `node`; `frame` is the parent's (R, T, s).

    A _COLLISION_ROOT subtree is skipped: collision geometry, not the door.
    """
    if type(node).__name__ == _COLLISION_ROOT:
        return
    R, T, s = frame
    name = getattr(node, 'name', b'')
    if isinstance(name, bytes):
        name = name.decode('ascii', 'replace')
    o = ovr.get(name)
    keyed = keyed or bool(o and o[2])
    lr, lt = _node_local(node, o)
    Tw = [R[i][0] * lt[0] * s + R[i][1] * lt[1] * s + R[i][2] * lt[2] * s + T[i]
          for i in range(3)]
    Rw, sw = mat_mul(R, lr), s * (getattr(node, 'scale', 1.0) or 1.0)
    box = _shape_box(node, Rw, Tw, sw)
    if box is not None:
        out.append((keyed, box[0], box[1]))
    for ch in (getattr(node, 'children', None) or []):
        if ch is not None:
            _door_shapes(ch, (Rw, Tw, sw), keyed, ovr, out)


def _touch(a, b) -> bool:
    """True when two (keyed, lo, hi) shape boxes overlap or meet within _TOUCH_SLACK."""
    return all(a[1][i] <= b[2][i] + _TOUCH_SLACK and b[1][i] <= a[2][i] + _TOUCH_SLACK
               for i in range(3))


def _origin_gap(sh) -> float:
    """Distance from the model origin (the REFR's placement) to a shape's box."""
    return math.sqrt(sum(max(sh[1][i], 0.0, -sh[2][i]) ** 2 for i in range(3)))


def _attached(shapes) -> list:
    """The shapes joined, box to touching box, to the one nearest the model origin; stray pieces drop."""
    anchor = min(shapes, key=_origin_gap)
    keep = [anchor]
    rest = [sh for sh in shapes if sh is not anchor]
    grew = True
    while grew:
        joined = [sh for sh in rest if any(_touch(sh, k) for k in keep)]
        keep += joined
        rest = [sh for sh in rest if sh not in joined]
        grew = bool(joined)
    return keep


def door_closed_geometry(data):
    """Doorway as ('X'|'Y', width, center_x, center_y, z_min) in world units, or None.

    The union bbox of the shapes under the Close sequence's KEYED nodes at
    their final pose; with no keyed node, of the render shapes attached to the
    one nearest the model origin.  None when that union is thin in Z: a trapdoor or hatch, which
    swings about a horizontal axis and has no vertical threshold line.

    See: docs/commentary/tes5_import_navmesh.md#door-closed-pose-cache
    """
    ovr = _close_pose_overrides(data)
    shapes = []
    for r in data.roots:
        try:
            _door_shapes(r, (_IDENT, [0.0, 0.0, 0.0], 1.0), False, ovr, shapes)
        except Exception:
            continue
    if not shapes:
        return None
    pick = [sh for sh in shapes if sh[0]] or _attached(shapes)
    lo = [min(sh[1][i] for sh in pick) for i in range(3)]
    hi = [max(sh[2][i] for sh in pick) for i in range(3)]
    ex, ey, ez = hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]
    if max(ex, ey) <= 1e-9 or ez < max(ex, ey) * 0.5:
        return None
    return ('Y' if ey > ex else 'X', max(ex, ey),
            0.5 * (lo[0] + hi[0]), 0.5 * (lo[1] + hi[1]), lo[2])
