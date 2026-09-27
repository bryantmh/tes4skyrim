"""Command dispatch: one call node -> Papyrus text.

This is what `_emit_function`'s 76-branch chain became.  The chain answered
three questions in sequence and the answers are now three lookups:

    1. Does a HANDLER claim this name?   -> `commands.REGISTRY`
    2. Does a ROW describe it?           -> `constants.COMMAND_ROWS`
    3. Neither                           -> the generic mapped-call rendering

A handler may decline by returning None (`getfirstref` converts only the actor
walk), and dispatch falls through to the row exactly as if it had not existed.
"""

import re

from script_convert.stage_latch import guard_stage_timer
from script_convert import commands as _commands
from script_convert.commands import Call
from script_convert.constants import (PLAYER_ALIAS_EXTENDS,
                                      is_generated_script_type)
from script_convert.command_rows import MAP
from script_convert.command_rows import (
    COMMAND_ROWS, COMPOUND_HAS_OWN_HANDLER, DEFAULT_ARGS, DROP_ARGS_FUNCS,
    HANDLED_COMMANDS, ACTOR_ONLY_FUNCTIONS, OBJREF_IMPLICIT_SELF_FUNCTIONS,
    OBJREF_SHARED_FUNCTIONS, ZERO_ARG_REF_FUNCTIONS, command_prefix_row
)
from script_convert.emit.commands import emit_row
from script_convert.tes4 import nodes as N

#: A mapped name that is already a GLOBAL call has nowhere to put a receiver.
_GLOBAL_CALL_RE = re.compile(r'^(?:Game|Utility|Debug|Math)\.')


def emit_command(conv, ref_name, func_name: str, extends: str, args=()) -> str:
    """Convert one TES4 command invocation, leaving the caller's arguments in place.

    A nested command (`Call F 30 * (getPCMiscStat 8 - x), 1, 1`) replaces
    `conv._arg_nodes` while it converts; restoring it keeps the outer call's
    later arguments. See: docs/commentary/script_convert.md#nested-call-arguments
    """
    outer = conv._arg_nodes
    try:
        return _emit_command(conv, ref_name, func_name, extends, args)
    finally:
        conv._arg_nodes = outer


def _emit_command(conv, ref_name, func_name: str, extends: str, args=()) -> str:
    """Convert one TES4 command invocation.

    A name with no row, handler or prefix family -- and a handler-only command
    that fell past its handler -- becomes an inert `;TODO:` line rather than a
    call: the spelling is TES4's, so emitting it would name an undefined
    Papyrus function and fail the whole script.
    See: docs/commentary/script_convert.md#unknown-commands-must-be-inert
    """
    call = Call(conv, ref_name, func_name, extends, args)
    conv._arg_nodes = call.args

    ref_name = _promote_receiver(conv, call)

    result = _commands.dispatch(conv, call)
    if result is not None:
        return result

    row = COMMAND_ROWS.get(call.name)
    if row is None and call.name not in HANDLED_COMMANDS:
        # A prefix family says "any name starting with this is inert", so it
        # must never shadow a command that HAS a handler: `sv_Construct` is the
        # one `sv_` command with a real equivalent, and swallowed by the family
        # it survived as an undefined identifier that failed Morroblivion's
        # chargen quiz.
        row = command_prefix_row(call.name)
    if row is not None and row.subj != MAP:
        return emit_row(conv, row, ref_name, func_name, call.src, extends)

    # `<ref>.<func>` as a COMPOUND name: some rows key on the pair.
    if ref_name and call.name not in COMPOUND_HAS_OWN_HANDLER:
        crow = COMMAND_ROWS.get(f'{ref_name}.{func_name}'.lower())
        if crow is not None and crow.subj == MAP:
            args_txt = _convert_args(conv, call) if len(call) else ''
            out = f'{crow.emit}({args_txt})'
            return f'{out}  {crow.note}' if crow.note else out

    if call.name in HANDLED_COMMANDS or not _is_known(call.name):
        return conv.note(f'TODO: {call.written()}')

    return _emit_mapped(conv, call, ref_name, func_name, extends)


def _is_known(name: str) -> bool:
    """True when a row or handler defines this command's Papyrus spelling.

    See: docs/commentary/script_convert.md#unknown-commands-must-be-inert
    """
    return (name in COMMAND_ROWS or name in _commands.REGISTRY
            or command_prefix_row(name) is not None)


def _promote_receiver(conv, call):
    """A zero-argument command's first argument is its RECEIVER.

    Oblivion tolerated a comma between a command and its first argument, and
    Nehrim's scripts use the style constantly -- but for a command that takes
    NO arguments the token after that comma is the subject: `StopCombat,
    Player` means Player's combat state, the same as `Player.StopCombat`.
    Treating it as an argument emitted `IsInCombat(Player)` ("takes 0
    parameters not 1") and `(Self as Actor).StopCombat()`, which silently
    acted on the wrong actor.
    """
    if (conv._leading_comma and not call.ref
            and call.name in ZERO_ARG_REF_FUNCTIONS
            and len(call) == 1 and isinstance(call.args[0], N.Ident)):
        call.ref = call.args[0].name
        call.args = ()
        conv._arg_nodes = ()
    return call.ref


def _convert_args(conv, call) -> str:
    """The call's arguments, converted and comma-joined.

    A row's `max_args` caps the count at what the Papyrus native declares:
    FO3/FNV spell several commands with trailing flags Skyrim has no parameter
    for.  See: docs/commentary/script_convert.md#kill-takes-only-the-killer
    """
    if call.name in DROP_ARGS_FUNCS:
        return ''
    n = len(call)
    row = COMMAND_ROWS.get(call.name)
    cap = getattr(row, 'max_args', None)
    if cap is not None:
        n = min(n, cap)
    return ', '.join(call.arg(i) for i in range(n))


def _emit_mapped(conv, call, ref_name, func_name: str, extends: str) -> str:
    """The generic mapped-call rendering, for every `Cmd(..., MAP)` row.

    An UNKNOWN name renders the same way -- receiver resolution, the Actor
    casts and the implicit-subject rules are about the CALL, not about whether
    we recognise the name -- under its own spelling and flagged for review.
    Written twice, and the copies had drifted: the fallback's
    receiver arm never applied the TES4_-script cast, and its bare arm omitted
    the event-actor subject.
    """
    row = COMMAND_ROWS.get(call.name)
    if row is not None and row.subj == MAP:
        papyrus_func, needs_self, note = row.emit, not row.bare, row.note
    else:
        papyrus_func, needs_self, note = func_name, True, ';TODO: Verify'

    if not len(call) and call.name in DEFAULT_ARGS:
        args = DEFAULT_ARGS[call.name]
    else:
        args = _convert_args(conv, call) if len(call) else ''

    # Oblivion allowed a receiver on the player-global commands; Papyrus does
    # not, and it carries no information (the target is always the player).
    # `Player.DisablePlayerControls` emitted
    # `Game.GetPlayer().Game.DisablePlayerControls()` -- a property named
    # `Game` on Actor, which does not exist.
    if ref_name and papyrus_func and _GLOBAL_CALL_RE.match(papyrus_func):
        ref_name = None

    if ref_name:
        result = f'{_receiver(conv, call, ref_name, papyrus_func, extends)}' \
                 f'.{papyrus_func}({args})'
    else:
        result = _bare(conv, call, papyrus_func, args, needs_self, extends)
    return f'{result}  {note}' if note else result


def _receiver(conv, call, ref_name, papyrus_func: str, extends: str) -> str:
    """The resolved receiver for an explicitly-referenced mapped call."""
    ref = conv._convert_ref(ref_name, extends, as_receiver=True)
    papyrus_low = (papyrus_func or '').lower()
    is_actor_func = (call.name in ACTOR_ONLY_FUNCTIONS
                     or papyrus_low in ACTOR_ONLY_FUNCTIONS)
    # ActiveMagicEffect Self has no actor/objref methods.
    if ref == 'Self' and extends == 'ActiveMagicEffect':
        return 'GetTargetActor()'
    if ref == 'Self' and extends == 'TopicInfo' and is_actor_func:
        ref = 'akSpeakerRef'
    if not is_actor_func or call.name in OBJREF_SHARED_FUNCTIONS:
        return ref
    # akSpeakerRef is a fixed ObjectReference parameter in TopicInfo scripts.
    if ref == 'akSpeakerRef':
        return '(akSpeakerRef as Actor)'
    # BOTH type sources: an external record becomes a property, and a
    # script-local `ref` is hoisted to one.  Reading only `property_refs`
    # missed every local (`ref combatant1`), so an actor-only call on one
    # emitted a bare `combatant1.SetActorValue(...)` -- undefined on
    # ObjectReference, which failed the whole script.
    cur = (conv.sc.property_refs.get(ref, '')
           or conv.sc.var_types.get(ref.lower(), ''))
    if cur == 'ObjectReference':
        return f'({ref} as Actor)'
    if cur == '' and conv._is_bindable_property(ref):
        conv.sc.property_refs[ref] = 'Actor'
    elif is_generated_script_type(cur):
        # Typed as the SCRIPT attached to the record it names (see
        # _resolve_self_ref): cast at the call site so the cross-script
        # variable reads that need that type keep working.
        return f'({ref} as Actor)'
    return ref


def _bare(conv, call, papyrus_func: str, args: str, needs_self: bool,
          extends: str) -> str:
    """A mapped call with NO receiver -- infer the implicit subject.

    `OBJREF_SHARED_FUNCTIONS` is excluded from the Actor promotion exactly as
    it is at the receiver site: 14 of `ACTOR_ONLY_FUNCTIONS` are also declared
    on ObjectReference, and casting one of those on a non-actor Self yields
    **None**, so the call aborts at runtime instead of failing to compile.
    MS48OblivionGateScript (an ACTI) called TES4's bare `getdistance player`;
    emitted as `(Self as Actor).GetDistance` it returned None -> the comparison
    read 0 -> `0 < 1000` was always true, so the gate hammered
    `OblivionStormTamriel.ForceActive()` every 0.1s.
    """
    if (needs_self and call.name in ACTOR_ONLY_FUNCTIONS
            and call.name not in OBJREF_SHARED_FUNCTIONS):
        if extends == 'TopicInfo':
            return f'(akSpeakerRef as Actor).{papyrus_func}({args})'
        if extends == 'ActiveMagicEffect':
            return f'GetTargetActor().{papyrus_func}({args})'
        if extends == PLAYER_ALIAS_EXTENDS:
            # Self is the ReferenceAlias, not an actor; the alias's filled
            # reference (the player) is the subject.
            return f'GetActorReference().{papyrus_func}({args})'
        event_actor = conv._current_event_actor_param()
        if extends != 'Actor' and event_actor:
            # Inside an event that hands us the actor it is about
            # (`OnEquipped(Actor akActor)`), TES4's implicit subject for an
            # actor-only call is that actor, not the item.
            # MGBloodwormHelmScript's bare `addspell` is cast on the WEARER;
            # `(Self as Actor)` on an ARMO is None, so the helm's whole effect
            # was silently lost.
            return f'{event_actor}.{papyrus_func}({args})'
        if extends != 'Actor':
            return f'(Self as Actor).{papyrus_func}({args})'
        return f'{papyrus_func}({args})'

    if (needs_self
            and (call.name in OBJREF_IMPLICIT_SELF_FUNCTIONS
                 or call.name in OBJREF_SHARED_FUNCTIONS)
            and extends in ('ActiveMagicEffect', 'TopicInfo',
                            PLAYER_ALIAS_EXTENDS)):
        # An ObjectReference method called bare inside a script whose Self is
        # not a reference -- route it onto the reference the effect/topic acts
        # on, with no `as Actor` cast.  These must not be left bare:
        # TopicInfo/ActiveMagicEffect have no implicit reference at all, and a
        # bare `AddItem(...)` is an undefined function (52 scripts failed to
        # compile at exactly this point).
        return (f'{conv._resolve_objref_ref(None, extends)}'
                f'.{papyrus_func}({args})')
    return f'{papyrus_func}({args})'


def as_statement(conv, result: str) -> str:
    """Fold accumulated `;NE:` notes into a command used as a STATEMENT.

    In VALUE position a command with no equivalent reads as `0`; in STATEMENT
    position that bare `0` is not a statement at all, so it is REPLACED by the
    note.  That is why the two positions cannot share a return value.
    """
    result = guard_stage_timer(conv, result)
    if not conv._line_comments:
        return result
    comments = '  '.join(conv._line_comments)
    conv._line_comments.clear()
    if result.strip() == '0':
        return comments
    if not result.lstrip().startswith(';'):
        return f'{result}  {comments}'
    return result
