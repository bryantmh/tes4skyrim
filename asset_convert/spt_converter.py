"""SpeedTree (.spt) -> Skyrim NIF converter.

Reads Oblivion/SpeedTree v2 .spt binary files and generates Skyrim-compatible
NIF meshes with procedural trunk, branches, and leaf-billboard geometry.

Output NIF structure:
  BSLeafAnimNode  "TES5 Skyrim Tree"  flags=14  BSXFlags=130
    bhkCollisionObject (bhkRigidBodyT → bhkCapsuleShape trunk collision)
    NiTriShape  "TES5 Skyrim Tree - Branches"   bark texture + vertex colors  ExtraVectorsFlags=16
    NiTriShape  "TES5 Skyrim Tree - Leaves"      leaf texture + alpha + vertex colors  ExtraVectorsFlags=16
    NiTriShape  "TES5 Skyrim Tree - Caps"        caps texture + vertex colors  ExtraVectorsFlags=16

Scale: SPT tree_size is the tree height in game units (Oblivion/Skyrim inches).
All geometry is generated proportionally:
  - Trunk height = tree_size * TRUNK_HEIGHT_FRAC
  - Trunk base radius = tree_size * TRUNK_RADIUS_FRAC
  - Branches spread to ~tree_size * 0.6 radius
  - Leaves fill a sphere at canopy zone (upper 40% of total height)

Usage (CLI):
    python -m asset_convert.spt_converter <src_dir> <dst_dir>
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

# Texture folders under output/oblivion.esm/textures/tes4/speedtrees/
BARK_TEX_DIR = 'textures\\tes4\\speedtrees\\branches\\'
LEAF_TEX_DIR = 'textures\\tes4\\speedtrees\\leaves\\'

# Procedural generation defaults
MIN_TREE_HEIGHT    = 50.0
TRUNK_AZ_SEGMENTS  = 10     # sides of trunk cylinder
TRUNK_Z_SEGMENTS   = 8      # vertical subdivisions
BRANCH_AZ_SEGMENTS = 6      # sides of branch tubes
BRANCH_Z_SEGMENTS  = 4      # length subdivisions per branch
LEAVES_PER_BRANCH  = 20     # leaf billboards per branch tip
LEAF_QUAD_SIZE_FRAC = 0.10  # leaf size as fraction of tree height
TRUNK_HEIGHT_FRAC  = 0.55   # trunk makes up this fraction of total tree height
TRUNK_RADIUS_FRAC  = 0.055  # trunk base radius as fraction of tree height
CANOPY_SPREAD_FRAC = 0.55   # canopy/branch spread radius as fraction of tree height


def _copy_nif_remap_textures(src: Path, dst: Path) -> bool:
    if not _PYFFI:
        return False
    data = NifFormat.Data()
    with open(src, 'rb') as f:
        data.read(f)
    _SKY_PFX = 'textures\\oblivion\\landscape\\trees\\'
    _OUR_PFX = 'tes4\\speedtrees\\'
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
    # The Skyblivion tree assets pair bhkRigidBodyT with CMS collision — a
    # combination vanilla Skyrim never ships and which intermittently
    # produces invalid-shape-key CTDs.  Bake the body transform into the
    # shape data and demote to a plain identity bhkRigidBody.
    from .collision import demote_t_body_on_mesh_collision
    demote_t_body_on_mesh_collision(data)
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


def _generate_caps_mesh(center, radius, quad_size: float, rng: np.random.Generator):
    """Generate a ring of billboard quads at the top of the canopy (Caps mesh).

    The Caps mesh in Skyrim trees covers the top/exterior of the canopy crown
    with upward-facing billboard quads tiling the outer sphere surface.
    Returns (verts Nx3, normals Nx3, uvs Nx2, tris Mx3, colors Nx4 uint8).
    """
    cx, cy, cz = center
    n_caps = max(8, int(radius / quad_size * 2))
    n_caps = min(n_caps, 24)  # cap polygon count to stay within reference (24 verts)

    all_v = []
    all_n = []
    all_uv = []
    all_tri = []
    all_col = []

    half = quad_size * 0.5

    for i in range(n_caps):
        # Distribute quads on a hemisphere cap
        # Use Fibonacci-sphere-like distribution for outer caps
        frac = (i + 0.5) / n_caps
        phi = math.acos(1.0 - frac * 0.6)  # top 60% of sphere
        theta = 2.0 * math.pi * i / (1.618033988)  # golden angle

        # Position on canopy sphere
        r_off = radius * math.sin(phi) * rng.uniform(0.6, 1.0)
        px = cx + r_off * math.cos(theta)
        py = cy + r_off * math.sin(theta)
        pz = cz + radius * math.cos(phi) * 0.5 + rng.uniform(-quad_size * 0.3, quad_size * 0.3)

        # Caps face mostly upward/outward
        outward = np.array([px - cx, py - cy, pz - cz + radius * 0.5])
        outward /= (np.linalg.norm(outward) + 1e-9)
        face_norm = outward

        # Build quad
        if abs(face_norm[2]) < 0.9:
            world_up = np.array([0.0, 0.0, 1.0])
        else:
            world_up = np.array([1.0, 0.0, 0.0])
        right = np.cross(face_norm, world_up)
        right /= (np.linalg.norm(right) + 1e-9)
        up = np.cross(right, face_norm)
        up /= (np.linalg.norm(up) + 1e-9)

        s = half * rng.uniform(0.8, 1.2)
        base_idx = len(all_v)
        pos = np.array([px, py, pz])
        corners = [
            pos - right * s - up * s,
            pos + right * s - up * s,
            pos + right * s + up * s,
            pos - right * s + up * s,
        ]
        for c in corners:
            all_v.append((float(c[0]), float(c[1]), float(c[2])))
            all_n.append((float(face_norm[0]), float(face_norm[1]), float(face_norm[2])))
        all_uv.extend([(0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)])

        all_tri.append((base_idx, base_idx + 1, base_idx + 2))
        all_tri.append((base_idx, base_idx + 2, base_idx + 3))
        # Backface
        all_tri.append((base_idx, base_idx + 2, base_idx + 1))
        all_tri.append((base_idx, base_idx + 3, base_idx + 2))

        g_var = rng.integers(180, 255)
        r_var = rng.integers(160, 230)
        for _ in range(4):
            all_col.append((r_var, g_var, rng.integers(140, 180), 255))

    return (np.array(all_v, dtype=np.float32),
            np.array(all_n, dtype=np.float32),
            np.array(all_uv, dtype=np.float32),
            np.array(all_tri, dtype=np.int32),
            np.array(all_col, dtype=np.uint8))


def _generate_tree_geometry(spt_data: dict, spt_name: str):
    """Generate complete tree geometry from SPT parameters.

    Returns dict with 'branch_*', 'leaf_*', and 'caps_*' numpy arrays.

    Scale notes:
      SPT tree_size is the total tree height in game units (Oblivion inches).
      All other SPT parameters are LOD/lighting metadata — NOT usable for geometry.
      We derive all proportions from tree_size alone.
    """
    rng = np.random.default_rng(_seed_from_name(spt_name))

    tree_size = max(spt_data.get('tree_size', 200.0), MIN_TREE_HEIGHT)
    is_shrub = spt_name.lower().startswith('shrub') or tree_size < 80

    # Trunk geometry proportions
    trunk_height = tree_size * (0.40 if is_shrub else TRUNK_HEIGHT_FRAC)
    r_start = tree_size * TRUNK_RADIUS_FRAC
    r_end = r_start * (0.5 if is_shrub else 0.2)

    gravity = 0.0   # SPT gravity fields are LOD metadata, not actual lean

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

    # ---- Branch parameters derived from tree_size ----
    if is_shrub:
        n_branches = 6
        branch_start_h = 0.10   # shrubs branch from near the base
    else:
        n_branches = 8
        branch_start_h = 0.40   # trees branch from mid-trunk

    # Branch length: extend well into the canopy
    canopy_spread = tree_size * CANOPY_SPREAD_FRAC
    branch_len_base = canopy_spread * 0.9

    # Branch elevation: roughly 30-50 deg above horizontal for nice spread
    branch_elevation_deg = 35.0 if is_shrub else 28.0

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

    # Leaf size: use SPT leaf_sizes if available (they are X/100 of tree unit).
    # Multiply by tree_size to get game-unit leaf quad size.
    leaf_sizes = spt_data.get('leaf_sizes', [])
    if leaf_sizes:
        raw_w, raw_h = leaf_sizes[0]
        # raw values are fraction / 100.0 per sptparser description "Size/100.0"
        # So actual leaf size = raw * 100 * scale_to_game_units
        # For tree_size=255, typical leaf 0.14*100 = 14 game units (reasonable)
        leaf_size = max(raw_w, raw_h) * 100.0
        # Clamp to sensible range: min 5% of tree height, max 20%
        leaf_size = max(tree_size * 0.05, min(leaf_size, tree_size * 0.20))
    else:
        leaf_size = tree_size * LEAF_QUAD_SIZE_FRAC

    for bi in range(n_branches):
        # Distribute branches evenly between branch_start and 95% of trunk height
        t_frac = branch_start_h + (0.95 - branch_start_h) * bi / max(n_branches - 1, 1)
        t_frac = max(0.05, min(0.95, t_frac + rng.uniform(-0.04, 0.04)))

        ring_idx = int(t_frac * (len(rings) - 1))
        ring_idx = min(ring_idx, len(rings) - 1)
        rc = rings[ring_idx]
        attach_pos = (rc[0], rc[1], rc[2])
        attach_r = rc[3]

        # Branch direction: spread outward with slight upward elevation
        azimuth = 2.0 * math.pi * bi / n_branches + rng.uniform(-0.2, 0.2)
        elevation_rad = math.radians(branch_elevation_deg + rng.uniform(-10, 10))
        dx = math.cos(azimuth) * math.cos(elevation_rad)
        dy = math.sin(azimuth) * math.cos(elevation_rad)
        dz = math.sin(elevation_rad)

        branch_len = branch_len_base * rng.uniform(0.80, 1.20)

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

        # Generate leaves at branch tip — cluster radius proportional to canopy
        cluster_radius = leaf_size * 3.0
        n_leaves = LEAVES_PER_BRANCH + rng.integers(-4, 6)
        n_leaves = max(8, n_leaves)

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

    # Dense leaf fill in the canopy sphere (upper canopy zone)
    # This fills the inner canopy with leaves between branch tips
    canopy_center_z = trunk_height + (tree_size - trunk_height) * 0.35
    canopy_sphere_r  = canopy_spread * 0.7
    canopy_center = (rings[-1][0], rings[-1][1], canopy_center_z)
    n_canopy_clusters = 4 if is_shrub else 6
    for ci in range(n_canopy_clusters):
        # Distribute fill clusters around canopy
        a = 2.0 * math.pi * ci / n_canopy_clusters + rng.uniform(-0.4, 0.4)
        e = rng.uniform(0.0, math.pi * 0.5)
        r_off = canopy_sphere_r * rng.uniform(0.3, 0.8)
        cx = canopy_center[0] + r_off * math.cos(a) * math.sin(e)
        cy = canopy_center[1] + r_off * math.sin(a) * math.sin(e)
        cz = canopy_center[2] + r_off * math.cos(e) * 0.6
        fill_center = (cx, cy, cz)
        fill_r = leaf_size * 2.5
        n_leaves = LEAVES_PER_BRANCH * 2 + rng.integers(0, 8)

        lv, ln, luv, lt, lc = _generate_leaf_cluster(
            fill_center, fill_r, leaf_size, n_leaves, rng
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

    # ---- Generate caps mesh (top-canopy billboard ring) ----
    caps_verts, caps_norms, caps_uvs, caps_tris, caps_colors = _generate_caps_mesh(
        canopy_center, canopy_spread * 0.6, leaf_size, rng
    )

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
        'caps_verts': caps_verts,
        'caps_normals': caps_norms,
        'caps_uvs': caps_uvs,
        'caps_tris': caps_tris,
        'caps_colors': caps_colors,
        # Save trunk parameters for collision generation
        'trunk_height': trunk_height,
        'trunk_radius_base': r_start,
        'trunk_radius_top': r_end,
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
    # ExtraVectorsFlags = 16: tangent space vectors (cond: has_normals && extra_vectors_flags & 16)
    # Must be set BEFORE update_size() calls so tangents/bitangents arrays are sized correctly
    tsd.extra_vectors_flags = 16
    tsd.num_vertices = len(verts)
    tsd.vertices.update_size()
    tsd.normals.update_size()
    tsd.tangents.update_size()
    tsd.bitangents.update_size()
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


def _make_tree_collision(root_node: 'NifFormat.NiAVObject',
                         trunk_height: float, r_base: float, r_top: float):
    """Build a bhkCapsuleShape collision for the tree trunk.

    Uses a capsule (two-point cylinder with hemispherical caps) matching
    the trunk dimensions.  Scaled by HAVOK_SCALE (0.1) for Skyrim Havok units.

    Returns a bhkCollisionObject attached to root_node, or None on failure.
    """
    HAVOK = 0.1

    # Capsule points: bottom-center and top-center of trunk cylinder
    # The capsule radius covers the base of the trunk
    r_cap = max(r_base, r_top) * HAVOK
    # Extend points inward by radius so the capsule ends are flush with trunk ends
    r_inset = r_cap
    z_bot = r_inset         # bottom hemisphere center
    z_top = trunk_height * HAVOK - r_inset  # top hemisphere center
    # If trunk is too short for two separate hemisphere centers, use sphere
    if z_top <= z_bot:
        z_bot = trunk_height * HAVOK * 0.5
        z_top = z_bot

    cap_shape = NifFormat.bhkCapsuleShape()
    cap_shape.radius   = r_cap
    cap_shape.radius_1 = r_cap
    cap_shape.radius_2 = r_cap
    cap_shape.first_point.x  = 0.0
    cap_shape.first_point.y  = 0.0
    cap_shape.first_point.z  = z_bot
    cap_shape.second_point.x = 0.0
    cap_shape.second_point.y = 0.0
    cap_shape.second_point.z = z_top

    rb = NifFormat.bhkRigidBody()
    rb.shape = cap_shape
    # Static body (STATIC motion type, fixed quality)
    rb.mass                = 0.0
    rb.friction            = 0.5
    rb.restitution         = 0.4
    rb.linear_damping      = 0.0996
    rb.angular_damping     = 0.0498
    rb.max_linear_velocity  = 104.4
    rb.max_angular_velocity = 31.57
    rb.motion_system       = 5   # MO_SYS_BOX_STABILIZED (static)
    rb.quality_type        = 0   # MO_QUAL_INVALID → static
    rb.deactivator_type    = 1   # DEACTIVATOR_NEVER (static objects never deactivate)
    rb.havok_col_filter_copy.layer = 2   # LAYER_STATIC
    rb.havok_col_filter.layer      = 2

    # Standard Skyrim RB padding fields:
    rb.unknown_int_1 = 0
    rb.unknown_int_2 = 1          # BroadPhaseType=1 (BROAD_PHASE_ENTITY)
    rb.unknown_3_ints[0] = 0
    rb.unknown_3_ints[1] = 0
    rb.unknown_3_ints[2] = -2147483648  # 0x80000000
    rb.unknown_byte = 116
    rb.unknown_time_factor_or_gravity_factor_1 = 1.0
    rb.unknown_time_factor_or_gravity_factor_2 = 1.0
    rb.unknown_int_6  = 196608
    rb.unknown_int_7  = 0
    rb.unknown_int_8  = 0
    rb.unknown_int_81 = 0
    rb.unknown_int_91 = 0
    # Reference tree values for unknown_6_shorts from treeblacklocust01summer.nif
    rb.unknown_6_shorts[0] = 23888
    rb.unknown_6_shorts[1] = 9525
    rb.unknown_6_shorts[2] = 0      # MUST be 0
    rb.unknown_6_shorts[3] = 0      # MUST be 0
    rb.unknown_6_shorts[4] = 3841
    rb.unknown_6_shorts[5] = 65535
    rb.unknown_2_shorts[0] = 7392
    rb.unknown_2_shorts[1] = 13917

    co = NifFormat.bhkCollisionObject()
    co.flags  = 129   # CO_FLAGS_ACTIVE | CO_FLAGS_SYNC
    co.target = root_node
    co.body   = rb
    return co


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

    # Caps use same bark texture as branches (standard Skyrim tree convention)
    caps_diffuse = bark_diffuse
    caps_normal = bark_normal

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
    caps_shape = _make_shape(
        b'TES5 Skyrim Tree - Caps',
        geo['caps_verts'], geo['caps_normals'],
        geo['caps_uvs'], geo['caps_tris'], geo['caps_colors'],
        tex0_path=caps_diffuse, tex1_path=caps_normal,
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

    # Build trunk collision
    co = _make_tree_collision(
        root,
        geo['trunk_height'],
        geo['trunk_radius_base'],
        geo['trunk_radius_top'],
    )
    root.collision_object = co

    root.num_children = 3
    root.children.update_size()
    root.children[0] = bark_shape
    root.children[1] = leaf_shape
    root.children[2] = caps_shape

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
# Project asset matching (assets/speedtrees/ directory)
# ---------------------------------------------------------------------------

# Root of our bundled pre-converted tree assets
_PROJECT_ASSET_MESH_DIR = (
    Path(__file__).parent.parent
    / 'assets' / 'speedtrees' / 'Meshes' / 'Oblivion' / 'Landscape' / 'Trees'
)
_PROJECT_ASSET_TEX_DIR = (
    Path(__file__).parent.parent
    / 'assets' / 'speedtrees' / 'Textures' / 'Oblivion' / 'Landscape' / 'Trees'
)

# Lazy index: lowercase stem → Path for all non-_col NIFs in project assets
_asset_nif_index: dict | None = None


def _get_asset_nif_index() -> dict:
    """Return a {lowercase_stem: path} dict for all project asset NIFs."""
    global _asset_nif_index
    if _asset_nif_index is not None:
        return _asset_nif_index
    idx: dict = {}
    if _PROJECT_ASSET_MESH_DIR.exists():
        for p in _PROJECT_ASSET_MESH_DIR.rglob('*.nif'):
            if p.stem.lower().endswith('_col'):
                continue
            idx[p.stem.lower()] = p
    _asset_nif_index = idx
    return idx


def _find_closest_project_asset(spt_stem: str) -> Path | None:
    """Return the best matching project-asset NIF for a given SPT stem.
    """
    import difflib

    idx = _get_asset_nif_index()
    if not idx:
        return None

    # difflib fuzzy match over all stems
    query = spt_stem.lower()
    best_ratio = 0.0
    best_path: Path | None = None
    for stem_lc, path in idx.items():
        ratio = difflib.SequenceMatcher(None, query, stem_lc).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_path = path

    return best_path if best_ratio > 0.4 else None


def _copy_project_asset(src_nif: Path, dst_nif: Path) -> bool:
    """Copy a project-asset NIF to dst, remapping internal texture paths.

    Textures are referenced as  textures\\oblivion\\landscape\\trees\\*  inside
    the NIF; we remap them to  tes4\\landscape\\trees\\*  so they sit under the
    tes4\\ namespace in the output archive.
    """
    if not _PYFFI:
        dst_nif.parent.mkdir(parents=True, exist_ok=True)
        import shutil as _sh
        _sh.copy2(src_nif, dst_nif)
        return True
    return _copy_nif_remap_textures(src_nif, dst_nif)


def _copy_project_textures(tex_output_dir: Path) -> int:
    """Copy all bundled speedtree textures to tex_output_dir.

    Source:  assets/speedtrees/Textures/Oblivion/Landscape/Trees/*.dds
    Dest:    tex_output_dir/*.dds  (caller sets this to textures/tes4/speedtrees/)
    """
    import shutil as _sh
    if not _PROJECT_ASSET_TEX_DIR.exists():
        return 0
    tex_output_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for src in _PROJECT_ASSET_TEX_DIR.iterdir():
        if not src.is_file():
            continue
        dst = tex_output_dir / src.name
        # Always copy/overwrite bundled textures for speedtrees
        _sh.copy2(src, dst)
        count += 1
    return count


# ---------------------------------------------------------------------------
# Batch conversion
# ---------------------------------------------------------------------------

def convert_spt_directory(src_dir: Path, dst_dir: Path,
                          tex_output_dir: Path | None = None) -> dict:
    """Convert all .spt files under src_dir into .nif in dst_dir.

    Uses bundled project assets (assets/speedtrees/) to find the closest
    matching pre-converted NIF for each SPT file by name similarity.  The
    procedural SPT converter is kept but disabled.

    Args:
        src_dir:         source directory (e.g. export/Oblivion.esm/trees/)
        dst_dir:         destination directory for NIF output
        tex_output_dir:  if provided, copy bundled tree textures here

    Returns dict with 'ok', 'fail', 'skip' counts.
    """
    spt_files = sorted(src_dir.rglob('*.spt'))
    if not spt_files:
        print(f'  [SPT] No .spt files found in {src_dir}')
        return {'ok': 0, 'fail': 0, 'skip': 0}

    # Eagerly build asset index (single-threaded, fast)
    idx = _get_asset_nif_index()
    index_size = len(idx)
    print(f'  [SPT] {len(spt_files)} SPT files → asset-match mode '
          f'({index_size} project assets) with {_WORKER_COUNT} workers...')

    # Copy bundled textures once before parallel NIF copying
    if tex_output_dir is not None:
        n_tex = _copy_project_textures(tex_output_dir)
        if n_tex:
            print(f'  [SPT] Copied {n_tex} bundled tree textures to {tex_output_dir}')

    counts = {'ok': 0, 'fail': 0, 'skip': 0}

    def _task(spt_path: Path) -> bool | None:
        rel = spt_path.relative_to(src_dir)
        stem = rel.stem
        dst_main = dst_dir / rel.with_suffix('.nif')
        src_nif = _find_closest_project_asset(stem)
        if src_nif is None:
            # No matching asset found — fall back to procedural converter (disabled)
            # return convert_spt(spt_path, dst_main)
            return False

        return _copy_project_asset(src_nif, dst_main)

    with ThreadPoolExecutor(max_workers=_WORKER_COUNT) as pool:
        futures = {pool.submit(_task, p): p for p in spt_files}
        for fut in as_completed(futures):
            result = fut.result()
            if result:
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
    args = parser.parse_args()

    if not _PYFFI:
        print('ERROR: pyffi not installed.  Run: pip install PyFFI')
        raise SystemExit(1)

    counts = convert_spt_directory(
        Path(args.src_dir), Path(args.dst_dir)
    )
    raise SystemExit(0 if counts['fail'] == 0 else 1)
