"""
Constants, lookup tables, and dispatch maps for TES4→TES5 conversion.
"""

# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------

# Oblivion race EditorID → Skyrim race FormID (from skyrim_overrides)
from .skyrim_overrides import DEFAULT_RACE, RACE_MAP

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

# TES4 ENCH type → TES5 cast type
ENCH_CAST_TYPE_MAP = {
    0: 4,  # Scroll → Scroll
    1: 2,  # Staff → Fire and Forget
    2: 2,  # Weapon → Fire and Forget
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

# LTEX material type → Skyrim MATT FormID
MATT_MAP = {
    0: 0x00012F34,   # Stone
    1: 0x00012F38,   # Dirt
    2: 0x00012F3A,   # Grass
    3: 0x00012F42,   # HeavyWood (Glass approximation)
    4: 0x00012F3B,   # Metal
    5: 0x00012F3F,   # Wood
    6: 0x00012F3C,   # Organic
    7: 0x00012F3D,   # Skin
    8: 0x00012F3E,   # Water
    9: 0x00012F37,   # Cloth (Book approximation)
    10: 0x00012F44,  # Snow
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
# Minimum bounding box dimension (in game units) for the Show-in-World-Map flag
# (RecordFlags |= 0x10000000).  Only very large landmarks warrant a world map
# marker — castle walls, large towers, major ruins.
WORLD_MAP_SIZE_THRESHOLD = 1024

def map_lock_level(tes4_level: int) -> int:
    if tes4_level <= 20:
        return 1   # Novice
    elif tes4_level <= 40:
        return 25  # Apprentice
    elif tes4_level <= 60:
        return 50  # Adept
    elif tes4_level <= 80:
        return 75  # Expert
    else:
        return 100  # Master


# ---------------------------------------------------------------------------
# Dispatch maps — populated by record_types submodules
# ---------------------------------------------------------------------------

# Populated at end of module after all imports
IMPORT_DISPATCH = {}
TYPE_MAP = {}
SKIP_TYPES = set()


def _init_dispatch():
    """Initialize dispatch tables. Called after record_types are imported."""
    from .record_types.actors import (
        convert_CLAS,
        convert_CREA,
        convert_EYES,
        convert_FACT,
        convert_GLOB,
        convert_GMST,
        convert_HAIR,
        convert_LVLC,
        convert_LVLI,
        convert_LVSP,
        convert_NPC_,
    )
    from .dialog_converter import (
        convert_DIAL,
        convert_INFO,
        convert_QUST,
    )
    from .record_types.dialog_misc import (
        convert_PACK,
        convert_WTHR,
    )
    from .record_types.equipment import (
        convert_ALCH,
        convert_AMMO,
        convert_APPA,
        convert_ARMO,
        convert_BOOK,
        convert_CLOT,
        convert_ENCH,
        convert_INGR,
        convert_SGST,
        convert_SPEL,
        convert_WEAP,
    )
    from .record_types.items import (
        convert_ACTI,
        convert_ANIO,
        convert_CONT,
        convert_DOOR,
        convert_FLOR,
        convert_FURN,
        convert_GRAS,
        convert_KEYM,
        convert_LIGH,
        convert_MISC,
        convert_SLGM,
        convert_STAT,
        convert_TREE,
    )
    from .record_types.world import (
        convert_ACHR,
        convert_ACRE,
        convert_CELL,
        convert_EFSH,
        convert_LAND,
        convert_LSCR,
        convert_REFR,
        convert_REGN,
        convert_WATR,
        convert_WRLD,
    )
    from .pgrd_to_navm import convert_PGRD

    IMPORT_DISPATCH.update({
        # Simple objects
        'STAT': convert_STAT,
        'ACTI': convert_ACTI,
        'MISC': convert_MISC,
        'KEYM': convert_KEYM,
        'DOOR': convert_DOOR,
        'FLOR': convert_FLOR,
        'FURN': convert_FURN,
        'GRAS': convert_GRAS,
        'TREE': convert_TREE,
        'LIGH': convert_LIGH,
        'SLGM': convert_SLGM,
        'ANIO': convert_ANIO,
        'CONT': convert_CONT,
        'SBSP': convert_STAT,
        # Equipment
        'WEAP': convert_WEAP,
        'ARMO': convert_ARMO,
        'CLOT': convert_CLOT,
        'AMMO': convert_AMMO,
        'BOOK': convert_BOOK,
        'ENCH': convert_ENCH,
        'SPEL': convert_SPEL,
        'ALCH': convert_ALCH,
        'INGR': convert_INGR,
        'SGST': convert_SGST,
        'APPA': convert_APPA,
        # Actors
        'NPC_': convert_NPC_,
        'CREA': convert_CREA,
        'FACT': convert_FACT,
        'EYES': convert_EYES,
        'HAIR': convert_HAIR,
        'CLAS': convert_CLAS,
        'GLOB': convert_GLOB,
        'GMST': convert_GMST,
        # Leveled lists
        'LVLI': convert_LVLI,
        'LVLC': convert_LVLC,
        'LVSP': convert_LVSP,
        # World
        'CELL': convert_CELL,
        'WRLD': convert_WRLD,
        'REFR': convert_REFR,
        'ACHR': convert_ACHR,
        'ACRE': convert_ACRE,
        'LAND': convert_LAND,
        'REGN': convert_REGN,
        'LSCR': convert_LSCR,
        'EFSH': convert_EFSH,
        'PGRD': convert_PGRD,
        # Dialog
        'QUST': convert_QUST,
        'DIAL': convert_DIAL,
        'INFO': convert_INFO,
        # Newly implemented
        'PACK': convert_PACK,
        'WATR': convert_WATR,
        'WTHR': convert_WTHR,
    })

    TYPE_MAP.update({
        'CREA': 'NPC_',
        'CLOT': 'ARMO',
        'LVLC': 'LVLN',
        'HAIR': 'HDPT',
        'SGST': 'SCRL',
        'APPA': 'MISC',
        'SBSP': 'STAT',
        'ACRE': 'ACHR',
    })

    SKIP_TYPES.update({
        'ROAD',   # Roads → NavMesh (not enough structured data for conversion)
        'SCPT',   # Scripts → Papyrus (can't auto-convert)
        'SKIL',   # Hardcoded in TES5
        'BSGN',   # Birthsigns → no equivalent
        'RACE',   # NPCs map to Skyrim races
        'MGEF',   # Magic Effect -> Completely restructured
        'CSTY',   # Combat Style -> Completely restructured
        'IDLE',   # Animation system different
        'GMST',   # Game settings differ between TES4/TES5
        # GLOB is NOT skipped: converted scripts bind GlobalVariable properties
        # to TES4 globals (TES4Fame, quest counters...), which read None if the
        # records don't exist. convert_GLOB drops the engine-time globals
        # (GameHour etc.) whose references are canonicalized to vanilla forms.
        'CLMT',   # Climate system differs
        'REGN',   # Region system differs
        'EYES',   # Do not convert — NPCs map to Skyrim head parts
        'HAIR',   # Do not convert — NPCs map to Skyrim head parts
        'PACK',   # Causes startup to not finish. Will implement later.
    })


# Initialize on import
_init_dispatch()
