"""Actor inventory → TES5 outfit (OTFT) + carried inventory (CNTO) split.

TES4 and TES5 equip actors by completely different models, and converting one
to the other naively produces three separate in-game bugs.

**TES4 model.** An Oblivion NPC has a single CNTO inventory holding everything:
armor, clothing, weapons, keys, potions, ingredients, loot leveled-lists. The
engine picks what to *wear* out of that pile at runtime, scoring candidates and
equipping the best one per slot — so an NPC carrying both a full iron set and a
shirt/pants shows up in the iron.

**TES5 model.** A Skyrim actor wears exactly what its DOFT outfit (an OTFT
record) lists, and carries CNTO separately. The engine equips *every* item in
the outfit, does no scoring, and the outfit is **added on top of** CNTO at load.
The outfit list may only contain ARMO / WEAP / AMMO / LIGH, or LVLI lists that
resolve entirely to those.

Feeding the raw TES4 inventory into both CNTO and OTFT.INAM (what the converter
used to do) therefore broke in three ways:

1. **CK errors.** Loot lists, keys, ingredients, potions, soul gems and gold all
   landed in the outfit, which only accepts wearables. The Creation Kit rejected
   each one — "LeveledItem '…' used in outfit '…' contains non-armor objects"
   and "Unable to find valid outfit form (…) on outfit …", thousands of lines of
   them.
2. **Duplicate items.** Every wearable appeared in CNTO *and* in the outfit, and
   because Skyrim adds the outfit on top of CNTO the actor ended up holding two
   of each — visible as doubled entries when looting a corpse.
3. **Random equipping.** Oblivion's best-per-slot scoring has no TES5
   counterpart, so an NPC whose inventory held both armor and clothes (205 of
   them in Oblivion.esm, e.g. SE32TestJoelGhost's iron set *and* a
   shirt/pants/shoes set) gave the engine several items claiming the same biped
   slot. Skyrim equips all of them and the last one to win the slot is
   arbitrary, so the NPC randomly appeared in armor or in underwear.

This module resolves all three by classifying every inventory entry once and
splitting it:

* **Outfit** gets the wearables, after a per-slot conflict resolution that keeps
  only ONE winner per biped slot (armor beats clothing, then higher value —
  reproducing Oblivion's own preference deterministically).
* **Inventory (CNTO)** gets everything else — the loot, keys, ingredients and
  potions the actor should carry but not wear.

Nothing is dropped: every source item still reaches the actor, through exactly
one of the two channels.
"""

from .text_reader import get_int

# Record types Skyrim's outfit system accepts directly. LVLI is allowed too,
# but only when the whole list resolves to these (see _lvli_is_wearable).
_WEARABLE_SIGS = {'ARMO', 'CLOT', 'WEAP', 'AMMO', 'LIGH'}

# Every TES4 record type a CNTO entry (or an LVLI leaf) can legally name — i.e.
# the carryable items. Placed instances (REFR/ACHR/ACRE), cells, land, pathgrids
# and dialogue can never appear in an inventory, so indexing them is both wasted
# work and (at ~1M REFRs) a memory problem — see load_item_index.
_INVENTORY_SIGS = frozenset({
    'ARMO', 'CLOT', 'WEAP', 'AMMO', 'LIGH',   # wearables
    'LVLI',                                    # leveled lists
    'MISC', 'KEYM', 'ALCH', 'INGR', 'BOOK',    # carried, never worn
    'SLGM', 'SGST', 'APPA',
})

# The subset whose record dict we retain: LVLI needs its entries walked, and
# ARMO/CLOT need BMDT.BipedFlags + DATA.Value for slot-conflict resolution.
_INSPECTED_SIGS = frozenset({'LVLI', 'ARMO', 'CLOT', 'WEAP', 'AMMO', 'LIGH'})

# Guard against a malformed/cyclic LVLI graph in a plugin.
_MAX_LVLI_DEPTH = 8

# Built once per import run by load_item_index() (Phase 0i).
_ITEM_SIG = {}      # fid_low24 → TES4 signature
_ITEM_REC = {}      # fid_low24 → record dict (only for the types we inspect)
_LVLI_WEARABLE = {} # fid_low24 → bool, memoised _lvli_is_wearable


def _low(fid_str: str):
    """Export FormID hex string → low 24 bits, or None if unparseable."""
    try:
        return int(fid_str, 16) & 0x00FFFFFF
    except (ValueError, TypeError):
        return None


def load_item_index(by_type: dict) -> None:
    """Index every record an actor inventory can point at, so the actor
    converters can classify each CNTO entry by type and biped slot.

    Only *item* types are indexed. An earlier version walked all of `by_type`,
    which meant indexing the plugin's ~1M REFRs (placed-object instances, which
    can never appear in an inventory) into a module-global dict. That inflated
    the parent process by hundreds of MB, and since the navmesh phase then forks
    31 worker processes, the machine went to swap and the import appeared to
    hang at "Generating 8228 navmeshes". Anything not indexed is treated as
    non-wearable and stays in CNTO, which is the safe side of the split.
    """
    _ITEM_SIG.clear()
    _ITEM_REC.clear()
    _LVLI_WEARABLE.clear()

    for sig in _INVENTORY_SIGS:
        for rec in by_type.get(sig, []):
            fid = _low(rec.get('FormID', ''))
            if fid is None:
                continue
            _ITEM_SIG[fid] = sig
            # Only the types we actually inspect need their record retained:
            # LVLI for resolution, ARMO/CLOT/WEAP for slot + value.
            if sig in _INSPECTED_SIGS:
                _ITEM_REC[fid] = rec

    n_wear = sum(1 for s in _ITEM_SIG.values() if s in _WEARABLE_SIGS)
    print(f'  Outfit index: {len(_ITEM_SIG)} items '
          f'({n_wear} wearable, {len(by_type.get("LVLI", []))} leveled lists)')


def _lvli_is_wearable(fid: int, depth: int = 0, path=()) -> bool:
    """True when every leaf of this leveled list is outfit-eligible.

    Skyrim allows an LVLI in an outfit only if it resolves entirely to
    wearables — a list mixing in gold/potions/ingredients is what triggers the
    CK's "contains non-armor objects". Empty lists are not wearable (an outfit
    entry that can resolve to nothing is the "Unable to find valid outfit form"
    error).

    `path` holds only the ANCESTORS of this node, so it detects a true cycle
    without penalising a list that legitimately names the same sublist twice.
    Oblivion weights an entry by repeating it (LL2NPCStaff25 lists
    LL1NPCStaff1Normal100 twice to double its odds); a set shared across
    siblings would treat that second, perfectly valid mention as a cycle,
    return False, and mark the whole staff list non-wearable — which
    disarmed every leveled-weapon actor, e.g. TESTVampireMage's staffs.
    """
    cached = _LVLI_WEARABLE.get(fid)
    if cached is not None:
        return cached

    # A cycle contributes no leaves; treat as vacuous rather than wearable.
    if fid in path or depth > _MAX_LVLI_DEPTH:
        return False

    rec = _ITEM_REC.get(fid)
    if rec is None:
        return False

    count = get_int(rec, 'EntryCount')
    if count <= 0:
        return False  # empty list → no valid outfit form

    sub_path = path + (fid,)
    result = True
    for i in range(count):
        entry = _low(rec.get(f'Entry[{i}].FormID', ''))
        if entry is None:
            result = False
            break
        sig = _ITEM_SIG.get(entry)
        if sig == 'LVLI':
            if not _lvli_is_wearable(entry, depth + 1, sub_path):
                result = False
                break
        elif sig not in _WEARABLE_SIGS:
            result = False  # loot, key, ingredient, gold, or dangling ref
            break

    _LVLI_WEARABLE[fid] = result
    return result


def is_outfit_eligible(fid: int) -> bool:
    """True when this inventory entry belongs in an OTFT rather than CNTO.

    Accepts a FormID in either form: the index is keyed on the low 24 bits, but
    callers hold FormIDs from get_formid(), which has already applied the
    load-order offset (0x00xxxxxx → 0x01xxxxxx). Masking here means an offset
    FormID still finds its record instead of silently missing the index — an
    unmasked lookup classifies every item as non-wearable and the actor ends up
    with no outfit at all.
    """
    sig = _ITEM_SIG.get(fid & 0x00FFFFFF)
    if sig in _WEARABLE_SIGS:
        return True
    if sig == 'LVLI':
        return _lvli_is_wearable(fid & 0x00FFFFFF)
    return False


# Jewelry bits: 6/7 = rings (an NPC legitimately wears one per hand), 8 =
# amulet. These never contend with armor, so they're excluded from conflict
# resolution and every jewelry item is kept.
_JEWELRY_BITS = 0b1_1100_0000


def _resolve_wearables(fid: int, depth: int = 0, path=()) -> list:
    """The concrete ARMO/CLOT records this outfit entry can put on the body.

    An LVLI stands for whichever leaf the engine rolls, so for conflict purposes
    it claims the UNION of its leaves' slots. Resolving it is what makes Azzan
    work: his steel cuirass/greaves/boots compete not with plain CLOT records
    but with LL0NPCClothingShirt/Pants/ShoesMiddle. Treating a leveled list as
    slotless left all seven in the outfit, Skyrim equipped both, and the
    middle-class clothes won the body/legs/feet slots over the steel.

    `path` tracks ancestors only — a list may name the same sublist twice to
    weight it, and that repeat must still contribute its slots (see
    _lvli_is_wearable).
    """
    if fid in path or depth > _MAX_LVLI_DEPTH:
        return []

    sig = _ITEM_SIG.get(fid)
    if sig in ('ARMO', 'CLOT'):
        rec = _ITEM_REC.get(fid)
        return [rec] if rec is not None else []
    if sig != 'LVLI':
        return []  # weapons/ammo/torches occupy no biped slot

    rec = _ITEM_REC.get(fid)
    if rec is None:
        return []
    sub_path = path + (fid,)
    out = []
    for i in range(get_int(rec, 'EntryCount')):
        entry = _low(rec.get(f'Entry[{i}].FormID', ''))
        if entry is not None:
            out += _resolve_wearables(entry, depth + 1, sub_path)
    return out


def _equip_slots(fid: int) -> int:
    """TES4 biped-slot mask this item claims (0 = contends for no slot).

    Weapons, ammo and torches return 0 and so are always kept. A leveled list
    claims the union of the slots its leaves can fill.
    """
    mask = 0
    for rec in _resolve_wearables(fid & 0x00FFFFFF):
        mask |= get_int(rec, 'BMDT.BipedFlags')
    return mask & ~_JEWELRY_BITS


def _priority(fid: int) -> tuple:
    """Sort key deciding which item wins a contested biped slot.

    Oblivion's engine equipped the *best* candidate per slot; Skyrim equips
    everything and lets an arbitrary one win. We reproduce Oblivion's intent
    deterministically: armor outranks clothing (an NPC issued a full steel set
    plus middle-class clothes was meant to be seen in the steel), then higher
    gold value, then FormID as a stable tiebreak so runs are reproducible.

    A leveled list is judged by its leaves: it ranks as armor if it can produce
    any ARMO, and takes its leaves' best value — so an armor list still beats a
    clothing list, and a plain ARMO still beats a clothing list.
    """
    fid &= 0x00FFFFFF
    leaves = _resolve_wearables(fid)
    is_armor = 0
    value = 0
    for rec in leaves:
        if _ITEM_SIG.get(_low(rec.get('FormID', ''))) == 'ARMO':
            is_armor = 1
        value = max(value, get_int(rec, 'DATA.Value'))
    return (is_armor, value, fid)


def split_inventory(items: list) -> tuple:
    """Split an actor's TES4 inventory into (outfit_fids, carried_items).

    `items` is a list of (fid, count) in export order.

    Returns:
        outfit_fids   — FormIDs for the OTFT's INAM, one winner per biped slot.
        carried_items — [(fid, count)] for CNTO: everything NOT in the outfit.

    The two are disjoint. Skyrim ADDS the outfit to CNTO on load, so anything
    listed in both would be duplicated in the actor's inventory — keeping them
    disjoint is what removes the duplicate-item bug.
    """
    wearable, carried = [], []
    for fid, count in items:
        if is_outfit_eligible(fid):
            wearable.append(fid)
        else:
            carried.append((fid, count))

    # Resolve biped-slot contention. Items occupying no biped slot (weapons,
    # ammo, torches) never contend and are all kept. Leveled lists DO contend,
    # via the union of their leaves' slots — a clothing list left unresolved is
    # what put Azzan in a middle-class shirt instead of his steel cuirass.
    #
    # Claim slots greedily, best item first, and keep an item only if EVERY slot
    # it covers is still free. Picking a separate winner per slot is not enough:
    # a multi-slot garment can lose one slot and still win another, and Skyrim
    # would then equip it anyway, dragging the lost slot back in. LL0VampireShirt
    # spans upper+lower body, so against a cuirass it lost the body slot but won
    # the legs — and the shirt covered the chest again, exactly the arbitrary
    # equip we're fixing.
    taken = 0
    winners = set()
    for fid in sorted(set(wearable), key=_priority, reverse=True):
        slots = _equip_slots(fid)
        if slots and (slots & taken):
            continue  # a better item already claimed one of these slots
        taken |= slots
        winners.add(fid)

    outfit_fids, seen = [], set()
    for fid in wearable:
        if fid in seen:
            continue  # a duplicated CNTO line must not double the outfit entry
        if fid not in winners:
            # Outranked for a slot it needs — it stays with the actor as loot
            # rather than being dropped, matching TES4 (the item was in the
            # inventory; Oblivion simply chose not to wear it).
            carried.append((fid, 1))
            continue
        outfit_fids.append(fid)
        seen.add(fid)

    return outfit_fids, carried
