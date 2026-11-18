"""Actor inventory -> TES5 outfit (OTFT) + carried inventory (CNTO) split.

Every TES4 inventory entry is classified once: wearables go to the outfit after
per-slot conflict resolution, everything else stays in CNTO, and the two are
disjoint. Nothing is dropped; an outranked wearable stays carried.

See: docs/commentary/tes5_import_actors.md#outfit-split
"""

from ..base.master_export import values_of
from ..base.text_reader import get_int
from ..record_types.equipment import armo_slots

#: Record types an outfit takes directly. See: docs/commentary/tes5_import_actors.md#outfit-no-weapons
_WEARABLE_SIGS = frozenset({'ARMO', 'CLOT'})

#: Every TES4 type a CNTO entry or LVLI leaf can name. See: docs/commentary/tes5_import_actors.md#outfit-index
_INVENTORY_SIGS = frozenset({
    'ARMO', 'CLOT', 'WEAP', 'AMMO', 'LIGH', 'LVLI',
    'MISC', 'KEYM', 'ALCH', 'INGR', 'BOOK', 'SLGM', 'SGST', 'APPA',
})

#: The types whose record is retained: LVLI to walk, ARMO/CLOT for slots and value.
_INSPECTED_SIGS = frozenset({'LVLI', 'ARMO', 'CLOT'})

#: Depth cap against a malformed or cyclic LVLI graph.
_MAX_LVLI_DEPTH = 8

#: Skyrim amulet (35) and ring (36) bits: jewelry never contends, so every piece is kept.
_JEWELRY_BITS = 0b110_0000

#: Skyrim Body (slot 32), where TES4's upper body lands.
_BODY_BIT = 0x4

_ITEM_SIG = {}
_ITEM_REC = {}
_LVLI_WEARABLE = {}


def _low(fid_str: str):
    """Export FormID hex string -> low 24 bits, or None if unparseable."""
    try:
        return int(fid_str, 16) & 0x00FFFFFF
    except (ValueError, TypeError):
        return None


def load_item_index(by_type: dict, master_export: dict = None) -> None:
    """Index every item record an actor inventory can name, masters' first.

    `master_export` ({FormID hex -> record}) is required for a plugin with
    masters. Keys are low 24 bits; the plugin's own records load last and win.
    See: docs/commentary/tes5_import_actors.md#outfit-index
    """
    _ITEM_SIG.clear()
    _ITEM_REC.clear()
    _LVLI_WEARABLE.clear()
    n_master = 0
    for rec in values_of(master_export, *_INVENTORY_SIGS):
        n_master += _index_record(rec, rec['Signature'])
    for sig in _INVENTORY_SIGS:
        for rec in by_type.get(sig, []):
            _index_record(rec, sig)
    n_wear = sum(1 for s in _ITEM_SIG.values() if s in _WEARABLE_SIGS)
    n_lvli = sum(1 for s in _ITEM_SIG.values() if s == 'LVLI')
    msg = f'  Outfit index: {len(_ITEM_SIG)} items ({n_wear} wearable, {n_lvli} leveled lists)'
    if n_master:
        msg += f' — {n_master} from master(s)'
    print(msg)


def _index_record(rec: dict, sig: str) -> int:
    """Index one item record; 1 when it was indexed, else 0."""
    fid = _low(rec.get('FormID', ''))
    if fid is None:
        return 0
    _ITEM_SIG[fid] = sig
    if sig in _INSPECTED_SIGS:
        _ITEM_REC[fid] = rec
    return 1


def _lvli_entries(rec: dict) -> list:
    """The low FormIDs a leveled list names, None for an unparseable one."""
    return [_low(rec.get(f'Entry[{i}].FormID', '')) for i in range(get_int(rec, 'EntryCount'))]


def _lvli_is_wearable(fid: int, depth: int = 0, path=()) -> bool:
    """True when every leaf of this non-empty leveled list is outfit-eligible.

    `path` holds ancestors only. See: docs/commentary/tes5_import_actors.md#outfit-lvli-repeats
    """
    cached = _LVLI_WEARABLE.get(fid)
    if cached is not None:
        return cached
    if fid in path or depth > _MAX_LVLI_DEPTH:
        return False
    rec = _ITEM_REC.get(fid)
    entries = _lvli_entries(rec) if rec is not None else []
    result = bool(entries) and all(_entry_is_wearable(e, depth, path + (fid,)) for e in entries)
    _LVLI_WEARABLE[fid] = result
    return result


def _entry_is_wearable(entry, depth: int, path: tuple) -> bool:
    """True when one leveled-list entry is a wearable or an all-wearable list."""
    sig = _ITEM_SIG.get(entry) if entry is not None else None
    if sig == 'LVLI':
        return _lvli_is_wearable(entry, depth + 1, path)
    return sig in _WEARABLE_SIGS


def is_outfit_eligible(fid: int) -> bool:
    """True when this inventory entry belongs in an OTFT; the load-order byte is masked off."""
    sig = _ITEM_SIG.get(fid & 0x00FFFFFF)
    if sig == 'LVLI':
        return _lvli_is_wearable(fid & 0x00FFFFFF)
    return sig in _WEARABLE_SIGS


def _resolve_wearables(fid: int, depth: int = 0, path=()) -> list:
    """The ARMO/CLOT records this entry can put on the body (every leaf of a list).

    See: docs/commentary/tes5_import_actors.md#outfit-lvli-slots
    """
    if fid in path or depth > _MAX_LVLI_DEPTH:
        return []
    sig = _ITEM_SIG.get(fid)
    rec = _ITEM_REC.get(fid)
    if rec is None or sig not in _INSPECTED_SIGS:
        return []
    if sig != 'LVLI':
        return [rec]
    out = []
    for entry in _lvli_entries(rec):
        if entry is not None:
            out += _resolve_wearables(entry, depth + 1, path + (fid,))
    return out


def _equip_slots(fid: int) -> int:
    """Skyrim slot mask this item claims; 0 for weapons, ammo, torches and jewelry."""
    mask = 0
    for rec in _resolve_wearables(fid & 0x00FFFFFF):
        mask |= armo_slots(rec)
    return mask & ~_JEWELRY_BITS


def _sure_slots(fid: int) -> int:
    """The slots this item fills whatever a list rolls: the slots every leaf shares."""
    leaves = _resolve_wearables(fid & 0x00FFFFFF)
    mask = armo_slots(leaves[0]) if leaves else 0
    for rec in leaves[1:]:
        mask &= armo_slots(rec)
    return mask & ~_JEWELRY_BITS


def _is_guaranteed(fid: int) -> bool:
    """True when this item is certain to fill its slots at spawn, so it may evict others.

    See: docs/commentary/tes5_import_actors.md#outfit-guaranteed
    """
    fid &= 0x00FFFFFF
    sig = _ITEM_SIG.get(fid)
    if sig == 'LVLI':
        return _lvli_guarantees(fid)
    return sig in _WEARABLE_SIGS


def _lvli_guarantees(fid: int, depth: int = 0, path=()) -> bool:
    """True when this list has ChanceNone 0 and every entry is itself guaranteed."""
    if fid in path or depth > _MAX_LVLI_DEPTH:
        return False
    rec = _ITEM_REC.get(fid)
    if rec is None or get_int(rec, 'LVLD.ChanceNone') != 0:
        return False
    entries = _lvli_entries(rec)
    return bool(entries) and all(_entry_guarantees(e, depth, path + (fid,)) for e in entries)


def _entry_guarantees(entry, depth: int, path: tuple) -> bool:
    """True when one leveled-list entry is a wearable or a guaranteed list."""
    sig = _ITEM_SIG.get(entry) if entry is not None else None
    if sig == 'LVLI':
        return _lvli_guarantees(entry, depth + 1, path)
    return sig in _WEARABLE_SIGS


def _priority(fid: int) -> tuple:
    """Sort key for a contested slot: upper body first, then armor, value, FormID.

    See: docs/commentary/tes5_import_actors.md#upper-body-first
    """
    fid &= 0x00FFFFFF
    is_armor = 0
    value = 0
    for rec in _resolve_wearables(fid):
        if _ITEM_SIG.get(_low(rec.get('FormID', ''))) == 'ARMO':
            is_armor = 1
        value = max(value, get_int(rec, 'DATA.Value'))
    return (bool(_equip_slots(fid) & _BODY_BIT), is_armor, value, fid)


def _slot_winners(wearable: list) -> set:
    """The wearables that win their slots; a guaranteed winner closes the slots it surely fills.

    See: docs/commentary/tes5_import_actors.md#outfit-guaranteed
    """
    taken = 0
    winners = set()
    for fid in sorted(set(wearable), key=_priority, reverse=True):
        if _equip_slots(fid) & taken:
            continue
        winners.add(fid)
        if _is_guaranteed(fid):
            taken |= _sure_slots(fid)
    return winners


def split_inventory(items: list) -> tuple:
    """Split [(fid, count)] into (outfit_fids, carried_items), disjoint.

    The outfit holds one winner per biped slot; every other entry, including
    an outranked wearable, is carried.
    """
    wearable, carried = [], []
    for fid, count in items:
        if is_outfit_eligible(fid):
            wearable.append(fid)
        else:
            carried.append((fid, count))
    winners = _slot_winners(wearable)
    outfit_fids = []
    for fid in wearable:
        if fid in outfit_fids:
            continue
        if fid in winners:
            outfit_fids.append(fid)
        else:
            carried.append((fid, 1))
    return outfit_fids, carried
