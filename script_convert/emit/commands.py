"""The command row engine.

`_emit_function` grew as a chain of ~135 name-guarded branches, each of which
resolved a receiver, converted a few arguments, registered a property type and
returned one format string.  That is data written as control flow: the only
thing that varies between branches is *which* format string, *how* the receiver
resolves and *what* the arguments must be typed as.  Three more tables
(`ACTOR_METHOD_CALLS`, `FIXED_PROPERTY_CALLS`, `CONSTANT_CALLS`) each carried
one slice of the same data behind its own little engine.

`COMMAND_ROWS` in `constants.py` is now the one table and `emit_row` below the
one engine.

A row's `emit` is a Papyrus template over:

    {ref}   the resolved receiver, per `subj`
    {a0}..  argument n, CONVERTED to Papyrus (default from `defaults`)
    {s0}..  argument n as AUTHORED source text
    {b0}..  argument n as a Papyrus BOOL literal -- TES4 spells these `0`/`1`
    {p0}..  argument n as a PROPERTY name (sanitised), registered by `types`
    {i0}..  argument n cast to Int, but only when it reads as a Float
    {c0}..  argument n cast to Int unconditionally
    {f0}..  argument n cast to Float
    {fmt}   the WHOLE argument list as one printf-style Papyrus string
    {<v>}   a value only the converter knows -- see `_CONVERTER_VALUES`
    {?n<want>}  the row's first `arm` when argument n reads `want`,
            otherwise its second


`subj` picks how `{ref}` resolves: `ACTOR` promotes a bare `Self` to
`(Self as Actor)` the way every actor-only branch did by hand, `AV` resolves as
an actor WITHOUT that promotion, `OBJREF` routes through the reference an
effect/topic acts on, and `RAW` is the plain converted receiver defaulting to
`Self` -- or to `defaults['ref']`, for a command whose bare form names a fixed
subject rather than the running script (`GetInWorldspace` asks about the
PLAYER).

`types` registers `_property_refs[<arg n source>] = <type>`, which is how a
command tells the property writer that its argument names a Faction/Quest/Spell
rather than a local; `self_type` does the same for a FIXED property the command
always drives (the crime faction, the fame globals).  `note` marks a command with no Papyrus equivalent: the
call becomes a `;NE:` comment and the expression reads as `emit`, which
defaults to the inert `0` (`None` or `""` where the caller needs that type).
"""

import re

from script_convert.constants import (
    PAPYRUS_BOOL_FUNCTIONS, safe_property_name
)
from script_convert.command_rows import (
    ACTOR, AV, COMMAND_ROWS, COMPARISON_BOOL_FUNCTIONS, OBJREF, RAW
)

from script_convert.tes4 import lexer as L

#: A placeholder is one atom: `{a0} == {a1}` compares, `Foo({a0})` does not.
_PLACEHOLDER_RE = re.compile(r'\{[^}]*\}')


def _template_compares(template: str) -> bool:
    """Does this row's template render a comparison at its TOP level?

    Rows wrap their own output (`({ref}.GetParentCell() == {a0})`), so the
    enclosing pair is peeled first -- it is exactly as much a Bool either way.
    Depth still matters: `Foo({a0} == {a1})` calls, it does not compare.
    """
    flat = L.unwrap_parens(_PLACEHOLDER_RE.sub('', template or '').strip())
    depth = 0
    for i, ch in enumerate(flat):
        depth += (ch == '(') - (ch == ')')
        if not depth and flat.startswith(L.BOOL_OPS_LONGEST, i):
            return True
    return False


def _template_head(template: str) -> str:
    """Lowercased name a template calls: `X.GetDisabled({r})` -> `getdisabled`."""
    m = _TEMPLATE_HEAD_RE.match((template or '').strip())
    return m.group(1).lower() if m else ''


#: The call at the head of a row template: an optional receiver, then a name.
_TEMPLATE_HEAD_RE = re.compile(r'^\(?(?:[\w.()]*\.)?([A-Za-z_]\w*)\s*\(')


#: Comparison branches no row and no bool table names; getincell is dynamic.
_BRANCH_COMPARISONS = frozenset({'getinworldspace', 'ispcrace'})

#: Commands emitting a comparison; the row half reads TEMPLATES, never output.
COMPARISON_COMMANDS = frozenset(
    name for name, row in COMMAND_ROWS.items()
    if getattr(row, 'emit', None) and not row.note
    and _template_compares(row.emit)) | _BRANCH_COMPARISONS     | COMPARISON_BOOL_FUNCTIONS


def _ref(conv, row, ref_name, extends):
    """The receiver `{ref}` stands for, per the row's `subj`.

    An OBJREF receiver is CAST when it holds a wider handle: an OBSE user
    function declares its `ref` parameter `Form` when nothing narrows it, and
    Papyrus will not convert down implicitly (`TES4Polyfill.Update3D(Form)`).
    """
    if row.subj == OBJREF:
        ref = conv._resolve_objref_ref(ref_name, extends)
        if conv.type_of(ref) == 'Form':
            return conv._cast(ref, 'ObjectReference')
        return ref
    if row.subj == RAW:
        if ref_name:
            return conv._convert_ref(ref_name, extends)
        return row.defaults.get('ref', 'Self')
    ref = conv._resolve_self_ref(ref_name, extends,
                                 actor_func=row.subj in (ACTOR, AV))
    # The promotion 10 branches spelled out inline: an actor-only call on a
    # script whose Self is not an Actor needs the cast, or it fails to compile.
    if row.subj == ACTOR and ref == 'Self' and extends != 'Actor':
        ref = '(Self as Actor)'
    return ref


class _Args(dict):
    """Renders `emit`'s per-argument placeholders, on demand.

    A dict subclass so `str.format_map` drives it: only the placeholders a
    template actually names are converted, so a row costs nothing for the
    arguments it ignores.
    """

    def __init__(self, conv, row, args_str, extends):
        dict.__init__(self)
        self._c, self._r, self._a, self._e = conv, row, args_str, extends

    def __missing__(self, key):
        """Render one `{...}` placeholder; see the module docstring for each.

        `{gN}` UPPERCASES the axis letter because it is spliced into the
        function NAME, where case is part of the identifier -- `GetPositionx`
        is not a Papyrus function, and failed 4 Nehrim scripts reading a
        coordinate.
        """
        if key in _CONVERTER_VALUES:
            return _CONVERTER_VALUES[key](self._c, self._e)
        if key == 'fmt':
            return self._c._format_string_call(self._a, self._e)
        # `<n>?<then>:<else>` -- pick by the nth argument's SOURCE text.  TES4
        # spells a boolean as `0`/`1` and several commands mean two different
        # Papyrus calls by it (`ToggleFirstPerson 1` vs `0`), which is a value
        # in the row rather than a branch in code.
        if key.startswith('?'):
            # `?<n><want>` -- pick between the row's two `arms` by the nth
            # argument's SOURCE text.  The arms live on the ROW, not in the
            # key: `str.format_map` splits a key at `.` and `[`, so Papyrus
            # text cannot travel inside one.
            n, want = key[1], key[2:]
            got = self._c.arg_src(int(n),
                                  self._r.defaults.get(int(n), '')).lower()
            return self._r.arms[0] if got == want.lower() else self._r.arms[1]
        kind, n = key[0], int(key[1:])
        default = self._r.defaults.get(n, '')
        if kind == 'g':
            return self._c.arg_src(n, default).strip().upper()[:1]
        if kind == 's':
            return self._c.arg_src(n, default).strip().strip('"')
        if kind == 'p':
            return safe_property_name(self._c.arg_src(n, default))
        if kind == 'b':
            return self._bool(n, default)
        arg = self._c.arg_expr(n, self._e, default)
        if kind == 'i':
            # Cast only a Float: the branches this replaced left an Int alone.
            return (self._c._cast(arg, 'Int')
                    if self._c.type_of(arg) == 'Float' else arg)
        if kind == 'c':
            return self._c._cast(arg, 'Int')
        if kind == 'f':
            return self._c._cast(arg, 'Float')
        return arg

    def _bool(self, n: int, default: str) -> str:
        """Argument n under TES4's truth test: any nonzero value is true.

        A literal folds to `true`/`false` (`SetIgnoreFriendlyHits 3` is on); a
        variable is cast, which Papyrus also reads as nonzero-is-true.
        """
        try:
            return 'true' if float(self._c.arg_src(n, default) or 0) else 'false'
        except ValueError:
            return self._c._cast(self._c.arg_expr(n, self._e, default), 'Bool')


def emit_row(conv, row, ref_name, func_name, args_str, extends):
    """Render one `Cmd` row.  Returns the Papyrus text."""
    if row.note:
        # `{f}` is the command AS WRITTEN, receiver included: a note that
        # quotes the source has to quote the whole call or it names the wrong
        # thing (`;NE: isActor` for `Target.isActor`).
        written = f'{ref_name}.{func_name}' if ref_name else func_name
        conv._line_comments.append(
            ';NE: ' + row.note.format(f=written, a=args_str or '').rstrip())
        return row.emit
    if row.self_type:
        conv.sc.property_refs[row.self_type[0]] = row.self_type[1]
    for n, ptype in row.types.items():
        name = conv.arg_src(n)
        if name:
            conv.sc.property_refs[safe_property_name(name)] = ptype
    args = _Args(conv, row, args_str, extends)
    if '{ref}' in row.emit:
        args['ref'] = _ref(conv, row, ref_name, extends)
    return row.emit.format_map(args)


#: Values a row names that come from the CONVERTER rather than from the call.
_CONVERTER_VALUES = {
    'destroyed': lambda conv, extends: conv._destroyed_formlist(),
    'event_actor': lambda conv, extends: conv._current_event_actor_param(),
    'self_ref': lambda conv, extends: conv._self_reference(extends),
    'action_ref': lambda conv, extends: conv._get_action_ref_param(),
}


#: Commands converting to a Bool call. A MAP row's `emit` is a bare name.
BOOL_TEMPLATE_COMMANDS = frozenset(
    name for name, row in COMMAND_ROWS.items()
    if getattr(row, 'emit', None) and not row.note
    and (_template_head(row.emit) in PAPYRUS_BOOL_FUNCTIONS
         or row.emit.strip().rsplit('.', 1)[-1].lower()
         in PAPYRUS_BOOL_FUNCTIONS))


#: Commands with no Papyrus equivalent: an inert literal plus a `;NE:` marker.
INERT_COMMANDS = frozenset(
    name for name, row in COMMAND_ROWS.items() if row.note)
