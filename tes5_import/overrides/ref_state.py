"""Header flags of an override REFERENCE (REFR/ACHR/ACRE).

An override keeps the master's converted bytes, but a reference's Persistent,
Initially Disabled and Visible When Distant bits are authored header state, so
the bits the plugin's author changed are taken from the plugin's export.

See: docs/commentary/tes5_import_override.md#override-reference-state
"""

import struct

#: TES4 signatures of placed references.
REF_SIGS = frozenset({'REFR', 'ACHR', 'ACRE'})

PERSISTENT = 0x400
INITIALLY_DISABLED = 0x800
VISIBLE_WHEN_DISTANT = 0x8000
#: LIGH-ref "Casts Shadows"; on an ACHR the same bit is Starts Dead, which the master run owns.
LIGHT_CASTS_SHADOWS = 0x200

_TAKEN = PERSISTENT | INITIALLY_DISABLED | VISIBLE_WHEN_DISTANT


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
