"""Drive a retargeted creature's legs from the source animation's foot paths.

Per frame and leg: the source foot tip's displacement from its rest spot,
taken in the body bone's frame and scaled level by the legs' length ratio,
is added to the target foot's rest spot.  The leg starts from its own
authored rest shape and a FABRIK solve over hip -> upper -> lower -> tip
moves its joints onto that spot; each bone then turns to its solved segment.
The root motion is scaled by the same ratio (anim._scaled_root), so a
planted foot stays planted.

See: skyb_retarget/README.md#planting-the-feet
"""
import numpy as np

from asset_convert.havok.clip_retarget import (Skeleton, compose,
                                               mat_to_quat_wxyz,
                                               quat_wxyz_to_mat,
                                               rotation_between,
                                               world_positions)

UP = np.array([0.0, 0.0, 1.0])

#: FABRIK passes per leg and frame.
ITERATIONS = 12


def _flat(v) -> np.ndarray:
    """`v` with its world-up component removed."""
    return v - UP * np.dot(v, UP)


def _path(skel: Skeleton, top: int, tip: int) -> list:
    """Bone indices from `top` down to `tip`'s parent."""
    out, i = [], skel.parents[tip]
    while i != top:
        out.append(i)
        i = skel.parents[i]
    return [top] + out[::-1]


def _span(skel: Skeleton, hip: int, tip: int) -> float:
    """Level distance from hip to foot tip at rest."""
    return float(np.linalg.norm(_flat(skel.world[tip][3, :3] - skel.world[hip][3, :3])))


class LegRig:
    """Index tables and rest measures for driving `legs` on `dst`."""

    def __init__(self, src: Skeleton, dst: Skeleton, legs, frame_bone: str):
        """`legs` are (hip, upper, lower, tip) names; `frame_bone` is the body."""
        self.src, self.dst = src, dst
        self.ref = src.index[frame_bone]
        self.legs = []
        for hip, upper, lower, tip in legs:
            idx = tuple(dst.index[n] for n in (hip, upper, lower, tip))
            self.legs.append({'idx': idx, 'path': _path(dst, idx[0], idx[3]),
                              'span': [_span(s, idx[0], idx[3]) for s in (src, dst)],
                              'src_rest': self._body(src.world, idx[3], src.world),
                              'dst_rest': self._body(dst.world, idx[3], dst.world)})
        self.stride = float(np.mean([g['span'][1] / g['span'][0] for g in self.legs]))

    def _body(self, world, i, frame_world) -> np.ndarray:
        """Bone `i`'s position in the body bone's frame of `frame_world`."""
        b = frame_world[self.ref]
        return (world[i][3, :3] - b[3, :3]) @ np.linalg.inv(b[:3, :3])

    def target(self, leg: dict, src_world, dst_world) -> np.ndarray:
        """World spot this frame's foot tip should touch (stride scaled level)."""
        up = UP @ np.linalg.inv(self.src.world[self.ref][:3, :3])
        d = self._body(src_world, leg['idx'][3], src_world) - leg['src_rest']
        along = up * np.dot(d, up)
        local = leg['dst_rest'] + along + (d - along) * self.stride
        b = dst_world[self.ref]
        return local @ b[:3, :3] + b[3, :3]


def fabrik(points: np.ndarray, goal) -> np.ndarray:
    """Joint positions of a chain (root fixed) moved so its end meets `goal`."""
    p = points.copy()
    lengths = np.linalg.norm(np.diff(p, axis=0), axis=1)
    root = p[0].copy()
    for _ in range(ITERATIONS):
        p[-1] = goal
        for i in range(len(p) - 2, -1, -1):
            p[i] = p[i + 1] + _unit(p[i] - p[i + 1]) * lengths[i]
        p[0] = root
        for i in range(1, len(p)):
            p[i] = p[i - 1] + _unit(p[i] - p[i - 1]) * lengths[i - 1]
    return p


def _unit(v) -> np.ndarray:
    """`v` normalized (zero stays zero)."""
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else v


def locals_at(tracks: dict, dst: Skeleton, f: int) -> np.ndarray:
    """Frame `f`'s local matrices (rest where a bone has no track)."""
    local = dst.local.copy()
    for name, tr in tracks.items():
        local[dst.index[name]] = compose(tr.translations[f],
                                         quat_wxyz_to_mat(tr.rotations[f]))
    return local


def aim(local, dst: Skeleton, world, bone: int, child: int, spot) -> np.ndarray:
    """Turn `bone` so `child`'s pivot lies toward `spot`; returns new worlds."""
    pivot = world[bone][3, :3]
    fix = rotation_between(world[child][3, :3] - pivot, spot - pivot, UP)
    p = dst.parents[bone]
    parent_rot = np.eye(3) if p < 0 else world[p][:3, :3]
    local[bone][:3, :3] = world[bone][:3, :3] @ fix @ np.linalg.inv(parent_rot)
    return dst.fk(local)


def _plant(rig: LegRig, leg: dict, local, world, src_world) -> np.ndarray:
    """Rest-shape the leg, solve its joints onto the target, turn each bone to fit."""
    h, u, lo, t = leg['idx']
    for i in leg['path']:
        local[i][:3, :3] = rig.dst.local[i][:3, :3]
    world = rig.dst.fk(local)
    goal = rig.target(leg, src_world, world)
    solved = fabrik(np.array([world[i][3, :3] for i in (h, u, lo, t)]), goal)
    for bone, child, spot in ((h, u, solved[1]), (u, lo, solved[2]), (lo, t, solved[3])):
        world = aim(local, rig.dst, world, bone, child, spot)
    return world


def _leg_length(world, leg: dict) -> float:
    """Summed segment lengths hip -> upper -> lower -> tip."""
    pts = np.array([world[i][3, :3] for i in leg['idx']])
    return float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum())


def fit_stride(rig: LegRig, frames) -> float:
    """Largest stride scale (up to the length ratio) every foot target can reach.

    `frames` is [(source worlds, target worlds)] per frame.
    """
    lo, hi = 0.2, rig.stride
    for _ in range(12):
        mid = (lo + hi) / 2
        rig.stride = mid
        ok = all(np.linalg.norm(rig.target(leg, sw, dw) - dw[leg['idx'][0]][3, :3])
                 <= 0.97 * _leg_length(rig.dst.world, leg)
                 for sw, dw in frames for leg in rig.legs)
        lo, hi = (mid, hi) if ok else (lo, mid)
    rig.stride = lo
    return lo


def plant_feet(tracks: list, clip, rig: LegRig) -> None:
    """Rewrite the leg bones' rotations in `tracks` (in place) frame by frame.

    Fits rig.stride to the clip first (fit_stride).
    """
    by_name = {tr.bone: tr for tr in tracks}
    frames = [(world_positions(clip, rig.src, f),
               rig.dst.fk(locals_at(by_name, rig.dst, f)))
              for f in range(len(clip.times))]
    fit_stride(rig, frames)
    for f, (src_world, world) in enumerate(frames):
        local = locals_at(by_name, rig.dst, f)
        for leg in rig.legs:
            world = _plant(rig, leg, local, world, src_world)
        for leg in rig.legs:
            for i in leg['path']:
                by_name[rig.dst.names[i]].rotations[f] = mat_to_quat_wxyz(
                    local[i][:3, :3])
