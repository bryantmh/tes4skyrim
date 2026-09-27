"""TES4 script AST.

Deliberately small: these nodes describe Oblivion/OBSE source as WRITTEN, not
as converted.  Anything Skyrim-shaped belongs on the TES5 side, so that the
transform layer is the only place the two languages meet.

Two properties are the whole point of having a tree at all:

  * a block OWNS its body, so `if`/`endif` nesting cannot come out unbalanced
    and does not have to be re-derived from emitted text (`_balance_if_endif`);
  * a comment ATTACHES to the statement it belongs to, so emitting one can
    never comment out the rest of an expression (`_repair_commented_condition`).

Every node keeps `line` (1-based, within the script body) so a diagnostic or a
`;NE:` marker can name the authored line it came from, and `Stmt.comment`
carries the trailing `; ...` text verbatim -- the converter round-trips these
into the Papyrus output, so dropping them would lose authored intent.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Expressions
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Expr:
    line: int = field(default=0, kw_only=True)

    @property
    def called(self) -> str:
        """Lowercased name (Ident/Member/Call), or '' if the node has none."""
        return getattr(self, 'name', '').lower()
    #: The source wrapped this expression in parentheses.  Redundant for
    #: MEANING -- the tree already encodes precedence -- but the converter
    #: echoes the author's parens into the output, so dropping them makes
    #: every such line differ for no reason (649 of 12,098 assignment values
    #: in Oblivion.esm carry them).  Recorded, never interpreted.
    parenthesised: bool = field(default=False, kw_only=True)


@dataclass(frozen=True)
class Literal(Expr):
    """A number or a double-quoted string; `text` is the source spelling.

    The spelling is kept rather than a parsed value because the converter
    emits numbers back out and `1.0` vs `1` is visible in the output.
    """
    text: str
    is_string: bool = False


@dataclass(frozen=True)
class Ident(Expr):
    """A bare name: a variable, a form EditorID, or a zero-argument command.

    Which of the three it is cannot be known without the symbol table, so the
    parser deliberately does not guess -- that is the resolver's job.
    """
    name: str


@dataclass(frozen=True)
class Member(Expr):
    """`owner.name` -- a cross-script variable read or a method receiver."""
    owner: Expr
    name: str


@dataclass(frozen=True)
class Index(Expr):
    """OBSE array subscript, `arr[i]`. Rare (38 lines in Nehrim) but real."""
    target: Expr
    index: Expr


@dataclass(frozen=True)
class Unary(Expr):
    op: str
    operand: Expr


@dataclass(frozen=True)
class BinOp(Expr):
    op: str
    left: Expr
    right: Expr


@dataclass(frozen=True)
class Call(Expr):
    """A command invocation.

    TES4 has no call parentheses: `SayTo BaurusRef, CharGenMain 1` passes
    three arguments separated by whitespace, commas, or both.  `receiver` is
    the `x.` prefix when written (`BaurusRef.getdisposition player`).

    A Call is an Expr because TES4 commands are freely used as values --
    `set CharacterGen.convTimer to SayTo player, CharGenMain 1` assigns the
    line duration Say returns.
    """
    name: str
    args: tuple[Expr, ...] = ()
    receiver: Expr | None = None
    #: The argument list opened with a COMMA (`StopCombat, Player`).  For a
    #: command that takes no arguments the token after that comma is the
    #: RECEIVER, not an argument -- `StopCombat, Player` means Player's
    #: combat, the same as `Player.StopCombat`.  Recorded because the comma is
    #: the only thing that says so, and dropping it acted on the wrong actor.
    leading_comma: bool = False


@dataclass(frozen=True)
class Raw(Expr):
    """Source that did not parse as an expression, kept verbatim.

    The converter must never fail a whole script over one odd line (a bad
    script takes down every script declaring a property of its type), so the
    parser degrades to Raw and lets the transform decide.  A Raw reaching the
    emitter is a bug worth reporting, not a crash.
    """
    text: str


# --------------------------------------------------------------------------
# Statements
# --------------------------------------------------------------------------

@dataclass
class Stmt:
    line: int = field(default=0, kw_only=True)
    # Trailing `; ...` on the same source line, verbatim (including the `;`).
    comment: str = field(default='', kw_only=True)


@dataclass
class Comment(Stmt):
    """A whole-line comment; `text` includes the leading `;`."""
    text: str = ''


@dataclass
class Blank(Stmt):
    """An empty source line. Kept so emitted output can preserve spacing."""


@dataclass
class VarDecl(Stmt):
    """`short foo` / `ref bar` / `float baz`.

    TES4 variables are script-global no matter where they are declared, which
    is why the parser hoists these to Script.variables rather than leaving
    them in the block they appear in.
    """
    vtype: str = ''
    name: str = ''


@dataclass
class Assign(Stmt):
    """`set <target> to <value>` or OBSE `let <target> := <value>`.

    `op` is '' for a plain assignment, or the compound operator ('+', '-',
    '*', '/') for `let x += 1`.
    """
    target: Expr = None
    value: Expr = None
    op: str = ''
    is_let: bool = False


@dataclass
class ExprStmt(Stmt):
    """A bare command statement: `Activate`, `player.additem Gold001 100`."""
    expr: Expr = None


@dataclass
class If(Stmt):
    """`if` with its elseif chain and else, each owning its own body.

    `elifs` is [(condition, body, line)]. Storing the chain flat rather than nesting
    else-if inside else keeps the emitted Papyrus shaped like the source.
    """
    cond: Expr = None
    body: list = field(default_factory=list)
    elifs: list = field(default_factory=list)
    orelse: list = field(default_factory=list)
    # Source spelling of each closer, so emission can round-trip `else <cond>`
    # (TES4 accepts it as an `elseif`) without inventing text.
    else_is_elseif: bool = False


@dataclass
class While(Stmt):
    """OBSE `while <cond> ... loop`."""
    cond: Expr = None
    body: list = field(default_factory=list)


@dataclass
class ForEach(ExprStmt):
    """OBSE `forEach <it> <- <container> ... loop`; `expr` is the header command."""
    body: list = field(default_factory=list)


@dataclass
class Label(Stmt):
    """OBSE `Label <n>` -- the head of a Goto loop."""
    number: str = ''


@dataclass
class Goto(Stmt):
    """OBSE `Goto <n>` -- jumps back to the matching Label."""
    number: str = ''


@dataclass
class Return(Stmt):
    """`return` -- ends the current block early."""


@dataclass
class SetFunctionValue(Stmt):
    """OBSE `SetFunctionValue <expr>` -- a user function's return value."""
    value: Expr = None


@dataclass
class Block(Stmt):
    """`begin <type> [filter] ... end`.

    `filter` is the raw trailing text of the begin line (`OnEquip player`,
    `OnHit CGAssassinFinal`, `GameMode`): it RESTRICTS the block to that
    object, and dropping it makes the block fire for everyone.
    """
    btype: str = ''
    filter: str = ''
    body: list = field(default_factory=list)


@dataclass
class Script:
    """A whole parsed script body.

    `variables` holds every declaration hoisted out of its block (TES4 scoping
    is script-global). `preamble` is what appeared before the first `begin` --
    comments and blank lines that belong at the top of the emitted file.
    """
    name: str = ''
    variables: list = field(default_factory=list)
    blocks: list = field(default_factory=list)
    preamble: list = field(default_factory=list)
    # Statements found outside any begin/end. A fragment (INFO/QUST result
    # script) is ALL body and no blocks, which is the FRAGMENT parse mode.
    body: list = field(default_factory=list)


# --------------------------------------------------------------------------
# Traversal
# --------------------------------------------------------------------------

#: Expression fields that hold a child node, checked in this order.
_CHILD_FIELDS = ('left', 'right', 'operand', 'owner', 'target', 'index',
                 'receiver')

#: Per node class: which of `_CHILD_FIELDS` it declares, and whether it has args.
_CHILDREN: dict = {}


def _child_fields(cls) -> tuple:
    """`(child field names, has args)` for one node class, memoised.

    Blind `getattr` over all seven fields cost 2.3M calls per 600 scripts,
    nearly all returning None.
    """
    known = _CHILDREN.get(cls)
    if known is None:
        names = tuple(f for f in _CHILD_FIELDS if hasattr(cls, f)
                      or f in getattr(cls, '__annotations__', ()))
        known = (names, hasattr(cls, 'args')
                 or 'args' in getattr(cls, '__annotations__', ()))
        _CHILDREN[cls] = known
    return known


def walk_expr(node) -> Iterator[Expr]:
    """Every node reachable from an expression, itself included.

    Child fields are named, not derived: four private copies of this tuple
    once drifted apart.  `None` yields nothing.
    """
    if node is None:
        return
    yield node
    fields, has_args = _child_fields(type(node))
    for attr in fields:
        child = getattr(node, attr, None)
        if child is not None:
            yield from walk_expr(child)
    if has_args:
        for arg in node.args or ():
            yield from walk_expr(arg)


def walk_stmts(body) -> Iterator[Stmt]:
    """Every statement in a body, including those nested in If/While.

    An `If` reports only its header line, so a top-level walk truncates spans.
    """
    for st in body or ():
        yield st
        for attr in ('body', 'orelse'):
            yield from walk_stmts(getattr(st, attr, None))
        for entry in getattr(st, 'elifs', None) or ():
            yield from walk_stmts(entry[1])


def walk_exprs_in(body) -> Iterator[Expr]:
    """Every expression node reachable from a list of statements."""
    for st in walk_stmts(body):
        for attr in ('expr', 'cond', 'value', 'target'):
            yield from walk_expr(getattr(st, attr, None))
        for entry in getattr(st, 'elifs', None) or ():
            yield from walk_expr(entry[0])
