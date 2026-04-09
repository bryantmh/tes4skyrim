#!/usr/bin/env python3
"""
Oblivion Script -> Papyrus Converter

Converts all TES4 scripts (SCPT, INFO ResultScript, QUST stage scripts) to
compilable Papyrus .psc source files.

Usage:
    python tools/oblivion_to_papyrus.py export/Oblivion.esm -o output/oblivion.esm/scripts/source

Pipeline integration:
    Called from tes5_import or convert.py after export phase.
"""

import argparse
import os
import re
import struct
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Add project root to path for imports
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tes5_import.text_reader import parse_export_file, unescape_value

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
    'scripteffectstart':  ('Event OnEffectStart(MagicEffect akBaseEffect, Float afMagnitude, Float afDuration)', 'EndEvent'),
    'scripteffectfinish': ('Event OnEffectFinish(MagicEffect akBaseEffect)', 'EndEvent'),
    'scripteffectupdate': ('Event OnUpdate()', 'EndEvent'),
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

# Papyrus reserved words — cannot be used as property names
_PAPYRUS_RESERVED = {
    'self', 'parent', 'as', 'is', 'new', 'return', 'if', 'else', 'elseif',
    'endif', 'while', 'endwhile', 'function', 'endfunction', 'event',
    'endevent', 'property', 'endproperty', 'state', 'endstate', 'auto',
    'autoreadonly', 'import', 'extends', 'native', 'global', 'hidden',
    'conditional', 'int', 'float', 'bool', 'string', 'none', 'true', 'false',
    'length', 'scriptname', 'next',
    # Built-in class names that shouldn't be used as variable/property names
    'debug', 'game', 'math', 'utility', 'weather', 'sound',
}

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
    'getinventoryobject':('GetNthForm',        True,  ';TODO: GetInventoryObject->GetNthForm may need Container.Find'),
    'drop':              ('DropObject',        True,  None),

    # --- Spells ---
    'addspell':          ('AddSpell',          True,  None),
    'removespell':       ('RemoveSpell',       True,  None),
    'hasspell':          ('HasSpell',          True,  None),
    'cast':              ('Cast',              True,  None),
    'dispel':            ('DispelSpell',       True,  None),
    'dispelspell':       ('DispelSpell',       True,  None),
    'dispelallspells':   ('DispelAllSpells',   True,  None),
    'getspellcount':     (None,                True,  ';TODO: No Papyrus equivalent for GetSpellCount'),
    'getnthspell':       (None,                True,  ';TODO: No Papyrus equivalent for GetNthSpell'),

    # --- Movement / Position ---
    'moveto':            ('MoveTo',            True,  None),
    'getdistance':       ('GetDistance',       True,  None),
    'getparentcell':     ('GetParentCell',     True,  None),
    'setposition':       ('SetPosition',       True,  None),
    'getlinkedref':      ('GetLinkedRef',      True,  None),
    'getheadingangle':   ('GetHeadingAngle',   True,  None),
    'pathtoref':         (None,                True,  ';TODO: No Papyrus equivalent for PathToRef'),

    # --- Enable / Disable ---
    'enable':            ('Enable',            True,  None),
    'disable':           ('Disable',           True,  None),
    'isenabled':         ('IsEnabled',         True,  None),
    'activate':          ('Activate',          True,  None),
    'delete':            ('Delete',            True,  None),
    'markfordelete':     ('Delete',            True,  None),
    'placeatme':         ('PlaceAtMe',         True,  None),
    'setdestroyed':      (None,                True,  ';TODO: No Papyrus SetDestroyed'),

    # --- Actor State ---
    'kill':              ('Kill',              True,  None),
    'killandresurrect':  ('Kill',              True,  ';TODO: Kill then Resurrect'),
    'resurrect':         ('Resurrect',         True,  None),
    'getdead':           ('IsDead',            True,  None),
    'isdead':            ('IsDead',            True,  None),
    'isincombat':        ('IsInCombat',        True,  None),
    'startcombat':       ('StartCombat',       True,  None),
    'stopcombat':        ('StopCombat',        True,  None),
    'getisid':           (None,                True,  ';TODO: GetIsID -> compare against base form'),
    'getisrace':         (None,                True,  ';TODO: Use ref.GetRace() == raceForm'),
    'isactordetected':   ('IsDetectedBy',      True,  None),
    'getdetected':       ('IsDetectedBy',      True,  None),
    'getincell':         (None,                True,  ';TODO: GetInCell -> compare GetParentCell()'),
    'getinsamecell':     (None,                True,  ';TODO: Compare GetParentCell() on both refs'),
    'getissex':          ('GetActorBase',      True,  ';TODO: GetIsSex -> GetActorBase().GetSex()'),
    'issneaking':        ('IsSneaking',        True,  None),
    'isweaponout':       ('IsWeaponDrawn',     True,  None),
    'isswimming':        (None,                True,  ';TODO: No IsSwimming in Papyrus'),
    'getsitting':        ('GetSitState',       True,  None),
    'getsleeping':       ('GetSleepState',     True,  None),
    'getequipped':       ('IsEquipped',        True,  None),
    'getweaponanimtype': ('GetEquippedItemType', True, ';TODO: Needs hand param: 1=right'),
    'clearlookat':       ('ClearLookAt',       True,  None),
    'getisalerted':      (None,                True,  ';TODO: No Papyrus equivalent for GetIsAlerted'),
    'setessential':      (None,                False, ';TODO: ActorBase.SetEssential() needed'),
    'getisplayablerace': (None,                True,  ';TODO: Check Race.IsPlayable()'),
    'istalking':         ('IsInDialogueWithPlayer', True, None),
    'setunconscious':    ('SetUnconscious',    True,  None),
    'setghost':          ('SetGhost',          True,  None),
    'isghost':           ('IsGhost',           True,  None),
    'setcrimegold':      (None,                False, ';TODO: Needs faction.SetCrimeGold()'),
    'getcrimegold':      (None,                False, ';TODO: Needs faction.GetCrimeGold()'),
    'modcrimegold':      (None,                False, ';TODO: Needs faction.ModCrimeGold()'),
    'setalert':          (None,                True,  ';TODO: No Papyrus equivalent for SetAlert'),
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
    'setforcerun':       ('SetDontMove',       True,  ';TODO: ForceRun not direct equivalent'),
    'setforcewalk':      (None,                True,  ';TODO: No Papyrus equivalent for SetForceWalk'),
    'wait':              (None,                False, ';TODO: Wait (script package) has no Papyrus equivalent'),

    # --- Quest ---
    'setstage':          ('SetStage',          False, None),
    'getstage':          ('GetStage',          False, None),
    'getstagedone':      ('GetStageDone',      False, None),
    'startquest':        ('Start',             False, None),
    'stopquest':         ('Stop',              False, None),
    'getquestrunning':   ('IsRunning',         False, None),
    'setquestobject':    (None,                False, ';TODO: No Papyrus equivalent for SetQuestObject'),
    'isquestcompleted':  ('IsCompleted',       False, None),
    'completequest':     ('CompleteQuest',      False, None),

    # --- UI / Messages ---
    'message':           ('Debug.Notification', False, None),
    'messagebox':        ('Debug.MessageBox',   False, None),
    'showmessage':       ('Debug.MessageBox',   False, None),
    'getbuttonpressed':  (None,                False, ';TODO: Use Message.Show() return value'),

    # --- Game State ---
    'getgamesetting':    ('Game.GetGameSettingFloat', False, None),
    'getgs':             ('Game.GetGameSettingFloat', False, None),
    'getpcissex':        (None,                False, ';TODO: Game.GetPlayer().GetActorBase().GetSex()'),
    'getpcinfaction':    (None,                False, ';TODO: Game.GetPlayer().IsInFaction()'),
    'ispcrace':          (None,                False, ';TODO: Game.GetPlayer().GetRace()'),
    'getrandompercent':  ('Utility.RandomInt',  False, None),
    'getamountsoldstolen': ('Game.QueryStat',  False, ';TODO: Game.QueryStat("Items Stolen")'),
    'showracemenu':      ('Game.ShowRaceMenu', False, None),
    'showdialogsubtitles':(None,               False, ';TODO: INI setting, not scriptable'),
    'getlevel':          ('GetLevel',           True,  None),
    # 'isininterior' handled by special handler in _emit_function
    'getcurrentgametime':('Utility.GetCurrentGameTime', False, None),
    'getdayofweek':      (None,                False, ';TODO: Math.Floor(GameDaysPassed % 7)'),
    'getcurrenttime':    (None,                False, ';TODO: GameHour global value'),
    'getsecondspassed':  (None,                False, None),  # Special: replaced inline
    'isplayerinprison':  (None,                False, ';TODO: No direct equivalent'),
    'getplayerinjail':   (None,                False, ';TODO: No direct equivalent'),
    'getgameloaded':     (None,                False, ';TODO: Use OnPlayerLoadGame event'),

    # --- Sound ---
    'playsound':         (None,                False, ';TODO: Needs Sound form Play()'),
    'playsound3d':       (None,                False, ';TODO: Needs Sound form Play()'),
    'stopsound':         (None,                False, ';TODO: No direct StopSound'),

    # --- Animation ---
    'playgroup':         (None,                True,  ';TODO: Use Debug.SendAnimationEvent()'),
    'lookismile':        (None,                True,  ';TODO: No equivalent'),
    'lookat':            ('SetLookAt',         True,  None),
    'stoplook':          ('ClearLookAt',       True,  None),

    # --- Misc ---
    'getself':           (None,                False, None),  # Special: replaced with Self
    'getcontainer':      ('GetContainer',      True,  None),
    'getparentref':      ('GetLinkedRef',      True,  ';TODO: GetParentRef -> GetLinkedRef may differ'),
    'showmap':           (None,                False, ';TODO: No Papyrus equivalent for ShowMap'),
    'lock':              ('Lock',              True,  None),
    'unlock':            ('Lock',              True,  None),  # handled by special handler below
    'getlocked':         ('IsLocked',          True,  None),
    'getlocklevel':      ('GetLockLevel',      True,  None),
    'setownership':      ('SetActorOwner',     True,  None),  # handled by special handler above
    'getownership':      (None,                False, ';TODO: No direct equivalent'),
    'setscale':          ('SetScale',          True,  None),
    'getscale':          ('GetScale',          True,  None),
    'purgecellbuffers':  (None,                False, ';TODO: No Papyrus equivalent for PurgeCellBuffers'),
    'pcb':               (None,                False, ';TODO: No Papyrus equivalent for PCB'),
    'closeobliviongate': (None,                False, ';TODO: Oblivion-specific: CloseOblivionGate'),
    'say':               ('Say',               True,  ';TODO: Say() needs Topic form'),
    'reset3dstate':      (None,                False, ';TODO: No Papyrus equivalent'),
    'setactorsai':       (None,                True,  ';TODO: No Papyrus equivalent for SetActorsAI'),
    'addtopic':          (None,                False, ';TODO: No Papyrus equivalent for AddTopic'),
    'setcellpublicflag': (None,                True,  ';TODO: No Papyrus equivalent'),
    'moddisposition':    (None,                True,  ';TODO: Disposition system removed in Skyrim'),
    'getdisposition':    (None,                True,  ';TODO: Disposition system removed'),
    'setfactionreaction':('SetReaction',       False, ';TODO: faction1.SetReaction(faction2, val)'),
    'modfactionreaction':('ModReaction',       False, ';TODO: faction1.ModReaction(faction2, val)'),
    'isactionref':       (None,                False, None),  # Special: compare akActionRef
    'getactionref':      (None,                False, None),  # Special: returns akActionRef
    'iscurrentfurnitureref': (None,            True,  ';TODO: No direct equivalent'),
    'iscurrentfurnitureobj': (None,            True,  ';TODO: No direct equivalent'),
    'showenchantment':   (None,                False, ';TODO: No Papyrus equivalent'),
    'triggerscreenblood':(None,                False, ';TODO: Use Game.TriggerScreenBlood()'),
    'isonguard':         (None,                True,  ';TODO: No direct equivalent'),
    'setactorfullname':  (None,                True,  ';TODO: SKSE required - SetDisplayName'),
    'setcellfullname':   (None,                True,  ';TODO: No Papyrus equivalent'),
    'respawnhorse':      (None,                True,  ';TODO: No direct equivalent'),
    'setdoordisabletakeoff':(None,             True,  ';TODO: No equivalent'),
    'setdoordefaultopen':('SetOpen',           True,  None),
    'opendoor':          ('SetOpen',           True,  None),
    'closedoor':         ('SetOpen',           True,  ';TODO: SetOpen(false)'),
    'setweather':        (None,                False, ';TODO: Weather.ForceActive()'),
    'sw':                (None,                False, ';TODO: SetWeather -> Weather.ForceActive()'),
    'forceweather':      (None,                False, ';TODO: Weather.ForceActive()'),
    'fw':                (None,                False, ';TODO: ForceWeather -> Weather.ForceActive()'),
    'releaseweatheroverride':(None,            False, ';TODO: Weather.ReleaseOverride()'),
    'getbookread':       (None,                True,  ';TODO: No direct equivalent for GetBookRead'),
    'removeme':          ('Delete',            True,  ';TODO: RemoveMe->Delete (item from container)'),

    # --- Object state ---
    'getisref':          (None,                True,  ';TODO: Compare references directly'),
    'hasvariable':       (None,                False, ';TODO: No Papyrus equivalent'),
    'setdisabled':       ('Disable',           True,  None),
    'setenabled':        ('Enable',            True,  None),
    'getis3dloaded':     ('Is3DLoaded',        True,  None),
    'hasbeenpickedup':   (None,                True,  ';TODO: No direct equivalent'),

    # --- Weather ---
    'getweatherpercent': (None,                False, ';TODO: Weather.GetClassification()'),
    'forceweather':      (None,                False, ';TODO: Weather.ForceActive()'),
    'releaseweatheroverride': (None,           False, ';TODO: Weather.ReleaseOverride()'),

    # --- Special compound player.X ---
    'player.additem':    ('Game.GetPlayer().AddItem', False, None),
    'player.removeitem': ('Game.GetPlayer().RemoveItem', False, None),
    'player.getitemcount': ('Game.GetPlayer().GetItemCount', False, None),
    'player.addspell':   ('Game.GetPlayer().AddSpell', False, None),
    'player.removespell':('Game.GetPlayer().RemoveSpell', False, None),
    'player.moveto':     ('Game.GetPlayer().MoveTo', False, None),
    'player.placeatme':  ('Game.GetPlayer().PlaceAtMe', False, None),

    # --- Additional Actor/Combat ---
    'addscriptpackage':  ('EvaluatePackage',   True,  ';TODO: AddScriptPackage->EvaluatePackage (package arg dropped)'),
    'removescriptpackage': ('EvaluatePackage', True,  ';TODO: RemoveScriptPackage->EvaluatePackage'),
    'startconversation': (None,                True,  ';TODO: No Papyrus equivalent for StartConversation'),
    'getiscurrentpackage': (None,              True,  ';TODO: No direct equivalent for GetIsCurrentPackage'),
    'pickidle':          (None,                True,  ';TODO: Use Debug.SendAnimationEvent()'),
    'playidle':          (None,                True,  ';TODO: Use Debug.SendAnimationEvent()'),
    'isanimplaying':     (None,                True,  ';TODO: No direct equivalent'),
    'getcombattarget':   ('GetCombatTarget',   True,  None),
    'isdisabled':        ('IsDisabled',        True,  None),
    'getparentcellowner':('GetParentCell',     True,  ';TODO: GetParentCellOwner->need cell ownership check'),
    'hasmagiceffect':    ('HasMagicEffect',    True,  ';TODO: Needs MagicEffect form instead of 4-char code'),
    'isexpelled':        (None,                False, None),  # Special handler (ispcexpelled)
    'getdeadcount':      ('GetDeadCount',      True,  ';TODO: Call on ActorBase form'),
    'getcurrentpackage': (None,                True,  ';TODO: No direct equivalent for GetCurrentPackage'),
    'setopendoor':       ('SetOpen',           True,  None),

    # --- Player state ---
    'getplayerinseworld': (None,               False, ';TODO: Use Game.GetPlayer().GetWorldSpace()'),
    'getpcfactionmurder':(None,                False, ';TODO: faction.GetCrimeGoldViolent()'),
    'setpcfactionmurder':(None,                False, ';TODO: faction.SetCrimeGoldViolent()'),
    'getpcfactionattack':(None,                False, ';TODO: faction.GetCrimeGoldViolent()'),
    'setpcfactionattack':(None,                False, ';TODO: faction.SetCrimeGoldViolent()'),
    'getpcfactionsteal': (None,                False, ';TODO: faction.GetCrimeGoldNonViolent()'),
    'setpcfactionsteal': (None,                False, ';TODO: faction.SetCrimeGold()'),
    'getinworldspace':   (None,                False, ';TODO: ref.GetWorldSpace() == worldspace'),
    'getiscurrentweather':(None,               False, ';TODO: Weather.GetCurrentWeather() == weather'),
    'getisreference':    (None,                False, ';TODO: ref == otherRef'),
    'senttojail':        (None,                False, ';TODO: No SendToJail in Papyrus - use quest stage to trigger jail'),
    'isplayersleeping':  (None,                False, ';TODO: Game.GetPlayer().GetSleepState()'),
    'disableplayercontrols': ('Game.DisablePlayerControls', False, None),
    'enableplayercontrols': ('Game.EnablePlayerControls', False, None),
    'enablefasttravel':  (None,                False, ';TODO: Game.EnableFastTravel()'),
    'playbink':          (None,                False, ';TODO: PlayBink - video playback has no Papyrus equivalent'),
    'sendtrespassalarm': (None,               True,  ';TODO: No Papyrus equivalent for SendTrespassAlarm'),
    'getpcisrace':       (None,                False, ';TODO: Game.GetPlayer().GetRace()'),
    'getinfame':         (None,                False, ';TODO: No infamy in Skyrim'),
    'getpcinfamy':       (None,                False, ';TODO: No infamy in Skyrim'),
    'getpcfame':         (None,                False, ';TODO: No fame in Skyrim'),

    # --- AI/Package ---
    'setforcesneak':     ('SetPlayerControls', True,  ';TODO: ForceSneak->no precise equivalent'),
    'getisalerted':      (None,                True,  ';TODO: No Papyrus equivalent for GetIsAlerted'),
    'setalert':          (None,                True,  ';TODO: No Papyrus equivalent for SetAlert'),

    # --- Object Interaction ---
    'getcontainer':      ('GetContainer',      True,  None),
    'opencurrentcontainer': (None,             True,  ';TODO: No direct equivalent'),
    'removeallitems':    ('RemoveAllItems',    True,  None),
    'getdisabled':       ('IsDisabled',        True,  None),
    'attachashpile':     (None,                True,  ';TODO: No Papyrus equivalent for AttachAshPile'),
    'setsize':           ('SetScale',          True,  None),
    'getsize':           ('GetScale',          True,  None),

    # --- Cell/Location ---
    'getincell':         (None,                True,  ';TODO: Use GetParentCell() == target'),
    # 'isininterior' handled by special handler in _emit_function
    'getinsamecellas':   (None,                True,  ';TODO: Use GetParentCell() comparison'),

    # --- Faction/Crime ---
    'ispcexpelled':      (None,                False, None),  # Special handler in _emit_function
    'getpcexpelled':     (None,                False, None),  # Special handler in _emit_function
    'setpcexpelled':     (None,                False, None),  # Special handler in _emit_function
    'payfinethief':      (None,                False, ';TODO: No direct equivalent'),
    'payfine':           (None,                False, ';TODO: No direct equivalent'),
    'gotojail':          (None,                False, None),  # Special handler in _emit_function
    'addachievement':    (None,                False, ';TODO: Achievements not in Skyrim'),
    'modpcfame':         (None,                False, ';TODO: No fame system in Skyrim'),
    'modpcinfamy':       (None,                False, ';TODO: No infamy system in Skyrim'),
    'getpcfame':         (None,                False, ';TODO: No fame system in Skyrim'),
    'getpcinfamy':       (None,                False, ';TODO: No infamy system in Skyrim'),
    'getinfame':         (None,                True,  ';TODO: No infamy system in Skyrim'),
    'setcrimegold':      (None,                False, ';TODO: Needs faction.SetCrimeGold()'),
    'getcrimegold':      (None,                False, ';TODO: Needs faction.GetCrimeGold()'),
    'modcrimegold':      (None,                False, ';TODO: Needs faction.ModCrimeGold()'),

    # --- Dialog/Topic ---
    'refreshtopiclist':  (None,                False, ';TODO: No equivalent (topic lists auto-refresh)'),
    'saycustom':         ('Say',               True,  ';TODO: SayCustom->Say (topic reference needed)'),

    # --- Look/Perception ---
    'look':              ('SetLookAt',         True,  None),
    'stoplooking':       ('ClearLookAt',       True,  None),

    # --- Display/Name ---
    'getdisplayname':    ('GetDisplayName',    True,  None),
    'getname':           ('GetDisplayName',    True,  None),

    # --- Travel ---
    'movetomyeditorlocation': ('MoveToMyEditorLocation', True, None),
    'moveto':            ('MoveTo',            True,  None),
    'movetomarker':      ('MoveTo',            True,  ';TODO: MoveToMarker->MoveTo'),

    # --- Path/Linked Points ---
    'enablelinkedpathpoints':  (None,          True,  ';TODO: No Papyrus path point system'),
    'disablelinkedpathpoints': (None,          True,  ';TODO: No Papyrus path point system'),

    # --- Shader/Visual Effects ---
    'pms':               (None,                True,  ';TODO: PlayMagicShaderVisuals->no direct equivalent'),
    'sms':               (None,                True,  ';TODO: StopMagicShaderVisuals->no direct equivalent'),
    'playmagicshadervisuals':  (None,          True,  ';TODO: No direct equivalent'),
    'stopmagicshadervisuals':  (None,          True,  ';TODO: No direct equivalent'),
    'playmagiceffectvisuals':  (None,          True,  ';TODO: No direct equivalent'),
    'stopmagiceffectvisuals':  (None,          True,  ';TODO: No direct equivalent'),
    'pme':               (None,                True,  ';TODO: PlayMagicEffectVisuals->no direct equivalent'),
    'sme':               (None,                True,  ';TODO: StopMagicEffectVisuals->no direct equivalent'),
    'triggerhitshader':  (None,                True,  ';TODO: No Papyrus equivalent for TriggerHitShader'),
    'scaonactor':        (None,                True,  ';TODO: SetCombatAlarmOnActor->no direct equivalent'),
    'sca':               (None,                True,  ';TODO: SetCombatAlarm->no direct equivalent'),

    # --- AI/Wait ---
    'stopwaiting':       ('EvaluatePackage',   True,  ';TODO: StopWaiting->EvaluatePackage'),
    'setcombatstyle':    (None,                True,  ';TODO: Needs CombatStyle form reference'),
    'setignorefriendlyhits': (None,            True,  ';TODO: No direct equivalent'),
    'sayto':             ('Say',               True,  ';TODO: SayTo->Say (needs topic form)'),

    # --- Detection ---
    'getdetectionlevel': (None,                True,  ';TODO: Use IsDetectedBy() instead'),

    # --- Door/Object State ---
    'setopenstate':      ('SetOpen',           True,  None),
    'resetinterior':     (None,                True,  ';TODO: cellRef.Reset() -- ResetInterior needs target cell'),

    # --- Player Skill/Misc ---
    'modpcskill':        (None,                False, ';TODO: Game.AdvanceSkill()'),
    'modpcmiscstat':     (None,                False, ';TODO: Game.IncrementStat()'),
    'getpcmiscstat':     (None,                False, ';TODO: Game.QueryStat()'),

    # --- Trap/Custom functions that are quest-specific ---
    'trapupdate':        (None,                True,  ';TODO: Quest-specific trap function'),

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
    'modamountsoldstolen':(None,               False, ';TODO: No Papyrus equivalent for ModAmountSoldStolen'),
    'setcellownership':  (None,                False, ';TODO: No Papyrus equivalent (use cell.SetActorOwner in CK)'),
    'setpublic':         (None,                False, ';TODO: No Papyrus equivalent for SetPublic (cell ownership)'),
    'closeCurrentOblivionGate': (None,         False, ';TODO: Oblivion-specific'),
    'setshowquestitems': (None,                False, ';TODO: No Papyrus equivalent'),
    'setnorumors':       (None,                False, ';TODO: No Papyrus equivalent'),
    'setsceneiscomplex': (None,                False, ';TODO: No Papyrus equivalent'),
    'setdisplayname':    ('SetDisplayName',    True,  None),
    'setpackduration':   (None,                False, ';TODO: No Papyrus equivalent'),
    'showbirthsignmenu': (None,                False, ';TODO: No birthsigns in Skyrim'),
    'isspelltarget':     (None,                True,  ';TODO: No direct equivalent'),
    'getarmorrating':    (None,                True,  ';TODO: GetArmorRating - SKSE required'),
    'isidleplaying':     (None,                True,  ';TODO: No direct equivalent'),
    'getopenstate':      ('GetOpenState',      True,  None),
    'getstartingpos':    (None,                True,  ';TODO: No Papyrus equivalent'),
    'getcurrentaiprocedure': (None,            True,  ';TODO: No direct equivalent'),
    'getcurrentaipackage': (None,              True,  ';TODO: No direct equivalent'),
    'isessential':       ('IsEssential',       True,  None),
    'getlos':            ('HasLOS',            True,  None),
    'isactor':           (None,                True,  ';TODO: Use (ref as Actor) != None'),
    'israining':         (None,                False, ';TODO: Weather.GetClassification()'),
    'isindangerouswater':(None,                True,  ';TODO: No direct equivalent'),
    'getplayercontrolsdisabled': (None,        False, ';TODO: No direct equivalent'),
    'getisplayerbirthsign': (None,             False, ';TODO: No birthsigns in Skyrim'),
    'isplayerinjail':    (None,                False, ';TODO: No direct equivalent'),
    'getpcfactionattack':(None,                False, ';TODO: faction.GetCrimeGoldViolent()'),
    'getpcfactionsteal': (None,                False, ';TODO: faction.GetCrimeGold()'),
    'ispcanmurderer':    (None,                False, ';TODO: No direct equivalent'),
    'ispcamurderer':     (None,                False, ';TODO: No direct equivalent'),
    'getpcismurderer':   (None,                False, ';TODO: No direct equivalent'),
    'isowner':           ('IsInFaction',        True,  ';TODO: IsOwner->IsInFaction (approximate)'),
    'gettalkedtopc':     (None,                False, ';TODO: No Papyrus equivalent for GetTalkedToPC'),
    'gettalkedtopcp':    (None,                False, ';TODO: No Papyrus equivalent for GetTalkedToPCParam'),
    'menumode':          (None,                False, ';TODO: No Papyrus equivalent for MenuMode check'),
    'istimepassing':     (None,                False, ';TODO: No Papyrus equivalent for IsTimePassing'),
    'expel':             (None,                True,  ';TODO: No Papyrus equivalent for Expel'),
    'setitemvalue':      (None,                True,  ';TODO: No Papyrus equivalent for SetItemValue'),
    'setnoavoidance':    (None,                True,  ';TODO: No Papyrus equivalent for SetNoAvoidance'),
    'offerhorse':        (None,                True,  ';TODO: No Papyrus equivalent for OfferHorse'),
    'setactorrefraction':(None,                True,  ';TODO: SKSE required - SetActorRefraction'),
    'setdisplayname':    (None,                True,  ';TODO: SKSE required - SetDisplayName'),
    'getcontainer':      (None,                True,  ';TODO: SKSE required - GetContainer'),
    'stopcombatalarmonactor': ('StopCombat',   True,  None),
    'essentialdeathreload': (None,             False, ';TODO: No Papyrus equivalent - use quest failure stage'),
    'setallreachable':   (None,                True,  ';TODO: No Papyrus equivalent for SetAllReachable'),
    'getdestroyed':      (None,                True,  ';TODO: No Papyrus equivalent for GetDestroyed'),
    'setclass':          (None,                True,  ';TODO: No Papyrus equivalent for SetClass'),
    'setdoordefaultopen':(None,                True,  ';TODO: No Papyrus equivalent'),
    'setrestrained':     (None,                True,  ';TODO: No Papyrus equivalent'),
    'getrestrained':     (None,                True,  ';TODO: No Papyrus equivalent'),
    'rotate':            (None,                True,  ';TODO: Use SetAngle(GetAngleX()+dx, GetAngleY()+dy, GetAngleZ()+dz)'),
    'clearownership':    (None,                True,  ';TODO: No Papyrus equivalent for ClearOwnership'),
    'setlevel':          (None,                True,  ';TODO: No Papyrus equivalent for SetLevel'),
    'showspellmaking':   (None,                False, ';TODO: No Papyrus equivalent for ShowSpellmaking'),
    'setrigidbodymass':  (None,                True,  ';TODO: No Papyrus equivalent'),
    'resetfalldamagetimer': (None,             True,  ';TODO: No Papyrus equivalent'),
    'setpcfame':         (None,                False, ';TODO: No Papyrus equivalent for SetPCFame'),
    'setpcinfamy':       (None,                False, ';TODO: No Papyrus equivalent for SetPCInfamy'),
    'forceflee':         (None,                True,  ';TODO: Use package system instead'),
    'setinvestmentgold': (None,                True,  ';TODO: No Papyrus equivalent'),
    'setallvisible':     (None,                True,  ';TODO: No Papyrus equivalent'),
    'getpcfame':         (None,                False, ';TODO: No Papyrus equivalent for GetPCFame'),
    'getpcinfamy':       (None,                False, ';TODO: No Papyrus equivalent for GetPCInfamy'),
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
}


# ===========================================================================
# Cross-reference graph builder
# ===========================================================================

class CrossRefGraph:
    """Builds FormID->EditorID and EditorID->ScriptName lookup tables."""

    def __init__(self):
        self.formid_to_edid: dict[str, str] = {}
        self.edid_to_formid: dict[str, str] = {}
        self.script_formid_to_edid: dict[str, str] = {}
        self.script_formid_to_type: dict[str, int] = {}
        self.record_scri: dict[str, str] = {}  # record FormID -> SCRI FormID
        self.record_type: dict[str, str] = {}  # record FormID -> record Signature
        self.record_base: dict[str, str] = {}  # placed ref FormID -> base record FormID (NAME)
        self.quest_edids: set[str] = set()
        self.npc_formids: set[str] = set()
        # Cross-script ref-as-int analysis: set of (script_name_lower, var_name_lower)
        # where the TES4 `ref` variable is only ever assigned/compared with integers
        self.ref_as_int: set[tuple[str, str]] = set()
        # Per-script ref-typed variable names (populated by build_ref_as_int_map)
        self.script_ref_vars: dict[str, set[str]] = {}
        # Cross-script variable accesses: script_name_lower -> set of var_name_lower
        # Variables that are accessed from OTHER scripts (need to be Properties)
        self.cross_script_vars: dict[str, set[str]] = {}
        # Per-script ALL variable declarations: script_name_lower -> dict(var_low -> type_str)
        self.script_all_vars: dict[str, dict[str, str]] = {}
    def load_from_export(self, export_dir: str):
        """Load cross-reference data from all export .txt files."""
        if not os.path.isdir(export_dir):
            return
        for fname in os.listdir(export_dir):
            if fname.endswith('.txt'):
                sig = fname[:-4]
                self._scan_file(os.path.join(export_dir, fname), sig)

    def _scan_file(self, fpath: str, sig: str):
        """Scan a single export file for cross-reference data."""
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                content = f.read()
        except Exception:
            return

        in_record = False
        formid = edid = scri = name_fid = None
        schr_type = None

        for line in content.split('\n'):
            line = line.rstrip()
            if line == '---RECORD_BEGIN---':
                in_record = True
                formid = edid = scri = name_fid = None
                schr_type = None
                continue
            if line == '---RECORD_END---':
                if in_record and formid:
                    if edid:
                        self.formid_to_edid[formid] = edid
                        self.edid_to_formid[edid.lower()] = formid
                    if sig == 'SCPT':
                        if edid:
                            self.script_formid_to_edid[formid] = edid
                        if schr_type is not None:
                            self.script_formid_to_type[formid] = schr_type
                    if scri:
                        self.record_scri[formid] = scri
                    if name_fid and sig in ('ACHR', 'ACRE', 'REFR'):
                        self.record_base[formid] = name_fid
                    self.record_type[formid] = sig
                    if sig == 'QUST' and edid:
                        self.quest_edids.add(edid.lower())
                    if sig in ('NPC_', 'CREA'):
                        self.npc_formids.add(formid)
                in_record = False
                continue
            if not in_record:
                continue
            if line.startswith('FormID='):
                formid = line[7:]
            elif line.startswith('EditorID='):
                edid = line[9:]
            elif line.startswith('SCRI='):
                scri = line[5:]
            elif line.startswith('NAME='):
                name_fid = line[5:]
            elif line.startswith('SCHR.Type='):
                try:
                    schr_type = int(line[10:])
                except ValueError:
                    pass

    def get_extends_class(self, script_formid: str) -> str:
        """Determine the Papyrus extends class for a script."""
        schr_type = self.script_formid_to_type.get(script_formid, 0)

        if schr_type == 1:
            return 'Quest'
        if schr_type == 256:
            return 'ActiveMagicEffect'

        # Type 0: check if attached to NPC_/CREA -> Actor
        for rec_fid, scri_fid in self.record_scri.items():
            if scri_fid == script_formid:
                rec_sig = self.record_type.get(rec_fid, '')
                if rec_sig in ('NPC_', 'CREA'):
                    return 'Actor'
                if rec_sig == 'QUST':
                    return 'Quest'

        return 'ObjectReference'

    def is_quest_ref(self, name: str) -> bool:
        """Check if a name refers to a known quest."""
        return name.lower() in self.quest_edids

    def get_quest_script_type(self, quest_name: str) -> str:
        """Get the Papyrus script class name for a quest, e.g. 'TES4_MyQuestScript'.
        Returns 'Quest' if no attached script is found."""
        low = quest_name.lower()
        fid = self.edid_to_formid.get(low, '')
        if not fid:
            return 'Quest'
        scri_fid = self.record_scri.get(fid, '')
        if not scri_fid:
            return 'Quest'
        script_edid = self.script_formid_to_edid.get(scri_fid, '')
        if not script_edid:
            return 'Quest'
        return f'TES4_{script_edid}'

    def get_record_script_type(self, name: str) -> str:
        """Get the Papyrus script class name for any record with an attached script.
        For placed references (ACHR/ACRE/REFR), follows the NAME chain to the
        base record to find the attached script.
        Returns '' if the record has no attached script."""
        low = name.lower()
        fid = self.edid_to_formid.get(low, '')
        if not fid:
            return ''
        scri_fid = self.record_scri.get(fid, '')
        # For placed refs without own SCRI, follow base form chain
        if not scri_fid:
            base_fid = self.record_base.get(fid, '')
            if base_fid:
                scri_fid = self.record_scri.get(base_fid, '')
        if not scri_fid:
            return ''
        script_edid = self.script_formid_to_edid.get(scri_fid, '')
        if not script_edid:
            return ''
        return f'TES4_{script_edid}'

    def build_ref_as_int_map(self, scpt_path: str):
        """Scan all SCPT SCTX sources to find ref variables used only as integers.

        TES4 'ref' type can hold both references and integers.  When a ref
        variable is only ever assigned/compared with numeric literals across
        ALL scripts that touch it, it should be typed Int in Papyrus.
        """
        records = parse_export_file(scpt_path)

        # Phase A: collect variable declarations per script
        _decl_re = re.compile(r'^\s*ref\s+(\w+)', re.IGNORECASE)
        _all_decl_re = re.compile(r'^\s*(short|long|float|ref)\s+(\w+)', re.IGNORECASE)
        _TES4_TO_PAPYRUS_TYPE = {'short': 'Int', 'long': 'Int', 'float': 'Float', 'ref': 'ObjectReference'}
        script_ref_vars: dict[str, set[str]] = {}
        script_all_vars: dict[str, dict[str, str]] = {}
        script_sources: dict[str, str] = {}

        for rec in records:
            edid = rec.get('EditorID', '')
            sctx = rec.get('SCTX', '')
            if not edid or not sctx:
                continue
            scn_low = edid.lower()
            script_sources[scn_low] = sctx
            ref_vars = set()
            all_vars: dict[str, str] = {}
            for line in sctx.split('\n'):
                stripped = line.strip()
                m = _decl_re.match(stripped)
                if m:
                    ref_vars.add(m.group(1).lower())
                am = _all_decl_re.match(stripped)
                if am:
                    vtype = am.group(1).lower()
                    vname = am.group(2).lower()
                    all_vars[vname] = _TES4_TO_PAPYRUS_TYPE.get(vtype, 'Int')
            if ref_vars:
                script_ref_vars[scn_low] = ref_vars
            if all_vars:
                script_all_vars[scn_low] = all_vars

        # Persist for cross-script type lookups
        self.script_ref_vars = script_ref_vars
        self.script_all_vars = script_all_vars

        if not script_ref_vars:
            return

        # Phase B: scan ALL scripts for usage of ref vars
        _set_re = re.compile(
            r'\bset\s+(?:(\w+)\.)?(\w+)\s+to\s+(.+)',
            re.IGNORECASE
        )
        # (script_lower, var_lower) -> {'zero', 'int', 'ref'}
        usage: dict[tuple[str, str], set[str]] = {}

        for scn_low, sctx in script_sources.items():
            for raw_line in sctx.split('\n'):
                line = raw_line.strip()
                if not line or line.startswith(';'):
                    continue

                # Detect ref usage: var.method() patterns on local ref variables
                if scn_low in script_ref_vars:
                    for ref_var in script_ref_vars[scn_low]:
                        if re.search(r'\b' + re.escape(ref_var) + r'\.\w+',
                                     line, re.IGNORECASE):
                            key = (scn_low, ref_var)
                            if key not in usage:
                                usage[key] = set()
                            usage[key].add('ref')

                # Check 'set [obj.]var to value' patterns
                sm = _set_re.match(line)
                if sm:
                    target_obj = (sm.group(1) or '').lower()
                    var_name = sm.group(2).lower()
                    value = sm.group(3).strip()
                    # Strip TES4 inline comments ("; comment text")
                    semi_idx = value.find(';')
                    if semi_idx >= 0:
                        value = value[:semi_idx].strip()
                    if target_obj:
                        owner = target_obj
                    else:
                        owner = scn_low
                    # Resolve owner to its script name
                    owner_script = None
                    if owner in script_ref_vars and var_name in script_ref_vars[owner]:
                        owner_script = owner
                    elif owner != scn_low:
                        base_fid = self.edid_to_formid.get(owner, '')
                        if base_fid:
                            scri_fid = self.record_scri.get(base_fid, '')
                            if scri_fid:
                                se = self.script_formid_to_edid.get(scri_fid, '')
                                if se:
                                    se_low = se.lower()
                                    if se_low in script_ref_vars and var_name in script_ref_vars[se_low]:
                                        owner_script = se_low

                    if owner_script:
                        key = (owner_script, var_name)
                        if key not in usage:
                            usage[key] = set()
                        if re.match(r'^-?\d+(\.\d+)?$', value):
                            if value.strip() == '0':
                                usage[key].add('zero')
                            else:
                                usage[key].add('int')
                        else:
                            usage[key].add('ref')

        # Phase C: ref vars with ONLY non-zero integer usage -> retype to Int
        for (script_low, var_low), types in usage.items():
            if 'ref' not in types and 'int' in types:
                self.ref_as_int.add((script_low, var_low))

        # Phase D: detect cross-script variable access (Owner.VarName patterns)
        # These variables must be Properties on the owning script so other scripts
        # can access them. Scans SCPT sources, INFO result scripts, and QUST stage scripts.
        _owner_var_re = re.compile(r'\b(\w+)\.(\w+)\b')
        cross_script_vars: dict[str, set[str]] = {}

        def _scan_text_for_cross_access(text):
            for raw_line in text.split('\n'):
                line = raw_line.strip()
                if not line or line.startswith(';'):
                    continue
                semi = line.find(';')
                if semi >= 0:
                    line = line[:semi]
                for match in _owner_var_re.finditer(line):
                    owner = match.group(1).lower()
                    var = match.group(2).lower()
                    target_script = None
                    if owner in script_all_vars and var in script_all_vars[owner]:
                        target_script = owner
                    else:
                        fid = self.edid_to_formid.get(owner, '')
                        if fid:
                            scri_fid = self.record_scri.get(fid, '')
                            if scri_fid:
                                se = self.script_formid_to_edid.get(scri_fid, '')
                                if se:
                                    se_low = se.lower()
                                    if se_low in script_all_vars and var in script_all_vars[se_low]:
                                        target_script = se_low
                    if target_script:
                        if target_script not in cross_script_vars:
                            cross_script_vars[target_script] = set()
                        cross_script_vars[target_script].add(var)

        # Scan all SCPT sources
        for scn_low, sctx in script_sources.items():
            _scan_text_for_cross_access(sctx)

        # Scan INFO result scripts and QUST stage scripts for cross-script access
        export_dir = os.path.dirname(scpt_path)
        for extra_file, field_name in [('INFO.txt', 'ResultScript'), ('QUST.txt', 'SCTX')]:
            extra_path = os.path.join(export_dir, extra_file)
            if not os.path.isfile(extra_path):
                continue
            try:
                with open(extra_path, 'r', encoding='utf-8') as f:
                    for raw_line in f:
                        if raw_line.startswith(field_name + '='):
                            text = raw_line[len(field_name) + 1:].strip()
                            text = text.replace('\\r\\n', '\n').replace('\\n', '\n')
                            _scan_text_for_cross_access(text)
            except Exception:
                pass

        self.cross_script_vars = cross_script_vars
    def is_remote_ref_var(self, owner_edid: str, var_name: str) -> bool:
        """Check if a variable on a remote record's script is ref-typed in TES4.

        *owner_edid* is the EditorID of the quest/NPC/object (e.g. 'MQ00').
        *var_name* is the property name (e.g. 'nearOblivionGate').
        Returns True if the remote script declares that variable as 'ref'
        AND it is not a ref-as-int variable (used only as integers).
        """
        var_low = var_name.lower()
        owner_low = owner_edid.lower()
        # Direct script name match
        if owner_low in self.script_ref_vars:
            if var_low in self.script_ref_vars[owner_low]:
                return (owner_low, var_low) not in self.ref_as_int
            return False
        # Resolve owner EditorID -> script name
        fid = self.edid_to_formid.get(owner_low, '')
        if not fid:
            return False
        scri_fid = self.record_scri.get(fid, '')
        if not scri_fid:
            return False
        se = self.script_formid_to_edid.get(scri_fid, '')
        if not se:
            return False
        se_low = se.lower()
        if se_low in self.script_ref_vars:
            if var_low in self.script_ref_vars[se_low]:
                return (se_low, var_low) not in self.ref_as_int
        return False


# ===========================================================================
# Script converter
# ===========================================================================

class ScriptConverter:
    """Converts Oblivion script source to Papyrus .psc source."""

    def __init__(self, xref: CrossRefGraph):
        self.xref = xref
        self._property_refs: dict[str, str] = {}
        self._has_gamemode = False
        self._has_menumode = False
        self._has_scripteffectupdate = False
        self._uses_getsecondspassed = False
        self._uses_timer = False
        self._local_vars = set()
        self._var_renames: dict[str, str] = {}  # orig_lower -> safe_name
        self._var_types: dict[str, str] = {}  # lower_name -> papyrus_type
        self._current_event: str = ''  # Current event header for context-aware conversion

    def _is_ref_typed_access(self, dotted_expr: str) -> bool:
        """Check if a dotted expression (e.g. 'SEHerdirRef.TargetRef') accesses a ref-typed variable.

        Checks both via EditorID resolution and via property type → script_all_vars.
        """
        if '.' not in dotted_expr:
            return False
        parts = dotted_expr.strip().split('.', 1)
        prop_low = parts[0].lower()
        var_low = parts[1].lower()
        # Method 1: resolve via EditorID
        if self.xref and self.xref.is_remote_ref_var(parts[0], parts[1]):
            return True
        # Method 2: resolve via property type declaration
        if self.xref:
            for pname, ptype in self._property_refs.items():
                if pname.lower() == prop_low and ptype.startswith('TES4_'):
                    script_name = ptype[5:].lower()
                    all_vars = self.xref.script_all_vars.get(script_name, {})
                    if all_vars.get(var_low) in ('ObjectReference', 'Actor'):
                        return True
        return False

    def _is_ref_as_int_crossscript(self, dotted_expr: str) -> bool:
        """Check if a cross-script dotted var (e.g. 'MQConversations.OGDeadDaedra') was retyped to Int.

        Returns True if the variable exists in ref_as_int for the owning script.
        """
        if '.' not in dotted_expr or not self.xref:
            return False
        parts = dotted_expr.strip().split('.', 1)
        prop_low = parts[0].lower()
        var_low = parts[1].lower()
        # Resolve property type to script name
        for pname, ptype in self._property_refs.items():
            if pname.lower() == prop_low:
                if ptype.startswith('TES4_'):
                    script_name = ptype[5:].lower()
                else:
                    script_name = ptype.lower()
                if (script_name, var_low) in self.xref.ref_as_int:
                    return True
        # Also try EditorID resolution
        prop_upper = parts[0]
        edid = self.xref.edid_to_formid.get(prop_upper) or self.xref.edid_to_formid.get(prop_upper.lower())
        if edid:
            scri_fid = self.xref.record_scri.get(edid)
            if scri_fid:
                script_edid = self.xref.script_formid_to_edid.get(scri_fid, '').lower()
                if script_edid and (script_edid, var_low) in self.xref.ref_as_int:
                    return True
        return False

    @staticmethod
    def _infer_extends(source: str, extends: str) -> str:
        """Pre-scan source for bare Actor-only function calls; upgrade extends."""
        for func in _ACTOR_ONLY_FUNCTIONS:
            # Match bare calls (not preceded by '.') anywhere in source
            if re.search(r'(?<!\.)(?<!\w)' + re.escape(func) + r'(?:\s|$|\()',
                         source, re.IGNORECASE):
                return 'Actor'
        return extends

    def convert_standalone(self, name: str, source: str, extends: str = 'ObjectReference',
                           editor_id: str = '') -> str:
        """Convert a standalone SCPT record to a full .psc file."""
        saved_refs = dict(self._property_refs)
        self._reset()
        self._property_refs = saved_refs

        # Pre-scan: if script uses Actor-only functions on self (no ref prefix),
        # upgrade extends to Actor
        if extends == 'ObjectReference':
            extends = self._infer_extends(source, extends)

        variables, blocks = self._parse_source(source)
        # Store locally declared variable names for expression disambiguation
        self._local_vars = {_safe_property_name(v[1]).lower() for v in variables}
        # Store variable types for type-aware assignment conversion
        _edid_low = editor_id.lower()
        for v in variables:
            vtype_low = v[0].lower()
            vname_safe = _safe_property_name(v[1])
            ptype = TYPE_MAP.get(vtype_low, 'Int')
            if ptype == 'ObjectReference' and _edid_low and \
               (_edid_low, vname_safe.lower()) in self.xref.ref_as_int:
                ptype = 'Int'
            self._var_types[vname_safe.lower()] = ptype
        # Build rename map: original_lower -> safe_name (only when they differ)
        for _, vname in variables:
            safe = _safe_property_name(vname)
            if safe.lower() != vname.lower():
                self._var_renames[vname.lower()] = safe

        source_low = source.lower()
        self._uses_getsecondspassed = 'getsecondspassed' in source_low
        self._uses_timer = bool(re.search(r'\btimer\b', source_low))
        self._has_gamemode = any(b[0] == 'gamemode' for b in blocks)
        self._has_menumode = any(b[0] == 'menumode' for b in blocks)
        self._has_scripteffectupdate = any(b[0] == 'scripteffectupdate' for b in blocks)

        out = []
        out.append(f'ScriptName TES4_{name} extends {extends}')
        out.append(f'{{Converted from TES4: {editor_id or name}}}')
        out.append('')

        # Variable declarations as properties (type may be upgraded after conversion)
        _var_info = []
        _seen_vars = set()
        for vtype, vname in variables:
            ptype = TYPE_MAP.get(vtype, 'Int')
            safe_vname = _safe_property_name(vname)
            if safe_vname.lower() in _seen_vars:
                continue  # skip duplicate declarations
            _seen_vars.add(safe_vname.lower())
            # Override ref vars that are only used with integers (cross-script analysis)
            if ptype == 'ObjectReference' and _edid_low and \
               (_edid_low, safe_vname.lower()) in self.xref.ref_as_int:
                ptype = 'Int'
            _var_info.append((safe_vname, ptype))
        var_start_idx = len(out)
        for safe_vname, ptype in _var_info:
            if ptype == 'Float':
                out.append(f'{ptype} Property {safe_vname} = 0.0 Auto')
            else:
                out.append(f'{ptype} Property {safe_vname} Auto')

        if variables:
            out.append('')

        # Convert blocks — merge duplicate event types
        needs_oninit_update = self._has_gamemode or self._has_scripteffectupdate
        gamemode_body = []

        # Group blocks by event type to merge duplicates (Papyrus forbids
        # duplicate Event declarations)
        from collections import defaultdict
        merged_blocks: dict[str, list] = defaultdict(list)
        block_order: list[str] = []

        for block_type, block_lines in blocks:
            if block_type in ('gamemode', 'menumode', 'scripteffectupdate'):
                gamemode_body.extend(block_lines)
                continue

            # Merge blocks by their target Papyrus event name, not TES4 block type
            # This prevents duplicate events (e.g. onadd+ondrop→OnContainerChanged)
            mapping = BLOCK_MAP.get(block_type)
            merge_key = mapping[0] if mapping else block_type
            if merge_key not in merged_blocks:
                block_order.append(merge_key)
            merged_blocks[merge_key].extend(block_lines)

        for merge_key in block_order:
            all_lines = merged_blocks[merge_key]
            # merge_key is already the event_begin string (or the block_type if unmapped)
            self._current_event = merge_key
            if merge_key.startswith('Event ') or merge_key.startswith(';'):
                event_begin = merge_key
                event_end = 'EndEvent' if merge_key.startswith('Event ') else ''
                if not event_begin.startswith(';'):
                    out.append(event_begin)
                    for bline in all_lines:
                        converted = self._convert_line(bline, extends)
                        out.append(f'  {converted}')
                    out.append(event_end)
                else:
                    # Unsupported event — comment out all code to avoid top-level errors
                    out.append(event_begin)
                    for bline in all_lines:
                        converted = self._convert_line(bline, extends)
                        out.append(f'  ;{converted}')
                    if event_end:
                        out.append(event_end)
                out.append('')
            else:
                # Unknown block type — comment it out
                out.append(f';TODO: Unknown event block: {merge_key}')
                for bline in all_lines:
                    converted = self._convert_line(bline, extends)
                    out.append(f'  ;{converted}')
                out.append('')

        # Emit OnUpdate for GameMode/MenuMode/ScriptEffectUpdate
        if gamemode_body:
            interval = self._get_update_interval()
            self._current_event = 'Event OnUpdate()'
            out.append('Event OnUpdate()')
            for bline in gamemode_body:
                converted = self._convert_line(bline, extends)
                out.append(f'  {converted}')
            out.append(f'  RegisterForSingleUpdate({interval})')
            out.append('EndEvent')
            out.append('')

        # Emit OnInit for RegisterForSingleUpdate
        if needs_oninit_update:
            has_oninit = any(b[0] == 'oninit' for b in blocks)
            if not has_oninit:
                interval = self._get_update_interval()
                out.append('Event OnInit()')
                out.append(f'  RegisterForSingleUpdate({interval})')
                out.append('EndEvent')
                out.append('')

        # Balance If/EndIf within event blocks (some TES4 scripts have extra EndIf)
        out = self._balance_if_endif(out)

        # Remove dead code after Return statements within event/function blocks
        out = self._remove_dead_code_after_return(out)

        # Apply shared post-processing (TES4-only functions, type mismatches, etc.)
        out = self._postprocess_lines(out)

        # Post-process: retype ObjectReference variables that are only used as integers
        # TES4 'ref' type was general-purpose; scripts often used ref vars as int flags
        if _var_info:
            _ref_typed_vars = {name.lower(): idx for idx, (name, ptype) in enumerate(_var_info)
                            if ptype == 'ObjectReference'}
            if _ref_typed_vars:
                _assign_re = re.compile(r'^\s*(\w+)\s*=\s*(.+)', re.IGNORECASE)
                for var_low, vi in list(_ref_typed_vars.items()):
                    has_int_assign = False
                    has_ref_assign = False
                    has_ref_usage = False
                    for line in out[var_start_idx + len(_var_info) + 1:]:
                        # Check if variable is used as a reference (method calls, comparisons with refs)
                        if re.search(r'\b' + re.escape(var_low) + r'\.\w+\s*\(', line, re.IGNORECASE):
                            has_ref_usage = True
                            break
                        # Check for comparisons with None or ref variables
                        if re.search(r'\b' + re.escape(var_low) + r'\s*[!=]=\s*None\b', line, re.IGNORECASE):
                            has_ref_usage = True
                            break
                        # Check if variable is used as a function argument (not on LHS of =)
                        stripped = line.lstrip()
                        if not stripped.startswith(var_low) and re.search(
                                r'\(\s*' + re.escape(var_low) + r'\b', line, re.IGNORECASE):
                            has_ref_usage = True
                            break
                        am = _assign_re.match(line)
                        if not am:
                            continue
                        if am.group(1).lower() != var_low:
                            continue
                        val = am.group(2).split(';')[0].strip()
                        # Integer literal assignments (0, 1, 2, etc.)
                        if re.match(r'^-?\d+$', val):
                            has_int_assign = True
                        # None assignment (already converted from ref = 0)
                        elif val == 'None':
                            has_ref_assign = True
                        # Math expressions producing int
                        elif re.match(r'^[\w.]+ [+\-*/] \d+$', val):
                            has_int_assign = True
                        else:
                            has_ref_assign = True
                    if has_int_assign and not has_ref_assign and not has_ref_usage:
                        # Retype: ObjectReference → Int
                        decl_idx = var_start_idx + vi
                        if decl_idx < len(out):
                            out[decl_idx] = out[decl_idx].replace(
                                'ObjectReference Property', 'Int Property', 1)
                            real_name = _var_info[vi][0]
                            self._var_types[real_name.lower()] = 'Int'
                            # Also replace = None back to = 0 in body
                            none_re = re.compile(
                                r'^(\s*' + re.escape(real_name) + r'\s*=\s*)None\b',
                                re.IGNORECASE)
                            for bidx in range(var_start_idx + len(_var_info) + 1, len(out)):
                                if none_re.match(out[bidx]):
                                    out[bidx] = none_re.sub(r'\g<1>0', out[bidx])

        # Post-process: upgrade ObjectReference variables to more specific types
        # based on usage (Actor from actor-only functions, or script type from SCRO/xref)
        if _var_info and self._property_refs:
            # Build case-insensitive lookup for type upgrades
            _ci_refs = {k.lower(): v for k, v in self._property_refs.items()}
            for idx in range(var_start_idx, var_start_idx + len(_var_info)):
                if idx >= len(out):
                    break
                line = out[idx]
                if 'ObjectReference Property ' not in line:
                    continue
                parts = line.split('Property ', 1)
                if len(parts) < 2:
                    continue
                prop_name = parts[1].split()[0]
                new_type = _ci_refs.get(prop_name.lower(), '')
                if new_type and new_type != 'ObjectReference':
                    out[idx] = line.replace('ObjectReference Property ',
                                            f'{new_type} Property ', 1)
                    # Also update _var_types so downstream logic knows the new type
                    self._var_types[prop_name.lower()] = new_type

        # Post-process: add 'as Actor' casts for ObjRef-returning assignments to Actor vars
        _actor_vars = {k.lower() for k, v in self._property_refs.items() if v == 'Actor'}
        _actor_vars |= {k for k, v in self._var_types.items() if v == 'Actor'}
        if _actor_vars:
            _objref_re = self._OBJREF_RETURNING
            _objref_params = self._OBJREF_PARAMS
            # Build set of variables known to be ObjectReference
            _objref_vars = {k for k, v in self._var_types.items() if v == 'ObjectReference'}
            _objref_vars |= {k.lower() for k, v in self._property_refs.items() if v == 'ObjectReference'}
            for idx in range(len(out)):
                line = out[idx]
                s = line.lstrip()
                # Match: VarName = expr  (not already cast)
                eq_m = re.match(r'^(\w+)\s*=\s*(.+)', s)
                if not eq_m:
                    continue
                var_name = eq_m.group(1)
                val = eq_m.group(2).rstrip()
                if var_name.lower() not in _actor_vars:
                    continue
                if 'as Actor' in val:
                    continue
                # Strip inline comments for checking
                val_check = val.split(';')[0].strip() if ';' in val else val
                needs_cast = False
                if _objref_re.search(val_check):
                    needs_cast = True
                elif val_check.lower().strip('() ') in _objref_params:
                    needs_cast = True
                elif val_check.lower() in _objref_vars:
                    needs_cast = True
                if needs_cast:
                    indent = line[:len(line) - len(s)]
                    # Insert 'as Actor' before any inline comment
                    if ';' in val and not val.startswith(';'):
                        code_part = val.split(';')[0].rstrip()
                        comment_part = ';' + val.split(';', 1)[1]
                        out[idx] = f'{indent}{var_name} = {code_part} as Actor  {comment_part}'
                    else:
                        out[idx] = f'{indent}{var_name} = {val} as Actor'

        # Post-process: cast ObjRef-typed args to Actor in actor-parameter functions
        _actor_param_funcs = re.compile(
            r'\b(?:StartCombat|IsDetectedBy|PushActorAway|SendAssaultAlarm|GetRelationshipRank'
            r'|SetRelationshipRank|SetPlayerTeammate)\s*\(',
            re.IGNORECASE)
        _all_objref = _objref_vars if '_objref_vars' in dir() else set()
        _all_objref |= {k for k, v in self._var_types.items() if v == 'ObjectReference'}
        _all_objref |= {k.lower() for k, v in self._property_refs.items() if v == 'ObjectReference'}
        if _all_objref:
            for idx in range(len(out)):
                line = out[idx]
                if not _actor_param_funcs.search(line):
                    continue
                # Replace ObjRef variables with 'var as Actor' in function args
                for var in _all_objref:
                    # Match var as a whole word inside parentheses, not already cast
                    pattern = r'(\b' + re.escape(var) + r')(\b)(?!\s+as\s+Actor)'
                    if re.search(pattern, line, re.IGNORECASE):
                        line = re.sub(pattern, r'\1 as Actor\2', line, flags=re.IGNORECASE)
                out[idx] = line

        # Post-process: convert integer literal assignments to None for ref-typed variables
        # Handles both local (someActorVar = 0) and cross-script (Quest.Var = 1)
        if self._property_refs or self._var_types:
            _ref_types = ('ObjectReference', 'Actor', 'ActorBase')
            _assign_int_re = re.compile(r'^(\s*)([\w.]+)\s*=\s*(-?\d+)\s*(;.*)?$')
            for idx in range(len(out)):
                m = _assign_int_re.match(out[idx])
                if not m:
                    continue
                tgt = m.group(2)
                int_val = m.group(3)
                is_ref = False
                if '.' in tgt:
                    # Cross-script: check remote ref type via xref graph
                    parts = tgt.split('.', 1)
                    if self.xref and self.xref.is_remote_ref_var(parts[0], parts[1]):
                        is_ref = True
                else:
                    low = tgt.lower()
                    vtype = self._var_types.get(low, '')
                    if not vtype:
                        vtype = self._property_refs.get(tgt, self._property_refs.get(low, ''))
                    if vtype in _ref_types or vtype.startswith('TES4_'):
                        is_ref = True
                if is_ref:
                    cmt = m.group(4) or ''
                    out[idx] = f'{m.group(1)}{tgt} = None  {cmt}'.rstrip()

        # Post-process: add 'as Int' for cross-script Float args in item count functions
        # (RemoveItem/AddItem count param should be Int but cross-script may be Float)
        _item_count_re = re.compile(
            r'(\.(RemoveItem|AddItem)\s*\(\s*\w+\s*,\s*)(\w+\.\w+)(\s*\))',
            re.IGNORECASE)
        for idx in range(len(out)):
            m = _item_count_re.search(out[idx])
            if m and ' as Int' not in m.group(3):
                out[idx] = out[idx][:m.start(3)] + m.group(3) + ' as Int' + out[idx][m.end(3):]

        # Post-process: fix comparisons where RHS is entirely a TODO comment
        # e.g. "If expr >= ;TODO: something" → "If True  ;TODO: expr >= something"
        _broken_cmp_re = re.compile(
            r'^(\s*(?:If|ElseIf)\s+)(.+?)\s*(==|!=|>=|<=|>|<)\s*(;TODO:.*)$',
            re.IGNORECASE)
        for idx in range(len(out)):
            m = _broken_cmp_re.match(out[idx])
            if m:
                out[idx] = f'{m.group(1)}True  ;TODO: {m.group(2)} {m.group(3)} {m.group(4)}'

        # Post-process: remove spurious commas from conditions
        # e.g. "if((, expr, == , 1, ))" → "if(expr == 1)"
        for idx in range(len(out)):
            line = out[idx]
            if ',  ==' in line or ', ==' in line or '==,' in line or '== ,' in line:
                # Strip commas that are not inside string literals
                cleaned = re.sub(r',\s*', ' ', line)
                # Collapse multiple spaces
                cleaned = re.sub(r'  +', ' ', cleaned)
                # Restore indentation
                indent = len(line) - len(line.lstrip())
                cleaned = line[:indent] + cleaned.lstrip()
                out[idx] = cleaned

        # Post-process: fix "None as Int/Float" casts (can't cast None to Int/Float)
        # These arise when a TODO function returns None but variable is Int/Float
        for idx in range(len(out)):
            line = out[idx]
            if 'None as Int' in line:
                out[idx] = line.replace('None as Int', '0')
            elif 'None as Float' in line:
                out[idx] = line.replace('None as Float', '0.0')

        # Post-process: promote local variables used across events to properties
        # TES4 locals are script-scoped; Papyrus locals are event-scoped
        _event_re = re.compile(r'^\s*Event\s+(\w+)', re.IGNORECASE)
        _endevent_re = re.compile(r'^\s*EndEvent\b', re.IGNORECASE)
        _local_decl_re = re.compile(r'^(\s*)(Int|Float|Bool|String|ObjectReference|Actor)\s+(\w+)\s*=', re.IGNORECASE)
        _local_use_re = {}  # Populated per-variable below
        # Pass 1: find local declarations and their owning events
        event_locals = {}  # var_name_lower -> (event_name, decl_line_idx, type, indent)
        current_event = None
        for idx, line in enumerate(out):
            em = _event_re.match(line)
            if em:
                current_event = em.group(1)
                continue
            if _endevent_re.match(line):
                current_event = None
                continue
            if current_event:
                dm = _local_decl_re.match(line)
                if dm:
                    vname = dm.group(3)
                    vtype = dm.group(2)
                    event_locals[vname.lower()] = (current_event, idx, vtype, dm.group(1))
        # Pass 2: find variables used in events OTHER than where declared
        promote = {}  # var_name_lower -> (vtype, decl_idx)
        if event_locals:
            for var_low, (decl_event, decl_idx, vtype, indent) in event_locals.items():
                current_event = None
                for idx, line in enumerate(out):
                    em = _event_re.match(line)
                    if em:
                        current_event = em.group(1)
                        continue
                    if _endevent_re.match(line):
                        current_event = None
                        continue
                    if current_event and current_event != decl_event:
                        if re.search(r'\b' + re.escape(var_low) + r'\b', line, re.IGNORECASE):
                            promote[var_low] = (vtype, decl_idx)
                            break
        # Pass 2b: promote variables accessed from OTHER scripts (cross-script access)
        if event_locals and self.xref and _edid_low:
            cross_vars = self.xref.cross_script_vars.get(_edid_low, set())
            for var_low in cross_vars:
                if var_low in event_locals and var_low not in promote:
                    _, decl_idx, vtype, _ = event_locals[var_low]
                    promote[var_low] = (vtype, decl_idx)
        # Promote: remove local declaration, add as property at top
        _promoted_props = []
        for var_low, (vtype, decl_idx) in promote.items():
            # Comment out the local declaration
            out[decl_idx] = f';{out[decl_idx].lstrip()}  ;promoted to property'
            _promoted_props.append((_safe_property_name(var_low), vtype))

        # Insert property declarations for referenced FormIDs
        if self._property_refs or _promoted_props:
            # Collect declared variable names (case-insensitive) to avoid collisions
            declared = {v[0].lower() for v in _var_info}
            insert_idx = 3 + len(_var_info) + (1 if _var_info else 0)
            prop_lines = []
            # Insert promoted local→property declarations first
            for pname, ptype in _promoted_props:
                if pname.lower() not in declared:
                    default = ' = 0.0' if ptype == 'Float' else (' = None' if ptype in ('ObjectReference', 'Actor') else '')
                    prop_lines.append(f'{ptype} Property {pname} Auto')
                    declared.add(pname.lower())
            if self._property_refs:
                prop_lines.append('; --- External references (fill in CK) ---')
                for pname, ptype in sorted(self._property_refs.items()):
                    safe_name = _safe_property_name(pname)
                    if safe_name.lower() in declared:
                        continue  # skip if already declared as a variable
                    declared.add(safe_name.lower())
                    prop_lines.append(f'{ptype} Property {safe_name} Auto')
            prop_lines.append('')
            for i, pl in enumerate(prop_lines):
                out.insert(insert_idx + i, pl)

        return '\n'.join(out)

    def convert_fragment(self, source: str, extends: str = 'Quest') -> list[str]:
        """Convert a script fragment body (not a full script).

        Returns list of converted lines (indented for function body).
        Preserves _property_refs across calls (quest fragments share a converter).
        """
        # Reset conversion state but preserve accumulated property_refs
        saved_refs = dict(self._property_refs)
        self._reset()
        self._property_refs = saved_refs
        lines = source.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        result = []
        for raw_line in lines:
            stripped = raw_line.strip()
            if not stripped:
                result.append('')
                continue
            low = stripped.lower()
            if low.startswith('scriptname ') or low.startswith('scn '):
                continue
            if re.match(r'^(short|long|int|float|ref|reference)\s+\w+', stripped, re.IGNORECASE):
                m = re.match(r'^(short|long|int|float|ref|reference)\s+(\w+)', stripped, re.IGNORECASE)
                if m:
                    ptype = TYPE_MAP.get(m.group(1).lower(), 'Int')
                    vname = m.group(2)
                    if ptype == 'Float':
                        result.append(f'  {ptype} {vname} = 0.0')
                    else:
                        result.append(f'  {ptype} {vname} = 0')
                continue
            if low.startswith('begin ') or low == 'end':
                continue
            result.append(f'  {self._convert_line(raw_line, extends)}')
        # Apply shared post-processes to fragment lines
        result = self._postprocess_lines(result)
        return result

    def _postprocess_lines(self, lines: list[str]) -> list[str]:
        """Shared post-processing for both standalone and fragment scripts."""
        # Fix akActionRef used in events that don't define it
        # TES4 scripts could use GetActionRef across blocks; Papyrus scopes params to events
        _event_re2 = re.compile(r'^\s*Event\s+(\w+)', re.IGNORECASE)
        _endevent_re2 = re.compile(r'^\s*EndEvent\b', re.IGNORECASE)
        _EVENTS_WITH_ACTIONREF = {'ontriggerenter', 'ontrigger', 'onactivate'}
        current_event = None
        has_actionref = False
        for idx in range(len(lines)):
            em = _event_re2.match(lines[idx])
            if em:
                current_event = em.group(1).lower()
                has_actionref = current_event in _EVENTS_WITH_ACTIONREF
                continue
            if _endevent_re2.match(lines[idx]):
                current_event = None
                has_actionref = False
                continue
            if current_event and not has_actionref and 'akActionRef' in lines[idx]:
                # Replace undefined akActionRef with Self
                lines[idx] = lines[idx].replace('akActionRef', 'Self')
        # Fix cross-script Float args in item count functions
        _item_count_re = re.compile(
            r'(\.(RemoveItem|AddItem)\s*\(\s*\w+\s*,\s*)(\w+\.\w+)(\s*\))',
            re.IGNORECASE)
        for idx in range(len(lines)):
            m = _item_count_re.search(lines[idx])
            if m and ' as Int' not in m.group(3):
                lines[idx] = lines[idx][:m.start(3)] + m.group(3) + ' as Int' + lines[idx][m.end(3):]
        # Fix incomplete comparisons where RHS is a TODO
        _broken_cmp_re = re.compile(
            r'^(\s*(?:If|ElseIf)\s+)(.+?)\s*(==|!=|>=|<=|>|<)\s*(;TODO:.*)$',
            re.IGNORECASE)
        for idx in range(len(lines)):
            m = _broken_cmp_re.match(lines[idx])
            if m:
                lines[idx] = f'{m.group(1)}True  ;TODO: {m.group(2)} {m.group(3)} {m.group(4)}'
        # Fix None as Int/Float
        for idx in range(len(lines)):
            line = lines[idx]
            if 'None as Int' in line:
                lines[idx] = line.replace('None as Int', '0')
            elif 'None as Float' in line:
                lines[idx] = line.replace('None as Float', '0.0')
        # Replace TES4-only condition functions used as property accesses
        # e.g. Game.GetPlayer().HasVampireFed == 1 → True  ;TODO: HasVampireFed
        _tes4_only_props = re.compile(
            r'\b(HasVampireFed|GetIsCreature|getClothingValue)\b',
            re.IGNORECASE)
        for idx in range(len(lines)):
            m = _tes4_only_props.search(lines[idx])
            if not m:
                continue
            func_name = m.group(1)
            line = lines[idx]
            indent = len(line) - len(line.lstrip())
            stripped = line.strip()
            # If it's an If/ElseIf condition, replace with True + TODO
            if re.match(r'(?:If|ElseIf)\b', stripped, re.IGNORECASE):
                kw = stripped.split()[0]
                lines[idx] = ' ' * indent + f'{kw} True  ;TODO: {func_name} - No Papyrus equivalent ({stripped})'
            else:
                # Assignment or other statement — comment it out
                lines[idx] = ' ' * indent + f';TODO: {func_name} - No Papyrus equivalent: {stripped}'
        # Fix spurious commas in conditions
        for idx in range(len(lines)):
            line = lines[idx]
            if ',  ==' in line or ', ==' in line or '==,' in line or '== ,' in line:
                cleaned = re.sub(r',\s*', ' ', line)
                cleaned = re.sub(r'  +', ' ', cleaned)
                indent = len(line) - len(line.lstrip())
                lines[idx] = line[:indent] + cleaned.lstrip()
        # Fix Int vs Package/Topic/Form comparisons (TES4 GetCurrentAIPackage → TODO)
        # e.g. "If (0 == SE10GoldenSaintPray2x12)" where SE10GoldenSaintPray2x12 is Package
        _type_mismatch_types = {'Package', 'Topic', 'MiscObject'}
        _cond_re = re.compile(r'^(\s*(?:If|ElseIf)\s+).*\b(\w+)\b.*$', re.IGNORECASE)
        for idx in range(len(lines)):
            m = _cond_re.match(lines[idx])
            if not m:
                continue
            line_stripped = lines[idx].strip()
            # Check if any identifier in the condition is a Package/Topic/MiscObject property
            for ident in re.findall(r'\b([a-zA-Z_]\w*)\b', line_stripped):
                ptype = self._property_refs.get(ident, self._property_refs.get(ident.lower(), ''))
                if ptype in _type_mismatch_types:
                    kw = line_stripped.split()[0]
                    indent = len(lines[idx]) - len(line_stripped)
                    lines[idx] = ' ' * indent + f'{kw} True  ;TODO: Type mismatch fix ({line_stripped})'
                    break
        # Fix integer assignments to cross-script ref-typed variables
        if self.xref:
            _assign_int_re = re.compile(r'^(\s*)([\w.]+)\s*=\s*(-?\d+)\s*(;.*)?$')
            for idx in range(len(lines)):
                m = _assign_int_re.match(lines[idx])
                if not m:
                    continue
                tgt = m.group(2)
                # fQuestDelayTime → RegisterForSingleUpdate (TES4 built-in)
                if tgt.lower().endswith('.fquestdelaytime'):
                    val = m.group(3)
                    fval = float(val) if val != '0' else 0
                    indent = m.group(1)
                    if fval > 0:
                        lines[idx] = f'{indent}RegisterForSingleUpdate({val}.0)  ;fQuestDelayTime'
                    else:
                        lines[idx] = f'{indent}UnregisterForUpdate()  ;fQuestDelayTime = 0'
                    continue
                if '.' in tgt:
                    if self._is_ref_typed_access(tgt):
                        lines[idx] = f'{m.group(1)}{tgt} = None  {m.group(4) or ""}'.rstrip()
        # Fix TODO comments inside function call arguments
        # e.g. "RemoveItem(Gold001, ;TODO: ...)" -> "RemoveItem(Gold001)  ;TODO: ..."
        for idx in range(len(lines)):
            line = lines[idx]
            if ';TODO' not in line or '(' not in line:
                continue
            semi_idx = line.find(';TODO')
            if semi_idx < 0:
                continue
            code_before = line[:semi_idx]
            open_count = code_before.count('(') - code_before.count(')')
            if open_count > 0:
                # TODO is inside unclosed parens - close properly
                todo_text = line[semi_idx:].rstrip().rstrip(')')
                code = code_before.rstrip().rstrip(',').rstrip()
                code += ')' * open_count
                lines[idx] = f'{code}  {todo_text}'
        # Fix Say() with Int variable as Topic arg (TES4 used FormIDs interchangeably)
        for idx in range(len(lines)):
            say_m = re.search(r'\.Say\((\w+)\)', lines[idx])
            if say_m:
                arg = say_m.group(1)
                arg_type = self._var_types.get(arg.lower(), '') or self._property_refs.get(arg, self._property_refs.get(arg.lower(), ''))
                if arg_type == 'Int':
                    indent = len(lines[idx]) - len(lines[idx].lstrip())
                    lines[idx] = ' ' * indent + f';TODO: Say() needs Topic form, not Int ({lines[idx].strip()})'
        # Fix Self assigned to Actor property: Self → Self as Actor
        for idx in range(len(lines)):
            m = re.match(r'^(\s*)(\w+)\s*=\s*Self\s*(;.*)?$', lines[idx])
            if m:
                tgt_low = m.group(2).lower()
                ptype = self._var_types.get(tgt_low, '') or self._property_refs.get(m.group(2), self._property_refs.get(tgt_low, ''))
                if ptype == 'Actor':
                    comment = m.group(3) or ''
                    lines[idx] = f'{m.group(1)}{m.group(2)} = Self as Actor  {comment}'.rstrip()
        # Fix ActorBase vs Script type comparisons (e.g. GetActorBase() == ScriptProperty)
        # TES4 compared refs directly to base objects - in Papyrus, base form needs GetBaseObject()
        for idx in range(len(lines)):
            line = lines[idx]
            if '.GetActorBase()' not in line:
                continue
            comp_m = re.search(r'(GetActorBase\(\))\s*(==|!=)\s*(\w+)', line)
            if comp_m:
                rhs = comp_m.group(3)
                ptype = self._property_refs.get(rhs, self._property_refs.get(rhs.lower(), ''))
                if ptype and ptype.startswith('TES4_'):
                    # Replace GetActorBase() == ScriptProp with GetActorBase() == ScriptProp.GetActorBase()
                    indent = len(line) - len(line.lstrip())
                    kw = line.strip().split()[0]
                    lines[idx] = ' ' * indent + f'{kw} True  ;TODO: ActorBase vs Script type ({line.strip()})'
        # Fix ObjectReference vs MiscObject/Ingredient/etc comparisons
        # TES4: "akActionRef == SomeMiscObject" → Papyrus: "akActionRef.GetBaseObject() == SomeMiscObject"
        _base_form_types = {'MiscObject', 'Ingredient', 'Potion', 'Weapon', 'Armor', 'Book', 'Key'}
        for idx in range(len(lines)):
            line = lines[idx]
            comp_m = re.search(r'\b(akActionRef|\w+Ref)\s*(==|!=)\s*(\w+)', line)
            if comp_m:
                rhs = comp_m.group(3)
                ptype = self._property_refs.get(rhs, self._property_refs.get(rhs.lower(), ''))
                if ptype in _base_form_types:
                    old_expr = comp_m.group(0)
                    new_expr = f'{comp_m.group(1)}.GetBaseObject() {comp_m.group(2)} {rhs}'
                    lines[idx] = line.replace(old_expr, new_expr)
        return lines

    def get_property_refs(self) -> dict[str, str]:
        """Get accumulated external property references."""
        return dict(self._property_refs)

    # -----------------------------------------------------------------------
    # Private
    # -----------------------------------------------------------------------

    def _reset(self):
        self._property_refs = {}
        self._has_gamemode = False
        self._has_menumode = False
        self._has_scripteffectupdate = False
        self._uses_getsecondspassed = False
        self._uses_timer = False
        self._local_vars = set()
        self._var_renames = {}
        self._var_types = {}

    @staticmethod
    def _balance_if_endif(lines: list[str]) -> list[str]:
        """Balance If/EndIf within event/function blocks.

        Remove extra EndIf/Else/ElseIf that don't have matching If.
        Insert missing EndIf before EndEvent/EndFunction.
        """
        result = []
        depth = 0
        in_event = False
        for line in lines:
            stripped = line.strip().lower()
            # Strip inline comments for keyword matching
            code_part = stripped.split(';')[0].strip() if ';' in stripped else stripped
            if code_part.startswith('event ') or code_part.startswith('function '):
                in_event = True
                depth = 0
            elif code_part in ('endevent', 'endfunction'):
                # Insert missing EndIf statements before closing
                while depth > 0:
                    result.append('EndIf')
                    depth -= 1
                in_event = False
                depth = 0
            elif in_event:
                if code_part.startswith('if ') or code_part.startswith('if(') or code_part == 'if':
                    depth += 1
                elif code_part.startswith('elseif '):
                    if depth <= 0:
                        continue  # orphaned ElseIf
                elif code_part == 'else':
                    if depth <= 0:
                        continue  # orphaned Else
                elif code_part == 'endif':
                    if depth <= 0:
                        # Extra EndIf — skip it
                        continue
                    depth -= 1
            result.append(line)
        return result

    @staticmethod
    def _remove_dead_code_after_return(lines: list[str]) -> list[str]:
        """Comment out executable code after Return at event/function top-level."""
        result = []
        in_dead_zone = False
        depth = 0  # if/while nesting depth
        for line in lines:
            stripped = line.strip().lower()
            if stripped.startswith('event ') or stripped.startswith('function '):
                in_dead_zone = False
                depth = 0
            elif stripped in ('endevent', 'endfunction'):
                in_dead_zone = False
                depth = 0
            elif in_dead_zone:
                # Allow empty lines and comments through
                if stripped and not stripped.startswith(';'):
                    result.append(f';  {line.strip()}  ;dead code after Return')
                    continue
            else:
                # Track nesting
                if stripped.startswith('if ') or stripped == 'if':
                    depth += 1
                elif stripped == 'endif':
                    depth = max(0, depth - 1)
                elif stripped.startswith('while '):
                    depth += 1
                elif stripped == 'endwhile':
                    depth = max(0, depth - 1)
                elif stripped == 'return' and depth == 0:
                    in_dead_zone = True
            result.append(line)
        return result

    def _get_update_interval(self) -> str:
        if self._uses_getsecondspassed:
            return '0.1'
        if self._uses_timer:
            return '0.25'
        return '0.5'

    def _parse_source(self, source: str):
        """Parse Oblivion source into (variables, blocks)."""
        lines = source.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        variables = []
        blocks = []
        current_block = None
        current_lines = []

        for raw_line in lines:
            stripped = raw_line.strip()
            if not stripped or stripped.startswith(';'):
                if current_block is not None:
                    current_lines.append(raw_line)
                continue

            low = stripped.lower()

            if low.startswith('scriptname ') or low.startswith('scn '):
                continue

            # Variable declarations (top-level only)
            if current_block is None:
                m = re.match(r'^(short|long|int|float|ref|reference)\s+(\w+)', stripped, re.IGNORECASE)
                if m:
                    variables.append((m.group(1).lower(), m.group(2)))
                    continue

            begin_m = re.match(r'^begin\s+(\w+)(.*)', stripped, re.IGNORECASE)
            if begin_m:
                current_block = begin_m.group(1).lower()
                current_lines = []
                continue

            if low == 'end':
                if current_block is not None:
                    blocks.append((current_block, current_lines))
                    current_block = None
                    current_lines = []
                continue

            if current_block is not None:
                current_lines.append(raw_line)

        return variables, blocks

    def _convert_line(self, line: str, extends: str) -> str:
        """Convert a single Oblivion script line to Papyrus."""
        stripped = line.strip()
        if not stripped:
            return ''
        if stripped.startswith(';'):
            return stripped

        # Strip inline comments (;) that aren't inside string literals
        # TES4 uses ; for comments, these must not leak into Papyrus expressions
        inline_comment = ''
        in_str = False
        for ci, ch in enumerate(stripped):
            if ch == '"':
                in_str = not in_str
            elif ch == ';' and not in_str:
                inline_comment = '  ' + stripped[ci:]
                stripped = stripped[:ci].rstrip()
                break

        if not stripped:
            return inline_comment.strip() if inline_comment else ''

        result = self._convert_line_inner(stripped, extends)
        if inline_comment and not result.lstrip().startswith(';'):
            return result + inline_comment
        return result

    def _convert_line_inner(self, stripped: str, extends: str) -> str:
        """Core line conversion logic (no inline-comment handling)."""
        low = stripped.lower()

        # Variable declarations inside blocks -> local vars
        var_m = re.match(r'^(short|long|int|float|ref|reference)\s+(\w+)', stripped, re.IGNORECASE)
        if var_m:
            ptype = TYPE_MAP.get(var_m.group(1).lower(), 'Int')
            vname = var_m.group(2)
            # Track local variable types for type-aware assignment
            self._var_types[vname.lower()] = ptype
            self._local_vars.add(vname.lower())
            if ptype == 'ObjectReference':
                return f'{ptype} {vname} = None'
            if ptype == 'Float':
                return f'{ptype} {vname} = 0.0'
            return f'{ptype} {vname} = 0'

        # set X to Y
        set_m = re.match(r'^set\s+(\S+)\s+to\s+(.*)', stripped, re.IGNORECASE)
        if set_m:
            target = self._convert_ref(set_m.group(1), extends)
            value = self._convert_expression(set_m.group(2), extends)
            # Can't assign to Self in Papyrus
            if target == 'Self':
                return f';Self = {value}  ;cannot assign to Self in Papyrus'
            # In AME/TopicInfo scripts, Self refers to the target actor, not the script
            if value == 'Self':
                if extends == 'ActiveMagicEffect':
                    value = 'GetTargetActor()'
                elif extends == 'TopicInfo':
                    value = 'akSpeakerRef'
            if value.lstrip().startswith(';TODO:'):
                # Use None for ref-typed targets, 0 for others
                tgt_low_todo = target.lower().split('.')[-1]
                tgt_type_todo = self._var_types.get(tgt_low_todo, '') or self._property_refs.get(target, self._property_refs.get(tgt_low_todo, ''))
                if tgt_type_todo == 'GlobalVariable':
                    return f'{target}.SetValue(0)  {value}'
                dflt = 'None' if tgt_type_todo in ('ObjectReference', 'Actor', 'ActorBase') or tgt_type_todo.startswith('TES4_') else '0'
                return f'{target} = {dflt}  {value}'
            value = self._fix_ref_zero(target, value)
            # Say() returns None in Papyrus but TES4 returned audio duration
            if '.Say(' in value or value.startswith('Say('):
                # Extract the Say() call using balanced-paren matching
                # TES4: "set timer to (ref.Say topic args) + delay" → "(ref.Say(topic)) + 0.2"
                say_idx = value.find('.Say(')
                if say_idx < 0:
                    say_idx = value.find('Say(')
                if say_idx >= 0:
                    # Find the start of the Say expression (back up to find ref or outer paren)
                    # The Say call starts at say_idx (for .Say) or the ref before it
                    paren_start = value.index('(', say_idx)
                    depth = 0
                    paren_end = paren_start
                    for ci in range(paren_start, len(value)):
                        if value[ci] == '(':
                            depth += 1
                        elif value[ci] == ')':
                            depth -= 1
                            if depth == 0:
                                paren_end = ci
                                break
                    # say_call is everything from expression start to closing paren
                    # Find start: go back from say_idx to find the ref or opening paren
                    expr_start = 0
                    for ci in range(say_idx - 1, -1, -1):
                        ch = value[ci]
                        if ch in ' \t(' and ci < say_idx - 1:
                            expr_start = ci + 1
                            break
                    say_call = value[expr_start:paren_end + 1]
                    # Strip balanced outer wrapping parens: "(ref.Say(topic))" → "ref.Say(topic)"
                    if say_call.startswith('(') and say_call.endswith(')'):
                        inner = say_call[1:-1]
                        # Only strip if inner parens are balanced (i.e. outer were wrapping)
                        d = 0
                        balanced = True
                        for ch in inner:
                            if ch == '(':
                                d += 1
                            elif ch == ')':
                                d -= 1
                                if d < 0:
                                    balanced = False
                                    break
                        if balanced and d == 0:
                            say_call = inner
                    remainder = value[paren_end + 1:].strip()
                    # If remainder is just "+ number", extract the delay for the timer
                    delay_m = re.match(r'[+\-]\s*([\d.]+)', remainder) if remainder else None
                    delay_val = delay_m.group(1) if delay_m else '0.0'
                    return f'{say_call}\n  {target} = {delay_val}  ;Say() returns None in Papyrus; delay approximated'
                return f'{value}\n  {target} = 0.0  ;Say() returns None in Papyrus'
            # GlobalVariable: use SetValue() instead of direct assignment
            tgt_low = target.lower().split('.')[-1]
            if self._property_refs.get(target, self._property_refs.get(tgt_low, '')) == 'GlobalVariable':
                # Strip inline TODO comments from value to avoid broken parentheses
                val_clean = value.split(';TODO')[0].rstrip() if ';TODO' in value else value
                todo_part = '  ;TODO' + value.split(';TODO', 1)[1] if ';TODO' in value else ''
                return f'{target}.SetValue({val_clean}){todo_part}'
            # Float→Int coercion: if target is Int and value is from a Float-returning function, cast
            value = self._coerce_float_to_int(target, value)
            # ObjectReference→Actor coercion: if target is Actor and value is ObjectReference param, cast
            value = self._coerce_ref_to_actor(target, value)
            return f'{target} = {value}'

        # let X := Y (OBSE)
        let_m = re.match(r'^let\s+(\S+)\s*:=\s*(.*)', stripped, re.IGNORECASE)
        if let_m:
            target = self._convert_ref(let_m.group(1), extends)
            value = self._convert_expression(let_m.group(2), extends)
            if value.lstrip().startswith(';TODO:'):
                tgt_low_todo = target.lower().split('.')[-1]
                tgt_type_todo = self._var_types.get(tgt_low_todo, '') or self._property_refs.get(target, self._property_refs.get(tgt_low_todo, ''))
                dflt = 'None' if tgt_type_todo in ('ObjectReference', 'Actor', 'ActorBase') or tgt_type_todo.startswith('TES4_') else '0'
                return f'{target} = {dflt}  {value}'
            value = self._fix_ref_zero(target, value)
            return f'{target} = {value}'

        # if / elseif
        if_m = re.match(r'^(if|elseif)\s+(.*)', stripped, re.IGNORECASE)
        if if_m:
            keyword = 'If' if if_m.group(1).lower() == 'if' else 'ElseIf'
            condition = self._convert_expression(if_m.group(2), extends)
            # If the condition converted entirely to a ;TODO comment, keep the
            # block structure valid by using True as placeholder
            if condition.lstrip().startswith(';TODO:'):
                return f'{keyword} True  {condition}'
            return f'{keyword} {condition}'

        if low == 'else':
            return 'Else'
        # TES4 allows "else <condition>" as equivalent to "elseif <condition>"
        if low.startswith('else ') and not low.startswith('elseif'):
            rest = stripped[5:].strip()
            if rest:
                # TES4 also allows "else if <cond>" — strip leading 'if' keyword
                rest_low = rest.lower()
                if rest_low.startswith('if '):
                    rest = rest[3:].strip()
                condition = self._convert_expression(rest, extends)
                if condition.lstrip().startswith(';TODO:'):
                    return f'ElseIf True  {condition}'
                return f'ElseIf {condition}'
            return 'Else'
        if low == 'endif' or low.startswith('endif') and not low[5:6].isalpha():
            return 'EndIf'
        if low == 'return':
            return 'Return'
        # return followed by a comment or anything (TES4 return has no value)
        if low.startswith('return ') or low.startswith('return;'):
            rest = stripped[6:].strip()
            if rest.startswith(';'):
                return f'Return  {rest}'
            value = self._fix_ref_zero(target, value)
            return f'{target} = {value}'

        return self._convert_function_call(stripped, extends)

    def _fix_ref_zero(self, target: str, value: str) -> str:
        """If target is a ref-typed variable and value is an integer literal, return 'None'.

        TES4 scripts often use ref vars as boolean flags (set refVar to 0/1).
        In Papyrus, Actor/ObjectReference cannot hold integers, so convert to None.
        """
        val_stripped = value.strip()
        if not re.match(r'^-?\d+$', val_stripped):
            return value
        # Check local/declared var type first (takes priority)
        tgt_low = target.lower().split('.')[-1]  # handle quest.var as var
        vtype = self._var_types.get(tgt_low, '')
        if vtype in ('ObjectReference', 'Actor', 'ActorBase') or vtype.startswith('TES4_'):
            return 'None'
        if vtype:
            return value  # Known non-ref type, don't convert
        # Check property refs (cross-script variables) only if not a declared var
        ptype = self._property_refs.get(target, self._property_refs.get(tgt_low, ''))
        if ptype in ('ObjectReference', 'Actor', 'ActorBase') or ptype.startswith('TES4_'):
            # But if the cross-script var was retyped to Int via ref_as_int, keep integer
            if '.' in target and self.xref and self._is_ref_as_int_crossscript(target):
                return value  # retyped to Int, keep integer
            return 'None'
        # Check cross-script ref type via xref graph (e.g. MQ00.nearOblivionGate)
        if '.' in target:
            parts = target.split('.', 1)
            if self.xref and self.xref.is_remote_ref_var(parts[0], parts[1]):
                if self._is_ref_as_int_crossscript(target):
                    return value
                return 'None'
            # Also check via property type → script_all_vars (property name != EditorID)
            if self._is_ref_typed_access(target):
                if self._is_ref_as_int_crossscript(target):
                    return value
                return 'None'
        return value

    # Functions that return Float in Papyrus (need 'as Int' cast when assigned to Int vars)
    _FLOAT_RETURNING_FUNCS = re.compile(
        r'(?:GetBaseActorValue|GetActorValue|GetAV|GetSecondsPassed|GetDistance|'
        r'GetPosition[XYZ]|GetAngle[XYZ]|GetHeadingAngle|GetScale|GetLevel|'
        r'GetPos[XYZ]|GetWalkSpeed|GetCurrentTime|RandomFloat|Utility\.RandomFloat|'
        r'GetHeight|GetWidth|GetLength)\s*\(', re.IGNORECASE)

    def _coerce_float_to_int(self, target: str, value: str) -> str:
        """Add 'as Int' cast when assigning Float-returning function to Int variable."""
        tgt_low = target.lower().split('.')[-1]
        vtype = self._var_types.get(tgt_low, '')
        if vtype != 'Int':
            return value
        if self._FLOAT_RETURNING_FUNCS.search(value):
            return f'{value} as Int'
        # Also detect float literals in arithmetic (e.g. X * 0.8, -50 * 0.5)
        if re.search(r'\d+\.\d+', value):
            return f'({value}) as Int'
        # Detect Float variables in arithmetic expressions (e.g. totalTime - timer / 60)
        if re.search(r'[+\-*/]', value):
            # Check if any identifier in the expression is a Float variable
            for ident in re.findall(r'\b([a-zA-Z_]\w*)\b', value):
                id_type = self._var_types.get(ident.lower(), '')
                if not id_type:
                    id_type = self._property_refs.get(ident, self._property_refs.get(ident.lower(), ''))
                if id_type == 'Float':
                    return f'({value}) as Int'
        # Bool→Int coercion: functions like IsDetectedBy return Bool, TES4 assigns to Int
        if re.search(r'\.(?:IsDetectedBy|HasLOS|CanSee|IsInDialogueWithPlayer|'
                     r'IsRidingMount|IsInCombat|IsAnimPlaying|GetDetected)\s*\(', value, re.IGNORECASE):
            return f'{value} as Int'
        return value

    # ObjectReference event parameter names that may need Actor cast
    _OBJREF_PARAMS = {'akactionref', 'aknewcontainer', 'akoldcontainer', 'akcastref',
                      'akactionref', 'akaggressor', 'akcaster'}

    # Functions that return ObjectReference in Papyrus
    _OBJREF_RETURNING = re.compile(
        r'(?:GetLinkedRef|PlaceAtMe|GetParentRef|PlaceActorAtMe|GetEditorLocation|'
        r'GetItemInSlot|GetCombatTarget)\s*\(', re.IGNORECASE)

    def _coerce_ref_to_actor(self, target: str, value: str) -> str:
        """Add 'as Actor' cast when assigning ObjectReference to Actor variable."""
        val_stripped = value.strip()
        val_low = val_stripped.lower()
        # Check if value is an ObjectReference event param, an ObjRef-returning function,
        # or the bare 'akActionRef' identifier
        is_objref_value = (
            val_low in self._OBJREF_PARAMS
            or self._OBJREF_RETURNING.search(val_stripped)
            or val_low == 'akactionref'
        )
        # Also check cross-script property access returning ObjectReference
        if not is_objref_value and '.' in val_stripped:
            is_objref_value = self._is_ref_typed_access(val_stripped)
        if not is_objref_value:
            return value
        tgt_low = target.lower().split('.')[-1]
        vtype = self._var_types.get(tgt_low, '')
        if vtype in ('Actor', 'ActorBase') or vtype.startswith('TES4_'):
            return f'{value} as Actor'
        # Check property refs too
        ptype = self._property_refs.get(target, self._property_refs.get(tgt_low, ''))
        if ptype in ('Actor', 'ActorBase') or (ptype and ptype.startswith('TES4_')):
            return f'{value} as Actor'
        return value

        # Direct assignment: X.Y = Z or X = Z (OBSE-style, no 'set' prefix)
        assign_m = re.match(r'^(\S+)\s*=\s*(.*)', stripped)
        if assign_m:
            target = self._convert_ref(assign_m.group(1), extends)
            value = self._convert_expression(assign_m.group(2), extends)
            return f'{target} = {value}'

        return self._convert_function_call(stripped, extends)

    @staticmethod
    def _split_logical(expr: str, op: str) -> list[str] | None:
        """Split *expr* on a logical operator (``||`` or ``&&``) only at
        top-level — i.e. not inside parentheses.  Returns ``None`` if the
        operator does not appear at top level.
        """
        parts: list[str] = []
        depth = 0
        start = 0
        i = 0
        while i < len(expr):
            c = expr[i]
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
            elif depth == 0 and expr[i:i+len(op)] == op:
                parts.append(expr[start:i])
                start = i + len(op)
                i += len(op)
                continue
            i += 1
        if len(parts) == 0:
            return None
        parts.append(expr[start:])
        return parts

    def _convert_expression(self, expr: str, extends: str) -> str:
        """Convert an Oblivion expression to Papyrus."""
        expr = expr.strip()
        if not expr:
            return expr

        # Quoted EditorID → property ref (TES4 allows quoting form names)
        if len(expr) > 2 and expr[0] == '"' and expr[-1] == '"':
            inner_name = expr[1:-1]
            fid = self.xref.edid_to_formid.get(inner_name.lower(), '')
            if fid:
                rtype = self.xref.record_type.get(fid, '')
                ptype = _record_type_to_papyrus(rtype)
                script_type = self.xref.get_record_script_type(inner_name)
                if script_type:
                    ptype = script_type
                safe = _safe_property_name(inner_name)
                self._property_refs[safe] = ptype
                if ptype == 'GlobalVariable':
                    return f'{safe}.GetValue() as Int'
                return safe

        # Strip balanced outer parens and recurse — TES4 conditions always
        # wrap in parens e.g. "( GetStage Quest >= 10 )" which blocks regex
        if expr.startswith('(') and expr.endswith(')'):
            depth = 0
            balanced = True
            for i, c in enumerate(expr):
                if c == '(': depth += 1
                elif c == ')': depth -= 1
                if depth == 0 and i < len(expr) - 1:
                    balanced = False
                    break
            if balanced:
                inner = self._convert_expression(expr[1:-1].strip(), extends)
                # If inner contains a ;TODO comment, close parens before the comment
                if ';TODO:' in inner or ';TODO ' in inner:
                    semi_idx = inner.index(';TODO')
                    code_part = inner[:semi_idx].rstrip()
                    comment_part = inner[semi_idx:]
                    if not code_part:
                        # Entirely TODO'd — pass through as bare TODO
                        return comment_part
                    return f'({code_part}){comment_part}'
                return f'({inner})'

        expr = expr.replace('<>', '!=')

        # Fix spaces around dots in method chains (e.g. "Player. GetItemCount" → "Player.GetItemCount")
        expr = re.sub(r'(\w)\.\s+(\w)', r'\1.\2', expr)

        # Split on logical operators first, convert each part independently
        # Handle || and && — paren-aware to avoid splitting inside subexprs
        or_parts = self._split_logical(expr, '||')
        if or_parts is not None:
            converted = [self._convert_expression(p.strip(), extends) for p in or_parts]
            if any(';TODO:' in c for c in converted):
                return f';TODO: {expr}  ;partially unconvertible'
            return ' || '.join(converted)
        and_parts = self._split_logical(expr, '&&')
        if and_parts is not None:
            converted = [self._convert_expression(p.strip(), extends) for p in and_parts]
            if any(';TODO:' in c for c in converted):
                return f';TODO: {expr}  ;partially unconvertible'
            return ' && '.join(converted)

        # TES4 boolean functions compared to 1/0 (e.g., "IsActionRef player == 1", "ref.GetIsRace Argonian == 1")
        _BOOL_FUNC_NAMES = (
            r'IsActionRef|GetDead|IsDead|IsInCombat|IsSneaking|IsWeaponOut|IsSwimming|'
            r'IsGhost|GetLocked|IsEnabled|HasSpell|GetInFaction|GetQuestRunning|GetStageDone|'
            r'GetDetected|IsActorDetected|GetIsID|GetIsRace|GetPCIsRace|GetIsRef|'
            r'GetInCell|GetInSameCell|GetIsSex|IsInFaction|IsEssential|IsInInterior|'
            r'GetIsCurrentPackage|IsOwner|GetTalkedToPCParam|GetTalkedToPC|'
            r'IsActorUsingATorch|IsRidingHorse')
        bool_comp_m = re.match(
            r'^(?:(\w+)\.)?' + r'(' + _BOOL_FUNC_NAMES + r')(?:\s+(.+?))?\s*==\s*([01])\s*$',
            expr, re.IGNORECASE)
        if bool_comp_m:
            ref_part = bool_comp_m.group(1)
            fname = bool_comp_m.group(2)
            args_part = bool_comp_m.group(3) or ''
            bool_val = bool_comp_m.group(4)
            converted_call = self._emit_function(ref_part, fname, args_part, extends)
            # If function converted to TODO, propagate it
            if converted_call.lstrip().startswith(';TODO'):
                return converted_call
            if bool_val == '0':
                return f'!({converted_call})'
            return converted_call

        # Handle comparison operators: split into LHS op RHS (depth-aware)
        # Scan left-to-right for comparison operators at paren depth 0
        comp_m = None
        _depth = 0
        for _ci in range(len(expr)):
            ch = expr[_ci]
            if ch == '(':
                _depth += 1
            elif ch == ')':
                _depth -= 1
            elif _depth == 0:
                # Check for 2-char operators first (==, !=, >=, <=)
                two = expr[_ci:_ci+2]
                if two in ('==', '!=', '>=', '<='):
                    comp_m = (expr[:_ci].strip(), two, expr[_ci+2:].strip())
                    break
                # Single-char: > or < (but not part of >= or <=)
                if ch in ('>', '<') and (_ci + 1 >= len(expr) or expr[_ci+1] != '='):
                    comp_m = (expr[:_ci].strip(), ch, expr[_ci+1:].strip())
                    break
        if comp_m:
            lhs = self._convert_expression(comp_m[0], extends)
            op = comp_m[1]
            rhs = self._convert_expression(comp_m[2], extends)
            # If LHS is entirely a TODO comment, propagate it
            if lhs.lstrip().startswith(';TODO'):
                return lhs
            # If LHS contains an inline TODO comment, extract code part for comparison
            if ';TODO' in lhs:
                semi_idx = lhs.index(';TODO')
                code_part = lhs[:semi_idx].rstrip()
                comment_part = lhs[semi_idx:]
                return f'{code_part} {op} {rhs}  {comment_part}'
            # Fix ref == 0 / ref != 0 → ref == None / ref != None
            if rhs.strip() == '0' and op in ('==', '!='):
                lhs_var = lhs.strip().lower().split('.')[-1]
                lhs_type = self._var_types.get(lhs_var, '') or self._property_refs.get(lhs_var, '')
                is_ref = lhs_type in ('ObjectReference', 'Actor', 'ActorBase') or lhs_type.startswith('TES4_')
                # Self is always a ref type
                if not is_ref and lhs.strip() == 'Self':
                    is_ref = True
                # Also check cross-script ref type (e.g. MQ00.nearOblivionGate == 0)
                if not is_ref and '.' in lhs.strip():
                    is_ref = self._is_ref_typed_access(lhs.strip())
                if is_ref:
                    rhs = 'None'
            # Reversed: 0 == ref / 0 != ref → None == ref / None != ref
            if lhs.strip() == '0' and op in ('==', '!='):
                rhs_var = rhs.strip().lower().split('.')[-1]
                rhs_type = self._var_types.get(rhs_var, '') or self._property_refs.get(rhs_var, '')
                is_ref = rhs_type in ('ObjectReference', 'Actor', 'ActorBase') or rhs_type.startswith('TES4_')
                if not is_ref and rhs.strip() == 'Self':
                    is_ref = True
                if not is_ref and '.' in rhs.strip():
                    is_ref = self._is_ref_typed_access(rhs.strip())
                if is_ref:
                    lhs = 'None'
            # ref > 0 / ref >= 1 → ref != None (null check pattern for ref types)
            if rhs.strip() in ('0', '1') and op in ('>', '>='):
                lhs_var = lhs.strip().lower().split('.')[-1]
                lhs_type = self._var_types.get(lhs_var, '') or self._property_refs.get(lhs_var, '')
                is_ref = lhs_type in ('ObjectReference', 'Actor', 'ActorBase') or lhs_type.startswith('TES4_')
                if not is_ref and '.' in lhs.strip():
                    is_ref = self._is_ref_typed_access(lhs.strip())
                if not is_ref and '.' in lhs.strip():
                    parts = lhs.strip().split('.', 1)
                    if self.xref and self.xref.is_remote_ref_var(parts[0], parts[1]):
                        is_ref = True
                if is_ref:
                    return f'{lhs} != None'
            # TES4 boolean comparison: (comparison_expr) == 1 → comparison_expr
            # In TES4, comparisons return 0/1 so "== 1" checks truth, "== 0" checks false
            if op in ('==', '!=') and rhs.strip() in ('0', '1'):
                # Check if LHS already contains a comparison (implying boolean result)  
                inner = lhs.strip()
                if inner.startswith('(') and inner.endswith(')'):
                    inner_content = inner[1:-1]
                    if re.search(r'\s(==|!=|>=|<=|>|<)\s', inner_content):
                        if (op == '==' and rhs.strip() == '1') or (op == '!=' and rhs.strip() == '0'):
                            return lhs  # Already a boolean, == 1 is redundant
                        if (op == '==' and rhs.strip() == '0') or (op == '!=' and rhs.strip() == '1'):
                            return f'!{lhs}'  # Negate
            # Bool function compared to 0/1: IsDisabled() == 0 → !IsDisabled()
            if op in ('==', '!=') and rhs.strip() in ('0', '1'):
                inner = lhs.strip()
                # Match ref.Func() or Func() patterns
                bool_call_m = re.match(
                    r'^(?:.*\.)?('
                    r'Is(?:Disabled|Enabled|Dead|InCombat|Sneaking|WeaponOut|Swimming|Ghost|'
                    r'InInterior|Essential|Guard|ActionRef|ChildOf|InDialogueWithPlayer|'
                    r'Running|InFaction|Arrested|BleedingOut|UnconscIous|Commanded|'
                    r'PlayerTeammate|Hostile|Sprinting|OnMount|Alerted|EquipPed|'
                    r'Mounted|Trespassing|AVRecoveryDisabled|FurnitureInUse|FlightBlocked)|'
                    r'Get(?:Dead|Disabled|Locked|Ghost|IsAlerted|InCombat|NoBleedoutRecovery|'
                    r'IsPlayableRace|CurrentWeatherPercent|IsCurrentPackage)|'
                    r'Has(?:Spell|MagicEffect|Perk|EffectKeyword|KeyWord|Node|LOSToRef|RefType)|'
                    r'(?:IsInterior|IsInInterior|WornHasKeyword|PathToReference)'
                    r')\s*\(', inner, re.IGNORECASE)
                if bool_call_m:
                    if (op == '==' and rhs.strip() == '1') or (op == '!=' and rhs.strip() == '0'):
                        return lhs  # Already bool, == 1 is redundant
                    if (op == '==' and rhs.strip() == '0') or (op == '!=' and rhs.strip() == '1'):
                        return f'!({lhs})'  # Negate
            return f'{lhs} {op} {rhs}'

        # Handle arithmetic operators at top level (+, -, *, /)
        # Scan right-to-left for + and - (lowest precedence), then * and /
        # This lets recursive conversion handle each operand properly
        for ops in (('+', '-'), ('*', '/', '%')):
            depth = 0
            best_i = -1
            for i in range(len(expr) - 1, 0, -1):  # right-to-left, skip pos 0 (unary)
                c = expr[i]
                if c == ')': depth += 1
                elif c == '(': depth -= 1
                elif depth == 0 and c in ops:
                    # Don't split on +/- that are part of a number (.2, 1e+5)
                    # or are unary (preceded by operator or open paren)
                    # Look back past whitespace to find the significant prev char
                    sig_prev = ' '
                    for k in range(i - 1, -1, -1):
                        if expr[k] not in ' \t':
                            sig_prev = expr[k]
                            break
                    if sig_prev in '(,=<>!+-*/%':
                        continue
                    best_i = i
                    break
            if best_i > 0:
                lhs = self._convert_expression(expr[:best_i].strip(), extends)
                op = expr[best_i]
                rhs = self._convert_expression(expr[best_i+1:].strip(), extends)
                return f'{lhs} {op} {rhs}'

        # Handle function calls in expressions: "funcname arg1 arg2"
        # Route through _emit_function for special-case handling
        func_in_expr = re.match(r'^(\w+)\s+(.+)$', expr)
        if func_in_expr:
            fname = func_in_expr.group(1).lower()
            if fname in FUNCTION_MAP or fname in ('getstage', 'getstagedone', 'setstage',
                    'startquest', 'stopquest', 'getquestrunning', 'getrandompercent',
                    'completequest', 'isquestcompleted',
                    'getpos', 'getangle', 'setpos', 'setangle', 'getstartingangle', 'getself',
                    'getactionref', 'isactionref', 'message', 'messagebox',
                    'getisid', 'getisrace', 'getpcisrace', 'getisref',
                    'getincell', 'getinsamecell', 'getissex', 'getcrimeknown',
                    'sme', 'pme', 'setdisplayname', 'placeatme',
                    'createfullactorcopy', 'wakeuppc', 'isexpelled',
                    'getcontainer', 'getbookread', 'bookread', 'showclassmenu',
                    'showbirthsignmenu', 'showracemenu', 'setinchargen',
                    'setplayerinseworld', 'forcecloseobliviongate',
                    'closecurrentobliviongate', 'isinfaction'):
                return self._emit_function(None, func_in_expr.group(1),
                                           func_in_expr.group(2).strip(), extends)

        # Handle ref.Func in expressions (only if no parens yet — avoid re-matching)
        # Require ref to start with a letter (not digit) to avoid matching floats like 0.5
        ref_func = re.match(r'^([a-zA-Z_]\w*)\.(\w+)\s*((?:[^(].*)?)', expr)
        if ref_func and '(' not in ref_func.group(2):
            args_rest = ref_func.group(3).strip()
            ref_name = ref_func.group(1)
            prop_name = ref_func.group(2)
            prop_low = prop_name.lower()
            # Sanitize property name to avoid Papyrus reserved word collisions
            safe_prop = _safe_property_name(prop_name)
            # If "args" starts with arithmetic op, it's property access not function call
            # e.g. Quest.Var + 1 -> Quest.Var + 1, not Quest.Var(+, 1)
            if args_rest and args_rest[0] in '+-*/%':
                ref = self._convert_ref(ref_name, extends)
                rest = self._convert_expression(args_rest, extends)
                return f'{ref}.{safe_prop} {rest}'
            # If "args" starts with comparison op, it's also a property comparison
            # e.g. Quest.Var > 0, Quest.Var == 1
            # BUT: if prop is a known function, route through _emit_function instead
            if args_rest and re.match(r'^(==|!=|>=|<=|>|<)\s*', args_rest):
                if prop_low in FUNCTION_MAP or prop_low in _BARE_BOOL_FUNCTIONS or prop_low in (
                        'isininterior', 'isanimplaying', 'getparentcell',
                        'getdead', 'isdead', 'getdisabled', 'isdisabled',
                        'isinfaction', 'isessential', 'getisrace',
                        'getisid', 'getissex', 'getincell', 'getinsamecell',
                        'isactionref', 'getactionref', 'getisplayablerace',
                        'isactorusingatorch', 'getdetected', 'isdetectedby',
                        'getismurderer', 'isguard', 'getnosneakwaterpenalty',
                        'getstartingangle', 'getstartingpos'):
                    # It's a function call followed by comparison — split and handle
                    comp = re.match(r'^(==|!=|>=|<=|>|<)\s*(.*)', args_rest)
                    func_result = self._emit_function(ref_name, prop_name, '', extends)
                    if comp:
                        rhs = self._convert_expression(comp.group(2).strip(), extends)
                        return f'{func_result} {comp.group(1)} {rhs}'
                    return func_result
                ref = self._convert_ref(ref_name, extends)
                rest = self._convert_expression(args_rest, extends)
                return f'{ref}.{safe_prop} {rest}'
            # If ref is a known quest and prop is NOT a known function, treat as property access
            if self.xref.is_quest_ref(ref_name) and prop_low not in FUNCTION_MAP and prop_low not in (
                    'getstage', 'setstage', 'getstagedone', 'start', 'stop', 'isrunning',
                    'iscompleted', 'completequest', 'setstage', 'getstage'):
                ref = self._convert_ref(ref_name, extends)
                if args_rest:
                    rest = self._convert_expression(args_rest, extends)
                    return f'{ref}.{safe_prop} {rest}'
                return f'{ref}.{safe_prop}'
            # No args and not a known function — treat as property access
            # e.g. NpcRef.someVar (cross-script variable)
            if not args_rest and prop_low not in FUNCTION_MAP and prop_low not in _BARE_BOOL_FUNCTIONS and prop_low not in (
                    'getstage', 'setstage', 'getstagedone', 'start', 'stop', 'isrunning',
                    'iscompleted', 'completequest', 'evaluatepackage', 'enable', 'disable',
                    'delete', 'activate', 'reset', 'kill', 'resurrect', 'moveto',
                    'getparentcell', 'getself', 'getactionref', 'getlinkedref',
                    'getparentref', 'getbaseobject', 'getactorbase',
                    'isactorusingatorch', 'isridinghorse', 'createfullactorcopy'):
                ref = self._convert_ref(ref_name, extends)
                return f'{ref}.{safe_prop}'
            return self._emit_function(ref_name, prop_name,
                                       args_rest, extends)

        # Bare function names used as values (no ref, no args)
        # e.g. "getParentRef" -> "GetLinkedRef()", "GetActionRef" -> "akActionRef"
        if re.match(r'^[a-zA-Z_]\w*$', expr):
            bare_low = expr.lower()
            # Special bare identifiers
            if bare_low in ('getactionref', 'isactionref'):
                return self._get_action_ref_param()
            if bare_low == 'isanimplaying':
                return 'False  ;TODO: IsAnimPlaying has no Papyrus equivalent'
            if bare_low == 'isxbox':
                return 'False'
            if bare_low in ('getdayofweek', 'getdayoftheweek'):
                self._property_refs['GameDaysPassed'] = 'GlobalVariable'
                return '(GameDaysPassed.GetValue() as Int) % 7'
            if bare_low in ('getrandompercent', 'getrandpercent'):
                return 'Utility.RandomInt(0, 99)'
            if bare_low in ('getcurrenttime', 'gamehour'):
                self._property_refs['GameHour'] = 'GlobalVariable'
                return 'GameHour.GetValue() as Int'
            if bare_low == 'getpcfame':
                return '0  ;TODO: No fame system in Skyrim'
            if bare_low in ('getpcinfamy', 'getinfame'):
                return '0  ;TODO: No infamy system in Skyrim'
            if bare_low == 'isplayerinprison':
                return 'False  ;TODO: No direct equivalent'
            if bare_low in ('getpcissleeping', 'ispcsleeping'):
                return 'False  ;TODO: Game.GetPlayer().GetSleepState()'
            if bare_low == 'isininterior':
                if extends == 'ActiveMagicEffect':
                    return 'GetTargetActor().GetParentCell().IsInterior()'
                return 'Self.GetParentCell().IsInterior()'
            if bare_low == 'getdestroyed':
                return 'IsDisabled()  ;TODO: GetDestroyed->IsDisabled approximate'
            # Only check FUNCTION_MAP if NOT a declared local variable
            if bare_low not in self._local_vars:
                entry = FUNCTION_MAP.get(bare_low)
                if entry and entry[0] is not None:
                    return self._emit_function(None, expr, '', extends)
                if entry and entry[0] is None and entry[2]:
                    return f'0  {entry[2]}'
            # Check if it's a known EditorID -> property ref
            fid = self.xref.edid_to_formid.get(bare_low, '')
            if fid:
                rtype = self.xref.record_type.get(fid, '')
                ptype = _record_type_to_papyrus(rtype)
                # Prefer attached script type for cross-script property access
                script_type = self.xref.get_record_script_type(expr)
                if script_type:
                    ptype = script_type
                safe = _safe_property_name(expr)
                self._property_refs[safe] = ptype
                if ptype == 'GlobalVariable':
                    return f'{safe}.GetValue() as Int'
                return safe

        # Terminal substitutions (applied last, after all function matching)
        expr = re.sub(r'\bplayer\b', 'Game.GetPlayer()', expr, flags=re.IGNORECASE)
        # In AME/TopicInfo scripts, Self/GetSelf refers to the target actor
        if extends == 'ActiveMagicEffect':
            expr = re.sub(r'\bgetSelf\b', 'GetTargetActor()', expr, flags=re.IGNORECASE)
            expr = re.sub(r'\bthis\b', 'GetTargetActor()', expr, flags=re.IGNORECASE)
        elif extends == 'TopicInfo':
            expr = re.sub(r'\bgetSelf\b', 'akSpeakerRef', expr, flags=re.IGNORECASE)
            expr = re.sub(r'\bthis\b', 'akSpeakerRef', expr, flags=re.IGNORECASE)
        else:
            expr = re.sub(r'\bgetSelf\b', 'Self', expr, flags=re.IGNORECASE)
            expr = re.sub(r'\bthis\b', 'Self', expr, flags=re.IGNORECASE)
        expr = re.sub(r'\bGetSecondsPassed\b', '0.5', expr, flags=re.IGNORECASE)
        expr = re.sub(r'\bScriptEffectElapsedSeconds\b', '0.5', expr, flags=re.IGNORECASE)

        # Fix bare decimals: .5 -> 0.5 (Papyrus requires leading zero)
        expr = re.sub(r'(?<![.\w])\.(\d)', r'0.\1', expr)

        # Known TES4 globals -> GlobalVariable.GetValue()
        if expr.lower() in KNOWN_GLOBALS:
            canonical = _canonical_global(expr)
            self._property_refs[canonical] = 'GlobalVariable'
            return f'{canonical}.GetValue()'

        # Actor value name substitution
        for ob_av, sk_av in ACTOR_VALUE_MAP.items():
            expr = re.sub(r'\b' + ob_av + r'\b', sk_av, expr, flags=re.IGNORECASE)

        # Rename reserved-word variables (e.g. next -> myNext)
        for orig_low, safe in self._var_renames.items():
            expr = re.sub(r'\b' + re.escape(orig_low) + r'\b', safe, expr, flags=re.IGNORECASE)

        return expr

    def _convert_ref(self, name: str, extends: str) -> str:
        """Convert an Oblivion reference name to Papyrus."""
        low = name.lower()
        if low == 'player':
            return 'Game.GetPlayer()'
        if low in ('getself', 'myself', 'self'):
            return 'Self'

        # Known TES4 globals -> property
        if low in KNOWN_GLOBALS:
            canonical = _canonical_global(name)
            self._property_refs[canonical] = 'GlobalVariable'
            return canonical

        if '.' in name:
            parts = name.split('.', 1)
            ref_part = self._convert_ref(parts[0], extends)
            return f'{ref_part}.{_safe_property_name(parts[1])}'

        if self.xref.is_quest_ref(name):
            self._property_refs[name] = self.xref.get_quest_script_type(name)
            return name

        # Check if this is any known EditorID from the export
        fid = self.xref.edid_to_formid.get(low, '')
        if fid:
            rtype = self.xref.record_type.get(fid, '')
            ptype = _record_type_to_papyrus(rtype)
            # Prefer attached script type over generic Actor/ObjectReference
            # so cross-script property access works (e.g., NPCRef.rent)
            script_type = self.xref.get_record_script_type(name)
            if script_type:
                ptype = script_type
            safe = _safe_property_name(name)
            # Don't downgrade a more specific type (e.g., Actor from
            # _resolve_self_ref) back to a generic one (ObjectReference).
            cur = self._property_refs.get(safe, '')
            _generic = ('', 'ObjectReference')
            if not cur or ptype not in _generic or cur in _generic:
                self._property_refs[safe] = ptype
            return safe

        return _safe_property_name(name)

    def _convert_args(self, args_str: str, func_name: str, extends: str) -> str:
        """Convert Oblivion function arguments to Papyrus."""
        if not args_str:
            return ''

        # Actor value functions: first arg is AV name -> quoted string
        av_funcs = {'getactorvalue', 'setactorvalue', 'modactorvalue', 'forceactorvalue',
                     'getav', 'setav', 'modav', 'forceav', 'getbaseactorvalue', 'getbaseav'}
        if func_name in av_funcs:
            parts = args_str.split(None, 1)
            av_name = parts[0].rstrip(',').strip('"\'')
            sk_av = ACTOR_VALUE_MAP.get(av_name.lower(), av_name)
            rest = ''
            if len(parts) > 1:
                rest_str = parts[1].lstrip(', ')
                if rest_str:
                    rest = f', {self._convert_expression(rest_str, extends)}'
            return f'"{sk_av}"{rest}'

        # Default: split on commas first, then whitespace within each part
        # Oblivion scripts use both "func arg1 arg2" and "func arg1, arg2"
        if ',' in args_str:
            parts = [p.strip() for p in args_str.split(',') if p.strip()]
        else:
            parts = args_str.split()
        converted = [self._convert_expression(p, extends) for p in parts]
        return ', '.join(converted)

    def _convert_function_call(self, line: str, extends: str) -> str:
        """Convert an Oblivion function call line to Papyrus."""
        stripped = line.strip()

        # ref.function pattern
        ref_m = re.match(r'^(\w+)\.(\w+)\s*(.*)', stripped, re.IGNORECASE)
        if ref_m:
            return self._emit_function(ref_m.group(1), ref_m.group(2), ref_m.group(3).strip(), extends)

        # Standalone function
        func_m = re.match(r'^(\w+)\s*(.*)', stripped, re.IGNORECASE)
        if func_m:
            return self._emit_function(None, func_m.group(1), func_m.group(2).strip(), extends)

        return f'{stripped}  ;TODO: Could not parse'

    def _get_action_ref_param(self) -> str:
        """Return the correct event parameter for GetActionRef/IsActionRef.
        
        TES4 GetActionRef is available in every block. Papyrus scopes event params.
        Map to the appropriate parameter based on the current event being converted.
        """
        ev = self._current_event.lower()
        if 'onactivate' in ev or 'ontrigger' in ev:
            return 'akActionRef'
        if 'onequipped' in ev or 'onunequipped' in ev:
            return 'akActor'
        if 'onhit' in ev:
            return 'akAggressor'
        if 'ondeath' in ev:
            return 'akKiller'
        if 'oncontainerchanged' in ev:
            return 'akNewContainer'
        if 'oncombatstate' in ev:
            return 'akTarget'
        # OnUpdate/OnInit/other events have no action ref - use Self as fallback
        if 'onupdate' in ev or 'oninit' in ev:
            return 'Self'
        # Fallback: akActionRef (may be undefined, but most common case)
        return 'akActionRef'

    def _resolve_self_ref(self, ref_name, extends, actor_func=False):
        """Resolve the reference for a function call.
        
        For ActiveMagicEffect scripts, bare (no ref) or Self-prefixed actor/objref
        functions need GetTargetActor() instead of Self.
        For TopicInfo scripts, bare actor functions need akSpeakerRef.
        """
        if ref_name:
            ref_low = ref_name.lower()
            # Self in ActiveMagicEffect/TopicInfo should redirect actor functions
            if actor_func and ref_low == 'self':
                if extends == 'ActiveMagicEffect':
                    return 'GetTargetActor()'
                if extends == 'TopicInfo':
                    return 'akSpeakerRef'
            # Upgrade property type to Actor when used with actor-only functions
            if actor_func:
                cur = self._property_refs.get(ref_name, '')
                if cur in ('', 'ObjectReference'):
                    self._property_refs[ref_name] = 'Actor'
            return self._convert_ref(ref_name, extends)
        if actor_func:
            if extends == 'ActiveMagicEffect':
                return 'GetTargetActor()'
            if extends == 'TopicInfo':
                return 'akSpeakerRef'
        return 'Self'

    def _emit_function(self, ref_name: Optional[str], func_name: str,
                       args_str: str, extends: str) -> str:
        """Emit a converted function call."""
        fname_low = func_name.lower()

        # --- Special case functions ---

        if fname_low == 'getself':
            return 'Self'

        if fname_low == 'getactionref':
            return self._get_action_ref_param()

        if fname_low == 'isactionref':
            arg = self._convert_expression(args_str, extends) if args_str else ''
            return f'{self._get_action_ref_param()} == {arg}'

        # GetPos/GetAngle/GetStartingAngle: axis param -> GetPositionX/Y/Z or GetAngleX/Y/Z
        if fname_low in ('getpos', 'getangle', 'getstartingangle'):
            axis = args_str.strip().upper() if args_str else 'X'
            if axis not in ('X', 'Y', 'Z'):
                axis = 'X'
            if fname_low == 'getpos':
                papyrus = f'GetPosition{axis}'
            else:
                papyrus = f'GetAngle{axis}'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.{papyrus}()' if ref_name else f'{papyrus}()'

        # SetPos/SetAngle: axis param -> SetPosition(x,y,z) / SetAngle(x,y,z)
        if fname_low in ('setpos', 'setangle'):
            parts = args_str.split(None, 1) if args_str else ['X', '0']
            axis = parts[0].upper() if parts else 'X'
            value = self._convert_expression(parts[1], extends) if len(parts) > 1 else '0'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            if fname_low == 'setpos':
                axes = {'X': (value, f'{ref}.GetPositionY()', f'{ref}.GetPositionZ()'),
                        'Y': (f'{ref}.GetPositionX()', value, f'{ref}.GetPositionZ()'),
                        'Z': (f'{ref}.GetPositionX()', f'{ref}.GetPositionY()', value)}
                x, y, z = axes.get(axis, (value, f'{ref}.GetPositionY()', f'{ref}.GetPositionZ()'))
                return f'{ref}.SetPosition({x}, {y}, {z})'
            else:
                axes = {'X': (value, f'{ref}.GetAngleY()', f'{ref}.GetAngleZ()'),
                        'Y': (f'{ref}.GetAngleX()', value, f'{ref}.GetAngleZ()'),
                        'Z': (f'{ref}.GetAngleX()', f'{ref}.GetAngleY()', value)}
                x, y, z = axes.get(axis, (value, f'{ref}.GetAngleY()', f'{ref}.GetAngleZ()'))
                return f'{ref}.SetAngle({x}, {y}, {z})'

        # GetRandomPercent -> Utility.RandomInt(0, 99)
        if fname_low == 'getrandompercent':
            return 'Utility.RandomInt(0, 99)'

        # SetStage/GetStage/GetStageDone: first arg is quest, second is stage
        if fname_low in ('setstage', 'getstage', 'getstagedone'):
            if args_str and ',' in args_str:
                parts = [p.strip() for p in args_str.split(',')]
            else:
                parts = args_str.split() if args_str else []
            if len(parts) >= 2:
                quest_ref = parts[0].rstrip(',')
                stage = parts[1].rstrip(',')
            elif len(parts) == 1:
                quest_ref = parts[0].rstrip(',')
                stage = '0'
            else:
                quest_ref = 'quest'
                stage = '0'
            quest_type = self.xref.get_quest_script_type(quest_ref) if self.xref.is_quest_ref(quest_ref) else 'Quest'
            self._property_refs[quest_ref] = quest_type
            papyrus = {'setstage': 'SetStage', 'getstage': 'GetStage',
                        'getstagedone': 'GetStageDone'}[fname_low]
            if fname_low in ('getstage', 'getstagedone') and len(parts) < 2:
                return f'{quest_ref}.{papyrus}()'
            if fname_low in ('getstage', 'getstagedone') and stage == '0' and len(parts) < 2:
                return f'{quest_ref}.{papyrus}()'
            return f'{quest_ref}.{papyrus}({stage})'

        # StartQuest/StopQuest/GetQuestRunning/CompleteQuest/IsQuestCompleted: arg is quest
        if fname_low in ('startquest', 'stopquest', 'getquestrunning', 'completequest', 'isquestcompleted'):
            quest_ref = args_str.strip() if args_str else 'quest'
            quest_type = self.xref.get_quest_script_type(quest_ref) if self.xref.is_quest_ref(quest_ref) else 'Quest'
            self._property_refs[quest_ref] = quest_type
            papyrus = {'startquest': 'Start', 'stopquest': 'Stop',
                        'getquestrunning': 'IsRunning',
                        'completequest': 'CompleteQuest',
                        'isquestcompleted': 'IsCompleted'}[fname_low]
            return f'{quest_ref}.{papyrus}()'

        # Message/MessageBox
        if fname_low == 'message':
            return f'Debug.Notification({self._quote_msg(args_str)})'
        if fname_low == 'messagebox':
            return f'Debug.MessageBox({self._quote_msg(args_str)})'

        # --- Compound player.Function ---
        compound = f'{ref_name}.{func_name}'.lower() if ref_name else ''
        if compound in FUNCTION_MAP:
            entry = FUNCTION_MAP[compound]
            papyrus_func, _, note = entry
            if papyrus_func:
                args = self._convert_args(args_str, fname_low, extends) if args_str else ''
                result = f'{papyrus_func}({args})'
                return f'{result}  {note}' if note else result

        # GetPCExpelled / SetPCExpelled: faction arg
        # Skyrim has no Expel/IsExpelled — use faction rank manipulation
        if fname_low in ('getpcexpelled', 'ispcexpelled'):
            faction = self._convert_expression(args_str, extends) if args_str else 'None'
            if args_str:
                self._property_refs[args_str.strip()] = 'Faction'
            return f'(Game.GetPlayer().GetFactionRank({faction}) < 0)'
        if fname_low == 'setpcexpelled':
            parts = args_str.split(None, 1) if args_str else []
            faction = self._convert_expression(parts[0], extends) if parts else 'None'
            if parts:
                self._property_refs[parts[0].strip()] = 'Faction'
            val = parts[1].strip() if len(parts) > 1 else '1'
            if val == '0':
                return f'Game.GetPlayer().SetFactionRank({faction}, 0)  ;TODO: un-expel (was SetPCExpelled 0)'
            return f'Game.GetPlayer().SetFactionRank({faction}, -1)  ;TODO: Expel via faction rank'

        # GotoJail
        if fname_low == 'gotojail':
            return ';TODO: GotoJail - No direct Papyrus equivalent, use quest stage'

        # Say: ref.Say topic [force] [headRef] -> ref.Say(topic)
        # SayTo: ref.SayTo target topic [force] -> ref.Say(topic)
        if fname_low in ('say', 'sayto', 'saycustom'):
            if args_str and ',' in args_str:
                pparts = [p.strip() for p in args_str.split(',')]
            else:
                pparts = args_str.split() if args_str else []
            if fname_low == 'sayto' and len(pparts) >= 2:
                # SayTo target topic [force] -> first arg is target, second is topic
                # If topic part has a trailing number (force flag), strip it
                topic_str = pparts[1].strip().split()[0] if pparts[1].strip() else 'None'
                topic = self._convert_expression(topic_str, extends)
                self._property_refs[topic_str] = 'Topic'
            else:
                topic = self._convert_expression(pparts[0], extends) if pparts else 'None'
                if pparts:
                    self._property_refs[pparts[0].strip()] = 'Topic'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.Say({topic})'

        # Functions whose Papyrus equivalent takes fewer args - drop the extra
        _DROP_ARGS_FUNCS = {'addscriptpackage', 'removescriptpackage', 'stopcombat', 'resurrect'}
        if fname_low in _DROP_ARGS_FUNCS:
            args_str = ''

        # SetFactionReaction/ModFactionReaction: TES4 setfactionreaction f1 f2 val
        # -> Papyrus f1.SetReaction(f2, val)
        if fname_low in ('setfactionreaction', 'modfactionreaction'):
            parts = args_str.split(',') if args_str and ',' in args_str else (args_str.split() if args_str else [])
            parts = [p.strip() for p in parts if p.strip()]
            if len(parts) >= 3:
                f1 = self._convert_expression(parts[0], extends)
                f2 = self._convert_expression(parts[1], extends)
                val = self._convert_expression(parts[2], extends)
                self._property_refs[parts[0].strip()] = 'Faction'
                self._property_refs[parts[1].strip()] = 'Faction'
                papyrus_fn = 'SetReaction' if fname_low == 'setfactionreaction' else 'ModReaction'
                return f'{f1}.{papyrus_fn}({f2}, {val})'
            # Fallback: not enough args
            return f';TODO: {func_name} {args_str}  ;needs faction1.SetReaction(faction2, val)'

        # GetGameSetting/getgs: arg is GMST name → quoted string
        if fname_low in ('getgamesetting', 'getgs'):
            setting = args_str.strip().strip('"') if args_str else 'fUnknown'
            # Use Int/Float/String variant based on naming convention (i=int, f=float, s=string)
            if setting.startswith('i'):
                return f'Game.GetGameSettingInt("{setting}")'
            elif setting.startswith('s'):
                return f'Game.GetGameSettingString("{setting}")'
            return f'Game.GetGameSettingFloat("{setting}")'

        # GetDeadCount: TES4 GetDeadCount ActorRef -> ref.IsDead() (no Skyrim equivalent for count)
        if fname_low == 'getdeadcount':
            if ref_name:
                ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
                return f'{ref}.IsDead()'
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'Self'
            if args_str:
                self._property_refs[args_str.strip()] = 'Actor'
            return f'{arg}.IsDead()'

        # ResetHealth: TES4 ResetHealth -> RestoreActorValue("Health", 9999)
        if fname_low == 'resethealth':
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.RestoreActorValue("Health", 9999)'

        # EvaluatePackage/EVP/AddScriptPackage/RemoveScriptPackage/StopWaiting:
        # Skyrim version takes no args (drop TES4 package arg)
        if fname_low in ('evaluatepackage', 'evp', 'addscriptpackage', 'removescriptpackage', 'stopwaiting'):
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.EvaluatePackage()'

        # ClearLookAt / StopLook: Skyrim version takes no args (drop TES4 target arg)
        if fname_low in ('clearlookat', 'stoplook', 'stoplooking'):
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.ClearLookAt()'

        # GetAmountSoldStolen / GetPCMiscStat: -> Game.QueryStat("Items Stolen")
        if fname_low in ('getamountsoldstolen', 'getpcmiscstat'):
            stat = args_str.strip().strip('"') if args_str else 'Items Stolen'
            return f'Game.QueryStat("{stat}")'

        # GetEquippedItemType: Skyrim requires hand param (0=left, 1=right)
        if fname_low in ('getweaponanimtype', 'getequippeditemtype'):
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.GetEquippedItemType(1)'

        # IsActorUsingATorch: check if left hand has torch equipped (type 11)
        if fname_low == 'isactorusingatorch':
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'({ref}.GetEquippedItemType(0) == 11)'

        # IsRidingHorse: Actor.IsOnMount() in Skyrim
        if fname_low == 'isridinghorse':
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.IsOnMount()'

        # GetRace: ref.GetRace() -> ref.GetRace()
        if fname_low == 'getrace':
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.GetRace()'

        # Expel: ref.Expel(faction) -> Game.GetPlayer().SetFactionRank(faction, -1)
        if fname_low == 'expel':
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'None'
            if args_str:
                self._property_refs[args_str.strip()] = 'Faction'
            return f'Game.GetPlayer().SetFactionRank({arg}, -1)  ;TODO: Expel mapped to faction rank -1'

        # IsExpelled/IsPCExpelled/GetPCExpelled: check faction rank < 0
        if fname_low in ('isexpelled', 'ispcexpelled', 'getpcexpelled'):
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'None'
            if args_str:
                self._property_refs[args_str.strip()] = 'Faction'
            return f'(Game.GetPlayer().GetFactionRank({arg}) < 0)'

        # IsInInterior: ref.IsInInterior -> ref.GetParentCell().IsInterior()
        if fname_low == 'isininterior':
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.GetParentCell().IsInterior()'

        # Unlock: ref.Unlock -> ref.Lock(false)
        if fname_low == 'unlock':
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.Lock(false)'

        # Cast: TES4 ref.cast spell [target] -> Papyrus spell.Cast(ref, target)
        # Cast is a method on Spell in Papyrus, not on ObjectReference
        if fname_low == 'cast':
            parts = args_str.split(',') if args_str and ',' in args_str else (args_str.split() if args_str else [])
            parts = [p.strip() for p in parts if p.strip()]
            spell = self._convert_expression(parts[0], extends) if parts else 'None'
            if parts:
                self._property_refs[parts[0].strip()] = 'Spell'
            source = self._resolve_self_ref(ref_name, extends, actor_func=True)
            if len(parts) > 1:
                target = self._convert_expression(parts[1], extends)
                return f'{spell}.Cast({source}, {target})'
            return f'{spell}.Cast({source})'

        # GetIsId: ref.GetIsId ActorBase -> (ref as Actor).GetActorBase() == actorBase
        if fname_low == 'getisid':
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'None'
            if args_str:
                self._property_refs[args_str.strip()] = 'ActorBase'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'({ref} as Actor).GetActorBase() == {arg}'

        # GetIsRace: ref.GetIsRace RaceRef -> ref.GetRace() == raceRef
        if fname_low in ('getisrace', 'getpcisrace'):
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'None'
            if args_str:
                self._property_refs[args_str.strip()] = 'Race'
            if fname_low == 'getpcisrace':
                return f'Game.GetPlayer().GetRace() == {arg}'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.GetRace() == {arg}'

        # GetIsRef: ref.GetIsRef otherRef -> ref == otherRef
        if fname_low == 'getisref':
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'None'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref} == {arg}'

        # GetInCell: ref.GetInCell CellEditorID -> ref.GetParentCell() == cellRef
        if fname_low == 'getincell':
            arg = args_str.strip().strip('"') if args_str else 'None'
            self._property_refs[arg] = 'Cell'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.GetParentCell() == {arg}'

        # GetInSameCell: ref.GetInSameCell otherRef -> ref.GetParentCell() == otherRef.GetParentCell()
        if fname_low in ('getinsamecell', 'getinsamecellas'):
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'Game.GetPlayer()'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.GetParentCell() == {arg}.GetParentCell()'

        # GetIsSex: ref.GetIsSex Male/Female -> ref.GetActorBase().GetSex() == 0/1
        if fname_low == 'getissex':
            arg = args_str.strip().lower() if args_str else 'male'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            sex_val = '1' if 'female' in arg else '0'
            return f'({ref} as Actor).GetActorBase().GetSex() == {sex_val}'

        # sme (StopMagicEffectVisuals) / pme (PlayMagicEffectVisuals)
        if fname_low in ('sme', 'stopmage', 'stopmagiceffectvisuals'):
            return f';TODO: StopMagicEffectVisuals {args_str or ""}  ;no direct equivalent'
        if fname_low in ('pme', 'playmage', 'playmagiceffectvisuals'):
            return f';TODO: PlayMagicEffectVisuals {args_str or ""}  ;no direct equivalent'

        # GetCrimeKnown: complex TES4 condition, no direct equivalent
        if fname_low == 'getcrimeknown':
            return f';TODO: GetCrimeKnown {args_str or ""}  ;no Papyrus equivalent'

        # SetDisplayName: ref.SetDisplayName "name" -> ref.SetDisplayName("name")
        if fname_low == 'setdisplayname':
            arg = self._convert_expression(args_str, extends) if args_str else '""'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f';TODO: {ref}.SetDisplayName({arg})  ;SKSE required'

        # SetOwnership: determines Actor vs Faction from argument type
        if fname_low == 'setownership':
            ref = self._resolve_self_ref(ref_name, extends)
            if args_str:
                arg = self._convert_expression(args_str.strip(), extends)
                arg_low = args_str.strip().lower()
                # Check if arg is a faction
                arg_fid = self.xref.edid_to_formid.get(arg_low, '')
                arg_rtype = self.xref.record_type.get(arg_fid, '') if arg_fid else ''
                pref_type = self._property_refs.get(arg, self._property_refs.get(_safe_property_name(args_str.strip()), ''))
                if arg_rtype == 'FACT' or pref_type == 'Faction':
                    return f'{ref}.SetFactionOwner({arg})'
                else:
                    return f'{ref}.SetActorOwner({arg}.GetActorBase())'
            return f'{ref}.SetActorOwner(Game.GetPlayer().GetActorBase())'

        # MoveTo: ref.MoveTo target [X Y Z] -> ref.MoveTo(target, X, Y, Z)
        if fname_low in ('moveto', 'movetomarker'):
            normalized = args_str.replace(',', ' ') if args_str else ''
            parts = [p.strip() for p in normalized.split() if p.strip()]
            target = self._convert_expression(parts[0], extends) if parts else 'None'
            offsets = ', '.join(parts[1:4]) if len(parts) > 1 else ''
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            if offsets:
                return f'{ref}.MoveTo({target}, {offsets})'
            return f'{ref}.MoveTo({target})'

        # PlaceAtMe: ref.PlaceAtMe base [count] [distance] -> ref.PlaceAtMe(base, count)
        if fname_low == 'placeatme':
            # Normalize: replace commas with spaces, then split on whitespace
            normalized = args_str.replace(',', ' ') if args_str else ''
            parts = [p.strip() for p in normalized.split() if p.strip()]
            base = self._convert_expression(parts[0], extends) if parts else 'None'
            count = parts[1] if len(parts) > 1 else '1'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.PlaceAtMe({base}, {count})'

        # CreateFullActorCopy: TES4 function, no direct Papyrus equivalent
        if fname_low == 'createfullactorcopy':
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f';TODO: {ref}.CreateFullActorCopy()  ;no direct equivalent'

        # WakeUpPC -> Game.GetPlayer() wake up
        if fname_low == 'wakeuppc':
            return 'Game.GetPlayer().RestoreActorValue("Health", 0)  ;TODO: WakeUpPC approximate'

        # IsExpelled: faction arg -> Game.GetPlayer().IsExpelled(faction) - TES4 condition  
        if fname_low == 'isexpelled':
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'None'
            ref = self._convert_ref(ref_name, extends) if ref_name else 'Game.GetPlayer()'
            return f';TODO: {ref}.IsExpelled({arg})  ;no direct equivalent, use faction rank check'

        # GetContainer: item.GetContainer -> item.GetContainer() (valid ObjectReference method)
        if fname_low == 'getcontainer':
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f';TODO: {ref}.GetContainer()  ;SKSE required'

        # GetBookRead -> no direct equivalent
        if fname_low in ('getbookread', 'bookread'):
            return f';TODO: GetBookRead  ;no direct equivalent'

        # ShowClassMenu, ShowBirthSignMenu etc - character gen functions
        if fname_low in ('showclassmenu', 'showbirthsignmenu', 'showracemenu'):
            return f';TODO: {func_name}  ;character generation menus not available in Papyrus'

        # SetInCharGen: TES4 command to mark character generation state
        if fname_low == 'setinchargen':
            return f';TODO: SetInCharGen {args_str or ""}  ;no Papyrus equivalent'

        # SetPlayerInSEWorld: Shivering Isles specific
        if fname_low == 'setplayerinseworld':
            return f';TODO: SetPlayerInSEWorld  ;Shivering Isles specific'

        # ForceCloseOblivionGate / CloseCurrentOblivionGate: Oblivion-specific
        if fname_low in ('forcecloseobliviongate', 'closecurrentobliviongate'):
            return f';TODO: {func_name}  ;Oblivion gate specific, no equivalent'

        # IsInFaction: ref.IsInFaction faction -> ref.IsInFaction(faction)  
        if fname_low == 'isinfaction':
            arg = self._convert_expression(args_str.strip(), extends) if args_str else 'None'
            if args_str:
                self._property_refs[args_str.strip()] = 'Faction'
            ref = self._resolve_self_ref(ref_name, extends, actor_func=True)
            return f'{ref}.IsInFaction({arg})'

        # --- Standard function map lookup ---
        # Default args for TES4 functions that implicitly use "player" when
        # called with no arguments, but the Papyrus equivalent requires them.
        _DEFAULT_ARGS = {
            'activate': 'Game.GetPlayer()',
            'startconversation': 'Game.GetPlayer()',
            'sayto': 'Game.GetPlayer()',
            'getrandompercent': '0, 99',
            'isactordetected': 'Game.GetPlayer()',
            'getdetected': 'Game.GetPlayer()',
            'isdetectedby': 'Game.GetPlayer()',
            'setownership': 'Game.GetPlayer().GetActorBase()',
            'setactorowner': 'Game.GetPlayer().GetActorBase()',
        }
        entry = FUNCTION_MAP.get(fname_low)
        if entry:
            papyrus_func, needs_self, note = entry
            if papyrus_func is None:
                orig = f'{ref_name}.{func_name} {args_str}'.strip() if ref_name else f'{func_name} {args_str}'.strip()
                return f';TODO: {orig}' + (f'  {note}' if note else '')
            if not args_str and fname_low in _DEFAULT_ARGS:
                args = _DEFAULT_ARGS[fname_low]
            else:
                args = self._convert_args(args_str, fname_low, extends) if args_str else ''
            if ref_name:
                ref = self._convert_ref(ref_name, extends)
                # ActiveMagicEffect Self doesn't have actor/objref methods
                if ref == 'Self' and extends == 'ActiveMagicEffect':
                    ref = 'GetTargetActor()'
                elif ref == 'Self' and extends == 'TopicInfo' and fname_low in _ACTOR_ONLY_FUNCTIONS:
                    ref = 'akSpeakerRef'
                # Infer Actor type from function usage (don't overwrite script types)
                if fname_low in _ACTOR_ONLY_FUNCTIONS:
                    cur = self._property_refs.get(ref_name, '')
                    if cur in ('', 'ObjectReference'):
                        self._property_refs[ref_name] = 'Actor'
                result = f'{ref}.{papyrus_func}({args})'
            else:
                # No ref — infer implicit target based on script context
                if needs_self and fname_low in _ACTOR_ONLY_FUNCTIONS:
                    if extends == 'TopicInfo':
                        result = f'akSpeakerRef.{papyrus_func}({args})'
                    elif extends == 'ActiveMagicEffect':
                        result = f'GetTargetActor().{papyrus_func}({args})'
                    else:
                        result = f'{papyrus_func}({args})'
                else:
                    result = f'{papyrus_func}({args})'
            return f'{result}  {note}' if note else result
            
        # --- Fallback: unknown function ---
        args = self._convert_args(args_str, fname_low, extends) if args_str else ''
        if ref_name:
            ref = self._convert_ref(ref_name, extends)
            if fname_low in _ACTOR_ONLY_FUNCTIONS:
                cur = self._property_refs.get(ref_name, '')
                if cur in ('', 'ObjectReference'):
                    self._property_refs[ref_name] = 'Actor'
            return f'{ref}.{func_name}({args})  ;TODO: Verify'
        if fname_low in _ACTOR_ONLY_FUNCTIONS:
            if extends == 'TopicInfo':
                return f'akSpeakerRef.{func_name}({args})  ;TODO: Verify'
            if extends == 'ActiveMagicEffect':
                return f'GetTargetActor().{func_name}({args})  ;TODO: Verify'
        return f'{func_name}({args})  ;TODO: Verify'

    def _quote_msg(self, args_str: str) -> str:
        """Quote a message argument if not already quoted.
        For MessageBox with buttons (e.g. '"text" "Yes" "No"'), extract only the message."""
        s = args_str.strip()
        if s.startswith('"'):
            # Find the end of the first quoted string
            end = s.index('"', 1) if '"' in s[1:] else len(s)
            first_str = s[:end + 1]
            # If there are more quoted strings (button labels), strip them
            return first_str
        return f'"{s}"'


# ===========================================================================
# High-level conversion functions
# ===========================================================================

def convert_all_scripts(export_dir: str, output_dir: str, workers: int = None) -> dict:
    """Convert all TES4 scripts from export directory to Papyrus .psc files.

    Args:
        export_dir: Path to export/Oblivion.esm (contains .txt files)
        output_dir: Path to write .psc files
        workers: Number of worker threads (default: cpu_count-1)

    Returns dict with conversion statistics.
    """
    if workers is None:
        workers = max(1, (os.cpu_count() or 4) - 1)

    os.makedirs(output_dir, exist_ok=True)

    # Phase 1: Build cross-reference graph
    print('  Building cross-reference graph...')
    xref = CrossRefGraph()
    xref.load_from_export(export_dir)
    print(f'    {len(xref.formid_to_edid)} FormID->EditorID mappings')
    print(f'    {len(xref.script_formid_to_edid)} scripts, {len(xref.quest_edids)} quests')

    # Phase 1.5: Analyze cross-script ref-as-int patterns
    scpt_path = os.path.join(export_dir, 'SCPT.txt')
    if os.path.exists(scpt_path):
        xref.build_ref_as_int_map(scpt_path)
        if xref.ref_as_int:
            print(f'    {len(xref.ref_as_int)} ref variables detected as integer-only (cross-script)')

    stats = {
        'scpt_total': 0, 'scpt_ok': 0, 'scpt_err': 0,
        'info_total': 0, 'info_ok': 0, 'info_err': 0,
        'qust_total': 0, 'qust_ok': 0, 'qust_err': 0,
        'todo_count': 0, 'errors': [],
    }

    # Phase 2: Convert SCPT records
    scpt_path = os.path.join(export_dir, 'SCPT.txt')
    if os.path.exists(scpt_path):
        print('  Converting SCPT records...')
        _convert_scpt_records(scpt_path, output_dir, xref, stats)

    # Phase 3: Convert INFO result scripts
    info_path = os.path.join(export_dir, 'INFO.txt')
    if os.path.exists(info_path):
        print('  Converting INFO result scripts...')
        _convert_info_scripts(info_path, output_dir, xref, stats)

    # Phase 4: Convert QUST stage scripts
    qust_path = os.path.join(export_dir, 'QUST.txt')
    if os.path.exists(qust_path):
        print('  Converting QUST stage scripts...')
        _convert_qust_scripts(qust_path, output_dir, xref, stats)

    total = stats['scpt_ok'] + stats['info_ok'] + stats['qust_ok']
    errs = stats['scpt_err'] + stats['info_err'] + stats['qust_err']
    print(f'\n  Script conversion complete:')
    print(f'    SCPT: {stats["scpt_ok"]}/{stats["scpt_total"]} converted')
    print(f'    INFO: {stats["info_ok"]}/{stats["info_total"]} fragments')
    print(f'    QUST: {stats["qust_ok"]}/{stats["qust_total"]} stage scripts')
    print(f'    Total: {total} converted, {errs} errors, {stats["todo_count"]} TODOs')

    _write_report(output_dir, stats)
    return stats


def _convert_scpt_records(scpt_path: str, output_dir: str, xref: CrossRefGraph, stats: dict):
    """Convert all SCPT records from the export file."""
    records = parse_export_file(scpt_path)
    stats['scpt_total'] = len(records)

    for rec in records:
        formid = rec.get('FormID', '')
        edid = rec.get('EditorID', '')
        sctx = rec.get('SCTX', '')
        if not sctx or not sctx.strip():
            continue

        try:
            extends = xref.get_extends_class(formid)
            conv = ScriptConverter(xref)
            # Pre-populate external references from SCRO entries
            _preload_scro_refs(conv, rec, xref)
            name = _sanitize_name(edid or f'Script_{formid}')
            papyrus = conv.convert_standalone(name, sctx, extends, edid)

            out_path = os.path.join(output_dir, f'TES4_{name}.psc')
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(papyrus)
            stats['scpt_ok'] += 1
            stats['todo_count'] += papyrus.count(';TODO')
        except Exception as e:
            stats['scpt_err'] += 1
            stats['errors'].append(f'SCPT {edid} ({formid}): {e}')


def _convert_info_scripts(info_path: str, output_dir: str, xref: CrossRefGraph, stats: dict):
    """Convert INFO result scripts to TopicInfo fragment .psc files."""
    records = parse_export_file(info_path)

    for rec in records:
        result_script = rec.get('ResultScript', '')
        if not result_script or not result_script.strip():
            continue

        formid = rec.get('FormID', '')
        stats['info_total'] += 1

        try:
            conv = ScriptConverter(xref)
            _preload_scro_refs(conv, rec, xref)
            body_lines = conv.convert_fragment(result_script, 'TopicInfo')

            script_name = f'TES4_TIF__{formid}'
            prop_refs = dict(conv._property_refs)
            out_lines = [
                f'ScriptName {script_name} extends TopicInfo Hidden',
                '',
            ]
            if prop_refs:
                declared = set()
                for pname, ptype in sorted(prop_refs.items()):
                    safe = _safe_property_name(pname)
                    if safe.lower() in declared:
                        continue
                    declared.add(safe.lower())
                    out_lines.append(f'{ptype} Property {safe} Auto')
                out_lines.append('')
            out_lines.append('Function Fragment_0(Actor akSpeakerRef)')
            out_lines.extend(body_lines)
            out_lines.append('EndFunction')
            out_lines.append('')

            papyrus = '\n'.join(out_lines)
            out_path = os.path.join(output_dir, f'{script_name}.psc')
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(papyrus)
            stats['info_ok'] += 1
            stats['todo_count'] += papyrus.count(';TODO')
        except Exception as e:
            stats['info_err'] += 1
            stats['errors'].append(f'INFO {formid}: {e}')


def _convert_qust_scripts(qust_path: str, output_dir: str, xref: CrossRefGraph, stats: dict):
    """Convert QUST stage scripts to Quest fragment .psc files."""
    records = parse_export_file(qust_path)

    for rec in records:
        edid = rec.get('EditorID', '')
        if not edid:
            continue

        stage_count_str = rec.get('StageCount', '0')
        try:
            stage_count = int(stage_count_str)
        except ValueError:
            continue

        fragments = []  # (stage_index, log_idx, script_source, stage_arr_idx, log_arr_idx)
        for i in range(stage_count):
            stage_idx_str = rec.get(f'Stage[{i}].Index', '0')
            try:
                stage_idx = int(stage_idx_str)
            except ValueError:
                continue

            log_count_str = rec.get(f'Stage[{i}].LogCount', '0')
            try:
                log_count = int(log_count_str)
            except ValueError:
                continue

            for j in range(log_count):
                script = rec.get(f'Stage[{i}].Log[{j}].ResultScript', '')
                if script and script.strip():
                    fragments.append((stage_idx, j, script, i, j))

        if not fragments:
            continue

        stats['qust_total'] += len(fragments)

        try:
            conv = ScriptConverter(xref)
            # Pre-populate external references from SCRO entries
            _preload_scro_refs(conv, rec, xref)
            script_name = f'TES4_QF_{_sanitize_name(edid)}'
            out_lines = [
                f'ScriptName {script_name} extends Quest Hidden',
                '',
            ]

            for stage_idx, log_idx, script_src, stage_arr_idx, log_arr_idx in fragments:
                # Load per-stage SCROs for this fragment
                _preload_stage_scro_refs(conv, rec, xref, stage_arr_idx, log_arr_idx)
                func_name = f'Fragment_Stage_{stage_idx:04d}_Item_{log_idx}'
                out_lines.append(f'Function {func_name}()')
                body_lines = conv.convert_fragment(script_src, 'Quest')
                out_lines.extend(body_lines)
                out_lines.append('EndFunction')
                out_lines.append('')

            # Insert property declarations after ScriptName line
            prop_refs = conv.get_property_refs()
            if prop_refs:
                insert_idx = 2  # After ScriptName + blank line
                declared = set()
                count = 0
                for pname, ptype in sorted(prop_refs.items()):
                    safe = _safe_property_name(pname)
                    if safe.lower() in declared:
                        continue
                    declared.add(safe.lower())
                    out_lines.insert(insert_idx + count, f'{ptype} Property {safe} Auto')
                    count += 1
                out_lines.insert(insert_idx + count, '')

            papyrus = '\n'.join(out_lines)
            out_path = os.path.join(output_dir, f'{script_name}.psc')
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(papyrus)
            stats['qust_ok'] += len(fragments)
            stats['todo_count'] += papyrus.count(';TODO')
        except Exception as e:
            stats['qust_err'] += len(fragments)
            stats['errors'].append(f'QUST {edid}: {e}')


def _write_report(output_dir: str, stats: dict):
    """Write a conversion summary report."""
    report_path = os.path.join(output_dir, '_CONVERSION_REPORT.txt')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('TES4 Script -> Papyrus Conversion Report\n')
        f.write('=' * 50 + '\n\n')
        f.write(f'SCPT records: {stats["scpt_ok"]}/{stats["scpt_total"]} converted\n')
        f.write(f'INFO fragments: {stats["info_ok"]}/{stats["info_total"]} converted\n')
        f.write(f'QUST stage scripts: {stats["qust_ok"]}/{stats["qust_total"]} converted\n')
        total = stats['scpt_ok'] + stats['info_ok'] + stats['qust_ok']
        errs = stats['scpt_err'] + stats['info_err'] + stats['qust_err']
        f.write(f'\nTotal: {total} converted, {errs} errors\n')
        f.write(f';TODO markers: {stats["todo_count"]}\n\n')

        if stats['errors']:
            f.write('Errors:\n')
            for err in stats['errors'][:100]:
                f.write(f'  {err}\n')
            if len(stats['errors']) > 100:
                f.write(f'  ... and {len(stats["errors"]) - 100} more\n')


def _sanitize_name(name: str) -> str:
    """Sanitize a script name for use as a filename."""
    return re.sub(r'[^\w]', '_', name)


def _safe_property_name(name: str) -> str:
    """Return a Papyrus-safe property name, renaming reserved words."""
    # Replace non-word characters with underscore
    safe = re.sub(r'[^\w]', '_', name)
    # Strip leading digits
    safe = re.sub(r'^\d+', '', safe)
    if not safe:
        safe = 'var_' + name.replace(' ', '_')
    low = safe.lower()
    if low in _PAPYRUS_RESERVED:
        return f'my{safe.capitalize()}'
    return safe


# Canonical names for known TES4 globals
_GLOBAL_CANONICAL = {
    'gamehour': 'GameHour', 'gamedayspassed': 'GameDaysPassed',
    'gameday': 'GameDay', 'gamemonth': 'GameMonth', 'gameyear': 'GameYear',
    'timescale': 'TimeScale',
}


def _canonical_global(name: str) -> str:
    """Return the canonical property name for a known global."""
    return _GLOBAL_CANONICAL.get(name.lower(), name)


_RECORD_TYPE_PAPYRUS = {
    'QUST': 'Quest', 'NPC_': 'Actor', 'CREA': 'Actor',
    'FACT': 'Faction', 'GLOB': 'GlobalVariable',
    'SPEL': 'Spell', 'ENCH': 'Enchantment', 'MGEF': 'MagicEffect',
    'CELL': 'Cell', 'WRLD': 'WorldSpace', 'PACK': 'Package',
    'SOUN': 'Sound', 'DIAL': 'Topic', 'RACE': 'Race',
    'FLST': 'FormList', 'KYWD': 'Keyword', 'LVLI': 'LeveledItem',
    'LVLN': 'LeveledActor', 'LVSP': 'LeveledSpell',
    'WEAP': 'Weapon', 'ARMO': 'Armor', 'BOOK': 'Book',
    'ALCH': 'Potion', 'INGR': 'Ingredient', 'LIGH': 'Light',
    'MISC': 'MiscObject', 'KEYM': 'Key', 'AMMO': 'Ammo',
    'ACTI': 'Activator', 'DOOR': 'ObjectReference',
    'CONT': 'ObjectReference', 'STAT': 'ObjectReference',
    'FURN': 'ObjectReference', 'FLOR': 'ObjectReference',
    'ACHR': 'Actor', 'ACRE': 'Actor',
    'REFR': 'ObjectReference',
}


def _record_type_to_papyrus(rtype: str) -> str:
    """Map a TES4 record type to a Papyrus property type."""
    return _RECORD_TYPE_PAPYRUS.get(rtype, 'ObjectReference')


# Player FormID — skip when pre-loading SCRO refs
_PLAYER_FORMID = '00000014'


def _preload_scro_refs(conv: 'ScriptConverter', rec: dict, xref: CrossRefGraph):
    """Pre-populate converter property_refs from SCRO entries in a record."""
    i = 0
    while True:
        key = f'SCRO[{i}]'
        fid = rec.get(key)
        if fid is None:
            break
        i += 1
        _add_scro_ref(conv, fid, xref)


def _preload_stage_scro_refs(conv: 'ScriptConverter', rec: dict, xref: CrossRefGraph,
                              stage_arr_idx: int, log_arr_idx: int):
    """Pre-populate converter property_refs from per-stage/log SCRO entries."""
    k = 0
    while True:
        key = f'Stage[{stage_arr_idx}].Log[{log_arr_idx}].SCRO[{k}]'
        fid = rec.get(key)
        if fid is None:
            break
        k += 1
        _add_scro_ref(conv, fid, xref)


def _add_scro_ref(conv: 'ScriptConverter', fid: str, xref: CrossRefGraph):
    """Add a single SCRO FormID as a property ref on the converter."""
    if fid == _PLAYER_FORMID:
        return
    edid = xref.formid_to_edid.get(fid)
    if not edid:
        return
    rtype = xref.record_type.get(fid, '')
    ptype = _record_type_to_papyrus(rtype)
    # Prefer attached script type for cross-script property access
    script_type = xref.get_record_script_type(edid)
    if script_type:
        ptype = script_type
    conv._property_refs[edid] = ptype


# ===========================================================================
# VMAD binary helpers (for tes5_import integration)
# ===========================================================================

def build_vmad_quest_fragments(quest_edid: str, stage_fragments: list[tuple[int, int]]) -> bytes:
    """Build VMAD binary for a QUST record with stage script fragments.

    Args:
        quest_edid: Quest EditorID
        stage_fragments: list of (stage_index, log_index) tuples

    Returns VMAD binary data.
    """
    script_name = f'TES4_QF_{_sanitize_name(quest_edid)}'
    buf = bytearray()

    # VMAD header
    buf += struct.pack('<HH', 5, 2)  # version=5, objectFormat=2

    # Scripts array: 1 script
    buf += struct.pack('<H', 1)
    buf += _pack_wstring(script_name)
    buf += struct.pack('<B', 0)   # flags=0
    buf += struct.pack('<H', 0)   # propertyCount=0

    # Script fragments (quest type, wbScriptFragmentsQuest):
    #   S8  Extra bind data version = 2
    #   U16 FragmentCount
    #   LenString(U16) FileName
    buf += struct.pack('<b', 2)                  # Extra bind data version = 2
    buf += struct.pack('<H', len(stage_fragments))  # FragmentCount
    buf += _pack_wstring(script_name)            # FileName
    for stage_idx, log_idx in stage_fragments:
        frag_name = f'Fragment_Stage_{stage_idx:04d}_Item_{log_idx}'
        buf += struct.pack('<H', stage_idx)   # stageIndex
        buf += struct.pack('<H', 0)           # unknown
        buf += struct.pack('<I', stage_idx)   # stageIndex (I32)
        buf += struct.pack('<B', 0)           # unknown
        buf += _pack_wstring(script_name)
        buf += _pack_wstring(frag_name)

    return bytes(buf)


def build_vmad_info_fragment(info_formid: str) -> bytes:
    """Build VMAD binary for an INFO record with a result script fragment.

    Args:
        info_formid: INFO FormID string (e.g. "00012345")

    Returns VMAD binary data.
    """
    script_name = f'TES4_TIF__{info_formid}'
    buf = bytearray()

    # VMAD header
    buf += struct.pack('<HH', 5, 2)   # version=5, objectFormat=2
    buf += struct.pack('<H', 0)       # 0 attached scripts

    # Script fragments for INFO (wbScriptFragmentsInfo):
    #   S8  Extra bind data version = 2
    #   U8  Flags: bit0=OnBegin, bit1=OnEnd (no other bits defined for INFO)
    #   LenString(U16) FileName
    #   For each set bit in Flags, one fragment: S8 Unknown + LenString ScriptName + LenString FragmentName
    # Fragment count is implicit (popcount of Flags bits 0-1).
    buf += struct.pack('<b', 2)        # Extra bind data version = 2
    buf += struct.pack('<B', 0x01)     # Flags = OnBegin (1 fragment)
    buf += _pack_wstring(script_name)  # FileName

    # Fragment 0 — OnBegin
    buf += struct.pack('<b', 0)        # Unknown
    buf += _pack_wstring(script_name)  # ScriptName
    buf += _pack_wstring('Fragment_0') # FragmentName

    return bytes(buf)


def _pack_wstring(s: str) -> bytes:
    """Pack a VMAD wstring: U16 length + UTF-8 bytes."""
    encoded = s.encode('utf-8')
    return struct.pack('<H', len(encoded)) + encoded


# ===========================================================================
# CLI
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(description='Convert TES4 scripts to Papyrus')
    parser.add_argument('export_dir', help='Path to export directory (e.g. export/Oblivion.esm)')
    parser.add_argument('-o', '--output', default=None,
                        help='Output dir for .psc files (default: output/oblivion.esm/scripts/source)')
    parser.add_argument('--workers', type=int, default=None, help='Worker threads')
    args = parser.parse_args()

    output_dir = args.output
    if output_dir is None:
        output_dir = os.path.join('output', 'oblivion.esm', 'scripts', 'source')

    convert_all_scripts(args.export_dir, output_dir, args.workers)


if __name__ == '__main__':
    main()
