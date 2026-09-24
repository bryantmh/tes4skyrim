"""Constant lookup tables for TES4→Papyrus script conversion."""

import hashlib
import re

from asset_convert.game_paths import current_namespace

# ===========================================================================
# Constants
# ===========================================================================

#: TES4 player-base script rides a QUST alias. See: docs/commentary/script_convert.md#player-base-script-needs-quest-alias
PLAYER_ALIAS_EXTENDS = 'ReferenceAlias'

from script_convert.reserved_names import papyrus_reserved


# Oblivion type -> Papyrus type mapping
TYPE_MAP = {
    'short': 'Int',
    'long':  'Int',
    'int':   'Int',
    'float': 'Float',
    'ref':   'ObjectReference',
    'reference': 'ObjectReference',
    # OBSE types.  Without these the variable got NO declaration at all and
    # every use was an undefined identifier (HMSfromFloat24h builds its return
    # value in a `string_var sTime`).  Papyrus String is the direct equivalent;
    # array_var has none, so it falls back to a String the script can at least
    # declare and assign.
    'string_var': 'String',
    'array_var':  'String',
}

# Actor value name mapping (TES4 -> TES5)
# TES4 attribute names. SKYRIM HAS NO ATTRIBUTES — Strength, Intelligence,
# Willpower, Agility, Speed, Endurance, Personality and Luck do not exist as
# actor values, and no TES5 actor value is a faithful stand-in, because every
# candidate sits on a different scale than TES4's 0-100.
#
# They used to be aliased onto the nearest-looking AV here
# (strength->UnarmedDamage, endurance->HealRate, agility/speed/acrobatics->
# SpeedMult, personality->Speechcraft, luck->LuckModifier — which is not even
# a real AV name, so it failed silently). That broke every Morroblivion guild:
# the Fighters Guild gates each rank on `Player.GetAV Strength >= 30 &&
# Player.GetAV Endurance >= 30`, and UnarmedDamage sits near 0, so no character
# could ever qualify at any level; the Thieves Guild's Agility gate read
# SpeedMult (~100) and passed unconditionally instead.
#
# An attribute read is now a no-op that returns ATTRIBUTE_STUB_VALUE, so the
# gate falls OPEN, and an attribute write is discarded. Falling open is the
# faithful outcome: an Oblivion attribute gate exists to keep an
# under-developed character out, and a Skyrim character cannot raise an
# attribute at all, so enforcing it would lock the content away permanently
# rather than merely early. Mirrors dialog_conditions._TES4_AV_ATTRIBUTES,
# which drops the equivalent CTDA, and TES4Polyfill.IsTES4Attribute.
TES4_ATTRIBUTES = frozenset({
    'strength', 'intelligence', 'willpower', 'agility',
    'speed', 'endurance', 'personality', 'luck',
})

# Value substituted for a removed attribute read. Above every authored TES4
# attribute threshold (TES4 attributes cap at 100; the highest in the guild
# advancement scripts is 35) so `>=` gates pass, and positive so the rarer
# `> 0` / `!= 0` forms behave the same way.
ATTRIBUTE_STUB_VALUE = '100.0'

ACTOR_VALUE_MAP = {
    'armorer':      'Smithing',
    'athletics':    'Stamina',
    'blade':        'OneHanded',
    'block':        'Block',
    # Blunt is Oblivion's mace/warhammer skill and covers BOTH one- and
    # two-handed blunt weapons; Skyrim splits them. OneHanded matches Blade so
    # a script comparing the two reads one consistent scale, and it is what
    # skyrim_overrides.TES4_SKILL_TO_TES5_INDEX already uses on the record side.
    'blunt':        'OneHanded',
    'handtohand':   'UnarmedDamage',
    'heavyarmor':   'HeavyArmor',
    'alchemy':      'Alchemy',
    'alteration':   'Alteration',
    'conjuration':  'Conjuration',
    'destruction':  'Destruction',
    'illusion':     'Illusion',
    # Mysticism was folded into Illusion in Skyrim (Detect Life, Telekinesis
    # and Soul Trap all became Illusion/Conjuration spells); Alteration was a
    # mismatch with the record side, which already maps it to Illusion.
    'mysticism':    'Illusion',
    'restoration':  'Restoration',
    # Acrobatics and Athletics have no Skyrim skill at all. Stamina is the
    # athletic-capacity value the engine actually tracks, and matches the
    # 0-100 scale a TES4 skill threshold expects far better than SpeedMult
    # (which sits at ~100 for everyone and made every gate pass).
    'acrobatics':   'Stamina',
    'lightarmor':   'LightArmor',
    'marksman':     'Marksman',
    'mercantile':   'Speechcraft',
    'security':     'Lockpicking',
    'sneak':        'Sneak',
    'speechcraft':  'Speechcraft',
    'health':       'Health',
    'magicka':      'Magicka',
    'fatigue':      'Stamina',
    'encumbrance':  'CarryWeight',
    'invisibility': 'Invisibility',
    'chameleon':    'Invisibility',
    'nighteye':     'NightEye',
    'waterbreathing': 'WaterBreathing',
    'waterwalking': 'WaterWalking',
    'paralysis':    'Paralysis',
    'detectlife':   'DetectLifeRange',
    'blindness':    'Blindness',
    # Skyrim has NO silence actor value — the engine's AV name table (verified
    # against SkyrimSE.exe) runs ...Blindness, WeaponSpeedMult... with nothing
    # between, and 'MuteModifier' (what this used to emit) is not a name the
    # engine knows, so every read returned 0 and every write was rejected.
    # Silence is a spell-supplied condition in Skyrim, not a trackable value;
    # omitting it leaves the AV name unmapped, which is the honest outcome.
    'resistfire':   'FireResist',
    'resistfrost':  'FrostResist',
    'resistshock':  'ElectricResist',
    'resistmagic':  'MagicResist',
    'resistdisease':'DiseaseResist',
    'resistpoison': 'PoisonResist',
    'resistnormalweapons': 'DamageResist',
    'aggression':   'Aggression',
    'confidence':   'Confidence',
    # Energy is an AI trait in BOTH games (TES4 AV 35, TES5 AV 2), not a pool.
    # Mapping it to Magicka aliased an AI personality value onto the actor's
    # spell resource, so a scripted energy change silently drained or refilled
    # magicka instead.
    'energy':       'Energy',
    'responsibility': 'Morality',
}


# TES4 global variables that exist in Skyrim — these need GlobalVariable property access
KNOWN_GLOBALS = {
    'gamehour', 'gamedayspassed', 'gameday', 'gamemonth', 'gameyear',
    'timescale',
}

# Crime bounties used to reconstruct TES4's three per-faction crime booleans
# (GetPCFactionMurder / Attack / Steal) from Skyrim's crime-gold split, which is
# the only part of the system Papyrus can reach.  These are the vanilla CRVA
# amounts: every one of Skyrim.esm's 14 real crime factions uses exactly
# murder=1000, assault=40, and the steal multiplier applies to item value.  The
# importer writes the same numbers into every converted crime faction's CRVA
# (tes5_import/record_types/actors.py), so the two sides must stay in step.
TES4_MURDER_BOUNTY = 1000
TES4_ASSAULT_BOUNTY = 40
TES4_STEAL_BOUNTY = 100


# PAPYRUS function names that return Bool.  Checked against the EMITTED text,
# because a TES4 command whose own name is in no table can still convert into a
# bool call -- `GetDisabled` becomes `TES4Polyfill.GetDisabled(...)` and
# `HasMagicEffect` keeps its name, and both need `== 1` collapsed or Papyrus
# rejects the Bool-vs-Int comparison.
PAPYRUS_BOOL_FUNCTIONS = {
    'isdisabled', 'isenabled', 'isdead', 'isincombat', 'issneaking',
    'isweaponout', 'isswimming', 'isghost', 'isininterior', 'isessential',
    'isguard', 'isactionref', 'ischildof', 'isindialoguewithplayer',
    'isrunning', 'isinfaction', 'isarrested', 'isbleedingout',
    'isunconscious', 'iscommanded', 'isplayerteammate', 'ishostile',
    'issprinting', 'isonmount', 'isalerted', 'isequipped', 'ismounted',
    'istrespassing', 'isavrecoverydisabled', 'isfurnitureinuse',
    'isflightblocked', 'isinterior', 'islocked',
    'getdead', 'getdisabled', 'getlocked', 'getghost', 'getisalerted',
    'getincombat', 'getnobleedoutrecovery', 'getisplayablerace',
    'getcurrentweatherpercent', 'getiscurrentpackage',
    'hasspell', 'hasmagiceffect', 'hasperk', 'haseffectkeyword',
    'haskeyword', 'hasnode', 'haslostoref', 'hasreftype',
    'wornhaskeyword', 'pathtoreference',
    # Merged in from converter._BOOL_FUNC_NAMES, which was a SECOND list of
    # Bool-returning Papyrus names written as a regex alternation.  The two
    # disagreed by twelve names -- these -- so whether a Bool got its `as Int`
    # depended on which list the code path happened to consult.
    'isdetectedby', 'haslos', 'cansee', 'isridingmount', 'isanimplaying',
    'getdetected', 'ishostiletoactor', 'isweapondrawn', 'ischild',
    'isalarmed', 'iscompleted', 'isobjectivecompleted',
}


#: Papyrus types a TES4 form-vs-number comparison can leave behind.  The TES4
#: condition function returned a form and has no Papyrus equivalent, so the
#: literal it is compared against is meaningless.
MISMATCH_TYPES = frozenset({'Package', 'Topic', 'MiscObject', 'Quest'})

#: Base-object types: comparing a REFERENCE to one of these means "is this
#: reference an instance of that object", which in Papyrus is GetBaseObject().
BASE_FORM_TYPES = frozenset({'MiscObject', 'Ingredient', 'Potion', 'Weapon',
                             'Armor', 'Book', 'Key'})

#: Event parameters that carry an ObjectReference.  Nothing DECLARES these, so
#: a type lookup finds nothing and every reference rule would skip them.
EVENT_REF_PARAMS = frozenset({'akactionref', 'akactor', 'aktarget',
                              'aksource', 'akspeaker', 'akcaster'})

#: Papyrus calls returning a FORM type narrower (or simply other) than
#: ObjectReference.  TES4 spelled every handle `ref`, and Papyrus converts
#: between none of these implicitly, so a `ref` variable assigned from one of
#: them must be DECLARED as that type -- see the retype pass in
#: `convert_standalone`.  Named rather than inlined into `RETURN_TYPES` because
#: `symbols.py` asks the narrower question "is this call form-typed at all".
_FORM_RETURNING = {
    'getbaseobject': 'Form',
    'getequippedweapon': 'Weapon',
    'getequippedshield': 'Armor',
    'getworncoveringitem': 'Armor',
    'getactorowner': 'ActorBase',
    'getfactionowner': 'Faction',
    'getparentcell': 'Cell',
}

#: Papyrus return type, keyed by PAPYRUS name so aliases resolve via FUNCTION_MAP.
RETURN_TYPES = dict(
    [(n, 'Bool') for n in PAPYRUS_BOOL_FUNCTIONS]

    #: Float reads, under both authored and emitted elapsed-time spellings.
    + [(n, 'Float') for n in (
        'getactorvalue', 'getbaseactorvalue', 'getsecondspassed',
        'getdistance', 'getheadingangle', 'getscale', 'getlevel',
        'getwalkspeed', 'getcurrenttime', 'randomfloat', 'getheight',
        'getwidth', 'getlength', 'getvalue', 'getvaluepercentage',
        'scripteffectelapsedseconds', 'tes4_secondspassed',
        'getcurrentrealtime')]
    + [('get%s%s' % (kind, axis), 'Float')
       for kind in ('position', 'angle') for axis in 'xyz']

    #: Math.psc natives we emit; every one but Ceiling/Floor returns a float.
    + [(n, 'Float') for n in (
        'abs', 'acos', 'asin', 'atan', 'cos', 'pow', 'sin', 'sqrt', 'tan')]

    #: Whole-number reads, plus TES4 names converting to a fixed expression.
    + [(n, 'Int') for n in (
        'getvalueint', 'getcrimegold', 'getitemcount', 'getgoldamount',
        'getlocklevel', 'getdayofweek', 'getdayoftheweek', 'getrandompercent',
        'getrandpercent', 'getpcfame', 'getpcinfamy', 'getinfame',
        'ceiling', 'floor')]

    #: Reference-valued names; the `ak*` event parameters need a downcast.
    + [(n, 'ObjectReference') for n in (
        'getlinkedref', 'placeatme', 'getparentref', 'placeactoratme',
        'geteditorlocation', 'getiteminslot', 'akactionref', 'aknewcontainer',
        'akoldcontainer', 'akcastref', 'akaggressor', 'akcaster')]
    + [(n, 'Actor') for n in (
        'gettargetactor', 'getcasteractor', 'getactorreference',
        'game.getplayer', 'getplayer', 'getcombattarget', 'getkiller',
        'getlastridden', 'findrandomactorfromref')]

    + list(_FORM_RETURNING.items())
)

#: Commands where TES4 passes the axis as the FIRST ARGUMENT but Papyrus spells
#: it in the NAME: `GetPos Z` is `GetPositionZ()`.  Maps the TES4 spelling to
#: the Papyrus STEM, which the axis letter is appended to -- FUNCTION_MAP has
#: no row for these (they are handled by a dedicated branch that builds the
#: name), so typing them needs the stem stated here.
AXIS_COMMANDS = {
    'getpos': 'getposition',
    'getposition': 'getposition',
    'getstartingpos': 'getposition',
    'getangle': 'getangle',
    'getstartingangle': 'getangle',
}


#: See: docs/commentary/script_convert.md#reads-skyrim-cannot-answer
_FORM_TYPE_TESTS = {
    'isdoor', 'isactivator', 'iscontainer', 'isbook', 'isingredient',
    'islight', 'ismisc', 'iskey', 'isclothing', 'isarmor', 'isweapon',
    'ispotion',
}


# Canonical names for known TES4 globals
_GLOBAL_CANONICAL = {
    'gamehour': 'GameHour', 'gamedayspassed': 'GameDaysPassed',
    'gameday': 'GameDay', 'gamemonth': 'GameMonth', 'gameyear': 'GameYear',
    'timescale': 'TimeScale',
}


_RECORD_TYPE_PAPYRUS = {
    # NPC_/CREA are BASE records (TESNPC), so 'Actor' is technically wrong —
    # the VM type-checks VMAD object properties and an Actor-typed property
    # bound to a base form silently reads None in-game. But TES4 scripts use
    # NPC base EditorIDs in reference contexts pervasively (comparisons,
    # assignments), and a blanket ActorBase typing breaks ~1000 script
    # compilations. Instead, handlers whose TES4 argument is base-semantics
    # (SetEssential) override the individual property to ActorBase; a full fix
    # needs base-aware comparison/assignment emission (GetBaseObject()).
    'QUST': 'Quest', 'NPC_': 'Actor', 'CREA': 'Actor',
    'FACT': 'Faction', 'GLOB': 'GlobalVariable',
    'SPEL': 'Spell', 'ENCH': 'Enchantment', 'MGEF': 'MagicEffect',
    'CELL': 'Cell', 'WRLD': 'WorldSpace', 'PACK': 'Package',
    'SOUN': 'Sound', 'SNDR': 'Sound', 'DIAL': 'Topic', 'RACE': 'Race',
    'MESG': 'Message', 'MSTT': 'Static',
    'FLST': 'FormList', 'KYWD': 'Keyword', 'LVLI': 'LeveledItem',
    'LVLN': 'LeveledActor', 'LVSP': 'LeveledSpell',
    'WEAP': 'Weapon', 'ARMO': 'Armor', 'BOOK': 'Book',
    'ALCH': 'Potion', 'INGR': 'Ingredient', 'LIGH': 'Light',
    'MISC': 'MiscObject', 'KEYM': 'Key', 'AMMO': 'Ammo',
    # TES4-only item types, typed by what the IMPORTER writes them as (measured
    # over Morrowind_ob: 565 CLOT -> ARMO, 22 APPA -> MISC).  Leaving them
    # unmapped fell through to the 'ObjectReference' default, which is not a
    # base-object type -- a property bound to the converted ARMO then failed
    # with "cannot be bound because (...) is not the right type" and read None,
    # so `player.removeitem <ring>` silently did nothing.
    'CLOT': 'Armor', 'APPA': 'MiscObject', 'SLGM': 'SoulGem',
    # LVLC is Oblivion's leveled CREATURE list; the importer writes it as a
    # Skyrim LVLN (measured: 682 in Oblivion.esm). SGST (sigil stone) becomes a
    # SCRL (150). Both are base objects, so leaving them on the
    # 'ObjectReference' default made their properties fail to bind
    # (SE12GnarlSpawnerNewSCRIPT's PlaceAtMe spawners among them).
    'LVLC': 'LeveledActor', 'SGST': 'Scroll',
    'ACTI': 'Activator', 'DOOR': 'ObjectReference',
    'CONT': 'ObjectReference', 'STAT': 'ObjectReference',
    'FURN': 'ObjectReference', 'FLOR': 'ObjectReference',
    'EFSH': 'EffectShader', 'WTHR': 'Weather',
    'CSTY': 'Form', 'CLAS': 'Form',
    'EYES': 'ObjectReference', 'HAIR': 'ObjectReference',
    'TREE': 'ObjectReference', 'GRAS': 'ObjectReference',
    'ACHR': 'Actor', 'ACRE': 'Actor',
    'REFR': 'ObjectReference',
}


# ===========================================================================
# Utility functions (used by both converter.py and pipeline.py)
# ===========================================================================

def sanitize_name(name: str) -> str:
    """Sanitize a script name for use as a filename."""
    return re.sub(r'[^\w]', '_', name)


# Papyrus caps a ScriptName at 38 characters; the compiler rejects anything
# longer outright ("...is too long, please shorten it to 38 characters or
# less"), so the script never produces a .pex and the object it is attached to
# silently does nothing in-game.  81 Oblivion script EditorIDs overflow once the
# TES4_ prefix is added.
PAPYRUS_MAX_SCRIPT_NAME = 38


def music_type_editor_id(plugin: str, category: str) -> str:
    """EditorID of the MUSC a TES4 music category converts to.

    MUST match tes5_import.record_types.music.musc_editor_id exactly: the
    importer writes the record under this name and the script converter
    declares a Papyrus property under it, so the two disagreeing means every
    StreamMusic call binds to nothing.
    """
    stem = ''.join(c for c in plugin if c.isalnum())
    return 'MUS%s%s' % (stem, category.capitalize())


def music_cue_editor_id(plugin: str, source_rel: str) -> str:
    """EditorID of the per-cue MUSC built for one `Special/` track.

    A script names a specific FILE (`StreamMusic "data/music/special/x.mp3"`),
    so each Special track gets its own addressable MUSC.  Mirrors the
    'MUSC_TRACK' branch of tes5_import.record_types.music.build_music_records.
    """
    stem = ''.join(c for c in plugin if c.isalnum())
    tail = source_rel.rsplit('/', 1)[-1].rsplit('.', 1)[0]
    tail = ''.join(c if c.isalnum() else '_' for c in tail)
    return 'MUSCue%s_%s' % (stem, tail)


def script_prefix(suffix: str = '_') -> str:
    """The generated-script name prefix for the ACTIVE game."""
    return current_namespace().upper() + suffix


def is_generated_script_type(ptype: str) -> bool:
    """Whether `ptype` names a script class this pipeline generated.

    See: docs/commentary/script_convert.md#generated-script-types
    """
    return bool(ptype) and ptype.startswith(script_prefix())


def generated_script_stem(ptype: str) -> str:
    """`ptype` with the generated-script prefix removed.

    See: docs/commentary/script_convert.md#generated-script-types
    """
    return ptype[len(script_prefix()):] if is_generated_script_type(ptype) \
        else ptype


def papyrus_script_name(edid: str, prefix: str = None) -> str:
    """Return the Papyrus ScriptName for a TES4 script EditorID.

    MUST be the single source of truth: the same name is written as the .psc
    ScriptName, the .psc filename, and the ScriptName inside the VMAD that binds
    the script to its record.  If those three ever disagree the binding breaks,
    so every producer calls this rather than formatting the name itself.

    Over-long names are truncated and given a short hash of the FULL original,
    which keeps them unique (several Oblivion scripts differ only in a suffix
    past the cut, e.g. TrigZoneCloseCurrentOblivionRdCitadel0{1..5}SCRIPT).
    """
    name = (prefix or script_prefix()) + sanitize_name(edid)
    if len(name) <= PAPYRUS_MAX_SCRIPT_NAME:
        return name
    digest = hashlib.md5(name.encode('utf-8')).hexdigest()[:4].upper()
    # keep the head (it carries the recognisable quest/area prefix) + _<hash>
    keep = PAPYRUS_MAX_SCRIPT_NAME - len(digest) - 1
    return f'{name[:keep]}_{digest}'


def safe_property_name(name: str) -> str:
    """Return a Papyrus-safe property name, renaming reserved words."""
    # Oblivion's parser accepts quotes around any EditorID and Nehrim's authors
    # use them constantly (173 sites: `SetStage "MQ01Tate" 20`,
    # `GetStage "NQ00Karick"`, `StartQuest "NQ05"`, `AddScriptPackage "..."`).
    # Left in, the `[^\w]` pass below turns each quote into an underscore, so
    # `"MQ01Tate"` became the property `_MQ01Tate_` while the SAME script's
    # unquoted `GetStage MQ01Tate` became `MQ01Tate`.  Only the unquoted
    # spelling matches an EditorID, so only it was bound in the VMAD —
    # `_MQ01Tate_` stayed None and every `_MQ01Tate_.SetStage(...)` threw.
    # MQ01Tate was stranded at stage 15, never reaching the stage 40 that is
    # the only thing that starts MQ01, so MQ00 could never complete either.
    name = name.strip()
    if len(name) > 1 and name[0] == '"' and name[-1] == '"':
        name = name[1:-1]
    safe = re.sub(r'[^\w]', '_', name)
    # A Papyrus identifier may not start with a digit. DELETING the leading
    # digits is lossy and collides: Morroblivion names ~19,000 records with a
    # leading digit, and stripping collapses 337 of them onto a shared name,
    # 155 onto a DIFFERENT record this plugin already owns, and 32 onto a
    # VANILLA SKYRIM record (`0miner` -> the Skyrim CLAS `Miner`,
    # `0banditfaction` -> the Skyrim FACT `BanditFaction`). A property bound by
    # name then resolves to the wrong record entirely, and nothing downstream
    # can tell. Prefix instead: `d` + the digits keeps the name UNIQUE and
    # REVERSIBLE (`0Blades` -> `d0Blades`, `1Necromancy` -> `d1Necromancy`), so
    # two records that differ only in their leading digits stay distinct and
    # neither can shadow an existing EditorID.
    m = re.match(r'^(\d+)(.*)$', safe)
    if m:
        safe = 'd' + m.group(1) + m.group(2)
    if not safe:
        safe = 'var_' + name.replace(' ', '_')
    # PapyrusCompiler mangles a variable `x` to the register `::x_var`, and it
    # reserves the `::temp*` namespace for its OWN scratch registers.  A user
    # variable starting with a lowercase `temp` therefore collides with the
    # compiler's free list ("Attempting to add temporary variable named
    # ::temp_var to free list multiple times") and the script does not compile.
    # Verified against PapyrusCompiler.exe: `temp`, `tempstage`, `template` and
    # `temperature` all fail; `Temp`, `tmp` and `atemp` are fine — the check is
    # case-sensitive and anchored at the start, so capitalising is enough.
    if safe.startswith('temp'):
        safe = 'T' + safe[1:]
    low = safe.lower()
    if low in papyrus_reserved():
        # Keep the original casing — `.capitalize()` lowercases the tail and
        # turns DarkBrotherhood into the unreadable myDarkbrotherhood.
        return 'my' + safe[0].upper() + safe[1:]
    return safe


def mgef_family_keyword_name(effect_edid: str) -> str:
    """EditorID and script property name of the KYWD every copy of one MGEF carries.

    See: docs/commentary/tes5_import_magic.md#effect-families
    """
    return 'TES4FX_' + effect_edid.strip().strip('"').lower()


def _canonical_global(name: str) -> str:
    """Return the canonical property name for a known global."""
    return _GLOBAL_CANONICAL.get(name.lower(), name)


def record_type_to_papyrus(rtype: str) -> str:
    """Map a TES4 record type to a Papyrus property type."""
    return _RECORD_TYPE_PAPYRUS.get(rtype, 'ObjectReference')


def is_base_object_type(ptype: str) -> bool:
    """True when `ptype` is a base-object class, not a placed reference."""
    return ptype in _BASE_OBJECT_PAPYRUS


# Record types whose Papyrus class is a BASE OBJECT (Armor, Weapon, Potion,
# ...), not a placed reference.  A VMAD property naming one of these binds to
# the base record itself, and the VM type-checks that binding: an
# `extends ObjectReference` script class is NOT a valid type for it.
#
# TES4 attaches scripts to base items freely (mwCWUItemScript rides every
# Morroblivion clothing record), and the converter preferred that script class
# over the record class so cross-script property reads would work. On a base
# item that preference is wrong and silently fatal -- measured in the game's
# own Papyrus log:
#
#   Property fbmwEngravedRingofHealing on script TES4_TIF__013236A5 ...
#     cannot be bound because (1B001677) is not the right type
#   error: Cannot add None to a container
#     [ (00000014)].Actor.RemoveItem() - "<native>"
#
# The property read None, so `player.removeitem fbmwEngravedRingofHealing 1`
# and Fargoth's matching `additem` both no-oped -- the quest still advanced to
# stage 100 (native errors are non-fatal), so the ring stayed in the player's
# inventory after handing it over.
_BASE_OBJECT_PAPYRUS = frozenset({
    'Armor', 'Weapon', 'Book', 'Potion', 'Ingredient', 'MiscObject', 'Key',
    'Ammo', 'SoulGem', 'Light', 'Activator', 'Flora', 'Furniture',
    'LeveledItem', 'LeveledActor', 'LeveledSpell', 'Scroll',
})


def script_type_may_override(record_ptype: str) -> bool:
    """Whether an attached TES4 script class may stand in for `record_ptype`.

    Reference-semantics types (ObjectReference, Actor, ...) may: the script
    extends one of those, so it binds and additionally exposes the script's own
    variables. Base-object types may NOT -- see _BASE_OBJECT_PAPYRUS.
    """
    return record_ptype not in _BASE_OBJECT_PAPYRUS


def wants_placed_reference(ptype: str) -> bool:
    """Whether a VMAD property of this Papyrus type must bind a PLACED
    reference rather than an actor BASE record.

    Oblivion resolves a unique actor's BASE EditorID to its placed instance
    (`ArenaMouth.Say ...` works even though ArenaMouth is the NPC_ record), so
    TES4 scripts name bases and mean references constantly. Skyrim's VM
    type-checks the binding: an NPC_/CREA base does NOT satisfy an
    Actor/ObjectReference(-derived) property, the bind is refused, and the
    property reads None for the whole session — measured live in the Papyrus
    log across 69 scripts (every Daedric statue voice, the Arena's ArenaMouth
    chain, the house-furnisher merchants). A TES4_* script class extends
    Actor/ObjectReference when it resolves to an actor base, so it needs the
    same treatment; script classes with other extends (Quest,
    ActiveMagicEffect) never resolve to an NPC_/CREA and fall out at the
    caller's record-type gate.
    """
    return (ptype in ('Actor', 'ObjectReference')
            or is_generated_script_type(ptype))


def _record_type_to_base_papyrus(rtype: str) -> str:
    """Map a TES4 record type to the Papyrus type of its BASE form.

    `record_type_to_papyrus` answers "what do I call a *reference* to this",
    which is what most TES4 script arguments mean.  Base-object comparisons
    (`GetIsID`) mean the opposite: the operand is the base record itself, so an
    NPC_ is an ActorBase (not an Actor) and a placed reference resolves to the
    base it points at.  Everything else already maps to its base type.
    """
    if rtype in ('NPC_', 'CREA', 'ACHR', 'ACRE'):
        return 'ActorBase'
    if rtype == 'REFR':
        # A REFR's base could be anything; Form compares against them all.
        return 'Form'
    mapped = _RECORD_TYPE_PAPYRUS.get(rtype, '')
    # ObjectReference is this table's fallback for base records with no
    # dedicated Papyrus class (DOOR/CONT/STAT/FLOR/...).  As a *base* operand
    # those are plain Forms, and Form compares against any base type.
    if not mapped or mapped == 'ObjectReference':
        return 'Form'
    return mapped


#: Oblivion animation GROUP -> Skyrim behavior-graph event.
ANIM_GROUP_EVENTS = {
    'forward': 'moveStart', 'backward': 'moveStartBackward',
    'left': 'moveStartStrafeLeft', 'right': 'moveStartStrafeRight',
    'idle': 'IdleForceDefaultState', 'specialidle': 'SpecialIdle',
    'unequip': 'Unequip', 'equip': 'Equip',
    'torchidle': 'IdleForceDefaultState',
    'castself': 'MagicCastSelf', 'casttouch': 'attackStart',
    'casttarget': 'attackStart',
    'jumpstart': 'JumpStandingStart', 'jumpland': 'JumpLand',
    'handstohandsattack': 'attackStart',
}


#: Papyrus types holding a REFERENCE rather than a value.
_REF_TYPES = frozenset({
    'ObjectReference', 'Actor', 'ActorBase', 'Form', 'Cell', 'Quest',
    'Faction', 'Race', 'Package', 'Spell', 'Sound', 'Topic', 'Weapon',
    'Armor', 'Book', 'Potion', 'Ingredient', 'Key', 'MiscObject', 'Light',
    'Container', 'Door', 'Activator', 'Static', 'Furniture', 'Flora',
    'EffectShader', 'WorldSpace', 'Location', 'Keyword', 'FormList',
})


# ===========================================================================
# Shared vocabulary
# ===========================================================================

#: TES4 spellings of "the object this script runs on".
SELF_NAMES = ('self', 'myself', 'getself')

#: Export signatures of a PLACED reference (as opposed to its base record).
PLACED_REF_SIGS = ('ACHR', 'ACRE', 'REFR')


# ===========================================================================
# Magic school and service-menu vocabulary
# ===========================================================================

#: TES4 magic school index -> EFSH EditorID for the enchant glow.
SCHOOL_ENCHANT_SHADER = {
    0: 'effectenchantalteration', 1: 'effectenchantconjuration',
    2: 'effectenchantdestruction', 3: 'effectenchantillusion',
    4: 'effectenchantmysticism',  5: 'effectenchantrestoration',
}

#: Fragment line opening each Skyrim service menu, by TES4 service topic kind.
SERVICE_MENU_CALL = {
    'barter': '  (akSpeakerRef as Actor).ShowBarterMenu()',
    'training': '  Game.ShowTrainingMenu(akSpeakerRef as Actor)',
}


# ===========================================================================
# Compatibility re-exports
# ===========================================================================

#: Moved to resolve.py; re-exported so the docs/ §5 boundary is unchanged.
from script_convert.resolve import (
    resolve_property_formid, digit_stripped_formid,
)
__all__ = ['resolve_property_formid', 'digit_stripped_formid']


#: TES4 block types whose body becomes the OnUpdate poll.
POLL_BLOCKS = ('gamemode', 'scripteffectupdate')

#: `begin MenuMode <id>` -> menu whose close runs it; docs/commentary/script_convert.md#menumode-with-a-menu-id
MENU_ID_NAMES = {
    '1036': 'RaceSex Menu',
}


#: Reference types, WIDEST first: the later one is the more specific.
REF_SPECIFICITY = ('Form', 'ObjectReference', 'Actor')

#: Numeric types, widest first.  Mixed arithmetic takes the widest operand.
NUMERIC_RANK = ('Float', 'Int', 'Bool')

#: Declared parameter type -> the source types it may be cast FROM, only these.
CASTABLE = {
    'Int': ('Float',),
    'Spell': ('Form', 'ObjectReference'),
    'Faction': ('Form', 'ObjectReference'),
    'ObjectReference': ('Form',),
}

#: The types an OBSE user function declares when nothing narrows its `ref`.
UDF_WIDE_TYPES = {'form', 'objectreference'}

#: TES4 spelled inequality `<>`; Papyrus spells it `!=`.
OP_MAP = {'<>': '!='}

#: Operators binding LOOSER than Papyrus `as`, so a cast over one needs parens.
LOOSE_OPS = (' + ', ' - ', ' * ', ' / ', ' % ', ' && ', ' || ',
             ' == ', ' != ', ' < ', ' > ', ' <= ', ' >= ')

#: TES4 fame/infamy read -> (global property, the Papyrus read).
FAME_GLOBALS = {
    'getpcfame': ('TES4Fame', 'TES4Fame.GetValueInt()'),
    'getpcinfamy': ('TES4Infamy', 'TES4Infamy.GetValueInt()'),
    'getinfame': ('TES4Infamy', 'TES4Infamy.GetValueInt()'),
}


def typed_already(property_refs: dict, prop: str) -> bool:
    """Does this property already carry a type, under any casing of its name?
    See: docs/commentary/script_convert.md#quest-property-never-downgrades
    """
    low = prop.lower()
    return any(name.lower() == low and ptype
               for name, ptype in property_refs.items())
