"""SpeedTree (.spt) -> Skyrim NIF converter.

Reads Oblivion/SpeedTree v2 .spt binary files and generates Skyrim-compatible
NIF meshes with procedural trunk, branches, and leaf-billboard geometry.

Output NIF structure (matches Skyblivion reference pattern):
  BSLeafAnimNode  "TES5 Skyrim Tree"  flags=14  BSXFlags=130
    NiTriShape  "TES5 Skyrim Tree - Branches"   bark texture + vertex colors
    NiTriShape  "TES5 Skyrim Tree - Leaves"      leaf texture + alpha + vertex colors

Usage (CLI):
    python -m asset_convert.spt_converter <src_dir> <dst_dir>
    python -m asset_convert.spt_converter <src_dir> <dst_dir> --use-skyblivion

The --use-skyblivion flag copies high-quality Skyblivion reference NIFs when
available (disabled by default).
"""

import io
import math
import os
import re
import struct
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

_WORKER_COUNT = max(1, (os.cpu_count() or 4) - 3)

# ---------------------------------------------------------------------------
# PyFFI monkey-patch (must be before NifFormat import)
# ---------------------------------------------------------------------------
from . import pyffi_monkey_patch as _patch  # noqa: F401

try:
    from pyffi.formats.nif import NifFormat
    _PYFFI = True
except ImportError:
    _PYFFI = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NIF_FLAGS = 14
BSX_FLAGS = 130   # 0x82 = Complex + DYNAMIC

# Texture folders under output/oblivion.esm/textures/tes4/trees/
BARK_TEX_DIR = 'textures\\tes4\\trees\\branches\\'
LEAF_TEX_DIR = 'textures\\tes4\\trees\\leaves\\'

# Procedural generation defaults
MIN_TREE_HEIGHT    = 50.0
TRUNK_AZ_SEGMENTS  = 10     # sides of trunk cylinder
TRUNK_Z_SEGMENTS   = 8      # vertical subdivisions
BRANCH_AZ_SEGMENTS = 6      # sides of branch tubes
BRANCH_Z_SEGMENTS  = 4      # length subdivisions per branch
LEAVES_PER_BRANCH  = 14     # leaf billboards per branch tip
LEAF_QUAD_SIZE_FRAC = 0.12  # leaf size as fraction of tree height

# ---------------------------------------------------------------------------
# Skyblivion reference tree lookup (used only with --use-skyblivion)
# ---------------------------------------------------------------------------
_SKYBLIVION_REF_DIR = (
    Path(__file__).parent.parent
    / 'external' / 'Speed Tree Conversion'
    / 'Data' / 'Meshes' / 'Oblivion' / 'Landscape' / 'Trees'
)
_SEASON_CODE_MAP = {'su': 'summer', 'fa': 'fall', 'wi': 'winter', 'sp': 'spring'}
_SEASONS = ('summer', 'fall', 'winter', 'spring')


def _spt_to_skyblivion(spt_stem: str) -> tuple:
    """Map a SPT stem to (skyblivion_stem_without_season, season_or_None).

    Returns (None, None) if no Skyblivion reference is expected.
    """
    s = spt_stem

    m = re.match(r'^D(Bush|Tree)(\d+)(?:Leaves)?$', s, re.IGNORECASE)
    if m:
        return f'dementia{m.group(1).lower()}{m.group(2)}', None

    m = re.match(r'^Mania(Bush|Tree)(\d+)$', s, re.IGNORECASE)
    if m:
        return f'mania{m.group(1).lower()}{m.group(2)}', None

    if s.lower().startswith('tree'):
        core = s[4:]
        season = None
        m = re.search(r'(SU|FA|WI|SP)$', core, re.IGNORECASE)
        if m:
            season = _SEASON_CODE_MAP[m.group(1).lower()]
            core = core[:m.start()]
        if core.lower().endswith('snow'):
            if season is None:
                season = 'winter'
            core = core[:-4]
        m2 = re.match(r'^(.*?)(\d+)$', core)
        if m2:
            base, num = m2.group(1), m2.group(2).zfill(2)
        else:
            base, num = core, '01'
        return 'tree' + base.lower() + num, season

    return None, None


def _find_season_nif(skyblivion_stem: str, season: str):
    if not _SKYBLIVION_REF_DIR.exists():
        return None
    p = _SKYBLIVION_REF_DIR / f'{skyblivion_stem}{season}.nif'
    return p if p.exists() else None


def _copy_nif_remap_textures(src: Path, dst: Path) -> bool:
    if not _PYFFI:
        return False
    data = NifFormat.Data()
    with open(src, 'rb') as f:
        data.read(f)
    _SKY_PFX = 'textures\\oblivion\\landscape\\trees\\'
    _OUR_PFX = 'tes4\\landscape\\trees\\'
    _OB_PFX = 'textures\\oblivion\\'
    for root in data.roots:
        for block in root.tree():
            if not isinstance(block, NifFormat.BSShaderTextureSet):
                continue
            for i in range(getattr(block, 'num_textures', 0)):
                raw = block.textures[i]
                if not (isinstance(raw, bytes) and raw):
                    continue
                tex = raw.decode('latin-1')
                lc = tex.lower()
                if lc.startswith(_SKY_PFX):
                    block.textures[i] = (_OUR_PFX + tex[len(_SKY_PFX):]).encode()
                elif lc.startswith(_OB_PFX):
                    block.textures[i] = ('tes4\\' + tex[len(_OB_PFX):]).encode()
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, 'wb') as f:
        data.write(f)
    return True


# ---------------------------------------------------------------------------
# SPT binary parser
# ---------------------------------------------------------------------------

def parse_spt(path: Path) -> dict:
    """Parse a SpeedTree v2 .spt binary and return extracted parameters."""
    data = path.read_bytes()
    pos = [0]

    def read_int():
        v = struct.unpack_from('<i', data, pos[0])[0]
        pos[0] += 4
        return v

    def read_float():
        v = struct.unpack_from('<f', data, pos[0])[0]
        pos[0] += 4
        return v

    def read_byte():
        v = data[pos[0]]
        pos[0] += 1
        return v

    def read_intstring():
        n = read_int()
        if n < 0 or n > 65536:
            return ''
        s = data[pos[0]:pos[0]+n].decode('latin-1', errors='replace')
        pos[0] += n
        return s

    def skip_intstring():
        n = read_int()
        if 0 <= n <= 65536:
            pos[0] += n

    def basename(p: str) -> str:
        return os.path.basename(p.replace('\\', os.sep).replace('/', os.sep))

    result = {
        'bark_texture': None,
        'leaf_textures': [],
        'leaf_sizes': [],
        'tree_size': 200.0,
        'tree_size_variance': 0.0,
        'collisions': [],
        # Trunk shape params
        'trunk_radius_start': 0.0,  # section 3000
        'trunk_radius_end': 0.0,    # section 3002
        'trunk_length': 0.0,        # section 3004
        'trunk_gravity': 0.0,       # section 3005
        'trunk_segments': 4,        # section 3001
        # Branch params
        'branch_count': 0,          # section 8002
        'branch_angle': 45.0,       # section 9003
        'branch_length': 0.0,       # section 9004
        'branch_gravity': 0.0,      # section 9008
        'branch_start_height': 0.5, # section 9009 (fraction of trunk)
        'branch_radius': 0.0,       # extracted from sections
        'num_tree_levels': 1,       # section 1014
    }

    while pos[0] < len(data) - 4:
        try:
            sid = read_int()
        except Exception:
            break

        # Structural markers (no data)
        if sid in {1001, 1002, 1003, 1004, 1005, 1007, 1008, 1009, 1010,
                   1011, 1012, 1015, 1016, 1017,
                   7000, 7001, 8000, 8001, 9000, 9001,
                   10000, 10001, 11000, 11001, 12000, 12001,
                   13000, 13001, 14000, 14001, 15000, 15001,
                   16000, 16001, 18000, 18001, 19000, 19001,
                   20000, 20001, 25000, 25001, 26000, 26001,
                   26002, 26003, 27000, 27001, 28000, 28001,
                   29000, 29001, 30000, 30001,
                   71000, 71001, 72000, 74000, 74001, 75000, 75001}:
            continue

        try:
            if sid == 1000:
                skip_intstring()
            elif sid == 1006:
                read_int()
            elif sid == 1014:
                result['num_tree_levels'] = read_int()
            elif sid == 2000:
                result['bark_texture'] = basename(read_intstring())
            elif sid in {2001, 2003}:
                read_float()
            elif sid == 2002:
                read_byte()
            elif sid == 2004:
                read_int()
            elif sid == 2005:
                read_int()
            elif sid == 2006:
                result['tree_size'] = read_float()
            elif sid == 2007:
                result['tree_size_variance'] = read_float()
            elif sid == 3000:
                result['trunk_radius_start'] = read_float()
            elif sid == 3002:
                result['trunk_radius_end'] = read_float()
            elif sid == 3004:
                result['trunk_length'] = read_float()
            elif sid == 3005:
                result['trunk_gravity'] = read_float()
            elif sid in {3007, 3010}:
                read_float()
            elif sid == 3001:
                result['trunk_segments'] = max(1, read_int())
            elif sid == 3008:
                read_int()
            elif sid in {3003, 3006, 3009}:
                read_byte()
            elif sid == 4000:
                read_byte()
            elif sid == 4001:
                read_float(); read_float(); read_float()
            elif sid == 4002:
                read_float()
            elif sid == 4003:
                result['leaf_textures'].append(basename(read_intstring()))
            elif sid == 4004:
                read_float(); read_float(); read_float()
            elif sid == 4005:
                w = read_float(); h = read_float(); read_float()
                result['leaf_sizes'].append((abs(w) or 30.0, abs(h) or 30.0))
            elif sid in {4006}:
                read_float(); read_float(); read_float()
            elif sid == 4007:
                read_float()
            elif sid in {5000, 5001, 5002, 5003, 5004}:
                read_float(); read_float(); read_float()
            elif sid == 5005:
                read_float()
            elif sid == 5006:
                read_byte()
            elif sid in {6000, 6001, 6002, 6003, 6004, 6005, 6006, 6007, 6017}:
                skip_intstring()
            elif sid in {6008, 6009}:
                read_int()
            elif sid in {6010, 6011, 6012, 6013, 6014}:
                read_float()
            elif sid in {6015, 6016}:
                read_byte()
            elif sid == 8002:
                result['branch_count'] = max(0, read_int())
            elif sid in {8004, 8007, 8008}:
                read_int()
            elif sid in {8003, 8005, 8009}:
                for _ in range(13):
                    read_float()
            elif sid == 8006:
                read_float()
            elif sid == 9002:
                read_int()
            elif sid == 9003:
                result['branch_angle'] = read_float()
            elif sid == 9004:
                result['branch_length'] = read_float()
            elif sid == 9007:
                read_int()
            elif sid == 9008:
                result['branch_gravity'] = read_float()
            elif sid == 9009:
                result['branch_start_height'] = read_float()
            elif sid in {9010, 9011, 9012, 9013, 9014}:
                read_float()
            elif sid in {9005, 9006}:
                pass
            elif sid == 10002:
                n = read_int()
                for _ in range(n * 8): read_float()
            elif sid in {10003, 10004}:
                n = read_int()
                for _ in range(n * 8): read_float()
            elif sid == 11002:
                read_int()
            elif sid == 12002:
                x = read_float(); y = read_float(); z = read_float()
                r = read_float()
                result['collisions'].append(('sphere', x, y, z, r))
            elif sid == 12003:
                x = read_float(); y = read_float(); z = read_float()
                r = read_float(); l = read_float()
                result['collisions'].append(('capsule', x, y, z, r, l))
            elif sid == 12004:
                for _ in range(6): read_float()
            elif sid in {13002, 13009, 13010, 13011}:
                read_int()
            elif sid in {13003, 13004, 13008}:
                read_int()
            elif sid == 13005:
                skip_intstring()
            elif sid == 13006:
                read_int()
            elif sid == 13007:
                read_byte()
            elif sid in {13012, 13013}:
                read_float()
            elif sid == 14002:
                skip_intstring()
            elif sid in {14003, 14004, 14005, 14006}:
                read_float()
            elif sid in {14007, 14008}:
                read_int()
            elif sid == 15002:
                read_byte()
            elif sid == 15003:
                read_float()
            elif sid == 16002:
                read_float()
            elif sid == 16003:
                read_int()
            elif sid in {16004, 16005, 16006, 16007, 16008, 16009,
                         16010, 16011, 16012}:
                read_float()
            elif sid == 16013:
                read_int()
            elif sid == 16014:
                read_float()
            elif sid in {18002, 18003, 18004}:
                read_float(); read_float(); read_float()
            elif sid == 18005:
                skip_intstring()
            elif sid == 19002:
                read_int()
            elif sid == 20002:
                skip_intstring()
            elif sid in {20003, 20004}:
                read_byte()
            elif sid == 20005:
                for _ in range(8): read_float()
            elif sid in {21000, 21001}:
                read_float()
            elif sid == 22000:
                read_byte()
            elif sid in {23002, 23003}:
                read_float()
            elif sid == 25002:
                read_float()
            elif sid in {25003, 25004, 25005, 25006}:
                read_int()
            elif sid == 25007:
                read_byte()
            elif sid in {26004, 26005, 26006, 26007}:
                read_float()
            elif sid == 26008:
                read_int()
            elif sid == 26009:
                read_byte()
            elif sid in {26010, 26011}:
                read_float()
            elif sid == 26012:
                read_int()
            elif sid in {26013, 26014, 26018, 26019, 26020, 26022}:
                skip_intstring()
            elif sid in {26015, 26016, 26017, 26021}:
                read_float()
            elif sid == 26023:
                read_byte()
            elif sid == 27002:
                read_byte()
            elif sid in {27003, 27005, 27006}:
                read_float()
            elif sid == 27004:
                read_int()
            elif sid == 28002:
                read_byte()
            elif sid == 28003:
                read_float()
            elif sid == 28004:
                read_int()
            elif sid == 29002:
                read_int()
            elif sid in {30002, 30003, 30004}:
                read_float()
            elif sid in {71002, 71003}:
                if sid == 71003: skip_intstring()
            elif sid == 71004:
                read_int()
            elif sid in {71005, 71011, 71014, 71015}:
                pass
            elif sid in {71006, 71007, 71008, 71009}:
                read_float(); read_float(); read_float()
            elif sid == 71010:
                read_float(); read_float()
            elif sid == 71012:
                read_int()
            elif sid == 71013:
                read_int()
            elif sid == 72001:
                read_byte()
            elif sid == 72003:
                read_int()
            elif sid in {72005, 72006}:
                read_float()
            elif sid in {73002, 73003}:
                pass
            elif sid in {73004, 73005, 73006}:
                read_float()
            elif sid == 74002:
                read_float()
            elif sid in {75002, 75003, 75005}:
                read_float()
            elif sid == 75004:
                read_byte()
            else:
                break
        except (struct.error, IndexError):
            break

    return result


# ---------------------------------------------------------------------------
# Procedural geometry generators (numpy-vectorized)
# ---------------------------------------------------------------------------

def _seed_from_name(name: str) -> int:
    """Deterministic seed from filename for reproducibility."""
    h = 0
    for c in name.lower():
        h = (h * 31 + ord(c)) & 0xFFFFFFFF
    return h


def _generate_trunk(height: float, radius_base: float, radius_top: float,
                    n_az: int, n_z: int, rng: np.random.Generator,
                    gravity: float = 0.0):
    """Generate a tapered trunk cylinder with optional lean.

    Returns (verts Nx3, normals Nx3, uvs Nx2, tris Mx3, ring_centers list).
    ring_centers: list of (cx, cy, cz, radius) for each ring — used to attach branches.
    """
    # Slight random lean for organic feel
    lean_x = rng.uniform(-0.03, 0.03) + gravity * 0.02
    lean_y = rng.uniform(-0.03, 0.03)

    ring_centers = []
    verts = []
    norms = []
    uvs = []

    for iz in range(n_z + 1):
        t = iz / n_z
        z = height * t
        r = radius_base + (radius_top - radius_base) * t
        # Apply lean (cumulative offset at each height)
        cx = lean_x * z
        cy = lean_y * z
        ring_centers.append((cx, cy, z, r))

        for ia in range(n_az):
            angle = 2.0 * math.pi * ia / n_az
            # Slight radius variation for organic feel
            r_var = r * (1.0 + rng.uniform(-0.03, 0.03))
            x = cx + r_var * math.cos(angle)
            y = cy + r_var * math.sin(angle)
            verts.append((x, y, z))
            # Normal points outward from cylinder center
            nx = math.cos(angle)
            ny = math.sin(angle)
            norms.append((nx, ny, 0.0))
            uvs.append((ia / n_az, 1.0 - t))

    # Triangulate rings
    tris = []
    for iz in range(n_z):
        for ia in range(n_az):
            ia_next = (ia + 1) % n_az
            i0 = iz * n_az + ia
            i1 = iz * n_az + ia_next
            i2 = (iz + 1) * n_az + ia
            i3 = (iz + 1) * n_az + ia_next
            tris.append((i0, i2, i1))
            tris.append((i1, i2, i3))

    # Bottom cap
    bc = len(verts)
    c0 = ring_centers[0]
    verts.append((c0[0], c0[1], c0[2]))
    norms.append((0.0, 0.0, -1.0))
    uvs.append((0.5, 1.0))
    for ia in range(n_az):
        ia_next = (ia + 1) % n_az
        tris.append((bc, ia_next, ia))

    # Top cap
    tc = len(verts)
    ct = ring_centers[-1]
    verts.append((ct[0], ct[1], ct[2]))
    norms.append((0.0, 0.0, 1.0))
    uvs.append((0.5, 0.0))
    top_start = n_z * n_az
    for ia in range(n_az):
        ia_next = (ia + 1) % n_az
        tris.append((tc, top_start + ia, top_start + ia_next))

    return (np.array(verts, dtype=np.float32),
            np.array(norms, dtype=np.float32),
            np.array(uvs, dtype=np.float32),
            np.array(tris, dtype=np.int32),
            ring_centers)


def _generate_branch(attach_pos, attach_radius, direction, length,
                     n_az: int, n_z: int, rng: np.random.Generator):
    """Generate a single tapered branch tube.

    attach_pos: (x, y, z) where branch meets trunk
    attach_radius: trunk radius at attachment
    direction: (dx, dy, dz) unit vector for branch direction
    length: branch length
    Returns (verts Nx3, normals Nx3, uvs Nx2, tris Mx3, tip_pos (x,y,z)).
    """
    radius_base = attach_radius * 0.35
    radius_tip = radius_base * 0.15

    dx, dy, dz = direction
    # Build a local coordinate system for the branch
    # branch forward = direction, build perpendicular axes
    if abs(dz) < 0.9:
        up = np.array([0.0, 0.0, 1.0])
    else:
        up = np.array([1.0, 0.0, 0.0])
    fwd = np.array([dx, dy, dz])
    right = np.cross(fwd, up)
    right /= (np.linalg.norm(right) + 1e-9)
    up = np.cross(right, fwd)
    up /= (np.linalg.norm(up) + 1e-9)

    ax, ay, az = attach_pos
    verts = []
    norms = []
    uvs = []

    for iz in range(n_z + 1):
        t = iz / n_z
        r = radius_base + (radius_tip - radius_base) * t
        # Position along branch with slight droop (gravity)
        droop = t * t * length * 0.08
        pos = np.array([ax, ay, az]) + fwd * (length * t) + np.array([0, 0, -droop])

        for ia in range(n_az):
            angle = 2.0 * math.pi * ia / n_az
            r_var = r * (1.0 + rng.uniform(-0.04, 0.04))
            offset = right * (r_var * math.cos(angle)) + up * (r_var * math.sin(angle))
            v = pos + offset
            verts.append((float(v[0]), float(v[1]), float(v[2])))
            # Normal perpendicular to branch axis
            n = right * math.cos(angle) + up * math.sin(angle)
            norms.append((float(n[0]), float(n[1]), float(n[2])))
            uvs.append((ia / n_az, 1.0 - t))

    tris = []
    for iz in range(n_z):
        for ia in range(n_az):
            ia_next = (ia + 1) % n_az
            i0 = iz * n_az + ia
            i1 = iz * n_az + ia_next
            i2 = (iz + 1) * n_az + ia
            i3 = (iz + 1) * n_az + ia_next
            tris.append((i0, i2, i1))
            tris.append((i1, i2, i3))

    # Tip cap
    tc = len(verts)
    tip = np.array([ax, ay, az]) + fwd * length + np.array([0, 0, -length * 0.08])
    verts.append((float(tip[0]), float(tip[1]), float(tip[2])))
    norms.append((float(fwd[0]), float(fwd[1]), float(fwd[2])))
    uvs.append((0.5, 0.0))
    top_start = n_z * n_az
    for ia in range(n_az):
        ia_next = (ia + 1) % n_az
        tris.append((tc, top_start + ia, top_start + ia_next))

    return (np.array(verts, dtype=np.float32),
            np.array(norms, dtype=np.float32),
            np.array(uvs, dtype=np.float32),
            np.array(tris, dtype=np.int32),
            (float(tip[0]), float(tip[1]), float(tip[2])))


def _generate_leaf_cluster(center, cluster_radius, leaf_size, n_leaves,
                           rng: np.random.Generator):
    """Generate leaf billboards around a branch tip.

    Each leaf is a small quad oriented semi-randomly outward/upward.
    Returns (verts Nx3, normals Nx3, uvs Nx2, tris Mx3, colors Nx4 uint8).
    """
    cx, cy, cz = center
    half = leaf_size * 0.5

    all_v = []
    all_n = []
    all_uv = []
    all_tri = []
    all_col = []

    for _ in range(n_leaves):
        # Random position in spherical cloud around center
        theta = rng.uniform(0, 2 * math.pi)
        phi = rng.uniform(0.2, math.pi * 0.7)  # bias upward
        r = cluster_radius * rng.uniform(0.3, 1.0)
        px = cx + r * math.sin(phi) * math.cos(theta)
        py = cy + r * math.sin(phi) * math.sin(theta)
        pz = cz + r * math.cos(phi)

        # Leaf orientation: face outward from center with upward bias
        outward = np.array([px - cx, py - cy, pz - cz + 0.3])
        norm_len = np.linalg.norm(outward)
        if norm_len < 0.01:
            outward = np.array([0.0, 0.0, 1.0])
        else:
            outward = outward / norm_len
        face_normal = outward

        # Build a quad perpendicular to face_normal
        if abs(face_normal[2]) < 0.9:
            world_up = np.array([0.0, 0.0, 1.0])
        else:
            world_up = np.array([1.0, 0.0, 0.0])
        right = np.cross(face_normal, world_up)
        right /= (np.linalg.norm(right) + 1e-9)
        up = np.cross(right, face_normal)
        up /= (np.linalg.norm(up) + 1e-9)

        # Slight random rotation for variety
        rot_angle = rng.uniform(0, math.pi * 0.3)
        cos_r, sin_r = math.cos(rot_angle), math.sin(rot_angle)
        right2 = right * cos_r + up * sin_r
        up2 = -right * sin_r + up * cos_r

        # Slight random size variation
        s = half * rng.uniform(0.7, 1.3)
        base_idx = len(all_v)
        pos = np.array([px, py, pz])
        corners = [
            pos - right2 * s - up2 * s,
            pos + right2 * s - up2 * s,
            pos + right2 * s + up2 * s,
            pos - right2 * s + up2 * s,
        ]
        for c in corners:
            all_v.append((float(c[0]), float(c[1]), float(c[2])))
            all_n.append((float(face_normal[0]), float(face_normal[1]),
                          float(face_normal[2])))
        all_uv.extend([(0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)])

        # Two triangles for the quad (double-sided)
        all_tri.append((base_idx, base_idx + 1, base_idx + 2))
        all_tri.append((base_idx, base_idx + 2, base_idx + 3))
        # Backface
        all_tri.append((base_idx, base_idx + 2, base_idx + 1))
        all_tri.append((base_idx, base_idx + 3, base_idx + 2))

        # Vertex color: slight green/yellow variation for natural look
        g_var = rng.integers(200, 255)
        r_var = rng.integers(180, 230)
        for _ in range(4):
            all_col.append((r_var, g_var, rng.integers(160, 200), 255))

    return (np.array(all_v, dtype=np.float32),
            np.array(all_n, dtype=np.float32),
            np.array(all_uv, dtype=np.float32),
            np.array(all_tri, dtype=np.int32),
            np.array(all_col, dtype=np.uint8))


def _generate_tree_geometry(spt_data: dict, spt_name: str):
    """Generate complete tree geometry from SPT parameters.

    Returns dict with 'trunk' and 'leaves' entries, each containing
    (verts, normals, uvs, tris) numpy arrays, plus 'leaf_colors'.
    """
    rng = np.random.default_rng(_seed_from_name(spt_name))

    tree_size = max(spt_data.get('tree_size', 200.0), MIN_TREE_HEIGHT)
    trunk_length_frac = spt_data.get('trunk_length', 0.0)
    if trunk_length_frac <= 0 or trunk_length_frac > 5.0:
        trunk_length_frac = 1.0
    trunk_height = tree_size * trunk_length_frac

    # Trunk radius from SPT or derived from tree size
    r_start = spt_data.get('trunk_radius_start', 0.0)
    r_end = spt_data.get('trunk_radius_end', 0.0)
    if r_start <= 0:
        r_start = tree_size * 0.04  # ~4% of height
    if r_end <= 0:
        r_end = r_start * 0.25
    # SPT radii are in SPT units; scale to game units relative to tree_size
    if r_start < 1.0:
        r_start *= tree_size
    if r_end < 1.0:
        r_end *= tree_size

    gravity = spt_data.get('trunk_gravity', 0.0)

    # ---- Generate trunk ----
    tv, tn, tuv, tt, rings = _generate_trunk(
        trunk_height, r_start, r_end,
        TRUNK_AZ_SEGMENTS, TRUNK_Z_SEGMENTS, rng, gravity
    )

    # Trunk vertex colors (brownish, darker at base)
    trunk_colors = np.zeros((len(tv), 4), dtype=np.uint8)
    for i in range(len(tv)):
        t = tv[i, 2] / trunk_height if trunk_height > 0 else 0.5
        brightness = int(120 + 80 * t)
        trunk_colors[i] = [brightness, int(brightness * 0.85),
                           int(brightness * 0.65), 255]

    # ---- Generate branches ----
    branch_count_spt = spt_data.get('branch_count', 0)
    is_shrub = spt_name.lower().startswith('shrub') or tree_size < 80
    if is_shrub:
        n_branches = max(3, min(branch_count_spt, 8)) if branch_count_spt > 0 else 5
    else:
        n_branches = max(4, min(branch_count_spt, 10)) if branch_count_spt > 0 else 6

    branch_angle_deg = spt_data.get('branch_angle', 45.0)
    if branch_angle_deg <= 0 or branch_angle_deg > 90:
        branch_angle_deg = 50.0
    branch_angle_rad = math.radians(branch_angle_deg)

    branch_len_frac = spt_data.get('branch_length', 0.0)
    if branch_len_frac <= 0 or branch_len_frac > 5.0:
        branch_len_frac = 0.4

    start_height_frac = spt_data.get('branch_start_height', 0.5)
    if start_height_frac <= 0 or start_height_frac > 1.0:
        start_height_frac = 0.4 if is_shrub else 0.5

    all_branch_v = [tv]
    all_branch_n = [tn]
    all_branch_uv = [tuv]
    all_branch_tri = [tt]
    all_branch_col = [trunk_colors]

    all_leaf_v = []
    all_leaf_n = []
    all_leaf_uv = []
    all_leaf_tri = []
    all_leaf_col = []

    # Leaf size
    leaf_sizes = spt_data.get('leaf_sizes', [])
    if leaf_sizes:
        raw_w, raw_h = leaf_sizes[0]
        leaf_size = max(raw_w * 800.0, raw_h * 800.0, tree_size * 0.06)
        leaf_size = min(leaf_size, tree_size * LEAF_QUAD_SIZE_FRAC)
    else:
        leaf_size = tree_size * LEAF_QUAD_SIZE_FRAC

    for bi in range(n_branches):
        # Attachment height: distribute between start_height and 95% of trunk
        t_frac = start_height_frac + (0.95 - start_height_frac) * bi / max(n_branches - 1, 1)
        # Add slight randomness to attachment height
        t_frac = max(0.1, min(0.95, t_frac + rng.uniform(-0.05, 0.05)))

        ring_idx = int(t_frac * (len(rings) - 1))
        ring_idx = min(ring_idx, len(rings) - 1)
        rc = rings[ring_idx]
        attach_pos = (rc[0], rc[1], rc[2])
        attach_r = rc[3]

        # Branch direction: outward from trunk at angle
        azimuth = 2.0 * math.pi * bi / n_branches + rng.uniform(-0.3, 0.3)
        # Elevation angle from horizontal
        elevation = math.pi / 2.0 - branch_angle_rad + rng.uniform(-0.15, 0.15)
        dx = math.cos(azimuth) * math.cos(elevation)
        dy = math.sin(azimuth) * math.cos(elevation)
        dz = math.sin(elevation)

        remaining_height = trunk_height - rc[2]
        branch_len = max(remaining_height * branch_len_frac, tree_size * 0.1)
        branch_len *= rng.uniform(0.75, 1.25)

        bv, bn, buv, bt, tip = _generate_branch(
            attach_pos, attach_r, (dx, dy, dz), branch_len,
            BRANCH_AZ_SEGMENTS, BRANCH_Z_SEGMENTS, rng
        )

        # Offset triangle indices
        offset = sum(len(v) for v in all_branch_v)
        bt_offset = bt + offset
        all_branch_v.append(bv)
        all_branch_n.append(bn)
        all_branch_uv.append(buv)
        all_branch_tri.append(bt_offset)

        # Branch vertex colors
        branch_col = np.zeros((len(bv), 4), dtype=np.uint8)
        for i in range(len(bv)):
            branch_col[i] = [rng.integers(100, 160), rng.integers(80, 130),
                             rng.integers(50, 90), 255]
        all_branch_col.append(branch_col)

        # Generate leaves at branch tip
        cluster_radius = branch_len * 0.5
        n_leaves = LEAVES_PER_BRANCH + rng.integers(-3, 4)
        n_leaves = max(4, n_leaves)

        lv, ln, luv, lt, lc = _generate_leaf_cluster(
            tip, cluster_radius, leaf_size, n_leaves, rng
        )

        leaf_offset = sum(len(v) for v in all_leaf_v)
        lt_offset = lt + leaf_offset
        all_leaf_v.append(lv)
        all_leaf_n.append(ln)
        all_leaf_uv.append(luv)
        all_leaf_tri.append(lt_offset)
        all_leaf_col.append(lc)

    # Also add leaves at the crown (top of trunk) for fuller canopy
    crown_center = rings[-1][:3]
    crown_radius = tree_size * 0.15
    crown_leaves = LEAVES_PER_BRANCH * 2
    lv, ln, luv, lt, lc = _generate_leaf_cluster(
        crown_center, crown_radius, leaf_size, crown_leaves, rng
    )
    leaf_offset = sum(len(v) for v in all_leaf_v)
    lt_offset = lt + leaf_offset
    all_leaf_v.append(lv)
    all_leaf_n.append(ln)
    all_leaf_uv.append(luv)
    all_leaf_tri.append(lt_offset)
    all_leaf_col.append(lc)

    # Merge everything
    branch_verts = np.concatenate(all_branch_v)
    branch_norms = np.concatenate(all_branch_n)
    branch_uvs = np.concatenate(all_branch_uv)
    branch_tris = np.concatenate(all_branch_tri)
    branch_colors = np.concatenate(all_branch_col)

    leaf_verts = np.concatenate(all_leaf_v) if all_leaf_v else np.zeros((0, 3), dtype=np.float32)
    leaf_norms = np.concatenate(all_leaf_n) if all_leaf_n else np.zeros((0, 3), dtype=np.float32)
    leaf_uvs = np.concatenate(all_leaf_uv) if all_leaf_uv else np.zeros((0, 2), dtype=np.float32)
    leaf_tris = np.concatenate(all_leaf_tri) if all_leaf_tri else np.zeros((0, 3), dtype=np.int32)
    leaf_colors = np.concatenate(all_leaf_col) if all_leaf_col else np.zeros((0, 4), dtype=np.uint8)

    return {
        'branch_verts': branch_verts,
        'branch_normals': branch_norms,
        'branch_uvs': branch_uvs,
        'branch_tris': branch_tris,
        'branch_colors': branch_colors,
        'leaf_verts': leaf_verts,
        'leaf_normals': leaf_norms,
        'leaf_uvs': leaf_uvs,
        'leaf_tris': leaf_tris,
        'leaf_colors': leaf_colors,
    }


# ---------------------------------------------------------------------------
# NIF builder
# ---------------------------------------------------------------------------

def _make_shape(name_bytes: bytes, verts, norms, uvs, tris, colors,
                tex0_path: str, tex1_path: str = '',
                has_alpha: bool = False):
    """Build a NiTriShape with BSLightingShaderProperty + vertex colors."""
    tsd = NifFormat.NiTriShapeData()
    tsd.has_vertices = True
    tsd.has_normals = len(norms) > 0
    tsd.has_uv = True
    tsd.num_uv_sets = 1
    tsd.has_vertex_colors = (colors is not None and len(colors) > 0)
    tsd.num_vertices = len(verts)
    tsd.vertices.update_size()
    tsd.normals.update_size()
    tsd.uv_sets.update_size()
    if tsd.has_vertex_colors:
        tsd.vertex_colors.update_size()

    for i in range(len(verts)):
        tsd.vertices[i].x = float(verts[i, 0])
        tsd.vertices[i].y = float(verts[i, 1])
        tsd.vertices[i].z = float(verts[i, 2])

    if tsd.has_normals:
        for i in range(len(norms)):
            tsd.normals[i].x = float(norms[i, 0])
            tsd.normals[i].y = float(norms[i, 1])
            tsd.normals[i].z = float(norms[i, 2])

    for i in range(len(uvs)):
        tsd.uv_sets[0][i].u = float(uvs[i, 0])
        tsd.uv_sets[0][i].v = float(uvs[i, 1])

    if tsd.has_vertex_colors and colors is not None:
        for i in range(len(colors)):
            tsd.vertex_colors[i].r = float(colors[i, 0]) / 255.0
            tsd.vertex_colors[i].g = float(colors[i, 1]) / 255.0
            tsd.vertex_colors[i].b = float(colors[i, 2]) / 255.0
            tsd.vertex_colors[i].a = float(colors[i, 3]) / 255.0

    tsd.num_triangles = len(tris)
    tsd.num_triangle_points = len(tris) * 3
    tsd.has_triangles = True
    tsd.triangles.update_size()
    for i in range(len(tris)):
        tsd.triangles[i].v_1 = int(tris[i, 0])
        tsd.triangles[i].v_2 = int(tris[i, 1])
        tsd.triangles[i].v_3 = int(tris[i, 2])

    # Bounding sphere
    if len(verts) > 0:
        mins = verts.min(axis=0)
        maxs = verts.max(axis=0)
        center = (mins + maxs) / 2.0
        dists = np.linalg.norm(verts - center, axis=1)
        tsd.center.x = float(center[0])
        tsd.center.y = float(center[1])
        tsd.center.z = float(center[2])
        tsd.radius = float(dists.max())

    # Texture set
    texset = NifFormat.BSShaderTextureSet()
    texset.num_textures = 9
    texset.textures.update_size()
    texset.textures[0] = tex0_path.encode()
    if tex1_path:
        texset.textures[1] = tex1_path.encode()

    # Shader
    shader = NifFormat.BSLightingShaderProperty()
    shader.texture_set = texset
    sf1 = shader.shader_flags_1
    sf1.slsf_1_specular = 1
    sf1.slsf_1_recieve_shadows = 1
    sf1.slsf_1_cast_shadows = 1
    sf1.slsf_1_z_buffer_test = 1
    if colors is not None and len(colors) > 0:
        sf1.slsf_1_vertex_alpha = 1
    sf2 = shader.shader_flags_2
    sf2.slsf_2_z_buffer_write = 1
    if has_alpha:
        sf2.slsf_2_double_sided = 1

    # Shape
    ts = NifFormat.NiTriShape()
    ts.name = name_bytes
    ts.flags = NIF_FLAGS
    ts.data = tsd
    ts.bs_properties[0] = shader

    if has_alpha:
        alpha = NifFormat.NiAlphaProperty()
        alpha.flags = 0x12EC  # standard Skyrim alpha test (like vanilla trees)
        alpha.threshold = 80   # lower threshold for smoother leaf edges
        ts.bs_properties[1] = alpha

    return ts


def _resolve_texture(spt_fname: str, tex_dir: str, fallback: str = '') -> str:
    """Build texture path from SPT filename, searching the appropriate dir."""
    if not spt_fname:
        return fallback
    stem = os.path.splitext(spt_fname)[0]
    return tex_dir + stem + '.dds'


def _resolve_normal(spt_fname: str, tex_dir: str) -> str:
    if not spt_fname:
        return ''
    stem = os.path.splitext(spt_fname)[0]
    return tex_dir + stem + '_n.dds'


def build_tree_nif(spt_data: dict, spt_name: str) -> bytes:
    """Build a Skyrim NIF from parsed SPT data. Returns raw NIF bytes."""
    if not _PYFFI:
        raise RuntimeError("pyffi not available")

    # Generate geometry
    geo = _generate_tree_geometry(spt_data, spt_name)

    # Resolve textures
    bark_tex = spt_data.get('bark_texture') or ''
    leaf_texs = spt_data.get('leaf_textures') or []
    leaf_tex = leaf_texs[0] if leaf_texs else ''

    bark_diffuse = _resolve_texture(bark_tex, BARK_TEX_DIR, BARK_TEX_DIR + 'treebarkplaceholder.dds')
    bark_normal = _resolve_normal(bark_tex, BARK_TEX_DIR)
    leaf_diffuse = _resolve_texture(leaf_tex, LEAF_TEX_DIR, LEAF_TEX_DIR + 'treeleaf.dds')
    leaf_normal = _resolve_normal(leaf_tex, LEAF_TEX_DIR)

    # Build shapes
    bark_shape = _make_shape(
        b'TES5 Skyrim Tree - Branches',
        geo['branch_verts'], geo['branch_normals'],
        geo['branch_uvs'], geo['branch_tris'], geo['branch_colors'],
        tex0_path=bark_diffuse, tex1_path=bark_normal,
    )
    leaf_shape = _make_shape(
        b'TES5 Skyrim Tree - Leaves',
        geo['leaf_verts'], geo['leaf_normals'],
        geo['leaf_uvs'], geo['leaf_tris'], geo['leaf_colors'],
        tex0_path=leaf_diffuse, tex1_path=leaf_normal,
        has_alpha=True,
    )

    # BSXFlags
    bsx = NifFormat.BSXFlags()
    bsx.name = b'BSX'
    bsx.integer_data = BSX_FLAGS

    # Root: BSLeafAnimNode
    root = NifFormat.BSLeafAnimNode()
    root.name = b'TES5 Skyrim Tree'
    root.flags = NIF_FLAGS

    root.num_extra_data_list = 1
    root.extra_data_list.update_size()
    root.extra_data_list[0] = bsx

    root.num_children = 2
    root.children.update_size()
    root.children[0] = bark_shape
    root.children[1] = leaf_shape

    # NIF data
    nif_data = NifFormat.Data()
    nif_data.version = 0x14020007
    nif_data.user_version = 12
    nif_data.user_version_2 = 83
    nif_data.header.endian_type = 1
    nif_data.roots = [root]

    buf = io.BytesIO()
    nif_data.write(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Single-file entry point
# ---------------------------------------------------------------------------

def convert_spt(src: Path, dst: Path) -> bool:
    """Convert one .spt file to a .nif at dst. Returns True on success."""
    try:
        spt_data = parse_spt(src)
        nif_bytes = build_tree_nif(spt_data, src.stem)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(nif_bytes)
        return True
    except Exception as e:
        print(f'  [SPT] ERROR converting {src.name}: {e}')
        return False


# ---------------------------------------------------------------------------
# Batch conversion
# ---------------------------------------------------------------------------

def convert_spt_directory(src_dir: Path, dst_dir: Path,
                          use_skyblivion: bool = False) -> dict:
    """Convert all .spt files under src_dir into .nif in dst_dir.

    Args:
        src_dir:         source directory (e.g. export/Oblivion.esm/trees/)
        dst_dir:         destination directory for NIF output
        use_skyblivion:  if True, copy Skyblivion reference NIFs when available

    Returns dict with 'ok', 'fail', 'skip' counts.
    """
    spt_files = sorted(src_dir.rglob('*.spt'))
    if not spt_files:
        print(f'  [SPT] No .spt files found in {src_dir}')
        return {'ok': 0, 'fail': 0, 'skip': 0}

    mode = "Skyblivion-first" if use_skyblivion else "procedural"
    print(f'  [SPT] Converting {len(spt_files)} files ({mode}) with {_WORKER_COUNT} workers...')
    counts = {'ok': 0, 'fail': 0, 'skip': 0}

    def _task(spt_path: Path) -> bool:
        rel = spt_path.relative_to(src_dir)
        stem = rel.stem
        dst_main = dst_dir / rel.with_suffix('.nif')

        if use_skyblivion:
            sk_stem, season_hint = _spt_to_skyblivion(stem)
            if sk_stem:
                seasons_to_try = [season_hint] if season_hint else list(_SEASONS)
                main_done = dst_main.exists()
                any_ok = False
                for season in seasons_to_try:
                    src_nif = _find_season_nif(sk_stem, season)
                    if src_nif is None:
                        continue
                    dst_seasonal = dst_dir / f'{sk_stem}{season}.nif'
                    if not dst_seasonal.exists():
                        _copy_nif_remap_textures(src_nif, dst_seasonal)
                        any_ok = True
                    if not main_done and season == seasons_to_try[0]:
                        _copy_nif_remap_textures(src_nif, dst_main)
                        main_done = True
                        any_ok = True
                if any_ok:
                    return True

        # Procedural generation
        if dst_main.exists():
            return None  # skip existing
        return convert_spt(spt_path, dst_main)

    with ThreadPoolExecutor(max_workers=_WORKER_COUNT) as pool:
        futures = {pool.submit(_task, p): p for p in spt_files}
        for fut in as_completed(futures):
            result = fut.result()
            if result is None:
                counts['skip'] += 1
            elif result:
                counts['ok'] += 1
            else:
                counts['fail'] += 1

    print(f"  [SPT] Done: {counts['ok']} ok, {counts['fail']} fail, "
          f"{counts['skip']} skipped")
    return counts


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(
        description='Convert Oblivion SpeedTree (.spt) files to Skyrim NIFs')
    parser.add_argument('src_dir', help='Source directory with .spt files')
    parser.add_argument('dst_dir', help='Destination directory for .nif output')
    parser.add_argument('--use-skyblivion', action='store_true',
                        help='Copy Skyblivion reference NIFs when available '
                             '(disabled by default)')
    args = parser.parse_args()

    if not _PYFFI:
        print('ERROR: pyffi not installed.  Run: pip install PyFFI')
        raise SystemExit(1)

    counts = convert_spt_directory(
        Path(args.src_dir), Path(args.dst_dir),
        use_skyblivion=args.use_skyblivion,
    )
    raise SystemExit(0 if counts['fail'] == 0 else 1)
