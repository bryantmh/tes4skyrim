"""
Child scripts that keep a split pair together when Morroblivion's own scripts
hand out or take back the pair item.

The script is never re-converted: the patch binds a CHILD of the converted
script instead. Each child function calls its parent's, then adds or removes as
many left halves as the parent added or removed pair items, so Morroblivion's
code -- topic unlocks, quest flags and all -- runs unchanged.

See: docs/commentary/tes4_export_morrowind.md#split-pair-scripts
"""

import os
import re
from typing import NamedTuple

from asset_convert.game_paths import namespace_for
from core.plugin_masters import masters_from_export_header
from script_convert.blocks import BLOCK_MAP
from script_convert.constants import papyrus_script_name
from script_convert.tes4.nodes import Call, ExprStmt, Ident, If, Literal, While
from script_convert.tes4.parser import Mode, parse
from tes5_import.base.text_reader import info_result_script
from tes5_import.overrides.vmad_swap import SCRIPT_SWAP_KEY, SCRIPTED_TYPES

from .morroblivion_pairs import master_records, pair_item_fid, verbatim_lines

#: Spellings of the player as a command's receiver.
_PLAYER = frozenset({'player', 'playerref'})

#: The commands that change what an inventory holds.
_ITEM_COMMANDS = frozenset({'additem', 'removeitem'})

#: The End fragment an INFO's result script is converted into.
_END_FRAGMENT = ('Function Fragment_0(ObjectReference akSpeakerRef)', 'EndFunction')

#: The placed-reference type of each actor base type; any other base is placed as a REFR.
_PLACED = {'NPC_': 'ACHR', 'CREA': 'ACRE'}

#: Appended to a parent script's name to name its child.
_CHILD_SUFFIX = '_Pairs'

#: A Papyrus event or function header: its name and parameter list.
_HEADER = re.compile(r'^(?:Event|Function) (\w+)\((.*)\)$')


class Twin(NamedTuple):
    """A pair item one function hands out or takes back, and its left half."""
    receiver: str
    item_file: str
    item: int
    left: int


def pair_scripts(ctx, pairs, left_fids: dict, patch: str) -> tuple:
    """({child name: .psc text}, {(signature, FormID): (master record, parent>child)}).

    Every Morroblivion dialogue line and object script adding or removing a
    pair item gets a child; the second map names each record whose binding
    swaps to it -- the INFO itself, or every record attaching the script.
    """
    names = _pair_names(ctx, pairs, left_fids)
    scripts, swaps, attached = {}, {}, {}
    for sig, fid, rec, path in master_records(ctx, {'INFO', 'SCPT'}):
        functions = _functions(sig, rec, names)
        if not functions:
            continue
        prefix = namespace_for(path).upper() + ('_TIF__' if sig == 'INFO' else '_')
        stem = rec['FormID'] if sig == 'INFO' else rec.get('EditorID', '')
        parent = papyrus_script_name(stem, prefix)
        child = papyrus_script_name(stem + _CHILD_SUFFIX, prefix)
        scripts[child] = _child_script(parent, child, functions, patch)
        if sig == 'INFO':
            swaps[(sig, fid)] = (rec, f'{parent}>{child}')
        else:
            attached[fid] = f'{parent}>{child}'
    swaps.update(_attached_swaps(ctx, attached))
    return scripts, swaps


def _attached_swaps(ctx, attached: dict) -> dict:
    """{(signature, FormID): (record, swap)} for each base whose SCRI is in `attached`, and each placement of it.

    The importer moves an object script that handles a reference event onto
    the placed reference, so the swap goes wherever the script may be bound.
    See: docs/commentary/tes4_export_morrowind.md#split-pair-scripts
    """
    swaps, bases = {}, {}
    for sig, fid, rec, _path in master_records(ctx, set(SCRIPTED_TYPES)):
        swap = attached.get(rec.get('SCRI', '').upper())
        if swap:
            swaps[(sig, fid)] = (rec, swap)
            bases[fid] = (sig, swap)
    placed = {_PLACED.get(sig, 'REFR') for sig, _swap in bases.values()}
    for sig, fid, rec, _path in master_records(ctx, placed):
        found = bases.get(rec.get('NAME', '').upper())
        if found:
            swaps[(sig, fid)] = (rec, found[1])
    return swaps


def swap_lines(lines: list, rec: dict, swap: str) -> list:
    """Override lines (`lines`, else `rec` verbatim) binding the child `swap` names."""
    return (lines or verbatim_lines(rec)) + [f'{SCRIPT_SWAP_KEY}={swap}']


def _pair_names(ctx, pairs, left_fids: dict) -> dict:
    """{name a script may call the pair item by: (item file, item id, left id)}, ids the low 24 bits."""
    names = {}
    for pair in pairs:
        raw = int(pair.item['FormID'], 16)
        masters = masters_from_export_header(pair.source)
        index = raw >> 24
        item_file = (os.path.basename(pair.source) if index >= len(masters)
                     else masters[index])
        found = (item_file, raw & 0xFFFFFF,
                 int(left_fids[pair.left.record_id.lower()], 16) & 0xFFFFFF)
        fid = pair_item_fid(pair, ctx)
        for name in (pair.item.get('EditorID', ''), fid, fid.lstrip('0')):
            names[name.lower()] = found
    names.pop('', None)
    return names


def _functions(sig: str, rec: dict, names: dict) -> dict:
    """{(header, footer): {Twin}} of the converted functions of `rec` that change a pair item's count."""
    text = info_result_script(rec) if sig == 'INFO' else rec.get('SCTX', '')
    if not any(name in text.lower() for name in names):
        return {}
    if sig == 'INFO':
        twins = _twins(parse(text, Mode.FRAGMENT).body, names, 'akSpeakerRef')
        return {_END_FRAGMENT: twins} if twins else {}
    out = {}
    for block in parse(text).blocks:
        twins = _twins(block.body, names, 'Self')
        if twins and block.btype.lower() in BLOCK_MAP:
            out.setdefault(BLOCK_MAP[block.btype.lower()], set()).update(twins)
    return out


def _twins(stmts, names: dict, implicit: str) -> set:
    """Every pair item `stmts` add or remove, each with the inventory it changes."""
    out = set()
    for stmt in stmts:
        for body in _bodies(stmt):
            out |= _twins(body, names, implicit)
        call = stmt.expr if isinstance(stmt, ExprStmt) else None
        if not isinstance(call, Call) or call.name.lower() not in _ITEM_COMMANDS or not call.args:
            continue
        found = names.get(_name_of(call.args[0]))
        receiver = _receiver(call.receiver, implicit)
        if found and receiver:
            out.add(Twin(receiver, *found))
    return out


def _bodies(stmt) -> list:
    """The statement lists nested in `stmt`."""
    if isinstance(stmt, If):
        return [stmt.body, *(branch[1] for branch in stmt.elifs), stmt.orelse]
    return [stmt.body] if isinstance(stmt, While) else []


def _name_of(expr) -> str:
    """The lowercase name an argument spells, quoted or not; '' for anything else."""
    if isinstance(expr, Ident):
        return expr.name.lower()
    return expr.text.strip('"').lower() if isinstance(expr, Literal) else ''


def _receiver(expr, implicit: str) -> str:
    """The Papyrus inventory holder a command acts on; '' when the source names a reference."""
    if expr is None:
        return implicit
    if isinstance(expr, Ident) and expr.name.lower() in _PLAYER:
        return 'Game.GetPlayer()'
    return ''


def _child_script(parent: str, child: str, functions: dict, patch: str) -> str:
    """The .psc text of `child`, overriding each of `functions` to keep the left beside its pair."""
    lines = [f'ScriptName {child} extends {parent} Hidden', '']
    for (header, footer), twins in sorted(functions.items()):
        lines += [header, *_follow(header, sorted(twins), patch), footer, '']
    return '\n'.join(lines)


def _follow(header: str, twins: list, patch: str) -> list:
    """A function body calling its parent, then matching each pair item's change with its left."""
    name, params = _HEADER.match(header).groups()
    args = ', '.join(p.split()[-1] for p in params.split(',') if p.strip())
    body = []
    for i, twin in enumerate(twins):
        body += [f'  Form item{i} = Game.GetFormFromFile(0x{twin.item:06X}, "{twin.item_file}")',
                 f'  int held{i} = {twin.receiver}.GetItemCount(item{i})']
    body.append(f'  Parent.{name}({args})')
    for i, twin in enumerate(twins):
        left = f'Game.GetFormFromFile(0x{twin.left:06X}, "{patch}")'
        body += [f'  int change{i} = {twin.receiver}.GetItemCount(item{i}) - held{i}',
                 f'  If change{i} > 0', f'    {twin.receiver}.AddItem({left}, change{i})',
                 f'  ElseIf change{i} < 0', f'    {twin.receiver}.RemoveItem({left}, -change{i})',
                 '  EndIf']
    return body
