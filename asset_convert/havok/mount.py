"""Rideable creatures: the authored mount clips, the saddle bone and the paired clips.

An Oblivion mount authors its own mounting: two clips in the creature's folder
animate the creature AND a whole rider hung under its `ActorParent` node.  The
rider rises into the seat in one (mount) and climbs down in the other
(dismount).  Skyrim plays mounting as one paired clip that both actors' graphs
play; each binds its tracks BY NAME, the rider the plain ones and the mount the
`2_` ones.  The rider's graph is vanilla and shared by every horse, so it always
plays vanilla's file: the creature's own file is vanilla's human tracks verbatim
plus the creature's authored half.

The engine seats a rider on the node NAMED `SaddleBone`, placed here from the
rider's authored seated pose at the end of the mount clip, plus SEAT_TO_SADDLE:
vanilla's SaddleBone minus the vanilla rider's seated root, in mount model space.
See: docs/commentary/asset_convert_creature.md#rideable-mounts
"""

import ntpath
import os

import numpy as np

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat

from asset_convert.havok.hkx_anim import (build_animation_xml,
                                          clip_to_animation_data, decode_clip,
                                          reference_pose_from_bones,
                                          retime_clip)
from asset_convert.havok.hkx_skeleton import (BONE_RENAMES, ROOT_BONE_NAME,
                                              Bone, quat_xyzw_to_mat33)
from asset_convert.havok.hkx_xml import compile_hkx
from asset_convert.havok.ragdoll_math import (bone_worlds, mat_row_to_quat,
                                              quat_to_mat_row)
from asset_convert.nif.nif_flags import NIF_FLAGS
from asset_convert.sources.skyrim_assets import get_asset_bytes
from external.pynifly_hkx.anim_fo4 import AnimationData
from external.pynifly_hkx.anim_skyrim import parse_skyrim_animation

#: The node the engine seats a rider on, looked up by name.
SADDLE_BONE = 'SaddleBone'
#: Oblivion's rider attach node; a skeleton without one is never a mount.
RIDER_PARENT = 'ActorParent'
#: Second-actor track prefix: the graphs' BSSynchronizedClipGenerator SyncAnimPrefix.
PAIRED_PREFIX = '2_'
#: The vanilla paired clip the shared rider graph plays, by role.
VANILLA_PAIRED = {
    'enter': 'meshes\\actors\\sharedkillmoves\\human&horse\\paired_mount.hkx',
    'exit': 'meshes\\actors\\sharedkillmoves\\human&horse\\paired_dismount.hkx',
}
#: Vanilla saddle-over-seat offset (module docstring), measured from Paired_Mount.hkx.
SEAT_TO_SADDLE = np.array((0.0, -5.2, 4.5))
#: Clip-generator name and animation stem of each role's paired clip.
PAIRED_CLIPS = {'enter': ('PairedMount', 'pairedmount'),
                'exit': ('PairedDismount', 'paireddismount')}


# ---------------------------------------------------------------------------
# Finding the authored clips
# ---------------------------------------------------------------------------

def _rider_root(clip, sources):
    """(root track, accumulation track) of the rider `clip` carries, or None.

    The rider is every track naming no bone of the mount's own skeleton
    (`sources`); its root is the one the rig-root rename maps (Oblivion 'Bip01').
    """
    tracks = {t.bone: t for t in clip.tracks if t.bone not in sources}
    for name, track in tracks.items():
        accum = tracks.get(f'{name} NonAccum')
        if (BONE_RENAMES.get(name) == ROOT_BONE_NAME and accum is not None
                and accum.translations is not None):
            return track, accum
    return None


def _kf_files(creature_dir):
    """Every .kf under the creature's folder, idle subfolders included."""
    for dirpath, _dirs, files in sorted(os.walk(creature_dir)):
        for fn in sorted(files):
            if fn.lower().endswith('.kf'):
                yield os.path.join(dirpath, fn)


def find_mount_clips(creature_dir, bones, fps=30.0) -> dict:
    """{'enter': clip, 'exit': clip}: the authored mount and dismount, else {}.

    Only a skeleton with RIDER_PARENT qualifies.  Oblivion's ridden gaits
    carry the seated rider too, so the mount is the clip whose rider RISES
    most and the dismount the one whose rider drops most.
    """
    sources = {b.source for b in bones}
    if RIDER_PARENT not in sources:
        return {}
    rises = []
    for kf in _kf_files(creature_dir):
        try:
            clip, _motion = decode_clip(kf, fps, extract_motion=False)
        except Exception:
            continue
        rider = _rider_root(clip, sources)
        if rider is not None:
            z = rider[1].translations[:, 2]
            rises.append((float(z[-1] - z[0]), kf, clip))
    if not rises:
        return {}
    rises.sort(key=lambda r: (r[0], r[1]))
    if rises[0][0] >= 0 or rises[-1][0] <= 0:
        return {}
    return {'enter': rises[-1][2], 'exit': rises[0][2]}


# ---------------------------------------------------------------------------
# The saddle bone
# ---------------------------------------------------------------------------

def _trans(track, frame, rest):
    """A track's translation at `frame`, else `rest`."""
    if track is None or track.translations is None:
        return tuple(rest)
    return tuple(float(v) for v in track.translations[frame])


def _quat(track, frame, rest):
    """A track's xyzw rotation at `frame` (tracks store wxyz), else `rest`."""
    if track is None or track.rotations is None:
        return tuple(rest)
    w, x, y, z = (float(v) for v in track.rotations[frame])
    return (x, y, z, w)


def _posed(bones, clip, frame):
    """`bones` with every animated local transform taken from `clip` at `frame`."""
    by_source = {t.bone: t for t in clip.tracks}
    return [Bone(b.name, b.parent,
                 _trans(by_source.get(b.source), frame, b.translation),
                 _quat(by_source.get(b.source), frame, b.quat_xyzw),
                 b.scale, b.source) for b in bones]


def _compose(track, frame, parent):
    """Model (rotation, translation) of a rider track under a parent world transform."""
    r = quat_to_mat_row(_quat(track, frame, (0.0, 0.0, 0.0, 1.0)))
    t = np.array(_trans(track, frame, (0.0, 0.0, 0.0)))
    rp, tp = parent
    return r @ rp, t @ rp + tp


def saddle_bone(bones, mount_clip) -> Bone:
    """SaddleBone on RIDER_PARENT's parent, from the rider seated at the mount clip's end.

    Its model rotation is the seated rider root's; its position is the rider's
    seat (the root's accumulation node) plus SEAT_TO_SADDLE.
    """
    frame = len(mount_clip.times) - 1
    worlds = bone_worlds(_posed(bones, mount_clip, frame))
    anchor = next(i for i, b in enumerate(bones) if b.source == RIDER_PARENT)
    root, accum = _rider_root(mount_clip, {b.source for b in bones})
    root_world = _compose(root, frame, worlds[anchor])
    seat = _compose(accum, frame, root_world)[1]
    parent = bones[anchor].parent
    inv = np.linalg.inv(worlds[parent][0])
    local_t = (seat + SEAT_TO_SADDLE - worlds[parent][1]) @ inv
    return Bone(SADDLE_BONE, parent, tuple(float(v) for v in local_t),
                mat_row_to_quat(root_world[0] @ inv), 1.0, SADDLE_BONE)


def add_saddle_node(skeleton_nif: str, saddle: dict) -> bool:
    """Insert the SaddleBone NiNode into a converted skeleton.nif; False without its parent.

    `saddle` is the manifest's {'parent', 'translation', 'quat_xyzw'}.
    """
    data = NifFormat.Data()
    with open(skeleton_nif, 'rb') as f:
        data.read(f)
    nodes = {bytes(b.name).rstrip(b'\x00').decode('latin-1'): b
             for root in data.roots if root is not None
             for b in root.tree() if isinstance(b, NifFormat.NiNode)}
    parent = nodes.get(saddle['parent'])
    if parent is None:
        return False
    node = nodes.get(SADDLE_BONE)
    if node is None:
        node = NifFormat.NiNode()
        node.name = SADDLE_BONE.encode('latin-1')
        node.flags = NIF_FLAGS
        parent.add_child(node)
    rows = quat_xyzw_to_mat33(saddle['quat_xyzw'])
    for i, row in enumerate(rows, 1):
        for j, v in enumerate(row, 1):
            setattr(node.rotation, f'm_{i}{j}', float(v))
    node.translation.x, node.translation.y, node.translation.z = saddle['translation']
    node.scale = 1.0
    with open(skeleton_nif, 'wb') as f:
        data.write(f)
    return True


# ---------------------------------------------------------------------------
# The paired clips
# ---------------------------------------------------------------------------

def _static_identity(track) -> bool:
    """Whether a track holds the identity transform on every frame (a scene placeholder)."""
    t = np.asarray(track.translations, dtype=float)
    r = np.asarray(track.rotations, dtype=float)
    return bool(np.allclose(t, 0.0, atol=1e-3)
                and np.allclose(np.abs(r[:, 3]), 1.0, atol=1e-4))


def _mount_half(clip, bones, vanilla):
    """(bone names, tracks) of the mount's own half, stretched onto `vanilla`'s frames."""
    sources = {b.source for b in bones}
    clip.tracks = [t for t in clip.tracks if t.bone in sources]
    retime_clip(clip, None, float(vanilla.duration))
    names = [b.name for b in bones]
    half = clip_to_animation_data(clip, names, reference_pose_from_bones(bones))
    if half.num_frames != vanilla.num_frames:
        raise ValueError(f'paired clip frames {half.num_frames} != vanilla '
                         f'{vanilla.num_frames}')
    return names, half.tracks


def write_paired_clip(role: str, clip, bones, out_hkx: str) -> float:
    """Write the mount's paired clip for `role`; returns its duration.

    Vanilla's second-actor tracks go, except its static scene placeholders; the
    mount's bones go in as `2_<bone>`.  Annotations stay in the rider's own
    (vanilla) file, so the mount sound plays once.
    """
    raw = get_asset_bytes(VANILLA_PAIRED[role])
    if raw is None:
        raise FileNotFoundError(VANILLA_PAIRED[role])
    vanilla = parse_skyrim_animation(raw)
    kept = [(n, t) for n, t in zip(vanilla.bone_names, vanilla.tracks)
            if not n.startswith(PAIRED_PREFIX) or _static_identity(t)]
    names, tracks = _mount_half(clip, bones, vanilla)
    kept += [(PAIRED_PREFIX + n, t) for n, t in zip(names, tracks)]
    anim = AnimationData()
    anim.duration = float(vanilla.duration)
    anim.num_frames = vanilla.num_frames
    anim.frame_duration = float(vanilla.frame_duration)
    anim.num_tracks = len(kept)
    anim.bone_names = [n for n, _t in kept]
    anim.tracks = [t for _n, t in kept]
    xml_path = os.path.splitext(out_hkx)[0] + '.hkx.xml'
    os.makedirs(os.path.dirname(os.path.abspath(out_hkx)), exist_ok=True)
    with open(xml_path, 'w', encoding='ascii', errors='replace', newline='\n') as f:
        f.write(build_animation_xml(anim, vanilla.original_skeleton_name,
                                    anim.bone_names))
    compile_hkx(xml_path, out_hkx)
    os.remove(xml_path)
    return anim.duration


def shared_preload() -> list:
    """[dir, stem] of each vanilla paired clip, for a mount's preload list as vanilla's horse has.

    See: docs/commentary/asset_convert_creature.md#rideable-mounts
    """
    return [list(ntpath.split(os.path.splitext(p)[0])) for p in sorted(VANILLA_PAIRED.values())]


def paired_clip_meta(role: str, duration: float) -> dict:
    """The clip_meta row registering `role`'s paired clip in animationdata."""
    name, stem = PAIRED_CLIPS[role]
    return {'name': name, 'stem': stem, 'anim': f'Animations\\{stem}.hkx',
            'duration': duration, 'looping': False, 'end_event': None,
            'sounds': [], 'feet': [], 'hits': []}
