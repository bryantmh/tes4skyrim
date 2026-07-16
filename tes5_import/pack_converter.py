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

See docs/package_conversion_plan.md for the fidelity analysis (which TES4 types
map exactly, which degrade, and why).
"""

import struct

from .pack_templates import (
    ESCORT,
    EAT,
    FLEE_TO,
    FOLLOW,
    HOLD_POSITION,
    PKDT_TYPE_PACKAGE,
    SANDBOX,
    SIT_TARGET,
    SLEEP,
    TRAVEL,
    T_BOOL,
    T_FLOAT,
    T_INT,
    T_LOCATION,
    T_OBJECTLIST,
    T_SINGLEREF,
    T_TARGETSEL,
    USE_MAGIC,
    Template,
)
from .dialog_conditions import convert_ctda_list_with_strings
from .text_reader import get_formid, get_int, get_str
from .writer import (
    pack_formid_subrecord,
    pack_record,
    pack_string_subrecord,
    pack_subrecord,
)

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

PLAYER_FID = 0x00000014

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

# Default interrupt flags — vanilla instances use 0xFFFF (all interrupts
# allowed: hellos, conversations, combat observation).  Oblivion packages have
# no equivalent field, and allowing interrupts is what makes NPCs feel alive.
DEFAULT_INTERRUPT = 0xFFFF

# Furniture object types (TES5 wbObjectTypeEnum) used to decide whether a TES4
# UseItemAt target is "sit-like".
FURNITURE_SIGS = frozenset({'FURN', 'CHAI', 'BED '})


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
                out += pack_subrecord('PLDT', v if isinstance(v, bytes)
                                      else _null_location())
            elif atype in (T_SINGLEREF, T_TARGETSEL):
                out += pack_subrecord('PTDA', v if isinstance(v, bytes)
                                      else _null_target())
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


# wbObjectTypeEnum values used by vanilla PTDA type-2 ("Object Type") defaults.
OBJTYPE_FOOD = 15
OBJTYPE_CHAIR = 27
OBJTYPE_BED = 26


def _null_target() -> bytes:
    return struct.pack('<iIi', 0, 0, 0)


def build_object_type_target(obj_type: int) -> bytes:
    """PTDA type 2 = Object Type — 'any object of this kind', not a specific
    FormID.  This is what every vanilla Eat/Sleep instance uses for a
    TargetSelector it doesn't pin to a specific ref; a type-0 "Specific
    Reference" with FormID 0 (our old default) is what triggers CKPE's
    "Unable to find Package Target Reference (00000000)" warning at runtime."""
    return struct.pack('<iIi', 2, obj_type, 0)


def build_location(loc_type: int, value: int, radius: int) -> bytes:
    """TES4 PLDT -> TES5 PLDT.  Types 0..5 are the same enum in both games,
    and vanilla Skyrim uses every one we need (type 1 'in cell' appears 448x),
    so this is a copy, not an approximation."""
    if loc_type < 0 or loc_type > 5:
        return _null_location()
    return struct.pack('<iIi', loc_type, value & 0xFFFFFFFF, radius)


def build_target(t_type: int, target: int, count: int) -> bytes:
    """TES4 PTDT -> TES5 PTDA.  Types 0 (specific ref), 1 (object id) and
    2 (object type) map 1:1."""
    if t_type < 0 or t_type > 2:
        return _null_target()
    return struct.pack('<iIi', t_type, target & 0xFFFFFFFF, count)


def build_alias_target(alias_index: int) -> bytes:
    """PTDA type 4 = Ref Alias — how a quest package names an actor."""
    return struct.pack('<iii', 4, alias_index, 0)


def build_alias_location(alias_index: int, radius: int = 0) -> bytes:
    """PLDT type 9 = reference-in-alias."""
    return struct.pack('<iii', 9, alias_index, radius)


# ---------------------------------------------------------------------------
# PKDT / PSDT
# ---------------------------------------------------------------------------

def convert_flags(t4_flags: int, pack_type: int) -> tuple:
    """TES4 PKDT flags -> (TES5 flags, preferred speed).

    Returns the speed separately because TES4's "always run" is a *flag* while
    TES5's speed is a *field* (plus the 0x2000 'use preferred speed' opt-in).
    """
    flags = 0
    for t4_bit, t5_bit in _FLAG_MAP:
        if t4_flags & t4_bit:
            flags |= t5_bit

    speed = SPEED_WALK
    if t4_flags & T4_ALWAYS_RUN:
        speed = SPEED_RUN
        flags |= T5_PREFERRED_SPEED

    if t4_flags & T4_DEFENSIVE_COMBAT:
        flags |= T5_IGNORE_COMBAT

    # TES4 "lock doors at start/end" has no TES5 flag — the Sleep template owns
    # door-locking as a procedure input instead.  Dropped deliberately.
    # TES4 armor-unequipped / allow-falls / no-idle-anims / use-horse: no TES5
    # counterpart (use-horse becomes a template Ride Horse? input).  Dropped.

    if pack_type == T4_AMBUSH:
        # Wait hidden, weapon out, don't call for help.
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

    # TES5 splits TES4's hour-only time into hour + minute.
    minute = 0
    if hour < -1 or hour > 23:
        hour = -1
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

    def __init__(self, plan=None, script_vars=None):
        self.plan = plan
        self.script_vars = script_vars or {}

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
    package outranks the actor's standing schedule.
    """
    t_type = get_int(rec, 'PTDT.Type', -1)
    if t_type < 0:
        return _null_target()
    target = get_formid(rec, 'PTDT.Target')
    count = get_int(rec, 'PTDT.Count', 0)

    if t_type == 0 and target:
        alias = ctx.alias_for(pack_fid, target)
        if alias is not None:
            return build_alias_target(alias)
    return build_target(t_type, target, count)


def resolve_location(rec: dict, ctx: PackContext, pack_fid: int) -> bytes:
    loc_type = get_int(rec, 'PLDT.Type', -1)
    if loc_type < 0:
        return _null_location()
    value = get_formid(rec, 'PLDT.Location')
    radius = get_int(rec, 'PLDT.Radius', 0)

    if loc_type == 0 and value:
        alias = ctx.alias_for(pack_fid, value)
        if alias is not None:
            return build_alias_location(alias, radius)
    return build_location(loc_type, value, radius)


def _has_location(rec: dict) -> bool:
    return get_int(rec, 'PLDT.Type', -1) >= 0


def _has_target(rec: dict) -> bool:
    return get_int(rec, 'PTDT.Type', -1) >= 0


# ---------------------------------------------------------------------------
# TES4 type -> template + inputs
# ---------------------------------------------------------------------------

def _choose(rec: dict, ctx: PackContext, pack_fid: int) -> Inputs:
    """Pick the Skyrim template for a TES4 package and fill its inputs.

    Every branch preserves the TES4 location (incl. its type and radius) and
    target; only the procedure is re-expressed.
    """
    ptype = get_int(rec, 'PKDT.Type', -1)
    loc = resolve_location(rec, ctx, pack_fid)
    tgt = resolve_target(rec, ctx, pack_fid)
    radius = get_int(rec, 'PLDT.Radius', 0)

    # --- Travel: exact ---
    if ptype == T4_TRAVEL:
        i = Inputs(TRAVEL)
        i.set('location', loc)
        return i

    # --- Wander -> Sandbox: exact.  TES4 Wander = wander/sit/idle in a radius,
    # which is precisely what the Sandbox procedure does. ---
    if ptype == T4_WANDER:
        i = Inputs(SANDBOX)
        i.set('location', loc)
        i.set('allow_wandering', 1)
        i.set('allow_sitting', 1)
        i.set('allow_idle_markers', 1)
        i.set('allow_conversation', 1)
        i.set('allow_eating', 0)
        i.set('allow_sleeping', 0)
        return i

    # --- Eat: dedicated template (Find -> Acquire food -> Find chair) ---
    # TES4 has no per-package food/chair ref, so these TargetSelector slots
    # always take the vanilla "any object of this type" default (100% of
    # 395 vanilla Eat instances use exactly these two object types).
    if ptype == T4_EAT:
        i = Inputs(EAT)
        i.set('location', loc)
        i.set('food_target', build_object_type_target(OBJTYPE_FOOD))
        i.set('chair_target', build_object_type_target(OBJTYPE_CHAIR))
        return i

    # --- Sleep: dedicated template (Find bed -> LockDoors -> Sleep) ---
    if ptype == T4_SLEEP:
        i = Inputs(SLEEP)
        i.set('location', loc)
        if _has_target(rec) and get_int(rec, 'PTDT.Type') == 0:
            i.set('bed_target', tgt)   # sleep in *this* bed
        else:
            # 94% of vanilla Sleep instances use "any Bed" here too.
            i.set('bed_target', build_object_type_target(OBJTYPE_BED))
        return i

    # --- Follow / Accompany: exact.  Skyrim models Accompany as a Follow
    # input, so type 7 is not an approximation. ---
    if ptype in (T4_FOLLOW, T4_ACCOMPANY):
        i = Inputs(FOLLOW)
        i.set('target', tgt)
        i.set('accompany', 1 if ptype == T4_ACCOMPANY else 0)
        return i

    # --- Escort: exact. ---
    if ptype == T4_ESCORT:
        i = Inputs(ESCORT)
        i.set('target', tgt)
        i.set('location', loc)
        return i

    # --- Flee ---
    if ptype == T4_FLEE:
        i = Inputs(FLEE_TO)
        i.set('location', loc)
        return i

    # --- Ambush -> hold position, weapon drawn (flags set in convert_flags) ---
    if ptype == T4_AMBUSH:
        i = Inputs(HOLD_POSITION)
        i.set('location', loc)
        return i

    # --- CastMagic ---
    if ptype == T4_CASTMAGIC:
        i = Inputs(USE_MAGIC)
        i.set('location', loc)
        if _has_target(rec):
            i.set('target', tgt)
        return i

    # --- UseItemAt: sit at a specific furniture ref, else travel + sandbox
    # with furniture allowed.  TES4's object-*type* targets ("use any chair")
    # have no direct TES5 input here; they degrade to sandbox. ---
    if ptype == T4_USEITEMAT:
        if get_int(rec, 'PTDT.Type', -1) == 0 and get_formid(rec, 'PTDT.Target'):
            i = Inputs(SIT_TARGET)
            i.set('target', tgt)
            return i
        i = Inputs(SANDBOX)
        i.set('location', loc)
        i.set('allow_sitting', 1)
        i.set('allow_special_furniture', 1)
        i.set('allow_idle_markers', 1)
        i.set('allow_wandering', 0)
        return i

    # --- Find: travel to the location, then sandbox there.  The "locate this
    # object" tail has no TES5 standalone equivalent and is dropped; the travel
    # and the destination — what a player actually observes — are exact. ---
    if ptype == T4_FIND:
        i = Inputs(SANDBOX)
        i.set('location', loc)
        i.set('allow_wandering', 1)
        i.set('allow_sitting', 1)
        i.set('allow_idle_markers', 1)
        return i

    # Unknown type: a sandbox at the location beats an inert actor.
    i = Inputs(SANDBOX)
    i.set('location', loc)
    return i


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
    flags, speed = convert_flags(get_int(rec, 'PKDT.Flags'), ptype)
    subs += pack_subrecord('PKDT', build_pkdt(flags, speed))
    subs += pack_subrecord('PSDT', build_psdt(rec))

    # Conditions carry the activation logic and ARE the package's gate.  A
    # GetScriptVariable condition becomes GetVMScriptVariable + a CIS2 naming
    # the Papyrus property — see dialog_conditions; the legacy function is dead
    # in Skyrim, so without this the package could never fire.
    for ctda, cis2 in convert_ctda_list_with_strings(rec, ctx.script_vars):
        subs += pack_subrecord('CTDA', ctda)
        if cis2:
            subs += pack_string_subrecord('CIS2', cis2)

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

    return pack_record('PACK', pack_fid, get_int(rec, 'RecordFlags'), subs)
