"""MGEF — Magic Effect.

Oblivion's magic system is a fixed table of ~145 engine-known effect codes;
Skyrim's is open, and every effect is an authored MGEF record whose *Archtype*
selects one of 47 engine classes (`ValueModifierEffect`, `SummonCreatureEffect`,
`BoundItemEffect`, `OpenEffect`, ... — all present as RTTI classes with real
vtables in SkyrimSE.exe, including the four archetypes vanilla Skyrim.esm never
uses: 2 Dispel, 15 Lock, 16 Open, 24 Turn Undead).

Before this module MGEF was in SKIP_TYPES and every effect on every
SPEL/ENCH/ALCH/INGR/SGST was re-pointed at a vanilla Skyrim MGEF through a flat
4-char code table.  That works for value modifiers (Restore Health →
AlchRestoreHealth) and fails completely for effects parameterised by a FormID
the source record carries — all 33 summons, the bound weapons/armor — which
were dropped, gutting 382 records into zero-magnitude filler.

Emitting real MGEFs makes those convertible: the archetype carries the
behaviour and `Assoc. Item` carries the converted CREA/WEAP/ARMO.

Layout: TES5 MGEF DATA is 152 bytes, FormVersion 44.  Field offsets from
xEdit `wbDefinitionsTES5.pas` wbMGEFData, byte-verified against
references/Skyrim.esm/MGEF.txt (950 records).

TES5 record order: EDID VMAD FULL MDOB KSIZ/KWDA DATA ESCE* SNDD DNAM CTDA
"""

import struct

from . import magic_art
from ..base.text_reader import get_float, get_formid, get_int, get_str
from ..base.writer import (
    pack_formid_subrecord,
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
)

# --- TES5 MGEF DATA offsets (152 bytes) -----------------------------------
MGEF_DATA_SIZE = 152

O_FLAGS = 0
O_BASE_COST = 4
O_ASSOC_ITEM = 8
O_MAGIC_SKILL = 12
O_RESIST_VALUE = 16
#: Counter Effect Count: a u16 followed by 2 unused bytes.
O_COUNTER_COUNT = 20
O_CASTING_LIGHT = 24
O_TAPER_WEIGHT = 28
O_HIT_SHADER = 32
O_ENCHANT_SHADER = 36
O_MIN_SKILL = 40
O_SPELLMAKING_AREA = 44
O_SPELLMAKING_TIME = 48
O_TAPER_CURVE = 52
O_TAPER_DURATION = 56
O_SECOND_AV_WEIGHT = 60
O_ARCHETYPE = 64
O_ACTOR_VALUE = 68
O_PROJECTILE = 72
O_EXPLOSION = 76
O_CASTING_TYPE = 80
O_DELIVERY = 84
O_SECOND_AV = 88
O_CASTING_ART = 92
O_HIT_EFFECT_ART = 96
O_IMPACT_DATA = 100
O_SKILL_USAGE_MULT = 104
O_DUAL_CAST_ART = 108
O_DUAL_CAST_SCALE = 112
O_ENCHANT_ART = 116
O_HIT_VISUALS = 120
O_ENCHANT_VISUALS = 124
O_EQUIP_ABILITY = 128
O_IMAGE_SPACE_MOD = 132
O_PERK_TO_APPLY = 136
O_CASTING_SOUND_LEVEL = 140
O_AI_SCORE = 144
O_AI_DELAY = 148

# --- TES5 MGEF DATA.Flags -------------------------------------------------
F_HOSTILE = 0x00000001
F_RECOVER = 0x00000002
F_DETRIMENTAL = 0x00000004
F_NO_HIT_EVENT = 0x00000010
F_NO_DURATION = 0x00000200
F_NO_MAGNITUDE = 0x00000400
F_NO_AREA = 0x00000800
F_FX_PERSIST = 0x00001000
F_HIDE_IN_UI = 0x00008000
F_NO_RECAST = 0x00020000
F_POWER_AFFECTS_MAGNITUDE = 0x00200000
F_POWER_AFFECTS_DURATION = 0x00400000
F_PAINLESS = 0x04000000
F_NO_HIT_EFFECT = 0x08000000
F_NO_DEATH_DISPEL = 0x10000000

# --- TES4 MGEF DATA.Flags (tes4_export/record_types/equipment.py) ---------
T4_HOSTILE = 0x00000001
T4_RECOVER = 0x00000002
T4_DETRIMENTAL = 0x00000004
T4_MAGNITUDE_PERCENT = 0x00000008
T4_SELF = 0x00000010
T4_TOUCH = 0x00000020
T4_TARGET = 0x00000040
T4_NO_DURATION = 0x00000080
T4_NO_MAGNITUDE = 0x00000100
T4_NO_AREA = 0x00000200
T4_FX_PERSIST = 0x00000400
T4_SPELLMAKING = 0x00000800
T4_ENCHANTING = 0x00001000
T4_NO_INGREDIENT = 0x00002000
T4_USE_WEAPON = 0x00010000
T4_USE_ARMOR = 0x00020000
T4_USE_CREATURE = 0x00040000
T4_USE_SKILL = 0x00080000
T4_USE_ATTRIBUTE = 0x00100000
T4_USE_ACTOR_VALUE = 0x01000000
T4_SPRAY_PROJECTILE = 0x02000000
T4_BOLT_PROJECTILE = 0x04000000
T4_NO_HIT_EFFECT = 0x08000000
T4_PERSIST_ON_DEATH = 0x10000000
T4_FOG_PROJECTILE = 0x40000000

# --- TES5 archetypes we emit ---------------------------------------------
A_VALUE_MODIFIER = 0
A_SCRIPT = 1
A_DISPEL = 2
A_CURE_DISEASE = 3
A_ABSORB = 4
A_DUAL_VALUE_MODIFIER = 5
A_CALM = 6
A_DEMORALIZE = 7
A_FRENZY = 8
A_COMMAND_SUMMONED = 10
A_INVISIBILITY = 11
A_LIGHT = 12
A_LOCK = 15
A_OPEN = 16
A_BOUND_WEAPON = 17
A_SUMMON_CREATURE = 18
A_DETECT_LIFE = 19
A_TELEKINESIS = 20
A_PARALYSIS = 21
A_REANIMATE = 22
A_SOUL_TRAP = 23
A_TURN_UNDEAD = 24
A_CURE_PARALYSIS = 27
A_CURE_POISON = 29
A_PEAK_VALUE_MODIFIER = 34
A_CLOAK = 35
A_RALLY = 38

# Archetypes whose Assoc. Item the engine reads (wbMGEFAssocItemDecider).
# Writing a FormID under any OTHER archetype is meaningless, and xEdit flags
# it, so the field is zeroed unless the archetype is in this map.  The value
# is the record type the engine expects there.
ARCHETYPE_ASSOC_KIND = {
    A_LIGHT: 'LIGH',
    A_BOUND_WEAPON: 'ITEM',        # WEAP or ARMO
    A_SUMMON_CREATURE: 'NPC_',
    25: 'HAZD',                    # Guide
    A_PEAK_VALUE_MODIFIER: 'KYWD',
    A_CLOAK: 'SPEL',
    36: 'RACE',                    # Werewolf
    39: 'ENCH',                    # Enhance Weapon
    40: 'HAZD',                    # Spawn Hazard
    46: 'RACE',                    # Vampire Lord
}

# --- TES5 actor values (xEdit wbActorValueEnum) --------------------------
AV_NONE = -1
AV_ONE_HANDED = 6
AV_TWO_HANDED = 7
AV_ARCHERY = 8
AV_BLOCK = 9
AV_SMITHING = 10
AV_HEAVY_ARMOR = 11
AV_LIGHT_ARMOR = 12
AV_PICKPOCKET = 13
AV_LOCKPICKING = 14
AV_SNEAK = 15
AV_ALCHEMY = 16
AV_SPEECH = 17
AV_ALTERATION = 18
AV_CONJURATION = 19
AV_DESTRUCTION = 20
AV_ILLUSION = 21
AV_RESTORATION = 22
AV_ENCHANTING = 23
AV_HEALTH = 24
AV_MAGICKA = 25
AV_STAMINA = 26
AV_HEAL_RATE = 27
AV_MAGICKA_RATE = 28
AV_STAMINA_RATE = 29
AV_SPEED_MULT = 30
AV_CARRY_WEIGHT = 32
AV_CRITICAL_CHANCE = 33
AV_MELEE_DAMAGE = 34
AV_UNARMED_DAMAGE = 35
AV_DAMAGE_RESIST = 39
AV_POISON_RESIST = 40
AV_RESIST_FIRE = 41
AV_RESIST_SHOCK = 42
AV_RESIST_FROST = 43
AV_RESIST_MAGIC = 44
AV_RESIST_DISEASE = 45
AV_PARALYSIS = 53
AV_INVISIBILITY = 54
AV_NIGHT_EYE = 55
AV_DETECT_LIFE_RANGE = 56
AV_WATER_BREATHING = 57
AV_WATER_WALKING = 58

# TES4 magic school (DATA.School) → TES5 skill actor value.
# Mysticism has no Skyrim counterpart; Oblivion's mysticism effects (Dispel,
# Detect Life, Soul Trap, Telekinesis, Reflect, Spell Absorption) are split in
# Skyrim between Alteration and Conjuration.  Alteration is the closer home for
# the majority (Detect Life and Telekinesis are literally Alteration spells in
# Skyrim), so the school folds there and the handful that belong elsewhere are
# overridden per-code in EFFECT_ARCHETYPES below.
SCHOOL_TO_AV = {
    0: AV_ALTERATION,
    1: AV_CONJURATION,
    2: AV_DESTRUCTION,
    3: AV_ILLUSION,
    4: AV_ALTERATION,     # Mysticism → Alteration
    5: AV_RESTORATION,
}

# TES4 attribute index (EFIT ActorValue when UseAttribute is set) → TES5 AV.
# Oblivion's eight attributes have no direct Skyrim analogue; each maps to the
# derived stat it governed.  Strength drove carry weight and melee damage;
# Intelligence magicka; Willpower magicka regen; Agility/Speed stamina and
# movement; Endurance health; Personality barter (→ Speech); Luck crit.
ATTRIBUTE_TO_AV = {
    0: AV_CARRY_WEIGHT,    # Strength
    1: AV_MAGICKA,         # Intelligence
    2: AV_MAGICKA_RATE,    # Willpower
    3: AV_STAMINA,         # Agility
    4: AV_SPEED_MULT,      # Speed
    5: AV_HEALTH,          # Endurance
    6: AV_SPEECH,          # Personality
    7: AV_CRITICAL_CHANCE,  # Luck
}

# TES4 skill index (12..32, EFIT ActorValue when UseSkill is set) → TES5 AV.
SKILL_TO_AV = {
    12: AV_SMITHING,       # Armorer
    13: AV_STAMINA,        # Athletics (no Skyrim skill)
    14: AV_ONE_HANDED,     # Blade
    15: AV_BLOCK,          # Block
    16: AV_TWO_HANDED,     # Blunt
    17: AV_UNARMED_DAMAGE,  # Hand to Hand
    18: AV_HEAVY_ARMOR,    # Heavy Armor
    19: AV_ALCHEMY,        # Alchemy
    20: AV_ALTERATION,     # Alteration
    21: AV_CONJURATION,    # Conjuration
    22: AV_DESTRUCTION,    # Destruction
    23: AV_ILLUSION,       # Illusion
    24: AV_ALTERATION,     # Mysticism → Alteration
    25: AV_RESTORATION,    # Restoration
    26: AV_STAMINA,        # Acrobatics (no Skyrim skill)
    27: AV_LIGHT_ARMOR,    # Light Armor
    28: AV_ARCHERY,        # Marksman
    29: AV_SPEECH,         # Mercantile
    30: AV_LOCKPICKING,    # Security
    31: AV_SNEAK,          # Sneak
    32: AV_SPEECH,         # Speechcraft
}

# TES4 ResistValue is an Oblivion actor-value index naming the resistance that
# opposes this effect.  Only the handful Skyrim also has are meaningful.
TES4_RESIST_AV_TO_TES5 = {
    61: AV_RESIST_FIRE,      # ResistFire
    62: AV_RESIST_FROST,     # ResistFrost
    63: AV_RESIST_DISEASE,   # ResistDisease
    64: AV_RESIST_MAGIC,     # ResistMagic
    66: AV_PARALYSIS,        # ResistParalysis → Paralysis AV
    67: AV_POISON_RESIST,    # ResistPoison
    68: AV_RESIST_SHOCK,     # ResistShock
}


# ---------------------------------------------------------------------------
# TES4 effect code → (archetype, actor value)
#
# `None` for the actor value means "derive it from the effect's own EFIT
# ActorValue" (the attribute/skill-targeted families) — resolved per-effect in
# resolve_actor_value(), because the SAME MGEF (e.g. DGAT Damage Attribute) is
# used with a different attribute by every spell that carries it.
#
# EVERY key here is validated against export/*/MGEF.txt by
# tests/test_import.py::TestMgefArchetypeTable — a plausible 4-char code that
# no Oblivion or Nehrim record uses is a bug, not coverage (17 of the old
# vanilla-alias table's 100 entries were exactly that).
# ---------------------------------------------------------------------------
DERIVE_AV = 'derive'

EFFECT_ARCHETYPES = {
    # -- Summons: AssocItem is the creature ---------------------------------
    # Z001-Z020 plus the named daedra/undead codes.  All carry UseCreature and
    # an AssocItem resolving to a CREA (33), NPC_ (4) or LVLC (2).
    **{code: (A_SUMMON_CREATURE, AV_NONE) for code in (
        'Z001', 'Z002', 'Z003', 'Z004', 'Z005', 'Z006', 'Z007', 'Z008',
        'Z009', 'Z010', 'Z011', 'Z012', 'Z013', 'Z014', 'Z015', 'Z016',
        'Z017', 'Z018', 'Z019', 'Z020',
        'ZCLA', 'ZDAE', 'ZDRE', 'ZDRL', 'ZFIA', 'ZFRA', 'ZGHO', 'ZHDZ',
        'ZLIC', 'ZSCA', 'ZSKA', 'ZSKC', 'ZSKE', 'ZSKH', 'ZSPD', 'ZSTA',
        'ZWRA', 'ZWRL', 'ZXIV', 'ZZOM',
    )},

    # -- Bound weapons and armor: AssocItem is the WEAP/ARMO ----------------
    # Archetype 17 covers both (wbMGEFAssocItemDecider accepts [WEAP, ARMO]);
    # the engine equips whatever the item is for the effect's duration.
    **{code: (A_BOUND_WEAPON, AV_NONE) for code in (
        'BWAX', 'BWBO', 'BWDA', 'BWMA', 'BWSW',
        'BW01', 'BW02', 'BW03', 'BW04', 'BW05', 'BW06', 'BW07', 'BW08',
        'BW09', 'BW10',
        'BABO', 'BACU', 'BAGA', 'BAGR', 'BAHE', 'BASH',
        'BA01', 'BA02', 'BA03', 'BA04', 'BA05', 'BA06', 'BA07', 'BA08',
        'BA09', 'BA10',
        'MYHL', 'MYTH',
    )},

    # -- Alteration ---------------------------------------------------------
    'BRDN': (A_VALUE_MODIFIER, AV_CARRY_WEIGHT),     # Burden (detrimental)
    'FTHR': (A_VALUE_MODIFIER, AV_CARRY_WEIGHT),     # Feather
    'FISH': (A_DUAL_VALUE_MODIFIER, AV_DAMAGE_RESIST),   # Fire Shield
    'FRSH': (A_DUAL_VALUE_MODIFIER, AV_DAMAGE_RESIST),   # Frost Shield
    'LISH': (A_DUAL_VALUE_MODIFIER, AV_DAMAGE_RESIST),   # Shock Shield
    'SHLD': (A_VALUE_MODIFIER, AV_DAMAGE_RESIST),    # Shield
    'LOCK': (A_LOCK, AV_NONE),
    'OPEN': (A_OPEN, AV_NONE),
    'WABR': (A_PEAK_VALUE_MODIFIER, AV_WATER_BREATHING),
    'WAWA': (A_PEAK_VALUE_MODIFIER, AV_WATER_WALKING),

    # -- Conjuration --------------------------------------------------------
    'REAN': (A_REANIMATE, AV_NONE),
    'TURN': (A_TURN_UNDEAD, AV_NONE),

    # -- Destruction: damage / drain ----------------------------------------
    # Damage is a one-shot subtraction (Value Modifier); Drain is a temporary
    # reduction that restores when the effect ends — Skyrim expresses that as
    # Peak Value Modifier, which tracks and reverses its own contribution.
    'DGHE': (A_VALUE_MODIFIER, AV_HEALTH),
    'DGFA': (A_VALUE_MODIFIER, AV_STAMINA),
    'DGSP': (A_VALUE_MODIFIER, AV_MAGICKA),
    'DGAT': (A_VALUE_MODIFIER, DERIVE_AV),
    'DRHE': (A_PEAK_VALUE_MODIFIER, AV_HEALTH),
    'DRFA': (A_PEAK_VALUE_MODIFIER, AV_STAMINA),
    'DRSP': (A_PEAK_VALUE_MODIFIER, AV_MAGICKA),
    'DRAT': (A_PEAK_VALUE_MODIFIER, DERIVE_AV),
    'DRSK': (A_PEAK_VALUE_MODIFIER, DERIVE_AV),
    'FIDG': (A_VALUE_MODIFIER, AV_HEALTH),
    'FRDG': (A_VALUE_MODIFIER, AV_HEALTH),
    'SHDG': (A_VALUE_MODIFIER, AV_HEALTH),
    'SUDG': (A_VALUE_MODIFIER, AV_HEALTH),
    'POSN': (A_VALUE_MODIFIER, AV_HEALTH),
    'DISE': (A_VALUE_MODIFIER, AV_HEALTH),
    'DUMY': (A_VALUE_MODIFIER, AV_HEALTH),
    # Disintegrate Armor/Weapon degraded equipment condition — Skyrim has no
    # item condition at all, so the closest surviving meaning is the combat
    # consequence: less armor rating / less melee damage while it lasts.
    'DIAR': (A_PEAK_VALUE_MODIFIER, AV_DAMAGE_RESIST),
    'DIWE': (A_PEAK_VALUE_MODIFIER, AV_MELEE_DAMAGE),
    # Stunted Magicka suppressed regeneration entirely.
    'STMA': (A_PEAK_VALUE_MODIFIER, AV_MAGICKA_RATE),
    'VAMP': (A_PEAK_VALUE_MODIFIER, AV_HEALTH),

    # -- Destruction: weaknesses (negative resistance) ----------------------
    'WKFI': (A_PEAK_VALUE_MODIFIER, AV_RESIST_FIRE),
    'WKFR': (A_PEAK_VALUE_MODIFIER, AV_RESIST_FROST),
    'WKSH': (A_PEAK_VALUE_MODIFIER, AV_RESIST_SHOCK),
    'WKMA': (A_PEAK_VALUE_MODIFIER, AV_RESIST_MAGIC),
    'WKPO': (A_PEAK_VALUE_MODIFIER, AV_POISON_RESIST),
    'WKDI': (A_PEAK_VALUE_MODIFIER, AV_RESIST_DISEASE),
    'WKNW': (A_PEAK_VALUE_MODIFIER, AV_DAMAGE_RESIST),

    # -- Illusion -----------------------------------------------------------
    'CALM': (A_CALM, AV_NONE),
    'CHRM': (A_CALM, AV_NONE),          # Charm raised disposition → pacify
    'DEMO': (A_DEMORALIZE, AV_NONE),
    'FRNZ': (A_FRENZY, AV_NONE),
    'RALY': (A_RALLY, AV_NONE),
    'COCR': (A_COMMAND_SUMMONED, AV_NONE),
    'COHU': (A_COMMAND_SUMMONED, AV_NONE),
    'INVI': (A_PEAK_VALUE_MODIFIER, AV_INVISIBILITY),
    'CHML': (A_PEAK_VALUE_MODIFIER, AV_INVISIBILITY),
    'LGHT': (A_LIGHT, AV_NONE),
    'NEYE': (A_PEAK_VALUE_MODIFIER, AV_NIGHT_EYE),
    'DARK': (A_PEAK_VALUE_MODIFIER, AV_NIGHT_EYE),
    'PARA': (A_PARALYSIS, AV_PARALYSIS),
    # Silence blocked spellcasting. Skyrim has no silence archetype; draining
    # magicka to nothing is the closest engine-native equivalent.
    'SLNC': (A_PEAK_VALUE_MODIFIER, AV_MAGICKA),

    # -- Mysticism ----------------------------------------------------------
    'DSPL': (A_DISPEL, AV_NONE),
    'DTCT': (A_DETECT_LIFE, AV_DETECT_LIFE_RANGE),
    'TELE': (A_TELEKINESIS, AV_NONE),
    'STRP': (A_SOUL_TRAP, AV_NONE),
    'SABS': (A_PEAK_VALUE_MODIFIER, AV_RESIST_MAGIC),
    'RFLC': (A_PEAK_VALUE_MODIFIER, AV_RESIST_MAGIC),
    'REDG': (A_PEAK_VALUE_MODIFIER, AV_DAMAGE_RESIST),

    # -- Restoration: restore / fortify / absorb ----------------------------
    'REHE': (A_VALUE_MODIFIER, AV_HEALTH),
    'REFA': (A_VALUE_MODIFIER, AV_STAMINA),
    'RESP': (A_VALUE_MODIFIER, AV_MAGICKA),
    'REAT': (A_VALUE_MODIFIER, DERIVE_AV),
    'FOHE': (A_PEAK_VALUE_MODIFIER, AV_HEALTH),
    'FOFA': (A_PEAK_VALUE_MODIFIER, AV_STAMINA),
    'FOSP': (A_PEAK_VALUE_MODIFIER, AV_MAGICKA),
    'FOMM': (A_PEAK_VALUE_MODIFIER, AV_MAGICKA),
    'FOAT': (A_PEAK_VALUE_MODIFIER, DERIVE_AV),
    'FOSK': (A_PEAK_VALUE_MODIFIER, DERIVE_AV),
    'ABHE': (A_ABSORB, AV_HEALTH),
    'ABFA': (A_ABSORB, AV_STAMINA),
    'ABSP': (A_ABSORB, AV_MAGICKA),
    'ABAT': (A_ABSORB, DERIVE_AV),
    'ABSK': (A_ABSORB, DERIVE_AV),

    # -- Restoration: resistances and cures ---------------------------------
    'RSFI': (A_PEAK_VALUE_MODIFIER, AV_RESIST_FIRE),
    'RSFR': (A_PEAK_VALUE_MODIFIER, AV_RESIST_FROST),
    'RSSH': (A_PEAK_VALUE_MODIFIER, AV_RESIST_SHOCK),
    'RSMA': (A_PEAK_VALUE_MODIFIER, AV_RESIST_MAGIC),
    'RSPO': (A_PEAK_VALUE_MODIFIER, AV_POISON_RESIST),
    'RSDI': (A_PEAK_VALUE_MODIFIER, AV_RESIST_DISEASE),
    'RSNW': (A_PEAK_VALUE_MODIFIER, AV_DAMAGE_RESIST),
    'RSPA': (A_PEAK_VALUE_MODIFIER, AV_PARALYSIS),
    'RSWD': (A_PEAK_VALUE_MODIFIER, AV_RESIST_MAGIC),
    'CUDI': (A_CURE_DISEASE, AV_NONE),
    'CUPO': (A_CURE_POISON, AV_NONE),
    'CUPA': (A_CURE_PARALYSIS, AV_NONE),

    # -- Script effect ------------------------------------------------------
    # Archetype 1 needs a VMAD carrying a Papyrus ActiveMagicEffect script to
    # do anything; until Phase 4 attaches one the effect still fires, still
    # holds its duration, and still answers HasMagicEffect — which is what the
    # converted IsSpellTarget polling looks for.
    'SEFF': (A_SCRIPT, AV_NONE),
}

#: Dual Value Modifier code -> its second actor value, raised by the same magnitude as the armor rating.
SECOND_ACTOR_VALUES = {
    'FISH': AV_RESIST_FIRE,
    'FRSH': AV_RESIST_FROST,
    'LISH': AV_RESIST_SHOCK,
}

# Effects whose TES4 school is misleading once the archetype is chosen.
# (Oblivion filed Turn Undead under Conjuration and Burden under Alteration;
# Skyrim's equivalents live in different schools.)
SCHOOL_OVERRIDES = {
    'TURN': AV_RESTORATION,   # Turn Undead is Restoration in Skyrim
    'STRP': AV_CONJURATION,   # Soul Trap is Conjuration in Skyrim
    'DSPL': AV_RESTORATION,   # Dispel: Restoration's counter to magic
    'REAN': AV_CONJURATION,
}

# Effects that must not be castable on their own — Oblivion uses them purely as
# information markers on items (no Self/Touch/Target flag set), and letting the
# player see them in the magic menu shows junk entries.
_MARKER_CODES = frozenset({'POSN', 'DISE', 'DUMY', 'VAMP', 'DARK'})


def is_derived(code: str, rec: dict) -> bool:
    """Whether this effect takes its actor value from the spell carrying it.

    Imported in-function: magic_morrowind reads this module's archetype and
    actor-value constants, so the dependency only runs one way at import.
    """
    from .magic_morrowind import MW_EFFECT_ARCHETYPES

    index = morrowind_index(rec)
    if index >= 0:
        return MW_EFFECT_ARCHETYPES.get(index, (None, None))[1] == DERIVE_AV
    return EFFECT_ARCHETYPES.get(code, (None, None))[1] == DERIVE_AV


def resolve_actor_value(code: str, effect_av: int, rec: dict = None) -> int:
    """TES5 actor value for an effect instance.

    ``effect_av`` is the per-effect EFIT ActorValue from the owning
    SPEL/ENCH/ALCH record — the attribute or skill the effect targets.  For
    codes marked DERIVE_AV in EFFECT_ARCHETYPES it decides the AV entirely
    (DGAT+Strength and DGAT+Endurance are different effects in Skyrim); for
    every other code the table's fixed AV wins.
    """
    from .magic_morrowind import mw_actor_value

    index = morrowind_index(rec) if rec else -1
    if index >= 0:
        return mw_actor_value(index, effect_av)
    entry = EFFECT_ARCHETYPES.get(code)
    if entry is None:
        return AV_NONE
    av = entry[1]
    if av != DERIVE_AV:
        return av
    if effect_av is None or effect_av < 0:
        return AV_NONE
    if effect_av >= 12:
        return SKILL_TO_AV.get(effect_av, AV_NONE)
    return ATTRIBUTE_TO_AV.get(effect_av, AV_NONE)


def morrowind_index(rec: dict) -> int:
    """This record's TES3 effect index, or -1 when it is not a Morrowind one."""
    raw = rec.get('MorrowindEffectIndex')
    return int(raw) if raw not in (None, '') else -1


def get_archetype(code: str, rec: dict = None) -> int:
    """TES5 archetype for one effect (Value Modifier if unknown).

    Imported in-function to break the cycle with magic_morrowind, which
    reads this module's archetype constants at import time.
    """
    from .magic_morrowind import mw_archetype

    index = morrowind_index(rec) if rec else -1
    if index >= 0:
        return mw_archetype(index)
    entry = EFFECT_ARCHETYPES.get(code)
    return entry[0] if entry else A_VALUE_MODIFIER


def _effect_flags(rec: dict) -> int:
    """This record's DATA.Flags in the TES4 bit layout.

    A Morrowind MGEF stores TES3's own bits, whose meanings diverge from the
    range flags onward, so they are translated before any reader sees them.
    """
    from .magic_morrowind import mw_tes4_flags

    flags = get_int(rec, 'DATA.Flags')
    if morrowind_index(rec) >= 0:
        return mw_tes4_flags(flags)
    return flags


def _base_actor_value(code: str, rec: dict) -> int:
    """The MGEF's own actor value, or AV_NONE when each effect decides it.

    An attribute/skill-targeted effect has no single actor value: it comes
    from whichever spell carries it, which is what the per-AV variants exist
    for.  The base record keeps AV_NONE so a variant is always what an item
    actually references.
    """
    from .magic_morrowind import MW_EFFECT_ARCHETYPES, NATIVE_NONE

    index = morrowind_index(rec)
    if index >= 0:
        entry = MW_EFFECT_ARCHETYPES.get(index)
        if entry is None or entry[1] in (DERIVE_AV, NATIVE_NONE):
            return AV_NONE
        return entry[1]
    entry = EFFECT_ARCHETYPES.get(code)
    return entry[1] if entry and entry[1] != DERIVE_AV else AV_NONE


def is_known_code(code: str) -> bool:
    return code in EFFECT_ARCHETYPES


def _convert_flags(t4: int, code: str, archetype: int) -> int:
    """TES4 MGEF DATA.Flags → TES5 MGEF DATA.Flags.

    The bit meanings diverge from bit 3 onward (TES4 0x8 is Magnitude Is
    Percent; TES5 0x8 is Snap to Navmesh), so this is a translation, never a
    mask-and-copy.  The TES4 Self/Touch/Target bits are NOT flags in TES5 —
    they become the Delivery field — and the Spellmaking/Enchanting/
    UseWeapon/UseArmor/UseCreature/UseSkill/UseAttribute bits describe how the
    CS editor treated the effect, which TES5 encodes in the archetype instead.
    """
    out = 0
    if t4 & T4_HOSTILE:
        out |= F_HOSTILE
    if t4 & T4_RECOVER:
        out |= F_RECOVER
    if t4 & T4_DETRIMENTAL:
        out |= F_DETRIMENTAL
    if t4 & T4_NO_DURATION:
        out |= F_NO_DURATION
    if t4 & T4_NO_MAGNITUDE:
        out |= F_NO_MAGNITUDE
    if t4 & T4_NO_AREA:
        out |= F_NO_AREA
    if t4 & T4_FX_PERSIST:
        out |= F_FX_PERSIST
    if t4 & T4_NO_HIT_EFFECT:
        out |= F_NO_HIT_EFFECT
    if t4 & T4_PERSIST_ON_DEATH:
        out |= F_NO_DEATH_DISPEL

    # Archetypes the engine drives entirely from the archetype class carry no
    # meaningful magnitude/area; vanilla sets the suppression bits so the item
    # card doesn't print "0 points".
    if archetype in (A_SUMMON_CREATURE, A_BOUND_WEAPON, A_LIGHT, A_CLOAK,
                     A_TELEKINESIS, A_OPEN, A_LOCK, A_DISPEL,
                     A_CURE_DISEASE, A_CURE_POISON, A_CURE_PARALYSIS):
        out |= F_NO_MAGNITUDE | F_NO_AREA

    # Information-only markers must never surface in the magic menu.
    if code in _MARKER_CODES:
        out |= F_HIDE_IN_UI

    return out | _power_affects(out)


def _power_affects(flags: int) -> int:
    """The Power Affects bit for magnitude, else for duration, else none.

    See: docs/commentary/tes5_import_magic.md#power-affects
    """
    if not flags & F_NO_MAGNITUDE:
        return F_POWER_AFFECTS_MAGNITUDE
    return 0 if flags & F_NO_DURATION else F_POWER_AFFECTS_DURATION


def _delivery_and_cast(t4_flags: int) -> tuple:
    """(casting type, delivery) from the TES4 Self/Touch/Target flags.

    wbCastEnum:     0 Constant Effect, 1 Fire and Forget, 2 Concentration, 3 Scroll
    wbDeliveryEnum: 0 Self, 1 Contact (touch), 2 Aimed, 3 Target Actor, 4 Target Location

    An Oblivion MGEF advertises *every* delivery it supports; the item that
    carries it picks one.  Skyrim's MGEF commits to a single delivery, so the
    most specific one the effect allows wins — Target beats Touch beats Self,
    matching what the spells that use the effect overwhelmingly do.
    """
    if t4_flags & T4_TARGET:
        return 1, 2       # Fire and Forget, Aimed
    if t4_flags & T4_TOUCH:
        return 1, 1       # Fire and Forget, Contact
    return 1, 0           # Fire and Forget, Self


# --- Projectile resolution ------------------------------------------------
# An Aimed magic item MUST reach a non-null Projectile through at least one of
# its effects or the engine null-derefs.  The chain, read out of the GOG 1.6.659
# exe (crash frames translated by tools/disasm/address_lib.py):
#
#   MagicItem::GetCostliestEffectItem  (0x10c9f0)
#       calls GetDelivery (EnchantmentItem vtable +0x2b8 -> `mov eax,[rcx+0xa0]`)
#       and, when delivery == 2 (Aimed), SKIPS every effect whose
#       EffectSetting+0xC8 (the MGEF Projectile) is null (0x10ca7c).
#       With every effect skipped it returns null.
#   The combat-AI item rating function (0x7fb6c0, crash at 0x7fb83e) calls it
#       for any ENCH/SPEL with delivery Aimed and does `mov rdi,[rax+0xC8]`
#       with NO null check -> EXCEPTION_ACCESS_VIOLATION reading 0xC8.
#
# So the crash is unconditional: any Aimed enchantment/spell whose effects all
# have Projectile=0 kills the game the moment an actor's combat AI rates it.
# (Repro: Nehrim "Stab des Frosts" / EnStaffFrostDamage, effect FRDG.)
#
# Vanilla census (references/Skyrim.esm) confirms the invariant with zero
# exceptions: 43/43 Aimed ENCH and 264/264 Aimed SPEL reach a projectile.
# Individual *effects* may have none (23 MGEFs do) — those are always secondary
# effects riding alongside a primary that supplies one — so the requirement is
# enforced per item, not per effect.
#
# The FormIDs below are Skyrim.esm PROJ records, chosen the way vanilla chooses
# them: the resist type (element) decides first, then the magic school, and the
# cast type selects the Fire-and-Forget vs Concentration variant.
# HealFakeProjectile is vanilla's own "this effect needs a projectile but has no
# visual of its own" stand-in (31 MGEFs use it), which makes it the right
# fallback rather than an invented record.
_PROJ_HEAL_FAKE = 0x00012FDC      # HealFakeProjectile — visual-less default
_PROJ_FIREBOLT = 0x00012E84       # FireboltProjectile01
_PROJ_FLAMES = 0x00012FCF         # FlamesProjectile (concentration)
_PROJ_FROST_ICICLE = 0x0002F774   # FrostIcicleProjectile01
_PROJ_FROST_SPRAY = 0x00018123    # FrostSprayProjectile01 (concentration)
_PROJ_SHOCK_BOLT = 0x00058E9C     # ShockBoltAim
_PROJ_SHOCK_CONC = 0x00034190     # ShockBoltConAim (concentration)
_PROJ_ABSORB_BEAM = 0x000ABEFD    # AbsorbBeam01 — magic-resisted / absorb
_PROJ_SPIDER_SPIT = 0x0004600A    # SpiderSpitProjectile — poison
_PROJ_ILLUSION = 0x0007331D       # Illusion01Projectile — beneficial illusion
_PROJ_ILLUSION_NEG = 0x00074796   # IllusionNeg01Projectile — hostile illusion
_PROJ_REANIMATE = 0x00075348      # ReanimateProjectile — conjuration
_PROJ_TURN_UNDEAD = 0x0004BE35    # TurnUndeadProjectile — restoration
_PROJ_PARALYZE = 0x0006EBC8       # ParalyzeProjectile — alteration

#: TES5 resist actor value (the effect's element) -> (fire-and-forget, concentration) projectile.
_PROJ_BY_RESIST = {
    AV_RESIST_FIRE: (_PROJ_FIREBOLT, _PROJ_FLAMES),
    AV_RESIST_FROST: (_PROJ_FROST_ICICLE, _PROJ_FROST_SPRAY),
    AV_RESIST_SHOCK: (_PROJ_SHOCK_BOLT, _PROJ_SHOCK_CONC),
    AV_RESIST_MAGIC: (_PROJ_ABSORB_BEAM, _PROJ_ABSORB_BEAM),
    AV_POISON_RESIST: (_PROJ_SPIDER_SPIT, _PROJ_SPIDER_SPIT),
}

# (magic school actor value) -> (fire-and-forget, concentration), used when the
# effect names no resist type.  Destruction with no element falls back to the
# neutral projectile rather than inventing one.
_PROJ_BY_SCHOOL = {
    AV_ALTERATION: (_PROJ_PARALYZE, _PROJ_PARALYZE),
    AV_CONJURATION: (_PROJ_REANIMATE, _PROJ_REANIMATE),
    AV_RESTORATION: (_PROJ_TURN_UNDEAD, _PROJ_TURN_UNDEAD),
}


# {output MGEF FormID: projectile FormID} for every MGEF this module emits.
# equipment._pack_effects consults it (through magic_effects.has_projectile) to
# decide whether an Aimed item already reaches a projectile; without it the
# converter's own effects are invisible to that check and every Aimed item made
# of them ships with the null-deref described above.
_emitted_projectiles: dict = {}

#: {output MGEF FormID: its source record}, so a variant can clone any base MGEF before the MGEF pass writes it.
_mgef_sources: dict = {}


def register_emitted_projectile(fid: int, projectile: int) -> None:
    """Record the projectile written into one emitted MGEF's DATA."""
    if fid:
        _emitted_projectiles[fid] = projectile


def source_record(fid: int):
    """The export record a base MGEF of this plugin converts from, or None."""
    return _mgef_sources.get(fid)


def data_projectile(data: bytes) -> int:
    """The projectile an MGEF DATA needs for its delivery: the one it carries, else a vanilla bolt."""
    delivery = struct.unpack_from('<I', data, O_DELIVERY)[0]
    if delivery not in (2, 4):
        return 0
    carried = struct.unpack_from('<I', data, O_PROJECTILE)[0]
    if carried:
        return carried
    flags = struct.unpack_from('<I', data, O_FLAGS)[0]
    return _resolve_projectile(
        delivery,
        struct.unpack_from('<I', data, O_CASTING_TYPE)[0],
        struct.unpack_from('<i', data, O_MAGIC_SKILL)[0],
        struct.unpack_from('<i', data, O_RESIST_VALUE)[0],
        bool(flags & F_HOSTILE))


def emitted_projectile(fid: int) -> int:
    """Projectile of an MGEF this module emitted (0 if none / not ours)."""
    return _emitted_projectiles.get(fid, 0)


def is_emitted_mgef(fid: int) -> bool:
    """True when ``fid`` is an MGEF this converter emitted."""
    return fid in _emitted_projectiles


def _resolve_projectile(delivery: int, cast_type: int, school: int,
                        resist: int, hostile: bool) -> int:
    """Projectile FormID for an MGEF, or 0 when the delivery needs none.

    Only Aimed (2) and Target Location (4) deliveries fly a projectile; Self,
    Contact and Target Actor resolve on the target directly, and vanilla leaves
    those null far more often than not.  Aimed is the delivery that crashes
    without one, so that is where a projectile is mandatory.
    """
    if delivery not in (2, 4):
        return 0

    conc = 1 if cast_type == 2 else 0

    pair = _PROJ_BY_RESIST.get(resist)
    if pair is not None:
        return pair[conc]

    if school == AV_ILLUSION:
        # Illusion splits on intent, not element: vanilla uses the "Neg" variant
        # for hostile effects (fear/frenzy) and the plain one for calm/courage.
        return _PROJ_ILLUSION_NEG if hostile else _PROJ_ILLUSION

    pair = _PROJ_BY_SCHOOL.get(school)
    if pair is not None:
        return pair[conc]

    return _PROJ_HEAL_FAKE


# --- AssocItem resolution -------------------------------------------------
# The converter needs to turn a TES4 AssocItem FormID into the FormID of the
# record type Skyrim's archetype expects.  Two cases need the whole-plugin
# view rather than the single MGEF record:
#   * summon targets that point at an LVLC (leveled creature list) — Skyrim's
#     Summon Creature takes an NPC_, so the list's first entry stands in;
#   * confirming a bound-item target really is a WEAP/ARMO.
# import_main registers the index before the MGEF pass runs.
_lvlc_first_entry: dict = {}
known_sigs: dict = {}


def set_assoc_item_index(lvlc_first: dict, formid_sigs: dict) -> None:
    """Register plugin-wide FormID information for AssocItem resolution.

    ``lvlc_first``   {output LVLC FormID: output FormID of its first entry}
    ``formid_sigs``  {output FormID: TES4 signature} for the types that can be
                     an AssocItem (CREA, NPC_, WEAP, ARMO, CLOT, LIGH, LVLC).
    """
    _lvlc_first_entry.clear()
    _lvlc_first_entry.update(lvlc_first)
    known_sigs.clear()
    known_sigs.update(formid_sigs)


def _resolve_assoc_item(fid: int, archetype: int, t4_flags: int) -> int:
    """AssocItem FormID for an archetype, or 0 when the archetype ignores it.

    Writing a creature FormID under a value-modifier archetype is meaningless
    (wbMGEFAssocItemDecider returns "Unused"), so anything not in
    ARCHETYPE_ASSOC_KIND is dropped rather than written blind.
    """
    kind = ARCHETYPE_ASSOC_KIND.get(archetype)
    if not kind or not fid:
        return 0

    sig = known_sigs.get(fid)

    if kind == 'NPC_':
        # Summons: a CREA converts to NPC_, an NPC_ stays one.  An LVLC becomes
        # an LVLN, which the Summon Creature archetype does not accept — use
        # the list's first entry so the spell still summons something.
        if sig == 'LVLC':
            return _lvlc_first_entry.get(fid, 0)
        if sig in ('CREA', 'NPC_'):
            return fid
        # Unknown signature: trust the TES4 UseCreature flag rather than
        # dropping a summon whose target lives in a master we did not index.
        return fid if t4_flags & T4_USE_CREATURE else 0

    if kind == 'ITEM':
        # Bound weapon/armor: WEAP stays WEAP, ARMO and CLOT both become ARMO.
        if sig in ('WEAP', 'ARMO', 'CLOT'):
            return fid
        return fid if t4_flags & (T4_USE_WEAPON | T4_USE_ARMOR) else 0

    if kind == 'LIGH':
        return fid if sig == 'LIGH' else 0

    # HAZD/KYWD/SPEL/RACE/ENCH archetypes are never produced by this converter.
    return 0


def build_data(rec: dict, code: str, archetype: int, actor_value: int,
                counter_count: int) -> bytes:
    """The 152-byte TES5 MGEF DATA for one effect.

    ``counter_count`` must equal the ESCE subrecords the record carries, or the
    CK reads garbage counter slots.  TES4 ResistValue 0xFFFFFFFF means none.
    The projectile is mandatory for Aimed delivery (see _resolve_projectile);
    Dual Cast Scale is 1.0 on every vanilla MGEF, and 0.0 makes dual casting
    collapse the effect to nothing.
    """
    t4_flags = _effect_flags(rec)
    cast_type, delivery = _delivery_and_cast(t4_flags)

    data = bytearray(MGEF_DATA_SIZE)
    struct.pack_into('<I', data, O_FLAGS, _convert_flags(t4_flags, code, archetype))
    struct.pack_into('<f', data, O_BASE_COST, get_float(rec, 'DATA.BaseCost'))
    struct.pack_into('<I', data, O_ASSOC_ITEM,
                     _resolve_assoc_item(get_formid(rec, 'DATA.AssocItem'),
                                         archetype, t4_flags))
    school = SCHOOL_OVERRIDES.get(
        code, SCHOOL_TO_AV.get(get_int(rec, 'DATA.School', -1), AV_NONE))
    struct.pack_into('<i', data, O_MAGIC_SKILL, school)
    resist = TES4_RESIST_AV_TO_TES5.get(
        get_int(rec, 'DATA.ResistValue', 0xFFFFFFFF), AV_NONE)
    struct.pack_into('<i', data, O_RESIST_VALUE, resist)
    own_bolt = magic_art.projectile(rec) if delivery in (2, 4) else 0
    struct.pack_into('<I', data, O_PROJECTILE, own_bolt or _resolve_projectile(
        delivery, cast_type, school, resist, bool(t4_flags & T4_HOSTILE)))
    struct.pack_into('<II', data, O_CASTING_ART, magic_art.casting_art(rec),
                     magic_art.hit_art(rec))
    struct.pack_into('<H', data, O_COUNTER_COUNT, counter_count)
    struct.pack_into('<I', data, O_CASTING_LIGHT, get_formid(rec, 'DATA.Light'))
    struct.pack_into('<I', data, O_HIT_SHADER, get_formid(rec, 'DATA.EffectShader'))
    struct.pack_into('<I', data, O_ENCHANT_SHADER, get_formid(rec, 'DATA.EnchantEffect'))
    struct.pack_into('<f', data, O_SPELLMAKING_TIME, 0.5)
    struct.pack_into('<I', data, O_ARCHETYPE, archetype)
    struct.pack_into('<i', data, O_ACTOR_VALUE, actor_value)
    struct.pack_into('<I', data, O_CASTING_TYPE, cast_type)
    struct.pack_into('<I', data, O_DELIVERY, delivery)
    second_av = SECOND_ACTOR_VALUES.get(code, AV_NONE)
    struct.pack_into('<i', data, O_SECOND_AV, second_av)
    if second_av != AV_NONE:
        struct.pack_into('<f', data, O_SECOND_AV_WEIGHT, 1.0)
    struct.pack_into('<f', data, O_DUAL_CAST_SCALE, 1.0)
    return bytes(data)


def mgef_parts(rec: dict) -> tuple:
    """(EditorID, subrecords before DATA, DATA, subrecords after ESCE) of one source MGEF.

    TES5 order: EDID VMAD FULL MDOB KSIZ/KWDA DATA ESCE* SNDD DNAM CTDA.  The
    split is what lets a variant clone the record with only its DATA changed.
    """
    from ..base.object_scripts import get_object_vmad

    code = get_str(rec, 'EditorID')
    head = get_object_vmad(get_formid(rec, 'FormID'))
    full = get_str(rec, 'FULL')
    if full:
        head += pack_string_subrecord('FULL', full)
    data = build_data(rec, code, get_archetype(code, rec),
                       _base_actor_value(code, rec),
                       len(_counter_effect_fids(rec)))
    return code, head, data, mgef_tail(rec)


def mgef_tail(rec: dict) -> bytes:
    """The subrecords after ESCE: the sound set, then the description."""
    desc = get_str(rec, 'DESC')
    return magic_art.sound_set(rec) + (pack_string_subrecord('DNAM', desc) if desc else b'')


def convert_MGEF(rec: dict, writer=None) -> bytes:
    """MGEF — Magic Effect, packed from `mgef_parts` plus its ESCE array.

    The projectile in this DATA was already registered by
    register_mgef_formids: ENCH and SPEL convert before MGEF.
    """
    code, head, data, tail = mgef_parts(rec)
    subs = pack_string_subrecord('EDID', code) if code else b''
    subs += head + pack_subrecord('DATA', data)
    for fid in _counter_effect_fids(rec):
        subs += pack_formid_subrecord('ESCE', fid)
    subs += tail
    return pack_record('MGEF', get_formid(rec, 'FormID'),
                       get_int(rec, 'RecordFlags'), subs)


#: TES4 effect code -> this plugin's MGEF FormID (output space), filled before the MGEF pass.
code_to_fid: dict = {}


def register_mgef_formids(mgef_records: list) -> None:
    """Index every source MGEF by code and FormID, and reset the per-plugin registries.

    Phase 1 converts types alphabetically, so ENCH and SPEL run before MGEF:
    their projectile check and every variant clone read what is indexed here,
    never a side effect of convert_MGEF.  magic_variants is imported here, not
    at module scope, because it imports this module.
    """
    from .magic_variants import reset

    reset()
    code_to_fid.clear()
    _emitted_projectiles.clear()
    _mgef_sources.clear()
    for rec in mgef_records:
        code = get_str(rec, 'EditorID')
        if not code:
            continue
        fid = get_formid(rec, 'FormID')
        code_to_fid[code] = fid
        _mgef_sources[fid] = rec
        register_emitted_projectile(fid, data_projectile(
            build_data(rec, code, get_archetype(code, rec), AV_NONE, 0)))


def _counter_effect_fids(rec: dict) -> list:
    """ESCE targets as output FormIDs, dropping codes with no MGEF of ours."""
    out = []
    for i in range(get_int(rec, 'CounterEffects')):
        fid = code_to_fid.get(get_str(rec, f'ESCE[{i}]'))
        if fid and fid not in out:
            out.append(fid)
    return out
