"""FO3/FNV CTDA fields that TES4's 24-byte layout does not carry.

A Fallout CTDA is 28 bytes: TES4's 20 shared bytes, then an explicit Run On
u32 and a Reference u32 in place of TES4's unused tail.  Its function indices
and its actor-value parameters follow Fallout's own tables, so both are
rewritten to Skyrim's before any index-keyed lookup.

See: docs/commentary/tes5_import_conditions.md#fallout-ctda
"""

import struct

from ..generated.ctda_fnv_remap import FNV_FUNC_ABSENT, FNV_FUNC_REMAP
from ..record_types.world_falloutnv import is_fallout_source

#: A CTDA at least this long carries Fallout's Run On / Reference tail.
FALLOUT_CTDA_SIZE = 28

#: GetDisposition: absent in Skyrim, but evaluated at a fixed tier by convert_ctda.
_GET_DISPOSITION = 76

#: Fallout Run On values: Subject, Target, Reference, Combat Target, Linked Ref.
_RUN_ON_TARGET = 1
_RUN_ON_REFERENCE = 2

#: Actor value name -> (FO3/FNV index, Skyrim index); same meaning and scale in both games only.
_FALLOUT_AV = {
    'Aggression': (0, 0),
    'Confidence': (1, 1),
    'Energy': (2, 2),
    'Mood': (4, 4),
    'Carry Weight': (13, 32),
    'Critical Chance': (14, 33),
    'Health': (16, 24),
    'Melee Damage': (17, 34),
    'Poison Resistance': (19, 40),
    'Speed Multiplier': (21, 30),
    'Barter': (32, 17),
    'Lockpick': (36, 14),
    'Repair': (39, 10),
    'Sneak': (42, 15),
    'Speech': (43, 17),
    'Inventory Weight': (46, 31),
    'Paralysis': (47, 53),
    'Invisibility': (48, 54),
    'Night Eye': (50, 55),
    'Fire Resistance': (52, 41),
    'Water Breathing': (53, 57),
    'Unarmed Damage': (56, 35),
    'Assistance': (57, 5),
    'Electric Resistance': (58, 42),
    'Frost Resistance': (59, 43),
}

#: FO3/FNV actor-value index -> Skyrim's, from _FALLOUT_AV.
_FALLOUT_AV_TO_TES5 = {fo: tes5 for fo, tes5 in _FALLOUT_AV.values()}


def fallout_ctda(raw: bytes) -> bytes:
    """`raw`, padded to the full 28 bytes when it is a Fallout CTDA.

    Length alone cannot tell: FO3/FNV masters also store the older 20- and
    24-byte forms, which omit Reference (24) or Run On and Reference (20), so
    a short CTDA is Fallout's whenever the source plugin is. Zero padding
    reads as Run On = Subject, the omitted fields' meaning. A TES4 CTDA is
    returned unchanged.
    See: docs/commentary/tes5_import_conditions.md#fallout-short-ctda
    """
    if len(raw) < FALLOUT_CTDA_SIZE and is_fallout_source():
        return raw.ljust(FALLOUT_CTDA_SIZE, b'\0')
    return raw


def fallout_function(func_idx: int) -> 'int | None':
    """The Skyrim index for a Fallout condition function, or None if it has none."""
    if func_idx in FNV_FUNC_ABSENT and func_idx != _GET_DISPOSITION:
        return None
    return FNV_FUNC_REMAP.get(func_idx, func_idx)


def fallout_actor_value(av: int) -> 'int | None':
    """The Skyrim actor value a Fallout condition's actor-value parameter names.

    None drops the condition, failing open as TES4's attributes do: the
    S.P.E.C.I.A.L. stats, karma and the skills Skyrim lacks have no Skyrim
    value, and the limb conditions and the values on another scale are left
    out. Fallout numbers its actor values its own way, so TES4's table must
    never be applied to them.
    See: docs/commentary/tes5_import_conditions.md#fallout-actor-values
    """
    return _FALLOUT_AV_TO_TES5.get(av)


def fallout_run_on(raw: bytes, remap) -> tuple:
    """(is_target, run_on, reference) from the Fallout tail.

    Run On = Target is reported as `is_target` so the caller applies the
    same Say-topic retargeting it gives TES4's run-on-target flag; every
    other Run On passes through with its reference load-order remapped.
    """
    run_on, reference = struct.unpack_from('<II', raw, 20)
    if run_on == _RUN_ON_TARGET:
        return True, 0, 0
    if run_on == _RUN_ON_REFERENCE:
        reference = remap(reference)
    return False, run_on, reference
