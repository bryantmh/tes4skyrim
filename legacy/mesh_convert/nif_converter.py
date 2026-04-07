"""Convert Oblivion NIF meshes to Skyrim SE format using pure binary parsing.

This module is the main entry point for NIF conversion. It uses nif_reader.py
to parse Oblivion NIFs and nif_writer.py to produce Skyrim output.

Conversion steps performed:
  1. Parse Oblivion NIF via nif_reader.parse_oblivion_nif()
  2. Transform:
       - NiTriStrips  -> NiTriShape  (triangulate strips)
       - NiTriStripsData -> NiTriShapeData
       - NiTexturingProperty + NiMaterialProperty -> BSLightingShaderProperty +
         BSShaderTextureSet  (Skyrim shader system)
       - NiAlphaProperty -> pass-through
       - NiNode root -> BSFadeNode
       - Texture paths: prepend tes4\\ prefix
       - Bone names: Oblivion Bip01 -> Skyrim NPC skeleton names
  3. Write Skyrim NIF via nif_writer.build_skyrim_nif()
"""
from __future__ import annotations

import os
import re
import struct
from pathlib import Path
from typing import Any

from .nif_reader import (
    NifBlock,
    OblNif,
    OBV_VERSION,
    OBV_VERSION_2,
    SKY_VERSION,
    parse_oblivion_nif,
)
from .nif_writer import build_skyrim_nif

# Re-export for backwards compatibility with tests
_OBV_VERSION = OBV_VERSION
_OBV_VERSION_2 = OBV_VERSION_2
_SKY_VERSION = SKY_VERSION
_SKY_UV = 12
_SKY_UV2 = 83

# ---------------------------------------------------------------------------
# Bone name mapping: Oblivion (Bip01) -> Skyrim (NPC) skeleton
# ---------------------------------------------------------------------------
BONE_MAP: dict[str, str] = {
    'Bip01':                   'NPC Root [Root]',
    'Bip01 NonAccum':          'NPC COM [COM ]',
    'Bip01 Pelvis':            'NPC Pelvis [Pelv]',
    'Bip01 Spine':             'NPC Spine [Spn0]',
    'Bip01 Spine1':            'NPC Spine1 [Spn1]',
    'Bip01 Spine2':            'NPC Spine2 [Spn2]',
    'Bip01 Neck':              'NPC Neck [Neck]',
    'Bip01 Neck1':             'NPC Neck [Neck]',
    'Bip01 Head':              'NPC Head [Head]',
    'Bip01 L Clavicle':        'NPC L Clavicle [LClv]',
    'Bip01 L UpperArm':        'NPC L UpperArm [LUar]',
    'Bip01 L UpperArmTwist':   'NPC L UpperarmTwist1 [LUt1]',
    'Bip01 L Forearm':         'NPC L Forearm [LLar]',
    'Bip01 L ForearmTwist':    'NPC L ForearmTwist1 [LLt1]',
    'Bip01 L Hand':            'NPC L Hand [LHnd]',
    'Bip01 L Finger0':         'NPC L Finger00 [LF00]',
    'Bip01 L Finger01':        'NPC L Finger01 [LF01]',
    'Bip01 L Finger02':        'NPC L Finger02 [LF02]',
    'Bip01 L Finger1':         'NPC L Finger10 [LF10]',
    'Bip01 L Finger11':        'NPC L Finger11 [LF11]',
    'Bip01 L Finger12':        'NPC L Finger12 [LF12]',
    'Bip01 L Finger2':         'NPC L Finger20 [LF20]',
    'Bip01 L Finger21':        'NPC L Finger21 [LF21]',
    'Bip01 L Finger22':        'NPC L Finger22 [LF22]',
    'Bip01 L Finger3':         'NPC L Finger30 [LF30]',
    'Bip01 L Finger31':        'NPC L Finger31 [LF31]',
    'Bip01 L Finger32':        'NPC L Finger32 [LF32]',
    'Bip01 L Finger4':         'NPC L Finger40 [LF40]',
    'Bip01 L Finger41':        'NPC L Finger41 [LF41]',
    'Bip01 L Finger42':        'NPC L Finger42 [LF42]',
    'Bip01 R Clavicle':        'NPC R Clavicle [RClv]',
    'Bip01 R UpperArm':        'NPC R UpperArm [RUar]',
    'Bip01 R UpperArmTwist':   'NPC R UpperarmTwist1 [RUt1]',
    'Bip01 R Forearm':         'NPC R Forearm [RLar]',
    'Bip01 R ForearmTwist':    'NPC R ForearmTwist1 [RLt1]',
    'Bip01 R Hand':            'NPC R Hand [RHnd]',
    'Bip01 R Finger0':         'NPC R Finger00 [RF00]',
    'Bip01 R Finger01':        'NPC R Finger01 [RF01]',
    'Bip01 R Finger02':        'NPC R Finger02 [RF02]',
    'Bip01 R Finger1':         'NPC R Finger10 [RF10]',
    'Bip01 R Finger11':        'NPC R Finger11 [RF11]',
    'Bip01 R Finger12':        'NPC R Finger12 [RF12]',
    'Bip01 R Finger2':         'NPC R Finger20 [RF20]',
    'Bip01 R Finger21':        'NPC R Finger21 [RF21]',
    'Bip01 R Finger22':        'NPC R Finger22 [RF22]',
    'Bip01 R Finger3':         'NPC R Finger30 [RF30]',
    'Bip01 R Finger31':        'NPC R Finger31 [RF31]',
    'Bip01 R Finger32':        'NPC R Finger32 [RF32]',
    'Bip01 R Finger4':         'NPC R Finger40 [RF40]',
    'Bip01 R Finger41':        'NPC R Finger41 [RF41]',
    'Bip01 R Finger42':        'NPC R Finger42 [RF42]',
    'Bip01 L Thigh':           'NPC L Thigh [LThg]',
    'Bip01 L Calf':            'NPC L Calf [LClf]',
    'Bip01 L Foot':            'NPC L Foot [Lft ]',
    'Bip01 L Toe0':            'NPC L Toe0 [LToe]',
    'Bip01 R Thigh':           'NPC R Thigh [RThg]',
    'Bip01 R Calf':            'NPC R Calf [RClf]',
    'Bip01 R Foot':            'NPC R Foot [Rft ]',
    'Bip01 R Toe0':            'NPC R Toe0 [RToe]',
    'Bip01 L Weapon':          'WeaponLeft',
    'Bip01 R Weapon':          'WeaponRight',
    'Weapon':                   'WeaponRight',
    'BackWeapon':               'WeaponBack',
    'SideWeapon':               'WeaponSword',
    'Torch':                    'NPC L MagicNode [LMag]',
    'Bip01 L Shield':          'SHIELD',
    'Shield':                   'SHIELD',
    'Bip01 Quiver':            'WeaponBack',
    'Quiver':                   'WeaponBack',
    'Bip01 Spine0':            'NPC Spine [Spn0]',
}

# Paths that indicate skinned/worn meshes requiring bone remapping
_SKINNED_PATH_PATTERN = re.compile(
    r'(^|[\\/])(armor|clothes|characters)[\\/]', re.IGNORECASE
)


def _is_skinned_mesh_path(filepath: str) -> bool:
    """Return True if the path suggests a skinned mesh needing bone remapping."""
    return bool(_SKINNED_PATH_PATTERN.search(str(filepath)))


def _rewrite_texture_path(path: str) -> str:
    """Prepend tes4\\ after the top-level folder in a texture path.

    'textures\\armor\\iron\\cuirass.dds'  ->  'textures\\tes4\\armor\\iron\\cuirass.dds'
    Already-prefixed or empty paths are returned unchanged.
    """
    if not path:
        return path
    p = path.replace('/', '\\')
    if 'tes4\\' in p.lower():
        return p
    low = p.lower()
    if low.startswith('textures\\'):
        return 'textures\\tes4\\' + p[9:]
    return p


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------

def _resolve_texture_source(nif: OblNif, src_ref: Any) -> str:
    """Resolve a NiSourceTexture block reference to its file path string."""
    if not isinstance(src_ref, int) or src_ref < 0 or src_ref >= len(nif.blocks):
        return ''
    src_blk = nif.blocks[src_ref]
    if src_blk.type_name == 'NiSourceTexture':
        return src_blk.file_name
    return ''


def _find_property_refs(nif: OblNif, blk: NifBlock) -> tuple:
    """Return (tex_blk, mat_blk, alpha_blk, has_stencil) from a geometry block's properties."""
    tex_blk = mat_blk = alpha_blk = None
    has_stencil = False
    for prop_ref in blk.properties:
        if prop_ref < 0 or prop_ref >= len(nif.blocks):
            continue
        prop = nif.blocks[prop_ref]
        if prop.type_name == 'NiTexturingProperty':
            tex_blk = prop
        elif prop.type_name == 'NiMaterialProperty':
            mat_blk = prop
        elif prop.type_name == 'NiAlphaProperty':
            alpha_blk = prop
        elif prop.type_name in ('NiStencilProperty', 'NiSpecularProperty'):
            has_stencil = True
    return tex_blk, mat_blk, alpha_blk, has_stencil


def _convert_nif(nif: OblNif, *, fix_textures: bool, remap_bones_flag: bool) -> tuple:
    """Transform an Oblivion NIF structure for Skyrim compatibility (in-place).

    Returns (nif, stats_dict).
    """
    stats = {
        'strips_fixed': 0,
        'properties_converted': 0,
        'textures_fixed': 0,
        'bones_remapped': 0,
        'root_converted': 0,
        'shader_blocks_added': 0,
        'root_rotation_reset': 0,
    }

    new_blocks: list[NifBlock] = []

    # Pass 1: remap bone names
    if remap_bones_flag:
        for blk in nif.blocks:
            if blk.name in BONE_MAP:
                blk.name = BONE_MAP[blk.name]
                stats['bones_remapped'] += 1

    # Pass 2: rewrite texture paths in NiSourceTexture blocks
    if fix_textures:
        for blk in nif.blocks:
            if blk.type_name == 'NiSourceTexture' and blk.file_name:
                new_path = _rewrite_texture_path(blk.file_name)
                if new_path != blk.file_name:
                    blk.file_name = new_path
                    stats['textures_fixed'] += 1

    # Pass 2b: Promote NiBinaryExtraData tangent space data to inline NiGeometryData tangents.
    # Oblivion stores tangent/bitangent vectors in a NiBinaryExtraData block attached to NiTriShape.
    # Skyrim (BSStream=83) expects inline tangents in NiTriShapeData (bs_num_uv bit 12 set).
    # Without this, normal maps render completely wrong (black exterior, colour noise interior).
    _num_before_new = len(nif.blocks)
    for _blk in nif.blocks[:_num_before_new]:
        if _blk.type_name not in ('NiTriShape', 'NiTriStrips'):
            continue
        if _blk.data_ref < 0 or _blk.data_ref >= len(nif.blocks):
            continue
        _data_blk = nif.blocks[_blk.data_ref]
        if _data_blk.tangents or not _data_blk.has_normals or _data_blk.num_vertices == 0:
            continue  # Already have inline tangents, or no normals/vertices
        for _ed_ref in _blk.extra_data:
            if _ed_ref < 0 or _ed_ref >= len(nif.blocks):
                continue
            _ed = nif.blocks[_ed_ref]
            if _ed.type_name != 'NiBinaryExtraData':
                continue
            if 'tangent' not in _ed.name.lower():
                continue
            _n = _data_blk.num_vertices
            _expected = _n * 24  # tangent (3×f32) + bitangent (3×f32) per vertex
            if len(_ed.extra_bytes) < _expected:
                continue
            _tang_list = [struct.unpack_from('<3f', _ed.extra_bytes, i * 12) for i in range(_n)]
            _bitan_list = [struct.unpack_from('<3f', _ed.extra_bytes, _n * 12 + i * 12) for i in range(_n)]
            _data_blk.tangents = _tang_list
            _data_blk.bitangents = _bitan_list
            _data_blk.num_uv_sets |= 0x1000  # Signal Skyrim: inline tangents present
            break

    # Pass 3: create BSLightingShaderProperty + BSShaderTextureSet for each geometry block
    for blk in nif.blocks:
        if blk.type_name not in ('NiTriShape', 'NiTriStrips'):
            continue

        tex_blk, mat_blk, alpha_blk, has_stencil = _find_property_refs(nif, blk)

        # Build texture set block
        tex_set_blk = NifBlock(type_name='BSShaderTextureSet')
        tex_set_blk.textures = [''] * 9

        diffuse = _resolve_texture_source(nif, tex_blk.diffuse_path if tex_blk else None)
        if fix_textures and diffuse:
            diffuse = _rewrite_texture_path(diffuse)
        if diffuse:
            tex_set_blk.textures[0] = diffuse
            if '.' in diffuse:
                base, ext = diffuse.rsplit('.', 1)
                tex_set_blk.textures[1] = base + '_n.' + ext
            else:
                tex_set_blk.textures[1] = diffuse + '_n'

        glow = _resolve_texture_source(nif, tex_blk.glow_path if tex_blk else None)
        if fix_textures and glow:
            glow = _rewrite_texture_path(glow)
        if glow:
            tex_set_blk.textures[3] = glow  # slot 3 = glow in Skyrim

        tex_set_idx = len(nif.blocks) + len(new_blocks)
        new_blocks.append(tex_set_blk)

        # Build shader property block
        shader_blk = NifBlock(type_name='BSLightingShaderProperty')
        shader_blk.shader_type = 0  # Default shader
        shader_blk.texture_set_ref = tex_set_idx
        shader_blk.uv_scale = (1.0, 1.0)
        shader_blk.uv_offset = (0.0, 0.0)
        shader_blk.texture_clamp_mode = 3    # WRAP_S | WRAP_T
        # Specular + ZBuffer_Test + ZBuffer_Write + Recieve_Shadows + Cast_Shadows
        shader_blk.shader_flags1 = 0x82400301
        shader_blk.shader_flags2 = 0x00008021
        # Set Skinned flag only for skinned meshes
        if blk.skin_instance >= 0:
            shader_blk.shader_flags1 |= 0x00000002  # SLSF1_Skinned

        if mat_blk is not None:
            shader_blk.glossiness = max(mat_blk.mat_glossiness, 1.0)
            shader_blk.specular_color = mat_blk.mat_specular
            spec = mat_blk.mat_specular
            shader_blk.specular_strength = max(spec[0], spec[1], spec[2], 0.0)
            shader_blk.emissive_color = mat_blk.mat_emissive
            shader_blk.shader_alpha = mat_blk.mat_alpha

        if has_stencil:
            shader_blk.shader_flags2 |= 0x00000010  # SLSF2_Double_Sided (bit 4)

        # Check vertex colors on the geometry data block
        if 0 <= blk.data_ref < len(nif.blocks):
            data_blk = nif.blocks[blk.data_ref]
            if data_blk.has_vertex_colors:
                shader_blk.shader_flags2 |= 0x00000020  # SLSF2_Vertex_Colors

        shader_idx = len(nif.blocks) + len(new_blocks)
        new_blocks.append(shader_blk)

        # Set BS Properties refs on the geometry block
        blk.bs_shader_ref = shader_idx
        if alpha_blk is not None:
            for prop_ref in blk.properties:
                if 0 <= prop_ref < len(nif.blocks):
                    if nif.blocks[prop_ref].type_name == 'NiAlphaProperty':
                        blk.bs_alpha_ref = prop_ref
                        break

        blk.properties = []   # Clear Oblivion property refs — not used in Skyrim
        stats['properties_converted'] += 1

    stats['shader_blocks_added'] = len(new_blocks)
    stats['collision_stripped'] = 0
    stats['controllers_stripped'] = 0

    # Pass 3b: Strip NiControllerManager chains.
    # Oblivion NiControllerManager uses inline SizedStrings (u32 len + chars) for
    # NiObjectNET.Name and for controlled-block node/property names within each
    # NiControllerSequence.  Skyrim's BSStream 83 NIF loader expects string table
    # indices for NiObjectNET blocks, so passing Oblivion raw_bytes through causes
    # stream misalignment and crashes (e.g. fencedoor02.nif).
    # Skyrim doors use Havok behaviour graphs for open/close animation, not NIF
    # controllers, so stripping the animation chain is safe.
    for blk in nif.blocks:
        if blk.controller < 0 or blk.controller >= len(nif.blocks):
            continue
        ctrl_blk = nif.blocks[blk.controller]
        if ctrl_blk.type_name == 'NiControllerManager':
            blk.controller = -1
            stats['controllers_stripped'] += 1

    # Pass 3c: Upgrade NiSkinInstance → BSDismemberSkinInstance.
    # Skyrim requires BSDismemberSkinInstance for all skinned biped meshes;
    # NiSkinInstance causes crashes when equipping armor/clothing.
    # Each BSDismemberSkinInstance partition entry maps a NiSkinPartition
    # partition to a body part ID.  We default to body part 32 (SBP_32_BODY)
    # for all partitions since Oblivion has no body-part semantic.
    skins_upgraded = 0
    for blk in nif.blocks:
        if blk.type_name != 'NiSkinInstance':
            continue
        blk.type_name = 'BSDismemberSkinInstance'
        sp_idx = blk.skin_partition
        if 0 <= sp_idx < len(nif.blocks):
            n = nif.blocks[sp_idx].n_skin_partitions
        else:
            n = 0
        n = max(n, 1)  # always at least one entry
        blk.dismember_partitions = [(0, 32)] * n  # PartFlag=0, BodyPart=32 (torso)
        skins_upgraded += 1
    stats['skins_upgraded'] = skins_upgraded

    # Pass 4: Strip all collision from the NIF.
    # The PyFFI-based collision handler (collision_handler.py) will re-apply
    # properly converted MOPP collision after the NIF is written, reading
    # the original Oblivion NIF and using MOPP_RL.exe to generate real MOPP data.
    for blk in nif.blocks:
        if blk.collision_object >= 0:
            blk.collision_object = -1
            stats['collision_stripped'] += 1

    # Pass 5: type renames — NiTriStrips -> NiTriShape, NiTriStripsData -> NiTriShapeData
    for bi, blk in enumerate(nif.blocks):
        if blk.type_name == 'NiTriStrips':
            blk.type_name = 'NiTriShape'
            stats['strips_fixed'] += 1
        elif blk.type_name == 'NiTriStripsData':
            blk.type_name = 'NiTriShapeData'
            stats['strips_fixed'] += 1

    # Pass 6: root NiNode -> BSFadeNode
    for root_idx in nif.root_indices:
        if 0 <= root_idx < len(nif.blocks):
            blk = nif.blocks[root_idx]
            if blk.type_name == 'NiNode':
                blk.type_name = 'BSFadeNode'
                stats['root_converted'] += 1

    # Pass 6b: Normalize NiAVObject flags to Skyrim standard.
    # Oblivion uses various flag values (0x0010, 0x0002, etc.) that differ
    # from Skyrim's standard 0x000E (SelectiveUpdate bits 1-3).
    # Skyrim reference meshes consistently use 0x000E for visible nodes.
    _SKY_DEFAULT_FLAGS = 0x000E
    for blk in nif.blocks:
        if blk.type_name in ('BSFadeNode', 'NiNode', 'NiTriShape',
                             'NiBillboardNode'):
            if blk.flags & 0x0001:  # Bit 0 = Hidden — preserve
                blk.flags = _SKY_DEFAULT_FLAGS | 0x0001
            else:
                blk.flags = _SKY_DEFAULT_FLAGS

    # Pass 6c: Bake non-identity root rotation into child nodes, then zero it.
    # Some Oblivion architecture/static NIFs have a non-identity rotation on the
    # root NiNode. In Oblivion, the engine composed REFR_transform * NIF_root *
    # child_transform to get world positions.  In Skyrim the composition order
    # differs, causing the mesh to appear rotated relative to its placement.
    # Fix: push the root rotation down into each direct child's local transform
    # (R_child_new = R_root * R_child; T_child_new = R_root * T_child), then
    # set the root rotation to identity.  Only safe for non-skinned meshes.
    def _mat33_mul(a: tuple, b: tuple) -> tuple:
        """3×3 matrix multiply: a @ b (row-major, both 9-tuples)."""
        return (
            a[0]*b[0]+a[1]*b[3]+a[2]*b[6], a[0]*b[1]+a[1]*b[4]+a[2]*b[7], a[0]*b[2]+a[1]*b[5]+a[2]*b[8],
            a[3]*b[0]+a[4]*b[3]+a[5]*b[6], a[3]*b[1]+a[4]*b[4]+a[5]*b[7], a[3]*b[2]+a[4]*b[5]+a[5]*b[8],
            a[6]*b[0]+a[7]*b[3]+a[8]*b[6], a[6]*b[1]+a[7]*b[4]+a[8]*b[7], a[6]*b[2]+a[7]*b[5]+a[8]*b[8],
        )

    def _mat33_vec3(m: tuple, v: tuple) -> tuple:
        """Multiply 3×3 matrix m by column vector v."""
        return (
            m[0]*v[0]+m[1]*v[1]+m[2]*v[2],
            m[3]*v[0]+m[4]*v[1]+m[5]*v[2],
            m[6]*v[0]+m[7]*v[1]+m[8]*v[2],
        )

    _IDENTITY_ROT = (1.0, 0.0, 0.0,
                     0.0, 1.0, 0.0,
                     0.0, 0.0, 1.0)
    _has_skin = any(blk.skin_instance >= 0 for blk in nif.blocks)
    if not _has_skin and not remap_bones_flag:
        for root_idx in nif.root_indices:
            if 0 <= root_idx < len(nif.blocks):
                root_blk = nif.blocks[root_idx]
                if root_blk.type_name not in ('BSFadeNode', 'NiNode'):
                    continue
                if root_blk.rotation == _IDENTITY_ROT:
                    continue
                R = root_blk.rotation
                # Push R into each direct child's local transform
                for child_idx in root_blk.children:
                    if 0 <= child_idx < len(nif.blocks):
                        child = nif.blocks[child_idx]
                        if hasattr(child, 'rotation') and hasattr(child, 'translation'):
                            child.rotation = _mat33_mul(R, child.rotation)
                            child.translation = _mat33_vec3(R, child.translation)
                root_blk.rotation = _IDENTITY_ROT
                stats.setdefault('root_rotation_reset', 0)
                stats['root_rotation_reset'] += 1

    nif.blocks.extend(new_blocks)

    # Pass 7: remove orphan blocks — blocks no longer referenced by any other
    # block or root.  This removes leftover Oblivion property blocks
    # (NiMaterialProperty, NiTexturingProperty, NiVertexColorProperty,
    # NiSourceTexture, etc.) that would corrupt the Skyrim NIF.
    nif = _remove_orphan_blocks(nif)

    # Pass 8: convert raw block bytes from Oblivion to Skyrim format.
    # bhk blocks have version-conditional fields that differ between
    # Oblivion (UV=0, UV2=11) and Skyrim (UV=12, UV2=83).
    for blk in nif.blocks:
        if blk.raw_bytes:
            _convert_raw_block_to_skyrim(blk)

    return nif, stats



# ---------------------------------------------------------------------------
# Oblivion → Skyrim raw block format conversion
# ---------------------------------------------------------------------------
# These functions convert raw block bytes from Oblivion field layout
# (UV=0, UV2=11) to Skyrim layout (UV=12, UV2=83) by inserting/modifying
# version-conditional fields at the exact byte positions.
#
# Field positions are derived from NifSkope's nif.xml and verified against
# actual Oblivion NIF files and reference Skyrim NIFs.
# ---------------------------------------------------------------------------

_F32_1_0 = struct.pack('<f', 1.0)
_F32_0_0 = struct.pack('<f', 0.0)
_ZERO4 = b'\x00\x00\x00\x00'


def _convert_raw_block_to_skyrim(blk: NifBlock) -> None:
    """Dispatch raw block format conversion by type."""
    tn = blk.type_name
    if tn in ('bhkRigidBody', 'bhkRigidBodyT'):
        blk.raw_bytes = _convert_bhk_rigid_body(blk.raw_bytes)
        blk.raw_refs = _update_raw_refs_for_rigid_body(blk.raw_refs)
    elif tn == 'bhkMoppBvTreeShape':
        blk.raw_bytes = _convert_bhk_mopp(blk.raw_bytes)
        blk.raw_refs = _update_raw_refs_after_insert(blk.raw_refs, 40, 1)
    elif tn == 'bhkRagdollConstraint':
        blk.raw_bytes = _convert_bhk_ragdoll_constraint(blk.raw_bytes)
    elif tn == 'bhkLimitedHingeConstraint':
        blk.raw_bytes = _convert_bhk_limited_hinge_constraint(blk.raw_bytes)
    elif tn == 'bhkHingeConstraint':
        blk.raw_bytes = _convert_bhk_hinge_constraint(blk.raw_bytes)
    elif tn == 'bhkMalleableConstraint':
        blk.raw_bytes = _convert_bhk_malleable_constraint(blk.raw_bytes)


def _convert_bhk_rigid_body(raw: bytes) -> bytes:
    """Convert bhkRigidBody/T from Oblivion to Skyrim layout.

    Key version-conditional field differences (nif.xml):
      - UnknownInt2 (UV2>34):  ABSENT in Oblivion, INSERT 4 bytes at offset 44
      - UnknownInt2 (UV2<=34): present in Oblivion at [48:52], SKIP for Skyrim
      - TimeFactor, GravityFactor: ABSENT in Oblivion, INSERT after AngularDamping
      - RollingFrictionMult: ABSENT in Oblivion, INSERT after Friction
      - EnableDeactivation (UV2>34) replaces DeactivatorType (UV2<=34) — same size
      - UnknownBytes2[4]: ABSENT in Oblivion, INSERT after UnknownBytes1
      - BodyFlags: u32 (UV2<76) in Oblivion → u16 (UV2>=76) in Skyrim

    Oblivion layout (UV2=11, total = 236 + n*4):
      [0:28]    bhkWorldObject: Shape(4)+HavokFilter(4)+Unused(4)+BPType(1)+Unused(3)+CinfoProp(12)
      [28:32]   CollResp(1)+UnusedByte1(1)+ProcCallDelay(2)
      [32:36]   UnknownInt1
      [36:40]   HavokFilterCopy
      [40:44]   Unused2[4]
      [44:48]   CollResp2(1)+UnusedByte2(1)+ProcCallDelay2(2)
      [48:52]   UnknownInt2 (UV2<=34)
      [52:68]   Translation (Vector4)
      [68:84]   Rotation (hkQuaternion)
      [84:100]  LinearVelocity (Vector4)
      [100:116] AngularVelocity (Vector4)
      [116:164] InertiaTensor (hkMatrix3, 48 bytes)
      [164:180] Center (Vector4)
      [180:184] Mass
      [184:188] LinearDamping
      [188:192] AngularDamping
      [192:196] Friction
      [196:200] Restitution
      [200:204] MaxLinearVelocity
      [204:208] MaxAngularVelocity
      [208:212] PenetrationDepth
      [212:213] MotionSystem
      [213:214] DeactivatorType (UV2<=34)
      [214:215] SolverDeactivation
      [215:216] QualityType
      [216:228] UnknownBytes1[12]
      [228:232] NumConstraints
      [232:232+n*4] Constraints
      [232+n*4:236+n*4] BodyFlags (u32)

    Skyrim layout (UV2=83, total = 250 + n*4):
      [0:44]    Same through Unused2
      [44:48]   UnknownInt2 (UV2>34) ← INSERT
      [48:52]   CollResp2+ProcCallDelay2 (from Oblivion[44:48])
      [52:192]  Translation through AngularDamping (same net offset)
      [192:196] TimeFactor ← INSERT
      [196:200] GravityFactor ← INSERT
      [200:204] Friction
      [204:208] RollingFrictionMult ← INSERT
      [208:212] Restitution
      ...
      [224:225] MotionSystem
      [225:226] EnableDeactivation (UV2>34, replaces DeactivatorType)
      [226:227] SolverDeactivation
      [227:228] QualityType
      [228:240] UnknownBytes1[12]
      [240:244] UnknownBytes2[4] ← INSERT
      [244:248] NumConstraints
      [248+n*4:250+n*4] BodyFlags (u16)
    """
    out = bytearray()

    # [0:44] bhkWorldObject(28) + CollResp/ProcCallDelay + UnknownInt1 +
    #        HavokFilterCopy + Unused2
    out.extend(raw[0:44])

    # INSERT UnknownInt2 (UV2>34) = 0
    out.extend(_ZERO4)

    # [44:48] CollResp2 + UnusedByte2 + ProcCallDelay2
    out.extend(raw[44:48])

    # SKIP [48:52] UnknownInt2 (UV2<=34) — absent in Skyrim

    # [52:192] Translation through AngularDamping (140 bytes, net offset +0)
    out.extend(raw[52:192])

    # INSERT TimeFactor=1.0, GravityFactor=1.0
    out.extend(_F32_1_0)
    out.extend(_F32_1_0)

    # [192:196] Friction
    out.extend(raw[192:196])

    # INSERT RollingFrictionMult=0.0
    out.extend(_F32_0_0)

    # [196:228] Restitution through UnknownBytes1 (32 bytes)
    #   Includes: Restitution(4) + MaxLinVel(4) + MaxAngVel(4) + PenDepth(4)
    #   + MotionSys(1) + DeactivatorType→EnableDeactivation(1)
    #   + SolverDeact(1) + QualityType(1) + UnknownBytes1(12)
    out.extend(raw[196:228])

    # INSERT UnknownBytes2[4] = zeros
    out.extend(_ZERO4)

    # NumConstraints + Constraints
    n_cons = struct.unpack_from('<I', raw, 228)[0]
    cons_end = 232 + n_cons * 4
    out.extend(raw[228:cons_end])

    # BodyFlags: convert u32 to u16
    body_flags = struct.unpack_from('<I', raw, cons_end)[0]
    out.extend(struct.pack('<H', body_flags & 0xFFFF))

    return bytes(out)


def _update_raw_refs_for_rigid_body(raw_refs: list) -> list:
    """Update ref offsets after bhkRigidBody format conversion.

    Offset mapping from Oblivion raw positions → Skyrim raw positions:
      [0, 44):    no change (bhkWorldObject through Unused2)
      [44, 48):   +4 (insert UnknownInt2(UV2>34) at 44 pushes CollResp2)
      [48, 52):   removed (UnknownInt2(UV2<=34) skipped)
      [52, 192):  no change (insert +4, skip -4 cancel out)
      [192, 196): +8 (TimeFactor + GravityFactor inserted)
      [196, 228): +12 (+ RollingFrictionMult inserted)
      [228+):     +16 (+ UnknownBytes2 inserted)
    """
    new_refs = []
    for off, ref in raw_refs:
        if off < 44:
            new_refs.append((off, ref))
        elif off < 48:
            new_refs.append((off + 4, ref))
        elif off < 52:
            continue  # removed field — should not contain refs
        elif off < 192:
            new_refs.append((off, ref))
        elif off < 196:
            new_refs.append((off + 8, ref))
        elif off < 228:
            new_refs.append((off + 12, ref))
        else:
            new_refs.append((off + 16, ref))
    return new_refs


def _convert_bhk_mopp(raw: bytes) -> bytes:
    """Convert bhkMoppBvTreeShape from Oblivion to Skyrim layout.

    Insert BuildType byte (=2, BUILT_WITHOUT_CHUNK_SUBDIVISION) between
    Scale and MOPP Data at offset 40.
    """
    return raw[:40] + b'\x02' + raw[40:]


def _update_raw_refs_after_insert(raw_refs: list, insert_off: int, insert_len: int) -> list:
    """Shift ref offsets after an insertion point."""
    return [(off + insert_len if off >= insert_off else off, ref) for off, ref in raw_refs]


# Motor descriptor with Type=MOTOR_NONE (1 byte: 0x00)
_MOTOR_NONE = b'\x00'

# Zero Vector4 (16 bytes) used for MotorA/B and Perp2AxleInB1
_VEC4_ZERO = b'\x00' * 16


def _constraint_header_end(raw: bytes) -> int:
    """Return offset of the first byte after the bhkConstraint header.

    Layout: NumEntities(u32) + Entities[n](Ref*n) + Priority(u32)
    """
    n_ent = struct.unpack_from('<I', raw, 0)[0]
    return 4 + n_ent * 4 + 4


def _convert_ragdoll_descriptor(desc: bytes) -> bytes:
    """Convert RagdollDescriptor from Oblivion (UV2<=16) to Skyrim (UV2>16).

    Oblivion: PivotA(16) PlaneA(16) TwistA(16) PivotB(16) PlaneB(16) TwistB(16) + angles(24)
    Skyrim:   TwistA(16) PlaneA(16) MotorA(16) PivotA(16) TwistB(16) PlaneB(16) MotorB(16) PivotB(16) + angles(24) + Motor(1)
    """
    pivot_a = desc[0:16]
    plane_a = desc[16:32]
    twist_a = desc[32:48]
    pivot_b = desc[48:64]
    plane_b = desc[64:80]
    twist_b = desc[80:96]
    angles = desc[96:120]  # ConeMax + PlaneMin/Max + TwistMin/Max + MaxFriction

    out = bytearray()
    out.extend(twist_a)
    out.extend(plane_a)
    out.extend(_VEC4_ZERO)  # MotorA (not in Oblivion)
    out.extend(pivot_a)
    out.extend(twist_b)
    out.extend(plane_b)
    out.extend(_VEC4_ZERO)  # MotorB (not in Oblivion)
    out.extend(pivot_b)
    out.extend(angles)
    out.extend(_MOTOR_NONE)
    return bytes(out)


def _convert_limited_hinge_descriptor(desc: bytes) -> bytes:
    """Convert LimitedHingeDescriptor from Oblivion (UV2<=16) to Skyrim (UV2>16).

    Oblivion: PivotA(16) AxleA(16) Perp2A1(16) Perp2A2(16) PivotB(16) AxleB(16) Perp2B2(16) + angles(12)
    Skyrim:   AxleA(16) Perp2A1(16) Perp2A2(16) PivotA(16) AxleB(16) Perp2B1(16) Perp2B2(16) PivotB(16) + angles(12) + Motor(1)
    """
    pivot_a = desc[0:16]
    axle_a = desc[16:32]
    perp2_a1 = desc[32:48]
    perp2_a2 = desc[48:64]
    pivot_b = desc[64:80]
    axle_b = desc[80:96]
    perp2_b2 = desc[96:112]
    angles = desc[112:124]  # MinAngle + MaxAngle + MaxFriction

    out = bytearray()
    out.extend(axle_a)
    out.extend(perp2_a1)
    out.extend(perp2_a2)
    out.extend(pivot_a)
    out.extend(axle_b)
    out.extend(_VEC4_ZERO)  # Perp2AxleInB1 (not in Oblivion)
    out.extend(perp2_b2)
    out.extend(pivot_b)
    out.extend(angles)
    out.extend(_MOTOR_NONE)
    return bytes(out)


def _convert_hinge_descriptor(desc: bytes) -> bytes:
    """Convert HingeDescriptor from Oblivion (ver<=20.0.0.5) to Skyrim (ver>=20.2.0.7).

    Oblivion: PivotA(16) Perp2A1(16) Perp2A2(16) PivotB(16) AxleB(16) = 80 bytes
    Skyrim:   AxleA(16) Perp2A1(16) Perp2A2(16) PivotA(16) AxleB(16) Perp2B1(16) Perp2B2(16) PivotB(16) = 128 bytes
    """
    pivot_a = desc[0:16]
    perp2_a1 = desc[16:32]
    perp2_a2 = desc[32:48]
    pivot_b = desc[48:64]
    axle_b = desc[64:80]

    # Derive AxleA as cross product of Perp2A1 × Perp2A2
    # (AxleA is always orthogonal to both Perp2 axes)
    p1 = struct.unpack_from('<4f', perp2_a1)
    p2 = struct.unpack_from('<4f', perp2_a2)
    ax = p1[1]*p2[2] - p1[2]*p2[1]
    ay = p1[2]*p2[0] - p1[0]*p2[2]
    az = p1[0]*p2[1] - p1[1]*p2[0]
    axle_a = struct.pack('<4f', ax, ay, az, 0.0)

    out = bytearray()
    out.extend(axle_a)
    out.extend(perp2_a1)
    out.extend(perp2_a2)
    out.extend(pivot_a)
    out.extend(axle_b)
    out.extend(_VEC4_ZERO)  # Perp2AxleInB1
    out.extend(_VEC4_ZERO)  # Perp2AxleInB2
    out.extend(pivot_b)
    return bytes(out)


def _convert_bhk_ragdoll_constraint(raw: bytes) -> bytes:
    """Convert bhkRagdollConstraint from Oblivion to Skyrim layout."""
    hdr_end = _constraint_header_end(raw)
    header = raw[:hdr_end]
    desc = raw[hdr_end:hdr_end + 120]
    return header + _convert_ragdoll_descriptor(desc)


def _convert_bhk_limited_hinge_constraint(raw: bytes) -> bytes:
    """Convert bhkLimitedHingeConstraint from Oblivion to Skyrim layout."""
    hdr_end = _constraint_header_end(raw)
    header = raw[:hdr_end]
    desc = raw[hdr_end:hdr_end + 124]
    return header + _convert_limited_hinge_descriptor(desc)


def _convert_bhk_hinge_constraint(raw: bytes) -> bytes:
    """Convert bhkHingeConstraint from Oblivion to Skyrim layout."""
    hdr_end = _constraint_header_end(raw)
    header = raw[:hdr_end]
    desc = raw[hdr_end:hdr_end + 80]
    return header + _convert_hinge_descriptor(desc)


def _convert_bhk_malleable_constraint(raw: bytes) -> bytes:
    """Convert bhkMalleableConstraint from Oblivion to Skyrim layout.

    Layout: NumEntities(4) + Entities[](n*4) + Priority(4) +
            Type(4) + NumEntities2(4) + EntityA(4) + EntityB(4) + Priority2(4) +
            Descriptor (depends on Type) +
            Tau(4) + Damping(4)  [Oblivion: ver<=20.0.0.5]
            → Strength(4)        [Skyrim: ver>=20.2.0.7]
    """
    hdr_end = _constraint_header_end(raw)
    # After constraint header: Type + inner header (20 bytes)
    inner_start = hdr_end
    ctype = struct.unpack_from('<I', raw, inner_start)[0]
    inner_header = raw[inner_start:inner_start + 20]  # Type + NumEnt2 + EntA + EntB + Priority2

    desc_start = inner_start + 20
    if ctype == 7:  # Ragdoll
        desc = raw[desc_start:desc_start + 120]
        new_desc = _convert_ragdoll_descriptor(desc)
        tail_start = desc_start + 120
    elif ctype == 2:  # Limited Hinge
        desc = raw[desc_start:desc_start + 124]
        new_desc = _convert_limited_hinge_descriptor(desc)
        tail_start = desc_start + 124
    elif ctype == 1:  # Hinge
        desc = raw[desc_start:desc_start + 80]
        new_desc = _convert_hinge_descriptor(desc)
        tail_start = desc_start + 80
    else:
        # Unknown type — pass through unchanged
        return raw

    # Oblivion tail: Tau(4) + Damping(4); Skyrim: Strength(4)
    tau = struct.unpack_from('<f', raw, tail_start)[0]
    strength = struct.pack('<f', tau)  # Use Tau as Strength

    out = bytearray()
    out.extend(raw[:hdr_end])
    out.extend(inner_header)
    out.extend(new_desc)
    out.extend(strength)
    return bytes(out)


def _remove_orphan_blocks(nif: OblNif) -> OblNif:
    """Remove unreferenced blocks and remap all block indices."""
    num = len(nif.blocks)
    # Collect all block index references from every block
    referenced: set[int] = set()
    for idx in nif.root_indices:
        if 0 <= idx < num:
            referenced.add(idx)

    def _collect_refs(blk: NifBlock) -> list[int]:
        refs: list[int] = []
        refs.extend(blk.extra_data)
        if blk.controller >= 0:
            refs.append(blk.controller)
        refs.extend(blk.children)
        refs.extend(blk.effects)
        if blk.data_ref >= 0:
            refs.append(blk.data_ref)
        if blk.skin_instance >= 0:
            refs.append(blk.skin_instance)
        if blk.bs_shader_ref >= 0:
            refs.append(blk.bs_shader_ref)
        if blk.bs_alpha_ref >= 0:
            refs.append(blk.bs_alpha_ref)
        if blk.collision_object >= 0:
            refs.append(blk.collision_object)
        if blk.texture_set_ref >= 0:
            refs.append(blk.texture_set_ref)
        if blk.skin_data >= 0:
            refs.append(blk.skin_data)
        if blk.skin_partition >= 0:
            refs.append(blk.skin_partition)
        if blk.skeleton_root >= 0:
            refs.append(blk.skeleton_root)
        refs.extend(blk.bone_refs)
        refs.extend(blk.properties)
        # Tracked refs from raw block parsing (precise, no false positives)
        refs.extend(ref for _, ref in blk.raw_refs)
        return [r for r in refs if 0 <= r < num]

    # BFS from roots
    queue = list(referenced)
    while queue:
        idx = queue.pop()
        for r in _collect_refs(nif.blocks[idx]):
            if r not in referenced:
                referenced.add(r)
                queue.append(r)

    # Force-remove Oblivion-only block types that can never exist in Skyrim.
    _OBLIVION_ONLY = frozenset({
        'NiMaterialProperty', 'NiTexturingProperty', 'NiVertexColorProperty',
        'NiSpecularProperty', 'NiStencilProperty', 'NiDitherProperty',
        'NiFogProperty',
    })
    referenced = {idx for idx in referenced
                  if nif.blocks[idx].type_name not in _OBLIVION_ONLY}

    if len(referenced) == num:
        return nif  # Nothing to remove

    # Build old→new index map
    kept = sorted(referenced)
    old_to_new: dict[int, int] = {}
    for new_idx, old_idx in enumerate(kept):
        old_to_new[old_idx] = new_idx

    def _remap(ref: int) -> int:
        if ref < 0:
            return ref
        return old_to_new.get(ref, -1)

    # Remap all references in kept blocks
    new_blocks = []
    for old_idx in kept:
        blk = nif.blocks[old_idx]
        blk.extra_data = [_remap(r) for r in blk.extra_data if _remap(r) >= 0]
        blk.controller = _remap(blk.controller)
        blk.children = [_remap(r) for r in blk.children if _remap(r) >= 0]
        blk.effects = [_remap(r) for r in blk.effects if _remap(r) >= 0]
        blk.data_ref = _remap(blk.data_ref)
        blk.skin_instance = _remap(blk.skin_instance)
        blk.bs_shader_ref = _remap(blk.bs_shader_ref)
        blk.bs_alpha_ref = _remap(blk.bs_alpha_ref)
        blk.collision_object = _remap(blk.collision_object)
        blk.texture_set_ref = _remap(blk.texture_set_ref)
        blk.skin_data = _remap(blk.skin_data)
        blk.skin_partition = _remap(blk.skin_partition)
        blk.skeleton_root = _remap(blk.skeleton_root)
        blk.bone_refs = [_remap(r) for r in blk.bone_refs]
        blk.properties = [_remap(r) for r in blk.properties if _remap(r) >= 0]

        # Remap refs inside raw_bytes at tracked positions only (no blind scan)
        if blk.raw_refs:
            raw = bytearray(blk.raw_bytes)
            new_raw_refs = []
            for off, old_ref in blk.raw_refs:
                new_ref = _remap(old_ref)
                raw[off:off+4] = struct.pack('<i', new_ref)
                new_raw_refs.append((off, new_ref))
            blk.raw_bytes = bytes(raw)
            blk.raw_refs = new_raw_refs

        new_blocks.append(blk)

    nif.blocks = new_blocks
    nif.root_indices = [_remap(r) for r in nif.root_indices]
    return nif


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def convert_nif(
    input_path: str,
    output_path: str,
    *,
    fix_textures: bool = True,
    remap_skeleton: bool | None = None,
) -> dict:
    """Convert a single Oblivion NIF file to Skyrim SE format.

    Args:
        input_path: Source .nif file path.
        output_path: Destination path (required, never overwrites input).
        fix_textures: Prepend tes4\\ to texture paths.
        remap_skeleton: Remap Bip01 bone names to NPC skeleton.
                        None = auto-detect from path (armor/clothes/characters).

    Returns:
        Dict with keys: converted, strips_fixed, properties_converted,
        textures_fixed, bones_remapped, root_converted, version_upgraded, error.
    """
    with open(input_path, 'rb') as f:
        raw = f.read()

    result = {
        'converted': False,
        'strips_fixed': False,
        'properties_converted': False,
        'textures_fixed': False,
        'bones_remapped': False,
        'root_converted': False,
        'version_upgraded': False,
        'error': None,
    }

    # Detect version from header
    try:
        nul = raw.index(b'\n')
        ver = struct.unpack_from('<I', raw, nul + 1)[0]
    except (ValueError, struct.error):
        result['error'] = 'Not a valid NIF file'
        return result

    if ver == SKY_VERSION:
        return result

    if ver not in (OBV_VERSION, OBV_VERSION_2):
        result['error'] = f'Unsupported NIF version: 0x{ver:08X}'
        return result

    try:
        nif = parse_oblivion_nif(raw)
    except Exception as e:
        result['error'] = f'Parse error: {e}'
        return result

    do_remap = remap_skeleton if remap_skeleton is not None else _is_skinned_mesh_path(input_path)

    try:
        nif, conv_stats = _convert_nif(nif, fix_textures=fix_textures, remap_bones_flag=do_remap)
    except Exception as e:
        result['error'] = f'Conversion error: {e}'
        return result

    try:
        out_bytes = build_skyrim_nif(nif)
    except Exception as e:
        result['error'] = f'Write error: {e}'
        return result

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, 'wb') as f:
        f.write(out_bytes)

    result['converted'] = True
    result['strips_fixed'] = conv_stats['strips_fixed'] > 0
    result['properties_converted'] = conv_stats['properties_converted'] > 0
    result['textures_fixed'] = conv_stats['textures_fixed'] > 0
    result['bones_remapped'] = conv_stats['bones_remapped'] > 0
    result['root_converted'] = conv_stats['root_converted'] > 0
    result['root_rotation_reset'] = conv_stats['root_rotation_reset'] > 0
    result['version_upgraded'] = True

    # Apply PyFFI-based MOPP collision from the original Oblivion NIF.
    # collision_handler.py reads the original, converts bhkNiTriStripsShape →
    # bhkPackedNiTriStripsShape → bhkMoppBvTreeShape via MOPP_RL.exe, then
    # injects the result into the already-written Skyrim NIF.
    _mopp_rl = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'MOPP_RL.exe')
    try:
        from mesh_convert.collision_handler import apply_mopp_collision, _PYFFI_AVAILABLE
        if _PYFFI_AVAILABLE:
            result['collision_applied'] = apply_mopp_collision(
                input_path, output_path, _mopp_rl)
    except Exception as e:
        result['collision_error'] = str(e)

    return result


# ---------------------------------------------------------------------------
# Multiprocessing batch conversion
# ---------------------------------------------------------------------------

def _batch_worker(args: tuple) -> tuple:
    """Worker for multiprocessing pool — must be module-level for pickling."""
    nif_str, out_path, fix_textures, remap_skeleton = args
    try:
        r = convert_nif(nif_str, out_path, fix_textures=fix_textures, remap_skeleton=remap_skeleton)
        return ('ok', nif_str, r)
    except Exception as e:
        return ('error', nif_str, str(e))


def batch_convert(
    mesh_dir: str,
    output_dir: str,
    *,
    dry_run: bool = False,
    fix_textures: bool = True,
    remap_skeleton: bool | None = None,
    workers: int | None = None,
) -> dict:
    """Convert all NIF files in a directory tree to Skyrim format.

    Args:
        mesh_dir: Source directory of NIF files.
        output_dir: Output directory (required, never modifies source).
        dry_run: Count files without converting.
        fix_textures: Prepend tes4\\ to texture paths.
        remap_skeleton: Remap bone names. None = auto-detect from path.
        workers: Parallel worker count. None = os.cpu_count().

    Returns:
        Stats dict.
    """
    mesh_path = Path(mesh_dir)
    out_base = Path(output_dir)
    nif_files = list(mesh_path.rglob('*.nif'))

    total = len(nif_files)
    stats = {
        'total': total,
        'converted': 0,
        'skipped': 0,
        'errors': 0,
        'strips': 0,
        'textures': 0,
        'bones': 0,
        'properties': 0,
        'roots': 0,
        'versions': 0,
    }

    if workers is None:
        workers = os.cpu_count() or 1

    print(f'Found {total} NIF files in {mesh_dir} (workers={workers})')

    if dry_run:
        print(f'Dry run -- would process {total} files')
        return stats

    work_args = [
        (
            str(nif_file),
            str(out_base / nif_file.relative_to(mesh_path)),
            fix_textures,
            remap_skeleton,
        )
        for nif_file in nif_files
    ]

    def _update(r: dict) -> None:
        if r.get('error'):
            stats['errors'] += 1
        elif r['converted']:
            stats['converted'] += 1
            if r['strips_fixed']:       stats['strips'] += 1
            if r['textures_fixed']:     stats['textures'] += 1
            if r['bones_remapped']:     stats['bones'] += 1
            if r['properties_converted']: stats['properties'] += 1
            if r['root_converted']:     stats['roots'] += 1
            if r['version_upgraded']:   stats['versions'] += 1
        else:
            stats['skipped'] += 1

    if workers > 1:
        import multiprocessing as mp
        done = 0
        with mp.Pool(processes=workers) as pool:
            for status, nif_str, payload in pool.imap_unordered(_batch_worker, work_args):
                done += 1
                if done % 500 == 0 or done == total:
                    print(f'  {done}/{total} -- converted={stats["converted"]} '
                          f'errors={stats["errors"]}')
                if status == 'ok':
                    _update(payload)
                else:
                    stats['errors'] += 1
                    if stats['errors'] <= 20:
                        print(f'  ERROR: {Path(nif_str).name}: {payload}')
    else:
        for i, args in enumerate(work_args):
            if (i + 1) % 200 == 0 or i == 0:
                print(f'  {i + 1}/{total} -- converted={stats["converted"]} '
                      f'errors={stats["errors"]}')
            status, _, payload = _batch_worker(args)
            if status == 'ok':
                _update(payload)
            else:
                stats['errors'] += 1
                if stats['errors'] <= 20:
                    print(f'  ERROR: {Path(work_args[i][0]).name}: {payload}')

    print(f'\nResults: {stats["converted"]} converted, {stats["skipped"]} unchanged, '
          f'{stats["errors"]} errors / {total} total')
    print(f'  Strips->Shape: {stats["strips"]}, Properties: {stats["properties"]}, '
          f'Textures: {stats["textures"]}, Bones: {stats["bones"]}')
    print(f'  Root->BSFadeNode: {stats["roots"]}, Version upgrade: {stats["versions"]}')
    return stats
    