"""SetPos/SetAngle steps a TES4 GameMode block takes from the object's own pose.

TES4 ran GameMode every frame, so `SetAngle Z (GetAngle Z - 2)` turns two
degrees per FRAME: a rate.  Such a step is handed to TESRuntime as a rate per
second, which turns the object on its own tick instead of per Papyrus pass.

See: docs/commentary/script_convert.md#gamemode-steps-are-rates
"""

from script_convert.tes4 import nodes as N

#: The frame rate a per-frame TES4 step is taken to have run at.
TES4_FRAME_RATE = 30.0

_KINDS = {'getpos': 'pos', 'getangle': 'angle', 'setpos': 'pos', 'setangle': 'angle'}
_READS = ('getpos', 'getangle')


def axis_key(kind_name: str, axis_node, receiver: str) -> tuple | None:
    """('pos'|'angle', axis, receiver) for one of the four commands, else None."""
    axis = getattr(axis_node, 'name', '').upper()
    if kind_name not in _KINDS or axis not in ('X', 'Y', 'Z'):
        return None
    return _KINDS[kind_name], axis, (receiver or '').lower()


def _read_key(expr) -> tuple | None:
    """The axis a `GetPos`/`GetAngle` call reads, else None."""
    if not isinstance(expr, N.Call) or expr.called not in _READS or not expr.args:
        return None
    return axis_key(expr.called, expr.args[0], getattr(expr.receiver, 'name', ''))


def _held_key(expr, reads: dict) -> tuple | None:
    """The axis `expr` reads directly, or through a variable holding a read."""
    return _read_key(expr) or reads.get(getattr(expr, 'name', '').lower())


def step_of(expr, reads: dict) -> tuple | None:
    """(axis key, step node, sign) when `expr` is an axis read plus or minus a step."""
    if not isinstance(expr, N.BinOp) or expr.op not in ('+', '-'):
        return None
    key = _held_key(expr.left, reads)
    if key:
        return key, expr.right, 1 if expr.op == '+' else -1
    key = _held_key(expr.right, reads) if expr.op == '+' else None
    return (key, expr.left, 1) if key else None


def set_key(call) -> tuple | None:
    """(value line, axis key) naming one SetPos/SetAngle call in `relative_sets`."""
    if len(call.args) < 2 or call.called not in ('setpos', 'setangle'):
        return None
    key = axis_key(call.called, call.args[0], getattr(call.receiver, 'name', ''))
    return (call.args[1].line, key) if key else None


def relative_sets(blocks) -> dict:
    """`set_key` -> `step_of` result, for each SetPos/SetAngle stepping from an axis read of the SAME pass.

    A read or step made inside an `if` reaches only the statements after it in
    that branch: one made once behind a DoOnce is a stored base, not this
    pass's pose.
    """
    found = {}
    for block in blocks:
        _scan(block.body, {}, {}, found)
    return found


def _scan(body, reads: dict, steps: dict, found: dict) -> None:
    """Record the relative sets in `body`; `reads`/`steps` are what this pass holds so far."""
    for st in body or ():
        if isinstance(st, N.Assign) and isinstance(st.target, N.Ident):
            name = st.target.name.lower()
            read, step = _read_key(st.value), step_of(st.value, reads)
            reads.pop(name, None)
            steps.pop(name, None)
            if read:
                reads[name] = read
            elif step:
                steps[name] = step
        elif isinstance(getattr(st, 'expr', None), N.Call):
            key = set_key(st.expr)
            value = st.expr.args[1] if key else None
            step = key and (steps.get(getattr(value, 'name', '').lower())
                            or step_of(value, reads))
            if step and step[0] == key[1]:
                found[key] = step
        branches = [getattr(st, 'body', None), getattr(st, 'orelse', None)]
        branches += [entry[1] for entry in getattr(st, 'elifs', None) or ()]
        for branch in branches:
            _scan(branch, dict(reads), dict(steps), found)


def rate_scale(step) -> str:
    """Suffix making `step` per second (per pass if it has GetSecondsPassed)."""
    if any(e.called == 'getsecondspassed' for e in N.walk_expr(step)):
        return ' / TES4_SecondsPassed'
    return f' * {TES4_FRAME_RATE}'
