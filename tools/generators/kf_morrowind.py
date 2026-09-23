"""Morrowind animation input for the pose-cache generator (kf_animation_explorer).

Morrowind keeps every animation group in one `.kf`: a NiSequenceStreamHelper
whose NiStringExtraData chain names, in order, the bone each NiKeyframeController
drives, every bone keyed at its own times. The pose search compares whole-body
frames, so each bone is resampled onto one shared grid at the authored key
spacing, giving every frame an exact key for every bone.

See: docs/commentary/asset_convert_armor.md#morrowind-pose-cache
"""

import json
from pathlib import Path

import numpy as np

from asset_convert.nif.sse_nif import read_nif
from asset_convert.sources.morrowind_assets import find_vanilla_mesh

#: Resampling step: the median rotation-key spacing of xbase_anim.kf (1/15 s).
FRAME_STEP = 1.0 / 15.0

#: Header every Morrowind-version NIF starts with.
_MORROWIND_HEADER = b'NetImmerse File Format, Version 4.0.0.2'

#: gender -> (rest skeleton, the .kf clips its actors play), as OpenMW loads them.
_SOURCES = {False: ('base_anim.nif', ('xbase_anim.kf',)),
            True: ('base_anim_female.nif', ('xbase_anim_female.kf', 'xbase_anim.kf'))}


def morrowind_sources(export_root, female: bool) -> tuple:
    """(skeleton NIF, [.kf paths]) of the vanilla human rig for one gender."""
    skeleton, clips = _SOURCES[female]
    found = [find_vanilla_mesh(export_root, name) for name in (skeleton,) + clips]
    if any(path is None for path in found):
        raise SystemExit('Morrowind rig not found -- register the Morrowind install')
    return Path(found[0]), [Path(p) for p in found[1:]]


def pose_to_bind(bones: dict, bind_json) -> None:
    """Re-pose a load_skeleton_hierarchy dict in place onto the bind skeleton JSON.

    The corpus keys base_anim's hierarchy, but skinned parts are authored in
    the T-posed bind skeleton, so every delta must start from it.
    See: docs/commentary/asset_convert_armor.md#morrowind-pose-cache
    """
    with open(bind_json) as fh:
        bind = {n: np.array(m, dtype=np.float64) for n, m in json.load(fh).items()}
    for name, bone in bones.items():
        parent = bones.get(bone['parent'])
        parent_world = parent['world'] if parent is not None else np.eye(4)
        if name in bind:
            bone['world'] = bind[name]
            bone['local'] = bind[name] @ np.linalg.inv(parent_world)
        else:
            bone['world'] = bone['local'] @ parent_world


def is_morrowind_kf(kf_path) -> bool:
    """Whether the file is a Morrowind-version NIF, read from its header alone."""
    with open(kf_path, 'rb') as fh:
        return fh.read(len(_MORROWIND_HEADER)) == _MORROWIND_HEADER


def _chain(first, attr: str) -> list:
    """Every block of a linked list starting at `first`, following `attr`."""
    out = []
    while first is not None:
        out.append(first)
        first = getattr(first, attr)
    return out


def _bone_controllers(root) -> list:
    """(bone name, NiKeyframeData) per controller, paired with the name chain."""
    names = [bytes(ed.string_data).rstrip(b'\x00').decode('latin-1')
             for ed in _chain(root.extra_data, 'next_extra_data')
             if hasattr(ed, 'string_data')]
    ctrls = _chain(root.controller, 'next_controller')
    return [(name, ctrl.data) for name, ctrl in zip(names, ctrls)
            if ctrl.data is not None]


def _sample_rotations(keys, grid):
    """(frames, 4) w,x,y,z quaternions linearly blended at `grid`; None without keys."""
    if not keys:
        return None
    times = np.array([k.time for k in keys])
    quats = np.array([[k.value.w, k.value.x, k.value.y, k.value.z] for k in keys])
    for i in range(1, len(quats)):
        if quats[i] @ quats[i - 1] < 0:
            quats[i] = -quats[i]
    out = np.stack([np.interp(grid, times, quats[:, c]) for c in range(4)], axis=1)
    return out / np.linalg.norm(out, axis=1, keepdims=True)


def _sample_translations(keys, grid):
    """(frames, 3) translations linearly blended at `grid`; None without keys."""
    if not keys:
        return None
    times = np.array([k.time for k in keys])
    values = np.array([[k.value.x, k.value.y, k.value.z] for k in keys])
    return np.stack([np.interp(grid, times, values[:, c]) for c in range(3)], axis=1)


def _end_time(controllers) -> float:
    """The last key time over every bone."""
    ends = [0.0]
    for _name, kd in controllers:
        if kd.num_rotation_keys:
            ends.append(kd.quaternion_keys[-1].time)
        if kd.translations.num_keys:
            ends.append(kd.translations.keys[-1].time)
    return max(ends)


def parse_morrowind_kf(kf_path) -> dict:
    """{bone: {time: (translation or None, w,x,y,z rotation or None)}} on one shared grid.

    The same float times key every bone, as parse_kf_file's callers expect of
    one coherent clip.
    """
    controllers = _bone_controllers(read_nif(str(kf_path)).roots[0])
    grid = np.arange(0.0, _end_time(controllers) + FRAME_STEP / 2, FRAME_STEP)
    times = [float(t) for t in grid]
    out = {}
    for name, kd in controllers:
        rot = _sample_rotations(list(kd.quaternion_keys), grid)
        trans = _sample_translations(list(kd.translations.keys), grid)
        if rot is None and trans is None:
            continue
        out[name] = {t: (None if trans is None else tuple(map(float, trans[i])),
                         None if rot is None else tuple(map(float, rot[i])))
                     for i, t in enumerate(times)}
    return out
