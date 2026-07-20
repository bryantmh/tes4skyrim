"""Vanilla Skyrim PACK template roots and their data-input signatures.

A TES5 package comes in two kinds (verified by census of all 5,961 PACK records
in references/Skyrim.esm/PACK.txt):

  * template ROOT     PKDT.Type = 19, owns a Procedure Tree      (197 records)
  * package INSTANCE  PKDT.Type = 18, no tree; points PKCU.Template
                      at a root and supplies data inputs        (5,764 records)

96.7% of vanilla packages are instances.  The root owns the *behaviour*; the
instance owns the *customisation* (destination, target, schedule, conditions,
owner quest).  We therefore never author procedure trees — we emit Type-18
instances against stock Skyrim.esm roots (master index 0, so their FormIDs are
written unremapped) and fill in their inputs.

The data inputs are POSITIONAL.  An instance must emit exactly as many
ANAM(+CNAM/PLDT/PTDA) entries as the root declares, in the root's order, then
repeat the root's UNAM index list verbatim, then the root's XNAM value.  Getting
the order wrong feeds e.g. "max radius" into "min radius" silently.

Every table below was dumped from real Skyrim.esm records with
tools/pack_template_dump.py — do not hand-edit.  Regenerate with:

    python -m tools.pack_template_dump --list
    python -m tools.pack_template_dump Travel Sandbox Eat Sleep ...

`inputs` gives each slot's ANAM type string; `defaults` gives the value a vanilla
instance supplies when we have no TES4 data for that slot (taken from a real
instance of that template, not invented).  Slots we drive from TES4 data are
overwritten by the converter and their default is ignored.
"""

from dataclasses import dataclass, field

# ANAM type strings (the 4-byte ANAM payload is a zstring)
T_LOCATION = 'Location'          # payload subrecord: PLDT
T_SINGLEREF = 'SingleRef'        # payload subrecord: PTDA
T_TARGETSEL = 'TargetSelector'   # payload subrecord: PTDA
T_OBJECTLIST = 'ObjectList'      # payload subrecord: CNAM (u32 formid, 0 = none)
T_BOOL = 'Bool'                  # payload subrecord: CNAM (1 byte)
T_INT = 'Int'                    # payload subrecord: CNAM (u32)
T_FLOAT = 'Float'                # payload subrecord: CNAM (f32)

# PKDT.Type
PKDT_TYPE_PACKAGE = 18
PKDT_TYPE_TEMPLATE = 19


@dataclass(frozen=True)
class Template:
    """A vanilla Skyrim template root an instance can point at."""
    formid: int
    edid: str
    xnam: int              # marker value, copied verbatim from the root
    version: int           # PKCU version counter, copied from the root
    index_list: tuple      # UNAM indices, copied verbatim from the root
    inputs: tuple          # ordered ANAM type per slot
    defaults: dict = field(default_factory=dict)   # slot -> value
    # Named slots the converter drives from TES4 data.
    slots: dict = field(default_factory=dict)      # name -> slot index

    def slot(self, name: str) -> int:
        return self.slots[name]


# --- Travel (00016FAA) — 1,988 vanilla instances -------------------------
# procedures: Travel
TRAVEL = Template(
    formid=0x00016FAA, edid='Travel', xnam=5, version=3,
    index_list=(0, 2, 4),
    inputs=(T_LOCATION, T_BOOL, T_BOOL),
    defaults={1: 0, 2: 0},          # Ride Horse?, Prefer Preferred Path?
    slots={'location': 0, 'ride_horse': 1},
)

# --- Sandbox (0001C254) — 918 instances ----------------------------------
# procedures: Travel -> UnlockDoors -> Sandbox
# Slots 1..10 are the sandbox permission booleans, 11 is Energy.
SANDBOX = Template(
    formid=0x0001C254, edid='Sandbox', xnam=32, version=10,
    index_list=(0, 14, 1, 3, 4, 5, 6, 31, 7, 25, 27, 29),
    inputs=(T_LOCATION,) + (T_BOOL,) * 10 + (T_FLOAT,),
    # Defaults from vanilla WinterholdKraldarSandboxHome (00000E8C).
    defaults={
        1: 1,    # unlock on arrival
        2: 1,    # allow eating
        3: 0,    # allow sleeping
        4: 1,    # allow conversation
        5: 1,    # allow idle markers
        6: 1,    # allow sitting
        7: 1,    # allow special furniture
        8: 0,    # allow wandering
        9: 0,    # preferred path only
        10: 0,   # ride horse
        11: 50.0,  # energy
    },
    slots={
        'location': 0, 'unlock_on_arrival': 1, 'allow_eating': 2,
        'allow_sleeping': 3, 'allow_conversation': 4, 'allow_idle_markers': 5,
        'allow_sitting': 6, 'allow_special_furniture': 7, 'allow_wandering': 8,
        'preferred_path_only': 9, 'ride_horse': 10, 'energy': 11,
    },
)

# --- Eat (00019714) — 395 instances --------------------------------------
# procedures: Travel -> UnlockDoors -> Find -> Sandbox -> Acquire -> Find -> Sandbox
# NOT sandbox-with-a-flag: it has its own Acquire (go get food) + Find (chair).
EAT = Template(
    formid=0x00019714, edid='Eat', xnam=36, version=8,
    index_list=(0, 21, 1, 4, 5, 6, 10, 12, 16, 14, 23, 25, 26, 27, 28, 33, 29,
                30, 35, 8, 32),
    inputs=(T_LOCATION, T_BOOL, T_TARGETSEL, T_OBJECTLIST, T_TARGETSEL,
            T_OBJECTLIST, T_INT) + (T_BOOL,) * 11 + (T_FLOAT, T_BOOL, T_FLOAT),
    defaults={
        1: 1,        # unlock on arrival
        3: 0,        # food ObjectList (none -> engine default food criteria)
        5: 0,        # chair ObjectList
        6: 1,        # NumFoodItems
        7: 1, 8: 1, 9: 1, 10: 1, 11: 1, 12: 1, 13: 1, 14: 1, 15: 1, 16: 1,
        17: 1,
        18: 300.0,   # wait time
        19: 0,
        20: 50.0,    # energy
    },
    slots={'location': 0, 'food_target': 2, 'chair_target': 4},
)

# --- Sleep (00019717) — 313 instances ------------------------------------
# procedures: Travel -> LockDoors -> Find -> Sandbox -> Sleep
SLEEP = Template(
    formid=0x00019717, edid='Sleep', xnam=27, version=6,
    index_list=(0, 1, 15, 13, 11, 2, 8, 17, 18, 19, 20, 21, 25, 22, 26, 6, 24),
    inputs=(T_LOCATION, T_TARGETSEL, T_BOOL, T_BOOL, T_BOOL, T_OBJECTLIST)
           + (T_BOOL,) * 8 + (T_FLOAT, T_BOOL, T_FLOAT),
    # Defaults from vanilla WinterholdHousecarlSleepLonghouse23x7 (00000E8F).
    defaults={
        2: 0, 3: 1, 4: 0,      # warn before locking / lock doors / ...
        5: 0,                  # bed ObjectList (none)
        6: 0, 7: 0, 8: 1, 9: 1, 10: 1, 11: 1, 12: 1, 13: 1,
        14: 300.0,             # wait timer
        15: 0,
        16: 50.0,              # energy
    },
    slots={'location': 0, 'bed_target': 1},
)

# --- Sit (00019715) — 129 instances --------------------------------------
# procedures: Find -> Travel -> Wander -> Wait
SIT = Template(
    formid=0x00019715, edid='Sit', xnam=14, version=5,
    index_list=(0, 1, 2, 3, 4, 6, 8),
    inputs=(T_LOCATION, T_TARGETSEL, T_OBJECTLIST, T_FLOAT, T_BOOL, T_BOOL,
            T_BOOL),
    defaults={2: 0, 3: 300.0, 4: 0, 5: 0, 6: 0},
    slots={'location': 0, 'chair_target': 1, 'wait_time': 3},
)

# --- Follow (00019B2C) — 124 instances -----------------------------------
# procedures: Follow.  Skyrim models TES4 "Accompany" as Follow(Accompany?=1).
FOLLOW = Template(
    formid=0x00019B2C, edid='Follow', xnam=9, version=4,
    index_list=(0, 1, 2, 4, 6, 8),
    inputs=(T_SINGLEREF, T_FLOAT, T_FLOAT, T_BOOL, T_BOOL, T_BOOL),
    # ride_horse default 0 (root + 121/124 vanilla instances); a horseless
    # follower with ride_horse=1 never moves.  See ESCORT note.
    defaults={1: 128.0, 2: 256.0, 3: 0, 4: 0, 5: 0},
    slots={'target': 0, 'min_radius': 1, 'max_radius': 2, 'accompany': 3,
           'ride_horse': 4, 'need_los': 5},
)

# --- Escort (00023B73) — 44 instances ------------------------------------
# procedures: Escort.  Preferred over EscortPlayerWhenNear (which additionally
# waits/travels); TES4 Escort is a plain "walk the target to the destination".
ESCORT = Template(
    formid=0x00023B73, edid='Escort', xnam=18, version=8,
    index_list=(11, 2, 3, 4, 5, 6, 13, 15, 17),
    inputs=(T_SINGLEREF, T_INT, T_LOCATION, T_FLOAT, T_FLOAT, T_FLOAT,
            T_BOOL, T_BOOL, T_FLOAT),
    # Defaults follow the template ROOT and the dominant vanilla instance
    # values (41/44 escorts: ride horse 0, prefer preferred path 0).  The old
    # 1/1 values were copied from WERoad02 — a HORSEBACK world encounter — and
    # ride horse=1 on a horseless NPC freezes the escort: the package wins the
    # stack (its conditions pass) but the procedure never moves the actor
    # (Pinarus Inventius standing still in FGC01Rats).  Genuine TES4
    # Use-Horse packages set ride_horse via _choose().
    defaults={
        1: 1,        # number of followers
        3: 512.0,    # distance to wait for follower(s) (root value)
        4: 120.0,    # follower min distance
        5: 256.0,    # follower max distance
        6: 0,        # ride horse
        7: 0,        # prefer preferred path
        8: 500.0,    # run-if-behind distance
    },
    slots={'target': 0, 'location': 2, 'ride_horse': 6},
)

# --- HoldPosition (000503D0) — 116 instances -----------------------------
HOLD_POSITION = Template(
    formid=0x000503D0, edid='HoldPosition', xnam=8, version=5,
    index_list=(0,),
    inputs=(T_LOCATION,),
    defaults={},
    slots={'location': 0},
)

# --- SitTarget (000A9277) — 276 instances --------------------------------
# procedures: Wait.  Sits at a *specific* furniture ref.
SIT_TARGET = Template(
    formid=0x000A9277, edid='SitTarget', xnam=17, version=2,
    index_list=(16, 3, 4),
    inputs=(T_SINGLEREF, T_FLOAT, T_BOOL),
    defaults={1: 300.0, 2: 0},
    slots={'target': 0, 'wait_time': 1},
)

# --- UseIdleMarker (000283F0) — 60 instances -----------------------------
USE_IDLE_MARKER = Template(
    formid=0x000283F0, edid='UseIdleMarker', xnam=2, version=1,
    index_list=(1,),
    inputs=(T_SINGLEREF,),
    defaults={},
    slots={'target': 0},
)

# --- FleeTo (000C7039) — flee to a location ------------------------------
FLEE_TO = Template(
    formid=0x000C7039, edid='FleeTo', xnam=17, version=8,
    index_list=(3, 6, 1, 0, 4, 14, 15),
    inputs=(T_LOCATION, T_OBJECTLIST, T_FLOAT, T_BOOL, T_FLOAT, T_BOOL, T_BOOL),
    defaults={1: 0, 2: 1000.0, 3: 0, 4: 128.0, 5: 0, 6: 1},
    slots={'location': 0, 'flee_distance': 2},
)

# --- UseMagic (000504F5) — 46 instances ----------------------------------
USE_MAGIC = Template(
    formid=0x000504F5, edid='UseMagic', xnam=13, version=1,
    index_list=(2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12),
    inputs=(T_LOCATION, T_TARGETSEL, T_SINGLEREF, T_BOOL, T_FLOAT, T_FLOAT,
            T_FLOAT, T_FLOAT, T_INT, T_INT, T_BOOL),
    defaults={3: 0, 4: 0.0, 5: 0.0, 6: 0.0, 7: 0.0, 8: 0, 9: 0, 10: 0},
    slots={'location': 0, 'target': 1},
)

# --- Patrol (00017723) — 313 instances -----------------------------------
PATROL = Template(
    formid=0x00017723, edid='Patrol', xnam=9, version=3,
    index_list=(0, 1, 2, 4, 6, 8),
    inputs=(T_SINGLEREF, T_FLOAT, T_BOOL, T_BOOL, T_BOOL, T_BOOL),
    defaults={1: 0.0, 2: 1, 3: 1, 4: 0, 5: 0},
    slots={'target': 0, 'radius': 1},
)


ALL_TEMPLATES = (
    TRAVEL, SANDBOX, EAT, SLEEP, SIT, FOLLOW, ESCORT, HOLD_POSITION,
    SIT_TARGET, USE_IDLE_MARKER, FLEE_TO, USE_MAGIC, PATROL,
)
