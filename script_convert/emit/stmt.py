"""TES4 statement tree -> Papyrus text.

The counterpart to `emit/expr.py`.  `_convert_line_inner` decides what a line
IS by running fourteen regexes in order -- `label <n>`, `goto <n>`, `foreach`,
a variable declaration, an array write, an array read, `set X to Y`,
`let X := Y`, a control keyword, `setfunctionvalue`, `return` -- and the
parser has already answered every one of those as a node type.  So this
module is only the TES4->TES5 SEMANTICS of each statement kind.

Like the expression emitter, it is NOT pure: converting a statement discovers
external references and registers them on the converter's `_property_refs`,
which the importer reads to build VMAD property bindings.

Statements that need converter state the tree does not carry (the OBSE
ref-walk's label set, the chargen-menu sequence, the Say timer) delegate back
to the string path for now; R4 moves the command layer and those follow.
"""

from __future__ import annotations

from script_convert.emit import expr as E
from script_convert.tes4 import nodes as N


def emit(conv, stmt: N.Stmt, extends: str) -> str:
    """Papyrus text for one TES4 statement node.

    Returns the converted line WITHOUT indentation -- the caller owns layout,
    exactly as `_convert_line` did.  A trailing source comment is re-attached
    by the caller too, since it must survive whatever the statement became.
    """
    if isinstance(stmt, N.Comment):
        return stmt.text
    if isinstance(stmt, N.Blank):
        return ''
    if isinstance(stmt, N.VarDecl):
        # Declarations are hoisted to properties by the script assembler, so
        # the line itself emits nothing.
        return ''
    if isinstance(stmt, N.If):
        return _if(conv, stmt, extends)
    if isinstance(stmt, N.While):
        return f'While {emit_condition(conv, stmt.cond, extends)}'
    if isinstance(stmt, N.Assign):
        return conv.emit_assignment(stmt, extends)
    if isinstance(stmt, N.ExprStmt):
        # A standalone elapsed-time read resets OBSE's baseline. Morrowind's
        # tree-growth scripts use it immediately after arming a countdown so
        # earlier work in the same frame is not charged to the new timer.
        name = getattr(stmt.expr, 'name', '').lower()
        args = getattr(stmt.expr, 'args', ())
        if name in ('getsecondspassed', 'scripteffectelapsedseconds') \
                and not args:
            if conv.sc.gsp_realtime:
                return 'TES4_LastTick = Utility.GetCurrentRealTime()'
            return ('; GetSecondsPassed baseline reset '
                    f'({conv._get_update_interval()}s poll)')
        # A bare command in STATEMENT position is not the same as in value
        # position: `disableLinkedPathPoints` as a value is `0`, but as a
        # statement it is `;NE: disableLinkedPathPoints`.  The difference is
        # made by the line wrapper, which folds a `0` result plus the
        # accumulated `;NE:` notes into a comment -- so route through it.
        return conv.emit_command_statement(stmt.expr, extends)
    if isinstance(stmt, N.Return):
        return conv.emit_return(stmt, extends)
    if isinstance(stmt, N.SetFunctionValue):
        return conv.emit_set_function_value(stmt, extends)
    if isinstance(stmt, (N.Label, N.Goto)):
        return conv.emit_jump(stmt, extends)
    if isinstance(stmt, N.Raw):
        return stmt.text
    return ''


def _if(conv, stmt: N.If, extends: str) -> str:
    """`If`/`ElseIf` header text for the node.

    Only the HEADER: the body and the `Else`/`EndIf` closers are emitted by
    the caller walking the block, because Papyrus wants them as separate
    lines and the tree owns the nesting.
    """
    cond = emit_condition(conv, stmt.cond, extends)
    # A condition that converted entirely to a `;TODO:` comment would comment
    # out the keyword itself and take the block structure with it.
    if cond.lstrip().startswith(';TODO:'):
        return f'If True  {cond}'
    return f'If {cond}'


def emit_condition(conv, cond: N.Expr, extends: str) -> str:
    """A condition, keeping the author's own outer parentheses.

    TES4 conditions are conventionally written `if ( x == 1 )` and the
    converter echoes those parens into the output.  The tree drops them as
    redundant, which is correct but makes every such line differ -- and the
    parens are recorded on the node precisely so this is a node question, not
    a re-scan of the source text.
    """
    text = E.emit(conv, cond, extends)
    if (getattr(cond, 'parenthesised', False)
            and not text.startswith(('(', ';'))):
        return f'({text})'
    return text
