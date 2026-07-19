"""Reconstruct missing triangle arrays in NiTriShapeData.

Several vanilla Oblivion grass meshes (GroundCoverMediumGrass01,
GroundCoverLongGrass01, GroundCoverPineappleWeed*, GroundCoverWildPlant*)
ship with ``has_triangles = False``: Num Triangles / Num Triangle Points
are set but the index array itself is absent.  Oblivion's grass renderer
tolerated that; Skyrim's grass planter dereferences the missing data and
CTDs (no crash log) wherever such a grass type spawns.

The geometry is reconstructible: these meshes are grass-blade triangle
lists where every blade uses the same three UV coordinates (base-left,
base-right, tip).  Verts are classified into the three roles by UV; the
role whose vertex count equals Num Triangles anchors one blade each, and
each anchor is paired with the candidate pair from the other two roles
whose midpoint lies closest below/above it (blades are isosceles: the tip
sits over the midpoint of its base).  Winding is chosen to agree with the
stored vertex normals.
"""


class UnreconstructibleGeometry(ValueError):
    """Triangle-less NiTriShapeData whose UV layout doesn't match the
    grass-blade pattern — the file carries NO topology at all (several
    dev-era Oblivion creature meshes: minotaur hair01/hornsa/minotaurold).
    Such a shape cannot render anywhere; callers drop it."""


def _role_key(uv):
    return (round(uv.u, 3), round(uv.v, 3))


def fix_missing_triangles(tri_data):
    # TODO: This should only apply to grass/plant meshes
    """Rebuild tri_data.triangles when has_triangles is unset.

    Returns True if triangles were reconstructed, False if nothing to do.
    Raises ValueError when the mesh doesn't match the reconstructible
    blade-list pattern (caller should surface the file for inspection).
    """
    if not hasattr(tri_data, 'has_triangles'):
        return False
    if tri_data.has_triangles or not tri_data.num_triangles:
        return False

    nt = tri_data.num_triangles
    nv = tri_data.num_vertices
    if not tri_data.num_uv_sets or nv < 3:
        raise UnreconstructibleGeometry(
            'missing triangles and no UV roles to reconstruct from')

    uvs = tri_data.uv_sets[0]
    roles = {}
    for i in range(nv):
        roles.setdefault(_role_key(uvs[i]), []).append(i)
    if len(roles) != 3:
        raise UnreconstructibleGeometry(
            f'missing triangles; expected 3 UV roles, found {len(roles)}')

    verts = tri_data.vertices

    def pos(i):
        v = verts[i]
        return (v.x, v.y, v.z)

    # The tip role is the one sitting highest; the other two are base
    # corners.  Blades are isosceles: the tip sits over the midpoint of
    # its base pair, which is the pairing metric in both anchor cases.
    groups = sorted(roles.values(),
                    key=lambda g: sum(pos(i)[2] for i in g) / len(g))
    base_a, base_b, tips = groups

    def blade_cost(l, r, t):
        lx, ly, _ = pos(l)
        rx, ry, _ = pos(r)
        tx, ty, _ = pos(t)
        return ((lx + rx) / 2 - tx) ** 2 + ((ly + ry) / 2 - ty) ** 2

    tris = []
    if len(tips) == nt:
        # One tip per blade: pick the base pair whose midpoint it tops.
        for t in tips:
            best = min(((blade_cost(l, r, t), l, r)
                        for l in base_a for r in base_b))
            tris.append((t, best[1], best[2]))
    else:
        # Shared tips: anchor on a base role with one vert per blade.
        anchor, other = ((base_a, base_b) if len(base_a) == nt else
                         (base_b, base_a) if len(base_b) == nt else (None, None))
        if anchor is None:
            raise ValueError(f'missing triangles; no UV role has {nt} verts '
                             f'(role sizes {[len(g) for g in groups]})')
        for l in anchor:
            best = min(((blade_cost(l, r, t), r, t)
                        for r in other for t in tips))
            tris.append((l, best[1], best[2]))

    # Winding: agree with stored vertex normals
    normals = tri_data.normals if tri_data.has_normals else None
    fixed = []
    for a, b, c in tris:
        if normals is not None:
            pa, pb, pc = pos(a), pos(b), pos(c)
            e1 = [pb[k] - pa[k] for k in range(3)]
            e2 = [pc[k] - pa[k] for k in range(3)]
            face = (e1[1] * e2[2] - e1[2] * e2[1],
                    e1[2] * e2[0] - e1[0] * e2[2],
                    e1[0] * e2[1] - e1[1] * e2[0])
            n = normals[a]
            if face[0] * n.x + face[1] * n.y + face[2] * n.z < 0:
                a, b, c = a, c, b
        fixed.append((a, b, c))

    tri_data.has_triangles = True
    tri_data.num_triangles = len(fixed)
    tri_data.num_triangle_points = len(fixed) * 3
    tri_data.triangles.update_size()
    for tri, (a, b, c) in zip(tri_data.triangles, fixed):
        tri.v_1, tri.v_2, tri.v_3 = a, b, c
    return True


def clear_match_groups(tri_data):
    """Drop legacy vertex match groups (unused by Skyrim; no vanilla Skyrim
    mesh carries them, and they crash the grass planter).  Returns True if
    any were removed."""
    if not getattr(tri_data, 'num_match_groups', 0):
        return False
    tri_data.num_match_groups = 0
    tri_data.match_groups.update_size()
    return True
