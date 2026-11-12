"""GetItemCount of an item the receiver's base record lists.

Skyrim is reported to answer 0 for those items until it has built the
reference's inventory, where Oblivion answered the listed count.  When the
receiver's base record and the item are both known at conversion time, the
read goes through `TES4Polyfill.AuthoredItemCount` with that count; a read
that cannot be told keeps the plain native, and so does one whose script also
takes the item out of the same receiver.

See: docs/commentary/script_convert.md#getitemcount-before-the-inventory-is-built
"""

from script_convert.command_rows import COMMAND_ROWS
from script_convert.constants import SELF_NAMES
from script_convert.emit.expr import emit_source
from script_convert.tes4 import nodes as N
from tes5_import.dialogue.say_topics import PLAYER_TOKENS

#: Papyrus calls that take items out of an inventory: one item (removed or dropped), or all of them.
_TAKE_ONE, _TAKE_ALL = ('RemoveItem', 'DropObject'), 'RemoveAllItems'

#: TES4 command -> the Papyrus call above that its row converts it to.
_REMOVERS = {name: row.emit for name, row in COMMAND_ROWS.items()
             if row.emit in (*_TAKE_ONE, _TAKE_ALL)}

#: Script types whose own reference is an instance of a record carrying the script.
_REFERENCE_SCRIPTS = ('ObjectReference', 'Actor')


def _spelled(node) -> str:
    """A node as its lowercase source name, unquoted; '' for no node."""
    return emit_source(node).strip().strip('"').lower() if node is not None else ''


def removals(exprs) -> frozenset:
    """(receiver, item) of each call among `exprs` that takes items out of an inventory.

    Lowercase source names.  The receiver is '' for a bare call; the item is
    '' when the call names none, as RemoveAllItems does.
    """
    found = set()
    for expr in exprs:
        emit = _REMOVERS.get(expr.called)
        if emit:
            call = expr if isinstance(expr, N.Call) else None
            receiver = call.receiver if call else expr.owner if isinstance(expr, N.Member) else None
            item = call.args[0] if call and call.args and emit in _TAKE_ONE else None
            found.add((_spelled(receiver), _spelled(item)))
    return frozenset(found)


def _is_variable(ctx, name: str) -> bool:
    """Is `name` (lowercase) a script variable, which hides a record of that name?"""
    return name in ctx.sc.local_vars or name in ctx.sc.var_types


def _receiver(ctx, name: str, extends: str):
    """(placed reference FormID or '', its base record FormIDs) of a receiver; None when it cannot be told.

    A named receiver must be a placed reference's EditorID, which alone has a
    `record_base` row.  A bare call in a reference script acts on the script's
    own reference, an instance of any record carrying the script; where one
    of them is a leveled list's template the script also runs on what the
    list spawns, whose items are not that record's.  The player has no base.
    """
    name = (name or '').lower()
    if _is_variable(ctx, name):
        return None
    if name in PLAYER_TOKENS:
        return name, ()
    if not name or name in SELF_NAMES:
        own = ctx.xref.script_owner_actors(ctx.sc.edid) if extends in _REFERENCE_SCRIPTS else []
        lent = any(fid in ctx.xref.leveled_templates for fid in own)
        return ('', own) if own and not lent else None
    fid = ctx.xref.edid_to_formid.get(name, '')
    base = ctx.xref.record_base.get(fid, '')
    return (fid, [base]) if base else None


def _may_be_one(first, second) -> bool:
    """Can two receivers (see `_receiver`) be the same reference?  One that cannot be told can."""
    if first is None or second is None:
        return True
    if first[0] and second[0]:
        return first[0] == second[0]
    return bool(set(first[1]) & set(second[1]))


def _item_formid(ctx, call) -> str:
    """FormID of the item the one argument names, or ''; a leveled list is none."""
    name = call.source(0).strip('"').lower() if len(call) == 1 else ''
    fid = '' if _is_variable(ctx, name) else ctx.xref.edid_to_formid.get(name, '')
    return '' if ctx.xref.record_type.get(fid) == 'LVLI' else fid


def _takes_out(ctx, extends: str, receiver, item_fid: str) -> bool:
    """Does the script also take the item out of the receiver?

    A removal counts unless it names another record as its item, or a
    receiver that cannot be the one read: the player, another placed
    reference.  A variable on either side cannot be told, so it counts.
    """
    for name, item in ctx.sc.removals:
        named = '' if _is_variable(ctx, item) else ctx.xref.edid_to_formid.get(item, '')
        if named in ('', item_fid) and _may_be_one(receiver, _receiver(ctx, name, extends)):
            return True
    return False


def call_text(ctx, call) -> str | None:
    """The authored-count read for a GetItemCount the base record answers, or None to decline."""
    receiver = _receiver(ctx, call.ref, call.extends)
    if receiver is None or ctx.xref.is_fallout_source():
        return None
    item = _item_formid(ctx, call)
    count = ctx.xref.listed_item_count(receiver[1], item)
    if not count or _takes_out(ctx, call.extends, receiver, item):
        return None
    ref = ctx._resolve_objref_ref(call.ref, call.extends)
    return f'TES4Polyfill.AuthoredItemCount({ref}, {call.arg(0)}, {count})'
