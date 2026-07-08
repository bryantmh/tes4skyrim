"""Oblivion .kf → Skyrim LE .hkx via pynifly's native hk_2010 writer.

This is the PRIMARY animation conversion path. It uses the pure-Python
hkaSplineCompressedAnimation writer vendored from pynifly 27.4.0
(`external/pynifly_hkx/anim_skyrim.py` + spline math in `anim_fo4.py`, no
Blender dependency; local alignment fixes marked `# TESConversion:`) because
hkxcmd's CONVERTKF compressor is unusably lossy: vanilla deer walkforward
round-trips through CONVERTKF with a median 7.4° / max 37.6° per-bone rotation
error (measured 2026-07-07), and our clips fared no better. pynifly's writer
takes exact per-frame track data.

`kf_writer.py` (Skyrim-format KF + CONVERTKF) is kept as a debugging path —
its KF output is also useful for eyeballing clips in NifSkope.

Pipeline: kf_decode.decode_kf → split_root_motion → AnimationData (one track
per skeleton bone, identity binding) → write_skyrim_animation(ptr_size=4).
"""

import os
import sys

import numpy as np

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from external.pynifly_hkx.anim_fo4 import (Annotation,      # noqa: E402
                                           AnimationData, TrackData)
from external.pynifly_hkx.anim_skyrim import (               # noqa: E402
    load_skyrim_animation, write_skyrim_animation)

from asset_convert.kf_decode import (DecodedClip, decode_kf,  # noqa: E402
                                     split_root_motion)


def clip_to_animation_data(clip: DecodedClip, bone_names: list,
                           reference_pose=None) -> AnimationData:
    """Build a pynifly AnimationData with one track per skeleton bone.

    bone_names: skeleton bone order (from hkx_skeleton.load_skeleton_bones).
    reference_pose: optional {bone: (trans(3), quat_wxyz(4), scale)} used for
    bones the clip does not animate; defaults to identity (the engine blends
    against the skeleton reference pose anyway, but vanilla files carry real
    values, so pass the skeleton pose when available).
    """
    n_frames = len(clip.times)
    track_map = {t.bone: t for t in clip.tracks}

    anim = AnimationData()
    anim.duration = float(clip.duration)
    anim.num_frames = n_frames
    anim.num_tracks = len(bone_names)
    anim.frame_duration = (clip.duration / (n_frames - 1)
                           if n_frames > 1 else 1.0 / 30.0)
    anim.bone_names = list(bone_names)
    anim.track_to_bone_indices = list(range(len(bone_names)))
    anim.original_skeleton_name = bone_names[0] if bone_names else ''

    for bone in bone_names:
        td = TrackData()
        tr = track_map.get(bone)
        ref_t, ref_q_wxyz, ref_s = (0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0), 1.0
        if reference_pose and bone in reference_pose:
            ref_t, ref_q_wxyz, ref_s = reference_pose[bone]

        for f in range(n_frames):
            if tr is not None and tr.translations is not None:
                t = tr.translations[f]
            else:
                t = ref_t
            td.translations.append([float(t[0]), float(t[1]), float(t[2])])

            if tr is not None and tr.rotations is not None:
                w, x, y, z = tr.rotations[f]
            else:
                w, x, y, z = ref_q_wxyz
            # pynifly rotations are x,y,z,w
            td.rotations.append([float(x), float(y), float(z), float(w)])

            if tr is not None and tr.scales is not None:
                s = float(tr.scales[f])
            else:
                s = float(ref_s)
            td.scales.append([s, s, s])
        anim.tracks.append(td)

    for t, text in clip.text_keys:
        text = text.strip()
        if text and text.lower() not in ('start', 'end'):
            anim.annotations.append(Annotation(time=float(t), text=text))
    return anim


def reference_pose_from_bones(bones) -> dict:
    """hkx_skeleton.Bone list → {name: (trans, quat_wxyz, scale)}."""
    pose = {}
    for b in bones:
        x, y, z, w = b.quat_xyzw
        pose[b.name] = (b.translation, (w, x, y, z), b.scale)
    return pose


def build_animation_xml(anim: 'AnimationData', skeleton_root: str) -> str:
    """Render the clip as hk_2010 packfile XML (vanilla walkforward layout).

    The binary is produced by hkxcmd's real Havok serializer (`compile_hkx`)
    — pynifly's hand-rolled binary writer produced files that crashed real
    Havok deserializers (unaligned/idiosyncratic layout), so it is used only
    for its spline COMPRESSOR here; layout is Havok's own.
    """
    from asset_convert.hkx_xml import HkxPackfile, _esc
    from external.pynifly_hkx.anim_fo4 import _compress_all_blocks

    if not anim.num_blocks:
        max_fpb = anim.max_frames_per_block or 256
        anim.num_blocks = max(1, (anim.num_frames + max_fpb - 1) // max_fpb)
    blob, block_offsets = _compress_all_blocks(anim, rot_quant=1)

    frame_dur = anim.frame_duration
    # vanilla single-block convention: blockDuration 8.5 / inverse 0.117647
    block_dur = (anim.max_frames_per_block or 256) * frame_dur if \
        anim.num_blocks > 1 else 8.5

    pf = HkxPackfile(first_id=45)
    mrc = pf.add('hkMemoryResourceContainer')
    spline = pf.add('hkaSplineCompressedAnimation')
    binding = pf.add('hkaAnimationBinding')
    container = pf.add('hkaAnimationContainer')
    top = pf.add('hkRootLevelContainer')

    mrc.param('name', '')
    mrc.param_raw('resourceHandles', '', numelements=0)
    mrc.param_raw('children', '', numelements=0)

    spline.param('type', 'HK_SPLINE_COMPRESSED_ANIMATION')
    spline.param('duration', f'{anim.duration:.6f}')
    spline.param('numberOfTransformTracks', anim.num_tracks)
    spline.param('numberOfFloatTracks', 0)
    spline.param('extractedMotion', 'null')
    # annotationTracks: all annotations on track 0, empty names (vanilla)
    ann_body = []
    for i in range(anim.num_tracks):
        if i == 0 and anim.annotations:
            inner = '\n'.join(
                '<hkobject>\n'
                f'\t<hkparam name="time">{a.time:.6f}</hkparam>\n'
                f'\t<hkparam name="text">{_esc(a.text)}</hkparam>\n'
                '</hkobject>' for a in anim.annotations)
            ann_body.append(
                '<hkobject>\n\t<hkparam name="trackName"></hkparam>\n'
                f'\t<hkparam name="annotations" '
                f'numelements="{len(anim.annotations)}">\n'
                + '\n'.join('\t\t' + ln for ln in inner.split('\n'))
                + '\n\t</hkparam>\n</hkobject>')
        else:
            ann_body.append(
                '<hkobject>\n\t<hkparam name="trackName"></hkparam>\n'
                '\t<hkparam name="annotations" numelements="0"></hkparam>\n'
                '</hkobject>')
    spline.param_raw('annotationTracks', '\n'.join(ann_body),
                     numelements=anim.num_tracks)
    spline.param('numFrames', anim.num_frames)
    spline.param('numBlocks', anim.num_blocks)
    spline.param('maxFramesPerBlock', anim.max_frames_per_block or 256)
    spline.param('maskAndQuantizationSize', 4 * anim.num_tracks)
    spline.param('blockDuration', f'{block_dur:.6f}')
    spline.param('blockInverseDuration', f'{1.0 / block_dur:.6f}')
    spline.param('frameDuration', f'{frame_dur:.6f}')
    spline.param_array('blockOffsets', block_offsets)
    spline.param_array('floatBlockOffsets', [len(blob) - 4] * anim.num_blocks)
    spline.param_array('transformOffsets', [])
    spline.param_array('floatOffsets', [])
    spline.param_array('data', list(blob), per_line=16)

    binding.param('originalSkeletonName', skeleton_root)
    binding.param('animation', spline.ref)
    binding.param_array('transformTrackToBoneIndices', [])
    binding.param_array('floatTrackToFloatSlotIndices', [])
    binding.param('blendHint', 'NORMAL')

    container.param_array('skeletons', [])
    container.param_array('animations', [spline.ref])
    container.param_array('bindings', [binding.ref])
    container.param_array('attachments', [])
    container.param_array('skins', [])

    top.param_structs('namedVariants', [
        [('name', 'Merged Animation Container'),
         ('className', 'hkaAnimationContainer'),
         ('variant', container.ref)],
        [('name', 'Resource Data'),
         ('className', 'hkMemoryResourceContainer'),
         ('variant', mrc.ref)],
    ])
    return pf.render(top)


def convert_clip_hkx(ob_kf_path: str, bones, out_hkx: str,
                     fps: float = 30.0, extract_motion: bool = True,
                     keep_xml: bool = False):
    """Oblivion .kf → Skyrim LE .hkx (XML → hkxcmd serializer).

    bones: hkx_skeleton.Bone list of the creature's generated skeleton.
    Returns (DecodedClip, motion_or_None).
    """
    from asset_convert.hkx_xml import compile_hkx

    clips = decode_kf(ob_kf_path, fps)
    if not clips:
        raise ValueError(f'no NiControllerSequence in {ob_kf_path}')
    clip = clips[0]
    motion = split_root_motion(clip) if extract_motion else None

    bone_names = [b.name for b in bones]
    anim = clip_to_animation_data(clip, bone_names,
                                  reference_pose_from_bones(bones))
    xml = build_animation_xml(anim, skeleton_root=bone_names[0])
    xml_path = os.path.splitext(out_hkx)[0] + '.hkx.xml'
    os.makedirs(os.path.dirname(os.path.abspath(out_hkx)), exist_ok=True)
    with open(xml_path, 'w', encoding='ascii', errors='replace',
              newline='\n') as f:
        f.write(xml)
    compile_hkx(xml_path, out_hkx)
    if not keep_xml:
        os.remove(xml_path)
    return clip, motion


def verify_hkx(hkx_path: str, clip: DecodedClip, bone_names: list) -> dict:
    """Read the written hkx back (pynifly reader) and measure error vs clip.

    Returns {'max_trans_err', 'max_rot_err_deg', 'frames', 'tracks'}.
    """
    back = load_skyrim_animation(hkx_path)
    track_map = {t.bone: t for t in clip.tracks}
    max_t, max_r = 0.0, 0.0
    n = min(back.num_frames, len(clip.times))
    for ti, bone in enumerate(back.bone_names):
        tr = track_map.get(bone)
        if tr is None or ti >= len(back.tracks):
            continue
        bt = back.tracks[ti]
        if tr.translations is not None and bt.translations:
            a = tr.translations[:n]
            b = np.array(bt.translations[:n])
            max_t = max(max_t, float(np.linalg.norm(a - b, axis=1).max()))
        if tr.rotations is not None and bt.rotations:
            a = tr.rotations[:n]                      # wxyz
            b = np.array(bt.rotations[:n])            # xyzw
            b = b[:, [3, 0, 1, 2]]
            dots = np.clip(np.abs(np.sum(a * b, axis=1)), -1, 1)
            max_r = max(max_r, float(np.degrees(2 * np.arccos(dots)).max()))
    return {'max_trans_err': max_t, 'max_rot_err_deg': max_r,
            'frames': back.num_frames, 'tracks': back.num_tracks}


if __name__ == '__main__':
    import argparse
    from asset_convert.hkx_skeleton import load_skeleton_bones

    ap = argparse.ArgumentParser(
        description='Oblivion .kf → Skyrim LE .hkx via native spline writer')
    ap.add_argument('ob_kf')
    ap.add_argument('skeleton_nif', help='Oblivion skeleton.nif (bone source)')
    ap.add_argument('out_hkx')
    ap.add_argument('--fps', type=float, default=30.0)
    ap.add_argument('--no-motion', action='store_true')
    ap.add_argument('--verify', action='store_true')
    args = ap.parse_args()

    bones = load_skeleton_bones(args.skeleton_nif)
    clip, motion = convert_clip_hkx(args.ob_kf, bones, args.out_hkx,
                                    fps=args.fps,
                                    extract_motion=not args.no_motion)
    print(f"{args.out_hkx}: '{clip.name}' {len(clip.tracks)} src tracks → "
          f"{len(bones)} bone tracks, dur {clip.duration:.3f}s, "
          f"{os.path.getsize(args.out_hkx)} bytes")
    if motion:
        t = motion['translations']
        print(f"  root motion [{motion['bone']}]"
              + (f" trans {np.linalg.norm(t[-1]):.1f}u" if t is not None else '')
              + (' + rotation' if motion['rotations'] is not None else ''))
    if args.verify:
        stats = verify_hkx(args.out_hkx, clip, [b.name for b in bones])
        print(f"  verify: {stats['tracks']} tracks {stats['frames']} frames, "
              f"max trans err {stats['max_trans_err']:.4f}u, "
              f"max rot err {stats['max_rot_err_deg']:.4f} deg")
