"""Put a retargeted limb's joints where the source animation puts its own.

Per frame and chain: each source joint's offset from the source chain root,
taken in a body bone's frame and scaled by that joint's rest distance ratio,
is added to the target chain root; each bone then turns to point at its
child's spot, keeping its twist.  Arms of a different build keep the source's
elbow and claw placement: joints the source never crosses do not cross.

See: skyb_retarget/README.md#arm-reference-pose
"""
import numpy as np

from asset_convert.havok.clip_retarget import Skeleton, mat_to_quat_wxyz, world_positions
from skyb_retarget.leg_ik import aim, locals_at


def _offset(world, root: int, joint: int, ref: int) -> np.ndarray:
    """Joint minus root, in bone `ref`'s frame."""
    return (world[joint][3, :3] - world[root][3, :3]) @ np.linalg.inv(world[ref][:3, :3])


class ReachRig:
    """Index tables, distance ratios and stance offsets for `chains` (root, ..., tip names)."""

    def __init__(self, src: Skeleton, dst: Skeleton, chains, frame_bone: str,
                 stance=None):
        """`frame_bone` carries the offsets; `stance` is (source idle worlds, blends).

        With a stance, a joint's offset is the source's scaled movement away
        from its idle, added to a blend of the target's rest offset (0) and
        the source idle's scaled offset (1); `blends` has one per joint after
        the chain root.
        """
        self.src, self.dst = src, dst
        self.ref = src.index[frame_bone]
        self.chains = [[dst.index[n] for n in names] for names in chains]
        self.scale, self.bias = {}, {}
        for idx in self.chains:
            for n, j in enumerate(idx[1:]):
                rest = _offset(dst.world, idx[0], j, self.ref)
                k = float(np.linalg.norm(rest)
                          / np.linalg.norm(_offset(src.world, idx[0], j, self.ref)))
                self.scale[j], self.bias[j] = k, np.zeros(3)
                if stance is not None:
                    idle = _offset(stance[0], idx[0], j, self.ref) * k
                    self.bias[j] = (1.0 - stance[1][n]) * (rest - idle)

    def target(self, root: int, joint: int, src_world, dst_world) -> np.ndarray:
        """World spot this frame's `joint` should reach."""
        off = _offset(src_world, root, joint, self.ref) * self.scale[joint] + self.bias[joint]
        return dst_world[root][3, :3] + off @ dst_world[self.ref][:3, :3]


def reach_tips(tracks: list, clip, rig: ReachRig) -> None:
    """Rewrite the chain bones' rotations in `tracks` (in place) frame by frame."""
    by_name = {tr.bone: tr for tr in tracks}
    for f in range(len(clip.times)):
        src_world = world_positions(clip, rig.src, f)
        local = locals_at(by_name, rig.dst, f)
        world = rig.dst.fk(local)
        for idx in rig.chains:
            for bone, child in zip(idx, idx[1:]):
                spot = rig.target(idx[0], child, src_world, world)
                world = aim(local, rig.dst, world, bone, child, spot)
                by_name[rig.dst.names[bone]].rotations[f] = mat_to_quat_wxyz(
                    local[bone][:3, :3])
