"""The key `derive_formid` hashes: FormIDs read as if no vanilla master past Skyrim.esm were listed.

Adding a master shifts every later index byte, so a key holding a shifted
FormID would hash differently and move the generated id. Here each index byte
names the slot it would have without Update/Dawnguard/HearthFires/Dragonborn,
and a FormID in one of those masters is named by file.

See: docs/commentary/performance.md#added-masters-never-move-a-hash
"""

import functools

from .vanilla_quests import VANILLA_MASTERS


@functools.lru_cache(maxsize=8)
def _slots(masters: tuple) -> dict:
    """{index byte: what is hashed in its place}, only for the indices that vanilla masters moved."""
    vanilla = {name.lower() for name in VANILLA_MASTERS[1:]}
    slots, kept = {}, 0
    for index, name in enumerate(masters + ('',)):
        if name.lower() in vanilla:
            slots[index] = name.lower()
            continue
        if index != kept:
            slots[index] = kept
        kept += 1
    return slots


def _part(part, slots: dict):
    """One key part with its FormID index byte replaced, if a vanilla master moved it."""
    if isinstance(part, tuple):
        return tuple(_part(item, slots) for item in part)
    if not isinstance(part, int) or isinstance(part, bool) or part < 0:
        return part
    slot = slots.get(part >> 24)
    if slot is None:
        return part
    if isinstance(slot, str):
        return (slot, part & 0xFFFFFF)
    return (slot << 24) | (part & 0xFFFFFF)


def hash_key(key, masters):
    """`key` as `derive_formid` hashes it for a plugin with these masters."""
    slots = _slots(tuple(masters))
    return _part(key, slots) if slots else key
