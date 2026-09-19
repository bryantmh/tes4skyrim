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
    'swimwalkback': 'swimbackward', 'swimidle': 'swimidle',
    'swimturnleft': 'swimturnleft', 'swimturnright': 'swimturnright',
    'hit1': 'recoil', 'knockdown': 'stagger', 'death1': 'death',
    'spellcast': 'casttarget',
}

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


def _chain(first, link: str):
    """Every block on a singly linked NIF chain starting at `first`."""
    block = first
    while block is not None:
        yield block
        block = getattr(block, link, None)


def _text_keys(data) -> list:
    """(time, text) for every text key in the file."""
    out = []
    for block in data.blocks:
        if isinstance(block, NifFormat.NiTextKeyExtraData):
            out.extend((float(k.time), k.value.decode('cp1252', 'replace'))
                       for k in block.text_keys)
    return out


def _tracks(data, root_name: str) -> list:
    """(node name, `_key_arrays`) per animated node, from a model or a .kf.

    The root bone's track is named for its NonAccum child, where its
    transform now lives.
    """
    root = data.roots[0]
    if isinstance(root, NifFormat.NiSequenceStreamHelper):
        names = [e.string_data.decode('cp1252', 'replace')
                 for e in _chain(root.extra_data, 'next_extra_data')
                 if isinstance(e, NifFormat.NiStringExtraData)]
        controllers = [c for c in _chain(root.controller, 'next_controller')
                       if isinstance(c, NifFormat.NiKeyframeController)]
        found = [(n, c.data) for n, c in zip(names, controllers) if c.data]
    else:
        found = []
        for node in root.tree():
            if not isinstance(node, NifFormat.NiNode):
                continue
            for c in _chain(node.controller, 'next_controller'):
                if isinstance(c, NifFormat.NiKeyframeController) and c.data:
                    found.append((get_block_name(node), c.data))
    return [(n + _NONACCUM_SUFFIX if n == root_name else n, _key_arrays(kd))
            for n, kd in found]


def _groups(keys: list) -> tuple:
    """({group: {event: time}}, [(time, cue kind, cue value)]) from the text keys.

    A key may carry several lines; each is `Group: Event` or a cue
    (`SoundGen: Left`, `Sound: SwishL`).
    """
    groups, cues = {}, []
    for time, text in keys:
        for line in text.replace(chr(13), chr(10)).split(chr(10)):
            head, sep, tail = line.partition(':')
            if not sep:
                continue
            head, tail = head.strip().lower(), tail.strip()
            if head in ('soundgen', 'sound'):
                cues.append((time, head, tail))
            else:
                groups.setdefault(head, {}).setdefault(tail.lower(), time)
    return groups, cues


def _clip_range(events: dict):
    """(start, stop, loops) for one group, or None when it has no span.

    A group with a real loop segment ships only that segment, because Skyrim
    loops whole clips; a one-shot ships Start..Stop.
    """
    start, stop = events.get('start'), events.get('stop')
    if start is None or stop is None or stop <= start:
        return None
    loop_start, loop_stop = events.get('loop start'), events.get('loop stop')
    if loop_start is not None and loop_stop is not None and loop_stop > loop_start:
        return loop_start, loop_stop, True
    return start, stop, False


def _interp(times: np.ndarray, channel: tuple) -> np.ndarray:
    """Linear interpolation of a (key times, one value row per key) channel at `times`."""
    key_times, values = channel
    return np.stack([np.interp(times, key_times, values[:, i])
                     for i in range(values.shape[1])], axis=1)


def _channel(keys, row):
    """(key times, `row(value)` per key) of one keyframe channel, or None when it is empty."""
    if not len(keys):
        return None
    return (np.array([float(k.time) for k in keys], dtype=np.float64),
            np.array([row(k.value) for k in keys], dtype=np.float64))


def _key_arrays(kd) -> tuple:
    """(rotation, translation, scale) channels of one node, read once; euler rotations are skipped.

    Quaternions are sign-aligned to their predecessor so interpolation
    takes the short arc.
    """
    rotation = None
    if kd.rotation_type != 4 and kd.num_rotation_keys:
        rotation = _channel(kd.quaternion_keys, lambda v: (v.w, v.x, v.y, v.z))
        quats = rotation[1]
        for i in range(1, len(quats)):
            if np.dot(quats[i], quats[i - 1]) < 0:
                quats[i] = -quats[i]
    return (rotation,
            _channel(kd.translations.keys, lambda v: (v.x, v.y, v.z)),
            _channel(kd.scales.keys, lambda v: (v,)))


def _sample(arrays: tuple, times: np.ndarray) -> BoneTrack:
    """One node's `_key_arrays` sampled at `times`."""
    rotation, translation, scale = arrays
    track = BoneTrack(bone='')
    if rotation is not None:
        rots = _interp(times, rotation)
        norm = np.linalg.norm(rots, axis=1, keepdims=True)
        norm[norm == 0] = 1.0
        track.rotations = rots / norm
    if translation is not None:
        track.translations = _interp(times, translation)
    if scale is not None:
        track.scales = _interp(times, scale)[:, 0]
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
    duration = stop - start
    count = max(int(round(duration * fps)), 1) + 1
    times = start + np.arange(count, dtype=np.float64) / fps
    times[-1] = stop
    clip = DecodedClip(name=stem, duration=duration,
                       cycle_type=CYCLE_LOOP if loops else CYCLE_CLAMP,
                       frequency=1.0, times=times - start,
                       text_keys=_clip_text_keys(events, cues, start, stop))
    for node_name, kd in tracks:
        track = _sample(kd, times)
        track.bone = node_name
        if track.rotations is not None or track.translations is not None \
                or track.scales is not None:
            clip.tracks.append(track)
    return clip


def _strip(data, keep_geometry: bool) -> None:
    """Drop controllers and text keys from every node; optionally the geometry."""
    for block in data.roots[0].tree():
        if isinstance(block, NifFormat.NiObjectNET):
            block.controller = None
            extras = [e for e in _chain(block.extra_data, 'next_extra_data')
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


def split_creature(model_path, out_dir: str, fps: float = DEFAULT_FPS) -> list:
    """Write skeleton.nif, the body NIF and one .kf per group; the stems written."""
    model_path = Path(model_path)
    stem = model_path.stem.lower()
    animated = model_path.with_name(_ANIMATED_PREFIX + model_path.stem + '.kf')
    animated_model = model_path.with_name(_ANIMATED_PREFIX + model_path.name)
    if animated.is_file():
        anim = read_nif(str(animated))
        mesh_source = animated_model if animated_model.is_file() else model_path
    else:
        anim = read_nif(str(model_path))
        mesh_source = model_path
    groups, cues = _groups(_text_keys(anim))

    skeleton = read_nif(str(mesh_source))
    root_name = _insert_nonaccum(skeleton)
    _strip(skeleton, keep_geometry=False)
    _write_nif(os.path.join(out_dir, SKELETON_NIF), skeleton)
    body = read_nif(str(mesh_source))
    _insert_nonaccum(body)
    _strip(body, keep_geometry=True)
    _skin_rigid_parts(body)
    tracks = _tracks(anim, root_name)
    _write_nif(os.path.join(out_dir, stem + '.nif'), body)

    written = []
    for group, events in groups.items():
        span = _clip_range(events)
        if span is None or not tracks:
            continue
        clip_stem = GROUP_STEMS.get(group, ''.join(
            ch for ch in group if ch.isalnum()))
        if not clip_stem or clip_stem in written:
            continue
        clip = _clip(clip_stem, span, tracks, events, cues, fps)
        write_skyrim_kf(clip, os.path.join(out_dir, clip_stem + '.kf'))
        written.append(clip_stem)
    return written


def _sources(rec_dir) -> dict:
    """{source model: folder relative to the meshes root} for every Morrowind CREA."""
    crea_path = os.path.join(str(rec_dir), 'CREA.txt')
    if not os.path.exists(crea_path):
        return {}
    out = {}
    for rec in parse_export_file(crea_path):
        source = (rec.get(SOURCE_KEY) or '').strip()
        model = (rec.get('Model.MODL') or '').strip()
        if source and model:
            out[source] = os.path.dirname(
                model.replace(chr(92) * 2, chr(92)).replace(chr(92), '/'))
    return out


def _up_to_date(model_path: Path, out_dir: str) -> bool:
    """Whether the folder's skeleton postdates the source model, its animation and the code that writes the folder."""
    skeleton = os.path.join(out_dir, SKELETON_NIF)
    if not os.path.isfile(skeleton):
        return False
    stamp = os.path.getmtime(skeleton)
    inputs = [model_path, Path(__file__),
              Path(find_skeleton_root.__code__.co_filename),
              Path(write_skyrim_kf.__code__.co_filename),
              model_path.with_name(_ANIMATED_PREFIX + model_path.stem + '.kf')]
    return all(p.stat().st_mtime <= stamp for p in inputs if p.is_file())


def _split_pool(todo: list, workers: int, log) -> int:
    """Split every (source, folder, model path, out dir) in a process pool; how many failed."""
    failed = 0
    with ProcessPoolExecutor(max_workers=min(workers, len(todo))) as pool:
        futs = {pool.submit(split_creature, path, out_dir): (source, folder)
                for source, folder, path, out_dir in todo}
        for fut in as_completed(futs):
            source, folder = futs[fut]
            try:
                log(f'  {folder}: {len(fut.result())} clips')
            except (OSError, ValueError, AttributeError) as exc:
                failed += 1
                log(f'  [skip] {source}: {type(exc).__name__}: {exc}')
    return failed


def split_creatures(rec_dir, meshes_root, log=print, workers: int = None) -> int:
    """Split every Morrowind creature the export names; how many folders were written.

    Runs at the head of the creature stage so the folders exist when it scans.
    Source meshes resolve through this plugin's mesh tree, its masters', then
    the vanilla Morrowind install; a folder already newer than its inputs is
    kept.
    """
    sources = _sources(rec_dir)
    if not sources:
        return 0
    roots = [Path(meshes_root)] + [Path(d) / 'meshes'
                                   for d in base_plugins.export_dirs(rec_dir)]
    todo, failed, kept = [], 0, 0
    for source, folder in sorted(sources.items()):
        path = resolve_mesh(roots, source, paths.EXPORT)
        out_dir = os.path.join(str(meshes_root), folder)
        if path is None:
            failed += 1
            log(f'  [skip] {source}: not found')
        elif _up_to_date(path, out_dir):
            kept += 1
        else:
            todo.append((source, folder, path, out_dir))
    unsplit = _split_pool(todo, workers or worker_count(), log) if todo else 0
    done, failed = len(todo) - unsplit, failed + unsplit
    log(f'  Morrowind creatures: {done} split, {kept} up to date'
        + (f', {failed} failed' if failed else ''))
    return done
