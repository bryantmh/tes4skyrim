"""Skyrim-derived constants for asset_convert.

These values were gleaned from vanilla Skyrim mesh files and are used when
converting Oblivion assets that lack equivalent data.  All values are
read-only constants — do not modify them at runtime.
"""
from __future__ import annotations
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Armor bone name mapping: Oblivion Bip01 → Skyrim NPC skeleton
# ---------------------------------------------------------------------------
# Sourced from comparing Oblivion worn armor bone node names to vanilla Skyrim
# worn armor NIFs (e.g. armor/ebony/m/cuirass_0.nif, boots_0.nif, etc.).
# Applied to all skinned (non-_gnd) armor/clothing NIF files during conversion.
OBLIVION_TO_SKYRIM_BONE_MAP: dict[str, str] = {
    # Root / COM
    'Bip01':              'NPC Root [Root]',
    'Bip01 NonAccum':     'NPC COM [COM ]',
    # Pelvis / spine
    'Bip01 Pelvis':       'NPC Pelvis [Pelv]',
    'Bip01 Spine':        'NPC Spine [Spn0]',
    'Bip01 Spine1':       'NPC Spine1 [Spn1]',
    'Bip01 Spine2':       'NPC Spine2 [Spn2]',
    # Head / neck
    'Bip01 Neck':         'NPC Neck [Neck]',
    'Bip01 Neck1':        'NPC Neck [Neck]',
    'Bip01 Head':         'NPC Head [Head]',
    # Right arm
    'Bip01 R Clavicle':      'NPC R Clavicle [RClv]',
    'Bip01 R UpperArm':      'NPC R UpperArm [RUar]',
    'Bip01 R UpperArmTwist': 'NPC R UpperarmTwist1 [RUt1]',
    'Bip01 R Forearm':       'NPC R Forearm [RLar]',
    'Bip01 R ForearmTwist':  'NPC R ForearmTwist1 [RLt1]',
    'Bip01 R Hand':          'NPC R Hand [RHnd]',
    # Left arm
    'Bip01 L Clavicle':      'NPC L Clavicle [LClv]',
    'Bip01 L UpperArm':      'NPC L UpperArm [LUar]',
    'Bip01 L UpperArmTwist': 'NPC L UpperarmTwist1 [LUt1]',
    'Bip01 L Forearm':       'NPC L Forearm [LLar]',
    'Bip01 L ForearmTwist':  'NPC L ForearmTwist1 [LLt1]',
    'Bip01 L Hand':          'NPC L Hand [LHnd]',
    # Right leg
    'Bip01 R Thigh':   'NPC R Thigh [RThg]',
    'Bip01 R Calf':    'NPC R Calf [RClf]',
    'Bip01 R Foot':    'NPC R Foot [Rft ]',
    'Bip01 R Toe0':    'NPC R Toe0 [RToe]',
    # Left leg
    'Bip01 L Thigh':   'NPC L Thigh [LThg]',
    'Bip01 L Calf':    'NPC L Calf [LClf]',
    'Bip01 L Foot':    'NPC L Foot [Lft ]',
    'Bip01 L Toe0':    'NPC L Toe0 [LToe]',
    # Alternate spine name (some Oblivion NIFs use Spine0 instead of Spine)
    'Bip01 Spine0':    'NPC Spine [Spn0]',
    # Weapon / attachment bone nodes
    'Bip01 L Weapon':  'WeaponLeft',
    'Bip01 R Weapon':  'WeaponRight',
    'Bip01 L Shield':  'SHIELD',
    'Bip01 Quiver':    'WeaponBack',
    # Right fingers (Oblivion: FingerX = base, FingerXY = knuckle Y of finger X)
    'Bip01 R Finger0':  'NPC R Finger00 [RF00]',
    'Bip01 R Finger01': 'NPC R Finger01 [RF01]',
    'Bip01 R Finger02': 'NPC R Finger02 [RF02]',
    'Bip01 R Finger1':  'NPC R Finger10 [RF10]',
    'Bip01 R Finger11': 'NPC R Finger11 [RF11]',
    'Bip01 R Finger12': 'NPC R Finger12 [RF12]',
    'Bip01 R Finger2':  'NPC R Finger20 [RF20]',
    'Bip01 R Finger21': 'NPC R Finger21 [RF21]',
    'Bip01 R Finger22': 'NPC R Finger22 [RF22]',
    'Bip01 R Finger3':  'NPC R Finger30 [RF30]',
    'Bip01 R Finger31': 'NPC R Finger31 [RF31]',
    'Bip01 R Finger32': 'NPC R Finger32 [RF32]',
    'Bip01 R Finger4':  'NPC R Finger40 [RF40]',
    'Bip01 R Finger41': 'NPC R Finger41 [RF41]',
    'Bip01 R Finger42': 'NPC R Finger42 [RF42]',
    # Left fingers
    'Bip01 L Finger0':  'NPC L Finger00 [LF00]',
    'Bip01 L Finger01': 'NPC L Finger01 [LF01]',
    'Bip01 L Finger02': 'NPC L Finger02 [LF02]',
    'Bip01 L Finger1':  'NPC L Finger10 [LF10]',
    'Bip01 L Finger11': 'NPC L Finger11 [LF11]',
    'Bip01 L Finger12': 'NPC L Finger12 [LF12]',
    'Bip01 L Finger2':  'NPC L Finger20 [LF20]',
    'Bip01 L Finger21': 'NPC L Finger21 [LF21]',
    'Bip01 L Finger22': 'NPC L Finger22 [LF22]',
    'Bip01 L Finger3':  'NPC L Finger30 [LF30]',
    'Bip01 L Finger31': 'NPC L Finger31 [LF31]',
    'Bip01 L Finger32': 'NPC L Finger32 [LF32]',
    'Bip01 L Finger4':  'NPC L Finger40 [LF40]',
    'Bip01 L Finger41': 'NPC L Finger41 [LF41]',
    'Bip01 L Finger42': 'NPC L Finger42 [LF42]',
}

# ---------------------------------------------------------------------------
# BSDismemberSkinInstance body part IDs
# ---------------------------------------------------------------------------
# These match Skyrim's biped slot numbers, used in BSDismemberSkinInstance
# partition entries to identify which body part each skin mesh covers.
# Sourced from vanilla Skyrim armor NIFs (ebony, db, elven sets).
SBP_32_BODY      = 32   # Torso (cuirass)
SBP_33_HANDS     = 33   # Hands (gloves, gauntlets inner)
SBP_34_FOREARMS  = 34   # Forearms (gauntlets outer, vambraces)
SBP_37_FEET      = 37   # Feet (boots)
SBP_38_CALVES    = 38   # Calves / lower legs (boots)
SBP_44_LOWERBODY = 44   # Upper legs / thighs (greaves).  Requires the character
                         # body mesh to have a matching partition — see
                         # tools/modify_body_meshes.py to add part-44 partitions
                         # to malebody_0.nif / femalebody_0.nif.
SBP_130_HEAD     = 130  # Head skin (character's head mesh)
SBP_131_HAIR     = 131  # Hair / headwear (helmets, hoods, circlets)

# Geometry-name keyword → primary body part + optional multi-partition list.
# Used by _get_body_parts_for_geometry() to assign BSDismemberSkinInstance
# partition body_part values based on the NiTriShape mesh name.
# Format: (lowercase_keyword, single_block_bp, multi_block_bps_or_None)
# multi_block_bps is used when the geometry has exactly 2 partition blocks;
# it should list the most anatomically accurate part for each block.
ARMOR_GEOMETRY_BODY_PARTS: list[tuple[str, int, list[int] | None]] = [
    ('head',      SBP_131_HAIR,    None),   # headwear (helmets, hoods)
    ('helmet',    SBP_131_HAIR,    None),   # helmets → hair slot
    ('hood',      SBP_131_HAIR,    None),
    ('upperbody', SBP_32_BODY,     None),
    ('torso',     SBP_32_BODY,     None),
    ('lowerbody', SBP_44_LOWERBODY, None),   # greaves / upper leg armour
    ('thigh',     SBP_44_LOWERBODY, None),
    ('greave',    SBP_44_LOWERBODY, None),
    ('calf',      SBP_38_CALVES,   None),   # lower-leg / calves
    ('foot',      SBP_37_FEET,     None),
    ('boot',      SBP_37_FEET,     None),
    ('shoe',      SBP_37_FEET,     None),
    # Gauntlets/gloves: all partitions use Hands slot, matching vanilla Skyrim gauntlets.
    # (Left/right split by bone count is internal; biped body_part must be Hands to avoid
    #  the left gauntlet being suppressed by cuirass ForeArms(34) coverage.)
    ('hand',      SBP_33_HANDS, None),
    ('gauntlet',  SBP_33_HANDS, None),
    ('glove',     SBP_33_HANDS, None),
    # NB: Oblivion 'Arms' geometry only appears in cuirasses/shirts, never in
    # gauntlets (which use 'Hand').  Tag as SBP_32_BODY so the arm portions
    # of cuirasses/shirts are NEVER hidden when gauntlets/gloves are equipped.
    ('arm',       SBP_32_BODY,     None),
    ('vambrace',  SBP_34_FOREARMS, None),
    ('bracer',    SBP_34_FOREARMS, None),
]
ARMOR_DEFAULT_BODY_PART = SBP_32_BODY  # fallback for unrecognised geometry names

# ---------------------------------------------------------------------------
# BSXFlags
# ---------------------------------------------------------------------------
# Vanilla Skyrim weapon NIFs use 0xC2 = COMPLEX(0x80) | ANIMATED(0x40) | HAVOK(0x02).
# Static/environment meshes use 0x82 (no ANIMATED).
# Animated doors/activators use 0x8B = ARTICULATED(0x80) | COMPLEX(0x08) | HAVOK(0x02) | ANIMATED(0x01).
# Constrained dynamic objects (swinging signs) use 0xCA = ARTICULATED(0x80) | DYNAMIC(0x40) | COMPLEX(0x08) | HAVOK(0x02).
BSX_FLAGS_STATIC      = 0x82   # 130 — static objects with collision
BSX_FLAGS_ANIMATED    = 0x8B   # 139 — animated objects (doors, display cases, activators)
BSX_FLAGS_CONSTRAINED = 0xCA   # 202 — dynamic constrained objects (swinging signs)

# ---------------------------------------------------------------------------
# Weapon blood textures (BSEffectShaderProperty.source_texture)
# Sourced from vanilla Skyrim iron weapon NIFs.
# ---------------------------------------------------------------------------
# BloodEdge  — used for bladed weapons (swords, daggers, axes)
# BloodHit   — used for blunt weapons (maces, warhammers, battleaxes)
BLOOD_TEXTURE_EDGE  = r'textures\blood\BloodEdge01.dds'
BLOOD_TEXTURE_BLUNT = r'textures\blood\BloodHitDecals01.dds'

# Mapping from Skyrim Prn value → blood texture path
# Used when generating blood geometry for converted weapons.
WEAPON_PRN_BLOOD_TEXTURE: dict[str, str] = {
    'WeaponSword':  BLOOD_TEXTURE_EDGE,
    'WeaponBack':   BLOOD_TEXTURE_EDGE,    # 2H swords / greatswords
    'WeaponDagger': BLOOD_TEXTURE_EDGE,
    'WeaponAxe':    BLOOD_TEXTURE_EDGE,
    'WeaponMace':   BLOOD_TEXTURE_BLUNT,
    'WeaponStaff':  BLOOD_TEXTURE_BLUNT,
    'Weapon':       BLOOD_TEXTURE_EDGE,    # generic
}

# ---------------------------------------------------------------------------
# BSEffectShaderProperty flags for blood decal geometry
# Sourced from vanilla ironmace.nif BloodFX NiTriShape
# ---------------------------------------------------------------------------
BLOOD_FX_SHADER_FLAGS_1 = 0x8C000000
BLOOD_FX_SHADER_FLAGS_2 = 0x00020000

# BSLightingShaderProperty flags for BloodLighting geometry
# type=1 (EnvironmentMap shader), sf1 from vanilla ironmace BloodLighting
BLOOD_LIGHTING_SHADER_TYPE  = 1          # EnvironmentMap
BLOOD_LIGHTING_SHADER_FLAGS_1 = 0x8E400181
BLOOD_LIGHTING_SHADER_FLAGS_2 = 0x00000001

# ---------------------------------------------------------------------------
# BSInvMarker defaults for weapon inventory display
# Rotation values are milliradians (e.g. 4712 ≈ 4.712 rad ≈ 270°).
# Sourced from vanilla iron weapon and armor NIFs.
# ---------------------------------------------------------------------------
WEAPON_INV_MARKER_ROT_X = 4712
WEAPON_INV_MARKER_ROT_Y = 6283
WEAPON_INV_MARKER_ROT_Z = 0
WEAPON_INV_MARKER_ZOOM  = 1.0

# Shield: rot_x ≈ 270° (face toward camera); sourced from vanilla ironshield.nif
SHIELD_INV_MARKER_ROT_X = 4712
SHIELD_INV_MARKER_ROT_Y = 0
SHIELD_INV_MARKER_ROT_Z = 0
SHIELD_INV_MARKER_ZOOM  = 1.0

# Armor ground models (_gnd): rot_x ≈ 90° (upright); sourced from vanilla cuirassgnd.nif
ARMOR_GND_INV_MARKER_ROT_X = 1570
ARMOR_GND_INV_MARKER_ROT_Y = 0
ARMOR_GND_INV_MARKER_ROT_Z = 0
ARMOR_GND_INV_MARKER_ZOOM  = 1.0

# ---------------------------------------------------------------------------
# Per-piece armor mesh offsets applied after skin retarget.
# Shifts, scales, and optionally tilts the armor geometry (NOT the spliced
# body skin) in Skyrim skeleton space.
# Positive Z = up, positive Y = forward (into screen from front view), positive X = right.
# Applied order: per-axis scale → pull → rotate → translate, then bind matrices recomputed.
# Piece type is detected from the NIF filename in nif_converter._get_armor_piece_type().
# ---------------------------------------------------------------------------


@dataclass
class ArmorOffsetConfig:
    """Named configuration for armor vertex offset + scale applied after skin retarget.

    Translation (dx, dy, dz):
        World-space shift in Skyrim NIF units (≈ inches), applied last after all
        other ops.  Positive X = right, positive Y = forward, positive Z = up.

    Per-axis scale (sx, sy, sz):
        Independent scale per axis around world origin (0, 0, 0).  A uniform
        8% scale-up is sx=sy=sz=1.08.  Setting sz=1.0 with sx=sy=1.08
        expands the mesh outward while leaving height unchanged.

    rotate:
        Front-to-back tilt in radians around the mesh YZ centroid.
        Positive → top tilts backward (−Y), bottom tilts forward (+Y).
        Implemented as a proper 2D rotation in the YZ plane (shape-preserving).
    """
    dx: float = 0.0
    dy: float = 0.0
    dz: float = 0.0
    sx: float = 1.0   # per-axis scale X (left/right)
    sy: float = 1.0   # per-axis scale Y (front/back)
    sz: float = 1.0   # per-axis scale Z (up/down)
    rotate: float = 0.0   # front-to-back tilt in radians


ARMOR_PIECE_OFFSETS: dict[str, ArmorOffsetConfig] = {
    # Upper body: torso armors.
    'cuirass':   ArmorOffsetConfig(dy=-1.6, dz=-6, sx=1.05, sy=1.08, sz=1.08),
    # Lower body: pants, greaves, skirts.
    'greaves':   ArmorOffsetConfig(dy=-0.5, sx=1.05, sy=1.08, sz=1.05),
    # Feet: boots, shoes.
    'boots':     ArmorOffsetConfig(sx=1.08, sy=1.08, sz=1.08),
    # Hands: gauntlets, gloves, bracers.
    'gauntlets': ArmorOffsetConfig(),
    # Head: helmets, hoods, hats, circlets.
    'helmet':    ArmorOffsetConfig(dz=7, dy=-1.8),
    # Shields (Prn='SHIELD' handled separately, but keep entry for safety).
    'shield':    ArmorOffsetConfig(),
    # Fallback for unrecognised piece types
    'default':   ArmorOffsetConfig(),
}
