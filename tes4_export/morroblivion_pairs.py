"""
Morrowind's left/right hand pieces, which Morroblivion merged into one
two-handed item, split back into a left and a right half by the gap patch.

The pair is named by the authored id: the left's id with its side word
swapped is the right's, and Morroblivion's pair item carries the right's id.

See: docs/commentary/tes4_export_morrowind.md#split-pairs
"""

import os
import re
from typing import NamedTuple

from asset_convert.character.hand_pairs import split_models
from asset_convert.character.morrowind_coverage import SIDED_SLOTS
from asset_convert.character.skyrim_overrides import SBP_33_HANDS, SBP_59_RIGHT_HAND
from asset_convert.sources.base_plugins import export_dirs
from output_layout import record_dir
from tes5_import.base.text_reader import parse_export_file

from .morrowind_cell import parse_cell
from .morrowind_ids import remap_form_id
from .morrowind_world import cell_grid
from .record_types.common import escape_value
from .tes3_reader import get_string, get_subrecord, read_file, read_masters

#: A lone `l` between separators, or a trailing one (`bm_ice_gauntletl`).
_SIDE_LETTER = re.compile(r'(?<![a-z])l(?![a-z])|l$')

#: Pair-item fields that hold a FormID, re-keyed into the patch's master list.
_FORMID_KEYS = ('ENAM', 'SCRI')

#: Fields a half takes from its own vanilla record rather than the pair item.
_VANILLA_KEYS = ('FULL', 'DATA.Weight', 'DATA.Value', 'MorrowindWearableType')

#: Fields of a vanilla half's export the split replaces.
_REPLACED_PREFIXES = ('MorrowindPart', 'Male.BipedModel', 'Female.BipedModel',
                      'Male.WorldModel', 'Female.WorldModel', 'DATA.ArmorRating')

#: Ground-model fields a half copies from the pair item where it has no split of its own.
_GROUND_KEYS = ('Male.WorldModel.MODL', 'Male.WorldModel.MODB',
                'Female.WorldModel.MODL', 'Female.WorldModel.MODB')

#: Vanilla record type holding items -> the TES4 type Morroblivion converted it to.
_HOLDER_TYPES = {'NPC_': 'NPC_', 'CREA': 'CREA', 'CONT': 'CONT', 'LEVI': 'LVLI'}

#: TES4 holder type -> the export list its items are in.
_HOLDER_LISTS = {'NPC_': 'Item', 'CREA': 'Item', 'CONT': 'Item', 'LVLI': 'Entry'}


class Pair(NamedTuple):
    """One vanilla left/right pair and the Morroblivion item that merged it."""
    left: object
    right: object
    item: dict
    source: str
    models: dict = {}


def find_pairs(esms, index, export_dir: str, morroblivion, gaps) -> list:
    """Every pair whose left is a gap record and whose right is a Morroblivion item.

    `index` is the unremapped index of the Morroblivion exports named in
    `morroblivion`; the right's id resolves to the pair item's FormID there.
    """
    lefts, rights = _sided(esms)
    items = _pair_items(export_dir, morroblivion)
    pairs = []
    for key, left in sorted(lefts.items()):
        twins = [rights[(key[0], rid)] for rid in _right_ids(key[1])
                 if (key[0], rid) in rights]
        if key not in gaps or len(twins) != 1:
            continue
        found = items.get((index.lookup(twins[0].record_id) or '').upper())
        if found:
            pairs.append(Pair(left, twins[0], *found))
    return pairs


def split_pairs(pairs, out_meshes, log=print) -> list:
    """The `pairs` whose pair item's worn models split into two hands, each with its halves."""
    out = []
    for pair in pairs:
        roots = [os.path.join(d, 'meshes') for d in [pair.source] + export_dirs(pair.source)]
        models = split_models(pair.item, roots, out_meshes, log)
        if models:
            out.append(pair._replace(models=models))
    return out


def left_half(lines: list, pair: Pair) -> list:
    """The vanilla left's export `lines`, wearing the left half of the pair item."""
    kept = [line for line in lines if not line.startswith(_REPLACED_PREFIXES)]
    return kept + _half_models(pair, 0) + _half_rating(pair)


def right_half(pair: Pair, right_lines: list, ctx) -> tuple:
    """(FormID, lines) of the pair item overridden as the right half.

    `right_lines` is the vanilla right's own export; FormIDs are re-keyed
    into the patch's master list through `ctx`.
    """
    remap = _remap_of(ctx, pair.source)
    vanilla = dict(line.split('=', 1) for line in right_lines if '=' in line)
    lines = []
    for key, value in pair.item.items():
        if key in ('Signature', 'FormID') or key in _VANILLA_KEYS \
                or key.startswith(_REPLACED_PREFIXES):
            continue
        if key in _FORMID_KEYS:
            value = remap_form_id(value, remap) or value
        lines.append(f'{key}={escape_value(value)}')
    lines += [f'{key}={vanilla[key]}' for key in _VANILLA_KEYS if key in vanilla]
    lines += _half_models(pair, 1) + _half_rating(pair)
    return remap_form_id(pair.item['FormID'], remap), lines


def _half_models(pair: Pair, side: int) -> list:
    """One half's model lines (0 left, 1 right): its splits, else the pair's own."""
    lines = [f'{key}={escape_value(halves[side])}' for key, halves in pair.models.items()]
    return lines + [f'{key}={escape_value(pair.item[key])}'
                    for key in _GROUND_KEYS if key not in pair.models and pair.item.get(key)]


def _half_rating(pair: Pair) -> list:
    """Half the pair item's armor rating, so a worn pair totals Morroblivion's."""
    rating = pair.item.get('DATA.ArmorRating')
    return [f'DATA.ArmorRating={int(rating) // 2}'] if rating else []


def holder_overrides(esms, pairs, ctx, left_fids: dict) -> list:
    """(signature, FormID, lines) of every Morroblivion holder of a pair item, re-listed with the left beside it.

    A holder is an NPC, creature, container or leveled item list whose
    vanilla counterpart held the left; each of its entries naming the pair
    item gains a twin naming the left (`left_fids`: left id -> FormID), with
    the same count or level. A master whose ids are not the patch's own
    (`remap` not the identity) is skipped: its values could not be copied.
    See: docs/commentary/tes4_export_morrowind.md#split-pairs
    """
    wanted = {}
    for (sig, rid), lefts in _vanilla_holders(esms, set(left_fids)).items():
        fid = ctx.index.lookup(rid)
        if fid and ctx.index.lookup_signature(rid) == sig:
            wanted.setdefault((sig, fid), set()).update(lefts)
    twins_of = {pair_item_fid(pair, ctx): pair.left.record_id.lower() for pair in pairs}
    out = []
    for sig, fid, rec, _path in master_records(ctx, {sig for sig, _fid in wanted}):
        lefts = wanted.get((sig, fid))
        twins = {item: left_fids[left] for item, left in twins_of.items()
                 if lefts and left in lefts}
        lines = _with_twins(rec, _HOLDER_LISTS[sig], twins) if twins else []
        if lines:
            out.append((sig, fid, lines))
    return out


def pair_item_fid(pair: Pair, ctx) -> str:
    """The pair item's FormID in the patch's own master list."""
    return remap_form_id(pair.item['FormID'], _remap_of(ctx, pair.source))


def _remap_of(ctx, folder: str) -> dict:
    """The master re-keying `ctx` applies to the export at `folder`."""
    want = os.path.normcase(os.path.abspath(folder))
    return next(remap for path, remap in ctx.master_dirs
                if os.path.normcase(os.path.abspath(path)) == want)


def _vanilla_holders(esms, lefts: set) -> dict:
    """{(TES4 type, id): left ids it holds} over `esms`, a later file's record replacing an earlier one's."""
    held = {}
    for path in esms:
        for rec in read_file(path)[1]:
            if rec.type in _HOLDER_TYPES and not rec.deleted:
                items = {get_string(sub).lower() if sub.type == 'INAM'
                         else sub.data[4:36].split(bytes(1))[0].decode('cp1252').lower()
                         for sub in rec.subrecords if sub.type in ('NPCO', 'INAM')}
                held[(_HOLDER_TYPES[rec.type], rec.record_id.lower())] = items & lefts
    return {key: lefts for key, lefts in held.items() if lefts}


def master_records(ctx, signatures):
    """(signature, patch FormID, record, export folder) of each master record of `signatures`.

    Only masters whose ids ARE the patch's (identity `remap`) are read, so a
    record's values can be copied into an override unchanged.
    """
    for path, remap in ctx.master_dirs:
        if any(key != value for key, value in remap.items()):
            continue
        for sig in sorted(signatures):
            for rec in parse_export_file(os.path.join(path, f'{sig}.txt')):
                yield sig, remap_form_id(rec.get('FormID', ''), remap), rec, path


def verbatim_lines(rec: dict, skip=()) -> list:
    """A parsed export record's own lines, identity and `skip` keys left out."""
    return [f'{key}={escape_value(value)}' for key, value in rec.items()
            if key not in ('Signature', 'FormID', *skip)]


def _with_twins(rec: dict, name: str, twins: dict) -> list:
    """`rec`'s export lines with every `name` entry naming a key of `twins` doubled for its value; [] if none does."""
    count = int(rec.get(f'{name}Count') or 0)
    entries = [{key[len(f'{name}[{i}].'):]: value for key, value in rec.items()
                if key.startswith(f'{name}[{i}].')} for i in range(count)]
    extra = [dict(entry, FormID=twins[entry['FormID'].upper()]) for entry in entries
             if entry.get('FormID', '').upper() in twins]
    if not extra:
        return []
    lines = verbatim_lines(rec, (f'{name}Count',))
    lines.append(f'{name}Count={count + len(extra)}')
    for n, entry in enumerate(extra, start=count):
        lines += [f'{name}[{n}].{field}={value}' for field, value in entry.items()]
    return lines


def restored_placements(esms, pairs, ctx) -> list:
    """[(authoring key, ref, parent cell FormID)] for every vanilla placement of a split left.

    A placement is as the LAST file to author it states it, parented to the
    Morroblivion cell the index resolves; one in a cell Morroblivion lacks is
    not restored. The key is (authoring file, reference number).
    """
    lefts = {pair.left.record_id.lower() for pair in pairs}
    latest = {}
    for path in esms:
        for cell, ref, key in _placements(path):
            if key in latest or ref.record_id.lower() in lefts:
                latest[key] = (cell, ref)
    out = []
    for key, (cell, ref) in sorted(latest.items()):
        parent = (ctx.index.lookup_interior(cell.name) if cell.interior
                  else ctx.index.lookup_exterior(cell_grid(ref.pos[0], ref.pos[1])))
        if parent and not ref.deleted and ref.record_id.lower() in lefts:
            out.append((key, ref, parent))
    return out


def _placements(path):
    """(cell, ref, (authoring file, reference number)) for every placement in one ESM."""
    own = os.path.basename(path).lower()
    masters = [m.lower() for m in read_masters(path)]
    for rec in read_file(path)[1]:
        if rec.type != 'CELL':
            continue
        cell = parse_cell(rec)
        for ref in cell.refs:
            local = ref.ref_num >> 24
            owner = own if not local or local > len(masters) else masters[local - 1]
            yield cell, ref, (owner, ref.ref_num & 0xFFFFFF)


def _right_ids(left_id: str) -> set:
    """The ids a left piece's right twin may carry: one side word of `left_id` swapped."""
    lid = left_id.lower()
    out = {lid[:m.start()] + 'right' + lid[m.end():] for m in re.finditer('left', lid)}
    return out | {lid[:m.start()] + 'r' + lid[m.end():] for m in _SIDE_LETTER.finditer(lid)}


def _sided(esms) -> tuple:
    """({(type, id): left hand piece}, {(type, id): right hand piece}) over `esms`, earlier files winning."""
    sides = {SBP_33_HANDS: {}, SBP_59_RIGHT_HAND: {}}
    for path in esms:
        for rec in read_file(path)[1]:
            data = get_subrecord(rec, {'ARMO': 'AODT', 'CLOT': 'CTDT'}.get(rec.type, ''))
            if rec.deleted or data is None or len(data.data) < 4:
                continue
            slot = SIDED_SLOTS.get((rec.type, int.from_bytes(data.data[:4], 'little')))
            if slot in sides:
                sides[slot].setdefault((rec.type, rec.record_id.lower()), rec)
    return sides[SBP_33_HANDS], sides[SBP_59_RIGHT_HAND]


def _pair_items(export_dir: str, morroblivion) -> dict:
    """{FormID: (record, export folder)} of every ARMO/CLOT the Morroblivion exports hold."""
    items = {}
    for name in morroblivion:
        folder = str(record_dir(export_dir, name))
        for sig in ('ARMO', 'CLOT'):
            for rec in parse_export_file(os.path.join(folder, f'{sig}.txt')):
                items.setdefault(rec.get('FormID', '').upper(), (rec, folder))
    return items
