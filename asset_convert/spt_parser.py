"""SpeedTree .spt binary parser (Oblivion / SpeedTreeCAD 4.x, "__IdvSpt_02_").

Complete structured parse of the .spt section stream, following the
reverse-engineered format documentation in references/spttools-master/FORMAT
(GPL, Juhana Sadeharju 2006-2008).  Every section is consumed; parsing is
strict — an unknown section id raises, so format drift is caught immediately.

The file is a flat stream of  <int32 section_id> <payload>  chunks.  Shape
curves are stored as ASCII "BezierSpline" strings:

    BezierSpline <lo> <hi> <variance>
    {
        <num_points>
        <x y tan_u tan_v tan_weight>      # per control point
    }

The curve maps x in [0,1] (position along the parent stem) to y in [0,1],
then the output value = lo + y*(hi-lo).  Constant parameters have lo == hi.

Level layout (section 1014 gives the count, typically 4):
    level 0            = trunk
    levels 1..n-2      = branch levels
    level n-1          = leaves
Generation params for level N+1 children (first/last/frequency) are stored in
level N's 6010..6012 slots.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


# ---------------------------------------------------------------------------
# Bezier spline
# ---------------------------------------------------------------------------

@dataclass
class BezierSpline:
    lo: float = 0.0
    hi: float = 0.0
    variance: float = 0.0
    points: list = field(default_factory=list)   # (x, y, u, v, w)
    _samples: np.ndarray | None = None

    @classmethod
    def parse(cls, text: str) -> 'BezierSpline':
        toks = text.replace('{', ' ').replace('}', ' ').split()
        assert toks[0] == 'BezierSpline', f'bad spline header: {text[:40]!r}'
        lo, hi, var = (float(t) for t in toks[1:4])
        n = int(toks[4])
        vals = [float(t) for t in toks[5:5 + n * 5]]
        pts = [tuple(vals[i * 5:i * 5 + 5]) for i in range(n)]
        return cls(lo, hi, var, pts)

    def _sample_curve(self) -> np.ndarray:
        """Sample the y(x) curve as a (2, N) array for interpolation."""
        if self._samples is not None:
            return self._samples
        pts = self.points
        if len(pts) < 2:
            xs = np.array([0.0, 1.0])
            ys = np.array([pts[0][1] if pts else 1.0] * 2)
            self._samples = np.vstack([xs, ys])
            return self._samples
        seg_x, seg_y = [], []
        for i in range(len(pts) - 1):
            x0, y0, u0, v0, w0 = pts[i]
            x1, y1, u1, v1, w1 = pts[i + 1]
            p0 = np.array([x0, y0])
            p3 = np.array([x1, y1])
            c0 = p0 + np.array([u0, v0]) * w0
            c1 = p3 - np.array([u1, v1]) * w1
            t = np.linspace(0.0, 1.0, 24)[:, None]
            b = ((1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * c0
                 + 3 * (1 - t) * t ** 2 * c1 + t ** 3 * p3)
            seg_x.append(b[:, 0])
            seg_y.append(b[:, 1])
        xs = np.concatenate(seg_x)
        ys = np.concatenate(seg_y)
        # x must be ascending for np.interp; enforce monotonicity
        order = np.argsort(xs, kind='stable')
        self._samples = np.vstack([xs[order], ys[order]])
        return self._samples

    def eval(self, x) -> np.ndarray | float:
        """Value at position x in [0,1]: lo + curve_y(x) * (hi - lo)."""
        if self.lo == self.hi:
            if np.isscalar(x):
                return self.lo
            return np.full(np.shape(x), self.lo)
        s = self._sample_curve()
        y = np.interp(x, s[0], s[1])
        return self.lo + y * (self.hi - self.lo)

    def eval_var(self, x, rng: np.random.Generator):
        """eval() plus the stored per-instance random variance."""
        v = self.eval(x)
        if self.variance:
            v = v + rng.uniform(-self.variance, self.variance, np.shape(v) or None)
        return v


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------

@dataclass
class LevelParams:
    """Shape parameters for one tree level (trunk / branch level / leaves)."""
    disturbance: BezierSpline = None    # 6000  profile over own length
    gravity: BezierSpline = None        # 6001  over parent position
    flexibility: BezierSpline = None    # 6002
    flex_profile: BezierSpline = None   # 6003
    length: BezierSpline = None         # 6004  (leaves: placement distance)
    radius: BezierSpline = None         # 6005
    radius_profile: BezierSpline = None  # 6006  profile over own length
    start_angle: BezierSpline = None    # 6007
    gravity_profile: BezierSpline = None  # 6017 profile over own length
    cross_segments: int = 3             # 6008
    length_segments: int = 3            # 6009
    child_first: float = 0.0            # 6010  next level generation window
    child_last: float = 1.0             # 6011
    child_freq: float = 0.0             # 6012
    u_tile: float = 1.0                 # 6013
    v_tile: float = 1.0                 # 6014
    u_abs: int = 0                      # 6015
    v_abs: int = 0                      # 6016
    # 15002/15003
    random_v_offset: int = 0
    twist: float = 0.0
    # 16002..16012 flares
    seg_pack: float = 1.0
    flare_count: int = 0
    flare_balance: float = 1.0
    flare_radial_infl: float = 0.0      # azimuthal influence, degrees
    flare_radial_infl_var: float = 0.0
    flare_radial_exp: float = 1.0
    flare_radial_dist: float = 0.0      # extra radius, multiple of base radius
    flare_radial_dist_var: float = 0.0
    flare_length_dist: float = 0.0      # fraction of stem length affected
    flare_length_dist_var: float = 0.0
    flare_length_exp: float = 1.0
    # 26000 roughness/fork/generation-profile group
    rough_amount: float = 0.0           # 26004
    seg_keep_length: float = 1.0        # 26005
    seg_keep_cross: float = 1.0         # 26006
    gen_dist: float = 0.0               # 26007
    gen_depth: int = 0                  # 26008
    fork_enabled: int = 0               # 26009
    fork_bias: float = 0.0              # 26010
    fork_angle: float = 0.0             # 26011
    fork_limit: int = 0                 # 26012
    cross_profile: BezierSpline = None  # 26013
    normal_profile: BezierSpline = None  # 26014
    rough_vert_freq: float = 0.0        # 26015
    rough_horiz_freq: float = 0.0       # 26016
    rough_amount_var: float = 0.0       # 26017
    rough_profile: BezierSpline = None  # 26018
    gen_profile: BezierSpline = None    # 26019 child frequency profile
    seam_bias: BezierSpline = None      # 26020
    gnarl: float = 0.0                  # 26021
    gnarl_profile: BezierSpline = None  # 26022
    rough_unison: int = 0               # 26023


@dataclass
class LeafMap:
    blossom: int = 0                    # 4000
    color: tuple = (1.0, 1.0, 1.0)      # 4001
    orientation_var: float = 0.0        # 4002
    texture: str = ''                   # 4003
    origin: tuple = (0.5, 0.5, 0.0)     # 4004 pivot on the texture (u,v)
    size: tuple = (0.1, 0.1, 0.0)       # 4005 fraction of tree Size
    world_size: tuple = (0.0, 0.0, 0.0)  # 4006 pre-multiplied game units
    unknown7: float = 1.0               # 4007
    # from the 72000 section (parallel per-map groups)
    use_mesh: int = 0                   # 72001
    mesh_index: int = 0                 # 72003
    hang: float = 0.0                   # 72005
    rotate: float = 0.0                 # 72006


@dataclass
class FrondMap:
    texture: str = ''                   # 14002
    unknown3: float = 1.0               # 14003
    size_factor: float = 1.0            # 14004
    min_angle: float = 0.0              # 14005
    max_angle: float = 0.0              # 14006


@dataclass
class Collision:
    kind: str = 'sphere'                # sphere | capsule | box
    values: tuple = ()                  # sphere: x,y,z,r ; capsule: x,y,z,r,len ; box: 6 floats
    angles: tuple = (0.0, 0.0, 0.0)     # 73004-73006 (capsules; order matches)


@dataclass
class SptTree:
    path: str = ''
    version: str = ''
    bark_texture: str = ''              # 2000
    seed: int = 0                       # 2005
    size: float = 100.0                 # 2006 (game units)
    size_variance: float = 0.0          # 2007
    orientation_angle: float = 0.0      # 74002
    num_levels: int = 0                 # 1014
    levels: list = field(default_factory=list)          # LevelParams
    roots_level: LevelParams | None = None               # 40000 section
    roots_depth: int = 0                # 40002
    roots_first: float = 0.0            # 40003
    roots_last: float = 1.0             # 40004
    roots_freq: float = 0.0             # 40005
    leaf_maps: list = field(default_factory=list)        # LeafMap
    frond_maps: list = field(default_factory=list)       # FrondMap
    fronds_enabled: int = 0             # 13007
    fronds_level: int = 0               # 13002
    fronds_num_blades: int = 1          # 13004
    fronds_extruded: int = 0            # 13003
    collisions: list = field(default_factory=list)       # Collision
    floor_enabled: int = 0              # 27002
    floor_value: float = 0.0            # 27003
    floor_level: int = 0                # 27004
    floor_exponent: float = 1.0         # 27005
    floor_bias: float = 0.0             # 27006 (stored as 1.0 - bias)
    composite_map: str = ''             # 20002
    leaf_quads: list = field(default_factory=list)       # 10002: per-leaf-map UV quad (8 floats)
    billboard_quads: list = field(default_factory=list)  # 10003
    frond_quads: list = field(default_factory=list)      # 10004
    leaf_blossom_distance: float = 0.0  # 3000
    leaf_placement_tolerance: float = 0.0  # 3007
    leaf_rock: float = 0.0              # 21000
    leaf_rustle: float = 0.0            # 21001
    wind_level: int = 0                 # 11002
    first_visible_level: int = 0        # 29002
    flare_seed: int = 1                 # 16013
    branch_material: tuple = ()         # 8003 (13 floats)
    leaf_material: tuple = ()           # 8005
    leaf_meshes: list = field(default_factory=list)      # (name, verts, tris)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_MARKERS = {
    1001, 1002, 1003, 1004, 1005, 1008, 1009, 1010, 1011, 1012, 1015,
    7001, 8000, 8001, 9000, 9001, 9005, 9006, 10000, 10001, 11000, 11001,
    12000, 12001, 13000, 13001, 14001, 15000, 15001, 16000, 16001,
    18000, 18001, 19000, 19001, 20000, 20001, 25000, 25001,
    26000, 26001, 27000, 27001, 28000, 28001, 29000, 29001, 30000, 30001,
    40000, 40001, 40006, 40007, 40008, 50000, 50001, 50003,
    60000, 60001, 60002, 60003, 60004, 60005, 60009,
    71000, 71005, 71011, 71014, 71015, 72000, 72004, 73000, 73001, 73003,
    74000, 74001, 75000, 75001,
}

# sections consumed generically: value pattern per section id
_PATTERNS = {
    2001: 'f', 2002: 'b', 2003: 'f', 2004: 'i',
    3000: 'f', 3001: 'i', 3002: 'f', 3003: 'b', 3004: 'f', 3005: 'f',
    3006: 'b', 3007: 'f', 3008: 'i', 3009: 'b', 3010: 'f',
    5000: 'fff', 5001: 'fff', 5002: 'fff', 5003: 'fff', 5004: 'fff',
    5005: 'f', 5006: 'b',
    8002: 'i', 8003: 'f' * 13, 8004: 'i', 8005: 'f' * 13, 8006: 'f',
    8007: 'i', 8008: 'i', 8009: 'f' * 13,
    9002: 'i', 9003: 'f', 9004: 'f', 9007: 'i', 9008: 'f', 9009: 'f',
    9010: 'f', 9011: 'i', 9012: 'f', 9013: 'f', 9014: 'f',
    11002: 'i',
    13005: 's', 13006: 'i', 13008: 'i', 13009: 'i', 13010: 'f',
    13011: 'f', 13012: 'f', 13013: 'f',
    14007: 'i', 14008: 'i',
    16014: 'f',
    18002: 'fff', 18003: 'fff', 18004: 'fff', 18005: 's',
    19002: 'i',
    20002: 's', 20003: 'b', 20004: 'b', 20005: 'f' * 8,
    22000: 'b', 23002: 'f', 23003: 'f',
    25002: 'f', 25003: 'i', 25004: 'i', 25005: 'i', 25006: 'i', 25007: 'b',
    28002: 'b', 28003: 'f', 28004: 'i',
    30002: 'f', 30003: 'f', 30004: 'f', 30005: 'f', 30006: 'f',
    30007: 'f', 30008: 'f', 30009: 'f',
    50004: 'f', 50005: 'f', 50006: 'b', 50007: 'b', 50008: 'f', 50009: 'b',
    50010: 'f', 50011: 'b', 50012: 'b', 50013: 'f', 50014: 'f',
    50015: 'f', 50016: 'f', 50017: 'f', 50018: 'b',
    60006: 's', 60007: 'i', 60008: 'i',
    70002: 's', 70003: 's', 70004: 's', 70005: 's', 70006: 's',
    70007: 's', 70008: 's',
    75002: 'f', 75003: 'f', 75004: 'b', 75005: 'f',
}

# per-level sections (routed to the current LevelParams)
_LEVEL_SPLINES = {6000: 'disturbance', 6001: 'gravity', 6002: 'flexibility',
                  6003: 'flex_profile', 6004: 'length', 6005: 'radius',
                  6006: 'radius_profile', 6007: 'start_angle',
                  6017: 'gravity_profile'}
_LEVEL_FIELDS = {6008: ('cross_segments', 'i'), 6009: ('length_segments', 'i'),
                 6010: ('child_first', 'f'), 6011: ('child_last', 'f'),
                 6012: ('child_freq', 'f'), 6013: ('u_tile', 'f'),
                 6014: ('v_tile', 'f'), 6015: ('u_abs', 'b'),
                 6016: ('v_abs', 'b')}
_FLARE_FIELDS = {16002: ('seg_pack', 'f'), 16003: ('flare_count', 'i'),
                 16004: ('flare_balance', 'f'),
                 16005: ('flare_radial_infl', 'f'),
                 16006: ('flare_radial_infl_var', 'f'),
                 16007: ('flare_radial_exp', 'f'),
                 16008: ('flare_radial_dist', 'f'),
                 16009: ('flare_radial_dist_var', 'f'),
                 16010: ('flare_length_dist', 'f'),
                 16011: ('flare_length_dist_var', 'f'),
                 16012: ('flare_length_exp', 'f')}
_ROUGH_FIELDS = {26004: ('rough_amount', 'f'), 26005: ('seg_keep_length', 'f'),
                 26006: ('seg_keep_cross', 'f'), 26007: ('gen_dist', 'f'),
                 26008: ('gen_depth', 'i'), 26009: ('fork_enabled', 'b'),
                 26010: ('fork_bias', 'f'), 26011: ('fork_angle', 'f'),
                 26012: ('fork_limit', 'i'), 26013: ('cross_profile', 'S'),
                 26014: ('normal_profile', 'S'), 26015: ('rough_vert_freq', 'f'),
                 26016: ('rough_horiz_freq', 'f'),
                 26017: ('rough_amount_var', 'f'), 26018: ('rough_profile', 'S'),
                 26019: ('gen_profile', 'S'), 26020: ('seam_bias', 'S'),
                 26021: ('gnarl', 'f'), 26022: ('gnarl_profile', 'S'),
                 26023: ('rough_unison', 'b')}
_LEAF_FIELDS = {4000: ('blossom', 'b'), 4001: ('color', 'fff'),
                4002: ('orientation_var', 'f'), 4003: ('texture', 's'),
                4004: ('origin', 'fff'), 4005: ('size', 'fff'),
                4006: ('world_size', 'fff'), 4007: ('unknown7', 'f')}
_FROND_FIELDS = {14002: ('texture', 's'), 14003: ('unknown3', 'f'),
                 14004: ('size_factor', 'f'), 14005: ('min_angle', 'f'),
                 14006: ('max_angle', 'f')}
_LEAFMESH_FIELDS = {72001: ('use_mesh', 'b'), 72003: ('mesh_index', 'i'),
                    72005: ('hang', 'f'), 72006: ('rotate', 'f')}


class SptParseError(Exception):
    pass


def parse_spt(path) -> SptTree:
    """Parse a .spt file into an SptTree.  Raises SptParseError on failure."""
    path = Path(path)
    data = path.read_bytes()
    pos = 0

    def ri():
        nonlocal pos
        v = struct.unpack_from('<i', data, pos)[0]
        pos += 4
        return v

    def rf():
        nonlocal pos
        v = struct.unpack_from('<f', data, pos)[0]
        pos += 4
        return v

    def rb():
        nonlocal pos
        v = data[pos]
        pos += 1
        return v

    def rs():
        nonlocal pos
        n = ri()
        if n < 0 or n > 1 << 20:
            raise SptParseError(f'{path.name}: bad string length {n} at {pos - 4}')
        s = data[pos:pos + n].decode('latin-1').rstrip('\x00')
        pos += n
        return s

    def read_pattern(p):
        vals = []
        for c in p:
            if c == 'f':
                vals.append(rf())
            elif c == 'i':
                vals.append(ri())
            elif c == 'b':
                vals.append(rb())
            elif c in ('s', 'S'):
                vals.append(rs())
        return vals[0] if len(vals) == 1 else tuple(vals)

    tree = SptTree(path=str(path))
    cur_level: LevelParams | None = None      # target for 6xxx sections
    level_idx = -1                            # index for 15002/16002/26000 routing
    flare_idx = -1
    twist_idx = -1
    rough_idx = -1
    in_roots = False
    cur_leaf: LeafMap | None = None
    cur_frond: FrondMap | None = None
    leafmesh_idx = -1                         # 72000 per-map group counter
    pending_capsule_angles = 0                # 73000 capsule angle group counter
    # leaf mesh (71000) scratch
    cur_mesh = None

    try:
        while pos < len(data) - 3:
            sid = ri()

            if sid in _MARKERS:
                if sid == 1016:
                    pass
                elif sid == 40000:
                    in_roots = True
                elif sid == 40001:
                    in_roots = False
                elif sid == 14001:
                    cur_frond = None
                elif sid == 73003:
                    pending_capsule_angles += 1
                continue

            # --- header / globals ---
            if sid == 1000:
                tree.version = rs()
            elif sid == 1016:
                continue
            elif sid == 1017:
                cur_level = None
            elif sid == 1006:
                ri()   # leaf map count (implied by 1007 groups)
            elif sid == 1014:
                tree.num_levels = ri()
            elif sid == 1007:
                cur_leaf = LeafMap()
                tree.leaf_maps.append(cur_leaf)
            elif sid == 2000:
                tree.bark_texture = rs()
            elif sid == 2005:
                tree.seed = ri()
            elif sid == 2006:
                tree.size = rf()
            elif sid == 2007:
                tree.size_variance = rf()
            elif sid == 74002:
                tree.orientation_angle = rf()
            elif sid == 16013:
                tree.flare_seed = ri()
            elif sid == 21000:
                tree.leaf_rock = rf()
            elif sid == 21001:
                tree.leaf_rustle = rf()
            elif sid == 29002:
                tree.first_visible_level = ri()

            # --- level splines / fields ---
            elif sid in _LEVEL_SPLINES:
                if sid == 6000 and not in_roots and cur_level is None:
                    # 1016 opens a new level; 6000 is always its first section
                    cur_level = LevelParams()
                    tree.levels.append(cur_level)
                    level_idx += 1
                if in_roots:
                    if tree.roots_level is None:
                        tree.roots_level = LevelParams()
                    tgt = tree.roots_level
                else:
                    tgt = cur_level
                setattr(tgt, _LEVEL_SPLINES[sid], BezierSpline.parse(rs()))
            elif sid in _LEVEL_FIELDS:
                name, p = _LEVEL_FIELDS[sid]
                tgt = tree.roots_level if in_roots else cur_level
                setattr(tgt, name, read_pattern(p))

            # --- 15002/15003 texture twist per level (sequential) ---
            elif sid == 15002:
                twist_idx += 1
                tgt = _level_by_seq(tree, twist_idx)
                tgt.random_v_offset = rb()
            elif sid == 15003:
                tgt = _level_by_seq(tree, twist_idx)
                tgt.twist = rf()

            # --- 16002..16012 flares per level (sequential groups) ---
            elif sid in _FLARE_FIELDS:
                if sid == 16002 and not in_roots:
                    flare_idx += 1
                name, p = _FLARE_FIELDS[sid]
                tgt = tree.roots_level if in_roots else _level_by_seq(tree, flare_idx)
                setattr(tgt, name, read_pattern(p))

            # --- 26000 roughness/fork groups (sequential; 26002 opens) ---
            elif sid == 26002:
                if not in_roots:
                    rough_idx += 1
                continue
            elif sid == 26003:
                continue
            elif sid in _ROUGH_FIELDS:
                name, p = _ROUGH_FIELDS[sid]
                tgt = tree.roots_level if in_roots else _level_by_seq(tree, rough_idx)
                if tgt is not None:
                    if p == 'S':
                        setattr(tgt, name, BezierSpline.parse(rs()))
                    else:
                        setattr(tgt, name, read_pattern(p))
                else:
                    read_pattern('s' if p == 'S' else p)

            # --- leaf maps ---
            elif sid in _LEAF_FIELDS:
                name, p = _LEAF_FIELDS[sid]
                setattr(cur_leaf, name, read_pattern(p))

            # --- 72000 leaf-map extras: 7000 opens a parallel group ---
            elif sid == 7000:
                leafmesh_idx += 1
                continue
            elif sid in _LEAFMESH_FIELDS:
                name, p = _LEAFMESH_FIELDS[sid]
                if 0 <= leafmesh_idx < len(tree.leaf_maps):
                    setattr(tree.leaf_maps[leafmesh_idx], name, read_pattern(p))
                else:
                    read_pattern(p)

            # --- fronds ---
            elif sid == 13002:
                tree.fronds_level = ri()
            elif sid == 13003:
                tree.fronds_extruded = ri()
            elif sid == 13004:
                tree.fronds_num_blades = ri()
            elif sid == 13007:
                tree.fronds_enabled = rb()
            elif sid == 14000:
                cur_frond = FrondMap()
                tree.frond_maps.append(cur_frond)
                continue
            elif sid in _FROND_FIELDS:
                name, p = _FROND_FIELDS[sid]
                setattr(cur_frond, name, read_pattern(p))

            # --- collision ---
            elif sid == 12002:
                tree.collisions.append(Collision('sphere', tuple(rf() for _ in range(4))))
            elif sid == 12003:
                tree.collisions.append(Collision('capsule', tuple(rf() for _ in range(5))))
            elif sid == 12004:
                tree.collisions.append(Collision('box', tuple(rf() for _ in range(6))))
            elif sid == 73002:
                continue
            elif sid in (73004, 73005, 73006):
                # angle groups follow capsule order in 12000
                caps = [c for c in tree.collisions if c.kind == 'capsule']
                v = rf()
                if pending_capsule_angles < len(caps):
                    c = caps[pending_capsule_angles]
                    a = list(c.angles)
                    a[sid - 73004] = v
                    c.angles = tuple(a)

            # --- floor ---
            elif sid == 27002:
                tree.floor_enabled = rb()
            elif sid == 27003:
                tree.floor_value = rf()
            elif sid == 27004:
                tree.floor_level = ri()
            elif sid == 27005:
                tree.floor_exponent = rf()
            elif sid == 27006:
                tree.floor_bias = 1.0 - rf()

            # --- roots generation window ---
            elif sid == 40002:
                tree.roots_depth = ri()
            elif sid == 40003:
                tree.roots_first = rf()
            elif sid == 40004:
                tree.roots_last = rf()
            elif sid == 40005:
                tree.roots_freq = rf()

            # --- leaves globals kept ---
            elif sid == 3000:
                tree.leaf_blossom_distance = rf()
            elif sid == 3007:
                tree.leaf_placement_tolerance = rf()
            elif sid == 11002:
                tree.wind_level = ri()
            elif sid == 8003:
                tree.branch_material = tuple(rf() for _ in range(13))
            elif sid == 8005:
                tree.leaf_material = tuple(rf() for _ in range(13))

            # --- leaf meshes (71000) ---
            elif sid == 71001:
                ri()
            elif sid == 71002:
                cur_mesh = {'name': '', 'verts': [], 'uvs': [], 'tris': []}
                tree.leaf_meshes.append(cur_mesh)
            elif sid == 71003:
                cur_mesh['name'] = rs()
            elif sid == 71004:
                ri()
            elif sid == 71006:
                cur_mesh['verts'].append((rf(), rf(), rf()))
            elif sid in (71007, 71008, 71009):
                rf(); rf(); rf()
            elif sid == 71010:
                cur_mesh['uvs'].append((rf(), rf()))
            elif sid == 71012:
                ri()
            elif sid == 71013:
                cur_mesh['tris'].append(ri())

            # --- composite map quads ---
            elif sid in (10002, 10003, 10004):
                n = ri()
                dst = {10002: tree.leaf_quads, 10003: tree.billboard_quads,
                       10004: tree.frond_quads}[sid]
                for _ in range(n):
                    dst.append(tuple(rf() for _ in range(8)))
            elif sid == 20002:
                tree.composite_map = rs()

            # --- generic consumed sections ---
            elif sid in _PATTERNS:
                read_pattern(_PATTERNS[sid])

            else:
                raise SptParseError(
                    f'{path.name}: unknown section {sid} at offset {pos - 4}')
    except (struct.error, IndexError) as e:
        raise SptParseError(f'{path.name}: truncated read at {pos}: {e}') from e

    if pos != len(data):
        raise SptParseError(f'{path.name}: trailing bytes ({pos}/{len(data)})')
    if tree.num_levels and len(tree.levels) != tree.num_levels:
        raise SptParseError(
            f'{path.name}: level count mismatch ({len(tree.levels)} parsed, '
            f'{tree.num_levels} declared)')
    return tree


def _level_by_seq(tree: SptTree, idx: int) -> LevelParams | None:
    if 0 <= idx < len(tree.levels):
        return tree.levels[idx]
    return None


# ---------------------------------------------------------------------------
# CLI: dump / survey
# ---------------------------------------------------------------------------

def _dump(tree: SptTree):
    print(f'== {tree.path}')
    print(f'  version={tree.version} seed={tree.seed} size={tree.size} '
          f'+-{tree.size_variance} levels={tree.num_levels}')
    print(f'  bark={tree.bark_texture!r}')
    for i, lv in enumerate(tree.levels):
        kind = 'trunk' if i == 0 else ('leaves' if i == len(tree.levels) - 1 else f'branch{i}')
        print(f'  [{i}] {kind}: len=({lv.length.lo:.4g},{lv.length.hi:.4g}) '
              f'rad=({lv.radius.lo:.4g},{lv.radius.hi:.4g}) '
              f'angle=({lv.start_angle.lo:.4g},{lv.start_angle.hi:.4g}) '
              f'grav=({lv.gravity.lo:.4g},{lv.gravity.hi:.4g}) '
              f'dist=({lv.disturbance.lo:.4g},{lv.disturbance.hi:.4g})')
        print(f'       segs={lv.cross_segments}x{lv.length_segments} '
              f'children: first={lv.child_first} last={lv.child_last} '
              f'freq={lv.child_freq} fork={lv.fork_enabled} '
              f'flares={lv.flare_count} gnarl={lv.gnarl}')
    for m in tree.leaf_maps:
        print(f'  leaf: {m.texture!r} world={m.world_size[0]:.3g}x{m.world_size[1]:.3g} '
              f'color=({m.color[0]:.2f},{m.color[1]:.2f},{m.color[2]:.2f}) '
              f'origin=({m.origin[0]:.2f},{m.origin[1]:.2f}) hang={m.hang} rot={m.rotate}')
    for m in tree.frond_maps:
        print(f'  frond: {m.texture!r} size_factor={m.size_factor} '
              f'(enabled={tree.fronds_enabled} level={tree.fronds_level} '
              f'blades={tree.fronds_num_blades})')
    for c in tree.collisions:
        print(f'  coll: {c.kind} {tuple(round(v, 2) for v in c.values)} ang={c.angles}')
    print(f'  floor: on={tree.floor_enabled} val={tree.floor_value} '
          f'level={tree.floor_level}')
    print(f'  roots: freq={tree.roots_freq} fronds_en={tree.fronds_enabled}')


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Parse/dump Oblivion .spt files')
    ap.add_argument('paths', nargs='+', help='.spt files or directories')
    ap.add_argument('--survey', action='store_true',
                    help='aggregate stats over all files instead of full dumps')
    args = ap.parse_args()

    files = []
    for p in args.paths:
        p = Path(p)
        files.extend(sorted(p.rglob('*.spt')) if p.is_dir() else [p])

    ok, failed = 0, []
    fronds_used, meshes_used, boxes = [], [], []
    for f in files:
        try:
            t = parse_spt(f)
            ok += 1
            if not args.survey:
                _dump(t)
            else:
                if t.fronds_enabled:
                    fronds_used.append(f.name)
                if any(m.use_mesh for m in t.leaf_maps):
                    meshes_used.append(f.name)
                if any(c.kind == 'box' for c in t.collisions):
                    boxes.append(f.name)
        except SptParseError as e:
            failed.append(str(e))
    print(f'\nparsed {ok}/{len(files)}')
    for e in failed:
        print(' FAIL', e)
    if args.survey:
        print(f'fronds enabled: {len(fronds_used)} {fronds_used[:10]}')
        print(f'leaf meshes used: {len(meshes_used)} {meshes_used[:10]}')
        print(f'box collisions: {len(boxes)} {boxes[:10]}')
