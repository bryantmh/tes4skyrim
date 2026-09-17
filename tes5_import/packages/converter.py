"""TES4 PACK -> TES5 PACK conversion.

Goal: Oblivion-equivalent AI behaviour expressed in Skyrim syntax.

TES4 encodes behaviour in PKDT.Type (0=Find .. 11=CastMagic) plus PLDT (a
location) and PTDT (a target).  TES5 encodes it as a *template instance*: the
package points PKCU.PackageTemplate at one of the stock Skyrim template roots
(which owns the procedure tree) and supplies that root's data inputs.

So the conversion is:

    TES4 PKDT.Type  ->  which Skyrim template root
    TES4 PLDT       ->  the template's Location input   (same 12-byte struct,
                        same type enum for 0..5 — copied, not approximated)
    TES4 PTDT       ->  the template's Target input     (PTDA, types 0..2 map 1:1)
    TES4 PSDT       ->  PSDT           (hours -> minutes on Duration)
    TES4 CTDA       ->  CTDA           (existing dialog_conditions translator)
    TES4 PKDT.Flags ->  PKDT flags     (re-derived; the bit layouts differ)

Locations, schedules and conditions are *copied*, so an NPC keeps the same
destination, the same hours and the same activation logic.  Only the procedure
is re-expressed in Skyrim's vocabulary.

See docs/commentary/tes5_import_package.md for the fidelity analysis (which TES4 types
map exactly, which degrade, and why).
"""

import struct

from .templates import (
    ACQUIRE,
    ESCORT,
    EAT,
    FLEE_TO,
    FOLLOW,
    FORCE_GREET,
    HOLD_POSITION,
    PKDT_TYPE_PACKAGE,
    SANDBOX,
    SIT,
    SIT_TARGET,
    ACTIVATE,
    SLEEP,
    TRAVEL,
    T_BOOL,
    T_FLOAT,
    T_LOCATION,
    T_TOPIC,
    T_SINGLEREF,
    T_TARGETSEL,
    USE_MAGIC,
    Template,
)
from ..base.conditions import convert_ctda_list_with_strings
from ..base.text_reader import (get_formid, get_int, get_str, remap_formid,
                          PLAYER_REF_FID, PLAYER_BASE_FID)

from ..base.writer import (
    pack_formid_subrecord,
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
)

# Legacy alias: most call sites here mean "the player's REFERENCE".
PLAYER_FID = PLAYER_REF_FID


def _targets_player(rec: dict) -> bool:
    """True when this package's PTDT names the player, either spelling.

    Oblivion writes "the player" as the REFERENCE 0x14 or, just as often, as
    Object-ID + the player's base NPC_ 0x07.  Every player-specific branch
    below has to accept both or it silently takes the generic path.
    """
    return get_formid(rec, 'PTDT.Target') in (PLAYER_REF_FID, PLAYER_BASE_FID)

# Fallback SEARCH area for an Object-ID target when no interior cell can be
# named (see PackContext.search_ground): a wide radius around the actor's own
# position, so it roams the area rather than being pinned to its spawn by the
# default type-3 "near editor location" radius 0.  Type 12 "near self" with a
# 3000 radius is the vanilla predator package's own Hunt Location
# (DefaultPredatorPackage slot 4); WEJS21HunterWander uses type 2 radius 5000.
_SEARCH_NEAR_SELF = (12, 0, 3000)

# --- TES4 PKDT.Type ------------------------------------------------------
T4_FIND = 0
T4_FOLLOW = 1
T4_ESCORT = 2
T4_EAT = 3
T4_SLEEP = 4
T4_WANDER = 5
T4_TRAVEL = 6
T4_ACCOMPANY = 7
T4_USEITEMAT = 8
T4_AMBUSH = 9
T4_FLEE = 10
T4_CASTMAGIC = 11

# Types whose target is a specific reference we must route through a quest
# alias when the package is quest-owned (see resolve_target()).
REF_TARGET_TYPES = frozenset({T4_FIND, T4_FOLLOW, T4_ESCORT, T4_ACCOMPANY,
                              T4_USEITEMAT, T4_AMBUSH, T4_CASTMAGIC})

# --- TES4 PKDT flags (wbDefinitionsTES4.pas:3844) ------------------------
T4_OFFERS_SERVICES = 0x00000001
T4_MUST_COMPLETE = 0x00000004
T4_LOCK_DOORS_START = 0x00000008
T4_LOCK_DOORS_END = 0x00000010
T4_UNLOCK_DOORS_START = 0x00000040
T4_UNLOCK_DOORS_END = 0x00000080
T4_CONTINUE_IF_PC_NEAR = 0x00000200
T4_ONCE_PER_DAY = 0x00000400
T4_ALWAYS_RUN = 0x00002000
T4_ALWAYS_SNEAK = 0x00020000
T4_ALLOW_SWIMMING = 0x00040000
T4_WEAPONS_UNEQUIPPED = 0x00200000
T4_DEFENSIVE_COMBAT = 0x00400000
T4_USE_HORSE = 0x00800000
T4_NO_IDLE_ANIMS = 0x01000000

# --- TES5 PKDT flags (wbDefinitionsTES5.pas:11116) -----------------------
T5_OFFERS_SERVICES = 0x00000001
T5_MUST_COMPLETE = 0x00000004
T5_MAINTAIN_SPEED = 0x00000008
T5_UNLOCK_DOORS_START = 0x00000040
T5_UNLOCK_DOORS_END = 0x00000080
T5_CONTINUE_IF_PC_NEAR = 0x00000200
T5_ONCE_PER_DAY = 0x00000400
T5_PREFERRED_SPEED = 0x00002000
T5_ALWAYS_SNEAK = 0x00020000
T5_ALLOW_SWIMMING = 0x00040000
T5_IGNORE_COMBAT = 0x00100000
T5_WEAPONS_UNEQUIPPED = 0x00200000
T5_WEAPON_DRAWN = 0x00800000
T5_NO_COMBAT_ALERT = 0x08000000

# The TES4 bit layout is NOT the TES5 layout (TES4 0x8 = "lock doors at start",
# TES5 0x8 = "maintain speed at goal"), so flags are re-derived per bit.  Bits
# with no TES5 counterpart are DROPPED, never mapped onto an "Unknown NN" bit —
# that would set arbitrary engine behaviour.
_FLAG_MAP = (
    (T4_OFFERS_SERVICES, T5_OFFERS_SERVICES),
    (T4_MUST_COMPLETE, T5_MUST_COMPLETE),
    (T4_UNLOCK_DOORS_START, T5_UNLOCK_DOORS_START),
    (T4_UNLOCK_DOORS_END, T5_UNLOCK_DOORS_END),
    (T4_CONTINUE_IF_PC_NEAR, T5_CONTINUE_IF_PC_NEAR),
    (T4_ONCE_PER_DAY, T5_ONCE_PER_DAY),
    (T4_ALWAYS_SNEAK, T5_ALWAYS_SNEAK),
    (T4_ALLOW_SWIMMING, T5_ALLOW_SWIMMING),
    (T4_WEAPONS_UNEQUIPPED, T5_WEAPONS_UNEQUIPPED),
)

# PKDT preferred speed
SPEED_WALK, SPEED_JOG, SPEED_RUN, SPEED_FASTWALK = 0, 1, 2, 3

# PKDT Interrupt Flags (TES5 only).  These authorise an actor to INTERRUPT the
# running package to speak: 0x01 Hellos to player, 0x02 Random conversations,
# 0x80 Allow Idle Chatter, plus combat/corpse/aggro observation bits.
#
# Oblivion has NO equivalent.  Its complete package flag set (xEdit
# wbDefinitionsCommon.pas:7635 wbPackageFlags, and UESP "Oblivion Mod:Mod File
# Format/PACK") is Offers Services / Must Reach Location / Must Complete /
# Lock+Unlock Doors / Continue If PC Near / Once Per Day / Skip Fallout
# Behavior / Always Run / Always Sneak / Allow Swimming / Allow Falls / Armor
# Unequipped / Weapons Unequipped / Defensive Combat / Use Horse / No Idle
# Anims.  Not one concerns dialogue -- "No Idle Anims" is idle ANIMATIONS
# ("Turns off Idle animations during package", CS wiki).  Oblivion gates
# ambient chatter GLOBALLY through GMSTs instead (see AMBIENT_GMST_OVERRIDES
# in tes5_import/constants.py), never per package.
#
# So there is nothing to convert from and no per-package rule to derive.  This
# value was 0xFFFF, which is not a translation of anything: per UESP, 0xFFFF is
# precisely what the CK's "Set all interrupt flags" button writes.  Every one of
# the 7,209 converted packages therefore force-authorised all nine interrupts,
# so every NPC was permanently allowed to break off whatever they were doing to
# greet or chatter -- the "NPCs quip every few seconds, anywhere, even mid-
# scripted-sequence" defect.
#
# The over-correction to 0x0000 caused the OPPOSITE defect: actors locked in a
# package stopped responding to COMBAT.  The CharacterGen ambushes stood in a
# swords-out staring match until the player threw the first punch, because
# these flags gate more than chatter -- 0x04 "Observe combat behavior", 0x10
# "Reaction to player actions" and 0x40 "Aggro Radius Behavior" authorise the
# actor to break off the package and fight.  A census of Skyrim.esm's 5,961
# packages shows 0x0000 is NOT a neutral default: the 1,411 vanilla packages
# carrying it are scene lockdowns by name (dunCGAlduinBaitStayAtLinkedRef-
# NoCombat, pelagiusHoldPosSleepIgnoreCombat, CWFinaleEnemyLeaderWaitFor-
# Execution, MQ206PaarthurnaxCombatHoldPosition, DefaultMasterPackageNo-
# Interrupt, MQ106GuardsFleePatrol...), while ordinary live-your-life packages
# authorise the behaviour bits (observe-combat is set on 64.2%, aggro-radius
# on 56.3%, reaction-to-player on 56.4%).
#
# TES4 packages never gate combat response at all (its only lever is the
# Defensive Combat flag, which we deliberately drop — see
# project_defensive_combat_flag), so the faithful default is: COMBAT bits ON
# (an actor in a package still fights, exactly as in Oblivion), everything
# VOCAL OFF.  That includes 0x10 "Reaction to player actions": it authorises
# spoken reaction comments, and a converted scene actor barking one over a
# scripted Say line disturbs conversation timing — the same reason the
# chatter bits (hellos/random conversations/idle chatter/corpse greets) stay
# governed by Oblivion's global GMST pacing rather than per-package
# authorisation.
DEFAULT_INTERRUPT = 0x0044  # observe combat 0x04 | aggro radius 0x40

# TES4 record signatures a package target's BASE can carry, by what the actor
# does with one.  ('CHAI'/'BED ' used to be listed here, but those are TES5
# wbObjectTypeEnum names, never TES4 signatures — only FURN can match.)
FURNITURE_SIGS = frozenset({'FURN'})
# Carriable items: what a TES4 Find(Object ID) picks UP -> TES5 Acquire.
ITEM_SIGS = frozenset({'WEAP', 'ARMO', 'CLOT', 'BOOK', 'INGR', 'ALCH', 'MISC',
                       'KEYM', 'LIGH', 'SGST', 'SLGM', 'AMMO', 'APPA'})

# Base types that an actor OPERATES rather than approaches: a lever, switch,
# crumbling wall, or door.  A TES4 package aimed at one of these means "go and
# activate that thing", and the thing's own OnActivate script is what advances
# the quest — so it must become a TES5 Activate template, never a Sandbox.
OPERABLE_SIGS = frozenset({'ACTI', 'DOOR', 'CONT'})
# Placed actors, as ref_base_sig reports them (ACHR -> 'NPC_', ACRE -> 'CREA').
ACTOR_SIGS = frozenset({'NPC_', 'CREA'})


def _operate_target(rec: dict, ctx: 'PackContext') -> bool:
    """True when this package's target is a specific ref the actor must ACTIVATE.

    Covers both TES4 idioms that mean "go operate that object":
      * UseItemAt (type 8) at a non-furniture ref  — Renault at the prison wall
        switch, CharacterGen stage 18.
      * Find (type 0) at an ACTI/DOOR/CONT ref     — the CharacterGen rats at
        CGCrumbleWall01REF (CGRatAmbushAPushBricks), SE32PullLever,
        MS92BlackBrugoOpenSwitch, SE06GSWardenUnlockMainDoor, and 20 others.

    Oblivion's "Find" is a seek-then-use procedure; Skyrim has no standalone
    equivalent, and sandboxing these left the actor inert next to the object so
    the object's OnActivate script never ran and the quest stalled forever.
    """
    if get_int(rec, 'PTDT.Type', -1) != 0:
        return False
    target = get_formid(rec, 'PTDT.Target')
    if not target or target == PLAYER_FID:
        return False
    sig = ctx.base_sig_of(target)
    if not sig:
        return False
    ptype = get_int(rec, 'PKDT.Type', -1)
    if ptype == T4_USEITEMAT:
        # A static is "operated" too: Relmyna studying her atronach STAT
        # (SE09RelmynaStudyAtronach) walks up to it and stays; Activate on a
        # STAT is a harmless no-op that leaves the actor there.  Carriable
        # items are NOT — activating Sinderion's mortar (MS39, an APPA ref)
        # would pick it up, so those take the sandbox fallback in _choose.
        return sig in OPERABLE_SIGS or sig == 'STAT'
    if ptype == T4_FIND:
        return sig in OPERABLE_SIGS
    return False


def _f32(v: float) -> bytes:
    return struct.pack('<f', float(v))


def _u32(v: int) -> bytes:
    return struct.pack('<I', int(v) & 0xFFFFFFFF)


# ---------------------------------------------------------------------------
# Data inputs
# ---------------------------------------------------------------------------

class Inputs:
    """Positional data-input values for one template instance.

    Starts from the template's vanilla defaults so every slot the converter
    does not drive still carries a value a real Skyrim package would carry.
    """

    def __init__(self, template: Template):
        self.t = template
        self.values = dict(template.defaults)

    def set(self, name: str, value):
        self.values[self.t.slot(name)] = value

    def set_slot(self, idx: int, value):
        self.values[idx] = value

    def emit(self) -> bytes:
        """ANAM(+CNAM/PLDT/PTDA) per slot, in the template's declared order."""
        out = b''
        for i, atype in enumerate(self.t.inputs):
            out += pack_string_subrecord('ANAM', atype)
            v = self.values.get(i)
            if atype == T_LOCATION:
                if isinstance(v, tuple):        # (type, target, radius)
                    v = _location(*v)
                out += pack_subrecord('PLDT', v if isinstance(v, bytes)
                                      else _null_location())
            elif atype in (T_SINGLEREF, T_TARGETSEL):
                if isinstance(v, tuple):        # (type, target, count)
                    v = _target(*v)
                out += pack_subrecord('PTDA', v if isinstance(v, bytes)
                                      else _null_target())
            elif atype == T_TOPIC:
                # PDTO: (u32 type, u32 formid); type 0 names a DIAL record.
                # This input is what opens dialogue for a ForceGreet package.
                out += pack_subrecord('PDTO', struct.pack('<II', 0,
                                                          int(v or 0)))
            elif atype == T_BOOL:
                # Bool CNAM is a single byte (verified against vanilla).
                out += pack_subrecord('CNAM', bytes([1 if v else 0]))
            elif atype == T_FLOAT:
                out += pack_subrecord('CNAM', _f32(v or 0.0))
            else:  # T_INT, T_OBJECTLIST — u32
                out += pack_subrecord('CNAM', _u32(v or 0))
        # The UNAM index list and XNAM are the template's public-input
        # signature: copied verbatim, not computed.
        for idx in self.t.index_list:
            out += pack_subrecord('UNAM', struct.pack('<b', idx))
        out += pack_subrecord('XNAM', bytes([self.t.xnam]))
        return out


def _null_location() -> bytes:
    # Type 3 = "near editor location", the harmless vanilla default.
    return struct.pack('<iIi', 3, 0, 0)


def _location(ltype: int, target: int, radius: int) -> bytes:
    """A PLDT payload: (type u32, target/formid i32, radius i32)."""
    return struct.pack('<iIi', ltype, target, radius)


# wbObjectTypeEnum values used by vanilla PTDA type-2 ("Object Type") defaults.
OBJTYPE_FOOD = 15
OBJTYPE_CHAIR = 27
OBJTYPE_BED = 26

# TES4 PTDT type-2 "Object Type" -> TES5 wbObjectTypeEnum.  The TWO ENUMS
# DIFFER (xEdit wbDefinitionsTES4.pas ~3110 vs wbDefinitionsTES5.pas 2636):
# TES4 has Apparatus at 2 and Clothing at 5, so every entry after Activators
# is shifted, and TES4 keeps NPCs/Creatures/Soul Gems as kinds Skyrim folds
# into "Actors: Any" or has no word for.  A TES4 value copied through as-is
# lands on the wrong kind — TES4 "Furniture" (12) is TES5 "Ammo".
TES4_TO_TES5_OBJECT_TYPE = {
    0: 0,    # None
    1: 1,    # Activators
    2: 0,    # Apparatus            -> (no TES5 kind)
    3: 2,    # Armor
    4: 3,    # Books
    5: 17,   # Clothing             -> All: Wearable (Skyrim clothes are ARMO)
    6: 4,    # Containers
    7: 5,    # Doors
    8: 6,    # Ingredients
    9: 7,    # Lights
    10: 8,   # Miscellaneous
    11: 9,   # Flora
    12: 10,  # Furniture
    13: 11,  # Weapons: Any
    14: 12,  # Ammo
    15: 25,  # NPCs                 -> Actors: Any
    16: 25,  # Creatures            -> Actors: Any
    17: 0,   # Soul Gems            -> (no TES5 kind)
    18: 13,  # Keys
    19: 14,  # Alchemy
    20: 15,  # Food
    21: 16,  # All: Combat Wearable
    22: 17,  # All: Wearable
    23: 18,  # Weapons: None
    24: 19,  # Weapons: Melee
    25: 20,  # Weapons: Ranged
    26: 21,  # Spells: Any
    27: 22,  # Spells: Range Target
    28: 23,  # Spells: Range Touch
    29: 24,  # Spells: Range Self
    30: 21, 31: 21, 32: 21, 33: 21, 34: 21, 35: 21,   # Spells: School X -> Any
}
# The TES4 object types an actor can pick UP (Find -> Acquire), sit ON, or
# hunt/seek — the type-2 twin of ITEM_SIGS / FURNITURE_SIGS / ACTOR_SIGS.
TES4_OBJTYPE_ITEMS = frozenset({3, 4, 5, 8, 9, 10, 13, 14, 17, 18, 19, 20,
                                21, 22, 23, 24, 25})
TES4_OBJTYPE_FURNITURE = frozenset({12})
TES4_OBJTYPE_ACTORS = frozenset({15, 16})


def object_criteria_kind(t_type: int, value: int, sig: str = '') -> str:
    """'actor' / 'item' / 'furniture' / '' for a PTDT criteria: a type-1
    Object ID (judged by its base's signature) or a type-2 Object Type
    (judged by the TES4 enum)."""
    if t_type == 1:
        if sig in ACTOR_SIGS:
            return 'actor'
        if sig in ITEM_SIGS:
            return 'item'
        if sig in FURNITURE_SIGS:
            return 'furniture'
        return ''
    if t_type == 2:
        if value in TES4_OBJTYPE_ACTORS:
            return 'actor'
        if value in TES4_OBJTYPE_ITEMS:
            return 'item'
        if value in TES4_OBJTYPE_FURNITURE:
            return 'furniture'
    return ''


def _target(ttype: int, target: int, count: int) -> bytes:
    """A PTDA payload: (type u32, target/formid i32, count i32)."""
    return struct.pack('<iIi', ttype, target, count)


def _null_target() -> bytes:
    # Type 6 = Self — vanilla's own filler for a TargetSelector it doesn't
    # point anywhere (PTDA hex 06000000 00000000 00000000 appears in
    # Skyrim.esm). A type-0 "Specific Reference" with FormID 0 (our old
    # default) is what triggered the CK's "Unable to find Package Target
    # Reference (00000000)" warning.
    return struct.pack('<iIi', 6, 0, 0)


def build_object_type_target(obj_type: int) -> bytes:
    """PTDA type 2 = Object Type — 'any object of this kind', not a specific
    FormID.  This is what every vanilla Eat/Sleep instance uses for a
    TargetSelector it doesn't pin to a specific ref; a type-0 "Specific
    Reference" with FormID 0 (our old default) is what triggers CKPE's
    "Unable to find Package Target Reference (00000000)" warning at runtime."""
    return struct.pack('<iIi', 2, obj_type, 0)


def build_location(loc_type: int, value: int, radius: int) -> bytes:
    """TES4 PLDT -> TES5 PLDT.  Location types 0..5 are the same enum in both
    games, and vanilla Skyrim uses every one we lean on (type 1 'in cell'
    appears 448x), so this is a copy, not an approximation.  Type 5's payload
    is an object-TYPE enum, and THAT enum differs between the games (see
    TES4_TO_TES5_OBJECT_TYPE), so it is translated; the caller passes the raw
    TES4 value."""
    if loc_type < 0 or loc_type > 5:
        return _null_location()
    if loc_type == 5:
        value = TES4_TO_TES5_OBJECT_TYPE.get(int(value), 0)
    return struct.pack('<iIi', loc_type, value & 0xFFFFFFFF, radius)


def build_target(t_type: int, target: int) -> bytes:
    """TES4 PTDT -> TES5 PTDA.  Types 0 (specific ref), 1 (object id) and
    2 (object type) map 1:1.

    The third field is NOT TES4's Count.  xEdit names it 'Count / Distance'
    (wbDefinitionsTES5.pas ~8665) and for a Specific-Reference target the
    engine reads it as the DISTANCE the actor must be within to act on the
    target -- so TES4's usage Count lands in a slot that means something else
    entirely.  CGRenoteOpenSecretDoor carries PTDT.Count=1, which became
    "activate this switch only from within 1 unit": Renault was handed the
    package (log shows her taking 1A04D84D for a single frame at dist=120),
    could not satisfy it, and the engine dropped her straight back to
    CGRenoteWalkToMarkerB (GetStage >= 15, still true at 18) -- so she stood
    at the switch forever and the secret door never opened.

    Skyrim never uses the field: ALL 3,740 PTDA records in Skyrim.esm +
    Dawnguard + Dragonborn + HearthFires + Update have it at 0, across every
    target type (0/1/2/3/4/6).  There is nothing to translate, so write 0.
    """
    if t_type < 0 or t_type > 2:
        return _null_target()
    if t_type == 2:
        # An object TYPE, not a FormID: the TES4 enum value, translated (the
        # caller passes the raw TES4 value — see resolve_target).
        target = TES4_TO_TES5_OBJECT_TYPE.get(int(target), 0)
    return struct.pack('<iIi', t_type, target & 0xFFFFFFFF, 0)


def build_alias_target(alias_index: int) -> bytes:
    """PTDA type 4 = Ref Alias — how a quest package names an actor."""
    return struct.pack('<iii', 4, alias_index, 0)


def build_alias_location(alias_index: int, radius: int = 0) -> bytes:
    """PLDT type 8 = 'Alias (reference)' — a location given by a REFERENCE alias.

    Type 9 is 'Alias (location)', which resolves a LOCATION-type alias (LCTN);
    handing it a reference-alias index resolves to nothing, so the package
    procedure starts (the actor stands up) and then has nowhere to go.  Census
    of Skyrim.esm PLDT types: 8 appears 585x, 9 appears **once** in 6,838
    packages — 9 is effectively unused, 8 is the attested way a quest package
    names a reference destination (WERoad11EscortNoHorse: PLDT type 8, alias
    0x22).  Enum from xEdit wbLocationEnum (wbDefinitionsTES5.pas:2620).
    """
    return struct.pack('<iii', 8, alias_index, radius)


# ---------------------------------------------------------------------------
# PKDT / PSDT
# ---------------------------------------------------------------------------

def convert_flags(t4_flags: int, pack_type: int,
                  hostile_ambush: bool = True,
                  quest_gated: bool = False) -> tuple:
    """TES4 PKDT flags -> (TES5 flags, preferred speed).

    Returns the speed separately because TES4's "always run" is a *flag* while
    TES5's speed is a *field* (plus the 0x2000 'use preferred speed' opt-in).

    `hostile_ambush` gates the weapon-drawn/sneak flags an Ambush package would
    otherwise always get — see the T4_AMBUSH branch in _choose(): the type also
    covers scripted APPROACHES (CharacterGen's `CGEmperorGreetPlayerInCell`).
    """
    flags = 0
    for t4_bit, t5_bit in _FLAG_MAP:
        if t4_flags & t4_bit:
            flags |= t5_bit

    # "Once Per Day" latches the package to at most one run per 24 game-hours.
    # On an Oblivion *routine* package that is the intent, but on a QUEST-gated
    # one the GetStage condition already scopes when it may run, and the daily
    # latch actively breaks it: a persistent actor loaded since game start is
    # already counted as having used the package today, so the engine takes the
    # package for a single frame and immediately drops the actor back to the
    # next passing package.  That is CharacterGen stage 18 — Renault took
    # CGRenoteOpenSecretDoor (ONCE_PER_DAY set) and fell straight back to
    # CGRenoteWalkToMarkerB, so the secret door never opened.  Whether the latch
    # was already spent depends on the in-game clock when the stage fired, which
    # is why it intermittently worked.  676 of 7,209 TES4 packages set this bit,
    # 235 of them UseItemAt, so this is not one record's problem.
    if quest_gated:
        flags &= ~T5_ONCE_PER_DAY

    speed = SPEED_WALK
    if t4_flags & T4_ALWAYS_RUN:
        speed = SPEED_RUN
        flags |= T5_PREFERRED_SPEED

    # TES4 "Defensive Combat" is NOT TES5 "Ignore Combat" — they occupy the same
    # bit (20) but mean opposite things, and mapping one onto the other told
    # every Oblivion bodyguard to stand still and be killed.
    #
    #   TES4 Defensive Combat : do not START fights, but DO fight back.
    #   TES5 Ignore Combat    : take no part in combat at all.
    #
    # UESP's Oblivion "The Killing Field" talk page describes the TES4 flag's
    # own symptom exactly — "the brothers won't attack the goblins unless
    # provoked... one just stands there... remove [defensive combat]" — and the
    # TES5 flag is what the "Horses Ignore Combat" mod uses to make a horse a
    # passive bystander ("everything else still attacks the horse").
    #
    # This was CharacterGen's ambush: `CGGlenroyDefendEmperorAmbushA` — the
    # package whose entire job is to DEFEND the Emperor — carries Defensive
    # Combat, so the converted guards drew their swords and then watched the
    # assassins kill Renault and each other.  All four packages the Blades run
    # during the ambush (DefendEmperorAmbushA / BladesWaitToMove / ToMarkerF /
    # AccompanyEmperorToC) had the flag.
    #
    # Skyrim has no Defensive Combat equivalent (Skyrim Mod:Mod File Format/PACK
    # lists no such flag), and it does not need one: an actor's aggression tier
    # already decides whether it initiates, and every actor retaliates when
    # attacked.  TES5's default IS TES4's Defensive Combat, so the correct
    # conversion is to drop the bit.  Setting Ignore Combat instead is actively
    # harmful — vanilla reserves it for actors who must stay OUT of a scripted
    # fight (horses, MQ101 stand-still archers, CWFinaleEnemyLeaderWaitFor-
    # Execution), never for a bodyguard.  388 of 7,209 TES4 packages set the
    # TES4 bit, so this suppressed combat well beyond CharacterGen.

    # TES4 "lock doors at start/end" has no TES5 flag — the Sleep template owns
    # door-locking as a procedure input instead.  Dropped deliberately.
    # TES4 defensive-combat / armor-unequipped / allow-falls / no-idle-anims:
    # no TES5 counterpart.  Dropped.  Use-horse IS honored — it becomes the
    # template "Ride Horse?" input in _choose().

    if pack_type == T4_AMBUSH and hostile_ambush:
        # Wait hidden, weapon out, don't call for help.  NOT applied to an
        # Ambush that targets the player as a scripted approach — drawing a
        # weapon there reads as hostility and suppresses the force-greet the
        # package exists to set up (Uriel at the prison cell).
        flags |= T5_WEAPON_DRAWN | T5_NO_COMBAT_ALERT | T5_ALWAYS_SNEAK

    return flags, speed


def build_pkdt(flags: int, speed: int,
               interrupt: int = DEFAULT_INTERRUPT) -> bytes:
    """PKDT: Flags u32, Type u8, InterruptOverride u8, PreferredSpeed u8,
    pad u8, InterruptFlags u16, pad u16  (12 bytes)."""
    return struct.pack('<IBBBBHH', flags, PKDT_TYPE_PACKAGE, 0, speed, 0,
                       interrupt, 0)


def build_psdt(rec: dict) -> bytes:
    """TES4 PSDT -> TES5 PSDT.

    Same schedule concept in both games; the one real conversion is Duration,
    which is HOURS in TES4 and MINUTES in TES5.  Miss it and a 6-hour sleep
    package becomes a 6-minute nap.
    """
    month = get_int(rec, 'PSDT.Month', -1)
    dow = get_int(rec, 'PSDT.DayOfWeek', -1)
    date = get_int(rec, 'PSDT.Date', 0)
    hour = get_int(rec, 'PSDT.Time', -1)
    duration_hours = get_int(rec, 'PSDT.Duration', 0)

    # TES5 splits TES4's hour-only time into hour + minute.  Vanilla writes
    # minute=-1 ("Any") on 5,771/5,961 packages — an explicit 0 only ever
    # accompanies an explicit hour, so mirror that contract.
    if hour < -1 or hour > 23:
        hour = -1
    minute = 0 if hour >= 0 else -1
    return struct.pack('<bbBbb3xi', _s8(month), _s8(dow), date & 0xFF,
                       _s8(hour), _s8(minute), duration_hours * 60)


def _s8(v: int) -> int:
    v = int(v)
    if v < -128:
        return -128
    if v > 127:
        return 127
    return v


# ---------------------------------------------------------------------------
# Target / location resolution
# ---------------------------------------------------------------------------

class PackContext:
    """Per-import context: how a package's refs resolve.

    Wraps the PackagePlan (built in Phase 0, shared with the QUST converter so
    alias indices cannot drift) plus the script-variable table needed to
    translate GetScriptVariable conditions.
    """

    def __init__(self, plan=None, script_vars=None, greeting_topic=0,
                 ref_base_sig=None, base_sig=None, base_placements=None,
                 interior_cells=None,
                 ref_cell=None, pack_runner_cells=None,
                 pack_runner_refs=None, actor_pos=None):
        self.plan = plan
        # raw24 PACK fid -> the raw24 ACHR/ACRE refs that run it, and raw24
        # ACHR/ACRE -> (x, y, z): a hunt's seek chain is ordered nearest-first
        # from the hunter's own placement (hunt_chain_targets).
        self.pack_runner_refs = pack_runner_refs or {}
        self.actor_pos = actor_pos or {}
        # source PACK fid -> [(seek PACK fid, target ref fid), ...] for the
        # hunts expanded into a Follow chain (import_main fills this from
        # hunt_chain_targets + writer.derive_formid).
        self.hunt_chains = {}
        self.script_vars = script_vars or {}
        # Converted FormID of the GREETING topic (TES4 DIAL 0x000000C8).  A
        # ForceGreet package opens THIS -- Oblivion's force-greet raised the
        # dialogue menu, whose greeting comes from the shared GREETING topic.
        self.greeting_topic = greeting_topic
        # raw24 REFR fid -> its BASE record signature ('ACTI', 'FURN', ...).
        # UseItemAt has to know whether its target is furniture (sit) or
        # something to operate (activate); see _choose().
        self.ref_base_sig = ref_base_sig or {}
        # raw24 BASE fid -> its own signature ('NPC_', 'CREA', 'WEAP', ...).
        # A PTDT of type 1 (Object ID) names a base record, not a placed ref,
        # and the base's KIND decides the procedure: an actor base is a
        # hunt/visit, an item base is an Acquire, a furniture base a Sit.
        self.base_sig = base_sig or {}
        # raw24 BASE fid -> tuple of (placed ref fid [remapped], raw24 cell)
        # for every ACHR/ACRE/REFR of that base.  Answers two questions about
        # an Object-ID target: "is there exactly ONE of these placed" (then the
        # Object ID names that ref, see sole_placement) and "which cell do
        # they live in" (the hunting/search ground, see search_ground).
        self.base_placements = base_placements or {}
        # raw24 fids of INTERIOR cells.  A PLDT type-1 "in cell" location is
        # only ever an interior in vanilla (448/448 in Skyrim.esm); an
        # exterior cell is never handed to it.
        self.interior_cells = interior_cells or set()
        # raw24 REFR/ACHR fid -> its ParentCELL, and raw24 PACK fid -> the set
        # of cells the actors running it stand in.  Together these answer "can
        # this actor walk there" — see location_reachable().
        self.ref_cell = ref_cell or {}
        self.pack_runner_cells = pack_runner_cells or {}

    def base_sig_of(self, ref_fid: int) -> str:
        return self.ref_base_sig.get(ref_fid & 0x00FFFFFF, '')

    def sig_of_base(self, base_fid: int) -> str:
        return self.base_sig.get(base_fid & 0x00FFFFFF, '')

    def is_actor_base(self, fid: int) -> bool:
        return self.sig_of_base(fid) in ACTOR_SIGS

    def sole_placement(self, base_fid: int) -> int:
        """The ONE placed ref of this base, or 0 when there are none/several.

        An Object-ID target with exactly one placement in the plugin IS that
        reference — the two spellings are equivalent by construction, so the
        package can be given the reference and routed exactly like a
        specific-reference target (alias, Travel-to-ref).
        """
        refs = self.base_placements.get(base_fid & 0x00FFFFFF)
        if refs and len(refs) == 1:
            return refs[0][0]
        return 0

    def _unique_interior(self, cells) -> int:
        """The single interior cell in `cells`, else 0."""
        if cells and len(cells) == 1:
            cell = next(iter(cells))
            if cell in self.interior_cells:
                return cell
        return 0

    def search_ground(self, pack_fid: int, base_fid: int = 0,
                      ref_fid: int = 0, radius: int = 0):
        """Where to look for a criteria/item target with no authored location.

        TES4's Find/UseItemAt with no PLDT searches the loaded area.  Skyrim's
        Find/Sandbox procedures need a Location, and vanilla's own "search
        this whole place" idiom is PLDT type 1 ("in cell", interiors only):
        MQ202SandboxInRatway sweeps the whole Ratway that way,
        MS09Stage25JonAcquireNote searches a whole house.  The authored ground
        is where the TARGETS are placed; if they are spread over several cells
        (or the base is script-spawned and placed nowhere), the runners' own
        cell; failing an interior on either side, a wide radius around the
        actor's own position (vanilla WEJS21HunterWander / the predator
        package's Hunt Location, type 12 "near self").
        """
        placements = (self.base_placements.get(base_fid & 0x00FFFFFF) or ()
                      if base_fid else ())
        cells = {c for _r, c in placements if c}
        if ref_fid and self.ref_cell.get(ref_fid & 0x00FFFFFF):
            cells = {self.ref_cell[ref_fid & 0x00FFFFFF]}
        cell = self._unique_interior(cells)
        if not cell:
            cell = self._unique_interior(
                self.pack_runner_cells.get(pack_fid & 0x00FFFFFF))
        if cell:
            return build_location(1, remap_formid(cell), 0)
        if radius > 0:
            return _location(_SEARCH_NEAR_SELF[0], 0, radius)
        return _location(*_SEARCH_NEAR_SELF)

    def runner_origin(self, pack_fid: int):
        """The (x, y, z) the package's single runner stands at, else None."""
        refs = self.pack_runner_refs.get(pack_fid & 0x00FFFFFF)
        if refs and len(refs) == 1:
            return self.actor_pos.get(next(iter(refs)))
        return None

    def location_reachable(self, pack_fid: int, ref_fid: int) -> bool:
        """Can the actor running this package WALK to this reference?

        Only a same-cell destination is reachable: Skyrim's Escort/Travel
        procedures need a navmesh route, and there is none between two
        interiors.  Unknown either side -> True, so a missing index never
        silently downgrades a package that was fine.
        """
        if not self.ref_cell:
            return True
        dest = self.ref_cell.get(ref_fid & 0x00FFFFFF)
        if dest is None:
            return True
        cells = self.pack_runner_cells.get(pack_fid & 0x00FFFFFF)
        if not cells:
            return True
        return dest in cells

    def quest_of(self, pack_fid: int):
        if self.plan is None:
            return None
        return self.plan.owner_quest.get(pack_fid)

    def alias_for(self, pack_fid: int, ref_fid: int):
        q = self.quest_of(pack_fid)
        if q is None or self.plan is None:
            return None
        return self.plan.alias_of(q, ref_fid)


def resolve_target(rec: dict, ctx: PackContext, pack_fid: int) -> bytes:
    """TES4 PTDT -> PTDA, routing specific refs through a quest alias when the
    package belongs to a quest.

    This is what makes escort/follow work: Skyrim resolves a package's actor
    target through a quest reference alias (PTDA type 4), which is also how the
    package outranks the actor's standing schedule.  A player target is
    normalized to PlayerRef before the alias lookup, never left as the base
    NPC_.
    See: docs/commentary/tes5_import_package.md#player-target-is-the-reference
    """
    t_type = get_int(rec, 'PTDT.Type', -1)
    if t_type < 0:
        return _null_target()
    if t_type == 2:
        # export_PACK writes an Object-Type target as the DECIMAL enum value;
        # get_formid would read "12" as hex and then shift it into our
        # load-order index.  build_target translates the TES4 enum.
        return build_target(2, get_int(rec, 'PTDT.Target', 0))
    target = get_formid(rec, 'PTDT.Target')

    if t_type == 1 and target == PLAYER_BASE_FID:
        t_type, target = 0, PLAYER_REF_FID

    if t_type == 0:
        if not target:
            # TES4 "specific reference" slot left empty (CastMagic-at-self
            # packages) — a type-0 PTDA with FormID 0 is the CK's "Unable to
            # find Package Target Reference (00000000)".
            return _null_target()
        alias = ctx.alias_for(pack_fid, target)
        if alias is not None:
            return build_alias_target(alias)
    return build_target(t_type, target)


def resolve_location(rec: dict, ctx: PackContext, pack_fid: int) -> bytes:
    loc_type = get_int(rec, 'PLDT.Type', -1)
    if loc_type < 0:
        return _null_location()
    if loc_type == 5:
        # "Near any object of this TYPE".  Skyrim's engine does not resolve
        # type 4/5 locations (measured live 2026-08-18: a type-4 location
        # patched into a running package left the actor standing), so the
        # search area stands in: the runners' interior cell, else a radius
        # around the actor itself.
        return ctx.search_ground(pack_fid, radius=get_int(rec, 'PLDT.Radius'))
    value = get_formid(rec, 'PLDT.Location')
    radius = get_int(rec, 'PLDT.Radius', 0)

    if loc_type == 4 and value:
        # "Near any object of this BASE".  When the plugin places exactly one,
        # that IS the reference: say so as a type-0 location, the form
        # Skyrim uses 4,048 times.  Otherwise the placements' cell (type 4
        # itself is dead in the engine, see above).
        ref = ctx.sole_placement(value)
        if ref:
            loc_type, value = 0, ref
        else:
            return ctx.search_ground(pack_fid, value, radius=radius)
    if loc_type == 0:
        if not value:
            return _null_location()   # empty "near reference" slot (see resolve_target)
        alias = ctx.alias_for(pack_fid, value)
        if alias is not None:
            return build_alias_location(alias, radius)
    return build_location(loc_type, value, radius)


def _has_location(rec: dict) -> bool:
    return get_int(rec, 'PLDT.Type', -1) >= 0


def _has_target(rec: dict) -> bool:
    return get_int(rec, 'PTDT.Type', -1) >= 0


def _approaches_ref(rec: dict, ptype: int) -> bool:
    """True for an Ambush on a SPECIFIC reference: a scripted walk-up."""
    return ptype == T4_AMBUSH and get_int(rec, 'PTDT.Type', -1) == 0


# ---------------------------------------------------------------------------
# TES4 type -> template + inputs
# ---------------------------------------------------------------------------

def _authored_or_search_ground(rec: dict, ctx: PackContext, pack_fid: int,
                               base: int, loc: bytes, ref: int = 0) -> bytes:
    """The area a criteria Find works in: the authored PLDT when the package
    has one, else the target's own ground (see search_ground)."""
    if _has_location(rec) and get_formid(rec, 'PLDT.Location'):
        return loc
    return ctx.search_ground(pack_fid, base, ref)


def _find_object_criteria(rec: dict, ctx: PackContext, pack_fid: int,
                          loc: bytes, tgt: bytes, kind: str,
                          base: int = 0) -> Inputs:
    """TES4 Find whose target is a CRITERIA rather than a reference: a type-1
    Object ID naming a BASE record with several placements (or none), or a
    type-2 Object Type — "go and find one of THESE".

    The criteria's KIND (object_criteria_kind) picks the procedure — this is
    what TES4's single Find package did implicitly:

      * an ACTOR base is a HUNT (or a search for someone): FGC06's three
        fighters Find FGC06Goblin (nine placed, 1,400 units away and ~350
        below them on another level of the mine), FGD08's Blackwood Company
        Find their goblins, FGC01's lions Find the rats.  Oblivion walked the
        actor to the nearest one and let faction hostility start the fight.
        The SEEKING is done by the Follow chain import_main plans from
        hunt_chain_targets (one Follow per placed target, nearest first, gated
        on alive/enabled/same cell); THIS record is the chain's TAIL — a
        wander-only Sandbox in the prey's cell (vanilla MQ202SandboxInRatway)
        for when no target is left to follow.  Measured live 2026-08-18: the
        Sandbox alone keeps the fighters within ~300 units of spawn, and a
        PLDT type-4 "Object ID" location leaves them standing.
      * an ITEM base is a pick-up: Oblivion's Find picks the item up (the
        beggars' food Finds, Bruscus Dannus's dropped-weapon Finds, the
        goblins' totem-staff Finds).  Skyrim's Acquire template is exactly
        "search this area, walk to a matching item, take it", and its criteria
        slot takes a type-1 base in vanilla (MQ101RalofGetDoorKey).
        PTDT.Count is how many to find (FGD08 count 11 for 11 goblins,
        FGC06 count 10 for 9), so it is Acquire's "num to acquire".
      * a FURNITURE base is a sit: Skyrim's Sit template searches the area
        for a chair matching a criteria that vanilla also spells as a type-1
        FURN base (MG06Stage99MirabelleGetIntoFurniture).
      * anything else (a container/door base to activate, a plant to harvest)
        keeps the sandbox fallback: no vanilla template pairs Find with
        Activate that has any instances.
    """
    ground = _authored_or_search_ground(rec, ctx, pack_fid, base, loc)
    if kind == 'actor':
        i = Inputs(SANDBOX)
        i.set('location', ground)
        i.set('allow_wandering', 1)
        # A hunt is not a social errand: no sitting, no idle markers, no
        # furniture — those are what park a sandboxing actor in one spot.
        i.set('allow_sitting', 0)
        i.set('allow_idle_markers', 0)
        i.set('allow_special_furniture', 0)
        i.set('allow_eating', 0)
        i.set('allow_sleeping', 0)
        i.set('allow_conversation', 0)
        return i
    if kind == 'item':
        i = Inputs(ACQUIRE)
        i.set('location', ground)
        i.set('target', tgt)
        i.set('count', max(1, get_int(rec, 'PTDT.Count', 0) or 0))
        return i
    if kind == 'furniture':
        i = Inputs(SIT)
        i.set('location', ground)
        i.set('chair_target', tgt)
        return i
    i = Inputs(SANDBOX)
    i.set('location', loc)
    i.set('allow_wandering', 1)
    i.set('allow_sitting', 1)
    i.set('allow_idle_markers', 1)
    return i


class _Pick:
    """The TES4 package facts every template handler reads.

    Frozen for the whole of one `_choose` call. A handler that needs a
    derived value (a float radius, a resolved base) computes its own.
    """

    __slots__ = ('rec', 'ctx', 'pack_fid', 'loc', 'tgt', 'radius',
                 'use_horse', 'greet_topic')

    def __init__(self, rec, ctx, pack_fid, loc, tgt, radius, use_horse,
                 greet_topic):
        self.rec = rec
        self.ctx = ctx
        self.pack_fid = pack_fid
        self.loc = loc
        self.tgt = tgt
        self.radius = radius
        self.use_horse = use_horse
        self.greet_topic = greet_topic


def _sandbox(loc, **flags) -> Inputs:
    """A Sandbox at `loc` with the named allow_* flags set."""
    i = Inputs(SANDBOX)
    i.set('location', loc)
    for name, value in flags.items():
        i.set(name, value)
    return i


def _pick_travel(p: _Pick) -> Inputs:
    """Travel: exact."""
    i = Inputs(TRAVEL)
    i.set('location', p.loc)
    if p.use_horse:
        i.set('ride_horse', 1)
    return i


def _pick_wander(p: _Pick) -> Inputs:
    """Wander -> Sandbox: exact, wander/sit/idle within a radius."""
    return _sandbox(p.loc, allow_wandering=1, allow_sitting=1,
                    allow_idle_markers=1, allow_conversation=1,
                    allow_eating=0, allow_sleeping=0)


def _pick_eat(p: _Pick) -> Inputs:
    """Eat: Find food -> Acquire -> Find chair, both object-type defaults."""
    i = Inputs(EAT)
    i.set('location', p.loc)
    i.set('food_target', build_object_type_target(OBJTYPE_FOOD))
    i.set('chair_target', build_object_type_target(OBJTYPE_CHAIR))
    return i


def _pick_sleep(p: _Pick) -> Inputs:
    """Sleep: an authored type-0 PTDT bed, else any Bed."""
    i = Inputs(SLEEP)
    i.set('location', p.loc)
    if _has_target(p.rec) and get_int(p.rec, 'PTDT.Type') == 0:
        i.set('bed_target', p.tgt)
    else:
        i.set('bed_target', build_object_type_target(OBJTYPE_BED))
    return i


def _follow_destination_reachable(p: _Pick) -> bool:
    """Whether this Follow's PLDT destination can be walked to from here."""
    dest = get_formid(p.rec, 'PLDT.Location')
    return bool(_has_location(p.rec) and dest
                and p.ctx.location_reachable(p.pack_fid, dest))


def _pick_follow(p: _Pick) -> Inputs:
    """Follow / Accompany, rerouted to Escort when it must ARRIVE.

    Skyrim's Follow has no location slot, so a TES4 Follow carrying a PLDT
    destination becomes an Escort -- but only when that destination is
    reachable, since Escort makes it the goal and a cross-cell goal has no
    navmesh route.
    """
    if _follow_destination_reachable(p):
        i = Inputs(ESCORT)
        i.set('target', p.tgt)
        i.set('location', p.loc)
        if p.use_horse:
            i.set('ride_horse', 1)
        return i
    i = Inputs(FOLLOW)
    i.set('target', p.tgt)
    i.set('accompany', 1 if get_int(p.rec, 'PKDT.Type', -1) == T4_ACCOMPANY
          else 0)
    if p.use_horse:
        i.set('ride_horse', 1)
    return i


def _pick_escort(p: _Pick) -> Inputs:
    """Escort: exact."""
    i = Inputs(ESCORT)
    i.set('target', p.tgt)
    i.set('location', p.loc)
    if p.use_horse:
        i.set('ride_horse', 1)
    return i


def _pick_flee(p: _Pick) -> Inputs:
    """Flee: exact."""
    i = Inputs(FLEE_TO)
    i.set('location', p.loc)
    return i


def _force_greet(p: _Pick, radius: float) -> Inputs:
    """A ForceGreet on the player, anchored on the player at `radius`.

    The forcegreet distance MUST be a type-0 location on ref 0x14: a
    type-2/type-3 location is not relative to the player, so the actor walks
    to a world spot instead of talking.
    """
    i = Inputs(FORCE_GREET)
    i.set('target', _target(0, PLAYER_FID, 0))
    i.set('topic', p.greet_topic or 0)
    if radius > 0:
        i.set('forcegreet_distance', _location(0, PLAYER_FID, int(radius)))
    return i


def _pldt_radius_f(rec: dict) -> float:
    """PLDT.Radius as a float, 0.0 when absent."""
    return float(get_int(rec, 'PLDT.Radius', 0) or 0)


def _pick_ambush(p: _Pick) -> Inputs:
    """Ambush: ForceGreet the player, approach an actor, else hold position.

    TES4 Ambush means "wait for the target to come near, then act on it" --
    Oblivion uses it for scripted approaches as well as literal ambushes.
    An actor is approached to PTDT.Count, the authored talking distance;
    PLDT.Radius is only the watch area around the location.
    """
    if not _has_target(p.rec):
        i = Inputs(HOLD_POSITION)
        i.set('location', p.loc)
        return i
    radius = _pldt_radius_f(p.rec)
    if _targets_player(p.rec):
        return _force_greet(p, radius)
    reach = float(get_int(p.rec, 'PTDT.Count', 0) or 0) or radius
    i = Inputs(FOLLOW)
    i.set('target', p.tgt)
    if reach > 0:
        i.set('min_radius', max(64.0, reach * 0.5))
        i.set('max_radius', reach)
    return i


def _pick_cast(p: _Pick) -> Inputs:
    """CastMagic -> UseMagic."""
    i = Inputs(USE_MAGIC)
    i.set('location', p.loc)
    if _has_target(p.rec):
        i.set('target', p.tgt)
    return i


def _use_item_at_ref(p: _Pick) -> Inputs:
    """UseItemAt on a specific reference: operate it, sit on it, or None.

    None means the ref is a carriable item, which can be neither sat on nor
    activated without picking it up.
    """
    if _operate_target(p.rec, p.ctx):
        i = Inputs(ACTIVATE)
        i.set('target', p.tgt)
        return i
    sig = p.ctx.base_sig_of(get_formid(p.rec, 'PTDT.Target'))
    if not sig or sig in FURNITURE_SIGS:
        i = Inputs(SIT_TARGET)
        i.set('target', p.tgt)
        return i
    return None


def _use_item_at_is_furniture(p: _Pick) -> bool:
    """Whether an object-TYPE UseItemAt target names furniture."""
    return bool(_has_target(p.rec) and object_criteria_kind(
        get_int(p.rec, 'PTDT.Type', -1), get_int(p.rec, 'PTDT.Target', 0),
        p.ctx.sig_of_base(get_formid(p.rec, 'PTDT.Target'))) == 'furniture')


def _pick_use_item(p: _Pick) -> Inputs:
    """UseItemAt: sit at furniture, operate an activator, else sandbox."""
    if (get_int(p.rec, 'PTDT.Type', -1) == 0
            and get_formid(p.rec, 'PTDT.Target')):
        chosen = _use_item_at_ref(p)
        if chosen is not None:
            return chosen
    if _use_item_at_is_furniture(p):
        i = Inputs(SIT)
        i.set('location', p.loc)
        i.set('chair_target', p.tgt)
        return i
    return _sandbox(p.loc, allow_sitting=1, allow_special_furniture=1,
                    allow_idle_markers=1, allow_wandering=0)


def _find_resolved_target(p: _Pick) -> tuple:
    """The Find target as (type, formid), collapsing a sole-placement base.

    A type-1 Object ID that the plugin places exactly once IS that reference.
    """
    target = get_formid(p.rec, 'PTDT.Target')
    t_type = get_int(p.rec, 'PTDT.Type', -1)
    if t_type == 1 and target and p.ctx.is_actor_base(target):
        ref = p.ctx.sole_placement(target)
        if ref:
            return 0, ref
    return t_type, target


def _find_approach_radius(p: _Pick, t_type: int) -> int:
    """PTDT.Count as an approach distance -- 0 for an Object-ID target."""
    if t_type == 1:
        return 0
    return max(0, min(int(get_int(p.rec, 'PTDT.Count', 0) or 0), 4096))


def _find_seek_ref(p: _Pick, target: int, t_type: int) -> Inputs:
    """Travel to where a placed reference is -- the seek half of Find."""
    radius = _find_approach_radius(p, get_int(p.rec, 'PTDT.Type', -1))
    alias = p.ctx.alias_for(p.pack_fid, target)
    i = Inputs(TRAVEL)
    i.set('location', build_alias_location(alias, radius)
          if alias is not None else build_location(0, target, radius))
    if p.use_horse:
        i.set('ride_horse', 1)
    return i


def _find_by_ref_sig(p: _Pick, target: int, ref_sig: str) -> Inputs:
    """Route a Find at a specific reference by what that reference IS."""
    if ref_sig in FURNITURE_SIGS:
        i = Inputs(SIT_TARGET)
        i.set('target', p.tgt)
        return i
    if ref_sig in ITEM_SIGS:
        i = Inputs(ACQUIRE)
        i.set('location', _authored_or_search_ground(
            p.rec, p.ctx, p.pack_fid, 0, p.loc, ref=target))
        i.set('target', p.tgt)
        i.set('count', max(1, get_int(p.rec, 'PTDT.Count', 0) or 0))
        return i
    if ref_sig and ref_sig not in OPERABLE_SIGS:
        return _find_seek_ref(p, target, 0)
    return None


def _pick_find(p: _Pick) -> Inputs:
    """Find: force-greet, operate, sit, acquire, seek, or sandbox.

    One TES4 type covering five idioms; the target decides which.
    """
    if _targets_player(p.rec):
        return _force_greet(p, _pldt_radius_f(p.rec))
    if _operate_target(p.rec, p.ctx):
        i = Inputs(ACTIVATE)
        i.set('target', p.tgt)
        return i
    t_type, target = _find_resolved_target(p)
    ref_sig = p.ctx.base_sig_of(target) if t_type == 0 and target else ''
    if ref_sig:
        chosen = _find_by_ref_sig(p, target, ref_sig)
        if chosen is not None:
            return chosen
    if t_type == 1 and target:
        return _find_object_criteria(
            p.rec, p.ctx, p.pack_fid, p.loc, p.tgt,
            object_criteria_kind(1, target, p.ctx.sig_of_base(target)),
            base=target)
    if t_type == 2:
        return _find_object_criteria(
            p.rec, p.ctx, p.pack_fid, p.loc, p.tgt,
            object_criteria_kind(2, get_int(p.rec, 'PTDT.Target', 0)))
    return _sandbox(p.loc, allow_wandering=1, allow_sitting=1,
                    allow_idle_markers=1)


#: TES4 PKDT.Type -> the handler that builds that template's inputs.
_PICK_BY_TYPE = {
    T4_TRAVEL: _pick_travel,
    T4_WANDER: _pick_wander,
    T4_EAT: _pick_eat,
    T4_SLEEP: _pick_sleep,
    T4_FOLLOW: _pick_follow,
    T4_ACCOMPANY: _pick_follow,
    T4_ESCORT: _pick_escort,
    T4_FLEE: _pick_flee,
    T4_AMBUSH: _pick_ambush,
    T4_CASTMAGIC: _pick_cast,
    T4_USEITEMAT: _pick_use_item,
    T4_FIND: _pick_find,
}


def _choose(rec: dict, ctx: PackContext, pack_fid: int) -> Inputs:
    """Pick the Skyrim template for a TES4 package and fill its inputs.

    Every branch preserves the TES4 location (incl. its type and radius) and
    target; only the procedure is re-expressed. An unknown type sandboxes at
    the location, which beats an inert actor.
    """
    handler = _PICK_BY_TYPE.get(get_int(rec, 'PKDT.Type', -1))
    p = _Pick(
        rec=rec, ctx=ctx, pack_fid=pack_fid,
        loc=resolve_location(rec, ctx, pack_fid),
        tgt=resolve_target(rec, ctx, pack_fid),
        radius=get_int(rec, 'PLDT.Radius', 0),
        use_horse=1 if (get_int(rec, 'PKDT.Flags', 0) & T4_USE_HORSE) else 0,
        greet_topic=getattr(ctx, 'greeting_topic', 0))
    if handler is None:
        return _sandbox(p.loc)
    return handler(p)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def convert_PACK(rec: dict, ctx: PackContext = None) -> bytes:
    """TES4 PACK -> TES5 PACK (a Type-18 template instance).

    TES5 subrecord order:
        EDID PKDT PSDT CTDA* QNAM PKCU
        <Package Data: ANAM/CNAM/PLDT/PTDA ...  UNAM* XNAM>
        POBA INAM PDTO   POEA INAM PDTO   POCA INAM PDTO
    """
    ctx = ctx or PackContext()
    pack_fid = get_formid(rec, 'FormID')

    subs = b''
    edid = get_str(rec, 'EditorID')
    if edid:
        subs += pack_string_subrecord('EDID', edid)

    ptype = get_int(rec, 'PKDT.Type', -1)
    # An Ambush aimed at the PLAYER is Oblivion's scripted-approach idiom, not
    # a hostile ambush: the actor walks over so dialogue can fire.  Real
    # ambushes wait on a location (no PTDT) or target another actor.
    is_forcegreet = (ptype in (T4_AMBUSH, T4_FIND)
                     and _targets_player(rec))
    hostile = not (is_forcegreet or _approaches_ref(rec, ptype))
    flags, speed = convert_flags(get_int(rec, 'PKDT.Flags'), ptype, hostile,
                                 quest_gated=ctx.quest_of(pack_fid) is not None)
    # A scripted one-shot (force-greet, or "go operate that switch") must run
    # at vanilla's pace and with vanilla's interrupt authorisation, or the actor
    # dawdles / can never break off to do the thing.  Both were measured from
    # real instances: MS05InductionForcegreet and CWEscapeCitySceneActivateDoor.
    is_activate = _operate_target(rec, ctx)
    if is_forcegreet:
        subs += pack_subrecord('PKDT', build_pkdt(_forcegreet_flags(rec, flags),
                                                  SPEED_RUN, interrupt=0xFEFF))
    elif is_activate:
        # Keep the TES4 flags (Must Complete / Once Per Day are real), but take
        # vanilla's speed and interrupts.
        subs += pack_subrecord('PKDT', build_pkdt(flags, SPEED_RUN,
                                                  interrupt=0xFFFF))
    else:
        subs += pack_subrecord('PKDT', build_pkdt(flags, speed))
    subs += pack_subrecord('PSDT', build_psdt(rec))

    # Conditions carry the activation logic and ARE the package's gate.  A
    # GetScriptVariable condition becomes GetVMScriptVariable + a CIS2 naming
    # the Papyrus property — see dialog_conditions; the legacy function is dead
    # in Skyrim, so without this the package could never fire.
    subs += _source_conditions(rec, ctx)

    owner = ctx.quest_of(pack_fid)
    if owner:
        subs += pack_formid_subrecord('QNAM', owner)

    inputs = _choose(rec, ctx, pack_fid)
    t = inputs.t

    # PKCU: DataInputCount u32, PackageTemplate formid, VersionCounter u32.
    # The template lives in Skyrim.esm (master index 0) so it is written
    # unremapped.
    subs += pack_subrecord('PKCU', struct.pack('<III', len(t.inputs),
                                               t.formid, t.version))
    subs += inputs.emit()

    # All three markers are mandatory (943/944 vanilla packages carry them).
    for marker in (b'POBA', b'POEA', b'POCA'):
        subs += pack_subrecord(marker.decode(), b'')
        subs += pack_formid_subrecord('INAM', 0)
        subs += pack_subrecord('PDTO', struct.pack('<II', 0, 0))

    if inputs.t is FORCE_GREET:
        # The greeting topic does not exist yet (Phase 5); remember which quest
        # this package belongs to so patch_forcegreet_topics can bind it.
        # Only 120 of 7,209 packages are quest-OWNED, so fall back to the quest
        # the package's own conditions test (GetStage/GetQuestVariable) — that
        # is the quest whose greeting the force-greet is part of.
        _FORCEGREET_PENDING[pack_fid] = owner or _condition_quest(rec)

    return pack_record('PACK', pack_fid, get_int(rec, 'RecordFlags'), subs)


# pack fid -> owning quest fid, for packages whose ForceGreet Topic input still
# holds the 0 placeholder. Drained by patch_forcegreet_topics.
_FORCEGREET_PENDING: dict = {}


# --- Hunt = Find at an actor BASE with several placements ------------------
#
# Oblivion's Find(Object ID = FGC06Goblin, count 10) walks the actor to the
# NEAREST living goblin, again and again.  Skyrim has no procedure that seeks
# "any of this base": a Sandbox only wanders locally (measured live 2026-08-18
# on FGC06 — all three fighters were RUNNING their hunt package and stayed
# within ~300 units of spawn while the goblins sat 1,400 units away and 350
# below), and a PLDT type-4 "Object ID" location leaves the actor standing
# (patched into the live package: no movement at all).  What Skyrim does have
# is Follow-a-reference, so the one TES4 package becomes a CHAIN of Follow
# packages, one per placed target, nearest first, each gated on that target
# being alive, enabled and in the hunter's cell.  The engine walks the chain
# by itself: kill goblin #1 and its GetDead gate fails, so #2 wins.  The
# source package (the roam Sandbox) stays as the tail.

MAX_HUNT_CHAIN = 24


def hunt_chain_targets(rec: dict, ctx: PackContext, pack_fid: int) -> list:
    """The placed refs a Find-at-actor-BASE package seeks, nearest first from
    the hunter's own placement; [] when this package is not such a hunt (or
    the base has fewer than two placements — one resolves to the ref, see
    _choose)."""
    if get_int(rec, 'PKDT.Type', -1) != T4_FIND:
        return []
    if get_int(rec, 'PTDT.Type', -1) != 1 or _targets_player(rec):
        return []
    base = get_formid(rec, 'PTDT.Target')
    if not base or not ctx.is_actor_base(base):
        return []
    placements = ctx.base_placements.get(base & 0x00FFFFFF) or ()
    if len(placements) < 2:
        return []
    refs = [r for r, _c in placements]
    origin = ctx.runner_origin(pack_fid)
    if origin:
        def _d2(ref):
            pos = ctx.actor_pos.get(ref & 0x00FFFFFF)
            if pos is None:
                return float('inf')
            return sum((a - b) ** 2 for a, b in zip(pos, origin))
        refs.sort(key=_d2)
    if len(refs) > MAX_HUNT_CHAIN:
        print(f"  NOTE: hunt {get_str(rec, 'EditorID')} seeks "
              f"{len(refs)} placements; chaining the nearest "
              f"{MAX_HUNT_CHAIN}")
        refs = refs[:MAX_HUNT_CHAIN]
    return refs


def _run_on_ref_ctda(func: int, ref: int, comp: float,
                     operator: int = 0x00) -> bytes:
    """A TES5 CTDA `func <op> comp` evaluated ON `ref` (RunOn = Reference)."""
    comp_raw = struct.unpack('<I', struct.pack('<f', comp))[0]
    return struct.pack('<B3xIHHIIII I', operator, comp_raw, func, 0,
                       0, 0, 2, ref, 0xFFFFFFFF)


def _subject_ctda(func: int, param1: int, comp: float,
                  operator: int = 0x00) -> bytes:
    comp_raw = struct.unpack('<I', struct.pack('<f', comp))[0]
    return struct.pack('<B3xIHHIIII I', operator, comp_raw, func, 0,
                       param1, 0, 0, 0, 0xFFFFFFFF)


CTDA_GET_IN_SAME_CELL = 32   # GetInSameCell(ref)   (same index in TES4/TES5)
CTDA_GET_DISABLED = 35       # GetDisabled
CTDA_GET_DEAD = 46           # GetDead


def _source_conditions(rec: dict, ctx: PackContext) -> bytes:
    out = b''
    for ctda, cis2 in convert_ctda_list_with_strings(rec, ctx.script_vars):
        out += pack_subrecord('CTDA', ctda)
        if cis2:
            out += pack_string_subrecord('CIS2', cis2)
    return out


def _seek_record(rec: dict, ctx: PackContext, src_fid: int, seek_fid: int,
                 ref: int, k: int) -> bytes:
    """One link of a hunt chain: Follow `ref` while it is alive, enabled and
    in the hunter's cell, under the source package's own gates."""
    edid = get_str(rec, 'EditorID')
    subs = b''
    if edid:
        subs += pack_string_subrecord('EDID', f'{edid}Seek{k:02d}')
    owner = ctx.quest_of(src_fid)
    flags, speed = convert_flags(get_int(rec, 'PKDT.Flags'), T4_FOLLOW,
                                 quest_gated=owner is not None)
    subs += pack_subrecord('PKDT', build_pkdt(flags, speed))
    subs += pack_subrecord('PSDT', build_psdt(rec))
    subs += _source_conditions(rec, ctx)
    # AND-gates on the target: `GetInSameCell` is evaluated on the HUNTER
    # (its param is the target), the other two ON the target reference.
    subs += pack_subrecord('CTDA', _subject_ctda(CTDA_GET_IN_SAME_CELL, ref,
                                                 1.0))
    subs += pack_subrecord('CTDA', _run_on_ref_ctda(CTDA_GET_DISABLED, ref,
                                                    0.0))
    subs += pack_subrecord('CTDA', _run_on_ref_ctda(CTDA_GET_DEAD, ref, 0.0))
    if owner:
        subs += pack_formid_subrecord('QNAM', owner)
    inputs = Inputs(FOLLOW)
    alias = ctx.alias_for(src_fid, ref)
    inputs.set('target', build_alias_target(alias) if alias is not None
               else build_target(0, ref))
    if get_int(rec, 'PKDT.Flags', 0) & T4_USE_HORSE:
        inputs.set('ride_horse', 1)
    t = inputs.t
    subs += pack_subrecord('PKCU', struct.pack('<III', len(t.inputs),
                                               t.formid, t.version))
    subs += inputs.emit()
    for marker in (b'POBA', b'POEA', b'POCA'):
        subs += pack_subrecord(marker.decode(), b'')
        subs += pack_formid_subrecord('INAM', 0)
        subs += pack_subrecord('PDTO', struct.pack('<II', 0, 0))
    return pack_record('PACK', seek_fid, get_int(rec, 'RecordFlags'), subs)


def convert_PACK_records(rec: dict, ctx: PackContext = None) -> list:
    """Every TES5 PACK record one TES4 PACK becomes: the hunt chain's seek
    links (when import_main planned one, see PackContext.hunt_chains) followed
    by the package itself."""
    ctx = ctx or PackContext()
    src_fid = get_formid(rec, 'FormID')
    out = []
    for k, (seek_fid, ref) in enumerate(ctx.hunt_chains.get(src_fid, ()), 1):
        out.append(_seek_record(rec, ctx, src_fid, seek_fid, ref, k))
    out.append(convert_PACK(rec, ctx))
    return out

# TES4 condition functions whose param1 is a QUEST FormID.
_QUEST_PARAM_FUNCS = frozenset({
    58,    # GetStage
    59,    # GetStageDone
    79,    # GetQuestVariable
    62,    # GetQuestRunning
})


def _forcegreet_flags(rec: dict, flags: int) -> int:
    """Force-greet PKDT flags: TES4's, Once Per Day restored (contracts doc, PKDT)."""
    if get_int(rec, 'PKDT.Flags') & T4_ONCE_PER_DAY:
        flags |= T5_ONCE_PER_DAY
    return flags


def _condition_quest(rec: dict) -> int:
    """The quest this package's conditions gate on (converted fid), or 0.

    A force-greet is part of a quest's dialogue even when the PACKAGE is not
    quest-owned, and `GetStage <quest> == N` is how Oblivion scheduled it.
    """
    i = 0
    while True:
        raw = rec.get(f'Condition[{i}].Raw')
        if raw is None:
            return 0
        i += 1
        try:
            d = bytes.fromhex(raw)
        except ValueError:
            continue
        if len(d) < 20:
            continue
        if struct.unpack_from('<H', d, 8)[0] in _QUEST_PARAM_FUNCS:
            from ..base.text_reader import remap_formid
            return remap_formid(struct.unpack_from('<I', d, 12)[0])

# The Topic input is the FIRST data input of a ForceGreet instance, so its PDTO
# is the first PDTO in the record. The POBA/POEA/POCA blocks that follow also
# carry PDTOs, hence "first" rather than "any".
_FORCEGREET_TOPIC_SLOT = 0


def patch_forcegreet_topics(writer) -> int:
    """Bind each ForceGreet package's Topic input to its quest's GREETING.

    Skyrim opens a forced conversation by naming a DIAL in the package's Topic
    data input (PDTO). Those topics are built per quest in Phase 5, after PACK
    is written, so conversion leaves a 0 placeholder and this fills it in.
    """
    from ..dialogue.converter import GREET_TOPIC_BY_QUEST
    if not _FORCEGREET_PENDING or not GREET_TOPIC_BY_QUEST:
        return 0
    records = writer._top_groups.get('PACK') or []
    patched = 0
    for i, blob in enumerate(records):
        if len(blob) < 24:
            continue
        fid = struct.unpack_from('<I', blob, 12)[0]
        quest = _FORCEGREET_PENDING.get(fid)
        if quest is None:
            continue
        topic = GREET_TOPIC_BY_QUEST.get(quest)
        if not topic:
            continue
        # First PDTO in the data-input block: 6-byte header then (type, fid).
        at = blob.find(b'PDTO', 24)
        if at < 0:
            continue
        off = at + 6
        if struct.unpack_from('<I', blob, off + 4)[0] != 0:
            continue          # already bound
        records[i] = (blob[:off + 4] + struct.pack('<I', topic)
                      + blob[off + 8:])
        patched += 1
    return patched
