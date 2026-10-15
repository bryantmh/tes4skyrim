"""
Split a Morrowind creature NIF into the folder layout the creature pipeline scans.

A Morrowind creature is one NIF (or an `x<name>.nif` + `x<name>.kf` pair)
holding skeleton, skinned body and every animation on a single timeline that
text keys cut into groups. The pipeline wants Oblivion's shape: a folder with
`skeleton.nif`, the body NIF and one `.kf` per clip. Each group is sampled at
the pipeline's frame rate over its range and written through the same KF
writer the pipeline already reads back, so nothing downstream changes.

See: docs/commentary/tes4_export_morrowind.md#creatures
"""

import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat

from asset_convert import paths
from asset_convert.character.morrowind_armor import (shape_normals,
                                                     shape_vertices,
                                                     write_geometry)
from asset_convert.character.prn_skin import (rigid_skin_data,
                                              rigid_skin_instance)
from asset_convert.character.skin_retarget import (get_block_name, m44_to_np,
                                                   write_node_transform)
from asset_convert.havok.hkx_skeleton import find_skeleton_root
from asset_convert.havok.kf_decode import (CYCLE_CLAMP, CYCLE_LOOP, DEFAULT_FPS,
                                           BoneTrack, DecodedClip)
from asset_convert.havok.kf_writer import write_skyrim_kf
from asset_convert.nif.particles_morrowind import LEGACY_EMITTERS
from asset_convert.nif.timeline_morrowind import (chain, clip_range, groups,
                                                   interp, key_arrays,
                                                   text_keys)
from asset_convert.nif.sse_nif import read_nif
from asset_convert.sources import base_plugins
from asset_convert.sources.morrowind_assets import resolve_mesh
from core.worker_budget import worker_count
from tes5_import.base.text_reader import parse_export_file

#: Morrowind animation group -> the Oblivion clip stem the claim tables read.
GROUP_STEMS = {
    'idle': 'idle', 'walkforward': 'forward', 'walkback': 'backward',
    'walkleft': 'left', 'walkright': 'right', 'runforward': 'runforward',
    'runback': 'fastbackward', 'turnleft': 'turnleft', 'turnright': 'turnright',
    'swimwalkforward': 'swimforward', 'swimrunforward': 'swimfastforward',
    'swimwalkback': 'swimbackward', 'swimidle': 'swimidle', 'idleswim': 'swimidle',
    'swimturnleft': 'swimturnleft', 'swimturnright': 'swimturnright',
    'hit1': 'recoil', 'knockdown': 'stagger', 'death1': 'death',
    'spellcast': 'casttarget', 'idlehh': 'handtohandidle',
    'idle1h': 'onehandidle', 'idle2c': 'twohandidle', 'idle2w': 'twohandidle',
}

#: Weapon group -> the clip-stem prefix of its equip, unequip and attack segments.
_STANCE_PREFIXES = {'handtohand': 'handtohand', 'weapononehand': 'onehand',
                    'weapontwohand': 'twohand', 'weapontwowide': 'twohand',
                    'bowandarrow': 'bow'}

#: Segment -> (start event or None for a one-frame hold, stop event, hit event) in a multi-action group.
_SEGMENTS = {
    'equip': ('equip start', 'equip stop', None),
    'unequip': ('unequip start', 'unequip stop', None),
    **{a: (f'{a} start', f'{a} large follow stop', f'{a} hit')
       for a in ('chop', 'slash', 'thrust')},
    'shoot': ('shoot start', 'shoot follow stop', 'shoot release'),
    **{d: (f'{d} start', f'{d} stop', f'{d} release')
       for d in ('self', 'target', 'touch')},
    'blockidle': (None, 'block hit', None),
    'blockhit': ('block hit', 'block stop', None),
}

#: The shared humanoid animation a bipedal creature plays under its own.
BASE_ANIM = 'xbase_anim.kf'

#: TES4 ACBS creature flag: animated from BASE_ANIM, as NPCs are.
_BIPEDAL = 0x01

#: `SoundGen:` cue -> the Oblivion footfall text key.
_FOOTFALLS = {'left': 'Enum: Left', 'right': 'Enum: Right'}

#: Group events that survive as Oblivion text keys.
_EVENTS = {'hit': 'hit'}

#: The record line naming the source creature NIF.
SOURCE_KEY = 'MorrowindModel'

#: Filename of the bones-only NIF the pipeline keys a creature folder on.
SKELETON_NIF = 'skeleton.nif'

#: Prefix of the animated model / animation pair Morrowind prefers when present.
_ANIMATED_PREFIX = 'x'

#: Suffix of the accumulation child the root bone's transform and animation move to.
_NONACCUM_SUFFIX = ' NonAccum'


def _tracks(data, root_name: str) -> list:
    """(node name, `_key_arrays`) per animated node, from a model or a .kf.

    The root bone's track is named for its NonAccum child, where its
    transform now lives.
    """
    root = data.roots[0]
    if isinstance(root, NifFormat.NiSequenceStreamHelper):
        names = [e.string_data.decode('cp1252', 'replace')
                 for e in chain(root.extra_data, 'next_extra_data')
                 if isinstance(e, NifFormat.NiStringExtraData)]
        controllers = [c for c in chain(root.controller, 'next_controller')
                       if isinstance(c, NifFormat.NiKeyframeController)]
        found = [(n, c.data) for n, c in zip(names, controllers) if c.data]
    else:
        found = []
        for node in root.tree():
            if not isinstance(node, NifFormat.NiNode):
                continue
            for c in chain(node.controller, 'next_controller'):
                if isinstance(c, NifFormat.NiKeyframeController) and c.data:
                    found.append((get_block_name(node), c.data))
    return [(n + _NONACCUM_SUFFIX if n == root_name else n, key_arrays(kd))
            for n, kd in found]


def _segment_stem(group: str, segment: str) -> str:
    """The clip stem one segment of a multi-action group becomes, or '' when none."""
    if group == 'spellcast' and segment in ('self', 'target', 'touch'):
        return 'cast' + segment
    if group == 'shield' and segment.startswith('block'):
        return segment
    prefix = _STANCE_PREFIXES.get(group)
    if prefix is None or segment.startswith('block'):
        return ''
    if segment in ('equip', 'unequip'):
        return prefix + segment
    return prefix + 'attack' + segment


def _segment_events(events: dict, start, stop: str, hit) -> dict:
    """A segment's events renamed to the start/stop/hit a single-action group carries."""
    end = events[stop]
    out = {'start': end - 1.0 / DEFAULT_FPS if start is None else events[start],
           'stop': end}
    if hit in events:
        out['hit'] = events[hit]
    return out


def _plan(groups: dict, known_only: bool) -> dict:
    """{clip stem: (span, events)} for every clip the groups yield; the first group per stem wins.

    A multi-action group (weapon stances, spellcast, shield) packs several
    clips on one span; each segment becomes its own clip. `known_only` drops
    groups no claim table reads.
    """
    plan = {}
    for group, events in groups.items():
        stem = GROUP_STEMS.get(group) or (
            '' if known_only else ''.join(ch for ch in group if ch.isalnum()))
        found = [(stem, events)] if stem else []
        for segment, (start, stop, hit) in _SEGMENTS.items():
            seg_stem = _segment_stem(group, segment)
            if seg_stem and stop in events and (start is None or start in events):
                found.append((seg_stem, _segment_events(events, start, stop, hit)))
        for clip_stem, clip_events in found:
            span = clip_range(clip_events)
            # TES3 accepts an Idle that holds one authored instant. Do not
            # change clip_range globally: other consumers require motion.
            if (span is None and clip_stem == 'idle'
                    and clip_events.get('start') is not None
                    and clip_events.get('start') == clip_events.get('stop')):
                span = (clip_events['start'], clip_events['stop'], True)
            if span is not None:
                plan.setdefault(clip_stem, (span, clip_events))
    return plan


def _sample(arrays: tuple, times: np.ndarray) -> BoneTrack:
    """One node's `_key_arrays` sampled at `times`."""
    rotation, translation, scale = arrays
    track = BoneTrack(bone='')
    if rotation is not None:
        rots = interp(times, rotation)
        norm = np.linalg.norm(rots, axis=1, keepdims=True)
        norm[norm == 0] = 1.0
        track.rotations = rots / norm
    if translation is not None:
        track.translations = interp(times, translation)
    if scale is not None:
        track.scales = interp(times, scale)[:, 0]
    return track


def _clip_text_keys(events: dict, cues: list, start: float, stop: float) -> list:
    """The Oblivion text keys of one clip: start, the cues in range, end."""
    keys = [(0.0, 'start')]
    for name, time in events.items():
        if name in _EVENTS and start <= time <= stop:
            keys.append((time - start, _EVENTS[name]))
    for time, kind, value in cues:
        if not start <= time <= stop:
            continue
        if kind == 'sound':
            keys.append((time - start, 'Sound: ' + value))
        elif value.lower() in _FOOTFALLS:
            keys.append((time - start, _FOOTFALLS[value.lower()]))
    keys.append((stop - start, 'end'))
    return sorted(keys)


def _clip(stem: str, span: tuple, tracks: list, events: dict, cues: list,
          fps: float) -> DecodedClip:
    """One group sampled into a DecodedClip the KF writer serialises."""
    start, stop, loops = span
    held = stop == start
    duration = 1.0 / fps if held else stop - start
    count = max(int(round(duration * fps)), 1) + 1
    sample_times = start + np.arange(count, dtype=np.float64) / fps
    sample_times[-1] = stop
    times = sample_times - start
    if held:
        times = np.array([0.0, duration], dtype=np.float64)
        sample_times = np.full(count, start)
    clip = DecodedClip(name=stem, duration=duration,
                       cycle_type=CYCLE_LOOP if loops else CYCLE_CLAMP,
                       frequency=1.0, times=times,
                       text_keys=([(0.0, 'start'), (duration, 'end')] if held else
                                  _clip_text_keys(events, cues, start, stop)))
    for node_name, kd in tracks:
        track = _sample(kd, sample_times)
        track.bone = node_name
        if track.rotations is not None or track.translations is not None \
                or track.scales is not None:
            clip.tracks.append(track)
    return clip


def _strip(data, keep_geometry: bool) -> None:
    """Drop controllers and text keys from every node; optionally the geometry.

    A legacy particle emitter keeps its controller: that is the authored
    emission, not an animation track, and the Morrowind pre-pass reads it to
    build the NiParticleSystem once the file is at a version that can hold one.
    See: docs/commentary/tes4_export_morrowind.md#creature-particle-emitters
    """
    for block in data.roots[0].tree():
        if isinstance(block, NifFormat.NiObjectNET):
            if type(block).__name__ not in LEGACY_EMITTERS:
                block.controller = None
            extras = [e for e in chain(block.extra_data, 'next_extra_data')
                      if not isinstance(e, NifFormat.NiTextKeyExtraData)]
            block.extra_data = extras[0] if extras else None
            for a, b in zip(extras, extras[1:] + [None]):
                a.next_extra_data = b
        if isinstance(block, NifFormat.NiNode) and not keep_geometry:
            keep = [c for c in block.children
                    if c is not None and not isinstance(c, NifFormat.NiTriBasedGeom)]
            block.num_children = len(keep)
            block.children.update_size()
            for i, child in enumerate(keep):
                block.children[i] = child


def _drop_child(parent, child) -> None:
    """Remove `child` from `parent`'s child array."""
    keep = [c for c in parent.children if c is not None and c is not child]
    parent.num_children = len(keep)
    parent.children.update_size()
    for i, node in enumerate(keep):
        parent.children[i] = node


def _insert_nonaccum(data) -> str:
    """Move the root bone's transform and subtree onto a NonAccum child; the root's name.

    The pipeline plays the accum root as identity and keeps a creature's
    heading and height on `Bip01 NonAccum`, as every Oblivion creature
    authors it; Morrowind authors both on `Bip01` itself. Skins bound to
    the root are re-pointed at the child, whose world frame is the old root's.
    See: docs/commentary/tes4_export_morrowind.md#creatures
    """
    root = data.roots[0]
    bone = find_skeleton_root(data)
    accum = NifFormat.NiNode()
    accum.name = bone.name + _NONACCUM_SUFFIX.encode('latin-1')
    accum.flags = bone.flags
    write_node_transform(accum, m44_to_np(bone.get_transform()))
    accum.num_children = bone.num_children
    accum.children.update_size()
    for i in range(bone.num_children):
        accum.children[i] = bone.children[i]
    bone.num_children = 1
    bone.children.update_size()
    bone.children[0] = accum
    write_node_transform(bone, np.eye(4))
    for block in root.tree():
        skin = getattr(block, 'skin_instance', None)
        for i in range(skin.num_bones if skin is not None else 0):
            if skin.bones[i] is bone:
                skin.bones[i] = accum
    return get_block_name(bone)


def _skin_rigid_parts(data) -> int:
    """Skin every unskinned shape under a bone to that bone; how many were skinned.

    Morrowind draws a shape parented to a bone in that bone's frame; the
    creature pipeline knows only skinned and Prn-attached geometry, so the
    chain from the nearest named ancestor is baked into the vertices and the
    shape takes a one-bone identity skin on it, under the root.
    See: docs/commentary/tes4_export_morrowind.md#creatures
    """
    root = data.roots[0]
    nodes = [n for n in root.tree() if isinstance(n, NifFormat.NiNode)]
    parents = {id(c): n for n in nodes for c in n.children if c is not None}
    skinned = 0
    for shape in [b for b in root.tree() if isinstance(b, NifFormat.NiTriBasedGeom)]:
        bone = parents.get(id(shape))
        if shape.skin_instance is not None or shape.data is None \
                or not shape.data.num_vertices or bone is None:
            continue
        while bone is not root and not get_block_name(bone):
            bone = parents[id(bone)]
        if bone is root:
            continue
        full = m44_to_np(shape.get_transform(bone))
        normals = shape_normals(shape)
        write_geometry(shape, shape_vertices(shape) @ full[:3, :3] + full[3, :3],
                       None if normals is None else normals @ full[:3, :3])
        _drop_child(parents[id(shape)], shape)
        shape.skin_instance = rigid_skin_instance(
            root, bone, rigid_skin_data(shape.data), 0, True)
        root.add_child(shape)
        skinned += 1
    return skinned


def _write_nif(path, data) -> None:
    """Write one NIF, creating the folder."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as fh:
        data.write(fh)


def _animation_kf(model_path: Path) -> Path:
    """The `x<name>.kf` animation paired with a model."""
    return model_path.with_name(_ANIMATED_PREFIX + model_path.stem + '.kf')


def _anim_sources(model_path: Path) -> tuple:
    """(animation file, mesh file): the `x` pair when its .kf exists, else the model itself."""
    animated = _animation_kf(model_path)
    if not animated.is_file():
        return model_path, model_path
    animated_model = model_path.with_name(_ANIMATED_PREFIX + model_path.name)
    return animated, animated_model if animated_model.is_file() else model_path


def write_skeleton(mesh_source, out_dir: str) -> tuple:
    """Write a model's bones as `skeleton.nif` with a NonAccum root child; (path, root bone name)."""
    skeleton = read_nif(str(mesh_source))
    root_name = _insert_nonaccum(skeleton)
    _strip(skeleton, keep_geometry=False)
    path = os.path.join(out_dir, SKELETON_NIF)
    _write_nif(path, skeleton)
    return path, root_name


def group_clips(kf_path, root_name: str, wanted, unless: str = '',
                fps: float = DEFAULT_FPS) -> dict:
    """{group: DecodedClip} for each of `wanted` the animation defines; {} when it defines `unless`.

    Every clip is the group's whole Start..Stop span and loops, which is how
    the TES3 engine replays an idle group back to back; a group whose Stop
    is its Start is a held pose and ships as one frame's worth.
    See: docs/commentary/asset_convert_animation.md#morrowind-npc-animation-files
    """
    anim = read_nif(str(kf_path))
    found, cues = groups(text_keys(anim))
    tracks = _tracks(anim, root_name)
    out = {}
    if unless in found:
        return out
    for group in wanted:
        events = found.get(group, {})
        start, stop = events.get('start'), events.get('stop')
        if tracks and start is not None and stop is not None and stop >= start:
            span = (start, max(stop, start + 1.0 / fps), True)
            out[group] = _clip(group, span, tracks, events, cues, fps)
    return out


def split_creature(model_path, out_dir: str, body_name: str, base_anim=None,
                   fps: float = DEFAULT_FPS) -> list:
    """Write skeleton.nif, the body NIF and one .kf per clip; the stems written.

    A bipedal creature passes `base_anim`: its known clips play first and the
    creature's own groups override them, as the TES3 engine layers them.
    See: docs/commentary/tes4_export_morrowind.md#bipedal-creatures
    """
    anim_path, mesh_source = _anim_sources(Path(model_path))
    root_name = write_skeleton(mesh_source, out_dir)[1]
    body = read_nif(str(mesh_source))
    _insert_nonaccum(body)
    _strip(body, keep_geometry=True)
    _skin_rigid_parts(body)
    _write_nif(os.path.join(out_dir, body_name), body)

    layers = [(base_anim, True)] if base_anim else []
    clips = {}
    for path, known_only in layers + [(anim_path, False)]:
        anim = read_nif(str(path))
        found, cues = groups(text_keys(anim))
        tracks = _tracks(anim, root_name)
        if tracks:
            clips.update({stem: (span, events, tracks, cues) for stem, (span, events)
                          in _plan(found, known_only).items()})
    _supply_base_idle(clips, fps)
    for stem, (span, events, tracks, cues) in clips.items():
        clip = _clip(stem, span, tracks, events, cues, fps)
        write_skyrim_kf(clip, os.path.join(out_dir, stem + '.kf'))
    return list(clips)


def _supply_base_idle(clips: dict, fps: float) -> None:
    """Keep a numbered idle's authored loop, or hold the rig's reference pose.

    Numbered one-shots remain separate source clips, never a perpetual gesture.
    Empty tracks deliberately let hkx_anim fill every bone from the skeleton's
    reference pose. A creature with no idle group at all still reports an error.
    """
    if 'idle' in clips or 'swimidle' in clips:
        return
    numbered = sorted((s for s in clips if re.fullmatch(r'idle[1-9][0-9]*', s)),
                      key=lambda s: int(s[4:]))
    if not numbered:
        return
    looping = next((s for s in numbered if clips[s][0][2]), None)
    clips['idle'] = (clips[looping] if looping else
                     ((0.0, 1.0 / fps, True), {}, [], []))


def slashed(path: str) -> str:
    """An exported path with its escaped backslashes folded to forward slashes."""
    return path.replace(chr(92) * 2, chr(92)).replace(chr(92), '/')


def _sources(rec_dir) -> dict:
    """{source model: (folder, body NIF, bipedal)} for every Morrowind CREA.

    The folder is relative to the meshes root; a model is bipedal when any
    record using it is.
    """
    crea_path = os.path.join(str(rec_dir), 'CREA.txt')
    if not os.path.exists(crea_path):
        return {}
    out = {}
    for rec in parse_export_file(crea_path):
        source = (rec.get(SOURCE_KEY) or '').strip()
        model = (rec.get('Model.MODL') or '').strip()
        if source and model:
            biped = bool(int(rec.get('ACBS.Flags') or 0) & _BIPEDAL)
            known = out.get(source, (None, None, False))
            out[source] = (os.path.dirname(slashed(model)),
                           rec.get('NIFZ[0]', '').strip(), biped or known[2])
    return out


def source_dirs(rec_dir) -> set:
    """Lowercase backslashed meshes-relative folders holding a source model."""
    return {os.path.dirname(slashed(s)).lower().replace('/', chr(92))
            for s in _sources(rec_dir)}


def _up_to_date(model_path: Path, out_dir: str, base_anim) -> bool:
    """Whether the folder's skeleton postdates the source model, its animations and the code that writes the folder."""
    skeleton = os.path.join(out_dir, SKELETON_NIF)
    if not os.path.isfile(skeleton):
        return False
    stamp = os.path.getmtime(skeleton)
    inputs = [model_path, Path(__file__),
              Path(find_skeleton_root.__code__.co_filename),
              Path(write_skyrim_kf.__code__.co_filename),
              _animation_kf(model_path)] + ([Path(base_anim)] if base_anim else [])
    return all(p.stat().st_mtime <= stamp for p in inputs if p.is_file())


def _split_pool(todo: list, workers: int, log) -> int:
    """Split every (source, folder, model path, out dir, body, base anim) in a process pool; how many failed."""
    failed = 0
    with ProcessPoolExecutor(max_workers=min(workers, len(todo))) as pool:
        futs = {pool.submit(split_creature, path, out_dir, body, base): (source, folder)
                for source, folder, path, out_dir, body, base in todo}
        for fut in as_completed(futs):
            source, folder = futs[fut]
            try:
                log(f'  {folder}: {len(fut.result())} clips')
            except (OSError, ValueError, AttributeError) as exc:
                failed += 1
                log(f'  [skip] {source}: {type(exc).__name__}: {exc}')
    return failed


def split_creatures(rec_dir, meshes_root, log=print, workers: int = None,
                    names: list = None) -> int:
    """Split every Morrowind creature the export names; how many folders were written.

    Runs at the head of the creature stage so the folders exist when it scans.
    Source meshes resolve through this plugin's mesh tree, its masters', then
    the vanilla Morrowind install; a folder already newer than its inputs is
    kept. An explicit `names` selection rebuilds those sources even if an old
    running converter wrote a newer skeleton after the code was updated.
    """
    sources = _sources(rec_dir)
    if names is not None:
        wanted = {n.lower() for n in names}
        sources = {src: row for src, row in sources.items()
                   if os.path.basename(row[0]).lower() in wanted}
    if not sources:
        return 0
    roots = [Path(meshes_root)] + [Path(d) / 'meshes'
                                   for d in base_plugins.export_dirs(rec_dir)]
    base = None
    if any(biped for _f, _b, biped in sources.values()):
        base = resolve_mesh(roots, BASE_ANIM, paths.EXPORT)
    todo, failed, kept = [], 0, 0
    for source, (folder, body, biped) in sorted(sources.items()):
        path = resolve_mesh(roots, source, paths.EXPORT)
        out_dir = os.path.join(str(meshes_root), folder)
        layered = base if biped else None
        if path is None:
            failed += 1
            log(f'  [skip] {source}: not found')
        elif names is None and _up_to_date(path, out_dir, layered):
            kept += 1
        else:
            todo.append((source, folder, path, out_dir, body, layered))
    unsplit = _split_pool(todo, workers or worker_count(), log) if todo else 0
    done, failed = len(todo) - unsplit, failed + unsplit
    log(f'  Morrowind creatures: {done} split, {kept} up to date'
        + (f', {failed} failed' if failed else ''))
    return done
