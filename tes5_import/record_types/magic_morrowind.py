"""Morrowind magic effect -> TES5 archetype and actor value.

TES3's 143 engine-fixed effects are keyed by index, not by the four-character
code the Oblivion tables use, so the mapping lives here rather than in
`magic.EFFECT_ARCHETYPES`.  Nothing in this module is reachable from the
Oblivion path: `magic.convert_MGEF` consults it only for a record carrying
`MorrowindEffectIndex`.

114 of the 143 land on a native Skyrim archetype.  The rest carry NATIVE_NONE,
which converts them as an inert Value Modifier today and is where the
MorrowindRuntime effect table will attach.

See: docs/commentary/tes5_import_magic.md#morrowind-effects
"""

from .magic import (A_ABSORB, A_BOUND_WEAPON, A_CALM, A_CLOAK,
                    A_COMMAND_SUMMONED, A_CURE_DISEASE, A_CURE_PARALYSIS,
                    A_CURE_POISON, A_DEMORALIZE, A_DETECT_LIFE, A_DISPEL,
                    A_FRENZY, A_LIGHT, A_LOCK, A_OPEN, A_PARALYSIS,
                    A_PEAK_VALUE_MODIFIER, A_RALLY, A_SOUL_TRAP,
                    A_SUMMON_CREATURE, A_TELEKINESIS, A_TURN_UNDEAD,
                    A_VALUE_MODIFIER, AV_CARRY_WEIGHT,
                    AV_DAMAGE_RESIST,
                    AV_DETECT_LIFE_RANGE, AV_HEALTH,
                    AV_INVISIBILITY, AV_MAGICKA, AV_MAGICKA_RATE,
                    AV_MELEE_DAMAGE, AV_NIGHT_EYE, AV_NONE, AV_PARALYSIS,
                    AV_POISON_RESIST, AV_RESIST_DISEASE, AV_RESIST_FIRE,
                    AV_RESIST_FROST, AV_RESIST_MAGIC, AV_RESIST_SHOCK,
                    AV_STAMINA, AV_WATER_BREATHING,
                    AV_WATER_WALKING, DERIVE_AV)

#: Actor values Skyrim has but the Oblivion tables never needed.
AV_JUMPING_BONUS = 62
AV_ABSORB_CHANCE = 83
AV_BLINDNESS = 84
AV_MOVEMENT_NOISE = 92
AV_REFLECT_DAMAGE = 163

#: An effect Skyrim has no mechanism for; inert until the runtime carries it.
NATIVE_NONE = 'runtime'

#: TES3 MEDT flag -> the TES4 DATA.Flags bit meaning the same thing.
MW_FLAG_TO_TES4 = (
    (0x00004, 0x00000080),    # NoDuration
    (0x00008, 0x00000100),    # NoMagnitude
    (0x00010, 0x00000005),    # Harmful -> Hostile | Detrimental
    (0x00020, 0x00000400),    # ContinuousVfx -> FX Persist
    (0x00040, 0x00000010),    # CastSelf
    (0x00080, 0x00000020),    # CastTouch
    (0x00100, 0x00000040),    # CastTarget
    (0x00200, 0x00000800),    # AllowSpellmaking
    (0x00400, 0x00001000),    # AllowEnchanting
    (0x00001, 0x00080000),    # TargetSkill -> UseSkill
    (0x00002, 0x00100000),    # TargetAttribute -> UseAttribute
)

#: TES3 effect index -> (TES5 archetype, actor value | DERIVE_AV | NATIVE_NONE).
MW_EFFECT_ARCHETYPES = {
    0: (A_PEAK_VALUE_MODIFIER, AV_WATER_BREATHING),
    1: (A_PEAK_VALUE_MODIFIER, NATIVE_NONE),      # SwiftSwim
    2: (A_PEAK_VALUE_MODIFIER, AV_WATER_WALKING),
    3: (A_VALUE_MODIFIER, AV_DAMAGE_RESIST),      # Shield
    4: (A_CLOAK, AV_NONE),                        # FireShield
    5: (A_CLOAK, AV_NONE),                        # LightningShield
    6: (A_CLOAK, AV_NONE),                        # FrostShield
    7: (A_VALUE_MODIFIER, AV_CARRY_WEIGHT),       # Burden
    8: (A_VALUE_MODIFIER, AV_CARRY_WEIGHT),       # Feather
    9: (A_PEAK_VALUE_MODIFIER, AV_JUMPING_BONUS),
    10: (A_PEAK_VALUE_MODIFIER, NATIVE_NONE),     # Levitate
    11: (A_PEAK_VALUE_MODIFIER, NATIVE_NONE),     # SlowFall
    12: (A_LOCK, AV_NONE),
    13: (A_OPEN, AV_NONE),
    14: (A_VALUE_MODIFIER, AV_HEALTH),            # FireDamage
    15: (A_VALUE_MODIFIER, AV_HEALTH),            # ShockDamage
    16: (A_VALUE_MODIFIER, AV_HEALTH),            # FrostDamage
    17: (A_PEAK_VALUE_MODIFIER, DERIVE_AV),       # DrainAttribute
    18: (A_PEAK_VALUE_MODIFIER, AV_HEALTH),
    19: (A_PEAK_VALUE_MODIFIER, AV_MAGICKA),
    20: (A_PEAK_VALUE_MODIFIER, AV_STAMINA),
    21: (A_PEAK_VALUE_MODIFIER, DERIVE_AV),       # DrainSkill
    22: (A_VALUE_MODIFIER, DERIVE_AV),            # DamageAttribute
    23: (A_VALUE_MODIFIER, AV_HEALTH),
    24: (A_VALUE_MODIFIER, AV_MAGICKA),
    25: (A_VALUE_MODIFIER, AV_STAMINA),
    26: (A_VALUE_MODIFIER, DERIVE_AV),            # DamageSkill
    27: (A_VALUE_MODIFIER, AV_HEALTH),            # Poison
    28: (A_PEAK_VALUE_MODIFIER, AV_RESIST_FIRE),
    29: (A_PEAK_VALUE_MODIFIER, AV_RESIST_FROST),
    30: (A_PEAK_VALUE_MODIFIER, AV_RESIST_SHOCK),
    31: (A_PEAK_VALUE_MODIFIER, AV_RESIST_MAGIC),
    32: (A_PEAK_VALUE_MODIFIER, AV_RESIST_DISEASE),
    33: (A_PEAK_VALUE_MODIFIER, AV_RESIST_DISEASE),
    34: (A_PEAK_VALUE_MODIFIER, AV_RESIST_DISEASE),
    35: (A_PEAK_VALUE_MODIFIER, AV_POISON_RESIST),
    36: (A_PEAK_VALUE_MODIFIER, AV_DAMAGE_RESIST),
    37: (A_VALUE_MODIFIER, NATIVE_NONE),          # DisintegrateWeapon
    38: (A_VALUE_MODIFIER, NATIVE_NONE),          # DisintegrateArmor
    39: (A_PEAK_VALUE_MODIFIER, AV_INVISIBILITY),
    40: (A_PEAK_VALUE_MODIFIER, AV_INVISIBILITY),  # Chameleon
    41: (A_LIGHT, AV_NONE),
    42: (A_PEAK_VALUE_MODIFIER, NATIVE_NONE),     # Sanctuary
    43: (A_PEAK_VALUE_MODIFIER, AV_NIGHT_EYE),
    44: (A_CALM, AV_NONE),                        # Charm
    45: (A_PARALYSIS, AV_PARALYSIS),
    46: (A_PEAK_VALUE_MODIFIER, AV_MAGICKA),      # Silence
    47: (A_PEAK_VALUE_MODIFIER, AV_BLINDNESS),
    48: (A_PEAK_VALUE_MODIFIER, AV_MOVEMENT_NOISE),  # Sound
    49: (A_CALM, AV_NONE),
    50: (A_CALM, AV_NONE),
    51: (A_FRENZY, AV_NONE),
    52: (A_FRENZY, AV_NONE),
    53: (A_DEMORALIZE, AV_NONE),
    54: (A_DEMORALIZE, AV_NONE),
    55: (A_RALLY, AV_NONE),
    56: (A_RALLY, AV_NONE),
    57: (A_DISPEL, AV_NONE),
    58: (A_SOUL_TRAP, AV_NONE),
    59: (A_TELEKINESIS, AV_NONE),
    60: (A_VALUE_MODIFIER, NATIVE_NONE),          # Mark
    61: (A_VALUE_MODIFIER, NATIVE_NONE),          # Recall
    62: (A_VALUE_MODIFIER, NATIVE_NONE),          # DivineIntervention
    63: (A_VALUE_MODIFIER, NATIVE_NONE),          # AlmsiviIntervention
    64: (A_DETECT_LIFE, AV_DETECT_LIFE_RANGE),
    65: (A_VALUE_MODIFIER, NATIVE_NONE),          # DetectEnchantment
    66: (A_VALUE_MODIFIER, NATIVE_NONE),          # DetectKey
    67: (A_PEAK_VALUE_MODIFIER, AV_ABSORB_CHANCE),
    68: (A_PEAK_VALUE_MODIFIER, AV_REFLECT_DAMAGE),
    69: (A_CURE_DISEASE, AV_NONE),
    70: (A_CURE_DISEASE, AV_NONE),
    71: (A_CURE_DISEASE, AV_NONE),
    72: (A_CURE_POISON, AV_NONE),
    73: (A_CURE_PARALYSIS, AV_NONE),
    74: (A_VALUE_MODIFIER, DERIVE_AV),            # RestoreAttribute
    75: (A_VALUE_MODIFIER, AV_HEALTH),
    76: (A_VALUE_MODIFIER, AV_MAGICKA),
    77: (A_VALUE_MODIFIER, AV_STAMINA),
    78: (A_VALUE_MODIFIER, DERIVE_AV),            # RestoreSkill
    79: (A_PEAK_VALUE_MODIFIER, DERIVE_AV),       # FortifyAttribute
    80: (A_PEAK_VALUE_MODIFIER, AV_HEALTH),
    81: (A_PEAK_VALUE_MODIFIER, AV_MAGICKA),
    82: (A_PEAK_VALUE_MODIFIER, AV_STAMINA),
    83: (A_PEAK_VALUE_MODIFIER, DERIVE_AV),       # FortifySkill
    84: (A_PEAK_VALUE_MODIFIER, AV_MAGICKA),      # FortifyMagickaMultiplier
    85: (A_ABSORB, DERIVE_AV),                    # AbsorbAttribute
    86: (A_ABSORB, AV_HEALTH),
    87: (A_ABSORB, AV_MAGICKA),
    88: (A_ABSORB, AV_STAMINA),
    89: (A_ABSORB, DERIVE_AV),                    # AbsorbSkill
    90: (A_PEAK_VALUE_MODIFIER, AV_RESIST_FIRE),
    91: (A_PEAK_VALUE_MODIFIER, AV_RESIST_FROST),
    92: (A_PEAK_VALUE_MODIFIER, AV_RESIST_SHOCK),
    93: (A_PEAK_VALUE_MODIFIER, AV_RESIST_MAGIC),
    94: (A_PEAK_VALUE_MODIFIER, AV_RESIST_DISEASE),
    95: (A_PEAK_VALUE_MODIFIER, AV_RESIST_DISEASE),
    96: (A_PEAK_VALUE_MODIFIER, AV_RESIST_DISEASE),
    97: (A_PEAK_VALUE_MODIFIER, AV_POISON_RESIST),
    98: (A_PEAK_VALUE_MODIFIER, AV_DAMAGE_RESIST),
    99: (A_PEAK_VALUE_MODIFIER, AV_PARALYSIS),
    100: (A_DISPEL, AV_NONE),                     # RemoveCurse
    101: (A_TURN_UNDEAD, AV_NONE),
    117: (A_PEAK_VALUE_MODIFIER, AV_MELEE_DAMAGE),
    118: (A_COMMAND_SUMMONED, AV_NONE),           # CommandCreatures
    119: (A_COMMAND_SUMMONED, AV_NONE),           # CommandHumanoids
    126: (A_VALUE_MODIFIER, NATIVE_NONE),         # ExtraSpell
    132: (A_PEAK_VALUE_MODIFIER, AV_HEALTH),      # Corpus
    133: (A_PEAK_VALUE_MODIFIER, AV_HEALTH),      # Vampirism
    135: (A_VALUE_MODIFIER, AV_HEALTH),           # SunDamage
    136: (A_PEAK_VALUE_MODIFIER, AV_MAGICKA_RATE),  # StuntedMagicka
}

#: Summon indices, whose Assoc. Item is the creature the engine conjures.
MW_SUMMONS = tuple(range(102, 117)) + (134, 137, 138, 139, 140, 141, 142)

#: Bound weapon indices; Skyrim equips the WEAP named as the Assoc. Item.
MW_BOUND_WEAPONS = tuple(range(120, 126))

#: Bound armor indices; Skyrim has none, so these need the scripted stand-in.
MW_BOUND_ARMOR = tuple(range(127, 132))

MW_EFFECT_ARCHETYPES.update(
    {index: (A_SUMMON_CREATURE, AV_NONE) for index in MW_SUMMONS})
MW_EFFECT_ARCHETYPES.update(
    {index: (A_BOUND_WEAPON, AV_NONE)
     for index in MW_BOUND_WEAPONS + MW_BOUND_ARMOR})


def is_morrowind_effect(rec: dict) -> bool:
    """Whether this MGEF record came from a Morrowind export."""
    return bool(rec.get('MorrowindEffectIndex'))


def mw_archetype(index: int) -> int:
    """TES5 archetype for one TES3 effect index (Value Modifier if unknown)."""
    entry = MW_EFFECT_ARCHETYPES.get(index)
    return entry[0] if entry else A_VALUE_MODIFIER


def mw_actor_value(index: int, effect_av: int) -> int:
    """TES5 actor value for one effect instance.

    `effect_av` is the per-effect ActorValue the export wrote, already in the
    TES4 index space, so the shared attribute and skill tables apply.  An
    effect the runtime will own has no actor value of its own.
    """
    from .magic import ATTRIBUTE_TO_AV, SKILL_TO_AV

    entry = MW_EFFECT_ARCHETYPES.get(index)
    if entry is None:
        return AV_NONE
    value = entry[1]
    if value == NATIVE_NONE:
        return AV_NONE
    if value != DERIVE_AV:
        return value
    if effect_av is None or effect_av < 0:
        return AV_NONE
    if effect_av >= 12:
        return SKILL_TO_AV.get(effect_av, AV_NONE)
    return ATTRIBUTE_TO_AV.get(effect_av, AV_NONE)


def mw_needs_runtime(index: int) -> bool:
    """Whether this effect has no Skyrim mechanism and awaits the runtime."""
    entry = MW_EFFECT_ARCHETYPES.get(index)
    return bool(entry) and entry[1] == NATIVE_NONE


def mw_tes4_flags(mw_flags: int) -> int:
    """TES3 MEDT flags rewritten into the TES4 DATA.Flags layout.

    Every downstream reader speaks TES4 bits, so a Morrowind effect is
    translated once here rather than teaching each of them a second
    vocabulary.  The layouts diverge from the range bits onward: without
    this, TES3 CastTarget (0x100) reads as TES4 NoMagnitude and every
    effect converts as Self.
    """
    out = 0
    for mw_bit, tes4_bit in MW_FLAG_TO_TES4:
        if mw_flags & mw_bit:
            out |= tes4_bit
    return out
