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

from asset_convert.havok.behavior_clips import clip_state_name


from external.pynifly_hkx.anim_fo4 import (Annotation,
                                           AnimationData, TrackData)
from external.pynifly_hkx.anim_skyrim import (
    load_skyrim_animation)

from asset_convert.havok.hkx_skeleton import BONE_RENAMES, ROOT_BONE_NAME
from asset_convert.havok.hkx_xml import HkxPackfile, esc
from asset_convert.havok.kf_decode import (BoneTrack, DecodedClip, decode_kf,
                                           split_root_motion)


def _drop_unanimated_root(reference_pose, track_map):
    """The reference pose minus an unanimated accum root: bone 0 plays identity."""
    if not reference_pose or ROOT_BONE_NAME in track_map:
        return reference_pose
    return {b: v for b, v in reference_pose.items() if b != ROOT_BONE_NAME}


def _bone_track(tr, n_frames, ref) -> TrackData:
    """One TrackData, using `ref` (trans, quat_wxyz, scale) where tr is silent.

    Rotations arrive as w,x,y,z and are emitted as pynifly's x,y,z,w.
    """
    ref_t, ref_q_wxyz, ref_s = ref
    td = TrackData()
    for f in range(n_frames):
        t = tr.translations[f] if tr is not None and \
            tr.translations is not None else ref_t
        td.translations.append([float(t[0]), float(t[1]), float(t[2])])

        if tr is not None and tr.rotations is not None:
            w, x, y, z = tr.rotations[f]
        else:
            w, x, y, z = ref_q_wxyz
        td.rotations.append([float(x), float(y), float(z), float(w)])

        s = float(tr.scales[f]) if tr is not None and \
            tr.scales is not None else float(ref_s)
        td.scales.append([s, s, s])
    return td


def _merged_tracks(clip: DecodedClip, bone_names: list) -> dict:
    """{skeleton bone: merged BoneTrack copy} for the clip's tracks.

    A track already named for a skeleton bone keeps its name; any other is
    an Oblivion name and goes through BONE_RENAMES. Tracks targeting one
    bone merge channel-wise (a NiVisController scale beside a transform),
    into copies, so the caller's clip is untouched.
    See: docs/commentary/asset_convert_falloutnv.md#weapon-track-rename
    """
    known = set(bone_names)
    track_map = {}
    for t in clip.tracks:
        name = t.bone if t.bone in known else BONE_RENAMES.get(t.bone, t.bone)
        prev = track_map.get(name)
        if prev is None:
            track_map[name] = BoneTrack(
                bone=t.bone, translations=t.translations,
                rotations=t.rotations, scales=t.scales)
            continue
        for chan in ('translations', 'rotations', 'scales'):
            if getattr(prev, chan) is None and getattr(t, chan) is not None:
                setattr(prev, chan, getattr(t, chan))
    return track_map


def clip_to_animation_data(clip: DecodedClip, bone_names: list,
                           reference_pose=None,
                           annotations=None) -> AnimationData:
    """Build a pynifly AnimationData with one track per skeleton bone.

    bone_names: skeleton bone order (hkx_skeleton.load_skeleton_bones).
    reference_pose: {bone: (trans(3), quat_wxyz(4), scale)} for bones the
    clip omits, else identity. An omitted accum root is ALWAYS identity:
    NonAccum already carries the bind pose the NIF stores on `Bip01`.
    annotations: [(time, text)] engine events (parse_kf_events first).
    See: docs/commentary/asset_convert_falloutnv.md#accum-root-identity
    """
    n_frames = len(clip.times)
    track_map = _merged_tracks(clip, bone_names)
    anim = AnimationData()
    anim.duration = float(clip.duration)
    anim.num_frames = n_frames
    anim.num_tracks = len(bone_names)
    anim.frame_duration = (clip.duration / (n_frames - 1)
                           if n_frames > 1 else 1.0 / 30.0)
    anim.bone_names = list(bone_names)
    anim.track_to_bone_indices = list(range(len(bone_names)))
    anim.original_skeleton_name = bone_names[0] if bone_names else ''

    pose = _drop_unanimated_root(reference_pose, track_map)
    identity = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0), 1.0)
    for bone in bone_names:
        ref = pose[bone] if pose and bone in pose else identity
        anim.tracks.append(_bone_track(track_map.get(bone), n_frames, ref))

    for t, text in sorted(annotations or []):
        anim.annotations.append(Annotation(time=float(t), text=text))
    return anim


# Default (quadruped) mapping for Oblivion foot enums; creature projects pass
# the slot-aware map from creature_pipeline.foot_enum_map instead.
_QUAD_ENUM_MAP = {'left': 'FootFront', 'right': 'FootFront',
                  'backleft': 'FootBack', 'backright': 'FootBack'}


def _classify_key(t: float, k: str, enum_map: dict,
                  sounds: list, feet: list, hits: list) -> None:
    """Route one text key onto the sound, footfall or hit list."""
    low = k.lower()
    if low.startswith('sound:'):
        edid = k.split(':', 1)[1].strip()
        if edid:
            sounds.append((t, edid))
    elif low.startswith('enum:'):
        tag = enum_map.get(low.split(':', 1)[1].replace(' ', ''))
        if tag:
            feet.append((t, tag))
    elif low == 'hit':
        hits.append(t)


def parse_kf_events(text_keys, enum_map=None) -> dict:
    """TES4 .kf text keys → {'sounds': [(t, SOUN EDID)], 'feet': [(t, event)],
    'hits': [t]}.

    'Sound: <EDID>' becomes a SoundPlay.<SNDR EDID> event (event_annotations);
    'Enum: Left/Right/BackLeft/BackRight' are the authored footfalls, mapped
    through enum_map and preferred over synthesis; 'Hit' is the damage frame.
    Anything else is TES4-internal and dropped.  ONE FO3/FNV value holds
    SEVERAL newline-separated keys, so each value is split before classifying.
    See: docs/commentary/asset_convert_falloutnv.md#multi-line-text-keys
    """
    enum_map = enum_map or _QUAD_ENUM_MAP
    sounds, feet, hits = [], [], []
    for t, s in text_keys:
        for k in s.splitlines():
            _classify_key(float(t), k.strip(), enum_map, sounds, feet, hits)
    return {'sounds': sorted(sounds), 'feet': sorted(feet),
            'hits': sorted(hits)}


def event_annotations(events: dict, include_hits: bool = True) -> list:
    """parse_kf_events()-shaped dict → [(t, text)] hkx annotations.

    Sound events MUST be the fully-qualified `SoundPlay.<SNDR EditorID>` —
    a bare `SoundPlay` is measured and discarded by the engine (handler
    0x140565c90, GOG build). include_hits=False for creature-project clips:
    their behavior graph already fires the weaponSwing/preHitFrame/HitFrame
    triple through hkbClipTriggerArray (proven live — attack states return
    to default via those triggers), and firing it from both channels would
    double the damage window and the swing sound.
    """
    out = [(t, f'SoundPlay.TES4_{edid}_SNDR')
           for t, edid in events.get('sounds', []) if edid]
    out += [(t, name) for t, name in events.get('feet', [])]
    if include_hits:
        for t in events.get('hits', []):
            out += [(max(0.0, t - 0.3), 'weaponSwing'),
                    (max(0.0, t - 0.1), 'preHitFrame'),
                    (t, 'HitFrame')]
    return sorted(out)


def ensure_weapon_swing(anim: AnimationData, clip: DecodedClip) -> None:
    """Give an ATTACK clip the weaponSwing annotation Skyrim expects.

    Skyrim reads the swing moment (swoosh sound, weapon-hit registration
    window) off a `weaponSwing` anim event; every vanilla attack animation
    carries one and the CK logs "Animation 'X' on race 'X' has no
    weaponSwing/weaponLeftSwing event" per race x clip without it (3,231
    warnings). Oblivion marks the same moment with its `hit` text key, so the
    swing goes there; a keyless clip gets it at 40% — about where vanilla
    one-shot attacks put it. (weaponLeftSwing is deliberately never emitted:
    Oblivion has no left-hand attack animations.)
    """
    if any(a.text.lower() == 'weaponswing' for a in anim.annotations):
        return
    hits = [float(t) for t, s in clip.text_keys if s.strip().lower() == 'hit']
    swing = hits[0] if hits else float(clip.duration) * 0.4
    anim.annotations.append(Annotation(time=swing, text='weaponSwing'))
    anim.annotations.sort(key=lambda a: a.time)


def reference_pose_from_bones(bones) -> dict:
    """hkx_skeleton.Bone list → {name: (trans, quat_wxyz, scale)}."""
    pose = {}
    for b in bones:
        x, y, z, w = b.quat_xyzw
        pose[b.name] = (b.translation, (w, x, y, z), b.scale)
    return pose


def _annotation_tracks(anim: 'AnimationData', track_names=None) -> str:
    """The annotationTracks body: every annotation on track 0, as vanilla files carry them.

    Track names are empty (a plain clip binds by index) unless `track_names`
    is given: a paired clip binds each actor's tracks BY NAME.
    """
    names = list(track_names or [''] * anim.num_tracks)
    body = []
    for i, name in enumerate(names):
        anns = anim.annotations if i == 0 else []
        inner = '\n'.join(
            '\t\t<hkobject>\n'
            f'\t\t\t<hkparam name="time">{a.time:.6f}</hkparam>\n'
            f'\t\t\t<hkparam name="text">{esc(a.text)}</hkparam>\n'
            '\t\t</hkobject>' for a in anns)
        body.append(
            f'<hkobject>\n\t<hkparam name="trackName">{esc(name)}</hkparam>\n'
            f'\t<hkparam name="annotations" numelements="{len(anns)}">'
            + (f'\n{inner}\n\t' if anns else '') + '</hkparam>\n</hkobject>')
    return '\n'.join(body)


def build_animation_xml(anim: 'AnimationData', skeleton_root: str,
                        track_names=None) -> str:
    """Render the clip as hk_2010 packfile XML (vanilla walkforward layout).

    `track_names` names every track (paired clips; see _annotation_tracks)
    and adds the per-block track offsets a by-name sampler indexes into.

    hkxcmd's Havok serializer (`compile_hkx`) writes the binary: pynifly's own
    writer crashed real deserializers, so only its spline COMPRESSOR is used.
    See: docs/commentary/asset_convert_creature.md#rideable-mounts
    """
    from external.pynifly_hkx.anim_fo4 import _compress_all_blocks

    if not anim.num_blocks:
        max_fpb = anim.max_frames_per_block or 256
        anim.num_blocks = max(1, (anim.num_frames + max_fpb - 1) // max_fpb)
    track_offsets = [] if track_names else None
    blob, block_offsets = _compress_all_blocks(anim, rot_quant=1,
                                               track_offsets=track_offsets)

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
    spline.param_raw('annotationTracks', _annotation_tracks(anim, track_names),
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
    spline.param_array('transformOffsets', track_offsets or [])
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


def decode_clip(ob_kf_path: str, fps: float = 30.0,
                extract_motion: bool = True):
    """Decode an Oblivion .kf. Returns (DecodedClip, motion_or_None)."""
    clips = decode_kf(ob_kf_path, fps)
    if not clips:
        raise ValueError(f'no NiControllerSequence in {ob_kf_path}')
    clip = clips[0]
    motion = split_root_motion(clip) if extract_motion else None
    return clip, motion


def slice_clip(clip, t0: float, t1: float, name: str = None,
               hold: bool = False):
    """A new DecodedClip covering [t0, t1] of `clip`.

    Sampling is uniform, so the cut is an exact index slice — no resampling
    and no interpolation error. Text keys are carried across with their times
    rebased to the slice; keys outside it are dropped.

    hold=True produces a STATIC one-pose clip from the sample at t0 (used for
    the magic Loop, which must hold the charged pose while the engine decides
    whether to release). A held clip still needs >= 2 samples so the spline
    writer has a segment to encode, so the pose is duplicated.
    """
    import numpy as np
    from asset_convert.havok.kf_decode import BoneTrack, DecodedClip

    times = clip.times
    if times is None or len(times) == 0:
        raise ValueError('clip has no samples to slice')
    t0 = max(float(t0), float(times[0]))
    t1 = min(float(t1), float(times[-1]))
    if t1 <= t0:
        # degenerate window — fall back to a hold at t0
        hold = True
    i0 = int(np.searchsorted(times, t0, side='left'))
    i1 = int(np.searchsorted(times, t1, side='right'))
    i0 = min(max(i0, 0), len(times) - 1)
    i1 = max(min(i1, len(times)), i0 + 1)

    if hold:
        new_times = np.array([0.0, float(t1 - t0) or (1.0 / 30.0)],
                             dtype=times.dtype)
        sel = slice(i0, i0 + 1)
    else:
        new_times = np.array(times[i0:i1], dtype=times.dtype) - times[i0]
        sel = slice(i0, i1)

    tracks = []
    for tr in clip.tracks:
        def _cut(a):
            if a is None:
                return None
            part = a[sel]
            if hold:
                part = np.concatenate([part, part], axis=0)
            return np.array(part)
        tracks.append(BoneTrack(bone=tr.bone,
                                translations=_cut(tr.translations),
                                rotations=_cut(tr.rotations),
                                scales=_cut(tr.scales)))

    keys = [] if hold else [(float(t) - float(times[i0]), s)
                            for t, s in (clip.text_keys or [])
                            if t0 <= t <= t1]
    return DecodedClip(
        name=name or f'{clip.name}_slice',
        duration=float(new_times[-1] - new_times[0]),
        cycle_type=clip.cycle_type,
        frequency=clip.frequency,
        times=new_times,
        tracks=tracks,
        text_keys=keys,
        skipped_blocks=list(clip.skipped_blocks or []))


# Vanilla FireForget cast timing, measured from the real SSE atronachflame
# animations (Mag_FF_RH_In 0.800s / _Loop 1.000s / _Out 1.267s, all 44 tracks
# — read straight out of the hkaSplineCompressedAnimation headers). The Loop
# is exactly 1.0s and is a true hold: the engine parks there while it decides
# whether to commit the cast.
CAST_LOOP_SECONDS = 1.0

# How much of the release GESTURE precedes the release moment inside the Out
# clip. Vanilla's Mag_FF_RH_Out fires MLh_SpellFire_Event at 0.233s INTO the
# clip (animationdata atronachflame.txt: `MLh_SpellFire_Event:0.233334`), so
# the Out is not "release at frame 0" — it opens with a short wind-up and the
# spell leaves partway in. The In/Loop hold the CHARGED pose (pre-gesture).
CAST_PRE_RELEASE = 0.25


def split_cast_clip(clip, hit_time: float = None):
    """One Oblivion cast .kf -> (In, Loop, Out, release_offset).

    Oblivion authors a cast as ONE clip; Skyrim expresses it as three states
    (charge / hold / release): the engine enters the chain by sending
    Spell_FireForget_LH/RH, the In charges into the hold, the Loop parks on
    the charged pose until the engine commits (Spell_Release), and the Out
    plays the release gesture — its MLh_SpellFire_Event trigger at
    `release_offset` is what actually fires the spell (the exact analogue of
    HitFrame for a melee swing; vanilla layout verbatim).

    The release moment is the clip's authored 'Hit' text key — the same key
    melee clips use for HitFrame, present in all 69 Oblivion cast clips
    (measured) — so nothing here is a guessed fraction. The cut point sits
    CAST_PRE_RELEASE before it (vanilla's Out opens with ~0.23s of wind-up),
    so the Loop holds the charged pose and the Out carries the throw.
    """
    dur = float(clip.duration)
    if hit_time is None:
        hits = [t for t, v in (clip.text_keys or [])
                if v.strip().lower() == 'hit']
        hit_time = hits[0] if hits else dur * 0.4
    hit_time = min(max(float(hit_time), 0.0), dur)
    cut = max(0.0, hit_time - CAST_PRE_RELEASE)
    release_offset = hit_time - cut

    in_clip = slice_clip(clip, 0.0, cut, f'{clip.name}_In')
    # hold the CHARGED pose — the last frame before the release gesture
    loop_clip = slice_clip(clip, cut, cut + CAST_LOOP_SECONDS,
                           f'{clip.name}_Loop', hold=True)
    loop_clip.duration = CAST_LOOP_SECONDS
    loop_clip.times = loop_clip.times * 0 + np.array(
        [0.0, CAST_LOOP_SECONDS], dtype=loop_clip.times.dtype)
    out_clip = slice_clip(clip, cut, dur, f'{clip.name}_Out')
    return in_clip, loop_clip, out_clip, release_offset


def _resample_track(times, new_times, arr, is_quat: bool):
    """Linear (translations/scales) or sign-continuous nlerp (quaternions)
    resampling of one channel onto `new_times`."""
    if arr is None:
        return None
    a = np.asarray(arr, dtype=np.float64)
    flat = a.ndim == 1
    if flat:
        a = a[:, None]
    if is_quat:
        # keep neighbouring quaternions in the same hemisphere so the
        # interpolation never takes the long way round
        a = a.copy()
        for i in range(1, len(a)):
            if np.dot(a[i], a[i - 1]) < 0:
                a[i] = -a[i]
    out = np.empty((len(new_times), a.shape[1]), dtype=a.dtype)
    for c in range(a.shape[1]):
        out[:, c] = np.interp(new_times, times, a[:, c])
    if is_quat:
        n = np.linalg.norm(out, axis=1)
        n[n == 0] = 1.0
        out /= n[:, None]
    if flat:
        out = out[:, 0]
    return out.astype(np.asarray(arr).dtype)


#: Speed-bake resample floor. See: docs/commentary/asset_convert_creature.md#8-ground-speed-baked-not
MIN_BAKED_FRAMES = 15


def speed_bake_targets(clips: dict, attr_speed: int) -> dict:
    """{clip stem: (target speed u/s, legacy cap)} for the two gait clips.

    Oblivion drove ground speed from the CREA Speed attribute via the GMST
    formula, ignoring root motion, so the target is that formula's speed.
    """
    if not attr_speed:
        return {}
    f_walk = 5.0 + (300.0 - 5.0) * attr_speed / 100.0
    out = {}
    for kf_path, formula, cap in (
            (clips['locomotion'].get('MoveForward'), f_walk, 1.4),
            (clips.get('run'), f_walk * 3.0, 2.0)):
        if kf_path:
            out[clip_state_name(kf_path)] = (formula, cap)
    return out


def speed_bake_factor(rule, clip, motion, fps: float = 30.0) -> float:
    """Timescale factor baking `rule`'s target speed into one gait clip.

    Returns >= 1.0, bounded so the resampled clip keeps at least
    MIN_BAKED_FRAMES frames or the legacy cap, whichever is LARGER — the
    frame floor may only ever raise a factor, never lower one.
    See: docs/commentary/asset_convert_creature.md#8-ground-speed-baked-not
    """
    if not rule or motion is None or motion.get('translations') is None:
        return 1.0
    formula, cap = rule
    end = motion['translations'][-1]
    dur = float(clip.duration) or 1.0
    natural = float(end[0] ** 2 + end[1] ** 2) ** 0.5 / dur
    if natural <= 1.0:
        return 1.0
    cap = max(dur * fps / (MIN_BAKED_FRAMES - 1), cap)
    return min(max(formula / natural, 1.0), cap)


def timescale_clip(clip, motion, factor: float, fps: float = 30.0):
    """Play `clip` `factor`x faster, IN PLACE (bakes Oblivion's attribute ground speed).

    A factor at or below 1 leaves the clip as it is.
    See: docs/commentary/asset_convert_creature.md#8-ground-speed-baked-not
    """
    if factor <= 1.0 + 1e-6:
        return clip, motion
    return retime_clip(clip, motion, float(clip.times[-1]) / float(factor), fps)


def retime_clip(clip, motion, duration: float, fps: float = 30.0):
    """Stretch `clip` IN PLACE to last `duration`, resampled onto the 1/fps grid.

    The duration snaps to whole frames, so frameDuration is exactly 1/fps as
    in every vanilla file (a denser grid broke the lion's run).
    See: docs/commentary/asset_convert_creature.md#8-ground-speed-baked-not
    """
    src_times = np.asarray(clip.times, dtype=np.float64)
    src_dur = float(src_times[-1])
    n = max(2, int(round(duration * fps)) + 1)
    new_dur = (n - 1) / fps
    inv = new_dur / src_dur
    old_times = src_times * inv
    new_times = np.linspace(0.0, new_dur, n)
    from asset_convert.havok.kf_decode import BoneTrack
    tracks = []
    for tr in clip.tracks:
        tracks.append(BoneTrack(
            bone=tr.bone,
            translations=_resample_track(old_times, new_times,
                                         tr.translations, False),
            rotations=_resample_track(old_times, new_times,
                                      tr.rotations, True),
            scales=_resample_track(old_times, new_times, tr.scales, False)))
    clip.tracks = tracks
    clip.times = new_times.astype(np.asarray(clip.times).dtype)
    clip.duration = new_dur
    clip.text_keys = [(float(t) * inv, s) for t, s in (clip.text_keys or [])]
    if motion is not None and motion.get('times') is not None:
        motion = dict(motion)
        motion['times'] = np.asarray(motion['times']) * inv
    return clip, motion




def write_clip_hkx(clip: DecodedClip, bones, out_hkx: str,
                   annotations=(), is_attack: bool = False,
                   keep_xml: bool = False):
    """Compile a decoded clip to Skyrim LE .hkx (XML → hkxcmd serializer).

    bones: hkx_skeleton.Bone list of the creature's generated skeleton.
    annotations: [(t, text)] translated Skyrim events (see
    clip_to_animation_data).
    is_attack: attack clips get the weaponSwing annotation Skyrim expects
    (see ensure_weapon_swing) unless the annotation list already has one.
    """
    from asset_convert.havok.hkx_xml import compile_hkx

    bone_names = [b.name for b in bones]
    anim = clip_to_animation_data(clip, bone_names,
                                  reference_pose_from_bones(bones),
                                  annotations)
    if is_attack:
        ensure_weapon_swing(anim, clip)
    xml = build_animation_xml(anim, skeleton_root=bone_names[0])
    xml_path = os.path.splitext(out_hkx)[0] + '.hkx.xml'
    os.makedirs(os.path.dirname(os.path.abspath(out_hkx)), exist_ok=True)
    with open(xml_path, 'w', encoding='ascii', errors='replace',
              newline='\n') as f:
        f.write(xml)
    compile_hkx(xml_path, out_hkx)
    if not keep_xml:
        os.remove(xml_path)


def convert_clip_hkx(ob_kf_path: str, bones, out_hkx: str,
                     fps: float = 30.0, extract_motion: bool = True,
                     keep_xml: bool = False, is_attack: bool = False):
    """One-shot Oblivion .kf → Skyrim LE .hkx (CLI/standalone path).

    Annotations are translated from the kf's own text keys (quadruped foot
    naming). Creature projects instead decode first, fold in the CREA CSDT
    sound slots, and call write_clip_hkx with the full annotation set —
    see hkx_behavior.generate_creature_project.
    Returns (DecodedClip, motion_or_None).
    """
    clip, motion = decode_clip(ob_kf_path, fps, extract_motion)
    annotations = event_annotations(parse_kf_events(clip.text_keys))
    write_clip_hkx(clip, bones, out_hkx, annotations,
                   is_attack=is_attack, keep_xml=keep_xml)
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
    from asset_convert.havok.hkx_skeleton import load_skeleton_bones

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
