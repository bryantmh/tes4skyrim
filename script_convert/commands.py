"""Command handlers: the TES4 commands a `COMMAND_ROWS` row cannot express.

A row in `constants.COMMAND_ROWS` covers every command whose conversion is
"resolve a receiver, convert a few arguments, register a property type, emit
one template".  What is left is real logic -- a command that branches on an
argument's VALUE, emits several lines, consults the cross-reference graph or
synthesises a helper.  Each of those is one `@command` handler here.

A handler is `(ctx, call) -> str`, registered by the TES4 command names it
converts.  Returning `None` means "not mine after all"; dispatch falls through
to the row table exactly as if no handler existed.

`Call` carries the invocation.  `_emit_function` took five positional
parameters and rebuilt the same derived values at the top of nearly every
branch -- the lowercased name, the first argument's source, the authored
argument text -- so those are properties of the CALL and live on it.
"""

from script_convert import resolve_name as _resolve_name
from script_convert.constants import (
    ACTOR_VALUE_MAP, ANIM_GROUP_EVENTS, ATTRIBUTE_STUB_VALUE, CASTABLE,
    GMST_TO_ACTOR_VALUE, param_types,
    PLACED_REF_SIGS, TES4_ATTRIBUTES, _ACTOR_VALUE_FUNCTIONS,
    _ACTOR_VALUE_READ_FUNCTIONS, TES4_ASSAULT_BOUNTY, TES4_MURDER_BOUNTY,
    TES4_STEAL_BOUNTY, _safe_property_name, papyrus_script_name,
)

#: TES4 command name (lowercase) -> handler `(ctx, call) -> str | None`.
REGISTRY: dict = {}


def command(*names: str):
    """Register a handler for one or more TES4 command names."""
    def bind(fn):
        for name in names:
            REGISTRY[name] = fn
        return fn
    return bind


def dispatch(ctx, call) -> str:
    """Run the handler for `call`, or return None if there is none.

    A handler may also return None to DECLINE -- `getfirstref` converts only
    the actor walk -- and dispatch then falls through to the row table exactly
    as if no handler existed.
    """
    handler = REGISTRY.get(call.name)
    return handler(ctx, call) if handler is not None else None



class Call:
    """A command invocation: name, receiver, parsed arguments."""

    __slots__ = ('name', 'raw_name', 'ref', 'args', 'extends', '_conv')

    def __init__(self, conv, ref, func_name: str, extends: str, args=()):
        #: The command name, LOWERCASE -- what handlers and rows key on.
        self.name = func_name.lower()
        #: The name as the author spelled it, for `;NE:`/`;TODO:` markers.
        self.raw_name = func_name
        #: The receiver as authored (`Foo` in `Foo.GetDead`), or None.
        self.ref = ref
        #: The parsed argument NODES -- the single source of the arguments.
        self.args = tuple(args)
        self.extends = extends
        self._conv = conv

    def __len__(self) -> int:
        return len(self.args)

    @property
    def src(self) -> str:
        """The whole argument list as the author wrote it."""
        return ', '.join(self._conv.arg_sources())

    def arg(self, n: int, default: str = '') -> str:
        """Argument `n` converted to Papyrus, cast to the parameter's type.

        The cast lives here rather than in the row renderer because a handler
        builds its own call text and would otherwise miss it: `IsInFaction`
        takes a Faction while an OBSE user function's `ref` parameter is
        declared `Form`, which Papyrus will not convert down implicitly.
        """
        text = self._conv.arg_expr(n, self.extends, default)
        want = param_types(self.name).get(n)
        if want and self._conv.type_of(text) in CASTABLE.get(want, ()):
            return self._conv._cast(text, want)
        return text

    def source(self, n: int, default: str = '') -> str:
        """Argument `n` as AUTHORED source text."""
        return self._conv.arg_src(n, default)

    def written(self) -> str:
        """The call as the author wrote it, receiver included."""
        head = f'{self.ref}.{self.raw_name}' if self.ref else self.raw_name
        return f'{head} {self.src}'.strip()


# ---------------------------------------------------------------------------
# Quests, stages and globals
# ---------------------------------------------------------------------------

@command('setstage', 'getstage', 'getstagedone')
def stage(ctx, call) -> str:
    """SetStage / GetStage / GetStageDone.

    TES4 spells the quest as the first argument and the stage as the second;
    Papyrus makes the quest the receiver.
    """
    parts = ctx.arg_srcs()
    quest_src = parts[0].strip() if parts else (call.ref or '')
    if not quest_src:
        return None
    prop = _safe_property_name(quest_src)
    # Never DOWNGRADE: the same quest is often reached both as a stage target
    # and as a cross-script variable owner (`Arena.SetStage 10` beside
    # `Arena.ChorrolMatch`), and the specific TES4_<script> type is what makes
    # the variable read compile.  Overwriting it with the base `Quest` failed
    # every such read ("field or property ChorrolMatch not found").
    if not _typed_already(ctx, prop):
        ctx.sc.property_refs[prop] = 'Quest'
    if call.name == 'setstage':
        return f'{prop}.SetStage({call.arg(1, "0") if len(parts) > 1 else 0})'
    # GetStageDone asks whether a specific stage has run; GetStage reads the
    # current stage number, and TES4 writes it with no stage operand.
    if len(parts) > 1:
        return f'{prop}.GetStageDone({call.arg(1, "0")})'
    return f'{prop}.GetStage()'


@command('startquest', 'stopquest', 'getquestrunning', 'completequest',
         'isquestcompleted')
def quest_state(ctx, call) -> str:
    """Quest lifecycle.

    StopQuest is `Stop()` -- a run-bit global was tried and REVERTED (see
    docs/commentary/script_convert.md); the fix is the Start() hoist and nothing
    more.
    """
    parts = ctx.arg_srcs()
    quest_src = parts[0].strip() if parts else (call.ref or '')
    if not quest_src:
        return None
    # This names the property directly rather than going through
    # `_convert_ref`, so it needs its own stale-name recovery: Oblivion's SE02
    # stage 15 reads `startQuest SE02FIN`, a name no record carries, while the
    # stage's SCRO binds the real quest SE02Conv.  Unrecovered it declared a
    # property bound to NOTHING, and the first use of an unbound property
    # ABORTS the fragment -- so the Shivering Isles post-quest dialogue quest
    # was never started.
    quest_src = ctx._scro_alias_for(quest_src) or quest_src
    prop = _safe_property_name(quest_src)
    # Keep an existing type: a TES4_XxxScript (which extends Quest) still
    # answers Start/Stop/IsRunning, and the cross-script variable reads that
    # need that type keep working.  Quest is enough when nothing is known --
    # the SCPT-derived name would be wrong here, since in TES5 the quest's
    # VMAD script is TES4_QF_<EditorID> rather than the SCPT name.
    if not _typed_already(ctx, prop):
        ctx.sc.property_refs[prop] = 'Quest'
    papyrus = {'startquest': 'Start', 'stopquest': 'Stop',
               'getquestrunning': 'IsRunning',
               'completequest': 'CompleteQuest',
               'isquestcompleted': 'IsCompleted'}[call.name]
    return f'{prop}.{papyrus}()'


@command('getglobalvalue', 'setglobalvalue')
def global_value(ctx, call) -> str:
    """OBSE GetGlobalValue / SetGlobalValue -- reach a global by NAME.

    Papyrus reaches a global through a GlobalVariable property, which the
    normal named-form path already builds.  Left unmapped the operand stayed a
    bare name and broke the enclosing expression ("unexpected name
    fbmwbmclawcost"), taking the werewolf script family down with it.
    """
    gname = call.source(0).strip()
    if not gname:
        return None
    safe = _safe_property_name(gname)
    ctx.sc.property_refs[safe] = 'GlobalVariable'
    if call.name == 'getglobalvalue':
        return ctx._global_read(safe)
    return f'{safe}.SetValue({call.arg(1) if len(call) > 1 else 0})'


@command('isplayable', 'isplayable2')
def is_playable(ctx, call) -> str:
    """OBSE IsPlayable -- SKSE64 Form.IsPlayable on the underlying base form."""
    if call.ref:
        target = ctx._convert_ref(call.ref, call.extends)
    elif len(call):
        target = call.arg(0)
    else:
        target = ctx._implicit_self(call.extends)
    return f'TES4SKSE.GetBaseForm({target}).IsPlayable()'


@command('getfirstref')
def get_first_ref(ctx, call) -> str:
    """GetFirstRef <formtype> -- open OBSE's walk over loaded references.

    Only the ACTOR walk (TES4 form type 69) converts: FindRandomActorFromRef is
    the one primitive of this shape.  A walk over any other form type
    neutralises to None, so the `While (<ref> != None)` the Label emits simply
    never runs -- inert, not wrong.
    """
    if call.source(0).strip() == '69':
        return 'Game.FindRandomActorFromRef(Game.GetPlayer(), 4096.0)'
    return ctx.note(f'{call.raw_name} over form type '
                    f'{call.source(0).strip() or "?"} - Papyrus iterates '
                    f'actors only', value='None')


@command('call')
def udf_call(ctx, call) -> str:
    """OBSE `Call <ScriptName> arg...` -- invoke a user-defined function.

    The callee is a script, so it is reached through a property typed as that
    script.  The property is keyed on the CANONICAL EditorID, not the spelling
    this call happened to use: TES4 name lookup is case-insensitive, so
    `Call fbmwbmWerewolfManageControlPC` and the record's own
    `fbmwBMWerewolfManageControlPC` are the same script -- but keying on the
    local spelling created a SECOND property differing only in case, and since
    Papyrus is case-insensitive the two declarations collided, the generic
    ObjectReference typing won, and `.TES4Call()` became "undefined function"
    on a property that has it.
    """
    target = call.source(0).strip().rstrip(',')
    if not target:
        return None
    fid = ctx.xref.edid_to_formid.get(target.lower(), '') if ctx.xref else ''
    canon = ctx.xref.formid_to_edid.get(fid, target) if fid else target
    prop = _safe_property_name(canon)
    ctx.sc.property_refs[prop] = papyrus_script_name(canon)
    args = [call.arg(i) for i in range(1, len(call))]
    ctx.sc.udf_calls.append((prop, tuple(args)))
    return f'{prop}.TES4Call({", ".join(args)})'


# ---------------------------------------------------------------------------
# Dialogue and topics
# ---------------------------------------------------------------------------

@command('say', 'sayto', 'saycustom')
def say(ctx, call) -> str:
    """Say / SayTo -- speak a topic.

    Say is declared on ObjectReference, NOT Actor.  A census of Oblivion.esm's
    receivers found 144 calls on 21 NON-actor references -- Daedric shrines
    (ACTI), Clavicus' dog statue (MISC), the XMarker (STAT) speakers the Arena
    announcer talks through.  Promoting the receiver to Actor made those
    declare an `Actor Property`, which the VM refuses to bind, so the property
    read None and the first call on it aborted the function.
    """
    parts = ctx.arg_srcs()
    # SayTo names the TARGET first and the topic second.
    n = 1 if (call.name == 'sayto' and len(parts) >= 2) else 0
    topic = call.arg(n, 'None')
    if len(parts) > n:
        ctx._mark_topic_property(parts[n].strip().split()[0])

    ref = ctx._resolve_objref_ref(call.ref, call.extends)

    # TES4 `Say <topic> <flag> <speak-as NPC> <flag>` names WHO is speaking,
    # separately from the reference that emits the sound.  Skyrim's Say has no
    # such argument, and voice-file lookup is keyed on the SPEAKER's voice type
    # -- an XMarker STAT has none, so the engine finds no folder, plays no
    # audio, and (having no audio to time against) leaves the subtitle onscreen
    # forever.  The importer mints the vanilla answer: a TACT carrying the
    # speak-as NPC's voice type, placed at the emitter's own position.
    speaker, in_head = ctx._say_speak_as(call.ref, parts, call.name)
    if not speaker:
        return f'{ref}.Say({topic})'
    # The topic rides along for the polyfill and the fallback length lookup --
    # unless a script LOCAL shadows the topic's name
    # (DABoethiaCageOpenScript01 has `Short Salutation` next to
    # `say Salutation`; TES4 resolved the argument as the topic, Papyrus would
    # pass the Int).
    name = parts[n].strip().split()[0] if len(parts) > n else ''
    if name and ctx.sc.var_types.get(name.lower()):
        topic = 'None'
    return (f'TES4Polyfill.SpeakAs({speaker}, '
            f'{"True" if in_head else "False"}, {topic})')


@command('startconversation')
def start_conversation(ctx, call) -> str:
    """StartConversation -- the topic INFO is the payload, not just the target.

    Discarding it as `Say(None)` silenced every scripted NPC-NPC conversation
    (DANocturnal's Bejeen/Nocturnal talk, MQ12's Jauffre/Martin council, MS10's
    Llevana scene) and lost their SetStage results.  Per UESP the Topic
    argument is explicitly optional, and omitting it opens the conversation on
    the GREETING -- a real resolvable topic rather than "nothing to say";
    dropping those silenced 64 call sites, all the standard walk-up beat.
    """
    ref = ctx._resolve_self_ref(call.ref, call.extends, actor_func=True)
    parts = ctx.arg_srcs()
    if len(parts) >= 2 and parts[1].strip():
        ctx._mark_topic_property(parts[1].strip().split()[0])
        return f'{ref}.Say({call.arg(1)})'
    ctx.sc.property_refs['GREETING'] = 'Topic'
    return f'{ref}.Say(GREETING)'


@command('addtopic')
def add_topic(ctx, call) -> str:
    """AddTopic on a GATED topic opens that topic's unlock gate.

    Skyrim has no AddTopic, so the visibility model is re-expressed as one
    `TES4Unlock_<topic>` global per explicitly-added topic (see
    tes5_import/dialog_unlocks.py).  INFO and quest-stage fragments already
    emit the SetValue; a script AddTopic is the THIRD reveal route.
    Load-bearing rather than cosmetic: TGReadWantedPoster and
    TG00MysteriousNoteScript are how the player first learns of the Gray Fox.
    An UNGATED topic is already visible, so it no-ops.
    """
    topic = call.source(0).strip().strip('"')
    gname = (ctx.topic_unlock_globals or {}).get(topic.lower())
    if not gname:
        return ctx.note(f'{call.raw_name} (topic not gated)')
    ctx.sc.property_refs.setdefault(gname, 'GlobalVariable')
    return f'{gname}.SetValue(1)'


# ---------------------------------------------------------------------------
# Animation
# ---------------------------------------------------------------------------

@command('playgroup')
def play_group(ctx, call) -> str:
    """PlayGroup -- the API depends on WHAT THE TARGET IS.

    Animated OBJECTS (activators/doors/statics with a NiControllerManager) keep
    their TES4 sequences, so `PlayGroup Forward 0` is
    `PlayAnimation("Forward")`.  Debug.SendAnimationEvent only works on
    behavior-graph ACTORS and silently does nothing on an activator, while
    PlayAnimation on an ACTOR corrupts its behavior graph
    (BShkbAnimationGraph/hkbRagdollDriver crash).

    Routing explicit-ref calls to SendAnimationEvent unconditionally was wrong:
    `CGPrisonSecretWallRef.playgroup forward 1` (CharacterGen's secret door,
    base ACTI prisonSecretWall01, whose NIF carries the Forward sequence)
    became SendAnimationEvent(..., "moveStart") and did nothing, so Renault
    threw the switch and the wall never moved -- while the SELF-call on the
    very next line converted correctly, making two identical TES4 statements
    behave differently.  Resolve the base record and treat only real actors as
    actors; an UNKNOWN target keeps the graph event, which is inert on an
    object but never corrupts an actor.
    """
    anim = call.source(0, 'Idle').rstrip(',').strip('"').strip("'") or 'Idle'
    if call.ref:
        sig = ctx.xref.get_base_signature(call.ref) if ctx.xref else ''
        is_actor = sig in ('NPC_', 'CREA', 'ACHR', 'ACRE') if sig else True
    else:
        # ActiveMagicEffect/TopicInfo Self is the script object, not the actor
        # TES4's block acts on.  Their implicit PlayGroup target is the event
        # actor, so it must go through the behavior graph as an animation
        # event rather than ObjectReference.PlayAnimation on the effect.
        is_actor = call.extends in ('Actor', 'ActiveMagicEffect', 'TopicInfo')

    if is_actor:
        # SendAnimationEvent takes an ObjectReference, and TES4 aims PlayGroup
        # at doors and animated statics as often as at actors, so promoting the
        # property to Actor would leave the VM unable to bind a REFR.
        event = ANIM_GROUP_EVENTS.get(anim.lower(), anim)
        ref = ctx._resolve_objref_ref(call.ref, call.extends)
        return f'Debug.SendAnimationEvent({ref}, "{event}")'

    # NiControllerSequence names in Oblivion NIFs are capitalized.
    obj = (ctx._resolve_objref_ref(call.ref, call.extends) if call.ref
           else ctx._implicit_self(call.extends))
    play = f'{obj}.PlayAnimation("{anim.capitalize()}")'
    return (f'{play}\n  TES4Polyfill.ReleaseBreakaway({obj})'
            if _needs_havok_release(ctx, call) else play)


def _needs_havok_release(ctx, call) -> bool:
    """Is this target a prop Havok holds rigid until the clip ends?

    Oblivion holds two families rigid until a script fires: break-apart props
    (mwallplankbreakaway01's planks) and whole constrained trap islands
    (ctrapswingmacelong01, ctrigtripwire01).  Both are keyframed bodies with
    real mass and Unyielding = 1 -- the clip only creaks the piece off its
    mounting and HAVOK does the visible part once it ends.  CTrapLogs01SCRIPT
    says so in its own header: "On activation havok will turn on and logs will
    roll".  Skyrim keyframed bodies never yield to gravity, so without a
    release the planks hang half-broken and the tripwire never snaps.

    Which objects get it is decided by the MESH, not by the animation group:
    'forward' is 491 of Oblivion's 850 playgroup calls and is overwhelmingly
    gates and portcullises that must follow their clip exactly, yet it is also
    the tripwire's break group.  The group name cannot separate them; the mesh
    can.  The release stays inert on anything not held, because every other
    animated object converts to a mass-0 keyframed body.
    """
    if not ctx.xref:
        return False
    if call.ref:
        return ctx.xref.needs_havok_release(call.ref)
    return ctx.xref.script_owner_needs_havok_release(ctx.sc.edid)


# ---------------------------------------------------------------------------
# Position and movement
# ---------------------------------------------------------------------------

@command('setpos', 'setangle')
def set_pos(ctx, call) -> str:
    """SetPos / SetAngle -- one axis, written through the three-axis native.

    Papyrus has no per-axis setter, so the other two axes are read back from
    the reference.  TES4 separates arguments with whitespace, a comma or both,
    so `SetPos Z, PlacePosZ` is as legal as `SetPos Z PlacePosZ` -- splitting
    on whitespace alone left the axis as `Z,`, which failed the X/Y/Z test and
    silently fell back to X, writing the Z coordinate into the X slot (27 sites
    in 10 scripts, including Morroblivion's levitation and rotation fixes).
    """
    axis = call.source(0, 'X').strip().strip(',').upper()
    if axis not in ('X', 'Y', 'Z'):
        axis = 'X'
    value = call.arg(1, '0')
    ref = ctx._resolve_objref_ref(call.ref, call.extends)
    verb = 'Position' if call.name == 'setpos' else 'Angle'
    coords = [value if a == axis else f'{ref}.Get{verb}{a}()'
              for a in ('X', 'Y', 'Z')]
    return f'{ref}.Set{verb}({", ".join(coords)})'


@command('positionworld')
def position_world(ctx, call) -> str:
    """PositionWorld x, y, z, angleZ, worldspace -- teleport to absolute coords.

    Papyrus splits this into SetPosition + SetAngle (both on ObjectReference);
    there is no worldspace parameter, so that operand is dropped.  Emitted
    verbatim before, it was an undefined function and every mount-recall in
    TeleportRueckkehr failed to compile.
    """
    if len(call) < 3:
        return ctx.note(f'{call.written()} (could not parse)')
    ref = ctx._resolve_objref_ref(call.ref, call.extends)
    xyz = ', '.join(call.arg(i) for i in range(3))
    out = f'{ref}.SetPosition({xyz})'
    if len(call) >= 4:
        out += f'\n  {ref}.SetAngle(0.0, 0.0, {call.arg(3)})'
    return out


# ---------------------------------------------------------------------------
# Actors, factions and combat
# ---------------------------------------------------------------------------

@command('startcombat')
def start_combat(ctx, call) -> str:
    """StartCombat -- TES4's call FORCES the fight.

    Aggression, disposition and faction relations are all ignored (UESP
    Oblivion:StartCombat; CharacterGen stage 74 has the final assassin, base
    aggression 0 and a faction the Emperor's faction Friends at +50, cut the
    Emperor down purely on the strength of this call).  Skyrim's
    Actor.StartCombat is only a nudge the combat AI immediately re-evaluates:
    an Aggression-0 actor exits combat at once -- vanilla's own turn-hostile
    fragment (MS08) pairs SetEnemy with SetAV Aggression 1 for exactly this
    reason -- and a target the actor has no hostile reaction to is dropped as
    invalid.  TES4Polyfill.ForceCombat supplies both preconditions before the
    native.  A player-driven attack needs no forcing.
    """
    if not len(call):
        return None
    ref = ctx._resolve_self_ref(call.ref, call.extends, actor_func=True)
    target = _as_actor(ctx, call.arg(0))
    if (call.ref or '').lower() in ('player', 'playerref'):
        return f'{ref}.StartCombat({target})'
    if ref == 'Self' and call.extends == 'ObjectReference':
        # A bare StartCombat in a script NOT attached to an actor (Nehrim's
        # unused MQ33Sarantha02Script: `StartCombat, Player`).  ForceCombat's
        # parameter is Actor-typed and Papyrus will not pass an
        # ObjectReference there; on a non-actor the cast is None and the call
        # is a logged no-op -- what Oblivion did with it too.
        ref = '(Self as Actor)'
    return ctx._force_combat_call(ref, target)


@command('moddisposition')
def mod_disposition(ctx, call) -> str:
    """ModDisposition -- disposition was removed in Skyrim.

    A full -100 drop is Oblivion's "make them hostile" idiom, so it becomes
    StartCombat.  DIRECTION MATTERS: TES4's signature is
    `<actor>.ModDisposition <target> <value>` and it changes the CALLING
    actor's disposition toward the target, so `UngolimRef.ModDisposition
    player -100` means Ungolim now hates the player and Ungolim is the
    aggressor.  Emitting `<target>.StartCombat(<ref>)` inverted that and made
    the PLAYER attack Ungolim, which in Dark16Kiss framed the player for the
    murder the quest wanted Ungolim to commit.
    """
    parts = ctx.arg_srcs()
    try:
        hostile = len(parts) >= 2 and int(parts[-1]) <= -100
    except ValueError:
        hostile = False
    if not hostile:
        return ctx.note('ModDisposition')
    target = _as_actor(ctx, call.arg(0))
    ref = ctx._resolve_self_ref(call.ref, call.extends, actor_func=True)
    if (call.ref or '').lower() in ('player', 'playerref'):
        return f'{ref}.StartCombat({target})'
    return ctx._force_combat_call(ref, target)


@command('pushactoraway')
def push_actor_away(ctx, call) -> str:
    """PushActorAway -- ObjectReference source, Actor target."""
    ref = ctx._resolve_objref_ref(call.ref, call.extends)
    target = _as_actor(ctx, call.arg(0)) if len(call) else 'Game.GetPlayer()'
    return f'{ref}.PushActorAway({target}, {call.arg(1, "1.0")})'


@command('forcetakecover', 'takecover')
def force_take_cover(ctx, call) -> str:
    """Run TES4's timed flee procedure without blocking the calling script."""
    actor = _as_actor(ctx, ctx._resolve_objref_ref(call.ref, call.extends))
    threat = _as_actor(ctx, call.arg(0))
    duration = call.arg(1, '0.0')
    ctx.sc.property_refs['TES4TakeCoverTaskBase'] = 'Activator'
    return (f'TES4Polyfill.ForceTakeCover({actor}, {threat}, {duration}, '
            'TES4TakeCoverTaskBase)')


@command('dropme')
def drop_me(ctx, call) -> str:
    """Drop the scripted inventory object from its tracked container.

    TES4 inventory scripts could ask the engine to drop their own stack.
    Skyrim exposes the inverse operation on the container, so the assembler
    tracks OnContainerChanged/OnEquipped and this call removes one base item
    from that exact owner into the world.
    """
    return ('If TES4_Container != None\n'
            '  TES4_Container.DropObject(Self.GetBaseObject(), 1)\n'
            'EndIf')


@command('setpcfactionmurder', 'setpcfactionattack', 'setpcfactionsteal')
def set_pc_faction_crime(ctx, call) -> str:
    """The WRITE side of TES4's three per-faction crime booleans.

    Skyrim has no equivalent, so they are reconstructed from the crime-gold
    split.  Writing the flag true means "make this crime stand": raise the
    bounty into the matching band -- murder must clear the threshold, assault
    and theft sit below it.  Census of Skyrim.esm: all 14 real crime factions
    use murder=1000, assault=40 in CRVA, and the importer writes those same
    amounts for every converted crime faction.
    """
    if not len(call):
        return f';NE: {call.raw_name} missing faction arg'
    faction = call.arg(0)
    ctx.sc.property_refs[call.source(0).strip()] = 'Faction'
    violent = call.name != 'setpcfactionsteal'
    setter = 'SetCrimeGoldViolent' if violent else 'SetCrimeGold'
    if call.source(1, '1') in ('0', '0.0'):
        return f'{faction}.{setter}(0)'
    amount = {'setpcfactionmurder': TES4_MURDER_BOUNTY,
              'setpcfactionattack': TES4_ASSAULT_BOUNTY}.get(
                  call.name, TES4_STEAL_BOUNTY)
    return f'{faction}.{setter}({amount})'


def _as_actor(ctx, target: str) -> str:
    """Cast or register `target` so it is Actor-typed at the call site."""
    vtype = ctx.sc.var_types.get(target.lower(), '')
    ptype = ctx.sc.property_refs.get(target, '')
    if ptype.startswith('TES4_') or 'ObjectReference' in (ptype, vtype):
        return f'({target} as Actor)'
    if not ptype and not vtype and target.isidentifier():
        ctx.sc.property_refs[target] = 'Actor'
    return target


# ---------------------------------------------------------------------------
# Magic
# ---------------------------------------------------------------------------

@command('pme', 'playmagiceffectvisuals', 'sme', 'stopmagiceffectvisuals')
def magic_effect_visuals(ctx, call) -> str:
    """pme / sme -- the argument is a magic EFFECT CODE, not a shader EditorID.

    The visuals Oblivion plays are the effect's EFSH, and EFSH records ARE
    converted, so resolve code -> TES4 MGEF -> its shader and Play/Stop that,
    exactly as pms/sms do for a directly-named shader.
    """
    code = call.source(0)
    shader = (ctx.xref.get_mgef_shader_edid(code)
              if (ctx.xref and code) else '')
    if not shader:
        return ctx.note(f'{call.written()} (no shader found for effect code)')
    ref = ctx._resolve_objref_ref(call.ref, call.extends)
    safe = _safe_property_name(shader)
    ctx.sc.property_refs[safe] = 'EffectShader'
    if call.name in ('sme', 'stopmagiceffectvisuals'):
        return f'{safe}.Stop({ref})'
    return f'{safe}.Play({ref}, {call.arg(1, "-1.0")})'


@command('isspelltarget')
def is_spell_target(ctx, call) -> str:
    """IsSpellTarget -- "is ref currently affected by spell X".

    Papyrus has no per-spell test, but HasMagicEffect on the effect the
    converted SPEL actually carries (resolved through the importer's own
    code->MGEF mapping) answers the same question at runtime.
    """
    spell = call.source(0, '')
    fid = (ctx.xref.get_spell_first_skyrim_mgef(spell)
           if (ctx.xref and spell) else 0)
    if not fid:
        return ctx.note(f'{call.written()} (spell has no convertible effect)',
                        value='False')
    ref = ctx._resolve_self_ref(call.ref, call.extends, actor_func=True)
    if ref == 'Self' and call.extends != 'Actor':
        ref = '(Self as Actor)'
    return f'TES4Polyfill.HasMagicEffectByID({ref}, 0x{fid:08X})'


@command('getiscurrentpackage')
def get_is_current_package(ctx, call) -> str:
    """GetIsCurrentPackage -- exact when the argument is a converted PACK."""
    arg = call.source(0, '')
    fid = ctx.xref.edid_to_formid.get(arg.lower(), '') if (ctx.xref and arg) \
        else ''
    if not fid or ctx.xref.record_type.get(fid, '') != 'PACK':
        return ctx.note(call.written())
    ref = ctx._resolve_self_ref(call.ref, call.extends, actor_func=True)
    if ref == 'Self' and call.extends != 'Actor':
        ref = '(Self as Actor)'
    safe = _safe_property_name(arg)
    ctx.sc.property_refs[safe] = 'Package'
    return f'({ref}.GetCurrentPackage() == {safe})'


# ---------------------------------------------------------------------------
# OBSE strings and music
# ---------------------------------------------------------------------------

@command('sv_construct')
def sv_construct(ctx, call) -> str:
    """sv_Construct -- the ONE OBSE string command with an exact equivalent.

    It builds a string_var from a literal, and Papyrus String IS that literal.
    Falling through to the inert ar_/sv_ catch-all left
    `quizQuestion = sv_Construct "..."` as an undefined identifier, which
    failed the whole script -- Morroblivion's fbmwChargenQuestScript (the class
    quiz) is the site, and the Chargen-and-Transport start menu imports it, so
    the Imperial City transport NPC went down with it.  sv_Destruct stays a
    no-op: Papyrus strings are garbage-collected.
    """
    arg = call.source(0)
    if not arg:
        return '""'
    # A bare quoted literal passes straight through; anything else is an
    # expression (a format string plus args) the caller already handles.
    if arg.startswith('"') and arg.endswith('"') and arg.count('"') == 2:
        return arg
    return call.arg(0)


@command('streammusic')
def stream_music(ctx, call) -> str:
    """StreamMusic by FILE PATH.

    Skyrim's music system is form-driven (MusicType.Add() on a MUSC record), so
    a path cannot be played directly -- but the importer authors one MUSC per
    Special cue, named deterministically from that same path, so the call
    resolves to a real record.  Measured: 38 StreamMusic calls in Nehrim.esm,
    35 by path and 3 by bare category; Oblivion.esm has none.  A path with no
    converted file behind it (8 of Nehrim's references are dead on disk) still
    gets the inert marker, because binding a property to a record that was
    never written would abort the whole function at runtime.
    """
    cue = ctx._music_cue_property(call.source(0, '').strip('"\''))
    if cue:
        return f'{cue}.Add()'
    return ctx.note(f'{call.raw_name} - no converted music for '
                    f'({call.src.strip()})')


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

@command('message', 'messagebox')
def message(ctx, call) -> str:
    """Message / MessageBox.

    Vanilla TES4 uses the same printf convention as the OBSE variants --
    `Message "%.0f seconds to close Great Gate!", remainingSec` -- so a format
    string WITH arguments goes through the concatenation helper.  Quoting only
    the first literal printed the specifier verbatim to the player: MQ14's
    Great Gate countdown read "%.0f seconds to close Great Gate!", and so did
    the bounty, the Dawnfang kill count and the Bruma statue's year (86 call
    sites, 16 SCPT + 70 INFO).
    """
    if call.name == 'messagebox':
        shown = _button_box(ctx, call)
        if shown is not None:
            return shown
    papyrus = ('Debug.Notification' if call.name == 'message'
               else 'Debug.MessageBox')
    sources = ctx.arg_sources()
    if not sources:
        return f'{papyrus}("")'
    # The MESSAGE is the first argument; any that follow are button labels,
    # which Papyrus has no equivalent for.  Read it from the NODE rather than
    # by scanning for a closing quote: a literal containing the separator
    # (`"LEVEL AUFSTEIGEN!"`) survives here and did not survive the scan.
    first = sources[0]
    if first.startswith('"') and ctx._OBSE_FMT_RE.search(first[1:-1]):
        return f'{papyrus}({ctx._format_message_args(sources, call.extends)})'
    return (f'{papyrus}({first})' if first.startswith('"')
            else f'{papyrus}({ctx._quote_msg(first)})')


def _button_box(ctx, call) -> str:
    """A MessageBox WITH buttons, as an authored MESG's Show().

    Show() parks this thread on the box and returns the clicked index, which
    TES4_TakeMsgButton() then hands to the script's GetButtonPressed poll
    exactly once (see message_menus.py -- the importer writes the MESG records
    this property binds to).  Returns None when there is no planned MESG (a
    fragment context, or plan drift), so the text-only box is still shown.
    """
    from script_convert.message_menus import parse_button_box
    parsed = parse_button_box(call.src or '')
    if not parsed:
        return None
    mesg = ctx._mesg_for_box(*parsed)
    if not mesg:
        return None
    ctx.sc.property_refs[mesg] = 'Message'
    ctx.sc.uses_msg_buttons = True
    return f'TES4_MsgButton = TES4_ShowMsg({mesg})'


@command('isactionref')
def is_action_ref(ctx, call) -> str:
    """IsActionRef -- was the acting reference this one?

    The operand is always a REFERENCE, never a script variable, so the `player`
    keyword wins even in a script that also declares a local called Player
    (StartCelleAufzugTriggerZone01Script does): `IsActionRef player` asks
    whether the ACTOR was the player, while its own `Player` short is a
    separate trigger flag.  Resolving through the ordinary name path let the
    local-variable guard suppress the keyword and emitted
    `akActionRef == player`, comparing an ObjectReference against an Int.
    """
    if not len(call):
        return f'{ctx._get_action_ref_param()} == None'
    src = call.source(0).strip()
    arg = ('Game.GetPlayer()' if src.lower() in ('player', 'playerref')
           else call.arg(0))
    return f'{ctx._get_action_ref_param()} == {arg}'


# ---------------------------------------------------------------------------
# Spells, factions and actor state
# ---------------------------------------------------------------------------

@command('cast')
def cast(ctx, call) -> str:
    """`<caster>.Cast <spell> <target>` -- Papyrus makes the SPELL the subject.

    Skyrim spells it `Spell.Cast(akSource, akTarget)`, so TES4's spell argument
    becomes the receiver and the authored receiver becomes the source.  Emitted
    positionally it was a bare `Cast(spell, target)` -- an undefined function,
    and the 103 sites naming it took their whole scripts down.
    """
    if not len(call):
        return None
    raw = call.source(0).strip()
    spell = call.arg(0, 'None')
    # Only claim the Spell typing when the name is still free.  A variable that
    # already resolved to something wider -- a `ref` read out of another
    # script's variable table lands as `Form` -- keeps its declaration, and the
    # cast goes on the CALL instead.
    cur = (ctx.sc.property_refs.get(spell, '')
           or ctx.sc.var_types.get(spell.lower(), ''))
    if cur in ('', 'ObjectReference'):
        ctx.sc.property_refs[raw] = 'Spell'
    elif cur != 'Spell':
        spell = f'({spell} as Spell)'
    # Spell.Cast(ObjectReference akSource, ObjectReference akTarget) -- the
    # caster is an ObjectReference.  TES4 fires spells from invisible marker
    # refs (SEHaskillSummonMarker, MG05ShockMark1, SE05SpellMarker1-3), all
    # STATs; promoting the source to Actor left an unbindable `Actor Property`
    # and the spell was never cast.
    source = ctx._resolve_objref_ref(call.ref, call.extends)
    target = call.arg(1) if len(call) > 1 else source
    return f'{spell}.Cast({source}, {target})'


@command('isinfaction')
def is_in_faction(ctx, call) -> str:
    """IsInFaction -- Papyrus declares it on Actor, so the subject is cast."""
    if not len(call):
        return None
    ctx.sc.property_refs[_safe_property_name(call.source(0).strip())] = 'Faction'
    ref = ctx._resolve_self_ref(call.ref, call.extends, actor_func=True)
    if ref == 'Self' and call.extends != 'Actor':
        ref = '(Self as Actor)'
    return f'{ref}.IsInFaction({call.arg(0)})'


@command('getdeadcount')
def get_dead_count(ctx, call) -> str:
    """GetDeadCount -- Skyrim has the SAME function, on ActorBase.

    `ActorBase.GetDeadCount()` is documented as "the number of actors of this
    type that have been killed", so the operand is a base form and the call
    converts exactly.  This previously emitted a literal 0 on the belief that
    no equivalent existed, which silently disabled 152 quest gates across
    Nehrim -- 126 of them plain "is at least one dead" checks that became
    `0 == 1`.
    """
    if len(call):
        return f'{ctx._actor_base_property(call.source(0), call.extends)}' \
               f'.GetDeadCount()'
    if call.ref:
        return f'{ctx._actor_base_property(call.ref, call.extends)}' \
               f'.GetDeadCount()'
    # A bare 0, NOT a trailing `;TODO`: this is an operand and gets embedded
    # mid-expression (`getdeadcount X + 3`), where a `;` would comment out the
    # rest of the line.
    return '0'


@command('setfactionreaction', 'modfactionreaction',
         'setreaction', 'modreaction')
def faction_reaction(ctx, call) -> str:
    """Set/ModReaction -- TES4's per-faction disposition matrix.

    Skyrim replaced it with faction RELATIONS, which Papyrus reaches through
    the polyfill; the two factions and the amount are the payload.
    """
    if len(call) < 2:
        return ctx.note(f'{call.raw_name} needs two factions')
    for n in (0, 1):
        name = call.source(n).strip()
        if name:
            ctx.sc.property_refs[_safe_property_name(name)] = 'Faction'
    return ctx._faction_reaction_call(
        call.arg(0), call.arg(1), call.source(2, '0'),
        is_mod=call.name.startswith('mod'), extends=call.extends)


@command('getincell')
def get_in_cell(ctx, call) -> str:
    """`<ref>.GetInCell <cell>` -- is the reference in that cell?

    TES4 matched the operand as a PREFIX over cell EditorIDs: `GetInCell
    Chorrol` is true anywhere in Chorrol, across all 86 of its cells.  A single
    `GetParentCell() == X` therefore under-tests a family name, so a prefix
    that names more than one cell becomes a generated `TES4_IsIn<Name>()`
    helper (see `_register_cell_family`).

    Exteriors cannot be Cell properties -- a Papyrus `Cell` binds only to an
    interior, and all 43 of vanilla Skyrim's Cell properties name interiors --
    so the helper compares those by worldspace and grid instead.
    """
    name = call.source(0).strip().strip('"')
    if not name:
        return None
    ref = ctx._resolve_objref_ref(call.ref, call.extends)

    interiors, exteriors = ((ctx.xref.split_cell_family(name))
                            if ctx.xref else ([], []))
    if len(interiors) + len(exteriors) > 1:
        helper = ctx._register_cell_family(name, interiors, exteriors)
        return f'{helper}({ref})'

    # A single cell compares directly.
    prop = _safe_property_name(interiors[0] if interiors else name)
    ctx.sc.property_refs[prop] = 'Cell'
    return f'({ref}.GetParentCell() == {prop})'


# ---------------------------------------------------------------------------
# Ownership, essentiality and activation
# ---------------------------------------------------------------------------

@command('setessential')
def set_essential(ctx, call) -> str:
    """SetEssential -- TES4 names a BASE id (`SetEssential <base> 1`).

    The property must be typed to match what VMAD BINDS it to (the SCRO
    FormID, which for a base EditorID is the base record).  An Actor-derived
    type on a base would be UNBINDABLE -- a base is not an Actor -- and abort
    the whole script's init, so the quest never finishes init and its aliases
    never fill.  That was the FGC01Rats bug: QuillWeave (an NPC_ base) typed
    as the Actor script.  A PLACED ref goes the other way and reaches the base
    through the reference.
    """
    parts = ctx.arg_srcs()
    if not parts:
        if not call.ref:
            return ctx.note(f'SetEssential {call.src} (could not parse)')
        ref = ctx._resolve_self_ref(call.ref, call.extends, actor_func=True)
        return (f'({ref} as Actor).GetActorBase()'
                f'.SetEssential({_flag(call.source(0))})')

    target = call.arg(0)
    value = _flag(parts[1] if len(parts) > 1 else '1')
    if _record_type(ctx, parts[0]) in PLACED_REF_SIGS:
        ctx.sc.property_refs[target] = 'Actor'
        return (f'({target} as Actor).GetActorBase().SetEssential({value})')
    # Base form (or unresolved): bind as ActorBase and call directly.  Forced
    # even over an attached-script type, since only ActorBase can bind there.
    ctx.sc.property_refs[target] = 'ActorBase'
    return f'{target}.SetEssential({value})'


@command('setownership')
def set_ownership(ctx, call) -> str:
    """SetOwnership -- Skyrim splits ownership into ACTOR and FACTION owners.

    Which one this is depends on what the argument names, so the comparison
    picks by record type; a bare call means the player.
    """
    ref = ctx._resolve_self_ref(call.ref, call.extends)
    if not len(call):
        return f'{ref}.SetActorOwner(Game.GetPlayer().GetActorBase())'
    arg = call.arg(0)
    if _is_faction(ctx, call, arg):
        return f'{ref}.SetFactionOwner({arg})'
    return f'{ref}.SetActorOwner({arg}.GetActorBase())'


@command('isowner')
def is_owner(ctx, call) -> str:
    """IsOwner -- the READ side of SetOwnership, split the same way.

    Written bare it asks "does the PLAYER own this reference"; with an
    argument it names the owner to test.
    """
    ref = ctx._resolve_objref_ref(call.ref, call.extends)
    if not len(call):
        return f'({ref}.GetActorOwner() == Game.GetPlayer().GetActorBase())'
    arg = call.arg(0)
    if _is_faction(ctx, call, arg):
        return f'({ref}.GetFactionOwner() == {arg})'
    return f'({ref}.GetActorOwner() == {arg}.GetActorBase())'


@command('activate')
def activate(ctx, call) -> str:
    """Activate -- who activates what.

    TES4's optional trailing 0/1 is the "run the activate BLOCK" flag, and
    Papyrus spells its inverse as `abDefaultProcessingOnly`.  A bare
    `X.Activate` means X activates ITSELF (quest and stage scripts opening
    secret walls, where there is no action ref at all).
    """
    parts = [p for p in ctx.arg_srcs() if p.strip()]
    run_flag = '0'
    if parts and parts[-1].strip() in ('0', '1'):
        run_flag = parts[-1].strip()
        parts = parts[:-1]
    ref = ctx._convert_ref(call.ref, call.extends) if call.ref else ''
    if parts:
        activator = call.arg(0)
    elif ref:
        activator = ref
    elif call.extends == 'TopicInfo':
        activator = 'akSpeakerRef'
    else:
        # Which parameter names the activator depends on the EVENT: TES4's
        # GetActionRef is legal in every block, Papyrus scopes each event's
        # parameters, and naming `akActionRef` in an event that declares none
        # is an undefined identifier that fails the whole script.
        activator = ctx._get_action_ref_param()
    target = f'{ref}.' if ref else ''
    if run_flag == '1':
        return f'{target}Activate({activator})'
    return f'{target}Activate({activator}, true)'


def _flag(src: str) -> str:
    """TES4 spells a boolean `0`/`1`; Papyrus wants the keyword."""
    return 'true' if src.strip() in ('1', 'true') else 'false'


def _record_type(ctx, name: str) -> str:
    """The export's record signature for an EditorID, or ''."""
    if not ctx.xref or not name:
        return ''
    fid = ctx.xref.edid_to_formid.get(name.strip().lower(), '')
    return ctx.xref.record_type.get(fid, '') if fid else ''


def _is_faction(ctx, call, arg: str) -> bool:
    """Does this operand name a FACTION rather than an actor?"""
    src = call.source(0).strip()
    if _record_type(ctx, src) == 'FACT':
        return True
    refs = ctx.sc.property_refs
    return 'Faction' in (refs.get(arg, ''),
                         refs.get(_safe_property_name(src), ''))


# ---------------------------------------------------------------------------
# Placement and movement
# ---------------------------------------------------------------------------

@command('moveto', 'movetomarker')
def move_to(ctx, call) -> str:
    """MoveTo -- relocate a reference onto another one.

    MoveTo is declared on ObjectReference, and TES4 moves scenery with it as
    readily as actors (SEHaskillSummonMarker is a STAT the summon spell
    relocates), so the SUBJECT must not be promoted to Actor: an `Actor
    Property` the VM refuses to bind on a STAT left the marker None and it
    never moved.
    """
    parts = ctx.arg_srcs()
    target = call.arg(0, 'None') if parts else 'None'
    # The destination is a PLACED REFERENCE and nothing else in the script
    # necessarily declares it.  Without registering it the call emitted a bare
    # identifier no property backed, and the compiler rejected the whole
    # script -- Morroblivion's CATChargenAndTransport dies on
    # `Player.MoveTo CGPlayerStartMarker1` (a typo: the SCRO table binds only
    # CGPlayerStartMarker, so Oblivion silently no-opped it).  Register only a
    # plain identifier: an already-converted expression (Game.GetPlayer(), a
    # local, a literal) is not a property and must not be declared as one.
    if parts and parts[0].strip().isidentifier() and target == parts[0].strip():
        ctx.sc.property_refs.setdefault(parts[0].strip(), 'ObjectReference')
    ref = ctx._resolve_objref_ref(call.ref, call.extends)
    offsets = ', '.join(p.strip() for p in parts[1:4])
    return f'{ref}.MoveTo({target}, {offsets})' if offsets \
        else f'{ref}.MoveTo({target})'


@command('placeatme')
def place_at_me(ctx, call) -> str:
    """PlaceAtMe -- spawn a base form at this reference.

    Declared on ObjectReference, so the subject is NOT promoted to Actor.
    """
    base = call.arg(0, 'None')
    count = call.source(1, '1')
    ref = ctx._resolve_self_ref(call.ref, call.extends, actor_func=False)
    if ref == 'Self':
        if call.extends == 'ActiveMagicEffect':
            ref = 'GetTargetActor()'
        elif call.extends == 'TopicInfo':
            ref = 'akSpeakerRef'
    return f'{ref}.PlaceAtMe({base}, {count})'


@command('dispel', 'dispelspell')
def dispel(ctx, call) -> str:
    """Dispel -- remove an active spell.

    An ENCHANTMENT operand has no Skyrim Spell behind it to dispel, so it
    neutralises rather than binding a property that could never resolve.
    """
    if not len(call):
        return None
    raw = call.source(0).strip()
    if _record_type(ctx, raw) == 'ENCH':
        return ctx.note(f'Dispel {raw} names an enchantment, which has no '
                        f'Skyrim Spell to dispel')
    arg = call.arg(0)
    ctx.sc.property_refs[raw] = 'Spell'
    ref = ctx._resolve_self_ref(call.ref, call.extends, actor_func=True)
    return f'{ref}.DispelSpell({arg})'


# ---------------------------------------------------------------------------
# Character-generation menus
# ---------------------------------------------------------------------------

@command('showclassmenu', 'showbirthsignmenu')
def chargen_menu(ctx, call) -> str:
    """ShowClassMenu / ShowBirthsignMenu -- the modal chargen pickers.

    TES4's menu was modal to the WHOLE GameMode pass: it blocked, and the
    `setstage` written on the next source line did not run until the player had
    chosen.  Papyrus only parks the thread that called Show(), so the poll's
    NEXT tick re-enters this body while the menu is still open.  The busy latch
    catches that, and in a POLLED body a latched-out pass must RETURN rather
    than fall through: falling through fired `setstage 44` mid-menu, whose
    fragment force-greets the Emperor against a player still locked in the
    menu, so the greet was consumed with nobody able to receive it and the
    scene died (verified live through the game bridge,).

    A ONE-SHOT site (a quest-stage fragment, an OnActivate handler) has no
    repeating caller, so its latch can only trip on a genuine race -- and there
    a Return would DROP the authored tail rather than defer it.  CharacterGen
    stage 87 is exactly that shape: the class menu is followed by
    `MQ02.SetStage(20)`, the end-of-chargen topic unlocks and the autosave.
    """
    from script_convert.message_menus import PAGE_OPTIONS

    key = 'birthsign' if call.name == 'showbirthsignmenu' else 'class'
    plan = (ctx.chargen_menus or {}).get(key)
    if not plan:
        return ctx.note(call.raw_name)

    pages, actions = plan['pages'], plan['actions']
    ctx.sc.uses_chargen_menus = True
    ctx.sc.chargen_menu_seq += 1
    var = f'TES4_menuPick{ctx.sc.chargen_menu_seq}'
    retry = f'TES4_menuRetry{ctx.sc.chargen_menu_seq}'
    first = _safe_property_name(pages[0][0])
    ctx.sc.property_refs[first] = 'Message'

    # Show() returns -1 when the box could not display (a menu/dialogue
    # transition still in flight -- this menu opens 0.1s after an authored
    # Goodbye closes the conversation).  Retry briefly rather than swallow the
    # choice.
    lines = ['TES4_ChargenMenuBusy = True',
             f'Int {var} = {first}.Show()'
             '  ; TES4 modal chargen menu - pauses the game like the original',
             f'Int {retry} = 0',
             f'While {var} < 0 && {retry} < 20',
             '  Utility.Wait(0.5)',
             f'  {var} = {first}.Show()',
             f'  {retry} += 1',
             'EndWhile']

    # Slot PAGE_OPTIONS on a non-final page is "More ...": the global choice
    # index is 9*page + button (see message_menus._paged).
    for page, (medid, _title, _btns) in enumerate(pages[1:], start=1):
        safe = _safe_property_name(medid)
        ctx.sc.property_refs[safe] = 'Message'
        lines += [f'If {var} == {PAGE_OPTIONS * page}',
                  f'  {var} = {PAGE_OPTIONS * page} + {safe}.Show()',
                  'EndIf']

    keyword, acted = 'If', False
    for idx, spells in enumerate(actions):
        if not spells:
            continue
        lines.append(f'{keyword} {var} == {idx}')
        keyword, acted = 'ElseIf', True
        for spell in spells:
            safe = _safe_property_name(spell)
            ctx.sc.property_refs[safe] = 'Spell'
            lines.append(f'  Game.GetPlayer().AddSpell({safe}, false)')
    if acted:
        lines.append('EndIf')

    # Persist the pick (index+1; 0 = unchosen) so the dialogue conditions the
    # import rewrote to GetGlobalValue can match it -- this is what makes the
    # Emperor's post-menu line agree with the sign the player actually chose.
    # Never persist a FAILED pick: 0 means "unchosen" and the dialogue side has
    # an ungated fallback for that case.
    gname = plan.get('choice_global')
    if gname:
        safe = _safe_property_name(gname)
        ctx.sc.property_refs[safe] = 'GlobalVariable'
        lines += [f'If {var} >= 0', f'  {safe}.SetValue({var} + 1)', 'EndIf']
    lines.append('TES4_ChargenMenuBusy = False')

    if ctx._current_event == 'Event OnUpdate()':
        # The queued tick defers to the pass that owns the menu, which runs the
        # authored tail itself once Show() returns.
        lines = ['If TES4_ChargenMenuBusy',
                 '  Return  ; menu already open - TES4 blocked the whole pass',
                 'EndIf'] + lines
    else:
        # One-shot site: skip the menu on a race but keep running, so the
        # authored tail after it is never dropped.
        lines = (['If !TES4_ChargenMenuBusy']
                 + [f'  {line}' for line in lines] + ['EndIf'])
    return '\n  '.join(lines)


# ---------------------------------------------------------------------------
# Identity tests
# ---------------------------------------------------------------------------

@command('getisid')
def get_is_id(ctx, call) -> str:
    """GetIsID -- "is this reference's BASE record that one".

    The operand can be ANY base type (the SE38 oddities are MISC items, not
    actors).  Emitting `(ref as Actor).GetActorBase()` was wrong twice: on a
    non-actor script `Self as Actor` is a cast the CK rejects outright, and
    typing the operand ActorBase mis-binds every non-actor base.
    GetBaseObject() is declared on ObjectReference -- so it needs no cast, and
    still works for actors since Actor extends ObjectReference -- and returns a
    Form, which compares against every base type.
    """
    operand = call.source(0).strip()
    # A raw FormID operand (`GetIsID 7`) is a FORM here, never a number.
    edid = ctx._form_operand_edid(operand)
    arg = (_resolve_name.resolve(ctx, edid, call.extends) if edid
           else call.arg(0, 'None'))
    operand = edid or operand
    if operand:
        ctx._bind_base_form_property(operand)
    ref = ctx._resolve_objref_ref(call.ref, call.extends)
    return f'{ref}.GetBaseObject() == {arg}'


@command('getisclass', 'getpcisclass')
def get_is_class(ctx, call) -> str:
    """GetIsClass -- the CLAS operand is read off the ActorBase.

    Actor has no GetClass of its own, so the reference has to reach its base
    first.
    """
    arg = call.arg(0, 'None')
    if len(call):
        ctx.sc.property_refs[call.source(0).strip()] = 'Class'
    if call.name == 'getpcisclass':
        return f'Game.GetPlayer().GetActorBase().GetClass() == {arg}'
    ref = ctx._resolve_self_ref(call.ref, call.extends, actor_func=True)
    return f'({ref} as Actor).GetActorBase().GetClass() == {arg}'


@command('getisref')
def get_is_ref(ctx, call) -> str:
    """GetIsRef -- reference identity, which is a plain comparison."""
    ref = ctx._resolve_self_ref(call.ref, call.extends, actor_func=True)
    return f'{ref} == {call.arg(0, "None")}'


# ---------------------------------------------------------------------------
# Actor values
# ---------------------------------------------------------------------------

#: The AV commands that WRITE an absolute value, so an enum trait must be
#: bucketed rather than passed through raw.
_AV_SET = frozenset({'setactorvalue', 'setav', 'forceactorvalue', 'forceav',
                     'setactorvalue2', 'setav2'})

#: Reads, for which Encumbrance means the CURRENT carried weight.
_AV_READ = frozenset({'getactorvalue', 'getav'})


@command(*sorted(_ACTOR_VALUE_FUNCTIONS))
def actor_value(ctx, call) -> str:
    """Get/Set/Mod ActorValue -- the AV NAME is a quoted string in Papyrus.

    The OBSE `...2` aliases take the same (AV name, value) arguments as the
    vanilla commands they map onto, so they quote the name here too: without
    them `modAV2 Health 300` emitted an unquoted `Health` and the script failed
    with "undefined identifier".

    SKYRIM HAS NO ATTRIBUTES.  A call naming Strength, Intelligence,
    Willpower, Agility, Speed, Endurance, Personality or Luck has no faithful
    target -- every TES5 actor value sits on a different scale than TES4's
    0-100, so aliasing one onto the nearest look-alike does not preserve the
    authored threshold.  Aliasing them (strength->UnarmedDamage,
    agility/speed->SpeedMult) broke every Morroblivion guild: the Fighters
    Guild gates each rank on `GetAV Strength >= 30`, UnarmedDamage sits near 0
    so nobody qualified, while the Thieves Guild's Agility gate read SpeedMult
    (~100) and passed unconditionally.  A read becomes ATTRIBUTE_STUB_VALUE
    (above every authored threshold) so the gate falls OPEN -- the faithful
    outcome, since a Skyrim character cannot raise an attribute at all and
    enforcing it would lock the content away permanently rather than early.
    """
    if not len(call):
        return None
    raw = call.source(0).rstrip(',').strip('"\'')
    if raw.lower() in TES4_ATTRIBUTES:
        if call.name in _ACTOR_VALUE_READ_FUNCTIONS:
            return ATTRIBUTE_STUB_VALUE
        return (f';TES4 attribute {raw} has no Skyrim equivalent '
                f'-- write dropped')

    av = ACTOR_VALUE_MAP.get(raw.lower(), raw)
    # Oblivion's single Encumbrance AV is TWO in Skyrim: the current carried
    # weight is InventoryWeight, the maximum is CarryWeight.  TES4 splits them
    # the modified-vs-base way, so the over-encumbered idiom is
    # `player.getav encumbrance > player.getbaseav encumbrance` -- MQ01's
    # stage 75/78 tutorial.  Mapping both sides to CarryWeight compared the cap
    # against itself, so neither tutorial stage could ever fire.
    if raw.lower() == 'encumbrance' and call.name in _AV_READ:
        av = 'InventoryWeight'

    args = [f'"{av}"']
    if len(call) > 1:
        scaled = (ctx._scale_enum_av(av, call.source(1))
                  if call.name in _AV_SET else None)
        args.append(scaled if scaled is not None else call.arg(1))

    papyrus = _AV_PAPYRUS.get(call.name, 'GetActorValue')
    if call.name in _AV_PLAYER_ONLY:
        return f'Game.GetPlayer().{papyrus}({", ".join(args)})'
    ref = ctx._resolve_self_ref(call.ref, call.extends, actor_func=True)
    if ref == 'Self':
        # An ACTOR script IS the subject, so the call is written bare -- adding
        # `Self.` changes nothing at runtime but every such line then differs
        # from the reference output.  Any other Self needs the cast.
        return (f'{papyrus}({", ".join(args)})' if call.extends == 'Actor'
                else f'(Self as Actor).{papyrus}({", ".join(args)})')
    return f'{ref}.{papyrus}({", ".join(args)})'


#: AV commands naming the PLAYER by definition, whatever script calls them.
_AV_PLAYER_ONLY = frozenset({'modpcskill', 'advancepcskill'})


#: TES4 AV command -> its Papyrus native.
_AV_PAPYRUS = {
    'getactorvalue': 'GetActorValue', 'getav': 'GetActorValue',
    'getav2': 'GetActorValue',
    'setactorvalue': 'SetActorValue', 'setav': 'SetActorValue',
    'setactorvalue2': 'SetActorValue', 'setav2': 'SetActorValue',
    'modactorvalue': 'ModActorValue', 'modav': 'ModActorValue',
    'modactorvalue2': 'ModActorValue', 'modav2': 'ModActorValue',
    'forceactorvalue': 'ForceActorValue', 'forceav': 'ForceActorValue',
    'getbaseactorvalue': 'GetBaseActorValue', 'getbaseav': 'GetBaseActorValue',
    'modpcskill': 'ModActorValue', 'advancepcskill': 'ModActorValue',
}


# ---------------------------------------------------------------------------
# Game settings
# ---------------------------------------------------------------------------

@command('getgamesetting', 'getgs')
def get_game_setting(ctx, call) -> str:
    """GetGameSetting -- read a GMST.

    A setting this converter WRITES through an actor value must also be READ
    through it, or the save/restore pattern these scripts use ("remember the
    old value, set a new one, put it back") reads the untouched global and
    restores a number the write never changed.

    Otherwise the Int/Float/String variant follows TES4's own naming
    convention: `i` is an integer, `s` a string, everything else a float.
    """
    setting = call.source(0, 'fUnknown').strip().strip('"')
    av = GMST_TO_ACTOR_VALUE.get(setting.lower())
    if av:
        target = ctx._actor_target_for_gamesetting(call.extends)
        return f'{target}.GetActorValue("{av}")'
    if setting.startswith('i'):
        return f'Game.GetGameSettingInt("{setting}")'
    if setting.startswith('s'):
        return f'Game.GetGameSettingString("{setting}")'
    return f'Game.GetGameSettingFloat("{setting}")'


@command('setnumericgamesetting', 'setgamesetting',
         'setnumericgamesettingfloat')
def set_game_setting(ctx, call) -> str:
    """SetGameSetting (OBSE) -- write a GMST at runtime.

    SKSE's Game.SetGameSettingFloat is the literal counterpart, but it does NOT
    compile against the vanilla headers this pipeline builds with (verified:
    "undefined function SetGameSettingFloat", while the getter resolves), and
    requiring SKSE to build is not an option.  So the settings that have a
    per-actor ACTOR VALUE equivalent go through Actor.ModActorValue -- a
    vanilla native producing the same observable change on the player, scoped
    to the actor instead of the whole game, which is what these scripts want.
    Anything without an equivalent keeps a visible marker rather than a call
    that silently does nothing.
    """
    if len(call) < 2:
        return (f';TODO: {call.raw_name} {call.src}'
                f'  ;needs a setting name and value')
    setting = call.source(0).strip().strip('"')
    return ctx._gamesetting_write(setting, call.arg(1), call.extends)


def _typed_already(ctx, prop: str) -> bool:
    """Does this property already carry a type?

    CASE-INSENSITIVE: Papyrus is, so `CharacterGen` and `Charactergen` are ONE
    property -- but they land under different dict keys, and an exact-match
    guard let a later `SetStage` overwrite the specific `TES4_<script>` type
    with the base `Quest`.  Every cross-script variable read through it then
    failed ("field or property speaker not found").
    """
    low = prop.lower()
    return any(name.lower() == low and ptype
               for name, ptype in ctx.sc.property_refs.items())


# ---------------------------------------------------------------------------
# Player controls
# ---------------------------------------------------------------------------

@command('disableplayercontrols', 'enableplayercontrols')
def player_controls(ctx, call) -> str:
    """Toggle the player's controls, MIRRORING the state into a global.

    Skyrim has both writers as natives but NO getter, so TES4's
    GetPlayerControlsDisabled is read back from `TES4ControlsDisabled` (the
    importer authors the record).  Every writer is shadowed, not just those in
    a script that also reads: in MG18 -- the only reader in the plugin -- the
    writers live in two SEPARATE magic-effect scripts, so a same-script gate
    would shadow nothing at all.
    """
    disabling = call.name == 'disableplayercontrols'
    verb = 'Disable' if disabling else 'Enable'
    ctx.sc.property_refs['TES4ControlsDisabled'] = 'GlobalVariable'
    return (f'Game.{verb}PlayerControls()\n'
            f'TES4ControlsDisabled.SetValue({1 if disabling else 0})')
