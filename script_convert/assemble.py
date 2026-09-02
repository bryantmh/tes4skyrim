"""Assemble a whole `.psc` file from one parsed TES4 script.

`convert_standalone` was 991 lines and ~12 unnamed sequential phases sharing 34
mutable fields, with 136 `out.append` calls interleaved through them.  Nothing
said where one phase ended and the next began, so a change to the poll loop
could silently depend on state the block loop happened to leave behind.

Each phase is a named function here, and the ORDER is stated once in `build`.
A phase reads the `ScriptContext` and returns lines; it never reaches into a
later phase's state.
"""

from script_convert.constants import (
    BLOCK_MAP, COMMAND_ROWS, COMBAT_STATE_GUARDS, POLL_BLOCKS,
    _ACTOR_ONLY_FUNCTIONS, _OBJREF_SHARED_FUNCTIONS, TYPE_MAP,
    _safe_property_name, papyrus_script_name,
)
from script_convert import symbols as _symbols
from script_convert.emit import script as _script
from script_convert.tes4 import nodes as N


def build(conv, name: str, source: str, extends: str, editor_id: str) -> str:
    """Convert one standalone SCPT record to a full `.psc` file."""
    tree = _prepare(conv, name, source, extends, editor_id)
    extends = conv._script_extends

    # The BODY converts FIRST: resolving a name is what registers the property
    # for it, so the property table is only complete once every statement has
    # been emitted.  Declarations are spliced in afterwards, at the top where
    # Papyrus wants them.
    body = udf(conv, tree, extends)
    body += poll(conv, tree, extends)
    body += events(conv, tree, extends, skip_poll=True)
    body += sleep_listener(conv, tree, extends)
    body += menu_blocks(conv, tree, extends)
    body += trap_hit(conv, tree, extends)
    body += door_relock(conv, tree, extends)
    body += lifecycle(conv, tree, extends)
    body = block_activation(conv, tree, extends, body)
    body = fall_damage(conv, extends, body)
    body += helpers(conv)
    body += chargen_latch(conv)
    body += stage_latches(conv)

    out = list(header(conv, name, extends, editor_id))
    # Preserve authored notes and banners that precede the first Begin block.
    # Generated converter diagnostics never enter tree.preamble, so source
    # TODO labels can be kept distinct from machine-readable failure TODOs.
    out += _script.emit_body(conv, tree.preamble, extends)
    out += properties(conv, tree)
    out += body
    return '\n'.join(out)


def _prepare(conv, name: str, source: str, extends: str, editor_id: str):
    """Parse the script and load the context: symbols, then facts."""
    conv.sc.edid = editor_id or name
    # A script calling Actor-only functions on a bare Self is an ACTOR script,
    # whatever the record said.
    if extends == 'ObjectReference':
        extends = conv._infer_extends(source, extends)
    conv._script_extends = extends

    conv._parse_source(source)
    tree = conv._tree
    _load_symbols(conv, tree, editor_id)
    _load_facts(conv, tree)
    _promote_actor_locals(conv, tree)
    _promote_assigned_actors(conv, tree)
    _preresolve_owners(conv, tree)
    return tree


def _preresolve_owners(conv, tree) -> None:
    """Type every cross-script OWNER before any statement converts.

    `set target to DAHermaeusMora.target` is the FIRST line of its body, so
    the property that names the other script was not typed yet when the
    assignment asked what the member's type was -- the answer came back empty
    and the ObjectReference-into-Actor downcast was skipped.  Resolving the
    owners up front makes the answer independent of statement order.
    """
    for body in ([tree.preamble, tree.body]
                 + [b.body for b in tree.blocks] if tree else []):
        for expr in N.walk_exprs_in(body):
            owner = getattr(expr, 'owner', None)
            name = getattr(owner, 'name', '')
            if name and conv.xref and conv.xref.is_quest_ref(name):
                conv._convert_ref(name, conv._script_extends)


def _promote_actor_locals(conv, tree) -> None:
    """Retype a `ref` local the body calls an ACTOR-only method on.

    TES4 is untyped, so `ref target` holds whatever the script puts in it; the
    Papyrus declaration has to commit, and calling `EvaluatePackage` on the
    variable means it is an Actor.  This runs BEFORE emission because the
    assignment that fills the variable is converted first, and the downcast it
    needs (`GetLinkedRef() as Actor`) depends on the answer.
    """
    sc = conv.sc
    for body in ([tree.preamble, tree.body]
                 + [b.body for b in tree.blocks] if tree else []):
        for expr in N.walk_exprs_in(body):
            # BOTH shapes name a subject: `target.GetDead` parses as a Member
            # (owner + name) and `target.SetActorValue x` as a Call with a
            # receiver.  Checking only the receiver missed every zero-argument
            # member form, which is how most actor tests are written.
            owner = getattr(expr, 'receiver', None) or getattr(
                expr, 'owner', None)
            name = getattr(owner, 'name', '')
            if not name:
                continue
            called = (expr.called or '') if expr.called else ''
            # A call promotes when it RESOLVES ITS SUBJECT AS AN ACTOR --
            # either the name is actor-only, or its row says so (`subj` ACTOR
            # or AV, which is what makes `myActivator.GetIsReference` an
            # actor).  Promoting on ANY known command over-fires:
            # `gate01.playgroup` is a command on a DOOR, and declaring
            # `Actor Property gate01` then cannot hold the door it is assigned.
            row = COMMAND_ROWS.get(called)
            actorish = (called in _ACTOR_ONLY_FUNCTIONS
                        or (row is not None and row.subj in ('ACTOR', 'AV')))
            # ...but a method ObjectReference ALSO declares proves nothing:
            # 14 of `_ACTOR_ONLY_FUNCTIONS` are shared, and `mySelf.PlaceAtMe`
            # on a spawner marker is the ObjectReference form.  Promoting on
            # one declared `Actor Property mySelf`, which cannot hold the
            # marker the script assigns to it.
            if not actorish or called in _OBJREF_SHARED_FUNCTIONS:
                continue
            low = name.lower()
            if sc.var_types.get(low) == 'ObjectReference':
                sc.var_types[low] = 'Actor'
                sc.var_types[_safe_property_name(name).lower()] = 'Actor'


def _load_symbols(conv, tree, editor_id: str) -> None:
    """Declared variables: their names, their types and their renames."""
    sc = conv.sc
    edid_low = (editor_id or '').lower()
    for var in (tree.variables if tree else ()):
        vname, vtype = var.name, var.vtype
        safe = _safe_property_name(vname)
        # BOTH spellings: the body still writes the variable the TES4 way, and
        # a name that collides with a TES4 command (DiveRockScript's `short
        # message`) is only recognised as a variable -- rather than compiled as
        # that command -- if the ORIGINAL spelling is in this set.
        sc.local_vars.add(vname.lower())
        sc.local_vars.add(safe.lower())

        ptype = TYPE_MAP.get(vtype.lower(), 'Int')
        # A `ref` the export proved is used as an integer is an Int here: TES4
        # let a script store either in the same slot.
        # BOTH spellings: the graph keys on the AUTHORED name while the
        # declaration carries the Papyrus-safe one, and a renamed variable
        # (`ref faction` -> `myFaction`) matched neither table under one alone.
        keys = {(edid_low, safe.lower()), (edid_low, vname.lower())}
        if ptype == 'ObjectReference' and edid_low and conv.xref:
            if keys & conv.xref.ref_as_int:
                ptype = 'Int'
            elif keys & conv.xref.ref_as_base_form:
                # ANOTHER script assigns a base record into this `ref`, which
                # the local pass cannot see -- it reads only this body.  Form
                # is the permissive handle both sides accept; a unanimous
                # narrower type still upgrades it in `_narrow_ref_types`.
                ptype = 'Form'
            else:
                attached = set()
                for key in keys:
                    attached.update(conv.xref.ref_script_types.get(key, ()))
                if len(attached) == 1:
                    candidate = next(iter(attached))
                    externally_read = (vname.lower() in
                                       conv.xref.cross_script_vars.get(
                                           edid_low, ()))
                    if (externally_read or
                            _uses_attached_member(tree, vname, candidate,
                                                  conv.xref)):
                        ptype = candidate
        sc.var_types[safe.lower()] = ptype
        sc.var_types[vname.lower()] = ptype
        # A DECLARED variable outranks anything the SCRO preload guessed for
        # the same name.  `_preload_scro_refs` runs first and types a name off
        # the record it binds, so DABoethiaCageOpenScript01's `Short
        # Salutation` -- which shares its name with the topic the script says
        # -- arrived here already typed `Topic`, and `Salutation = 1` then
        # compared a Topic against an Int.
        if safe in sc.property_refs and sc.property_refs[safe] != ptype:
            sc.property_refs[safe] = ptype

        # Compare CASE-SENSITIVELY: the `temp` -> `Temp` rename (which dodges
        # the compiler's ::temp* scratch-register namespace) differs only in
        # case, and a case-insensitive test skipped it -- leaving the
        # declaration renamed but every reference pointing at the old name.
        if safe != vname:
            sc.var_renames[vname.lower()] = safe

    # Result fragments can author indexed siblings (`item1`..`item6`) beside
    # one declared scalar (`item`). The TES4 VM creates those script variables
    # even when the source declaration table omitted them; the cross-reference
    # census has already copied the base variable's authored type.
    if conv.xref and edid_low:
        for name, ptype in conv.xref.synthetic_script_vars.get(edid_low, {}).items():
            safe = _safe_property_name(name)
            sc.synthetic_vars[safe] = ptype
            sc.local_vars.update((name.lower(), safe.lower()))
            sc.var_types[name.lower()] = ptype
            sc.var_types[safe.lower()] = ptype

    _narrow_ref_types(conv, tree)


def _uses_attached_member(tree, var_name: str, script_type: str, xref) -> bool:
    """Whether `var.member` needs the attached script's declared field."""
    if not script_type.startswith('TES4_'):
        return False
    fields = xref.script_all_vars.get(script_type[5:].lower(), {})
    if not fields:
        return False
    low = var_name.lower()
    bodies = ([tree.preamble, tree.body] + [b.body for b in tree.blocks]) \
        if tree else []
    for body in bodies:
        for expr in N.walk_exprs_in(body):
            owner = getattr(expr, 'receiver', None) or getattr(expr, 'owner', None)
            if (isinstance(owner, N.Ident) and owner.name.lower() == low
                    and getattr(expr, 'name', '').lower() in fields):
                return True
    return False


def _narrow_ref_types(conv, tree) -> None:
    """Retype each `ref` from what the body DOES with it.

    TES4's one `ref` type covers placed references, base records and integer
    flags, so the declaration says nothing: `ref weapon` assigned from
    `GetEquippedObject` is a Weapon, and Papyrus refuses the implicit
    conversion in both directions.  `symbols.resolve_ref_types` answers this
    from the tree BEFORE emission, so each declaration is written once and
    nothing downstream repairs it.
    """
    sc = conv.sc
    # Start from the AUTHORED `ref` declarations, not only the cross-script
    # preload's current guess.  A slot cleared with 0 can be preclassified Int
    # before this script's own comparisons prove it is a reference chain.
    refs = {var.name.lower() for var in tree.variables
            if var.vtype.lower() == 'ref'}
    refs.update(low for low, t in sc.var_types.items()
                if t in ('ObjectReference', 'Form'))
    if not refs or tree is None:
        return
    stmts = [st for block in tree.blocks for st in N.walk_stmts(block.body)]
    narrowed = _symbols.resolve_ref_types(
        stmts, refs, conv.type_of, conv._assignment_record_type)
    for low, ptype in narrowed.items():
        if (ptype.startswith('TES4_')
                and not _uses_attached_member(tree, low, ptype, conv.xref)):
            ptype = 'ObjectReference'
        existing = sc.var_types.get(low, '')
        if existing.startswith('TES4_') and ptype in ('ObjectReference', 'Form'):
            continue
        for spelling in (low, _safe_property_name(low).lower()):
            sc.var_types[spelling] = ptype
        safe = _safe_property_name(low)
        if safe in sc.property_refs:
            sc.property_refs[safe] = ptype


def _load_facts(conv, tree) -> None:
    """Feature flags, derived from the TREE rather than from the source text.

    A regex over the source matched inside comments and string literals: the
    old `\\btimer\\b` scan fired on `; count down timer` while missing the real
    `convTimer`, so 122 scripts polled at the wrong interval.
    """
    sc = conv.sc
    bodies = [tree.preamble, tree.body] + [b.body for b in tree.blocks] \
        if tree else []
    exprs = [e for b in bodies for e in N.walk_exprs_in(b)]
    called = {e.called for e in exprs if e.called}
    # A DECLARATION counts too: `Float Timer` means the script has a timer
    # even before any statement reads it.
    declared = {v.name.lower() for v in tree.variables} if tree else set()
    names = called | declared
    btypes = {b.btype.lower() for b in tree.blocks} if tree else set()

    sc.suppressed_fall_damage = 'resetfalldamagetimer' in called
    sc.uses_getsecondspassed = 'getsecondspassed' in called
    sc.gsp_realtime = bool(
        (called & {'getsecondspassed', 'scripteffectelapsedseconds'})
        and (btypes & {'gamemode', 'scripteffectupdate'}))
    if sc.gsp_realtime:
        # The synthesised elapsed-time variable must be TYPED for the
        # Float->Int coercion: TES4 `short` timers decremented by
        # getSecondsPassed (`damage = -50 * TES4_SecondsPassed`) need the
        # `as Int` cast the old float literal got via its own path.
        sc.var_types['tes4_secondspassed'] = 'Float'
        sc.var_types['tes4_lasttick'] = 'Float'

    sc.uses_timer = 'timer' in names
    sc.uses_say = bool(called & {'say', 'sayto'})
    sc.uses_say_timer = any(
        isinstance(st, N.Assign)
        and any(e.called in ('say', 'sayto')
                for e in N.walk_expr(st.value) if e.called)
        for b in bodies for st in N.walk_stmts(b))
    sc.uses_dropme = 'dropme' in called
    for body in bodies:
        for st in N.walk_stmts(body):
            if (isinstance(st, N.Assign) and isinstance(st.target, N.Ident)
                    and getattr(st.value, 'called', '') in ('getself', 'self')):
                sc.self_aliases.add(st.target.name.lower())
    # The hour-boundary guard: `GameHour >= 23.98`.
    sc.uses_hour_window = any(
        isinstance(e, N.BinOp) and e.op in ('>=', '<=')
        and e.left.called == 'gamehour'
        and isinstance(e.right, N.Literal) and '.' in e.right.text
        for e in exprs)

    blocks = tree.blocks if tree else []
    # A bare `begin MenuMode` merges into the GameMode poll, so it needs the
    # OnUpdate loop even when the script has no GameMode block of its own.
    sc.has_gamemode = any(
        b.btype.lower() == 'gamemode'
        or (b.btype.lower() == 'menumode' and not str(b.filter or '').strip()
            and not _reads_sleep_state(b.body))
        for b in blocks)
    sc.has_menumode = any(b.btype.lower() == 'menumode' for b in blocks)
    sc.has_scripteffectupdate = any(
        b.btype.lower() == 'scripteffectupdate' for b in blocks)


def _reads_sleep_state(body) -> bool:
    """Does this block body test the player's sleep state?"""
    return any(e.called in ('getpcissleeping', 'ispcsleeping',
                            'isplayersleeping')
               for e in N.walk_exprs_in(body) if e.called)


def header(conv, name: str, extends: str, editor_id: str) -> list:
    """The ScriptName line and the conversion docstring.

    Value-typed TES4 script variables must be readable by the engine's
    condition system: GetVMScriptVariable/GetVMQuestVariable (629/630) look up
    the mangled `::<name>_var` backing variable, which exists in the .pex only
    when BOTH the script and the auto-property carry the Conditional flag.
    Without it every converted GetScriptVariable/GetQuestVariable condition
    silently fails ("Unable to find variable ::X_var on any VM scripts").
    """
    conditional = any(t in ('Int', 'Float', 'Bool')
                      for t in conv.sc.var_types.values())
    flag = ' Conditional' if conditional else ''
    return [f'ScriptName {papyrus_script_name(name)} extends {extends}{flag}',
            f'{{Converted from TES4: {editor_id or name}}}',
            '']


def properties(conv, tree) -> list:
    """The script's variables, as auto-properties.

    An OBSE `begin Function{a, b}` declares its parameters as ordinary script
    variables.  They become the Papyrus Function's parameters, so they must NOT
    also be auto-properties: the parameter would shadow the property inside the
    body while callers write neither, leaving the body reading a permanent 0.
    """
    params = set()
    for block in (tree.blocks if tree else ()):
        if block.btype.lower() == 'function':
            for name in _udf_params(block.filter):
                params.add(name.lower())
                params.add(_safe_property_name(name).lower())

    out, seen = [], set()
    for var in (tree.variables if tree else ()):
        safe = _safe_property_name(var.name)
        low = safe.lower()
        if low in seen or low in params:
            continue
        seen.add(low)
        # A declared local is governed by the usage-driven variable table.
        # `property_refs` also contains SCRO preload guesses for external
        # records with the same name, including attached script types; letting
        # that table win retyped ordinary locals as Actor/script handles and
        # made their authored assignments illegal.  Actor call-site discovery
        # updates `var_types` together with `property_refs`, so no information
        # is lost by making the declaration table authoritative here.
        ptype = conv.sc.var_types.get(low, 'Int')
        out.append(_declare(safe, ptype))

    for safe, ptype in sorted(conv.sc.synthetic_vars.items()):
        low = safe.lower()
        if low in seen or low in params:
            continue
        seen.add(low)
        out.append(_declare(safe, ptype))

    # Every EXTERNAL record the body named -- a quest, a faction, a sound -- is
    # reached through a property too, and those are discovered WHILE the body
    # converts rather than from the declarations.  Missing them left the
    # emitted name undefined and failed the whole script to compile.
    for prop, ptype in sorted(conv.sc.property_refs.items()):
        low = prop.lower()
        if low in seen or low in params or not prop.isidentifier():
            continue
        seen.add(low)
        out.append(_declare(prop, ptype))

    if conv.sc.uses_dropme:
        out.append('ObjectReference TES4_Container = None')

    if out:
        out.append('')
    return out


def _declare(name: str, ptype: str) -> str:
    """One auto-property declaration.

    Value types carry `Conditional` so the engine's condition system can read
    the backing `::<name>_var` (GetVMScriptVariable 629/630); without it every
    converted GetScriptVariable condition silently fails.

    A Float is INITIALISED.  Papyrus defaults an uninitialised Float property
    to None rather than 0.0, so arithmetic on one before its first write reads
    as a type error -- TES4 started every declared variable at 0.
    """
    flag = ' Conditional' if ptype in ('Int', 'Float', 'Bool') else ''
    init = ' = 0.0' if ptype == 'Float' else ''
    return f'{ptype} Property {name}{init} Auto{flag}'


def udf(conv, tree, extends: str) -> list:
    """The `TES4Call` function an OBSE `begin Function{a, b}` script exposes.

    OBSE's user-defined function is a whole SCRIPT whose Function block is its
    body; callers reach it as `Call <ScriptName> args`, which converts to
    `<prop>.TES4Call(args)` on a property typed as that script.  Without this
    the callee declares no such function and every call site fails to compile
    (378 Nehrim failures, `GlobalScriptItemRequiredToUse` among them).

    The body converts BEFORE the parameters are typed: a TES4 `ref` is an
    untyped handle and the declaration alone is too weak to pick a Papyrus
    type.  `GlobalScriptAddSpellIfNotOwned` takes a `ref` every caller fills
    with a Spell; typing it ObjectReference rejects all its call sites, so the
    usage-driven inference runs first and its answer is read here.
    """
    block = next((b for b in (tree.blocks if tree else ())
                  if b.btype.lower() == 'function'), None)
    if block is None:
        return []
    params = _udf_params(block.filter)
    conv.sc.udf_params = {p.lower() for p in params}
    _script.emit_body(conv, block.body, extends, 1)
    # Record each parameter's final type BEFORE rendering the body's casts:
    # the widening to `Form` happens here, and a `type_of` that still answered
    # ObjectReference skipped the downcast the wider handle needs.
    types = [_param_type(conv, p) for p in params]
    for name, ptype in zip(params, types):
        for spelling in (name.lower(), _safe_property_name(name).lower()):
            conv.sc.var_types[spelling] = ptype
    lines = _script.emit_body(conv, block.body, extends, 1)
    sig = ', '.join('%s %s' % (ptype, _safe_property_name(name))
                    for name, ptype in zip(params, types))
    conv.sc.udf_signature = types
    rtype = 'Int ' if conv.sc.udf_returns else ''
    return ['%sFunction TES4Call(%s)' % (rtype, sig)] + lines + ['EndFunction',
                                                                 '']


def _param_type(conv, name: str) -> str:
    """The Papyrus type for one UDF parameter.

    Only a `ref` is ambiguous enough for usage to override the declaration --
    Int and Float came from an explicit TES4 type and mean what they say.  A
    `ref` with no usage evidence becomes `Form`, the permissive handle, so a
    caller passing any record still compiles.  `Form` counts as ambiguous too:
    it is what the cross-script `ref_as_base_form` pre-declaration leaves
    behind, and returning it unchanged typed `mwGetFactionWitnessesFunc`'s
    parameter Form while the body passed it to IsInFaction.
    """
    safe = _safe_property_name(name)
    declared = conv.sc.var_types.get(name.lower(), 'Int')
    if declared not in ('ObjectReference', 'Form'):
        return declared
    return (conv.sc.var_types.get(safe.lower())
            if conv.sc.var_types.get(safe.lower()) not in
            (None, 'ObjectReference', 'Form')
            else conv.sc.property_refs.get(safe)
            or conv.sc.property_refs.get(safe.lower()) or 'Form')


def _udf_params(block_filter: str) -> list:
    """Parameter names from an OBSE `begin Function{a, b}` header.

    OBSE accepts either separator and Nehrim uses both -- `Function{ ItemType,
    ItemAmount }` and `Function{ refRuneSpell levelRequired}`.  Splitting on
    the comma alone read the second as ONE parameter, so the emitted signature
    took 1 argument where every caller passed 2 (170 arity failures).
    """
    text = (block_filter or '').strip().strip('{}').replace(',', ' ')
    return text.split()


def events(conv, tree, extends: str, skip_poll: bool = False) -> list:
    """One Papyrus event per TES4 block, duplicates merged.

    Papyrus forbids two events of the same name, but TES4 allows several blocks
    of one type guarded on different parameters (two OnContainerChanged blocks
    with different filters), so same-typed blocks merge into one event whose
    body is the guarded arms in source order.
    """
    merged, order = {}, []
    for block in (tree.blocks if tree else ()):
        header = BLOCK_MAP.get(block.btype.lower())
        if header is None or (skip_poll and header[0] == 'Event OnUpdate()'):
            continue
        if block.btype.lower() == 'menumode':
            # Every MenuMode fate is handled elsewhere: the poll absorbs the
            # bare bookkeeping bodies, `sleep_listener` takes the sleep idiom,
            # and a menu-ID block has no convertible trigger at all.
            continue
        # The EVENT is context for the body: TES4's GetActionRef is legal in
        # every block, but Papyrus scopes each event's parameters, so the
        # subject a bare `GetActionRef` means depends on which event we are
        # inside (see `_get_action_ref_param`).
        conv._current_event = header[0]
        conv.sc.current_block_type = block.btype.lower()
        body = _script.emit_body(conv, block.body, extends, 1)
        conv._current_event = ''
        conv.sc.current_block_type = ''
        consumes = (block.btype.lower() == 'onactivate'
                    and _consumes_activation(conv, tree))
        if not body and not consumes:
            continue
        body = _guarded(conv, block, body)
        if consumes:
            body = door_preamble(conv, extends) + body
        if block.btype.lower() == 'onactivate':
            body = gate_capture(tree, extends) + body
        if header not in merged:
            merged[header] = []
            order.append(header)
        merged[header] += body

    out = []
    for header in order:
        opener, closer = header
        out.append(opener)
        if conv.sc.uses_dropme and opener.startswith('Event OnContainerChanged('):
            out.append('  TES4_Container = akNewContainer')
        elif conv.sc.uses_dropme and opener.startswith('Event OnEquipped('):
            out.append('  TES4_Container = akActor')
        out += merged[header]
        out.append(closer)
        out.append('')
        # TES4's `begin OnTrigger` runs EVERY FRAME an object is inside the
        # volume.  Skyrim splits that: OnTriggerEnter is the entry frame and
        # OnTrigger the repeat, so a converted OnTrigger body alone never runs
        # on entry.  Emitting BOTH keeps each event's meaning, and the entry
        # event just calls the repeat one so the body exists once.  Skipped
        # when the script authors its own OnTriggerEnter -- Papyrus allows one
        # definition per event and the author's body is authoritative.
        if (opener == BLOCK_MAP['ontrigger'][0]
                and BLOCK_MAP['ontriggerenter'] not in merged):
            out += ['Event OnTriggerEnter(ObjectReference akActionRef)',
                    '  ; Entry frame: Skyrim sends OnTriggerEnter, not '
                    'OnTrigger (vanilla Tripwire/PressurePlate do the same).  '
                    'Repeat ticks still arrive on OnTrigger.',
                    '  OnTrigger(akActionRef)',
                    'EndEvent',
                    '']
    if (conv.sc.uses_dropme
            and BLOCK_MAP['onadd'] not in merged
            and BLOCK_MAP['ondrop'] not in merged):
        out += ['Event OnContainerChanged(ObjectReference akNewContainer, '
                'ObjectReference akOldContainer)',
                '  TES4_Container = akNewContainer',
                'EndEvent', '']
    return out


def helpers(conv) -> list:
    """The synthesised helpers this script's own conversions asked for."""
    out = []
    out += conv.get_cell_family_helpers()
    out += conv._emit_button_helpers()
    return out


#: The insurance arm's interval.  Long ON PURPOSE -- see `poll`.
_INSURANCE_SECS = '5.0'


def poll(conv, tree, extends: str) -> list:
    """The OnUpdate loop that replaces TES4's per-frame `begin GameMode`.

    Emitted whenever the script DECLARES a poll block, empty or not: an empty
    `begin ScriptEffectUpdate` still declares one, and 19 scripts have one
    (GhostEffectScript's is empty by design -- the work is in
    ScriptEffectStart, and the update block exists to keep the effect alive).
    Gating on a non-empty body dropped the whole event for them.
    """
    sc = conv.sc
    if not (sc.has_gamemode or sc.has_scripteffectupdate):
        return []

    interval = conv._get_update_interval()
    conv._current_event = 'Event OnUpdate()'
    load_gated = extends in ('ObjectReference', 'Actor')

    out = []
    if sc.gsp_realtime:
        # Backing state for TES4_SecondsPassed: plain script variables, not
        # properties -- nothing outside this script reads them and they must
        # not appear in the VMAD.
        out += [f'Float TES4_SecondsPassed = {interval}',
                'Float TES4_LastTick = 0.0', '']
    out.append('Event OnUpdate()')

    # Arm the poll TWICE: an insurance arm at the TOP and the real re-arm at
    # the BOTTOM.
    #
    # The TOP arm is abort insurance ONLY, and it is LONG.  A runtime error
    # anywhere in the body ("Cannot call X on a None object", a bad cast)
    # ABORTS the event at that line, and with only a bottom re-register one
    # abort silently killed the poll for the rest of the game -- the
    # intermittent "the NPCs just stand there" class of failure.
    #
    # It must NOT arm at the real interval.  RegisterForSingleUpdate counts
    # from NOW, so a top arm at `interval` starts the next pass `interval`
    # after this one STARTED -- and a pass whose body takes longer than that
    # (MQ01Script's tutorial poll: ~15 latent natives per 0.1s tick) overlaps
    # itself, every overlap slows the VM further, and the pile grows without
    # bound.  Measured in game 251 concurrent OnUpdate stacks, End
    # fragments of 1-2s lines running 19-24s late.  A 5s insurance arm bounds
    # the overlap to one extra stack per 5s of blocking, and any pass that
    # finishes replaces it with the real interval.
    out += _arm(conv, _INSURANCE_SECS, load_gated)

    # A TES4 `return` inside the polled body ends THIS pass only, so the
    # converted `Return` must re-arm at the real interval itself: it skips the
    # bottom arm and the top arm is the long one.
    sc.poll_return_prefix = '\n'.join(
        _arm(conv, interval, load_gated, indent='')) + '\n'

    if extends == 'Quest':
        # Not running: skip the body, but the poll keeps ticking so the loop
        # resumes once the quest is started.
        out += ['  If (!IsRunning())', '    Return', '  EndIf']

    out += _dialogue_gate(conv, extends, load_gated)
    out += _elapsed_prologue(conv, interval)

    for block in (tree.blocks if tree else ()):
        btype = block.btype.lower()
        if btype in POLL_BLOCKS or (btype == 'menumode'
                                     and _menumode_kind(block) == 'poll'):
            out += _script.emit_body(conv, block.body, extends, 1)

    # Stage-arrival latches: record the stage each guarded quest is on NOW, so
    # the next pass can tell "we have already seen this stage" from "it just
    # arrived".  Emitted at the very END so every guard above compared against
    # the PREVIOUS pass's value.
    for _, var in sorted(sc.stage_latches.items()):
        quest = var[len('TES4_LastStage_'):]
        out.append(f'  {var} = {quest}.GetStage()')

    sc.poll_return_prefix = ''
    out += _arm(conv, interval, load_gated)
    out += ['EndEvent', '']
    return out


def _arm(conv, secs: str, load_gated: bool, indent: str = '  ') -> list:
    """Re-arm the poll, gated on the script's reference being live."""
    if not load_gated:
        return [f'{indent}RegisterForSingleUpdate({secs})']
    return [f'{indent}If ({conv._GAMEMODE_GATE})',
            f'{indent}  RegisterForSingleUpdate({secs})',
            f'{indent}EndIf']


def _dialogue_gate(conv, extends: str, load_gated: bool) -> list:
    """Skip this pass while the player is in a dialogue menu.

    TES4 GameMode never ran while a menu was open.  Measured in game (CharacterGen 40-50): the Emperor's poll fired during his
    stage-42 dialogue and its Say() INTERRUPTED his 17.8s Goodbye reply, so
    the reply's `setstage 43` was lost and the birthsign menu never opened;
    the quest poll's stage-45 guard fired during the stage-44 dialogue and
    sent Baurus in to force-greet over it.

    Only scripts that SPEAK carry the gate.  Applying it to every poll (first
    attempt, same day) put PlayerIsInDialogue on ~210 quest polls at 0.1s and
    the VM starved: End fragments ran 11-17s late and "Yessir" played twice.
    A non-speaking poll cannot cut a line.
    """
    if not conv.sc.uses_say or extends not in ('Actor', 'Quest'):
        return []
    test = ('IsInDialogueWithPlayer() || TES4Polyfill.PlayerIsInDialogue()'
            if extends == 'Actor' else 'TES4Polyfill.PlayerIsInDialogue()')
    return ([f'  If {test}  '
             '; TES4 GameMode did not run while a menu was open']
            + _arm(conv, '0.5', load_gated, indent='    ')
            + ['    Return', '  EndIf'])


def _elapsed_prologue(conv, interval: str) -> list:
    """Measure the REAL time this pass took, for getSecondsPassed.

    TES4 getSecondsPassed returned the time the frame actually took.  Measuring
    it beats assuming the tick interval: RegisterForSingleUpdate delivers late
    under VM load, and a fixed decrement then drains every counted timer slower
    than real time, so all conversation pacing floated with load.  Every read
    this pass sees the same value, exactly like TES4's per-frame constant.

    The clamp covers the first pass and resumption after unload, menus or a
    save-load, where the raw delta spans a gap TES4 never counted.
    """
    if not conv.sc.gsp_realtime:
        return []
    return ['  Float TES4_Now = Utility.GetCurrentRealTime()',
            '  TES4_SecondsPassed = TES4_Now - TES4_LastTick',
            '  If TES4_SecondsPassed < 0.0 || TES4_SecondsPassed > 2.0',
            f'    TES4_SecondsPassed = {interval}',
            '  EndIf',
            '  TES4_LastTick = TES4_Now']


def lifecycle(conv, tree, extends: str) -> list:
    """The events that START the poll loop.

    Four events arm it -- OnCellAttach, OnLoad and two OnInit shapes -- and
    they arm it identically, so `_start` is one definition rather than four
    copies that can drift apart.
    """
    sc = conv.sc
    sleeps = any(b.btype.lower() == 'menumode' and _menumode_kind(b) == 'sleep'
                 for b in (tree.blocks if tree else ()))
    if not (sc.has_gamemode or sc.has_scripteffectupdate or sleeps):
        return []
    interval = conv._get_update_interval()
    declared = {b.btype.lower() for b in (tree.blocks if tree else ())}
    # The sleep listener shares the poll's lifecycle: TES4 MenuMode also ran
    # only while the script's owner was loaded / its quest instantiated.
    start = ([f'  RegisterForSingleUpdate({interval})']
             if (sc.has_gamemode or sc.has_scripteffectupdate) else [])
    if sleeps:
        start.append('  RegisterForSleep()')

    if extends not in ('ObjectReference', 'Actor'):
        return [] if 'oninit' in declared else (
            ['Event OnInit()'] + start + ['EndEvent', ''])

    # Object/actor: run only while loaded.  OnCellAttach fires each time the
    # reference streams into an active cell, which confines the loop to when
    # the object is actually present, exactly like TES4 GameMode.
    #
    # NO UnregisterForUpdate on OnCellDetach.  Cell-transition events arrive in
    # no guaranteed order, so the detach for the OLD cell could land after
    # OnLoad/OnCellAttach had already re-armed the poll for the NEW one and
    # silently kill a loaded actor's loop mid-scene (the CharacterGen escort
    # NPCs went mute this way -- "sometimes they talk, sometimes nothing").
    # The arm-first gate in OnUpdate stops the loop by itself one tick after
    # the 3D goes away, so the unregister bought nothing but the race.
    out = ['Event OnCellAttach()'] + start + ['EndEvent', '']
    if sleeps:
        out += ['Event OnCellDetach()', '  UnregisterForSleep()',
                'EndEvent', '']

    # OnCellAttach only fires when a cell BECOMES attached.  A persistent actor
    # standing in an already-attached cell when the script is first bound (new
    # game, or the player is simply already there) never gets that event, so
    # the poll would never start and a GameMode variable the rest of the quest
    # depends on stays 0 forever.  That kept Arielle (MG04Restore) standing
    # still: her package waits on `startconv == 1`, which only her GameMode
    # body ever sets.
    #
    # OnInit ALONE is not enough once the script lives on the placed reference
    # (which reference events like OnPackageEnd require): on a reference OnInit
    # runs at load BEFORE the 3D exists, so the gate is false and the poll
    # never starts -- that is what silenced Valen Dreth.  OnLoad means "this
    # object is completely loaded ... fired every time this object is loaded"
    # (vanilla ObjectReference.psc), so it starts the loop for an actor already
    # standing in the player's current cell, which OnCellAttach cannot do.
    if 'onload' not in declared:
        out += ['Event OnLoad()'] + start + ['EndEvent', '']
    if 'oninit' not in declared:
        # Gating keeps the anti-storm property: the gate is true ONLY for
        # references that are actually loaded, so this cannot re-create the
        # "every scripted object in the game starts ticking at load" failure
        # an unconditional OnInit register caused.
        out += (['Event OnInit()', f'  If ({conv._GAMEMODE_GATE})']
                + [f'  {line}' for line in start]
                + ['  EndIf', 'EndEvent', ''])
    return out


# ---------------------------------------------------------------------------
# Synthesised events
# ---------------------------------------------------------------------------

def trap_hit(conv, tree, extends: str) -> list:
    """The contact-damage event TES4's ENGINE used to raise.

    When a Havok body on layer 14 (OL_TRAP) struck an actor, Oblivion read this
    script's own `fTrapDamage` variables and dealt the hit itself.  Skyrim
    raises OnTrapHitStart instead and leaves the damage to the script, exactly
    as vanilla TrapHitBase.psc does -- so a script that DECLARES those
    variables gets the event synthesised for it.
    """
    if extends not in ('ObjectReference', 'Actor'):
        return []
    damage = _declared(tree, 'ftrapdamage')
    if not damage:
        return []
    levelled = _declared(tree, 'flevelleddamage')
    pushback = _declared(tree, 'ftrappushback') or '0.0'
    total = damage + (f' + {levelled} * victim.GetLevel()' if levelled else '')
    return [
        'Event OnTrapHitStart(ObjectReference akTarget, float afXVel, '
        'float afYVel, float afZVel, float afXPos, float afYPos, '
        'float afZPos, int aeMaterial, bool abInitialHit, int aeMotionType)',
        "  ; TES4's engine read this script's fTrapDamage variables when an "
        'OL_TRAP',
        '  ; body struck an actor.  Skyrim raises OnTrapHitStart instead and '
        'the',
        '  ; script deals the hit itself, like vanilla TrapHitBase.psc.',
        '  Actor victim = akTarget as Actor',
        '  If victim == None',
        '    Return',
        '  EndIf',
        f'  Float totalDamage = {total}',
        '  If totalDamage <= 0.0',
        '    Return   ; not armed yet - TES4 variables start at 0',
        '  EndIf',
        f'  akTarget.ProcessTrapHit(Self, totalDamage, {pushback}, '
        'afXVel, afYVel, afZVel, afXPos, afYPos, afZPos, aeMaterial, 0.0)',
        'EndEvent',
        '']


def _declared(tree, name_low: str):
    """The Papyrus-safe name of a variable this script declares, or None."""
    for var in (tree.variables if tree else ()):
        if var.name.lower() == name_low:
            return _safe_property_name(var.name)
    return None


def door_relock(conv, tree, extends: str) -> list:
    """Restore the authored lock lifted for an AI door passage.

    Companion to the door preamble in `events`: TES4 has no OnClose event, so
    this can never collide with an authored block.
    """
    if extends != 'ObjectReference' or not _consumes_activation(conv, tree):
        return []
    if not any(b.btype.lower() == 'onactivate'
               for b in (tree.blocks if tree else ())):
        return []
    return ['Int TES4_pendingRelock = 0',
            '',
            'Event OnClose(ObjectReference akActionRef)',
            '  ; Restore the authored lock lifted for an AI door passage '
            '(see OnActivate).',
            '  If TES4_pendingRelock > 0',
            '    Lock(true)',
            '    SetLockLevel(TES4_pendingRelock)',
            '    TES4_pendingRelock = 0',
            '  EndIf',
            'EndEvent',
            '']


def _consumes_activation(conv, tree) -> bool:
    """Does this script's OnActivate REPLACE default activation?"""
    blocks = [(b.btype.lower(), b.filter, b.body)
              for b in (tree.blocks if tree else ())]
    return conv._onactivate_consumes(blocks)


def gate_capture(tree, extends: str) -> list:
    """Remember the Oblivion gate the player just entered, before it is lost.

    The authored line `set MQ00.nearOblivionGate to 0` marks the entry AND
    discards the gate's identity, so the capture goes AHEAD of the body.  All
    5 vanilla gate scripts use the idiom; nothing else writes that variable 0.
    """
    if extends not in ('ObjectReference', 'Actor') \
            or not _is_gate_entry(tree):
        return []
    return ['  If akActionRef == Game.GetPlayer()',
            '    TES4Polyfill.EnterOblivionGate(Self)',
            '  EndIf']


def _is_gate_entry(tree) -> bool:
    """Does an OnActivate clear `MQ00.nearOblivionGate` behind a player test?"""
    for block in (tree.blocks if tree else ()):
        if block.btype.lower() != 'onactivate':
            continue
        if 'isactionref' not in {e.called for e in N.walk_exprs_in(block.body)}:
            continue
        for st in N.walk_stmts(block.body):
            target = getattr(st, 'target', None)
            if (isinstance(st, N.Assign) and isinstance(target, N.Member)
                    and target.name.lower() == 'nearobliviongate'
                    and isinstance(st.value, N.Literal)
                    and st.value.text.strip() == '0'):
                return True
    return False


def door_preamble(conv, extends: str) -> list:
    """TES4 parity: AI door-use ignores this script AND the lock.

    An NPC opening a door does not run the door's script in Oblivion, and the
    authored lock is restored when the door next closes (see `door_relock`).
    """
    if extends != 'ObjectReference':
        return []
    return ['  If (GetBaseObject() as Door) && (akActionRef as Actor) && '
            'akActionRef != Game.GetPlayer()',
            '    ; TES4 parity: AI door-use ignores this script and the lock; '
            'the authored lock is restored when the door next closes.',
            '    If GetOpenState() >= 3',
            '      TES4_pendingRelock = GetLockLevel()',
            '      If IsLocked()',
            '        Lock(false)',
            '      EndIf',
            '      Activate(akActionRef, true)',
            '    EndIf',
            '    Return',
            '  EndIf']


def fall_damage(conv, extends: str, body: list) -> list:
    """Restore the fall-damage threshold a ResetFallDamageTimer raised.

    The OBSE call raises a GLOBAL GMST, so leaving it set disables fall damage
    permanently.  The restore joins the teardown event the script ALREADY has;
    only a script with none gets a synthesized one.
    """
    if not conv.sc.suppressed_fall_damage:
        return body
    return conv._append_fall_damage_restore(body, extends)


def block_activation(conv, tree, extends: str, body: list) -> list:
    """Block default activation for a script whose OnActivate CONSUMES it.

    TES4's contract: the PRESENCE of an OnActivate block REPLACES the default
    activation.  Papyrus runs both unless the script says otherwise, so a door
    with a consuming OnActivate would open AND run its script.

    Vanilla defaultBlockActivation.psc applies the call from OnLoad ("block
    activation upon loading"), so it rides an existing OnLoad when the
    lifecycle phase emitted one -- inserted before any gating If, since
    blocking must be unconditional -- and otherwise gets an OnLoad of its own.
    """
    if extends not in ('ObjectReference', 'Actor'):
        return body
    if not _consumes_activation(conv, tree):
        return body
    for i, line in enumerate(body):
        if line.strip() == 'Event OnLoad()':
            return body[:i + 1] + ['  BlockActivation(true)'] + body[i + 1:]
    return body + ['Event OnLoad()', '  BlockActivation(true)', 'EndEvent', '']



def _guarded(conv, block, body: list) -> list:
    """Wrap a block body in the condition its TES4 FILTER stood for.

    `begin OnEquip player` fires ONLY when the player equips the item;
    `begin OnPackageDone SomePkg` only when that package ends.  Papyrus events
    carry no filter, so the restriction becomes an `If` around the body --
    without it an item's "you cannot equip this" message fired for every NPC
    the moment they loaded in.
    """
    btype = block.btype.lower()
    guard = conv._block_filter_guard(btype, block.filter or '')
    state = COMBAT_STATE_GUARDS.get(btype)
    if state and guard is not None:
        guard = f'{state} && {guard}' if guard else state

    if guard is None:
        # The filter EXISTS but cannot be expressed, and running the body for
        # every event would be wrong -- keep it visible but inert.
        return (['  ; TES4 block filter could not be converted; '
                 'body preserved but NOT executed:']
                + [f'  ;{line.strip()}' for line in body])
    if not guard:
        return body
    return ([f'  If {guard}']
            + [f'  {line}' for line in body]
            + ['  EndIf'])


# ---------------------------------------------------------------------------
# MenuMode
# ---------------------------------------------------------------------------

def _menumode_kind(block) -> str:
    """Which of the three MenuMode fates this block takes.

    `begin MenuMode <id>` fires ONLY while that specific menu is open (1014 =
    lockpicking, 1030 = class, 1002 = inventory).  Skyrim has no per-menu
    equivalent -- Utility.IsInMenuMode() is only "some menu is open" -- so
    there is nothing to convert the trigger to.  Merging those into the
    GameMode poll ran them on the first tick as if every menu were open at
    once: MQ01Script's 1014 and 1030 blocks `setstage MQ01 70`/`84`
    unconditionally, so the tutorial blew through its whole stage machine on a
    new game and hit stage 100's `stopquest MQ01`.

    A BARE block reading isPCSleeping is the SLEEP idiom: in Oblivion the only
    frames where isPCSleeping is 1 are sleep-menu frames, so those bodies are
    self-gated and exist purely to observe sleep (Rufio's murder, vampirism
    onset, MG04's inn ambush).  Skyrim's native equivalent is
    RegisterForSleep().

    A bare block that does NOT read it is time-and-inventory bookkeeping that
    Oblivion ran on the frames GameMode did not -- wait/sleep and inventory
    frames.  Censused over the corpus, not one bare block is a menu-specific
    trigger, and dropping them silently deletes logic: MelisandeScript's body
    holds the ONLY `set MS40.cureready to 1` in the plugin.  Merging them into
    the poll reproduces the union of frames rather than half of it; they are
    all idempotent state machines guarded by their own doonce variables.
    """
    if str(block.filter or '').strip():
        return 'menu'
    return 'sleep' if _reads_sleep_state(block.body) else 'poll'


def sleep_listener(conv, tree, extends: str) -> list:
    """Sleep-idiom MenuMode bodies, as real Papyrus sleep listeners.

    Oblivion ran the body every menu frame while the player slept; the two
    Skyrim-observable moments are the start and stop events, so the body runs
    once in each (several bodies need two passes: MG04 records GameHour on the
    first and arms its trigger on the second).  isPCSleeping reads inside the
    body compile to the TES4_PCSleeping flag, which is 1 for BOTH passes --
    matching Oblivion, where every frame that executed the body had it set.
    """
    blocks = [b for b in (tree.blocks if tree else ())
              if b.btype.lower() == 'menumode' and _menumode_kind(b) == 'sleep']
    if not blocks:
        return []

    conv._current_event = 'Function TES4_MenuModeSleepBody()'
    conv.sc.in_sleep_menumode = True
    body = []
    for block in blocks:
        body += _script.emit_body(conv, block.body, extends, 1)
    conv.sc.in_sleep_menumode = False
    conv._current_event = ''

    out = ['Int TES4_PCSleeping = 0', '', 'Function TES4_MenuModeSleepBody()']
    if extends == 'Quest':
        out += ['  If (!IsRunning())', '    Return', '  EndIf']
    out += body + ['EndFunction', '']
    out += ['Event OnSleepStart(float afSleepStartTime, '
            'float afDesiredSleepEndTime)',
            '  TES4_PCSleeping = 1',
            '  TES4_MenuModeSleepBody()',
            'EndEvent',
            '',
            'Event OnSleepStop(bool abInterrupted)',
            '  TES4_MenuModeSleepBody()',
            '  TES4_PCSleeping = 0',
            'EndEvent',
            '']
    return out


def menu_blocks(conv, tree, extends: str) -> list:
    """Menu-ID MenuMode bodies, preserved as COMMENTS.

    `begin MenuMode <id>` has no Skyrim trigger to convert to, so the body must
    not execute -- but it is converted rather than dumped raw, so a hand-port
    only has to supply the menu hook instead of redoing the translation.
    """
    out = []
    for block in (tree.blocks if tree else ()):
        if block.btype.lower() != 'menumode' or _menumode_kind(block) != 'menu':
            continue
        label = ('MenuMode %s' % (block.filter or '')).strip()
        out.append('; --- TES4 `begin %s` - no Skyrim equivalent; '
                   'body preserved but NOT executed ---' % label)
        for line in _script.emit_body(conv, block.body, extends, 1):
            if line.strip():
                out.append(';  %s' % line.strip())
        out.append('')
    return out


def stage_latches(conv) -> list:
    """Declare the stage-arrival latches `_guard_stage_timer` reads.

    Initialised to -1 so the FIRST pass at a stage never satisfies
    `latch == N`: the guard then waits one pass, which is what lets that
    stage's fragment charge the timer before the poll tests it.
    """
    if not conv.sc.stage_latches:
        return []
    out = ['']
    for _, var in sorted(conv.sc.stage_latches.items()):
        quest = var[len('TES4_LastStage_'):]
        out.append('Int %s = -1  ; stage of %s on the previous poll pass'
                   % (var, quest))
    return out


def chargen_latch(conv) -> list:
    """Declare the re-entrancy latch the modal chargen menus read.

    Papyrus parks only the thread that called Show(), so the poll's next tick
    re-enters the same body while the menu is still open.  Without the latch
    every queued tick re-showed the menu ("I had to click through it multiple
    times").
    """
    if not conv.sc.uses_chargen_menus:
        return []
    return ['', 'Bool TES4_ChargenMenuBusy = False']



def _promote_assigned_actors(conv, tree) -> None:
    """Retype a `ref` local FILLED by an Actor-returning native.

    TES4 declares `ref combatTarget` and then writes `GetCombatTarget` into
    it; the Papyrus native returns an Actor, so the variable is one even when
    the script never calls an actor-only method through it (CGEmperorScript
    only compares it and passes it on).  Without this the declaration stayed
    ObjectReference and the pass-on needed a cast HEAD does not emit.
    """
    from script_convert.converter import _call_return_type
    sc = conv.sc
    for body in ([tree.preamble, tree.body]
                 + [b.body for b in tree.blocks] if tree else []):
        for st in N.walk_stmts(body):
            if not isinstance(st, N.Assign):
                continue
            name = getattr(st.target, 'name', '')
            called = getattr(st.value, 'called', '') or ''
            if not name or not called:
                continue
            if _call_return_type(called) != 'Actor':
                continue
            low = name.lower()
            if sc.var_types.get(low) == 'ObjectReference':
                sc.var_types[low] = 'Actor'
                sc.var_types[_safe_property_name(name).lower()] = 'Actor'
