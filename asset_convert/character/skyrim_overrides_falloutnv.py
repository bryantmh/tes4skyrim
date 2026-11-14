"""FO3/FNV skeleton bones that Oblivion's map has no entry for.

FO3 renamed Oblivion's twist bones and added thumbs and pauldrons, so 16 of
the FNV skeleton's 65 bones fall outside OBLIVION_TO_SKYRIM_BONE_MAP.  Armor
weighted to an unmapped bone collapses to the origin, so the two tables are
merged for an FO3/FNV source rather than replacing each other -- the shared
Bip01 spine, limb and finger names still map through Oblivion's entries.

See: docs/commentary/asset_convert_falloutnv.md#fnv-skeleton-bones
"""

from asset_convert.character.skyrim_overrides import (MORROWIND_SKIN_BONE_MAP,
                                                      OBLIVION_TO_SKYRIM_BONE_MAP)

#: The Bip01 table plus Morrowind's extra skin bones, whose names no other source uses.
_BASE_BONE_MAP = {**OBLIVION_TO_SKYRIM_BONE_MAP, **MORROWIND_SKIN_BONE_MAP}

#: FO3/FNV bone -> Skyrim bone, for the names Oblivion's table does not cover.
FALLOUT_TO_SKYRIM_BONE_MAP = {
    'Bip01 LUpArmTwistBone': 'NPC L UpperarmTwist1 [LUt1]',
    'Bip01 RUpArmTwistBone': 'NPC R UpperarmTwist1 [RUt1]',
    'Bip01 L ForeTwist': 'NPC L ForearmTwist1 [LLt1]',
    'Bip01 R ForeTwist': 'NPC R ForearmTwist1 [RLt1]',
    'Bip01 L Thumb1': 'NPC L Finger00 [LF00]',
    'Bip01 L Thumb11': 'NPC L Finger01 [LF01]',
    'Bip01 L Thumb12': 'NPC L Finger02 [LF02]',
    'Bip01 R Thumb1': 'NPC R Finger00 [RF00]',
    'Bip01 R Thumb11': 'NPC R Finger01 [RF01]',
    'Bip01 R Thumb12': 'NPC R Finger02 [RF02]',
    'Bip01 LPauldron': 'NPC L Clavicle [LClv]',
    'Bip01 RPauldron': 'NPC R Clavicle [RClv]',
    'Weapon': 'WEAPON',
}

#: Bones only FO3/FNV skeletons carry; any one of them identifies the source.
FALLOUT_SKELETON_MARKERS = frozenset(FALLOUT_TO_SKYRIM_BONE_MAP) - {'Weapon'}

#: FO3/FNV rig helpers with no Skyrim counterpart; skinning must never use them.
FALLOUT_DROPPED_BONES = frozenset({
    'Bip01 R ForeTwistDriver', 'Camera3rd', 'HeadAnims',
})


def is_fallout_skeleton(skel: dict) -> bool:
    """True when these bone names came from an FO3/FNV skeleton.

    See: docs/commentary/asset_convert_falloutnv.md#selected-by-authored-bone-names
    """
    return any(name in skel for name in FALLOUT_SKELETON_MARKERS)


def bone_map_for(skel: dict) -> dict:
    """The source skeleton's bone map, FO3/FNV entries merged in when needed.

    See: docs/commentary/asset_convert_falloutnv.md#fnv-skeleton-bones
    """
    if not is_fallout_skeleton(skel):
        return _BASE_BONE_MAP
    return {**_BASE_BONE_MAP, **FALLOUT_TO_SKYRIM_BONE_MAP}


def oblivion_alias_map(skel: dict) -> dict:
    """FO3/FNV bone -> the Oblivion bone sharing its Skyrim target; {} otherwise.

    See: docs/commentary/asset_convert_falloutnv.md#fnv-body-fitting
    """
    if not skel or not is_fallout_skeleton(skel):
        return {}
    by_target = {sk: ob for ob, sk in OBLIVION_TO_SKYRIM_BONE_MAP.items()}
    return {fo3: by_target[sk] for fo3, sk in FALLOUT_TO_SKYRIM_BONE_MAP.items()
            if sk in by_target}
