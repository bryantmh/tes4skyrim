"""PyFFI-based Oblivion → Skyrim NIF converter.

Replaces mesh_convert/nif_converter.py for the asset_convert pipeline.
Uses PyFFI to read/write NIF files and handles all conversions in-place.
Source files are NEVER modified; converted output is written to dst_path only.

Supported:
  - NiTriStrips → NiTriShape
  - NiTexturingProperty → BSLightingShaderProperty + BSShaderTextureSet
  - NiNode root → BSFadeNode
  - Root rotation baking into children (non-skinned)
  - Inline tangents from NiBinaryExtraData
  - NiControllerManager string palette resolution
  - Havok collision conversion (bhkNiTriStripsShape→bhkCompressedMeshShape via cms_builder)
  - NiParticleSystem removal

Skip reason codes (printed in skip list at end of batch_convert):
  VER   — NIF version is unsupported (too old, unrecognised)
  SKY   — Already Skyrim version, copied as-is
  RD    — Read failed (corrupt/truncated/unknown blocks)
  WR    — Write failed (version-incompatible blocks like NiGeomMorpherController)
"""

import collections as _collections
import io as _io
import logging as _logging
import os
import shutil
import struct
import math
from pathlib import Path

import numpy as np

from .skyrim_overrides import (
    ARMOR_DEFAULT_BODY_PART,
    ARMOR_GEOMETRY_BODY_PARTS,
    ARMOR_GND_INV_MARKER_ROT_X,
    ARMOR_GND_INV_MARKER_ROT_Y,
    ARMOR_GND_INV_MARKER_ROT_Z,
    ARMOR_GND_INV_MARKER_ZOOM,
    ARMOR_PIECE_OFFSETS,
    BSX_FLAGS_ANIMATED,
    BSX_FLAGS_CONSTRAINED,
    BSX_FLAGS_DYNAMIC,
    BSX_FLAGS_STATIC,
    OBLIVION_TO_SKYRIM_BONE_MAP,
    SHIELD_INV_MARKER_ROT_X,
    SHIELD_INV_MARKER_ROT_Y,
    SHIELD_INV_MARKER_ROT_Z,
    SHIELD_INV_MARKER_ZOOM,
    WEAPON_INV_MARKER_ROT_X,
    WEAPON_INV_MARKER_ROT_Y,
    WEAPON_INV_MARKER_ROT_Z,
    WEAPON_INV_MARKER_ZOOM,
)
from .collision import (bake_node_transform_into_body, convert_all_collisions, hoist_collision,
                        remove_empty_collision_nodes, scale_constraint_pivots)

# Apply all PyFFI patches (time.clock fix, nif.xml condition fixes) before import
from . import pyffi_monkey_patch as _patch  # noqa: F401

try:
    from pyffi.formats.nif import NifFormat
    _PYFFI = True
    try:
        from pyffi.spells.nif.fix import SpellAddTangentSpace as _SpellAddTangentSpace
        from pyffi.spells.nif import NifToaster as _NifToaster
        _TANGENT_SPELL = True
    except ImportError:
        _TANGENT_SPELL = False
except ImportError:
    _PYFFI = False

# ---------------------------------------------------------------------------
# PyFFI warning capture (suppresses verbose output; counts by category)
# ---------------------------------------------------------------------------

# Per-worker warning message accumulator (reset at the start of each file).
# Each worker process has its own copy of this list.
_worker_warn_log: list = []


class _PyFFICapture(_logging.Handler):
    """Capture PyFFI log messages at WARNING+ without printing them."""

    def emit(self, record: _logging.LogRecord) -> None:  # type: ignore[override]
        _worker_warn_log.append(record.getMessage())


def _pyffi_capture_init() -> None:
    """Install silent PyFFI log capture.

    Called as a multiprocessing.Pool initializer (once per worker) and
    directly before single-worker processing.
    """
    global _worker_warn_log
    _worker_warn_log = []
    pyffi_log = _logging.getLogger('pyffi')
    pyffi_log.propagate = False
    pyffi_log.setLevel(_logging.WARNING)
    pyffi_log.handlers = []
    pyffi_log.addHandler(_PyFFICapture())


_WARN_CATEGORIES = {
    # SpellAddTangentSpace / NifToaster progress markers (INFO logged at WARNING)
    'spell_marker_tilde':          lambda m: m.startswith('~~~'),
    'spell_marker_dash':           lambda m: m.startswith('---'),
    'tangent_space_added':         lambda m: m.startswith('adding'),
    # Skin partition progress messages (from update_skin_partition)
    'skin_part_optimizing':        lambda m: m.startswith('optimizing'),
    'skin_part_imposing':          lambda m: m.startswith('imposing'),
    'skin_part_counted':           lambda m: m.startswith('counted'),
    'skin_part_creating':          lambda m: m.startswith('creating'),
    'skin_part_created':           lambda m: m.startswith('created'),
    'skin_part_merging':           lambda m: m.startswith('merging'),
    'skin_part_progress':          lambda m: m.startswith('skin '),
    # Geometry issues
    'improper_geometry':           lambda m: m.startswith('improper'),
    # Actual geometry/data errors
    'block_size_check':            lambda m: 'block size check' in m,
    'nan_in_vertices':             lambda m: 'nan' in m and 'vert' in m,
    'nan_generic':                 lambda m: 'nan' in m,
    # Collision / Havok
    'mopp_read_fail':              lambda m: 'bhkmoppbvtreeshape' in m or ('mopp' in m and ('fail' in m or 'error' in m)),
    'havok_block_invalid':         lambda m: 'bhk' in m and ('invalid' in m or 'not in nif' in m),
    'havok_shape':                 lambda m: 'bhkconvex' in m or 'bhkbox' in m or 'bhkcapsule' in m or 'bhksphere' in m,
    'havok_rigidbody':             lambda m: 'bhkrigid' in m,
    # Shader / texture
    'invalid_enum_extravectors':   lambda m: 'extravectorsflag' in m,
    'invalid_enum_shader':         lambda m: 'slsf' in m or ('shader_flags' in m and 'invalid' in m),
    'texture_path_issue':          lambda m: 'texture' in m and ('not found' in m or 'missing' in m or 'invalid' in m),
    # Skin / bones
    'skin_partition':              lambda m: 'niskinpartition' in m or 'skin partition' in m,
    'skin_data':                   lambda m: 'niskindata' in m or 'skin data' in m,
    'bone_invalid':                lambda m: 'bone' in m and ('invalid' in m or 'not found' in m or 'missing' in m),
    # Particle system
    'particle_system':             lambda m: 'nipsys' in m or 'particle system' in m,
    # Animation / controllers
    'controller_invalid':          lambda m: 'nicontroller' in m and ('invalid' in m or 'not in nif' in m),
    'controller_target':           lambda m: 'controller' in m and 'target' in m,
    'string_palette':              lambda m: 'nistringpalette' in m or 'string palette' in m or 'stringpalette' in m,
    'keyframe_data':               lambda m: 'nikeyframedata' in m or 'nitransformdata' in m or 'keyframe' in m,
    # Geometry block types
    'tristrips_data':              lambda m: 'nitristripsdata' in m,
    'trishape_data':               lambda m: 'nitrishapedata' in m,
    'geometry_morphdata':          lambda m: 'nimorphdata' in m or 'geommorph' in m,
    # Object palette / references
    'av_object_palette':           lambda m: 'avobject' in m or 'objectpalette' in m,
    'linked_block_invalid':        lambda m: 'linked block' in m,
    # Object tree
    'missing_from_nif_tree':       lambda m: 'missing from the nif tree' in m or 'not in nif tree' in m,
    # General value errors
    'value_out_of_range':          lambda m: 'out of range' in m,
    'invalid_nif_value':           lambda m: 'invalid' in m and ('nif' in m or 'value' in m),
    # Stream / parsing
    'unexpected_end_stream':       lambda m: 'unexpected end' in m or 'end of stream' in m,
    'unknown_block_type':          lambda m: 'unknown block type' in m or 'unrecognised block' in m,
}


def _categorize_pyffi_warnings(messages: list) -> dict:
    """Convert raw PyFFI WARNING messages to a {category: count} dict.

    Unrecognised messages are grouped by their leading block-type name so the
    summary shows detailed breakdowns rather than a single huge 'other' bucket.
    """
    c: _collections.Counter = _collections.Counter()
    for msg in messages:
        m = msg.lower()
        matched = False
        for cat, test in _WARN_CATEGORIES.items():
            if test(m):
                c[cat] += 1
                matched = True
                break
        if not matched:
            # Group by leading word (typically the NIF block type name)
            first_word = msg.split()[0].rstrip(':').lower() if msg.split() else 'unknown'
            c[f'type_{first_word}'] += 1
    return dict(c)


# ---------------------------------------------------------------------------
# CONSTANTS — edit these to change conversion behaviour
# ---------------------------------------------------------------------------

# Path segments (case-insensitive) to skip during batch conversion.
# Any NIF whose relative path contains one of these segments is excluded.
SKIP_PATHS = frozenset({
    'menus',
    'creatures',
})

_WORKER_COUNT = max(1, (os.cpu_count() or 4) - 3)

OUTPUT_VERSION       = 0x14020007  # Skyrim SE NIF version
OUTPUT_USER_VERSION  = 12
OUTPUT_USER_VERSION_2 = 83

NIF_FLAGS = 14  # Standard Skyrim NiAVObject flags (SelectiveUpdate bits 1-3)

# BSLightingShaderProperty flags (default preset)
# SLSF1: Specular | Receive_Shadows | Cast_Shadows | Own_Emit | Remappable | ZBufferTest
_SF1_SPECULAR           = 0x00000001
_SF1_RECIEVE_SHADOWS    = 0x00000100
_SF1_CAST_SHADOWS       = 0x00000200
_SF1_OWN_EMIT           = 0x00400000
_SF1_REMAPPABLE         = 0x00800000
_SF1_Z_BUFFER_TEST      = 0x80000000
SHADER_FLAGS_1 = (_SF1_SPECULAR | _SF1_RECIEVE_SHADOWS | _SF1_CAST_SHADOWS |
                  _SF1_OWN_EMIT | _SF1_REMAPPABLE | _SF1_Z_BUFFER_TEST)

# SLSF2: ZBufferWrite | VertexColors | EnvMapLightFade
_SF2_Z_BUFFER_WRITE     = 0x00000001
_SF2_VERTEX_COLORS      = 0x00000020
_SF2_ENV_MAP_LIGHT_FADE = 0x00008000
_SF2_DOUBLE_SIDED       = 0x00000010
SHADER_FLAGS_2 = _SF2_Z_BUFFER_WRITE | _SF2_VERTEX_COLORS | _SF2_ENV_MAP_LIGHT_FADE

# Supported source versions — anything else is skipped (not copied to output)
_SUPPORTED_VERSIONS = {
    0x14000004,  # Gamebryo v20.0.0.4 — primary Oblivion format
    0x14000005,  # Gamebryo v20.0.0.5
    0x0a020000,  # Gamebryo v10.2.0.0
    0x0a01006a,  # Gamebryo v10.1.0.106
    0x0a000100,  # NetImmerse v10.0.1.0
    0x0a000102,  # NetImmerse v10.0.1.2
    0x0a010065,  # Gamebryo v10.1.0.101
}

# Already-Skyrim versions — copy to output unchanged
_SKYRIM_VERSIONS = {
    0x14020007,  # Skyrim SE v20.2.0.7
}

# Havok unit scale factor (Oblivion → Skyrim).
# Applied to rigid body translations, center of mass, and primitive shape dimensions.
# Strip mesh vertices use a separate ÷7 factor (hardcoded in _ni_strips_to_packed).
# This matches the legacy copyover_legacy_nif_animations.py value of 0.1.
_HAVOK_SCALE = 0.1

# ---------------------------------------------------------------------------
# Oblivion → Skyrim attachment point (Prn) name remapping.
# Oblivion NIF files carry a NiStringExtraData block named 'Prn' on the root
# node that tells the engine which skeleton node to attach the mesh to.
# Skyrim uses different node names, so we remap them here.
# Source: Skyrim skeleton.nif node survey + legacy BONE_MAP.
# ---------------------------------------------------------------------------
_PRN_REMAP: dict[str, str] = {
    'BackWeapon':  'WeaponBack',    # 2H weapons, bows
    'SideWeapon':  'WeaponSword',   # 1H swords / generic 1H (refined by filename below)
    'Quiver':      'QUIVER',        # arrow quivers
    'Weapon':      'Weapon',        # already valid (keeps as-is)
    'Shield':      'SHIELD',        # shields
    'Torch':       'NPC L MagicNode [LMag]',
    # Shields: Oblivion uses the forearm bone; Skyrim has a dedicated SHIELD node
    'Bip01 L ForearmTwist': 'SHIELD',
    # Helmets: Oblivion attaches helmets to 'Bip01 Head'; Skyrim uses 'NPC Head [Head]'
    'Bip01 Head': 'NPC Head [Head]',
}

# Filename keyword → Skyrim Prn for 1H weapons (overrides 'WeaponSword' default).
# Oblivion uses 'SideWeapon' for all 1H weapons; Skyrim has per-type nodes.
# Checked against Skyrim skeleton.nif weapon node names.
_WEAPON_FILENAME_PRN: list[tuple[str, str]] = [
    ('dagger',    'WeaponDagger'),
    ('shortsword','WeaponDagger'),   # short swords mount on dagger node
    ('mace',      'WeaponMace'),
    ('waraxe',    'WeaponAxe'),
    ('axe',       'WeaponAxe'),
    ('club',      'WeaponMace'),     # clubs → mace node (closest 1H blunt)
    ('staff',     'WeaponStaff'),
    ('hammer',    'WeaponMace'),
    # 'sword', 'longsword', 'claymore', etc. → WeaponSword (default)
]

# Oblivion Prn values that indicate weapon/equipment (will also get BSInvMarker)
_WEAPON_PRN_VALUES = frozenset({
    'SideWeapon', 'BackWeapon', 'Weapon', 'WeaponSword', 'WeaponBack',
    'WeaponMace', 'WeaponAxe', 'WeaponDagger', 'WeaponStaff', 'QUIVER',
    'Quiver',
})


def _remap_prn(oblivion_prn: str, nif_filename: str) -> str:
    """Map an Oblivion Prn value to the correct Skyrim skeleton node name.

    For 'SideWeapon' (all Oblivion 1H weapons), refines to per-type node by
    looking for weapon type keywords in the NIF filename.
    """
    skyrim_prn = _PRN_REMAP.get(oblivion_prn, oblivion_prn)
    if oblivion_prn == 'SideWeapon':
        lower = nif_filename.lower()
        for keyword, prn in _WEAPON_FILENAME_PRN:
            if keyword in lower:
                return prn
    return skyrim_prn



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_identity(rotation):
    """Return True if a PyFFI Matrix33 is the identity matrix."""
    return (abs(rotation.m_11 - 1.0) < 1e-4 and abs(rotation.m_22 - 1.0) < 1e-4 and
            abs(rotation.m_33 - 1.0) < 1e-4 and abs(rotation.m_12) < 1e-4 and
            abs(rotation.m_13) < 1e-4 and abs(rotation.m_21) < 1e-4 and
            abs(rotation.m_23) < 1e-4 and abs(rotation.m_31) < 1e-4 and
            abs(rotation.m_32) < 1e-4)


def _identity_matrix():
    m = NifFormat.Matrix33()
    m.m_11 = 1.0; m.m_22 = 1.0; m.m_33 = 1.0
    return m


# --- Furniture marker conversion ------------------------------------------
#
# The full algorithm and data-verified ref/heading/z relations live in
# asset_convert/furniture_markers.py, SHARED with tes5_import's FURN record
# converter: the FURN MNAM bitmask indexes the NIF positions written here,
# so both sides must produce the identical seat list.
from .furniture_markers import (
    ENTRY_BEHIND as _ENTRY_BEHIND,
    ENTRY_FRONT as _ENTRY_FRONT,
    ENTRY_LEFT as _ENTRY_LEFT,
    ENTRY_RIGHT as _ENTRY_RIGHT,
    cluster_seats as _cluster_seats,
    extract_entries as _extract_furniture_entries,
    geometry_center_xy as _geometry_center_xy,
    origin_shift as _furniture_origin_shift,
)


def _convert_furniture_markers(markers, root):
    """Convert Oblivion BSFurnitureMarker blocks (entry points) into one
    Skyrim BSFurnitureMarkerNode (seat positions).

    Returns (frn, origin_shift) — origin_shift is the +z translation that
    re-origins the model to the vanilla floor-origin convention.  The
    engine anchors the seated actor to the REFR z (not the marker z), so
    the model must be wrapped in an inner NiNode translated by this amount
    and the importer lowers the REFRs to match (see furniture_markers.py).
    Returns (None, 0.0) if the markers contain no positions."""
    entries = _extract_furniture_entries(markers)
    if not entries:
        return None, 0.0
    shift = _furniture_origin_shift(entries)
    seats = _cluster_seats(entries, lambda: _geometry_center_xy(root))

    frn = NifFormat.BSFurnitureMarkerNode()
    frn.name = b'FRN'
    frn.num_positions = len(seats)
    frn.positions.update_size()
    for ci, seat in enumerate(seats):
        dst = frn.positions[ci]
        dst.offset.x = seat['x']
        dst.offset.y = seat['y']
        dst.offset.z = seat['z'] + shift  # re-origined coords (floor = 0)
        dst.heading = seat['heading']
        dst.animation_type = 2 if seat['sleep'] else 1
        ep = dst.entry_properties
        ep.front = 1 if seat['entry_flags'] & _ENTRY_FRONT else 0
        ep.behind = 1 if seat['entry_flags'] & _ENTRY_BEHIND else 0
        ep.right = 1 if seat['entry_flags'] & _ENTRY_RIGHT else 0
        ep.left = 1 if seat['entry_flags'] & _ENTRY_LEFT else 0
    return frn, shift


def _rewrite_tex_path(raw_bytes):
    """Prepend tes4\\ to a texture path that doesn't already have it."""
    path = raw_bytes.decode('utf-8', errors='replace')
    low = path.lower()
    # Oblivion paths start with "Textures\" — insert "tes4\" after that prefix
    if low.startswith('textures\\') and '\\tes4\\' not in low:
        path = path[:9] + 'tes4\\' + path[9:]
    elif not low.startswith('textures\\'):
        path = 'Textures\\tes4\\' + path
    return path


def _has_skin(data):
    """Return True if any block in the NIF is a NiSkinInstance."""
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if isinstance(block, NifFormat.NiSkinInstance):
                return True
    return False


def _extract_inline_tangents(ed, nv):
    """Extract (binormals, tangents) from a NiBinaryExtraData 'Tangent space...' block.

    Returns (list_of_bitangent_tuples, list_of_tangent_tuples) or (None, None).
    The binary layout is: nv*12 bytes binormals, then nv*12 bytes tangents.
    """
    raw = bytes(ed.binary_data)
    expected = nv * 12 * 2
    if len(raw) < expected:
        return None, None
    binormals = [struct.unpack_from('<fff', raw, i * 12) for i in range(nv)]
    tangents  = [struct.unpack_from('<fff', raw, nv * 12 + i * 12) for i in range(nv)]
    return binormals, tangents


def _set_tangents(ts_data, bitangents, tangents):
    """Write inline tangents/bitangents into NiTriShapeData.

    PyFFI Array elements must be mutated in-place (no item assignment).
    """
    nv = ts_data.num_vertices
    if len(tangents) != nv or len(bitangents) != nv:
        return
    ts_data.extra_vectors_flags = 16  # must set before update_size so arrays are sized
    ts_data.tangents.update_size()
    ts_data.bitangents.update_size()
    for i in range(nv):
        ts_data.tangents[i].x, ts_data.tangents[i].y, ts_data.tangents[i].z = tangents[i]
        ts_data.bitangents[i].x, ts_data.bitangents[i].y, ts_data.bitangents[i].z = bitangents[i]


# ---------------------------------------------------------------------------
# Node-level conversion
# ---------------------------------------------------------------------------

def _strip_dead_geometry_controllers(geom):
    """Remove NiGeomMorpherController / NiMaterialColorController from a
    geometry node's controller chain.

    Neither block type exists in vanilla Skyrim (0 of 17,216 meshes use
    NiGeomMorpherController — it's the Oblivion bow/flex morph system, which
    Skyrim replaces with skeletal animation on *skinned.nif bows).  Beyond
    being dead weight, PyFFI mis-serializes NiGeomMorpherController across the
    20.0→20.2 version bump: interpolator_weights is populated under the
    Oblivion layout but empty under the Skyrim layout, so the write aborts with
    an array-size mismatch (the entire weapons\\*\\bow.nif [WR] failure list).
    """
    prev = None
    ctrl = getattr(geom, 'controller', None)
    while ctrl is not None:
        nxt = getattr(ctrl, 'next_controller', None)
        if isinstance(ctrl, (NifFormat.NiGeomMorpherController,
                             NifFormat.NiMaterialColorController)):
            # Unlink this controller from the chain.
            if prev is None:
                geom.controller = nxt
            else:
                prev.next_controller = nxt
        else:
            prev = ctrl
        ctrl = nxt


def _strip_creature_bone_controllers(data):
    """Remove Oblivion-runtime controllers from creature NIF node chains.

    Oblivion creature skeletons carry an active (flags=12) but DATALESS
    NiTransformController on every bone plus a bhkBlendController on every
    ragdoll bone and a NiBSBoneLODController on Bip01 — all driven by
    Oblivion's engine at runtime.  Vanilla Skyrim creature skeletons ship
    NONE of these (bhkBlendController: 0 of all vanilla actor meshes; their
    only NiTransformControllers have a real interpolator+data — e.g. the
    dog's jaw/tongue idle).  Skyrim drives bones from the behavior graph, so
    these leftovers are at best dead weight and at worst engine hazards
    (an active controller with a null interpolator on every bone).

    Keeps NiTransformControllers that have an interpolator (real embedded
    animation).  Returns the number of controllers removed.
    """
    removed = 0
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not hasattr(block, 'controller'):
                continue
            prev = None
            ctrl = getattr(block, 'controller', None)
            while ctrl is not None:
                nxt = getattr(ctrl, 'next_controller', None)
                dead = isinstance(ctrl, (NifFormat.bhkBlendController,
                                         NifFormat.NiBSBoneLODController)) \
                    or (isinstance(ctrl, NifFormat.NiTransformController)
                        and getattr(ctrl, 'interpolator', None) is None)
                if dead:
                    if prev is None:
                        block.controller = nxt
                    else:
                        prev.next_controller = nxt
                    removed += 1
                else:
                    prev = ctrl
                ctrl = nxt
    return removed


def _resolve_source_texture(tex_rel, src_nif_path):
    """Map a rewritten texture path (textures\\tes4\\fire\\x\\y.dds) back to the
    extracted source file next to the source mesh tree
    (export/<esm>/textures/fire/x/y.dds).  Returns an absolute path or None."""
    if not src_nif_path:
        return None
    norm = src_nif_path.replace('/', os.sep).replace('\\', os.sep)
    key = os.sep + 'meshes' + os.sep
    i = norm.lower().rfind(key)
    if i < 0:
        return None
    tex_root = norm[:i] + os.sep + 'textures' + os.sep
    rel = tex_rel.replace('/', '\\')
    low = rel.lower()
    for prefix in ('textures\\tes4\\', 'textures\\'):
        if low.startswith(prefix):
            rel = rel[len(prefix):]
            break
    cand = tex_root + rel.replace('\\', os.sep)
    return cand if os.path.isfile(cand) else None


def _plan_flipbook_atlas(frame_rels, stats):
    """Validate NiFlipController frame textures and register an atlas-build
    job (executed by convert_nif, which knows the output tree).

    Returns (atlas_rel_path, n_padded, n_real) or None if the frames can't be
    resolved/decoded — the caller then falls back to a static first frame."""
    from . import flipbook
    if stats is None or len(frame_rels) < 2:
        return None
    src_nif = stats.get('_src_path', '')
    files = []
    dims = None
    for rel in frame_rels:
        f = _resolve_source_texture(rel, src_nif)
        if f is None:
            return None
        info = flipbook.probe_dds(f)
        if info is None:
            return None
        if dims is None:
            dims = info[:2]
        elif info[:2] != dims:
            return None
        files.append(f)
    # Atlas name: <frame dir>_flip.dds beside the frame folder, e.g.
    # textures\tes4\fire\fireopensmall\FireOpenSmall01.dds
    #   -> textures\tes4\fire\fireopensmall_flip.dds
    first = frame_rels[0].replace('/', '\\')
    parent = first.rsplit('\\', 1)[0]
    atlas_rel = parent.rstrip('\\') + '_flip.dds'
    n_real = len(files)
    n_pad = flipbook.next_pow2(n_real)
    jobs = stats.setdefault('_flipbook_atlases', {})
    jobs[atlas_rel.lower()] = {'atlas_rel': atlas_rel, 'files': files}
    return atlas_rel, n_pad, n_real


def _process_geometry(strips_or_shape, fix_textures, stats=None):
    """Convert a NiTriStrips or NiTriShape into a ready Skyrim NiTriShape.

    Returns the NiTriShape (may be a new object if input was NiTriStrips).
    NiTriStrips with controllers are NOT converted to NiTriShape (the controller
    still references the original node by block index; converting breaks the NIF).
    """
    # Drop Skyrim-incompatible geometry controllers (morph/material-color) first,
    # so strips that were only kept as strips because of a dead morpher can
    # convert to NiTriShape.
    _strip_dead_geometry_controllers(strips_or_shape)

    # Convert NiTriStrips → NiTriShape only when there are no controllers attached.
    # Strips with controllers (NiGeomMorpherController etc.) must stay as NiTriStrips.
    if isinstance(strips_or_shape, NifFormat.NiTriStrips):
        if strips_or_shape.controller is not None:
            # Keep as NiTriStrips — just update properties in-place
            ts = strips_or_shape
            src = strips_or_shape
        else:
            ts = strips_or_shape.get_interchangeable_tri_shape()
            src = strips_or_shape
    else:
        ts = strips_or_shape
        src = strips_or_shape

    ts.flags = NIF_FLAGS

    # Extract inline tangents from NiBinaryExtraData before clearing extra data
    bitangents = tangents = None
    for ed in list(src.extra_data_list):
        if (isinstance(ed, NifFormat.NiBinaryExtraData) and
                ed.name == b'Tangent space (binormal & tangent vectors)'):
            bitangents, tangents = _extract_inline_tangents(ed, src.data.num_vertices)
            break

    # Clear extra data list (Skyrim doesn't use Oblivion extra data)
    ts.num_extra_data_list = 0
    ts.extra_data_list.update_size()

    # consistency_flags = CT_STATIC (0x4000 = 16384)
    if hasattr(ts.data, 'consistency_flags'):
        ts.data.consistency_flags = 0x4000  # CT_STATIC

    # Reset ExtraVectorsFlags to 0 (Skyrim valid: 0=none, 16=has binormal+tangent).
    # Oblivion NIFs may store value 1 (binormals-only) which is invalid in Skyrim
    # and triggers a PyFFI enum warning, potentially causing corrupt tangent data.
    # _set_tangents() will set this to 16 when proper tangent data is available.
    if hasattr(ts.data, 'extra_vectors_flags'):
        ts.data.extra_vectors_flags = 0

    # Inject inline tangents from NiBinaryExtraData if available
    if tangents is not None and hasattr(ts.data, 'tangents'):
        _set_tangents(ts.data, bitangents, tangents)

    # Collect shader inputs from old Oblivion properties
    diffuse_path = b''
    has_double_sided = False
    alpha_prop = None
    emissive_r = 0.0
    emissive_g = 0.0
    emissive_b = 0.0
    flip_ctrl = None   # NiFlipController on NiTexturingProperty → animated fire quads

    for prop in src.properties:
        if isinstance(prop, NifFormat.NiTexturingProperty):
            if prop.has_base_texture and prop.base_texture.source:
                diffuse_path = prop.base_texture.source.file_name
            # Detect NiFlipController: Oblivion fire/effect quads animate through
            # multiple NiSourceTexture frames.  We'll move this to the NiTriShape
            # and use BSEffectShaderProperty so the frames survive conversion.
            ctrl = prop.controller
            while ctrl is not None:
                if isinstance(ctrl, NifFormat.NiFlipController):
                    flip_ctrl = ctrl
                    break
                ctrl = getattr(ctrl, 'next_controller', None)
        elif isinstance(prop, NifFormat.NiMaterialProperty):
            ec = prop.emissive_color
            emissive_r = ec.r
            emissive_g = ec.g
            emissive_b = ec.b
        elif isinstance(prop, NifFormat.NiStencilProperty):
            has_double_sided = True
        elif isinstance(prop, NifFormat.NiAlphaProperty):
            alpha_prop = prop

    # Clear old properties
    ts.num_properties = 0
    ts.properties.update_size()

    # Build BSShaderTextureSet
    tex_set = NifFormat.BSShaderTextureSet()
    tex_set.num_textures = 9
    tex_set.textures.update_size()

    if diffuse_path:
        diffuse = _rewrite_tex_path(diffuse_path) if fix_textures else diffuse_path.decode('utf-8', errors='replace')
        tex_set.textures[0] = diffuse.encode('utf-8')
        base = diffuse.rsplit('.', 1)[0] if '.' in diffuse else diffuse
        tex_set.textures[1] = (base + '_n.dds').encode('utf-8')

    # Build BSLightingShaderProperty
    shader = NifFormat.BSLightingShaderProperty()
    # Set shader flags via bit-struct attributes
    sf1 = shader.shader_flags_1
    sf1.slsf_1_specular = 1
    sf1.slsf_1_recieve_shadows = 1
    sf1.slsf_1_cast_shadows = 1
    sf1.slsf_1_own_emit = 1
    sf1.slsf_1_remappable_textures = 1
    sf1.slsf_1_z_buffer_test = 1

    sf2 = shader.shader_flags_2
    sf2.slsf_2_z_buffer_write = 1
    sf2.slsf_2_env_map_light_fade = 1
    if has_double_sided:
        sf2.slsf_2_double_sided = 1
    if ts.data.has_vertex_colors:
        sf2.slsf_2_vertex_colors = 1

    shader.texture_clamp_mode = 3   # WRAP_S | WRAP_T
    shader.uv_scale.u = 1.0
    shader.uv_scale.v = 1.0
    shader.texture_set = tex_set

    # Transfer emissive from NiMaterialProperty to Skyrim shader
    if emissive_r > 0.0 or emissive_g > 0.0 or emissive_b > 0.0:
        sf1.slsf_1_own_emit = 1
        shader.emissive_color.r = emissive_r
        shader.emissive_color.g = emissive_g
        shader.emissive_color.b = emissive_b
        shader.emissive_multiple = 1.0
    else:
        # No emissive — clear the own_emit flag (reduces overdraw on most objects)
        sf1.slsf_1_own_emit = 0

    # NiFlipController: fire/effect quads animate through multiple texture frames
    # using NiFlipController on the NiTexturingProperty.  NiFlipController is
    # DEAD in Skyrim (0/17,216 vanilla meshes) — the Skyrim equivalent is a
    # frame-strip atlas texture + BSEffectShaderPropertyFloatController stepping
    # "U Offset" (var 6) with stepped (CONST) keys.  We compose the flip frames
    # into a horizontal-strip DDS (asset_convert/flipbook.py; the job runs in
    # convert_nif which knows the output tree) and drive the shader with the
    # controller — this restores the flip-book animation in game AND in
    # NifSkope (its EffectFloatController is supported; NiPSys chains are not).
    # Fallback when frames can't be resolved: static first-frame texture.
    if flip_ctrl is not None:
        srcs = [s for s in flip_ctrl.sources if s is not None and s.file_name]
        frames = []
        for s in srcs:
            pth = s.file_name
            frames.append((_rewrite_tex_path(pth) if fix_textures
                           else pth.decode('utf-8', errors='replace')))
        atlas = _plan_flipbook_atlas(frames, stats) if len(frames) >= 2 else None
        if frames:
            effective_path = frames[0].encode('utf-8')
        else:
            effective_path = tex_set.textures[0] if diffuse_path else b''

        # Build BSEffectShaderProperty for the effect quad
        eff_shader = NifFormat.BSEffectShaderProperty()
        # PyFFI defaults UV Scale to (0,0) — that collapses EVERY UV to the
        # texture's top-left texel (usually transparent on flame textures) and
        # renders the geometry invisible.  Vanilla is offset (0,0), scale (1,1).
        eff_shader.uv_offset.u = 0.0
        eff_shader.uv_offset.v = 0.0
        eff_shader.uv_scale.u = 1.0
        eff_shader.uv_scale.v = 1.0
        esf1 = eff_shader.shader_flags_1
        esf1.slsf_1_own_emit = 1       # fire is self-illuminated
        esf1.slsf_1_z_buffer_test = 1
        esf2 = eff_shader.shader_flags_2
        esf2.slsf_2_z_buffer_write = 0  # effect quads don't write to depth
        if has_double_sided:
            esf2.slsf_2_double_sided = 1
        # SSE renders geometry invisible/black when the shader's Vertex Colors
        # flag disagrees with the mesh data (vanilla fire quads: data vcolors +
        # flags2 0x30).  Vertex alpha rides along (Oblivion uses vcol alpha to
        # dim layered flame quads, e.g. 0.25 on FireOpenLarge:1).
        if getattr(ts.data, 'has_vertex_colors', False):
            esf2.slsf_2_vertex_colors = 1
            esf1.slsf_1_vertex_alpha = 1
        eff_shader.source_texture = effective_path
        eff_shader.texture_clamp_mode = 3
        # emissive_multiple defaults to 0.0 → the flame quad renders BLACK.
        # Fire is self-illuminated; scale its emission to full.
        eff_shader.emissive_multiple = 1.0
        eff_shader.emissive_color.r = 1.0
        eff_shader.emissive_color.g = 1.0
        eff_shader.emissive_color.b = 1.0
        eff_shader.emissive_color.a = 1.0

        if atlas is not None:
            atlas_path, n_pad, n_real = atlas
            eff_shader.source_texture = atlas_path.encode('utf-8')
            eff_shader.uv_scale.u = 1.0 / n_pad   # show one frame of the strip
            # Frame duration: NiFlipController.delta, else spread over its
            # cycle, else the Oblivion default ~15fps.
            delta = float(getattr(flip_ctrl, 'delta', 0.0) or 0.0)
            if delta <= 0.0:
                span = float(flip_ctrl.stop_time) - float(flip_ctrl.start_time)
                delta = span / n_real if span > 0 else 1.0 / 15.0
            fc = NifFormat.BSEffectShaderPropertyFloatController()
            fc.flags = 0x48               # Active | Compute Scaled Time, loop
            fc.frequency = 1.0
            fc.phase = 0.0
            fc.start_time = 0.0
            fc.stop_time = n_real * delta
            fc.type_of_controlled_variable = 6   # U Offset
            fc.target = eff_shader
            interp = NifFormat.NiFloatInterpolator()
            interp.float_value = 0.0
            fdata = NifFormat.NiFloatData()
            kg = fdata.data
            kg.interpolation = 5          # CONST — stepped frames (no smear)
            kg.num_keys = n_real
            kg.keys.update_size()
            for k in range(n_real):
                kg.keys[k].time = k * delta
                kg.keys[k].value = k / float(n_pad)
            interp.data = fdata
            fc.interpolator = interp
            eff_shader.controller = fc

        ts.bs_properties[0] = eff_shader
    else:
        ts.bs_properties[0] = shader
    if alpha_prop is not None:
        ts.bs_properties[1] = alpha_prop

    # Set SKINNED shader flag when geometry has a skin instance.
    # Without this flag, Skyrim's character renderer ignores the mesh's bone
    # weights and renders it at the origin (near the character's feet).
    if getattr(ts, 'skin_instance', None) is not None:
        active_shader = ts.bs_properties[0]
        if isinstance(active_shader, NifFormat.BSLightingShaderProperty):
            active_shader.shader_flags_1.slsf_1_skinned = 1

    # Geometry data finalization: unknown_int_2 is the Material CRC field for
    # Skyrim (NIF 20.2.0.7 BSStream 83).  All vanilla Skyrim NIFs have this
    # as 0.  A non-zero value was incorrectly set before (confused with the
    # extra_vectors_flags field that controls tangent storage).
    if hasattr(ts, 'data') and ts.data is not None:
        ts.data.unknown_int_2 = 0

    return ts



def _process_controller_manager(node, palette):
    """Strip unsupported NiControllerManager sequences.

    Resolves node names from palette, removes blocks referencing the root,
    strips blocks with NiMaterialColorController/NiGeomMorpherController,
    and handles NiTransformInterpolator with empty data + zero translation.
    """
    mgr = node.controller
    root_name = node.name

    def _resolve_name(name_or_offset):
        """PyFFI may give bytes directly or a palette offset depending on version."""
        if isinstance(name_or_offset, int) and palette is not None:
            try:
                return palette.get_string(name_or_offset)
            except Exception:
                return b''
        return name_or_offset if isinstance(name_or_offset, bytes) else b''

    for seq in mgr.controller_sequences:
        key = 0
        while key < seq.num_controlled_blocks:
            blk = seq.controlled_blocks[key]
            node_name = _resolve_name(blk.node_name)

            # Remove blocks with empty or root node name
            if not node_name or node_name == root_name:
                seq.controlled_blocks.pop(key)
                seq.num_controlled_blocks -= 1
                continue

            # NiTransformInterpolator with empty data → remove the block.
            # These are placeholder accumulation-root blocks (or other no-op blocks)
            # that have no keyframe data.  In Skyrim they would cause the engine to
            # snap the target node to the interpolator's stored rotation/translation
            # even though no animation was intended, potentially repositioning parts
            # of the object incorrectly.  Just remove the block; do NOT zero the
            # target node's NIF-space transform (that is its correct rest pose).
            if isinstance(blk.interpolator, NifFormat.NiTransformInterpolator):
                interp = blk.interpolator
                if interp.data is None:
                    seq.controlled_blocks.pop(key)
                    seq.num_controlled_blocks -= 1
                    continue

            # Strip material and morph controllers (not supported in Skyrim)
            if isinstance(blk.controller, (NifFormat.NiMaterialColorController,
                                           NifFormat.NiGeomMorpherController)):
                seq.controlled_blocks.pop(key)
                seq.num_controlled_blocks -= 1
                continue

            key += 1


# ---------------------------------------------------------------------------
# Flame attachment for "fake" light NIFs
# ---------------------------------------------------------------------------

# Oblivion marks where a flame should burn with an empty FlameNode* NiNode and
# attaches a flame NIF there dynamically at runtime (firecandleflame.nif for
# candles/sconces/lamps, the torch flame for torches).  Skyrim has no such
# runtime attachment, so we CONVERT: the matching Oblivion flame NIF is run
# through the full converter once (cached per worker) and its converted
# subtree is grafted under each FlameNode marker.  This ships Oblivion's own
# flame visuals — flip-book quads + particle systems — instead of substituting
# Skyrim's Master-Particle-System flames.
#
# (A much earlier graft attempt crashed the engine — that crash was actually
# the PyFFI NiPSysData 66-vs-70-byte misalignment plus uv_scale=(0,0), both
# long fixed; the interim BSValueNode/AddonNode substitution is now removed.)

_FLAME_CACHE = {}   # (meshes_root_lower, flame_name) -> nif bytes | None
_FLAME_ATLAS_JOBS = {}  # same key -> flip-book atlas jobs from the conversion


def _flame_nif_for_host(src_path):
    """Which Oblivion flame NIF burns at this host's FlameNode markers."""
    name = os.path.basename(str(src_path)).lower()
    return 'firetorchsmall.nif' if 'torch' in name else 'firecandleflame.nif'


def _load_converted_flame(src_path, flame_name):
    """Convert meshes/fire/<flame_name> once per worker; return serialized
    Skyrim NIF bytes (deep-copies are made by re-reading), or None."""
    norm = str(src_path).replace('/', os.sep).replace('\\', os.sep)
    key = os.sep + 'meshes' + os.sep
    i = norm.lower().rfind(key)
    if i < 0:
        return None
    meshes_root = norm[:i + len(key)]
    cache_key = (meshes_root.lower(), flame_name)
    if cache_key in _FLAME_CACHE:
        return _FLAME_CACHE[cache_key]
    result = None
    flame_src = meshes_root + 'fire' + os.sep + flame_name
    if os.path.isfile(flame_src):
        try:
            fdata = NifFormat.Data()
            with open(flame_src, 'rb') as f:
                fdata.inspect(f)
                f.seek(0)
                fdata.read(f)
            fstats = _convert_nif(fdata, fix_textures=True, src_path=flame_src)
            buf = _io.BytesIO()
            fdata.write(buf)
            result = buf.getvalue()
            _FLAME_ATLAS_JOBS[cache_key] = fstats.get('_flipbook_atlases', {})
        except Exception:
            result = None
    _FLAME_CACHE[cache_key] = result
    return result


def _convert_flame_nodes(root_node, src_path, stats=None):
    """Graft the converted Oblivion flame NIF under every empty FlameNode*
    marker.  Modifies root_node's tree in-place; returns the graft count.

    Marker transform: TRANSLATION and SCALE are kept (Oblivion authored
    FlameNodes with ~2x scale that the attached flame NIF expects); ROTATION
    is reset to identity — the converted flame's own billboard wrappers carry
    the Skyrim −90°X axis correction, and a rotated parent would tip them.
    """
    flame_name = _flame_nif_for_host(src_path)
    flame_bytes = _load_converted_flame(src_path, flame_name)
    if flame_bytes is None:
        return 0
    count = 0

    def _visit(node):
        nonlocal count
        if not isinstance(node, NifFormat.NiNode):
            return
        for i in range(len(node.children)):
            child = node.children[i]
            if child is None:
                continue
            nm = getattr(child, 'name', b'') or b''
            if isinstance(nm, bytes):
                nm = nm.decode('latin1')
            if (nm.startswith('FlameNode')
                    and isinstance(child, NifFormat.NiNode)
                    and child.num_children == 0):
                # Deep-copy the converted flame by re-reading its bytes.
                fdata = NifFormat.Data()
                buf = _io.BytesIO(flame_bytes)
                fdata.inspect(buf)
                buf.seek(0)
                fdata.read(buf)
                froot = fdata.roots[0]
                kids = [c for c in froot.children if c is not None]
                child.rotation.set_identity()
                child.num_children = len(kids)
                child.children.update_size()
                for j, k in enumerate(kids):
                    child.children[j] = k
                count += 1
            else:
                _visit(child)

    _visit(root_node)

    if count:
        # The grafted particle systems need per-frame controller updates:
        # ensure BSXFlags bit 0 (Animated) on the host root.
        if hasattr(root_node, 'extra_data_list'):
            for ed in root_node.extra_data_list:
                if isinstance(ed, NifFormat.BSXFlags):
                    ed.integer_data |= 0x01
                    break
            else:
                bsx = NifFormat.BSXFlags()
                bsx.name = b'BSX'
                bsx.integer_data = 0x01
                root_node.num_extra_data_list += 1
                root_node.extra_data_list.update_size()
                for i in range(root_node.num_extra_data_list - 1, 0, -1):
                    root_node.extra_data_list[i] = root_node.extra_data_list[i - 1]
                root_node.extra_data_list[0] = bsx
        # Propagate the flame's flip-book atlas jobs so convert_nif builds
        # them into this host's output tree too (idempotent, exists-checked).
        if stats is not None:
            norm = str(src_path).replace('/', os.sep).replace('\\', os.sep)
            key = os.sep + 'meshes' + os.sep
            i = norm.lower().rfind(key)
            if i >= 0:
                cache_key = (norm[:i + len(key)].lower(), flame_name)
                jobs = _FLAME_ATLAS_JOBS.get(cache_key, {})
                if jobs:
                    stats.setdefault('_flipbook_atlases', {}).update(jobs)

    return count


# Vanilla Skyrim particle-modifier `order` values (slighthousefire.nif census).
# The engine processes modifiers in ascending order; the BS* rewrites and the
# injected LOD must slot into the same order bands or the system misbehaves.
_PSYS_ORDER = {
    'NiPSysAgeDeathModifier': 0,
    'BSPSysLODModifier': 1,
    'NiPSysEmitter': 1000,          # any *Emitter
    'NiPSysSpawnModifier': 1000,
    'BSPSysSimpleColorModifier': 3000,
    'NiPSysRotationModifier': 3000,
    'BSPSysScaleModifier': 3000,
    'NiPSysGravityModifier': 4000,
    'NiPSysPositionModifier': 6000,
    'NiPSysBoundUpdateModifier': 7000,
}


def _psys_order_for(mod):
    tn = type(mod).__name__
    if tn in _PSYS_ORDER:
        return _PSYS_ORDER[tn]
    if tn.endswith('Emitter'):
        return 1000
    return 3000


def _make_scale_ramp_from_growfade(gf):
    """Build a 60-entry BSPSysScaleModifier ramp reproducing a
    NiPSysGrowFadeModifier's grow-in/hold/fade-out over the particle lifetime.

    grow_time/fade_time are absolute seconds; without the emitter life span we
    treat them as fractions of a unit lifetime (Oblivion fire values are small,
    e.g. grow 0.0 fade 0.2).  Vanilla ramps peak ~1.0 and taper to ~0.1."""
    n = 60
    grow = max(float(getattr(gf, 'grow_time', 0.0)), 0.0)
    fade = max(float(getattr(gf, 'fade_time', 0.2)), 0.001)
    base = float(getattr(gf, 'base_scale', 1.0)) or 1.0
    # Interpret grow/fade as fractions of lifetime (clamp to sane band).
    grow_frac = min(max(grow, 0.0), 0.9)
    fade_frac = min(max(fade, 0.05), 0.9)
    scales = []
    for i in range(n):
        t = i / (n - 1)
        if grow_frac > 0 and t < grow_frac:
            s = t / grow_frac
        elif t > 1.0 - fade_frac:
            s = max((1.0 - t) / fade_frac, 0.1)
        else:
            s = 1.0
        scales.append(base * s)
    return scales


def _skyrimize_modifiers(node):
    """Rewrite a NiParticleSystem's modifier list to the Skyrim vocabulary so
    the SSE particle engine actually drives it (else particles are invisible).

    - NiPSysGrowFadeModifier → BSPSysScaleModifier (60-entry scale ramp)
    - NiPSysColorModifier    → BSPSysSimpleColorModifier
    - inject BSPSysLODModifier (universal in vanilla) if absent
    - keep emitter/spawn/rotation/gravity/position/bound-update/age-death as-is
    - set NiPSysModifier Name/Order/Target/Active on every modifier
    """
    old = [m for m in node.modifiers if m is not None]
    new = []
    have_lod = any(isinstance(m, NifFormat.BSPSysLODModifier) for m in old)
    have_age = any(isinstance(m, NifFormat.NiPSysAgeDeathModifier) for m in old)

    for m in old:
        if isinstance(m, NifFormat.NiPSysGrowFadeModifier):
            sm = NifFormat.BSPSysScaleModifier()
            ramp = _make_scale_ramp_from_growfade(m)
            sm.num_floats = len(ramp)
            sm.floats.update_size()
            for i, v in enumerate(ramp):
                sm.floats[i] = v
            new.append(sm)
        elif isinstance(m, NifFormat.NiPSysColorModifier):
            cm = NifFormat.BSPSysSimpleColorModifier()
            cm.fade_in_percent = 0.1
            cm.fade_out_percent = 0.25
            cm.color_1_start_percent = 0.0
            cm.color_1_end_percent = 0.15
            cm.color_2_start_percent = 1.0
            cm.color_2_end_percent = 0.5
            # Fire palette: warm→bright→cool, alpha in→hold→out.
            cols = [(1.0, 0.75, 0.5, 0.0), (1.0, 1.0, 1.0, 1.0), (1.0, 0.6, 0.3, 0.0)]
            for i, (r, g, b, a) in enumerate(cols):
                cm.colors[i].r = r; cm.colors[i].g = g
                cm.colors[i].b = b; cm.colors[i].a = a
            new.append(cm)
        else:
            new.append(m)

    if not have_lod:
        lod = NifFormat.BSPSysLODModifier()
        lod.uknown_float_1 = 0.033333
        lod.uknown_float_2 = 0.233333
        lod.uknown_float_3 = 0.2
        lod.uknown_float_4 = 1.0
        new.append(lod)
    if not have_age:
        age = NifFormat.NiPSysAgeDeathModifier()
        new.append(age)

    # Sort by vanilla processing order (stable).
    new.sort(key=_psys_order_for)

    # Set NiPSysModifier common fields on each.
    for i, m in enumerate(new):
        tn = type(m).__name__
        if not (getattr(m, 'name', None) or b''):
            m.name = ('%s:%d' % (tn, i)).encode('latin1')
        m.order = _psys_order_for(m)
        m.target = node
        m.active = True

    node.num_modifiers = len(new)
    node.modifiers.update_size()
    for i, m in enumerate(new):
        node.modifiers[i] = m


def _convert_particle_system(node, fix_textures):
    """Convert Oblivion NiParticleSystem properties to Skyrim BSEffectShaderProperty.

    NiParticleSystem inherits from NiAVObject like NiGeometry, so it has the same
    properties / bs_properties arrays.  Oblivion stores textures in NiTexturingProperty;
    Skyrim particle systems use BSEffectShaderProperty in bs_properties.

    NiPSysData is replaced with a fresh instance because UV2=11 and UV2=83 have
    different binary layouts (at BS202, per-particle arrays are NOT serialized).
    We preserve the original particle pool size in bs_max_vertices so emitters can
    spawn the correct number of particles at runtime.

    All modifiers are kept.  NiPSysGrowFadeModifier gains a base_scale field at
    UV2>=34 which defaults to 0.0 (invisible); we set it to 1.0.
    """

    diffuse_path = b''
    flip_ctrl = None
    alpha_prop = None

    for prop in node.properties:
        if isinstance(prop, NifFormat.NiTexturingProperty):
            if prop.has_base_texture and prop.base_texture.source:
                diffuse_path = prop.base_texture.source.file_name
            ctrl = prop.controller
            while ctrl is not None:
                if isinstance(ctrl, NifFormat.NiFlipController):
                    flip_ctrl = ctrl
                    break
                ctrl = getattr(ctrl, 'next_controller', None)
        elif isinstance(prop, NifFormat.NiAlphaProperty):
            alpha_prop = prop

    # Clear old Oblivion properties (NiTexturingProperty, NiMaterialProperty, etc.)
    node.num_properties = 0
    node.properties.update_size()

    # Replace Oblivion NiPSysData with a fresh Skyrim-compatible instance.
    # At UV2>=34 (BS202), NiPSysData per-particle arrays are NOT serialized —
    # only boolean flags and bs_max_vertices are written.  The particle pool
    # size moves from num_vertices (Oblivion) to bs_max_vertices (Skyrim).
    # bs_max_vertices MUST be non-zero or emitters crash trying to allocate
    # particles into an empty pool.
    if node.data is not None:
        old_data = node.data
        orig_count = max(old_data.num_vertices, 75)
        fresh = NifFormat.NiPSysData()
        # The Skyrim NiPSysData binary layout is hand-rolled by
        # pyffi_monkey_patch Patch 4 (PyFFI's own layout is structurally wrong
        # for #BS202#).  That serializer always emits an empty inline pool with
        # BS Max Vertices = max(num_vertices, bs_max_vertices, 75), so we just
        # record the pool size here; the per-vertex arrays are never written.
        fresh.bs_max_vertices = orig_count
        fresh.has_vertices = True
        fresh.has_normals = False
        node.data = fresh

    # Rebuild the modifier chain to the Skyrim vocabulary.  Oblivion-era
    # NiPSysGrowFadeModifier / NiPSysColorModifier are valid block types but the
    # SSE particle engine does NOT drive them (it expects the BS* equivalents),
    # so particles spawn at scale 0 / alpha 0 = INVISIBLE.  Every vanilla
    # Skyrim particle system also carries a BSPSysLODModifier (498/498 census)
    # without which the system culls at all distances.  _skyrimize_modifiers
    # converts GrowFade→BSPSysScaleModifier, Color→BSPSysSimpleColorModifier,
    # injects BSPSysLODModifier, and sets vanilla modifier `order` values.
    _skyrimize_modifiers(node)

    # Fix the emitter/update controller flags to the vanilla value.  Oblivion
    # ships flags=0x08 (Active only); vanilla Skyrim uses 0x48/0x4c (Active |
    # Compute Scaled Time, cycle bits preserved).  The Compute-Scaled-Time bit
    # (0x40) is default-true in Skyrim and drives the emitter's time base —
    # without it the birth-rate interpolator can evaluate to 0.  OR the bit in
    # rather than overwrite: Oblivion's NiPSysUpdateCtlr carries CLAMP cycle
    # bits (0x0c) that vanilla keeps (campfire01burning UpdateCtlr = 0x4c).
    ctrl = node.controller
    while ctrl is not None:
        if isinstance(ctrl, (NifFormat.NiPSysEmitterCtlr,
                             NifFormat.NiPSysUpdateCtlr,
                             NifFormat.NiPSysModifierActiveCtlr)):
            ctrl.flags |= 0x48
        ctrl = getattr(ctrl, 'next_controller', None)

    # Rewrite paths and derive effective texture from NiFlipController (first frame)
    # if present, else use the diffuse from NiTexturingProperty.
    # We do NOT attach the NiFlipController to the particle system: NiFlipController
    # targets NiTexturingProperty (now gone) and attaching it to NiParticleSystem
    # causes an invalid-target crash in Skyrim.  Static first-frame texture is used.
    if flip_ctrl is not None:
        for src_tex in flip_ctrl.sources:
            if src_tex is not None and src_tex.file_name:
                pth = src_tex.file_name
                pth = _rewrite_tex_path(pth) if fix_textures else pth.decode('utf-8', errors='replace')
                src_tex.file_name = pth.encode('utf-8')
        srcs = [s for s in flip_ctrl.sources if s is not None and s.file_name]
        effective_path = srcs[0].file_name if srcs else b''
    elif diffuse_path:
        ep = _rewrite_tex_path(diffuse_path) if fix_textures else diffuse_path.decode('utf-8', errors='replace')
        effective_path = ep.encode('utf-8')
    else:
        effective_path = b''

    # Build BSEffectShaderProperty (Skyrim particle shader) — flags match
    # vanilla fire (slighthousefire.nif Fireball): flags1 = z_buffer_test only,
    # flags2 = vertex_colors only; emissive_multiple 1.5 (fire glows; vanilla
    # uses 1.25–1.5).  soft_effect is NOT set on vanilla fire particle shaders.
    shader = NifFormat.BSEffectShaderProperty()
    # PyFFI defaults UV Scale to (0,0) — that collapses EVERY particle UV to
    # the texture's top-left texel (transparent on flame textures) = invisible
    # particles.  Vanilla: offset (0,0), scale (1,1).  THIS was the fire-
    # invisibility endgame bug (2026-07-05).
    shader.uv_offset.u = 0.0
    shader.uv_offset.v = 0.0
    shader.uv_scale.u = 1.0
    shader.uv_scale.v = 1.0
    shader.shader_flags_1.slsf_1_z_buffer_test = 1
    sf2 = shader.shader_flags_2
    sf2.slsf_2_z_buffer_write = 0       # particles don't write to depth buffer
    sf2.slsf_2_vertex_colors = 1        # particles modulate colour per-vertex
    shader.source_texture = effective_path
    # u32 packs clamp mode (low byte, 3 = WRAP_S|WRAP_T) with lighting
    # influence (byte 1, 0xFF) — every vanilla fire effect shader uses 0xFF03.
    shader.texture_clamp_mode = 0xFF03
    shader.emissive_multiple = 1.5
    shader.emissive_color.r = 1.0
    shader.emissive_color.g = 1.0
    shader.emissive_color.b = 1.0
    shader.emissive_color.a = 1.0

    node.bs_properties[0] = shader
    if alpha_prop is None:
        # Vanilla particles always have a NiAlphaProperty (additive: src=SRC_ALPHA
        # dst=ONE, flags 0x100d).  Without it the particles don't alpha-blend.
        alpha_prop = NifFormat.NiAlphaProperty()
        alpha_prop.flags = 0x100d
    else:
        # Oblivion sources often SHARE one NiAlphaProperty across several
        # particle systems.  Vanilla Skyrim never does — every PS carries its
        # own shader+alpha pair.  Clone so each system owns its alpha block.
        cloned = NifFormat.NiAlphaProperty()
        cloned.flags = alpha_prop.flags
        cloned.threshold = alpha_prop.threshold
        alpha_prop = cloned
    node.bs_properties[1] = alpha_prop


# Skyrim billboard axis correction (see the root-billboard handling for the
# full story): Oblivion mode-1 billboards keep local +Y up / +Z at camera;
# Skyrim keeps local +Z up / ±Y at camera.  Oblivion-authored flat-XY quads
# need this −90°-about-X rotation on their billboard node (byte-identical to
# vanilla campfire01burning "Plane05").
_BB_AXIS_FIX = ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0))


def _compose_axis_fix(rot):
    """rot ← rot · R_fix (PyFFI row-vector convention) in place."""
    m = [[rot.m_11, rot.m_12, rot.m_13],
         [rot.m_21, rot.m_22, rot.m_23],
         [rot.m_31, rot.m_32, rot.m_33]]
    f = _BB_AXIS_FIX
    r = [[sum(m[i][k] * f[k][j] for k in range(3)) for j in range(3)]
         for i in range(3)]
    rot.m_11, rot.m_12, rot.m_13 = r[0]
    rot.m_21, rot.m_22, rot.m_23 = r[1]
    rot.m_31, rot.m_32, rot.m_33 = r[2]


def _wrap_in_billboard(child, bb_mode):
    """Wrap a geometry block in a fresh NiBillboardNode carrying the Skyrim
    axis correction (vanilla campfire pattern: BSFadeNode → NiBillboardNode
    → NiTriShape)."""
    bb = NifFormat.NiBillboardNode()
    bb.name = (child.name or b'') + b'-Billboard'
    bb.flags = NIF_FLAGS
    bb.billboard_mode = bb_mode
    _compose_axis_fix(bb.rotation)
    bb.num_children = 1
    bb.children.update_size()
    bb.children[0] = child
    return bb


def _skyrimize_billboard(bb):
    """Convert a (non-root) Oblivion NiBillboardNode for Skyrim.

    - Contains a particle system anywhere in its subtree → DEMOTE to a plain
      NiNode (a billboarding ancestor spins the emitters) and wrap its direct
      geometry children in fresh axis-corrected billboard nodes.
    - Pure geometry billboard → keep it, but compose the Skyrim axis
      correction into its rotation (Oblivion billboards are authored identity
      over flat-XY quads; Skyrim's up/facing axes differ).
    """
    bb_mode = int(getattr(bb, 'billboard_mode', 1)) or 1
    has_psys = any(isinstance(b, NifFormat.NiParticleSystem)
                   for b in bb.tree())
    if not has_psys:
        _compose_axis_fix(bb.rotation)
        return bb
    plain = NifFormat.NiNode()
    plain.name = bb.name
    plain.flags = NIF_FLAGS
    plain.translation.x = bb.translation.x
    plain.translation.y = bb.translation.y
    plain.translation.z = bb.translation.z
    plain.rotation.m_11 = bb.rotation.m_11; plain.rotation.m_12 = bb.rotation.m_12; plain.rotation.m_13 = bb.rotation.m_13
    plain.rotation.m_21 = bb.rotation.m_21; plain.rotation.m_22 = bb.rotation.m_22; plain.rotation.m_23 = bb.rotation.m_23
    plain.rotation.m_31 = bb.rotation.m_31; plain.rotation.m_32 = bb.rotation.m_32; plain.rotation.m_33 = bb.rotation.m_33
    plain.scale = bb.scale
    plain.num_extra_data_list = bb.num_extra_data_list
    plain.extra_data_list.update_size()
    for j, ed in enumerate(bb.extra_data_list):
        plain.extra_data_list[j] = ed
    if bb.controller is not None:
        plain.controller = bb.controller
    if getattr(bb, 'collision_object', None) is not None:
        plain.collision_object = bb.collision_object
        plain.collision_object.target = plain
    plain.num_children = bb.num_children
    plain.children.update_size()
    for j in range(bb.num_children):
        c = bb.children[j]
        if isinstance(c, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
            c = _wrap_in_billboard(c, bb_mode)
        plain.children[j] = c
    # Particle modifiers in the subtree may reference the OLD billboard node
    # (emitter_object / gravity_object) — remap to the replacement or the ref
    # dangles ("block is missing from the nif tree") and the sim breaks.
    for blk in plain.tree():
        for attr in ('emitter_object', 'gravity_object'):
            if getattr(blk, attr, None) is bb:
                setattr(blk, attr, plain)
    return plain


def _walk_node(parent, node, fix_textures, stats):
    """Recursively process a node and its children.

    Returns the (possibly replaced) node that should occupy the parent's slot.
    """
    if node is None:
        return None

    # SecretBigger* geometry: Oblivion artists placed tiny 3-vertex triangles
    # far below the model origin (e.g. Z=-1725) to artificially expand the
    # bounding sphere so the mesh loads from further away.  Skyrim's BSFadeNode
    # uses a different LOD system and doesn't need this trick.  In the converted
    # output these triangles appear as visible floating geometry underground,
    # causing the "mispositioned" visual bug.  Strip them.
    node_name = getattr(node, 'name', b'') or b''
    if node_name.startswith(b'SecretBigger') or node_name.startswith(b'Secret Bigger'):
        return None

    # EditorMarker geometry: Oblivion ships editor-only marker meshes (e.g.
    # the pyramid inside fire NIFs) hidden at runtime via the node's hidden
    # flag.  Our conversion clobbers node flags with NIF_FLAGS (visible), so
    # the marker shows in game as an untextured black shape.  Vanilla Skyrim
    # NIFs don't carry editor markers in these objects — strip them.
    if node_name.startswith(b'EditorMarker'):
        return None

    # NiParticleSystem: convert to Skyrim-compatible format.
    # NiPSysData binary layout differs between UV2=11 and UV2=83, causing
    # "Block size check failed" errors when Skyrim tries to read the raw
    # Oblivion data.  _convert_particle_system() replaces the data block
    # with a fresh empty instance and converts shader properties.
    if isinstance(node, NifFormat.NiParticleSystem):
        _convert_particle_system(node, fix_textures)
        node.flags = NIF_FLAGS
        return node

    # NiDynamicEffect subtypes: handle by type.
    # NiTextureEffect (projected texture environment mapping) has a completely
    # different Skyrim rendering path — strip it.
    # NiDirectionalLight would override Skyrim's day/night cycle — strip it.
    # NiPointLight, NiSpotLight, NiAmbientLight are valid Skyrim NIF block types
    # and may contribute to world-space mesh illumination; keep them.
    # Note: the root NiNode's own effects array is cleared during NiNode→BSFadeNode
    # conversion; this branch handles dynamic-effect nodes that appear in the
    # children array.
    if isinstance(node, NifFormat.NiDynamicEffect):
        if isinstance(node, (NifFormat.NiTextureEffect, NifFormat.NiDirectionalLight)):
            return None
        # NiPointLight / NiSpotLight / NiAmbientLight
        node.flags = NIF_FLAGS
        return node

    # Geometry conversion
    if isinstance(node, (NifFormat.NiTriStrips, NifFormat.NiTriShape)):
        ts = _process_geometry(node, fix_textures, stats)
        # Only count as strips_fixed if we actually converted (not kept as NiTriStrips)
        if isinstance(node, NifFormat.NiTriStrips) and not isinstance(ts, NifFormat.NiTriStrips):
            stats['strips_fixed'] += 1
        stats['properties_converted'] += 1
        # Track old→new mapping so NiDefaultAVObjectPalette can be fixed later
        if ts is not node:
            stats.setdefault('_block_map', {})[id(node)] = ts
        return ts

    # NiNode and descendants
    if isinstance(node, NifFormat.NiNode):
        node.flags = NIF_FLAGS

        # Strip FX effects
        if hasattr(node, 'num_effects') and node.num_effects > 0:
            node.num_effects = 0
            node.effects.update_size()

        # Handle NiControllerManager
        if (node.controller is not None and
                isinstance(node.controller, NifFormat.NiControllerManager)):
            # Find string palette if present
            palette = None
            for block in node.tree():
                if isinstance(block, NifFormat.NiStringPalette):
                    palette = block.palette
                    break
            _process_controller_manager(node, palette)

        # Recurse into children.  Non-root NiBillboardNodes get the Skyrim
        # billboard treatment on the way back up (axis correction, or demotion
        # when they contain particle emitters — e.g. firecandleflame.nif nests
        # its emitter under two levels of billboards).
        for i in range(len(node.children)):
            result = _walk_node(node, node.children[i], fix_textures, stats)
            if isinstance(result, NifFormat.NiBillboardNode):
                result = _skyrimize_billboard(result)
            node.children[i] = result

        # Compact: remove None slots left by stripped nodes (NiParticleSystem,
        # NiDynamicEffect, SecretBigger, etc.)  PyFFI writes None refs as -1
        # (null) but a non-zero num_children with null slots can confuse Skyrim.
        keep = [c for c in node.children if c is not None]
        if len(keep) < node.num_children:
            node.num_children = len(keep)
            node.children.update_size()
            for _ci, _cv in enumerate(keep):
                node.children[_ci] = _cv

    return node



def _tree_is_animated(root):
    """True if anything in the tree needs per-frame controller updates:
    a NiParticleSystem, or any block with a NiTimeController attached.

    Vanilla census (400 particle-bearing Skyrim meshes): 399/400 set BSXFlags
    bit 0 (Animated) — the exception is a trailer camera rig.  Without bit 0
    the engine never ticks the controllers, so particles never emit (the file
    is valid but the fire/effect is INVISIBLE)."""
    for b in root.tree():
        if isinstance(b, NifFormat.NiParticleSystem):
            return True
        if getattr(b, 'controller', None) is not None:
            return True
    return False


def _add_bsx_flags(root, has_constraints=False):
    """Add BSXFlags extra data to root if collision is present anywhere in the
    tree, or if the tree is animated (particles / time controllers).

    Value selection (priority order):
      constrained dynamic (signs)  → 0xCA  BSX_FLAGS_CONSTRAINED
      animated (doors/activators)  → 0x8B  BSX_FLAGS_ANIMATED
      dynamic clutter (mass > 0)   → 0xC2  BSX_FLAGS_DYNAMIC
      static                       → 0x82  BSX_FLAGS_STATIC
    plus bit 0 (Animated) OR'd in whenever the tree has particle systems or
    time controllers (0x82→0x83, 0xC2→0xC3 — both appear in the vanilla
    census).  With no collision at all, animated trees get plain 0x01 (the
    most common vanilla value for collisionless particle meshes).

    The DYNAMIC bit (0x40) is critical for any object with mass > 0:
    without it Skyrim uses a coarse bounding sphere for the activation/grab
    shell instead of the actual collision shape, and applies extra drag when
    the object is carried.

    BSInvMarker (if present) must remain first — BSXFlags goes immediately after it.
    """
    def _has_any_collision(node):
        if node is None:
            return False
        if getattr(node, 'collision_object', None) is not None:
            return True
        if hasattr(node, 'children'):
            for child in node.children:
                if _has_any_collision(child):
                    return True
        return False

    def _has_dynamic_body(node):
        """Return True if any rigid body in the tree has mass > 0."""
        if node is None:
            return False
        co = getattr(node, 'collision_object', None)
        if co is not None:
            rb = getattr(co, 'body', None)
            if rb is not None and getattr(rb, 'mass', 0) > 0:
                return True
        if hasattr(node, 'children'):
            for child in node.children:
                if _has_dynamic_body(child):
                    return True
        return False

    tree_animated = _tree_is_animated(root)
    if not _has_any_collision(root):
        if not tree_animated:
            return
        bsx_value = 0x01  # Animated only — vanilla collisionless particle meshes
    else:
        root_is_animated = (
            root.controller is not None and
            isinstance(root.controller, NifFormat.NiControllerManager)
        )
        if has_constraints:
            bsx_value = BSX_FLAGS_CONSTRAINED
        elif root_is_animated:
            bsx_value = BSX_FLAGS_ANIMATED
        elif _has_dynamic_body(root):
            bsx_value = BSX_FLAGS_DYNAMIC
        else:
            bsx_value = BSX_FLAGS_STATIC
        if tree_animated:
            bsx_value |= 0x01  # engine must tick controllers/particles

    if hasattr(root, 'extra_data_list'):
        for ed in root.extra_data_list:
            if isinstance(ed, NifFormat.BSXFlags):
                # Already present — just make sure the Animated bit is right.
                if tree_animated:
                    ed.integer_data |= 0x01
                return

    bsx = NifFormat.BSXFlags()
    bsx.name = b'BSX'
    bsx.integer_data = bsx_value
    root.num_extra_data_list += 1
    root.extra_data_list.update_size()

    # Find insertion point: after BSInvMarker (index 0 if present), else at index 0.
    insert_at = 0
    for i in range(root.num_extra_data_list - 1):
        if type(root.extra_data_list[i]).__name__ == 'BSInvMarker':
            insert_at = i + 1
            break

    # Shift elements from insert_at onward to make room
    for i in range(root.num_extra_data_list - 1, insert_at, -1):
        root.extra_data_list[i] = root.extra_data_list[i - 1]
    root.extra_data_list[insert_at] = bsx



# ---------------------------------------------------------------------------
# Main per-file conversion
# ---------------------------------------------------------------------------


def _sanitize_geometry_data(data):
    """Zero out non-finite floats in render geometry.

    A handful of Oblivion source meshes ship NaN data (anvildooruc02.nif has
    9 NaN UVs, middlecandlestickfloor03fake.nif has 2 — one mesh in each of
    the AnvilMagesGuild / AnvilCastlePrivateQuarters cells, whose loads
    crashed with no crash log).  Oblivion's renderer tolerated non-finite
    mesh data; Skyrim SE dies at cell load.

    Non-finite UVs are zeroed; non-finite vertices move to the mesh's finite
    centroid (collapses the triangle instead of stretching it to the origin);
    non-finite normals/tangents/bitangents become +Z; a non-finite bound
    sphere is recomputed after vertices are fixed.

    Returns the number of components fixed.
    """
    fixed = 0
    for block in data.blocks:
        if not isinstance(block, NifFormat.NiGeometryData):
            continue

        if getattr(block, 'has_vertices', False) and block.num_vertices:
            bad_verts = [v for v in block.vertices
                         if not (math.isfinite(v.x) and math.isfinite(v.y)
                                 and math.isfinite(v.z))]
            if bad_verts:
                finite = [(v.x, v.y, v.z) for v in block.vertices
                          if math.isfinite(v.x) and math.isfinite(v.y)
                          and math.isfinite(v.z)]
                if finite:
                    cx = sum(p[0] for p in finite) / len(finite)
                    cy = sum(p[1] for p in finite) / len(finite)
                    cz = sum(p[2] for p in finite) / len(finite)
                else:
                    cx = cy = cz = 0.0
                for v in bad_verts:
                    v.x, v.y, v.z = cx, cy, cz
                    fixed += 1
                try:
                    block.update_center_radius()
                except Exception:
                    pass

        for attr in ('normals', 'tangents', 'bitangents'):
            for v in getattr(block, attr, []):
                if not (math.isfinite(v.x) and math.isfinite(v.y)
                        and math.isfinite(v.z)):
                    v.x, v.y, v.z = 0.0, 0.0, 1.0
                    fixed += 1

        for uv_set in getattr(block, 'uv_sets', []):
            for uv in uv_set:
                if not math.isfinite(uv.u):
                    uv.u = 0.0
                    fixed += 1
                if not math.isfinite(uv.v):
                    uv.v = 0.0
                    fixed += 1

        for c in getattr(block, 'vertex_colors', []):
            for ch in ('r', 'g', 'b', 'a'):
                if not math.isfinite(getattr(c, ch)):
                    setattr(c, ch, 1.0)
                    fixed += 1

        center, radius = getattr(block, 'center', None), getattr(block, 'radius', None)
        if center is not None and radius is not None:
            if not (math.isfinite(center.x) and math.isfinite(center.y)
                    and math.isfinite(center.z) and math.isfinite(radius)):
                try:
                    block.update_center_radius()
                    fixed += 1
                except Exception:
                    center.x = center.y = center.z = 0.0
                    block.radius = 100.0
                    fixed += 1
    return fixed


def _resolve_palette_strings(data):
    """Resolve StringOffset fields in NiControllerSequence controlled_blocks.

    In Oblivion NIF format (UV2=11), NiControllerSequence.controlled_blocks store
    node_name, controller_type, variable_1, variable_2 as integer offsets into a
    per-sequence NiStringPalette.  The corresponding string fields are empty.

    In Skyrim NIF format (UV2=83), these become direct string fields (no palette).
    PyFFI reads the version at write time and uses the offset path when UV2<=34,
    producing empty strings in the output for any field not backed by an offset.

    This function reads the offsets while still at Oblivion version and writes the
    resolved strings into the string fields.  After the version upgrade the string
    fields are authoritative and the offset fields are ignored, so all node_name
    values survive correctly into the Skyrim output.

    Without this fix, Skyrim reads empty node_name strings from every controlled_block
    and cannot find the animation target nodes, causing a null-deref crash on NIF load.
    """
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not isinstance(block, NifFormat.NiControllerSequence):
                continue
            sp = getattr(block, 'string_palette', None)
            if sp is None:
                continue
            pal = getattr(sp, 'palette', None)
            if pal is None:
                continue
            raw = bytes(pal.palette) if hasattr(pal, 'palette') else b''
            if not raw:
                continue

            def _get(offset):
                if offset < 0 or offset >= len(raw):
                    return b''
                end = raw.find(b'\x00', offset)
                return raw[offset:end] if end >= 0 else raw[offset:]

            for cb in block.controlled_blocks:
                nn_off = getattr(cb, 'node_name_offset', -1)
                ct_off = getattr(cb, 'controller_type_offset', -1)
                v1_off = getattr(cb, 'variable_1_offset', -1)
                v2_off = getattr(cb, 'variable_2_offset', -1)
                pt_off = getattr(cb, 'property_type_offset', -1)
                if nn_off >= 0:
                    cb.node_name = _get(nn_off)
                if ct_off >= 0:
                    cb.controller_type = _get(ct_off)
                if v1_off >= 0:
                    cb.variable_1 = _get(v1_off)
                if v2_off >= 0:
                    cb.variable_2 = _get(v2_off)
                if pt_off >= 0:
                    # property_type field may not always exist at all versions
                    try:
                        cb.property_type = _get(pt_off)
                    except AttributeError:
                        pass


# ---------------------------------------------------------------------------
# Armor / clothing NIF helpers
# ---------------------------------------------------------------------------

def _strip_gnd_skin(data):
    """Strip NiSkinInstance from _gnd ground-model files.

    Oblivion _gnd files may use cloth physics bones (Bone01, Bone02, …) for
    ragdoll simulation of dropped items.  Skyrim cannot find these bones in any
    skeleton, so the mesh fails to load and shows as a red question mark.

    Stripping the NiSkinInstance leaves vertices in their bind pose, which is
    the correct rest pose for a static ground display model (the same pose the
    item would show in when lying on the ground).  After stripping, orphaned
    NiSkinData / NiSkinPartition blocks become unreachable and are not written.
    """
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if isinstance(block, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
                skin = getattr(block, 'skin_instance', None)
                if skin is not None and not isinstance(skin, NifFormat.BSDismemberSkinInstance):
                    block.skin_instance = None


# ---------------------------------------------------------------------------
# Skin replacement — delegated to asset_convert.skin_replacement module
# ---------------------------------------------------------------------------
from .skin_replacement import (collect_skin_info, strip_body_skin_geometry, splice_body_geometry, apply_armor_offset)


def _get_armor_piece_type(src_path: str) -> str:
    """Classify an armor NIF path into a piece-type key for ARMOR_PIECE_OFFSETS.

    Detection is based solely on the NIF filename stem (case-insensitive).  This
    covers the standard Oblivion naming convention (cuirass_0.nif, boots_0.nif,
    pants.nif, etc.) as well as common clothing names (shirt, robe, pants, shoe).
    Returns one of: 'cuirass', 'greaves', 'boots', 'gauntlets', 'helmet', 'shield',
    or 'default' (treated identically to 'cuirass' by ARMOR_PIECE_OFFSETS).
    """
    stem = Path(src_path).stem.lower()
    if any(k in stem for k in ('boot', 'shoe', 'sandal', 'slipper', 'clog')):
        return 'boots'
    if any(k in stem for k in ('gauntlet', 'glove', 'bracer', 'vambrace', 'handwrap')):
        return 'gauntlets'
    if any(k in stem for k in ('helm', 'hood', 'hat', 'coif', 'circlet', 'crown',
                                 'mask', 'cap', 'cowl')):
        return 'helmet'
    if any(k in stem for k in ('greave', 'pant', 'trouser', 'lowerbody', 'skirt',
                                 'kilt', 'loincloth', 'shorts')):
        return 'greaves'
    if any(k in stem for k in ('shield', 'buckler', 'targe')):
        return 'shield'
    # Cuirass / shirt / robe / default upper-body
    return 'cuirass'


def _remap_bone_names(data) -> int:
    """Rename Oblivion Bip01 skeleton bones to Skyrim NPC skeleton names.

    Skyrim's character skeleton uses fully qualified node names with bracket
    tags (e.g. 'NPC Spine1 [Spn1]') that differ from Oblivion's Bip01 rig.
    Any NiNode in the tree whose name is in OBLIVION_TO_SKYRIM_BONE_MAP is
    renamed in-place so the game's skin deformation system can find the bones.
    Returns the number of bones that were renamed.
    """
    count = 0
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if isinstance(block, NifFormat.NiNode):
                raw = bytes(block.name).rstrip(b'\x00')
                name = raw.decode('latin-1', errors='replace')
                mapped = OBLIVION_TO_SKYRIM_BONE_MAP.get(name)
                if mapped:
                    block.name = mapped.encode('latin-1')
                    count += 1
    return count


def _get_body_parts_for_geometry(geom_name: str, num_partitions: int) -> list[int]:
    """Return a list of BSDismemberSkinInstance body_part IDs, one per partition block.

    Body parts are inferred from substrings in the geometry name (lower-cased).
    When num_partitions > 1 and a multi-partition list is configured for the
    matched keyword, that list is used; otherwise the single body part is repeated.
    """
    lower = geom_name.lower()
    for keyword, single_bp, multi_bps in ARMOR_GEOMETRY_BODY_PARTS:
        if keyword in lower:
            if multi_bps is not None and num_partitions > 1:
                # Pad or trim the multi-bp list to exactly num_partitions entries
                result = list(multi_bps)
                while len(result) < num_partitions:
                    result.append(result[-1])
                return result[:num_partitions]
            return [single_bp] * num_partitions
    return [ARMOR_DEFAULT_BODY_PART] * num_partitions


def _get_prn_bone(root_node):
    """Return the 'Prn' NiStringExtraData value on a root node, or None."""
    ed_list = getattr(root_node, 'extra_data_list', None)
    if ed_list is None:
        return None
    for ed_idx in range(root_node.num_extra_data_list):
        ed = ed_list[ed_idx]
        if isinstance(ed, NifFormat.NiStringExtraData):
            ed_name = bytes(ed.name).rstrip(b'\x00').decode('latin-1',
                                                            errors='replace')
            if ed_name == 'Prn':
                return bytes(ed.string_data).rstrip(b'\x00').decode(
                    'latin-1', errors='replace')
    return None


def _bake_node_transforms_into_verts(root_node):
    """Bake each geometry's node-to-root transform PLUS the root's own
    transform into the vertex/normal data, then zero those transforms.

    Needed before rigid-skinning Prn-attached creature parts: skinned
    rendering ignores node transforms, but Oblivion applied them when
    parenting the part to the bone (doghead's root carries a real rotation).
    After baking, vertices are in bone-local space so an identity bind
    matrix is correct.
    """
    root_m = root_node.get_transform()
    for block in list(root_node.tree()):
        if not isinstance(block, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
            continue
        gd = block.data
        if gd is None:
            continue
        full = block.get_transform(root_node) * root_m
        rot = full.get_matrix_33()
        for v in gd.vertices:
            nv = v * full
            v.x, v.y, v.z = nv.x, nv.y, nv.z
        if getattr(gd, 'has_normals', 0):
            for n in gd.normals:
                nn = n * rot
                n.x, n.y, n.z = nn.x, nn.y, nn.z
        block.rotation.set_identity()
        block.translation.x = block.translation.y = block.translation.z = 0.0
        block.scale = 1.0
        try:
            gd.update_center_radius()
        except Exception:
            pass
    root_node.rotation.set_identity()
    root_node.translation.x = root_node.translation.y = 0.0
    root_node.translation.z = 0.0
    root_node.scale = 1.0


def _add_prn_skin(data, root_node, keep_bone_names=False, plain=False):
    """Add Skyrim-compatible BSDismemberSkinInstance to non-skinned rigid armor.

    Oblivion attaches some armor pieces (e.g. helmets) rigidly to a bone via a
    'Prn' NiStringExtraData on the root instead of skeleton skinning.  Skyrim
    requires all worn-armor geometry to use BSDismemberSkinInstance.

    This function finds the Prn target bone, creates a NiNode placeholder in
    the NIF, then assigns all vertices weight 1.0 to that bone so the existing
    Oblivion mesh geometry is preserved exactly in the converted NIF.

    keep_bone_names=True keeps the ORIGINAL Oblivion bone name (creature
    parts — the converted creature skeleton keeps Oblivion bones).
    plain=True builds a plain NiSkinInstance instead of a
    BSDismemberSkinInstance (vanilla creature meshes never use dismember).

    Returns the number of geometry blocks that were skinned.
    """
    if not isinstance(root_node, NifFormat.NiNode):
        return 0

    prn_val = _get_prn_bone(root_node)
    if prn_val is None:
        return 0
    prn_bone = prn_val if keep_bone_names else \
        OBLIVION_TO_SKYRIM_BONE_MAP.get(prn_val, prn_val)

    # Create the bone NiNode placeholder (Skyrim engine matches by name to skeleton)
    bone_node = NifFormat.NiNode()
    bone_node.name = prn_bone.encode('latin-1')
    bone_node.flags = NIF_FLAGS

    # Insert bone node as FIRST child of root (vanilla Skyrim helmets have the
    # bone NiNode before geometry blocks). Shift existing children right by one.
    old_count = root_node.num_children
    root_node.num_children = old_count + 1
    root_node.children.update_size()
    for ci in range(old_count, 0, -1):
        root_node.children[ci] = root_node.children[ci - 1]
    root_node.children[0] = bone_node

    # Determine body_part from the Prn bone name
    b_lower = prn_bone.lower()
    if 'head' in b_lower or 'neck' in b_lower:
        body_part = 131   # SBP_131_HAIR — helmet (vanilla Skyrim uses HAIR slot for helmets)
    elif 'hand' in b_lower or 'finger' in b_lower:
        body_part = 33    # SBP_33_HANDS
    elif 'foot' in b_lower or 'toe' in b_lower:
        body_part = 37    # SBP_37_FEET
    elif 'calf' in b_lower or 'thigh' in b_lower:
        body_part = 38    # SBP_38_CALVES
    else:
        body_part = ARMOR_DEFAULT_BODY_PART

    skinned = 0
    for block in list(root_node.tree()):
        if not isinstance(block, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
            continue
        if getattr(block, 'skin_instance', None) is not None:
            continue  # already skinned

        geom_data = block.data
        if geom_data is None:
            continue
        num_verts = geom_data.num_vertices
        if num_verts == 0:
            continue

        # Build NiSkinData: one bone, all vertices weight 1.0
        skin_data_blk = NifFormat.NiSkinData()
        skin_data_blk.skin_transform.rotation.m_11 = 1.0
        skin_data_blk.skin_transform.rotation.m_22 = 1.0
        skin_data_blk.skin_transform.rotation.m_33 = 1.0
        skin_data_blk.skin_transform.scale = 1.0
        skin_data_blk.num_bones = 1
        skin_data_blk.bone_list.update_size()
        bone_entry = skin_data_blk.bone_list[0]
        bone_entry.skin_transform.rotation.m_11 = 1.0
        bone_entry.skin_transform.rotation.m_22 = 1.0
        bone_entry.skin_transform.rotation.m_33 = 1.0
        bone_entry.skin_transform.scale = 1.0
        bone_entry.num_vertices = num_verts
        bone_entry.vertex_weights.update_size()
        for vi in range(num_verts):
            bone_entry.vertex_weights[vi].index = vi
            bone_entry.vertex_weights[vi].weight = 1.0

        # Per-bone bounding sphere — the engine visibility-culls skinned
        # geometry by these spheres (moved by the live bone each frame); a
        # zero-radius sphere is never visible in-game even though NifSkope
        # ignores the field and renders the mesh fine.  The bind transform is
        # identity, so the sphere is just the vertex bounds in mesh space.
        verts = geom_data.vertices
        cx = (min(v.x for v in verts) + max(v.x for v in verts)) / 2.0
        cy = (min(v.y for v in verts) + max(v.y for v in verts)) / 2.0
        cz = (min(v.z for v in verts) + max(v.z for v in verts)) / 2.0
        bone_entry.bounding_sphere_offset.x = cx
        bone_entry.bounding_sphere_offset.y = cy
        bone_entry.bounding_sphere_offset.z = cz
        bone_entry.bounding_sphere_radius = max(
            ((v.x - cx) ** 2 + (v.y - cy) ** 2 + (v.z - cz) ** 2) ** 0.5
            for v in verts)

        # Build the skin instance (plain for creature parts, dismember for
        # worn armor)
        if plain:
            bsd = NifFormat.NiSkinInstance()
        else:
            bsd = NifFormat.BSDismemberSkinInstance()
        bsd.skeleton_root = root_node
        bsd.data = skin_data_blk
        bsd.skin_partition = None   # regenerated by retarget_skin_to_skyrim
        bsd.num_bones = 1
        bsd.bones.update_size()
        bsd.bones[0] = bone_node
        if not plain:
            bsd.num_partitions = 1
            bsd.partitions.update_size()
            bsd.partitions[0].body_part = body_part
            bsd.partitions[0].part_flag.pf_editor_visible = 1
            bsd.partitions[0].part_flag.pf_start_net_boneset = 1

        block.skin_instance = bsd
        skinned += 1

    return skinned


def _upgrade_skin_instances(data):
    """Convert NiSkinInstance → BSDismemberSkinInstance for worn armor/clothing.

    Skyrim requires BSDismemberSkinInstance (a subclass of NiSkinInstance) on all
    skinned geometry so the engine knows which body part each mesh partition covers.
    Without it the engine falls back to a basic NiSkinInstance which Skyrim's
    character pipeline does not fully support — resulting in the mesh not appearing
    on the character at all.

    The body_part field of each partition entry is derived from the geometry node
    name so that the correct biped slot is used for dismemberment / hiding.
    """
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not isinstance(block, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
                continue
            skin = getattr(block, 'skin_instance', None)
            if skin is None or isinstance(skin, NifFormat.BSDismemberSkinInstance):
                continue  # already correct or no skin

            # Build BSDismemberSkinInstance with copied skeleton references
            bsd = NifFormat.BSDismemberSkinInstance()
            bsd.skeleton_root = skin.skeleton_root
            bsd.data = skin.data                        # NiSkinData
            bsd.skin_partition = skin.skin_partition    # NiSkinPartition
            bsd.num_bones = skin.num_bones
            bsd.bones.update_size()
            for idx in range(skin.num_bones):
                bsd.bones[idx] = skin.bones[idx]

            # Determine partition count from NiSkinPartition
            n_blocks = 0
            if skin.skin_partition is not None:
                n_blocks = skin.skin_partition.num_skin_partition_blocks

            # Assign body_part IDs for each partition block
            geom_name = bytes(block.name).rstrip(b'\x00').decode('latin-1', errors='replace')
            body_parts = _get_body_parts_for_geometry(geom_name, n_blocks)

            bsd.num_partitions = n_blocks
            bsd.partitions.update_size()
            for idx in range(n_blocks):
                # part_flag bits: pf_editor_visible(0x01) + pf_start_net_boneset(0x100)
                # matches vanilla Skyrim worn-armor partitions (int value = 257)
                bsd.partitions[idx].body_part = body_parts[idx]
                bsd.partitions[idx].part_flag.pf_editor_visible = 1
                bsd.partitions[idx].part_flag.pf_start_net_boneset = 1

            block.skin_instance = bsd


def _convert_nif(data, fix_textures=True, src_path='', weight=0,
                 creature=False):
    """Convert a PyFFI NifFormat.Data in-place to Skyrim format.

    creature=True selects the creature-asset rules (skeleton.nif and skinned
    body parts from meshes/creatures/): skinned bodies keep a plain NiNode
    root and their plain NiSkinInstance with ORIGINAL Oblivion bone names
    (the faithful-port strategy keeps the Oblivion skeleton, so no retarget
    and no bone renaming); skeleton.nif becomes a BSFadeNode with BSX=198
    (vanilla creature-skeleton value) and its ragdoll bhk tree converted in
    place on the bone nodes (never hoisted to the root).

    Returns a stats dict.
    """
    stats = {
        'strips_fixed': 0,
        'properties_converted': 0,
        'root_converted': 0,
        'root_rotation_baked': 0,
        'tangents_injected': 0,
        'bones_remapped': 0,
        'textures_fixed': 0,
        '_src_path': str(src_path),   # for flip-book frame resolution
    }

    # Detect animation (affects motion_system choice in collision_handler)
    has_skin = _has_skin(data)

    # Resolve NiControllerSequence StringPalette offsets BEFORE version upgrade.
    # In Oblivion format (UV2=11) controlled_blocks store node_name etc. as
    # integer offsets into NiStringPalette.  After we set data.version to the
    # Skyrim value, PyFFI switches to direct-string mode and the offsets are
    # ignored — leaving every node_name as b''.  Skyrim uses node_name to look
    # up animation targets; empty names → null → crash on NIF load.
    _resolve_palette_strings(data)

    # Fix non-finite render geometry (NaN UVs/verts in Oblivion sources) BEFORE
    # any tangent computation or skin retargeting can propagate the NaNs.
    # Skyrim SE crashes at cell load on non-finite mesh data with no crash log.
    _sanitize_geometry_data(data)

    # --- Armor / clothing NIF fixups (before version upgrade) ---------------
    nif_basename = os.path.basename(src_path).lower()
    _is_gnd = nif_basename.endswith('_gnd.nif')
    _in_armor_dir = 'armor' in src_path.lower().replace('\\', '/') or \
                    'clothes' in src_path.lower().replace('\\', '/')   # clothing
    _is_shield = 'shield' in nif_basename

    if _is_gnd and has_skin:
        # Ground models with cloth-physics bones (Bone01/Bone02) show as red
        # question marks in Skyrim because the bones don't exist in any skeleton.
        # Strip the skin instance; the mesh will be rendered in its bind pose.
        _strip_gnd_skin(data)
        has_skin = False

    # Body-skin geometry is stripped AFTER retarget (see below).
    _body_nibs_to_splice: dict = {}

    if creature:
        # Oblivion-runtime bone controllers (dataless NiTransformController,
        # bhkBlendController, NiBSBoneLODController) don't exist in vanilla
        # Skyrim creature assets — the behavior graph drives the bones.
        _strip_creature_bone_controllers(data)

        # Engine contract: the anim rig root must be named 'NPC Root [Root]'
        # (all 30 vanilla creature rigs; the engine binds the graph to the
        # actor 3D through this node by name — a 'Bip01' root never binds
        # and the actor spawns invisible).  hkx_skeleton/hkx_anim apply the
        # same rename on the havok side; here we rename the NIF node in the
        # skeleton AND every body part (skin bones resolve by node name).
        from .hkx_skeleton import BONE_RENAMES
        renames = {k.encode('latin-1'): v.encode('latin-1')
                   for k, v in BONE_RENAMES.items()}
        for root in data.roots:
            if root is None:
                continue
            for block in root.tree():
                nm = getattr(block, 'name', None)
                if nm is None:
                    continue
                key = bytes(nm).rstrip(b'\x00')
                if key in renames:
                    block.name = renames[key]

    if creature and not has_skin:
        # Rigid Prn-attached creature parts (heads, eyes, tails): bake node
        # transforms into the verts, then rigid-skin to the ORIGINAL Oblivion
        # bone (the converted creature skeleton keeps Oblivion bone names)
        # with a plain NiSkinInstance, matching vanilla creature meshes.
        for root in data.roots:
            if root is not None and _get_prn_bone(root) is not None:
                _bake_node_transforms_into_verts(root)
                _add_prn_skin(data, root, keep_bone_names=True, plain=True)
        has_skin = _has_skin(data)

    if not creature and not _is_gnd and _in_armor_dir:
        if not has_skin and not _is_shield:
            # Non-skinned armor pieces (e.g. Oblivion helmets attached via 'Prn'
            # NiStringExtraData) need a BSDismemberSkinInstance added.
            for root in data.roots:
                if root is not None:
                    _add_prn_skin(data, root)
            has_skin = _has_skin(data)

        if has_skin:
            # NOTE: Bone renaming (Bip01 → NPC) is deferred until AFTER
            # retarget_skin_to_skyrim so that NiSkinData transforms are updated
            # while bones still have their original Oblivion names/positions.
            # Upgrade NiSkinInstance → BSDismemberSkinInstance so Skyrim's character
            # system correctly assigns geometry to biped slots.
            _upgrade_skin_instances(data)

    # Upgrade version fields — PyFFI writes using these
    data.version = OUTPUT_VERSION
    data.user_version = OUTPUT_USER_VERSION
    data.user_version_2 = OUTPUT_USER_VERSION_2
    data.header.endian_type = 1  # ENDIAN_LITTLE — critical, PyFFI defaults to 0 (BIG)

    # Flag: worn armor/clothing meshes keep NiNode root (not BSFadeNode).
    # Skyrim worn armor is attached to the character skeleton and uses NiNode
    # as root.  BSFadeNode is for world objects (architecture, statics, etc.).
    # This applies to body armor, helmets, gauntlets, boots, greaves, clothing —
    # but NOT shields.  Shields use BSFadeNode root + Prn='SHIELD' in Skyrim,
    # just like weapons.  Only _gnd (ground model) variants also get BSFadeNode.
    _is_shield = 'shield' in nif_basename
    # Creature body parts keep NiNode root + plain NiSkinInstance, exactly
    # like worn armor keeps NiNode (both are skeleton-attached at runtime).
    _is_creature_body = creature and has_skin
    _is_worn_armor = (not _is_gnd and _in_armor_dir and not _is_shield) \
        or _is_creature_body

    for i, root in enumerate(data.roots):
        if root is None:
            continue

        # NiBillboardNode roots.  A NiBillboardNode re-orients its ENTIRE
        # subtree to face the camera every frame.  For a pure billboard sprite
        # that's fine, but Oblivion fire/effect NIFs put the particle-system
        # emitters under the billboard root too — the spinning transform then
        # scrambles world-space particle emission and the system renders
        # nowhere (invisible flames).  Vanilla Skyrim keeps particle emitters
        # under a PLAIN node.  So: if the billboard subtree contains any
        # NiParticleSystem, demote the root to a plain NiNode (the individual
        # particles self-billboard; static effect quads keep a fixed
        # orientation, which is acceptable).  Otherwise keep the billboard and
        # just wrap it so the root can become a BSFadeNode.
        if isinstance(root, NifFormat.NiBillboardNode):
            has_psys = any(isinstance(b, NifFormat.NiParticleSystem)
                           for b in root.tree())
            if has_psys:
                plain = NifFormat.NiNode()
                plain.name = root.name
                plain.flags = NIF_FLAGS
                plain.translation = root.translation
                plain.rotation = root.rotation
                plain.scale = root.scale
                plain.num_children = root.num_children
                plain.children.update_size()
                for j, c in enumerate(root.children):
                    plain.children[j] = c
                plain.num_extra_data_list = root.num_extra_data_list
                plain.extra_data_list.update_size()
                for j, ed in enumerate(root.extra_data_list):
                    plain.extra_data_list[j] = ed
                if root.controller is not None:
                    plain.controller = root.controller
                # The root must not billboard (it would spin the particle
                # emitters), but the flat fire QUADS still need to face the
                # camera — a fixed-facing quad is edge-on/backfacing from most
                # angles in game (fires looked invisible).  Vanilla pattern
                # (campfire01burning): BSFadeNode → NiBillboardNode "Plane05"
                # → NiTriShape.  Wrap each direct geometry child in a child
                # NiBillboardNode carrying the source root's billboard mode.
                # Wrap direct geometry children in axis-corrected billboards
                # (see _wrap_in_billboard / _BB_AXIS_FIX for the Oblivion vs
                # Skyrim billboard axis convention story).
                bb_mode = int(getattr(root, 'billboard_mode', 1)) or 1
                for j in range(len(plain.children)):
                    c = plain.children[j]
                    if isinstance(c, (NifFormat.NiTriShape,
                                      NifFormat.NiTriStrips)):
                        plain.children[j] = _wrap_in_billboard(c, bb_mode)
                data.roots[i] = plain
                root = plain
            else:
                wrapper = NifFormat.NiNode()
                wrapper.flags = NIF_FLAGS
                wrapper.num_children = 1
                wrapper.children.update_size()
                wrapper.children[0] = root
                data.roots[i] = wrapper
                root = wrapper

        # Convert NiNode root → BSFadeNode (skip for worn armor: they use NiNode)
        if type(root).__name__ == 'NiNode' and not _is_worn_armor:
            old_root = root
            fade = NifFormat.BSFadeNode()
            fade.name = root.name
            fade.flags = NIF_FLAGS
            fade.translation = root.translation
            fade.rotation = root.rotation
            fade.scale = root.scale
            if hasattr(root, 'collision_object'):
                fade.collision_object = root.collision_object
                if fade.collision_object is not None:
                    fade.collision_object.target = fade
            fade.num_children = root.num_children
            fade.children.update_size()
            for j, c in enumerate(root.children):
                fade.children[j] = c
            if root.controller is not None:
                fade.controller = root.controller
            # Transfer specific extra data from old NiNode to new BSFadeNode.
            # We selectively copy rather than bulk-copy because bulk-copying all
            # extra data breaks animated objects (throne nif controller refs).
            # Prn tells Skyrim which skeleton node to attach this mesh to.
            # BSFurnitureMarker is converted to BSFurnitureMarkerNode for sit/sleep.
            if hasattr(root, 'extra_data_list'):
                # --- Furniture marker conversion ---
                # See _convert_furniture_markers: Oblivion entry points on the
                # floor become Skyrim seat positions (clustered, re-headed).
                frn_markers = [ed for ed in root.extra_data_list
                               if isinstance(ed, NifFormat.BSFurnitureMarker)
                               and not isinstance(ed, NifFormat.BSFurnitureMarkerNode)]
                if frn_markers:
                    frn, furn_shift = _convert_furniture_markers(frn_markers, root)
                    if frn is not None:
                        fade.num_extra_data_list += 1
                        fade.extra_data_list.update_size()
                        fade.extra_data_list[fade.num_extra_data_list - 1] = frn
                        stats.setdefault('furniture_markers', 0)
                        stats['furniture_markers'] += 1
                        # Geometry must be re-origined by the same shift
                        # (wrap pass below); importer lowers REFRs to match.
                        stats['_furn_origin_shift'] = furn_shift

                # --- Prn string extra data ---
                for ed in root.extra_data_list:
                    if not isinstance(ed, NifFormat.NiStringExtraData):
                        continue
                    ed_name = bytes(ed.name).rstrip(b'\x00')
                    if ed_name != b'Prn':
                        continue
                    prn_val = bytes(ed.string_data).rstrip(b'\x00').decode('latin-1', errors='replace')
                    nif_filename = os.path.basename(src_path)
                    remapped = _remap_prn(prn_val, nif_filename)

                    # Weapon NIFs need BSInvMarker so Skyrim can resolve the equipped-weapon model.
                    # Default rotation/zoom values match vanilla iron weapons.
                    if prn_val in _WEAPON_PRN_VALUES:
                        inv = NifFormat.BSInvMarker()
                        inv.name = b'INV'
                        inv.rotation_x = WEAPON_INV_MARKER_ROT_X
                        inv.rotation_y = WEAPON_INV_MARKER_ROT_Y
                        inv.rotation_z = WEAPON_INV_MARKER_ROT_Z
                        inv.zoom = WEAPON_INV_MARKER_ZOOM
                        fade.num_extra_data_list += 1
                        fade.extra_data_list.update_size()
                        fade.extra_data_list[fade.num_extra_data_list - 1] = inv
                        # War-axe orientation fix: the Skyrim WeaponAxe attachment node has a
                        # different local orientation from Oblivion's SideWeapon node. A 180°
                        # rotation around Y (the handle–blade axis) corrects the blade appearing
                        # on the wrong side without flipping the weapon upside-down (unlike 180°Z).
                        # Pass-6c below detects this non-identity rotation and bakes it into an
                        # inner NiNode so Skyrim applies it correctly to static geometry.
                        # if _weapon_prn_remapped == 'WeaponAxe':
                        fade.rotation.m_11 = -1.0; fade.rotation.m_12 =  0.0; fade.rotation.m_13 = 0.0
                        fade.rotation.m_21 =  0.0; fade.rotation.m_22 =  1.0; fade.rotation.m_23 = 0.0
                        fade.rotation.m_31 =  0.0; fade.rotation.m_32 =  0.0; fade.rotation.m_33 = -1.0

                    elif remapped == 'SHIELD':
                        # Shield BSInvMarker for inventory display (match vanilla ironshield.nif)
                        inv = NifFormat.BSInvMarker()
                        inv.name = b'INV'
                        inv.rotation_x = SHIELD_INV_MARKER_ROT_X
                        inv.rotation_y = SHIELD_INV_MARKER_ROT_Y
                        inv.rotation_z = SHIELD_INV_MARKER_ROT_Z
                        inv.zoom = SHIELD_INV_MARKER_ZOOM
                        fade.num_extra_data_list += 1
                        fade.extra_data_list.update_size()
                        fade.extra_data_list[fade.num_extra_data_list - 1] = inv
                        # Shield orientation fix.
                        #
                        # Oblivion shields: face in XZ plane (Y is depth), face normal = -Y.
                        #   Attachment bone (Bip01 L ForearmTwist) is at the forearm/wrist rim,
                        #   so NIF origin (X=0) is at the forearm, and the shield face center
                        #   is at roughly X≈21, Z≈0.
                        #
                        # Skyrim shields: face in XY plane (Z is depth), face normal = -Z.
                        #   Attachment bone (SHIELD) is near the hand, positioned at face center.
                        #   NIF origin should be at face center (vanilla convention).
                        #
                        # Rotation R = [[-1,0,0],[0,0,1],[0,1,0]] maps:
                        #   X → -X  (width flipped, corrects 180° orientation)
                        #   Y → +Z  (depth stays as depth; -Y face normal → -Z face normal ✓)
                        #   Z → +Y  (height: Oblivion Z-up → Skyrim Y-up ✓)
                        #
                        # Translation: compute mesh bbox center in Oblivion local space,
                        # apply R, then negate — this re-centers the face at the NIF origin
                        # to match the Skyrim SHIELD bone attachment convention.
                        #
                        # Pass-6c below detects non-identity rotation and wraps geometry in
                        # an inner NiNode (carrying both R and T), then zeros the BSFadeNode.
                        _shield_verts = []
                        def _collect_shield_verts(node, accum):
                            if hasattr(node, 'data') and node.data is not None:
                                d = node.data
                                if hasattr(d, 'vertices') and d.vertices:
                                    for _sv in d.vertices:
                                        accum.append((_sv.x, _sv.y, _sv.z))
                            if hasattr(node, 'children'):
                                for _sc in node.children:
                                    if _sc is not None:
                                        _collect_shield_verts(_sc, accum)
                        _collect_shield_verts(old_root, _shield_verts)
                        if _shield_verts:
                            _sv_arr = np.array(_shield_verts, dtype=np.float64)
                            # Use bbox midpoint as face center estimate
                            _cx = (_sv_arr[:, 0].min() + _sv_arr[:, 0].max()) * 0.5
                            _cy = (_sv_arr[:, 1].min() + _sv_arr[:, 1].max()) * 0.5
                            _cz = (_sv_arr[:, 2].min() + _sv_arr[:, 2].max()) * 0.5
                            # R * center: new_x=-cx, new_y=cz, new_z=cy
                            _tx = float( _cx)   # negate rotated center: -(-cx) = cx
                            _ty = float(-_cz)   # -cz
                            _tz = float(-_cy)   # -cy
                        else:
                            _tx, _ty, _tz = 0.0, 0.0, 0.0
                        fade.rotation.m_11 = -1.0; fade.rotation.m_12 =  0.0; fade.rotation.m_13 = 0.0
                        fade.rotation.m_21 =  0.0; fade.rotation.m_22 =  0.0; fade.rotation.m_23 = 1.0
                        fade.rotation.m_31 =  0.0; fade.rotation.m_32 =  1.0; fade.rotation.m_33 = 0.0
                        fade.translation.x = _tx
                        fade.translation.y = _ty
                        fade.translation.z = _tz

                    new_prn = NifFormat.NiStringExtraData()
                    new_prn.name = b'Prn'
                    new_prn.string_data = remapped.encode('latin-1')
                    fade.num_extra_data_list += 1
                    fade.extra_data_list.update_size()
                    fade.extra_data_list[fade.num_extra_data_list - 1] = new_prn
                    break

            data.roots[i] = fade
            root = fade
            stats['root_converted'] += 1

            # Armor/clothing _gnd (ground/inventory) models need BSInvMarker for
            # the inventory 3D viewer.  Without it the item is invisible in menus.
            # Values sourced from vanilla Skyrim cuirassgnd.nif / ironshield.nif.
            if _is_gnd and _in_armor_dir:
                inv = NifFormat.BSInvMarker()
                inv.name = b'INV'
                inv.rotation_x = ARMOR_GND_INV_MARKER_ROT_X
                inv.rotation_y = ARMOR_GND_INV_MARKER_ROT_Y
                inv.rotation_z = ARMOR_GND_INV_MARKER_ROT_Z
                inv.zoom = ARMOR_GND_INV_MARKER_ZOOM
                fade.num_extra_data_list += 1
                fade.extra_data_list.update_size()
                fade.extra_data_list[fade.num_extra_data_list - 1] = inv

            # Fix NiTimeController.target chain: every controller whose .target
            # pointed to old_root must now point to the new BSFadeNode.
            # NiControllerManager AND NiMultiTargetTransformController both store
            # a back-reference to their controlled node via .target.  Since old_root
            # is removed from data.roots and no longer reachable, PyFFI writes any
            # remaining references to it as null (-1).  Skyrim uses
            # NiControllerManager.target as the root for animated-node lookup; a
            # null target causes an immediate null-deref crash on NIF load.
            # NiMultiTargetTransformController also maintains an extra_targets array
            # which may additionally reference old_root.
            ctrl = root.controller
            while ctrl is not None:
                if hasattr(ctrl, 'target') and ctrl.target is old_root:
                    ctrl.target = root
                if hasattr(ctrl, 'extra_targets'):
                    for i in range(len(ctrl.extra_targets)):
                        if ctrl.extra_targets[i] is old_root:
                            ctrl.extra_targets[i] = root
                ctrl = getattr(ctrl, 'next_controller', None)

            # Fix NiDefaultAVObjectPalette: entries that referenced the old NiNode
            # now need to point to the new BSFadeNode (otherwise Skyrim null-deref crash)
            mgr = root.controller
            if mgr is not None and hasattr(mgr, 'object_palette') and mgr.object_palette is not None:
                pal = mgr.object_palette
                if hasattr(pal, 'num_objs'):
                    for obj_entry in pal.objs:
                        if obj_entry.av_object is old_root:
                            obj_entry.av_object = root

        elif type(root).__name__ == 'NiNode' and _is_worn_armor:
            # Worn armor: keep NiNode root but update flags and clear properties.
            root.flags = NIF_FLAGS
            # Clear NiNode effects array (Oblivion NiDynamicEffect refs)
            if hasattr(root, 'num_effects') and root.num_effects > 0:
                root.num_effects = 0
                root.effects.update_size()
            # Strip Prn from worn armor: Skyrim worn armor doesn't use Prn
            # (biped slot in ARMA handles positioning).  Keeping a Prn with an
            # Oblivion bone name (e.g. 'Bip01 Head') causes mis-attachment.
            if hasattr(root, 'extra_data_list'):
                keep_ed = []
                for ed in root.extra_data_list:
                    if isinstance(ed, NifFormat.NiStringExtraData):
                        ed_name = bytes(ed.name).rstrip(b'\x00')
                        if ed_name == b'Prn':
                            continue  # strip Prn
                    keep_ed.append(ed)
                if len(keep_ed) < root.num_extra_data_list:
                    root.num_extra_data_list = len(keep_ed)
                    root.extra_data_list.update_size()
                    for _ei, _ev in enumerate(keep_ed):
                        root.extra_data_list[_ei] = _ev

        # Process the root's own NiControllerManager (if any).
        # The manager is on the BSFadeNode root and may contain controlled_blocks that
        # target the root node itself by name ("X" and/or "X NonAccum").  In Oblivion,
        # the root animation drives the accumulation system for characters and is a no-op
        # for static objects.  In Skyrim, if BSFadeNode has these blocks they are applied
        # literally — a rotation animation on the root SPINS the entire object in world
        # space (the stonewallgatedoor01 "spinning" bug).  Strip root-named blocks here.
        # _process_controller_manager strips blocks named after the node, strips
        # NiMaterialColorController/NiGeomMorpherController, and handles zero-interp data.
        if (root.controller is not None and
                isinstance(root.controller, NifFormat.NiControllerManager)):
            _process_controller_manager(root, None)

        # If root has non-identity rotation (non-skinned), wrap all geometry children
        # in a new inner NiNode that carries the rotation and translation, then zero
        # the BSFadeNode's own transform.
        #
        # Skyrim ignores BSFadeNode root-node rotation for static placement, but it
        # DOES apply child NiNode rotation correctly.  The collision object STAYS
        # on the root (a bhkCollisionObject on a child NiNode intermittently
        # crashes hkpCollisionDispatcher) and the vanishing root transform is
        # composed into the rigid body instead (bake_node_transform_into_body).
        #
        # Furniture re-origin rides the same wrapper: marker-bearing models are
        # translated +furn_shift so the floor plane sits at z=0 (vanilla origin
        # convention — the engine anchors seated actors to the REFR z, so the
        # origin must be at the floor).  The importer lowers the REFRs of every
        # base record using the model by the same amount, keeping world-space
        # visuals identical.  See asset_convert/furniture_markers.py.
        wrapped = False
        furn_shift = stats.pop('_furn_origin_shift', 0.0)
        if (not has_skin and hasattr(root, 'rotation') and hasattr(root, 'children')
                and (not _is_identity(root.rotation) or abs(furn_shift) > 1e-4)):
            # Create inner NiNode that carries the original rotation and translation
            inner = NifFormat.NiNode()
            inner.name = root.name
            inner.flags = NIF_FLAGS
            # Copy rotation field-by-field to avoid PyFFI reference aliasing
            R = root.rotation
            inner.rotation.m_11 = R.m_11; inner.rotation.m_12 = R.m_12; inner.rotation.m_13 = R.m_13
            inner.rotation.m_21 = R.m_21; inner.rotation.m_22 = R.m_22; inner.rotation.m_23 = R.m_23
            inner.rotation.m_31 = R.m_31; inner.rotation.m_32 = R.m_32; inner.rotation.m_33 = R.m_33
            inner.translation.x = root.translation.x
            inner.translation.y = root.translation.y
            inner.translation.z = root.translation.z + furn_shift
            inner.scale = root.scale
            # Collision stays on the root BSFadeNode (target already = root),
            # but the body must absorb the root transform L that is being
            # zeroed: the engine places a root collision body at REFR ∘ bodyT,
            # while Oblivion applied REFR ∘ L ∘ bodyT.  Without this the
            # collision is rotated relative to the mesh (stackhallentrance01:
            # 90° off).  The furniture origin shift rides the same wrapper, so
            # it must be absorbed too (REFRs are lowered by the same amount).
            # Note: root.rotation/translation are still the original values
            # here — zeroing happens below.
            if getattr(root, 'collision_object', None) is not None:
                bake_node_transform_into_body(root.collision_object, root,
                                              extra_z=furn_shift)
            # Move all children to inner node
            inner.num_children = root.num_children
            inner.children.update_size()
            for j in range(root.num_children):
                inner.children[j] = root.children[j]
            # Zero root transform
            root.rotation = _identity_matrix()
            root.translation.x = 0.0
            root.translation.y = 0.0
            root.translation.z = 0.0
            root.scale = 1.0
            # Root's single child is the inner NiNode wrapper
            root.num_children = 1
            root.children.update_size()
            root.children[0] = inner
            wrapped = True
            stats['root_rotation_baked'] += 1

        # Walk and convert children first (geometry, shaders, etc.)
        if hasattr(root, 'children'):
            for j in range(len(root.children)):
                _res = _walk_node(root, root.children[j], fix_textures, stats)
                if isinstance(_res, NifFormat.NiBillboardNode):
                    # Same Skyrim billboard treatment as _walk_node applies to
                    # deeper levels (axis fix / demote-when-particles).
                    _res = _skyrimize_billboard(_res)
                root.children[j] = _res
            # Compact: remove None children left by stripped nodes
            keep = [c for c in root.children if c is not None]
            if len(keep) < root.num_children:
                root.num_children = len(keep)
                root.children.update_size()
                for _ri, _rv in enumerate(keep):
                    root.children[_ri] = _rv

        # Fix NiDefaultAVObjectPalette entries that referenced old NiTriStrips
        # blocks now replaced by NiTriShape during _walk_node.
        block_map = stats.get('_block_map', {})
        if block_map:
            mgr = root.controller
            if mgr is not None and hasattr(mgr, 'object_palette') and mgr.object_palette is not None:
                pal = mgr.object_palette
                if hasattr(pal, 'num_objs'):
                    for obj_entry in pal.objs:
                        replacement = block_map.get(id(obj_entry.av_object))
                        if replacement is not None:
                            obj_entry.av_object = replacement

        # Skyrim requires collision on the root node only.
        # If we did NOT wrap, check whether a child holds the collision and hoist it.
        # (When wrapped, the root's own collision was kept on the root above;
        # hoisting from under the rotated wrapper is not supported because
        # hoist_collision only bakes the child's translation, not rotation.)
        # Exception 1: animated objects (NiControllerManager on root) keep collision on
        # the animated child node so the KEYFRAMED rigid body follows the animation.
        # Exception 2: NIFs with Havok constraints (hinge/ragdoll/malleable) need the
        # collision objects to stay on their original nodes so the constraint spatial
        # relationship is preserved (e.g. swinging shop signs with bhkLimitedHingeConstraint).
        root_is_animated = (
            root.controller is not None and
            isinstance(root.controller, NifFormat.NiControllerManager)
        )
        has_constraints = any(
            isinstance(block, NifFormat.bhkConstraint) for block in data.blocks
        )
        if not wrapped and not root_is_animated and not has_constraints \
                and not creature \
                and hasattr(root, 'collision_object') and root.collision_object is None:
            # (creature skeletons/bodies excluded: ragdoll collision lives on
            # the bone nodes and empty leaf bones must not be pruned)
            if hoist_collision(root):
                # Remove the now-empty collision-container NiNode child
                remove_empty_collision_nodes(root)

        # Convert ALL collision objects in the tree (root + any child nodes).
        # Child-node collisions (e.g. animated display-case lids) also need
        # Skyrim-format unknown_6_shorts; leaving them unconverted causes crashes.
        # Creature skeletons keep + convert their bhkBlendCollisionObjects
        # (ragdoll bone collision — vanilla creature skeletons have them).
        convert_all_collisions(root, keep_blend=creature)

        # Scale Havok constraint pivot points (Oblivion → Skyrim Havok scale).
        # Constraint pivots are stored in Havok-space positions and must be scaled
        # by _HAVOK_SCALE (0.1) just like rigid body translations and shape dims.
        # Also sets broadphaseType=10 for dynamic constrained bodies (swinging signs).
        if has_constraints:
            scale_constraint_pivots(data)

        # Skyrim requires BSXFlags extra data when collision is present
        _add_bsx_flags(root, has_constraints=has_constraints)

        # Creature skeleton.nif: vanilla value is 198 (0xC6 = Havok | Ragdoll
        # | Dynamic | Articulated) — the generic collision heuristics above
        # can't derive it from a bone tree.
        if creature and 'skeleton' in nif_basename and \
                hasattr(root, 'extra_data_list'):
            bsx = next((ed for ed in root.extra_data_list
                        if isinstance(ed, NifFormat.BSXFlags)), None)
            if bsx is None:
                bsx = NifFormat.BSXFlags()
                bsx.name = b'BSX'
                root.num_extra_data_list += 1
                root.extra_data_list.update_size()
                root.extra_data_list[root.num_extra_data_list - 1] = bsx
            bsx.integer_data = 198

    # Retarget worn armor/clothing skins to Skyrim skeleton bind poses and
    # regenerate NiSkinPartition in Skyrim triangle format.  Must run AFTER
    # _walk_node (NiTriStrips→NiTriShape complete) so that update_skin_partition
    # can read triangle data; must also be AFTER version upgrade (UV2=83).
    # Bones still have OBLIVION names at this point — retarget uses OB→SK
    # name mapping internally.
    if creature and has_skin:
        # Creature skins keep Oblivion bones/weights/bind matrices verbatim
        # (same skeleton) — only the NiSkinPartition must be regenerated in
        # Skyrim triangle format (after _walk_node's strips→shapes pass).
        from .skin_retarget import _regen_skin_partition
        for root in data.roots:
            if root is None:
                continue
            for block in list(root.tree()):
                if not isinstance(block, (NifFormat.NiTriShape,
                                          NifFormat.NiTriStrips)):
                    continue
                skin = getattr(block, 'skin_instance', None)
                if skin is not None:
                    geom_name = bytes(block.name).rstrip(b'\x00').decode(
                        'latin-1', errors='replace')
                    _regen_skin_partition(block, skin, geom_name)

    if not creature and not _is_gnd and _in_armor_dir and has_skin:
        from .skin_retarget import retarget_skin_to_skyrim as _retarget

        # Pre-retarget: classify the piece type early so we can apply fixups.
        _piece_type = _get_armor_piece_type(src_path)

        _retarget(data, src_path=src_path)

        # NOW rename bones to Skyrim names — AFTER skin transforms are correct.
        stats['bones_remapped'] += _remap_bone_names(data)

        # Collect body-skin info and strip AFTER retarget + bone rename.
        # Bones now have Skyrim names; vertex positions are in Skyrim skeleton space.
        # Reads BSLightingShaderProperty texture paths (converted by _walk_node).
        # section_bboxes are computed in post-retarget SK-space coordinates, which
        # correctly localise the armor hole (including arm openings that shift ~20 Z
        # units relative to pre-retarget OB coords).
        _body_nibs_to_splice = collect_skin_info(data, src_path=src_path)
        strip_body_skin_geometry(data)

        # Apply per-piece armor vertex offset/scale (from skyrim_overrides) AFTER
        # body-skin is stripped, so only true armor geometry is shifted.
        _cfg = ARMOR_PIECE_OFFSETS.get(_piece_type, ARMOR_PIECE_OFFSETS['default'])
        apply_armor_offset(data, _cfg)

    # Splice Skyrim body geometry AFTER retarget + bone rename so that bone
    # NiNodes in the armor NIF already have Skyrim names to match against.
    if _body_nibs_to_splice:
        # For weight=1: morph armor vertices to follow body_1 shape BEFORE splice
        if weight == 1:
            from .skin_replacement import morph_armor_to_weight1
            morph_armor_to_weight1(data, _body_nibs_to_splice)
        splice_body_geometry(data, _body_nibs_to_splice, weight=weight)

    # Count tangents injected (approximate: each converted geometry node that had tangent data)
    stats['tangents_injected'] = stats['properties_converted']  # best we can count here

    return stats


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _shape_blocks(root):
    """All NiTriShape/NiTriStrips geometry blocks under a root."""
    return [b for b in root.tree()
            if isinstance(b, (NifFormat.NiTriShape, NifFormat.NiTriStrips))]


def _bone_nodes_by_name(root):
    """Map name(str, no NUL) -> NiNode for every NiNode under root."""
    out = {}
    for b in root.tree():
        if isinstance(b, NifFormat.NiNode):
            nm = bytes(b.name).rstrip(b'\x00').decode('latin-1')
            out.setdefault(nm, b)
    return out


def _append_child(node, child):
    node.num_children += 1
    node.children.update_size()
    node.children[node.num_children - 1] = child


def merge_creature_body(part_paths, dst_path):
    """Merge the converted creature body-part NIFs into ONE skinned NIF.

    Vanilla Skyrim creatures ship the WHOLE animal (body + head + eyes + tail
    + any extra parts) as a single skinned mesh under one root, referenced by
    a SINGLE ARMA on the BODY slot (census: DogRace names only 'BODY'; the
    rabbit.nif root carries every Rabbit* bone NiNode plus 'Bunnyfur01' and
    'Eyes' shapes as siblings).  Oblivion instead splits the creature into a
    skeleton.nif plus one NIF per body part.  Attaching each Oblivion part as
    its own ARMA on an extra biped slot does NOT work — the engine renders
    only the BODY-slot ARMA for a creature, so the head/eyes silently vanish.

    So: take the largest converted part (the body — it already embeds the full
    Bip01 bone hierarchy) as the base, then graft every OTHER part's shapes in
    as siblings under the base root, re-pointing each grafted shape's skin
    bones at the base root's matching NiNode (adding the node if the base
    lacks it).  Skin bones resolve against the animated skeleton by NAME at
    runtime, so the merged NIF renders exactly like a vanilla single-file
    creature mesh.

    part_paths: list of already-converted .nif paths (Skyrim version).  The
    one with the most bone nodes is used as the base.  Writes the merged NIF
    to dst_path.  Returns {'base': str, 'grafted': int, 'shapes': int}.
    """
    if not _PYFFI:
        return {'error': 'pyffi not installed'}
    if not part_paths:
        return {'error': 'no parts'}

    datas = []
    for p in part_paths:
        d = NifFormat.Data()
        with open(p, 'rb') as f:
            d.read(f)
        datas.append((p, d))

    # Base = the part with the most bone NiNodes (the body carries the whole
    # skeleton copy; heads/eyes carry only one or two bones).
    def bone_count(d):
        return sum(1 for r in d.roots if r is not None
                   for b in r.tree() if isinstance(b, NifFormat.NiNode))

    datas.sort(key=lambda pd: bone_count(pd[1]), reverse=True)
    base_path, base_data = datas[0]
    base_root = next(r for r in base_data.roots if r is not None)
    base_bones = _bone_nodes_by_name(base_root)

    grafted = 0
    for path, d in datas[1:]:
        for src_root in d.roots:
            if src_root is None:
                continue
            for shape in _shape_blocks(src_root):
                si = shape.skin_instance
                if si is not None:
                    # Re-point each skin bone at the base root's node of the
                    # same name (add a placeholder bone node if absent so the
                    # engine still matches it against the live skeleton).
                    for bi, bone in enumerate(si.bones):
                        nm = bytes(bone.name).rstrip(b'\x00').decode('latin-1')
                        tgt = base_bones.get(nm)
                        if tgt is None:
                            tgt = NifFormat.NiNode()
                            tgt.name = bone.name
                            tgt.flags = NIF_FLAGS
                            _append_child(base_root, tgt)
                            base_bones[nm] = tgt
                        si.bones[bi] = tgt
                    if si.skeleton_root is not None:
                        si.skeleton_root = base_root
                _append_child(base_root, shape)
                grafted += 1

    base_root.name = os.path.basename(dst_path).encode('latin-1')

    dst_dir = os.path.dirname(dst_path)
    if dst_dir:
        os.makedirs(dst_dir, exist_ok=True)
    with open(dst_path, 'wb') as f:
        base_data.write(f)
    return {'base': base_path, 'grafted': grafted,
            'shapes': len(_shape_blocks(base_root))}


def convert_nif(src_path, dst_path, *, fix_textures=True, remap_skeleton=None,
                src_meshes_dir=None, creature=False):
    """Convert a single Oblivion NIF to Skyrim format.

    Already-Skyrim versions are copied to dst_path unchanged.
    Unsupported/incompatible versions are skipped (not written to dst_path).
    Returns a result dict compatible with batch_convert's _update() expectations.

    src_meshes_dir: root of the source mesh tree (passed through by
    batch_convert).  Currently unused — kept as a hook for passes that need to
    read sibling meshes.
    """
    result = {
        'converted': False,
        'skipped': False,
        'copied': False,           # already-Skyrim, copied as-is
        'skip_reason': None,       # VER | RD | WR
        'error': None,
        'strips_fixed': False,
        'properties_converted': False,
        'root_converted': False,
        'root_rotation_baked': False,
        'version_upgraded': False,
    }

    if not _PYFFI:
        result['error'] = 'pyffi not installed'
        return result

    # Inspect version without full read
    data = NifFormat.Data()
    try:
        with open(src_path, 'rb') as f:
            data.inspect(f)
    except Exception:
        result['error'] = 'RD'
        return result

    if data.version in _SKYRIM_VERSIONS:
        # Already Skyrim — copy as-is
        dst_dir = os.path.dirname(dst_path)
        if dst_dir:
            os.makedirs(dst_dir, exist_ok=True)
        shutil.copy2(src_path, dst_path)
        result['copied'] = True
        return result

    if data.version not in _SUPPORTED_VERSIONS:
        # Too old or unrecognised — skip, do not copy
        result['error'] = 'VER'
        return result

    # Full read (fresh Data object so inspect state is clean)
    data = NifFormat.Data()
    try:
        with open(src_path, 'rb') as f:
            data.inspect(f)
            data.read(f)
    except Exception:
        result['error'] = 'RD'
        return result

    stats = _convert_nif(data, fix_textures=fix_textures,
                         src_path=str(src_path), creature=creature)

    # Graft the converted Oblivion flame NIF under FlameNode* markers (candle
    # flame / torch fire) — full conversion of Oblivion's own flame visuals,
    # not a Skyrim MPS substitution.  Runs BEFORE the atlas build so the
    # flame's flip-book atlas jobs (merged into stats) are executed below.
    for root in data.roots:
        if root is not None:
            _convert_flame_nodes(root, src_path, stats)

    # Build flip-book atlas textures planned by _process_geometry (frame strip
    # for BSEffectShaderPropertyFloatController U-Offset animation).  Output
    # goes into the textures/ tree beside the destination meshes/ tree.
    _atlas_jobs = stats.pop('_flipbook_atlases', {})
    if _atlas_jobs:
        from . import flipbook as _flipbook
        _dstn = str(dst_path).replace('/', os.sep).replace('\\', os.sep)
        _k = os.sep + 'meshes' + os.sep
        _i = _dstn.lower().rfind(_k)
        if _i >= 0:
            _out_root = _dstn[:_i] + os.sep
            for _job in _atlas_jobs.values():
                _out = _out_root + _job['atlas_rel'].replace('\\', os.sep)
                if not os.path.isfile(_out):
                    try:
                        _flipbook.build_flip_atlas(_job['files'], _out)
                    except Exception:
                        pass  # shader falls back to sampling a missing atlas;
                              # frames were pre-validated so this is unexpected

    # Generate tangent space for all NiTriShapeData that don't already have it.
    # Missing tangents cause incorrect normal-map lighting in Skyrim which
    # appears as "rainbow colored shaders" on architecture and other meshes.
    # This is equivalent to what SpellOptimizeGeometry does in the legacy converter.
    if _TANGENT_SPELL:
        try:
            toaster = _NifToaster()
            spell = _SpellAddTangentSpace(data=data, toaster=toaster)
            spell.recurse()
        except Exception:
            pass

    # Write to a buffer first — some NIFs have version-incompatible blocks
    # (e.g. NiGeomMorpherController morph arrays) that fail at Skyrim version.
    buf = _io.BytesIO()
    try:
        data.write(buf)
    except Exception:
        result['error'] = 'WR'
        return result

    dst_dir = os.path.dirname(dst_path)
    if dst_dir:
        os.makedirs(dst_dir, exist_ok=True)
    with open(dst_path, 'wb') as f:
        f.write(buf.getvalue())

    result['converted'] = True
    result['strips_fixed'] = stats['strips_fixed'] > 0
    result['properties_converted'] = stats['properties_converted'] > 0
    result['root_converted'] = stats['root_converted'] > 0
    result['root_rotation_baked'] = stats['root_rotation_baked'] > 0
    result['version_upgraded'] = True
    result['bones_remapped'] = stats['bones_remapped'] > 0
    result['textures_fixed'] = stats['properties_converted'] > 0  # proxy: every property conversion rewrites textures
    return result


def batch_convert(mesh_dir, output_dir, *, fix_textures=True,
                  remap_skeleton=None, subdir_filter=None):
    """Convert all NIF files in mesh_dir to Skyrim format, writing to output_dir.

    Skip reason codes:
      VER  — unsupported NIF version (too old / unrecognised)
      RD   — read failure (corrupt, truncated, unknown block types)
      WR   — write failure (version-incompatible blocks, e.g. NiGeomMorpherController)

    Args:
        subdir_filter: If provided, an iterable of root subfolder names (e.g.
                       ['architecture', 'clutter']) to include. NIFs whose first
                       path component (relative to mesh_dir) is not in the set
                       are skipped. None means include everything.

    Returns a stats dict compatible with asset_pipeline.py expectations.
    """
    mesh_path = Path(mesh_dir)
    out_base = Path(output_dir)
    all_nifs = list(mesh_path.rglob('*.nif'))

    allowed_subdirs = None
    if subdir_filter is not None:
        allowed_subdirs = {s.lower() for s in subdir_filter}

    # Filter out paths matching SKIP_PATHS segments
    nif_files = []
    skipped_by_path = 0
    for nf in all_nifs:
        rel_parts = [p.lower() for p in nf.relative_to(mesh_path).parts]
        if any(seg in rel_parts for seg in SKIP_PATHS):
            skipped_by_path += 1
        elif allowed_subdirs is not None and rel_parts and rel_parts[0] not in allowed_subdirs:
            skipped_by_path += 1
        else:
            nif_files.append(nf)
    total = len(nif_files)

    stats = {
        'total': total,
        'converted': 0,
        'copied': 0,
        'skipped': 0,
        'errors': 0,
        'strips': 0,
        'properties': 0,
        'roots': 0,
        'rotations': 0,
        'warn_counts': _collections.Counter(),
    }

    # Collect (rel_path, reason) for every skipped file
    skipped_list = []

    workers = _WORKER_COUNT
    print(f'Found {total} NIF files in {mesh_dir} (workers={workers})')
    if skipped_by_path:
        print(f'  Skipped {skipped_by_path} files matching SKIP_PATHS: {sorted(SKIP_PATHS)}')

    if total == 0:
        return stats

    work_args = [
        (str(nif_file), str(out_base / nif_file.relative_to(mesh_path)),
         fix_textures, remap_skeleton, str(mesh_path))
        for nif_file in nif_files
    ]

    def _update(nif_str, r):
        stats['warn_counts'].update(r.get('warn_counts', {}))
        if r.get('error'):
            stats['errors'] += 1
            rel = str(Path(nif_str).relative_to(mesh_path))
            skipped_list.append((rel, str(r['error'])))
        elif r.get('converted'):
            stats['converted'] += 1
            if r['strips_fixed']:         stats['strips'] += 1
            if r['properties_converted']: stats['properties'] += 1
            if r['root_converted']:       stats['roots'] += 1
            if r['root_rotation_baked']:  stats['rotations'] += 1
        elif r.get('copied'):
            stats['copied'] += 1
        else:
            stats['skipped'] += 1
            rel = str(Path(nif_str).relative_to(mesh_path))
            skipped_list.append((rel, r.get('skip_reason', '?')))

    if workers > 1:
        import multiprocessing as mp
        done = 0
        with mp.Pool(processes=workers, initializer=_pyffi_capture_init) as pool:
            for status, nif_str, payload in pool.imap_unordered(_batch_worker, work_args):
                done += 1
                if status == 'ok':
                    _update(nif_str, payload)
                else:
                    stats['errors'] += 1
                    rel = str(Path(nif_str).relative_to(mesh_path))
                    skipped_list.append((rel, 'EXC'))
                    if stats['errors'] <= 20:
                        print(f'  ERROR: {Path(nif_str).name}: {payload}')
                if done % 500 == 0 or done == total:
                    try:
                        rel_parts = Path(nif_str).relative_to(mesh_path).parts
                        folder = rel_parts[0] if len(rel_parts) > 1 else '.'
                    except ValueError:
                        folder = Path(nif_str).parent.name
                    print(f'  {done}/{total} [{folder}] -- converted={stats["converted"]} '
                          f'copied={stats["copied"]} errors={stats["errors"]}')
    else:
        _pyffi_capture_init()
        for i, args in enumerate(work_args):
            status, nif_str, payload = _batch_worker(args)
            if status == 'ok':
                _update(nif_str, payload)
            else:
                stats['errors'] += 1
                rel = str(Path(nif_str).relative_to(mesh_path))
                skipped_list.append((rel, 'EXC'))
                if stats['errors'] <= 20:
                    print(f'  ERROR: {Path(nif_str).name}: {payload}')
            if (i + 1) % 200 == 0 or i == 0:
                try:
                    rel_parts = Path(nif_str).relative_to(mesh_path).parts
                    folder = rel_parts[0] if len(rel_parts) > 1 else '.'
                except ValueError:
                    folder = Path(nif_str).parent.name
                print(f'  {i + 1}/{total} [{folder}] -- converted={stats["converted"]} '
                      f'copied={stats["copied"]} errors={stats["errors"]}')

    print(f'\nResults: {stats["converted"]} converted, {stats["copied"]} copied, '
          f'{stats["skipped"]} skipped, {stats["errors"]} errors / {total} total')

    if skipped_list:
        print(f'\nFailed/Skipped ({len(skipped_list)}) — '
              f'RD=read fail, WR=write fail, EXC=exception:')
        for rel, reason in sorted(skipped_list):
            print(f'  [{reason}] {rel}')

    if stats['warn_counts']:
        total_suppressed = sum(stats['warn_counts'].values())
        top_cats = sorted(stats['warn_counts'].items(), key=lambda x: -x[1])[:30]
        shown = sum(c for _, c in top_cats)
        print(f'\nPyFFI warnings suppressed ({total_suppressed} total):')
        for cat, cnt in top_cats:
            print(f'  {cat}: {cnt}')
        if shown < total_suppressed:
            remaining = len(stats['warn_counts']) - len(top_cats)
            print(f'  ... ({total_suppressed - shown} more in {remaining} other categories)')

    # plain ASCII: cp1252 consoles/pipes choke on the arrow character
    print(f'\nDetailed stats: Strips->Shape={stats["strips"]}, '
          f'Properties={stats["properties"]}, '
          f'Roots={stats["roots"]}, Rotations baked={stats["rotations"]}')

    return stats


def _batch_worker(args):
    nif_str, out_path, fix_textures, remap_skeleton, src_meshes_dir = args
    global _worker_warn_log
    _worker_warn_log = []
    try:
        r = convert_nif(nif_str, out_path,
                        fix_textures=fix_textures, remap_skeleton=remap_skeleton,
                        src_meshes_dir=src_meshes_dir)
        r['warn_counts'] = _categorize_pyffi_warnings(_worker_warn_log)
        return ('ok', nif_str, r)
    except Exception as e:
        return ('error', nif_str, str(e))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Convert Oblivion NIFs to Skyrim format')
    parser.add_argument('src', help='Source NIF file or directory')
    parser.add_argument('dst', help='Destination NIF file or directory')
    parser.add_argument('--no-fix-textures', action='store_true')
    a = parser.parse_args()

    if Path(a.src).is_dir():
        batch_convert(a.src, a.dst, fix_textures=not a.no_fix_textures)
    else:
        r = convert_nif(a.src, a.dst, fix_textures=not a.no_fix_textures)
        print(r)
