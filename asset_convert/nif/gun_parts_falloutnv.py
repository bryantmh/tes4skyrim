"""FO3/FNV gun parts: the `##` node tracks of the actor clips become
NiControllerSequences inside the weapon mesh, one per clip stem.

See: docs/commentary/asset_convert_falloutnv.md#gun-parts
"""

import glob
import os
import re

import numpy as np

from asset_convert.havok.gun_anim_falloutnv import classify_stem
from asset_convert.havok.gun_vocabulary_falloutnv import PART_PREFIX
from asset_convert.havok.kf_decode import decode_kf
from asset_convert.havok.kf_writer import KEY_LINEAR, KEY_QUADRATIC
from asset_convert.nif.sequences import transform_manager
from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat

#: NiControllerSequence cycle type CLAMP.
CYCLE_CLAMP = 2
_NODE_NAME_RE = re.compile(rb'##[A-Za-z0-9_:.]{1,40}')
#: `_male` clip folder -> {part node: {clip stems animating it}}, built once per process.
_PART_INDEX = {}
#: kf path -> DecodedClip, per process.
_DECODED = {}


def clip_dir_for(src_path: str) -> str:
    """The FNV `_male` clip folder beside a source mesh path, '' if none."""
    norm = src_path.replace('\\', '/')
    i = norm.lower().rfind('/meshes/')
    if i < 0:
        return ''
    d = os.path.join(norm[:i], 'meshes', 'characters', '_male')
    return d if os.path.isdir(d) else ''


def _part_index(clip_dir: str) -> dict:
    """{part node: {stems}} over every gun clip in the folder (a raw name
    scan; the clips are decoded only for the meshes that hold the node)."""
    if clip_dir in _PART_INDEX:
        return _PART_INDEX[clip_dir]
    index = {}
    for kf in glob.glob(os.path.join(clip_dir, '*.kf')):
        stem = os.path.splitext(os.path.basename(kf))[0].lower()
        c = classify_stem(stem)
        if c is None or c['prefix'].startswith('pa'):
            continue
        with open(kf, 'rb') as f:
            names = set(_NODE_NAME_RE.findall(f.read()))
        for n in names:
            index.setdefault(n.decode('latin-1'), set()).add(stem)
    _PART_INDEX[clip_dir] = index
    return index


def _decoded(kf: str):
    """The decoded first sequence of a kf (cached), None when it has none."""
    if kf not in _DECODED:
        clips = decode_kf(kf)
        _DECODED[kf] = clips[0] if clips else None
    return _DECODED[kf]


def _part_nodes(root) -> dict:
    """{name: NiNode} for the mesh's `##` nodes."""
    return {b.name.decode('latin-1'): b for b in root.tree()
            if isinstance(b, NifFormat.NiNode)
            and b.name.startswith(PART_PREFIX.encode())}


def _interpolator(track, times):
    """A NiTransformInterpolator holding the track's sampled keys."""
    interp = NifFormat.NiTransformInterpolator()
    td = NifFormat.NiTransformData()
    interp.data = td
    interp.scale = float(track.scales[0]) if track.scales is not None else 1.0
    if track.translations is not None:
        interp.translation.x, interp.translation.y, interp.translation.z = (
            float(v) for v in track.translations[0])
        td.translations.interpolation = KEY_LINEAR
        td.translations.num_keys = len(times)
        td.translations.keys.update_size()
        for k, (t, v) in enumerate(zip(times, track.translations)):
            key = td.translations.keys[k]
            key.time = float(t)
            key.value.x, key.value.y, key.value.z = (float(c) for c in v)
    if track.rotations is not None:
        (interp.rotation.w, interp.rotation.x, interp.rotation.y,
         interp.rotation.z) = (float(v) for v in track.rotations[0])
        td.rotation_type = KEY_QUADRATIC
        td.num_rotation_keys = len(times)
        td.quaternion_keys.update_size()
        for k, (t, q) in enumerate(zip(times, track.rotations)):
            qk = td.quaternion_keys[k]
            qk.time = float(t)
            qk.value.w, qk.value.x, qk.value.y, qk.value.z = (float(v) for v in q)
    if track.scales is not None and np.ptp(track.scales) > 1e-6:
        td.scales.interpolation = KEY_LINEAR
        td.scales.num_keys = len(times)
        td.scales.keys.update_size()
        for k, (t, s) in enumerate(zip(times, track.scales)):
            td.scales.keys[k].time = float(t)
            td.scales.keys[k].value = float(s)
    return interp


def _sequence(stem: str, clip, tracks: list, root, manager, controller):
    """One NiControllerSequence named `stem` over the given part tracks."""
    seq = NifFormat.NiControllerSequence()
    seq.name = stem.encode('latin-1')
    seq.start_time = 0.0
    seq.stop_time = float(clip.duration)
    seq.cycle_type = CYCLE_CLAMP
    seq.frequency = 1.0
    seq.weight = 1.0
    seq.manager = manager
    seq.accum_root_name = root.name
    tk = NifFormat.NiTextKeyExtraData()
    tk.num_text_keys = 2
    tk.text_keys.update_size()
    tk.text_keys[0].time, tk.text_keys[0].value = 0.0, b'start'
    tk.text_keys[1].time, tk.text_keys[1].value = float(clip.duration), b'end'
    seq.text_keys = tk
    seq.num_controlled_blocks = len(tracks)
    seq.controlled_blocks.update_size()
    for i, tr in enumerate(tracks):
        cb = seq.controlled_blocks[i]
        cb.node_name = tr.bone.encode('latin-1')
        cb.controller_type = b'NiTransformController'
        cb.priority = 0
        cb.controller = controller
        cb.interpolator = _interpolator(tr, clip.times)
    return seq


def _manager(root, nodes: dict):
    """(manager, controller) on the root moving the part nodes."""
    palette = NifFormat.NiDefaultAVObjectPalette()
    palette.scene = root
    entries = [root] + list(nodes.values())
    palette.num_objs = len(entries)
    palette.objs.update_size()
    for i, av in enumerate(entries):
        palette.objs[i].name = av.name
        palette.objs[i].av_object = av
    return transform_manager(root, palette, list(nodes.values()))


def add_gun_part_sequences(data, src_path: str) -> list:
    """Give a FNV weapon mesh one sequence per actor clip that animates its
    `##` nodes; the sequence carries the clip's stem as its name, which the
    actor clip raises as an event. Returns the names of the sequences added.
    """
    clip_dir = clip_dir_for(src_path)
    if not clip_dir or '/weapons/' not in src_path.replace('\\', '/').lower():
        return []
    added = []
    index = _part_index(clip_dir)
    for root in data.roots:
        if not isinstance(root, NifFormat.NiNode):
            continue
        nodes = _part_nodes(root)
        stems = set().union(*(index.get(n, set()) for n in nodes))
        if not stems:
            continue
        mgr, ctrl = _manager(root, nodes)
        for stem in sorted(stems):
            clip = _decoded(os.path.join(clip_dir, stem + '.kf'))
            tracks = [t for t in (clip.tracks if clip else []) if t.bone in nodes]
            if not tracks:
                continue
            seq = _sequence(stem, clip, tracks, root, mgr, ctrl)
            mgr.num_controller_sequences += 1
            mgr.controller_sequences.update_size()
            mgr.controller_sequences[-1] = seq
            added.append(stem)
    return added
