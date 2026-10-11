"""Root-state builders for the optional action branches of a creature graph.

Split out of hkx_behavior.build_behavior_xml, which held every branch inline
in one 600-statement function.  Swimming, spellcasting and blocking are each
a self-contained cluster: a sub-machine wrapped in the modifier that holds
the engine variable the branch owns (IsCasting, IsBlocking), reachable from
the root by one wildcard event.

Each builder returns `(states, wildcards)` — the root states to append and
the `(event id, state id, flags)` wildcards that enter them — so the caller
keeps ownership of state-id allocation and nothing here mutates its tables.
A branch whose clips the creature does not ship returns `([], [])`.

See: docs/commentary/asset_convert_creature.md#7c-swimming-implemented-2026-08-22
"""

from asset_convert.havok.behavior_clips import (
    cast_anim_stems,
    swim_blend_plan,
)
from asset_convert.havok.behavior_nodes import (
    F_LOCAL,
    F_WILD,
    TRIGGER_TMPL,
)

#: Vanilla's own Mag_FF_RH_Out release offset, used when none is authored.
_DEFAULT_RELEASE_T = 0.233334


# ---------------------------------------------------------------------------
# Shared modifier wrappers
# ---------------------------------------------------------------------------

def _is_active_modifier(gb, name, bindings):
    """A BSIsActiveModifier holding `bindings` true while its branch runs."""
    iso = gb.add('BSIsActiveModifier')
    iso.param('variableBindingSet', gb.binding_set(bindings).ref)
    iso.param('userData', 1)
    iso.param('name', name)
    iso.param('enable', True)
    for i in range(5):
        iso.param(f'bIsActive{i}', False)
        iso.param(f'bInvertActive{i}', False)
    return iso


def _modifier_list(gb, name, modifier_ref):
    """A single-entry hkbModifierList wrapping `modifier_ref`."""
    ml = gb.add('hkbModifierList')
    ml.param('variableBindingSet', 'null')
    ml.param('userData', 1)
    ml.param('name', name)
    ml.param('enable', True)
    ml.param_array('modifiers', [modifier_ref])
    return ml


def _modifier_generator(gb, name, modifier_ref, generator_ref):
    """An hkbModifierGenerator applying `modifier_ref` over a generator."""
    mg = gb.add('hkbModifierGenerator')
    mg.param('variableBindingSet', 'null')
    mg.param('userData', 1)
    mg.param('name', name)
    mg.param('modifier', modifier_ref)
    mg.param('generator', generator_ref)
    return mg


# ---------------------------------------------------------------------------
# Swimming
# ---------------------------------------------------------------------------

def _swim_states(gb, sw, clips, speeds):
    """The SwimBehavior sub-machine's own states, in state-id order."""
    eid = gb.eid
    idle_trans = [(eid['moveStart'], 1, F_LOCAL)]
    if sw.get('left'):
        idle_trans.append((eid['turnLeft'], 3, F_LOCAL))
    if sw.get('right'):
        idle_trans.append((eid['turnRight'], 4, F_LOCAL))
    states = [gb.state(
        0, 'SwimIdleState',
        gb.clip('SwimIdle', sw.get('idle') or sw['forward'], True).ref,
        transitions=idle_trans)]

    splan = swim_blend_plan(clips, speeds)
    move_gen = (gb.parametric_blend('SwimSpeedBlend', splan) if splan
                else gb.clip('SwimMove', sw['forward'], True))
    move_trans = [(eid['moveStop'], 0, F_LOCAL)]
    if sw.get('backward'):
        move_trans.append((eid['moveBackward'], 2, F_LOCAL))
    states.append(gb.state(1, 'SwimMoveState', move_gen.ref,
                           transitions=move_trans))

    if sw.get('backward'):
        states.append(gb.state(
            2, 'SwimBackState',
            gb.clip('SwimBack', sw['backward'], True).ref,
            transitions=[(eid['moveForward'], 1, F_LOCAL),
                         (eid['moveStop'], 0, F_LOCAL)]))
    for sid, role, name in ((3, 'left', 'SwimTurnLeft'),
                            (4, 'right', 'SwimTurnRight')):
        if sw.get(role):
            states.append(gb.state(
                sid, f'{name}State', gb.clip(name, sw[role], True).ref,
                transitions=[(eid['turnStop'], 0, F_LOCAL)]))
    return states


def build_swim(gb, clips, speeds, state_id):
    """SwimState: a sub-machine mirroring the land standing/locomotion split.

    Driven by the same engine locomotion events.  Vanilla sabrecat ships a
    single looping SwimForward; the richer Oblivion gait sets
    (slaughterfish: idle/turns/fast) wire in when authored.
    """
    sw = clips.get('swim', {})
    if not sw.get('forward'):
        return [], []
    sm = gb.state_machine('SwimBehavior', _swim_states(gb, sw, clips, speeds))
    state = gb.state(state_id, 'SwimState', sm.ref,
                     transitions=[(gb.eid['swimStop'], 0, F_LOCAL)], root=True)
    return [state], [(gb.eid['swimStart'], state_id, F_WILD)]


# ---------------------------------------------------------------------------
# Spellcasting
# ---------------------------------------------------------------------------

def _cast_clip(gb, spec, stems, release_t):
    """One In/Loop/Out cast slice, carrying its own phase triggers.

    The Out slice fires BOTH hand events: vanilla's own data answers a
    right-hand cast with the LEFT event (both atronach Out clips fire
    MLh_SpellFire_Event) and the extra event is inert when that hand has no
    cast in flight.
    """
    st_name, kf, phase, _stem = spec
    eid, items = gb.eid, []
    if phase == 'In':
        items.append(TRIGGER_TMPL.format(
            time=0.0, event_id=eid['Magic_Pre_Out'], rel='true'))
    elif phase == 'Out':
        items.append(TRIGGER_TMPL.format(
            time=release_t, event_id=eid['MLh_SpellFire_Event'], rel='false'))
        items.append(TRIGGER_TMPL.format(
            time=release_t, event_id=eid['MRh_SpellFire_Event'], rel='false'))
        items.append(TRIGGER_TMPL.format(
            time=0.0, event_id=eid['Spell_Stop'], rel='true'))
    trig = 'null'
    if items:
        ta = gb.add('hkbClipTriggerArray')
        ta.param_raw('triggers', '\n'.join(items), numelements=len(items))
        trig = ta.ref
    return gb.clip(st_name, kf, phase == 'Loop', trig,
                   anim=stems.get(st_name))


def _cast_machine(gb, cast_defs, clips, hit_times):
    """The Mag_FF_Behavior In -> Loop -> Out sub-machine.

    In chains to Loop on the In clip's own end trigger; the engine may
    commit early, so Spell_Release must work from the WHOLE chain (vanilla
    scopes it as a wildcard of the FF sub-machine).
    """
    eid = gb.eid
    stems = cast_anim_stems(clips)
    release_t = (hit_times.get('Mag_FF_Out') or [_DEFAULT_RELEASE_T])[0]
    in_clip, loop_clip, out_clip = [
        _cast_clip(gb, spec, stems, release_t) for spec in cast_defs]
    states = [
        gb.state(0, 'Mag_FF_InState', in_clip.ref,
                 transitions=[(eid['Magic_Pre_Out'], 1, F_LOCAL)]),
        gb.state(1, 'Mag_FF_LoopState', loop_clip.ref),
        gb.state(2, 'Mag_FF_OutState', out_clip.ref),
    ]
    wild = gb.trans_array([
        (eid['Spell_Release'], 2, F_WILD),
        (eid['Spell_Ready'], 0,
         'FLAG_IS_LOCAL_WILDCARD|'
         'FLAG_ALLOW_SELF_TRANSITION_BY_TRANSITION_FROM_ANY_STATE|'
         'FLAG_DISABLE_CONDITION'),
    ])
    return gb.state_machine('Mag_FF_Behavior', states, start_id=0,
                            wildcard_ref=wild.ref)


def _cast_entry_wilds(eid, state_id):
    """The engine's cast-type replies to our BeginCast*, plus Magic_Equip.

    Magic_Equip means "snap to combat idle" in vanilla; our combat idle
    lives inside DefaultState, hence state 0.
    """
    wilds = [(eid[e], state_id, F_WILD)
             for e in ('Spell_FireForget_LH', 'Spell_FireForget_RH',
                       'Spell_Concentration_LH')]
    wilds.append((eid['Magic_Equip'], 0, F_WILD))
    return wilds


def build_cast(gb, cast_defs, clips, hit_times, state_id):
    """FireForgetState: the vanilla FireForget chain, entered by the engine.

    Three variables are held true for the whole chain by one IsActive
    modifier: IsCasting (the engine's `!IsCasting` guard blocks a second
    overlapping cast), bAnimationDriven (motion comes from the clip's root
    motion, which the cast slices lack, so the actor stands planted) and
    bAllowRotation (an animation-driven actor cannot otherwise turn, and the
    AI faces its target before it will release).
    See: docs/commentary/asset_convert_creature.md#7b-ii-casters-slide-while-casting--pinned-with-banimationdriven-2026-08-26
    """
    if not cast_defs:
        return [], []
    eid = gb.eid
    sm = _cast_machine(gb, cast_defs, clips, hit_times)
    iso = _is_active_modifier(gb, 'BSIsActiveModifier_Spells',
                              [('bIsActive0', 'IsCasting'),
                               ('bIsActive1', 'bAnimationDriven'),
                               ('bIsActive2', 'bAllowRotation')])
    ml = _modifier_list(gb, 'Spells_ModifierList', iso.ref)
    mg = _modifier_generator(gb, 'FF_MG', ml.ref, sm.ref)
    state = gb.state(state_id, 'FireForgetState', mg.ref,
                     transitions=[(eid['Spell_Stop'], 0, F_LOCAL),
                                  (eid['Spell_Interrupt'], 0, F_LOCAL),
                                  (eid['InterruptCast'], 0, F_LOCAL)],
                     root=True)
    return [state], _cast_entry_wilds(eid, state_id)


# ---------------------------------------------------------------------------
# Blocking
# ---------------------------------------------------------------------------

def _block_hit_state(gb, blk, ml, hit_id, block_id):
    """BlockHitState: the flinch, firing its own blockHitStop at clip end.

    The guard resumes even if the engine never sends the stop.  It builds
    the trigger array directly rather than through `clip_triggers`: an
    authored 'Hit' key on a flinch must NOT become a damage HitFrame.
    """
    eid = gb.eid
    ta = gb.add('hkbClipTriggerArray')
    ta.param_raw('triggers', TRIGGER_TMPL.format(
        time=0.0, event_id=eid['blockHitStop'], rel='true'), numelements=1)
    clip = gb.clip('BlockHit', blk['hit'], False, ta.ref)
    return gb.state(
        hit_id, 'BlockHitState',
        _modifier_generator(gb, 'BlockHit_MG', ml.ref, clip.ref).ref,
        transitions=[(eid['blockHitStop'], block_id, F_LOCAL),
                     (eid['blockStop'], 0, F_LOCAL)], root=True)


def build_block(gb, clips, state_id):
    """BlockState: loops the guard with IsBlocking held true.

    The engine reads IsBlocking — draugrbehavior binds the same variable in
    its block states — and blockHitStart plays the flinch before returning
    to the guard.
    """
    blk = clips.get('block', {})
    if not blk.get('idle'):
        return [], []
    eid = gb.eid
    iso = _is_active_modifier(gb, 'BSIsActiveModifier_IsBlocking',
                              [('bIsActive0', 'IsBlocking')])
    ml = _modifier_list(gb, 'Block_ModifierList', iso.ref)

    block_id = state_id
    hit_id = state_id + 1 if blk.get('hit') else None
    trans = [(eid['blockStop'], 0, F_LOCAL)]
    if hit_id is not None:
        trans.append((eid['blockHitStart'], hit_id, F_LOCAL))
    guard = gb.clip('Block', blk['idle'], True)
    states = [gb.state(
        block_id, 'BlockState',
        _modifier_generator(gb, 'Block_MG', ml.ref, guard.ref).ref,
        transitions=trans, root=True)]
    wilds = [(eid['blockStart'], block_id, F_WILD)]
    if hit_id is not None:
        states.append(_block_hit_state(gb, blk, ml, hit_id, block_id))
        wilds.append((eid['blockHitStart'], hit_id, F_WILD))
    return states, wilds


# ---------------------------------------------------------------------------
# Root modifiers
# ---------------------------------------------------------------------------

def _expression_modifier(gb, name, exprs):
    """An hkbEvaluateExpressionModifier over (expression, eventMode) pairs."""
    arr = gb.expression_array(exprs)
    eem = gb.add('hkbEvaluateExpressionModifier')
    eem.param('variableBindingSet', 'null')
    eem.param('userData', 2)
    eem.param('name', name)
    eem.param('enable', True)
    eem.param('expressions', arr.ref)
    return eem


def begin_cast_modifier(gb, cast_defs):
    """The BeginCast_EEM telling the engine this actor wants to cast.

    Level-triggered (SEND_ON_TRUE), never edge-triggered: the engine never
    rewrites its want-cast flag while it waits, so a single false->true edge
    can be missed forever.
    See: docs/commentary/asset_convert_creature.md#begincast-is-level-triggered
    """
    if not cast_defs:
        return None
    return _expression_modifier(gb, 'BeginCast_EEM', [
        ('BeginCastLeft if (bWantCastLeft && bMLh_Ready && !IsCasting)',
         'EVENT_MODE_SEND_ON_TRUE'),
        ('BeginCastRight if (bWantCastRight && bMRh_Ready && !IsCasting)',
         'EVENT_MODE_SEND_ON_TRUE')])


def swim_state_modifier(gb, movement_types, has_swim_mt):
    """The EEM pointing iState at the swim MOVT while isSwimming is set.

    iState is for SWIM ONLY — never for pinning a caster.
    See: docs/commentary/asset_convert_creature.md#istate-is-for-swim-only
    """
    if not has_swim_mt:
        return None
    default_mt = swim_mt = None
    for mt in movement_types:
        if mt.endswith('Swim'):
            swim_mt = f'iState_{mt}'
        elif mt.endswith('Default'):
            default_mt = f'iState_{mt}'
    return _expression_modifier(gb, 'SwimiState_EEM', [
        (f'iState = cond((isSwimming == 1), {swim_mt}, {default_mt})',
         'EVENT_MODE_SEND_ONCE')])


def combat_idle_generator(gb, cast_defs, default_sm):
    """DefaultState's generator, holding cast readiness true when a caster.

    Returns `default_sm.ref` unchanged for a non-caster.
    See: docs/commentary/asset_convert_creature.md#cast-readiness-is-the-graphs
    """
    if not cast_defs:
        return default_sm.ref
    iso = _is_active_modifier(gb, 'BSIsActiveModifier_CombatIdle',
                              [('bIsActive0', 'bMLh_Ready'),
                               ('bIsActive1', 'bMRh_Ready')])
    ml = _modifier_list(gb, 'CombatIdle_ModifierList', iso.ref)
    return _modifier_generator(gb, 'CombatIdle_MG', ml.ref,
                               default_sm.ref).ref
