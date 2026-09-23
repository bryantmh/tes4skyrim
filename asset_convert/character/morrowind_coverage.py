"""
Which Skyrim slots a Morrowind wearable takes, the body sections it hides, and where its skin still shows.

Morrowind draws the actor's skin part in every body-part slot no equipped
item fills; Skyrim hides whole body partitions per ARMA slot and expects the
armor to carry any skin left showing inside them. The record's
`MorrowindPart[i].Slot` list is the authored coverage: the ARMA claims the
partitions those parts sit in, and the worn mesh carries Skyrim skin only
where Morrowind would draw the actor's own skin part -- a part the record
does not list, inside a hidden partition.

One-sided pieces (a left gauntlet, a right bracer, a pauldron) take their own
equip slot from their armor or clothing type. Pauldrons rest on the
shoulders and hide nothing.

See: docs/commentary/asset_convert_armor.md#morrowind-skin-fill
See: docs/commentary/asset_convert_armor.md#body-slot-layout
"""

from typing import NamedTuple

from asset_convert.character.skyrim_overrides import (SBP_32_BODY, SBP_33_HANDS, SBP_34_FOREARMS,
                                                      SBP_37_FEET, SBP_38_CALVES,
                                                      SBP_49_LOWER_BODY, SBP_57_LEFT_PAULDRON,
                                                      SBP_58_RIGHT_PAULDRON, SBP_59_RIGHT_HAND)

#: INDX body-part slot -> the Skyrim body partitions its skin overlaps, measured on malebody_0.
PART_PARTITIONS = {2: (SBP_32_BODY,), 3: (SBP_32_BODY,), 4: (SBP_49_LOWER_BODY,),
                   5: (SBP_49_LOWER_BODY,), 6: (SBP_59_RIGHT_HAND,), 7: (SBP_33_HANDS,),
                   8: (SBP_34_FOREARMS,), 9: (SBP_34_FOREARMS,),
                   11: (SBP_32_BODY, SBP_34_FOREARMS), 12: (SBP_32_BODY, SBP_34_FOREARMS),
                   13: (SBP_32_BODY,), 14: (SBP_32_BODY,), 15: (SBP_37_FEET,),
                   16: (SBP_37_FEET,), 17: (SBP_38_CALVES,), 18: (SBP_38_CALVES,),
                   19: (SBP_38_CALVES,), 20: (SBP_38_CALVES,), 21: (SBP_49_LOWER_BODY,),
                   22: (SBP_49_LOWER_BODY,), 23: (SBP_32_BODY,), 24: (SBP_32_BODY,)}

#: Every Skyrim body partition a part can overlap.
BODY_PARTITIONS = frozenset(p for parts in PART_PARTITIONS.values() for p in parts)

#: (the slot its skin addon claims, the partitions it holds) per split skin file: torso, legs, calves, hands, feet.
SKIN_FILES = ((SBP_32_BODY, frozenset({SBP_32_BODY, SBP_34_FOREARMS})),
              (SBP_49_LOWER_BODY, frozenset({SBP_49_LOWER_BODY})), (SBP_38_CALVES, frozenset({SBP_38_CALVES})),
              (SBP_33_HANDS, frozenset({SBP_33_HANDS})), (SBP_59_RIGHT_HAND, frozenset({SBP_59_RIGHT_HAND})),
              (SBP_37_FEET, frozenset({SBP_37_FEET})))

#: (record signature, AODT/CTDT type) -> the Skyrim slot of a one-sided piece.
SIDED_SLOTS = {('ARMO', 2): SBP_57_LEFT_PAULDRON, ('ARMO', 3): SBP_58_RIGHT_PAULDRON,
               ('ARMO', 6): SBP_33_HANDS, ('ARMO', 7): SBP_59_RIGHT_HAND,
               ('ARMO', 9): SBP_33_HANDS, ('ARMO', 10): SBP_59_RIGHT_HAND,
               ('CLOT', 5): SBP_59_RIGHT_HAND, ('CLOT', 6): SBP_33_HANDS}

#: Sided slots that hide no body section.
_DISPLAY_ONLY = frozenset({SBP_57_LEFT_PAULDRON, SBP_58_RIGHT_PAULDRON})

#: Biped slot of BOD2 bit 0.
_FIRST_SLOT = 30


def sided_slot(rec: dict):
    """The Skyrim slot a one-sided Morrowind piece takes, or None."""
    kind = rec.get('MorrowindWearableType', '').strip()
    if not kind.isdigit():
        return None
    return SIDED_SLOTS.get((rec.get('Signature', '').strip(), int(kind)))


def part_slots(rec: dict) -> list:
    """The INDX slots a Morrowind wearable covers; empty for a pauldron or any other record."""
    if sided_slot(rec) in _DISPLAY_ONLY:
        return []
    count = int(rec.get('MorrowindPartCount', '0') or 0)
    return [int(rec[f'MorrowindPart[{i}].Slot']) for i in range(count)]


def covered_partitions(slots) -> frozenset:
    """The Skyrim body partitions the parts in `slots` overlap."""
    return frozenset(p for slot in slots for p in PART_PARTITIONS.get(slot, ()))


def hidden_skin(partitions) -> frozenset:
    """The skin partitions hidden by claiming `partitions`.

    See: docs/commentary/asset_convert_armor.md#body-slot-layout
    """
    return frozenset(p for slot, held in SKIN_FILES
                     for p in (held if slot in partitions else held & partitions))


def coverage_bits(slots) -> int:
    """BOD2 bits of the body partitions the parts in `slots` overlap."""
    bits = 0
    for partition in covered_partitions(slots):
        bits |= 1 << (partition - _FIRST_SLOT)
    return bits


class SkinFill(NamedTuple):
    """A Morrowind worn piece's skin fill: the partitions its ARMA hides, the parts it covers."""

    partitions: frozenset
    covered: frozenset

    def shown(self) -> list:
        """The INDX parts whose skin shows inside the hidden partitions under this piece."""
        return [slot for slot, parts in PART_PARTITIONS.items()
                if slot not in self.covered and self.partitions.intersection(parts)]
