"""Retarget Oblivion creature .kf files onto a fitted skeleton, in place format.

Each clip goes through clip_retarget.retarget_clip (bone-local rotation
deviation from the rest pose, the accumulation bones keeping their
translation deviation); every other bone holds its fitted rest offset.  The
keys are written back into a copy of the ORIGINAL kf, so text keys,
priorities, visibility channels and everything else authored survive.

See: skyb_retarget/README.md#retargeting-the-animations
"""
import os
from dataclasses import replace

import numpy as np

from asset_convert.havok.clip_retarget import (Skeleton, retarget_clip,
                                               world_positions)
from asset_convert.havok.kf_decode import (BoneTrack, controlled_block_target,
                                           decode_kf)
from asset_convert.havok.kf_writer import transform_interpolator
from pyffi.formats.nif import NifFormat
from skyb_retarget.leg_ik import LegRig, plant_feet
from skyb_retarget.reach_ik import ReachRig, reach_tips

#: Bones whose translation follows the source animation (body height, root motion).
TRANSLATED = ('Bip01', 'Bip01 NonAccum')

#: Seconds per sample when decoding.
FPS = 30.0


def root_identity(skel: Skeleton) -> Skeleton:
    """`skel` with an identity root, its rest folded into the root's children.

    The engine plays the accumulation root as identity and the clips carry
    its rest on the child, so rest worlds stay where the clips put them.
    See: docs/commentary/asset_convert_falloutnv.md#accum-root-identity
    """
    local = skel.local.copy()
    for i, p in enumerate(skel.parents):
        if p < 0:
            local[i] = np.eye(4)
        elif skel.parents[p] < 0:
            local[i] = skel.local[i] @ skel.local[p]
    return Skeleton(skel.names, skel.parents, local)


def _one_hemisphere(q: np.ndarray) -> None:
    """Flip quaternions in place so consecutive keys never jump hemispheres."""
    for k in range(1, len(q)):
        if np.dot(q[k], q[k - 1]) < 0:
            q[k] = -q[k]


def _without_root(clip, src: Skeleton):
    """`clip` with the root's track (the root motion) removed."""
    root = src.names[src.parents.index(-1)]
    return replace(clip, tracks=[t for t in clip.tracks if t.bone != root])


def first_frame_worlds(kf: str, src: Skeleton) -> np.ndarray:
    """Source world matrices at `kf`'s first frame (root folded, no root motion)."""
    src = root_identity(src)
    return world_positions(_without_root(decode_kf(kf, FPS)[0], src), src, 0)


def match_deltas(src: Skeleton, dst: Skeleton, bones) -> dict:
    """Matched-pose deltas making `bones` copy the source's world rotation."""
    src, dst = root_identity(src), root_identity(dst)
    return {b: np.linalg.inv(src.world[src.index[b]]) @ dst.world[dst.index[b]]
            for b in bones if b in src.index}


def retarget_tracks(clip, src: Skeleton, dst: Skeleton, legs=(), deltas=None,
                    reach=()) -> list:
    """The clip's tracks over `dst`, with every bone's translation filled in.

    The root is left out: its track (root motion) stays the source's own.
    `legs` is (leg tuples, body bone) for leg_ik.plant_feet; `deltas`
    (match_deltas) copies chosen bones' world rotation; `reach` is (chains,
    body bone) for reach_ik.reach_tips. Each may be empty.
    """
    src, dst = root_identity(src), root_identity(dst)
    full, clip = clip, _without_root(clip, src)
    out = retarget_clip(clip, src, dst, {n: n for n in dst.names},
                        deltas=deltas, translated=TRANSLATED)
    scales = {t.bone: t.scales for t in clip.tracks}
    n = len(clip.times)
    tracks = []
    for tr in out.tracks:
        trans = tr.translations
        if trans is None:
            trans = np.tile(dst.local[dst.index[tr.bone]][3, :3], (n, 1))
        tracks.append(BoneTrack(bone=tr.bone, rotations=tr.rotations,
                                translations=trans, scales=scales.get(tr.bone)))
    if reach:
        reach_tips(tracks, clip, ReachRig(src, dst, *reach))
    if legs:
        rig = LegRig(src, dst, *legs)
        plant_feet(tracks, clip, rig)
        _scale_travel(tracks, dst, legs[1], rig.stride)
        tracks += _scaled_root(full, src, rig.stride)
    for tr in tracks:
        _one_hemisphere(tr.rotations)
    return tracks


def _level(stride: float) -> np.ndarray:
    """Per-axis factors scaling level travel by `stride`, height untouched."""
    return np.array([stride, stride, 1.0])


def _scale_travel(tracks: list, dst: Skeleton, body: str, stride: float) -> None:
    """Scale the body bone's level travel from rest by `stride` (idles and attacks carry their motion there)."""
    rest = dst.local[dst.index[body]][3, :3]
    for tr in tracks:
        if tr.bone == body:
            tr.translations = rest + (tr.translations - rest) * _level(stride)


def _scaled_root(clip, src: Skeleton, stride: float) -> list:
    """The root track with its level travel scaled by `stride` (locomotion carries its motion there)."""
    root = src.names[src.parents.index(-1)]
    return [BoneTrack(bone=root, rotations=tr.rotations, scales=tr.scales,
                      translations=tr.translations * _level(stride))
            for tr in clip.tracks
            if tr.bone == root and tr.translations is not None]


def rewrite_kf(src_kf: str, out_kf: str, src: Skeleton, dst: Skeleton,
               limbs=((), None, ())) -> int:
    """Write `src_kf` retargeted onto `dst` to `out_kf`; returns tracks replaced.

    `limbs` is (legs, deltas, reach) for retarget_tracks.
    """
    clip = decode_kf(src_kf, FPS)[0]
    tracks = {t.bone: t for t in retarget_tracks(clip, src, dst, *limbs)}
    data = NifFormat.Data()
    with open(src_kf, 'rb') as f:
        data.read(f)
    seq = next(r for r in data.roots
               if isinstance(r, NifFormat.NiControllerSequence))
    times = clip.times + float(seq.start_time)
    replaced = 0
    for cb in seq.controlled_blocks:
        tr = tracks.get(controlled_block_target(cb))
        if tr is None or not isinstance(cb.interpolator, (
                NifFormat.NiTransformInterpolator,
                NifFormat.NiBSplineTransformInterpolator)):
            continue
        cb.interpolator = transform_interpolator(tr, times)
        replaced += 1
    os.makedirs(os.path.dirname(os.path.abspath(out_kf)), exist_ok=True)
    with open(out_kf, 'wb') as f:
        data.write(f)
    return replaced
