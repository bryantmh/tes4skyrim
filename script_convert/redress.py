"""A run of converted item calls that moves armor on one reference re-dresses it once.

See: docs/commentary/script_convert.md#redress-after-inventory-change
"""
import re

#: A converted AddItem / RemoveItem / RemoveAllItems statement on a reference.
_ITEM_CALL = re.compile(r'^(?P<pad>\s*)(?P<ref>\S.*?)\.(?P<fn>AddItem|RemoveItem|RemoveAllItems)\((?P<item>\w*)')

#: Receivers that name the player, who TES4 never re-dressed.
_PLAYER = re.compile(r'GetPlayer\(\)|^\(?Player(Ref)?\b', re.IGNORECASE)


def armor_names(property_refs: dict) -> set:
    """Lower-case names of the Armor-typed properties a script refers to."""
    return {name.lower() for name, ptype in property_refs.items() if ptype == 'Armor'}


def redress_runs(lines: list, armors: set) -> list:
    """`lines` with `TES4Polyfill.Redress(<ref>)` after each run of item calls on one ref that moves armor."""
    out, run = [], None
    for line in lines:
        call = _ITEM_CALL.match(line)
        if run and (not call or call.group('ref') != run[1]):
            out += _close(run)
            run = None
        if call and not _PLAYER.search(call.group('ref')):
            moves = call.group('fn') == 'RemoveAllItems' or call.group('item').lower() in armors
            run = (call.group('pad'), call.group('ref'), moves or bool(run and run[2]))
        out.append(line)
    return out + (_close(run) if run else [])


def _close(run: tuple) -> list:
    """The Redress line ending a run, when the run moved armor."""
    pad, ref, moves = run
    return [f'{pad}TES4Polyfill.Redress({ref})'] if moves else []
