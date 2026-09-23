"""Head-gear fitting: Oblivion head-part space -> Skyrim head-part space.

Hair and Prn-attached helmets are authored in "face space": an upright frame
whose origin is the head attach point (the OB head mesh's node translation
(0,-1.187,112.441) IS Bip01 Head's world translation, and the converted piece
renders at ``verts + NPC Head world`` on the Skyrim side).  Vanilla Skyrim
head parts use the same convention (hair01.nif spans z -10.5..12.1,
head-bone-local).

MEASURED GROUND TRUTH (2026-08-23, after an in-game round trip):
  In head-LOCAL frames the two skulls are nearly the SAME SIZE — earless
  widths OB x +-5.59 vs SK +-5.51 (male) / +-5.58 (female), local scalp
  deltas mean 0.96 / p95 2.2 / max 2.8 units (crown ~2 DOWN, back of skull
  ~2.2 further back).  A v1 affine carrier measured from the world-frame ICP
  fit claimed sx 1.18 / sz 1.24 and OVERSIZED every mesh in game: the x was
  ICP stretching the earless OB head over the SK EARS, the z conflated bone
  placement with head size.  Never fit a carrier in world frames.

THE MECHANISM (v3) — one smooth scalp-to-scalp displacement field:

  At BUILD time (body_wrap_build.build_field -> build_arrays) a per-scalp-vertex
  displacement field is computed once per gender/race: for every vertex of
  the OB head, where the matching point on the SK head is.  It initialises
  from NEAREST POINT over the identity carrier (the shared FaceGen UV
  layout was tried and rejected: its v-coordinates differ by up to 0.042 at
  the same landmark, dragging the whole field ~2 units down).  The raw
  correspondence is locally noisy, so it is relaxed by cycles of graph
  smoothing followed by reprojection onto the SK surface — the final
  projection leaves every field target EXACTLY ON the Skyrim skin.

  At RUNTIME (fit_head_gear / field_deltas) fitting is a pure per-vertex
  function: each vertex samples the field at its closest point on the OB
  scalp (barycentric over the containing triangle) and moves by that delta.
  Consequences, by construction:
    - a vertex authored ON the skin lands ON the new skin (hairline edges
      exactly at the skin line — no under- or over-fit);
    - a vertex authored N units off the skin stays exactly N units off
      (helmets keep their authored standoff);
    - all vertices over the same scalp region move identically, so the
      outside of a shell moves exactly as much as the inside — thickness,
      headbands and silhouettes are never stretched.  The only deformation
      is the field's own (smoothed) gradient, i.e. the real anatomical
      difference between the skulls.

  EARS ARE IGNORED BY FLATTENING, NOT BY CUTTING (the OB heads are
  earless by authoring; the SK ear verts are projected onto the OB ear
  socket at build).  Cutting ear triangles left holes whose rims attracted
  every nearby correspondence outward; flattening keeps the surface
  continuous, gear follows the skull, and the SK ears may poke through
  hair sides exactly as vanilla hair allows.

Build data lives in body_wrap's generated npz (hf_*/hfr_* arrays, written by
body_wrap_build.build_field via build_arrays below; 'hf_v4' marks the field
format).  Surfaces are stored in WORLD coordinates; O_OB / O_SK translate
face space to and from it.  The same field also replaces the wrap's ICP head
surface (build_field), so skinned head gear takes the identical mapping.
"""

from pathlib import Path

import numpy as np
from asset_convert import paths

try:
    from scipy.spatial import cKDTree
    _SCIPY = True
except ImportError:
    _SCIPY = False

_GEN_DIR = paths.GENERATED

# HAIR IS BAKED PER RACE GROUP (2026-08-24).  A races.tri on a type-3 (Hair)
# HDPT was tried and the ENGINE DOES NOT APPLY IT (vanilla never ships one on
# hair — only on heads, type 1, and beards, type 4; in game the hair rendered
# unmorphed: floating 1.7 units behind the High Elf occiput while nearly
# right on Imperials).  Vanilla's own architecture is per-race-group MESHES
# gated by RNAM race lists, and the scalp measurements define the groups
# exactly: all five human races + Dremora share the BASE scalp (morphs <=
# 0.15 there), HighElf == DarkElf exactly with WoodElf within 1.4 (vanilla
# shares one hair set across all three elves), Orc its own (1.5 from base).
# group -> the maleheadraces/femaleheadraces morph its target head wears
GROUP_MORPHS = {
    'elves': 'HighElfRace',
    'orc': 'OrcRace',
}

FIELD_CYCLES = 6           # smooth->reproject relaxation cycles (build)
FIELD_SMOOTH_PASSES = 24   # Jacobi passes per cycle over the scalp graph
SAMPLE_K = 16              # candidate triangles per field sample
SAMPLE_SIG_A = 0.5         # sample blur floor: on-skin verts stay exact
SAMPLE_SIG_B = 0.5         # ...widening with standoff distance
PROJECT_K = 8              # candidate triangles per surface projection
FAR_DIST = 4.0             # beyond this standoff, deltas diffuse from the
                           # near verts over the mesh graph (hanging tails)
FAR_MAX_ITERS = 6000       # diffusion passes cap
FAR_TOL = 5e-4             # ...with early exit at this max per-pass change
REFINE_MAX_C0 = 4.0        # authored clearance range the refinement covers
REFINE_C0_SIGMA = 1.5      # ...fading with distance from the skin
REFINE_CAP = 0.5           # max exact-clearance correction per vertex
REFINE_SMOOTH_PASSES = 4   # graph smoothing of the correction
REFINE_ITERS = 3           # hug-mode measure->correct rounds (the smoothing
                           # dilutes each round's step; iterating holds it).
                           # This is the FLOOR of the budget -- more rounds
                           # are added when a mesh asks for more travel than
                           # one REFINE_CAP can deliver (see _fit_core).
REFINE_ITERS_MAX = 12      # ...bounded, so a pathological mesh cannot spin.
                           # 12 covers a 1.4 standoff pulled to 0.2 with
                           # margin; the loop exits early on convergence.
# STANDOFF HUG (hair only, 2026-08-24 in-game round): Oblivion hair is
# authored 0.3-0.5 OFF its head where vanilla Skyrim hair HUGS the skin
# (measured: OB style07 front +0.31/crown +0.42 vs vanilla hairshorthumanm
# front +0.13; scalp-hugging vanilla styles sit at +0.03).  Preserving the
# authored clearance therefore reads as a uniform float in game.  Near-skin
# clearance is compressed monotonically toward the skin; identity again by
# HUG_RAMP[1], so hair VOLUME (and helmets, which never pass hug=True) keep
# their authored shape.
# 0.45 clipped, 0.62 was loose, 0.535 still read slightly loose.  Since the
# CLIP FLOOR below (not the hug) is what actually guarantees clearance, the
# hug can pull much further in and let the floor push back out only where a
# vertex would really enter the head.  Measured across 5 styles: at 0.25 the
# front/crown reach +0.14 (vs +0.21 at 0.535) with NO new below-skin verts,
# while the floor still only has to move ~60% of the mesh.  Below ~0.10 the
# floor ends up moving 75-100% of it, i.e. the smoothed push -- not the
# authored geometry -- would be deciding the silhouette.
# The FLOOR sets the clearance, not the hug: sweeping HUG_K 0.25 -> 0.00
# moves style07's crown only +0.204 ->
# +0.198.  Kept low so the authored standoff is nearly discarded and the
# floor decides, but not 0 -- the hug still orders verts sensibly where the
# floor is inactive (hair well off the skin).
HUG_K = 0.15               # on-skin standoff keeps this fraction
HUG_RAMP = (0.5, 2.5)      # clearance band over which compression fades out
# The human scalp standoff HUG_RAMP was tuned against: median authored
# clearance over the on-head band, measured across style07/01/02/03 and
# imperialbald (0.38-0.81).  A mesh authored looser than this has its ramp
# scaled up in proportion, so the compression is the same FRACTION of its
# own standoff -- see _hug.  Meshes tighter than the reference are left on
# the original ramp (s is clamped at 1.0), so no human style shifts.
_HUG_BASE_REF = 0.6
# EARS ARE EXCLUDED FROM THE PUSH (2026-08-24, in-game).  Flooring hair
# ABOVE the ear shell inflates the style around it -- badly on High Elves,
# whose races-tri morph moves the ear verts by up to 2.27 (vs 0.29 over the
# rest of the head), so the "drape over the ear" push had a huge, oddly
# shaped obstacle to clear.  Vanilla lets ears poke through hair sides, and
# the user's call is that ignoring the ear beats deforming the hair.  A
# vertex whose nearest REAL-skin triangle is an EAR triangle is simply left
# alone (the SKULL floor still governs it through the flattened surface).
# (the ear shell is ignored by the floor; see the SKIN FLOOR pass)  This ALONE controls
                           # the behind-the-ear standoff (HUG_K moves it by
                           # <0.01): it decides how far hair drapes over the
                           # ear instead of letting the shell poke through.
                           # Vanilla ranges from +0.04 (scalp-hugging styles)
                           # to +0.36 (draping ones); 0.32 read too far out.
# THIS is now the main tightness dial (see HUG_K).  0.05 is the tightest
# value that still keeps TRIANGLE penetration at zero on the clean styles:
# at 0.03 the style07 crown starts dipping again (4 tris at -0.064).
# 0.05 left 8 CROWN triangles of style07 dipping to -0.061 against the REAL
# head (the user could see scalp through the top; vertex checks showed none,
# it is the flat-triangle-over-a-dome effect).  0.10 clears every dip on the
# style at a cost of only +0.05 mean clearance.
SKIN_FLOOR_MIN = 0.10      # floor over non-ear REAL skin
TRI_PASS_ITERS = 10        # triangle-clearance rounds after the vertex pass
TRI_LIFT_CAP = 0.40        # max lift ONE triangle pass may give a vertex
TRI_TOTAL_CAP = 0.6        # ...and the total across all of them
TRI_CLEAR_MIN = 0.06       # a triangle's deepest interior point must clear
                           # the head by at least this.  Was 0.02, which is
                           # BELOW the residual the smoothed push settles at:
                           # each round's lift is graph-smoothed (3 passes),
                           # so the pass converges a little short of its own
                           # target and 0.02 landed the crown AT the skin.
                           # Shipped style07 measured 20 crown triangles at
                           # -0.05 (z 10.2-10.9, |x|<2.7) -- the row of scalp
                           # the user saw through the top of the head
                           # (in-game 2026-08-25).
# SAMPLE DENSITY IS PART OF THE MEASUREMENT, NOT A COST KNOB.  At n=4 (15
# points) the pass missed interior minima that n=8 (45 points) finds: the
# same style07 crown triangles the pass called clean scored -0.05 when
# sampled densely.  A dip that is not sampled is a dip that is not fixed,
# and it was exactly the flaw behind "there is obviously a minor flaw in
# your measurement".  The audit probe (temp/hair_probe.py) uses n=8 too, so
# the pass is now checked at the density it is graded at.
_TRI_N = 8
_TRI_SAMPLES = np.array([(i / _TRI_N, j / _TRI_N, (_TRI_N - i - j) / _TRI_N)
                         for i in range(_TRI_N + 1)
                         for j in range(_TRI_N + 1 - i)])
_CLEAR_CHUNK = 4096        # max points per closest-point solve (memory)
WELD_TOL = 1e-4
FLOOR_CAP = 1.3            # push per ITERATION is capped at this
FLOOR_TOTAL_CAP = 1.5      # ...and this is the TOTAL across all iterations.
                           # Bounds the compounding that drove one style03
                           # vertex 4.45 units out (x 6.38 -> 9.38) as a
                           # visible spike; 1.5 is past any real clip depth,
                           # so it costs no legitimate correction.
FLOOR_ITERS = 10           # measure->push rounds (see REFINE_ITERS).  Deep
                           # clips (style02 reached -1.29) cannot escape in
                           # one capped, smoothed step; iterating lets the
                           # push accumulate without a jerky per-step cap.


def _hug(c, base=None):
    """Compress near-skin clearance toward the vanilla hug (monotone).

    `base` is the mesh's OWN authored scalp standoff (the median clearance
    of its on-head band).  THE RAMP IS RELATIVE TO IT, because the absolute
    band was calibrated on human hair and silently exempted anything
    authored looser.  Oblivion drew BEAST hair much further off the head
    than human hair -- measured median clearance 1.43 (argonianfins), 1.34
    (khajiitcommon), 1.25 (khajiitmane) against 0.38 (style07), 0.41
    (imperialbald), 0.59 (style03) -- so every beast style sat past
    HUG_RAMP's 0.5-2.5 fade and kept 80-100% of a standoff 3x the human
    one.  That is the argonian/khajiit hair "floating slightly off the
    head" (in-game 2026-08-25).  Scaling the ramp by the mesh's own
    baseline compresses a loose author exactly as hard as a tight one, so
    the rule is one rule and no race is special-cased.
    """
    if base is not None and np.isfinite(base) and base > 1e-6:
        # normalize by the mesh's own standoff, relative to the human
        # baseline the ramp was tuned on
        s = max(float(base) / _HUG_BASE_REF, 1.0)
        lo, hi = HUG_RAMP[0] * s, HUG_RAMP[1] * s
    else:
        lo, hi = HUG_RAMP
    t = np.clip((c - lo) / max(hi - lo, 1e-6), 0.0, 1.0)
    w = t * t * (3.0 - 2.0 * t)
    return c * (HUG_K + (1.0 - HUG_K) * w)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _tri_normals(V, T):
    n = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


def _closest_point_on_triangles(p, a, b, c):
    """Closest points of p (P,1,3) on triangles a/b/c (P,K,3)."""
    ab = b - a
    ac = c - a
    ap = p - a
    d1 = np.einsum('pki,pki->pk', ab, ap)
    d2 = np.einsum('pki,pki->pk', ac, ap)
    bp = p - b
    d3 = np.einsum('pki,pki->pk', ab, bp)
    d4 = np.einsum('pki,pki->pk', ac, bp)
    cp_ = p - c
    d5 = np.einsum('pki,pki->pk', ab, cp_)
    d6 = np.einsum('pki,pki->pk', ac, cp_)
    vc = d1 * d4 - d3 * d2
    vb = d5 * d2 - d1 * d6
    va = d3 * d6 - d5 * d4
    denom = va + vb + vc
    denom = np.where(np.abs(denom) < 1e-300, 1e-300, denom)
    v = vb / denom
    w = vc / denom
    out = a + v[..., None] * ab + w[..., None] * ac
    m = (d1 <= 0) & (d2 <= 0)
    out[m] = np.broadcast_to(a, out.shape)[m]
    m = (d3 >= 0) & (d4 <= d3)
    out[m] = np.broadcast_to(b, out.shape)[m]
    m = (d6 >= 0) & (d5 <= d6)
    out[m] = np.broadcast_to(c, out.shape)[m]
    m = (vc <= 0) & (d1 >= 0) & (d3 <= 0)
    den = np.where(np.abs(d1 - d3) < 1e-300, 1.0, d1 - d3)
    t = np.clip(d1 / den, 0.0, 1.0)
    e = a + t[..., None] * ab
    out[m] = e[m]
    m = (vb <= 0) & (d2 >= 0) & (d6 <= 0)
    den = np.where(np.abs(d2 - d6) < 1e-300, 1.0, d2 - d6)
    t = np.clip(d2 / den, 0.0, 1.0)
    e = a + t[..., None] * ac
    out[m] = e[m]
    m = (va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0)
    den = np.where(np.abs((d4 - d3) + (d5 - d6)) < 1e-300, 1.0,
                   (d4 - d3) + (d5 - d6))
    t = np.clip((d4 - d3) / den, 0.0, 1.0)
    e = b + t[..., None] * (c - b)
    out[m] = e[m]
    return out


def _weld_map(V, tol=WELD_TOL):
    """Vertex -> welded-group index (coincident verts share a group)."""
    key = np.round(np.asarray(V, dtype=np.float64) / tol).astype(np.int64)
    _uniq, inv = np.unique(key, axis=0, return_inverse=True)
    return inv


def _adjacency(n, tris):
    """CSR-ish adjacency (idx array + ptr) over welded vertex groups."""
    pairs = set()
    for tr in tris:
        a, b, c = int(tr[0]), int(tr[1]), int(tr[2])
        for x, y in ((a, b), (b, c), (c, a)):
            if x != y:
                pairs.add((x, y))
                pairs.add((y, x))
    if not pairs:
        return None, None
    arr = np.array(sorted(pairs), dtype=np.int64)
    idx = arr[:, 1]
    ptr = np.zeros(n + 1, dtype=np.int64)
    np.add.at(ptr, arr[:, 0] + 1, 1)
    ptr = np.cumsum(ptr)
    return idx, ptr


def _smooth(vals, idx, ptr, passes, lam=0.5):
    """Jacobi smoothing of per-vertex vectors over the graph."""
    x = vals.astype(np.float64).copy()
    if idx is None:
        return x
    deg = np.maximum(np.diff(ptr), 1)
    if x.ndim > 1:
        deg = deg[:, None]
    empty = np.diff(ptr) == 0
    starts = np.minimum(ptr[:-1], max(len(idx) - 1, 0))
    for _ in range(passes):
        s = np.add.reduceat(x[idx], starts, axis=0)
        mean = s / deg
        mean[empty] = x[empty]
        x = (1.0 - lam) * x + lam * mean
    return x


def _vertex_normals(V, T, wg=None):
    """Area-weighted per-vertex normals (weld-aware when wg given)."""
    fn = np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]])
    n = np.zeros_like(V)
    for c in range(3):
        np.add.at(n, T[:, c], fn)
    if wg is not None:
        n_g = int(wg.max()) + 1
        gs = np.zeros((n_g, 3))
        np.add.at(gs, wg, n)
        n = gs[wg]
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


def _project_ray(P, N, V, T, tree, max_t=8.0, k=32):
    """Ray-cast each P along +-N onto surface (V,T); nearest-point fallback.

    Casting along the source surface's own normal keeps the correspondence
    free of lateral drift and reaches concave/convex features (the SK nape
    bulge) that nearest-point projection exits sideways from.
    """
    k = min(k, len(T))
    _, tri = tree.query(P, k=k)
    if k == 1:
        tri = tri[:, None]
    a = V[T[tri, 0]]
    e1 = V[T[tri, 1]] - a
    e2 = V[T[tri, 2]] - a
    d = N[:, None, :]
    pv = np.cross(np.broadcast_to(d, e2.shape), e2)
    det = np.einsum('pki,pki->pk', e1, pv)
    ok = np.abs(det) > 1e-12
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    tv = P[:, None, :] - a
    u = np.einsum('pki,pki->pk', tv, pv) * inv
    qv = np.cross(tv, e1)
    v = np.einsum('pki,pki->pk', np.broadcast_to(d, qv.shape), qv) * inv
    t = np.einsum('pki,pki->pk', e2, qv) * inv
    hit = ok & (u >= -1e-6) & (v >= -1e-6) & (u + v <= 1 + 1e-6)         & (np.abs(t) <= max_t)
    t_abs = np.where(hit, np.abs(t), np.inf)
    best = np.argmin(t_abs, axis=1)
    r = np.arange(len(P))
    got = np.isfinite(t_abs[r, best])
    out = _project_exact(P, V, T, tree)
    out[got] = P[got] + t[r, best][got, None] * N[got]
    return out


def _project_exact(P, V, T, tree, k=None):
    """Exact nearest point of each P on surface (V,T)."""
    k = min(k or PROJECT_K, len(T))
    _, tri = tree.query(P, k=k)
    if k == 1:
        tri = tri[:, None]
    a, b, c = V[T[tri, 0]], V[T[tri, 1]], V[T[tri, 2]]
    cp = _closest_point_on_triangles(P[:, None, :], a, b, c)
    d = np.linalg.norm(P[:, None, :] - cp, axis=2)
    best = np.argmin(d, axis=1)
    r = np.arange(len(P))
    return cp[r, best]


def _signed_clearance(P, V, T, tree, k=None, want_normals=False):
    """Signed distance of each P to surface (V,T) (+ blended normal).

    CHUNKED: the closest-point solve expands to ~15 intermediate (P,K,3)
    arrays at once, so querying a whole mesh's triangle-sample grid in one
    call peaked at 118 MB for a 1.7k-vert style -- times 29 pool workers,
    that is what raised MemoryError mid-build (2026-08-25).  Peak is now
    bounded by _CLEAR_CHUNK regardless of how many points are asked for.
    """
    P = np.asarray(P, dtype=np.float64)
    if len(P) > _CLEAR_CHUNK:
        outs = []
        nrms = [] if want_normals else None
        for i in range(0, len(P), _CLEAR_CHUNK):
            r = _signed_clearance(P[i:i + _CLEAR_CHUNK], V, T, tree, k=k,
                                  want_normals=want_normals)
            if want_normals:
                outs.append(r[0])
                nrms.append(r[1])
            else:
                outs.append(r)
        if want_normals:
            return np.concatenate(outs), np.concatenate(nrms)
        return np.concatenate(outs)
    k = min(k or PROJECT_K, len(T))
    _, tri = tree.query(P, k=k)
    if k == 1:
        tri = tri[:, None]
    tn = _tri_normals(V, T)
    a, b, c = V[T[tri, 0]], V[T[tri, 1]], V[T[tri, 2]]
    cp = _closest_point_on_triangles(P[:, None, :], a, b, c)
    d = np.linalg.norm(P[:, None, :] - cp, axis=2)
    best = np.argmin(d, axis=1)
    r = np.arange(len(P))
    off = P - cp[r, best]
    dist = np.linalg.norm(off, axis=1)
    sgn = np.where(np.einsum('pi,pi->p', off, tn[tri[r, best]]) >= 0,
                   1.0, -1.0)
    if not want_normals:
        return sgn * dist
    n = tn[tri[r, best]]
    return sgn * dist, n


def _unit_out(V, origin):
    """Outward radial unit vectors of V about `origin` (head center)."""
    d = V - origin
    return d / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-9)


def _inside_surface(P, V, T, tree, k=12):
    """Robust inside test: do the k nearest triangles AGREE P is behind them?

    _signed_clearance takes the sign from the single closest triangle, which
    misreads a vertex that has sunk past a fold or a thin feature (the ear
    root): measured on style05, 20 verts genuinely inside the head scored
    non-negative, one at +0.18, so the clip floor never fired on them.
    Averaging the signed side over several nearby triangles, weighted by
    proximity, is stable there -- a truly interior point is behind MOST of
    its neighborhood, an exterior one in front of most.

    Returns (inside mask, depth) where depth is how far behind the surface
    the point sits (0 outside).
    """
    k = min(k, len(T))
    _, tri = tree.query(P, k=k)
    if k == 1:
        tri = tri[:, None]
    tn = _tri_normals(V, T)
    a, b, c = V[T[tri, 0]], V[T[tri, 1]], V[T[tri, 2]]
    cp = _closest_point_on_triangles(P[:, None, :], a, b, c)
    off = P[:, None, :] - cp
    d = np.linalg.norm(off, axis=2)
    sd = np.einsum('pki,pki->pk', off, tn[tri])      # signed, per triangle
    w = 1.0 / np.maximum(d, 1e-6) ** 2
    w /= w.sum(axis=1, keepdims=True)
    vote = (w * np.sign(sd)).sum(axis=1)
    inside = vote < 0.0
    depth = np.where(inside, d.min(axis=1), 0.0)
    return inside, depth


def _sample_field(P, src_v, src_t, tree, dv, k=None):
    """Distance-weighted field sample of dv at each P's nearby scalp points.

    Each candidate triangle contributes the barycentric field value at its
    own closest point, Gaussian-weighted by how much farther it is than the
    best candidate (sigma widens with standoff distance).  A vertex ON the
    scalp takes essentially only its containing triangle — sampling stays
    EXACT at the skin — while a vertex far off the scalp (helmet dome, hair
    tail) blends a neighborhood, so the sampled delta cannot jump where the
    closest-point correspondence crosses an open rim (eye sockets, the neck
    cut) or a concave crease.

    CHUNKED: a skinned helmet passes its whole vertex set here and this
    expands to ~10 (P,K,3) intermediates at K=16 -- 333 MB for a 50k mesh,
    unbounded with size.  Across the mesh-stage worker pool that is what
    exhausted memory and froze the machine (2026-08-25).
    """
    P = np.asarray(P, dtype=np.float64)
    if len(P) > _CLEAR_CHUNK:
        return np.concatenate([
            _sample_field(P[i:i + _CLEAR_CHUNK], src_v, src_t, tree, dv, k=k)
            for i in range(0, len(P), _CLEAR_CHUNK)])
    k = min(k or SAMPLE_K, len(src_t))
    _, tri = tree.query(P, k=k)
    if k == 1:
        tri = tri[:, None]
    a = src_v[src_t[tri, 0]]
    b = src_v[src_t[tri, 1]]
    c = src_v[src_t[tri, 2]]
    cp = _closest_point_on_triangles(P[:, None, :], a, b, c)
    d = np.linalg.norm(P[:, None, :] - cp, axis=2)

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
    t = src_t[tri]
    val = (bu[..., None] * dv[t[..., 0]] + bv[..., None] * dv[t[..., 1]]
           + bw[..., None] * dv[t[..., 2]])         # (P,K,3)

    d_best = d.min(axis=1)
    sig = SAMPLE_SIG_A + SAMPLE_SIG_B * d_best
    w = np.exp(-((d - d_best[:, None]) ** 2) / (2.0 * sig[:, None] ** 2))
    w /= w.sum(axis=1, keepdims=True)
    return (w[..., None] * val).sum(axis=1)


def _sample_fields(P, src_v, src_t, tree, dv_list, k=None):
    """Sample several per-vertex fields through ONE set of weights.

    Identical weighting to _sample_field; returns a list of (P,3) arrays,
    one per field in dv_list, so derived fields (race morphs) stay exactly
    consistent with the base fit.  Chunked like _sample_field.
    """
    P = np.asarray(P, dtype=np.float64)
    if len(P) > _CLEAR_CHUNK:
        parts = [_sample_fields(P[i:i + _CLEAR_CHUNK], src_v, src_t, tree,
                                dv_list, k=k)
                 for i in range(0, len(P), _CLEAR_CHUNK)]
        return [np.concatenate([pt[j] for pt in parts])
                for j in range(len(dv_list))]
    k = min(k or SAMPLE_K, len(src_t))
    _, tri = tree.query(P, k=k)
    if k == 1:
        tri = tri[:, None]
    a = src_v[src_t[tri, 0]]
    b = src_v[src_t[tri, 1]]
    c = src_v[src_t[tri, 2]]
    cp = _closest_point_on_triangles(P[:, None, :], a, b, c)
    d = np.linalg.norm(P[:, None, :] - cp, axis=2)

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
    t = src_t[tri]

    d_best = d.min(axis=1)
    sig = SAMPLE_SIG_A + SAMPLE_SIG_B * d_best
    w = np.exp(-((d - d_best[:, None]) ** 2) / (2.0 * sig[:, None] ** 2))
    w /= w.sum(axis=1, keepdims=True)

    out = []
    for dv in dv_list:
        val = (bu[..., None] * dv[t[..., 0]] + bv[..., None] * dv[t[..., 1]]
               + bw[..., None] * dv[t[..., 2]])
        out.append((w[..., None] * val).sum(axis=1))
    return out


# ---------------------------------------------------------------------------
# Build (called from body_wrap_build.build_field)
# ---------------------------------------------------------------------------

# EARS ARE EXCLUDED FROM EVERY SURFACE, both sides (2026-08-23, in-game).
# Oblivion ships ears as separate meshes (the OB heads are earless) while
# Skyrim bakes them into its head meshes.  Including ears on either side made
# ear-adjacent verts map inconsistently between skull and ear surfaces, which
# tore holes into hairstyles around the ears.  With both surfaces earless,
# hair around the ears follows the SKULL — the ears may poke through hair
# sides exactly as vanilla Skyrim hair allows, and the mesh stays intact.
# Skyrim ear geometry is cut from the target surfaces at build via the
# head-local boxes below (min|x|, y range, z range).
SK_EAR_BOXES = {
    # Human ears.  The z range USED to be -9.0..7.0, which reached JAW/NECK
    # level: 12 of the 91 captured verts sat at z -8..-4 and were flattened
    # by 1.2-1.4 units, deleting real skull BEHIND AND BELOW the ear.  Hair
    # then conformed into a surface that is not there and came out under the
    # skin (in-game 2026-08-24).  The ear proper is the z 0..8 cluster (77
    # verts); the range is now bounded to it.
    'human': (5.2, -2.8, 3.8, -0.5, 8.0),
    # Khajiit ears sit on TOP of the head, so the box has to separate them
    # from the CROWN rather than from the side of the skull.  |x| > 2.5 was
    # far too generous: it captured 120 verts spanning z 9.09-14.85 and
    # flattened them by up to 24.9 units -- the entire top of the skull,
    # not an ear.  Hair conformed to that collapsed surface and the real
    # crown then poked straight through it, which is the khajiit half of
    # the "beast hair floats off the head" report (in-game 2026-08-25).
    # Measured on maleheadkhajiit: at z > 9 the ear verts stand at |x|
    # 4.0-8.05 while 54 genuine skull verts sit at |x| <= 4.0 and reach z
    # 12.0, so 4.0 is the separating plane.  The ears also lie entirely
    # forward of the nape (y 2.18-7.57), which bounds the box in y.
    'khajiit': (4.0, 1.5, 99.0, 9.0, 99.0),
}

# RACE PACKS.  Oblivion authors race-specific heads (headkhajiit/headargonian/
# headorc), and Skyrim ships its own khajiit and argonian head meshes while
# every other race chargen-morphs the shared malehead/femalehead (censused
# over all 766 vanilla HDPTs — wood elf, high elf, orc all share it).  A hair
# authored for one of these races (they are race-restricted records, routed by
# the same EDID tokens as the HDPT RNAM lists) is fitted by ITS OWN pair's
# displacement field.
# race -> (OB head rel, SK mesh name per gender or None for the shared human
#          head, SK ear-box key or None)
_RACE_PACKS = {
    'khajiit': (('khajiit', 'headkhajiit.nif'),
                {'male': 'maleheadkhajiit.nif',
                 'female': 'femaleheadkhajiit.nif'}, 'khajiit'),
    'argonian': (('argonian', 'headargonian.nif'),
                 {'male': 'maleheadargonian.nif',
                  'female': 'femaleheadargonian.nif'}, None),
    # NO orc pack: Skyrim orcs use the shared human head, and the OB orc
    # SCALP differs from the human one by only 0.36 mean (the orc-specific
    # differences are brow and jaw, where hair never sits) — orc hair fits
    # through the human pair.
}


def _visible_exterior(v_local, tris):
    """Vert mask: radially visible from outside the head (exterior surface).

    The OB head carries INTERIOR geometry — the mouth bag and inner
    structures — whose nearest-point correspondence forms +-3-unit dipoles
    (the bag maps forward onto the lips, the inner column backward onto the
    skull).  A face-covering mask sampling near them tore 4.9-unit edge
    strain.  A vertex counts as exterior when the ray from well outside the
    head toward it (radially from the head-local origin) reaches it without
    first crossing the mesh; interior verts leave the field domain and get
    their dv in-filled from the exterior field.
    """
    n = len(v_local)
    r = np.linalg.norm(v_local, axis=1)
    d = v_local / np.maximum(r, 1e-6)[:, None]
    start = v_local + d * 25.0                    # well outside the head
    dirs = -d                                     # toward the vertex
    a = v_local[tris[:, 0]]
    e1 = v_local[tris[:, 1]] - a
    e2 = v_local[tris[:, 2]] - a
    vis = np.ones(n, dtype=bool)
    for i0 in range(0, n, 128):                   # chunked Moller-Trumbore
        s = slice(i0, min(i0 + 128, n))
        D = dirs[s][:, None, :]
        pv = np.cross(np.broadcast_to(D, (s.stop - s.start,) + e2.shape),
                      e2[None, :, :])
        det = np.einsum('ti,pti->pt', e1, pv)
        ok = np.abs(det) > 1e-12
        inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
        tv = start[s][:, None, :] - a[None, :, :]
        u = np.einsum('pti,pti->pt', tv, pv) * inv
        qv = np.cross(tv, e1[None, :, :])
        vv = np.einsum('pti,pti->pt', np.broadcast_to(D, qv.shape), qv) * inv
        t = np.einsum('ti,pti->pt', e2, qv) * inv
        reach = 25.0 - 0.05                       # distance to the vertex
        hit = (ok & (u >= -1e-6) & (vv >= -1e-6) & (u + vv <= 1 + 1e-6)
               & (t > 1e-4) & (t < reach))
        vis[s] = ~hit.any(axis=1)
    return vis


def _ear_mask(sk_v, box_key, o_sk):
    """Mask of the SK head verts that belong to the ear shell.

    Selection only -- the capping itself is _flatten_ears_with_tris, which
    needs the triangle set.  The box is per head type because the ear sits
    somewhere different on each: human ears are on the SIDE of the skull,
    khajiit ears on TOP of it (see SK_EAR_BOXES).
    """
    box = SK_EAR_BOXES.get(box_key)
    if box is None:
        return np.zeros(len(sk_v), dtype=bool)
    xmin, ymin, ymax, zmin, zmax = box
    lc = sk_v - np.asarray(o_sk, dtype=np.float64)
    return ((np.abs(lc[:, 0]) > xmin)
            & (lc[:, 1] > ymin) & (lc[:, 1] < ymax)
            & (lc[:, 2] > zmin) & (lc[:, 2] < zmax))


def _flatten_ears_with_tris(sk_v, sk_t, box_key, o_sk, limit=None):
    """Cap the ear opening with a curve fitted to the surrounding skull.

    `limit` restricts flattening to the first N verts (the head portion of a
    neck-extended surface) so shoulder verts can never fall in the ear box.
    """
    m = _ear_mask(sk_v, box_key, o_sk)
    if limit is not None:
        m[limit:] = False
    if not m.any():
        return sk_v, m
    keep = sk_t[~m[sk_t].any(axis=1)]
    if not len(keep):
        return sk_v, np.zeros(len(sk_v), dtype=bool)

    # CAP THE OPENING WITH A CURVE, NOT A FLAT LID (2026-08-24, in-game).
    # Projecting the ear verts straight onto the surrounding triangles makes
    # a flat plate across the ear hole: hair draping over it reads as "below
    # the surface" and the plate's rim distorts nearby hair.  Instead the
    # skull AROUND the opening is extended smoothly across it -- each capped
    # vertex is placed on a quadratic fitted to the surviving ring, so the
    # cap continues the head's own curvature and the surface stays smooth.
    # verts on the boundary of the hole (they belong to a surviving tri AND
    # touch a removed one) -- the ring whose curvature the cap continues
    on_keep = np.zeros(len(sk_v), dtype=bool)
    on_keep[np.unique(keep)] = True
    border = np.zeros(len(sk_v), dtype=bool)
    border[np.unique(sk_t[m[sk_t].any(axis=1)])] = True
    u = np.array([0.0, 1.0, 0.0])
    v = np.array([0.0, 0.0, 1.0])
    cap_lift = []
    orig_v = sk_v.copy()
    for side in (+1.0, -1.0):                    # each ear fitted separately
        sm = m & (np.sign(sk_v[:, 0] - o_sk[0]) == side)
        if not sm.any():
            continue
        rm = border & on_keep & (np.sign(sk_v[:, 0] - o_sk[0]) == side)
        if rm.sum() < 6:                          # too small to fit a quadric
            tree = cKDTree(sk_v[keep].mean(axis=1))
            sk_v[sm] = _project_exact(sk_v[sm], sk_v, keep, tree, k=12)
            continue
        n = np.array([side, 0.0, 0.0])
        c = sk_v[rm].mean(axis=0)
        P = sk_v[rm] - c
        a, b = P @ u, P @ v
        h = P @ n
        A = np.stack([np.ones_like(a), a, b, a * a, a * b, b * b], axis=1)
        try:
            coef, *_ = np.linalg.lstsq(A, h, rcond=None)
        except np.linalg.LinAlgError:
            tree = cKDTree(sk_v[keep].mean(axis=1))
            sk_v[sm] = _project_exact(sk_v[sm], sk_v, keep, tree, k=12)
            continue
        Q = sk_v[sm] - c
        qa, qb = Q @ u, Q @ v
        Aq = np.stack([np.ones_like(qa), qa, qb, qa * qa, qa * qb, qb * qb],
                      axis=1)
        fitted = (c + qa[:, None] * u + qb[:, None] * v
                  + (Aq @ coef)[:, None] * n)

        # THE CAP MUST MEET THE RIM EXACTLY.  The quadric alone is a global
        # least-squares fit, so at the join it sat INSIDE the real head --
        # measured -0.031 mean, -0.324 worst, i.e. the cap UNDERSHOOTS the
        # skull exactly where hair behind the ear rests, which is the
        # recurring "behind the ear is under the skin" defect (in-game
        # 2026-08-25).  Clamping to the straight projection made it worse:
        # that projection is itself the undershoot.  Instead the residual at
        # the rim (real skull minus quadric) is carried INTO the opening by
        # inverse-distance blending, so the cap interpolates the boundary
        # exactly and relaxes to the quadric further in.
        sk_v[sm] = fitted
        cap_lift.append(sm)

    # THE CAP MUST NOT UNDERSHOOT THE REAL SKULL.  A fitted quadric is a
    # least-squares surface, so most of it ended up INSIDE the head: 64 of
    # 81 cap verts, worst -0.575.  Hair conforms to the cap, so behind the
    # ear it started under the skin before any floor ran -- the recurring
    # "behind the ear" defect (in-game 2026-08-25).  Any cap vertex sitting
    # inside the real head is lifted back onto it, so the earless head
    # MATCHES the real geometry everywhere except the ear shell it removes.
    if cap_lift:
        lift = np.zeros(len(sk_v), dtype=bool)
        for sm_i in cap_lift:
            lift |= sm_i
        tree_real = cKDTree(orig_v[sk_t].mean(axis=1))
        c_in = _signed_clearance(sk_v[lift], orig_v, sk_t, tree_real)
        idx = np.where(lift)[0][c_in < 0.0]
        if len(idx):
            sk_v[idx] = _project_exact(sk_v[idx], orig_v, sk_t, tree_real,
                                       k=12)
    return sk_v, m


def fit_race_for_hair(edid: str):
    """The race pack a hair record should be fitted with, or None (human).

    Matches on the EditorID the same way the HDPT RNAM routing does —
    Oblivion names every race-specific hair for its race.
    """
    low = (edid or '').lower()
    for race in _RACE_PACKS:
        if race in low:
            return race
    return None


# BEAST HEAD GEAR NEEDS ITS OWN MESH (2026-08-27).  Hair is authored per race
# and so carries its race in the EDID (fit_race_for_hair above), but a HOOD or
# HELMET is ONE Oblivion record worn by every race — there is no token to read.
# Fitting that one mesh through the human field is what put converted hoods
# inside khajiit/argonian skulls: measured head-local, the khajiit SK head
# reaches z 14.85 and |x| 8.47 against the human head's 11.51 / 6.85, and over
# the scalp region a hood sits on, beast verts stand a mean 1.91 (khajiit) /
# 1.40 (argonian) — max 6.87 / 4.29 — proud of the human surface.  A uniform
# 0.8-unit authored standoff run through the human field lands 0.90 mean /
# 2.01 max from the real khajiit head (0.95 / 2.89 argonian); through the
# race's own field it stays 0.48 / 1.08 (0.51 / 0.94).
#
# Vanilla Skyrim answers this with a MESH PER RACE FAMILY, not with an
# alternate model slot on one record: ArmorIronHelmet lists three armatures
# (IronHelmetAA RNAM=DefaultRace, IronHelmetKhajiitAA RNAM=KhajiitRace,
# IronHelmetArgonianAA RNAM=ArgonianRace), each naming its own reshaped NIF
# (Helmet.nif / HelmetKhajiit.nif / HelmetArgonian.nif).  The same split runs
# through BoneCrown, Blades, Orcish, Dragonscale, Draugr, Dragonplate, Falmer,
# ThalmorHood and every Circlet.  We mirror it exactly: the converter writes a
# <name>_khajiit.nif / <name>_argonian.nif beside the base mesh (nif_converter)
# and the importer emits the matching per-race ARMA (record_types.equipment).
#
# Khajiit and Argonian get SEPARATE variants, never one shared "beast" mesh:
# the two skulls differ from each other as much as either differs from the
# human one (khajiit ears sit on TOP of the crown, argonian snout runs to
# y 15.39 against khajiit's 13.63).
BEAST_RACES = ('khajiit', 'argonian')


def beast_variant_suffix(race: str) -> str:
    """Filename suffix for a beast head-gear variant ('_khajiit')."""
    return '_' + race


def beast_races_available(female: bool) -> tuple:
    """The beast race packs whose field data is actually built, in order.

    Empty when the fit data is missing entirely — callers then write only the
    base mesh and emit only the default ARMA, which is the pre-2026-08-27
    behaviour and never worse than it.
    """
    fit = _get(female)
    if fit is None:
        return ()
    return tuple(r for r in BEAST_RACES if r in fit.races)


def _load_unskinned(char_dir, rel):
    """(verts_face_local, tris) of an unskinned OB face-part mesh, or None."""
    from asset_convert.character.wrap_mesh import read_nif, geom_triangles
    from asset_convert.nif.pyffi_monkey_patch import apply_patches
    apply_patches()
    from pyffi.formats.nif import NifFormat
    path = Path(char_dir).joinpath(*rel)
    if not path.exists():
        return None
    data = read_nif(path)
    v_parts, t_parts = [], []
    off = 0
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not isinstance(block, (NifFormat.NiTriShape,
                                      NifFormat.NiTriStrips)):
                continue
            gd = block.data
            if gd is None or gd.num_vertices == 0:
                continue
            verts = np.array([[p.x, p.y, p.z] for p in gd.vertices],
                             dtype=np.float64)
            try:
                G = np.array(block.get_transform(root).as_list(),
                             dtype=np.float64)
                if not np.allclose(G, np.eye(4), atol=1e-6):
                    verts = verts @ G[:3, :3] + G[3, :3]
            except (ValueError, RuntimeError):
                pass
            v_parts.append(verts)
            t_parts.append(geom_triangles(block) + off)
            off += len(verts)
    if not v_parts:
        return None
    return np.vstack(v_parts), np.vstack(t_parts)


def _neck_surfaces(gender):
    """(ob_neck_v, ob_neck_t, sk_neck_v, sk_neck_t) in WORLD coords, or None.

    The OB head mesh ends at local z -3.5 on the BACK of the neck, so hair
    hanging below it had nothing to conform to and kept the occiput's
    backward delta all the way down — a visible gap off the nape/neck.  Both
    fit surfaces are extended with the body meshes' neck columns so the
    field, the refinement and the ear-cover floor all see real skin there.
    """
    try:
        from asset_convert.character.body_wrap_build import load_ob_group, load_sk_surface
        g = load_ob_group(gender)
        if 'body' not in g:
            return None
        sk = load_sk_surface(gender, 'body', 0)
        if sk is None:
            return None

        def cut(v, t, zmin=103.0):
            v = np.asarray(v, dtype=np.float64)
            t = np.asarray(t, dtype=np.int64)
            m = ((v[:, 2] > zmin) & (np.abs(v[:, 0]) < 7.0)
                 & (v[:, 1] > -9.0) & (v[:, 1] < 9.0))
            keep = m[t].all(axis=1)
            t2 = t[keep]
            if not len(t2):
                return None
            used = np.unique(t2)
            remap = -np.ones(len(v), dtype=np.int64)
            remap[used] = np.arange(len(used))
            return v[used], remap[t2]

        a = cut(g['body']['v0'], g['body']['tris'])
        b = cut(sk[0], sk[1])
        if a is None or b is None:
            return None
        return a[0], a[1], b[0], b[1]
    except Exception as e:
        print(f'  [head_fit/{gender}] neck surfaces unavailable: {e}')
        return None


def _relax_field(src_v, src_t, targets, sk_v, sk_t):
    """Smooth+reproject the raw correspondence into per-vertex targets.

    The raw correspondence (UV samples or nearest-point) is locally noisy —
    adjacent scalp verts can disagree by whole units.  FIELD_CYCLES rounds
    of weld-aware graph smoothing of the deltas followed by exact
    reprojection keep only the slowly-varying real skull difference, and the
    final step is a projection: every returned target lies EXACTLY ON the
    (earless) Skyrim skin.  Weld twins share one target by construction.
    """
    wg = _weld_map(src_v)
    n_g = int(wg.max()) + 1
    gsum = np.zeros((n_g, 3))
    gcnt = np.zeros(n_g)
    np.add.at(gsum, wg, src_v)
    np.add.at(gcnt, wg, 1.0)
    gv = gsum / gcnt[:, None]
    idx, ptr = _adjacency(n_g, wg[src_t])
    tree = cKDTree(sk_v[sk_t].mean(axis=1))

    # group normals of the source scalp: reprojection casts along them, so
    # the correspondence cannot drift laterally and reaches the SK nape
    # bulge that nearest-point projection exits sideways from
    gn = np.zeros((n_g, 3))
    np.add.at(gn, wg, _vertex_normals(src_v, src_t))
    gn /= np.maximum(np.linalg.norm(gn, axis=1, keepdims=True), 1e-12)

    dsum = np.zeros((n_g, 3))
    np.add.at(dsum, wg, np.asarray(targets, dtype=np.float64) - src_v)
    dg = dsum / gcnt[:, None]
    for _ in range(FIELD_CYCLES):
        dg = _smooth(dg, idx, ptr, FIELD_SMOOTH_PASSES)
        proj = _project_ray(gv + dg, gn, sk_v, sk_t, tree)
        dg = proj - gv
    return gv[wg] + dg[wg]


def build_arrays(head_v0, head_tris, sk_surface, char_dir,
                 o_ob, o_sk, gender='male') -> dict:
    """The hf_* arrays body_wrap_build.build_field stores in the field npz.

    head_v0 / head_tris   OB head group, authored T-pose, WORLD coords
    sk_surface            (verts, tris) of the real Skyrim head, WORLD
    char_dir              OB characters\\ dir (for the race heads)
    o_ob / o_sk           head-space origins (Bip01 Head / NPC Head world)
    gender                which npz this is ('male'/'female'), selecting the
                          gendered Skyrim mesh in each race pack
    """
    head_v0 = np.asarray(head_v0, dtype=np.float64)
    head_tris = np.asarray(head_tris, dtype=np.int64)
    o_ob = np.asarray(o_ob, dtype=np.float64)
    o_sk = np.asarray(o_sk, dtype=np.float64)

    sk_v_raw, sk_t_raw = sk_surface
    sk_v_raw = np.asarray(sk_v_raw, dtype=np.float64)
    sk_t_raw = np.asarray(sk_t_raw, dtype=np.int64)
    n_head_sk = len(sk_v_raw)

    # BOTH surfaces are extended with the body meshes' NECK columns: the OB
    # head ends at local z -3.5 on the back, so hair below it had nothing to
    # conform to (visible gap off the nape/neck).
    src_v, src_t = head_v0, head_tris
    neck = _neck_surfaces(gender)
    if neck is not None:
        onv, ont, snv, snt = neck
        src_v = np.vstack([head_v0, onv])
        src_t = np.vstack([head_tris, ont + len(head_v0)])
        sk_full_ext = np.vstack([sk_v_raw, snv])
        sk_t = np.vstack([sk_t_raw, snt + n_head_sk])
    else:
        sk_full_ext = sk_v_raw.copy()
        sk_t = sk_t_raw

    # the field domain is the VISIBLE EXTERIOR surface only (see
    # _visible_exterior); interior mouth-bag geometry is excluded
    vis = _visible_exterior(src_v - o_ob, src_t)
    field_tris = src_t[vis[src_t].all(axis=1)]
    if not len(field_tris):
        field_tris = src_t
    carrier = src_v - o_ob + o_sk

    # the races-tri morph each group's target head wears (head verts only;
    # the appended neck is unmorphed)
    group_deltas = _group_head_deltas(gender, n_head_sk, len(sk_full_ext))

    def _build_one(morph_delta):
        """(dv, sk_flat_v) for one target head (base or group-morphed)."""
        skv = sk_full_ext.copy()
        if morph_delta is not None:
            skv += morph_delta
        # Target surface: the head with its EARS CAPPED — ear-box verts are
        # projected onto the surrounding skull (_flatten_ears), closing the
        # opening at the REAL skull contour (an OB-socket cap recessed the
        # region and sank short hair under the skin; cut triangles left a
        # rim that attracted projections outward).  Only HEAD verts flatten.
        flat, _m = _flatten_ears_with_tris(skv.copy(), sk_t, 'human', o_sk,
                                           limit=n_head_sk)
        # NEAREST-POINT init from the identity carrier (the shared FaceGen
        # UV layout was rejected as init: ~2 units of v-coordinate bias).
        init = _project_exact(carrier, flat, sk_t,
                              cKDTree(flat[sk_t].mean(axis=1)))
        targets = _relax_field(src_v, field_tris, init, flat, sk_t)
        dv = (targets - o_sk) - (src_v - o_ob)
        hidden = ~vis
        if hidden.any() and len(field_tris):
            v_local = src_v - o_ob
            tree_f = cKDTree(v_local[field_tris].mean(axis=1))
            dv[hidden] = _sample_field(v_local[hidden], v_local, field_tris,
                                       tree_f, dv)
        return dv, flat

    dv, sk_flat = _build_one(None)
    out = {
        'hf_src_v': src_v.astype(np.float32),
        'hf_src_t': field_tris.astype(np.int32),
        'hf_sk_v': sk_flat.astype(np.float32),
        'hf_sk_t': sk_t.astype(np.int32),
        'hf_dv': dv.astype(np.float32),
        'hf_skf_v': sk_full_ext.astype(np.float32),
        'hf_n_head': np.array([len(head_v0)], dtype=np.int64),
        'hf_o_ob': o_ob,
        'hf_o_sk': o_sk,
        # format marker: v4 = neck-extended surfaces + per-race-GROUP fields
        # (baked meshes per group; a races.tri on hair is NOT applied by the
        # engine).  The loader requires it, so a stale npz can never
        # silently feed the new runtime.
        'hf_v4': np.array([1], dtype=np.int8),
    }
    for gname, delta in group_deltas.items():
        gdv, gflat = _build_one(delta)
        out[f'hf_g_{gname}_dv'] = gdv.astype(np.float32)
        out[f'hf_g_{gname}_sk_v'] = gflat.astype(np.float32)
        out[f'hf_g_{gname}_skf_v'] = (sk_full_ext + delta).astype(np.float32)
    for race in _RACE_PACKS:
        out.update(_build_race_pack(race, char_dir, gender, o_ob, o_sk) or {})
    return out



def _group_head_deltas(gender, n_head, n_total):
    """{group: (n_total,3) delta} — the races-tri morph each group wears.

    Head verts (the first n_head, matching the races tri vert order) carry
    the morph; the appended neck rows are zero.  Empty when the tri cannot
    be fetched or its vertex count mismatches.
    """
    out = {}
    try:
        from asset_convert.sources.skyrim_assets import get_asset_bytes
        from asset_convert.character.facegen_tri import TriFile
        sep = chr(92)
        rel = sep.join(['meshes', 'actors', 'character', 'character assets',
                        ('female' if gender == 'female' else 'male')
                        + 'headraces.tri'])
        raw = get_asset_bytes(rel)
        if raw is None:
            print(f'  [head_fit/{gender}] races tri unavailable')
            return out
        tri = TriFile.from_bytes(raw)
        if len(tri.vertices) != n_head:
            print(f'  [head_fit/{gender}] races tri vert count '
                  f'{len(tri.vertices)} != head {n_head}')
            return out
        for gname, morph in GROUP_MORPHS.items():
            deltas = tri.morphs.get(morph)
            if deltas is None:
                continue
            D = np.zeros((n_total, 3))
            D[:n_head] = np.asarray(deltas, dtype=np.float64)
            out[gname] = D
    except Exception as e:
        print(f'  [head_fit/{gender}] group head deltas failed: {e}')
    return out



def _build_race_pack(race, char_dir, gender, o_ob, o_sk):
    """hfr_<race>_* arrays (surfaces + displacement field) for one race.

    The OB source is that race's own head (earless by authoring); the SK
    target is the race's Skyrim head with its ears flattened.  The field
    initialises from nearest-point over the identity carrier (the race pairs
    have no verified shared UV layout) and relaxes exactly like the human
    field.  A race whose own SK mesh cannot be fetched is skipped entirely —
    the runtime then falls back to the human field.
    """
    from asset_convert.character.body_wrap_build import head_uv_geometry
    head_rel, sk_names, ear_box = _RACE_PACKS[race]
    o_ob = np.asarray(o_ob, dtype=np.float64)
    o_sk = np.asarray(o_sk, dtype=np.float64)

    path = Path(char_dir).joinpath(*head_rel)
    if not path.exists():
        return None
    got = head_uv_geometry(path)                  # skinned heads (world)
    if got is not None:
        hv, ht = got[0], got[1]
    else:
        got = _load_unskinned(char_dir, head_rel)  # unskinned fallback
        if got is None:
            return None
        hv, ht = got[0] + o_ob, got[1]
    hv = np.asarray(hv, dtype=np.float64)
    ht = np.asarray(ht, dtype=np.int64)

    if sk_names is None:
        return None
    from asset_convert.sources.skyrim_assets import get_body_nif_bytes
    raw = get_body_nif_bytes(sk_names[gender])
    got = head_uv_geometry(raw) if raw else None
    if got is None:
        return None
    sk_v = np.asarray(got[0], dtype=np.float64)
    sk_t = np.asarray(got[1], dtype=np.int64)
    # Vanilla head meshes are inconsistent about their stored frame:
    # malehead/maleheadkhajiit carry world coordinates, maleheadargonian
    # head-bone-local ones.  A head centered anywhere near the origin is
    # local -- lift it to world with the head bone translation.
    if np.abs(sk_v).max() < 60.0:
        sk_v = sk_v + o_sk
    # Keep the REAL (un-flattened) head as well: hair conforms to the
    # flattened surface, so the clip floor needs the real one to tell where
    # the actual ear/skin sits.  Without it the floor had to be disabled for
    # beast races entirely, and every khajiit/argonian style shipped with
    # hair inside the head (measured 2026-08-24: 1808 verts across 10
    # styles, khajiit worst -- its ears sit on TOP of the skull, so
    # flattening removes a large area the hair then sinks into).
    sk_full = sk_v.copy()
    sk_v, _m = _flatten_ears_with_tris(sk_v.copy(), sk_t, ear_box, o_sk)

    carrier = hv - o_ob + o_sk
    init = _project_exact(carrier, sk_v, sk_t,
                          cKDTree(sk_v[sk_t].mean(axis=1)))
    vis = _visible_exterior(hv - o_ob, ht)
    field_tris = ht[vis[ht].all(axis=1)]
    if not len(field_tris):
        field_tris = ht
    targets = _relax_field(hv, field_tris, init, sk_v, sk_t)
    dv = (targets - o_sk) - (hv - o_ob)
    hidden = ~vis
    if hidden.any() and len(field_tris):
        v_local = hv - o_ob
        tree_f = cKDTree(v_local[field_tris].mean(axis=1))
        dv[hidden] = _sample_field(v_local[hidden], v_local, field_tris,
                                   tree_f, dv)

    return {
        f'hfr_{race}_src_v': hv.astype(np.float32),
        f'hfr_{race}_src_t': field_tris.astype(np.int32),
        f'hfr_{race}_sk_v': sk_v.astype(np.float32),
        f'hfr_{race}_skf_v': sk_full.astype(np.float32),
        f'hfr_{race}_sk_t': sk_t.astype(np.int32),
        f'hfr_{race}_dv': dv.astype(np.float32),
    }


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------

class _SurfPack:
    """One scalp + its field: head-LOCAL verts, tris, tri KD-tree, deltas."""

    def __init__(self, v_world, t, dv, origin):
        self.v = np.asarray(v_world, dtype=np.float64) - origin
        self.t = np.asarray(t, dtype=np.int64)
        self.dv = np.asarray(dv, dtype=np.float64)
        self.tree = cKDTree(self.v[self.t].mean(axis=1))


class _HeadFit:
    def __init__(self, z):
        self.o_ob = z['hf_o_ob'].astype(np.float64)
        self.o_sk = z['hf_o_sk'].astype(np.float64)
        self.human = _SurfPack(z['hf_src_v'], z['hf_src_t'], z['hf_dv'],
                               self.o_ob)
        # target surface kept for validation/tests (SK-head-LOCAL frame)
        self.sk_v = z['hf_sk_v'].astype(np.float64) - self.o_sk
        self.sk_t = z['hf_sk_t'].astype(np.int64)
        # the REAL head with ears intact (same tris) — the ear-cover floor
        self.sk_full_v = (z['hf_skf_v'].astype(np.float64) - self.o_sk
                          if 'hf_skf_v' in z else None)
        # per-race-GROUP fields (elves/orc) sharing the human src surface:
        # group -> (dv, sk_flat_local, skf_local)
        self.groups = {}
        for g in GROUP_MORPHS:
            if f'hf_g_{g}_dv' not in z:
                continue
            self.groups[g] = (
                z[f'hf_g_{g}_dv'].astype(np.float64),
                z[f'hf_g_{g}_sk_v'].astype(np.float64) - self.o_sk,
                z[f'hf_g_{g}_skf_v'].astype(np.float64) - self.o_sk)
        self.races = {}
        self.races_sk = {}      # race -> (sk_v local, sk_t), for validation
        self.races_full = {}    # race -> (REAL eared head, sk_t): clip floor
        for race in _RACE_PACKS:
            if f'hfr_{race}_dv' not in z:
                continue
            self.races[race] = _SurfPack(
                z[f'hfr_{race}_src_v'], z[f'hfr_{race}_src_t'],
                z[f'hfr_{race}_dv'], self.o_ob)
            self.races_sk[race] = (
                z[f'hfr_{race}_sk_v'].astype(np.float64) - self.o_sk,
                z[f'hfr_{race}_sk_t'].astype(np.int64))
            # the REAL (un-flattened) beast head, for the clip floor
            if f'hfr_{race}_skf_v' in z:
                self.races_full[race] = (
                    z[f'hfr_{race}_skf_v'].astype(np.float64) - self.o_sk,
                    z[f'hfr_{race}_sk_t'].astype(np.int64))


_CACHE: dict = {}


def _get(female: bool):
    key = bool(female)
    if key in _CACHE:
        return _CACHE[key]
    fit = None
    if _SCIPY:
        path = _GEN_DIR / f'body_wrap_{"female" if female else "male"}.npz'
        if path.exists():
            try:
                with np.load(path) as z:
                    if 'hf_v4' in z:
                        fit = _HeadFit(z)
            except Exception:
                fit = None
    _CACHE[key] = fit
    return fit


def fit_available(female: bool) -> bool:
    return _get(female) is not None


def field_deltas(P, female: bool, race=None):
    """Per-vertex field deltas for head-LOCAL points P, or None.

    Each point samples the displacement field at its closest point on the
    OB scalp — the same mapping fit_head_gear applies.  Used by the skinned
    head-gear path so a skinned helmet's head-weighted verts move by exactly
    what a rigid helmet would move.
    """
    fit = _get(female)
    if fit is None:
        return None
    pack = fit.races.get(race, fit.human)
    P = np.asarray(P, dtype=np.float64)
    if not len(P):
        return np.zeros((0, 3))
    return _sample_field(P, pack.v, pack.t, pack.tree, pack.dv)


def fit_head_gear(shapes, female: bool, race=None, group=None,
                  cover_ears=False, hug=False):
    """Fit head gear from OB face space into SK head space.

    `shapes` is a list of (verts (N,3), tris (M,3)) in authored face-space
    coordinates.  Fitting is a pure per-vertex function of position
    (coincident seam twins and cross-shape seams sample identical deltas by
    construction), so shapes need no joint solve.  `race` selects a beast
    race pack (see _RACE_PACKS); `group` selects a race-GROUP target head
    ('elves' / 'orc', see GROUP_MORPHS) — None fits the base head all five
    human races and Dremora wear.  `hug` (hair only) compresses the authored
    near-skin standoff toward the vanilla hug — helmets keep their authored
    clearance exactly.  Returns a list of fitted vert arrays (SK head space)
    or None when no fit data exists.
    """
    return _fit_core(shapes, female, race, group, cover_ears=cover_ears,
                     hug=hug)


def _fit_core(shapes, female, race, group, cover_ears=False, hug=False):
    fit = _get(female)
    if fit is None or not shapes:
        return None
    pack = fit.races.get(race, fit.human)
    grp = fit.groups.get(group) if (group and race is None) else None
    dv_active = grp[0] if grp is not None else pack.dv

    V = np.vstack([np.asarray(v, dtype=np.float64) for v, _t in shapes])
    offs = np.cumsum([0] + [len(v) for v, _t in shapes])
    if not len(V):
        return [np.asarray(v, dtype=np.float64).copy() for v, _t in shapes]

    D_all = _sample_fields(V, pack.v, pack.t, pack.tree, [dv_active])

    # FAR GEOMETRY FOLLOWS ITS ATTACHMENT, IT IS NOT RE-SAMPLED.  A hanging
    # tail's verts sit many units from the scalp, where per-vertex sampling
    # jitters between candidate neighborhoods (measured: 19% mean edge
    # stretch on the lengthened style01 tail).  Verts beyond FAR_DIST take
    # their deltas by diffusion over the mesh graph from the near verts
    # instead — the tail translates rigidly with the scalp region it hangs
    # from, keeping its authored length (the sampled values seed the
    # diffusion, so shapes with no near verts keep them).
    d_best, _ = pack.tree.query(V)
    far = d_best > FAR_DIST
    tris_all = [np.asarray(t, dtype=np.int64) + offs[i]
                for i, (_v, t) in enumerate(shapes) if len(t)]
    if far.any() and tris_all:
        wg = _weld_map(V)
        n_g = int(wg.max()) + 1
        gcnt = np.zeros(n_g)
        np.add.at(gcnt, wg, 1.0)
        gfar = np.ones(n_g, dtype=bool)
        np.logical_and.at(gfar, wg, far)
        idx, ptr = _adjacency(n_g, wg[np.vstack(tris_all)])
        if idx is not None:
            deg = np.maximum(np.diff(ptr), 1)[:, None]
            empty_g = np.diff(ptr) == 0
            starts = np.minimum(ptr[:-1], max(len(idx) - 1, 0))
            for fi in range(len(D_all)):
                D = D_all[fi]
                gD = np.zeros((n_g, 3))
                np.add.at(gD, wg, D)
                gD /= gcnt[:, None]
                for _ in range(FAR_MAX_ITERS):
                    m = np.add.reduceat(gD[idx], starts, axis=0) / deg
                    m[empty_g] = gD[empty_g]
                    delta = np.abs(m[gfar] - gD[gfar]).max()                         if gfar.any() else 0
                    gD[gfar] = m[gfar]
                    if delta < FAR_TOL:
                        break
                D[far] = gD[wg[far]]

    out = V + D_all[0]

    # CLEARANCE REFINEMENT.  The field sample is blurred (SAMPLE_SIG) and
    # leaves a small systematic residual at high-curvature bands — the front
    # hairline and the nape read "ever so slightly too far" in game
    # (2026-08-24).  Near-skin verts get a capped, graph-smoothed correction
    # to their TARGET clearance against the fitted surface: the exact
    # authored clearance normally (helmets), or the vanilla-HUG compression
    # of it for hair (hug=True; see _hug).  Hug mode iterates because the
    # smoothing dilutes each round's step; helmets keep the original single
    # pass, so their output is bit-identical to before.
    if race is not None:
        tgt = fit.races_sk.get(race)
    elif grp is not None:
        tgt = (grp[1], fit.sk_t)
    else:
        tgt = (fit.sk_v, fit.sk_t)
    c0 = None
    if tgt is not None and len(tris_all):
        sk_v_t, sk_t_t = tgt
        c0 = _signed_clearance(V, pack.v, pack.t, pack.tree)
        near = np.abs(c0) < REFINE_MAX_C0
        if near.any():
            tree_t = cKDTree(sk_v_t[sk_t_t].mean(axis=1))
            # The mesh's OWN authored scalp standoff drives both the hug
            # ramp and the fade below (see _hug).  Measured over the on-head
            # band only: clearance in (-0.5, 3.0), which is the scalp and
            # excludes both hair tunnelling through the head and long
            # strands hanging off it.
            _band = c0[(c0 > -0.5) & (c0 < 3.0)]
            _base = float(np.median(_band)) if len(_band) >= 16 else None
            # THE FADE IS RELATIVE TO THE MESH'S OWN STANDOFF, for the same
            # reason the hug ramp is.  A fixed sigma treats "far from the
            # skin" as an absolute distance, so a mesh Oblivion authored
            # loosely was exempted from the very correction that would pull
            # it in: at the beast standoff (c0 ~ 1.4) the weight was 0.42,
            # roughly halving every step, and three capped+smoothed rounds
            # then could not cover the 1.2 units the hug asked for.  Beast
            # hair shipped at +1.06 against a +0.22 target -- the argonian/
            # khajiit float (in-game 2026-08-25).  Scaling sigma by the same
            # factor as the ramp keeps the fade's MEANING ("this vertex is
            # far out for THIS mesh") while making it scale-free.
            _sig = REFINE_C0_SIGMA * (max(_base / _HUG_BASE_REF, 1.0)
                                      if _base else 1.0)
            w = np.exp(-(np.abs(c0[near]) / _sig) ** 2)
            target = _hug(c0[near], _base) if hug else c0[near]
            wg_r = _weld_map(V)
            n_gr = int(wg_r.max()) + 1
            gcnt = np.zeros(n_gr)
            np.add.at(gcnt, wg_r, 1.0)
            idx_r, ptr_r = _adjacency(n_gr, wg_r[np.vstack(tris_all)])
            # THE ROUND BUDGET FOLLOWS THE DEMANDED TRAVEL.  Each round is
            # capped at REFINE_CAP and then smoothed, so a fixed 3 rounds
            # can only ever deliver about one cap's worth of net movement --
            # enough for human hair (asked ~0.2) and not for a mesh asked to
            # come in by 1.2, which is what the beast styles need once the
            # ramp is relative.  argonianfins settled at +0.75 against a
            # +0.22 target with 3 rounds.  Rounds are added in proportion to
            # the largest correction actually asked for, and the loop still
            # exits early the moment the step falls below 0.02, so nothing
            # that already converges pays for them.
            _demand = float(np.abs(target - c0[near]).max()) if hug else 0.0
            _iters = (min(REFINE_ITERS
                          + int(np.ceil(_demand / max(REFINE_CAP, 1e-6))),
                          REFINE_ITERS_MAX)
                      if hug else 1)
            for _ in range(_iters):
                c1, n1 = _signed_clearance(out[near], sk_v_t, sk_t_t,
                                           tree_t, want_normals=True)
                step = np.clip(target - c1, -REFINE_CAP, REFINE_CAP) * w
                if np.abs(step).max() < 0.02:
                    break
                corr = np.zeros_like(V)
                corr[near] = step[:, None] * n1
                gsum = np.zeros((n_gr, 3))
                np.add.at(gsum, wg_r, corr)
                gc = gsum / gcnt[:, None]
                gc = _smooth(gc, idx_r, ptr_r, REFINE_SMOOTH_PASSES)
                out = out + gc[wg_r]

    # SKIN FLOOR (hair only) — the anti-clip pass.  Every candidate vertex is
    # held at its authored clearance (clamped to SKIN_FLOOR_MIN) above the
    # head, iterated so
    # the graph smoothing cannot dilute the floor away.
    #
    # THE EAR IS NOT AN OBSTACLE (2026-08-24, in-game).  Pushing hair out to
    # clear the ear SHELL inflated styles around it — badly on High Elves,
    # whose morph moves the ear verts by up to 2.27 vs 0.29 over the rest of
    # the head.  But simply skipping those verts let hair sink into the SIDE
    # OF THE SKULL there (measured: style07 105 triangles below the earless
    # skull, worst -1.04), because the ear floor had been holding that whole
    # region up.  So ear-adjacent verts are floored against the FLATTENED
    # (earless) skull — the same surface the hair was conformed to — and the
    # ear shell itself is ignored.  Hair keeps its shape and may pass through
    # the ear exactly as vanilla hair does, while the skull still stops it.
    # The floor runs for BEAST races too, against that race's own real head
    # (races_full).  It used to be skipped for them entirely — there was no
    # un-flattened surface stored — which shipped every khajiit/argonian
    # style with hair sunk into the skull (measured 2026-08-24: 1808 verts
    # across 10 styles; khajiit worst, its ears sit on TOP of the skull so
    # flattening removes a large area the hair then conforms straight into).
    # THE FLOOR RUNS AGAINST THE HEAD THE GAME RENDERS — ears included.
    #
    # The fit CONFORMS to the ear-capped head (that is what makes hair follow
    # the skull instead of wrapping the ear shell), but the floor must judge
    # penetration against the REAL head, because that is the surface the
    # player sees.  Measuring the floor on the capped head was the systemic
    # bug behind every "hair under the skin" report: shipped style07 had 1
    # vertex under the CAPPED head (so every audit called it clean) and 45
    # under the REAL one, all at |x| 4.8-6.0, z 2.2-7.2 — exactly the bare
    # scalp band seen in game (2026-08-24).  The two surfaces differ on 70
    # verts around the ears, by 0.57 on average.
    # TWO SURFACES, DELIBERATELY.  `cover_v` is the head the floor pushes
    # AGAINST, and it is the EARLESS (capped) one: the ear must never drive
    # hair outward -- with the real head, elf ears (protruding 1.6) shoved
    # ear-adjacent verts up to 1.9 units out, the spikes seen in game
    # (2026-08-25).  `real_v` is the head the game renders, used only to
    # DETECT penetration, because that is the surface the player sees; using
    # the capped head for detection was the old systemic bug that hid hair
    # inside the skin around the ears.  Push on the skull, detect on reality.
    if race is not None:
        rs = fit.races_sk.get(race)
        rf = fit.races_full.get(race)
        cover_v, cover_t = rs if rs is not None else (None, None)
        real_v = rf[0] if rf is not None else cover_v
    else:
        cover_v = grp[1] if grp is not None else fit.sk_v
        cover_t = fit.sk_t
        real_v = grp[2] if grp is not None else fit.sk_full_v
    # THE EAR IS NOT AN OBSTACLE AT ALL — not to push against, and not to
    # detect against either (2026-08-25, in-game).  Detecting on the full
    # head still let the ear GENERATE lift: every runaway vertex measured on
    # shipped elf styles sat at |x| 7.5-9.4, outside the ~6-wide skull, and
    # was driven up to 3.29 units out — hair spiking sideways off elf heads.
    # `real_v` keeps the real skin everywhere the ear is not, so hair can
    # still never sink into the actual head; ear triangles are simply
    # dropped from the detection surface and hair passes through them, as
    # vanilla hair does.
    # The detection surface is the REAL head everywhere EXCEPT the ear, and
    # the CAPPED skull where the ear was.  Deleting the ear triangles
    # outright was tried first and left a HOLE: detection fell through it
    # and generated lift from the far side (style07's worst mover went 0.88
    # -> 1.97, still out past the skull).  Substituting the cap keeps the
    # surface closed, so hair is stopped by the real skin everywhere it can
    # be seen, and simply passes through the ear as vanilla hair does.
    # Only the part of the ear that PROTRUDES past the capped skull is
    # substituted.  The ear box also covers skull BEHIND the ear that sits
    # at or inside the cap; replacing that too flattened it away, so hair
    # there had nothing to stop it and sank up to -0.28 under the skin --
    # the recurring "behind the ear" defect (in-game 2026-08-25).  Keeping
    # the real surface wherever it is not proud of the cap means hair passes
    # only through the ear SHELL, and the skull behind it still stops it.
    _delta = real_v - cover_v
    _out = np.einsum('vi,vi->v', _delta, _unit_out(cover_v, cover_v.mean(0)))
    _shell = _out > 0.02                      # genuinely proud of the skull
    det_v = np.where(_shell[:, None], cover_v, real_v)
    det_t = cover_t
    # NO SLACK ON THE CAP.  Recessing the capped ear region (tried at 0.35)
    # let hair BEHIND the ear sink that far into the skull -- style07 came
    # back with 26 of 50 behind-ear verts under the skin, worst -0.589, the
    # third recurrence of that bug (in-game 2026-08-25).  The cap IS the
    # skull there and must stop hair exactly like any other skin: the ear
    # SHELL is what hair may pass through, and that is already handled by
    # substituting the cap for it, not by pushing the cap inward.
    if cover_ears and cover_v is not None and len(tris_all):
        c0e = c0 if c0 is not None             else _signed_clearance(V, pack.v, pack.t, pack.tree)
        cand = c0e > -0.5
        if cand.any():
            tree_f = cKDTree(cover_v[cover_t].mean(axis=1))
            tree_r2 = cKDTree(det_v[det_t].mean(axis=1))
            # NO standoff fade on the push: this is a CLIP FLOOR, and a
            # vertex authored far from the skin that still lands inside the
            # head needs the full correction, not a faded one.  (It used to
            # fade by EAR_COVER_C0_SIGMA, which weakened exactly the verts
            # most at risk once the hug pulled everything in.)
            wf = np.ones(int(cand.sum()))
            moved_tot = np.zeros(int(cand.sum()))   # cumulative push
            wg_e = _weld_map(V)
            n_ge = int(wg_e.max()) + 1
            gcnt = np.zeros(n_ge)
            np.add.at(gcnt, wg_e, 1.0)
            idx_e, ptr_e = _adjacency(n_ge, wg_e[np.vstack(tris_all)])
            for _ in range(FLOOR_ITERS):
                # detect against the head the game renders...
                cf, _n_real = _signed_clearance(out[cand], det_v, det_t,
                                                tree_r2, want_normals=True)
                # ...but move along the EARLESS skull's normal, so the ear
                # never becomes a direction to be pushed away from
                _c_sk, nf = _signed_clearance(out[cand], cover_v, cover_t,
                                              tree_f, want_normals=True)
                # THE FLOOR FOLLOWS THE AUTHORED POSITION.  A blanket
                # minimum can only push hair AWAY from the head, so every
                # edge vertex Oblivion authored ON the skin (measured: mean
                # +0.079 on style07, -0.062 on style02) got lifted to
                # +0.14..+0.26 -- the front-hairline gap seen on every race
                # (in-game 2026-08-25).  Each vertex instead keeps its OWN
                # authored clearance as its target, so an edge drawn on the
                # skin stays on the skin.  Clamped at SKIN_FLOOR_MIN so
                # deeply-authored hair is not dragged out.  The flat-triangle-
                # over-a-domed-skull dip this used to add a per-vertex chord
                # allowance for is handled by the TRIANGLE PASS below, which
                # measures each triangle's own interior instead of estimating
                # it from the vertex's longest edge.
                fmin = np.minimum(c0e[cand], SKIN_FLOOR_MIN)
                # A vertex sunk past a fold can score a POSITIVE nearest-
                # triangle distance (measured: +0.18 while inside the head),
                # so the floor would never fire on it.  Override the reading
                # with the robust neighborhood vote wherever the two
                # disagree, and treat the depth as negative clearance.
                ins, depth = _inside_surface(out[cand], det_v, det_t,
                                             tree_r2)
                cf = np.where(ins, -depth, cf)
                push = np.clip(fmin - cf, 0.0, FLOOR_CAP) * wf
                # TOTAL push per vertex is bounded.  Without this the
                # inside-vote can re-fire every iteration and compound:
                # measured on style03, one vertex was driven 4.45 units out
                # (x 6.38 -> 9.38), a visible spike of hair (in-game
                # 2026-08-24).  A clip is at most the head's own thickness
                # away, so anything past FLOOR_TOTAL_CAP is a bad reading.
                push = np.minimum(push,
                                  np.maximum(FLOOR_TOTAL_CAP - moved_tot,
                                             0.0))
                moved_tot += push
                if push.max() < 0.02:
                    break
                corr = np.zeros_like(V)
                corr[cand] = push[:, None] * nf
                gsum = np.zeros((n_ge, 3))
                np.add.at(gsum, wg_e, corr)
                gc = gsum / gcnt[:, None]
                gc = _smooth(gc, idx_e, ptr_e, 2)
                out = out + gc[wg_e]

            # TRIANGLE PASS.  A per-VERTEX allowance was a stand-in for what is
            # really a per-TRIANGLE property, and the two requirements
            # (honour the authored depth vs. keep triangles off the skin)
            # cannot both be met through one vertex offset -- every tuning
            # traded the front-hairline gap against crown show-through.
            # Measure each triangle's own deepest interior point and lift
            # only the triangles that actually fail, spreading the lift to
            # their three corners.  Vertices whose triangles are all fine
            # keep exactly the authored depth, so the hairline stays put.
            tree_r = cKDTree(det_v[det_t].mean(axis=1))
            lift_tot = np.zeros(len(out))     # cumulative, see TRI_TOTAL_CAP
            TT = np.vstack(tris_all)
            keep_t = cand[TT].all(axis=1)
            TT = TT[keep_t]
            if len(TT):
                bw = _TRI_SAMPLES
                for _ in range(TRI_PASS_ITERS):
                    Pt = (out[TT[:, 0]][:, None, :] * bw[None, :, 0, None]
                          + out[TT[:, 1]][:, None, :] * bw[None, :, 1, None]
                          + out[TT[:, 2]][:, None, :] * bw[None, :, 2, None])
                    ct = _signed_clearance(Pt.reshape(-1, 3), det_v,
                                           det_t, tree_r)
                    ct = ct.reshape(len(TT), -1).min(axis=1)
                    need = np.clip(TRI_CLEAR_MIN - ct, 0.0, FLOOR_CAP)
                    if need.max() < 0.005:
                        break
                    # AVERAGE the demand over a vertex's triangles, do not
                    # take the MAXIMUM: with max, one bad triangle yanked a
                    # single vertex far out of its neighbourhood -- 105
                    # spikes up to +3.15 on style04, visible as hair
                    # shooting off the head (in-game 2026-08-25).  A shared
                    # dip is fixed by lifting the whole neighbourhood a
                    # little, which averaging does and max does not.  The
                    # per-vertex total is capped for the same reason.
                    lift_s = np.zeros(len(out))
                    lift_n = np.zeros(len(out))
                    for cix in range(3):
                        np.add.at(lift_s, TT[:, cix], need)
                        np.add.at(lift_n, TT[:, cix], 1.0)
                    lift = np.where(lift_n > 0, lift_s / np.maximum(lift_n, 1),
                                    0.0)
                    lift = np.minimum(lift, TRI_LIFT_CAP)
                    # ...and bound the TOTAL, exactly as the vertex floor
                    # does.  Per-iteration capping alone let 10 rounds stack
                    # to 4.0: measured on shipped woodelfmalespiky, verts
                    # moved 3.29 out to |x| 8.2 (the skull is ~6 wide) --
                    # hair spiking sideways off elf heads (in-game
                    # 2026-08-25).
                    lift = np.minimum(lift,
                                      np.maximum(TRI_TOTAL_CAP - lift_tot,
                                                 0.0))
                    lift_tot += lift
                    _cf, nrm = _signed_clearance(out, cover_v, cover_t,
                                                 tree_f, want_normals=True)
                    corr = lift[:, None] * nrm
                    gsum = np.zeros((n_ge, 3))
                    np.add.at(gsum, wg_e, corr)
                    gl = _smooth(gsum / gcnt[:, None], idx_e, ptr_e, 3)
                    out = out + gl[wg_e]

    return [out[offs[i]:offs[i + 1]] for i in range(len(shapes))]
