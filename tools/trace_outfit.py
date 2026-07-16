"""Trace how an actor's TES4 inventory is split into OTFT (worn) + CNTO (carried).

Reuses the real tes5_import.outfits logic so what it prints is exactly what the
converter does. Given an export dir and an actor EditorID (or FormID), it shows
every inventory line classified as outfit/carried, the biped slots each wearable
claims, and — crucially — which wearables lost a slot-conflict and got demoted
to carried loot (the "bandit with no pants" symptom).

Usage:
    python -m tools.trace_outfit export/Oblivion.esm BanditHighwayman04
    python -m tools.trace_outfit export/Oblivion.esm 000C1234
    python -m tools.trace_outfit export/Oblivion.esm Bandit --contains
"""
import argparse
import sys

from tes5_import.text_reader import parse_export_directory
from tes5_import import outfits
from tes5_import.text_reader import get_int

# TES4 biped bit → human label (from constants.BIPED_SLOT_MAP).
_SLOT_NAMES = {
    0: 'Head', 1: 'Hair', 2: 'UpperBody', 3: 'LowerBody', 4: 'Hand',
    5: 'Foot', 6: 'RRing', 7: 'LRing', 8: 'Amulet', 13: 'Shield', 15: 'Tail',
}


def _slot_str(mask: int) -> str:
    if not mask:
        return '(no slot)'
    return '+'.join(_SLOT_NAMES.get(b, f'bit{b}')
                    for b in range(16) if mask & (1 << b))


def _label(fid: int) -> str:
    low = fid & 0x00FFFFFF
    sig = outfits._ITEM_SIG.get(low, '????')
    rec = outfits._ITEM_REC.get(low)
    eid = rec.get('EditorID', '') if rec else ''
    return f'{low:06X} {sig} {eid}'.rstrip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('export_dir')
    ap.add_argument('actor', help='EditorID or FormID (hex) of the NPC_/CREA')
    ap.add_argument('--contains', action='store_true',
                    help='match any actor whose EditorID contains ACTOR')
    args = ap.parse_args()

    records = parse_export_directory(args.export_dir)
    by_type = {}
    for r in records:
        by_type.setdefault(r.get('Signature', ''), []).append(r)

    outfits.load_item_index(by_type)

    actors = by_type.get('NPC_', []) + by_type.get('CREA', [])
    key = args.actor.upper()
    matches = []
    for a in actors:
        eid = a.get('EditorID', '')
        fid = a.get('FormID', '')
        if args.contains and key in eid.upper():
            matches.append(a)
        elif eid.upper() == key or fid.upper().endswith(key):
            matches.append(a)

    if not matches:
        print(f'No actor matching {args.actor!r}')
        return 1

    for a in matches:
        _trace_actor(a)
    return 0


def _trace_actor(a: dict) -> None:
    eid = a.get('EditorID', '')
    print(f'\n=== {eid} ({a.get("FormID","")}) {a.get("Signature","")} ===')
    n = get_int(a, 'ItemCount')
    items = []
    for i in range(n):
        fid = outfits._low(a.get(f'Item[{i}].FormID', ''))
        cnt = get_int(a, f'Item[{i}].Count')
        if fid is not None:
            items.append((fid, cnt))

    print(f'{len(items)} inventory entries:')
    for fid, cnt in items:
        elig = outfits.is_outfit_eligible(fid)
        slots = outfits._equip_slots(fid) if elig else 0
        tag = 'WEARABLE' if elig else 'carried'
        prio = outfits._priority(fid) if elig else ''
        print(f'  x{cnt:<2} {_label(fid):40s} {tag:9s} '
              f'{_slot_str(slots):24s} prio={prio}')

    outfit_fids, carried = outfits.split_inventory(items)
    outfit_set = set(f & 0x00FFFFFF for f in outfit_fids)
    src_wearable = {f & 0x00FFFFFF for f, _ in items
                    if outfits.is_outfit_eligible(f)}
    demoted = src_wearable - outfit_set

    print(f'\n  OUTFIT ({len(outfit_fids)}):')
    for fid in outfit_fids:
        print(f'    {_label(fid)}  [{_slot_str(outfits._equip_slots(fid))}]')
    if demoted:
        print(f'\n  DEMOTED to carried (lost a slot conflict):')
        for low in demoted:
            print(f'    {_label(low)}  [{_slot_str(outfits._equip_slots(low))}]')


if __name__ == '__main__':
    sys.exit(main())
