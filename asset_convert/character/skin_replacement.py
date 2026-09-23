"""Skin replacement for converted Oblivion → Skyrim armor/clothing NIFs.

Fills exposed body gaps in armor meshes by splicing clipped Skyrim body geometry.
Also generates _1 weight variants for body-weight interpolation."""

from pathlib import Path

import numpy as np

# Apply all PyFFI patches (time.clock fix, nif.xml condition fixes) before import
from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from asset_convert import paths
from asset_convert.character.skin_retarget import load_skeleton, regen_skin_partition

try:
    from pyffi.formats.nif import NifFormat
    _PYFFI = True
except ImportError:
    _PYFFI = False


# Texture path prefixes that identify embedded body-skin geometry in Oblivion
# armor/clothing NIFs.  These nodes render the character body through gaps in the
# armor; Skyrim renders the body separately so they must be removed.
_SKIN_TEX_PREFIX = 'textures\\characters\\'

# Hair lives under textures\characters\hair\ but is NOT body skin: it is a head
# part with its own HDPT, and stripping it deletes the whole mesh (the converted
# NIF ships with the head bone node and no geometry at all).  The body-skin test
# keys off the textures\characters\ prefix, which hair shares, so the hair
# subfolder has to be excluded explicitly.
_HAIR_TEX_MARKER = '\\hair\\'

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NIF_FLAGS = 14  # Standard Skyrim NiAVObject flags

# Cache: (filepath) → list of NiTriShape blocks (cloned once, reused per NIF)
_BODY_GEOM_CACHE: dict[str, list] = {}

#: Modified body: part-32 split into torso+upper-legs so a cuirass cannot hide the legs.
_SKYRIM_BODY_DIR_MODIFIED = (paths.OUTPUT / 'oblivion.esm' / 'meshes'
                             / 'actors' / 'character'
                             / 'character assets')

# Keywords in the Oblivion skin texture path → (male_nif, female_nif) basename
# Order matters: 'upperbody'/'leg' → body NIF, 'hand' → hands NIF, 'foot' → feet NIF
_SKIN_TEX_TO_BODY_NIF = [
    ('upperbody', 'malebody_0.nif',  'femalebody_0.nif'),
    ('leg',       'malebody_0.nif',  'femalebody_0.nif'),
    ('hand',      'malehands_0.nif', 'femalehands_0.nif'),
    ('foot',      'malefeet_0.nif',  'femalefeet_0.nif'),
    ('underwear', 'malebody_0.nif',  'femalebody_0.nif'),
]

#: (male NIF, female NIF, the body partitions it holds) for the Skyrim body, hands and feet.
_BODY_NIF_PARTITIONS = (
    ('malebody_0.nif', 'femalebody_0.nif', frozenset({32, 34, 38, 44})),
    ('malehands_0.nif', 'femalehands_0.nif', frozenset({33})),
    ('malefeet_0.nif', 'femalefeet_0.nif', frozenset({37})),
)

_HANDS_FEET_NIFS = frozenset({
    'malehands_0.nif', 'femalehands_0.nif',
    'malefeet_0.nif', 'femalefeet_0.nif',
})

# Bones NOTHING but a torso is weighted to.  Deliberately excludes upperarm and
# forearm (gauntlets and bracers reach them) and calf and thigh (boots do), so
# this only ever fires on geometry that genuinely spans the chest.
_TORSO_ONLY_BONES = ('spine', 'clavicle', 'neck')


def is_torso_skin(skin) -> bool:
    """True if this skin instance is weighted to the chest."""
    for bi in range(getattr(skin, 'num_bones', 0)):
        bone = skin.bones[bi]
        if bone is None:
            continue
        name = bytes(bone.name).rstrip(b'\x00').decode(
            'latin-1', errors='replace').lower()
        if any(k in name for k in _TORSO_ONLY_BONES):
            return True
    return False


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
    (the NiTexturingProperty was converted by walk_node earlier).

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
                    # The texture name is the author's label; the skin bones are
                    # what the geometry IS.  Nehrim ships 18 wearables whose
                    # TORSO skin carries a foot or hand texture — the Silverlight
                    # cuirass ("Foot:Body", 3321 verts, weighted to Spine/
                    # Clavicle/Neck/Pelvis, textured characters\imperial\female\
                    # footfemale) and the whole female Eyren set.  The keyword
                    # picked the hands/feet body NIF, which has no torso, so the
                    # stripped chest was never spliced back and the armour
                    # rendered as plates with see-through gaps.
                    #
                    # Spine/clavicle/neck are the only bones tested on purpose:
                    # a gauntlet legitimately reaches the forearm and a boot the
                    # calf, and including those produced 9 false positives on
                    # correctly-named vanilla gauntlets, while these three
                    # produced zero.
                    if nif_name in _HANDS_FEET_NIFS and is_torso_skin(skin):
                        nif_name = ('femalebody_0.nif' if is_female
                                    else 'malebody_0.nif')
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
                # Bones are already Skyrim names after retarget + remap_bone_names
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
    # matches the character body in-game.  Fall back to the vanilla body
    # auto-extracted from the SSE BSAs (SSE-format — sse_nif rebuilds it
    # into LE blocks).
    source = _SKYRIM_BODY_DIR_MODIFIED / nif_basename
    if not source.exists():
        from asset_convert.sources.skyrim_assets import get_body_nif_bytes
        source = get_body_nif_bytes(nif_basename)
    if source is None:
        print('[skin_replacement] WARNING: body mesh %s not found (no '
              'modified body and no SSE install for BSA extraction) — '
              'body-skin splice DISABLED, armor will have skin holes'
              % nif_basename)
        _BODY_GEOM_CACHE[nif_basename] = []
        return []

    try:
        from asset_convert.nif.sse_nif import read_nif
        body_data = read_nif(source)
    except Exception as exc:
        print('[skin_replacement] WARNING: failed to read body mesh %s (%s: '
              '%s) — body-skin splice DISABLED, armor will have skin holes'
              % (nif_basename, type(exc).__name__, exc))
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


def partition_skin_info(fill, female: bool) -> dict:
    """Splice input for a Morrowind piece's SkinFill, or {} without one.

    Shaped like `collect_skin_info`'s result, with no proximity cloud: the
    hidden partitions to draw from and the fill's `keep` test, which leaves
    only skin the actor's own uncovered body parts would show.
    See: docs/commentary/asset_convert_armor.md#morrowind-skin-fill
    """
    out = {}
    for male_nif, female_nif, held in (_BODY_NIF_PARTITIONS if fill else ()):
        if held & fill.partitions:
            out[female_nif if female else male_nif] = {
                'bones': set(), 'sections': [], 'section_verts': [],
                'partitions': held & fill.partitions,
                'keep': lambda pts, fill=fill: fill.keep(pts, female)}
    return out


def _partition_mask(src_geom, partitions) -> list:
    """Per vertex: whether it lies in a skin partition whose body part is in `partitions`."""
    skin = src_geom.skin_instance
    keep = [False] * src_geom.data.num_vertices
    blocks = skin.skin_partition.skin_partition_blocks if skin.skin_partition else []
    for block, info in zip(blocks, getattr(skin, 'partitions', [])):
        if info.body_part in partitions:
            for index in block.vertex_map:
                keep[int(index)] = True
    return keep


def _vertex_weights(skin_data, num_verts: int) -> list:
    """Per vertex {bone index: summed positive weight}, read from NiSkinData.bone_list."""
    weights: list = [{} for _ in range(num_verts)]
    for bi in range(skin_data.num_bones):
        bone = skin_data.bone_list[bi]
        for vwi in range(bone.num_vertices):
            vw = bone.vertex_weights[vwi]
            w = float(vw.weight)
            if vw.index < num_verts and w > 0.0:
                weights[vw.index][bi] = weights[vw.index].get(bi, 0.0) + w
    return weights


def _triangles(src_geom) -> list:
    """The shape's triangles as index triples; empty when it has none."""
    try:
        return [(tri.v_1, tri.v_2, tri.v_3) for tri in src_geom.data.triangles]
    except Exception:
        return []


#: Cloud points compared per chunk in the proximity clip, bounding its memory.
_CLOUD_CHUNK = 256


def _proximity_mask(src_geom, section_verts, threshold: float) -> list:
    """Per vertex: within `threshold` of the sections' point cloud; all True without one."""
    num_verts = src_geom.data.num_vertices
    if not section_verts:
        return [True] * num_verts
    cloud = np.array([p for sec in section_verts for p in sec], dtype=np.float32)
    t = src_geom.translation
    sk_pos = np.array([[v.x + t.x, v.y + t.y, v.z + t.z]
                       for v in src_geom.data.vertices], dtype=np.float32)
    min_dist_sq = np.full(num_verts, np.inf, dtype=np.float32)
    for start in range(0, len(cloud), _CLOUD_CHUNK):
        diff = sk_pos[:, np.newaxis, :] - cloud[np.newaxis, start:start + _CLOUD_CHUNK, :]
        np.minimum(min_dist_sq, (diff * diff).sum(axis=2).min(axis=1), out=min_dist_sq)
    return (min_dist_sq < threshold ** 2).tolist()


def clip_body_geom(src_geom, bi_to_name: dict, keep_bones: set,
                    section_verts: list = None, proximity_threshold: float = 6.0,
                    partitions=None, keep=None):
    """Clip a Skyrim body NiTriShape to the region matching removed body skin.

    `section_verts` (per-section vertex-position lists, post-retarget) keeps
    each vert within `proximity_threshold` of their combined cloud; without
    them every vert is kept. `partitions` further keeps only verts in those
    body partitions, and `keep(points)` only the points it passes.
    bi_to_name / keep_bones are not used for filtering.
    Returns (verts, normals, uvs, tris, kept_weights, bi_to_name) or None.
    """
    skin = getattr(src_geom, 'skin_instance', None)
    if skin is None or src_geom.data is None or getattr(skin, 'data', None) is None:
        return None
    all_tris = _triangles(src_geom)
    if not all_tris:
        return None
    keep_vert = _proximity_mask(src_geom, section_verts, proximity_threshold)
    if partitions:
        keep_vert = [a and b for a, b in zip(keep_vert, _partition_mask(src_geom, partitions))]
    if keep is not None:
        t = src_geom.translation
        pts = np.array([[v.x + t.x, v.y + t.y, v.z + t.z] for v in src_geom.data.vertices])
        keep_vert = [a and bool(b) for a, b in zip(keep_vert, keep(pts))]
    return _compact(src_geom.data, keep_vert, all_tris,
                    _vertex_weights(skin.data, src_geom.data.num_vertices),
                    bi_to_name)


def _compact(src_data, keep_vert: list, all_tris: list, vert_weights: list,
             bi_to_name: dict):
    """The kept verts and the triangles wholly inside them, reindexed; None when empty."""
    num_verts = src_data.num_vertices
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


# Occlusion trim of the spliced fill (2026-08-23).  The removed OB skin often
# includes the whole body section hidden inside the armor (Oblivion bakes the
# full upperbody into a cuirass), so the proximity clip keeps skyrim skin that
# is never visible -- measured on the iron cuirass, 71.9% of the fill's verts
# have armor directly outside them.  That hidden skin exists only to poke
# through the armor when animation moves the two meshes differently.
#
# Coverage is measured along the FILL VERTEX'S OWN NORMAL (armor centroid
# within FILL_COVER_ALONG outward, lateral offset under FILL_COVER_PERP) --
# a signed distance against the nearest armor triangle is useless here, the
# armor's plates and straps flip its sign vertex to vertex.  The covered set
# is then ERODED by one ring and only triangles fully inside it are dropped,
# so the kept fill always ends two triangle-rings UNDER the armor edge --
# never at it (no visible seam, no hole).  Iron cuirass: 49.7% of fill tris
# dropped, 120/221 verts kept.
# 2026-08-24 (in-game): the first cut was too aggressive — armor with view
# gaps (the female iron pauldron over the upper arm, open shirts) reads as
# "covering" to a ray test, and visible skin was cut away.  Better to cut
# not enough than too much: only skin buried DEEP under CLOSE armor goes,
# and the kept region is eroded by two rings, not one.
FILL_COVER_ALONG = (2.0, 3.5)   # armor this far outward along the vert normal
FILL_COVER_PERP = 1.0           # ...within this lateral distance of the ray
_FILL_COVER_EROSIONS = 2        # rings of covered verts spared at the border
_FILL_COVER_K = 24              # armor triangles examined per fill vert


def _armor_surface(armor_root):
    """KD-tree of the NIF's armor triangle centroids, or None.  Called AFTER
    strip_body_skin_geometry, so every trishape under the root is armor."""
    parts = []
    for block in armor_root.tree():
        if not isinstance(block, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
            continue
        if block.data is None or block.data.num_vertices == 0:
            continue
        try:
            tris = np.array([tuple(t) for t in block.data.get_triangles()],
                            dtype=np.int64)
        except Exception:
            continue
        if tris.size == 0:
            continue
        v = np.array([[p.x, p.y, p.z] for p in block.data.vertices])
        parts.append(v[tris].mean(axis=1))
    if not parts:
        return None
    try:
        from scipy.spatial import cKDTree
    except ImportError:
        return None
    cent = np.vstack(parts)
    return cKDTree(cent), cent


def drop_armor_covered_tris(clip_result, armor_surf, translation):
    """Drop fill triangles fully hidden under the armor surface.

    clip_result verts are body-local; `translation` is the body geom's
    translation (the same offset build_clipped_geom bakes in), which puts
    them in the armor's skeleton space for the occlusion test.
    """
    if armor_surf is None:
        return clip_result
    verts, normals, uvs, tris, kept_weights, bi_to_name = clip_result
    if not tris:
        return clip_result
    tree, cent = armor_surf
    v = np.asarray(verts, dtype=np.float64) + np.asarray(translation)
    t = np.asarray(tris, dtype=np.int64)

    # fill vertex normals from its own triangles (robust: the clip may have
    # been built from a source without normals)
    fn = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    n = np.zeros_like(v)
    for i in range(3):
        np.add.at(n, t[:, i], fn)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)

    k = min(_FILL_COVER_K, len(cent))
    _, ti = tree.query(v, k=k)
    if k == 1:
        ti = ti[:, None]
    rel = cent[ti] - v[:, None, :]
    along = np.einsum('pki,pi->pk', rel, n)
    perp = np.linalg.norm(rel - along[..., None] * n[:, None, :], axis=2)
    covered = ((along > FILL_COVER_ALONG[0]) & (along < FILL_COVER_ALONG[1])
               & (perp < FILL_COVER_PERP)).any(axis=1)

    # erode: covered verts within _FILL_COVER_EROSIONS rings of an exposed
    # one stay, so the kept fill always ends well under the armor edge
    eroded = covered.copy()
    edges = np.vstack([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    for _ in range(_FILL_COVER_EROSIONS):
        nbr_unc = np.zeros(len(v), bool)
        unc_a = ~eroded[edges[:, 0]]
        unc_b = ~eroded[edges[:, 1]]
        np.logical_or.at(nbr_unc, edges[:, 1], unc_a)
        np.logical_or.at(nbr_unc, edges[:, 0], unc_b)
        eroded = eroded & ~nbr_unc

    drop = eroded[t].all(axis=1)
    if not drop.any() or drop.all():
        # nothing hidden -- or everything (a fully-enclosed fill some armor
        # legitimately keeps as backing); leave the fill alone either way
        return clip_result
    keep_tris = t[~drop]
    used = sorted(set(int(i) for i in keep_tris.ravel()))
    remap = {old: new for new, old in enumerate(used)}
    return ([verts[i] for i in used],
            [normals[i] for i in used] if normals else [],
            [uvs[i] for i in used] if uvs else [],
            [(remap[a], remap[b], remap[c]) for a, b, c in keep_tris],
            [kept_weights[i] for i in used],
            bi_to_name)


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

    # Leave partition empty — splice_body_geometry will call regen_skin_partition
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


def apply_armor_offset(data, cfg, only_block_ids: set | None = None,
                       exclude_block_ids: set | None = None) -> None:
    """Shift, scale, and optionally tilt all skinned armor geometry vertices.

    cfg : ArmorOffsetConfig (from skyrim_overrides)
        dx/dy/dz   – translation applied last.
        sx/sy/sz   – independent per-axis scale around world origin.
        rotate     – front-to-back tilt in radians in the YZ plane around
                     the mesh centroid.
    only_block_ids / exclude_block_ids : set of id(block) values
        Restrict the offset to (or exempt from it) specific geometry blocks —
        used to give PRN-attached rigid pieces their own offset config
        (ARMOR_PIECE_OFFSETS_PRN) separate from FK-retargeted skinned pieces.

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

    from asset_convert.character.skin_retarget import manual_update_bind_position, m44_to_np as _sr_m44

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
                if only_block_ids is not None and id(block) not in only_block_ids:
                    continue
                if exclude_block_ids is not None and id(block) in exclude_block_ids:
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
        manual_update_bind_position(block, skin, skel_root)


def is_underwear_only(geom_name: bytes) -> bool:
    """Return True if this is an underwear-only overlay, not the body mesh."""
    low = geom_name.lower().rstrip(b'\x00')
    # Strip trailing digits and underscores: "MaleUnderwear_1" -> "maleunderwear"
    # But "MaleUnderwearBody:0" -> "maleunderwearbody" (not in the set -> kept)
    base = low.split(b':')[0].split(b'_')[0]
    return base in _UNDERWEAR_ONLY_NAMES


def _recompute_body_binds(geom, armor_root):
    """Recompute NiSkinData transforms for spliced body geometry.

    Mirrors manual_update_bind_position from skin_retarget: uses PyFFI's
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

    from asset_convert.character.skin_retarget import m44_to_np as _sr_m44, write_skin_transform

    try:
        G = _sr_m44(geom.get_transform(armor_root))
    except (ValueError, RuntimeError):
        G = np.eye(4, dtype=np.float64)

    G_inv = np.linalg.inv(G)
    write_skin_transform(skin_data.skin_transform, G_inv)

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
        write_skin_transform(skin_data.bone_list[i].skin_transform, B)


def _armor_bone_map(data) -> tuple:
    """(last non-None root, {node name: NiNode}) of the armor NIF."""
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
    return armor_root, bone_map


def _skyrim_skeletons() -> tuple:
    """(male, female) Skyrim bone maps; empty dicts when unavailable."""
    try:
        return (load_skeleton(paths.GENERATED / 'skeleton_bones_skyrim_male.json'),
                load_skeleton(paths.GENERATED / 'skeleton_bones_skyrim_female.json'))
    except Exception:
        return {}, {}


def _spliceable(src_geom) -> bool:
    """A dismember-skinned body shape with data that is not an underwear overlay."""
    skin = getattr(src_geom, 'skin_instance', None)
    if not isinstance(skin, NifFormat.BSDismemberSkinInstance) or src_geom.data is None:
        return False
    return not is_underwear_only(bytes(src_geom.name) if src_geom.name else b'')


def _attach_fill(new_geom, armor_root, fill_body_part: int) -> None:
    """Parent a fill shape, rebind it to the armor's bones, and slot its partitions."""
    old_n = armor_root.num_children
    armor_root.num_children = old_n + 1
    armor_root.children.update_size()
    armor_root.children[old_n] = new_geom
    _recompute_body_binds(new_geom, armor_root)
    try:
        geom_name_str = bytes(new_geom.name).rstrip(b'\x00').decode('latin-1', errors='replace')
        regen_skin_partition(new_geom, new_geom.skin_instance, geom_name_str)
    except Exception:
        pass
    fill_skin = new_geom.skin_instance
    if isinstance(fill_skin, NifFormat.BSDismemberSkinInstance):
        for pi in range(fill_skin.num_partitions):
            fill_skin.partitions[pi].body_part = fill_body_part


def _splice_nif(nif_name: str, info: dict, armor, sk_skel, fill_body_part) -> list:
    """Splice one Skyrim body NIF's clipped, trimmed shapes; the shapes added.

    `armor` is (root, bone map, occlusion surface).
    """
    armor_root, bone_map, armor_surf = armor
    section_verts = info.get('section_verts', []) or None
    spliced = []
    for src_geom, bi_to_name in load_body_geom(nif_name):
        if not _spliceable(src_geom):
            continue
        clip_result = clip_body_geom(src_geom, bi_to_name, info['bones'],
                                     section_verts=section_verts,
                                     proximity_threshold=3.8,
                                     partitions=info.get('partitions'),
                                     keep=info.get('keep'))
        if clip_result is None:
            continue
        clip_result = drop_armor_covered_tris(
            clip_result, armor_surf,
            (src_geom.translation.x, src_geom.translation.y,
             src_geom.translation.z))
        new_geom = build_clipped_geom(
            src_geom, clip_result, armor_root, bone_map,
            bytes(src_geom.name) if src_geom.name else b'BodyFill', sk_skel=sk_skel)
        if new_geom is not None:
            _attach_fill(new_geom, armor_root, fill_body_part)
            spliced.append(new_geom)
    return spliced


def splice_body_geometry(data, skin_info: dict, fill_body_part: int = 32,
                         fill=None, female: bool = False) -> int:
    """Attach clipped Skyrim body geometry into an armor NIF after retarget+rename.

    skin_info: `collect_skin_info`'s result. Without one, `fill` is a Morrowind
    piece's SkinFill (`partition_skin_info`, gendered by `female`).
    fill_body_part: the body part every fill partition takes; it MUST be a
    slot the item's ARMA claims or the engine culls the fill. A SkinFill then
    seats the piece's skinned armor outside the skin it added.
    See: docs/commentary/asset_convert_armor.md#body-splice-fill-partition
    See: docs/commentary/asset_convert_armor.md#morrowind-skin-fill
    """
    seat = not skin_info and fill
    skin_info = skin_info or partition_skin_info(fill, female)
    if not skin_info or not _PYFFI:
        return 0
    armor_root, bone_map = _armor_bone_map(data)
    if armor_root is None or not bone_map:
        return 0
    armor_shapes = [b for b in armor_root.tree() if isinstance(b, NifFormat.NiTriShape)
                    and b.skin_instance is not None and b.skin_instance.num_bones > 1]
    armor = (armor_root, bone_map, _armor_surface(armor_root))
    sk_skel_m, sk_skel_f = _skyrim_skeletons()
    spliced = []
    for nif_name, info in sorted(skin_info.items()):
        sk_skel = sk_skel_f if nif_name.lower().startswith('female') else sk_skel_m
        spliced += _splice_nif(nif_name, info, armor, sk_skel, fill_body_part)
    if seat:
        fill.seat(armor_shapes, spliced)
    return len(spliced)


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
                if tex.startswith(_SKIN_TEX_PREFIX) and \
                        _HAIR_TEX_MARKER not in tex:
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
            if '\\characters\\' in tex and _HAIR_TEX_MARKER not in tex:
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