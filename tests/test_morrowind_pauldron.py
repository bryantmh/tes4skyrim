"""Morrowind pauldrons are pushed out clear of the Skyrim skin and skinned like it: asset_convert.character.morrowind_pauldron."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from asset_convert.character import wearable_plan
from asset_convert.character.morrowind_armor import PART_ATTACH_NODES, rest_skeletons
from asset_convert.character.morrowind_pauldron import (PAULDRON_MARGIN, clearance, outward, push_group,
                                                        side_reference, skin_surface)
from asset_convert.character.morrowind_weights import skin_reference
from asset_convert.character.wrap_mesh import block_name, geom_world, iter_skinned_geoms
from asset_convert.nif.nif_converter import convert_nif
from asset_convert.nif.sse_nif import read_nif

MW_EXPORT = Path('export/Morrowind.esm')
PAULDRON = Path('armor/morrowind/m/0ironupauldronuright.nif')
BRACER = Path('armor/morrowind/m/0templarsbracersright.nif')
ARK_EXPORT = Path('export/Arktwend')
SKINNED_PAULDRON = Path('armor/morrowind/m/0darkbrotherhoodspauldronur.nif')


def _tube(radius: float, z0: float, z1: float, around: int, rings: int) -> tuple:
    """(points, outward-facing triangles) of an open tube about the z axis."""
    angle = np.linspace(0.0, 2 * np.pi, around, endpoint=False)
    points = np.array([[radius * np.cos(a), radius * np.sin(a), z] for z in np.linspace(z0, z1, rings)
                       for a in angle])
    tris = []
    for r in range(rings - 1):
        for i in range(around):
            a, b = r * around + i, r * around + (i + 1) % around
            tris += [[a, b, b + around], [a, b + around, a + around]]
    return points, np.array(tris)


def test_a_sunk_sleeve_is_pushed_clear_and_stays_round():
    """A round sleeve 1 unit inside a round arm ends clear of it and still round: pushed, not form-fitted."""
    arm, arm_tris = _tube(5.0, -10.0, 30.0, 24, 9)
    sleeve, sleeve_tris = _tube(4.0, 0.0, 20.0, 16, 5)
    skin = skin_surface(arm[arm_tris])
    line = (np.zeros(3), np.array([0.0, 0.0, 1.0]))
    group = SimpleNamespace(points=sleeve, faces=sleeve_tris, line=line, surface=skin, hidden=None)
    none = (np.zeros((0, 3)), np.zeros(0))
    out = sleeve + outward(sleeve, *line) * push_group(group, np.zeros(len(sleeve_tris), bool), none, none)[:, None]
    assert clearance(skin, out).min() >= PAULDRON_MARGIN - 0.05
    radius = np.linalg.norm(out[:, :2], axis=1)
    assert radius.std() / radius.mean() < 0.02
    assert np.allclose(out[:, 2], sleeve[:, 2])


@pytest.mark.skipif(not (MW_EXPORT / 'meshes' / 'base_anim.nif').exists(), reason='needs the Morrowind.esm export')
def test_a_shoulder_part_hangs_from_the_shoulder_joint_unturned():
    """A clavicle-slot part keeps Morrowind's turn; its shoulder joint lands on Skyrim's."""
    skel = rest_skeletons([MW_EXPORT / 'meshes'])[False]
    bone = skel.attach[PART_ATTACH_NODES[23]][0]
    child = skel.child[bone]
    mw_shoulder = skel.world[child][3, :3] - skel.world[bone][3, :3]
    placed, turn = skel.place(23, bone, mw_shoulder[None])
    assert np.allclose(turn, np.eye(3))
    assert np.allclose(placed[0] + skel.pivot(bone), skel.pivot(child))


def _convert(tmp_path, rel: Path, root: Path = MW_EXPORT, records: Path = MW_EXPORT) -> list:
    """[(shape name, skeleton-space vertices, bone names)] of `rel` under `root`, converted with the plan of `records`."""
    plan = wearable_plan.build_plan(records)
    dst = tmp_path / rel.name
    convert_nif(str(root / 'meshes' / rel), str(dst), src_meshes_dir=str(root / 'meshes'), wearable_plan=plan)
    written = sorted(tmp_path.glob(f'{rel.stem}*.nif'))
    return [(block_name(b), geom_world(b, root)[0], [block_name(n) for n in b.skin_instance.bones])
            for b, root in iter_skinned_geoms(read_nif(str(written[0])))]


def _inside(points) -> float:
    """Fraction of `points` inside the Skyrim male right arm."""
    return float(np.mean(clearance(skin_surface(side_reference(skin_reference(False, 0, keep=bool), 'R')[0]),
                                   points) < 0.0))


@pytest.mark.skipif(not (MW_EXPORT / 'meshes' / PAULDRON).exists(), reason='needs the Morrowind.esm export')
def test_pauldron_pieces_clear_the_skin_and_follow_the_shoulder(tmp_path):
    """The iron pauldron's outer sleeve is mostly outside the arm, and every piece moves with the arm."""
    shapes = _convert(tmp_path, PAULDRON)
    sleeve = next(pts for name, pts, _b in shapes if name.endswith('UA 0'))
    assert _inside(sleeve) < 0.15
    bones = {n for _s, _p, names in shapes for n in names}
    assert {'NPC R Clavicle [RClv]', 'NPC R UpperArm [RUar]', 'NPC R Forearm [RLar]'} <= bones
    assert all(len(names) > 1 for _s, _p, names in shapes)


@pytest.mark.skipif(not (ARK_EXPORT / 'meshes' / SKINNED_PAULDRON).exists(), reason='needs the Arktwend export')
def test_a_skinned_pauldron_part_is_pushed_out_too(tmp_path):
    """The Dark Brotherhood pauldron's skinned upper-arm part, 3.4 units deep in the arm unfitted, is mostly outside."""
    shapes = _convert(tmp_path, SKINNED_PAULDRON, ARK_EXPORT, ARK_EXPORT / 'Arktwend_English.esm')
    assert _inside(np.vstack([pts for _n, pts, _b in shapes])) < 0.2


@pytest.mark.skipif(not (MW_EXPORT / 'meshes' / BRACER).exists(), reason='needs the Morrowind.esm export')
def test_a_bracer_keeps_its_rigid_pieces(tmp_path):
    """Only a pauldron record is fitted: a bracer's rigid pieces stay on their one bone."""
    own = {block_name(b) for b, _r in iter_skinned_geoms(read_nif(str(MW_EXPORT / 'meshes' / BRACER)))}
    shapes = [s for s in _convert(tmp_path, BRACER) if s[0] in own]
    assert shapes and all(len(names) == 1 for _s, _p, names in shapes)
