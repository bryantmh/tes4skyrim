"""Exact body-fit correction for converted armor/clothing meshes.

The FK animation retarget (skin_retarget) only APPROXIMATES the Skyrim rest
pose — it poses the Oblivion mesh using deltas mined from Oblivion's own
animations, so armor lands near, but not on, the Skyrim body (stretched
shirts, seam gaps, clipping).

This module makes the fit exact where the body defines shape:

Build (per gender):
  1. Run the actual Oblivion BODY meshes (upperbody/lowerbody/hand/foot)
     through the very same FK retarget used for armor.
  2. Find the corresponding Skyrim body surface point for every FK-placed
     body vertex (normal-filtered nearest neighbours, smoothed).
  3. Fit ONE AFFINE TRANSFORM PER BONE (weighted least squares over the body
     vertices the bone drives) mapping FK positions → SK body positions.

Apply (per armor mesh, right after retarget_skin_to_skyrim):
     v' = Σ_b w_vb · (v @ R_b + t_b)   using the armor's own skin weights.

Blending per-bone affines through skin weights is exactly as smooth as
skinning itself: no shear inside a bone's region (affine), smooth at weight
boundaries, and armor vertices with similar weights always receive similar
corrections — unlike free-form displacement-field interpolation, which
sheared armor bridging two body regions (armpits: 30-70%% edge stretch).

Field files: asset_convert/generated/body_fit_{male,female}.npz
  bone_names (B,)    — Oblivion bone names (apply runs before bone renaming)
  matrices   (B,4,3) — affine per bone, rows 0-2 rotation/scale, row 3 translation

Build:  python -m asset_convert.body_fit          (requires export/ + references/)
Apply:  apply_body_fit(data, src_path) — after retarget_skin_to_skyrim().
"""

import os
from pathlib import Path

import numpy as np

from . import pyffi_monkey_patch as _patch  # noqa: F401 — before NifFormat

try:
    from pyffi.formats.nif import NifFormat
    _PYFFI = True
except ImportError:
    _PYFFI = False

_REPO = Path(__file__).parent.parent
_GEN_DIR = Path(__file__).parent / 'generated'
_OB_BODY_DIR = _REPO / 'export' / 'Oblivion.esm' / 'meshes' / 'characters' / '_male'
_SK_BODY_DIR = (_REPO / 'references' / 'Skyrim Meshes' / 'meshes' /
                'actors' / 'character' / 'character assets')

# Oblivion body-part NIFs per gender.  src_path passed to the retarget encodes
# gender via '/f/' (that is how the armor pipeline detects female skeletons).
_OB_BODY_SETS = {
    'male':   (['upperbody.nif', 'lowerbody.nif', 'hand.nif', 'foot.nif'], 'armor/m/'),
    'female': (['femaleupperbody.nif', 'femalelowerbody.nif',
                'femalehand.nif', 'femalefoot.nif'], 'armor/f/'),
}

# Skyrim reference body NIFs per gender (weight 0 — the _1 variant is
# produced separately by morph_armor_to_weight1).
_SK_BODY_SETS = {
    'male':   ['malebody_0.nif', 'malehands_0.nif', 'malefeet_0.nif'],
    'female': ['femalebody_0.nif', 'femalehands_0.nif', 'femalefeet_0.nif'],
}

# Correspondence when building the field
_BUILD_K = 6           # nearest SK verts considered per OB vert
_NORMAL_DOT_MIN = 0.0  # reject SK verts facing away (avoids front/back snapping)
_SMOOTH_ITERS = 2      # target-point smoothing passes (8-NN mean over deltas)
_SMOOTH_K = 8
_MIN_BONE_VERTS = 30.0  # effective (weight-sum) verts required for a full affine
_RIDGE = 5.0            # regularization pulling the affine toward identity

# Per-process cache: gender -> (names list, (B,4,3) matrices) or None
_FIELD_CACHE: dict = {}


# ---------------------------------------------------------------------------
# Reading helpers
# ---------------------------------------------------------------------------

def _read_nif(path):
    data = NifFormat.Data()
    with open(path, 'rb') as f:
        data.inspect(f)
        f.seek(0)
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


def _geom_world_transform(block, skel_root):
    from .skin_retarget import _m44_to_np
    try:
        return _m44_to_np(block.get_transform(skel_root))
    except (ValueError, RuntimeError):
        return np.eye(4)


def _geom_verts_world(block, skel_root):
    G = _geom_world_transform(block, skel_root)
    verts = np.array([[v.x, v.y, v.z] for v in block.data.vertices])
    if not np.allclose(G, np.eye(4), atol=1e-6):
        verts = verts @ G[:3, :3] + G[3, :3]
    return verts, G


def _geom_normals_world(block, G):
    d = block.data
    if not getattr(d, 'has_normals', False):
        return np.zeros((d.num_vertices, 3))
    n = np.array([[p.x, p.y, p.z] for p in d.normals])
    if not np.allclose(G[:3, :3], np.eye(3), atol=1e-6):
        n = n @ G[:3, :3]
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    return n / np.maximum(ln, 1e-8)


def _geom_bone_weights(block):
    """{bone_name: (indices array, weights array)} from NiSkinData."""
    skin = block.skin_instance
    skin_data = skin.data
    out = {}
    for bi in range(min(skin.num_bones, skin_data.num_bones)):
        bone = skin.bones[bi]
        if bone is None:
            continue
        name = _block_name(bone)
        be = skin_data.bone_list[bi]
        idx = np.fromiter((vw.index for vw in be.vertex_weights), dtype=np.int64,
                          count=be.num_vertices)
        w = np.fromiter((vw.weight for vw in be.vertex_weights), dtype=np.float64,
                        count=be.num_vertices)
        if name in out:
            pi, pw = out[name]
            idx = np.concatenate([pi, idx])
            w = np.concatenate([pw, w])
        out[name] = (idx, w)
    return out


# ---------------------------------------------------------------------------
# Field construction
# ---------------------------------------------------------------------------

def build_field(gender: str, verbose: bool = True) -> bool:
    """Build and save the per-bone affine body-fit field.  Returns success."""
    from scipy.spatial import cKDTree
    from .nif_converter import OUTPUT_VERSION, OUTPUT_USER_VERSION, OUTPUT_USER_VERSION_2
    from .skin_retarget import retarget_skin_to_skyrim

    ob_names, gender_tag = _OB_BODY_SETS[gender]
    sk_names = _SK_BODY_SETS[gender]

    # --- FK-retarget the OB body meshes; gather verts/normals/weights --------
    v_parts, n_parts = [], []
    bone_accum: dict = {}   # bone name -> list of (global indices, weights)
    offset = 0
    for name in ob_names:
        path = _OB_BODY_DIR / name
        if not path.exists():
            if verbose:
                print(f'  [{gender}] missing OB body mesh: {path}')
            continue
        data = _read_nif(path)
        data.version = OUTPUT_VERSION
        data.user_version = OUTPUT_USER_VERSION
        data.user_version_2 = OUTPUT_USER_VERSION_2
        # gender_tag contains '/f/' so the retarget picks the female skeleton
        retarget_skin_to_skyrim(data, src_path=gender_tag + name)
        for block, skel_root in _iter_skinned_geoms(data):
            verts, G = _geom_verts_world(block, skel_root)
            v_parts.append(verts)
            n_parts.append(_geom_normals_world(block, G))
            for bone, (idx, w) in _geom_bone_weights(block).items():
                bone_accum.setdefault(bone, []).append((idx + offset, w))
            offset += len(verts)
    if not v_parts:
        return False
    ob_v = np.vstack(v_parts)
    ob_n = np.vstack(n_parts)

    # --- SK reference body ----------------------------------------------------
    sk_v_parts, sk_n_parts = [], []
    for name in sk_names:
        path = _SK_BODY_DIR / name
        if not path.exists():
            if verbose:
                print(f'  [{gender}] missing SK body mesh: {path}')
            continue
        data = _read_nif(path)
        for block, skel_root in _iter_skinned_geoms(data):
            verts, G = _geom_verts_world(block, skel_root)
            sk_v_parts.append(verts)
            sk_n_parts.append(_geom_normals_world(block, G))
    if not sk_v_parts:
        return False
    sk_v = np.vstack(sk_v_parts)
    sk_n = np.vstack(sk_n_parts)

    # --- Correspondence: FK OB vert -> SK body surface point -----------------
    tree = cKDTree(sk_v)
    dist, idx = tree.query(ob_v, k=min(_BUILD_K, len(sk_v)))
    cand_n = sk_n[idx]                                # (N, K, 3)
    agree = np.einsum('nki,ni->nk', cand_n, ob_n) > _NORMAL_DOT_MIN
    w = 1.0 / np.maximum(dist, 1e-3) ** 2
    w = np.where(agree, w, 0.0)
    no_agree = w.sum(axis=1) < 1e-12
    if no_agree.any():
        w[no_agree, 0] = 1.0                          # plain nearest fallback
    w /= w.sum(axis=1, keepdims=True)
    target = (w[:, :, None] * sk_v[idx]).sum(axis=1)  # (N, 3)
    deltas = target - ob_v

    # Smooth correspondence noise over the OB body point cloud
    ob_tree = cKDTree(ob_v)
    _, nidx = ob_tree.query(ob_v, k=min(_SMOOTH_K, len(ob_v)))
    for _ in range(_SMOOTH_ITERS):
        deltas = deltas[nidx].mean(axis=1)
    target = ob_v + deltas

    # --- Per-bone weighted least-squares affine fit ---------------------------
    names, mats = [], []
    for bone, chunks in sorted(bone_accum.items()):
        idx_all = np.concatenate([c[0] for c in chunks])
        w_all = np.concatenate([c[1] for c in chunks])
        valid = (w_all > 1e-4) & (idx_all < len(ob_v))
        idx_all, w_all = idx_all[valid], w_all[valid]
        if len(idx_all) == 0:
            continue
        P = ob_v[idx_all]
        T = target[idx_all]
        n_eff = float(w_all.sum())

        if n_eff < _MIN_BONE_VERTS:
            # Not enough support for a stable fit — translation only
            t = np.average(T - P, axis=0, weights=w_all)
            A = np.vstack([np.eye(3), t])
        else:
            # Weighted similarity fit (Umeyama): rotation + uniform scale +
            # translation.  A free affine captures shear, and disagreeing
            # shears between adjacent bones (clavicle/upperarm) stretch armor
            # edges badly at weight boundaries; a similarity is shape-
            # preserving locally while still absorbing proportion differences.
            wn = w_all / n_eff
            c_p = wn @ P
            c_t = wn @ T
            P0 = P - c_p
            T0 = T - c_t
            C = (P0 * wn[:, None]).T @ T0            # covariance (3,3)
            U, S, Vt = np.linalg.svd(C)
            d = np.sign(np.linalg.det(U @ Vt))
            D = np.diag([1.0, 1.0, d])
            # Row-vector convention: moved = v @ R + t
            R = U @ D @ Vt
            var_p = float(wn @ (P0 ** 2).sum(axis=1))
            s = float((S * np.diag(D)).sum() / max(var_p, 1e-8))
            s = float(np.clip(s, 0.85, 1.2))
            R = R * s
            t = c_t - c_p @ R
            A = np.vstack([R, t])
        names.append(bone)
        mats.append(A)

    if not names:
        return False

    _GEN_DIR.mkdir(parents=True, exist_ok=True)
    out = _GEN_DIR / f'body_fit_{gender}.npz'
    np.savez_compressed(out,
                        bone_names=np.array(names),
                        matrices=np.array(mats, dtype=np.float32))

    if verbose:
        # Report fit residual over the body verts using the affine field itself
        corrected = _apply_affines_to_points(ob_v, bone_accum, dict(zip(names, mats)))
        mag = np.linalg.norm(target - corrected, axis=1)
        raw = np.linalg.norm(deltas, axis=1)
        print(f'  [{gender}] {len(names)} bones, {len(ob_v)} body verts -> {out.name}')
        print(f'    FK error before: mean={raw.mean():.2f} p95={np.percentile(raw, 95):.2f}; '
              f'after affine fit: mean={mag.mean():.2f} p95={np.percentile(mag, 95):.2f}')
    return True


def _apply_affines_to_points(points, bone_accum, affines):
    """Apply the per-bone affine blend to the build point cloud (for reporting)."""
    out = points.copy()
    acc = np.zeros_like(points)
    wsum = np.zeros(len(points))
    for bone, chunks in bone_accum.items():
        A = affines.get(bone)
        if A is None:
            continue
        for idx, w in chunks:
            valid = (w > 1e-4) & (idx < len(points))
            i, ww = idx[valid], w[valid]
            moved = points[i] @ A[:3, :] + A[3, :]
            acc[i] += ww[:, None] * moved
            wsum[i] += ww
    has = wsum > 1e-6
    out[has] = acc[has] / wsum[has][:, None] * np.minimum(wsum[has], 1.0)[:, None] \
        + points[has] * np.maximum(1.0 - wsum[has], 0.0)[:, None]
    return out


def build_all_fields(verbose: bool = True) -> int:
    """Build both gender fields.  Returns the number built."""
    n = 0
    for gender in ('male', 'female'):
        try:
            if build_field(gender, verbose=verbose):
                n += 1
        except Exception as e:
            if verbose:
                print(f'  [{gender}] field build failed: {e}')
    return n


# ---------------------------------------------------------------------------
# Field application
# ---------------------------------------------------------------------------

def _load_field(gender: str):
    """Load (and cache) a gender's field as {bone_name: (4,3) affine} or None."""
    if gender in _FIELD_CACHE:
        return _FIELD_CACHE[gender]
    result = None
    path = _GEN_DIR / f'body_fit_{gender}.npz'
    if path.exists():
        try:
            with np.load(path) as z:
                names = [str(n) for n in z['bone_names']]
                mats = z['matrices'].astype(np.float64)
            result = dict(zip(names, mats))
        except Exception:
            result = None
    _FIELD_CACHE[gender] = result
    return result


def apply_body_fit(data, src_path: str = '') -> int:
    """Correct all skinned geometry by the per-bone affine body-fit field.

    Call immediately AFTER retarget_skin_to_skyrim() and BEFORE bone renaming —
    the field is keyed by Oblivion bone names in post-FK space.  Corrections
    blend through each vertex's own skin weights:

        v' = Σ_b w_vb (v @ R_b + t_b) + (1 - Σ_b w_vb) v

    Bind matrices are recomputed for each corrected geometry.  Returns the
    number of geometries corrected (0 when the field file is absent — legacy
    piece offsets should then be used instead).
    """
    if not _PYFFI:
        return 0
    female = '/f/' in src_path.replace('\\', '/').lower()
    field = _load_field('female' if female else 'male')
    if field is None:
        return 0

    from .skin_retarget import _manual_update_bind_position

    count = 0
    for block, skel_root in _iter_skinned_geoms(data):
        verts, G = _geom_verts_world(block, skel_root)
        G_identity = np.allclose(G, np.eye(4), atol=1e-6)
        nv = len(verts)

        acc = np.zeros_like(verts)
        wsum = np.zeros(nv)
        matched = False
        for bone, (idx, w) in _geom_bone_weights(block).items():
            A = field.get(bone)
            if A is None:
                continue
            matched = True
            valid = (w > 1e-6) & (idx < nv)
            i, ww = idx[valid], w[valid]
            moved = verts[i] @ A[:3, :] + A[3, :]
            acc[i] += ww[:, None] * moved
            wsum[i] += ww
        if not matched:
            continue

        # Blend: covered weight gets the affine result, the remainder stays put
        # (bones without field data / unnormalized weights degrade gracefully).
        has = wsum > 1e-6
        norm = np.minimum(wsum[has], 1.0)[:, None]
        vw = verts.copy()
        vw[has] = acc[has] / wsum[has][:, None] * norm + verts[has] * (1.0 - norm)

        # Weld UV-seam twins: coincident source vertices must receive the SAME
        # correction (twins can carry marginally different stored weights, which
        # would split the seam).  Group by rounded original position, average.
        key = np.round(verts, 3)
        _, inv = np.unique(key, axis=0, return_inverse=True)
        counts = np.bincount(inv).astype(np.float64)
        sums = np.zeros((len(counts), 3))
        np.add.at(sums, inv, vw)
        vw = sums[inv] / counts[inv][:, None]

        new_verts = vw if G_identity else (vw - G[3, :3]) @ np.linalg.inv(G[:3, :3])
        geom_data = block.data
        for vi in range(nv):
            geom_data.vertices[vi].x = float(new_verts[vi, 0])
            geom_data.vertices[vi].y = float(new_verts[vi, 1])
            geom_data.vertices[vi].z = float(new_verts[vi, 2])

        # Keep binds + bone bounding spheres consistent with moved vertices
        _manual_update_bind_position(block, block.skin_instance, skel_root)
        count += 1
    return count


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Build body-fit per-bone affine fields')
    parser.add_argument('--gender', choices=['male', 'female'],
                        help='Build a single gender (default: both)')
    args = parser.parse_args()
    if args.gender:
        ok = build_field(args.gender)
        print('OK' if ok else 'FAILED')
    else:
        n = build_all_fields()
        print(f'{n}/2 fields built')


if __name__ == '__main__':
    main()
