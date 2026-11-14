"""Body-wrap armor fitting: exact fit onto the Skyrim body without clipping.

The FK animation retarget (skin_retarget Phase B) poses Oblivion armor ~90%
of the way to the Skyrim rest pose and is locally SMOOTH (it is ordinary
skinning), but it lands near — not on — the Skyrim body: armor floats or
sinks by 0.5-2.5 units, which is exactly the clipping seen in game.

This module measures that FK error EXACTLY and cancels it. The fields it
reads are built offline by `body_wrap_build` (one per gender and source game).

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
from types import SimpleNamespace

import numpy as np

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from asset_convert import paths
from asset_convert.character.skyrim_overrides_falloutnv import oblivion_alias_map
from asset_convert.character.wearable_plan import mesh_is_female

try:
    from pyffi.formats.nif import NifFormat
    _PYFFI = True
except ImportError:
    _PYFFI = False

_GEN_DIR = paths.GENERATED

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
#
# 2026-08-23: lowered 1.0 -> 0.85.  In game the converted armor read as puffy /
# scaled-up, and this margin is the only thing inflating it (all 968 armor and
# clothes NIFs take the wrap path, so ARMOR_PIECE_OFFSETS' legacy 1.05-1.08
# scales never run for them).  Swept on iron cuirass + greaves, measuring both
# looseness and how many verts end up under the Skyrim skin:
#
#   margin   cuirass inside   greaves inside   greaves size (X,Y)
#     1.00      7.61%           14.26%           32.99 x 19.63
#     0.85      7.61%           15.69%           32.69 x 19.37
#     0.75      7.61%           16.89%           32.49 x 19.25
#     0.60      7.74%           18.84%           32.19 x 19.12
#     0.25      7.87%           22.00%           31.93 x 18.64
#
# 0.85 is the knee: the cuirass is bit-for-bit unchanged (same 233 verts, same
# p05 -0.49) while the fit tightens measurably, and greaves give up only 1.4
# points.  Below 0.75 the greaves degrade fast for very little extra tightness.
CLEAR_MARGIN = 0.85
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
# Skin weight on this bone marks head gear.  This USED to force such geometry
# back to the plain FK result plus the hand-tuned ARMOR_PIECE_OFFSETS helmet
# constants, because the field was built from body/hands/feet only and had no
# head surface to interpolate from.  The field now includes a fitted head and
# ears, so the gate is inert whenever `has_head` is set (see deform_geoms_wrap)
# and survives only as the fallback for fields built before the head group.
# Head ONLY — the OB body upperbody mesh includes the neck, so Neck/Neck1
# regions have real field coverage and gating them regresses cuirass collars.
# Bone renaming may already have run by the time the wrap deform sees a NIF
# (converted hair ships skinned to 'NPC Head [Head]'), so head detection must
# accept BOTH naming conventions -- matching only the Oblivion name silently
# classified every converted hair mesh as non-head, so it never took the head
# group's correction at all.
HEAD_BONES = ('Bip01 Head', 'NPC Head [Head]')
# Correction-field smoothing at load (body-graph Jacobi passes).  Sweep on
# iron cuirass/gauntlets/boots (2026-07-10): more passes monotonically lowers
# armor edge distortion but slowly reintroduces clipping; 12 = best tradeoff
# (gauntlets 5.8% edges >15% / 1.0% clipped verts; 8:7.8%/0.6%, 16:5.1%/1.7%).
DELTA_SMOOTH_PASSES = 12

_FIELD_CACHE: dict = {}


from asset_convert.character.wrap_mesh import (
    build_adjacency as _build_adjacency, closest_point_on_triangles,
    geom_bone_weights as _geom_bone_weights, geom_triangles,
    group_mean as _group_mean, smooth_group_field as _smooth_group_field,
    weld_groups)
from asset_convert.character.skin_retarget import (
    SKEL_OBLIVION, deform_vertices_animation_fk, load_animation_deltas, load_skeleton, m44_to_np)
from asset_convert.character import head_fit


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
        # Does this field carry a fitted head surface?  Fields built before the
        # head group existed do not, and head-weighted geometry must keep the
        # old FK+constants fallback for them rather than take corrections
        # interpolated from neck and shoulder triangles.
        self.has_head = bool(z['has_head'][0]) if 'has_head' in z else False

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
        # ...AND DROP THE HEAD (part 2) FROM THE WRAP ENTIRELY.  The head
        # group is in this npz so head_fit can build its scalp displacement
        # field from the hf_* arrays; it is NOT a correction source for worn
        # body armor, and nothing worn on the body should ever sample it.
        # Left in, its triangles are simply the nearest surface for collar
        # geometry -- in the z 110-116 band 610 of 662 field verts are head
        # verts carrying a mean delta of 7.33 -- so cuirass and robe collars
        # inherited the head's correction and rode up with it (iron cuirass
        # reached z 121.6 against a head bone at 120.3).  Armor never did
        # this before the head group was added.  Helmets, hoods and hair are
        # fitted by asset_convert.character.head_fit in the head's own frame and do not
        # come through here, so removing these triangles costs nothing.
        good &= part[tris].max(axis=1) != 2
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


# ---------------------------------------------------------------------------
# Field files
# ---------------------------------------------------------------------------

def field_path(female: bool, morrowind: bool = False) -> Path:
    """The field npz for a gender, fitted from Morrowind's reference body on request.

    See: docs/commentary/asset_convert_armor.md#morrowind-wrap-field
    """
    return _GEN_DIR / ('body_wrap_%s%s.npz' % ('morrowind_' if morrowind else '',
                                               'female' if female else 'male'))


def get_field(female: bool, morrowind: bool = False):
    """Load (and cache) the wrap field for a gender and source, or None."""
    key = (female, morrowind)
    if key in _FIELD_CACHE:
        return _FIELD_CACHE[key]
    field = None
    path = field_path(female, morrowind)
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
    return get_field(mesh_is_female(src_path)) is not None


def wrap_has_head(src_path: str) -> bool:
    """True when this NIF's field carries a fitted HEAD surface.

    Head gear (helmets, hoods, converted hair) takes the measured correction
    only when the field actually has a head in it; a field built before the
    head group existed must keep the legacy ARMOR_PIECE_OFFSETS fallback or
    corrections interpolated from neck/shoulder triangles drag it into the
    middle of the skull.
    """
    field = get_field(mesh_is_female(src_path))
    return field is not None and getattr(field, 'has_head', False)


# ---------------------------------------------------------------------------
# Runtime application
# ---------------------------------------------------------------------------

def _field_corrections(field, pts, abc, weight=0):
    """Blended correction vectors for points (P,3) in FK-posed space.

    For each point: K nearest body triangles, per-candidate correction =
    barycentric interpolation of vertex deltas at the closest surface point,
    Gaussian-blended by (surface distance, bone-centroid distance) with a
    wrong-side penalty.  Normalized blending extrapolates the regional
    correction as a constant for far-away points.

    CHUNKED for memory, exactly like _blended_clearance."""
    pts = np.asarray(pts, dtype=np.float64)
    if len(pts) > _WRAP_CHUNK:
        return np.concatenate([
            _field_corrections(field, pts[i:i + _WRAP_CHUNK],
                               abc[i:i + _WRAP_CHUNK], weight)
            for i in range(0, len(pts), _WRAP_CHUNK)])
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


_WRAP_CHUNK = 4096         # max pts per closest-point solve (memory)


def _blended_clearance(field, pts, verts_surf, tri_normals, tree,
                       tri_rel=None, k=12):
    """Smooth signed clearance of pts against a body surface, plus the
    blended outward normal.  Gaussian blend over nearby triangles so the
    result is a smooth field (safe to use for pushing vertices).

    CHUNKED: this expands to ~10 (P,K,3) intermediates at CLEAR_K=24, i.e.
    ~1.15 GB for a 200k-vert armor mesh -- times the mesh-stage worker pool,
    which exhausted memory and froze the machine (2026-08-25).  Peak is now
    bounded by _WRAP_CHUNK no matter how large the mesh is.
    """
    pts = np.asarray(pts, dtype=np.float64)
    if len(pts) > _WRAP_CHUNK:
        outs = []
        for i in range(0, len(pts), _WRAP_CHUNK):
            outs.append(_blended_clearance(field, pts[i:i + _WRAP_CHUNK],
                                           verts_surf, tri_normals, tree,
                                           tri_rel=tri_rel, k=k))
        # `rel_out` is None when tri_rel is not supplied, and None cannot be
        # concatenated ("zero-dimensional arrays cannot be concatenated").
        # That exception propagated out of deform_geoms_wrap, which catches
        # it and falls back to plain FK -- so every mesh LARGER than
        # _WRAP_CHUNK silently lost the wrap entirely.  On the mythic dawn
        # robe (1936-vert Hand shape) FK alone left the hands 84.6 units out
        # of place and the whole robe offset; smaller meshes such as the
        # iron cuirass never chunked, which is why only some armor broke
        # (in-game 2026-08-25).  Members that are None in every chunk stay
        # None; the rest concatenate.
        return tuple(
            None if outs[0][j] is None
            else np.concatenate([o[j] for o in outs])
            for j in range(len(outs[0])))
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


def _shape_world(block, skel_root):
    """(world transform, whether it is the identity) of a shape under `skel_root`."""
    try:
        world = m44_to_np(block.get_transform(skel_root))
    except (ValueError, RuntimeError):
        world = np.eye(4)
    return world, np.allclose(world, np.eye(4), atol=1e-6)


def _world_verts(block, skel_root) -> np.ndarray:
    """A shape's vertices in skeleton (world) space."""
    world, is_id = _shape_world(block, skel_root)
    v = np.array([[p.x, p.y, p.z] for p in block.data.vertices], dtype=np.float64)
    return v if is_id else v @ world[:3, :3] + world[3, :3]


def _pre_fk_world(skinned_geoms, skel_root) -> dict:
    """id(shape) -> authored world verts of each non-PRN shape with verts.

    See: docs/commentary/asset_convert_armor.md#body-wrap-armor-fitting
    """
    return {id(block): _world_verts(block, skel_root) for block, is_prn, _pb in skinned_geoms
            if not is_prn and block.data is not None and block.data.num_vertices}


def _head_weights(bones_w, nv, ob_skel, vw):
    """(bone-centroid anchor per vertex, head-weight fraction per vertex, whether the shape is head gear).

    A shape is head gear when over half its total skin weight is on HEAD_BONES; a vertex
    with no bone in `ob_skel` anchors on itself.
    See: docs/commentary/asset_convert_armor.md#body-wrap-armor-fitting
    """
    abc, absum, head_w, tot_w = np.zeros((nv, 3)), np.zeros(nv), np.zeros(nv), np.zeros(nv)
    for bone, (idx, w) in bones_w.items():
        valid = (idx < nv) & (w > 1e-6)
        np.add.at(tot_w, idx[valid], w[valid])
        if bone in HEAD_BONES:
            np.add.at(head_w, idx[valid], w[valid])
        if bone in ob_skel:
            np.add.at(abc, idx[valid], np.outer(w[valid], ob_skel[bone][3, :3]))
            np.add.at(absum, idx[valid], w[valid])
    has = absum > 1e-6
    abc[has] /= absum[has][:, None]
    abc[~has] = vw[~has]
    w_total = float(tot_w.sum())
    is_head = w_total > 1e-6 and float(head_w.sum()) / w_total > 0.5
    return abc, head_w / np.maximum(tot_w, 1e-6), is_head


def _shape_parts(block, skel_root, v0, alias, gate):
    """One shape's rows: (world verts, authored verts, anchors, head gate, head fraction, head-gear flag)."""
    nv = len(v0)
    vw = _world_verts(block, skel_root)
    abc, hwf, is_head = _head_weights(_geom_bone_weights(block, alias), nv,
                                      load_skeleton(SKEL_OBLIVION), vw)
    return (vw, v0, abc, np.full(nv, float(is_head and gate)), hwf, np.full(nv, float(is_head)))


def _gather(skinned_geoms, skel_root, pre_fk, alias, field):
    """Every non-PRN skinned shape as ONE system, or None when there is none.

    The namespace holds `metas` [(shape, start, nv)] and the stacked rows VW (posed world
    verts), PRE (authored), ABC (anchors), HF (head gate), HW (head fraction), HG (head
    gear) and TRIS. Head gear is gated only when `field` has no head surface.
    See: docs/commentary/asset_convert_armor.md#body-wrap-armor-fitting
    """
    gate = not getattr(field, 'has_head', False)
    metas, rows, tris, off = [], [], [], 0
    for block, is_prn, _pb in skinned_geoms:
        v0 = None if is_prn else pre_fk.get(id(block))
        skin = block.skin_instance
        if v0 is None or skin is None or skin.data is None or len(v0) != block.data.num_vertices:
            continue
        rows.append(_shape_parts(block, skel_root, v0, alias, gate))
        metas.append((block, off, len(v0)))
        tris.append(geom_triangles(block) + off)
        off += len(v0)
    if not metas:
        return None
    cols = [np.vstack(c) if c[0].ndim == 2 else np.concatenate(c) for c in zip(*rows)]
    return SimpleNamespace(metas=metas, VW=cols[0], PRE=cols[1], ABC=cols[2], HF=cols[3],
                           HW=cols[4], HG=cols[5], TRIS=np.vstack(tris))


def _weld(s) -> None:
    """Weld the system once across shapes; seam twins share ABC, HF, HW and HG.

    See: docs/commentary/asset_convert_armor.md#body-wrap-armor-fitting
    """
    s.wg = weld_groups(s.VW)
    s.n_g = int(s.wg.max()) + 1
    s.ABC = _group_mean(s.ABC, s.wg, s.n_g)[s.wg]
    for name in ('HF', 'HW', 'HG'):
        setattr(s, name, _group_mean(getattr(s, name)[:, None], s.wg, s.n_g)[s.wg][:, 0])


def head_origin(src_skel, default):
    """The source rig's Bip01 Head world position, else `default`.

    See: docs/commentary/asset_convert_armor.md#head-gear-fit
    """
    if src_skel and 'Bip01 Head' in src_skel:
        return np.asarray(src_skel['Bip01 Head'], dtype=np.float64)[3, :3]
    return default


def _fit_head_gear(s, new_w, female, race, src_skel):
    """`new_w` with head gear's head-weighted verts mapped through head_fit's scalp field.

    Verts are taken head-local about the SOURCE rig's head bone and blended by head-weight
    fraction; fitted verts are exempted from the clearance push (HF raised).
    See: docs/commentary/asset_convert_armor.md#head-gear-fit
    """
    m_head = (s.HW > 1e-3) & (s.HG > 0.5)
    hfit = head_fit._get(female) if m_head.any() else None
    if hfit is None:
        return new_w
    o_src = head_origin(src_skel, hfit.o_ob)
    delta = head_fit.field_deltas(s.PRE[m_head] - o_src, female, race=race)
    if delta is None:
        return new_w
    tgt = s.PRE[m_head] + (hfit.o_sk - o_src) + delta
    w_h = s.HW[m_head][:, None]
    new_w[m_head] = (1.0 - w_h) * new_w[m_head] + w_h * tgt
    s.HF = np.maximum(s.HF, s.HW)
    return new_w


def _push_clear(s, new_w, field, weight):
    """`new_w` pushed out to the authored clearance plus margin from the fitted Skyrim body.

    PUSH_ITERS rounds; each deficit is diffused over the armor graph, keeping PUSH_RAW_KEEP
    of the raw deficit as a floor.
    See: docs/commentary/asset_convert_armor.md#body-wrap-armor-fitting
    """
    c0, _n0, _r0 = _blended_clearance(field, s.PRE, field.src, field.src_tri_n,
                                      field.src_tree, k=CLEAR_K)
    margin = CLEAR_MARGIN * np.exp(-(np.maximum(c0, 0.0) / CLEAR_MARGIN_RANGE) ** 2) * np.exp(
        -(np.minimum(c0, 0.0) / CLEAR_INNER_FADE) ** 2)
    prox = np.exp(-(np.maximum(c0, 0.0) / CLEAR_PROX) ** 2)
    adj = _build_adjacency(s.TRIS, s.wg, s.n_g) if len(s.TRIS) else None
    for _ in range(PUSH_ITERS):
        c1, n1, rel1 = _blended_clearance(field, new_w, field.dst[weight], field.dst_tri_n[weight],
                                          field.dst_tree[weight], field.tri_rel[weight], k=CLEAR_K)
        deficit = raw = ((c0 + margin) - c1) * prox * rel1 * (1.0 - s.HF)
        if adj is not None:
            smooth = _smooth_group_field(_group_mean(raw[:, None], s.wg, s.n_g), adj[0], adj[1],
                                         PUSH_SMOOTH_PASSES)
            deficit = np.maximum(smooth[s.wg][:, 0], PUSH_RAW_KEEP * raw)
        new_w = new_w + n1 * np.clip(deficit, 0.0, PUSH_CAP)[:, None]
    return new_w


def _write_back(metas, new_w, skel_root) -> None:
    """Store each shape's rows of `new_w` back into its own vertex frame."""
    for block, start, nv in metas:
        world, is_id = _shape_world(block, skel_root)
        seg = new_w[start:start + nv]
        out = seg if is_id else (seg - world[3, :3]) @ np.linalg.inv(world[:3, :3])
        for vi in range(nv):
            vert = block.data.vertices[vi]
            vert.x, vert.y, vert.z = (float(c) for c in out[vi])


def deform_geoms_wrap(skinned_geoms, skel_root, field, female: bool,
                      weight: int = 0, race=None, src_skel: dict = None) -> int:
    """FK deform + exact body-fit correction for all non-PRN skinned shapes; how many (0 = run plain FK).

    FK first, then the wrap field cancels FK's measured error against the Skyrim body,
    all shapes solved as one system. `weight` picks the _0/_1 body, `race` a beast head
    pack, `src_skel` (the source rest skeleton) the pose cache and head origin.
    See: docs/commentary/asset_convert_armor.md#body-wrap-armor-fitting
    """
    bone_deltas, alias = load_animation_deltas(src_skel), oblivion_alias_map(src_skel)
    if not bone_deltas:
        return 0
    pre_fk = _pre_fk_world(skinned_geoms, skel_root)
    deform_vertices_animation_fk(skinned_geoms, skel_root, bone_deltas)
    s = _gather(skinned_geoms, skel_root, pre_fk, alias, field)
    if s is None:
        return 0
    _weld(s)
    new_w = s.VW + _field_corrections(field, s.VW, s.ABC, weight) * (1.0 - s.HF)[:, None]
    new_w = _push_clear(s, _fit_head_gear(s, new_w, female, race, src_skel), field, weight)
    _write_back(s.metas, _group_mean(new_w, s.wg, s.n_g)[s.wg], skel_root)
    return len(s.metas)


def morph_converted_to_weight1(data, female: bool) -> int:
    """Morph a CONVERTED (weight-0) wearable NIF into its _1 variant in place.

    The engine lerps the _0/_1 pair per-vertex by actor weight, which
    requires IDENTICAL topology — so the _1 mesh must never come from a
    second independent conversion (the body splice clips differently and
    the pair explodes at intermediate slider values).  Instead the finished
    _0 mesh gets the body morph applied: each skinned vertex receives
    dst1 - dst0 (the fitted _0->_1 Skyrim body morph, built from the
    REFERENCE body meshes) interpolated from the nearest fitted-body
    triangles.  Spliced body fill lies ON the _0 surface so it receives the
    exact body morph; armor receives the same smooth field, which is how
    vanilla _1 armor relates to _0.  Rigid PRN pieces (helmets, shields)
    are never morphed.  Returns the number of geometries morphed."""
    field = get_field(female)
    if field is None or not _PYFFI:
        return 0
    diff = field.dst[1] - field.dst[0]      # per body vertex, SK space
    dst0 = field.dst[0]
    tree = field.dst_tree[0]

    count = 0
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not isinstance(block, (NifFormat.NiTriShape,
                                      NifFormat.NiTriStrips)):
                continue
            skin = getattr(block, 'skin_instance', None)
            if skin is None or skin.data is None:
                continue
            if block.data is None or block.data.num_vertices == 0:
                continue
            # rigid PRN pieces: single bone with identity bind — no morph
            if skin.num_bones == 1 and skin.data.num_bones >= 1:
                st = skin.data.bone_list[0].skin_transform
                if (abs(st.rotation.m_11 - 1.0) < 0.001
                        and abs(st.translation.x) < 0.001
                        and abs(st.translation.y) < 0.001
                        and abs(st.translation.z) < 0.001):
                    continue
            nv = block.data.num_vertices
            v = np.array([[p.x, p.y, p.z] for p in block.data.vertices],
                         dtype=np.float64)

            k = min(CLEAR_K, len(field.tris))
            _, tri = tree.query(v, k=k)
            if k == 1:
                tri = tri[:, None]
            t = field.tris[tri]
            a, b, c = dst0[t[..., 0]], dst0[t[..., 1]], dst0[t[..., 2]]
            cp = closest_point_on_triangles(v[:, None, :], a, b, c)
            d = np.linalg.norm(v[:, None, :] - cp, axis=2)
            # barycentric interpolation of the morph at each closest point
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
            m_cp = (bu[..., None] * diff[t[..., 0]]
                    + bv[..., None] * diff[t[..., 1]]
                    + bw[..., None] * diff[t[..., 2]])
            d_best = d.min(axis=1)
            sig = 1.5 + 0.5 * d_best
            w = np.exp(-((d - d_best[:, None]) ** 2) / (2.0 * sig[:, None] ** 2))
            w /= w.sum(axis=1, keepdims=True)
            morph = (w[:, :, None] * m_cp).sum(axis=1)

            # weld so coincident seam twins morph identically
            wg = weld_groups(v)
            morph = _group_mean(morph, wg, int(wg.max()) + 1)[wg]

            out = v + morph
            gd = block.data
            for vi in range(nv):
                gd.vertices[vi].x = float(out[vi, 0])
                gd.vertices[vi].y = float(out[vi, 1])
                gd.vertices[vi].z = float(out[vi, 2])
            count += 1
    return count

