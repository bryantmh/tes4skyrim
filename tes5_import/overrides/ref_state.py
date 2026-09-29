"""Header flags and GRUP placement of a REFERENCE (REFR/ACHR/ACRE) in a master's cell.

An override keeps the master's converted bytes, but a reference's Persistent,
Initially Disabled and Visible When Distant bits are authored header state, so
the bits the plugin's author changed are taken from the plugin's export.

See: docs/commentary/tes5_import_override.md#override-reference-state
See: docs/commentary/tes5_import_override.md#full-lod-whitelist
"""

import struct

#: TES4 signatures of placed references.
REF_SIGS = frozenset({'REFR', 'ACHR', 'ACRE'})

PERSISTENT = 0x400
INITIALLY_DISABLED = 0x800
VISIBLE_WHEN_DISTANT = 0x8000
#: LIGH-ref "Casts Shadows"; on an ACHR the same bit is Starts Dead, which the master run owns.
LIGHT_CASTS_SHADOWS = 0x200

#: TES5 REFR "Is Full LOD": drawn at full detail at any distance, never baked into object LOD.
FULL_LOD = 0x10000

_TAKEN = PERSISTENT | INITIALLY_DISABLED | VISIBLE_WHEN_DISTANT

#: {plugin: low 24 bits of its OWN FormIDs} shipped as Full LOD; the whitelist doc is cited above.
FULL_LOD_REFS = {
    'dlcfrostcragreborn.esp': frozenset({
        0x01624E, 0x016238, 0x047EFD, 0x047F2E, 0x05104B, 0x051051,
        0x051056, 0x051059, 0x01B3AC, 0x01B3AF, 0x008CD0, 0x01BD59}),
}


def take_mask(out_sig: bytes, base_is_light: bool) -> int:
    """Header bits an override of this output reference takes from the plugin."""
    if out_sig == b'REFR' and base_is_light:
        return _TAKEN | LIGHT_CASTS_SHADOWS
    return _TAKEN


def merge_flags(base: int, master_export: int, plugin_export: int,
                take: int, map_marker: bool) -> int:
    """`base` with only the bits the author changed set to the plugin's value.

    A bit the two exports agree on keeps the master RUN's value, so derived
    bits survive (a map marker's forced 0x400, a TES3 source's settle bit). A
    map marker never loses 0x400: the master run forces it on every marker.
    """
    authored = (master_export ^ plugin_export) & take
    if map_marker:
        authored &= ~PERSISTENT
    return (base & ~authored) | (plugin_export & authored)


def record_flags(record: bytes) -> int:
    """The header flags of a packed record."""
    return struct.unpack_from('<I', record, 8)[0]


def set_flags(record: bytes, flags: int) -> bytes:
    """`record` with its header flags replaced."""
    return record[:8] + struct.pack('<I', flags) + record[12:]


def ref_chain(parent_out: int, persistent: bool) -> tuple:
    """The (6, cell), (8|9, cell) groups a reference sits in under its cell."""
    label = struct.pack('<I', parent_out)
    return ((6, label), (8 if persistent else 9, label))


def ref_path(master_index, parent_out: int, persistent: bool) -> tuple:
    """A reference's full GRUP path under a master's cell; () if it has none."""
    parent = master_index.group_path(parent_out) if parent_out else ()
    return parent + ref_chain(parent_out, persistent) if parent else ()


def placement_fault(path: tuple) -> str:
    """The counter a reference path breaks, or ''.

    A persistent reference belongs in its world's persistent cell, never in an
    exterior block's cell; a temporary one never sits in the persistent cell.
    """
    if len(path) < 3:
        return ''
    parent, gtype = path[:-2], path[-1][0]
    if gtype == 8 and any(step[0] == 4 for step in parent):
        return 'renest-pers-in-block'
    if gtype == 9 and parent[-1][0] == 1:
        return 'renest-temp-in-persistent-cell'
    return ''


def is_full_lod_ref(plugin: str, formid: str, own_index) -> bool:
    """True when `formid` (TES4 export hex) is one of `plugin`'s whitelisted Full-LOD refs."""
    refs = FULL_LOD_REFS.get((plugin or '').lower())
    if not refs or own_index is None or not formid:
        return False
    raw = int(formid, 16)
    return (raw >> 24) == own_index and (raw & 0x00FFFFFF) in refs


def full_lod_flags(flags: int) -> int:
    """Header flags of a Full-LOD reference: persistent, Is Full LOD, no VWD."""
    return (flags | FULL_LOD | PERSISTENT) & ~VISIBLE_WHEN_DISTANT
