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

# Stem shape = ANGLE PROFILE out-curl + GRAVITY TROPISM return + random
# DISTURBANCE — see _grow_stem's docstring for the full model.  Gravity is
# a WORLD-SPACE TROPISM FORCE (the SpeedTree docs' model — a vertical pull
# whose bending torque is proportional to the sine of the stem's angle
# from the pole):
#
#     d(theta)/dt = -GRAVITY_RESPONSE * g * flex * flex_profile(t)
#                    * sin(theta) / n_rings
#
# where theta = angle from straight up (g > 0 lifts; a negative draw from
# the stored variance pulls toward straight down instead) and g is pure
# STRENGTH — no wrap rule, no target pitch.  The sin term makes torque
# VANISH as the stem nears the pole, so stems asymptote smoothly instead
# of pinning, and a vertical trunk feels nothing at all (which is why
# trunk gravity values are arbitrary in the data: 76 SPTs store 1, 29
# store 0).  The flex-profile gate (6003, 0 at the base -> 1 at the tip)
# is what confines the return-hook to the outer stem, completing the S
# started by the angle profile's out-curl.
#
# Corpus evidence (references/spttools + all 113 Oblivion SPTs):
#   - cottonwood/dogwood limbs (g .2-.6, flex .4-.6): emerge at half
#     their start angle, curl out, then the tip curls back vertical.
#   - forsythia canes: g 0.2-0.3 VARIANCE 0.5 — the only negative-capable
#     level in the corpus; each cane draws its own strength, some rising,
#     some arching down = the fountain.
#   - black locust lvl1 is the ONLY branch level with g>1 and flex>0
#     (g 0..3): strong lift = its upright crown — disproving any
#     "g>1 bends downward" wrap rule.
#   - willow branches g 2-4 but flex 0: held; the weeping look is the
#     hanging leaf curtains.
# Deadbush pins the magnitude: it stores gravity 1.0 with flexibility up
# to 0.96, yet Oblivion renders a sprawling crooked bush — tropism is a
# WEAK finisher on top of the authored shape (start angle + angle profile
# + disturbance deflection), never the shape itself.
GRAVITY_RESPONSE = 2.0          # tropism strength at g*flex*fp = 1

# stems at least this thick (world units, base radius) contribute their
# tube triangles to the exact-mesh collision shape
COLLISION_MIN_RADIUS = 5.0

# geometry budgets (SpeedTree LOD0 counts can be enormous; these caps keep
# tri counts near vanilla Skyrim tree levels while preserving the look)
MAX_STEMS_PER_LEVEL = {1: 64, 2: 260, 3: 320, 4: 320}
MAX_LEAVES = 900
MIN_STEM_WORLD_LEN = 2.0        # skip micro-stems
TUBE_MIN_RADIUS = 0.35          # below this, a stem renders no tube (world units)

# tessellation caps (cross-section sides x length rings).  Ring caps must
# stay near the stored segment counts (oak trunk 18, cottonwood limbs 13):
# crushing them to 3-6 rings turned every gravity/disturbance curve into a
# short straight polyline — the "branches don't curve" look.
_CROSS_CAP = {0: 16, 1: 6, 2: 4}
_RING_CAP = {0: 16, 1: 10, 2: 6}


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


def _cull_isolated_groups(leaf_positions, radius):
    """Drop whole attachment GROUPS (cards sharing an attach id) that have
    no other group's centre within `radius` — those render as clumps
    floating detached from the crown.  Per-card counting misses them: a
    3-card clump (or two adjacent tip caps) is its own neighbourhood.
    """
    from collections import defaultdict
    groups = defaultdict(list)
    for lp in leaf_positions:
        groups[lp[3]].append(lp[0])
    ids = list(groups)
    if len(ids) <= 2:
        return leaf_positions
    centres = np.array([np.mean(groups[g], axis=0) for g in ids])
    try:
        from scipy.spatial import cKDTree
        t = cKDTree(centres)
        counts = t.query_ball_point(centres, radius, return_length=True)
        keep_ids = {g for g, c in zip(ids, counts) if c > 1}  # self + 1 other
    except Exception:
        keep_ids = set()
        for i in range(len(ids)):
            d = np.linalg.norm(centres - centres[i], axis=1)
            if int((d < radius).sum()) > 1:
                keep_ids.add(ids[i])
    if len(keep_ids) == len(ids):
        return leaf_positions
    return [lp for lp in leaf_positions if lp[3] in keep_ids]


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

def _rot_axis(v, axis, ang):
    """Rodrigues rotation of v about unit axis by ang, renormalized."""
    c, s = math.cos(ang), math.sin(ang)
    out = (v * c + np.cross(axis, v) * s
           + axis * float(np.dot(axis, v)) * (1.0 - c))
    return out / (np.linalg.norm(out) + 1e-12)


def _grow_stem(origin: np.ndarray, parent_axis: np.ndarray,
               radial: np.ndarray, alpha0: float, world_len: float,
               base_radius: float, lv: LevelParams, grav_value: float,
               flex_value: float, n_rings: int,
               rng: np.random.Generator) -> tuple:
    """Integrate a stem centerline from the STORED SHAPE CURVES.

    The centerline is the composition of three authored effects, in the
    order the engine's output shows them:

    1. ANGLE PROFILE (6017, misnamed "Gravity Profile" in CAD 4 — the v3
       UI calls it Angle Profile): the stem's angle from its parent axis
       along its own length is  alpha(t) = start_angle * profile(t).
       Every Oblivion branch level stores the profile as 0.5 -> 1.0, so a
       branch EMERGES AT HALF ITS START ANGLE — flowing out of the parent
       like a real limb out of a stump — and curls outward to the full
       angle at the tip.  This is the deterministic out-curl of the
       billboard silhouettes; spawning branches at their full start angle
       made every attachment a straight rigid spoke.
    2. GRAVITY TROPISM: world-space pull toward straight up (negative
       draws: down), torque proportional to sin(angle from the pole),
       strength g * flexibility * FLEXIBILITY PROFILE (6003, a 0 -> 1
       tip-ward ramp: bases hold the authored angle-profile shape, tips
       curl back).  Angle profile curls the stem OUT, tip-weighted
       tropism hooks it back IN — together they are the S-curve of
       forsythia canes and cottonwood limbs.
    3. DISTURBANCE (6000): the stored curve IS the drawn deflection
       profile — accumulated bend away from the stem's heading =
       variance_degrees * curve_y(t), applied in a per-stem plane.
       Forsythia stores a bell (0 -> 0.92 -> 0): the cane bows out ~46
       degrees mid-length and RETURNS by the tip — the S-curve is drawn
       in the file.  Cottonwood stores a 0 -> 1 ramp: limbs sweep
       outward toward the tips.  Treating this curve as a mere weight on
       random noise (every previous model) threw the authored shape away
       and left branches straight.  The per-stem randomness is the
       amplitude/sign/plane draw, not the shape.

    parent_axis/radial: unit vectors, the attachment frame (radial is
    perpendicular to parent_axis, pointing where the branch leans out).
    alpha0: start angle in radians (0 for trunks — their angle profile
    slot is a no-op since they have no parent axis to lean from).

    Returns (points (n_rings+1,3), radii (n_rings+1,), tangents (n_rings+1,3)).
    """
    pts = np.empty((n_rings + 1, 3))
    tans = np.empty((n_rings + 1, 3))
    radii = np.empty(n_rings + 1)
    p = origin.copy()
    seg = world_len / n_rings
    dist_var = math.radians(lv.disturbance.variance)

    g = float(grav_value)
    pole = np.array([0.0, 0.0, 1.0 if g > 0.0 else -1.0])
    g_mag = abs(g)

    aprof = lv.gravity_profile          # 6017 = angle profile (see above)
    plane_axis = np.cross(parent_axis, radial)
    plane_axis /= (np.linalg.norm(plane_axis) + 1e-12)

    def _alpha(t):
        prof = float(aprof.eval(t)) if aprof else 1.0
        return alpha0 * prof

    a_prev = _alpha(0.0)
    d = math.cos(a_prev) * parent_axis + math.sin(a_prev) * radial
    d /= (np.linalg.norm(d) + 1e-12)
    pts[0] = p
    tans[0] = d

    # disturbance: deflection from the stem's authored heading =
    # variance * envelope(t) * smooth_noise(t), where envelope is the
    # stored curve and the noise is a low-frequency signed sine.  The
    # envelope BOUNDS the deflection, so wherever the author's curve
    # returns to zero the stem must return to its heading (forsythia's
    # bell = the S-swing), and a ramp envelope (most trunks) pins the
    # base straight while only the top wanders — applying the curve with
    # a constant sign instead turned it into a full one-way lean that
    # tipped whole trees sideways and swung branches into the ground.
    d_amp = dist_var * rng.uniform(0.7, 1.0)
    d_az = rng.uniform(0.0, 2.0 * math.pi)
    d_axis = None                      # built lazily from the initial frame
    d_phase = rng.uniform(0.0, 2.0 * math.pi)
    d_turns = rng.uniform(0.5, 1.4)
    defl_prev = 0.0
    if dist_var > 0.0:
        defl_prev = (d_amp * max(float(lv.disturbance.eval(0.0)), 0.0)
                     * math.sin(d_phase))

    for i in range(1, n_rings + 1):
        t = i / n_rings
        # 1) authored out-curl: advance the angle-profile arc in the
        #    attachment plane (rotating the CURRENT d keeps accumulated
        #    tropism/disturbance deviations)
        a_now = _alpha(t)
        if abs(a_now - a_prev) > 1e-9:
            d = _rot_axis(d, plane_axis, a_now - a_prev)
        a_prev = a_now
        # 2) tropism: rotate toward the pole by step * sin(theta) — the
        #    perpendicular component of the pole IS sin(theta) * unit bend
        #    direction, so torque self-limits at the pole
        if g_mag > 0.0 and flex_value > 0.0:
            perp = pole - float(np.dot(pole, d)) * d
            s = float(np.linalg.norm(perp))         # = sin(theta)
            if s > 1e-9:
                fp = float(lv.flex_profile.eval(t)) if lv.flex_profile else t
                step = (GRAVITY_RESPONSE * g_mag * flex_value
                        * max(fp, 0.0) / n_rings)
                ang = min(step * s, math.asin(min(s, 1.0)))
                d = math.cos(ang) * d + math.sin(ang) * (perp / s)
                d /= (np.linalg.norm(d) + 1e-12)
        # 3) authored deflection: advance envelope(t) * noise(t).  The bend
        #    axis is fixed per stem (so the drawn shape reads as one clean
        #    arc/S, not scribble) and perpendicular to the stem's initial
        #    heading at a random azimuth.
        if dist_var > 0.0:
            if d_axis is None:
                n1, n2 = _ortho_frame(d)
                bend_dir = math.cos(d_az) * n1 + math.sin(d_az) * n2
                d_axis = np.cross(d, bend_dir)
                d_axis /= (np.linalg.norm(d_axis) + 1e-12)
            env = max(float(lv.disturbance.eval(t)), 0.0)
            defl = (d_amp * env
                    * math.sin(2.0 * math.pi * d_turns * t + d_phase))
            if abs(defl - defl_prev) > 1e-9:
                d = _rot_axis(d, d_axis, defl - defl_prev)
            defl_prev = defl
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
               is_trunk: bool, rng, flare_phases=None, stored_len=1.0):
    """Build a tapered tube along the centerline.  Returns v, n, uv, tri.

    Bark UV model (SPT 6013-6016 + 15002/15003, semantics per the
    SpeedTreeCAD notes in references/spttools-master):
      - U: u_tile repeats around the circumference, plus a Twist offset that
        spirals the texture along the length.
      - V: v_abs means v_tile is the exact repeat count; otherwise the count
        scales with the stem's STORED length so texel density matches across
        stems (dogwood trunk: 12 * 0.8 stored = 9.6 repeats, which lands
        square texels against its 3-repeat U — verified for every sampled
        tree).
      - Random V offset (15002) de-syncs the bark phase between stems.
    The seam column is duplicated (n_az + 1 columns) so the last quad runs
    u -> u_tile instead of sweeping the whole texture backwards — the old
    modulo wrap smeared a mirrored copy of the bark across one face of
    every trunk.
    """
    n_rings = len(pts) - 1
    verts, norms, uvs = [], [], []
    total_len = float(np.sum(np.linalg.norm(np.diff(pts, axis=0), axis=1))) or 1.0
    u_tile = lv.u_tile or 1.0
    v_rep = (lv.v_tile or 1.0) * (1.0 if lv.v_abs else max(stored_len, 1e-3))
    v_off = float(rng.uniform(0.0, 1.0)) if lv.random_v_offset else 0.0
    twist = lv.twist or 0.0
    cols = n_az + 1

    run = 0.0
    for i in range(n_rings + 1):
        if i > 0:
            run += float(np.linalg.norm(pts[i] - pts[i - 1]))
        t = run / total_len
        n1, n2 = _ortho_frame(tans[i])
        r = radii[i]
        for a in range(cols):
            ang = 2.0 * math.pi * (a % n_az) / n_az
            rr = r
            if is_trunk and flare_phases is not None and lv.flare_length_dist > 0:
                rr *= _flare_multiplier(ang, t, lv, flare_phases)
            radial = math.cos(ang) * n1 + math.sin(ang) * n2
            verts.append(pts[i] + radial * rr)
            norms.append(radial)
            uvs.append((a / n_az * u_tile + twist * t,
                        (1.0 - t) * v_rep + v_off))

    # winding: front face outward (geometric normal aligned with the radial
    # vertex normals — matches vanilla; inverted winding renders the trunk
    # visible only from inside)
    tris = []
    for i in range(n_rings):
        for a in range(n_az):
            i0 = i * cols + a
            i1 = i * cols + a + 1
            i2 = (i + 1) * cols + a
            i3 = (i + 1) * cols + a + 1
            tris.append((i0, i1, i2))
            tris.append((i1, i3, i2))
    # tip cap (single fan point)
    tip_idx = len(verts)
    verts.append(pts[-1] + tans[-1] * max(radii[-1], 0.01))
    norms.append(tans[-1])
    uvs.append((0.5 * u_tile + twist, v_off))
    top = n_rings * cols
    for a in range(n_az):
        tris.append((tip_idx, top + a + 1, top + a))
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

    # A vertical trunk feels no tropism torque (sin 0), so trunk gravity
    # only matters once disturbance tilts it: g=1 trunks right themselves,
    # g=0 trunks (Camoran paradise, high disturbance) wander freely.  Use
    # the STORED flexibility — flooring it (old 0.15) put bend on stems the
    # author pinned rigid (forsythia's stub stores 0.01).
    t_az = rng.uniform(0.0, 2.0 * math.pi)
    tpts, tradii, ttans = _grow_stem(
        np.zeros(3), np.array([0.0, 0.0, 1.0]),
        np.array([math.cos(t_az), math.sin(t_az), 0.0]), 0.0,
        trunk_len, trunk_rad, trunk, float(trunk.gravity.eval(0.0)),
        float(trunk.flexibility.eval(0.0)), n_rings, rng)

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
                               rng, flare_phases, stored_len=trunk_stored_len)
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

            # attachment frame: full start angle + spawn azimuth; the
            # angle profile inside _grow_stem shapes the actual emergence
            # (branches flow out of the parent at ~half the start angle)
            a_deg = float(lv.start_angle.eval_var(x_rel, rng))
            a_rad = math.radians(np.clip(a_deg, -180.0, 180.0))
            n1, n2 = _ortho_frame(ptan)
            az = golden * ci + rng.uniform(-0.35, 0.35)
            radial = math.cos(az) * n1 + math.sin(az) * n2

            # Radius curve over the spawn window is the LIMB SIZE VARIATION
            # (cottonwood forks store 0.03 -> 0.01, a 3x spread); cap only at
            # the parent's radius at the attach point, not a fraction of it —
            # 0.85*prad flattened the fork thickness range to near-uniform.
            base_r = float(lv.radius.eval_var(x_rel, rng)) * K
            base_r = float(np.clip(base_r, 0.1, max(prad, 0.1)))

            nr = int(np.clip(lv.length_segments, 2, _RING_CAP.get(min(li, 2), 3)))
            naz = int(np.clip(lv.cross_segments, 3, _CROSS_CAP.get(min(li, 2), 4)))

            gval = float(lv.gravity.eval_var(x_rel, rng))
            fval = float(lv.flexibility.eval(x_rel))
            pts, radii, tans = _grow_stem(ppos, ptan, radial, a_rad, wlen,
                                          base_r, lv, gval, fval, nr, rng)
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
        v, n, uv, tri = _tube_mesh(pts, radii, tans, naz, lv, tree, False, rng,
                                   stored_len=stem.stored_length)
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
            strand_len = min(avg_branch * 2.0, tree.size * WORLD_SCALE * 0.40)

        # 1) Collect ATTACHMENT POINTS along every leaf-carrying branch,
        #    inside the LEAF GENERATION WINDOW — stored in the carrier
        #    level's own 6010-12 slots (FORMAT: "Generation/First+Last+Freq
        #    of leaves are in 6010-12 of the last branch level").  Carpeting
        #    each branch 0..1 instead buried trunks under ground-level
        #    foliage (black locust stores [0.15,0.90], dogwood [0.40,1.00]).
        #    Tips are still capped below so no drawn tube pokes out bare.
        #    Each point is tagged with its host branch index so thinning can
        #    guarantee every branch keeps foliage.
        lx0 = float(np.clip(carrier_lv.child_first, 0.02, 0.95))
        lx1 = float(np.clip(carrier_lv.child_last, lx0 + 0.05, 1.0))
        leaf_w = (leaf_maps[0].size[0] * K) if leaf_maps else 0.08 * K
        attach = []   # (pos, tan, x, branch_idx)

        # PLACEMENT DISTANCE (the leaves level's 6004 curve): how far a
        # leaf stands off its twig, by position along it.  Willow stores
        # up to 0.13 * Size (~160 units) — the cascading curtain shell
        # hangs OUTSIDE the twigs; black locust ~0.04 = puffy clumps;
        # forsythia stores 0 = clusters hug the canes.  Ignoring it
        # shrink-wrapped every crown tight to the branch skeleton.
        dist_curve = leaf_level.length
        # gather-vs-dot style: placement distance > 0 means leaves puff
        # OFF the twig (locust 0.04, dogwood 0.08 — clumped crowns);
        # distance 0 means leaves ride ALONG the stem (forsythia — clusters
        # dotted the length of each cane; gathering those bunched every
        # cane's foliage into one blob and bared the rest)
        gather = bool(dist_curve
                      and max(dist_curve.lo, dist_curve.hi) > 0.01)

        def _sample_branch(pstem, ptans, x0, x1, count, bidx, cap=10):
            span = max(x1 - x0, 1e-3)
            if gather and count > cap:
                # dense foliage gathers as ONE tight clump at a random spot
                # on the twig — black locust stores ~70 leaves per twig
                # (freq 270), and the engine's crowds read as discrete
                # billowing masses; spreading a thin budget along every
                # twig instead merged the whole crown into a shapeless blob
                w = span * max(cap / count, 0.12)
                c0 = x0 + rng.uniform(0.0, span - w)
                x0, x1, span, count = c0, c0 + w, w, cap
            for i in range(count):
                x = min(x0 + span * (i + rng.uniform(0.15, 0.85)) / count, 1.0)
                fi = x * (len(pstem.points) - 1)
                i0 = int(min(fi, len(pstem.points) - 1))
                f = fi - i0
                j1 = min(i0 + 1, len(pstem.points) - 1)
                pos = pstem.points[i0] * (1 - f) + pstem.points[j1] * f
                tan = ptans[i0] * (1 - f) + ptans[j1] * f
                tan /= (np.linalg.norm(tan) + 1e-12)
                dd = float(dist_curve.eval_var(x, rng)) * K if dist_curve else 0.0
                if dd > 0.0:
                    n1, n2 = _ortho_frame(tan)
                    az = rng.uniform(0.0, 2.0 * math.pi)
                    pos = pos + (math.cos(az) * n1 + math.sin(az) * n2) * dd
                attach.append((pos, tan, x, bidx))

        # tip caps: leaf clusters that sit AT/just beyond a branch tip so the
        # end point is always buried in foliage (kept out of the drape budget
        # and never thinned away — they carry a sentinel branch index of -1)
        tip_caps = []      # (pos, tan)

        bidx = 0
        for (pstem, ptans) in carriers:
            cnt = int(round(carrier_lv.child_freq * pstem.stored_length))
            cnt = max(cnt, 4)                     # never leave a branch bare
            _sample_branch(pstem, ptans, lx0, lx1, cnt, bidx)
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

        # 1b) LEAF COLLISION (3008 mode + 3007 tolerance): the engine
        #     prunes leaves that collide with tree geometry.  Every
        #     Oblivion tree stores a mode (1=branch, 2=tree; tol 0.32-0.6).
        #
        #     Both modes: leaves against the TRUNK are removed — this is
        #     what keeps foliage off the lower trunk (black locust's
        #     bough-base carriers otherwise skirt the trunk foot).
        if tree.leaf_collision and attach:
            tol = float(np.clip(tree.leaf_placement_tolerance, 0.0, 1.0))
            tp = trunk_stem.points
            tr = trunk_stem.radii
            A = np.array([a[0] for a in attach])
            d2 = np.linalg.norm(A[:, None, :] - tp[None, :, :], axis=2)
            near = np.argmin(d2, axis=1)
            lim = tr[near] + leaf_w * 0.6 * (1.0 - tol)
            keepm = d2[np.arange(len(A)), near] > lim
            if not keepm.all():
                attach = [a for a, k in zip(attach, keepm) if k]

        #     Mode 2 additionally prunes leaf-vs-TREE overlap — the open,
        #     clumped crown of the billboards (discrete masses with gaps,
        #     never a solid shell).  Modelled as greedy poisson-disk
        #     thinning with radius leaf_w * (1 - tolerance); each branch
        #     always keeps its first point so nothing goes bare.  Mode 1
        #     trees (dogwood, forsythia) keep their full leaf density.
        if tree.leaf_collision == 2 and attach:
            tol = float(np.clip(tree.leaf_placement_tolerance, 0.0, 1.0))
            r_min = leaf_w * (1.0 - tol)
            if r_min > 1e-3:
                inv = 1.0 / r_min
                grid = {}
                kept = []
                seen_branch = set()
                for oi in rng.permutation(len(attach)):
                    a = attach[int(oi)]
                    p3 = a[0]
                    key = (int(p3[0] * inv), int(p3[1] * inv), int(p3[2] * inv))
                    hit = False
                    for kx in range(key[0] - 1, key[0] + 2):
                        for ky in range(key[1] - 1, key[1] + 2):
                            for kz in range(key[2] - 1, key[2] + 2):
                                for j in grid.get((kx, ky, kz), ()):
                                    if float(np.linalg.norm(attach[j][0] - p3)) < r_min:
                                        hit = True
                                        break
                                if hit:
                                    break
                            if hit:
                                break
                        if hit:
                            break
                    if not hit or a[3] not in seen_branch:
                        kept.append(int(oi))
                        seen_branch.add(a[3])
                        grid.setdefault(key, []).append(int(oi))
                kept.sort()
                attach = [attach[i] for i in kept]

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
        leaf_positions = []      # (pos, stem_dir, x, attach_id)
        for aidx, (pos, tan, x, _bi) in enumerate(attach):
            n1, n2 = _ortho_frame(tan)
            if drape:
                # strand hangs from the branch; the TOP card sits at the
                # branch so the strand is always visually anchored
                step = strand_len * rng.uniform(0.75, 1.05) / cards_per
                for c in range(cards_per):
                    z = pos - np.array([0.0, 0.0, step * c])
                    leaf_positions.append((z, tan, x, aidx))
            else:
                for _ in range(clump):
                    off = (n1 * rng.uniform(-1, 1) + n2 * rng.uniform(-1, 1)
                           + tan * rng.uniform(-0.6, 0.6)) * clump_r
                    leaf_positions.append((pos + off, tan, x, aidx))

        # tip caps: 3 jittered cards straddling each branch tip so the end
        # point is fully buried (never draped, never thinned).  Jitter by a
        # fraction of the leaf size so the cards overlap the tip.
        leaf_w = (leaf_maps[0].size[0] * K) if leaf_maps else 0.08 * K
        for ti, (tip, ttan) in enumerate(tip_caps):
            aid = len(attach) + ti
            if drape:
                # weeping trees: the tip cap is a hanging strand too — flat
                # cards at twig ends read as stubby tips and kill the
                # curtain fringe past the leaf window
                step = strand_len * rng.uniform(0.75, 1.05) / cards_per
                for c in range(cards_per):
                    z = tip - np.array([0.0, 0.0, step * c])
                    leaf_positions.append((z, ttan, 1.0, aid))
            else:
                tn1, tn2 = _ortho_frame(ttan)
                for _ in range(3):
                    jit = (tn1 * rng.uniform(-0.4, 0.4)
                           + tn2 * rng.uniform(-0.4, 0.4)) * leaf_w
                    leaf_positions.append((tip + jit, ttan, 1.0, aid))

        # foliage floor: no card CENTER may sit below the crown's real
        # underside.  Use the 5th PERCENTILE of attachment heights — a
        # single rogue down-swung twig otherwise drags the floor to the
        # ground and lets its clump sit at the trunk foot (black locust).
        # Exempt drape trees — willow strands hang below their branches by
        # design.
        if attach and not drape:
            az_ = [a[0][2] for a in attach]
            floor_z = float(np.percentile(az_, 5.0))
            # only trees whose crown genuinely starts well off the ground
            # get floored — a shrub's foliage legitimately reaches its base
            if floor_z > 0.12 * max(az_):
                leaf_positions = [lp for lp in leaf_positions
                                  if lp[0][2] >= floor_z]

        # cull FLOATING GROUPS: an attachment group (clump or tip cap)
        # with no OTHER group's centre within ~2.4 leaf widths renders as
        # a detached clump floating off the crown.  Per-card counting
        # missed these — a 3-card clump (or two adjacent tip caps) is its
        # own neighbourhood.  (Draped strands are exempt — a hanging
        # curtain is legitimately sparse.)
        if leaf_positions and not drape:
            leaf_positions = _cull_isolated_groups(leaf_positions, leaf_w * 2.4)

        # canopy centre for outward normals
        if leaf_positions:
            centre = np.mean([lp[0] for lp in leaf_positions], axis=0)

        # UV source: composite-map quads (section 10002) when present — the
        # actual shipped leaf DDS is the composite; per-map quads crop it.
        # Quads are indexed by ORIGINAL leaf-map position.
        map_indices = [i for i, m in enumerate(tree.leaf_maps) if m in leaf_maps]
        use_composite = len(tree.leaf_quads) >= len(tree.leaf_maps) > 0
        _FULL_QUAD = (1.0, 1.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0)   # TR TL BL BR, TGA v-up

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

        # Blossom rules (SPT 3000/3002 + per-map 4000 flag): blossom maps
        # (dogwood flowers, azalea blooms) only appear past blossom_distance
        # along the branch and take blossom_weight of the eligible picks;
        # ordinary leaf maps share the rest uniformly.  The pick is made
        # ONCE PER ATTACHMENT (cached by attach id), so a blossom shows as
        # a coherent multi-card cluster like the engine's render — a
        # per-card pick scattered lone flower specks that vanished behind
        # the much larger leaf cards.
        blossom_ks = [i for i, m in enumerate(leaf_maps) if m.blossom]
        normal_ks = [i for i, m in enumerate(leaf_maps) if not m.blossom]
        b_weight = float(np.clip(tree.leaf_blossom_weight, 0.0, 1.0))
        b_dist = float(np.clip(tree.leaf_blossom_distance, 0.0, 1.0))
        pick_cache = {}

        for pos, tan, x, aidx in leaf_positions:
            k = pick_cache.get(aidx)
            if k is None:
                if blossom_ks and normal_ks:
                    if x >= b_dist and rng.random() < b_weight:
                        k = blossom_ks[int(rng.integers(0, len(blossom_ks)))]
                    else:
                        k = normal_ks[int(rng.integers(0, len(normal_ks)))]
                else:
                    k = int(rng.integers(0, len(leaf_maps)))
                pick_cache[aidx] = k
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

    uv_quad: 8 floats, texture corners TC0..TC3 = TR, TL, BL, BR in TGA
    space, where v runs UP (composite-map convention from SPT section 10002;
    corner layout per the FORMAT doc's embedded-texcoords dialog).  DDS v
    runs DOWN, so sampling uses v_dds = 1 - v_tga — the SpeedTreeRT
    "texture flip" ck-cmd enables before Compute().  Getting this wrong
    swaps vertically-stacked atlas crops (dogwood rendered flowers where
    its leaves should be: leaves live in the atlas' DDS bottom half).
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

    # pivot offset from texture origin: origin (u,v) is the attachment point,
    # authored in TGA space (v runs up)
    ou = 0.5 - float(leaf_map.origin[0])
    ovv = 0.5 - float(leaf_map.origin[1])

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
        # card corners are BL, BR, TR, TL; uv_quad is TR, TL, BL, BR
        # (TGA v-up), flipped into DDS v-down space
        q = uv_quad
        uvs = np.asarray([(q[4], 1.0 - q[5]), (q[6], 1.0 - q[7]),
                          (q[0], 1.0 - q[1]), (q[2], 1.0 - q[3])], np.float32)
        col = np.ones((4, 4), np.float32)
        col[:, 0] = leaf_map.color[0]
        col[:, 1] = leaf_map.color[1]
        col[:, 2] = leaf_map.color[2]
        col[:, 3] = 1.0     # full wind weight on leaves
        tris = np.asarray([(0, 1, 2), (0, 2, 3)], np.int32) + base
        g['v'].append(vs); g['n'].append(ns); g['uv'].append(uvs)
        g['c'].append(col); g['t'].append(tris)
        base += 4
