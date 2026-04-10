"""Skin replacement for converted Oblivion → Skyrim armor/clothing NIFs.

Fills exposed body gaps in armor meshes by splicing clipped Skyrim body geometry.
Also generates _1 weight variants for body-weight interpolation."""

import time
from pathlib import Path

import numpy as np

# Apply all PyFFI patches (time.clock fix, nif.xml condition fixes) before import
from . import pyffi_monkey_patch as _patch  # noqa: F401

try:
    from pyffi.formats.nif import NifFormat
    _PYFFI = True
except ImportError:
    _PYFFI = False


# Texture path prefixes that identify embedded body-skin geometry in Oblivion
# armor/clothing NIFs.  These nodes render the character body through gaps in the
# armor; Skyrim renders the body separately so they must be removed.
_SKIN_TEX_PREFIX = 'textures\\characters\\'

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NIF_FLAGS = 14  # Standard Skyrim NiAVObject flags

# Skyrim body-NIF source directory
_SKYRIM_BODY_DIR = (Path(__file__).parent.parent /
                    'references' / 'Skyrim Meshes' / 'meshes' /
                    'actors' / 'character' / 'character assets')

# Cache: (filepath) → list of NiTriShape blocks (cloned once, reused per NIF)
_BODY_GEOM_CACHE: dict[str, list] = {}

# Preferred path for body-fill geometry: the modified body (after modify_body_meshes.py
# has been run) splits part-32 into torso(32)+upper-legs(44), preventing the cuirass
# from hiding the legs.  Falls back to vanilla Skyrim Meshes if not yet generated.
_SKYRIM_BODY_DIR_MODIFIED = (Path(__file__).parent.parent /
                             'output' / 'oblivion.esm' / 'meshes' /
                             'actors' / 'character' / 'character assets')

# Keywords in the Oblivion skin texture path → (male_nif, female_nif) basename
# Order matters: 'upperbody'/'leg' → body NIF, 'hand' → hands NIF, 'foot' → feet NIF
_SKIN_TEX_TO_BODY_NIF = [
    ('upperbody', 'malebody_0.nif',  'femalebody_0.nif'),
    ('leg',       'malebody_0.nif',  'femalebody_0.nif'),
    ('hand',      'malehands_0.nif', 'femalehands_0.nif'),
    ('foot',      'malefeet_0.nif',  'femalefeet_0.nif'),
    ('underwear', 'malebody_0.nif',  'femalebody_0.nif'),
]

def morph_armor_to_weight1(data, skin_info: dict) -> None:
    """Morph armor vertices toward body_1 shape for weight=1 variant.

    Placeholder — full weight-morph implementation pending.
    """
    pass


def _forward_skin_verts(block) -> list | None:
    """Return world-space vertex positions of a body-skin block.

    v_world ≈ v_local + block.translation  (flat/armored bone, transform ≈ identity).
    Returns a list of (x, y, z) tuples, or None if no geometry.
    """
    if block.data is None:
        return None
    nv = block.data.num_vertices
    if nv == 0:
        return None
    tx = block.translation.x
    ty = block.translation.y
    tz = block.translation.z
    return [(block.data.vertices[i].x + tx,
             block.data.vertices[i].y + ty,
             block.data.vertices[i].z + tz)
            for i in range(nv)]



def collect_skin_info(data,  src_path: str = '') -> dict:
    """Scan armor NIF for body-skin geometry AFTER retarget + bone rename.

    Called post-retarget so bones already have Skyrim names and vertex positions
    are in Skyrim skeleton space.  Texture lookup uses BSLightingShaderProperty
    (the NiTexturingProperty was converted by _walk_node earlier).

    Returns a dict mapping body-NIF-basename ->
        {'bones': set of Skyrim bone names, 'sections': list of per-block bone sets}
    """
    # nif_name -> {'bones': set, 'sections': [set, set, ...]}
    raw: dict = {}

    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not is_body_skin_geometry(block):
                continue
            skin = getattr(block, 'skin_instance', None)
            if skin is None:
                continue

            nif_name = None
            # Post-conversion: read texture path from BSLightingShaderProperty
            for prop in getattr(block, 'bs_properties', []):
                if prop is None:
                    continue
                if isinstance(prop, NifFormat.BSLightingShaderProperty):
                    ts = getattr(prop, 'texture_set', None)
                    if ts is None:
                        continue
                    tex = bytes(ts.textures[0]).decode('latin-1', errors='replace').lower().replace('/', '\\')
                    is_female = 'female' in tex
                    for keyword, male_nif, female_nif in _SKIN_TEX_TO_BODY_NIF:
                        if keyword in tex:
                            nif_name = female_nif if is_female else male_nif
                            break
                    if nif_name:
                        break

            if nif_name is None:
                continue

            entry = raw.setdefault(nif_name, {'bones': set(), 'sections': []})
            section_bones = set()
            for bi in range(skin.num_bones):
                bn = skin.bones[bi]
                if bn is None:
                    continue
                # Bones are already Skyrim names after retarget + _remap_bone_names
                sk_name = bytes(bn.name).rstrip(b'\x00').decode('latin-1', errors='replace')
                entry['bones'].add(sk_name)
                section_bones.add(sk_name)
            if section_bones:
                entry['sections'].append(section_bones)
                # Store actual vertex positions for proximity-based clipping.
                verts = _forward_skin_verts(block)
                if verts:
                    entry.setdefault('section_verts', []).append(verts)

    # Build result with bone expansion and per-nif bbox
    _BONE_EXPANSIONS = {
        'NPC R UpperArm [RUar]':       ['NPC R Clavicle [RClv]', 'NPC R UpperarmTwist2 [RUt2]'],
        'NPC L UpperArm [LUar]':       ['NPC L Clavicle [LClv]', 'NPC L UpperarmTwist2 [LUt2]'],
        'NPC R UpperarmTwist1 [RUt1]': ['NPC R UpperarmTwist2 [RUt2]'],
        'NPC L UpperarmTwist1 [LUt1]': ['NPC L UpperarmTwist2 [LUt2]'],
        'NPC R Forearm [RLar]':        ['NPC R ForearmTwist2 [RLt2]'],
        'NPC L Forearm [LLar]':        ['NPC L ForearmTwist2 [LLt2]'],
        'NPC R ForearmTwist1 [RLt1]':  ['NPC R ForearmTwist2 [RLt2]'],
        'NPC L ForearmTwist1 [LLt1]':  ['NPC L ForearmTwist2 [LLt2]'],
        'NPC R Foot [Rft ]':           ['NPC R Toe0 [RToe]'],
        'NPC L Foot [Lft ]':           ['NPC L Toe0 [LToe]'],
        'NPC R Calf [RClf]':           ['NPC R CalfTwist [RClt]'],
        'NPC L Calf [LClf]':           ['NPC L CalfTwist [LClt]'],
    }

    result: dict = {}
    for nif_name, entry in raw.items():
        bone_set = entry['bones']
        for base_bone in list(bone_set):
            for extra in _BONE_EXPANSIONS.get(base_bone, []):
                bone_set.add(extra)
        result[nif_name] = {'bones': bone_set, 'sections': entry['sections'],
                            'section_verts': entry.get('section_verts', [])}

    return result


def load_body_geom(nif_basename: str) -> list:
    """Load and cache (geom, bone_index_to_name) pairs from a Skyrim body NIF.

    Returns a list of (NiTriShape, dict[int->str]) tuples.
    The dict maps NiSkinData bone index -> bone name, built from the NiNode tree
    since vanilla Skyrim NIFs don't populate skin.bones[] references via PyFFI.
    """
    if nif_basename in _BODY_GEOM_CACHE:
        return _BODY_GEOM_CACHE[nif_basename]

    # Prefer modified body (split part-32 → torso+upper-legs) so spliced fill
    # matches the character body in-game.  Fall back to vanilla Skyrim meshes.
    path = _SKYRIM_BODY_DIR_MODIFIED / nif_basename
    if not path.exists():
        path = _SKYRIM_BODY_DIR / nif_basename
    if not path.exists():
        _BODY_GEOM_CACHE[nif_basename] = []
        return []

    body_data = NifFormat.Data()
    try:
        with open(path, 'rb') as f:
            body_data.read(f)
    except Exception:
        _BODY_GEOM_CACHE[nif_basename] = []
        return []

    result = []
    for root in body_data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not isinstance(block, NifFormat.NiTriShape):
                continue
            skin = getattr(block, 'skin_instance', None)
            if skin is None:
                continue
            skin_data = getattr(skin, 'data', None)
            if skin_data is None:
                continue

            # Build bone index -> name from the skin instance's bone references.
            # In vanilla Skyrim NIFs loaded by PyFFI, skin.bones[bi] IS populated
            # as a pointer into the block graph — PyFFI resolves ptr refs on read.
            # If it's None, fall back to walking all_blocks for NiNodes.
            bi_to_name: dict = {}
            for bi in range(skin.num_bones):
                bn = skin.bones[bi] if bi < len(list(skin.bones)) else None
                if bn is not None:
                    bi_to_name[bi] = bytes(bn.name).rstrip(b'\x00').decode('latin-1', errors='replace')

            # Fallback: if bones unresolved, match skin_data bone transforms
            # against NiNode world positions from the skeleton tree
            if not bi_to_name:
                # Collect NiNodes from the body NIF by walking the tree
                name_to_node: dict = {}
                for b in root.tree():
                    if isinstance(b, NifFormat.NiNode):
                        nm = bytes(b.name).rstrip(b'\x00').decode('latin-1', errors='replace')
                        if nm:
                            name_to_node[nm] = b
                # Match by skin_transform translation proximity to NiNode translation
                node_list = [(nm, nd.translation.x, nd.translation.y, nd.translation.z)
                             for nm, nd in name_to_node.items()]
                for bi in range(skin_data.num_bones):
                    be = skin_data.bone_list[bi]
                    tx = be.skin_transform.translation.x
                    ty = be.skin_transform.translation.y
                    tz = be.skin_transform.translation.z
                    best_name = None
                    best_dist = float('inf')
                    for nm, nx, ny, nz in node_list:
                        d = (tx - nx)**2 + (ty - ny)**2 + (tz - nz)**2
                        if d < best_dist:
                            best_dist = d
                            best_name = nm
                    if best_name is not None:
                        bi_to_name[bi] = best_name

            result.append((block, bi_to_name))

    _BODY_GEOM_CACHE[nif_basename] = result
    return result


def clip_body_geom(src_geom, bi_to_name: dict, keep_bones: set,
                    section_verts: list = None, proximity_threshold: float = 6.0):
    """Clip a Skyrim body NiTriShape to the region matching removed OB body skin.

    When section_verts is provided (list of per-section vertex-position lists):
        Build a combined point cloud from all OB skin sections (post-retarget world
        positions).  Keep each SK body vert whose nearest-neighbour distance to the
        cloud is below proximity_threshold.  This is geometrically tight — the cloud
        exactly traces the shape of each gap even when the AABB would be far too large.

    When not provided, all vertices are kept.

    bi_to_name / keep_bones: retained for API compatibility, not used for filtering.
    Returns (verts, normals, uvs, tris, kept_weights, bi_to_name) or None.
    """
    skin = getattr(src_geom, 'skin_instance', None)
    if skin is None or src_geom.data is None:
        return None

    skin_data = getattr(skin, 'data', None)
    if skin_data is None:
        return None

    # Read weights from NiSkinData.bone_list — always present in both Oblivion and
    # Skyrim NIFs, unlike NiSkinPartition which is absent in vanilla Skyrim NIFs.
    num_verts = src_geom.data.num_vertices
    vert_weights: list = [{} for _ in range(num_verts)]
    for bi in range(skin_data.num_bones):
        be = skin_data.bone_list[bi]
        for vwi in range(be.num_vertices):
            vw = be.vertex_weights[vwi]
            vi = vw.index
            w = float(vw.weight)
            if vi < num_verts and w > 0.0:
                vert_weights[vi][bi] = vert_weights[vi].get(bi, 0.0) + w

    # Triangles come directly from NiTriShapeData
    all_tris: list = []
    try:
        for tri in src_geom.data.triangles:
            all_tris.append((tri.v_1, tri.v_2, tri.v_3))
    except Exception:
        pass

    if not all_tris:
        return None

    # Precompute translation offset (body NIF local → skeleton space)
    tx = src_geom.translation.x
    ty = src_geom.translation.y
    tz = src_geom.translation.z

    src_data = src_geom.data

    # ---- Build the vertex keep-mask ----------------------------------------
    # Primary: point-cloud proximity against retargeted OB skin vertices.
    # Fallback: expanded per-section AABB.
    # When neither filter is supplied, keep all verts.
    if section_verts:
        # Flatten all per-section vertex clouds into one (M, 3) array.
        flat: list = []
        for sec in section_verts:
            flat.extend(sec)
        cloud = np.array(flat, dtype=np.float32)       # (M, 3)

        # Collect all SK body world positions in one (N, 3) array.
        sk_pos = np.empty((num_verts, 3), dtype=np.float32)
        for vi in range(num_verts):
            v = src_data.vertices[vi]
            sk_pos[vi, 0] = v.x + tx
            sk_pos[vi, 1] = v.y + ty
            sk_pos[vi, 2] = v.z + tz

        # Min-distance from each SK vert to the cloud. Process in chunks of
        # 256 cloud points to stay inside reasonable memory usage.
        thr_sq = proximity_threshold ** 2
        min_dist_sq = np.full(num_verts, np.inf, dtype=np.float32)
        CHUNK = 256
        for start in range(0, len(cloud), CHUNK):
            diff = sk_pos[:, np.newaxis, :] - cloud[np.newaxis, start:start + CHUNK, :]
            d_sq = (diff * diff).sum(axis=2)           # (N, chunk)
            chunk_min = d_sq.min(axis=1)               # (N,)
            np.minimum(min_dist_sq, chunk_min, out=min_dist_sq)

        keep_vert = (min_dist_sq < thr_sq).tolist()

    else:
        keep_vert = [True] * num_verts

    old_to_new = {}
    new_idx = 0
    for vi in range(num_verts):
        if keep_vert[vi]:
            old_to_new[vi] = new_idx
            new_idx += 1

    if new_idx == 0:
        return None

    new_tris = []
    for v0, v1, v2 in all_tris:
        if v0 in old_to_new and v1 in old_to_new and v2 in old_to_new:
            new_tris.append((old_to_new[v0], old_to_new[v1], old_to_new[v2]))

    if not new_tris:
        return None

    kept_indices = sorted(old_to_new.keys())
    # Return raw positions — translation offset applied in build_clipped_geom
    verts = [(src_data.vertices[vi].x, src_data.vertices[vi].y, src_data.vertices[vi].z)
             for vi in kept_indices]
    normals = [(src_data.normals[vi].x, src_data.normals[vi].y, src_data.normals[vi].z)
               for vi in kept_indices] if src_data.has_normals else []
    uvs = [(src_data.uv_sets[0][vi].u, src_data.uv_sets[0][vi].v)
           for vi in kept_indices] if src_data.num_uv_sets > 0 else []
    kept_weights = [vert_weights[vi] for vi in kept_indices]

    return verts, normals, uvs, new_tris, kept_weights, bi_to_name


def build_clipped_geom(src_geom, clip_result, armor_root, bone_map: dict, geom_name: bytes, sk_skel: dict | None = None):
    """Build a new NiTriShape from clipped body geometry data.

    Skin transforms are initially copied from the body NIF's NiSkinData; the
    caller must follow up with _recompute_body_binds() to fix them for the
    armor's flat bone layout.  sk_skel, if provided, is used to position any
    stub bones created for bones not already in the armor NIF.
    """
    verts, normals, uvs, tris, kept_weights, bi_to_name = clip_result
    skin_src = src_geom.skin_instance
    n_verts = len(verts)
    n_tris = len(tris)

    # Bake the body NIF's geom.translation into vertex positions so the spliced
    # geometry lives in the same skeleton-space coordinate system as the armor's
    # own geometry.  Armor geometry has geom.translation ≈ 0 with vertices
    # directly in skeleton space (z ≈ 75-120).  The vanilla body NIF stores
    # vertices at local z ≈ -108 to -6 with geom.translation.z ≈ 120.  If we
    # copy both verbatim, the bind matrices are consistent but the geometry node
    # sits at z ≈ -44 in the armor NIF — 120 units below where it should be.
    # Baking the translation and setting geom.translation = 0, then calling
    # _recompute_body_binds(), produces the correct M@B@W = I with vertices in
    # skeleton space.
    src_tx = src_geom.translation.x
    src_ty = src_geom.translation.y
    src_tz = src_geom.translation.z

    ts_data = NifFormat.NiTriShapeData()
    ts_data.consistency_flags = 0x4000
    ts_data.num_vertices = n_verts
    ts_data.has_vertices = True
    ts_data.vertices.update_size()
    for i, (x, y, z) in enumerate(verts):
        ts_data.vertices[i].x = x + src_tx
        ts_data.vertices[i].y = y + src_ty
        ts_data.vertices[i].z = z + src_tz
    if normals:
        ts_data.has_normals = True
        ts_data.normals.update_size()
        for i, (nx, ny, nz) in enumerate(normals):
            ts_data.normals[i].x = nx
            ts_data.normals[i].y = ny
            ts_data.normals[i].z = nz
    if uvs:
        ts_data.num_uv_sets = 1
        ts_data.uv_sets.update_size()
        for i, (u, v) in enumerate(uvs):
            ts_data.uv_sets[0][i].u = u
            ts_data.uv_sets[0][i].v = v
    ts_data.num_triangles = n_tris
    ts_data.num_triangle_points = n_tris * 3
    ts_data.has_triangles = True
    ts_data.triangles.update_size()
    for i, (v0, v1, v2) in enumerate(tris):
        ts_data.triangles[i].v_1 = v0
        ts_data.triangles[i].v_2 = v1
        ts_data.triangles[i].v_3 = v2

    active_bone_totals: dict = {}
    for wdict in kept_weights:
        for bi, w in wdict.items():
            active_bone_totals[bi] = active_bone_totals.get(bi, 0.0) + w
    active_bone_indices = sorted(active_bone_totals.keys())

    bone_nodes = []
    for gbi in active_bone_indices:
        bname = bi_to_name.get(gbi, f'Bone{gbi}')
        node = bone_map.get(bname)
        if node is None:
            stub = NifFormat.NiNode()
            stub.name = bname.encode('latin-1')
            stub.flags = NIF_FLAGS
            # Set stub bone to its Skyrim skeleton world position so that
            # _recompute_body_binds produces correct bind matrices.
            if sk_skel is not None and bname in sk_skel:
                W = sk_skel[bname]
                stub.rotation.m_11 = float(W[0, 0]); stub.rotation.m_12 = float(W[0, 1]); stub.rotation.m_13 = float(W[0, 2])
                stub.rotation.m_21 = float(W[1, 0]); stub.rotation.m_22 = float(W[1, 1]); stub.rotation.m_23 = float(W[1, 2])
                stub.rotation.m_31 = float(W[2, 0]); stub.rotation.m_32 = float(W[2, 1]); stub.rotation.m_33 = float(W[2, 2])
                stub.translation.x = float(W[3, 0]); stub.translation.y = float(W[3, 1]); stub.translation.z = float(W[3, 2])
            bone_map[bname] = stub
            old_n = armor_root.num_children
            armor_root.num_children = old_n + 1
            armor_root.children.update_size()
            armor_root.children[old_n] = stub
            node = stub
        bone_nodes.append((gbi, bname, node))

    n_bones = len(bone_nodes)

    # Copy the global skin_transform from the source body NIF so that the
    # bone bind matrices (also from the body NIF) remain consistent.
    sk_data_blk = NifFormat.NiSkinData()
    src_skin_data = skin_src.data
    if src_skin_data is not None:
        sst = src_skin_data.skin_transform
        sk_data_blk.skin_transform.rotation.m_11 = sst.rotation.m_11
        sk_data_blk.skin_transform.rotation.m_12 = sst.rotation.m_12
        sk_data_blk.skin_transform.rotation.m_13 = sst.rotation.m_13
        sk_data_blk.skin_transform.rotation.m_21 = sst.rotation.m_21
        sk_data_blk.skin_transform.rotation.m_22 = sst.rotation.m_22
        sk_data_blk.skin_transform.rotation.m_23 = sst.rotation.m_23
        sk_data_blk.skin_transform.rotation.m_31 = sst.rotation.m_31
        sk_data_blk.skin_transform.rotation.m_32 = sst.rotation.m_32
        sk_data_blk.skin_transform.rotation.m_33 = sst.rotation.m_33
        sk_data_blk.skin_transform.translation.x = sst.translation.x
        sk_data_blk.skin_transform.translation.y = sst.translation.y
        sk_data_blk.skin_transform.translation.z = sst.translation.z
        sk_data_blk.skin_transform.scale = sst.scale
    else:
        sk_data_blk.skin_transform.rotation.m_11 = 1.0
        sk_data_blk.skin_transform.rotation.m_22 = 1.0
        sk_data_blk.skin_transform.rotation.m_33 = 1.0
        sk_data_blk.skin_transform.scale = 1.0
    sk_data_blk.num_bones = n_bones
    sk_data_blk.bone_list.update_size()
    for li, (gbi, _bname, _node) in enumerate(bone_nodes):
        be = sk_data_blk.bone_list[li]
        # Copy the inverse bind-pose transform directly from the source body NIF's
        # NiSkinData — gbi is already the body-NIF bone index so this is correct.
        if src_skin_data is not None and gbi < src_skin_data.num_bones:
            src_be = src_skin_data.bone_list[gbi]
            sr = src_be.skin_transform.rotation
            be.skin_transform.rotation.m_11 = sr.m_11
            be.skin_transform.rotation.m_12 = sr.m_12
            be.skin_transform.rotation.m_13 = sr.m_13
            be.skin_transform.rotation.m_21 = sr.m_21
            be.skin_transform.rotation.m_22 = sr.m_22
            be.skin_transform.rotation.m_23 = sr.m_23
            be.skin_transform.rotation.m_31 = sr.m_31
            be.skin_transform.rotation.m_32 = sr.m_32
            be.skin_transform.rotation.m_33 = sr.m_33
            be.skin_transform.translation.x = src_be.skin_transform.translation.x
            be.skin_transform.translation.y = src_be.skin_transform.translation.y
            be.skin_transform.translation.z = src_be.skin_transform.translation.z
            be.skin_transform.scale = src_be.skin_transform.scale
        else:
            be.skin_transform.rotation.m_11 = 1.0
            be.skin_transform.rotation.m_22 = 1.0
            be.skin_transform.rotation.m_33 = 1.0
            be.skin_transform.scale = 1.0
        vw_list = [(new_vi, wd.get(gbi, 0.0))
                   for new_vi, wd in enumerate(kept_weights) if wd.get(gbi, 0.0) > 0.0]
        be.num_vertices = len(vw_list)
        be.vertex_weights.update_size()
        for vwi, (new_vi, w) in enumerate(vw_list):
            be.vertex_weights[vwi].index = new_vi
            be.vertex_weights[vwi].weight = w

    # Leave partition empty — splice_body_geometry will call _regen_skin_partition
    # (from skin_retarget) after _recompute_body_binds so PyFFI builds the correct
    # NiSkinPartition with proper 0-based bone indices into the skin instance array.
    new_bsd = NifFormat.BSDismemberSkinInstance()
    new_bsd.skeleton_root = armor_root
    new_bsd.data = sk_data_blk
    new_bsd.num_bones = n_bones
    new_bsd.bones.update_size()
    for li, (_, _, node) in enumerate(bone_nodes):
        new_bsd.bones[li] = node

    new_geom = NifFormat.NiTriShape()
    new_geom.name = geom_name
    new_geom.flags = NIF_FLAGS
    # geom.translation is intentionally left at 0 — vertices were baked above.
    new_geom.data = ts_data
    new_geom.skin_instance = new_bsd
    for pi, prop in enumerate(src_geom.bs_properties):
        new_geom.bs_properties[pi] = prop

    return new_geom


# Names that are actual underwear overlays (NOT the body mesh itself).
# MaleUnderwearBody:0 / FemaleUnderwearBody:0 ARE the main body and must be kept.
_UNDERWEAR_ONLY_NAMES = frozenset([
    b'maleunderwear', b'femaleunderwear',
])


def apply_armor_offset(data, cfg) -> None:
    """Shift, scale, and optionally tilt all skinned armor geometry vertices.

    cfg : ArmorOffsetConfig (from skyrim_overrides)
        dx/dy/dz   – translation applied last.
        sx/sy/sz   – independent per-axis scale around world origin.
        rotate     – front-to-back tilt in radians in the YZ plane around
                     the mesh centroid.

    Body skin geometry blocks are always skipped.  Recomputes NiSkinData
    bind matrices after moving vertices.
    """
    dx = getattr(cfg, 'dx', 0.0)
    dy = getattr(cfg, 'dy', 0.0)
    dz = getattr(cfg, 'dz', 0.0)
    sx = getattr(cfg, 'sx', 1.0)
    sy = getattr(cfg, 'sy', 1.0)
    sz = getattr(cfg, 'sz', 1.0)
    rotate = getattr(cfg, 'rotate', 0.0)

    has_work = (abs(dx) > 1e-6 or abs(dy) > 1e-6 or abs(dz) > 1e-6
                or abs(sx - 1.0) > 1e-6 or abs(sy - 1.0) > 1e-6 or abs(sz - 1.0) > 1e-6
                or abs(rotate) > 1e-6)
    if not has_work:
        return

    from .skin_retarget import _manual_update_bind_position, _m44_to_np as _sr_m44

    # ---------------------------------------------------------------------- #
    # Helper: iterate armor (non-body-skin) skinned geometry blocks.
    # Body skin is always excluded so offsets don't move the fill skin.
    # ---------------------------------------------------------------------- #
    def _armor_blocks():
        for root in data.roots:
            if root is None:
                continue
            skel_root = None
            # Find skeleton root from any skin instance
            for block in root.tree():
                skin = getattr(block, 'skin_instance', None)
                if skin is not None and skin.skeleton_root is not None:
                    skel_root = skin.skeleton_root
                    break
            if skel_root is None:
                skel_root = root

            for block in root.tree():
                if not isinstance(block, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
                    continue
                if is_body_skin_geometry(block):
                    continue  # never shift body fill skin
                skin = getattr(block, 'skin_instance', None)
                if skin is None:
                    continue
                geom_data = block.data
                if geom_data is None or geom_data.num_vertices == 0:
                    continue

                # Get geometry world transform to convert between local and world space
                try:
                    G = _sr_m44(block.get_transform(skel_root))
                except (ValueError, RuntimeError):
                    G = np.eye(4, dtype=np.float64)
                yield block, skin, G, skel_root

    # ---------------------------------------------------------------------- #
    # Pass 1: collect world-space vertices to compute centroids for  #
    # rotate pivot.  Runs only when rotate is non-zero.              #
    # ---------------------------------------------------------------------- #
    x_cen = 0.0
    y_mid = 0.0
    z_mid = 0.0
    if abs(rotate) > 1e-6:
        vw_parts: list = []
        for block, _sk, G, _sr in _armor_blocks():
            n = block.data.num_vertices
            v = np.array([[block.data.vertices[i].x,
                           block.data.vertices[i].y,
                           block.data.vertices[i].z] for i in range(n)],
                         dtype=np.float64)
            if not np.allclose(G, np.eye(4), atol=1e-6):
                v = v @ G[:3, :3] + G[3, :3]
            vw_parts.append(v)

        if vw_parts:
            all_vw = np.vstack(vw_parts)
            x_cen = float(all_vw[:, 0].mean())
            y_mid = float(all_vw[:, 1].mean())
            z_mid = float(all_vw[:, 2].mean())

    # ---------------------------------------------------------------------- #
    # Pass 2: apply transforms and recompute bind matrices.                  #
    # ---------------------------------------------------------------------- #
    cos_r = np.cos(rotate)
    sin_r = np.sin(rotate)
    do_rotate = abs(rotate) > 1e-6
    translate = np.array([dx, dy, dz], dtype=np.float64)

    for block, skin, G, skel_root in _armor_blocks():
        G_rot = G[:3, :3]
        G_trans = G[3, :3]
        G_is_identity = np.allclose(G, np.eye(4), atol=1e-6)

        n = block.data.num_vertices
        verts = np.array([[block.data.vertices[i].x,
                           block.data.vertices[i].y,
                           block.data.vertices[i].z] for i in range(n)],
                         dtype=np.float64)

        if G_is_identity:
            vw = verts.copy()
        else:
            vw = verts @ G_rot + G_trans

        # 1. Per-axis scale around world origin (0, 0, 0).
        vw[:, 0] *= sx
        vw[:, 1] *= sy
        vw[:, 2] *= sz


        # 3. Front-to-back tilt: proper 2D rotation in the YZ plane around
        #    the mesh centroid (y_mid, z_mid).  Both Y and Z are modified,
        #    preserving mesh shape (no shear distortion).
        #      new_Y = y_mid + (Y-y_mid)*cos(r) - (Z-z_mid)*sin(r)
        #      new_Z = z_mid + (Y-y_mid)*sin(r) + (Z-z_mid)*cos(r)
        if do_rotate:
            y_off = vw[:, 1] - y_mid
            z_off = vw[:, 2] - z_mid
            vw[:, 1] = y_mid + y_off * cos_r - z_off * sin_r
            vw[:, 2] = z_mid + y_off * sin_r + z_off * cos_r

        # 4. Final translation.
        vw += translate

        # Convert back to local space.
        if G_is_identity:
            verts_new = vw
        else:
            verts_new = (vw - G_trans) @ np.linalg.inv(G_rot)

        geom_data = block.data
        for i in range(n):
            geom_data.vertices[i].x = float(verts_new[i, 0])
            geom_data.vertices[i].y = float(verts_new[i, 1])
            geom_data.vertices[i].z = float(verts_new[i, 2])

        # Recompute bind matrices so M@B@W = I with new vertex positions.
        _manual_update_bind_position(block, skin, skel_root)


def is_underwear_only(geom_name: bytes) -> bool:
    """Return True if this is an underwear-only overlay, not the body mesh."""
    low = geom_name.lower().rstrip(b'\x00')
    # Strip trailing digits and underscores: "MaleUnderwear_1" -> "maleunderwear"
    # But "MaleUnderwearBody:0" -> "maleunderwearbody" (not in the set -> kept)
    base = low.split(b':')[0].split(b'_')[0]
    return base in _UNDERWEAR_ONLY_NAMES


def _recompute_body_binds(geom, armor_root):
    """Recompute NiSkinData transforms for spliced body geometry.

    Mirrors _manual_update_bind_position from skin_retarget: uses PyFFI's
    get_transform(skel_root) to traverse the actual node hierarchy so that
    bones at any depth (not just flat children of armor_root) are handled
    correctly.

    S = inv(G)  where G = geom.get_transform(armor_root)
    B_i = G @ inv(W_i)  where W_i = bone.get_transform(armor_root)
    Guarantees S @ B_i @ W_i = I at rest pose.
    """
    skin = getattr(geom, 'skin_instance', None)
    if skin is None:
        return
    skin_data = skin.data
    if skin_data is None:
        return

    from .skin_retarget import _m44_to_np as _sr_m44, _write_skin_transform

    try:
        G = _sr_m44(geom.get_transform(armor_root))
    except (ValueError, RuntimeError):
        G = np.eye(4, dtype=np.float64)

    G_inv = np.linalg.inv(G)
    _write_skin_transform(skin_data.skin_transform, G_inv)

    for i in range(skin_data.num_bones):
        if i >= skin.num_bones:
            break
        bone = skin.bones[i]
        if bone is None:
            continue
        try:
            W = _sr_m44(bone.get_transform(armor_root))
        except (ValueError, RuntimeError):
            continue
        B = G @ np.linalg.inv(W)
        _write_skin_transform(skin_data.bone_list[i].skin_transform, B)


def splice_body_geometry(data, skin_info: dict, weight: int = 0) -> int:
    """Attach clipped Skyrim body geometry into an armor NIF after retarget+rename.

    skin_info: {nif_basename -> {'bones': set[str], 'sections': [set, ...]}}
    Bounding boxes are computed from bone NiNode world positions in the converted
    armor NIF (post-retarget), ensuring correct coordinate space matching with
    the Skyrim body NIF vertices.  Each section (disconnected removed skin block)
    produces its own bbox for fine-grained clipping.
    Must be called AFTER retarget_skin_to_skyrim() and _remap_bone_names().
    """
    if not skin_info or not _PYFFI:
        return 0

    bone_map: dict = {}
    armor_root = None
    for root in data.roots:
        if root is None:
            continue
        armor_root = root
        for block in root.tree():
            if isinstance(block, NifFormat.NiNode):
                raw = bytes(block.name).rstrip(b'\x00').decode('latin-1', errors='replace')
                bone_map[raw] = block

    if armor_root is None or not bone_map:
        return 0

    # Load Skyrim skeleton data for positioning stub bones.
    _gen_dir = Path(__file__).parent / 'generated'
    sk_skel_m, sk_skel_f = {}, {}
    try:
        from .skin_retarget import _load_skeleton
        sk_skel_m = _load_skeleton(_gen_dir / 'skeleton_bones_skyrim_male.json')
        sk_skel_f = _load_skeleton(_gen_dir / 'skeleton_bones_skyrim_female.json')
    except Exception:
        pass

    spliced = 0
    for nif_name, info in sorted(skin_info.items()):
        keep_bones = info['bones']
        sections = info.get('sections', [])

        # Detect gender from body NIF name
        is_female = nif_name.lower().startswith('female')
        sk_skel = sk_skel_f if is_female else sk_skel_m
        section_verts  = info.get('section_verts',  []) or None

        geom_entries = load_body_geom(nif_name)
        for src_geom, bi_to_name in geom_entries:
            skin = getattr(src_geom, 'skin_instance', None)
            if skin is None or not isinstance(skin, NifFormat.BSDismemberSkinInstance):
                continue
            if src_geom.data is None:
                continue
            # Skip actual underwear overlay meshes (e.g. MaleUnderwear_1) but
            # keep the body mesh (MaleUnderwearBody:0) which IS the main body.
            geom_name_raw = bytes(src_geom.name) if src_geom.name else b''
            if is_underwear_only(geom_name_raw):
                continue
            clip_result = clip_body_geom(src_geom, bi_to_name, keep_bones,
                                           section_verts=section_verts,
                                           proximity_threshold=3.8)
            if clip_result is None:
                continue
            geom_name = geom_name_raw if geom_name_raw else b'BodyFill'
            new_geom = build_clipped_geom(
                src_geom, clip_result, armor_root, bone_map, geom_name,
                sk_skel=sk_skel)
            if new_geom is None:
                continue
            old_n = armor_root.num_children
            armor_root.num_children = old_n + 1
            armor_root.children.update_size()
            armor_root.children[old_n] = new_geom
            # Vertices were baked to skeleton space (geom.translation=0).
            # Recompute NiSkinData bind matrices for the armor's flat bone layout.
            _recompute_body_binds(new_geom, armor_root)
            # Regenerate NiSkinPartition using PyFFI's update_skin_partition so
            # bone indices are correct (0-based into the skin instance bone array).
            try:
                from .skin_retarget import _regen_skin_partition
                geom_name_str = bytes(new_geom.name).rstrip(b'\x00').decode('latin-1', errors='replace')
                _regen_skin_partition(new_geom, new_geom.skin_instance, geom_name_str)
            except Exception:
                pass
            spliced += 1

    return spliced


def is_body_skin_geometry(block) -> bool:
    """Return True if this geometry block is embedded body skin.

    Oblivion armor NIFs include the character body mesh (skinned with the
    character skeleton) to show skin through armor gaps.  These nodes use
    textures from textures\\characters\\  (UpperBodyMale.dds, LegFemale.dds,
    HandMale.dds, FootFemale.dds, etc.) rather than the armor texture directory.

    Works both before conversion (NiTexturingProperty in properties) and after
    conversion (BSLightingShaderProperty in bs_properties with tes4\\ prefix).
    """
    if not isinstance(block, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
        return False
    if getattr(block, 'skin_instance', None) is None:
        return False  # only skinned geometry can be body skin
    # Pre-conversion: NiTexturingProperty in properties
    for prop in block.properties:
        if isinstance(prop, NifFormat.NiTexturingProperty):
            if prop.has_base_texture and prop.base_texture.source:
                tex = bytes(prop.base_texture.source.file_name).decode(
                    'latin-1', errors='replace').lower().replace('/', '\\')
                if tex.startswith(_SKIN_TEX_PREFIX):
                    return True
    # Post-conversion: BSLightingShaderProperty in bs_properties (tes4\\ prefix added)
    for prop in getattr(block, 'bs_properties', []):
        if prop is None:
            continue
        if isinstance(prop, NifFormat.BSLightingShaderProperty):
            ts = getattr(prop, 'texture_set', None)
            if ts is None:
                continue
            tex = bytes(ts.textures[0]).decode('latin-1', errors='replace').lower().replace('/', '\\')
            if '\\characters\\' in tex:
                return True
    return False


def strip_body_skin_geometry(data) -> int:
    """Remove embedded body-skin geometry from armor/clothing NIFs.

    Walks the NIF tree and removes any NiTriShape/NiTriStrips child nodes that
    are identified as body skin (texture from textures\\characters\\).

    Removing these nodes lets the Skyrim body mesh show through naturally.
    Skyrim's BSDismemberSkinInstance partition system controls which body-part
    slots the armor covers; any slot not present in the armor's partitions
    continues to display the body mesh underneath.

    Returns the number of geometry nodes removed.
    """
    removed = 0

    def _prune_children(node):
        nonlocal removed
        if not hasattr(node, 'children'):
            return
        keep = []
        for child in node.children:
            if child is not None and is_body_skin_geometry(child):
                removed += 1
            else:
                if child is not None and hasattr(child, 'children'):
                    _prune_children(child)
                keep.append(child)
        if len(keep) < node.num_children:
            node.num_children = len(keep)
            node.children.update_size()
            for ci, cv in enumerate(keep):
                node.children[ci] = cv

    for root in data.roots:
        if root is not None:
            _prune_children(root)

    return removed