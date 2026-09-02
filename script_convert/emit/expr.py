"""TES4 expression tree -> Papyrus text.

Replaces `_convert_expression`, which was 850 lines because it re-derived
structure from a string at every step: a depth-aware scan to find the top-level
comparison operator, a hand-built mask marking which characters sit inside a
string literal, right-to-left loops over `('+','-')` then `('*','/','%')` to
find the arithmetic split, and regexes to tell `ref.Func args` from `Func args`
from a bare name.  The parser answers all of that -- `BinOp`, `Unary`, `Call`,
`Member`, `Ident`, `Literal` -- so what is left here is only the TES4->TES5
SEMANTICS, which is what this module is.

Emission is NOT pure.  Converting an expression discovers external references
(`MQ01` is a Quest, `ArmandRef` is an Actor) and registers them on the
converter's `_property_refs`, which the importer reads to build VMAD property
bindings.  So every function here takes the converter and may write to it.
"""

from __future__ import annotations


from script_convert.constants import (
    BASE_FORM_TYPES, OP_MAP, PAPYRUS_BOOL_FUNCTIONS, EVENT_REF_PARAMS,
    MISMATCH_TYPES,
    _PAPYRUS_VALUE_TYPES)
from script_convert.emit.commands import (
    BOOL_TEMPLATE_COMMANDS, COMPARISON_COMMANDS, INERT_COMMANDS,
)
from script_convert import resolve_name as _resolve
from script_convert.tes4 import lexer as L, nodes as N

# Papyrus binds `as` tighter than the arithmetic operators, so a cast applied
# to one operand does not type the whole expression.  These are the operators
# whose result needs parenthesising before a cast can apply to it.
_ARITH = frozenset({'+', '-', '*', '/', '%'})

#: Papyrus types whose values compare against `None`, not `0`.  TES4 spelled a
#: null reference `0`, so `if ref == 0` has to become `if ref == None` -- and
#: never for a value type, because an Int that happens to hold 0 must not.
#:
#: Stated as "not a value type" rather than as a list of object types: the
#: pre-emission resolver assigns whatever class the records actually are
#: (Form, Cell, Armor, Spell, EffectShader...), and an enumeration missed all
#: of them -- `Form != 0` does not compile.
REF_TYPES = frozenset({'objectreference', 'actor', 'actorbase'})

_VALUE_TYPES_LOW = frozenset(t.lower() for t in _PAPYRUS_VALUE_TYPES)

#: Calls that RETURN a reference, so `x.GetContainer() == 0` is also a null
#: test even though the receiver's own type says nothing.
_REF_RETURNING = frozenset({
    'getcontainer', 'getlinkedref', 'getparentref', 'getself',
    'getactionref', 'getcombattarget', 'getcrimeknown',
})


def is_ref_typed(conv, node: N.Expr) -> bool:
    """Does this expression evaluate to an object reference?

    Drives the `== 0` -> `== None` rewrite below.  A wrong answer here is a
    compile error in one direction (`Int == None`) and a silently-true test in
    the other, so it checks the declared type, the two literal self-forms, and
    the ref-returning calls -- exactly the four routes the string version used.
    """
    if isinstance(node, N.Ident):
        low = node.name.lower()
        if low in ('self', 'this', 'getself'):
            return True
        # An event parameter is an ObjectReference the script never declares,
        # so no lookup can find its type.
        if low in EVENT_REF_PARAMS:
            return True
        # TES4 reads a zero-argument command bare, so `getActionRef` arrives as
        # an Ident, not a Call -- the same name in either shape is the same
        # reference-returning command.
        if low in _REF_RETURNING:
            return True
        # Case-insensitive: `_property_refs` is keyed by the AUTHORED spelling,
        # so a source `timer` does not find a registered `Timer` by exact
        # lookup -- and getting that wrong emitted `Timer <= 0` on an
        # ObjectReference, which does not compile.
        t = (conv.type_of(node.name)
             or conv._property_type_ci(node.name)).lower()
        return bool(t) and t not in _VALUE_TYPES_LOW
    if isinstance(node, N.Call):
        return node.name.lower() in _REF_RETURNING
    if isinstance(node, N.Member):
        # `Owner.var` on another converted script.  BOTH routes are needed:
        # `remote_type_of` resolves through the owner's declared `TES4_<script>`
        # property type, but a quest named bare (`SE09.rebuiltGatekeeperRef`)
        # has no such property yet -- `_is_ref_typed_access` reaches it via
        # EditorID -> SCRI.  Missing that emitted `!= 0` on a reference, which
        # does not compile.
        dotted = f'{emit_bare(conv, node.owner)}.{node.name}'
        return (conv.remote_type_of(dotted).lower() in REF_TYPES
                or conv._is_ref_typed_access(dotted))
    return False


def emit_bare(conv, node: N.Expr) -> str:
    """Name of a node without conversion -- for building a lookup key."""
    if isinstance(node, N.Ident):
        return node.name
    if isinstance(node, N.Member):
        return f'{emit_bare(conv, node.owner)}.{node.name}'
    return ''


def emit_source(node: N.Expr) -> str:
    """Reconstruct the TES4 source text of a node.

    Needed only while `emit_call` still hands a STRING to `_emit_function`;
    R4 deletes both this and that rebuild by passing the nodes straight
    through.  It is deliberately a faithful TES4 spelling, not Papyrus --
    `_emit_function` re-converts what it is given.

    The author's own parentheses are reproduced (`Expr.parenthesised`): the
    converter echoes them into the output, so dropping them here made 649
    assignment values differ for no reason.
    """
    if getattr(node, 'parenthesised', False):
        return f'({_emit_source_bare(node)})'
    return _emit_source_bare(node)


def _emit_source_bare(node: N.Expr) -> str:
    """`emit_source` without the author's outer parentheses."""
    if isinstance(node, N.Literal):
        return node.text
    if isinstance(node, N.Ident):
        return node.name
    if isinstance(node, N.Member):
        return f'{emit_source(node.owner)}.{node.name}'
    if isinstance(node, N.Index):
        return f'{emit_source(node.target)}[{emit_source(node.index)}]'
    if isinstance(node, N.Unary):
        return f'{node.op}{emit_source(node.operand)}'
    if isinstance(node, N.BinOp):
        return f'{emit_source(node.left)} {node.op} {emit_source(node.right)}'
    if isinstance(node, N.Call):
        head = f'{emit_source(node.receiver)}.' if node.receiver else ''
        args = ' '.join(emit_source(a) for a in node.args)
        # The LEADING comma is meaningful, not punctuation: for a
        # zero-argument command the token after it is the receiver, so
        # `StopCombat, Player` is Player's combat and dropping the comma made
        # the call act on Self.
        if node.leading_comma and args:
            args = f', {args}'
        return f'{head}{node.name} {args}'.rstrip()
    if isinstance(node, N.Raw):
        return node.text
    return ''


def _is_zero(node: N.Expr) -> bool:
    return isinstance(node, N.Literal) and not node.is_string \
        and node.text.strip() in ('0', '0.0')


def _is_bool_valued(conv, node: N.Expr) -> bool:
    """Is this already a Papyrus Bool, so `== 1` is redundant?

    TES4 comparisons yield 0/1 and the idiom `if (a == b) == 1` is common; so
    is `if IsDead == 1` on a function Papyrus declares as returning Bool.
    Emitting either literally gives `Bool == Int`, which does not compile.

    `COMPARISON_COMMANDS` answers for a command that CONVERTS into a
    comparison (`GetInCell X` is one Call node emitting `GetParentCell() == X`)
    Every arm reads the NODE and the row tables, never emitted output: the
    command's own name is what the tables are keyed by, and what its row
    renders is known at import.
    """
    if isinstance(node, N.BinOp):
        return node.op in L.BOOL_OPS
    name_low = node.name.lower() if isinstance(
        node, (N.Call, N.Ident, N.Member)) else ''
    if name_low in COMPARISON_COMMANDS:
        return True
    # Otherwise the SOURCE name decides.  The table is keyed by TES4 spelling,
    # so it must be asked with the node's name -- never with the emitted text,
    # whose names are Papyrus (`GetParentCell`, not `getincell`).
    # `Member` too: `Player.IsSwimming` is a zero-argument bool read written as
    # a member access, and its NAME is what the table knows.
    name = node.name if name_low else ''
    # A command with NO Papyrus equivalent converts to the bare literal `0`.
    # Whether the comparison then collapses depends on WHICH bool list names
    # it -- the two disagree (docs/commentary/script_convert.md #6), and this is
    # the position where that shows: `GetIsCurrentPackage == 0` collapses to
    # `!(0)` (it is a comparison-position name) while `HasFlames == 0` stays
    # `0 == 0` (bare-read name only).  Both are TRUE, so this preserves a
    # spelling difference, not a behaviour one -- but preserving it is the
    # contract until the lists are merged.
    if name_low in INERT_COMMANDS or name_low in _resolve.BARE_INERT:
        return bool(name) and conv.compares_bool(name)
    if name and conv.returns_bool(name):
        return True
    # Last: a TES4 command in no source table can still CONVERT into a bool
    # call (`GetDisabled` -> TES4Polyfill.GetDisabled).  Answered from the
    # row's TEMPLATE, derived at import -- this was the one place left where an
    # emitter inspected its own output to decide something.
    return name_low in BOOL_TEMPLATE_COMMANDS


def emit(conv, node: N.Expr, extends: str) -> str:
    """Papyrus text for one TES4 expression node."""
    if isinstance(node, N.Literal):
        # A quoted STRING may be a TES4 EditorID: the game let a form name be
        # quoted anywhere a form was wanted (`player.additem "MGWellKey" 1`,
        # and Nehrim's `"1TrapFireMineWorldRef".GetDisabled`).  Emitting the
        # quotes gives Papyrus a String where a Form is declared -- a hard
        # compile error.  The converter owns the record lookup, so ask it.
        return conv.emit_string(node.text, extends) if node.is_string \
            else _number(node.text)

    if isinstance(node, N.Ident):
        return conv.emit_name(node.name, extends)

    if isinstance(node, N.Member):
        return conv.emit_member(emit_bare(conv, node.owner), node.name, extends)

    if isinstance(node, N.Unary):
        inner = emit(conv, node.operand, extends)
        # OBSE `$x` is a string cast, not an operator Papyrus knows.
        if node.op == '$':
            return f'({inner} as String)'
        # `- (a + b)` needs its parens; `-x` does not.
        if isinstance(node.operand, N.BinOp) and node.operand.op in _ARITH:
            inner = f'({inner})'
        return f'{node.op}{inner}'

    if isinstance(node, N.Call):
        # OBSE `eval <expr>` only forces evaluation order; the value is the
        # inner expression.  The parser cannot know that (`eval` looks like any
        # other command), so unwrap here: `eval Call Foo a` is `Call Foo a`.
        if node.name.lower() == 'eval' and node.args:
            return emit(conv, _as_call(node.args), extends)
        return conv.emit_call(node, extends)

    if isinstance(node, N.Index):
        # OBSE arrays have no Papyrus equivalent.  Emit the BASE variable and
        # drop the subscript, which is what the string path does -- returning a
        # `0` marker instead is more honest but does not compile where the
        # comparand is a typed form (`0 == <Spell>`, 2 scripts in Morroblivion).
        return conv.emit_array_read(emit_bare(conv, node.target), extends)

    if isinstance(node, N.BinOp):
        return _binop(conv, node, extends)

    if isinstance(node, N.Raw):
        return node.text

    return ''


def _number(text: str) -> str:
    """`.5` is legal TES4 and illegal Papyrus; everything else passes through."""
    return f'0{text}' if text.startswith('.') else text


#: TES4 reads the running package with these; Papyrus has
#: `Actor.GetCurrentPackage()`, so the comparison converts -- but only as a
#: WHOLE, because a numeric comparand is a package TYPE code that has to be
#: expanded into one equality per package of that type.
_PACKAGE_READS = frozenset({'getcurrentaipackage', 'getcurrentpackage'})


def _package_comparison(conv, node: N.BinOp, extends: str):
    """`GetCurrentAIPackage == <PACK|type>` -> Papyrus, or None.

    Skyrim exposes no way to read a Package's TYPE from Papyrus, but the set
    of packages an actor can be running is fixed at conversion time by its own
    AIPackage list -- so `!= 5` becomes "is none of this actor's Wander
    packages".  Emitting the number instead produced the constant `If 0 != 5`
    and the whole test, plus its Package properties, vanished.
    """
    if node.op not in ('==', '!='):
        return None
    lhs = node.left
    if isinstance(lhs, N.Member) and lhs.name.lower() in _PACKAGE_READS:
        recv = emit_bare(conv, lhs.owner)
    elif isinstance(lhs, N.Ident) and lhs.name.lower() in _PACKAGE_READS:
        recv = None
    elif isinstance(lhs, N.Call) and lhs.name.lower() in _PACKAGE_READS \
            and not lhs.args:
        recv = emit_bare(conv, lhs.receiver) if lhs.receiver else None
    else:
        return None
    return conv.emit_package_test(recv, node.op, emit_source(node.right),
                                  extends)


def _form_typed(conv, node: N.Expr) -> bool:
    """Is this bare name a FORM being compared as though it were a number?

    A script's own variable shadows any same-named form: DABoethia declares
    `short Salutation` while a Topic named Salutation also exists, and
    `Salutation == 1` is an ordinary Int test on the variable.  A COMMAND read
    bare (`GetDead == 0`, `GameHour < 6`) is not a form either --
    `get_quest_script_type` answers 'Quest' for any name it cannot resolve, so
    without that guard every such read looked like a form-vs-number pun and
    the comparison was replaced with a constant.
    """
    if not isinstance(node, N.Ident):
        return False
    low = node.name.lower()
    if conv.sc.var_types.get(low) or low in conv.sc.local_vars:
        return False
    if conv._is_known_command(node.name):
        return False
    t = conv.type_of(node.name, locals_first=False)
    if not t:
        # The property may not be registered yet -- emitting this very operand
        # is what registers it -- but the RECORD is known before any emission.
        # Ask what the record is, never `get_quest_script_type`, which answers
        # the generic 'Quest' for anything it cannot resolve: that made every
        # global read (`GameHour < 19`) look like a form-vs-number pun and
        # replaced the comparison with a constant.
        t = conv._base_record_type(node.name)
    return t in MISMATCH_TYPES or t.startswith('TES4_')


def _is_number(node: N.Expr) -> bool:
    return isinstance(node, N.Literal) and not node.is_string


def _mismatch(conv, a, b, node, extends):
    """A form compared directly against a number.

    The truth of the test is unknowable -- the TES4 condition function behind
    it has no Papyrus equivalent -- so it resolves to the value that does NOT
    fire the branch.  This reproduces what the TES4 runtime did with the
    form-vs-number pun, so the note is `;NE:`, not `;TODO:`.
    """
    if not (_is_number(b) and _form_typed(conv, a)):
        return None
    conv._line_comments.append(f';NE: Type mismatch fix ({emit_source(node)})')
    return 'True' if node.op == '!=' else 'False'


def _base_object(conv, ref, base, node, extends):
    """`someRef == SomeMiscObject` -> `someRef.GetBaseObject() == ...`.

    `_base_record_type` reads the RECORD, so it answers before the property
    that will carry it has been registered.  Matched on the BASE operand's
    type -- the old text pass matched the reference by NAME (any identifier
    ending in "Ref"), which missed refs named otherwise and fired on any
    variable whose name happened to end that way.

    GetBaseObject() is declared on ObjectReference, so an OBSE user-function
    parameter is excluded: a `ref` parameter is emitted as `Form` when nothing
    narrows it, and its local type still reads `ObjectReference` here because
    the signature is decided after the body.
    """
    if not isinstance(base, N.Ident):
        return None
    btype = (conv.type_of(base.name, locals_first=False)
             or conv._base_record_type(base.name))
    if btype not in BASE_FORM_TYPES or not is_ref_typed(conv, ref):
        return None
    if isinstance(ref, N.Ident) and ref.name.lower() in conv.sc.udf_params:
        return None
    return (f'{emit(conv, ref, extends)}.GetBaseObject()',
            emit(conv, base, extends))


def _self_cast(conv, a, b, node, extends):
    """`Self == <Actor-typed thing>` inside an ObjectReference script.

    `Self` is the script's own type, and Papyrus refuses to compare it with an
    Actor.  TES4 had one reference type and compared them freely; the object
    behind Self really is the reference being tested, so cast it rather than
    dropping the comparison, which would change which branch runs.
    """
    if not (isinstance(a, N.Ident) and a.name.lower() in ('self', 'this')):
        return None
    base = b.owner if isinstance(b, N.Member) else b
    if not isinstance(base, N.Ident):
        return None
    otype = conv.type_of(base.name, locals_first=False)
    if otype != 'Actor' and not (isinstance(b, N.Member)
                                 and otype.startswith('TES4_')):
        return None
    return '(Self as Actor)', emit(conv, b, extends)


def _bool_as_int(conv, a, b, node, extends):
    """`X.IsDead() > 0` -> `(X.IsDead() as Int) > 0`.

    TES4's GetDetected/GetDead return 0/1, so scripts order them against a
    number; Papyrus refuses to relatively compare a Bool.
    """
    if not (_is_number(b) and isinstance(a, N.Call)
            and a.name.lower() in PAPYRUS_BOOL_FUNCTIONS):
        return None
    return f'({emit(conv, a, extends)} as Int)', emit(conv, b, extends)


def _container(conv, a, b, node, extends):
    """`GetContainer == 0` -> "am I lying in the world?".

    TES4Polyfill.IsInContainer answers that exactly.  Any other comparison
    (`GetContainer != SomeRef` -- "is a PARTICULAR actor holding me") has no
    Papyrus equivalent, so it is neutralised to the value that does not fire
    the branch and left as a TODO rather than compiled into a lie.
    """
    if not _names_get_container(a):
        return None
    if _is_zero(b):
        call = 'TES4Polyfill.IsInContainer(Self)'
        return f'!{call}' if node.op == '==' else call
    conv._line_comments.append(
        f';TODO: GetContainer has no Papyrus equivalent ({emit_source(node)})')
    return 'False' if node.op == '!=' else 'True'


def _names_get_container(node: N.Expr) -> bool:
    """A bare `GetContainer`, with or without an empty argument list."""
    if isinstance(node, N.Ident):
        return node.name.lower() == 'getcontainer'
    if isinstance(node, N.Call):
        return node.name.lower() == 'getcontainer' and not node.args
    return False


#: Comparison rules, each `(ops, fn)`.  `fn(conv, a, b, node, extends)` is
#: tried with the operands both ways round and returns either a whole
#: replacement expression (a string) or the rewritten `(a, b)` pair -- the
#: driver restores the operand order it was called with, so a rule never has
#: to track which way round it saw them.
_CMP_RULES = (
    (('==', '!=', '>=', '<=', '>', '<'), _mismatch),
    (('==', '!='), _base_object),
    (('==', '!='), _self_cast),
    (('>', '>=', '<', '<='), _bool_as_int),
    (('==', '!='), _container),
)


def _apply_cmp_rules(conv, node: N.BinOp, extends: str):
    """First rule that rewrites this comparison, or None."""
    for ops, fn in _CMP_RULES:
        if node.op not in ops:
            continue
        for a, b, swapped in ((node.left, node.right, False),
                              (node.right, node.left, True)):
            got = fn(conv, a, b, node, extends)
            if got is None:
                continue
            if isinstance(got, str):
                return got
            x, y = got
            return f'{y} {node.op} {x}' if swapped else f'{x} {node.op} {y}'
    return None


def _binop(conv, node: N.BinOp, extends: str) -> str:
    op = OP_MAP.get(node.op, node.op)
    left, right = node.left, node.right

    packaged = _package_comparison(conv, node, extends)
    if packaged is not None:
        return packaged

    fixed = _apply_cmp_rules(conv, node, extends)
    if fixed is not None:
        return fixed

    # `GetDistance Player <= Player 500` -- Oblivion's parser reads the
    # comparand as the trailing NUMBER and ignores the leading name, so the
    # reference token is redundant.  The tree sees it as a Call (`Player 500`),
    # which emitted `Player(500)` and failed to compile.  Only a bare name
    # applied to a single numeric literal; anything else is a real call.
    if (isinstance(right, N.Call) and len(right.args) == 1
            and right.receiver is None
            and isinstance(right.args[0], N.Literal)
            and not right.args[0].is_string
            and not conv._is_known_command(right.name)):
        right = right.args[0]

    if op in ('==', '!='):
        # TES4's null test.  `ref == 0` must become `ref == None`, in either
        # operand order, or the comparison does not compile.
        if _is_zero(right) and is_ref_typed(conv, left):
            return f'{_operand(conv, left, node, extends)} {op} None'
        if _is_zero(left) and is_ref_typed(conv, right):
            return f'None {op} {_operand(conv, right, node, extends, right=True)}'
        # `<bool> == 1` is TES4's way of spelling `<bool>`, and `== 0` its
        # negation.  Papyrus rejects Bool-vs-Int outright.
        if isinstance(right, N.Literal) and right.text.strip() in ('0', '1'):
            # In a `== 0/1` comparison TES4 lets a zero-argument reference
            # function name its SUBJECT as an argument: `GetDead KimRef == 1`
            # means `KimRef.IsDead()`, not `Self.IsDead(KimRef)`.  Only here --
            # a bare call keeps its arguments where they are.
            value = (conv.emit_call(left, extends, promote_subject=True)
                     if isinstance(left, N.Call)
                     else emit(conv, left, extends))
            is_bool = _is_bool_valued(conv, left)
            inner = str(value)
            if not is_bool:
                return f'{inner} {op} {emit(conv, right, extends)}'
            truthy = (op == '==') == (right.text.strip() == '1')
            if truthy:
                return inner
            # Parenthesise: `!(x as Actor).IsDead()` would negate the CAST,
            # not the call.  Skip it only when the text is ALREADY one enclosing
            # pair, so a converted comparison does not come out `!((a == b))`.
            if inner.startswith('(') and L.unwrap_parens(inner) == inner[1:-1].strip():
                return f'!{inner}'
            return f'!({inner})'

    # TES4 refs coerce to 0 when unset, so scripts test them with ORDERING
    # operators: `ref > 0` / `ref >= 1` means "is set", `ref <= 0` means "is
    # not set".  MQ04's Cloud Ruler conversation driver, MQ09's ghost-blade
    # release and CGEmperorScript's call for help all use the idiom; censused,
    # all 8 sites in Oblivion.esm are null checks, none a form-name collision.
    # Both directions are required -- emitting `ref <= 0` literally does not
    # compile (`ObjectReference` has no ordering), which is what kept
    # TrapHungerStatue05SCRIPT building.
    if isinstance(right, N.Literal) and not right.is_string \
            and right.text.strip() in ('0', '1') \
            and ((op in ('>', '>=')) or (op == '<=' and _is_zero(right))) \
            and is_ref_typed(conv, left):
        null_op = '==' if op == '<=' else '!='
        return f'{_operand(conv, left, node, extends)} {null_op} None'

    if op in ('&&', '||'):
        return _logical(conv, left, right, op, node, extends)
    return (f'{_operand(conv, left, node, extends)} {op} '
            f'{_operand(conv, right, node, extends, right=True)}')


def _logical(conv, left, right, op: str, node, extends: str) -> str:
    """`&&`/`||`, keeping the terms that convert and trailing those that do not.

    A term with no Papyrus equivalent renders as a `;` note, which comments out
    the REST OF THE LINE -- every later operand is lost and the `If` is left
    dangling.  TES4 conditions are long chains (`KimMaleScript` ANDs four
    terms, one of them `GetFriendHit`, a Skyrim CONDITION function with no
    Papyrus native), so the note is lifted out of the operand and re-attached
    after the whole expression, where it comments out nothing.

    The surviving terms keep their meaning: dropping a term from an `&&` widens
    the guard and from an `||` narrows it.  Neutralising the condition to
    `True` instead would run the guarded body unconditionally -- for that
    script, healing the player with no distance, stage or combat check at all.
    """
    a, a_note = _split_note(_operand(conv, left, node, extends))
    b, b_note = _split_note(_operand(conv, right, node, extends, right=True))
    notes = [n for n in (a_note, b_note) if n]
    if a and b:
        return f'{a} {op} {b}  ;{" ".join(notes)}' if notes else f'{a} {op} {b}'
    kept = a or b or 'True'
    return f'{kept}  ;{op} {" ".join(notes)}'


def _split_note(text: str) -> tuple:
    """`(expression, note)` -- the value part of an operand and its `;` note."""
    head, sep, tail = text.partition(';')
    if not sep:
        return text.strip(), ''
    return head.strip(), tail.strip()



#: Operators where `a op (b op c)` differs from `(a op b) op c`, so an
#: equal-ranked RIGHT child still needs its parentheses.
_NON_ASSOCIATIVE = frozenset({'-', '/', '%'})


def _operand(conv, child: N.Expr, parent: N.BinOp, extends: str,
             *, right: bool = False) -> str:
    """Emit `child`, parenthesised only when re-reading would regroup it.

    The tree already encodes precedence, so parens here exist purely so the
    flat text parses back to the same shape.  A LEFT child of an equal-ranked
    operator never needs them (`a - b - c` is already left-associative);
    wrapping it turned `(1.5 - GrowTimer) - 13/6` into
    `1.5 - (GrowTimer - 13)/6`, which is a different number.
    """
    text = emit(conv, child, extends)
    if parent.op in _ARITH and _is_bool_valued(conv, child):
        # TES4 freely used command booleans as 0/1 arithmetic operands.
        # Papyrus requires the cast on that operand; casting the completed
        # expression changes precedence and leaves the other operand invalid.
        text = f'({text} as Int)'
    if not isinstance(child, N.BinOp):
        return text
    if _rank(child.op) < _rank(parent.op):
        return f'({text})'
    if right and _rank(child.op) == _rank(parent.op) \
            and parent.op in _NON_ASSOCIATIVE:
        return f'({text})'
    return text


def _rank(op: str) -> int:
    """Binding tier of `op`; unknown operators bind tightest (atoms)."""
    return L.RANK.get(op, L.RANK_ATOM)


def _as_call(args) -> N.Expr:
    """Re-form `eval`'s flattened arguments into the call they spell.

    `eval Call Foo a b` parses as four arguments; the value is `Call Foo a b`,
    i.e. the first argument is the command and the rest are its own.
    """
    head, rest = args[0], args[1:]
    if isinstance(head, N.Ident):
        return N.Call(head.name, tuple(rest), None, line=head.line)
    return head
