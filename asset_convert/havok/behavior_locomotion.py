"""The nested standing/locomotion machines: DefaultBehavior and below.

Split out of hkx_behavior.build_behavior_xml.  This is the vanilla quadruped
topology: turns nest inside StandingBehavior (unreachable while moving or
attacking) rather than sitting at the root.  The old FLAT graph made every
engine event a root wildcard, so the continuously-sent combat facing
adjustments hijacked the graph — constant body-whipping — and aborted every
attack mid-swing.

`build_default` returns `(default_sm, loco_sm, gait_eem)`: the machine the
root's DefaultState wraps, the locomotion sub-machine (None when the creature
has no forward gait) and the walk/run hysteresis modifier the root modifier
list needs (None unless the creature ships a run gait).
See: docs/commentary/asset_convert_creature.md#strafes-are-blends-not-states
"""

from asset_convert.havok.behavior_clips import (
    backward_blend_plan,
    combat_idle_clip,
    direction_children,
    gait_blend_plan,
    gait_thresholds,
    run_blend_plan,
    speed_blend_plan,
)
from asset_convert.havok.behavior_nodes import F_LOCAL


# ---------------------------------------------------------------------------
# Gait blends
# ---------------------------------------------------------------------------

def gait_speed_blend(gb, name, kf, spd):
    """`gait_blend_plan` as a SpeedSampled blend, or a bare clip when it has no speed.

    The chaurus per-direction layout (RightSlow / Right / Right_Run).  The 2x
    child is there because the engine's commanded speed at a strafe heading
    blends the MOVT columns rather than using the strafe column alone.
    See: docs/commentary/asset_convert_creature.md#strafes-are-blends-not-states
    """
    plan = gait_blend_plan(name, kf, spd)
    if len(plan) == 1:
        return gb.clip(name, kf, True)
    return gb.parametric_blend(f'{name}Blend', plan)


def direction_family(gb, prefix, fwd_ref, loco, speeds):
    """The Direction blend around ONE gait family (walk or run).

    Forward @0 plus the `direction_children` the creature ships, each as its
    own speed blend.  None when there is nothing to blend.
    """
    kids = [(fwd_ref, 0.0)] + [
        (gait_speed_blend(gb, f'{prefix}{sfx}', kf, spd).ref, anchor)
        for sfx, kf, anchor, spd in direction_children(loco, speeds)]
    if len(kids) == 1:
        return None
    return gb.direction_blend(f'{prefix}DirectionalBlend', kids)


# ---------------------------------------------------------------------------
# LocomotionBehavior
# ---------------------------------------------------------------------------

def _gait_hysteresis(gb, speeds):
    """The EEM switching walk<->run on SpeedSampled, with hysteresis.

    iMovementSpeed picks the start state at the same runStart threshold;
    comparison operators must be XML-escaped in the packfile text.
    """
    lo, hi = gait_thresholds(speeds)
    arr = gb.expression_array(
        [(f'iMovementSpeed = cond((Speed &lt; {hi}), 0, 1)',
          'EVENT_MODE_SEND_ONCE'),
         (f'runStart if (SpeedSampled &gt; {hi})',
          'EVENT_MODE_SEND_ON_FALSE_TO_TRUE'),
         (f'walkStart if (SpeedSampled &lt; {lo})',
          'EVENT_MODE_SEND_ON_FALSE_TO_TRUE')])
    eem = gb.add('hkbEvaluateExpressionModifier')
    eem.param('variableBindingSet', 'null')
    eem.param('userData', 2)
    eem.param('name', 'ForwardLocomotion_EEM')
    eem.param('enable', True)
    eem.param('expressions', arr.ref)
    return eem


def _forward_generator(gb, clips, speeds, loco):
    """The forward gait, as `(generator, gait_eem)`.

    One family when the creature has only a walk; two states switched by
    runStart/walkStart when it also ships a run, which is the vanilla
    forwardlocomotion layout — the walk blend and the run blend never mix
    clips.
    """
    fplan = speed_blend_plan(clips, speeds)
    walk = (gb.parametric_blend('ForwardWalkBlend', fplan) if fplan
            else gb.clip('MoveForward', loco['MoveForward'], True))
    walk = direction_family(gb, 'Walk', walk.ref, loco, speeds) or walk

    rplan = run_blend_plan(clips, speeds)
    if not rplan:
        return walk, None
    run = gb.parametric_blend('ForwardRunBlend', rplan)
    run = direction_family(gb, 'Run', run.ref, loco, speeds) or run
    sm = gb.state_machine(
        'ForwardLocomotionBehavior',
        [gb.state(0, 'ForwardWalkState', walk.ref,
                  transitions=[(gb.eid['runStart'], 1, F_LOCAL)]),
         gb.state(1, 'ForwardRunState', run.ref,
                  transitions=[(gb.eid['walkStart'], 0, F_LOCAL)])],
        binding_ref=gb.binding_set(
            [('startStateId', 'iMovementSpeed')]).ref)
    return sm, _gait_hysteresis(gb, speeds)


def _locomotion_machine(gb, clips, speeds, loco):
    """LocomotionBehavior: Forward(0) <-> Backward(1), or None.

    Strafes live inside the gait families' Direction blends, never as
    event-entered states; the engine's moveBackward event still owns the
    dedicated BackwardLocomotionState.
    """
    if 'MoveForward' not in loco:
        return None, None
    eid = gb.eid
    fwd_gen, gait_eem = _forward_generator(gb, clips, speeds, loco)
    has_back = 'MoveBackward' in loco
    states = [gb.state(
        0, 'ForwardLocomotionState', fwd_gen.ref,
        transitions=([(eid['moveBackward'], 1, F_LOCAL)] if has_back
                     else None))]
    if has_back:
        bplan = backward_blend_plan(clips, speeds)
        back = (gb.parametric_blend('BackwardSpeedBlend', bplan) if bplan
                else gb.clip('MoveBackward', loco['MoveBackward'], True))
        states.append(gb.state(1, 'BackwardLocomotionState', back.ref,
                               transitions=[(eid['moveForward'], 0,
                                             F_LOCAL)]))
    return gb.state_machine('LocomotionBehavior', states), gait_eem


# ---------------------------------------------------------------------------
# StandingBehavior and DefaultBehavior
# ---------------------------------------------------------------------------

def _standing_idle_machine(gb, clips):
    """StandingIdleBehavior: NonCombatIdle(0) <-> CombatIdle(1).

    The start state syncs to `iCombatStance` (0/1), so returning from an
    action re-enters the combat idle instead of the default state.
    """
    eid = gb.eid
    idle = gb.clip('Idle', clips['idle'], True)
    combat = gb.clip('CombatStance', combat_idle_clip(clips), True)
    return gb.state_machine('StandingIdleBehavior', [
        gb.state(0, 'NonCombatIdleState', idle.ref,
                 transitions=[(eid['combatStanceStart'], 1, F_LOCAL)]),
        gb.state(1, 'CombatIdleState', combat.ref,
                 transitions=[(eid['combatStanceStop'], 0, F_LOCAL)]),
    ], sync_var='iCombatStance')


def _standing_machine(gb, clips, loco):
    """StandingBehavior: TurnRight(0), StandingIdle(1), TurnLeft(2).

    Vanilla's ids and start state, bound to the engine's `iSyncTurnState`
    when both turns exist, so re-entry after an action resumes a turn.
    States are listed in id order: the engine resolves id 0 by checking
    only `states[0]`.
    See: docs/commentary/asset_convert_creature.md#resume-locomotion
    """
    eid = gb.eid
    turns = {sid: (name, evt) for sid, name, evt in
             ((0, 'TurnRight', 'turnRight'), (2, 'TurnLeft', 'turnLeft'))
             if name in loco}
    to_turns = [(eid[evt], sid, F_LOCAL) for sid, (_n, evt) in turns.items()]
    states = {1: gb.state(1, 'StandingIdleState',
                          _standing_idle_machine(gb, clips).ref,
                          transitions=to_turns or None)}
    for sid, (name, _evt) in turns.items():
        trans = [t for t in to_turns if t[1] != sid]
        states[sid] = gb.state(
            sid, f'Looping{name}', gb.clip(name, loco[name], True).ref,
            transitions=trans + [(eid['turnStop'], 1, F_LOCAL)])
    bind = (gb.binding_set([('startStateId', 'iSyncTurnState')]).ref
            if len(turns) == 2 else 'null')
    return gb.state_machine('StandingBehavior',
                            [states[k] for k in sorted(states)],
                            start_id=1, binding_ref=bind)


def build_default(gb, clips, speeds):
    """DefaultBehavior: Standing(0) <-> Locomotion(1).

    Its start state is bound to `iSyncIdleLocomotion` (vanilla), so coming
    back from an attack, recoil or equip mid-stride resumes walking: the
    engine sends `moveStart` once and never again while the actor moves.
    Returns `(default_sm, loco_sm, gait_eem)` — the machine the root's
    DefaultState wraps, the locomotion sub-machine (None without a forward
    gait) and the walk/run hysteresis modifier (None without a run gait).
    See: docs/commentary/asset_convert_creature.md#resume-locomotion
    """
    eid = gb.eid
    loco = clips['locomotion']
    loco_sm, gait_eem = _locomotion_machine(gb, clips, speeds, loco)
    standing_sm = _standing_machine(gb, clips, loco)
    states = [gb.state(
        0, 'StandingState', standing_sm.ref,
        transitions=([(eid['moveStart'], 1, F_LOCAL)] if loco_sm else None))]
    if loco_sm:
        states.append(gb.state(1, 'LocomotionState', loco_sm.ref,
                               transitions=[(eid['moveStop'], 0, F_LOCAL)]))
    bind = gb.binding_set([('startStateId', 'iSyncIdleLocomotion')]).ref
    return (gb.state_machine('DefaultBehavior', states, binding_ref=bind),
            loco_sm, gait_eem)
