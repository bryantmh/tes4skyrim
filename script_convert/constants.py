"""Constant lookup tables for TES4→Papyrus script conversion."""

import functools
import hashlib
import os
import re

# ===========================================================================
# Constants
# ===========================================================================

# Papyrus base class for a TES4 script attached to the PLAYER BASE record
# (NPC_ 0x00000007).  Oblivion let a plugin script the player that way -- Nehrim
# puts its whole XP/level/gold system AND `SetStage MQ00 1` (the intro's only
# starter) there.  Skyrim cannot: the acting player is PlayerRef 0x14, whose
# signature is PLYR (not ACHR, so a plugin cannot author an override of it), and
# its base is Skyrim's own Player 0x07 -- never the converted plugin's shifted
# copy, which no actor ever instantiates.  Vanilla's mechanism for "code that
# runs on the player forever" is a start-game-enabled quest holding a reference
# alias forced to 0x14 (71 vanilla QUSTs do exactly this); the script rides that
# alias.  `Self` there is the ReferenceAlias, so every implicit-self call is
# routed through GetReference()/GetActorReference() -- see
# ScriptConverter._implicit_self and tes5_import.object_scripts.
PLAYER_ALIAS_EXTENDS = 'ReferenceAlias'

# Oblivion block type -> Papyrus event mapping
# (event_signature, end_keyword)
BLOCK_MAP = {
    'gamemode':           ('Event OnUpdate()', 'EndEvent'),
    'menumode':           ('Event OnUpdate()', 'EndEvent'),
    'onactivate':         ('Event OnActivate(ObjectReference akActionRef)', 'EndEvent'),
    'onadd':              ('Event OnContainerChanged(ObjectReference akNewContainer, ObjectReference akOldContainer)', 'EndEvent'),
    'ondrop':             ('Event OnContainerChanged(ObjectReference akNewContainer, ObjectReference akOldContainer)', 'EndEvent'),
    'onequip':            ('Event OnEquipped(Actor akActor)', 'EndEvent'),
    'onunequip':          ('Event OnUnequipped(Actor akActor)', 'EndEvent'),
    'ondeath':            ('Event OnDeath(Actor akKiller)', 'EndEvent'),
    'onmurder':           ('Event OnMurder(Actor akKiller)', 'EndEvent'),
    'onknockout':         ('Event OnEnterBleedout()', 'EndEvent'),
    'onhit':              ('Event OnHit(ObjectReference akAggressor, Form akSource, Projectile akProjectile, bool abPowerAttack, bool abSneakAttack, bool abBashAttack, bool abHitBlocked)', 'EndEvent'),
    'onhitwith':          ('Event OnHit(ObjectReference akAggressor, Form akSource, Projectile akProjectile, bool abPowerAttack, bool abSneakAttack, bool abBashAttack, bool abHitBlocked)', 'EndEvent'),
    'onload':             ('Event OnLoad()', 'EndEvent'),
    'onreset':            ('Event OnReset()', 'EndEvent'),
    'onsell':             ('Event OnSell(Actor akSeller)', 'EndEvent'),
    # TES4 `Begin OnTrigger` runs EVERY FRAME an object is inside the volume,
    # not once on entry — Nehrim's Magieverbot (magic-ban) scripts count 25 and
    # 100 *executions* in it, which is only meaningful under repeat semantics.
    # Skyrim keeps the same three-way split (all three are distinct engine
    # events in SkyrimSE.exe): OnTrigger = "trigger is tripped", sent
    # repeatedly while inside; OnTriggerEnter/Leave are the edges.  Mapping
    # OnTrigger -> OnTriggerEnter froze every such state machine on its first
    # state, which left the Erothin bell latch stuck and re-ringing.
    'ontrigger':          ('Event OnTrigger(ObjectReference akActionRef)', 'EndEvent'),
    'ontriggerenter':     ('Event OnTriggerEnter(ObjectReference akActionRef)', 'EndEvent'),
    'ontriggerleave':     ('Event OnTriggerLeave(ObjectReference akActionRef)', 'EndEvent'),
    'onmagiceffectapply': ('Event OnMagicEffectApply(ObjectReference akCaster, MagicEffect akEffect)', 'EndEvent'),
    'oninit':             ('Event OnInit()', 'EndEvent'),
    'onpackagestart':     ('Event OnPackageStart(Package akNewPackage)', 'EndEvent'),
    'onpackagedone':      ('Event OnPackageEnd(Package akOldPackage)', 'EndEvent'),
    'onpackageend':       ('Event OnPackageEnd(Package akOldPackage)', 'EndEvent'),
    'onpackagechange':    ('Event OnPackageChange(Package akOldPackage)', 'EndEvent'),
    # OnTriggerActor/OnTriggerMob differ from OnTrigger only in WHAT trips them
    # (any actor / any creature), not in edge-vs-repeat — they are per-frame
    # too, so they take the repeating event as well.  Skyrim has no
    # actor-vs-creature split, so the filter is left to the block body.
    'ontriggeractor':     ('Event OnTrigger(ObjectReference akActionRef)', 'EndEvent'),
    'ontriggermob':       ('Event OnTrigger(ObjectReference akActionRef)', 'EndEvent'),
    'onmagiceffecthit':   ('Event OnMagicEffectApply(ObjectReference akCaster, MagicEffect akEffect)', 'EndEvent'),
    'onactorequip':       ('Event OnEquipped(Actor akActor)', 'EndEvent'),
    # OnAlarm (actor noticed a crime/attack) has no Papyrus event; entering
    # combat/search via OnCombatStateChanged is the closest trigger.  The block
    # loop adds an aeCombatState guard per block type (alarm: != 0, start
    # combat: == 1) so the two merge cleanly into one event.
    'onalarm':            ('Event OnCombatStateChanged(Actor akTarget, int aeCombatState)', 'EndEvent'),
    'onstartcombat':      ('Event OnCombatStateChanged(Actor akTarget, int aeCombatState)', 'EndEvent'),
    # Signatures are fixed by ActiveMagicEffect.psc — an invented one fails to
    # compile ("the parameter types of function oneffectstart ... do not match
    # the parent script activemagiceffect").
    'scripteffectstart':  ('Event OnEffectStart(Actor akTarget, Actor akCaster)', 'EndEvent'),
    'scripteffectfinish': ('Event OnEffectFinish(Actor akTarget, Actor akCaster)', 'EndEvent'),
    'scripteffectupdate': ('Event OnUpdate()', 'EndEvent'),
}

# Oblivion block filters (`begin OnEquip player`, `begin OnTrigger player`,
# `begin OnPackageDone SomePackage`) restrict the block to fire only for that
# object.  Papyrus has no such filter, so the block body must be wrapped in an
# equivalent guard on the event parameter that carries the filtered object.
#
# Maps block type -> (event parameter name, Papyrus type of that parameter).
# A block type absent from this table has no parameter to filter on, so its
# filter cannot be expressed and is dropped (with a TODO).
BLOCK_FILTER_PARAM = {
    'onactivate':         ('akActionRef', 'ObjectReference'),
    'onadd':              ('akNewContainer', 'ObjectReference'),
    'ondrop':             ('akOldContainer', 'ObjectReference'),
    'onequip':            ('akActor', 'Actor'),
    'onactorequip':       ('akActor', 'Actor'),
    'onunequip':          ('akActor', 'Actor'),
    'onsell':             ('akSeller', 'Actor'),
    'ontrigger':          ('akActionRef', 'ObjectReference'),
    'ontriggerenter':     ('akActionRef', 'ObjectReference'),
    'ontriggerleave':     ('akActionRef', 'ObjectReference'),
    'ontriggeractor':     ('akActionRef', 'ObjectReference'),
    'ontriggermob':       ('akActionRef', 'ObjectReference'),
    'onhit':              ('akAggressor', 'ObjectReference'),
    'onhitwith':          ('akSource', 'Form'),
    'ondeath':            ('akKiller', 'Actor'),
    'onmurder':           ('akKiller', 'Actor'),
    'onstartcombat':      ('akTarget', 'Actor'),
    'onmagiceffecthit':   ('akEffect', 'MagicEffect'),
    'onmagiceffectapply': ('akEffect', 'MagicEffect'),
    'onpackagestart':     ('akNewPackage', 'Package'),
    'onpackagedone':      ('akOldPackage', 'Package'),
    'onpackageend':       ('akOldPackage', 'Package'),
    'onpackagechange':    ('akOldPackage', 'Package'),
}

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

def _load_papyrus_script_names() -> set:
    """Every script name Skyrim ships (types AND gameplay scripts).

    The compiler rejects a variable or property named the same as ANY script it
    can see ("cannot name a variable or property the same as a known type or
    script"), and then every use of that name also fails ("Door is not a
    variable") — one bad name takes its whole dependency chain down with it.
    Oblivion EditorIDs collide freely: `Door`, `DarkBrotherhood`, `MS14`, ...

    Read from a checked-in list (generated by tools/generators/gen_papyrus_reserved.py from
    Data/Scripts.zip) rather than the live Data/Source/Scripts, so the conversion
    is reproducible and does not shift with the user's installed mods.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'papyrus_reserved.txt')
    try:
        with open(path, encoding='utf-8') as f:
            return {ln.strip().lower() for ln in f
                    if ln.strip() and not ln.startswith('#')}
    except OSError:
        return set()


#: Papyrus keywords; a property may not be named for one.
_PAPYRUS_KEYWORDS = frozenset({
    'self', 'parent', 'as', 'is', 'new', 'return', 'if', 'else', 'elseif',
    'endif', 'while', 'endwhile', 'function', 'endfunction', 'event',
    'endevent', 'property', 'endproperty', 'state', 'endstate', 'auto',
    'autoreadonly', 'import', 'extends', 'native', 'global', 'hidden',
    'conditional', 'int', 'float', 'bool', 'string', 'none', 'true', 'false',
    'length', 'scriptname', 'next',
})


@functools.cache
def papyrus_reserved() -> frozenset:
    """Keywords plus every shipped script name; read on first use.

    Reading the 309 KB name list at IMPORT time made every pool worker pay
    ~79 ms for a table most of them never touch.
    """
    return _PAPYRUS_KEYWORDS | _load_papyrus_script_names()

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
    'isplayable', 'isplayable2',
    'getdead', 'getdisabled', 'getlocked', 'getghost', 'getisalerted',
    'getincombat', 'getnobleedoutrecovery', 'getisplayablerace',
    'getcurrentweatherpercent', 'getiscurrentpackage',
    'getincell', 'getiscreature', 'iscreature', 'isleftup', 'samefaction',
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

    #: Whole-number reads, plus TES4 names converting to a fixed expression.
    + [(n, 'Int') for n in (
        'getvalueint', 'getcrimegold', 'getitemcount', 'getgoldamount',
        'getlocklevel', 'getdayofweek', 'getdayoftheweek', 'getrandompercent',
        'getrandpercent', 'getpcfame', 'getpcinfamy', 'getinfame')]

    #: Reference-valued names; the `ak*` event parameters need a downcast.
    + [(n, 'ObjectReference') for n in (
        'getlinkedref', 'placeatme', 'getparentref', 'placeactoratme',
        'geteditorlocation', 'getiteminslot', 'akactionref', 'aknewcontainer',
        'akoldcontainer', 'akcastref', 'akaggressor', 'akcaster',
        'getself', 'getactionref')]
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







# Methods declared on ObjectReference that a TES4 script calls BARE, relying on
# the implicit "me".  ActiveMagicEffect and TopicInfo are not references, so an
# unqualified `Disable()` / `GetLinkedRef()` in those scripts is an undefined
# function at compile time — they must be routed onto the reference the effect
# or topic acts upon.  Distinct from _ACTOR_ONLY_FUNCTIONS: these need the
# receiver redirected but NOT an `as Actor` cast, since they are valid on any
# reference.
# OBSE tests that ask a form what TYPE it is.  Vanilla Papyrus cannot answer —
# Form.GetType is SKSE — so all of them neutralise to 0 ("not that type"),
# which is the branch-not-taken side for every TES4 caller.
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

def _sanitize_name(name: str) -> str:
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


def papyrus_script_name(edid: str, prefix: str = 'TES4_') -> str:
    """Return the Papyrus ScriptName for a TES4 script EditorID.

    MUST be the single source of truth: the same name is written as the .psc
    ScriptName, the .psc filename, and the ScriptName inside the VMAD that binds
    the script to its record.  If those three ever disagree the binding breaks,
    so every producer calls this rather than formatting the name itself.

    Over-long names are truncated and given a short hash of the FULL original,
    which keeps them unique (several Oblivion scripts differ only in a suffix
    past the cut, e.g. TrigZoneCloseCurrentOblivionRdCitadel0{1..5}SCRIPT).
    """
    name = prefix + _sanitize_name(edid)
    if len(name) <= PAPYRUS_MAX_SCRIPT_NAME:
        return name
    digest = hashlib.md5(name.encode('utf-8')).hexdigest()[:4].upper()
    # keep the head (it carries the recognisable quest/area prefix) + _<hash>
    keep = PAPYRUS_MAX_SCRIPT_NAME - len(digest) - 1
    return f'{name[:keep]}_{digest}'


def _safe_property_name(name: str) -> str:
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




def _canonical_global(name: str) -> str:
    """Return the canonical property name for a known global."""
    return _GLOBAL_CANONICAL.get(name.lower(), name)


def _record_type_to_papyrus(rtype: str) -> str:
    """Map a TES4 record type to a Papyrus property type."""
    return _RECORD_TYPE_PAPYRUS.get(rtype, 'ObjectReference')


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
            or ptype.startswith('TES4_'))


def _record_type_to_base_papyrus(rtype: str) -> str:
    """Map a TES4 record type to the Papyrus type of its BASE form.

    `_record_type_to_papyrus` answers "what do I call a *reference* to this",
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


#: Papyrus natives whose parameter N needs a cast; keyed by PAPYRUS name.
PARAM_TYPES = {
    'additem': {1: 'Int'},
    'removeitem': {1: 'Int'},
    'additemhealthpercent': {1: 'Int'},
    'addspell': {0: 'Spell'},
    'removespell': {0: 'Spell'},
    'isinfaction': {0: 'Faction'},
    'addtofaction': {0: 'Faction'},
    'removefromfaction': {0: 'Faction'},
    'getfactionrank': {0: 'Faction'},
    'setfactionrank': {0: 'Faction'},
    'modfactionrank': {0: 'Faction'},
    'tes4polyfill.update3d': {0: 'ObjectReference'},
}


def param_types(tes4_name: str) -> dict:
    """`{index: Papyrus type}` for a command, under any of its spellings."""
    direct = PARAM_TYPES.get(tes4_name)
    if direct is not None:
        return direct
    row = COMMAND_ROWS.get(tes4_name)
    emit = (row.emit or '') if row is not None else ''
    return PARAM_TYPES.get(emit.split('(')[0].lower(), {})

#: Every OBSE raw-input command converts to the same inert marker.
_OBSE_INPUT_NOTE = '{f} {a}  ;OBSE input command, no Papyrus equivalent'

ACTOR, AV, SELF, OBJREF, RAW, MAP = ('ACTOR', 'AV', 'SELF', 'OBJREF',
                                    'RAW', 'MAP')


class Cmd:
    """One command's conversion, as data.

    `emit_row` in `emit/commands.py` renders it; that module's docstring is the
    spec for `emit`'s placeholders and for `subj`.
    """

    __slots__ = ('emit', 'subj', 'types', 'defaults', 'note', 'self_type',
                 'bare', 'arms', 'flags')

    def __init__(self, emit='0', subj=SELF, types=(), defaults=(), note='',
                 self_type=None, bare=False, arms=(), flags=''):
        self.emit = emit
        self.subj = subj
        self.types = dict(types)
        self.defaults = dict(defaults)
        self.note = note
        self.self_type = self_type
        self.bare = bare
        #: The two alternatives a `{?n<want>}` placeholder picks between.
        self.arms = arms
        #: Per-command properties, space separated; `HAS_FLAG` is the vocabulary.
        self.flags = frozenset(flags.split()) if flags else frozenset()


# Commands whose whole conversion is "resolve a receiver, convert a couple of
# arguments, register a property type, emit one expression".  Each row here
# replaced a name-guarded branch in `_emit_function`; the rationale that used
# to sit above the branch sits above its row.
COMMAND_ROWS = {
    # IsAnimPlaying: the behavior graph exposes this as an animation variable.
    # Cast to Int because TES4 call sites compare/assign 0/1.
    'isanimplaying': Cmd(
        '({ref}.GetAnimationVariableBool("bAnimPlaying") as Int)', OBJREF),

    #: GetArmorRating -> DamageResist actor value (what armor rating feeds).
    'getarmorrating': Cmd('{ref}.GetActorValue("DamageResist")', ACTOR, flags='actor_only'),

    # GetIsCreature: Skyrim marks people via the ActorTypeNPC race keyword;
    # converted creatures use generated races without it.
    'getiscreature': Cmd('TES4Polyfill.GetIsCreature({ref})', ACTOR,
                         flags='actor_arg bare_bool cmp_bool zero_arg'),
    'iscreature': Cmd('TES4Polyfill.GetIsCreature({ref})', ACTOR,
                      flags='actor_arg bare_bool cmp_bool zero_arg'),

    # Quadruped get-up direction. Skyrim's pose matcher publishes the chosen
    # left/right generator through iGetUpType; the polyfill reads that graph
    # variable rather than inventing an input/control interpretation.
    'isleftup': Cmd('TES4Polyfill.IsLeftUp({ref})', ACTOR,
                    flags='actor_only bare_bool cmp_bool zero_arg'),

    #: IsGuard: membership in Skyrim's guard dialogue faction.
    'isguard': Cmd('TES4Polyfill.IsGuard({ref})', ACTOR, flags='actor_arg zero_arg'),

    # SetActorRefraction: no Papyrus refraction control; a translucent alpha
    # fade is the closest visual (0 restores full opacity).
    'setactorrefraction': Cmd(
        'TES4Polyfill.SetActorRefraction({ref}, {a0})', ACTOR,
        defaults={0: '0'}, flags='actor_arg'),

    # SetAlert -> Actor.SetAlert (native, same name and semantics both ways).
    # NOT DrawWeapon: Oblivion's SetAlert sets the AI combat-READINESS flag,
    # which the engine clears on its own and which does NOT block dialogue.
    # DrawWeapon puts the actor in a weapon-drawn state that suppresses the
    # force-greet, and `SetAlert 0` (the sheathe half) was a NO-OP, so an
    # actor alerted for a scripted ambush never stood down: CharacterGen
    # stage 15 alerts Uriel for the prison-cell ambush and stage 17/24 clears
    # it to run the conversation, so converted Uriel drew his sword, never
    # sheathed it, and could never initiate dialogue with the player -- the
    # intro soft-locked with controls disabled.
    'setalert': Cmd('{ref}.SetAlert({b0})', ACTOR, defaults={0: '0'}),

    #: Reset3DState -> MoveTo self (reloads 3D).
    'reset3dstate': Cmd('{ref}.MoveTo({ref})'),

    #: SetRestrained -> SetDontMove.
    'setrestrained': Cmd('{ref}.SetDontMove({b0})', ACTOR, defaults={0: '0'}),

    #: IsOnGround: Skyrim has only the inverse.
    'isonground': Cmd('!({ref}.IsFlying())', RAW),

    #: IsInAir: cast to Int because TES4 call sites compare/assign 0/1.
    'isinair': Cmd('({ref}.IsFlying() as Int)', ACTOR),

    # GetAttacked -> IsAlarmed, the nearest Skyrim state: an actor that has
    # noticed a hostile action against it.
    'getattacked': Cmd('({ref}.IsAlarmed() as Int)', ACTOR, flags='zero_arg'),

    #: IsActorUsingATorch: equipped-item type 11 is the torch slot.
    'isactorusingatorch': Cmd('({ref}.GetEquippedItemType(0) == 11)', ACTOR, flags='cmp_bool'),

    # TES4 IsActor is a runtime type test on any reference.  Papyrus exposes
    # the same fact through a safe cast: a non-actor casts to None.  Keep this
    # as a Bool-valued zero-argument reference command so the normal expression
    # pass also folds the TES4 `== 0/1` spelling instead of comparing Bool/Int.
    'isactor': Cmd('(({ref} as Actor) != None)', OBJREF,
                   flags='bare_bool cmp_bool zero_arg'),

    # Vanilla Oblivion actor predicates and AI controls.  IsAIEnabled is the
    # SKSE64 getter for the same engine flag that vanilla EnableAI writes.
    'isactorsaioff': Cmd('(!{ref}.IsAIEnabled())', ACTOR,
                         flags='actor_only bare_bool cmp_bool zero_arg'),
    'setactorsai': Cmd('{ref}.EnableAI({b0})', ACTOR,
                       defaults={0: '1'}, flags='actor_only'),
    'toggleactorsai': Cmd('{ref}.EnableAI(!{ref}.IsAIEnabled())', ACTOR,
                          flags='actor_only zero_arg'),
    'isactorevil': Cmd('TES4Polyfill.IsActorEvil({ref})', ACTOR,
                       flags='actor_only bare_bool cmp_bool zero_arg'),
    'samefactionaspc': Cmd('TES4Polyfill.SameFactionAsPC({ref})', ACTOR,
                           flags='actor_only bare_bool cmp_bool zero_arg'),
    'samefaction': Cmd('TES4Polyfill.SameFaction({ref}, {a0})', ACTOR,
                       types={0: 'Actor'},
                       flags='actor_only bare_bool cmp_bool'),
    'gettimedead': Cmd('TES4Polyfill.GetTimeDead({ref})', ACTOR,
                       flags='actor_only zero_arg'),
    'isrunning': Cmd('IsRunning', MAP,
                     flags='actor_only bare_bool cmp_bool zero_arg'),

    #: Unlock takes no argument in TES4; Skyrim's Lock(false) is the unlock.
    'unlock': Cmd('{ref}.Lock(false)', OBJREF),

    #: GetIsReference / GetIsRef: identity comparison against the argument.
    'getisreference': Cmd('{ref} == {a0}', AV, defaults={0: 'None'}),
    'getisref': Cmd('{ref} == {a0}', AV, defaults={0: 'None'}, flags='cmp_bool'),

    # CreateFullActorCopy: Papyrus can only place a fresh instance of the
    # actor's BASE, which is the copy TES4's callers use it for.
    'createfullactorcopy': Cmd(
        '{ref}.PlaceAtMe({ref}.GetActorBase())', ACTOR),

    # GetPCExpelled / SetPCExpelled: faction arg.  Skyrim has the exact
    # natives on both sides -- vanilla Faction.psc declares `bool Function
    # IsPlayerExpelled()` and `Function SetPlayerExpelled(bool abIsExpelled =
    # true)`.  The reader used to test `GetFactionRank(...) < 0` instead,
    # which was asymmetric with the setter: SetPlayerExpelled sets the
    # engine's expelled flag and never touches rank, so nothing ever drove
    # the rank negative and every GetPCExpelled read was permanently false.
    'getpcexpelled': Cmd('{a0}.IsPlayerExpelled()', types={0: 'Faction'},
                         defaults={0: 'None'}),
    'ispcexpelled': Cmd('{a0}.IsPlayerExpelled()', types={0: 'Faction'},
                        defaults={0: 'None'}),
    'isexpelled': Cmd('{a0}.IsPlayerExpelled()', types={0: 'Faction'},
                      defaults={0: 'None'}),

    # OBSE printf-style variants: a format string plus its arguments.
    # printToConsole is a debug trace.
    'printtoconsole': Cmd('Debug.Trace({fmt})'),
    'printc': Cmd('Debug.Trace({fmt})'),

    # MessageBoxEX is a player-facing box; its `|`-separated button list has
    # no Papyrus equivalent, so only the message text survives --
    # _format_string_call keeps the whole string, the closest faithful
    # rendering without a UI menu.
    'messageboxex': Cmd('Debug.MessageBox({fmt})'),
    'messageex': Cmd('Debug.MessageBox({fmt})'),

    # OBSE IsCasting: "is this actor playing a cast animation".  Skyrim exposes
    # exactly that natively through the animation graph, so no SKSE dependency.
    'iscasting': Cmd('({ref}.GetAnimationVariableBool("bIsCastingRight") || '
                     '{ref}.GetAnimationVariableBool("bIsCastingLeft"))', ACTOR, flags='bare_bool zero_arg'),

    # OBSE `SetCurrentHealth <value>` takes only the value -- the actor value is
    # implicit in the name, so it cannot map straight onto SetActorValue (which
    # would swallow the number as the AV NAME and set nothing).
    'setcurrenthealth': Cmd('{ref}.SetActorValue("Health", {a0})', RAW,
                            defaults={0: '0'}),

    #: SetPCExpelled: Skyrim's exact native.  See getpcexpelled above.
    'setpcexpelled': Cmd('{p0}.SetPlayerExpelled({b1})', types={0: 'Faction'},
                         defaults={0: 'None', 1: '1'}),

    # `sms`/StopMagicShaderVisuals: EffectShader.Stop takes an
    # **ObjectReference**, so the subject must not be promoted to Actor -- TES4
    # casts these shaders on markers and statues (SEXedPuzStatue1-5, the SE05
    # spell markers), and an `Actor Property` on a STAT/ACTI refuses to bind.
    'sms': Cmd('{p0}.Stop({ref})', OBJREF, types={0: 'EffectShader'},
               defaults={0: 'Self'}),
    'stopmagicshadervisuals': Cmd(
        '{p0}.Stop({ref})', OBJREF, types={0: 'EffectShader'},
        defaults={0: 'Self'}),

    #: `pms`/PlayMagicShaderVisuals: same ObjectReference contract as `sms`.
    'pms': Cmd('{p0}.Play({ref}, {a1})', OBJREF, types={0: 'EffectShader'},
               defaults={0: 'Self', 1: '-1.0'}),
    'playmagicshadervisuals': Cmd(
        '{p0}.Play({ref}, {a1})', OBJREF, types={0: 'EffectShader'},
        defaults={0: 'Self', 1: '-1.0'}),

    #: GetIsCurrentWeather / GetWeatherPercent: Weather.psc globals.
    'getweatherpercent': Cmd('Weather.GetCurrentWeatherTransition()'),
    'getcurrentweatherpercent': Cmd('Weather.GetCurrentWeatherTransition()'),
    'getiscurrentweather': Cmd('(Weather.GetCurrentWeather() == {a0})',
                               types={0: 'Weather'}, defaults={0: 'None'}),
    # `GetWeather <WTHR>` is the older spelling of the same current-weather
    # predicate; Midas and other OBSE-era mods use both spellings.
    'getweather': Cmd('(Weather.GetCurrentWeather() == {a0})',
                      types={0: 'Weather'}, defaults={0: 'None'}),

    # Weather.  WTHR/CLMT/REGN weather are fully converted, so scripted weather
    # moments drive the real converted records.  Signatures verified against
    # vanilla Weather.psc: ForceActive(bool abOverride=false) is the instant
    # switch, SetActive(bool abOverride, bool abAccelerate) the gradual one.
    #
    # abOverride must be FALSE on both.  Oblivion holds scripted weather by
    # CONTINUOUS RE-APPLICATION -- the gate scripts re-force the storm every
    # GameMode pass while the player is near -- not by an engine lock; its
    # scripts stop running when the ref unloads and the sky then rolls
    # naturally.  Skyrim's abOverride=True is a GLOBAL lock that survives the
    # caller unloading, so mapping to True let a fast-travel away from an
    # Oblivion gate strand OblivionStormTamriel over the whole world forever
    # (the release call lives in the same unloaded script's update loop and can
    # never run).
    'forceweather': Cmd('{p0}.ForceActive(False)', types={0: 'Weather'}),
    'fw': Cmd('{p0}.ForceActive(False)', types={0: 'Weather'}),
    'setweather': Cmd('{p0}.SetActive(False, False)', types={0: 'Weather'}),
    'sw': Cmd('{p0}.SetActive(False, False)', types={0: 'Weather'}),

    # `ref.Update3D` -- written as a RECEIVER method, so it must consume the
    # receiver rather than be emitted after a dot (`ActorRef.TES4Polyfill
    # .Update3D()` is not Papyrus).  The receiver becomes the polyfill's
    # argument; with no receiver the call is on the player, the only actor
    # whose camera or first-person model this command can concern.
 
    # --- Commands with no Papyrus equivalent ------------------------------
    # Neutralise rather than emit: an unknown name is a hard compile error that
    # takes down the whole file AND every script that imports it, whereas an
    # inert 0 keeps the rest of the script working.

    # TES4 `PositionCell x, y, z, angle, Cell` teleports a reference to raw
    # coordinates in a named cell.  Papyrus MoveTo takes a TARGET REFERENCE,
    # and Skyrim exposes no cell-coordinate move.
    'positioncell': Cmd(note='PositionCell needs a target marker; Papyrus '
                             'MoveTo takes a reference, not cell '
                             'coordinates ({a})'),

    # OBSE `runScriptLine "<console command>"` compiles and runs a console
    # command at runtime.  Papyrus cannot execute the console at all, and
    # Morrowind_ob uses it exclusively to poke the OPTIONAL ObXP mod's globals
    # -- a mod that is not part of the conversion, so there is no target to
    # write even in principle.  The payload is a quoted console line containing
    # OBSE's `%q` escaped-quote token and apostrophes, which as emitted broke
    # the Papyrus string literal it was pasted into.
    'runscriptline': Cmd(note='{f} - OBSE console execution, no Papyrus '
                              'equivalent ({f} {a})'),
    'runbatchscript': Cmd(note='{f} - OBSE console execution, no Papyrus '
                               'equivalent ({f} {a})'),

    # OBSE `SetEventHandler "OnDeath" <script> "object"::Player` registers a
    # script as a callback for an engine event.  Papyrus has no registration
    # API of this shape -- an event is bound by DECLARING it (`Event
    # OnDeath()`) on a script attached to the form.  The argument syntax
    # carries OBSE's `::` type-tag operator, which is not Papyrus syntax at all
    # and fails the parse of every script that imports this one.
    'seteventhandler': Cmd(
        note='{f} - OBSE event registration; Papyrus binds events by '
             'declaring them on the attached script ({f} {a})'),
    'removeeventhandler': Cmd(
        note='{f} - OBSE event registration; Papyrus binds events by '
             'declaring them on the attached script ({f} {a})'),

    'getcrosshairref': Cmd(
        'None', note='getCrosshairRef has no Papyrus equivalent (read as None)'),
    'getcrosshairreference': Cmd(
        'None', note='getCrosshairRef has no Papyrus equivalent (read as None)', flags='branch_only'),
    'getstringgamesetting': Cmd(
        '""', note='GetStringGameSetting has no Papyrus equivalent (read as "")'),
    'getpackagetarget': Cmd(
        'None', note='getPackageTarget has no Papyrus equivalent (read as None)'),

    # StopCombatAlarmOnActor / SCAOnActor / SCA.
    # NOT StopCombat: that "removes this actor from combat" (ends the
    # actor's OWN aggression), whereas SCAOnActor "stops all combat and
    # alarms AGAINST this actor" — the opposite direction.  Skyrim has the
    # exact native, Actor.StopCombatAlarm().  With StopCombat the whole
    # point of the call was lost: `player.SCAOnActor` is the idiom for
    # calming a mob that is attacking the player (Dark19Whispers uses it to
    # hold the player still through the Night Mother's speech), and
    # stopping only the player's own aggression left everyone still hostile.
    'scaonactor': Cmd('{ref}.StopCombatAlarm()', AV),
    'sca': Cmd('{ref}.StopCombatAlarm()', AV),
    'stopcombatalarmonactor': Cmd('{ref}.StopCombatAlarm()', AV, flags='zero_arg'),
    #: ClearOwnership
    'clearownership': Cmd('{ref}.SetActorOwner(Game.GetPlayer().GetActorBase())', SELF, flags='zero_arg'),
    #: Reset → ref.Reset()
    'reset': Cmd('{ref}.Reset()', AV, flags='branch_only objref_self'),
    #: DeleteFullActorCopy
    'deletefullactorcopy': Cmd('{ref}.Delete()', AV, flags='branch_only'),
    'opendoor': Cmd('{ref}.SetOpen(true)', SELF),
    'closedoor': Cmd('{ref}.SetOpen(false)', SELF),
    'getsize': Cmd('{ref}.GetScale()', SELF),
    #: ResetHealth: TES4 ResetHealth -> RestoreActorValue("Health", 9999)
    'resethealth': Cmd('{ref}.RestoreActorValue("Health", 9999)', AV, flags='actor_only branch_only'),
    # EvaluatePackage/EVP/AddScriptPackage/RemoveScriptPackage/StopWaiting:
    # Skyrim version takes no args (drop TES4 package arg)
    'evaluatepackage': Cmd('{ref}.EvaluatePackage()', AV, flags='actor_arg actor_only zero_arg'),
    'evp': Cmd('{ref}.EvaluatePackage()', AV, flags='actor_arg actor_only'),
    'addscriptpackage': Cmd('{ref}.EvaluatePackage()', AV, flags='actor_only drop_args'),
    'removescriptpackage': Cmd('{ref}.EvaluatePackage()', AV, flags='actor_only drop_args'),
    'stopwaiting': Cmd('{ref}.EvaluatePackage()', AV, flags='actor_only'),
    #: ClearLookAt / StopLook: Skyrim version takes no args (drop TES4 target arg)
    'clearlookat': Cmd('{ref}.ClearLookAt()', AV, flags='actor_only'),
    'stoplook': Cmd('{ref}.ClearLookAt()', AV, flags='actor_only zero_arg'),
    'stoplooking': Cmd('{ref}.ClearLookAt()', AV, flags='actor_only'),
    #: GetEquippedItemType: Skyrim requires hand param (0=left, 1=right)
    'getweaponanimtype': Cmd('{ref}.GetEquippedItemType(1)', AV, flags='actor_only zero_arg'),
    'getequippeditemtype': Cmd('{ref}.GetEquippedItemType(1)', AV, flags='branch_only'),
    #: IsRidingHorse: Actor.IsOnMount() in Skyrim
    'isridinghorse': Cmd('{ref}.IsOnMount()', AV, flags='cmp_bool'),
    #: GetRace: ref.GetRace() -> ref.GetRace()
    'getrace': Cmd('{ref}.GetRace()', AV, flags='actor_only'),
    #: IsInInterior: ref.IsInInterior -> ref.GetParentCell().IsInterior()
    'isininterior': Cmd('{ref}.GetParentCell().IsInterior()', AV, flags='bare_bool cmp_bool objref_self'),
    #: GetContainer: item.GetContainer -> item.GetContainer()
    'getcontainer': Cmd('{ref}.GetContainer()', SELF),
    # The crime/fame/infamy WRITES.  Each was a branch that registered the
    # same fixed property, converted argument 0 and cast it -- identical apart
    # from the property and the cast, so they are rows.  `{int}`/`{float}` is
    # argument 0 cast to what the Papyrus setter declares.
    'setcrimegold': Cmd('TES4CyrodiilCrimeFaction.SetCrimeGold({i0})', self_type=('TES4CyrodiilCrimeFaction', 'Faction'), flags='actor_only'),
    'modcrimegold': Cmd('TES4CyrodiilCrimeFaction.ModCrimeGold({c0}, false)', self_type=('TES4CyrodiilCrimeFaction', 'Faction'), flags='actor_only'),
    'modpcfame': Cmd('TES4Fame.Mod({f0})', self_type=('TES4Fame', 'GlobalVariable')),
    'modpcinfamy': Cmd('TES4Infamy.Mod({f0})', self_type=('TES4Infamy', 'GlobalVariable')),
    'setpcfame': Cmd('TES4Fame.SetValueInt({i0})', self_type=('TES4Fame', 'GlobalVariable')),
    'setpcinfamy': Cmd('TES4Infamy.SetValueInt({i0})', self_type=('TES4Infamy', 'GlobalVariable')),
    #: GotoJail → faction.SendPlayerToJail()
    'gotojail': Cmd('TES4CyrodiilCrimeFaction.SendPlayerToJail()', self_type=('TES4CyrodiilCrimeFaction', 'Faction')),
    #: Crime gold functions → TES4CyrodiilCrimeFaction proxy
    'getcrimegold': Cmd('TES4CyrodiilCrimeFaction.GetCrimeGold()', self_type=('TES4CyrodiilCrimeFaction', 'Faction'), flags='actor_only'),
    'payfine': Cmd('TES4CyrodiilCrimeFaction.PlayerPayCrimeGold(false, false)', self_type=('TES4CyrodiilCrimeFaction', 'Faction')),
    'payfinethief': Cmd('TES4CyrodiilCrimeFaction.PlayerPayCrimeGold(false, false)', self_type=('TES4CyrodiilCrimeFaction', 'Faction')),
    #: Fame/Infamy → GlobalVariable
    'getpcfame': Cmd('TES4Fame.GetValueInt()', self_type=('TES4Fame', 'GlobalVariable')),
    'getpcinfamy': Cmd('TES4Infamy.GetValueInt()', self_type=('TES4Infamy', 'GlobalVariable')),
    'getinfame': Cmd('TES4Infamy.GetValueInt()', self_type=('TES4Infamy', 'GlobalVariable')),
    # GetDayOfWeek → GameDaysPassed % 7
    # `GetValueInt`, not `GetValue() as Int`: the result is the operand of a
    # `% 7`, and the Float form made the whole expression Float, which then
    # attracted a second cast on assignment (`... % 7 as Int`).
    'getdayofweek': Cmd('(GameDaysPassed.GetValueInt() % 7)', self_type=('GameDaysPassed', 'GlobalVariable')),
    'getdayoftheweek': Cmd('(GameDaysPassed.GetValueInt() % 7)', self_type=('GameDaysPassed', 'GlobalVariable')),
    #: GetAmountSoldStolen: gold fenced, paired with ModAmountSoldStolen above.
    'getamountsoldstolen': Cmd('TES4GoldFenced.GetValue()', self_type=('TES4GoldFenced', 'GlobalVariable')),
    # Player-controls state.  Skyrim exposes the two WRITERS as natives
    # (Game.DisablePlayerControls/EnablePlayerControls) but no getter, so
    # the writers also shadow the state into a synthesized global and the
    # read returns that.  Flattening the read to 0 was actively wrong
    # rather than merely inert: MG18Script polls it three times to sequence
    # Mannimarco's confrontation, and a constant 0 made the force-greet
    # branch (`== 1`) permanently false while the combat branch (`== 0`)
    # fired immediately — so Mannimarco never spoke and attacked at once.
    'getplayercontrolsdisabled': Cmd('TES4ControlsDisabled.GetValue()', self_type=('TES4ControlsDisabled', 'GlobalVariable')),
    'getplayercontrolsdisabled_': Cmd('TES4ControlsDisabled.GetValue()', self_type=('TES4ControlsDisabled', 'GlobalVariable'), flags='branch_only'),

    # AdvancePCLevel: raise the player exactly one level.  Skyrim's vanilla
    # Game.psc (Scripts.zip) has NO level setter — Game.SetPlayerLevel is a
    # mod-supplied extension, absent from the shipped headers — so the
    # writable Level actor value is the equivalent the base game does offer.
    # Nehrim drives its whole custom level-up through this call
    # (GlobaltagebuchScript's journal menu), so leaving it unmapped left the
    # player permanently at level 1.
    'advancepclevel': Cmd('Game.GetPlayer().ModActorValue("Level", 1)'),
    # con_Save / Autosave / con_SaveGame: write a save.  Papyrus exposes
    # Game.RequestSave() (a normal save) and Game.RequestAutoSave().  The
    # TES4 argument is a save-slot NAME, which Papyrus does not accept, so it
    # is dropped — the engine picks the slot.
    # (`autosave` itself already maps to Game.RequestAutoSave via FUNCTION_MAP.)
    'con_save': Cmd('Game.RequestSave()'),
    'con_savegame': Cmd('Game.RequestSave()'),
    'getdisposition': Cmd('50'),
    #: GetIsPlayableRace
    'getisplayablerace': Cmd('true', flags='zero_arg'),
    'getplayerinjail': Cmd('Game.GetPlayer().IsArrested()'),
    #: GetRandomPercent -> Utility.RandomInt(0, 99)
    'getrandompercent': Cmd('Utility.RandomInt(0, 99)'),
    # HasVampireFed: Skyrim's PlayerVampireQuestScript.VampireStatus is 1
    # exactly while the vampire has recently fed.
    'hasvampirefed': Cmd('TES4Polyfill.HasVampireFed()'),
    # "Is the player serving a jail sentence" — NOT faction expulsion, which
    # is what all four spellings used to emit. Skyrim has the exact native:
    # vanilla Actor.psc declares `bool Function IsArrested() native`,
    # documented "Is this actor currently arrested?" (the condition-function
    # form is GetArrestedState, index 656).
    #
    # All 9 TES4 sites are jail mechanics — the prison cell doors, the
    # Leyawiin jailor, Amusei (whom you meet in a cell), the tutorial's
    # prison start, and TG00FindThievesGuildScript, whose stage 10 is the
    # ENTRY POINT of the Thieves Guild questline. Expulsion is never set on
    # TES4CyrodiilCrimeFaction for the player, so every one read false.
    'isplayerinjail': Cmd('Game.GetPlayer().IsArrested()'),
    'isplayerinprison': Cmd('Game.GetPlayer().IsArrested()'),
    'isthirdperson': Cmd('False'),
    'releaseweatheroverride': Cmd('Weather.ReleaseOverride()'),
    'savegame': Cmd('Game.RequestSave()'),
    'senttojail': Cmd('Game.GetPlayer().IsArrested()'),
    'triggerhitshader': Cmd('Game.TriggerScreenBlood(3)'),

    'addachievement': Cmd(note='{f}'),
    'addflames': Cmd(note='{f} has no Skyrim equivalent', flags='zero_arg'),
    'attachashpile': Cmd(note='{f}'),
    'bookread': Cmd(note='GetBookRead'),
    'disablelinkedpathpoints': Cmd(note='{f}', flags='zero_arg'),
    'enablelinkedpathpoints': Cmd(note='{f}', flags='zero_arg'),
    'essentialdeathreload': Cmd(note='{f}'),
    'flamesoff': Cmd(note='{f} has no Skyrim equivalent'),
    'flameson': Cmd(note='{f} has no Skyrim equivalent'),
    #: ForceFlee → StartCombat avoidance (approximate)
    'getaltcontrol': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    #: GetBookRead -> no direct equivalent, return 0
    'getbookread': Cmd(note='{f}'),
    # OBSE reads with no vanilla-Papyrus counterpart at all: raw input
    # bindings (getControl/getAltControl), UI introspection
    # (getMenuHasTrait), and inventory/form queries whose return shape has no
    # Skyrim analogue (getItems is an OBSE array, isPlayable2/
    # getFullGoldValue/getWeaponSkillType are OBSE-only form reads).
    # All are numeric/boolean in context, so 0 keeps the surrounding
    # expression well-typed.  Bare literal — these sit inside conditions and
    # arithmetic, where a trailing comment would eat the rest of the line.
    'getcontrol': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    'getcrimeknown': Cmd(note='{f}'),
    'getcurrentaipackage': Cmd(note='{f}', flags='zero_arg'),
    'getcurrentaiprocedure': Cmd(note='{f}', flags='zero_arg'),
    'getcurrentpackage': Cmd(note='{f}'),
    'getfullgoldvalue': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    # GetGameRestarted / IsPlayerMovingIntoNewSpace (OBSE): both report a
    # one-off engine transition Skyrim does not expose.  False is the safe
    # reading — the guarded body is a re-initialisation that is allowed to be
    # skipped, and the alternative (an undefined identifier) kills the script.
    # Return a BARE literal: this is an operand and gets embedded inside a
    # larger condition, where a trailing `;` comment would swallow the rest
    # of the expression (`If True  ;(False ;NE: ...)`).
    'getgamerestarted': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    # ObjectReference.IgnoreFriendlyHits is a SETTER in Skyrim; TES4's
    # GetIgnoreFriendlyHits reads the flag back and Papyrus cannot.
    'getignorefriendlyhits': Cmd(note='GetIgnoreFriendlyHits — Skyrim exposes only the setter', flags='bare_bool zero_arg'),
    'getisalerted': Cmd(note='{f}', flags='zero_arg'),
    'getisplayerbirthsign': Cmd(note='{f}'),
    'getitems': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    'getmousecontrol': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    'getnumericinisetting': Cmd(note='GetNumericINISetting has no Papyrus equivalent (read as 0)'),
    # getObjectType (OBSE): the numeric TES4 form-type code of a reference's
    # base object.  Skyrim's form-type numbering is entirely different and
    # Papyrus has no equivalent read, so comparisons against the TES4 codes
    # could not be honoured even if it did.  Reads as 0 (a bare literal — it
    # sits inside larger conditions).
    'getobjecttype': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    # Vanilla TES4 GetPlayerHasLastRiddenHorse — no Skyrim equivalent (the
    # engine tracks no "last ridden" horse), and SKSE adds none.
    'getplayerhaslastriddenhorse': Cmd(note='{f} has no Skyrim equivalent', flags='bare_bool zero_arg'),
    'getrestrained': Cmd(note='GetRestrained', flags='zero_arg'),
    'getstartingpos': Cmd(note='{f}'),
    # GetTalkedToPC
    # Both spellings answer with the canonical command name, as the branch
    # they replace did -- GetTalkedToPCP is a variant of the same command.
    'gettalkedtopc': Cmd(note='GetTalkedToPC', flags='cmp_bool zero_arg'),
    'gettalkedtopcp': Cmd(note='GetTalkedToPC'),
    'gettype': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    'getweaponskilltype': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    'hasbeenpickedup': Cmd(note='{f}'),
    # Vanilla TES4 HasFlames / light-state toggles on a light reference.
    # Skyrim lights carry no scriptable flame state.
    'hasflames': Cmd(note='HasFlames has no Skyrim equivalent', flags='bare_bool zero_arg'),
    'hasvariable': Cmd(note='{f}'),
    # IsActorDetected takes no argument — "am I detected by ANYONE".  Skyrim
    # only offers IsDetectedBy(specificActor), so there is nothing to call.
    # Emitting IsDetectedBy with the default player arg produced
    # `Game.GetPlayer().IsDetectedBy(Game.GetPlayer())` (always true).
    'isactordetected': Cmd(note='IsActorDetected (no Skyrim equivalent)', flags='cmp_bool'),
    'isbuttonpressed': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    'iscontrolpressed': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    'iscurrentfurnitureobj': Cmd(note='{f}'),
    'iscurrentfurnitureref': Cmd(note='{f}'),
    'isidleplaying': Cmd(note='{f}', flags='zero_arg'),
    'isindangerouswater': Cmd(note='{f}', flags='zero_arg'),
    # isKeyPressed / isKeyPressed2 / isControlPressed (OBSE): raw input
    # polling.  Papyrus has no key-state read outside SKSE, so these read as
    # "not pressed".  A BARE 0 — the call sits inside a larger condition
    # (`if isKeyPressed2 attackKey || isKeyPressed2 attackButton`) where a
    # trailing comment would swallow the rest of the expression.
    'iskeypressed': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    'iskeypressed2': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    'iskeypressed3': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    'isonguard': Cmd(note='{f}'),
    # Implemented by the SKSE64 handler in commands.py. The row keeps the
    # source-level Bool/zero-argument facts available to the tree emitter.
    'isplayable': Cmd(flags='bare_bool cmp_bool zero_arg'),
    'isplayable2': Cmd(flags='bare_bool cmp_bool zero_arg'),
    'isplayermovingintonewspace': Cmd(note='{f} has no Papyrus equivalent (read as 0)'),
    'isplayerslastriddenhorse': Cmd(note='{f} has no Skyrim equivalent', flags='bare_bool zero_arg'),
    #: IsSwimming → no vanilla equivalent, approximate with submerged check
    'isswimming': Cmd(note='IsSwimming', flags='actor_only bare_bool cmp_bool zero_arg'),
    'istimepassing': Cmd(note='{f}'),
    'menumode': Cmd(note='{f}'),
    'offerhorse': Cmd(note='{f}'),
    'opencurrentcontainer': Cmd(note='{f}'),
    'pcb': Cmd(note='{f}'),
    'playbink': Cmd(note='{f}'),
    'purgecellbuffers': Cmd(note='{f}'),
    'refreshtopiclist': Cmd(note='{f}'),
    'removeflames': Cmd(note='{f} has no Skyrim equivalent', flags='zero_arg'),
    'removetopic': Cmd(note='{f}'),
    'respawnhorse': Cmd(note='{f}'),
    #: Rotate → no-op
    'rotate': Cmd(note='Rotate'),
    'sendtrespassalarm': Cmd(note='{f}'),
    #: SetActorFullName → no-op (SKSE required for SetDisplayName)
    'setactorfullname': Cmd(note='SetActorFullName'),
    'setallreachable': Cmd(note='{f}'),
    'setallvisible': Cmd(note='{f}'),
    #: SetCellFullName no-op
    'setcellfullname': Cmd(note='{f}'),
    'setcellownership': Cmd(note='{f}'),
    'setcellpublicflag': Cmd(note='{f}'),
    'setclass': Cmd(note='{f}'),
    #: SetCombatStyle → no-op (managed by CK/race)
    'setcombatstyle': Cmd(note='SetCombatStyle'),
    # SetName is the same capability as SetDisplayName (both rename a form)
    # and neither exists in vanilla Papyrus — Form.psc has no name setter.
    'setdisplayname': Cmd(note='{f}'),
    'setdoordisabletakeoff': Cmd(note='{f}'),
    #: SetForceSneaking
    'setforcesneak': Cmd(note='SetForceSneak', flags='actor_only'),
    'setignorefriendlyhits': Cmd(note='{f}'),
    #: SetInCharGen: no-op
    'setinchargen': Cmd(note='SetInCharGen'),
    'setinvestmentgold': Cmd(note='{f}'),
    #: SetItemValue → no-op
    'setitemvalue': Cmd(note='SetItemValue'),
    #: SetLevel → no-op
    'setlevel': Cmd(note='SetLevel'),
    'setname': Cmd(note='{f}'),
    'setnoavoidance': Cmd(note='{f}'),
    'setnorumors': Cmd(note='{f}'),
    'setpackduration': Cmd(note='{f}'),
    #: SetPlayerInSEWorld: no-op
    'setplayerinseworld': Cmd(note='SetPlayerInSEWorld'),
    'setpublic': Cmd(note='{f}'),
    'setquestobject': Cmd(note='{f}'),
    #: SetRigidBodyMass → no-op
    'setrigidbodymass': Cmd(note='SetRigidBodyMass'),
    'setsceneiscomplex': Cmd(note='{f}'),
    'setshowquestitems': Cmd(note='{f}'),
    'showdialogsubtitles': Cmd(note='{f}'),
    'showenchantment': Cmd(note='{f}'),
    'showspellmaking': Cmd(note='{f}'),
    'stopsound': Cmd(note='StopSound has no Papyrus equivalent'),
    'trapupdate': Cmd(note='{f}'),
    #: Wait → no-op (TES4 Wait is a package instruction, not a time delay)
    'wait': Cmd(note='Wait is a package instruction'),
    # WakeUpPC kicks the player OUT OF SLEEP.  It does not move them, change
    # the camera, or play an animation — the old mapping to
    # Game.ForceThirdPerson() did none of the right things.
    #
    # Skyrim genuinely has no equivalent: no native in Game/Debug/Actor/
    # ObjectReference ends an active sleep, and SKSE registers none either
    # (grepped every NativeFunction in references/skse64-master).  Vanilla's
    # closest case, the Dark Brotherhood abduction, does not wake the player
    # with a function — it runs its whole sequence inside OnSleepStart.
    #
    # That is exactly where our converted body already runs: all 5 TES4 call
    # sites sit in a MenuMode block reading isPCSleeping, which this
    # converter routes into OnSleepStart/OnSleepStop.  So the surrounding
    # code the script wanted to run on waking DOES run, at the right moment;
    # only the "cut the sleep short" part has no target.  Emitting a no-op
    # keeps that faithful and visible instead of inventing a side effect the
    # original never had.
    'wakeuppc': Cmd(note='WakeUpPC (no Skyrim equivalent; body runs in OnSleepStart)'),

    # --- OBSE / TES4-only commands with no VANILLA Papyrus
    # equivalent.  Each was checked against Actor.psc,
    # ObjectReference.psc, Form.psc, Game.psc and Utility.psc and
    # exists in none of them.  Some are available through SKSE
    # (docs/audits/skse_conversion.md); nothing here targets SKSE
    # today, so they are neutralised for now.
    'preloadmagiceffect': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'closeallmenus': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setmodelpath': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getmodelpath': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setlowlevelprocessing': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setharvested': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'selectplayerspell': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setquestitem': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setpcamurderer': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setcellwaterheight': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setstringinisetting': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setstringgamesettingex': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getobseversion': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    # getfirstref/getnextref are NOT here: they have a real special handler
    # (the ref-walk becomes Game.FindRandomActorFromRef sampling).  Listing
    # them neutralised them to `0`, which left the loop body walking a ref that
    # was never assigned.
    'getformfrommod': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getaltcontrol2': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'sifh': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'equipme': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'modavmod': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getvelocity': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setvelocity': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'isunderwater': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getvampire': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getweapontype': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'iswaiting': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getnumfollowers': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getnthfollower': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getspells': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setattackdamage': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'togglespecialanim': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setavmod': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'starttimer': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getmodlocaldata': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setmodlocaldata': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setaltcontrol': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    # OBSE plugin functions with no Skyrim counterpart at all.  SetPlayerSkeleton
    # Path swaps the player's skeleton .nif at runtime (Skyrim's is fixed by
    # race); IsDoor/IsActivator/IsContainer ask a form's TYPE, which Papyrus
    # does not expose (GetType is SKSE).  Neutralised so a werewolf/trap script
    # keeps the rest of its logic instead of failing to compile outright.
    'setplayerskeletonpath': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getplayerskeletonpath': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    # The form-type tests are NOT here: they need the dotted spelling too, so
    # they have FUNCTION_MAP entries and a shared handler (_FORM_TYPE_TESTS).
    # NOT fileexists: neutralising it to 0 answers "the file is MISSING", which
    # is the wrong polarity — see its dedicated handler in _emit_function.
    'getgodmode': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getplayerbirthsign': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getdisplayname': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'getname': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    # AddActorValues (OBSE plugin) — the float-typed AV-modifier accessors that
    # sit alongside the already-listed setavmod/modavmod.  Skyrim has no such
    # plugin, and every TES4 caller already guards the block with
    # `IsPluginInstalled "AddActorValues" == 0 / return`, so the block is dead
    # by construction.
    #
    # Left unrouted they survived as undefined identifiers and failed the
    # CHECKER, so NO .pex was emitted for the owning script at all.  That is
    # what kept mwMorroDefaultQuestScript from running, and with it the
    # PlayerInMorrowind global its GameMode block maintains -- the global that
    # gates Fargoth's unique greeting and his "ring" topic.
    'getavmodf': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),
    'setavmodf': Cmd(note='{f} - no Papyrus equivalent ({f} {a})'),

    # --- Commands the generic mapped-call rendering covers.
    # --- Actor Values ---
    'getactorvalue': Cmd('GetActorValue', MAP, flags='actor_only av av_read'),
    'setactorvalue': Cmd('SetActorValue', MAP, flags='actor_only av'),
    'modactorvalue': Cmd('ModActorValue', MAP, flags='actor_only av'),
    'forceactorvalue': Cmd('ForceActorValue', MAP, flags='actor_only av'),
    'getav': Cmd('GetActorValue', MAP, flags='actor_only av av_read'),
    'setav': Cmd('SetActorValue', MAP, flags='actor_only av'),
    'modav': Cmd('ModActorValue', MAP, flags='actor_only av'),
    'forceav': Cmd('ForceActorValue', MAP, flags='actor_only av'),
    'getbaseactorvalue': Cmd('GetBaseActorValue', MAP, flags='actor_only av av_read'),
    'getbaseav': Cmd('GetBaseActorValue', MAP, flags='actor_only av av_read'),

    #: --- Items / Inventory ---
    'additem': Cmd('AddItem', MAP, flags='actor_only objref_shared'),
    'removeitem': Cmd('RemoveItem', MAP, flags='actor_only objref_shared'),
    'getitemcount': Cmd('GetItemCount', MAP, flags='actor_only objref_self objref_shared'),
    'equipitem': Cmd('EquipItem', MAP, flags='actor_only'),
    'unequipitem': Cmd('UnequipItem', MAP, flags='actor_only'),
    'getnumitems': Cmd('GetNumItems', MAP),
    'getinventoryobject': Cmd('GetNthForm', MAP),
    'drop': Cmd('DropObject', MAP),

    #: --- Spells ---
    'addspell': Cmd('AddSpell', MAP, flags='actor_only'),
    'removespell': Cmd('RemoveSpell', MAP, flags='actor_only'),
    'hasspell': Cmd('HasSpell', MAP, flags='actor_only cmp_bool'),
    'dispel': Cmd('DispelSpell', MAP, flags='actor_only'),
    'dispelspell': Cmd('DispelSpell', MAP, flags='actor_only'),
    'dispelallspells': Cmd('DispelAllSpells', MAP, flags='actor_only zero_arg'),

    #: --- Movement / Position ---
    'getdistance': Cmd('GetDistance', MAP, flags='actor_only objref_shared'),
    'getparentcell': Cmd('GetParentCell', MAP, flags='objref_self'),
    'setposition': Cmd('SetPosition', MAP),
    'getlinkedref': Cmd('GetLinkedRef', MAP, flags='objref_self'),
    'getheadingangle': Cmd('GetHeadingAngle', MAP, flags='objref_self'),

    #: --- Enable / Disable ---
    'enable': Cmd('Enable', MAP, flags='objref_self'),
    'disable': Cmd('Disable', MAP, flags='objref_self'),
    'isenabled': Cmd('IsEnabled', MAP, flags='bare_bool cmp_bool'),
    'activate': Cmd('Activate', MAP, flags='objref_self'),
    'delete': Cmd('Delete', MAP, flags='objref_self'),
    'markfordelete': Cmd('Delete', MAP, flags='zero_arg'),
    'placeatme': Cmd('PlaceAtMe', MAP, flags='actor_only objref_shared'),
    #: Native SetDestroyed (4300/0x10CC) has NO reader, so the polyfill mirrors every write into TES4DestroyedRefs.

    #: --- Actor State ---
    'kill': Cmd('Kill', MAP, flags='actor_only'),
    'killandresurrect': Cmd('Kill', MAP), # then Resurrect manually
    'resurrect': Cmd('Resurrect', MAP, flags='actor_only drop_args'),
    'getdead': Cmd('IsDead', MAP, flags='actor_only bare_bool cmp_bool zero_arg'),
    'isdead': Cmd('IsDead', MAP, flags='actor_only bare_bool cmp_bool zero_arg'),
    'isincombat': Cmd('IsInCombat', MAP, flags='actor_only bare_bool cmp_bool zero_arg'),
    # SetForceSneak is neutralised (no Skyrim equivalent), so the live sneak
    # state is the closest readable value for its getter.
    'getforcesneak': Cmd('IsSneaking', MAP, flags='actor_only bare_bool zero_arg'),
    #: TES4 knocked-down state ~ Skyrim's bleedout/recovery state.
    'getknockedstate': Cmd('IsBleedingOut', MAP, flags='actor_only bare_bool zero_arg'),
    'startcombat': Cmd('StartCombat', MAP, flags='actor_arg actor_only'),
    'stopcombat': Cmd('StopCombat', MAP, flags='actor_only drop_args zero_arg'),
    # IsActorDetected takes NO argument (UESP opcode 0x10B5, 0 params): "is this
    # actor detected by ANYONE".  GetDetected takes 1 Actor and asks the
    # OPPOSITE question from Skyrim's IsDetectedBy: `<observer>.GetDetected
    # <target>` is "does the observer detect the target", while
    # `<target>.IsDetectedBy(<observer>)` is "is the target detected by the
    # observer".  Mapping IsActorDetected to IsDetectedBy made the argument-less
    # form default to the player (`player.IsActorDetected` →
    # Game.GetPlayer().IsDetectedBy(Game.GetPlayer()), the player detecting
    # itself); mapping GetDetected positionally kept receiver and argument in
    # place and asked the mirror-image question.  Both now have special handlers
    # in _emit_function: IsActorDetected is a no-op (Skyrim has no "detected by
    # anyone" primitive, like GetDetectionLevel), GetDetected swaps the two refs.
    'issneaking': Cmd('IsSneaking', MAP, flags='actor_only bare_bool cmp_bool zero_arg'),
    'isweaponout': Cmd('IsWeaponDrawn', MAP, flags='actor_only bare_bool cmp_bool zero_arg'),
    'getsitting': Cmd('GetSitState', MAP, flags='actor_only zero_arg'),
    'getsleeping': Cmd('GetSleepState', MAP, flags='actor_only zero_arg'),
    'getequipped': Cmd('IsEquipped', MAP, flags='actor_only'),
    'istalking': Cmd('IsInDialogueWithPlayer', MAP, flags='zero_arg'),
    'setunconscious': Cmd('SetUnconscious', MAP, flags='actor_only'),
    'setghost': Cmd('SetGhost', MAP, flags='actor_only'),
    'isghost': Cmd('IsGhost', MAP, flags='actor_only bare_bool cmp_bool'),
    # TES4 spells the ghost/unconscious GETTERS `GetIsGhost` / `GetUnconscious`
    # while Skyrim names them IsGhost() / IsUnconscious().  Only the SETTERS
    # were mapped, so a read emitted a bare member access
    # (`NextActor.GetIsGhost`) that the compiler rejects as an unknown property
    # — which is fatal, not cosmetic: the whole script fails to compile and
    # every script declaring a property of its type then fails to LINK.
    'getisghost': Cmd('IsGhost', MAP),
    'getunconscious': Cmd('IsUnconscious', MAP),
    'resetai': Cmd('ResetAI', MAP, flags='zero_arg'),

    #: --- Factions ---
    'getinfaction': Cmd('IsInFaction', MAP, flags='actor_only cmp_bool'),
    'getfactionrank': Cmd('GetFactionRank', MAP, flags='actor_only'),
    'setfactionrank': Cmd('SetFactionRank', MAP, flags='actor_only'),
    'modfactionrank': Cmd('ModFactionRank', MAP),
    'addfaction': Cmd('AddToFaction', MAP),
    'removefaction': Cmd('RemoveFromFaction', MAP),
    'removefromfaction': Cmd('RemoveFromFaction', MAP),

    # --- AI ---
    # setforcerun has a dedicated handler (SpeedMult); deliberately NOT mapped
    # here.  It carried ('SetDontMove', ...) — the exact inverse of "force this
    # actor to run" — which was unreachable only because the handler runs first.

    #: --- Quest ---
    'setstage': Cmd('SetStage', MAP, bare=True),
    'getstage': Cmd('GetStage', MAP, bare=True),
    'getstagedone': Cmd('GetStageDone', MAP, bare=True, flags='cmp_bool'),
    'startquest': Cmd('Start', MAP, bare=True),
    'stopquest': Cmd('Stop', MAP, bare=True),
    'getquestrunning': Cmd('IsRunning', MAP, bare=True, flags='cmp_bool'),
    'isquestcompleted': Cmd('IsCompleted', MAP, bare=True),
    'completequest': Cmd('CompleteQuest', MAP, bare=True),

    #: --- UI / Messages ---
    'message': Cmd('Debug.Notification', MAP, bare=True),
    'messagebox': Cmd('Debug.MessageBox', MAP, bare=True),
    'showmessage': Cmd('Debug.MessageBox', MAP, bare=True),

    # --- Math (OBSE) ---
    # OBSE writes these with a bare whitespace operand (`set x to sin angleZ`),
    # which is why they reached the Papyrus parser unconverted as "no viable
    # alternative at input 'sin'".  Papyrus exposes the same set as globals on
    # Math.psc, and BOTH engines take/return DEGREES, so no unit conversion is
    # needed.  `exp`/`log` have no Papyrus native — see _EXP_POLYFILL below.
    'sin': Cmd('Math.sin', MAP, bare=True),
    'cos': Cmd('Math.cos', MAP, bare=True),
    'tan': Cmd('Math.tan', MAP, bare=True),
    'asin': Cmd('Math.asin', MAP, bare=True),
    'acos': Cmd('Math.acos', MAP, bare=True),
    'atan': Cmd('Math.atan', MAP, bare=True),
    'sqrt': Cmd('Math.sqrt', MAP, bare=True),
    'pow': Cmd('Math.pow', MAP, bare=True),
    'abs': Cmd('Math.abs', MAP, bare=True),
    'floor': Cmd('Math.Floor', MAP, bare=True),
    'ceil': Cmd('Math.Ceiling', MAP, bare=True),
    'exp': Cmd('TES4Polyfill.Exp', MAP, bare=True),
    'log': Cmd('TES4Polyfill.Log', MAP, bare=True),

    # --- OBSE "NS"/silent variants ---
    # The OBSE `...NS` forms differ from the vanilla command ONLY in suppressing
    # the pickup/spell sound and the "item added" message.  Papyrus's plain
    # calls take an abSilent argument for exactly that, so these are the same
    # command, not a missing feature.
    'additemns': Cmd('AddItem', MAP),
    'removeitemns': Cmd('RemoveItem', MAP),
    'addspellns': Cmd('AddSpell', MAP),
    'removespellns': Cmd('RemoveSpell', MAP),
    'equipitemsilent': Cmd('EquipItem', MAP),
    'equipitemns': Cmd('EquipItem', MAP),
    'unequipitemns': Cmd('UnequipItem', MAP),
    # The remaining OBSE spellings of the same two commands.  `2` widens the
    # argument types and `NS`/`Silent` suppress the equip sound — Skyrim carries
    # both on the SAME natives (abSilent), so they map like the variants above
    # rather than being neutralised.
    'equipitem2ns': Cmd('EquipItem', MAP),
    'unequipitem2': Cmd('UnequipItem', MAP),
    'unequipitem2ns': Cmd('UnequipItem', MAP),
    'unequipitemsilent': Cmd('UnequipItem', MAP),
    #: OBSE aliases that only widen the vanilla command's argument types.
    'modav2': Cmd('ModActorValue', MAP, flags='av'),
    'modactorvalue2': Cmd('ModActorValue', MAP, flags='av'),
    'getav2': Cmd('GetActorValue', MAP, flags='av av_read'),
    'setav2': Cmd('SetActorValue', MAP, flags='av'),
    'rand': Cmd('Utility.RandomFloat', MAP, bare=True),
    'islocked': Cmd('IsLocked', MAP, flags='bare_bool objref_self'),
    'getequippedobject': Cmd('GetEquippedWeapon', MAP),
    # TES4 `LoopGroup <group>` plays an idle animation on repeat;
    # PlayGamebryoAnimation is Skyrim's own looping Gamebryo-animation call.
    'loopgroup': Cmd('PlayGamebryoAnimation', MAP),
    # OBSE `IsOnGround` is the complement of Skyrim's IsFlying: both engines
    # only distinguish "supported by the ground" from "not".
    # OBSE `IsModLoaded "Foo.esp"` — Morrowind_ob guards every Oblivion XP
    # hand-off with it.  Game.GetFormFromFile returns None for an unloaded
    # file, which answers the same question in vanilla Papyrus.
    'ismodloaded': Cmd('TES4Polyfill.IsModLoaded', MAP, bare=True),
    # Written bare as `ref.GetRace == Argonian`, so without a FUNCTION_MAP entry
    # the ref.Func branch treated it as PROPERTY access and emitted
    # `ActorRef.GetRace` with no parens ("field or property `GetRace` not
    # found").  Actor.psc has the real native.
    # No vanilla Papyrus equivalent — see COMMAND_ROWS.
    'equipitem2': Cmd('EquipItem', MAP),
    # TES4 `UncompleteQuest` reopens a finished quest; Quest.Reset() is the
    # Papyrus call that returns a quest to its un-run state.
    # OBSE file/plugin probes and god-mode read: no VANILLA Papyrus equivalent
    # (GetGodMode exists only in third-party SKSE plugins, not Game.psc).
    # OBSE `GetModIndex "Foo.esm"` — the plugin's load-order slot.  Papyrus
    # cannot read load order, and every TES4 caller compares it to flag a
    # MIS-ordered install (`> 1` meaning "not loaded early enough").  Special
    # handler so the answer lands on the not-an-error side.
    # OBSE form-TYPE tests, written both bare and as a dotted member read
    # (`crosshairRef.IsDoor == 1`).  The dotted path resolves a name as a
    # FUNCTION only when it is a FUNCTION_MAP key, so without these entries the
    # read fell through to a raw member access on a type that has no such
    # property.  They neutralise in _emit_function (Papyrus cannot ask a form
    # its type — GetType is SKSE).
    # Same question as IsModLoaded — route to the same polyfill.
    'isplugininstalled': Cmd('TES4Polyfill.IsModLoaded', MAP, bare=True),
    # OBSE `print`/`printc` write to the console log; Debug.Trace is Papyrus's
    # own log write, which is the same capability.
    'print': Cmd('Debug.Trace', MAP, bare=True),

    # --- OBSE commands with no VANILLA Papyrus equivalent (neutralised) ---
    # Each has been checked against Actor/ObjectReference/Game/Form/Utility and
    # exists in none of them.  Several are reachable via SKSE — see
    # docs/audits/skse_conversion.md — and neutralising is only the current
    # behaviour, not a judgement that SKSE is off the table.

    # --- Camera / 3D refresh (OBSE) ---
    # `ToggleFirstPerson 0/1` forces the camera into third/first person.  Skyrim
    # splits it into two argument-free globals, so the argument picks which —
    # handled in _emit_function (the bare form toggles, which has no global).
    # Vanilla Papyrus can FORCE a camera mode but cannot QUERY one
    # (Game.psc has ForceFirstPerson/ForceThirdPerson and nothing else;
    # GetCameraState is SKSE).  Every caller here guards a model-refresh, and
    # Skyrim's own model-swap script for the same job — DLC1PlayerVampire-
    # ChangeScript, which re-skins the player exactly like the werewolf swap —
    # just calls Game.ForceThirdPerson() unconditionally rather than testing.
    # So the test is reported False and the refresh path always runs, matching
    # vanilla behaviour instead of inventing a query that does not exist.
    # OBSE `ref.Update3D` rebuilds a reference's 3D after its model changed
    # (Morrowind_ob calls it through fbmwUpdate3D after a werewolf model swap).
    # Papyrus has no direct call — QueueNiNodeUpdate is SKSE — but the engine's
    # own refresh idiom is a disable/enable cycle, which tears down and rebuilds
    # exactly the same 3D.

    #: --- Game State ---
    'getgamesetting': Cmd('Game.GetGameSettingFloat', MAP, bare=True),
    'getgs': Cmd('Game.GetGameSettingFloat', MAP, bare=True),
    'getpcinfaction': Cmd('Game.GetPlayer().IsInFaction', MAP, bare=True),
    'showracemenu': Cmd('Game.ShowRaceMenu', MAP, bare=True),
    'getlevel': Cmd('GetLevel', MAP, flags='actor_only zero_arg'),
    #: 'isininterior' handled by special handler in _emit_function
    'getcurrentgametime': Cmd('Utility.GetCurrentGameTime', MAP, bare=True),
    'getcurrenttime': Cmd('Utility.GetCurrentGameTime', MAP, bare=True),

    #: --- Sound ---

    #: --- Animation ---
    'lookat': Cmd('SetLookAt', MAP, flags='actor_only'),

    #: --- Misc ---
    'getparentref': Cmd('GetLinkedRef', MAP, flags='objref_self zero_arg'),
    'lock': Cmd('Lock', MAP, flags='objref_self'),
    'getlocked': Cmd('IsLocked', MAP, flags='bare_bool cmp_bool objref_self zero_arg'),
    'getlocklevel': Cmd('GetLockLevel', MAP, flags='objref_self zero_arg'),
    'setownership': Cmd('SetActorOwner', MAP), # handled by special handler above
    'setscale': Cmd('SetScale', MAP, flags='actor_only objref_shared'),
    'getscale': Cmd('GetScale', MAP, flags='actor_only objref_shared zero_arg'),
    'say': Cmd('Say', MAP, flags='actor_only objref_shared'),
    'setfactionreaction': Cmd('SetReaction', MAP, bare=True),
    'modfactionreaction': Cmd('ModReaction', MAP, bare=True),
    'triggerscreenblood': Cmd('Game.TriggerScreenBlood', MAP, bare=True),
    'removeme': Cmd('Delete', MAP),

    #: --- Object state ---
    'setdisabled': Cmd('Disable', MAP),
    'setenabled': Cmd('Enable', MAP),
    'getis3dloaded': Cmd('Is3DLoaded', MAP, flags='bare_bool'),

    # --- Weather ---
    # Same reading, spelled out in full.  Takes no arguments, so it is ALWAYS
    # read bare — without a FUNCTION_MAP entry the bare-identifier path had
    # nothing to route and the name survived into the output undefined.

    #: --- Special compound player.X ---
    'player.additem': Cmd('Game.GetPlayer().AddItem', MAP, bare=True),
    'player.removeitem': Cmd('Game.GetPlayer().RemoveItem', MAP, bare=True),
    'player.getitemcount': Cmd('Game.GetPlayer().GetItemCount', MAP, bare=True),
    'player.addspell': Cmd('Game.GetPlayer().AddSpell', MAP, bare=True),
    'player.removespell': Cmd('Game.GetPlayer().RemoveSpell', MAP, bare=True),
    'player.moveto': Cmd('Game.GetPlayer().MoveTo', MAP, bare=True),
    'player.placeatme': Cmd('Game.GetPlayer().PlaceAtMe', MAP, bare=True),

    #: --- Additional Actor/Combat ---
    'getcombattarget': Cmd('GetCombatTarget', MAP, flags='zero_arg'),
    'getparentcellowner': Cmd('GetParentCell', MAP),
    'hasmagiceffect': Cmd('HasMagicEffect', MAP, flags='actor_only'),
    'setopendoor': Cmd('SetOpen', MAP),

    #: --- Player state ---
    'disableplayercontrols': Cmd('Game.DisablePlayerControls', MAP, bare=True),
    'enableplayercontrols': Cmd('Game.EnablePlayerControls', MAP, bare=True),
    'enablefasttravel': Cmd('Game.EnableFastTravel', MAP, bare=True),
    # OBSE `SetCanFastTravelFromWorld <worldspace> <flag>` toggles fast travel
    # PER WORLDSPACE.  Skyrim only has the global Game.EnableFastTravel(bool),
    # so the worldspace argument is dropped — see the special handler, which
    # cannot be a plain mapping because the arity differs (a straight map passed
    # the worldspace where the bool goes).
    # OBSE string_var builder; Papyrus String is the literal.  Special handler
    # in _emit_function — the inert ar_/sv_ catch-all would leave it undefined.

    #: --- AI/Package ---

    #: --- Object Interaction ---
    'removeallitems': Cmd('RemoveAllItems', MAP, flags='actor_only objref_shared'),
    # Special handlers in _emit_function (see there for why each is inert):
    # path-based music has no Skyrim API, IsCasting maps to the animation graph.
    # The same engine function (0x1153) under its other authored spelling —
    # Knights.esp writes `<horse>.IsPlayersLastRiddenHorse == 0`.

    # --- Cell/Location ---
    # 'isininterior' handled by special handler in _emit_function

    #: --- Faction/Crime ---

    #: --- Dialog/Topic ---
    'saycustom': Cmd('Say', MAP, flags='actor_only objref_shared'),

    #: --- Look/Perception ---
    'look': Cmd('SetLookAt', MAP, flags='actor_only'),

    # --- Display/Name ---
    # GetDisplayName is SKSE, not vanilla — Form.psc/ObjectReference.psc/
    # Actor.psc have no name accessor at all, so these emitted a call that does
    # not exist.  Neutralised via COMMAND_ROWS.

    #: --- Travel ---
    'movetomyeditorlocation': Cmd('MoveToMyEditorLocation', MAP),
    'moveto': Cmd('MoveTo', MAP, flags='objref_self'),
    'movetomarker': Cmd('MoveTo', MAP),

    #: --- Path/Linked Points ---

    #: --- Shader/Visual Effects ---

    #: --- AI/Wait ---
    'sayto': Cmd('Say', MAP, flags='actor_only objref_shared'),

    #: --- Detection ---

    #: --- Door/Object State ---
    'setopenstate': Cmd('SetOpen', MAP, flags='objref_self'),

    #: --- Player Skill/Misc ---
    'modpcskill': Cmd('Game.AdvanceSkill', MAP, bare=True, flags='av'),
    'modpcmiscstat': Cmd('Game.IncrementStat', MAP, bare=True),

    #: --- Trap/Custom functions that are quest-specific ---

    #: --- Gold ---
    'getgold': Cmd('GetGoldAmount', MAP, flags='actor_only zero_arg'),

    #: --- Alpha ---
    'saa': Cmd('SetAlpha', MAP, flags='actor_only actorbase_arg'),
    'setactoralpha': Cmd('SetAlpha', MAP, flags='actor_only actorbase_arg'),
    'gaa': Cmd('GetAlpha', MAP, flags='actor_only actorbase_arg'),
    'getactoralpha': Cmd('GetAlpha', MAP, flags='actor_only actorbase_arg'),

    # --- Interior ---
    # 'isininterior' handled by special handler in _emit_function

    #: --- Save ---
    'autosave': Cmd('Game.RequestAutoSave', MAP, bare=True),

    # --- Misc unmapped ---
    # Oblivion accepts BOTH spellings of the creature test, and the dotted
    # member path (`NextActor.IsCreature`) resolves a function only when the
    # name is a FUNCTION_MAP key.  Without the alias the read fell through to a
    # raw member access on a type that has no such property, failing the whole
    # compile.  Routed to the same polyfill handler as `getiscreature`.
    'getclothingvalue': Cmd(note='{f} {a}  (clothing value not tracked in Skyrim; 0)', flags='zero_arg'),
    'getshouldattack': Cmd(note='{f} {a}  (no Papyrus equivalent; 0 -- sibling IsInCombat term carries the check)'),
    'getopenstate': Cmd('GetOpenState', MAP, flags='zero_arg'),
    'isessential': Cmd('IsEssential', MAP, flags='actor_arg actor_only cmp_bool zero_arg'),
    'getlos': Cmd('HasLOS', MAP, flags='actor_only'),
    # TES4 `IsOwner [owner]` asks whether the ACTOR owns this reference, and is
    # written bare (`if IsOwner != 1`) to mean the player.  Mapping it to
    # IsInFaction was wrong twice over: it is a different question, and the bare
    # form emitted the argument-less `IsInFaction()`, a hard compile error that
    # took the whole script down.  Skyrim answers it with GetActorOwner().
    # No native bool reader for the destroyed state, but the destruction STAGE
    # is native: stage > 0 means the ref has been destroyed.  IsDisabled() was
    # unrelated (a destroyed ref is still enabled) and always returned false.
    'setlookat': Cmd('SetLookAt', MAP, flags='actor_only'),
    # GetSelf / GetActionRef: what the script's subject and the activating
    # reference are called in this base type.
    'getself': Cmd('{self_ref}'),
    'getactionref': Cmd('{action_ref}'),

    #: GetPCIsSex: Skyrim's ActorBase.GetSex() is 0 male / 1 female.
    'getpcissex': Cmd(
        'Game.GetPlayer().GetActorBase().GetSex() == {?0female}',
        arms=('1', '0'), defaults={0: 'male'}),

    #: GetIsSex on any actor, same encoding.
    'getissex': Cmd('({ref}.GetActorBase().GetSex() == {?0female})', ACTOR,
                    arms=('1', '0'), defaults={0: 'male'}, flags='cmp_bool'),

    # OBSE `GetLocalGravity <axis>` -- the per-axis gravity acting on the
    # calling reference.  Papyrus exposes no gravity accessor at all (the
    # value lives in the `fGravity` INI setting, present in BOTH engines and
    # reachable from neither script language), so the literal constant IS the
    # faithful translation: gravity in Skyrim is a world constant pointing
    # straight down, so X and Y are always 0 and only Z carries the magnitude.
    # Signed to match OBSE, whose callers subtract it as a downward
    # acceleration.
    'getlocalgravity': Cmd('{?0z}', arms=('-9.81', '0.0'), defaults={0: 'Z'}),

    # `ToggleFirstPerson <0|1>` -- Oblivion's one command with an argument is
    # two argument-free globals in Skyrim.  0 forces THIRD person, 1 forces
    # first; the bare form is a true toggle, which Papyrus cannot express
    # because it cannot read the current mode, so it takes the third-person
    # branch (the mode every caller here is refreshing in).
    'togglefirstperson': Cmd(
        '{?01}', arms=('Game.ForceFirstPerson()',
                       'Game.ForceThirdPerson()'), defaults={0: '0'}),

    # GetDestroyed / GetDisabled -> the polyfill's FormList shadow.  Skyrim
    # has NO reader for the destroyed flag, and GetCurrentDestructionStage()
    # reads the unrelated DEST stage system this conversion never writes -- so
    # it returned 0 for every record and the read was always false.
    'getdisabled': Cmd('TES4Polyfill.GetDisabled({ref}, {destroyed})', flags='objref_self zero_arg'),
    'isdisabled': Cmd('TES4Polyfill.GetDisabled({ref}, {destroyed})', flags='bare_bool objref_self'),
    'getdestroyed': Cmd('TES4Polyfill.GetDestroyed({ref}, {destroyed})', flags='bare_no_equiv zero_arg'),

    #: SetDestroyed writes that same shadow list.
    'setdestroyed': Cmd(
        'TES4Polyfill.SetDestroyed({ref}, {destroyed}, {b0})',
        defaults={0: '1'}),

    # The Oblivion gates: closing one needs the destroyed list, since that is
    # where the gate's closed state is recorded.
    'closecurrentobliviongate': Cmd(
        'TES4Polyfill.CloseCurrentOblivionGate({destroyed})'),
    'forcecloseobliviongate': Cmd(
        'TES4Polyfill.CloseOblivionGate({ref}, {destroyed})'),
    'closeobliviongate': Cmd(
        'TES4Polyfill.CloseOblivionGate({ref}, {destroyed})'),

    # ModAmountSoldStolen adds GOLD to the "amount fenced" counter, which
    # Skyrim exposes only as a condition function.  Backed by the synthesized
    # TES4GoldFenced global; NOT the vanilla "Items Stolen" stat, which counts
    # items and is driven by the engine on every theft.
    'modamountsoldstolen': Cmd(
        'TES4GoldFenced.Mod({f0})', defaults={0: '1'},
        self_type=('TES4GoldFenced', 'GlobalVariable')),

    # IsPCAMurderer: murder is the 1000-gold band.  `> 0` was the *Attack*
    # test -- any violent bounty at all -- which made the player a "murderer"
    # for a bar brawl.
    'ispcamurderer': Cmd(
        '(TES4CyrodiilCrimeFaction.GetCrimeGoldViolent() >= 1000)',
        self_type=('TES4CyrodiilCrimeFaction', 'Faction')),
    'ispcanmurderer': Cmd(
        '(TES4CyrodiilCrimeFaction.GetCrimeGoldViolent() >= 1000)',
        self_type=('TES4CyrodiilCrimeFaction', 'Faction')),
    'getpcismurderer': Cmd(
        '(TES4CyrodiilCrimeFaction.GetCrimeGoldViolent() >= 1000)',
        self_type=('TES4CyrodiilCrimeFaction', 'Faction')),

    # SetDoorDefaultOpen -> SetOpen.  The argument is a BOOLEAN, not a flag to
    # be ignored: per UESP's function table (opcode 0x10D8, 1 Integer) "a value
    # of 1 will make the door open by default", so 0 CLOSES it.  Hardcoding
    # SetOpen(true) inverted the `0` form -- MQ16's endgame
    # `ICPalaceElderCouncilMainDoor.SetDoorDefaultOpen 0`, the line whose own
    # comment reads "close Elder Council door", flung it open instead.
    'setdoordefaultopen': Cmd('{ref}.SetOpen({b0})', defaults={0: '1'}),

    #: SetScale / SetSize.
    'setsize': Cmd('{ref}.SetScale({a0})', defaults={0: '1.0'}),

    #: GetPCMiscStat reads one of the game's own tracked statistics.
    'getpcmiscstat': Cmd('Game.QueryStat("{s0}")',
                         defaults={0: 'Items Stolen'}),

    # GetInSameCell: both references' parent cells compared.
    # GetParentCell is an ObjectReference method, so the subject must NOT
    # be promoted to Actor: `(Self as Actor)` on a non-actor yields None.
    'getinsamecell': Cmd('({ref}.GetParentCell() == {a0}.GetParentCell())',
                         defaults={0: 'Game.GetPlayer()'}, flags='cmp_bool'),
    'getinsamecellas': Cmd('({ref}.GetParentCell() == {a0}.GetParentCell())',
                           defaults={0: 'Game.GetPlayer()'}),

    # --- No Papyrus equivalent -------------------------------------------
    # OBSE `forEach <it> <- <container> ... loop`.  The loop OPENER; its
    # body is commented out by the walker (see emit/script.py), which is what
    # makes the iterator's absence harmless.
    'foreach': Cmd(note='{f} - OBSE array/string command, no Papyrus '
                        'equivalent ({f} {a})'),
    'loop': Cmd(note='{f} - OBSE array/string command, no Papyrus '
                     'equivalent ({f} {a})'),
    'getmodindex': Cmd('1',
                       note='GetModIndex - Papyrus cannot read load order'),
    'unlockachievement': Cmd(
        note='UnlockAchievement {a}  ;no Papyrus equivalent', flags='bare_no_equiv'),

    # --- Migrated from `_emit_function`'s branch chain --------------------
    # Each row below replaced a name-guarded branch that resolved a receiver,
    # converted an argument or two and returned one template.  The rationale
    # that sat above the branch sits above its row.

    # SkipAnim / SetNumericIniSetting: the ;NE: text IS the emission, not a
    # comment beside a value -- both are written in STATEMENT position
    # (Nehrim's portcullis calls <ref>.SkipAnim on its own line), so the
    # whole line becomes the comment.
    'skipanim': Cmd(note='SkipAnim  ;no Papyrus equivalent', flags='bare_no_equiv zero_arg'),
    'setnumericinisetting': Cmd(note='{f} {a}  ;no Papyrus INI access'),

    # Update3D: the operand is the reference to refresh, named either as the
    # receiver or as the first argument; the player is TES4's default subject.
    'update3d': Cmd('TES4Polyfill.Update3D({ref})', OBJREF),

    # TES4 UncompleteQuest <Quest> reopens a finished quest, naming the quest
    # as an ARGUMENT.  Papyrus spells it as a method on the quest itself, so
    # the argument has to become the receiver -- mapping it straight onto Reset
    # emitted Reset(fbmwEBBone) ("function takes 0 parameters not 1").
    'uncompletequest': Cmd('{a0}.Reset()', RAW, defaults={0: 'Self'}),

    # GetPos/GetAngle/GetStartingAngle: the axis argument picks the accessor.
    # These are declared on ObjectReference, so the subject must NOT be
    # promoted to Actor -- TES4 reads the position of plain scenery
    # (SEXedPuzStatue1-5 are STATs the Xeddefen puzzle rotates), and an
    # Actor Property on a STAT never binds, so the read came back None.
    'getpos': Cmd('{ref}.GetPosition{g0}()', OBJREF, defaults={0: 'X'}, flags='objref_self'),
    'getangle': Cmd('{ref}.GetAngle{g0}()', OBJREF, defaults={0: 'X'}, flags='objref_self'),
    'getstartingangle': Cmd('{ref}.GetAngle{g0}()', OBJREF,
                            defaults={0: 'X'}),

    # Sound playback.  Vanilla writes the EditorID QUOTED (PlaySound
    # "AMBBaenlinDeath") as often as bare, and the property must be registered
    # under the name that is actually EMITTED -- registering the raw argument
    # kept the quotes and _safe_property_name turned each into an underscore,
    # declaring a second, never-bindable Sound Property _X_ alongside the real
    # one (75 dead properties across 23 files).
    'playsound': Cmd('{p0}.Play(Game.GetPlayer())', types={0: 'Sound'}),
    'playsound3d': Cmd('{p0}.Play({ref})', OBJREF, types={0: 'Sound'}),

    # GetPCFaction{Murder,Attack,Steal}: no Papyrus native, so rebuilt from the
    # crime-gold split -- Steal = non-violent, Attack = violent below the murder
    # bounty, Murder = violent at or above.  All 14 Skyrim.esm crime factions
    # use murder=1000 assault=40, which the importer also writes.  One shared
    # GetCrimeGoldViolent() shadowed every murder branch (FGExpulsionScript,
    # TGCastOut, both MGExpulsion scripts).
    'getpcfactionsteal': Cmd(
        '({a0}.GetCrimeGoldNonViolent() > 0) as Int',
        defaults={0: 'None'}, types={0: 'Faction'}),
    'getpcfactionmurder': Cmd(
        '({a0}.GetCrimeGoldViolent() >= 1000) as Int',
        defaults={0: 'None'}, types={0: 'Faction'}),
    'getpcfactionattack': Cmd(
        '({a0}.GetCrimeGoldViolent() > 0 && '
        '{a0}.GetCrimeGoldViolent() < 1000) as Int',
        defaults={0: 'None'}, types={0: 'Faction'}),

    # GetNextRef -- OBSE's walk over every reference in the loaded cells.
    # Papyrus has no such iterator, but Skyrim ships the engine's own "an actor
    # near here" primitive, so an ACTOR walk becomes repeated
    # FindRandomActorFromRef sampling; the authored loop re-assigns the
    # variable each pass, so a fresh sample per pass is exactly the iteration
    # it asked for.  GetFirstRef carries the form TYPE and has its own handler:
    # only form type 69 (actors) has an Actor-typed primitive behind it.
    'getnextref': Cmd(
        'Game.FindRandomActorFromRef(Game.GetPlayer(), 4096.0)'),

    #: ShowMap: the marker is the subject; bare it maps Self.
    'showmap': Cmd('{p0}.AddToMap(true)', types={0: 'ObjectReference'},
                   defaults={0: 'Self'}),

    # SetForceRun -> SpeedMult.  Skyrim has no force-run flag; the actor value
    # is what the engine actually reads for movement speed.
    'setforcerun': Cmd('{ref}.SetActorValue("SpeedMult", {?01})', ACTOR,
                       defaults={0: '0'}, arms=('150.0', '100.0'), flags='actor_only branch_only'),

    #: ResetInterior -> Cell.Reset().
    'resetinterior': Cmd('{p0}.Reset()', RAW, types={0: 'Cell'},
                         defaults={0: 'Self'}),

    #: IsPCRace / GetPCIsRace -> the player's race compared to the argument.
    'ispcrace': Cmd('Game.GetPlayer().GetRace() == {a0}',
                    defaults={0: 'None'}, types={0: 'Race'}),
    'getpcisrace': Cmd('Game.GetPlayer().GetRace() == {a0}',
                       defaults={0: 'None'}, types={0: 'Race'}, flags='cmp_bool'),

    #: Expel -> faction.SetPlayerExpelled(true).
    'expel': Cmd('{a0}.SetPlayerExpelled(true)', defaults={0: 'None'},
                 types={0: 'Faction'}),

    #: GetIsRace: ref.GetIsRace RaceRef -> ref.GetRace() == raceRef.
    'getisrace': Cmd('{ref}.GetRace() == {a0}', ACTOR,
                     defaults={0: 'None'}, types={0: 'Race'}, flags='cmp_bool'),

    # GetInWorldSpace -> a WorldSpace comparison.  GetPlayerInSEWorld stays a
    # literal 0: an SI interior has no worldspace and no invariant to key on,
    # and 11 of 16 sites test == 0 (suppression guards, right in Cyrodiil).
    'getinworldspace': Cmd('{ref}.GetWorldSpace() == {a0}', RAW,
                           defaults={0: 'None', 'ref': 'Game.GetPlayer()'},
                           types={0: 'WorldSpace'}),

    # GetDetected is the OBSERVER's question; IsDetectedBy is the TARGET's, so
    # receiver and argument SWAP.  Mapping them positionally made every call
    # ask the mirror-image question: CharGenQuest's GlenroyRef.getdetected
    # player (has Glenroy spotted the player, which advances the Ambush-B
    # stage) became "has the player spotted Glenroy", true the moment the
    # player looks down the corridor.
    'getdetected': Cmd('{a0}.IsDetectedBy({ref})', ACTOR,
                       defaults={0: 'Game.GetPlayer()'},
                       types={0: 'Actor'}, flags='actor_arg cmp_bool'),

    # GetDetectionLevel has the SAME shape (UESP opcode 0x10B4, receiver is the
    # observer), so it gets the same swap.  The threshold must be RESCALED, not
    # just wrapped: TES4 levels run 0..3 but IsDetectedBy is a Bool, and all 56
    # call sites read >= 2, >= 3 or == 3.  Scaling to TES4's top level yields
    # 0 or 3, satisfying every threshold exactly when detected.  A bare
    # Bool >= 2 is rejected by the CK compiler outright.
    'getdetectionlevel': Cmd('(({a0}.IsDetectedBy({ref}) as Int) * 3)', ACTOR,
                             defaults={0: 'Game.GetPlayer()'},
                             types={0: 'Actor'}, flags='actor_arg'),

    # ForceFlee / Flee (UESP function index 407).  Skyrim has no Flee call --
    # fleeing is driven by the Confidence actor value, so dropping the actor to
    # Cowardly and re-evaluating its package makes the ENGINE break off combat.
    # That is the engine's own mechanism rather than a Papyrus approximation.
    'flee': Cmd('{ref}.SetActorValue("Confidence", 0)\n'
                '  {ref}.EvaluatePackage()', ACTOR, flags='bare_no_equiv'),
    'forceflee': Cmd('{ref}.SetActorValue("Confidence", 0)\n'
                     '  {ref}.EvaluatePackage()', ACTOR),

    # FileExists "<path>" -- OBSE probes a loose file, which Papyrus cannot see.
    # It answers PRESENT, not absent, and polarity is the whole point: every
    # caller uses it as an installation check against Oblivion-side artifacts
    # (BSAs, inis) that do not exist after conversion BY DESIGN.  Answering 0
    # fired every "missing file" branch and greeted the player with a bogus
    # installation-error box on load.
    'fileexists': Cmd('1', note='FileExists - converted assets are deployed '
                                'by the pipeline, not under the TES4 path'),

    # SetCanFastTravelFromWorld: Skyrim's toggle is GLOBAL, so the worldspace
    # operand has nowhere to go.  Keep the flag, note the widened scope.
    'setcanfasttravelfromworld': Cmd(
        'Game.EnableFastTravel({b1})', defaults={1: '1'},
        note='Skyrim fast travel is global, not per-worldspace'),

    #: PickIdle / PlayIdle -> a behavior-graph event on the actor.
    'pickidle': Cmd('Debug.SendAnimationEvent({ref}, "{s0}")', ACTOR,
                    defaults={0: 'IdleForceDefaultState'}, flags='zero_arg'),
    'playidle': Cmd('Debug.SendAnimationEvent({ref}, "{s0}")', ACTOR,
                    defaults={0: 'IdleForceDefaultState'}),

    # OBSE raw-INPUT control.  Skyrim has no vanilla input API (SKSE-only), so
    # the writers no-op and the readers return 0.  Kept as ONE family because
    # enumerating them one build at a time is how disableKey survived to fail
    # on its own.
    'disablekey': Cmd(note=_OBSE_INPUT_NOTE, flags='bare_no_equiv'),
    'enablekey': Cmd(note=_OBSE_INPUT_NOTE, flags='bare_no_equiv'),
    'tapkey': Cmd(note=_OBSE_INPUT_NOTE, flags='bare_no_equiv'),
    'holdkey': Cmd(note=_OBSE_INPUT_NOTE, flags='bare_no_equiv'),
    'releasekey': Cmd(note=_OBSE_INPUT_NOTE, flags='bare_no_equiv'),
    'playback': Cmd(note=_OBSE_INPUT_NOTE, flags='bare_no_equiv'),
    'playbackalt': Cmd(note=_OBSE_INPUT_NOTE, flags='bare_no_equiv'),
    'disablecontrol': Cmd(note=_OBSE_INPUT_NOTE, flags='bare_no_equiv'),
    'enablecontrol': Cmd(note=_OBSE_INPUT_NOTE, flags='bare_no_equiv'),
    'tapcontrol': Cmd(note=_OBSE_INPUT_NOTE, flags='bare_no_equiv'),

    # ResetFallDamageTimer (OBSE) cleared accumulated fall distance.  Skyrim
    # has the console command (opcode 4404) but no Papyrus binding, so the
    # substitute is the GMST the fall-damage formula actually reads
    # (fJumpFallHeightMin, default 600): pushing the threshold beyond any
    # reachable fall makes the landing survivable, which is the whole
    # observable behaviour.  TES4Polyfill restores the original on release.
    'resetfalldamagetimer': Cmd(
        'TES4Polyfill.SuppressFallDamage({event_actor})', flags='zero_arg'),

}


# Command names that only a dedicated handler in `_emit_function`
# converts.  They carry no data of their own -- the entry exists so the
# string path recognises the name AS a command rather than an identifier.
HANDLED_COMMANDS = frozenset((
    'addtopic', 'closecurrentobliviongate', 'closeobliviongate',
    'con_runmemorypass', 'disablecontrol', 'disablekey', 'emcgetplaylist',
    'emcisbattleoverridden', 'emcismusiconhold', 'emcmusicnexttrack',
    'emcmusicresume', 'emcmusicstop', 'emcplaytrack', 'emcsetbattleoverride',
    'emcsetmusichold', 'emcsetmusictype', 'enablecontrol', 'enablekey',
    'expel', 'fileexists', 'flee', 'forceflee', 'getactionref',
    'getbuttonpressed', 'getdestroyed', 'getdetected', 'getdetectionlevel',
    'getfirstref', 'getgameloaded', 'getglobalvalue', 'getincell',
    'getincell', 'getinsamecell', 'getinsamecellas', 'getinworldspace',
    'getisclass', 'getiscurrentpackage', 'getisid', 'getisrace', 'getissex',
    'getlocalgravity', 'getmenufloatvalue', 'getmenuhastrait',
    'getmenustringvalue', 'getmodindex', 'getnextref', 'getnthspell',
    'getownership', 'getpcfactionattack', 'getpcfactionattack',
    'getpcfactionmurder', 'getpcfactionsteal', 'getpcfactionsteal',
    'getpcisclass', 'getpcismurderer', 'getpcisrace', 'getpcissex',
    'getplayerinseworld', 'getsecondspassed', 'getself', 'getspellcount',
    'holdkey', 'isactionref', 'isactivator', 'isactor', 'isarmor', 'isbook',
    'isclothing', 'iscontainer', 'isdoor', 'isingredient', 'iskey', 'islight',
    'ismisc', 'isowner', 'ispcamurderer', 'ispcanmurderer', 'ispcrace',
    'isplayersleeping', 'ispotion', 'israining', 'isspelltarget', 'isweapon',
    'lookismile', 'modamountsoldstolen', 'moddisposition', 'pathtoref',
    'pickidle', 'playback', 'playbackalt', 'playgroup', 'playidle',
    'playmagiceffectvisuals', 'playsound', 'playsound3d', 'pme',
    'positionworld', 'pushactoraway', 'releasekey', 'resetfalldamagetimer',
    'resetinterior', 'setcanfasttravelfromworld', 'setdestroyed',
    'setdoordefaultopen', 'setessential', 'setforcewalk', 'setglobalvalue',
    'setnumericinisetting', 'setpcfactionattack', 'setpcfactionmurder',
    'setpcfactionsteal', 'showbirthsignmenu', 'showmap', 'skipanim', 'sme',
    'startconversation', 'stopmagiceffectvisuals', 'streammusic',
    'sv_construct', 'tapcontrol', 'tapkey', 'togglefirstperson',
    'uncompletequest', 'unlockachievement', 'update3d',
))


# Every name the converter treats as a TES4 COMMAND rather than an identifier.
# `COMMAND_ROWS` carries the ones that convert from data; `HANDLED_COMMANDS`
# the ones only a dedicated handler converts.  The distinction matters to the
# emitters and to nothing else, so the membership tests use the union.
KNOWN_COMMANDS = frozenset(COMMAND_ROWS) | HANDLED_COMMANDS


#: Papyrus types that hold a VALUE rather than an object.  Everything else is
#: an object type, which cannot be assigned an integer -- TES4 wrote `set
#: myRef to 0` to clear a reference, and that has to become `None`.
#: `GlobalVariable` is an object, but a TES4 write to a global is a write to
#: its VALUE (`GlobalVariable.SetValue(0)`), so its integer must survive.
_PAPYRUS_VALUE_TYPES = frozenset({'Int', 'Float', 'Bool', 'String',
                                  'GlobalVariable'})


#: `ACTOR_VALUE_MAP` keyed lowercase.  The substitution used to run as a regex
#: over the whole expression once per entry; the tree hands over one name, so
#: it is a lookup.
_ACTOR_VALUE_MAP_LOW = {k.lower(): v for k, v in ACTOR_VALUE_MAP.items()}


#: Command FAMILIES matched by prefix rather than by whole name.  Longest
#: prefix wins, so a more specific family can override a broader one.
#: `emcount` is a local VARIABLE in some scripts rather than a command, which
#: is why the Elys family also requires a longer name.
COMMAND_PREFIXES = (
    # OBSE arrays and string-variables (ar_Construct/ar_Null/sv_Destruct).
    # Papyrus has real arrays and strings but no equivalent of OBSE's dynamic
    # containers, and the surrounding logic reads them element-by-element --
    # there is nothing to translate call-for-call.
    ('ar_', Cmd(note='{f} - OBSE array/string command, no Papyrus '
                     'equivalent ({f} {a})')),
    ('sv_', Cmd(note='{f} - OBSE array/string command, no Papyrus '
                     'equivalent ({f} {a})')),
    # OBSE console commands: Papyrus cannot execute the console at all.
    ('con_', Cmd(note='{f} {a}  ;OBSE console command, no Papyrus '
                      'equivalent')),
    # OBSE menu queries.  Skyrim's UI is Scaleform and exposes none of this.
    ('getmenu', Cmd(note='{f} {a}  ;OBSE menu query, no Papyrus equivalent')),
    ('setmenu', Cmd(note='{f} {a}  ;OBSE menu query, no Papyrus equivalent')),
    # Nehrim's bundled Elys Music Control plugin (emcMusicStop,
    # emcSetMusicHold, emcIsBattleOverridden...).  These control the PLAYLIST
    # rather than naming a track, and Papyrus exposes no equivalent even with
    # MUSC authored.
    ('emcm', Cmd(note='{f} - no Papyrus equivalent for the Elys '
                      'music-control API ({a})')),
    ('emcs', Cmd(note='{f} - no Papyrus equivalent for the Elys '
                      'music-control API ({a})')),
    ('emcg', Cmd(note='{f} - no Papyrus equivalent for the Elys '
                      'music-control API ({a})')),
    ('emci', Cmd(note='{f} - no Papyrus equivalent for the Elys '
                      'music-control API ({a})')),
    ('emcp', Cmd(note='{f} - no Papyrus equivalent for the Elys '
                      'music-control API ({a})')),
)


def command_prefix_row(name: str):
    """The `COMMAND_PREFIXES` row for `name`, or None.

    Longest prefix first, so a specific family beats a broader one.
    """
    low = name.lower()
    best = None
    for prefix, row in COMMAND_PREFIXES:
        if low.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), row)
    return best[1] if best else None



#: Compound `ref.func` names whose bare form has its own handler.
COMPOUND_HAS_OWN_HANDLER = ('placeatme', 'moveto', 'movetomarker')


#: TES4 commands that imply `player` when bare; Papyrus requires it written.
DEFAULT_ARGS = {
    'startconversation': 'Game.GetPlayer()',
    'sayto': 'Game.GetPlayer()',
    'getrandompercent': '0, 99',
    'isactordetected': 'Game.GetPlayer()',
    'getdetected': 'Game.GetPlayer()',
    'isdetectedby': 'Game.GetPlayer()',
    'setownership': 'Game.GetPlayer().GetActorBase()',
    'setactorowner': 'Game.GetPlayer().GetActorBase()',
}

#: Events on the engine's dispatch path, where a blocking Say stalls it.
DISPATCH_EVENTS = ('onpackagestart', 'onpackageend', 'onpackagechange',
                   'onhit', 'oncombatstatechanged', 'onactivate',
                   'ondeath', 'ondying', 'onload', 'oncellattach',
                   'onlocationchange')

#: Tokens a Say command needs before its topic can be a speak-as target.
SAY_SPEAKAS_MIN_TOKENS = {'say': 3, 'saycustom': 3, 'sayto': 4}

#: GMSTs Skyrim exposes only as an actor value.
GMST_TO_ACTOR_VALUE = {
    'fjumpheightmin': 'JumpingBonus',
    'fjumpheightmax': 'JumpingBonus',
    'fmoverunmult': 'SpeedMult',
    'fmovecharwalkmin': 'SpeedMult',
    'fmovecharwalkmax': 'SpeedMult',
    'fmoverunathleticsmult': 'SpeedMult',
}


#: Actor values TES5 stores as an enum tier, with the tier count.
ENUM_ACTOR_VALUES = {
    'aggression': 3, 'confidence': 4, 'assistance': 2,
    'mood': 8, 'morality': 3,
}

#: TES4 0-100 thresholds mapped onto those tiers, highest first.
#: `> 5`, not `>= 6`: a fractional 5.5 is tier 0, as `raw <= 5` was.
ENUM_AV_LADDERS = {
    'aggression': ((106, 3), (65, 2), (5.000001, 1), (0, 0)),
    'confidence': ((100, 4), (70, 3), (40, 2), (15, 1), (0, 0)),
}


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
from script_convert.resolve import (  # noqa: E402,F401
    resolve_property_formid, _digit_stripped_formid,
)


def _flagged(flag):
    """Every command whose COMMAND_ROWS row carries `flag`; rowless ones cannot."""
    return frozenset(name for name, row in COMMAND_ROWS.items()
                     if flag in row.flags)


#: Commands whose Papyrus equivalent takes fewer arguments; drop the extras.
DROP_ARGS_FUNCS = _flagged('drop_args')

# TES4 functions naming their target as an ARGUMENT (`GetDeadCount X`,
# `SetEssential X 0`).  Skyrim declares both on ActorBase, so a bare occurrence
# says nothing about the script's own type: used ONLY to stop `_infer_extends`
# upgrading an ACTI/DOOR script to `extends Actor`, which will not bind.  They
# stay in `_ACTOR_ONLY_FUNCTIONS`; the call site still needs the cast.
_ACTORBASE_ARG_FUNCTIONS = _flagged('actorbase_arg') | frozenset({
    'getdeadcount', 'setessential',
})

# TES4 functions whose Papyrus signature declares an Actor parameter, per the
# vanilla headers: StartCombat, IsHostileToActor, Get/SetRelationshipRank.  A
# script-typed argument must be cast at the call site or the checker rejects it
# (`StartCombat(NQ05Soldat01nRef)`).  SetLookAt/Say/GetDistance are ABSENT --
# their parameters are ObjectReference, which converts implicitly.
_ACTOR_ARG_FUNCTIONS = _flagged('actor_arg') | frozenset({
    'getrelationshiprank', 'isdetectedby', 'ishostiletoactor',
    'setrelationshiprank',
})

# Functions that can ONLY be called on Actor (not ObjectReference)
# Used to infer correct property type for callers
_ACTOR_ONLY_FUNCTIONS = _flagged('actor_only') | frozenset({
    'drawweapon', 'getalpha', 'getclass', 'getdeadcount',
    'getgoldamount', 'getincombat', 'getsitstate', 'getsleepstate',
    'getweapondrawn', 'haslos', 'isequipped', 'isinfaction',
    'isspelltarget', 'pathtoref', 'setalpha', 'setcell', 'setessential',
    'setopacity', 'setplayerteammate', 'setrace',
    'setrelationshiprank', 'sheatheweapon', 'startconversation',
})

# Every TES4 command whose FIRST argument is an actor-value name.  Used both to
# quote that argument and to detect calls naming a removed attribute.
_ACTOR_VALUE_FUNCTIONS = _flagged('av') | frozenset({
    'advancepcskill',
})

# The subset that READS an actor value, so a call naming a removed attribute
# has to yield a value.  The rest write, and are dropped instead.
_ACTOR_VALUE_READ_FUNCTIONS = _flagged('av_read')

# TES4 functions that are boolean (return 0/1) and can be used as bare checks
_BARE_BOOL_FUNCTIONS = _flagged('bare_bool') | frozenset({
    'is3dloaded',
})

_BARE_NO_EQUIV_COMMANDS = _flagged('bare_no_equiv') | frozenset({
    'con_runmemorypass', 'emcgetplaylist', 'emcisbattleoverridden',
    'emcismusiconhold', 'emcmusicnexttrack', 'emcmusicresume',
    'emcmusicstop', 'emcplaytrack', 'emcsetbattleoverride',
    'emcsetmusichold', 'emcsetmusictype', 'getmenufloatvalue',
    'getmenuhastrait', 'getmenustringvalue', 'streammusic',
})

# Commands the branch chain in `_emit_function` handles by name but that no
# other table lists.  Derived by reading that chain rather than kept by hand,
# because a hand-kept list drifts from it: these twelve were missing, so the
# node path judged them unknown and emitted `;TODO:` over lines the string
# path converted correctly (`setforcerun 1` becomes the SpeedMult write --
# 62 statements in Oblivion.esm alone).  `foreach` is deliberately absent: it
# is a STATEMENT keyword intercepted before the command layer, and listing it
# here would let a bare `foreach` be treated as a call.
_BRANCH_ONLY_COMMANDS = _flagged('branch_only') | frozenset({
    'setgamesetting', 'setnumericgamesetting',
    'setnumericgamesettingfloat',
})

# TES4 functions returning 0/1 that are collapsed in a COMPARISON position:
# `X == 1` is `X`, `X == 0` is `!X`.  Papyrus rejects Bool-vs-Int, so a literal
# conversion of the TES4 idiom does not compile.
#
# 🛑 This is a SECOND list, and that is a known defect (docs/script_conversion_
# bugs.md #6): it and `_BARE_BOOL_FUNCTIONS` agree on only 10 of 45 names, so
# whether a call collapses depends on which list happens to name it.  Merging
# them changes 3,577 sites across 1,944 scripts, so it is deliberately deferred
# until the parse-tree rewrite is verified -- at which point it is one edit
# here and the diff is attributable.  Until then BOTH are the current
# behaviour, and `_BOOL_VALUED_FUNCTIONS` below is what the emitter reads.
_COMPARISON_BOOL_FUNCTIONS = _flagged('cmp_bool') | frozenset({
    'getincell', 'getisclass', 'getiscurrentpackage', 'getisid',
    'getpcisclass', 'gettalkedtopcparam', 'isactionref',
    'isinfaction', 'isowner',
})

_OBJREF_IMPLICIT_SELF_FUNCTIONS = _flagged('objref_self') | frozenset({
    'getbaseobject', 'is3dloaded', 'isdeleted', 'playanimation',
    'playgroup', 'setactorowner', 'setangle', 'setpos',
})

# Functions that exist on ObjectReference (not truly Actor-only).
# These should NOT trigger type promotion from ObjectReference→Actor
# because they can be called on ObjectReference refs legally.
_OBJREF_SHARED_FUNCTIONS = _flagged('objref_shared') | frozenset({
    'getalpha', 'setalpha', 'setcell',
})

# TES4 `ref.` commands that take NO arguments.  Oblivion let the receiver be
# written after a comma instead of a dot — `StopCombat, Player` and
# `IsInCombat, Player == 1` mean exactly `Player.StopCombat` /
# `Player.IsInCombat`.  Because the generic comma-stripping treats whatever
# follows as an argument, these emitted `IsInCombat(Player)` ("function takes 0
# parameters not 1") or dropped the token and acted on the wrong actor, so the
# receiver has to be promoted for precisely this set.
# Derived from the `ref.` rows with an empty argument column in
# docs/reference/skyrim_commands.md, intersected with FUNCTION_MAP.  (IsInCombat's
# "Integer" column there is its RETURN type, not a parameter.)
_ZERO_ARG_REF_FUNCTIONS = _flagged('zero_arg') | frozenset({
    'getalarmed', 'getdisease', 'getwantblocking', 'isactor',
    'ismoving', 'isturning',
})

#: What `emit/expr.py` reads: every name either list above calls boolean.
_BOOL_VALUED_FUNCTIONS = _BARE_BOOL_FUNCTIONS | _COMPARISON_BOOL_FUNCTIONS

#: TES4 block types whose body becomes the OnUpdate poll.
POLL_BLOCKS = ('gamemode', 'scripteffectupdate')

#: TES4 block type -> the Papyrus combat-state test its filter stood for.
COMBAT_STATE_GUARDS = {'onalarm': 'aeCombatState != 0',
                       'onstartcombat': 'aeCombatState == 1'}

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
