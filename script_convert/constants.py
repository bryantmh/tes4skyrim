"""Constant lookup tables for TES4→Papyrus script conversion."""

import hashlib
import os
import re

# ===========================================================================
# Constants
# ===========================================================================

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
    'onhit':              ('Event OnHit(ObjectReference akAggressor, Form akSource, Projectile akProjectile, bool abPowerAttack, bool abSneakAttack, bool abBashAttack, bool abHitBlocked)', 'EndEvent'),
    'onhitwith':          ('Event OnHit(ObjectReference akAggressor, Form akSource, Projectile akProjectile, bool abPowerAttack, bool abSneakAttack, bool abBashAttack, bool abHitBlocked)', 'EndEvent'),
    'onload':             ('Event OnLoad()', 'EndEvent'),
    'onreset':            ('Event OnReset()', 'EndEvent'),
    'onsell':             ('Event OnSell(Actor akSeller)', 'EndEvent'),
    'ontrigger':          ('Event OnTriggerEnter(ObjectReference akActionRef)', 'EndEvent'),
    'ontriggerenter':     ('Event OnTriggerEnter(ObjectReference akActionRef)', 'EndEvent'),
    'ontriggerleave':     ('Event OnTriggerLeave(ObjectReference akActionRef)', 'EndEvent'),
    'onmagiceffectapply': ('Event OnMagicEffectApply(ObjectReference akCaster, MagicEffect akEffect)', 'EndEvent'),
    'oninit':             ('Event OnInit()', 'EndEvent'),
    'onpackagestart':     ('Event OnPackageStart(Package akNewPackage)', 'EndEvent'),
    'onpackagedone':      ('Event OnPackageEnd(Package akOldPackage)', 'EndEvent'),
    'onpackageend':       ('Event OnPackageEnd(Package akOldPackage)', 'EndEvent'),
    'onpackagechange':    ('Event OnPackageChange(Package akOldPackage)', 'EndEvent'),
    'ontriggeractor':     ('Event OnTriggerEnter(ObjectReference akActionRef)', 'EndEvent'),
    'ontriggermob':       ('Event OnTriggerEnter(ObjectReference akActionRef)', 'EndEvent'),
    'onmagiceffecthit':   ('Event OnMagicEffectApply(ObjectReference akCaster, MagicEffect akEffect)', 'EndEvent'),
    'onactorequip':       ('Event OnEquipped(Actor akActor)', 'EndEvent'),
    'onalarm':            (';TODO: No Papyrus equivalent for OnAlarm', ''),
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
}

# Actor value name mapping (TES4 -> TES5)
ACTOR_VALUE_MAP = {
    'strength':     'UnarmedDamage',
    'intelligence': 'Magicka',
    'willpower':    'MagickaRate',
    'agility':      'SpeedMult',
    'speed':        'SpeedMult',
    'endurance':    'HealRate',
    'personality':  'Speechcraft',
    'luck':         'LuckModifier',
    'armorer':      'Smithing',
    'athletics':    'Stamina',
    'blade':        'OneHanded',
    'block':        'Block',
    'blunt':        'TwoHanded',
    'handtohand':   'UnarmedDamage',
    'heavyarmor':   'HeavyArmor',
    'alchemy':      'Alchemy',
    'alteration':   'Alteration',
    'conjuration':  'Conjuration',
    'destruction':  'Destruction',
    'illusion':     'Illusion',
    'mysticism':    'Alteration',
    'restoration':  'Restoration',
    'acrobatics':   'SpeedMult',
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
    'silencearea':  'MuteModifier',
    'resistfire':   'FireResist',
    'resistfrost':  'FrostResist',
    'resistshock':  'ElectricResist',
    'resistmagic':  'MagicResist',
    'resistdisease':'DiseaseResist',
    'resistpoison': 'PoisonResist',
    'resistnormalweapons': 'DamageResist',
    'aggression':   'Aggression',
    'confidence':   'Confidence',
    'energy':       'Magicka',
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

    Read from a checked-in list (generated by tools/gen_papyrus_reserved.py from
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


# Papyrus reserved words — cannot be used as property names
_PAPYRUS_RESERVED = {
    'self', 'parent', 'as', 'is', 'new', 'return', 'if', 'else', 'elseif',
    'endif', 'while', 'endwhile', 'function', 'endfunction', 'event',
    'endevent', 'property', 'endproperty', 'state', 'endstate', 'auto',
    'autoreadonly', 'import', 'extends', 'native', 'global', 'hidden',
    'conditional', 'int', 'float', 'bool', 'string', 'none', 'true', 'false',
    'length', 'scriptname', 'next',
} | _load_papyrus_script_names()

# Comprehensive function mapping
# key: lowercased oblivion function name
# value: (papyrus_expression, needs_self, note_or_none)
FUNCTION_MAP = {
    # --- Actor Values ---
    'getactorvalue':     ('GetActorValue',     True,  None),
    'setactorvalue':     ('SetActorValue',     True,  None),
    'modactorvalue':     ('ModActorValue',     True,  None),
    'forceactorvalue':   ('ForceActorValue',   True,  None),
    'getav':             ('GetActorValue',     True,  None),
    'setav':             ('SetActorValue',     True,  None),
    'modav':             ('ModActorValue',     True,  None),
    'forceav':           ('ForceActorValue',   True,  None),
    'getbaseactorvalue': ('GetBaseActorValue', True,  None),
    'getbaseav':         ('GetBaseActorValue', True,  None),

    # --- Items / Inventory ---
    'additem':           ('AddItem',           True,  None),
    'removeitem':        ('RemoveItem',        True,  None),
    'getitemcount':      ('GetItemCount',      True,  None),
    'equipitem':         ('EquipItem',         True,  None),
    'unequipitem':       ('UnequipItem',       True,  None),
    'removeallitems':    ('RemoveAllItems',    True,  None),
    'getnumitems':       ('GetNumItems',       True,  None),
    'getinventoryobject':('GetNthForm',        True,  None),
    'drop':              ('DropObject',        True,  None),

    # --- Spells ---
    'addspell':          ('AddSpell',          True,  None),
    'removespell':       ('RemoveSpell',       True,  None),
    'hasspell':          ('HasSpell',          True,  None),
    'cast':              ('Cast',              True,  None),
    'dispel':            ('DispelSpell',       True,  None),
    'dispelspell':       ('DispelSpell',       True,  None),
    'dispelallspells':   ('DispelAllSpells',   True,  None),
    'getspellcount':     (None,                True,  None),  # no-op
    'getnthspell':       (None,                True,  None),  # no-op

    # --- Movement / Position ---
    'moveto':            ('MoveTo',            True,  None),
    'getdistance':       ('GetDistance',       True,  None),
    'getparentcell':     ('GetParentCell',     True,  None),
    'setposition':       ('SetPosition',       True,  None),
    'getlinkedref':      ('GetLinkedRef',      True,  None),
    'getheadingangle':   ('GetHeadingAngle',   True,  None),
    'pathtoref':         (None,                True,  None),  # no-op

    # --- Enable / Disable ---
    'enable':            ('Enable',            True,  None),
    'disable':           ('Disable',           True,  None),
    'isenabled':         ('IsEnabled',         True,  None),
    'activate':          ('Activate',          True,  None),
    'delete':            ('Delete',            True,  None),
    'markfordelete':     ('Delete',            True,  None),
    'placeatme':         ('PlaceAtMe',         True,  None),
    # TES4 SetDestroyed marks the ref destroyed but keeps it VISIBLE (the
    # tripwire stays as a snapped rope).  Disable() made objects vanish on
    # trigger; BlockActivation just stops further use like TES4 intended.
    'setdestroyed': ('BlockActivation',     True,  None),

    # --- Actor State ---
    'kill':              ('Kill',              True,  None),
    'killandresurrect':  ('Kill',              True,  None),  # then Resurrect manually
    'resurrect':         ('Resurrect',         True,  None),
    'getdead':           ('IsDead',            True,  None),
    'isdead':            ('IsDead',            True,  None),
    'isincombat':        ('IsInCombat',        True,  None),
    'startcombat':       ('StartCombat',       True,  None),
    'stopcombat':        ('StopCombat',        True,  None),
    'getisid':           (None,                True,  None),  # Special handler in _emit_function
    'getisrace':         (None,                True,  None),  # Special handler in _emit_function
    'isactordetected':   ('IsDetectedBy',      True,  None),
    'getdetected':       ('IsDetectedBy',      True,  None),
    'getincell':         (None,                True,  None),  # Special handler in _emit_function
    'getinsamecell':     (None,                True,  None),  # Special handler in _emit_function
    'getissex':          (None,                True,  None),  # Special handler
    'issneaking':        ('IsSneaking',        True,  None),
    'isweaponout':       ('IsWeaponDrawn',     True,  None),
    'isswimming':        (None,                True,  None),  # Special handler
    'getsitting':        ('GetSitState',       True,  None),
    'getsleeping':       ('GetSleepState',     True,  None),
    'getequipped':       ('IsEquipped',        True,  None),
    'getweaponanimtype': ('GetEquippedItemType', True, None),
    'clearlookat':       ('ClearLookAt',       True,  None),
    'getisalerted':      (None,                True,  None),  # Special handler
    'setessential':      (None,                False, None),  # Special handler
    'getisplayablerace': (None,                True,  None),  # Special handler
    'istalking':         ('IsInDialogueWithPlayer', True, None),
    'setunconscious':    ('SetUnconscious',    True,  None),
    'setghost':          ('SetGhost',          True,  None),
    'isghost':           ('IsGhost',           True,  None),
    'setcrimegold':      (None,                False, None),  # Special handler
    'getcrimegold':      (None,                False, None),  # Special handler
    'modcrimegold':      (None,                False, None),  # Special handler
    'setalert':          (None,                True,  None),  # Special handler
    'resetai':           ('ResetAI',           True,  None),

    # --- Factions ---
    'getinfaction':      ('IsInFaction',       True,  None),
    'getfactionrank':    ('GetFactionRank',    True,  None),
    'setfactionrank':    ('SetFactionRank',    True,  None),
    'modfactionrank':    ('ModFactionRank',    True,  None),
    'addfaction':        ('AddToFaction',      True,  None),
    'removefaction':     ('RemoveFromFaction',  True,  None),
    'removefromfaction': ('RemoveFromFaction',  True,  None),

    # --- AI ---
    'evp':               ('EvaluatePackage',   True,  None),
    'evaluatepackage':   ('EvaluatePackage',   True,  None),
    'setforcerun':       ('SetDontMove',       True,  None),
    'setforcewalk':      (None,                True,  None),  # no-op
    'wait':              (None,                False, None),  # Special handler

    # --- Quest ---
    'setstage':          ('SetStage',          False, None),
    'getstage':          ('GetStage',          False, None),
    'getstagedone':      ('GetStageDone',      False, None),
    'startquest':        ('Start',             False, None),
    'stopquest':         ('Stop',              False, None),
    'getquestrunning':   ('IsRunning',         False, None),
    'setquestobject':    (None,                False, None),  # Special handler (no-op)
    'isquestcompleted':  ('IsCompleted',       False, None),
    'completequest':     ('CompleteQuest',      False, None),

    # --- UI / Messages ---
    'message':           ('Debug.Notification', False, None),
    'messagebox':        ('Debug.MessageBox',   False, None),
    'showmessage':       ('Debug.MessageBox',   False, None),
    'getbuttonpressed':  (None,                False, None),  # Special handler

    # --- Game State ---
    'getgamesetting':    ('Game.GetGameSettingFloat', False, None),
    'getgs':             ('Game.GetGameSettingFloat', False, None),
    'getpcissex':        (None,                 False, None),  # Special handler in _emit_function
    'getpcinfaction':    ('Game.GetPlayer().IsInFaction', False, None),
    'ispcrace':          (None,                False, None),  # Special handler
    'getrandompercent':  ('Utility.RandomInt',  False, None),
    'getamountsoldstolen': ('Game.QueryStat',  False, None),
    'showracemenu':      ('Game.ShowRaceMenu', False, None),
    'showdialogsubtitles':(None,               False, None),  # Special handler (no-op)
    'getlevel':          ('GetLevel',           True,  None),
    # 'isininterior' handled by special handler in _emit_function
    'getcurrentgametime':('Utility.GetCurrentGameTime', False, None),
    'getdayofweek':      (None,                False, None),  # Special handler
    'getcurrenttime':    ('Utility.GetCurrentGameTime', False, None),
    'getsecondspassed':  (None,                False, None),  # Special: replaced inline
    'isplayerinprison':  (None,                False, None),  # Special handler
    'getplayerinjail':   (None,                False, None),  # Special handler
    'getgameloaded':     (None,                False, None),  # no-op

    # --- Sound ---
    'playsound':         (None,                False, None),  # Special handler
    'playsound3d':       (None,                False, None),  # Special handler
    'stopsound':         (None,                False, None),  # Special handler

    # --- Animation ---
    'playgroup':         (None,                True,  None),  # Special handler
    'lookismile':        (None,                True,  None),  # no-op
    'lookat':            ('SetLookAt',         True,  None),
    'stoplook':          ('ClearLookAt',       True,  None),

    # --- Misc ---
    'getself':           (None,                False, None),  # Special: replaced with Self
    'getcontainer':      ('GetContainer',      True,  None),
    'getparentref':      ('GetLinkedRef',      True,  None),
    'showmap':           (None,                False, None),  # Special handler
    'lock':              ('Lock',              True,  None),
    'unlock':            ('Lock',              True,  None),  # handled by special handler below
    'getlocked':         ('IsLocked',          True,  None),
    'getlocklevel':      ('GetLockLevel',      True,  None),
    'setownership':      ('SetActorOwner',     True,  None),  # handled by special handler above
    'getownership':      (None,                False, None),  # no-op
    'setscale':          ('SetScale',          True,  None),
    'getscale':          ('GetScale',          True,  None),
    'purgecellbuffers':  (None,                False, None),  # Special handler (no-op)
    'pcb':               (None,                False, None),  # Special handler (no-op)
    'closeobliviongate': (None,                False, None),  # Special handler (no-op)
    'say':               ('Say',               True,  None),
    'reset3dstate':      (None,                False, None),  # Special handler
    'setactorsai':       (None,                True,  None),  # Special handler
    'addtopic':          (None,                False, None),  # Special handler (no-op)
    'setcellpublicflag': (None,                True,  None),  # Special handler (no-op)
    'moddisposition':    (None,                True,  None),  # Special handler
    'getdisposition':    (None,                True,  None),  # Special handler
    'setfactionreaction':('SetReaction',       False, None),
    'modfactionreaction':('ModReaction',       False, None),
    'isactionref':       (None,                False, None),  # Special: compare akActionRef
    'getactionref':      (None,                False, None),  # Special: returns akActionRef
    'iscurrentfurnitureref': (None,            True,  None),  # no-op
    'iscurrentfurnitureobj': (None,            True,  None),  # no-op
    'showenchantment':   (None,                False, None),  # no-op
    'triggerscreenblood': ('Game.TriggerScreenBlood', False,  None),
    'isonguard':         (None,                True,  None),  # no-op
    'setactorfullname':  (None,                True,  None),  # Special handler
    'setcellfullname':   (None,                True,  None),  # Special handler (no-op)
    'respawnhorse':      (None,                True,  None),  # no-op
    'setdoordisabletakeoff':(None,             True,  None),  # no-op
    'setdoordefaultopen':('SetOpen',           True,  None),
    'opendoor':          ('SetOpen',           True,  None),
    'closedoor':         ('SetOpen',           True,  None),
    'setweather': (None,                  False,  None),  # Special handler
    'sw': (None,                  False,  None),  # Special handler
    'forceweather': (None,                  False,  None),  # Special handler
    'fw': (None,                  False,  None),  # Special handler
    'releaseweatheroverride': (None,                  False,  None),  # Special handler
    'getbookread':       (None,                True,  None),  # Special handler
    'removeme':          ('Delete',            True,  None),

    # --- Object state ---
    'getisref':          (None,                True,  None),  # Special handler
    'hasvariable':       (None,                False, None),  # no-op
    'setdisabled':       ('Disable',           True,  None),
    'setenabled':        ('Enable',            True,  None),
    'getis3dloaded':     ('Is3DLoaded',        True,  None),
    'hasbeenpickedup':   (None,                True,  None),  # no-op

    # --- Weather ---
    'getweatherpercent': (None,                False, None),  # Special handler
    'forceweather': (None,                  False,  None),  # Special handler
    'releaseweatheroverride': (None,                  False,  None),  # Special handler

    # --- Special compound player.X ---
    'player.additem':    ('Game.GetPlayer().AddItem', False, None),
    'player.removeitem': ('Game.GetPlayer().RemoveItem', False, None),
    'player.getitemcount': ('Game.GetPlayer().GetItemCount', False, None),
    'player.addspell':   ('Game.GetPlayer().AddSpell', False, None),
    'player.removespell':('Game.GetPlayer().RemoveSpell', False, None),
    'player.moveto':     ('Game.GetPlayer().MoveTo', False, None),
    'player.placeatme':  ('Game.GetPlayer().PlaceAtMe', False, None),

    # --- Additional Actor/Combat ---
    'addscriptpackage':  ('EvaluatePackage',   True,  None),
    'removescriptpackage': ('EvaluatePackage', True,  None),
    'startconversation': (None,                True,  None),  # Special handler
    'getiscurrentpackage': (None,              True,  None),  # no-op
    'pickidle':          (None,                True,  None),  # Special handler in _emit_function
    'playidle':          (None,                True,  None),  # Special handler in _emit_function
    'isanimplaying':     (None,                True,  None),  # returns 0
    'getcombattarget':   ('GetCombatTarget',   True,  None),
    'isdisabled':        ('IsDisabled',        True,  None),
    'getparentcellowner':('GetParentCell',     True,  None),
    'hasmagiceffect':    ('HasMagicEffect',    True,  None),
    'isexpelled':        (None,                False, None),  # Special handler (ispcexpelled)
    'getdeadcount':      ('GetDeadCount',      True,  None),
    'getcurrentpackage': (None,                True,  None),  # no-op
    'setopendoor':       ('SetOpen',           True,  None),

    # --- Player state ---
    'getplayerinseworld': (None,               False, None),  # Special handler
    'getpcfactionmurder':(None,                False, None),  # Special handler
    'setpcfactionmurder':(None,                False, None),  # Special handler
    'getpcfactionattack':(None,                False, None),  # Special handler
    'setpcfactionattack':(None,                False, None),  # Special handler
    'getpcfactionsteal': (None,                False, None),  # Special handler
    'setpcfactionsteal': (None,                False, None),  # Special handler
    'getinworldspace':   (None,                False, None),  # Special handler
    'getiscurrentweather':(None,               False, None),  # Special handler
    'getisreference':    (None,                False, None),  # Special handler
    'senttojail':        (None,                False, None),  # Special handler
    'isplayersleeping':  (None,                False, None),  # Special handler
    'disableplayercontrols': ('Game.DisablePlayerControls', False, None),
    'enableplayercontrols': ('Game.EnablePlayerControls', False, None),
    'enablefasttravel': ('Game.EnableFastTravel', False,  None),
    'playbink':          (None,                False, None),  # no-op
    'sendtrespassalarm': (None,               True,  None),  # no-op
    'getpcisrace':       (None,                False, None),  # Special handler
    'getinfame':         (None,                False, None),  # Special handler
    'getpcinfamy':       (None,                False, None),  # Special handler
    'getpcfame':         (None,                False, None),  # Special handler

    # --- AI/Package ---
    'setforcesneak':     (None,                True,  None),  # Special handler
    'getisalerted':      (None,                True,  None),  # no-op
    'setalert':          (None,                True,  None),  # Special handler

    # --- Object Interaction ---
    'getcontainer':      ('GetContainer',      True,  None),
    'opencurrentcontainer': (None,             True,  None),  # no-op
    'removeallitems':    ('RemoveAllItems',    True,  None),
    'getdisabled':       ('IsDisabled',        True,  None),
    'attachashpile':     (None,                True,  None),  # no-op
    'setsize':           ('SetScale',          True,  None),
    'getsize':           ('GetScale',          True,  None),

    # --- Cell/Location ---
    'getincell':         (None,                True,  None),  # Special handler
    # 'isininterior' handled by special handler in _emit_function
    'getinsamecellas':   (None,                True,  None),  # Special handler

    # --- Faction/Crime ---
    'ispcexpelled':      (None,                False, None),  # Special handler in _emit_function
    'getpcexpelled':     (None,                False, None),  # Special handler in _emit_function
    'setpcexpelled':     (None,                False, None),  # Special handler in _emit_function
    'payfinethief':      (None,                False, None),  # Special handler
    'payfine':           (None,                False, None),  # Special handler
    'gotojail':          (None,                False, None),  # Special handler
    'addachievement':    (None,                False, None),  # Special handler (no-op)
    'modpcfame':         (None,                False, None),  # Special handler
    'modpcinfamy':       (None,                False, None),  # Special handler
    'getpcfame':         (None,                False, None),  # Special handler
    'getpcinfamy':       (None,                False, None),  # Special handler
    'getinfame':         (None,                True,  None),  # Special handler

    # --- Dialog/Topic ---
    'refreshtopiclist':  (None,                False, None),  # Special handler (no-op)
    'saycustom':         ('Say',               True,  None),

    # --- Look/Perception ---
    'look':              ('SetLookAt',         True,  None),
    'stoplooking':       ('ClearLookAt',       True,  None),

    # --- Display/Name ---
    'getdisplayname':    ('GetDisplayName',    True,  None),
    'getname':           ('GetDisplayName',    True,  None),

    # --- Travel ---
    'movetomyeditorlocation': ('MoveToMyEditorLocation', True, None),
    'moveto':            ('MoveTo',            True,  None),
    'movetomarker':      ('MoveTo',            True,  None),

    # --- Path/Linked Points ---
    'enablelinkedpathpoints':  (None,          True,  None),  # Special handler (no-op)
    'disablelinkedpathpoints': (None,          True,  None),  # Special handler (no-op)

    # --- Shader/Visual Effects ---
    'pms':               (None,                True,  None),  # Special handler
    'sms':               (None,                True,  None),  # Special handler
    'playmagicshadervisuals':  (None,          True,  None),  # Special handler
    'stopmagicshadervisuals':  (None,          True,  None),  # Special handler
    'playmagiceffectvisuals':  (None,          True,  None),  # Special handler
    'stopmagiceffectvisuals':  (None,          True,  None),  # Special handler
    'pme':               (None,                True,  None),  # Special handler
    'sme':               (None,                True,  None),  # Special handler
    'triggerhitshader':  (None,                True,  None),  # Special handler
    'scaonactor':        (None,                True,  None),  # Special handler
    'sca':               (None,                True,  None),  # Special handler

    # --- AI/Wait ---
    'stopwaiting':       ('EvaluatePackage',   True,  None),
    'setcombatstyle':    (None,                True,  None),  # Special handler (no-op)
    'setignorefriendlyhits': (None,            True,  None),  # no-op
    'sayto':             ('Say',               True,  None),

    # --- Detection ---
    'getdetectionlevel': (None,                True,  None),  # Special handler

    # --- Door/Object State ---
    'setopenstate':      ('SetOpen',           True,  None),
    'resetinterior':     (None,                True,  None),  # Special handler

    # --- Player Skill/Misc ---
    'modpcskill': ('Game.AdvanceSkill',   False,  None),
    'modpcmiscstat': ('Game.IncrementStat',  False,  None),
    'getpcmiscstat': ('Game.QueryStat',      False,  None),

    # --- Trap/Custom functions that are quest-specific ---
    'trapupdate':        (None,                True,  None),  # Special handler (no-op)

    # --- Gold ---
    'getgold':           ('GetGoldAmount',     True,  None),

    # --- Alpha ---
    'saa':               ('SetAlpha',          True,  None),
    'setactoralpha':     ('SetAlpha',          True,  None),
    'gaa':               ('GetAlpha',          True,  None),
    'getactoralpha':     ('GetAlpha',          True,  None),

    # --- Interior ---
    # 'isininterior' handled by special handler in _emit_function

    # --- Save ---
    'autosave':          ('Game.RequestAutoSave', False, None),

    # --- Misc unmapped ---
    'modamountsoldstolen':(None,               False, None),  # Special handler
    'setcellownership':  (None,                False, None),  # Special handler (no-op)
    'setpublic':         (None,                False, None),  # no-op
    'closeCurrentOblivionGate': (None,         False, None),  # Special handler
    'setshowquestitems': (None,                False, None),  # no-op
    'setnorumors':       (None,                False, None),  # no-op
    'setsceneiscomplex': (None,                False, None),  # no-op
    'setdisplayname':    ('SetDisplayName',    True,  None),
    'setpackduration':   (None,                False, None),  # no-op
    'showbirthsignmenu': (None,                False, None),  # Special handler
    'isspelltarget':     (None,                True,  None),  # no-op
    'getarmorrating':    (None,                True,  None),  # no-op
    'isidleplaying':     (None,                True,  None),  # no-op
    'getopenstate':      ('GetOpenState',      True,  None),
    'getstartingpos':    (None,                True,  None),  # no-op
    'getcurrentaiprocedure': (None,            True,  None),  # no-op
    'getcurrentaipackage': (None,              True,  None),  # no-op
    'isessential':       ('IsEssential',       True,  None),
    'getlos':            ('HasLOS',            True,  None),
    'isactor':           (None,                True,  None),  # no-op
    'israining':         (None,                False, None),  # no-op
    'isindangerouswater':(None,                True,  None),  # no-op
    'getplayercontrolsdisabled': (None,        False, None),  # no-op
    'getisplayerbirthsign': (None,             False, None),  # no-op
    'isplayerinjail':    (None,                False, None),  # Special handler
    'getpcfactionattack':(None,                False, None),  # Special handler
    'getpcfactionsteal': (None,                False, None),  # Special handler
    'ispcanmurderer':    (None,                False, None),  # Special handler
    'ispcamurderer':     (None,                False, None),  # Special handler
    'getpcismurderer':   (None,                False, None),  # Special handler
    'isowner':           ('IsInFaction',        True,  None),
    'gettalkedtopc':     (None,                False, None),  # no-op
    'gettalkedtopcp':    (None,                False, None),  # no-op
    'menumode':          (None,                False, None),  # no-op
    'istimepassing':     (None,                False, None),  # no-op
    'expel':             (None,                True,  None),  # Special handler
    'setitemvalue':      (None,                True,  None),  # no-op
    'setnoavoidance':    (None,                True,  None),  # no-op
    'offerhorse':        (None,                True,  None),  # no-op
    'setactorrefraction':(None,                True,  None),  # no-op
    'setdisplayname':    (None,                True,  None),  # Special handler
    'getcontainer':      (None,                True,  None),  # Special handler
    'stopcombatalarmonactor': ('StopCombat',   True,  None),
    'essentialdeathreload': (None,             False, None),  # no-op
    'setallreachable':   (None,                True,  None),  # no-op
    'getdestroyed': ('IsDisabled',          True,  ';approximate - GetDestroyed not in Skyrim'),
    'setclass':          (None,                True,  None),  # no-op
    'setdoordefaultopen':(None,                True,  None),  # Special handler
    'setrestrained':     (None,                True,  None),  # Special handler
    'getrestrained':     (None,                True,  None),  # Special handler
    'rotate':            (None,                True,  None),  # Special handler
    'clearownership':    (None,                True,  None),  # Special handler
    'setlevel':          (None,                True,  None),  # no-op
    'showspellmaking':   (None,                False, None),  # no-op
    'setrigidbodymass':  (None,                True,  None),  # no-op
    'resetfalldamagetimer': (None,             True,  None),  # no-op
    'setpcfame':         (None,                False, None),  # Special handler
    'setpcinfamy':       (None,                False, None),  # Special handler
    'forceflee':         (None,                True,  None),  # Special handler
    'setinvestmentgold': (None,                True,  None),  # no-op
    'setallvisible':     (None,                True,  None),  # no-op
    'getpcfame':         (None,                False, None),  # Special handler
    'getpcinfamy':       (None,                False, None),  # Special handler
    'setlookat':         ('SetLookAt',         True,  None),
}


# TES4 functions that are boolean (return 0/1) and can be used as bare checks
_BARE_BOOL_FUNCTIONS = {
    'getdead', 'isdead', 'isincombat', 'issneaking', 'isweaponout',
    'isswimming', 'isghost', 'isenabled', 'isdisabled', 'islocked',
    'getlocked', 'is3dloaded', 'getis3dloaded', 'isininterior',
}

# Functions that can ONLY be called on Actor (not ObjectReference)
# Used to infer correct property type for callers
_ACTOR_ONLY_FUNCTIONS = {
    'startcombat', 'stopcombat', 'getincombat', 'isincombat',
    'getdead', 'isdead', 'kill', 'resurrect',
    'addspell', 'removespell', 'hasspell', 'dispelallspells',
    'additem', 'removeitem', 'getitemcount', 'removeallitems',
    'equipitem', 'unequipitem',
    'getactorvalue', 'setactorvalue', 'modactorvalue', 'forceactorvalue',
    'getav', 'setav', 'modav', 'forceav', 'getbaseactorvalue', 'getbaseav',
    'startconversation', 'setrelationshiprank',
    'getinfaction', 'setfactionrank', 'getfactionrank',
    'modcrimegold', 'setcrimegold', 'getcrimegold',
    'evaluatepackage', 'evp', 'addscriptpackage', 'removescriptpackage', 'stopwaiting',
    'setessential', 'setghost', 'setunconscious',
    'setscale', 'getscale',
    'setforcerun', 'setforcesneak',
    'setrace', 'getrace',
    'getlevel', 'getclass',
    'setplayerteammate', 'pathtoref',
    'getweapondrawn', 'isweaponout',
    'setactoralpha', 'setopacity',
    'issneaking', 'isswimming', 'isghost',
    'say', 'saycustom', 'sayto',
    'getdistance', 'setcell',
    'getsitting', 'getsitstate', 'getsleeping', 'getsleepstate',
    'getequipped', 'isequipped', 'hasmagiceffect',
    'clearlookat', 'stoplook', 'stoplooking', 'setlookat', 'lookat', 'look',
    'getweaponanimtype', 'getdeadcount',
    'getgold', 'getgoldamount', 'saa', 'gaa', 'getactoralpha',
    'resethealth', 'setalpha', 'getalpha', 'getarmorrating',
    'isessential', 'getlos', 'haslos',
    'dispel', 'dispelspell', 'placeatme',
    'drawweapon', 'sheatheweapon', 'isinfaction',
}

# Functions that exist on ObjectReference (not truly Actor-only).
# These should NOT trigger type promotion from ObjectReference→Actor
# because they can be called on ObjectReference refs legally.
_OBJREF_SHARED_FUNCTIONS = {
    'placeatme', 'getdistance', 'additem', 'removeitem', 'getitemcount',
    'removeallitems', 'setscale', 'getscale', 'say', 'saycustom', 'sayto',
    'setalpha', 'getalpha', 'setcell', 'dispel', 'dispelspell',
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
    safe = re.sub(r'[^\w]', '_', name)
    safe = re.sub(r'^\d+', '', safe)
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
    if low in _PAPYRUS_RESERVED:
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

