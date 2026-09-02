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
import re
import shutil
import struct
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from worker_budget import worker_count  # noqa: E402

from . import landscape_normals   # owns the shared stand-in normal's path
from .skyrim_overrides import (
    ARMOR_DEFAULT_BODY_PART,
    ARMOR_GEOMETRY_BODY_PARTS,
    ARMOR_GND_INV_MARKER_ROT_X,
    ARMOR_GND_INV_MARKER_ROT_Y,
    ARMOR_GND_INV_MARKER_ROT_Z,
    ARMOR_GND_INV_MARKER_ZOOM,
    ARMOR_PIECE_OFFSETS,
    ARMOR_PIECE_OFFSETS_PRN,
    BSX_FLAGS_ANIMATED,
    BSX_FLAGS_CONSTRAINED,
    BSX_FLAGS_DYNAMIC,
    BSX_FLAGS_STATIC,
    OBLIVION_TO_SKYRIM_BONE_MAP,
    SHIELD_INV_MARKER_ROT_X,
    SHIELD_INV_MARKER_ROT_Y,
    SHIELD_INV_MARKER_ROT_Z,
    SHIELD_INV_MARKER_ZOOM,
    TORCH_INV_MARKER_ROT_X,
    TORCH_INV_MARKER_ROT_Y,
    TORCH_INV_MARKER_ROT_Z,
    TORCH_INV_MARKER_ZOOM,
    WEAPON_INV_MARKER_ROT_X,
    WEAPON_INV_MARKER_ROT_Y,
    WEAPON_INV_MARKER_ROT_Z,
    WEAPON_INV_MARKER_ZOOM,
)
from .collision import (bake_node_transform_into_body, convert_all_collisions,
                        enforce_ragdoll_tree, hoist_collision,
                        remove_empty_collision_nodes, scale_constraint_pivots,
                        strip_marker_collision_bodies)
from .tri_reconstruct import (clear_match_groups, fix_missing_triangles,
                              UnreconstructibleGeometry)

# Apply all PyFFI patches (time.clock fix, nif.xml condition fixes) before import
from . import pyffi_monkey_patch as _patch  # noqa: F401
from .texture_prune import _texture_refs_in  # noqa: E402
from .nif_flames import convert_flame_nodes

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
    # Join the parent's containment job so this worker cannot outlive a parent
    # that dies without cleanup (crash / external kill). No-op off Windows.
    from process_job import join_pool_job
    join_pool_job()

    global _worker_warn_log
    _worker_warn_log = []
    pyffi_log = _logging.getLogger('pyffi')
    pyffi_log.propagate = False
    pyffi_log.setLevel(_logging.WARNING)
    pyffi_log.handlers = []
    pyffi_log.addHandler(_PyFFICapture())


_PYFFI_PROGRESS_PREFIXES = (
    # PyFFI's toaster/spells log traversal and progress at WARNING rather than
    # INFO. These are not data warnings. Match after whitespace normalization:
    # tree traversal indents every ``~~~`` and ``adding tangent space`` line.
    '~~~', '---', 'adding ', 'optimizing ', 'imposing ', 'counted ',
    'creating ', 'created ', 'merging ', 'skin ',
)


_WARN_CATEGORIES = {
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
        m = msg.strip().lower()
        if m.startswith(_PYFFI_PROGRESS_PREFIXES):
            continue
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
    'characters'
})

_WORKER_COUNT = worker_count()

OUTPUT_VERSION       = 0x14020007  # Skyrim SE NIF version
OUTPUT_USER_VERSION  = 12
OUTPUT_USER_VERSION_2 = 83

NIF_FLAGS = 14  # Standard Skyrim NiAVObject flags (SelectiveUpdate bits 1-3)


# Controller types vanilla Skyrim puts inside a NiControllerSequence's
# controlled blocks.  A NiControllerSequence stores its controller type as a
# STRING and the engine instantiates it BY NAME at load, so any type outside
# this set rejects the entire NIF (Skyrim's red missing-mesh triangle).
# Census of ~8,300 vanilla meshes (references/Skyrim Meshes) via
# tools/nif/nif_block_scan.py --histogram; NiFlipController and NiSourceTexture
# appear ZERO times, which is what killed the four Oblivion gate meshes.
_VANILLA_SEQ_CONTROLLERS = frozenset({
    'BSEffectShaderPropertyFloatController',
    'BSEffectShaderPropertyColorController',
    'BSLightingShaderPropertyFloatController',
    'BSLightingShaderPropertyColorController',
    'BSNiAlphaPropertyTestRefController',
    'BSFrustumFOVController',
    'BSLagBoneController',
    'BSProceduralLightningController',
    'BSPSysMultiTargetEmitterCtlr',
    'NiControllerManager',
    'NiMultiTargetTransformController',
    'NiTransformController',
    'NiVisController',
    'NiFloatExtraDataController',
    'NiBSBoneLODController',
    'NiPSysUpdateCtlr',
    'NiPSysEmitterCtlr',
    'NiPSysModifierActiveCtlr',
    'NiPSysEmitterSpeedCtlr',
    'NiPSysGravityStrengthCtlr',
    'NiPSysEmitterInitialRadiusCtlr',
    'NiPSysEmitterLifeSpanCtlr',
    'NiPSysEmitterPlanarAngleCtlr',
    'NiPSysEmitterDeclinationCtlr',
    'NiPSysInitialRotSpeedCtlr',
})

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


# --- Sky meshes ------------------------------------------------------------
#
# Sky geometry is NOT a world object.  Skyrim draws it through a dedicated sky
# pass keyed on BSSkyShaderProperty.Sky Object Type; a sky mesh that ships a
# BSLightingShaderProperty instead is lit, fogged and depth-sorted as ordinary
# world geometry, which is why converted stars drew ON TOP of the landscape.
#
# SkyObjectType enum (references/nif 0.10.0.0.xml):
#   0 BSSM_SKY_TEXTURE, 1 BSSM_SKY_SUNGLARE, 2 BSSM_SKY, 3 BSSM_SKY_CLOUDS,
#   5 BSSM_SKY_STARS, 7 BSSM_SKY_MOON_STARS_MASK
# Confirmed against the shipped meshes: sky/stars.nif is type 5 throughout and
# sky/clouds.nif is type 3.
SKY_TEXTURE, SKY_SUNGLARE, SKY_BASE = 0, 1, 2
SKY_CLOUDS, SKY_STARS, SKY_MOON_STARS_MASK = 3, 5, 7

# Oblivion's Sky/ meshes, keyed by lowercase basename, mapped to the sky object
# type Skyrim's sky pass expects.  Oblivion had no such enum — it identified sky
# geometry by which slot of the climate/weather record referenced it — so this
# table is the mapping between the two models and cannot be derived from the
# NIF itself.
_SKY_MESH_TYPES = {
    'stars.nif':            SKY_STARS,
    'stars_oblivion.nif':   SKY_STARS,
    'sestars.nif':          SKY_STARS,
    'clouds.nif':           SKY_CLOUDS,
    'clouds_oblivion.nif':  SKY_CLOUDS,
    'atmosphere.nif':       SKY_BASE,
    'sky.nif':              SKY_BASE,
    'sunbeam01.nif':        SKY_SUNGLARE,
    'sunbeam02.nif':        SKY_SUNGLARE,
    'sunbeam03.nif':        SKY_SUNGLARE,
}


def sky_object_type_for(src_path):
    """Return the BSSkyShaderProperty sky object type for a mesh, else None.

    Only meshes living under a `sky/` directory are eligible: the basenames
    alone are generic enough to collide with ordinary clutter.
    """
    if not src_path:
        return None
    norm = str(src_path).replace('\\', '/').lower()
    parts = norm.rsplit('/', 2)
    if len(parts) < 2 or parts[-2] != 'sky':
        return None
    return _SKY_MESH_TYPES.get(parts[-1])

#: Convertible source versions; anything else is skipped, not copied.
_SUPPORTED_VERSIONS = {
    0x14000004,  # Gamebryo 20.0.0.4 - the primary Oblivion format
    0x14000005,  # Gamebryo 20.0.0.5
    0x14020007,  # Gamebryo 20.2.0.7 - FO3/FNV
    0x0a020000,  # Gamebryo 10.2.0.0
    0x0a01006a,  # Gamebryo 10.1.0.106
    0x0a010065,  # Gamebryo 10.1.0.101
    0x0a000100,  # NetImmerse 10.0.1.0
    0x0a000102,  # NetImmerse 10.0.1.2
}

#: Already-Skyrim (version, user_version_2), copied out unchanged.
_SKYRIM_VERSIONS = {
    (0x14020007, 83),  # FO3/FNV share the version, differing only in uv2
}

#: Havok unit scale, Oblivion to Skyrim: bodies, mass centres, primitive dims.
_HAVOK_SCALE = 0.1

# ---------------------------------------------------------------------------
# Oblivion → Skyrim attachment point (Prn) name remapping.
# Oblivion NIF files carry a NiStringExtraData block named 'Prn' on the root
# node that tells the engine which skeleton node to attach the mesh to.
# Skyrim uses different node names, so we remap them here.
# Source: Skyrim skeleton.nif node survey + legacy BONE_MAP.
# ---------------------------------------------------------------------------
_PRN_REMAP: dict[str, str] = {
    'BackWeapon':  'WeaponBack',    # 2H weapons (bows refined to WeaponBow below)
    'SideWeapon':  'WeaponSword',   # 1H swords / generic 1H (refined by filename below)
    'Quiver':      'QUIVER',        # arrow quivers
    'Weapon':      'Weapon',        # already valid (keeps as-is)
    'Shield':      'SHIELD',        # shields
    # Skyrim carries the torch in the OFF-HAND, on the shield node: vanilla
    # meshes\weapons\torch\torch.nif ships Prn='SHIELD' (the static sconce
    # torches under clutter\ carry no Prn at all -- they are placed world
    # objects).  'NPC L MagicNode [LMag]' is the spell-CAST node: its axes
    # point outward from the open palm, so a torch attached there renders
    # rotated ~90deg with the flame off to the left.
    'Torch':       'SHIELD',
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
    # NOTE: shortswords deliberately NOT listed — they stay on WeaponSword.
    # The record converter maps TES4 Blade1H → Skyrim OneHandSword, and the
    # draw animation only finds the weapon at the node matching the record's
    # AnimationType (see convert_WEAP axe/mace comment).  Prn=WeaponDagger on
    # a Sword-type record = invisible when held.
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

# Skyrim-side (post-_remap_prn) Prn values of skeleton-attached equipment.
# These meshes are normalized into vanilla attachment frames during
# conversion, so the vanilla-derived BSInvMarker constants are exact — the
# per-mesh inventory-rotation pass must not override them.
_EQUIPPED_PRN_VALUES = frozenset({
    'Weapon', 'WeaponSword', 'WeaponDagger', 'WeaponMace', 'WeaponAxe',
    'WeaponStaff', 'WeaponBack', 'WeaponBow', 'SHIELD', 'QUIVER', 'Quiver',
})


def _remap_prn(oblivion_prn: str, nif_filename: str) -> str:
    """Map an Oblivion Prn value to the correct Skyrim skeleton node name.

    For 'SideWeapon' (all Oblivion 1H weapons), refines to per-type node by
    looking for weapon type keywords in the NIF filename.

    For 'BackWeapon' (all Oblivion 2H weapons AND bows), bows must go to
    'WeaponBow' — with 'WeaponBack' the draw animation never reparents the
    mesh to the hand, so an equipped bow stays glued to the back and the
    hands look empty.  Vanilla Skyrim bow NIFs all carry Prn=WeaponBow.
    """
    skyrim_prn = _PRN_REMAP.get(oblivion_prn, oblivion_prn)
    lower = nif_filename.lower()
    if oblivion_prn == 'SideWeapon':
        for keyword, prn in _WEAPON_FILENAME_PRN:
            if keyword in lower:
                return prn
    elif oblivion_prn == 'BackWeapon' and 'bow' in lower:
        return 'WeaponBow'
    return skyrim_prn


_SHIELD_ATTACH_T = None


def _shield_attach_transform():
    """4x4 mapping shield geometry from Oblivion attach space to Skyrim's.

    Oblivion shields attach to 'Bip01 L ForearmTwist' (strapped across the
    forearm, identity root transform); Skyrim attaches the NIF root to the
    'SHIELD' bone (child of the left hand, at the grip).  To keep the shield
    sitting on the arm EXACTLY as it did in Oblivion, we map between the two
    attach frames through an anatomical hand frame built from the same three
    landmarks on each skeleton (hand joint, middle-finger base, thumb base):

        T = W_obForearmTwist @ F_ob^-1 @ F_sk @ W_SHIELD^-1

    (row-vector convention, matching skeleton_bones_*.json).  Applying T as
    the shield NIF's root transform reproduces the Oblivion placement
    relative to the arm; validated against vanilla ironshield.nif — the
    result lands on the Skyrim convention (face in XY plane, dome toward -Z,
    grip near the origin) within a few units.

    Returns a 4x4 numpy array (rotation rows 0-2, translation row 3), or
    None if the skeleton JSONs are unavailable.
    """
    global _SHIELD_ATTACH_T
    if _SHIELD_ATTACH_T is not None:
        return _SHIELD_ATTACH_T

    import json as _json
    gen = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'generated')
    try:
        with open(os.path.join(gen, 'skeleton_bones_oblivion.json')) as f:
            ob = {k: np.array(v, dtype=np.float64) for k, v in _json.load(f).items()}
        with open(os.path.join(gen, 'skeleton_bones_skyrim_male.json')) as f:
            sk = {k: np.array(v, dtype=np.float64) for k, v in _json.load(f).items()}

        def _anat_hand_frame(hand, mid_base, thumb_base):
            """Rows: [finger-dir, thumb-dir, cross, hand-origin] anatomy->world."""
            h = hand[3, :3]
            fdir = mid_base[3, :3] - h
            fdir /= np.linalg.norm(fdir)
            tdir = thumb_base[3, :3] - h
            tdir = tdir - (tdir @ fdir) * fdir
            tdir /= np.linalg.norm(tdir)
            q = np.cross(fdir, tdir)
            q /= np.linalg.norm(q)
            F = np.eye(4)
            F[0, :3] = fdir
            F[1, :3] = tdir
            F[2, :3] = q
            F[3, :3] = h
            return F

        f_ob = _anat_hand_frame(ob['Bip01 L Hand'], ob['Bip01 L Finger2'],
                                ob['Bip01 L Finger0'])
        f_sk = _anat_hand_frame(sk['NPC L Hand [LHnd]'], sk['NPC L Finger20 [LF20]'],
                                sk['NPC L Finger00 [LF00]'])
        T = (ob['Bip01 L ForearmTwist'] @ np.linalg.inv(f_ob)
             @ f_sk @ np.linalg.inv(sk['SHIELD']))

        # Forearm-clearance correction.  T preserves the shield's pose
        # relative to the OBLIVION forearm, but the Skyrim forearm leaves the
        # hand at a different angle (~16° out of the strap plane, elbow at
        # SHIELD-local z=+7.2 vs the shield back face at z≈+2) — the arm pokes
        # through the shield.  Rotate about the grip (origin) so the mapped
        # Oblivion forearm axis lands on the actual Skyrim forearm axis: the
        # shield lies along the real arm, hand position unchanged.
        w_s_inv = np.linalg.inv(sk['SHIELD'])[:3, :3]
        d_ob = ob['Bip01 L Forearm'][3, :3] - ob['Bip01 L Hand'][3, :3]
        d_ob /= np.linalg.norm(d_ob)
        d_ob = d_ob @ (np.linalg.inv(f_ob) @ f_sk)[:3, :3] @ w_s_inv
        d_ob /= np.linalg.norm(d_ob)
        d_sk = sk['NPC L Forearm [LLar]'][3, :3] - sk['NPC L Hand [LHnd]'][3, :3]
        d_sk /= np.linalg.norm(d_sk)
        d_sk = d_sk @ w_s_inv
        d_sk /= np.linalg.norm(d_sk)
        axis = np.cross(d_ob, d_sk)
        s = np.linalg.norm(axis)
        if s > 1e-6:
            axis /= s
            c = float(np.clip(d_ob @ d_sk, -1.0, 1.0))
            K = np.array([[0, -axis[2], axis[1]],
                          [axis[2], 0, -axis[0]],
                          [-axis[1], axis[0], 0]])
            # Rodrigues in row-vector convention (v @ R): transpose of the
            # standard column form.
            r_fix = np.eye(3) + s * K.T + (1 - c) * (K.T @ K.T)
            fix4 = np.eye(4)
            fix4[:3, :3] = r_fix
            T = T @ fix4

        _SHIELD_ATTACH_T = T
    except (OSError, KeyError, ValueError) as e:
        print(f'  WARNING: shield attach transform unavailable ({e}); '
              f'shield keeps Oblivion orientation')
        _SHIELD_ATTACH_T = None
    return _SHIELD_ATTACH_T


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


def _bs_pp_texture_slots(prop):
    """Diffuse, normal and glow paths from an FO3/FNV BSShaderPPLightingProperty.

    FO3/FNV keep their paths in a BSShaderTextureSet on this property rather
    than on NiTexturingProperty, in the same slot order Skyrim uses: 0 diffuse,
    1 normal, 2 glow. All three are AUTHORED, so the normal is taken verbatim
    instead of being derived from the diffuse name.

    See: docs/commentary/asset_convert_nif.md#fo3fnv-shader-properties
    """
    tex_set = getattr(prop, 'texture_set', None)
    if tex_set is None:
        return b'', b'', b''
    slots = list(getattr(tex_set, 'textures', ()) or ())

    def slot(i):
        """The i-th texture path, or empty when absent or blank."""
        return slots[i] if i < len(slots) and slots[i] else b''

    return slot(0), slot(1), slot(2)


def _rewrite_tex_path(raw_bytes):
    """Prepend tes4\\ to a texture path that doesn't already have it.

    Oblivion NIFs use both separators, sometimes in the same file, so
    normalise to backslash FIRST — testing only for 'textures\\' let a
    forward-slash 'textures/lowres/foo.dds' fall through to the else branch and
    come out as 'Textures\\tes4\\textures/lowres/foo.dds', a path that resolves
    to nothing (the LOD tiles then reference 100 textures that do not exist).

    'textures\\lowres\\' is an Oblivion _far.nif authoring convention for
    low-resolution LOD copies (pyffi ships a spell that writes exactly this
    prefix, documented "used mainly for making _far.nifs"). We do not ship a
    lowres tree — the converted textures live at the normal path — so the
    segment is dropped and the reference resolves to the real texture.

    A leading 'data\\' is an authoring slip Oblivion tolerates (it resolves
    paths from the Data folder either way) and Skyrim does not. Measured across
    Nehrim's 12,437 source meshes: 4 distinct textures in 10 meshes, among them
    dwarven\\rock02.dds in 7. Left in, the reference came out as
    'Textures\\tes4\\data\\textures\\...' — nothing there, AND the prune then
    deleted the real texture, because the manifest key never matched the
    shipped path.
    """
    path = raw_bytes.decode('utf-8', errors='replace').replace('/', '\\')
    if path.lower().startswith('data\\'):
        path = path[len('data\\'):]
    low = path.lower()

    if low.startswith('textures\\'):
        rest = path[len('textures\\'):]
    else:
        rest = path
    if rest.lower().startswith('lowres\\'):
        rest = rest[len('lowres\\'):]

    if rest.lower().startswith('tes4\\'):
        return 'Textures\\' + rest
    return 'Textures\\tes4\\' + rest


def _norm_tex_ref(raw):
    """Normalise a NIF texture path to a key relative to the textures root.

    'Textures\\tes4\\foo\\Bar.DDS' -> 'tes4/foo/bar.dds'.  Returns None for
    anything that isn't a texture (file_name also carries non-DDS paths).
    """
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode('utf-8', errors='replace')
    p = raw.strip().lower().replace('\\', '/').lstrip('/')
    if not p.endswith('.dds'):
        return None
    if p.startswith('textures/'):
        p = p[len('textures/'):]
    return p or None


def _harvest_textures(data, out):
    """Add every texture path the converted NIF references to the set *out*.

    Walks the finished blocks rather than the paths we rewrote, so it also picks
    up textures written by the particle/effect/flip-book branches and by any
    block we pass through untouched.
    """
    def add(raw):
        p = _norm_tex_ref(raw)
        if p:
            out.add(p)

    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            tex_set = getattr(block, 'texture_set', None)
            if tex_set is not None:
                for t in tex_set.textures:
                    add(t)
            add(getattr(block, 'source_texture', None))
            add(getattr(block, 'greyscale_texture', None))
            add(getattr(block, 'file_name', None))


def _harvest_texture_bytes(raw: bytes, out):
    """Scrape texture paths out of a NIF we copied through without parsing.

    Uses texture_prune's scanner rather than a local regex: the equivalent
    `[A-Za-z0-9_\\\\/ .()&+-]{3,200}?\\.dds` pattern opens with a lazy star, so
    it retried at every offset of every mesh (22.8x slower, measured).
    """
    for match in _texture_refs_in(raw):
        p = _norm_tex_ref(match)
        if p:
            out.add(p)


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


def _clamp_uv_sets(ts_data):
    """Reduce geometry data to the single UV set Skyrim reads.

    On disk the u16 "BS Data Flags" packs the UV-set COUNT in its low 6 bits
    (PyFFI exposes that half as num_uv_sets and bit 12 as extra_vectors_flags).
    That count is the only thing telling the engine how many TexCoord arrays
    follow the vertex colors, so a mesh that stores 2 sets while
    BSLightingShaderProperty binds 1 leaves the engine's vertex buffer a whole
    array short: the copy runs past the end of the allocation and faults on a
    non-temporal store (vmovntdq) at the next page boundary.

    Oblivion authors the extra set for detail/overlay passes that Skyrim has no
    slot for; set 0 is the diffuse UVs every shader samples, so the surplus is
    dropped rather than remapped.  Census: 2,233 vanilla shapes carry 0 or 1 UV
    sets and NEVER 2.
    """
    n = int(getattr(ts_data, 'num_uv_sets', 0) or 0)
    if n <= 1:
        return 0
    keep = list(ts_data.uv_sets[0]) if len(ts_data.uv_sets) else []
    ts_data.num_uv_sets = 1
    ts_data.uv_sets.update_size()
    if keep and len(ts_data.uv_sets):
        for dst, src in zip(ts_data.uv_sets[0], keep):
            dst.u = src.u
            dst.v = src.v
    return n - 1


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
        # NiUVController has NO RTTI in SkyrimSE.exe — NiStream cannot build it
        # and a link to the slot yields a non-NiObject pointer (CTD on load).
        # Its curves are re-emitted as shader float controllers by
        # _collect_uv_ctrls, which must run BEFORE this strip.
        if isinstance(ctrl, (NifFormat.NiGeomMorpherController,
                             NifFormat.NiMaterialColorController,
                             NifFormat.NiUVController)):
            # Unlink this controller from the chain.
            if prev is None:
                geom.controller = nxt
            else:
                prev.next_controller = nxt
        else:
            prev = ctrl
        ctrl = nxt


def _prune_orphan_roots(data):
    """Drop entries from data.roots that are not scene-graph roots.

    Many Nehrim meshes were authored by tools that leave dangling blocks —
    NiTriShapeData, NiTriStripsData, NiBinaryExtraData, bhkCollisionObject,
    NiTexturingProperty — in the block list with nothing referencing them.
    PyFFI reports every unreferenced block as a root, so data.roots comes back
    as [NiNode, NiTriShapeData, ...].  Every pass here assumes a root is a
    scene node and reads root.controller / root.children, which raises
    AttributeError on those orphans (the castle\\*_far.nif and
    artilleryduell\\flamecannonballnew.nif failures).

    The orphans are unreachable from the real root, so they are dead weight:
    dropping them both fixes the crash and shrinks the output.  Keeps every
    NiAVObject root, and keeps a non-NiAVObject root only if it is the sole
    root (nothing to fall back to — let the later passes deal with it).

    Returns the number of roots removed.
    """
    roots = [r for r in data.roots if r is not None]
    if len(roots) < 2:
        return 0

    keep = [r for r in roots if isinstance(r, NifFormat.NiAVObject)]
    if not keep:
        return 0

    # An orphan is only safe to drop if nothing we keep still references it.
    reachable = set()
    for r in keep:
        for block in r.tree():
            reachable.add(id(block))
    keep_ids = set(id(r) for r in keep)
    keep += [r for r in roots
             if id(r) not in keep_ids and id(r) in reachable]

    removed = len(roots) - len(keep)
    if removed <= 0:
        return 0

    data.roots = keep
    return removed


# Equip/sheath nodes Skyrim looks up by hard-coded name, and where each one
# hangs on a vanilla weapon-using creature rig (Draugr skeleton.nif node order:
# WeaponAxe/Sword/Mace sit by the pelvis, WeaponBack/Bow/QUIVER after the left
# pauldron on the upper spine, WEAPON under the right hand, SHIELD under the
# left).
#
# Oblivion rigs have only three attachment points — Weapon, Torch, Quiver —
# which the BONE_RENAMES pass turns into WEAPON/SHIELD/QUIVER.  The per-type
# SHEATH nodes have no Oblivion counterpart at all, so the converted rig simply
# lacked them: a converted weapon carries Prn=WeaponMace (the sheathed node,
# which is what vanilla Skyrim weapon meshes use), the engine could not find a
# node by that name, and the mesh fell back to the actor root — the weapon
# visibly slid around at the creature's feet instead of sitting in its hand,
# and no draw animation could ever reparent it.
#
# 'anchor' is the node to hang it under, in preference order (the first that
# exists on this rig wins); 'source' is the node whose LOCAL transform is
# copied, so the new node lands somewhere sensible for a rig of any size
# rather than at a hardcoded human offset.
#
# Oblivion creatures do NOT sheathe: every one of the 41 armed creature folders
# ships equip/unequip clips whose text keys are `attach`/`detach` (the AnimObject
# mechanism) -- the weapon is created in the hand and destroyed, never parked on
# the body.  38 of those 41 rigs accordingly carry NO Quiver/Shield/BackWeapon
# node at all.  So the per-type SHEATH nodes below exist only to give the ENGINE
# a node of the name it looks up; nothing is ever displayed at them, and the
# creature's own rig is the authority on what it needs.
#
# An earlier pass synthesized "proportionate" offsets for them from vanilla
# Skyrim ratios.  That was wrong on two counts: the placements disagreed with
# vanilla anyway (axe/mace hang on the RIGHT hip in Skyrim, the guessed offset
# put them on the left), and no Oblivion creature has anything to place there.
# The node is created at the anchor's origin, which is what a node nothing
# renders at should be.
_CREATURE_EQUIP_NODES = (
    # name,           anchors (first match wins),               source
    ('WeaponSword',  ('Bip01 Pelvis', 'Bip01 Spine'),           'WEAPON'),
    ('WeaponDagger', ('Bip01 Pelvis', 'Bip01 Spine'),           'WEAPON'),
    ('WeaponAxe',    ('Bip01 Pelvis', 'Bip01 Spine'),           'WEAPON'),
    ('WeaponMace',   ('Bip01 Pelvis', 'Bip01 Spine'),           'WEAPON'),
    ('WeaponBack',   ('Bip01 Spine2', 'Bip01 Spine1',
                      'Bip01 Spine'),                           'QUIVER'),
    ('WeaponBow',    ('Bip01 Spine2', 'Bip01 Spine1',
                      'Bip01 Spine'),                           'QUIVER'),
    ('WeaponStaff',  ('Bip01 Spine2', 'Bip01 Spine1',
                      'Bip01 Spine'),                           'QUIVER'),
)

# Spell-cast nodes. Vanilla rigs carry one per hand plus a body-centre node;
# without them a casting creature's effect art has nowhere to attach.
_CREATURE_MAGIC_NODES = (
    ('NPC L MagicNode [LMag]', ('Bip01 L Hand',),               'SHIELD'),
    ('NPC R MagicNode [RMag]', ('Bip01 R Hand',),               'WEAPON'),
    ('MagicEffectsNode',       ('Bip01 Spine', 'Bip01 Spine1'), None),
)


def _add_creature_equip_nodes(data):
    """Give a converted creature rig the equip/sheath nodes Skyrim expects.

    Returns the number of nodes added.  Runs AFTER the BONE_RENAMES pass so the
    renamed WEAPON/SHIELD/QUIVER nodes are available as transform sources, and
    is a no-op for any node the rig already has (so re-running is safe and a rig
    that legitimately ships one keeps its own).
    """
    added = 0
    for root in data.roots:
        if root is None:
            continue
        by_name, parent_of = {}, {}
        for block in root.tree():
            if not isinstance(block, NifFormat.NiNode):
                continue
            by_name.setdefault(
                bytes(block.name).rstrip(b'\x00').decode(
                    'cp1252', 'replace'), block)
            for child in block.children or []:
                if isinstance(child, NifFormat.NiNode):
                    parent_of[id(child)] = block

        for name, anchors, source in (_CREATURE_EQUIP_NODES
                                      + _CREATURE_MAGIC_NODES):
            if name in by_name:
                continue
            parent = next((by_name[a] for a in anchors if a in by_name), None)
            if parent is None:
                continue
            node = NifFormat.NiNode()
            node.name = name.encode('latin-1')
            # Copy a sibling attachment point's local transform where we have
            # one; otherwise sit at the anchor's origin.  Either way the node
            # is scaled to THIS creature, not to a human.
            src = by_name.get(source) if source else None
            if src is not None and parent_of.get(id(src)) is parent:
                node.translation.x = src.translation.x
                node.translation.y = src.translation.y
                node.translation.z = src.translation.z
                node.rotation = src.rotation
            node.scale = 1.0
            node.flags = parent.flags
            parent.add_child(node)
            by_name[name] = node
            added += 1
    return added


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


def _resolve_source_texture(tex_rel, src_nif_path,
                            fallback_roots=()):
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
    if os.path.isfile(cand):
        return cand
    # MASTER-EXPORT BLINDNESS, the asset half of it.  An imported mod ships
    # only the files it changes; everything else lives in its MASTER's export
    # tree, which deriving the root from the mesh path can never reach.
    # Measured on the author's parallax mod: of the 3357 distinct texture paths
    # its 8665 meshes reference, 1464 were in the mod and 1602 ONLY in
    # Nehrim.esm.  Unreachable means no height map and no specular verdict.
    for root in (fallback_roots or ()):
        cand = os.path.join(root, rel.replace('\\', os.sep))
        if os.path.isfile(cand):
            return cand
    return None


# NiTextureTransformController.operation (TransformMember) → the Skyrim shader
# float-controller variable that does the same thing.  Oblivion scrolls/scales a
# texture by animating the NiTexturingProperty's texture transform; Skyrim has no
# NiTexturingProperty, so the equivalent is a BS*ShaderPropertyFloatController on
# the shader's UV offset/scale (vanilla FXWaterfallThin512x128 chains U Scale +
# V Offset + U Offset exactly this way).  TT_ROTATE has NO Skyrim equivalent —
# neither shader exposes a UV rotation float — so it is dropped, not faked.
_TT_TRANSLATE_U, _TT_TRANSLATE_V, _TT_ROTATE, _TT_SCALE_U, _TT_SCALE_V = range(5)

# operation -> (LightingShaderControlledFloat, EffectShaderControlledVariable)
_TEX_TRANSFORM_VARS = {
    _TT_TRANSLATE_U: (20, 6),   # U Offset
    _TT_TRANSLATE_V: (22, 8),   # V Offset
    _TT_SCALE_U:     (21, 7),   # U Scale
    _TT_SCALE_V:     (23, 9),   # V Scale
}


# NiTexturingProperty.apply_mode = APPLY_HILIGHT2: Oblivion's PARALLAX switch.
# The diffuse's alpha channel holds a HEIGHT FIELD, not transparency and not a
# blend weight -- see asset_convert/parallax.py for both engines' mechanism.
# Skyrim reads the same channel as plain opacity, so the alpha property must be
# dropped either way (see the alpha handling in _process_geometry); with
# --parallax the height is additionally carried across into a slot-3 map.
_APPLY_HILIGHT2 = 4

# NiMaterialColorController.target_color: which material channel the curve
# drives.  3 = TC_SELF_ILLUM (emissive) -- the only one with a Skyrim analogue.
_MATERIAL_COLOR_EMISSIVE = 3

# BS*ShaderPropertyColorController.type_of_controlled_color -- the two shaders
# number this differently.  Vanilla census of 361 meshes carrying a shader
# ColorController: Lighting uses 1 for emissive (124 blocks, vs 5 at 0 which is
# Specular), Effect uses 0 (46 blocks).  (Lighting, Effect):
_SHADER_COLOR_EMISSIVE = (1, 0)

# BS*ShaderPropertyFloatController variable for opacity, (Lighting, Effect).
# Per references/nif 0.10.0.0.xml: Lighting 12 = "Alpha", Effect 5 = "Alpha
# Transparency"; both appear in the vanilla float-controller census.
_SHADER_ALPHA_VAR = (12, 5)

# NiVertexColorProperty.lighting_mode.  LIGHTING_E (0) = "emissive only": the
# surface ignores scene lighting entirely.  That is Oblivion's declaration of
# an UNLIT FX surface, and it maps onto Skyrim's BSEffectShaderProperty, while
# LIGHTING_E_A_D (1) -- ordinary lit geometry -- maps onto
# BSLightingShaderProperty.  See the shader choice in _process_geometry.
_LIGHTING_EMISSIVE_ONLY = 0

# NiAlphaProperty.flags: bit 0 = alpha blending enabled.  Everything below keys
# off "does this FX surface alpha-blend", which is the discriminator vanilla
# itself uses for the soft-particle depth fade (see _apply_fx_soft_effect).
_ALPHA_BLEND_ENABLED = 0x0001

# NiAlphaProperty.flags bits 5-8 = destination blend factor.  0 = GL_ONE, i.e.
# ADDITIVE blending (the surface adds its color to whatever is behind it).
# Oblivion's FX quads are authored this way; ordinary lit geometry never is.
_ALPHA_DST_SHIFT = 5
_ALPHA_DST_ONE = 0

# Skyrim's soft-particle depth fade.  A blended FX quad that intersects solid
# geometry is normally cut off along the intersection line, so a smoke/mist
# billboard standing in a floor or wall shows the QUAD'S OWN RECTANGULAR EDGE --
# the "distracting bounding box around transparent effects".  slsf_1_soft_effect
# makes the engine fade the quad out over Soft Falloff Depth units of depth
# difference instead, which is what removes the hard edge.
#
# Oblivion has no equivalent flag (its FX quads are simply hand-placed to avoid
# intersections), so there is no source field to carry across -- the value comes
# from what vanilla Skyrim does with the same kind of surface.  Census of 1,198
# BSEffectShaderProperty shapes across meshes/effects + meshes/dungeons:
#
#   alpha 0x100d (additive)  n=470   soft_effect=1 in 417 (89%)
#   alpha 0x10ed (blend)     n=362   soft_effect=1 in 224 (62%)
#   no NiAlphaProperty       n=332   soft_effect=0 in 322 (97%)
#
# So: blended FX gets the fade, unblended FX does not.  100.0 is the commonest
# falloff depth in the same census (250/521 on mist/smoke/fog geometry) and is
# the value vanilla uses for ambient room fog, which is exactly this case.
_SOFT_FALLOFF_DEPTH = 100.0


# A FLAME is not identified by its texture NAME.  An earlier revision matched
# b'fire'/b'flame'/b'torch' in the diffuse path (minus a
# smoke/mist/fog/dust/steam/cloud veto) and boosted anything that hit to
# emissive_multiple 1.5.  That is classification by filename, and it is wrong
# in both directions: in Oblivion's own tree it caught textures\lights\torch02.dds
# -- the WOODEN HANDLE, whose host lights\torch02noflame.nif has no flame in it
# at all -- and it can only ever work for meshes that follow Bethesda's naming,
# never for Nehrim, Morroblivion or any third-party plugin.
#
# Oblivion states the brightness itself, per SHAPE, in
# NiMaterialProperty.emissive_color.  Measured across every particle system in
# meshes/ (all 778 of them) the two populations do not overlap:
#
#   flames   fire\firetorchlarge "Fire"        (1.000, 1.000, 1.000)
#            crtfirelogs "PCloud08BigFlame"    (1.000, 1.000, 1.000)
#   fog      fx\fxcloudthick01 "Cloud"         (0.078, 0.078, 0.078)
#            fx\fxcloudthin01  "Cloud"         (0.047, 0.047, 0.047)
#            fx\fxdustcloud01  "PCloud02v"     (0.337, 0.337, 0.294)
#
# Distribution: 227 author full 1.0 white, 190 a dim <0.5, 202 in between, and
# 159 author black (which already falls back to white).
#
# So the authored value IS the discriminator, at better than 12x separation,
# and it is per-shape -- which matters because firetorchlargesmoke.nif holds a
# flame AND a smoke plume in one file and any per-file test must give them the
# same answer.  Carry emissive_color through verbatim and hold the multiple at
# the vanilla-neutral 1.0; a flame authored full white is already at full
# emission and needs no boost.


# A SELF-LIT flame must not be soft-faded, and the authored emissive says so.
#
# The depth fade attenuates a quad against whatever it intersects.  On ambient
# fog that is the whole point -- it removes the rectangular quad edge where the
# plane cuts the floor.  On a FLAME it is destructive: a candle flame sits
# directly on its own wax and a sconce flame against its own bracket, so the
# fade dims the flame into the very object it is mounted on.  Vanilla Skyrim
# authors exactly this split inside ONE mesh -- mps\mpscandleflame01.nif, both
# particle systems, both additive 0x100d, both emissive_multiple 1.0:
#
#   CandleFlame01  (the flame)  soft_effect=0  falloff 2.0
#   CandleGlow01   (the halo)   soft_effect=1  falloff 6.0
#
# and the same holds for every mounted fire core in the vanilla corpus:
# slighthousefire "Fireball", torchsconce01 "pFireballCore04",
# giantcampfire01burning "PFireball" -- all soft_effect=0 (49 such particle
# systems across 281 vanilla fire meshes).
#
# Skyrim's own value is NOT reconstructible from structure -- measured over
# those 511 vanilla FX shaders, neither block type (particle 119/168 soft=1 vs
# geometry 159/343), nor alpha flags (0x100d splits 163/74), nor double_sided
# (78% vs 44%) predicts it; it is authored per effect.  Oblivion has no
# equivalent field to carry across either.  So key it on the one authored
# quantity that DOES separate the two populations -- the same
# NiMaterialProperty.emissive_color that drives brightness:
#
#   flames    fire\firetorchlarge, firecandleflame, fireopen*   (1.0, 1.0, 1.0)
#   fog/dust  fxcloudthick01 0.078, fxcloudthin01 0.047,
#             fxdustcloud01 0.337, sefxmistdemen 0.310          all <= 0.34
#
# A surface authored at FULL WHITE is declaring "I am the light source" and is
# left hard; anything dimmer is ambient haze and takes the fade.  Erring here
# is asymmetric: a missing fade only leaves a Skyrim-era nicety off a flame,
# while a wrongly-applied one erases the flame outright.
_FX_SELF_LIT_EMISSIVE = 0.999

# ...and a self-lit surface is BOOSTED, not left neutral.  1.0 is the mode
# across all vanilla FX, but that population is mostly smoke, mist and glow
# planes.  Restrict the census to the shapes that actually match this branch --
# vanilla FX that are full-white AND soft_effect=0, i.e. self-lit surfaces
# mounted against geometry -- and 1.0 is the minority:
#
#   mult  1.0  1.1  1.25  1.5  1.6  2.0  3.0
#   n      16    9     6    5   43    6    5     (74 of 90 are ABOVE 1.0)
#
# with a median of 1.6 and the burning cores clustered at the top:
# torchsconce01 pFireballCore04 1.50 (Torch:0 1.25), giantcampfire01burning
# PCloudForgeSparks 1.25, fxsmokelargeclose01 Flames 1.60.  The 1.0 entries are
# `*off*` variants and non-flame parts (GlowMesh, lamp bodies).
#
# The flames commit's 1.5 was therefore the RIGHT VALUE on the wrong test: it
# keyed on the texture filename.  Holding every flame at the neutral 1.0 made
# fire\fireopensmall.nif and its siblings visibly dimmer than they had been,
# which the project owner spotted in game.  1.5 sits inside the vanilla cluster
# and is what the previous build shipped, so it is also the no-regression
# choice.
_FX_SELF_LIT_MULTIPLE = 1.5


def _apply_fx_soft_effect(eff_shader, alpha_prop, emissive_rgb=None):
    """Enable the soft-particle depth fade on a blended FX shader.

    Keyed on the source's own NiAlphaProperty: blending on -> fade, off/absent
    -> leave hard (matching the vanilla split above).  A quad that does not
    blend has no soft edge to preserve in the first place.

    SELF-LIT surfaces (authored full-white emissive) are excluded -- see the
    census above: a fade on a mounted flame attenuates it against its own
    holder and the flame disappears.  They also take vanilla's emissive BOOST,
    for which see _FX_SELF_LIT_MULTIPLE.
    """
    if alpha_prop is None:
        return False
    if not (int(alpha_prop.flags) & _ALPHA_BLEND_ENABLED):
        return False
    if emissive_rgb is not None and all(
            c >= _FX_SELF_LIT_EMISSIVE for c in emissive_rgb):
        eff_shader.shader_flags_1.slsf_1_soft_effect = 0
        eff_shader.emissive_multiple = _FX_SELF_LIT_MULTIPLE
        return False
    eff_shader.shader_flags_1.slsf_1_soft_effect = 1
    eff_shader.soft_falloff_depth = _SOFT_FALLOFF_DEPTH
    return True

# Diffuse for a shape whose Oblivion source carries no NiTexturingProperty.
# Skyrim's lighting shader dereferences the diffuse without a null check, so
# "no texture" is not representable -- see the else branch in _process_geometry.
# white.dds is vanilla Skyrim's own neutral texture (shipped in the SSE BSAs),
# so the material color we carry across shows through unmodified.
_DEFAULT_DIFFUSE_TEXTURE = b'Textures\\white.dds'


def _collect_tex_transform_ctrls(props):
    """Harvest animated NiTextureTransformControllers from Oblivion properties.

    Returns a list of (source controller, NiFloatData) for the base-texture
    slot only; the caller reads operation/timing off the source controller.
    Skipped: TT_ROTATE (no Skyrim equivalent), non-base slots (Skyrim shaders
    expose one UV transform, applied to all maps), and controllers whose curve
    can't be translated — a NiBlendFloatInterpolator is driven by a
    NiControllerManager sequence rather than inline keys (46/127 in Nehrim, all
    on skull/fireball meshes), and a single key is a constant, not an animation.
    """
    out = []
    for prop in props:
        if not isinstance(prop, NifFormat.NiTexturingProperty):
            continue
        ctrl = prop.controller
        while ctrl is not None:
            if (isinstance(ctrl, NifFormat.NiTextureTransformController) and
                    ctrl.operation in _TEX_TRANSFORM_VARS and
                    getattr(ctrl, 'texture_slot', 0) == 0):
                interp = getattr(ctrl, 'interpolator', None)
                data = getattr(interp, 'data', None) if interp is not None else None
                keys = getattr(data, 'data', None) if data is not None else None
                if keys is not None and keys.num_keys >= 2:
                    out.append((ctrl, data))
            ctrl = getattr(ctrl, 'next_controller', None)
    return out


# NiUVData.uv_groups is a fixed 4-entry array in this order.  The values are
# the same UV offset/scale curves NiTextureTransformController carries, so they
# reuse _TEX_TRANSFORM_VARS via the equivalent TT_* operation.
_UV_GROUP_OPS = (_TT_TRANSLATE_U, _TT_TRANSLATE_V, _TT_SCALE_U, _TT_SCALE_V)


class _SyntheticTexTransform:
    """Adapter making a NiUVController group look like a NiTextureTransformController.

    _attach_tex_transform_ctrls only reads operation/flags/frequency/phase/
    start_time/stop_time off the source, so a small stand-in is enough and
    avoids duplicating the emit logic.
    """

    def __init__(self, src, operation):
        self.operation = operation
        self.flags = int(getattr(src, 'flags', 0))
        self.frequency = getattr(src, 'frequency', 1.0) or 1.0
        self.phase = getattr(src, 'phase', 0.0)
        self.start_time = src.start_time
        self.stop_time = src.stop_time


def _collect_uv_ctrls(geom):
    """Harvest animated NiUVControllers from a geometry node's controller chain.

    NiUVController is Oblivion's UV-scroll animation (Morrowind's Ghostfence
    shimmer, ex_gg_fence*).  **SkyrimSE.exe has no NiUVController RTTI at all**
    — searching its RTTI for "NiUV" returns only NiUVData — so NiStream cannot
    construct the block, and a link to that slot hands NiPointer a non-NiObject
    pointer: the engine then does `lock cmpxchg` on a "refcount" inside
    read-only .rdata and takes an access violation while loading the mesh.

    The curve itself survives: NiUVData.uv_groups holds the same U/V offset and
    scale key groups a NiTextureTransformController would, so each populated
    group becomes one BS*ShaderPropertyFloatController, exactly as the
    NiTextureTransformController path does.  Returns (source, NiFloatData)
    tuples for _attach_tex_transform_ctrls.
    """
    out = []
    ctrl = getattr(geom, 'controller', None)
    while ctrl is not None:
        nxt = getattr(ctrl, 'next_controller', None)
        if isinstance(ctrl, NifFormat.NiUVController):
            data = getattr(ctrl, 'data', None)
            groups = list(getattr(data, 'uv_groups', []) or []) if data else []
            for gi, group in enumerate(groups[:len(_UV_GROUP_OPS)]):
                # A single key is a constant, not an animation.
                if group is None or group.num_keys < 2:
                    continue
                fdata = NifFormat.NiFloatData()
                fdata.data.num_keys = group.num_keys
                fdata.data.interpolation = group.interpolation
                fdata.data.keys.update_size()
                for dst, src_key in zip(fdata.data.keys, group.keys):
                    dst.time = src_key.time
                    dst.value = src_key.value
                    for extra in ('forward', 'backward'):
                        if hasattr(dst, extra) and hasattr(src_key, extra):
                            setattr(dst, extra, getattr(src_key, extra))
                out.append((_SyntheticTexTransform(ctrl, _UV_GROUP_OPS[gi]),
                            fdata))
        ctrl = nxt
    return out


def _attach_tex_transform_ctrls(shader, harvested):
    """Re-emit harvested texture transforms as a Skyrim shader controller chain.

    Vanilla chains one BS*ShaderPropertyFloatController per animated UV channel
    through next_controller (fxwaterfallthin512x128 does U Scale -> V Offset ->
    U Offset), so we mirror that.  The NiFloatData is reused as-is: both engines
    interpret the curve as a UV-space offset/scale over time, so the Oblivion
    keys (e.g. waterfall V 0.0 -> -2.0 over 3.3s) are already correct.
    """
    if not harvested:
        return
    is_effect = isinstance(shader, NifFormat.BSEffectShaderProperty)
    ctrl_type = (NifFormat.BSEffectShaderPropertyFloatController if is_effect
                 else NifFormat.BSLightingShaderPropertyFloatController)

    head = None
    tail = None
    for src_ctrl, fdata in harvested:
        new = ctrl_type()
        # 0x48 = Active | Compute Scaled Time, the value on every vanilla
        # shader float controller.  Oblivion ships 0x08 (Active only); without
        # the scaled-time bit the curve does not advance.  Preserve the source
        # cycle bits (0x06) so CLAMP/REVERSE loops survive.
        new.flags = 0x48 | (int(getattr(src_ctrl, 'flags', 0)) & 0x06)
        new.frequency = getattr(src_ctrl, 'frequency', 1.0) or 1.0
        new.phase = getattr(src_ctrl, 'phase', 0.0)
        new.start_time = src_ctrl.start_time
        new.stop_time = src_ctrl.stop_time
        new.target = shader
        new.type_of_controlled_variable = _TEX_TRANSFORM_VARS[src_ctrl.operation][1 if is_effect else 0]

        interp = NifFormat.NiFloatInterpolator()
        interp.float_value = -3.4028234663852886e+38   # vanilla "use data" sentinel
        interp.data = fdata
        new.interpolator = interp

        if head is None:
            head = new
        else:
            tail.next_controller = new
        tail = new

    # Preserve anything already on the shader (e.g. the flip-book U-Offset
    # controller built for NiFlipController meshes) at the end of the chain.
    tail.next_controller = shader.controller
    shader.controller = head


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
        f = _resolve_source_texture(rel, src_nif,
                                    stats.get('_tex_fallback', ()))
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


# Alpha classification is a full scan of a texture's top mip, and 2359 flagged
# shapes share only 130 diffuse textures — without this the same DDS would be
# decoded eighteen times over.  Keyed on the resolved absolute path, so it
# holds per worker process and stays deterministic.
_PARALLAX_ALPHA_CACHE = {}


def _plan_parallax(diffuse_rel, stats):
    """Decide whether this shape's diffuse can carry a Skyrim height map.

    Two independent questions, both of which must answer yes — see
    asset_convert/parallax.py.  The mesh flag (checked by the caller) is the
    AUTHORED intent; this is the measurement of whether there is anything to
    carry.  Returns the height map's texture path, or None.

    Registers a build job in stats; convert_nif executes it, because only it
    knows the output tree.
    """
    from . import parallax
    src = _resolve_source_texture(diffuse_rel,
                                  stats.get('_src_path', ''),
                                  stats.get('_tex_fallback', ()))
    if src is None:
        stats['parallax_texture_unresolved'] = \
            stats.get('parallax_texture_unresolved', 0) + 1
        return None
    key = src.lower()
    info = _PARALLAX_ALPHA_CACHE.get(key)
    if info is None:
        try:
            with open(src, 'rb') as f:
                raw = f.read()
        except OSError:
            raw = b''
        info = parallax.classify_alpha(raw)
        _PARALLAX_ALPHA_CACHE[key] = info
    if not info.usable:
        # Named per category, not a single "skipped" counter: two thirds of the
        # flagged textures have nothing to carry, and the build log has to say
        # WHY or the next person re-measures all 130 of them.
        stats[f'parallax_skipped_{info.kind}'] = \
            stats.get(f'parallax_skipped_{info.kind}', 0) + 1
        return None

    rel = parallax.height_path(diffuse_rel)
    jobs = stats.setdefault('_parallax_maps', {})
    jobs[rel.lower()] = {'height_rel': rel, 'src': src}
    return rel


# Vanilla Skyrim's shader-type-0 glossiness, measured over a random 1500-mesh
# sample of references/Skyrim Meshes: 80.0 is both the median and the modal
# value (1333 of 2961 type-0 shaders) and the modal value in 12 of 15 top
# folders.  See tools/shader_value_census.py.
from . import base_plugins as _base_plugins

# Re-exported: convert.py writes the file, the census tools read it.
BASE_PLUGINS_FILE = _base_plugins.FILE_NAME


def master_texture_roots(mesh_dir):
    """Texture trees to fall back on, in order, for the mod at `mesh_dir`.

    See asset_convert/base_plugins for where a tree's base comes from.
    """
    mesh_dir = str(mesh_dir).replace('/', os.sep)
    key = os.sep + 'meshes'
    i = mesh_dir.lower().rfind(key)
    if i < 0:
        return ()
    return _base_plugins.subdirs(mesh_dir[:i], 'textures')


_DEFAULT_GLOSSINESS = 80.0

# EVERY shape gets the same specular strength, and the modulation lives in the
# normal map's alpha where Skyrim expects it.  Where a source has no usable
# mask, `landscape_normals.normalize_specular_alpha` bakes a constant 64/255
# into the texture instead -- 64/255 = 0.251, i.e. EXACTLY the per-mesh 0.25
# this used to write, so the two encodings render identically.
#
# 🔴 The point is not the pixels, it is who can change them afterwards.  A
# strength baked into 20,000 NIFs is a TRAP for anyone who later ships real
# specular maps: their good mask would be multiplied by 0.25, and fixing it
# means editing every mesh rather than dropping in a texture.  The alpha is
# overridable by definition.  Uniform 1.0 is also vanilla's mode (44.7%).
#
# It also retires the double damping recorded in shader_value_mapping.md:
# landscape was 0.125 (alpha) x 0.25 (strength) = 0.03, and is now 0.125.
_SPEC_STRENGTH = 1.0

# Classifying a normal map means reading its first mip, and one texture is
# shared by many shapes -- the same reason _PARALLAX_ALPHA_CACHE exists.  Keyed
# on the resolved absolute path, so it holds per worker process.
_SPEC_MASK_CACHE = {}


# The shared stand-in normal map, written once per plugin by the texture stage
# (landscape_normals.write_default_normal).  Named here so the mesh stage and
# the texture stage cannot drift apart.
_DEFAULT_NORMAL_TEXTURE = 'Textures\\' + \
    landscape_normals.DEFAULT_NORMAL_REL.split('\\', 1)[1]


def _normal_exists(normal_rel, stats):
    """Is there a real source file behind this DERIVED `_n` path?

    Uses the same resolution as `_has_spec_mask`, master fallback included --
    a mod's mesh usually names a normal that lives in its BASE's tree, and
    without the fallback every one of those would look absent and get
    needlessly replaced by the stand-in.
    """
    if not normal_rel:
        return False
    return _resolve_source_texture(normal_rel, stats.get('_src_path', ''),
                                   stats.get('_tex_fallback', ())) is not None


def _resolve_map_for(diffuse, suffix, stats):
    """The best real `<diffuse base><suffix>.dds` for a diffuse, or None.

    Oblivion's base-name rule is not specific to normal maps -- it applies to
    every derived map, glow (`_g`) included.  Keeping this generic means the
    next slot we implement inherits it instead of reinventing it, which is the
    failure mode this replaced: `_n` had the rule, nothing else would have.
    """
    base = diffuse.rsplit('.', 1)[0] if '.' in diffuse else diffuse
    own = base + suffix + '.dds'
    if _normal_exists(own, stats):
        return own
    head, sep, _tail = base.rpartition('_')
    if sep and head:
        shared = head + suffix + '.dds'
        if _normal_exists(shared, stats):
            return shared
    return None


def _resolve_normal_for(diffuse, stats):
    """The best real normal map for a diffuse, or None.

    Oblivion does not store the normal's path -- it appends `_n` to the
    diffuse -- and when the variant's own `_n` is absent it falls back to the
    BASE name, the part before the last `_`.  That is intended engine
    behaviour, confirmed by the project owner from their own research
    (2026-08-26); it is why `BrumaWoodPost_Dark.dds` and
    `BrumaWoodPost_Grey.dds` both render with `BrumaWoodPost_n.dds` and ship
    no normal of their own.  Deriving from the full name alone invents
    `BrumaWoodPost_Dark_n.dds`, which exists nowhere, and dropping straight to
    a flat stand-in would discard a real normal sitting right beside it.

    Measured over the merged Nehrim texture tree: of the variants whose own
    `_n` is missing, 201 have one under the base name, against 48 that ship
    their own alongside the base's -- and those 48 are unaffected, because the
    variant's own is tried FIRST.  The suffixes involved are colour and state
    words throughout (`_dark`, `_black`, `_red`, `_harvested`, `_haunted`,
    `_01`), i.e. variants of one surface rather than different materials.

    Only ONE separator is stripped, and only when the result actually exists
    on disk -- this never guesses a path into being.
    """
    return _resolve_map_for(diffuse, '_n', stats)


def _has_spec_mask(normal_rel, stats):
    """True when slot 1's alpha is a usable specular mask.

    Counts its verdict into `stats` per category, because "no specular" has
    three quite different causes and a build log that says only "off" sends the
    next person back to every normal map in the tree.
    """
    from . import spec_mask
    if not normal_rel or stats is None:
        return False
    rel = normal_rel.decode('utf-8', 'replace') \
        if isinstance(normal_rel, bytes) else normal_rel
    src = _resolve_source_texture(rel, stats.get('_src_path', ''),
                                  stats.get('_tex_fallback', ()))
    if src is None:
        stats['spec_missing_normal'] = stats.get('spec_missing_normal', 0) + 1
        return False
    key = src.lower()
    kind = _SPEC_MASK_CACHE.get(key)
    if kind is None:
        try:
            with open(src, 'rb') as f:
                raw = f.read()
        except OSError:
            raw = b''
        kind = spec_mask.classify_bytes(raw)
        _SPEC_MASK_CACHE[key] = kind
    stats[f'spec_{kind}'] = stats.get(f'spec_{kind}', 0) + 1
    return kind == 'mask'


# Oblivion's distant-LOD tier meshes.  `_far` is the convention; `_far8` and
# `_far16` are the coarser tiers `lod_far_gen` derives beside it.
_LOD_TIER_SUFFIXES = ('_far', '_far8', '_far16')


def _is_lod_tier_mesh(src_path) -> bool:
    """True for a `_far` / `_far8` / `_far16` distant-LOD tier mesh."""
    stem = os.path.splitext(os.path.basename(str(src_path or '')))[0].lower()
    return stem.endswith(_LOD_TIER_SUFFIXES)


# Slot 2 and shader type 2, per Arcane University's texture-slot table:
# "2 | Glow | Glow map / Skin Tint | none | _g / _sk.dds | BC1".
GLOW_SLOT = 2
SHADER_TYPE_GLOWMAP = 2


def _apply_glow(shader, tex_set, glow_path, stats):
    """Give a shape Skyrim's GLOW shader when Oblivion had a glow map for it.

    Oblivion does not require the NIF to name this texture.  It derives
    `<diffuse base>_g.dds` exactly as it derives `_n`, so most glowing shapes
    name nothing at all: measured over a random 1200-mesh sample of Nehrim,
    227 shapes have a `_g` on disk for their diffuse and only 31 name it in
    NiTexturingProperty's glow slot.  Reading the slot alone therefore missed
    86% of the glow content.  The named path still wins when present -- it is
    authored -- and derivation is the fallback, base-name aware via
    `_resolve_map_for` (see there for Oblivion's variant rule).

    🔴 This is not only about missing glow: without it the conversion is
    actively WRONG.  Arcane University on Emissive Color -- "if the shader
    type is not 'Glow Shader', it will make the WHOLE MESH glow" -- while the
    glow shader "allows per-texel glow ... applied additively using the color
    map in texture slot 2".  So a rune stone whose glyph should glow was
    flooding its entire surface with the emissive colour instead.

    Emissive is set to match vanilla when the source left it black: of 60
    type-2 shapes sampled across Skyrim's own meshes, ALL set `own_emit` and
    carry the glow flag, 55 of 60 carry a slot-2 texture, the modal emissive
    colour is white (21) and the modal multiple is 1.0.  Leaving it black
    would keep the glow map and show nothing, because emissive modulates it.

    Returns True when the shape became a glow shape, which BLOCKS parallax:
    `skyrim_shader_type` holds ONE value, so type 2 (glow) and type 3 (height)
    cannot coexist.  Glow wins -- the glow map is authored content while our
    height map is derived from the diffuse, and a surface that was meant to
    glow and does not is far more noticeable than one that is merely flat.
    """
    if stats is None:
        return False

    rel = None
    if glow_path:
        # Named in the NIF: authored, so it is used as given.
        named = _rewrite_tex_path(glow_path)
        if _normal_exists(named, stats):
            rel = named
    if rel is None:
        _diffuse = tex_set.textures[0]
        if isinstance(_diffuse, bytes):
            _diffuse = _diffuse.decode('utf-8', errors='replace')
        if _diffuse and _diffuse != _DEFAULT_DIFFUSE_TEXTURE.decode('utf-8'):
            rel = _resolve_map_for(_diffuse, '_g', stats)
        if rel is not None and not glow_path:
            stats['glow_derived'] = stats.get('glow_derived', 0) + 1
    if rel is None:
        if glow_path:
            # The NIF named one and it is nowhere -- worth counting, but never
            # worth inventing: absence of glow is the neutral state.
            stats['glow_unresolved'] = stats.get('glow_unresolved', 0) + 1
        return False

    tex_set.textures[GLOW_SLOT] = rel.encode('utf-8')
    shader.skyrim_shader_type = SHADER_TYPE_GLOWMAP
    shader.shader_flags_2.slsf_2_glow_map = 1
    # AU: "The environment map shader is incompatible with glow mapping."
    shader.shader_flags_1.slsf_1_environment_mapping = 0
    shader.shader_flags_1.slsf_1_own_emit = 1
    _e = shader.emissive_color
    if max(_e.r, _e.g, _e.b) <= 0.0:
        # Oblivion showed this glow map without an emissive colour; Skyrim
        # multiplies by it, so black would silently discard the whole effect.
        # White with multiple 1.0 is vanilla's mode.
        _e.r = _e.g = _e.b = 1.0
        stats['glow_emissive_defaulted'] =             stats.get('glow_emissive_defaulted', 0) + 1
    if float(shader.emissive_multiple) <= 0.0:
        shader.emissive_multiple = 1.0
    stats['glow_applied'] = stats.get('glow_applied', 0) + 1
    return True


def _apply_parallax(ts, shader, tex_set, tex_apply_mode, stats):
    """Rebuild a flagged shape as a Skyrim parallax shape.

    Never runs by default: verified in game, a correctly built parallax shape
    SWIMS under vanilla SSE (and the SSE Parallax Shader Fix did not help), so
    the output needs Community Shaders or ENB.  The converter cannot detect
    that, hence the opt-in.
    """
    if (stats is None or not stats.get('_parallax')
            or tex_apply_mode != _APPLY_HILIGHT2):
        return
    # 🔴 Never on a distant-LOD tier mesh, for three independent reasons.
    #
    # It is invisible: a `_far.nif` is only ever drawn at LOD distance, where a
    # per-pixel height offset resolves to nothing.
    #
    # It does not survive: the LOD stage regenerates these from the full model
    # with `force_regen_generated=True`, and that path knows nothing about
    # parallax — it drops the vertex colors the heightmap shader needs while
    # leaving shader type 3 in place. `parallax_check.py verify` found exactly
    # that: 60 malformed shapes, every one of them in a `_far`/`_far8`/`_far16`
    # mesh, all reported as "no vertex colors (renders unlit-black)".
    #
    # And it made the output ORDER-DEPENDENT, which is the real defect: run
    # meshes then LOD and the tier meshes come out clean, run LOD then meshes
    # and they keep a half-built parallax shape. The shape count moved 1495 ->
    # 1555 purely on that ordering.
    if _is_lod_tier_mesh(stats.get('_src_path', '')):
        stats['parallax_skipped_lod_tier'] = \
            stats.get('parallax_skipped_lod_tier', 0) + 1
        return
    data = getattr(ts, 'data', None)
    if data is None:
        return                      # no geometry to render the height on
    diffuse_rel = tex_set.textures[0]
    if not diffuse_rel:
        return
    if isinstance(diffuse_rel, bytes):
        diffuse_rel = diffuse_rel.decode('utf-8', errors='replace')
    height_rel = _plan_parallax(diffuse_rel, stats)
    if height_rel is None:
        return

    from . import parallax
    tex_set.textures[parallax.HEIGHT_SLOT] = height_rel.encode('utf-8')
    shader.skyrim_shader_type = parallax.SHADER_TYPE_HEIGHTMAP
    shader.shader_flags_1.slsf_1_parallax = 1
    # Mutually exclusive with the height path — the shader has one auxiliary
    # slot and type 3 claims it.  Neither is set anywhere in this converter
    # today; clearing them keeps that true if that ever changes.
    shader.shader_flags_1.slsf_1_environment_mapping = 0
    shader.shader_flags_2.slsf_2_glow_map = 0

    # Skyrim's heightmap shader needs vertex colors present; without them the
    # shape renders unlit-black.  All-white is neutral and is what the in-game
    # test shipped.  Measured on Nehrim: 848 of the 1551 converted shapes have
    # none of their own.
    if not getattr(data, 'has_vertex_colors', False):
        data.has_vertex_colors = True
        data.vertex_colors.update_size()
        for c in data.vertex_colors:
            c.r = c.g = c.b = c.a = 1.0
        stats['parallax_vertex_colors_added'] = \
            stats.get('parallax_vertex_colors_added', 0) + 1
    shader.shader_flags_2.slsf_2_vertex_colors = 1
    stats['parallax_shapes'] = stats.get('parallax_shapes', 0) + 1


def _base_texture_path(prop):
    """The base (diffuse) texture path on a NiTexturingProperty, or empty."""
    if prop.has_base_texture and prop.base_texture.source:
        return prop.base_texture.source.file_name
    return b''


def _glow_texture_path(prop):
    """The glow path a NiTexturingProperty NAMES, or empty.

    Oblivion names its glow map (unlike the normal, which it derives), so this
    is authored data taken verbatim rather than guessed.
    """
    if not getattr(prop, 'has_glow_texture', False):
        return b''
    source = getattr(prop.glow_texture, 'source', None)
    return source.file_name if source is not None and source.file_name else b''


def _find_flip_controller(prop):
    """The NiFlipController on a property, or None.

    Oblivion fire and effect quads animate through multiple NiSourceTexture
    frames; the controller moves to the NiTriShape so BSEffectShaderProperty
    can keep the frames through conversion.
    """
    ctrl = prop.controller
    while ctrl is not None:
        if isinstance(ctrl, NifFormat.NiFlipController):
            return ctrl
        ctrl = getattr(ctrl, 'next_controller', None)
    return None


def _has_emissive_animation(prop):
    """True when a NiMaterialColorController animates the emissive channel.

    The material's static emissive is then only the curve's starting point --
    frequently (0,0,0) -- so it must not be read as "this surface does not glow".
    """
    ctrl = prop.controller
    while ctrl is not None:
        if (isinstance(ctrl, NifFormat.NiMaterialColorController) and
                int(getattr(ctrl, 'target_color', -1)) == _MATERIAL_COLOR_EMISSIVE):
            return True
        ctrl = getattr(ctrl, 'next_controller', None)
    return False


class _ShaderInputs:
    """The shader values harvested from one shape's old NIF properties.

    Defaults describe a shape with no properties at all: no textures, opaque,
    lit (vertex_lighting_mode 1), and no animation.
    """

    __slots__ = ('diffuse_path', 'glow_path', 'authored_normal', 'has_double_sided',
                 'alpha_prop', 'tex_apply_mode', 'emissive_r', 'emissive_g',
                 'emissive_b', 'material_alpha', 'emissive_animated',
                 'vertex_lighting_mode', 'flip_ctrl', 'tex_transforms')

    def __init__(self, tex_transforms):
        self.diffuse_path = b''
        self.glow_path = b''
        self.authored_normal = b''
        self.has_double_sided = False
        self.alpha_prop = None
        self.tex_apply_mode = None
        self.emissive_r = 0.0
        self.emissive_g = 0.0
        self.emissive_b = 0.0
        self.material_alpha = 1.0
        self.emissive_animated = False
        self.vertex_lighting_mode = 1
        self.flip_ctrl = None
        self.tex_transforms = tex_transforms


def _harvest_texturing(prop, out):
    """Fold one NiTexturingProperty's paths, apply mode and flip controller in."""
    out.diffuse_path = _base_texture_path(prop) or out.diffuse_path
    out.glow_path = _glow_texture_path(prop) or out.glow_path
    out.tex_apply_mode = int(prop.apply_mode)
    out.flip_ctrl = _find_flip_controller(prop) or out.flip_ctrl


def _harvest_material(prop, out):
    """Fold one NiMaterialProperty's emissive and alpha in."""
    color = prop.emissive_color
    out.emissive_r, out.emissive_g, out.emissive_b = color.r, color.g, color.b
    out.material_alpha = prop.alpha
    out.emissive_animated = _has_emissive_animation(prop)


def _collect_shader_inputs(src, uv_transforms):
    """Harvest shader inputs from a shape's Oblivion / FO3 properties.

    Oblivion keeps them on NiTexturingProperty and friends; FO3/FNV keep their
    texture paths in a BSShaderTextureSet on BSShaderPPLightingProperty. Both
    are read here.

    See: docs/commentary/asset_convert_nif.md#fo3fnv-shader-properties
    """
    out = _ShaderInputs(_collect_tex_transform_ctrls(src.properties) + uv_transforms)

    for prop in src.properties:
        if isinstance(prop, NifFormat.NiTexturingProperty):
            _harvest_texturing(prop, out)
            continue
        if isinstance(prop, NifFormat.NiMaterialProperty):
            _harvest_material(prop, out)
            continue
        if isinstance(prop, NifFormat.BSShaderPPLightingProperty):
            diffuse, normal, glow = _bs_pp_texture_slots(prop)
            out.diffuse_path = diffuse
            out.authored_normal = normal
            out.glow_path = glow or out.glow_path
            continue
        if isinstance(prop, NifFormat.NiVertexColorProperty):
            out.vertex_lighting_mode = int(prop.lighting_mode)
        elif isinstance(prop, NifFormat.NiStencilProperty):
            out.has_double_sided = True
        elif isinstance(prop, NifFormat.NiAlphaProperty):
            out.alpha_prop = prop
    return out


def _process_geometry(strips_or_shape, fix_textures, stats=None, sky_type=None):
    """Convert a NiTriStrips or NiTriShape into a ready Skyrim NiTriShape.

    Returns the NiTriShape (may be a new object if input was NiTriStrips).
    NiTriStrips with controllers are NOT converted to NiTriShape (the controller
    still references the original node by block index; converting breaks the NIF).
    """
    # NiUVController lives on the geometry controller chain and is stripped
    # below, so its UV curves must be harvested first (re-emitted as shader
    # float controllers alongside the NiTexturingProperty ones).
    uv_transforms = _collect_uv_ctrls(strips_or_shape)

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

    # Preserve the AUTHORED hidden bit.  Oblivion hides helper geometry
    # (particle emitter sources, spawn volumes, effect proxies) with bit 0 of
    # the node flags; overwriting flags wholesale with NIF_FLAGS un-hides all of
    # it, so the helper renders in game as an untextured shard.  That geometry
    # carries no UVs, so a lighting shader over it samples an absent texcoord
    # stream -- the OblivionArchGate01 "red triangle".  Bit 0 means the same
    # thing in both games, so carry it across rather than re-deriving it.
    ts.flags = NIF_FLAGS | (int(getattr(src, 'flags', 0)) & 0x0001)

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

    # Some vanilla Oblivion meshes (grass blades in particular) ship
    # NiTriShapeData with has_triangles=False — the index array is absent.
    # Skyrim's grass planter CTDs on that; rebuild the triangles.  Legacy
    # vertex match groups likewise ship on several Oblivion meshes and no
    # vanilla Skyrim mesh carries them — drop them.
    fix_missing_triangles(ts.data)
    clear_match_groups(ts.data)

    # Reset ExtraVectorsFlags to 0 (Skyrim valid: 0=none, 16=has binormal+tangent).
    # Oblivion NIFs may store value 1 (binormals-only) which is invalid in Skyrim
    # and triggers a PyFFI enum warning, potentially causing corrupt tangent data.
    # _set_tangents() will set this to 16 when proper tangent data is available.
    if hasattr(ts.data, 'extra_vectors_flags'):
        ts.data.extra_vectors_flags = 0

    # Skyrim reads ONE UV set.  On disk these share a u16 "BS Data Flags" whose
    # low 6 bits are the UV-set count (PyFFI splits it into num_uv_sets +
    # extra_vectors_flags); the count is the ONLY thing telling the engine how
    # many TexCoord arrays follow, so a file storing 2 sets while the shader
    # binds 1 overruns the vertex buffer it sized -- a non-temporal memcpy off
    # the end of the allocation (vmovntdq, CTD on cell load).  Oblivion authors
    # a second set for detail/overlay passes that Skyrim has no slot for.
    # Census: 2,233 vanilla shapes are 0 or 1 UV sets, NEVER 2.
    dropped_uv = _clamp_uv_sets(ts.data)
    if dropped_uv and stats is not None:
        stats['uv_sets_dropped'] = stats.get('uv_sets_dropped', 0) + dropped_uv

    # Inject inline tangents from NiBinaryExtraData if available
    if tangents is not None and hasattr(ts.data, 'tangents'):
        _set_tangents(ts.data, bitangents, tangents)

    _si = _collect_shader_inputs(src, uv_transforms)
    diffuse_path = _si.diffuse_path
    glow_path = _si.glow_path
    authored_normal = _si.authored_normal
    has_double_sided = _si.has_double_sided
    alpha_prop = _si.alpha_prop
    tex_apply_mode = _si.tex_apply_mode
    emissive_r = _si.emissive_r
    emissive_g = _si.emissive_g
    emissive_b = _si.emissive_b
    material_alpha = _si.material_alpha
    emissive_animated = _si.emissive_animated
    vertex_lighting_mode = _si.vertex_lighting_mode
    flip_ctrl = _si.flip_ctrl
    tex_transforms = _si.tex_transforms
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
        # The normal path is DERIVED from the diffuse, so it is a guess, not
        # authored data -- and Oblivion content frequently has no `_n` beside
        # the diffuse at all.  Measured on the shipped tree before this check
        # existed: 1904 of 20696 lighting shaders (9.2%) named a normal map
        # with no file behind it, all of them fabricated right here.
        #
        # Skyrim null-checks slot 1, so a dangling path does not crash -- it
        # simply renders with NO normal, which vanilla never does (0 of 8740
        # shapes sampled across architecture, dungeons, clutter and weapons
        # ship an empty slot 1).  Point those at the shared flat normal
        # instead; it carries the same constant specular mask the texture
        # stage bakes into maskless maps, so the shape stays consistent with
        # everything around it.
        #
        # But the stand-in is the LAST resort -- see _resolve_normal_for, which
        # first tries the variant's own `_n` and then the one its base name
        # shares across colour variants.
        _norm = base + '_n.dds'
        if authored_normal:
            _norm = (_rewrite_tex_path(authored_normal) if fix_textures
                     else authored_normal.decode('utf-8', errors='replace'))
        elif stats is not None:
            _found = _resolve_normal_for(diffuse, stats)
            if _found is None:
                _norm = _DEFAULT_NORMAL_TEXTURE
                # `spec_` prefix so it rides the bucket _finish_result already
                # merges with Counter.update() -- see the note there.
                stats['spec_normal_defaulted'] = \
                    stats.get('spec_normal_defaulted', 0) + 1
            else:
                _norm = _found
                if _found != base + '_n.dds':
                    stats['spec_normal_from_base'] = \
                        stats.get('spec_normal_from_base', 0) + 1
        tex_set.textures[1] = _norm.encode('utf-8')
    else:
        # No NiTexturingProperty at all: Oblivion renders these shapes with the
        # flat NiMaterialProperty color, so the source legitimately names no
        # texture.  Skyrim has no such mode -- BSLightingShader::SetupMaterial
        # binds the diffuse UNCONDITIONALLY (SkyrimSE.exe 1.6.659 +0x1412138 ->
        # +0x1415790 "mov rax,[rdx+0x48]" with rdx = material->diffuse), so a
        # null diffuse is an access violation the moment the shape is drawn.
        # Vanilla never exercises that path: 0 of 772 BSLightingShaderProperty
        # shapes sampled across Skyrim's own meshes ship an empty slot 0.
        #
        # white.dds is Skyrim's own neutral texture, so multiplying it by the
        # material color we already carry across reproduces Oblivion's flat
        # shading exactly.  Slot 1 stays empty on purpose -- the normal map is
        # null-checked (+0x1412144 "test rax,rax / je") and vanilla ships
        # normal-less shapes, so a fabricated _n path would only dangle.
        tex_set.textures[0] = _DEFAULT_DIFFUSE_TEXTURE
        if stats is not None:
            stats['untextured_diffuse_defaulted'] = (
                stats.get('untextured_diffuse_defaulted', 0) + 1)

    # Does slot 1 actually carry a specular mask?  See asset_convert/spec_mask
    # -- the normal map's alpha decides, not the mesh's NiSpecularProperty.
    _spec_mask = _has_spec_mask(tex_set.textures[1], stats)

    # Sky geometry takes the dedicated sky shader instead of the lighting one.
    # Skyrim's sky pass draws these before the world, unlit and unfogged, with
    # the horizon/atmosphere blend the weather record drives; routing them
    # through BSLightingShaderProperty made the stars ordinary world geometry
    # that drew over the terrain.
    if sky_type is not None:
        sky_shader = NifFormat.BSSkyShaderProperty()
        ssf1 = sky_shader.shader_flags_1
        ssf1.slsf_1_z_buffer_test = 1
        ssf2 = sky_shader.shader_flags_2
        ssf2.slsf_2_z_buffer_write = 1
        # Oblivion tints its star/cloud layers with vertex colors; vanilla
        # sky/stars.nif sets the same flag, and SSE renders geometry black when
        # the flag disagrees with the mesh data.
        if getattr(ts.data, 'has_vertex_colors', False):
            ssf2.slsf_2_vertex_colors = 1
        sky_shader.uv_offset.u = 0.0
        sky_shader.uv_offset.v = 0.0
        sky_shader.uv_scale.u = 1.0
        sky_shader.uv_scale.v = 1.0
        sky_shader.source_texture = (tex_set.textures[0] if diffuse_path else b'')
        sky_shader.sky_object_type = sky_type
        # The sky pass does its own blending; vanilla sky meshes carry NO
        # NiAlphaProperty, so the Oblivion one is dropped rather than copied
        # into bs_properties[1].
        ts.bs_properties[0] = sky_shader
        ts.bs_properties[1] = None
        if stats is not None:
            stats['sky_shaders'] = stats.get('sky_shaders', 0) + 1
        return ts

    # Build BSLightingShaderProperty
    shader = NifFormat.BSLightingShaderProperty()

    # Material values.  These were never assigned, so every shape shipped at
    # pyffi's defaults -- glossiness 0.0 with a BLACK specular colour and the
    # specular flag on, measured at 100% of 3931 shaders in our own output.
    # Vanilla's shader type 0 has glossiness 80 as both median AND mode (1333
    # of 2961 sampled shaders, and the modal value in 12 of 15 top folders);
    # specular is white in 56% and black in 3%; strength 1.0 is the mode, and
    # Arcane University puts the typical band at 0.25-1.0 -- which vanilla's
    # own 2.2 and 3.0 outliers ignore, so the mode is taken and the tail is not.
    #
    # Oblivion's glossiness is NOT carried over: its median is 10 with 59.4% of
    # shapes sitting on exactly 10, an authoring default rather than a chosen
    # value, and 10 in Skyrim is what HAIR uses -- a very wide highlight.
    shader.glossiness = _DEFAULT_GLOSSINESS
    shader.specular_color.r = 1.0
    shader.specular_color.g = 1.0
    shader.specular_color.b = 1.0
    # Uniform, deliberately: the modulation belongs in the alpha, not here.
    # See _SPEC_STRENGTH.  `_spec_mask` is still evaluated -- its per-category
    # counters are what tell the texture stage how much it had to synthesise.
    shader.specular_strength = _SPEC_STRENGTH

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
    if emissive_r > 0.0 or emissive_g > 0.0 or emissive_b > 0.0 or emissive_animated:
        sf1.slsf_1_own_emit = 1
        shader.emissive_color.r = emissive_r
        shader.emissive_color.g = emissive_g
        shader.emissive_color.b = emissive_b
        # Skyrim MULTIPLIES the emissive color by this, so a zero here leaves
        # the surface black no matter what the color animation does.  Vanilla
        # shapes carrying an emissive color controller set own_emit in 133/133
        # cases and never pair it with a 0 multiple; 1.0 is the baseline.
        shader.emissive_multiple = 1.0
    else:
        # No emissive — clear the own_emit flag (reduces overdraw on most objects)
        sf1.slsf_1_own_emit = 0

    # Carry NiMaterialProperty.alpha across.  Both engines store a per-material
    # opacity multiplier -- Oblivion on NiMaterialProperty, Skyrim as
    # BSLightingShaderProperty.alpha -- and dropping it forced every surface to
    # fully opaque.  Oblivion authors invisible helper geometry as alpha 0.0:
    # se11sheopooffx's EmitterMeshBodyGlow / EmitterMeshSwirly / GlowPlane are
    # particle EMITTER SOURCES that should never be drawn (they only define
    # where particles spawn), so at alpha 1.0 they render as solid white
    # boxes over the effect.  The NiAlphaProperty is already present and says
    # blend; without the material alpha there is simply nothing to blend by.
    shader.alpha = material_alpha

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
    # Static FX surfaces take the effect shader too.  BSLightingShaderProperty
    # is a LIT material: it shades every pixel against the normal map named in
    # texture slot 1.  Oblivion's FX textures ship no _n companion at all
    # (SEFXWHITE, SEFXLightRippleINVERT, SEForceRipple), so routing this
    # geometry through the lighting shader left it shaded against a texture
    # that does not exist -- the "major texturing problem" on se11sheopooffx
    # and se01waitingroomwalls.  BSEffectShaderProperty is the vanilla home for
    # glow/FX geometry and has no normal-map slot at all.
    #
    # The discriminator is Oblivion's OWN declaration that a surface is unlit:
    # NiVertexColorProperty.lighting_mode == LIGHTING_E (0), "emissive only --
    # ignore scene lighting".  Lit geometry uses LIGHTING_E_A_D (1).  In
    # se01waitingroomwalls the three roomRoomFX light-ripple shapes are the
    # only mode-0 surfaces in the whole mesh; all 40-odd wall/trim shapes are
    # mode 1.  That is exactly the lit/unlit split BSLightingShaderProperty vs
    # BSEffectShaderProperty encodes in Skyrim.
    #
    # Do NOT infer this from the texture path or a missing _n: a 700-mesh
    # census found 101 shapes whose diffuse has no _n companion, and they are
    # overwhelmingly ordinary LIT geometry (troll skin, clothing, painted
    # signs, plaster walls, grass) that must keep its lighting.  The material
    # fields are useless here too -- these FX shapes disagree on every one of
    # them (roomRoomFX emissive-white + blended, LightBeam emissive-black +
    # blended, Cone01 no alpha property, GlowPlane material-alpha 0).
    #
    # lighting_mode == 0 is Oblivion's EXPLICIT unlit declaration, but it is not
    # the only one: many FX meshes ship no NiVertexColorProperty at all, so the
    # mode defaults to "lit" and genuine FX geometry took the lighting shader.
    # dungeons/misc/fx/fxmistgroundeffect01 -- the Ayleid-ruin ground mist --
    # is exactly that: five additively-blended AtmosphereCloud01 planes, no
    # vertex-color property, so every one became a LIT, normal-mapped surface
    # with no soft fade.  That is the visible rectangle the user reported.
    # Across Oblivion's own FX directories 76 of 179 blended shapes declare no
    # lighting_mode at all, so the gap is the common case, not an edge case.
    #
    # ADDITIVE blending is the second authored indicator.  A surface whose
    # NiAlphaProperty sets dst=ONE ADDS its color to the framebuffer; it can
    # never be ordinary lit geometry, because lighting it would double-count the
    # light it is already contributing.  Vanilla Skyrim agrees without exception:
    # of 64 additively-blended shapes sampled across meshes/effects and
    # meshes/dungeons, 64 use BSEffectShaderProperty and 0 use the lighting
    # shader.  Plain alpha blending is deliberately NOT included -- that census
    # does show 3 legitimate BSLightingShaderProperty cases (glass, ice), so
    # widening this to all blending would misroute real lit geometry.
    is_additive_fx = (alpha_prop is not None and
                      (int(alpha_prop.flags) & _ALPHA_BLEND_ENABLED) and
                      ((int(alpha_prop.flags) >> _ALPHA_DST_SHIFT) & 0xF)
                      == _ALPHA_DST_ONE)
    is_static_fx = (flip_ctrl is None and diffuse_path and
                    (vertex_lighting_mode == _LIGHTING_EMISSIVE_ONLY or
                     is_additive_fx))
    if flip_ctrl is not None or is_static_fx:
        srcs = ([s for s in flip_ctrl.sources if s is not None and s.file_name]
                if flip_ctrl is not None else [])
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
        # Fire is self-illuminated; scale its emission to full.  1.0 is also
        # what vanilla uses on 852/1164 blended FX shapes -- it is the neutral
        # value, and anything above it is a deliberate over-brighten.
        eff_shader.emissive_multiple = 1.0
        # Carry Oblivion's AUTHORED emissive color instead of forcing white.
        # NiMaterialProperty.emissive_color is how Oblivion dims an FX surface:
        # dungeons/misc/fx/fxmist01 ships (0.47, 0.47, 0.47), i.e. the mist is
        # authored at just under HALF brightness.  Overwriting that with white
        # doubled every such effect, and on an additively-blended quad (dst=ONE)
        # the excess accumulates per overlapping layer -- which is why the
        # Ayleid-ruin mist read as blinding and opaque rather than translucent.
        # Fall back to white only when the source genuinely declares no emissive
        # (pure black), so unlit-but-untinted quads still light up.
        if emissive_r > 0.0 or emissive_g > 0.0 or emissive_b > 0.0:
            eff_shader.emissive_color.r = emissive_r
            eff_shader.emissive_color.g = emissive_g
            eff_shader.emissive_color.b = emissive_b
        else:
            eff_shader.emissive_color.r = 1.0
            eff_shader.emissive_color.g = 1.0
            eff_shader.emissive_color.b = 1.0
        # Oblivion's NiMaterialProperty.alpha is a second opacity multiplier the
        # effect path was dropping entirely (the lighting path already carries it
        # via shader.alpha).  On the effect shader it belongs in the emissive
        # alpha, which is what the engine multiplies the sampled texel by.
        eff_shader.emissive_color.a = material_alpha
        # Kill the rectangular hard edge where the quad intersects walls/floor.
        # The AUTHORED emissive, not the shader's final value -- a shape that
        # authored BLACK was defaulted to white just above, and that fallback
        # must not read as "self-lit flame" and lose the fade.
        _authored = ((emissive_r, emissive_g, emissive_b)
                     if (emissive_r > 0.0 or emissive_g > 0.0
                         or emissive_b > 0.0) else None)
        if _apply_fx_soft_effect(eff_shader, alpha_prop,
                                 _authored) and stats is not None:
            stats['fx_soft_effect'] = stats.get('fx_soft_effect', 0) + 1

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
        # Oblivion's parallax, carried across when the opt-in is on.  Only on
        # this branch: the FX/flip-book path above threw `shader` away for a
        # BSEffectShaderProperty, which has neither a height slot nor a shader
        # type to set.
        #
        # Glow FIRST, and it vetoes parallax: `skyrim_shader_type` holds one
        # value, so a shape is type 2 (glow) or type 3 (height), never both.
        # The glow map is AUTHORED; our height map is derived from the diffuse.
        if not _apply_glow(shader, tex_set, glow_path, stats):
            _apply_parallax(ts, shader, tex_set, tex_apply_mode, stats)
        elif tex_apply_mode == _APPLY_HILIGHT2 and stats is not None:
            stats['parallax_skipped_glow'] = \
                stats.get('parallax_skipped_glow', 0) + 1
        ts.bs_properties[0] = shader

    # Record the diffuse as a DETAIL OVERLAY when the source authored it that
    # way.  This is not about the NiAlphaProperty (most overlay shapes ship
    # none, e.g. RockGreatForest645): it is about the TEXTURE, whose alpha is a
    # blend weight rather than a mask.  Harmless in the full mesh -- nothing
    # samples that channel as transparency -- but object LOD does, because
    # LODGen stamps every baked shape slsf_2_lod_objects and the LOD shader
    # reads diffuse alpha as opacity.  The LOD stage flattens the alpha on a
    # LOD-local copy of exactly these textures; see
    # `lod_gen._force_opaque_lod_diffuses`.
    #
    # Keyed off the CONVERTED path (tex_set.textures[0], i.e. post-'tes4\'
    # rewrite), because that is what the shipped mesh -- and therefore the
    # baked .bto tile -- actually references.
    if tex_apply_mode == _APPLY_HILIGHT2 and stats is not None:
        _overlay_key = _norm_tex_ref(tex_set.textures[0])
        if _overlay_key:
            stats.setdefault('overlay_diffuses', set()).add(_overlay_key)

    if alpha_prop is not None:
        # Oblivion's APPLY_HILIGHT2 (4) is its PARALLAX switch: the diffuse's
        # alpha channel is a HEIGHT FIELD, not a transparency mask.  Skyrim
        # reads the same channel as plain transparency, so the SI
        # mania/dementia rocks render see-through, and where the surface is
        # low they disappear completely (seisland's body texture mrock01.dds
        # averages alpha 133 = the whole island ~50% transparent).
        #
        # These are provably not cutout masks: 97-99% of texels are PARTIALLY
        # opaque with almost no fully-transparent region (mrock01 97.9% >= 1
        # but only 22% >= 254; DMRockSideRoot01 99.0% >= 1 and 0% >= 254) --
        # mid-tone-dominant, which is exactly a height map's profile and not a
        # cutout's.  Vanilla agrees on the remedy: across 600 landscape/clutter
        # meshes, 1088/1313 shapes ship NO NiAlphaProperty at all and the
        # commonest value on the rest is 0x12EC (test, blend OFF) -- vanilla
        # rock simply does not alpha-blend.  So drop the property and let the
        # rock render solid.  This is right whether or not --parallax is on;
        # with it, the height also survives as a real slot-3 map.
        #
        # Only the parallax case is touched.  Genuine transparency (gems,
        # bottles, curtains, potion liquids) ships MODULATE/HILIGHT and keeps
        # its alpha exactly as authored.
        if tex_apply_mode == _APPLY_HILIGHT2 and (int(alpha_prop.flags) & 0x0001):
            stats['hilight2_alpha_dropped'] = \
                stats.get('hilight2_alpha_dropped', 0) + 1
        else:
            ts.bs_properties[1] = alpha_prop
            # This shape READS the diffuse's alpha -- as blend weight or as a
            # test threshold, either way as opacity.  That is evidence the
            # channel is not a height field here, whatever the texture-level
            # classifier decided, so the diffuse must keep its alpha and may
            # not be stripped to BC1 later.  Measured on the author's Nehrim
            # parallax mod: 1 shape of 39,201, but the converter runs on
            # plugins nobody has measured.
            _dif = tex_set.textures[0] if tex_set.textures else None
            if _dif and stats is not None:
                if isinstance(_dif, bytes):
                    _dif = _dif.decode('utf-8', errors='replace')
                stats.setdefault('_alpha_opacity_diffuse', set()).add(
                    _dif.replace('/', '\\').lower())

    # Re-emit the harvested UV animation onto whichever shader we settled on.
    _attach_tex_transform_ctrls(ts.bs_properties[0], tex_transforms)

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


def _drop_mttc_target(mgr, node_name: bytes) -> int:
    """Remove `node_name` from every NiMultiTargetTransformController's targets.

    The extra-target list is POSITIONAL -- the engine pairs slot N with the
    NiControllerSequence entry that drives it -- so a target whose entry has
    been removed leaves a null interpolator that
    BGSGamebryoSequenceGenerator dereferences when the object animates.
    Whenever a controlled block goes, its target must go with it.

    Returns the number of slots removed (for stats/tests).
    """
    removed = 0
    for ctrl in _iter_controllers(mgr):
        if not isinstance(ctrl, NifFormat.NiMultiTargetTransformController):
            continue
        targets = list(getattr(ctrl, 'extra_targets', None) or ())
        kept = [t for t in targets
                if t is None
                or bytes(getattr(t, 'name', b'') or b'') != node_name]
        if len(kept) == len(targets):
            continue
        removed += len(targets) - len(kept)
        ctrl.num_extra_targets = len(kept)
        ctrl.extra_targets.update_size()
        for i, t in enumerate(kept):
            ctrl.extra_targets[i] = t
    return removed


def _iter_controllers(mgr):
    """Every controller reachable from a NiControllerManager."""
    ctrl = getattr(mgr, 'next_controller', None)
    while ctrl is not None:
        yield ctrl
        ctrl = getattr(ctrl, 'next_controller', None)
    for seq in (getattr(mgr, 'controller_sequences', None) or ()):
        for cb in (getattr(seq, 'controlled_blocks', None) or ()):
            c = getattr(cb, 'controller', None)
            if c is not None:
                yield c


_NO_VALUE = -3.4028234663852886e+38   # Gamebryo's "channel has no value"


def _accum_root_mode(seq, root, resolve_name):
    """How the sequence's accum-root controlled block must be converted.

    Oblivion's exporter writes the accum root's entry as the ROOT-MOTION
    placeholder -- an IDENTITY pose (census of all 464 Oblivion non-creature
    NIFs with sequences: 853 accum-root entries, 815 data-less identity poses,
    38 with never-varying keys, 0 that move) -- and moves the node's real
    transform onto the "<accum> NonAccum" child (as a pose on doors, as key 0
    on keyed nodes: sesacellumgate01's NonAccum keys start at MetalGate's
    authored (-7,-16.2,37.9); bravilloaddoorlowerint01's NonAccum pose is
    (0,-42.7,12) with rotation keys starting at the root's 90 deg).  Both
    engines apply the identity, and NonAccum restores the world pose.  Playing
    the identity is therefore CORRECT for those ('transferred') and the entry
    must be left exactly as authored -- sentinelling its rotation, as the
    generic data-less rule below does, doubles the door's authored rotation.

    The arena spectators are exactly this: Bip01 (the actor rig's 82.5 deg Z
    rotation, 64 units up) has the identity pose and "Bip01 NonAccum" key 0 is
    (-0.34,-1.64,64.07) at 82.6 deg.  Sentinelling Bip01's rotation left the
    authored 82.5 deg in place while NonAccum re-applied its own -- the crowd
    faced 165 deg off whenever the sequence played ("rotated 90 degrees").
    Patched in the live engine (2026-08-18): with Bip01's pose left as the
    valid identity, Bip01 read back as identity, NonAccum as
    (-0.34,-1.64,~64)/82.5 deg, and the crowd sat at its authored pose.

    Every one of the 195 non-identity accum roots in Oblivion.esm is
    'transferred'.  'orphan' (nothing carries the transform, so applying the
    identity would collapse the node) is defensive, for plugins whose exporter
    did not follow the convention: every channel is sentinelled so the node
    keeps its authored transform.

    Returns 'transferred', 'orphan', or None (no accum root, or one whose
    authored transform is identity, where the pose is a no-op either way).
    """
    accum = getattr(seq, 'target_name', b'') or b''
    if isinstance(accum, str):
        accum = accum.encode('latin-1')
    accum = bytes(accum)
    if not accum:
        return None
    anode = None
    for b in root.tree():
        nm = getattr(b, 'name', None)
        if nm is not None and bytes(nm) == accum and hasattr(b, 'translation'):
            anode = b
            break
    if anode is None:
        return None
    t = anode.translation
    m = anode.rotation
    root_t = (t.x, t.y, t.z)
    _trace = m.m_11 + m.m_22 + m.m_33
    _cos = max(-1.0, min(1.0, (_trace - 1.0) / 2.0))
    rot_identity = math.degrees(math.acos(_cos)) < 0.1
    root_moved = max(abs(v) for v in root_t) >= 0.05
    if not root_moved and rot_identity:
        return None
    # The NonAccum child's ANIMATED transform in this sequence.
    na_name = accum + b' NonAccum'
    na_t = None
    na_rot_animated = False
    for cb in seq.controlled_blocks:
        nm = resolve_name(cb, seq, 'node_name')
        nm = nm.encode('latin-1') if isinstance(nm, str) else bytes(nm or b'')
        if nm != na_name:
            continue
        it = cb.interpolator
        if it is None or not hasattr(it, 'rotation'):
            continue
        d = getattr(it, 'data', None)
        if d is not None and d.translations.num_keys:
            k = d.translations.keys[0].value
            na_t = (k.x, k.y, k.z)
        elif it.translation.x > _NO_VALUE:
            na_t = (it.translation.x, it.translation.y, it.translation.z)
        if d is not None and (getattr(d, 'num_rotation_keys', 0) or
                              any(g.num_keys for g in d.xyz_rotations)):
            na_rot_animated = True
        elif it.rotation.w > _NO_VALUE:
            na_rot_animated = True
        break
    if root_moved:
        if (na_t is not None and
                max(abs(a - b) for a, b in zip(na_t, root_t)) < 1.0):
            return 'transferred'
        return 'orphan'
    # Root at the origin but rotated: transferred iff NonAccum animates a
    # rotation at all (the identity pose then only zeroes what NonAccum
    # re-applies).
    return 'transferred' if na_rot_animated else 'orphan'


def _property_ctrl_index(root):
    """id(property controller) -> [geometry names wearing that property].

    Oblivion shares one NiTexturingProperty / NiMaterialProperty block between
    several shapes, and a sequence entry names only ONE of them: palacefont01's
    'Water' entry drives NiTexturingProperty #71, which Water03, PalaceWaterL2
    and PalaceWaterR02 also wear, so in TES4 one entry scrolls all four (the
    fountain's upper tier).  Skyrim gives every converted shape its own
    BS*ShaderProperty, so the retargeted entry must be fanned out to one
    entry (and one controller) per sharing shape or the siblings stay frozen
    -- the upper tier of the Font of Madness after the first conversion.
    Built once per manager (one tree walk), looked up per entry.
    """
    index = {}
    for blk in root.tree():
        if not hasattr(blk, 'properties') or not hasattr(blk, 'data'):
            continue           # geometry only (NiGeometry / NiParticleSystem)
        nm = bytes(getattr(blk, 'name', b'') or b'')
        if not nm:
            continue
        for prop in blk.properties or ():
            if prop is None:
                continue
            c = getattr(prop, 'controller', None)
            while c is not None:
                lst = index.setdefault(id(c), [])
                if nm not in lst:
                    lst.append(nm)
                c = getattr(c, 'next_controller', None)
    return index


def _shapes_sharing_property_ctrl(index, src_ctrl, own_name):
    """Names of the OTHER geometries whose Oblivion property carries *src_ctrl*."""
    return [nm for nm in index.get(id(src_ctrl), ()) if nm != own_name]


def _fan_out_shared_entries(seq, extras):
    """Append one controlled block per (source entry, sibling name) pair.

    The controller and interpolator are cloned per sibling (each shape's own
    shader gets its own controller, as vanilla does); the key DATA is shared.
    """
    if not extras:
        return 0
    have = set()
    for cb in seq.controlled_blocks:
        have.add((bytes(cb.node_name or b''), cb.controller.__class__.__name__,
                  getattr(cb.controller, 'type_of_controlled_variable', None),
                  getattr(cb.controller, 'type_of_controlled_color', None)))
    added = 0
    for src_cb, sib in extras:
        sc = src_cb.controller
        sig = (sib, sc.__class__.__name__,
               getattr(sc, 'type_of_controlled_variable', None),
               getattr(sc, 'type_of_controlled_color', None))
        if sig in have:
            continue
        have.add(sig)
        names = list(src_cb._get_names())
        seq.num_controlled_blocks += 1
        seq.controlled_blocks.update_size()
        cb = seq.controlled_blocks[seq.num_controlled_blocks - 1]
        for m in names:
            setattr(cb, m, getattr(src_cb, m))
        cb.node_name = sib
        for off in ('node_name_offset', 'property_type_offset',
                    'controller_type_offset', 'variable_1_offset',
                    'variable_2_offset'):
            if hasattr(cb, off):
                try:
                    setattr(cb, off, -1)
                except Exception:
                    pass
        # Clone the controller (same class, same public fields + the private
        # re-stamp markers _match_seq_shader_types reads).
        c2 = sc.__class__()
        for m in sc._get_names():
            try:
                setattr(c2, m, getattr(sc, m))
            except Exception:
                pass
        for priv in ('_tt_operation', '_alpha_ctrl', '_is_color_ctrl'):
            if hasattr(sc, priv):
                setattr(c2, priv, getattr(sc, priv))
        c2.next_controller = None
        c2.target = None
        # Clone the interpolator (key data shared).
        it = src_cb.interpolator
        if it is not None:
            i2 = it.__class__()
            for m in it._get_names():
                try:
                    setattr(i2, m, getattr(it, m))
                except Exception:
                    pass
            c2.interpolator = i2
            cb.interpolator = i2
        cb.controller = c2
        added += 1
    return added


def _apply_rotation(m, quat):
    """Write quaternion (w,x,y,z) into an existing pyffi Matrix33."""
    w, x, y, z = quat
    m.m_11 = 1 - 2 * (y * y + z * z)
    m.m_12 = 2 * (x * y - z * w)
    m.m_13 = 2 * (x * z + y * w)
    m.m_21 = 2 * (x * y + z * w)
    m.m_22 = 1 - 2 * (x * x + z * z)
    m.m_23 = 2 * (y * z - x * w)
    m.m_31 = 2 * (x * z - y * w)
    m.m_32 = 2 * (y * z + x * w)
    m.m_33 = 1 - 2 * (x * x + y * y)


def _dropped_accum_root_pose(root, mgr, resolve_name):
    """Apply the accum-root entry's pose to the root NODE, since the entry dies.

    Only for an entry that names the FILE ROOT -- that is the one dropped by the
    root-name rule.  TES4 applies the entry (an identity placeholder in 815 of
    853 vanilla accum-root entries), overriding the node's authored bind; we
    cannot ship the entry, so the node has to carry the value instead.
    """
    root_name = bytes(getattr(root, 'name', b'') or b'')
    if not root_name:
        return None
    for seq in mgr.controller_sequences:
        accum = getattr(seq, 'target_name', b'') or b''
        accum = accum.encode('latin-1') if isinstance(accum, str) else bytes(accum)
        if accum != root_name:
            continue
        for cb in seq.controlled_blocks:
            nm = resolve_name(cb, seq, 'node_name')
            nm = nm.encode('latin-1') if isinstance(nm, str) else bytes(nm or b'')
            if nm != accum:
                continue
            it = cb.interpolator
            if (isinstance(it, NifFormat.NiTransformInterpolator) and
                    it.data is None and it.rotation.w > _NO_VALUE):
                q = it.rotation
                return (float(q.w), float(q.x), float(q.y), float(q.z))
            break
        break
    return None


def _door_target_geometry(target):
    """Return render-geometry vertex arrays in *target*'s local frame.

    A transform controller replaces the target node's own local transform, so
    the hinge calculation must use the geometry below that node while excluding
    the target transform itself. Descendant transforms remain authored data.
    """
    parts = []

    def _matrix(block):
        m = np.eye(4, dtype=np.float64)
        r = block.rotation
        m[0, :3] = (r.m_11, r.m_12, r.m_13)
        m[1, :3] = (r.m_21, r.m_22, r.m_23)
        m[2, :3] = (r.m_31, r.m_32, r.m_33)
        m[:3, :3] *= float(block.scale)
        m[3, :3] = (block.translation.x, block.translation.y,
                    block.translation.z)
        return m

    def _walk(block, parent, include_local=True):
        local = _matrix(block) @ parent if include_local else parent
        geom = getattr(block, 'data', None)
        if (geom is not None and hasattr(geom, 'vertices') and
                getattr(geom, 'num_vertices', 0)):
            vertices = np.asarray([(v.x, v.y, v.z) for v in geom.vertices],
                                  dtype=np.float64)
            parts.append(vertices @ local[:3, :3] + local[3, :3])
        for child in (getattr(block, 'children', None) or ()):
            if child is not None:
                _walk(child, local)

    _walk(target, np.eye(4, dtype=np.float64), include_local=False)
    return parts


def _generated_door_track(seq):
    """Return the sole generated hinge track in an Open/Close sequence.

    Morroblivion-style converted Morrowind doors carry a distinctive authored
    track: two linear Euler keys, X/Y fixed at zero, Z swinging about 90
    degrees, and no translation channel. Keeping this fingerprint narrow
    prevents ordinary Oblivion animated doors and multi-leaf gates from being
    rewritten.
    """
    found = []
    for block in seq.controlled_blocks:
        interp = block.interpolator
        data = getattr(interp, 'data', None)
        if (not isinstance(interp, NifFormat.NiTransformInterpolator) or
                data is None or int(data.rotation_type) != 4 or
                data.translations.num_keys != 0):
            continue
        groups = data.xyz_rotations
        if any(group.num_keys != 2 for group in groups):
            continue
        times = [[float(key.time) for key in group.keys] for group in groups]
        if any(abs(times[axis][key] - times[2][key]) > 1e-5
               for axis in (0, 1) for key in (0, 1)):
            continue
        if any(abs(float(key.value)) > 1e-4
               for group in groups[:2] for key in group.keys):
            continue
        z0, z1 = (float(key.value) for key in groups[2].keys)
        if not 0.75 <= abs(z1 - z0) <= 2.2:
            continue
        found.append((block, interp, data))
    return found[0] if len(found) == 1 else None


def _door_hinge_point(parts):
    """Infer the hinge line of a vertical door whose pivot is in its leaf."""
    if not parts:
        return None
    vertices = np.concatenate(parts)
    low = vertices.min(axis=0)
    high = vertices.max(axis=0)
    span = high - low
    width_axis = 0 if span[0] >= span[1] else 1
    depth_axis = 1 - width_axis
    width = float(span[width_axis])
    height = float(span[2])
    if (width <= 1e-4 or height < width * 0.65 or
            span[depth_axis] > width * 0.55):
        return None

    # A Skyrim-style door is authored with its pivot at the hinge already.
    if min(abs(float(low[width_axis])),
           abs(float(high[width_axis]))) < width * 0.30:
        return None

    midpoint = (low + high) * 0.5
    side_score = {-1: 0, 1: 0}
    for part in parts:
        part_low = part.min(axis=0)
        part_high = part.max(axis=0)
        part_span = part_high - part_low
        side = ((part_low[width_axis] + part_high[width_axis]) * 0.5 -
                midpoint[width_axis]) / width
        if (part_span[width_axis] < width * 0.30 and
                part_span[2] < height * 0.40 and abs(side) > 0.25):
            side_score[1 if side > 0 else -1] += len(part)

    if side_score[1] > side_score[-1] * 1.5:
        hinge_high = False
    elif side_score[-1] > side_score[1] * 1.5:
        hinge_high = True
    else:
        # Without a handle, the nearer extent is the authored side signal.
        hinge_high = abs(float(high[width_axis])) < abs(float(low[width_axis]))

    hinge = np.zeros(3, dtype=np.float64)
    hinge[width_axis] = high[width_axis] if hinge_high else low[width_axis]
    hinge[depth_axis] = midpoint[depth_axis]
    return hinge


def _euler_xyz_matrix(angles):
    """NiTransformData Euler XYZ angles as a column-vector matrix."""
    ax, ay, az = angles
    cx, sx = math.cos(ax), math.sin(ax)
    cy, sy = math.cos(ay), math.sin(ay)
    cz, sz = math.cos(az), math.sin(az)
    rx = np.asarray(((1, 0, 0), (0, cx, -sx), (0, sx, cx)))
    ry = np.asarray(((cy, 0, sy), (0, 1, 0), (-sy, 0, cy)))
    rz = np.asarray(((cz, -sz, 0), (sz, cz, 0), (0, 0, 1)))
    return rx @ ry @ rz


def _write_hinge_translations(interp, data, hinge, closed_angles):
    """Add T(t)=T0+Rclosed*P-R(t)*P so *hinge* stays stationary."""
    base = np.asarray((interp.translation.x, interp.translation.y,
                       interp.translation.z), dtype=np.float64)
    closed = _euler_xyz_matrix(closed_angles)
    groups = data.xyz_rotations
    data.translations.interpolation = 1
    data.translations.num_keys = 2
    data.translations.keys.update_size()
    for index in range(2):
        angles = tuple(float(group.keys[index].value) for group in groups)
        value = base + closed @ hinge - _euler_xyz_matrix(angles) @ hinge
        key = data.translations.keys[index]
        key.time = float(groups[2].keys[index].time)
        key.value.x, key.value.y, key.value.z = (float(v) for v in value)
    first = data.translations.keys[0].value
    interp.translation.x, interp.translation.y, interp.translation.z = (
        first.x, first.y, first.z)


def _fix_centered_door_pivot(root):
    """Turn generated center-spinning Open/Close doors into hinged doors."""
    sequences = {bytes(block.name or b'').lower(): block
                 for block in root.tree()
                 if isinstance(block, NifFormat.NiControllerSequence)}
    open_track = (_generated_door_track(sequences[b'open'])
                  if b'open' in sequences else None)
    close_track = (_generated_door_track(sequences[b'close'])
                   if b'close' in sequences else None)
    if open_track is None or close_track is None:
        return False
    open_block, open_interp, open_data = open_track
    close_block, close_interp, close_data = close_track
    open_name = bytes(open_block.node_name or b'')
    if not open_name or open_name != bytes(close_block.node_name or b''):
        return False

    open_angles = [tuple(float(group.keys[index].value)
                         for group in open_data.xyz_rotations)
                   for index in range(2)]
    close_angles = [tuple(float(group.keys[index].value)
                          for group in close_data.xyz_rotations)
                    for index in range(2)]
    if (max(abs(a - b) for a, b in zip(open_angles[0], close_angles[1])) > 1e-3 or
            max(abs(a - b) for a, b in zip(open_angles[1], close_angles[0])) > 1e-3):
        return False

    target = next((block for block in root.tree()
                   if bytes(getattr(block, 'name', b'') or b'') == open_name),
                  None)
    hinge = (_door_hinge_point(_door_target_geometry(target))
             if target is not None else None)
    if hinge is None:
        return False

    _write_hinge_translations(open_interp, open_data, hinge, open_angles[0])
    _write_hinge_translations(close_interp, close_data, hinge, open_angles[0])
    return True


def _process_controller_manager(node, palette):
    """Strip unsupported NiControllerManager sequences.

    Resolves node names from palette, removes blocks referencing the root,
    strips blocks with NiMaterialColorController/NiGeomMorpherController,
    and handles NiTransformInterpolator with empty data + zero translation.
    """
    mgr = node.controller
    root_name = node.name

    def _resolve_name(blk, seq, attr):
        """Controlled-block names live EITHER in the bytes field OR, when the
        sequence carries a NiStringPalette, at an offset into that palette.

        Oblivion NIFs written with a palette leave the bytes field EMPTY and
        put the name in <attr>_offset.  Reading only the bytes field therefore
        returned '' for every entry, and the "drop blocks with an empty node
        name" rule below then deleted the ENTIRE sequence -- 16 of 108 sampled
        animated meshes lost 100% of their animation this way (candles, light
        sconces, the gnarl spawner, Cameron's Paradise bricks).  Prefer the
        bytes, fall back to the palette offset.
        """
        val = getattr(blk, attr, b'')
        if isinstance(val, bytes) and val:
            return val
        if isinstance(val, int) and val and palette is not None:
            try:
                return palette.get_string(val)
            except Exception:
                pass
        return _palette_lookup(_palette_bytes(getattr(seq, 'string_palette', None)),
                               getattr(blk, attr + '_offset', None))

    prop_index = None      # id(property ctrl) -> shapes; built on first use

    # TES4 APPLIES the accum-root entry, overriding the node's authored bind --
    # but that entry names the file root, so it must be DROPPED (it crashes the
    # engine; see the census and crash dumps below).  Capture its pose now,
    # while the entry still exists, and bake it onto the NODE after the loop so
    # the playing transform matches TES4 and the NonAccum entry can stay exactly
    # as authored.  Captured before, applied after: _accum_root_mode classifies
    # from the AUTHORED bind, so baking first would make it read identity.
    _pending_bake = _dropped_accum_root_pose(node, mgr, _resolve_name)
    for seq in mgr.controller_sequences:
        accum_mode = _accum_root_mode(seq, node, _resolve_name)
        accum_name = getattr(seq, 'target_name', b'') or b''
        if isinstance(accum_name, str):
            accum_name = accum_name.encode('latin-1')
        accum_name = bytes(accum_name)
        key = 0
        shared_extras = []     # (retargeted entry, sibling shape name)
        while key < seq.num_controlled_blocks:
            blk = seq.controlled_blocks[key]
            node_name = _resolve_name(blk, seq, 'node_name')

            # Remove blocks with an empty or root node name -- AND, for the
            # root case, drop that node from every NiMultiTargetTransformController
            # extra-target list at the same time.
            #
            # Both halves are required, and vanilla satisfies both because it
            # never puts the root in the target list at all.  Census of 141
            # sequences across 43 animated vanilla meshes:
            #   * 0 controlled blocks target their own root node
            #   * 0 MTTC extra targets lack a driving controlled block
            #
            # `extra_targets` is POSITIONAL: the engine pairs slot N with the
            # entry that drives it.  Leaving the target while removing the
            # block gives that slot a null interpolator, which
            # BGSGamebryoSequenceGenerator dereferences as soon as the object
            # animates -- `movdqu xmm2,[rax]`, rax=0, in VCRUNTIME140
            # (crash-2026-08-10-00-42-35, spiddalcloudplant.nif, whose root
            # `spiddalplant` is also extra-target #1).  Keeping the block
            # instead is equally wrong: it produces a root-targeting entry
            # vanilla never ships, and it crashed in exactly the same place
            # (crash-2026-08-10-00-51-26, after that attempt shipped).
            if not node_name or node_name == root_name:
                if node_name:
                    _drop_mttc_target(mgr, node_name)
                seq.controlled_blocks.pop(key)
                seq.num_controlled_blocks -= 1
                continue

            # NiTransformInterpolator with empty data → KEEP the entry, but
            # neutralise it the way vanilla does.
            #
            # Deleting these was wrong.  The snapping concern is real (a
            # dataless interpolator whose stored transform is a real value
            # would yank the node there), but vanilla's answer is a SENTINEL,
            # not removal: `volunruudleftswordanimated`'s LeftLockDoor /
            # LeftRingParentDoor entries keep a real translation and store
            # rotation + scale as -FLT_MAX (-3.4028235e38), which tells the
            # engine "this channel has no value, leave the node alone".
            # Census of vanilla animated doors/traps: 12 dataless transform
            # entries are KEPT (96 have data), and 123/123 sequences have at
            # least one controlled block — vanilla ships NO empty sequence.
            #
            # Removal emptied whole sequences: ctrapswingmacelong01 and
            # ctrapswingmaceshort01's `Unequip` went 2 entries -> 0.  A
            # sequence with nothing to bind never runs, so its TEXT KEYS never
            # fire — which is why the swinging traps made no sound.  (Their
            # visible motion is Havok, not the clip, so the empty sequence was
            # invisible until the sound went missing.)  ctraplogs01 3->1,
            # ctrigpressureplate01 3->1, ctrigtripwire01 6->3.
            #
            # The root-named entry is still dropped above: 0 vanilla sequences
            # target their own root node.
            #
            # The sequence's ACCUM ROOT is the exception, both ways -- see
            # _accum_root_mode.  'transferred' (NonAccum carries the node's
            # transform): the exporter's identity pose is what both engines
            # play, so leave the entry exactly as authored -- sentinelling its
            # rotation would double the door's authored rotation (the arena
            # crowd's "rotated 90 degrees").  'orphan' (nothing carries the
            # transform): sentinel EVERY channel so the node keeps its
            # authored transform; a keyless NiTransformData is dropped first
            # so the sentinel applies.
            if isinstance(blk.interpolator, NifFormat.NiTransformInterpolator):
                interp = blk.interpolator
                _nn = node_name.encode('latin-1') if isinstance(node_name, str) else bytes(node_name or b'')
                is_accum_root = bool(_nn) and _nn == accum_name
                _keeps = (accum_mode == 'transferred' and bool(_nn) and
                          (is_accum_root or _nn == accum_name + b' NonAccum'))
                if _keeps:
                    pass
                else:
                    if is_accum_root and interp.data is not None:
                        _d = interp.data
                        if (getattr(_d, 'num_rotation_keys', 0) == 0 and
                                _d.translations.num_keys == 0 and
                                _d.scales.num_keys == 0 and
                                all(g.num_keys == 0 for g in _d.xyz_rotations)):
                            interp.data = None
                    if interp.data is None:
                        interp.rotation.w = _NO_VALUE
                        interp.rotation.x = _NO_VALUE
                        interp.rotation.y = _NO_VALUE
                        interp.rotation.z = _NO_VALUE
                        interp.scale = _NO_VALUE
                        if is_accum_root and accum_mode == 'orphan':
                            interp.translation.x = _NO_VALUE
                            interp.translation.y = _NO_VALUE
                            interp.translation.z = _NO_VALUE

            # Morph controllers do not exist in Skyrim (the SSE exe has no
            # NiGeomMorpherController RTTI class at all; vanilla ships 0) --
            # the entry must go.  But the morph IS the visible effect for a
            # whole family of Oblivion meshes (ctrigtripwire01's wire snap,
            # se01waitingroomwalls, the forming Oblivion gate), so harvest
            # everything _emulate_morphs needs to rebuild it as a baked
            # target shape + wrapper-node scale swap, then drop the entry.
            if isinstance(blk.controller, NifFormat.NiGeomMorpherController):
                _seen = getattr(node, '_morph_cb_seen', None)
                if _seen is None:
                    _seen = node._morph_cb_seen = {}
                _k = (id(seq), bytes(node_name))
                _ordinal = _seen.get(_k, 0)
                _seen[_k] = _ordinal + 1
                if getattr(blk.interpolator, 'data', None) is not None:
                    _swaps = getattr(node, '_morph_swaps', None)
                    if _swaps is None:
                        _swaps = node._morph_swaps = []
                    _swaps.append({
                        'seq': seq,
                        'shape': bytes(node_name),
                        'frame': bytes(_resolve_name(blk, seq, 'variable_2')
                                       or b''),
                        'ordinal': _ordinal,
                        'interp': blk.interpolator,
                        'morpher': blk.controller,
                    })
                seq.controlled_blocks.pop(key)
                seq.num_controlled_blocks -= 1
                continue

            # NiMaterialColorController animates a material color channel;
            # target_color 3 is EMISSIVE.  Skyrim's equivalent is
            # BS*ShaderPropertyColorController (present in vanilla sequences),
            # so CONVERT it -- deleting it froze the animation at its first key,
            # which for se11sheopooffx's Cone01 is emissive (0,0,0): a large
            # PITCH BLACK cone where an orange force-ripple should pulse
            # (the curve runs black -> (0.2,0.02,0) -> black over ~13s).
            # The curve lives on the sequence entry's NiPoint3Interpolator; the
            # controller block itself only holds a keyless blend interpolator.
            if isinstance(blk.controller, NifFormat.NiMaterialColorController):
                if (int(getattr(blk.controller, 'target_color', -1)) ==
                        _MATERIAL_COLOR_EMISSIVE and
                        isinstance(blk.interpolator,
                                   NifFormat.NiPoint3Interpolator)):
                    src_ctrl = blk.controller
                    new = NifFormat.BSLightingShaderPropertyColorController()
                    new.flags = 0x48 | (int(getattr(src_ctrl, 'flags', 0)) & 0x06)
                    new.frequency = getattr(src_ctrl, 'frequency', 1.0) or 1.0
                    new.phase = getattr(src_ctrl, 'phase', 0.0)
                    new.start_time = src_ctrl.start_time
                    new.stop_time = src_ctrl.stop_time
                    # Provisionally Lighting; _match_seq_shader_types re-stamps
                    # it for nodes that ended up on the Effect shader.
                    new.type_of_controlled_color = _SHADER_COLOR_EMISSIVE[0]
                    new.interpolator = blk.interpolator
                    new._is_color_ctrl = True     # for _match_seq_shader_types
                    blk.controller = new
                    blk.controller_type = b'BSLightingShaderPropertyColorController'
                    if prop_index is None:
                        prop_index = _property_ctrl_index(node)
                    for sib in _shapes_sharing_property_ctrl(prop_index, src_ctrl, node_name):
                        shared_extras.append((blk, sib))
                    key += 1
                    continue
                seq.controlled_blocks.pop(key)
                seq.num_controlled_blocks -= 1
                continue

            # NiFlipController inside a SEQUENCE.  The property-side handler
            # only sees flip controllers hanging off a geometry's
            # NiTexturingProperty; one referenced from a sequence entry never
            # reaches it, so the block -- and every NiSourceTexture frame it
            # holds -- stayed in the file.  Both types are dead in Skyrim
            # (NiSourceTexture: 0 of ~8,300 vanilla meshes), and the sequence
            # names "NiFlipController" as a type string the engine instantiates
            # by name, so the load fails outright: OblivionArchGate01 and the
            # other three gates rendered as the red missing-mesh triangle.
            #
            # DROP the entry rather than retarget it: the flip-book is already
            # fully converted geometry-side into a frame-strip atlas driven by
            # a BSEffectShaderPropertyFloatController on the shader itself
            # (verified on all 5 of this mesh's flip nodes -- each carries its
            # *_flip.dds atlas and a working controller).  The sequence entry is
            # pure duplicate, so removing it loses no animation.
            if isinstance(blk.controller, NifFormat.NiFlipController):
                seq.controlled_blocks.pop(key)
                seq.num_controlled_blocks -= 1
                continue

            # A NiControllerSequence names its controller TYPE as a string and
            # the engine instantiates it by name when the sequence loads, so an
            # Oblivion-only type here fails the WHOLE NIF -> red missing-mesh
            # triangle (se11sheopooffx, palacefont01, se01waitingroomwalls).
            # Census of ~8,300 vanilla Skyrim meshes: NiTextureTransformController
            # and NiAlphaController appear ZERO times; the controlled_block types
            # vanilla does use are BS*ShaderPropertyFloatController, NiPSys*Ctlr,
            # NiTransformController and NiVisController.
            #
            # RETARGET rather than drop: the animation CURVE lives on the
            # sequence entry's own interpolator (palacefont01's scrolling water
            # is 3 x NiFloatInterpolator, 2 keys, V 0 -> -2/-4/-1 over 2s), while
            # the controller block itself holds only a NiBlendFloatInterpolator
            # with no inline keys -- which is exactly why the harvest in
            # _collect_tex_transform_ctrls skips these and they would otherwise
            # be lost.  Point the entry at the Skyrim shader float controller
            # and keep the interpolator as-is; both engines read the curve as a
            # UV offset/scale over time.
            # The block the entry POINTS AT must be replaced too -- rewriting
            # only the type string leaves the Oblivion block in the file's
            # block-type table, which is what the engine rejects.
            if isinstance(blk.controller, NifFormat.NiTextureTransformController):
                src_ctrl = blk.controller
                op = getattr(src_ctrl, 'operation', None)
                if op in _TEX_TRANSFORM_VARS:
                    new = NifFormat.BSLightingShaderPropertyFloatController()
                    # 0x48 = Active | Compute Scaled Time (every vanilla shader
                    # float controller); keep the source CLAMP/REVERSE bits.
                    new.flags = 0x48 | (int(getattr(src_ctrl, 'flags', 0)) & 0x06)
                    new.frequency = getattr(src_ctrl, 'frequency', 1.0) or 1.0
                    new.phase = getattr(src_ctrl, 'phase', 0.0)
                    new.start_time = src_ctrl.start_time
                    new.stop_time = src_ctrl.stop_time
                    new.interpolator = blk.interpolator
                    # Provisionally the Lighting variant; _match_seq_shader_types
                    # re-stamps it as the Effect variant after the geometry walk,
                    # once each node's real shader is known.
                    new.type_of_controlled_variable = _TEX_TRANSFORM_VARS[op][0]
                    new._tt_operation = op       # remembered for that pass
                    blk.controller = new
                    blk.controller_type = b'BSLightingShaderPropertyFloatController'
                    # One entry per shape that WORE the shared source property
                    # (see _shapes_sharing_property_ctrl).
                    if prop_index is None:
                        prop_index = _property_ctrl_index(node)
                    for sib in _shapes_sharing_property_ctrl(prop_index, src_ctrl, node_name):
                        shared_extras.append((blk, sib))
                    key += 1
                    continue
                # TT_ROTATE has no Skyrim equivalent -- drop the entry.
                seq.controlled_blocks.pop(key)
                seq.num_controlled_blocks -= 1
                continue

            # NiAlphaController animates the material's opacity.  Skyrim drives
            # the same thing through the shader's Alpha float variable, so
            # CONVERT it -- dropping the entry froze the fade and left the
            # surface static (se11sheopooffx's GlowPlane pulses 0 -> 1 -> 0
            # over 13s).  Enum per references/nif 0.10.0.0.xml and confirmed by
            # vanilla counts: Lighting var 12 "Alpha", Effect var 5 "Alpha
            # Transparency".  The curve is on the sequence entry's
            # NiFloatInterpolator; the controller block holds only a keyless
            # blend interpolator.
            if isinstance(blk.controller, NifFormat.NiAlphaController):
                if isinstance(blk.interpolator, NifFormat.NiFloatInterpolator):
                    src_ctrl = blk.controller
                    new = NifFormat.BSLightingShaderPropertyFloatController()
                    new.flags = 0x48 | (int(getattr(src_ctrl, 'flags', 0)) & 0x06)
                    new.frequency = getattr(src_ctrl, 'frequency', 1.0) or 1.0
                    new.phase = getattr(src_ctrl, 'phase', 0.0)
                    new.start_time = src_ctrl.start_time
                    new.stop_time = src_ctrl.stop_time
                    new.interpolator = blk.interpolator
                    # Provisionally Lighting; re-stamped for Effect-shader nodes.
                    new.type_of_controlled_variable = _SHADER_ALPHA_VAR[0]
                    new._alpha_ctrl = True       # for _match_seq_shader_types
                    blk.controller = new
                    blk.controller_type = b'BSLightingShaderPropertyFloatController'
                    if prop_index is None:
                        prop_index = _property_ctrl_index(node)
                    for sib in _shapes_sharing_property_ctrl(prop_index, src_ctrl, node_name):
                        shared_extras.append((blk, sib))
                    key += 1
                    continue
                seq.controlled_blocks.pop(key)
                seq.num_controlled_blocks -= 1
                continue

            # Backstop: ANY controller type vanilla never puts in a sequence.
            # The engine instantiates the controlled block's type BY NAME when
            # the sequence loads, so one unknown string rejects the whole NIF
            # (the red missing-mesh triangle) -- and every handler above is
            # type-by-type, so the next Oblivion-only controller to turn up
            # would ship broken exactly the way NiFlipController did.  Dropping
            # the entry costs at most one animation channel; leaving it costs
            # the entire mesh.
            ctrl_cls = blk.controller.__class__.__name__ if blk.controller else None
            if ctrl_cls is not None and ctrl_cls not in _VANILLA_SEQ_CONTROLLERS:
                seq.controlled_blocks.pop(key)
                seq.num_controlled_blocks -= 1
                continue

            key += 1

        _fan_out_shared_entries(seq, shared_extras)

    # Some Morrowind-to-Oblivion assets synthesize Open/Close as a pure
    # 90-degree rotation while leaving the animated node at the leaf center.
    # Add the matching translation curve so Skyrim opens them about a hinge.
    _fix_centered_door_pivot(node)

    if _pending_bake is not None:
        _apply_rotation(node.rotation, _pending_bake)


def _apply_rest_visibility(root, stats=None):
    """Hide nodes a sequence keeps invisible at time 0.

    Oblivion drives per-node visibility from a NiVisController inside a
    NiControllerSequence.  Where that sequence is script-triggered, Skyrim
    leaves it unplayed until the script fires -- but the NODE still renders,
    because the engine only applies the sequence's keys while it is playing.
    So geometry Oblivion keeps hidden until mid-effect is visible from the
    moment the cell loads.

    se11sheopooffx is the case in point: its `Forward` sequence holds Cone01 at
    visibility 0 until t=0.3 and hides it again at 12.93, and nothing ever
    plays the sequence (the STAT has no script), so the cone renders
    permanently -- a large black cone over the effect.

    Applying the t=0 value as the node's authored rest state matches what the
    object looks like before its animation is triggered, which is the correct
    resting appearance in both engines.  A node visible at t=0 is untouched.
    """
    hidden = 0
    for block in root.tree():
        if not isinstance(block, NifFormat.NiControllerSequence):
            continue
        # An AutoPlay sequence RUNS from cell load, so its own keys restore the
        # node's visibility -- baking the t=0 value in would hide geometry the
        # animation is about to show.
        seq_name = bytes(getattr(block, 'name', b'') or b'')
        if seq_name in (_AUTOPLAY_SEQUENCE.encode('latin-1'),
                        _AUTOLOOP_SEQUENCE.encode('latin-1')):
            continue
        raw = _palette_bytes(getattr(block, 'string_palette', None))
        for cb in block.controlled_blocks:
            ctrl_type = bytes(getattr(cb, 'controller_type', b'') or b'')
            if not ctrl_type:
                ctrl_type = _palette_lookup(
                    raw, getattr(cb, 'controller_type_offset', None))
            if ctrl_type != b'NiVisController':
                continue
            interp = cb.interpolator
            if interp is None:
                continue
            data = getattr(interp, 'data', None)
            keys = getattr(data, 'data', None) if data is not None else None
            if keys is not None and keys.num_keys:
                first = min(keys.keys, key=lambda k: k.time)
                rest_visible = bool(first.value)
            else:
                # A NiBoolInterpolator with NO data block is a CONSTANT: its
                # bool_value IS the rest state, and there are no keys to read.
                # OblivionArchGate01 drives every one of its 30+ vis-controlled
                # nodes this way, so the keys-only path skipped all of them and
                # the meteors/tendrils rendered from cell load.
                if not isinstance(interp, NifFormat.NiBoolInterpolator):
                    continue
                rest_visible = bool(getattr(interp, 'bool_value', True))
            if rest_visible:
                continue        # visible at rest -- leave it alone
            name = bytes(getattr(cb, 'node_name', b'') or b'')
            if not name:
                name = _palette_lookup(raw, getattr(cb, 'node_name_offset', None))
            if not name:
                continue
            for node in root.tree():
                # ONLY scene graph objects have a "hidden" bit.  root.tree()
                # also yields properties, and bit 0 of NiAlphaProperty.flags is
                # ALPHA BLEND ENABLE -- setting it there turned opaque surfaces
                # into additive blends (0x1042 -> 0x1043, src=ONE dst=SRC_COLOR)
                # and they rendered as blown-out green/red.  Those blocks also
                # have an empty name, so a name-only match hits every one of
                # them at once.
                if not isinstance(node, NifFormat.NiAVObject):
                    continue
                if bytes(getattr(node, 'name', b'') or b'') != name:
                    continue
                if not int(getattr(node, 'flags', 0)) & 0x0001:
                    node.flags = int(node.flags) | 0x0001   # hidden
                    hidden += 1
    if hidden and stats is not None:
        stats['rest_hidden_nodes'] = stats.get('rest_hidden_nodes', 0) + hidden
    return hidden


def _attach_seq_shader_controllers(root, stats=None):
    """Hang each sequence's shader controller off the shader it drives.

    A NiControllerSequence entry only says "while this sequence plays, drive
    node N's controller of type T".  It does not itself connect the controller
    to the property, and Skyrim resolves T against the controllers already
    hanging off the target -- so a controller that exists ONLY as a sequence
    entry drives nothing and the surface renders frozen (palacefont01's
    fountain water, converted from Oblivion's NiTextureTransformController).

    Vanilla never leaves one dangling: across 80 meshes carrying shader float
    controllers, 481/481 are reachable from `shader.controller`.  Mirror that
    -- put the controller on the shader's chain, targeted at the shader.
    """
    shaders = {}
    for block in root.tree():
        nm = bytes(getattr(block, 'name', b'') or b'')
        if not nm:
            continue
        for pr in getattr(block, 'bs_properties', []) or []:
            if pr is None:
                continue
            if pr.__class__.__name__ in ('BSLightingShaderProperty',
                                         'BSEffectShaderProperty'):
                shaders[nm] = pr

    attached = 0
    for block in root.tree():
        if not isinstance(block, NifFormat.NiControllerSequence):
            continue
        raw = _palette_bytes(getattr(block, 'string_palette', None))
        for cb in block.controlled_blocks:
            ctrl = cb.controller
            if ctrl is None or 'ShaderProperty' not in ctrl.__class__.__name__:
                continue
            name = bytes(getattr(cb, 'node_name', b'') or b'')
            if not name:
                name = _palette_lookup(raw, getattr(cb, 'node_name_offset', None))
            shader = shaders.get(name)
            if shader is None:
                continue
            existing = shader.controller
            already = False
            probe = existing
            while probe is not None:
                if probe is ctrl:
                    already = True
                    break
                probe = probe.next_controller
            if already:
                continue
            ctrl.target = shader
            ctrl.next_controller = existing
            shader.controller = ctrl
            attached += 1
    if attached and stats is not None:
        stats['seq_shader_ctrls_attached'] = \
            stats.get('seq_shader_ctrls_attached', 0) + attached
    return attached


def _clone_sequence_as(root, seq, new_name, cycle_type):
    """Add a second NiControllerSequence named *new_name* beside *seq*.

    Vanilla's shared ambient graph (GenericBehaviors/Autoplay.hkx) needs BOTH
    'AutoPlay' (its start state) and 'AutoLoop' (the state it hands off to).
    Oblivion authors only one ambient sequence, so the second is cloned here.

    The clone REUSES the original's controlled-block interpolators rather than
    deep-copying the key data: a NiControllerSequence only references its
    interpolators, two sequences may reference the same ones (verified in the
    live engine: both sequences bind the same interpolator pointers and play),
    and the keys are by far the largest part of the block.

    Returns the new sequence, or None when the manager cannot be reached (a
    sequence with no manager is unreachable by the graph, so there is nothing
    to register).
    """
    mgr = getattr(seq, 'manager', None)
    if mgr is None:
        return None
    clone = NifFormat.NiControllerSequence()
    clone.name = new_name.encode('latin-1')
    clone.cycle_type = cycle_type
    clone.frequency = seq.frequency
    clone.start_time = seq.start_time
    clone.stop_time = seq.stop_time
    clone.manager = mgr
    clone.text_keys = seq.text_keys
    clone.string_palette = seq.string_palette
    if hasattr(seq, 'target_name'):
        clone.target_name = seq.target_name      # accum root name
    clone.num_controlled_blocks = seq.num_controlled_blocks
    clone.array_grow_by = getattr(seq, 'array_grow_by', 0)
    clone.controlled_blocks.update_size()
    # Copy every declared member: ControllerLink's field set differs across NIF
    # versions (20.0.0.4 has variable_1/variable_2 where later ones have
    # controller_id/interpolator_id), so naming them explicitly breaks on the
    # next version.  _get_names() is pyffi's own declaration order.
    names = list(seq.controlled_blocks[0]._get_names()) if seq.num_controlled_blocks else []
    for dst, src in zip(clone.controlled_blocks, seq.controlled_blocks):
        for member in names:
            setattr(dst, member, getattr(src, member))
    mgr.num_controller_sequences += 1
    mgr.controller_sequences.update_size()
    mgr.controller_sequences[mgr.num_controller_sequences - 1] = clone
    return clone


def _autoplay_ambient_sequences(root, stats=None):
    """Turn Oblivion's self-playing "Idle" sequence into vanilla's AutoPlay pair.

    Oblivion starts a sequence called Idle on load; Skyrim starts nothing by
    itself.  Vanilla's self-playing meshes (63 in Skyrim.esm's BSAs) all point
    their BGED at GenericBehaviors/Autoplay.hkx, whose state machine STARTS on
    a state playing sequence 'AutoPlay' (a CLAMP intro; 53/54 vanilla) and, on
    that sequence's End event, hands off to a state playing 'AutoLoop' (the
    real motion, cycle type LOOP; 39/53 vanilla).  Looping is the SEQUENCE's
    own cycle type -- BGSGamebryoSequenceGenerator has no looping field
    (bLooping is SERIALIZE_IGNORED) and the AutoLoop state has no
    self-transition.

    So: the authored Idle becomes AutoLoop and KEEPS its authored cycle type
    (all 116 Oblivion 'Idle' sequences are CYCLE_LOOP = 0), and a CLAMP clone
    named AutoPlay is added for the start state.  Read out of the running
    engine (2026-08-18, arena spectator, `tools/live/game_bridge.py`): with AutoLoop
    written as CLAMP the graph reached AutoLoopState and froze on the last
    frame; flipping the loaded sequence's cycleType to LOOP in memory and
    `sae AutoReset` made it loop indefinitely.

    Script-driven names (Forward, SpecialIdle, ...) are left alone -- those are
    started through the behaviour graph BY NAME and renaming them would break
    the PlayAnimation() call that drives them.
    """
    renamed = 0
    for block in root.tree():
        if not isinstance(block, NifFormat.NiControllerSequence):
            continue
        raw = getattr(block, 'name', b'') or b''
        name = raw.decode('latin-1') if isinstance(raw, bytes) else str(raw)
        if name.lower() not in _AMBIENT_SEQUENCES:
            continue
        block.name = _AUTOLOOP_SEQUENCE.encode('latin-1')
        _clone_sequence_as(root, block, _AUTOPLAY_SEQUENCE, _CYCLE_CLAMP)
        renamed += 1
    if renamed and stats is not None:
        stats['autoplay_sequences'] = stats.get('autoplay_sequences', 0) + renamed
    return renamed


def _palette_bytes(string_palette):
    """Raw NUL-separated blob out of a NiStringPalette ref, or b''.

    PyFFI nests it: NiStringPalette.palette is a StringPalette struct whose own
    `palette` member is the byte string.
    """
    if string_palette is None:
        return b''
    pal = getattr(string_palette, 'palette', string_palette)
    raw = getattr(pal, 'palette', pal)
    if isinstance(raw, bytes):
        return raw
    try:
        return bytes(raw)
    except Exception:
        return b''


def _palette_lookup(raw, offset):
    """Read the NUL-terminated string at `offset` in a palette blob."""
    if not raw or offset is None or offset == 0xFFFFFFFF or offset >= len(raw):
        return b''
    end = raw.find(b'\x00', offset)
    return raw[offset:end] if end >= 0 else raw[offset:]


def _bind_shader_ctrl_target(ctrl, shader):
    """Point a BS*ShaderProperty*Controller at the shader property it drives.

    NiTimeController.Target is a NON-optional back-pointer for this family:
    every one of the vanilla BS*ShaderProperty{Color,Float}Controllers sampled
    names its own shader block (Lighting controllers -> BSLightingShaderProperty,
    Effect -> BSEffectShaderProperty; 15/15, 0 nulls).  The Oblivion source
    controllers (NiMaterialColorController / NiAlphaController /
    NiTextureTransformController) target the NiTriShape's *property list*, which
    has no Skyrim counterpart, so the rebuilt controller was left with a NULL
    target -- the engine dereferences it while loading the shader property and
    faults.
    """
    if ctrl is None or shader is None:
        return
    cn = ctrl.__class__.__name__
    if 'ShaderProperty' not in cn or 'Controller' not in cn:
        return
    # Lighting controllers must not be bound to an Effect shader (or vice
    # versa) -- the controlled-variable enums differ between the two.
    want = ('BSEffectShaderProperty' if cn.startswith('BSEffectShaderProperty')
            else 'BSLightingShaderProperty')
    if shader.__class__.__name__ != want:
        return
    ctrl.target = shader


def _match_seq_shader_types(root):
    """Make each retargeted UV controller match its target node's shader.

    Must run AFTER the geometry walk: _process_controller_manager rewrites the
    Oblivion NiTextureTransformController entries before the shaders exist, so
    it can only assume the Lighting variant.  Unlit surfaces (lighting_mode 0,
    e.g. palacefont01's fountain water) end up on BSEffectShaderProperty, and
    the two shaders number their controlled variables differently -- V Offset
    is 22 on Lighting but 8 on Effect -- so an unreconciled entry leaves the
    engine unable to bind the animation.
    """
    shader_of = {}
    shader_block_of = {}
    for blk in root.tree():
        nm = getattr(blk, 'name', None)
        if not nm:
            continue
        for pr in getattr(blk, 'bs_properties', []) or []:
            if pr is None:
                continue
            cn = pr.__class__.__name__
            if cn in ('BSEffectShaderProperty', 'BSLightingShaderProperty'):
                shader_of[bytes(nm)] = cn
                shader_block_of[bytes(nm)] = pr

    def _copy_timing(dst, src):
        dst.flags = src.flags
        dst.frequency = src.frequency
        dst.phase = src.phase
        dst.start_time = src.start_time
        dst.stop_time = src.stop_time
        dst.interpolator = src.interpolator

    for blk in root.tree():
        if not isinstance(blk, NifFormat.NiControllerSequence):
            continue
        # The node name may live in the bytes field or in the sequence's string
        # palette (see _resolve_name); a shape whose name only exists in the
        # palette must still be matched, or its controller keeps the wrong type.
        raw = _palette_bytes(getattr(blk, 'string_palette', None))

        for cb in blk.controlled_blocks:
            ctrl = cb.controller
            if ctrl is None:
                continue
            name = bytes(getattr(cb, 'node_name', b'') or b'')
            if not name:
                name = _palette_lookup(raw, getattr(cb, 'node_name_offset', None))
            if shader_of.get(name) != 'BSEffectShaderProperty':
                # Stays on the Lighting shader; still needs its target bound
                # (see _bind_shader_ctrl_targets for why NULL is fatal).
                _bind_shader_ctrl_target(ctrl, shader_block_of.get(name))
                continue

            op = getattr(ctrl, '_tt_operation', None)
            if op is not None:
                eff = NifFormat.BSEffectShaderPropertyFloatController()
                _copy_timing(eff, ctrl)
                eff.type_of_controlled_variable = _TEX_TRANSFORM_VARS[op][1]
                cb.controller = eff
                cb.controller_type = b'BSEffectShaderPropertyFloatController'
            elif getattr(ctrl, '_alpha_ctrl', False):
                eff = NifFormat.BSEffectShaderPropertyFloatController()
                _copy_timing(eff, ctrl)
                eff.type_of_controlled_variable = _SHADER_ALPHA_VAR[1]
                cb.controller = eff
                cb.controller_type = b'BSEffectShaderPropertyFloatController'
            elif getattr(ctrl, '_is_color_ctrl', False):
                eff = NifFormat.BSEffectShaderPropertyColorController()
                _copy_timing(eff, ctrl)
                eff.type_of_controlled_color = _SHADER_COLOR_EMISSIVE[1]
                cb.controller = eff
                cb.controller_type = b'BSEffectShaderPropertyColorController'
            else:
                _bind_shader_ctrl_target(ctrl, shader_block_of.get(name))
                continue
            _bind_shader_ctrl_target(cb.controller, shader_block_of.get(name))

    _normalize_shader_cb_strings(root)
    _retarget_geometry_suffix_entries(root)


def _normalize_shader_cb_strings(root):
    """Stamp vanilla controlled-block ID strings on converted shader controllers.

    The engine resolves a sequence's controlled block at activation time BY
    STRING: on node <node_name> find the property whose class is
    <property_type>, then its controller of class <controller_type>, using
    <variable_1> (Controller ID) to pick the channel.  The rewrites above swap
    the controller block and controller_type but the entry keeps Oblivion's
    strings — property_type 'NiTexturingProperty' (a class that no longer
    exists in the file) and controller IDs like '0-0-TT_TRANSLATE_V'.  The
    lookup fails silently and the interpolator is never applied: palacefont01's
    fountain water shipped with a correct V-Offset curve that never played.

    Vanilla convention (beehive01, blackpool, dweastrolabehub01 — every
    BS*ShaderProperty*Controller entry sampled):
        property_type = the shader property's class name
        variable_1    = str(type_of_controlled_variable/color), e.g. '8', '11'
        variable_2    = ''
    """
    for blk in root.tree():
        if not isinstance(blk, NifFormat.NiControllerSequence):
            continue
        for cb in blk.controlled_blocks:
            ctrl = cb.controller
            if ctrl is None:
                continue
            cn = ctrl.__class__.__name__
            if not (cn.startswith(('BSEffectShaderProperty',
                                   'BSLightingShaderProperty'))
                    and cn.endswith('Controller')):
                continue
            cb.property_type = (
                b'BSEffectShaderProperty' if cn.startswith('BSEffectShaderProperty')
                else b'BSLightingShaderProperty')
            cb.controller_type = cn.encode('ascii')
            var = getattr(ctrl, 'type_of_controlled_variable',
                          getattr(ctrl, 'type_of_controlled_color', None))
            cb.variable_1 = (b'' if var is None else str(int(var)).encode('ascii'))
            cb.variable_2 = b''


def _all_attr_names(cls):
    """Attribute names of a PyFFI struct class INCLUDING inherited ones.

    `_attrs` is per-class only — NiTriStripsData._attrs holds just num_strips/
    strip_lengths/points, while the vertices live on NiTriBasedGeomData.
    Walk the MRO base-first so counts still precede their arrays.
    """
    names = []
    for klass in reversed(cls.__mro__):
        for a in klass.__dict__.get('_attrs', ()):
            if a.name not in names:
                names.append(a.name)
    return names


def _copy_block_fields(src, dst):
    """Field-by-field copy of a PyFFI block (scalars, compounds, arrays).

    Counts are declared before their arrays, so a single in-order pass with
    update_size() at each array keeps dimensions valid.  Reference-typed
    fields are copied as POINTERS (shared blocks); the caller overrides the
    ones the clone must own (data, controller, collision).
    """
    from pyffi.object_models.xml.array import Array as _Arr

    def _copy_value(sv, dv, setter):
        if isinstance(dv, _Arr):
            if hasattr(dv, 'update_size'):
                try:
                    dv.update_size()
                except Exception:
                    pass
            for i in range(min(len(sv), len(dv))):
                _copy_value(sv[i], dv[i],
                            lambda v, _dv=dv, _i=i: _dv.__setitem__(_i, v))
        elif hasattr(dv, '_attrs'):                          # compound
            for name in _all_attr_names(type(dv)):
                try:
                    _copy_value(getattr(sv, name), getattr(dv, name),
                                lambda v, _dv=dv, _n=name: setattr(_dv, _n, v))
                except Exception:
                    pass
        else:
            try:
                setter(sv)
            except Exception:
                pass

    for name in _all_attr_names(type(dst)):
        try:
            sv = getattr(src, name)
            dv = getattr(dst, name)
        except Exception:
            continue
        if isinstance(dv, _Arr) or hasattr(dv, '_attrs'):
            _copy_value(sv, dv, lambda v, _n=name: setattr(dst, _n, v))
        else:
            try:
                setattr(dst, name, sv)
            except Exception:
                pass


def _morph_weight_curve(interp):
    """(time, value) list of a morph target's weight keys, or []."""
    data = getattr(interp, 'data', None)
    kg = getattr(data, 'data', None)
    keys = getattr(kg, 'keys', None)
    if not keys:
        return []
    return [(k.time, k.value) for k in keys]


def _vis_toggle_times(curve):
    """Times at which a weight curve crosses 0.5, plus the initial state.

    Returns (initially_on, [t0, t1, ...]) — each t toggles the state.
    """
    if not curve:
        return False, []
    on = curve[0][1] >= 0.5
    times = []
    state = on
    for (t0, v0), (t1, v1) in zip(curve, curve[1:]):
        nxt = v1 >= 0.5
        if nxt != state:
            # linear crossing inside the segment
            if v1 != v0:
                tc = t0 + (0.5 - v0) * (t1 - t0) / (v1 - v0)
            else:
                tc = t1
            times.append(max(t0, min(tc, t1)))
            state = nxt
    return on, times


# NiBlendInterpolator.Flags bit 0 is "Manager Controlled".  nif.xml makes the
# next SEVEN fields (Interp Count, Single Index, High Priority, Next High
# Priority, Single Time, High Weights Sum, Next High Weights Sum) conditional on
# that bit being CLEAR, so a manager-driven interpolator is 7 bytes and a
# free-standing one is 15.  Vanilla is unanimous that these are manager-driven:
# 8779/8779 NiBlend*Interpolator blocks across Skyrim's meshes store Flags=1 and
# Array Size=2 (2688 bool, 5520 float, 571 point3).
#
# PyFFI 2.2.3 models this block WRONG -- it declares 'unknown_short' +
# 'unknown_int' + 'bool_value' where the real layout is byte Flags, byte Array
# Size, float Weight Threshold, byte Value.  These are NOT padding (the usual
# reason to leave unknown_* alone); they are real named fields PyFFI failed to
# describe, so they must be written explicitly:
#     unknown_short = 0x0201  -> Flags=0x01 (low byte), Array Size=0x02 (high)
#     unknown_int   = 0.0f    -> Weight Threshold
# Leaving them 0 emits Flags=0, which tells the engine to read 15 bytes out of a
# 7-byte block: it runs into the next block and AddRefs whatever it finds.  That
# is the `lock inc [rax+0x08]` CTD on entering Vilverin, where rax's high half
# was 0xBF800000 -- the -1.0f default of the Single Time field it never had.
_BLEND_INTERP_FLAGS_ARRAYSIZE = 0x0201


def _init_blend_interpolator(blend):
    """Give a synthesized NiBlend*Interpolator vanilla's manager-driven header."""
    blend.unknown_short = _BLEND_INTERP_FLAGS_ARRAYSIZE
    blend.unknown_int = 0          # Weight Threshold 0.0f
    return blend


_BLEND_INTERP_TYPES = tuple(
    t for t in (getattr(NifFormat, n, None) for n in (
        'NiBlendBoolInterpolator', 'NiBlendFloatInterpolator',
        'NiBlendPoint3Interpolator', 'NiBlendTransformInterpolator',
        'NiBlendColorInterpolator')) if t is not None)


def _normalize_blend_interpolators(root, stats=None):
    """Force every NiBlend*Interpolator to vanilla's manager-driven header.

    Blocks COPIED from an Oblivion source hit the same trap as synthesized ones:
    PyFFI reads them under the old version's layout and rewrites them under
    Skyrim's, and because its schema calls these fields 'unknown_short' /
    'unknown_int' rather than Flags / Array Size / Weight Threshold, the flags do
    not survive the round trip.  The result is Flags=0, which promises the engine
    seven trailing fields that the 7-byte block does not contain -- see the
    _BLEND_INTERP_FLAGS_ARRAYSIZE comment for the full mechanism and the crash
    it caused.

    Every one of these interpolators is owned by a NiControllerManager sequence
    in both games, so manager-controlled is not merely the vanilla-common case,
    it is the correct description of the object.  Vanilla agrees 8779/8779.
    """
    fixed = 0
    if not _BLEND_INTERP_TYPES:
        return fixed
    for blk in root.tree():
        if not isinstance(blk, _BLEND_INTERP_TYPES):
            continue
        if getattr(blk, 'unknown_short', None) != _BLEND_INTERP_FLAGS_ARRAYSIZE:
            blk.unknown_short = _BLEND_INTERP_FLAGS_ARRAYSIZE
            blk.unknown_int = 0
            fixed += 1
    if fixed and stats is not None:
        stats['blend_interp_fixed'] = stats.get('blend_interp_fixed', 0) + fixed
    return fixed


def _emulate_morphs(root, stats=None):
    """Rebuild dropped NiGeomMorpherController animation as shape swaps.

    Skyrim's engine has no morph-controller class (RTTI absent from the SSE
    exe, 0 vanilla uses), so a converted sequence cannot morph vertices.  What
    it CAN do is toggle visibility: for every animated morph target harvested
    by _process_controller_manager, bake the target's vertex positions into a
    hidden sibling copy of the shape and add NiVisController entries to the
    sequence that swap base -> target when the weight curve crosses 0.5.
    The wire of ctrigtripwire01 visibly snaps again; the smooth crossfade of
    slow morphs degrades to a cut, which is the closest the engine gets.

    Vanilla structure copied exactly from sldjailwallcollapse01: node carries
    NiVisController (flags 108, NiBlendBoolInterpolator bool_value=2), the
    sequence entry carries NiBoolInterpolator + NiBoolData step keys,
    controller_type 'NiVisController', empty property/variable strings.

    The keys MUST be CONST_KEY (5), never LINEAR (1) -- see _add_vis_cb.
    """
    swaps = []
    for blk in root.tree():
        got = getattr(blk, '_morph_swaps', None)
        if got:
            swaps.extend(got)
    if not swaps:
        return

    geoms = {}
    parents = {}
    for blk in root.tree():
        if isinstance(blk, NifFormat.NiNode):
            for ch in blk.children:
                if isinstance(ch, NifFormat.NiTriBasedGeom):
                    geoms[bytes(ch.name)] = ch
                    parents[id(ch)] = blk

    mgr = root.controller
    pal = (mgr.object_palette
           if isinstance(mgr, NifFormat.NiControllerManager) else None)

    def _palette_add(obj):
        if pal is None:
            return
        pal.num_objs += 1
        pal.objs.update_size()
        entry = pal.objs[pal.num_objs - 1]
        entry.name = bytes(obj.name)
        entry.av_object = obj

    def _vis_controller(geom):
        ctrl = geom.controller
        while ctrl is not None:
            if isinstance(ctrl, NifFormat.NiVisController):
                return ctrl
            ctrl = ctrl.next_controller
        ctrl = NifFormat.NiVisController()
        ctrl.flags = 108           # ACTIVE | CLAMP | Compute Scaled Time
        ctrl.frequency = 1.0
        ctrl.phase = 0.0
        ctrl.start_time = 0.0
        ctrl.stop_time = 0.0
        blend = NifFormat.NiBlendBoolInterpolator()
        _init_blend_interpolator(blend)
        blend.bool_value = 2       # vanilla's "no authored value" sentinel
        ctrl.interpolator = blend
        ctrl.target = geom
        ctrl.next_controller = geom.controller
        geom.controller = ctrl
        return ctrl

    def _add_vis_cb(seq, geom, initially_on, toggles):
        bd = NifFormat.NiBoolData()
        kg = bd.data
        times = [0.0] + [t for t in toggles if t > 0.0]
        values = []
        state = initially_on
        # value at each emitted key; the 0.0 key carries the initial state
        values.append(1 if state else 0)
        for _ in times[1:]:
            state = not state
            values.append(1 if state else 0)
        # CONST_KEY (step).  nif.xml calls type 5 "Step function.  Used for
        # visibility keys in NiBoolData", and the census agrees absolutely:
        # 3449/3449 vanilla Skyrim NiBoolData and 1296/1296 Oblivion source
        # NiBoolData store 5.  LINEAR (1) appears in neither game, and writing
        # it crashed the engine inside NiBoolData::Load while streaming
        # ctrigtripwire01 (Vilverin).  A bool has no meaningful interpolant,
        # so step is also the only type that expresses these keys correctly.
        kg.interpolation = 5
        kg.num_keys = len(times)
        kg.keys.update_size()
        for i, (t, v) in enumerate(zip(times, values)):
            kg.keys[i].time = t
            kg.keys[i].value = v
        ip = NifFormat.NiBoolInterpolator()
        ip.bool_value = bool(values[0])
        ip.data = bd
        ctrl = _vis_controller(geom)
        ctrl.stop_time = max(ctrl.stop_time, seq.stop_time)
        seq.num_controlled_blocks += 1
        seq.controlled_blocks.update_size()
        cb = seq.controlled_blocks[seq.num_controlled_blocks - 1]
        cb.interpolator = ip
        cb.controller = ctrl
        if hasattr(cb, 'priority'):
            cb.priority = 0
        cb.node_name = bytes(geom.name)
        cb.property_type = b''
        cb.controller_type = b'NiVisController'
        cb.variable_1 = b''
        cb.variable_2 = b''
        # Palette offsets were resolved into the string fields long before
        # this point; make sure stale-looking offsets can never re-resolve.
        for off in ('node_name_offset', 'property_type_offset',
                    'controller_type_offset', 'variable_1_offset',
                    'variable_2_offset'):
            if hasattr(cb, off):
                try:
                    setattr(cb, off, -1)
                except Exception:
                    pass

    made = {}          # (shape_name, morph_index) -> clone block
    base_toggles = {}  # (id(seq), shape_name) -> list of clone curves

    for entry in swaps:
        name = entry['shape']
        geom = geoms.get(name)
        if geom is None:
            continue
        morpher = entry['morpher']
        md = getattr(morpher, 'data', None)
        morphs = getattr(md, 'morphs', None)
        if md is None or morphs is None or len(morphs) == 0:
            continue
        idx = entry['ordinal']
        # Prefer a frame-name match when the format stores one.
        frame = entry['frame']
        if frame:
            for i in range(len(morphs)):
                fn = getattr(morphs[i], 'frame_name', None)
                if fn is not None and bytes(fn) == frame:
                    idx = i
                    break
        if idx <= 0 or idx >= len(morphs):
            continue               # base target: visibility derived below
        gdata = geom.data
        nverts = getattr(gdata, 'num_vertices', 0)
        vectors = morphs[idx].vectors
        if len(vectors) != nverts or nverts == 0:
            continue

        curve = _morph_weight_curve(entry['interp'])
        on, toggles = _vis_toggle_times(curve)
        if not on and not toggles:
            continue               # never reaches 0.5: no visible effect

        key = (name, idx)
        clone = made.get(key)
        if clone is None:
            clone = geom.__class__()
            _copy_block_fields(geom, clone)
            cdata = gdata.__class__()
            _copy_block_fields(gdata, cdata)
            relative = bool(getattr(md, 'relative_targets', 1))
            for i in range(nverts):
                v = cdata.vertices[i]
                d = vectors[i]
                if relative:
                    v.x += d.x
                    v.y += d.y
                    v.z += d.z
                else:
                    v.x, v.y, v.z = d.x, d.y, d.z
            try:
                cdata.update_center_radius()
            except Exception:
                pass
            clone.data = cdata
            suffix = frame if frame else str(idx).encode('ascii')
            clone.name = bytes(name) + b'Mrph' + suffix
            clone.controller = None
            clone.collision_object = None
            clone.flags = int(geom.flags) | 0x01     # hidden at rest
            parent = parents.get(id(geom))
            if parent is not None:
                parent.add_child(clone)
            _palette_add(clone)
            made[key] = clone

        seq = entry['seq']
        _add_vis_cb(seq, clone, on, toggles)
        base_toggles.setdefault((id(seq), name), []).append(curve)

    # Base shape: visible exactly while NO target weight is >= 0.5.
    seq_by_id = {id(e['seq']): e['seq'] for e in swaps}
    for (sid, name), curves in base_toggles.items():
        geom = geoms.get(name)
        seq = seq_by_id.get(sid)
        if geom is None or seq is None:
            continue
        cut = sorted({t for c in curves for t in _vis_toggle_times(c)[1]})
        # evaluate combined state per interval
        def _any_on(t):
            for c in curves:
                v = None
                for (t0, v0), (t1, v1) in zip(c, c[1:]):
                    if t0 <= t <= t1:
                        v = v0 + (v1 - v0) * ((t - t0) / (t1 - t0)
                                              if t1 > t0 else 0.0)
                        break
                if v is None and c:
                    v = c[0][1] if t <= c[0][0] else c[-1][1]
                if v is not None and v >= 0.5:
                    return True
            return False
        probes = [0.0] + cut
        states = []
        for i, t in enumerate(probes):
            nxt = cut[i] if i < len(cut) else (t + 0.001)
            mid = (t + nxt) / 2 if nxt > t else t
            states.append(not _any_on(mid))
        toggles = [cut[i] for i in range(len(cut))
                   if states[i + 1] != states[i]]
        _add_vis_cb(seq, geom, states[0], toggles)
    if stats is not None:
        stats['morph_swaps'] = stats.get('morph_swaps', 0) + len(made)


def _retarget_geometry_suffix_entries(root):
    """Bind sequence entries that name geometry as "<node>:<index>".

    Oblivion's exporter names a node's geometry children either as
    "Tri <parent> <index>" (a real block name) or, inside a NiControllerSequence
    string palette, as "<parent>:<index>".  morroblivionchandilier01's Idle
    sequence uses BOTH conventions at once:

        node='CandleSkinny01:0'          NiMaterialColorController   (emissive)
        node='CandleSkinny01'            NiTransformController
        node='CandleSkinny01 NonAccum'   NiTransformController

    The last two name real NiNodes, so the palette is NOT stale -- only the
    ":0" form needs translating.  It means "geometry child 0 of CandleSkinny01",
    which after conversion is the shape carrying the BSLightingShaderProperty.

    Binding matters because Skyrim dereferences NiTimeController.Target while
    loading the shader property (vanilla: 0 NULL targets), so an unbound
    shader controller is a CTD.  Deleting the entry is NOT an acceptable fix:
    it costs the chandelier its emissive flicker, and emptying a sequence
    strands its NiControllerManager with zero sequences -- which the engine
    also dereferences (vanilla ships no manager with 0 sequences).
    """
    for blk in root.tree():
        if not isinstance(blk, NifFormat.NiControllerSequence):
            continue
        raw = _palette_bytes(getattr(blk, 'string_palette', None))
        for cb in blk.controlled_blocks:
            ctrl = cb.controller
            cn = ctrl.__class__.__name__ if ctrl is not None else ''
            if 'ShaderProperty' not in cn or 'Controller' not in cn:
                continue
            if getattr(ctrl, 'target', None) is not None:
                continue
            name = bytes(getattr(cb, 'node_name', b'') or b'')
            if not name:
                name = _palette_lookup(raw, getattr(cb, 'node_name_offset', None))
            shader = _resolve_geometry_suffix(root, name)
            if shader is None:
                continue
            _bind_shader_ctrl_target(ctrl, shader)
            # The entry must name a block that exists in the output, or the
            # engine cannot re-bind it at run time.
            owner = getattr(shader, '_owner_name', None)
            if owner:
                cb.node_name = owner


def _resolve_geometry_suffix(root, name):
    """Map a "<node>:<index>" palette name onto that node's Nth shader."""
    if not name or b':' not in name:
        return None
    parent, _, idx = name.rpartition(b':')
    try:
        want = int(idx)
    except ValueError:
        return None

    # Collect the geometry under `parent`, in tree order, and take the Nth.
    target = None
    for blk in root.tree():
        if bytes(getattr(blk, 'name', b'') or b'') == parent:
            target = blk
            break
    if target is None:
        return None

    geoms = []
    for blk in target.tree():
        if not isinstance(blk, NifFormat.NiTriBasedGeom):
            continue
        for pr in getattr(blk, 'bs_properties', []) or []:
            if pr is None:
                continue
            if pr.__class__.__name__ in ('BSLightingShaderProperty',
                                         'BSEffectShaderProperty'):
                geoms.append((blk, pr))
                break
    if want >= len(geoms):
        return None
    node, shader = geoms[want]
    # Remember the real block name so the sequence entry can point at it.
    shader._owner_name = bytes(getattr(node, 'name', b'') or b'')
    return shader



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


def _sample_color_keys(keys, t):
    """Linearly sample a NiColorData key list at normalised time `t`."""
    if not keys:
        return (1.0, 1.0, 1.0, 1.0)
    pts = sorted(((float(k.time), k.value) for k in keys),
                 key=lambda kv: kv[0])
    t0, t1 = pts[0][0], pts[-1][0]
    span = (t1 - t0) or 1.0
    want = t0 + t * span
    prev = pts[0]
    for cur in pts:
        if cur[0] >= want:
            if cur[0] == prev[0]:
                c = cur[1]
                return (c.r, c.g, c.b, c.a)
            f = (want - prev[0]) / (cur[0] - prev[0])
            a, b = prev[1], cur[1]
            return (a.r + (b.r - a.r) * f, a.g + (b.g - a.g) * f,
                    a.b + (b.b - a.b) * f, a.a + (b.a - a.a) * f)
        prev = cur
    c = pts[-1][1]
    return (c.r, c.g, c.b, c.a)


def _simple_color_from(mod):
    """BSPSysSimpleColorModifier carrying the AUTHORED colour gradient.

    Skyrim's modifier holds exactly three colours (plus the percentages at
    which each is reached), while Oblivion's NiPSysColorModifier points at a
    NiColorData curve of arbitrary length -- so sample that curve at its
    start, middle and end.

    THE AUTHORED COLOUR IS THE POINT.  This used to write a fixed warm-orange
    "fire palette" for every particle system in every plugin, which is why the
    ghost's ectoplasm smoke came out orange/black instead of the pale green
    its NiColorData actually specifies (0.70, 0.83, 0.75 -> 0.51, 0.65, 0.56).
    """
    cm = NifFormat.BSPSysSimpleColorModifier()
    cm.fade_in_percent = 0.1
    cm.fade_out_percent = 0.25
    cm.color_1_start_percent = 0.0
    cm.color_1_end_percent = 0.15
    cm.color_2_start_percent = 1.0
    cm.color_2_end_percent = 0.5

    keys = []
    data = getattr(mod, 'data', None)
    kg = getattr(data, 'data', None) if data is not None else None
    if kg is not None:
        keys = list(getattr(kg, 'keys', []) or [])

    if not keys:
        # No authored curve: a neutral white ramp with an alpha envelope is
        # the honest default -- it tints nothing rather than inventing a hue.
        cols = [(1.0, 1.0, 1.0, 0.0), (1.0, 1.0, 1.0, 1.0),
                (1.0, 1.0, 1.0, 0.0)]
    else:
        cols = [_sample_color_keys(keys, 0.0),
                _sample_color_keys(keys, 0.5),
                _sample_color_keys(keys, 1.0)]

    for i, (r, g, b, a) in enumerate(cols):
        cm.colors[i].r = float(r)
        cm.colors[i].g = float(g)
        cm.colors[i].b = float(b)
        cm.colors[i].a = float(a)
    return cm


def _color_curve_carries_hue(node):
    """Does this system's NiPSysColorModifier supply an actual COLOR?

    Oblivion uses the modifier for two unrelated jobs and they need opposite
    handling when deciding what the shader tint should be:

      * an ALPHA ENVELOPE -- an achromatic ramp, R==G==B at every key, whose
        only real content is the alpha fade-in/fade-out.  fxcloudthick01,
        fxcloudthin01 and fxdustcloud01 all ship exactly
        (0,0,0,0) -> (1,1,1,1) -> (0,0,0,0).  It contributes NO color, so the
        material's emissive_color is the only brightness the effect has.

      * a real COLOR CURVE -- chromatic keys, R!=G!=B.  creatures/ghost's
        PArray* systems ramp (0.702, 0.831, 0.745) -> (0.514, 0.647, 0.561),
        the ghost's pale green, against a near-black 0.039 material.  Here the
        CURVE is the authored color and the material is just a carrier, so
        deferring to the curve is right -- carrying 0.039 through would
        multiply the green down to ~0.027 and render the ghost black.

    Measured over 778 particle systems in meshes/: of the 190 that author a dim
    (<0.5) emissive, 120 have an achromatic curve and 70 a chromatic one.
    Telling them apart by whether the keys carry chroma is the authored test;
    "has a modifier at all" conflates the two and whitens both.
    """
    def _chromatic(r, g, b):
        hi, lo = max(r, g, b), min(r, g, b)
        # Ignore keys that are essentially black -- the endpoints of an alpha
        # envelope -- and call it chroma only on a real spread.
        return hi > 0.02 and (hi - lo) > 0.03

    for m in (node.modifiers or []):
        if isinstance(m, NifFormat.NiPSysColorModifier):
            data = getattr(m, 'data', None)
            kg = getattr(data, 'data', None) if data is not None else None
            for k in (getattr(kg, 'keys', None) or []):
                c = k.value
                if _chromatic(c.r, c.g, c.b):
                    return True
        elif isinstance(m, NifFormat.BSPSysSimpleColorModifier):
            # Already rewritten by _skyrimize_modifiers -- the sampled curve.
            for c in (getattr(m, 'colors', None) or []):
                if _chromatic(c.r, c.g, c.b):
                    return True
    return False


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
            new.append(_simple_color_from(m))
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


def _authored_emissive(color):
    """A material's emissive as a tuple; None when black (no glow authored)."""
    if color.r > 0.0 or color.g > 0.0 or color.b > 0.0:
        return (color.r, color.g, color.b)
    return None


def _collect_psys_properties(node):
    """Texture, flip controller, alpha and authored color from a particle emitter.

    Returns (diffuse_path, flip_ctrl, alpha_prop, emissive, alpha). The emissive
    is the emitter's authored brightness, taken verbatim so a smoke emitter at
    (0.35, 0.35, 0.35) is never promoted to white.

    See: docs/commentary/asset_convert_nif.md#fo3fnv-shader-properties
    """
    diffuse_path = b''
    flip_ctrl = None
    alpha_prop = None
    emissive = None
    alpha = 1.0
    for prop in node.properties:
        if isinstance(prop, NifFormat.NiTexturingProperty):
            diffuse_path = _base_texture_path(prop) or diffuse_path
            flip_ctrl = _find_flip_controller(prop) or flip_ctrl
            continue
        if isinstance(prop, NifFormat.BSShaderPPLightingProperty):
            diffuse_path = _bs_pp_texture_slots(prop)[0]
            continue
        if isinstance(prop, NifFormat.NiMaterialProperty):
            ec = prop.emissive_color
            emissive = _authored_emissive(ec) or emissive
            alpha = float(prop.alpha)
        elif isinstance(prop, NifFormat.NiAlphaProperty):
            alpha_prop = prop
    return diffuse_path, flip_ctrl, alpha_prop, emissive, alpha


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
    # Oblivion's authored brightness/opacity for the particles, taken from the
    # emitter's NiMaterialProperty exactly as the geometry path does.  A smoke
    # emitter authored at (0.35, 0.35, 0.35) must not be promoted to white.
    psys_emissive = None
    psys_alpha = 1.0

    # Sample the authored color curve BEFORE _skyrimize_modifiers rewrites
    # NiPSysColorModifier into its Skyrim equivalent.
    psys_curve_hue = _color_curve_carries_hue(node)

    # Harvest UV-scroll controllers before the Oblivion properties are cleared.
    tex_transforms = _collect_tex_transform_ctrls(node.properties)

    (diffuse_path, flip_ctrl, alpha_prop,
     psys_emissive, psys_alpha) = _collect_psys_properties(node)

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
    # flags2 = vertex_colors only.  emissive_multiple stays at the vanilla
    # neutral 1.0 and the AUTHORED emissive_color supplies the brightness.
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
    sf2.slsf_2_vertex_colors = 1        # particles modulate color per-vertex
    shader.source_texture = effective_path
    # u32 packs clamp mode (low byte, 3 = WRAP_S|WRAP_T) with lighting
    # influence (byte 1, 0xFF) — every vanilla fire effect shader uses 0xFF03.
    shader.texture_clamp_mode = 0xFF03
    # 1.5 was applied here to EVERY particle system regardless of what it emits.
    # It is a fire value (vanilla flame shaders sit at 1.25-1.5), but the same
    # code path converts smoke, mist, steam and dust, and a 50% over-brighten on
    # an additively-blended smoke plume makes it glaring and opaque instead of
    # translucent.  Vanilla's overwhelming default is 1.0 (852/1164 blended FX
    # shapes); the brighter values are authored per-effect, not applied blanket.
    # Oblivion states the intended brightness in NiMaterialProperty.emissive_color
    # (harvested below), so the multiple stays neutral and the authored color
    # does the dimming.
    shader.emissive_multiple = 1.0
    # ...UNLESS the particles carry a real COLOR of their own.  When the
    # system's NiPSysColorModifier ramps an actual hue, that curve is the
    # authored color and the NiMaterialProperty emissive is only its carrier:
    # the ghost pairs a pale-green curve with a near-black 0.039 material, and
    # since Skyrim's effect shader MULTIPLIES by emissive_color, carrying the
    # 0.039 across crushed the green to ~0.027 and rendered the smoke black.
    #
    # But an ACHROMATIC curve is not a color -- it is an alpha envelope, and
    # deferring to it throws the material away for nothing.  That is what made
    # Ayleid-ruin fog blinding: fxcloudthick01 authors (0.078, 0.078, 0.078)
    # against a plain (0,0,0,0)->(1,1,1,1)->(0,0,0,0) ramp, so whitening the
    # shader over-brightened it 12.8x on ADDITIVELY blended geometry that Belda
    # layers several planes deep.  190 of 778 particle systems author a dim
    # (<0.5) emissive and 120 of those have an achromatic curve like this.
    #
    # A NiVertexColorProperty alone is likewise not a color source -- every one
    # of the fog meshes above carries one -- so it no longer forces the tint.
    if psys_emissive is not None and not psys_curve_hue:
        shader.emissive_color.r = psys_emissive[0]
        shader.emissive_color.g = psys_emissive[1]
        shader.emissive_color.b = psys_emissive[2]
    else:
        shader.emissive_color.r = 1.0
        shader.emissive_color.g = 1.0
        shader.emissive_color.b = 1.0
    shader.emissive_color.a = psys_alpha

    node.bs_properties[0] = shader
    _attach_tex_transform_ctrls(shader, tex_transforms)
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
    # Soft-particle depth fade.  Particles are the case this matters most for:
    # a smoke plume drifting into a wall otherwise cuts off along a hard line,
    # and every billboard shows its own quad edge.  alpha_prop is always set by
    # this point (defaulted to additive above), so blended systems all qualify.
    # The AUTHORED emissive, never the shader's final value.  The shader ends
    # up white in three different situations and only one of them is a flame:
    # authored full white (109 systems), a fallback because the source authored
    # BLACK (159), and a fallback because a chromatic curve supplies the color
    # instead (320).  Keying the flame test on the final value would skip the
    # depth fade on all 479 fallback cases -- including the smoke plume in
    # fire\fireopensmallsmoke.nif, which authors (0,0,0) and is exactly the
    # kind of surface the fade exists for.
    _apply_fx_soft_effect(shader, alpha_prop, psys_emissive)


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
    """Wrap a geometry block in a fresh NiBillboardNode so the quad faces the
    camera (vanilla campfire pattern: BSFadeNode → NiBillboardNode →
    NiTriShape).

    The wrapper deliberately carries NO axis correction -- see the comment in
    the body -- and is tagged `_axis_fixed` so the later _skyrimize_billboard
    pass leaves it alone instead of treating it as an Oblivion-authored
    billboard and composing one in.
    """
    bb = NifFormat.NiBillboardNode()
    bb.name = (child.name or b'') + b'-Billboard'
    bb.flags = NIF_FLAGS
    bb.billboard_mode = bb_mode
    # NO axis fix here.  These meshes are authored +Y-up and their PLACED
    # REFERENCES carry the stand-up rotation: censused across Oblivion.esm,
    # 494 REFRs of the Fire\*.nif lights use RotX = +-90 deg (10/10 for
    # FireTorchLargeSmoke, 188+51 of 395 for FireOpenSmall, ...).  The whole
    # model -- quads AND emitter markers -- shares that one +Y-up frame, and
    # the REFR rotates all of it together.  Pre-rotating the quad to +Z-up
    # here made it the ONLY part in a different frame, so the REFR's -90 then
    # laid it flat: the "third flame component on its side", with the smoke
    # and flame beside it looking correct.  Leave the quad in the model frame.
    bb._axis_fixed = True
    bb.num_children = 1
    bb.children.update_size()
    bb.children[0] = child
    return bb


def _is_emitter_marker(node):
    """Is this node referenced as a particle emitter/gravity marker?

    Such a node's rotation is the emission DIRECTION a NiPSysEmitter reads, so
    it survives demotion even though a billboard's rotation is otherwise
    discarded for drawing (NifSkope BillboardNode::viewTrans).
    """
    for blk in node.tree():
        for attr in ('emitter_object', 'gravity_object'):
            if getattr(blk, attr, None) is node:
                return True
    return False


def _skyrimize_billboard(bb):
    """Convert a (non-root) Oblivion NiBillboardNode for Skyrim.

    - Contains a particle system anywhere in its subtree → DEMOTE to a plain
      NiNode (a billboarding ancestor spins the emitters) and wrap its direct
      geometry children in fresh axis-corrected billboard nodes.
    - Pure geometry billboard → keep it, but compose the Skyrim axis
      correction into its rotation (Oblivion billboards are authored identity
      over flat-XY quads; Skyrim's up/facing axes differ).
    """
    # A wrapper this converter built (the root-billboard demotion runs before
    # the child walk reaches these).  It is already in the frame we want, so
    # the pure-geometry branch below must not compose an axis fix into it --
    # that is what laid the flame quad on its side.
    if getattr(bb, '_axis_fixed', False):
        return bb
    bb_mode = int(getattr(bb, 'billboard_mode', 1)) or 1
    has_psys = any(isinstance(b, NifFormat.NiParticleSystem)
                   for b in bb.tree())
    if not has_psys:
        _compose_axis_fix(bb.rotation)
        bb._axis_fixed = True
        return bb
    plain = NifFormat.NiNode()
    plain.name = bb.name
    plain.flags = NIF_FLAGS
    plain.translation.x = bb.translation.x
    plain.translation.y = bb.translation.y
    plain.translation.z = bb.translation.z
    # NOT the billboard's rotation.  A NiBillboardNode DISCARDS its own
    # rotation at runtime and substitutes identity in view space -- NifSkope's
    # BillboardNode::viewTrans (glnode.cpp): `t = parent->viewTrans() * local;
    # t.rotation = Matrix();`.  So the authored rotation on a billboard node
    # was never used for orientation.  Copying it onto the plain replacement
    # RESURRECTS a dead value: firetorchsmall's "Sparks-Emitter" and
    # firecandleflame's "FlameParticles-Emitter" are billboards carrying
    # +Z=(0,-1,0), and reviving that aims the emitter sideways -- the
    # horizontal jet beside the upright flame.  Identity is what the engine
    # actually applied, so identity is what the demoted node inherits.
    #
    # EXCEPT when the node is an EMITTER MARKER.  Rotation-is-discarded applies
    # to how a billboard DRAWS its subtree; a NiPSysEmitter reads its
    # `emitter_object` node's orientation as the emission DIRECTION, and that
    # is live data.  firecandleflame authors quad and emitter in one +Y-up
    # frame -- quad identity with extent [1.3, 2.6, 0.0] (tall in Y), emitter
    # [1,0,0][0,0,-1][0,1,0] whose local +Z maps to model +Y.  Zeroing the
    # emitter makes it +Z-up while the quad stays +Y-up, so the flame splits
    # into an upright quad and a sideways particle jet -- visible once the
    # FlameNode marker rotates the pair into a +Z-up host.
    if _is_emitter_marker(bb):
        plain.rotation.m_11 = bb.rotation.m_11
        plain.rotation.m_12 = bb.rotation.m_12
        plain.rotation.m_13 = bb.rotation.m_13
        plain.rotation.m_21 = bb.rotation.m_21
        plain.rotation.m_22 = bb.rotation.m_22
        plain.rotation.m_23 = bb.rotation.m_23
        plain.rotation.m_31 = bb.rotation.m_31
        plain.rotation.m_32 = bb.rotation.m_32
        plain.rotation.m_33 = bb.rotation.m_33
    else:
        plain.rotation.set_identity()
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

    # NiDynamicEffect subtypes: strip ALL of them.
    # NiTextureEffect (projected texture environment mapping) has a completely
    # different Skyrim rendering path.  Ni*Light blocks (Ambient/Directional/
    # Point/Spot) are 3ds Max export leftovers in Oblivion assets: ZERO vanilla
    # Skyrim meshes contain any Ni*Light block (nif_block_scan 2026-07-18), and
    # SSE fails to load a static that carries one — statuegodszenithar01.nif
    # (NiAmbientLight child) rendered as the missing-model red triangle.
    # Skyrim lighting comes from placed LIGH references, never from mesh-
    # embedded light nodes, so there is nothing to convert these into.
    # Note: the root NiNode's own effects array is cleared during NiNode→BSFadeNode
    # conversion; this branch handles dynamic-effect nodes that appear in the
    # children array.
    if isinstance(node, NifFormat.NiDynamicEffect):
        stats['dynamic_effects_stripped'] = \
            stats.get('dynamic_effects_stripped', 0) + 1
        return None

    # Geometry conversion
    if isinstance(node, (NifFormat.NiTriStrips, NifFormat.NiTriShape)):
        try:
            ts = _process_geometry(node, fix_textures, stats,
                                   sky_type=(stats or {}).get('_sky_type'))
        except UnreconstructibleGeometry as e:
            # dev-era Oblivion shapes with NO topology in the file at all
            # (minotaur hair01/hornsa/minotaurold, has_triangles=False with a
            # non-grass UV layout) — nothing can render them; drop the shape
            # instead of failing the whole file/creature.
            name = node.name.decode('latin-1', 'replace') \
                if isinstance(node.name, bytes) else str(node.name)
            print(f"  [warn] dropping triangle-less shape '{name}': {e}")
            stats['shapes_dropped'] = stats.get('shapes_dropped', 0) + 1
            return None
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


def _has_autoplay_sequence(root):
    """True if the tree carries an ambient (AutoPlay/AutoLoop) sequence.

    Those meshes get the animated-object BSX (0x8B, later masked to 0x0B when
    the BGED is attached) like a converted animated door.  Verified running in
    game with 0x0B (arena spectators, 2026-08-18); vanilla ships 0x01 for the
    same graph on collisionless meshes (29/63), so either would do -- this is
    the one that has been seen working.
    """
    for b in root.tree():
        if not isinstance(b, NifFormat.NiControllerSequence):
            continue
        raw = getattr(b, 'name', b'') or b''
        nm = raw.decode('latin-1') if isinstance(raw, bytes) else str(raw)
        if nm in (_AUTOPLAY_SEQUENCE, _AUTOLOOP_SEQUENCE):
            return True
    return False


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
        # ...except an ambient AutoPlay/AutoLoop mesh (crowd/fountain), which
        # takes the animated-object value like a converted door -- see
        # _has_autoplay_sequence.
        if _has_autoplay_sequence(root):
            bsx_value = BSX_FLAGS_ANIMATED
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
    """Repair geometry Oblivion tolerates and the Skyrim-side tools do not.

    Two defects, both authored, both fatal further down the line.

    A handful of Oblivion source meshes ship NaN data (anvildooruc02.nif has
    9 NaN UVs, middlecandlestickfloor03fake.nif has 2 — one mesh in each of
    the AnvilMagesGuild / AnvilCastlePrivateQuarters cells, whose loads
    crashed with no crash log).  Oblivion's renderer tolerated non-finite
    mesh data; Skyrim SE dies at cell load.

    Non-finite UVs are zeroed; non-finite vertices move to the mesh's finite
    centroid (collapses the triangle instead of stretching it to the origin);
    non-finite normals/tangents/bitangents become +Z; a non-finite bound
    sphere is recomputed after vertices are fixed.

    The second is a shape that declares vertices and ships none — see the
    comment on that branch. It is cleared to a genuinely empty shape, because
    without positions there is nothing to repair it with.

    Returns the number of components fixed.
    """
    fixed = 0
    for block in data.blocks:
        if not isinstance(block, NifFormat.NiGeometryData):
            continue

        # A shape that declares vertices and then ships NONE of them.
        # `LeyawiinLowerDoor01` in leyawiinhouselower01.nif is the measured
        # case: num_vertices=16, has_vertices=False, yet normals, colors, UVs
        # and 6 triangles all still index 16 of them. Oblivion tolerates it
        # (there is nothing to draw, so it draws nothing); anything that walks
        # the faces and reaches for a vertex does not.
        #
        # LODGen is what found it: RemoveUnseenFaces indexes straight into the
        # empty list, throws ArgumentOutOfRangeException, and the run ends with
        # exit 548 and NO .bto tiles — one broken shape costs an entire
        # worldspace its object LOD. Measured: 1 of Nehrim's 1552 _far.nif.
        #
        # Cleared rather than repaired, because there is nothing to repair
        # with: without vertex positions the triangles have no geometry to
        # describe. The shape already drew nothing, so it loses nothing.
        if block.num_vertices and not getattr(block, 'has_vertices', False):
            block.num_vertices = 0
            block.vertices.update_size()
            for flag, arr in (('has_normals', 'normals'),
                              ('has_vertex_colors', 'vertex_colors')):
                if getattr(block, flag, False):
                    setattr(block, flag, False)
                    getattr(block, arr).update_size()
            if hasattr(block, 'uv_sets'):
                block.num_uv_sets = 0
                block.uv_sets.update_size()
            block.num_triangles = 0
            # 🔴 BOTH geometry layouts, and the strips are the one that bites.
            # The measured case is `NiTriStripsData`, which has no `triangles`
            # array at all — it stores STRIPS. A first version of this cleared
            # only `triangles`, so the strips survived, the strips-to-triangles
            # conversion downstream turned them back into 6 triangles, and the
            # shape shipped with zero vertices and six faces indexing vertex 15.
            # LODGen happened to tolerate that; the next tool would not.
            if hasattr(block, 'triangles'):
                block.num_triangle_points = 0
                block.has_triangles = False
                block.triangles.update_size()
            if hasattr(block, 'points'):
                block.num_strips = 0
                block.has_points = False
                block.strip_lengths.update_size()
                block.points.update_size()
            fixed += 1
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


# NiTimeController.flags bit 6 (0x40) is "Compute Scaled Time"
# (references/nif 0.10.0.0.xml TimeControllerFlags, default="true").  Oblivion's
# engine computed scaled time unconditionally and never wrote the bit: across the
# Chargen secret-wall/switch NIFs every controller stores 12 / 40 / 44 — always
# 0x40 CLEAR.  Skyrim reads the flag, so a sequence started via
# ObjectReference.PlayAnimation() binds its targets and reports success but its
# scaled time never advances, leaving the object frozen on frame 0.
#
# Vanilla Skyrim never ships it clear: across 62 animated door/activator meshes
# (Windhelm animated secret doors, Nordic animated doors, Dwemer doors,
# Labyrinthian panel, Winterhold anim door) 157/157
# NiMultiTargetTransformController have flags=108 (0x6C) and every other
# NiTimeController — NiTransformController, NiControllerManager, NiVisController,
# NiFloatExtraDataController — has flags=76 (0x4C).  Both set 0x40; the only
# difference between vanilla's 108 and our 44 is this one bit.
#
# This is why CharacterGen's secret wall never physically opened: the quest
# script ran, the switch fired and PlayAnimation("Forward") was accepted with no
# Papyrus error, but the wall stayed shut.
_CTLR_COMPUTE_SCALED_TIME = 0x40


_TES4_SOUND_KEY = re.compile(rb'^sound:\s*(\S+)\s*$', re.IGNORECASE)


def _convert_sound_text_keys(data):
    """Leave Oblivion's `sound: <EDID>` sequence text keys ALONE.

    DO NOT "modernise" these to `SoundPlay.<SNDR EDID>`.  That rewrite was
    made here on 2026-08-02 and it SILENCED every animated door and gate that
    had been working (StoneWallGateDoor01's iron creak, the portcullises, the
    prison gates) — building the mesh at the parent commit and diffing showed
    the text key was the ONLY difference in the whole file.

    The premise was wrong in both halves:

    1. **Skyrim DOES understand Oblivion's form.**  The Gamebryo text-key
       handler at `0x1401db723` (GOG SkyrimSE.exe) compares the key against
       the literal `"Sound: "` (`r8d = 7`) via `_strnicmp` — CASE-INSENSITIVE,
       so lowercase `sound:` matches — and plays whatever follows those 7
       characters (`lea rcx, [rbx + 7]` at `0x1401db890`).  The same handler
       also takes `"Enum: StopSounds "`.  These strings live at file offsets
       `0x1635f50` / `0x168d0ec`; searching only for lowercase `sound:` is
       what made an earlier pass conclude the engine had no such keyword.

    2. **`SoundPlay.` is the BEHAVIOR-GRAPH channel, not the NIF one.**  It is
       matched against the graph's declared event-name table, so it needs an
       hkx behavior graph to route through — 38 of the 39 vanilla meshes using
       a `SoundPlay.<name>` key carry `BSBehaviorGraphExtraData`.  Converted
       doors deliberately have NO graph (attaching one to an Open/Close door
       CTDs it on cell load), so the rewritten key matched nothing and was
       dropped.

    See: docs/commentary/asset_convert_animation.md#sound-text-keys-are-native

    Kept as a no-op returning 0 so the call site stays explicit about the
    decision rather than looking like an omission.
    """
    return 0


def _strip_empty_text_keys(data):
    """Drop whitespace-only text keys — ONLY for meshes that get a graph.

    On state activation, BGSGamebryoSequenceGenerator (GOG SkyrimSE.exe
    0x505130, Address Library ID 32774) walks the sequence's
    NiTextKeyExtraData translating keys into behavior events: each value is
    first matched whole against the project's event table, and on a miss the
    engine calls `strchr(value, '.')` to split an `Event.Payload` key.  An
    EMPTY NiString loads as a NULL BSFixedString, and the strchr runs on the
    raw pointer — `movdqu xmm2,[rax]` with rax=0 in VCRUNTIME140, R9=0x2E2E
    (the broadcast '.').  That is crash-2026-08-10-01-41-07 (spiddalcloudplant
    ships `t=0.1 ''`) and -01-39-02 (harradauprightattack ships SEVEN empty
    keys): the plant crashed the game the moment it animated.

    Oblivion authored empty keys freely and its engine ignored them.  Skyrim
    tolerates them ONLY on the graph-less path: vanilla ships exactly two
    (impjaildoor01, ruinscanopicjar02 — both plain Open/Close meshes with no
    BSBehaviorGraphExtraData), and zero on any graph-carrying mesh.  So the
    strip is scoped to meshes we give an animobject graph, keeping converted
    graph-less doors byte-identical to what already works in-game.

    Trailing whitespace is left alone — 107 vanilla dungeon keys carry it
    (`'Sound: X\\r\\n'`), so it is engine-legal and not ours to "fix".
    """
    removed = 0
    seen = set()
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not isinstance(block, NifFormat.NiTextKeyExtraData):
                continue
            if id(block) in seen:
                continue
            seen.add(id(block))
            kept = [(k.time, k.value) for k in block.text_keys
                    if bytes(k.value or b'').strip()]
            if len(kept) == block.num_text_keys:
                continue
            removed += block.num_text_keys - len(kept)
            block.num_text_keys = len(kept)
            block.text_keys.update_size()
            for slot, (t, v) in zip(block.text_keys, kept):
                slot.time = t
                slot.value = v
    return removed


def _fix_controller_flags(data):
    """Set "Compute Scaled Time" on every NiTimeController (Skyrim requirement).

    Oblivion sources always leave bit 0x40 clear; Skyrim needs it set or the
    controller's scaled time never advances and the animation never plays.
    Applies to the whole tree, so animated activators, doors, traps and levers
    are all covered — not just the record that surfaced the bug.
    """
    fixed = 0
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not isinstance(block, NifFormat.NiTimeController):
                continue
            flags = getattr(block, 'flags', None)
            if flags is None or (flags & _CTLR_COMPUTE_SCALED_TIME):
                continue
            block.flags = flags | _CTLR_COMPUTE_SCALED_TIME
            fixed += 1
    return fixed


# TES4 animation GROUP names that a script can drive with `playgroup`, which
# converts to ObjectReference.PlayAnimation().  Census of the converted output
# (18,566 scripts): Forward 418, Backward 192, Unequip 45, Equip 27,
# SpecialIdle 10, FastForward 8, Left 6, FastBackward 6, Right 5, Stagger 1.
#
# 'Open'/'Close' are deliberately ABSENT.  They are the engine's own DOOR group
# names, driven natively through the NIF's NiControllerManager — no script ever
# names them, and giving such a mesh a behaviour graph is what CTD'd
# prisonCellGate01 on cell load (2026-07-26).  Vanilla agrees: the graph-driven
# NocturnalsSecretDoor01 uses AnimIdle01/AnimPlay01, never Open/Close.
_SCRIPT_DRIVEN_SEQUENCES = frozenset((
    'forward', 'backward', 'fastforward', 'fastbackward',
    'left', 'right', 'equip', 'unequip', 'specialidle', 'stagger',
))

# Oblivion auto-plays a sequence named "Idle" as soon as the object loads.
# Skyrim has NO such convention: a NiControllerSequence sits idle until
# something starts it (a script's PlayGamebryoAnimation, an engine-native name
# like Open/Close, or the behaviour graph).  So converted ambient animation --
# palacefont01's fountain water, se01waitingroomwalls' light ripples, the arena
# crowds -- simply never ran, and the surface rendered as a frozen first frame.
#
# Vanilla's self-playing convention is the AutoPlay/AutoLoop sequence pair
# driven by GenericBehaviors\Autoplay.hkx (see _autoplay_ambient_sequences).
_AUTOPLAY_SEQUENCE = 'AutoPlay'
_AUTOLOOP_SEQUENCE = 'AutoLoop'
# NiControllerSequence cycle types (nif.xml CycleType): 0 = LOOP, 1 = REVERSE,
# 2 = CLAMP.  Reading 2 as "loop" is what left every converted ambient mesh
# playing exactly one cycle and freezing.
_CYCLE_CLAMP = 2

# Oblivion sequence names that mean "ambient, plays by itself" and therefore
# become the AutoPlay/AutoLoop pair.  Script-driven names are excluded --
# those are reached through the behaviour graph by their own name and must
# keep it.
_AMBIENT_SEQUENCES = frozenset(('idle',))


def collect_sequence_names(data):
    """NiControllerSequence names a script can reach via PlayAnimation().

    These become both the behaviour-graph state names and the events that
    select them, so `PlayAnimation("Forward")` reaches the right sequence.
    Order is the manager's own, deduplicated, so the generated graph is
    byte-reproducible across runs.

    Only SCRIPT-DRIVEN group names qualify (see _SCRIPT_DRIVEN_SEQUENCES).  A
    mesh whose sequences are all engine-native ('Open'/'Close' on doors) is
    already animated by the engine and must NOT get a graph — attaching one
    makes the engine bind the sequence through the graph instead and crash.

    Empty when the mesh has no controller manager — a static mesh needs no
    graph and must not get a BGED.
    """
    names = []
    seen = set()
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not isinstance(block, NifFormat.NiControllerManager):
                continue
            for seq in block.controller_sequences:
                if seq is None:
                    continue
                raw = getattr(seq, 'name', b'') or b''
                name = raw.decode('latin-1') if isinstance(raw, bytes) else str(raw)
                # A sequence stripped down to nothing by
                # _process_controller_manager animates no node; giving it a
                # state would make PlayAnimation() succeed on a dead sequence.
                if not name or name in seen or not seq.num_controlled_blocks:
                    continue
                # AutoPlay/AutoLoop are not script-driven, but they still
                # need the graph: it is the behaviour graph that starts them
                # (63/63 vanilla AutoPlay meshes carry a BGED, and the arena
                # crowd's graph was read running out of the live engine).
                if (name not in (_AUTOPLAY_SEQUENCE, _AUTOLOOP_SEQUENCE) and
                        name.lower() not in _SCRIPT_DRIVEN_SEQUENCES):
                    continue
                seen.add(name)
                names.append(name)
    return names


def _add_animobject_bged(data, graph_file):
    """Point the root at the generated hkx project + mark the tree Animated.

    Mirrors the vanilla animated-object contract (and `bow_rig._add_bged`):
    without the BGED there is no animation graph manager, so PlayAnimation()
    returns immediately and does nothing; without the BSX Animated bit the
    engine never ticks the graph it just loaded.
    """
    for root in data.roots:
        if root is None or not hasattr(root, 'extra_data_list'):
            continue
        for ed in root.extra_data_list:
            if isinstance(ed, NifFormat.BSBehaviorGraphExtraData):
                ed.behaviour_graph_file = graph_file.encode('latin-1')
                break
        else:
            bged = NifFormat.BSBehaviorGraphExtraData()
            bged.name = b'BGED'
            bged.behaviour_graph_file = graph_file.encode('latin-1')
            # 0 = graph drives this object only, not a shared base skeleton.
            bged.controls_base_skeleton = 0
            root.num_extra_data_list += 1
            root.extra_data_list.update_size()
            root.extra_data_list[root.num_extra_data_list - 1] = bged
        for ed in root.extra_data_list:
            if isinstance(ed, NifFormat.BSXFlags):
                # Animated bit ON (or the engine never ticks the graph) and
                # bit 0x80 OFF.  0x80 marks the object as articulated/
                # ragdoll-driven; paired with a BGED the engine waits on a
                # physics rig that a Gamebryo-sequence graph never provides and
                # NEVER DRAWS THE MESH — invisible in game, perfect in NifSkope
                # (which does not load the hkx at all).
                #
                # Census of all 217 vanilla animated-object meshes that carry a
                # BGED: **0 set bit 0x80** (values 0x4-0x20; the graph-driven
                # NocturnalsSecretDoor01 is 0x0B).  Our converter's longstanding
                # BSX_FLAGS_ANIMATED is 0x8B, which is correct for a mesh with
                # NO graph — prisonCellGate01 renders fine with it — so the
                # illegal combination only appears where we add the BGED.
                ed.integer_data = (int(ed.integer_data) | 0x01) & ~0x80
                break
        return True
    return False


# ---------------------------------------------------------------------------
# Armor / clothing NIF helpers
# ---------------------------------------------------------------------------

def _is_ground_model(nif_basename: str) -> bool:
    """True if this filename is an armor/clothing ground (inventory) model.

    Bethesda's convention is ``<item>_gnd.nif``, but assets that came through a
    Morrowind->Oblivion conversion lost the separator: the Morroblivion naming
    scheme rewrites '_' as 'u', so the same files land as ``<item>ugnd.nif``
    (``cumurobeucommonu02ugnd.nif``).  Others drop the separator entirely after
    a body-part word (``...shoegnd.nif``, ``...shirtgnd.nif``,
    ``amuletcommon1gnd.nif``).

    Matching the bare ``gnd`` suffix covers all three spellings.  A worn mesh
    would have to genuinely end in the letters "gnd" to false-positive, which no
    body slot or equipment word does.

    Getting this wrong is not cosmetic: a ground model misread as worn armor
    keeps a NiNode root instead of BSFadeNode (so it never collides and floats
    where it was dropped), loses its BSInvMarker, and — when it is skinned — is
    FK-retargeted onto the Skyrim biped, which mangles the mesh.
    """
    return nif_basename.endswith('gnd.nif')


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


# Bone names that mark a Prn piece as HEAD gear (post-rename the bone is
# 'NPC Head [Head]'; the Oblivion name is accepted defensively).
_PRN_HEAD_BONES = (b'NPC Head [Head]', b'Bip01 Head')


def _collect_prn_head_blocks(data, prn_block_ids):
    """The Prn blocks of a NIF that hang on the HEAD bone.

    Walks the LIVE tree, never data.blocks: the strips->shape conversion
    replaces geometry objects, so data.blocks is stale by this point and an
    id() lookup over it silently matches nothing (the fit then never ran and
    every helmet fell back to the legacy PRN scale table).
    """
    blocks = []
    seen = set()
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if id(block) not in prn_block_ids or id(block) in seen:
                continue
            if not isinstance(block, (NifFormat.NiTriShape,
                                      NifFormat.NiTriStrips)):
                continue
            skin = getattr(block, 'skin_instance', None)
            if skin is None or skin.num_bones < 1 or skin.bones[0] is None:
                continue
            name = bytes(skin.bones[0].name or b'')
            if name not in _PRN_HEAD_BONES:
                continue
            gd = block.data
            if gd is None or gd.num_vertices == 0:
                continue
            seen.add(id(block))
            blocks.append(block)
    return blocks


def _fit_prn_head_blocks(data, prn_block_ids, src_path, race=None) -> set:
    """Fit head-attached Prn blocks onto the Skyrim head (head_fit).

    Runs AFTER retarget: Prn verts are face-space (authored coords, the shape
    transform baked in) and render as ``verts + head bone world``, so the fit
    maps them into Skyrim head space directly — the same frame the old
    ARMOR_PIECE_OFFSETS_PRN affine operated in.  All head blocks of the NIF
    are solved as ONE system so multi-shape helmets keep their seams.

    `race` selects a beast head pack (head_fit.BEAST_RACES); None fits the
    shared human head.  A hood is ONE Oblivion record worn by every race,
    so the caller re-runs this per race and writes a mesh per race exactly
    as vanilla Skyrim ships one - see head_fit.BEAST_RACES.

    Returns the ids of the blocks that were fitted (empty when the fit data
    is unavailable — callers then fall back to the legacy constants).
    """
    from . import head_fit
    female = '/f/' in str(src_path).replace('\\', '/').lower()
    if not head_fit.fit_available(female):
        return set()

    blocks = _collect_prn_head_blocks(data, prn_block_ids)
    if not blocks:
        return set()

    import numpy as np
    shapes = []
    for block in blocks:
        gd = block.data
        verts = np.array([[v.x, v.y, v.z] for v in gd.vertices],
                         dtype=np.float64)
        try:
            tris = np.array([tuple(t) for t in gd.get_triangles()],
                            dtype=np.int64)
        except Exception:
            tris = np.zeros((0, 3), dtype=np.int64)
        if tris.size == 0:
            tris = np.zeros((0, 3), dtype=np.int64)
        shapes.append((verts, tris))

    fitted = head_fit.fit_head_gear(shapes, female, race=race)
    if fitted is None:
        return set()
    for block, new_v in zip(blocks, fitted):
        gd = block.data
        for i, v in enumerate(gd.vertices):
            v.x = float(new_v[i, 0])
            v.y = float(new_v[i, 1])
            v.z = float(new_v[i, 2])
        try:
            gd.update_center_radius()
        except Exception:
            pass
    return {id(b) for b in blocks}


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


def _bake_root_transform_into_verts(root_node):
    """Bake ONLY the root node's own transform into the geometry verts.

    The sibling _bake_node_transforms_into_verts flattens the geometry node's
    transform too, which is right for creature parts (their bind is identity
    and nothing else carries the offset) but wrong for worn armor: skin_retarget
    places a PRN piece by ADDING the Skyrim bone position to the geometry node's
    existing translation, so that translation has to survive to that point.
    Baking the root alone keeps a non-identity root (rare, but real) from being
    dropped while leaving the per-geometry offset intact.
    """
    root_m = root_node.get_transform()
    if root_m.is_identity():
        return
    rot = root_m.get_matrix_33()
    for block in list(root_node.tree()):
        if not isinstance(block, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
            continue
        gd = block.data
        if gd is None:
            continue
        for v in gd.vertices:
            nv = v * root_m
            v.x, v.y, v.z = nv.x, nv.y, nv.z
        if getattr(gd, 'has_normals', 0):
            for n in gd.normals:
                nn = n * rot
                n.x, n.y, n.z = nn.x, nn.y, nn.z
        try:
            gd.update_center_radius()
        except Exception:
            pass
    root_node.rotation.set_identity()
    root_node.translation.x = root_node.translation.y = 0.0
    root_node.translation.z = 0.0
    root_node.scale = 1.0


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


# Skyrim body part -> the Oblivion bone that piece rigidly attaches to.  Used
# only when a worn NIF carries NO 'Prn' and NO skin at all: those meshes fell
# straight out of _add_prn_skin, shipping with no skin instance, so they never
# left Oblivion object space (Morroblivion's cryohelm.nif rendered at z -5..27
# instead of ~115..133, i.e. on the floor).  Keyed off the wearing record's
# BMDT biped flags -- the plugin's own statement of what the item is.
_BODY_PART_FALLBACK_PRN_BONE = {
    131: 'Bip01 Head',
    33:  'Bip01 R Hand',
    37:  'Bip01 R Foot',
}


def _add_prn_skin(data, root_node, keep_bone_names=False, plain=False,
                  fallback_bone=None):
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

    The per-bone skin bind is IDENTITY, which is correct here: the caller
    runs _bake_node_transforms_into_verts first, leaving the verts in
    bone-LOCAL space (verified: converted dog Head centroid (2.8,14.5,0),
    a bone-local coordinate, not the (0,42,57) bind-world of Bip01 Head), so
    `vert · I · boneWorld` places the part correctly and tracks the bone
    under both animation and ragdoll.

    Returns the number of geometry blocks that were skinned.
    """
    if not isinstance(root_node, NifFormat.NiNode):
        return 0

    prn_val = _get_prn_bone(root_node) or fallback_bone
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
                 creature=False, worn=False, parallax=False, biped_flags=0,
                 tex_fallback=(), hair=False, race=None):
    """Convert a PyFFI NifFormat.Data in-place to Skyrim format.

    worn=True marks the NIF as body-worn gear on the plugin's own authority (an
    ARMO/CLOT record names it as a biped model — see wearable_plan.is_worn).
    It only ever widens the armor path: the folder-name guess below still
    applies on its own for meshes no record references.

    parallax=True carries Oblivion's APPLY_HILIGHT2 height field across as a
    Skyrim slot-3 height map (asset_convert/parallax.py).  Off by default: the
    result needs Community Shaders or ENB and renders wrong without one.

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
        '_src_path': str(src_path),   # for flip-book/height-map source lookup
        '_tex_fallback': tex_fallback or (),
        '_parallax': bool(parallax),  # opt-in; see _apply_parallax
        # Sky geometry (stars/clouds/atmosphere) needs BSSkyShaderProperty
        # rather than the lighting shader — see sky_object_type_for.
        '_sky_type': sky_object_type_for(src_path),
    }

    # Drop orphaned non-scene-graph roots before anything walks data.roots.
    _prune_orphan_roots(data)

    # Detect animation (affects motion_system choice in collision_handler)
    has_skin = _has_skin(data)

    # Resolve NiControllerSequence StringPalette offsets BEFORE version upgrade.
    # In Oblivion format (UV2=11) controlled_blocks store node_name etc. as
    # integer offsets into NiStringPalette.  After we set data.version to the
    # Skyrim value, PyFFI switches to direct-string mode and the offsets are
    # ignored — leaving every node_name as b''.  Skyrim uses node_name to look
    # up animation targets; empty names → null → crash on NIF load.
    _resolve_palette_strings(data)

    # Oblivion `sound: X` text keys are NATIVE in Skyrim too and must survive
    # verbatim — rewriting them to SoundPlay.* silenced every animated gate.
    _convert_sound_text_keys(data)

    # Oblivion never sets NiTimeController "Compute Scaled Time" (0x40); Skyrim
    # requires it or a PlayAnimation()'d sequence binds but never advances.
    _fix_controller_flags(data)

    # Fix non-finite render geometry (NaN UVs/verts in Oblivion sources) BEFORE
    # any tangent computation or skin retargeting can propagate the NaNs.
    # Skyrim SE crashes at cell load on non-finite mesh data with no crash log.
    _sanitize_geometry_data(data)

    # --- Armor / clothing NIF fixups (before version upgrade) ---------------
    nif_basename = os.path.basename(src_path).lower()
    _is_gnd = _is_ground_model(nif_basename)
    # Worn gear: the plugin's biped model references decide it (worn), and the
    # vanilla folder convention is the fallback for meshes no record names.
    # Never the folder alone — Nehrim files its armor under eyren/, spinat/,
    # nehrim/, skeletonk/ and would lose the dismember skin, the NiNode root
    # and the Skyrim skeleton retarget on all 88 of them.
    _in_armor_dir = worn or \
        'armor' in src_path.lower().replace('\\', '/') or \
        'clothes' in src_path.lower().replace('\\', '/')   # clothing
    # Bit 13 of the record's BMDT flags is Shield -- authored, and it catches
    # what the filename misses ('towersheild.nif' is misspelled, so the old
    # name check dragged it through the worn-armor path).  The name is only
    # consulted for meshes no ARMO/CLOT record names.
    _is_shield = (bool(biped_flags & (1 << 13)) if biped_flags
                  else 'shield' in nif_basename)
    # Biped slot the wearing record assigns this mesh (131 head, 32 body, ...),
    # or None when no record names it.  Authored data -- always preferred over
    # the filename stem and the geometry name, both of which are guesses.
    from .wearable_plan import (body_part_for_flags as _bp_for_flags,
                                body_parts_for_flags as _bps_for_flags)
    _authored_bp = _bp_for_flags(biped_flags) if biped_flags else None
    # Every slot the record claims.  More than one means the mesh holds several
    # shapes and the per-shape slot is resolved from its skin weights.
    _authored_allowed = _bps_for_flags(biped_flags) if biped_flags else None
    # True when the record names exactly one slot, so it answers outright.
    _single_slot = _authored_allowed is not None and len(_authored_allowed) == 1
    # Slot used for whole-file decisions (offset table, body-fill partition).
    # Filled in after retarget for multi-slot meshes, which need the skin.
    _slot_for_offset = _authored_bp if _single_slot else None

    # Bow bend rig: capture string vertex masks from the Oblivion draw morph
    # BEFORE _walk_node strips the NiGeomMorpherController (see bow_rig.py).
    # Detection mirrors _remap_prn: Prn='BackWeapon' + 'bow' in the filename.
    _bow_string_masks: dict = {}
    _is_bow_weapon = False
    if 'bow' in nif_basename and not _is_gnd and not _in_armor_dir:
        for _r in data.roots:
            if _r is not None and _get_prn_bone(_r) == 'BackWeapon':
                from .bow_rig import capture_string_masks
                _is_bow_weapon = True
                _bow_string_masks = capture_string_masks(data)
                break

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

        # Skyrim needs a node per weapon TYPE for the sheathed position, on top
        # of the three Oblivion attachment points renamed above — a converted
        # weapon's Prn names one of those (Prn=WeaponMace etc.) and without the
        # node the mesh attaches to the actor root and slides around at its
        # feet.  Only meaningful on the skeleton, which is the only creature
        # NIF carrying the rig.
        _add_creature_equip_nodes(data)

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
            #
            # Bake the ROOT's transform into the verts first, exactly as the
            # creature path above does: _add_prn_skin writes an IDENTITY bind,
            # which is only correct once the verts are in bone-local space.
            # The geometry node's own transform is deliberately NOT baked --
            # skin_retarget composes it with the Skyrim bone position (a PRN
            # part is placed by its node, not by its bind matrix).
            #
            # A few worn meshes carry neither 'Prn' nor a skin; fall back to
            # the bone their piece type implies so they still attach instead
            # of shipping unskinned at Oblivion origin.
            _fb_bone = _BODY_PART_FALLBACK_PRN_BONE.get(_authored_bp)
            for root in data.roots:
                if root is not None:
                    _bake_root_transform_into_verts(root)
                    _add_prn_skin(data, root, fallback_bone=_fb_bone)
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
    # Creature body parts keep NiNode root + plain NiSkinInstance, exactly
    # like worn armor keeps NiNode (both are skeleton-attached at runtime).
    _is_creature_body = creature and has_skin
    _is_worn_armor = (not _is_gnd and _in_armor_dir and not _is_shield) \
        or _is_creature_body

    for i, root in enumerate(data.roots):
        if root is None:
            continue

        # Bare geometry roots.  A few Oblivion-era meshes are authored with a
        # NiTriShape/NiTriStrips as the ROOT block, with no node above it.
        # Skyrim never ships one: a 400-mesh vanilla census found 0 geometry
        # roots (BSFadeNode 340, NiNode 55, BSMasterParticleSystem 2,
        # BSLeafAnimNode 3), and anything walking the tree as a node scene
        # graph breaks on them — LODGenx64 hard-crashes with
        # "Unable to cast NiTriShape to NiNode" and abandons the ENTIRE
        # worldspace's object LOD, not just the offending mesh.  Wrap the
        # geometry in a NiNode so it converts to BSFadeNode like any other
        # static below.  The geometry keeps its own transform, so wrapping is
        # visually identity.
        if isinstance(root, (NifFormat.NiTriShape, NifFormat.NiTriStrips)):
            holder = NifFormat.NiNode()
            holder.name = root.name
            holder.flags = NIF_FLAGS
            holder.num_children = 1
            holder.children.update_size()
            holder.children[0] = root
            data.roots[i] = holder
            root = holder
            stats['geometry_roots_wrapped'] = \
                stats.get('geometry_roots_wrapped', 0) + 1

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
                # Identity, not the billboard's rotation -- see the note in
                # _skyrimize_billboard: a NiBillboardNode discards its own
                # rotation at runtime (NifSkope BillboardNode::viewTrans), so
                # copying it onto the plain replacement revives a value the
                # engine never used and skews the whole subtree.
                plain.rotation.set_identity()
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

        # Convert NiNode root → BSFadeNode (skip for worn armor: they use NiNode).
        # Sky meshes also keep a plain NiNode: BSFadeNode applies distance-based
        # fading, which is meaningless for a dome that is always drawn around the
        # camera, and every vanilla sky/*.nif root is a plain NiNode.
        _is_sky = stats.get('_sky_type') is not None
        if type(root).__name__ == 'NiNode' and not _is_worn_armor and not _is_sky:
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
                # --- BSBound (actor bounding box) ---
                # "Bethesda-specific collision bounding box for skeletons"
                # (nif.xml).  The engine uses it as the actor's physical
                # bounds; a creature skeleton without one has no bounds to
                # place or collide a body against, so the ragdoll/death
                # handoff has nothing to land on.  Oblivion creature
                # skeletons ship one (name 'BBX') and 35/39 vanilla Skyrim
                # creature skeletons have one — but the SELECTIVE copy below
                # never carried it, so all 44 converted creature skeletons
                # lost it at the NiNode->BSFadeNode swap (2026-08-08).
                # Values are already in NIF object space (NOT Havok space),
                # so they carry over verbatim — no _HAVOK_SCALE here.
                for ed in root.extra_data_list:
                    if isinstance(ed, NifFormat.BSBound):
                        fade.num_extra_data_list += 1
                        fade.extra_data_list.update_size()
                        fade.extra_data_list[
                            fade.num_extra_data_list - 1] = ed
                        stats.setdefault('bsbound_kept', 0)
                        stats['bsbound_kept'] += 1
                        break

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
                        # NOT for bows: Oblivion bows already match the Skyrim WeaponBow
                        # frame (string side at -X: OB steel bow string x=-15.7, vanilla
                        # steelbow string bones x=-13.7) — the flip held them backwards
                        # with the curve facing the archer.
                        if remapped != 'WeaponBow':
                            fade.rotation.m_11 = -1.0; fade.rotation.m_12 =  0.0; fade.rotation.m_13 = 0.0
                            fade.rotation.m_21 =  0.0; fade.rotation.m_22 =  1.0; fade.rotation.m_23 = 0.0
                            fade.rotation.m_31 =  0.0; fade.rotation.m_32 =  0.0; fade.rotation.m_33 = -1.0

                    elif remapped == 'SHIELD' and prn_val == 'Torch':
                        # A TORCH also hangs off the SHIELD node (Skyrim carries
                        # it in the off-hand), but it is NOT a shield and must
                        # NOT get the shield attach transform below.
                        #
                        # That transform exists because Oblivion straps a shield
                        # to 'Bip01 L ForearmTwist' while Skyrim glues the root
                        # to the SHIELD bone at the hand grip — the two attach
                        # frames differ, so the geometry has to be remapped.
                        # A torch has no such mismatch: it is authored at the
                        # grip in BOTH games.  Vanilla
                        # meshes\weapons\torch\torch.nif is identity rotation,
                        # zero translation, geometry at identity.  Applying the
                        # shield transform threw it ~65deg off with a -20.5
                        # forearm-strap offset (in-game: torch at a completely
                        # wrong orientation).
                        #
                        # The torch still needs its own BSInvMarker: SHIELD is
                        # in _EQUIPPED_PRN_VALUES, so the per-mesh inventory
                        # pass skips it and it would otherwise ship none.
                        # Vanilla torch.nif: rot (4712, 0, 0), zoom 0.82 —
                        # same orientation as a shield, slightly pulled back.
                        inv = NifFormat.BSInvMarker()
                        inv.name = b'INV'
                        inv.rotation_x = TORCH_INV_MARKER_ROT_X
                        inv.rotation_y = TORCH_INV_MARKER_ROT_Y
                        inv.rotation_z = TORCH_INV_MARKER_ROT_Z
                        inv.zoom = TORCH_INV_MARKER_ZOOM
                        fade.num_extra_data_list += 1
                        fade.extra_data_list.update_size()
                        fade.extra_data_list[fade.num_extra_data_list - 1] = inv

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
                        # Shield placement: exact Oblivion-relative attachment.
                        #
                        # Oblivion straps the shield to 'Bip01 L ForearmTwist'
                        # (identity root transform); Skyrim glues the NIF root
                        # to the 'SHIELD' bone at the hand grip.  The transform
                        # from _shield_attach_transform() maps between the two
                        # attach frames through anatomically corresponding hand
                        # frames of both skeletons, so the shield sits on the
                        # forearm at the handle exactly as it did in Oblivion —
                        # no per-mesh bbox heuristics.
                        #
                        # Pass-6c below detects non-identity rotation and wraps
                        # geometry in an inner NiNode (carrying both R and T),
                        # then zeros the BSFadeNode.
                        _T = _shield_attach_transform()
                        if _T is not None:
                            fade.rotation.m_11 = float(_T[0, 0]); fade.rotation.m_12 = float(_T[0, 1]); fade.rotation.m_13 = float(_T[0, 2])
                            fade.rotation.m_21 = float(_T[1, 0]); fade.rotation.m_22 = float(_T[1, 1]); fade.rotation.m_23 = float(_T[1, 2])
                            fade.rotation.m_31 = float(_T[2, 0]); fade.rotation.m_32 = float(_T[2, 1]); fade.rotation.m_33 = float(_T[2, 2])
                            fade.translation.x = float(_T[3, 0])
                            fade.translation.y = float(_T[3, 1])
                            fade.translation.z = float(_T[3, 2])

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

            # Fix NiSkinInstance.skeleton_root -- the same dangling-back-
            # reference class as the controllers above.  A skinned shape names
            # the node its bone transforms are relative to; on a self-skinned
            # clutter mesh (rope, chain, banner, hanging bucket) that node IS
            # the root.  old_root is no longer in the tree, so PyFFI writes the
            # link as null (-1), and Skyrim cannot resolve the skin's frame of
            # reference -- the shape renders as the red missing-geometry
            # marker.  Symptom seen on dungeons\chargen\ropebucket01.nif, whose
            # two BucketRope shapes are skinned to the c_BucketBone chain.
            for blk in root.tree():
                si = getattr(blk, 'skin_instance', None)
                if si is not None and si.skeleton_root is old_root:
                    si.skeleton_root = root

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

            # Fix NiPSysMeshEmitter.emitter_meshes the same way.  Mesh emitters
            # reference their source geometry through a SECOND link, outside the
            # children arrays that _walk_node rewrites — so when a NiTriStrips
            # emitter mesh is replaced by its NiTriShape equivalent, the emitter
            # still points at the ORPHANED strips block.  PyFFI then re-serializes
            # that block (it is still reachable), leaving raw Oblivion NiTriStrips
            # in a Skyrim file.  Skyrim has no NiTriStrips renderer — vanilla is
            # 107/107 NiTriShape across all 256 NiPSysMeshEmitter meshes — so the
            # engine fails the whole NIF and draws the red missing-mesh triangle
            # (se11sheopooffx, se01waitingroomwalls, palacefont01).
            for block in root.tree():
                if not isinstance(block, NifFormat.NiPSysMeshEmitter):
                    continue
                for mi in range(len(block.emitter_meshes)):
                    replacement = block_map.get(id(block.emitter_meshes[mi]))
                    if replacement is not None:
                        block.emitter_meshes[mi] = replacement

        # Reconcile retargeted UV controllers with the shader each target node
        # actually received (Lighting vs Effect number their variables
        # differently).  Must follow the geometry walk.
        _match_seq_shader_types(root)

        # Rebuild dropped NiGeomMorpherController animation (Skyrim has no
        # morph class) as baked target shapes + NiVisController swaps.
        # Must follow the geometry walk (clones copy CONVERTED shapes/
        # shaders) and precede _apply_rest_visibility / sequence-name
        # collection.
        _emulate_morphs(root, stats)

        # Oblivion's auto-started "Idle" sequence -> vanilla's AutoPlay (CLAMP
        # intro) + AutoLoop (the authored loop) pair, or the animation never
        # starts.  Runs before collect_sequence_names so the behaviour graph is
        # built from the final names.
        _autoplay_ambient_sequences(root, stats)

        # Nodes a sequence keeps invisible at t=0 must ship hidden: Skyrim only
        # applies a sequence's keys while it plays, so mid-effect-only geometry
        # otherwise renders from cell load (se11sheopooffx's black cone).
        _apply_rest_visibility(root, stats)

        # A shader controller that lives ONLY as a sequence entry drives
        # nothing; vanilla always hangs it off the shader too (481/481).
        # Runs after _match_seq_shader_types so the final controller object is
        # the one that gets attached.
        _attach_seq_shader_controllers(root, stats)

        # Stamp vanilla's manager-driven header onto every blend interpolator,
        # whether synthesized here or copied from the source.  Must follow every
        # pass that can create or replace one.
        _normalize_blend_interpolators(root, stats)

        # Hide NiPSysMeshEmitter SOURCE geometry.  These shapes exist only to
        # define where particles spawn and must never be drawn.  Oblivion hides
        # them with NiMaterialProperty.alpha = 0.0; Skyrim has no material
        # property, and our conversion also forces NIF_FLAGS (visible) onto
        # every node, so they came through as solid untextured boxes sitting
        # over the effect (se11sheopooffx's white blobs).
        # Vanilla census of 119 emitter-source shapes across 80 particle
        # meshes: 114 set the node's HIDDEN flag (0x000F, bit 0) *and* carry NO
        # shader property at all.  Match that exactly -- it is what the engine
        # expects, and it also drops the pointless texture/shader payload.
        for block in root.tree():
            if not isinstance(block, NifFormat.NiPSysMeshEmitter):
                continue
            for mesh in block.emitter_meshes:
                if mesh is None:
                    continue
                mesh.flags = int(mesh.flags) | 0x0001   # hidden
                for pi in range(len(mesh.bs_properties)):
                    mesh.bs_properties[pi] = None
                stats['emitter_meshes_hidden'] = \
                    stats.get('emitter_meshes_hidden', 0) + 1

        # A lighting shader over UV-less geometry is unrenderable.
        # BSLightingShaderProperty ALWAYS samples a diffuse texcoord and reads
        # the tangent basis for its normal map, but geometry with
        # num_uv_sets == 0 ships neither stream, so the shader samples whatever
        # follows the vertex buffer -- OblivionArchGate01's "red triangle".
        # Vanilla census (373 shapes, references/Skyrim Meshes): ZERO pair a
        # lighting shader with 0 UV sets.  The 54 UV-less vanilla shapes are
        # either BSEffectShaderProperty (45 -- that shader needs no tangents)
        # or carry NO shader at all (9).
        #
        # These are Oblivion helper volumes (emitter sources, spawn//effect
        # proxies) that the source hides but that the emitter_meshes pass above
        # cannot see: they reach the shape through a path other than
        # NiPSysMeshEmitter, or nothing references them at all.  Match vanilla:
        # drop the shader and hide the node.  Geometry that is genuinely meant
        # to be drawn always has UVs, so this can only ever catch helpers.
        for block in root.tree():
            if not isinstance(block, NifFormat.NiTriBasedGeom):
                continue
            geom_data = getattr(block, 'data', None)
            if geom_data is None:
                continue
            if int(getattr(geom_data, 'num_uv_sets', 0) or 0):
                continue
            props = getattr(block, 'bs_properties', None)
            if props is None:
                continue
            lit = [pi for pi, p in enumerate(props)
                   if p is not None
                   and isinstance(p, NifFormat.BSLightingShaderProperty)]
            if not lit:
                continue
            for pi in lit:
                props[pi] = None
            block.flags = int(block.flags) | 0x0001   # hidden
            stats['uvless_lit_shapes_hidden'] = \
                stats.get('uvless_lit_shapes_hidden', 0) + 1

        # Skyrim requires collision on the root node only.
        # If we did NOT wrap, check whether a child holds the collision and hoist it.
        # (When wrapped, the root's own collision was kept on the root above.
        # Hoisting from under the rotated wrapper would have to compose the
        # WRAPPER's transform as well as the child's — hoist_collision only
        # composes the child's — so that case stays on the wrap path, which
        # already absorbs the root transform via bake_node_transform_into_body.)
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
        # First drop Oblivion's collision-toggle proxies (alit), while the
        # bodies still carry SOURCE units: hkx_ragdoll applies the same
        # predicate to the source NIF, and the two files must agree body
        # for body.
        if creature and 'skeleton' in nif_basename:
            strip_marker_collision_bodies(data, root)
        convert_all_collisions(root, keep_blend=creature)

        # Scale Havok constraint pivot points (Oblivion → Skyrim Havok scale).
        # Constraint pivots are stored in Havok-space positions and must be scaled
        # by _HAVOK_SCALE (0.1) just like rigid body translations and shape dims.
        # Also sets broadphaseType=10 for dynamic constrained bodies (swinging signs).
        if has_constraints:
            scale_constraint_pivots(data)

        # Creature skeletons: make the NIF's constraint lists EXACTLY the
        # ragdoll tree the skeleton.hkx ships (first body bare, every other
        # body one joint to an earlier body) — the engine's ragdoll attach
        # indexes NIF constraints by body order with no bounds check (the
        # 2026-08-28 alit crash), and an unconstrained body never falls
        # (2026-08-08).  Contract: hkx_ragdoll.plan_ragdoll_tree.
        # AFTER scale_constraint_pivots: the synthesized pivots are built from
        # body `center` values that are already in Skyrim Havok units.
        if creature and 'skeleton' in nif_basename:
            if enforce_ragdoll_tree(data, root):
                has_constraints = True

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
        # (The 80-bone cap reduction happens later in merge_creature_body —
        # part NIFs store bones FLAT, so no hierarchy exists to merge into.)
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
                    _regen_skin_partition(block, skin, geom_name,
                                          authored_body_part=_authored_bp,
                                          authored_allowed=_authored_allowed)

    if not creature and not _is_gnd and _in_armor_dir and has_skin:
        from .skin_retarget import retarget_skin_to_skyrim as _retarget

        # Which offset/scale table entry fits this piece.  ONE offset applies to
        # the whole NIF, so a record claiming a SINGLE slot answers it outright.
        # A multi-slot record (Knight of Order: helmet + torso + legs + feet in
        # one mesh, flags 0x003D) has no single stated answer, and taking its
        # head-ward slot lifted the entire suit by the helmet's dz=+7 -- its
        # Foot shape floated from z -1.3 to +5.9.  Resolve those by where the
        # mesh's skinned vertex MASS actually sits, which is the rig the artist
        # authored.  Per-SHAPE slotting is handled separately in retarget.
        from .skin_retarget import dominant_body_part as _dominant_bp
        _BP_TO_PIECE = {131: 'helmet', 32: 'cuirass', 44: 'greaves',
                        33: 'gauntlets', 37: 'boots'}
        if not _single_slot:
            _slot_for_offset = _dominant_bp(data, allowed=_authored_allowed)
        _piece_type = _BP_TO_PIECE.get(_slot_for_offset, 'default')

        _prn_block_ids: set = set()
        _retarget(data, src_path=src_path, prn_out=_prn_block_ids,
                  weight=weight, authored_body_part=_authored_bp,
                  authored_allowed=_authored_allowed,
                  race=race)

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

        # RIGID HEAD GEAR IS FITTED BY MEASUREMENT, NOT BY A SCALE.
        #
        # The two skulls differ in SHAPE, not by a factor: in world space the
        # Oblivion head spans z 106.84..126.04 (19.20 tall) and the Skyrim head
        # z 109.33..131.85 (22.52) -- the Skyrim skull reaches 5.4 further down
        # AND 2.1 higher at the crown.  No single scale expresses that, which
        # is why the old ARMOR_PIECE_OFFSETS_PRN['helmet'] affine could never
        # stop the back of the head poking through.
        #
        # Every Prn block hanging on the HEAD bone (helmets, hoods, hair) is
        # instead run through asset_convert.head_fit: each vertex keeps its
        # authored signed distance from the Oblivion skin, measured against
        # the real Skyrim head — so a helmet authored 2 units off the skull
        # stays exactly 2 units off, and the skull can no longer poke through
        # anything that covered it in Oblivion.  Converted hair (`hair=True`)
        # was fitted upstream in hair_pipeline.bake_hair_variant and must not
        # be touched again here.
        #
        # Everything else keeps the previous rules: skinned geometry is exact
        # under the wrap (offsets suppressed), non-head Prn pieces (shields)
        # keep their near-zero PRN offsets, and the FK-tuned constants remain
        # the fallback whenever the fit/field data is unavailable.
        from .body_wrap import wrap_available as _wrap_available
        from .body_wrap import wrap_has_head as _wrap_has_head
        _prn_head_ids = set()
        if _prn_block_ids and not hair:
            _prn_head_ids = _fit_prn_head_blocks(data, _prn_block_ids,
                                                 src_path, race=race)
        if not hair:
            # Skinned helmet/hood geometry: exact under the wrap once the
            # field carries a head surface; FK constants only as fallback.
            _skinned_head_ok = _wrap_has_head(src_path)
            if not _wrap_available(src_path) or (
                    _piece_type == 'helmet' and not _skinned_head_ok):
                _cfg = ARMOR_PIECE_OFFSETS.get(_piece_type, ARMOR_PIECE_OFFSETS['default'])
                apply_armor_offset(data, _cfg, exclude_block_ids=_prn_block_ids)
            _prn_legacy = _prn_block_ids - _prn_head_ids
            if _prn_legacy:
                _cfg_prn = ARMOR_PIECE_OFFSETS_PRN.get(
                    _piece_type, ARMOR_PIECE_OFFSETS_PRN['default'])
                apply_armor_offset(data, _cfg_prn, only_block_ids=_prn_legacy)

        # BEAST RACES GET THEIR OWN MESH, exactly as vanilla ships one
        # (head_fit.BEAST_RACES).  A hood is ONE Oblivion record worn by
        # every race, so unlike hair -- whose EDID names its race -- there
        # is nothing on the record to read: the only way to serve a khajiit
        # and a human from one source is to write a mesh per race and let
        # the per-race ARMA pick.  Mark the NIF here; convert_nif re-runs
        # the WHOLE conversion per race afterwards.
        #
        # It must be a re-run, not a re-fit of the finished mesh: a hood is
        # multi-bone SKINNED geometry (Bip01 Head + Neck + Clavicles), so
        # its head fit happens inside the retarget wrap, not in the rigid
        # Prn pass -- there is no later point where the head verts can be
        # displaced again without redoing the skin solve.
        if _piece_type == 'helmet' and (_prn_head_ids or has_skin):
            stats['_head_gear'] = True

    # Splice Skyrim body geometry AFTER retarget + bone rename so that bone
    # NiNodes in the armor NIF already have Skyrim names to match against.
    if _body_nibs_to_splice:
        # always the _0 fill: the _1 variant is generated afterwards by
        # post-morphing the finished mesh (identical topology contract)
        # Fill partitions get the piece's primary biped slot — the ARMA only
        # renders partitions for slots it claims (a slot-44 pants ARMA culls
        # a partition-32 fill, leaving invisible skin holes).  Same rule as the
        # offset above: a single-slot record states it, a multi-slot one is
        # resolved from where the skinned vertex mass sits.
        _fill_bp = (_authored_bp if _single_slot else _slot_for_offset) or 32
        splice_body_geometry(data, _body_nibs_to_splice, fill_body_part=_fill_bp)

    # Bow bend rig: graft the vanilla 7-bone rig + BGED onto converted bows
    # so limbs bend and the string draws (BowProject.hkx animates the bones).
    # Runs LAST: needs final NiTriShape geometry and the BSFadeNode root with
    # Prn already remapped to WeaponBow.
    if _is_bow_weapon:
        from .bow_rig import add_bow_rig
        for _r in data.roots:
            if _r is not None and _get_prn_bone(_r) == 'WeaponBow':
                stats['bow_rig_shapes'] = add_bow_rig(data, _bow_string_masks)
                break

    # --- NiSkinPartition strip-format safety net ---------------------------
    # A NiSkinPartition can store its geometry as either STRIPS or TRIANGLES.
    # Oblivion writes strips; Skyrim's renderer reads the partition (not the
    # NiTriShapeData) to draw a skinned shape, and a strip-format partition
    # gives it no triangles at all — the shape renders as the red
    # missing-geometry marker.  Census: 678/678 vanilla skin partitions across
    # 350 sampled meshes store TRIANGLES, zero store strips.
    #
    # The strips->triangles conversion in _walk_node rebuilds NiTriShapeData
    # but does NOT touch the partition, and the two regeneration passes above
    # are gated on mesh CATEGORY (creature / worn armor).  Anything else that
    # happens to be skinned — self-skinned clutter (rope, chain, banner,
    # hanging bucket), effect meshes, odd creature parts outside the creature
    # path — kept its Oblivion strip partition and broke.  Found via
    # dungeons\chargen\ropebucket01.nif (red triangle in game); a sweep of 500
    # converted meshes found 93 such partitions across 6+ unrelated meshes, so
    # this is a general class, not one file.
    #
    # Runs after every category-specific pass: those set up bones/bind poses
    # and regenerate correctly on their own, and this leaves their triangle
    # partitions alone.  It only rewrites what is still in strip format.
    for root in data.roots:
        if root is None:
            continue
        for block in list(root.tree()):
            if not isinstance(block, (NifFormat.NiTriShape,
                                      NifFormat.NiTriStrips)):
                continue
            skin = getattr(block, 'skin_instance', None)
            if skin is None or skin.skin_partition is None:
                continue
            if not any(pb.num_strips > 0
                       for pb in skin.skin_partition.skin_partition_blocks):
                continue
            from .skin_retarget import _regen_skin_partition
            geom_name = bytes(block.name).rstrip(b'\x00').decode(
                'latin-1', errors='replace')
            _regen_skin_partition(block, skin, geom_name)
            stats['skin_partitions_destripified'] = \
                stats.get('skin_partitions_destripified', 0) + 1

    # --- BSInvMarker finalize: per-mesh inventory orientation ---------------
    # Weapons and shields sit in Skyrim's normalized attachment frames (Prn
    # node convention / SHIELD attach transform), so the vanilla-derived
    # constant markers set above are already exact — leave them alone.
    # Everything else that can appear in the inventory (armor/clothes _gnd,
    # clutter, books, ingredients, keys, soul gems, ...) is still in an
    # arbitrary Oblivion modeling frame, so a fixed rotation shows a random
    # side.  Compute the rotation from the finished geometry (after root
    # wrapping/furniture shifts) so the side showing the most mesh faces the
    # inventory camera; meshes never viewed in inventory simply carry an
    # inert extra-data block.  See asset_convert/inv_marker.py.
    if not creature:
        from .inv_marker import compute_inv_rotation
        for root in data.roots:
            if root is None or type(root).__name__ != 'BSFadeNode':
                continue
            if _get_prn_bone(root) in _EQUIPPED_PRN_VALUES:
                continue
            marker = None
            for ed in getattr(root, 'extra_data_list', []) or []:
                if isinstance(ed, NifFormat.BSInvMarker):
                    marker = ed
                    break
            if marker is None and _has_skin(data):
                # Skinned non-equipment meshes pose via bones, not node
                # transforms — geometry analysis would misjudge them.
                continue
            rot = compute_inv_rotation(root)
            if rot is None:
                continue
            if marker is None:
                marker = NifFormat.BSInvMarker()
                marker.name = b'INV'
                marker.zoom = 1.0
                root.num_extra_data_list += 1
                root.extra_data_list.update_size()
                root.extra_data_list[root.num_extra_data_list - 1] = marker
            marker.rotation_x, marker.rotation_y, marker.rotation_z = rot
            stats.setdefault('inv_markers_computed', 0)
            stats['inv_markers_computed'] += 1

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


def _append_child(node, child):
    node.num_children += 1
    node.children.update_size()
    node.children[node.num_children - 1] = child


# The pile box is written in OBLIVION havok units, because convert_nif runs
# collision through the usual Oblivion->Skyrim rescale afterwards
# (collision._HAVOK_SCALE = 0.1).  Oblivion havok -> game units is x7, so a
# game-unit extent is divided by 7 here and ends up correct after the x0.1.
# Writing Skyrim-scale values here instead produced a box exactly 0.10x the
# geometry on every axis -- the double-scale that measurement caught.
_PILE_HAVOK_SCALE = 7.0
# SkyrimLayer 15: collides with nothing, still ray-cast for activation --
# exactly what vanilla's ash-pile phantom uses.
_PILE_COLL_LAYER = 15


def _pile_bounds(root):
    """(min, max) of every shape under `root`, in root space, or None."""
    lo = [float('inf')] * 3
    hi = [float('-inf')] * 3
    seen = set()
    for blk in root.tree():
        if not isinstance(blk, NifFormat.NiTriBasedGeom) or id(blk) in seen:
            continue
        seen.add(id(blk))
        data = getattr(blk, 'data', None)
        verts = getattr(data, 'vertices', None) if data is not None else None
        if not verts:
            continue
        s = float(getattr(blk, 'scale', 1.0) or 1.0)
        t = blk.translation
        base = (t.x, t.y, t.z)
        for v in verts:
            for i, c in enumerate((v.x, v.y, v.z)):
                w = c * s + base[i]
                if w < lo[i]:
                    lo[i] = w
                if w > hi[i]:
                    hi[i] = w
    if lo[0] > hi[0]:
        return None
    return lo, hi


def _centre_pile_xy(root):
    """Shift every shape so the pile straddles the origin in X and Y.

    A placed object is dropped AT its origin, and the activation box the
    engine builds comes from the record's OBND about that origin -- so a mesh
    that sits 10 units to one side gives a click target beside the visible
    pile (reported in game).  Z is preserved: that is the authored ground
    drop, not drift.
    """
    b = _pile_bounds(root)
    if b is None:
        return False
    lo, hi = b
    dx = (lo[0] + hi[0]) / 2.0
    dy = (lo[1] + hi[1]) / 2.0
    if abs(dx) < 1e-4 and abs(dy) < 1e-4:
        return False
    seen = set()
    for blk in root.tree():
        if not isinstance(blk, NifFormat.NiTriBasedGeom) or id(blk) in seen:
            continue
        seen.add(id(blk))
        blk.translation.x -= dx
        blk.translation.y -= dy
    return True


def _fit_pile_collision(root):
    """Attach vanilla's ash-pile PHANTOM, box-fitted to `root`'s geometry.

    A pile's activation volume must be a bhkSimpleShapePhantom, NOT a rigid
    body.  Every vanilla ash pile is built the same way (ashpileghost01/
    ashpile01/ashpileghostblack, byte-read from `references/Skyrim Meshes`):
    a child NiNode `Box01` carries bhkSPCollisionObject(flags 129) ->
    bhkSimpleShapePhantom(layer 15 NONCOLLIDABLE) -> bhkTransformShape ->
    bhkBoxShape, with the transform shape lifting the box over the mesh
    (vanilla ghost pile: 64x64x16 game units, raised z 0..16).  A fixed
    bhkRigidBodyT on the same layer 15 was tried first: it shipped with the
    box measured correct (half-extents 10.4/10.4/2 on the pile's own
    geometry) and the pile was still unselectable in game — the crosshair
    pick never sees the body, only the phantom.

    The box covers the FULL geometry extents; the Z half-extent floors at
    8 game units, vanilla's own pick-box thickness, so a flat puddle still
    has a comfortable crosshair target.
    """
    b = _pile_bounds(root)
    if b is None:
        return False
    lo, hi = b
    # Full-size half-extents; Z floored at vanilla's 8-game-unit thickness.
    half = [(hi[i] - lo[i]) / 2.0 for i in range(3)]
    half[2] = max(half[2], 8.0)
    centre = [(hi[i] + lo[i]) / 2.0 for i in range(3)]

    box = NifFormat.bhkBoxShape()
    box.material.material = 0
    box.radius = 1.0                # x0.1 in conversion -> vanilla's 0.1
    box.dimensions.x = half[0] / _PILE_HAVOK_SCALE
    box.dimensions.y = half[1] / _PILE_HAVOK_SCALE
    box.dimensions.z = half[2] / _PILE_HAVOK_SCALE

    # The box's placement rides a bhkTransformShape exactly as vanilla
    # ships it (the phantom itself carries no usable offset).  Translation
    # is in the 4th column; the converter rescales m_14/24/34 by x0.1.
    xf = NifFormat.bhkTransformShape()
    xf.material.material = 0
    xf.unknown_float_1 = 0.1        # radius; not rescaled by the converter
    xf.shape = box
    xf.transform.set_identity()
    xf.transform.m_14 = centre[0] / _PILE_HAVOK_SCALE
    xf.transform.m_24 = centre[1] / _PILE_HAVOK_SCALE
    xf.transform.m_34 = centre[2] / _PILE_HAVOK_SCALE

    phantom = NifFormat.bhkSimpleShapePhantom()
    phantom.shape = xf
    phantom.havok_col_filter.layer = _PILE_COLL_LAYER
    # Float block layout copied from a real Oblivion-authored phantom
    # (ctrigtripwire01.nif): 7 zeros, then three [1,0,0,0,0] rows.
    for i in range(3):
        phantom.unknown_floats_2[i][0] = 1.0

    # Vanilla hangs the phantom on a dedicated child node.
    box_node = NifFormat.NiNode()
    box_node.name = b'Box01'
    box_node.flags = NIF_FLAGS
    co = NifFormat.bhkSPCollisionObject()
    co.flags = 129
    co.target = box_node
    co.body = phantom
    box_node.collision_object = co
    _append_child(root, box_node)
    return True


def extract_death_pile(src_skeleton_path, dst_path, reveal_holders=None,
                       holder_offsets=None):
    """Lift a dissolving creature's AUTHORED death pile into its own NIF.

    An Oblivion ghost's ectoplasm is not a standalone mesh: it is geometry
    parked inside skeleton.nif under an attachment node that the death
    animation REVEALS -- `AttachmentsBip` -> `Bip01 ectoplasm` ->
    `Bip01 ectoplasm:0` (47 verts, textures\\creatures\\ghost\\Ghost03.dds,
    alpha blended).  Those NiVisController reveals cannot survive into a Havok
    clip, so the conversion drops a real placed object instead -- and it must
    be THIS geometry, not Skyrim's DefaultAshPileGhost.

    reveal_holders: node names the death clip turns ON (from the decoded
        clip's vis_tracks -- an authored signal, not a guess).  Only geometry
        under one of these is a pile; the wraith's `Attachments` holds a CLOAK
        and is never revealed, so it is correctly skipped.
    holder_offsets: {holder name: (dx, dy, dz)} the death clip applies to that
        node by its LAST frame.  The pile is authored at body height and the
        clip lowers it to the ground (ghost: z +14.04 -> -56.80, resting at
        world z ~ -3.4), so without this the pile floats at chest height.

    Writes a plain unskinned NIF with each shape baked to its final world
    transform, visible, and stripped of the reveal controllers (which mean
    nothing on a static).  Returns True when something was written.
    """
    reveal_holders = tuple(reveal_holders or ())
    holder_offsets = holder_offsets or {}
    if not reveal_holders:
        return False
    try:
        data = NifFormat.Data()
        with open(src_skeleton_path, 'rb') as f:
            data.read(f)
    except Exception:
        return False

    root = data.roots[0] if data.roots else None
    if root is None:
        return False

    picked = []          # (shape, holder name, holder node)
    seen_shapes = set()
    for want in reveal_holders:
        for blk in root.tree():
            if not isinstance(blk, NifFormat.NiNode):
                continue
            nm = bytes(blk.name).rstrip(b'\x00').decode('latin-1', 'replace')
            if nm != want:
                continue
            for sub in blk.tree():
                # DEDUPE BY IDENTITY: pyffi's tree() yields a block once
                # per reference, and the ghost's ectoplasm shape is
                # referenced twice -- transforming it twice moved the
                # pile by the clip offset TWICE (Z 21.2 instead of 10.6).
                if (isinstance(sub, NifFormat.NiTriBasedGeom)
                        and id(sub) not in seen_shapes):
                    seen_shapes.add(id(sub))
                    picked.append((sub, nm, blk))
    if not picked:
        return False

    out_root = NifFormat.NiNode()
    out_root.name = os.path.basename(dst_path).encode('latin-1')
    out_root.flags = NIF_FLAGS


    # parent-of-holder world transforms, so the clip's holder position can
    # be turned back into world space
    parent_of = {}
    for blk in root.tree():
        if isinstance(blk, NifFormat.NiNode):
            for ch in blk.children:
                if ch is not None:
                    parent_of[id(ch)] = blk

    for shape, holder, holder_node in picked:
        # Where the death clip LEAVES this pile:
        #   final = parent_of_holder_world
        #         + holder_local_on_the_clip's_last_frame
        #         + (shape_rest_world - holder_rest_world)
        # The last term keeps the shape's offset relative to its holder;
        # the middle term is where the clip actually parks the holder.
        # Both source creatures land on the ground this way (ghost pile
        # world Z 6.7..12.9, wraith 1.6..16.6, with Scene Root at 0).
        try:
            tm = shape.get_transform(root)
        except Exception:
            tm = None
        if tm is None:
            _append_child(out_root, shape)
            continue
        # Write the composed WORLD transform straight onto the node rather
        # than round-tripping the matrix: the ghost's ectoplasm shape has a
        # 0.57 SCALE baked into its rotation rows, and set_transform()
        # re-decomposes that, so arithmetic on m_43 did not survive (the
        # pile came out at Z 21.2 instead of 10.6).
        shape.set_transform(tm)          # rotation + scale, world-relative
        dx = dy = dz = 0.0
        if holder in holder_offsets:
            parent = parent_of.get(id(holder_node))
            try:
                pw = (parent.get_transform(root) if parent is not None
                      else None)
                hw = holder_node.get_transform(root)
            except Exception:
                pw = hw = None
            if pw is not None and hw is not None:
                fx, fy, fz = holder_offsets[holder]
                dx = (pw.m_41 + float(fx)) - hw.m_41
                dy = (pw.m_42 + float(fy)) - hw.m_42
                dz = (pw.m_43 + float(fz)) - hw.m_43
        shape.translation.x = tm.m_41 + dx
        shape.translation.y = tm.m_42 + dy
        shape.translation.z = tm.m_43 + dz
        # The source hides this until the death animation reveals it; a placed
        # pile must be visible, and the reveal controllers (transform + geom
        # morpher) have no meaning on a static.
        shape.flags = NIF_FLAGS
        shape.controller = None
        _append_child(out_root, shape)

    # Collision: vanilla's ash-pile PHANTOM, box-fitted to the pile (see
    # _fit_pile_collision).  The holder's own bhkCollisionObject is NOT
    # reusable -- it belongs to the living creature's rig (a limb proxy), so
    # it is the wrong size and in the wrong place: measured on the shipped
    # meshes it covered 38% of the ghost pile's width at 2.3x its height,
    # offset 10 units sideways, and just 4% of the wraith pile's width.
    #
    # Centre the pile on its own origin in X/Y.  Whatever offset survives
    # here is drift inside the creature's rig -- the ghost's from the
    # death clip, the wraith's from the shape's authored rest position --
    # and a placed object must straddle the point AttachAshPile drops it
    # at, which is also the point the engine builds the activation target
    # around.  Z is left alone: that is the ground drop.
    _centre_pile_xy(out_root)

    has_coll = _fit_pile_collision(out_root)

    # BSXFlags: vanilla's own ash piles ship 147.  Bit 1 (Havok) is what
    # tells the engine this static has collision to trace against at all;
    # without a BSXFlags the converted pile is inert even with a phantom.
    if has_coll:
        bsx = NifFormat.BSXFlags()
        bsx.name = b'BSX'
        bsx.integer_data = 2
        out_root.num_extra_data_list += 1
        out_root.extra_data_list.update_size()
        out_root.extra_data_list[out_root.num_extra_data_list - 1] = bsx

    data.roots = [out_root]
    dst_dir = os.path.dirname(dst_path)
    if dst_dir:
        os.makedirs(dst_dir, exist_ok=True)
    with open(dst_path, 'wb') as f:
        data.write(f)
    return True


def source_hidden_attachment_nodes(src_skeleton_path):
    """Attachment nodes the SOURCE skeleton hides at rest.

    Oblivion authors a creature's rest visibility on the attachment NODE:
    the ghost skeleton ships AttachmentsShrink with flags=21 (hidden set)
    because the shrink blob belongs to the death dissolve only, while
    every other attachment is flags=20 (visible).  Conversion normalises
    node flags to NIF_FLAGS and the body merge flattens shapes out of the
    subtree, so the bit has to be carried onto the shape explicitly or a
    LIVING ghost wears its own ectoplasm.

    Census (every Oblivion creature skeleton, 2026-08-26): exactly one
    hidden node exists -- ghost/AttachmentsShrink.  Reading the bit from
    the SKELETON and not from the body parts is deliberate: the parts set
    the same bit on ~1165 ordinary Bip01 bones, where it means nothing.
    """
    out = set()
    try:
        data = NifFormat.Data()
        with open(src_skeleton_path, 'rb') as f:
            data.read(f)
    except Exception:
        return out
    for r in data.roots:
        if r is None:
            continue
        for blk in r.tree():
            if isinstance(blk, NifFormat.NiNode) and \
                    int(getattr(blk, 'flags', 0)) & 1:
                out.add(bytes(blk.name).rstrip(b'\x00').decode(
                    'latin-1', 'replace'))
    return out


def source_attachment_node(src_nif_path):
    """Attachment node of an UNCONVERTED Oblivion creature body part.

    convert_nif strips the `Prn` NiStringExtraData, so the creature
    pipeline reads this from the source file and hands the result to
    merge_creature_body via its `attachments` argument.  Same rule as
    _part_attachment_node: the `Prn` value, else 'SkinAttachment'.
    """
    data = NifFormat.Data()
    with open(src_nif_path, 'rb') as f:
        data.read(f)
    for r in data.roots:
        if r is None:
            continue
        return _part_attachment_node(r)
    return 'SkinAttachment'


def _part_attachment_node(src_root):
    """Name of the node an Oblivion creature body part attaches to.

    `Prn` NiStringExtraData when the part carries one (heademissive ->
    'AttachmentsHead', shrink.nif -> 'AttachmentsShrink'); otherwise the
    part is a plain skin part and Oblivion attaches it under
    'SkinAttachment'.  The ghost/wraith death animation drives
    NiVisControllers on exactly these nodes, so the association has to
    survive the body merge.
    """
    for blk in src_root.tree():
        if isinstance(blk, NifFormat.NiStringExtraData) and \
                bytes(blk.name).rstrip(b'\x00') == b'Prn':
            v = bytes(blk.string_data).rstrip(
                b'\x00').decode('latin-1')
            if v:
                return v
    return 'SkinAttachment'


def _copy_bone_tree(src_node, dst_parent, mapping):
    """Recursively copy the NiNode-only hierarchy under src_node into
    dst_parent (name, flags, full local transform — no collision objects,
    controllers or extra data).  Fills mapping[name] = copied node."""
    for child in src_node.children:
        if not isinstance(child, NifFormat.NiNode):
            continue
        cp = NifFormat.NiNode()
        cp.name = child.name
        cp.flags = child.flags
        cp.set_transform(child.get_transform())
        nm = bytes(child.name).rstrip(b'\x00').decode('latin-1')
        mapping.setdefault(nm, cp)
        _append_child(dst_parent, cp)
        _copy_bone_tree(child, cp, mapping)


def merge_creature_body(part_paths, dst_path, skeleton_path=None,
                        attachments=None, hidden_nodes=None):
    """Merge the converted creature body-part NIFs into ONE skinned NIF.

    Vanilla Skyrim creatures ship the WHOLE animal (body + head + eyes + tail
    + any extra parts) as a single skinned mesh under one root, referenced by
    a SINGLE ARMA on the BODY slot (census: DogRace names only 'BODY'; the
    rabbit.nif root carries every Rabbit* bone NiNode plus 'Bunnyfur01' and
    'Eyes' shapes as siblings).  Oblivion instead splits the creature into a
    skeleton.nif plus one NIF per body part.  Attaching each Oblivion part as
    its own ARMA on an extra biped slot does NOT work — the engine renders
    only the BODY-slot ARMA for a creature, so the head/eyes silently vanish.

    Layout (mirrors vanilla): a fresh NiNode root carrying the FULL bone
    hierarchy copied from the converted skeleton.nif (correct names incl.
    'NPC Root [Root]', local transforms, no collision), plus every part's
    shapes as siblings, each shape's skin bones re-pointed by NAME at the
    rig copy.  No part is a "base": Oblivion body-part NIFs only embed the
    bone SUBSET they are skinned to (a goblin hand embeds 14 finger bones,
    the chest only 13 spine bones), so grafting onto any single part leaves
    the other parts' bones as identity placeholders at the origin — the
    mangled-goblin bug.  A skin bone missing from the skeleton (e.g. a
    part-local control node) is copied from the part's own tree with its
    true world transform.

    part_paths: already-converted .nif paths (Skyrim version).
    skeleton_path: the creature's converted 'character assets/skeleton.nif'.
    attachments: {converted part path: attachment node name} read from
    the SOURCE parts' `Prn` before conversion strips it (see
    _part_attachment_node).  Shapes are parented under that node so a
    death animation's NiVisController -> bone-scale collapse can hide
    them; without it every shape is a root sibling and nothing hides.
    Writes the merged NIF to dst_path.  Returns {'grafted': int,
    'shapes': int, 'bones': int}.
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

    root = NifFormat.NiNode()
    root.name = os.path.basename(dst_path).encode('latin-1')
    root.flags = NIF_FLAGS

    bones = {}
    if skeleton_path and os.path.exists(skeleton_path):
        skel = NifFormat.Data()
        with open(skeleton_path, 'rb') as f:
            skel.read(f)
        for r in skel.roots:
            if isinstance(r, NifFormat.NiNode):
                _copy_bone_tree(r, root, bones)

    grafted = 0
    for _path, d in datas:
        for src_root in d.roots:
            if src_root is None:
                continue
            # The part's attachment node: Oblivion names it in a `Prn`
            # NiStringExtraData (head/hands/shrink blob) and leaves it
            # off the plain SKIN parts (body), which the engine attaches
            # under 'SkinAttachment'.  We keep the association because a
            # creature death animation HIDES these nodes -- see
            # kf_decode's NiVisController handling, which converts hiding
            # to a bone-scale collapse.  That only reaches geometry
            # actually hanging off the bone, and a flat merge (every
            # shape a sibling at the root) left the ghost's body fully
            # visible throughout its dissolve.
            prn = (attachments or {}).get(_path) or \
                _part_attachment_node(src_root)
            for shape in _shape_blocks(src_root):
                si = shape.skin_instance
                if si is not None:
                    for bi, bone in enumerate(si.bones):
                        if bone is None:
                            continue
                        nm = bytes(bone.name).rstrip(b'\x00').decode('latin-1')
                        tgt = bones.get(nm)
                        if tgt is None:
                            # part-local bone the rig lacks: copy it with its
                            # true world transform (root is identity)
                            tgt = NifFormat.NiNode()
                            tgt.name = bone.name
                            tgt.flags = bone.flags
                            tgt.set_transform(bone.get_transform(src_root))
                            _append_child(root, tgt)
                            bones[nm] = tgt
                        si.bones[bi] = tgt
                    if si.skeleton_root is not None:
                        si.skeleton_root = root
                # ALWAYS parent at the root.  Do NOT hang a skinned shape
                # off its attachment bone: the engine applies the shape's
                # parent chain ON TOP of the skinned result, so a body
                # under `SkinAttachment` (a child of the animated
                # `Bip01 NonAccum`) gets that animation twice and leaves
                # the view entirely -- reported in game 2026-08-26 as the
                # ghost losing its whole body while still alive, with only
                # the skeleton-owned smoke left.  Vanilla agrees: the
                # working dog merge keeps `WolfBody` at the root, and
                # Oblivion's own part NIFs are standalone roots the engine
                # attaches at runtime, never children inside a mesh file.
                #
                # The attachment node still matters for REST visibility:
                # carry the authored hidden bit onto the shape (the shrink
                # blob must not show on a living ghost) without moving it.
                if prn in (hidden_nodes or ()):
                    shape.flags = int(shape.flags) | 1
                _append_child(root, shape)
                grafted += 1

    # SSE renders a skinned shape by memcpy'ing one 3x4 matrix per skin bone
    # into a fixed 80-matrix buffer (shadow pass) — >80 bones = CTD (imp: 85,
    # in-game verified 2026-07-10). Merge the lightest leaf bones into their
    # parents; must run HERE (after grafting) because only the merged rig has
    # bone hierarchy — Oblivion part NIFs store bones flat. Bone merging
    # invalidates the parts' NiSkinPartitions, so regenerate them.
    from .skin_retarget import (_regen_skin_partition,
                                merge_oversized_skin_bones)
    if merge_oversized_skin_bones(root):
        for shape in _shape_blocks(root):
            si = shape.skin_instance
            if si is not None:
                _regen_skin_partition(
                    shape, si,
                    bytes(shape.name).rstrip(b'\x00').decode('latin-1',
                                                             'replace'))

    # reuse the first part's Data for version/header fields
    out_data = datas[0][1]
    out_data.roots = [root]

    dst_dir = os.path.dirname(dst_path)
    if dst_dir:
        os.makedirs(dst_dir, exist_ok=True)
    with open(dst_path, 'wb') as f:
        out_data.write(f)
    return {'grafted': grafted, 'shapes': len(_shape_blocks(root)),
            'bones': len(bones)}


def convert_nif(src_path, dst_path, *, fix_textures=True, remap_skeleton=None,
                src_meshes_dir=None, creature=False, wearable_plan=None,
                parallax=False, textures_only=False, tex_fallback=(),
                hair=False, race=None):
    """Convert a single Oblivion NIF to Skyrim format.

    Already-Skyrim versions are copied to dst_path unchanged.
    Unsupported/incompatible versions are skipped (not written to dst_path).
    Returns a result dict compatible with batch_convert's _update() expectations.

    src_meshes_dir: root of the source mesh tree (passed through by
    batch_convert), used to key a NIF against the wearable plan.

    wearable_plan: mapping from asset_convert.wearable_plan.build_plan, naming
    the _0/_1/plain variants each armor/clothing mesh is referenced as.  None
    disables weight-variant output entirely.

    parallax: carry Oblivion's parallax across (opt-in — see _apply_parallax).

    textures_only: read and analyse every mesh, write NONE of them.  The
    height maps still get built, because the decision to build one needs the
    mesh's own APPLY_HILIGHT2 flag — see the mode's rationale in batch_convert.

    hair: this NIF is an Oblivion hair head part (asset_convert.hair_pipeline).
    Hair lives outside meshes\armor and no ARMO/CLOT record names it, so the
    wearable plan cannot mark it worn — but it is rigid Prn-attached geometry
    that needs exactly the same treatment as a helmet: a dismember skin bound
    to the head bone in slot 131.  Without this the mesh ships unskinned and
    also picks up a meaningless BSInvMarker (hair is never an inventory item).

    race: fit head gear to a BEAST race's skull instead of the shared human
    one (head_fit.BEAST_RACES).  Set only by the beast-variant pass below,
    which re-runs this conversion once per race; None is the normal path.
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
        'textures': set(),         # texture paths this mesh references
        'overlay_diffuses': set(), # of those, the APPLY_HILIGHT2 overlays
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

    if (data.version, data.user_version_2) in _SKYRIM_VERSIONS:
        # Already Skyrim — copy as-is.  Nothing rewrote its texture paths, so
        # scan the bytes for them; the prune must not drop what this still uses.
        dst_dir = os.path.dirname(dst_path)
        if dst_dir:
            os.makedirs(dst_dir, exist_ok=True)
        shutil.copy2(src_path, dst_path)
        with open(src_path, 'rb') as f:
            _harvest_texture_bytes(f.read(), result['textures'])
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

    # Standalone animation files (e.g. creatures/*/idleanims/*.nif) hold only a
    # NiControllerSequence — no scene graph at all.  There is nothing to convert
    # and every pass below assumes a NiAVObject root, so skip rather than crash.
    if not any(isinstance(r, NifFormat.NiAVObject) for r in data.roots):
        result['error'] = 'NOGEO'
        return result

    # Does the plugin itself wear this mesh?  Asked before the conversion so the
    # armor rules (dismember skin, NiNode root, skeleton retarget) apply to gear
    # filed outside meshes\armor and meshes\clothes.
    _worn = bool(hair)
    # Biped bit 1 (Hair) — the same authored slot a helmet-bearing record
    # would carry, so the converter resolves body part 131 without guessing.
    _biped_flags = 0x02 if hair else 0
    if wearable_plan is not None and src_meshes_dir is not None and not creature             and not hair:
        from . import wearable_plan as _wp
        _worn = _wp.is_worn(wearable_plan, src_path, src_meshes_dir)
        # What the plugin says this mesh IS (head/body/hands/feet/shield), so
        # the converter never has to guess the slot from the filename.
        _biped_flags = _wp.biped_flags_for(wearable_plan, src_path,
                                           src_meshes_dir)

    stats = _convert_nif(data, fix_textures=fix_textures,
                         src_path=str(src_path), creature=creature,
                         worn=_worn, parallax=parallax,
                         biped_flags=_biped_flags,
                         tex_fallback=tex_fallback, hair=hair, race=race)

    # Graft the converted Oblivion flame NIF under FlameNode* markers (candle
    # flame / torch fire) — full conversion of Oblivion's own flame visuals,
    # not a Skyrim MPS substitution.  Runs BEFORE the atlas build so the
    # flame's flip-book atlas jobs (merged into stats) are executed below.
    for root in data.roots:
        if root is not None:
            convert_flame_nodes(root, src_path, _convert_nif, stats)

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

    # Write the BC4 height maps planned by _apply_parallax.  Same reason as the
    # atlas above: only convert_nif knows the output tree.  Each map is written
    # once — the file test skips the other meshes sharing that diffuse, and
    # 2359 flagged shapes share just 163 textures, so nearly all of them skip.
    _height_jobs = stats.pop('_parallax_maps', {})
    if _height_jobs:
        from . import parallax as _parallax
        _dstn = str(dst_path).replace('/', os.sep).replace('\\', os.sep)
        _k = os.sep + 'meshes' + os.sep
        _i = _dstn.lower().rfind(_k)
        if _i >= 0:
            _out_root = _dstn[:_i] + os.sep
            for _job in _height_jobs.values():
                _out = _out_root + _job['height_rel'].replace('\\', os.sep)
                if not os.path.isfile(_out):
                    _parallax.build_height_map(_job['src'], _out)

    # Animated objects (activators/doors/levers): Skyrim will not drive an
    # in-NIF NiControllerSequence from ObjectReference.PlayAnimation() — that
    # call needs an animation graph manager, which only exists when the root
    # carries a BSBehaviorGraphExtraData naming an hkx project.  Generate the
    # 4-file project/character/skeleton/behavior tree beside the mesh, with one
    # BGSGamebryoSequenceGenerator state per surviving sequence, and point the
    # BGED at it.  Runs AFTER _convert_nif so stripped/empty sequences (which
    # would give PlayAnimation a dead state) are already gone, and before the
    # write so the BGED ships in the file.  See asset_convert/hkx_animobject.py.
    _seq_names = collect_sequence_names(data)
    if _seq_names and not textures_only:
        # A graph-bound mesh must ship NO empty text keys: the generator
        # strchr()s every key value on activation and an empty NiString loads
        # as a NULL pointer (see _strip_empty_text_keys — the Spiddal Stick /
        # Harrada crash).  Stripped BEFORE project generation so the rule
        # holds even if hkxcmd later fails and the BGED is skipped: a
        # graph-less mesh with fewer dead keys loses nothing.
        _stripped = _strip_empty_text_keys(data)
        if _stripped:
            stats['empty_text_keys_stripped'] = _stripped
        _dstn = str(dst_path).replace('/', os.sep).replace('\\', os.sep)
        _k = os.sep + 'meshes' + os.sep
        _i = _dstn.lower().rfind(_k)
        if _i >= 0:
            _meshes_root = _dstn[:_i + len(_k)]
            _model_rel = _dstn[_i + len(_k):]
            try:
                from .hkx_animobject import generate_animobject_project
                _bged = generate_animobject_project(
                    _meshes_root, _model_rel, _seq_names)
                if _bged and _add_animobject_bged(data, _bged):
                    result['animobject_graph'] = _bged
                    stats['animobject_sequences'] = len(_seq_names)
            except Exception as _e:
                # A missing/failing hkxcmd must not lose the whole mesh: the
                # object still converts and renders, it just stays unanimated.
                result['animobject_error'] = str(_e)

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

    # Record which textures this mesh ends up referencing.  Collected here, off
    # the finished blocks, so the pipeline can drop every texture nothing ships
    # a reference to without re-reading the whole output tree afterwards.
    _harvest_textures(data, result['textures'])

    # Diffuses the source authored as APPLY_HILIGHT2 detail overlays, for the
    # LOD stage (see the note beside _APPLY_HILIGHT2 in _process_geometry).
    # Harvested BEFORE the textures_only return: that mode still analyses every
    # shape, so the LOD manifest it feeds must be complete either way.
    result['overlay_diffuses'] = stats.get('overlay_diffuses', set())

    if textures_only:
        # Everything above still ran: the shape was read, its APPLY_HILIGHT2
        # flag consulted, its diffuse measured, and the BC4 height map written.
        # Only the mesh itself is not emitted — PGPatcher does that side, in
        # the player's real load order where it can see every plugin.
        return _finish_result(result, stats)

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

    # Biped wearables get vanilla-style _0/_1 weight-slider variants: the
    # ARMA records for body/hands/feet gear reference <name>_1.nif with the
    # weight slider enabled and the engine lerps the pair per-vertex.  That
    # lerp REQUIRES identical topology, so the _1 file is NEVER a second
    # independent conversion (the body splice clips differently and the
    # pair explodes at intermediate slider values) — it is the finished
    # weight-0 mesh post-morphed by the fitted _0->_1 Skyrim body morph
    # (body_wrap.morph_converted_to_weight1; rigid PRN blocks untouched).
    #
    # Which variants exist is decided by the plugin, not by the path: gear
    # without the slider (helmets, shields, rings) is referenced as the plain
    # mesh and gains nothing from a _0/_1 pair, while slider gear never uses
    # the plain mesh unless it doubles as a ground model.  wearable_plan
    # derives that from the same records the importer writes, so we only emit
    # files something actually references.
    #
    # Which mesh is a wearable is the plan's call, not the folder's:
    # variants_for returns BASE for anything no ARMO/CLOT record names, so
    # non-wearables keep their plain conversion either way, while gear filed
    # outside meshes\armor finally gets the _0/_1 pair its ARMA asks for.
    _srcl = str(src_path).lower().replace('\\', '/')
    # A beast variant is a copy of ONE mesh, never a weight pair: head gear
    # has the slider off, and _0/_1 would collide with the race suffix.
    _wearable = (not creature and race is None
                 and not _is_ground_model(_srcl.rsplit('/', 1)[-1]))
    if _wearable and wearable_plan is not None:
        from . import wearable_plan as _wp
        want = _wp.variants_for(wearable_plan, src_path, src_meshes_dir)
        _root, _ext = os.path.splitext(str(dst_path))

        # The plain mesh was already written above; drop it if nothing uses it.
        if not want & _wp.BASE:
            try:
                os.remove(dst_path)
            except OSError:
                pass

        if want & _wp.W0:
            with open(_root + '_0' + _ext, 'wb') as f:
                f.write(buf.getvalue())
        if want & _wp.W1:
            w1_bytes = None
            try:
                from .body_wrap import morph_converted_to_weight1
                _female = '/f/' in _srcl
                if morph_converted_to_weight1(data, _female):
                    buf1 = _io.BytesIO()
                    data.write(buf1)
                    w1_bytes = buf1.getvalue()
            except Exception:
                w1_bytes = None
            # no morph (PRN-only piece / no field) or failure: identical copy so
            # the ARMA _1 path always resolves
            with open(_root + '_1' + _ext, 'wb') as f:
                f.write(w1_bytes if w1_bytes is not None else buf.getvalue())

    # BEAST-RACE HEAD GEAR VARIANTS.  The mesh written above is fitted to the
    # SHARED HUMAN skull; on a khajiit or argonian that same geometry sits
    # inside the head.  Measured on blades/m/helmet against the real beast
    # heads: the human-fitted mesh puts 447 verts inside the khajiit skull
    # (max depth 2.76) and 409 inside the argonian one (max 2.81), against 44
    # for the human mesh on the human head; the per-race fits cut that to 56
    # and 20 -- see head_fit.BEAST_RACES for the field measurements.
    #
    # Vanilla Skyrim's own answer is a mesh per race family selected by a
    # per-race ARMA, so we write <name>_khajiit.nif / <name>_argonian.nif here
    # and tes5_import.record_types.equipment emits the ARMA naming each.
    if stats.get('_head_gear') and not creature and not hair and race is None:
        _write_beast_head_variants(
            src_path, dst_path,
            fix_textures=fix_textures, src_meshes_dir=src_meshes_dir,
            wearable_plan=wearable_plan, parallax=parallax)

    return _finish_result(result, stats)


def _write_beast_head_variants(src_path, dst_path, *, fix_textures,
                               src_meshes_dir, wearable_plan, parallax):
    """Write the per-beast-race copies of a head-gear NIF.

    The whole conversion is RE-RUN per race from the source file rather than
    the finished mesh being re-fitted.  A hood is multi-bone SKINNED geometry
    (Bip01 Head + Neck + Clavicles), so its head fit happens inside the
    retarget wrap -- there is no later point at which the head verts can be
    displaced again without redoing the skin solve.  Re-reading also keeps
    each variant a FIRST fit through its race's field, never a second
    displacement stacked on the human result.

    A variant that fails for any reason is simply not written: the ARMA for it
    then points at a missing mesh, which the engine falls back from to the
    default armature -- the pre-existing behaviour, never worse than it.
    """
    from . import head_fit
    female = '/f/' in str(src_path).replace(chr(92), '/').lower()
    races = head_fit.beast_races_available(female)
    if not races:
        return

    root, ext = os.path.splitext(str(dst_path))
    for race in races:
        out = root + head_fit.beast_variant_suffix(race) + ext
        try:
            convert_nif(src_path, out, fix_textures=fix_textures,
                        src_meshes_dir=src_meshes_dir,
                        wearable_plan=wearable_plan, parallax=parallax,
                        race=race)
        except Exception:
            try:
                if os.path.isfile(out):
                    os.remove(out)
            except OSError:
                pass


def _finish_result(result, stats):
    """Roll `stats` up into the worker's result dict.

    Its own function because --textures-only returns before the mesh is ever
    written, and both exits owe batch_convert the same accounting.
    """
    result['converted'] = True
    result['strips_fixed'] = stats['strips_fixed'] > 0
    result['properties_converted'] = stats['properties_converted'] > 0
    result['root_converted'] = stats['root_converted'] > 0
    result['root_rotation_baked'] = stats['root_rotation_baked'] > 0
    result['version_upgraded'] = True
    result['bones_remapped'] = stats['bones_remapped'] > 0
    result['textures_fixed'] = stats['properties_converted'] > 0  # proxy: every property conversion rewrites textures
    # Parallax accounting.  Carried up per CATEGORY, because "skipped" on its
    # own sends the next person back to all 163 flagged textures with no lead —
    # over half of them legitimately have no height data to carry.
    # `spec_` rides in the same bucket: both are per-category counters merged
    # with Counter.update(), and both answer "why was this shape left alone".
    _px = {k: v for k, v in stats.items()
           if k.startswith('parallax_') or k.startswith('spec_')
           or k.startswith('glow_')}
    if _px:
        result['parallax'] = _px
    # Carried separately from the counters above: this one is a SET of texture
    # paths, and `parallax` is merged with Counter.update().
    _au = stats.get('_alpha_opacity_diffuse')
    if _au:
        result['alpha_opacity_diffuse'] = _au
    return result


def _matches_subdir_filter(rel_parts, subdir_filter) -> bool:
    """Whether a relative mesh path is under one selected path prefix."""
    if subdir_filter is None:
        return True
    rel = tuple(str(part).lower() for part in rel_parts)
    for selected in subdir_filter:
        prefix = tuple(part.lower() for part in re.split(
            r'[\\/]+', str(selected)) if part)
        if prefix and rel[:len(prefix)] == prefix:
            return True
    return False


def batch_convert(mesh_dir, output_dir, *, fix_textures=True,
                  remap_skeleton=None, subdir_filter=None, wearable_plan=None,
                  parallax=False, textures_only=False):
    """Convert all NIF files in mesh_dir to Skyrim format, writing to output_dir.

    Skip reason codes:
      VER  — unsupported NIF version (too old / unrecognised)
      RD   — read failure (corrupt, truncated, unknown block types)
      WR   — write failure (version-incompatible blocks, e.g. NiGeomMorpherController)

    textures_only: analyse every mesh, emit none of them.  For the parallax
    path there is a better mesh patcher than us — **PGPatcher** (ParallaxGen)
    runs over the player's finished load order, so it sees every plugin at
    once and can also upgrade a shape to ENB's complex-material system, which
    Community Shaders reads too.  Our job then reduces to the one thing it
    cannot do: recover the height field out of Oblivion's diffuse alpha.

    The meshes still have to be READ.  Whether a diffuse carries a height map
    is only knowable from the shape's own APPLY_HILIGHT2 flag — the authored
    intent — so the analysis is the same and only the emit is dropped.

    Args:
        subdir_filter: If provided, relative folder prefixes to include (e.g.
                       ['architecture', 'morro/d']). None means everything.
        wearable_plan: Mapping from asset_convert.wearable_plan.build_plan,
                       naming which _0/_1/plain variants of each armor and
                       clothing mesh the plugin references.  None writes no
                       weight variants at all.
        parallax:      Carry Oblivion's parallax across as Skyrim height maps.
                       OFF by default — the result needs Community Shaders or
                       ENB and renders wrong under vanilla SSE.

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
        elif not _matches_subdir_filter(rel_parts, subdir_filter):
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
        # Union of the textures every written mesh references — the pipeline
        # prunes the texture tree against this.
        'textures_used': set(),
        # Per-category parallax accounting, empty unless parallax=True.
        'parallax': _collections.Counter(),
        # Diffuse textures some shape reads as opacity — see the alpha branch
        # in _process_geometry.  Their alpha is never stripped to BC1.
        'alpha_opacity_diffuse': set(),
        # Of those, the ones the source authored as APPLY_HILIGHT2 detail
        # overlays: their alpha is a blend weight, not transparency, and object
        # LOD must not read it as opacity.
        'overlay_diffuses': set(),
    }

    # Collect (rel_path, reason) for every skipped file
    skipped_list = []

    workers = _WORKER_COUNT
    # Resolved once here, not per shape: reading _HEADER.txt in every
    # worker for every mesh would be thousands of redundant opens.
    _tex_fallback = master_texture_roots(mesh_dir)
    print(f'Found {total} NIF files in {mesh_dir} (workers={workers})')
    if _tex_fallback:
        print(f'  Texture fallback: {len(_tex_fallback)} master tree(s) '
              f'-- {", ".join(os.path.basename(os.path.dirname(r)) for r in _tex_fallback)}')
    if skipped_by_path:
        print(f'  Skipped {skipped_by_path} files matching SKIP_PATHS: {sorted(SKIP_PATHS)}')

    if total == 0:
        return stats

    work_args = [
        (str(nif_file), str(out_base / nif_file.relative_to(mesh_path)),
         fix_textures, remap_skeleton, str(mesh_path), wearable_plan,
         parallax, textures_only, _tex_fallback)
        for nif_file in nif_files
    ]

    def _update(nif_str, r):
        stats['warn_counts'].update(r.get('warn_counts', {}))
        stats['textures_used'].update(r.get('textures', ()))
        stats['parallax'].update(r.get('parallax') or {})
        stats['alpha_opacity_diffuse'].update(
            r.get('alpha_opacity_diffuse') or ())
        stats['overlay_diffuses'].update(r.get('overlay_diffuses', ()))
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

    if parallax:
        px = stats['parallax']
        built = px.get('parallax_shapes', 0)
        print(f'\nParallax: {built} shapes converted to the heightmap shader'
              f' (+{px.get("parallax_vertex_colors_added", 0)} given white '
              f'vertex colors)')
        skipped = sorted((k, v) for k, v in px.items()
                         if k.startswith('parallax_skipped_')
                         or k == 'parallax_texture_unresolved')
        for cat, cnt in skipped:
            # Flagged by the author but nothing to carry.  Not a failure:
            # Oblivion renders no parallax there either.
            print(f'  left flat, {cat[len("parallax_"):]}: {cnt} shapes')

    _glow = {k[len('glow_'):]: v for k, v in stats['parallax'].items()
             if k.startswith('glow_')}
    if _glow:
        print(f"\nGlow: {_glow.get('applied', 0)} shapes carry Oblivion's "
              f'authored glow map into slot {GLOW_SLOT} (shader type '
              f'{SHADER_TYPE_GLOWMAP})')
        if _glow.get('unresolved'):
            print(f"  {_glow['unresolved']} named a glow texture that does "
                  f'not exist -- left unlit rather than guessed')
        _pg = stats['parallax'].get('parallax_skipped_glow', 0)
        if _pg:
            print(f'  {_pg} of them also asked for parallax; glow wins '
                  f'(one shader type, and the glow map is authored while the '
                  f'height map is derived)')

    _spec = {k[len('spec_'):]: v for k, v in stats['parallax'].items()
             if k.startswith('spec_')}
    if _spec:
        _on = _spec.get('mask', 0)
        # Only the VERDICT categories form the base.  `normal_from_base` and
        # `normal_defaulted` ride the same `spec_` bucket for plumbing reasons
        # but describe where the normal came FROM, not what its alpha holds --
        # counting them diluted the share from 92.9% to a meaningless 86.2%.
        _verdicts = ('mask', 'no_alpha', 'flat', 'binary', 'missing_normal')
        _tot = sum(_spec.get(k, 0) for k in _verdicts)
        print(f'\nSpecular: strength {_SPEC_STRENGTH} on every shape; '
              f'{_on} of {_tot} ({_on * 100.0 / max(1, _tot):.1f}%) modulate '
              f"it with an AUTHORED mask in the normal map's alpha")
        for _k in ('no_alpha', 'flat', 'binary', 'missing_normal'):
            if _spec.get(_k):
                print(f'  {_k}: {_spec[_k]} shapes -> the texture stage bakes '
                      f'a constant mask instead')
        if _spec.get('normal_from_base'):
            print(f"  normal shared with the base name: "
                  f"{_spec['normal_from_base']} shapes (a colour variant "
                  f"reuses its base's _n, the way the artists authored it)")
        if _spec.get('normal_defaulted'):
            print(f"  normal map absent: {_spec['normal_defaulted']} shapes "
                  f'-> {_DEFAULT_NORMAL_TEXTURE} (a fabricated _n path would '
                  f'only dangle; vanilla never ships an empty slot 1)')

    # plain ASCII: cp1252 consoles/pipes choke on the arrow character
    print(f'\nDetailed stats: Strips->Shape={stats["strips"]}, '
          f'Properties={stats["properties"]}, '
          f'Roots={stats["roots"]}, Rotations baked={stats["rotations"]}')

    return stats


def _batch_worker(args):
    (nif_str, out_path, fix_textures, remap_skeleton, src_meshes_dir,
     wearable_plan, parallax, textures_only, tex_fallback) = args
    global _worker_warn_log
    _worker_warn_log = []
    try:
        r = convert_nif(nif_str, out_path,
                        fix_textures=fix_textures, remap_skeleton=remap_skeleton,
                        src_meshes_dir=src_meshes_dir,
                        wearable_plan=wearable_plan, parallax=parallax,
                        textures_only=textures_only,
                        tex_fallback=tex_fallback)
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
