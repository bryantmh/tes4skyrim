#!/usr/bin/env python3
"""
Oblivion Script to Papyrus Converter

Reads TES4_Records.txt, extracts SCPT_SOURCE records, and converts
Oblivion scripting language to Skyrim's Papyrus as closely as possible.

Oblivion scripts use an imperative, event-block style:
  scriptname MyScript
  short myVar
  begin GameMode
    set myVar to 1
    if myVar == 1
      Message "Hello"
    endif
  end

Papyrus uses an object-oriented, event-driven style:
  ScriptName MyScript extends ObjectReference
  Int Property myVar Auto
  Event OnUpdate()
    myVar = 1
    if myVar == 1
      Debug.Notification("Hello")
    endif
  EndEvent

Many constructs have no direct equivalent and are marked with ;TODO comments.

================================================================================
BRAINSTORM: Getting to fully automatic, complete-functionality conversion
================================================================================

FUNDAMENTAL DIFFERENCES (the hard problems)
  1. Execution model: Oblivion scripts poll every frame (GameMode = while(true)).
     Papyrus is event-driven; persistent polling requires RegisterForSingleUpdate()
     at the end of OnUpdate, which is expensive and causes performance issues
     for scripts that formerly ran every frame for gameplay logic.
  2. Global state: Oblivion scripts run in a single-threaded flat namespace.
     Papyrus is multi-threaded; variable state is per-instance, not global.
     Oblivion "global variables" (GLOB records) become Papyrus GlobalVariable
     properties. Script variables that were effectively global (set on one NPC,
     read on another via GetScript) need redesign.
  3. Compiled bytecode: Papyrus requires compilation by the CK compiler before
     it will load. We output .psc source; a full pipeline also needs to call
     the compiler or package .pex files. The CK compiler is available as
     PapyrusCompiler.exe.
  4. No interpreted expressions: Oblivion allows inline function calls in
     conditions (if GetActorValue Health > 50). Papyrus requires temp variables
     for multi-step expressions.
  5. Reference scope: Oblivion script functions called without a prefix run on
     the attached object. Papyrus uses Self explicitly. Scripts attached to
     non-Actor objects still call Actor functions — must infer type from
     SCHR.Type (0=Object, 1=Quest, 2=Effect/Magic).

IDEA 1 — SCHR.Type-aware extends inference (fix the most common crash)
  SCHR.Type in the exported data tells us the script type:
    0 = Object script   → extends ObjectReference (or Actor for NPC-attached)
    1 = Quest script    → extends Quest
    2 = Effect/Magic    → extends ActiveMagicEffect
  The current code always uses 'extends ObjectReference'. Quest scripts must
  extend Quest or they will not compile. Magic effect scripts must extend
  ActiveMagicEffect and their event names differ (OnEffectStart, not OnActivate).
  Fix: read SCHR.Type from the export, determine which SCRI FormID attached
  this script (stored on QUST/NPC_/CREA/REFR as SCRI subrecord), and choose
  the correct base class.
  Additionally, if a script is attached to NPC_ or CREA records, it should
  extend Actor, not ObjectReference, to get Actor methods without casting.

IDEA 2 — Cross-script reference graph (fix GetScript / SetScript patterns)
  In Oblivion, scripts communicate via:
    let myRef.ScriptVar := value   (write another script's variable)
    set myRef.ScriptVar to value   (same)
    GetScript myRef                (get a script handle — not in Papyrus)
  In Papyrus, inter-script communication requires:
    (myRef as MyScript).ScriptVar = value
  To do this automatically, we need to know which script type each reference
  has. Build a FormID → script_name map from all SCRI attachments in the
  export data, then when we see `myRef.SomeVar`, look up the script on myRef
  and emit `(myRef as ScriptName).SomeVar`.

IDEA 3 — GameMode polling → RegisterForSingleUpdate pattern (fix idle waste)
  Every Oblivion GameMode block needs to become:
    Event OnUpdate()
      ; ... body ...
      RegisterForSingleUpdate(0.1)  ; re-poll at desired interval
    EndEvent
    Event OnInit()
      RegisterForSingleUpdate(0.1)  ; start the update loop
    EndEvent
  The update interval should be configurable — fast scripts (0.016s) vs
  slow scripts (1.0s or more). We can heuristically detect "slow" scripts
  (those that only fire when a condition changes) and use longer intervals.
  Scripts that read GetDistance or GetAngle every frame should use
  RegisterForDistanceGreaterThanEvent / RegisterForDistanceLessThanEvent
  instead of polling, which is both faster and correct.

IDEA 4 — Condition function mapping (complete the function table)
  The current FUNCTION_MAP covers ~40 functions. Oblivion has ~800 functions
  (including OBSE extensions). The full mapping needs:
  a. Vanilla Oblivion functions: documented at UESP wiki. ~200 have Papyrus
     equivalents, ~100 need workarounds, ~100 are engine-only with no equivalent.
  b. OBSE functions: ar_* (array ops), sv_* (string ops), GetNthRef, etc.
     These have no Papyrus equivalents but can often be replaced by standard
     Papyrus data structures (arrays, strings, Forms).
  c. Actor Value functions: GetActorValue/SetActorValue/ModActorValue with the
     correct AV name mapping (ACTOR_VALUE_MAP already exists but is incomplete —
     missing derived stats like WaterBreathing, Paralysis, etc.)
  Key missing functions that appear frequently in Oblivion scripts:
    GetCurrentTime → Utility.GetCurrentGameTime()
    GetDayOfWeek   → no direct equivalent; use GetCurrentGameTime() mod
    GetCurrentWeather → Sky.GetCurrentWeather()
    ForceWeather   → Sky.ForceWeather()
    GetQuestRunning → Quest.IsRunning() [after quest property setup]
    MessageBox with choices → Message form + Show() + GetButtonPressed()
    GetButtonPressed → Message.GetButtonPressed()
    SetCellOwnership → no direct equivalent
    GetCellOwnership → no direct equivalent
    GetOwnership → ObjectReference.GetActorOwner() / GetFactionOwner()
    PlaceAtMe with persist flag → Game.CreateReferenceAtLocation()
    GetNumItems → GetNumItems() [same name, on Container]
    OpenContainerMenu → Activate(Game.GetPlayer(), true) or custom UI
    TriggerHitShader → Game.ShakeCamera() (approximate)
    AddAchievement → Game.AddAchievement()

IDEA 5 — set/get variable disambiguation with script context
  Oblivion: `set SomeRef.VarName to Value` is ambiguous — VarName could be
  a script variable on SomeRef's attached script, or a quest variable if
  SomeRef is a quest. The current converter emits `SomeRef.VarName = Value`
  literally, which is wrong — Papyrus requires explicit casting:
    (SomeRef as AttachedScriptType).VarName = Value
  or for quest variables:
    QuestEditorID.VarName = Value  ; (quest scripts are singleton)
  Full fix requires the cross-script reference graph from Idea 2.
  Partial fix: detect if the ref name matches a known quest EditorID from
  the export and emit `QuestEditorID.VarName = Value` directly.

IDEA 6 — MessageBox choice → Message form + GetButtonPressed
  Oblivion MessageBox supports buttons:
    MessageBox "Title" "Button1" "Button2"
    short result
    set result to GetButtonPressed
    if result == 0 ...
  Papyrus requires a Message form with buttons defined in the plugin, then:
    Int iResult = myMessage.Show()
    If iResult == 0 ...
  Full automation requires:
    a. Detect MessageBox with multiple string args
    b. Create a synthetic MESSAGE record in the output plugin
    c. Replace MessageBox call with myMessage.Show()
    d. Replace GetButtonPressed with the Show() return value
  This is implementable: the converter already produces ESM output, so it
  can allocate new FormIDs for synthetic MESSAGE records.

IDEA 7 — Quest stage script injection as fragment bodies
  TES5 quest stage logic lives in Papyrus "script fragments" attached to the
  quest (via VMAD). In TES4, stage logic is inline SCTX in the INDX/QSDT
  block. The exporter already extracts ResultScript from QUST stage blocks.
  The importer should:
    a. Generate a QuestScript (extends Quest) for each QUST
    b. For each stage with a ResultScript, generate a fragment function:
       Function Fragment_Stage_NNNN_Item_0()
         ; converted stage script body
       EndFunction
    c. Populate the VMAD ScriptFragments array pointing at each fragment
  This is how the CK handles stage scripts natively.

IDEA 8 — Property injection for all referenced FormIDs
  Every FormID that appears as a literal in Oblivion script (e.g. a ref to
  a placed object, an item, a spell) must become a property in Papyrus:
    ObjectReference Property MyRef Auto  ; filled in CK
  The current converter emits bare identifiers. We need to:
    a. Build a FormID → EditorID lookup from the export
    b. When a bare EditorID appears in script (it often does in Oblivion
       scripts as global references), declare a matching property
    c. Emit a {property_fill} block comment listing all properties that need
       to be filled in the CK or via script

IDEA 9 — LLM-assisted translation for complex blocks (post-processing pass)
  Some Oblivion script patterns have no mechanical mapping to Papyrus:
    - Combat state machines
    - Dialogue trigger logic
    - Scripted spell systems
    - Radiant patrol / sandbox scripts
  After mechanical translation (which handles ~70% of code), run a second
  pass using Claude API (claude-haiku-4-5 for cost) to:
    a. Identify ;TODO blocks that are structurally untranslatable
    b. Generate a Papyrus equivalent using semantic understanding
    c. Flag the result with ;AI-GENERATED: REVIEW for human verification
  This is the "last mile" that gets from 70% to 95%+ coverage.
  Input: the Oblivion source + the partially translated Papyrus + the TODO list.
  Prompt structure: include the function map and type map as context so the
  model doesn't hallucinate Papyrus function names.

IDEA 10 — Papyrus compiler integration (close the loop)
  After generating .psc files, automatically invoke PapyrusCompiler.exe
  to compile them and report which files fail to compile and why. Use the
  compiler error output to drive a second conversion pass:
    - "Unknown identifier X" → add property declaration for X
    - "Type mismatch" → add (X as Type) cast
    - "Unknown function Y on type Z" → look up alternate function name
  This creates a feedback loop: convert → compile → fix → repeat until
  compilation succeeds or only genuine semantic gaps remain.
  The compiler is at: <Skyrim install>/Papyrus Compiler/PapyrusCompiler.exe
  Flags: -f="TESV_Papyrus_Flags.flg" -i=<src dir> -o=<output dir> -a

IDEA 12 — Script type classification from usage patterns
  Without SCHR.Type being reliable, infer the script type from content:
    - Contains "setstage", "getstage" → likely Quest script
    - Attached to effect (begin ScriptEffectStart) → ActiveMagicEffect
    - Calls "GetAV", "Kill", "Resurrect" → likely Actor script
    - Calls "Activate", "GetLocked" → likely ObjectReference
    - Has "begin MenuMode" → UI script, wrap in OnMenuOpen/OnMenuClose
  Use these signals to select the correct extends class and available functions.

IDEA 13 — Inline expression decomposition into temp vars
  Oblivion allows complex nested expressions:
    if ((GetActorValue Health) + (GetActorValue Fatigue)) > 100
  Papyrus requires temps:
    Float fHealth = GetActorValue("Health")
    Float fFatigue = GetActorValue("Stamina")
    If (fHealth + fFatigue) > 100.0
  The converter needs an expression parser (not just regex) that decomposes
  function-call subexpressions into auto-named temp variables.
  Approach: build a simple recursive descent parser for Oblivion expressions,
  then flatten into straight-line Papyrus with generated temp names
  (_tmp0, _tmp1, ...).

CURRENT GAPS IN THIS FILE
  - convert_expression() only does token substitution — no AST
  - convert_line() uses regex, not a proper parser; fails on nested calls
  - extract_scripts_from_records() reads only SCTX, not multi-line sources
    (Oblivion SCTX can span multiple lines in the escaped export format)
  - No cross-script reference resolution
  - No SCHR.Type → extends inference
  - No GameMode → RegisterForSingleUpdate injection
  - No quest fragment generation
  - FUNCTION_MAP covers ~5% of the full Oblivion function corpus
================================================================================"""

import re
import os
import sys
from pathlib import Path
from typing import Optional


# ============================================================================
# Oblivion block type -> Papyrus event mapping
# ============================================================================
BLOCK_MAP = {
    'gamemode':       ('Event OnUpdate()', 'EndEvent'),
    'menumode':       ('Event OnUpdate()', 'EndEvent'),
    'onactivate':     ('Event OnActivate(ObjectReference akActionRef)', 'EndEvent'),
    'onadd':          ('Event OnContainerChanged(ObjectReference akNewContainer, ObjectReference akOldContainer)', 'EndEvent'),
    'ondrop':         ('Event OnContainerChanged(ObjectReference akNewContainer, ObjectReference akOldContainer)', 'EndEvent'),
    'onequip':        ('Event OnEquipped(Actor akActor)', 'EndEvent'),
    'onunequip':      ('Event OnUnequipped(Actor akActor)', 'EndEvent'),
    'ondeath':        ('Event OnDeath(Actor akKiller)', 'EndEvent'),
    'onhit':          ('Event OnHit(ObjectReference akAggressor, Form akSource, Projectile akProjectile, bool abPowerAttack, bool abSneakAttack, bool abBashAttack, bool abHitBlocked)', 'EndEvent'),
    'onhitwith':      ('Event OnHit(ObjectReference akAggressor, Form akSource, Projectile akProjectile, bool abPowerAttack, bool abSneakAttack, bool abBashAttack, bool abHitBlocked)', 'EndEvent'),
    'onload':         ('Event OnLoad()', 'EndEvent'),
    'onreset':        ('Event OnReset()', 'EndEvent'),
    'onsell':         ('Event OnSell(Actor akSeller)', 'EndEvent'),
    'ontrigger':      ('Event OnTriggerEnter(ObjectReference akActionRef)', 'EndEvent'),
    'ontriggerenter': ('Event OnTriggerEnter(ObjectReference akActionRef)', 'EndEvent'),
    'ontriggerleave': ('Event OnTriggerLeave(ObjectReference akActionRef)', 'EndEvent'),
    'onmagiceffectapply': ('Event OnMagicEffectApply(ObjectReference akCaster, MagicEffect akEffect)', 'EndEvent'),
    'oninit':         ('Event OnInit()', 'EndEvent'),
    'onpackagestart': ('Event OnPackageStart(Package akNewPackage)', 'EndEvent'),
    'onpackageend':   ('Event OnPackageEnd(Package akOldPackage)', 'EndEvent'),
    'onpackagechange':('Event OnPackageChange(Package akOldPackage)', 'EndEvent'),
    'onalarm':        (';TODO: No Papyrus equivalent for OnAlarm', ''),
    'scripteffectstart':  ('Event OnEffectStart(MagicEffect akActiveEffect, Actor akCaster)', 'EndEvent'),
    'scripteffectfinish': ('Event OnEffectFinish(MagicEffect akActiveEffect, Actor akCaster)', 'EndEvent'),
    'scripteffectupdate': ('Event OnUpdate()', 'EndEvent'),
}

# ============================================================================
# Oblivion type -> Papyrus type mapping
# ============================================================================
TYPE_MAP = {
    'short':  'Int',
    'long':   'Int',
    'int':    'Int',
    'float':  'Float',
    'ref':    'ObjectReference',
    'reference': 'ObjectReference',
}

# ============================================================================
# Oblivion function -> Papyrus function mapping
# Each entry: (papyrus_call, needs_self, note)
#   needs_self: if True, called on the reference (self.Function or ref.Function)
#   note: optional ;TODO comment
# ============================================================================
FUNCTION_MAP = {
    # Actor values
    'getactorvalue':     ('GetActorValue',     True,  None),
    'setactorvalue':     ('SetActorValue',     True,  None),
    'modactorvalue':     ('ModActorValue',     True,  None),
    'forceactorvalue':   ('ForceActorValue',   True,  None),
    'getav':             ('GetActorValue',     True,  None),
    'setav':             ('SetActorValue',     True,  None),
    'modav':             ('ModActorValue',     True,  None),

    # Items / Inventory
    'additem':           ('AddItem',           True,  None),
    'removeitem':        ('RemoveItem',        True,  None),
    'getitemcount':      ('GetItemCount',      True,  None),
    'equipitem':         ('EquipItem',         True,  None),
    'unequipitem':       ('UnequipItem',       True,  None),
    'removeallitems':    ('RemoveAllItems',    True,  None),

    # Spells
    'addspell':          ('AddSpell',          True,  None),
    'removespell':       ('RemoveSpell',       True,  None),
    'hasspell':          ('HasSpell',          True,  None),
    'cast':              ('Cast',              True,  None),

    # Movement / Position
    'moveto':            ('MoveTo',            True,  None),
    'getpos':            ('GetPositionX',      True,  ';TODO: GetPos axis param needs manual conversion'),
    'setpos':            ('SetPosition',       True,  ';TODO: SetPos axis param needs manual conversion'),
    'getangle':          ('GetAngleX',         True,  ';TODO: GetAngle axis param needs manual conversion'),
    'setangle':          ('SetAngle',          True,  ';TODO: SetAngle axis param needs manual conversion'),
    'getdistance':       ('GetDistance',       True,  None),
    'getparentcell':     ('GetParentCell',     True,  None),

    # Enable / Disable
    'enable':            ('Enable',            True,  None),
    'disable':           ('Disable',           True,  None),
    'isenabled':         ('IsEnabled',         True,  None), # technically Is3DLoaded() in some contexts
    'activate':          ('Activate',          True,  None),
    'delete':            ('Delete',            True,  None),
    'markfordelete':     ('Delete',            True,  None),
    'placeatme':         ('PlaceAtMe',         True,  None),

    # Actor state
    'kill':              ('Kill',              True,  None),
    'resurrect':         ('Resurrect',         True,  None),
    'getdead':           ('IsDead',            True,  None),
    'isdead':            ('IsDead',            True,  None),
    'isincombat':        ('IsInCombat',        True,  None),
    'startcombat':       ('StartCombat',       True,  None),
    'stopcombat':        ('StopCombat',        True,  None),
    'getincell':         ('IsInLocation',      True,  ';TODO: GetInCell->IsInLocation needs Location not Cell'),
    'getinsameCell':     ('GetParentCell',     True,  ';TODO: Needs manual comparison'),

    # AI
    'evp':               ('EvaluatePackage',   True,  None),
    'evaluatepackage':   ('EvaluatePackage',   True,  None),
    'setrestrained':     ('SetRestrained',     True,  None),
    'setunconscious':    ('SetUnconscious',    True,  None),
    'setghost':          ('SetGhost',          True,  None),
    'isghost':           ('IsGhost',           True,  None),

    # Quest
    'setstage':          ('SetStage',          False, ';TODO: Needs quest reference'),
    'getstage':          ('GetStageDone',      False, ';TODO: Needs quest reference and stage check'),
    'getstagedone':      ('GetStageDone',      False, None),
    'startquest':        ('Start',             False, ';TODO: Needs quest reference.Start()'),
    'stopquest':         ('Stop',              False, ';TODO: Needs quest reference.Stop()'),
    'setquestobject':    (None,                False, ';TODO: No direct Papyrus equivalent for SetQuestObject'),

    # UI / Messages
    'message':           ('Debug.Notification', False, None),
    'messagebox':        ('Debug.MessageBox',   False, None),
    'showmessage':       ('Debug.MessageBox',   False, None),

    # Game state
    'getgamesetting':    ('Game.GetFormFromFile', False, ';TODO: GetGameSetting needs Game.GetSetting()'),
    'getpcissex':        ('GetSex',            False, ';TODO: Use Game.GetPlayer().GetActorBase().GetSex()'),
    'getpcinfaction':    ('IsInFaction',       False, ';TODO: Use Game.GetPlayer().IsInFaction()'),
    'ispcrace':          ('GetRace',           False, ';TODO: Use Game.GetPlayer().GetRace()'),
    'player.additem':    ('Game.GetPlayer().AddItem', False, None),
    'player.removeitem': ('Game.GetPlayer().RemoveItem', False, None),

    # Sound
    'playsound':         ('Sound.Play',        False, ';TODO: Needs Sound form reference'),
    'playsound3d':       ('Sound.Play',        False, ';TODO: Needs Sound form reference'),

    # Misc
    'wait':              ('Utility.Wait',      False, None),
    'getself':           ('Self',              False, None),
    'getcontainer':      ('GetContainer',      True,  None),
    'showmap':           (None,                False, ';TODO: No Papyrus equivalent for ShowMap'),
    'lock':              ('Lock',              True,  None),
    'unlock':            ('Lock',              True,  ';TODO: Use Lock(false)'),
    'getlocked':         ('IsLocked',          True,  None),
    'setownership':      ('SetActorOwner',     True,  None),
    'getownership':      (None,                False, ';TODO: No direct Papyrus equivalent for GetOwnership'),
    'setscale':          ('SetScale',          True,  None),
    'getscale':          ('GetScale',          True,  None),
    'purgecellbuffers':  (None,                False, ';TODO: No Papyrus equivalent'),
    'closeobliviongate': (None,                False, ';TODO: Oblivion-specific: CloseOblivionGate'),
    'setcrimegold':      (None,                False, ';TODO: Use Faction.SetCrimeGold()'),
    'getcrimegold':      (None,                False, ';TODO: Use Faction.GetCrimeGold()'),
    'getrandompercent':  ('Utility.RandomInt', False, ';TODO: Use Utility.RandomInt(0, 99)'),
    'getlevel':          ('GetLevel',          True,  None),
    'getisrace':         ('GetRace',           True,  ';TODO: Needs comparison: GetRace() == raceForm'),
    'reset3dstate':      (None,                False, ';TODO: No Papyrus equivalent for Reset3DState'),
    'say':               ('Say',               True,  ';TODO: VoiceType/Topic system changed'),
}

# ============================================================================
# Actor Value name mapping (TES4 -> TES5)
# ============================================================================
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
}


class OblivionScript:
    """Parsed representation of an Oblivion script."""

    def __init__(self, name: str, source: str, editor_id: str = ''):
        self.name = name
        self.editor_id = editor_id
        self.source = source
        self.variables: list[tuple[str, str]] = []  # (type, name)
        self.blocks: list[tuple[str, list[str]]] = []  # (blocktype, [lines])
        self.extends = 'ObjectReference'  # default parent
        self.todos: list[str] = []

    def parse(self):
        """Parse the Oblivion script source into structured form."""
        lines = self.source.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        current_block: Optional[str] = None
        current_lines: list[str] = []

        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith(';'):
                if current_block is not None:
                    current_lines.append(raw_line)
                continue

            low = line.lower()

            # Script name
            if low.startswith('scriptname ') or low.startswith('scn '):
                parts = line.split(None, 1)
                if len(parts) > 1:
                    self.name = parts[1].strip()
                continue

            # Variable declarations
            match = re.match(r'^(short|long|int|float|ref|reference)\s+(\w+)', line, re.IGNORECASE)
            if match and current_block is None:
                vtype = match.group(1).lower()
                vname = match.group(2)
                self.variables.append((vtype, vname))
                continue

            # Begin block
            begin_match = re.match(r'^begin\s+(\w+)(.*)', line, re.IGNORECASE)
            if begin_match:
                current_block = begin_match.group(1).lower()
                current_lines = []
                continue

            # End block
            if low == 'end':
                if current_block is not None:
                    self.blocks.append((current_block, current_lines))
                    current_block = None
                    current_lines = []
                continue

            if current_block is not None:
                current_lines.append(raw_line)


def convert_expression(expr: str) -> str:
    """Convert an Oblivion expression to Papyrus."""
    expr = expr.strip()

    # Map actor value names in string literals
    for ob_av, sk_av in ACTOR_VALUE_MAP.items():
        expr = re.sub(r'\b' + ob_av + r'\b', sk_av, expr, flags=re.IGNORECASE)

    # Convert != to !=
    expr = expr.replace('<>', '!=')

    # Convert 'player' reference
    expr = re.sub(r'\bplayer\b', 'Game.GetPlayer()', expr, flags=re.IGNORECASE)

    return expr


def convert_function_call(line: str, indent: str) -> str:
    """Convert an Oblivion function call line to Papyrus."""
    stripped = line.strip()
    low = stripped.lower()
    comment = ''

    # Check for ref.function pattern
    ref_match = re.match(r'^(\w+)\.(\w+)\s*(.*)', stripped, re.IGNORECASE)
    if ref_match:
        ref_name = ref_match.group(1)
        func_name = ref_match.group(2).lower()
        args_str = ref_match.group(3).strip()

        # Special case: player.xxx
        if ref_name.lower() == 'player':
            ref_name = 'Game.GetPlayer()'

        entry = FUNCTION_MAP.get(func_name)
        if entry:
            papyrus_func, needs_self, note = entry
            if note:
                comment = '  ' + note
            if papyrus_func:
                args = convert_expression(args_str) if args_str else ''
                return f'{indent}{ref_name}.{papyrus_func}({args}){comment}'
            else:
                return f'{indent};TODO: {stripped}{comment}'

        # Unknown function on ref
        args = convert_expression(args_str) if args_str else ''
        return f'{indent}{ref_name}.{func_name}({args})  ;TODO: Verify function exists in Papyrus'

    # Standalone function (no ref prefix)
    func_match = re.match(r'^(\w+)\s*(.*)', stripped, re.IGNORECASE)
    if func_match:
        func_name = func_match.group(1).lower()
        args_str = func_match.group(2).strip()

        entry = FUNCTION_MAP.get(func_name)
        if entry:
            papyrus_func, needs_self, note = entry
            if note:
                comment = '  ' + note
            if papyrus_func:
                args = convert_expression(args_str) if args_str else ''
                if needs_self:
                    return f'{indent}{papyrus_func}({args}){comment}'
                else:
                    return f'{indent}{papyrus_func}({args}){comment}'
            else:
                return f'{indent};TODO: {stripped}{comment}'

        # Unknown standalone function
        args = convert_expression(args_str) if args_str else ''
        return f'{indent}{func_match.group(1)}({args})  ;TODO: Verify function exists in Papyrus'

    return f'{indent}{stripped}  ;TODO: Could not parse'


def convert_line(line: str) -> str:
    """Convert a single Oblivion script line to Papyrus."""
    stripped = line.strip()
    if not stripped:
        return ''
    if stripped.startswith(';'):
        return line  # preserve comments with original indentation

    # Calculate indentation
    indent = ''
    for ch in line:
        if ch in (' ', '\t'):
            indent += ch
        else:
            break

    low = stripped.lower()

    # Variable declarations inside blocks
    var_match = re.match(r'^(short|long|int|float|ref|reference)\s+(\w+)', stripped, re.IGNORECASE)
    if var_match:
        vtype = TYPE_MAP.get(var_match.group(1).lower(), 'Int')
        vname = var_match.group(2)
        return f'{indent}{vtype} {vname} = 0'

    # set X to Y => X = Y
    set_match = re.match(r'^set\s+(\S+)\s+to\s+(.*)', stripped, re.IGNORECASE)
    if set_match:
        var_name = set_match.group(1)
        value = convert_expression(set_match.group(2))
        return f'{indent}{var_name} = {value}'

    # let X := Y (OBSE) => X = Y
    let_match = re.match(r'^let\s+(\S+)\s*:=\s*(.*)', stripped, re.IGNORECASE)
    if let_match:
        var_name = let_match.group(1)
        value = convert_expression(let_match.group(2))
        return f'{indent}{var_name} = {value}'

    # if / elseif with condition
    if_match = re.match(r'^(if|elseif)\s+(.*)', stripped, re.IGNORECASE)
    if if_match:
        keyword = 'If' if if_match.group(1).lower() == 'if' else 'ElseIf'
        condition = convert_expression(if_match.group(2))
        return f'{indent}{keyword} {condition}'

    # else
    if low == 'else':
        return f'{indent}Else'

    # endif
    if low == 'endif':
        return f'{indent}EndIf'

    # return
    if low == 'return':
        return f'{indent}Return'

    # Known function calls
    return convert_function_call(line, indent)


def convert_script(script: OblivionScript) -> str:
    """Convert parsed Oblivion script to Papyrus source."""
    script.parse()
    output: list[str] = []

    # Header
    output.append(f'ScriptName {script.name} extends {script.extends}')
    output.append(f'{{Converted from TES4 script: {script.editor_id}}}')
    output.append('')

    # Variables as properties
    for vtype, vname in script.variables:
        ptype = TYPE_MAP.get(vtype, 'Int')
        output.append(f'{ptype} Property {vname} Auto')

    if script.variables:
        output.append('')

    # Convert blocks
    for block_type, block_lines in script.blocks:
        mapping = BLOCK_MAP.get(block_type)
        if mapping:
            event_begin, event_end = mapping
            output.append(event_begin)
            for bline in block_lines:
                converted = convert_line(bline)
                if converted is not None:
                    # Add one level of indentation inside events
                    if converted.strip():
                        output.append('  ' + converted.lstrip())
                    else:
                        output.append('')
            if event_end:
                output.append(event_end)
        else:
            output.append(f';TODO: Unknown block type: begin {block_type}')
            for bline in block_lines:
                converted = convert_line(bline)
                if converted is not None:
                    output.append(converted)
            output.append(';TODO: End unknown block')

        output.append('')

    return '\n'.join(output)


def extract_scripts_from_records(records_path: str) -> list[tuple[str, str, str]]:
    """
    Read TES4_Records.txt and extract SCPT_SOURCE records.
    Returns list of (editor_id, form_id, source_text).
    """
    scripts = []
    in_record = False
    current_data: dict[str, str] = {}

    with open(records_path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.rstrip('\n').rstrip('\r')

            if line == '---RECORD_BEGIN---':
                in_record = True
                current_data = {}
                continue

            if line == '---RECORD_END---':
                if in_record and current_data.get('TargetType') == 'SCPT_SOURCE':
                    editor_id = current_data.get('EditorID', '')
                    form_id = current_data.get('FormID', '')
                    source = current_data.get('SCTX', '')
                    if source:
                        scripts.append((editor_id, form_id, source))
                in_record = False
                current_data = {}
                continue

            if in_record:
                eq_pos = line.find('=')
                if eq_pos > 0:
                    key = line[:eq_pos]
                    value = line[eq_pos + 1:]
                    current_data[key] = value

    return scripts


def extract_info_scripts(records_path: str) -> list[tuple[str, str, str]]:
    """
    Extract result scripts from INFO records.
    Returns list of (editor_id/form_id label, form_id, source_text).
    """
    scripts = []
    in_record = False
    current_data: dict[str, str] = {}

    with open(records_path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.rstrip('\n').rstrip('\r')

            if line == '---RECORD_BEGIN---':
                in_record = True
                current_data = {}
                continue

            if line == '---RECORD_END---':
                if in_record and current_data.get('TargetType') == 'INFO':
                    form_id = current_data.get('FormID', '')
                    source = current_data.get('ResultScript.Source', '')
                    if source and source.strip():
                        scripts.append((f'INFO_{form_id}', form_id, source))
                in_record = False
                current_data = {}
                continue

            if in_record:
                eq_pos = line.find('=')
                if eq_pos > 0:
                    key = line[:eq_pos]
                    value = line[eq_pos + 1:]
                    current_data[key] = value

    return scripts


def main():
    # Determine paths
    script_dir = Path(__file__).parent
    records_path = script_dir / 'TES4_Records.txt'
    output_dir = script_dir / 'Papyrus_Output'

    if not records_path.exists():
        print(f'ERROR: {records_path} not found.')
        print('Run TES4_Export_Records.pas in xEdit first to generate the records file.')
        sys.exit(1)

    print(f'Reading records from: {records_path}')
    print('Extracting SCPT_SOURCE records...')
    scripts = extract_scripts_from_records(str(records_path))
    print(f'Found {len(scripts)} script records.')

    print('Extracting INFO result scripts...')
    info_scripts = extract_info_scripts(str(records_path))
    print(f'Found {len(info_scripts)} INFO result scripts.')

    all_scripts = scripts + info_scripts

    if not all_scripts:
        print('No scripts found to convert.')
        sys.exit(0)

    # Create output directory
    output_dir.mkdir(exist_ok=True)

    converted = 0
    errors = 0
    todo_count = 0

    for editor_id, form_id, source in all_scripts:
        try:
            script = OblivionScript(
                name=editor_id or f'Script_{form_id}',
                source=source,
                editor_id=editor_id
            )
            papyrus = convert_script(script)

            # Count TODOs
            file_todos = papyrus.count(';TODO:')
            todo_count += file_todos

            # Write output
            safe_name = re.sub(r'[^\w\-.]', '_', editor_id or f'Script_{form_id}')
            out_path = output_dir / f'{safe_name}.psc'
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(papyrus)
            converted += 1
        except Exception as e:
            print(f'  ERROR converting {editor_id}: {e}')
            errors += 1

    # Write summary report
    report_path = output_dir / '_CONVERSION_REPORT.txt'
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write('Oblivion to Papyrus Conversion Report\n')
        f.write('=' * 50 + '\n\n')
        f.write(f'Total scripts found: {len(all_scripts)}\n')
        f.write(f'  SCPT records: {len(scripts)}\n')
        f.write(f'  INFO result scripts: {len(info_scripts)}\n')
        f.write(f'Successfully converted: {converted}\n')
        f.write(f'Errors: {errors}\n')
        f.write(f'Total ;TODO markers: {todo_count}\n\n')
        f.write('Note: Scripts marked with ;TODO require manual review.\n')
        f.write('Papyrus is fundamentally different from Oblivion scripting.\n')
        f.write('All converted scripts need testing and likely manual adjustment.\n\n')
        f.write('Common manual fixes needed:\n')
        f.write('  - Add Form properties for referenced objects\n')
        f.write('  - Replace player references with Game.GetPlayer()\n')
        f.write('  - Convert MessageBox with choices to Message form + Show()\n')
        f.write('  - Rewrite quest stage logic for Papyrus quest system\n')
        f.write('  - Add RegisterForSingleUpdate() for GameMode scripts\n')

    print(f'\nConversion complete:')
    print(f'  Converted: {converted}')
    print(f'  Errors:    {errors}')
    print(f'  TODO markers: {todo_count}')
    print(f'\nOutput written to: {output_dir}')
    print(f'Report: {report_path}')


if __name__ == '__main__':
    main()
