"""Body-wrap armor fitting: exact fit onto the Skyrim body without clipping.

The FK animation retarget (skin_retarget Phase B) poses Oblivion armor ~90%
of the way to the Skyrim rest pose and is locally SMOOTH (it is ordinary
skinning), but it lands near — not on — the Skyrim body: armor floats or
sinks by 0.5-2.5 units, which is exactly the clipping seen in game.

This module measures that FK error EXACTLY and cancels it:

BUILD (offline, `python -m asset_convert.body_wrap`):
  1. Load the Oblivion body part meshes (upperbody/lowerbody/hand/foot) in
     T-pose — the surfaces all Oblivion armor was modelled around.
  2. FK-pose a copy with the very same retarget the armor gets (fkp).
  3. Fit the posed body EXACTLY onto the real Skyrim body surfaces — BOTH
     weight-slider targets (malebody_0 AND malebody_1, hands, feet):
     iterative closest-point projection with
     normal-agreement filtering, the per-step displacement smoothed over the
     welded mesh graph (topology-aware — never bleeds between the legs), plus
     limb-segment length rescaling so wrists/ankles land right (dst).
  4. Save src (T-pose), fkp, dst0/dst1, triangles, and per-vertex
     skin-weight bone centroids to generated/body_wrap_{gender}.npz.  The
     dual dst targets give converted armor true _0/_1 weight-morph variants
     (vanilla ARMA weight-slider convention).

APPLY (runtime, called from skin_retarget.retarget_skin_to_skyrim):
  1. Run the normal FK deform (unchanged — provides the smooth base).
  2. For every armor vertex, interpolate the correction field
     delta = dst - fkp from the FK-posed body surface: Gaussian blend over
     the K nearest body triangles (distance + skin-weight bone-centroid
     gating + wrong-side penalty), evaluating each candidate's delta at the
     closest surface point via barycentric interpolation.
  3. v' = v_fk + blended delta.  Near the body this lands armor at its
     authored clearance from the Skyrim body (measured error dmean ~0);
     away from the body the normalized blend extrapolates the regional
     correction as a constant — replacing the old hand-tuned
     ARMOR_PIECE_OFFSETS drift compensation entirely.

Because the correction is a smooth, slowly-varying translation field, armor
keeps FK's local mesh quality (no crumpling, no vertex explosions, UV-seam
twins move identically), while the residual body clipping is cancelled.
"""

from pathlib import Path

import numpy as np

# Apply all PyFFI patches (time.clock fix, nif.xml condition fixes) before import
from . import pyffi_monkey_patch as _patch  # noqa: F401

try:
    from pyffi.formats.nif import NifFormat
    _PYFFI = True
except ImportError:
    _PYFFI = False

from .skyrim_overrides import OBLIVION_TO_SKYRIM_BONE_MAP

_REPO = Path(__file__).parent.parent
_GEN_DIR = Path(__file__).parent / 'generated'
_OB_BODY_DIR = _REPO / 'export' / 'Oblivion.esm' / 'meshes' / 'characters' / '_male'
_SK_BODY_DIR = (_REPO / 'references' / 'Skyrim Meshes' / 'meshes' /
                'actors' / 'character' / 'character assets')

# Oblivion body parts per gender, grouped by which Skyrim target surface they
# fit onto.  src tag makes retarget_skin_to_skyrim pick the right skeleton.
_OB_BODY_SETS = {
    'male': ({'body':  ['upperbody.nif', 'lowerbody.nif'],
              'hands': ['hand.nif'],
              'feet':  ['foot.nif']}, 'armor/m/'),
    'female': ({'body':  ['femaleupperbody.nif', 'femalelowerbody.nif'],
                'hands': ['femalehand.nif'],
                'feet':  ['femalefoot.nif']}, 'armor/f/'),
}
# Skyrim target bodies exist as _0 (thin) / _1 (heavy) weight-slider pairs;
# a field is fitted against each so armor gets true _0/_1 morph variants.
_SK_BODY_SETS = {
    'male':   {'body': 'malebody', 'hands': 'malehands', 'feet': 'malefeet'},
    'female': {'body': 'femalebody', 'hands': 'femalehands',
               'feet': 'femalefeet'},
}

# ---- build parameters ------------------------------------------------------
_FIT_PHASES = ((30, 8, 0.5), (20, 2, 0.7))  # (iterations, smooth passes, step)
_PROJ_K = 8              # candidate triangles per projection query
_NORMAL_DOT_MIN = 0.1    # reject target tris facing away during projection
_WELD_TOL = 1e-3         # coincident-vertex weld tolerance (UV seam twins)

# Long-bone segments rescaled along their axis before surface fitting so that
# wrist/ankle rings land near the Skyrim wrist/ankle (fixes the bone-length
# residual FK cannot express).  Twist bones ride their parent segment.
# Fit-initialisation only — armor never gets axis-scaled.
_SCALE_SEGMENTS = [
    ('Bip01 L UpperArm', 'Bip01 L Forearm', ('Bip01 L UpperArmTwist',)),
    ('Bip01 R UpperArm', 'Bip01 R Forearm', ('Bip01 R UpperArmTwist',)),
    ('Bip01 L Forearm', 'Bip01 L Hand', ('Bip01 L ForearmTwist',)),
    ('Bip01 R Forearm', 'Bip01 R Hand', ('Bip01 R ForearmTwist',)),
    ('Bip01 L Thigh', 'Bip01 L Calf', ()),
    ('Bip01 R Thigh', 'Bip01 R Calf', ()),
    ('Bip01 L Calf', 'Bip01 L Foot', ()),
    ('Bip01 R Calf', 'Bip01 R Foot', ()),
]

# ---- apply parameters ------------------------------------------------------
K_CAND = 40              # body triangles blended per armor vertex (large so
                         # gap vertices — robe panel between the legs — see
                         # BOTH sides and average instead of flip-flopping)
SIGMA_BONE = 7.0         # bone-centroid Gaussian (units) — region gating
SIDE_GAMMA = -0.75       # signed distance below which a candidate is inside
SIDE_PENALTY = 0.03
# Minimum clearance enforcement: armor must end up at least its AUTHORED
# clearance from the fitted Skyrim body plus this outward margin (game units).
# Cancels residual field noise (female chest) at the cost of a slightly
# looser fit — clipping is far more visible than half a unit of looseness.
CLEAR_MARGIN = 1.0
CLEAR_MARGIN_RANGE = 8.0   # margin fades out by this authored clearance
CLEAR_INNER_FADE = 0.5     # the outward margin dies off by this depth for
                           # verts authored INSIDE the OB body (shirt collars/
                           # necklines sit against the chest at c0 ~ -0.6..-1.5).
                           # Their authored DEPTH is still preserved (target =
                           # c0): excluding them entirely let the field drag
                           # collars 2+ units deeper -> jagged skin-through-
                           # fabric neckline clipping
CLEAR_PROX = 4.0           # enforcement fades out by this authored clearance:
                           # only near-body verts can poke through skin, and
                           # far away the two clearance estimators diverge.
                           # 2.5 was too tight: shirt collars (authored 2-3
                           # off the neck) and the cuirass front fauld (3.4)
                           # ended up inside the body with enforcement faded
                           # to <25% strength
PUSH_SMOOTH_PASSES = 8     # deficit diffusion over the armor mesh graph
PUSH_CAP = 2.0             # per-vertex push hard limit (game units)
PUSH_RAW_KEEP = 0.6        # fraction of the RAW (undiffused) deficit kept as
                           # a floor under the diffused value: diffusion kills
                           # per-vertex noise but also diluted genuine isolated
                           # deficits (shirt collar ring 0.9-1.9 deep) into
                           # surrounding slack.  Raw deficits are already
                           # gated by rel/prox, so the floor is safe.
PUSH_ITERS = 2             # enforcement passes: one push rarely lands exactly
                           # on target (c1 is re-estimated after moving), a
                           # second pass converges deep deficits (collar backs)
CLEAR_K = 24               # triangles per clearance query.  12 was too few at
                           # the wrist: cuff verts saw ONLY hand triangles
                           # (which abstain from reliability) and never the
                           # body's wrist ring, so cuffs kept rel=0/no rescue
# Fit-reliability floor on BODY triangles.  Without it, enforcement dies
# exactly at the wrist and neck seam rings (the fit bunches there, stretch
# reliability -> 0), which is where shirt cuffs and collars kept clipping.
# Hand/foot triangles stay hard-masked to 0 (gauntlets/boots replace them).
REL_FLOOR = 0.4
# Skin weight on this bone marks head gear: the field has no head surface,
# so corrections interpolated from neck/shoulder triangles would drag helmets
# into the middle of the head.  Head-weighted vertices keep the plain FK
# result and the legacy ARMOR_PIECE_OFFSETS helmet offset (see nif_converter).
# Head ONLY — the OB body upperbody mesh includes the neck, so Neck/Neck1
# regions have real field coverage and gating them regresses cuirass collars.
HEAD_BONES = ('Bip01 Head',)
# Correction-field smoothing at load (body-graph Jacobi passes).  Sweep on
# iron cuirass/gauntlets/boots (2026-07-10): more passes monotonically lowers
# armor edge distortion but slowly reintroduces clipping; 12 = best tradeoff
# (gauntlets 5.8% edges >15% / 1.0% clipped verts; 8:7.8%/0.6%, 16:5.1%/1.7%).
DELTA_SMOOTH_PASSES = 12

_FIELD_CACHE: dict = {}


# ---------------------------------------------------------------------------
# Shared geometry helpers
# ---------------------------------------------------------------------------

def closest_point_on_triangles(p, a, b, c):
    """Vectorised closest point on triangle (Ericson).  All args (..., 3)."""
    ab = b - a
    ac = c - a
    ap = p - a
    d1 = np.einsum('...i,...i->...', ab, ap)
    d2 = np.einsum('...i,...i->...', ac, ap)
    bp = p - b
    d3 = np.einsum('...i,...i->...', ab, bp)
    d4 = np.einsum('...i,...i->...', ac, bp)
    cp = p - c
    d5 = np.einsum('...i,...i->...', ab, cp)
    d6 = np.einsum('...i,...i->...', ac, cp)
    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2

    denom = va + vb + vc
    denom = np.where(np.abs(denom) < 1e-12, 1.0, denom)
    v = (vb / denom)[..., None]
    w = (vc / denom)[..., None]
    res = a + v * ab + w * ac                                    # interior

    m = (va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0)          # edge BC
    div = (d4 - d3) + (d5 - d6)
    t = ((d4 - d3) / np.where(np.abs(div) < 1e-12, 1.0, div))[..., None]
    res = np.where(m[..., None], b + t * (c - b), res)

    m = (vb <= 0) & (d2 >= 0) & (d6 <= 0)                        # edge AC
    div = d2 - d6
    t = (d2 / np.where(np.abs(div) < 1e-12, 1.0, div))[..., None]
    res = np.where(m[..., None], a + t * ac, res)

    m = (vc <= 0) & (d1 >= 0) & (d3 <= 0)                        # edge AB
    div = d1 - d3
    t = (d1 / np.where(np.abs(div) < 1e-12, 1.0, div))[..., None]
    res = np.where(m[..., None], a + t * ab, res)

    res = np.where(((d6 >= 0) & (d5 <= d6))[..., None], c, res)  # vertex C
    res = np.where(((d3 >= 0) & (d4 <= d3))[..., None], b, res)  # vertex B
    res = np.where(((d1 <= 0) & (d2 <= 0))[..., None], a, res)   # vertex A
    return res


def weld_groups(verts: np.ndarray, tol: float = _WELD_TOL) -> np.ndarray:
    """Group coincident vertices (UV-seam twins). Returns group id per vertex.

    True distance-based welding (KDTree pairs + union-find), not grid
    rounding: seam twins that land on opposite sides of a rounding boundary
    (e.g. after per-block world transforms differing by float error) must
    still weld, or the two copies receive different corrections and the seam
    visibly splits."""
    from scipy.spatial import cKDTree
    n = len(verts)
    parent = np.arange(n)

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    pairs = cKDTree(verts).query_pairs(tol, output_type='ndarray')
    for i, j in pairs:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri
    roots = np.fromiter((find(i) for i in range(n)), dtype=np.int64, count=n)
    _, inv = np.unique(roots, return_inverse=True)
    return inv


def _group_mean(values: np.ndarray, group: np.ndarray, n_groups: int):
    """Mean of `values` (N, D) per weld group -> (G, D)."""
    sums = np.zeros((n_groups, values.shape[1]), dtype=np.float64)
    np.add.at(sums, group, values)
    counts = np.bincount(group, minlength=n_groups).astype(np.float64)
    return sums / np.maximum(counts, 1.0)[:, None]


def _vertex_normals(verts, tris, group, n_groups):
    """Area-weighted vertex normals accumulated over weld groups."""
    fn = np.cross(verts[tris[:, 1]] - verts[tris[:, 0]],
                  verts[tris[:, 2]] - verts[tris[:, 0]])
    acc = np.zeros((n_groups, 3), dtype=np.float64)
    for k in range(3):
        np.add.at(acc, group[tris[:, k]], fn)
    ln = np.linalg.norm(acc, axis=1, keepdims=True)
    acc /= np.maximum(ln, 1e-12)
    return acc[group]


def _build_adjacency(tris, group, n_groups):
    """Weld-group adjacency as (nbr_idx, nbr_ptr) CSR arrays."""
    e = np.vstack([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [0, 2]]])
    ge = group[e]
    ge = ge[ge[:, 0] != ge[:, 1]]
    ge = np.vstack([ge, ge[:, ::-1]])
    ge = np.unique(ge, axis=0)
    nbr_idx = ge[:, 1]
    nbr_ptr = np.zeros(n_groups + 1, dtype=np.int64)
    counts = np.bincount(ge[:, 0], minlength=n_groups)
    nbr_ptr[1:] = np.cumsum(counts)
    return nbr_idx, nbr_ptr


def _smooth_group_field(field_g, nbr_idx, nbr_ptr, iters, lam=0.5):
    """Jacobi smoothing of a per-group vector field over the mesh graph."""
    counts = np.maximum(np.diff(nbr_ptr), 1).astype(np.float64)
    src_of_edge = np.repeat(np.arange(len(counts)), np.diff(nbr_ptr))
    for _ in range(iters):
        nbr_sum = np.zeros_like(field_g)
        np.add.at(nbr_sum, src_of_edge, field_g[nbr_idx])
        nbr_mean = nbr_sum / counts[:, None]
        field_g = (1.0 - lam) * field_g + lam * nbr_mean
    return field_g


# ---------------------------------------------------------------------------
# NIF reading helpers (build side)
# ---------------------------------------------------------------------------

def _read_nif(path):
    data = NifFormat.Data()
    with open(path, 'rb') as f:
        data.read(f)
    return data


def _block_name(block) -> str:
    return bytes(block.name).rstrip(b'\x00').decode('latin-1', errors='replace')


def _iter_skinned_geoms(data):
    """Yield (block, skel_root) for every skinned NiTriShape/Strips."""
    for root in data.roots:
        if root is None:
            continue
        skel_root = None
        for block in root.tree():
            skin = getattr(block, 'skin_instance', None)
            if skin is not None and skin.skeleton_root is not None:
                skel_root = skin.skeleton_root
                break
        if skel_root is None:
            skel_root = root
        for block in root.tree():
            if not isinstance(block, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
                continue
            skin = getattr(block, 'skin_instance', None)
            if skin is None or skin.data is None:
                continue
            if block.data is None or block.data.num_vertices == 0:
                continue
            yield block, skel_root


def _geom_world(block, skel_root):
    from .skin_retarget import _m44_to_np
    try:
        G = _m44_to_np(block.get_transform(skel_root))
    except (ValueError, RuntimeError):
        G = np.eye(4)
    verts = np.array([[v.x, v.y, v.z] for v in block.data.vertices],
                     dtype=np.float64)
    if not np.allclose(G, np.eye(4), atol=1e-6):
        verts = verts @ G[:3, :3] + G[3, :3]
    return verts, G


def _geom_triangles(block) -> np.ndarray:
    d = block.data
    if hasattr(d, 'triangles') and d.num_triangles:
        return np.array([[t.v_1, t.v_2, t.v_3] for t in d.triangles],
                        dtype=np.int64)
    if hasattr(d, 'get_triangles'):  # NiTriStrips
        return np.array(d.get_triangles(), dtype=np.int64)
    return np.zeros((0, 3), dtype=np.int64)


def _geom_bone_weights(block) -> dict:
    """{bone_name: (indices, weights)} from NiSkinData."""
    skin = block.skin_instance
    sd = skin.data
    out: dict = {}
    for bi in range(min(skin.num_bones, sd.num_bones)):
        bone = skin.bones[bi]
        if bone is None:
            continue
        name = _block_name(bone)
        be = sd.bone_list[bi]
        idx = np.fromiter((vw.index for vw in be.vertex_weights),
                          dtype=np.int64, count=be.num_vertices)
        w = np.fromiter((vw.weight for vw in be.vertex_weights),
                        dtype=np.float64, count=be.num_vertices)
        if name in out:
            pi, pw = out[name]
            idx = np.concatenate([pi, idx])
            w = np.concatenate([pw, w])
        out[name] = (idx, w)
    return out


# ---------------------------------------------------------------------------
# Field construction (offline)
# ---------------------------------------------------------------------------

def _load_ob_group(gender: str):
    """Read OB body parts.  Returns per-group dict:
       {group: {'v0': (N,3) T-pose verts, 'tris': (M,3), 'bones': {name:(idx,w)}}}"""
    sets, _tag = _OB_BODY_SETS[gender]
    groups: dict = {}
    for group, names in sets.items():
        v_parts, t_parts, bone_acc = [], [], {}
        offset = 0
        for name in names:
            path = _OB_BODY_DIR / name
            if not path.exists():
                print(f'  [{gender}] missing OB body mesh: {path}')
                continue
            data = _read_nif(path)
            for block, skel_root in _iter_skinned_geoms(data):
                verts, _G = _geom_world(block, skel_root)
                tris = _geom_triangles(block)
                v_parts.append(verts)
                t_parts.append(tris + offset)
                for bone, (idx, w) in _geom_bone_weights(block).items():
                    bone_acc.setdefault(bone, []).append((idx + offset, w))
                offset += len(verts)
        if v_parts:
            groups[group] = {
                'v0': np.vstack(v_parts),
                'tris': np.vstack(t_parts),
                'bones': {b: (np.concatenate([c[0] for c in ch]),
                              np.concatenate([c[1] for c in ch]))
                          for b, ch in bone_acc.items()},
            }
    return groups


def _fk_pose_group(gender: str):
    """FK-retarget the OB body parts (exactly what armor gets); return
    {group: (N,3) posed verts} in _load_ob_group's concatenation order."""
    from .skin_retarget import retarget_skin_to_skyrim
    sets, tag = _OB_BODY_SETS[gender]
    posed: dict = {}
    for group, names in sets.items():
        v_parts = []
        for name in names:
            path = _OB_BODY_DIR / name
            if not path.exists():
                continue
            data = _read_nif(path)
            retarget_skin_to_skyrim(data, src_path=tag + name, allow_wrap=False)
            for block, skel_root in _iter_skinned_geoms(data):
                verts, _G = _geom_world(block, skel_root)
                v_parts.append(verts)
        if v_parts:
            posed[group] = np.vstack(v_parts)
    return posed


def _load_sk_surface(gender: str, group: str, weight: int):
    """Skyrim target surface for a group+weight: (verts (N,3), tris (M,3))."""
    path = _SK_BODY_DIR / f'{_SK_BODY_SETS[gender][group]}_{weight}.nif'
    if not path.exists():
        return None
    data = _read_nif(path)
    v_parts, t_parts = [], []
    offset = 0
    for block, skel_root in _iter_skinned_geoms(data):
        verts, _G = _geom_world(block, skel_root)
        v_parts.append(verts)
        t_parts.append(_geom_triangles(block) + offset)
        offset += len(verts)
    if not v_parts:
        return None
    return np.vstack(v_parts), np.vstack(t_parts)


def _segment_scale(verts, bones, ob_skel, sk_skel):
    """Longitudinally rescale limb segments (FK-posed space) so segment
    lengths match the Skyrim skeleton.  Blended by skin weights."""
    nv = len(verts)
    acc = np.zeros_like(verts)
    wsum = np.zeros(nv)
    for parent, child, riders in _SCALE_SEGMENTS:
        sk_p = OBLIVION_TO_SKYRIM_BONE_MAP.get(parent)
        sk_c = OBLIVION_TO_SKYRIM_BONE_MAP.get(child)
        if (parent not in ob_skel or child not in ob_skel
                or sk_p not in sk_skel or sk_c not in sk_skel):
            continue
        ob_len = np.linalg.norm(ob_skel[child][3, :3] - ob_skel[parent][3, :3])
        sk_head = sk_skel[sk_p][3, :3]
        sk_vec = sk_skel[sk_c][3, :3] - sk_head
        sk_len = np.linalg.norm(sk_vec)
        if ob_len < 1e-3 or sk_len < 1e-3:
            continue
        axis = sk_vec / sk_len
        s = sk_len / ob_len
        idx_list, w_list = [], []
        for bn in (parent,) + riders:
            if bn in bones:
                bi, bw = bones[bn]
                idx_list.append(bi)
                w_list.append(bw)
        if not idx_list:
            continue
        idx = np.concatenate(idx_list)
        w = np.concatenate(w_list)
        rel = verts[idx] - sk_head
        along = rel @ axis
        moved = verts[idx] + np.outer(along * (s - 1.0), axis)
        np.add.at(acc, idx, w[:, None] * moved)
        np.add.at(wsum, idx, w)
    has = wsum > 1e-6
    out = verts.copy()
    frac = np.minimum(wsum[has], 1.0)[:, None]
    out[has] = (acc[has] / wsum[has][:, None]) * frac + verts[has] * (1.0 - frac)
    return out


def _project_points(points, normals, sk_verts, sk_tris, sk_tree, sk_tri_n):
    """Closest point on the SK surface for each input point.
    Normal-agreement filtered; falls back to plain nearest.  Returns (P,3)."""
    k = min(_PROJ_K, len(sk_tris))
    _, cand = sk_tree.query(points, k=k)
    if k == 1:
        cand = cand[:, None]
    a = sk_verts[sk_tris[cand, 0]]
    b = sk_verts[sk_tris[cand, 1]]
    c = sk_verts[sk_tris[cand, 2]]
    cp = closest_point_on_triangles(points[:, None, :], a, b, c)
    d = np.linalg.norm(cp - points[:, None, :], axis=2)
    agree = np.einsum('pki,pi->pk', sk_tri_n[cand], normals) > _NORMAL_DOT_MIN
    d_f = np.where(agree, d, np.inf)
    no_valid = ~np.isfinite(d_f).any(axis=1)
    if no_valid.any():
        d_f[no_valid] = d[no_valid]
    best = np.argmin(d_f, axis=1)
    return cp[np.arange(len(points)), best]


def build_field(gender: str, verbose: bool = True) -> bool:
    """Build + save the wrap field for one gender.  Returns success."""
    from scipy.spatial import cKDTree
    from .skin_retarget import (_load_skeleton, _SKEL_OBLIVION,
                                _SKEL_SKYRIM_MALE, _SKEL_SKYRIM_FEMALE)

    ob_skel = _load_skeleton(_SKEL_OBLIVION)
    sk_skel = _load_skeleton(
        _SKEL_SKYRIM_FEMALE if gender == 'female' else _SKEL_SKYRIM_MALE)
    if not ob_skel or not sk_skel:
        print(f'  [{gender}] skeleton JSONs missing — cannot build')
        return False

    groups = _load_ob_group(gender)
    posed = _fk_pose_group(gender)
    if not groups or set(groups) != set(posed):
        print(f'  [{gender}] OB body meshes missing — cannot build')
        return False

    all_src, all_fkp, all_tris, all_bc, all_part = [], [], [], [], []
    all_dst = {0: [], 1: []}
    offset = 0

    for group, gd in groups.items():
        v0 = gd['v0']
        tris = gd['tris']
        fk_raw = posed[group]
        if len(fk_raw) != len(v0):
            print(f'  [{gender}/{group}] vert count mismatch T-pose vs FK')
            return False

        # segment scaling is fit INITIALISATION only; the stored FK-posed
        # verts (fkp) stay raw — they must match what armor FK produces.
        seed = _segment_scale(fk_raw.copy(), gd['bones'], ob_skel, sk_skel)

        wg = weld_groups(v0)
        n_g = int(wg.max()) + 1
        nbr_idx, nbr_ptr = _build_adjacency(tris, wg, n_g)

        for wt in (0, 1):
            sk = _load_sk_surface(gender, group, wt)
            if sk is None:
                print(f'  [{gender}/{group}] missing SK target surface _{wt}')
                return False
            sk_v, sk_t = sk
            sk_cent = sk_v[sk_t].mean(axis=1)
            sk_tri_n = np.cross(sk_v[sk_t[:, 1]] - sk_v[sk_t[:, 0]],
                                sk_v[sk_t[:, 2]] - sk_v[sk_t[:, 0]])
            sk_tri_n /= np.maximum(
                np.linalg.norm(sk_tri_n, axis=1, keepdims=True), 1e-12)
            sk_tree = cKDTree(sk_cent)

            cur = seed.copy()
            for iters, smooth_n, step in _FIT_PHASES:
                for _ in range(iters):
                    vn = _vertex_normals(cur, tris, wg, n_g)
                    tgt = _project_points(cur, vn, sk_v, sk_t, sk_tree,
                                          sk_tri_n)
                    delta_g = _group_mean(tgt - cur, wg, n_g)
                    delta_g = _smooth_group_field(delta_g, nbr_idx, nbr_ptr,
                                                  smooth_n)
                    cur = cur + step * delta_g[wg]

            # residual: how exactly the fitted body sits on the SK surface
            vn = _vertex_normals(cur, tris, wg, n_g)
            proj = _project_points(cur, vn, sk_v, sk_t, sk_tree, sk_tri_n)
            res = np.linalg.norm(proj - cur, axis=1)
            corr = np.linalg.norm(cur - fk_raw, axis=1)
            if verbose:
                print(f'  [{gender}/{group}/_{wt}] {len(v0)} verts: surface '
                      f'residual mean={res.mean():.3f} '
                      f'p95={np.percentile(res, 95):.3f}; '
                      f'FK correction mean={corr.mean():.2f} '
                      f'p95={np.percentile(corr, 95):.2f} max={corr.max():.2f}')
            all_dst[wt].append(cur)

        # per-vertex bone centroid (region gate for candidate matching)
        bc = np.zeros_like(v0)
        bw_sum = np.zeros(len(v0))
        for bone, (idx, w) in gd['bones'].items():
            if bone not in ob_skel:
                continue
            head = ob_skel[bone][3, :3]
            np.add.at(bc, idx, np.outer(w, head))
            np.add.at(bw_sum, idx, w)
        has = bw_sum > 1e-6
        bc[has] /= bw_sum[has][:, None]
        bc[~has] = v0[~has]

        all_src.append(v0)
        all_fkp.append(fk_raw)
        all_tris.append(tris + offset)
        all_bc.append(bc)
        # part id per vertex: clearance is only ENFORCED against the body
        # part — gauntlets/boots replace the body's hands/feet in Skyrim, and
        # the fitted hand/foot surfaces are the least reliable
        all_part.append(np.full(len(v0), 0 if group == 'body' else 1,
                                dtype=np.int32))
        offset += len(v0)

    _GEN_DIR.mkdir(parents=True, exist_ok=True)
    out = _GEN_DIR / f'body_wrap_{gender}.npz'
    np.savez_compressed(
        out,
        src=np.vstack(all_src).astype(np.float32),
        fkp=np.vstack(all_fkp).astype(np.float32),
        dst0=np.vstack(all_dst[0]).astype(np.float32),
        dst1=np.vstack(all_dst[1]).astype(np.float32),
        tris=np.vstack(all_tris).astype(np.int32),
        vert_bc=np.vstack(all_bc).astype(np.float32),
        part=np.concatenate(all_part))
    if verbose:
        print(f'  [{gender}] saved {out.name}: {offset} verts, '
              f'{sum(len(t) for t in all_tris)} tris')
    return True


def build_all_fields(verbose: bool = True) -> int:
    n = 0
    for gender in ('male', 'female'):
        try:
            if build_field(gender, verbose=verbose):
                n += 1
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f'  [{gender}] wrap field build failed: {e}')
    return n


# ---------------------------------------------------------------------------
# Runtime field
# ---------------------------------------------------------------------------

class WrapField:
    """Loaded wrap field: FK-posed body surface + smoothed correction fields.

    Weight-indexed members (lists [w0, w1]) carry the _0 (thin) and _1
    (heavy) Skyrim body targets; everything Oblivion-side is single."""

    def __init__(self, z):
        from scipy.spatial import cKDTree
        self.src = z['src'].astype(np.float64)     # T-pose verts (metrics)
        fkp = z['fkp'].astype(np.float64)
        dst_w = [z['dst0'].astype(np.float64), z['dst1'].astype(np.float64)]
        tris = z['tris'].astype(np.int64)
        vert_bc = z['vert_bc'].astype(np.float64)
        part = z['part'].astype(np.int64)

        # Smooth the correction field over the body graph: the fit's residual
        # high-frequency noise (tangential bunching, per-triangle projection
        # jitter) must not imprint on armor.  The smoothing error it costs
        # against the exact fitted surface (~0.3 units mean) is unbiased and
        # does not reintroduce systematic clipping — measured armor clearance
        # error stays ~0 with newclip <1%.
        wg = weld_groups(fkp)
        n_g = int(wg.max()) + 1
        nbr_idx, nbr_ptr = _build_adjacency(tris, wg, n_g)
        self.delta = []
        for dst in dst_w:
            delta_g = _group_mean(dst - fkp, wg, n_g)
            delta_g = _smooth_group_field(delta_g, nbr_idx, nbr_ptr,
                                          DELTA_SMOOTH_PASSES)
            self.delta.append(delta_g[wg])         # (N,3) per body vertex

        # drop degenerate triangles (zero area in FK pose)
        n = np.cross(fkp[tris[:, 1]] - fkp[tris[:, 0]],
                     fkp[tris[:, 2]] - fkp[tris[:, 0]])
        area2 = np.linalg.norm(n, axis=1)
        good = area2 > 1e-8
        self.tris = tris[good]
        self.fkp = fkp
        self.tri_n = n[good] / area2[good][:, None]
        self.tri_bc = vert_bc[self.tris].mean(axis=1)
        self.tree = cKDTree(fkp[self.tris].mean(axis=1))

        # T-pose (authored) and fitted surfaces for clearance enforcement
        def _tri_normals(v):
            tn = np.cross(v[self.tris[:, 1]] - v[self.tris[:, 0]],
                          v[self.tris[:, 2]] - v[self.tris[:, 0]])
            ln = np.linalg.norm(tn, axis=1, keepdims=True)
            return tn / np.maximum(ln, 1e-12)
        self.src_tri_n = _tri_normals(self.src)
        self.src_tree = cKDTree(self.src[self.tris].mean(axis=1))
        self.dst = dst_w
        self.dst_tri_n = [_tri_normals(d) for d in dst_w]
        self.dst_tree = [cKDTree(d[self.tris].mean(axis=1)) for d in dst_w]

        # Per-triangle fit reliability: 1 where the fitted surface is locally
        # near-isometric to the authored body, low where the fit bunched
        # (fingers, seam rings).  Floored at REL_FLOOR on the body so
        # enforcement never fully dies at the wrist/neck seam rings (shirt
        # cuff + collar clipping); hand/foot triangles are hard-masked to 0
        # (gauntlets/boots replace them and their fit is untrustworthy).
        e = np.vstack([self.tris[:, [0, 1]], self.tris[:, [1, 2]],
                       self.tris[:, [0, 2]]])
        l0 = np.linalg.norm(self.src[e[:, 0]] - self.src[e[:, 1]], axis=1)
        body_tri = part[self.tris].max(axis=1) == 0
        self.tri_rel = []
        for dst in dst_w:
            l1 = np.linalg.norm(dst[e[:, 0]] - dst[e[:, 1]], axis=1)
            stretch = np.abs(l1 / np.maximum(l0, 0.05) - 1.0)
            tri_stretch = stretch.reshape(3, -1).mean(axis=0)
            rel = np.maximum(np.exp(-(tri_stretch / 0.25) ** 2), REL_FLOOR)
            self.tri_rel.append(rel * body_tri)


def _field_path(female: bool) -> Path:
    return _GEN_DIR / f'body_wrap_{"female" if female else "male"}.npz'


def get_field(female: bool):
    """Load (and cache) the wrap field for a gender, or None."""
    key = 'female' if female else 'male'
    if key in _FIELD_CACHE:
        return _FIELD_CACHE[key]
    field = None
    path = _field_path(female)
    if path.exists() and _PYFFI:
        try:
            with np.load(path, allow_pickle=False) as z:
                field = WrapField(z)
        except Exception as e:
            print(f'      [WRAP] failed to load {path.name}: {e}')
            field = None
    _FIELD_CACHE[key] = field
    return field


def wrap_available(src_path: str) -> bool:
    """True when the wrap field for this NIF's gender can be used (the legacy
    FK-drift piece offsets must then be skipped)."""
    female = '/f/' in src_path.replace('\\', '/').lower()
    return get_field(female) is not None


# ---------------------------------------------------------------------------
# Runtime application
# ---------------------------------------------------------------------------

def _field_corrections(field, pts, abc, weight=0):
    """Blended correction vectors for points (P,3) in FK-posed space.

    For each point: K nearest body triangles, per-candidate correction =
    barycentric interpolation of vertex deltas at the closest surface point,
    Gaussian-blended by (surface distance, bone-centroid distance) with a
    wrong-side penalty.  Normalised blending extrapolates the regional
    correction as a constant for far-away points."""
    k = min(K_CAND, len(field.tris))
    _, tri = field.tree.query(pts, k=k)
    if k == 1:
        tri = tri[:, None]

    t = field.tris[tri]                                          # (P,K,3)
    a = field.fkp[t[..., 0]]
    b = field.fkp[t[..., 1]]
    c = field.fkp[t[..., 2]]
    cp = closest_point_on_triangles(pts[:, None, :], a, b, c)
    off = pts[:, None, :] - cp
    d = np.linalg.norm(off, axis=2)                              # (P,K)
    gamma = np.einsum('pki,pki->pk', off, field.tri_n[tri])

    # barycentric coordinates of cp (degenerate-safe: fall back to vert 0)
    ab = b - a
    ac = c - a
    d00 = np.einsum('pki,pki->pk', ab, ab)
    d01 = np.einsum('pki,pki->pk', ab, ac)
    d11 = np.einsum('pki,pki->pk', ac, ac)
    cpa = cp - a
    d20 = np.einsum('pki,pki->pk', cpa, ab)
    d21 = np.einsum('pki,pki->pk', cpa, ac)
    den = d00 * d11 - d01 * d01
    den = np.where(np.abs(den) < 1e-12, 1.0, den)
    bv = np.clip((d11 * d20 - d01 * d21) / den, 0.0, 1.0)
    bw = np.clip((d00 * d21 - d01 * d20) / den, 0.0, 1.0)
    bu = np.clip(1.0 - bv - bw, 0.0, 1.0)
    tot = np.maximum(bu + bv + bw, 1e-12)
    bu, bv, bw = bu / tot, bv / tot, bw / tot

    delta = field.delta[weight]
    delta_cp = (bu[..., None] * delta[t[..., 0]]
                + bv[..., None] * delta[t[..., 1]]
                + bw[..., None] * delta[t[..., 2]])              # (P,K,3)

    d_best = d.min(axis=1)
    sig_d = 0.8 + 0.30 * d_best
    w = np.exp(-((d - d_best[:, None]) ** 2) / (2.0 * sig_d[:, None] ** 2))
    bc_d2 = ((abc[:, None, :] - field.tri_bc[tri]) ** 2).sum(axis=2)
    w *= np.exp(-bc_d2 / (2.0 * SIGMA_BONE ** 2))
    w *= np.where(gamma > SIDE_GAMMA, 1.0, SIDE_PENALTY)
    wsum = w.sum(axis=1)
    dead = wsum < 1e-12
    if dead.any():                       # extreme filter kill: plain nearest
        w[dead] = 0.0
        w[dead, np.argmin(d[dead], axis=1)] = 1.0
        wsum = w.sum(axis=1)
    w = w / wsum[:, None]
    return (w[:, :, None] * delta_cp).sum(axis=1)


def _blended_clearance(field, pts, verts_surf, tri_normals, tree,
                       tri_rel=None, k=12):
    """Smooth signed clearance of pts against a body surface, plus the
    blended outward normal.  Gaussian blend over nearby triangles so the
    result is a smooth field (safe to use for pushing vertices)."""
    k = min(k, len(field.tris))
    _, tri = tree.query(pts, k=k)
    if k == 1:
        tri = tri[:, None]
    t = field.tris[tri]
    a = verts_surf[t[..., 0]]
    b = verts_surf[t[..., 1]]
    c = verts_surf[t[..., 2]]
    cp = closest_point_on_triangles(pts[:, None, :], a, b, c)
    off = pts[:, None, :] - cp
    d = np.linalg.norm(off, axis=2)
    gamma = np.einsum('pki,pki->pk', off, tri_normals[tri])
    d_best = d.min(axis=1)
    sig_d = 1.5 + 0.5 * d_best
    w = np.exp(-((d - d_best[:, None]) ** 2) / (2.0 * sig_d[:, None] ** 2))
    w /= w.sum(axis=1, keepdims=True)
    sign = np.where(gamma >= 0.0, 1.0, -1.0)
    c_out = (w * sign * d).sum(axis=1)
    n_out = (w[:, :, None] * tri_normals[tri]).sum(axis=1)
    ln = np.linalg.norm(n_out, axis=1, keepdims=True)
    n_out /= np.maximum(ln, 1e-12)
    if tri_rel is None:
        rel_out = None
    else:
        # zero-rel triangles (hand/foot parts) ABSTAIN from the reliability
        # vote instead of vetoing it: a sleeve cuff whose neighbourhood is
        # half forearm / half hand must keep the forearm's reliability, or
        # wrist clearance enforcement dies exactly where cuffs clip.  BUT
        # only triangles near the closest surface may vote (d_best + 2):
        # otherwise boot-shaft verts hugging the (abstaining) foot inherit
        # reliability from calf triangles 8+ units away and get pushed
        # around by an estimate that has nothing to do with their surface.
        # Verts with no nearby voting triangles get 0 (protected).
        r = tri_rel[tri]
        voting = w * (r > 0.0) * (d <= (d_best + 2.0)[:, None])
        vsum = voting.sum(axis=1)
        rel_out = (voting * r).sum(axis=1) / np.maximum(vsum, 1e-12)
        rel_out[vsum < 1e-12] = 0.0
    return c_out, n_out, rel_out


def deform_geoms_wrap(skinned_geoms, skel_root, field, female: bool,
                      weight: int = 0) -> int:
    """FK deform + exact body-fit correction for all non-PRN skinned geoms.

    Drop-in replacement for skin_retarget's FK Phase B: runs the standard FK
    animation deform first (smooth base), then cancels its measured error
    against the Skyrim body via the wrap correction field.  `weight` selects
    the _0 (thin) or _1 (heavy) Skyrim body target.

    ALL blocks are solved as ONE system — a single cross-block weld, one
    correction query, one deficit diffusion graph.  Per-block solving split
    armor seams (cuirass/pauldron boundary verts got different corrections
    and visibly came apart).  Returns the number of geometries corrected
    (0 = caller should run plain FK)."""
    from .skin_retarget import (_deform_vertices_animation_fk,
                                _load_animation_deltas, _load_skeleton,
                                _SKEL_OBLIVION, _m44_to_np)
    bone_deltas = _load_animation_deltas()
    if not bone_deltas:
        return 0    # wrap needs the FK base; fall back entirely

    # capture pre-FK (authored T-pose) world verts for clearance enforcement
    pre_fk: dict = {}
    for block, is_prn, _pb in skinned_geoms:
        if is_prn or block.data is None or block.data.num_vertices == 0:
            continue
        try:
            G = _m44_to_np(block.get_transform(skel_root))
        except (ValueError, RuntimeError):
            G = np.eye(4)
        v = np.array([[p.x, p.y, p.z] for p in block.data.vertices],
                     dtype=np.float64)
        if not np.allclose(G, np.eye(4), atol=1e-6):
            v = v @ G[:3, :3] + G[3, :3]
        pre_fk[id(block)] = v

    _deform_vertices_animation_fk(skinned_geoms, skel_root, bone_deltas)

    ob_skel = _load_skeleton(_SKEL_OBLIVION)

    # ---- gather every eligible block into one concatenated system --------
    metas = []          # (block, G_id, G, start, nv)
    vw_parts, pre_parts, abc_parts, hf_parts, tri_parts = [], [], [], [], []
    off = 0
    for block, is_prn, _prn_bone in skinned_geoms:
        if is_prn:
            continue
        geom_data = block.data
        skin = block.skin_instance
        if (geom_data is None or skin.data is None
                or geom_data.num_vertices == 0):
            continue
        v0 = pre_fk.get(id(block))
        nv = geom_data.num_vertices
        if v0 is None or len(v0) != nv:
            continue

        try:
            G = _m44_to_np(block.get_transform(skel_root))
        except (ValueError, RuntimeError):
            G = np.eye(4)
        G_id = np.allclose(G, np.eye(4), atol=1e-6)

        verts = np.array([[v.x, v.y, v.z] for v in geom_data.vertices],
                         dtype=np.float64)
        vw = verts if G_id else verts @ G[:3, :3] + G[3, :3]

        # per-vertex skin-weight bone centroid (region gate) + head-gear
        # weight fraction (helmets: the field has no head surface, so
        # head-weighted verts keep the plain FK result)
        bones_w = _geom_bone_weights(block)
        abc = np.zeros((nv, 3), dtype=np.float64)
        absum = np.zeros(nv)
        head_w = np.zeros(nv)
        for bone, (idx, w) in bones_w.items():
            if bone not in ob_skel:
                continue
            head = ob_skel[bone][3, :3]
            valid = (idx < nv) & (w > 1e-6)
            np.add.at(abc, idx[valid], np.outer(w[valid], head))
            np.add.at(absum, idx[valid], w[valid])
            if bone in HEAD_BONES:
                np.add.at(head_w, idx[valid], w[valid])
        has = absum > 1e-6
        abc[has] /= absum[has][:, None]
        abc[~has] = vw[~has]
        # head-gear gating is a PER-GEOMETRY decision: a helmet (majority
        # head-weighted) keeps plain FK everywhere, but a shirt whose collar
        # verts carry partial head weights (authored for neck-turn deform)
        # must NOT lose correction/enforcement exactly at the collar
        hw_total = float(head_w.sum())
        ab_total = float(absum.sum())
        geom_is_head = ab_total > 1e-6 and hw_total / ab_total > 0.5
        hf = np.full(nv, 1.0 if geom_is_head else 0.0)

        metas.append((block, G_id, G, off, nv))
        vw_parts.append(vw)
        pre_parts.append(v0)
        abc_parts.append(abc)
        hf_parts.append(hf)
        tri_parts.append(_geom_triangles(block) + off)
        off += nv

    if not metas:
        return 0

    VW = np.vstack(vw_parts)
    PRE = np.vstack(pre_parts)
    ABC = np.vstack(abc_parts)
    HF = np.concatenate(hf_parts)
    TRIS = np.vstack(tri_parts) if tri_parts else np.zeros((0, 3), np.int64)

    # single cross-block weld: seam twins across blocks (pauldron/torso)
    # must receive identical output positions
    wg = weld_groups(VW)
    n_g = int(wg.max()) + 1
    ABC = _group_mean(ABC, wg, n_g)[wg]
    HF = _group_mean(HF[:, None], wg, n_g)[wg][:, 0]

    corr = _field_corrections(field, VW, ABC, weight)
    corr = corr * (1.0 - HF)[:, None]
    new_w = VW + corr

    # --- minimum-clearance enforcement ------------------------------------
    # authored clearance (T-pose vert vs OB body) must be preserved, plus an
    # outward safety margin near the body: residual field noise must never
    # leave armor under the Skyrim body skin.  The deficit is DIFFUSED over
    # the (global) armor mesh graph before pushing: per-vertex estimator
    # noise cancels against neighbouring slack, while genuine deficit
    # regions survive and get pushed out coherently.
    c0, _n0, _r0 = _blended_clearance(field, PRE, field.src,
                                      field.src_tri_n, field.src_tree,
                                      k=CLEAR_K)
    # outward margin fades with authored clearance in BOTH directions:
    # far-off verts (hoods, hems) get none, and verts authored inside the
    # body (collar necklines) get none either — but their authored depth is
    # still enforced (target = c0), so a sinking collar gets pushed back.
    margin = CLEAR_MARGIN * np.exp(
        -(np.maximum(c0, 0.0) / CLEAR_MARGIN_RANGE) ** 2) * np.exp(
        -(np.minimum(c0, 0.0) / CLEAR_INNER_FADE) ** 2)
    prox = np.exp(-(np.maximum(c0, 0.0) / CLEAR_PROX) ** 2)
    a_idx = a_ptr = None
    if len(TRIS):
        a_idx, a_ptr = _build_adjacency(TRIS, wg, n_g)
    for _ in range(PUSH_ITERS):
        c1, n1, rel1 = _blended_clearance(field, new_w, field.dst[weight],
                                          field.dst_tri_n[weight],
                                          field.dst_tree[weight],
                                          field.tri_rel[weight], k=CLEAR_K)
        raw = ((c0 + margin) - c1) * prox * rel1 * (1.0 - HF)
        deficit = raw
        if a_idx is not None:
            deficit_g = _group_mean(raw[:, None], wg, n_g)
            deficit_g = _smooth_group_field(deficit_g, a_idx, a_ptr,
                                            PUSH_SMOOTH_PASSES)
            # diffusion cancels per-vertex noise but also dilutes genuine
            # isolated deficits (collar rings) — keep a floor of the raw
            deficit = np.maximum(deficit_g[wg][:, 0], PUSH_RAW_KEEP * raw)
        push = np.clip(deficit, 0.0, PUSH_CAP)
        new_w = new_w + n1 * push[:, None]

    # weld final positions (coincident twins must stay coincident)
    new_w = _group_mean(new_w, wg, n_g)[wg]

    for block, G_id, G, start, nv in metas:
        seg = new_w[start:start + nv]
        out = seg if G_id else (seg - G[3, :3]) @ np.linalg.inv(G[:3, :3])
        geom_data = block.data
        for vi in range(nv):
            geom_data.vertices[vi].x = float(out[vi, 0])
            geom_data.vertices[vi].y = float(out[vi, 1])
            geom_data.vertices[vi].z = float(out[vi, 2])
    return len(metas)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description='Build the body-wrap fields (OB body fitted onto SK body)')
    parser.add_argument('--gender', choices=['male', 'female'],
                        help='build a single gender (default: both)')
    args = parser.parse_args()
    if args.gender:
        ok = build_field(args.gender)
        print('OK' if ok else 'FAILED')
    else:
        n = build_all_fields()
        print(f'{n}/2 wrap fields built')


if __name__ == '__main__':
    main()
