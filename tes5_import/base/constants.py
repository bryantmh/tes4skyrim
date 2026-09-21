"""
Constants, lookup tables, and dispatch maps for TES4→TES5 conversion.
"""

# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------

from .equivalents import DEFAULT_RACE, RACE_MAP

#: Re-exported from `equivalents` so race lookups keep one import site.
__all__ = ['DEFAULT_RACE', 'RACE_MAP']

# Engine-owned globals, keyed by lowercase EditorID.  Skyrim ships these at the
# SAME FormIDs Oblivion uses (GameYear 0x35 ... TimeScale 0x3A — verified
# against both GLOB dumps), and convert_GLOB deliberately does NOT re-emit them,
# so there is no record of our own for a VMAD property to bind to.  Like Player
# (0x14) they must therefore stay UNSHIFTED: running them through the
# load-order remap produced e.g. `1A000038`, a form that does not exist, and
# the property silently bound to None.  `GameHour.GetValue()` then returned 0.0
# forever — which sits inside every `GameHour <= 0.02` hour-boundary window, so
# Nehrim's chapel bell re-fired on a permanent loop.  Confirmed in
# Papyrus.0.log:
#   "Property Gamehour ... cannot be bound because <nullptr form> (1A000038)
#    is not the right type", then "Cannot call GetValue() on a None object".
ENGINE_GLOBAL_FORMIDS = {
    'gameyear':       0x35,
    'gamemonth':      0x36,
    'gameday':        0x37,
    'gamehour':       0x38,
    'gamedayspassed': 0x39,
    'timescale':      0x3A,
}

# TES4 biped slot bit → TES5 biped slot bit (BOD2 first person flags)
BIPED_SLOT_MAP = {
    0: 0,    # Head → 30-Head
    1: 1,    # Hair → 31-Hair
    2: 2,    # Upper Body → 32-Body
    3: 14,   # Lower Body → 44-LowerBody (greaves get their own slot)
    4: 3,    # Hand → 33-Hands
    5: 7,    # Foot → 37-Feet
    6: 6,    # Right Ring → 36-Ring
    7: 6,    # Left Ring → 36-Ring (merged)
    8: 5,    # Amulet → 35-Amulet
    13: 9,   # Shield → 39-Shield
    15: 13,  # Tail → 43-Tail (was 43-Ears, corrected to unnamed/tail)
}

# Additional TES5 ARMO BOD2 slots to set when a TES4 slot is present.
# These are EQUIPMENT CONFLICTS: wearing a helmet should block circlets, etc.
# Applied in _convert_biped_flags() to ARMO BOD2 only.
# Derived from vanilla Skyrim armor sets (iron, steel, daedric, elven):
#   IronHelmet ARMO BOD2: Hair(31)+Circlet(42) = open-face
#   EbonyHelmet ARMO BOD2: Head(30)+Hair(31)+Circlet(42)+Ears(43) = full-face
# Slot 41 (LongHair) is ALSO covered (unlike vanilla helmets): slot 31 alone
# swaps the hair headpart to its "hairline" extra part, whose meshes carry
# partitions [141, 131] — vanilla helmets are modelled to enclose the
# hairline but tighter Oblivion helms are not, so it pokes through the shell.
# Covering 41 suppresses the 141 partitions too → all hair fully hidden.
BIPED_SLOT_EXTRA = {
    0: [1, 11, 12, 13],  # Head (full-face) → also Hair(31)+LongHair(41)+Circlet(42)+Ears(43)
    1: [11, 12],         # Hair (open-face helm) → also LongHair(41)+Circlet(42)
}

# Extra TES5 slots for ARMA body coverage.  ARMA records need to declare
# which body regions the mesh covers (beyond the primary equipment slot).
# This controls which body NIF partitions are HIDDEN when armor is equipped.
# Derived from vanilla Skyrim:
#   IronCuirassAA ARMA: Body(32) + ForeArms(34) + Calves(38)
#   IronBootsAA ARMA:   Feet(37) + Calves(38)
#   IronGlovesAA ARMA:  Hands(33) + ForeArms(34)
#   GreavesAA ARMA:     LowerBody(44) + Calves(38)
#   IronHelmetAA ARMA:  Hair(31) + Ears(43)  (hides character ears through helm)
ARMA_BODY_COVERAGE_EXTRA = {
    2: [4],      # Body (cuirass) → also 34-ForeArms
    3: [4],      # Hands (gauntlets) → also 34-ForeArms
    7: [8],      # Feet (boots) → also 38-Calves
    14: [8],     # LowerBody (greaves) → also 38-Calves
    # Hair (helmet ARMA slot) → also 41-LongHair + 43-Ears.  LongHair hides
    # the hairline headpart / long-hair strands (partition 141) that slot 31
    # alone leaves visible — they clip through tight Oblivion helm shells.
    1: [11, 13],
}

# TES4 weapon type → TES5 animation type
WEAPON_TYPE_MAP = {
    0: 1,  # Blade 1H → Sword
    1: 5,  # Blade 2H → Greatsword
    2: 4,  # Blunt 1H → Mace
    3: 6,  # Blunt 2H → Battleaxe
    4: 8,  # Staff → Staff
    5: 7,  # Bow → Bow
}

# TES4 ENCH type → TES5 ENCH type
ENCH_TYPE_MAP = {
    0: 6,   # Scroll → Enchantment
    1: 12,  # Staff → Staff Enchantment
    2: 6,   # Weapon → Enchantment
    3: 6,   # Apparel → Enchantment
}

# TES4 ENCH type → TES5 cast type (wbCastEnum: 0=Constant Effect, 1=Fire and
# Forget, 2=Concentration, 3=Scroll — verified against vanilla Skyrim.esm ENCH
# records: weapon/staff enchants use CastType=1, armor enchants use CastType=0)
ENCH_CAST_TYPE_MAP = {
    0: 3,  # Scroll → Scroll
    1: 1,  # Staff → Fire and Forget
    2: 1,  # Weapon → Fire and Forget
    3: 0,  # Apparel → Constant Effect
}

# Map marker type mapping (TES4 → TES5).
# TES4 enum (wbDefinitionsTES4.pas): 0 None, 1 Camp, 2 Cave, 3 City,
#   4 Elven Ruin, 5 Fort Ruin, 6 Mine, 7 Landmark, 8 Tavern, 9 Settlement,
#   10 Daedric Shrine, 11 Oblivion Gate, 12 Unknown (door icon).
# TES5 enum (wbDefinitionsTES5.pas): 0 None, 1 City, 2 Town, 3 Settlement,
#   4 Cave, 5 Camp, 6 Fort, 7 Nordic Ruins, 8 Dwemer Ruin, 11 Landmark,
#   13 Farm, 15 Mine, 34 Shrine, ...
MAP_MARKER_TYPE_MAP = {
    0: 0,    # None            → None
    1: 5,    # Camp            → Camp
    2: 4,    # Cave            → Cave
    3: 1,    # City            → City
    4: 8,    # Elven Ruin      → Dwemer Ruin (closest "ancient ruin" icon)
    5: 6,    # Fort Ruin       → Fort
    6: 15,   # Mine            → Mine
    7: 11,   # Landmark        → Landmark
    8: 2,    # Tavern          → Town (Skyrim has no inn icon; TES5 14 = Wood Mill)
    9: 3,    # Settlement      → Settlement
    10: 34,  # Daedric Shrine  → Shrine
    11: 34,  # Oblivion Gate   → Shrine (no gate icon in Skyrim)
    12: 11,  # Unknown (door)  → Landmark
}

# LCRT "MapMarkerRefType" in Skyrim.esm.  Every vanilla map-marker REFR carries
# this as its XLRT (Location Ref Type); it is what binds the reference to its
# Location as that location's map marker.
SKYRIM_MAP_MARKER_LCRT = 0x0010F63C

# Base object every Skyrim map marker REFR points at (STAT "MapMarker").
# Oblivion uses the same FormID for its MapMarker static.
SKYRIM_MAP_MARKER_STAT = 0x00000010

#: TES4 LTEX HNAM.Material enum -> Skyrim MATT FormID. See: docs/commentary/tes5_import_landscape.md#ltex-material-types
MATT_MAP = {
    0: 0x00012F34,   # Stone       → MaterialStone
    1: 0x00012F37,   # Cloth       → MaterialCloth
    2: 0x00012F38,   # Dirt        → MaterialDirt
    3: 0x00012F39,   # Glass       → MaterialGlass
    4: 0x00012F46,   # Grass       → MaterialGrass
    5: 0x00012F3C,   # Metal       → MaterialSolidMetal
    6: 0x00012F3D,   # Organic     → MaterialOrganic
    7: 0x00012F3F,   # Skin        → MaterialSkin
    8: 0x00012F40,   # Water       → MaterialWater
    9: 0x00043DCC,   # Wood        → MaterialWoodMedium
    10: 0x00012F36,  # Heavy Stone → MaterialHeavyStone
    11: 0x00012F3B,  # Heavy Metal → MaterialHeavyMetal
    12: 0x00012F42,  # Heavy Wood  → MaterialWoodHeavy
    13: 0x00012F3A,  # Chain       → MaterialChainMetal
    14: 0x00012F45,  # Snow        → MaterialSnow
    15: 0x0002EE2C,  # Stone Stairs  → MaterialStairsStone
    16: 0x0002EE2D,  # Cloth Stairs  → MaterialStairsWood (carpeted)
    17: 0x0002EE2C,  # Dirt Stairs   → MaterialStairsStone
    18: 0x000C6FB1,  # Glass Stairs  → MaterialGlassStairs
    19: 0x0002EE2C,  # Grass Stairs  → MaterialStairsStone
    20: 0x0002EE2C,  # Metal Stairs  → MaterialStairsStone
    21: 0x0002EE2D,  # Organic Stairs → MaterialStairsWood
    22: 0x0002EE2D,  # Skin Stairs   → MaterialStairsWood
    23: 0x0002EE2C,  # Water Stairs  → MaterialStairsStone
    24: 0x0002EE2D,  # Wood Stairs   → MaterialStairsWood
    25: 0x0002EE2C,  # Heavy Stone Stairs → MaterialStairsStone
    26: 0x0002EE2C,  # Heavy Metal Stairs → MaterialStairsStone
    27: 0x0002EE2D,  # Heavy Wood Stairs  → MaterialStairsWood
    28: 0x0002EE2C,  # Chain Stairs  → MaterialStairsStone
    29: 0x00052ED0,  # Snow Stairs   → MaterialStairsSnow
    30: 0x00012F3C,  # Elevator      → MaterialSolidMetal
    31: 0x00012F3D,  # Rubber        → MaterialOrganic
}

# TES4 skill index → TES5 skill name for NPC DNAM
TES4_SKILL_TO_TES5 = {
    12: 'Smithing',       # Armorer
    # 13: Athletics removed
    14: 'OneHanded',      # Blade
    15: 'Block',          # Block
    16: 'OneHanded',      # Blunt (merged with blade)
    17: 'OneHanded',      # Hand to Hand (merged)
    18: 'HeavyArmor',    # Heavy Armor
    19: 'Alchemy',        # Alchemy
    20: 'Alteration',     # Alteration
    21: 'Conjuration',    # Conjuration
    22: 'Destruction',    # Destruction
    23: 'Illusion',       # Illusion
    24: 'Illusion',       # Mysticism → Illusion
    25: 'Restoration',    # Restoration
    # 26: Acrobatics removed
    27: 'LightArmor',    # Light Armor
    28: 'Marksman',       # Marksman
    29: 'Pickpocket',     # Mercantile → Pickpocket
    30: 'Lockpicking',    # Security
    31: 'Sneak',          # Sneak
    32: 'Speechcraft',    # Speechcraft
}

# TES5 skill ordering in NPC_ DNAM Skill Values (18 skills)
TES5_SKILL_ORDER = [
    'OneHanded', 'TwoHanded', 'Marksman', 'Block', 'Smithing',
    'HeavyArmor', 'LightArmor', 'Pickpocket', 'Lockpicking', 'Sneak',
    'Alchemy', 'Speechcraft', 'Alteration', 'Conjuration', 'Destruction',
    'Illusion', 'Restoration', 'Enchanting',
]


# ---------------------------------------------------------------------------
# Lock level mapping
# ---------------------------------------------------------------------------

# Minimum bounding box dimension (in game units) that qualifies a STAT for the
# Visible-When-Distant LOD flag (RecordFlags |= 0x8000).  Any STAT whose OBND
# spans >= this value in any single axis (width, depth, or height) will receive
# the flag so SSELodGen generates distant LOD meshes for it.
#
# 1 Skyrim unit ≈ 1.4 cm; 512 units ≈ ~7 m — large architecture/terrain pieces.
# Tune upward to reduce LOD count, downward to include more mid-size objects.
LOD_SIZE_THRESHOLD = 256
# See: docs/commentary/ck_vs_game_missing_objects.md#no-show-in-world-map-flag

def map_lock_level(tes4_level: int, leveled: bool = False) -> int:
    """TES4 lock level -> TES5 lock level.

    Both games separate pickable lock tiers from "this needs a key", but
    encode the key-required state differently: TES4 uses level 100 (UESP: a
    level-100 lock "can only be opened with the proper key"; Oblivion.esm has
    353 locks at exactly 100 and none above), while TES5 keeps 100 as an
    ordinary pickable Master lock and uses 255 for Requires Key.  A LEVELED
    lock (XLOC flag 0x4) scales with the player instead of reading its level
    byte as a tier, so it never maps to Requires Key.
    """
    if tes4_level >= 100 and not leveled:
        return 255  # Requires Key
    elif tes4_level <= 20:
        return 1   # Novice
    elif tes4_level <= 40:
        return 25  # Apprentice
    elif tes4_level <= 60:
        return 50  # Adept
    elif tes4_level <= 80:
        return 75  # Expert
    else:
        return 100  # Master


#: FO3/FNV base objects Oblivion lacks -> the Skyrim type they reduce to; a missing one nulls its refs' base.
FALLOUT_BASE_TYPES = {
    'MSTT': 'STAT', 'SCOL': 'STAT', 'PWAT': 'STAT', 'IDLM': 'STAT',
    'ASPC': 'STAT', 'TERM': 'ACTI', 'NOTE': 'ACTI', 'TACT': 'ACTI',
}


# ---------------------------------------------------------------------------
# Ambient-dialogue pacing (GMST)
AMBIENT_GMST_OVERRIDES = {
    'fAIGreetingTimer':                     (20.0,   True),
    'fIdleChatterCommentTimer':             (100.0,  True),
    'fAISocialchanceForConversation':       (100.0,  True),
    'fAISocialRadiusToTriggerConversation': (1800.0, True),
}

