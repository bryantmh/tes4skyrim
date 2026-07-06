"""Procedural tree geometry from parsed SpeedTree .spt parameters.

Reimplements the SpeedTreeCAD/RT generation model closely enough to
reproduce each Oblivion tree's silhouette, proportions, and textures:

  - Recursive stem levels: trunk (level 0) -> branch levels -> leaves (last).
  - Children spawn along the parent in the [child_first, child_last] window;
    count = child_freq * parent_stored_length (empirically verified:
    deadbush 250*0.05=12 stems, oak 80*0.6=48 branches).
  - Shape curves (length/radius/start-angle/gravity) evaluate at the child's
    position along its parent; profile curves (radius/gravity/disturbance)
    evaluate along the stem's own length.
  - Scale: world_units = stored_value * Size * 10.  Verified against the
    TREE records' billboard heights (median predicted/actual = 1.09) and
    leaf sizes (section 4006 = 4005 * Size, rendered at ~10x).
  - Start angle: degrees away from the parent axis (0 = parallel,
    90 = perpendicular; trunk uses -90 = vertical).
  - Gravity: branches bend toward -Z (weeping willow = 2..4, oak = 0.1..0.25);
    the trunk instead straightens toward +Z (Camoran paradise trunks have
    gravity 0 + high disturbance = wandering).
  - Disturbance: random growth-direction wander, degrees (variance field),
    weighted by its profile curve along the stem.
  - Flares: azimuthal root swell at the trunk base.

Output is plain numpy arrays grouped into a bark mesh plus one leaf-card
mesh per leaf texture, ready for the NIF builder.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .spt_parser import SptTree, LevelParams

# world units per (stored_value * Size)
WORLD_SCALE = 10.0

# Gravity semantics (validated against Oblivion's own billboard renders in
# textures/trees/billboards/ — the engine's actual output): the value maps
# to a TARGET PITCH the stem bends toward along its length,
#
#     theta_target = 90 deg - |g - 1| * 90 deg      (g > 0)
#
# i.e. g=1 points straight up (every normal trunk stores 1 = stay vertical),
# g=0.2..0.4 settles just above horizontal (oak/cottonwood boughs), and
# values past 1 WRAP OVER the top: g=2 back to horizontal, g=3 straight
# DOWN.  g=0 means no influence (redwood).
#
# How fast a stem approaches the target is scaled by its FLEXIBILITY curve
# (6002) — the second key: weeping willow branches store gravity 2..4 but
# flexibility 0 (they hold their start angles; the round crown and hanging
# leaf curtains make the weeping look), while cottonwood limbs (flex
# 0.4..0.6, gravity 0.2..0.4) bow outward along their length.
GRAVITY_RESPONSE = 1.2          # approach rate at flexibility = 1

# stems at least this thick (world units, base radius) contribute their
# tube triangles to the exact-mesh collision shape
COLLISION_MIN_RADIUS = 5.0

# geometry budgets (SpeedTree LOD0 counts can be enormous; these caps keep
# tri counts near vanilla Skyrim tree levels while preserving the look)
MAX_STEMS_PER_LEVEL = {1: 64, 2: 260, 3: 320, 4: 320}
MAX_LEAVES = 900
MIN_STEM_WORLD_LEN = 2.0        # skip micro-stems
TUBE_MIN_RADIUS = 0.35          # below this, a stem renders no tube (world units)

# tessellation caps (cross-section sides x length rings)
_CROSS_CAP = {0: 12, 1: 6, 2: 4}
_RING_CAP = {0: 12, 1: 6, 2: 3}


@dataclass
class Stem:
    level: int
    points: np.ndarray            # (n+1, 3) centerline
    radii: np.ndarray             # (n+1,) radius at each ring
    length: float                 # world length
    stored_length: float          # pre-scale length (drives child counts)
    parent_x: float = 0.0         # position on parent (0..1)


@dataclass
class TreeGeometry:
    bark_verts: np.ndarray = None
    bark_normals: np.ndarray = None
    bark_uvs: np.ndarray = None
    bark_colors: np.ndarray = None      # float RGBA, alpha = wind weight
    bark_tris: np.ndarray = None
    # leaves grouped by texture: list of dicts with verts/normals/uvs/colors/tris
    leaf_groups: list = field(default_factory=list)
    trunk_capsule: tuple = None         # (p0, p1, radius) world units (fallback)
    collision_verts: list = field(default_factory=list)  # (N,3) arrays, world
    collision_tris: list = field(default_factory=list)   # (M,3) index arrays
    height: float = 0.0
    radius: float = 0.0                 # horizontal extent
    n_leaves: int = 0
    n_stems: int = 0


# ---------------------------------------------------------------------------
# direction helpers
# ---------------------------------------------------------------------------

def _ortho_frame(d: np.ndarray):
    """Two unit vectors perpendicular to d."""
    a = np.array([0.0, 0.0, 1.0]) if abs(d[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    n1 = np.cross(d, a)
    n1 /= (np.linalg.norm(n1) + 1e-12)
    n2 = np.cross(d, n1)
    return n1, n2


def _cull_isolated_leaves(leaf_positions, radius, min_neighbours=2):
    """Drop leaf attachments that have fewer than `min_neighbours` other
    leaves within `radius` — those render as floating clumps detached from
    the main foliage mass.
    """
    if len(leaf_positions) <= min_neighbours + 1:
        return leaf_positions
    pts = np.array([p for p, _, _ in leaf_positions], dtype=np.float64)
    try:
        from scipy.spatial import cKDTree
        tree = cKDTree(pts)
        counts = tree.query_ball_point(pts, radius, return_length=True)
        keep = counts > min_neighbours    # >self means >=min_neighbours others
    except Exception:
        # O(n^2) fallback (n is small — hundreds)
        keep = np.ones(len(pts), bool)
        for i in range(len(pts)):
            d = np.linalg.norm(pts - pts[i], axis=1)
            if int((d < radius).sum()) <= min_neighbours:
                keep[i] = False
    if keep.all():
        return leaf_positions
    return [lp for lp, k in zip(leaf_positions, keep) if k]


def _thin_keep_all_branches(attach, budget, rng):
    """Down-sample leaf attachment points to `budget` while guaranteeing
    every branch keeps at least one point (no bare branch).

    attach: list of (pos, tan, x, branch_idx).  Returns a thinned list.
    Each branch is allotted a share of the budget proportional to its point
    count (>=1), then thinned within itself.
    """
    from collections import defaultdict
    by_branch = defaultdict(list)
    for a in attach:
        by_branch[a[3]].append(a)
    n_branches = len(by_branch)
    if budget <= n_branches:
        # one point per branch (the branch's midpoint-ish first point)
        return [pts[len(pts) // 2] for pts in by_branch.values()][:budget]
    total = len(attach)
    kept = []
    for pts in by_branch.values():
        share = max(1, round(budget * len(pts) / total))
        if share >= len(pts):
            kept.extend(pts)
        else:
            idx = rng.choice(len(pts), size=share, replace=False)
            kept.extend(pts[i] for i in sorted(idx))
    # if rounding overshot the budget, trim extras (keeping >=1 per branch is
    # already satisfied; drop from the largest contributors)
    if len(kept) > budget:
        drop = len(kept) - budget
        # drop random surplus but never a branch's last point
        counts = defaultdict(int)
        for a in kept:
            counts[a[3]] += 1
        droppable = [i for i, a in enumerate(kept) if counts[a[3]] > 1]
        rng.shuffle(droppable)
        remove = set()
        for i in droppable:
            if drop <= 0:
                break
            if counts[kept[i][3]] > 1:
                remove.add(i)
                counts[kept[i][3]] -= 1
                drop -= 1
        kept = [a for i, a in enumerate(kept) if i not in remove]
    return kept


def _rotate_toward(d: np.ndarray, target: np.ndarray, angle_rad: float) -> np.ndarray:
    """Rotate unit vector d toward unit vector target by angle_rad (capped)."""
    c = float(np.clip(np.dot(d, target), -1.0, 1.0))
    total = math.acos(c)
    if total < 1e-6 or angle_rad <= 0.0:
        return d
    t = min(1.0, angle_rad / total)
    # slerp
    s = math.sin(total)
    out = (math.sin((1 - t) * total) * d + math.sin(t * total) * target) / s
    return out / (np.linalg.norm(out) + 1e-12)


def _perturb(d: np.ndarray, angle_rad: float, rng: np.random.Generator) -> np.ndarray:
    """Rotate d by angle_rad about a random perpendicular axis."""
    if angle_rad <= 0.0:
        return d
    n1, n2 = _ortho_frame(d)
    az = rng.uniform(0.0, 2.0 * math.pi)
    axis_dir = math.cos(az) * n1 + math.sin(az) * n2
    out = math.cos(angle_rad) * d + math.sin(angle_rad) * axis_dir
    return out / (np.linalg.norm(out) + 1e-12)


# ---------------------------------------------------------------------------
# stem centerline growth
# ---------------------------------------------------------------------------

def _grow_stem(origin: np.ndarray, direction: np.ndarray, world_len: float,
               base_radius: float, lv: LevelParams, grav_value: float,
               flex_value: float, n_rings: int,
               rng: np.random.Generator) -> tuple:
    """Integrate a stem centerline with disturbance + gravity.

    Returns (points (n_rings+1,3), radii (n_rings+1,), tangents (n_rings+1,3)).
    """
    pts = np.empty((n_rings + 1, 3))
    tans = np.empty((n_rings + 1, 3))
    radii = np.empty(n_rings + 1)
    d = direction.copy()
    p = origin.copy()
    pts[0] = p
    tans[0] = d
    seg = world_len / n_rings
    dist_var = math.radians(lv.disturbance.variance)
    up = np.array([0.0, 0.0, 1.0])

    g = float(grav_value)
    # target pitch: g=1 -> +90 (up), wraps over past 1 (g=3 -> -90, down)
    if g > 0.0:
        target_pitch = math.radians(90.0 - abs(g - 1.0) * 90.0)
    else:
        target_pitch = math.radians(max(-90.0, g * 90.0))
    target_pitch = float(np.clip(target_pitch, -math.pi / 2, math.pi / 2))

    for i in range(1, n_rings + 1):
        t = i / n_rings
        # disturbance: random wander, weighted by its profile along the stem
        if dist_var > 0.0:
            w = float(lv.disturbance.eval(t))
            jitter = rng.uniform(-1.0, 1.0) * dist_var * w / n_rings * 2.0
            d = _perturb(d, abs(jitter), rng)
        if g != 0.0 and flex_value > 0.0:
            # bend toward the target-pitch direction, keeping the stem's
            # current azimuth; a purely vertical stem has no azimuth and
            # stays put until disturbance gives it one (paradise trunks).
            hx, hy = float(d[0]), float(d[1])
            hlen = math.hypot(hx, hy)
            if hlen > 1e-6:
                target = np.array([hx / hlen * math.cos(target_pitch),
                                   hy / hlen * math.cos(target_pitch),
                                   math.sin(target_pitch)])
                gp = float(lv.gravity_profile.eval(t)) if lv.gravity_profile else t
                gap = math.acos(float(np.clip(np.dot(d, target), -1.0, 1.0)))
                frac = min(1.0, GRAVITY_RESPONSE * flex_value * gp / n_rings)
                d = _rotate_toward(d, target, gap * frac)
        p = p + d * seg
        pts[i] = p
        tans[i] = d
        # radius taper from the profile curve
        prof = float(lv.radius_profile.eval(t)) if lv.radius_profile else (1.0 - t)
        radii[i] = max(base_radius * max(prof, 0.0), 0.0)
    prof0 = float(lv.radius_profile.eval(0.0)) if lv.radius_profile else 1.0
    radii[0] = base_radius * max(prof0, 0.02)
    # monotone-ish taper: never widen much going up (avoids bulges from curves)
    for i in range(1, n_rings + 1):
        radii[i] = min(radii[i], radii[i - 1] * 1.05)
    return pts, radii, tans


# ---------------------------------------------------------------------------
# tube tessellation
# ---------------------------------------------------------------------------

def _tube_mesh(pts, radii, tans, n_az, lv: LevelParams, tree: SptTree,
               is_trunk: bool, rng, flare_phases=None):
    """Build a tapered tube along the centerline.  Returns v, n, uv, tri."""
    n_rings = len(pts) - 1
    verts, norms, uvs = [], [], []
    total_len = float(np.sum(np.linalg.norm(np.diff(pts, axis=0), axis=1))) or 1.0
    v_tile = lv.v_tile or 1.0
    u_tile = lv.u_tile or 1.0

    run = 0.0
    for i in range(n_rings + 1):
        if i > 0:
            run += float(np.linalg.norm(pts[i] - pts[i - 1]))
        t = run / total_len
        n1, n2 = _ortho_frame(tans[i])
        r = radii[i]
        for a in range(n_az):
            ang = 2.0 * math.pi * a / n_az
            rr = r
            if is_trunk and flare_phases is not None and lv.flare_length_dist > 0:
                rr *= _flare_multiplier(ang, t, lv, flare_phases)
            radial = math.cos(ang) * n1 + math.sin(ang) * n2
            verts.append(pts[i] + radial * rr)
            norms.append(radial)
            uvs.append((a / n_az * u_tile, (1.0 - t) * v_tile))

    # winding: front face outward (geometric normal aligned with the radial
    # vertex normals — matches vanilla; inverted winding renders the trunk
    # visible only from inside)
    tris = []
    for i in range(n_rings):
        for a in range(n_az):
            b = (a + 1) % n_az
            i0 = i * n_az + a
            i1 = i * n_az + b
            i2 = (i + 1) * n_az + a
            i3 = (i + 1) * n_az + b
            tris.append((i0, i1, i2))
            tris.append((i1, i3, i2))
    # tip cap (single fan point)
    tip_idx = len(verts)
    verts.append(pts[-1] + tans[-1] * max(radii[-1], 0.01))
    norms.append(tans[-1])
    uvs.append((0.5 * u_tile, 0.0))
    top = n_rings * n_az
    for a in range(n_az):
        b = (a + 1) % n_az
        tris.append((tip_idx, top + b, top + a))
    return (np.asarray(verts, np.float32), np.asarray(norms, np.float32),
            np.asarray(uvs, np.float32), np.asarray(tris, np.int32))


def _flare_multiplier(ang, t, lv: LevelParams, phases):
    if t >= lv.flare_length_dist or lv.flare_length_dist <= 0:
        return 1.0
    fall = (1.0 - t / lv.flare_length_dist) ** max(lv.flare_length_exp, 0.5)
    width = math.radians(max(lv.flare_radial_infl, 5.0))
    m = 0.0
    for ph, dist in phases:
        d = abs((ang - ph + math.pi) % (2 * math.pi) - math.pi)
        if d < width:
            lobe = (math.cos(d / width * math.pi) * 0.5 + 0.5) ** max(lv.flare_radial_exp, 0.3)
            m = max(m, dist * lobe)
    return 1.0 + m * fall


# ---------------------------------------------------------------------------
# main generation
# ---------------------------------------------------------------------------

def build_tree(tree: SptTree, seed: int | None = None,
               max_leaves: int = MAX_LEAVES) -> TreeGeometry:
    """Generate full tree geometry from parsed SPT parameters."""
    rng = np.random.default_rng(tree.seed if seed is None else seed)
    K = tree.size * WORLD_SCALE

    levels = tree.levels
    n_levels = len(levels)
    if n_levels < 2:
        raise ValueError(f'{tree.path}: fewer than 2 levels')
    leaf_level = levels[-1]
    branch_levels = levels[:-1]     # trunk + branch levels

    geo = TreeGeometry()
    all_v, all_n, all_uv, all_c, all_t = [], [], [], [], []
    vbase = 0

    # ---- trunk ----
    trunk = branch_levels[0]
    trunk_stored_len = float(trunk.length.eval_var(0.0, rng))
    trunk_len = max(trunk_stored_len * K, 1.0)
    trunk_rad = max(float(trunk.radius.eval_var(0.0, rng)) * K, 0.5)
    n_rings = int(np.clip(trunk.length_segments, 3, _RING_CAP[0]))
    n_az = int(np.clip(trunk.cross_segments, 4, _CROSS_CAP[0]))

    # trunk gravity 1 = stay vertical (fights disturbance); Camoran paradise
    # trunks have gravity 0 + high disturbance = wandering; forsythia canes
    # have gravity 3 = arch to the ground.
    tpts, tradii, ttans = _grow_stem(
        np.zeros(3), np.array([0.0, 0.0, 1.0]), trunk_len, trunk_rad,
        trunk, float(trunk.gravity.eval(0.0)),
        max(float(trunk.flexibility.eval(0.0)), 0.15), n_rings, rng)

    flare_phases = None
    if trunk.flare_count > 0:
        frng = np.random.default_rng(tree.flare_seed or 1)
        flare_phases = []
        for i in range(trunk.flare_count):
            ph = 2 * math.pi * i / trunk.flare_count + frng.uniform(-0.5, 0.5)
            dist = max(trunk.flare_radial_dist
                       + frng.uniform(-abs(trunk.flare_radial_dist_var),
                                      abs(trunk.flare_radial_dist_var)), 0.0)
            flare_phases.append((ph, dist))

    v, n, uv, tri = _tube_mesh(tpts, tradii, ttans, n_az, trunk, tree, True,
                               rng, flare_phases)
    c = _bark_colors(v, trunk_len)
    all_v.append(v); all_n.append(n); all_uv.append(uv); all_c.append(c)
    all_t.append(tri + vbase)
    vbase += len(v)
    geo.collision_verts.append(v)
    geo.collision_tris.append(tri)

    trunk_stem = Stem(0, tpts, tradii, trunk_len, trunk_stored_len)
    # collision hugs the trunk: capsule along the actual (leaning) centerline
    # over the lower ~70%, radius = mean of the tapered ring radii there
    k = max(1, int(round(n_rings * 0.7)))
    cap_r = float(np.mean(tradii[:k + 1])) * 0.7
    geo.trunk_capsule = (tpts[0].copy(), tpts[k].copy(), max(cap_r, 1.0))

    # ---- branch levels ----
    # Two passes: (1) grow all stems, recording each parent's furthest child
    # attach position; (2) draw each branch's tube clipped to where its
    # children/leaves actually reach — a branch is bare beyond its last child,
    # so an unclipped tip protrudes past the foliage as a stray spike.
    stems_by_level = {0: [(trunk_stem, ttans)]}
    pending = []          # tube draw jobs, filled below
    golden = math.pi * (3.0 - math.sqrt(5.0))
    # id(stem) -> furthest child attach x (0..1); trunk seeded from its branches
    furthest_child = {}

    for li in range(1, len(branch_levels)):
        lv = branch_levels[li]
        parent_lv = branch_levels[li - 1]
        parents = stems_by_level.get(li - 1, [])
        if not parents:
            break

        candidates = []      # (parent_stem, ptans, x, x_rel)
        for (pstem, ptans) in parents:
            cnt = parent_lv.child_freq * pstem.stored_length
            if lv.gen_profile is not None:
                cnt *= max(float(np.mean(lv.gen_profile._sample_curve()[1])), 0.05) * 2.0 \
                    if lv.gen_profile.lo != lv.gen_profile.hi else 1.0
            cnt = int(round(cnt))
            if cnt <= 0:
                continue
            first, last = parent_lv.child_first, parent_lv.child_last
            span = max(last - first, 1e-3)
            for i in range(cnt):
                x_rel = (i + rng.uniform(0.15, 0.85)) / cnt
                x = first + span * x_rel
                candidates.append((pstem, ptans, min(x, 0.999), x_rel))

        budget = MAX_STEMS_PER_LEVEL.get(li, 300)
        if len(candidates) > budget:
            idx = rng.choice(len(candidates), size=budget, replace=False)
            candidates = [candidates[i] for i in sorted(idx)]

        out = []
        for ci, (pstem, ptans, x, x_rel) in enumerate(candidates):
            stored_len = float(lv.length.eval_var(x_rel, rng))
            wlen = stored_len * K
            if wlen < MIN_STEM_WORLD_LEN:
                continue
            # this child covers its parent up to x
            furthest_child[id(pstem)] = max(furthest_child.get(id(pstem), 0.0), x)
            # interpolate parent point / tangent / radius at x
            fi = x * (len(pstem.points) - 1)
            i0 = int(fi)
            f = fi - i0
            ppos = pstem.points[i0] * (1 - f) + pstem.points[min(i0 + 1, len(pstem.points) - 1)] * f
            ptan = ptans[i0] * (1 - f) + ptans[min(i0 + 1, len(ptans) - 1)] * f
            ptan /= (np.linalg.norm(ptan) + 1e-12)
            prad = float(pstem.radii[i0] * (1 - f)
                         + pstem.radii[min(i0 + 1, len(pstem.radii) - 1)] * f)

            # direction: rotate away from parent axis by start angle
            a_deg = float(lv.start_angle.eval_var(x_rel, rng))
            a_rad = math.radians(np.clip(a_deg, -180.0, 180.0))
            n1, n2 = _ortho_frame(ptan)
            az = golden * ci + rng.uniform(-0.35, 0.35)
            radial = math.cos(az) * n1 + math.sin(az) * n2
            d = math.cos(a_rad) * ptan + math.sin(a_rad) * radial
            d /= (np.linalg.norm(d) + 1e-12)

            base_r = float(lv.radius.eval_var(x_rel, rng)) * K
            base_r = float(np.clip(base_r, 0.1, max(prad * 0.85, 0.1)))

            nr = int(np.clip(lv.length_segments, 2, _RING_CAP.get(min(li, 2), 3)))
            naz = int(np.clip(lv.cross_segments, 3, _CROSS_CAP.get(min(li, 2), 4)))

            gval = float(lv.gravity.eval_var(x_rel, rng))
            fval = float(lv.flexibility.eval(x_rel))
            pts, radii, tans = _grow_stem(ppos, d, wlen, base_r, lv, gval,
                                          fval, nr, rng)
            stem = Stem(li, pts, radii, wlen, stored_len, x)
            out.append((stem, tans))
            if base_r >= TUBE_MIN_RADIUS:
                pending.append((stem, tans, lv, naz, base_r, li))

        stems_by_level[li] = out

    # pass 2: draw branch tubes.  A childless intermediate branch is not
    # drawn (it would be a bare stick); everything else is drawn in full and
    # COVERED with leaves in the leaf pass so no bare wood shows.
    n_branch_levels = len(branch_levels)
    tube_stems = []       # stems that got drawn (bark that must be leaf-covered)
    for (stem, tans, lv, naz, base_r, li) in pending:
        is_leaf_carrier = (li == n_branch_levels - 1)
        if not is_leaf_carrier and furthest_child.get(id(stem), 0.0) <= 0.01:
            continue          # childless intermediate branch → skip (bare)
        pts, radii = stem.points, stem.radii
        v, n, uv, tri = _tube_mesh(pts, radii, tans, naz, lv, tree, False, rng)
        c = _bark_colors(v, trunk_len, wind=0.5 if is_leaf_carrier else 0.0,
                         t_axis=(pts, stem.length))
        all_v.append(v); all_n.append(n); all_uv.append(uv); all_c.append(c)
        all_t.append(tri + vbase)
        vbase += len(v)
        if base_r >= COLLISION_MIN_RADIUS:
            geo.collision_verts.append(v)
            geo.collision_tris.append(tri)
        tube_stems.append((stem, tans, li))

    # ---- leaves ----
    leaf_maps = [m for m in tree.leaf_maps
                 if m.texture and 'fileloaderror' not in m.texture.lower()]
    geo.leaf_groups = []
    if leaf_maps:
        last_bi = len(branch_levels) - 1
        carriers = stems_by_level.get(last_bi, [])
        if not carriers and last_bi > 0:
            carriers = stems_by_level.get(last_bi - 1, [])
        carrier_lv = branch_levels[-1]
        # Hanging foliage (long draped strands) is the weeping-willow look.
        # It requires BOTH high leaf-level gravity (leaves hang, stored 90)
        # AND high BRANCH gravity (the branches themselves arch over and
        # down, willow stores 2..4).  Junipers and white pines also store
        # leaf gravity 90 but their branches have gravity <=0.5 — they are
        # ordinary upright conifers, NOT weeping, so leaf gravity alone is
        # not enough (it would trail foliage down a juniper's bare trunk).
        lg = float(leaf_level.gravity.eval(0.0))
        max_branch_grav = max((abs(lv.gravity.hi) for lv in branch_levels[1:]),
                              default=0.0)
        drape = lg >= 5.0 and max_branch_grav >= 1.5
        strand_len = 0.0
        if drape:
            # curtain length: proportional to the average carrier-branch
            # length so the strand always trails its own branch (never floats
            # off on its own).  Willow branches are ~0.3*Size long.
            avg_branch = np.mean([s.length for s, _ in carriers]) if carriers else 0.0
            strand_len = min(avg_branch * 1.4, tree.size * WORLD_SCALE * 0.28)

        # 1) Collect ATTACHMENT POINTS along every leaf-carrying branch.
        #    Leaves cover the WHOLE branch (x from just above the base out to
        #    the very tip, x=1.0) so no bare tip protrudes past the foliage.
        #    Also cover the exposed tips of any drawn structural bough whose
        #    own children stop short of its end (furthest_child < ~0.9).
        #    Each point is tagged with its host branch index so thinning can
        #    guarantee every branch keeps foliage.
        attach = []   # (pos, tan, x, branch_idx)

        def _sample_branch(pstem, ptans, x0, x1, count, bidx):
            span = max(x1 - x0, 1e-3)
            for i in range(count):
                x = min(x0 + span * (i + rng.uniform(0.15, 0.85)) / count, 1.0)
                fi = x * (len(pstem.points) - 1)
                i0 = int(min(fi, len(pstem.points) - 1))
                f = fi - i0
                j1 = min(i0 + 1, len(pstem.points) - 1)
                pos = pstem.points[i0] * (1 - f) + pstem.points[j1] * f
                tan = ptans[i0] * (1 - f) + ptans[j1] * f
                tan /= (np.linalg.norm(tan) + 1e-12)
                attach.append((pos, tan, x, bidx))

        # tip caps: leaf clusters that sit AT/just beyond a branch tip so the
        # end point is always buried in foliage (kept out of the drape budget
        # and never thinned away — they carry a sentinel branch index of -1)
        tip_caps = []      # (pos, tan)

        bidx = 0
        for (pstem, ptans) in carriers:
            cnt = int(round(carrier_lv.child_freq * pstem.stored_length))
            cnt = max(cnt, 4)                     # never leave a branch bare
            _sample_branch(pstem, ptans, 0.02, 1.0, cnt, bidx)
            tip = pstem.points[-1]
            ttan = ptans[-1] / (np.linalg.norm(ptans[-1]) + 1e-12)
            tip_caps.append((tip + ttan * pstem.radii[-1], ttan))
            bidx += 1

        # cover the exposed tips of drawn structural boughs (non-carrier
        # branches whose children/leaves stopped short of the branch end)
        carrier_ids = {id(s) for s, _ in carriers}
        for (stem, tans, li) in tube_stems:
            if id(stem) in carrier_ids:
                continue
            cov = furthest_child.get(id(stem), 0.0)
            if cov >= 0.9:
                continue                          # already covered to the tip
            # dress the bare span [cov, 1.0] with a few leaf clusters
            n_tip = max(2, int(round((1.0 - cov) * 6)))
            _sample_branch(stem, tans, max(cov, 0.5), 1.0, n_tip, bidx)
            ttan = tans[-1] / (np.linalg.norm(tans[-1]) + 1e-12)
            tip_caps.append((stem.points[-1] + ttan * stem.radii[-1], ttan))
            bidx += 1

        # 2) Budget by ATTACHMENT POINT (not final card).  Each non-drape
        #    attachment expands to a small CLUMP of cards (see step 3); draped
        #    attachments expand to a hanging strand.  Thin proportionally but
        #    keep >=1 point per branch so no branch is left bare.
        cards_per = 4 if drape else 3
        max_attach = max(1, max_leaves // cards_per)
        if len(attach) > max_attach:
            attach = _thin_keep_all_branches(attach, max_attach, rng)
        geo.n_leaves = len(attach) * cards_per

        # 3) Expand each attachment into a TIGHT CLUSTER of cards hugging the
        #    twig.  Placing a small clump (not one lone card) at each point is
        #    what makes foliage read as branch-following — dense where twigs
        #    are dense, thin at the edges, with the natural inlets/gaps of a
        #    real crown — and guarantees no single card is left floating.  The
        #    cluster radius is a fraction of a leaf width so cards overlap.
        leaf_w = (leaf_maps[0].size[0] * K) if leaf_maps else 0.08 * K
        clump = 1 if drape else 3          # cards per attachment
        clump_r = leaf_w * 0.55
        leaf_positions = []      # (pos, stem_dir, x)
        for (pos, tan, x, _bi) in attach:
            n1, n2 = _ortho_frame(tan)
            if drape:
                # strand hangs from the branch; the TOP card sits at the
                # branch so the strand is always visually anchored
                step = strand_len * rng.uniform(0.75, 1.05) / cards_per
                for c in range(cards_per):
                    z = pos - np.array([0.0, 0.0, step * c])
                    leaf_positions.append((z, tan, x))
            else:
                for _ in range(clump):
                    off = (n1 * rng.uniform(-1, 1) + n2 * rng.uniform(-1, 1)
                           + tan * rng.uniform(-0.6, 0.6)) * clump_r
                    leaf_positions.append((pos + off, tan, x))

        # tip caps: 3 jittered cards straddling each branch tip so the end
        # point is fully buried (never draped, never thinned).  Jitter by a
        # fraction of the leaf size so the cards overlap the tip.
        leaf_w = (leaf_maps[0].size[0] * K) if leaf_maps else 0.08 * K
        for (tip, ttan) in tip_caps:
            tn1, tn2 = _ortho_frame(ttan)
            for _ in range(3):
                jit = (tn1 * rng.uniform(-0.4, 0.4)
                       + tn2 * rng.uniform(-0.4, 0.4)) * leaf_w
                leaf_positions.append((tip + jit, ttan, 1.0))

        # foliage floor: no leaf may sit below the lowest branch attachment
        # on the trunk.  A branch (child_first .. 1.0 on the trunk) plus its
        # leaf coverage can otherwise trail foliage down a bare lower trunk
        # (juniper: branches start at 25% but leaves reached the ground).
        # Exempt drape trees — willow strands hang below their branch by design.
        first_branches = stems_by_level.get(1, [])
        if first_branches and not drape:
            trunk_first_z = min(float(s.points[0][2]) for s, _ in first_branches)
            trunk_first_z -= leaf_w    # small tolerance so the join isn't bald
            leaf_positions = [lp for lp in leaf_positions if lp[0][2] >= trunk_first_z]

        # cull isolated CLUMPS: cards come in clumps of 3, so a lone clump
        # already has 2 internal neighbours — require more than that within a
        # ~2.5-leaf radius so an isolated clump (no OTHER clump nearby) is
        # removed while a clump embedded in the mass survives.  (Draped
        # strands are exempt — a hanging curtain is legitimately sparse.)
        if leaf_positions and not drape:
            leaf_positions = _cull_isolated_leaves(
                leaf_positions, leaf_w * 2.5, min_neighbours=clump + 2)

        # canopy centre for outward normals
        if leaf_positions:
            centre = np.mean([p for p, _, _ in leaf_positions], axis=0)

        # UV source: composite-map quads (section 10002) when present — the
        # actual shipped leaf DDS is the composite; per-map quads crop it.
        # Quads are indexed by ORIGINAL leaf-map position.
        map_indices = [i for i, m in enumerate(tree.leaf_maps) if m in leaf_maps]
        use_composite = len(tree.leaf_quads) >= len(tree.leaf_maps) > 0
        _FULL_QUAD = (1.0, 1.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0)   # BR BL TL TR

        if use_composite:
            groups = {'__composite__': {'v': [], 'n': [], 'uv': [], 'c': [], 't': []}}
        else:
            groups = {m.texture: {'v': [], 'n': [], 'uv': [], 'c': [], 't': []}
                      for m in leaf_maps}
        # Composite leaf atlases ship square (512x512 / 256x256), so the
        # UV crop's on-screen aspect is du/dv.  Keeping each leaf card at that
        # aspect stops the texture being stretched / alpha-cut (the "sharp
        # bottom edge" artifact).
        atlas_ar = 1.0

        for pos, tan, x in leaf_positions:
            k = int(rng.integers(0, len(leaf_maps)))
            m = leaf_maps[k]
            if use_composite:
                g = groups['__composite__']
                quad = tree.leaf_quads[map_indices[k]]
            else:
                g = groups[m.texture]
                quad = _FULL_QUAD
            # leaf card size = section 4005 * Size (section 4006 is supposed
            # to be exactly that product, but it is STALE in ~15 shrubs —
            # buckthorn stores 0.08 where 4005*Size = 3.6 — so always derive).
            # The card must match the UV crop's ON-SCREEN aspect (crop_uv_ar *
            # atlas_ar) or the leaf texture is stretched and its alpha edge
            # cuts across the card — the "sharp bottom edge" artifact.
            du = abs(quad[0] - quad[2])           # crop u extent
            dv = abs(quad[1] - quad[5])           # crop v extent
            crop_ar = (du / dv) * atlas_ar if dv > 1e-4 else 1.0
            area = max(m.size[0] * m.size[1], 1e-6) * K * K
            h = math.sqrt(area / max(crop_ar, 1e-3))
            w = crop_ar * h
            if not (w > 0 and h > 0):
                w = h = 0.08 * K
            _leaf_card(g, pos, w, h, m, quad, centre, rng, drape)
        for tex_key, g in groups.items():
            if not g['v']:
                continue
            geo.leaf_groups.append({
                'texture': tex_key,
                'verts': np.asarray(np.concatenate(g['v']), np.float32),
                'normals': np.asarray(np.concatenate(g['n']), np.float32),
                'uvs': np.asarray(np.concatenate(g['uv']), np.float32),
                'colors': np.asarray(np.concatenate(g['c']), np.float32),
                'tris': np.asarray(np.concatenate(g['t']), np.int32),
            })

    geo.bark_verts = np.concatenate(all_v)
    geo.bark_normals = np.concatenate(all_n)
    geo.bark_uvs = np.concatenate(all_uv)
    geo.bark_colors = np.concatenate(all_c)
    geo.bark_tris = np.concatenate(all_t)

    zs = [geo.bark_verts[:, 2].max()]
    rs = [float(np.hypot(geo.bark_verts[:, 0], geo.bark_verts[:, 1]).max())]
    for g in geo.leaf_groups:
        zs.append(g['verts'][:, 2].max())
        rs.append(float(np.hypot(g['verts'][:, 0], g['verts'][:, 1]).max()))
    geo.height = float(max(zs))
    geo.radius = float(max(rs))
    geo.n_stems = sum(len(v) for v in stems_by_level.values())
    return geo


def _bark_colors(verts, trunk_len, wind=0.0, t_axis=None):
    """Vertex colors for bark: white RGB, alpha = wind weight."""
    c = np.ones((len(verts), 4), np.float32)
    if wind > 0.0 and t_axis is not None:
        pts, wlen = t_axis
        # weight ramps toward the stem tip
        z0 = verts[:, 2] - verts[:, 2].min()
        rng_z = max(float(z0.max()), 1e-3)
        c[:, 3] = np.clip(z0 / rng_z, 0, 1) * wind
    else:
        c[:, 3] = wind
    return c


def _leaf_card(g, pos, w, h, leaf_map, uv_quad, centre, rng, drape=False):
    """Two crossed quads forming one leaf cluster card.

    uv_quad: 8 floats, texture corners in order BR, BL, TL, TR
    (composite-map convention from SPT section 10002).
    drape: part of a hanging strand — cards stay upright (the strand itself
    provides the downward drape), so no per-card downward pitch.
    """
    yaw = rng.uniform(0, 2 * math.pi)
    ov = math.radians((leaf_map.orientation_var or 0.0) * 90.0)
    tilt = rng.uniform(-ov, ov) if ov else rng.uniform(-0.25, 0.25)
    rot = math.radians(leaf_map.rotate or 0.0) + rng.uniform(-0.3, 0.3)
    hang = float(np.clip(leaf_map.hang or 0.0, 0.0, 1.0)) if not drape else 0.0

    out = pos - centre
    out[2] *= 0.5
    nlen = np.linalg.norm(out)
    outward = out / nlen if nlen > 1e-3 else np.array([0.0, 0.0, 1.0])

    # pivot offset from texture origin: origin (u,v) is the attachment point
    ou = 0.5 - float(leaf_map.origin[0])
    ovv = float(leaf_map.origin[1]) - 0.5   # dds v runs down

    base = len(g['v']) and int(sum(len(a) for a in g['v'])) or 0
    for k in range(2):
        ang = yaw + k * (math.pi / 2)
        right = np.array([math.cos(ang), math.sin(ang), 0.0])
        upv = np.array([0.0, 0.0, 1.0])
        # tilt/rot the card a little; hang pitches it downward
        upv = _rotate_toward(upv, right, rot * 0.5)
        if hang > 0:
            upv = _rotate_toward(upv, np.array([0.0, 0.0, -1.0]), hang * math.pi / 3)
        upv = _perturb(upv, abs(tilt) * 0.5, rng)
        upv /= np.linalg.norm(upv) + 1e-12
        centre_pos = pos + right * (ou * w) + upv * (ovv * h)
        hw, hh = w * 0.5, h * 0.5
        corners = [centre_pos - right * hw - upv * hh,
                   centre_pos + right * hw - upv * hh,
                   centre_pos + right * hw + upv * hh,
                   centre_pos - right * hw + upv * hh]
        vs = np.asarray(corners, np.float32)
        nrm = np.cross(right, upv)
        nrm /= np.linalg.norm(nrm) + 1e-12
        # lighting normal: blend quad normal toward canopy-outward
        ln = nrm * 0.35 + outward * 0.65
        ln /= np.linalg.norm(ln) + 1e-12
        ns = np.tile(ln.astype(np.float32), (4, 1))
        # card corners are BL, BR, TR, TL; uv_quad is BR, BL, TL, TR
        q = uv_quad
        uvs = np.asarray([(q[2], q[3]), (q[0], q[1]),
                          (q[6], q[7]), (q[4], q[5])], np.float32)
        col = np.ones((4, 4), np.float32)
        col[:, 0] = leaf_map.color[0]
        col[:, 1] = leaf_map.color[1]
        col[:, 2] = leaf_map.color[2]
        col[:, 3] = 1.0     # full wind weight on leaves
        tris = np.asarray([(0, 1, 2), (0, 2, 3)], np.int32) + base
        g['v'].append(vs); g['n'].append(ns); g['uv'].append(uvs)
        g['c'].append(col); g['t'].append(tris)
        base += 4
