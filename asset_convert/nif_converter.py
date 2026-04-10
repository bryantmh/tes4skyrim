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
  - Havok collision conversion (bhkNiTriStripsShape→bhkPackedNiTriStripsShape via MOPP_RL)
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
import time
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
    ArmorOffsetConfig,
    BSX_FLAGS_ANIMATED,
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
from .collision import (convert_all_collisions, hoist_collision, remove_empty_collision_nodes)

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

def _process_geometry(strips_or_shape, fix_textures):
    """Convert a NiTriStrips or NiTriShape into a ready Skyrim NiTriShape.

    Returns the NiTriShape (may be a new object if input was NiTriStrips).
    NiTriStrips with controllers are NOT converted to NiTriShape (the controller
    still references the original node by block index; converting breaks the NIF).
    """
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
    # using NiFlipController on the NiTexturingProperty.  We switch to
    # BSEffectShaderProperty (needed for additive/effect blending) and use the
    # first-frame texture as a static source_texture.
    #
    # We do NOT port the NiFlipController itself: in Skyrim, NiFlipController
    # targets NiTexturingProperty which is now gone, so attaching it to the
    # geometry node causes an invalid-target crash (red triangle).  Skyrim fire
    # quads use BSEffectShaderPropertyFloatController for UV animation, which
    # requires artist-tuned parameters we cannot derive from the Oblivion source.
    # A static first-frame texture is far better than a crashed mesh.
    if flip_ctrl is not None:
        # Derive effective texture (first frame)
        srcs = [s for s in flip_ctrl.sources if s is not None and s.file_name]
        if srcs:
            pth = srcs[0].file_name
            effective_path = (_rewrite_tex_path(pth) if fix_textures
                              else pth.decode('utf-8', errors='replace')).encode('utf-8')
        else:
            effective_path = tex_set.textures[0] if diffuse_path else b''

        # Build BSEffectShaderProperty for the effect quad
        eff_shader = NifFormat.BSEffectShaderProperty()
        esf1 = eff_shader.shader_flags_1
        esf1.slsf_1_own_emit = 1       # fire is self-illuminated
        esf1.slsf_1_z_buffer_test = 1
        esf2 = eff_shader.shader_flags_2
        esf2.slsf_2_z_buffer_write = 0  # effect quads don't write to depth
        if has_double_sided:
            esf2.slsf_2_double_sided = 1
        eff_shader.source_texture = effective_path
        eff_shader.texture_clamp_mode = 3

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

# Oblivion dynamically attaches a flame NIF at FlameNode* markers on the
# fake (lit) versions of light objects at runtime.  Skyrim doesn't do this;
# the flame must be embedded in the NIF.  We detect empty FlameNode* children
# and embed a pre-converted copy of firecandleflame.nif at each one.

_FLAME_EMBED_CACHE = {}   # src_meshes_dir → converted NiBillboardNode subtree root


def _get_flame_subtree(src_meshes_dir, fix_textures):
    """Return a converted NiBillboardNode from firecandleflame.nif, or None on failure.

    Cached per src_meshes_dir so we only convert it once per batch.
    The returned object is a raw PyFFI block; callers must NOT mutate it
    (each call to embed gets its own tree via _clone_nif_tree).
    """
    cache_key = (str(src_meshes_dir), fix_textures)
    if cache_key in _FLAME_EMBED_CACHE:
        return _FLAME_EMBED_CACHE[cache_key]

    _FLAME_EMBED_CACHE[cache_key] = None  # set sentinel first to avoid re-entry

    flame_src = os.path.join(str(src_meshes_dir), 'fire', 'firecandleflame.nif')
    if not os.path.exists(flame_src):
        return None

    flame_data = NifFormat.Data()
    try:
        with open(flame_src, 'rb') as f:
            flame_data.read(f)
    except Exception:
        return None

    # Convert the flame NIF in-place (same pipeline as regular NIFs)
    _convert_nif(flame_data, fix_textures=fix_textures)

    # Find the NiBillboardNode (flame visual root) in the converted data
    # Structure: NiNode "Scene Root" → NiBillboardNode "FireCandleFlame" → children
    # After _convert_nif the outer NiNode becomes BSFadeNode; NiBillboardNode is child
    for root in flame_data.roots:
        if root is None:
            continue
        for child in getattr(root, 'children', []):
            if child is not None and isinstance(child, NifFormat.NiBillboardNode):
                _FLAME_EMBED_CACHE[cache_key] = child
                return child
    return None


def _embed_flame_nodes(root_node, src_meshes_dir, fix_textures):
    """Walk root_node's tree looking for empty FlameNode* NiNodes.

    For each found, attach a converted firecandleflame.nif subtree as its child.
    Modifies root_node in-place.  Returns count of flame nodes populated.
    """
    if not src_meshes_dir:
        return 0

    flame_root = _get_flame_subtree(src_meshes_dir, fix_textures)
    if flame_root is None:
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
            if nm.startswith('FlameNode') and isinstance(child, NifFormat.NiNode):
                if child.num_children == 0:
                    # Attach flame subtree — PyFFI allows sharing block refs
                    # (the NIF writer walks reachable blocks so shared subtrees
                    # appear once in the output block list).
                    child.num_children = 1
                    child.children.update_size()
                    child.children[0] = flame_root
                    count += 1
            _visit(child)

    _visit(root_node)
    return count


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
        fresh.bs_max_vertices = orig_count
        fresh.num_vertices = 0
        fresh.has_vertices = True
        fresh.has_normals = False
        if hasattr(fresh, 'has_sizes'):
            fresh.has_sizes = True
        if hasattr(fresh, 'has_rotation_angles'):
            fresh.has_rotation_angles = getattr(old_data, 'has_rotation_angles', True)
        node.data = fresh

    # Fix version-conditional fields on kept modifiers.
    # NiPSysGrowFadeModifier gains base_scale (float) at UV2>=34.  The field
    # defaults to 0.0 which makes all particles invisible (scale = 0 × grow).
    # Set to 1.0 so particles start at full size and grow/fade correctly.
    for mod in node.modifiers:
        if mod is not None and isinstance(mod, NifFormat.NiPSysGrowFadeModifier):
            if hasattr(mod, 'base_scale'):
                mod.base_scale = 1.0

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

    # Build BSEffectShaderProperty (Skyrim particle shader)
    shader = NifFormat.BSEffectShaderProperty()
    sf1 = shader.shader_flags_1
    sf1.slsf_1_z_buffer_test = 1
    sf1.slsf_1_soft_effect = 1          # soft (depth-fade) particle blending
    sf2 = shader.shader_flags_2
    sf2.slsf_2_z_buffer_write = 0       # particles don't write to depth buffer
    sf2.slsf_2_vertex_colors = 1        # most particles modulate colour per-vertex
    shader.source_texture = effective_path
    shader.texture_clamp_mode = 3       # WRAP_S | WRAP_T
    shader.emissive_multiple = 1.0      # default 0 = black particles

    node.bs_properties[0] = shader
    if alpha_prop is not None:
        node.bs_properties[1] = alpha_prop


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
        ts = _process_geometry(node, fix_textures)
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

        # Recurse into children
        for i in range(len(node.children)):
            result = _walk_node(node, node.children[i], fix_textures, stats)
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



def _add_bsx_flags(root):
    """Add BSXFlags extra data to root if collision is present anywhere in the tree.

    Static meshes use value 130 (COMPLEX=0x80 | HAVOK=0x02).
    Weapon/equipment meshes use value 194 (COMPLEX=0x80 | ANIMATED=0x40 | HAVOK=0x02).
    The ANIMATED flag is required for Skyrim to set up weapon animation bindings;
    without it the engine leaves the model pointer null and crashes on equip.

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

    if not _has_any_collision(root):
        return
    if hasattr(root, 'extra_data_list'):
        for ed in root.extra_data_list:
            if isinstance(ed, NifFormat.BSXFlags):
                return  # Already present

    # Animated objects (NiControllerManager on root) need ANIMATED + COMPLEX bits
    # so Skyrim sets up animation bindings and collision sync.
    root_is_animated = (
        root.controller is not None and
        isinstance(root.controller, NifFormat.NiControllerManager)
    )
    bsx_value = BSX_FLAGS_ANIMATED if root_is_animated else BSX_FLAGS_STATIC

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


def _add_prn_skin(data, root_node):
    """Add Skyrim-compatible BSDismemberSkinInstance to non-skinned rigid armor.

    Oblivion attaches some armor pieces (e.g. helmets) rigidly to a bone via a
    'Prn' NiStringExtraData on the root instead of skeleton skinning.  Skyrim
    requires all worn-armor geometry to use BSDismemberSkinInstance.

    This function finds the Prn target bone, creates a NiNode placeholder in
    the NIF, then assigns all vertices weight 1.0 to that bone so the existing
    Oblivion mesh geometry is preserved exactly in the converted NIF.

    Returns the number of geometry blocks that were skinned.
    """
    if not isinstance(root_node, NifFormat.NiNode):
        return 0

    # Find 'Prn' NiStringExtraData on the root node
    prn_bone = None
    ed_list = getattr(root_node, 'extra_data_list', None)
    if ed_list is not None:
        for ed_idx in range(root_node.num_extra_data_list):
            ed = ed_list[ed_idx]
            if isinstance(ed, NifFormat.NiStringExtraData):
                ed_name = bytes(ed.name).rstrip(b'\x00').decode('latin-1', errors='replace')
                if ed_name == 'Prn':
                    prn_val = bytes(ed.string_data).rstrip(b'\x00').decode(
                        'latin-1', errors='replace')
                    prn_bone = OBLIVION_TO_SKYRIM_BONE_MAP.get(prn_val, prn_val)
                    break

    if prn_bone is None:
        return 0

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

        # Build BSDismemberSkinInstance
        bsd = NifFormat.BSDismemberSkinInstance()
        bsd.skeleton_root = root_node
        bsd.data = skin_data_blk
        bsd.skin_partition = None   # regenerated by retarget_skin_to_skyrim
        bsd.num_bones = 1
        bsd.bones.update_size()
        bsd.bones[0] = bone_node
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


def _convert_nif(data, fix_textures=True, src_path='', weight=0):
    """Convert a PyFFI NifFormat.Data in-place to Skyrim format.

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

    if not _is_gnd and _in_armor_dir:
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
    _is_worn_armor = (not _is_gnd and _in_armor_dir and not _is_shield)

    for i, root in enumerate(data.roots):
        if root is None:
            continue

        # Wrap NiBillboardNode roots in a plain NiNode parent
        if isinstance(root, NifFormat.NiBillboardNode):
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
                for ed in root.extra_data_list:
                    if not isinstance(ed, NifFormat.BSFurnitureMarker):
                        continue
                    frn = NifFormat.BSFurnitureMarkerNode()
                    frn.name = b'FRN'
                    frn.num_positions = ed.num_positions
                    frn.positions.update_size()
                    for pi in range(ed.num_positions):
                        src_pos = ed.positions[pi]
                        dst_pos = frn.positions[pi]
                        dst_pos.offset.x = src_pos.offset.x
                        dst_pos.offset.y = src_pos.offset.y
                        # Oblivion furniture marker Z is stored relative to the
                        # marker's own frame (top-of-chair origin → negative = below
                        # origin).  Skyrim expects Z measured upward from floor.
                        # Negating matches empirical data: |OB Z| ≈ Skyrim Z ≈ 33-34.
                        dst_pos.offset.z = -src_pos.offset.z
                        # should be slightly lower and further back
                        # Oblivion orientation is the direction the furniture "faces";
                        # in Skyrim the heading is the direction the NPC FACES when
                        # seated, which is 180° (~π rad) offset from the Oblivion value.
                        dst_pos.heading = src_pos.orientation / 1000.0 + math.pi
                        # position_ref → animation_type + entry_properties
                        ref = src_pos.position_ref_1
                        if 1 <= ref <= 10:
                            dst_pos.animation_type = 2  # Sleep
                        elif 11 <= ref <= 19:
                            dst_pos.animation_type = 1  # Sit
                        else:
                            dst_pos.animation_type = 1  # Default to Sit
                        # Entry direction from position_ref
                        ep = dst_pos.entry_properties
                        if ref in (1, 11):
                            ep.left = 1
                        elif ref in (2, 12):
                            ep.right = 1
                        elif ref == 13:
                            ep.front = 1
                        elif ref == 14:
                            ep.behind = 1
                        else:
                            # Unknown ref or generic — allow entry from front
                            ep.front = 1
                    fade.num_extra_data_list += 1
                    fade.extra_data_list.update_size()
                    fade.extra_data_list[fade.num_extra_data_list - 1] = frn
                    stats.setdefault('furniture_markers', 0)
                    stats['furniture_markers'] += 1
                    break  # Only one BSFurnitureMarker per NIF

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
        # DOES apply child NiNode rotation correctly.  The collision object is moved
        # to the inner NiNode so Havok reads the NiNode's world transform
        # (= original R + T) when positioning the collision — no baking required.
        # This matches the legacy copyover_legacy_nif_animations.py approach exactly.
        wrapped = False
        if (not has_skin and hasattr(root, 'rotation') and hasattr(root, 'children')
                and not _is_identity(root.rotation)):
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
            inner.translation.z = root.translation.z
            inner.scale = root.scale
            # Move collision to inner node so Havok uses the correct NiNode world transform
            # Note: this likely causes crashes due to the collision not being on the root node
            if hasattr(root, 'collision_object') and root.collision_object is not None:
                inner.collision_object = root.collision_object
                inner.collision_object.target = inner
                root.collision_object = None
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
                root.children[j] = _walk_node(root, root.children[j], fix_textures, stats)
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

        # Skyrim requires collision on the root node only (or on an inner NiNode
        # when a rotation wrapper was created — Havok reads the NiNode's world xform).
        # If we did NOT wrap, check whether a child holds the collision and hoist it.
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
        if not wrapped and not root_is_animated and not has_constraints and hasattr(root, 'collision_object') and root.collision_object is None:
            if hoist_collision(root):
                # Remove the now-empty collision-container NiNode child
                remove_empty_collision_nodes(root)

        # Convert ALL collision objects in the tree (root + any child nodes).
        # Child-node collisions (e.g. animated display-case lids) also need
        # Skyrim-format unknown_6_shorts; leaving them unconverted causes crashes.
        convert_all_collisions(root)

        # Skyrim requires BSXFlags extra data when collision is present
        _add_bsx_flags(root)

    # Retarget worn armor/clothing skins to Skyrim skeleton bind poses and
    # regenerate NiSkinPartition in Skyrim triangle format.  Must run AFTER
    # _walk_node (NiTriStrips→NiTriShape complete) so that update_skin_partition
    # can read triangle data; must also be AFTER version upgrade (UV2=83).
    # Bones still have OBLIVION names at this point — retarget uses OB→SK
    # name mapping internally.
    if not _is_gnd and _in_armor_dir and has_skin:
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

def convert_nif(src_path, dst_path, *, fix_textures=True, remap_skeleton=None,
                src_meshes_dir=None):
    """Convert a single Oblivion NIF to Skyrim format.

    Already-Skyrim versions are copied to dst_path unchanged.
    Unsupported/incompatible versions are skipped (not written to dst_path).
    Returns a result dict compatible with batch_convert's _update() expectations.
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

    stats = _convert_nif(data, fix_textures=fix_textures, src_path=str(src_path))

    # Embed flame particle subtree at FlameNode* attachment points.
    # Oblivion's engine dynamically attached fire/flame NIFs at these nodes at
    # runtime; Skyrim doesn't, so we bake the converted flame into the NIF.
    # Code does work so it's been disabled
    # if src_meshes_dir is not None:
    #     for root in data.roots:
    #         if root is not None:
    #             _embed_flame_nodes(root, src_meshes_dir, fix_textures)

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
                  remap_skeleton=None):
    """Convert all NIF files in mesh_dir to Skyrim format, writing to output_dir.

    Skip reason codes:
      VER  — unsupported NIF version (too old / unrecognised)
      RD   — read failure (corrupt, truncated, unknown block types)
      WR   — write failure (version-incompatible blocks, e.g. NiGeomMorpherController)

    Returns a stats dict compatible with asset_pipeline.py expectations.
    """
    mesh_path = Path(mesh_dir)
    out_base = Path(output_dir)
    all_nifs = list(mesh_path.rglob('*.nif'))

    # Filter out paths matching SKIP_PATHS segments
    nif_files = []
    skipped_by_path = 0
    for nf in all_nifs:
        rel_parts = [p.lower() for p in nf.relative_to(mesh_path).parts]
        if any(seg in rel_parts for seg in SKIP_PATHS):
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

    print(f'\nDetailed stats: Strips→Shape={stats["strips"]}, '
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
