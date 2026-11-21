"""Morrowind sleeves sit on Skyrim's arm segments: tests for asset_convert.character.morrowind_arm_fit."""

from pathlib import Path

import numpy as np
import pytest
from scipy.spatial import cKDTree

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from asset_convert.character import wearable_plan
from asset_convert.character.morrowind_arm_fit import (ARM_SEGMENTS, SEAM_SIGMA, rotation_between,
                                                       stretch_along)
from asset_convert.character.skin_retarget import (SKEL_MORROWIND, SKEL_SKYRIM_MALE, load_animation_deltas,
                                                   load_skeleton)
from asset_convert.character.wrap_mesh import block_name, geom_world, iter_skinned_geoms
from asset_convert.nif.nif_converter import convert_nif
from asset_convert.nif.sse_nif import read_nif

EXPORT = Path('export/Arktwend')
ROBE = Path('armor/morrowind/m/0commonurobeu02.nif')
SLEEVE = 'Tri chest 2'
MW_EXPORT = Path('export/Morrowind.esm')
SEAM_ROBE = Path('armor/morrowind/m/0commonurobeu05.nif')


def _plan_wearing(tmp_path, plugin_dir: Path, editor_id: str, model: Path) -> dict:
    """The wearable plan of `editor_id`'s CLOT record alone, wearing the assembled `model`.

    Hybrid mode may swap the robe's worn model for Morroblivion's; these tests
    fit the assembled mesh, so the record is replayed with that model.
    """
    marker = '---RECORD_BEGIN---'
    body = (plugin_dir / 'CLOT.txt').read_text(encoding='utf-8', errors='replace')
    rec = next(r for r in body.split(marker) if f'\nEditorID={editor_id}\n' in r)
    lines = [ln for ln in rec.splitlines() if not ln.startswith(('Male.BipedModel', 'Female.BipedModel'))]
    lines.insert(1, 'Male.BipedModel.MODL=' + str(model).replace('/', chr(92)))
    (tmp_path / 'CLOT.txt').write_text(marker + '\n'.join(lines) + '\n', encoding='utf-8')
    return wearable_plan.build_plan(tmp_path)


def _sleeve(data):
    """(skeleton-space vertices, shape block) of the robe's sleeve shape."""
    block, root = next((b, r) for b, r in iter_skinned_geoms(data) if block_name(b) == SLEEVE)
    return geom_world(block, root)[0], block


def _line_distance(points, a, b) -> np.ndarray:
    """Perpendicular distance of each point from the infinite line through `a` and `b`."""
    axis = (b - a) / np.linalg.norm(b - a)
    rel = points - a
    return np.linalg.norm(rel - np.outer(rel @ axis, axis), axis=1)


def _weights(block) -> tuple:
    """(bone names, per-vertex weight matrix) of a skinned shape."""
    names = [block_name(b) for b in block.skin_instance.bones]
    w = np.zeros((block.data.num_vertices, len(names)))
    for bi, bd in enumerate(block.skin_instance.data.bone_list):
        for vw in bd.vertex_weights:
            w[vw.index, bi] = vw.weight
    return names, w


def _off_arm_points(data) -> np.ndarray:
    """Skeleton-space vertices of every shape that carry any weight off the arm bones."""
    out = []
    for block, root in iter_skinned_geoms(data):
        names, w = _weights(block)
        arm = np.array([n in ARM_SEGMENTS for n in names])
        out.append(geom_world(block, root)[0][w[:, ~arm].sum(axis=1) > 1e-6])
    return np.vstack(out)


def test_rotation_between_turns_one_direction_onto_another():
    """The row-vector rotation carries `here` onto `there`."""
    here, there = np.array([1.0, 0.0, 0.0]), np.array([0.0, 0.6, -0.8])
    assert np.allclose(here @ rotation_between(here, there), there)


def test_stretch_along_scales_inside_the_bone_and_shifts_past_it():
    """Inside the bone a point scales; past the child joint it keeps its offset."""
    pts = np.array([[5.0, 2.0, 0.0], [12.0, 0.0, 1.0], [-1.0, 3.0, 0.0]])
    out = stretch_along(pts, np.array([1.0, 0.0, 0.0]), 10.0, 20.0)
    assert np.allclose(out, [[10.0, 2.0, 0.0], [22.0, 0.0, 1.0], [-1.0, 3.0, 0.0]])


@pytest.mark.skipif(not (EXPORT / 'meshes' / ROBE).exists(), reason='needs the Arktwend export')
def test_robe_sleeve_keeps_its_radius_around_the_skyrim_upper_arm(tmp_path):
    """Away from the shoulder seam, a pure upper-arm sleeve vertex keeps its distance from the arm bone."""
    src = EXPORT / 'meshes' / ROBE
    source = read_nif(str(src))
    bind, block = _sleeve(source)
    names, w = _weights(block)
    pure = np.flatnonzero(w[:, names.index('Bip01 R UpperArm')] > 0.999)
    keep = pure[cKDTree(_off_arm_points(source)).query(bind[pure])[0] > 3.0 * SEAM_SIGMA]
    plan = _plan_wearing(tmp_path, EXPORT / 'Arktwend_English.esm', 'common_robe_02', ROBE)
    convert_nif(str(src), str(tmp_path / ROBE.name), src_meshes_dir=str(EXPORT / 'meshes'), wearable_plan=plan)
    out = _sleeve(read_nif(str(tmp_path / f'{ROBE.stem}_0.nif')))[0]
    assert len(keep) >= 10
    mw, sk = load_skeleton(SKEL_MORROWIND[False]), load_skeleton(SKEL_SKYRIM_MALE)
    deltas = load_animation_deltas(mw)
    posed = [(mw[b] @ deltas[b])[3, :3] for b in ('Bip01 R UpperArm', 'Bip01 R Forearm')]
    delta = deltas['Bip01 R UpperArm']
    before = _line_distance(bind[keep] @ delta[:3, :3] + delta[3, :3], *posed)
    after = _line_distance(out[keep], sk['NPC R UpperArm [RUar]'][3, :3], sk['NPC R Forearm [RLar]'][3, :3])
    assert np.abs(after - before).max() < 0.25


def _shape_points(data) -> list:
    """Skeleton-space vertices of every skinned shape, in file order."""
    return [geom_world(block, root)[0] for block, root in iter_skinned_geoms(data)]


@pytest.mark.skipif(not (MW_EXPORT / 'meshes' / SEAM_ROBE).exists(), reason='needs the Morrowind.esm export')
def test_seam_welded_across_shapes_stays_closed(tmp_path):
    """The robe's sleeve shape and its torso shape share their shoulder vertices; the fit keeps them together."""
    src = MW_EXPORT / 'meshes' / SEAM_ROBE
    source = _shape_points(read_nif(str(src)))
    plan = _plan_wearing(tmp_path, MW_EXPORT, 'common_robe_05', SEAM_ROBE)
    convert_nif(str(src), str(tmp_path / SEAM_ROBE.name), src_meshes_dir=str(MW_EXPORT / 'meshes'),
                wearable_plan=plan)
    out = _shape_points(read_nif(str(tmp_path / f'{SEAM_ROBE.stem}_0.nif')))
    gaps = []
    for i, a in enumerate(source):
        for j in range(i + 1, len(source)):
            d, k = cKDTree(source[j]).query(a, distance_upper_bound=0.01)
            hit = np.flatnonzero(np.isfinite(d))
            gaps += np.linalg.norm(out[i][hit] - out[j][k[hit]], axis=1).tolist()
    assert len(gaps) >= 100
    assert max(gaps) < 0.01
