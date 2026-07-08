"""Write a DecodedClip as a Skyrim-format .kf and convert it to LE .hkx.

The KF layout mirrors exactly what `hkxcmd EXPORTKF` produces from vanilla
Skyrim animations (verified on deer walkforward: NIF 20.2.0.7 / uv 11 / uv2 83,
NiControllerSequence with DIRECT node-name strings, per-track
NiTransformInterpolator + NiTransformData with QUADRATIC quaternion keys and
LINEAR translation keys, `start`/`end` text keys). `hkxcmd CONVERTKF
<skeleton.hkx> <clip.kf> <clip.hkx>` then does the binding + spline compression
against our generated skeleton.hkx — track↔bone matching is by node name, which
is why the faithful-port pipeline (Oblivion bone names everywhere) needs no
retarget step.
"""

import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from asset_convert import pyffi_monkey_patch  # noqa: F401
from asset_convert.hkx_xml import HKXCMD
from asset_convert.kf_decode import DecodedClip, decode_kf, split_root_motion
from pyffi.formats.nif import NifFormat

KF_VERSION = 0x14020007
KF_USER_VERSION = 11      # what hkxcmd EXPORTKF emits (not 12)
KF_USER_VERSION_2 = 83

KEY_LINEAR = 1
KEY_QUADRATIC = 2


def write_skyrim_kf(clip: DecodedClip, out_path: str,
                    skeleton_bone_names=None) -> int:
    """Write the clip as a Skyrim-format .kf. Returns the track count.

    skeleton_bone_names: optional set — tracks whose bone is not in the
    skeleton are dropped (CONVERTKF warns/errors on unknown tracks).
    """
    data = NifFormat.Data(version=KF_VERSION,
                          user_version=KF_USER_VERSION,
                          user_version_2=KF_USER_VERSION_2)
    # PyFFI's fresh header defaults endian_type to 0 (BIG endian) — every
    # reader then byteswaps the whole file into garbage. Must be 1 (little).
    data.header.endian_type = 1

    seq = NifFormat.NiControllerSequence()
    seq.name = clip.name.encode('latin-1')
    seq.start_time = 0.0
    seq.stop_time = clip.duration
    seq.cycle_type = clip.cycle_type
    seq.frequency = clip.frequency or 1.0
    seq.weight = 1.0

    tk = NifFormat.NiTextKeyExtraData()
    keys = clip.text_keys or [(0.0, 'start'), (clip.duration, 'end')]
    tk.num_text_keys = len(keys)
    tk.text_keys.update_size()
    for i, (t, s) in enumerate(keys):
        tk.text_keys[i].time = t
        tk.text_keys[i].value = s.strip().encode('latin-1')
    seq.text_keys = tk

    tracks = [tr for tr in clip.tracks
              if skeleton_bone_names is None or tr.bone in skeleton_bone_names]

    seq.num_controlled_blocks = len(tracks)
    seq.controlled_blocks.update_size()

    for i, tr in enumerate(tracks):
        cb = seq.controlled_blocks[i]
        cb.node_name = tr.bone.encode('latin-1')
        cb.controller_type = b'NiTransformController'
        cb.priority = 0

        interp = NifFormat.NiTransformInterpolator()
        td = NifFormat.NiTransformData()
        interp.data = td

        # static transform on the interpolator = first sample (used by the
        # engine before the first key / for missing channels)
        if tr.translations is not None:
            interp.translation.x, interp.translation.y, interp.translation.z = \
                (float(v) for v in tr.translations[0])
        if tr.rotations is not None:
            (interp.rotation.w, interp.rotation.x,
             interp.rotation.y, interp.rotation.z) = \
                (float(v) for v in tr.rotations[0])
        interp.scale = float(tr.scales[0]) if tr.scales is not None else 1.0

        if tr.rotations is not None:
            td.rotation_type = KEY_QUADRATIC
            td.num_rotation_keys = len(clip.times)
            td.quaternion_keys.update_size()
            for k, (t, q) in enumerate(zip(clip.times, tr.rotations)):
                qk = td.quaternion_keys[k]
                qk.time = float(t)
                qk.value.w, qk.value.x, qk.value.y, qk.value.z = \
                    (float(v) for v in q)

        if tr.translations is not None:
            td.translations.interpolation = KEY_LINEAR
            td.translations.num_keys = len(clip.times)
            td.translations.keys.update_size()
            for k, (t, v) in enumerate(zip(clip.times, tr.translations)):
                key = td.translations.keys[k]
                key.time = float(t)
                key.value.x, key.value.y, key.value.z = \
                    (float(c) for c in v)

        if tr.scales is not None and (np.ptp(tr.scales) > 1e-6):
            td.scales.interpolation = KEY_LINEAR
            td.scales.num_keys = len(clip.times)
            td.scales.keys.update_size()
            for k, (t, s) in enumerate(zip(clip.times, tr.scales)):
                td.scales.keys[k].time = float(t)
                td.scales.keys[k].value = float(s)

        cb.interpolator = interp

    data.roots = [seq]
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, 'wb') as f:
        data.write(f)
    return len(tracks)


def convertkf(skeleton_hkx: str, kf_path: str, out_hkx: str) -> None:
    """hkxcmd CONVERTKF wrapper (LE 32-bit output). Backslash paths only."""
    skeleton_hkx = os.path.abspath(skeleton_hkx)
    kf_path = os.path.abspath(kf_path)
    out_hkx = os.path.abspath(out_hkx)
    os.makedirs(os.path.dirname(out_hkx), exist_ok=True)
    res = subprocess.run([HKXCMD, 'convertkf', skeleton_hkx, kf_path, out_hkx],
                         capture_output=True, text=True)
    if res.returncode != 0 or not os.path.exists(out_hkx):
        raise RuntimeError(f'hkxcmd convertkf failed ({res.returncode}) '
                           f'for {kf_path}:\n{res.stdout}\n{res.stderr}')


def convert_clip(ob_kf_path: str, skeleton_hkx: str, out_hkx: str,
                 skeleton_bone_names=None, fps: float = 30.0,
                 keep_kf: bool = False, extract_motion: bool = True):
    """Full Oblivion .kf → Skyrim LE .hkx conversion for one clip.

    Returns (DecodedClip, motion_dict_or_None, track_count).
    """
    clips = decode_kf(ob_kf_path, fps)
    if not clips:
        raise ValueError(f'no NiControllerSequence in {ob_kf_path}')
    clip = clips[0]
    motion = split_root_motion(clip) if extract_motion else None
    kf_path = os.path.splitext(out_hkx)[0] + '.skyrim.kf'
    n = write_skyrim_kf(clip, kf_path, skeleton_bone_names)
    try:
        convertkf(skeleton_hkx, kf_path, out_hkx)
    finally:
        if not keep_kf and os.path.exists(kf_path):
            try:
                os.remove(kf_path)
            except OSError:
                pass
    return clip, motion, n


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(
        description='Convert Oblivion .kf → Skyrim LE .hkx via a generated '
                    'skeleton.hkx')
    ap.add_argument('ob_kf')
    ap.add_argument('skeleton_hkx')
    ap.add_argument('out_hkx')
    ap.add_argument('--fps', type=float, default=30.0)
    ap.add_argument('--keep-kf', action='store_true')
    ap.add_argument('--no-motion', action='store_true',
                    help='keep root motion in the tracks')
    args = ap.parse_args()

    clip, motion, n = convert_clip(args.ob_kf, args.skeleton_hkx, args.out_hkx,
                                   fps=args.fps, keep_kf=args.keep_kf,
                                   extract_motion=not args.no_motion)
    print(f"{args.out_hkx}: '{clip.name}' {n} tracks, "
          f"dur {clip.duration:.3f}s, "
          f"{os.path.getsize(args.out_hkx)} bytes")
    if motion:
        parts = []
        if motion['translations'] is not None:
            parts.append(f"trans {np.linalg.norm(motion['translations'][-1]):.1f}u")
        if motion['rotations'] is not None:
            q = motion['rotations'][-1]
            ang = 2 * np.degrees(np.arccos(np.clip(abs(q[0]), -1, 1)))
            parts.append(f"rot {ang:.1f}deg")
        print(f"  root motion [{motion['bone']}]: {', '.join(parts)}")
