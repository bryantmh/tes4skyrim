"""The generated graph's event and variable tables.

Split out of hkx_behavior.  Both tables are ORDERED and the order is the
contract: a graph's events and variables are referenced by INDEX everywhere
in the packfile, and `tools/live/graph_vars.py` reads a live actor's
hkbVariableValueSet back by this same declaration order.

The variable table is the engine-side graph interface — SSE's movement
controller, combat and AI bind these by name at graph init.  A graph with NO
variables leaves the movement hookup dead: the actor plays its start state
forever and never walks or reacts.

Three initial values are load-bearing.  `IsAttackReady` and `bEquipOK` init
to 1 because the combat controller reads them before it will ever send an
attackStart_* event, so a 0 default means the actor follows its target but
NEVER attacks.  Every magic variable inits to 0 verbatim from the vanilla
atronach: cast readiness is the ENGINE's to grant, not ours to fake.

The optional tables mirror how vanilla splits its own declarations —
atronach and chaurus declare the magic set, wolf declares none; draugr
declares the block set.
See: docs/commentary/asset_convert_creature.md#5-key-technical-facts-verified-from-references
"""

from asset_convert.game_paths import current_namespace
from asset_convert.havok.behavior_clips import (
    build_attack_events,
    cast_phase_defs,
)
from asset_convert.havok.behavior_getup import GETUP_EVENTS

#: Engine-set/read variables every creature declares: (name, type, init).
ENGINE_VARIABLES = [
    ('Speed', 'REAL', 0), ('Direction', 'REAL', 0), ('TurnDelta', 'REAL', 0),
    ('TurnDeltaDamped', 'REAL', 0), ('SpeedSampled', 'REAL', 0),
    ('staggerMagnitude', 'REAL', 0), ('staggerDirection', 'REAL', 0),
    ('iState', 'INT32', 0), ('iGetUpType', 'INT32', 0),
    ('iCharacterSelector', 'INT32', 0), ('iCombatStance', 'INT32', 0),
    ('iMovementSpeed', 'INT32', 0),
    ('IsAttacking', 'BOOL', 0), ('IsAttackReady', 'BOOL', 1),
    ('bEquipOK', 'BOOL', 1),
    ('iRightHandType', 'INT32', 0), ('iLeftHandType', 'INT32', 0),
    ('iEquippedItemState', 'INT32', 0), ('IsUnequipping', 'BOOL', 0),
    ('IsRecoiling', 'BOOL', 0), ('IsStaggering', 'BOOL', 0),
    ('IsBleedingOut', 'BOOL', 0), ('IsBashing', 'BOOL', 0),
    ('bAnimationDriven', 'BOOL', 0), ('bAllowRotation', 'BOOL', 0),
    ('bHeadTracking', 'BOOL', 0), ('bCanHeadTrack', 'BOOL', 0),
    ('bDisableHeadTrack', 'BOOL', 0), ('bForceIdleStop', 'BOOL', 0),
]

#: The magic interface, declared only for casters.
MAGIC_VARIABLES = [
    ('bWantCastRight', 'BOOL', 0), ('bWantCastLeft', 'BOOL', 0),
    ('bMRh_Ready', 'BOOL', 0), ('bMLh_Ready', 'BOOL', 0),
    ('IsCasting', 'BOOL', 0),
]

#: The block interface, declared only when a guard clip exists.
BLOCK_VARIABLES = [
    ('IsBlocking', 'BOOL', 0), ('iWantBlock', 'INT32', 0),
]

#: The standard event vocabulary every generated graph declares.
BASE_EVENTS = [
    'moveStart', 'moveStop', 'moveForward', 'moveBackward',
    'turnLeft', 'turnRight', 'turnStop', 'swimStart', 'swimStop',
    'runStart', 'walkStart',
    'recoilStart', 'recoilStop', 'recoilLargeStart',
    'staggerStart', 'staggerStop',
    'combatStanceStart', 'combatStanceStop',
    'weaponDraw', 'weaponSheathe', 'weaponSwing',
    'attackStop', 'returnToDefault', 'deathStart', 'IdleStop',
    'DeathAnimation', 'Ragdoll', 'RagdollInstant', 'GetUpBegin',
    'AddRagdollToWorld', 'RemoveCharacterControllerFromWorld',
    'SoundPlay', 'preHitFrame', 'HitFrame',
    'FootFront', 'FootBack', 'FootLeft', 'FootRight',
]

#: The magic handshake: BeginCast* goes out, Spell_* comes back in.
MAGIC_EVENTS = [
    'BeginCastRight', 'BeginCastLeft',
    'MRh_SpellFire_Event', 'MLh_SpellFire_Event',
    'Spell_FireForget_LH', 'Spell_FireForget_RH',
    'Spell_Concentration_LH', 'Magic_Pre_Out',
    'Spell_Ready', 'Spell_Release', 'Spell_Stop',
    'Spell_Interrupt', 'InterruptCast', 'Magic_Equip',
]

#: Added when a guard clip exists.
BLOCK_EVENTS = ['blockStart', 'blockStop', 'blockHitStart', 'blockHitStop']


# ---------------------------------------------------------------------------
# Table builders
# ---------------------------------------------------------------------------

def equip_events(clips):
    """One enter-event per equip/unequip state, in stance order.

    The root wildcard routes weaponDraw/weaponSheathe to whichever matches
    the engine-set iRightHandType.
    """
    out = []
    for stance in sorted(clips.get('equip', {})):
        out.append(f'equipStart_{stance}')
        if clips['equip'][stance][1]:
            out.append(f'unequipStart_{stance}')
    return out


def graph_events(clips, vocal_states=None, sound_events=None, getup=False):
    """The graph's full event table, in declaration order.

    `sound_events` are FULLY-QUALIFIED `SoundPlay.<SNDR EditorID>` names;
    every one must appear here or the annotation resolves to no event and is
    dropped.  `vocal_states` contribute their IDLE records' ENAM strings.
    `getup` appends the getup state's events last, leaving every other index as it was.
    """
    events = list(BASE_EVENTS)
    if cast_phase_defs(clips):
        events += MAGIC_EVENTS
    if clips.get('block', {}).get('idle'):
        events += BLOCK_EVENTS
    events += [evt for _s, evt, _a in (vocal_states or [])]
    events += [e for e in dict.fromkeys(sound_events or [])
               if e not in events]
    events += build_attack_events(clips)
    events += equip_events(clips)
    if getup:
        events += GETUP_EVENTS
    return events


def movement_type_names(name: str, has_swim: bool = False) -> list:
    """The creature's engine movement-type names.

    The engine registers an actor's movement types by enumerating the ROOT
    graph's `iState_<X>` variables and looking up the MOVT whose MNAM == X.
    A graph with none gives the movement controller ZERO movement types and
    the actor cannot move at all.  There is deliberately NO casting movement
    type; pinning a caster is bAnimationDriven's job.  The game namespace is
    upper-cased so Oblivion keeps the exact `TES4<name>` spelling it shipped.
    See: docs/commentary/asset_convert_creature.md#7b-ii-casters-slide-while-casting--pinned-with-banimationdriven-2026-08-26
    """
    base = f'{current_namespace().upper()}{name.lower()}'
    out = [f'{base}Default', f'{base}Run']
    if has_swim:
        out.append(f'{base}Swim')
    return out


def graph_variables(clips: dict, movement_types: list) -> list:
    """The graph's variable table: (name, type, initial word value).

    iState family: vanilla-style movement-type tags (dogbehavior
    iState_DogDefault=30 / iState_DogRun=31).  `isSwimming` is
    engine-written in deep water and drives the iState switch.
    """
    variables = list(ENGINE_VARIABLES)
    if cast_phase_defs(clips):
        variables += MAGIC_VARIABLES
    if clips.get('block', {}).get('idle'):
        variables += BLOCK_VARIABLES
    if any(mt.endswith('Swim') for mt in movement_types):
        variables.append(('isSwimming', 'BOOL', 0))
    variables += [(f'iState_{mt}', 'INT32', 30 + i)
                  for i, mt in enumerate(movement_types)]
    return variables
