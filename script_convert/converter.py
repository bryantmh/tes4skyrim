"""ScriptConverter class — core TES4→Papyrus line-by-line conversion."""

import re

from script_convert.emit import expr as _expr
from script_convert.emit import script as _script
from script_convert.tes4 import nodes as _tes4_nodes
from script_convert.constants import (
    BLOCK_FILTER_PARAM, COMMAND_ROWS, DISPATCH_EVENTS, ENUM_ACTOR_VALUES,
    EVENT_REF_PARAMS,
    LOOSE_OPS,
    ENUM_AV_LADDERS, GMST_TO_ACTOR_VALUE, KNOWN_COMMANDS, KNOWN_GLOBALS,
    PAPYRUS_BOOL_FUNCTIONS, PLACED_REF_SIGS, PLAYER_ALIAS_EXTENDS,
    RETURN_TYPES, SAY_SPEAKAS_MIN_TOKENS, SELF_NAMES, TYPE_MAP,
    _ACTORBASE_ARG_FUNCTIONS, _ACTOR_ONLY_FUNCTIONS, _BASE_OBJECT_PAPYRUS,
    _BARE_BOOL_FUNCTIONS,
    _BARE_NO_EQUIV_COMMANDS, _BOOL_VALUED_FUNCTIONS,
    _BRANCH_ONLY_COMMANDS, _COMPARISON_BOOL_FUNCTIONS,
    _OBJREF_SHARED_FUNCTIONS, _PAPYRUS_VALUE_TYPES, _REF_TYPES,
    _ZERO_ARG_REF_FUNCTIONS, _canonical_global, _digit_stripped_formid,
    _record_type_to_base_papyrus, _record_type_to_papyrus,
    _safe_property_name, resolve_property_formid,
    script_type_may_override,
)
from script_convert import resolve_name as _resolve_name
from script_convert import assemble as _assemble
from script_convert import commands as _commands
from script_convert.context import ScriptContext
from script_convert.emit import dispatch as _dispatch
from script_convert.cross_ref import CrossRefGraph
from script_convert.tes4.parser import (
    Mode, parse, split_trailing_comment,
)
from script_convert import symbols as _symbols


# A whole name wrapped in Oblivion's optional quotes: `"MQ01Tate"`.  Anchored,
# so it only ever strips a quoted IDENTIFIER handed to _convert_ref — never a
# string literal, which contains spaces/punctuation and reaches other handlers.
# `\w+` rather than an identifier shape: an EditorID may START WITH A DIGIT
# (`"1TrapFireMineWorldRef"`, `"2akulaSdoorSa"`), and leaving those quoted here
# is exactly the `_MQ01Tate_` trap described below.
_QUOTED_NAME_RE = re.compile(r'^"(\w+)"$')

#: Any `_ACTOR_ONLY_FUNCTIONS` name called BARE -- a pre-filter for
#: `_infer_extends` (see there).  Longest-first so the alternation cannot
#: match a prefix of a longer name.
_ACTOR_ONLY_ANY_RE = re.compile(
    r'(?<!\.)(?<!\w)(?:'
    + '|'.join(sorted((re.escape(_f) for _f in _ACTOR_ONLY_FUNCTIONS),
                      key=len, reverse=True))
    + r')(?:\s|$|\()', re.IGNORECASE)


# See: docs/commentary/script_convert.md#say-line-fallback-duration
SAY_LINE_SECONDS = 3.0
# SayLine's start timeout: how long it waits for the engine's OnBegin fragment
# before declaring the line dropped.  Mirrors SAY_START_WAIT in
# TES4Polyfill.psc and bounds how long a SayLine call can block, which is what
# the say-timer pre-charge has to outlast.
SAY_START_WAIT = 1.5

# The names the expression path treats as COMMANDS even though they are
# absent from COMMAND_ROWS -- each has a dedicated handler in
# `_emit_function`.  Written inline as a tuple literal;
# the tree path needs the same gate, and two copies would drift.
_EXTRA_COMMAND_NAMES = frozenset({
    'bookread', 'call', 'closecurrentobliviongate', 'completequest',
    'createfullactorcopy', 'forcecloseobliviongate', 'getactionref',
    'getangle', 'getbookread', 'getcontainer', 'getcrimeknown',
    'getincell', 'getinsamecell', 'getisid', 'getisrace', 'getisref',
    'getissex', 'getpcisrace', 'getpcissex', 'getpos', 'getquestrunning',
    'getrandompercent', 'getself', 'getstage', 'getstagedone',
    'getstartingangle', 'isactionref', 'isexpelled', 'isinfaction',
    'isquestcompleted', 'message', 'messagebox', 'placeatme', 'pme', 'say',
    'saycustom', 'sayto', 'setangle', 'setdisplayname', 'setinchargen',
    'setplayerinseworld', 'setpos', 'setstage', 'showbirthsignmenu',
    'showclassmenu', 'showracemenu', 'sme', 'startquest', 'stopquest',
    'wakeuppc',
})


class ScriptConverter:
    """Converts Oblivion script source to Papyrus .psc source."""

    # topic (lowercase) -> longest spoken line in seconds, and `info:<FID>` ->
    # that line's exact length, measured from the exported Oblivion voice files
    # (say_durations.scan_voice_durations).  Populated once per run by the
    # pipeline; a topic with no entry falls back to SAY_LINE_SECONDS.
    say_durations: dict = {}

    # DIAL FormIDs (upper hex) whose topic a TES4 script drives via Say/SayTo.
    # These are the only topics whose INFOs need Begin/End timing fragments --
    # TES4Polyfill.SayLine blocks until OnBegin reports the line started and
    # reads its length from OnEnd, while a line the PLAYER picks never goes
    # through SayLine at all.  Filled once per run by pipeline's
    # scan_say_topic_fids() and passed explicitly into every worker (spawned
    # processes do not inherit it); consumed by pipeline.info_needs_fragment(),
    # which the fragment emitter and the importer's VMAD writer BOTH call so
    # the two can never disagree about which INFOs carry a fragment.
    say_topics: set = set()

    # DIAL EditorID (lower) -> `TES4Unlock_<topic>` GlobalVariable name, from
    # tes5_import.dialog_unlocks.build_unlock_plan. Populated once per run by
    # the pipeline. `AddTopic X` on a GATED topic opens that topic's gate, the
    # same SetValue(1) the INFO/QUST fragments emit — see _NO_OP_FUNCS for why
    # an ungated topic stays an inert comment.
    topic_unlock_globals: dict = {}

    # script EditorID (lower) -> [(mesg_edid, text, buttons)], from
    # script_convert.message_menus.build_message_plan. Populated once per run
    # by the pipeline AND the importer from the same analysis, so the Message
    # properties the .psc declares are exactly the MESG records the ESM ships.
    message_menus: dict = {}

    # 'birthsign'/'class' -> {'pages': [(mesg_edid, title, buttons)],
    # 'actions': [[spell_edid, ...] per choice]}, from
    # message_menus.build_chargen_menus.  Shared with the importer, which
    # authors the page MESGs at fixed FormIDs.  Empty when the plugin has no
    # BSGN/CLAS records — the menus then stay no-ops.
    chargen_menus: dict = {}

    def __init__(self, xref: CrossRefGraph):
        self.xref = xref
        # Parsed arguments of the call being emitted, for handlers that
        # have moved off `args_str` (see _emit_function).
        self._arg_nodes: tuple = ()
        #: Did the current call's argument list open with a COMMA?  For a
        #: zero-argument command the token after it is the RECEIVER
        #: (`StopCombat, Player` is `Player.StopCombat`), which is the only
        #: thing that says so.
        self._leading_comma = False
        #: Papyrus type of the value in the assignment being emitted, read off
        #: its parse tree by `emit_assignment`.  Empty outside an assignment.
        self._value_type: str = ''
        #: Parse tree of the script being converted, set by `_parse_source`.
        self._tree = None
        self._current_event: str = ''  # Current event header for context-aware conversion
        self._line_comments: list[str] = []  # Comments accumulated during expression conversion

        # Per-script state lives on ONE object, replaced per script rather
        # than reset field by field (see context.ScriptContext).
        self.sc = ScriptContext()


    @property
    def _property_refs(self) -> dict:
        """The script's property table.

        A live attribute of the COMPATIBILITY surface (I14): read by
        `tes5_import/dialog_converter.py` and written by
        `pipeline._add_scro_ref`, so it stays assignable even though the
        storage moved onto `ScriptContext`.
        """
        return self.sc.property_refs

    @_property_refs.setter
    def _property_refs(self, value: dict) -> None:
        self.sc.property_refs = value


    def note(self, text: str, value: str = '0') -> str:
        """Record a `;NE:` marker for this line and emit an inert value.

        A command with no Papyrus equivalent still has to yield an EXPRESSION:
        the call is often an operand (`getdeadcount X + 3`), where a bare `;`
        would comment out the rest of the line.
        """
        self._line_comments.append(f';NE: {text}')
        return value

    _SAY_TOPIC_RE = re.compile(r'\.?Say\(\s*([A-Za-z_]\w*)')

    def _say_fallback_seconds(self, say_expr: str) -> float:
        """Fallback length for a converted `set T to Say topic` (see SAY_LINE_SECONDS).

        The topic's longest MEASURED line, used by TES4Polyfill.SayLine only
        when the line the engine picked has no measured length of its own.
        """
        tm = self._SAY_TOPIC_RE.search(say_expr or '')
        topic = tm.group(1).lower() if tm else ''
        if not topic:
            ms = self._SPEAK_AS_CALL_RE.match(say_expr or '')
            if ms:
                topic = ms.group('topic').strip().lower()
        return float((self.say_durations or {}).get(topic) or SAY_LINE_SECONDS)

    _SAY_CALL_RE = re.compile(r'^\s*(?P<recv>.+?)\.Say\((?P<topic>[^()]*)\)\s*$')
    # The speak-as shape emitted by the Say handler (see _say_speak_as).
    _SPEAK_AS_CALL_RE = re.compile(
        r'^\s*TES4Polyfill\.SpeakAs\((?P<speaker>[^,()]+),'
        r'(?P<inhead>[^,()]+),(?P<topic>[^,()]+)\)\s*$')

    # Events that run on the engine's own dispatch path, where a blocking
    # Say would stall the engine rather than just this script's tick.  A
    # quest-stage / INFO fragment is compiled as `Function Fragment_*`, so
    # match that too.

    def _say_may_block(self) -> bool:
        """True when a blocking SayLine is safe here (a poll, not a callback)."""
        ev = (self._current_event or '').lower()
        if 'onupdate' in ev:
            return True
        if 'fragment' in ev:
            return False
        return not any(e in ev for e in DISPATCH_EVENTS)

    # `<quest>.GetStage() == N` ... `<timer> <= 0` in ONE condition.  Both
    # orders occur, and other terms may sit between them.
    _STAGE_TIMER_GUARD_RE = re.compile(
        r'^(?P<indent>\s*)If\s+(?P<cond>.*?\b(?P<q>[A-Za-z_]\w*)\.GetStage\(\)\s*=='
        r'\s*(?P<stage>\d+)\b.*?)\s*$', re.IGNORECASE)
    _TIMER_ZERO_RE = re.compile(
        r'\b(?P<timer>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)\s*<=\s*0(?:\.0*)?\b')

    def _guard_stage_timer(self, line: str) -> str:
        """Close the stage-arrival race on `GetStage()==N && <timer> <= 0`.

        \U0001f6d1 THE TIMER IS CHARGED BY STAGE N'S OWN FRAGMENT, and nothing makes
        that charge land before this guard is first tested.  If the previous
        beat left the timer at or below zero -- which is its NORMAL resting
        state, and it also goes negative whenever a line is dropped -- then the
        instant stage N arrives the guard is ALREADY satisfied and the body
        runs before stage N's fragment has said anything.

        Measured (temp/chargen_rec_4.log, 14:47:14-18): CharacterGen
        sat at convTimer = -0.076 for four seconds after Renault's line was
        dropped, so `GetStage()==16 && convTimer<=0` fired the moment stage 16
        was set.  SetStage(17) ran, the force-greet pulled the player into the
        menu, and the Emperor's stage-16 line was never spoken -- INFO 00032B11
        ("You ... I've seen you") is gated on `GetStage CharacterGen == 16` and
        is the ONLY CharGenVoice entry for that stage, so once the stage reads
        17 nothing qualifies at all.

        The fix is a stage-arrival latch: remember the stage this quest was on
        when we last saw it, and require at least one poll pass at stage N
        before honouring the guard.  That pass is what lets stage N's fragment
        run and charge the timer.  25 guards of this shape exist in the
        Oblivion build; the CharacterGen ones are simply the ones that show.
        """
        if 'GetStage()' not in line or '<=' not in line:
            return line
        m = self._STAGE_TIMER_GUARD_RE.match(line)
        if not m:
            return line
        if not self._TIMER_ZERO_RE.search(m.group('cond')):
            return line
        quest = m.group('q')
        stage = m.group('stage')
        # One latch variable per quest, declared once by the caller.
        var = self._stage_latch_var(quest)
        indent = m.group('indent')
        cond = m.group('cond')
        return (f'{indent}If {cond} && {var} == {stage}'
                f'  ; stage-arrival latch: stage {stage} seen a full pass, '
                f'so its fragment has run')

    def _stage_latch_var(self, quest: str) -> str:
        """Name of the "stage we saw last pass" latch for `quest`, registering
        it so the emitter declares and updates it.

        Keyed CASE-INSENSITIVELY: TES4 scripts spell the same quest both ways
        in one file (CharacterGen's poll uses `characterGen` on some lines and
        `CharacterGen` on others).  Keying on the raw spelling emitted TWO
        latches for one quest, and a guard could then compare against the one
        the poll tail never updated -- the guard would never open.  Papyrus is
        case-insensitive, so the duplicate declarations compiled and the fault
        would only have shown in game.
        """
        key = quest.lower()
        var = self.sc.stage_latches.get(key)
        if var is None:
            var = f'TES4_LastStage_{quest}'
            self.sc.stage_latches[key] = var
        return var

    def _emit_say_line(self, target: str, say_call: str, delay: str) -> str:
        """`set T to [ref.]Say[To] ... topic [+ n]` -> a TES4Polyfill.SayLine call.

        TES4's Say/SayTo returned the selected line's length synchronously and
        the script went on at once; every participant of a scripted
        conversation waits on that number.  TES4Polyfill.SayLine restores the
        contract: it blocks until the engine has BEGUN the line (the INFO's
        OnBegin fragment reports the exact measured length), returns that
        length plus a fixed tail, and the caller continues immediately.
        Fragments never write the timer; the owning script's plain countdown
        drains it, and any beat an End result adds lands on top, exactly as
        in Oblivion.

        The pre-charge closes this poll's own `T <= 0` guard for the ~2s a
        SayLine can take (a Say nothing qualifies for waits out its start
        timeout), so a second poll tick cannot start a duplicate; SayLine also
        keeps one waiter per speaker.
        """
        m = self._SAY_CALL_RE.match(say_call or '')
        ms = self._SPEAK_AS_CALL_RE.match(say_call or '')
        if not m and not ms:
            # Unrecognised shape: keep the line audible and the timer > 0.
            return (f'{target} = {SAY_LINE_SECONDS:g}{delay}\n'
                    f'  {say_call}')
        fallback = self._say_fallback_seconds(say_call)
        if ms:
            # A speak-as site (see _say_speak_as): the measuring variant waits
            # for the INFO's Begin fragment the same way SayLine does and
            # returns the same measured length.
            pre = SAY_START_WAIT + 0.25
            fn = 'SpeakAsLine' if self._say_may_block() else 'SpeakAsLineNoWait'
            call = (f'TES4Polyfill.{fn}({ms.group("speaker").strip()}, '
                    f'{fallback:g}, {ms.group("inhead").strip()}, '
                    f'{ms.group("topic").strip()})')
            if self.sc.var_types.get(target.lower().split('.')[-1]) == 'Int':
                return (f'{target} = {int(pre + 0.999)}  ; TES4 Say: closed until the line is under way\n'
                        f'  {target} = Math.Ceiling({call}){delay}')
            return (f'{target} = {pre:g}  ; TES4 Say: closed until the line is under way\n'
                    f'  {target} = {call}{delay}')
        speaker = m.group('recv').strip()
        topic = m.group('topic').strip()
        # The pre-charge closes this poll's own `T <= 0` guard for as long as
        # SayLine can still be BLOCKED, so a second tick cannot re-enter.
        #
        # The bound is SayLine's own start timeout (SAY_START_WAIT, 1.5s), NOT
        # the line length: on a line the engine ACCEPTS it returns after the
        # measured 0.18s median / 0.84s max (, 76 lines), and on a
        # DROPPED line it waits the full timeout and returns 0.0.  The old
        # `min(fallback + 1.0, 2.0)` scaled with the line instead, so a long
        # line charged 2.0s of closed guard even though SayLine had already
        # returned -- dead air on the timer of every line in the topic.
        pre = SAY_START_WAIT + 0.25
        # ð A BLOCKING SayLine IS ONLY SAFE IN A POLL.  It waits for the
        # engine's OnBegin fragment -- 0.18s median, up to SAY_START_WAIT when
        # the line is refused.  In OnUpdate that costs this script's own tick.
        # On the ENGINE'S DISPATCH PATH (a quest-stage or INFO fragment, or an
        # OnPackageStart/End, OnHit, OnCombatStateChanged callback) it stalls
        # the transition itself: the stage cannot advance, the package cannot
        # swap.  That is the stutter that accompanies a line at a STAGE CHANGE
        # rather than a polled line.  Those sites fire and don't wait.
        call_fn = 'SayLine' if self._say_may_block() else 'SayLineNoWait'
        call = f'TES4Polyfill.{call_fn}({speaker}, {topic}, {fallback:g})'
        if self.sc.var_types.get(target.lower().split('.')[-1]) == 'Int':
            # A TES4 `short` holding a Say length: round UP so the tail survives.
            return (f'{target} = {int(pre + 0.999)}  ; TES4 Say: closed until the line is under way\n'
                    f'  {target} = Math.Ceiling({call}){delay}')
        return (f'{target} = {pre:g}  ; TES4 Say: closed until the line is under way\n'
                f'  {target} = {call}{delay}')

    def _owning_scripts(self, ref_name: str, *, converted_only: bool = True,
                        via_record: bool = True) -> list:
        """Converted scripts `ref_name` may name, lowercase.

        Two resolution routes, written out inline at three call sites: the
        property's declared type, and EditorID -> SCRI -> script EditorID for a
        name bound to a record rather than held in a property.  The flags say
        which routes a caller trusts, because they did not agree:

        `converted_only` keeps only a `TES4_<script>` property type.  Off, a
        plain `Actor`/`Quest` type is also tried as a script name -- which only
        `_is_ref_as_int_crossscript` did.

        `via_record` enables the EditorID route, which
        `_is_ref_typed_access` did not use (it has `is_remote_ref_var` for it).
        """
        if not self.xref:
            return []
        out = []
        ptype = self._property_type_ci(ref_name)
        if ptype.startswith('TES4_'):
            out.append(ptype[5:].lower())
        elif ptype and not converted_only:
            out.append(ptype.lower())
        if not via_record:
            return out
        fid = (self.xref.edid_to_formid.get(ref_name)
               or self.xref.edid_to_formid.get(ref_name.lower(), ''))
        scri = self.xref.record_scri.get(fid, '') if fid else ''
        if scri:
            edid = self.xref.script_formid_to_edid.get(scri, '').lower()
            if edid:
                out.append(edid)
        return out

    def _is_ref_typed_access(self, dotted_expr: str) -> bool:
        """Does `Owner.Var` read a ref-typed variable on Owner's script?"""
        if '.' not in dotted_expr or not self.xref:
            return False
        owner, _, var = dotted_expr.strip().partition('.')
        if self.xref.is_remote_ref_var(owner, var):
            return True
        var_low = var.lower()
        return any(
            self.xref.script_all_vars.get(script, {}).get(var_low)
            in ('ObjectReference', 'Actor')
            and (script, var_low) not in self.xref.ref_as_int
            for script in self._owning_scripts(owner, via_record=False))

    def _ref_has_script_var(self, ref_name: str, var_name: str) -> bool:
        """Does ref_name resolve to a script declaring var_name?

        Used to disambiguate Quest.variable vs Quest.function().
        """
        var_low = var_name.lower()
        return any(var_low in self.xref.script_all_vars.get(script, {})
                   for script in self._owning_scripts(ref_name))

    def _is_ref_as_int_crossscript(self, dotted_expr: str) -> bool:
        """Was `Owner.Var` retyped to Int on the owning script?"""
        if '.' not in dotted_expr or not self.xref:
            return False
        owner, _, var = dotted_expr.strip().partition('.')
        var_low = var.lower()
        return any((script, var_low) in self.xref.ref_as_int
                   for script in self._owning_scripts(
                       owner, converted_only=False))

    @staticmethod
    def _infer_extends(source: str, extends: str) -> str:
        """Pre-scan source for bare Actor-only function calls; upgrade extends.

        `_ACTOR_ONLY_FUNCTIONS` is NOT sound for this question — 14 of its
        entries are declared on `ObjectReference` too (`GetDistance`, `AddItem`,
        `GetItemCount`, `Say`, `PlaceAtMe`, `SetScale`, ...), which is exactly
        why `_OBJREF_SHARED_FUNCTIONS` exists and why the call-site cast at
        `_emit_function` already subtracts it.  Here the set must be subtracted
        as well: an upgrade is not a cosmetic type widening but a hard runtime
        failure.  Papyrus binds a script to a form only when the declared base
        type matches, so an `extends Actor` script on a WEAP/ACTI/CONT/DOOR is
        rejected outright — *"Unable to bind script X because their base types
        do not match"* — and never runs at all.  A bare `GetDistance` upgraded
        88 non-actor scripts that way, 67 of which the last in-game run logged
        as unbindable (`GoblinHeadScript` on `GoblinShamanStaff`, every
        `Dark*DeadDropScript`, the Daedric statue scripts, the Publican inn
        triggers).  `get_extends_class` already answers this correctly from the
        attaching record's signature, so only a genuinely Actor-only call may
        override it.

        The scan must also see CODE ONLY.  Run over the raw source it matched
        prose — `MessageBox "…not kill them!"` (`DAMalacathStatueScript`),
        `; evp the post guards` (`ICUmbacanoExitDoorScript`),
        `;StartCombat to get the scene rolling` (`SE09AltarScript`) — and each
        of those upgraded a DOOR/ACTI script into an unbindable one.  Comments
        and string literals are therefore stripped per line first.

        Finally, a function whose TES4 form names its target as an ARGUMENT
        (`GetDeadCount JesanRilian`, `SetEssential SEMuurine 0`) says nothing
        about the calling script's own type — both are `ActorBase` methods in
        Skyrim, not `Actor` ones — so they are excluded from the scan.
        """
        code_lines = []
        locals_declared = set()
        in_actor_event = False
        for line in source.split('\n'):
            code, _ = split_trailing_comment(line)
            code = re.sub(r'"[^"]*"', '""', code)
            begin = re.match(r'\s*begin\s+(\w+)', code, re.IGNORECASE)
            if begin:
                # A block whose Papyrus event HANDS US the actor
                # (`OnEquipped(Actor akActor)`) supplies the implicit subject
                # itself, so an actor-only call inside it says nothing about
                # the script's own type — the `MGBloodwormHelmScript*` helms
                # ride on ARMO records.  Their bodies are emitted against
                # `akActor` (see `_current_event_actor_param`).
                in_actor_event = (BLOCK_FILTER_PARAM.get(begin.group(1).lower(),
                                                         ('', ''))[1] == 'Actor')
            elif re.match(r'\s*end\s*$', code, re.IGNORECASE):
                in_actor_event = False
            decl = re.match(r'\s*(short|long|int|float|ref)\s+(\w+)\s*$', code,
                            re.IGNORECASE)
            if decl:
                # A TES4 local may be NAMED like an Actor function
                # (`MS05DreamworldAmuletScript`'s `short isEquipped`); reading
                # or assigning it is not a call and must not upgrade the type.
                locals_declared.add(decl.group(2).lower())
                continue
            if not in_actor_event:
                code_lines.append(code)
        code = '\n'.join(code_lines)
        # One alternation over every candidate name decides in a single pass
        # whether ANY of them appears.  The per-function loop below still has
        # the final say -- it subtracts this script's own locals, which vary
        # per script and so cannot be baked into the pattern -- but it now runs
        # only for scripts that can actually match.  Measured: `_infer_extends`
        # was 52% of script-conversion time, building and running one regex per
        # candidate name (81) for every script.
        if not _ACTOR_ONLY_ANY_RE.search(code):
            return extends
        for func in _ACTOR_ONLY_FUNCTIONS:
            if (func in _OBJREF_SHARED_FUNCTIONS
                    or func in _ACTORBASE_ARG_FUNCTIONS
                    or func in locals_declared):
                continue
            # Match bare calls (not preceded by '.') anywhere in source
            if re.search(r'(?<!\.)(?<!\w)' + re.escape(func) + r'(?:\s|$|\()',
                         code, re.IGNORECASE):
                return 'Actor'
        return extends


    def _mesg_for_box(self, text, buttons) -> str:
        """The planned MESG EDID for a button-MessageBox call site, matched by
        content (blocks can convert out of source order — MenuMode merges into
        the GameMode poll — so positional matching would misnumber duplicate
        texts). Returns '' when this context has no plan (fragments) or the
        site is not in it."""
        plan = self.message_menus.get((self.sc.edid or '').lower())
        if not plan:
            return ''
        for name, ptext, pbuttons in plan:
            if name in self.sc.msgbox_used:
                continue
            if ptext == text and list(pbuttons) == list(buttons):
                self.sc.msgbox_used.add(name)
                return name
        return ''

    def _emit_button_helpers(self) -> list:
        """The shared state behind the button-MessageBox conversion: Show()
        writes the clicked index here, and the converted GetButtonPressed
        reads it back through the consumer — once, then -1 again, which is
        TES4's own contract and what keeps every `if button == N` poll from
        re-firing forever on a stale index."""
        if not self.sc.uses_msg_buttons:
            return []
        return [
            '',
            'Int TES4_MsgButton = -1',
            '',
            '; Displaying a box resets the pressed state (TES4: GetButtonPressed',
            '; reads -1 from display until the click), then Show() parks this',
            '; thread on the box and its return lands in TES4_MsgButton.',
            'Int Function TES4_ShowMsg(Message TES4_akMsg)',
            '  TES4_MsgButton = -1',
            '  Return TES4_akMsg.Show()',
            'EndFunction',
            '',
            'Int Function TES4_TakeMsgButton()',
            '  Int TES4_taken = TES4_MsgButton',
            '  TES4_MsgButton = -1',
            '  Return TES4_taken',
            'EndFunction',
        ]

    def _emit_cell_family_helpers(self) -> list:
        """Helper functions for the GetInCell prefix families used by a script.

        TES4 matches GetInCell on an EditorID prefix, so one call can mean "in
        any of these 86 cells" — see the GetInCell handler in _emit_function.
        """
        if not self.sc.cell_families:
            return []
        lines = ['']
        for entry in sorted(self.sc.cell_families.values(),
                            key=lambda kv: kv[0].lower()):
            key, cells = entry[0], entry[1]
            exterior = entry[2] if len(entry) > 2 else []
            lines.append(
                f'; TES4 `GetInCell {key}` matched {len(cells)} interior and '
                f'{len(exterior)} exterior cells by EditorID prefix.')
            lines.append(
                f'Bool Function TES4_IsIn{key}(ObjectReference akRef)')
            # `parent` is taken in this scope (the CK compiler rejects it with
            # "function variable parent already defined"), hence the prefix.
            lines.append('  Cell TES4_parentCell = akRef.GetParentCell()')
            # Papyrus has no line-continuation, and a several-hundred-term
            # expression on one line is unreadable, so test-and-return instead.
            for c in cells:
                lines.append(f'  If TES4_parentCell == {c}')
                lines.append('    Return true')
                lines.append('  EndIf')
            if exterior:
                # An exterior cell cannot be a bound Cell property, so match it
                # by the position that identifies it: same worldspace, same
                # 4096-unit grid square. GetPositionX/Y are world units;
                # floor-divide to the cell grid the same way the engine does.
                lines.append('  WorldSpace TES4_ws = akRef.GetWorldSpace()')
                lines.append('  Float TES4_fx = akRef.GetPositionX() / 4096.0')
                lines.append('  Float TES4_fy = akRef.GetPositionY() / 4096.0')
                lines.append('  Int TES4_gx = TES4_fx as Int')
                lines.append('  Int TES4_gy = TES4_fy as Int')
                # `as Int` truncates toward zero; the grid floors. Correct only
                # when truncation actually rounded UP, i.e. the value was
                # negative and not already exact (-4096.0 is cell -1, not -2).
                lines.append('  If TES4_fx < 0.0 && TES4_fx != (TES4_gx as Float)')
                lines.append('    TES4_gx = TES4_gx - 1')
                lines.append('  EndIf')
                lines.append('  If TES4_fy < 0.0 && TES4_fy != (TES4_gy as Float)')
                lines.append('    TES4_gy = TES4_gy - 1')
                lines.append('  EndIf')
                for wrld, x, y in exterior:
                    if not wrld:
                        continue
                    if x is None or y is None:
                        # Worldspace dummy cell: anywhere in the worldspace.
                        lines.append(f'  If TES4_ws == {wrld}')
                    else:
                        lines.append(
                            f'  If TES4_ws == {wrld} && TES4_gx == {x} '
                            f'&& TES4_gy == {y}')
                    lines.append('    Return true')
                    lines.append('  EndIf')
            lines.append('  Return false')
            lines.append('EndFunction')
            lines.append('')
        return lines


    def convert_standalone(self, name: str, source: str,
                           extends: str = 'ObjectReference',
                           editor_id: str = '') -> str:
        """Convert a standalone SCPT record to a full .psc file.

        The phases live in `assemble.py`; this is the compatibility entry
        point (I14) and the per-script context boundary.
        """
        prev = self.sc
        self.sc = ScriptContext(property_refs=dict(prev.property_refs),
                                scro_aliases=dict(prev.scro_aliases))
        return _assemble.build(self, name, source, extends, editor_id)

    def convert_fragment(self, source: str, extends: str = 'Quest') -> list[str]:
        """Convert a script fragment body (not a full script).

        Returns list of converted lines (indented for function body).
        Preserves _property_refs across calls (quest fragments share a converter).
        """
        # A fragment gets a FRESH context, carrying over the property table
        # and the GetInCell families that go with it: the caller emits both
        # AFTER every fragment has converted, so dropping them here would lose
        # a helper a fragment body already called (undefined function
        # TES4_IsIn...).  The SCRO alias map the caller just installed belongs
        # to THIS fragment, so it carries too.
        prev = self.sc
        self.sc = ScriptContext(
            property_refs=dict(prev.property_refs),
            cell_families=dict(prev.cell_families),
            scro_aliases=dict(prev.scro_aliases))
        # A fragment body runs on the ENGINE'S DISPATCH PATH: a quest stage
        # cannot advance, and an INFO cannot finish, while the fragment is
        # still executing.  A blocking SayLine there stalls the transition
        # itself -- the stutter that accompanies a line at a stage change.
        # _say_may_block() reads this to emit SayLineNoWait instead.
        self._current_event = 'Fragment'
        # PARSED, not re-scanned: a fragment is a script without the block
        # wrappers, which is a parser MODE rather than a second hand-written
        # loop.  The three regexes this replaces (the `scn` skip, the
        # declaration match written twice, the `begin`/`end` skip) were a
        # fifth parser that had to agree with the other four about what a
        # declaration looks like -- and did not, since only this copy accepted
        # `reference` while `_convert_line_inner`'s copy also had to.
        #
        # A fragment declares its variables as LOCALS with an initialiser,
        # unlike a script body where they are hoisted to properties: a
        # fragment is one function, so there is nowhere else for them to live.
        try:
            tree = parse(source, Mode.FRAGMENT)
        except Exception:
            return []
        result = []
        for var in tree.variables:
            ptype = TYPE_MAP.get(var.vtype.lower(), 'Int')
            result.append('  %s %s = %s'
                          % (ptype, var.name, '0.0' if ptype == 'Float' else '0'))
        result += _script.emit_body(self, tree.body, extends, 1)
        return result


    _CONTROLS_WRITE_RE = re.compile(
        r'^(\s*)Game\.(Disable|Enable)PlayerControls\(\)\s*(;.*)?$')


    def get_cell_family_helpers(self) -> list:
        """Helper functions for the GetInCell families used so far.

        Fragment callers (QUST stage / INFO scripts) assemble their own file, so
        they must append these once every fragment body has been converted —
        the bodies call them by name.
        """
        return self._emit_cell_family_helpers()


    # ---- hooks for emit/expr.py ------------------------------------------
    # The tree emitter owns expression STRUCTURE; these own the TES4 SEMANTICS
    # it reaches.  Four delegate to the string phases that still exist below --
    # they are replaced by the command table in R4, at which point the string
    # path goes away entirely.

    def returns_bool(self, name: str) -> bool:
        """Does this TES4 SOURCE name return a boolean, so `X == 1` is `X`?"""
        return name.lower() in _BOOL_VALUED_FUNCTIONS

    def compares_bool(self, name: str) -> bool:
        """Does this TES4 name collapse `X == 0/1` in a COMPARISON position?

        Narrower than `returns_bool`: only the comparison-position list, which
        differs from the bare-read one (docs/commentary/script_convert.md #6).
        """
        return name.lower() in _COMPARISON_BOOL_FUNCTIONS

    def emit_name(self, name: str, extends: str) -> str:
        """A local, a zero-argument command read, or an external property."""
        return _resolve_name.resolve(self, name, extends)

    def emit_member(self, owner: str, name: str, extends: str) -> str:
        """`Owner.name` -- a cross-script variable read or a zero-argument call.

        Decides which of the two it is.  A regex used to re-split the dotted
        name out of `owner.name` text; the parser already separated them, so
        what is left is the lookup.
        """
        prop_low = name.lower()
        safe = _safe_property_name(name)
        # TES4 authors commonly cache GetSelf in a ref and then qualify their
        # own variables through it (`me.timer`). Papyrus local properties are
        # already members of Self; emitting `me.timer` instead makes the
        # ObjectReference alias look as though it declared that field.
        if owner.lower() in self.sc.self_aliases and prop_low in self.sc.local_vars:
            return safe
        # A variable the owner's script actually declares is a property read,
        # whatever the name happens to collide with.
        if self._ref_has_script_var(owner, name):
            return f'{self._convert_ref(owner, extends)}.{safe}'
        # A quest's own variable, likewise -- except for the quest methods,
        # which are commands on it rather than variables of it.
        if self.xref.is_quest_ref(owner) and prop_low not in _QUEST_METHODS \
                and prop_low not in KNOWN_COMMANDS:
            return f'{self._convert_ref(owner, extends)}.{safe}'
        # Not a command anywhere: a cross-script variable read.
        if (prop_low not in KNOWN_COMMANDS
                and prop_low not in _BARE_BOOL_FUNCTIONS
                and prop_low not in _MEMBER_COMMANDS):
            return f'{self._convert_ref(owner, extends)}.{safe}'
        return _dispatch.emit_command(self, owner, name, extends)

    def emit_call(self, node, extends: str, *,
                  promote_subject: bool = False) -> str:
        """A command call with its receiver and arguments.

        Rebuilds the argument text and hands it to `_emit_function`, which is
        still the 201-branch chain.  R4 replaces this body with a table lookup
        over the already-parsed `node.args`, deleting the rebuild with it.
        """
        recv = _expr.emit_bare(self, node.receiver) if node.receiver else None
        args = list(node.args)
        # TES4 lets a zero-argument reference function name its SUBJECT as an
        # argument instead of a receiver: `GetDead KimFermaleRef` is
        # `KimFermaleRef.IsDead()`, not `Self.IsDead(KimFermaleRef)`.  Promote
        # it, exactly as the string path does before dispatching.
        # The gate is the BOOL table, not `_ZERO_ARG_REF_FUNCTIONS`: `getlos`
        # is in the latter but genuinely takes a target, so promoting its
        # argument made the TARGET the caster -- `GetLOS player == 1` came out
        # `player.HasLOS()` instead of `(Self as Actor).HasLOS(player)`.
        if (promote_subject and recv is None and len(args) == 1
                and isinstance(args[0], _tes4_nodes.Ident)
                and node.name.lower() in _BOOL_VALUED_FUNCTIONS
                and node.name.lower() in _ZERO_ARG_REF_FUNCTIONS):
            recv, args = args[0].name, []
        # An UNKNOWN name carrying arguments is not a call.  The string path's
        # gate requires the name to be in KNOWN_COMMANDS (or one of its extra
        # lists) before it will treat `name arg` as a command; anything else
        # falls through and the whole expression becomes a `;TODO:` comment.
        # Emitting it as a call instead produced `GetFriendHit(Player)` --
        # `undefined function`, 9 Nehrim scripts that then failed to compile.
        if args and not self._is_known_command(node.name):
            return self._unknown_command_todo(node, extends)
        # `arg_text` is the argument list as the AUTHOR wrote it, kept only
        # for the `;NE:` markers that quote the source.  Nothing decides
        # anything from it any more -- the nodes do.
        self._leading_comma = node.leading_comma
        return _dispatch.emit_command(self, recv, node.name, extends,
                                      args=args)

    def _is_known_command(self, name: str) -> bool:
        """Would the string path treat `name <args>` as a command call?"""
        low = name.lower()
        return (low in KNOWN_COMMANDS or low in _BOOL_VALUED_FUNCTIONS
                # A registered HANDLER is the command's definition just as much
                # as a row is: without this a handler-only name (setReaction,
                # modReaction) fell out here as an unknown identifier and its
                # handler was unreachable dead code.
                or low in _commands.REGISTRY
                or low in _EXTRA_COMMAND_NAMES
                # Commands with NO Papyrus equivalent are still COMMANDS: they
                # convert to a `;NE:` marker plus `0`, not to a `;TODO:` on the
                # whole line.  Omitting this list turned `GetAVModF a b != X`
                # into `If True ;TODO:` and dropped the comparison entirely.
                or low in _BARE_NO_EQUIV_COMMANDS
                or low in _BRANCH_ONLY_COMMANDS
                or low in COMMAND_ROWS
                or bool(re.match(r'^(?:get|set)menu\w*$', low))
                or low.startswith(('con_', 'ar_', 'sv_')))

    def _unknown_command_todo(self, node, extends: str) -> str:
        """What the string path emits for an unrecognised `name <args>`."""
        return f';TODO: {_expr.emit_source(node)}'

    # ---- hooks for emit/stmt.py ------------------------------------------
    # The tree owns which statement KIND a line is; these own what each kind
    # converts to.  They delegate to the string path while R3 is verified,
    # the same staging that made R2 checkable one construct at a time.

    def _string_into_object(self, stmt) -> bool:
        """Does this assignment put a String into an object-typed variable?

        The shape an OBSE array read makes: the container is declared
        `array_var`, which has no Papyrus type and lands on String.
        """
        target = self.type_of(_expr.emit_source(stmt.target))
        if not target or target in _PAPYRUS_VALUE_TYPES:
            return False
        value = _expr.emit_source(stmt.value)
        # A cross-script read resolves on the OWNING script's table, which is
        # where an `array_var` declaration actually lives.
        return (self.type_of(value) == 'String'
                or self.remote_type_of(value) == 'String')

    def _is_obse_array(self, node) -> bool:
        """Does this expression read an OBSE `array_var`?

        The declaration maps to String for want of a Papyrus equivalent, so a
        read assigns a String into whatever the target really is.  Cross-script
        reads (`OtherScript.someArray`) count too -- that is how Morroblivion's
        werewolf scripts pass their equipment list around.
        """
        text = _expr.emit_source(node)
        if text.split('.')[-1].lower() in self.sc.obse_arrays:
            return True
        # A cross-script read names the OWNING script first.  `script_all_vars`
        # holds each script's declarations by name, keyed by ScriptName.
        parts = text.split('.')
        if len(parts) != 2 or self.xref is None:
            return False
        owner = self.xref.script_all_vars.get(parts[0].lower(), {})
        return owner.get(parts[1].lower(), '').lower() == 'array_var'

    def emit_assignment(self, stmt, extends: str) -> str:
        """`set X to Y` / `let X := Y` / `let X += Y`."""

        # An OBSE ARRAY element write (`let arr[0] := x`).  Papyrus has real
        # arrays but no equivalent of OBSE's dynamic containers, and the
        # `ar_Construct` that built this one is already inert -- so the
        # element writes are too, rather than assigning into an undeclared
        # `arr_0_` identifier that fails the whole script.
        # A cross-script READ of a member the owning script never declares is
        # dangling in the ORIGINAL mod, exactly like the write below --
        # Morroblivion's werewolf scripts read `fbmwBMAAAImAWere.equippeditem`,
        # an OBSE `array_var` that survives only as a String.  Oblivion
        # ignored it; Papyrus fails the whole file.
        if isinstance(stmt.value, _tes4_nodes.Member):
            _read_dangling = self._dangling_cross_script_target(
                _expr.emit_source(stmt.value))
            if _read_dangling:
                return (';%s = %s  ;%s'
                        % (_expr.emit_source(stmt.target),
                           _expr.emit_source(stmt.value), _read_dangling))

        # Reading an OBSE ARRAY variable is inert for the same reason writing
        # one is: `array_var` maps to String for want of anything better, so
        # the read lands a String in whatever the target is declared as -- and
        # Papyrus refuses that outright.  The cross-script case is caught by
        # the TYPES disagreeing, since only an array read produces a String
        # where an object is declared.
        # An `Index` VALUE is always one: `arr[i]` subscripts an OBSE array,
        # which Papyrus has no equivalent for -- the subscript cannot even be
        # preserved, so the read is inert regardless of what it is assigned to.
        if (isinstance(stmt.value, _tes4_nodes.Index)
                or (isinstance(stmt.value, (_tes4_nodes.Ident, _tes4_nodes.Member))
                    and (self._is_obse_array(stmt.value)
                         or self._string_into_object(stmt)))):
            return (';%s = %s  ;NE: OBSE array read, no Papyrus equivalent'
                    % (_expr.emit_source(stmt.target),
                       _expr.emit_source(stmt.value)))
        if isinstance(stmt.target, _tes4_nodes.Index):
            return (';let %s := %s  ;NE: OBSE array write, no Papyrus '
                    'equivalent' % (_expr.emit_source(stmt.target),
                                    _expr.emit_source(stmt.value)))
        target = self._convert_ref(_expr.emit_source(stmt.target), extends)
        # The VALUE'S TYPE comes off the node, not from scanning the rendered
        # text: a command name inside a string literal cannot be mistaken for
        # a call, and arithmetic is typed by its operands rather than by
        # whether the rendering happens to contain a decimal point.
        self._value_type = _symbols.type_of_expr(stmt.value, self.type_of)
        try:
            return self._assign(stmt, target, extends)
        finally:
            self._value_type = ''

    def _assign(self, stmt, target: str, extends: str) -> str:

        # `set <ref> to GetFirstRef <type>` opens an OBSE ref-walk: remember
        # which variable it drives so the `Label` that follows emits the
        # matching `While (<ref> != None)`.
        if _call_name(stmt.value) == 'getfirstref':
            self.sc.refwalk_var = target

        # A compound `let X += Y` expands to `X = X + Y`; Papyrus has none.
        value_node = stmt.value
        value = _expr.emit(self, value_node, extends)

        if target in ('Self', 'GetTargetActor()', 'akSpeakerRef'):
            return f';{target} = {value}  ;cannot assign to Self in Papyrus'
        # A cross-script write whose variable the owner script never declares
        # is dangling in the ORIGINAL mod, not a conversion bug: three Nehrim
        # scripts write `AutoSaveQuest.ReadyForAutosave`, which
        # AutoSaveQuestScript does not define.  Oblivion ignored it; Papyrus
        # fails the whole file ("field or property not found").
        dangling = self._dangling_cross_script_target(
            _expr.emit_source(stmt.target))
        if dangling:
            return f';{target} = {value}  ;{dangling}'
        # In AME/TopicInfo scripts, Self is the target actor, not the script;
        # akSpeakerRef is an ObjectReference and needs the cast.
        if value == 'akSpeakerRef' and extends == 'TopicInfo':
            value = '(akSpeakerRef as Actor)'
        elif value == 'Self':
            if extends == 'ActiveMagicEffect':
                value = 'GetTargetActor()'
            elif extends == 'TopicInfo':
                value = '(akSpeakerRef as Actor)'

        if (value.strip().lower() in EVENT_REF_PARAMS
                and self.type_of(target) == 'Actor'):
            value = self._cast(value, 'Actor')

        if value.lstrip().startswith(';TODO:'):
            ttype = self.type_of(target)
            if ttype == 'GlobalVariable':
                return f'{target}.SetValue(0)  {value}'
            dflt = '0' if not ttype or ttype in _PAPYRUS_VALUE_TYPES else 'None'
            return f'{target} = {dflt}  {value}'

        if stmt.op:
            if self._value_type == 'Bool':
                value = f'({value} as Int)'
            joiner = value if not stmt.op else f'{target} {stmt.op} {value}'
            if self._is_global_target(target):
                return (f'{target}.SetValue({self._global_read(target)} '
                        f'{stmt.op} {value})')
            return f'{target} = {joiner}'


        # TES4 returned the LINE DURATION from Say; Papyrus returns nothing,
        # so the assignment becomes the polyfill's measured call.  The tree
        # already separates the Say call from any `+ 2` the author added to
        # it, which is what 60 lines of balanced-paren scanning over the
        # emitted text used to recover.
        say, delay = _split_say(self, value_node, extends)
        if say is not None:
            return self._emit_say_line(target, say, delay)

        if self._is_global_target(target):
            clean = value.split(';TODO')[0].rstrip() if ';TODO' in value else value
            todo = '  ;TODO' + value.split(';TODO', 1)[1] if ';TODO' in value else ''
            return f'{target}.SetValue({clean}){todo}'

        # `set X.fQuestDelayTime to N` kicks the OWNING quest script's poll.
        # NEVER RegisterForUpdate here: that is a REPEATING zero-interval
        # registration -- OnUpdate every frame until something unregisters it
        # -- and it shipped in 45 scripts.  A single update is the TES4
        # semantics anyway: the converted OnUpdate re-arms itself, and per the
        # TES4 CS a delay of 0 means "revert to the DEFAULT 5s cadence".
        if target.endswith('.fQuestDelayTime'):
            quest_ref = target.rsplit('.', 1)[0]
            try:
                fval = float(value.strip())
            except ValueError:
                return (f'{quest_ref}.RegisterForSingleUpdate({value.strip()})'
                        f'  ;fQuestDelayTime')
            if fval <= 0:
                return (f'{quest_ref}.RegisterForSingleUpdate(5.0)'
                        f'  ;fQuestDelayTime = 0 (TES4 default cadence)')
            return (f'{quest_ref}.RegisterForSingleUpdate({fval:g})'
                    f'  ;fQuestDelayTime')

        return self._typed_assign(target, value, value_node, extends)

    def _typed_assign(self, target: str, value: str, value_node,
                      extends: str) -> str:
        """Write `value` into `target`, reconciling their Papyrus types.

        TES4 is untyped: a `short` holds a reference, a `float` expression
        lands in an `int`, an ObjectReference goes into an Actor slot.  Papyrus
        rejects all three.  The reconciliation reads the TARGET's declared type
        and the VALUE's inferred one -- never the emitted text, which is what
        `_coerce_float_to_int` and `_coerce_ref_to_actor` scanned.
        """
        want = self.remote_type_of(target) or self.type_of(target)
        remote_got = self.remote_type_of(value)
        got = (self._value_type or remote_got
               or self.type_of(value) or _call_return_type(value))
        # The event parameters are declared by the Papyrus event signature,
        # not in the script's local symbol table. Their source names are
        # authoritative ObjectReference values and require the same Actor
        # downcast as any explicitly declared ObjectReference.
        if value.strip().lower() in EVENT_REF_PARAMS:
            got = 'ObjectReference'
        # TES4 used a `ref` as a flag and wrote a plain INTEGER to it (`set
        # attackRef to 1` means "already handled").  Papyrus has no such
        # coercion and `ref = 1` does not compile, so any integer literal into
        # a reference slot is the clear: `None`.
        if ((want in _REF_TYPES or want.startswith('TES4_'))
                and _INT_LITERAL_RE.match(value.strip())
                # ...unless the OWNING script's own use proves the variable is
                # an integer.  TES4 declares it `ref` and then uses it as a
                # flag; the export records that (`ref_as_int`), and writing
                # `None` into it makes the flag unreadable.
                and not ('.' in target
                         and self._is_ref_as_int_crossscript(target))):
            return f'{target} = None'

        # TES4 allowed storing a reference in a `short`; Papyrus does not, and
        # there is no cast that makes it meaningful -- the script is reading an
        # id it can no longer act on, so the write is commented out rather than
        # silently truncated.
        if want == 'Int' and got in _REF_TYPES:
            return f';{target} = {value}  ;TES4 stored ref in short'

        # `Self` is whatever the SCRIPT extends, so assigning it into an Actor
        # slot on a non-actor script needs the downcast the same as any other
        # ObjectReference (SE09AltarScript stores itself in GatekeeperRef).
        if want == 'Actor' and value.strip() == 'Self' and extends != 'Actor':
            return f'{target} = {self._cast(value, "Actor")}'

        if want and got and want != got:
            # A Float expression assigned to an Int variable: TES4 truncated,
            # Papyrus refuses to compile.  The cast is what TES4 meant.
            if want == 'Int' and got in ('Float', 'Bool'):
                # TES4 stored a test's answer in a `short`; Papyrus will not
                # put a Bool (or a Float) in an Int without the cast.
                return f'{target} = {self._cast(value, "Int")}'
            # Int -> Float needs NOTHING: Papyrus converts freely UP, and the
            # cast only differs from HEAD's output while changing no meaning.
            if want == 'Float' and got == 'Int':
                return f'{target} = {value}'
            # A cross-script TES4 `ref` is conservatively exposed as Form,
            # while the receiving local's authored use can prove the narrow
            # base type (Spell for Cast, Armor for EquipItem, etc.).  Papyrus
            # requires the explicit downcast even when the runtime form is the
            # correct type.
            if ((got == 'Form' or remote_got == 'Form')
                    and want not in _PAPYRUS_VALUE_TYPES and want != 'Form'):
                return f'{target} = {self._cast(value, want)}'
            # An ObjectReference into an Actor slot needs the downcast; the
            # reverse is implicit (Actor extends ObjectReference).  A property
            # typed as the SCRIPT attached to a record (`TES4_MS45MonsterScript`)
            # is not an Actor either, but the object it binds to IS one -- so
            # it casts at the assignment rather than being retyped, which the
            # cross-script variable reads through it still need.
            if want == 'Actor' and (got == 'ObjectReference'
                                    or got.startswith('TES4_')):
                return f'{target} = {self._cast(value, "Actor")}'
            if (want.startswith('TES4_')
                    and got not in _PAPYRUS_VALUE_TYPES):
                return f'{target} = {self._cast(value, want)}'
        return f'{target} = {value}'

    def emit_command_statement(self, expr, extends: str) -> str:
        """A bare command used as a STATEMENT, not as a value.

        Routed through `emit_call` so the PARSED arguments reach the command
        layer.  The difference from value position is what `0` means: as a
        value `disableLinkedPathPoints` is `0`, as a statement it is
        `;NE: disableLinkedPathPoints` -- which is what the wrapper folds.
        """
        if isinstance(expr, _tes4_nodes.Call):
            return _dispatch.as_statement(self, self.emit_call(expr, extends))
        # A bare identifier in statement position is a zero-argument command.
        return _dispatch.as_statement(self, _expr.emit(self, expr, extends))

    def emit_return(self, stmt, extends: str) -> str:
        """TES4 `return` ends the block; a UDF carries its value out here."""
        if self.sc.udf_returns:
            return f'Return {self.sc.udf_return_value or "0"}'
        return f'{self.sc.poll_return_prefix}Return' if self.sc.poll_return_prefix \
            else 'Return'

    def emit_set_function_value(self, stmt, extends: str) -> str:
        """OBSE `SetFunctionValue <expr>` -- record a user function's result.

        Emits nothing itself: TES4 always pairs it with a `return`, which is
        what carries the value out.  Emitting a `Return` here as well gave the
        pair two, and the second was unreachable.
        """
        self.sc.udf_returns = True
        self.sc.udf_return_value = (_expr.emit(self, stmt.value, extends)
                                    if stmt.value else '0')
        return ''

    def emit_jump(self, stmt, extends: str) -> str:
        """OBSE `Label <n>` / `Goto <n>` -- the head and tail of a ref-walk.

        Oblivion scans the loaded cells with
            set <ref> to GetFirstRef <type>
            Label <n>
              if ( <ref> ) ... set <ref> to GetNextRef / Goto <n> ... endif
        `Label`/`Goto` are not Papyrus keywords at all, and emitting them
        verbatim is an undefined-function error that fails the ENTIRE script
        -- which then fails every other script declaring a property of its
        type.  The Label becomes the `While` header and the Goto a no-op: the
        authored `set <ref> to GetNextRef` above it already advanced the ref
        and the header re-tests it, so the jump back is implicit.  `EndWhile`
        is emitted where the enclosing block ends (see `_close_refwalk`) --
        the Goto sits deep inside the body's `if` nest and cannot close the
        loop across them.
        """
        if isinstance(stmt, _tes4_nodes.Label):
            if not self.sc.refwalk_var:
                # A Label with no walk in flight controls something this
                # converter cannot model; drop it rather than emit a call.
                return (f';Label {stmt.number}'
                        f'  ;NE: OBSE Label has no Papyrus equivalent')
            self.sc.refwalk_labels.add(stmt.number)
            return (f'While ({self.sc.refwalk_var} != None)'
                    f'  ;OBSE ref-walk (Label {stmt.number})')
        if stmt.number in self.sc.refwalk_labels:
            return f';Goto {stmt.number}  ;OBSE ref-walk continues (loop re-tests)'
        return f';Goto {stmt.number}  ;NE: OBSE Goto has no Papyrus equivalent'

    def emit_package_test(self, recv, op: str, comparand: str,
                          extends: str):
        """`GetCurrentAIPackage <op> <PACK|type-code>`, or None if not one.

        Delegates to the string path, which owns both halves of this rule --
        the named-package equality and the numeric type-code expansion over
        the actor's own AIPackage list.  Reproducing it here would duplicate
        `_packages_of_type` and its actor resolution; R4 moves the whole rule
        onto the node instead.
        """
        if op not in ('==', '!='):
            return None
        cand = comparand.strip().strip('()').strip()
        actor = self._resolve_self_ref(recv, extends, actor_func=True)
        if actor == 'Self' and extends not in ('Actor',):
            actor = '(Self as Actor)'

        # A PACK EditorID compares exactly: vanilla Papyrus has
        # Actor.GetCurrentPackage(), so the test converts one-for-one.
        if self.xref and re.match(r'^[A-Za-z_]\w*$', cand)                 and not cand.isdigit():
            fid = self.xref.edid_to_formid.get(cand.lower(), '')
            if fid and self.xref.record_type.get(fid, '') == 'PACK':
                canon = self.xref.formid_to_edid.get(fid, cand)
                prop = _safe_property_name(canon)
                self.sc.property_refs[prop] = 'Package'
                return f'{actor}.GetCurrentPackage() {op} {prop}'

        # A numeric TES4 package TYPE has no Papyrus counterpart -- Skyrim
        # exposes the package, not its type -- so the test expands over the
        # actor's OWN packages of that type: `== N` becomes an OR chain of
        # equalities, `!= N` an AND chain of inequalities.  Resolving the list
        # needs the actor, which is why an unresolvable one falls through to
        # the caller's ordinary emission rather than inventing a constant.
        if cand.isdigit():
            packs = self._packages_of_type(recv, int(cand))
            if not packs:
                return None
            joiner = ' || ' if op == '==' else ' && '
            terms = []
            for edid in packs:
                prop = _safe_property_name(edid)
                self.sc.property_refs[prop] = 'Package'
                terms.append(f'{actor}.GetCurrentPackage() {op} {prop}')
            return '(' + joiner.join(terms) + ')' if len(terms) > 1                 else terms[0]
        return None

    def emit_string(self, text: str, extends: str) -> str:
        """A quoted literal: a TES4 EditorID reference, or a real string.

        TES4 let a form name be quoted wherever a form was wanted, so the
        quotes have to come off when the name resolves to a record, a local,
        or the player -- otherwise Papyrus is handed a String where a Form is
        declared.  Delegates to the resolver, which owns that lookup and
        registers the property it creates.

        A string that resolves to nothing comes back QUOTED, so a genuine
        string literal is returned unchanged.
        """
        return _resolve_name.resolve(self, text, extends)

    def emit_array_read(self, owner: str, extends: str) -> str:
        """OBSE array element read: emit the base variable, drop the subscript.

        Papyrus has no `array_var`, and the subscript cannot be preserved.
        The base name is kept rather than a `0` marker because the comparand
        is often a typed form -- `0 == <Spell>` is a compile error, while
        `spells == <Spell>` builds (Morroblivion's blight-cure scripts).
        """
        return _resolve_name.resolve(self, owner, extends)

    def remote_type_of(self, dotted: str) -> str:
        """Type of `Var` in `Owner.Var`, resolved on the script Owner is typed as.

        TES4 let one script read another's variables directly.  The owner is a
        property typed `TES4_<script>`, so the remote script's variable table
        answers what the member is -- three sites resolved this identically by
        hand.
        """
        if '.' not in dotted or not self.xref:
            return ''
        owner, _, member = dotted.partition('.')
        owner_type = self.type_of(owner.strip(), locals_first=False)
        if not owner_type.startswith('TES4_'):
            return ''
        script = owner_type[5:].lower()
        member_low = member.lower()
        # The owning script's OWN use decides: a `ref` it calls an actor-only
        # method on is declared Actor there, so a write from here needs the
        # downcast.  `script_all_vars` records the TES4 declaration, which says
        # only `ObjectReference`; `script_actor_vars` records the promotion.
        if member_low in self.xref.script_actor_vars.get(script, ()):
            return 'Actor'
        key = (script, member_low)
        if key in self.xref.ref_as_int:
            return 'Int'
        if key in self.xref.ref_as_base_form:
            return 'Form'
        attached = self.xref.ref_script_types.get(key, ())
        if len(attached) == 1:
            return next(iter(attached))
        return self.xref.script_all_vars.get(script, {}).get(member_low, '')

    def type_of(self, name: str, *, locals_first: bool = True) -> str:
        """Papyrus type carried by `name`, or '' if it is not declared here.

        `_property_refs` is keyed by the AUTHORED spelling but Papyrus is
        case-insensitive, so the lowercase fallback is mandatory -- 16 sites
        wrote this chain by hand and did not all agree on it.  Pass
        `locals_first=False` where a local of the same name must be ignored.
        """
        if '.' in name:
            # `Owner.Var` is the OWNER'S variable, not a local that happens to
            # share the member's name: reading only the tail typed
            # `DAHermaeusMora.target` as this script's own `target` (Actor),
            # so the ObjectReference the other script declares needed no cast
            # and the assignment failed to compile.
            remote = self.remote_type_of(name)
            if remote:
                return remote
        low = name.lower().split('.')[-1]
        if locals_first:
            local = self.sc.var_types.get(low, '')
            if local:
                return local
        return self.sc.property_refs.get(name, self.sc.property_refs.get(low, ''))

    def _property_type_ci(self, name: str) -> str:
        """`type_of` for a property, matching ANY case spelling of the key.

        `_property_refs` is keyed by the AUTHORED spelling and mutated at 92
        sites, so a maintained side index would drift.  Only the cross-script
        resolvers want this: registering a property goes through `type_of`,
        and making THAT case-insensitive stopped a second spelling from ever
        being added -- which changed the emitted property name from the
        script's `TG02Taxes` to the record's `TG02taxes` (2 files).  Sorted so
        the answer cannot depend on insertion order.
        """
        exact = self.type_of(name, locals_first=False)
        if exact:
            return exact
        want = name.lower()
        return next((t for k, t in sorted(self.sc.property_refs.items())
                     if k.lower() == want), '')

    def get_property_refs(self) -> dict[str, str]:
        """Get accumulated external property references.

        Property TYPES are decided by how the script body uses each ref (the
        per-function handlers promote to Actor/ObjectReference/base as needed).
        We deliberately do NOT blanket-coerce types here based on the bound
        record: a property the body uses as an Actor/ObjectReference must stay
        that type even if it happens to be bound to a base, because retyping it
        to ActorBase would break the body (`StartCombat`, MoveTo, ==Actor…).

        The one confirmed alias-break case — an NPC base used ONLY via
        `GetActorBase()` (SetEssential) but typed as an Actor-derived script —
        is fixed at the point of use (the SetEssential handler types it
        ActorBase), not here.
        """
        return dict(self.sc.property_refs)

    # Where the speak-as identity sits in a TES4 Say/SayTo argument list:
    #   Say   <topic> <force-subtitles> <speak-as> [<in-players-head>]
    #   SayTo <target> <topic> <flag> <speak-as> <flag>

    def _say_speak_as(self, ref_name, pparts: list, fname_low: str) -> tuple:
        """(speaker property, in-head) for a speak-as call site.

        TES4's third `Say` argument is the identity the line belongs to; the
        receiver only emits the sound.  Skyrim has no equivalent parameter and
        keys voice lookup on the SPEAKER, so the emitting marker (a STAT, with
        no voice type) resolves to no voice folder and the line is silent.
        The importer places a TACT carrying that NPC's voice type at the
        emitter's authored position and registers it under the speaker name --
        see tes5_import/speaker_activators.py, which derives the SAME name
        from the same authored pair, so the two agree with no side-channel.

        The fourth authored argument is TES4's "speak in the player's head",
        which Skyrim exposes natively as `Say`'s third parameter
        (abSpeakInPlayersHead) -- passed straight through by
        TES4Polyfill.SpeakAs.  🛑 Never emulate it by moving the speaker.

        Returns ('', False) when this is not a speak-as site.
        """
        none = ('', False)
        if not ref_name:
            return none
        need = SAY_SPEAKAS_MIN_TOKENS.get(fname_low)
        if need is None:
            return none
        tokens = []
        for part in pparts:
            tokens.extend(str(part).split())
        if len(tokens) < need:
            return none
        # The identity is the first non-numeric token after the topic; the
        # in-head flag is the numeric token after it.
        rest = tokens[2:] if fname_low == 'sayto' else tokens[1:]
        topic = tokens[1] if fname_low == 'sayto' else tokens[0]
        voice = ''
        in_head = False
        for i, t in enumerate(rest):
            if t and not t.lstrip('-').replace('.', '').isdigit():
                voice = t
                nxt = rest[i + 1] if i + 1 < len(rest) else ''
                in_head = bool(nxt) and nxt.lstrip('-').isdigit() and int(nxt) != 0
                break
        if not voice or not re.fullmatch(r'\w+', voice):
            return none
        topic = topic.strip().strip('"')
        if not re.fullmatch(r'\w+', topic):
            return none
        # Only an actor BASE is a speak-as identity; anything else in that slot
        # is a flag or a stray token.  And only a real DIAL is a topic.
        if self.xref:
            fid = self.xref.edid_to_formid.get(voice.lower(), '')
            if not fid or self.xref.record_type.get(fid, '') not in ('NPC_',
                                                                     'CREA'):
                return none
            tfid = self.xref.edid_to_formid.get(topic.lower(), '')
            if not tfid or self.xref.record_type.get(tfid, '') != 'DIAL':
                return none
        speaker = _safe_property_name(
            f'TES4Voice_{ref_name.lower()}_{voice.lower()}')
        self.sc.property_refs[speaker] = 'ObjectReference'
        return speaker, in_head

    def _mark_topic_property(self, name: str) -> None:
        """Type `name` as a Topic, but only if it really names a DIAL.

        Say/SayTo/StartConversation take a topic, and TES4 EditorIDs are not
        unique across record types: Morroblivion has CELLs named DagothSUr and
        KoalSCave with no DIAL of that name at all. Typing those `Topic`
        produced a property the VM refuses to bind ("is not the right type"),
        which reads None. Leave the name untyped instead -- the AddTopic unlock
        global is what actually drives the topic.
        """
        key = (name or '').strip()
        if not key:
            return
        # A script LOCAL of the same name wins.  DABoethiaCageOpenScript01
        # declares `Short Salutation` next to `say Salutation`: TES4 resolved
        # the argument as the topic, but the DECLARATION is what the rest of
        # the body reads and writes, so typing the property Topic left
        # `Salutation = 1` comparing a Topic against an Int.
        if key.lower() in self.sc.var_types or key.lower() in self.sc.local_vars:
            return
        if self.xref:
            fid = self.xref.edid_to_formid.get(key.lower(), '')
            rtype = self.xref.record_type.get(fid, '') if fid else ''
            if rtype and rtype != 'DIAL':
                return
        self.sc.property_refs[key] = 'Topic'

    def _papyrus_type_for(self, fid: str, rtype: str) -> str:
        """Papyrus property type for a record, as the IMPORTER writes it.

        `_record_type_to_papyrus` maps the TES4 signature, which is right until
        the importer changes the signature on the way out. A BOOK carrying an
        ENAM becomes a SCRL (see project_enchanted_book_is_a_scroll), so a
        `Book` property naming one cannot bind and reads None in-game.
        """
        ptype = _record_type_to_papyrus(rtype)
        if (ptype == 'Book' and self.xref
                and fid in getattr(self.xref, 'enchanted_books', ())):
            return 'Scroll'
        return ptype

    def _script_type_binds(self, ptype: str, fid: str) -> bool:
        """Whether an attached script class may stand in for `ptype` HERE.

        Base-object types normally cannot (the VM refuses the base record —
        see script_type_may_override), but a scripted world object (ACTI/LIGH)
        with exactly ONE placed ref can: the property binder redirects the
        binding to that ref, which carries the script instance. Without this,
        the base gate stripped cross-script variables off unique activators —
        `SE01Metronome.weatherVAR` and the SE11 trigzone stopped compiling.
        Inventory item types (ARMO/WEAP/...) stay excluded even with a lone
        world placement, because their properties mean the BASE (AddItem /
        RemoveItem), never that placement.
        """
        if script_type_may_override(ptype):
            return True
        return (self.xref.record_type.get(fid, '') in ('ACTI', 'LIGH')
                and bool(self.xref.unique_placed_ref(fid)))

    def _register_cell_family(self, name: str, cells: list,
                              exterior: list = None) -> str:
        """Record a GetInCell prefix family and return its helper's name.

        See the GetInCell handler in _emit_function for why a family exists at
        all.  Helpers are keyed case-insensitively so `Chorrol` and `chorrol`
        (both appear in vanilla scripts) share one function.

        `cells` are INTERIOR EditorIDs (compared as Cell properties);
        `exterior` are (worldspace EditorID, x, y) grid keys.
        """
        key = _safe_property_name(name)
        existing = self.sc.cell_families.get(key.lower())
        if existing is None:
            self.sc.cell_families[key.lower()] = (key, list(cells),
                                                list(exterior or []))
            # Register the helper's properties HERE, not when its body is
            # emitted: the declarations are collected once the body is
            # converted, so a ref added later never gets declared and the
            # helper cites an undefined identifier.
            for cell in cells:
                if cell:
                    self.sc.property_refs[cell] = 'Cell'
            for wrld, _x, _y in (exterior or []):
                if wrld:
                    self.sc.property_refs[wrld] = 'WorldSpace'
        else:
            key = existing[0]
        return f'TES4_IsIn{key}'

    # -----------------------------------------------------------------------
    # Private
    # -----------------------------------------------------------------------




    # The condition under which a placed reference's TES4 `begin GameMode` body
    # would run, used at every site that arms the OnUpdate poll for an
    # object/actor script.
    #
    # The gate is CELL-SCOPED (attached parent cell), matching TES4, with
    # Is3DLoaded() only as a fast path.  It must satisfy TWO independent
    # requirements, and an implementation meeting just one is silently broken:
    #
    # 1. Never throw on a held item (see the container note below).
    # 2. Stay true for a reference with no 3D.  A disabled ref, or one whose
    #    own poll body calls Disable(), keeps ticking in TES4.  Gating on 3D
    #    alone deadlocks both the self-ENABLE idiom (~200 Nehrim refs incl.
    #    Celebro) and the self-DISABLE one — Nehrim MQ00LichtScript disables
    #    itself, then five seconds later fires the plugin's only
    #    `SetStage MQ00 2`, whose result script holds the only
    #    EnablePlayerControls.  A 3D gate pins that quest at stage 1 with the
    #    player's controls locked forever.
    #
    # This was once reverted to a 3D-only gate to chase a CharacterGen
    # regression (Valen Dreth not reaching his taunt marker).  That was a
    # misattribution: Dreth is fixed by the UNGATED OnLoad emitted above, which
    # this gate does not affect.
    # 🛑 NOT a bare Is3DLoaded().  An item sitting INSIDE A CONTAINER has no
    # 3D and no parent cell, and calling Is3DLoaded() on it raises
    #   "Unable to call Is3DLoaded - no native object bound to the script
    #    object, or object is of incorrect type"
    # which ABORTS THE WHOLE OnUpdate EVENT AT THAT LINE -- including the
    # RegisterForSingleUpdate that keeps the poll alive.  The script is then
    # dead for the rest of the save.  Measured in the user's Papyrus.0.log
    #: 17 aborted OnInit/OnUpdate passes on the CharacterGen
    # Blades equipment inside Glenroy (1A032A16) and Renault (1A032A15).
    #
    # TES4Polyfill.SafeGameModeGate does the container test FIRST
    # (GetParentCell() == None is safe on an inventory item and never throws)
    # and only calls Is3DLoaded() on a reference that is actually in a cell.
    # 1,111 converted scripts gate their poll re-arm on this, so a throw here
    # is a silent, permanent loop death across the whole plugin.
    _GAMEMODE_GATE = 'TES4Polyfill.SafeGameModeGate(Self)'

    def _get_update_interval(self) -> str:
        if self.sc.uses_getsecondspassed:
            return '0.1'
        # A script that drives a spoken line with a timer (`set T to Say ...`)
        # ticks fast: its `T <= 0` guard is what starts the next line, so the
        # tick is pure dead air between lines (TES4 ran it every frame).
        #
        # 0.1s was tried and measurably LENGTHENED the gaps, so
        # it was set to 0.25s.  That measurement was taken when every SayLine
        # also blocked on Utility.Wait(0.05) for its claim handshake and
        # Utility.Wait(0.25) after any busy wait, and when fragments blocked
        # the dispatch path -- the VM was saturated by the Say path itself and
        # extra poll passes queued behind it.  All three are gone, so the
        # contention that made a faster tick counterproductive is gone with
        # them, and 0.15s buys back most of the tick latency without
        # returning to the 0.1s that was measured as too aggressive.
        if self.sc.uses_say_timer:
            return '0.15'
        if self.sc.uses_timer:
            return '0.25'
        return '0.5'


    def _parse_source(self, source: str):
        """Parse Oblivion source into (variables, blocks).

        `blocks` is `(type, filter, STATEMENT NODES)`.  It used to hand back
        the SOURCE LINES between each `begin` and its `end`, reconstructed by
        counting keywords -- so the converter parsed the script, threw the
        body away and converted text line by line.  That is the whole reason
        the string layer existed: a line on its own carries no structure, so
        nesting, block balance and dead-code-after-return all had to be
        re-derived from the emitted output afterwards.

        The parser already owns the body, so it is handed over directly.
        """
        from script_convert.tes4.parser import Mode, parse
        try:
            tree = parse(source, Mode.SCRIPT)
        except Exception:
            self._tree = None
            return [], []
        self._tree = tree
        # OBSE `array_var` declarations, so a read of one can be neutralised:
        # the type maps to String for want of a Papyrus equivalent, which
        # otherwise lands a String in whatever the target is declared as.
        self.sc.obse_arrays = {v.name.lower() for v in tree.variables
                             if v.vtype.lower() == 'array_var'}
        variables = [(v.vtype, v.name) for v in tree.variables]
        return variables, [(b.btype, b.filter, b.body) for b in tree.blocks]

    def _current_event_actor_param(self) -> str:
        """Name of the Actor parameter of the event being converted, if any.

        Used for TES4 calls whose implicit subject is "whoever this event is
        about" — e.g. bare GetContainer inside OnEquipped is the equipping
        actor, which is exactly akActor.
        """
        ev = self._current_event or ''
        m = re.search(r'\bActor\s+(ak\w+)', ev)
        return m.group(1) if m else ''

    # TES4 GMSTs a script writes at runtime → the Skyrim ACTOR VALUE that
    # produces the same observable change on the actor.  Skyrim has no vanilla
    # Papyrus GMST *writer* (only readers), so a global setting cannot be
    # changed without SKSE; every one of these settings does, however, have a
    # per-actor equivalent the engine already reads.
    #
    # Names verified against Skyrim.esm's AVIF records and the actor-value
    # table in SkyrimSE.exe.  Note fJumpHeightMax does NOT exist in Skyrim at
    # all (only fJumpHeightMin) — scripts that set both are writing one real
    # setting and one that Oblivion had and Skyrim dropped.

    def _gamesetting_write(self, setting: str, value: str, extends: str) -> str:
        """A runtime GMST write, re-expressed as the actor value it changes."""
        av = GMST_TO_ACTOR_VALUE.get(setting.lower())
        if not av:
            return (f';TODO: SetNumericGameSetting {setting} {value}  '
                    f';no vanilla Papyrus GMST writer and no actor-value '
                    f'equivalent (SKSE Game.SetGameSetting* would be needed)')
        # ForceActorValue, not ModActorValue: the TES4 call SETS the value
        # outright, and a script that writes the same setting on every update
        # would otherwise stack the modifier without bound.
        target = self._actor_target_for_gamesetting(extends)
        return f'{target}.ForceActorValue("{av}", {value})'

    def _actor_target_for_gamesetting(self, extends: str) -> str:
        """The actor a runtime game-setting write should apply to.

        These settings were GLOBAL in Oblivion, so every script that writes one
        is changing the world for whoever is affected — in practice the player,
        which is who casts the scroll or wears the ring.  A magic-effect script
        has a real target parameter and uses it; anything else applies to the
        player, matching the global's practical scope.
        """
        if extends == 'ActiveMagicEffect':
            param = self._current_event_actor_param()
            if param:
                return param
        return 'Game.GetPlayer()'

    _FALL_RESTORE = 'TES4Polyfill.RestoreFallDamage()'

    def _append_fall_damage_restore(self, out: list, extends: str) -> list:
        """Pair every SuppressFallDamage() with a restore when the effect ends.

        `TES4Polyfill.SuppressFallDamage()` (the ResetFallDamageTimer
        conversion) writes fJumpFallHeightMin, a GLOBAL game setting.  Oblivion
        needed no teardown because ResetFallDamageTimer only cleared a
        per-actor accumulator; leaving the Skyrim equivalent set would disable
        fall damage permanently.

        The restore goes in whichever teardown event the script already has —
        OnEffectFinish for a magic-effect script, otherwise OnUpdate's exit —
        and a fresh OnEffectFinish is synthesized when the script has none.
        """
        idx = next((i for i, line in enumerate(out)
                    if line.startswith('Event OnEffectFinish(')), None)

        if idx is not None:
            # Restore the SAME actor the suppression applied to, which is the
            # teardown event's own target parameter.
            m = re.search(r'\bActor\s+(ak\w+)', out[idx])
            actor = m.group(1) if m else ''
            end = next((i for i in range(idx + 1, len(out))
                        if out[i] == 'EndEvent'), None)
            if end is not None:
                out.insert(end, f'  TES4Polyfill.RestoreFallDamage({actor})')
                return out

        # No teardown event at all: an ActiveMagicEffect always gets one, so
        # synthesize it rather than leaving the suppression permanent.
        if extends == 'ActiveMagicEffect':
            out.append('Event OnEffectFinish(Actor akTarget, Actor akCaster)')
            out.append('  TES4Polyfill.RestoreFallDamage(akTarget)')
            out.append('EndEvent')
            out.append('')
        return out


    @staticmethod
    def _onactivate_consumes(blocks) -> bool:
        """True when an OnActivate body has a path that CONSUMES activation.

        In TES4 the block replaces default activation, so any path that does
        not execute a bare `Activate` swallows the click.  A body whose bare
        `Activate` sits at nesting depth 0 runs it on every path -- a pure
        passthrough (AutoClosingDoor et al.) that needs no blocking.  Only a
        missing or conditionally-guarded `Activate` needs BlockActivation for
        Skyrim to honour the consume.  (`X.Activate` -- activating some OTHER
        ref -- is not a passthrough and does not count.)

        "At depth 0" is a TREE question: a statement in the block's own body
        rather than inside an If or a While.  Counting `if`/`endif` keywords
        across source text answered it before.
        """
        consumes = False
        for btype, _bf, body in blocks:
            if btype != 'onactivate':
                continue
            top_level_activate = any(
                isinstance(st, _tes4_nodes.ExprStmt)
                and st.expr.called == 'activate'
                and st.expr.receiver is None
                for st in body or ())
            if not top_level_activate:
                consumes = True
        return consumes


    def _block_filter_guard(self, block_type: str,
                            block_filter: str) -> 'str | None':
        """Compile a TES4 block filter into a Papyrus condition, or '' if none.
        Returns None when a real filter exists but CANNOT be expressed — the
        caller must then keep the body commented out rather than run it
        unconditionally for every event.

        `begin OnEquip player` fires the block ONLY when the player equips the
        item; `begin OnPackageDone SomePkg` only when that package ends.  Papyrus
        events carry no filter, so the restriction becomes an `If` around the
        body, testing the event parameter that holds the filtered object (see
        BLOCK_FILTER_PARAM).  Without this the block runs for every actor /
        container / package, which is how an item's "you can't equip this"
        message ended up firing for NPCs the moment they loaded in.
        """
        if not block_filter:
            return ''
        target = BLOCK_FILTER_PARAM.get(block_type)
        if not target:
            # MenuMode's argument is a menu ID and OnAlarm's is a crime type —
            # neither names an object, and neither block has a parameter to
            # filter on.  Nothing to guard.
            return ''
        param, param_type = target

        name = block_filter.strip()
        if name.lower() == 'player':
            return f'{param} == Game.GetPlayer()'

        # Anything else is a form EditorID. Bind it as a property and compare.
        if not re.match(r'^\w+$', name) or not self.xref:
            return ''
        fid = self.xref.edid_to_formid.get(name.lower(), '')
        if not fid:
            return ''
        rtype = _record_type_to_papyrus(self.xref.record_type.get(fid, ''))

        # The comparison has to typecheck against the event parameter.  On an
        # ACTOR script `begin OnEquip SomePotion` filters the ITEM equipped, not
        # the equipper — but Skyrim's OnEquipped only hands us the actor, so
        # there is nothing to test the item against.  Emitting the comparison
        # anyway gives `akActor == SomePotion`, which will not compile.
        param_is_actor = param_type == 'Actor'
        filter_is_actor = rtype in ('Actor', 'ObjectReference')
        if param_is_actor and not filter_is_actor:
            # (no Papyrus parameter carries the item; the filter is lost)
            return ''
        if param_type in ('ObjectReference', 'Actor', 'Form'):
            ptype = rtype if filter_is_actor else param_type
        else:
            ptype = param_type
        safe = _safe_property_name(name)
        existing = self.sc.property_refs.get(safe)
        if existing and existing != ptype:
            # Already bound at a TES4_* script type: those extend Actor/
            # ObjectReference, so the comparison against the event parameter
            # still compiles — keep the existing binding and emit the guard.
            # (Dropping it here ran CGRenote's `begin onHit CGAssassin01Ref`
            # bodies on EVERY hit: any stray arrow killed her and jumped
            # CharacterGen's stages out of order.)
            if (existing.startswith('TES4_')
                    and ptype in ('Actor', 'ObjectReference', 'Form')):
                return f'{param} == {safe}'
            # A base record compares to a `Form` parameter perfectly well, and
            # the body converts BEFORE the guard, so the property is normally
            # already bound at its own narrow type by the time we get here.
            if param_type == 'Form' and existing in _BASE_OBJECT_PAPYRUS:
                return f'{param} == {safe}'
            # Genuinely incomparable (e.g. bound as Faction/GlobalVariable).
            # An unguarded body is WRONG for every event the filter excluded —
            # signal the caller to keep the body but not execute it.
            return None
        self.sc.property_refs[safe] = ptype
        return f'{param} == {safe}'


    #: Statement kinds the node path owns.  Everything else -- the OBSE
    #: ref-walk, `foreach`, an array element write -- still needs converter
    #: state the tree does not carry, so it falls through to the chain.


    # Papyrus functions that return Bool where the TES4 original returned an
    # Int 0/1.  Oblivion scripts freely write `getdetected X > 0` / `getdead ==
    # 0`, but Papyrus refuses to order or add a Bool ("cannot relatively compare
    # variables of type bool", "cannot add a bool to a int"), so these need an
    # explicit `as Int` wherever they meet a number.
    # (name list defined below, shared with _BOOL_CMP_RE)

    # A Bool-returning call placed in a RELATIONAL comparison against a number.
    # `X.IsDead() > 0` must become `(X.IsDead() as Int) > 0`.  The argument list
    # may itself contain a call (`IsDetectedBy(Game.GetPlayer())`), so the arg
    # pattern allows one level of nested parentheses.
    # DERIVED from PAPYRUS_BOOL_FUNCTIONS, not written out again.  This was a
    # second hand-kept list of the same fact and the two had drifted apart by
    # twelve names, so a Bool got its `as Int` or not depending on which list
    # the code path consulted -- `Temp = Player.IsDetectedBy(x)` compiled or
    # did not for that reason alone.  Longest-first so the alternation cannot
    # match a prefix of a longer name.
    _BOOL_FUNC_NAMES = '|'.join(
        sorted((re.escape(n) for n in PAPYRUS_BOOL_FUNCTIONS),
               key=len, reverse=True))
    _ARGS = r'(?:[^()]|\([^()]*\))*'      # args, allowing one nesting level
    _BOOL_CMP_RE = re.compile(
        r'((?:\w+(?:\(' + _ARGS + r'\))?\.)*'              # optional receiver chain
        r'(?:' + _BOOL_FUNC_NAMES + r')'
        r'\s*\(' + _ARGS + r'\))'                          # the call itself
        r'(\s*(?:>=|<=|>|<)\s*-?\d+(?:\.\d+)?)',           # relational op + number
        re.IGNORECASE)

    @staticmethod
    def _cast(expr: str, ptype: str) -> str:
        """Cast `expr` to `ptype`, unless it is already cast to it.

        Papyrus rejects a doubled cast (`X as Int as Int`) outright, and several
        handlers emit their own cast before the caller adds one.

        `as` binds TIGHTER than arithmetic, so a compound expression is
        parenthesised first: `totalTime - timer / 60 as Int` casts only the
        `60` and leaves the subtraction Float -- which is the very error this
        exists to prevent.  A trailing cast only counts as "already cast" when
        it covers the WHOLE expression: `100.0 - X.GetValue() as Int` ends in
        `as Int` while casting just the right operand, so the sum stays Float.
        """
        text = expr.strip()
        if (re.search(rf'\bas\s+{ptype}\s*$', text, re.IGNORECASE)
                and not _needs_parens(text)):
            return text
        if _needs_parens(text):
            return f'({text}) as {ptype}'
        return f'{text} as {ptype}'

    # The hour-boundary guard these scripts use: `GameHour >= X.98` /
    # `GameHour <= X.02`, i.e. a window HALF_WINDOW_GAME_HOURS wide either
    # side of the top of the hour.
    _HOUR_WINDOW_GAME_HOURS = 0.04
    # Both games ship GLOB 0x3A TimeScale = 30 by default, and every vanilla
    # Oblivion chime script is tuned against that.
    _DEFAULT_TIMESCALE = 30.0
    _GAMEHOUR_WINDOW_RE = re.compile(
        r'\bGameHour\b\s*(?:>=|<=)\s*\d+\.\d+', re.IGNORECASE)

    # `timer <= -5` — the chime latch's expiry test.  The sentinel is negative
    # because the countdown runs past zero; its magnitude is how many REAL
    # seconds the latch holds.
    _LATCH_EXPIRY_RE = re.compile(
        r'^(?P<head>.*?\b\w[\w.]*\s*<=\s*)-(?P<secs>\d+(?:\.\d+)?)(?P<tail>\s*\)?\s*)$')


    # Engine-owned globals that Oblivion declares `short` but that carry a
    # genuine fractional value at runtime (and that Skyrim declares float).
    # GameHour is 0x00000038 in both games; Oblivion's own bell scripts bracket
    # the top of the hour with `>= X.98 / <= X.02` windows, which only ever
    # match because the read is fractional.
    # Deliberately NOT here: TimeScale (Skyrim FNAM=115, genuinely short) and
    # GameDaysPassed.  Skyrim declares GameDaysPassed float (FNAM=102, Ord('f')
    # per xEdit's GLOB definition), but OBLIVION declares it Short
    # (GLOB 00000039, FNAM.Type=s), so the source scripts only ever saw whole
    # days and the `as Int` truncation is what REPRODUCES their behaviour.  That
    # matters beyond the day-of-week idiom: 72 lines across 28 scripts compare
    # it against script floats, several by exact equality
    # (MS39Script: `GameDaysPassed == (CurrentDay + 1)`), which only ever
    # matched in Oblivion because both sides were whole numbers.
    _FRACTIONAL_ENGINE_GLOBALS = frozenset(('gamehour',))

    def _global_read(self, safe: str) -> str:
        """Emit a GlobalVariable read, casting to Int only when that is lossless.

        A blanket `as Int` truncates float globals, which silently turns any
        fractional comparison into a whole-number one.  For GameHour that
        collapsed each `>= 23.98 || <= 0.02` hour-boundary window into an
        always-true test, so the guarded body ran every single frame — the
        Erodans-Kapelle chapel bell (and Oblivion's BellTowerScript) rang
        continuously instead of once on the hour.
        """
        low = safe.lower()
        gtype = ''
        if self.xref:
            gtype = self.xref.global_types.get(low, '')
        if low in self._FRACTIONAL_ENGINE_GLOBALS or gtype == 'f':
            return f'{safe}.GetValue()'
        return f'{safe}.GetValue() as Int'


    # ObjectReference event parameter names that may need Actor cast

    # Functions that return ObjectReference in Papyrus
    _OBJREF_RETURNING = re.compile(
        r'(?:GetLinkedRef|PlaceAtMe|GetParentRef|PlaceActorAtMe|GetEditorLocation|'
        r'GetItemInSlot|GetCombatTarget)\s*\(', re.IGNORECASE)



    def _convert_ref(self, name: str, extends: str, as_receiver: bool = False) -> str:
        """Convert an Oblivion reference name to Papyrus.

        `as_receiver` marks the name as the target of a method call.  A local
        variable can shadow the `player` keyword in a VALUE position but never
        as a receiver — a `Short` has no methods — so the keyword wins there.
        """
        # Oblivion's parser accepts quotes around any EditorID, and Nehrim's
        # authors use them constantly (173 sites: `SetStage "MQ01Tate" 20`,
        # `GetStage "NQ00Karick"`, `StartQuest "NQ05"`,
        # `AddScriptPackage "..."`).  The quotes reached the property namer,
        # which turned each `"` into `_` — so `"MQ01Tate"` became the property
        # `_MQ01Tate_` while the same script's UNQUOTED `GetStage MQ01Tate`
        # became `MQ01Tate`.  Only the unquoted spelling matched an EditorID,
        # so only it was bound in the VMAD; `_MQ01Tate_` stayed None and every
        # `_MQ01Tate_.SetStage(...)` threw at runtime.  That stranded MQ01Tate
        # at stage 15 — it could never reach stage 40, which is the only thing
        # that starts MQ01, so MQ00 could never be completed either.
        name = _QUOTED_NAME_RE.sub(r'\1', name.strip())
        low = name.lower()
        # A declared local otherwise wins over the built-in keywords, including
        # `player`.  StartCelleAufzugTriggerZone01Script declares `Short Player`
        # as its own trigger flag; mapping that to Game.GetPlayer() turned
        # `Set Player to 1` into the un-assignable `Game.GetPlayer() = 1`.
        # (The same precedence is applied further down for EditorIDs.)
        _is_player_kw = low in ('player', 'playerref')
        if ((low in self.sc.local_vars or low in self.sc.var_types)
                and not (as_receiver and _is_player_kw)):
            return _safe_property_name(name)
        if _is_player_kw:
            return 'Game.GetPlayer()'
        if low in SELF_NAMES:
            return self._self_reference(extends)

        # Known TES4 globals -> property
        if low in KNOWN_GLOBALS:
            canonical = _canonical_global(name)
            self.sc.property_refs[canonical] = 'GlobalVariable'
            return canonical

        if '.' in name:
            parts = name.split('.', 1)
            ref_part = self._convert_ref(parts[0], extends)
            return f'{ref_part}.{_safe_property_name(parts[1])}'

        if self.xref.is_quest_ref(name):
            # Use the canonical EditorID (original case from export) as the key
            # so this matches what _add_scro_ref stores (both use formid_to_edid).
            canon_fid = self.xref.edid_to_formid.get(low, '')
            canon_edid = self.xref.formid_to_edid.get(canon_fid, name) if canon_fid else name
            # Through _safe_property_name like every other ref: an Oblivion quest
            # EditorID can collide with a Skyrim script name (MS14), and emitting
            # it raw here left the body calling `MS14.SetStage()` while the
            # declaration said `myMS14` — the CK then reads MS14 as the TYPE
            # ("cannot call the member function SetStage ... on a type").
            safe = _safe_property_name(canon_edid)
            self.sc.property_refs[safe] = self.xref.get_quest_script_type(name)
            return safe

        # Local variables take precedence over game form EditorIDs (name collision)
        if low in self.sc.local_vars or low in self.sc.var_types:
            return _safe_property_name(name)

        # Check if this is any known EditorID from the export.
        #
        # A Papyrus identifier may not begin with a digit, so a TES4 script that
        # names a record with a leading digit — Morroblivion names almost
        # everything `0<name>` — reaches here already stripped
        # (`dwemerUshieldUbattleUunique` for `0dwemerUshieldUbattleUunique`).
        # The direct lookup misses, no property gets DECLARED, and the name then
        # survives into the body as an undefined identifier that fails the whole
        # script.  resolve_property_formid already reverses the strip when
        # BINDING the VMAD, so without the same reversal here the two disagreed:
        # the binder had a FormID for a property the script never declared.
        fid = self.xref.edid_to_formid.get(low, '')
        if not fid:
            fid = _digit_stripped_formid(self.xref, low)
        if not fid:
            # Stale source spelling: recover the record the ORIGINAL compiler
            # bound, from this record's SCRO table (see register_scro_alias_pool).
            alias = self._scro_alias_for(name)
            if alias:
                name, low = alias, alias.lower()
                fid = self.xref.edid_to_formid.get(low, '')
        if fid:
            # Use canonical EditorID (original case) as key to match _add_scro_ref
            canon_edid = self.xref.formid_to_edid.get(fid, name)
            rtype = self.xref.record_type.get(fid, '')
            ptype = self._papyrus_type_for(fid, rtype)
            # Prefer attached script type over generic Actor/ObjectReference
            # so cross-script property access works (e.g., NPCRef.rent).
            # Base-object types (Armor/Weapon/Potion/...) are excluded: the VM
            # refuses to bind an ObjectReference-derived script class to a base
            # record, and the property then reads None. A unique-placed
            # ACTI/LIGH is the exception — the binder redirects to its ref.
            script_type = self.xref.get_record_script_type(name)
            if script_type and self._script_type_binds(ptype, fid):
                ptype = script_type
            safe = _safe_property_name(canon_edid)
            # Don't downgrade a more specific type (e.g., Actor from
            # _resolve_self_ref) back to a generic one (ObjectReference).
            cur = self.sc.property_refs.get(safe, '')
            _generic = ('', 'ObjectReference')
            if not cur or ptype not in _generic or cur in _generic:
                self.sc.property_refs[safe] = ptype
            return safe

        return _safe_property_name(name)

    def arg_srcs(self) -> list:
        """Every argument as AUTHORED source text."""
        return [_expr.emit_source(a).strip() for a in self._arg_nodes]


    def arg_sources(self) -> list:
        """The current call's arguments as unconverted TES4 source text."""
        return [_expr.emit_source(a) for a in self._arg_nodes]

    def arg_src(self, n: int, default: str = '') -> str:
        """The nth argument as AUTHORED source text, or `default` if absent."""
        nodes = self._arg_nodes
        if n >= len(nodes):
            return default
        return _expr.emit_source(nodes[n]).strip() or default

    def arg_expr(self, n: int, extends: str, default: str = '') -> str:
        """The nth argument CONVERTED to Papyrus, or `default` if absent."""
        nodes = self._arg_nodes
        if n >= len(nodes):
            return default
        return _expr.emit(self, nodes[n], extends)



    # Actor values that TES4 stores on a 0-100 scale but TES5 defines as a small
    # ENUM (xEdit wbDefinitionsCommon.pas: wbAggressionEnum 0-3,
    # wbConfidenceEnum 0-4, wbAssistanceEnum 0-2, wbMoodEnum 0-8, and Morality
    # 0-3).  Writing the raw TES4 number is rejected outright by the engine —
    # `SetActorValue("Aggression", 100)` logs "attempt made to set illegal
    # value" and leaves the trait UNCHANGED, so every scripted "now turn
    # hostile" beat silently did nothing.
    # Value is the inclusive maximum for each trait.

    # Descending (floor, tier) ladders for the actor values whose TES5 tier is
    # NOT a proportional bucket.  The last row must be a catch-all, since the
    # caller has already rejected `raw < 0`.  The reasoning for each threshold
    # is in `_scale_enum_av` -- these mirror `_convert_aidt` in
    # tes5_import/record_types/actors.py so a scripted change lands on the same
    # tier the NPC's AIDT was converted to.
    # TES4 aggression is only half of a PER-TARGET rule: an actor
    # attacks a target when disposition(actor->target) < aggression - 5
    # (UESP Oblivion:Aggression).  TES5 aggression is a GLOBAL tier
    # naming which reaction class it attacks, so the TES4 number cannot
    # be read on its own — the disposition it has to beat decides the
    # tier.
    #
    # Collapsing everything from 6..105 onto tier 2 was wrong because
    # tier 2 is "attacks enemies AND NEUTRALS on sight", and the player
    # is a Neutral to most factions.  CharacterGen stage 22 does
    # `GlenroyRef.setav aggression 10` purely so the Emperor's guards
    # will fight the assassins; 10 only beats a disposition below 5,
    # and the guards' disposition toward the player is ~47, so in
    # Oblivion they never turn on you.  Converted to tier 2 they
    # attacked the player on sight from stage 22 onward.  UESP names
    # this exact failure: "a guard would attack the whole town if their
    # aggression were sufficiently raised".
    #
    # _ONSIGHT_AGGRESSION is the aggression needed to beat an ordinary
    # NPC disposition and so genuinely mean "hostile to bystanders".
    # It matches the record path's margin test, which subtracts
    # disposition before it will grant tier 2: there, a default actor
    # (disposition ~= Personality 50) needs (aggr-5) - 50 >= 10, i.e.
    # aggression >= 65.  Values below that are Oblivion's "defend
    # yourself / join this specific fight" idiom and belong on tier 1,
    # which attacks declared Enemies only and leaves Neutrals alone —
    # the faction graph then picks the actual opponent, exactly as the
    # TES4 rule did.  Census of the 227 scripted calls in Oblivion.esm:
    # 38 land on 0, 76 on tier 1 (10/20/25/30/40/50), 113 on tier 2+
    # (90/100 = the real "now attack anyone" beats).

    def _scale_enum_av(self, sk_av: str, value_src: str):
        """Map a TES4 0-100 trait value onto its TES5 enum tier.

        Returns None when this is not an enum-valued actor value, or when the
        operand is not a literal (a variable cannot be bucketed at conversion
        time), so the caller falls back to normal expression conversion.
        """
        max_tier = ENUM_ACTOR_VALUES.get(sk_av.lower())
        if max_tier is None:
            return None
        literal = value_src.strip().rstrip(',').strip()
        if not re.match(r'^-?\d+(?:\.\d+)?$', literal):
            return None
        raw = float(literal)
        # A value already inside the enum range is a deliberate Skyrim-style
        # tier (or the TES4 default 0) — pass it through untouched rather than
        # re-bucketing it and changing behaviour.
        if 0 <= raw <= max_tier:
            return str(int(raw))
        if raw < 0:
            return '0'
        # Mirror the record-side thresholds in tes5_import/record_types/
        # actors.py so a scripted change lands on the same tier the NPC's AIDT
        # was converted to: <=5 never initiates, >=106 attacks everyone.
        ladder = ENUM_AV_LADDERS.get(sk_av.lower())
        if ladder is None:
            # Generic 0-100 → 0..max_tier proportional bucket.
            tier = int(round((min(raw, 100.0) / 100.0) * max_tier))
        else:
            tier = next(t for floor, t in ladder if raw >= floor)
        return str(max(0, min(max_tier, tier)))

    def _faction_reaction_call(self, f1: str, f2: str, amount_src: str,
                               is_mod: bool, extends: str):
        """Map a TES4 faction disposition amount onto SetEnemy/SetAlly.

        TES4 dispositions run -100..+100.  Skyrim only stores a four-value
        Group Combat Reaction, so the amount is bucketed onto the tier that
        preserves the intent:

            <= -50   Enemy    (`setfactionreaction X Y -100` = "now hate them")
            <  0     Neutral  (a mild grudge is not open warfare)
            == 0     Neutral  (explicitly clearing a relation)
            >  0     Friend   (goodwill between two DIFFERENT factions)

        Positive amounts stop at Friend and never reach Ally.  A TES4
        disposition is a 0-100 scalar meaning "likes them more"; TES5's Ally is
        a hard contract that makes members ASSIST each other into combat (UESP
        Skyrim:Factions — reaction combines with aggression and assistance to
        decide who joins a fight).  Since `setfactionreaction` always names two
        DIFFERENT factions, promoting its positive amounts to Ally wired
        bystanders into other people's fights.  Ally is reserved for a
        faction's relation to itself, which only the FACT record path emits
        (see convert_FACT in tes5_import/record_types/actors.py).

        Returns None when the amount is not a literal, so the caller can emit a
        runtime branch instead.  ModFactionReaction shifts an existing value we
        cannot read at conversion time, so only its SIGN is honoured — that is
        the part vanilla scripts actually depend on.

        A flip naming the TES4 PlayerFaction is mirrored onto the vanilla
        PlayerFaction: the runtime player was never a member of the
        converted record, so the original write reaches nobody.  The mirror
        covers all three tiers — the neutral clear included, because TES4
        uses `setfactionreaction X PlayerFaction 0` mid-scene to stand a
        group down from hunting the player (CharacterGen stage 23), and a
        clear that reaches nobody leaves the real player hunted.

        Nothing else is pushed here.  SetEnemy/SetAlly write the same Group
        Combat Reaction enum vanilla's own scripted battles run on; with the
        package interrupt flags authorising combat behaviour (see
        pack_converter.DEFAULT_INTERRUPT) the engine initiates the fight
        itself, exactly as it does for its own factions.
        """
        literal = amount_src.strip().rstrip(',').strip()
        if not re.match(r'^-?\d+(?:\.\d+)?$', literal):
            return None
        amount = float(literal)

        def _mirror(mode: int) -> str:
            if f1.lower() == 'playerfaction':
                return ('\n  TES4Polyfill.MirrorPlayerFactionRelation('
                        f'{f2}, {mode})')
            if f2.lower() == 'playerfaction':
                return ('\n  TES4Polyfill.MirrorPlayerFactionRelation('
                        f'{f1}, {mode})')
            return ''

        if is_mod:
            # A relative nudge: treat any negative shift as souring the
            # relation and any positive one as improving it.
            if amount < 0:
                return f'{f1}.SetEnemy({f2}, false, false)' + _mirror(1)
            if amount > 0:
                return f'{f1}.SetAlly({f2}, true, true)' + _mirror(2)
            return f';{f1}.ModReaction({f2}, 0)  ;no-op'
        if amount <= -50:
            return f'{f1}.SetEnemy({f2}, false, false)' + _mirror(1)
        if amount <= 0:
            # Neutral: SetEnemy with the "self is neutral to other" bool set.
            return f'{f1}.SetEnemy({f2}, true, true)' + _mirror(0)
        return f'{f1}.SetAlly({f2}, true, true)' + _mirror(2)

    def _force_combat_call(self, ref: str, target: str) -> str:
        """Emit a TES4Polyfill.ForceCombat call with the conversion-owned
        enemy-faction pair that makes the fight stick for ANY actors.

        The two factions are records the import writes at fixed FormIDs
        (record-side mutual Enemy XNAM); the property names are registered
        in _WELL_KNOWN_PROPERTIES so the VMAD fill binds them.
        """
        self.sc.property_refs['TES4ForceCombatAttackers'] = 'Faction'
        self.sc.property_refs['TES4ForceCombatVictims'] = 'Faction'
        return (f'TES4Polyfill.ForceCombat({ref}, {target}, '
                'TES4ForceCombatAttackers, TES4ForceCombatVictims)')

    def _destroyed_formlist(self) -> str:
        """Register and name the conversion-owned destroyed-reference FormList.

        Skyrim has ObjectReference.SetDestroyed but NO reader for the flag, so
        TES4's `getdestroyed` has nothing native to read.  The import writes a
        FormList (TES4DestroyedRefs, fixed FormID) that the polyfill's
        SetDestroyed mirrors every write into and GetDestroyed queries.  A
        FormList rather than a script AV because AVs are Actor-only and the
        references TES4 destroys are doors, activators and statics.
        """
        self.sc.property_refs['TES4DestroyedRefs'] = 'FormList'
        return 'TES4DestroyedRefs'

    def _get_action_ref_param(self) -> str:
        """Return the correct event parameter for GetActionRef/IsActionRef.
        
        TES4 GetActionRef is available in every block. Papyrus scopes event params.
        Map to the appropriate parameter based on the current event being converted.
        """
        ev = self._current_event.lower()
        if 'onactivate' in ev or 'ontrigger' in ev:
            return 'akActionRef'
        if 'onequipped' in ev or 'onunequipped' in ev:
            return 'akActor'
        if 'onhit' in ev:
            return 'akAggressor'
        if 'ondeath' in ev:
            return 'akKiller'
        if 'oncontainerchanged' in ev:
            return 'akNewContainer'
        if 'oncombatstate' in ev:
            return 'akTarget'
        # OnUpdate/OnInit/other events have no action ref - use None as fallback
        if 'onupdate' in ev or 'oninit' in ev:
            return 'None'
        # Every other event -- OnUpdate, OnInit, OnLoad, OnDeath's siblings --
        # declares NO action ref, and naming one there is an undefined
        # identifier that fails the whole script.  TES4 answered GetActionRef
        # outside an activation block with the script's own subject.
        return 'Self'

    # Papyrus locals/parameters that are already actors — calling an actor-only
    # function on one must never mint a property for it.
    _NON_PROPERTY_REFS = frozenset({
        'self', 'akspeakerref', 'akactionref', 'akactor', 'aktarget',
        'akcaster', 'aksource', 'akaggressor', 'akdestination',
        'game.getplayer()', 'gettargetactor()', 'getactorreference()',
        'getcasteractor()', 'getowningquest()',
    })

    def _is_bindable_property(self, ref: str) -> bool:
        """True when `ref` is a bare identifier worth recording as Actor-typed.

        The receiver reaching the actor-only cast below is already CONVERTED, so
        it can be an expression (`Game.GetPlayer()`), a cast (`(x as Actor)`) or
        a fixed event parameter.  Registering one of those as a property ref put
        it through _safe_property_name and emitted a mangled, never-referenced
        declaration — `Actor Property Game_GetPlayer__ Auto` appeared in 511
        scripts, bound to nothing.

        Script-local variables DO belong here even though they never become VMAD
        properties: _property_refs is also what marks a local as Actor-typed, so
        it drives the `as Actor` downcast and the variable's declared type
        (AmuletofKings' `TempRef.UnequipItem`).  Excluding them broke 73 scripts.
        """
        if not ref or not re.match(r'^[A-Za-z_]\w*$', ref):
            return False
        return ref.lower() not in self._NON_PROPERTY_REFS

    def _packages_of_type(self, ref_name: str, pkg_type: int) -> list:
        """PACK EditorIDs backing a `GetCurrentAIPackage == <type>` test.

        A named receiver resolves through that record's own AIPackage list; a
        bare call runs on whatever actor attaches the script being converted,
        so it resolves through SCRI instead.  Empty when nothing resolves,
        which leaves the caller on the pre-existing no-op path.
        """
        if not self.xref:
            return []
        if ref_name and ref_name.lower() not in SELF_NAMES:
            return self.xref.get_actor_packages_of_type(ref_name, pkg_type)
        if self.sc.edid:
            return self.xref.get_script_owner_packages_of_type(
                self.sc.edid, pkg_type)
        return []

    # Music cues converted for THIS plugin: {source_rel -> cue EditorID}.
    # Populated by set_music_cues() from the same music_tracks.json the importer
    # builds MUSC from, so the two sides cannot drift apart.
    _music_cues: dict = {}

    @classmethod
    def set_music_cues(cls, cues: dict):
        """Register {lowercase source_rel -> MUSC EditorID} for StreamMusic."""
        cls._music_cues = dict(cues or {})

    def _music_cue_property(self, raw_path: str):
        """Papyrus property name for a StreamMusic argument, or None.

        `raw_path` is spelled as the TES4 script spells it: a backslash or
        forward-slash path, or a bare category name.  Normalise to the
        manifest's `source_rel` form (forward slashes, lowercase, no `data/`
        prefix, no extension) and look it up; a miss returns None so the caller
        emits the inert marker rather than binding a property to a record that
        does not exist.
        """
        if not raw_path or not self._music_cues:
            return None
        norm = raw_path.replace(chr(92), '/').strip().lower()
        while '//' in norm:
            norm = norm.replace('//', '/')
        norm = norm.lstrip('/')
        if norm.startswith('data/'):
            norm = norm[5:]
        if not norm.startswith('music/'):
            # A bare category (`StreamMusic dungeon`) names the whole folder.
            norm = 'music/' + norm
        stem = norm.rsplit('.', 1)[0]

        for key, edid in self._music_cues.items():
            if key.rsplit('.', 1)[0] == stem:
                self.sc.property_refs[edid] = 'MusicType'
                return edid
        return None

    def _resolve_self_ref(self, ref_name, extends, actor_func=False):
        """Resolve the reference for a function call.

        For ActiveMagicEffect scripts, bare (no ref) or Self-prefixed actor/objref
        functions need GetTargetActor() instead of Self.
        For TopicInfo scripts, bare actor functions need akSpeakerRef.
        For PlayerAlias scripts (a TES4 script attached to the Player BASE
        record, rehosted on a quest's PlayerRef alias — see
        object_scripts._build_player_alias_plan) Self is a ReferenceAlias, not
        an actor, so the implicit subject is the alias's filled reference.
        """
        if extends == PLAYER_ALIAS_EXTENDS and (
                not ref_name or ref_name.lower() in SELF_NAMES):
            return 'GetActorReference()' if actor_func else 'GetReference()'
        if ref_name:
            ref_low = ref_name.lower()
            # Self in ActiveMagicEffect/TopicInfo should redirect actor functions
            if actor_func and ref_low in SELF_NAMES:
                if extends == 'ActiveMagicEffect':
                    return 'GetTargetActor()'
                if extends == 'TopicInfo':
                    return '(akSpeakerRef as Actor)'
            # Upgrade property type to Actor when used with actor-only functions
            canon = self._convert_ref(ref_name, extends, as_receiver=True)
            if actor_func:
                # akSpeakerRef is a fixed ObjectReference parameter; cast it rather than upgrading
                if canon == 'akSpeakerRef':
                    return '(akSpeakerRef as Actor)'
                cur = self.sc.property_refs.get(canon, '')
                # Upgrading an existing ObjectReference entry is always right;
                # creating a NEW one is only right for a bare identifier (see
                # _is_bindable_property — `Game.GetPlayer()` must not become a
                # mangled `Game_GetPlayer__` property).
                if cur == 'ObjectReference' or (
                        cur == '' and self._is_bindable_property(canon)):
                    self.sc.property_refs[canon] = 'Actor'
                    # A LOCAL promoted here must be promoted in `var_types`
                    # too, or the declaration (which reads property_refs) says
                    # Actor while the assignment (which reads var_types) still
                    # thinks ObjectReference and skips its downcast.
                    low_canon = canon.lower()
                    if self.sc.var_types.get(low_canon) == 'ObjectReference':
                        self.sc.var_types[low_canon] = 'Actor'
                elif cur.startswith('TES4_'):
                    # The property is typed as the SCRIPT attached to the record
                    # it names (_add_scro_ref prefers that so cross-script
                    # variable reads work).  That type is not an Actor, so an
                    # actor-only call on it does not compile — but the object it
                    # binds to IS one, so cast at the call site rather than
                    # retyping the property and breaking the variable reads.
                    # (`KreoRef.EvaluatePackage()`, `MelvinTotRef.SetGhost()`,
                    # `NQ05Soldat01Ref.StartCombat()` — all actors carrying a
                    # converted script.)
                    return f'({canon} as Actor)'
                elif self.sc.var_types.get(canon.lower(), '')                         == 'ObjectReference':
                    # A script-LOCAL `ref` is declared ObjectReference, not
                    # Actor, and a local is not in `property_refs` at all --
                    # so an actor-only call on one emitted a bare
                    # `combatant1.SetActorValue(...)`, undefined on
                    # ObjectReference, which failed the whole script.
                    return f'({canon} as Actor)'
            return canon
        if actor_func:
            if extends == 'ActiveMagicEffect':
                return 'GetTargetActor()'
            if extends == 'TopicInfo':
                return '(akSpeakerRef as Actor)'
        return 'Self'

    # `(Self as Actor)` / `Self as Actor` inside a PlayerAlias script.  Matches
    # the parenthesised and bare forms; a bare `Self` on its own is left alone
    # (assigning the alias itself to an alias-typed property is legitimate).
    _PLAYER_ALIAS_SELF_RE = re.compile(
        r'\(\s*Self\s+as\s+Actor\s*\)|\bSelf\s+as\s+Actor\b', re.IGNORECASE)

    @staticmethod
    def _self_reference(extends: str) -> str:
        """What TES4's `Self` / `GetSelf` NAMES in a script of this base type.

        Written out at four sites, and the copies agreed --
        which is the point: this is one fact about the base types, not four
        decisions.  A TES4 script is attached to a REFERENCE, so `Self` is that
        reference; the three TES5 base types that have no reference of their
        own each name theirs differently.
        """
        if extends == 'ActiveMagicEffect':
            return 'GetTargetActor()'
        if extends == 'TopicInfo':
            return 'akSpeakerRef'
        if extends == PLAYER_ALIAS_EXTENDS:
            return 'GetReference()'
        return 'Self'

    @staticmethod
    def _implicit_self(extends: str) -> str:
        """What a bare, receiver-less call acts on in this script's base type.

        `Self` everywhere except a PlayerAlias script, whose Self is the
        ReferenceAlias rather than the reference it fills.
        """
        return 'GetReference()' if extends == PLAYER_ALIAS_EXTENDS else 'Self'

    def _base_record_type(self, name: str) -> str:
        """Papyrus type of the BASE RECORD `name` refers to, or ''.

        Resolved exactly as the property binder resolves it: a bare
        `edid_to_formid` lookup misses the sanitised spellings it handles --
        `0probeUbent` is emitted as the property `probeUbent` -- so the plain
        lookup found nothing and the type came back unknown even though the
        property itself had bound as MiscObject.

        Only BASE records answer; a placed reference really is a reference, so
        ACHR/ACRE/REFR give '' and leave the variable an ObjectReference.
        """
        if not self.xref:
            return ''
        fid = resolve_property_formid(self.xref, name)
        rtype = self.xref.record_type.get(fid, '') if fid else ''
        if not rtype or rtype in PLACED_REF_SIGS:
            return ''
        return _record_type_to_base_papyrus(rtype)

    def _assignment_record_type(self, name: str) -> str:
        """Papyrus value type of the record assigned by bare EditorID.

        Unlike `_base_record_type`, this deliberately includes placed refs:
        REFR is ObjectReference and ACHR/ACRE are Actor.  The declaration
        preloader may type either one as its attached script (or as Actor from
        an eventual call site), which is useful for property member access but
        is not the type a separate local receives from `set local to RefID`.
        """
        if not self.xref:
            return ''
        fid = resolve_property_formid(self.xref, name)
        rtype = self.xref.record_type.get(fid, '') if fid else ''
        if rtype in PLACED_REF_SIGS:
            attached = self.xref.get_record_script_type(name)
            if attached:
                return attached
        if rtype in ('ACHR', 'ACRE'):
            return 'Actor'
        if rtype == 'REFR':
            return 'ObjectReference'
        return _record_type_to_base_papyrus(rtype) if rtype else ''

    def _is_global_target(self, target: str) -> bool:
        """True when `target` names a GlobalVariable-typed property.

        A Papyrus global is an object written through SetValue(), never by
        assignment.  Shared by the `set` and `let` assignment paths so both
        spellings of a global write emit the same call.
        """
        tgt_low = target.lower().split('.')[-1]
        return self.sc.property_refs.get(
            target, self.sc.property_refs.get(tgt_low, '')) == 'GlobalVariable'

    def _resolve_objref_ref(self, ref_name, extends) -> str:
        """Resolve the reference for an ObjectReference-typed function call.

        Like `_resolve_self_ref(actor_func=True)` this redirects the implicit
        `Self` of ActiveMagicEffect/TopicInfo scripts (whose Self is NOT a
        reference) onto the reference they act on — but it does not add the
        `as Actor` cast, because the callee is declared on ObjectReference and
        works for actors and objects alike.
        """
        if not ref_name:
            ref = self._self_reference(extends)
            return (self._cast(ref, 'ObjectReference')
                    if self.type_of(ref) == 'Form' else ref)
        if (ref_name.lower() in SELF_NAMES
                and extends in ('ActiveMagicEffect', 'TopicInfo',
                                PLAYER_ALIAS_EXTENDS)):
            ref = self._self_reference(extends)
        else:
            ref = self._convert_ref(ref_name, extends, as_receiver=True)
        return (self._cast(ref, 'ObjectReference')
                if self.type_of(ref) == 'Form' else ref)

    def set_scro_aliases(self, aliases: dict) -> None:
        """Install the stale-name -> canonical-EditorID map for this fragment.

        Oblivion runs the COMPILED script, not the source text the CK shows, and
        the two can disagree.  Knights.esp's quest-stage result scripts still say
        `player.additem NDArmorCuirass 1` and `player.additem NDLL0WeaponSword 1`
        — names no record in the plugin carries — while the SCRO table those same
        stages ship binds 01000ECE (NDArmorHeavyCuirass1, "Cuirass of the
        Crusader") and 01000FCA (NDLL0WeaponSwordLvl100).  The records were
        renamed after the scripts were last compiled; the engine kept handing out
        the right items because it reads the SCRO FormID, so the stale spellings
        are invisible in-game.

        Converting the TEXT, those names resolve to nothing, declare no property
        and reach the compiler undefined — which fails the CHECKER, so no .pex is
        emitted for the whole script and every OTHER stage of the quest dies with
        it (these fragments are what hand out the Crusader relics).

        The map is built positionally by `resolve_scro_aliases`; see there.
        """
        self.sc.scro_aliases = {k.lower(): v for k, v in aliases.items()}

    def _scro_alias_for(self, name: str) -> str:
        """Return the canonical EditorID a stale script-text `name` refers to."""
        low = name.strip().lower()
        if not low or not self.xref:
            return ''
        alias = self.sc.scro_aliases.get(low, '')
        if not alias:
            return ''
        # Never redirect a name that resolves on its own.
        if (self.xref.edid_to_formid.get(low)
                or _digit_stripped_formid(self.xref, low)):
            return ''
        return alias

    def _form_operand_edid(self, raw: str) -> str:
        """Resolve a FORM-ARGUMENT operand written as a raw FormID.

        The bare-identifier path in _convert_expression only reinterprets a
        6-8 digit token as a FormID, because anywhere else in a script a short
        run of digits is an ordinary numeric literal.  In an argument slot that
        the engine reads as a FORM (GetIsID's base record) there is no such
        ambiguity: a number there is ALWAYS a FormID, and the low ids are the
        ones scripts actually write by hand — Knights.esp's ND10 time-stop
        effect tests `GetIsID 7`, i.e. the Player NPC_ at 0x00000007.

        Left unresolved the number survived as a literal and the comparison
        became `Form == Int`, which the checker rejects outright, so no .pex was
        emitted for the script at all.  Returns the canonical EditorID, or ''
        when the token is not a resolvable id.
        """
        raw = raw.strip().strip('"\'')
        if not raw or not re.fullmatch(r'[0-9A-Fa-f]{1,8}', raw):
            return ''
        if not self.xref:
            return ''
        return self.xref.formid_to_edid.get(raw.upper().zfill(8), '')

    def _bind_base_form_property(self, name: str) -> None:
        """Type `name` as the Papyrus type of the BASE record it names.

        Used by base-object comparisons (GetIsID), whose operand is the base
        record itself: an NPC_ is an ActorBase, a MISC is a MiscObject.  Falls
        back to Form, which compares against every base type.
        """
        rtype = ''
        if self.xref:
            fid = self.xref.edid_to_formid.get(name.lower(), '')
            rtype = self.xref.record_type.get(fid, '') if fid else ''
        self.sc.property_refs[name] = _record_type_to_base_papyrus(rtype)

    def _dangling_cross_script_target(self, raw_target: str) -> str:
        """Return a reason string when `Owner.Var` names an undeclared variable.

        Only fires when the owner resolves to a script whose variable list is
        KNOWN and does not contain the name — an unresolved owner is left alone
        so this never suppresses a legitimate assignment.
        """
        if '.' not in raw_target or not self.xref:
            return ''
        owner, _, var = raw_target.partition('.')
        owner_low, var_low = owner.strip().lower(), var.strip().lower()
        if not owner_low or not var_low:
            return ''
        # Resolve the owner EditorID to its attached script's variable table.
        fid = self.xref.edid_to_formid.get(owner_low, '')
        script_low = ''
        if fid:
            scri = self.xref.record_scri.get(fid, '')
            if scri:
                script_low = self.xref.script_formid_to_edid.get(scri, '').lower()
        if not script_low and owner_low in self.xref.script_all_vars:
            script_low = owner_low
        if not script_low:
            return ''
        known = self.xref.script_all_vars.get(script_low)
        if not known:
            return ''
        if var_low in known:
            return ''
        return (f'NE: {owner}.{var} is not declared in {script_low} '
                f'(dangling in the original script)')

    def _actor_base_property(self, name: str, extends: str) -> str:
        """Bind `name` as an ActorBase property and return the property name.

        Commands whose operand is an actor BASE record (GetDeadCount) need the
        property typed ActorBase, which is where the method is declared.  The
        name may collide case-insensitively with one of the script's own
        variables — MQ19Script has both an `Int narel` flag and a reference to
        the NPC_ `Narel` — and Papyrus is case-insensitive, so reusing the name
        would either redeclare it or silently resolve to the local (which is
        what made `Narel.GetDeadCount()` an undefined function).  Suffix the
        property in that case.
        """
        canon = name
        if self.xref:
            fid = self.xref.edid_to_formid.get(name.lower(), '')
            if fid:
                canon = self.xref.formid_to_edid.get(fid, name)
        prop = _safe_property_name(canon)
        low = prop.lower()
        if low in self.sc.local_vars or low in self.sc.var_types:
            prop = f'{prop}Base'
        self.sc.property_refs[prop] = 'ActorBase'
        return prop


    # An OBSE format specifier: %z (string_var), %g/%.Nf (number), %c, %x, %%.
    # The precision digits are optional on BOTH sides of the dot: authors write
    # `%0.f` as often as `%.0f` (XPKnotboneFactionFixerSCRIPT) and the engine
    # accepts it, so requiring a digit after the dot missed those and left the
    # specifier printing literally.
    _OBSE_FMT_RE = re.compile(r'%(?:%|[-+ #0]*\d*(?:\.\d*)?[a-zA-Z])')

    def _format_string_call(self, args_str: str, extends: str,
                            indexes=None) -> str:
        """Convert an OBSE printf-style call into Papyrus concatenation.

        `printToConsole "attack button == %.0f" attackButton` and
        `MessageBoxEX "…%z…%g", a, b` pass a format string followed by its
        arguments.  Papyrus has no formatting, so each specifier is replaced by
        `+ (arg as String) +`.  Previously the arguments were emitted straight
        after the string with no separator, which is not parseable at all
        ("unexpected name `attackButton`").
        """
        s = args_str.strip().lstrip(',').strip()
        if not s.startswith('"'):
            return self._quote_msg(s)
        end = s.find('"', 1)
        if end < 0:
            return self._quote_msg(s)
        fmt = s[1:end]
        if indexes is None:
            indexes = range(1, len(self._arg_nodes))
        args = [self.arg_expr(i, extends) for i in indexes]

        pieces: list[str] = []
        last = 0
        idx = 0
        for m in self._OBSE_FMT_RE.finditer(fmt):
            if m.group(0) == '%%':
                continue
            if idx >= len(args):
                # No argument left to fill this specifier, so it is not one:
                # `%` also appears as an ordinary character ("100% done", where
                # the regex sees "% d").  Consuming it swallowed the following
                # letter and split the sentence.  Leave the text untouched.
                continue
            lit = fmt[last:m.start()]
            if lit:
                pieces.append(f'"{lit}"')
            pieces.append(f'({args[idx]} as String)')
            idx += 1
            last = m.end()
        tail = fmt[last:]
        if tail or not pieces:
            pieces.append(f'"{tail}"')
        # Any argument with no matching specifier still has to appear.
        for extra in args[idx:]:
            pieces.append(f'({extra} as String)')
        return ' + '.join(pieces)

    def _format_message_args(self, sources: list, extends: str) -> str:
        """`_format_message` over already-separated arguments.

        The string version re-splits its tail on commas and then on
        whitespace, which tears a literal containing either (`"LEVEL
        AUFSTEIGEN!"` became two arguments).  The parser separated them
        already, so this only has to drop the surplus display-time literal
        TES4's `Message` allows after the format arguments -- Papyrus's
        Debug.Notification has no duration, and concatenating it would print
        "Rank 3 Fireball10".
        """
        fmt = sources[0][1:-1] if sources else ''
        n_spec = len([m for m in self._OBSE_FMT_RE.finditer(fmt)
                      if m.group(0) != '%%'])
        keep = list(range(1, len(sources)))
        while (len(keep) > n_spec and keep
               and re.match(r'^-?\d+(?:\.\d+)?$', sources[keep[-1]])):
            keep.pop()
        return self._format_string_call(f'"{fmt}"', extends, keep)

    def _format_message(self, s: str, extends: str) -> str:
        """Format a vanilla Message/MessageBox call.

        Same printf model as _format_string_call, with one TES4-only wrinkle:
        `Message` takes an optional trailing DISPLAY TIME after the format
        arguments (`message "Rank %.0f Fireball", SpellRank, 10` shows one
        value for 10 seconds).  Papyrus's Debug.Notification has no duration,
        and _format_string_call appends every unconsumed argument to the text —
        which would print "Rank 3 Fireball10".  So surplus numeric literals
        beyond the specifier count are dropped rather than concatenated.
        """
        end = s.find('"', 1)
        fmt = s[1:end]
        n_spec = len([m for m in self._OBSE_FMT_RE.finditer(fmt)
                      if m.group(0) != '%%'])
        keep = list(range(1, len(self._arg_nodes)))
        srcs = self.arg_srcs()
        while (len(keep) > n_spec and keep
               and re.match(r'^-?\d+(?:\.\d+)?$', srcs[keep[-1]])):
            keep.pop()
        return self._format_string_call(s, extends, keep)

    def _quote_msg(self, args_str: str) -> str:
        """Quote a message argument if not already quoted.
        For MessageBox with buttons (e.g. '"text" "Yes" "No"'), extract only the message."""
        s = args_str.strip()
        # `Message, "text"` / `MessageBox, "text"` — Oblivion tolerated a comma
        # between the command and its first argument.  Left in place it is not
        # recognised as the opening quote, so the whole thing (comma included)
        # got re-quoted into `", "text""`, which does not parse.
        s = s.lstrip(',').strip()
        if s.startswith('"'):
            # Find the end of the first quoted string
            end = s.index('"', 1) if '"' in s[1:] else len(s)
            first_str = s[:end + 1]
            # If there are more quoted strings (button labels), strip them
            return first_str
        return f'"{s}"'




_ONACTIVATE_BLOCK_RE = re.compile(
    r'^[ \t]*begin[ \t]+onactivate\b[^\r\n]*\r?\n(.*?)^[ \t]*end\b',
    re.IGNORECASE | re.MULTILINE | re.DOTALL)


def sctx_onactivate_consumes(sctx: str) -> bool:
    """True when a raw TES4 script source has a consuming OnActivate block.

    Text-level twin of ScriptConverter._onactivate_consumes for callers that
    hold the SCTX source rather than parsed blocks (tes5_import uses it to
    spot barrier doors whose lock level must stay AI-passable).  A script
    with no OnActivate block at all consumes nothing.
    """
    blocks = [('onactivate', '', m.group(1).splitlines())
              for m in _ONACTIVATE_BLOCK_RE.finditer(sctx or '')]
    if not blocks:
        return False
    return ScriptConverter._onactivate_consumes(blocks)


def _call_name(node) -> str:
    """Lowercased command name a value NAMES, or ''.

    TES4 writes a zero-argument command as a bare word, so the same call
    reaches the tree as `Call`, `Member` or `Ident` depending on how it was
    written.  The name is what matters.
    """
    return node.called


def _split_say(conv, node, extends: str):
    """`(say_call, delay)` when this value is a Say, else `(None, '')`.

    `set T to ref.Say topic + 2` assigns the LINE DURATION plus an authored
    offset.  The tree hands over the call and the offset separately; the text
    version scanned the rendered string for balanced parentheses to pull them
    apart.
    """
    delay = ''
    if isinstance(node, _tes4_nodes.BinOp) and node.op in ('+', '-'):
        inner, other = node.left, node.right
        if _call_name(inner) in _SAY_COMMANDS:
            delay = ' %s %s' % (node.op, _expr.emit(conv, other, extends))
            node = inner
    if _call_name(node) not in _SAY_COMMANDS:
        return None, ''
    return _expr.emit(conv, node, extends), delay


#: TES4 commands that SPEAK and return the spoken line's length.
_SAY_COMMANDS = frozenset({'say', 'sayto', 'saycustom'})


#: Quest METHODS -- `X.SetStage` is a command on the quest, not a variable of
#: it.  Split out of the member resolver so the two lists it used to carry
#: inline are named rather than repeated.
_QUEST_METHODS = frozenset({
    'getstage', 'setstage', 'getstagedone', 'start', 'stop', 'isrunning',
    'iscompleted', 'completequest',
})

#: Commands reached as `ref.Name` that carry no FUNCTION_MAP row of their own,
#: so the membership tests above would read them as variables.
_MEMBER_COMMANDS = frozenset({
    'evaluatepackage', 'enable', 'disable', 'delete', 'activate', 'reset',
    'kill', 'resurrect', 'moveto', 'getparentcell', 'getself', 'getactionref',
    'getlinkedref', 'getparentref', 'getbaseobject', 'getactorbase',
    'isactorusingatorch', 'isridinghorse', 'createfullactorcopy',
})


def _call_return_type(value: str) -> str:
    """The Papyrus type a rendered call yields, when the table knows it.

    Only the head NAME is consulted, so this reads the call's identity rather
    than parsing its text: `GetLinkedRef()` is an ObjectReference wherever it
    appears.  `RETURN_TYPES` is the one table that answers this; four separate
    per-name sets asked it.
    """
    name = _outermost_call(value)
    return RETURN_TYPES.get(name, '') if name else ''


def _outermost_call(value: str) -> str:
    """The name of the call that PRODUCES this value, lowercased.

    A chain is typed by its LAST link: `Game.GetPlayer().PlaceAtMe(x)` yields
    whatever PlaceAtMe yields, not whatever GetPlayer does.  Splitting on the
    first `(` read the head of the chain instead and typed the whole
    expression Actor, so the ObjectReference PlaceAtMe returns kept its
    downcast.  A zero-argument native written bare (`GetTargetActor` beside
    `GetTargetActor()`) has no parentheses at all, and names itself.
    """
    text = value.strip()
    depth, tail = 0, text
    for i, ch in enumerate(text):
        if ch == '(':
            if depth == 0:
                tail = text[:i]
            depth += 1
        elif ch == ')':
            depth -= 1
            # A call that CLOSES at the top level and is followed by `.name(`
            # is not the producer -- the link after it is.
            if depth == 0 and text[i + 1:i + 2] == '.':
                rest = text[i + 2:]
                head = rest.partition('(')[0]
                if head:
                    tail = head
    return tail.rsplit('.', 1)[-1].strip().lower()

def _needs_parens(text: str) -> bool:
    """Would a trailing `as T` bind to only PART of this expression?"""
    depth = 0
    for i, ch in enumerate(text):
        if ch in '([':
            depth += 1
        elif ch in ')]':
            depth -= 1
        elif not depth and any(text.startswith(op, i) for op in LOOSE_OPS):
            return True
    return False


#: A bare integer literal -- what TES4 wrote into a `ref` used as a flag.
_INT_LITERAL_RE = re.compile(r'^-?\d+$')
