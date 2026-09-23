"""Morrowind merchant stock: a TES3 merchant sells what it owns nearby.

TES3 trades the merchant's inventory plus every container with capacity and
every loose item it owns in the loaded cells. Skyrim's barter menu adds the
containers and loose items owned by the merchant's vendor faction within 10000
units, when that faction's PLVD is In Cell -- so each such merchant gets an In
Cell vendor faction and the refs it owns are re-owned to that faction.

See: docs/commentary/tes5_import_actors.md#morrowind-merchant-stock
"""

import struct

from ..dialogue.morrowind_sidecar import is_tes3_export
from .common import get_float, get_formid, get_int

#: Loose-item signatures Skyrim's barter scan sells (CLOT converts to ARMO).
_LOOSE_STOCK = frozenset({'ARMO', 'CLOT', 'BOOK', 'INGR', 'MISC', 'APPA',
                          'WEAP', 'AMMO', 'KEYM', 'ALCH'})

#: Every base signature `_is_stock` reads.
_STOCK_BASES = _LOOSE_STOCK | {'CONT', 'LIGH'}

#: LIGH DATA flag Can Be Carried (TES3 and TES4 agree); a fixed light is not stock.
_LIGH_CARRIED = 0x2

#: {remapped REFR FormID: remapped stock-faction FormID}, the owner convert_REFR writes.
_stock_owner_by_ref: dict = {}


def plvd_in_cell(cell_fid: int) -> bytes:
    """PLVD type 1 In Cell for `cell_fid`."""
    return struct.pack('<iIi', 1, cell_fid, 0)


def _is_stock(base: dict) -> bool:
    """Whether a placed ref of this base is stock the barter scan sells."""
    sig = base.get('Signature')
    if sig == 'CONT':
        return get_float(base, 'DATA.Weight') > 0
    if sig == 'LIGH':
        return bool(get_int(base, 'DATA.Flags') & _LIGH_CARRIED)
    return sig in _LOOSE_STOCK


def _bases(by_type: dict, master_export: dict) -> dict:
    """{TES4 FormID string: record} for every stock base in scope, own ones last."""
    out = {key: rec for key, rec in (master_export or {}).items()
           if rec.get('Signature') in _STOCK_BASES}
    for sig in _STOCK_BASES:
        out.update((rec.get('FormID', ''), rec) for rec in by_type.get(sig, []))
    return out


def _placement_cells(by_type: dict) -> dict:
    """{remapped actor FormID: remapped CELL of its first placement}."""
    cells = {}
    for sig in ('ACHR', 'ACRE'):
        for ref in by_type.get(sig, []):
            cells.setdefault(get_formid(ref, 'NAME'), get_formid(ref, 'ParentCELL'))
    return cells


def owned_stock(by_type: dict, master_export: dict, export_dir: str) -> dict:
    """{remapped actor FormID: (placement CELL, [owned stock REFRs])}; {} unless TES3."""
    _stock_owner_by_ref.clear()
    if not is_tes3_export(export_dir):
        return {}
    bases = _bases(by_type, master_export)
    cells = _placement_cells(by_type)
    out = {}
    for ref in by_type.get('REFR', []):
        if 'XOWN.Owner' not in ref:
            continue
        owner = get_formid(ref, 'XOWN.Owner')
        if owner in cells and _is_stock(bases.get(ref.get('NAME', ''), {})):
            out.setdefault(owner, (cells[owner], []))[1].append(ref)
    return out


def claim_stock(refs: list, faction_fid: int) -> None:
    """Re-own `refs` to `faction_fid`, the merchant's In Cell vendor faction."""
    for ref in refs:
        _stock_owner_by_ref[get_formid(ref, 'FormID')] = faction_fid


def stock_owner(ref_fid: int) -> int:
    """The vendor faction that now owns this REFR, or 0."""
    return _stock_owner_by_ref.get(ref_fid, 0)
