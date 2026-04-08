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
    'setrestrained':     ('SetRestrained',     True,  None),
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
    'getisrace':         ('GetRace',           True,  ';TODO: Needs == comparison'),
    'isactordetected':   ('IsDetectedBy',      True,  None),
    'getdetected':       ('IsDetectedBy',      True,  None),
    'getincell':         (None,                True,  ';TODO: GetInCell -> compare GetParentCell()'),
    'getinsamecell':     (None,                True,  ';TODO: Compare GetParentCell() on both refs'),
    'getissex':          ('GetActorBase',      True,  ';TODO: GetIsSex -> GetActorBase().GetSex()'),
    'issneaking':        ('IsSneaking',        True,  None),
    'isweaponout':       ('IsWeaponDrawn',     True,  None),
    'isswimming':        ('IsSwimming',        True,  None),
    'getisalerted':      (None,                True,  ';TODO: No Papyrus equivalent for GetIsAlerted'),
    'setessential':      (None,                False, ';TODO: ActorBase.SetEssential() needed'),
    'getisplayablerace': (None,                True,  ';TODO: Check Race.IsPlayable()'),
    'istalking':         ('IsInDialogueWithPlayer', True, None),
    'setunconscious':    ('SetUnconscious',    True,  None),
    'setghost':          ('SetGhost',          True,  None),
    'isghost':           ('IsGhost',           True,  None),
    'setcrimegold':      (None,                False, ';TODO: Use Faction.SetCrimeGold()'),
    'getcrimegold':      (None,                False, ';TODO: Use Faction.GetCrimeGold()'),
    'modcrimegold':      (None,                False, ';TODO: Use Faction.ModCrimeGold()'),
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
    'wait':              ('Utility.Wait',      False, None),

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
    'getpcissex':        (None,                False, ';TODO: Game.GetPlayer().GetActorBase().GetSex()'),
    'getpcinfaction':    (None,                False, ';TODO: Game.GetPlayer().IsInFaction()'),
    'ispcrace':          (None,                False, ';TODO: Game.GetPlayer().GetRace()'),
    'getrandompercent':  ('Utility.RandomInt',  False, None),
    'getlevel':          ('GetLevel',           True,  None),
    'isininterior':      ('IsInInterior',       True,  None),
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
    'unlock':            ('Lock',              True,  ';TODO: Use Lock(false)'),
    'getlocked':         ('IsLocked',          True,  None),
    'getlocklevel':      ('GetLockLevel',      True,  None),
    'setownership':      ('SetActorOwner',     True,  None),
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
    'setfactionreaction':(None,                False, ';TODO: Use Faction.SetReaction()'),
    'isactionref':       (None,                False, None),  # Special: compare akActionRef
    'getactionref':      (None,                False, None),  # Special: returns akActionRef
    'iscurrentfurnitureref': (None,            True,  ';TODO: No direct equivalent'),
    'iscurrentfurnitureobj': (None,            True,  ';TODO: No direct equivalent'),
    'showenchantment':   (None,                False, ';TODO: No Papyrus equivalent'),
    'triggerscreenblood':(None,                False, ';TODO: Use Game.TriggerScreenBlood()'),
    'isonguard':         (None,                True,  ';TODO: No direct equivalent'),
    'setactorfullname':  ('SetDisplayName',    True,  None),
    'setcellfullname':   (None,                True,  ';TODO: No Papyrus equivalent'),

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
        self.quest_edids: set[str] = set()
        self.npc_formids: set[str] = set()

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
        formid = edid = scri = None
        schr_type = None

        for line in content.split('\n'):
            line = line.rstrip()
            if line == '---RECORD_BEGIN---':
                in_record = True
                formid = edid = scri = None
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

    def convert_standalone(self, name: str, source: str, extends: str = 'ObjectReference',
                           editor_id: str = '') -> str:
        """Convert a standalone SCPT record to a full .psc file."""
        self._reset()
        variables, blocks = self._parse_source(source)

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

        # Variable declarations as properties
        for vtype, vname in variables:
            ptype = TYPE_MAP.get(vtype, 'Int')
            if ptype == 'Float':
                out.append(f'{ptype} Property {vname} = 0.0 Auto')
            else:
                out.append(f'{ptype} Property {vname} Auto')

        if variables:
            out.append('')

        # Convert blocks
        needs_oninit_update = self._has_gamemode or self._has_scripteffectupdate
        gamemode_body = []

        for block_type, block_lines in blocks:
            if block_type in ('gamemode', 'menumode', 'scripteffectupdate'):
                gamemode_body.extend(block_lines)
                continue

            mapping = BLOCK_MAP.get(block_type)
            if mapping:
                event_begin, event_end = mapping
                if not event_begin.startswith(';'):
                    out.append(event_begin)
                    for bline in block_lines:
                        converted = self._convert_line(bline, extends)
                        out.append(f'  {converted}')
                    out.append(event_end)
                else:
                    out.append(event_begin)
                    for bline in block_lines:
                        converted = self._convert_line(bline, extends)
                        out.append(f'  {converted}')
                    if event_end:
                        out.append(event_end)
                out.append('')

        # Emit OnUpdate for GameMode/MenuMode/ScriptEffectUpdate
        if gamemode_body:
            interval = self._get_update_interval()
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

        # Insert property declarations for referenced FormIDs
        if self._property_refs:
            insert_idx = 3 + len(variables) + (1 if variables else 0)
            prop_lines = ['; --- External references (fill in CK) ---']
            for pname, ptype in sorted(self._property_refs.items()):
                prop_lines.append(f'{ptype} Property {pname} Auto')
            prop_lines.append('')
            for i, pl in enumerate(prop_lines):
                out.insert(insert_idx + i, pl)

        return '\n'.join(out)

    def convert_fragment(self, source: str, extends: str = 'Quest') -> list[str]:
        """Convert a script fragment body (not a full script).

        Returns list of converted lines (indented for function body).
        """
        self._reset()
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
        return result

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

        low = stripped.lower()

        # Variable declarations inside blocks -> local vars
        var_m = re.match(r'^(short|long|int|float|ref|reference)\s+(\w+)', stripped, re.IGNORECASE)
        if var_m:
            ptype = TYPE_MAP.get(var_m.group(1).lower(), 'Int')
            vname = var_m.group(2)
            if ptype == 'Float':
                return f'{ptype} {vname} = 0.0'
            return f'{ptype} {vname} = 0'

        # set X to Y
        set_m = re.match(r'^set\s+(\S+)\s+to\s+(.*)', stripped, re.IGNORECASE)
        if set_m:
            target = self._convert_ref(set_m.group(1), extends)
            value = self._convert_expression(set_m.group(2), extends)
            return f'{target} = {value}'

        # let X := Y (OBSE)
        let_m = re.match(r'^let\s+(\S+)\s*:=\s*(.*)', stripped, re.IGNORECASE)
        if let_m:
            target = self._convert_ref(let_m.group(1), extends)
            value = self._convert_expression(let_m.group(2), extends)
            return f'{target} = {value}'

        # if / elseif
        if_m = re.match(r'^(if|elseif)\s+(.*)', stripped, re.IGNORECASE)
        if if_m:
            keyword = 'If' if if_m.group(1).lower() == 'if' else 'ElseIf'
            condition = self._convert_expression(if_m.group(2), extends)
            return f'{keyword} {condition}'

        if low == 'else':
            return 'Else'
        if low == 'endif':
            return 'EndIf'
        if low == 'return':
            return 'Return'

        return self._convert_function_call(stripped, extends)

    def _convert_expression(self, expr: str, extends: str) -> str:
        """Convert an Oblivion expression to Papyrus."""
        expr = expr.strip()
        if not expr:
            return expr

        expr = expr.replace('<>', '!=')

        # Split on logical operators first, convert each part independently
        # Handle || and &&
        if '||' in expr:
            parts = expr.split('||')
            converted = [self._convert_expression(p.strip(), extends) for p in parts]
            return ' || '.join(converted)
        if '&&' in expr:
            parts = expr.split('&&')
            converted = [self._convert_expression(p.strip(), extends) for p in parts]
            return ' && '.join(converted)

        # TES4 boolean functions compared to 1/0 (e.g., "IsActionRef player == 1")
        bool_comp_m = re.match(
            r'^(IsActionRef|GetDead|IsDead|IsInCombat|IsSneaking|IsWeaponOut|IsSwimming|'
            r'IsGhost|GetLocked|IsEnabled|HasSpell|GetInFaction|GetQuestRunning|GetStageDone|'
            r'GetDetected|IsActorDetected)\s+(.+?)\s*==\s*([01])\s*$',
            expr, re.IGNORECASE)
        if bool_comp_m:
            fname = bool_comp_m.group(1)
            args_part = bool_comp_m.group(2)
            bool_val = bool_comp_m.group(3)
            converted_call = self._emit_function(None, fname, args_part, extends)
            if bool_val == '0':
                return f'!({converted_call})'
            return converted_call

        # Handle comparison operators: split into LHS op RHS
        comp_m = re.match(r'^(.+?)\s*(==|!=|>=|<=|>|<)\s*(.+)$', expr)
        if comp_m:
            lhs = self._convert_expression(comp_m.group(1).strip(), extends)
            op = comp_m.group(2)
            rhs = self._convert_expression(comp_m.group(3).strip(), extends)
            return f'{lhs} {op} {rhs}'

        # Handle function calls in expressions: "funcname arg1 arg2"
        # Route through _emit_function for special-case handling
        func_in_expr = re.match(r'^(\w+)\s+(.+)$', expr)
        if func_in_expr:
            fname = func_in_expr.group(1).lower()
            if fname in FUNCTION_MAP or fname in ('getstage', 'getstagedone', 'setstage',
                    'startquest', 'stopquest', 'getquestrunning', 'getrandompercent',
                    'getpos', 'getangle', 'setpos', 'setangle', 'getself',
                    'getactionref', 'isactionref', 'message', 'messagebox'):
                return self._emit_function(None, func_in_expr.group(1),
                                           func_in_expr.group(2).strip(), extends)

        # Handle ref.Func in expressions (only if no parens yet — avoid re-matching)
        ref_func = re.match(r'^(\w+)\.(\w+)\s*((?:[^(].*)?)', expr)
        if ref_func and '(' not in ref_func.group(2):
            return self._emit_function(ref_func.group(1), ref_func.group(2),
                                       ref_func.group(3).strip(), extends)

        # Terminal substitutions (applied last, after all function matching)
        expr = re.sub(r'\bplayer\b', 'Game.GetPlayer()', expr, flags=re.IGNORECASE)
        expr = re.sub(r'\bgetSelf\b', 'Self', expr, flags=re.IGNORECASE)
        expr = re.sub(r'\bGetSecondsPassed\b', '0.5', expr, flags=re.IGNORECASE)

        # Actor value name substitution
        for ob_av, sk_av in ACTOR_VALUE_MAP.items():
            expr = re.sub(r'\b' + ob_av + r'\b', sk_av, expr, flags=re.IGNORECASE)

        return expr

    def _convert_ref(self, name: str, extends: str) -> str:
        """Convert an Oblivion reference name to Papyrus."""
        low = name.lower()
        if low == 'player':
            return 'Game.GetPlayer()'
        if low in ('getself', 'myself', 'self'):
            return 'Self'

        if '.' in name:
            parts = name.split('.', 1)
            ref_part = self._convert_ref(parts[0], extends)
            return f'{ref_part}.{parts[1]}'

        if self.xref.is_quest_ref(name):
            self._property_refs[name] = 'Quest'
            return name

        return name

    def _convert_args(self, args_str: str, func_name: str, extends: str) -> str:
        """Convert Oblivion function arguments to Papyrus."""
        if not args_str:
            return ''

        # Actor value functions: first arg is AV name -> quoted string
        av_funcs = {'getactorvalue', 'setactorvalue', 'modactorvalue', 'forceactorvalue',
                     'getav', 'setav', 'modav', 'forceav', 'getbaseactorvalue', 'getbaseav'}
        if func_name in av_funcs:
            parts = args_str.split(None, 1)
            av_name = parts[0]
            sk_av = ACTOR_VALUE_MAP.get(av_name.lower(), av_name)
            rest = f', {self._convert_expression(parts[1], extends)}' if len(parts) > 1 else ''
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

    def _emit_function(self, ref_name: Optional[str], func_name: str,
                       args_str: str, extends: str) -> str:
        """Emit a converted function call."""
        fname_low = func_name.lower()

        # --- Special case functions ---

        if fname_low == 'getself':
            return 'Self'

        if fname_low == 'getactionref':
            return 'akActionRef'

        if fname_low == 'isactionref':
            arg = self._convert_expression(args_str, extends) if args_str else ''
            return f'akActionRef == {arg}'

        # GetPos/GetAngle: axis param -> GetPositionX/Y/Z or GetAngleX/Y/Z
        if fname_low in ('getpos', 'getangle'):
            axis = args_str.strip().upper() if args_str else 'X'
            if axis not in ('X', 'Y', 'Z'):
                axis = 'X'
            if fname_low == 'getpos':
                papyrus = f'GetPosition{axis}'
            else:
                papyrus = f'GetAngle{axis}'
            ref = self._convert_ref(ref_name, extends) if ref_name else 'Self'
            return f'{ref}.{papyrus}()' if ref_name else f'{papyrus}()'

        # SetPos/SetAngle: axis param -> SetPosition(x,y,z) / SetAngle(x,y,z)
        if fname_low in ('setpos', 'setangle'):
            parts = args_str.split(None, 1) if args_str else ['X', '0']
            axis = parts[0].upper() if parts else 'X'
            value = self._convert_expression(parts[1], extends) if len(parts) > 1 else '0'
            ref = self._convert_ref(ref_name, extends) if ref_name else 'Self'
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
            parts = args_str.split(None, 1) if args_str else []
            if parts:
                quest_ref = parts[0]
                stage = self._convert_expression(parts[1], extends) if len(parts) > 1 else ''
                self._property_refs[quest_ref] = 'Quest'
                papyrus = {'setstage': 'SetStage', 'getstage': 'GetStage',
                           'getstagedone': 'GetStageDone'}[fname_low]
                return f'{quest_ref}.{papyrus}({stage})'

        # StartQuest/StopQuest/GetQuestRunning: arg is quest
        if fname_low in ('startquest', 'stopquest', 'getquestrunning'):
            quest_ref = args_str.strip() if args_str else 'quest'
            self._property_refs[quest_ref] = 'Quest'
            papyrus = {'startquest': 'Start', 'stopquest': 'Stop',
                        'getquestrunning': 'IsRunning'}[fname_low]
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

        # --- Standard function map lookup ---
        entry = FUNCTION_MAP.get(fname_low)
        if entry:
            papyrus_func, needs_self, note = entry
            if papyrus_func is None:
                orig = f'{ref_name}.{func_name} {args_str}'.strip() if ref_name else f'{func_name} {args_str}'.strip()
                return f';TODO: {orig}' + (f'  {note}' if note else '')
            args = self._convert_args(args_str, fname_low, extends) if args_str else ''
            if ref_name:
                ref = self._convert_ref(ref_name, extends)
                result = f'{ref}.{papyrus_func}({args})'
            else:
                result = f'{papyrus_func}({args})'
            return f'{result}  {note}' if note else result

        # --- Unknown function ---
        args = self._convert_args(args_str, fname_low, extends) if args_str else ''
        if ref_name:
            ref = self._convert_ref(ref_name, extends)
            return f'{ref}.{func_name}({args})  ;TODO: Verify'
        return f'{func_name}({args})  ;TODO: Verify'

    def _quote_msg(self, args_str: str) -> str:
        """Quote a message argument if not already quoted."""
        s = args_str.strip()
        if s.startswith('"'):
            return s
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
            body_lines = conv.convert_fragment(result_script, 'Quest')

            script_name = f'TES4_TIF__{formid}'
            out_lines = [
                f'ScriptName {script_name} extends TopicInfo Hidden',
                '',
                'Function Fragment_0()',
            ]
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

        fragments = []  # (stage_index, log_idx, script_source)
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
                    fragments.append((stage_idx, j, script))

        if not fragments:
            continue

        stats['qust_total'] += len(fragments)

        try:
            conv = ScriptConverter(xref)
            script_name = f'TES4_QF_{_sanitize_name(edid)}'
            out_lines = [
                f'ScriptName {script_name} extends Quest Hidden',
                '',
            ]

            for stage_idx, log_idx, script_src in fragments:
                func_name = f'Fragment_Stage_{stage_idx:04d}_Item_{log_idx}'
                out_lines.append(f'Function {func_name}()')
                body_lines = conv.convert_fragment(script_src, 'Quest')
                out_lines.extend(body_lines)
                out_lines.append('EndFunction')
                out_lines.append('')

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

    # Script fragments (quest type)
    buf += struct.pack('<B', 0)  # unknownByte
    buf += _pack_wstring(script_name)

    buf += struct.pack('<H', len(stage_fragments))
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

    buf += struct.pack('<HH', 5, 2)
    buf += struct.pack('<H', 0)       # 0 persistent scripts

    # Script fragments (topic info type)
    buf += struct.pack('<B', 0)       # unknownByte
    buf += _pack_wstring(script_name)

    buf += struct.pack('<H', 1)       # 1 fragment
    buf += struct.pack('<B', 0)
    buf += _pack_wstring(script_name)
    buf += _pack_wstring('Fragment_0')

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
