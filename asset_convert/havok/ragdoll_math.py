"""Rotation, bone-world and capsule-inertia math for the ragdoll stage.

Pure geometry: nothing here knows what a ragdoll part or a constraint is.
All matrices are ROW-convention 3x3 (the NIF convention), so a world
transform composes as `R_child @ R_parent` and a point maps as `v @ R + t`.

Lengths are whatever unit the caller passes in; `hkx_ragdoll` works in game
units and scales Oblivion Havok units by `OB_TO_GAME` before calling here.
See: docs/commentary/asset_convert_creature.md#ragdoll-inertia-root-cause
"""

import math

import numpy as np

#: Largest principal-axis ratio Havok's joint solver stays stable under.
MAX_ANISO = 6.5
#: Oblivion Havok units to game units.
OB_TO_GAME = 7.0


def quat_to_mat_row(q):
    """xyzw quat -> row-convention 3x3 (inverse of mat33_to_quat_xyzw)."""
    x, y, z, w = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w)],
        [2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w)],
        [2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y)],
    ])


def mat_row_to_quat(m):
    """Row-convention 3x3 -> xyzw quat (Shepperd)."""
    m00, m01, m02 = m[0]
    m10, m11, m12 = m[1]
    m20, m21, m22 = m[2]
    tr = m00 + m11 + m22
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        w = 0.25 * s
        x = (m12 - m21) / s
        y = (m20 - m02) / s
        z = (m01 - m10) / s
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2
        w = (m12 - m21) / s
        x = 0.25 * s
        y = (m10 + m01) / s
        z = (m20 + m02) / s
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2
        w = (m20 - m02) / s
        x = (m10 + m01) / s
        y = 0.25 * s
        z = (m21 + m12) / s
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2
        w = (m01 - m10) / s
        x = (m20 + m02) / s
        y = (m21 + m12) / s
        z = 0.25 * s
    n = math.sqrt(w * w + x * x + y * y + z * z)
    return (x / n, y / n, z / n, w / n)


def bone_worlds(bones):
    """World (rotation 3x3 row-convention, translation vec3) per anim bone.

    Bones must be parent-before-child, which every hkaSkeleton guarantees.
    """
    worlds = []
    for b in bones:
        R = quat_to_mat_row(b.quat_xyzw) * b.scale
        t = np.array(b.translation, dtype=float)
        if b.parent < 0:
            worlds.append((R, t))
        else:
            Rp, tp = worlds[b.parent]
            worlds.append((R @ Rp, t @ Rp + tp))
    return worlds


def unit(v):
    """`v` normalized, or `v` unchanged when it has no length."""
    v = np.asarray(v, dtype=float)
    return v / (np.linalg.norm(v) or 1.0)


def v4(v, scale=1.0):
    """A pyffi vector's (x, y, z) as a float array, optionally scaled."""
    return np.array([v.x * scale, v.y * scale, v.z * scale], dtype=float)


def capsule_inertia(shape, mass):
    """Principal inertia diagonal (Ixx, Iyy, Izz) of a solid capsule.

    `shape` is (radius, vertexA, vertexB); the result is about the center of
    mass, expressed axis-aligned so the axis holding the segment gets the
    axial moment and the other two the radial one.  Replaces Oblivion's
    authored diagonals, whose anisotropy diverges Havok's solver, and clamps
    the principal-axis ratio to `MAX_ANISO`.
    See: docs/commentary/asset_convert_creature.md#ragdoll-inertia-root-cause
    """
    r, va, vb = (float(shape[0]), np.asarray(shape[1], float),
                 np.asarray(shape[2], float))
    seg = vb - va
    L = float(np.linalg.norm(seg))
    r = max(r, 1e-3)

    v_cyl = math.pi * r * r * L
    v_cap = (4.0 / 3.0) * math.pi * r ** 3
    v_tot = v_cyl + v_cap or 1.0
    m_cyl = mass * v_cyl / v_tot
    m_cap = mass * v_cap / v_tot

    i_axial = 0.5 * m_cyl * r * r + 2.0 * (0.4 * m_cap * r * r)
    i_radial = (m_cyl * (r * r / 4.0 + L * L / 12.0)
                + 2.0 * m_cap * (0.4 * r * r
                                 + 0.5 * (L / 2.0) ** 2 + 0.375 * r * L))

    axis = int(np.argmax(np.abs(seg))) if L > 1e-6 else 2
    diag = [i_radial, i_radial, i_radial]
    diag[axis] = i_axial

    lo = min(diag)
    if lo > 0:
        diag = [min(x, lo * MAX_ANISO) for x in diag]
        diag = [max(x, max(diag) / MAX_ANISO) for x in diag]
    return tuple(max(x, 1e-6) for x in diag)
