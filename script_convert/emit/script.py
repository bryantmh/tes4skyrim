"""Statement BODIES: a tree walk that emits Papyrus lines with their nesting.

`convert_standalone` used to convert a block by handing `_convert_line` one
SOURCE LINE at a time.  That is why the string layer exists: a line on its own
has no structure, so every structural question -- is this `if` closed? how deep
am I? does this `else` belong to that `if`? -- had to be re-derived by counting
keywords across the emitted text afterwards (`_balance_if_endif`,
`_remove_dead_code_after_return`, the block-depth counter in `_convert_line`).

The parser already owns all of it: an `If` node holds its own body, its elseif
chain and its else, so walking the tree emits closed, correctly-indented blocks
by construction and there is nothing left to repair.

`emit/stmt.py` converts one statement; this module walks the bodies and owns
LAYOUT -- indentation, the `Else`/`EndIf`/`EndWhile` closers, and re-attaching
each statement's trailing source comment.
"""

from __future__ import annotations

import re

from script_convert.emit import stmt as S
from script_convert.tes4 import nodes as N

#: Papyrus indents with two spaces per level, matching the emitted events.
INDENT = '  '


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def emit_body(conv, body, extends: str, depth: int = 0) -> list[str]:
    """Papyrus lines for a list of statement nodes, indented from `depth`.

    Recurses through `If`/`While` rather than tracking a running depth counter,
    so a body cannot come out unbalanced and a `Return` cannot strand the lines
    that follow it inside the wrong block.
    """
    body = _drop_duplicate_say(conv, body)
    animated = _animated_targets(body)
    out = []
    open_walk = conv.sc.refwalk_var
    conv.sc.refwalk_var = ''
    for st in body:
        lines = emit_stmt(conv, st, extends, depth)
        # An OBSE `forEach <it> <- <container> ... loop` body is INERT: Papyrus
        # has no equivalent of OBSE's dynamic containers, the iterator carries
        # no value, and the body reads it element-by-element.  The opener
        # converts to a `;TODO:` and everything up to the `loop` follows it
        # into a comment rather than running against an unassigned iterator.
        if conv.sc.in_foreach:
            lines = [_comment(l) for l in lines]
        if _opens_foreach(st):
            conv.sc.in_foreach += 1
        elif conv.sc.in_foreach and _closes_foreach(st):
            conv.sc.in_foreach -= 1
        out += _deferred_destroy(lines, animated)
    # An OBSE ref-walk's `While` is opened by a `Label` mid-body and its `Goto`
    # cannot close it in place (the Goto sits inside the loop's own `if` nest,
    # and `EndWhile` there would cross those blocks).  The walk therefore ends
    # where the body containing its Label ends, which only the walker knows.
    if conv.sc.refwalk_var and conv.sc.refwalk_labels:
        out.append(INDENT * depth + 'EndWhile')
        conv.sc.refwalk_labels = set()
    conv.sc.refwalk_var = open_walk
    return out


def emit_stmt(conv, st: N.Stmt, extends: str, depth: int) -> list[str]:
    """Papyrus lines for ONE statement, including any body it owns."""
    pad = INDENT * depth
    if isinstance(st, N.If):
        return _if(conv, st, extends, depth)
    if isinstance(st, N.While):
        return ([pad + _text(conv, st, extends)]
                + emit_body(conv, st.body, extends, depth + 1)
                + [pad + 'EndWhile'])
    conv.sc.block_depth = depth
    text = _text(conv, st, extends)
    if not text:
        # A blank source line stays blank; a declaration emits nothing at all
        # (it was hoisted to a property), so it must not leave an empty line.
        return [''] if isinstance(st, N.Blank) else []
    return _indent(text, pad)


def _indent(text: str, pad: str) -> list[str]:
    """A statement's lines at `pad`, keeping any nesting between them.

    Only the FIRST line sits at the statement's depth; the rest keep their
    offset from it.  Re-padding a multi-line handler flat closed one block too
    many and swallowed the events after it (98 Nehrim scripts).
    """
    parts = text.splitlines()
    if not parts:
        return []
    base = len(parts[0]) - len(parts[0].lstrip())
    out = [pad + parts[0].strip()]
    for part in parts[1:]:
        if not part.strip():
            out.append(part)
            continue
        extra = max(len(part) - len(part.lstrip()) - base, 0)
        out.append(pad + ' ' * extra + part.strip())
    return out


def _if(conv, st: N.If, extends: str, depth: int) -> list[str]:
    """`If` with its elseif chain and else, each body owning its own nesting."""
    pad = INDENT * depth
    out = [pad + _text(conv, st, extends)]
    out += emit_body(conv, st.body, extends, depth + 1)
    for cond, body, _line in st.elifs:
        out.append(pad + 'ElseIf ' + S.emit_condition(conv, cond, extends))
        out += emit_body(conv, body, extends, depth + 1)
    if st.orelse:
        out.append(pad + 'Else')
        out += emit_body(conv, st.orelse, extends, depth + 1)
    out.append(pad + 'EndIf')
    return out


def _text(conv, st: N.Stmt, extends: str) -> str:
    """One statement's converted text, with its trailing comment re-attached.

    The comment rides on the NODE, so it can never be emitted in the middle of
    the expression it followed -- the failure `_repair_commented_condition`
    existed to undo.
    """
    conv._line_comments.clear()
    text = conv._guard_stage_timer(S.emit(conv, st, extends))
    if isinstance(st, N.Comment):
        text = _source_comment(text)
    notes = '  '.join(conv._line_comments)
    conv._line_comments.clear()
    if notes:
        # A command that converts to nothing but notes IS the comment; one
        # that produced a value keeps the notes beside it.
        text = notes if text.strip() in ('', '0') else f'{text}  {notes}'
    source_note = _source_comment(st.comment)
    if source_note and not text.lstrip().startswith(';'):
        text = f'{text}  {source_note}' if text else source_note
    return _safe_comments(text)


def _source_comment(comment: str) -> str:
    """Keep an author's TODO distinct from conversion-failure TODO markers."""
    return re.sub(r'(?i)\bTODO\b(?:\s*:)?', 'Source note:', comment)


def _safe_comments(text: str) -> str:
    """`;/` broken up: Papyrus reads it as a BLOCK comment and eats the file."""
    return text.replace(';/', '; /') if ';/' in text else text


def _comment(line: str) -> str:
    """The line, commented out in place, keeping its indentation."""
    stripped = line.lstrip()
    if not stripped or stripped.startswith(';'):
        return line
    return line[:len(line) - len(stripped)] + ';' + stripped


def _opens_foreach(st) -> bool:
    """Does this statement open an OBSE `forEach` block?"""
    return isinstance(st, N.ExprStmt) and st.expr.called == 'foreach'


def _closes_foreach(st) -> bool:
    """Does this statement close one with `loop`?"""
    return isinstance(st, N.ExprStmt) and st.expr.called == 'loop'


# ---------------------------------------------------------------------------
# Adjacent-statement rules
# ---------------------------------------------------------------------------

def _drop_duplicate_say(conv, body):
    """Collapse Oblivion's measure-then-deliver Say pair to ONE line.

    `Set L to X.Say T` then `X.SayTo Player T` -- both TES4 calls speak, so a
    literal conversion played 92 lines TWICE.  SayLine measures AND delivers,
    so the bare delivery for the same (receiver, topic) is dropped.

    A statement may sit between the halves, so scan a 3-statement window; a
    nested body ends it, since two Says in different branches are two beats.
    """
    says = {i: sig for i, st in enumerate(body)
            if (sig := _say_signature(st)) is not None}
    if len(says) < 2:
        return body
    window = 3
    drop = set()
    for i, sig in says.items():
        for j in range(i + 1, min(i + 1 + window, len(body))):
            if j in drop or _opens_a_body(body[j]):
                break
            if says.get(j) == sig:
                drop.add(j if _is_say_assignment(body[i]) else i)
                break
    return [st for i, st in enumerate(body) if i not in drop] if drop else body


def _say_signature(st):
    """`(receiver, topic)` if this statement speaks a line, else None."""
    expr = getattr(st, 'expr', None) or getattr(st, 'value', None)
    call = _find_say(expr)
    if call is None:
        return None
    recv = getattr(call.receiver, 'name', '') if call.receiver else ''
    n = 1 if (call.name.lower() == 'sayto' and len(call.args) >= 2) else 0
    topic = getattr(call.args[n], 'name', '') if len(call.args) > n else ''
    return (recv.lower(), topic.lower()) if topic else None


def _find_say(expr):
    """The `Say`/`SayTo` call inside `expr`, or None.

    An assignment wraps it (`set len to ref.Say topic`), so the walk has to
    reach through the arithmetic the author may have added to it.
    """
    if expr is None:
        return None
    for node in N.walk_expr(expr):
        if isinstance(node, N.Call) and node.name.lower() in ('say', 'sayto'):
            return node
    return None


def _is_say_assignment(st) -> bool:
    """Is this the MEASURING half -- the one that keeps the duration?"""
    return isinstance(st, N.Assign)


def _opens_a_body(st) -> bool:
    """Does this statement open a nested body, ending a scan window?"""
    return isinstance(st, (N.If, N.While))


#: The polyfill call a `setDestroyed 1` becomes, and its deferred twin.
_SETDESTROYED = 'TES4Polyfill.SetDestroyed('
_DESTROY_AFTER = 'TES4Polyfill.DestroyAfterAnimation('


def _animated_targets(body) -> set:
    """Objects this body starts an ANIMATION on, by source name.

    TES4 pairs `playgroup forward 0` with `setDestroyed 1` constantly
    (CTrigTripwire01SCRIPT, CTrapLogs01SCRIPT, MPlanksBreakAway01Script)
    because in Oblivion setDestroyed on a record with no destruction data only
    blocked re-activation.  The destroy still has to run -- it is what stops
    the trap re-triggering -- but not until the polyfill has waited out the
    clip, or the object vanishes mid-animation.
    """
    names = set()
    for st in N.walk_stmts(body):
        for expr in N.walk_expr(getattr(st, 'expr', None)):
            if expr.called != 'playgroup':
                continue
            recv = getattr(expr, 'receiver', None)
            names.add(getattr(recv, 'name', '').lower() or 'self')
    return names


def _deferred_destroy(lines: list, animated: set) -> list:
    """Rewrite a destroy on a just-animated object into the deferred form.

    Only `SetDestroyed ... true` defers: `setDestroyed 0` UN-destroys, and
    rewriting it dropped the flag so both directions destroyed the object.
    """
    if not animated:
        return lines
    out = []
    for line in lines:
        head, sep, rest = line.partition(_SETDESTROYED)
        if not sep:
            out.append(line)
            continue
        target = rest.split(',')[0].strip()
        if target.lower() not in animated:
            out.append(line)
            continue
        args = [a.strip() for a in rest.rsplit(')', 1)[0].split(',')]
        if len(args) > 2 and args[2].lower() != 'true':
            out.append(line)
            continue
        out.append(f'{head}{_DESTROY_AFTER}{args[0]}, {args[1]})')
    return out
