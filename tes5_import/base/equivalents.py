"""
Skyrim-specific overrides and value mappings.

This module contains mappings from Oblivion data to Skyrim equivalents
that require referencing Skyrim.esm records. These are the "speculative"
conversions that go beyond straightforward format translation.

Oblivion race EditorID/FormID → Skyrim race FormID,
NPC preset templates, voice types, head parts, hair colors, etc.
"""

import struct

from .tes5_reader import records
from .writer import pack_record, pack_string_subrecord, pack_subrecord

# ---------------------------------------------------------------------------
# Race mapping: Oblivion race EditorID → Skyrim race FormID
# ---------------------------------------------------------------------------

RACE_MAP = {
    'Argonian':      0x00013740,
    'Breton':        0x00013741,
    'DarkElf':       0x00013742,
    'HighElf':       0x00013743,
    'Imperial':      0x00013744,
    'Khajiit':       0x00013745,
    'Nord':          0x00013746,
    'Orc':           0x00013747,
    'Redguard':      0x00013748,
    'WoodElf':       0x00013749,
    # Shivering Isles playable-ish races.
    # The Golden Saint / Dark Seducer races only exist in the ccbgssse025
    # Creation ("Adventurer's Backpack"/AdvDSGS), which we cannot depend on as
    # a master.  DremoraRace was the previous stand-in, but it carries the
    # Dremora head mesh, horns and tint set, which suits neither.  Both are
    # golden-/dusk-skinned elves, so they map onto the base-game elf races:
    #   GoldenSaint → HighElfRace  (Skyrim.esm 0x00013743, verified in dump)
    #   DarkSeducer → DarkElfRace  (Skyrim.esm 0x00013742, verified in dump)
    # Dremora:     Skyrim.esm 0x000131F0 DremoraRace
    'GoldenSaint':   0x00013743,  # → HighElfRace (Skyrim.esm 0x00013743)
    'DarkSeducer':   0x00013742,  # → DarkElfRace (Skyrim.esm 0x00013742)
    'Dremora':       0x000131F0,  # → DremoraRace (Skyrim.esm 0x000131F0) — exact match
    'SEDremora':     0x000131F0,  # → DremoraRace (Skyrim.esm 0x000131F0)
    'Sheogorath':    0x00013744,  # → Imperial (no Sheogorath race in any source)
}
DEFAULT_RACE = 0x00013746  # Nord


# ---------------------------------------------------------------------------
# Creature race map: EditorID/name keyword → Skyrim race FormID
# ---------------------------------------------------------------------------

# Named creature FormIDs from all analyzed sources:
#   Skyrim.esm
_SKY_DREMORA         = 0x000131F0  # DremoraRace
_SKY_FLAME_ATRONACH  = 0x000131F5  # AtronachFlameRace
_SKY_FROST_ATRONACH  = 0x000131F6  # AtronachFrostRace
_SKY_STORM_ATRONACH  = 0x000131F7  # AtronachStormRace
_SKY_HAGRAVEN        = 0x000131FB  # HagravenRace
_SKY_ICEWRAITH       = 0x000131FE  # IceWraithRace
_SKY_SPRIGGAN        = 0x00013204  # SprigganRace
_SKY_TROLL           = 0x00013205  # TrollRace
_SKY_TROLL_FROST     = 0x00013206  # TrollFrostRace
_SKY_WISP            = 0x00013208  # WispRace
_SKY_DRAUGR          = 0x00000D53  # DraugrRace
_SKY_SKELETON        = 0x000B7998  # SkeletonRace
_SKY_FALMER          = 0x000131F4  # FalmerRace
_SKY_GIANT           = 0x000131F9  # GiantRace
_SKY_HORKER          = 0x000131FC  # HorkerRace
_SKY_HORSE           = 0x000131FD  # HorseRace
_SKY_GOAT            = 0x000131FA  # GoatRace (closest for deer/boar/sheep)
_SKY_ELK             = 0x000131ED  # ElkRace
_SKY_BEAR_BROWN      = 0x000131E7  # BearBrownRace
_SKY_BEAR_BLACK      = 0x000131E8  # BearBlackRace
_SKY_BEAR_SNOW       = 0x000131E9  # BearSnowRace
_SKY_MUDCRAB         = 0x000BA545   # MudcrabRace
_SKY_SABRECAT        = 0x00013200  # SabreCatRace
_SKY_SKEEVER         = 0x00013201  # SkeeverRace
_SKY_SPIDER          = 0x000131F8  # FrostbiteSpiderRace
_SKY_WOLF            = 0x000131EB  # WolfRace
_SKY_WEREWOLF        = 0x000CDD84  # WerewolfBeastRace (Dawnguard.esm 0x000CDD84)
_SKY_CHAURUS         = 0x000131F3  # ChaurusRace
_SKY_MAMMOTH         = 0x0001320A  # MammothRace (Skyrim.esm)
_SKY_RABBIT          = 0x00059339  # RabbitRace (Skyrim.esm)
_SKY_FOX             = 0x000A0EB2  # FoxRace (Skyrim.esm)
_SKY_DEER            = 0x000131ED  # ElkRace (closest for deer)
_SKY_DEFAULT         = 0x000B7998  # SkeletonRace (last-resort fallback)
#   CC ESLs — file-local FormIDs (prefix 05 is placeholder load-order byte)
_CC025_GOLDEN_SAINT  = 0x000816    # ccbgssse025-advdsgs.esm: ccBGSSSE025_GoldenSaintRace
_CC025_DARK_SEDUCER  = 0x000817    # ccbgssse025-advdsgs.esm: ccBGSSSE025_DarkSeducerRace
_CC025_ELYTRA        = 0x000A76    # ccbgssse025-advdsgs.esm: ccBGSSSE025_ElytraRace
_CC040_GOBLIN        = 0x000800    # ccbgssse040-advobgobs.esl: ccBGSSSE040_GoblinRace
_CC003_ZOMBIE        = 0x000D6B    # ccbgssse003-zombies.esl: ccBGSSSE003ZombieRace
#   BSAssets.esm (Beyond Skyrim)
_BS_SCAMP            = 0x01601FA8  # BSKScampRace
_BS_GOBLIN           = 0x01602681  # BSKGoblinRace (alt to CC040)
_BS_OGRE             = 0x01601FBD  # BSKOgreRace
_BS_IMP              = 0x0160299D  # BSKImpRace
_BS_MINOTAUR         = 0x016026EA  # CYRMinotaurRace
_BS_MINOTAUR_LORD    = 0x016026DB  # CYRMinotaurLordRace
#   BSHeartland.esm (Beyond Skyrim Cyrodiil)
_BSH_DAEDROTH        = 0x020ADFB0  # CYRDaedraDaedrothRace
_BSH_TROLL_RIVER     = 0x0208BB68  # CYRTrollRiverRace
_BSH_WISP            = 0x0207822E  # CYRWillotheWispRace (alt)
_BSH_SKELETON        = 0x0205BC32  # CYRSkeletonRace (alt)


# Keyword patterns matched against EditorID (case-insensitive, substring).
# Order matters — more specific patterns first. First match wins.
# Each value: (race_fid, source_note, alternate_note_or_None)
CREA_RACE_PATTERNS = [
    # ---- Daedra ----
    ('dremora',       _SKY_DREMORA,        'Skyrim.esm 0x000131F0 DremoraRace',         None),
    ('xivilai',       _SKY_DREMORA,        'Skyrim.esm 0x000131F0 DremoraRace',         None),
    ('clannfear',     _SKY_DREMORA,        'Skyrim.esm 0x000131F0 DremoraRace',         None),
    # ('daedroth',      _BSH_DAEDROTH,       'BSHeartland.esm 0x020ADFB0 CYRDaedraDaedrothRace', 'Skyrim.esm 0x000131F0 DremoraRace'),
    ('spiderda',      _SKY_DREMORA,        'Skyrim.esm 0x000131F0 DremoraRace (Spider Daedra)', None),
    # ('everscamp',     _BS_SCAMP,           'BSAssets.esm 0x01601FA8 BSKScampRace',      'Skyrim.esm 0x000131F0 DremoraRace'),
    # ('scamp',         _BS_SCAMP,           'BSAssets.esm 0x01601FA8 BSKScampRace',      'Skyrim.esm 0x000131F0 DremoraRace'),
    ('mehrunes',      _SKY_DREMORA,        'Skyrim.esm 0x000131F0 DremoraRace (Mehrunes Dagon)', None),
    ('jyggalag',      _SKY_DREMORA,        'Skyrim.esm 0x000131F0 DremoraRace (Jyggalag)', None),
    # ---- Shivering Isles unique creatures ----
    # ('goldensaint',   _CC025_GOLDEN_SAINT, 'ccbgssse025-advdsgs.esm local 0x000816 ccBGSSSE025_GoldenSaintRace', 'Skyrim.esm 0x000131F0 DremoraRace'),
    # ('golden saint',  _CC025_GOLDEN_SAINT, 'ccbgssse025-advdsgs.esm local 0x000816 ccBGSSSE025_GoldenSaintRace', 'Skyrim.esm 0x000131F0 DremoraRace'),
    # ('darkseducer',   _CC025_DARK_SEDUCER, 'ccbgssse025-advdsgs.esm local 0x000817 ccBGSSSE025_DarkSeducerRace', 'Skyrim.esm 0x000131F0 DremoraRace'),
    # ('dark seducer',  _CC025_DARK_SEDUCER, 'ccbgssse025-advdsgs.esm local 0x000817 ccBGSSSE025_DarkSeducerRace', 'Skyrim.esm 0x000131F0 DremoraRace'),
    # ('seducer',       _CC025_DARK_SEDUCER, 'ccbgssse025-advdsgs.esm local 0x000817 ccBGSSSE025_DarkSeducerRace', None),
    # ('saint',         _CC025_GOLDEN_SAINT, 'ccbgssse025-advdsgs.esm local 0x000816 ccBGSSSE025_GoldenSaintRace', None),
    # ('elytra',        _CC025_ELYTRA,       'ccbgssse025-advdsgs.esm local 0x000A76 ccBGSSSE025_ElytraRace',     'Skyrim.esm 0x000131F0 DremoraRace'),
    ('grummite',      _SKY_FALMER,         'Skyrim.esm 0x000131F4 FalmerRace (closest to Grummite)', None),
    ('gnarl',         _SKY_SPRIGGAN,       'Skyrim.esm 0x00013204 SprigganRace (closest to Gnarl)', None),
    ('hunger',        _SKY_DREMORA,        'Skyrim.esm 0x000131F0 DremoraRace (Hunger)', None),
    ('shambles',      _SKY_DRAUGR,         'Skyrim.esm 0x00000D53 DraugrRace (closest to Shambles)', None),
    ('scalon',        _SKY_CHAURUS,        'Skyrim.esm 0x000131F3 ChaurusRace (closest to Scalon)', None),
    ('baliwog',       _SKY_CHAURUS,        'Skyrim.esm 0x000131F3 ChaurusRace (closest to Baliwog)', None),
    ('skinned hound', _SKY_SKELETON,       'Skyrim.esm 0x000B7998 SkeletonRace (Skinned Hound)', None),
    ('skinnedh',      _SKY_SKELETON,       'Skyrim.esm 0x000B7998 SkeletonRace (Skinned Hound)', None),
    # ---- Atronachs ----
    ('flameatron',    _SKY_FLAME_ATRONACH, 'Skyrim.esm 0x000131F5 AtronachFlameRace',   None),
    ('flame atron',   _SKY_FLAME_ATRONACH, 'Skyrim.esm 0x000131F5 AtronachFlameRace',   None),
    ('frostatron',    _SKY_FROST_ATRONACH, 'Skyrim.esm 0x000131F6 AtronachFrostRace',   None),
    ('frost atron',   _SKY_FROST_ATRONACH, 'Skyrim.esm 0x000131F6 AtronachFrostRace',   None),
    ('frostfire atr', _SKY_FROST_ATRONACH, 'Skyrim.esm 0x000131F6 AtronachFrostRace (Frostfire)', None),
    ('stormatron',    _SKY_STORM_ATRONACH, 'Skyrim.esm 0x000131F7 AtronachStormRace',   None),
    ('storm atron',   _SKY_STORM_ATRONACH, 'Skyrim.esm 0x000131F7 AtronachStormRace',   None),
    ('flesh atron',   _SKY_DRAUGR,         'Skyrim.esm 0x00000D53 DraugrRace (Flesh Atronach—no equiv)', None),
    ('fleshatr',      _SKY_DRAUGR,         'Skyrim.esm 0x00000D53 DraugrRace (Flesh Atronach—no equiv)', None),
    # ---- Undead ----
    ('zombie',        _SKY_DRAUGR,         'Skyrim.esm 0x00000D53 DraugrRace',          'ccbgssse003-zombies.esl local 0x000D6B ccBGSSSE003ZombieRace'),
    ('skeleton',      _SKY_SKELETON,       'Skyrim.esm 0x000B7998 SkeletonRace',        'BSHeartland.esm 0x0205BC32 CYRSkeletonRace'),
    ('lich',          _SKY_DRAUGR,         'Skyrim.esm 0x00000D53 DraugrRace (Lich)',   None),
    ('ghost',         _SKY_DRAUGR,         'Skyrim.esm 0x00000D53 DraugrRace (Ghost)',  None),
    ('wraith',        _SKY_ICEWRAITH,      'Skyrim.esm 0x000131FE IceWraithRace (Wraith)', None),
    ('spirit',        _SKY_DRAUGR,         'Skyrim.esm 0x00000D53 DraugrRace (Spirit)', None),
    ('sanctified',    _SKY_DRAUGR,         'Skyrim.esm 0x00000D53 DraugrRace (Sanctified Dead)', None),
    ('ancestor',      _SKY_DRAUGR,         'Skyrim.esm 0x00000D53 DraugrRace (Ancestor)', None),
    ('deadakaviri',   _SKY_DRAUGR,         'Skyrim.esm 0x00000D53 DraugrRace (Dead Akaviri)', None),
    ('undead',        _SKY_DRAUGR,         'Skyrim.esm 0x00000D53 DraugrRace',          None),
    # ---- Wildlife ----
    ('bear',          _SKY_BEAR_BROWN,     'Skyrim.esm 0x000131E7 BearBrownRace',       'Skyrim.esm 0x000131E8 BearBlackRace / 0x000131E9 BearSnowRace'),
    ('wolf',          _SKY_WOLF,           'Skyrim.esm 0x000131EB WolfRace',            None),
    ('timber wolf',   _SKY_WOLF,           'Skyrim.esm 0x000131EB WolfRace',            None),
    ('mountain lion', _SKY_SABRECAT,       'Skyrim.esm 0x00013200 SabreCatRace (Mountain Lion)', None),
    ('mountainlion',  _SKY_SABRECAT,       'Skyrim.esm 0x00013200 SabreCatRace (Mountain Lion)', None),
    ('muontain',      _SKY_SABRECAT,       'Skyrim.esm 0x00013200 SabreCatRace (Mountain Lion typo)', None),
    ('deer',          _SKY_DEER,           'Skyrim.esm 0x000131ED ElkRace (closest to Deer)', None),
    ('horse',         _SKY_HORSE,          'Skyrim.esm 0x000131FD HorseRace',           None),
    ('unicorn',       _SKY_HORSE,          'Skyrim.esm 0x000131FD HorseRace (Unicorn)', None),
    ('dog',           _SKY_WOLF,           'Skyrim.esm 0x000131EB WolfRace (Dog)',      None),
    ('rat',           _SKY_SKEEVER,        'Skyrim.esm 0x00013201 SkeeverRace (Rat)',   None),
    ('sheep',         _SKY_GOAT,           'Skyrim.esm 0x000131FA GoatRace (Sheep)',    None),
    ('boar',          _SKY_GOAT,           'Skyrim.esm 0x000131FA GoatRace (Boar)',     None),
    ('mudcrab',       _SKY_MUDCRAB,        'Skyrim.esm 0x000BA545 MudcrabRace',          None),
    ('mud crab',      _SKY_MUDCRAB,        'Skyrim.esm 0x000BA545 MudcrabRace',          None),
    ('slaughterfish', _SKY_MUDCRAB,        'Skyrim.esm 0x000BA545 MudcrabRace (Slaughterfish—no fish race)', None),
    ('dreugh',        _SKY_CHAURUS,        'Skyrim.esm 0x000131F3 ChaurusRace (Land Dreugh)', None),
    ('spriggan',      _SKY_SPRIGGAN,       'Skyrim.esm 0x00013204 SprigganRace',        None),
    ('troll',         _SKY_TROLL,          'Skyrim.esm 0x00013205 TrollRace',           'BSHeartland.esm 0x0208BB68 CYRTrollRiverRace / Skyrim.esm 0x00013206 TrollFrostRace'),
    ('wisp',          _SKY_WISP,           'Skyrim.esm 0x00013208 WispRace',            'BSHeartland.esm 0x0207822E CYRWillotheWispRace'),
    ('will-o',        _SKY_WISP,           'Skyrim.esm 0x00013208 WispRace',            'BSHeartland.esm 0x0207822E CYRWillotheWispRace'),
    ('will o',        _SKY_WISP,           'Skyrim.esm 0x00013208 WispRace',            None),
    ('spiderling',    _SKY_SPIDER,         'Skyrim.esm 0x000131F8 FrostbiteSpiderRace (Spiderling)', None),
    ('spider',        _SKY_SPIDER,         'Skyrim.esm 0x000131F8 FrostbiteSpiderRace', None),
    # ---- Humanoid creatures ----
    # ('goblin',        _CC040_GOBLIN,       'ccbgssse040-advobgobs.esl local 0x000800 ccBGSSSE040_GoblinRace', 'BSAssets.esm 0x01602681 BSKGoblinRace'),
    # ('imp',           _BS_IMP,             'BSAssets.esm 0x0160299D BSKImpRace',        'Skyrim.esm 0x000131F0 DremoraRace'),
    # ('minotaur lord', _BS_MINOTAUR_LORD,   'BSAssets.esm 0x016026DB CYRMinotaurLordRace', 'BSAssets.esm 0x016026EA CYRMinotaurRace'),
    # ('minotaur',      _BS_MINOTAUR,        'BSAssets.esm 0x016026EA CYRMinotaurRace',   'BSAssets.esm 0x016026DB CYRMinotaurLordRace'),
    # ('ogre',          _BS_OGRE,            'BSAssets.esm 0x01601FBD BSKOgreRace',       'Skyrim.esm 0x000131F9 GiantRace'),
    ('giant',         _SKY_GIANT,          'Skyrim.esm 0x000131F9 GiantRace',           None),
    ('gatekeeper',    _SKY_GIANT,          'Skyrim.esm 0x000131F9 GiantRace (Gatekeeper)', None),
    # ---- Fallback for unrecognized ----
]


def resolve_creature_race(edid: str, full_name: str) -> tuple:
    """Resolve an Oblivion creature to a Skyrim race FormID.

    Matches EditorID and full name against CREA_RACE_PATTERNS keyword list.
    Returns (race_formid, source_note, alternate_note).
    Falls back to SkeletonRace if no pattern matches.
    """
    search_text = ((edid or '') + ' ' + (full_name or '')).lower()
    for keyword, fid, source, alt in CREA_RACE_PATTERNS:
        if keyword in search_text:
            return fid, source, alt
    return (_SKY_DEFAULT,
            'Skyrim.esm 0x000B7998 SkeletonRace (fallback — no match found)',
            None)


# ---------------------------------------------------------------------------
# TES4 race FormID → EditorID mapping (from Oblivion.esm RACE export)
# ---------------------------------------------------------------------------

TES4_RACE_FID_TO_EDID = {
    0x00000907: 'Imperial',
    0x000191C1: 'DarkElf',
    0x000191C0: 'Orc',
    0x00019204: 'HighElf',
    0x000224FC: 'Breton',
    0x000224FD: 'Nord',
    0x000223C7: 'Khajiit',
    0x000223C8: 'WoodElf',
    0x00023FE9: 'Argonian',
    0x00000D43: 'Redguard',
    0x00038010: 'Dremora',
    0x0005308E: 'Sheogorath',
    0x0001208F: 'GoldenSaint',
    0x0001208E: 'DarkSeducer',
    0x00000019: 'Imperial',    # VampireRace → Imperial
}


# ---------------------------------------------------------------------------
# NPC preset templates (Skywind/Skyblivion approach):
# ---------------------------------------------------------------------------
DEFAULT_PRESET_FEMALE = 0x00079F66  # ImperialFemalePreset01
DEFAULT_PRESET_MALE = 0x00026921    # ImperialMalePreset01


# ---------------------------------------------------------------------------
# Voice types: Race + Gender → custom VTYP FormID
# ---------------------------------------------------------------------------

VOICE_TYPE_MAP: dict = {}  # (race_edid, gender) -> FormID; populated at runtime

# Custom VTYP records to be created in the output plugin.
# Keys: VTYP EditorID → (race_edid, gender)
# DNAM flags: bit 0 = AllowDefaultDialogue, bit 1 = Female
#   Male voices:   DNAM = 1
#   Female voices: DNAM = 3
CUSTOM_VTYP_EDIDS = {
    'TES4MaleArgonian':      ('Argonian',    'Male'),
    'TES4FemaleArgonian':    ('Argonian',    'Female'),
    'TES4MaleBreton':        ('Breton',      'Male'),
    'TES4FemaleBreton':      ('Breton',      'Female'),
    'TES4MaleDarkElf':       ('DarkElf',     'Male'),
    'TES4FemaleDarkElf':     ('DarkElf',     'Female'),
    'TES4MaleHighElf':       ('HighElf',     'Male'),
    'TES4FemaleHighElf':     ('HighElf',     'Female'),
    'TES4MaleImperial':      ('Imperial',    'Male'),
    'TES4FemaleImperial':    ('Imperial',    'Female'),
    'TES4MaleKhajiit':       ('Khajiit',     'Male'),
    'TES4FemaleKhajiit':     ('Khajiit',     'Female'),
    'TES4MaleNord':          ('Nord',        'Male'),
    'TES4FemaleNord':        ('Nord',        'Female'),
    'TES4MaleOrc':           ('Orc',         'Male'),
    'TES4FemaleOrc':         ('Orc',         'Female'),
    'TES4MaleRedguard':      ('Redguard',    'Male'),
    'TES4FemaleRedguard':    ('Redguard',    'Female'),
    'TES4MaleWoodElf':       ('WoodElf',     'Male'),
    'TES4FemaleWoodElf':     ('WoodElf',     'Female'),
    'TES4MaleDarkSeducer':   ('DarkSeducer', 'Male'),
    'TES4FemaleDarkSeducer': ('DarkSeducer', 'Female'),
    'TES4MaleGoldenSaint':   ('GoldenSaint', 'Male'),
    'TES4FemaleGoldenSaint': ('GoldenSaint', 'Female'),
    'TES4MaleDremora':       ('Dremora',     'Male'),
    'TES4FemaleDremora':     ('Dremora',     'Female'),
    'TES4MaleSheogorath':    ('Sheogorath',  'Male'),
}


# VTYP FormID -> the EditorID actually written on that record.  Populated by
# _create_vtyp_records as it allocates them.  Anything needing the reverse
# direction MUST read it here rather than reconstructing it from
# CUSTOM_VTYP_EDIDS: several race EditorIDs share one VTYP (Nehrim's seven
# Alemanne* races), and a localised race's VTYP is named after its display
# name, so walking the fixed table backwards mislabels the FormID — it
# relabelled the Hochelf voice type as `TES4MaleHighElf` and sent 42 voice
# files to a folder no speaker reads.
VTYP_EDID_BY_FID: dict = {}


def set_voice_type(race_edid: str, gender: str, fid: int):
    """Register a custom VTYP FormID (called by _create_vtyp_records once
    each VTYP record has been allocated and written)."""
    VOICE_TYPE_MAP[(race_edid, gender)] = fid


# ---------------------------------------------------------------------------
# Eye mapping: Oblivion eye FormID → Skyrim HDPT eye FormID
# ---------------------------------------------------------------------------

# Default eyes per race (race-specific eye geometry — must match skeleton)
RACE_DEFAULT_EYES = {
    'Argonian':    0x0005150A,  # MaleEyesArgonian
    'Khajiit':     0x0005150B,  # MaleEyesKhajiitBase
}

# TES4 eye FormID → Skyrim HDPT FormID (male variant; female swapped in resolver)
# Human races (Imperial/Nord/Breton/Redguard) share the Human eye set.
# DarkElf, HighElf, WoodElf get elf-specific records; Orc gets orc records.
TES4_EYE_FID_TO_COLOR = {
    # Generic human eyes
    0x00027306: 'blue',        # EyeBlue
    0x00027308: 'brown',       # EyeBrown
    0x00027309: 'green',       # EyeGreen
    0x0002730A: 'hazel',       # EyeHazel
    0x0002730B: 'grey',        # EyeGrey
    # DarkElf eyes
    0x0001F7FE: 'darkelf_red', # EyesDarkElf01
    0x0001F7FF: 'darkelf_red', # EyesDarkElf02
    0x00027307: 'darkelf_red', # EyesDarkElf03
    # Argonian eyes
    0x00027EE4: 'argonian',    # EyesArgonian01
    0x00027EE5: 'argonian',    # EyesArgonian02
    0x00027EE6: 'argonian',    # EyesArgonian03
    # Khajiit eyes
    0x00027EE7: 'khajiit',     # EyesKhajiit01
    0x00027EE8: 'khajiit',     # EyesKhajiit02
    0x00027EE9: 'khajiit',     # EyesKhajiit03
    # Orc eyes
    0x00027EEA: 'orc_blue',    # EyesOrc01
    0x00027EEB: 'orc_grey',    # EyesOrc02
    # HighElf eyes
    0x00003F80: 'highelf_yellow',  # EyesHighElf01
    0x00003F81: 'highelf_orange',  # EyesHighElf02
    # WoodElf eyes
    0x00003F82: 'woodelf_brown',   # EyesWoodElf01
    0x00003F83: 'woodelf_brown',   # EyesWoodElf02
}

# Color key → (male HDPT FormID, female HDPT FormID) from Skyrim.esm
_SKY_EYE_BY_COLOR: dict[str, tuple[int, int]] = {
    'brown':         (0x00051632, 0x00072917),   # MaleEyesHumanBrown / FemaleEyesHumanBrown
    'hazel':         (0x00024250, 0x00040225),   # MaleEyesHumanHazel / FemaleEyesHumanHazel
    'hazel_brown':   (0x0002424F, 0x00051548),   # MaleEyesHumanHazelBrown / FemaleEyesHumanHazelBrown
    'blue':          (0x00024259, 0x00040208),   # MaleEyesHumanDarkBlue / FemaleEyesHumanDarkBlue
    'ice_blue':      (0x00024244, 0x00040228),   # MaleEyesHumanIceBlue / FemaleEyesHumanIceBlue
    'green':         (0x00023FE1, 0x00040210),   # MaleEyesHumanGreenHazelLeft / FemaleEyesHumanGreenHazel
    'bright_green':  (0x00023FE1, 0x0007291A),   # / FemaleEyesHumanBrightGreen
    'grey':          (0x0002425C, 0x00040211),   # MaleEyesHumanGrey / FemaleEyesHumanGrey
    'light_grey':    (0x0002425B, 0x00040227),   # MaleEyesHumanLightGrey / FemaleEyesHumanLightGrey
    'darkelf_red':   (0x00051625, 0x00051540),   # MaleEyesDarkElfRed / FemaleEyesDarkElfRed
    'argonian':      (0x0005150A, 0x0009250C),   # MaleEyesArgonian / FemaleEyesArgonian
    'khajiit':       (0x0005150B, 0x0002DDC1),   # MaleEyesKhajiitBase / FemaleEyesKhajiitBase
    'orc_blue':      (0x0004022A, 0x00040220),   # MaleEyesOrcIceBlue / FemaleEyesOrcIceBlue
    'orc_grey':      (0x00040229, 0x0004021F),   # MaleEyesOrcDarkGrey / FemaleEyesOrcDarkGrey
    'orc_red':       (0x00040226, 0x00040221),   # MaleEyesOrcRed / FemaleEyesOrcRed
    'highelf_yellow':(0x00051627, 0x00040209),   # MaleEyesHighElfYellow / FemaleEyesHighElfYellow
    'highelf_orange':(0x0004020F, 0x0005153F),   # MaleEyesHighElfOrange / FemaleEyesHighElfOrange
    'woodelf_brown': (0x00051626, 0x00051510),   # MaleEyesWoodElfBrown / FemaleEyesWoodElfBrown
}
_SKY_EYE_DEFAULT_M = 0x00051632  # MaleEyesHumanBrown
_SKY_EYE_DEFAULT_F = 0x00072917  # FemaleEyesHumanBrown


#: Orc eye sub-rules, tried in order; the empty fragment tuple always matches.
_EYE_ORC_RULES: tuple = (
    (('blue', 'ice'), 'orc_blue'),
    (('red',), 'orc_red'),
    ((), 'orc_grey'),
)

#: Ordered (name fragments, color key) rules; None defers to _EYE_ORC_RULES.
_EYE_NAME_RULES: tuple = (
    (('argonian',), 'argonian'),
    (('khajiit',), 'khajiit'),
    (('darkelf', 'dark elf', 'dunmer'), 'darkelf_red'),
    (('highelf', 'high elf', 'altmer'), 'highelf_yellow'),
    (('woodelf', 'wood elf', 'bosmer'), 'woodelf_brown'),
    (('orc', 'orsimer'), None),
    (('blue', 'dark blue'), 'blue'),
    (('ice', 'light blue'), 'ice_blue'),
    (('green',), 'green'),
    (('grey', 'gray', 'silver'), 'grey'),
    (('hazel', 'amber'), 'hazel'),
    (('brown', 'dark'), 'brown'),
)


def _eye_color_key(name: str) -> str:
    """The color key for a lowercased eye name, or '' when nothing matches.

    Rules are walked in order so the FIRST hit wins: race-specific entries
    precede the color fallbacks, so a race's own eye set beats a color word
    appearing elsewhere in the name.  The orc entry defers to the orc
    sub-table, whose trailing empty fragment tuple is its default.
    """
    for fragments, color in _EYE_NAME_RULES:
        if not any(f in name for f in fragments):
            continue
        if color:
            return color
        return next(c for frags, c in _EYE_ORC_RULES
                    if not frags or any(f in name for f in frags))
    return ''


def map_eye_formid(oblivion_edid: str, oblivion_full: str, gender: str = 'Male') -> int:
    """Map an Oblivion eye record to a Skyrim HDPT eye FormID based on name/color/gender."""
    name = (oblivion_full or oblivion_edid or '').lower()
    female = (gender == 'Female')
    color = _eye_color_key(name)
    if not color:
        return _SKY_EYE_DEFAULT_F if female else _SKY_EYE_DEFAULT_M
    pair = _SKY_EYE_BY_COLOR.get(color, (_SKY_EYE_DEFAULT_M, _SKY_EYE_DEFAULT_F))
    return pair[1] if female else pair[0]


# Lookup function used by npc_face_mapper.py (FormID-based)
def resolve_eye_by_fid(tes4_fid: int, gender: str) -> int:
    """Resolve a TES4 EYES FormID to the best Skyrim HDPT FormID."""
    color = TES4_EYE_FID_TO_COLOR.get(tes4_fid & 0x00FFFFFF, '')
    if not color:
        return _SKY_EYE_DEFAULT_F if gender == 'Female' else _SKY_EYE_DEFAULT_M
    pair = _SKY_EYE_BY_COLOR.get(color, (_SKY_EYE_DEFAULT_M, _SKY_EYE_DEFAULT_F))
    return pair[1] if gender == 'Female' else pair[0]


# ---------------------------------------------------------------------------
# TES4 HAIR FormID → Skyrim HDPT hair FormID mapping
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Hair color mapping: Oblivion HCLR (R,G,B) → Skyrim CLFM FormID
# ---------------------------------------------------------------------------

# Skyrim hair color CLFM FormIDs (from Skyrim.esm, HairColor group)
# RGB values verified by reading CNAM subrecord from Skyrim.esm directly.
HAIR_COLOR_MAP = [
    # (R, G, B, FormID, name)
    ( 92,  88,  80, 0x000A0439, 'HairColor01PlatinumBlond'),
    ( 67,  61,  46, 0x000A042D, 'HairColor02LightBlond'),
    ( 81,  77,  57, 0x000A042F, 'HairColor03BrightBlond'),
    ( 57,  55,  40, 0x000A042E, 'HairColor04HoneyBlond'),
    ( 56,  59,  44, 0x000A042C, 'HairColor05DarkBlond'),
    ( 48,  35,  33, 0x000A0431, 'HairColor06Auburn'),
    ( 66,  53,  45, 0x000A0430, 'HairColor07Chestnut'),
    ( 47,  41,  36, 0x000A0432, 'HairColor08MediumBrown'),
    ( 39,  38,  35, 0x000A0433, 'HairColor09DarkBrown'),
    ( 20,  20,  24, 0x000A0435, 'HairColor10BlueBlack'),
    ( 26,  28,  28, 0x000A0434, 'HairColor11Black'),
    ( 16,  18,  18, 0x000F7565, 'HairColor12BlackTrue'),
    ( 90,  95, 105, 0x000A0438, 'HairColor13BrightGrey'),
    ( 70,  75,  85, 0x000A0437, 'HairColor14Grey'),
    ( 43,  49,  51, 0x000A0436, 'HairColor15SteelGrey'),
]
DEFAULT_HAIR_COLOR = 0x000A0433  # HairColor09DarkBrown


def map_hair_color(r: int, g: int, b: int) -> int:
    """Map an Oblivion hair color (R,G,B) to the closest Skyrim CLFM FormID."""
    best_fid = DEFAULT_HAIR_COLOR
    best_dist = float('inf')
    for cr, cg, cb, fid, _name in HAIR_COLOR_MAP:
        dist = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
        if dist < best_dist:
            best_dist = dist
            best_fid = fid
    return best_fid


# ---------------------------------------------------------------------------
# TES4 MGEF 4-char code → Skyrim MGEF FormID mapping
# ---------------------------------------------------------------------------

MGEF_CODE_TO_SKYRIM = {
    # All FormIDs verified against Skyrim.esm MGEF records (950 total).
    # -- Alteration --
    'BRDN': 0x0003A2C6,            # Burden → AlchDamageStamina (closest hostile)
    'FTHR': 0x0003EB01,            # Feather → AlchFortifyCarryWeight
    'FISH': 0x0003AE9E,            # Fire Shield → FireCloakFFSelf
    'FRSH': 0x0003AEA0,            # Frost Shield → FrostCloakFFSelf
    'LISH': 0x0003AEA1,            # Lightning Shield → ShockCloakFFSelf
    'LOCK': 0,                     # Lock — no equivalent
    'OPEN': 0,                     # Open — no equivalent
    'SHLD': 0x00051B15,            # Shield → ArmorFFSelf0
    'WKFR': 0x00073F2E,            # Weakness to Frost → AlchWeaknessFrost
    'WKMA': 0x00073F51,            # Weakness to Magic → AlchWeaknessMagic
    'WKPO': 0x00090042,            # Weakness to Poison → AlchWeaknessPoison
    # -- Conjuration --
    'BABO': 0x00051B15,            # Bound Boots → ArmorFFSelf0 (armor buff)
    'BAGA': 0x00051B15,            # Bound Gauntlets → ArmorFFSelf0 (armor buff)
    'BAGR': 0x00051B15,            # Bound Greaves → ArmorFFSelf0 (armor buff)
    'BAHE': 0x00051B15,            # Bound Helmet → ArmorFFSelf0 (armor buff)
    'BASH': 0x00051B15,            # Bound Shield → ArmorFFSelf0 (armor buff)
    'BWAX': 0x0001CE9E,            # Bound Axe → BoundBattleAxeFFSelf
    'BWBO': 0x0001CEA0,            # Bound Bow → BoundBowFFSelf
    'BWDA': 0x0001CE9F,            # Bound Dagger → BoundSwordFFSelf
    'BWMA': 0x0001CE9F,            # Bound Mace → BoundSwordFFSelf (closest)
    'REAN': 0x00065BD6,            # Reanimate → ReanimateFFAimed0
    # -- Destruction --
    'DGAT': 0x0003A2C6,            # Damage Attribute → per-attribute (see MGEF_AV_CODE_TO_SKYRIM)
    'DGFA': 0x0003A2C6,            # Damage Fatigue → AlchDamageStamina
    'DGHE': 0x0003EB42,            # Damage Health → AlchDamageHealth
    'DGSP': 0x0003A2B6,            # Damage Magicka → AlchDamageMagicka
    'DRAT': 0x0003A2C6,            # Drain Attribute → per-attribute (see MGEF_AV_CODE_TO_SKYRIM)
    'DRFA': 0x0003A2C6,            # Drain Fatigue → AlchDamageStamina
    'DRHE': 0x0003EB42,            # Drain Health → AlchDamageHealth
    'DRSP': 0x0003A2B6,            # Drain Magicka → AlchDamageMagicka
    'DRSK': 0x0003A2C6,            # Drain Skill → AlchDamageStamina (no skill damage in TES5)
    'FIDG': 0x00012F03,            # Fire Damage → FireDamageFFAimed
    'FRDG': 0x0001CEA2,            # Frost Damage → FrostDamageFFAimed
    'SHDG': 0x0001CEA8,            # Shock Damage → ShockDamageFFAimed
    'DISE': 0x0003EB42,            # Disease → AlchDamageHealth (closest hostile)
    'POSN': 0x0003EB42,            # Poison → AlchDamageHealth (closest)
    'WKDI': 0x00090042,            # Weakness to Disease → AlchWeaknessPoison (closest)
    # -- Illusion --
    'CALM': 0x0004DEE7,            # Calm → InfluenceAggDownFFAimed
    'CHML': 0x0001EA6A,            # Chameleon → InvisibillityFFSelf
    'COCR': 0x0006F954,            # Command Creature → CommandFFActor
    'COHU': 0x0006F954,            # Command Humanoid → CommandFFActor
    'DEMO': 0x0001EA77,            # Demoralize → InfluenceConfDownFFAimed
    'FRNZ': 0x0004DEE6,            # Frenzy → InfluenceAggUpFFAimed
    'INVI': 0x0001EA6A,            # Invisibility → InvisibillityFFSelf
    'LGHT': 0x0001EA6D,            # Light → LightFFAimed
    'MYTH': 0x0006B10C,            # Nighteye → NightEyeEffect
    'PARA': 0x0001EA6E,            # Paralyze → ParalysisFFAimed
    'RALY': 0x0001EA79,            # Rally → InfluenceConfUpFFAimed
    'SLNC': 0x0008F3EA,            # Silence → MuffleFFSelf
    'TELE': 0x0001A4CB,            # Telekinesis → TelekinesisEffect
    # -- Restoration --
    'ABAT': 0x000F1D01,            # Absorb Attribute → per-attribute (see MGEF_AV_CODE_TO_SKYRIM)
    'ABFA': 0x000F1D01,            # Absorb Fatigue → AbsorbStaminaConcAimed
    'ABHE': 0x0008D5BE,            # Absorb Health → AbsorbHealthConcAimed
    'ABSK': 0x000F1D01,            # Absorb Skill → AbsorbStaminaConcAimed (no skill absorb in TES5)
    'ABSP': 0x000F1CFE,            # Absorb Magicka → AbsorbMagickaConcAimed
    'CUDI': 0x000AE722,            # Cure Disease → AlchCureDisease
    'CUPA': 0x000AE722,            # Cure Paralysis → AlchCureDisease (closest)
    'CUPO': 0x00109ADD,            # Cure Poison → AlchCurePoison
    'FOAT': 0x0003EAF9,            # Fortify Attribute → per-attribute (see MGEF_AV_CODE_TO_SKYRIM)
    'FOFA': 0x0003EAF9,            # Fortify Fatigue → AlchFortifyStamina
    'FOHE': 0x0003EAF3,            # Fortify Health → AlchFortifyHealth
    'FOSP': 0x0003EAF8,            # Fortify Magicka → AlchFortifyMagicka
    'FOSK': 0x0003EAF9,            # Fortify Skill → per-skill (see MGEF_AV_CODE_TO_SKYRIM)
    'REAT': 0x0003EB16,            # Restore Attribute → per-attribute (see MGEF_AV_CODE_TO_SKYRIM)
    'REFA': 0x0003EB16,            # Restore Fatigue → AlchRestoreStamina
    'REHE': 0x0003EB15,            # Restore Health → AlchRestoreHealth
    'RESP': 0x0003EB17,            # Restore Magicka → AlchRestoreMagicka
    'RSDI': 0x000E40D3,            # Resist Disease → AbResistDisease
    'RSFI': 0x0003EAEA,            # Resist Fire → AlchResistFire
    'RSFR': 0x0003EAEB,            # Resist Frost → AlchResistFrost
    'RSMA': 0x00039E51,            # Resist Magic → AlchResistMagic
    'RSNW': 0,                     # Resist Normal Weapons → no equivalent
    'RSPA': 0,                     # Resist Paralysis → no equivalent
    'RSPO': 0x00090041,            # Resist Poison → AlchResistPoison
    'RSSH': 0x0003EAEC,            # Resist Shock → AlchResistShock
    'RSWD': 0,                     # Resist Water Damage → no equivalent
    'SABS': 0x000954D6,            # Spell Absorption → PertAtronachEffect
    'SEFF': 0,                     # Script Effect — no equivalent
    'STRP': 0x0004DBA3,            # Soul Trap → SoulTrapFFActor
    'SUDG': 0x000ED096,            # Sun Damage → AbVampireSunDamage01
    'RFLC': 0x00108A41,            # Reflect Spell → PerkReflectBlows (closest)
    'REDG': 0,                     # Reflect Damage → no equivalent
    'DSPL': 0,                     # Dispel → no equivalent
    'DTCT': 0x0001EA74,            # Detect Life → DetectLifeFriendInteriorConcSelf
}

# ---------------------------------------------------------------------------
# Attribute/skill-targeted TES4 effects → Skyrim MGEF, keyed by the effect's
# ---------------------------------------------------------------------------

_DAMAGE_ATTR = {
    0: 0x0003A2C6,   # Strength → AlchDamageStamina
    1: 0x0003A2B6,   # Intelligence → AlchDamageMagicka
    2: 0x00073F2B,   # Willpower → AlchDamageMagickaRate
    3: 0x0003A2C6,   # Agility → AlchDamageStamina
    4: 0x00073F25,   # Speed → AlchDamageSpeed
    5: 0x0003EB42,   # Endurance → AlchDamageHealth
    6: 0x0003A2B6,   # Personality → AlchDamageMagicka
    7: 0x00073F2C,   # Luck → AlchDamageStaminaRate
}

_FORTIFY_ATTR = {
    0: 0x0003EB01,   # Strength → AlchFortifyCarryWeight
    1: 0x0003EAF8,   # Intelligence → AlchFortifyMagicka
    2: 0x0003EB07,   # Willpower → AlchFortifyMagickaRate
    3: 0x0003EAF9,   # Agility → AlchFortifyStamina
    4: 0x0003EAF9,   # Speed → AlchFortifyStamina
    5: 0x0003EAF3,   # Endurance → AlchFortifyHealth
    6: 0x0003EB23,   # Personality → AlchFortifyBarter
    7: 0x0003EB06,   # Luck → AlchFortifyHealRate
}

_RESTORE_ATTR = {
    0: 0x0003EB16,   # Strength → AlchRestoreStamina
    1: 0x0003EB17,   # Intelligence → AlchRestoreMagicka
    2: 0x0003EB17,   # Willpower → AlchRestoreMagicka
    3: 0x0003EB16,   # Agility → AlchRestoreStamina
    4: 0x0003EB16,   # Speed → AlchRestoreStamina
    5: 0x0003EB15,   # Endurance → AlchRestoreHealth
    6: 0x0003EB17,   # Personality → AlchRestoreMagicka
    7: 0x0003EB16,   # Luck → AlchRestoreStamina
}

_ABSORB_ATTR = {
    0: 0x000F1D01,   # Strength → AbsorbStaminaConcAimed
    1: 0x000F1CFE,   # Intelligence → AbsorbMagickaConcAimed
    2: 0x000F1CFE,   # Willpower → AbsorbMagickaConcAimed
    3: 0x000F1D01,   # Agility → AbsorbStaminaConcAimed
    4: 0x000F1D01,   # Speed → AbsorbStaminaConcAimed
    5: 0x0008D5BE,   # Endurance → AbsorbHealthConcAimed
    6: 0x000F1CFE,   # Personality → AbsorbMagickaConcAimed
    7: 0x000F1D01,   # Luck → AbsorbStaminaConcAimed
}

_FORTIFY_SKILL = {
    12: 0x0003EB1D,  # Armorer → AlchFortifySmithing
    13: 0x0003EAF9,  # Athletics → AlchFortifyStamina
    14: 0x0003EB19,  # Blade → AlchFortifyOneHanded
    15: 0x0003EB1C,  # Block → AlchFortifyBlock
    16: 0x0003EB19,  # Blunt → AlchFortifyOneHanded
    17: 0x0003EB19,  # HandToHand → AlchFortifyOneHanded
    18: 0x0003EB1E,  # HeavyArmor → AlchFortifyHeavyArmor
    19: 0x0003EB18,  # Alchemy → AlchFortifyAlchemy
    20: 0x0003EB24,  # Alteration → AlchFortifyAlteration
    21: 0x0003EB25,  # Conjuration → AlchFortifyConjuration
    22: 0x0003EB26,  # Destruction → AlchFortifyDestruction
    23: 0x0003EB27,  # Illusion → AlchFortifyIllusion
    24: 0x0003EB27,  # Mysticism → AlchFortifyIllusion
    25: 0x0003EB28,  # Restoration → AlchFortifyRestoration
    26: 0x0003EAF9,  # Acrobatics → AlchFortifyStamina
    27: 0x0003EB1F,  # LightArmor → AlchFortifyLightArmor
    28: 0x0003EB1B,  # Marksman → AlchFortifyMarksman
    29: 0x0003EB23,  # Mercantile → AlchFortifyBarter
    30: 0x0003EB21,  # Security → AlchFortifyLockpicking
    31: 0x0003EB22,  # Sneak → AlchFortifySneak
    32: 0x0003EB23,  # Speechcraft → AlchFortifyBarter
}

MGEF_AV_CODE_TO_SKYRIM = {
    'DGAT': _DAMAGE_ATTR,    # Damage Attribute
    'DRAT': _DAMAGE_ATTR,    # Drain Attribute
    'FOAT': _FORTIFY_ATTR,   # Fortify Attribute
    'REAT': _RESTORE_ATTR,   # Restore Attribute
    'ABAT': _ABSORB_ATTR,    # Absorb Attribute
    'FOSK': _FORTIFY_SKILL,  # Fortify Skill
}

# Skyrim fallback arrow projectile used when we cannot build a converted PROJ
# (e.g. when no writer is available). ArrowIronProjectile [PROJ:0003BE11]
DEFAULT_ARROW_PROJECTILE = 0x0003BE11

# TES4 skill index → TES5 Actor Value index for BOOK teaching
# TES4 uses absolute indices (12-32), TES5 uses AV indices (6-23)
# AV 6=OneHanded, 7=TwoHanded, 8=Marksman, 9=Block, 10=Smithing,
# 11=HeavyArmor, 12=LightArmor, 13=Pickpocket, 14=Lockpicking, 15=Sneak,
# 16=Alchemy, 17=Speechcraft, 18=Alteration, 19=Conjuration, 20=Destruction,
# 21=Illusion, 22=Restoration, 23=Enchanting
TES4_SKILL_TO_TES5_INDEX = {
    12: 10,  # Armorer → Smithing (AV 10)
    14: 6,   # Blade → OneHanded (AV 6)
    15: 9,   # Block → Block (AV 9)
    16: 6,   # Blunt → OneHanded (AV 6)
    17: 6,   # HandToHand → OneHanded (AV 6)
    18: 11,  # HeavyArmor → HeavyArmor (AV 11)
    19: 16,  # Alchemy → Alchemy (AV 16)
    20: 18,  # Alteration → Alteration (AV 18)
    21: 19,  # Conjuration → Conjuration (AV 19)
    22: 20,  # Destruction → Destruction (AV 20)
    23: 21,  # Illusion → Illusion (AV 21)
    24: 21,  # Mysticism → Illusion (AV 21)
    25: 22,  # Restoration → Restoration (AV 22)
    27: 12,  # LightArmor → LightArmor (AV 12)
    28: 8,   # Marksman → Archery (AV 8)
    29: 13,  # Mercantile → Pickpocket (AV 13)
    30: 14,  # Security → Lockpicking (AV 14)
    31: 15,  # Sneak → Sneak (AV 15)
    32: 17,  # Speechcraft → Speech (AV 17)
}

# ---------------------------------------------------------------------------
# Invisible marker base-object substitution:
# ---------------------------------------------------------------------------

TES4_MARKER_FORMID_TO_SKYRIM = {
    0x00000001: 0x0000003B,  # DoorMarker       → XMarker (invisible generic)
    0x00000002: 0x00000002,  # TravelMarker     → same FormID in Skyrim.esm (fast-travel dest)
    0x00000003: 0x00000003,  # NorthMarker      → same FormID in Skyrim.esm
    0x00000004: 0x00000004,  # PrisonMarker     → same FormID in Skyrim.esm (a jail)
    0x00000005: 0x0000003B,  # DivineMarker     → XMarker (no Skyrim equivalent)
    0x00000006: 0x0000003B,  # TempleMarker     → XMarker (no Skyrim equivalent)
    # MapMarker → MapMarker.  Skyrim's STAT 0x10 *is* the map marker base object
    # (all 397 vanilla marker REFRs use NAME=00000010).  Substituting
    # XMarkerHeading here makes the engine treat the reference as a plain
    # invisible marker, so it never appears on the map or becomes discoverable.
    0x00000010: 0x00000010,  # MapMarker        → MapMarker
    0x00000012: 0x00000002,  # HorseMarker      → TravelMarker (closest)
    0x00000034: 0x00000034,  # XMarkerHeading   → same FormID in Skyrim.esm
    0x0000003B: 0x0000003B,  # XMarker          → same FormID in Skyrim.esm
    0x0000080B: 0x0000003B,  # MarkerTeleport   → XMarker (door-destination marker)
}

# ---------------------------------------------------------------------------
# Engine-hardcoded ITEM substitutions: raw TES4 FormID -> Skyrim.esm FormID.
TES4_ITEM_FORMID_TO_SKYRIM = {
    0x0000000F: 0x0000000F,  # Gold001 → Skyrim.esm Gold001 (currency)
    0x0000000A: 0x0000000A,  # Lockpick/BobbyPin → Skyrim.esm Lockpick (LKPK)
    0x0000000B: 0x0003A070,  # DASkeletonKey → Skyrim.esm TG08SkeletonKey (SKLK)
}

# ---------------------------------------------------------------------------
#: Default Object Manager slot naming the form the engine uses for combat music
DOBJ_BATTLE_MUSIC_TAG = b'BTMS'

#: DOBJ slots the lockpicking minigame resolves; see tes5_import_pipeline.md
DOBJ_ITEM_TAGS = {
    b'LKPK': 0x0000000A,
    b'SKLK': 0x0003A070,
}

def _read_master_dobj(skyrim_esm: str):
    """(FormID, [(tag, formid), ...]) for Skyrim.esm's DOBJ, or None."""
    with open(skyrim_esm, 'rb') as fh:
        data = fh.read()
    for rec in records(data, b'DOBJ'):
        val = rec.sub(b'DNAM')
        if val is not None:
            return rec.form_id, [
                (val[j:j + 4], struct.unpack_from('<I', val, j + 4)[0])
                for j in range(0, len(val) - 7, 8)]
    return None


def build_DOBJ_override(overrides: dict, skyrim_esm: str):
    """Skyrim.esm's DOBJ with each tag in *overrides* repointed at our form.

    *overrides* maps a 4-byte DNAM tag to the FormID it should name.  Every
    other default object is copied unchanged.  Returns (formid, record_bytes),
    or None when the master has no DOBJ or names none of the tags -- in which
    case we write nothing rather than inventing a default-object table.

    See: docs/commentary/tes5_import_pipeline.md#phase-0c-dobj-btms
    """
    found = _read_master_dobj(skyrim_esm)
    if not found:
        return None
    fid, entries = found
    live = {tag: new_fid for tag, new_fid in overrides.items()
            if new_fid and any(tag == have for have, _ in entries)}
    if not live:
        return None
    dnam = b''.join(
        tag + struct.pack('<I', live.get(tag, old))
        for tag, old in entries)
    subs = pack_string_subrecord('EDID', 'DefaultObjectManager')
    subs += pack_subrecord('DNAM', dnam)
    return fid, pack_record('DOBJ', fid, 0, subs)

# ---------------------------------------------------------------------------
# Weapon equipment type (EQUP) FormIDs — Skyrim.esm
# ---------------------------------------------------------------------------
EQUP_RIGHT_HAND  = 0x00013F42  # RightHand  — all 1-handed melee
EQUP_BOTH_HANDS  = 0x00013F45  # BothHands  — 2-handed melee + bows
EQUP_EITHER_HAND = 0x00013F44  # EitherHand — (unused by vanilla weapons)

# TES5 anim type → EQUP FormID
# Anim types: 1=Sword, 2=Dagger, 3=WarAxe, 4=Mace, 5=GreatSword,
#             6=Battleaxe/Warhammer, 7=Bow, 8=Staff
WEAPON_ANIM_EQUP: dict[int, int] = {
    1: EQUP_RIGHT_HAND,   # Sword
    2: EQUP_RIGHT_HAND,   # Dagger
    3: EQUP_RIGHT_HAND,   # WarAxe
    4: EQUP_RIGHT_HAND,   # Mace
    5: EQUP_BOTH_HANDS,   # GreatSword
    6: EQUP_BOTH_HANDS,   # Battleaxe/Warhammer
    7: EQUP_BOTH_HANDS,   # Bow
    9: EQUP_BOTH_HANDS,   # Crossbow (as Bow)
    8: EQUP_RIGHT_HAND,   # Staff
}

#: DNAM anim type of a crossbow (wbWeaponAnimTypeEnum: 7 Bow, 8 Staff, 9 Crossbow).
WEAPON_ANIM_CROSSBOW = 9

# ---------------------------------------------------------------------------
# Per-anim-type weapon defaults — sourced from vanilla Skyrim iron/steel weapons
WEAPON_ANIM_INAM: dict[int, int] = {
    1: 0x00013CAC,   # Sword   → WPNzBlade1HandImpactSet
    2: 0x00013CAC,   # Dagger  → WPNzBlade1HandImpactSet
    3: 0x000193B8,   # WarAxe  → WPNzAxeImpactSet
    4: 0x000193B7,   # Mace    → WPNzBluntImpactSet
    5: 0x000949D5,   # GreatSword → WPNzBlade2HandImpactSet
    6: 0x00036A5B,   # Battleaxe  → WPNzAxeLargeImpactSet
    7: 0x000193B9,   # Bow     → WPNzArrowImpactSet
    9: 0x000193B9,   # Crossbow (as Bow)
    8: 0x000193B9,   # Staff   → WPNzArrowImpactSet (no staff-specific set)
}

# BIDS — Block Bash Impact Data Set FormIDs
WEAPON_ANIM_BIDS: dict[int, int] = {
    1: 0x000183FF,   # Sword   → WPNBashBladeImpactSet
    2: 0x000183FF,   # Dagger  → WPNBashBladeImpactSet
    3: 0x000193C8,   # WarAxe  → WPNBashAxeImpactSet
    4: 0x000193C7,   # Mace    → WPNBashBluntImpactSet
    5: 0x000183FF,   # GreatSword → WPNBashBladeImpactSet
    6: 0x000193C7,   # Battleaxe  → WPNBashBluntImpactSet
    7: 0x000193C6,   # Bow     → WPNBashBowImpactSet
    9: 0x000193C6,   # Crossbow (as Bow)
    8: 0x000193C6,   # Staff   → WPNBashBowImpactSet
}

# BAMT — Block Material FormIDs
WEAPON_ANIM_BAMT: dict[int, int] = {
    1: 0x000774C2,   # Sword   → MaterialBlockBlade1Hand
    2: 0x000774C2,   # Dagger  → MaterialBlockBlade1Hand
    3: 0x000774C0,   # WarAxe  → MaterialBlockAxe
    4: 0x000774C1,   # Mace    → MaterialBlockBlunt
    5: 0x00097786,   # GreatSword → MaterialBlockBlade2Hand
    6: 0x000E64A9,   # Battleaxe  → MaterialBlockBlunt2Hand
    7: 0x000774B6,   # Bow     → MaterialBlockBowsStaves
    9: 0x000774B6,   # Crossbow (as Bow)
    8: 0x000774B6,   # Staff   → MaterialBlockBowsStaves
}

# NAM8 — Sheathe Sound Descriptor FormIDs
WEAPON_ANIM_NAM8: dict[int, int] = {
    1: 0x0003C72F,   # Sword   → WPNBlade1HandSheatheSD
    2: 0x0003CEDB,   # Dagger  → WPNBlade1HandSmallSheatheSD
    3: 0x0003DDB8,   # WarAxe  → WPNAxe1HandSheatheSD
    4: 0x0003DE2B,   # Mace    → WPNMace1HandSheatheSD
    5: 0x0003C8AA,   # GreatSword → WPNBlade2HandSheatheSD
    6: 0x000605D3,   # Battleaxe  → WPNAxe2HandSheatheSD
    7: 0x0003D882,   # Bow     → WPNBowSheatheSD
    9: 0x0003D882,   # Crossbow (as Bow)
    8: 0x0003DE2D,   # Staff   → WPNStaffHandSheatheSD
}

# NAM9 — Draw Sound Descriptor FormIDs
WEAPON_ANIM_NAM9: dict[int, int] = {
    1: 0x0003C72E,   # Sword   → WPNBlade1HandDrawSD
    2: 0x0003CED9,   # Dagger  → WPNBlade1HandSmallDrawSD
    3: 0x0003DDB7,   # WarAxe  → WPNAxe1HandDrawSD
    4: 0x0003DE2A,   # Mace    → WPNMace1HandDrawSD
    5: 0x0003C8A9,   # GreatSword → WPNBlade2HandDrawSD
    6: 0x0006036A,   # Battleaxe  → WPNAxe2HandDrawSD
    7: 0x0003D78C,   # Bow     → WPNBowDrawSD
    9: 0x0003D78C,   # Crossbow (as Bow)
    8: 0x0003DE2C,   # Staff   → WPNStaffHandDrawSD
}

# VNAM — Violence type: 0=NotAllowed, 1=AllowNormal, 2=AllowByHoldingShield
WEAPON_ANIM_VNAM: dict[int, int] = {
    1: 1,   # Sword
    2: 2,   # Dagger
    3: 1,   # WarAxe
    4: 1,   # Mace
    5: 0,   # GreatSword
    6: 0,   # Battleaxe
    7: 2,   # Bow
    9: 2,   # Crossbow (as Bow)
    8: 1,   # Staff
}

# DNAM animationMultiplier (float at offset 4) by anim type
WEAPON_ANIM_MULT: dict[int, float] = {
    1: 1.0,   # Sword
    2: 1.1,   # Dagger
    3: 0.9,   # WarAxe
    4: 0.8,   # Mace
    5: 0.7,   # GreatSword
    6: 0.7,   # Battleaxe
    7: 1.0,   # Bow
    9: 1.0,   # Crossbow (as Bow)
    8: 1.0,   # Staff
}

# DNAM stagger (U8 at offset 76) by anim type
WEAPON_ANIM_STAGGER: dict[int, int] = {
    1: 6,   # Sword     — Ragdoll
    2: 5,   # Dagger    — Normal
    3: 6,   # WarAxe    — Ragdoll
    4: 6,   # Mace      — Ragdoll
    5: 7,   # GreatSword — Large Ragdoll
    6: 7,   # Battleaxe  — Large Ragdoll
    7: 0,   # Bow       — None
    9: 0,   # Crossbow (as Bow)
    8: 0,   # Staff     — None
}

# DNAM flags (U32 at offset 12) by anim type
# 0x40 = PlayerOnly (set for slower weapons: mace, greatsword, battleaxe)
WEAPON_ANIM_FLAGS: dict[int, int] = {
    1: 0x00,   # Sword
    2: 0x00,   # Dagger
    3: 0x00,   # WarAxe
    4: 0x40,   # Mace
    5: 0x40,   # GreatSword
    6: 0x40,   # Battleaxe
    7: 0x00,   # Bow
    9: 0x00,   # Crossbow (as Bow)
    8: 0x00,   # Staff
}

# TES4 Attribute skill mapping (for CLAS skill weights calculation)
# Attribute name → list of TES5 skill names that map from that attribute
ATTRIBUTE_SKILL_MAP = {
    'Strength':     ['OneHanded', 'TwoHanded', 'Smithing'],
    'Intelligence': ['Conjuration', 'Alchemy'],
    'Willpower':    ['Restoration', 'Alteration'],
    'Agility':      ['Sneak', 'LightArmor', 'Lockpicking'],
    'Speed':        ['Pickpocket', 'Speechcraft'],
    'Endurance':    ['Block', 'HeavyArmor'],
    'Personality':  ['Destruction', 'Illusion', 'Marksman', 'Enchanting'],
    'Luck':         [],  # Luck adds +1 to all
}

# ---------------------------------------------------------------------------
# Armor / ARMA constants
# ---------------------------------------------------------------------------

# Skyrim EQUP FormID for shields (ETYP subrecord on ARMO)
SHIELD_EQUIP_TYPE = 0x000141E8

# ---------------------------------------------------------------------------
# Spell equip types (ETYP subrecord on SPEL)
SPELL_EQUIP_EITHER_HAND = 0x00013F44    # EitherHand
SPELL_EQUIP_VOICE = 0x00025BEE          # Voice (powers / lesser powers)

# TES5 spell type -> EQUP.  0 Spell, 1 Disease, 2 Power, 3 Lesser Power,
# 4 Ability.  Powers are voice-slotted; everything else goes to either hand.
SPELL_TYPE_EQUIP_TYPE = {
    2: SPELL_EQUIP_VOICE,
    3: SPELL_EQUIP_VOICE,
}

# Skyrim footstep sets (FormIDs verified against references/Skyrim.esm/FSTS.txt
# — the old Light/Clothing values 0x24238/0x24237 didn't exist in Skyrim.esm,
# giving every clothing/light-boot ArmorAddon a dangling FootstepSet).
HEAVY_ARMOR_FOOTSTEP_SET = 0x00021487   # FSTArmorHeavyFootstepSet
LIGHT_ARMOR_FOOTSTEP_SET = 0x00021486   # FSTArmorLightFootstepSet
CLOTHING_FOOTSTEP_SET = 0x00021468      # FSTBarefootFootstepSet

# Additional races for ARMA records.  Every ARMA should list all playable
# races so any race can equip the armor.  These are the base race FormIDs
# from Skyrim.esm (sourced from IronBootsAA's MODL entries).
ARMA_ADDITIONAL_RACES = [
    0x00013740,  # ArgonianRace
    0x00013741,  # BretonRace
    0x00013742,  # DarkElfRace
    0x00013743,  # HighElfRace
    0x00013744,  # ImperialRace
    0x00013745,  # KhajiitRace
    0x00013746,  # NordRace
    0x00013747,  # OrcRace
    0x00013748,  # RedguardRace
    0x00013749,  # WoodElfRace
]


# BEAST-RACE HEAD GEAR (2026-08-27).  A converted hood/helmet mesh is fitted
# to the SHARED HUMAN skull, which on a khajiit or argonian puts the geometry
# inside the head: measured head-local, the khajiit Skyrim head reaches
# z 14.85 and abs(x) 8.47 against the human head's 11.51 / 6.85, and over the
# scalp region a hood sits on, beast verts stand a mean 1.91 (khajiit) / 1.40
# (argonian) proud of the human surface (max 6.87 / 4.29).
#
# Vanilla Skyrim's answer is one ARMA PER RACE FAMILY, each naming its own
# reshaped NIF -- ArmorIronHelmet lists IronHelmetAA (RNAM=DefaultRace,
# Helmet.nif), IronHelmetKhajiitAA (RNAM=KhajiitRace, HelmetKhajiit.nif) and
# IronHelmetArgonianAA (RNAM=ArgonianRace, HelmetArgonian.nif).  ARMA carries
# no alternate-model slot; race targeting is RNAM plus the MODL[] additional
# races, so a per-race MESH requires a per-race ARMA.  The same split runs
# through BoneCrown, Blades, Orcish, Dragonscale, Draugr, Dragonplate, Falmer,
# ThalmorHood and every Circlet.
#
# We mirror it: asset_convert.nif.nif_converter writes <name>_khajiit.nif /
# <name>_argonian.nif beside the base mesh and equipment._build_arma emits the
# ARMA naming each.  Khajiit and Argonian stay SEPARATE (never one shared
# "beast" mesh) because the two skulls differ from each other as much as
# either differs from the human one.
#
# race key -> (RNAM race FormID, [additional races], mesh suffix).  The
# additional race is that race's vampire variant, exactly as vanilla lists it.
ARMA_BEAST_RACES = {
    'khajiit': (0x00013745, [0x00088845], '_khajiit'),
    'argonian': (0x00013740, [0x0008883A], '_argonian'),
}

# The races the DEFAULT (human-fitted) ARMA should claim once beast variants
# exist: everything in ARMA_ADDITIONAL_RACES except the beast races, which are
# served by their own ARMA.  Leaving them in makes the engine pick whichever
# armature it finds first and the beast mesh never renders.
ARMA_BEAST_RACE_FIDS = {fid for fid, _extra, _sfx in ARMA_BEAST_RACES.values()}
ARMA_ADDITIONAL_RACES_NONBEAST = [
    fid for fid in ARMA_ADDITIONAL_RACES if fid not in ARMA_BEAST_RACE_FIDS
]
