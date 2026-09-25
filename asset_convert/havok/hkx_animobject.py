"""Behaviour graphs for animated OBJECTS (activators/doors), not actors.

Why this exists
---------------
Oblivion animates a door/secret wall entirely inside the NIF: a
`NiControllerManager` holds named `NiControllerSequence`s ('Forward',
'Backward', 'Open', ...) and the script says `playgroup forward 1`.

Skyrim does NOT drive in-NIF sequences from script.  `ObjectReference` exposes
two different animation paths (verified in the SSE executable's Papyrus native
function table):

    PlayAnimation / PlayAnimationAndWait  -> behaviour-graph animation
    PlayGamebryoAnimation                 -> in-NIF NiControllerSequence

and `PlayAnimation` needs an *animation graph manager*, which only exists when
the NIF carries a `BSBehaviorGraphExtraData` (BGED) pointing at an hkx project.
Without one the call is accepted, returns immediately and does nothing — the
exe's own diagnostic string is "No reference selected or it has no animation
graph manager."  That is why CharacterGen's secret wall never physically
opened: the quest ran, the switch fired, PlayAnimation("Forward") logged no
Papyrus error, and the wall stayed shut.

The bridge between the two worlds is `BGSGamebryoSequenceGenerator` — a
Bethesda behaviour-graph node whose only job is to play a NIF sequence by name
(the exe describes its parameter literally as "Gamebryo Sequence name").

Vanilla template
----------------
`NocturnalsSecretDoor01` (Clutter\\BlackPool\\BlackPoolSecretDoor) is the
reference: its NIF has sequences AnimIdle01 / AnimPlay01 / AnimIdle02 and its
graph contains `GamebryoSequenceGenerator00/01/02` wrapped in a
`hkbStateMachine`, reached through a BGED that points at a *project* file
(`hkbProjectData` -> `Characters\\Character01.hkx` -> `Behaviors\\
Behavior00.hkx`).  So one animated object needs a small four-file tree:

    <model>.hkx                     project      (this is what BGED names)
    Characters\\Character01.hkx      character
    CharacterAssets\\Skeleton.hkx    1-bone skeleton
    Behaviors\\Behavior00.hkx        the state machine + Gamebryo generators

Each NIF sequence becomes one state; the state's name is also the event that
selects it, so `PlayAnimation("Forward")` sends `Forward` and the state machine
transitions to the generator bound to the 'Forward' NiControllerSequence.
"""

import os
import struct

from asset_convert.havok.hkx_xml import HkxPackfile, compile_hkx, convert_hkx_to_amd64

# The one-bone reference pose as vanilla SingleBoneSkeleton.hkx stores it:
# three 16-byte hkVector4 slots — translation, rotation, scale.  The rotation
# lanes are (1,0,0,0): Havok's BINARY quaternion is w-first, which is not the
# xyzw order the packfile XML uses.
_POSE_TRANS = (0.0, 0.0, 0.0, 0.0)
_POSE_QUAT = (1.0, 0.0, 0.0, 0.0)
_POSE_SCALE = (1.0, 1.0, 1.0, 1.0)
_POSE_BYTES = struct.pack('<12f', *(_POSE_TRANS + _POSE_QUAT + _POSE_SCALE))
# What hkxcmd actually emits for the identity pose: same, but the rotation slot
# is all zeros (no valid rotation -> nothing renders).
_POSE_BROKEN = struct.pack('<12f', *(_POSE_TRANS + (0.0, 0.0, 0.0, 0.0)
                                     + _POSE_SCALE))


def fix_identity_quat(hkx_path: str) -> bool:
    """Rewrite the skeleton's zero reference-pose quaternion to identity.

    hkxcmd compiles `(0 0 0)(0 0 0 1)(1 1 1)` — the exact text every shipped
    creature skeleton uses — into a reference pose whose ROTATION SLOT IS ALL
    ZEROS.  A zero quaternion is not a rotation, so the engine had no valid
    bind pose for the single bone and the whole object rendered nothing while
    the graph itself loaded fine (prisonSecretWall01, 2026-07-26).

    Patch the compiled WIN32 packfile in place, before the AMD64 step.  Matches
    on the full 48-byte pose block so it cannot hit unrelated data, and is a
    no-op if a future hkxcmd ever writes the quaternion correctly.
    """
    with open(hkx_path, 'rb') as f:
        data = f.read()
    if _POSE_BYTES in data:
        return False                      # already correct
    idx = data.find(_POSE_BROKEN)
    if idx < 0:
        return False                      # unrecognised layout; leave alone
    if data.find(_POSE_BROKEN, idx + 1) >= 0:
        return False                      # ambiguous; refuse to guess
    with open(hkx_path, 'wb') as f:
        f.write(data[:idx] + _POSE_BYTES + data[idx + len(_POSE_BYTES):])
    return True

# Wildcard transition: any state may be interrupted by any event.  Doors are
# re-activated mid-swing constantly, so a blocked transition reads in-game as
# "the door ignored me".  Vanilla's per-state arrays use plain
# FLAG_DISABLE_CONDITION (they are already reached from one specific state);
# ours live on the state machine's `wildcardTransitions`, which is what makes
# them global, so DISABLE_CONDITION is the only flag needed.
_TRANSITION_FLAGS = 'FLAG_DISABLE_CONDITION'

# hkbStateMachineTimeInterval, "no interval restriction" — vanilla writes this
# same all -1 / 0.0 struct for every transition in the template.
_INTERVAL = (
    '\t\t<hkobject>\n'
    '\t\t\t<hkparam name="enterEventId">-1</hkparam>\n'
    '\t\t\t<hkparam name="exitEventId">-1</hkparam>\n'
    '\t\t\t<hkparam name="enterTime">0.000000</hkparam>\n'
    '\t\t\t<hkparam name="exitTime">0.000000</hkparam>\n'
    '\t\t</hkobject>')

_BLEND_DURATION = 0.0   # objects snap between sequences; no cross-fade

# Vanilla's name for a self-playing ambient sequence.  The state machine starts
# on it instead of the do-nothing Rest state (see behavior_xml).
_AUTOPLAY_SEQUENCE = 'AutoPlay'
_AUTOLOOP_SEQUENCE = 'AutoLoop'   # where the real ambient motion lives

#: BGED of vanilla's shared self-playing graph (all 63 AutoPlay meshes); relative to meshes\, no SoundPlay event.
VANILLA_AUTOPLAY_BGED = 'GenericBehaviors\\Autoplay.hkx'

# Vanilla's fixed dummy bone name for single-bone animated objects
# (clutter\beehive\characterassets\SingleBoneSkeleton.hkx uses exactly this).
# The rig is a placeholder — the real motion lives in the NIF's
# NiControllerSequences — so the bone must NOT be named after anything in the
# NIF.  Naming it after the model made the engine bind the graph's identity
# bind pose onto the object and place it far from its authored position.
DUMMY_BONE = 'x_SingleBone'

#: Graph event a `SoundPlay.<SNDR>` NIF text key raises; every vanilla sounded object graph declares it.
SOUND_EVENT = 'SoundPlay'


def skeleton_xml(root_bone: str) -> str:
    """One-bone skeleton — an animated object has no rig of its own.

    The transforms live in the NIF sequences; Havok only needs a skeleton to
    exist so the character/behaviour pair is well-formed.
    """
    # Emission order matches vanilla SingleBoneSkeleton.hkx: resource
    # container, skeleton, animation container, root.
    pf = HkxPackfile(first_id=8)
    res = pf.add('hkMemoryResourceContainer')
    skel = pf.add('hkaSkeleton')
    anim = pf.add('hkaAnimationContainer')
    top = pf.add('hkRootLevelContainer')

    skel.param('name', root_bone)
    skel.param_array('parentIndices', [-1])
    skel.param_structs('bones', [[('name', root_bone), ('lockTranslation', 'false')]])
    # referencePose is emitted ONCE, here, before referenceFloats (same order as
    # hkx_skeleton.py).  Emitting an empty one first and appending the real one
    # later makes hkxcmd keep the EMPTY array: the skeleton then has 1 bone and
    # 0 poses, and binding a sequence indexes past the end -> null deref ->
    # CTD at `movdqu xmm2,[rax]` with rax=0 on cell load (prisonCellGate01,
    # 2026-07-26).  One entry per bone is mandatory.
    #
    # Text form is the same 3+4+3 `(t)(q xyzw)(s)` every shipped creature
    # skeleton uses (hkx_skeleton.build_skeleton_xml).  NOTE: hkxcmd writes the
    # quaternion lanes as ZERO for this identity value; a zero quaternion has no
    # valid rotation and the object renders nothing.  `fix_identity_quat` below
    # patches the compiled bytes to vanilla's (1,0,0,0).
    skel.param_raw('referencePose',
                   '(0.000000 0.000000 0.000000)'
                   '(0.000000 0.000000 0.000000 1.000000)'
                   '(1.000000 1.000000 1.000000)', numelements=1)
    skel.param_array('referenceFloats', [])
    skel.param_raw('floatSlots', '', numelements=0)
    skel.param_raw('localFrames', '', numelements=0)

    # Order per vanilla SingleBoneSkeleton.hkx: skeletons FIRST, and there is
    # no `attachmentNames` member on this class.
    anim.param_raw('skeletons', skel.ref, numelements=1)
    anim.param_array('animations', [])
    anim.param_array('bindings', [])
    anim.param_array('attachments', [])
    anim.param_array('skins', [])

    # Vanilla's resource container name is EMPTY (the namedVariant is what is
    # called "Resource Data").
    res.param('name', '')
    res.param_array('resourceHandles', [])
    res.param_array('children', [])

    top.param_structs('namedVariants', [
        [('name', 'Merged Animation Container'),
         ('className', 'hkaAnimationContainer'), ('variant', anim.ref)],
        [('name', 'Resource Data'),
         ('className', 'hkMemoryResourceContainer'), ('variant', res.ref)],
    ])
    return pf.render(top)


def _project_xml(character_file: str) -> str:
    """The file BGED points at: just a pointer to the character file."""
    pf = HkxPackfile(first_id=9)
    sd = pf.add('hkbProjectStringData')
    pd = pf.add('hkbProjectData')
    top = pf.add('hkRootLevelContainer')

    sd.param_array('animationFilenames', [])
    sd.param_array('behaviorFilenames', [])
    sd.param_strings('characterFilenames', [character_file])
    sd.param_array('eventNames', [])
    sd.param('animationPath', '')
    sd.param('behaviorPath', '')
    sd.param('characterPath', '')
    sd.param('fullPathToSource', '')

    pd.param('worldUpWS', '(0.000000 0.000000 1.000000 0.000000)')
    pd.param('stringData', sd.ref)
    pd.param('defaultEventMode', 'EVENT_MODE_IGNORE_FROM_GENERATOR')

    top.param_structs('namedVariants', [
        [('name', 'hkbProjectData'), ('className', 'hkbProjectData'),
         ('variant', pd.ref)]])
    return pf.render(top)


def _character_xml(name: str, behavior_file: str, skeleton_file: str) -> str:
    pf = HkxPackfile(first_id=27)
    mirror = pf.add('hkbMirroredSkeletonInfo')
    strings = pf.add('hkbCharacterStringData')
    values = pf.add('hkbVariableValueSet')
    cdata = pf.add('hkbCharacterData')
    top = pf.add('hkRootLevelContainer')

    mirror.param('mirrorAxis', '(1.000000 0.000000 0.000000 0.000000)')
    # Vanilla single-bone objects ship an EMPTY bonePairMap — there is no pair
    # to mirror on a 1-bone rig.
    mirror.param_array('bonePairMap', [])

    strings.param_array('deformableSkinNames', [])
    strings.param_array('rigidSkinNames', [])
    strings.param_array('animationNames', [])
    strings.param_array('animationFilenames', [])
    strings.param_array('characterPropertyNames', [])
    strings.param_array('retargetingSkeletonMapperFilenames', [])
    strings.param_array('lodNames', [])
    strings.param_array('mirroredSyncPointSubstringsA', [])
    strings.param_array('mirroredSyncPointSubstringsB', [])
    strings.param('name', name)
    strings.param('rigName', skeleton_file)
    strings.param('ragdollName', '')
    strings.param('behaviorFilename', behavior_file)

    values.param_array('wordVariableValues', [])
    values.param_array('quadVariableValues', [])
    values.param_array('variantVariableValues', [])

    # Field set/ORDER/values are those of vanilla `clutter\beehive\characters\
    # Character00.hkx` — the single-bone animated-object character.  This class
    # has NO `variableInitialValues` and NO `aiControlDriverInfo`: the value set
    # hangs off `characterPropertyValues`, the IK infos are NULL POINTERS (not
    # arrays), stringData/mirroredSkeletonInfo come AFTER them, and `scale` is
    # mandatory.  Getting this wrong made hkxcmd silently drop the
    # hkbVariableValueSet (visible as a missing class in the packfile's
    # __classnames__ table), and the object rendered nothing in-game.
    cdata.param_structs('characterControllerInfo', [
        [('capsuleHeight', '1.700000'), ('capsuleRadius', '0.400000'),
         ('collisionFilterInfo', 1), ('characterControllerCinfo', 'null')]])
    cdata.param('modelUpMS', '(0.000000 0.000000 1.000000 0.000000)')
    cdata.param('modelForwardMS', '(1.000000 0.000000 0.000000 0.000000)')
    cdata.param('modelRightMS', '(-0.000000 -1.000000 -0.000000 0.000000)')
    cdata.param_array('characterPropertyInfos', [])
    cdata.param_array('numBonesPerLod', [])
    cdata.param('characterPropertyValues', values.ref)
    cdata.param('footIkDriverInfo', 'null')
    cdata.param('handIkDriverInfo', 'null')
    cdata.param('stringData', strings.ref)
    cdata.param('mirroredSkeletonInfo', mirror.ref)
    cdata.param('scale', '1.000000')

    top.param_structs('namedVariants', [
        [('name', 'hkbCharacterData'), ('className', 'hkbCharacterData'),
         ('variant', cdata.ref)]])
    return pf.render(top)


def behavior_xml(graph_name: str, sequences: list) -> str:
    """State machine with one BGSGamebryoSequenceGenerator per NIF sequence.

    `sequences` are the NiControllerSequence names from the converted NIF.
    Each becomes a same-named event, so PlayAnimation("<seq>") selects it;
    SOUND_EVENT follows them so the NIF's sound keys reach the engine.
    """
    pf = HkxPackfile(first_id=100)

    events = list(sequences) + [SOUND_EVENT]
    eid = {n: i for i, n in enumerate(events)}

    # Field set/order/values copied from the vanilla template's
    # "BlendingTransitionEffectGB" (flags is the integer 0, NOT a FLAG_* name).
    fx = pf.add('hkbBlendingTransitionEffect')
    fx.param('variableBindingSet', 'null')
    fx.param('userData', 0)
    fx.param('name', 'BlendingTransitionEffectGB')
    fx.param('selfTransitionMode',
             'SELF_TRANSITION_MODE_CONTINUE_IF_CYCLIC_BLEND_IF_ACYCLIC')
    fx.param('eventMode', 'EVENT_MODE_DEFAULT')
    fx.param('duration', f'{_BLEND_DURATION:.6f}')
    fx.param('toGeneratorStartTimeFraction', '0.000000')
    fx.param('flags', 0)
    fx.param('endMode', 'END_MODE_NONE')
    fx.param('blendCurve', 'BLEND_CURVE_SMOOTH')

    def _transitions(exclude_state=None):
        """Per-state array: every sequence event reachable from this state.

        Transitions must live ON THE STATE, not only in the machine's
        `wildcardTransitions` — vanilla's Gamebryo state machine sets
        wildcardTransitions=null and gives each state its own array (State00
        carries event 0 -> state 4).  With a null array the state is a DEAD
        END: once the machine started in Rest, no event could leave it and the
        wall stopped opening from both the quest and console `activate`.
        """
        rows = [(eid[s], j) for j, s in enumerate(sequences)
                if j != exclude_state]
        if not rows and exclude_state is not None:
            # A SINGLE-sequence object (IDCrumbleWall01's only sequence is
            # `Unequip`) has no "other" sequence to reach, so the exclusion
            # empties the array and the state ships transitions=null -- the
            # exact dead end this docstring warns about.  The exclusion only
            # exists to stop a repeated event restarting the sequence mid-play;
            # a dead end is strictly worse, because the object can then never be
            # re-played at all (OnReset and every repeat activation were inert).
            # Keep the self-transition in that case.
            rows = [(eid[sequences[exclude_state]], exclude_state)]
        if not rows:
            return 'null'
        arr = pf.add('hkbStateMachineTransitionInfoArray')
        arr.param_raw('transitions', '\n'.join(
            '<hkobject>\n'
            f'\t<hkparam name="triggerInterval">\n{_INTERVAL}\n\t</hkparam>\n'
            f'\t<hkparam name="initiateInterval">\n{_INTERVAL}\n\t</hkparam>\n'
            f'\t<hkparam name="transition">{fx.ref}</hkparam>\n'
            '\t<hkparam name="condition">null</hkparam>\n'
            f'\t<hkparam name="eventId">{ev}</hkparam>\n'
            f'\t<hkparam name="toStateId">{to}</hkparam>\n'
            '\t<hkparam name="fromNestedStateId">0</hkparam>\n'
            '\t<hkparam name="toNestedStateId">0</hkparam>\n'
            '\t<hkparam name="priority">0</hkparam>\n'
            f'\t<hkparam name="flags">{_TRANSITION_FLAGS}</hkparam>\n'
            '</hkobject>'
            for ev, to in rows), numelements=len(rows))
        return arr.ref

    states = []
    for i, seq in enumerate(sequences):
        gen = pf.add('BGSGamebryoSequenceGenerator')
        gen.param('variableBindingSet', 'null')
        gen.param('userData', 0)
        gen.param('name', f'GamebryoSequenceGenerator{i:02d}')
        # Exactly these three params, in this order — matches vanilla
        # BlackPoolSecretDoor Behavior00.hkx.  The class also declares
        # bLooping/bDelayedActivate/fTime/events, but they are
        # SERIALIZE_IGNORED and must NOT be emitted.
        # The whole point: name the NIF's NiControllerSequence.
        gen.param('pSequence', seq)
        gen.param('eBlendModeFunction', 'BMF_NONE')
        gen.param('fPercent', '1.000000')

        st = pf.add('hkbStateMachineStateInfo')
        st.param('variableBindingSet', 'null')
        st.param_array('listeners', [])
        st.param('enterNotifyEvents', 'null')
        st.param('exitNotifyEvents', 'null')
        # Reach every OTHER sequence from here (a self-transition would restart
        # the sequence mid-play on a repeated event).
        st.param('transitions', _transitions(exclude_state=i))
        st.param('generator', gen.ref)
        st.param('name', seq)
        st.param('stateId', i)
        st.param('probability', '1.000000')
        st.param('enable', 'true')
        states.append(st)

    # Rest state — the state machine STARTS here and plays nothing.
    #
    # Vanilla starts on an idle: BlackPoolSecretDoor's startStateId is 3 =
    # AnimIdle01, and the motion (AnimPlay01) is only ever reached by event.
    # Oblivion sources have no idle sequence — a converted wall has just
    # Forward/Backward — so starting on state 0 made the engine play the OPEN
    # animation the instant the object loaded: the secret wall swung open by
    # itself instead of waiting for the CharacterGen switch.
    #
    # A generator whose pSequence names nothing holds the NIF's authored rest
    # pose, which is exactly the "closed" state.  It is the LAST state so the
    # event -> stateId mapping of the real sequences is untouched.
    rest_gen = pf.add('BGSGamebryoSequenceGenerator')
    rest_gen.param('variableBindingSet', 'null')
    rest_gen.param('userData', 0)
    rest_gen.param('name', 'GamebryoSequenceGeneratorRest')
    rest_gen.param('pSequence', '')
    rest_gen.param('eBlendModeFunction', 'BMF_NONE')
    rest_gen.param('fPercent', '1.000000')

    rest_id = len(sequences)
    rest = pf.add('hkbStateMachineStateInfo')
    rest.param('variableBindingSet', 'null')
    rest.param_array('listeners', [])
    rest.param('enterNotifyEvents', 'null')
    rest.param('exitNotifyEvents', 'null')
    # MUST be able to reach every sequence — this is the start state.
    rest.param('transitions', _transitions())
    rest.param('generator', rest_gen.ref)
    rest.param('name', 'Rest')
    rest.param('stateId', rest_id)
    rest.param('probability', '1.000000')
    rest.param('enable', 'true')
    states.append(rest)

    # Global wildcard transitions: event <seq> -> state <seq>, from anywhere.
    # trigger/initiateInterval are NESTED hkobjects (hkbStateMachineTimeInterval),
    # not tuple literals — param_structs renders values inline and cannot express
    # that, so the body is built by hand to match the vanilla template exactly.
    trans = pf.add('hkbStateMachineTransitionInfoArray')
    trans.param_raw('transitions', '\n'.join(
        '<hkobject>\n'
        f'\t<hkparam name="triggerInterval">\n{_INTERVAL}\n\t</hkparam>\n'
        f'\t<hkparam name="initiateInterval">\n{_INTERVAL}\n\t</hkparam>\n'
        f'\t<hkparam name="transition">{fx.ref}</hkparam>\n'
        '\t<hkparam name="condition">null</hkparam>\n'
        f'\t<hkparam name="eventId">{eid[seq]}</hkparam>\n'
        f'\t<hkparam name="toStateId">{i}</hkparam>\n'
        '\t<hkparam name="fromNestedStateId">0</hkparam>\n'
        '\t<hkparam name="toNestedStateId">0</hkparam>\n'
        '\t<hkparam name="priority">0</hkparam>\n'
        f'\t<hkparam name="flags">{_TRANSITION_FLAGS}</hkparam>\n'
        '</hkobject>'
        for i, seq in enumerate(sequences)), numelements=len(sequences))

    sm = pf.add('hkbStateMachine')
    sm.param('variableBindingSet', 'null')
    sm.param('userData', 0)
    sm.param('name', f'{graph_name}SM')
    # Inline struct, NOT a pointer — a bare 'null' here fails to compile.
    sm.param_raw('eventToSendWhenStateOrTransitionChanges', (
        '<hkobject>\n\t<hkparam name="id">-1</hkparam>\n'
        '\t<hkparam name="payload">null</hkparam>\n</hkobject>'))
    sm.param('startStateChooser', 'null')
    # Start on the Rest state, never on a motion sequence (see above) -- UNLESS
    # the mesh carries an ambient sequence.  AutoPlay/AutoLoop is vanilla's
    # name for animation that plays by itself with no script behind it, so
    # for those the graph MUST start on it or the effect sits frozen on its
    # first frame -- which is precisely the Rest-state behaviour the doors
    # need.  A mesh mixing ambient and script-driven sequences reaches this
    # generated graph (the shared vanilla one has no states for the scripted
    # names); AutoLoop is the authored loop and AutoPlay only its CLAMP intro
    # (nif_converter._autoplay_ambient_sequences), so start on AutoLoop when
    # both exist -- this graph has no End -> AutoLoop hand-off.
    _autoplay_id = next(
        (i for i, s in enumerate(sequences) if s == _AUTOLOOP_SEQUENCE),
        next((i for i, s in enumerate(sequences) if s == _AUTOPLAY_SEQUENCE),
             None))
    sm.param('startStateId',
             rest_id if _autoplay_id is None else _autoplay_id)
    sm.param('returnToPreviousStateEventId', -1)
    sm.param('randomTransitionEventId', -1)
    sm.param('transitionToNextHigherStateEventId', -1)
    sm.param('transitionToNextLowerStateEventId', -1)
    sm.param('syncVariableIndex', -1)
    sm.param('wrapAroundStateId', 'false')
    sm.param('maxSimultaneousTransitions', 32)
    sm.param('startStateMode', 'START_STATE_MODE_DEFAULT')
    # Vanilla's shared AutoPlay graph (GenericBehaviors\Autoplay.hkx) decodes
    # to selfTransitionMode=0, so this stays NO_TRANSITION.  Looping is the
    # SEQUENCE's own cycle type, not the state machine's: an ambient sequence
    # loops because it is CYCLE_LOOP, and FORCE_TRANSITION_TO_START_STATE
    # (tried 2026-08-18) did not make a CLAMP sequence loop.
    sm.param('selfTransitionMode', 'SELF_TRANSITION_MODE_NO_TRANSITION')
    sm.params.append(('states', ' '.join(s.ref for s in states),
                      f'array:{len(states)}'))
    sm.param('wildcardTransitions', trans.ref)

    strings = pf.add('hkbBehaviorGraphStringData')
    strings.param_strings('eventNames', events)
    strings.param_array('attributeNames', [])
    strings.param_array('variableNames', [])
    strings.param_array('characterPropertyNames', [])

    values = pf.add('hkbVariableValueSet')
    values.param_array('wordVariableValues', [])
    values.param_array('quadVariableValues', [])
    values.param_array('variantVariableValues', [])

    gdata = pf.add('hkbBehaviorGraphData')
    gdata.param_array('attributeDefaults', [])
    gdata.param_array('variableInfos', [])
    gdata.param_structs('characterPropertyInfos', [])
    gdata.param_structs('eventInfos', [[('flags', 0)] for _ in events])
    # hkxcmd's class definition orders these between eventInfos and
    # variableInitialValues; omitting them fails the compile silently.
    gdata.param_array('wordMinVariableValues', [])
    gdata.param_array('wordMaxVariableValues', [])
    gdata.param('variableInitialValues', values.ref)
    gdata.param('stringData', strings.ref)

    graph = pf.add('hkbBehaviorGraph')
    graph.param('variableBindingSet', 'null')
    graph.param('userData', 0)
    graph.param('name', graph_name)
    graph.param('variableMode', 'VARIABLE_MODE_DISCARD_WHEN_INACTIVE')
    graph.param('rootGenerator', sm.ref)
    graph.param('data', gdata.ref)

    top = pf.add('hkRootLevelContainer')
    top.param_structs('namedVariants', [
        [('name', graph_name), ('className', 'hkbBehaviorGraph'),
         ('variant', graph.ref)]])
    return pf.render(top)


def generate_animobject_project(out_root: str, model_rel: str,
                                sequences: list) -> str:
    """Write the 4-file hkx tree for one animated object.

    out_root:  output meshes root (…/output/<plugin>/meshes)
    model_rel: NIF path relative to that root, e.g.
               'tes4/dungeons/chargen/prisonsecretwall01.nif'
    sequences: NiControllerSequence names in the converted NIF.

    Returns the BGED value (data-relative path of the project hkx, backslashed)
    or '' when there is nothing to animate.
    """
    if not sequences:
        return ''

    # Ambient meshes use VANILLA'S OWN shared AutoPlay graph instead of a
    # generated per-mesh project.
    #
    # All 63 self-playing vanilla meshes (atronach skins, dragon-priest mist,
    # steam vents, the camera-attach weather effects) store
    # 'GenericBehaviors\\Autoplay.hkx' in their BGED; none ships a project of
    # its own.  Its four files are present in vanilla SSE, so pointing at it
    # ships nothing extra.
    #
    # Vanilla proves this graph drives SKINNED bone animation off a
    # single-bone rig, which is what an arena spectator needs:
    # effects\\trailerfx\\tfxdsflight.nif is non-actor, skinned over 17
    # bones, animates 13 NiTransformControllers, and runs on this graph.
    #
    # Read out of the live engine on a converted arena spectator
    # (2026-08-18): the graph binds, AutoplayState plays 'AutoPlay' to its
    # end, hands off to AutoLoopState, and 'AutoLoop' runs from there.
    #
    # Scoped to meshes whose sequences are ONLY ambient.  Anything a script
    # drives by name (Forward/Backward/SpecialIdle) keeps its generated
    # project: the shared graph has no state for those events, so
    # PlayAnimation() would have nothing to transition to.
    if all(seq in (_AUTOPLAY_SEQUENCE, _AUTOLOOP_SEQUENCE) for seq in sequences):
        return VANILLA_AUTOPLAY_BGED

    rel_dir = os.path.dirname(model_rel).replace('\\', '/')
    stem = os.path.splitext(os.path.basename(model_rel))[0]
    # Sibling folder per model, so two animated NIFs in one directory never
    # collide on Characters\Character01.hkx.
    proj_dir = os.path.join(out_root, *rel_dir.split('/'), stem + '_behavior')

    char_rel = os.path.join('Characters', 'Character01.hkx')
    behv_rel = os.path.join('Behaviors', 'Behavior00.hkx')
    skel_rel = os.path.join('CharacterAssets', 'Skeleton.hkx')

    targets = [
        # The bone is a placeholder, NOT a node in the NIF — always the vanilla
        # dummy name (see DUMMY_BONE).
        (os.path.join(proj_dir, skel_rel), skeleton_xml(DUMMY_BONE), True),
        (os.path.join(proj_dir, behv_rel), behavior_xml(stem, sequences), False),
        (os.path.join(proj_dir, char_rel),
         _character_xml(stem, behv_rel, skel_rel), False),
        (os.path.join(proj_dir, stem + '.hkx'), _project_xml(char_rel), False),
    ]
    for path, xml, is_skeleton in targets:
        xml_path = path + '.xml'
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(xml_path, 'w', encoding='ascii', errors='replace',
                  newline='\n') as f:
            f.write(xml)
        compile_hkx(xml_path, path)
        os.remove(xml_path)
        if is_skeleton:
            # hkxcmd writes a ZERO reference-pose quaternion; must be repaired
            # while the file is still WIN32 (the AMD64 output is not readable
            # back by hkxcmd, and the pose offset shifts once converted).
            fix_identity_quat(path)
        # SSE only loads 64-bit packfiles; must be the last step per hkx_xml.
        convert_hkx_to_amd64(path)

    # BGED is relative to meshes\, NOT data\ — the engine prepends "Meshes\%s"
    # itself.  A leading 'meshes\' here resolves to Meshes\meshes\... , the
    # project is never found, the object gets no animation graph and IS NEVER
    # RENDERED — while NifSkope, which never loads the hkx, shows it animating
    # perfectly.  Vanilla agrees: NocturnalsSecretDoor01 stores
    # 'Clutter\BlackPool\BlackPoolSecretDoor\NocturnalsSecretDoor01.hkx', and
    # our own working bow rig stores 'Weapons\Bow\BowProject.hkx'.
    bged = '/'.join([rel_dir, stem + '_behavior', stem + '.hkx'])
    return bged.replace('/', '\\')
