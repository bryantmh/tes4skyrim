"""
Skyrim-specific overrides and value mappings.

This module contains mappings from Oblivion data to Skyrim equivalents
that require referencing Skyrim.esm records. These are the "speculative"
conversions that go beyond straightforward format translation.

Oblivion race EditorID/FormID → Skyrim race FormID,
NPC preset templates, voice types, head parts, hair colors, etc.
"""

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
    # Shivering Isles playable-ish races — mapped to proper CC races where available
    # GoldenSaint: ccbgssse025-advdsgs.esm local 0x000816 ccBGSSSE025_GoldenSaintRace
    # DarkSeducer: ccbgssse025-advdsgs.esm local 0x000817 ccBGSSSE025_DarkSeducerRace
    # Dremora:     Skyrim.esm 0x000131F0 DremoraRace
    'GoldenSaint':   0x000131F0,  # → DremoraRace (Skyrim.esm); ideal=ccbgssse025 0x000816
    'DarkSeducer':   0x000131F0,  # → DremoraRace (Skyrim.esm); ideal=ccbgssse025 0x000817
    'Dremora':       0x000131F0,  # → DremoraRace (Skyrim.esm 0x000131F0) — exact match
    'SEDremora':     0x000131F0,  # → DremoraRace (Skyrim.esm 0x000131F0)
    'Sheogorath':    0x00013744,  # → Imperial (no Sheogorath race in any source)
}
DEFAULT_RACE = 0x00013746  # Nord


# ---------------------------------------------------------------------------
# Creature race map: EditorID/name keyword → Skyrim race FormID
#
# Priority order for source selection:
#   1. Skyrim.esm  2. Dawnguard.esm  3. Dragonborn.esm
#   4. CC ESLs (ccbgssse025, ccbgssse040, ccbgssse003)
#   5. BSAssets.esm  6. BSHeartland.esm
#
# Each entry is a tuple: (race_formid, source_note, alternate_note)
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
_SKY_MAMMOTH         = 0x000131FC  # (re-use HorkerRace placeholder; mammoth=0x000131FC conflicts)
#   Corrected:
_SKY_MAMMOTH         = 0x0001320A  # MammothRace (Skyrim.esm)
_SKY_RABBIT          = 0x00059339  # RabbitRace (Skyrim.esm)
_SKY_FOX             = 0x000A0EB2  # FoxRace (Skyrim.esm)
_SKY_DEER            = 0x000131ED  # ElkRace (closest for deer)
_SKY_DEFAULT         = 0x00013746  # Nord (last-resort fallback)
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


def _make_race(fid, source, alt=None):
    """Return (formid, source_note, alternate_note) tuple."""
    return (fid, source, alt)


# Keyword patterns matched against EditorID (case-insensitive, substring).
# Order matters — more specific patterns first. First match wins.
# Each value: (race_fid, source_note, alternate_note_or_None)
CREA_RACE_PATTERNS = [
    # ---- Daedra ----
    ('dremora',       _SKY_DREMORA,        'Skyrim.esm 0x000131F0 DremoraRace',         None),
    ('xivilai',       _SKY_DREMORA,        'Skyrim.esm 0x000131F0 DremoraRace',         None),
    ('clannfear',     _SKY_DREMORA,        'Skyrim.esm 0x000131F0 DremoraRace',         None),
    ('daedroth',      _BSH_DAEDROTH,       'BSHeartland.esm 0x020ADFB0 CYRDaedraDaedrothRace', 'Skyrim.esm 0x000131F0 DremoraRace'),
    ('spiderda',      _SKY_DREMORA,        'Skyrim.esm 0x000131F0 DremoraRace (Spider Daedra)', None),
    ('everscamp',     _BS_SCAMP,           'BSAssets.esm 0x01601FA8 BSKScampRace',      'Skyrim.esm 0x000131F0 DremoraRace'),
    ('scamp',         _BS_SCAMP,           'BSAssets.esm 0x01601FA8 BSKScampRace',      'Skyrim.esm 0x000131F0 DremoraRace'),
    ('mehrunes',      _SKY_DREMORA,        'Skyrim.esm 0x000131F0 DremoraRace (Mehrunes Dagon)', None),
    ('jyggalag',      _SKY_DREMORA,        'Skyrim.esm 0x000131F0 DremoraRace (Jyggalag)', None),
    # ---- Shivering Isles unique creatures ----
    ('goldensaint',   _CC025_GOLDEN_SAINT, 'ccbgssse025-advdsgs.esm local 0x000816 ccBGSSSE025_GoldenSaintRace', 'Skyrim.esm 0x000131F0 DremoraRace'),
    ('golden saint',  _CC025_GOLDEN_SAINT, 'ccbgssse025-advdsgs.esm local 0x000816 ccBGSSSE025_GoldenSaintRace', 'Skyrim.esm 0x000131F0 DremoraRace'),
    ('darkseducer',   _CC025_DARK_SEDUCER, 'ccbgssse025-advdsgs.esm local 0x000817 ccBGSSSE025_DarkSeducerRace', 'Skyrim.esm 0x000131F0 DremoraRace'),
    ('dark seducer',  _CC025_DARK_SEDUCER, 'ccbgssse025-advdsgs.esm local 0x000817 ccBGSSSE025_DarkSeducerRace', 'Skyrim.esm 0x000131F0 DremoraRace'),
    ('seducer',       _CC025_DARK_SEDUCER, 'ccbgssse025-advdsgs.esm local 0x000817 ccBGSSSE025_DarkSeducerRace', None),
    ('saint',         _CC025_GOLDEN_SAINT, 'ccbgssse025-advdsgs.esm local 0x000816 ccBGSSSE025_GoldenSaintRace', None),
    ('elytra',        _CC025_ELYTRA,       'ccbgssse025-advdsgs.esm local 0x000A76 ccBGSSSE025_ElytraRace',     'Skyrim.esm 0x000131F0 DremoraRace'),
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
    ('goblin',        _CC040_GOBLIN,       'ccbgssse040-advobgobs.esl local 0x000800 ccBGSSSE040_GoblinRace', 'BSAssets.esm 0x01602681 BSKGoblinRace'),
    ('imp',           _BS_IMP,             'BSAssets.esm 0x0160299D BSKImpRace',        'Skyrim.esm 0x000131F0 DremoraRace'),
    ('minotaur lord', _BS_MINOTAUR_LORD,   'BSAssets.esm 0x016026DB CYRMinotaurLordRace', 'BSAssets.esm 0x016026EA CYRMinotaurRace'),
    ('minotaur',      _BS_MINOTAUR,        'BSAssets.esm 0x016026EA CYRMinotaurRace',   'BSAssets.esm 0x016026DB CYRMinotaurLordRace'),
    ('ogre',          _BS_OGRE,            'BSAssets.esm 0x01601FBD BSKOgreRace',       'Skyrim.esm 0x000131F9 GiantRace'),
    ('giant',         _SKY_GIANT,          'Skyrim.esm 0x000131F9 GiantRace',           None),
    ('gatekeeper',    _SKY_GIANT,          'Skyrim.esm 0x000131F9 GiantRace (Gatekeeper)', None),
    # ---- Fallback for unrecognized ----
]


def resolve_creature_race(edid: str, full_name: str) -> tuple:
    """Resolve an Oblivion creature to a Skyrim race FormID.

    Matches EditorID and full name against CREA_RACE_PATTERNS keyword list.
    Returns (race_formid, source_note, alternate_note).
    Falls back to DefaultRace (Nord) if no pattern matches.
    """
    search_text = ((edid or '') + ' ' + (full_name or '')).lower()
    for keyword, fid, source, alt in CREA_RACE_PATTERNS:
        if keyword in search_text:
            return fid, source, alt
    return _SKY_DEFAULT, 'Skyrim.esm 0x00013746 Nord (fallback — no match found)', None


# ---------------------------------------------------------------------------
# TES4 race FormID → EditorID mapping (from Oblivion.esm RACE export)
# Used to resolve NPC race references to EditorIDs for RACE_MAP lookup.
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
# Copy visual data (head parts, face morphs, tints) from preset NPCs.
# Key: (race_editorid, gender) → Skyrim preset NPC FormID
# These are actual NPC_ records in Skyrim.esm whose face data we reference.
# ---------------------------------------------------------------------------

NPC_PRESETS = {
    ('Argonian', 'Female'):   0x000B2E11,  # ArgonianFemalePreset01
    ('Argonian', 'Male'):     0x00043E57,  # ArgonianMalePreset01
    ('Breton', 'Female'):     0x00079F65,  # BretonFemalePreset01
    ('Breton', 'Male'):       0x00079F6A,  # BretonMalePreset01
    ('DarkElf', 'Female'):    0x00079F5B,  # DarkElfFemalePreset01
    ('DarkElf', 'Male'):      0x0005EFA7,  # DarkElfMalePreset01
    ('HighElf', 'Female'):    0x00079BED,  # HighElfFemalePreset01
    ('HighElf', 'Male'):      0x0005EF9C,  # HighElfMalePreset01
    ('Imperial', 'Female'):   0x00079F66,  # ImperialFemalePreset01
    ('Imperial', 'Male'):     0x00026921,  # ImperialMalePreset01
    ('Khajiit', 'Female'):    0x000EE856,  # KhajiitFemalePreset01
    ('Khajiit', 'Male'):      0x00043E59,  # KhajiitMalePreset01
    ('Nord', 'Female'):       0x00079F68,  # NordFemalePreset01
    ('Nord', 'Male'):         0x0001750C,  # NordMalePreset01
    ('Orc', 'Female'):        0x00079F4E,  # OrcFemalePreset01
    ('Orc', 'Male'):          0x00079F69,  # OrcMalePreset01
    ('Redguard', 'Female'):   0x00079F67,  # RedguardFemalePreset01
    ('Redguard', 'Male'):     0x0005B4F8,  # RedguardMalePreset01
    ('WoodElf', 'Female'):    0x00079CD3,  # WoodElfFemalePreset01
    ('WoodElf', 'Male'):      0x0005EF9A,  # WoodElfMalePreset01
}
DEFAULT_PRESET_FEMALE = 0x00079F66  # ImperialFemalePreset01
DEFAULT_PRESET_MALE = 0x00026921    # ImperialMalePreset01


# ---------------------------------------------------------------------------
# Voice types: Race + Gender → custom VTYP FormID
# Starts empty; fully populated at import time by _create_vtyp_records() in
# import_main.py before any NPC_ conversion runs.  All voice type records are
# created in the output plugin — we never reference Skyrim.esm VTYPs.
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


def set_voice_type(race_edid: str, gender: str, fid: int):
    """Register a custom VTYP FormID (called by _create_vtyp_records once
    each VTYP record has been allocated and written)."""
    VOICE_TYPE_MAP[(race_edid, gender)] = fid


# ---------------------------------------------------------------------------
# Eye mapping: Oblivion eye FormID → Skyrim HDPT eye FormID
# Full per-race, per-gender, per-color mapping from Skyrim.esm HDPT records.
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


def map_eye_formid(oblivion_edid: str, oblivion_full: str, gender: str = 'Male') -> int:
    """Map an Oblivion eye record to a Skyrim HDPT eye FormID based on name/color/gender."""
    name = (oblivion_full or oblivion_edid or '').lower()
    female = (gender == 'Female')

    def _pick(color_key: str) -> int:
        pair = _SKY_EYE_BY_COLOR.get(color_key, (_SKY_EYE_DEFAULT_M, _SKY_EYE_DEFAULT_F))
        return pair[1] if female else pair[0]

    # Race-specific first
    if 'argonian' in name:
        return _pick('argonian')
    if 'khajiit' in name:
        return _pick('khajiit')
    if 'darkelf' in name or 'dark elf' in name or 'dunmer' in name:
        return _pick('darkelf_red')
    if 'highelf' in name or 'high elf' in name or 'altmer' in name:
        return _pick('highelf_yellow')
    if 'woodelf' in name or 'wood elf' in name or 'bosmer' in name:
        return _pick('woodelf_brown')
    if 'orc' in name or 'orsimer' in name:
        if 'blue' in name or 'ice' in name:
            return _pick('orc_blue')
        if 'red' in name:
            return _pick('orc_red')
        return _pick('orc_grey')
    # Color fallback
    if 'blue' in name or 'dark blue' in name:
        return _pick('blue')
    if 'ice' in name or 'light blue' in name:
        return _pick('ice_blue')
    if 'green' in name:
        return _pick('green')
    if 'grey' in name or 'gray' in name or 'silver' in name:
        return _pick('grey')
    if 'hazel' in name or 'amber' in name:
        return _pick('hazel')
    if 'brown' in name or 'dark' in name:
        return _pick('brown')
    return _SKY_EYE_DEFAULT_F if female else _SKY_EYE_DEFAULT_M


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
# All 57 Oblivion HAIR records mapped to the best-matching Skyrim HDPT.
# Race groupings: Human = Nord/Imperial/Breton/Redguard (shared assets)
#                 Elf = HighElf/DarkElf/WoodElf (shared elf hair)
#                 Orc, Khajiit, Argonian each have dedicated sets.
# When multiple Skyrim styles are equally good, we use the TES4 FormID
# as an index to cycle through them for variety.
# ---------------------------------------------------------------------------

# Human female hair FormIDs from Skyrim.esm (Hair01–Hair21 + 4 Redguard)
_HUMAN_F_HAIR = [
    0x000511A7,  # HairFemaleNord01  Female\Hair01.nif
    0x00051193,  # HairFemaleNord02  Female\Hair02.nif
    0x00051177,  # HairFemaleNord03  Female\Hair03.nif
    0x00051176,  # HairFemaleNord04
    0x00051172,  # HairFemaleNord05
    0x0005114A,  # HairFemaleNord06
    0x00051148,  # HairFemaleNord07
    0x00051146,  # HairFemaleNord08
    0x0005110E,  # HairFemaleNord09
    0x00051107,  # HairFemaleNord10
    0x00051104,  # HairFemaleNord11
    0x000510F9,  # HairFemaleNord12
    0x000EAA70,  # HairFemaleNord13
    0x000EAA71,  # HairFemaleNord14
    0x000EAA72,  # HairFemaleNord15
    0x000EAA73,  # HairFemaleNord16
    0x000EAA74,  # HairFemaleNord17
    0x000EAA75,  # HairFemaleNord18
    0x000EAA76,  # HairFemaleNord19
    0x000EAA77,  # HairFemaleNord20
    0x00106B16,  # HairFemaleNord21
    0x000510B3,  # HairFemaleRedguard01
    0x000510B2,  # HairFemaleRedguard02
    0x001017ED,  # HairFemaleRedguard03
    0x001017EE,  # HairFemaleRedguard04
    0x0010F79E,  # HairFemaleImperial1
]

# Human male hair FormIDs from Skyrim.esm (Hair01–Hair20 + Redguard variants)
_HUMAN_M_HAIR = [
    0x00051507,  # HairMaleNord01  Male\Hair01.nif
    0x000514D5,  # HairMaleNord02
    0x000514B8,  # HairMaleNord03
    0x00051457,  # HairMaleNord04
    0x00051416,  # HairMaleNord05
    0x00051412,  # HairMaleNord06
    0x00051410,  # HairMaleNord07
    0x00051408,  # HairMaleNord08
    0x00051406,  # HairMaleNord09
    0x0005140E,  # HairMaleNord10
    0x0005140C,  # HairMaleNord11
    0x0005140A,  # HairMaleNord12
    0x000DFB30,  # HairMaleNord13
    0x000DFC00,  # HairMaleNord14
    0x000DFC01,  # HairMaleNord15
    0x000DFC02,  # HairMaleNord16
    0x000DFC03,  # HairMaleNord17
    0x000DFC04,  # HairMaleNord18
    0x000DFC05,  # HairMaleNord19
    0x000DFC06,  # HairMaleNord20
    0x00051403,  # HairMaleRedguard1
    0x000513FF,  # HairMaleRedguard2
    0x000513FE,  # HairMaleRedguard3
    0x000513FD,  # HairMaleRedguard4
    0x000513FC,  # HairMaleRedguard5
    0x000513FB,  # HairMaleRedguard6
    0x000513F7,  # HairMaleRedguard7
    0x000E756D,  # HairMaleRedguard8
    0x00051405,  # HairMaleImperial1
]

# Elf female hair (Elf\Female\Hair01–10 + DarkElf-specific)
_ELF_F_HAIR = [
    0x000EC3C2,  # HairFemaleElf01  Elf\Female\Hair01.nif
    0x000EC3C3,  # HairFemaleElf02
    0x000EC3C4,  # HairFemaleElf03
    0x000EC3C5,  # HairFemaleElf04
    0x000EC3C6,  # HairFemaleElf05
    0x000EC3C7,  # HairFemaleElf06
    0x000EC3C8,  # HairFemaleElf07
    0x000EC3C9,  # HairFemaleElf08
    0x000EC3CA,  # HairFemaleElf09
    0x000EC3CB,  # HairFemaleElf10
    0x0008CA6F,  # HairFemaleDarkElf01
    0x0008CA83,  # HairFemaleDarkElf02
    0x0008CA84,  # HairFemaleDarkElf03
    0x0008CA85,  # HairFemaleDarkElf04
    0x0008CA86,  # HairFemaleDarkElf05
    0x000EF317,  # HairFemaleDarkElf06
    0x000EF319,  # HairFemaleDarkElf07
    0x000EF31B,  # HairFemaleDarkElf08
]

# Elf male hair (Elf\Male\Hair01–09 + DarkElf-specific)
_ELF_M_HAIR = [
    0x000EC174,  # HairMaleElf01  Elf\Male\Hair01.nif
    0x000EC3B0,  # HairMaleElf02
    0x000EC3B1,  # HairMaleElf03
    0x000EC3B2,  # HairMaleElf04
    0x000EC3B3,  # HairMaleElf05
    0x000EC3B4,  # HairMaleElf06
    0x000EC3B5,  # HairMaleElf07
    0x000EC3B6,  # HairMaleElf08
    0x000EC3B7,  # HairMaleElf09
    0x0005107E,  # HairMaleDarkElf01
    0x0005107D,  # HairMaleDarkElf02
    0x0005107C,  # HairMaleDarkElf03
    0x0005107B,  # HairMaleDarkElf04
    0x00051054,  # HairMaleDarkElf05
    0x000EB7F5,  # HairMaleDarkElf06
    0x000EB7F7,  # HairMaleDarkElf07
    0x000F1A99,  # HairMaleDarkElf08
    0x000F1A9A,  # HairMaleDarkElf09
]

# Orc female hair (FemaleOrcHair01–17)
_ORC_F_HAIR = [
    0x00085DC8,  # HairFemaleOrc01
    0x00085DCA,  # HairFemaleOrc02
    0x00085DE7,  # HairFemaleOrc03
    0x00085DE8,  # HairFemaleOrc04
    0x00085DE9,  # HairFemaleOrc05
    0x00085DEA,  # HairFemaleOrc06
    0x00085DEB,  # HairFemaleOrc07
    0x00085DEC,  # HairFemaleOrc08
    0x00085DE6,  # HairFemaleOrc09
    0x00085DE5,  # HairFemaleOrc10
    0x00085DE4,  # HairFemaleOrc11
    0x00085DE2,  # HairFemaleOrc12
    0x00085DCB,  # HairFemaleOrc13
    0x001033B2,  # HairFemaleOrc14
    0x001033B3,  # HairFemaleOrc15
    0x001062A2,  # HairFemaleOrc17
]

# Orc male hair (MaleOrcHair01–27)
_ORC_M_HAIR = [
    0x0004154E,  # HairMaleOrc01
    0x0005E797,  # HairMaleOrc02
    0x0005E799,  # HairMaleOrc03
    0x0005E796,  # HairMaleOrc04
    0x0005E79A,  # HairMaleOrc05
    0x0005E79B,  # HairMaleOrc06
    0x0005E79D,  # HairMaleOrc07
    0x0005E7A2,  # HairMaleOrc08
    0x0005E7A7,  # HairMaleOrc09
    0x0005E7D1,  # HairMaleOrc10
    0x0005E7D3,  # HairMaleOrc11
    0x0005E7E9,  # HairMaleOrc12
    0x0005E7ED,  # HairMaleOrc13
    0x0005E7EE,  # HairMaleOrc14
    0x0005E810,  # HairMaleOrc15
    0x0005E814,  # HairMaleOrc16
    0x0005E815,  # HairMaleOrc17
    0x0005E83A,  # HairMaleOrc18
    0x0005E842,  # HairMaleOrc19
    0x0005E843,  # HairMaleOrc20
    0x0005E844,  # HairMaleOrc21
    0x0005E891,  # HairMaleOrc22
    0x0005E892,  # HairMaleOrc23
    0x0005E8A3,  # HairMaleOrc24
    0x0005E79F,  # HairMaleOrc25
    0x0005E79E,  # HairMaleOrc26
    0x0005E818,  # HairMaleOrc27
]

# Khajiit female
_KHJ_F_HAIR = [
    0x000D3354,  # HairKhajiitFemale01
    0x000D3356,  # HairKhajiitFemale02
    0x000D3357,  # HairKhajiitFemale03
    0x000D3359,  # HairKhajiitFemale04
    0x000829C2,  # HairKhajiitFemale05
    0x000829C3,  # HairKhajiitFemale06
    0x000829C4,  # HairKhajiitFemale07
    0x000829C5,  # HairKhajiitFemale08
    0x000EE85A,  # HairKhajiitFemale09
    0x000EE85B,  # HairKhajiitFemale10
]

# Khajiit male
_KHJ_M_HAIR = [
    0x000D335B,  # HairKhajiitMale01
    0x000D335D,  # HairKhajiitMale02
    0x000D3363,  # HairKhajiitMale03
    0x000D3364,  # HairKhajiitMale04
    0x000D3365,  # HairKhajiitMale05
    0x000D3366,  # HairKhajiitMale06
    0x000D3367,  # HairKhajiitMale08
    0x000829BB,  # HairKhajiitMale07
    0x000829BC,  # HairKhajiitMale09
    0x000829BD,  # HairKhajiitMale10 (ear tufts base)
]

# Argonian female (ArgonianFemaleHorns01–13)
_ARG_F_HAIR = [
    0x000D3340,  # HairArgonianFemale01
    0x000D3341,  # HairArgonianFemale02
    0x000D3342,  # HairArgonianFemale03
    0x000D3343,  # HairArgonianFemale04
    0x000D3344,  # HairArgonianFemale05
    0x000D3345,  # HairArgonianFemale06
    0x000D3346,  # HairArgonianFemale07
    0x000D3347,  # HairArgonianFemale08
    0x000D3348,  # HairArgonianFemale09
    0x000D3349,  # HairArgonianFemale10
    0x000E163D,  # HairArgonianFemale11
    0x000A2CF6,  # HairArgonianFemale12
    0x000B30F4,  # HairArgonianFemale13
]

# Argonian male (ArgonianMaleHorns01–10)
_ARG_M_HAIR = [
    0x000D334A,  # HairArgonianMale01
    0x000D334B,  # HairArgonianMale02
    0x000D334C,  # HairArgonianMale03
    0x000D334D,  # HairArgonianMale04
    0x000D334E,  # HairArgonianMale05
    0x000D334F,  # HairArgonianMale06
    0x000D3350,  # HairArgonianMale07
    0x000D3351,  # HairArgonianMale08
    0x000D3352,  # HairArgonianMale09 (non-extra)
    0x000D3353,  # HairArgonianMale10
]

# TES4 HAIR FormID (base, no load-order byte) → Skyrim HDPT hair FormIDs
# Format: {tes4_fid: (male_hdpt, female_hdpt)}
# For "Both" gender Oblivion hairs: both slots filled with gender-appropriate Skyrim hair.
# For single-gender Oblivion hairs: same result (we still use gender from NPC).
TES4_HAIR_FID_TO_SKY_HDPT: dict[int, tuple[int, int]] = {
    # ---- HUMAN hairs (Imperial, Nord, Breton, Redguard share human pool) ----
    0x0018A891: (_HUMAN_M_HAIR[0],  _HUMAN_F_HAIR[0]),   # Cropped       → Nord01 M/F
    0x00027FF2: (_HUMAN_M_HAIR[2],  _HUMAN_F_HAIR[2]),   # MediumLength  → Nord03 M/F
    0x00090475: (_HUMAN_M_HAIR[4],  _HUMAN_F_HAIR[4]),   # Loose         → Nord05 M/F
    0x0002C4D0: (_HUMAN_M_HAIR[6],  _HUMAN_F_HAIR[6]),   # Ponytail      → Nord07 M/F
    0x000950EB: (_HUMAN_M_HAIR[8],  _HUMAN_F_HAIR[8]),   # PonytailTwist → Nord09 / Nord09
    0x00069472: (_HUMAN_M_HAIR[10], _HUMAN_F_HAIR[10]),  # HumanFringes  → Nord11 M/F
    0x00064211: (_HUMAN_M_HAIR[28], _HUMAN_F_HAIR[25]),  # ImperialHeadband(Military) → Imperial M/F
    0x00064C7D: (_HUMAN_M_HAIR[12], _HUMAN_F_HAIR[12]),  # ImperialBald(Thinning)    → Nord13 M/F
    0x00177861: (_HUMAN_M_HAIR[14], _HUMAN_F_HAIR[14]),  # Blindfold     → Nord15 M/F

    # ---- ELF hairs (HighElf, DarkElf, WoodElf) ----
    0x0001DA82: (_ELF_M_HAIR[0],  _ELF_F_HAIR[0]),   # ElfPonytail / Windbound   → Elf01
    0x0001DA83: (_ELF_M_HAIR[2],  _ELF_F_HAIR[2]),   # ElfBraid / Wind Braids    → Elf03
    0x000690BF: (_ELF_M_HAIR[4],  _ELF_F_HAIR[4]),   # HighElfCone / Upswept     → Elf05 M / Elf05 F
    0x000690C0: (_ELF_M_HAIR[5],  _ELF_F_HAIR[5]),   # HighElfPeak / High Style
    0x000690C1: (_ELF_M_HAIR[6],  _ELF_F_HAIR[6]),   # HighElfBun / Pulled Knot
    0x00069474: (_ELF_M_HAIR[7],  _ELF_F_HAIR[7]),   # HighElfpony / Rogue Knot
    0x0007B792: (_ELF_M_HAIR[8],  _ELF_F_HAIR[8]),   # HighElfClassic / Oiled
    0x0006420D: (_ELF_M_HAIR[9],  _ELF_F_HAIR[9]),   # DarkElfMohawk / Ridgeback → DarkElf01
    0x00064214: (_ELF_M_HAIR[11], _ELF_F_HAIR[11]),  # DarkElfMane / Windswept   → DarkElf03
    0x000690BB: (_ELF_M_HAIR[13], _ELF_F_HAIR[13]),  # DarkElfTopknot / Quick Knot → DarkElf05 M, DarkElf05 F
    0x000690C2: (_ELF_M_HAIR[14], _ELF_F_HAIR[14]),  # DarkElfFringe / Court
    0x000690BC: (_ELF_M_HAIR[3],  _ELF_F_HAIR[3]),   # WoodElfSpiky / Mane → Elf04
    0x000690BD: (_ELF_M_HAIR[1],  _ELF_F_HAIR[1]),   # WoodElfPony / Tall Knot → Elf02
    0x00069473: (_ELF_M_HAIR[6],  _ELF_F_HAIR[6]),   # WoodElfFringes / Stick Twist

    # ---- ORC hairs ----
    0x0006420F: (_ORC_M_HAIR[0],  _ORC_F_HAIR[0]),   # OrcTopknot      → Orc01
    0x00064217: (_ORC_M_HAIR[2],  _ORC_F_HAIR[2]),   # OrcStubs(CoupKnots)  → Orc03
    0x000651D7: (_ORC_M_HAIR[4],  _ORC_F_HAIR[4]),   # OrcRomantic(Untouched) → Orc05
    0x00064218: (_ORC_M_HAIR[6],  _ORC_F_HAIR[6]),   # OrcBun(NomadMatron)   → Orc07 M, Orc06 F
    0x0006421A: (_ORC_M_HAIR[8],  _ORC_F_HAIR[8]),   # OrcPlaits          → Orc09 M, Orc09 F
    0x000663FC: (_ORC_M_HAIR[10], _ORC_F_HAIR[10]),  # OrcBraids(NomadBraids) → Orc11
    0x00066A27: (_ORC_M_HAIR[12], _ORC_F_HAIR[12]),  # OrcTwoBraids(WarBraids)
    0x00066A28: (_ORC_M_HAIR[14], _ORC_F_HAIR[14]),  # OrcOneBraid(FlipBraidKnot)
    0x00066A29: (_ORC_M_HAIR[16], _ORC_F_HAIR[15]),  # OrcHeadband
    0x00066A2A: (_ORC_M_HAIR[18], _ORC_F_HAIR[0]),   # OrcUpdo(PlainsCoif)

    # ---- KHAJIIT hairs ----
    0x000653CF: (_KHJ_M_HAIR[0],  _KHJ_F_HAIR[0]),   # KhajiitBraids
    0x000653D0: (_KHJ_M_HAIR[1],  _KHJ_F_HAIR[1]),   # KhajiitDreds
    0x000653D1: (_KHJ_M_HAIR[2],  _KHJ_F_HAIR[2]),   # KhajiitCommon
    0x000653D2: (_KHJ_M_HAIR[3],  _KHJ_F_HAIR[3]),   # KhajiitFeathers (Feathered Headdress)
    0x000653D3: (_KHJ_M_HAIR[4],  _KHJ_F_HAIR[4]),   # KhajiitHeadBand
    0x000653D4: (_KHJ_M_HAIR[5],  _KHJ_F_HAIR[5]),   # KhajiitMane
    0x000653D5: (_KHJ_M_HAIR[6],  _KHJ_F_HAIR[6]),   # KhajiitJeweled (Jeweled Headdress)
    0x000C4820: (_KHJ_M_HAIR[7],  _KHJ_F_HAIR[7]),   # KhajiitEarrings
    0x000C4821: (_KHJ_M_HAIR[8],  _KHJ_F_HAIR[8]),   # KhajiitWisps

    # ---- ARGONIAN hairs ----
    0x00064F31: (_ARG_M_HAIR[0],  _ARG_F_HAIR[0]),   # ArgonianDecoratedSpikes
    0x00064F32: (_ARG_M_HAIR[1],  _ARG_F_HAIR[1]),   # ArgonianFins
    0x00064F33: (_ARG_M_HAIR[2],  _ARG_F_HAIR[2]),   # ArgonianJeweledFins
    0x00064F34: (_ARG_M_HAIR[3],  _ARG_F_HAIR[3]),   # ArgonianRidge
    0x00064F35: (_ARG_M_HAIR[4],  _ARG_F_HAIR[4]),   # ArgonianSpikes
    0x00064F36: (_ARG_M_HAIR[5],  _ARG_F_HAIR[5]),   # ArgonianSpines

    # ---- REDGUARD-specific hairs (also available for human pool) ----
    0x00064210: (_HUMAN_M_HAIR[21], _HUMAN_F_HAIR[22]),  # RedguardCoil(BunTwist)   → Redguard
    0x00064215: (_HUMAN_M_HAIR[22], _HUMAN_F_HAIR[23]),  # RedguardClassic(Frizzy)  → Redguard3
    0x00064216: (_HUMAN_M_HAIR[23], _HUMAN_F_HAIR[24]),  # RedguardCornrows(RidgeRows)
    0x00066F21: (_HUMAN_M_HAIR[24], _HUMAN_F_HAIR[21]),  # RedguardDredz(ThickRows)

    # ---- NORD/BRETON/IMPERIAL common hairs (gender-specific in TES4) ----
    0x0006420E: (_HUMAN_M_HAIR[1],  _HUMAN_F_HAIR[1]),   # NordBaldPony(Gathered)
    0x00064213: (_HUMAN_M_HAIR[3],  _HUMAN_F_HAIR[3]),   # BretonTonsure

    # ---- DREMORA hairs (use elf-type as closest) ----
    0x0003832E: (_ELF_M_HAIR[0],  _ELF_F_HAIR[0]),   # dremoraHair
    0x0003B59C: (_ELF_M_HAIR[1],  _ELF_F_HAIR[1]),   # DremoraHairB
    0x0003E914: (_ELF_M_HAIR[2],  _ELF_F_HAIR[2]),   # DremoraHairLord
}

# Race-based fallback pools when TES4 FormID not found in mapping
_RACE_HAIR_FALLBACK: dict[str, tuple[list, list]] = {
    'Argonian':  (_ARG_M_HAIR,  _ARG_F_HAIR),
    'Khajiit':   (_KHJ_M_HAIR,  _KHJ_F_HAIR),
    'Orc':       (_ORC_M_HAIR,  _ORC_F_HAIR),
    'DarkElf':   (_ELF_M_HAIR,  _ELF_F_HAIR),
    'HighElf':   (_ELF_M_HAIR,  _ELF_F_HAIR),
    'WoodElf':   (_ELF_M_HAIR,  _ELF_F_HAIR),
    # Human races all share the human pool
    'Imperial':  (_HUMAN_M_HAIR, _HUMAN_F_HAIR),
    'Nord':      (_HUMAN_M_HAIR, _HUMAN_F_HAIR),
    'Breton':    (_HUMAN_M_HAIR, _HUMAN_F_HAIR),
    'Redguard':  (_HUMAN_M_HAIR, _HUMAN_F_HAIR),
}


def resolve_hair_hdpt(tes4_hair_fid: int, race_edid: str, gender: str) -> int:
    """Map a TES4 HAIR FormID to the best Skyrim HDPT hair FormID.

    Looks up the direct mapping table first; falls back to a race-based
    pool indexed by the TES4 FormID for variety.
    """
    female = (gender == 'Female')
    base_fid = tes4_hair_fid & 0x00FFFFFF
    pair = TES4_HAIR_FID_TO_SKY_HDPT.get(base_fid)
    if pair is not None:
        return pair[1] if female else pair[0]
    # Fallback: cycle through the race pool using the lower bits of the FormID
    pools = _RACE_HAIR_FALLBACK.get(race_edid, (_HUMAN_M_HAIR, _HUMAN_F_HAIR))
    pool = pools[1] if female else pools[0]
    return pool[base_fid % len(pool)]


# ---------------------------------------------------------------------------
# Hair color mapping: Oblivion HCLR (R,G,B) → Skyrim CLFM FormID
# All 15 Skyrim hair colors from Skyrim.esm (verified FormIDs & RGB values).
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
# Maps Oblivion magic effect codes to their Skyrim equivalents.
# Effects with no clean equivalent get 0 (null).
# ---------------------------------------------------------------------------

MGEF_CODE_TO_SKYRIM = {
    # All FormIDs verified against Skyrim.esm MGEF records (950 total).
    # -- Alteration --
    'BRDN': 0,                     # Burden — no Skyrim equivalent
    'FTHR': 0x0003EB01,            # Feather → AlchFortifyCarryWeight
    'FISH': 0x0003AE9E,            # Fire Shield → FireCloakFFSelf
    'FRSH': 0x0003AEA0,            # Frost Shield → FrostCloakFFSelf
    'LISH': 0x0003AEA1,            # Lightning Shield → ShockCloakFFSelf
    'LOCK': 0,                     # Lock — no equivalent
    'OPEN': 0,                     # Open — no equivalent
    'SHLD': 0x00051B15,            # Shield → ArmorFFSelf0
    'WBUA': 0x0003AC2D,            # Water Breathing → AlchWaterbreathing
    'WKFW': 0x00073F2D,            # Weakness to Fire → AlchWeaknessFire
    'WKFR': 0x00073F2E,            # Weakness to Frost → AlchWeaknessFrost
    'WKSK': 0x00073F2F,            # Weakness to Shock → AlchWeaknessShock
    'WKMA': 0x00073F51,            # Weakness to Magic → AlchWeaknessMagic
    'WKPO': 0x00090042,            # Weakness to Poison → AlchWeaknessPoison
    # -- Conjuration --
    'BABO': 0,                     # Bound Boots — no equivalent
    'BACT': 0,                     # Bound Cuirass — no equivalent
    'BAGA': 0,                     # Bound Gauntlets — no equivalent
    'BAGR': 0,                     # Bound Greaves — no equivalent
    'BAHE': 0,                     # Bound Helmet — no equivalent
    'BASH': 0,                     # Bound Shield — no equivalent
    'BWAX': 0x0001CE9E,            # Bound Axe → BoundBattleAxeFFSelf
    'BWBO': 0x0001CEA0,            # Bound Bow → BoundBowFFSelf
    'BWDA': 0x0001CE9F,            # Bound Dagger → BoundSwordFFSelf
    'BWMA': 0x0001CE9F,            # Bound Mace → BoundSwordFFSelf (closest)
    'REAN': 0x00065BD6,            # Reanimate → ReanimateFFAimed0
    'SMAC': 0x000640B4,            # Summon Ancestor → SummonFamiliar
    'SMBO': 0x000640B4,            # Summon Bear → SummonFamiliar
    'SMCL': 0x000640B4,            # Summon Clannfear → SummonFamiliar
    'SMDM': 0x0010DDED,            # Summon Dremora → SummonDremoraLord
    'SMFL': 0x0001CEAA,            # Summon Flame Atronach → SummonFlameAtronach
    'SMFR': 0x0001CEAB,            # Summon Frost Atronach → SummonFrostAtronach
    'SMGH': 0x000640B4,            # Summon Ghost → SummonFamiliar
    'SMLI': 0x000640B4,            # Summon Lich → SummonFamiliar
    'SMSK': 0x000640B4,            # Summon Skeleton → SummonFamiliar
    'SMSP': 0x0001CEAC,            # Summon Storm Atronach → SummonStormAtronach
    'SMZB': 0x000640B4,            # Summon Zombie → SummonFamiliar
    'TNUN': 0x0004B145,            # Turn Undead → TurnUndeadFFAimed25
    # -- Destruction --
    'DGAT': 0,                     # Damage Attribute — removed
    'DGFA': 0x0003A2C6,            # Damage Fatigue → AlchDamageStamina
    'DGHE': 0x0003EB42,            # Damage Health → AlchDamageHealth
    'DGSP': 0x0003A2B6,            # Damage Magicka → AlchDamageMagicka
    'DRAT': 0,                     # Drain Attribute — removed
    'DRFA': 0x0003A2C6,            # Drain Fatigue → AlchDamageStamina
    'DRHE': 0x0003EB42,            # Drain Health → AlchDamageHealth
    'DRSP': 0x0003A2B6,            # Drain Magicka → AlchDamageMagicka
    'DRSK': 0,                     # Drain Skill — removed
    'FIDG': 0x00012F03,            # Fire Damage → FireDamageFFAimed
    'FRDG': 0x0001CEA2,            # Frost Damage → FrostDamageFFAimed
    'SHDG': 0x0001CEA8,            # Shock Damage → ShockDamageFFAimed
    'DISE': 0,                     # Disease — no direct equivalent
    'POSN': 0x0003EB42,            # Poison → AlchDamageHealth (closest)
    'WKDI': 0,                     # Weakness to Disease
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
    'ABAT': 0,                     # Absorb Attribute — removed
    'ABFA': 0x000F1D01,            # Absorb Fatigue → AbsorbStaminaConcAimed
    'ABHE': 0x0008D5BE,            # Absorb Health → AbsorbHealthConcAimed
    'ABSK': 0,                     # Absorb Skill — removed
    'ABSP': 0x000F1CFE,            # Absorb Magicka → AbsorbMagickaConcAimed
    'CUDI': 0x000AE722,            # Cure Disease → AlchCureDisease
    'CUPA': 0x000AE722,            # Cure Paralysis → AlchCureDisease (closest)
    'CUPO': 0x00109ADD,            # Cure Poison → AlchCurePoison
    'FOAT': 0,                     # Fortify Attribute → various
    'FOFA': 0x0003EAF9,            # Fortify Fatigue → AlchFortifyStamina
    'FOHE': 0x0003EAF3,            # Fortify Health → AlchFortifyHealth
    'FOSP': 0x0003EAF8,            # Fortify Magicka → AlchFortifyMagicka
    'FOSK': 0,                     # Fortify Skill → various
    'REAT': 0,                     # Restore Attribute — removed
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
    'RFDG': 0,                     # Reflect Damage — no equivalent
    'REDG': 0,                     # Reflect Damage → no equivalent
    'DSPL': 0,                     # Dispel → no equivalent
    'DTCT': 0x0001EA74,            # Detect Life → DetectLifeFriendInteriorConcSelf
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

# BOOK pickup sound (INAM) — generic book pickup sound from Skyrim.esm.
# Sourced from BookSkyrim (0x000E894C) which is the most common general-purpose
# book pickup sound in Skyrim.esm.  All converted books use this default;
# scroll/note variants could be differentiated later by model name.
BOOK_INAM: int = 0x000E894C

# ---------------------------------------------------------------------------
# Invisible marker base-object substitution:
# Oblivion FormID (low 24-bit, no load-order byte) → Skyrim.esm FormID
#
# Oblivion has several invisible STAT "marker" objects used for scripting,
# fast travel, door destinations, orientation, etc. In Skyrim the same
# conceptual markers exist in Skyrim.esm at (mostly) the same low-byte
# FormIDs. Because our FormID remapping shifts every Oblivion record from
# index 0 (0x00…) to index 1 (0x01…), any REFR whose NAME points at one
# of these markers would be redirected into our converted file rather than
# into Skyrim.esm — and would reference a STAT record whose mesh is an
# Oblivion .nif that no longer exists.
#
# The substitution below maps the TES4 raw FormID (before offset) to the
# Skyrim.esm FormID that should be used instead. The Skyrim.esm markers
# listed here are documented in xEdit / USSEP and are stable engine records.
#
# Key:   raw TES4 FormID integer (as stored in the export, no remapping)
# Value: Skyrim.esm FormID (index 0 = Skyrim.esm, already correct)
# ---------------------------------------------------------------------------

TES4_MARKER_FORMID_TO_SKYRIM = {
    0x00000001: 0x0000003B,  # DoorMarker       → XMarker (invisible generic)
    0x00000002: 0x00000002,  # TravelMarker     → same FormID in Skyrim.esm (fast-travel dest)
    0x00000003: 0x00000003,  # NorthMarker      → same FormID in Skyrim.esm
    0x00000005: 0x0000003B,  # DivineMarker     → XMarker (no Skyrim equivalent)
    0x00000006: 0x0000003B,  # TempleMarker     → XMarker (no Skyrim equivalent)
    0x00000010: 0x00000034,  # MapMarker        → XMarkerHeading
    0x00000012: 0x00000002,  # HorseMarker      → TravelMarker (closest)
    0x00000034: 0x00000034,  # XMarkerHeading   → same FormID in Skyrim.esm
    0x0000003B: 0x0000003B,  # XMarker          → same FormID in Skyrim.esm
    0x0000080B: 0x0000003B,  # MarkerTeleport   → XMarker (door-destination marker)
}

# ---------------------------------------------------------------------------
# Weapon equipment type (EQUP) FormIDs — Skyrim.esm
# ETYP subrecord on WEAP records must reference one of these.
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
    8: EQUP_RIGHT_HAND,   # Staff
}

# ---------------------------------------------------------------------------
# Per-anim-type weapon defaults — sourced from vanilla Skyrim iron/steel weapons
# ---------------------------------------------------------------------------
# INAM — Impact Data Set FormIDs (what sound/particle plays when weapon hits)
WEAPON_ANIM_INAM: dict[int, int] = {
    1: 0x00013CAC,   # Sword   → WPNzBlade1HandImpactSet
    2: 0x00013CAC,   # Dagger  → WPNzBlade1HandImpactSet
    3: 0x000193B8,   # WarAxe  → WPNzAxeImpactSet
    4: 0x000193B7,   # Mace    → WPNzBluntImpactSet
    5: 0x000949D5,   # GreatSword → WPNzBlade2HandImpactSet
    6: 0x00036A5B,   # Battleaxe  → WPNzAxeLargeImpactSet
    7: 0x000193B9,   # Bow     → WPNzArrowImpactSet
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

# Skyrim footstep set for heavy armor boots (FSTArmorHeavyFootstepSet)
HEAVY_ARMOR_FOOTSTEP_SET = 0x00021487
# Skyrim footstep set for light armor boots (FSTArmorLightFootstepSet)
LIGHT_ARMOR_FOOTSTEP_SET = 0x00024238
# Skyrim footstep set for clothing/barefoot (FSTBarefootFootstepSet)
CLOTHING_FOOTSTEP_SET = 0x00024237

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
