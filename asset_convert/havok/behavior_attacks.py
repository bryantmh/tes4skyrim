"""Root states entered by single-play clips: interrupts, attacks, equips.

Split out of hkx_behavior.build_behavior_xml.  Everything here becomes a
state directly under the graph's root machine, entered by a wildcard on an
engine event and leaving on its clip's own end trigger.

Attacks are gated by NESTING when the creature ships more than one weapon
stance: one sub-machine per stance inside a selector whose active state is
bound to the engine-written iRightHandType.  A child state is unreachable
while its parent is inactive, which is what stops an armed actor playing a
bare-handed swing.
See: docs/commentary/asset_convert_creature.md#attacks-are-gated-by-nesting
"""

from asset_convert.havok.behavior_clips import (
    EQUIP_STANCES,
    SINGLE_PLAY,
    build_attack_events,
    clip_state_name,
)
from asset_convert.havok.behavior_nodes import (
    F_GLOBAL,
    F_LOCAL,
    F_WILD,
)

#: Nearest-armed-stance order for a hand type the creature has no clips for.
_FALLBACK_ORDER = ('OneH', 'TwoH', 'Staff', 'Bow', 'H2H')

#: The widest hand-type range the engine can write (WEAP DNAM anim types).
_HAND_TYPES = 10


# ---------------------------------------------------------------------------
# The root-state accumulator
# ---------------------------------------------------------------------------

class RootStates:
    """Accumulates the root machine's states, wildcards and local entries.

    `next_id` is the next free state id; state 0 is always DefaultState, so
    ids start at 1.  `default_trans` holds the LOCAL transitions out of
    DefaultState (attacks), `wilds` the wildcard entries (everything else).
    """

    def __init__(self):
        """Start empty, with state 0 reserved for DefaultState."""
        self.states = []
        self.wilds = []
        self.default_trans = []
        self.next_id = 1

    def add(self, state, event=None, flags=F_WILD):
        """Append `state`, optionally entered by a wildcard on `event`."""
        self.states.append(state)
        if event is not None:
            self.wilds.append((event, self.next_id, flags))
        self.next_id += 1
        return self.next_id - 1

    def extend(self, states, wilds):
        """Append a builder's `(states, wildcards)` result."""
        self.states += states
        self.wilds += wilds
        self.next_id += len(states)


# ---------------------------------------------------------------------------
# Single-play interrupts
# ---------------------------------------------------------------------------

def add_interrupts(gb, root, clips, hit_times):
    """Recoil, stagger, death and the other SINGLE_PLAY interrupt states.

    A ragdoll-less creature's Death state deliberately keeps no enter/exit
    notify and no end trigger, exactly as vanilla builds a single-play state.
    See: docs/commentary/asset_convert_creature.md#ragdoll-less-creatures
    """
    eid = gb.eid
    for st, (_names, enter, stop_evt) in SINGLE_PLAY.items():
        if st not in clips['single']:
            continue
        end_evt = 'returnToDefault' if st != 'Death' else None
        clip = gb.clip(st, clips['single'][st], False,
                       gb.clip_triggers(hit_times.get(st, []), end_evt))
        if st != 'Death':
            clip = gb.holding(st, clip.ref, ['bAnimationDriven'])
        state = gb.state(root.next_id, f'{st}State', clip.ref,
                         exit_events=[stop_evt] if stop_evt else None)
        state_id = root.add(state, eid[enter],
                            F_WILD if st != 'Death' else F_GLOBAL)
        if st == 'Recoil':
            root.wilds.append((eid['recoilLargeStart'], state_id, F_WILD))


# ---------------------------------------------------------------------------
# Attacks
# ---------------------------------------------------------------------------

def _attack_specs(gb, clips, hit_times, attack_ml, multi_stance):
    """One `(stance, state name, generator ref, event)` per attack clip."""
    stance_of = clips.get('attack_stance', {})
    specs = []
    for kf, evt in zip(clips['attacks'], build_attack_events(clips)):
        st_name = f'Attack_{clip_state_name(kf)}'
        clip = gb.clip(st_name, kf, False, gb.clip_triggers(
            hit_times.get(st_name, []), 'returnToDefault'))
        mg = gb.add('hkbModifierGenerator')
        mg.param('variableBindingSet', 'null')
        mg.param('userData', 1)
        mg.param('name', f'{st_name}_MG')
        mg.param('modifier', attack_ml.ref)
        mg.param('generator', clip.ref)
        stance = stance_of.get(kf) if multi_stance else None
        if stance not in clips.get('equip', {}):
            stance = None
        specs.append((stance, st_name, mg.ref, evt))
    return specs


def attack_modifier_list(gb):
    """The IsActive modifier every attack state wraps its clip in.

    Built by the caller BEFORE any root state: packfile object order is the
    id contract, and this is where the inline code created it.

    While the state is active it drives IsAttacking=1 (the combat
    controller's in-progress flag), bAllowRotation=1 (target tracking during
    the swing), bDisableHeadTrack=1 and bAnimationDriven=1 (motion from the
    clip, not the commanded velocity).
    See: docs/commentary/asset_convert_creature.md#attack-pin
    """
    bind = gb.binding_set([('bIsActive0', 'IsAttacking'),
                           ('bIsActive1', 'bAllowRotation'),
                           ('bIsActive2', 'bDisableHeadTrack'),
                           ('bIsActive3', 'bAnimationDriven')])
    iso = gb.add('BSIsActiveModifier')
    iso.param('variableBindingSet', bind.ref)
    iso.param('userData', 2)
    iso.param('name', 'BSIsActiveModifier_IsAttacking')
    iso.param('enable', True)
    for i in range(5):
        iso.param(f'bIsActive{i}', False)
        iso.param(f'bInvertActive{i}', False)
    ml = gb.add('hkbModifierList')
    ml.param('variableBindingSet', 'null')
    ml.param('userData', 1)
    ml.param('name', 'AttackModifierList')
    ml.param('enable', True)
    ml.param_array('modifiers', [iso.ref])
    return ml


def _stance_by_hand_type(clips):
    """Hand type -> owning stance, so a selector stateId IS the hand type.

    Every type the engine can write must resolve to a state; uncovered ones
    fall back to the nearest armed stance.
    """
    out = {}
    for stance in clips.get('equip', {}):
        for t in EQUIP_STANCES[stance][2]:
            out[t] = stance
    have = [st for st in _FALLBACK_ORDER if st in clips.get('equip', {})]
    for t in range(_HAND_TYPES):
        if t not in out and have:
            out[t] = have[0]
    return out


def _stance_machine(gb, entries, unstanced, stance):
    """One stance's attacks as a sub-machine, plus the unstanced ones.

    Unstanced attacks stay reachable in every stance.
    """
    eid, inner, trans = gb.eid, [], []
    for st_name, gen_ref, evt in entries:
        idx = len(inner)
        inner.append(gb.state(idx, f'{st_name}State', gen_ref,
                              exit_events=['attackStop'],
                              transitions=[(eid['returnToDefault'], 0,
                                            F_LOCAL)]))
        trans.append((eid[evt], idx, F_WILD))
    for st_name, gen_ref, evt in unstanced:
        if any(x[0] == st_name for x in entries):
            continue
        idx = len(inner)
        inner.append(gb.state(idx, f'{st_name}State', gen_ref,
                              exit_events=['attackStop'],
                              transitions=[(eid['returnToDefault'], 0,
                                            F_LOCAL)]))
        trans.append((eid[evt], idx, F_WILD))
    return gb.state_machine(f'Attack_{stance}_SM', inner, start_id=0,
                            wildcard_ref=gb.trans_array(trans, root=True).ref)


def _stance_selector(gb, root, specs, clips):
    """The iRightHandType-bound selector over the per-stance machines.

    It starts in the LOWEST hand type present, matching iRightHandType's own
    init value so the graph is consistent before the first equip.  Returns
    True when it was built; False when no stance had any attack and the
    caller should fall back to flat attack states.
    """
    by_stance = {}
    for stance, st_name, gen_ref, evt in specs:
        by_stance.setdefault(stance, []).append((st_name, gen_ref, evt))
    stance_by_type = _stance_by_hand_type(clips)

    sel_states = []
    for hand_type in sorted(stance_by_type):
        stance = stance_by_type[hand_type]
        entries = by_stance.get(stance) or by_stance.get(None) or []
        if not entries:
            continue
        sub = _stance_machine(gb, entries, by_stance.get(None, []), stance)
        sel_states.append(gb.state(hand_type, f'Attack_{stance}_State',
                                   sub.ref))
    if not sel_states:
        return False

    bind = gb.binding_set([('startStateId', 'iRightHandType')])
    selector = gb.state_machine('AttackStanceSelector', sel_states,
                                start_id=min(stance_by_type),
                                binding_ref=bind.ref)
    state_id = root.next_id
    root.add(gb.state(state_id, 'AttackStanceState', selector.ref,
                      transitions=[(gb.eid['returnToDefault'], 0, F_LOCAL)],
                      root=True))
    for _stance, _st_name, _gen, evt in specs:
        root.default_trans.append((gb.eid[evt], state_id, F_LOCAL))
    return True


def add_attacks(gb, root, clips, hit_times, attack_ml):
    """Attack states, nested per weapon stance when the creature has several.

    `attack_ml` comes from `attack_modifier_list`, which the caller builds
    before any root state so packfile object ids stay stable.
    See: docs/commentary/asset_convert_creature.md#attacks-are-gated-by-nesting
    """
    multi_stance = len(clips.get('equip', {})) > 1
    specs = _attack_specs(gb, clips, hit_times, attack_ml, multi_stance)
    if multi_stance and any(sp[0] for sp in specs):
        if _stance_selector(gb, root, specs, clips):
            return
    for _stance, st_name, gen_ref, evt in specs:
        state_id = root.next_id
        root.add(gb.state(state_id, f'{st_name}State', gen_ref,
                          exit_events=['attackStop']))
        root.default_trans.append((gb.eid[evt], state_id, F_LOCAL))


# ---------------------------------------------------------------------------
# Equip and vocal idles
# ---------------------------------------------------------------------------

def add_equips(gb, root, clips, hit_times):
    """Equip/unequip states; returns the dispatch table for the EEM.

    The engine sends weaponDraw when it wants the actor armed; the
    expression modifier turns that into the per-class equipStart_* event
    matching the engine-set iRightHandType, and the clip's END trigger
    replies weaponDraw so the combat controller knows the actor is ready.
    Sheathing is the mirror image.
    """
    dispatch = []
    for stance, (eq, uneq) in sorted(clips.get('equip', {}).items()):
        hand_types = EQUIP_STANCES[stance][2]
        for kind, kf, reply in (('equip', eq, 'weaponDraw'),
                                ('unequip', uneq, 'weaponSheathe')):
            if kf is None:
                continue
            st_name = f'{kind.capitalize()}_{stance}'
            evt = f'{kind}Start_{stance}'
            clip = gb.holding(st_name, gb.clip(st_name, kf, False, gb.clip_triggers(
                hit_times.get(st_name, []), reply)).ref, ['bAnimationDriven'])
            root.add(gb.state(root.next_id, f'{st_name}State', clip.ref,
                              transitions=[(gb.eid['returnToDefault'], 0,
                                            F_LOCAL)], root=True),
                     gb.eid[evt])
            dispatch.append((kind, evt, hand_types))
    return dispatch


def add_vocal_idles(gb, root, clips, hit_times, vocal_states):
    """Single-play vocal idle states (CSDT Idle/Aware slots).

    See: docs/commentary/asset_convert_creature.md#idlestop-is-local
    """
    for st_name, evt, stem in (vocal_states or []):
        clip = gb.clip(st_name, clips['idle'], False, gb.clip_triggers(
            hit_times.get(st_name, []), 'returnToDefault'), anim=stem)
        root.add(gb.state(root.next_id, f'{st_name}State', clip.ref,
                          transitions=[(gb.eid['IdleStop'], 0, F_LOCAL)],
                          root=True),
                 gb.eid[evt])


def equip_dispatch_modifier(gb, dispatch):
    """The EEM turning weaponDraw/Sheathe into the class-specific event.

    Guarded on iRightHandType so a bow draw plays bowequip and a mace draw
    plays onehandequip; the H2H entry (hand type 0) doubles as the fallback.
    """
    if not dispatch:
        return None
    exprs = []
    for kind, evt, hand_types in dispatch:
        src = 'weaponDraw' if kind == 'equip' else 'weaponSheathe'
        cond = ' || '.join(f'(iRightHandType == {t})' for t in hand_types)
        exprs.append((f'{evt} if (({src}) && ({cond}))',
                      'EVENT_MODE_SEND_ON_FALSE_TO_TRUE'))
    arr = gb.expression_array(exprs)
    eem = gb.add('hkbEvaluateExpressionModifier')
    eem.param('variableBindingSet', 'null')
    eem.param('userData', 2)
    eem.param('name', 'EquipDispatch_EEM')
    eem.param('enable', True)
    eem.param('expressions', arr.ref)
    return eem
