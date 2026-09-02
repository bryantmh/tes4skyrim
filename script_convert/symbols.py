"""Symbol tables for script conversion: what a name IS, resolved once.

The text pipeline answers "what type is this name?" by pattern-matching
emitted Papyrus, twice over, AFTER every script has been written:

  * `pipeline._fix_udf_call_arg_types` re-reads the whole output tree to find
    `Function TES4Call(...)` headers, because "signatures are only known once
    every script has been converted";
  * `pipeline._comment_undeclared_identifiers` re-reads it again to find which
    names a script declared, so it can comment out the ones it did not.

Both are rebuilding a symbol table by grepping generated files.  This module
holds the same facts directly:

  * `ScriptSymbols` -- one script's declarations, inferred property types and
    (if it is an OBSE user function) its parameter signature;

It also carries the six values the pipeline currently injects as MUTABLE
CLASS ATTRIBUTES on ScriptConverter (`say_durations`, `say_topics`,
`topic_unlock_globals`, `message_menus`, `chargen_menus`, `music_cues`) --
a hidden global channel that appears in no function signature and forces a
unit test to do global setup before it can convert a single line.

Nothing here converts anything; it only answers questions about names.
"""

from __future__ import annotations

from script_convert.tes4 import nodes as N

from dataclasses import dataclass, field

from script_convert.constants import (
    AXIS_COMMANDS, NUMERIC_RANK,
    _ACTOR_ARG_FUNCTIONS,
    _ACTOR_ONLY_FUNCTIONS,
    _OBJREF_SHARED_FUNCTIONS,
    _BASE_OBJECT_PAPYRUS,
    _FORM_RETURNING,
    ACTOR,
    AV,
    COMMAND_ROWS,
    MAP,
    KNOWN_GLOBALS,
    param_types,
    RETURN_TYPES,
)

# Names a generated script may use without declaring them: Papyrus globals,
# script-scope keywords, and the event parameters fragments are handed.
# `pipeline._comment_dangling` imports this one; it does not keep a copy.
IMPLICIT_NAMES = frozenset({
    'game', 'debug', 'utility', 'self', 'parent', 'math', 'input',
    # `weather` is the CLASS in `Weather.ReleaseOverride()`, not a variable.
    'weather',
    'akspeakerref', 'akactionref', 'aktarget', 'akcaster', 'akaggressor',
    'akkiller', 'akactor', 'akitem', 'aksource', 'akrefself',
    'tes4polyfill', 'form', 'true', 'false', 'none',
})


#: Numeric types, widest first.  Arithmetic over a mixed expression takes the
#: widest operand's type, exactly as Papyrus does.


def type_of_expr(node, lookup) -> str:
    """Papyrus type of an expression NODE, or '' when nothing says.

    Reading the TREE beats the text scan it replaced twice over: a name inside
    a string literal cannot be mistaken for a variable, and arithmetic is typed
    by its OPERANDS.  `/` included -- Papyrus types `Int / Int` as Int exactly
    as TES4 did, and always-Float changed every integer ratio in the corpus.

    `lookup(name) -> str` supplies a bare name's type; the converter passes its
    own `type_of`, so locals, properties and remote members all resolve there.
    """

    if node is None:
        return ''
    if isinstance(node, N.Literal):
        if node.is_string:
            return 'String'
        return 'Float' if '.' in node.text else 'Int'
    if isinstance(node, N.Ident):
        # A bare name is a variable if one is declared, and a zero-argument
        # command otherwise -- the same order the emitter resolves them in.
        declared = lookup(node.name)
        # A GlobalVariable read emits `X.GetValue()`, which Papyrus types
        # Float even for a global holding whole numbers.  TES4 wrote the same
        # read as a bare name (`set d to GameDaysPassed + 2`), so without this
        # the value looks Int and the assignment to a `short` loses its cast.
        if declared == 'GlobalVariable' or node.name.lower() in KNOWN_GLOBALS:
            return 'Float'
        return declared or RETURN_TYPES.get(node.name.lower(), '')
    if isinstance(node, N.Member):
        return lookup(f'{_bare(node.owner)}.{node.name}') \
            or RETURN_TYPES.get(node.name.lower(), '')
    if isinstance(node, N.Call):
        low = node.name.lower()
        hit = RETURN_TYPES.get(low) or RETURN_TYPES.get(_papyrus_name(low))
        if hit:
            return hit
        # TES4 spells the axis as an ARGUMENT (`GetAngle Z`) where Papyrus
        # spells it in the NAME (`GetAngleZ`), so the table keyed by Papyrus
        # name misses it unless the first argument is folded in.
        if low in AXIS_COMMANDS and node.args:
            axis = _bare(node.args[0]).strip().lower()
            if axis in ('x', 'y', 'z'):
                return RETURN_TYPES.get(AXIS_COMMANDS[low] + axis, '')
        # `GetGlobalValue <g>` and any read of a GlobalVariable reach Papyrus
        # as `<g>.GetValue()`, which is Float.
        if low in ('getglobalvalue', 'getglobal'):
            return 'Float'
        return ''
    if isinstance(node, N.Unary):
        return type_of_expr(node.operand, lookup)
    if isinstance(node, N.BinOp):
        if node.op in ('==', '!=', '>', '<', '>=', '<=', '&&', '||', '<>'):
            return 'Bool'
        left = type_of_expr(node.left, lookup)
        right = type_of_expr(node.right, lookup)
        for rank in NUMERIC_RANK:
            if rank in (left, right):
                return rank
        return left or right
    return ''


@dataclass
class VarUsage:
    """How one TES4 `ref` variable is actually USED, read off the parse tree.

    TES4 had a single `ref` type for references, base forms and (routinely)
    plain integer flags, so the declared type says nothing and the SCRIPT'S
    OWN USE has to decide.  The text pipeline answered this by grepping its
    own emitted Papyrus for `var.method(`, `var == None` and `(var` -- which
    cannot tell a real use from the same letters inside a string literal, and
    which had to run AFTER emission, when the declaration it wants to change
    is already written.

    Reading the tree answers it before a line exists, and answers it exactly.
    """

    #: Assigned an integer literal (`set myRef to 0`) -- the flag idiom.
    int_assign: bool = False
    #: Numeric result types assigned to the slot.  TES4's `ref` declaration
    #: did not stop scripts from using the slot for GetPos/GetLevel results;
    #: Papyrus requires the declaration to match the actual value type.
    numeric_types: set[str] = field(default_factory=set)
    #: Assigned anything reference-shaped, including another ref variable.
    ref_assign: bool = False
    #: Used AS a reference: a method receiver, or compared against one.
    ref_usage: bool = False
    #: Compared with another slot proven to hold a reference.  This is
    #: stronger than merely appearing as an untyped command argument.
    ref_compare: bool = False
    #: Called through with an ACTOR-ONLY command (`x.EvaluatePackage`), so the
    #: variable must be declared Actor -- an ObjectReference does not carry
    #: those methods and the assignment into it needs an `as Actor` cast that
    #: only a declared Actor triggers.
    actor_usage: bool = False
    #: Narrowest Papyrus type assigned from a typed call, when there is one
    #: (`set r to GetBaseObject` makes it a Form, never an ObjectReference).
    form_type: str = ''
    #: Every value assigned to this variable, as parse nodes.  A caller that
    #: needs cross-ref data the symbol table does not hold -- what RECORD a
    #: name refers to, so a `ref` only ever holding SPEL records is declared
    #: `Spell` -- resolves these itself rather than re-reading the output.
    assigned: list = field(default_factory=list)

    @property
    def is_int_flag(self) -> bool:
        """Is this `ref` really an Int the script never uses as a reference?"""
        return self.int_assign and not self.ref_assign and not self.ref_usage


def scan_var_usage(stmts, names, lookup):
    """`{lowercase name: VarUsage}` for every name in `names`, over `stmts`.

    One walk answers for ALL the variables at once; the pass this replaces
    re-scanned the whole emitted body once per candidate variable.  Traversal
    is `nodes.walk_expr`, not a local copy: four private copies of the
    child-field tuple once drifted apart.
    """

    usage = {n.lower(): VarUsage() for n in names}
    ref_comparisons = []

    def note_ref_usage(node):
        """Mark every name this expression uses AS a reference."""
        if isinstance(node, N.Member):
            # `x.foo` -- x is a receiver, so it is a reference.
            owner = _bare(node.owner).lower()
            if owner in usage:
                usage[owner].ref_usage = True
                if _needs_actor(node.name):
                    usage[owner].actor_usage = True
        elif isinstance(node, N.Call):
            if node.receiver is not None:
                recv = _bare(node.receiver).lower()
                if recv in usage:
                    usage[recv].ref_usage = True
                    if _needs_actor(node.name):
                        usage[recv].actor_usage = True
            # A bare name passed as an ARGUMENT is being used as a form --
            # and as an ACTOR when the command takes one there (TES4Polyfill
            # .GetIsCreature, StartCombat, IsDetectedBy...).
            call_name = node.name.lower()
            actor_arg = call_name in _ACTOR_ARG_FUNCTIONS
            row = COMMAND_ROWS.get(call_name)
            for index, a in enumerate(node.args):
                if isinstance(a, N.Ident) and a.name.lower() in usage:
                    arg_usage = usage[a.name.lower()]
                    expected = row.types.get(index) if row is not None else ''
                    if not expected:
                        expected = param_types(call_name).get(index, '')
                    if call_name == 'cast' and index == 0:
                        expected = 'Spell'
                    if actor_arg and index == 0:
                        expected = 'Actor'
                    # An argument is reference evidence only when the
                    # command's authored signature says it is. SetPos/GetPos
                    # and many other calls take numeric locals; treating every
                    # bare argument as a form kept TES4 `ref` scratch slots
                    # ObjectReference even when they only held Float results.
                    if expected and expected not in ('Int', 'Float', 'Bool',
                                                     'String'):
                        arg_usage.ref_usage = True
                    if expected == 'Actor':
                        arg_usage.actor_usage = True
                    # Command signatures are authored type evidence too.  A
                    # ref passed as a Spell/Armor/etc. argument must take that
                    # exact Papyrus base type before body emission; otherwise
                    # a later command handler changes only the property table
                    # after an earlier assignment has already been written.
                    if (expected and expected not in
                            ('Int', 'Float', 'Bool', 'String',
                             'Form', 'ObjectReference', 'ActorBase')):
                        arg_usage.form_type = expected
        elif isinstance(node, N.BinOp):
            # `x == None` / `x != None` tests a reference.
            if node.op in ('==', '!='):
                if isinstance(node.left, N.Ident) and isinstance(node.right, N.Ident):
                    left = node.left.name.lower()
                    right = node.right.name.lower()
                    if left in usage and right in usage:
                        ref_comparisons.append((left, right))
                for side, other in ((node.left, node.right),
                                    (node.right, node.left)):
                    if (isinstance(side, N.Ident)
                            and side.name.lower() in usage
                            and isinstance(other, N.Ident)
                            and other.name.lower() in ('none', 'player')):
                        usage[side.name.lower()].ref_usage = True

    def note_all(node):
        """Note every reference use in this expression and its children."""
        for sub in N.walk_expr(node):
            note_ref_usage(sub)

    def walk(body):
        for st in body:
            if isinstance(st, N.Assign):
                tgt = _bare(st.target).lower()
                u = usage.get(tgt)
                if u is not None:
                    _classify_assignment(u, st.value, lookup)
                # The TARGET is not a use, but everything in the VALUE is.
                note_all(st.value)
            else:
                for attr in ('expr', 'cond', 'value'):
                    note_all(getattr(st, attr, None))
            for attr in ('body', 'orelse'):
                walk(getattr(st, attr, None) or [])
            for entry in getattr(st, 'elifs', None) or []:
                note_all(entry[0])
                walk(entry[1])

    walk(stmts)
    # A TES4 ref slot cleared with 0 is still a reference when it is compared
    # with another slot proven to hold one.  Keep purely numeric ref flags as
    # Int; propagate only from an endpoint with actual reference assignment
    # evidence, after the complete body has been scanned.
    for left, right in ref_comparisons:
        if usage[left].ref_assign or usage[right].ref_assign:
            usage[left].ref_usage = True
            usage[right].ref_usage = True
            usage[left].ref_compare = True
            usage[right].ref_compare = True
    return usage


def _names_a_typed_call(value) -> bool:
    """Is this value a CALL to one of the narrow-return commands?

    TES4 writes a zero-argument command as a bare word, so `set r to
    GetBaseObject` parses as an `Ident` and `ref.GetBaseObject` as a `Member`
    -- neither is a `Call` node, though all three are calls.  The test is
    whether the NAME is one, not what the parser had to shape it as.
    """
    if not isinstance(value, (N.Call, N.Member, N.Ident)):
        return False
    low = value.name.lower()
    # Resolve the alias too: `_FORM_RETURNING` is keyed by the PAPYRUS name,
    # and TES4 reaches it under others -- `GetEquippedObject` is Skyrim's
    # `GetEquippedWeapon`, so testing the authored spelling alone missed it
    # and the variable stayed ObjectReference.
    return low in _FORM_RETURNING or _papyrus_name(low) in _FORM_RETURNING


def _classify_assignment(usage, value, lookup):
    """Record what one assignment says about the variable's real type."""

    usage.assigned.append(value)
    if isinstance(value, N.Literal) and not value.is_string:
        usage.int_assign = True
        usage.numeric_types.add('Float' if '.' in value.text else 'Int')
        return
    # `x = y + 1` on a numeric operand is still the flag idiom.
    if isinstance(value, N.BinOp) and value.op in ('+', '-', '*', '/', '%'):
        numeric = type_of_expr(value, lookup)
        if numeric in ('Int', 'Float'):
            usage.int_assign = True
            usage.numeric_types.add(numeric)
            return
    vtype = type_of_expr(value, lookup)
    if vtype in ('Int', 'Float', 'Bool'):
        usage.numeric_types.add(vtype)
        return
    # A CALL with a narrow return type names the exact type the variable must
    # be declared as.  A bare NAME does not: it is a base record, and the
    # variable is assigned real references elsewhere, so it widens to Form.
    # Ordering these by NODE KIND rather than by the type they produce
    # matters -- `Armor` is both a narrow return type and the type of an ARMO
    # property, and declaring the variable `Armor` breaks every other
    # assignment to it.
    if _names_a_typed_call(value) and vtype in _FORM_RETURNING.values():
        usage.form_type = vtype
    elif isinstance(value, N.Ident) and vtype:
        # Assignment from a BARE property, whose type is whatever the record
        # it names is: `let rCrosshairsLast := JailShoes` stores an ARMO BASE
        # record in a TES4 `ref`.  Papyrus will not put an Armor in an
        # ObjectReference and no cast is right either (a base form is not a
        # reference), so the VARIABLE widens to Form -- which legally holds
        # both that and the real references assigned elsewhere.
        #
        # Only a type Papyrus REFUSES to store in an ObjectReference forces
        # the widening; a ref-shaped or script-typed value is already fine.
        if (vtype not in ('ObjectReference', 'Actor', 'Form')
                and not vtype.startswith('TES4_')
                and vtype not in ('Int', 'Float', 'Bool', 'String')):
            usage.form_type = 'Form'
    usage.ref_assign = True


def _papyrus_name(tes4_name: str) -> str:
    """The Papyrus name a TES4 command converts to, lowercased.

    `RETURN_TYPES` is keyed by the PAPYRUS name so that TES4's aliases cost
    nothing: `getav`, `getactorvalue` and `getbaseav` all resolve through the
    one row that already lists them, instead of every alias being repeated
    here to be kept in sync by hand.
    """
    row = COMMAND_ROWS.get(tes4_name)
    name = row.emit if row is not None and row.subj == MAP else None
    if not name:
        return ''
    # A mapped name may be QUALIFIED (`Utility.RandomFloat`, `Game.GetPlayer`).
    # RETURN_TYPES is keyed by the bare method, since that is what a receiver
    # call reaches it as, so the class prefix comes off: without this,
    # `rand 1 5` -> `Utility.RandomFloat(1, 5)` looked untyped and lost the
    # `as Int` on assignment to a TES4 `short`.
    return name.lower().rsplit('.', 1)[-1]


def _bare(node) -> str:
    """Source spelling of a name-like node, for a dotted lookup."""
    if isinstance(node, N.Ident):
        return node.name
    if isinstance(node, N.Member):
        return f'{_bare(node.owner)}.{node.name}'
    return ''


def resolve_ref_types(stmts, ref_vars, lookup, record_type_of):
    """Final Papyrus type for every TES4 `ref` variable, decided BEFORE emission.

    TES4 has one `ref` type covering placed references, base records and plain
    integer flags, so the declaration says nothing and only the script's own
    USE decides.  The text pipeline answered this AFTER writing the file --
    three separate passes re-reading the emitted Papyrus to patch declaration
    lines it had already written -- because the emitter reached the body only
    after the declarations were out.

    Reading the tree answers it up front, so each declaration is written once,
    correctly, and nothing downstream has to be repaired.

    `record_type_of(name) -> str` gives the Papyrus value type of the RECORD a
    bare EditorID refers to, including placed REFR/ACHR/ACRE records, or '' --
    the cross-reference question the symbol table cannot answer on its own.
    Returns `{lowercase name: Papyrus type}`,
    holding only the variables whose type actually changes.
    """

    out = {}
    for low, use in scan_var_usage(stmts, ref_vars, lookup).items():
        # A TES4 `ref` may be used purely as a numeric slot (`ref z; set z to
        # GetPos Z`).  This is distinct from the integer-flag idiom and must
        # retain Float results instead of becoming ObjectReference.
        if (use.numeric_types and not use.ref_assign and not use.ref_usage
                and not use.ref_compare and not use.actor_usage):
            out[low] = next(t for t in NUMERIC_RANK
                            if t in use.numeric_types)
            continue
        # A `ref` only ever assigned integers and never used as a reference is
        # the TES4 flag idiom, not a reference at all.
        if use.is_int_flag:
            out[low] = 'Int'
            continue
        # A narrow-returning CALL decides it outright: Papyrus refuses the
        # implicit conversion in BOTH directions, so a `ref` assigned from
        # GetBaseObject (a Form) must be DECLARED Form.
        #
        # `Form` alone is the WIDENING fallback, not such a decision -- it is
        # what `_classify_assignment` records for a bare record name whose own
        # type it cannot narrow.  The records below are more specific, so they
        # get the chance first (stockFX holds only EFSH, and as a bare Form
        # its `.Play(...)` does not compile).
        if use.form_type and use.form_type != 'Form':
            out[low] = use.form_type
            continue
        # Otherwise the RECORDS it is assigned decide, when they agree.  A
        # `ref` holding only SPEL records is a Spell; one holding several
        # different base objects is their common supertype, Form.  `= None`
        # says nothing -- it is assignable to every object type -- so it is
        # not counted (soulGemRef ends with exactly that line).
        # An integer assignment alongside record assignments (TES4's
        # `rCrosshairsLast = 0` clear idiom) means the variable cannot be
        # narrowed to the record's class -- Papyrus takes no Int there.  The
        # old pass got this for free, since the emitted `0` read as a
        # non-record and broke unanimity.
        # Actor-only USE is decisive: the variable is called through with a
        # method only Actor carries, so no other evidence can override it --
        # `set combatant1 to 0` (TES4's clear idiom) sits in the same script
        # as `combatant1.SetActorValue`, and reading the 0 as "this is a flag"
        # left the call undefined.
        if use.actor_usage:
            out[low] = 'Actor'
            continue
        if use.int_assign:
            out[low] = use.form_type or _mixed_record_type(
                use, record_type_of, lookup, ref_vars)
            if not out[low] and use.ref_usage:
                out[low] = 'ObjectReference'
            if not out[low]:
                del out[low]
            continue
        kinds = set()
        for value in use.assigned:
            if not isinstance(value, N.Ident):
                kinds.add('')
                continue
            if value.name.lower() == 'none':
                continue
            # `Player` is emitted `Game.GetPlayer()`, an ACTOR -- not the
            # ActorBase its NPC_ record would suggest.  The old pass matched
            # emitted text and so never saw it as a record at all; naming it
            # here keeps `myTarget = Player` from dragging the variable to
            # ActorBase, which cannot hold an Actor.
            if value.name.lower() in ('player', 'playerref'):
                kinds.add('Actor')
                continue
            # A declared LOCAL wins over a same-named global record.  An
            # external property does not: placed refs with attached scripts
            # are deliberately typed as that script for member access, while
            # a local assigned the ref still holds an ObjectReference.  Using
            # the property type here made generic marker slots Actor/script
            # typed and rejected the authored assignment.
            assigned_type = lookup(value.name)
            if value.name.lower() in ref_vars and assigned_type:
                kinds.add(assigned_type)
            else:
                kinds.add(record_type_of(value.name) or assigned_type)
        kinds.discard('')
        if not kinds:
            if use.form_type:
                out[low] = use.form_type
            elif use.actor_usage:
                out[low] = 'Actor'
            elif use.ref_usage:
                out[low] = 'ObjectReference'
            continue
        # Being used AS a reference does NOT veto this.  A `ref` holding
        # EFSH records is called through (`stockFX.Play(...)`), and only the
        # EffectShader declaration makes that call compile -- the RECORDS it
        # holds are what decide, not how it is reached.
        if len(kinds) == 1:
            only = next(iter(kinds))
            if only not in ('ObjectReference', 'Form'):
                out[low] = only
        elif all(k in _BASE_OBJECT_PAPYRUS for k in kinds):
            out[low] = 'Form'
        elif use.form_type:
            out[low] = use.form_type
    return out


def _mixed_record_type(use, record_type_of, lookup, ref_vars) -> str:
    """`Form` when an int-assigned `ref` ALSO holds base records, else ''.

    TES4's clear idiom (`let r := 0`) sits in the same variable as a real base
    record, and Papyrus accepts neither in the other's declared type: an Armor
    will not go into an ObjectReference, and 0 will not go into an Armor.
    `Form` is the one handle that takes every record, so the mixture widens
    rather than narrowing to the record's own class.
    """
    for value in use.assigned:
        if not isinstance(value, N.Ident):
            continue
        assigned_type = lookup(value.name)
        if value.name.lower() not in ref_vars:
            record_type = record_type_of(value.name)
            if record_type:
                return ('ObjectReference' if record_type in
                        ('Actor', 'ObjectReference') else 'Form')
        if assigned_type in ('Actor', 'ObjectReference') \
                or assigned_type.startswith('TES4_'):
            return 'ObjectReference'
        if record_type_of(value.name) or assigned_type in _BASE_OBJECT_PAPYRUS:
            return 'Form'
    return ''


def _needs_actor(name: str) -> bool:
    """Does calling `name` on a receiver require that receiver to be an Actor?

    Two authorities agree on this and both are consulted: the
    `_ACTOR_ONLY_FUNCTIONS` census, and the command row's own `subj` -- a row
    written `Cmd(..., ACTOR)` or `Cmd(..., AV)` says so by construction, which
    keeps the answer correct for every command added since the census.
    """
    low = name.lower()
    # 14 of _ACTOR_ONLY_FUNCTIONS are ALSO declared on ObjectReference
    # (PlaceAtMe, AddItem...).  Calling one says nothing about the receiver,
    # and typing a spawn marker Actor because it calls PlaceAtMe breaks every
    # write into it -- the same exclusion the emitter applies before casting.
    if low in _OBJREF_SHARED_FUNCTIONS:
        return False
    if low in _ACTOR_ONLY_FUNCTIONS:
        return True
    row = COMMAND_ROWS.get(low)
    if row is None:
        return False
    if row.subj in (ACTOR, AV):
        return True
    # An ALIAS resolves through the name it maps to: `UnequipItemNS` is TES4's
    # no-sound spelling of `UnequipItem` and reaches the same Papyrus method,
    # so the census keyed by the primary spelling has to be consulted under
    # that name too -- otherwise the receiver stays ObjectReference and the
    # call is undefined.
    return row.subj == MAP and row.emit.lower() in _ACTOR_ONLY_FUNCTIONS


def property_declarations(property_refs, declared) -> list:
    """`Type Property Name Auto` lines for a script's external references.

    `declared` is the set of names already taken by variables, updated in
    place so a caller can keep adding to it.

    Two EditorID spellings differing only in case are ONE Papyrus property, so
    they merge -- and the SPECIFIC type wins over the generic `Quest`, which is
    what `_preload_scro_refs` assigns before the converter learns the script
    type.  Declaring the generic one instead made every cross-script variable
    read through it fail to compile.
    """
    from script_convert.constants import _safe_property_name

    merged = {}
    for pname, ptype in sorted(property_refs.items()):
        key = _safe_property_name(pname).lower()
        known = merged.get(key)
        if known is None or (known[1] == 'Quest' and ptype != 'Quest'):
            merged[key] = (pname, ptype)

    out = []
    for pname, ptype in sorted(merged.values(), key=lambda x: x[0].lower()):
        safe = _safe_property_name(pname)
        if safe.lower() in declared:
            continue
        declared.add(safe.lower())
        out.append('%s Property %s Auto' % (ptype, safe))
    return out
