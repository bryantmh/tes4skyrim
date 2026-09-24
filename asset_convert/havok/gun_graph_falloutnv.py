"""The FO3/FNV gun state machines, as nodes for the SSE humanoid graphs.

A gun is hand type 13 (TESRuntime writes it for a WEAP carried in a gun
sidecar; the vanilla types stop at 12, the crossbow). Every machine here is
keyed on the graph variables the DLL sets beside it: `iGunClass` (index into
GUN_CLASSES), `iGunReload` (reload letter index), `iGunAttack` (index into
ATTACK_ACTIONS), `iGunClipSize` and `iGunAuto`. Firing goes through the
engine's own ranged-weapon events (arrowAttach, bowDrawn, BowRelease,
arrowRelease, attackStop), so the shot, the ammo and the projectile are the
engine's; TESRuntime counts the magazine into `iGunShots` (a shot adds one,
the reload clip's end resets it), so the graph only reads it.

Machines:
  ready(c)   idle/turn/locomotion while the gun is drawn (1HM_Readied slot)
  attack(c)  fire -> reload/done, upper body over the drawn locomotion
  loco(c)    direction blend of the class' walk/run clips (1HM_Locomotion)
  equip(c) / unequip(c)  the draw and sheathe clips (WeapEquip, 0_master)
See: docs/commentary/asset_convert_falloutnv.md#gun-graph
"""

import math

from asset_convert.havok.behavior_nodes import (F_LOCAL, GraphBuilder,
                                                TRIGGER_TMPL)
from asset_convert.havok.gun_vocabulary_falloutnv import (ATTACK_ACTIONS,
                                                          GUN_CLASSES,
                                                          RELOAD_LETTERS)

#: The hand type TESRuntime writes for a gun (vanilla stops at 12, crossbow).
GUN_HAND_TYPE = 13

#: Variables the DLL sets per equipped gun, plus the graph's own counters.
GUN_VARIABLES = ('iGunClass', 'iGunReload', 'iGunAttack', 'iGunClipSize',
                 'iGunAuto', 'iGunShots', 'iGunZoom')
#: The zoom selector TESRuntime writes (0 hip, 1 iron sights) for the attack clips.
ZOOM_VAR = 'iGunZoom'
#: REAL variable: the zoom blend TESRuntime ramps (0 hip .. 1 iron); every aim pose crossfades on it.
ZOOM_BLEND_VAR = 'fGunZoom'
#: hkbBlenderGenerator flags: sync + parametric, what every vanilla parametric blend carries.
PARAMETRIC_BLEND = 17
#: REAL variable: the automatic loop clip's playbackSpeed, weaponSpeedMult x loop duration.
LOOP_SPEED_VAR = 'fGunLoopSpeed'
#: The attack action that fires once per loop pass (automatic weapons).
LOOP_ACTION = 'attackloop'
#: Events the gun clips raise for their own machines.
GUN_EVENTS = ('TES4GunFireEnd', 'TES4GunReloadStart', 'TES4GunReloadEnd',
              'TES4GunAttackEnd', 'TES4GunReloadRequest', 'reloadStart',
              'TES4GunFire', 'TES4GunFireRelease')
#: The attack-button events TESRuntime sends (press, release); the engine's attack actions never reach a gun.
FIRE_EVENT = 'TES4GunFire'
FIRE_RELEASE = 'TES4GunFireRelease'
#: The clip trigger TESRuntime fires the gun on (an engine event name, one per round).
SHOT_EVENT = 'arrowRelease'
#: The event TESRuntime sends for the reload key.
RELOAD_REQUEST = 'TES4GunReloadRequest'
#: A reload only while the magazine is not full.
RELOAD_ALLOWED = 'iGunShots > 0'
#: The engine's own ammo-equipped event, which vanilla routes to the crossbow reload.
ENGINE_RELOAD = 'reloadStart'
#: The engine's crossbow attack event; a gun's root never takes it (TESRuntime owns the click).
CROSSBOW_ATTACK = 'crossbowAttackStart'
#: Root transitions that must keep the crossbow's hand type only (no gun bash).
BASH_EVENTS = ('bashStart', 'bashPowerStart')
#: The vanilla attack-window pair bounding a re-attack transition, and its flags.
ATTACK_WINDOW = ('AttackWinStart', 'AttackWinEnd')
F_WINDOW = 'FLAG_DISABLE_CONDITION|FLAG_USE_INITIATE_INTERVAL'
F_WINDOW_COND = 'FLAG_USE_INITIATE_INTERVAL'

#: BSiStateTaggingGenerator value/priority of the ranged drawn stance (bow).
RANGED_ISTATE, RANGED_PRIORITY = 8, 4

_TRANS_C_TMPL = '''<hkobject>
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
\t\t\t<hkparam name="enterEventId">{win_in}</hkparam>
\t\t\t<hkparam name="exitEventId">{win_out}</hkparam>
\t\t\t<hkparam name="enterTime">0.000000</hkparam>
\t\t\t<hkparam name="exitTime">0.000000</hkparam>
\t\t</hkobject>
\t</hkparam>
\t<hkparam name="transition">{effect}</hkparam>
\t<hkparam name="condition">{cond}</hkparam>
\t<hkparam name="eventId">{event_id}</hkparam>
\t<hkparam name="toStateId">{to_state}</hkparam>
\t<hkparam name="fromNestedStateId">0</hkparam>
\t<hkparam name="toNestedStateId">0</hkparam>
\t<hkparam name="priority">{priority}</hkparam>
\t<hkparam name="flags">{flags}</hkparam>
</hkobject>'''

_EXPR_ASSIGN_TMPL = ('<hkobject>\n'
                     '\t<hkparam name="expression">{expr}</hkparam>\n'
                     '\t<hkparam name="assignmentVariableIndex">{var}</hkparam>\n'
                     '\t<hkparam name="assignmentEventIndex">-1</hkparam>\n'
                     '\t<hkparam name="eventMode">EVENT_MODE_SEND_ONCE</hkparam>\n'
                     '</hkobject>')

# ---------------------------------------------------------------------------
# Clip lookup and the builder
# ---------------------------------------------------------------------------

def xesc(s: str) -> str:
    """Escape an expression for the packfile text."""
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def clip_speed(entry: dict) -> float:
    """Root-motion speed of a locomotion clip in game units per second."""
    m = entry.get('motion') or {}
    tr = m.get('translations')
    if not tr or entry['duration'] <= 0:
        return 0.0
    x, y, z = tr[-1]
    return math.sqrt(x * x + y * y + z * z) / entry['duration']


class GunClips:
    """The gun manifest's clips, looked up by (prefix, class, action...)."""

    def __init__(self, manifest: dict):
        """Index the manifest's clips by stem and by classification."""
        self.entries = {c['stem']: c for c in manifest['clips']}
        self.anim_dir = manifest['anim_dir']
        self.classes = manifest['classes']
        self.camera_bone = manifest.get('camera_bone')
        self.by_key = {}
        for stem, c in self.classes.items():
            if stem in self.entries:
                self.by_key[self._key(c['prefix'], c['cls'], c['action'],
                                      c['letter'], c['start'], c['iron'],
                                      c['pitch'])] = stem

    @staticmethod
    def _key(prefix, cls, action, letter, start, iron, pitch):
        """The normalized lookup key of one classification."""
        return (prefix or '', cls, action, letter or '', bool(start),
                bool(iron), pitch or '')

    def find(self, cls, action, prefix='', letter=None, start=False,
             pitch=None, iron=False):
        """The stem of one clip, or None."""
        return self.by_key.get(self._key(prefix, cls, action, letter, start,
                                         iron, pitch))

    def present_classes(self) -> list:
        """The classes with an attack clip, in GUN_CLASSES order."""
        have = {c['cls'] for c in self.classes.values()}
        return [c for c in GUN_CLASSES if c in have and self.attacks_of(c)]

    def attacks_of(self, cls) -> list:
        """The attack actions the class has a level clip for."""
        return [a for a in ATTACK_ACTIONS if self.find(cls, a)]

    def reloads_of(self, cls) -> list:
        """The reload letters the class has a clip for."""
        return [x for x in RELOAD_LETTERS if self.find(cls, 'reload', letter=x)]


class GunGraphBuilder(GraphBuilder):
    """GraphBuilder with the node kinds the humanoid graphs use.

    Every clip generator it creates is recorded in `generators` (name, stem,
    absolute trigger times) for the animationdata registration.
    """

    def __init__(self, events, variables, first_id, clips: GunClips):
        """Index the target file's event/variable names; ids from first_id."""
        super().__init__(events, [(n, 'INT32', 0) for n in variables],
                         first_id=first_id)
        self.clips = clips
        self.generators = []
        self._names = set()

    def _blend_effect(self):
        """The 0.3s blend, in the SSE enum spelling (HKX2E rejects the LE one)."""
        return self.blend_effect(
            'TES4GunBlend', 0.3,
            'SELF_TRANSITION_MODE_CONTINUE_IF_CYCLIC_BLEND_IF_ACYCLIC',
            'FLAG_IGNORE_FROM_WORLD_FROM_MODEL')

    def unique(self, name: str) -> str:
        """A generator name no other clip in the project uses."""
        base, n = name, 1
        while name in self._names:
            n += 1
            name = f'{base}_{n}'
        self._names.add(name)
        return name

    def trig(self, items) -> str:
        """hkbClipTriggerArray from (time, event, relative_to_end) items."""
        if not items:
            return 'null'
        body = '\n'.join(TRIGGER_TMPL.format(
            time=t, event_id=self.eid[e], rel='true' if rel else 'false')
            for t, e, rel in items)
        ta = self.add('hkbClipTriggerArray')
        ta.param_raw('triggers', body, numelements=len(items))
        return ta.ref

    def clip(self, name, stem, looping, triggers_ref='null', rate=1.0,
             anim=None, triggers=(), speed_var=None):
        """A clip generator playing manifest clip `stem`; `speed_var` binds
        its playbackSpeed to that graph variable. A clip that animates gun
        parts raises its own stem at frame 0, the weapon mesh's sequence.
        See: docs/commentary/asset_convert_falloutnv.md#gun-parts
        """
        entry = self.clips.entries[stem]
        name = self.unique(name)
        if entry.get('parts'):
            triggers = [*triggers, (0.0, stem, False)]
        if triggers:
            triggers_ref = self.trig(triggers)
        c = self.add('hkbClipGenerator')
        c.param('variableBindingSet',
                self.binding_set([('playbackSpeed', speed_var)]).ref
                if speed_var else 'null')
        c.param('userData', 0)
        c.param('name', name)
        c.param('animationName', entry['anim'])
        c.param('triggers', triggers_ref)
        c.param('cropStartAmountLocalTime', '0.000000')
        c.param('cropEndAmountLocalTime', '0.000000')
        c.param('startTime', '0.000000')
        c.param('playbackSpeed', f'{rate:.6f}')
        c.param('enforcedDuration', '0.000000')
        c.param('userControlledTimeFraction', '0.000000')
        c.param('animationBindingIndex', -1)
        c.param('mode', 'MODE_LOOPING' if looping else 'MODE_SINGLE_PLAY')
        c.param('flags', 0)
        dur = entry['duration']
        self.generators.append({
            'name': name, 'stem': stem, 'rate': rate,
            'events': [(max(0.0, dur - t) if rel else t, e)
                       for t, e, rel in triggers]})
        return c

    def msg(self, name, gens, var):
        """hkbManualSelectorGenerator bound to `var`."""
        bind = self.binding_set([('selectedGeneratorIndex', var)])
        m = self.add('hkbManualSelectorGenerator')
        m.param('variableBindingSet', bind.ref)
        m.param('userData', 0)
        m.param('name', name)
        m.param_array('generators', gens)
        m.param('selectedGeneratorIndex', 0)
        m.param('currentGeneratorIndex', 0)
        return m

    def bref(self, name, path):
        """hkbBehaviorReferenceGenerator to another behavior file."""
        b = self.add('hkbBehaviorReferenceGenerator')
        b.param('variableBindingSet', 'null')
        b.param('userData', 0)
        b.param('name', name)
        b.param('behaviorName', path)
        return b

    def istate(self, name, gen_ref, istate, prio):
        """BSiStateTaggingGenerator: the movement-type tag around `gen_ref`."""
        t = self.add('BSiStateTaggingGenerator')
        t.param('variableBindingSet', 'null')
        t.param('userData', 1)
        t.param('name', name)
        t.param('pDefaultGenerator', gen_ref)
        t.param('iStateToSetAs', istate)
        t.param('iPriority', prio)
        return t

    def eem_assign(self, name, items):
        """hkbEvaluateExpressionModifier assigning `expr` to `var` each frame."""
        arr = self.add('hkbExpressionDataArray')
        arr.param_raw('expressionsData', '\n'.join(
            _EXPR_ASSIGN_TMPL.format(expr=xesc(e), var=self.vidx[v])
            for e, v in items), numelements=len(items))
        eem = self.add('hkbEvaluateExpressionModifier')
        eem.param('variableBindingSet', 'null')
        eem.param('userData', 2)
        eem.param('name', name)
        eem.param('enable', True)
        eem.param('expressions', arr.ref)
        return eem

    def mod_gen(self, name, modifier_refs, gen_ref):
        """hkbModifierGenerator over a list of modifiers."""
        ml = self.add('hkbModifierList')
        ml.param('variableBindingSet', 'null')
        ml.param('userData', 1)
        ml.param('name', name + '_ML')
        ml.param('enable', True)
        ml.param_array('modifiers', modifier_refs)
        mg = self.add('hkbModifierGenerator')
        mg.param('variableBindingSet', 'null')
        mg.param('userData', 1)
        mg.param('name', name)
        mg.param('modifier', ml.ref)
        mg.param('generator', gen_ref)
        return mg

    def condition(self, expr):
        """An hkbExpressionCondition over `expr`; its ref."""
        c = self.add('hkbExpressionCondition')
        c.param('expression', xesc(expr))
        return c.ref

    def trans_c(self, items, instant=False):
        """Transition array from (event, to_state, cond_expr, priority, flags
        [, window]); `window` names the (open, close) events of an initiate
        interval. `instant` drops the blend effect: one-frame switches.
        See: docs/commentary/asset_convert_falloutnv.md#fire-rate
        """
        rows = []
        for event, to, cond, prio, flags, *window in items:
            win = window[0] if window else None
            rows.append(_TRANS_C_TMPL.format(
                effect='null' if instant else self.blend_fx.ref,
                cond=self.condition(cond) if cond else 'null',
                event_id=self.eid[event], to_state=to, priority=prio,
                win_in=self.eid[win[0]] if win else -1,
                win_out=self.eid[win[1]] if win else -1,
                flags=flags or (F_LOCAL if not cond else '0')))
        arr = self.add('hkbStateMachineTransitionInfoArray')
        arr.param_raw('transitions', '\n'.join(rows), numelements=len(rows))
        return arr.ref

    def state_c(self, state_id, name, gen_ref, items=(), instant=False):
        """A stateInfo with conditioned transitions (see trans_c)."""
        return self._state_info(state_id, name, gen_ref,
                                self.trans_c(items, instant) if items
                                else 'null', 'null', 'null')

    def sm(self, name, states, start=0, bind_var=None):
        """A state machine, optionally with startStateId bound to a variable."""
        bind = (self.binding_set([('startStateId', bind_var)]).ref
                if bind_var else 'null')
        return self.state_machine(name, states, start_id=start,
                                  binding_ref=bind)

    def body_blend(self, name, lower_ref, upper_ref, lower_bw, upper_bw):
        """Lower/upper body blend with the vanilla bone-weight arrays."""
        refs = []
        for gen, bw, wfm in ((lower_ref, lower_bw, 1.0),
                             (upper_ref, upper_bw, 0.0)):
            ch = self.add('hkbBlenderGeneratorChild')
            ch.param('variableBindingSet', 'null')
            ch.param('generator', gen)
            ch.param('boneWeights', bw)
            ch.param('weight', '1.000000')
            ch.param('worldFromModelWeight', f'{wfm:.6f}')
            refs.append(ch.ref)
        b = self.add('hkbBlenderGenerator')
        b.param('variableBindingSet', 'null')
        b.param('userData', 0)
        b.param('name', name)
        b.param('referencePoseWeightThreshold', '0.000000')
        b.param('blendParameter', '1.000000')
        b.param('minCyclicBlendParameter', '0.000000')
        b.param('maxCyclicBlendParameter', '1.000000')
        b.param('indexOfSyncMasterChild', -1)
        b.param('flags', 0)
        b.param('subtractLastChild', False)
        b.param_array('children', refs)
        return b

    def pitch_blend(self, name, stems, triggers=(), looping=True,
                    speed_var=None):
        """Blend of (down, level, up) clips on the engine's AimPitchCurrent.

        A missing level clip leaves down/up whose midpoint is the level pose.
        """
        down, level, up = stems
        kids = []
        for stem, anchor in ((down, -1.2), (level, 0.0), (up, 1.2)):
            if stem:
                kids.append((lambda s=stem, a=anchor: self.clip(
                    f'{name}_{s}', s, looping, triggers=triggers,
                    speed_var=speed_var).ref, anchor))
        if len(kids) == 1:
            return self.clip(name, next(s for s in stems if s), looping,
                             triggers=triggers, speed_var=speed_var)
        return self._blender(name, kids, 'AimPitchCurrent', '0.000000', 17)


# ---------------------------------------------------------------------------
# Machines
# ---------------------------------------------------------------------------

def _first(clips: GunClips, cls, actions, prefix=''):
    """The first of `actions` the class has a clip for, or None."""
    for a in actions:
        s = clips.find(cls, a, prefix)
        if s:
            return s
    return None


def pose_stems(clips: GunClips, cls, sneak=False, iron=False):
    """(down, level, up) aim stems of a class; sneak falls back to standing,
    and None when the iron-sight set is asked for and absent."""
    for prefix in (('sneak', '') if sneak else ('',)):
        level = (clips.find(cls, 'aim', prefix, iron=iron)
                 or (None if iron else clips.find(cls, 'idle', prefix)))
        down = clips.find(cls, 'aim', prefix, pitch='down', iron=iron)
        up = clips.find(cls, 'aim', prefix, pitch='up', iron=iron)
        if level or (down and up):
            return down, level, up
    return None


def _stance(gb: GunGraphBuilder, cls, name, sneak_switch, triggers, iron):
    """The aim pose (pitch blend) with the sneak variant when present."""
    hip = pose_stems(gb.clips, cls, iron=iron)
    stand = gb.pitch_blend(f'{name}_Stand', hip, triggers)
    sneak = pose_stems(gb.clips, cls, sneak=True, iron=iron)
    if not sneak_switch or sneak == hip:
        return stand
    crouch = gb.pitch_blend(f'{name}_Sneak', sneak, triggers)
    return gb.sm(f'{name}_SneakSwitch', [
        gb.state_c(0, f'{name}_Standing', stand.ref,
                   [('SneakStart', 1, None, 0, None)]),
        gb.state_c(1, f'{name}_Sneaking', crouch.ref,
                   [('SneakStop', 0, None, 0, None)])],
        bind_var='iIsInSneak')


def pose_gen(gb: GunGraphBuilder, cls, name, sneak_switch=True,
             triggers=()):
    """The drawn aim pose, selecting the class' iron-sight pose on the zoom
    variable when the class has one.
    See: docs/commentary/tes_runtime_guns.md#zoom
    """
    hip = _stance(gb, cls, name, sneak_switch, triggers, False)
    if pose_stems(gb.clips, cls, iron=True) is None:
        return hip
    iron = _stance(gb, cls, f'{name}_IS', sneak_switch, triggers, True)
    return gb._blender(f'{name}_ZoomBlend', [(hip.ref, 0.0), (iron.ref, 1.0)],
                       ZOOM_BLEND_VAR, '0.000000', PARAMETRIC_BLEND)


def moving_gen(gb: GunGraphBuilder, cls, lower_bw, upper_bw):
    """1HM_Locomotion while moving and, on the zoom variable, the class'
    iron-sight aim pose over its lower body (FNV overlays the pose).
    See: docs/commentary/tes_runtime_guns.md#zoom
    """
    loco = gb.bref(f'TES4Gun_{cls}_LocoBFR', 'Behaviors\\1HM_Locomotion.hkx')
    if pose_stems(gb.clips, cls, iron=True) is None:
        return loco
    legs = gb.bref(f'TES4Gun_{cls}_LocoISBFR', 'Behaviors\\1HM_Locomotion.hkx')
    torso = _stance(gb, cls, f'TES4Gun_{cls}_Moving_IS', True, (), True)
    iron = gb.body_blend(f'TES4Gun_{cls}_MovingISBlend', legs.ref, torso.ref,
                         lower_bw, upper_bw)
    return gb._blender(f'TES4Gun_{cls}_Moving_ZoomBlend', [(loco.ref, 0.0), (iron.ref, 1.0)],
                       ZOOM_BLEND_VAR, '0.000000', PARAMETRIC_BLEND)


def ready_machine(gb: GunGraphBuilder, cls, lower_bw, upper_bw):
    """Drawn gun: idle/turn while standing, 1HM_Locomotion while moving.

    A turn clip is a lower-body overlay (FNV plays it over the aim pose by
    priority), so it drives the lower body under the aim pose's upper body.
    See: docs/commentary/asset_convert_falloutnv.md#turn-clips-are-overlays
    """
    idle = pose_gen(gb, cls, f'TES4Gun_{cls}_Idle')
    turn = {}
    for side in ('right', 'left'):
        stem = gb.clips.find(cls, f'turn{side}')
        if not stem:
            turn[side] = idle.ref
            continue
        legs = gb.clip(f'TES4Gun_{cls}_Turn{side}', stem, True)
        torso = pose_gen(gb, cls, f'TES4Gun_{cls}_Turn{side}Pose')
        turn[side] = gb.body_blend(f'TES4Gun_{cls}_Turn{side}Blend', legs.ref,
                                   torso.ref, lower_bw, upper_bw).ref
    standing = gb.sm(f'TES4Gun_{cls}_IdleTurn', [
        gb.state_c(0, 'TurnRight', turn['right'],
                   [('turnLeft', 2, None, 0, None), ('turnStop', 1, None, 0, None)]),
        gb.state_c(1, 'Idle', idle.ref,
                   [('turnLeft', 2, None, 0, None), ('turnRight', 0, None, 0, None)]),
        gb.state_c(2, 'TurnLeft', turn['left'],
                   [('turnRight', 0, None, 0, None), ('turnStop', 1, None, 0, None)])],
        start=1, bind_var='iSyncTurnState')
    moving = moving_gen(gb, cls, lower_bw, upper_bw)
    return gb.sm(f'TES4Gun_{cls}_Ready', [
        gb.state_c(0, 'Standing', standing.ref, [('moveStart', 1, None, 0, None)]),
        gb.state_c(1, 'Moving', moving.ref, [('moveStop', 0, None, 0, None)])],
        bind_var='iSyncIdleLocomotion')


def fire_triggers(entry: dict) -> list:
    """The shot trigger at the clip's fire frame (TESRuntime launches the
    round on it), and the attack window opening at FNV's `a:` key.
    See: docs/commentary/tes_runtime_guns.md#the-shot
    """
    t = (entry.get('hits') or [0.02])[0]
    nxt = entry.get('keys', {}).get('next', max(t, entry['duration'] - 0.034))
    return [(t, SHOT_EVENT, False),
            (nxt, ATTACK_WINDOW[0], False),
            (nxt, 'attackStop', False),
            (0.0, ATTACK_WINDOW[1], True),
            (0.0, 'TES4GunFireEnd', True)]


def _attack_blend(gb: GunGraphBuilder, cls, action, name, iron):
    """One attack's pitch blend at the gun's speed, every clip carrying the
    shot triggers; the loop attack plays at the class' loop speed.
    See: docs/commentary/asset_convert_falloutnv.md#automatic-fire-rate
    """
    level = gb.clips.find(cls, action, iron=iron)
    trig = fire_triggers(gb.clips.entries[level])
    stems = (gb.clips.find(cls, action, pitch='down', iron=iron), level,
             gb.clips.find(cls, action, pitch='up', iron=iron))
    speed = LOOP_SPEED_VAR if action == LOOP_ACTION else 'weaponSpeedMult'
    return gb.pitch_blend(name, stems, trig, looping=False, speed_var=speed)


def _attack_gen(gb: GunGraphBuilder, cls, action, tag):
    """One attack, hip or (on the zoom variable) FNV's own iron-sight fire
    clip when it ships one.
    See: docs/commentary/tes_runtime_guns.md#zoom
    """
    name = f'TES4Gun_{cls}_{action}{tag}'
    hip = _attack_blend(gb, cls, action, name, False)
    if not gb.clips.find(cls, action, iron=True):
        return hip
    iron = _attack_blend(gb, cls, action, f'{name}_IS', True)
    return gb.msg(f'{name}_ZoomMSG', [hip.ref, iron.ref], ZOOM_VAR)


def _fire_gen(gb: GunGraphBuilder, cls, letter):
    """The fire clip selector, under a per-frame loop-speed assignment when
    the class has a loop attack."""
    gen = fire_msg(gb, cls, letter)
    loop = gb.clips.find(cls, LOOP_ACTION)
    if not loop:
        return gen
    dur = gb.clips.entries[loop]['duration']
    eem = gb.eem_assign(f'TES4Gun_{cls}_LoopSpeed{letter}',
                        [(f'weaponSpeedMult * {dur:.5f}', LOOP_SPEED_VAR)])
    return gb.mod_gen(f'TES4Gun_{cls}_Fire{letter}', [eem.ref], gen.ref)


def fire_msg(gb: GunGraphBuilder, cls, tag):
    """One generator per ATTACK_ACTIONS index, selected by iGunAttack."""
    have = gb.clips.attacks_of(cls)
    default = _attack_gen(gb, cls, have[0], tag)
    gens = []
    for a in ATTACK_ACTIONS:
        gens.append(_attack_gen(gb, cls, a, tag).ref if a in have and a != have[0]
                    else default.ref)
    return gb.msg(f'TES4Gun_{cls}_Fire{tag}_MSG', gens, 'iGunAttack')


def _reload_chain(gb: GunGraphBuilder, cls, letter, prefix=''):
    """One reload: the `<letter>start` half chained into the main clip."""
    main = gb.clips.find(cls, 'reload', prefix, letter=letter)
    start = gb.clips.find(cls, 'reload', prefix, letter=letter, start=True)
    end = [(0.0, 'TES4GunReloadEnd', True)]
    main_clip = gb.clip(f'TES4Gun_{cls}_Reload{letter}{prefix}', main, False,
                        triggers=end)
    if not start:
        return main_clip
    start_clip = gb.clip(f'TES4Gun_{cls}_Reload{letter}Start{prefix}', start,
                         False, triggers=[(0.0, 'TES4GunReloadStart', True)])
    return gb.sm(f'TES4Gun_{cls}_Reload{letter}Chain{prefix}', [
        gb.state_c(0, 'Start', start_clip.ref,
                   [('TES4GunReloadStart', 1, None, 0, None)]),
        gb.state_c(1, 'Main', main_clip.ref)])


def _reload_gen(gb: GunGraphBuilder, cls, letter):
    """A reload with its sneak variant switched on iIsInSneak."""
    stand = _reload_chain(gb, cls, letter)
    if not gb.clips.find(cls, 'reload', 'sneak', letter=letter):
        return stand
    crouch = _reload_chain(gb, cls, letter, 'sneak')
    return gb.sm(f'TES4Gun_{cls}_Reload{letter}_SneakSwitch', [
        gb.state_c(0, 'Standing', stand.ref),
        gb.state_c(1, 'Sneaking', crouch.ref)], bind_var='iIsInSneak')


def reload_msg(gb: GunGraphBuilder, cls):
    """One generator per RELOAD_LETTERS index, selected by iGunReload.

    None for a class with no reload clip at all: the 2hh handle weapons
    (minigun, flamer, gatling laser) are belt-fed and author none.
    """
    have = gb.clips.reloads_of(cls)
    if not have:
        return None
    default = _reload_gen(gb, cls, have[0])
    gens = [(_reload_gen(gb, cls, x).ref if x in have and x != have[0]
             else default.ref) for x in RELOAD_LETTERS]
    return gb.msg(f'TES4Gun_{cls}_Reload_MSG', gens, 'iGunReload')


#: Reload when the magazine the WEAP declares is spent (0 = unknown, never).
RELOAD_COND = '(iGunClipSize > 0) && (iGunShots >= iGunClipSize)'


def fire_machine(gb: GunGraphBuilder, cls, reload_entry=False):
    """Fire (A/B alternate for automatics) -> reload or done; `reload_entry`
    starts the machine at Reload (Done without a reload clip).

    `iGunShots` is TESRuntime's; the transitions are instant since the fire
    clips end at the aim pose.
    See: docs/commentary/asset_convert_falloutnv.md#fire-transitions-are-instant
    """
    fire_a = _fire_gen(gb, cls, 'A')
    fire_b = _fire_gen(gb, cls, 'B')
    done = pose_gen(gb, cls, f'TES4Gun_{cls}_Done', sneak_switch=False,
                    triggers=[(0.02, 'attackStop', False),
                              (0.02, 'TES4GunAttackEnd', False)])
    reload_gen = reload_msg(gb, cls)

    def fire_trans(other):
        """A fire state's exits: reload (at the clip end or on the re-attack
        window), the other fire state, done.
        See: docs/commentary/asset_convert_falloutnv.md#reload-on-the-window
        """
        out = [(FIRE_EVENT, other, None, 3, F_WINDOW, ATTACK_WINDOW),
               ('TES4GunFireEnd', other, 'iGunAuto == 1', 1, None),
               ('TES4GunFireEnd', 3, None, 0, None),
               (FIRE_RELEASE, 3, 'iGunAuto == 1', 0, None)]
        if reload_gen is not None:
            out[:0] = [(RELOAD_REQUEST, 2, RELOAD_ALLOWED, 5, None),
                       (ENGINE_RELOAD, 2, RELOAD_ALLOWED, 5, None),
                       (FIRE_EVENT, 2, RELOAD_COND, 4,
                        F_WINDOW_COND, ATTACK_WINDOW),
                       ('TES4GunFireEnd', 2, RELOAD_COND, 2, None)]
        return out
    states = [gb.state_c(0, 'FireA', fire_a.ref, fire_trans(1), True),
              gb.state_c(1, 'FireB', fire_b.ref, fire_trans(0), True)]
    if reload_gen is not None:
        states.append(gb.state_c(2, 'Reload', reload_gen.ref,
                                 [('TES4GunReloadEnd', 3, None, 0, None)],
                                 True))
    again = [(FIRE_EVENT, 0, None, 0, None)]
    if reload_gen is not None:
        again.insert(0, (FIRE_EVENT, 2, RELOAD_COND, 4, None))
    states.append(gb.state_c(3, 'Done', done.ref, again))
    start = (2 if reload_gen is not None else 3) if reload_entry else 0
    return gb.sm(f'TES4Gun_{cls}_Fire{"Reload" if reload_entry else ""}Behavior',
                 states, start=start)


def attack_machine(gb: GunGraphBuilder, cls, lower_bw, upper_bw,
                   reload_entry=False):
    """The fire machine on the upper body over standing/moving lower body."""
    stand = pose_gen(gb, cls, f'TES4Gun_{cls}_AttackStand')
    loco = gb.bref(f'TES4Gun_{cls}_AttackLocoBFR',
                   'Behaviors\\1HM_Locomotion.hkx')
    lower = gb.sm(f'TES4Gun_{cls}_AttackLower', [
        gb.state_c(0, 'Standing', stand.ref, [('moveStart', 1, None, 0, None)]),
        gb.state_c(1, 'Moving', loco.ref, [('moveStop', 0, None, 0, None)])],
        bind_var='iSyncIdleLocomotion')
    return gb.body_blend(f'TES4Gun_{cls}_AttackBlend', lower.ref,
                         fire_machine(gb, cls, reload_entry).ref,
                         lower_bw, upper_bw)


def class_selector(gb: GunGraphBuilder, name, build):
    """MSG over GUN_CLASSES (iGunClass); absent classes reuse the first."""
    present = gb.clips.present_classes()
    built = {c: build(c).ref for c in present}
    first = built[present[0]]
    return gb.msg(name, [built.get(c, first) for c in GUN_CLASSES], 'iGunClass')


# ---------------------------------------------------------------------------
# Locomotion
# ---------------------------------------------------------------------------

_DIRS = (('forward', 0.0), ('right', 0.25), ('backward', 0.5), ('left', 0.75))


def _gait(gb: GunGraphBuilder, cls, direction, name):
    """Walk/run speed blend of one direction; None without any clip."""
    walk = gb.clips.find(cls, direction)
    run = gb.clips.find(cls, 'fast' + direction)
    plan = []
    for stem in (walk, run):
        if stem:
            spd = clip_speed(gb.clips.entries[stem])
            plan.append((f'{name}_{stem}', stem, 1.0, spd))
    if not plan:
        return None
    if len(plan) == 1 or plan[0][3] >= plan[1][3]:
        return gb.clip(name, plan[-1][1], True)
    return gb.parametric_blend(name, plan)


def loco_machine(gb: GunGraphBuilder, cls):
    """Direction blend of the class' walk/run clips, tagged as ranged stance."""
    kids = []
    for d, anchor in _DIRS:
        g = _gait(gb, cls, d, f'TES4Gun_{cls}_{d}')
        if g is not None:
            kids.append((g, anchor))
    blend = (gb.direction_blend(f'TES4Gun_{cls}_DirectionBlend',
                                [(g.ref, a) for g, a in kids])
             if len(kids) > 1 else kids[0][0])
    return gb.istate(f'TES4Gun_{cls}_iStateGen', blend.ref, RANGED_ISTATE,
                     RANGED_PRIORITY)


# ---------------------------------------------------------------------------
# Equip and sheathe
# ---------------------------------------------------------------------------

def equip_gen(gb: GunGraphBuilder, cls):
    """The draw clip: weaponDraw at its Attach key, the equip-out pair at end."""
    stem = gb.clips.find(cls, 'equip')
    entry = gb.clips.entries[stem]
    attach = entry.get('keys', {}).get('attach', 0.4 * entry['duration'])
    trig = [(0.0, 'BeginWeaponDraw', False), (attach, 'weaponDraw', False),
            (0.0, 'WeapEquip_Out', True), (0.0, 'WeapEquip_OutMoving', True)]
    return gb.clip(f'TES4Gun_{cls}_Equip', stem, False,
                   triggers=[t for t in trig if t[1] in gb.eid])


def unequip_gen(gb: GunGraphBuilder, cls):
    """The sheathe clip (holster when there is no unequip)."""
    stem = _first(gb.clips, cls, ('unequip', 'holster'))
    entry = gb.clips.entries[stem]
    detach = entry.get('keys', {}).get('detach', 0.5 * entry['duration'])
    trig = [(0.0, 'BeginWeaponSheathe', False), (detach, 'weaponSheathe', False),
            (0.0, 'Unequip_Out', True), (0.0, 'Unequip_OutMoving', True)]
    return gb.clip(f'TES4Gun_{cls}_Unequip', stem, False,
                   triggers=[t for t in trig if t[1] in gb.eid])
