"""Havok behavior-graph node primitives: one packfile object per method.

Split out of hkx_behavior.py, which held both the graph TOPOLOGY (which
states exist and how they connect) and the XML boilerplate for every node
type.  This is the boilerplate half: a `GraphBuilder` owns the packfile plus
the event/variable index tables, so a caller names a node and gets a ref back
without repeating the ~15 hkparam lines each object type needs.

Node parameter values are vanilla layouts read out of the shipped
dogbehavior / chaurus / draugr dumps.
See: docs/commentary/asset_convert_creature.md#strafes-are-blends-not-states
"""

from asset_convert.havok.behavior_clips import clip_state_name
from asset_convert.havok.hkx_xml import HkxPackfile


# ---------------------------------------------------------------------------
# Packfile XML templates
# ---------------------------------------------------------------------------

TRANSITION_TMPL = '''<hkobject>
\t<hkparam name="triggerInterval">
\t\t<hkobject>
\t\t\t<hkparam name="enterEventId">-1</hkparam>
\t\t\t<hkparam name="exitEventId">-1</hkparam>
\t\t\t<hkparam name="enterTime">0.000000</hkparam>
\t\t\t<hkparam name="exitTime">0.000000</hkparam>
\t\t</hkobject>
\t</hkparam>
\t<hkparam name="initiateInterval">
\t\t<hkobject>
\t\t\t<hkparam name="enterEventId">-1</hkparam>
\t\t\t<hkparam name="exitEventId">-1</hkparam>
\t\t\t<hkparam name="enterTime">0.000000</hkparam>
\t\t\t<hkparam name="exitTime">0.000000</hkparam>
\t\t</hkobject>
\t</hkparam>
\t<hkparam name="transition">{effect}</hkparam>
\t<hkparam name="condition">null</hkparam>
\t<hkparam name="eventId">{event_id}</hkparam>
\t<hkparam name="toStateId">{to_state}</hkparam>
\t<hkparam name="fromNestedStateId">0</hkparam>
\t<hkparam name="toNestedStateId">0</hkparam>
\t<hkparam name="priority">0</hkparam>
\t<hkparam name="flags">{flags}</hkparam>
</hkobject>'''

TRIGGER_TMPL = '''<hkobject>
\t<hkparam name="localTime">{time:.6f}</hkparam>
\t<hkparam name="event">
\t\t<hkobject>
\t\t\t<hkparam name="id">{event_id}</hkparam>
\t\t\t<hkparam name="payload">null</hkparam>
\t\t</hkobject>
\t</hkparam>
\t<hkparam name="relativeToEndOfClip">{rel}</hkparam>
\t<hkparam name="acyclic">false</hkparam>
\t<hkparam name="isAnnotation">false</hkparam>
</hkobject>'''

VARIABLE_INFO_TMPL = '''<hkobject>
\t<hkparam name="role">
\t\t<hkobject>
\t\t\t<hkparam name="role">ROLE_DEFAULT</hkparam>
\t\t\t<hkparam name="flags">0</hkparam>
\t\t</hkobject>
\t</hkparam>
\t<hkparam name="type">VARIABLE_TYPE_{vtype}</hkparam>
</hkobject>'''

BINDING_TMPL = '''<hkobject>
\t<hkparam name="memberPath">{member}</hkparam>
\t<hkparam name="variableIndex">{var_index}</hkparam>
\t<hkparam name="bitIndex">-1</hkparam>
\t<hkparam name="bindingType">BINDING_TYPE_VARIABLE</hkparam>
</hkobject>'''

_EVENT_PROP_TMPL = ('<hkobject>\n'
                    '\t<hkparam name="id">{eid}</hkparam>\n'
                    '\t<hkparam name="payload">null</hkparam>\n'
                    '</hkobject>')

_EXPRESSION_TMPL = ('<hkobject>\n'
                    '\t<hkparam name="expression">{expr}</hkparam>\n'
                    '\t<hkparam name="assignmentVariableIndex">-1</hkparam>\n'
                    '\t<hkparam name="assignmentEventIndex">-1</hkparam>\n'
                    '\t<hkparam name="eventMode">{mode}</hkparam>\n'
                    '</hkobject>')

#: State-local transition flags; wildcards add LOCAL_WILDCARD.
F_LOCAL = 'FLAG_DISABLE_CONDITION'
F_WILD = 'FLAG_IS_LOCAL_WILDCARD|FLAG_DISABLE_CONDITION'
#: Also bubbles out of nested machines (returnToDefault / deathStart).
F_GLOBAL = ('FLAG_IS_LOCAL_WILDCARD|FLAG_IS_GLOBAL_WILDCARD|'
            'FLAG_DISABLE_CONDITION')


# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------

class GraphBuilder:
    """Adds behavior-graph nodes to one packfile, resolving names to indices.

    Holds the three tables every node needs — the packfile, the event-name
    index (`eid`) and the variable-name index (`vidx`) — so callers pass
    names rather than indices.  `blend_fx` is the shared 0.3s transition
    effect every state transition references.
    """

    def __init__(self, events, variables, first_id=80):
        """Build the index tables and the shared blend effect."""
        self.pf = HkxPackfile(first_id=first_id)
        self.eid = {n: i for i, n in enumerate(events)}
        self.vidx = {n: i for i, (n, _t, _iv) in enumerate(variables)}
        self.blend_fx = self._blend_effect()

    def add(self, kind):
        """Add a bare packfile object of `kind`."""
        return self.pf.add(kind)

    def render(self, top):
        """Serialize the packfile rooted at `top`."""
        return self.pf.render(top)

    def _blend_effect(self):
        """The shared BlendSmooth transition effect (vanilla default 0.3s)."""
        return self.blend_effect(
            'BlendSmooth', 0.3, 'SELF_TRANSITION_MODE_CONTINUE_IF_CYCLIC',
            'FLAG_IGNORE_FROM_WORLD_FROM_MODEL')

    def blend_effect(self, name, duration, self_mode, flags):
        """An hkbBlendingTransitionEffect of `duration` seconds."""
        fx = self.pf.add('hkbBlendingTransitionEffect')
        fx.param('variableBindingSet', 'null')
        fx.param('userData', 0)
        fx.param('name', name)
        fx.param('selfTransitionMode', self_mode)
        fx.param('eventMode', 'EVENT_MODE_DEFAULT')
        fx.param('duration', f'{duration:.6f}')
        fx.param('toGeneratorStartTimeFraction', '0.000000')
        fx.param('flags', flags)
        fx.param('endMode', 'END_MODE_NONE')
        fx.param('blendCurve', 'BLEND_CURVE_SMOOTH')
        return fx

    def event_driven_modifier(self, name, modifier_ref, activate, deactivate,
                              active_by_default):
        """An hkbEventDrivenModifier gating `modifier_ref` on two events."""
        edm = self.pf.add('hkbEventDrivenModifier')
        edm.param('variableBindingSet', 'null')
        edm.param('userData', 1)
        edm.param('name', name)
        edm.param('enable', True)
        edm.param('modifier', modifier_ref)
        edm.param('activateEventId', activate)
        edm.param('deactivateEventId', deactivate)
        edm.param('activeByDefault', active_by_default)
        return edm

    def bone_index_array(self, indices):
        """An hkbBoneIndexArray over `indices`."""
        arr = self.pf.add('hkbBoneIndexArray')
        arr.param('variableBindingSet', 'null')
        arr.param_array('boneIndices', indices)
        return arr

    def binding_set(self, pairs):
        """hkbVariableBindingSet from (memberPath, variable name) pairs."""
        b = self.pf.add('hkbVariableBindingSet')
        b.param_raw('bindings',
                    '\n'.join(BINDING_TMPL.format(member=m,
                                                  var_index=self.vidx[v])
                              for m, v in pairs),
                    numelements=len(pairs))
        b.param('indexOfBindingToEnable', -1)
        return b

    def clip(self, name, kf_path, looping, triggers_ref='null', rate=1.0,
             anim=None):
        """A clip generator playing `kf_path`.

        `anim` overrides the animation FILE stem: annotations live in the
        file, so a state whose events differ from the kf's other states
        (vocal idles) — or must have none (the ragdoll pose) — points at its
        own copy instead of the shared one.
        """
        clip = self.pf.add('hkbClipGenerator')
        clip.param('variableBindingSet', 'null')
        clip.param('userData', 0)
        clip.param('name', name)
        clip.param('animationName',
                   'Animations\\' + (anim or clip_state_name(kf_path))
                   + '.hkx')
        clip.param('triggers', triggers_ref)
        clip.param('cropStartAmountLocalTime', '0.000000')
        clip.param('cropEndAmountLocalTime', '0.000000')
        clip.param('startTime', '0.000000')
        clip.param('playbackSpeed', f'{rate:.6f}')
        clip.param('enforcedDuration', '0.000000')
        clip.param('userControlledTimeFraction', '0.000000')
        clip.param('animationBindingIndex', -1)
        clip.param('mode', 'MODE_LOOPING' if looping else 'MODE_SINGLE_PLAY')
        clip.param('flags', 0)
        return clip

    def trans_array(self, items):
        """Transition array from (event_id, to_state, flags) tuples."""
        arr = self.pf.add('hkbStateMachineTransitionInfoArray')
        arr.param_raw(
            'transitions',
            '\n'.join(TRANSITION_TMPL.format(effect=self.blend_fx.ref,
                                             event_id=e, to_state=st,
                                             flags=f)
                      for e, st, f in items),
            numelements=len(items))
        return arr

    def events_array(self, names):
        """hkbStateMachineEventPropertyArray naming `names`."""
        arr = self.pf.add('hkbStateMachineEventPropertyArray')
        arr.param_raw('events',
                      '\n'.join(_EVENT_PROP_TMPL.format(eid=self.eid[n])
                                for n in names),
                      numelements=len(names))
        return arr

    def expression_array(self, exprs):
        """hkbExpressionDataArray from (expression, eventMode) pairs."""
        arr = self.pf.add('hkbExpressionDataArray')
        arr.param_raw('expressionsData',
                      '\n'.join(_EXPRESSION_TMPL.format(expr=e, mode=m)
                                for e, m in exprs),
                      numelements=len(exprs))
        return arr

    def clip_triggers(self, times, end_event=None):
        """Clip trigger array for Oblivion 'Hit' text-key `times`.

        Emits weaponSwing/preHitFrame/HitFrame per hit time (the engine's
        attack-damage contract — without HitFrame an attack deals none) and
        an optional end-relative event.  'null' when there is nothing.
        """
        items = []
        for t in times:
            items.append(TRIGGER_TMPL.format(
                time=max(0.0, t - 0.3), event_id=self.eid['weaponSwing'],
                rel='false'))
            items.append(TRIGGER_TMPL.format(
                time=max(0.0, t - 0.1), event_id=self.eid['preHitFrame'],
                rel='false'))
            items.append(TRIGGER_TMPL.format(
                time=t, event_id=self.eid['HitFrame'], rel='false'))
        if end_event:
            items.append(TRIGGER_TMPL.format(
                time=0.0, event_id=self.eid[end_event], rel='true'))
        if not items:
            return 'null'
        trig = self.pf.add('hkbClipTriggerArray')
        trig.param_raw('triggers', '\n'.join(items), numelements=len(items))
        return trig.ref

    def state(self, state_id, name, generator_ref, transitions=None,
              exit_events=None, enter_events=None):
        """One stateInfo, building its own transition and event arrays.

        Referenced objects are added BEFORE the stateInfo: hkxcmd's parser
        rejects forward references.
        """
        trans_ref = (self.trans_array(transitions).ref if transitions
                     else 'null')
        return self._state_info(
            state_id, name, generator_ref, trans_ref,
            self.events_array(enter_events).ref if enter_events else 'null',
            self.events_array(exit_events).ref if exit_events else 'null')

    def root_state(self, state_id, name, generator_ref, enter_event=None,
                   transitions_ref='null'):
        """A root-machine state, taking an already-built transitions ref."""
        names = ([enter_event] if isinstance(enter_event, str)
                 else list(enter_event or ()))
        return self._state_info(
            state_id, name, generator_ref, transitions_ref,
            self.events_array(names).ref if names else 'null', 'null')

    def _state_info(self, state_id, name, generator_ref, trans_ref,
                    enter_ref, exit_ref):
        """The stateInfo object itself, from already-built refs."""
        st = self.pf.add('hkbStateMachineStateInfo')
        st.param('variableBindingSet', 'null')
        st.param_array('listeners', [])
        st.param('enterNotifyEvents', enter_ref)
        st.param('exitNotifyEvents', exit_ref)
        st.param('transitions', trans_ref)
        st.param('generator', generator_ref)
        st.param('name', name)
        st.param('stateId', state_id)
        st.param('probability', '1.000000')
        st.param('enable', True)
        return st

    def state_machine(self, name, states, start_id=0, wildcard_ref='null',
                      binding_ref='null', sync_var=None):
        """An hkbStateMachine over already-built `states`.

        `sync_var` names a graph variable whose value picks the start state
        on every activation (START_STATE_MODE_SYNC) instead of `start_id`.
        """
        m = self.pf.add('hkbStateMachine')
        m.param('variableBindingSet', binding_ref)
        m.param('userData', 0)
        m.param('name', name)
        m.param_raw('eventToSendWhenStateOrTransitionChanges', (
            '<hkobject>\n\t<hkparam name="id">-1</hkparam>\n'
            '\t<hkparam name="payload">null</hkparam>\n</hkobject>'))
        m.param('startStateChooser', 'null')
        m.param('startStateId', start_id)
        m.param('returnToPreviousStateEventId', -1)
        m.param('randomTransitionEventId', -1)
        m.param('transitionToNextHigherStateEventId', -1)
        m.param('transitionToNextLowerStateEventId', -1)
        m.param('syncVariableIndex', self.vidx[sync_var] if sync_var else -1)
        m.param('wrapAroundStateId', False)
        m.param('maxSimultaneousTransitions', 32)
        m.param('startStateMode', 'START_STATE_MODE_SYNC' if sync_var
                else 'START_STATE_MODE_DEFAULT')
        m.param('selfTransitionMode', 'SELF_TRANSITION_MODE_NO_TRANSITION')
        m.param_array('states', [s.ref for s in states])
        m.param('wildcardTransitions', wildcard_ref)
        return m

    def _blender(self, name, kids, bind_var, blend_param, flags):
        """A blender over (generator, anchor weight) children.

        A generator may be a ref or a zero-arg callable building one: each
        child is created immediately after its own generator, which is the
        packfile ORDER vanilla uses and what keeps object ids stable.
        """
        children = []
        for gen, anchor in kids:
            gen_ref = gen() if callable(gen) else gen
            ch = self.pf.add('hkbBlenderGeneratorChild')
            ch.param('variableBindingSet', 'null')
            ch.param('generator', gen_ref)
            ch.param('boneWeights', 'null')
            ch.param('weight', f'{anchor:.6f}')
            ch.param('worldFromModelWeight', '1.000000')
            children.append(ch)
        bind = (self.binding_set([('blendParameter', bind_var)]).ref if bind_var
                else 'null')
        blender = self.pf.add('hkbBlenderGenerator')
        blender.param('variableBindingSet', bind)
        blender.param('userData', 0)
        blender.param('name', name)
        blender.param('referencePoseWeightThreshold', '0.000000')
        blender.param('blendParameter', blend_param)
        blender.param('minCyclicBlendParameter', '0.000000')
        blender.param('maxCyclicBlendParameter', '1.000000')
        blender.param('indexOfSyncMasterChild', -1)
        blender.param('flags', flags)
        blender.param('subtractLastChild', False)
        blender.param_array('children', [c.ref for c in children])
        return blender

    def parametric_blend(self, name, plan):
        """SpeedSampled blend over a (name, kf, rate, anchor) plan."""
        kids = [(lambda c=cnm, k=kf, r=rate: self.clip(c, k, True,
                                                       rate=r).ref, anchor)
                for cnm, kf, rate, anchor in plan]
        return self._blender(name, kids, 'SpeedSampled', '1.000000', 17)

    def direction_blend(self, name, children, flags=49):
        """Blend on the engine-written `Direction`; children are speed blends.

        See: docs/commentary/asset_convert_creature.md#strafes-are-blends-not-states
        """
        return self._blender(name, children, 'Direction', '0.000000', flags)
