"""Decode Oblivion .kf animations into uniformly-sampled bone tracks.

Handles both interpolator families found in the TES4 corpus:
  - NiTransformInterpolator + NiTransformData (plain keyframes)
  - NiBSplineCompTransformInterpolator / NiBSplineTransformInterpolator
    (B-spline compressed — the MAJORITY of creature/actor animation data)

PyFFI 2.2.3 dequantizes B-spline control points (get_translations() etc.) but
does NOT evaluate the curve (its own docstring says so) — control points of a
cubic B-spline lie off-curve, so treating them as keys distorts the motion.
The evaluation here mirrors NifSkope's glcontroller.cpp exactly:
degree 3, clamped integer knot vector, Cox–de Boor recursion, parameter
v = (time - start) / (stop - start) * (n_ctrl - degree).

Output is a DecodedClip of per-bone tracks sampled at a fixed fps, ready for
retarget-free HKX writing (the converted skeleton keeps Oblivion bone names).
"""

import math
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from asset_convert import pyffi_monkey_patch  # noqa: F401  (patches time.clock etc.)
from pyffi.formats.nif import NifFormat

SENTINEL = -3.0e38          # NIF "no static value" marker (-FLT_MAX)
SPLINE_DEGREE = 3
DEFAULT_FPS = 30.0

# NiControllerSequence cycle types
CYCLE_LOOP = 0
CYCLE_REVERSE = 1
CYCLE_CLAMP = 2


@dataclass
class BoneTrack:
    """Uniformly sampled local transform channel set for one target node."""
    bone: str
    # All arrays share DecodedClip.times length S. None = channel absent
    # (consumer should hold the skeleton rest pose for that channel).
    translations: Optional[np.ndarray] = None   # (S, 3)
    rotations: Optional[np.ndarray] = None      # (S, 4) w,x,y,z unit quats
    scales: Optional[np.ndarray] = None         # (S,)


@dataclass
class DecodedClip:
    name: str
    duration: float
    cycle_type: int
    frequency: float
    times: np.ndarray = field(default_factory=lambda: np.zeros(0))  # (S,)
    tracks: list = field(default_factory=list)          # [BoneTrack]
    text_keys: list = field(default_factory=list)       # [(time, str)]
    skipped_blocks: list = field(default_factory=list)  # [(bone, reason)]


# ---------------------------------------------------------------------------
# B-spline evaluation (NifSkope glcontroller.cpp algorithm)
# ---------------------------------------------------------------------------

def _knots(n_ctrl: int, degree: int = SPLINE_DEGREE) -> np.ndarray:
    """Clamped integer knot vector: 0 repeated, ramp, end repeated."""
    t = degree + 1
    n = n_ctrl - 1
    u = np.empty(n + t + 1, dtype=np.float64)
    for j in range(n + t + 1):
        if j < t:
            u[j] = 0
        elif j <= n:
            u[j] = j - t + 1
        else:
            u[j] = n - t + 2
    return u


def _basis_weights(n_ctrl: int, v: float, u: np.ndarray,
                   degree: int = SPLINE_DEGREE) -> np.ndarray:
    """Cox–de Boor basis weights for all control points at parameter v.

    Bottom-up equivalent of NifSkope's recursive blend(); same zero-division
    guards and the same half-open [u_k, u_k+1) base case.
    """
    t = degree + 1
    n = n_ctrl - 1
    # order-1 basis over the n+t spans
    b = np.zeros(n + t, dtype=np.float64)
    for k in range(n + t):
        b[k] = 1.0 if (u[k] <= v < u[k + 1]) else 0.0
    # raise order
    for order in range(2, t + 1):
        nb = np.zeros(n + t, dtype=np.float64)
        for k in range(n + t + 1 - order):
            d1 = u[k + order - 1] - u[k]
            d2 = u[k + order] - u[k + 1]
            val = 0.0
            if d1 != 0.0:
                val += (v - u[k]) / d1 * b[k]
            if d2 != 0.0:
                val += (u[k + order] - v) / d2 * b[k + 1]
            nb[k] = val
        b = nb
    return b[:n_ctrl]


def eval_bspline(ctrl: np.ndarray, v_params: np.ndarray,
                 degree: int = SPLINE_DEGREE) -> np.ndarray:
    """Evaluate a clamped uniform cubic B-spline at each parameter value.

    ctrl: (N, C) control points; v_params in [0, N - degree].
    Returns (S, C).
    """
    n_ctrl = ctrl.shape[0]
    if n_ctrl == 1:
        return np.repeat(ctrl, len(v_params), axis=0)
    u = _knots(n_ctrl, degree)
    end = float(n_ctrl - degree)
    out = np.empty((len(v_params), ctrl.shape[1]), dtype=np.float64)
    for i, v in enumerate(v_params):
        if v >= end:  # NifSkope end clamp: exactly the last control point
            out[i] = ctrl[-1]
        else:
            w = _basis_weights(n_ctrl, v, u, degree)
            out[i] = w @ ctrl
    return out


# ---------------------------------------------------------------------------
# Interpolator sampling
# ---------------------------------------------------------------------------

def _is_sentinel(*vals) -> bool:
    return any(v < SENTINEL for v in vals)


def _normalize_quats(q: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(q, axis=1, keepdims=True)
    norm[norm == 0] = 1.0
    return q / norm


def _hemisphere_align(q: np.ndarray) -> np.ndarray:
    """Flip quaternion signs so consecutive entries stay in one hemisphere."""
    q = q.copy()
    for i in range(1, len(q)):
        if np.dot(q[i], q[i - 1]) < 0:
            q[i] = -q[i]
    return q


def _sample_bspline(interp, times: np.ndarray):
    """Sample a NiBSpline(Comp)TransformInterpolator at the given times."""
    start = float(interp.start_time)
    stop = float(interp.stop_time)
    span = stop - start if stop > start else 1.0

    trans = rots = scales = None
    ctrl_t = np.array(list(interp.get_translations()), dtype=np.float64)
    ctrl_r = np.array(list(interp.get_rotations()), dtype=np.float64)
    ctrl_s = np.array(list(interp.get_scales()), dtype=np.float64)

    def params(n_ctrl):
        v = (np.clip(times, start, stop) - start) / span * (n_ctrl - SPLINE_DEGREE)
        return v

    if len(ctrl_t):
        trans = eval_bspline(ctrl_t, params(len(ctrl_t)))
    if len(ctrl_r):
        # stored order is w,x,y,z; blend then renormalize (weights sum to 1
        # but a quat blend is not length-preserving)
        rots = _normalize_quats(eval_bspline(_hemisphere_align(ctrl_r),
                                             params(len(ctrl_r))))
    if len(ctrl_s):
        scales = eval_bspline(ctrl_s.reshape(-1, 1), params(len(ctrl_s)))[:, 0]

    # absent channels keep the interpolator's static transform (NifSkope's
    # bsplineinterpolate returns false for a 65535 handle, leaving the
    # existing value in place)
    s_trans, s_rots, s_scales = _sample_transform_data_static(interp, times)
    if trans is None:
        trans = s_trans
    if rots is None:
        rots = s_rots
    if scales is None:
        scales = s_scales

    return trans, rots, scales


def _lerp_channel(key_times: np.ndarray, key_vals: np.ndarray,
                  times: np.ndarray) -> np.ndarray:
    """Piecewise-linear sample with constant extrapolation (per component)."""
    out = np.empty((len(times), key_vals.shape[1]), dtype=np.float64)
    for c in range(key_vals.shape[1]):
        out[:, c] = np.interp(times, key_times, key_vals[:, c])
    return out


def _sample_transform_data(interp, times: np.ndarray):
    """Sample a NiTransformInterpolator's NiTransformData at the given times.

    Keys are interpolated linearly (quadratic tangents ignored — negligible at
    30 fps sampling density). Quaternions use lerp+normalize with hemisphere
    alignment.
    """
    td = interp.data
    trans = rots = scales = None

    if td is not None:
        # rotations: quaternion keys or XYZ euler channels
        if td.rotation_type == 4:
            xyz = td.xyz_rotations
            comps = []
            ok = True
            for axis in range(3):
                kg = xyz[axis]
                if kg.num_keys == 0:
                    comps.append(None)
                    continue
                kt = np.array([k.time for k in kg.keys], dtype=np.float64)
                kv = np.array([k.value for k in kg.keys], dtype=np.float64)
                comps.append(np.interp(times, kt, kv))
            if ok:
                e = [c if c is not None else np.zeros(len(times)) for c in comps]
                rots = _euler_xyz_to_quat(e[0], e[1], e[2])
        elif td.num_rotation_keys > 0:
            kt = np.array([k.time for k in td.quaternion_keys], dtype=np.float64)
            kv = np.array([[k.value.w, k.value.x, k.value.y, k.value.z]
                           for k in td.quaternion_keys], dtype=np.float64)
            rots = _normalize_quats(_lerp_channel(kt, _hemisphere_align(kv), times))

        if td.translations.num_keys > 0:
            kt = np.array([k.time for k in td.translations.keys], dtype=np.float64)
            kv = np.array([[k.value.x, k.value.y, k.value.z]
                           for k in td.translations.keys], dtype=np.float64)
            good = ~np.any(kv < SENTINEL, axis=1)
            if good.any():
                trans = _lerp_channel(kt[good], kv[good], times)

        if td.scales.num_keys > 0:
            kt = np.array([k.time for k in td.scales.keys], dtype=np.float64)
            kv = np.array([k.value for k in td.scales.keys], dtype=np.float64)
            scales = np.interp(times, kt, kv)

    # static fallbacks from the interpolator's own transform
    if trans is None:
        tx, ty, tz = (float(interp.translation.x), float(interp.translation.y),
                      float(interp.translation.z))
        if not _is_sentinel(tx, ty, tz):
            trans = np.tile([tx, ty, tz], (len(times), 1))
    if rots is None:
        qw, qx, qy, qz = (float(interp.rotation.w), float(interp.rotation.x),
                          float(interp.rotation.y), float(interp.rotation.z))
        if not _is_sentinel(qw, qx, qy, qz):
            rots = np.tile([qw, qx, qy, qz], (len(times), 1))
    if scales is None:
        s = float(interp.scale)
        if not _is_sentinel(s):
            scales = np.full(len(times), s)

    return trans, rots, scales


def _static_bspline_pose(interp, times: np.ndarray):
    """B-spline interpolator without basis data (e.g. bowidle.kf) = static."""
    return _sample_transform_data_static(interp, times)


def _sample_transform_data_static(interp, times: np.ndarray):
    trans = rots = scales = None
    tx, ty, tz = (float(interp.translation.x), float(interp.translation.y),
                  float(interp.translation.z))
    if not _is_sentinel(tx, ty, tz):
        trans = np.tile([tx, ty, tz], (len(times), 1))
    qw, qx, qy, qz = (float(interp.rotation.w), float(interp.rotation.x),
                      float(interp.rotation.y), float(interp.rotation.z))
    if not _is_sentinel(qw, qx, qy, qz):
        rots = np.tile([qw, qx, qy, qz], (len(times), 1))
    s = float(interp.scale)
    if not _is_sentinel(s):
        scales = np.full(len(times), s)
    return trans, rots, scales


def _euler_xyz_to_quat(rx, ry, rz) -> np.ndarray:
    """Vectorized XYZ-order euler → quaternion (w,x,y,z), Gamebryo convention
    (R = Rz @ Ry @ Rx applied to row vectors, i.e. X then Y then Z)."""
    hx, hy, hz = rx * 0.5, ry * 0.5, rz * 0.5
    cx, sx = np.cos(hx), np.sin(hx)
    cy, sy = np.cos(hy), np.sin(hy)
    cz, sz = np.cos(hz), np.sin(hz)
    w = cx * cy * cz + sx * sy * sz
    x = sx * cy * cz - cx * sy * sz
    y = cx * sy * cz + sx * cy * sz
    z = cx * cy * sz - sx * sy * cz
    return np.stack([w, x, y, z], axis=1)


# ---------------------------------------------------------------------------
# KF parsing
# ---------------------------------------------------------------------------

def _palette_string(palette_block, offset: int) -> Optional[str]:
    """Resolve a byte offset into a NiStringPalette to its string."""
    if palette_block is None or offset in (-1, 0xFFFFFFFF):
        return None
    raw = bytes(palette_block.palette.palette)
    if offset >= len(raw):
        return None
    end = raw.find(b'\x00', offset)
    if end == -1:
        end = len(raw)
    return raw[offset:end].decode('latin-1')


def _controlled_block_target(cb) -> Optional[str]:
    """Target node name of a controlled block (palette or direct field)."""
    name = None
    if getattr(cb, 'string_palette', None) is not None:
        name = _palette_string(cb.string_palette, cb.node_name_offset)
    if not name and getattr(cb, 'node_name', None):
        name = bytes(cb.node_name).decode('latin-1').rstrip('\x00')
    if not name and getattr(cb, 'target_name', None):
        name = bytes(cb.target_name).decode('latin-1').rstrip('\x00')
    return name or None


def decode_kf(kf_path: str, fps: float = DEFAULT_FPS) -> list:
    """Decode all NiControllerSequences in a .kf file.

    Returns a list of DecodedClip (Oblivion creature KFs have exactly one).
    """
    data = NifFormat.Data()
    with open(kf_path, 'rb') as f:
        data.read(f)

    clips = []
    for root in data.roots:
        if not isinstance(root, NifFormat.NiControllerSequence):
            continue
        clips.append(_decode_sequence(root, fps))
    return clips


def _decode_sequence(seq, fps: float) -> DecodedClip:
    start = float(seq.start_time)
    stop = float(seq.stop_time)
    duration = max(stop - start, 0.0)

    n_samples = max(int(round(duration * fps)), 1) + 1
    times = start + np.arange(n_samples, dtype=np.float64) / fps
    times[-1] = stop  # land exactly on the clip end

    clip = DecodedClip(
        name=bytes(seq.name).decode('latin-1').rstrip('\x00'),
        duration=duration,
        cycle_type=int(seq.cycle_type),
        frequency=float(seq.frequency),
        times=times - start,  # normalize to 0-based clip time
    )

    if seq.text_keys is not None:
        for tk in seq.text_keys.text_keys:
            clip.text_keys.append((float(tk.time) - start,
                                   bytes(tk.value).decode('latin-1')))

    for cb in seq.controlled_blocks:
        bone = _controlled_block_target(cb)
        if not bone:
            clip.skipped_blocks.append(('?', 'unresolvable target name'))
            continue
        interp = cb.interpolator
        if interp is None:
            clip.skipped_blocks.append((bone, 'no interpolator'))
            continue

        if isinstance(interp, NifFormat.NiBSplineTransformInterpolator):
            if interp.basis_data is None or interp.spline_data is None:
                trans, rots, scales = _static_bspline_pose(interp, times)
            else:
                trans, rots, scales = _sample_bspline(interp, times)
        elif isinstance(interp, NifFormat.NiTransformInterpolator):
            trans, rots, scales = _sample_transform_data(interp, times)
        elif isinstance(interp, (NifFormat.NiFloatInterpolator,
                                 NifFormat.NiBSplineFloatInterpolator,
                                 NifFormat.NiBoolInterpolator)):
            # float/bool channels drive bhkBlendControllers & visibility —
            # not needed for Skyrim clips (the behavior graph owns ragdoll
            # blending)
            clip.skipped_blocks.append((bone, type(interp).__name__))
            continue
        else:
            clip.skipped_blocks.append((bone, type(interp).__name__))
            continue

        if trans is None and rots is None and scales is None:
            clip.skipped_blocks.append((bone, 'all channels empty (rest pose)'))
            continue
        clip.tracks.append(BoneTrack(bone=bone, translations=trans,
                                     rotations=rots, scales=scales))
    return clip


def split_root_motion(clip: DecodedClip,
                      accum_bones=('Bip01 NonAccum', 'Bip01',
                                   'Bip02 NonAccum', 'Bip02')) -> Optional[dict]:
    """Extract root motion from the accumulation bone and make it in-place.

    Oblivion accumulates locomotion on the sequence accum root — usually
    `Bip01` itself (dog forward.kf: Bip01 y 0→74.6 while NonAccum is static),
    sometimes `Bip01 NonAccum`. Skyrim clips are in-place with motion delivered
    via animationdata (boundanims). Picks the candidate track with the largest
    actual displacement. Returns {'bone', 'times', 'translations',
    'rotations'} with motion RELATIVE to the first sample, or None when no
    candidate moves more than `min_displacement`. The chosen track is
    flattened to its first-sample transform in place.
    """
    min_displacement = 0.5   # game units; below this = no linear motion
    min_rotation = 0.02      # radians of root yaw; below this = no turning

    def _trans_span(tr):
        if tr.translations is None:
            return 0.0
        return float(np.linalg.norm(
            tr.translations.max(axis=0) - tr.translations.min(axis=0)))

    def _rot_span(tr):
        if tr.rotations is None:
            return 0.0
        # angle between first and each subsequent orientation
        q0 = tr.rotations[0]
        dots = np.clip(np.abs(tr.rotations @ q0), -1.0, 1.0)
        return float((2 * np.arccos(dots)).max())

    best = None
    best_score = 0.0
    for tr in clip.tracks:
        if tr.bone in accum_bones:
            score = _trans_span(tr) + _rot_span(tr) * 50.0
            if score > best_score:
                best_score = score
                best = tr

    if best is None:
        return None
    has_trans = _trans_span(best) >= min_displacement
    has_rot = _rot_span(best) >= min_rotation
    if not has_trans and not has_rot:
        return None

    motion = {'bone': best.bone, 'times': clip.times.copy(),
              'translations': None, 'rotations': None}
    if has_trans:
        motion['translations'] = best.translations - best.translations[0]
        best.translations = np.tile(best.translations[0],
                                    (len(clip.times), 1))
    if has_rot:
        # rotation relative to the first sample: q_rel = conj(q0) * q_t
        q0 = best.rotations[0]
        conj = np.array([q0[0], -q0[1], -q0[2], -q0[3]])
        motion['rotations'] = np.array(
            [_quat_mul(conj, q) for q in best.rotations])
        best.rotations = np.tile(best.rotations[0], (len(clip.times), 1))
    return motion


def _quat_mul(a, b):
    """Hamilton product of quaternions in (w,x,y,z) order."""
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return np.array([
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    ])


# ---------------------------------------------------------------------------
# CLI: summarize decoded clips (validation aid)
# ---------------------------------------------------------------------------

def _summarize(path: str, fps: float):
    for clip in decode_kf(path, fps):
        print(f"=== {os.path.basename(path)}: '{clip.name}' "
              f"dur={clip.duration:.3f}s cycle={clip.cycle_type} "
              f"samples={len(clip.times)} tracks={len(clip.tracks)} "
              f"skipped={len(clip.skipped_blocks)}")
        for t, s in clip.text_keys:
            print(f"  key {t:7.3f}  {s!r}")
        for tr in clip.tracks:
            parts = []
            if tr.translations is not None:
                lo = tr.translations.min(axis=0)
                hi = tr.translations.max(axis=0)
                parts.append(f"T[{lo[0]:.1f},{lo[1]:.1f},{lo[2]:.1f}.."
                             f"{hi[0]:.1f},{hi[1]:.1f},{hi[2]:.1f}]")
            if tr.rotations is not None:
                nrm = np.linalg.norm(tr.rotations, axis=1)
                parts.append(f"R(norm {nrm.min():.4f}..{nrm.max():.4f})")
            if tr.scales is not None:
                parts.append(f"S({tr.scales.min():.3f}..{tr.scales.max():.3f})")
            print(f"  {tr.bone:32s} {' '.join(parts)}")
        for bone, why in clip.skipped_blocks:
            print(f"  [skip] {bone}: {why}")


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Decode Oblivion .kf files '
                                 '(incl. B-spline compressed) to sampled tracks')
    ap.add_argument('paths', nargs='+', help='.kf file(s) or directory')
    ap.add_argument('--fps', type=float, default=DEFAULT_FPS)
    ap.add_argument('--max', type=int, default=10, help='max files from a dir')
    args = ap.parse_args()

    files = []
    for p in args.paths:
        if os.path.isdir(p):
            for rootdir, _dirs, names in os.walk(p):
                files.extend(os.path.join(rootdir, n) for n in names
                             if n.lower().endswith('.kf'))
        else:
            files.append(p)
    for f in files[:args.max]:
        _summarize(f, args.fps)
