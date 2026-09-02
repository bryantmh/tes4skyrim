"""What does a bare IDENTIFIER mean in this script?

`_resolve_name` was a 374-line if-chain with 44 returns and no loops -- a
lookup table written as control flow.  Everything structural in it went to the
parser (operators, calls, nesting, the dotted name); what remains is exactly
the part the tree cannot answer, because a bare name is a variable, a form or a
command depending on what the PLUGIN declares.

The chain is now four ordered steps, each a dict lookup:

    1. a declared LOCAL wins over everything, including the `player` keyword
    2. a fixed READING -- `BARE_READINGS`, the names with one constant answer
    3. a COMMAND read with no arguments -- routed to the command layer
    4. a RECORD the plugin defines -> a typed property

Steps 2 and 3 are data (`BARE_READINGS`, `BARE_COMMANDS`); only step 4 needs
the graph.
"""

import re

from script_convert.constants import (
    FAME_GLOBALS, KNOWN_GLOBALS, COMMAND_ROWS, HANDLED_COMMANDS,
    TES4_MURDER_BOUNTY,
    _ACTOR_VALUE_MAP_LOW, _BARE_NO_EQUIV_COMMANDS, _FORM_TYPE_TESTS,
    _canonical_global, _safe_property_name,
)
from script_convert.resolve import _digit_stripped_formid

#: A raw FormID operand: TES4 scripts name a form by id as readily as by
#: EditorID (`additem 0000000f 500` is how Morrowind_ob hands out gold).  The
#: leading load-order zeroes are trimmed as often as not, so 6-8 digits are
#: accepted and zero-padded; 6 is the floor because that is a full 24-bit
#: object index.  A pure-DECIMAL run is an ordinary literal, so at least one
#: A-F digit or a leading zero is required -- which every real FormID here has
#: and no decimal literal in these scripts does.
_FORMID_RE = re.compile(r'[0-9A-Fa-f]{6,8}')

#: Bare names whose reading NEVER depends on the script.  Each was an `if low
#: ==` arm returning one constant expression.
BARE_READINGS = {
    'isxbox': 'False',
    'getrandompercent': 'Utility.RandomInt(0, 99)',
    'getrandpercent': 'Utility.RandomInt(0, 99)',
    'getdisposition': '50',
    # Only the ARGUMENT-LESS spelling lands here, and with no target named
    # there is nothing to ask IsDetectedBy about.  The one-argument form --
    # which is what all 56 sites in the plugin use -- has its own row.
    'getdetectionlevel': '0',
    'isplayerinprison': 'Game.GetPlayer().IsArrested()',
    'getplayerinjail': 'Game.GetPlayer().IsArrested()',
    'isplayerinjail': 'Game.GetPlayer().IsArrested()',
    'senttojail': 'Game.GetPlayer().IsArrested()',
}

#: Bare reads that are inert: no Papyrus equivalent and no side effect, so the
#: honest value is 0.  Kept as one set because enumerating them one build at a
#: time is how each new spelling reached the compiler as an undefined name.
BARE_INERT = frozenset({
    'getisalerted', 'israining', 'menumode', 'istimepassing',
    'getplayerinseworld', 'getcurrentaiprocedure', 'getcurrentaipackage',
    'getiscurrentpackage', 'isidleplaying', 'getbookread', 'gettalkedtopc',
    'getcrimeknown', 'getstartingpos', 'getisplayerbirthsign',
    'hasbeenpickedup', 'getgameloaded', 'hasvariable', 'getownership',
    'isonguard', 'isindangerouswater', 'getarmorrating', 'isspelltarget',
    'isswimming', 'isactor', 'getspellcount', 'getrestrained',
    'getpcfactionattack', 'getpcfactionsteal', 'getpcfactionmurder',
})

#: Zero-argument commands that are ALWAYS written bare, so they never reach the
#: argument-bearing path.  Without routing, each survives into the output as an
#: undefined identifier -- a hard compile error that fails the whole script.
BARE_COMMANDS = frozenset({
    'isanimplaying', 'getiscreature', 'iscreature', 'hasvampirefed',
    'isspelltarget', 'isguard', 'getnextref', 'isowner', 'getbaseobject',
    'isonground', 'isthirdperson', 'isplayerinjail', 'getpcinfamy',
    'getrestrained', 'ispcamurderer', 'getcrimegold', 'getpcfame',
    'gettalkedtopc', 'payfine', 'getdayofweek', 'getdayoftheweek',
})


#: Names meaning "the player is a murderer".  Takes NO arguments, so it is
#: always read bare -- which meant the real handler was unreachable dead code
#: and both sites became the literal `If 0 == 1`.  DarkBrotherhoodScript's is
#: the ONLY trigger for the entire Dark Brotherhood questline, so the questline
#: could never begin.  A violent bounty at or above the vanilla murder price is
#: what distinguishes a killing from an assault.
_MURDERER_NAMES = frozenset({'ispcamurderer', 'ispcanmurderer',
                             'getpcismurderer'})


def resolve(conv, expr: str, extends: str) -> str:
    """Resolve one bare identifier to Papyrus text."""
    expr = expr.strip()
    if not expr or expr.isdigit():
        return expr

    if expr[0] == '"' and expr[-1] == '"' and len(expr) > 2:
        return _quoted(conv, expr[1:-1], extends)

    low = expr.lower()
    sc = conv.sc

    # A declared LOCAL always wins -- over the keywords and over any command
    # it happens to collide with.  StartCelleAufzugTriggerZone01Script declares
    # `Short Player` as its own trigger flag; rewriting that to
    # Game.GetPlayer() produced the un-assignable `Game.GetPlayer() = 1`.
    if low in sc.local_vars:
        return sc.var_renames.get(low, expr)

    fixed = _fixed_reading(conv, low, extends)
    if fixed is not None:
        return fixed

    if _is_bare_command(low):
        from script_convert.emit import dispatch as _dispatch
        return _dispatch.emit_command(conv, None, expr, extends)

    record = _record(conv, expr, low)
    if record is not None:
        return record

    if low in ('player', 'playerref'):
        return 'Game.GetPlayer()'
    if low in ('getself', 'this', 'self'):
        return conv._self_reference(extends)
    if low in ('getsecondspassed', 'scripteffectelapsedseconds'):
        # In a script with a GameMode/ScriptEffectUpdate poll this is
        # TES4_SecondsPassed, a variable the OnUpdate prologue fills with the
        # MEASURED elapsed time -- a fixed per-tick constant assumed every tick
        # took exactly the registration interval, so under VM load every timer
        # drained slower than real time.  Outside such a script no prologue
        # exists, so the interval constant remains, and it MUST equal the
        # interval the script actually runs at: a 0.5 literal at a 0.1s tick
        # made every converted timer run 5x fast (Valen Dreth's 10s taunt
        # pause became 2s).
        return ('TES4_SecondsPassed' if sc.gsp_realtime
                else str(conv._get_update_interval()))
    if low in KNOWN_GLOBALS:
        canonical = _canonical_global(expr)
        conv.sc.property_refs[canonical] = 'GlobalVariable'
        return f'{canonical}.GetValue()'
    if low in _ACTOR_VALUE_MAP_LOW:
        return _ACTOR_VALUE_MAP_LOW[low]
    return sc.var_renames.get(low, expr)


def _fixed_reading(conv, low: str, extends: str):
    """A bare name with one fixed answer, or None."""
    if low in BARE_READINGS:
        return BARE_READINGS[low]
    if low in BARE_INERT:
        return '0'

    if low in FAME_GLOBALS:
        prop, text = FAME_GLOBALS[low]
        conv.sc.property_refs[prop] = 'GlobalVariable'
        return text
    if low in _MURDERER_NAMES:
        conv.sc.property_refs['TES4CyrodiilCrimeFaction'] = 'Faction'
        return (f'(TES4CyrodiilCrimeFaction.GetCrimeGoldViolent() '
                f'>= {TES4_MURDER_BOUNTY})')
    if low == 'getcrimegold':
        conv.sc.property_refs['TES4CyrodiilCrimeFaction'] = 'Faction'
        return 'TES4CyrodiilCrimeFaction.GetCrimeGold()'

    if low in ('getactionref', 'isactionref'):
        return conv._get_action_ref_param()
    if low in ('getcurrenttime', 'gamehour'):
        # NOT `as Int`.  GameHour is the engine's own global in both games and
        # Skyrim declares it float, so GetValue() returns fractional hours.
        # The bell/chime idiom brackets the top of each hour with a +-0.02
        # window, and truncating collapses every such window into an
        # always-true whole-hour test -- which made the Erodans-Kapelle bell
        # (and Oblivion's BellTowerScript) ring on a continuous loop.
        conv.sc.property_refs['GameHour'] = 'GlobalVariable'
        return 'GameHour.GetValue()'
    if low in ('getpcissleeping', 'ispcsleeping', 'isplayersleeping'):
        # Inside a sleep-idiom MenuMode body the read means "is this a sleep
        # frame" -- the script-managed flag.  Elsewhere Oblivion never ran
        # while sleeping, so a raw GetSleepState() read keeps the same truth.
        return ('TES4_PCSleeping' if conv.sc.in_sleep_menumode
                else 'Game.GetPlayer().GetSleepState()')
    if low == 'isininterior':
        return f'{_self_ref(extends)}.GetParentCell().IsInterior()'
    if low in ('getdisabled', 'isdisabled'):
        # Through the polyfill, not the bare native: a DESTROYED reference must
        # not report as disabled.  Oblivion keeps the two as independent bits
        # and closing a gate sets only destroyed, so its poll preambles were
        # never meant to fire for a closed gate.
        return (f'TES4Polyfill.GetDisabled({_self_ref(extends)}, '
                f'{conv._destroyed_formlist()})')
    if low == 'getdestroyed':
        # NOT IsDisabled() and NOT GetCurrentDestructionStage(): destroyed,
        # disabled and destruction-STAGE are three different engine states.
        # Skyrim exposes no reader for the destroyed flag, and this conversion
        # writes no DEST subrecord, so GetCurrentDestructionStage() is 0 for
        # every converted record -- a read that can never become true.  That
        # broke every quest advancing off its own destruction: MS48's Kvatch
        # gate has the ONLY setstage 50 in its chain, so it pinned at stage 10.
        return (f'TES4Polyfill.GetDestroyed({_self_ref(extends)}, '
                f'{conv._destroyed_formlist()})')
    if low == 'reset':
        return f'{_self_ref(extends, topic="akSpeakerRef")}.Reset()'
    if low == 'getbuttonpressed':
        # A script that shows a button MessageBox of its own reads the clicked
        # index back through the consume-on-read helper (TES4 returns it once,
        # then -1).  A script that never shows one is polling a box some OTHER
        # script displayed -- cross-script GetButtonPressed was global in TES4
        # -- and keeps the dead -1 rather than being miswired to its own
        # (nonexistent) state.
        if conv.message_menus.get((conv.sc.edid or '').lower()):
            conv.sc.uses_msg_buttons = True
            return 'TES4_TakeMsgButton()'
        return '-1'
    if low == 'getcontainer':
        # Bare GetContainer means "the container I am in".  Inside an
        # OnAdd block the new container is the exact authored answer; OnDrop
        # runs after removal and therefore has none. Inside equip/unequip the
        # container is the actor the event hands us.
        # A COMPARISON against it is answered on the BinOp before this operand
        # is emitted, so reaching here is a bare read.  Papyrus cannot walk
        # from an item to its container at all, so the honest value is None;
        # a placeholder only moved the failure to the compiler.
        if conv.sc.current_block_type == 'onadd':
            return 'akNewContainer'
        if conv.sc.current_block_type == 'ondrop':
            return 'None'
        actor = conv._current_event_actor_param()
        if actor:
            return actor
        return conv.note('GetContainer has no Papyrus equivalent',
                         value='None')
    return None


def _self_ref(extends: str, topic: str = 'Self') -> str:
    """The reference a bare self-read acts on, by script type."""
    if extends == 'ActiveMagicEffect':
        return 'GetTargetActor()'
    if extends == 'TopicInfo':
        return topic
    return 'Self'


def _is_bare_command(low: str) -> bool:
    """Should this bare name route to the command layer?

    A handler-only name normally falls through on purpose: bare reads like
    getSecondsPassed are rewritten by dedicated later passes, and routing them
    here TODOs them mid-expression, leaving `timer = timer - `.  The names
    below have no such pass and no same-named form, so they must be routed or
    they survive into the output as undefined identifiers.
    """
    if low in BARE_COMMANDS or low in _FORM_TYPE_TESTS:
        return True
    if low in COMMAND_ROWS:
        return True
    if low in HANDLED_COMMANDS and low in _BARE_NO_EQUIV_COMMANDS:
        return True
    # Prefix-matched no-equivalent families (OBSE menu/UI, console commands,
    # array/string helpers).  One row per variant would have to be added by
    # hand, and every one missed becomes an undefined identifier.
    return bool(re.match(r'^(?:get|set)menu\w*$', low)
                or low.startswith(('con_', 'ar_', 'sv_')))


def _quoted(conv, inner: str, extends: str) -> str:
    """A quoted operand -- TES4 allows quoting any EditorID.

    An unresolved one keeps its QUOTES: it is a genuine string, and handing
    back the bare content made `sTime = (ihour as String) + ":"` emit a lone
    `:`, which is not even scannable and took every script referencing
    `HMSfromFloat24h` down with it.
    """
    low = inner.lower()
    # A LOCAL VARIABLE may be quoted too: NQ15Turret01SCRIPT declares
    # `ref TowerTargetRef` and then writes `GetDistance "TowerTargetRef"`.
    # There is no form by that name, so the quotes survived and Papyrus got a
    # String where an ObjectReference was required.
    if low in conv.sc.local_vars:
        return conv.sc.var_renames.get(low, inner)
    if low in ('player', 'playerref'):
        return 'Game.GetPlayer()'
    resolved = _record(conv, inner, low)
    return resolved if resolved is not None else '"%s"' % inner


def _record(conv, expr: str, low: str):
    """A record the plugin defines, as a typed property.  None if unknown."""
    xref = conv.xref
    if xref is None:
        return None

    if _FORMID_RE.fullmatch(expr) and (not expr.isdigit()
                                       or expr.startswith('0')):
        edid = xref.formid_to_edid.get(expr.upper().zfill(8), '')
        if edid:
            expr, low = edid, edid.lower()

    fid = xref.edid_to_formid.get(low, '')
    if not fid:
        # A Papyrus identifier cannot start with a digit, so a Morroblivion
        # record named `0<name>` arrives here already stripped.
        fid = _digit_stripped_formid(xref, low)
    if not fid:
        # Stale source spelling -- recover it from this record's SCRO table.
        alias = conv._scro_alias_for(expr)
        if alias:
            expr, low = alias, alias.lower()
            fid = xref.edid_to_formid.get(low, '')
    if not fid:
        return None

    rtype = xref.record_type.get(fid, '')
    ptype = conv._papyrus_type_for(fid, rtype)
    # Prefer the attached script type for cross-script property access -- but
    # never on a base-object type, where it cannot bind.
    script_type = xref.get_record_script_type(expr)
    if script_type and conv._script_type_binds(ptype, fid):
        ptype = script_type
    # Key the property on the CANONICAL EditorID, not the spelling this script
    # happened to use.  TES4 name lookup is case-insensitive, so keying on the
    # local spelling created a SECOND entry differing only in case -- and since
    # Papyrus is also case-insensitive the two declarations collided, the
    # caller's type lost, and the call became "undefined function".
    canon = xref.formid_to_edid.get(fid, expr)
    safe = _safe_property_name(canon)
    conv.sc.property_refs[safe] = ptype
    return conv._global_read(safe) if ptype == 'GlobalVariable' else safe
