"""
Offline build of the body-wrap correction fields.

    python -m asset_convert.character.body_wrap_build [--gender G] [--source S]

A field fits a source game's reference body -- FK-posed through the very
retarget its armor gets -- exactly onto the Skyrim body (both weight-slider
targets) and saves what body_wrap interpolates at runtime: src (rest pose),
fkp (posed), dst0/dst1 (fitted), tris, vert_bc (skin-weight bone centroids in
Oblivion skeleton space) and part (0 body, 1 hands/feet, 2 head). Oblivion's
reference body is its body part meshes plus the imperial head; Morrowind's is
the reference race's skin parts (morrowind_body).

See: docs/commentary/asset_convert_armor.md#body-wrap-armor-fitting
See: docs/commentary/asset_convert_armor.md#morrowind-wrap-field
"""

import argparse
import traceback
from functools import partial
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from asset_convert import paths
from asset_convert.character import head_fit
from asset_convert.character.body_wrap import field_path
from asset_convert.character.morrowind_body import body_groups
from asset_convert.character.skin_retarget import (SKEL_MORROWIND, SKEL_OBLIVION,
                                                   SKEL_SKYRIM_FEMALE, SKEL_SKYRIM_MALE,
                                                   load_skeleton, retarget_skin_to_skyrim)
from asset_convert.character.skyrim_overrides import OBLIVION_TO_SKYRIM_BONE_MAP
from asset_convert.character.wrap_mesh import (build_adjacency, closest_point_on_triangles,
                                               geom_bone_weights, geom_triangles, geom_world,
                                               group_mean, iter_skinned_geoms, read_nif,
                                               smooth_group_field, vertex_normals,
                                               weld_groups)
from asset_convert.sources.skyrim_assets import get_body_nif_bytes
from asset_convert.character.morrowind_body import part_slot_labels

#: morrowind -> the reference body's per-vertex part labeler; Oblivion's body carries none.
_LABELERS = {True: part_slot_labels, False: None}

#: Oblivion's exported male body part folder.
_OB_BODY_DIR = paths.EXPORT / 'Oblivion.esm' / 'meshes' / 'characters' / '_male'

#: characters\ root; the head group is filed there, shared by both genders.
_OB_CHAR_DIR = _OB_BODY_DIR.parent

#: Oblivion's head group: the head and its separate ears mesh.
_OB_HEAD_PARTS = (Path('imperial') / 'headhuman.nif', Path('imperial') / 'earshuman.nif')

#: gender -> {wrap group: Oblivion body part NIFs}, grouped by Skyrim target surface.
_OB_BODY_SETS = {
    'male': {'body': ['upperbody.nif', 'lowerbody.nif'], 'hands': ['hand.nif'],
             'feet': ['foot.nif'], 'head': list(_OB_HEAD_PARTS)},
    'female': {'body': ['femaleupperbody.nif', 'femalelowerbody.nif'],
               'hands': ['femalehand.nif'], 'feet': ['femalefoot.nif'],
               'head': list(_OB_HEAD_PARTS)},
}

#: gender -> the path tag retarget_skin_to_skyrim reads the gender from.
_GENDER_TAGS = {'male': 'armor/m/', 'female': 'armor/f/'}

#: gender -> {wrap group: Skyrim target body NIF stem}; each ships _0 and _1 weights.
_SK_BODY_SETS = {
    'male': {'body': 'malebody', 'hands': 'malehands', 'feet': 'malefeet'},
    'female': {'body': 'femalebody', 'hands': 'femalehands', 'feet': 'femalefeet'},
}

#: gender -> the one Skyrim head, which has no weight variants.
SK_HEAD_SETS = {'male': 'malehead.nif', 'female': 'femalehead.nif'}

#: ICP fit phases: (iterations, smoothing passes, step).
_FIT_PHASES = ((30, 8, 0.5), (20, 2, 0.7))

#: Candidate triangles per projection query.
_PROJ_K = 8

#: Target triangles facing further away than this are rejected in projection.
_NORMAL_DOT_MIN = 0.1

#: (parent, child, riders): long-bone segments rescaled to Skyrim length before fitting.
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

#: Hand/foot verts this close to the body surface count as body (the wrist/ankle seam).
_SEAM_BODY_DIST = 3.0

#: Part ids: body, hands/feet, head.
_PART_BODY, _PART_LIMB, _PART_HEAD = 0, 1, 2

#: npz arrays concatenated over the groups, in the order they are saved.
_STACKED = ('src', 'fkp', 'dst0', 'dst1', 'tris', 'vert_bc')


# ---------------------------------------------------------------------------
# Surfaces
# ---------------------------------------------------------------------------

def _surface(data):
    """(verts, tris) over every skinned shape of `data`, or None."""
    v_parts, t_parts, offset = [], [], 0
    for block, skel_root in iter_skinned_geoms(data):
        verts, _G = geom_world(block, skel_root)
        v_parts.append(verts)
        t_parts.append(geom_triangles(block) + offset)
        offset += len(verts)
    return (np.vstack(v_parts), np.vstack(t_parts)) if v_parts else None


def load_sk_surface(gender: str, group: str, weight: int):
    """Skyrim target surface for a group and weight: (verts, tris), or None."""
    name = (SK_HEAD_SETS[gender] if group == 'head'
            else f'{_SK_BODY_SETS[gender][group]}_{weight}.nif')
    raw = get_body_nif_bytes(name)
    return None if raw is None else _surface(read_nif(raw))


def head_uv_geometry(source):
    """(verts_world, tris, uvs) for a head NIF given a path or bytes, or None."""
    v_parts, t_parts, u_parts, offset = [], [], [], 0
    for block, skel_root in iter_skinned_geoms(read_nif(source)):
        uv = block.data.uv_sets[0] if len(block.data.uv_sets) else None
        if uv is None:
            continue
        verts, _G = geom_world(block, skel_root)
        v_parts.append(verts)
        t_parts.append(geom_triangles(block) + offset)
        u_parts.append(np.array([[p.u, p.v] for p in uv], dtype=np.float64))
        offset += len(verts)
    if not v_parts:
        return None
    return np.vstack(v_parts), np.vstack(t_parts), np.vstack(u_parts)


def _uv_sample(q_uv, uv, tris, verts, k=24):
    """Sample `verts` at uv coordinates `q_uv` over the (uv, tris) uv mesh.

    Barycentric inside a uv triangle where one contains the query, else the
    triangle whose barycentrics are least negative, so vertices on a uv island
    boundary still map continuously.
    """
    k = min(k, len(tris))
    _, cand = cKDTree(uv[tris].mean(axis=1)).query(q_uv, k=k)
    if k == 1:
        cand = cand[:, None]
    a, b, c = uv[tris[cand, 0]], uv[tris[cand, 1]], uv[tris[cand, 2]]
    v0, v1, v2 = b - a, c - a, q_uv[:, None, :] - a
    d00, d01, d11 = (v0 * v0).sum(axis=2), (v0 * v1).sum(axis=2), (v1 * v1).sum(axis=2)
    d20, d21 = (v2 * v0).sum(axis=2), (v2 * v1).sum(axis=2)
    den = d00 * d11 - d01 * d01
    den = np.where(np.abs(den) < 1e-16, 1.0, den)
    bv = (d11 * d20 - d01 * d21) / den
    bw = (d00 * d21 - d01 * d20) / den
    bu = 1.0 - bv - bw
    inside = (bu >= -1e-6) & (bv >= -1e-6) & (bw >= -1e-6)
    pen = np.minimum(np.minimum(bu, bv), bw)
    best = np.argmax(np.where(inside, 1e6, pen), axis=1)
    r = np.arange(len(q_uv))
    bu_, bv_, bw_ = bu[r, best], bv[r, best], bw[r, best]
    tot = bu_ + bv_ + bw_
    tot = np.where(np.abs(tot) < 1e-12, 1.0, tot)
    bu_, bv_, bw_ = bu_ / tot, bv_ / tot, bw_ / tot
    t = tris[cand[r, best]]
    return (bu_[:, None] * verts[t[:, 0]] + bv_[:, None] * verts[t[:, 1]]
            + bw_[:, None] * verts[t[:, 2]])


def _uv_head_seed(gender: str, v0):
    """UV-correspondence positions for the Oblivion head verts as the fit's seed, or None.

    See: docs/commentary/asset_convert_armor.md#body-wrap-armor-fitting
    """
    try:
        ob_path = _OB_CHAR_DIR / _OB_HEAD_PARTS[0]
        raw = get_body_nif_bytes(SK_HEAD_SETS[gender])
        ob = head_uv_geometry(ob_path) if ob_path.exists() else None
        sk = head_uv_geometry(raw) if raw is not None else None
        if ob is None or sk is None or len(ob[0]) != len(v0):
            return None
        return _uv_sample(ob[2], sk[2], sk[1], sk[0])
    except Exception as e:
        print(f'  [{gender}/head] UV seed unavailable: {e}')
        return None


# ---------------------------------------------------------------------------
# Reference bodies
# ---------------------------------------------------------------------------

def _oblivion_groups(gender: str) -> dict:
    """{group: [(name, factory of a fresh Data)]} over the Oblivion body parts on disk."""
    out = {}
    for group, names in _OB_BODY_SETS[gender].items():
        items = []
        for name in names:
            path = (_OB_CHAR_DIR if group == 'head' else _OB_BODY_DIR) / name
            if path.exists():
                items.append((str(name), partial(read_nif, path)))
            else:
                print(f'  [{gender}] missing OB body mesh: {path}')
        out[group] = items
    return out


def _group_arrays(items, labeler=None):
    """{'v0', 'tris', 'bones'[, 'labels']} over every skinned shape of a group, or None.

    `labeler(block, verts)` gives each vertex its source body-part label.
    """
    v_parts, t_parts, l_parts, bone_acc, offset = [], [], [], {}, 0
    for _name, factory in items:
        for block, skel_root in iter_skinned_geoms(factory()):
            verts, _G = geom_world(block, skel_root)
            v_parts.append(verts)
            t_parts.append(geom_triangles(block) + offset)
            if labeler is not None:
                l_parts.append(labeler(block, verts))
            for bone, (idx, w) in geom_bone_weights(block).items():
                bone_acc.setdefault(bone, []).append((idx + offset, w))
            offset += len(verts)
    if not v_parts:
        return None
    arrays = {'v0': np.vstack(v_parts), 'tris': np.vstack(t_parts),
              'bones': {b: (np.concatenate([c[0] for c in ch]),
                            np.concatenate([c[1] for c in ch]))
                        for b, ch in bone_acc.items()}}
    if l_parts:
        arrays['labels'] = np.concatenate(l_parts)
    return arrays


def _posed_verts(items, gender: str, morrowind: bool):
    """A group's vertices after the FK retarget its armor gets (no wrap), or None."""
    v_parts = []
    for name, factory in items:
        data = factory()
        retarget_skin_to_skyrim(data, src_path=_GENDER_TAGS[gender] + Path(name).name,
                                allow_wrap=False, morrowind=morrowind)
        v_parts.extend(geom_world(b, r)[0] for b, r in iter_skinned_geoms(data))
    return np.vstack(v_parts) if v_parts else None


def load_ob_group(gender: str) -> dict:
    """{group: {'v0', 'tris', 'bones'}} of the Oblivion reference body in T-pose."""
    groups = {g: _group_arrays(items) for g, items in _oblivion_groups(gender).items()}
    return {g: arrays for g, arrays in groups.items() if arrays is not None}


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------

def _segment_axis(parent, child, src_skel, sk_skel):
    """(Skyrim segment head, unit axis, Skyrim/source length ratio), or None."""
    sk_p, sk_c = OBLIVION_TO_SKYRIM_BONE_MAP.get(parent), OBLIVION_TO_SKYRIM_BONE_MAP.get(child)
    if (parent not in src_skel or child not in src_skel
            or sk_p not in sk_skel or sk_c not in sk_skel):
        return None
    src_len = np.linalg.norm(src_skel[child][3, :3] - src_skel[parent][3, :3])
    sk_head = sk_skel[sk_p][3, :3]
    sk_vec = sk_skel[sk_c][3, :3] - sk_head
    sk_len = np.linalg.norm(sk_vec)
    if src_len < 1e-3 or sk_len < 1e-3:
        return None
    return sk_head, sk_vec / sk_len, sk_len / src_len


def _segment_scale(verts, bones, src_skel, sk_skel):
    """FK-posed `verts` with limb segments stretched to Skyrim length, blended by skin weight.

    Fit initialisation only; armor is never axis-scaled.
    """
    acc, wsum = np.zeros_like(verts), np.zeros(len(verts))
    for parent, child, riders in _SCALE_SEGMENTS:
        seg = _segment_axis(parent, child, src_skel, sk_skel)
        weighted = [bones[bn] for bn in (parent,) + riders if bn in bones]
        if seg is None or not weighted:
            continue
        sk_head, axis, s = seg
        idx = np.concatenate([bi for bi, _bw in weighted])
        w = np.concatenate([bw for _bi, bw in weighted])
        along = (verts[idx] - sk_head) @ axis
        np.add.at(acc, idx, w[:, None] * (verts[idx] + np.outer(along * (s - 1.0), axis)))
        np.add.at(wsum, idx, w)
    has = wsum > 1e-6
    out = verts.copy()
    frac = np.minimum(wsum[has], 1.0)[:, None]
    out[has] = (acc[has] / wsum[has][:, None]) * frac + verts[has] * (1.0 - frac)
    return out


def _project_points(points, normals, target):
    """Closest point on the Skyrim surface per point, normal-agreement filtered."""
    sk_verts, sk_tris, sk_tri_n, sk_tree = target
    k = min(_PROJ_K, len(sk_tris))
    _, cand = sk_tree.query(points, k=k)
    if k == 1:
        cand = cand[:, None]
    cp = closest_point_on_triangles(points[:, None, :], sk_verts[sk_tris[cand, 0]],
                                    sk_verts[sk_tris[cand, 1]], sk_verts[sk_tris[cand, 2]])
    d = np.linalg.norm(cp - points[:, None, :], axis=2)
    agree = np.einsum('pki,pi->pk', sk_tri_n[cand], normals) > _NORMAL_DOT_MIN
    d_f = np.where(agree, d, np.inf)
    no_valid = ~np.isfinite(d_f).any(axis=1)
    if no_valid.any():
        d_f[no_valid] = d[no_valid]
    return cp[np.arange(len(points)), np.argmin(d_f, axis=1)]


def _sk_target(gender, group, weight):
    """(verts, tris, triangle normals, centroid KD-tree) of one Skyrim target, or None."""
    sk = load_sk_surface(gender, group, weight)
    if sk is None:
        return None
    sk_v, sk_t = sk
    sk_cent = sk_v[sk_t].mean(axis=1)
    n = np.cross(sk_v[sk_t[:, 1]] - sk_v[sk_t[:, 0]], sk_v[sk_t[:, 2]] - sk_v[sk_t[:, 0]])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    return sk_v, sk_t, n, cKDTree(sk_cent)


def _icp(cur, tris, graph, target):
    """`cur` walked onto the target through _FIT_PHASES of graph-smoothed steps."""
    wg, n_g, nbr_idx, nbr_ptr = graph
    for iters, smooth_n, step in _FIT_PHASES:
        for _ in range(iters):
            tgt = _project_points(cur, vertex_normals(cur, tris, wg, n_g), target)
            delta_g = smooth_group_field(group_mean(tgt - cur, wg, n_g),
                                         nbr_idx, nbr_ptr, smooth_n)
            cur = cur + step * delta_g[wg]
    return cur


def _report(label, cur, fk_raw, tris, graph, target) -> None:
    """Print how exactly the fit sits on the target and how far it moved from FK."""
    wg, n_g = graph[:2]
    proj = _project_points(cur, vertex_normals(cur, tris, wg, n_g), target)
    res = np.linalg.norm(proj - cur, axis=1)
    corr = np.linalg.norm(cur - fk_raw, axis=1)
    print(f'  [{label}] {len(cur)} verts: surface residual mean={res.mean():.3f} '
          f'p95={np.percentile(res, 95):.3f}; FK correction mean={corr.mean():.2f} '
          f'p95={np.percentile(corr, 95):.2f} max={corr.max():.2f}')


def _fit_group(gender, group, gd, fk_raw, skels, verbose):
    """([dst0, dst1], the _0 Skyrim surface) for one group, or None without a target.

    The head starts from the UV correspondence instead of the FK pose.
    See: docs/commentary/asset_convert_armor.md#body-wrap-armor-fitting
    """
    seed = _segment_scale(fk_raw.copy(), gd['bones'], *skels)
    if group == 'head':
        uv_seed = _uv_head_seed(gender, gd['v0'])
        seed = uv_seed if uv_seed is not None else seed
    wg = weld_groups(gd['v0'])
    n_g = int(wg.max()) + 1
    graph = (wg, n_g) + tuple(build_adjacency(gd['tris'], wg, n_g))
    fitted, surface = [], None
    for wt in (0, 1):
        target = _sk_target(gender, group, wt)
        if target is None:
            print(f'  [{gender}/{group}] missing SK target surface _{wt}')
            return None
        cur = _icp(seed.copy(), gd['tris'], graph, target)
        if verbose:
            _report(f'{gender}/{group}/_{wt}', cur, fk_raw, gd['tris'], graph, target)
        fitted.append(cur)
        surface = surface or target[:2]
    return fitted, surface


def _bone_centroids(gd, ob_skel):
    """Per vertex, the skin-weighted mean of its bones' Oblivion positions."""
    v0 = gd['v0']
    bc, bw_sum = np.zeros_like(v0), np.zeros(len(v0))
    for bone, (idx, w) in gd['bones'].items():
        if bone in ob_skel:
            np.add.at(bc, idx, np.outer(w, ob_skel[bone][3, :3]))
            np.add.at(bw_sum, idx, w)
    has = bw_sum > 1e-6
    bc[has] /= bw_sum[has][:, None]
    bc[~has] = v0[~has]
    return bc


def _part_ids(group, v0, body_v0):
    """Per vertex part id; hand/foot verts at the body seam count as body.

    See: docs/commentary/asset_convert_armor.md#body-wrap-armor-fitting
    """
    if group == 'head':
        return np.full(len(v0), _PART_HEAD, dtype=np.int32)
    if group == 'body':
        return np.full(len(v0), _PART_BODY, dtype=np.int32)
    part = np.full(len(v0), _PART_LIMB, dtype=np.int32)
    if body_v0 is not None:
        d_body, _ = cKDTree(body_v0).query(v0)
        part[d_body < _SEAM_BODY_DIST] = _PART_BODY
    return part


def _fit_all(gender, groups, posed, skels, verbose):
    """({npz array: [per group]}, head pack or None), or None when a fit fails."""
    src_skel, sk_skel, ob_skel = skels
    acc = {key: [] for key in _STACKED + ('part',)}
    body_v0 = groups['body']['v0'] if 'body' in groups else None
    head, offset = None, 0
    for group, gd in groups.items():
        if len(posed[group]) != len(gd['v0']):
            print(f'  [{gender}/{group}] vert count mismatch T-pose vs FK')
            return None
        fit = _fit_group(gender, group, gd, posed[group], (src_skel, sk_skel), verbose)
        if fit is None:
            return None
        (dst0, dst1), surface = fit
        if group == 'head':
            head = (gd['v0'], gd['tris'], surface, len(acc['dst0']))
        for key, value in zip(_STACKED + ('part',),
                              (gd['v0'], posed[group], dst0, dst1, gd['tris'] + offset,
                               _bone_centroids(gd, ob_skel),
                               _part_ids(group, gd['v0'], body_v0))):
            acc[key].append(value)
        if 'labels' in gd:
            acc.setdefault('mw_slot', []).append(gd['labels'])
        offset += len(gd['v0'])
    return acc, head


def _apply_head_fit(acc, head, gender, skels, verbose) -> dict:
    """head_fit's arrays, with the head rows of dst0/dst1 replaced by its field mapping.

    See: docs/commentary/asset_convert_armor.md#head-gear-fit
    """
    if head is None:
        return {}
    hv0, htris, surface, slot = head
    _src_skel, sk_skel, ob_skel = skels
    try:
        hf = head_fit.build_arrays(hv0, htris, surface, _OB_CHAR_DIR,
                                   o_ob=ob_skel['Bip01 Head'][3, :3],
                                   o_sk=sk_skel['NPC Head [Head]'][3, :3], gender=gender)
    except Exception as e:
        traceback.print_exc()
        print(f'  [{gender}] head-fit arrays failed: {e}')
        return {}
    if 'hf_dv' in hf:
        carrier = sk_skel['NPC Head [Head]'][3, :3] - ob_skel['Bip01 Head'][3, :3]
        head_dst = hv0 + carrier + hf['hf_dv'].astype(np.float64)[:len(hv0)]
        acc['dst0'][slot] = acc['dst1'][slot] = head_dst
        if verbose:
            corr = np.linalg.norm(head_dst - hv0, axis=1)
            print(f'  [{gender}/head] dst replaced by head-fit field: '
                  f'correction mean={corr.mean():.2f} max={corr.max():.2f}')
    return hf


def _save(out, acc, hf, has_head, verbose) -> None:
    """Write the field npz, arrays in the order the runtime has always read them."""
    out.parent.mkdir(parents=True, exist_ok=True)
    stacked = {key: np.vstack(acc[key]).astype(np.int32 if key == 'tris' else np.float32)
               for key in _STACKED}
    labels = ({'mw_slot': np.concatenate(acc['mw_slot']).astype(np.int16)}
              if 'mw_slot' in acc else {})
    np.savez_compressed(out, **stacked, part=np.concatenate(acc['part']),
                        has_head=np.array([1 if has_head else 0], dtype=np.int8), **hf,
                        **labels)
    if verbose:
        print(f'  saved {out.name}: {len(stacked["src"])} verts, {len(stacked["tris"])} tris')


def build_field(gender: str, source: str = 'oblivion', verbose: bool = True) -> bool:
    """Build and save the wrap field for one gender of one source game; success."""
    female = gender == 'female'
    ob_skel = load_skeleton(SKEL_OBLIVION)
    sk_skel = load_skeleton(SKEL_SKYRIM_FEMALE if female else SKEL_SKYRIM_MALE)
    morrowind = source == 'morrowind'
    src_skel = load_skeleton(SKEL_MORROWIND[female]) if morrowind else ob_skel
    if not ob_skel or not sk_skel or not src_skel:
        print(f'  [{gender}] skeleton JSONs missing — cannot build')
        return False
    sources = body_groups(gender) if morrowind else _oblivion_groups(gender)
    groups = {g: a for g, items in sources.items()
              if (a := _group_arrays(items, _LABELERS[morrowind])) is not None}
    posed = {g: p for g, items in sources.items()
             if (p := _posed_verts(items, gender, morrowind)) is not None}
    if not groups or set(groups) != set(posed):
        print(f'  [{gender}] {source} body meshes missing — cannot build')
        return False
    skels = (src_skel, sk_skel, ob_skel)
    fit = _fit_all(gender, groups, posed, skels, verbose)
    if fit is None:
        return False
    acc, head = fit
    hf = _apply_head_fit(acc, head, gender, skels, verbose)
    _save(field_path(female, morrowind), acc, hf, 'head' in groups, verbose)
    return True


def build_all_fields(source: str = 'oblivion', verbose: bool = True) -> int:
    """Build both genders' fields for one source; how many succeeded."""
    built = 0
    for gender in ('male', 'female'):
        try:
            built += build_field(gender, source, verbose=verbose)
        except Exception as e:
            traceback.print_exc()
            print(f'  [{gender}] wrap field build failed: {e}')
    return built


def main():
    """CLI: build one gender or both, for one source game."""
    parser = argparse.ArgumentParser(
        description='Build the body-wrap fields (a source body fitted onto the Skyrim body)')
    parser.add_argument('--gender', choices=['male', 'female'],
                        help='build a single gender (default: both)')
    parser.add_argument('--source', choices=['oblivion', 'morrowind'], default='oblivion',
                        help='the reference body to fit')
    args = parser.parse_args()
    if args.gender:
        print('OK' if build_field(args.gender, args.source) else 'FAILED')
    else:
        print(f'{build_all_fields(args.source)}/2 wrap fields built')


if __name__ == '__main__':
    main()
